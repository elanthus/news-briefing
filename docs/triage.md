# Triage a briefing run

Use `triage_run.py` to turn one retained run directory into a short operational diagnosis:

```bash
python3 triage_run.py runs/2026-09-03 --no-model
```

The command writes `triage.json` and `triage.md` beside the run, under a directory named
`triage-<run-directory>`, and prints the Markdown report. Pass `--output-dir` to choose a
different destination. The destination cannot be the run directory or one of its descendants,
so triage never changes the evidence it is inspecting.

## Deterministic classification

The classifier checks fetch completion, structured provider failure records, recorded output truncation,
exhausted correction budgets, blocking checker findings, degraded source
coverage, and failed fallback chains. A completed `ready` run receives
`no_failure_detected`. One run can receive several classes. Every class cites a manifest key,
artifact key, or trace line, and blocking checker failures receive a stable short fingerprint
after destinations and opaque citation handles are removed.

Reports contain no web destinations. The classifier does not copy corpus articles or briefing
prose into either output. It reads only operational metadata, provider failure codes, checker finding
messages, source-health records, and bounded trace or provider-event data.

Current records carry schema version 1 and stable failure codes, HTTP status, transience, truncation,
checker identities, and correction stage/budget fields. Triage uses those fields
and cites their manifest locations. It does not infer truncation from malformed
JSON or recursively search provider events for length markers. Historical logs
without these records are not reclassified from exception wording; preserved
historical evidence remains unchanged. The original unversioned structured shape remains readable;
unknown versions or fields fail closed. Future record changes require an explicit
versioned read contract, independent of dataclass defaults.

Finalized checker outcomes take precedence over earlier recoverable provider errors
when the fallback chain selects its failure reason. Triage still lists those provider
errors separately. A zero correction limit means corrections were disabled, so failed
checks are classified as validation failure rather than budget exhaustion.

Chains with no selected result and untried candidates emit `chain_incomplete`; triage
reports `fallback_chain_incomplete`. Older failed logs without a valid chain code still
report `fallback_chain_failed` from their status, without claiming every model ran.
Provider diagnostic flags remain strict boolean projections from private metadata;
they do not change the validated failure code or public explanation.

## Optional model summary

The optional paragraph is retained to help an operator read several simultaneous
causes as one explanation. It adds no classification evidence and remains labeled
unverified; `--no-model` is the cost-free deterministic workflow.

Omit `--no-model` to add a model-generated paragraph:

```bash
python3 triage_run.py runs/2026-09-03 \
  --provider openrouter \
  --model tencent/hy3
```

The provider is selected through the same `ModelProvider` factory as briefing generation. If
the flags are absent, the command first checks `NEWS_BRIEFING_PROVIDER` and
`NEWS_BRIEFING_MODEL`, then the run manifest. The model receives only the deterministic class
records, redacted finding messages, allowlisted provider failure records, and the final 40 trace
event names and timestamps. It receives no corpus text, briefing prose, or URLs. No tools are
offered, and a returned tool call is rejected. The report labels accepted text as “model
summary (unverified).” A provider or policy failure is recorded as `model_summary_error` and
does not discard the deterministic diagnosis.

## Manual GitHub triage

The **Triage briefing run** workflow is manual-only. It downloads the requested unexpired
encrypted diagnostics artifact, or the newest artifact whose name starts with
`briefing-diagnostics-`, decrypts it with `CORPUS_ARCHIVE_PASSPHRASE`, and opens a labeled
issue from the deterministic report. Its token can read repository contents and Actions
artifacts and can write issues; it has no other repository permissions. The optional
`use_model` input adds an OpenRouter summary only when `OPENROUTER_API_KEY` is available.
