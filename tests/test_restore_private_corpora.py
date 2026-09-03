from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stderr
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
    def _artifact_zip(self, root: Path, passphrase: str) -> bytes:
        corpora = root / "corpora"
        corpora.mkdir()
        corpus, _config, _projected, _output = fixture_contract()
        corpus["report_date"] = "2026-08-20"
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
                    "owner/repository", root / "restored", "github token", "archive secret"
                )

            self.assertEqual([path.name for path in restored], ["2026-08-20.json"])
            self.assertIn(b'"report_date": "2026-08-20"', restored[0].read_bytes())

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
                    "owner/repository", root / "restored", "github token", "archive secret"
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
                    "owner/repository", root / "restored", "github token", "archive secret"
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
