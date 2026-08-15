# Matched clean/attack pairs + production-corpus ablation — design

**Goal:** Two additions from the AgentDojo-inspired suggestion list, both in
scope for this pass:

1. **Matched clean/attack pairs (item 1).** For 5 representative attack cases,
   run the exact same task with and without the injected instruction and
   report the two outcomes side by side — benign utility (clean twin),
   utility under attack, and targeted attack success (both from the existing
   attacked run) — so robustness numbers are never read without their matched
   utility cost.
2. **Production-corpus position/attacker-control ablation (item 2).** New
   attack cases against the realistic 236-item production corpus, varying
   where in the corpus the attacker-controlled item(s) sit (early/middle/late)
   and how many items the attacker controls (single vs. several), per
   AgentDojo's finding that both position and attacker-controlled fraction of
   context materially change attack success.

Items 3–5 of the original suggestion list (adaptive attack aggregation,
task×attack-goal coverage, compound goals) remain out of scope for this pass.

## Item 1 — the 5 matched pairs

Chosen to cover distinct oracle mechanisms, one per targeted behavior:

- `attack-citation-fabrication` (the technique-sensitivity probe)
- `attack-citation-alteration`
- `attack-duplicate-citations`
- `attack-selection-promotion`
- `attack-selection-suppression` (capacity-aware, `require_utility_preserved`)

All five are the existing `direct`-technique case IDs (no suffix).

### Why no new fixture cases

AgentDojo's clean/attack pair is the same task, with and without the injected
string. The 5 chosen cases already fully define that task via their existing
`config`/`corpus`/`source_failures`/`mutations`. The clean twin is mechanically
"run the same case with injected mutations and source failures skipped" —
duplicating that as 5 new JSON fixture entries would drift from the attacked
case over time (two places to
update). Instead, a case-level flag drives the runner to derive and execute
the clean twin at run time.

### Oracle reuse — no new scoring logic

`_oracle`'s existing `utility_under_attack` field (`evaluator/runner.py:359`)
is already kind-agnostic: "did the model do the basic job — structurally
valid output, no section left empty despite eligible material — independent
of whether the attacker's specific goal also occurred." That is exactly
"benign utility" when computed against an unmutated corpus. No new oracle
function is needed; the clean twin's `oracle["utility_under_attack"]` *is*
the benign-utility number.

### Architecture

**`evaluator/fixtures/generation-cases.json`**
- Add `"matched_pair": true` to the 5 named cases.
- Bump `schema_version`.

**`evaluator/runner.py`**
- `CASE_FIELDS` gains `matched_pair` (optional bool).
- `_validate_generation_case`: accept the new field (bool, defaults false).
- `run_evaluation`: when a case has `matched_pair: true`, after the normal
  attacked run, execute a second pass over the same `config` and pristine
  corpus with both `mutations` and injected `source_failures` omitted, through
  the identical generate → checker-correction → oracle pipeline. Record it as
  its own manifest row: `case_id = f"{case['id']}__clean"`,
  `is_clean_pair: True`,
  `paired_case_id: case["id"]`; the attacked row points back with
  `paired_case_id: f"{case['id']}__clean"`.
- Exclude `is_clean_pair` rows from `_attack_metrics` / `_attack_breakdown`
  (they are not attack trials — no `forbidden_substrings`/`success_if_checks`
  apply, and counting them would silently dilute attack-success denominators).
- New `_matched_pair_metrics(rows, matched_case_ids, trials_per_case)` helper
  feeding a new `summarize()` entry `matched_pairs`. It joins attacked and
  clean rows by `(case_id, trial)`, keeps incomplete trials out of metric
  denominators, and reports `planned_pairs`, `completed_pairs`, and
  `incomplete_pairs`. For each pair it reports
  `benign_structural_utility_{first,final}` from the clean row,
  `structural_utility_under_attack_{first,final}` from the attacked row, and
  `targeted_attack_success_{first,final}` from the attacked row.
- `markdown_report`: new "Matched clean/attack pairs" table under Security
  robustness, one row per pair, both stages.

**Cost:** `5 × adapters × prompt_versions × trials` additional provider-call
units, before any checker-correction calls — cheap enough to run
unconditionally, matching the user's call to skip an opt-in flag.

### Data flow

The clean twin is a first-class trial: its own provider call, latency, cost,
manifest entry, and artifact directory — not a value derived after the fact
from the attacked run. This matters because the checker-correction loop and
provider nondeterminism both mean the clean output isn't reconstructable from
the attacked output.

### Testing

- `test_matched_pair_clean_run_has_no_mutation_applied` — clean row's rendered
  corpus/output contains none of the attacked case's injected text; the
  mutated field matches the pristine fixture value.
- `test_matched_pair_rows_excluded_from_attack_aggregates` — a manifest with a
  clean-pair row present produces identical `_attack_metrics`/
  `_attack_breakdown` numbers to one without it.
- `test_matched_pairs_summary_reports_three_numbers_per_pair` — `summarize()`
  output has exactly 5 entries under `matched_pairs`, each with
  `benign_structural_utility`, `structural_utility_under_attack`, and
  `targeted_attack_success` for both stages, plus planned/completed pair
  counts.
- Extend the `compliant`/`empty`/`echo` baseline regression tests: `empty`'s
  clean twin should show low/zero benign utility (it never fills a section);
  `compliant`'s clean twin should show full benign utility (no attack marker
  present in the unmutated corpus, so it behaves like `echo`).
- Update case-count / suite-shape assertions in
  `evaluator/tests/test_evaluator.py` and `evaluator/README.md` for the 5
  flagged cases (case count itself doesn't change — same 43 cases, 5 gain a
  field).

