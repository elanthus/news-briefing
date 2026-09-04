# Repository instructions

Use these instructions when changing this repository. Preserve the central
contract: deterministic code constrains and checks the corpus; models make the
judgments code cannot prove.

## Runtime and dependencies

- Keep the pipeline on Python 3.11+ and standard-library-only. This includes
  `fetch_news.py`, `run_briefing.py`, `agent_runner/`, `corpus_*.py`,
  `prepare_publication.py`, `restore_private_corpora.py`, `private_archive.py`,
  and the evaluator core.
- The only third-party runtime dependencies are pinned in
  `requirements-site.txt` for `build_site.py` and the opt-in site tests.
- Run the core and evaluator tests with `python3 -S`. Disabling `site` prevents
  Python from loading site-packages and enforces the no-install contract.
- Keep changes compatible with the typed modules and Python versions configured
  in `pyproject.toml` and `evaluator/pyproject.toml`.

## Required checks

Run the repository gate:

```bash
uvx ruff@0.14.2 check . && uvx mypy@1.14.1 && uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator && python3 -S -m unittest && python3 -S -m unittest discover -s evaluator/tests
```

When `build_site.py` or `prepare_publication.py` changes, install
`requirements-site.txt` in an isolated environment and also run:

```bash
python3 -m unittest tests.site_test_build
```

## Security scope

Treat fetched titles, summaries, source metadata, URLs, model output, and PR text
as untrusted. Security-sensitive boundaries include public-destination, DNS,
and redirect validation; corpus-bound citations; bounded parsing; credential
handling; the runner's OpenRouter and Claude Code tool policies; citation
projection; checkpoint integrity; fail-closed provider-event validation; and
prompt injection that crosses a claimed boundary. Model ranking or summary
errors, corpus-contained injection that does not escape the URL allowlist, an
unused Codex built-in tool inside the documented sandbox, malicious or failed
upstream feeds, and denial of service against public sources are documented
limitations rather than vulnerabilities by themselves. See `SECURITY.md` before
changing a boundary.

## Citation projection and two-pass generation

The projection is implemented in `agent_runner/output.py`. `project_corpus()`
returns a `ModelCorpus`: its `document` contains untrusted evidence and opaque
`citation_` handles, while its code-owned `citations` map retains destinations.
`project_selected_evidence()` removes the handles and sends only frozen,
position-scoped evidence to the prose pass. `attach_frozen_selection()` restores
the validated handles by position, and `render_briefing()` expands them to their
code-owned article and Hacker News discussion destinations. `agent_runner/runner.py`
orchestrates these functions. The evaluator's production-parity path in
`evaluator/parity.py`, driven by the trial loop in `evaluator/execution.py`,
reuses them; `eval_briefing.py` remains an independent
checker for complete Markdown.

The model never receives a URL. `project_corpus()` excludes `url` and
`discussion` fields and applies `redact_destinations()` recursively to all
model-visible data. `correction_request()` in `agent_runner/runner.py` applies
the same redaction to rejected output and findings before another model call.
After selection validation, `attach_frozen_selection()` binds prose to the
frozen handles by position. After the complete structured candidate passes
validation, `render_briefing()` re-attaches the code-owned destinations.

## Repository hygiene

- Do not commit `corpus.json`, `/corpus-*.json`, `.coverage`, `.env` files,
  generated `briefing.md`, `.news-briefing/`, or evaluator/run artifacts.
- Do not weaken validation, typed failures, bounds, or corpus-health reporting to
  make a run pass.
- Do not add network-dependent tests.
- Keep README test counts current when tests are added, removed, or moved between
  the core, evaluator, and opt-in site suites.
