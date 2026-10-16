"""Committed evaluator protocols parse and bind to the committed fixture bytes."""
from __future__ import annotations

import hashlib
import json
import re
import unittest

from evaluator.runner import EVALUATOR_DIR, ROOT

PROTOCOLS = EVALUATOR_DIR / "protocols"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _hashed_entries(protocol: dict) -> list[dict]:
    entries = list(protocol.get("inputs", [])) + list(protocol.get("prompts", []))
    if isinstance(protocol.get("suite"), dict):
        entries.append(protocol["suite"])
    return entries


class ProtocolFilesTest(unittest.TestCase):
    def test_every_protocol_parses_with_an_id(self) -> None:
        paths = sorted(PROTOCOLS.glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                protocol = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(protocol, dict)
                self.assertIsInstance(protocol.get("protocol_id"), str)
                self.assertTrue(protocol["protocol_id"])
                for entry in _hashed_entries(protocol):
                    self.assertIsInstance(entry.get("path"), str)
                    self.assertRegex(entry.get("sha256", ""), SHA256)

    def test_draft_protocols_bind_current_fixture_bytes(self) -> None:
        drafts = [
            path for path in sorted(PROTOCOLS.glob("*.json"))
            if json.loads(path.read_text(encoding="utf-8")).get("status") == "draft_pending_freeze"
        ]
        self.assertIn(PROTOCOLS / "parity-v3.json", drafts)
        for path in drafts:
            protocol = json.loads(path.read_text(encoding="utf-8"))
            for entry in _hashed_entries(protocol):
                with self.subTest(protocol=path.name, path=entry["path"]):
                    target = ROOT / entry["path"]
                    self.assertTrue(target.is_file(), f"{entry['path']} missing")
                    self.assertEqual(
                        hashlib.sha256(target.read_bytes()).hexdigest(), entry["sha256"],
                        f"{entry['path']} changed; refreeze {path.name}",
                    )


if __name__ == "__main__":
    unittest.main()
