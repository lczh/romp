// A dropped comment create's dialog (comment-drops.ts; review of PR 218, 2026-10-11). The kernel offers a pane that
// starts listening every drop no pane has acknowledged, at once, and counts a drop as told only on the pane's
// acknowledgement. Each page drew one dialog per frame, a newer one replacing the last, and acknowledged each frame on
// arrival: two drops showed one dialog while both were acknowledged. Now one dialog lists every drop that arrives while it
// is up, each acknowledged only once its entry is drawn; the feed, which drew a drop again on every offer, remembers
// which it drew as the chat page does. And a drop is acknowledged only once SOMEONE SAW IT (review of PR 223): a page
// nobody can see hands it back to its kernel for a page someone can, a page on screen without the focus waits for the
// focus or the user's dismissal, and no newer dialog takes the drops dialog off the screen. SYNTHETIC fixtures.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { CmtDrops, cmtDropOf, cmtDropEntry, CMT_DROPS_TITLE, type CmtDrop, type CmtDropView } from "./comment-drops";
import type { Sight } from "./paint-gate";

const SID = "11111111-2222-3333-4444-555555555555";
const drop = (createId: string, copy = "Why ten percent?") => ({
  type: "err", op: "commentCreate", createId, sid: SID, title: "That comment did not start a thread",
  text: "Your comment on web waited for the message it quotes to reach the session's transcript, and no thread was started.",
  copy,
});

function world(start: Sight = "seen") {
  const events: string[] = [];
  let open = false;
  let failAdd = false;
  let sight: Sight = start;
  const view: CmtDropView = {
    isOpen: () => open,
    open: (title) => { open = true; events.push("open:" + title); },
    add: (d) => { if (failAdd) throw new Error("no document"); events.push("add:" + d.createId); },
    retitle: (title) => events.push("retitle:" + title),
    sight: () => sight,
    ack: (d) => events.push("ack:" + d.createId),
    hidden: (d, hidden) => events.push((hidden ? "handback:" : "want:") + d.createId),
    log: (d) => events.push("log:" + d.createId),
  };
  const drops = new CmtDrops(view);
  return { events, drops, failAdds: (v: boolean) => { failAdd = v; },
           see: (s: Sight) => { sight = s; drops.settle(); },     // the page's sight changes, and its event fires
           close: () => { open = false; drops.dismissed(); } };   // the user dismisses the dialog
}

test("two drops offered at once are both drawn in one dialog, each acknowledged only after its entry is drawn", () => {
  const w = world();
  assert.equal(w.drops.take(drop("g-one")), true);
  assert.equal(w.drops.take(drop("g-two")), true);
  assert.deepEqual(w.events, [
    "open:That comment did not start a thread", "add:g-one", "log:g-one", "ack:g-one",
    "add:g-two", "retitle:" + CMT_DROPS_TITLE, "log:g-two", "ack:g-two",
  ], "one dialog, both listed, the title plural once it holds two, a Log entry and an acknowledgement for each");
});

test("a drop this page drew already is acknowledged again and never drawn twice; a closed dialog makes the next one fresh", () => {
  const w = world();
  w.drops.take(drop("g-one"));
  w.close();                                         // dismissed
  w.events.length = 0;
  w.drops.take(drop("g-one"));                       // offered again: the acknowledgement was lost with a socket
  assert.deepEqual(w.events, ["ack:g-one"]);
  w.drops.take(drop("g-two"));
  assert.deepEqual(w.events, ["ack:g-one", "open:That comment did not start a thread", "add:g-two", "log:g-two", "ack:g-two"],
                   "a fresh dialog, singular again");
  w.drops.take({ ...drop("g-one"), sid: "TESTHOST:" + SID });
  assert.equal(w.events.filter((e) => e === "add:g-one").length, 1, "the same createId on another kernel is its own drop");
});

