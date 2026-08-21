# Portfolio-Quality Briefing Pages Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish every daily briefing at full quality: deterministically repair
`claim_exceeds_evidence` withholdings by swapping in the labeled source excerpt,
render corpus health as readable prose, and make 7-day backfill use stored
corpora instead of re-fetching feeds that no longer retain the window.

**Architecture:** Three independent tracks. (1) A new evidence-swap pass inside
`repair_structural_output` plus a WARN-repair hook in the run loop, with a
`*(source excerpt)*` producer marker emitted by `render_briefing`. (2) A
render-time transform in `build_site.py` that replaces the corpus-health JSON
block with grouped human-readable text. (3) `build_site.py` publishes per-day
corpora to `site/corpora/`, and the workflow resolves each backfill day as
stored corpus → live fetch (≤ 3 days old) → skip.

**Tech Stack:** Python 3.13 stdlib (zero-dependency runtime), `markdown-it-py`
(site rendering only), GitHub Actions bash, `unittest`.

**Design doc:** `docs/plans/2026-08-21-portfolio-quality-briefings-design.md`

**Verified facts the plan relies on (do not re-derive):**
- `claim_exceeds_evidence` is a WARN from `check_claims_supported`
  (`eval_briefing.py:944`): fires when `len(prose) > CLAIM_EVIDENCE_RATIO *
  len(support)` where `support = " ".join(dict.fromkeys(evidence[url] for url
  in links if evidence.get(url))).strip()`.
- `eval_briefing.corpus_evidence(corpus)` maps **canonicalized** URLs → "title
  summary" text. `agent_runner/output.py` already imports `eval_briefing`
  (no import cycle).
- The run loop (`agent_runner/runner.py:742-756`) only triggers deterministic
  repair for **ERROR** findings; a WARN-only candidate has
  `contract_success == True` and finalizes immediately at `runner.py:724`. The
  WARN swap therefore needs its own hook there.
- `_TOPIC_LINE` (`eval_briefing.py:70`) already tolerates one `*(...)*` marker
  between headline and em dash and excludes it from `prose`. `*(consolidated)*`
  uses this; a combined `*(consolidated · source excerpt)*` also matches.
- The corpus-health JSON block is emitted by `render_briefing`
  (`agent_runner/output.py:717`) and validated by
  `check_corpus_health_reported`; the published page HTML is rendered by
  `build_site._render_markdown` with `markdown-it` (`html: False`, so any
  friendly replacement must be Markdown text, not raw HTML).
- `test_daily_workflow.py` asserts on the raw YAML text of
  `.github/workflows/daily-briefing.yml` — follow that pattern.
- Repair actions are `{"action", "path", "reason"}` dicts; sidecar v4 already
  publishes `repair_actions` (`PublicationRecord`, `build_site.py:19`).
- Structural repair may drop entries, so the swap pass must run **after** the
  existing structural pass and compute paths from post-repair indices, which
  match `render_briefing`'s anchor indices.

**Branch:** create `feat/portfolio-quality-pages` off `main` before Task 1.

---

### Task 1: Evidence-swap pass in `repair_structural_output`

**Files:**
- Modify: `agent_runner/output.py` (`REPAIRABLE_CHECKS` ~line 249,
  `repair_structural_output` ~line 270)
- Test: `test_briefing_output.py` (copy fixture setup from
  `test_structural_repair_drops_ineligible_and_repeated_entries`, ~line 328)

**Step 1: Write the failing tests**

Add to `test_briefing_output.py`, reusing the existing config/projection
fixtures from the structural-repair tests:

```python
def test_repair_swaps_summary_exceeding_evidence_for_excerpt(self):
    # Entry whose summary is > 2x its cited evidence text.
    # Build `evidence` with eval_briefing.corpus_evidence(corpus).
    repaired, actions = repair_structural_output(
        output, config, projected.citations,
        evidence=eval_briefing.corpus_evidence(corpus))
    entry = repaired["sections"]["AI Dev Practices"]["topics"][0]
    # Summary replaced by the deduplicated cited evidence join.
    self.assertEqual(entry["summary"], expected_support)
    self.assertIn({"action": "replace_summary_with_excerpt",
                   "path": "topics.AI Dev Practices[0]",
                   "reason": actions[0]["reason"]}, actions)

def test_repair_swap_skips_short_summaries_unknown_refs_and_no_evidence(self):
    # (a) summary within ratio -> untouched, no action
    # (b) entry with a ref not in citations -> untouched (preserved for review)
    # (c) entry whose citations have no corpus evidence -> untouched
    # (d) evidence=None (default) -> behaves exactly as before

def test_repair_swap_runs_after_structural_drops_and_uses_final_paths(self):
    # Section with entry[0] droppable (repeated item) and entry[1] bloated:
    # after repair the swap action path must be topics.<name>[0] (post-drop).

def test_claim_exceeds_evidence_is_repairable(self):
    self.assertIn("claim_exceeds_evidence", REPAIRABLE_CHECKS)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest test_briefing_output.py -k "swap or claim_exceeds" -v`
