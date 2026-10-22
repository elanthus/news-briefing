# 0006: Protect main without a monitor bypass

## Status

Accepted and externally enforced on October 10, 2026.
Supersedes [ADR 0005](0005-protect-main-with-monitor-bypass.md).
The ruleset was read again on October 22, 2026; the controls below remained active.

## Context

The weekly grounding monitor was the only scheduled workflow that pushed to `main`.
Its retirement removed the reason for a deploy-key bypass.
Explicit benchmark grounding experiments remain supported.
Daily publication deploys GitHub Pages without pushing source changes.

## Decision

The monitor workflow was disabled before branch protection was enabled.
Its workflow and monitor-only code were then removed through a pull request.
The API confirmed workflow `351935607` as `disabled_manually`, with no active run, before ruleset creation.

The [protect-main ruleset](https://github.com/elanthus/news-briefing/rules/24857963) targets exactly `refs/heads/main`.
It requires a pull request, blocks deletion and force pushes, and has no bypass actors.
It requires zero approvals, so a solo maintainer can merge their own reviewed pull request.
This does not enforce independent human review.

Required checks use the GitHub Actions integration:

- `tests (py3.11)`.
- `tests (py3.12)`.
- `tests (py3.13)`.
- `tests (py3.14)`.
- `lint`.
- `offline reliability policy`.

The informational `end-to-end (live sources)` job is not required.
Strict up-to-date checking and linear history are not required.
No new credential, deploy key, or monitor bypass was created.

## Verification and maintenance

The original ruleset read-back confirmed enforcement, branch scope, required checks, zero approvals, blocked deletion/force pushes, and no bypass actors.
The branch API reported `protected: true`.
PR #260 checks at `3b93f442a59e167ae4c4d2aeae40ab5008eb9d42` matched required contexts and GitHub Actions integration `15368`.
No direct-push test was performed.

Inspect current external state:

```bash
gh api repos/elanthus/news-briefing/rulesets/24857963
gh api repos/elanthus/news-briefing/branches/main --jq '{name,protected}'
```

Update required contexts when CI job names change.
Repository documentation and configuration alone cannot enforce GitHub protection.