test("an entry that could not be drawn is not acknowledged, so the kernel offers it again", () => {
  const w = world();
  w.failAdds(true);
  assert.throws(() => w.drops.take(drop("g-fail")));
  assert.ok(!w.events.some((e) => e.startsWith("ack:")), "nothing acknowledged");
  w.failAdds(false);
  w.drops.take(drop("g-fail"));
  assert.ok(w.events.includes("add:g-fail") && w.events.includes("ack:g-fail"), "the next offer draws it");
});

test("a drop offered while nobody can see the page is not drawn or acknowledged: it is handed back, and asked for again once someone can", () => {
  const w = world("hidden");                         // a background tab, or a pane off screen, says ready first after a restart
  assert.equal(w.drops.take(drop("g-bg")), true);
  assert.deepEqual(w.events, ["handback:g-bg"], "nothing drawn, nothing acknowledged: the kernel offers it to a page someone can see");
  w.see("hidden");
  assert.deepEqual(w.events, ["handback:g-bg"], "still nobody: nothing asked");
  w.see("shown");                                    // the user switches to this tab
  assert.deepEqual(w.events, ["handback:g-bg", "want:g-bg"], "asked for again once, the moment someone can see the page");
  w.see("seen");
  assert.equal(w.events.length, 2, "asked once only");
  w.drops.take(drop("g-bg"));                        // the kernel offers it back: no other page holds it
  assert.deepEqual(w.events.slice(2), ["open:That comment did not start a thread", "add:g-bg", "log:g-bg", "ack:g-bg"]);
});

test("a drop drawn on a page on screen without the focus is acknowledged once the page is focused, or the user dismisses it", () => {
  const w = world("shown");                          // another window, or another pane of the dashboard, holds the focus
  w.drops.take(drop("g-one"));
  assert.deepEqual(w.events, ["open:That comment did not start a thread", "add:g-one", "log:g-one"], "drawn, not yet acknowledged");
  w.see("hidden");                                   // the user went elsewhere: it stays drawn here, waiting
  w.drops.take(drop("g-one"));                       // offered again on a redial: still listed, so neither drawn twice nor handed back
  assert.equal(w.events.length, 3);
  w.see("seen");
  assert.deepEqual(w.events.slice(3), ["ack:g-one"], "the user is here: seen");
  w.drops.take(drop("g-two"));
  assert.deepEqual(w.events.slice(4), ["add:g-two", "retitle:" + CMT_DROPS_TITLE, "log:g-two", "ack:g-two"]);

  const v = world("shown");
  v.drops.take(drop("g-three"));
  v.close();                                         // the user's own dismissal: they saw it
  assert.deepEqual(v.events.slice(-1), ["ack:g-three"]);
  v.drops.take(drop("g-three"));
  assert.deepEqual(v.events.slice(-1), ["ack:g-three"], "offered again after a lost acknowledgement: acknowledged, not drawn");
  assert.equal(v.events.filter((e) => e === "add:g-three").length, 1);
});

test("only a frame naming a dropped create is a drop: every other err frame stays the page's own dialog", () => {
  const w = world();
  for (const m of [{ type: "err", op: "sendMessage", sid: SID, text: "x" }, { ...drop("g"), createId: "" }, { ...drop("g"), sid: "" },
                   { ...drop("g"), text: "" }, { ...drop("g"), type: "warn" }, null])
    assert.equal(w.drops.take(m), false);
  assert.deepEqual(w.events, []);
  assert.deepEqual(cmtDropOf({ ...drop("g"), title: "" }) as CmtDrop,
                   { sid: SID, createId: "g", text: drop("g").text, copy: "Why ten percent?", title: "That comment did not start a thread" });
});

// a stand-in element: enough of the DOM for cmtDropEntry
class FakeEl {
  children: FakeEl[] = [];
  textContent = "";
  title = "";
  private clicks: Array<() => void> = [];
  constructor(public tag: string, public cls = "") {}
  appendChild(c: FakeEl) { this.children.push(c); return c; }
  addEventListener(kind: string, fn: () => void) { if (kind === "click") this.clicks.push(fn); }
  click() { for (const f of this.clicks) f(); }
  find(cls: string): FakeEl | undefined {
    for (const c of this.children) { if (c.cls.split(" ").includes(cls)) return c; const f = c.find(cls); if (f) return f; }
    return undefined;
  }
}

