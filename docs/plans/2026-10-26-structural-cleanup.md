# Structural cleanup plan

## Implementation status

[PR 308](https://github.com/elanthus/news-briefing/pull/308) introduced a shared story-position type and hashing helpers.
[PR 309](https://github.com/elanthus/news-briefing/pull/309) replaced the evaluator’s variant dispatch chain with registered transforms.
The README now illustrates a repair with a published integrity report.

The remaining proposals cover the runner output module, the site builder, and typed parser results.
Each refactor must preserve validation, public interfaces, and rendered output. The module boundaries below reflect the October 26 proposal.

## Phase 3: split `agent_runner/output.py`

The file holds seven concerns in sequence. Split along its existing section
order so the diff is mostly moves. Keep `agent_runner/output.py` as the
import facade re-exporting every public name, so `runner.py`,
`evaluator/parity.py`, `eval_briefing.py`, and tests do not change in this PR.
Remove the facade in a follow-up once imports are updated.

| New module | Blocks moved from `output.py` | Contents |
| --- | --- | --- |
| `agent_runner/citations.py` | `OutputFinding`..`ModelCorpus`, `_complete_item_citations` | `Citation`, `CitationDestination`, `ModelCorpus`, `OutputFinding`, `_complete_item_citations` |
| `agent_runner/redaction.py` | `DESTINATION_REDACTION`..`redact_preview_value` | `redact_destinations`, `redecode_redacted_text`, `redact_opaque_references`, `redact_preview_value` and their regexes |
| `agent_runner/projection.py` | `project_corpus`..`project_selected_evidence`, `attach_frozen_selection`..`detach_prose` | `project_corpus`, `project_selected_evidence`, `attach_frozen_selection`, `detach_prose` |
| `agent_runner/schemas.py` | `_text_property`..`build_prose_schema` | `build_selection_schema`, `build_prose_schema` and the schema builders |
| `agent_runner/structural_repair.py` | `REPAIRABLE_CHECKS`..`promote_excluded_to_underfilled` | `REPAIRABLE_CHECKS`, `repair_structural_output`, `repair_entries`, `empty_section_findings`, `promote_excluded_to_underfilled` |
| `agent_runner/validation.py` | `_object_fields`..`validate_prose_output`, `validate_output` | `_validate_structure`, `validate_selection`, `validate_prose_output`, `validate_output` |
| `agent_runner/render.py` | `_date_label`..`complete_briefing` | `render_briefing`, `render_candidate_preview`, `render_validation_status`, `complete_briefing` |

Rules for this phase:

- `redaction.py` and `projection.py` are the citation-projection security boundary named in `AGENTS.md` and ADR 0001. Move them, do not edit them. Update the file paths in `SECURITY.md`, `docs/adr/0001-citation-projection.md`, and `AGENTS.md` ("`agent_runner/output.py` implements citation projection").
- `repair_structural_output` (218 lines) and `repair_entries` (109) stay long in this PR. Splitting them is phase 4 work and should not be mixed with moves.
- Import direction: `citations` and `redaction` depend on nothing; `projection` depends on both; `schemas` on `citations`; `validation` on `citations` and `projection`; `structural_repair` on `validation`; `render` on `citations`. If a cycle appears, the move is wrong, not the import.

Verification: full gate, plus `git diff --stat` should show `output.py` shrinking to under 60 lines of re-exports and almost no changed lines inside moved blocks (`git diff --color-moved=dimmed-zebra` makes this reviewable).

## Phase 4: split `build_site.py` and shorten the longest functions

`build_site.py` is the only file allowed third-party imports, so the split must
keep `markdown-it` behind one module. Same facade approach: `build_site.py`
keeps `main()` and `build_site()` and imports the rest.

Ranges are named by symbol, not line, because the file keeps moving; each row is a
contiguous block in the October 26 source.

| New module | From | Through | Contents |
| --- | --- | --- | --- |
| `site_build/history.py` | `SIDECAR_FIELDS` | `_metrics_row` | `BriefingEntry`, sidecar and history loading, `_history_payload`, `HISTORY_SCHEMA_VERSION` |
| `site_build/style.py` | `STYLE` | `_document` | the stylesheet string and the page shell |
| `site_build/prose.py` | `_COVERAGE_LABELS` | `_merge_source_notes` | coverage labels, corpus-health prose, `_humanize_corpus_health`, `_omit_report_only_warnings` |
| `site_build/markdown.py` | `_autolink_citation` | `_decorate_briefing` | `_render_markdown`, autolink, post-render decoration regexes. Only this module imports `markdown_it`. |
| `site_build/pages.py` | `_history_nav` | `_excluded_topics` | index, briefing page, nav, status chip, generation summary |
| `site_build/report.py` | `_render_report` | `_render_semantic_audit` | integrity report, decision sentence, action ledger |
| `site_build/publish.py` | `_remove_path` | `_publish_audit_manifests` | manifest publication |

Naming: `site` shadows the stdlib `site` module that `python3 -S` disables. Use `site_build/` instead. This matters because core tests run with `-S` and a top-level package named `site` would be importable there and confusing.

Long-function targets after the moves, in priority order because they sit on the reader-facing path:

1. `_render_semantic_audit` (162 lines): extract `_audit_topic_row(topic, checks, integrity)` and `_audit_summary(audit)`.
2. `build_site` (104 lines): extract `_collect_entries(root)` and `_write_pages(entries, out)`.
3. `publication_schema.parse_integrity` (226 lines) and `parse_semantic_audit` (157): split per top-level key into `_parse_integrity_sources`, `_parse_integrity_results`, `_parse_audit_topics`, `_parse_audit_checks`. These are validators, so each extracted function must keep the same `ValueError` messages.
4. `agent_runner/semantic_repairs.load_public_audit` (195) and `agent_runner/integrity.semantic_integrity` (172): these are the hardest and the most security-relevant. Defer them to their own PR after phase 4 lands, with a short design note first.

Leave alone: `evaluator/__main__.py:main` (141 lines of argparse wiring) and `run_briefing.py:main` (153). Long CLI entry points are not a maintenance problem and splitting them adds indirection for no reader.

Verification: full gate plus the site suite in an isolated environment:

```bash
python3 -m unittest tests.site_test_build
```

Also diff the generated HTML for one fixture day before and after. `tests/site_test_build.py` has fixtures that produce complete pages; render them from `main` into the scratchpad, render again from the branch, and `diff -r`. Zero differences is the acceptance bar for phases 3 and 4.

## Phase 5: typed parse results instead of `dict[str, Any]`

This is the long tail and should be done one schema at a time, not as one PR.
The rule is: a parser returns a frozen dataclass, and downstream code reads
attributes. The `isinstance` checks stay inside the parser, where they belong.
Count the checks outside parsers as the metric: around 750 total checks at the October review, and
the target is that none remain outside `corpus_schema.py`,
`publication_schema.py`, `news_fetch/`, and the provider response parsers.

Order, by how many consumers each unlocks:

1. `StoryPosition` (done in phase 1).
2. `SemanticAudit`, `AuditTopic`, `AuditCheck` out of `publication_schema.parse_semantic_audit`. Consumers: `build_site` report code, `integrity.py`, `semantic_repairs.py`. This removes most of the `t["position"]`, `c["positions"][0]`, `row.get("evidence_index")` indexing.
3. `IntegrityReport` out of `parse_integrity`. Consumers: `build_site`, `jev_review`, `audit_metrics`.
4. `CorpusItem`, `Corpus` out of `corpus_schema.py`. This is the largest, since `project_corpus`, `render_briefing`, and `eval_briefing.py` all walk the corpus. The evaluator's fixture builders in `evaluator/cases.py` build corpora as dicts, so they need a `Corpus.from_payload` entry point and the variant transforms from phase 2 keep mutating the dict before parsing.
5. `SelectionOutput`, `ProseOutput` from `validate_selection` and `validate_prose_output`.

Constraints that hold for every step:

- The dataclass is constructed only after validation passes. No `__post_init__` validation that could be bypassed with a direct constructor call in tests. If a test needs an instance, it goes through the parser with a payload.
- `schema_version` rules from `AGENTS.md` still apply. Typing the result does not change the on-disk shape, so no version bump, and the writer still validates the payload before writing.
- Use `slots=True, frozen=True`. Pin to Python 3.11 per `pyproject.toml`, which has `slots`.
- Do not introduce a `TypedDict` layer as an intermediate. It gives the type checker something to say but the guarantees still do not travel at runtime.

Verification per step: full gate, and the `isinstance` count outside the parser modules goes down and never up.
