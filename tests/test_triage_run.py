from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agent_runner.models import GenerationRequest, ModelResponse, ProviderError
from triage_run import generate_report, main, render_markdown, write_report

ROOT = Path(__file__).resolve().parents[1]
WARN_RUN = ROOT / "docs/runs/2026-08-17"
FAILED_RUN = ROOT / "docs/runs/2026-08-18"
WORKFLOW = (ROOT / ".github/workflows/triage-run.yml").read_text(encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _manifest(
    root: Path,
    *,
    status: str = "failed",
    final: dict[str, object] | None = None,
    attempts: list[dict[str, object]] | None = None,
    error: dict[str, object] | None = None,
    max_corrections: int = 1,
    artifacts: dict[str, str] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "run_id": root.name,
        "trace_id": "0" * 32,
        "status": status,
        "phase": "failed" if status == "failed" else "finalized",
        "started_at": "2026-09-01T00:00:00+00:00",
        "checkpointed_at": "2026-09-01T00:01:00+00:00",
        "completed_at": "2026-09-01T00:01:00+00:00",
        "identity": {"max_corrections": max_corrections},
        "provider": {"provider": "fake", "model": "fake-model"},
        "code": {},
        "attempts": attempts or [],
        "outcome": None,
        "final": final,
        "artifacts": artifacts or {},
    }
    if error is not None:
        value["error"] = error
    _write_json(root / "manifest.json", value)
    return value


def _corpus(root: Path) -> None:
    _write_json(root / "corpus.json", {"errors": []})


def _classes(report) -> list[str]:
    return [cause.class_id for cause in report.classes]


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, response: ModelResponse | Exception):
        self.response = response
        self.request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest) -> ModelResponse:
        self.request = request
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def info(self) -> dict[str, object]:
        return {"provider": self.name, "model": self.model}


