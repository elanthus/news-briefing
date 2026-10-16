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

    def test_unrun_protocols_bind_current_fixture_bytes(self) -> None:
        # A protocol that has not run yet must bind the bytes a run would use. Once a
        # run is authorized, its hashes become the historical record of that run; the
        # run itself verifies the tagged source. Historical protocols such as
        # parity-v1.json keep status frozen_for_run with a recorded authorized_at.
        unrun = []
        for path in sorted(PROTOCOLS.glob("*.json")):
            protocol = json.loads(path.read_text(encoding="utf-8"))
            authorized = (protocol.get("authorization") or {}).get("authorized_at")
            if protocol.get("status") == "draft_pending_freeze" or (
                protocol.get("status") == "frozen_for_run" and authorized is None
            ):
                unrun.append(path)
        parity_v3 = json.loads((PROTOCOLS / "parity-v3.json").read_text(encoding="utf-8"))
        if parity_v3["authorization"]["authorized_at"] is None:
            self.assertIn(PROTOCOLS / "parity-v3.json", unrun)
        for path in unrun:
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
