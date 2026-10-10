import unittest

from publication_schema import Provenance, parse_provenance, parse_workflow_run_id, provenance_payload

VALID_PAYLOAD = {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-flash-0731",
    "attempt_index": 2,
    "attempt_count": 3,
    "selection_corrections": 1,
    "prose_corrections": 0,
    "repair_action_count": 0,
    "prompt_sha256": "a" * 64,
}


class ProvenanceSchemaTests(unittest.TestCase):
    def test_absent_provenance_parses_to_none(self):
        self.assertIsNone(parse_provenance(None))

    def test_valid_provenance_round_trips_through_its_payload(self):
        provenance = parse_provenance(VALID_PAYLOAD)
        self.assertIsInstance(provenance, Provenance)
        assert provenance is not None
        self.assertEqual(provenance_payload(provenance), VALID_PAYLOAD)
        self.assertEqual(parse_provenance(provenance_payload(provenance)), provenance)

    def test_malformed_provenance_fails_closed(self):
        cases = [
            "not-a-dict",
            {**VALID_PAYLOAD, "extra": "field"},
            {k: v for k, v in VALID_PAYLOAD.items() if k != "model"},
            {**VALID_PAYLOAD, "provider": ""},
            {**VALID_PAYLOAD, "model": 7},
            {**VALID_PAYLOAD, "prompt_sha256": "not-hex"},
            {**VALID_PAYLOAD, "prompt_sha256": "A" * 64},  # uppercase hex is rejected
            {**VALID_PAYLOAD, "selection_corrections": -1},
            {**VALID_PAYLOAD, "prose_corrections": True},  # bool is not an int count
            {**VALID_PAYLOAD, "attempt_index": 4, "attempt_count": 3},
            {**VALID_PAYLOAD, "attempt_index": 0},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_provenance(raw)


class WorkflowRunIdentityTests(unittest.TestCase):
    def test_optional_identity_accepts_only_bounded_integers_or_ascii_decimal_text(self):
        for value in (1, 10**18, "123", "000123"):
            with self.subTest(value=value):
                self.assertEqual(parse_workflow_run_id(value), int(value))
        for value in (None, True, 0, -1, 10**18 + 1, 1.0, "", "0", "-1", "+1",
                      "1.0", " 123", "１２３", "١٢٣", "1" * 20):
            with self.subTest(value=value):
                self.assertIsNone(parse_workflow_run_id(value))


if __name__ == "__main__":
    unittest.main()
