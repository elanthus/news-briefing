from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from private_archive import (
    create_encrypted_archive,
    decrypt_archive,
    prune_corpora,
    restore_corpora_from_bytes,
    restore_corpora_from_tar,
)
from tests.test_briefing_output import fixture_contract


class PrivateArchiveTests(unittest.TestCase):
    def _corpus_bytes(self, day: str) -> bytes:
        corpus, _config, _projected, _output = fixture_contract()
        corpus["report_date"] = day
        return (json.dumps(corpus, ensure_ascii=False) + "\n").encode("utf-8")

    def test_encrypted_archive_round_trips_without_plaintext_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpora = root / "corpora"
            corpora.mkdir()
            source = corpora / "2026-08-20.json"
            source.write_bytes(self._corpus_bytes("2026-08-20"))
            encrypted = root / "corpus-archive.tar.gz.enc"
            plaintext = root / "restored.tar.gz"

            create_encrypted_archive([corpora], encrypted, "test passphrase")
            self.assertNotIn(source.read_bytes(), encrypted.read_bytes())
            decrypt_archive(encrypted, plaintext, "test passphrase")
            restored = restore_corpora_from_tar(plaintext, root / "restored")

            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].read_bytes(), source.read_bytes())

            tampered = bytearray(encrypted.read_bytes())
            tampered[len(tampered) // 2] ^= 1
            encrypted.write_bytes(tampered)
            with self.assertRaisesRegex(ValueError, "authentication failed"):
                decrypt_archive(encrypted, root / "tampered.tar.gz", "test passphrase")

    def test_wrong_passphrase_fails_before_decryption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpora = root / "corpora"
            corpora.mkdir()
            (corpora / "2026-08-20.json").write_bytes(
                self._corpus_bytes("2026-08-20")
            )
            encrypted = root / "corpus-archive.tar.gz.enc"
            create_encrypted_archive([corpora], encrypted, "correct passphrase")

            with self.assertRaisesRegex(ValueError, "authentication failed"):
                decrypt_archive(encrypted, root / "wrong.tar.gz", "wrong passphrase")

    def test_restore_refuses_path_traversal_and_unexpected_members(self) -> None:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            info = tarfile.TarInfo("../corpora/2026-08-20.json")
            content = self._corpus_bytes("2026-08-20")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "unexpected private corpus archive member"
        ):
            restore_corpora_from_bytes(payload.getvalue(), Path(directory))

    def test_restore_refuses_report_date_filename_mismatch(self) -> None:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            directory = tarfile.TarInfo("corpora")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            content = self._corpus_bytes("2026-08-19")
            info = tarfile.TarInfo("corpora/2026-08-20.json")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "report_date does not match"
        ):
            restore_corpora_from_bytes(payload.getvalue(), Path(directory))

    def test_restore_refuses_non_object_corpus_json(self) -> None:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            directory = tarfile.TarInfo("corpora")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            content = b"[]"
            info = tarfile.TarInfo("corpora/2026-08-20.json")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "not a JSON object"
        ):
            restore_corpora_from_bytes(payload.getvalue(), Path(directory))

    @staticmethod
    def _tar_members(rows: list[tuple[str, bytes]]) -> bytes:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            for name, content in rows:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        return payload.getvalue()

    def test_restore_skips_obsolete_dates_but_preserves_current_bytes(self) -> None:
        old = json.dumps({"schema_version": 6, "report_date": "2026-08-19"}).encode()
        current = self._corpus_bytes("2026-08-20")
        payload = self._tar_members([
            ("corpora/2026-08-19.json", old), ("corpora/2026-08-20.json", current),
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = restore_corpora_from_bytes(payload, root)
            self.assertEqual([path.name for path in paths], ["2026-08-20.json"])
            self.assertEqual(paths[0].read_bytes(), current)
            self.assertFalse((root / "2026-08-19.json").exists())

    def test_all_obsolete_archive_restores_an_empty_window(self) -> None:
        old = json.dumps({"schema_version": 6, "report_date": "2026-08-19"}).encode()
        payload = self._tar_members([("corpora/2026-08-19.json", old)])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(restore_corpora_from_bytes(payload, Path(directory)), ())

    def test_obsolete_members_still_require_unique_matching_dates(self) -> None:
        old = json.dumps({"schema_version": 6, "report_date": "2026-08-19"}).encode()
        for rows in ([('corpora/2026-08-20.json', old)],
                     [('corpora/2026-08-19.json', old)] * 2):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    restore_corpora_from_bytes(self._tar_members(rows), Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_or_future_versions_are_not_skipped(self) -> None:
        for version in (None, True, 0, -1, 8):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                content = json.dumps({"schema_version": version, "report_date": "2026-08-19"}).encode()
                with self.assertRaisesRegex(ValueError, "violates its schema"):
                    restore_corpora_from_bytes(
                        self._tar_members([("corpora/2026-08-19.json", content)]), Path(directory)
                    )

    def test_prune_keeps_only_fourteen_days_ending_at_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpora = Path(directory)
            for day in ("2026-08-20", "2026-08-07", "2026-08-06", "2026-08-21"):
                (corpora / f"{day}.json").write_bytes(self._corpus_bytes(day))

            removed = prune_corpora(corpora, date(2026, 8, 20))

            self.assertEqual(
                {path.name for path in removed},
                {"2026-08-06.json", "2026-08-21.json"},
            )
            self.assertTrue((corpora / "2026-08-20.json").is_file())
            self.assertTrue((corpora / "2026-08-07.json").is_file())

    def test_prune_counts_only_current_corpora_against_retained_size_limit(self) -> None:
        current = self._corpus_bytes("2026-08-20")
        obsolete = json.dumps({"schema_version": 6, "report_date": "2026-08-19"}).encode()
        with tempfile.TemporaryDirectory() as directory:
            corpora = Path(directory)
            (corpora / "2026-08-19.json").write_bytes(obsolete)
            (corpora / "2026-08-20.json").write_bytes(current)
            with patch("private_archive.MAX_RESTORED_BYTES", len(current)):
                removed = prune_corpora(corpora, date(2026, 8, 20))
            self.assertEqual([path.name for path in removed], ["2026-08-19.json"])
            self.assertEqual((corpora / "2026-08-20.json").read_bytes(), current)

    def test_prune_refuses_invalid_retained_corpus_before_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpora = Path(directory)
            (corpora / "2026-08-20.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "violates its schema"):
                prune_corpora(corpora, date(2026, 8, 20))

    def test_prune_refuses_invalid_json_download_before_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpora = Path(directory)
            (corpora / "2026-08-20.json").write_text(
                "{not a corpus", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                prune_corpora(corpora, date(2026, 8, 20))


if __name__ == "__main__":
    unittest.main()
