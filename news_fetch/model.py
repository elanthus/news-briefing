"""Corpus item, source-health, and fetch-result types shared by every source."""

from __future__ import annotations

from typing import NamedTuple, Never, NotRequired, TypedDict


class Item(TypedDict):
    """A corpus item. Field names are fixed by corpus_schema."""

    title: str
    url: str
    published: str
    source: str
    summary: NotRequired[str]
    discussion: NotRequired[str]
    points: NotRequired[int]
    comments: NotRequired[int]
    query: NotRequired[str]


class SourceStatus(TypedDict):
    """Observable outcome for one configured source request."""

    source_type: str
    source_id: str
    category: str
    status: str
    requested: bool
    http_success: bool
    parsed_entries: int
    dated_entries: int
    retained_entries: int
    retained_bytes: int
    estimated_tokens: int
    duration_ms: int
    error_type: NotRequired[str]
    message: NotRequired[str]


class FetchResult(NamedTuple):
    """Fetched items plus parsed, dated, and undated entry counts.

    Undated entries never reach category processing, so this separate count is
    required to distinguish an incompatible date format from an empty source.
    """

    items: list[Item]
    undated: int
    parsed_entries: int
    dated_entries: int
    # In-window entries dropped as removed/deleted or below the score floor;
    # separate from the counts above so an empty result stays diagnosable.
    filtered_entries: int


class TimedFetchResult(NamedTuple):
    result: FetchResult | None
    error_type: str | None
    message: str | None
    duration_ms: int
    http_success: bool


class SourceDataError(ValueError):
    """A response arrived successfully but its payload could not be consumed."""

    def __init__(self, cause: Exception):
        self.error_type = cause.__class__.__name__
        super().__init__(str(cause) or self.error_type)


def _raise_data_error(exc: Exception) -> Never:
    raise SourceDataError(exc) from exc
