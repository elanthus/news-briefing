"""Text-free operational metrics ledger and page for the static archive.

``history.json`` keeps seven report dates, so a page computed from it alone
covers one week. This module keeps a separate ledger, ``metrics.json``, with
one compact row per report date. The daily workflow downloads the deployed
ledger and merges each build into it under the same publication-rank rule as
history; rows are never truncated.

Rows hold only dates, enums, counts, model identifiers and costs. They never
carry feed text, headlines, summaries, prose, findings messages, URLs or
hashes. A field the source entry does not record is ``null`` and renders as
"unknown"; it is never filled with zero.

Generation spend is the fallback chain's reported cost across every attempt,
published as the integrity record's ``generation`` cost phase. A row whose
entry recorded no spend stays unknown rather than zero.

Only ``LEDGER_SCHEMA_VERSION`` is readable. A ledger at any other version is
refused and the build fails before anything is written. Rows are never upgraded
in place; a refused deployed ledger is rebuilt from history by an explicit
``--allow-empty-metrics`` build.

The page is an operational summary of the daily integrity records. It is not
an independent assessment, a grounding rate, or a quality measurement.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from publication_schema import INTEGRITY_DECISIONS, Provenance

LEDGER_SCHEMA_VERSION = 2
LEDGER_ROW_LIMIT = 100_000
DISPOSITIONS = frozenset({"blocked", "degraded", "no_result", "ready", "rejected", "review_required"})
REVIEW_STATUSES = frozenset({"complete", "partial", "failed"})
COST_PHASES = ("initial_review", "repair_generation", "followup_review")
ROLLING_WINDOW_DAYS = 28
# SVG user units. Narrow enough that 15px chart text stays readable when the
# chart scales to a phone's content width.
CHART_WIDTH = 440
# Model and provider identifiers as the fallback chain configures them, e.g.
# "deepseek/deepseek-v4-flash-0731" or "anthropic/claude-opus-4[1m]". Anything
# else is kept out of the ledger, so free text cannot ride into it through an
# identifier field.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,199}")
ROW_FIELDS = frozenset({
    "date", "disposition", "provider", "model", "attempt_index", "attempt_count", "fallback_used",
    "failed_attempts", "review_status", "followup_status", "review_complete", "repair_decision",
    "generation_cost_usd", "generation_unknown_cost_calls",
    "review_stage_cost_usd", "phase_costs_usd", "unknown_cost_calls", "flags_raised",
    "flags_confirmed", "findings_count", "advisory_findings",
})


def _identifier(value: str) -> str | None:
    """Return a plain identifier, or None (rendered "unknown") for anything else.

    publication_schema accepts any non-empty provenance string, so an unusual
    identifier must degrade this one field rather than abort the site build.
    """
    return value if _IDENTIFIER.fullmatch(value) else None


def _review_status(record: Mapping[str, Any] | None, audit: Mapping[str, Any] | None, *,
                   followup: bool) -> str | None:
    coverage_key, audit_key = ("followup_review", "post_status") if followup else ("initial_review", "status")
    if record is not None and record.get(coverage_key) is not None:
        return str(record[coverage_key]["status"])
    if audit is not None and audit.get(audit_key) is not None:
        return str(audit[audit_key])
    return None


def _phase_costs(record: Mapping[str, Any] | None) -> dict[str, float | None] | None:
    if record is None:
        return None
    recorded = {cost["phase"]: cost["reported_cost_usd"] for cost in record["costs"]}
    return {phase: recorded.get(phase) for phase in COST_PHASES}


def _generation_cost(record: Mapping[str, Any] | None) -> tuple[float | None, int | None]:
    """(known USD, unknown-cost calls) of the date's model chain, or unknown."""
    if record is None:
        return None, None
    for cost in record["costs"]:
        if cost["phase"] == "generation":
            usd = cost["reported_cost_usd"]
            return (None if usd is None else float(usd)), cost["unknown_cost_calls"]
    return None, None


