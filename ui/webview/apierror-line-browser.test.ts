// The live API-error card, RENDERED by the real chat bundle (render.ts) in headless Chromium for sessions that are NOT the
// active tab (the review of PR 470, 2026-11-15). The card's line has one decider (api-card-line.ts, executed in
// apierror-line.test.ts), but which SESSION a card reads is render.ts's, and the pure test cannot see it: the idle prebuild
// builds background tabs while another tab is active, and the keyed paint never repaints an unchanged card. A card that read
// the active tab wore that session's line and buttons for good: a background tab stopped on a spend cap kept "Retry now" and
// read "retrying soon…" after the Resume frame, and a background transient error never counted down after a switch to it.
// Here the page is driven by the frames the kernel sends (session frames, the globalRetryPaused frame), one tab active and
// four built in the background, and every card is read off the DOM: its stamp, its line and its buttons, through the pause
// engaging, the Resume frame, a pause that comes back marked `still`, and a switch to each tab. A comment thread's card,
// rendered into the popover under the thread's own session, is read the same way.
// Skips with a stated reason when no playwright browser is installed, and fails with it where CI requires the leg
// (browser-leg.ts). Synthetic prose and placeholder ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { chatBody, ATTACH_TITLE_WEB } from "../../vscode-extension/src/page-skeleton";
import { launchLeg } from "./browser-leg";
import { AUTH_REMEDY, MODEL_LIMIT_REMEDY, SPEND_CAP_REMEDY, SPEND_STILL_REMEDY } from "./api-card-line";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
const STYLES = fs.readFileSync(path.join(UI, "styles.css"), "utf8").replace(/^@import [^\n]*\n/m, "");
const BUILD = { bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
  nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent" };

const chatHtml = () => `<!DOCTYPE html><html lang=en><head><meta charset=utf-8><style>${STYLES}</style></head><body>
${chatBody(ATTACH_TITLE_WEB)}
<script>window.__posted=[];window.acquireVsCodeApi=function(){return{postMessage:function(m){window.__posted.push(m)},getState:function(){return null},setState:function(){}}};</script>
<script src=/dist/render.js></script></body></html>`;

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

const sid = (n: number) => `aaaaaaaa-bbbb-4ccc-8ddd-${String(n).padStart(12, "0")}`;
const WEB = sid(1), API = sid(2), TESTS = sid(3), DOCS = sid(4), BUILDS = sid(5);
const TID = "11111111-2222-4333-8444-555555555555", ANCHOR = "11111111-2222-4333-8444-666666666666";
const PASSAGE = "Keep the retry cap at two minutes";
const SPEND_TEXT = "API Error: 429 the monthly spend limit for this workspace was reached";   // invented wording
const PAUSED_SPEND = "monthly spend limit reached — raise it at claude.ai/settings/usage";

type Card = { stamp: string; line: string; buttons: string[]; shown: boolean };

const post = (page: any, msg: object) => page.evaluate((m: object) => window.postMessage(m, "*"), msg);
const pause = (page: any, value: boolean, reason = "", extra: object = {}) =>
  post(page, { type: "globalRetryPaused", value, reason, resumeAt: null, ...extra });
// two animation frames: the message handler and the paint it asked for have both run
const settle = (page: any) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));

// a session whose turn stopped on an API error: its status carries the class, its tail the live card
function blocked(id: string, name: string, n: number, status: object, text: string, code: number): object {
  return { type: "session", id, name, cwd: "/tmp/TESTHOST/notes-api", status: { state: "blocked", sinceEpoch: null, ...status },
           events: [{ kind: "assistant", md: `Working on the ${name} side of the notes-api.`, uuid: `11111111-2222-4333-8444-0000000000${10 + n}` },
                    { kind: "apiError", text, status: code }] };
}

// every live card in the page, by the session whose view (or thread popover) holds it
const cards = (page: any): Promise<Record<string, Card>> => page.evaluate(() => {
  const out: Record<string, { stamp: string; line: string; buttons: string[]; shown: boolean }> = {};
  for (const c of Array.from(document.querySelectorAll(".api-countdown")) as HTMLElement[]) {
    const card = c.closest(".turn-apierror") as HTMLElement | null;
    const owner = (c.closest("[data-session]") as HTMLElement | null)?.dataset.session || "";
    out[owner] = { stamp: c.dataset.sid || "", line: c.textContent || "", shown: !!(card && card.offsetParent),
                   buttons: Array.from(card?.querySelectorAll("button") || []).map((b) => b.textContent || "") };
  }
  return out;
});

