// A comment thread's popover while the thread works, read by the REAL chat bundle (render.ts) in headless Chromium (the review
// of PR 240, 2026-10-14). Two things the popover's live chrome must not break:
//  1. a reader at the bottom of the conversation stays there when the chrome under it takes room: the thinking line, the
//     background box. Each takes height from the conversation list, and the list follows a streaming reply only from within
//     a few pixels of its bottom, so before this a long thread's reply streamed below the visible area once it began thinking;
//  2. the context gauge's click reads the thread's state at the click, by the chat's own rules: opened while the thread
//     worked, a click during its question compacted it anyway; opened while it compacted, every later click did nothing.
// And the background box's output tails, which now arrive on a frame of their own (threadBgOut) rather than in the comments
// frame, fill the box's open row and follow it as the command writes.
// The page is driven by the frames the kernel sends (a session, a comments frame, a liveStatus frame) and what it posts is read
// off a recording acquireVsCodeApi. Skips with a stated reason when no playwright browser is installed, and fails with it where
// CI requires the leg (browser-leg.ts). Synthetic prose and placeholder ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { chatBody, ATTACH_TITLE_WEB } from "../../vscode-extension/src/page-skeleton";
import { launchLeg } from "./browser-leg";

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

const SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";                // the session the comment was made on (`web`)
const TID = "11111111-2222-4333-8444-555555555555";                // the thread, which is its own session
const ANCHOR = "11111111-2222-4333-8444-666666666666";
const PASSAGE = "Keep the retry cap at two minutes";
const REPLY = PASSAGE + " on every sync path of the notes-api. Longer caps only hide an outage from the sync's own alarms.";

// the thread's conversation: long enough to scroll well past the popover's height
function threadEvents(n: number): object[] {
  const out: object[] = [];
  for (let i = 0; i < n; i++) {
    out.push({ kind: "user", md: `Question ${i}: should the export of the notes table retry on a timeout?`, uuid: `11111111-2222-4333-8444-7${String(i).padStart(11, "0")}` });
    out.push({ kind: "assistant", md: `Answer ${i}: yes, twice, with a backoff that doubles each time. The export writes its header once, so a retry `
      + `starts from the top of the table and the file stays whole.`, uuid: `11111111-2222-4333-8444-8${String(i).padStart(11, "0")}` });
  }
  return out;
}

// one running thread as the comments frame lists it; `chip` is the chat's state chip, `live` its build's status and tasks
function thread(chip: string, events: object[], live: object = {}): object {
  return { tid: TID, sid: TID, name: "web-comment-1", anchorUuid: ANCHOR, exact: PASSAGE, status: "open", createdT: 1_700_000_000,
           state: chip === "needsInput" ? "picker" : chip, chip, unread: false, replyOwed: chip === "working", promotedName: "",
           msgs: [], events, model: "Opus 5", effort: "high",
           live: { status: { state: chip, ctx: "41", ...live }, bgTasks: (live as any).bgTasks ?? null } };
}

