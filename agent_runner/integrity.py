"""Code-owned integrity summaries derived from verified private artifacts."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import corpus_schema
from agent_runner.checkpoint import sha256_bytes
from agent_runner.jev_review import MAX_ARTIFACT_BYTES, _read_json
from agent_runner.stages import SELECTION_ATTEMPT_KINDS
from publication_schema import parse_integrity, parse_provenance, provenance_payload


def position_key(position: dict[str, Any]) -> str:
    return json.dumps(position, sort_keys=True)


def _bound_bytes(run: Path, manifest: dict[str, Any], name: Any) -> bytes:
    if not isinstance(name, str) or Path(name).name != name or (run / name).resolve().parent != run.resolve():
        raise ValueError("invalid integrity artifact")
    with (run / name).open("rb") as stream:
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES or manifest.get("artifacts", {}).get(name) != sha256_bytes(raw):
        raise ValueError("integrity artifact hash mismatch")
    return raw


def run_provenance(manifest: dict[str, Any], *, attempt_index: int = 1,
                   attempt_count: int = 1) -> dict[str, Any] | None:
    """Model identity and correction/repair counts from a run manifest.

    ``None`` when the manifest predates recorded provider identity or holds a
    malformed one, so an older run still publishes without provenance.
    ``selection_promotion`` is not a repair: filling a reserved slot from the
    accountability log is routine editorial bookkeeping.
    """
    provider, identity = manifest.get("provider"), manifest.get("identity")
    attempts = manifest.get("attempts", [])
    if not isinstance(provider, dict) or not isinstance(identity, dict):
        return None
    try:
        parsed = parse_provenance({
            "provider": provider["provider"], "model": provider["model"],
            "attempt_index": attempt_index, "attempt_count": attempt_count,
            "selection_corrections": sum(a.get("kind") == "selection_correction" for a in attempts),
            "prose_corrections": sum(a.get("kind") == "correction" for a in attempts),
            "repair_action_count": sum(len(a.get("repair_actions", [])) for a in attempts
                                       if a.get("kind") in {"selection_repair", "deterministic_repair"}),
            "prompt_sha256": identity["prompt_sha256"],
        })
        return provenance_payload(parsed) if parsed is not None else None
    except (ValueError, KeyError, TypeError):
        return None


def blank_integrity(decision: str, reasons: list[str]) -> dict[str, Any]:
    return {"version": 1, "decision": decision, "reasons": reasons, "acceptance_verified": False,
            "generation": None, "repair_generation": None, "artifacts": [], "phases": [], "actions": [],
            "initial_review": None, "followup_review": None, "costs": [], "workflow_run_id": None,
            "corpus_health": None}


def add_phase(record: dict[str, Any], phase: str, status: str, reasons: list[str] | None = None,
              report: dict[str, Any] | None = None) -> int:
    sequence = len(record["phases"])
    record["phases"].append({"sequence": sequence, "phase": phase, "status": status,
                             "started_at": (report or {}).get("started_at"),
                             "completed_at": (report or {}).get("completed_at"), "reasons": reasons or []})
    return sequence


def add_action(record: dict[str, Any], phase: int, actor: str, action: str, outcome: str,
               reasons: list[str], *, artifact: str | None = None,
               positions: list[dict[str, Any]] | None = None, sources: list[int] | None = None,
               checks: list[dict[str, Any]] | None = None) -> None:
    index = len(record["actions"])
    record["actions"].append({"id": f"action_{index}", "sequence": index, "phase_sequence": phase,
                              "actor": actor, "artifact": artifact, "positions": positions or [],
                              "source_indexes": sources or [], "check_refs": checks or [],
                              "action": action, "outcome": outcome, "reasons": reasons})


def review_coverage(report: dict[str, Any]) -> dict[str, Any] | None:
    if "planned_checks" not in report:
        return None
    rows = report["results"]
    stop = report.get("stop_code")
    if stop is None:
        old = report.get("stop_reason", "")
        stop = ("unknown_billing" if "cost" in old and "report" in old else
                "deadline" if old == "review deadline reached" else
                "budget" if "budget" in old else
                "provider_failure" if old == "ProviderError" else
                "invalid_result" if old else None)
    required = sum(bool(r["flagged"]) for r in rows)
    returned = sum(r.get("confirmation_probability") is not None for r in rows if r["flagged"])
    return {"status": report["status"], "planned": report["planned_checks"], "returned": len(rows),
            "confirmation_required": required, "confirmation_returned": returned,
            "omitted_pairs": report["omitted_duplicate_pairs"], "oversized": report["skipped_oversized_checks"],
            "unavailable_results": report["planned_checks"] - len(rows) - report["skipped_oversized_checks"],
            "stop_reason": stop}


def review_cost(report: dict[str, Any], phase: str) -> dict[str, Any]:
    calls = report["calls"]
    return {"phase": phase, "reported_cost_usd": sum(c.get("cost_usd") or 0 for c in calls),
            "unknown_cost_calls": sum(c.get("cost_usd") is None for c in calls)}


def repair_scope(report: dict[str, Any]) -> tuple[set[str], set[str], dict[str, set[int]], list[str]]:
    targets: set[str] = set()
    grouping: set[str] = set()
    removed: dict[str, set[int]] = {}
    for row in report["results"]:
        if row.get("confirmation_label") != "confirmed" or row["check"] == "duplicate":
            continue
        pos = row["positions"][0]
        if pos["bucket"] != "sections":
            continue
        key = position_key(pos)
        targets.add(key)
        if row["check"] == "unsafe_grouping":
            grouping.add(key)
        elif row["check"] == "irrelevant_citation":
            removed.setdefault(key, set()).add(row["evidence_index"])
    counts = {position_key(t["position"]): len(t["evidence"]) for t in report.get("topics", [])}
    # Kept independent: several safeguards can withhold the same attempted round.
    reasons = []
    if report["status"] != "complete":
        reasons.append("incomplete_review")
    if review_cost(report, "initial_review")["unknown_cost_calls"]:
        reasons.append("unknown_billing")
    if len(targets) > 4:
        reasons.append("target_limit")
    if any(len(indexes) == counts[key] for key, indexes in removed.items()):
        reasons.append("all_sources_removed")
    return targets, grouping, removed, reasons


def semantic_integrity(original: Path, destination: Path, audit: dict[str, Any],
                       metadata: dict[str, Any]) -> dict[str, Any]:
    """Recompute operational facts; callers first verify report/artifact bindings."""
    before = _read_json(destination / "report.json")[0]
    targets, grouping, confirmed_removed, reasons = repair_scope(before)
    record = blank_integrity("original_retained", reasons)
    manifest = _read_json(original / "manifest.json")[0]
    original_digest = sha256_bytes(_bound_bytes(original, manifest, "final.md"))
    record["artifacts"] = [{"id": "original", "sha256": original_digest}]
    record["initial_review"] = review_coverage(before)
    record["costs"].append(review_cost(before, "initial_review"))
    coverage = record["initial_review"]
    initial_calls = [c for c in before["calls"] if c.get("purpose") != "isolated_confirmation"]
    confirmation_calls = [c for c in before["calls"] if c.get("purpose") == "isolated_confirmation"]
    def timing(calls: list[dict[str, Any]]) -> dict[str, Any]:
        return {"started_at": calls[0].get("started_at") if calls else None,
                "completed_at": calls[-1].get("completed_at") if calls else None}
    questions_complete = coverage is not None and not any(coverage[k] for k in (
        "omitted_pairs", "oversized", "unavailable_results"))
    add_phase(record, "initial_review", "complete" if questions_complete else before["status"],
              [] if questions_complete else ["incomplete_review"], timing(initial_calls))
    if coverage is not None:
        complete = coverage["confirmation_required"] == coverage["confirmation_returned"]
        add_phase(record, "confirmation", "complete" if complete else "partial",
                  [] if complete else ["incomplete_review"], timing(confirmation_calls))
    refs = {key: [{"stage": "initial", "index": i} for i, row in enumerate(audit["checks"])
                  if row["confirmation_label"] == "confirmed" and row["check"] != "duplicate"
                  and any(position_key(p) == key for p in row["positions"])] for key in targets}
    if reasons:
        phase = add_phase(record, "prose_repair", "skipped", reasons)
        for key in sorted(targets):
            add_action(record, phase, "code", "withhold_repair", "skipped", reasons,
                       artifact="original", positions=[json.loads(key)], checks=refs[key])
    elif not targets:
        record["reasons"] = ["no_targets"]
    else:
        calls = _read_json(destination / "repair" / "repair-calls.json")[0]
        record["costs"].append(review_cost({"calls": calls}, "repair_generation"))
        statuses = {t["repair_status"] for t in audit["topics"] if position_key(t["position"]) in targets}
        outcome = next(iter(statuses))
        changed = outcome in {"applied", "candidate", "rejected"}
        repaired = destination / "repair" / "run"
        repaired_manifest = _read_json(repaired / "manifest.json")[0]
        if any(c["status"] != "code_preserved" for c in calls):
            record["repair_generation"] = run_provenance(repaired_manifest)
        if changed:
            record["artifacts"].append({"id": "candidate", "sha256": sha256_bytes(
                _bound_bytes(repaired, repaired_manifest, "final.md"))})
        failure = "provider_failure" if any(c.get("failure_reason") == "provider_failure" for c in calls) else (
            "unknown_billing" if any(c.get("cost_usd") is None for c in calls) else "invalid_candidate")
        removal_phase = (add_phase(record, "code_removal", "complete") if confirmed_removed else None)
        selection_phase = None
        for call in calls:
            stage = "selection_repair" if call["stage"] == "selection" else "prose_repair"
            if call["status"] == "code_preserved":
                phase = add_phase(record, stage, "complete", ["preserved_selection"])
                add_action(record, phase, "code", "selection_repair", "recorded", ["preserved_selection"])
                continue
            call_failed = call["status"] != "complete" or call.get("validated") is False
            phase = add_phase(record, stage, "failed" if call_failed else "complete",
                              [failure] if call_failed else [])
            if call["stage"] == "selection":
                selection_phase = phase
            for key in sorted(grouping if call["stage"] == "selection" else targets):
                add_action(record, phase, "model", stage, outcome, [failure] if outcome == "failed" else [],
                           artifact="candidate" if changed else "original", positions=[json.loads(key)],
                           checks=refs[key])
        validation = add_phase(record, "candidate_validation", "complete" if changed else "failed",
                               [] if changed else [failure])
        if not changed:
            record["reasons"] = [failure]
            if record["costs"][-1]["unknown_cost_calls"] and "unknown_billing" not in record["reasons"]:
                record["reasons"].append("unknown_billing")
            if removal_phase is not None:
                for key, indexes in sorted(confirmed_removed.items()):
                    for index in sorted(indexes):
                        add_action(record, removal_phase, "code", "remove_source", "failed",
                                   ["confirmed_irrelevance", *record["reasons"]], artifact="original",
                                   positions=[json.loads(key)], sources=[index], checks=refs[key])
            # Invalid generation may end before any paid prose request can be recorded.
            if not any(a["action"] == "prose_repair" for a in record["actions"]):
                for key in sorted(targets):
                    add_action(record, validation, "code", "prose_repair", "failed", record["reasons"],
                               artifact="original", positions=[json.loads(key)], checks=refs[key])
        else:
            after = _read_json(destination / "post-review" / "report.json")[0]
            record["followup_review"] = review_coverage(after)
            record["costs"].append(review_cost(after, "followup_review"))
            blockers = [i for i, row in enumerate(audit["followup_checks"]) if row["probability"] >= (
                audit.get("citation_threshold", audit["threshold"]) if row["check"] == "irrelevant_citation"
                else audit["threshold"]) and any(position_key(p) in targets for p in row["positions"])]
            follow_reasons = []
            if after["status"] != "complete":
                follow_reasons.append("incomplete_followup")
            if record["costs"][-1]["unknown_cost_calls"]:
                follow_reasons.append("unknown_billing")
            if blockers:
                follow_reasons.append("followup_flag")
            follow_phase = add_phase(record, "followup_review", after["status"], follow_reasons, after)
            for i in blockers:
                row = audit["followup_checks"][i]
                add_action(record, follow_phase, "judge", "withhold_repair", "rejected", ["followup_flag"],
                           artifact="candidate", positions=row["positions"],
                           sources=[row["evidence_index"]] if row["check"] == "irrelevant_citation" else [],
                           checks=[{"stage": "followup", "index": i}])
            record["reasons"] = follow_reasons
            if outcome == "applied":
                record.update(decision="repaired_applied", acceptance_verified=True)
            elif outcome == "candidate":
                record.update(decision="candidate_retained", acceptance_verified=True, reasons=["candidate_mode"])
            old = _read_json(original / "frozen-selection.json")[0]
            new = _read_json(repaired / "frozen-selection.json")[0]
            # Source indexes always refer to the original selection, regardless of subset order.
            for key in sorted(targets):
                pos = json.loads(key)
                previous = old["sections"][pos["section"]]["topics"][pos["index"]]["citation_refs"]
                current = new["sections"][pos["section"]]["topics"][pos["index"]]["citation_refs"]
                for index, ref in enumerate(previous):
                    if ref in current:
                        continue
                    causes = (["confirmed_irrelevance"] if index in confirmed_removed.get(key, set()) else [])
                    if key in grouping:
                        causes.append("grouping_subset")
                    source_phase = removal_phase if "confirmed_irrelevance" in causes else selection_phase
                    assert source_phase is not None
                    add_action(record, source_phase, "code" if "confirmed_irrelevance" in causes else "model",
                               "remove_source", outcome, causes, artifact="original", positions=[pos],
                               sources=[index], checks=refs[key])
    published_digest = original_digest
    if record["decision"] == "repaired_applied":
        published_digest = next(a["sha256"] for a in record["artifacts"] if a["id"] == "candidate")
    record["artifacts"].append({"id": "published", "sha256": published_digest})
    record["actions"].sort(key=lambda action: action["phase_sequence"])
    for index, action in enumerate(record["actions"]):
        action.update(id=f"action_{index}", sequence=index)
    phase = add_phase(record, "publication_verification", "complete")
    add_action(record, phase, "code", "publication", "applied" if metadata["applied"] else "recorded",
               record["reasons"])
    parsed = parse_integrity(record, semantic_audit=audit, disposition="ready")
    assert parsed is not None
    return parsed


def generation_history(original: Path, record: dict[str, Any], *,
                       audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Prepend verified generation bookkeeping without exposing superseded prose."""
    result = copy.deepcopy(record)
    subjects = {position_key(t["position"]) for t in (audit or {}).get("topics", [])}
    prefix = blank_integrity(record["decision"], [])
    manifest = _read_json(original / "manifest.json")[0]
    attempts = manifest.get("attempts", [])
    baseline_found = False
    final_index = manifest.get("final", {}).get("attempt")
    final_attempt: dict[str, Any] = next((a for a in attempts if a.get("index") == final_index), {})
    final_output = (json.loads(_bound_bytes(original, manifest, final_attempt["structured_artifact"]))
                    if final_attempt.get("structured_artifact") else {})
    for attempt in attempts:
        name = attempt.get("structured_artifact")
        if not name:
            continue
        candidate = json.loads(_bound_bytes(original, manifest, name))
        kind = attempt.get("kind")
        preparation = kind in SELECTION_ATTEMPT_KINDS
        phase = add_phase(prefix, "generation_preparation" if preparation else "initial_validation",
                          "complete" if attempt.get("contract_success") else "partial")
        if not baseline_found and not preparation and attempt.get("briefing_artifact"):
            raw = _bound_bytes(original, manifest, attempt["briefing_artifact"])
            raw.decode("utf-8")
            result["artifacts"].insert(0, {"id": "initial_prose", "sha256": sha256_bytes(raw)})
            baseline_found = True
        action_name = {"selection_correction": "selection_correction", "correction": "prose_correction",
                       "selection_repair": "selection_repair", "deterministic_repair": "prose_repair",
                       "selection_promotion": "promotion"}.get(kind)
        if action_name is None:
            continue
        promotion = kind == "selection_promotion"
        superseded = attempt.get("index") != final_index and any(a.get("kind") in {
            "selection_correction", "correction"} and a.get("index", 0) > attempt.get("index", 0) for a in attempts)
        outcome = "recorded" if promotion else "superseded" if superseded else "recorded" if preparation else "applied"
        reason = "promoted" if promotion else "superseded_correction" if superseded else "deterministic_correction"
        count = len(attempt.get("repair_actions", [])) if kind in {
            "selection_repair", "deterministic_repair", "selection_promotion"} else 1
        for index in range(count):
            positions = []
            raw_action = attempt.get("repair_actions", [])[index] if attempt.get("repair_actions") else {}
            match = re.match(r"^(topics|excluded_topics)\.(.+?)\[(\d+)\]", raw_action.get("path", ""))
            if match and not superseded:
                bucket = "sections" if match[1] == "topics" else "excluded_topics"
                section, slot = match[2], int(match[3])
                try:
                    left, right = candidate[bucket][section], final_output[bucket][section]
                    if bucket == "sections":
                        left, right = left["topics"], right["topics"]
                    pos = {"bucket": bucket, "section": section, "index": slot}
                    if left[slot] == right[slot] and position_key(pos) in subjects:
                        positions = [pos]
                except (KeyError, IndexError, TypeError):
                    pass
            add_action(prefix, phase, "model" if kind in {"selection_correction", "correction"} else "code",
                       action_name, outcome, [reason], artifact="original" if positions else None,
                       positions=positions)
    if not baseline_found and "baseline_unavailable" not in result["reasons"]:
        result["reasons"].append("baseline_unavailable")
    offset = len(prefix["phases"])
    for phase_record in result["phases"]:
        phase_record["sequence"] += offset
    for action in result["actions"]:
        action["phase_sequence"] += offset
    result["phases"] = prefix["phases"] + result["phases"]
    result["actions"] = prefix["actions"] + result["actions"]
    for index, action in enumerate(result["actions"]):
        action.update(id=f"action_{index}", sequence=index)
    if len(result["phases"]) > 128 or len(result["actions"]) > 512:
        raise ValueError("generation integrity exceeds reporting bounds")
    return result


def corpus_health(corpus: Any) -> list[dict[str, Any]] | None:
    if not isinstance(corpus, dict) or corpus_schema.validate_corpus(corpus):
        return None
    records = []
    for source in corpus.get("sources", []):
        status = source["status"]
        parsed, dated, retained = (source[k] for k in ("parsed_entries", "dated_entries", "retained_entries"))
        reason: str | None
        if status == "empty":
            if dated:
                return None
            reason = "empty_source" if parsed == 0 else "no_dated_entries"
        elif status == "quiet":
            reason = {"NoWindowEntries": "no_window_entries", "EntriesFiltered": "entries_filtered"}.get(
                source.get("error_type"))
            if reason is None:
                return None
        else:
            reason = "fetch_error" if status == "error" else "undated_entries" if parsed > dated else None
        row = {key: source[key] for key in ("source_type", "source_id", "category", "status",
                                           "parsed_entries", "dated_entries", "retained_entries")}
        row["reason"] = reason
        if (len(row["source_id"]) > 256 or len(row["category"]) > 100
                or any(ord(c) < 32 or ord(c) == 127 for c in row["source_id"] + row["category"])):
            return None
        records.append(row)
    return records if len(records) <= 256 else None