Expected: FAIL (unexpected keyword `evidence`; membership assert fails).

**Step 3: Implement**

In `agent_runner/output.py`:

1. Add `"claim_exceeds_evidence"` to `REPAIRABLE_CHECKS` and extend the
   `is_repairable_finding` docstring: the swap replaces prose with its own
   cited evidence, so the finding cannot re-fire by construction.
2. Change the signature:
   `def repair_structural_output(output, config, citations, evidence=None):`
   (`evidence: dict[str, str] | None`).
3. After the two existing `repair_entries` loops, when `evidence` is truthy,
   run a swap pass over included topics only (the check never fires for
   excluded entries):

```python
def _swap_oversized_summaries() -> None:
    for section in config.sections:
        section_value = sections.get(section.name)
        if not isinstance(section_value, dict):
            continue
        topics = section_value.get("topics")
        if not isinstance(topics, list):
            continue
        for index, entry in enumerate(topics):
            if not isinstance(entry, dict):
                continue
            refs = entry.get("citation_refs")
            summary = entry.get("summary")
            if not isinstance(refs, list) or not isinstance(summary, str):
                continue
            if any(not isinstance(ref, str) or ref not in citations
                   for ref in refs):
                continue  # evidence-boundary violation: preserve for review
            support = " ".join(dict.fromkeys(
                text
                for citation in _complete_item_citations(refs, citations)
                if (text := evidence.get(
                    corpus_schema.canonicalize_url(citation.url)))
            )).strip()
            if not support:
                continue
            if len(summary) <= eval_briefing.CLAIM_EVIDENCE_RATIO * len(support):
                continue
            # Collapse whitespace so a multi-line blurb cannot break the
            # one-line topic grammar; collapsing only shrinks the prose, so
            # the ratio check still passes against the raw corpus text.
            entry["summary"] = " ".join(support.split())
            actions.append({
                "action": "replace_summary_with_excerpt",
                "path": f"topics.{section.name}[{index}]",
                "reason": (
                    f"summary of {len(summary)} characters exceeded "
                    f"{len(support)} characters of cited evidence; "
                    "replaced with the verbatim source excerpt"),
            })
```

Mirror the checker exactly: same dedup-by-text join, same ratio comparison
(`eval_briefing.CLAIM_EVIDENCE_RATIO`).

**Step 4: Run tests to verify they pass**

Run: `python -m pytest test_briefing_output.py -v`
Expected: all PASS (including pre-existing repair tests — `evidence=None`
default must not change old behavior).

**Step 5: Commit**

```bash
git add agent_runner/output.py test_briefing_output.py
git commit -m "feat: deterministic evidence-swap repair for claim_exceeds_evidence"
```

---

### Task 2: `*(source excerpt)*` producer marker in `render_briefing`

**Files:**
- Modify: `agent_runner/output.py` (`_topic_lines` ~line 633,
  `render_briefing` ~line 644)
- Test: `test_briefing_output.py`

**Step 1: Write the failing tests**

```python
def test_render_marks_swapped_entries_as_source_excerpts(self):
    actions = [{"action": "replace_summary_with_excerpt",
                "path": "topics.AI Dev Practices[0]", "reason": "…"}]
    rendered = render_briefing(output, corpus, config, citations,
                               repair_actions=actions)
    self.assertIn("*(source excerpt)*", rendered)
    # Consolidated + swapped entry combines both in one marker group:
    # "*(consolidated · source excerpt)*" (add a second fixture entry).

def test_source_excerpt_marker_is_excluded_from_checker_prose(self):
    # End-to-end invariant: render the swapped output with its actions,
    # then eval_briefing.evaluate(corpus, rendered, config) must contain
    # no claim_exceeds_evidence, unsupported_figure, or
    # unsupported_quotation finding.
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest test_briefing_output.py -k excerpt -v`
Expected: FAIL (unexpected keyword `repair_actions`).

