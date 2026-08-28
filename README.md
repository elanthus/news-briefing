<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/favicon-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/favicon-light.png">
    <img alt="" src="docs/images/favicon-light.png" width="72" height="72" align="center">
  </picture>
  news briefing
</h1>

**An LLM chooses stories and writes a daily AI, US, and world news briefing from feed titles and excerpts. Code fixes the time window, allowed evidence, link destinations, and publication gate.**

[![CI](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml/badge.svg)](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml)

Live site → <https://elanthus.github.io/news-briefing/> · Each published date links to an [integrity report](docs/images/auditor-report.png). When the matching retained corpus is available, the report also links to its text-free audit manifest.

---

A GitHub Actions cron job fetches roughly 150–250 items from RSS, Hacker News, and Reddit into a closed, schema-validated corpus. The model ranks and summarizes only the titles, feed excerpts, and metadata in that corpus; it does not fetch or read the linked articles. Before generation, code replaces every destination with an opaque citation handle. After generation, a deterministic policy checker verifies the selected handles, section eligibility, source-health declaration, and output shape before code restores the links. A failed candidate is corrected or quarantined, never silently published.

An early version [fabricated a citation](docs/writeups/injection-benchmark-post.md). The checker caught it, and the rest of the project grew around a simple boundary: anything deterministic code can decide should not be left to the prompt.

| Reader view | Auditor view |
|---|---|
| ![Reader view of the daily briefing](docs/images/reader-view.png) | ![Per-run integrity report](docs/images/auditor-report.png) |
| Every rendered destination comes from a selected corpus item. This does not establish that the summary is faithful to the linked article. | Each date links to its disposition, automated repair log, and corpus-health findings. |

## Try it

Python 3.11+ is required. The pipeline and evaluator use only the standard library; the optional static-site build has a separate pinned dependency set.

Run the deterministic checker against the committed frozen fixtures. This exercises the standalone publication-policy checker end to end, offline:

```bash
python3 -S eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --config fixtures/briefing-config-2026-08-09.json
```

Point it at a poisoned fixture and it exits nonzero with the blocking findings:

```bash
python3 -S eval_briefing.py --corpus fixtures/injection-corpus.json --briefing fixtures/injection-briefing.md --config fixtures/injection-config.json
```

Fetch a live corpus in a human-readable form:

```bash
python3 -S fetch_news.py --hours 24 --markdown
```

That view prints raw URLs and omits feed excerpts, so it is useful for inspection but is not the destination-redacted representation sent to the model.

Run an end-to-end briefing through OpenRouter (requires `OPENROUTER_API_KEY`):

```bash
python3 -S run_briefing.py --provider openrouter --model your/model-id --output briefing.md
```

`--provider` also accepts `claude-code-cli` and `codex-cli`; those adapters use an existing signed-in CLI session. The committed results cover two models, but the harness is not tied to them: any OpenRouter model compatible with the structured-output request can run the same 55-case suite, and `--prompt` can compare another prompt with the production prompt under the same preregistered rules.

## What code enforces

| Question | Enforcement |
|---|---|
| Is this item inside the publication window? | The fetcher applies the cutoff before generation. |
| Where can a link point? | The model receives an opaque handle, not a URL. Rendering resolves that handle through a frozen code-owned map. |
| Is the citation in the run's evidence? | The validator checks the selected handle and the rendered canonical destination against the frozen corpus. |
| Is the story eligible for this section? | Source configuration assigns its corpus category; per-section schema enums and an independent validator restrict the eligible handles. The model chooses among them. |
| Did a source silently fail? | Each configured source request records an outcome, and the briefing must declare the resulting corpus health. |
| Is the summary faithful to the article? | This is not checked against the article: the system sees only the feed title and excerpt. Heuristics can warn about claims unsupported by that evidence but cannot establish article-level faithfulness. |

The contract is intentionally narrower than “the model is correct.” It proves corpus membership, destination ownership, routing, and structural requirements; it does not turn a feed excerpt into human review of the underlying article.

### Citation projection

Before generation, each corpus item is projected into untrusted evidence text plus exactly one opaque identifier. The sequence is item-aligned even when an item has both article and discussion destinations: both real URLs remain together in a code-owned map the model never receives. Internal item IDs are also withheld.

Each section's JSON Schema enumerates only the identifiers eligible for that section. An independent validator rejects unknown or ineligible identifiers, as well as any URL or opaque reference token placed in a prose field. Rendering then expands each selected identifier to all of its code-owned destinations, so a Hacker News story automatically carries its discussion link and cannot substitute or omit it.

This is destination allowlisting, not semantic grounding. The model can type arbitrary characters, but it cannot author a destination that survives validation.

## Architecture

![Runtime pipeline: fetch, project, generate, validate, repair, correct, gate, publish](docs/images/runtime-pipeline.svg)

```text
fetch_news.py      →  corpus.json (schema v6, validated on write)
agent_runner/      →  project → generate → validate → repair → correct → gate
eval_briefing.py   →  deterministic policy checker, usable standalone
prepare_publication.py / build_site.py  →  static site + per-run integrity report
```