async function inChat(t: any, body: (page: any, errors: string[]) => Promise<void>): Promise<void> {
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
    await page.evaluate(([sid, uuid, reply]: string[]) => {
      window.postMessage({ type: "session", id: sid, name: "web", cwd: "/tmp/TESTHOST/notes-api", status: { state: "idle", sinceEpoch: null },
                           events: [{ kind: "assistant", md: reply, uuid }] }, "*");
    }, [SID, ANCHOR, REPLY]);
    await page.waitForSelector("#content .turn-assistant .md p", { state: "attached", timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

const post = (page: any, msg: object) => page.evaluate((m: object) => window.postMessage(m, "*"), msg);
const comments = (page: any, th: object) => post(page, { type: "comments", id: SID, threads: [th], caps: ["threadAsks"] });

// open the thread's popover the way a reader does: a click on its highlight
async function openThread(page: any): Promise<void> {
  await page.waitForSelector(`mark.cmt-hl[data-tid="${TID}"]`, { state: "attached", timeout: 10000 });
  await page.evaluate((tid: string) => (document.querySelector(`mark.cmt-hl[data-tid="${tid}"]`) as HTMLElement).click(), TID);
  await page.waitForSelector("#cmt-pop .cmt-msgs .turn", { state: "attached", timeout: 10000 });
}

// two animation frames: layout, the resize observers and the scroll writes they make have all run
const settle = (page: any) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));

// how far the conversation list sits above its own bottom, and whether it overflows at all
const listDist = (page: any): Promise<{ dist: number; overflow: number }> => page.evaluate(() => {
  const l = document.querySelector("#cmt-pop .cmt-msgs") as HTMLElement;
  return { dist: l.scrollHeight - l.scrollTop - l.clientHeight, overflow: l.scrollHeight - l.clientHeight };
});

test("a reader at the bottom of a working thread stays there as its thinking line and its background box take room, the reply streams into view, and the box's output follows its own frame", async (t) => {
  await inChat(t, async (page, errors) => {
    const evs = threadEvents(14);
    await comments(page, thread("working", evs));
    await openThread(page);
    await settle(page);
    const open = await listDist(page);
    assert.ok(open.overflow > 200, "premise: the conversation scrolls well past the box: " + JSON.stringify(open));
    assert.ok(open.dist <= 2, "premise: the popover opens on the conversation's bottom: " + JSON.stringify(open));

    // the thread starts thinking: its line takes its room under the conversation
    await post(page, { type: "liveStatus", id: TID, thinking: { t: Date.now() / 1000 }, tasks: {} });
    await page.waitForFunction(() => /Thinking/.test(document.querySelector("#cmt-pop .cmt-live-status")?.textContent || ""), null, { timeout: 5000 });
    await settle(page);
    const thinking = await listDist(page);
    assert.ok(thinking.dist <= 2, "the thinking line's room kept the reader on the bottom: " + JSON.stringify(thinking));

    // the reply streams: the reader at the bottom follows it
    const replyUuid = "11111111-2222-4333-8444-999999999999";
    const streamed = [...evs, { kind: "assistant", md: "The export now retries twice. Rerunning the notes table export to check the header.", uuid: replyUuid }];
    await comments(page, thread("working", streamed));
    await page.waitForFunction((u: string) => !!document.querySelector(`#cmt-pop .cmt-msgs [data-uuid="${u}"]`)
      || /Rerunning the notes table export/.test(document.querySelector("#cmt-pop .cmt-msgs")?.textContent || ""), replyUuid, { timeout: 5000 });
    await settle(page);
    const followed = await listDist(page);
    assert.ok(followed.dist <= 2, "the streaming reply came into view: " + JSON.stringify(followed));

    // a background command starts: its box takes its room too, and the reader stays on the bottom
    const items = [{ kind: "commands", label: "export the notes table", id: "tu-export" }];
    await comments(page, thread("working", streamed, {
      awaitingWhy: "export the notes table", awaitingItems: items, awaitingTaskIds: ["tu-export"],
      bgTasks: { count: 1, tasks: [{ id: "tu-export", status: "running", summary: "export the notes table", command: "make export" }] } }));
    await page.waitForFunction(() => { const b = document.querySelector("#cmt-pop .cmt-bg") as HTMLElement | null; return !!b && b.style.display !== "none" && b.offsetHeight > 0; }, null, { timeout: 5000 });
    await settle(page);
    const boxed = await listDist(page);
    assert.ok(boxed.dist <= 2, "the background box's room kept the reader on the bottom: " + JSON.stringify(boxed));

    // the command's output tail arrives on its own frame (the comments frame carries the row without it) and moves with it
    const rowText = () => page.evaluate(() => document.querySelector("#cmt-pop .cmt-bg")?.textContent || "");
    await page.evaluate(() => (document.querySelector("#cmt-pop .cmt-bg [data-act='bg-fold']") as HTMLElement).click());
    await page.waitForSelector("#cmt-pop .cmt-bg [data-act='bg-toggle'][data-id='tu-export']", { state: "attached", timeout: 5000 });
    await page.evaluate(() => (document.querySelector("#cmt-pop .cmt-bg [data-act='bg-toggle'][data-id='tu-export']") as HTMLElement).click());
    await post(page, { type: "threadBgOut", id: TID, out: { "tu-export": "exported 120 rows" } });
    await page.waitForFunction(() => /exported 120 rows/.test(document.querySelector("#cmt-pop .cmt-bg")?.textContent || ""), null, { timeout: 5000 });
    await post(page, { type: "threadBgOut", id: TID, out: { "tu-export": "exported 120 rows\nexported 240 rows" } });
    await page.waitForFunction(() => /exported 240 rows/.test(document.querySelector("#cmt-pop .cmt-bg")?.textContent || ""), null, { timeout: 5000 });
    assert.match(await rowText(), /exported 240 rows/, "the row's fold shows the newest tail");
    await settle(page);
    // the box grew with its open row: a reader on the bottom is still there
    const grown = await listDist(page);
    assert.ok(grown.dist <= 2 || grown.overflow <= 0, "the opened row's room kept the reader on the bottom: " + JSON.stringify(grown));

    // a reader who scrolled up to read keeps their place when the chrome changes under them
    await page.evaluate(() => { const l = document.querySelector("#cmt-pop .cmt-msgs") as HTMLElement; l.scrollTop = 40; });
    await settle(page);
    await comments(page, thread("working", streamed));   // the box goes
    await page.waitForFunction(() => (document.querySelector("#cmt-pop .cmt-bg") as HTMLElement | null)?.style.display === "none", null, { timeout: 5000 });
    await settle(page);
    const top = await page.evaluate(() => (document.querySelector("#cmt-pop .cmt-msgs") as HTMLElement).scrollTop);
    assert.equal(top, 40, "a scrolled-up reader is never pulled to the bottom");
    assert.deepEqual(errors, []);
  });
});

test("a reply and the first thinking stretch landing in one frame leave the reader on the bottom too", async (t) => {
  await inChat(t, async (page, errors) => {
    const evs = threadEvents(14);
    await comments(page, thread("working", evs));
    await openThread(page);
    await settle(page);
    // the refill pins the reader to the new bottom, and the thinking line takes its room before that scroll is even reported
    const more = [...evs, { kind: "assistant", md: "Checking the export's header handling before the retry.", uuid: "11111111-2222-4333-8444-999999999998" }];
    await page.evaluate(([sid, tid, th]: [string, string, object]) => {
      window.postMessage({ type: "comments", id: sid, threads: [th], caps: ["threadAsks"] }, "*");
      window.postMessage({ type: "liveStatus", id: tid, thinking: { t: Date.now() / 1000 }, tasks: {} }, "*");
    }, [SID, TID, thread("working", more)] as [string, string, object]);
    await page.waitForFunction(() => /Thinking/.test(document.querySelector("#cmt-pop .cmt-live-status")?.textContent || "")
      && /header handling/.test(document.querySelector("#cmt-pop .cmt-msgs")?.textContent || ""), null, { timeout: 5000 });
    await settle(page);
    const d = await listDist(page);
    assert.ok(d.dist <= 2, "the reader is on the bottom: " + JSON.stringify(d));
    assert.deepEqual(errors, []);
  });
});

test("the context gauge compacts the thread by its state at the click, never the state the popover opened on", async (t) => {
  await inChat(t, async (page, errors) => {
    const evs = threadEvents(2);
    const compacts = () => page.evaluate(() => (window as any).__posted.filter((m: any) => m.type === "compactSession"));
    const click = () => page.evaluate(() => (document.querySelector("#cmt-pop .cmt-ctx") as HTMLElement).click());

    // opened while it works; then it stops on a question: the chat's battery refuses while a question waits on you
    await comments(page, thread("working", evs));
    await openThread(page);
    await page.waitForSelector("#cmt-pop .cmt-ctx", { state: "attached", timeout: 5000 });
    await comments(page, thread("needsInput", evs));
    await page.waitForFunction(() => /Needs you/i.test(document.querySelector("#cmt-pop #cmt-state")?.textContent || ""), null, { timeout: 5000 });
    await click();
    assert.deepEqual(await compacts(), [], "no compaction while the thread's question waits on the reader");

    // closed, reopened while it compacts, and the compaction ends: a click compacts the thread again
    await page.evaluate(() => (document.querySelector("#cmt-pop .cmt-x") as HTMLElement).click());
    await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 });
    await comments(page, thread("compacting", evs));
    await openThread(page);
    await click();
    assert.deepEqual(await compacts(), [], "premise: no second compaction while one runs");
    await comments(page, thread("ready", evs));
    await page.waitForFunction(() => !/Compacting/i.test(document.querySelector("#cmt-pop #cmt-state")?.textContent || ""), null, { timeout: 5000 });
    await click();
    assert.deepEqual((await compacts()).map((m: any) => m.id), [TID], "the click compacts the THREAD once its compaction is over");
    assert.deepEqual(errors, []);
  });
});
