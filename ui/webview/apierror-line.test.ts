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
import { apiCardLine, apiCardSays, apiCardOnYou, retryPausedText, API_CARD_STATUS_KEYS, REFUSAL_REMEDY, SPEND_CAP_REMEDY, SPEND_STILL_REMEDY,
         MODEL_LIMIT_REMEDY, AUTH_REMEDY, SUPPRESSED_LINE, type ApiCardStatus, type RetryPause } from "./api-card-line";
import { apiErrorReason } from "./api-error-reason";

const ROOT = path.resolve(process.cwd(), "..");
const R = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const card = R.slice(R.indexOf("function renderApiError("), R.indexOf("// ── API-error auto-retry"));   // the card's render, isolated

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

test("a remedy is told from a short form: what the person must do, against a countdown", () => {
  // the open card gives a remedy a line of its own in the body, where it wraps on a pane of any width; a short form
  // keeps the head's one-line slot while it fits (render.ts paintApiCardLine and the sheet decide where each shows)
  for (const [what, st] of ON_YOU) for (const [name, pause] of PAUSES) assert.equal(apiCardSays(st, pause, undefined, NOW).remedy, true, `${what}, pause ${name}`);
  assert.deepEqual(apiCardSays({ retrySuppressed: true }, OFF, undefined, NOW), { line: SUPPRESSED_LINE, remedy: true }, "a thread they stopped: send a message");
  assert.equal(apiCardSays({}, SPEND, undefined, NOW).remedy, true, "a spend pause names what to do: raise the cap");
  assert.equal(apiCardSays({}, STILL, undefined, NOW).remedy, true);
  assert.deepEqual(apiCardSays({}, MANUAL, undefined, NOW), { line: "auto-retry off (global)", remedy: false });
  assert.equal(apiCardSays({}, LIMIT, undefined, NOW).remedy, false, "a usage window counts down to its reset");
  assert.deepEqual(apiCardSays({}, OFF, undefined, NOW), { line: "retrying soon…", remedy: false });
  assert.deepEqual(apiCardSays({ retryTries: 4 }, OFF, NOW + 150_000, NOW), { line: "retrying in 2m 30s · 4 tries so far", remedy: false });
  for (const [st, pause, next] of [[{ apiSpendLimit: true }, STILL, undefined], [{}, LIMIT, NOW + 5_000], [{ retrySuppressed: true }, OFF, undefined]] as const) {
    assert.equal(apiCardSays(st, pause, next, NOW).line, apiCardLine(st, pause, next, NOW), "the line alone is the same line");
  }
});

test("every status field the card reads is one the kernel projects onto a comment thread's status", () => {
  // a thread's card is the same card, drawn for the thread, whose status the kernel cuts down to a list of keys. With a
  // field missing from that list a thread stopped on a spend cap read as about to retry and offered Retry now, whatever
  // this page did (the review of PR 470): the two lists are held together here.
  const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
  const tuple = KERNEL.slice(KERNEL.indexOf("_THREAD_LIVE_STATUS_KEYS = ("), KERNEL.indexOf("class _lend_thread_row"));
  const projected = new Set(Array.from(tuple.matchAll(/^\s*(?:_THREAD_LIVE_STATUS_KEYS = \()?((?:"\w+",?\s*)+)\)?$/gm)).flatMap((m) => Array.from(m[1].matchAll(/"(\w+)"/g)).map((k) => k[1])));
  assert.ok(projected.has("state") && projected.has("awaitingWhy"), "premise: the tuple was read: " + Array.from(projected).join(","));
  for (const k of API_CARD_STATUS_KEYS) assert.ok(projected.has(k), `the thread status projection lacks ${k}`);
  // …and the list names what the decider and the card's render read, no less
  const L = fs.readFileSync(path.join(ROOT, "ui", "webview", "api-card-line.ts"), "utf8");
  const read = new Set(Array.from((L + card + lineFor).matchAll(/\bst\??\.(api\w+|retry\w+)/g)).map((m) => m[1]));
  assert.deepEqual(Array.from(read).sort(), Array.from(API_CARD_STATUS_KEYS).slice().sort(), "API_CARD_STATUS_KEYS is exactly what the card reads off a status");
});

// ── the wiring: three writers, one decider ───────────────────────────────────────────────────────────
const lineFor = R.slice(R.indexOf("function apiCardSaysFor("), R.indexOf("function relabelStopAll("));
const flip = R.slice(R.indexOf("function relabelStopAll("), R.indexOf("function renderApiError("));
const tick = R.slice(R.indexOf("function apiRetryTick("), R.indexOf("function renderTool("));

