# Scheduler

A Cloudflare Worker that starts the daily briefing and its watchdog with `workflow_dispatch`.
GitHub's `schedule:` trigger has started the daily run four to ten hours late.
A dispatch starts within seconds.

The Worker only starts runs.
`briefing_watchdog.py` and `daily_publish.py` still decide whether a date needs a run.

| Cron (UTC) | Workflow | Inputs |
|---|---|---|
| `30 13 * * *` | `daily-briefing.yml` | `mode: single-day` |
| `41 * * * *` | `daily-briefing-watchdog.yml` | `dry_run: false` |

## Token

The Worker needs a GitHub fine-grained personal access token in the `GITHUB_TOKEN` secret.

- Repository access: only `news-briefing`.
- Permissions: Actions, read and write. Nothing else.

Anyone holding the token can start and cancel workflow runs, and each daily run spends model credits.
It cannot read secrets or push code.

When the token expires, dispatches fail with HTTP 401 and the Worker logs show the failure.
The watchdog's own GitHub schedule still runs, so a missed date eventually gets an issue.
Renew the token before it expires and store the new value with the `secret put` command below.

## Deploy

Run these commands from this directory.
`wrangler.toml` names the Worker, so commands run elsewhere fail with "required worker name is missing".

```bash
cd scheduler
```

```bash
npm test
```

```bash
npx wrangler@4.167.0 deploy
```

```bash
npx wrangler@4.167.0 secret put GITHUB_TOKEN
```

`secret put` prompts for the value, so the token never appears in shell history.

## Check that it works

The Cloudflare dashboard lists each cron invocation under Workers & Pages → `news-briefing-scheduler` → Logs.
A successful dispatch logs `dispatched <workflow> (HTTP 204 …)`, and the run appears in the repository's Actions tab within seconds.

To test without waiting for the cron, run the Worker locally against GitHub.
This starts a real run.

```bash
npx wrangler@4.167.0 dev --test-scheduled
```

Then request `http://localhost:8787/__scheduled?cron=41+*+*+*+*` to dispatch the watchdog.
The watchdog does nothing when today's date is already recorded.
Local dev reads the token from `scheduler/.dev.vars`, which is ignored by git.
