# Security policy

## Supported versions

This project is not distributed as a versioned package. Security fixes are made on
the `main` branch, which is the only supported version.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Instead, use
[GitHub's private vulnerability reporting](https://github.com/elanthus/news-briefing/security/advisories/new)
and include:

- a description of the vulnerability and its impact;
- the affected files, configuration, and environment;
- the smallest reproducible example you can provide; and
- any suggested remediation, if you have one.

Do not include credentials, personal data, or live exploit targets in the report.
Please allow time to investigate and coordinate a fix before public disclosure.

## Security scope

Useful reports include vulnerabilities in the repository's code or default
configuration, such as:

- bypasses of public-destination validation, DNS pinning, or redirect checks;
- ways to introduce a briefing URL that is absent from the fetched corpus;
- parser or resource-exhaustion flaws that violate the documented input bounds;
- credential exposure from the runner or optional evaluator;
- bypasses of the runner's OpenRouter or Claude Code action-tool policy, citation
  projection, checkpoint integrity, or fail-closed provider-event validation; and
- prompt-injection paths that cross a boundary the project claims to enforce.

The following are documented limitations rather than vulnerabilities by
themselves:

- inaccurate ranking or summaries produced by a model;
- prompt injection that changes selection or prose without escaping the corpus
  URL allowlist;
- a Codex built-in tool being present but unused inside the documented empty,
  read-only sandbox boundary (actual non-message/reasoning trace events are in
  scope);
- malicious, inaccurate, unavailable, or rate-limited third-party feeds; and
- denial of service against the upstream public sources.

The README's [what code enforces, and what it doesn't](README.md#what-code-enforces-and-what-it-doesnt)
table states the trust boundaries and guarantees; [`docs/design.md`](docs/design.md)
covers the reasoning behind them. Reports showing that an implementation does not
meet one of those stated guarantees are in scope.
