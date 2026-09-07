import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent_runner.checkpoint import RunStore
from agent_runner.failures import FailureRecord, final_failure, parse_failure, run_failure
from agent_runner.models import ProviderError
from publication_failures import summarize_failed_chain
from run_daily_briefing import PRODUCTION_MODEL_CHAIN, _write_chain_logs
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

    def test_versioned_and_original_records_have_an_explicit_read_contract(self):
        current = FailureRecord("rate_limited", status_code=429).payload()
        original = {key: value for key, value in current.items() if key != "schema_version"}
        self.assertEqual(parse_failure(original), parse_failure(current))
        for version in (0, 2, True, "1", None):
            with self.subTest(version=version):
                self.assertIsNone(parse_failure({**current, "schema_version": version}))

    def test_finalized_checker_result_precedes_recoverable_provider_error(self):
        error = ProviderError("private", transient=True, status_code=429).record()
        for code in ("validation_failed", "correction_exhausted"):
            with self.subTest(code=code):
                final = FailureRecord(code, checks=("invalid_shape",), stage="prose",
                                      corrections_used=1, correction_limit=1)
                record = run_failure({"error": error, "correction_error": error,
                                      "final": {"status": "rejected", "failure": final.payload()}})
                self.assertEqual(record, final)
                public = summarize_failed_chain({
                    "schema_version": 2, "status": "failed", "model_chain": ["tencent/hy3"],
                    "attempts": [{"model": "tencent/hy3", "status": "quarantined",
                                  "failure": record.payload()}],
                })
                self.assertEqual(public[0].reason, code)
        self.assertEqual(run_failure({"final": {"failure": None}, "correction_error": error}).code,
                         "rate_limited")

    def test_damaged_resume_metadata_does_not_break_finalization(self):
        for attempts in (None, "bad", {}, [], [None], [{"kind": "correction"}, None],
                         [None, {"kind": "correction"}], [{"kind": []}]):
            for identity in (None, [], "bad", {"max_corrections": 1}):
                with self.subTest(attempts=attempts, identity=identity), tempfile.TemporaryDirectory() as directory:
                    store = RunStore.create(Path(directory) / "run", identity={}, provider={}, code={})
                    store.manifest.update(attempts=attempts, identity=identity)
                    store.finalize({"status": "rejected", "findings": [
                        {"level": "ERROR", "check": "invalid_shape"}, None,
                    ]})
                    record = parse_failure(store.manifest["final"]["failure"])
                    self.assertIsNotNone(record)
                    self.assertEqual(record.code, "validation_failed")
                    self.assertIsNone(record.stage)
                    self.assertIsNone(record.corrections_used)
                    self.assertEqual(store.manifest["status"], "complete")
        for identity in (None, [], "bad"):
            record = final_failure({"status": "rejected", "findings": [
                {"level": "ERROR", "check": "invalid_shape"},
            ]}, {"attempts": [{"kind": "correction"}], "identity": identity})
            self.assertEqual(record.code, "validation_failed")
            self.assertIsNone(record.correction_limit)
            self.assertEqual(record.corrections_used, 1)

    def test_disabled_corrections_are_validation_failure_not_exhaustion(self):
        record = final_failure({"status": "rejected", "findings": [
            {"level": "ERROR", "check": "invalid_shape"},
        ]}, {"attempts": [{"kind": "prose"}], "identity": {"max_corrections": 0}})
        self.assertEqual(record.code, "validation_failed")
        self.assertEqual(record.corrections_used, 0)
        self.assertEqual(record.correction_limit, 0)
        self.assertEqual(parse_failure(record.payload()), record)
        self.assertIsNone(parse_failure({**record.payload(), "code": "correction_exhausted"}))

    def test_incomplete_chain_and_legacy_failed_chain_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [{"model": candidate.model, "status": "failed", "run_dir": f"candidate-{index}",
                     "completed_at": "2026-09-07T00:00:00Z", "failure_reason": None,
                     "failure": FailureRecord("provider_error").payload()}
                    for index, candidate in enumerate(PRODUCTION_MODEL_CHAIN[:2])]
            _write_chain_logs(root, "2026-09-07T00:00:00Z", rows)
            path = root / "fallback-log.json"
            chain = json.loads(path.read_text())
            self.assertEqual(parse_failure(chain["failure"]).code, "chain_incomplete")
            self.assertEqual(summarize_failed_chain(chain), ())
            for failure, expected in ((chain["failure"], "fallback_chain_incomplete"),
                                      (None, "fallback_chain_failed")):
                chain["failure"] = failure
                path.write_text(json.dumps(chain))
                report = generate_report(root)
                cause = next(cause for cause in report.classes if cause.class_id == expected)
                self.assertEqual(len(cause.details["candidates"]), 2)
                self.assertNotIn("fallback_chain_exhausted", [cause.class_id for cause in report.classes])

    def test_unexpected_model_summary_error_uses_a_neutral_private_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"status": "complete",
                                                           "final": {"status": "ready"}}))
            provider = Mock()
            provider.generate.side_effect = TypeError("private sensitive error")
            report = generate_report(root, provider=provider)
            self.assertEqual(report.model_summary_error, "model_summary_failed")
            self.assertIsNone(report.model_summary)
