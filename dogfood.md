# GLM 5.2 dogfood report — August 16, 2026

## Result

The complete live fetch → rank and summarize → check → one correction → final check workflow completed with OpenRouter [`z-ai/glm-5.2`](https://openrouter.ai/z-ai/glm-5.2). The first briefing had one checker error and no warnings. A single checker-guided correction produced a final briefing with **0 errors and 0 warnings**.

The final briefing filled all 22 configured slots: 3 US Politics, 4 US News, 5 World Events, 4 AI News, 3 AI Dev Tools, and 3 AI Dev Practices. It also supplied all 25 configured exclusion-log entries and reported every degraded source.

## Run configuration

- Operator date: August 16, 2026 in `America/Los_Angeles`.
- Execution environment: OpenAI Codex desktop agent on macOS 26.5.2 with Python 3.14.6.
- Provider and model: OpenRouter `z-ai/glm-5.2`.
- Prompt version: `production`, using `briefing-prompt.md` from base commit `b340548be06ec2d1a898bffda384defe5fd31730` (SHA-256 `41b038151c36031df3d3ae35578b5d959168251a22fb04e6baa8273dd6b9d86c`; Git blob `731d706316b358b65550a22c8116dba5c0847df8`). The assembled first-pass request, including the operator-date prefix, trusted configuration, and fetched corpus, had SHA-256 `b0473bf8ba717ff62f8be8ebc556f0776528b8c271d6d2914588ceb00a7a5231`.
- Configuration version: `briefing-config.json` from the same base commit (SHA-256 `ef665c7c0c4cd7476593cc176b54de0764cde6e29fc7dc02cc6a24694e6c23d9`; Git blob `38429ea90cdd97ad95d949a232ccd169134e9905`).
- Generation controls: temperature 0, no seed, reasoning enabled at `high` effort, 100,000-token completion ceiling, and a 600-second per-call timeout.
- Tool boundary: the model received only the trusted prompt and configuration plus the closed, untrusted corpus. No tools, browsing, or external retrieval were available to the model.
- Correction policy: at most one correction pass, using only deterministic checker findings and the original closed corpus.

## Live corpus

- Window: `2026-08-16T06:06:13.270935+00:00` → `2026-08-17T06:06:13.270935+00:00` (24 hours).
- Fetch time: 25.503 seconds.
- Retained corpus: 171 items — 26 US politics, 60 US news, 52 world, 7 AI/tech, and 26 developer-community.
- Processing: 10 relevance-filter drops, 2 duplicate drops, 1 per-source-cap drop, and 3 category-cap drops. No field-budget, source-budget, or global-budget drops occurred. Sixty-six summaries were truncated at the configured field limit. The counters reconcile from 187 fetched items to 171 retained items.
- Source failures: the Hacker News `prompt engineering` query returned successfully but contained zero recognized entries. `r/ClaudeCode`, `r/LocalLLaMA`, and `r/cursor` each returned HTTP 429. All four gaps appeared in the final briefing's prose and machine-readable corpus-health manifest.

## Checker and correction

The first checker result was **1 error and 0 warnings**. A World Events topic cited an Axios item categorized as `us_politics`, which is not eligible for that section.

The single correction removed the ineligible Axios citation, added an eligible NPR citation, and revised the topic summary to remain supported by the eligible Guardian, PBS, and NPR corpus items. No topic or section placement changed.

The final checker result was **0 errors and 0 warnings**:

```text
0 error(s), 0 warning(s)
Briefing is consistent with its corpus.
```

## Usage and cost

| Call | Latency | Prompt tokens | Completion tokens | Reasoning tokens | Visible-output tokens | OpenRouter cost |
|---|---:|---:|---:|---:|---:|---:|
| First draft | 62.328 s | 30,043 | 5,391 | 1,103 | 4,288 | $0.05920254 |
| Checker-guided correction | 30.940 s | 34,426 | 5,021 | 789 | 4,232 | $0.07028880 |
| **Total** | **93.268 s** | **64,469** | **10,412** | **1,892** | **8,520** | **$0.12949134** |

Both calls completed on their first provider attempt. OpenRouter's per-call usage envelopes supplied the token counts and costs; no cache, audio, video, or image tokens were reported.

## Assessment

GLM 5.2 produced a structurally complete, fully grounded briefing on its first attempt except for one cross-category citation. The deterministic finding was specific enough for the model to repair in one pass without creating new errors or warnings. On this run, the model respected the closed-corpus boundary, exact-URL requirement, slot allocation, exclusion-log contract, source-health reporting, and claim-grounding heuristics after correction.

This is one stochastic live run, not a benchmark or a general model-quality claim. Feed contents, source availability, provider routing, and model output can all vary on a rerun even with temperature 0.
