// The touch "Comment" pill (comment-pill.ts, the user 2026-09-30: a comment could not be started on a phone, where a
// long-press selection fires no contextmenu). The pure half is driven here: the placement (below the selection, above
// only without room, clamped, clear of the message box) and the controller's life (shown on a qualifying selection, gone
// on a collapse or a box opening; out of the way while a finger drags the selection or scrolls the page and placed once
// on the settled selection; carried with its selection through a scroll the page makes; a tap hands over the aim it was
// placed for). The render.ts wiring is pinned at the source: the tap runs the
// one comment door the menu, the key and the palette use. The served half is tests/test_comment_pill_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CommentPill, PILL_GAP, PILL_MARGIN, placeCommentPill } from "./comment-pill";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

// ── placement ─────────────────────────────────────────────────────────────────────────────────────
test("below the selection, centred on it, when there is room above the message box", () => {
  const at = placeCommentPill({ top: 200, bottom: 220, left: 100, right: 260 }, 120, 40, 390, 760);
  assert.deepEqual(at, { left: 120, top: 220 + PILL_GAP, above: false });
});

test("flipped above the selection only when the room below runs into the message box", () => {
  const at = placeCommentPill({ top: 690, bottom: 710, left: 100, right: 260 }, 120, 40, 390, 760);
  assert.equal(at.above, true);
  assert.equal(at.top, 690 - PILL_GAP - 40);
  const just = placeCommentPill({ top: 600, bottom: 620, left: 100, right: 260 }, 120, 40, 390, 760);
  assert.equal(just.above, false, "620 + gap + 40 still clears 760 less the margin: below");
});

test("clamped inside the viewport and never over the message box", () => {
  const edge = placeCommentPill({ top: 200, bottom: 220, left: 0, right: 30 }, 120, 40, 390, 760);
  assert.equal(edge.left, PILL_MARGIN, "a selection at the left edge: the pill stays on screen");
  const right = placeCommentPill({ top: 200, bottom: 220, left: 350, right: 388 }, 120, 40, 390, 760);
  assert.equal(right.left, 390 - PILL_MARGIN - 120);
  // a selection taller than the room: neither below nor above fits, and it sits just above the message box
  const tall = placeCommentPill({ top: 10, bottom: 740, left: 20, right: 370 }, 120, 40, 390, 760);
  assert.equal(tall.top, PILL_MARGIN, "above the selection runs off the top: clamped to the margin");
  const low = placeCommentPill({ top: 745, bottom: 755, left: 20, right: 370 }, 120, 40, 390, 760);
  assert.ok(low.top + 40 <= 760 - PILL_MARGIN, "never over the message box");
});

// ── the controller ───────────────────────────────────────────────────────────────────────────────
type Aim = { uuid: string };
function world(init: Partial<{ coarse: boolean; blocked: boolean; aim: Aim | null }> = {}) {
  const w = { coarse: true, blocked: false, aim: { uuid: "u-1" } as Aim | null, ...init, log: [] as string[] };
  const pill = new CommentPill<Aim>({
    coarse: () => w.coarse, blocked: () => w.blocked, aim: () => w.aim,
    show: (a) => { w.log.push("show:" + a.uuid); }, hide: () => { w.log.push("hide"); },
  });
  return { w, pill };
}

test("a qualifying selection shows it; a collapse or a non-qualifying selection hides it", () => {
  const { w, pill } = world();
  pill.selectionChanged();
  assert.deepEqual(w.log, ["show:u-1"]);
  assert.deepEqual(pill.current(), { uuid: "u-1" });
  w.aim = null;
  pill.selectionChanged();
  assert.deepEqual(w.log, ["show:u-1", "hide"]);
  assert.equal(pill.current(), null);
  pill.selectionChanged();
  assert.deepEqual(w.log, ["show:u-1", "hide"], "hidden stays hidden: no redundant hide");
});

