import copy
import tempfile
import unittest
from pathlib import Path

from agent_runner.checkpoint import RunStore
from agent_runner.failures import FailureRecord, parse_failure, run_failure
from agent_runner.models import ProviderError
from publication_failures import summarize_failed_chain
from triage_run import generate_report


class FailureRecordTests(unittest.TestCase):
    def test_provider_codes_are_independent_of_exception_wording(self):
        cases = [
            ({"status_code": 429, "transient": True}, "rate_limited"),
            ({"status_code": 503, "transient": True}, "provider_unavailable"),
            ({"transient": True}, "provider_unavailable"),
            ({"status_code": 400, "transient": False}, "invalid_request"),
            ({"transient": False, "empty_response": True}, "empty_response"),
            ({"transient": False, "output_truncated": True}, "output_truncated"),
            ({"transient": False}, "provider_error"),
        ]
        for kwargs, code in cases:
            with self.subTest(code=code):
                first = ProviderError("arbitrary private message", **kwargs).record()
                second = ProviderError("review_required: repeated_topic: fake", **kwargs).record()
                self.assertEqual(first["failure"], second["failure"])
                self.assertEqual(first["failure"]["code"], code)
                self.assertIsNotNone(parse_failure(first["failure"]))

    def test_malformed_records_fail_closed_and_never_publish_text(self):
        base = FailureRecord("rate_limited", status_code=429, transient=True).payload()
        for update in [
            {"code": "secret"}, {"code": []}, {"status_code": True},
            {"status_code": 999}, {"transient": "yes"}, {"checks": ["<script>secret"]},
            {"checks": ["valid"] * 101}, {"extra": "secret"}, {"stage": []},
            {"output_truncated": True}, {"corrections_used": -1},
        ]:
            with self.subTest(update=update):
                malformed = {**base, **update}
                self.assertIsNone(parse_failure(malformed))
                result = summarize_failed_chain({
                    "schema_version": 2, "status": "failed", "model_chain": ["tencent/hy3"],
                    "attempts": [{"model": "tencent/hy3", "status": "failed",
                                  "failure": malformed, "failure_reason": "secret"}],
                })
                self.assertEqual(result[0].reason, "generation_failed")
                self.assertNotIn("secret", str(result))

    def test_finalization_records_correction_exhaustion_by_stage(self):
        for stage, kind in [("selection", "selection_correction"), ("prose", "correction")]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                store = RunStore.create(Path(directory) / "run", identity={"max_corrections": 1},
                                        provider={}, code={})
                store.manifest["attempts"] = [{"kind": kind}]
                final = {"status": "rejected", "findings": [
                    {"level": "ERROR", "check": "unknown_citation_ref", "message": "private"}
                ]}
                store.finalize(final)
                failure = parse_failure(store.manifest["final"]["failure"])
                self.assertIsNotNone(failure)
                self.assertEqual(failure.code, "correction_exhausted")
                self.assertEqual(failure.stage, stage)
                self.assertEqual(failure.checks, ("unknown_citation_ref",))

    def test_fallback_projection_preserves_originating_provider_fields(self):
        error = ProviderError("private", transient=True, status_code=429).record()
        record = run_failure({"error": error})
        self.assertEqual(record, parse_failure(error["failure"]))
        self.assertEqual(run_failure(None, error), record)
        self.assertEqual(run_failure({"error": {"message": "HTTP 429"}}).code, "generation_failed")

    def test_invalid_json_and_embedded_length_text_do_not_prove_truncation(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw.txt").write_text('{"unfinished":')
            (root / "events.jsonl").write_text(json.dumps({"untrusted": {"finish_reason": "length"}}))
            (root / "manifest.json").write_text(json.dumps({
                "attempts": [{"raw_artifact": "raw.txt", "provider_events_artifact": "events.jsonl"}],
                "error": {"type": "ProviderError", "message": "output truncated"},
            }))
            report = generate_report(root)
            self.assertNotIn("output_truncated", [cause.class_id for cause in report.classes])

    def test_public_code_is_unchanged_when_private_message_changes(self):
        raw = {"schema_version": 2, "status": "failed", "model_chain": ["tencent/hy3"],
               "attempts": [{"model": "tencent/hy3", "status": "failed",
                             "failure": FailureRecord("rate_limited", status_code=429).payload(),
                             "failure_reason": "ProviderError: openrouter HTTP 400: private"}]}
        changed = copy.deepcopy(raw)
        changed["attempts"][0]["failure_reason"] = "rejected: repeated_topic: private"
        self.assertEqual(summarize_failed_chain(raw), summarize_failed_chain(changed))
        self.assertEqual(summarize_failed_chain(raw)[0].reason, "rate_limited")
