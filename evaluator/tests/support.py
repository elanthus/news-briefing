"""Small offline fixtures used by more than one evaluator test module."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from evaluator.adapters import (
    Adapter,
    Generation,
)
from evaluator.adapters import (
    adapter_for as judge_adapter_for,
)
from evaluator.tests.oracle_controls import BaselineAdapter


def adapter_for(provider: str, model: str, *args: Any, **kwargs: Any) -> Adapter:
    if provider == "baseline":
        return BaselineAdapter(model)
    return judge_adapter_for(provider, model, *args, **kwargs)



class FakeAdapter(Adapter):
    provider = "offline-fixture"

    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        sections = output_schema["properties"]["sections"]["properties"]
        selection = "citation_refs" in next(iter(sections.values()))["properties"]["topics"]["items"]["properties"]
        output: dict[str, Any] = {"schema_version": 1, "sections": {}, "excluded_topics": {}}
        if selection:
            if hasattr(self, "selection_requests"):
                self.selection_requests.append(prompt)
            for name, section in sections.items():
                refs = section["properties"]["topics"]["items"]["properties"]["citation_refs"]["items"]["enum"]
                ref = "citation_0002" if "citation_0002" in refs else refs[0]
                output["sections"][name] = {"topics": [{"citation_refs": [ref]}]}
            for name in output_schema["properties"]["excluded_topics"]["properties"]:
                output["excluded_topics"][name] = []
            return Generation(text=json.dumps(output), structured_output=output,
                              latency_ms=0, input_tokens=0, output_tokens=0, cost_usd=0)
        generation = self.generate(prompt)
        match = re.search(r"\*\*(.+?)\*\* — ([^\n]+)", generation.text)
        topics = [] if match is None else [{"headline": match[1], "summary": match[2]}]
        if topics and "https://invented.example.test/story" in generation.text:
            topics[0]["summary"] += " https://invented.example.test/story"
        output["sections"] = {name: {"topics": topics} for name in sections}
        for name in output_schema["properties"]["excluded_topics"]["properties"]:
            output["excluded_topics"][name] = []
        return replace(generation, text=json.dumps(output), structured_output=output)

    def generate(self, prompt: str) -> Generation:
        self.last_prompt = prompt
        return Generation(
            text=(
                "# Daily Briefing — August 11, 2026\n\n"
                "## AI Dev Tools\n\n"
                "**Third-party models as subagents** — The author built a patch so subagents can use other providers.\n"
                "🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/\n"
            ),
            latency_ms=12.5,
            input_tokens=100,
            output_tokens=30,
            cost_usd=0.001,
        )



class RecordingFakeAdapter(FakeAdapter):
    def __init__(self, model: str):
        super().__init__(model)
        self.requests: list[str] = []
        self.selection_requests: list[str] = []

    def generate(self, prompt: str) -> Generation:
        self.requests.append(prompt)
        return super().generate(prompt)



class StructuredFakeAdapter(Adapter):
    provider = "structured-fixture"

    def __init__(self, model: str):
        super().__init__(model)
        self.requests: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    def generate(self, prompt: str) -> Generation:
        raise AssertionError("production-parity evaluation must use generate_structured")

    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        self.requests.append(prompt)
        self.schemas.append(output_schema)
        topic_schema = output_schema["properties"]["sections"]["properties"][
            "AI Dev Tools"
        ]["properties"]["topics"]["items"]
        required = set(topic_schema["required"])
        if required == {"citation_refs"}:
            self.assert_selection_contract(prompt, topic_schema)
            output = {
                "schema_version": 1,
                "sections": {
                    "AI Dev Tools": {
                        "topics": [{"citation_refs": ["citation_0001"]}]
                    }
                },
                "excluded_topics": {},
            }
        elif required == {"headline", "summary"}:
            self.assert_prose_contract(prompt, topic_schema)
            output = {
                "schema_version": 1,
                "sections": {
                    "AI Dev Tools": {
                        "topics": [{
                            "headline": "Tiny MCP server for local notes",
                            "summary": (
                                "A small MCP server stores local notes and exposes search and "
                                "retrieval tools."
                            ),
                        }]
                    }
                },
                "excluded_topics": {},
            }
        else:
            raise AssertionError(f"unexpected structured contract: {sorted(required)}")
        return Generation(
            text=json.dumps(output),
            structured_output=output,
            latency_ms=5.0,
            input_tokens=50,
            output_tokens=20,
        )

    def assert_selection_contract(
        self, prompt: str, topic_schema: dict[str, Any]
    ) -> None:
        assert "--- SELECTION PASS ---" in prompt
        assert "headline" not in topic_schema["properties"]
        assert "summary" not in topic_schema["properties"]

    def assert_prose_contract(
        self, prompt: str, topic_schema: dict[str, Any]
    ) -> None:
        assert "--- PROSE PASS ---" in prompt
        assert "citation_refs" not in topic_schema["properties"]



class CostedFakeAdapter(FakeAdapter):
    provider = "costed-fixture"



class AlwaysFailAdapter(FakeAdapter):
    provider = "nvidia"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        raise TimeoutError("provider remained unavailable")



def _final_provenance(tags: list[str] | None = None) -> dict[str, Any]:
    return {
        "commit": "abc",
        "tree": "def",
        "dirty": False,
        "tags": tags or ["portfolio-v2-source"],
        "source_tag": "portfolio-v2-source",
        "runtime_source_sha256": {"evaluator/runner.py": "123"},
    }



def _resume_fixture(
    temporary: Path,
    case_count: int = 3,
    config_name: str = "generation-config-1.json",
) -> tuple[Path, Path, Path]:
    config = temporary / "config.json"
    config.write_text(
        (Path(__file__).parents[1] / "fixtures" / config_name).read_text(),
        encoding="utf-8",
    )
    suite = temporary / "suite.json"
    suite.write_text(json.dumps({
        "schema_version": 8,
        "case_count": case_count,
        "cases": [
            {
                "id": f"resume-{index}",
                "kind": "utility",
                "family": "valid_edge",
                "config": "config.json",
                "mutations": [],
            }
            for index in range(case_count)
        ],
    }), encoding="utf-8")
    prompt = temporary / "prompt.md"
    prompt.write_text("Produce the briefing.", encoding="utf-8")
    return suite, prompt, temporary / "results"

