# Structural cleanup plan

Date: 2026-10-26. Branch base: `main` after PR 306.

This plan answers a four-part audit: god-files and long functions, six ways of
keying a story position plus duplicated private helpers, `dict[str, Any]`
everywhere with hand-written `isinstance` checks, and a stale README screenshot.
Every phase is behaviour-preserving. The repository gate is the acceptance test
for each PR, and no phase weakens validation, bounds, or typed failures.

## What the audit got right, with corrections

Measured on this checkout, excluding the locked worktree at
`.claude/worktrees/site-portfolio-polish` (that worktree doubles every grep count,
so any number taken from a raw repo-wide grep is roughly 2x reality).

| Claim | Measured | Note |
| --- | --- | --- |
| `build_site.py` is 2,505 lines | 2,505 | Correct. |
| `output.py` is 1,735 lines | 1,775 | Grew since the audit. |
| 34 functions over 100 lines | 45 total, 34 outside tests | Audit counted non-test code. |
| `apply_variant` has 47 branches | 45 `elif` plus the `if`, 319 lines | Close enough. |
| `_sha256` defined five times | 5 | Two take `bytes`, three take `Path`. |
| Private cross-module imports | 7 sites | Three are tests, which is acceptable. |
| ~650 `isinstance` checks | ~750 | Concentrated in `output.py`, `corpus_schema.py`, `publication_schema.py`, `build_site.py`. |
| Story position keyed six ways | 6 | Listed below. |
| Stale auditor screenshot | File last touched 2026-10-14 | Text comes from a test fixture, not a bug. |

One correction: the audit names `jev_review.py` under the evaluator. The file is
`agent_runner/jev_review.py`. The evaluator package does not key positions.

The six position encodings today:

1. `agent_runner/integrity.py:19` `position_key()` returns `json.dumps(position, sort_keys=True)`, and callers round-trip with `json.loads(key)` at lines 180, 218, 231, 236.
2. `build_site.py:2068` `_position_key()` is a verbatim copy of the above, and `_story_anchor()` hashes it.
3. `agent_runner/semantic_repairs.py:46` `_action_path()` renders `topics.<section>[<index>]` and `_STORY_ANCHOR` parses it back out of a Markdown comment.
4. `build_site.py:58` `STORY_ANCHOR` and `build_site.py:1740` `_STORY_PATH` parse that same path with two different regexes.
5. `agent_runner/jev_review.py:185,198` builds JSON-string keys inline instead of calling `position_key`.
6. `publication_schema.py:451` `_integrity_position()` validates into a `(bucket, section, index)` tuple and `_integrity_check_key()` sorts those tuples. `parse_semantic_audit` separately calls a local `position()` helper for the same purpose.

## Ordering and why

Phase 1 is the typed foundation. Doing it first means the file splits in phases
3 and 4 move functions that already take a `StoryPosition` rather than moving
code and then retyping it inside the new modules. Phase 2 is small and
independent. Phase 5 is independent and can land any time.

Each phase is one PR. Within a phase, each numbered step should be one commit so
`git bisect` stays useful across a pure refactor.

## Phase 1: one `StoryPosition` type and shared helpers

Goal: a story position is a frozen dataclass constructed once at the boundary
and passed by value. JSON strings stop being the in-memory key.

1. Add `agent_runner/positions.py` (stdlib only):

   ```python
   @dataclass(frozen=True, slots=True, order=True)
   class StoryPosition:
       bucket: Literal["sections", "excluded_topics"]
       section: str
       index: int

       @classmethod
       def from_payload(cls, value: object) -> "StoryPosition": ...   # validates like _integrity_position
       def to_payload(self) -> dict[str, object]: ...                  # {"bucket", "section", "index"}
       def path(self) -> str: ...                                      # "topics.<section>[<index>]"
       @classmethod
       def from_path(cls, text: str) -> "StoryPosition": ...
       def anchor_id(self) -> str: ...                                 # "story-" + sha256(path)[:12]
   ```

   `from_payload` carries the existing bounds from `publication_schema._integrity_position` (section length 100, index 0..63, bucket enum) so validation gets stricter nowhere and looser nowhere.

   Decision to settle before coding: the current anchor hashes the sorted-JSON string. Switching `anchor_id` to hash `path()` changes every `id="story-…"` in published HTML. Either keep hashing `json.dumps(to_payload(), sort_keys=True)` for continuity, or accept the anchor change and note it in the PR. Recommendation: keep the JSON hash for now. Anchor stability is cheap and this PR should not touch rendered output.

2. Replace `agent_runner/integrity.py:position_key` with `StoryPosition.from_payload`. Remove every `json.loads(key)` round-trip (lines 180, 218, 231, 236) by keeping the position object in the dict key. Because the dataclass is hashable and ordered, every `{position_key(t["position"]): t}` map and every `sorted(...)` works unchanged.

3. Move `agent_runner/semantic_repairs.py:_action_path` and `_STORY_ANCHOR` to `StoryPosition.path()` and `from_path()`. Delete the local `_check_key` JSON key and use a tuple `(check, tuple(sorted(positions)), evidence_index)` matching `publication_schema._integrity_check_key`.