test("a fine pointer never shows it, and neither does an open comment box", () => {
  const fine = world({ coarse: false });
  fine.pill.selectionChanged();
  assert.deepEqual(fine.w.log, [], "the desktop keeps its right-click and its key");
  const boxed = world({ blocked: true });
  boxed.pill.selectionChanged();
  assert.deepEqual(boxed.w.log, []);
});

test("while a finger is down the pill only gets out of the way; the gesture's end places it once on the settled selection", () => {
  const { w, pill } = world();
  pill.selectionChanged();                           // shown for the long-press's word
  pill.touchStart(false);                            // a handle is grabbed
  w.aim = { uuid: "u-2" };
  pill.selectionChanged();                           // dragged…
  w.aim = null;
  pill.selectionChanged();                           // …across the gap between two turns (not qualifying for a moment)…
  w.aim = { uuid: "u-3" };
  pill.selectionChanged();                           // …and on
  assert.deepEqual(w.log, ["show:u-1", "hide"], "one hide for the whole drag: no flicker, no jump");
  pill.touchEnd(1);
  assert.deepEqual(w.log, ["show:u-1", "hide"], "a second finger still down: not settled yet");
  pill.touchEnd(0);
  assert.deepEqual(w.log, ["show:u-1", "hide", "show:u-3"], "the last finger up: placed on the settled selection");
});

test("a touch that ends on a selection that no longer qualifies leaves it hidden", () => {
  const { w, pill } = world();
  pill.selectionChanged();
  pill.touchStart(false);
  w.aim = null;
  pill.touchEnd(0);
  assert.deepEqual(w.log, ["show:u-1", "hide"]);
});

test("a scroll under a finger hides it until the finger lifts; a scroll the page makes carries it with its selection", () => {
  const { w, pill } = world();
  pill.selectionChanged();
  pill.touchStart(false);
  pill.scrolled();
  pill.scrolled();
  assert.deepEqual(w.log, ["show:u-1", "hide"], "out of the way while the reader scrolls");
  pill.touchEnd(0);
  assert.deepEqual(w.log, ["show:u-1", "hide", "show:u-1"], "placed again where the selection now is");
  pill.scrolled();                                   // the clamp when the quote chip shrank the pane, momentum, a programmatic scroll
  assert.deepEqual(w.log, ["show:u-1", "hide", "show:u-1", "show:u-1"], "re-placed on the moved selection, never lost");
  w.aim = null;                                      // the selection scrolled out of the pane
  pill.scrolled();
  assert.deepEqual(w.log.slice(-1), ["hide"]);
  w.aim = { uuid: "u-1" };
  pill.scrolled();
  assert.deepEqual(w.log.slice(-1), ["hide"], "a hidden pill stays hidden through a scroll with no gesture behind it");
});

test("the first long-press: the chip it seeds shrinks the pane and the clamp scrolls, under the finger; the lift still shows the pill", () => {
  const { w, pill } = world();
  pill.touchStart(false);                            // the long-press
  pill.selectionChanged();                           // the word it selects
  pill.scrolled();                                   // the clamp as the quote chip appears
  pill.touchEnd(0);
  assert.deepEqual(w.log, ["show:u-1"]);
});

test("a press on the pill holds it through a selection change; the tap hands over the aim it was placed for and hides it", () => {
  const { w, pill } = world();
  pill.selectionChanged();
  pill.touchStart(true);
  w.aim = null;                                      // the tap collapsed the selection before its click
  pill.selectionChanged();
  pill.touchEnd(0);
  assert.deepEqual(w.log, ["show:u-1"], "still on screen for the click to land");
  assert.deepEqual(pill.tapped(), { uuid: "u-1" });
  assert.deepEqual(w.log, ["show:u-1", "hide"]);
  assert.equal(pill.tapped(), null, "nothing on screen: nothing to hand over");
  pill.selectionChanged();
  assert.deepEqual(w.log, ["show:u-1", "hide"], "after the tap a change is read again (and here it does not qualify)");
});