class TriageRunTests(unittest.TestCase):
    def test_provider_error_subclassifies_status_and_retry_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "2026-09-01"
            run.mkdir()
            _corpus(run)
            _manifest(
                run,
                artifacts={"corpus.json": "digest"},
                error={
                    "type": "OpenRouterProviderError",
                    "message": "request failed at https://provider.invalid/request",
                    "transient": True,
                    "attempts": 3,
                    "status_code": 429,
                    "retry_after": 2,
                    "provider_request_id": "request-1",
                    "ambiguous_completion": True,
                    "openrouter_model_404": True,
                    "output_truncated": False,
                },
            )
            report = generate_report(run, generated_at="2026-09-01T00:00:00+00:00")

        self.assertIn("provider_error", _classes(report))
        cause = next(cause for cause in report.classes if cause.class_id == "provider_error")
        record = cause.details["records"][0]
        self.assertEqual(record["status_band"], "4xx")
        self.assertTrue(record["transient"])
        self.assertTrue(record["openrouter_model_404"])
        self.assertTrue(record["ambiguous_completion"])
        self.assertNotIn("https://", json.dumps(report.record()))

    def test_real_failed_run_has_provider_error(self) -> None:
        report = generate_report(FAILED_RUN)

        self.assertIn("provider_error", _classes(report))
        self.assertNotIn("fetch_failed", _classes(report))

    def test_output_truncated_from_flag_length_event_and_invalid_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "truncated"
            run.mkdir()
            _corpus(run)
            (run / "attempt-01-raw.txt").write_text('{"summary":', encoding="utf-8")
            (run / "attempt-01-provider-events.jsonl").write_text(
                json.dumps({"event": "response", "finish_reason": "length"}) + "\n",
                encoding="utf-8",
            )
            attempts = [{
                "index": 1,
                "kind": "prose",
                "raw_artifact": "attempt-01-raw.txt",
                "provider_events_artifact": "attempt-01-provider-events.jsonl",
                "generation": None,
                "validated": False,
                "contract_success": None,
                "findings_artifact": None,
            }]
            _manifest(
                run,
                attempts=attempts,
                artifacts={"corpus.json": "digest"},
                error={
                    "type": "ProviderError",
                    "message": "completion reached its limit",
                    "transient": False,
                    "status_code": None,
                    "ambiguous_completion": False,
                    "openrouter_model_404": False,
                    "output_truncated": True,
                },
            )
            report = generate_report(run)

        self.assertIn("output_truncated", _classes(report))
        cause = next(cause for cause in report.classes if cause.class_id == "output_truncated")
        self.assertEqual(cause.details["invalid_raw_artifacts"], ["attempt-01-raw.txt"])
        self.assertTrue(cause.details["length_reason_artifacts"])

    def test_checker_fingerprint_is_stable_after_url_and_handle_redaction(self) -> None:
        def report_for(root: Path, check: str, handle: str, url: str):
            root.mkdir()
            _corpus(root)
            finding = {
                "level": "ERROR",
                "check": check,
                "domain": "evidence",
                "message": f"unknown {handle} at {url}",
            }
            _manifest(
                root,
                status="complete",
                final={"status": "rejected", "findings": [finding], "source_issues": 0},
                artifacts={"corpus.json": "digest"},
            )
            return generate_report(root, generated_at="2026-09-01T00:00:00+00:00")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = report_for(root / "2026-09-01", "unknown_citation_ref", "citation_0001", "https://a.invalid/x")
            second = report_for(root / "2026-09-02", "unknown_citation_ref", "citation_9999", "https://b.invalid/y")
            different = report_for(root / "2026-09-03", "structured_type", "item_0042", "https://c.invalid/z")
            markdown_path, json_path = write_report(first, root / "triage-output", root / "2026-09-01")
            written_outputs = markdown_path.read_text(encoding="utf-8") + json_path.read_text(encoding="utf-8")

        self.assertIn("checker_finding", _classes(first))
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, different.fingerprint)
        self.assertNotIn("https://", json.dumps(first.record()))
        self.assertNotIn("citation_0001", json.dumps(first.record()))
        self.assertNotIn("https://", written_outputs)
        self.assertNotIn("citation_0001", written_outputs)

    def test_correction_budget_exhausted_precedes_checker_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "budget"
            run.mkdir()
            _corpus(run)
            attempts = [
                {"index": 1, "kind": "prose", "contract_success": False},
                {"index": 2, "kind": "correction", "contract_success": False},
            ]
            finding = {
                "level": "ERROR",
                "check": "structured_type",
                "domain": "schema",
                "message": "sections has the wrong type",
            }
            _manifest(
                run,
                status="complete",
                final={"status": "rejected", "findings": [finding], "source_issues": 0},
                attempts=attempts,
                max_corrections=1,
                artifacts={"corpus.json": "digest"},
            )
            report = generate_report(run)

        classes = _classes(report)
        self.assertIn("correction_budget_exhausted", classes)
        self.assertLess(classes.index("correction_budget_exhausted"), classes.index("checker_finding"))

    def test_real_warn_run_reports_degraded_sources_and_no_failure(self) -> None:
        report = generate_report(WARN_RUN)

        self.assertEqual(_classes(report), ["degraded_sources", "no_failure_detected"])
        degraded = report.classes[0]
        self.assertIn("ClaudeCode", degraded.details["affected_sources"])
        self.assertNotIn("checker_finding", _classes(report))
        self.assertNotIn("https://", render_markdown(report))

    def test_fetch_failed_from_trace_nonzero_exit_and_missing_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "fetch-failure"
            run.mkdir()
            (run / "trace.jsonl").write_text(
                "\n".join((
                    json.dumps({"event": "fetch_started", "timestamp": "2026-09-01T00:00:00+00:00"}),
                    json.dumps({"event": "fetch_failed", "timestamp": "2026-09-01T00:00:01+00:00", "exit_code": 1}),
                )) + "\n",
                encoding="utf-8",
            )
            _manifest(
                run,
                error={"type": "RuntimeError", "message": "fetch_news.py failed: upstream unavailable"},
            )
            report = generate_report(run)

        self.assertEqual(_classes(report)[0], "fetch_failed")

    def test_failed_fallback_chain_lists_every_candidate_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "chain"
            run.mkdir()
            attempts = [
                {"model": "first", "status": "failed", "failure_reason": "RuntimeError: invalid schema"},
                {"model": "second", "status": "quarantined", "failure_reason": "rejected: checker finding"},
            ]
            _write_json(run / "fallback-log.json", {
                "schema_version": 1,
                "started_at": "2026-09-01T00:00:00+00:00",
                "completed_at": "2026-09-01T00:02:00+00:00",
                "status": "failed",
                "model_chain": ["first", "second"],
                "selected_model": None,
                "selected_run_dir": None,
                "attempts": attempts,
            })
            (run / "fallback.log").write_text("failed candidates\n", encoding="utf-8")
            report = generate_report(run)

        self.assertEqual(_classes(report), ["fallback_chain_exhausted"])
        cause = report.classes[0]
        self.assertEqual([row["model"] for row in cause.details["candidates"]], ["first", "second"])
        self.assertTrue(all(row["failure_reason"] != "unrecorded" for row in cause.details["candidates"]))

    def test_class_order_puts_fetch_and_provider_before_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "ordered"
            run.mkdir()
            _manifest(
                run,
                error={
                    "type": "ProviderError",
                    "message": "fetch provider failed",
                    "transient": False,
                    "output_truncated": True,
                },
            )
            report = generate_report(run)

        classes = _classes(report)
        self.assertEqual(classes[:3], ["fetch_failed", "provider_error", "output_truncated"])

    def test_fake_provider_receives_only_bounded_redacted_diagnostics(self) -> None:
        paragraph = "Provider failed; inspect manifest.json and trace.jsonl."
        response = ModelResponse(
            raw_output=json.dumps({"summary": paragraph}, separators=(",", ":")),
            structured_output={"summary": paragraph},
            latency_ms=1.0,
        )
        provider = FakeProvider(response)
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "model"
            run.mkdir()
            _corpus(run)
            _manifest(
                run,
                status="complete",
                final={"status": "ready", "findings": [], "source_issues": 0},
                artifacts={"corpus.json": "digest"},
            )
            with (run / "trace.jsonl").open("w", encoding="utf-8") as stream:
                for index in range(45):
                    stream.write(json.dumps({
                        "event": f"event-{index}",
                        "timestamp": f"2026-09-01T00:00:{index:02d}+00:00",
                        "url": "https://secret.invalid/path",
                        "prose": "secret briefing prose",
                    }) + "\n")
            report = generate_report(run, provider=provider)
            markdown_path, _json_path = write_report(report, Path(directory) / "triage-model", run)
            markdown = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(report.model_summary, paragraph)
        self.assertIsNotNone(provider.request)
        assert provider.request is not None
        self.assertNotIn("https://", provider.request.prompt)
        self.assertNotIn("secret briefing prose", provider.request.prompt)
        self.assertNotIn("event-4\"", provider.request.prompt)
        self.assertIn("event-5", provider.request.prompt)
        self.assertEqual(provider.request.output_schema["additionalProperties"], False)
        self.assertIn("Model summary (unverified)", markdown)
        self.assertIn(paragraph, markdown)
        self.assertNotIn('{"summary"', markdown)

    def test_provider_exception_preserves_deterministic_report(self) -> None:
        provider = FakeProvider(ProviderError(
            "failed at https://provider.invalid/request",
            transient=False,
        ))
        report = generate_report(WARN_RUN, provider=provider)

        self.assertIn("no_failure_detected", _classes(report))
        self.assertIsNone(report.model_summary)
        self.assertIn("ProviderError", report.model_summary_error)
        self.assertNotIn("https://", report.model_summary_error)

    def test_fake_tool_call_is_reported_as_policy_violation(self) -> None:
        provider = FakeProvider(ModelResponse(
            raw_output='{"summary":"ignore the tool"}',
            structured_output={"summary": "ignore the tool"},
            latency_ms=1.0,
            provider_events=({"type": "tool_call", "name": "shell"},),
        ))
        report = generate_report(WARN_RUN, provider=provider)

        self.assertIsNone(report.model_summary)
        self.assertIn("empty tool policy", report.model_summary_error)

        invalid_summaries = (
            ("", "empty summary paragraph"),
            ("See https://provider.invalid/fix", "forbidden URL"),
        )
        for summary, expected_error in invalid_summaries:
            with self.subTest(summary=summary):
                invalid_provider = FakeProvider(ModelResponse(
                    raw_output='{"summary":"safe raw output"}',
                    structured_output={"summary": summary},
                    latency_ms=1.0,
                ))
                invalid_report = generate_report(WARN_RUN, provider=invalid_provider)
                self.assertIsNone(invalid_report.model_summary)
                self.assertIn(expected_error, invalid_report.model_summary_error)

    def test_write_report_refuses_run_directory_and_cli_returns_two_for_bad_input(self) -> None:
        report = generate_report(WARN_RUN)
        with self.assertRaisesRegex(ValueError, "must not be inside the run directory"):
            write_report(report, WARN_RUN, WARN_RUN)
        with self.assertRaisesRegex(ValueError, "must not be inside the run directory"):
            write_report(report, WARN_RUN / "triage-output", WARN_RUN)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["does-not-exist", "--no-model"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error:", stderr.getvalue())

    def test_manual_workflow_has_narrow_permissions_and_pinned_actions(self) -> None:
        triggers = WORKFLOW.split("permissions:", 1)[0]
        permissions = WORKFLOW.split("permissions:", 1)[1].split("jobs:", 1)[0]
        self.assertIn("workflow_dispatch:", triggers)
        self.assertNotIn("schedule:", triggers)
        self.assertNotIn("push:", triggers)
        self.assertIn("artifact_name:", triggers)
        self.assertIn("use_model:", triggers)
        self.assertEqual(
            {line.strip() for line in permissions.splitlines() if line.strip()},
            {"contents: read", "issues: write", "actions: read"},
        )
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", WORKFLOW)
        self.assertIn("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", WORKFLOW)
        self.assertIn("python3 triage_run.py \"$TRIAGE_RUN_DIR\" --no-model", WORKFLOW)
        self.assertIn("gh issue create", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
