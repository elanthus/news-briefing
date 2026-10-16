#!/usr/bin/env python3
"""Restore the newest encrypted corpus archive from GitHub Actions artifacts."""

from __future__ import annotations

import argparse
import email.message
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from typing import IO, Any, NamedTuple

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
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: email.message.Message,
        newurl: str,
    ) -> urllib.request.Request | None:
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


def restore(
    repository: str, output_dir: Path, token: str, passphrase: str, newest: date
) -> tuple[Path, ...]:
    if not token or "\n" in token or "\r" in token:
        raise ValueError("GitHub token must be a non-empty single-line value")
    if not passphrase or "\n" in passphrase or "\r" in passphrase:
        raise ValueError("archive passphrase must be a non-empty single-line value")
    if repository.count("/") != 1 or any(not part for part in repository.split("/")):
        raise ValueError("repository must use owner/name format")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError(f"corpus output directory must be absent or empty: {output_dir}")

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
            return restore_corpora_from_tar(plaintext_path, output_dir, newest)
    raise ValueError(
        f"authentication failed for all {len(candidates)} eligible artifacts tried"
    )


def restore_local(artifact_dir: Path, output_dir: Path, passphrase: str, newest: date) -> tuple[Path, ...]:
    """Restore one date window from digest-checked, authenticated preserved wrappers.

    The manifest identifies wrappers, not trustworthy plaintext. Stage every
    eligible archive before writing output; conflicting copies of a date fail closed.
    No GitHub credential is read or used in this mode.
    """
    if not passphrase or "\n" in passphrase or "\r" in passphrase:
        raise ValueError("archive passphrase must be a non-empty single-line value")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError(f"corpus output directory must be absent or empty: {output_dir}")
    manifest_path = artifact_dir / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("local artifact manifest must not be a symlink")
    with manifest_path.open("rb") as stream:
        manifest = json.loads(_read_bounded(stream, MAX_API_BYTES))
    entries = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError("local artifact manifest must contain a bounded artifacts array")
    candidates = [row for row in entries if isinstance(row, dict) and row.get("name") == DEFAULT_ARTIFACT_NAME]
    if not candidates:
        raise NoArchiveError("no preserved corpus archive wrappers in manifest")
    seen: set[int] = set()
    restored: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="news-briefing-local-restore-") as directory:
        root = Path(directory)
        for row in candidates:
            artifact_id, digest, size = row.get("id"), row.get("sha256"), row.get("bytes")
            if (type(artifact_id) is not int or artifact_id <= 0 or artifact_id in seen
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or type(size) is not int or not 0 < size <= MAX_ARTIFACT_BYTES):
                raise ValueError("invalid local corpus archive manifest entry")
            seen.add(artifact_id)
            wrapper = artifact_dir / f"{artifact_id}.zip"
            if wrapper.is_symlink() or not wrapper.is_file():
                raise ValueError("local corpus archive wrapper must be a regular file")
            with wrapper.open("rb") as stream:
                payload = _read_bounded(stream, MAX_ARTIFACT_BYTES)
            if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("local corpus archive wrapper digest or size mismatch")
            encrypted = root / ENCRYPTED_MEMBER
            plaintext = root / "corpus-archive.tar.gz"
            encrypted.write_bytes(_encrypted_member(payload))
            decrypt_archive(encrypted, plaintext, passphrase)
            for path in restore_corpora_from_tar(plaintext, root / str(artifact_id), newest):
                data = path.read_bytes()
                if path.name in restored and restored[path.name] != data:
                    raise ValueError("conflicting preserved corpora for the same date")
                restored[path.name] = data
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, data in sorted(restored.items()):
            (output_dir / name).write_bytes(data)
    return tuple(output_dir / name for name in sorted(restored))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, help="preserved wrappers and manifest.json; no network")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--output-dir", type=Path, default=Path("corpora"))
    parser.add_argument("--newest", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        if args.artifact_dir is not None:
            restored = restore_local(args.artifact_dir, args.output_dir,
                                     os.environ.get("CORPUS_ARCHIVE_PASSPHRASE", ""), args.newest)
        else:
            if not args.repository:
                raise ValueError("--repository or GITHUB_REPOSITORY is required")
            restored = restore(
            args.repository,
            args.output_dir,
            os.environ.get("GITHUB_TOKEN", ""),
            os.environ.get("CORPUS_ARCHIVE_PASSPHRASE", ""),
            args.newest,
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
