# Archived runs

Two run directories from the manual dogfooding period are kept as exemplars. The rest were removed to keep the repository small; every removed file is still available from commit `59300ea3fca18bbb8edce5cb40c821e33ab2d6e6`, for example:

```bash
git show 59300ea3fca18bbb8edce5cb40c821e33ab2d6e6:docs/runs/2026-08-17/briefing.md
git ls-tree -r --name-only 59300ea3fca18bbb8edce5cb40c821e33ab2d6e6 docs/runs/2026-08-17
```

## Kept

| Directory | Date | What it shows | Files (bytes) |
| --- | --- | --- | --- |
| [`2026-08-18/`](2026-08-18/) | 2026-08-18 | First complete-run attempt of the day, stopped during the initial model call before a schema-valid briefing or checker result existed. | `manifest.json` (5,262), `corpus-2026-08-18.json` (110,868), `model-corpus.json` (112,086), `citation-map.json` (48,368), `request.txt` (115,928), `output-schema.json` (16,315), `briefing-config.json` (2,005), `trace.jsonl` (1,053), `fetch.stdout` (48), `fetch.stderr` (0) |
| [`2026-08-18/structured-output-enabled/`](2026-08-18/structured-output-enabled/) | 2026-08-18 | Rerun after the structured-output adapter fix: a complete run with a checker result and final briefing. `tests/test_eval_briefing.py` reads its corpus and `final.md`. | `manifest.json` (8,373), `corpus-2026-08-18.json` (110,559), `model-corpus.json` (111,601), `citation-map.json` (48,210), `request.txt` (115,443), `attempt-01-raw.txt` (19,659), `attempt-01-structured.json` (23,962), `attempt-01-briefing.md` (27,335), `attempt-01-findings.json` (705), `final.md` (28,576), `trace.jsonl` (1,409), [`README.md`](2026-08-18/structured-output-enabled/README.md) listing five byte-identical files that were removed |

## Removed run directories

All paths are under `docs/runs/` in commit `59300ea3fca18bbb8edce5cb40c821e33ab2d6e6`.

| Directory | Contents |
| --- | --- |
| `2026-08-10/` | Scheduled Claude Code run: corpus, briefing, `briefing-config.json`. |
| `2026-08-12/` | Codex run: corpus, briefing, `briefing-config.json`. |
| `2026-08-13/` | Claude Code CLI run: corpus, corrected briefing, `briefing-config.json`. The evaluator fixture corpus was copied from this run. |
| `2026-08-15/` | OpenRouter Hy3 run: corpus, first draft, final `ERROR` briefing, `briefing-config.json`, `generation-usage.json`. |
| `2026-08-15/hy3-reasoning-enabled/` | Same-input Hy3 rerun with reasoning enabled: corpus, first draft, briefing, config, usage. |
| `2026-08-17/` | Codex Terra complete run: corpus, both attempts (raw, structured, findings, provider events), corrected briefing, manifest, request, schema, citation map, trace, fetch logs. |
| `2026-08-18/replay-gpt-5.6-terra/` | Model replay against the 2026-08-18 structured-output corpus: two attempts, briefing, preview, manifest, trace. |
| `2026-08-18/replay-hy3/` | Model replay: one attempt, briefing, preview, manifest, trace. |
| `2026-08-18/replay-deepseek-v4-flash/` | Model replay: two attempts, briefing, preview, manifest, trace. |
| `2026-08-18/replay-gemini-3.7-flash/` | Model replay rejected before generation over the numeric `enum` schema: manifest, trace, request, schema. |
| `2026-08-18/replay-gemini-3.7-flash-schema-fixed/` | Model replay with the adjusted schema: two attempts, briefing, preview, manifest, trace. |

Each replay directory also held the same corpus, projected model corpus, citation map, request, and configuration; those copies were byte-identical across the five replays.
