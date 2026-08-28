#!/usr/bin/env python3
"""Encrypt operational artifacts and safely restore private corpus archives."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import date, timedelta
from pathlib import Path

import corpus_schema
from agent_runner.checkpoint import write_bytes_atomic

CORPUS_MEMBER = re.compile(r"^corpora/(\d{4}-\d{2}-\d{2})\.json$")
MAX_CORPUS_BYTES = 50_000_000
MAX_RESTORED_BYTES = 100_000_000
ARCHIVE_MAGIC = b"NBPA1\x00"
# OpenSSL 1.1/LibreSSL accept an eight-byte explicit salt, while OpenSSL 3 also
# accepts it. Keep the versioned envelope portable across local and CI runners.
ARCHIVE_SALT_BYTES = 8
ARCHIVE_MAC_BYTES = 32
PBKDF2_ITERATIONS = 200_000


def _passphrase(environment_name: str) -> str:
    value = os.environ.get(environment_name, "")
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{environment_name} must be a non-empty single-line secret")
    return value


def _run_openssl(arguments: list[str], passphrase: str) -> None:
    result = subprocess.run(
        ["openssl", "enc", *arguments],
        input=(passphrase + "\n").encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"openssl failed: {message}")


def _mac_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        b"news-briefing-archive-hmac-v1\x00" + salt,
        PBKDF2_ITERATIONS,
        dklen=ARCHIVE_MAC_BYTES,
    )


def create_encrypted_archive(
    paths: list[Path], output: Path, passphrase: str
) -> None:
    """Create an authenticated AES-256 encrypted gzip-compressed tar archive."""
    if not paths:
        raise ValueError("at least one archive input path is required")
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise ValueError("archive input does not exist: " + ", ".join(missing))
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise ValueError("archive input basenames must be unique")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="news-briefing-archive-") as directory:
        plaintext = Path(directory) / "archive.tar.gz"
        ciphertext = Path(directory) / "archive.ciphertext"
        with tarfile.open(plaintext, mode="w:gz") as archive:
            for path in paths:
                archive.add(path, arcname=path.name, recursive=True)
        salt = os.urandom(ARCHIVE_SALT_BYTES)
        _run_openssl(
            [
                "-aes-256-cbc",
                "-e",
                "-pbkdf2",
                "-iter",
                str(PBKDF2_ITERATIONS),
                "-md",
                "sha256",
                "-S",
                salt.hex(),
                "-pass",
                "stdin",
                "-in",
                str(plaintext),
                "-out",
                str(ciphertext),
            ],
            passphrase,
        )
        authenticated = ARCHIVE_MAGIC + salt + ciphertext.read_bytes()
        mac = hmac.digest(_mac_key(passphrase, salt), authenticated, "sha256")
        write_bytes_atomic(output, authenticated + mac)


def decrypt_archive(encrypted: Path, plaintext: Path, passphrase: str) -> None:
    """Authenticate then decrypt without putting the passphrase in arguments."""
    envelope = encrypted.read_bytes()
    header_bytes = len(ARCHIVE_MAGIC) + ARCHIVE_SALT_BYTES
    if len(envelope) <= header_bytes + ARCHIVE_MAC_BYTES:
        raise ValueError("encrypted archive is truncated")
    authenticated = envelope[:-ARCHIVE_MAC_BYTES]
    supplied_mac = envelope[-ARCHIVE_MAC_BYTES:]
    if not authenticated.startswith(ARCHIVE_MAGIC):
        raise ValueError("encrypted archive has an unsupported format")
    salt = authenticated[len(ARCHIVE_MAGIC):header_bytes]
    expected_mac = hmac.digest(_mac_key(passphrase, salt), authenticated, "sha256")
    if not hmac.compare_digest(supplied_mac, expected_mac):
        raise ValueError("encrypted archive authentication failed")

    plaintext.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="news-briefing-decrypt-", dir=plaintext.parent
    ) as directory:
        ciphertext = Path(directory) / "archive.ciphertext"
        candidate = Path(directory) / "archive.tar.gz"
        ciphertext.write_bytes(authenticated[header_bytes:])
        _run_openssl(
            [
                "-aes-256-cbc",
                "-d",
                "-pbkdf2",
                "-iter",
                str(PBKDF2_ITERATIONS),
                "-md",
                "sha256",
                "-S",
                salt.hex(),
                "-pass",
                "stdin",
                "-in",
                str(ciphertext),
                "-out",
                str(candidate),
            ],
            passphrase,
        )
        write_bytes_atomic(plaintext, candidate.read_bytes())


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if member.size > MAX_CORPUS_BYTES:
        raise ValueError(f"private corpus member is too large: {member.name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"cannot read private corpus member: {member.name}")
    payload = stream.read(MAX_CORPUS_BYTES + 1)
    if len(payload) > MAX_CORPUS_BYTES:
        raise ValueError(f"private corpus member is too large: {member.name}")
    return payload


def _validate_corpus_payload(payload: bytes, day: str, label: str) -> None:
    try:
        corpus = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"private corpus is invalid JSON: {label}") from exc
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        raise ValueError(
            f"private corpus violates its schema: {label}: " + "; ".join(problems)
        )
    if corpus.get("report_date") != day:
        raise ValueError(
            f"private corpus report_date does not match its filename: {label}"
        )


def restore_corpora_from_tar(archive_path: Path, output_dir: Path) -> tuple[Path, ...]:
    """Restore only validated ``corpora/YYYY-MM-DD.json`` regular files."""
    restored: list[tuple[str, bytes]] = []
    total_bytes = 0
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir() and member.name.rstrip("/") == "corpora":
                continue
            match = CORPUS_MEMBER.fullmatch(member.name)
            if match is None or not member.isfile():
                raise ValueError(f"unexpected private corpus archive member: {member.name}")
            day = match.group(1)
            if day in seen:
                raise ValueError(f"duplicate private corpus archive member: {member.name}")
            payload = _read_member(archive, member)
            total_bytes += len(payload)
            if total_bytes > MAX_RESTORED_BYTES:
                raise ValueError("private corpus archive exceeds the restored-size limit")
            _validate_corpus_payload(payload, day, member.name)
            seen.add(day)
            restored.append((day, payload))

    if not restored:
        raise ValueError("private corpus archive contains no corpus files")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for day, payload in restored:
        path = output_dir / f"{day}.json"
        write_bytes_atomic(path, payload)
        paths.append(path)
    return tuple(paths)


def restore_corpora_from_bytes(payload: bytes, output_dir: Path) -> tuple[Path, ...]:
    """Test-friendly wrapper around the path-based safe restore function."""
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as stream:
        stream.write(payload)
        stream.flush()
        return restore_corpora_from_tar(Path(stream.name), output_dir)


def prune_corpora(directory: Path, newest: date, keep_days: int = 14) -> tuple[Path, ...]:
    """Validate retained corpora and delete files outside a bounded window."""
    if keep_days < 1:
        raise ValueError("keep_days must be positive")
    if not directory.is_dir():
        raise ValueError(f"corpus directory does not exist: {directory}")
    oldest = newest - timedelta(days=keep_days - 1)
    removed = []
    retained_bytes = 0
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ValueError(f"unexpected entry in corpus directory: {path.name}")
        try:
            parsed = date.fromisoformat(path.stem)
        except ValueError as exc:
            raise ValueError(f"corpus filename is not a canonical date: {path.name}") from exc
        if path.stem != parsed.isoformat():
            raise ValueError(f"corpus filename is not a canonical date: {path.name}")
        if parsed < oldest or parsed > newest:
            path.unlink()
            removed.append(path)
            continue
        with path.open("rb") as stream:
            payload = stream.read(MAX_CORPUS_BYTES + 1)
        if len(payload) > MAX_CORPUS_BYTES:
            raise ValueError(f"private corpus is too large: {path.name}")
        retained_bytes += len(payload)
        if retained_bytes > MAX_RESTORED_BYTES:
            raise ValueError("retained private corpora exceed the restored-size limit")
        _validate_corpus_payload(payload, path.stem, path.name)
    return tuple(removed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create an encrypted tar archive")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument(
        "--passphrase-env", default="CORPUS_ARCHIVE_PASSPHRASE"
    )
    create.add_argument("paths", nargs="+", type=Path)
    decrypt = subparsers.add_parser("decrypt", help="decrypt an archive")
    decrypt.add_argument("encrypted", type=Path)
    decrypt.add_argument("plaintext", type=Path)
    decrypt.add_argument(
        "--passphrase-env", default="CORPUS_ARCHIVE_PASSPHRASE"
    )
    prune = subparsers.add_parser(
        "prune-corpora", help="prune dated corpus files outside a retention window"
    )
    prune.add_argument("directory", type=Path)
    prune.add_argument("--newest", type=date.fromisoformat, required=True)
    prune.add_argument("--keep-days", type=int, default=14)
    args = parser.parse_args()
    try:
        if args.command == "create":
            passphrase = _passphrase(args.passphrase_env)
            create_encrypted_archive(args.paths, args.output, passphrase)
        elif args.command == "decrypt":
            passphrase = _passphrase(args.passphrase_env)
            decrypt_archive(args.encrypted, args.plaintext, passphrase)
        else:
            removed = prune_corpora(args.directory, args.newest, args.keep_days)
            print(f"Pruned {len(removed)} private corpus file(s)")
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
