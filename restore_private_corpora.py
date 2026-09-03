#!/usr/bin/env python3
"""Restore the newest encrypted corpus archive from GitHub Actions artifacts."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

from private_archive import decrypt_archive, restore_corpora_from_tar

API_VERSION = "2026-03-10"
USER_AGENT = "news-briefing-private-corpus-restore/1.0"
DEFAULT_ARTIFACT_NAME = "briefing-corpus-archive"
ENCRYPTED_MEMBER = "corpus-archive.tar.gz.enc"
MAX_API_BYTES = 2_000_000
MAX_ARTIFACT_BYTES = 64_000_000


class NoArchiveError(RuntimeError):
    """The repository has no eligible corpus archive candidate to restore."""


class ArtifactCandidate(NamedTuple):
    artifact_id: int
    download_url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _is_main_repository_run(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("head_branch") != "main":
        return False
    head_repository_id = value.get("head_repository_id")
    repository_id = value.get("repository_id")
    return (
        isinstance(head_repository_id, int)
        and not isinstance(head_repository_id, bool)
        and isinstance(repository_id, int)
        and not isinstance(repository_id, bool)
        and head_repository_id == repository_id
    )


def _read_bounded(response: Any, limit: int) -> bytes:
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"response exceeded {limit} bytes")
    return payload


def _api_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
    )


def _artifact_candidates(
    repository: str, artifact_name: str, token: str
) -> tuple[ArtifactCandidate, ...]:
    encoded_name = urllib.parse.quote(artifact_name, safe="")
    url = (
        f"https://api.github.com/repos/{repository}/actions/artifacts"
        f"?name={encoded_name}&per_page=100"
    )
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(_api_request(url, token), timeout=30) as response:
        payload = json.loads(_read_bounded(response, MAX_API_BYTES))
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("GitHub artifact response has no artifacts array")
    candidates = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("name") == artifact_name
        and artifact.get("expired") is False
        and isinstance(artifact.get("id"), int)
        and not isinstance(artifact.get("id"), bool)
        and isinstance(artifact.get("archive_download_url"), str)
        and isinstance(artifact.get("created_at"), str)
        and _is_main_repository_run(artifact.get("workflow_run"))
    ]
    if not candidates:
        raise NoArchiveError(
            f"no eligible unexpired {artifact_name!r} artifact exists"
        )
    newest_first = sorted(
        candidates,
        key=lambda artifact: (
            artifact["created_at"],
            artifact["id"],
        ),
        reverse=True,
    )
    return tuple(
        ArtifactCandidate(artifact["id"], artifact["archive_download_url"])
        for artifact in newest_first
    )


def _download_without_forwarding_token(url: str, token: str) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    request = _api_request(url, token)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        try:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            location = exc.headers.get("Location")
            if not location:
                raise ValueError("GitHub artifact redirect has no Location header") from exc
        finally:
            exc.close()
    else:
        with response:
            return _read_bounded(response, MAX_ARTIFACT_BYTES)

    redirect = urllib.parse.urlsplit(location)
    if (
        redirect.scheme.lower() != "https"
        or not redirect.netloc
        or redirect.username is not None
        or redirect.password is not None
    ):
        raise ValueError("GitHub artifact redirect must be an absolute HTTPS URL")
    unsigned = urllib.request.Request(location, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(unsigned, timeout=60) as response:
        return _read_bounded(response, MAX_ARTIFACT_BYTES)


def _encrypted_member(artifact_zip: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(artifact_zip)) as archive:
            matches = [info for info in archive.infolist() if info.filename == ENCRYPTED_MEMBER]
            if len(matches) != 1:
                raise ValueError(
                    f"artifact must contain exactly one {ENCRYPTED_MEMBER!r} member"
                )
            info = matches[0]
            if info.is_dir() or info.file_size > MAX_ARTIFACT_BYTES:
                raise ValueError("encrypted corpus archive member is invalid or too large")
            payload = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise ValueError("downloaded GitHub artifact is not a ZIP archive") from exc
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("encrypted corpus archive member is too large")
    return payload


def restore(repository: str, output_dir: Path, token: str, passphrase: str) -> tuple[Path, ...]:
    if not token or "\n" in token or "\r" in token:
        raise ValueError("GitHub token must be a non-empty single-line value")
    if not passphrase or "\n" in passphrase or "\r" in passphrase:
        raise ValueError("archive passphrase must be a non-empty single-line value")
    if repository.count("/") != 1 or any(not part for part in repository.split("/")):
        raise ValueError("repository must use owner/name format")

    candidates = _artifact_candidates(repository, DEFAULT_ARTIFACT_NAME, token)
    with tempfile.TemporaryDirectory(prefix="news-briefing-restore-") as directory:
        root = Path(directory)
        encrypted_path = root / ENCRYPTED_MEMBER
        plaintext_path = root / "corpus-archive.tar.gz"
        for candidate in candidates:
            encrypted = _encrypted_member(
                _download_without_forwarding_token(candidate.download_url, token)
            )
            encrypted_path.write_bytes(encrypted)
            try:
                decrypt_archive(encrypted_path, plaintext_path, passphrase)
            except ValueError as exc:
                if str(exc) != "encrypted archive authentication failed":
                    raise
                print(
                    f"warning: artifact {candidate.artifact_id} failed authentication; "
                    "trying the next older artifact",
                    file=sys.stderr,
                )
                continue
            return restore_corpora_from_tar(plaintext_path, output_dir)
    raise ValueError(
        f"authentication failed for all {len(candidates)} eligible artifacts tried"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--output-dir", type=Path, default=Path("corpora"))
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--passphrase-env", default="CORPUS_ARCHIVE_PASSPHRASE")
    args = parser.parse_args()
    try:
        if not args.repository:
            raise ValueError("--repository or GITHUB_REPOSITORY is required")
        restored = restore(
            args.repository,
            args.output_dir,
            os.environ.get(args.token_env, ""),
            os.environ.get(args.passphrase_env, ""),
        )
    except NoArchiveError as exc:
        print(str(exc))
        return 4
    except (OSError, RuntimeError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(f"Restored {len(restored)} private corpus file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
