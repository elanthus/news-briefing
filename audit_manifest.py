"""Build a public, text-free audit manifest for one private news corpus."""

from __future__ import annotations

import hashlib
from typing import Any

import corpus_schema
from agent_runner.checkpoint import sha256_bytes
from agent_runner.output import project_corpus

AUDIT_MANIFEST_SCHEMA_VERSION = 1


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_audit_manifest(corpus: dict[str, Any], raw_corpus: bytes) -> dict[str, Any]:
    """Describe corpus membership without publishing titles or excerpts.

    Item identifiers and destinations come from the same projection used for
    generation. Text hashes cover the exact UTF-8 bytes of the source-owned
    ``title`` and optional ``summary`` fields in the private corpus.
    """
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        raise ValueError("corpus violates its schema: " + "; ".join(problems))

    corpus_version = corpus_schema.corpus_version(corpus)
    if corpus_version is None:
        raise ValueError("corpus has no readable schema version")
    projected = project_corpus(corpus)
    citations_by_item: dict[str, list[dict[str, str]]] = {}
    seen_urls_by_item: dict[str, set[tuple[str, str]]] = {}
    for citation in projected.citations.values():
        canonical = corpus_schema.canonicalize_url(citation.url)
        identity = (citation.kind, canonical)
        seen = seen_urls_by_item.setdefault(citation.item_ref, set())
        if identity in seen:
            continue
        seen.add(identity)
        citations_by_item.setdefault(citation.item_ref, []).append(
            {"kind": citation.kind, "url": canonical}
        )

    manifest_items: list[dict[str, Any]] = []
    projected_categories = projected.document["categories"]
    for category, raw_items in corpus["categories"].items():
        projected_items = projected_categories.get(category)
        if not isinstance(projected_items, list) or len(projected_items) != len(raw_items):
            raise AssertionError("model projection changed corpus item membership")
        for raw_item, projected_item in zip(raw_items, projected_items, strict=True):
            item_id = projected_item.get("item_ref")
            if not isinstance(item_id, str):
                raise AssertionError("model projection omitted an item reference")
            excerpt = raw_item.get("summary")
            manifest_items.append(
                {
                    "item_id": item_id,
                    "category": category,
                    "canonical_urls": citations_by_item.get(item_id, []),
                    "source": raw_item["source"],
                    "published": raw_item["published"],
                    "title_sha256": _text_sha256(raw_item["title"]),
                    "excerpt_sha256": (
                        _text_sha256(excerpt) if isinstance(excerpt, str) else None
                    ),
                }
            )

    return {
        "schema_version": AUDIT_MANIFEST_SCHEMA_VERSION,
        "corpus_schema_version": corpus_version,
        "report_date": corpus.get("report_date"),
        "generated_at": corpus["generated_at"],
        "cutoff": corpus["cutoff"],
        "text_encoding": "utf-8",
        "hash_algorithm": "sha256",
        "corpus_sha256": sha256_bytes(raw_corpus),
        "items": manifest_items,
    }
