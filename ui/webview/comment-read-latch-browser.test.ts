// Opening a comment thread reads it, and the read HOLDS until the kernel's frame shows it (2026-10-15, found in the review of
// #245, the unread badge that no longer moves on a scroll). The page clears the thread's unread mark the moment the reader
// opens it; a comments frame the kernel built before it recorded the read was already on its way, so with the box closed
// inside one round trip that frame put the mark back and the jump cluster's badge read 3, 2, 3, 2. A linked machine's
// relay makes the window longer. The kernel now counts each read it records (seenSeq on every frame) and answers the page's
// read with that count (commentSeenAck); until a frame reaches it, the frame's unread bit for the thread reads as read
// (read-latch.ts). Driven here on the REAL chat bundle (render.ts) in headless Chromium, by the frames a kernel sends:
//  1. open then close inside the frame window: a frame from before the read keeps the badge at 2, before and after the
//     kernel's answer; the frame that counts the read keeps it at 2; a NEW reply after the read brings it to 3;
//  2. a read the kernel refuses (commentSeenFailed): the thread is unread again and the page says why;
//  3. the link drops before the kernel answers: frames from the dead socket hold the read, and the first frame after the
//     link is back decides, read or not;
//  4. the same on a linked machine's session (host-prefixed ids, the read posted under that id), its relay slow: frames
//     from before the read arriving late, on both sides of the answer, and this page's own kernel dropping and returning
//     meanwhile, leave the badge at 2; its relay dropping before the answer lets the next frame decide.
// What the page posts is read off a recording acquireVsCodeApi. Skips with a stated reason when no playwright browser is
// installed, and fails with it where CI requires the leg (browser-leg.ts). Synthetic prose and placeholder ids only.
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

const SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";                // the session the comments are on (`web`)
const HOST = "TESTHOST";                                          // a linked machine
const N = 30;                                                     // exchanges: more than a screen, so the jump cluster shows
const uid = (lead: number, k: number) => `11111111-2222-4333-8444-${lead}${String(k).padStart(11, "0")}`;
const replyText = (k: number) => `The notes-api export for step ${k} streams rows through a bounded buffer, and its retry cap stays at two minutes.`;
const passage = (k: number) => `export for step ${k} streams rows`;
const ANCHORED = [27, 28, 29];                                    // the replies the three threads are on
const TIDS = ["22222222-3333-4444-8555-00000000000a", "22222222-3333-4444-8555-00000000000b", "22222222-3333-4444-8555-00000000000c"];
const CAPS = ["threadAsks", "seenAck"];

// one thread as the comments frame lists it; `host` prefixes its session id the way the federation layer does (the tid stays bare)
function row(i: number, host: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  const tid = TIDS[i];
  return { tid, sid: host ? host + ":" + tid : tid, name: `web-comment-${i + 1}`, anchorUuid: uid(2, ANCHORED[i]), exact: passage(ANCHORED[i]),
           status: "open", createdT: 1_700_000_000 + i, state: "", chip: "", unread: true, replyOwed: false, promotedName: "", seenSeq: 0,
           lastUuid: uid(4, i), msgs: [{ who: "you", text: "Why a bounded buffer here?", t: 1_700_000_100 },
                                       { who: "agent", text: "So the export never holds the whole table in memory.", t: 1_700_000_110 }],
           events: [{ kind: "user", md: "Why a bounded buffer here?", uuid: uid(3, i) },
                    { kind: "assistant", md: "So the export never holds the whole table in memory.", uuid: uid(4, i) }], ...over };
}
// thread 0 after a NEW reply landed in it (after the kernel recorded the read: its count stands, its bit is unread again)
const replied = (host: string, seq: number) => row(0, host, {
  unread: true, seenSeq: seq, lastUuid: uid(5, 0),
  msgs: [...(row(0, host).msgs as object[]), { who: "agent", text: "The buffer holds two thousand rows; that bound is the header's too.", t: 1_700_000_900 }],
  events: [...(row(0, host).events as object[]), { kind: "assistant", md: "The buffer holds two thousand rows; that bound is the header's too.", uuid: uid(5, 0) }] });

