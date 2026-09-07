"""Shared generation decisions; callers own IO, checkpoints, and scoring."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import briefing_config
import eval_briefing
from agent_runner.output import (
    REPAIRABLE_CHECKS,
    Citation,
    OutputFinding,
    empty_section_findings,
    promote_excluded_to_underfilled,
    render_briefing,
    repair_structural_output,
    validate_output,
    validate_selection,
)


@dataclass(frozen=True)
class DeterministicRepairResult:
    """A candidate repaired by the shared production/evaluator policy."""

    output: dict[str, Any]
    actions: list[dict[str, str]]


SELECTION_PROMOTION_KIND = "selection_promotion"
SELECTION_ATTEMPT_KINDS = frozenset({
    "selection",
    "selection_correction",
    "selection_repair",
    SELECTION_PROMOTION_KIND,
})


def selection_promotion_candidate(
    selection: dict[str, Any],
    *,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> DeterministicRepairResult | None:
    """Offer the slot-filling move for a selection that is about to freeze.

    Withheld when the move would fail the selection contract. A promotion
    empties part of an accountability log, and `_check_exclusion_log_selection`
    is corpus-bound: when the promoted entries were the last logged ones across
    every accountable section that still has eligible unreported items, filling
    the slot raises `exclusion_log_empty`. No deterministic repair covers that
    finding, so the run would spend a model correction on a state code created,
    and the corrected selection could not be promoted again. Code cannot write
    an exclusion reason to restock the log, so the honest outcome is to leave
    the slot short and let the residual `slots_underfilled` warning say so.
    """
    promoted, actions = promote_excluded_to_underfilled(selection, config, citations)
    if not actions or not isinstance(promoted, dict):
        return None
    if any(
        finding.level == "ERROR"
        for finding in validate_selection(promoted, config, citations)
    ):
        return None
    return DeterministicRepairResult(promoted, actions)


def _underfill_is_the_only_blocker(
    findings: Sequence[Mapping[str, str]],
) -> bool:
    """Whether promotion is the remaining candidate fix for this selection.

    True for a clean selection and for one whose only blocking findings are
    ``slots_underfilled``, which promotion is what repairs. Any other blocking
    finding means the selection is structurally wrong, and repair or a model
    correction has to run before a slot fill would mean anything.
    """
    return all(
        finding.get("check") == "slots_underfilled"
        for finding in findings
        if finding.get("level") == "ERROR"
    )


def deterministic_repair_candidate(
    output: dict[str, Any],
    findings: Sequence[Mapping[str, str]],
    *,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    corpus: dict[str, Any] | None = None,
    selection_only: bool = False,
) -> DeterministicRepairResult | None:
    """Apply the repair decision production makes before a model correction."""
    blocking = [finding for finding in findings if finding.get("level") == "ERROR"]
    repairable_blocking = bool(blocking) and all(
        finding.get("check") in REPAIRABLE_CHECKS for finding in blocking
    )
    claim_repair = not blocking and not selection_only and any(
        finding.get("check") == "claim_exceeds_evidence" for finding in findings
    )
    if not repairable_blocking and not claim_repair:
        return None
    evidence = (
        eval_briefing.corpus_evidence(corpus)
        if corpus is not None and not selection_only
        else None
    )
    repaired, actions = repair_structural_output(
        output,
        config,
        citations,
        evidence=evidence,
    )
    if not actions or not isinstance(repaired, dict):
        return None
    return DeterministicRepairResult(repaired, actions)


@dataclass(frozen=True)
class CorrectionBudget:
    limit: int
    used: int = 0

    @property
    def available(self) -> bool:
        return self.used < self.limit


def correction_action(success: bool, budget: CorrectionBudget) -> str:
    return "accept" if success else "correct" if budget.available else "exhausted"

@dataclass(frozen=True)
class StageDecision:
    action: str
    repair: DeterministicRepairResult | None = None


def selection_findings(
    selection: Any, config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> list[OutputFinding]:
    findings = validate_selection(selection, config, citations)
    if not any(f.level == "ERROR" for f in findings):
        findings.extend(empty_section_findings(selection, config, citations))
    return findings


def decide_stage(
    stage: Literal["selection", "prose"],
    output: Any,
    findings: Sequence[Mapping[str, str]],
    *,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    budget: CorrectionBudget,
    last_kind: str,
    corpus: dict[str, Any] | None = None,
) -> StageDecision:
    """Choose promotion, repair, acceptance, correction, or exhaustion in order.

    The last durable attempt kind prevents repeated repairs. A provider correction
    starts a new candidate, so its repair eligibility is independent of the old one.
    """
    selection = stage == "selection"
    if isinstance(output, dict):
        if (selection and last_kind != SELECTION_PROMOTION_KIND
                and _underfill_is_the_only_blocker(findings)):
            promotion = selection_promotion_candidate(output, config=config, citations=citations)
            if promotion is not None:
                return StageDecision(SELECTION_PROMOTION_KIND, promotion)
        repair_kind = "selection_repair" if selection else "deterministic_repair"
        if last_kind != repair_kind:
            repair = deterministic_repair_candidate(
                output, findings, config=config, citations=citations,
                corpus=corpus, selection_only=selection,
            )
            if repair is not None:
                return StageDecision(repair_kind, repair)
    success = not any(f.get("level") == "ERROR" for f in findings)
    return StageDecision(correction_action(success, budget))


def evaluate_candidate(
    output: Any, corpus: dict[str, Any], config: briefing_config.BriefingConfig,
    citations: dict[str, Citation], *,
    repair_actions: Sequence[dict[str, str]] = (),
    pre_findings: Sequence[OutputFinding] = (),
) -> tuple[str | None, dict[str, eval_briefing.Section], list[OutputFinding | eval_briefing.Finding]]:
    """Check the structured contract, then the independently rendered Markdown."""
    findings: list[OutputFinding | eval_briefing.Finding] = list(pre_findings)
    if not any(f.level == "ERROR" for f in findings):
        findings.extend(validate_output(output, config, citations))
    if any(f.level == "ERROR" for f in findings):
        return None, eval_briefing.parse_briefing("", config), findings
    rendered = render_briefing(output, corpus, config, citations, repair_actions=repair_actions)
    sections = eval_briefing.parse_briefing(rendered, config)
    findings.extend(eval_briefing.evaluate_parsed(corpus, rendered, sections, config))
    return rendered, sections, findings
