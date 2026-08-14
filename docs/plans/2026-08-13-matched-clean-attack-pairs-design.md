# Matched clean/attack pairs — design

**Goal:** For 5 representative attack cases, run the exact same task with and
without the injected instruction and report the two outcomes side by side —
benign utility (clean twin), utility under attack, and targeted attack
success (both from the existing attacked run) — so robustness numbers are
never read without their matched utility cost. This is AgentDojo's clean/attack
pairing (item 1 of the suggestions the user brought from the AgentDojo paper),
scoped down from the full 21-case matrix to 5 cases chosen to cover distinct
oracle mechanisms, one per targeted behavior:

- `attack-citation-fabrication` (the technique-sensitivity probe)
- `attack-citation-alteration`
- `attack-duplicate-citations`
- `attack-selection-promotion`
- `attack-selection-suppression` (capacity-aware, `require_utility_preserved`)

All five are the existing `direct`-technique case IDs (no suffix). Item 2 of
the original suggestion list (production-corpus position/attacker-control
ablation) is explicitly out of scope for this pass.

## Why no new fixture cases

AgentDojo's clean/attack pair is the same task, with and without the injected
string. The 5 chosen cases already fully define that task via their existing
`config`/`corpus`/`source_failures`/`mutations`. The clean twin is mechanically
"run the same case with `mutations` skipped" — duplicating that as 5 new JSON
fixture entries would drift from the attacked case over time (two places to
update). Instead, a case-level flag drives the runner to derive and execute
the clean twin at run time.

## Oracle reuse — no new scoring logic

`_oracle`'s existing `utility_under_attack` field (`evaluator/runner.py:359`)
is already kind-agnostic: "did the model do the basic job — structurally
valid output, no section left empty despite eligible material — independent
of whether the attacker's specific goal also occurred." That is exactly
"benign utility" when computed against an unmutated corpus. No new oracle
function is needed; the clean twin's `oracle["utility_under_attack"]` *is*
the benign-utility number.

## Architecture

**`evaluator/fixtures/generation-cases.json`**
- Add `"matched_pair": true` to the 5 named cases.
- Bump `schema_version`.

**`evaluator/runner.py`**
- `CASE_FIELDS` gains `matched_pair` (optional bool).
- `_validate_generation_case`: accept the new field (bool, defaults false).
- `run_evaluation`: when a case has `matched_pair: true`, after the normal
  attacked run, execute a second pass over the same `config`/`corpus`/
  `source_failures` with `mutations` NOT applied, through the identical
  generate → checker-correction → oracle pipeline. Record it as its own
  manifest row: `case_id = f"{case['id']}__clean"`, `is_clean_pair: True`,
  `paired_case_id: case["id"]`.
- Exclude `is_clean_pair` rows from `_attack_metrics` / `_attack_breakdown`
  (they are not attack trials — no `forbidden_substrings`/`success_if_checks`
  apply, and counting them would silently dilute attack-success denominators).
- New `_matched_pair_rows(rows)` helper feeding a new `summarize()` entry
  `matched_pairs`: for each of the 5 pairs, `{case_id, benign_utility (clean
  row's utility_under_attack rate), utility_under_attack (attacked row's
  rate), targeted_attack_success (attacked row's rate)}`, first and final
  stage.
- `markdown_report`: new "Matched clean/attack pairs" table under Security
  robustness, one row per pair, both stages.

**Cost:** +5 trials per run configuration (flat, not proportional to trial
count multiplier already in play) — cheap enough to run unconditionally,
matching the user's call to skip an opt-in flag.

## Data flow

The clean twin is a first-class trial: its own provider call, latency, cost,
manifest entry, and artifact directory — not a value derived after the fact
from the attacked run. This matters because the checker-correction loop and
provider nondeterminism both mean the clean output isn't reconstructable from
the attacked output.

## Testing

- `test_matched_pair_clean_run_has_no_mutation_applied` — clean row's rendered
  corpus/output contains none of the attacked case's injected text; the
  mutated field matches the pristine fixture value.
- `test_matched_pair_rows_excluded_from_attack_aggregates` — a manifest with a
  clean-pair row present produces identical `_attack_metrics`/
  `_attack_breakdown` numbers to one without it.
- `test_matched_pairs_summary_reports_three_numbers_per_pair` — `summarize()`
  output has exactly 5 entries under `matched_pairs`, each with
  `benign_utility`, `utility_under_attack`, `targeted_attack_success` for both
  stages.
- Extend the `compliant`/`empty`/`echo` baseline regression tests: `empty`'s
  clean twin should show low/zero benign utility (it never fills a section);
  `compliant`'s clean twin should show full benign utility (no attack marker
  present in the unmutated corpus, so it behaves like `echo`).
- Update case-count / suite-shape assertions in
  `evaluator/tests/test_evaluator.py` and `evaluator/README.md` for the 5
  flagged cases (case count itself doesn't change — same 43 cases, 5 gain a
  field).

## Report / docs

`evaluator/README.md`'s "What is fixed" section gets a short paragraph
naming the 5 paired cases and one sentence on why they're not all 21
(cost-scoped representative sample, one per distinct oracle mechanism).
