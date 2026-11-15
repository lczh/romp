// The API-error card's line has ONE decider (2026-11-15). The card's render, the one-second tick and the
// Stop/Resume-all flip each wrote the line from a ladder of their own, and the flip's was two words:
// the pause's text, or "retrying soon…", for EVERY card. So when a spend cap was raised and the person
// pressed "Resume all auto-retries", each spend-capped card changed to "retrying soon…" while the kernel,
// which never auto-retries a spend cap, sent nothing; the tick wrote the same promise every second for
// any card outside the retry set once the pause was off. The same went for a spent model allowance, a
// dead credential and a thread the person had stopped. apiCardLine (api-card-line.ts) now decides the line
// for all three writers from the card's own session status, the pause and the next retry time; it is pure,
// so it is executed here, and the wiring in render.ts (no jsdom harness) is pinned at the source.
// Synthetic values only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { apiCardLine, apiCardOnYou, retryPausedText, REFUSAL_REMEDY, SPEND_CAP_REMEDY, SPEND_STILL_REMEDY, MODEL_LIMIT_REMEDY,
         AUTH_REMEDY, SUPPRESSED_LINE, type ApiCardStatus, type RetryPause } from "./api-card-line";
import { apiErrorReason } from "./api-error-reason";

const ROOT = path.resolve(process.cwd(), "..");
const R = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");

const NOW = 1_800_000_000_000;                    // a fixed clock, epoch ms
const OFF: RetryPause = { paused: false, resumeAt: null, reason: "" };
const MANUAL: RetryPause = { paused: true, resumeAt: null, reason: "" };
const SPEND: RetryPause = { paused: true, resumeAt: null, reason: "spend" };
const LIMIT: RetryPause = { paused: true, resumeAt: NOW / 1000 + 134, reason: "limit" };
const STILL: RetryPause = { paused: true, resumeAt: null, reason: "spend", still: true };
const PAUSES: [string, RetryPause][] = [["off", OFF], ["manual", MANUAL], ["spend", SPEND], ["limit", LIMIT]];

const ON_YOU: [string, ApiCardStatus, string][] = [
  ["a spend cap", { apiSpendLimit: true }, SPEND_CAP_REMEDY],
  ["a spent model allowance", { apiModelLimit: true }, MODEL_LIMIT_REMEDY],
  ["a dead credential", { apiAuthErr: true }, AUTH_REMEDY],
  ["a refusal", { apiRefusal: true }, REFUSAL_REMEDY],
];

test("each stop nothing retries names its OWN fix, in words the interface already has for it", () => {
  // the review of PR 470: a spent model allowance and a dead credential wore the spend cap's sentence, the wrong remedy
  // for both (raising a spend limit mends neither). No new copy: each line is the sentence another surface already says.
  assert.equal(SPEND_CAP_REMEDY, "spend limit reached — raise it at claude.ai/settings/usage");
  assert.equal(REFUSAL_REMEDY, apiErrorReason({ refusal: true }), "the bell's sentence for a refusal (api-error-reason.ts)");
  assert.equal(MODEL_LIMIT_REMEDY, "this model is out of allowance — switch model or add credits");
  assert.equal(MODEL_LIMIT_REMEDY, apiErrorReason({ modelLimit: true }), "the retry line's and the bell's sentence (api-error-reason.ts)");
  assert.equal(AUTH_REMEDY, "this session's sign-in or API key isn't working — fix the login (claude /login) or the key, or switch which one it bills");
  const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
  assert.ok(KERNEL.includes(`else "${AUTH_REMEDY}" if aerr.get("authErr")`), "the feed card's sentence for a dead credential, word for word (the kernel's `what`)");
  assert.equal(new Set(ON_YOU.map(([, , line]) => line)).size, 4, "four stops, four lines");
});

test("a card nothing retries keeps its remedy whatever the global pause does: engaged, lifted, or never on", () => {
  for (const [what, st, line] of ON_YOU) {
    assert.ok(apiCardOnYou(st), what);
    for (const [name, pause] of PAUSES) {
      // with and without a stale retry time on the books, and suppressed or not: none of it words this card
      for (const next of [undefined, NOW + 12_000]) {
        for (const suppressed of [false, true]) {
          const got = apiCardLine({ ...st, retrySuppressed: suppressed, retryTries: 3 }, pause, next, NOW);
          assert.equal(got, line, `${what}, pause ${name}`);
          assert.doesNotMatch(got, /retrying/, `${what} never promises a retry (pause ${name})`);
        }
      }
    }
  }
});

