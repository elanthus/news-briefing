from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from corpus_storage import STORAGE_MARKER, validate_storage_marker, write_storage_marker


class CorpusStorageTests(unittest.TestCase):
    def test_written_marker_round_trips_through_strict_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_storage_marker(Path(directory))

            validate_storage_marker(path)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), STORAGE_MARKER)

    def test_unknown_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus-storage.json"
            path.write_text('{"schema_version": 2}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unrecognized"):
                validate_storage_marker(path)

    def test_json_scalar_types_must_match_exactly(self) -> None:
        invalid_markers = (
            {
                **STORAGE_MARKER,
                "schema_version": True,
            },
            {
                **STORAGE_MARKER,
                "public_corpus_text": 0,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus-storage.json"
            for marker in invalid_markers:
                with self.subTest(marker=marker):
                    path.write_text(json.dumps(marker), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "unrecognized"):
                        validate_storage_marker(path)


if __name__ == "__main__":
    unittest.main()
