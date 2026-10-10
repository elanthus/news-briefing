# 0006: Protect main without a monitor bypass

## Status

Accepted and externally enforced October 10, 2026. Supersedes
[ADR 0005](0005-protect-main-with-monitor-bypass.md).

## Context

The weekly grounding monitor was the only scheduled workflow that pushed to
`main`. Its retirement removes the reason for the proposed deploy-key bypass.
Explicit benchmark grounding experiments remain supported; daily publication
uses GitHub Pages and does not push source changes.

## Decision

Disable the monitor workflow before enabling protection, then remove its
workflow and monitor-only code through a pull request. GitHub's API confirmed
workflow `351935607` was `disabled_manually` with no active run before the
ruleset was created.

The active [protect-main ruleset](https://github.com/elanthus/news-briefing/rules/24857963)
targets exactly `refs/heads/main`. It requires a pull request with zero required
approvals, blocks force pushes and deletion, and has no bypass actors. Zero
approvals permits a solo maintainer to merge their own reviewed pull request;
it does not enforce an independent human review.

Required checks are bound to the GitHub Actions integration:

- `tests (py3.11)`
- `tests (py3.12)`
- `tests (py3.13)`
- `tests (py3.14)`
- `lint`
- `offline reliability policy`

The informational `end-to-end (live sources)` job is excluded. Strict
up-to-date checking and linear history are not required. No new credential,
deploy key, or monitor bypass was created.

## Verification and maintenance

Read-back of ruleset `24857963` confirmed active enforcement, the exact branch,
all required contexts, zero approvals, deletion and force-push blocking, and
an empty bypass list. The branch API reported `protected: true`. PR #260's
checks at `3b93f442a59e167ae4c4d2aeae40ab5008eb9d42` matched every required
context and GitHub Actions integration `15368`. No direct-push test was used.

To inspect the current external state:

```bash
gh api repos/elanthus/news-briefing/rulesets/24857963
gh api repos/elanthus/news-briefing/branches/main --jq '{name,protected}'
```

When CI job names change, update the ruleset's contexts as part of that
rollout. A committed configuration or this ADR alone cannot enforce GitHub
repository protection.