Four design decisions shape the implementation:

**1. One shared contract.** [`corpus_schema.py`](corpus_schema.py) owns field shapes, counter semantics, budgets, and `canonicalize_url`. The fetcher uses that function for deduplication, and the checker uses it for citation membership. A trailing slash therefore cannot identify one article during fetch and another during validation. Query parameters that identify distinct resources remain significant: `item?id=123` cannot be rewritten as `item?id=999` and excused as cosmetic normalization.

**2. Deterministic repair before model correction.** When every blocking finding is mechanically fixable—for example, a repeated citation or an over-limit section—a code-owned normalizer repairs the candidate and runs validation again. Production allows up to three model correction passes for findings that require regeneration. Every repair is recorded in the manifest and surfaced in the integrity report.

**3. Lifecycle and publication are separate states.** The checkpoint manifest records lifecycle (`running` / `complete` / `failed`) separately from publication disposition (`ready` / `review_required` / `rejected` / `no_result`). A degraded fetch reduces coverage without making the run itself fail. An unknown citation rejects publication. Only `ready` produces a published briefing.

**4. Tool restrictions are provider-specific.** OpenRouter receives no tool definitions, and any returned tool call is a hard failure. Claude Code CLI exposes and permits only `StructuredOutput`. The Codex adapter ignores user configuration, disables the named shell, web, multi-agent, remote-plugin, and image features, runs in an empty read-only workspace, and rejects unexpected trace item types. The sandbox specifically limits the effect of any shell capability that remains; it is one layer in the adapter's fail-closed policy, not a universal “remove all tools” switch.

### Network and parser boundaries

The fetcher accepts only HTTP(S) source URLs without embedded credentials. Before each request or redirect hop, it resolves the hostname once, rejects the request if any answer is non-public, and connects directly to one captured address. TLS certificate verification and SNI still use the original hostname. Repeating the process for each redirect prevents direct, redirect-based, and DNS-rebinding access to loopback, private, link-local, and common metadata-service destinations. Authenticated API transport rejects redirects entirely.

Feed XML is treated as untrusted. Before ElementTree builds the document tree, a preliminary Expat pass rejects every `DOCTYPE` declaration. Custom internal and external entities require a DTD, so this blocks entity-expansion and external-entity payloads without adding `defusedxml`.

## Evaluation

[`evaluator/`](evaluator/) is a development-only benchmark for two separate systems: the deterministic checker and model generation. Their denominators are reported separately and never combined into one score.

### Checker and feed parser

**81 offline cases** run without credentials. All 81 have completed blinded model review, with repository-owner adjudication of historical disagreements. No case has completed independent human review, so the repository does not present those reviews as human ground truth.

| Component | Cases | Precision | Recall | False-positive rate |
|---|---:|---:|---:|---:|
| Checker | 69 | 42/48; 87.5% [75.3, 94.1] | 42/54; 77.8% [65.1, 86.8] | 6/1671; 0.36% [0.16, 0.78] |
| Feed parser | 12 | 8/8; 100% [67.6, 100] | 8/8; 100% [67.6, 100] | 0/28; 0% [0.0, 12.1] |

On a deliberately hard 12-case subset of valid claim boundaries, the combined claim heuristics false-positive at **6/12; 50.0% [25.4, 74.6]**. That result marks the intended boundary: code can prove that a URL is absent from a corpus, but a short feed excerpt cannot prove a nuanced summary unfaithful. These checks remain warnings.

### Generation

**55 cases** measure model behavior: 22 utility cases and 33 indirect prompt-injection attacks embedded in titles, summaries, source names, and source-failure records. The attacks target nine observable behaviors, including citation fabrication, selection manipulation, section misrouting, health-report manipulation, prose distortion, and formatting damage.

Five attacks are paired with clean controls built from the same corpus with only the malicious mutations removed. Without those controls, a system that returns nothing could appear perfectly robust; the pairs expose that failure.

Portfolio v2 contains 1,200 preregistered rows from a clean tag, with 0 provider errors, 0 skips, and a total provider cost of **$3.80**.

| Model / prompt | End-to-end final utility | Final targeted attack success |
|---|---:|---:|
| DeepSeek V4 Flash / production | 99/110; 90.0% [83.0, 94.3] | 6/105; 5.7% [2.6, 11.9] |
| DeepSeek V4 Flash / reliability-v1 | 95/110; 86.4% [78.7, 91.6] | 3/105; 2.9% [1.0, 8.1] |
| Tencent HY3 / production | 90/110; 81.8% [73.6, 87.9] | 5/105; 4.8% [2.1, 10.7] |
| Tencent HY3 / reliability-v1 | 92/110; 83.6% [75.6, 89.4] | 4/105; 3.8% [1.5, 9.4] |