async function inChat(t: any, sid: string, body: (page: any, errors: string[]) => Promise<void>): Promise<void> {
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
    const events: object[] = [];
    for (let k = 0; k < N; k++) {
      events.push({ kind: "user", md: `Step ${k}: how should the export of the notes table handle a slow disk?`, uuid: uid(1, k) });
      events.push({ kind: "assistant", md: replyText(k), uuid: uid(2, k) });
    }
    // the probe counter: a listener added after the bundle's own, so it counts a message once the page has handled it
    await page.evaluate(([id, evs]: [string, object[]]) => {
      (window as any).__handled = 0;
      window.addEventListener("message", (e: MessageEvent) => { if (e.data && e.data.__probe) (window as any).__handled++; });
      window.postMessage({ type: "session", id, name: "web", cwd: "/tmp/TESTHOST/notes-api", status: { state: "idle", sinceEpoch: null }, events: evs }, "*");
    }, [sid, events] as [string, object[]]);
    await page.waitForSelector("#content .turn-assistant .md p", { state: "attached", timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

// two animation frames: the marks pass and the cluster's update have run
const settle = (page: any) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));

/** Post kernel frames in order, then wait until the page has handled every one. */
async function send(page: any, ...msgs: object[]): Promise<void> {
  const n0 = await page.evaluate(() => (window as any).__handled);
  await page.evaluate((ms: object[]) => { for (const m of ms) window.postMessage({ ...m, __probe: 1 }, "*"); }, msgs);
  await page.waitForFunction((n: number) => (window as any).__handled >= n, n0 + msgs.length, { timeout: 5000 });
  await settle(page);
}
const frame = (sid: string, rows: object[]) => ({ type: "comments", id: sid, threads: rows, caps: CAPS });
const edge = (page: any, type: string, host?: string) => page.evaluate(([ty, h]: [string, string | undefined]) => {
  window.dispatchEvent(h === undefined ? new Event(ty) : new CustomEvent(ty, { detail: { host: h } }));
}, [type, host] as [string, string | undefined]);

/** The jump cluster's unread badge ("" when it shows none). */
const badge = (page: any): Promise<string> => page.evaluate(() => {
  const c = document.querySelector("#jump-cluster .jc-count") as HTMLElement | null;
  return !c || c.hidden ? "" : (c.textContent || "").trim();
});
const markUnread = (page: any, tid: string): Promise<boolean> => page.evaluate((t: string) =>
  !!document.querySelector(`mark.cmt-hl[data-tid="${t}"].unread`), tid);

/** Open thread `tid` the way a reader does (a click on its highlight), and return the read the page posted for it. */
async function openThread(page: any, sid: string, tid: string): Promise<any> {
  await page.waitForSelector(`mark.cmt-hl[data-tid="${tid}"]`, { state: "attached", timeout: 10000 });
  const n0 = await page.evaluate(() => (window as any).__posted.length);
  await page.evaluate((t: string) => (document.querySelector(`mark.cmt-hl[data-tid="${t}"]`) as HTMLElement).click(), tid);
  await page.waitForSelector("#cmt-pop", { state: "attached", timeout: 10000 });
  await settle(page);
  const reads = await page.evaluate(([n, s, t]: [number, string, string]) =>
    (window as any).__posted.slice(n).filter((m: any) => m.type === "commentSeen" && m.id === s && m.tid === t), [n0, sid, tid] as [number, string, string]);
  assert.equal(reads.length, 1, "opening the thread posts one read, under the session's own id: " + JSON.stringify(reads));
  assert.equal(typeof reads[0].rid, "number", "numbered, so the kernel's answer names it");
  return reads[0];
}
async function closeThread(page: any): Promise<void> {
  await page.evaluate(() => (document.querySelector("#cmt-pop .cmt-x") as HTMLElement).click());
  await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 });
  await settle(page);
}

test("a thread opened and closed inside one round trip stays read: frames from before the read keep the badge at 2, and a reply after the read brings it to 3", async (t) => {
  await inChat(t, SID, async (page, errors) => {
    const before = [row(0, ""), row(1, ""), row(2, "")];            // what every frame built before the read says
    await send(page, frame(SID, before));
    assert.equal(await badge(page), "3", "premise: three unread threads");
    const read = await openThread(page, SID, TIDS[0]);
    assert.equal(await badge(page), "2", "opening the thread reads it");
    await closeThread(page);
    await send(page, frame(SID, before));                           // the frame already on its way when the box closed
    assert.equal(await badge(page), "2", "a frame from before the read does not undo it");
    assert.equal(await markUnread(page, TIDS[0]), false, "…and its mark stays plain");
    await send(page, { type: "commentSeenAck", id: SID, tid: TIDS[0], rid: read.rid, seq: 1 }, frame(SID, before));
    assert.equal(await badge(page), "2", "the kernel's answer names the count; a frame below it is still from before the read");
    await send(page, frame(SID, [row(0, "", { unread: false, seenSeq: 1 }), row(1, ""), row(2, "")]));
    assert.equal(await badge(page), "2", "the frame that counts the read");
    await send(page, frame(SID, [replied("", 1), row(1, ""), row(2, "")]));
    assert.equal(await badge(page), "3", "a new reply after the read is unread");
    assert.equal(await markUnread(page, TIDS[0]), true);
    assert.deepEqual(errors, []);
  });
});