def metrics_row(
    *,
    day: date,
    disposition: str,
    provenance: Provenance | None,
    semantic_audit: Mapping[str, Any] | None,
    integrity: Mapping[str, Any] | None,
    generation_failures: int,
    findings_count: int,
    advisory_findings: int,
) -> dict[str, Any]:
    """Project one validated history entry onto a text-free ledger row."""
    if provenance is not None:
        fallback_used: bool | None = provenance.attempt_index > 1
        failed_attempts: int | None = provenance.attempt_index - 1
    elif generation_failures:
        # A blocked day: every model in the chain failed.
        fallback_used, failed_attempts = generation_failures > 1, generation_failures
    else:
        fallback_used = failed_attempts = None
    review_status = _review_status(integrity, semantic_audit, followup=False)
    checks = semantic_audit["checks"] if semantic_audit is not None else None
    generation_usd, generation_unknown = _generation_cost(integrity)
    return {
        "date": day.isoformat(),
        "disposition": disposition,
        "provider": _identifier(provenance.provider) if provenance is not None else None,
        "model": _identifier(provenance.model) if provenance is not None else None,
        "attempt_index": provenance.attempt_index if provenance is not None else None,
        "attempt_count": provenance.attempt_count if provenance is not None else None,
        "fallback_used": fallback_used,
        "failed_attempts": failed_attempts,
        "review_status": review_status,
        "followup_status": _review_status(integrity, semantic_audit, followup=True),
        # Same rule as the integrity report's "complete review" wording.
        "review_complete": None if review_status is None else review_status == "complete",
        "repair_decision": integrity["decision"] if integrity is not None else None,
        "generation_cost_usd": generation_usd,
        "generation_unknown_cost_calls": generation_unknown,
        "review_stage_cost_usd": (
            float(semantic_audit["reported_cost_usd"]) if semantic_audit is not None else None
        ),
        "phase_costs_usd": _phase_costs(integrity),
        "unknown_cost_calls": semantic_audit["unknown_cost_calls"] if semantic_audit is not None else None,
        "flags_raised": (
            sum(check["confirmation_label"] not in (None, "not_flagged") for check in checks)
            if checks is not None else None
        ),
        "flags_confirmed": (
            sum(check["confirmation_label"] == "confirmed" for check in checks)
            if checks is not None else None
        ),
        "findings_count": findings_count,
        "advisory_findings": advisory_findings,
    }


def _count(value: object, *, nullable: bool = True, limit: int = 100_000) -> None:
    if value is None and nullable:
        return
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError("invalid metrics count")


def _cost(value: object) -> None:
    if value is None:
        return
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not 0 <= value <= 100):
        raise ValueError("invalid metrics cost")


def _enum(value: object, choices: Iterable[str]) -> None:
    if value is not None and value not in set(choices):
        raise ValueError("invalid metrics enum")


def parse_row(raw: object) -> dict[str, Any]:
    """Validate one ledger row; refuse any field or value outside the text-free shape."""
    if not isinstance(raw, dict) or set(raw) != ROW_FIELDS:
        raise ValueError(f"metrics rows must contain exactly {sorted(ROW_FIELDS)}")
    try:
        parsed = date.fromisoformat(raw["date"]) if isinstance(raw["date"], str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != raw["date"]:
        raise ValueError("metrics row date must be an ISO date")
    if raw["disposition"] not in DISPOSITIONS:
        raise ValueError("invalid metrics disposition")
    for field in ("provider", "model"):
        if raw[field] is not None and (not isinstance(raw[field], str) or not _IDENTIFIER.fullmatch(raw[field])):
            raise ValueError("invalid metrics identifier")
    for field in ("attempt_index", "attempt_count", "failed_attempts"):
        _count(raw[field], limit=64)
    if (raw["attempt_index"] is None) != (raw["attempt_count"] is None) or (
        raw["attempt_index"] is not None and not 1 <= raw["attempt_index"] <= raw["attempt_count"]
    ):
        raise ValueError("invalid metrics attempt position")
    for field in ("fallback_used", "review_complete"):
        if raw[field] is not None and type(raw[field]) is not bool:
            raise ValueError("invalid metrics flag")
    _enum(raw["review_status"], REVIEW_STATUSES)
    _enum(raw["followup_status"], REVIEW_STATUSES)
    if raw["review_complete"] is not (None if raw["review_status"] is None else raw["review_status"] == "complete"):
        raise ValueError("metrics review_complete disagrees with review_status")
    _enum(raw["repair_decision"], INTEGRITY_DECISIONS)
    _cost(raw["generation_cost_usd"])
    _count(raw["generation_unknown_cost_calls"], limit=5000)
    _cost(raw["review_stage_cost_usd"])
    phases = raw["phase_costs_usd"]
    if phases is not None:
        if not isinstance(phases, dict) or set(phases) != set(COST_PHASES):
            raise ValueError("invalid metrics phase costs")
        for value in phases.values():
            _cost(value)
    for field in ("unknown_cost_calls", "flags_raised", "flags_confirmed"):
        _count(raw[field], limit=5000)
    _count(raw["findings_count"], nullable=False)
    _count(raw["advisory_findings"], nullable=False)
    return raw


def parse_ledger(payload: object) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "rows"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != LEDGER_SCHEMA_VERSION
        or not isinstance(payload["rows"], list)
        or len(payload["rows"]) > LEDGER_ROW_LIMIT
    ):
        raise ValueError(f"metrics ledger must use schema_version {LEDGER_SCHEMA_VERSION} with a rows array")
    rows = [parse_row(row) for row in payload["rows"]]
    dates = [row["date"] for row in rows]
    if len(set(dates)) != len(dates):
        raise ValueError("metrics ledger contains a duplicate date")
    return rows


