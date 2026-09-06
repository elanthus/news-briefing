import unittest

from publication_failures import MODEL_LABELS, parse_generation_failures, summarize_failed_chain


class PublicationFailureTests(unittest.TestCase):
    def test_raw_errors_become_fixed_codes(self):
        reasons = [
            'review_required: repeated_topic: private headline https://private.example',
            'ProviderError: openrouter returned no text content',
            'ProviderError: openrouter HTTP 400: {"secret":"private"}',
        ]
        models = list(MODEL_LABELS)
        log = {"status": "failed", "model_chain": models, "attempts": [
            {"model": model, "status": "failed", "failure_reason": reason}
            for model, reason in zip(models, reasons, strict=True)
        ]}
        failures = summarize_failed_chain(log)
        self.assertEqual([failure.reason for failure in failures],
                         ["duplicate_story", "empty_response", "invalid_request"])
        self.assertNotIn("private", str(failures))
        self.assertEqual(parse_generation_failures([f.payload() for f in failures]), failures)

    def test_partial_or_malformed_chain_does_not_claim_all_models_failed(self):
        for log in [None, {"status": "ready"}, {"status": "failed", "model_chain": [[]]},
                    {"status": "failed", "model_chain": list(MODEL_LABELS), "attempts": []}]:
            with self.subTest(log=log):
                self.assertEqual(summarize_failed_chain(log), ())

    def test_history_rejects_untrusted_public_failure_text(self):
        for raw in [None, [{}], [{"model": "<script>", "reason": "generation_failed"}],
                    [{"model": "tencent/hy3", "reason": "secret"}],
                    [{"model": "tencent/hy3", "reason": "generation_failed", "raw": "secret"}]]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_generation_failures(raw)