test("a turned phone moves a pill on screen with its selection; a hidden one stays hidden", () => {
  const { w, pill } = world();
  pill.resized();
  assert.deepEqual(w.log, []);
  pill.selectionChanged();
  pill.resized();
  assert.deepEqual(w.log, ["show:u-1", "show:u-1"]);
});

// ── the wiring (render.ts) ───────────────────────────────────────────────────────────────────────
test("render.ts: the tap runs the one comment door with the pill's aim, and every box hides the pill", () => {
  assert.match(RENDER, /b\.addEventListener\("click", \(\) => \{ const aim = commentPill\.tapped\(\); if \(aim\) commentOnSelection\(aim\); \}\);/,
    "the menu's, the key's and the palette's commentOnSelection, handed the aim the pill was placed for");
  assert.match(RENDER, /coarse: isCoarsePointer,\n\s*blocked: \(\) => !!document\.getElementById\("cmt-pop"\) \|\| !!ctxMenuEl,\n\s*aim: \(\) => \{\n\s*const a = commentAim\(\);\n\s*if \(typeof a !== "object"\) return null;/,
    "a touch screen only, and commentAim's gate: the same test the key and the menu use");
  assert.match(RENDER, /return c && \(r\.bottom < c\.top \|\| r\.top > c\.bottom\) \? null : a;/, "a selection scrolled out of the pane takes the pill with it");
  assert.match(RENDER, /const at = placeCommentPill\(sel\.getRangeAt\(sel\.rangeCount - 1\)\.getBoundingClientRect\(\), b\.offsetWidth, b\.offsetHeight, window\.innerWidth, floor\);/);
  assert.match(RENDER, /document\.addEventListener\("selectionchange", \(\) => commentPill\.selectionChanged\(\)\);/);
  assert.match(RENDER, /document\.addEventListener\("touchstart", \(e\) => commentPill\.touchStart\(!!cmtPillEl && cmtPillEl\.contains\(e\.target as Node\)\), \{ capture: true, passive: true \}\);/);
  assert.match(RENDER, /document\.addEventListener\("touchend", commentPillTouchOver, \{ capture: true, passive: true \}\);\ndocument\.addEventListener\("touchcancel", commentPillTouchOver, \{ capture: true, passive: true \}\);/);
  assert.match(RENDER, /document\.addEventListener\("scroll", \(e\) => \{ if \(!cmtPillEl \|\| !cmtPillEl\.contains\(e\.target as Node\)\) commentPill\.scrolled\(\); \}, \{ capture: true, passive: true \}\);/);
  assert.match(RENDER, /function openCommentComposer\([^)]*\): void \{\n\s*commentPill\.hide\(\);/, "the box opening hides it, whichever door opened it");
  assert.match(RENDER, /function openCommentPopover\([^)]*\): void \{\n\s*commentPill\.hide\(\);/);
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("let cmtPillEl"), RENDER.indexOf('window.addEventListener("resize", () => commentPill.resized());')), /setTimeout|requestAnimationFrame/,
    "no timer anywhere in its life");
});

test("the sheet: 40px tall, the accent's wash over the raised surface and its ink, no outline, hidden means gone", () => {
  const at = CSS.indexOf("#cmt-pill {");
  assert.ok(at > 0);
  const rule = CSS.slice(at, CSS.indexOf("}", at));
  assert.match(rule, /height: 40px;/);
  assert.match(rule, /border: none; border-radius: var\(--radius-pill\);/);
  assert.match(rule, /background: color-mix\(in srgb, var\(--accent\) 12%, var\(--surface-raised\)\); color: var\(--accent\);/);
  assert.match(rule, /box-shadow: var\(--shadow-menu\);/);
  assert.match(rule, /-webkit-user-select: none; user-select: none; -webkit-touch-callout: none;/);
  assert.match(CSS, /#cmt-pill\[hidden\] \{ display: none; \}/);
});