async function inChat(t: any, body: (page: any) => Promise<void>): Promise<void> {
  const browser = await launchLeg(t, pw, "chromium");
  if (!browser) return;
  const errors: string[] = [];
  try {
    const renderJs = requireCjs("esbuild").buildSync({ ...BUILD, entryPoints: [path.join(UI, "render.ts")] }).outputFiles[0].text;
    const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    await page.route("**/*", (route: any) => {
      const u = new URL(route.request().url());
      if (u.hostname !== "romp.test") return route.fulfill({ status: 404, body: "" });
      if (u.pathname === "/chat") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: chatHtml() });
      if (u.pathname === "/dist/render.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: renderJs });
      return route.fulfill({ status: 404, body: "" });
    });
    await page.goto("http://romp.test/chat");
    // the first session to arrive is the active tab: an idle one, with no card of its own
    await post(page, { type: "session", id: WEB, name: "web", cwd: "/tmp/TESTHOST/notes-api", status: { state: "idle", sinceEpoch: null },
                       events: [{ kind: "assistant", md: PASSAGE + " on every sync path of the notes-api.", uuid: ANCHOR }] });
    await page.waitForSelector("#content .turn-assistant .md p", { state: "attached", timeout: 10000 });
    await body(page);
    assert.deepEqual(errors, [], "no page error");
  } finally {
    await browser.close();
  }
}

// show a tab the way a reader does: a click on it in the strip
async function showTab(page: any, id: string): Promise<void> {
  await page.click(`#tabs .tab[data-id="${id}"]`);
  await page.waitForFunction((s: string) => {
    const v = document.querySelector(`#content [data-session="${s}"]`) as HTMLElement | null;
    return !!v && v.style.display !== "none" && !!v.querySelector(".turn-apierror");
  }, id, { timeout: 5000 });
  await settle(page);
}

test("a card built for a background tab reads its own session: its stop's remedy and no Retry, or its own countdown, through the pause, the Resume frame and a switch to it", async (t) => {
  await inChat(t, async (page) => {
    // the spend pause holds when the sessions arrive; four tabs stop on an API error while `web` stays the active tab
    await pause(page, true, "spend");
    const next = Math.floor(Date.now() / 1000) + 396;   // the kernel's next retry for `tests`, 6m 36s out
    await post(page, blocked(API, "api", 1, { apiSpendLimit: true }, SPEND_TEXT, 429));
    await post(page, blocked(TESTS, "tests", 2, { retryNextAt: next, retryTries: 3 }, "API Error: 529 the service is overloaded", 529));
    await post(page, blocked(DOCS, "docs", 3, { apiAuthErr: true }, "Not logged in", 401));
    await post(page, blocked(BUILDS, "build", 4, { apiModelLimit: true }, "API Error: this model's allowance is spent", 429));
    // the idle prebuild builds the four hidden views; nothing was clicked
    await page.waitForFunction(() => document.querySelectorAll(".api-countdown").length === 4, null, { timeout: 10000 });
    await settle(page);
    let c = await cards(page);
    assert.deepEqual(Object.keys(c).sort(), [API, TESTS, DOCS, BUILDS].sort(), "one live card in each stopped session's view");
    for (const id of [API, TESTS, DOCS, BUILDS]) {
      assert.equal(c[id].stamp, id, "each card is stamped with the session it was built for, not the active tab");
      assert.equal(c[id].shown, false, "premise: built in the background, its view hidden");
    }
    // under the pause: each stop nothing retries wears its own remedy and no Retry; the transient one wears the pause's line
    assert.deepEqual([c[API].line, c[API].buttons], [SPEND_CAP_REMEDY, ["Resume all auto-retries"]]);
    assert.deepEqual([c[DOCS].line, c[DOCS].buttons], [AUTH_REMEDY, ["Resume all auto-retries"]]);
    assert.deepEqual([c[BUILDS].line, c[BUILDS].buttons], [MODEL_LIMIT_REMEDY, ["Resume all auto-retries"]]);
    assert.deepEqual([c[TESTS].line, c[TESTS].buttons], [PAUSED_SPEND, ["Retry now", "Resume all auto-retries"]]);

    // the Resume frame: the kernel says the pause is off
    await pause(page, false);
    await settle(page);
    c = await cards(page);
    assert.equal(c[API].line, SPEND_CAP_REMEDY, "a spend-capped card names the cap, never a retry the kernel does not send");
    assert.equal(c[DOCS].line, AUTH_REMEDY);
    assert.equal(c[BUILDS].line, MODEL_LIMIT_REMEDY);
    for (const id of [API, TESTS, DOCS, BUILDS]) assert.equal(c[id].buttons[c[id].buttons.length - 1], "Stop all auto-retries");
    assert.doesNotMatch(c[API].buttons.join("|"), /Retry now/);
    // the transient one counts down to ITS session's next retry, once the one-second tick has adopted the kernel's schedule
    await page.waitForFunction((s: string) => /^retrying in 6m \d+s · 3 tries so far$/.test(
      (document.querySelector(`#content [data-session="${s}"] .api-countdown`) as HTMLElement | null)?.textContent || ""), TESTS, { timeout: 5000 });
    // …and the tick, which runs for every card each second, left the others' remedies alone
    c = await cards(page);
    assert.deepEqual([c[API].line, c[DOCS].line, c[BUILDS].line], [SPEND_CAP_REMEDY, AUTH_REMEDY, MODEL_LIMIT_REMEDY]);

    // a switch to each tab shows the card as it was built: the view is revealed, not repainted
    await showTab(page, API);
    c = await cards(page);
    assert.deepEqual([c[API].shown, c[API].line, c[API].buttons], [true, SPEND_CAP_REMEDY, ["Stop all auto-retries"]]);
    await showTab(page, TESTS);
    c = await cards(page);
    assert.equal(c[TESTS].shown, true);
    assert.match(c[TESTS].line, /^retrying in 6m \d+s · 3 tries so far$/, "a background transient error counts down after the switch");
    assert.deepEqual(c[TESTS].buttons, ["Retry now", "Stop all auto-retries"]);

    // the pause comes back marked `still` (a retry was refused): the cap's card says so, shown or hidden, and the mark
    // goes with the frame that carries none
    await pause(page, true, "spend", { still: true });
    await settle(page);
    c = await cards(page);
    assert.equal(c[API].line, SPEND_STILL_REMEDY);
    assert.equal(c[TESTS].line, "monthly spend limit still in force — raise it at claude.ai/settings/usage");
    assert.deepEqual([c[DOCS].line, c[BUILDS].line], [AUTH_REMEDY, MODEL_LIMIT_REMEDY], "the mark is the spend cap's alone");
    await pause(page, true, "spend");
    await settle(page);
    c = await cards(page);
    assert.deepEqual([c[API].line, c[TESTS].line], [SPEND_CAP_REMEDY, PAUSED_SPEND]);
  });
});

