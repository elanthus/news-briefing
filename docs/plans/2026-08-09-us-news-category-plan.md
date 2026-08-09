# US News Category Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cap US Politics at 3 topics, add a US News section capped at 4 topics fed by its own corpus category, and make "one topic, one section" a hard contract violation.

**Architecture:** The project has three layers that agree through one written contract. `fetch_news.py` decides what exists (deterministic, tested), `briefing-prompt.md` decides what gets said (model judgment), `eval_briefing.py` checks the second against the first, and `corpus_schema.py` is the contract binding all three. US News is added as a real corpus category with its own feeds so categorization stays in the deterministic layer; the once-only rule is enforced in the prompt and the checker rather than the fetcher, so the corpus keeps complete evidence and the model keeps the placement decision.

**Tech Stack:** Python 3.11+, stdlib only (no third-party runtime dependencies — this is a deliberate project property). `unittest`, `ruff` 0.14.2, `mypy` 1.14.1.

**Design doc:** `docs/plans/2026-08-09-us-news-category-design.md`

**Sequencing note:** Tasks 1–3 keep the whole suite green. Task 4 adds the new fixture pair alongside the old one (still green). Task 5 flips the eval contract, repoints tests at the new fixture, and deletes the old pair in a single commit — splitting it would leave a red `CommittedFixtureTest`, because the 2026-08-08 baseline cannot satisfy a contract that did not exist when it was generated.

---

### Task 1: Add `us_news` to the corpus contract

**Files:**
- Modify: `corpus_schema.py:25-28`
- Test: `test_corpus_schema.py`

**Step 1: Write the failing test**

Add to `test_corpus_schema.py`, inside `class CategoryTest`:

```python
    def test_us_news_is_a_declared_category(self):
        """US News is a corpus category, not a model-side split of us_politics."""
        self.assertIn("us_news", corpus_schema.CATEGORIES)

    def test_a_corpus_without_us_news_is_reported(self):
        c = corpus()
        del c["categories"]["us_news"]
        del c["processing"]["us_news"]
        self.assertTrue(only(validate_corpus(c), "categories should be exactly"))
```

The `corpus()` helper already builds categories from `corpus_schema.CATEGORIES`, so it needs no change.

**Step 2: Run test to verify it fails**

```bash
python3 -m unittest test_corpus_schema -v
```

Expected: FAIL — `'us_news' not found in ('us_politics', 'world', 'ai_tech', 'dev_community')`.

**Step 3: Write minimal implementation**

In `corpus_schema.py`:

```python
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 0  # corpora written before the field existed

CATEGORIES = ("us_politics", "us_news", "world", "ai_tech", "dev_community")
```

The version bump is required, not cosmetic: `validate_corpus` demands an exact category-set match, so a v1-era checker reading a v2 corpus would report `us_news` as a violation. `is_readable` already lets new code read old corpora, so bumping only closes the direction where a stale reader misreads a newer file.

**Step 4: Run tests to verify they pass**

```bash
python3 -m unittest test_corpus_schema -v
```

Expected: PASS, including the existing `VersionTest` cases (they are written against `SCHEMA_VERSION`, not a literal).

**Step 5: Commit**

```bash
git add corpus_schema.py test_corpus_schema.py && git commit -m "feat: declare us_news corpus category, schema v2"
```

---

### Task 2: Fetch US News feeds

**Files:**
- Modify: `fetch_news.py:53-76` (`RSS_FEEDS`), `fetch_news.py:533` (category literal in `main`)
- Test: `test_fetch_news.py`

**Step 1: Write the failing test**

Add to `test_fetch_news.py` (put it next to the other module-level constant tests, or in a new class at the end before `if __name__`):

```python
class FeedConfigurationTest(unittest.TestCase):
    """The fetcher's categories must match the contract, or the corpus it
    writes fails validation before it is ever read."""

    def test_every_declared_category_has_a_source(self):
        import corpus_schema
        sourced = set(fetch_news.RSS_FEEDS) | {"dev_community"}
        self.assertEqual(sourced, set(corpus_schema.CATEGORIES))

    def test_us_news_feeds_are_distinct_from_us_politics(self):
        """Overlapping outlets are the duplication risk this change creates;
        don't build it into the source list as well."""
        politics = {url for _, url in fetch_news.RSS_FEEDS["us_politics"]}
        news = {url for _, url in fetch_news.RSS_FEEDS["us_news"]}
        self.assertEqual(politics & news, set())
```

