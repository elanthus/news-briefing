import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluator.research import migrate, verify


def make_run(root: Path) -> tuple[Path, str]:
    # Core fixtures are deliberately outside the evaluator's typed module set.
    return importlib.import_module("tests.test_semantic_repairs").make_run(root)


class ResearchMigrationTests(unittest.TestCase):
    def test_captures_originals_and_rebuilds_verified_views_without_rewriting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            study = root / "study"
            study.mkdir()
            run, _headline = make_run(study)
            original = (run / "manifest.json").read_bytes()
            (study / "old-review.json").write_text('{"schema_version":1,"unknown_history":true}')
            (study / "same-review.json").write_bytes((study / "old-review.json").read_bytes())
            output = root / "dataset"
            receipt = migrate(root, output, collections={"diagnostic": "study"})
            self.assertEqual(receipt["ready_records"], 1)
            self.assertLess(receipt["unique_blobs"], receipt["files"])
            self.assertEqual((run / "manifest.json").read_bytes(), original)
            self.assertEqual(verify(output), receipt)
            manifest = json.loads((output / "manifest.json").read_bytes())
            self.assertEqual(manifest["records"][0]["state"], "ready")
            self.assertIn("study/old-review.json", manifest["files"])
            modified = copy.deepcopy(manifest)
            modified["records"][0]["topics"][0]["prose"] = "An invented claim."
            (output / "manifest.json").write_text(json.dumps(modified))
            with self.assertRaisesRegex(ValueError, "captured source"):
                verify(output)

    def test_failed_and_obsolete_runs_remain_visible_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            study = root / "study"
            study.mkdir()
            run, _headline = make_run(study)
            path = run / "manifest.json"
            manifest = json.loads(path.read_bytes())
            manifest["status"] = "failed"
            path.write_text(json.dumps(manifest))
            output = root / "dataset"
            receipt = migrate(root, output, collections={"diagnostic": "study"})
            self.assertEqual(receipt["unavailable_records"], 1)
            data = json.loads((output / "manifest.json").read_bytes())
            self.assertIsNone(data["records"][0]["topics"])
            self.assertIn("study/run/final.md", data["files"])

    def test_path_escape_overlap_symlinks_and_existing_outputs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "study").mkdir()
            (root / "study/file").write_text("retained")
            for collections in ({"bad": "../study"}, {"a": "study", "b": "study/child"}):
                with self.subTest(collections=collections), self.assertRaises(ValueError):
                    migrate(root, root / "dataset", collections=collections)
            (root / "study/link").symlink_to(root / "study/file")
            with self.assertRaises(ValueError):
                migrate(root, root / "dataset", collections={"a": "study"})
            self.assertFalse((root / "dataset").exists())
            (root / "study/link").unlink()
            (root / "dataset").mkdir()
            with self.assertRaises(ValueError):
                migrate(root, root / "dataset", collections={"a": "study"})

    def test_blob_tampering_and_unsupported_schema_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "study").mkdir()
            (root / "study/input.json").write_text('{"record":"original"}')
            output = root / "dataset"
            migrate(root, output, collections={"a": "study"})
            manifest = json.loads((output / "manifest.json").read_bytes())
            digest = manifest["files"]["study/input.json"]["sha256"]
            (output / "blobs" / digest).write_text("changed")
            with self.assertRaisesRegex(ValueError, "differs from inventory"):
                verify(output)
            manifest["schema_version"] = True
            (output / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "schema"):
                verify(output)


if __name__ == "__main__":
    unittest.main()