test("a comment thread's card reads the thread, not the tab it hangs on", async (t) => {
  await inChat(t, async (page) => {
    // the thread stopped on the spend cap; the tab it was made on (`web`, active) is idle and stopped on nothing
    const th = { tid: TID, sid: TID, name: "web-comment-1", anchorUuid: ANCHOR, exact: PASSAGE, status: "open", createdT: 1_700_000_000,
                 state: "blocked", chip: "blocked", unread: false, replyOwed: false, promotedName: "", msgs: [], model: "Opus 5", effort: "high",
                 events: [{ kind: "user", md: "Should the export retry on a timeout?", uuid: "11111111-2222-4333-8444-700000000001" },
                          { kind: "apiError", text: SPEND_TEXT, status: 429 }],
                 live: { status: { state: "blocked", apiSpendLimit: true }, bgTasks: null } };
    await post(page, { type: "comments", id: WEB, threads: [th], caps: ["threadAsks"] });
    await page.waitForSelector(`mark.cmt-hl[data-tid="${TID}"]`, { state: "attached", timeout: 10000 });
    await page.evaluate((tid: string) => (document.querySelector(`mark.cmt-hl[data-tid="${tid}"]`) as HTMLElement).click(), TID);
    await page.waitForSelector("#cmt-pop .cmt-msgs .turn-apierror", { state: "attached", timeout: 10000 });
    await settle(page);
    const read = () => page.evaluate(() => {
      const c = document.querySelector("#cmt-pop .api-countdown") as HTMLElement;
      return { stamp: c.dataset.sid, line: c.textContent, buttons: Array.from(c.closest(".turn-apierror")!.querySelectorAll("button")).map((b) => b.textContent) };
    });
    assert.deepEqual(await read(), { stamp: TID, line: SPEND_CAP_REMEDY, buttons: ["Stop all auto-retries"] });
    await pause(page, true, "spend", { still: true });
    await settle(page);
    assert.deepEqual(await read(), { stamp: TID, line: SPEND_STILL_REMEDY, buttons: ["Resume all auto-retries"] });
    await pause(page, false);
    await settle(page);
    assert.deepEqual(await read(), { stamp: TID, line: SPEND_CAP_REMEDY, buttons: ["Stop all auto-retries"] });
  });
});
