# Contributing

Preserve the central contract: code constrains and checks the corpus. Models make judgments that code cannot prove.

## Before you start

For a bug, open an issue with reproduction steps, expected behavior, and actual behavior.
For a substantial feature or trust-boundary change, agree on the design and scope in an issue before implementation.
Report suspected vulnerabilities through the private process in [SECURITY.md](SECURITY.md).

## Development setup

Use Python 3.11 or later. The core pipeline needs no third-party Python packages.

```bash
git clone https://github.com/elanthus/news-briefing.git
cd news-briefing
python3 -S -m unittest -v
python3 -S -m unittest discover -s evaluator/tests -v
```

To install optional evaluator development tools in an isolated environment:

```bash
uv sync --project evaluator --group dev
```

The site tests require the pinned renderer. They are excluded from default discovery.
Run them when `build_site.py`, `prepare_publication.py`, or the generated site changes:

```bash
uv run --python 3.11 --with-requirements requirements-site.txt --no-project python -m unittest -v tests.site_test_build
```

Check evaluator loading, oracles, scoring, and report output without provider calls:

```bash
python3 -S -m evaluator checker
```

### One interpreter per run

Use `sys.executable` when one pipeline script starts another.
The child must use the parent's interpreter, version, and environment.
Do not select a child interpreter through a bare `python` or `python3` command on `PATH`.

Do not commit credentials, `.env` files, generated corpora, generated briefings, or evaluator run artifacts.

## Repository map

| Path | What it is |
|---|---|
| [`fetch_news.py`](fetch_news.py) | Corpus fetcher CLI: arguments, window resolution, output and exit status |
| [`news_fetch/`](news_fetch) | Fetcher modules: `config` (source loading), `destinations` (public-destination and DNS validation), `transport` (DNS-pinned HTTP client and header-deadline watchdog), `feed_xml` (XML defense), `sources/` (RSS, Hacker News, Reddit adapters), `relevance`, `curation` (deduplication, caps, budgets), `model` (item and fetch-result types), `collect` (fetch orchestration and source health) |
| [`sources.json`](sources.json) | RSS feeds, Hacker News queries, and subreddit list read by the fetcher |
| [`corpus_schema.py`](corpus_schema.py) | Corpus contract (schema v7) and shared URL canonicalization |
| [`briefing-config.json`](briefing-config.json) | Section targets, eligible corpus categories, and exclusion-log sizes |
| [`briefing_config.py`](briefing_config.py) | Loads and validates the trusted editorial configuration used for generation and checking |
| [`briefing-runner-prompt.md`](briefing-runner-prompt.md) | Production structured-output prompt used by `run_briefing.py` and the daily fallback chain |
| [`agent_runner/`](agent_runner) | Provider adapters, citation projection, structured-output validation, deterministic repair, checkpoints |
| [`run_briefing.py`](run_briefing.py) | Code-owned runner entry point: read a frozen corpus, project, generate, validate, repair, correct, finalize |
| [`eval_briefing.py`](eval_briefing.py) | Standalone deterministic policy checker |
| [`run_daily_briefing.py`](run_daily_briefing.py) | Production fallback chain across three models until one run is `ready` |
| [`daily_publish.py`](daily_publish.py) | Daily workflow steps: capture the window, restore corpora, generate and prepare scheduled or backfill reports |
| [`prepare_publication.py`](prepare_publication.py) | Validates a completed run and prepares its public archive input |
| [`publication_schema.py`](publication_schema.py) | Shared schema for publication review metadata and generation provenance |
| [`publication_failures.py`](publication_failures.py) | Allowlisted public failure explanations; raw provider and model text stays private |
| [`audit_manifest.py`](audit_manifest.py) | Text-free public corpus membership, provenance, canonical destinations, content hashes |
| [`private_archive.py`](private_archive.py) | Authenticated encryption and bounded retention for operational corpora and diagnostics |
| [`restore_private_corpora.py`](restore_private_corpora.py) | Token-scoped restore of the newest encrypted GitHub Actions corpus archive |
| [`build_site.py`](build_site.py) | Static archive: briefings, integrity reports, public audit manifests |
| [`site_metrics.py`](site_metrics.py) | Text-free `metrics.json` ledger (one row per report date, never truncated) and the operational metrics page |
| [`evaluator/`](evaluator) | Development-only benchmark: cases, oracles, judges, metrics, public evidence export |
| [`fixtures/`](fixtures) | Frozen corpus, briefing, configuration, and injection fixtures |

## Making a change

- Keep the core runtime compatible with Python 3.11 or later and the standard library.
- Add or update tests for behavior changes. Tests must not require network access.
- Treat fetched titles, summaries, sources, and URLs as untrusted input.
- Preserve limits, typed failures, and corpus-health reporting. Do not weaken checks to make a run pass.
- Update documentation when a public contract or trust boundary changes.
- Keep each pull request focused. Exclude unrelated formatting and refactoring.

Changes to production prompts, security controls, fixture labels, and evaluator oracles need specific evidence.
Describe the intended guarantee and add a regression case.
Distinguish code enforcement from heuristic checks and model judgments.
The production prompt is `briefing-runner-prompt.md`.
The historical direct-Markdown prompt is `evaluator/prompts/briefing-prompt.md`.

## Checks

Run the [repository gate](CLAUDE.md#required-checks).
CI runs the core and evaluator suites on Python 3.11 through 3.14.
It also runs lint, type checks, the evaluator checker, and the opt-in site tests.

The offline policy job verifies declared fixture counts and the committed checker snapshot.
Snapshot drift fails the gate. Use `--update-snapshot` only for an intentional, reviewed update.
CI also fetches public evidence bundles and verifies committed comparison output.
These downloads require network access and are outside the local gate.
The live-source smoke job is informational. It does not block a pull request.

The maintainer's [pre-push configuration](.agentic-preflight.toml) also runs site tests with the pinned dependency.
Outside contributors do not need Agentic Preflight.
For live evaluations, record the provider, exact model, prompt version, trials, sampling controls, and incomplete or failed runs.

## Documentation style

Use [ASD-STE100 Issue 9](https://www.asd-ste100.org/) as the writing reference for maintained documentation.
Write one instruction per sentence. Use the imperative form for procedures and active voice where possible.
Limit procedural sentences to 20 words and descriptive sentences to 25 words.
Use one term for one concept. Define necessary software terms and preserve exact code identifiers.

Check claims against code, configuration, tests, and recorded evidence before rewriting them.
Preserve commands, schemas, quotations, and measured values unless a verified correction is required.
Do not restyle historical reports, archived runs, prompts, fixtures, or raw evidence.
This repository uses an STE style. It does not claim complete dictionary conformity or certified compliance.

## Pull requests

Pull requests receive the [automated Claude review](.github/workflows/claude-code-review.yml).
Include these details in the description:

- The problem and resulting behavior.
- Changed guarantees, limitations, or compatibility requirements.
- The checks completed and their results.
- The related issue, if available.

Use clear, imperative commit subjects. Make the change understandable without unrelated conversation history.
Contributions use the project's [MIT License](LICENSE).
