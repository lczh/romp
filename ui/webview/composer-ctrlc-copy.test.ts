// Ctrl+C in the message box is a terminal-style interrupt of the active session. On a Mac copy is ⌘C, so the
// two never meet; on Windows and Linux Ctrl+C IS copy, and the keydown used to take it unconditionally (prevent
// the default, post the interrupt), so selecting part of a draft and pressing Ctrl+C never copied and cut the
// agent's running turn. Off a Mac a selection now keeps the native copy and only a collapsed caret interrupts.
// The rule (keybindings.ts composerCtrlCInterrupts) runs here for real, and so does the composer's own keydown
// branch: lifted from render.ts and executed against a stand-in event, text box and host bridge, so a branch
// that stops asking the rule, or asks it without the caret, fails here rather than only on a user's machine.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { composerCtrlCInterrupts } from "./keybindings";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const SID = "7d3e9a41-5c2b-4f80-9e16-2b8c4d0a6f53";   // invented

const key = (k: string, m: Partial<{ ctrl: boolean; meta: boolean; shift: boolean }> = { ctrl: true }) =>
  ({ key: k, ctrlKey: !!m.ctrl, metaKey: !!m.meta, shiftKey: !!m.shift });

test("the rule: off a Mac a selection keeps the copy and a collapsed caret interrupts; a Mac always interrupts", () => {
  assert.equal(composerCtrlCInterrupts(key("c"), 7, 15, false), false, "Windows/Linux, text selected: the native copy");
  assert.equal(composerCtrlCInterrupts(key("C", { ctrl: true, shift: true }), 0, 6, false), false, "Ctrl+Shift+C on a selection too");
  assert.equal(composerCtrlCInterrupts(key("c"), 4, 4, false), true, "Windows/Linux, collapsed caret: the interrupt");
  assert.equal(composerCtrlCInterrupts(key("c"), 0, 0, false), true, "an empty box interrupts");
  assert.equal(composerCtrlCInterrupts(key("c"), 7, 15, true), true, "a Mac copies with ⌘C, so ⌃C interrupts even over a selection");
  assert.equal(composerCtrlCInterrupts(key("c"), 3, 3, true), true);
  assert.equal(composerCtrlCInterrupts(key("c", { ctrl: true, meta: true }), 3, 3, true), false, "Cmd held: not ours");
  assert.equal(composerCtrlCInterrupts(key("c", { meta: true }), 3, 3, true), false, "⌘C is copy");
  assert.equal(composerCtrlCInterrupts(key("c", {}), 3, 3, false), false, "a bare c is typing");
  assert.equal(composerCtrlCInterrupts(key("v"), 3, 3, false), false, "another letter is not the interrupt");
});

// The composer's Ctrl+C branch: the if-statement under its "Ctrl+C = terminal-style interrupt" comment, brace-matched.
function ctrlCBranch(): string {
  const anchor = RENDER.indexOf("// Ctrl+C = terminal-style interrupt");
  assert.ok(anchor >= 0 && RENDER.indexOf("// Ctrl+C = terminal-style interrupt", anchor + 1) < 0, "one Ctrl+C branch");
  const start = RENDER.indexOf("\n    if (", anchor) + 1;
  assert.ok(start > anchor);
  const open = RENDER.lastIndexOf("{", RENDER.indexOf("\n", start));   // the block opens at the end of the if line
  let depth = 0, i = open;
  for (; i < RENDER.length; i++) {
    if (RENDER[i] === "{") depth++;
    else if (RENDER[i] === "}" && --depth === 0) break;
  }
  return RENDER.slice(start, i + 1);
}

const branch = new Function("e", "ta", "activeId", "vscodeApi", "lastSent", "growComposer", "flashInterrupted",
  "composerCtrlCInterrupts", "navigator", ctrlCBranch() + "\nreturn \"fell through\";");

function press(platform: string, k: string, value: string, selStart: number, selEnd: number, shift = false) {
  const posted: Array<{ type: string; id?: string }> = [];
  let prevented = false, flashed = 0;
  const e = { key: k, ctrlKey: true, metaKey: false, shiftKey: shift, altKey: false, preventDefault() { prevented = true; } };
  const ta = { value, selectionStart: selStart, selectionEnd: selEnd };
  const lastSent = new Map([[SID, "summarize the notes-api changes"]]);
  const out = branch(e, ta, SID, { postMessage: (m: { type: string; id?: string }) => posted.push(m) }, lastSent,
    () => {}, () => { flashed++; }, composerCtrlCInterrupts, { platform });
  return { out, prevented, posted, flashed, value: ta.value, lastSent };
}

test("the keydown branch, run: on Linux and Windows Ctrl+C over a selected part of the draft copies and leaves the turn alone", () => {
  for (const platform of ["Linux x86_64", "Win32"]) {
    const r = press(platform, "c", "please refactor the api module", 7, 15);
    assert.equal(r.out, "fell through", platform + ": the branch steps aside");
    assert.equal(r.prevented, false, platform + ": no preventDefault, so the browser's copy runs");
    assert.deepEqual(r.posted, [], platform + ": no interrupt posted for the active session");
    assert.equal(r.flashed, 0);
    assert.equal(r.lastSent.has(SID), true, "the just-sent prompt stays remembered for a later interrupt");
    const s = press(platform, "C", "please refactor the api module", 0, 6, true);
    assert.deepEqual([s.prevented, s.posted.length], [false, 0], platform + ": Ctrl+Shift+C over a selection too");
  }
});

test("the keydown branch, run: a collapsed caret still interrupts and puts the last prompt back in an empty box", () => {
  for (const platform of ["Linux x86_64", "Win32", "MacIntel"]) {
    const r = press(platform, "c", "", 0, 0);
    assert.equal(r.prevented, true, platform);
    assert.deepEqual(r.posted, [{ type: "interrupt", id: SID }], platform);
    assert.equal(r.value, "summarize the notes-api changes", platform + ": the restore leg");
    assert.equal(r.flashed, 1);
    const d = press(platform, "c", "a draft in progress", 5, 5);
    assert.deepEqual(d.posted, [{ type: "interrupt", id: SID }], platform + ": a caret inside a draft interrupts");
    assert.equal(d.value, "a draft in progress", "a draft is never overwritten by the restore");
  }
});

test("the keydown branch, run: on a Mac Ctrl+C over a selection interrupts as before (copy there is Cmd+C)", () => {
  const r = press("MacIntel", "c", "please refactor the api module", 7, 15);
  assert.equal(r.prevented, true);
  assert.deepEqual(r.posted, [{ type: "interrupt", id: SID }]);
});
