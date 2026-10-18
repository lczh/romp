// The tab ✕ (End-session) click must not be dropped mid-press (the user 2026-06-30). renderTabs() does
// `#tabs`.replaceChildren() on EVERY kernel push, so a push that lands between mousedown and mouseup on a
// tab's ✕ destroys the pressed node — the native `click` then never fires (or retargets to the data-act-less
// #tabs), the `close` delegate never runs, and the "End session?" dialog never opens. Intermittent: frequent
// while the fleet is busy (many pushes), rare when idle. Delegation to the stable #tabs (which normally
// survives a rebuild) can't save a click whose pressed node is gone. Fix: HOLD renderTabs while a pointer is
// pressed on the strip and flush AFTER release — the timeline's proven _pointerHeld pattern. Source-pin (no
// jsdom for the SVG/tab-bar draw path, like the sibling render-*/tab-*.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("renderTabs defers its rebuild while a pointer is pressed on the tab strip", () => {
  assert.match(RENDER, /let tabPointerHeld = false;/);
  assert.match(RENDER, /let renderPendingWhilePressed = false;/);
  // the guard sits at the TOP of renderTabs, alongside the rename guard, so no push rebuilds mid-press (the
  // @-mention roster hook ahead of both touches no strip DOM: composer-mention-pane.test.ts)
  assert.match(RENDER, /function renderTabs\(\) \{\s*\n\s*mentionRosterChanged\(\);[^\n]*\n\s*if \(renameActive\)[\s\S]*?\n\s*if \(tabPointerHeld\) \{ renderPendingWhilePressed = true; return; \}/);
});

test("the press guard is armed on #tabs pointerdown and released on pointerup/cancel/blur", () => {
  assert.match(RENDER, /tabs\.addEventListener\("pointerdown", \(\) => \{ tabPointerHeld = true; \}\)/);
  assert.match(RENDER, /window\.addEventListener\("pointerup", releaseTabStrip\)/);
  // pointercancel releases only when no drag of ours is in flight: the browser fires it the moment a drag starts, and the
  // drag IS the press — its dragend handlers release the hold (2026-09-11: a kernel push mid-drag rebuilt #tabs under the
  // gesture, detaching the dragged node, because this release had already let it)
  assert.match(RENDER, /window\.addEventListener\("pointercancel", \(\) => \{ if \(draggedId \|\| draggedGroup\) return; releaseTabStrip\(\); \}\);/);
  assert.doesNotMatch(RENDER, /window\.addEventListener\("pointercancel", releaseTabStrip\)/, "never the bare release: it would end the hold at dragstart");
  // the window's blur releases only when the PAGE lost the focus (a window switch: no pointerup ever comes); the focus moving to
  // another frame of the page (another column's question card focusing itself on a push) keeps the hold, and the window it moved
  // to is watched (page-focus.ts pageBlurred, executed in page-focus.test.ts; tests/test_chat_split_served.py drives it served)
  assert.doesNotMatch(RENDER, /window\.addEventListener\("blur", releaseTabStrip\)/, "never the bare release: a sibling frame's focus would drop the hold mid-press");
  assert.match(RENDER, /window\.addEventListener\("blur", \(\) => \{\n\s*if \(!tabPointerHeld \|\| tabHoldFocusOff\) return;[^\n]*\n\s*const off = pageBlurred\(window, releaseTabStrip\);\n\s*if \(tabPointerHeld\) tabHoldFocusOff = off;/);
  const rel = RENDER.slice(RENDER.indexOf("function releaseTabStrip(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function releaseTabStrip(): void {")));
  assert.match(rel, /^function releaseTabStrip\(\): void \{\n\s*if \(tabHoldFocusOff\) \{ tabHoldFocusOff\(\); tabHoldFocusOff = null; \}[^\n]*\n\s*if \(!tabPointerHeld\) return;/, "every release takes the watch off first, held or not");
});

test("release flushes a pending rebuild a tick LATER so the click fires against the live node first", () => {
  // setTimeout(0): the click dispatches right after pointerup, BEFORE the timer — so the pressed ✕ still
  // exists when the click lands; only then do we rebuild.
  assert.match(RENDER, /if \(renderPendingWhilePressed\) \{ renderPendingWhilePressed = false; setTimeout\(\(\) => renderTabs\(\), 0\); \}/);
});
