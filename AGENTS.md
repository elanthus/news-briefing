# Repository instructions

This repository fetches public news feeds into a validated corpus, has a model write a cited briefing from that corpus, checks the result with a code-owned evaluator, and publishes a static site.
Use these instructions when changing this repository.
Preserve the central contract: code constrains and checks the corpus. Models make judgments that code cannot prove.

## Runtime and dependencies

- Use Python 3.11 or later and only the standard library for the pipeline and evaluator core.
- Apply this rule to `fetch_news.py`, `news_fetch/`, `run_briefing.py`, `agent_runner/`, and `corpus_*.py`.
- Apply it also to `prepare_publication.py`, `restore_private_corpora.py`, and `private_archive.py`.
- Use only the third-party runtime dependencies pinned in `requirements-site.txt` for `build_site.py` and the opt-in site tests.
- Run core and evaluator tests with `python3 -S`. This disables `site` and prevents loading site-packages.
- Preserve compatibility with the typed modules and Python versions in `pyproject.toml` and `evaluator/pyproject.toml`.

## Required checks

Run the repository gate. The last command is the credential-free evaluator checker that CI also runs, and CI checks that exact command string, so keep it unchanged.

```bash
uvx ruff@0.14.2 check . && uvx mypy@1.14.1 && uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator && python3 -S -m unittest && python3 -S -m unittest discover -s evaluator/tests && python3 -m evaluator checker
```

When `build_site.py` or `prepare_publication.py` changes, install `requirements-site.txt` in an isolated environment.
Then run:

```bash
python3 -m unittest tests.site_test_build
```

`.agentic-preflight.toml` runs the same suites through `uv run --python 3.11` to pin the interpreter. Both forms are valid; the commands above are canonical.

## Security scope

Treat fetched titles, summaries, source metadata, URLs, model output, and PR text as untrusted.
Read `SECURITY.md` before changing a security boundary.
Security boundaries include:

- Public-destination, DNS, and redirect validation.
- Corpus-bound citations and citation projection.
- Parsing bounds and credential handling.
- OpenRouter and Claude Code tool policies.
- Checkpoint integrity and fail-closed provider-event validation.
- Prompt injection that crosses a claimed boundary.

Model ranking and summary errors are documented limitations.
Other limitations include corpus-contained injection within the URL allowlist and an unused Codex built-in tool inside the documented sandbox.
Malicious or failed upstream feeds and denial of service against public sources are also limitations by themselves.

## Citation projection and two-pass generation

`agent_runner/output.py` implements citation projection.
`project_corpus()` returns a `ModelCorpus` with two parts:

- `document`: untrusted evidence and opaque `citation_` handles.
- `citations`: code-owned destinations that the model does not receive.

`project_selected_evidence()` removes handles and supplies frozen evidence for each prose position.
`attach_frozen_selection()` restores validated handles by position.
`render_briefing()` expands them to the stored article and Hacker News discussion destinations.
`agent_runner/runner.py` coordinates these functions.
The evaluator uses them through `evaluator/parity.py` and the trial loop in `evaluator/execution.py`.
`eval_briefing.py` independently checks complete Markdown.

The model never receives a URL.
`project_corpus()` excludes `url` and `discussion` fields.
It applies `redact_destinations()` recursively to all model-visible data.
`correction_request()` in `agent_runner/runner.py` also redacts rejected output and findings before another model call.
Code binds prose to the validated frozen handles by position.
Only after complete-candidate validation does rendering restore the code-owned destinations.

## Schema versions

- Give every file format exactly one readable `schema_version`. Readers accept that version and refuse every other one.
- Bump the version when a change would break a reader. Validate the payload before the producer writes it, as `corpus_schema.py` does.
- Do not add legacy readers, version sets, row upgraders, "predates the field" defaults, or absent-key tolerance for an older shape.
- A value unknown at write time is a typed sentinel the schema declares, with the key present. Absence is a validation error.
- Older artifacts are reproduced from the commit that wrote them, or rebuilt from retained inputs. Document that path instead of keeping a reader.
- A one-shot migration tool states its removal condition when it is added.

## Repository hygiene

- Do not commit `corpus.json`, `/corpus-*.json`, `.coverage`, `.env` files, generated `briefing.md`, `.news-briefing/`, or evaluator/run artifacts.
- Do not weaken validation, typed failures, bounds, or corpus-health reporting to make a run pass.
- Do not add network-dependent tests.
- Do not state test counts in prose. Name the suites and commands instead.