**Step 2: Run test to verify it fails**

```bash
python3 -m unittest test_fetch_news.FeedConfigurationTest -v
```

Expected: FAIL — `KeyError: 'us_news'`.

**Step 3: Write minimal implementation**

In `fetch_news.py`, add to `RSS_FEEDS` immediately after the `us_politics` block:

```python
    "us_news": [
        ("CBS News US", "https://www.cbsnews.com/latest/rss/us"),
        ("The Guardian US", "https://www.theguardian.com/us-news/rss"),
        ("PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/headlines"),
        ("NPR National", "https://feeds.npr.org/1003/rss.xml"),
    ],
```

All four were measured live over a 24-hour window: 26, 14, 10 and 5 items. That is roughly 45 against the 9 the category needs, so the fourth source is not there for volume — it is there so the category survives losing any two feeds. Rejected on measurement: NBC US (3), ABC US (1, behind a 301), CNN US (0 in-window; 17 undated), and USA Today, whose feed carries a DOCTYPE and is correctly refused by `parse_feed_xml`.

Do **not** add these sources to `SOURCE_RELEVANCE_FILTERS`. Those filters exist for The Verge, Ars, Wired and the GitHub Changelog because those feeds carry high off-topic volume; curated hard-news feeds do not, and over-filtering is the more expensive mistake — a dropped item cannot be ranked at all (see the comment at `fetch_news.py:439-453`).

In `main()`, replace the hand-maintained literal:

```python
        "categories": {"us_politics": [], "world": [], "ai_tech": [], "dev_community": []},
```

with one derived from the contract, so this list cannot drift again:

```python
        "categories": {name: [] for name in corpus_schema.CATEGORIES},
```

**Step 4: Run the full suite**

```bash
python3 -m unittest -v
```

Expected: `FeedConfigurationTest` passes. `MainFailureModeTest` still passes — it patches `RSS_FEEDS` to `{}` and asserts an empty-but-valid corpus, which the derived dict still produces.

**Step 5: Commit**

```bash
git add fetch_news.py test_fetch_news.py && git commit -m "feat: fetch US domestic news into the us_news category"
```

---

### Task 3: Update the briefing prompt

**Files:**
- Modify: `briefing-prompt.md:21-23` (consolidation rule), `:30-31` (US Politics slots), `:56` (exclusion log)

No test in this task — the prompt's contract is enforced by `eval_briefing.py` in Task 5 and by `PromptSafetyContractTest`, which this change does not touch.

**Step 1: Extend the consolidation rule to cross sections**

Replace the `CONSOLIDATION RULE` paragraph (line 23) with:

```markdown
CONSOLIDATION RULE: If multiple stories share a common theme (e.g., corporate layoffs across different companies, tariff actions across multiple countries), merge them into a single bullet with a brief summary of all instances. This applies across sections as well as within one: US News and US Politics draw on overlapping outlets, so the same event can arrive through both.

ONE PLACEMENT RULE: Report each topic exactly once in the whole briefing. A story that belongs to two sections goes in the one where it matters most — it is not repeated, and it does not appear in any exclusion log once it has been reported. Section boundary: elections, Congress, the administration, federal policy and courts-as-politics are US Politics; every other US-domestic story — disasters, crime, public health, business, education, local government — is US News.
```

**Step 2: Change the section slots**

Replace lines 30-34:

```markdown
## US Politics
[3 topics, ranked by impact, from the `us_politics` category]

## US News
[4 topics, ranked by impact, from the `us_news` category]

## World Events
[5 topics, ranked by impact, from the `world` category]
```

**Step 3: Add US News to the exclusion log**

In the `Excluded Topics` paragraph (line 56), change "For each of the 4 sections (US Politics, World Events, AI Dev Tools, AI Dev Practices)" to:

```markdown
For each of the 5 sections (US Politics, US News, World Events, AI Dev Tools, AI Dev Practices), list the 5 next most significant topics that didn't make the cut, with a one-sentence reason each (e.g., "lower immediate impact," "regional rather than national significance," "consolidated into topic #4," "empty summary — insufficient corpus content to evaluate"). A topic reported in another section is not an exclusion and must not be listed here. Include the 🔗 URL from the corpus item for each excluded topic, same as for included topics.
```

