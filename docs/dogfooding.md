# Dogfooding log

This is the operating record for news-briefing. It records the first run of the day, including degraded runs, rather than keeping only successful reruns.

It is long on purpose — it is provenance, not a tutorial. If you are reading three entries, read these:

- [2026-08-09 — the run behind the committed reference pair](#2026-08-09--the-run-behind-the-committed-reference-pair): the model cited an article that was never fetched, the checker caught it, the correction loop replaced it. This is the run the project's design argument comes from.
- [2026-08-18 — Claude Code Sonnet 5 failed dogfood run](#2026-08-18--claude-code-sonnet-5-failed-dogfood-run): a deny-all tool rule also blocked the structured-output mechanism the schema needed. An adapter-policy conflict that only a live run surfaces.
- [2026-08-15 — OpenRouter Tencent Hy3 dogfood run](#2026-08-15--openrouter-tencent-hy3-dogfood-run): the same corpus run twice, reasoning disabled and then enabled, so the difference is attributable to the setting rather than to a different day's news.

The rest is the daily record, newest first.

For a complete daily run, record:

- the agent and execution environment;
- the prompt version (a content hash or immutable source revision, plus any version name);
- corpus item counts by category and elapsed fetch time;
- every failed source;
- the checker's first result;
- any correction made after checking; and
- the final checker result.

Generated corpora and briefings are archived per day under [`docs/runs/<date>/`](runs/) (corpus, briefing, and the `briefing-config.json` snapshot used), so each entry below can be re-derived instead of taken on trust. The frozen regression pair in `fixtures/` is separate: it is the fixed example the test suite pins to, and daily runs do not update it.

Runs recorded before the multi-axis outcome model use the historical `PASS`/`WARN`/`ERROR` final labels and `failed` workflow wording. New runs record publication disposition (`ready`, `review_required`, `rejected`, or `no_result`) separately from protocol, contract, evidence, and coverage. Historical entries remain unchanged so their archived manifests and prose continue to agree.

Prompt versions for runs before 2026-08-16 were not recorded at run time. The historical entries below now identify the committed `briefing-prompt.md` version inferred from the corpus generation time, prompt-change history, and the parent of the contemporaneous run or log commit. These retroactive values identify the most strongly supported repository prompt bytes, not the complete request delivered to the model: they cannot rule out uncommitted changes, recover provider or CLI system instructions, or reconstruct missing first-output and correction-request bytes.

## Pre-launch verification

### 2026-08-09 — live fetch and reference check

- Environment: Python 3.14.6, local macOS checkout.
- Live fetch: 24.1 seconds; 180 items — 27 US politics, 53 US news, 43 world, 7 AI/tech, and 50 developer-community.
- Source failures: `r/LocalLLaMA` and `r/cursor` returned HTTP 429. The fetch completed successfully and preserved both failures in `corpus.errors`.
- Live processing: 10 AI/tech items and 3 developer-community items failed the relevance filter; 2 US-news duplicates and 1 US-news item over the per-source cap were dropped. All processing counters reconciled.
- Reference checker: 0 errors, 0 warnings against `fixtures/corpus-2026-08-09.json` and `fixtures/briefing-2026-08-09.md`.
- This is a fetch-only check, not a complete run: no model step was involved. It is also a *different* fetch from the one recorded below, an hour or so earlier in the day — same window length, different contents, which is what live retrieval looks like.

## Daily runs

### 2026-08-18 — Claude Code Sonnet 5 failed dogfood run

The first complete-run attempt of the day stopped during the initial model call, before a schema-valid briefing or checker result existed. The failed run is preserved under [`docs/runs/2026-08-18/`](runs/2026-08-18/) rather than being replaced by a cleaner rerun. Its manifest, closed corpus, exact request and schema, source/configuration snapshots, and append-only trace are archived there.

- Agent and execution environment: OpenAI Codex desktop agent on macOS 26.5.2 with Python 3.14.6, using Claude Code CLI 2.1.224 and exact model identifier `claude-sonnet-5`.
- Provider boundary: the adapter invoked Claude Code with `--safe-mode`, no tools, a deny-all tool rule, disabled slash commands, no session persistence, and the runner's JSON schema. The model had no browsing or external retrieval path.
- Prompt version: [`briefing-runner-prompt.md`](../briefing-runner-prompt.md) SHA-256 `745c9dda04decb2f984916704f84ab4a20707a9e78e979af2f5814f25a4c488c`; repository commit `ab53f60`.
- Corpus window: 2026-08-17 17:09:08 UTC → 2026-08-18 17:09:08 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 196 items — 34 US politics, 60 US news, 53 world, 18 AI/tech, and 31 developer-community. Live fetch time: 24.642 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but had zero recognized entries; `r/ClaudeCode`, `r/LocalLLaMA`, and `r/cursor` returned HTTP 429.
- Processing: 33 AI/tech items failed the relevance filter, one US-news item exceeded the per-source cap, and the US-news category cap dropped 17 items. No duplicate, field-budget, source-budget, or global-budget drops occurred. Eighty-five summaries were truncated at the configured field limit; all counters reconcile from 247 fetched items to 196 retained items.
- Provider failure: after 281.623 seconds, Claude Code returned a successful outer result but no structured output and an empty text result. The adapter failed closed with `claude-code-cli returned invalid JSON: Expecting value: line 1 column 1 (char 0)`. It recorded no attempt because no schema-valid provider output existed. Usage and cost for this production call are unavailable because the failing wrapper was not persisted.
- Root-cause diagnostic: a separate minimal structured-output call using the same model and tool policy reproduced the behavior. Claude Code reported two denied `StructuredOutput` tool calls and then returned prose asking for tool approval. The diagnostic confirms that the adapter's `--disallowedTools '*'` rule also blocks the internal structured-output mechanism required by `--json-schema`; this is an adapter-policy conflict, not a briefing checker failure. The diagnostic used no web search or fetch, cost $0.0216557, and is not counted as a production retry.
- Briefing and checker: no briefing was produced, so there was no first checker result, correction pass, or final checker result. The run status is **failed** and `briefing.md` does not exist.

#### Structured-output-enabled follow-up

After preserving the failed first run, the Claude Code adapter was changed to expose and permit only its internal `StructuredOutput` tool: `--tools StructuredOutput --allowedTools StructuredOutput`. Safe mode, disabled slash commands, and no session persistence remained in force. The implementation and regression test are in commit `0a06c30`. A minimal Sonnet 5 probe of the exact new policy returned schema-valid structured output with no permission denials or web requests; it cost $0.0128138 and is not counted as a production call.

The complete live workflow was then rerun against a fresh corpus. Its artifacts are archived under [`structured-output-enabled/`](runs/2026-08-18/structured-output-enabled/). This is a follow-up to diagnose and verify the adapter fix, not a replacement for the failed first run.

- Agent and execution environment: OpenAI Codex desktop agent on macOS 26.5.2 with Python 3.14.6, using Claude Code CLI 2.1.224 and exact model identifier `claude-sonnet-5`.
- Prompt version: [`briefing-runner-prompt.md`](../briefing-runner-prompt.md) SHA-256 `745c9dda04decb2f984916704f84ab4a20707a9e78e979af2f5814f25a4c488c`; clean repository commit `0a06c30`.
- Corpus window: 2026-08-17 17:41:05 UTC → 2026-08-18 17:41:05 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 196 items — 34 US politics, 60 US news, 53 world, 19 AI/tech, and 30 developer-community. Live fetch time: 24.559 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but had zero recognized entries; `r/ClaudeCode`, `r/LocalLLaMA`, and `r/cursor` returned HTTP 429. The final briefing reports all four gaps.
- Processing: 38 AI/tech items failed the relevance filter, one US-news item exceeded the per-source cap, and the US-news category cap dropped 17 items. No duplicate, field-budget, source-budget, or global-budget drops occurred. Eighty-four summaries were truncated at the configured field limit; all counters reconcile from 252 fetched items to 196 retained items.
- Briefing: the first model response filled all 22 configured slots (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), supplied all 25 configured exclusions, and included the required corpus-health and validation sections.
- Checker, first and final result: **0 errors and 3 warnings** (`WARN`). Two `unsupported_figure` warnings concern “60” in the Iran ceasefire topic and “$12,000” in the Asana Codex topic. One `claim_exceeds_evidence` warning concerns the Cursor Origin summary expanding beyond the thin cited evidence. Because warnings do not fail the briefing contract, the runner finalized the first candidate and made no correction call.
- Provider behavior: the one production call completed on its first transport attempt in 399.413 seconds. Claude Code reported 1 uncached input token, 42,813 cache-creation input tokens, 7,044 cache-read input tokens, and 44,390 output tokens; it exposed no separate reasoning-token count. Cost was $0.9580372. Server usage reported zero web searches and zero web fetches.
- Result: the run status is **complete** and the final briefing status is **WARN**. This confirms that permitting only `StructuredOutput` restores schema-constrained Claude Code generation without reopening browsing or general tool access.

#### Controlled structured-runner corpus replay

At owner request, the latest successful August 18 corpus was replayed through Codex CLI Terra and three OpenRouter models instead of being fetched again. The runner gained a `--corpus` replay path that validates, hashes, and archives the supplied corpus, and OpenRouter reasoning became enabled by default while remaining explicitly disableable. These implementation changes and their tests were uncommitted during generation, so the manifests record clean base commit `022f2d0` plus `dirty: true` and exact runtime source hashes.

- Controlled input: [`structured-output-enabled/corpus-2026-08-18.json`](runs/2026-08-18/structured-output-enabled/corpus-2026-08-18.json), SHA-256 `6f6f798f1d2e6ae7a3547d1917c68b4f04f0fa8894928286953176ad17687ee5`. All four completed comparisons archived byte-identical `corpus.json` artifacts and the same projected model corpus, SHA-256 `09de2b773fe2ec0c844bc745ce4f56b9cee703acb23c6bc8d4ea3acdd1e59301`.
- Prompt and controls: [`briefing-runner-prompt.md`](../briefing-runner-prompt.md) SHA-256 `50373c30bf4471dc056b3eb97c3d5eeb3f7ae1e9464c6c0c782493360e1d022b`; temperature 0 for OpenRouter; one checker-guided correction allowed; 600-second per-call deadline. Codex used its fixed medium reasoning. OpenRouter sent reasoning enabled and used each model's provider-default effort: Hy3 high, DeepSeek high, and Gemini mandatory medium.
- Schema compatibility: Terra, Hy3, and DeepSeek used output-schema SHA-256 `a4413cb55fc963ac317e16be111a4df86a9bf0a0bc2d34e2d71eb7b971c75016`. Gemini's first production request was rejected before generation because Google AI Studio treated a required integer property constrained by numeric `enum` as unspecified. Minimal probes isolated numeric `enum` as the trigger. The semantically equivalent Draft-07 constraint `minimum: 1, maximum: 1`, also independently enforced by runtime validation, produced schema SHA-256 `91f760c6ddb0df4b8b80522bc308d92fcbc37e176f314a18216fdb3afe34c2f2` and allowed the preserved follow-up to complete. The failed run is archived under [`replay-gemini-3.7-flash/`](runs/2026-08-18/replay-gemini-3.7-flash/); the completed follow-up is under [`replay-gemini-3.7-flash-schema-fixed/`](runs/2026-08-18/replay-gemini-3.7-flash-schema-fixed/).

| Model | Calls | First check | Final check | Model latency | Reasoning tokens | Reported cost |
|---|---:|---|---|---:|---:|---:|
| Codex CLI `gpt-5.6-terra` | 2 | 3 errors, 0 warnings | 0 errors, 0 warnings after checker amendment | 106.650s | 512 | subscription; unavailable |
| OpenRouter `tencent/hy3` | 1 | 0 errors, 5 warnings | same | 191.830s | 10,452 | $0.011435952 |
| OpenRouter `deepseek/deepseek-v4-flash` | 2 | 3 errors, 0 warnings | 0 errors, 2 warnings | 233.676s | 9,908 | $0.0099731354 |
| OpenRouter `google/gemini-3.7-flash` | 2 | 7 errors, 0 warnings | 0 errors, 3 warnings | 41.472s | 9,063 | $0.1155945 |

The original run artifacts recorded all four completed candidates as `review_required`: their corrected or first accepted candidates remained corpus-bound and contract-accepted, but the multi-axis outcome policy quarantined evidence warnings instead of publishing them. PR review identified Terra's sole warning as a checker false positive: the figure parser split the supported abbreviated school-year range `2025–26` and treated `26` as a standalone figure. The corrected checker preserves and canonicalizes year ranges; re-evaluating Terra now yields 0 errors and 0 warnings, so its candidate is `ready` with degraded source coverage. The historical finding remains in the run artifact rather than being rewritten. Hy3 retains three `unsupported_figure` and two `claim_exceeds_evidence` warnings. DeepSeek retains one of each. Gemini retains one `exclusion_log_short` and two `claim_exceeds_evidence` warnings. The corpus had the same four recorded coverage gaps as the Claude structured-output follow-up.

Production OpenRouter calls cost **$0.1370035874**. One successful Gemini diagnostic probe cost $0.00063075; rejected schema requests reported no usage. Total measured spend was therefore **$0.1376343374**, below the authorized $1 ceiling.

Relative to previous records, Terra again needed one correction and was slightly faster than its August 17 run (106.650s versus 112.756s), with zero warnings under the amended checker versus two after the prior run's manual amendment. Reasoning-enabled Hy3 used one call, fewer reasoning tokens, less time, and less reported cost than its August 15 same-input follow-up, but corpus, prompt, and checker versions differ, so this is operational context rather than a controlled quality improvement. DeepSeek reasoning produced usable structured output for the first time in the production-sized workflow after the earlier 8,192-token ceiling had been exhausted without text; the permanent larger ceiling was the material difference. No earlier Gemini 3.7 Flash dogfood result exists. The same-corpus Claude result is not a controlled model comparison because it used an older prompt and projection, but it provides an operational reference: one 399.413-second call, three warnings, and $0.9580372.

### 2026-08-17 — Codex GPT-5.6 Terra dogfood run

The complete fetch → rank and summarize → check → single correction → final check loop, run at the requested Terra Medium setting. The corpus, both model attempts, corrected briefing, configuration snapshot, and machine-readable manifest are archived at [`docs/runs/2026-08-17/`](runs/2026-08-17/). The Codex CLI manifest records the exact generation model as `gpt-5.6-terra`; its adapter does not expose a separate reasoning-effort field.

- Agent and execution environment: OpenAI Codex desktop agent on macOS 26.5.2 with Python 3.14.6, using Codex CLI 0.147.0 and `gpt-5.6-terra`. The generation process ran in the runner's empty read-only sandbox and rejected non-message/non-reasoning trace items.
- Prompt version: [`briefing-runner-prompt.md`](../briefing-runner-prompt.md) SHA-256 `745c9dda04decb2f984916704f84ab4a20707a9e78e979af2f5814f25a4c488c`; repository commit `748f4a9`.
- Corpus window: 2026-08-17 04:13:45 UTC → 2026-08-18 04:13:45 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 210 items — 34 US politics, 60 US news, 49 world, 13 AI/tech, and 54 developer-community. Live fetch time: 24.655 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but had zero recognized entries; `r/ClaudeCode` and `r/cursor` returned HTTP 429. The briefing's corpus-health section lists all three coverage gaps.
- Processing: 24 AI/tech items failed the relevance filter and the US-news category cap dropped 22 items. No duplicate, source-cap, field-budget, source-budget, or global-budget drops occurred. One hundred six summaries were truncated at the configured field limit; all counters reconcile against 256 fetched items.
- Briefing: 22 reported topics filled all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), with a 25-row exclusion log, corpus-health section, and final validation section.
- Checker, first result: 1 error, 0 warnings — `category_ineligible_ref` because the first US Politics topic cited a `world`-category item.
- Correction made after checking: replaced the ineligible `world` citation in “Trump threatens Oman as Iran-war peace window expires” with an eligible `us_politics` citation. No section placement or topic headline changed.
- Checker, original final result: **0 errors and 3 warnings** (`WARN`). The two `missing_discussion_link` warnings concern Hacker News entries for Cursor Origin and its status incident; one `unsupported_figure` warning concerned “4.2 percent” in the kindergarten-vaccine topic. No additional model turn was taken under the one-correction workflow.
- PR review amendment: removed the unsupported vaccine-exemption figure from the structured output and regenerated the rendered briefings, findings, schema, and manifest while preserving the raw model response. The current final result is **0 errors and 2 warnings** (`WARN`), both for missing Hacker News discussion links. Reproduce it with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-17/corpus-2026-08-17.json --briefing docs/runs/2026-08-17/briefing.md --config docs/runs/2026-08-17/briefing-config.json
  ```

### 2026-08-16 — OpenRouter GLM 5.2 dogfood run

The complete fetch → rank and summarize → check → single correction → final check loop, run with OpenRouter [`z-ai/glm-5.2`](https://openrouter.ai/z-ai/glm-5.2). Unlike the surrounding entries, this run has no `docs/runs/2026-08-16/` archive: the corpus, drafts, and configuration snapshot were not committed, so the figures below cannot be re-derived and there is no reproduce command. The prompt and configuration hashes were recorded at run time and identify the exact inputs.

- Agent and execution environment: OpenAI Codex desktop agent on macOS 26.5.2 with Python 3.14.6. Operator date August 16, 2026 in `America/Los_Angeles`.
- Prompt version: [`briefing-prompt.md`](../briefing-prompt.md) at base commit `b340548be06ec2d1a898bffda384defe5fd31730` (SHA-256 `41b038151c36031df3d3ae35578b5d959168251a22fb04e6baa8273dd6b9d86c`; Git blob `731d706316b358b65550a22c8116dba5c0847df8`), prompt version name `production`. The assembled first-pass request, including the operator-date prefix, trusted configuration, and fetched corpus, had SHA-256 `b0473bf8ba717ff62f8be8ebc556f0776528b8c271d6d2914588ceb00a7a5231`.
- Configuration version: `briefing-config.json` from the same base commit (SHA-256 `ef665c7c0c4cd7476593cc176b54de0764cde6e29fc7dc02cc6a24694e6c23d9`; Git blob `38429ea90cdd97ad95d949a232ccd169134e9905`).
- Generation controls: temperature 0, no seed, reasoning enabled at `high` effort, a 100,000-token completion ceiling, and a 600-second per-call timeout. The model received only the trusted prompt and configuration plus the closed, untrusted corpus; no tools, browsing, or external retrieval were available to it.
- Corpus window: 2026-08-16 06:06:13 UTC → 2026-08-17 06:06:13 UTC (24h).
- Corpus: 171 retained items — 26 US politics, 60 US news, 52 world, 7 AI/tech, and 26 developer-community. Live fetch time: 25.503 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries; `r/ClaudeCode`, `r/LocalLLaMA`, and `r/cursor` each returned HTTP 429. All four gaps appeared in the final briefing's prose and machine-readable corpus-health manifest.
- Processing: 10 relevance-filter drops, 2 duplicate drops, 1 per-source-cap drop, and 3 category-cap drops. No field-budget, source-budget, or global-budget drops occurred. Sixty-six summaries were truncated at the configured field limit; all counters reconcile from 187 fetched items to 171 retained items.
- Briefing: 22 reported topics filled all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), with all 25 configured exclusion-log entries and every degraded source reported.
- Checker, first result: 1 error, 0 warnings — a World Events topic cited an Axios item categorized as `us_politics`, which is not eligible for that section.
- Correction made after checking: one checker-guided correction removed the ineligible Axios citation, added an eligible NPR citation, and revised the topic summary to remain supported by the eligible Guardian, PBS, and NPR corpus items. No topic or section placement changed.
- Checker, final result: **0 errors and 0 warnings** (`PASS`), reported as `Briefing is consistent with its corpus.`
- Usage and cost: the first draft took 62.328 seconds for 30,043 prompt and 5,391 completion tokens (1,103 reasoning, 4,288 visible output) at $0.05920254; the correction took 30.940 seconds for 34,426 prompt and 5,021 completion tokens (789 reasoning, 4,232 visible output) at $0.07028880. Totals: 93.268 seconds, 64,469 prompt + 10,412 completion tokens, and **$0.12949134** reported by OpenRouter. Both calls completed on their first provider attempt, and no cache, audio, video, or image tokens were reported.
- Assessment: GLM 5.2 produced a structurally complete, fully grounded briefing on its first attempt except for one cross-category citation, and the deterministic finding was specific enough to repair in one pass without creating new errors or warnings. After correction, the model respected the closed-corpus boundary, exact-URL requirement, slot allocation, exclusion-log contract, source-health reporting, and claim-grounding heuristics. This is one stochastic live run, not a benchmark or a general model-quality claim: feed contents, source availability, provider routing, and model output can all vary on a rerun even with temperature 0.

### 2026-08-15 — OpenRouter Tencent Hy3 dogfood run

The complete fetch → rank and summarize → check → single correction → final check loop run with OpenRouter `tencent/hy3`. The corpus, first draft, final `ERROR` briefing, configuration snapshot, and token/cost manifest are archived at [`docs/runs/2026-08-15/`](runs/2026-08-15/). This entry preserves the unhealthy result rather than replacing it with an unrecorded cleaner rerun.

- Agent and execution environment: OpenAI Codex desktop agent on macOS 26.5.2 with Python 3.14.6. Generation used OpenRouter `tencent/hy3` with temperature 0, no seed, reasoning disabled, an 8,192-token output ceiling, and a 300-second per-call timeout. The model received only the trusted prompt/configuration and the closed corpus; it had no tools or browsing capability.
- Inferred prompt version (not recorded at run time): `briefing-prompt.md` at `bbefb4b` (SHA-256 `41b038151c36031df3d3ae35578b5d959168251a22fb04e6baa8273dd6b9d86c`).
- Initial environment attempt: the first sandboxed fetch had no outbound-network access and exited after 6.1 seconds with 0 usable items and 28 fetch errors. It was immediately rerun after network access was approved; the failed environment check is recorded here and is not misreported as source health.
- Corpus window: 2026-08-14 17:09:55 UTC → 2026-08-15 17:09:55 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 185 items — 24 US politics, 60 US news, 46 world, 3 AI/tech, and 52 developer-community. The successful live fetch took 19.65 seconds according to the corpus manifest.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries; `r/LocalLLaMA` and `r/cursor` returned HTTP 429. The briefing's corpus-health and validation sections record all three coverage gaps.
- Processing: 26 AI/tech and 2 developer-community items failed the relevance filter; 1 developer-community duplicate was dropped; and the 60-per-category cap dropped 11 US-news items. No source-cap, field-budget, source-budget, or global-budget drops occurred. Eighty-eight summaries were truncated at the configured field limit, and all counters reconcile against 225 fetched items.
- Briefing: 22 reported topics filled all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), with a 25-row exclusion log, corpus-health section, and final validation section.
- Checker, first result: 13 errors and 2 warnings. The draft used literal square brackets around the three AI subsection labels, so the checker reported all three subsections missing; it removed required query strings from nine BBC/Al Jazeera URLs; and its World Events Iran topic cited one ineligible `us_politics` item. The checker also warned about an unsupported figure parsed from the ICE headline and an unsupported quotation in the Lebanon topic.
- Correction made after checking: one checker-guided Hy3 correction turn removed the literal headline brackets, restored the required query strings, and paraphrased the unsupported Lebanon quotation. It did not remove the ineligible Iran citation; once the AI subsections became recognizable, the checker also exposed two ineligible `us_politics` Axios citations in AI News. Per the one-correction workflow, no additional model turn was taken.
- Checker, final result: **3 errors and 6 warnings**. The errors are the three `category_ineligible` findings just described. The warnings are one `unsupported_figure` on the ICE topic, four `unsupported_figure` findings on the two Hacker News topics' engagement figures, and one `claim_exceeds_evidence` finding on the Codex auto-research topic. Adding the required `### Validation status` section did not change the findings, so the final status stabilized at `ERROR`.
- Usage and cost: the first draft used 33,234 prompt and 4,238 completion tokens; the correction used 37,758 prompt and 4,299 completion tokens. Total usage was 70,992 prompt + 8,537 completion = 79,529 tokens, with zero reasoning or cached tokens. At the OpenRouter catalog rates retrieved after the run ($0.132/M input and $0.528/M output), the token-based estimate is **$0.01387848**. OpenRouter reported **$0.014364728** across the two calls; the first call's cost details used slightly higher effective rates, so the provider-reported value is retained as authoritative. Full per-call details are in [`generation-usage.json`](runs/2026-08-15/generation-usage.json).
- Reproduce the stabilized final checker result with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-15/corpus-2026-08-15.json --briefing docs/runs/2026-08-15/briefing.md --config docs/runs/2026-08-15/briefing-config.json
  ```

#### Reasoning-enabled follow-up on the same corpus

At owner request, Hy3 was run again against the exact archived corpus and configuration with reasoning enabled at the provider-default effort (high at run time). This is a same-input operational comparison, not a replacement for the first run. Its artifacts are archived under [`hy3-reasoning-enabled/`](runs/2026-08-15/hy3-reasoning-enabled/).

- Inferred prompt version (not recorded at run time): the same `briefing-prompt.md` version as the initial run, `bbefb4b` (SHA-256 `41b038151c36031df3d3ae35578b5d959168251a22fb04e6baa8273dd6b9d86c`).
- Original-ceiling attempt: with the original 8,192-token completion budget and 300-second call timeout, Hy3 consumed the completion budget in reasoning and returned no text (`finish_reason='length'`). Because the one-off invocation did not persist the exception's usage envelope, its exact billed cost is unavailable. Using the identical retry's 33,260-token prompt count and the exhausted 8,192-token budget, the catalog-rate estimate is $0.008715696.
- Owner-authorized retry controls: the same request was retried with a 100,000-token completion budget and a 600-second per-call timeout. No other generation control changed. The larger budget was subsequently made the evaluator's permanent default; the 10-minute timeout applied only to this follow-up.
- First completed draft: 90.85 seconds; 33,260 prompt tokens and 19,631 completion tokens, including 15,748 reasoning and 3,883 visible-output tokens. OpenRouter reported $0.014438142. The checker found 6 errors and 16 warnings: three AI subsection labels were unrecognized because the model again emitted literal square brackets, three BBC URLs lost their required query strings, and the unsupported-figure heuristic produced 16 warnings.
- Correction: one checker-guided reasoning-enabled turn completed in 174.34 seconds using 37,355 prompt tokens and 14,093 completion tokens, including 10,189 reasoning and 3,904 visible-output tokens. OpenRouter reported $0.0117533658.
- Final checker result: **0 errors and 27 warnings**. The briefing stabilized at `WARN`: AI News filled 3 of 4 slots, one Hacker News summary exceeded its thin evidence, and the remaining 25 warnings were `unsupported_figure` findings, mostly digits in inline citation URL paths being attributed to topic prose by the checker used for this run. No second correction was made.
- Completed-call usage and cost: 70,615 prompt + 33,724 completion = 104,339 tokens, of which 25,937 were reasoning and 7,787 were visible output. OpenRouter reported $0.0261915078 for the two completed calls.
- All-attempt cost estimate: including the initial length-exhausted attempt, estimated usage is 145,791 tokens and the catalog-rate estimate is $0.035843148. Combining the completed calls' reported cost with the failed call's token-rate estimate gives approximately **$0.0349072038**. The failed call prevents an exact all-attempt billed total.
- Offline verification: all 82 evaluator tests passed after changing the permanent completion-token default to 100,000.
- Reproduce the stabilized final checker result with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-15/hy3-reasoning-enabled/corpus-2026-08-15.json --briefing docs/runs/2026-08-15/hy3-reasoning-enabled/briefing.md --config docs/runs/2026-08-15/hy3-reasoning-enabled/briefing-config.json
  ```

### 2026-08-13 — Claude Code CLI dogfood run

The complete fetch → rank and summarize → check loop run with the Claude Code CLI. The corpus, corrected briefing, and configuration snapshot are archived at [`docs/runs/2026-08-13/`](runs/2026-08-13/).

- Agent and execution environment: Claude Code 2.1.220 using Claude Sonnet 5 at high effort, in a local macOS checkout with Python 3.14.6. The generation process was limited to the `Read` and `Write` tools; its recorded usage confirms zero web searches and zero web fetches.
- Inferred prompt version (not recorded at run time): `briefing-prompt.md` at `bbefb4b` (SHA-256 `41b038151c36031df3d3ae35578b5d959168251a22fb04e6baa8273dd6b9d86c`). The CLI also loaded unarchived startup-hook context, so this identifies only the repository prompt, not the complete model input.
- Corpus window: 2026-08-12 16:53:27 UTC → 2026-08-13 16:53:27 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 236 items — 35 US politics, 60 US news, 58 world, 23 AI/tech, and 60 developer-community. Elapsed live fetch time: 10.5 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries. The briefing's corpus-health prose and machine-readable manifest record the resulting coverage gap.
- Processing: 36 AI/tech and 3 developer-community items failed the relevance filter; 1 US-news and 1 developer-community duplicate were dropped; a per-source cap dropped 6 world items; and the 60-per-category cap dropped 24 US-news and 43 developer-community items. No field, source-budget, or global-budget drops occurred, and all counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming the empty source.
- CLI behavior: the first buffered attempt was stopped after 369.1 seconds because it exposed no progress and had not written a file; it cost $0.3564 and made no web requests or file changes. A retry with streaming diagnostics completed in 363.4 seconds and cost $1.2746. That retry first wrote a partial one-section draft, recognized the truncation itself, and replaced it with the complete briefing. Total model cost across both attempts was $1.6310. User-level Claude Code startup hooks also loaded unrelated learning-mode and skill context despite the narrow tool allowlist; `--safe-mode` would make a future run more reproducible and less noisy.
- Checker, first result: 0 errors, 1 warning — `claim_exceeds_evidence` because the Hacker News item “Codex in ChatGPT desktop app for Linux is now in preview” had an empty summary, while the generated prose expanded its 56-character title into 184 characters of unsupported framing.
- Correction made after checking: reduced that summary to the title-supported statement, “Codex in the ChatGPT desktop app for Linux is now in preview.” No topic, citation, or section placement changed.
- Checker, final result: 0 errors, 0 warnings. Reproduce the final checker result with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-13/corpus-2026-08-13.json --briefing docs/runs/2026-08-13/briefing.md --config docs/runs/2026-08-13/briefing-config.json
  ```

- Offline verification: all 219 core tests and all 38 evaluator tests passed.

### 2026-08-12 — Codex daily dogfood run

The complete fetch → rank and summarize → check loop run in Codex. The corpus, briefing, and configuration snapshot are archived at [`docs/runs/2026-08-12/`](runs/2026-08-12/).

- Agent and execution environment: OpenAI Codex desktop agent in a local macOS checkout with Python 3.14.6.
- Inferred prompt version (not recorded at run time): `briefing-prompt.md` at `5e31cfa` (SHA-256 `e115aeb706bc87c3a9df87b349672d6f858e7ddf6a6b346dd6da0602b97fcf3a`).
- Corpus window: 2026-08-11 18:54:09 UTC → 2026-08-12 18:54:09 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 210 items — 40 US politics, 60 US news, 58 world, 24 AI/tech, and 28 developer-community. Elapsed live fetch time: 25.4 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries; `r/ClaudeCode`, `r/LocalLLaMA`, and `r/cursor` returned HTTP 429. The briefing's corpus-health prose and machine-readable manifest record all four coverage gaps.
- Processing: 55 AI/tech items and 3 developer-community items failed the relevance filter; the 60-per-category cap bound on US news, dropping 11 items. No duplicate, source-cap, field-budget, source-budget, or global-budget drops occurred, and all counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming every failed or empty source.
- Checker, first result: 1 error, 1 warning — an `ungrounded_link` error because the “second brain” Reddit citation used a percent-encoded form that did not exactly match the corpus URL, and an `unsupported_figure` warning because the Blacksmith valuation figure appeared in the item's URL but not in its title or summary.
- Corrections made after checking: replaced the Blacksmith valuation figure with the corpus-supported statement that its valuation jumped almost tenfold in less than a year. The first URL correction still included typographic quotation marks from the source title and produced 1 `ungrounded_link` error with 0 warnings on the intermediate check; the citation was then changed to the exact corpus URL slug.
- Checker, final result: 0 errors, 0 warnings. Reproduce the final checker result with:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-12/corpus-2026-08-12.json --briefing docs/runs/2026-08-12/briefing.md --config docs/runs/2026-08-12/briefing-config.json
  ```

### 2026-08-11 — Codex dogfood run and dated fixture sample

The complete fetch → rank and summarize → check loop requested as a dated sample. Unlike the fixed 2026-08-09 regression pair, this run is preserved as a separate dated fixture set: [`corpus-2026-08-11.json`](../fixtures/corpus-2026-08-11.json), [`briefing-2026-08-11.md`](../fixtures/briefing-2026-08-11.md), and [`briefing-config-2026-08-11.json`](../fixtures/briefing-config-2026-08-11.json).

- Agent and execution environment: OpenAI Codex desktop agent in a local macOS checkout with Python 3.14.6.
- Inferred prompt version (not recorded at run time): `briefing-prompt.md` at `2adfeba` (SHA-256 `a5067598917874ef5e30acfd65d7b2e55c9992c5b4c6ba2368a160696fa7e72b`).
- Corpus window: 2026-08-10 16:47:41 UTC → 2026-08-11 16:47:41 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 230 items — 40 US politics, 60 US news, 55 world, 22 AI/tech, and 53 developer-community. Elapsed fetch time: 19.5 seconds.
- Source failures: the Hacker News query `prompt engineering` returned successfully but contained zero recognized entries; `r/ClaudeCode` and `r/LocalLLaMA` returned HTTP 429. The briefing's corpus-health prose and machine-readable manifest record all three coverage gaps.
- Processing: 40 AI/tech items and 1 developer-community item failed the relevance filter; 3 developer-community duplicates were dropped; the 60-per-category cap bound on US news, dropping 18 items. No source cap bound, and all counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming every failed or empty source.
- Checker, first result: 0 errors, 2 warnings — one `unsupported_figure` warning for adding the year 2026 to the homicide summary when that year was absent from its cited item, and one for spelling the emissions estimate as `5%` when the corpus used `5 percent`.
- Correction made after checking: removed the unsupported year from the homicide summary and changed the emissions estimate to the corpus-supported words “five percent.” No topics or citations changed.
- Checker, final result: 0 errors, 0 warnings. The full 197-test suite also passed. Reproduce the final checker result with:

  ```bash
  python3 eval_briefing.py --corpus fixtures/corpus-2026-08-11.json --briefing fixtures/briefing-2026-08-11.md --config fixtures/briefing-config-2026-08-11.json
  ```

### 2026-08-10 — scheduled daily-news-briefing task

The regular `daily-news-briefing` scheduled task (fetch → rank and summarize → check loop), run unattended by Claude Code. The corpus, briefing, and config snapshot are archived at [`docs/runs/2026-08-10/`](runs/2026-08-10/), so this entry can be re-derived instead of taken on trust.

- Agent and execution environment: Claude Sonnet 5 in Claude Code, running the scheduled `daily-news-briefing` task unattended, local macOS checkout with Python 3.14.6.
- Inferred prompt version (not recorded at run time): `briefing-prompt.md` at `c47973a` (SHA-256 `c83082cbeaa8df013a8de6251b8e26011aceccf9728b49fee5a955894daf49b2`). This is the latest prompt change before corpus generation and the version in the parent of the contemporaneous log commit; the artifacts were archived later, after another prompt change.
- Corpus window: 2026-08-09 17:10:48 UTC → 2026-08-10 17:10:48 UTC (24h), default caps of 25 items per source and 60 per category.
- Corpus: 208 items — 26 US politics, 60 US news, 47 world, 15 AI/tech, 60 developer-community. Elapsed fetch time wasn't captured on the actual run; an immediate follow-up fetch under the same environment and script took 23.9 seconds, given here as representative.
- Source failures: `r/ClaudeCode` returned HTTP 429 — the only failure. All four other Reddit sources, Hacker News, and every RSS feed cleared the window.
- Processing: 19 AI/tech and 3 developer-community items failed the relevance filter; 1 US-news and 1 developer-community duplicate were dropped; the 60-per-category cap bound on US news (8 dropped) and developer-community (9 dropped). No source cap bound. All counters reconciled against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming the one failed source.
- Checker, first result: 3 errors, 7 warnings — three `category_ineligible` errors because the AI News section (eligible categories: `ai_tech`, `us_news`) cited two `us_politics` items (the Zuckerberg manifesto's "biggest risk" framing and the Sanders AI-pause letter); plus warnings for two unsupported figures (the Will Scharf item's "$400m" figure and the gas-price item's "$1" increase, both true but cited against items that didn't contain them), two unsupported quotations (the Netanyahu item's "historic" quote and the Zuckerberg item's "with as many people..." quote, both cited against items that didn't contain them), and three `claim_exceeds_evidence` warnings on the AI Dev Tools items sourced from Hacker News posts with empty corpus summaries, where the drafted summaries added unsupported framing beyond the bare title.
- Correction made after checking: moved the Sanders AI-pause letter into US Politics, where `us_politics` is an eligible category, dropping the progressive-primary-wins topic to the exclusion log to keep the section at 3; re-cited the Zuckerberg manifesto against eligible `ai_tech`/`us_news` sources and swapped in a Wired "AI slop backlash" item (from an eligible `ai_tech` source) to refill AI News's fourth slot; added the missing supporting citations for the Will Scharf and gas-price figures and the Netanyahu quote; and trimmed the three Hacker News-sourced AI Dev Tools items down to only what their (otherwise summary-less) titles support, per the empty-summary grounding rule.
- Checker, final result: 0 errors, 0 warnings — reproducible today:

  ```bash
  python3 eval_briefing.py --corpus docs/runs/2026-08-10/corpus-2026-08-10.json --briefing docs/runs/2026-08-10/briefing.md --config docs/runs/2026-08-10/briefing-config.json
  ```

### 2026-08-09 — the run behind the committed reference pair

The complete fetch → rank and summarize → check loop that produced [`fixtures/corpus-2026-08-09.json`](../fixtures/corpus-2026-08-09.json) and [`fixtures/briefing-2026-08-09.md`](../fixtures/briefing-2026-08-09.md). Every count below is derived from those two committed files, so this entry can be re-derived instead of taken on trust.

- Agent and execution environment: Claude Opus 5 subagent via Claude Desktop 2.1.222, in a local macOS checkout with Python 3.14.6.
- Inferred prompt version (not recorded at run time): `briefing-prompt.md` at `8f89fb6` (SHA-256 `3d470b528b257e52de3889bdda9dadda2f8f5255ac81abad4ae6010404c9885d`).
- Corpus window: 2026-08-09 00:34 UTC → 2026-08-10 00:34 UTC (24h), with the default caps of 25 items per source and 60 per category.
- Corpus: 158 items — 27 US politics, 53 US news, 46 world, 7 AI/tech, 25 developer-community. Elapsed fetch time: 27.1 seconds.
- Source failures: `r/ClaudeCode`, `r/LocalLLaMA` and `r/cursor` all returned HTTP 429 — three of the four subreddits. No Hacker News item cleared the window either, so both dev sub-sections drew on r/ClaudeAI alone, with no engagement signal available for any dev-community item. The briefing says so in its corpus-health section rather than just looking thin.
- Processing: 10 AI/tech and 2 developer-community items failed the relevance filter; 2 US-news duplicates were dropped. Neither cap bound. All counters reconcile against `fetched`.
- Briefing: 22 reported topics, filling all six configured sections to target (3/3, 4/4, 5/5, 4/4, 3/3, 3/3), plus a 25-row exclusion log and a corpus-health section naming all three failed sources.
- Checker, first result: 1 error, 1 warning — `ungrounded_link` for the AI Dev Practices item “Cowork Projects keep CLAUDE.md outside the project folder,” plus the expected transitional `slots_underfilled` warning because the checker still required 5 US Politics topics while the new prompt required 3.
- Correction made after checking: replaced the ungrounded “Cowork Projects” item and URL with the corpus-supported “A developer uses Claude to build tools around their own ADHD needs” item. The following checker-contract task changed the US Politics target from 5 to 3, removing the transitional warning without changing the briefing.
- Checker, final result: 0 errors, 0 warnings — still reproducible today:

  ```bash
  python3 eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --config fixtures/briefing-config-2026-08-09.json
  ```

Note on the claim-grounding checks: the four problems they caught on arrival (three over-reaching summaries, one misattributed quotation) were in the **2026-08-08** briefing, the baseline this pair replaced. That briefing was corrected in `30fafca` and removed from `fixtures/` in `2750a25`. This run's briefing has never been edited since it was committed.
