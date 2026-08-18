"""Atomic checkpoints and append-only local traces for briefing runs."""

from __future__ import annotations

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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


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
            "final": None,
        }
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
        store._validate_artifacts()
        store.trace("run_resumed", phase=phase)
        return store

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

    def write_text(self, name: str, text: str) -> Path:
        path = self.root / name
        write_text_atomic(path, text)
        self.manifest.setdefault("artifacts", {})[name] = sha256_file(path)
        return path

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

    def fail(self, error: dict[str, Any]) -> None:
        self.manifest["status"] = "failed"
        self.manifest["error"] = error
        self.manifest["completed_at"] = utc_now()
        self.trace("run_failed", error_type=error.get("type"), message=error.get("message"))
        self.checkpoint("failed")

    def finalize(self, final: dict[str, Any]) -> None:
        self.manifest["status"] = "complete"
        self.manifest["final"] = final
        self.manifest["completed_at"] = utc_now()
        self.trace("run_finalized", status=final.get("status"))
        self.checkpoint("finalized")