**Step 3: Implement**

1. `render_briefing(output, corpus, config, citations, repair_actions=())`.
   Compute once:
   `swapped = {a["path"] for a in repair_actions if a.get("action") == "replace_summary_with_excerpt"}`.
2. `_topic_lines(entry, citations, *, excerpt=False)`; build the marker list:

```python
labels = []
if len(item_refs) > 1:
    labels.append("consolidated")
if excerpt:
    labels.append("source excerpt")
marker = f" *({' · '.join(labels)})*" if labels else ""
lines = [f"**{entry['headline']}**{marker} — {entry['summary']}"]
```

3. At each topic call site (both grouped and ungrouped section loops), pass
   `excerpt=f"topics.{section.name}[{topic_index}]" in swapped`.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest test_briefing_output.py test_eval_briefing.py -v`
Expected: PASS (the invariant test proves the marker round-trips through the
checker's `_TOPIC_LINE` grammar without becoming prose).

**Step 5: Commit**

```bash
git add agent_runner/output.py test_briefing_output.py
git commit -m "feat: label excerpt-swapped entries as source excerpts in rendered output"
```

---

### Task 3: Runner integration — repair WARN-only candidates before finalizing

**Files:**
- Modify: `agent_runner/runner.py` (`_deterministic_repair_attempt` ~line 329,
  `_validate_attempt` ~line 433, run loop ~line 715)
- Test: `test_run_briefing.py` (reuse the existing fake-provider workflow
  fixtures)

**Step 1: Write the failing test**

```python
def test_bloated_summary_is_swapped_and_run_finalizes_ready(self):
    # Fake provider returns a structurally valid output whose only defect is
    # one summary > 2x its cited evidence (would classify review_required).
    result = <run workflow with fixtures>
    self.assertEqual(result.disposition, "ready")
    manifest = <load run manifest>
    repair = [a for a in manifest["attempts"]
              if a["kind"] == "deterministic_repair"]
    self.assertEqual(len(repair), 1)
    self.assertEqual(repair[0]["repair_actions"][0]["action"],
                     "replace_summary_with_excerpt")
    final_markdown = <read final briefing artifact>
    self.assertIn("*(source excerpt)*", final_markdown)
    # No provider correction call was spent on the WARN.
    self.assertEqual(<provider call count>, 1)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest test_run_briefing.py -k swapped -v`
Expected: FAIL — disposition is `review_required`, no repair attempt.

**Step 3: Implement**

1. `_deterministic_repair_attempt`: compute
   `evidence = eval_briefing.corpus_evidence(corpus)` and pass it to
   `repair_structural_output(output, config, citations, evidence=evidence)`;
   pass `repair_actions=actions` through to `_validate_attempt`.
2. `_validate_attempt(..., repair_actions=())`: forward to
   `render_briefing(output, corpus, config, citations, repair_actions=repair_actions)`.
3. In the run loop, replace the `if attempt["contract_success"]:` early
   finalize with a swap-aware guard:

```python
if attempt["contract_success"]:
    swap_worthy = any(
        row["check"] == "claim_exceeds_evidence" for row in findings)
    if swap_worthy and attempt.get("kind") != "deterministic_repair":
        repair_attempt, output = _deterministic_repair_attempt(
            store, output, corpus=corpus, config=config,
            citations=projected.citations)
        if repair_attempt is not attempt:
            continue  # re-validates; loop finalizes the repaired attempt
    return _finalize_candidate(...)
```

   The existing `if not actions: return current, output` no-op guard in
   `_deterministic_repair_attempt` plus the `kind` check prevent loops: a
   repair that produced no actions falls through and finalizes as before.
4. Keep the existing ERROR-repair branch untouched.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest test_run_briefing.py test_outcomes.py test_prepare_publication.py -v`
Expected: PASS — including existing tests for checkpoint/resume and the
correction loop (the new branch only fires on WARN-only candidates).

**Step 5: Commit**

```bash
git add agent_runner/runner.py test_run_briefing.py
git commit -m "feat: repair claim_exceeds_evidence before finalizing instead of withholding"
```

---

### Task 4: Human-readable corpus health on the site

**Files:**
- Modify: `build_site.py` (new helper + call in `_render_markdown` ~line 424)
- Test: `test_build_site.py`

