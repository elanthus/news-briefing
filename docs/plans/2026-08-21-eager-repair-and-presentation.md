# Eager Repair, Publication Manifest, and Reader/Auditor Split Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make deterministic repair the first response to editorial placement findings, carry structured finding/repair data through publication instead of re-parsing markdown, and split the public page into a clean reader view plus a per-run auditor report — so backfilled GitHub Pages present cleanly for ShowHN.

**Architecture:** Three follow-up PRs after PR 98 merges. PR A reorders the runner loop so `repair_structural_output` runs eagerly on repairable findings (model corrections are reserved for what repair can't fix), which makes editorial findings vanish from published output by construction — no severity relabeling needed, the audit trail is `repair_actions`. PR B has the producer label its own output: `render_briefing` emits stable HTML-comment story anchors, and the publication sidecar gains `repair_actions` plus path-keyed finding context, deleting `build_site.py`'s heading-regex layer. PR C reworks presentation: status chip on the briefing page, per-run integrity report page, and quarantine stubs.

**Tech Stack:** Python 3.11 stdlib-only runtime (`agent_runner/`), pytest, markdown-it rendering in `build_site.py` (pinned via `requirements-site.txt`).

---

## Decision record: what lands where

| Change | Lands in | Why |
| --- | --- | --- |
| Metamorphic repair invariant test | **PR 98** (this branch, before merge) | Tests code PR 98 introduced; cheap; strengthens the reviewed PR without scope creep |
| Schema-enum docs wording ("hint, not enforcement") | **PR 98** | Two-line doc fix to text PR 98 added |
| Eager repair ordering + over-limit trimming | **PR A** | Behavioral change to the correction loop; deserves its own review |
| Anchors + sidecar v4 (repair_actions, path-keyed context) | **PR B** | Data plumbing; prerequisite for PR C |
| Status chip, report page, quarantine stubs | **PR C** | Pure presentation, depends on PR B's data |
| Content-based (not source-based) categorization | **Deferred — not planned** | `general_news` triage is acceptable; revisit only if ineligible-category findings recur after PR A |
| Backfill rerun | **Operational, after PR 98 merges** (and again after PR C) | Historical pages regenerate only when the workflow reruns |

Rationale for the split: PR 98 is already reviewed (CodeRabbit + preflight attestation) and fixes the root cause of the ugly backfill (Axios categorization + repair fallback). Piling the loop reorder and presentation overhaul into it would invalidate that review and delay unblocking the backfill. Merge 98, rerun the 7-day backfill to get acceptable pages immediately, then improve.

A key simplification discovered during design: **once repair runs eagerly, there is no need for a WARN-level "editorial" class.** Repaired findings never reach `classify_outcome` — the repaired attempt re-validates clean, disposition becomes `ready`, and the only surviving record is `repair_actions`. Integrity findings (unknown refs, freeform URLs, schema violations) are untouched by repair, still produce `rejected`/`review_required`, and still quarantine. The severity split falls out of ordering, not labeling. Under-fill after repair is already covered: `eval_briefing.py:499` emits WARN-only `slots_underfilled` (non-blocking `quality` domain per `agent_runner/outcomes.py:37-44`); PR C surfaces it as a reader-facing note.

---

## Phase 0 — finish PR 98 (this branch)

### Task 0.1: Metamorphic invariant test for repair

The invariant the whole design leans on: repairing any output leaves no findings that repair claims to fix. Both functions are pure, so this is cheap to test across a grid of corrupted outputs.

**Files:**
- Test: `test_briefing_output.py` (append)

**Step 1: Write the failing-or-passing invariant test**

Reuse the module's existing fixture helpers for a valid output/config/citations triple (see the top of `test_briefing_output.py` for the builders used by the current repair tests). Add:

```python
REPAIRABLE_CHECKS = {"category_ineligible_ref", "duplicate_citation_ref", "duplicate_item"}


def _corruptions(output):
    """Yield independently corrupted copies of a valid output."""
    import copy, itertools, json

    # 1. Duplicate one included entry into another section's topics.
    dup = copy.deepcopy(output)
    sections = list(dup["sections"])
    first_topics = dup["sections"][sections[0]]["topics"]
    dup["sections"][sections[-1]]["topics"].append(copy.deepcopy(first_topics[0]))
    yield dup
    # 2. Repeat a ref inside one entry.
    rep = copy.deepcopy(output)
    entry = rep["sections"][sections[0]]["topics"][0]
    entry["citation_refs"].append(entry["citation_refs"][0])
    yield rep
    # 3. Cross-pollinate: give every section every other section's first ref
    #    (guarantees ineligible refs when categories differ).
    for a, b in itertools.permutations(sections, 2):
        cross = copy.deepcopy(output)
        donor = cross["sections"][a]["topics"][0]["citation_refs"][0]
        cross["sections"][b]["topics"][0]["citation_refs"].append(donor)
        yield cross


def test_repair_is_idempotent_and_clears_repairable_findings():
    output, config, citations = _valid_output_config_citations()  # existing helper
    for corrupted in _corruptions(output):
        repaired, actions = output_module.repair_structural_output(
            corrupted, config, citations
        )
        residual = {
            f.check
            for f in output_module.validate_output(repaired, config, citations)
        }
        assert not (residual & REPAIRABLE_CHECKS), (actions, residual)
        # Idempotence: repairing a repaired output is a no-op.
        again, second_actions = output_module.repair_structural_output(
            repaired, config, citations
        )
        assert again == repaired
        assert second_actions == []
```

Adapt `_valid_output_config_citations` to whatever helper the existing repair tests in this file actually use — do not invent a parallel fixture.

**Step 2: Run it**

Run: `python -m pytest test_briefing_output.py -k idempotent -v`
Expected: PASS (this validates PR 98's code; if it fails, that is a real PR 98 bug — fix `repair_structural_output`, not the test).

**Step 3: Docs wording fix**

In `README.md` and `docs/design.md`, where PR 98 describes the per-section ref enums, ensure the wording says the schema constrains *cooperative* providers and the deterministic checker remains the guarantee (providers do not uniformly honor `items.enum`/`uniqueItems`). One sentence each.

**Step 4: Full test run and commit**

Run: `python -m pytest -x -q`
Expected: all pass.

```bash
git add test_briefing_output.py README.md docs/design.md
git commit -m "test: add repair idempotence/clearing invariant; clarify schema is a hint"
```

Then merge PR 98 and **rerun the 7-day backfill** (manual daily-briefing workflow mode from #97) so the historical pages regenerate with repair active.

---

## Phase A — eager repair (new branch `feat/eager-repair` off main after 98 merges; one PR)

### Task A.1: Classify repairable findings

**Files:**
- Modify: `agent_runner/output.py` (near `repair_structural_output`)
- Test: `test_briefing_output.py`

**Step 1: Write the failing test**

```python
def test_repairable_finding_partition():
    assert output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "category_ineligible_ref", "x")
    )
    assert output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "duplicate_item", "x")
    )
    assert output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "duplicate_citation_ref", "x")
    )
    assert output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "structured_item_limit", "x")
    )
    assert not output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "unknown_citation_ref", "x")
    )
    assert not output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "freeform_url", "x")
    )
    assert not output_module.is_repairable_finding(
        output_module.OutputFinding("ERROR", "structured_type", "x")
    )
```

(Match `OutputFinding`'s real constructor signature — check its dataclass fields first.)

**Step 2: Run to verify it fails** — `python -m pytest test_briefing_output.py -k partition -v` → FAIL (`is_repairable_finding` undefined).

**Step 3: Implement**

```python
REPAIRABLE_CHECKS = frozenset({
    "category_ineligible_ref",
    "duplicate_citation_ref",
    "duplicate_item",
    "structured_item_limit",
})


def is_repairable_finding(finding: OutputFinding) -> bool:
    """True when deterministic structural repair fixes this finding by construction."""
    return finding.check in REPAIRABLE_CHECKS
```

**Step 4: Run to verify pass, commit** — `git commit -m "feat: classify deterministically repairable findings"`

### Task A.2: Repair trims over-limit entry lists

`structured_item_limit` fires when a section exceeds `target_stories`/`excluded_stories` (`agent_runner/output.py:452-455`), but `repair_structural_output` never trims. For the repairable set to be honest, repair must handle it.

**Step 1: Write the failing test** — build a valid output, append one extra valid entry beyond `target_stories` to a section, assert repair drops trailing entries down to the limit, records a `drop_entry` action with reason mentioning the limit, and the metamorphic invariant from Task 0.1 extended with this corruption still holds (add an over-limit corruption to `_corruptions`).

**Step 2: Run to verify it fails.**

**Step 3: Implement** — in `repair_entries`, pass the section maximum (`section.target_stories` / `section.excluded_stories`) and, after the existing per-entry loop builds `kept`, truncate:

```python
        for index, entry in enumerate(kept[maximum:], start=maximum):
            actions.append({
                "action": "drop_entry",
                "path": f"{label}.{section.name}[{index}]",
                "reason": f"section exceeds maximum of {maximum} entries",
            })
        del kept[maximum:]
```

Trim **before** registering `used_items` for the dropped tail entries (move the `used_items` registration after truncation, or register only for entries that survive), so a trimmed entry does not block a later legitimate use of its item. Extend `REPAIRABLE_CHECKS` note in the docstring.

**Step 4: Run tests, commit.**

### Task A.3: Reorder the runner loop — repair before corrections

**Files:**
- Modify: `agent_runner/runner.py` (`run_workflow` main loop, around the `corrections_used` check at `runner.py:731`)
- Test: `test_run_briefing.py`

**Step 1: Write the failing test**

Use the existing fake-provider harness in `test_run_briefing.py` (the tests PR 98 touched show the pattern). Scenario: provider's first response contains only repairable findings (e.g., one duplicate item across sections). Assert:
- the run completes with disposition `ready`,
- the manifest contains a `deterministic_repair` attempt,
- **zero** `correction` attempts exist (no model correction call was spent),
- `repair_actions` is non-empty on the repair attempt.

Second scenario: first response contains an unknown citation ref (non-repairable). Assert a `correction` attempt IS made (budget spent on what repair can't fix), preserving current behavior.

**Step 2: Run to verify it fails** — today the duplicate-item scenario consumes a correction first.

**Step 3: Implement**

In `run_workflow`, after `_validate_attempt` produces findings for the current attempt and before the corrections-budget check, insert:

```python
            blocking = [f for f in attempt_findings if f.level == "ERROR"]
            if blocking and all(is_repairable_finding(f) for f in blocking):
                return _finalize_after_deterministic_repair(
                    store, attempt, output,
                    corpus=corpus, config=config,
                    citations=projected.citations, settings=settings,
                )
```

(Adapt to how findings are actually held at that point in the loop — they may live on the attempt row as dicts; if so, compare `row["check"] in REPAIRABLE_CHECKS` and `row["level"] == "ERROR"`.) Keep the existing exhausted-budget path calling `_finalize_after_deterministic_repair` unchanged — it remains the fallback when corrections for non-repairable findings run out. Mixed findings (repairable + not) go to correction as today; the exhausted-budget repair still cleans up the repairable remainder.

**Step 4: Run** `python -m pytest test_run_briefing.py test_briefing_output.py -q` → PASS. Also run the full suite: `python -m pytest -q`.

**Step 5: Update docs** — `docs/design.md` orchestration section and the README architecture bullet: correction budget is reserved for findings repair cannot fix; editorial placement errors are repaired deterministically and logged as `repair_actions`. Update the mermaid flowchart edge labels in `README.md` (repair now sits before the correction loop for repairable findings, and remains the post-budget fallback).

**Step 6: Commit** — `git commit -m "feat: run deterministic repair before spending model corrections"`

### Task A.4: Under-fill visibility check

**Step 1: Write the test** — a run where repair drops a story below `target_stories`; assert final findings include `slots_underfilled` WARN and the disposition is still `ready` (it is a non-blocking `quality` finding per `outcomes.py:44`).

**Step 2: Run.** If it already passes (because `_finalize_candidate` re-renders and `eval_briefing` checks the rendered briefing), keep the test as a regression guard and commit. If it fails because eval checks are skipped on the repair path, wire `_finalize_candidate`'s existing eval step onto the repaired attempt — do not add a new check; `eval_briefing.py:499` already owns it.

**Step 3: Commit.** Open **PR A**.

---

## Phase B — producer-labeled output and sidecar v4 (branch `feat/publication-manifest`; one PR)

### Task B.1: Story anchors in rendered markdown

Delete the need for heading regexes by having `render_briefing` (in `agent_runner/output.py`) label each story with an HTML comment carrying its structured path.

**Files:**
- Modify: `agent_runner/output.py` (`render_briefing` and the excluded-topics rendering)
- Test: `test_briefing_output.py`

**Step 1: Write the failing test**

```python
def test_rendered_briefing_carries_story_anchors():
    output, config, citations, corpus = _valid_render_inputs()  # existing helper
    markdown = output_module.render_briefing(output, config, citations, corpus)
    assert '<!-- story: topics.' in markdown
    assert '<!-- story: excluded_topics.' in markdown
    # One anchor per entry, in order, directly above each headline line.
    for section_name, section in output["sections"].items():
        for index in range(len(section["topics"])):
            assert f"<!-- story: topics.{section_name}[{index}] -->" in markdown
```

**Step 2: Run to verify it fails.**

**Step 3: Implement** — where `render_briefing` emits each included topic and each excluded topic, prepend a line `f"<!-- story: {label}.{section.name}[{index}] -->"` using exactly the same path grammar as `validate_output`/`repair_structural_output` (`topics.US News[3]`). HTML comments survive markdown-it rendering into the page source and are invisible to readers. Confirm `eval_briefing` checks don't flag comment lines (run its tests).

**Step 4: Run full suite, commit.**

### Task B.2: Sidecar v4 — repair_actions and path-keyed findings

**Files:**
- Modify: `prepare_publication.py`
- Test: `test_prepare_publication.py`

**Step 1: Write the failing tests**

- A run manifest whose final attempt is `deterministic_repair` with two `repair_actions` → sidecar payload contains `"repair_actions": [...]` verbatim, hash-bound via the manifest like other artifacts (actions live in `manifest.json` itself, which is already trusted input, so no extra digest needed — assert they're copied from the final attempt row only).
- A `review_required` run → each finding's `context` gains a `"path"` field when `STRUCTURED_PATH` matched (keep `section`/`headline` for backward compatibility during PR B; PR C stops reading them).
- A `ready` run with zero findings → `"repair_actions": []` present (schema-stable).

**Step 2: Run to verify failure.**

**Step 3: Implement** — extend `PublicationRecord` with `repair_actions: tuple[dict[str, str], ...]`; in `prepare_publication`, read the final attempt row (already located by `_final_structured_output`'s loop — factor that lookup into a helper `_final_attempt(manifest, final)`) and copy its `repair_actions` list if `kind == "deterministic_repair"`, validating each item is a dict with string `action`/`path`/`reason` (drop the field to `[]` otherwise, fail-closed). Add `path` to `ReviewContext`.

**Step 4: Run, commit.**

### Task B.3: build_site matches findings by anchor, not heading regex

**Files:**
- Modify: `build_site.py` (`_render_markdown`, delete `_section_subheading`, `EXCLUDED_CONTEXT_PREFIX` tracking, `_topic_headline` fallback matching)
- Test: `test_build_site.py`

**Step 1: Write the failing test** — feed `_render_markdown` a markdown document containing story anchors plus a findings tuple whose context carries `path`; assert the inline review panel attaches to the anchored story even when (a) the section heading format changes and (b) two sections contain identical headlines. Add a test that a finding with a `path` no anchor matches falls back to the top-level unmatched panel (never silently dropped).

**Step 2: Run to verify failure.**

**Step 3: Implement** — in `_render_markdown`, track the current story path from `<!-- story: ... -->` lines; match findings by `finding.path == current_path` (extend `ReviewFinding` in `build_site.py` with `path: str | None`, populated from sidecar context in `_entry_from_payload`). Keep headline/section matching only as a legacy fallback for pre-v4 sidecars, behind a single guard, and delete `_section_subheading` and the `excluded_section` state machine. The digest-marker insertion mechanism stays (it is how panels get through markdown-it) — only the *matching* layer changes.

**Step 4: Run** `python -m pytest test_build_site.py test_prepare_publication.py -q`, then full suite, commit. Open **PR B**.

---

## Phase C — reader/auditor split (branch `feat/report-pages`; one PR; depends on B)

### Task C.1: Per-run integrity report page

**Files:**
- Modify: `build_site.py`
- Test: `test_build_site.py`

**Step 1: Write the failing tests**

- Site build for an entry with findings/repair_actions/degraded sources produces `reports/<slug>.html` containing: verdict, full findings list with the operator "Action:" text, the repair log ("dropped topics.AI News[1]: …"), and the degraded-sources list.
- Every briefing page links to its report page; report pages link back.
- An entry with no findings, no repairs, and full coverage still gets a report page (states "all checks passed") — the chip always has somewhere to point.

**Step 2: Run to verify failure.**

**Step 3: Implement** — new `_render_report(entry, entries) -> str` reusing `_render_review_panel` (move the "Action:" text here); `build_site` writes `reports/<slug>.html` per entry. Corpus-health and degraded-source detail move here from `_entry_body`.

**Step 4: Run, commit.**

### Task C.2: Status chip replaces the verdict/corpus-health header

**Step 1: Write the failing tests** — `_entry_body` output for:
- `ready`, no repairs → chip text `✓ Verified` linking to `reports/<slug>.html`; no `Checker verdict:` line; no `Corpus health:` line.
- `ready` with N repair_actions → `⚠ Published after automated repair (N actions)`.
- `ready` with a `slots_underfilled` WARN → chip gains a short note (e.g., `· 1 section under target`).
- degraded coverage → chip suffix `· sources degraded` (detail on report page).

**Step 2: Run to verify failure.**

**Step 3: Implement** — replace the two `<p>` lines in `_entry_body` (`build_site.py:490-491`) with one `<p class="status-chip">…</p>`; add minimal CSS (muted pill, no orange). Chip states: `✓ Verified` / `⚠ Published after automated repair (N)` / `🔍 Review required` / `✖ Not published`. Keep it one line; everything else lives on the report page.

**Step 4: Run, commit.**

### Task C.3: Quarantine stubs and index selection

**Step 1: Write the failing tests**

- A `review_required` entry renders a stub body: one paragraph ("This day's briefing did not pass automated checks and is withheld."), chip `🔍 Review required`, link to the report page, **no briefing prose** on the public page (the preview markdown moves to the report page inside the existing redaction affordances).
- The index (`index.html`) renders the newest entry regardless of disposition (stub included) — history nav unchanged — so a bad day is an honest gap, not a hidden one.
- Inline review panels (`_render_markdown` finding attachment) now only ever render on report pages; assert briefing pages contain no `review-panel`/`review-story` markup.

**Step 2: Run to verify failure.**

**Step 3: Implement** — in `_entry_body`, branch on disposition: `review_required` → stub; move the markdown-with-findings rendering into the report page path. Delete now-unused inline styling from `STYLE` only if nothing references it.

**Step 4: Run full suite, update `docs/design.md` publication section + README screenshots/wording, commit. Open PR C.**

### Task C.4: Final backfill rerun and visual check

After PR C merges: rerun the 7-day manual backfill, then eyeball the deployed Pages output — index chip states, one report page, one stub (if any day is quarantined). This is the ShowHN acceptance check.

---

## Non-goals (explicitly deferred)

- Content-based item categorization at fetch time (revisit only if ineligible-category findings recur post-PR-A).
- Any WARN-level relabeling of editorial checks in `outcomes.py` — eager repair makes it moot.
- Rendering pages from structured JSON instead of markdown — anchors give exact attachment without a renderer rewrite (YAGNI).