**Step 4: Verify the prompt still states its safety contract**

```bash
python3 -m unittest test_eval_briefing.PromptSafetyContractTest -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add briefing-prompt.md && git commit -m "feat: 3 US Politics slots, 4 US News slots, one-placement rule"
```

---

### Task 4: Generate the replacement fixture pair

**Files:**
- Create: `fixtures/corpus-2026-08-09.json`, `fixtures/briefing-2026-08-09.md`

The old pair stays in place through this task so the suite stays green; Task 5 deletes it.

**Step 1: Fetch a live corpus**

```bash
python3 fetch_news.py --hours 24 -o fixtures/corpus-2026-08-09.json
```

Expected: `Wrote N items (M fetch errors) to fixtures/corpus-2026-08-09.json` with a non-zero N and an exit status of 0. Reddit 429s are normal and are recorded in `errors` — note which sources failed, they must be named in the briefing's Corpus health section.

**Step 2: Confirm the new category actually filled**

```bash
python3 -c "import json; c=json.load(open('fixtures/corpus-2026-08-09.json')); print({k: len(v) for k, v in c['categories'].items()}); print(c['errors'])"
```

Expected: `us_news` holds well over the 9 items needed for 4 slots plus a 5-entry exclusion log — the four feeds measured ~45 in a 24-hour window.

If it is short, read `errors` before doing anything else, because the cause determines the fix:

- **A `us_news` source is listed in `errors`.** A timeout or a 429 is transient — re-run the fetch, which is the only case where re-running helps. A 403, a DNS failure or a `DOCTYPE` rejection is permanent: that feed needs replacing in `RSS_FEEDS`, and no number of retries will produce a usable fixture.
- **No errors and still thin.** Then the corpus genuinely cannot support 4 US News topics, and re-fetching will return the same window. Do not paper over it — `CommittedFixtureTest` asserts zero findings including warnings, so an underfilled section fails the suite by design. Report it: it is evidence the slot count is wrong for the available sources, which is a decision to take back to the design rather than absorb here.

**Step 3: Generate the briefing**

Follow `briefing-prompt.md` against `fixtures/corpus-2026-08-09.json` and write the result to `fixtures/briefing-2026-08-09.md`. Reproduce the header comment from the old fixture, updated for the new filename — it is what tells a reader this is a regression baseline rather than a golden answer.

Rank and summarize only what is in the corpus. Do not browse, do not open corpus URLs, do not fill gaps from memory.

**Step 4: Check it against its corpus**

```bash
python3 eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --strict
```

Expected at this point: `slots_overfilled` is **not** yet reported for US News (the checker still has the old `SECTIONS`), but `missing_section` for `US News` **is**. That is expected — the checker is updated in Task 5. What must be clean here: every link grounded, every topic and exclusion cited, nothing both included and excluded, and failed sources named. Fix the briefing, not the checker.

**Step 5: Commit**

```bash
git add fixtures/corpus-2026-08-09.json fixtures/briefing-2026-08-09.md && git commit -m "test: add 2026-08-09 reference corpus and briefing"
```

---

### Task 5: Enforce the new contract in the checker

**Files:**
- Modify: `eval_briefing.py:52-65` (`SECTIONS`, `EXCLUSION_SECTIONS`), `:278-287` (`check_no_repeated_topics`)
- Modify: `test_eval_briefing.py` (helpers, slot expectations, fixture paths), `test_fetch_news.py:243-250` (fixture paths)
- Delete: `fixtures/corpus-2026-08-08.json`, `fixtures/briefing-2026-08-08.md`

**Step 1: Update the test helpers**

In `test_eval_briefing.py`, add a US News category to `CORPUS`:

```python
        "us_politics": _items("p", 10),
        "us_news": _items("n", 10),
        "world": _items("w", 10),
```

and rebuild `briefing()` for the new shape:

```python
def briefing(politics=3, us_news=4, world=5, ai_news=4, tools=3, practices=3,
             exclusions=5, health="", extra_link=None):
```