**Step 1: Write the failing tests**

```python
def test_corpus_health_json_renders_as_grouped_prose(self):
    # Markdown fixture containing the renderer-emitted block:
    # ### Corpus health / explanation line / ```json {"failed_sources": [...]} ```
    # with rss empties, one hacker_news empty, one reddit error.
    html_out, _ = _render_markdown(markdown)
    self.assertNotIn("failed_sources", html_out)
    self.assertNotIn("```", html_out)
    self.assertIn("2 RSS feeds and 1 Hacker News search returned no items", html_out)
    self.assertIn("fetch failed", html_out)      # reddit error group
    self.assertIn("NPR Politics", html_out)

def test_malformed_corpus_health_json_is_left_verbatim(self):
    # Unparseable JSON inside the fence -> block passes through unchanged
    # (escaped by markdown-it, never interpreted).
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest test_build_site.py -k corpus_health -v`
Expected: FAIL — raw JSON still present in output HTML.

**Step 3: Implement**

Add `_humanize_corpus_health(markdown: str) -> str` and call it at the top of
`_render_markdown` (page rendering only — `history.json` keeps the raw
machine block). Find the fenced ` ```json ` block that follows the
`### Corpus health` heading; `json.loads` it; on any failure return the
input unchanged. On success replace the fence (and the fixed explanation
sentence) with Markdown:

```python
_TYPE_LABELS = {"rss": ("RSS feed", "RSS feeds"),
                "hacker_news": ("Hacker News search", "Hacker News searches"),
                "reddit": ("subreddit", "subreddits")}
_STATUS_LABELS = {"empty": "returned no items in this day's window",
                  "error": "fetch failed"}
```

Output shape (plain Markdown, since `html: False`):

```
⚠ 15 RSS feeds and 2 Hacker News searches returned no items in this day's
window.

- **RSS feeds — no items in this day's window:** NPR Politics, The Hill, …
- **Hacker News searches — no items in this day's window:** "llm agent", …
- **Subreddits — fetch failed:** LocalLLaMA, cursor
```

Summary sentence counts only `empty` groups; `error` groups get their own
sentence ("1 subreddit fetch failed."). Unknown types/statuses render
verbatim in their own group — never dropped.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest test_build_site.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add build_site.py test_build_site.py
git commit -m "feat: render corpus health as grouped prose instead of raw JSON"
```

---

### Task 5: Publish per-day corpora to `site/corpora/`

**Files:**
- Modify: `build_site.py` (`build_site` ~line 644, `main` ~line 692)
- Test: `test_build_site.py`

**Step 1: Write the failing tests**

```python
def test_publishes_valid_dated_corpora_with_first_dir_precedence(self):
    # fresh/2026-08-20.json and prior/2026-08-20.json differ -> fresh wins.
    # prior/2026-08-14.json only in prior -> published.
    # not-a-date.json, invalid-json 2026-08-19.json -> skipped.
    build_site(history_dir, out, corpora_dirs=[fresh, prior])
    assert (out / "corpora" / "2026-08-20.json") matches fresh content
    assert (out / "corpora" / "2026-08-14.json").exists()
    assert not (out / "corpora" / "2026-08-19.json").exists()

def test_prunes_corpora_older_than_fourteen_days(self):
    # A corpus dated 15+ days before the newest history entry is not copied.

def test_no_corpora_dir_builds_site_without_corpora(self):
    # Backwards compatible: omitted flag -> no site/corpora directory.
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest test_build_site.py -k corpora -v`
Expected: FAIL (unexpected keyword `corpora_dirs`).

**Step 3: Implement**

- `build_site(..., corpora_dirs: Sequence[Path] = ())`. For each dir in order,
  glob `*.json` whose stem parses as `date.fromisoformat`; skip stems already
  claimed (first dir wins); skip files that fail `json.loads` (a downloaded
  file is untrusted input — validate before publishing); skip dates older
  than `newest_entry_date - 13 days`. Write survivors to
  `output_dir / "corpora" / f"{stem}.json"`.
- `main`: `parser.add_argument("--corpora-dir", action="append", type=Path,
  default=[], dest="corpora_dirs")`.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest test_build_site.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add build_site.py test_build_site.py
git commit -m "feat: publish per-day corpora alongside the site history"
```

---

### Task 6: Workflow — stored-corpus backfill resolution and carry-forward

