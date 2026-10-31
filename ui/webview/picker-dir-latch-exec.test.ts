// The + picker's folder completer keeps one question in flight and holds what is typed behind its answer
// (askDirComplete / onDirCompletions in render.ts). That hold once outlived every answer that never came: one ask
// to a linked host whose tunnel was down (federation holds a dirComplete for the tunnel's reopen and answers
// nothing) left every later ask queued, so after switching back to this machine the field showed no folders and
// no verdict, its hint named the other host, and closing and reopening the picker did not help (2026-10-31).
// file-browse.ts had fixed the same latch in its own browser. EXECUTED: the picker's latch, its two handlers,
// the wire-return re-ask and the real closePicker and pickerVisible, lifted from render.ts in
// tab-cycle-standdown-exec.test.ts's idiom (an anchored slice, esbuild at run time, new Function) over a fake
// page that answers the few lookups they make. Opening the picker and clicking a host button are the statements
// openPicker and the button's click run (pinned at source in dir-complete.test.ts). The VS Code pane's pipe
// return is pinned at source in the last test. Synthetic: the notes-api demo, a linked host named TESTHOST.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function slice(startAnchor: string, endAnchor: string, name: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, name + ": anchors not found (" + startAnchor.slice(0, 40) + " / " + endAnchor.slice(0, 40) + "); re-anchor");
  assert.equal(RENDER.indexOf(startAnchor, a + 1), -1, name + ": the start anchor is not unique; re-anchor");
  return RENDER.slice(a, b);
}
/** A top-level render.ts function, through its closing brace. */
const fn = (start: string, name: string): string => slice(start, "\n}\n", name) + "\n}\n";
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;

// the completer's module state, then the picker's host lookup through to the menu helpers (the latch, its two
// handlers, and whatever the completer wires to the page's events), then the close and the shown test
const CODE = ts([
  slice("interface DirItem", "\n// The last directory a session was actually started in", "completer state"),
  slice("function pickerHost(): string {", "function dirMenuOpen(): boolean {", "completer"),
  fn("function closePicker() {", "closePicker"),
  fn("function pickerVisible(): boolean {", "pickerVisible"),
].join("\n"));

interface Post { type: string; value?: string; reqId?: number; host?: string }
interface Api {
  askDirComplete(v: string): void;
  onDirCompletions(m: unknown): void;
  closePicker(): void;
  setPickMode(on: boolean): void;
  verdict(): { items: unknown[]; status: unknown };
}

const TESTHOST = "TESTHOST";
const FOLDERS = [{ name: "web", path: "~/notes-api/web" }, { name: "api", path: "~/notes-api/api" }];

function page() {
  const posts: Post[] = [];
  let selected = "";                                   // the host button wearing .sel ("" = this machine)
  const picker = { style: { display: "none" } };
  const field = { value: "" };
  const listeners: Record<string, Array<() => void>> = {};
  const doc = {
    getElementById: (id: string) => (id === "picker" ? picker : id === "picker-dir" ? field : null),
    querySelector: (sel: string) => {
      if (sel === "#picker .picker-host .picker-be-opt.sel") return { dataset: { host: selected } };
      throw new Error("the fake page answers the picker's host lookup only, not " + JSON.stringify(sel) + ": extend it");
    },
  };
  const win = { addEventListener: (t: string, f: () => void) => { (listeners[t] ??= []).push(f); } };
  const vscodeApi = { postMessage: (m: Post) => { posts.push(m); } };
  const noop = () => {};
  const api = (new Function("vscodeApi", "document", "window", "renderDirMenu", "signalPickerOverlay", "syncComposerPh",
    "let pickMode = false;\n" + CODE +
    "\nreturn { askDirComplete, onDirCompletions, closePicker, setPickMode: (on) => { pickMode = on; }," +
    " verdict: () => ({ items: dirItems, status: dirStatus }) };") as (...a: unknown[]) => Api)(vscodeApi, doc, win, noop, noop, noop);
  const asks = (): Post[] => posts.filter((p) => p.type === "dirComplete");
  return {
    api, asks, field, picker,
    /** openPicker: shown, the host row back on this machine, the local prefill, and its question unless choosing */
    open(pick = false): void {
      picker.style.display = ""; api.setPickMode(pick); selected = ""; field.value = "~/notes-api";
      if (!pick) api.askDirComplete(field.value);
    },
    /** a host button's click: selected, the field takes that host's prefill and asks about it */
    clickHost(h: string, prefill: string): void { selected = h; field.value = prefill; api.askDirComplete(field.value); },
    type(v: string): void { field.value = v; api.askDirComplete(v); },
    answer(p: Post, items = FOLDERS): void {
      api.onDirCompletions({ type: "dirCompletions", reqId: p.reqId, host: p.host, value: p.value, items,
        status: { value: p.value, path: p.value, exists: true, isDir: true, isFile: false, canCreate: false,
                  nearest: p.value, missing: 0, isDefault: false } });
    },
    fire(ev: string): void { for (const f of listeners[ev] ?? []) f(); },
  };
}

