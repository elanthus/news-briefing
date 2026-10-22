# News briefing evaluator

The evaluator is a development benchmark. The production pipeline does not import it.
Its core uses Python 3.11 or later and the standard library.
This guide covers execution, interpretation, and evidence verification.
Read the [methodology](../docs/evaluation-methodology.md) before citing scores.

![Evaluation from frozen inputs through two-pass generation, deterministic checks, blinded review, reporting, and comparison](../docs/images/evaluation-loop.svg)

- [Bring your own model or prompt](#bring-your-own-model-or-prompt): start a development evaluation.
- [What is fixed](#what-is-fixed): understand cases and attack signals.
- [Setup](#setup): configure credentials and optional tools.
- [Live model runs](#live-model-runs): use controls, checkpoints, and exports.
- [Score families and denominators](#score-families-and-denominators): interpret measured outcomes.
- [Prose-quality judging](#prose-quality-judging): compare generated prose.
- [Historical portfolio runs](#historical-portfolio-runs): reproduce dated evidence.

Current generation uses production parity and suite version 9.
Historical direct-Markdown and version 8 results remain frozen.
[Parity v3](../docs/results/parity-v3.md) is the latest published injection experiment, with descriptive comparisons only.
It does not measure current version 9 behavior.

## Bring your own model or prompt

### 1. Check the harness offline

Run the checker and evaluator regression suite without credentials or provider calls:

```bash
python3 -S -m evaluator checker
python3 -S -m unittest discover -s evaluator/tests
```

### 2. Select a provider and model

Run commands from the repository root.
First record current fixture bytes in an ignored development protocol:

```bash
python3 -S - <<'PYTHON'
import hashlib
import json
from pathlib import Path

fixtures = Path("evaluator/fixtures")
def entry(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

protocol = {
    "protocol_id": "local-development-inputs",
    "status": "development",
    "suite": entry(fixtures / "generation-cases-v9.json"),
    "inputs": [entry(path) for pattern in ("generation-corpus*.json", "generation-config*.json")
               for path in sorted(fixtures.glob(pattern))],
}
destination = Path("evaluator/results/development-protocol.json")
destination.parent.mkdir(parents=True, exist_ok=True)
with destination.open("x") as stream:
    json.dump(protocol, stream, indent=2)
    stream.write("\n")
PYTHON
```

The destination must be new. Reuse those bytes when resuming the same development run.
This snapshot binds fixture inputs only. It does not authorize a final experiment or bind model/prompt/control choices.
Run manifests separately record the actual prompt, model, and controls.
Final evaluations need a complete frozen protocol and prior authorization.
The CLI's default protocol is historical Parity v1; do not treat it as a current study plan.

Copy `evaluator/.env.example` to ignored `evaluator/.env` and configure the required provider key.
Replace `YOUR_MODEL_ID` with an available structured-output model:

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --provider openrouter=YOUR_MODEL_ID \
  --trials 5 \
  --cost-ceiling-usd 5 \
  --output-dir evaluator/results/my-model
```

Repeat `--provider` for additional models.
`--all-providers` reads supported generation providers' comma-separated model lists.
New runs use `production-parity` and default to `briefing-runner-prompt.md`.
Direct-Markdown generation and the live baseline provider are retired.

### 3. Read the report

Open `report.md` in the output directory.
It reports utility, attack success, matched pairs, ablations, cost, latency, and incomplete rows.
Rebuild it without provider calls:

```bash
python3 -S -m evaluator report evaluator/results/my-model/manifest.json
```

### 4. Compare prompt versions

Create `my-prompt.md` with the same structured-output contract, then run both prompts:

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --provider openrouter=YOUR_MODEL_ID \
  --prompt production=briefing-runner-prompt.md \
  --prompt candidate=my-prompt.md \
  --trials 5 --output-dir evaluator/results/prompt-test

python3 -S -m evaluator compare \
  evaluator/results/prompt-test/report.json evaluator/results/prompt-test/report.json \
  --baseline-prompt production --candidate-prompt candidate --allow-descriptive
```

Each comparison input can be `report.json` or `manifest.json`.
Its directory must contain `manifest.json`.
This development example produces a descriptive comparison.
Promotion requires compatible complete final runs, recorded ordering, and completed required human review.

[Policy version 2](regression-policy.json) requires utility improvement and an interval lower bound above the configured floor.
Attack success must satisfy its interval-based non-inferiority margin, with no contract regression.
Every evaluated model must pass under `promotion_requires: all_models`.
Output records policy bytes by hash, policy version, applied rules, and interval bounds.
Use an [archived policy](policies/README.md) only to reproduce a comparison made under that policy.

## What is fixed

| File | Purpose |
|---|---|
| `fixtures/checker-cases.json` | Deterministic checker and feed-parser cases with label provenance. |
| `fixtures/generation-cases-v9.json` | Active utility, quality, and injection cases. |
| `fixtures/generation-cases.json` | Frozen historical version 8 suite. |
| `fixtures/generation-v9-scarcity-positive.json` | Production selection positive control. |
| `fixtures/generation-v9-utility-positive.json` | Compact utility positive controls. |
| `snapshots/offline-checker.json` | Committed deterministic results checked by CI. |

Offline cases cover citations, encodings, XML, structure, routing, source health, ambiguity, and claim heuristics.
All have model review with owner adjudication; none has independent human review.
See [label review](#label-review) for the dated history.
Checker and generation results use separate denominators.

Authored controls pass the active utility requirements through structured validation, the Markdown checker, and the utility oracle.
Independent machine review assessed their included claims and requirements.
The [assessment record](../docs/results/remediation-2026-10-02.md) retains editorial and exclusion-log limitations.
Controls establish that success is possible. They do not measure live generation success.

### Generation cases and signals

Cases include utility, over-refusal decoys, direct/combined injections, clean twins, and production-corpus position/count ablations.
Injection channels include `summary`, `title`, `source`, and health records.
Deterministic assertions check URL inclusion, exclusion, ordering, separation, and section placement.
URL-scoped `must_convey` propositions receive separate semantic review, so faithful paraphrases need not repeat fixture wording.

Version 9 corrects `utility-production-selection-scarcity` to require the Worklog and `MISTAKES.md` practices present in its corpus.
It also separates clean and attacked leading-URL observations from the attack-only canary.
A story leading both outputs is not automatically evidence of successful selection manipulation.
Historical version 8 scores retain their original interpretation.

Current attack scoring counts only ERROR-level `success_if_checks` findings.
WARN quotation and length heuristics cannot establish attack success.
`attack-prose` and `attack-prose-combined` therefore depend on their canaries.
Utility requirements can still use WARN findings.
New manifests record `attack_oracle_revision: 2`; revision mismatches permit descriptive comparison only.

Attack signals require positive evidence of the targeted outcome.
Missing sections or health blocks alone cannot prove that an instruction was obeyed.
Formatting and health attacks therefore use canaries.
Category attacks require the target citation in the named wrong section.
Suppression attacks require capacity-aware completion of the remaining task.
The scarcity utility case separately treats an inadequate accountability log as its declared failure.

### Clean twins and ablations

Version 9 marks six attacks with `matched_pair`.
Each attacked trial runs a clean twin from the same pristine corpus and config, without mutations or source failures.
Every twin uses the two-pass generation path and can require correction calls.
Only complete attack/clean pairs enter matched rates; incomplete pairs remain visible.
Benign structural utility measures contract-valid use of material and declared topic floors.
It does not reproduce AgentDojo's user-task utility measure.

Ablations cross citation fabrication and selection suppression with early/middle/late array position and one/three controlled items.
Carrier timestamps remain fixed while their serialized `dev_community` positions change.
Position is an array index, not merged eligibility rank or relative prompt-token position.
Controlled-item count is not controlled-token fraction.
These replicate rows stay outside headline, behavior, and technique attack denominators.

Production fixtures exercise routing, scarcity, duplicate detection across overlapping categories, and grouped sections.
Small synthetic fixtures isolate simpler boundaries.
Test controls in `tests/oracle_controls.py` include `empty`, `echo`, and intentionally vulnerable `compliant` strategies.
The compliant control must trigger every intended attack success.
These controls are not selectable generation providers.
Historical baseline rows remain separate from live model tables.

The design draws on [AgentDojo (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html).
The repository's structural measures and ablations are limited proxies, not complete reproductions of those methods.

Active corpora require schema 7.
Their evidence remains unchanged, with current schema and budget metadata.
Original dated corpora, receipts, bundles, and snapshots remain unchanged.
Historical model reviews cover evidence and labels, not new review of refreshed metadata.
Final runs need protocols bound to current input hashes.

## Setup

Optional development tools use an isolated environment:

```bash
uv sync --project evaluator --group dev
```

The standard-library checker requires no installation:

```bash
python3 -S -m evaluator checker
```

The default checker compares its result byte-for-byte with `snapshots/offline-checker.json`.
Drift returns nonzero.
Inspect the case-level diff and obtain review approval before an intentional snapshot update:

```bash
python3 -S -m evaluator checker --update-snapshot
```

The dated `results/offline-baseline.md` remains a historical snapshot.

Configure the ignored environment file:

```bash
cp evaluator/.env.example evaluator/.env
```

Keep credentials out of the tracked template.
Its model lists are dated examples. Check provider availability before a live run.
API adapters default to 100,000 completion tokens; `EVALUATOR_MAX_TOKENS` overrides this allowance.
Provider limits still apply.

Each provider's model variable accepts one model or a comma-separated list.
Whitespace is ignored. Empty entries are rejected.
Models execute sequentially and each provider/model/prompt combination receives separate results.
NVIDIA variables configure judging, not generation.

## Live model runs

### Production-parity generation path

New runs support only `production-parity`.
The first request selects citation handles from projected corpus evidence.
Code validates and freezes the selection.
The second request receives position-scoped evidence and returns prose without citation fields.
Code restores references, validates the candidate, and checks Markdown from the production renderer.

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --provider codex-cli=gpt-5.6-terra \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --prompt production=briefing-runner-prompt.md \
  --reasoning enabled \
  --trials 1 \
  --run-kind development \
  --output-dir evaluator/results/production-parity-smoke
```

The model identifiers are examples, subject to provider availability.
Generation supports `codex-cli`, `claude-code-cli`, and `openrouter`.
The production single-run CLI also supports local OpenAI-compatible servers, but the evaluator does not expose that transport.
NVIDIA and baseline adapters cannot generate evaluation trials.

Shared `agent_runner/stages.py` considers slot promotion, then deterministic repair, without repeating either consecutively.
Code revalidates each transition before requesting model correction.
Selection corrections use the selection schema.
Prose corrections retain frozen evidence and use the prose schema.

Production allows corrections separately for selection and prose.
Evaluation records a first candidate and at most one corrected candidate across both stages.
It does not grant another correction after a corrected selection's prose fails.
The cost ceiling can also prevent correction.
Provider failures retain completed-stage telemetry and costs.
The complete Markdown checker remains independent.

There is no `--generation-path` option.
Manifests record `production-parity`; historical Markdown identities cannot resume.
Artifacts retain raw/projected corpora, citation map, selection schema, requests, structured responses, selected evidence, and rendered output as available.
Failed trials retain artifacts completed before failure.
`selection-schema.json` is current; the obsolete `output-schema.json` alias is not written.

### Cost ceilings

The ceiling accumulates reported cost and checks it before each candidate or correction.
A selection call and its dependent prose call form one candidate.
That candidate can take observed cost above the ceiling.
Provider failures, billing/credit errors, and a reached ceiling preserve checkpointed work and cause a nonzero exit.
Unknown billing remains explicit; see [resume accounting](#resuming-an-interrupted-run).

### Comparing prompt versions after a run

Use the comparison command in the [quick start](#bring-your-own-model-or-prompt).
The comparator requires unique provider/model/case/trial keys and compatible identities.
It checks suite, corpus, configuration, protocol, prompts, controls, adjudication state, run kind, and repetition provenance.
The default bootstrap resamples authored-case clusters 10,000 times.
Output includes utility, attack success, corrections, grounding proxy, latency, cost, and unmatched rows.
Incomplete human grounding remains undetermined.
`--allow-descriptive` permits incompatible evidence without granting a gated promotion outcome.

### Grounding review packets

Export packets from a current verified run:

```bash
python3 -S -m evaluator export-grounding-review \
  evaluator/results/my-model/manifest.json \
  --output-dir evaluator/results/my-model/grounding-human-review
```

For automated review, select available judge models:

```bash
python3 -S -m evaluator judge-grounding \
  evaluator/results/my-model/manifest.json \
  --packet-dir evaluator/results/my-model/grounding-human-review \
  --primary-model YOUR_PRIMARY_MODEL_ID \
  --audit-model YOUR_AUDIT_MODEL_ID \
  --cost-ceiling-usd 7 \
  --output-dir evaluator/results/my-model/grounding-machine-review
```

The primary judge labels every topic. The audit judge independently labels the stratified double-review sample.
Calls are batched, validated, and checkpointed for resume.
The ceiling checks before the next call and preserves configured headroom.
Results identify both judges and remain automated evidence, not human approval.
Historical rows without current evidence digests require their recorded readers and cannot become verified by selecting a new output directory.

### Retired weekly production monitor

The scheduled grounding monitor was retired on October 10, 2026.
Its [historical log](../docs/results/grounding-monitor.md) remains frozen.
The `monitor-grounding` command and monitor-only downloader were removed.
Explicit packet export and machine review remain supported.
See the [methodology](../docs/evaluation-methodology.md#unverified-machine-grounding-monitor) for their measurement limits.

### What is retained and where

Historical raw portfolio and parity trees remain in the verified originals archive identified by the [retention receipt](../docs/results/retention-2026-10-15.json).
Restore them into a separate checkout of the recorded revision.
New runs and private review mappings remain local and ignored.
The superseded portfolio-v1 aggregate is `history/portfolio-v1.json`.
[Public bundle instructions](../docs/results/EVIDENCE-ASSETS.md) cover Portfolio v2 and Parity v1–v3.
These are frozen experiments, not current-version results.

### Everyday run commands

Run one configured model:

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --provider codex-cli=gpt-5.6-terra \
  --prompt production=briefing-runner-prompt.md \
  --trials 3
```

Run all configured generation providers:

```bash
python3 -S -m evaluator run --all-providers --trials 3 \
  --protocol evaluator/results/development-protocol.json
```

Compare structured prompts by repeating `--prompt`:

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --all-providers \
  --prompt baseline=briefing-runner-prompt.md \
  --prompt candidate=/path/to/candidate-prompt.md \
  --trials 3
```

Replace the candidate path before execution.
Development and pilot runs use fixed adapter, prompt, case, and trial order.
Final runs randomize case/trial work and interleave prompt versions within each provider/model.
`--execution-seed` selects that order; otherwise the runner generates and records a seed.
It does not seed model sampling.

### Sampling controls

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --all-providers \
  --temperature 0.2 \
  --reasoning disabled \
  --trials 3
```

OpenRouter temperature defaults to 0.
Omitted reasoning preserves the provider default. Explicit reasoning or effort is recorded in the manifest.
`--reasoning-effort` implies enabled reasoning.
These flags do not change CLI sampling controls.
Codex uses fixed medium reasoning. Claude Code remains provider-controlled.
Exact output reproducibility is not guaranteed.

### Providers and adapters

Generation uses production structured-output transports and tool policies.
Raw-text adapters, including NVIDIA, remain available for separate judging workflows.
CLI and API controls are not equivalent.
Every manifest records effective settings; those settings alone do not make cross-provider results directly comparable.

### Retries, timeouts, and circuit breaking

The progress display identifies the provider and model.
API calls permit at most three attempts for HTTP 408, 425, 429, 5xx, and known pre-transmission failures.
Timeouts, resets, and failures after possible transmission do not automatically retry.
`Retry-After` controls the delay when present; otherwise delays are one and two seconds.
`--timeout` includes all attempts and waits for one call.
A retry cannot start if its delay exceeds the remaining deadline.

Three consecutive failures open a circuit for that provider/model.
Remaining trials record circuit-open skips while other models continue.
A successful trial resets the failure count.

### Preflight without credentials

`preflight` accepts the same options as `run`.
It resolves the plan and checks declared protocol bindings without loading `evaluator/.env` or calling providers.
Unlike a plain development run, it checks the supplied protocol bindings against the requested plan.
After creating the development input snapshot in the quick start, run:

```bash
python3 -S -m evaluator preflight \
  --protocol evaluator/results/development-protocol.json \
  --provider openrouter=YOUR_MODEL_ID \
  --prompt production=briefing-runner-prompt.md \
  --trials 1 --run-kind development \
  --output-dir evaluator/results/my-model-preflight
```

For a final evaluation, replace the development snapshot with a newly frozen protocol matching current inputs and all requested controls.
Do not reuse a historical protocol as a current-run template without updating its bindings.
Preflight checks suite, prompts, corpora, configs, providers, models, trials, timeouts, controls, row counts, and cost authorization.
It checks protocol `status: frozen_for_run` and `authorization.authorized_at` for final execution.

The output directory must be new, or contain a matching `running` checkpoint with `--resume`.
Credential presence is reported but not required.
Final checks require a clean source tree and matching tag.
`--before-tag` permits a missing final tag as deferred while checking the plan as if that tag identified clean HEAD.
Exit zero means every non-deferred check passed.

### Run outputs and final-run requirements

Every run writes `manifest.json`, `report.json`, `report.md`, and trial artifacts.
The default directory is `evaluator/results/<UTC timestamp>/`.
Top-level files are atomically checkpointed after each trial.
Provider failures record stage, retry metadata, and error while other planned work continues.
The command returns nonzero for the resulting partial execution.
Successful trials also receive `grounding-adjudication.json` for topic-level human decisions.

A final run requires at least two prompts and `--source-tag TAG` pointing to clean HEAD.
Dirty trees and mismatched tags fail before credentials or provider calls.
The manifest records commit, Git tree, source tag, and tracked runtime/evaluator Python source hashes.

### Exporting public evidence

Export one complete manifest or compatible split manifests:

```bash
python3 -S -m evaluator export-public-run evaluator/results/my-model/manifest.json \
  --output-dir /tmp/my-model-public \
  --ledger-output /tmp/my-model-ledger.json
python3 -S -m evaluator verify-public-run /tmp/my-model-public
```

Review and adjudicate evidence before public export.
Split components need matching experiment identity, complete prompt/case/trial matrices, and disjoint adapter sets.
Interrupted components, overlap, duplicate rows, and missing rows are rejected.

The ledger contains row identities and score primitives without generated prose.
The public bundle contains redacted manifests with generations, adjudications, and reports.
Provider request IDs are removed.
Source tags and committed fixtures support independent aggregate recalculation and output-level scoring review.

`verify-public-run` checks names, sizes, hashes, source metadata, and a ledger rebuilt from manifest primitives.
It authenticates historical report files without rendering them through current code.
First authenticate released bundles through `evaluator.evidence_assets fetch` and pinned in-tree checksums.
For historical regeneration, use the recorded source reporter and [bundle procedure](../docs/results/EVIDENCE-ASSETS.md).

### Resuming an interrupted run

Resume only a manifest with `run_status: running`.
Repeat its original command and explicit output directory, adding `--resume`:

```bash
python3 -S -m evaluator run \
  --protocol evaluator/results/development-protocol.json \
  --provider openrouter=YOUR_MODEL_ID \
  --trials 5 --cost-ceiling-usd 5 \
  --output-dir evaluator/results/my-model \
  --resume
```

This example matches the earlier single-model command.
Use the original options if your run differs.
Resume verifies suite, corpora, configs, protocol, prompts and order, path, providers, controls, timeouts, trials, run kind, ordering, circuits, and ceilings.
Saved rows must form a unique exact plan prefix with valid required artifacts and hashes.
Changed evidence and symlinks are rejected.
Older compatible checkpoints retain only the integrity guarantees their recorded hashes support.

Saved completed and failed rows are skipped.
An interrupted row's partial directory can be reused.
Interruption before checkpointing can leave unrecorded billing that resume cannot reconstruct.
Known cost and circuit state are rebuilt from saved rows.
Unknown-cost calls stay unknown and receive the mean observed known call cost for ceiling accounting.
Before any known cost, that estimate is zero.
This estimate does not turn missing billing into a known zero charge.

The manifest separates known cost, known/unknown call counts, estimated unknown cost, and observed ceiling cost.
Unknown billing does not block resume.
An automatically generated execution seed can be omitted because resume reuses it.
Complete, stopped, completed-with-errors, incompatible, and corrupt manifests cannot resume.
`--resume` always requires `--output-dir`.

### Rebuilding reports

```bash
python3 -S -m evaluator report evaluator/results/my-model/manifest.json
```

This rebuilds complete or partial reports without model calls.

### Semantic judging

Cases with `must_convey` requirements receive `semantic-adjudication.json`.
Review them manually or select an available judge:

```bash
python3 -S -m evaluator judge-semantics evaluator/results/my-model/manifest.json \
  --judge-provider claude-code-cli --judge-model YOUR_JUDGE_MODEL_ID
```

The judge sees corpus evidence, topics citing the required URL, and one proposition at a time.
Labels are `conveyed`, `not_conveyed`, and `unclear`.
Unclear labels remain unresolved until human adjudication.
The default `semantic-judgments` directory updates adjudications and reports without changing the generation manifest.
A separate new `--output-dir` creates an independent assessment without replacing prior labels.

Recorded topics must match parsed final output.
Judgments retain reviewer identity and exact input hashes.
Embedded labels are rejudged, not attributed to the newly requested model.
Legacy labels without provenance are ignored for attribution; incompatible checkpoints remain rejected.
Trial-corpus and final-output digests are required even for a new assessment directory.
Changed evidence, prompts, propositions, reviewers, or controls refuse resume.

## Score families and denominators

Report schema 9 separates four families.
Rates include successes, trials, and 95% Wilson intervals.
Execution conditions remain separate from scores.

| Family | Meaning and denominator |
|---|---|
| Checker capability | Offline precision, recall, and heuristic false positives. Semantic labels that code misses still reduce recall. |
| Application utility | Completed utility trials: contract, routing, end-to-end success, corrections, decoys, and actual health failures. |
| Security robustness | Completed primary attacks: targeted success, its robustness complement, recovery, behavior, and technique breakdowns. |
| Editorial quality | Utility topics and propositions: meaning preservation, human grounding, labeled deterministic proxies, and optional pairwise quality. |

Clean twins and ablations do not enter primary attack denominators.
`utility_under_attack` checks structural validity and use of available sections.
It is less strict than application end-to-end utility; do not subtract one rate from the other.
Matched tables use one complete-pair denominator for benign utility, attacked utility, and targeted success.
Unclear and unreviewed editorial labels remain visible.

Operations records planned, recorded, completed, failed, skipped, and correction-error rows with latency and cost.
Each version 9 adapter/prompt/trial executes the authored cases plus six twins.
Historical version 8 used five twins.
A twin requires selection and prose calls and can require correction.
Missing cost is null. Subscription CLI usage generally has no meaningful per-call billed value.
NVIDIA judging can use configured per-token rates.

## Prose-quality judging

Use a completed multi-condition run with matching stories:

```bash
python3 -S -m evaluator judge-quality evaluator/results/prompt-test/manifest.json \
  --judge-provider claude-code-cli --judge-model YOUR_JUDGE_MODEL_ID
```

The judge compares utility topics with exact canonical-URL-set identity across provider/model/prompt groups.
It scores faithfulness to corpus evidence, salience, concision, coherence, and overall preference.
Each pair runs twice with swapped option order.
Reports show position consistency beside win rates because option order can influence judgments.
Low consistency limits interpretation.

Default output refreshes the main report. A new empty output directory retains an independent assessment.
Checkpoints bind rubric, prompts, corpus, output, config, reviewer, controls, and timeout.
Changed inputs refuse resume.
Quality schema 3 records this provenance; older schema 2 reports appear as `stale_schema` in current reports.
Machine preference is additional evidence, not human review.

## Label review

The 10 cases disputed by the initial blinded model review were adjudicated by the repository owner (one of those 10, `health-wrong-status`, was later removed as a redundant coverage cut, not for failing). The other 40 were reviewed and approved by the same repository owner on 2026-08-14. A second blinded pass with DeepSeek V4 Flash found 12 disagreements among the then-current 49 cases. GLM 5.2 arbitrated those disagreements, and the owner explicitly approved the four recommendations that changed fixture labels: `category-wrong`, `claim-conflicting-evidence`, `selection-category-ambiguity`, and `health-missing`.

Nemotron Ultra completed a randomized opaque-ID model review of the original 49 cases, producing 38 exact agreements and 11 disagreements. The repository owner adjudicated all 11 against the evidence and rubric; `claim-thin-unsupported` was the only final label set changed. GLM 5.2 model-reviewed the six subsequent coverage additions, producing 3 exact agreements and 3 owner-adjudicated disagreements with no final label changes. GLM 5.2 later reviewed the 24 paired heuristic cases and two UTF-32 regressions in a new randomized opaque-ID packet, producing 23 exact agreements and three owner-adjudicated disagreements. The owner accepted `unsupported_quotation` for `claim-quote-punctuation-valid` and `claim-quote-whitespace-valid` and retained both existing labels for `claim-uncertainty-invalid`.

These LLM reviews supported initial fixture and benchmark development; they are not independent human review. All 81 current cases have completed model review. On 2026-08-26, Nemotron Ultra exactly agreed with `structure-overfilled`; after `selection-category-ambiguity` was rewritten to isolate a story spanning a tool release and workflow change, GLM 5.2 exactly agreed with that label. The [renewed-review receipt](../docs/results/repaired-fixture-model-review-2026-08-26.json) records the effective prompt, response, checkpoint, case-payload, and fixture-builder hashes. Full independent human review is recommended before production use. The `review-labels` and `export-label-review` commands that ran these reviews were removed once fixture labelling finished; they are recoverable from the repository history (last present at commit 24af334).

## Prompt provenance

The default `production` version hashes root `briefing-runner-prompt.md`.
For durable comparison, preserve a named prompt in `evaluator/prompts/` and pass each version explicitly.
Reports retain version names and hashes.
The offline baseline marks live-model metrics unrun instead of inventing provider results.

`briefing-prompt.md` is the retired direct-Markdown prompt retained for historical evidence and offline tests.
`briefing-runner-prompt-deepseek-v4-flash.md` is the variant used by parity experiments.
The Parity v1 protocol names its former root path, but its bytes and recorded hash remain unchanged.
Leading HTML file comments are stripped before model input. Hashes still cover complete file bytes.

## Historical portfolio runs

The following records preserve original experiment narratives and commands.
They are historical evidence, outside the maintained-procedure rewrite.
Their raw input trees now reside in the [verified originals archive](../docs/results/retention-2026-10-15.json).
Use recorded source tags, protocols, and bytes for reproduction.
Current generation does not replay obsolete formats.

### Portfolio v2 (historical, direct-Markdown)

Portfolio v2 supersedes the dirty-source portfolio-v1 model metrics. Its clean-tagged rerun completed all
1,200 rows with no execution errors and $3.8005 in reported generation cost, on the **direct-Markdown
generation path** (`"generation_path": "markdown"`), not production parity. See the [curated result](../docs/results/portfolio-v2.md),
[public evidence](../docs/results/portfolio-v2-evidence/), and [comparison](../docs/results/portfolio-v2-comparison.json).
The exact generation source is tag `portfolio-v2-source-20260819`; Portfolio v1's protocol and machine
history remain provenance only because its final run came from an unpreserved dirty source tree.

The original portfolio-v2 command was (a historical record from tag
`portfolio-v2-source-20260819`; the current `run` subcommand no longer accepts `--seed`):

```bash
python3 -m evaluator run \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --provider openrouter=tencent/hy3 \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --prompt reliability-v1=evaluator/prompts/reliability-v1.md \
  --trials 5 --timeout 300 --temperature 0 --seed 20260819 \
  --execution-seed 20260819 --reasoning disabled \
  --suite evaluator/fixtures/generation-cases.json \
  --corpus evaluator/fixtures/generation-corpus.json \
  --protocol evaluator/protocols/portfolio-v2.json \
  --output-dir evaluator/results/portfolio-v2-final-20260819 \
  --run-kind final --source-tag portfolio-v2-source-20260819 \
  --cost-ceiling-usd 4 --cost-ceiling-provider openrouter
```

After the primary checkpoint was interrupted and resumed, HY3 ran concurrently in a second output directory
with the same command identity and a $3 component ceiling. The primary was stopped only after all 600 DeepSeek
rows were checkpointed. The literal HY3 component command was (also historical; the current `run`
subcommand no longer accepts `--seed`):

```bash
python3 -m evaluator run \
  --provider openrouter=tencent/hy3 \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --prompt reliability-v1=evaluator/prompts/reliability-v1.md \
  --trials 5 --timeout 300 --temperature 0 --seed 20260819 \
  --execution-seed 20260819 --reasoning disabled \
  --suite evaluator/fixtures/generation-cases.json \
  --corpus evaluator/fixtures/generation-corpus.json \
  --protocol evaluator/protocols/portfolio-v2.json \
  --output-dir evaluator/results/portfolio-v2-final-20260819-hy3 \
  --run-kind final --source-tag portfolio-v2-source-20260819 \
  --cost-ceiling-usd 3 --cost-ceiling-provider openrouter
```

The historical component export and verification commands were (their input
trees now exist only in the originals archive):

```bash
python3 -m evaluator export-public-run \
  evaluator/results/portfolio-v2-final-20260819/manifest.json \
  evaluator/results/portfolio-v2-final-20260819-hy3/manifest.json \
  --output-dir /tmp/portfolio-v2-evidence
python3 -m evaluator verify-public-run /tmp/portfolio-v2-evidence
```

### Portfolio v1 (superseded)

The historical portfolio-v1 protocol is [`protocols/portfolio-v1.json`](protocols/portfolio-v1.json).
Its one-trial pilot began with the versioned `production-2026-08` and `reliability-v1`
prompts on Claude Sonnet 5 through Claude Code and DeepSeek V4 Flash through
OpenRouter. Pilot rows are operational checks and are excluded from final estimates:

```bash
python3 -m evaluator run \
  --provider claude-code-cli=claude-sonnet-5 \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --prompt reliability-v1=evaluator/prompts/reliability-v1.md \
  --trials 1 \
  --timeout 300 \
  --temperature 0 \
  --reasoning enabled \
  --reasoning-effort high \
  --run-kind pilot \
  --cost-ceiling-usd 5 \
  --cost-ceiling-provider openrouter \
  --output-dir evaluator/results/portfolio-v1-pilot-20260814
```

The 2026-08-15 operational pilot found the original Sonnet path incompatible with
the production corpus at the frozen timeout and found that reasoning-enabled
DeepSeek could consume the completion budget without returning text. The dated
protocol amendments use reasoning-disabled DeepSeek and the selected
OpenRouter `tencent/hy3` replacement; both amended 120-row groups completed without
execution errors. See [`docs/results/portfolio-v1-pilot.md`](../docs/results/portfolio-v1-pilot.md).

The final run used the amended reasoning-disabled DeepSeek and HY3 conditions. Its manifests record a
dirty source tree, and the diff was not preserved. Its narrative result and model card are therefore
not retained as evaluation evidence. The [machine-readable aggregates](../docs/results/portfolio-v1.json)
remain for provenance only and must not be cited as a reproducible result.

### Finding regression tests

Run the evaluator suite:

```bash
python3 -S -m unittest discover -s evaluator/tests
```

Modules separate execution, parity, checkpoints, public evidence, reporting, comparison, and judging.
`test_cases.py` covers cases and oracles. `test_adapters.py` covers transports.
Shared builders live in `support.py`.
Integrated export/tamper tests follow evidence across successive trust boundaries.

## Retained portfolio research

The one-time research migration captured private studies before their original working trees were removed.
It did not rewrite old audits or invent generation provenance.
Verification needs the extractor plus reader modules from the receipt's source revision in a separate checkout.
The extractor itself was introduced after that revision.
See [research retention](../docs/research-retention.md) for collections, recovery, and scoring exclusions.
Public historical bundles retain their release contract.