**Files:**
- Modify: `.github/workflows/daily-briefing.yml`
- Test: `test_daily_workflow.py` (raw-YAML text assertions, same style as
  existing tests)

**Step 1: Write the failing tests**

```python
def test_backfill_resolves_stored_corpus_before_live_fetch(self):
    self.assertIn(
        "https://elanthus.github.io/news-briefing/corpora/$report_date.json",
        WORKFLOW)
    self.assertIn('elif (( days_ago <= 2 )); then', WORKFLOW)
    self.assertIn("no stored corpus and feeds no longer retain", WORKFLOW)

def test_every_run_carries_forward_stored_corpora(self):
    self.assertIn("for days_ago in $(seq 1 13); do", WORKFLOW)
    self.assertIn("--corpora-dir corpora", WORKFLOW)
```

Also update `test_manual_run_can_backfill_seven_completed_eastern_days` /
`test_preserves_dated_corpora_and_replaces_reports` if their exact-text
assertions overlap the edited lines.

**Step 2: Run tests to verify they fail**

Run: `python -m pytest test_daily_workflow.py -v`
Expected: new tests FAIL.

**Step 3: Implement**

In the generate step, replace the unconditional fetch with per-day
resolution (curl flags copied from the existing history download: `--fail
--location --max-time 30 --max-filesize 50000000 --remove-on-error --silent
--show-error`):

```bash
if curl <flags> \
  "https://elanthus.github.io/news-briefing/corpora/$report_date.json" \
  --output "$corpus"; then
  echo "Reusing stored corpus for $report_date"
elif (( days_ago <= 2 )); then
  if ! python fetch_news.py \
    --window-start "$window_start" \
    --window-end "$window_end" \
    --report-date "$report_date" \
    --output "$corpus"; then
    echo "::warning::Corpus fetch failed for $report_date"
  fi
else
  echo "::warning::Skipping $report_date: no stored corpus and feeds no longer retain this window"
  continue
fi
if [[ -f "$corpus" ]]; then
  <existing run_briefing.py block>
fi
<existing prepare_publication.py block>
```

Add a carry-forward step after "Download prior live history" (always, both
modes — otherwise a single-day deploy erases previously stored corpora):

```bash
latest_completed_date=$(TZ=America/New_York date --date="yesterday" +%F)
mkdir -p corpora
for days_ago in $(seq 1 13); do
  d=$(TZ=America/New_York date \
    --date="$latest_completed_date $days_ago days ago" +%F)
  [[ -f "corpora/$d.json" ]] && continue
  curl <flags> \
    "https://elanthus.github.io/news-briefing/corpora/$d.json" \
    --output "corpora/$d.json" || true
done
```

Append `--corpora-dir corpora` to the `build_site.py` invocation in the
"Build static archive" step (`args+=(--corpora-dir corpora)`).

**Step 4: Run tests to verify they pass**

Run: `python -m pytest test_daily_workflow.py -v`
Expected: PASS. Also run `python - <<'EOF'` with `yaml.safe_load` (or
`actionlint` if installed) to confirm the YAML still parses.

**Step 5: Commit**

```bash
git add .github/workflows/daily-briefing.yml test_daily_workflow.py
git commit -m "feat: backfill from stored corpora, cap live re-fetch at 3 days"
```

---

### Task 7: Docs, full verification, and branch finish

**Files:**
- Modify: `README.md` (architecture bullet list), `docs/design.md` (repair
  section)

**Step 1:** Document the new behavior: the evidence-swap repair (producer-
labeled `*(source excerpt)*` entries), the friendly corpus-health rendering,
and `site/corpora/` persistence with the 3-day live-fetch cap. Two or three
sentences each, in the existing sections — no new documents.

**Step 2:** Run the full suite: `python -m pytest -x -q`
Expected: all tests PASS.

**Step 3: Commit**

```bash
git add README.md docs/design.md
git commit -m "docs: describe evidence-swap repair and stored-corpus backfill"
```

**Step 4:** Use superpowers:finishing-a-development-branch — the repo merges
via PRs to `main` (see recent history), so the expected outcome is a PR from
`feat/portfolio-quality-pages`. After merge and deploy, trigger the
`Daily briefing` workflow manually with mode `backfill-7-days` once: the last
3 days regenerate from live fetches at full quality and their corpora start
accumulating; older degraded pages are replaced as stored corpora build up.
