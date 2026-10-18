// page-focus.ts, executed against a fake page: a top window and its frames, the focus moved the way Chromium moves it (the
// focused frame set BEFORE the blur of the frame losing it; the page's focus dropped BEFORE a window switch's blur, which goes
// to the window holding the focus and to no other). The two gestures that use it are driven for real in the served labs: the
// kit's divider drag in tests/test_live_dividers_served.py and the chat strip's press-hold in tests/test_chat_split_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { pageBlurred, pageFocusWindow, watchPageFocus } from "./page-focus";

type Fn = (e: { type: string }) => void;
class Target {
  ls = new Map<string, Set<Fn>>();
  addEventListener(t: string, f: Fn): void { if (!this.ls.has(t)) this.ls.set(t, new Set()); this.ls.get(t)!.add(f); }
  removeEventListener(t: string, f: Fn): void { this.ls.get(t)?.delete(f); }
  fire(t: string): void { for (const f of Array.from(this.ls.get(t) || [])) f({ type: t }); }
  n(t: string): number { return this.ls.get(t)?.size || 0; }
}
class Frame extends Target {
  tagName = "IFRAME";
  constructor(public contentWindow: Win | null) { super(); }
}
class Win extends Target {
  top!: Win; active: Target | Frame | null = null; crossOrigin = false;
  constructor(public page: Page, public name: string) { super(); }
  get document(): { hasFocus: () => boolean; activeElement: unknown } {
    if (this.crossOrigin) throw new Error("SecurityError: a cross-origin frame");
    return { hasFocus: () => this.page.focused && this === this.top, activeElement: this.active };
  }
}
/** A page: `shell` the top, `chat` and `files` its frames; `focus(w)` moves the focus the way Chromium does (the chain of active
 *  elements first, then the blur of the window that held it); `away()` is a window switch. */
class Page {
  focused = true; holder: Win;
  shell: Win; chat: Win; files: Win; fChat: Frame; fFiles: Frame;
  constructor() {
    this.shell = new Win(this, "shell"); this.shell.top = this.shell;
    this.chat = this.frame("chat"); this.files = this.frame("files");
    this.fChat = new Frame(this.chat); this.fFiles = new Frame(this.files);
    this.holder = this.shell;
  }
  frame(name: string): Win { const w = new Win(this, name); w.top = this.shell; return w; }
  frameOf(w: Win): Frame | null { return w === this.chat ? this.fChat : w === this.files ? this.fFiles : null; }
  focus(w: Win): void {
    const was = this.holder;
    this.holder = w;
    this.shell.active = this.frameOf(w);
    if (was !== w) was.fire("blur");
  }
  away(): void { this.focused = false; this.holder.fire("blur"); }
  back(): void { this.focused = true; }
  listening(): number { return [this.shell, this.chat, this.files].reduce((n, w) => n + w.n("blur") + w.n("pagehide"), 0) + this.fChat.n("load") + this.fFiles.n("load"); }
}
const W = (w: Win): Window => w as unknown as Window;

test("pageFocusWindow: the window holding the focus, walked down from the top; null with the page's focus gone or the top unreadable", () => {
  const p = new Page();
  assert.equal(pageFocusWindow(W(p.chat)), W(p.shell));
  p.focus(p.files);
  assert.equal(pageFocusWindow(W(p.chat)), W(p.files), "read from a sibling frame: the top's chain leads to the frame holding it");
  p.away();
  assert.equal(pageFocusWindow(W(p.chat)), null, "a window switch: the top reads no focus");
  const q = new Page(); q.shell.crossOrigin = true;
  assert.equal(pageFocusWindow(W(q.chat)), null, "a cross-origin top (VS Code's webview): nothing to read");
});

test("pageBlurred: a window switch with the focus in this frame runs lost at once and leaves nothing listening", () => {
  const p = new Page(); p.focus(p.chat);
  let lost = 0;
  p.away();   // the chat's blur fires here; the strip's listener calls pageBlurred from it
  const off = pageBlurred(W(p.chat), () => lost++);
  assert.equal(lost, 1);
  assert.equal(p.listening(), 0);
  off(); assert.equal(lost, 1, "the off switch after lost ran is a no-op");
});

test("pageBlurred: the focus moved to a sibling frame keeps the gesture, and the switch later blurs the sibling: lost once", () => {
  const p = new Page(); p.focus(p.chat);
  let lost = 0;
  p.focus(p.files);   // another column's question card focusing itself: the chat's window blurs with the page still focused
  pageBlurred(W(p.chat), () => lost++);
  assert.equal(lost, 0, "the focus only moved: nothing ends");
  assert.equal(p.files.n("blur"), 1, "the window the focus moved to is heard");
  p.away();
  assert.equal(lost, 1, "the switch blurs the sibling only, and that is heard");
  assert.equal(p.listening(), 0, "nothing left listening once lost ran");
  p.back(); p.focus(p.shell); p.away();
  assert.equal(lost, 1, "and nothing more after");
});

