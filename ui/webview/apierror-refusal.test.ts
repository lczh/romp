// Safeguards-refusal handling (the user 2026-08-15): a classifier refusal ("<Model>'s safeguards
// flagged this message…") is DETERMINISTIC on the same input — a retry re-sends the same prompt and
// manufactures the same refusal (one refused prompt drew twelve auto-retries in ~6 minutes before this),
// and in fallback configurations each retry manufactures another model downgrade. The kernel classifies
// it (_api_error.refusal — the system model_refusal_* record linked by parentUuid, plus the CLI's own
// wording as a co-equal signature) and never auto-retries it; the client skips it in the retry tick,
// renders it red/on-you, and names the real fix: rewrite the prompt or drop the thread. The chat/feed
// renderers have no jsdom harness, so pin the wiring at source. (Kernel side:
// tests/test_kernel_refusal_retry.py.)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { apiCardLine, REFUSAL_REMEDY } from "./api-card-line";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const F = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("Status carries the per-session apiRefusal flag", () => {
  assert.match(R, /apiRefusal\?: boolean/);
});

test("the auto-retry tick SKIPS a refused thread (a retry manufactures the same refusal)", () => {
  assert.match(R, /!s\.status\.retrySuppressed && !s\.status\.apiSpendLimit && !s\.status\.apiModelLimit && !s\.status\.apiAuthErr && !s\.status\.apiRefusal/);
});

test("a refusal paints the tab alarm-red (on-you), not amber retrying", () => {
  // the rule lives in tab-state.ts since tab groups (2026-09-04) — one function for the tab and the
  // folded section header's pip (tab-state.test.ts executes it); render.ts wears its result
  const S = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tab-state.ts"), "utf8");
  assert.match(S, /\(s!\.apiTooLong \|\| s!\.apiSpendLimit \|\| s!\.apiModelLimit \|\| s!\.apiAuthErr \|\| s!\.apiRefusal\) \? "tab-blocked" : "tab-retrying"/);
  assert.match(R, /const stateCls = tabStateClass\(s\.status\);/);
});

test("the chat error card drops Retry on a refusal and names the real fix in plain terms", () => {
  const apiErr = R.slice(R.indexOf("function renderApiError"), R.indexOf("// ── API-error auto-retry"));
  // the refusal joins the no-Retry gate (the button cannot work — same contract as the other on-you classes)
  assert.match(apiErr, /const refusal = !!st\?\.apiRefusal;/);
  assert.match(apiErr, /const spendCap = !!st\?\.apiSpendLimit \|\| !!st\?\.apiModelLimit \|\| !!st\?\.apiAuthErr \|\| refusal;/);
  // …and the card's line says what happened + the fix, never "retrying soon…": the ONE decider every writer of
  // the line calls (api-card-line.ts apiCardLine, 2026-11-15) answers the remedy first, so this write and the
  // tick's re-assert below cannot drift into different words
  assert.equal(REFUSAL_REMEDY, "the model's safeguards refused this prompt — rewrite it or drop this thread");
  assert.equal(apiCardLine({ apiRefusal: true }, { paused: false, resumeAt: null, reason: "" }, undefined, Date.now()), REFUSAL_REMEDY);
  assert.match(apiErr, /countdown\.textContent = apiCardLineFor\(activeId\);/);
  // no dead button either: a refusal (like every no-Retry class) appends nothing after the Retry arm. Anchored with no
  // wildcard (review find): the Retry push, its closing brace and the next comment are adjacent lines, so a re-added
  // `else if` arm under any label fails this pin
  assert.match(apiErr, /if \(!spendCap\) \{\n    acts\.push\(noticeAct\("Retry now", "apiRetryNow"[^\n]*\n  \}\n  \/\/ Global auto-retry pause/);
});

test("the 1s countdown tick RE-ASSERTS the refusal remedy — it must never write \"retrying soon…\" over it", () => {
  // apiRetryTick's countdown writer runs every second; without a refusal answer of its own it fell through to
  // the countdown arm (a refusal session is out of the retry set, so it has no retry time) and overwrote the
  // remedy with "retrying soon…" one second after render — promising a retry the kernel never fires. The tick
  // now writes every card through the same decider as the render (relabelApiCards → apiCardLine), whose first
  // answer is the remedy: the global pause is about auto-retry, which a refusal never gets, so no pause state,
  // no stale retry time and no per-thread stop words a refused card.
  const tick = R.slice(R.indexOf("function apiRetryTick"), R.indexOf("function renderTool"));
  assert.match(tick, /\n  relabelApiCards\(\);\n\}/);
  const now = Date.now();
  for (const pause of [{ paused: false, resumeAt: null, reason: "" }, { paused: true, resumeAt: null, reason: "" },
                       { paused: true, resumeAt: null, reason: "spend" }, { paused: true, resumeAt: now / 1000 + 60, reason: "limit" }]) {
    for (const next of [undefined, now + 9_000]) {
      assert.equal(apiCardLine({ apiRefusal: true, retrySuppressed: true }, pause, next, now), REFUSAL_REMEDY,
        "the remedy sits ABOVE the paused/suppressed/countdown chain");
    }
  }
});

test("the feed card badges a refusal and HIDES Retry (a useless click there)", () => {
  assert.match(F, /const refusal = !!\(it\.blocked && it\.blocked\.refusal\)/);
  assert.match(F, /a\._apiRetry\.style\.display = \(showApiErr && !spendLimit && !modelLimit && !refusal\) \? "" : "none"/);
  assert.match(F, /refusal \? "⚠ Safeguards refused"/);
  assert.match(F, /refusal\?: boolean/);
  // the badge tooltip carries the kernel's plain-terms remedy, not the CLI's boilerplate
  assert.match(F, /\(spendLimit \|\| refusal\) \? it\.blocked\.what/);
});
