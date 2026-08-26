"""Atomic checkpoints and append-only local traces for briefing runs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


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

    @classmethod
    def resume(cls, root: Path, *, identity: dict[str, Any]) -> RunStore:
        manifest_path = root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot resume corrupt checkpoint {manifest_path}: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("cannot resume corrupt checkpoint: manifest must be an object")
        if manifest.get("schema_version") != cls.MANIFEST_SCHEMA_VERSION:
            raise ValueError("cannot resume checkpoint with unsupported schema version")
        if manifest.get("status") != "running" or manifest.get("completed_at") is not None:
            raise ValueError("resume requires an interrupted running checkpoint")
        if manifest.get("identity") != identity:
            raise ValueError("cannot resume incompatible run: invocation identity differs")
        attempts = manifest.get("attempts")
        if not isinstance(attempts, list) or any(not isinstance(row, dict) for row in attempts):
            raise ValueError("cannot resume corrupt checkpoint: attempts must be an array of objects")
        phase = manifest.get("phase")
        if isinstance(phase, str) and phase.endswith("_call_started"):
            raise ValueError(
                "cannot safely resume an interrupted in-flight model call; completion and billing are ambiguous"
            )
        store = cls(root, manifest)
        store._recover_trace()
        store._validate_artifacts()
        store.trace("run_resumed", phase=phase)
        return store

    def _recover_trace(self) -> None:
        """Discard a suffix appended after the last committed trace hash."""
        commit = self.manifest.get("trace_commit")
        if commit is None:
            # Older manifests record only the full-file artifact hash and remain
            # subject to strict validation in _validate_artifacts().
            return
        if not isinstance(commit, dict):
            raise ValueError("cannot resume corrupt checkpoint: invalid trace commit")
        committed_bytes = commit.get("bytes")
        committed_hash = commit.get("sha256")
        if (
            isinstance(committed_bytes, bool)
            or not isinstance(committed_bytes, int)
            or committed_bytes < 0
            or not isinstance(committed_hash, str)
        ):
            raise ValueError("cannot resume corrupt checkpoint: invalid trace commit")
        artifacts = self.manifest.get("artifacts")
        if not isinstance(artifacts, dict) or artifacts.get("trace.jsonl") != committed_hash:
            raise ValueError("cannot resume corrupt checkpoint: trace commit hash differs")
        path = self.root / "trace.jsonl"
        if not path.is_file():
            raise ValueError("cannot resume corrupt checkpoint: missing artifact trace.jsonl")
        payload = path.read_bytes()
        if len(payload) < committed_bytes or sha256_bytes(payload[:committed_bytes]) != committed_hash:
            raise ValueError("cannot resume corrupt checkpoint: artifact hash differs for trace.jsonl")
        if len(payload) > committed_bytes:
            with path.open("r+b") as stream:
                stream.truncate(committed_bytes)
                stream.flush()
                os.fsync(stream.fileno())

    def _validate_artifacts(self) -> None:
        artifacts = self.manifest.get("artifacts", {})
        if not isinstance(artifacts, dict):
            raise ValueError("cannot resume corrupt checkpoint: artifacts must be an object")
        for name, expected in artifacts.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise ValueError("cannot resume corrupt checkpoint: invalid artifact hash record")
            path = self.root / name
            if not path.is_file():
                raise ValueError(f"cannot resume corrupt checkpoint: missing artifact {name}")
            if sha256_file(path) != expected:
                raise ValueError(f"cannot resume corrupt checkpoint: artifact hash differs for {name}")

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
        self.manifest["status"] = "complete"
        self.manifest["final"] = final
        self.manifest["outcome"] = final.get("outcome")
        self.manifest["completed_at"] = utc_now()
        self.trace("run_finalized", status=final.get("status"))
        self.checkpoint("finalized")