```python
    parts = [
        "# Daily Briefing — August 9, 2026",
        "## US Politics", topics("Politics", politics, "p"),
        "## US News", topics("US news", us_news, "n"),
        "## World Events", topics("World", world, "w"),
        "## AI/Tech",
        "**AI News (4 slots)**", topics("AI", ai_news, "a"),
        "**AI Dev Tools (3 slots)**", topics("Tools", tools, "t"),
        "**AI Dev Practices (3 slots)**", topics("Practices", practices, "d"),
        "---",
        "### Excluded Topics (accountability log)",
        log("US Politics", "p", exclusions, 4),
        log("US News", "n", exclusions, 5),
        log("World Events", "w", exclusions, 6),
        log("AI Dev Tools", "t", exclusions, 4),
        log("AI Dev Practices", "d", exclusions, 4),
    ]
```

The `log()` start offsets matter: the body now uses `p1`–`p3` and `n1`–`n4`, so the logs must begin at `p4` and `n5` or a story lands in both places and trips `included_and_excluded`.

**Step 2: Fix the tests that hard-code the old shape**

Three existing tests reference items that are no longer in the body:

```python
    # StructureTest
    def test_exclusion_subheaders_do_not_reopen_top_level_sections(self):
        """`**US Politics**` inside the log must not re-enter the real section."""
        sections = parse_briefing(briefing())
        self.assertEqual(len(sections["US Politics"]["topics"]), 3)
        self.assertEqual(len(sections["Excluded Topics"]["excluded"]), 5)

    # DoubleListingTest — p5 is no longer a body link; p3 is
    def test_story_in_both_briefing_and_exclusion_log_is_an_error(self):
        text = briefing().replace("🔗 https://ex.com/p3", "🔗 https://ex.com/p4")
        self.assertIn("included_and_excluded", checks(evaluate(CORPUS, text), ERROR))
```

**Step 3: Write the failing tests for the new contract**

Replace `DoubleListingTest.test_same_link_cited_twice_in_body_is_a_warning` with:

```python
    def test_same_link_cited_twice_in_body_is_an_error(self):
        text = briefing().replace("🔗 https://ex.com/w2", "🔗 https://ex.com/w1")
        self.assertIn("repeated_topic", checks(evaluate(CORPUS, text), ERROR))

    def test_a_story_reported_in_two_sections_is_an_error(self):
        """US News and US Politics share outlets; this is the failure that
        overlap produces, and it costs the briefing one of its few slots."""
        text = briefing().replace("🔗 https://ex.com/n1", "🔗 https://ex.com/p1")
        self.assertIn("repeated_topic", checks(evaluate(CORPUS, text), ERROR))
```

and add to `SlotAllocationTest`:

```python
    def test_us_news_over_four_topics_is_an_error(self):
        findings = evaluate(CORPUS, briefing(us_news=5))
        self.assertIn("slots_overfilled", checks(findings, ERROR))

    def test_us_politics_over_three_topics_is_an_error(self):
        findings = evaluate(CORPUS, briefing(politics=4))
        self.assertIn("slots_overfilled", checks(findings, ERROR))
```

**Step 4: Run tests to verify they fail**

```bash
python3 -m unittest test_eval_briefing -v
```

Expected: FAIL — `repeated_topic` is not produced (the check still emits `repeated_link` at WARN), and neither slot test errors.

**Step 5: Write the implementation**

In `eval_briefing.py`:

```python
# section label -> reserved topic slots (None = not slot-constrained)
SECTIONS = {
    "US Politics": 3,
    "US News": 4,
    "World Events": 5,
    "AI News": 4,
    "AI Dev Tools": 3,
    "AI Dev Practices": 3,
}
```

```python
# Sections the prompt requires an exclusion log for (AI News is exempt).
EXCLUSION_SECTIONS = ("US Politics", "US News", "World Events",
                      "AI Dev Tools", "AI Dev Practices")
```

and turn the repeat check into a contract:

```python
def check_no_repeated_topics(sections: dict[str, Section]) -> list[Finding]:
    """A story is reported in exactly one section.

    This was a warning while every section drew on a disjoint corpus category.
    US News and US Politics draw on overlapping outlets, so a repeat is now
    both likely and expensive: it spends two of a small number of slots on one
    story and pushes a real one into the exclusion log.

    Only exact URL repeats are decidable here. The same event filed by two
    outlets under two URLs is the model's job, under the consolidation rule.
    """
    findings: list[Finding] = []
    included = [url for name, bucket in sections.items() if name != EXCLUDED
                for url in bucket["links"]]
    for url, count in Counter(included).items():
        if count > 1:
            findings.append(Finding(
                ERROR, "repeated_topic",
                f"topic reported {count} times across sections — {url}"))
    return findings
```

