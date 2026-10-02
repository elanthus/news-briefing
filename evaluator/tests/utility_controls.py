"""Authored v9 utility controls for offline validation and independent review.

These examples establish case achievability; they are neither generation
baselines nor pre-adjudicated semantic evidence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from agent_runner.output import project_corpus, render_briefing, validate_output
from briefing_config import BriefingConfig, load_config
from evaluator.plan import _mutate, _relocate, _set_source_failures

FIXTURES = Path(__file__).parents[1] / "fixtures"


def utility_positive(
    case: dict[str, Any],
) -> tuple[dict[str, Any], BriefingConfig, dict[str, Any], str]:
    """Load a declared positive, applying exactly the suite's evidence changes."""
    controls = json.loads((FIXTURES / "generation-v9-utility-positive.json").read_text())
    if case["kind"] != "utility":
        raise ValueError("positive controls cover utility cases only")
    corpus = json.loads((FIXTURES / case.get("corpus", "generation-corpus.json")).read_text())
    _relocate(corpus, case.get("corpus_relocations", []))
    _mutate(corpus, case.get("mutations", []))
    _set_source_failures(corpus, case.get("source_failures", []))
    config = load_config(FIXTURES / case["config"])
    if case["id"] in controls["production_case_ids"]:
        output = json.loads((FIXTURES / "generation-v9-scarcity-positive.json").read_text())
    elif case["id"] in controls["small_case_ids"]:
        if len(config.sections) != 1 or config.sections[0].name != "AI Dev Tools":
            raise ValueError("small positive controls require the single tools section")
        topics = copy.deepcopy(controls["default_topics"][:config.sections[0].target_stories])
        overrides = controls["topic_overrides"].get(case["id"], {})
        for topic in topics:
            topic.update(overrides.get(topic["citation_refs"][0], {}))
        output = {
            "schema_version": 1,
            "sections": {"AI Dev Tools": {"topics": topics}},
            "excluded_topics": {},
        }
    else:
        raise ValueError(f"no authored utility positive for {case['id']}")
    citations = project_corpus(corpus).citations
    findings = validate_output(output, config, citations)
    if findings:
        raise ValueError(f"invalid authored positive: {findings}")
    markdown = render_briefing(output, corpus, config, citations)
    return corpus, config, output, markdown
