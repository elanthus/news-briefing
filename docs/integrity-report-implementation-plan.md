# Integrity report implementation plan

Status: implemented for PR [#211](https://github.com/elanthus/news-briefing/pull/211). Prepared October 1, 2026 against commit `293c2a56e4e08b6e4fcef071e767845865a3036e`. Recheck the PR and code before implementing.

The integrity report should let a maintainer quickly understand what code and models did after the initial briefing, which changes reached publication, and which issues remain unresolved. It must show the publication decision and actions before detailed check statistics. This plan changes reporting, public audit metadata, and tests; the existing selection, repair, and publication policies remain the behavioral contract.

## Reader contract

A reader should be able to identify these facts from the first screen:

- Whether a briefing was published, and whether it is the original or an accepted repair candidate.
- Whether deterministic validation passed and whether semantic review and confirmation completed.
- How many stories were actually changed, how many source items were removed, and which repair attempts failed, were skipped, or were rejected.
- Whether semantic flags remain unresolved and why the workflow retained the original.

The next section explains each action with its actor, subject, trigger, outcome, and reason. Details show actual text and citation changes once per story. Statistics, costs, corpus health, definitions, and artifact links follow.

The initial-briefing baseline is the first complete rendered prose candidate in the selected generation run, when that artifact is recorded and verified. Selection corrections before that point belong to a separately labeled generation-preparation phase. The briefing first accepted as `ready` is the semantic-review baseline. These are different boundaries; do not describe pre-generation selection bookkeeping as an edit to an existing briefing. If a baseline cannot be recovered, report that limitation explicitly.

## Existing implementation and evidence

| Source | Current responsibility |
| --- | --- |
| [`build_site.py`](../build_site.py), `_render_report`, `_render_semantic_audit`, `_verdict`, `STYLE` | Report HTML, verdicts, tables, and displayed differences. |
| [`agent_runner/jev_review.py`](../agent_runner/jev_review.py), `review_run` | Initial questions, isolated confirmations, coverage, call costs, timestamps, and stop reasons. |
| [`agent_runner/semantic_repairs.py`](../agent_runner/semantic_repairs.py), `daily_semantic_review`, `public_audit`, `load_public_audit` | Repair targeting, candidate generation, follow-up review, public projection, and artifact binding. |
| [`prepare_publication.py`](../prepare_publication.py), `_provenance`, `_extract_repair_actions`, `prepare_publication` | Selection of publication input and extraction of generation provenance and final-attempt actions. |
| [`publication_schema.py`](../publication_schema.py) | Strict bounded public metadata validation. |
| [`agent_runner/runner.py`](../agent_runner/runner.py) and checkpoint manifests | Recorded selection/prose attempts, corrections, deterministic actions, and promotions. |
| [`daily_publish.py`](../daily_publish.py) | Daily invocation and potential workflow identity propagation. |

Observed examples from published history on October 1:

- September 30: complete initial review, a confirmed citation finding, a failed repair, and unknown repair billing. PR #211 hides the finding's subject and displays `Repair review: not attempted` without explaining the repair failure.
- October 1: partial initial review, 767 of 915 initial questions returned, and two unconfirmed flags. The report displays `Repair review: not required`.
- Existing offline fixtures reproduce an applied citation-triggered repair that changes headline and summary, while the renderer suppresses both differences.
- A grouping-triggered source removal is displayed with the removed source's below-threshold citation judgment instead of identifying grouping as the removal cause.

Treat these as reproducible reporting cases, not calibrated evidence that Jev's judgments are correct. Build synthetic equivalents into tests rather than depending on live history or provider calls.

## Public data contract

Add one bounded, versioned `integrity` record to public entries. Use history schema 9 and continue reading schemas 7 and 8. Migrate older entries with `integrity: null`; continue preserving their existing `semantic_audit`, provenance, Markdown, and findings. Accept the known legacy sidecar shapes explicitly. Do not loosen exact-field validation globally.

Existing `semantic_audit` check records and generated prose remain the canonical public score/difference data. The new record contains missing operational facts and references those records; avoid copying complete topic prose or evidence into an additional ledger. Field names below are proposed and should be finalized once, before parallel implementation.

| Component | Required meaning |
| --- | --- |
| Publication decision | Original retained, repaired candidate applied, cleared candidate retained without applying, unpublished, or historical decision unknown; bounded reason codes where applicable. |
| Generation identity | Original selected provider/model, fallback position, prompt hash, and counters with explicit scope. Preserve this when a repair run supplies the final Markdown. |
| Repair identity | Actual repair provider/model and separately labeled repair prompt identity if recorded. Jev is the judge; it does not author the prose. |
| Phase records | Generation preparation, initial prose validation/correction, initial semantic review, isolated confirmation, code removal, model selection/prose repair, candidate validation, follow-up review, and publication verification, where recorded. |
| Coverage | Initial and follow-up question coverage separately; confirmation-required and confirmation-returned totals separately; omitted pairs, oversized questions, unavailable results, and bounded stop reason. |
| Action records | Stable local action ID, sequence/phase, actor, check references, position/source references, attempted action, outcome, and reason codes. Scope all subject references to a verified artifact/version. |
| Cost records | Separately scoped initial-review, repair-generation, and follow-up-review reported spend, with unknown billing identified per phase. Aggregate once; do not equate unknown cost with zero. |
| Workflow identity | Optional validated numeric GitHub Actions run identity. Construct its link from the configured repository; omit it when unavailable. |
| Corpus health details | Typed source/status/count records from the validated corpus, sufficient to explain each degradation. Historical missing details remain explicitly unavailable. |

Use fixed public messages for allowlisted codes. Preserve multiple simultaneous skip causes rather than inventing one exclusive cause. Useful causes include incomplete review, unknown billing, target limit exceeded, all citations would be removed, invalid repair candidate, provider failure, incomplete follow-up, follow-up flag in an affected position, and candidate mode without application. Never publish raw exception text, provider error bodies, prompts, feed titles/excerpts, or private filesystem paths.

Phase order must reflect recorded execution. Show timestamps only when available; sequence numbers do not imply measured durations. A reported action is distinct from a model call: code-preserved selection is not a paid model request. Mark generation repairs superseded by later corrections separately from actions bound to the published candidate. Include promotions as generation bookkeeping without counting them as repairs.

## Work packages

### A Define and validate reporting metadata

Owner: schema implementer. Main files: `publication_schema.py` and focused schema tests. Dependencies: none.

1. Finalize the `integrity` schema and share a small example plus parser interface with the other implementers.
2. Bound every list, string, identifier, count, timestamp, enum, and cross-reference. Preserve current URL, probability, topic, and input-size checks.
3. Validate internal consistency: applied publication requires applied actions and verified acceptance; completion cannot coexist with missing declared coverage; confirmation labels still match exact scores.
4. Represent missing historical information as unavailable. A zero must mean an observed zero, not an absent field.
5. Add meaningful invalid-shape, inconsistent-state, bound, and legacy tests. Avoid tests that merely repeat serialization implementation.

### B Record phase outcomes and bind them to artifacts

Implementation files: `agent_runner/jev_review.py`, `agent_runner/semantic_repairs.py`, `prepare_publication.py`, `daily_publish.py`, and their focused tests. Depends on A's agreed contract.

1. Preserve the original generation directory separately from the selected publication directory. Derive original generation provenance from the former and repair provenance from verified repair artifacts.
2. Extract safe generation action history and correction calls from recorded manifests. Verify any public subject context against hash-bound artifacts; preserve private superseded prose.
3. Record distinct skip/failure/rejection causes where decisions occur. Map exceptions to fixed classifications. Capture safe phase and coverage summaries even when candidate validation or post-review fails.
4. Record citation-removal cause as confirmed irrelevance, grouping subset selection, or both. Keep original source-index alignment. Preserve the existing requirement for confirmed citation-irrelevance removals; grouping selection remains governed by its current policy.
5. Derive initial and follow-up coverage and confirmation totals from their respective reports. Include the actual follow-up checks that prevent acceptance, including checks that were below threshold initially. Never fabricate a follow-up score for removed evidence or singleton grouping.
6. Produce explicit publication decisions from the verified selected artifact and application mode. `not_required` is valid only when complete applicable assessment establishes no eligible repair target. Partial assessment means eligibility is unresolved or action is withheld.
7. Bind new fields to existing verified manifests/reports and candidate hashes. Recompute or cross-check summaries; a structurally valid new string is insufficient evidence. An invalid audit must still retain the original and never authorize replacement. Expose safe audit-unavailable/verification-failed status when known.
8. Propagate optional workflow identity using code-owned configuration. Add typed corpus-health detail from validated inputs. Keep core runtime standard-library-only.

### C Render the action report and retain history

Owner: report implementer. Main files: `build_site.py`, `tests/site_test_build.py`. Depends on A's contract; use offline fixtures while B is being implemented.

1. Put publication decision, deterministic validation, semantic coverage, unresolved flags, actual published changes, and repair outcome at the top. Scope the deterministic finding count explicitly.
2. Render a compact action ledger before statistics. Include failed/skipped/rejected actions and subjects even when there is no new prose. Group related checks under one story/action rather than repeating text.
3. Select prose details by exact original/changed text differences, regardless of trigger category. Label applied text as published and rejected/candidate text as proposed. Show original and changed text once per story, with safe word-level highlighting using standard-library tools and escaped text.
4. Show removed source items with section/slot, short host/source labels, removal cause, and outcome. Distinguish article and HN discussion destinations associated with one source item. Do not count their two URLs as two source removals. Separate actual removals from proposals visually and in text.
5. Restore concise deterministic finding messages and safe context; include nonblocking advisory subjects and values. Preserve the privacy rules for rejected/blocked/no-result entries. Do not reprint the full briefing or accountability log.
6. Render initial and follow-up statistics separately. Use `Citation irrelevance probability`, define threshold direction, and distinguish confirmed initial targets from remaining follow-up flags. Display unavailable/not-in-scope categories explicitly.
7. Explain complete/partial, disputed/unconfirmed, candidate/applied, missing confirmation, and excerpt-only review in a compact definition disclosure. Make the footer consistent with the actual run.
8. Add deliberate table spacing, separators, tabular numerals, readable widths, accessible headers, and narrow-screen layouts. Wrap long content and give outcome text meaningful visual emphasis without depending on color alone.
9. Link full history/check data, the corpus manifest when available, and the validated originating workflow when available. Use stable report-local anchors for actions and story details. Present source degradation reasons in readable groups.
10. Write history schema 9, migrate 7/8 on read, preserve integrity metadata across merges/replacements/rebuilds, and render older entries with explicit uncertainty. Do not rerun models to populate historical metadata.

### D Integrate verify and document

Integration documentation: `README.md`, `docs/design.md`, `docs/jev-review.md`, `docs/publication-archive-contract.md`, and the stale per-run statement in `docs/evaluation-methodology.md`.

1. Trace all new public fields through production, sidecars, history merge, and rendering. Resolve contract disagreements before adding compatibility fallbacks.
2. Update documentation around the final implemented behavior and scope, including generation versus repair counts, report vocabulary, schema migration, public/private boundaries, and legacy limitations.
3. Render representative offline reports at desktop and narrow widths; inspect light/dark contrast and keyboard-accessible details. Fix clipping and scan-order problems. Keep screenshots and generated fixtures out of commits unless a deliberate documentation asset is needed.
4. Request Astra low review of the finalized contract before implementation proceeds across dependent packages, and a separate review of the integrated diff. Incorporate actionable findings through the Sol implementers, then request review of the fixes. Review must cover correctness, privacy, legacy behavior, and whether the rendered report answers the reader contract.
5. Complete repository checks and preflight, then push/review as directed in the session prompt. Keep documentation, code, and test evidence consistent with the final diff.

## Acceptance scenarios

Every scenario must preserve publication policy and make its outcome explicit. Tests use fake providers and offline artifacts.

| Scenario | Required report behavior |
| --- | --- |
| Complete review without eligible targets | Original retained; no repair needed established by complete assessment. |
| Partial initial questions or confirmations | Incomplete scope and missing counts visible; no false `not required` verdict. |
| Confirmed citation problem with failed repair | Story, source, trigger scores, failed phase, safe cause, and original-retained decision visible. |
| Skipped repair | All applicable causes and affected subjects visible, including target-limit and all-sources-removal cases. |
| Accepted citation-triggered repair changes prose | Source removal and actual headline/summary differences both visible once. |
| Accepted citation removal preserves prose | Removal visible; unchanged text omitted. |
| Grouping repair removes a source | Grouping cause identified; citation relevance score not presented as its removal justification. |
| Multiple triggers on one story | One story detail, all triggers, accurate check versus story counts. |
| Rejected candidate whose original trigger cleared | Candidate retained as proposal; the actual different follow-up blocker visible. |
| Clear candidate without application | Explicit candidate-mode outcome; original remains published. |
| Singleton or removed citation follow-up | Code-established scope change explained; no invented Jev score. |
| Original fallback model differs from repair model | Both identities and original fallback position preserved. |
| Historical repairs superseded by correction | Attempt scope and superseded outcome explain count/action-list differences. |
| Unknown billing or provider failure | Affected phase identified; known spend separated from unknown billing. |
| Review-required deterministic finding | Concise message and safe subject visible without reproducing the briefing. |
| Rejected blocked or no-result publication | Status-only privacy contract preserved; counts never imply acceptance. |
| Invalid or tampered audit | Original retained; no unverified actions, prose, scores, or destinations presented as applied. |
| Schemas 7/8 legacy audit and schema 9 rebuild | Content preserved, unknown history acknowledged, valid metadata retained. |
| Untrusted prose reasons and URLs | Escaping, allowlists, bounds, and artifact-binding checks preserved. |
| Degraded sources and absent workflow metadata | Reasons shown when verified; missing data not represented as healthy or fabricated links. |
| Desktop narrow screen and keyboard navigation | Outcome/action scan order works; tables and links do not make the page overflow. |

Run the mandatory gate:

```bash
uvx ruff@0.14.2 check . && uvx mypy@1.14.1 && uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator && python3 -S -m unittest && python3 -S -m unittest discover -s evaluator/tests && python3 -S -m evaluator checker
```

Because `build_site.py` and `prepare_publication.py` change, install `requirements-site.txt` in an isolated environment and run its Python with:

```bash
python3 -m unittest tests.site_test_build
```

The implementation follows [`AGENTS.md`](../AGENTS.md) and [`SECURITY.md`](../SECURITY.md). Do not change repair thresholds, provider budgets, topic limits, ranking, excerpt scope, or the one-round policy. Do not add paid or network-dependent tests.

## Implementation record

The final public contract is `integrity.version: 1` in history schema 9, validated
by `publication_schema.parse_integrity()`. Initial and canonical follow-up checks
remain in `semantic_audit`; actions reference them by stage and index. The action
check-reference bound is 128 so the reporting contract can represent confirmed
checks within the existing review-call budget. New artifact hashes remain bound
to exact public Markdown bytes through sidecar loading and history rebuilds.
Schemas 7/8 and legacy semantic envelopes remain readable with unavailable
integrity history. Superseded generation prose remains private; displayed change
counts explicitly compare with the ready semantic-review baseline.

Implementation packages used `gpt-6.1-sol` with medium reasoning, and independent
contract and integrated reviews used `gpt-6-astra` with low reasoning. Integrated
review findings were corrected and verified: published-original versus candidate
flag scope, precise undated/filtered corpus-health causes, non-target audit
outcome binding, and public Markdown digest verification. Offline fixtures cover
applied, failed, skipped, rejected, partial, candidate, legacy, deterministic
review-required, and status-only reports.
