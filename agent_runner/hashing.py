"""SHA-256 hex digests shared by the runner, publication, and evaluator code."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_BYTES = 1 << 20


def sha256_bytes(data: bytes) -> str:
    """The lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 digest of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