test("Resume all over a spend pause leaves a spend-capped card naming the cap, never \"retrying soon…\"", () => {
  // the reported sequence: the pause held for a spend cap, the person pressed Resume, the kernel's frame said unpaused
  const st: ApiCardStatus = { apiSpendLimit: true };
  const before = apiCardLine(st, SPEND, undefined, NOW);
  const after = apiCardLine(st, OFF, undefined, NOW);
  assert.equal(before, SPEND_CAP_REMEDY);
  assert.equal(after, before, "the flip changes nothing on a card the kernel never auto-retries");
});

test("a Resume the cap refused is answered: the pause comes back marked, and the capped card says the limit is still in force", () => {
  // the kernel sends ONE capped session a retry on the Resume press; refused, the spend pause re-engages with
  // `still` on its frame. Without it the card would read exactly as it did before the press.
  const st: ApiCardStatus = { apiSpendLimit: true };
  assert.equal(apiCardLine(st, STILL, undefined, NOW), "spend limit still in force — raise it at claude.ai/settings/usage");
  assert.equal(SPEND_STILL_REMEDY, apiCardLine(st, STILL, undefined, NOW));
  assert.equal(retryPausedText(STILL, NOW), "monthly spend limit still in force — raise it at claude.ai/settings/usage");
  assert.equal(apiCardLine({}, STILL, NOW + 4_000, NOW), retryPausedText(STILL, NOW), "a transient card under that pause wears the pause's line");
  // the mark belongs to a standing spend pause and to the cap's own card: nothing else reads it
  assert.equal(apiCardLine(st, { ...STILL, paused: false }, undefined, NOW), SPEND_CAP_REMEDY, "the pause lifted: the plain line");
  assert.equal(apiCardLine(st, { ...STILL, reason: "limit" }, undefined, NOW), SPEND_CAP_REMEDY);
  assert.equal(apiCardLine(st, SPEND, undefined, NOW), SPEND_CAP_REMEDY, "a spend pause with no mark: the plain line");
  assert.equal(apiCardLine({ apiModelLimit: true }, STILL, undefined, NOW), MODEL_LIMIT_REMEDY, "a spent model allowance is not the spend cap");
  assert.equal(apiCardLine({ apiAuthErr: true }, STILL, undefined, NOW), AUTH_REMEDY);
  assert.equal(apiCardLine({ apiRefusal: true }, STILL, undefined, NOW), REFUSAL_REMEDY);
  // the frame's field reaches the decider, and a kernel that sends none reads as the plain line
  assert.match(R, /globalRetryStill = m\.still === true;/);
});

test("a thread the person stopped says so once the pause is off; while a pause holds, the pause's line leads", () => {
  const st: ApiCardStatus = { retrySuppressed: true };
  assert.equal(apiCardLine(st, OFF, undefined, NOW), SUPPRESSED_LINE);
  assert.equal(apiCardLine(st, OFF, NOW + 5_000, NOW), SUPPRESSED_LINE, "a stale retry time is not a countdown for a stopped thread");
  assert.equal(apiCardLine(st, MANUAL, undefined, NOW), "auto-retry off (global)");
  assert.equal(apiCardLine(st, SPEND, undefined, NOW), "monthly spend limit reached — raise it at claude.ai/settings/usage");
});

test("a transient error's card counts down to its retry, and wears the pause's line while one holds", () => {
  assert.equal(apiCardLine({}, OFF, undefined, NOW), "retrying soon…", "no retry time published yet");
  assert.equal(apiCardLine(undefined, OFF, undefined, NOW), "retrying soon…", "a card whose session has no status yet");
  assert.equal(apiCardLine({}, OFF, NOW + 12_000, NOW), "retrying in 12s");
  assert.equal(apiCardLine({ retryTries: 1 }, OFF, NOW + 150_000, NOW), "retrying in 2m 30s", "one try is not a history");
  assert.equal(apiCardLine({ retryTries: 4 }, OFF, NOW + 150_000, NOW), "retrying in 2m 30s · 4 tries so far");
  assert.equal(apiCardLine({}, OFF, NOW - 3_000, NOW), "retrying in 0s", "a due retry never counts below zero");
  assert.equal(apiCardLine({ retryTries: 4 }, MANUAL, NOW + 150_000, NOW), "auto-retry off (global)");
});