test("render.ts reads a card's line through ONE helper, from the card's own session and the page's pause", () => {
  assert.match(lineFor, /const st = apiCardStatus\(sid\);/);
  assert.match(lineFor, /return apiCardSays\(st, \{ paused: globalRetryPaused, resumeAt: globalRetryResumeAt, reason: globalRetryReason, still: globalRetryStill \},\n\s+next, Date\.now\(\)\);/);
  // the next retry is the tick's schedule for a tab, else the kernel's deadline off the status (a thread's, or a card painted before the first tick)
  assert.match(lineFor, /const next = sid \? apiRetryNext\.get\(sid\) \?\? \(st\?\.retryNextAt \? st\.retryNextAt \* 1000 : undefined\) : undefined;/);
  // the status is the card's own session's: a tab's, or the open comment thread's for a card in its popover
  assert.match(lineFor, /function apiCardStatus\(sid: string \| null\): Status \| undefined \{\n\s+if \(!sid\) return undefined;\n\s+const s = liveSession\(sid\);\n\s+if \(s\) return s\.status;\n\s+const open = openCommentThread\(\);\n\s+return open && threadSidOf\(open\.th\) === sid \? threadMetaStatus\(open\.th\) : undefined;/);
  // one painter writes the line in its two places, the head's slot (with the whole line as its tooltip) and the body
  assert.match(lineFor, /function paintApiCardLine\(slot: HTMLElement, body: HTMLElement \| null\): void \{\n\s+const says = apiCardSaysFor\(slot\.dataset\.sid \|\| null\);\n\s+if \(slot\.textContent !== says\.line\) \{ slot\.textContent = says\.line; setTip\(slot, says\.line\); \}/);
  assert.match(lineFor, /if \(body\.textContent !== says\.line\) body\.textContent = says\.line;\n\s+body\.classList\.toggle\("api-line-remedy", says\.remedy\);/);
  // …and one loop applies it to every live card, each by the session stamped on it at render
  assert.match(lineFor, /function relabelApiCards\(\): void \{\n\s+for \(const slot of Array\.from\(document\.querySelectorAll\("\.api-countdown"\)\) as HTMLElement\[\]\) \{\n\s+paintApiCardLine\(slot, slot\.closest\("\.notice"\)\?\.querySelector\("\.api-line"\) as HTMLElement \| null\);/);
});

test("the card's render reads the session it is being built FOR, never the active tab: the stamp, the line and the buttons' status", () => {
  // the idle prebuild builds background tabs while another tab is active (renderingSid is the one being built), and the
  // keyed paint never repaints an unchanged card: read from activeId, a background card wore the active session's line
  // and buttons for good (the review of PR 470; apierror-line-browser.test.ts renders it)
  assert.match(card, /const own = renderingSid \?\? activeId;/);
  assert.match(card, /countdown\.dataset\.sid = own \|\| "";/);
  assert.match(card, /const line = el\("div", "api-line"\);\n\s+body\.insertBefore\(line, body\.firstChild\);\n\s+paintApiCardLine\(countdown, line\);/);
  assert.match(card, /const st = apiCardStatus\(own\);/);
  assert.doesNotMatch(card, /liveSession\(activeId\)/, "no read of the active tab's status is left in the card");
  assert.ok(card.indexOf("if (st?.apiNoRetry) {") < card.indexOf('el("div", "api-line")'), "a notice nothing retries returns before the line exists: it draws none");
});

test("the Stop/Resume-all flip relabels the buttons and hands every card's line to the helper", () => {
  assert.match(flip, /btn\.textContent = globalRetryPaused \? "Resume all auto-retries" : "Stop all auto-retries";/);
  assert.match(flip, /relabelApiCards\(\);/);
  assert.doesNotMatch(flip, /\.textContent = (?!globalRetryPaused)/, "the flip words no card itself: two words for every card was the defect");
});

test("the one-second tick hands every card's line to the helper, paused or not", () => {
  assert.match(tick, /\n  relabelApiCards\(\);\n\}/, "after the schedule block, at the function's own level: it runs while paused too");
  assert.doesNotMatch(tick, /\.textContent/);
});

test("no writer in render.ts words a card's line on its own", () => {
  // every sentence a card can wear lives in api-card-line.ts; render.ts holds none of them, so a fourth
  // writer cannot drift into words of its own without failing here
  for (const words of ["retrying soon…", "retrying in ", "auto-retry off (global)", "spend limit reached", "still in force",
                       MODEL_LIMIT_REMEDY, AUTH_REMEDY, SUPPRESSED_LINE, REFUSAL_REMEDY]) {
    assert.ok(!R.includes(words), `render.ts still carries "${words}"`);
  }
  assert.equal((R.match(/apiCardSaysFor\(/g) || []).length, 2, "its definition and the painter's call: nothing else asks what a card says");
  assert.equal((R.match(/paintApiCardLine\(/g) || []).length, 3, "the painter's definition, the render's call and relabelApiCards': the only writers");
});

test("the sheet shows the line where it can be read whole: a remedy in the body at every width, a countdown there on a narrow card", () => {
  const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.turn-apierror \.notice \{ container-type: inline-size; \}/, "the card is the size container, as the postal card is");
  assert.match(CSS, /\.api-line \{ display: none; margin: 0 0 5px; white-space: normal; overflow-wrap: anywhere; \}/, "it wraps; no size or ink of its own");
  assert.match(CSS, /\.api-line\.api-line-remedy \{ display: block; \}/);
  assert.match(CSS, /\.notice-open > \.notice-head > \.api-countdown\.api-line-remedy \{ display: none; \}/, "shown once: the open card's slot yields to the body");
  assert.match(CSS, /@container \(max-width: 540px\) \{\n  \.turn-apierror \.api-line \{ display: block; \}\n  \.turn-apierror \.notice-open > \.notice-head > \.api-countdown \{ display: none; \}\n\}/);
  assert.match(CSS, /@container \(max-width: 400px\) \{\n  \.turn-apierror \.notice-head \{ flex-wrap: wrap; row-gap: 5px; \}\n\}/, "a phone-width head wraps: the gist keeps its words");
});
