// Starts the news-briefing workflows with workflow_dispatch on a reliable clock.
//
// GitHub's own `schedule:` trigger has started the daily run four to ten hours
// late and fired the hourly watchdog about every eight hours. A dispatch starts
// within seconds. This Worker only triggers runs. Every decision about whether
// a date needs a run stays in briefing_watchdog.py and daily_publish.py.

const REPOSITORY = "elanthus/news-briefing";
const API_ROOT = "https://api.github.com";
// Matches briefing_watchdog.py.
const API_VERSION = "2026-03-10";
const USER_AGENT = "news-briefing-scheduler/1.0";
const MAX_ATTEMPTS = 3;
const RETRY_DELAY_MS = 30_000;
const MAX_LOGGED_BODY = 500;

// One dispatch per cron expression in wrangler.toml. Keep the two in sync;
// test/index.test.js checks that they match.
export const DISPATCHES = {
  // A single-day run with no report date prepares the current Eastern date,
  // and its run-name ("Daily briefing single-day today") is what the watchdog
  // reads as today's run.
  "30 13 * * *": { workflow: "daily-briefing.yml", inputs: { mode: "single-day" } },
  // The watchdog's own dispatch input defaults to a dry run.
  "41 * * * *": { workflow: "daily-briefing-watchdog.yml", inputs: { dry_run: "false" } },
};

// Decides whether a failed dispatch is worth sending again.
//
// `response` is the fetch Response, or null when the request failed before
// GitHub answered (DNS, connection reset, timeout). `attempt` counts from 1.
//
// A retry only makes the run start sooner. If no run starts, the watchdog
// dispatches one after 19:00 Eastern; if a run did start, a second dispatch
// would pay for a duplicate run that replaces the first briefing. So retry
// only failures where GitHub almost certainly created nothing:
//
// - 429 and 5xx: GitHub refused or failed to handle the request.
// - Not a network failure (null): the request may have reached GitHub, and
//   fetch cannot say whether it did.
// - Not other 4xx: an expired token or a bad input fails the same way again.
export function shouldRetry(response, attempt) {
  if (response === null) return false;
  return response.status === 429 || response.status >= 500;
}

export async function dispatch(env, { workflow, inputs }, { sleep = defaultSleep } = {}) {
  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN secret is not set; run `wrangler secret put GITHUB_TOKEN`");
  }
  const url = `${API_ROOT}/repos/${REPOSITORY}/actions/workflows/${workflow}/dispatches`;
  const request = {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": USER_AGENT,
      "X-GitHub-Api-Version": API_VERSION,
    },
    body: JSON.stringify({ ref: "main", inputs }),
  };

  for (let attempt = 1; ; attempt++) {
    let response = null;
    let failure;
    try {
      response = await fetch(url, request);
    } catch (error) {
      failure = `request failed: ${error}`;
    }
    if (response?.ok) {
      console.log(`dispatched ${workflow} (HTTP ${response.status}, attempt ${attempt})`);
      return;
    }
    if (response) {
      const body = (await response.text()).slice(0, MAX_LOGGED_BODY);
      failure = `HTTP ${response.status}: ${body}`;
    }
    if (attempt >= MAX_ATTEMPTS || !shouldRetry(response, attempt)) {
      // Throwing marks this cron invocation as failed in the Workers logs.
      throw new Error(`could not dispatch ${workflow} after ${attempt} attempt(s); ${failure}`);
    }
    console.warn(`dispatch of ${workflow} failed (${failure}); retrying`);
    await sleep(RETRY_DELAY_MS * attempt);
  }
}

function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default {
  async scheduled(controller, env) {
    const target = DISPATCHES[controller.cron];
    if (!target) {
      throw new Error(`no dispatch is configured for cron "${controller.cron}"`);
    }
    await dispatch(env, target);
  },
};