test("pageBlurred: the watch follows the focus from frame to frame, and back into this frame", () => {
  const p = new Page(); p.focus(p.chat);
  let lost = 0;
  p.focus(p.files);
  pageBlurred(W(p.chat), () => lost++);
  p.focus(p.shell);
  assert.equal(lost, 0);
  assert.deepEqual([p.files.n("blur"), p.shell.n("blur")], [0, 1], "moved to the shell, off the files pane");
  p.focus(p.chat);
  assert.deepEqual([p.shell.n("blur"), p.chat.n("blur")], [0, 1], "back into this frame: its own blur is heard");
  p.away();
  assert.equal(lost, 1);
  assert.equal(p.listening(), 0);
});

test("pageBlurred: VS Code's webview (a cross-origin top) ends the gesture at every blur, as it always did", () => {
  const p = new Page(); p.shell.crossOrigin = true;
  let lost = 0;
  pageBlurred(W(p.chat), () => lost++);
  assert.equal(lost, 1, "nothing this frame can read tells a window switch from a sibling's focus: a blur is the release");
  assert.equal(p.listening(), 0);
});

test("pageBlurred: the focus moved into a cross-origin frame cannot be followed, so the blur ends the gesture", () => {
  const p = new Page(); p.focus(p.chat);
  p.files.crossOrigin = true;   // a sandboxed URL-source pane
  let lost = 0;
  p.focus(p.files);
  pageBlurred(W(p.chat), () => lost++);
  assert.equal(lost, 1);
  assert.equal(p.listening(), 0);
});

test("a frame reloaded under the watch: its old window's listeners go with it, and its new window is heard from the frame's load", () => {
  const p = new Page(); p.focus(p.files);
  let lost = 0;
  const off = watchPageFocus(W(p.shell), () => lost++);
  const old = p.files;
  assert.deepEqual([old.n("blur"), old.n("pagehide")], [1, 1], "the files pane holds the focus at the start: it is heard, and its unload too");
  old.fire("pagehide");   // its document goes (a reload): the frame stays
  assert.deepEqual([old.n("blur"), old.n("pagehide"), p.fFiles.n("load")], [0, 0, 1], "the old window let go; the frame's load awaited");
  const fresh = p.frame("files'"); p.files = fresh; p.fFiles.contentWindow = fresh; p.holder = fresh;
  p.fFiles.fire("load");
  assert.deepEqual([fresh.n("blur"), p.fFiles.n("load")], [1, 0], "the new window heard, the load listener off");
  assert.equal(lost, 0);
  p.away();
  assert.equal(lost, 1, "the switch blurs the reloaded pane's new window, and that is heard");
  assert.equal(p.listening() + old.n("blur") + old.n("pagehide"), 0, "nothing left listening, old window or new");
  off();
});

test("a window switch during a frame's reload is read at its load: the page's focus is gone, lost runs", () => {
  const p = new Page(); p.focus(p.files);
  let lost = 0;
  watchPageFocus(W(p.shell), () => lost++);
  p.files.fire("pagehide");
  p.focused = false;   // the switch, while nothing of the new document is up to hear it
  p.fFiles.fire("load");
  assert.equal(lost, 1);
  assert.equal(p.listening(), 0);
});

test("watchPageFocus: the focus in a pane at the start is heard there; the focus moving between frames ends nothing", () => {
  const p = new Page(); p.focus(p.chat);   // the chat's composer typed into before the press, which keeps the focus where it was
  let lost = 0;
  watchPageFocus(W(p.shell), () => lost++);
  assert.deepEqual([p.shell.n("blur"), p.chat.n("blur")], [0, 1], "the chat's window is the one a switch would blur");
  p.focus(p.files); p.focus(p.shell); p.focus(p.chat);
  assert.equal(lost, 0, "a frame focusing itself mid-gesture is no switch");
  p.away();
  assert.equal(lost, 1);
  assert.equal(p.listening(), 0);
});

test("watchPageFocus: the off switch takes every listener off, and nothing runs after it", () => {
  const p = new Page(); p.focus(p.chat);
  let lost = 0;
  const off = watchPageFocus(W(p.shell), () => lost++);
  p.focus(p.files);
  p.files.fire("pagehide");   // a reload awaited: a load listener on the frame
  assert.ok(p.listening() > 0);
  off();
  assert.equal(p.listening(), 0, "the frame's load listener and this document's pagehide off too");
  p.fFiles.fire("load"); p.away();
  assert.equal(lost, 0);
});

test("this document going (a column closed mid-gesture) takes the watch off its siblings without running lost", () => {
  const p = new Page(); p.focus(p.chat);
  let lost = 0;
  p.focus(p.files);
  pageBlurred(W(p.chat), () => lost++);
  assert.equal(p.files.n("blur"), 1);
  p.chat.fire("pagehide");
  assert.equal(p.listening(), 0, "no listener of the closed page's left on a sibling");
  p.away();
  assert.equal(lost, 0);
});
