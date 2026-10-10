import json
import tempfile
import unittest
from pathlib import Path

from agent_runner.checkpoint import sha256_file, write_json_atomic
from agent_runner.publication import verified_publication
from tests.test_semantic_repairs import make_run


def prepared_day(root: Path) -> tuple[Path, Path, str]:
    day = root / "2026-09-01"
    day.mkdir()
    original, headline = make_run(day)
    write_json_atomic(day / "fallback-log.json", {"status": "ready", "selected_run_dir": "run"})
    return day, original, headline


class PublicationArtifactTests(unittest.TestCase):
    def test_verified_fresh_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            selected, topics, hashes = verified_publication(day)
            self.assertEqual(selected, original)
            self.assertTrue(topics)
            self.assertEqual(hashes["final.md"], sha256_file(original / "final.md"))

    def test_tampered_artifacts_are_rejected(self) -> None:
        for artifact in ("selected-evidence.json", "attempt-02-structured.json", "final.md"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                day, original, _headline = prepared_day(Path(directory))
                target = original / artifact
                target.write_bytes(target.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    verified_publication(day)

    def test_equal_counts_do_not_prove_reference_position_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            manifest = json.loads((original / "manifest.json").read_text())
            attempt = next(row for row in manifest["attempts"] if row["index"] == manifest["final"]["attempt"])
            target = original / attempt["structured_artifact"]
            candidate = json.loads(target.read_text())
            section = next(value for value in candidate["sections"].values() if len(value["topics"]) >= 2)
            section["topics"][0]["citation_refs"], section["topics"][1]["citation_refs"] = (
                section["topics"][1]["citation_refs"], section["topics"][0]["citation_refs"])
            write_json_atomic(target, candidate)
            manifest["artifacts"][target.name] = sha256_file(target)
            write_json_atomic(original / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "aligns with frozen evidence"):
                verified_publication(day)

    def test_rehashed_evidence_must_equal_frozen_corpus_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            target = original / "selected-evidence.json"
            evidence = json.loads(target.read_text())
            section = next(value for value in evidence["sections"].values() if value["topics"])
            section["topics"][0]["evidence"][0]["summary"] = "Planted unrelated support."
            write_json_atomic(target, evidence)
            manifest = json.loads((original / "manifest.json").read_text())
            manifest["artifacts"][target.name] = sha256_file(target)
            write_json_atomic(original / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "frozen corpus projection"):
                verified_publication(day)

    def test_rehashed_candidate_prose_must_equal_published_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day, original, _headline = prepared_day(Path(directory))
            manifest = json.loads((original / "manifest.json").read_text())
            attempt = next(row for row in manifest["attempts"] if row["index"] == manifest["final"]["attempt"])
            target = original / attempt["structured_artifact"]
            candidate = json.loads(target.read_text())
            section = next(value for value in candidate["sections"].values() if value["topics"])
            section["topics"][0]["headline"] = "Different candidate headline"
            write_json_atomic(target, candidate)
            manifest["artifacts"][target.name] = sha256_file(target)
            write_json_atomic(original / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "Markdown differs"):
                verified_publication(day)
