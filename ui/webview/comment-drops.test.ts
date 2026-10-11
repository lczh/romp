// A dropped comment create's dialog (comment-drops.ts; review of PR 218, 2026-10-11). The kernel offers a pane that
// starts listening every drop no pane has acknowledged, at once, and counts a drop as told only on the pane's
// acknowledgement. Each page drew one dialog per frame, a newer one replacing the last, and acknowledged each frame on
// arrival: two drops showed one dialog while both were acknowledged. Now one dialog lists every drop that arrives while it
// is up, each acknowledged only once its entry is drawn; the feed, which drew a drop again on every offer, remembers
// which it drew as the chat page does. SYNTHETIC fixtures.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CmtDrops, cmtDropOf, cmtDropEntry, CMT_DROPS_TITLE, type CmtDrop, type CmtDropView } from "./comment-drops";

const SID = "11111111-2222-3333-4444-555555555555";
const drop = (createId: string, copy = "Why ten percent?") => ({
  type: "err", op: "commentCreate", createId, sid: SID, title: "That comment did not start a thread",
  text: "Your comment on web waited for the message it quotes to reach the session's transcript, and no thread was started.",
  copy,
});

function world() {
  const events: string[] = [];
  let open = false;
  let failAdd = false;
  const view: CmtDropView = {
    isOpen: () => open,
    open: (title) => { open = true; events.push("open:" + title); },
    add: (d) => { if (failAdd) throw new Error("no document"); events.push("add:" + d.createId); },
    retitle: (title) => events.push("retitle:" + title),
    ack: (d) => events.push("ack:" + d.createId),
    log: (d) => events.push("log:" + d.createId),
  };
  return { events, drops: new CmtDrops(view), close: () => { open = false; }, failAdds: (v: boolean) => { failAdd = v; } };
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
  w.close();                                         // dismissed, or replaced by a newer dialog
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
    assert.match(src, /isOpen: \(\) => !!cmtDropList && cmtDropList\.isConnected,/, name + ": a dismissed or replaced dialog is not added to");
  }
  assert.doesNotMatch(RENDER, /cmtDropDrawnBefore|cmtDropsDrawn/, "the per-frame acknowledgement on arrival is gone");
  // the chat page's dialog is the confirm dialog, carrying the list under its title
  assert.match(RENDER, /showConfirm\(title, "", \[\{ label: "Dismiss", value: "ok" \}\], \(\) => \{\}, cmtDropList\);/);
  assert.match(RENDER, /box\.appendChild\(h\); if \(detail\) box\.appendChild\(d\); if \(body\) box\.appendChild\(body\); box\.appendChild\(actions\);/);
  // the feed's is in the err dialog's slot: a newer err dialog replaces it and it replaces an older one
  const dlg = FEED.slice(FEED.indexOf("function showCmtDropDialog(title: string): HTMLElement {"), FEED.indexOf("// The kernel's LOUD channel"));
  assert.match(dlg, /document\.getElementById\("err-dialog"\)\?\.remove\(\);\s*\n\s*const overlay = el\("div", "pickdlg-overlay"\); overlay\.id = "err-dialog";/);
  assert.match(dlg, /overlay\.appendChild\(box\);\s*\n\s*document\.body\.appendChild\(overlay\);/, "the box goes into the overlay");
  assert.match(dlg, /ok\.onclick = \(\) => overlay\.remove\(\);/, "Dismiss closes it");
});