test("each entry shows the kernel's account and the words on one line with their own Copy, which says it copied", () => {
  const make = (tag: string, cls?: string) => new FakeEl(tag, cls) as unknown as HTMLElement;
  const copied: string[] = [];
  const entry = cmtDropEntry(cmtDropOf(drop("g-one"))!, make, { detail: "confirm-detail", button: "confirm-btn" },
                             (t) => copied.push(t)) as unknown as FakeEl;
  assert.equal(entry.find("confirm-detail")!.textContent, drop("g-one").text);
  const words = entry.find("cmt-drop-quote")!;
  assert.equal(words.textContent, "Why ten percent?");
  assert.equal(words.title, "Why ten percent?", "the whole text on hover when the line clips it");
  const btn = entry.find("confirm-btn")!;
  assert.equal(btn.textContent, "Copy my text");
  btn.click();
  assert.deepEqual(copied, ["Why ten percent?"]);
  assert.equal(btn.textContent, "Copied", "the click is acknowledged at once");
  const bare = cmtDropEntry(cmtDropOf(drop("g-two", ""))!, make, { detail: "d", button: "b" }, () => {}) as unknown as FakeEl;
  assert.equal(bare.find("b"), undefined, "no words, no Copy (a restored drop whose words could not be read back)");
});

// ── both pages draw drops through it (no jsdom for these renderers: wiring pinned at source) ──
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("the chat page and the feed both route a drop to the one dialog ahead of their own err dialog, and acknowledge it nowhere else", () => {
  for (const [name, src, generic] of [["chat", RENDER, 'else if (m.type === "err" && typeof m.text === "string" && m.text) {'],
                                      ["feed", FEED, '} else if (m.type === "err" && typeof m.text === "string" && m.text) {']] as const) {
    assert.equal((src.match(/new CmtDrops\(\{/g) || []).length, 1, name + ": one drops dialog per page");
    const arm = src.indexOf('else if (m.type === "err" && cmtDrops.take(m)) {');
    assert.ok(arm > 0 && arm < src.indexOf(generic), name + ": the drop's arm sits ahead of the err dialog's");
    assert.equal((src.match(/commentDropSeen/g) || []).length, 1, name + ": the acknowledgement is posted from the view's ack alone");
    assert.match(src, /ack: \(d\) => vscodeApi\?\.postMessage\(\{ type: "commentDropSeen", id: d\.sid, createId: d\.createId \}\),/);
    assert.match(src, /hidden: \(d, hidden\) => vscodeApi\?\.postMessage\(\{ type: "commentDropHidden", id: d\.sid, createId: d\.createId, hidden \}\),/,
                 name + ": a drop nobody can see here goes back to its kernel");
    assert.match(src, /sight: \(\) => paneSight\(\),/, name + ": who can see the page is paint-gate.ts's answer");
    for (const ev of ['window.addEventListener("focus", () => cmtDrops.settle());', 'window.addEventListener("resize", () => cmtDrops.settle());',
                      'document.addEventListener("visibilitychange", () => cmtDrops.settle());'])
      assert.ok(src.includes(ev), name + ": settles on " + ev);
    assert.match(src, /isOpen: \(\) => !!cmtDropList && cmtDropList\.isConnected,/, name + ": a dismissed or replaced dialog is not added to");
  }
  assert.doesNotMatch(RENDER, /cmtDropDrawnBefore|cmtDropsDrawn/, "the per-frame acknowledgement on arrival is gone");
  // the chat page's dialog is the confirm dialog, carrying the list under its title, PINNED: only the user's dismissal closes it
  assert.match(RENDER, /showConfirm\(title, "", \[\{ label: "Dismiss", value: "ok" \}\], \(\) => cmtDrops\.dismissed\(\), cmtDropList, true\);/);
  assert.match(RENDER, /^function paneReturned\(\): void \{ renderNoticeDisclosures\(\); cmtDrops\.settle\(\); \}/m, "chat: the observer's return edge settles it");
  assert.match(FEED, /live\.catchUp\(\);[^\n]*\n\s*cmtDrops\.settle\(\);/, "feed: the visibility observer's report settles it");
  assert.match(RENDER, /box\.appendChild\(h\); if \(detail\) box\.appendChild\(d\); if \(body\) box\.appendChild\(body\); box\.appendChild\(actions\);/);
  // the feed's is in the err dialog's slot: it replaces an older err dialog, and a newer one waits behind it
  const dlg = FEED.slice(FEED.indexOf("function showCmtDropDialog(title: string): HTMLElement {"), FEED.indexOf("// The kernel's LOUD channel"));
  assert.match(dlg, /document\.getElementById\("err-dialog"\)\?\.remove\(\);\s*\n\s*const overlay = el\("div", "pickdlg-overlay"\); overlay\.id = "err-dialog";/);
  assert.match(dlg, /overlay\.appendChild\(box\);\s*\n\s*document\.body\.appendChild\(overlay\);/, "the box goes into the overlay");
  assert.match(dlg, /ok\.onclick = \(\) => dismissCmtDrops\(overlay\);/, "Dismiss closes it, as the user's dismissal");
  assert.match(dlg, /overlay\.onclick = \(e\) => \{ if \(e\.target === overlay\) dismissCmtDrops\(overlay\); \};/, "…and so does the backdrop");
});

// ── no newer dialog takes the drops dialog off the screen (review of PR 223), EXECUTED over a fake DOM ──
const requireCjs = createRequire(__filename);
const lift = (src: string, a: string, b: string) => {
  const i = src.indexOf(a), j = src.indexOf(b, i);
  assert.ok(i > 0 && j > i, "anchors moved: " + a.slice(0, 40) + " / " + b.slice(0, 40));
  return requireCjs("esbuild").transformSync(src.slice(i, j), { loader: "ts" }).code;
};
class DomEl {
  children: DomEl[] = []; parent: DomEl | null = null; listeners: Record<string, Function[]> = {};
  textContent = ""; id = ""; onclick: ((e: any) => void) | null = null;
  constructor(public tag: string, public className = "") {}
  appendChild(c: DomEl): DomEl { c.parent = this; this.children.push(c); return c; }
  append(...cs: DomEl[]): void { for (const c of cs) this.appendChild(c); }
  remove(): void { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
  get isConnected(): boolean { let e: DomEl | null = this; while (e.parent) e = e.parent; return e.tag === "body"; }
  get parentElement(): DomEl | null { return this.parent; }
  get firstElementChild(): DomEl | null { return this.children[0] ?? null; }
  querySelector(sel: string): DomEl | null { return this.walk().find((e) => e.className.split(" ").includes(sel.slice(1))) ?? null; }
  addEventListener(t: string, f: Function): void { (this.listeners[t] ??= []).push(f); }
  fire(t: string): void { for (const f of this.listeners[t] ?? []) f({ target: this }); if (t === "click" && this.onclick) this.onclick({ target: this }); }
  focus(): void {}
  walk(): DomEl[] { return this.children.flatMap((c) => [c, ...c.walk()]); }
}
function dom() {
  const body = new DomEl("body");
  const keys: Function[] = [];
  return { body, keys,
           getElementById: (id: string) => body.walk().find((e) => e.id === id) ?? null,
           addEventListener: (t: string, f: Function) => { if (t === "keydown") keys.push(f); },
           removeEventListener: (t: string, f: Function) => { const i = keys.indexOf(f); if (t === "keydown" && i >= 0) keys.splice(i, 1); } };
}
const titleOf = (body: DomEl, cls: string) => body.walk().find((e) => e.className.split(" ").includes(cls))?.textContent ?? null;

test("chat: a dialog opened over the pinned drops dialog waits behind it, a caller's close cancels only its own, and the user's dismissal opens it", () => {
  const confirm = lift(RENDER, "let confirmCb: ((v: string | null) => void) | null = null;", "// ---- move session")
    + lift(RENDER, "function closeConfirm(value: string | null) {", "// Inline validation message under the search box");
  const doc = dom();
  const api = new Function("document", "DomEl", `const el = (t, c) => new DomEl(t, c);\n${confirm}\nreturn { showConfirm, closeConfirm };`)(doc, DomEl);
  const answers: string[] = [];
  const list = new DomEl("div", "cmt-drop-list");
  api.showConfirm("That comment did not start a thread", "", [{ label: "Dismiss", value: "ok" }], (v: any) => answers.push("drops:" + v), list, true);
  api.showConfirm("That action was not delivered", "romp could not reach api.", [{ label: "Dismiss", value: "ok" }], (v: any) => answers.push("err:" + v));
  assert.equal(titleOf(doc.body, "confirm-title"), "That comment did not start a thread", "the drops stay on screen");
  assert.ok(list.isConnected);
  api.showConfirm("An attachment is still uploading", "", [{ label: "Wait for the upload", value: "wait" }], (v: any) => answers.push("gate:" + v));
  assert.deepEqual(answers, ["err:null"], "a newer held dialog cancels the older held one, as it would on screen");
  api.closeConfirm(null);                            // the upload finished: its caller closes the dialog IT opened
  assert.deepEqual(answers, ["err:null", "gate:null"], "the held one, never the drops dialog");
  assert.ok(list.isConnected, "the drops are still on screen");
  api.showConfirm("That action was not delivered", "romp could not reach web.", [{ label: "Dismiss", value: "ok" }], (v: any) => answers.push("err2:" + v));
  for (const f of [...doc.keys]) f({ key: "Escape", stopPropagation() {} });   // the user's own answer
  assert.deepEqual(answers, ["err:null", "gate:null", "drops:null"], "Esc is the user's dismissal of the drops");
  assert.ok(!list.isConnected);
  assert.equal(titleOf(doc.body, "confirm-title"), "That action was not delivered", "and the dialog waiting behind opens");
  api.showConfirm("End web?", "", [{ label: "End", value: "end" }], (v: any) => answers.push("end:" + v));
  assert.deepEqual(answers.slice(-1), ["err2:null"], "an unpinned dialog is replaced as before");
});

test("feed: an err dialog arriving over the drops dialog waits behind it and opens on the user's dismissal", () => {
  const code = lift(FEED, "let cmtDropList: HTMLElement | null = null;", "// White outline for cards");
  const doc = dom();
  const posted: any[] = [];
  const win = { listeners: {} as Record<string, Function[]>, parent: { postMessage: () => {} },
                addEventListener(t: string, f: Function) { (this.listeners[t] ??= []).push(f); } };
  const api = new Function("document", "window", "DomEl", "CmtDrops", "cmtDropEntry", "paneSight", "vscodeApi", "navigator",
    `const el = (t, c) => new DomEl(t, c);\n${code}\nreturn { cmtDrops, showErrDialog };`)(
    doc, win, DomEl, CmtDrops, cmtDropEntry, () => "shown", { postMessage: (m: any) => posted.push(m) }, {});
  api.cmtDrops.take(drop("g-feed"));
  assert.deepEqual(posted, [], "on screen without the focus: drawn, not yet acknowledged");
  api.showErrDialog("That reply was not delivered", "romp could not reach api.", "");
  assert.equal(titleOf(doc.body, "pickdlg-title"), "That comment did not start a thread", "the drops dialog stays");
  api.showErrDialog("That reply was not delivered", "romp could not reach web.", "");
  const ok = doc.body.walk().find((e) => e.textContent === "Dismiss")!;
  ok.fire("click");                                  // the user's dismissal
  assert.deepEqual(posted, [{ type: "commentDropSeen", id: SID, createId: "g-feed" }], "seen: acknowledged");
  assert.equal(titleOf(doc.body, "pickdlg-title"), "That reply was not delivered");
  assert.equal(titleOf(doc.body, "pickdlg-detail"), "romp could not reach web.", "the newest waiting dialog opens");
});