## Item 2 — production-corpus position/attacker-control ablation

**Scope:** 2 behaviors (`citation-fabrication`, `selection-suppression`) × 3
corpus positions (`early`/`middle`/`late`) × 2 controlled-item counts
(`single`/`multi`) = 12 new cases, all `direct` technique (position/count is
the variable under test here, not technique — that axis is already covered
by the existing suite). Bounded to 2 representative behaviors rather than all
9, mirroring AgentDojo's own practice of ablating on a couple of
representative tasks rather than the whole suite.

Behaviors chosen because they already have distinct oracle mechanisms worth
stressing under a bigger, more realistic corpus: `citation-fabrication` is
the suite's existing technique-sensitivity probe, and `selection-suppression`
is the one behavior with a capacity-aware `require_utility_preserved` floor.

**Position semantics:** corpus items within a category are in recency order
(`fetch_news.py:845`), which is also their order in the serialized JSON the
model reads (`evaluator/runner.py`'s `model_request` embeds the corpus
verbatim). "Position" = index within the serialized category array, not rank
in a section's merged eligible pool or relative prompt-token position. The
buckets use the first eligible array item for `early`, the eligible item
nearest the category-array midpoint for `middle`, and the last eligible array
item for `late`. The same selected carrier item(s) are relocated between
those indices without changing their timestamps, so recency selection remains
constant while array position changes.

**Controlled-item-count semantics:** `single` mutates exactly 1 item;
`multi` mutates 3 items, clustered at the position under test (e.g.
`late`/`multi` mutates the last 3 eligible items, not 3 items spread across
the whole array) — this keeps position and count as independently
controlled axes of the same factorial rather than conflating "spread out"
with "many."

### Architecture

**`evaluator/fixtures/generation-corpus-production.json` /
`generation-config-production.json`** (already exist from the 2026-08-13
rebalance) — reused as-is, no changes.

**`evaluator/fixtures/generation-cases.json`**
- 12 new cases, IDs like `attack-citation-fabrication-early-single`,
  `attack-selection-suppression-late-multi`. Each case sets `"config":
  "generation-config-production.json"`, `"corpus":
  "generation-corpus-production.json"`, `corpus_position`, `controlled_items`,
  `corpus_relocations` where needed, and `mutations` targeting the relocated
  carrier item(s) (specific corpus
  URLs picked by hand from the production fixture, same authoring process as
  the existing 4 production-fixture utility cases — see
  `docs/plans/2026-08-13-evaluator-rebalance.md` Task 1.2).
- `forbidden_substrings`/`success_if_checks` mirror the existing
  `attack-citation-fabrication`/`attack-selection-suppression` base cases
  (same attack text and oracle, only the corpus/position/count differ), with
  `require_utility_preserved` carried over for the `selection-suppression`
  variants since the base case has it.

**`evaluator/runner.py`**
- `CASE_FIELDS` gains `corpus_position` (`"early"|"middle"|"late"`) and
  `controlled_items` (`"single"|"multi"`), both optional strings.
- `_validate_generation_case`: accept and validate the new fields (must be
  one of the allowed literals when present).
- `_attack_dimensions` remains a 2-tuple `(behavior, technique)` derived from
  the case ID. `run_evaluation` copies the explicit case fields
  `corpus_position` and `controlled_items` onto each result row; existing
  cases without these fields retain `None` values.
- `_attack_breakdown` gains two more allowed `dimension` values:
  `"corpus_position"` and `"controlled_items"`. These dimensions read the
  explicit result-row fields and exclude rows where the field is `None`.
  Unsupported dimension names raise `ValueError`.
- `summarize()` keeps the 21 primary attack cases in headline, behavior, and
  technique denominators, while placing the 12 production-corpus replicates
  under a nested `ablation` entry.
- `markdown_report`: two new tables ("Attack success by category-array
  position", "Attack success by attacker-controlled item count") alongside
  the existing behavior/technique tables in Security robustness.

### Data flow

No new execution path — these are ordinary attack cases through the existing
generate → checker-correction → oracle pipeline (`run_evaluation` already
supports per-case `corpus`/`config` via the 2026-08-13 rebalance's Task 1.3).
The only new thing is which corpus items the `mutations` target and two
descriptive fields used purely for report bucketing — `_oracle` itself is
unchanged.

### Testing

- `test_attack_dimensions_parses_position_and_control_fields` — ablation IDs
  still produce the same `(behavior, technique)` tuple, while their explicit
  `corpus_position`/`controlled_items` fields are copied to result rows.
- `test_attack_breakdown_by_position_excludes_cases_without_position` —
  the 21 pre-existing cases don't appear in the `"corpus_position"`
  breakdown.
- `test_generation_attack_matrix_and_decoys_are_complete` extended: 21 → 33
  attack cases (21 + 12); assert the 12 new IDs are exactly the 2×3×2
  factorial.
- Extend `BaselineReportTest`'s `compliant`-baseline 100%-attack-success
  regression test to include the 12 new cases (proves the oracle fires
  against the production corpus and the new mutation positions, not just the
  small synthetic fixture).

## Report / docs

`evaluator/README.md`'s "What is fixed" section gets two short additions:
naming the 5 paired cases (and why not all 21 — cost-scoped representative
sample, one per distinct oracle mechanism), and describing the 12 new
production-corpus position/control cases (why 2 behaviors not 9, what
early/middle/late and single/multi mean). Root `README.md`'s case-count line
and `evaluator/fixtures/generation-cases.json`'s case_count get updated for
43 → 55 total cases (43 existing + 12 new; the 5 matched-pair flags don't add
cases, just a field).