def merge_ledger(
    prior: Iterable[dict[str, Any]],
    current: Iterable[dict[str, Any]],
    *,
    rank: Mapping[str, int],
    replaced: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Merge rows by date with the history rank rule, never dropping a date.

    A current row replaces a prior one when its publication rank is at least
    the prior rank, so a blocked retry never clobbers a ready day. Dates in
    ``replaced`` were explicitly republished (``--replace-existing``) and take
    the current row, matching how history treats them.
    """
    by_date = {row["date"]: row for row in prior}
    for row in current:
        existing = by_date.get(row["date"])
        if (
            existing is None
            or row["date"] in replaced
            or rank.get(row["disposition"], 0) >= rank.get(existing["disposition"], 0)
        ):
            by_date[row["date"]] = row
    return [by_date[key] for key in sorted(by_date)]


def ledger_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "rows": rows}


# Rendering. Every string that reaches the page passes through _e(), including
# SVG labels and <title> tooltips; charts are inline SVG with no script.

def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "unknown"


def _usd(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.4f}"


def _table(caption: str, headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f'<th scope="col">{_e(h)}</th>' for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    table = f"<table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return f'<div class="table-scroll">{table}</div>' if len(headers) > 3 else table


def _hbar_chart(label: str, bars: list[tuple[str, int, str]], total: int) -> str:
    """Horizontal bars: (label, count, css class). Widths are shares of ``total``.

    Each label sits above its bar rather than in a side column, so a full chain
    identifier such as deepseek/deepseek-v4-flash-0731 stays legible when the
    chart scales down to phone width.
    """
    row_height, chart_width = 44, CHART_WIDTH
    height = row_height * len(bars) + 4
    parts = [
        f'<svg class="metrics-chart" viewBox="0 0 {chart_width} {height}" role="img" '
        f'aria-label="{_e(label)}" preserveAspectRatio="xMinYMin meet">',
        f"<title>{_e(label)}</title>",
    ]
    span = chart_width - 56
    for index, (name, count, css) in enumerate(bars):
        y = index * row_height
        width = span * count / total if total else 0
        parts.append(
            f'<text class="chart-label" x="0" y="{y + 17}">{_e(name)}</text>'
            f'<rect class="{_e(css)}" x="0" y="{y + 23}" width="{width:.1f}" height="16">'
            f"<title>{_e(name)}: {_e(count)} of {_e(total)}</title></rect>"
            f'<text class="chart-value" x="{width + 6:.1f}" y="{y + 37}">{_e(count)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _cost_unknown(row: Mapping[str, Any], stage: str) -> bool:
    """True when a day's spend for ``stage`` is unknown or only partly known."""
    if stage == "generation":
        cost, calls = row["generation_cost_usd"], row["generation_unknown_cost_calls"]
    else:
        cost, calls = row["review_stage_cost_usd"], row["unknown_cost_calls"]
    return cost is None or calls is None or calls > 0


def _cost_tip(row: Mapping[str, Any]) -> str:
    generation, review = row["generation_cost_usd"], row["review_stage_cost_usd"]
    if generation is None and review is None:
        return f"{row['date']}: cost unknown"
    notes = []
    for stage, cost in (("generation", generation), ("review stage", review)):
        note = f"{stage} {_usd(cost)}"
        if cost is not None:
            note += " known" + (", plus calls of unknown cost" if _cost_unknown(row, stage.split()[0]) else "")
        notes.append(note)
    return f"{row['date']}: " + "; ".join(notes)


def _cost_chart(rows: list[dict[str, Any]]) -> str:
    """Two bars per recorded day: generation on the left, the Jev review stage on the right."""
    width, height, top, bottom = CHART_WIDTH, 180, 16, 30
    known = [row[field] for row in rows for field in ("generation_cost_usd", "review_stage_cost_usd")
             if row[field] is not None]
    ceiling = max(known, default=0.0) or 1.0
    step = width / max(len(rows), 1)
    label = ("Known generation (left bar) and Jev review-stage (right bar) cost per recorded day; "
             "days with unknown or partly unknown cost are marked")
    parts = [
        f'<svg class="metrics-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}" '
        'preserveAspectRatio="xMinYMin meet">',
        f"<title>{_e(label)}</title>",
        f'<line class="chart-axis" x1="0" y1="{height - bottom}" x2="{width}" y2="{height - bottom}"/>',
    ]
    plot = height - top - bottom
    for index, row in enumerate(rows):
        x = index * step + step * 0.1
        slot = max(step * 0.8, 2.0)
        bar_width = max(slot / 2 - 1.0, 1.0)
        tip = _e(_cost_tip(row))
        for offset, css, cost in (
            (0.0, "chart-bar cost-generation", row["generation_cost_usd"]),
            (slot / 2, "chart-bar cost-review", row["review_stage_cost_usd"]),
        ):
            if cost is None:
                continue
            bar_height = plot * cost / ceiling
            parts.append(
                f'<rect class="{css}" x="{x + offset:.1f}" y="{height - bottom - bar_height:.1f}" '
                f'width="{bar_width:.1f}" height="{bar_height:.1f}"><title>{tip}</title></rect>'
            )
        if _cost_unknown(row, "generation") or _cost_unknown(row, "review"):
            parts.append(
                f'<rect class="chart-unknown" x="{x:.1f}" y="{height - bottom + 4}" width="{slot:.1f}" '
                f'height="6"><title>{tip}</title></rect>'
            )
    if rows:
        parts.append(
            f'<text class="chart-label" x="0" y="{height - 4}">{_e(rows[0]["date"])}</text>'
            f'<text class="chart-label" x="{width}" y="{height - 4}" text-anchor="end">'
            f'{_e(rows[-1]["date"])}</text>'
        )
    parts.append(
        f'<text class="chart-value" x="0" y="{top}">max {_e(_usd(max(known, default=None)))}</text></svg>'
    )
    return "".join(parts)


def rolling_ready_rate(rows: list[dict[str, Any]], window: int = ROLLING_WINDOW_DAYS) -> list[tuple[str, int, int]]:
    """For each recorded date: (date, ready days, recorded days) over the trailing ``window`` calendar days.

    Unrecorded dates are absent from both counts; they are not treated as failures.
    """
    days = [(date.fromisoformat(row["date"]), row["disposition"] == "ready") for row in rows]
    series = []
    for current, _ready in days:
        start = current - timedelta(days=window - 1)
        inside = [ready for day, ready in days if start <= day <= current]
        series.append((current.isoformat(), sum(inside), len(inside)))
    return series


def _rolling_chart(series: list[tuple[str, int, int]]) -> str:
    width, height, top, bottom, left, right = CHART_WIDTH, 180, 16, 30, 48, 8
    plot = height - top - bottom
    label = f"{ROLLING_WINDOW_DAYS}-day rolling share of recorded days that were ready"
    parts = [
        f'<svg class="metrics-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}" '
        'preserveAspectRatio="xMinYMin meet">',
        f"<title>{_e(label)}</title>",
        f'<line class="chart-axis" x1="{left}" y1="{height - bottom}" x2="{width}" y2="{height - bottom}"/>',
        f'<line class="chart-grid" x1="{left}" y1="{top}" x2="{width}" y2="{top}"/>',
        f'<text class="chart-value" x="0" y="{top + 4}">100%</text>'
        f'<text class="chart-value" x="0" y="{height - bottom + 4}">0%</text>',
    ]
    span = width - left - right
    step = span / max(len(series) - 1, 1)
    points = []
    for index, (day, ready, recorded) in enumerate(series):
        x = left + (index * step if len(series) > 1 else span / 2)
        y = height - bottom - plot * (ready / recorded if recorded else 0)
        points.append(f"{x:.1f},{y:.1f}")
        parts.append(
            f'<circle class="chart-point" cx="{x:.1f}" cy="{y:.1f}" r="3">'
            f"<title>{_e(day)}: {_e(ready)} of {_e(recorded)} recorded days ready</title></circle>"
        )
    if len(points) > 1:
        parts.insert(5, f'<polyline class="chart-line" points="{" ".join(points)}"/>')
    if series:
        parts.append(
            f'<text class="chart-label" x="{left}" y="{height - 4}">{_e(series[0][0])}</text>'
            f'<text class="chart-label" x="{width}" y="{height - 4}" text-anchor="end">{_e(series[-1][0])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _unknown(value: object) -> str:
    return "unknown" if value is None else str(value)


_DECISION_LABELS = {
    "original_retained": "Original retained",
    "repaired_applied": "Repair applied",
    "partial_applied": "Partial repair applied",
    "unpublished": "Not published",
}


def render_metrics_body(rows: list[dict[str, Any]]) -> str:
    """Return the metrics page body; the caller wraps it in the shared page shell."""
    intro = (
        "<h1>Operational metrics</h1>"
        '<p class="metrics-scope">These are operational summaries of the daily integrity records: counts '
        "of what the pipeline recorded for each report date. They are not an independent assessment, a "
        "grounding rate, or a quality measurement. Jev review and repair outcomes are automated judgments "
        "and can be wrong.</p>"
    )
    if not rows:
        return f'<article class="metrics">{intro}<p class="muted">No report dates are recorded yet.</p></article>'
    total = len(rows)
    dispositions = Counter(row["disposition"] for row in rows)
    ready = dispositions["ready"]
    first, last = rows[0]["date"], rows[-1]["date"]
    summary = (
        '<section><h2 id="ready-rate">Ready rate</h2>'
        f'<p class="metrics-stat"><strong>{_e(ready)} of {_e(total)}</strong> recorded report dates '
        f"were published ready ({_e(_percent(ready, total))}), {_e(first)} to {_e(last)}.</p>"
        + _hbar_chart(
            "Recorded report dates by disposition",
            [(name, count, f"chart-bar disposition-{name}") for name, count in sorted(
                dispositions.items(), key=lambda item: (-item[1], item[0]))],
            total,
        )
        + "</section>"
    )

    models: Counter[str] = Counter()
    fallback: Counter[str] = Counter()
    fallback_unknown: Counter[str] = Counter()
    for row in rows:
        if row["disposition"] in ("ready", "review_required"):
            name = _unknown(row["model"])
            models[name] += 1
            if row["fallback_used"] is None:
                fallback_unknown[name] += 1
            elif row["fallback_used"]:
                fallback[name] += 1
    model_rows = [
        [name, str(count), str(fallback[name]), str(fallback_unknown[name])]
        for name, count in sorted(models.items(), key=lambda item: (-item[1], item[0]))
    ]
    blocked_fallback = sum(1 for row in rows if row["disposition"] == "blocked" and row["fallback_used"])
    model_section = (
        '<section><h2 id="models">Selected model and fallback</h2>'
        "<p>Each published day names the model that produced it. A fallback means an earlier model in "
        "the chain did not reach a ready result first.</p>"
        + _hbar_chart(
            "Published days by selected model",
            [(name, count, "chart-bar") for name, count in sorted(models.items(), key=lambda i: (-i[1], i[0]))],
            sum(models.values()),
        )
        + _table("Published days by selected model", ["Model", "Days", "After fallback", "Fallback unknown"],
                 model_rows)
        + f'<p class="muted">Blocked days that exhausted more than one model: {_e(blocked_fallback)}.</p></section>'
    )

    reviewed = [row for row in rows if row["review_complete"] is True]
    decisions = Counter(_unknown(row["repair_decision"]) for row in reviewed)
    repair_section = (
        '<section><h2 id="repairs">Repair outcomes on days with complete review</h2>'
        f"<p>{_e(len(reviewed))} of {_e(total)} recorded days had a complete initial Jev review. "
        f"Days with no review record: {_e(sum(1 for r in rows if r['review_complete'] is None))}.</p>"
        + (_hbar_chart(
            "Repair decision on days with complete review",
            [(_DECISION_LABELS.get(name, name), count, "chart-bar") for name, count in sorted(
                decisions.items(), key=lambda item: (-item[1], item[0]))],
            len(reviewed),
        ) if reviewed else '<p class="muted">No day has a complete review yet.</p>')
        + "</section>"
    )

    unknown_days = sum(1 for row in rows if _cost_unknown(row, "generation") or _cost_unknown(row, "review"))
    generation_known = [row["generation_cost_usd"] for row in rows if row["generation_cost_usd"] is not None]
    review_known = [row["review_stage_cost_usd"] for row in rows if row["review_stage_cost_usd"] is not None]
    # With no known cost at all a total is unknown, not $0.
    generation_total = sum(generation_known) if generation_known else None
    review_total = sum(review_known) if review_known else None
    cost_rows = [
        [row["date"],
         _usd(row["generation_cost_usd"]),
         _unknown(row["generation_unknown_cost_calls"]),
         _usd(row["review_stage_cost_usd"]),
         *(_usd(row["phase_costs_usd"][phase]) if row["phase_costs_usd"] is not None else "unknown"
           for phase in COST_PHASES),
         _unknown(row["unknown_cost_calls"])]
        for row in reversed(rows)
    ]
    cost_section = (
        '<section><h2 id="costs">Cost per day</h2>'
        f"<p>Known generation spend across recorded days: {_e(_usd(generation_total))}; days with unknown "
        f"generation cost: {_e(sum(1 for row in rows if row['generation_cost_usd'] is None))}. "
        f"Known Jev review, repair, and follow-up spend across recorded days: {_e(_usd(review_total))}. "
        f"Days with unknown or partly unknown cost, marked below the axis: {_e(unknown_days)}.</p>"
        "<p>Generation spend is the fallback chain's reported cost for the date across every attempt, "
        "including attempts that did not publish. Dates recorded before that spend entered the public "
        "record show unknown, as do calls whose billing never arrived.</p>"
        + _cost_chart(rows)
        + "<details><summary>Cost by day</summary>"
        + _table("Cost by day",
                 ["Date", "Generation", "Generation unknown-cost calls", "Review-stage total",
                  "Initial review", "Repair", "Follow-up", "Review unknown-cost calls"],
                 cost_rows)
        + "</details></section>"
    )

    series = rolling_ready_rate(rows)
    latest = series[-1]
    rolling_section = (
        f'<section><h2 id="rolling">{ROLLING_WINDOW_DAYS}-day rolling ready rate</h2>'
        f"<p>Each point counts recorded days in the {ROLLING_WINDOW_DAYS} calendar days ending on that date. "
        f"Dates with no record are left out of both counts. Latest: {_e(latest[1])} of {_e(latest[2])} "
        f"({_e(_percent(latest[1], latest[2]))}).</p>"
        + _rolling_chart(series)
        + "</section>"
    )

    flags_known = [row for row in rows if row["flags_raised"] is not None]
    flags_section = (
        '<section><h2 id="flags">Flags raised and confirmed</h2>'
        f"<p>Across {_e(len(flags_known))} days with a review record, Jev raised "
        f"{_e(sum(r['flags_raised'] for r in flags_known))} flags and isolated rechecks confirmed "
        f"{_e(sum(r['flags_confirmed'] for r in flags_known))}. A flag is an automated judgment, not a "
        "verified error.</p>"
        + "</section>"
    )
    sources = (
        '<p class="muted">Computed from <a href="metrics.json">metrics.json</a>, one text-free row per '
        "report date, without model calls or network access.</p>"
    )
    return (
        f'<article class="metrics">{intro}{summary}{model_section}{repair_section}{cost_section}'
        f"{rolling_section}{flags_section}{sources}</article>"
    )