test("the paused line: a spend cap names the fix, a usage window counts down to its reset, a manual Stop is the off label", () => {
  assert.equal(retryPausedText(SPEND, NOW), "monthly spend limit reached — raise it at claude.ai/settings/usage");
  const hm = new Date(LIMIT.resumeAt! * 1000).toTimeString().slice(0, 5);
  assert.equal(retryPausedText(LIMIT, NOW), `usage limit — retrying at ${hm} (in 2m 14s)`);
  assert.equal(retryPausedText({ ...LIMIT, resumeAt: NOW / 1000 - 9 }, NOW), `usage limit — retrying at ${new Date(NOW - 9_000).toTimeString().slice(0, 5)} (in 0s)`);
  assert.equal(retryPausedText(MANUAL, NOW), "auto-retry off (global)");
  // a spend pause carries no reset, and one that somehow did would still name the cap: spend is read first
  assert.equal(retryPausedText({ paused: true, resumeAt: NOW / 1000 + 60, reason: "spend" }, NOW), retryPausedText(SPEND, NOW));
});

// ── the wiring: three writers, one decider ───────────────────────────────────────────────────────────
const lineFor = R.slice(R.indexOf("function apiCardLineFor("), R.indexOf("function relabelStopAll("));
const flip = R.slice(R.indexOf("function relabelStopAll("), R.indexOf("function renderApiError("));
const card = R.slice(R.indexOf("function renderApiError("), R.indexOf("// ── API-error auto-retry"));
const tick = R.slice(R.indexOf("function apiRetryTick("), R.indexOf("function renderTool("));

test("render.ts reads a card's line through ONE helper, from the card's own session and the page's pause", () => {
  assert.match(lineFor, /return apiCardLine\(apiCardStatus\(sid\), \{ paused: globalRetryPaused, resumeAt: globalRetryResumeAt, reason: globalRetryReason, still: globalRetryStill \},\n\s+sid \? apiRetryNext\.get\(sid\) : undefined, Date\.now\(\)\);/);
  // the status is the card's own session's: a tab's, or the open comment thread's for a card in its popover
  assert.match(lineFor, /function apiCardStatus\(sid: string \| null\): Status \| undefined \{\n\s+if \(!sid\) return undefined;\n\s+const s = liveSession\(sid\);\n\s+if \(s\) return s\.status;\n\s+const open = openCommentThread\(\);\n\s+return open && threadSidOf\(open\.th\) === sid \? threadMetaStatus\(open\.th\) : undefined;/);
  // …and one writer applies it to every live card, each by the session stamped on it at render
  assert.match(lineFor, /function relabelApiCards\(\): void \{\n\s+for \(const cd of Array\.from\(document\.querySelectorAll\("\.api-countdown"\)\) as HTMLElement\[\]\) \{\n\s+cd\.textContent = apiCardLineFor\(cd\.dataset\.sid \|\| null\);/);
});

test("the card's render reads the session it is being built FOR, never the active tab: the stamp, the line and the buttons' status", () => {
  // the idle prebuild builds background tabs while another tab is active (renderingSid is the one being built), and the
  // keyed paint never repaints an unchanged card: read from activeId, a background card wore the active session's line
  // and buttons for good (the review of PR 470; apierror-line-browser.test.ts renders it)
  assert.match(card, /const own = renderingSid \?\? activeId;/);
  assert.match(card, /countdown\.dataset\.sid = own \|\| "";/);
  assert.match(card, /countdown\.textContent = apiCardLineFor\(own\);/);
  assert.match(card, /const st = apiCardStatus\(own\);/);
  assert.doesNotMatch(card, /liveSession\(activeId\)/, "no read of the active tab's status is left in the card");
});

test("the Stop/Resume-all flip relabels the buttons and hands every card's line to the helper", () => {
  assert.match(flip, /btn\.textContent = globalRetryPaused \? "Resume all auto-retries" : "Stop all auto-retries";/);
  assert.match(flip, /relabelApiCards\(\);/);
  assert.doesNotMatch(flip, /cd\.textContent/, "the flip words no card itself: two words for every card was the defect");
});

test("the one-second tick hands every card's line to the helper, paused or not", () => {
  assert.match(tick, /\n  relabelApiCards\(\);\n\}/, "after the schedule block, at the function's own level: it runs while paused too");
  assert.doesNotMatch(tick, /cd\.textContent/);
});

test("no writer in render.ts words a card's line on its own", () => {
  // every sentence a card can wear lives in api-card-line.ts; render.ts holds none of them, so a fourth
  // writer cannot drift into words of its own without failing here
  for (const words of ["retrying soon…", "retrying in ", "auto-retry off (global)", "spend limit reached", "still in force",
                       MODEL_LIMIT_REMEDY, AUTH_REMEDY, SUPPRESSED_LINE, REFUSAL_REMEDY]) {
    assert.ok(!R.includes(words), `render.ts still carries "${words}"`);
  }
  const writes = R.match(/\.textContent = apiCardLineFor\(/g) || [];
  assert.equal(writes.length, 2, "the render's write and relabelApiCards': the only two");
});