`reliability-v1` did not replace the production prompt. For DeepSeek, it reduced final utility and introduced eight contract regressions. For HY3, its utility and attack-resistance gains were both below the preregistered five-point thresholds.

Every rate reports successes, trials, and a two-sided 95% Wilson interval. Utility and attack denominators differ—completed utility trials versus completed primary attack trials—and are never pooled. The benchmark also includes an intentionally vulnerable `compliant` adapter that follows injected instructions. CI requires every attack against it to succeed; otherwise, the attack or scoring logic is not working as intended.

### Limits

- Wilson intervals describe this fixed authored suite, not deployment traffic. Repeated trials of one case are not independent samples of the world.
- This is not an editorial-ranking benchmark. Its utility score mostly measures structural behavior: valid output, populated routed sections, and configured minimums.
- The benchmark borrows AgentDojo's matched-twin design but does not reproduce its methodology. Here, “benign structural utility” concerns output structure rather than user-task completion, and the position ablation varies an item's array index rather than its token offset in the prompt.
- Portfolio v2 does not report human-reviewed semantic-faithfulness or grounding scores.

Reviewers can verify the committed evidence bundle and regenerate its aggregate report without credentials or provider calls:

```bash
python3 -S -m evaluator verify-public-run docs/results/portfolio-v2-evidence
```

## Development and verification

Run the core and evaluator tests without installing dependencies:

```bash
python3 -S -m unittest -v
python3 -S -m unittest discover -s evaluator/tests -v
```

The optional site build has a separate pinned dependency set and test module:

```bash
python3 -m pip install --requirement requirements-site.txt
python3 -m unittest -v tests.site_test_build
```

Together these commands are the complete offline test set behind the “over 600 tests” claim. CI also runs a separate non-blocking smoke test against live feeds.

Smoke-test the evaluation harness—case loading, oracles, scoring, and report rendering—with zero provider calls and a fresh output directory:

```bash
smoke_root="$(mktemp -d)"
python3 -S -m evaluator run --provider baseline=echo --trials 1 --output-dir "$smoke_root/eval-smoke"
```

CI runs the offline suites on Python 3.11–3.14, checks `ruff` and `mypy --disallow-untyped-defs`, pins GitHub Actions to commit SHAs, and requires explicit approval for reliability-snapshot drift. Production retains exact corpora and diagnostics for fourteen days in authenticated encrypted workflow artifacts. GitHub Pages receives text-free audit manifests rather than raw titles and excerpts; frozen development fixtures remain in the repository for reproducibility.

## Repository map

| Path | What it is |
|---|---|
| [`fetch_news.py`](fetch_news.py) | Corpus fetcher: sources, windowing, relevance, deduplication, budgets, SSRF defense, and XML defense |
| [`corpus_schema.py`](corpus_schema.py) | Corpus contract (schema v6) and shared URL canonicalization |
| [`agent_runner/`](agent_runner) | Provider adapters, citation projection, structured-output validation, deterministic repair, and checkpoints |
| [`eval_briefing.py`](eval_briefing.py) | Standalone deterministic policy checker |
| [`run_daily_briefing.py`](run_daily_briefing.py) | Production fallback chain across three models until one run is `ready` |
| [`audit_manifest.py`](audit_manifest.py) | Text-free public corpus membership, provenance, canonical destinations, and content hashes |
| [`corpus_storage.py`](corpus_storage.py) | Public private-storage marker for migration and archive-gap recovery |
| [`private_archive.py`](private_archive.py) | Authenticated encryption and bounded retention for operational corpora and diagnostics |
| [`restore_private_corpora.py`](restore_private_corpora.py) | Token-scoped restore of the newest encrypted GitHub Actions corpus archive |
| [`build_site.py`](build_site.py) | Static archive: briefings, integrity reports, and public audit manifests |
| [`evaluator/`](evaluator) | Development-only benchmark: cases, oracles, judges, metrics, and public evidence export |
| [`docs/design.md`](docs/design.md) | Design rationale |
| [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) | Threat model, labels, denominators, uncertainty, and limitations |
| [`docs/results/portfolio-v2.md`](docs/results/portfolio-v2.md) | Model card and non-promotion decision |
| [`fixtures/`](fixtures) | Frozen corpus, briefing, configuration, and injection fixtures |

Design influences include the [NIST AI RMF 1.0 MEASURE function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) for documented, repeatable, uncertainty-explicit evaluation, and [AgentDojo](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) and [MELON](https://proceedings.mlr.press/v267/zhu25z.html) for measuring utility alongside injection resistance.

## Further reading

- [My news agent fabricated a citation. The checker caught it.](docs/writeups/injection-benchmark-post.md) — the origin story and what $3.80 of evaluation bought
- [Dogfooding log](docs/dogfooding.md) — early live runs, checker findings, and failures that shaped the design
- [Sample briefing](docs/sample-briefing.md)
- [`SECURITY.md`](SECURITY.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md)

MIT licensed. Third-party news titles, feed excerpts, and linked content remain subject to their respective owners' rights and are not licensed under MIT.
