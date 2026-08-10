#!/usr/bin/env python3
"""Trusted editorial configuration for briefing generation and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

import corpus_schema

SCHEMA_VERSION = 1
DEFAULT_CONFIG_PATH = Path(__file__).with_name("briefing-config.json")

_TOP_LEVEL_FIELDS = {"schema_version", "sections"}
_SECTION_FIELDS = {
    "name",
    "group",
    "target_stories",
    "corpus_categories",
    "guidance",
    "excluded_stories",
}
_RESERVED_SECTION_NAMES = {"excluded topics", "corpus health"}


class BriefingSection(NamedTuple):
    name: str
    group: str | None
    target_stories: int
    corpus_categories: tuple[str, ...]
    guidance: str
    excluded_stories: int


class BriefingConfig(NamedTuple):
    schema_version: int
    sections: tuple[BriefingSection, ...]


def _field_difference(value: dict[str, Any], expected: set[str], where: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{where} is missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{where} has unknown field(s): {', '.join(sorted(unknown))}")


def parse_config(raw: Any) -> BriefingConfig:
    """Validate parsed JSON and return its typed representation."""
    if not isinstance(raw, dict):
        raise ValueError("top level must be a JSON object")
    _field_difference(raw, _TOP_LEVEL_FIELDS, "top level")

    version = raw["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("schema_version must be an integer")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version is {version}, but this code supports {SCHEMA_VERSION}")

    section_values = raw["sections"]
    if not isinstance(section_values, list) or not section_values:
        raise ValueError("sections must be a non-empty list")

    sections: list[BriefingSection] = []
    names: set[str] = set()
    for index, value in enumerate(section_values):
        where = f"sections[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{where} must be an object")
        _field_difference(value, _SECTION_FIELDS, where)

        name = value["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}.name must be a non-empty string")
        name = name.strip()
        folded_name = name.casefold()
        if folded_name in _RESERVED_SECTION_NAMES:
            raise ValueError(f"{where}.name is reserved: {name!r}")
        if folded_name in names:
            raise ValueError(f"section names must be unique, including case: {name!r}")
        names.add(folded_name)

        group = value["group"]
        if group is not None and (not isinstance(group, str) or not group.strip()):
            raise ValueError(f"{where}.group must be null or a non-empty string")
        group = group.strip() if isinstance(group, str) else None

        target = value["target_stories"]
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            raise ValueError(f"{where}.target_stories must be a positive integer")

        categories = value["corpus_categories"]
        if (not isinstance(categories, list) or not categories
                or any(not corpus_schema.valid_category_name(category)
                       for category in categories)):
            raise ValueError(
                f"{where}.corpus_categories must be a non-empty list of category names")
        if len(categories) != len(set(categories)):
            raise ValueError(f"{where}.corpus_categories contains a duplicate")

        guidance = value["guidance"]
        if not isinstance(guidance, str) or not guidance.strip():
            raise ValueError(f"{where}.guidance must be a non-empty string")

        exclusions = value["excluded_stories"]
        if not isinstance(exclusions, int) or isinstance(exclusions, bool) or exclusions < 0:
            raise ValueError(f"{where}.excluded_stories must be a non-negative integer")

        sections.append(BriefingSection(
            name=name,
            group=group,
            target_stories=target,
            corpus_categories=tuple(categories),
            guidance=guidance.strip(),
            excluded_stories=exclusions,
        ))

    heading_names = names | _RESERVED_SECTION_NAMES
    for index, section in enumerate(sections):
        if section.group is not None and section.group.casefold() in heading_names:
            raise ValueError(
                f"sections[{index}].group collides with a section or reserved heading: "
                f"{section.group!r}")

    return BriefingConfig(schema_version=version, sections=tuple(sections))


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> BriefingConfig:
    """Read and validate a briefing configuration file."""
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    return parse_config(raw)


def validate_corpus_categories(config: BriefingConfig, categories: set[str]) -> list[str]:
    """Report configured category references absent from a particular corpus."""
    problems: list[str] = []
    for section in config.sections:
        missing = set(section.corpus_categories) - categories
        if missing:
            problems.append(
                f"section {section.name!r} references missing corpus categories: "
                f"{', '.join(sorted(missing))}")
    return problems
