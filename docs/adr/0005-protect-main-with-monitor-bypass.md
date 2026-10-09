# 0005: Protect main with a deploy-key bypass for the grounding monitor

## Status

Proposed; external setup pending. Read-only GitHub checks on October 2, 2026
confirmed no rulesets, `protected: false`, legacy protection returning 404,
no deploy keys, and no `MONITOR_DEPLOY_KEY` secret. The committed workflow
alone does not activate protection.

## Context

`main` has no branch protection and no ruleset: `protected` is `false`,
`GET /rulesets` returns `[]`, and `GET /branches/main/protection` returns 404.
Anyone with write access can force-push to `main` or push commits that were
never reviewed.

Only one workflow writes to `main`. `.github/workflows/monitor-grounding.yml`
commits a one-line update to `docs/results/grounding-monitor.md` each week and
pushes it directly. `daily-briefing.yml` has `contents: read` and never pushes.
`ci.yml` and `claude-code-review.yml` are also read-only.

The maintainer chose to protect `main` and let the monitor keep pushing
directly. The alternative was to have the monitor open a pull request.

The default `GITHUB_TOKEN` cannot be listed as a bypass actor. The REST
ruleset API accepts these `bypass_actors[].actor_type` values: `Integration`,
`OrganizationAdmin`, `RepositoryRole`, `Team`, `DeployKey`, and `User`. The
rulesets guide lists roles, teams, GitHub Apps, and Dependabot as eligible
bypass actors. Neither source mentions `GITHUB_TOKEN` or `github-actions`. A
push that authenticates with `GITHUB_TOKEN` is therefore subject to the
ruleset, and the ruleset rejects it.

## Decision

Create one branch ruleset on `main` with these rules:

- **Require a pull request before merging, with 0 required approvals.** A
  solo maintainer cannot approve their own pull request, so any nonzero count
  would block every change. The trade-off: every change goes through a pull
  request and its checks, but nothing enforces a second reviewer.
- **Require the CI status checks.** These are the job names from CI run
  34818316515 on `main`: `tests (py3.11)`, `tests (py3.12)`, `tests (py3.13)`,
  `tests (py3.14)`, `lint`, and `offline reliability policy`. Do not require
  `end-to-end (live sources)`, because it hits live feeds and is marked
  `continue-on-error`. Leave the strict "branch must be up to date" policy
  off. Otherwise each weekly monitor commit would make every open pull request
  stale.
- **Block force pushes (`non_fast_forward`) and branch deletion.**
- **Do not require linear history.** The repository allows merge, squash, and
  rebase merges, and linear history would not add protection that the other
  rules lack. It can be added later with `required_linear_history`.

The monitor needs a bypass. The options, in order of preference:

1. **Deploy key (chosen).** Add a repository deploy key with write access and
   list it as a `DeployKey` bypass actor with `bypass_mode: "always"`. The API
   requires `actor_id: null` for this type and does not allow `pull_request`
   mode for it. The key works on this one repository only, needs no GitHub
   App, and grants no API access. Its private half is stored as the
   `MONITOR_DEPLOY_KEY` Actions secret, and only the monitor's push step uses
   it. The weakness: the bypass applies to every write deploy key on the
   repository, so no other write deploy key may be added.
2. **GitHub App installation token.** List the App as an `Integration` bypass
   actor and have the workflow mint a token with
   `actions/create-github-app-token`. The identity is narrower and the tokens
   are short-lived. The cost is creating and maintaining an App and two
   secrets for a one-line weekly commit.
3. **`RepositoryRole` admin bypass.** This works only if the push
   authenticates as the owner, which means storing the owner's PAT in Actions.
   That PAT acts as the owner on every repository it can reach. The same
   bypass would also let the owner's laptop push straight to `main`, which
   defeats the ruleset. Rejected.

The workflow changes in two places. `actions/checkout` now sets
`persist-credentials: false`. The commit step writes `MONITOR_DEPLOY_KEY` to a
temporary file, pins GitHub's published ed25519 host key, and pushes over SSH.
If the secret is missing, the step fails with an error. It does not fall back
to `github.token`, because the ruleset would reject that push.

## Maintainer commands

Run these in order. The deploy key and the secret must exist before the
ruleset is enabled. Otherwise the next monitor run fails.

```bash
ssh-keygen -t ed25519 -N '' -C 'news-briefing grounding monitor' -f monitor_deploy_key
gh repo deploy-key add monitor_deploy_key.pub --repo elanthus/news-briefing \
  --title 'grounding monitor' --allow-write
gh secret set MONITOR_DEPLOY_KEY --repo elanthus/news-briefing < monitor_deploy_key
rm monitor_deploy_key monitor_deploy_key.pub
```

Save the ruleset body as `ruleset.json`:

```json
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] }
  },
  "bypass_actors": [
    { "actor_id": null, "actor_type": "DeployKey", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "tests (py3.11)" },
          { "context": "tests (py3.12)" },
          { "context": "tests (py3.13)" },
          { "context": "tests (py3.14)" },
          { "context": "lint" },
          { "context": "offline reliability policy" }
        ]
      }
    }
  ]
}
```

Then create the ruleset:

```bash
gh api -X POST repos/elanthus/news-briefing/rulesets --input ruleset.json
```

## Verification after enabling

- Run `gh workflow run monitor-grounding.yml`, or wait for the Sunday
  14:00 UTC run. Confirm that the "Commit the updated weekly log" step either
  pushes or reports no change.
- Read back the ruleset rules, required check contexts, enforcement, and bypass
  actors through the GitHub API. Do not test protection with a real direct push.
- Open a pull request and confirm it cannot merge until the six required
  checks pass.
- Confirm the next scheduled `daily-briefing.yml` run succeeds. That workflow
  does not push to `main`, so the ruleset should not affect it.
- Run `gh api repos/elanthus/news-briefing/rulesets` and confirm it lists
  `protect-main`.

## Consequences

Direct pushes and force pushes to `main` stop, except for pushes made with the
monitor's deploy key. To rotate the key, repeat the key commands. The ruleset
does not change, because the bypass applies to deploy keys as a type. No
other write deploy key may be added while this bypass exists.

## References

- [REST API: create a repository ruleset](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset)
  (`bypass_actors[].actor_type`, `bypass_mode`, `actor_id: null` for DeployKey)
- [Creating rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
  (eligible bypass actors)
- [`.github/workflows/monitor-grounding.yml`](../../.github/workflows/monitor-grounding.yml)
- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