4. Point `agent_runner/jev_review.py:185,198` at the shared helpers. The URL map at line 198 should key on `StoryPosition`, not a JSON string.

5. In `publication_schema.py`, have `_integrity_position()` return `StoryPosition` and delete the local `position()` helper inside `parse_semantic_audit`. Keep the error messages byte-identical: `tests/test_publication_schema*` assert on them.

6. In `build_site.py`, delete `_position_key`, `_story_anchor`, `_STORY_PATH`, and `_story_path_label`, and parse `STORY_ANCHOR` matches with `from_path`. Twenty-three call sites change mechanically.

7. Shared `_sha256`. Add `agent_runner/hashing.py` with `sha256_bytes(data: bytes) -> str` and `sha256_file(path: Path) -> str` (chunked read). Replace the five copies in `run_jev_stage.py`, `evaluator/plan.py`, `evaluator/evidence_assets.py`, `evaluator/preflight.py`, `agent_runner/audit_adjudication.py`. The evaluator already imports from `agent_runner` through `evaluator/parity.py`, so this adds no new package edge. Check `evaluator/pyproject.toml` mypy config still type-checks the import.

8. Private imports across modules. Make the shared ones public with a docstring:
   - `agent_runner/jev_review._read_json` becomes `read_json_artifact` (it returns `(payload, digest)` and enforces `MAX_ARTIFACT_BYTES`, so it belongs next to the bound). Update `audit_metrics.py` and `integrity.py`.
   - `run_jev_stage._read_json` and `_sha256` used by `rerun_jev_partials.py`: delete `_sha256` in favour of step 7, rename `_read_json` to `read_json`.
   - `run_daily_briefing._write_chain_logs` used by `daily_publish.py`: rename to `write_chain_logs`.
   - `agent_runner/providers._urlopen` used by `decisions.py`: this is a security boundary (public destination and redirect validation per `SECURITY.md`). Rename to `open_validated_url` and leave the body alone. Tests may keep importing underscored names for monkeypatching; add a one-line note in each test where that is intentional.

Verification: full gate. Add `tests/test_positions.py` covering round-trips (`from_payload` to `to_payload`, `path` to `from_path`), bounds rejection, ordering, and that the anchor id for a fixed position is unchanged from today (compute the expected value from current `main` before starting).

Risk: `semantic_repairs.py` line 565 and 728 compare `t["position"] == check["positions"][0]` as raw dicts. Those still work during the migration because payload dicts compare by value, but convert them too so no raw-dict comparisons survive.

## Phase 2: flatten `apply_variant`

Goal: a table of named variant transforms instead of a 319-line elif chain,
so adding an evaluator case means adding one entry.

1. In `evaluator/cases.py`, define

   ```python
   Variant = Callable[[dict[str, Any], str, BriefingConfig], tuple[dict[str, Any], str, BriefingConfig]]
   VARIANTS: dict[str, Variant] = {}

   def variant(name: str) -> Callable[[Variant], Variant]: ...  # registers into VARIANTS
   ```

   and turn each `elif` body into a small decorated function. Branches that only touch `text` can use a helper `text_only(lambda text: _replace(...))`.

2. `apply_variant(variant)` becomes `baseline()` plus a dict lookup, raising `ValueError` for an unknown name exactly as the current trailing `else` does.

3. `evaluator/tests/test_cases.py:136` iterates variants from the case matrix; add an assertion that `set(VARIANTS) == {case["variant"] for case in matrix}` so a registered-but-unused or used-but-unregistered variant fails the suite.

Verification: `python3 -S -m unittest discover -s evaluator/tests` and `python3 -m evaluator checker`. The checker command string must not change.

Decision for you: group related variants into modules (`evaluator/variants/citations.py`, `.../structure.py`) or keep one table in `cases.py`? One table is less churn; split only if `cases.py` is still over 700 lines after the flatten.

## Phase 3: split `agent_runner/output.py`

The file holds seven concerns in sequence. Split along its existing section
order so the diff is mostly moves. Keep `agent_runner/output.py` as the
import facade re-exporting every public name, so `runner.py`,
`evaluator/parity.py`, `eval_briefing.py`, and tests do not change in this PR.
Remove the facade in a follow-up once imports are updated.

| New module | Lines moved from `output.py` | Contents |
| --- | --- | --- |
| `agent_runner/citations.py` | 20-76, 1390-1412 | `Citation`, `CitationDestination`, `ModelCorpus`, `OutputFinding`, `_complete_item_citations` |
| `agent_runner/redaction.py` | 76-184 | `redact_destinations`, `redecode_redacted_text`, `redact_opaque_references`, `redact_preview_value` and their regexes |
| `agent_runner/projection.py` | 185-297, 1250-1341 | `project_corpus`, `project_selected_evidence`, `attach_frozen_selection`, `detach_prose` |
| `agent_runner/schemas.py` | 298-484 | `build_selection_schema`, `build_prose_schema` and the schema builders |
| `agent_runner/structural_repair.py` | 485-849 | `REPAIRABLE_CHECKS`, `repair_structural_output`, `repair_entries`, `empty_section_findings`, `promote_excluded_to_underfilled` |
| `agent_runner/validation.py` | 850-1249, 1342-1380 | `_validate_structure`, `validate_selection`, `validate_prose_output`, `validate_output` |
| `agent_runner/render.py` | 1381-1775 | `render_briefing`, `render_candidate_preview`, `render_validation_status`, `complete_briefing` |

