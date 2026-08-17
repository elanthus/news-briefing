"""Shared parsing and durable checkpoint I/O for evaluator judge workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from evaluator.adapters import Adapter, Generation

Parsed = TypeVar("Parsed")
ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest for input identity records."""
    return hashlib.sha256(content).hexdigest()


def portable_path(path: Path) -> str:
    """Represent repository paths without exposing a machine-specific checkout."""
    resolved = path.resolve()
    try:
        return f"./{resolved.relative_to(ROOT).as_posix()}"
    except ValueError:
        return path.name


def parse_json_response(text: str, response_name: str) -> Any:
    """Decode a JSON response while tolerating a fence or short prose preface."""
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError(f"{response_name} has an unterminated code fence")
        value = "\n".join(lines[1:-1])
    elif not value.startswith("{"):
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start:end + 1]
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        preview = text.strip().replace("\n", " ")[:160]
        raise ValueError(
            f"{response_name} is not valid JSON: {exc}; response starts {preview!r}"
        ) from exc


def write_text_atomic(path: Path, content: str) -> None:
    """Replace a UTF-8 text file only after its complete temporary file is written."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_json_atomic(path: Path, payload: Any) -> None:
    """Serialize JSON through an atomic text replacement."""
    write_text_atomic(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def checkpointed_generate(
    adapter: Adapter,
    prompt: str,
    checkpoint: Path,
    parse: Callable[[str], Parsed],
) -> tuple[Generation, Parsed, bool]:
    """Return a valid cached generation or durably save and parse one new call."""
    if checkpoint.exists():
        try:
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"checkpoint {checkpoint.name} is not a JSON object")
            generation = Generation(**payload)
            return generation, parse(generation.text), True
        except (OSError, TypeError, ValueError):
            # Retry corrupt checkpoints and malformed saved model responses.
            pass
    generation = adapter.generate(prompt)
    write_json_atomic(checkpoint, generation.record())
    return generation, parse(generation.text), False