**Step 6: Repoint the fixture tests and delete the old pair**

In `test_eval_briefing.py` (`CommittedFixtureTest`, lines 262 and 287) and `test_fetch_news.py` (lines 244-245), change `2026-08-08` to `2026-08-09` in all four paths.

```bash
git rm fixtures/corpus-2026-08-08.json fixtures/briefing-2026-08-08.md
```

**Step 7: Verify the new fixture satisfies the new contract**

```bash
python3 eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --strict
```

Expected: `0 error(s), 0 warning(s)` and `Briefing is consistent with its corpus.` `CommittedFixtureTest` asserts zero findings, warnings included, so anything reported here must be fixed in the briefing before moving on.

**Step 8: Run the full suite**

```bash
python3 -m unittest -v
```

Expected: all pass except `test_readme_full_result_matches_reference_fixture`, which Task 6 fixes.

**Step 9: Commit**

```bash
git add -A && git commit -m "feat: enforce 3/4 US slots and one-placement as contract violations"
```

---

### Task 6: Update the README

**Files:**
- Modify: `README.md:25`, `:26`, `:44`, the quoted showcase block, `:222`, `:241`

**Step 1: Update the pipeline description**

Line 25 — add the new section to the list of what the prompt produces:

```markdown
2. **Rank & summarize (LLM).** [`briefing-prompt.md`](briefing-prompt.md) is the prompt an agent follows to turn that corpus into a ranked briefing (US Politics, US News, World Events, AI/Tech with fixed sub-category slots), plus an **excluded-topics log** so you can see what didn't make the cut and why.
```

Line 26 — add the repeat rule to the list of what the checker validates: "…slots must not be over-filled, a story can't be reported in two sections, a story can't be both included and excluded, and a degraded run must say so."

**Step 2: Replace the showcase block**

Line 44's caption must state the real numbers from the new run:

```markdown
Complete frozen reference result from a real run (`--hours 24`, 2026-08-09 — N items across 5 categories). The same result is stored unquoted in [`fixtures/briefing-2026-08-09.md`](fixtures/briefing-2026-08-09.md) for regression testing.
```

Then replace the whole quoted briefing between `<summary><b>Click to expand full briefing</b></summary>` and `</details>` with the new fixture, every line prefixed `> `, and the header comment stripped. `test_readme_full_result_matches_reference_fixture` reconstructs the fixture from those `> ` prefixes and re-runs the checker on it, so the block must be complete and exact.

**Step 3: Update the findings table and the customization note**

Line 222 — add the new violation to the ERROR examples: "…a section exceeding its reserved slots; a story reported in two sections; a degraded run reported as healthy". Remove the now-stale WARN example on line 223 if it mentions repeated links.

Line 241 — the customization section says which sources are keyword-filtered; confirm it still reads correctly now that `us_news` feeds pass through unfiltered.

**Step 4: Run the README test**

```bash
python3 -m unittest test_eval_briefing.CommittedFixtureTest -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md && git commit -m "docs: document US News section and the one-placement rule"
```

---

### Task 7: Full verification

**Step 1: Whole suite, lint, types**

```bash
python3 -m unittest -v && ruff check . && mypy
```

Expected: all tests pass, `All checks passed!`, and `Success: no issues found`.

**Step 2: End-to-end against live sources**

```bash
python3 fetch_news.py --hours 24 -o /tmp/smoke.json && python3 -c "import json; c=json.load(open('/tmp/smoke.json')); print({k: len(v) for k, v in c['categories'].items()}); print(c['errors'])"
```

Expected: exit status 0, `us_news` non-empty, no schema violations on stderr. This mirrors the `smoke` CI job, which is informational and never blocks a PR.

**Step 3: Confirm nothing still references the deleted fixture**

```bash
grep -rn "2026-08-08" --include="*.py" --include="*.md" . | grep -v docs/plans
```

Expected: no output. Design and plan docs legitimately mention the old date and are excluded.
