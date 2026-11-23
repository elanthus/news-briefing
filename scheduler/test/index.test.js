// Offline tests for the scheduler Worker. Run with `npm test` in scheduler/.
// fetch is replaced with a fake, so nothing reaches GitHub.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, test } from "node:test";

import worker, { DISPATCHES, dispatch } from "../src/index.js";

const realFetch = globalThis.fetch;
const env = { GITHUB_TOKEN: "test-token" };
const noSleep = async () => {};

function fakeFetch(...responses) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  };
  return calls;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

test("every cron in wrangler.toml has a dispatch, and no dispatch lacks a cron", () => {
  const config = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  const crons = JSON.parse(config.match(/^crons = (\[.*\])$/m)[1]);
  assert.deepEqual([...crons].sort(), Object.keys(DISPATCHES).sort());
});

test("the daily cron dispatches a single-day run of the daily workflow on main", async () => {
  const calls = fakeFetch(new Response(null, { status: 204 }));
  await worker.scheduled({ cron: "30 13 * * *" }, env);
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "https://api.github.com/repos/elanthus/news-briefing/actions/workflows/daily-briefing.yml/dispatches",
  );
  assert.equal(calls[0].init.headers.Authorization, "Bearer test-token");
  assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main", inputs: { mode: "single-day" } });
});

test("the hourly cron dispatches the watchdog with dry_run off", async () => {
  const calls = fakeFetch(new Response(null, { status: 204 }));
  await worker.scheduled({ cron: "41 * * * *" }, env);
  assert.match(calls[0].url, /\/daily-briefing-watchdog\.yml\/dispatches$/);
  assert.deepEqual(JSON.parse(calls[0].init.body).inputs, { dry_run: "false" });
});

test("an unknown cron fails instead of dispatching", async () => {
  const calls = fakeFetch();
  await assert.rejects(worker.scheduled({ cron: "0 0 * * *" }, env), /no dispatch is configured/);
  assert.equal(calls.length, 0);
});

test("a missing token fails before any request", async () => {
  const calls = fakeFetch();
  await assert.rejects(dispatch({}, DISPATCHES["30 13 * * *"]), /GITHUB_TOKEN secret is not set/);
  assert.equal(calls.length, 0);
});

test("a rejected token fails with GitHub's status and message, without a retry", async () => {
  const calls = fakeFetch(new Response('{"message":"Bad credentials"}', { status: 401 }));
  await assert.rejects(
    dispatch(env, DISPATCHES["30 13 * * *"], { sleep: noSleep }),
    /after 1 attempt\(s\); HTTP 401: \{"message":"Bad credentials"\}/,
  );
  assert.equal(calls.length, 1);
});

test("a network failure fails without a retry, since GitHub may have started the run", async () => {
  const calls = fakeFetch(new TypeError("connection reset"));
  await assert.rejects(
    dispatch(env, DISPATCHES["30 13 * * *"], { sleep: noSleep }),
    /request failed: TypeError: connection reset/,
  );
  assert.equal(calls.length, 1);
});

test("a server error or rate limit is retried until a dispatch succeeds", async () => {
  const delays = [];
  const calls = fakeFetch(
    new Response("unavailable", { status: 503 }),
    new Response("slow down", { status: 429 }),
    new Response(null, { status: 204 }),
  );
  await dispatch(env, DISPATCHES["30 13 * * *"], { sleep: async (ms) => delays.push(ms) });
  assert.equal(calls.length, 3);
  assert.deepEqual(delays, [30_000, 60_000]);
});

test("persistent server errors stop after three attempts", async () => {
  const calls = fakeFetch(
    new Response("a", { status: 502 }),
    new Response("b", { status: 502 }),
    new Response("c", { status: 502 }),
  );
  await assert.rejects(
    dispatch(env, DISPATCHES["30 13 * * *"], { sleep: noSleep }),
    /after 3 attempt\(s\); HTTP 502: c/,
  );
  assert.equal(calls.length, 3);
});
