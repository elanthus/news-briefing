"""Atomic checkpoints and append-only local traces for briefing runs."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runner.failures import final_failure
from agent_runner.hashing import sha256_bytes as sha256_bytes
from agent_runner.hashing import sha256_file as sha256_file


def write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    except BaseException:
        # A failed write must not leave the temporary behind: the target
        # directory may be swept into artifacts or globbed by later stages.
        # Cleanup errors are suppressed so the original failure re-raises.
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunStore:
    """Own one run directory, manifest, and trace stream."""

    MANIFEST_SCHEMA_VERSION = 1

    def __init__(self, root: Path, manifest: dict[str, Any]):
        self.root = root
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        identity: dict[str, Any],
        provider: dict[str, Any],
        code: dict[str, Any],
    ) -> RunStore:
        root.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schema_version": cls.MANIFEST_SCHEMA_VERSION,
            "run_id": root.name,
            "trace_id": secrets.token_hex(16),
            "status": "running",
            "phase": "initialized",
            "started_at": utc_now(),
            "checkpointed_at": None,
            "completed_at": None,
            "identity": identity,
            "provider": provider,
            "code": code,
            "attempts": [],
            "outcome": None,
            "final": None,
        }
        empty_trace_hash = sha256_bytes(b"")
        manifest["artifacts"] = {"trace.jsonl": empty_trace_hash}
        manifest["trace_commit"] = {"bytes": 0, "sha256": empty_trace_hash}
        write_bytes_atomic(root / "trace.jsonl", b"")
        store = cls(root, manifest)
        store.checkpoint("initialized")
        store.trace("run_initialized")
        return store

    def read_verified_text(self, name: str) -> str:
        """Read exactly the recorded, hash-matched bytes for one text artifact."""
        artifacts = self.manifest.get("artifacts")
        expected = artifacts.get(name) if isinstance(artifacts, dict) else None
        if not isinstance(expected, str):
            raise ValueError(f"cannot load unverified checkpoint artifact: {name} is not recorded")
        path = self.root / name
        if not path.is_file():
            raise ValueError(f"cannot load unverified checkpoint artifact: missing {name}")
        payload = path.read_bytes()
        if sha256_bytes(payload) != expected:
            raise ValueError(f"cannot load unverified checkpoint artifact: hash differs for {name}")
        return payload.decode("utf-8")

    def trace(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": utc_now(),
            "trace_id": self.manifest["trace_id"],
            "event": event,
            **fields,
        }
        with (self.root / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        trace_path = self.record_artifact("trace.jsonl")
        self.manifest["trace_commit"] = {
            "bytes": trace_path.stat().st_size,
            "sha256": self.manifest["artifacts"]["trace.jsonl"],
        }
        write_json_atomic(self.root / "manifest.json", self.manifest)

    def write_bytes(self, name: str, value: bytes) -> Path:
        path = self.root / name
        write_bytes_atomic(path, value)
        self.manifest.setdefault("artifacts", {})[name] = sha256_bytes(value)
        return path

    def write_text(self, name: str, text: str) -> Path:
        return self.write_bytes(name, text.encode("utf-8"))

    def write_json(self, name: str, value: Any) -> Path:
        path = self.root / name
        write_json_atomic(path, value)
        self.manifest.setdefault("artifacts", {})[name] = sha256_file(path)
        return path

    def record_artifact(self, name: str) -> Path:
        path = self.root / name
        if not path.is_file():
            raise ValueError(f"cannot record missing artifact {name}")
        self.manifest.setdefault("artifacts", {})[name] = sha256_file(path)
        return path

    def checkpoint(self, phase: str) -> None:
        self.manifest["phase"] = phase
        self.manifest["checkpointed_at"] = utc_now()
        write_json_atomic(self.root / "manifest.json", self.manifest)

    def fail(self, error: dict[str, Any], *, outcome: dict[str, str] | None = None) -> None:
        self.manifest["status"] = "failed"
        self.manifest["error"] = error
        if outcome is not None:
            self.manifest["outcome"] = outcome
        self.manifest["completed_at"] = utc_now()
        self.trace(
            "run_failed",
            disposition=outcome.get("disposition") if outcome else None,
            error_type=error.get("type"),
            message=error.get("message"),
        )
        self.checkpoint("failed")

    def finalize(self, final: dict[str, Any]) -> None:
        failure = final_failure(final, self.manifest)
        final["failure"] = failure.payload() if failure is not None else None
        self.manifest["status"] = "complete"
        self.manifest["final"] = final
        self.manifest["outcome"] = final.get("outcome")
        self.manifest["completed_at"] = utc_now()
        self.trace("run_finalized", status=final.get("status"))
        self.checkpoint("finalized")