test("a read the kernel refuses shows the thread unread again and says why", async (t) => {
  await inChat(t, SID, async (page, errors) => {
    const before = [row(0, ""), row(1, ""), row(2, "")];
    await send(page, frame(SID, before));
    const read = await openThread(page, SID, TIDS[1]);
    await closeThread(page);
    await send(page, frame(SID, before));
    assert.equal(await badge(page), "2", "held while the kernel has not answered");
    await send(page, { type: "commentSeenFailed", id: SID, tid: TIDS[1], rid: read.rid,
                       error: "the comments store could not be read or written: No space left on device" });
    assert.equal(await badge(page), "3", "the read was not recorded: the thread is unread, as the kernel's frame says");
    assert.equal(await markUnread(page, TIDS[1]), true);
    const toast = await page.evaluate(() => Array.from(document.querySelectorAll("#warn-toasts .warn-toast")).map((x) => x.textContent || "").join("\n"));
    assert.match(toast, /web-comment-2/, "the toast names the thread");
    assert.match(toast, /No space left on device/, "…and why");
    assert.deepEqual(errors, []);
  });
});

test("the link drops before the kernel answers the read: frames from the dead socket hold it, and the first frame after the link is back decides", async (t) => {
  await inChat(t, SID, async (page, errors) => {
    const before = [row(0, ""), row(1, ""), row(2, "")];
    await send(page, frame(SID, before));
    await openThread(page, SID, TIDS[0]);
    await closeThread(page);
    await edge(page, "romp:wsdown");
    await send(page, frame(SID, before));                           // the old socket's last frames, handed on after its close
    assert.equal(await badge(page), "2", "a frame from the dropped link does not decide");
    await edge(page, "romp:wsup");
    await send(page, frame(SID, before));                           // the new link's frame: the read never arrived
    assert.equal(await badge(page), "3", "the read was lost with the link: the thread shows unread, never held forever");
    // read again, and this time the read got there before the link dropped
    await openThread(page, SID, TIDS[0]);
    await closeThread(page);
    await edge(page, "romp:wsdown");
    await edge(page, "romp:wsup");
    await send(page, frame(SID, [row(0, "", { unread: false, seenSeq: 1 }), row(1, ""), row(2, "")]));
    assert.equal(await badge(page), "2", "the new link's frame shows the read");
    await send(page, frame(SID, [replied("", 1), row(1, ""), row(2, "")]));
    assert.equal(await badge(page), "3", "and nothing holds a later reply");
    assert.deepEqual(errors, []);
  });
});

test("a linked machine's thread: the read goes under the host-prefixed id, and a slow relay's late frames never undo it", async (t) => {
  const RSID = HOST + ":" + SID;
  await inChat(t, RSID, async (page, errors) => {
    const before = [row(0, HOST), row(1, HOST), row(2, HOST)];
    await send(page, frame(RSID, before));
    assert.equal(await badge(page), "3", "premise");
    const read = await openThread(page, RSID, TIDS[0]);             // the federation layer strips the prefix on the way out
    await closeThread(page);
    // the relay is slow: frames built before the read keep arriving, and this page's OWN kernel drops and comes back
    // meanwhile, which says nothing about the linked machine's link
    await send(page, frame(RSID, before), frame(RSID, before));
    await edge(page, "romp:wsdown");
    await edge(page, "romp:wsup");
    await send(page, frame(RSID, before));
    assert.equal(await badge(page), "2", "late frames from before the read, and a drop of another link, leave it read");
    // the answer, prefixed on the way in like every id the linked kernel sends, then one more late frame
    await send(page, { type: "commentSeenAck", id: RSID, tid: TIDS[0], rid: read.rid, seq: 4 }, frame(RSID, before));
    assert.equal(await badge(page), "2");
    await send(page, frame(RSID, [row(0, HOST, { unread: false, seenSeq: 4 }), row(1, HOST), row(2, HOST)]));
    assert.equal(await badge(page), "2", "the frame that counts the read");
    await send(page, frame(RSID, [replied(HOST, 4), row(1, HOST), row(2, HOST)]));
    assert.equal(await badge(page), "3", "a new reply after the read is unread");
    // read once more, and the linked machine's relay drops before its kernel answers: the next frame on the new relay decides
    await openThread(page, RSID, TIDS[0]);
    await closeThread(page);
    await edge(page, "romp:hostRelayDown", HOST);
    await send(page, frame(RSID, [replied(HOST, 4), row(1, HOST), row(2, HOST)]));
    assert.equal(await badge(page), "2", "the dropped relay's last frame does not decide");
    await edge(page, "romp:hostRelayUp", HOST);
    await send(page, frame(RSID, [row(0, HOST, { unread: false, seenSeq: 5, lastUuid: uid(5, 0) }), row(1, HOST), row(2, HOST)]));
    assert.equal(await badge(page), "2", "the read got there: read");
    assert.deepEqual(errors, []);
  });
});
