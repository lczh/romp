// Per-thread auto-retry suppression on interrupt (the user 2026-07-06): the global "Stop all auto-retries"
// only stops romp's own 10s loop and is account-wide + flap-prone. Interrupting a stuck thread is the real
// per-thread off-switch — the interrupt aborts the CLI's in-flight retry, and status.retrySuppressed keeps
// romp from re-firing "retry" into it until a successful turn re-arms it. The chat renderer has no jsdom
// harness, so pin the wiring at source. (Kernel side: tests/test_session_retry_suppress.py.)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { chipStops } from "./status-chip";
import { apiCardLine, SUPPRESSED_LINE } from "./api-card-line";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("Status carries the per-session retrySuppressed flag", () => {
  assert.match(R, /retrySuppressed\?: boolean/);
});

test("the stop/interrupt button is now reachable on a retrying / blocked thread (so you can interrupt it)", () => {
  // it renders for working/compacting AND the stuck states — that's where it doubles as the per-thread off-switch
  assert.match(R, /if \(chipStops\(s\.status\.state\)\) left\.appendChild\(stopButton\(s\.status\.state\)\);/);
  assert.ok(chipStops("retrying") && chipStops("blocked"), "the shared rule (status-chip.ts) holds both stuck states");
  // the button adapts its label/tip for the stuck states (styled tip since 2026-08-28 — label line + explanation)
  assert.match(R, /const stuck = state === "retrying" \|\| state === "blocked"/);
  assert.match(R, /Stop retrying\\ninterrupt this thread/);
  // and it fires the SAME interrupt op (the kernel arms suppression off it)
  assert.match(R, /vscodeApi\.postMessage\(\{ type: "interrupt", id: activeId \}\)/);
});

test("the auto-retry tick SKIPS a thread the user interrupted (retrySuppressed), like a recovered one", () => {
  assert.match(R, /s\.status\.state === "blocked" && !s\.status\.retrySuppressed/);
});

test("a suppressed thread's card says WHY it isn't retrying (distinct from the global pause)", () => {
  // the card's line has one decider for its render, the tick and the Stop/Resume-all flip (api-card-line.ts,
  // 2026-11-15), so the live tick keeps this message instead of a fake "retrying in Ns", and so does a Resume
  const off = { paused: false, resumeAt: null, reason: "" }, now = Date.now();
  assert.equal(SUPPRESSED_LINE, "auto-retry stopped for this session — send a message to resume");
  assert.equal(apiCardLine({ retrySuppressed: true }, off, undefined, now), SUPPRESSED_LINE);
  assert.equal(apiCardLine({ retrySuppressed: true }, off, now + 7_000, now), SUPPRESSED_LINE, "a retry time left on the books is no countdown");
  assert.equal(apiCardLine({ retrySuppressed: true }, { paused: true, resumeAt: null, reason: "" }, undefined, now),
    "auto-retry off (global)", "distinct from the global pause, whose line leads while it holds");
});
