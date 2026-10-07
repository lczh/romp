// THE ACTIVE INPUT (the user 2026-10-07): the one box typing, pasting and dictation from outside any box go to, the
// topmost open surface's. The rule is executed for EVERY combination of open surfaces (a viewer modal absent / with a bar
// / without one, a comment popover with a box or not, a file tab absent / with a bar / without one, the message box
// there or not), against an independent statement of the stacking order; then the page's reading of it (render.ts
// activeInputBox, typeFromAnywhereTarget, the bare-area Enter) is pinned at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { activeInput, type OpenSurfaces, type ActiveInput } from "./active-input";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const VIEWER: OpenSurfaces["viewerModal"][] = [null, "bar", "none"];
const TAB: OpenSurfaces["fileTab"][] = [null, "bar", "none"];

function* every(): Generator<OpenSurfaces> {
  for (const viewerModal of VIEWER) for (const comment of [false, true]) for (const fileTab of TAB) for (const composer of [false, true])
    yield { viewerModal, comment, fileTab, composer };
}

/** The stacking order, stated a second way: walk the layers top down; the first one that is UP decides. */
function expected(s: OpenSurfaces): ActiveInput | null {
  const layers: Array<[boolean, ActiveInput | null]> = [
    [s.viewerModal !== null, s.viewerModal === "bar" ? "viewerModal" : null],   // a full-pane modal: its bar, or nothing
    [s.comment, "comment"],                                                      // a popover with a box that takes typing
    [s.fileTab !== null, s.fileTab === "bar" ? "fileTab" : null],                // the file tab shown: its bar, or nothing
    [s.composer, "composer"],
  ];
  for (const [up, box] of layers) if (up) return box;
  return null;
}

test("every combination of open surfaces picks the topmost surface's box", () => {
  let n = 0;
  for (const s of every()) {
    assert.equal(activeInput(s), expected(s), JSON.stringify(s));
    n++;
  }
  assert.equal(n, 3 * 2 * 3 * 2, "all 36 combinations walked");
});

test("the cases the user named: a file viewer's bar, a comment popover's reply box, else the message box", () => {
  const none: OpenSurfaces = { viewerModal: null, comment: false, fileTab: null, composer: true };
  assert.equal(activeInput(none), "composer", "nothing open: the message box, as before");
  assert.equal(activeInput({ ...none, viewerModal: "bar" }), "viewerModal", "the viewer modal open: its bar");
  assert.equal(activeInput({ ...none, fileTab: "bar" }), "fileTab", "a file tab shown: its bar");
  assert.equal(activeInput({ ...none, comment: true }), "comment", "a comment popover open: its reply box");
});

test("a viewer with no bar owns its keys: nothing beneath it takes them", () => {
  assert.equal(activeInput({ viewerModal: "none", comment: true, fileTab: "bar", composer: true }), null, "the modal covers the rest");
  assert.equal(activeInput({ viewerModal: null, comment: false, fileTab: "none", composer: true }), null, "a file tab without a bar shows no message box either");
});

test("the viewer modal sits over a comment popover, and a popover over a file tab", () => {
  assert.equal(activeInput({ viewerModal: "bar", comment: true, fileTab: null, composer: true }), "viewerModal",
    "a file opened from a thread's messages opens over the popover: its bar is on top");
  assert.equal(activeInput({ viewerModal: null, comment: true, fileTab: "bar", composer: true }), "comment", "the popover floats over the pane");
  assert.equal(activeInput({ viewerModal: null, comment: true, fileTab: "none", composer: true }), "comment");
});

test("a popover whose box takes no typing takes nothing, and the rule reads on", () => {
  // a promoted thread's view has no box; a sent comment's box is read-only until its thread opens
  assert.equal(activeInput({ viewerModal: null, comment: false, fileTab: null, composer: true }), "composer");
  assert.equal(activeInput({ viewerModal: null, comment: false, fileTab: "bar", composer: true }), "fileTab");
});

test("the page reads itself into the rule, and every default goes through it (source pins)", () => {
  const box = RENDER.split("function activeInputBox(): HTMLTextAreaElement | null {")[1].split("\n}\n")[0];
  assert.match(box, /!\(b as HTMLTextAreaElement\)\.disabled && !\(b as HTMLTextAreaElement\)\.readOnly && !b\.closest\("\[hidden\]"\)/,
    "a box takes typing when it is enabled, writable, and not inside a hidden host (the bar while the file is edited)");
  assert.match(box, /const modalBar = modal \? modal\.querySelector\("\.fv-compose-input"\) : null;/, "the modal's bar");
  assert.match(box, /const cmtBox = pop \? pop\.querySelector\("\.cmt-input"\) : null;/, "the popover's reply box");
  assert.match(box, /const tabBar = fileTabOpen \? document\.getElementById\("filetab-host"\)\?\.querySelector\("\.fv-compose-input"\) : null;/, "the shown file tab's bar");
  assert.match(box, /const at = activeInput\(\{ viewerModal: !modal \? null : takes\(modalBar\) \? "bar" : "none", comment: takes\(cmtBox\),/);
  // the gate the keystroke, the paste and the dictated text share: a box being typed in first, then the overlays, then the rule
  const gate = RENDER.split("function typeFromAnywhereTarget(e: Event): HTMLTextAreaElement | null {")[1].split("\n}\n")[0];
  const typing = gate.indexOf("if (isTypingTarget(e.target) || isTypingTarget(document.activeElement)) return null;");
  const rule = gate.indexOf("const box = activeInputBox();");
  assert.ok(typing >= 0 && rule > typing, "a box the user is typing in is never stolen from, whichever box it is");
  // the bare-area Enter drops into the same box
  const enter = RENDER.split("// Enter from the bare area drops into the ACTIVE INPUT")[1].split("\n  if ((e.key === \"ArrowLeft\"")[0];
  assert.match(enter, /if \(e\.key === "Enter" && \(!document\.activeElement \|\| document\.activeElement === document\.body\)\) \{/, "only from the bare area: a focused control keeps its Enter");
  assert.match(enter, /const box = activeInputBox\(\);/);
  assert.match(enter, /if \(box\) \{ e\.preventDefault\(\); box\.focus\(\{ preventScroll: true \}\); \}/);
  assert.match(enter, /if \(box \|\| document\.getElementById\("romp-fileview"\)\) return;/, "never the message box hidden under a viewer");
});