Rules for this phase:

- `redaction.py` and `projection.py` are the citation-projection security boundary named in `AGENTS.md` and ADR 0001. Move them, do not edit them. Update the file paths in `SECURITY.md`, `docs/adr/0001-citation-projection.md`, and `AGENTS.md` ("`agent_runner/output.py` implements citation projection").
- `repair_structural_output` (218 lines) and `repair_entries` (109) stay long in this PR. Splitting them is phase 4 work and should not be mixed with moves.
- Import direction: `citations` and `redaction` depend on nothing; `projection` depends on both; `schemas` on `citations`; `validation` on `citations` and `projection`; `structural_repair` on `validation`; `render` on `citations`. If a cycle appears, the move is wrong, not the import.

Verification: full gate, plus `git diff --stat` should show `output.py` shrinking to under 60 lines of re-exports and almost no changed lines inside moved blocks (`git diff --color-moved=dimmed-zebra` makes this reviewable).

## Phase 4: split `build_site.py` and shorten the longest functions

`build_site.py` is the only file allowed third-party imports, so the split must
keep `markdown-it` behind one module. Same facade approach: `build_site.py`
keeps `main()` and `build_site()` and imports the rest.

| New module | Lines | Contents |
| --- | --- | --- |
| `site/history.py` | 49-56, 224-496 | `BriefingEntry`, sidecar and history loading, `_history_payload`, `HISTORY_SCHEMA_VERSION` |
| `site/style.py` | 85-223 | the `STYLE` string, and `_document()` |
| `site/prose.py` | 535-966 | coverage labels, corpus-health prose, `_humanize_corpus_health`, `_omit_report_only_warnings`, `_merge_source_notes` |
| `site/markdown.py` | 944-1177 | `_render_markdown`, autolink, and the post-render decoration regexes. Only this module imports `markdown_it`. |
| `site/pages.py` | 1178-1446 | index, briefing page, nav, status chip, generation summary |
| `site/report.py` | 1447-2263 | integrity report, decision sentence, action ledger, `_render_semantic_audit` |
| `site/publish.py` | 2264-2334 | `_remove_path`, `_publish_audit_manifests` |

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
Count the checks outside parsers as the metric: today around 750 in total, and
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

## Phase 6: the README screenshot

The "AFTER" cell reading "A supported complete sentence." is the
`REPAIRED_PROSE` constant from `tests/test_semantic_repairs.py:19`, reused by
`tests/site_test_build.py:2085`. The screenshot is of a test fixture, as
`docs/results/remediation-2026-10-02.md` says, but a README reader has no way
to know that.

1. Change the fixture prose in `tests/site_test_build.py` (and only there; the semantic-repairs tests match on the prefix) to a sentence that reads as a plausible repair, for example "The maintainers published the patch after a second review."
2. Rebuild the fixture site from the current branch, open the integrity report at desktop width (the earlier capture was 1280x1000), and recapture `docs/images/auditor-report-current.jpg`. Compress to keep it near the current 124 KB.
3. Add a one-line caption under the README image table: "Both captures are from offline fixtures with deterministic fake model responses."
4. Record the capture command in `docs/dogfooding.md` so the image can be regenerated after phase 4 lands, since the report layout will not change but the fixture may.

This phase has no code dependency and can go first if a visible fix is wanted sooner.

## What this plan does not do

- It does not change any schema version, any validation bound, or any error message.
- It does not reorder the pipeline or touch provider transport, DNS, or redirect checks beyond renaming `_urlopen`.
- It does not add a dependency. Every new module is stdlib only except `site_build/markdown.py`, which inherits `build_site.py`'s allowance.
- It does not remove the locked `site-portfolio-polish` worktree, but that worktree will conflict with phase 4. Finish or discard it before starting phase 4.

## Execution options

Option A, one session with subagents: run phases in order here, one PR each, using the repository gate between phases. Phases 2 and 6 can run as parallel subagents during phase 1 because they share no files.

Option B, parallel sessions. Phase 1 must land first; after that, phases 2, 3, and 6 are independent of each other. Ready-to-paste prompt for a fresh session on phase 2:

```
Read docs/plans/2026-10-26-structural-cleanup.md and implement Phase 2 only
(flatten evaluator/cases.py apply_variant into a registered variant table).
Branch from main. Do not change the evaluator checker command string. Run
`uvx ruff@0.14.2 check . && uvx mypy@1.14.1 && uvx mypy@1.14.1 --config-file
evaluator/pyproject.toml evaluator && python3 -S -m unittest && python3 -S -m
unittest discover -s evaluator/tests && python3 -m evaluator checker` before
opening the PR. Keep one commit per numbered step in the plan.
```

Swap "Phase 2" for "Phase 6" to get the screenshot work started in parallel; Phase 3 and later should wait for Phase 1 to merge.
