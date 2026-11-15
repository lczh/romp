// Monthly spend-cap handling (the user 2026-07-14): a billing cap ("You've hit your monthly spend limit")
// is on YOU and has no readable reset, so unlike a transient API error it must NEVER auto-retry — retrying
// can't lift a spend cap, and without this the 10s loop stormed forever ("retry retry retry…"). The kernel
// classifies it (_api_error.spendLimit) and auto-engages the global pause (reason "spend"); the client
// skips it in the retry tick, renders it red/on-you, and shows a "raise your cap" line. The chat/feed
// renderers have no jsdom harness, so pin the wiring at source. (Kernel side: tests/test_kernel_usage_limit.py
// AutoPauseOnSpendLimit + tests/test_kernel.py TestApiError.)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { retryPausedText } from "./api-card-line";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const F = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("Status carries the per-session apiSpendLimit flag", () => {
  assert.match(R, /apiSpendLimit\?: boolean/);
});

test("the auto-retry tick SKIPS a spend-capped thread (retrying can't fix a billing cap)", () => {
  // a spent MODEL allowance is skipped by the same rule (the user 2026-08-01), and a dead credential
  // too (per-session auth, the user 2026-08-08): every retry re-presents the same broken login/key
  assert.match(R, /!s\.status\.retrySuppressed && !s\.status\.apiSpendLimit && !s\.status\.apiModelLimit && !s\.status\.apiAuthErr/);
});

test("a spend cap paints the tab alarm-red (on-you), not amber retrying", () => {
  // a safeguards refusal is the fifth on-you class (the user 2026-08-15) — see apierror-refusal.test.ts.
  // The rule lives in tab-state.ts since tab groups (2026-09-04): one function for the tab and the
  // folded section header's pip (tab-state.test.ts executes it); render.ts wears its result
  const S = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tab-state.ts"), "utf8");
  assert.match(S, /\(s!\.apiTooLong \|\| s!\.apiSpendLimit \|\| s!\.apiModelLimit \|\| s!\.apiAuthErr \|\| s!\.apiRefusal\) \? "tab-blocked" : "tab-retrying"/);
  assert.match(R, /const stateCls = tabStateClass\(s\.status\);/);
});

test("the paused line names the spend cap and points at the settings page (no fake countdown)", () => {
  // the paused line lives in api-card-line.ts since 2026-11-15 (one decider for the card's three writers), executed here
  assert.equal(retryPausedText({ paused: true, resumeAt: null, reason: "spend" }, Date.now()),
    "monthly spend limit reached — raise it at claude.ai/settings/usage");
  assert.match(R, /reason: globalRetryReason, still: globalRetryStill \}/, "the page's pause reason reaches the decider");
});

test("the globalRetryPaused push reason is threaded into the client", () => {
  assert.match(R, /globalRetryReason = typeof m\.reason === "string" \? m\.reason : ""/);
});

test("the feed card badges a spend cap and HIDES Retry (a useless click there)", () => {
  assert.match(F, /const spendLimit = !!\(it\.blocked && it\.blocked\.spendLimit\)/);
  assert.match(F, /a\._apiRetry\.style\.display = \(showApiErr && !spendLimit && !modelLimit && !refusal\) \? "" : "none"/);
  assert.match(F, /spendLimit \? "⚠ Spend limit"/);
  assert.match(F, /spendLimit\?: boolean/);
});

// A spent MODEL allowance is the same shape and gets the same treatment (the user 2026-08-01): Retry
// re-fails until the model changes or its window resets, so the button goes and the badge names the fix.
test("the feed card badges a spent model allowance and HIDES Retry too", () => {
  assert.match(F, /const modelLimit = !!\(it\.blocked && it\.blocked\.modelLimit\)/);
  assert.match(F, /modelLimit \? "⚠ Model limit"/);
  assert.match(F, /modelLimit\?: boolean/);
});

// The kernel latches a usage-limit pause as reason "limit" in the pause file (the bottom bar's API health cell
// names the pause from it). The chat card's paused line is unchanged by that: retryPausedText branches on
// === "spend" first and otherwise falls to the resumeAt countdown, so a "limit" reason renders the countdown
// exactly as an unlabeled limit pause did. The kernel half (the frame still carries `reason`, the engage writes
// "limit") is pinned in tests/test_api_health_rail.py's Wiring class.
test("retryPausedText: spend first, then the resumeAt countdown (a 'limit' reason renders the countdown)", () => {
  const now = Date.now(), resumeAt = Math.floor(now / 1000) + 600;
  const hm = new Date(resumeAt * 1000).toTimeString().slice(0, 5);
  const limit = retryPausedText({ paused: true, resumeAt, reason: "limit" }, now);
  assert.match(limit, new RegExp("^usage limit — retrying at " + hm + " \\(in (9m 59s|10m 0s)\\)$"));
  assert.equal(retryPausedText({ paused: true, resumeAt, reason: "" }, now), limit, "no reason-specific branch: the countdown IS the limit's rendering");
  assert.equal(retryPausedText({ paused: true, resumeAt, reason: "spend" }, now),
    "monthly spend limit reached — raise it at claude.ai/settings/usage", "spend is checked before the countdown");
  const L = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "api-card-line.ts"), "utf8");
  const fn = L.slice(L.indexOf("export function retryPausedText("), L.indexOf("export function apiCardLine("));
  assert.doesNotMatch(fn, /"limit"/, "no reason-specific branch: the countdown IS the limit's rendering");
});
