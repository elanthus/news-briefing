from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stderr
from datetime import date
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import restore_private_corpora
from private_archive import create_encrypted_archive
from tests.test_briefing_output import fixture_contract


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RedirectingOpener:
    def __init__(self, location: str = "https://objects.example.test/signed-artifact"):
        self.request: urllib.request.Request | None = None
        self.location = location

    def open(self, request: urllib.request.Request, timeout: int):
        self.request = request
        headers = Message()
        headers["Location"] = self.location
        raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)


class StaticOpener:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.request: urllib.request.Request | None = None

    def open(self, request: urllib.request.Request, timeout: int):
        self.request = request
        return FakeResponse(self.payload)


class RestorePrivateCorporaTests(unittest.TestCase):
    def _artifact_zip(self, root: Path, passphrase: str, *, title_suffix="") -> bytes:
        corpora = root / "corpora"
        corpora.mkdir()
        corpus, _config, _projected, _output = fixture_contract()
        corpus["report_date"] = "2026-08-20"
        next(iter(corpus["categories"].values()))[0]["title"] += title_suffix
        expected = (json.dumps(corpus, ensure_ascii=False) + "\n").encode("utf-8")
        (corpora / "2026-08-20.json").write_bytes(expected)
        encrypted = root / restore_private_corpora.ENCRYPTED_MEMBER
        create_encrypted_archive([corpora], encrypted, passphrase)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, mode="w") as archive:
            archive.writestr(restore_private_corpora.ENCRYPTED_MEMBER, encrypted.read_bytes())
        return payload.getvalue()

    def _artifact_with_bad_mac(self, artifact: bytes) -> bytes:
        with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
            encrypted = bytearray(archive.read(restore_private_corpora.ENCRYPTED_MEMBER))
        encrypted[-1] ^= 1
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, mode="w") as archive:
            archive.writestr(restore_private_corpora.ENCRYPTED_MEMBER, encrypted)
        return payload.getvalue()

    @staticmethod
    def _workflow_run(
        branch: str = "main", head_repository_id: int = 7, repository_id: int = 7
    ) -> dict[str, object]:
        return {
            "head_branch": branch,
            "head_repository_id": head_repository_id,
            "repository_id": repository_id,
        }

    def _local_wrapper(self, root, artifact):
        wrappers = root / "wrappers"
        wrappers.mkdir()
        (wrappers / "10.zip").write_bytes(artifact)
        (wrappers / "manifest.json").write_text(json.dumps({"artifacts": [
            {"name": "github-pages", "id": 20},
            {"name": restore_private_corpora.DEFAULT_ARTIFACT_NAME, "id": 10,
             "sha256": hashlib.sha256(artifact).hexdigest(), "bytes": len(artifact),
             "workflow_run": self._workflow_run()}]}))
        return wrappers

    def test_local_restore_authenticates_without_network_or_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrappers = self._local_wrapper(root, self._artifact_zip(root, "secret"))
            with patch.object(restore_private_corpora, "_api_request", side_effect=AssertionError("network")):
                paths = restore_private_corpora.restore_local(
                    wrappers, root / "output", "secret", date(2026, 8, 20))
            self.assertEqual([p.name for p in paths], ["2026-08-20.json"])

    def test_local_restore_rejects_digest_authentication_symlinks_and_bounds(self):
        for mutation in ("digest", "mac", "symlink", "size", "id", "zip"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifact = self._artifact_zip(root, "secret")
                if mutation == "mac":
                    artifact = self._artifact_with_bad_mac(artifact)
                if mutation == "zip":
                    artifact = b"not a zip"
                wrappers = self._local_wrapper(root, artifact)
                path = wrappers / "manifest.json"
                manifest = json.loads(path.read_text())
                row = manifest["artifacts"][1]
                if mutation == "digest":
                    row["sha256"] = "0" * 64
                if mutation == "size":
                    row["bytes"] = restore_private_corpora.MAX_ARTIFACT_BYTES + 1
                if mutation == "id":
                    row["id"] = "../10"
                if mutation == "symlink":
                    (wrappers / "10.zip").unlink()
                    (wrappers / "10.zip").symlink_to(root / "missing")
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    restore_private_corpora.restore_local(wrappers, root / "output", "secret", date(2026, 8, 20))
                self.assertFalse((root / "output").exists())

    def test_local_restore_rejects_missing_branch_and_fork_provenance(self):
        for provenance in (None, self._workflow_run(branch="feature"), self._workflow_run(head_repository_id=8)):
            with self.subTest(provenance=provenance), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                wrappers = self._local_wrapper(root, self._artifact_zip(root, "secret"))
                path = wrappers / "manifest.json"
                value = json.loads(path.read_text())
                value["artifacts"][1]["workflow_run"] = provenance
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "workflow provenance"):
                    restore_private_corpora.restore_local(wrappers, root / "output", "secret", date(2026, 8, 20))
                self.assertFalse((root / "output").exists())

    def test_local_restore_merges_identical_dates_and_refuses_conflicts(self):
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                first, second = root / "first", root / "second"
                first.mkdir()
                second.mkdir()
                artifact = self._artifact_zip(first, "secret")
                other = self._artifact_zip(second, "secret", title_suffix=" changed" if conflicting else "")
                wrappers = self._local_wrapper(root, artifact)
                (wrappers / "11.zip").write_bytes(other)
                path = wrappers / "manifest.json"
                value = json.loads(path.read_text())
                value["artifacts"].append({"name": restore_private_corpora.DEFAULT_ARTIFACT_NAME, "id": 11,
                    "sha256": hashlib.sha256(other).hexdigest(), "bytes": len(other),
                    "workflow_run": self._workflow_run()})
                # A supplemented copy leaves the preservation manifest unchanged.
                supplement = root / "supplement.json"
                supplement.write_text(json.dumps(value))
                original = path.read_bytes()
                if conflicting:
                    with self.assertRaisesRegex(ValueError, "conflicting preserved corpora"):
                        restore_private_corpora.restore_local(wrappers, root / "output", "secret", date(2026, 8, 20),
                                                             manifest_path=supplement)
                    self.assertFalse((root / "output").exists())
                else:
                    paths = restore_private_corpora.restore_local(
                        wrappers, root / "output", "secret", date(2026, 8, 20), manifest_path=supplement)
                    self.assertEqual([p.name for p in paths], ["2026-08-20.json"])
                self.assertEqual(path.read_bytes(), original)

    def test_restore_downloads_decrypts_and_validates_corpus_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = self._artifact_zip(root, "archive secret")
            with (
                patch.object(
                    restore_private_corpora,
                    "_artifact_candidates",
                    return_value=(restore_private_corpora.ArtifactCandidate(
                        10, "https://api.github.test/artifact.zip"
                    ),),
                ),
                patch.object(
                    restore_private_corpora,
                    "_download_without_forwarding_token",
                    return_value=artifact,
                ),
            ):
                restored = restore_private_corpora.restore(
                    "owner/repository", root / "restored", "github token", "archive secret",
                    date(2026, 8, 20),
                )

            self.assertEqual([path.name for path in restored], ["2026-08-20.json"])
            self.assertIn(b'"report_date": "2026-08-20"', restored[0].read_bytes())

    def test_restore_refuses_non_empty_output_before_looking_for_archives(self) -> None:
        for stray in ("2026-08-01.json", "notes.txt"):
            with self.subTest(stray=stray), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "corpora"
                output.mkdir()
                (output / stray).write_text("{}", encoding="utf-8")
                with (
                    patch.object(restore_private_corpora, "_artifact_candidates") as candidates,
                    self.assertRaisesRegex(ValueError, "must be absent or empty"),
                ):
                    restore_private_corpora.restore(
                        "owner/repository", output, "github token", "archive secret",
                        date(2026, 8, 20),
                    )
                candidates.assert_not_called()

    def test_signed_redirect_download_does_not_receive_github_token(self) -> None:
        opener = RedirectingOpener()
        unsigned_requests: list[urllib.request.Request] = []

        def unsigned_open(request, timeout):
            unsigned_requests.append(request)
            return FakeResponse(b"artifact")

        with (
            patch("urllib.request.build_opener", return_value=opener),
            patch("urllib.request.urlopen", side_effect=unsigned_open),
        ):
            payload = restore_private_corpora._download_without_forwarding_token(
                "https://api.github.test/archive", "github token"
            )

        self.assertEqual(payload, b"artifact")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer github token")
        self.assertEqual(len(unsigned_requests), 1)
        self.assertIsNone(unsigned_requests[0].get_header("Authorization"))
        self.assertEqual(
            unsigned_requests[0].full_url,
            "https://objects.example.test/signed-artifact",
        )

    def test_artifact_listing_ignores_newer_branch_and_fork_decoys(self) -> None:
        payload = json.dumps(
            {
                "artifacts": [
                    {
                        "id": 10,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-20T00:00:00Z",
                        "archive_download_url": "https://api.github.test/old.zip",
                        "workflow_run": self._workflow_run(),
                    },
                    {
                        "id": 20,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-21T00:00:00Z",
                        "archive_download_url": "https://api.github.test/new.zip",
                        "workflow_run": self._workflow_run(),
                    },
                    {
                        "id": 30,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-23T00:00:00Z",
                        "archive_download_url": "https://api.github.test/branch.zip",
                        "workflow_run": self._workflow_run(branch="pull/123/merge"),
                    },
                    {
                        "id": 40,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-24T00:00:00Z",
                        "archive_download_url": "https://api.github.test/fork.zip",
                        "workflow_run": self._workflow_run(head_repository_id=8),
                    },
                    {
                        "id": 50,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-25T00:00:00Z",
                        "archive_download_url": "https://api.github.test/malformed.zip",
                        "workflow_run": None,
                    },
                ]
            }
        ).encode("utf-8")
        opener = StaticOpener(payload)

        with patch("urllib.request.build_opener", return_value=opener):
            candidates = restore_private_corpora._artifact_candidates(
                "owner/repository",
                restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                "github token",
            )

        self.assertEqual(
            candidates,
            (
                restore_private_corpora.ArtifactCandidate(
                    20, "https://api.github.test/new.zip"
                ),
                restore_private_corpora.ArtifactCandidate(
                    10, "https://api.github.test/old.zip"
                ),
            ),
        )
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer github token")
        self.assertEqual(
            opener.request.get_header("X-github-api-version"),
            restore_private_corpora.API_VERSION,
        )

    def test_restore_skips_bad_mac_and_uses_next_main_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = self._artifact_zip(root, "archive secret")
            bad_artifact = self._artifact_with_bad_mac(artifact)
            candidates = (
                restore_private_corpora.ArtifactCandidate(
                    20, "https://api.github.test/new.zip"
                ),
                restore_private_corpora.ArtifactCandidate(
                    10, "https://api.github.test/old.zip"
                ),
            )
            stderr = io.StringIO()
            with (
                patch.object(
                    restore_private_corpora,
                    "_artifact_candidates",
                    return_value=candidates,
                ),
                patch.object(
                    restore_private_corpora,
                    "_download_without_forwarding_token",
                    side_effect=[bad_artifact, artifact],
                ) as download,
                redirect_stderr(stderr),
            ):
                restored = restore_private_corpora.restore(
                    "owner/repository", root / "restored", "github token", "archive secret",
                    date(2026, 8, 20),
                )

        self.assertEqual([path.name for path in restored], ["2026-08-20.json"])
        self.assertEqual(download.call_count, 2)
        self.assertIn("warning: artifact 20 failed authentication", stderr.getvalue())

    def test_restore_fails_hard_when_all_candidates_have_bad_macs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = self._artifact_zip(root, "archive secret")
            bad_artifact = self._artifact_with_bad_mac(artifact)
            candidates = (
                restore_private_corpora.ArtifactCandidate(
                    20, "https://api.github.test/new.zip"
                ),
                restore_private_corpora.ArtifactCandidate(
                    10, "https://api.github.test/old.zip"
                ),
            )
            stderr = io.StringIO()
            with (
                patch.object(
                    restore_private_corpora,
                    "_artifact_candidates",
                    return_value=candidates,
                ),
                patch.object(
                    restore_private_corpora,
                    "_download_without_forwarding_token",
                    side_effect=[bad_artifact, bad_artifact],
                ) as download,
                redirect_stderr(stderr),
                self.assertRaisesRegex(
                    ValueError, "authentication failed for all 2 eligible artifacts tried"
                ),
            ):
                restore_private_corpora.restore(
                    "owner/repository", root / "restored", "github token", "archive secret",
                    date(2026, 8, 20),
                )

        self.assertEqual(download.call_count, 2)
        warnings = stderr.getvalue()
        self.assertEqual(warnings.count("warning:"), 2)
        self.assertIn("warning: artifact 20 failed authentication", warnings)
        self.assertIn("warning: artifact 10 failed authentication", warnings)

    def test_non_https_artifact_redirect_is_refused_before_opening(self) -> None:
        for location in (
            "file:///tmp/private-artifact.zip",
            "ftp://objects.example.test/private-artifact.zip",
            "//objects.example.test/private-artifact.zip",
            "https://user:password@objects.example.test/private-artifact.zip",
        ):
            with self.subTest(location=location):
                opener = RedirectingOpener(location)
                with (
                    patch("urllib.request.build_opener", return_value=opener),
                    patch("urllib.request.urlopen") as unsigned_open,
                    self.assertRaisesRegex(ValueError, "absolute HTTPS URL"),
                ):
                    restore_private_corpora._download_without_forwarding_token(
                        "https://api.github.test/archive", "github token"
                    )
                unsigned_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
