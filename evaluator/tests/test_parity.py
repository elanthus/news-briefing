"""Evaluator parity regression coverage."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent_runner.models import GenerationRequest, ModelResponse
from agent_runner.runner import RunnerSettings, run_workflow
from evaluator.adapters import (
    Generation,
    ProviderRequestError,
)
from evaluator.runner import (
    DEFAULT_CORPUS,
    ROOT,
    run_evaluation,
)
from evaluator.tests.support import (
    StructuredFakeAdapter,
    _resume_fixture,
)


class FailingProseStructuredFakeAdapter(StructuredFakeAdapter):
    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        if len(self.requests) == 1:
            self.requests.append(prompt)
            self.schemas.append(output_schema)
            raise ProviderRequestError(
                "prose transport failed after selection",
                transient=False,
                cost_usd=0.003,
            )
        generation = super().generate_structured(prompt, output_schema, trace_id)
        return Generation(
            text=generation.text,
            structured_output=generation.structured_output,
            latency_ms=generation.latency_ms,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
            cost_usd=0.002,
        )



class StructuredAdapterProvider:
    name = "structured-fixture"
    model = "fixture"

    def __init__(self, adapter: StructuredFakeAdapter):
        self.adapter = adapter

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "authentication": "none",
            "tool_policy": "no tools",
        }

    def generate(self, request: GenerationRequest) -> ModelResponse:
        generation = self.adapter.generate_structured(
            request.prompt,
            request.output_schema,
            request.trace_id,
        )
        structured_output = generation.structured_output
        if structured_output is None:
            raise AssertionError("structured fake returned no structured output")
        return ModelResponse(
            raw_output=generation.text,
            structured_output=structured_output,
            latency_ms=generation.latency_ms,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
            cost_usd=generation.cost_usd,
            provider_request_id=generation.provider_request_id,
            usage=generation.usage,
            attempts=generation.attempts,
        )



class UnderfillingStructuredFakeAdapter(StructuredFakeAdapter):
    """Reports one topic of two while logging an eligible unreported item.

    Sized from the schema rather than hardcoded, because promotion changes how
    many prose entries the second pass is asked for: the section gains a topic
    and its accountability log loses the entry that filled it.
    """

    empty_initial_section = False

    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        self.requests.append(prompt)
        self.schemas.append(output_schema)
        section = output_schema["properties"]["sections"]["properties"]["AI Dev Tools"]
        topic_schema = section["properties"]["topics"]["items"]
        excluded = output_schema["properties"]["excluded_topics"]["properties"]
        required = set(topic_schema["required"])
        output: dict[str, Any]
        if required == {"citation_refs"}:
            self.assert_selection_contract(prompt, topic_schema)
            output = {
                "schema_version": 1,
                "sections": {
                    "AI Dev Tools": {
                        "topics": [{"citation_refs": ["citation_0001"]}]
                    }
                },
                # Two logged entries so promoting one still leaves the section
                # accountable; emptying the last one would fail the contract.
                "excluded_topics": {
                    "AI Dev Tools": [
                        {"citation_refs": ["citation_0002"]},
                        {"citation_refs": ["citation_0003"]},
                    ]
                },
            }
        elif required == {"headline", "summary"}:
            self.assert_prose_contract(prompt, topic_schema)
            output = {
                "schema_version": 1,
                "sections": {
                    "AI Dev Tools": {
                        "topics": [
                            {
                                "headline": f"Local notes MCP server {index}",
                                "summary": (
                                    "A small MCP server stores local notes and "
                                    "exposes search and retrieval tools."
                                ),
                            }
                            for index in range(
                                section["properties"]["topics"]["minItems"]
                            )
                        ]
                    }
                },
                "excluded_topics": {
                    "AI Dev Tools": [
                        {
                            "headline": f"Passed over item {index}",
                            "reason": "Lower immediate impact than the reported tools.",
                        }
                        for index in range(
                            excluded["AI Dev Tools"]["minItems"]
                        )
                    ]
                },
            }
        else:
            raise AssertionError(f"unexpected structured contract: {sorted(required)}")
        if required == {"citation_refs"} and self.empty_initial_section:
            output["sections"]["AI Dev Tools"]["topics"] = []
        return Generation(
            text=json.dumps(output),
            structured_output=output,
            latency_ms=5.0,
            input_tokens=50,
            output_tokens=20,
        )



class DeterministicallyRepairableStructuredFakeAdapter(StructuredFakeAdapter):
    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        generation = super().generate_structured(prompt, output_schema, trace_id)
        output = copy.deepcopy(generation.structured_output)
        assert output is not None
        topic = output["sections"]["AI Dev Tools"]["topics"][0]
        if "citation_refs" in topic:
            topic["citation_refs"].append(topic["citation_refs"][0])
        else:
            topic["summary"] = ("word " * 250).strip()
        return Generation(
            text=json.dumps(output),
            structured_output=output,
            latency_ms=generation.latency_ms,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
        )



class RepairingProseStructuredFakeAdapter(StructuredFakeAdapter):
    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        generation = super().generate_structured(prompt, output_schema, trace_id)
        if len(self.requests) != 2:
            return generation
        output = copy.deepcopy(generation.structured_output)
        assert output is not None
        output["sections"]["AI Dev Tools"]["topics"][0]["summary"] = (
            "Opaque citation_0001 leaked into prose."
        )
        return Generation(
            text=json.dumps(output),
            structured_output=output,
            latency_ms=generation.latency_ms,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
        )



class RepairingStructuredFakeAdapter(StructuredFakeAdapter):
    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        generation = super().generate_structured(prompt, output_schema, trace_id)
        if len(self.requests) != 1:
            return generation
        output = copy.deepcopy(generation.structured_output)
        assert output is not None
        output["sections"]["AI Dev Tools"]["topics"][0]["citation_refs"] = [
            "citation_9999"
        ]
        return Generation(
            text=json.dumps(output),
            structured_output=output,
            latency_ms=generation.latency_ms,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
        )



class ParityTest(unittest.TestCase):
    def test_production_parity_path_uses_projection_schema_and_real_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            adapter = StructuredFakeAdapter("fixture")

            report = run_evaluation(
                [adapter],
                {"production": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 9)
            self.assertEqual(manifest["generation_path"], "production-parity")
            self.assertEqual(report["operations"]["run_status"], "complete")
            self.assertEqual(
                report["operations"]["groups"][0]["cost"]["unreported_calls"], 2
            )
            self.assertEqual(len(adapter.requests), 2)
            selection_request, prose_request = adapter.requests
            self.assertIn(
                "--- UNTRUSTED PROJECTED CORPUS (JSON) ---", selection_request
            )
            self.assertNotIn('"points"', selection_request)
            self.assertNotIn('"comments"', selection_request)
            self.assertNotIn(
                "https://news.ycombinator.com/item?id=90000001", selection_request
            )
            self.assertIn("--- FROZEN POSITION-SCOPED EVIDENCE (JSON) ---", prose_request)
            self.assertNotIn("citation_0001", prose_request)

            selection_topic = adapter.schemas[0]["properties"]["sections"][
                "properties"
            ]["AI Dev Tools"]["properties"]["topics"]["items"]
            prose_topic = adapter.schemas[1]["properties"]["sections"]["properties"][
                "AI Dev Tools"
            ]["properties"]["topics"]["items"]
            self.assertEqual(selection_topic["required"], ["citation_refs"])
            self.assertEqual(set(prose_topic["required"]), {"headline", "summary"})
            self.assertNotIn("citation_refs", prose_topic["properties"])

            artifact = output / manifest["results"][0]["artifact_dir"]
            rendered = (artifact / "first.md").read_text(encoding="utf-8")
            self.assertEqual(
                rendered.count("https://news.ycombinator.com/item?id=90000001"), 1
            )
            self.assertNotIn("42", rendered)
            self.assertNotIn("7 comments", rendered)
            self.assertFalse((artifact / "output-schema.json").exists())
            self.assertTrue((artifact / "selection-schema.json").exists())
            self.assertTrue((artifact / "first-prose-schema.json").exists())
            self.assertTrue((artifact / "first-selected-evidence.json").exists())
            self.assertTrue((artifact / "first-structured.json").exists())


    def test_production_parity_promotes_and_tags_like_production(self) -> None:
        self._assert_promotion_parity(empty=False)


    def test_production_parity_promotes_empty_section_before_correction(self) -> None:
        self._assert_promotion_parity(empty=True)


    def _assert_promotion_parity(self, *, empty: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, evaluation_output = _resume_fixture(
                temporary,
                case_count=1,
                config_name="generation-config-promotion.json",
            )
            if empty:
                config_path = temporary / "config.json"
                config_data = json.loads(config_path.read_text())
                config_data["sections"][0]["target_stories"] = 1
                config_path.write_text(json.dumps(config_data))
            evaluator_adapter = UnderfillingStructuredFakeAdapter("fixture")
            evaluator_adapter.empty_initial_section = empty
            run_evaluation(
                [evaluator_adapter],
                {"production": prompt},
                evaluation_output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            evaluator_manifest = json.loads(
                (evaluation_output / "manifest.json").read_text(encoding="utf-8")
            )
            evaluator_row = evaluator_manifest["results"][0]

            production_adapter = UnderfillingStructuredFakeAdapter("fixture")
            production_adapter.empty_initial_section = empty
            production_run = temporary / "production-run"
            run_workflow(
                StructuredAdapterProvider(production_adapter),
                RunnerSettings(
                    config_path=temporary / "config.json",
                    sources_path=ROOT / "sources.json",
                    prompt_path=prompt,
                    output_path=temporary / "briefing.md",
                    corpus_path=DEFAULT_CORPUS,
                    timeout_seconds=30,
                ),
                production_run,
            )
            production_manifest = json.loads(
                (production_run / "manifest.json").read_text(encoding="utf-8")
            )
            production_text = (temporary / "briefing.md").read_text(encoding="utf-8")
            evaluator_text = (
                evaluation_output / evaluator_row["artifact_dir"] / "final.md"
            ).read_text(encoding="utf-8")

        promotions = [
            repair for repair in evaluator_row["first"]["deterministic_repairs"]
            if repair["stage"] == "selection_promotion"
        ]
        self.assertEqual(len(promotions), 1)
        self.assertEqual(
            [action["action"] for action in promotions[0]["actions"]],
            ["promote_excluded_entry"],
        )
        self.assertEqual(
            [
                attempt["kind"] for attempt in production_manifest["attempts"]
                if attempt["kind"] == "selection_promotion"
            ],
            ["selection_promotion"],
        )
        self.assertEqual(len(evaluator_adapter.requests), 2)
        self.assertEqual(len(production_adapter.requests), 2)
        self.assertFalse(evaluator_row["correction_attempted"])
        self.assertIn("[promoted from the accountability log]", evaluator_text)
        self.assertIn("[promoted from the accountability log]", production_text)


    def test_production_parity_matches_production_deterministic_repairs(self) -> None:
        def finding_set(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
            return {
                (row["level"], row["check"], row["message"])
                for row in rows
            }

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, evaluation_output = _resume_fixture(
                temporary, case_count=1
            )
            evaluator_adapter = DeterministicallyRepairableStructuredFakeAdapter(
                "fixture"
            )
            run_evaluation(
                [evaluator_adapter],
                {"production": prompt},
                evaluation_output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            evaluator_manifest = json.loads(
                (evaluation_output / "manifest.json").read_text(encoding="utf-8")
            )
            evaluator_row = evaluator_manifest["results"][0]

            production_adapter = DeterministicallyRepairableStructuredFakeAdapter(
                "fixture"
            )
            production_run = temporary / "production-run"
            settings = RunnerSettings(
                config_path=temporary / "config.json",
                sources_path=ROOT / "sources.json",
                prompt_path=prompt,
                output_path=temporary / "briefing.md",
                corpus_path=DEFAULT_CORPUS,
                timeout_seconds=30,
            )
            run_workflow(
                StructuredAdapterProvider(production_adapter),
                settings,
                production_run,
            )
            production_manifest = json.loads(
                (production_run / "manifest.json").read_text(encoding="utf-8")
            )

            production_repairs: list[dict[str, Any]] = []
            attempts = production_manifest["attempts"]
            for index, attempt in enumerate(attempts):
                if attempt["kind"] not in {"selection_repair", "deterministic_repair"}:
                    continue
                before = json.loads(
                    (production_run / attempts[index - 1]["findings_artifact"]).read_text(
                        encoding="utf-8"
                    )
                )
                after = json.loads(
                    (production_run / attempt["findings_artifact"]).read_text(
                        encoding="utf-8"
                    )
                )
                production_repairs.append({
                    "stage": (
                        "selection"
                        if attempt["kind"] == "selection_repair"
                        else "prose"
                    ),
                    "findings_before": finding_set(before),
                    "findings_after": finding_set(after),
                    "actions": attempt["repair_actions"],
                })

        evaluator_repairs = evaluator_row["first"]["deterministic_repairs"]
        self.assertFalse(evaluator_row["correction_attempted"])
        self.assertEqual(len(evaluator_adapter.requests), 2)
        self.assertEqual(len(production_adapter.requests), 2)
        self.assertEqual(len(evaluator_repairs), 2)
        self.assertEqual(
            [repair["stage"] for repair in evaluator_repairs],
            [repair["stage"] for repair in production_repairs],
        )
        for evaluator_repair, production_repair in zip(
            evaluator_repairs, production_repairs, strict=True
        ):
            self.assertEqual(
                finding_set(evaluator_repair["findings_before"]),
                production_repair["findings_before"],
            )
            self.assertEqual(
                finding_set(evaluator_repair["findings_after"]),
                production_repair["findings_after"],
            )
            self.assertEqual(
                evaluator_repair["actions"], production_repair["actions"]
            )


    def test_production_parity_correction_repairs_structured_output_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            adapter = RepairingStructuredFakeAdapter("fixture")

            run_evaluation(
                [adapter],
                {"production": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            row = manifest["results"][0]
            artifact = output / row["artifact_dir"]
            self.assertTrue(row["correction_attempted"])
            self.assertEqual(len(adapter.requests), 3)
            self.assertEqual((artifact / "first.md").read_text(encoding="utf-8"), "")
            corrected_selection_topic = adapter.schemas[1]["properties"]["sections"][
                "properties"
            ]["AI Dev Tools"]["properties"]["topics"]["items"]
            self.assertEqual(corrected_selection_topic["required"], ["citation_refs"])
            self.assertIn("replacement selection JSON object", adapter.requests[1])
            self.assertIn("--- PROSE PASS ---", adapter.requests[2])
            self.assertIn(
                "https://news.ycombinator.com/item?id=90000001",
                (artifact / "final.md").read_text(encoding="utf-8"),
            )


    def test_production_parity_prose_correction_keeps_the_frozen_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            adapter = RepairingProseStructuredFakeAdapter("fixture")

            run_evaluation(
                [adapter],
                {"production": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            row = manifest["results"][0]
            artifact = output / row["artifact_dir"]
            self.assertTrue(row["correction_attempted"])
            self.assertEqual(len(adapter.requests), 3)
            corrected_prose_topic = adapter.schemas[2]["properties"]["sections"][
                "properties"
            ]["AI Dev Tools"]["properties"]["topics"]["items"]
            self.assertEqual(
                set(corrected_prose_topic["required"]), {"headline", "summary"}
            )
            self.assertNotIn("citation_refs", corrected_prose_topic["properties"])
            self.assertIn("replacement prose JSON object", adapter.requests[2])
            self.assertNotIn("citation_0001 leaked", adapter.requests[2])
            self.assertNotIn("citation_", (artifact / "final.md").read_text(encoding="utf-8"))


    def test_production_parity_preserves_selection_cost_when_prose_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            adapter = FailingProseStructuredFakeAdapter("fixture")

            report = run_evaluation(
                [adapter],
                {"production": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                cost_ceiling_usd=1.0,
                cost_ceiling_provider=adapter.provider,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            row = manifest["results"][0]
            artifact = output / row["artifact_dir"]
            self.assertEqual(row["status"], "provider_error")
            self.assertEqual(row["error"]["stage"], "first")
            self.assertEqual(len(row["error"]["completed_stage_calls"]), 1)
            self.assertEqual(row["error"]["completed_stage_calls"][0]["stage"], "selection")
            self.assertEqual(manifest["observed_ceiling_cost_usd"], 0.005)
            cost = report["operations"]["groups"][0]["cost"]
            self.assertEqual(cost["reported_calls"], 2)
            self.assertEqual(cost["total_usd"], 0.005)
            self.assertEqual(
                json.loads((artifact / "first-selection.json").read_text()),
                {
                    "schema_version": 1,
                    "sections": {
                        "AI Dev Tools": {
                            "topics": [{"citation_refs": ["citation_0001"]}]
                        }
                    },
                    "excluded_topics": {},
                },
            )
            selected_evidence = json.loads(
                (artifact / "first-selected-evidence.json").read_text()
            )
            self.assertNotIn("citation_", json.dumps(selected_evidence))
            self.assertNotIn("item_", json.dumps(selected_evidence))
            self.assertEqual(
                (artifact / "first-prose-request.txt").read_text(encoding="utf-8"),
                adapter.requests[1],
            )
            self.assertEqual(
                json.loads((artifact / "first-prose-schema.json").read_text()),
                adapter.schemas[1],
            )
            self.assertIsNone(
                json.loads((artifact / "first-prose.json").read_text())
            )

