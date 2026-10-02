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


def verified_generation_artifact(path: Path, row: dict[str, Any], digest_key: str) -> bytes:
    """Require generation-owned evidence identity, including for a new assessment."""
    expected = row.get(digest_key)
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(
            f"unverifiable legacy generation: missing {digest_key}; "
            "preserve the run and use a generation with recorded artifact digests"
        )
    content = path.read_bytes()
    if sha256_bytes(content) != expected:
        raise ValueError(f"{path.name} differs from the frozen generation artifact")
    return content


def portable_path(path: Path) -> str:
    """Represent repository paths without exposing a machine-specific checkout."""
    resolved = path.resolve()
    try:
        return f"./{resolved.relative_to(ROOT).as_posix()}"
    except ValueError:
        return path.name


def parse_json_response(text: str, response_name: str) -> Any:
    """Decode a JSON response while tolerating a fence or prose preface."""
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


def reviewer_identity(adapter: Adapter) -> dict[str, Any]:
    """Describe the requested reviewer and every adapter-exposed call control."""
    return {
        "provider": adapter.provider,
        "model": adapter.model,
        "generation_controls": adapter.generation_controls(),
        "timeout_seconds": adapter.timeout,
    }


def judgment_identity(adapter: Adapter, prompt: str) -> dict[str, Any]:
    """Bind a judgment to its effective evidence, output, rubric and reviewer."""
    return {
        "checkpoint_schema_version": 2,
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
        "reviewer": reviewer_identity(adapter),
    }


def checked_generation(payload: dict[str, Any], identity: dict[str, Any]) -> Generation:
    """Reject legacy or incompatible provenance before considering cached labels."""
    if any(payload.get(key) != value for key, value in identity.items()):
        raise ValueError(
            "checkpoint has incompatible or legacy judgment provenance; "
            "use a new assessment directory (the existing record is preserved)"
        )
    return Generation(**{key: value for key, value in payload.items() if key not in identity})


def checkpointed_generate(
    adapter: Adapter,
    prompt: str,
    checkpoint: Path,
    parse: Callable[[str], Parsed],
    *,
    bind_prompt: bool = True,
) -> tuple[Generation, Parsed, bool]:
    """Resume compatible judgments; preserve and reject incompatible or legacy ones."""
    if not bind_prompt:
        raise ValueError("judge checkpoints must bind the effective prompt")
    identity = judgment_identity(adapter, prompt)
    if checkpoint.exists():
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"checkpoint {checkpoint.name} is not a JSON object")
        generation = checked_generation(payload, identity)
        try:
            parsed = parse(generation.text)
        except ValueError:
            # Preserve the response and its provenance before retrying this same
            # assessment. Incompatible identities above are never retried.
            index = 1
            archived = checkpoint.with_name(f"{checkpoint.stem}-invalid-{index:03d}.json")
            while archived.exists():
                index += 1
                archived = checkpoint.with_name(f"{checkpoint.stem}-invalid-{index:03d}.json")
            checkpoint.rename(archived)
        else:
            return generation, parsed, True
    generation = adapter.generate(prompt)
    write_json_atomic(checkpoint, generation.record() | identity)
    return generation, parse(generation.text), False
