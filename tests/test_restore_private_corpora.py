from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
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
    def __init__(self):
        self.request: urllib.request.Request | None = None

    def open(self, request: urllib.request.Request, timeout: int):
        self.request = request
        headers = Message()
        headers["Location"] = "https://objects.example.test/signed-artifact"
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

    def test_restore_downloads_decrypts_and_validates_corpus_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = self._artifact_zip(root, "archive secret")
            with (
                patch.object(
                    restore_private_corpora,
                    "_latest_download_url",
                    return_value="https://api.github.test/artifact.zip",
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

    def test_artifact_listing_uses_current_version_and_selects_newest(self) -> None:
        payload = json.dumps(
            {
                "artifacts": [
                    {
                        "id": 10,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-20T00:00:00Z",
                        "archive_download_url": "https://api.github.test/old.zip",
                    },
                    {
                        "id": 20,
                        "name": restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                        "expired": False,
                        "created_at": "2026-08-21T00:00:00Z",
                        "archive_download_url": "https://api.github.test/new.zip",
                    },
                ]
            }
        ).encode("utf-8")
        opener = StaticOpener(payload)

        with patch("urllib.request.build_opener", return_value=opener):
            url = restore_private_corpora._latest_download_url(
                "owner/repository",
                restore_private_corpora.DEFAULT_ARTIFACT_NAME,
                "github token",
            )

        self.assertEqual(url, "https://api.github.test/new.zip")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer github token")
        self.assertEqual(
            opener.request.get_header("X-github-api-version"),
            restore_private_corpora.API_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
