# Security policy

## Supported versions

Only `main` receives security fixes. This project is not a versioned package.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/elanthus/news-briefing/security/advisories/new).
Do not report a suspected vulnerability in a public issue.
Include these details:

- The vulnerability and its impact.
- The affected files, configuration, and environment.
- A minimal reproducible example.
- A suggested fix, if available.

Exclude credentials, personal data, and live exploit targets.
Allow time for investigation and a coordinated fix before public disclosure.

## Security scope

Report failures in the repository's code or default configuration that cross a claimed boundary.
These boundaries include:

- Public-destination validation, DNS pinning, and redirect checks in `news_fetch/destinations.py` and `news_fetch/transport.py`.
- Citation membership in the fetched corpus.
- Parsing and resource bounds, including XML checks in `news_fetch/feed_xml.py`.
- Credentials used by the fetcher, runner, archive, and evaluator.
- OpenRouter and Claude Code action-tool restrictions.
- Citation projection and artifact verification at review, repair, and publication handoffs.
- Evaluator checkpoint integrity and provider-event validation.
- Prompt injection that escapes an enforced boundary.

Credentials include `OPENROUTER_API_KEY`, `CORPUS_ARCHIVE_PASSPHRASE`, and the optional `SCRAPECREATORS_API_KEY`.
The fetcher sends the ScrapeCreators key only to its approved HTTPS API origin. It does not follow redirects with that key.
See [Reddit fetching](docs/design.md#fetching).

Some pre-#291 evidence contains escaped text that the current projection decodes differently.
`load_verified_run` uses `redecode_redacted_text` only for recorded strings that already contain the destination marker.
It masks no additional content. It still requires equality with the current projection.
This compatibility path does not change the production projection grammar.

The following conditions alone are documented limitations:

- Model ranking and summary errors.
- Injection that changes selection or prose while staying inside the corpus URL allowlist.
- An unused Codex built-in tool inside the documented empty, read-only sandbox.
- Malicious, inaccurate, unavailable, or rate-limited upstream feeds.
- Denial of service against public upstream sources.

Actual Codex trace events outside permitted messages and reasoning remain in scope.
See the [enforcement table](README.md#what-code-enforces-and-what-it-doesnt) and [design notes](docs/design.md).
An implementation that fails a stated guarantee is in scope.