test("a host switch asks the newly selected host at once, while the other host's answer is still out", () => {
  const p = page();
  p.open();
  p.answer(p.asks()[0]);                               // this machine answered the open's question
  p.clickHost(TESTHOST, "");                           // TESTHOST's tunnel is down: the ask is held, nothing answers
  assert.equal(p.asks().length, 2);
  assert.equal(p.asks()[1].host, TESTHOST);
  p.clickHost("", "~/notes-api");                      // back to this machine
  assert.equal(p.asks().length, 3, "this machine's question went out, not queued behind TESTHOST's answer");
  assert.deepEqual([p.asks()[2].host, p.asks()[2].value], ["", "~/notes-api"]);
  p.answer(p.asks()[2]);
  assert.deepEqual(p.api.verdict().items, FOLDERS, "and its answer shows: folders and a verdict for the local path");
  p.type("~/notes-api/w");
  assert.equal(p.asks().length, 4, "the field keeps completing on this machine");
});

test("a late answer from the host left behind neither shows nor lets go of the newer question", () => {
  const p = page();
  p.clickHost(TESTHOST, "");                           // TESTHOST's ask, held
  p.clickHost("", "~/notes-api");                      // this machine's, in flight
  const [held, local] = p.asks();
  assert.ok(held && local, "both questions went out");
  p.answer(held, [{ name: "srv", path: "/srv" }]);     // TESTHOST's tunnel came back and its answer finally landed
  assert.deepEqual(p.api.verdict(), { items: [], status: null }, "another machine's folders never show under this one");
  p.type("~/notes-api/web");
  assert.equal(p.asks().length, 2, "the keystroke still waits on this machine's answer, which the late one did not stand for");
  p.answer(local);
  assert.equal(p.asks().length, 3);
  assert.deepEqual([p.asks()[2].host, p.asks()[2].value], ["", "~/notes-api/web"], "…and then only the newest value goes out");
});

test("what was typed for the host left behind is never asked of the newly selected one", () => {
  const p = page();
  p.clickHost(TESTHOST, "");                           // held, nothing answers
  p.type("/srv/notes-api");                            // typed for TESTHOST, waiting on its answer
  assert.equal(p.asks().length, 1);
  p.clickHost("", "~/notes-api");
  assert.equal(p.asks().length, 2);
  p.answer(p.asks()[1]);
  assert.equal(p.asks().length, 2, "nothing more went out: TESTHOST's path is not this machine's question");
  assert.deepEqual(p.api.verdict().items, FOLDERS);
});

test("on one host the pacing is unchanged: what is typed waits for the answer, and only the newest goes out", () => {
  const p = page();
  p.open();
  p.type("~/notes-api/a");
  p.type("~/notes-api/ap");
  assert.equal(p.asks().length, 1, "one question in flight");
  p.answer(p.asks()[0]);
  assert.equal(p.asks().length, 2);
  assert.equal(p.asks()[1].value, "~/notes-api/ap");
});

test("closing the picker lets go of an unanswered question, so the next open asks again on the same host", () => {
  const p = page();
  p.open();                                            // asked; the answer never comes
  p.api.closePicker();
  assert.equal(p.picker.style.display, "none");
  p.open();
  assert.equal(p.asks().length, 2, "the reopened picker asks about its path instead of waiting on the closed one's answer");
  p.answer(p.asks()[0]);                               // the first open's answer, late: stale
  assert.deepEqual(p.api.verdict(), { items: [], status: null });
  p.answer(p.asks()[1]);
  assert.deepEqual(p.api.verdict().items, FOLDERS);
});

test("the socket's return asks the field again while the picker is up (its drop lost the answer)", () => {
  const p = page();
  p.open();                                            // asked; the socket drops before the answer
  p.type("~/notes-api/web");                           // held behind the lost answer
  assert.equal(p.asks().length, 1);
  p.fire("romp:wsup");
  assert.equal(p.asks().length, 2, "the return asks again");
  assert.deepEqual([p.asks()[1].host, p.asks()[1].value], ["", "~/notes-api/web"], "about what the field says now");
  p.answer(p.asks()[1]);
  assert.deepEqual(p.api.verdict().items, FOLDERS);
});

test("…and asks nothing while the picker is closed or choosing an existing session", () => {
  const p = page();
  p.open(true);                                        // pick mode: no folder field in play
  p.fire("romp:wsup");
  assert.equal(p.asks().length, 0);
  p.api.setPickMode(false);
  p.open();
  p.api.closePicker();
  p.fire("romp:wsup");
  assert.equal(p.asks().length, 1, "only the open's own question; a closed picker is not re-asked");
});

test("the VS Code pane's pipe return runs the same re-ask (it never sees romp:wsup)", () => {
  const line = 'if (m.type === "pipeState" && m.up) dirWireUp();';
  const at = RENDER.indexOf(line);
  assert.ok(at > 0, "the pipeState up edge re-asks the picker's folder question");
  assert.ok(RENDER.indexOf('if (m.type === "pipeState") { if (!m.up)', at) > at, "…ahead of the pipeState arm's own return");
});
