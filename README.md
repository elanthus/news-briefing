<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/favicon-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/favicon-light.png">
    <img alt="" src="docs/images/favicon-light.png" width="72" height="72" align="center">
  </picture>
  news briefing
</h1>

**An LLM writes my daily AI news briefing. It chooses the stories and writes the summaries — code decides what is recent, what may be cited, and whether the result may be published.**

[![CI](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml/badge.svg)](https://github.com/elanthus/news-briefing/actions/workflows/ci.yml)

Live site → <https://elanthus.github.io/news-briefing/> · Every published run links an [integrity report](docs/images/auditor-report.png) to a text-free audit manifest of the corpus it was generated from.

---

## The one-paragraph version

A GitHub Actions cron job fetches ~150–200 news items from RSS, Hacker News, and Reddit into a closed, schema-validated corpus. A model ranks and summarizes that corpus — and nothing else. It never sees a URL, never gets a tool, and never chooses what "recent" means. A deterministic checker then decides whether the result may be published: every citation must resolve to an item that was actually in the corpus, every section must be filled from eligible categories, every failed source must be reported. Output that fails the gate is not published — it is quarantined behind a public status chip that links to the findings. The pipeline itself runs on the Python standard library alone — the one pinned dependency is the Markdown renderer that builds the static site — with over 600 tests, and it has been publishing itself daily since August 2026.

The failure that motivated all of it is documented: an early run [fabricated a citation](docs/writeups/injection-benchmark-post.md), the checker caught it, and I built the rest of the system around the idea that anything code can decide, code should decide.

| Reader view | Auditor view |
|---|---|
| ![Reader view of the daily briefing](docs/images/reader-view.png) | ![Per-run integrity report](docs/images/auditor-report.png) |
| Every 🔗 is verified against the corpus before publication. | Each date links to its integrity report: disposition, automated repair log, and corpus health. |

---

## Why this is not another "AI news summarizer"

Most LLM content pipelines are prompt → model → publish, with the prompt carrying the guarantees. This one moves every checkable guarantee out of the prompt and into code.

| Question | Who answers it here |
|---|---|
| Is this item inside the publication window? | Code — the fetcher applies the cutoff before the model exists |
| Where does this link point? | Code — the model never receives a URL, only opaque `citation_0007` handles |
| Is this citation real? | Code — canonical URL comparison against the frozen corpus |
| Is this story allowed in this section? | Code — per-section citation enums plus an independent validator |
| Did a source silently die? | Code — every request's outcome is recorded and must be declared in the briefing |
| Is this summary faithful to the article? | **Nobody, deterministically.** Heuristic warnings only, and the README says so |

That last row is the point. The project publishes what it can prove and refuses to launder the rest.

### The citation-projection trick

Before generation, each corpus item is projected into untrusted evidence text plus an opaque identifier. The real URLs live in a code-owned map the model never receives. Each section's JSON Schema enumerates only the citation IDs eligible for that section, and any free-form output field containing a web destination fails validation.

This converts URL grounding from *"check the model's links afterwards"* into *"the model cannot author a link that survives validation."* It can still emit a URL into a prose field — nothing stops it from typing characters — but that field is checked against the same code-owned allowlist, and a model-authored destination fails the run rather than reaching a reader. Rendering then expands each selected ID to all its code-owned destinations — so a Hacker News story automatically carries its discussion link, and cannot omit it.

---

## Architecture

![Runtime pipeline: fetch, project, generate, validate, repair, correct, gate, publish](docs/images/runtime-pipeline.svg)

```text
fetch_news.py      →  corpus.json (schema v6, validated on write)
agent_runner/      →  project → generate → validate → repair → correct → gate
eval_briefing.py   →  the deterministic oracle, usable standalone
prepare_publication.py / build_site.py  →  static site + per-run integrity report
```

Four design decisions worth stealing:

**1. A shared contract module, not two implementations.** [`corpus_schema.py`](corpus_schema.py) owns field shapes, counter semantics, budget limits, *and* `canonicalize_url`. The fetcher deduplicates with that function and the checker decides citation membership with it — a shared implementation means a trailing slash cannot identify one article during dedup and a different article during citation checking. It also knows that a URL *location* is not an article identity: `news.ycombinator.com/item` addresses every HN story there is, so `item?id=123 → item?id=999` stays an ungrounded citation rather than being excused as a cosmetic rewrite.

**2. Deterministic repair before spending model budget.** When every blocking finding is mechanically fixable — a globally repeated citation, an over-limit section — a code-owned normalizer fixes it and re-enters validation without spending a correction pass. The model's one correction pass is reserved for findings only a model can fix. Repairs are recorded in the manifest and surfaced publicly (`Published after automated repair (6 actions)`).

**3. "Complete" is not "correct."** The checkpoint manifest tracks lifecycle (`running` / `complete` / `failed`) separately from publication disposition (`ready` / `review_required` / `rejected` / `no_result`). A degraded fetch reduces coverage without making the run a failure. An unknown citation rejects it outright. Only `ready` writes the published file.

**4. Fail-closed tool policy, per provider.** OpenRouter gets no tool definitions and any returned tool call is a hard failure. Claude Code CLI runs in safe mode with `StructuredOutput` as the only exposed *and* permitted tool. Codex CLI ignores user config, disables shell/web/multi-agent/image tools, starts in an empty temp directory under a read-only sandbox, and rejects any trace item that is not reasoning or the final message — because Codex has no single documented "remove all tools" flag, so the sandbox is defense in depth.

Plus a detail I enjoyed more than I should have: **the fetcher does its own SSRF defense without an HTTP dependency.** Source URLs are syntax-checked, each hostname is resolved exactly once, every answer must be globally routable, and the socket connects to one of those captured addresses while TLS still authenticates the original name. Redirects repeat the whole process per hop. That closes direct, redirect, and DNS-rebinding paths to loopback and metadata endpoints. Untrusted feed XML is parsed with an Expat declaration callback that rejects every `DOCTYPE`, closing entity-expansion amplification without pulling in `defusedxml`.

---

## Evaluation: the part I'd actually want reviewed

[`evaluator/`](evaluator/) is a development-only benchmark measuring two separate systems with two separate denominators, never combined into one score.

**81 offline cases** measure the deterministic checker and feed parser with no credentials. All 81 have completed blinded model review; repository-owner adjudication resolved historical disagreements. These LLM reviews helped get the repository and benchmark running, but no case has completed independent human review; full human review is recommended before production use. Current committed snapshot:

| Component | Cases | Precision | Recall | False-positive rate |
|---|---:|---:|---:|---:|
| Checker | 69 | 42/48; 87.5% [75.3, 94.1] | 42/54; 77.8% [65.1, 86.8] | 6/1671; 0.36% [0.16, 0.78] |
| Feed parser | 12 | 8/8; 100% [67.6, 100] | 8/8; 100% [67.6, 100] | 0/28; 0% [0.0, 12.1] |

On a deliberately hard 12-case subset of *valid* claim boundaries, the combined claim heuristics false-positive at **6/12; 50.0% [25.4, 74.6]**. That number is published because it defines the boundary: code can prove a URL is absent from a corpus; a 400-character feed excerpt cannot prove a nuanced summary is unfaithful. Those checks are warnings, and the system treats them as warnings.

**55 generation cases** measure model behavior — 22 utility cases and 33 indirect prompt-injection attacks placed in the fields a news pipeline must treat as data: titles, summaries, source names, source-failure records. Attacks target nine observable behaviors (citation fabrication and alteration, duplicate citations, selection promotion/suppression, section misrouting, health-report manipulation, prose distortion, formatting damage). Five have **matched clean twins** built from the same pristine corpus with the mutations removed — because a system that outputs nothing scores as perfectly robust, and that has to be visible.

Portfolio v2: 1,200 preregistered rows from a clean tag, 0 provider errors, 0 skips, **$3.80**.

| Model / prompt | End-to-end final utility | Final targeted attack success |
|---|---:|---:|
| DeepSeek V4 Flash / production | 99/110; 90.0% [83.0, 94.3] | 6/105; 5.7% [2.6, 11.9] |
| DeepSeek V4 Flash / reliability-v1 | 95/110; 86.4% [78.7, 91.6] | 3/105; 2.9% [1.0, 8.1] |
| Tencent HY3 / production | 90/110; 81.8% [73.6, 87.9] | 5/105; 4.8% [2.1, 10.7] |
| Tencent HY3 / reliability-v1 | 92/110; 83.6% [75.6, 89.4] | 4/105; 3.8% [1.5, 9.4] |

Every rate above reports successes, trials, and a two-sided 95% Wilson interval, because that is what the evaluator emits — utility and attack denominators differ (completed utility trials versus completed primary attack trials) and are never pooled. There is a deliberately-bad `compliant` baseline adapter that obeys every injected instruction; CI asserts it scores 100% attack success, because if the strategy designed to lose doesn't lose, the benchmark is broken rather than the model.

**The candidate prompt was not promoted.** It failed its preregistered rules for both models. Writing that down was the whole exercise.

### What this repo explicitly does not claim

- Not a deployment-traffic estimate. Wilson intervals describe this fixed authored suite; five trials of one case are not five samples of the world.
- Not a ranking-quality benchmark. Utility here is largely structural: valid output, non-empty routed sections, declared floors.
- Not an AgentDojo reproduction — the matched-twin idea is borrowed; "benign structural utility" is not AgentDojo's user-task utility, and the ablation's array position is not token position.
- **No meaning-preservation or human-grounding rate is published for portfolio v2.** Those 180 semantic forms are unjudged. A blank cell beat substituting another model's confidence for human review.
- Portfolio v1's model metrics came from a dirty source tree and are retained only as a dated historical snapshot.

Reviewers can verify the published bundle and regenerate the aggregate report with no credentials and no provider calls:

```bash
python3 -m evaluator verify-public-run docs/results/portfolio-v2-evidence
```

---

## Try it

Python 3.11+. The pipeline itself has no runtime dependencies. The default test suite exercises that standard-library-only path with no setup:

```bash
python3 -m unittest -v
```

Static-site generation is optional and has its own test module and pinned Markdown renderer. Install it only if you want to build or test the site locally:

```bash
python3 -m pip install --requirement requirements-site.txt
python3 -m unittest -v tests.site_test_build
```

The evaluator has a separate standard-library-only test suite:

```bash
python3 -S -m unittest discover -s evaluator/tests -v
```

The core, evaluator, and static-site commands above are the complete offline test set behind the “over 600 tests” claim.

Every command below runs without the site renderer.

Fetch a live corpus and look at what the model would be allowed to see:

```bash
python3 fetch_news.py --hours 24 --markdown
```

Run the checker against the committed frozen fixtures — this is the whole oracle, offline, in one command:

```bash
python3 eval_briefing.py --corpus fixtures/corpus-2026-08-09.json --briefing fixtures/briefing-2026-08-09.md --config fixtures/briefing-config-2026-08-09.json
```

Point it at a poisoned corpus and watch it refuse:

```bash
python3 eval_briefing.py --corpus fixtures/injection-corpus.json --briefing fixtures/injection-briefing.md --config fixtures/injection-config.json
```

Smoke-test the full evaluation harness — oracles, scoring, report rendering — with zero provider calls:

```bash
python3 -m evaluator run --provider baseline=echo --trials 1 --output-dir /tmp/eval-smoke
```

Run an end-to-end briefing through the OpenRouter API (requires `OPENROUTER_API_KEY`):

```bash
python3 run_briefing.py --provider openrouter --model your/model-id --output briefing.md
```

`--provider` also accepts `claude-code-cli` and `codex-cli`. Those providers use an existing signed-in Claude Code or Codex CLI session and do not require an API key in this project. The benchmark is not hard-wired to the models in the committed results: any OpenRouter model id runs the same 55-case suite, and `--prompt` evaluates any prompt file against the production one under the same preregistered comparison rules.

---

## Repository map

| Path | What it is |
|---|---|
| [`fetch_news.py`](fetch_news.py) | Corpus fetcher: sources, windowing, relevance, dedup, caps, budgets, SSRF and XML defenses |
| [`corpus_schema.py`](corpus_schema.py) | The corpus contract (schema v6) and shared URL canonicalization |
| [`agent_runner/`](agent_runner) | Provider adapters, citation projection, structured-output validation, deterministic repair, verified checkpoints |
| [`eval_briefing.py`](eval_briefing.py) | The deterministic checker, usable standalone against any corpus/briefing pair |
| [`run_daily_briefing.py`](run_daily_briefing.py) | Production fallback chain across three models until one run is `ready` |
| [`audit_manifest.py`](audit_manifest.py) | Text-free public corpus membership, provenance, canonical destinations, and content hashes |
| [`private_archive.py`](private_archive.py) | Authenticated encryption and bounded 14-day retention for exact operational corpora and diagnostics |
| [`restore_private_corpora.py`](restore_private_corpora.py) | Token-scoped restore of the newest encrypted GitHub Actions corpus archive |
| [`build_site.py`](build_site.py) | Static archive: briefings, per-run integrity reports, and public audit manifests |
| [`evaluator/`](evaluator) | Development-only benchmark: cases, oracles, judges, metrics, public evidence export |
| [`docs/design.md`](docs/design.md) | Why each stage works the way it does |
| [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) | Threat model, labels, denominators, uncertainty, limitations |
| [`docs/results/portfolio-v2.md`](docs/results/portfolio-v2.md) | Model card and non-promotion decision |
| [`fixtures/`](fixtures) | Frozen corpus, briefing, config, and injection fixtures for controlled comparison |

## Engineering practices

- **Over 600 tests**, all offline — no network calls, no runtime fixture downloads. CI runs them on Python 3.11, 3.12, 3.13, and 3.14.
- **A credential-free reliability gate in CI** that runs the checker suite and fails on case-count drift or snapshot changes without review approval.
- `ruff` and `mypy --disallow-untyped-defs` across the pipeline and runner; the evaluator is type-checked under its own config.
- All GitHub Actions pinned to commit SHAs; Dependabot enabled.
- A live end-to-end smoke job that hits real feeds and is `continue-on-error` — a slow news day must never fail a pull request.
- Fourteen days of exact corpora retained only in authenticated encrypted workflow artifacts so historical backfill replays stored inputs rather than reconstructing them from retention-limited live feeds. GitHub Pages receives text-free audit manifests instead of raw source excerpts.

Design influences are cited rather than gestured at: the [NIST AI RMF 1.0 MEASURE function](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) for documented, repeatable, uncertainty-explicit evaluation, and [AgentDojo](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf) / [MELON](https://proceedings.mlr.press/v267/zhu25z.html) for measuring utility alongside injection resistance.

## Further reading

- [My news agent fabricated a citation. The checker caught it.](docs/writeups/injection-benchmark-post.md) — the origin story and what $3.80 of evaluation bought
- [Dogfooding log](docs/dogfooding.md) — real runs, real findings, reproducible commands
- [Sample briefing](docs/sample-briefing.md)
- [`SECURITY.md`](SECURITY.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md)

MIT licensed. Third-party news titles, feed excerpts, and linked content remain subject to their respective owners' rights and are not licensed under MIT.
