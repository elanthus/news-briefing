# Weekly unverified machine grounding monitor

Automated, non-gating measurement of already-published briefings. It never changes a day's publication decision. See [Evaluation methodology](../evaluation-methodology.md#unverified-machine-grounding-monitor) for what this rate can and cannot be used to claim; per-topic verdicts stay in the encrypted review artifact, not this log.

**Judge independence.** The primary judge, `tencent/hy3`, is also the first model in the production fallback
chain (`PRODUCTION_MODEL_CHAIN` in `run_daily_briefing.py`), so on most days it grades briefings it generated.
The audit judge, `deepseek/deepseek-v4-flash-0731`, is the second model in the same chain. A model judging its
own output can share its blind spots, so these rates should be read as self-consistency checks, not independent
verification.

**Audit agreement has no public denominator.** The agreement column is the share of topics in the stratified
double-review sample on which the audit judge's grounding verdict matched the primary judge's. The size of that
sample is recorded only in the encrypted review artifact, so a 100.0% cell may rest on few topics and should not
be read as strong evidence of judge reliability.

**Historical artifact limitations (2026-10-02).** The W37–W39 rows were produced before the monitor followed accepted semantic repairs, verified recorded artifact hashes and evidence positions, or required proof of a matching successful deployment. Its previous artifact selection also did not establish seven distinct report dates in the requested ISO week. Those measurements describe the diagnostic candidates reviewed at the time; they cannot establish the grounding of the exact published artifacts. Their values remain unchanged. The [remediation record](remediation-2026-10-02.md) describes the corrected sampling and publication contract. Future repeated assessments append separately labeled rows.

| Week | Runs reviewed | Runs skipped | Topics reviewed | Unverified grounding rate (95% CI) | Audit agreement | Primary judge | Cost (USD) |
|---|---:|---:|---:|---:|---:|---|---:|
| 2026-W37 | 5 | 0 | 110 | 110/110; 100.0% [96.6, 100.0] | 100.0% | openrouter/tencent/hy3 | 0.0103 |
| 2026-W38 | 7 | 0 | 154 | 153/154; 99.4% [96.4, 99.9] | 96.8% | openrouter/tencent/hy3 | 0.0157 |
| 2026-W39 | 6 | 0 | 132 | 132/132; 100.0% [97.2, 100.0] | 100.0% | openrouter/tencent/hy3 | 0.0122 |
