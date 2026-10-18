// Remembered size + maximize for the comment popover (the user 2026-09-10, who kept enlarging the box by
// hand on every open): the size the user last dragged it to is applied on every open, in the thread AND the
// create dialog, as a fraction of the window re-clamped to the live one; a maximize control (the head's
// button, or a double-click on the title bar) snaps it to the cap and back. Nothing stored → both modes open
// exactly as before. Executed tests on the pure module + source pins for the wiring (no jsdom for the chat
// renderer); the real-browser check is tests/test_comment_pop_size_browser.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CMT_POP_SIZE_KEY, CMT_POP_MIN_W, CMT_POP_MIN_H, CMT_POP_CAP_W, CMT_POP_CAP_H, CMT_POP_EDGE, CMT_POP_THREAD_DEFAULT,
  CMT_POP_MAX_SLACK, cmtPopCapPx, clampCmtPopPx, toCmtPopFrac, parseCmtPopSize, isCmtPopMax, centerCmtPop, cmtPopCovers, cmtPopBeside, cmtPopNudge } from "../../ui/webview/comment-pop-size";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SETTINGS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "settings.ts"), "utf8");
const MODULE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "comment-pop-size.ts"), "utf8");

// ── the pure module ─────────────────────────────────────────────────────────────────────────────

test("executed: a dragged size round-trips through the stored fraction, and means the same thing on a smaller window", () => {
  const f = toCmtPopFrac(840, 480, 1200, 800);
  assert.deepEqual(f, { w: 0.7, h: 0.6 });
  assert.deepEqual(parseCmtPopSize(JSON.stringify(f)), f, "what is written reads back");
  assert.deepEqual(clampCmtPopPx(f, 1200, 800), { w: 840, h: 480 }, "the same window: the same pixels");
  assert.deepEqual(clampCmtPopPx(f, 600, 400), { w: 420, h: 240 }, "half the window: half the pixels — a fraction, not a px count");
  assert.deepEqual(clampCmtPopPx({ w: 0.5, h: 0.5 }, 1001, 801), { w: 501, h: 401 }, "whole pixels");
});

test("executed: the floor is the CSS mins and the cap the CSS caps — and the box never comes nearer than 8px to an edge", () => {
  assert.equal(CMT_POP_MIN_W, 300); assert.equal(CMT_POP_MIN_H, 120);
  assert.equal(CMT_POP_CAP_W, 0.94); assert.equal(CMT_POP_CAP_H, 0.90); assert.equal(CMT_POP_EDGE, 8);
  assert.deepEqual(clampCmtPopPx({ w: 0.01, h: 0.01 }, 1200, 800), { w: 300, h: 120 }, "a tiny stored fraction lands on the CSS mins");
  assert.deepEqual(clampCmtPopPx({ w: 1, h: 1 }, 1200, 800), { w: 1128, h: 720 }, "a full stored fraction lands on 94vw × 90vh");
  assert.deepEqual(cmtPopCapPx(1200, 800), { w: 1128, h: 720 }, "the maximize target is the same cap");
  // the px-available rule: on a short window 90vh would leave less than 8px above and below — H−16 wins
  assert.deepEqual(clampCmtPopPx({ w: 1, h: 1 }, 800, 150), { w: 752, h: 134 }, "150px tall: 0.9×150 = 135 > 150−16 = 134");
  // a window too small for both: the floor wins over the cap, exactly as CSS resolves min-width over max-width
  assert.deepEqual(clampCmtPopPx({ w: 1, h: 1 }, 200, 100), { w: 300, h: 120 });
  // the phone case the browser test drives: 480×360 keeps the 8px margins with room to spare
  const tiny = clampCmtPopPx({ w: 1, h: 1 }, 480, 360);
  assert.deepEqual(tiny, { w: 451, h: 324 });
  assert.ok(tiny.w <= 480 - 2 * CMT_POP_EDGE && tiny.h <= 360 - 2 * CMT_POP_EDGE);
});

test("executed: a live size becomes a fraction in (0, 1] — never 0, never over 1, never NaN into storage", () => {
  assert.deepEqual(toCmtPopFrac(1128, 720, 1200, 800), { w: 0.94, h: 0.9 });
  assert.deepEqual(toCmtPopFrac(5000, 5000, 1200, 800), { w: 1, h: 1 }, "wider than the window caps at 1");
  assert.deepEqual(toCmtPopFrac(0, 0, 1200, 800), { w: 0.01, h: 0.01 }, "a 0×0 box never stores a zero");
  assert.deepEqual(toCmtPopFrac(300, 120, 0, 0), { w: 1, h: 1 }, "a zero window (never in practice) stays finite");
});

test("executed: a stored entry parses to a fraction pair or null — garbage and partials never throw", () => {
  assert.equal(parseCmtPopSize(null), null, "never stored");
  assert.equal(parseCmtPopSize(""), null, "reset");
  assert.equal(parseCmtPopSize("garbage"), null);
  assert.equal(parseCmtPopSize("{"), null, "half a JSON object");
  assert.equal(parseCmtPopSize("[]"), null);
  assert.equal(parseCmtPopSize("null"), null);
  assert.equal(parseCmtPopSize("42"), null);
  assert.equal(parseCmtPopSize('{"w":0.5}'), null, "a partial pair");
  assert.equal(parseCmtPopSize('{"w":0.5,"h":"x"}'), null, "a string where a number goes");
  assert.equal(parseCmtPopSize('{"w":0,"h":0.5}'), null, "zero is not a size");
  assert.equal(parseCmtPopSize('{"w":1.5,"h":0.5}'), null, "over the window is not a fraction");
  assert.equal(parseCmtPopSize('{"w":-0.5,"h":0.5}'), null);
  assert.deepEqual(parseCmtPopSize('{"w":0.5,"h":0.25}'), { w: 0.5, h: 0.25 });
  assert.deepEqual(parseCmtPopSize('{"w":1,"h":1,"extra":true}'), { w: 1, h: 1 }, "unknown keys are ignored");
});

test("executed: maximized is the LIVE size within a few px of the cap — computed, not a stored bit", () => {
  assert.equal(CMT_POP_MAX_SLACK, 4);
  assert.equal(isCmtPopMax(1128, 720, 1200, 800), true, "exactly the cap");
  assert.equal(isCmtPopMax(1125, 717, 1200, 800), true, "a few px short still reads maximized");
  assert.equal(isCmtPopMax(1120, 720, 1200, 800), false, "8px short on one axis is a size of its own");
  assert.equal(isCmtPopMax(1128, 700, 1200, 800), false);
  assert.equal(isCmtPopMax(840, 480, 1200, 800), false, "the thread default is not maximized");
  assert.equal(isCmtPopMax(1128, 720, 2400, 1600), false, "the same pixels on a bigger window are not the cap");
});

test("executed: centering, with the 8px margin as the floor", () => {
  assert.deepEqual(centerCmtPop(1128, 720, 1200, 800), { left: 36, top: 40 });
  assert.deepEqual(centerCmtPop(840, 480, 1200, 800), { left: 180, top: 160 });
  assert.deepEqual(centerCmtPop(300, 120, 316, 130), { left: 8, top: 8 }, "a box that barely fits sits at the margin, never above it");
  assert.deepEqual(centerCmtPop(301, 121, 1000, 1000), { left: 350, top: 440 }, "whole pixels");
});

// ── the wiring in render.ts / styles.css ────────────────────────────────────────────────────────

test("the module restates the CSS mins/caps and wireEdgeResize's floor verbatim — one set of numbers", () => {
  assert.match(CSS, /\.cmt-pop \{[^}]*max-width: 94vw/s);
  assert.match(CSS, /\.cmt-pop \{[^}]*min-width: 300px; min-height: 120px; max-height: 90vh/s);
  const fn = RENDER.split("function wireEdgeResize(")[1].split("\nfunction ")[0];
  assert.ok(fn.includes("const MIN_W = 300, MIN_H = 120;"), "the edge bands' floor is the same floor");
  assert.match(MODULE, /is per-viewer ARRANGEMENT, like the tab strip's dragged cap/, "the header says why localStorage");
  assert.equal(CMT_POP_SIZE_KEY, "romp:cmtPopSize");
  assert.ok(!SETTINGS.includes("cmtPop"), "not a kernel setting: settings.ts carries nothing of it");
});

test("a stored size is applied in BOTH modes, before the thread default — which stays verbatim, so nothing stored opens as before", () => {
  const body = RENDER.split("function renderCommentPopover(")[1].split("\nfunction ")[0];
  const pref = body.indexOf("const pref = readCmtPopSize();");
  const thW = body.indexOf('if (threadLayout && !pop.style.width) { pop.style.width = Math.round(window.innerWidth * 0.7) + "px"; }');
  const thH = body.indexOf('if (threadLayout && !pop.style.height) { pop.style.height = Math.round(window.innerHeight * 0.6) + "px"; }');
  const applied = body.indexOf("cmtPopApplied = { w: pop.offsetWidth, h: pop.offsetHeight };   // whatever it measures now is ours");
  const rect = body.indexOf("const r = pop.getBoundingClientRect();");
  const sync = body.indexOf("syncCmtMaxState(pop);                              // a stored cap-sized preference opens reading");
  assert.ok(pref > 0 && thW > pref && thH > thW && applied > thH && rect > applied && sync > rect,
    "order: read the preference → apply it → the thread default fills what it left → baseline → position → paint the toggle");
  const block = body.slice(pref, thW);
  assert.match(block, /const sz = clampCmtPopPx\(pref, window\.innerWidth, window\.innerHeight\);/, "re-clamped to the LIVE window");
  assert.match(block, /pop\.style\.width = sz\.w \+ "px";\s*\n\s*pop\.style\.height = sz\.h \+ "px";/);
  assert.match(block, /pop\.classList\.add\("sized"\);/, "an expressed size: the reflow rules apply from open");
  assert.ok(!block.includes("if (th"), "the preference branch is mode-blind: create and thread alike");
  // the create dialog with nothing stored writes NO inline size (content-sized, as before)
  assert.ok(!body.includes("if (create && !pop.style.width)"), "no create-mode default size was added");
  // a denied or corrupt store costs the preference, never the popover
  assert.match(RENDER, /function readCmtPopSize\(\): CmtPopFrac \| null \{\s*\n\s*try \{ return parseCmtPopSize\(localStorage\.getItem\(CMT_POP_SIZE_KEY\)\); \} catch \{ return null; \}/);
  assert.match(RENDER, /try \{ localStorage\.setItem\(CMT_POP_SIZE_KEY, JSON\.stringify\(frac\)\); \} catch \{/);
});

test("every user resize is remembered through the ONE observer both grips fire — our own sizing, the window's caps and the close are not", () => {
  const body = RENDER.split("function renderCommentPopover(")[1].split("\nfunction ")[0];
  const ro = body.slice(body.indexOf("const ro = new ResizeObserver("), body.indexOf("ro.observe(pop);"));
  assert.match(ro, /pop\.classList\.add\("sized"\)/, "the existing .sized arming stays");
  assert.match(ro, /if \(!pop\.isConnected \|\| !pop\.offsetWidth \|\| !pop\.offsetHeight\) return;/, "a 0×0 box is a close, never a preference");
  assert.match(ro, /if \(a && Math\.abs\(pop\.offsetWidth - a\.w\) <= 1 && Math\.abs\(pop\.offsetHeight - a\.h\) <= 1\) return;/, "the size WE applied is skipped");
  assert.match(ro, /cmtPopApplied = \{ w: pop\.offsetWidth, h: pop\.offsetHeight \};\s*\n\s*saveCmtPopSize\(pop\);[\s\S]{0,400}?commentPopPos = \{ x: pop\.offsetLeft, y: pop\.offsetTop \};\s*\n\s*cmtPopPosMine = true;\s*\n\s*syncCmtMaxState\(pop\);/,
    "then every observation writes the fraction and repaints the toggle");
  assert.ok(!/setTimeout|debounce|Date\.now/.test(ro), "event-based: no timer between the pull and the write");
  // the WINDOW moving the box through the vw/vh caps is re-baselined, not written back (the tab strip's rule)
  assert.match(body, /const onWin = \(\) => \{\s*\n\s*if \(!pop\.isConnected\) \{ window\.removeEventListener\("resize", onWin\); return; \}\s*\n\s*cmtPopApplied = \{ w: pop\.offsetWidth, h: pop\.offsetHeight \};/);
  assert.match(body, /window\.addEventListener\("resize", onWin\);/);
  // the edge bands still write the style directly — a USER pull, so the observer sees it as one
  const fn = RENDER.split("function wireEdgeResize(")[1].split("\nfunction ")[0];
  assert.ok(fn.includes('pop.style.width = wpx + "px";') && !fn.includes("cmtPopApplied"), "an edge pull is never mistaken for ours");
});

test("the maximize button sits in .cmt-head right before the ×, delegated like it, and paints its own label", () => {
  assert.match(RENDER, /const maxBtn = el\("button", "cmt-max"\) as HTMLButtonElement;\s*\n\s*maxBtn\.type = "button";\s*\n\s*maxBtn\.dataset\.act = "cmtmax";/);
  assert.match(RENDER, /if \(nameBox\) head\.append\(title, nameBox, maxBtn, closeBtn\);\s*\n\s*else head\.append\(title, maxBtn, closeBtn\);/,
    "before the × in both modes");
  assert.match(RENDER, /cmtmax: \(\) => toggleCommentPopMax\(\),/, "delegated on the stable root: the popover rebuilds on status flips");
  const sync = RENDER.split("function syncCmtMaxState(")[1].split("\nfunction ")[0];
  assert.match(sync, /const max = isCmtPopMax\(pop\.offsetWidth, pop\.offsetHeight, window\.innerWidth, window\.innerHeight\);/, "computed from the live size");
  assert.match(sync, /if \(max\) pop\.dataset\.max = "1"; else delete pop\.dataset\.max;/);
  assert.match(sync, /btn\.title = max \? "Restore" : "Maximize";/);
  assert.match(sync, /btn\.setAttribute\("aria-label", max \? "Restore" : "Maximize"\);/);
  assert.match(sync, /viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '\s*\n\s*\+ 'stroke-width="1\.4" stroke-linecap="round" stroke-linejoin="round">/,
    "the house line-icon style");
  assert.match(RENDER, /const CMT_MAX_GLYPH = '<rect x="2\.5" y="2\.5" width="11" height="11" rx="1\.5"\/>';/, "one frame: maximize");
  assert.match(RENDER, /const CMT_RESTORE_GLYPH = '<path d="M5\.5 5\.5V3\.5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2"\/>'\s*\n\s*\+ '<rect x="2\.5" y="5\.5" width="8" height="8" rx="1"\/>';/,
    "two offset frames: restore");
  // CSS: click-safe (a transparent border at rest holds the box), the house accent hover, the ×'s push moves to it
  assert.match(CSS, /\.cmt-max \{[^}]*border: 1px solid transparent;[^}]*\}/s);
  assert.match(CSS, /\.cmt-max:hover \{ opacity: 1; border-color: var\(--accent\); color: var\(--accent\); background: var\(--accent-wash\); \}/);
  assert.match(CSS, /\.cmt-max \{[^}]*margin-left: auto;/s);
  assert.match(CSS, /\.cmt-max \+ \.cmt-x \{ margin-left: 0; \}/, "the × no longer pushes itself: the pair sits together at the right");
  assert.match(CSS, /\.cmt-head \.cmt-x, \.cmt-head \.cmt-max \{ cursor: pointer; \}/);
  // the whole-box drag exempts it (a `button`), so a press on it is a click, never a drag
  assert.match(RENDER, /if \(t\.closest\("\.cmt-x, \.cmt-name, \.cmt-input, \.cmt-msgs, \.cmt-quote, \.cmt-staged, \.cmt-chips, \.cmt-ask, \.cmt-bg, button, input, textarea, \.meta-btn, \.meta-menu"\)\) return;/);
});

test("a double-click on the title bar routes to the SAME toggle; the head's interactive children keep theirs; the title selects nothing", () => {
  assert.equal(RENDER.split("function toggleCommentPopMax(").length, 2, "one toggle function");
  const body = RENDER.split("function renderCommentPopover(")[1].split("\nfunction ")[0];
  const dbl = body.slice(body.indexOf('pop.addEventListener("dblclick"'), body.indexOf("toggleCommentPopMax();", body.indexOf('pop.addEventListener("dblclick"')));
  assert.ok(dbl.length > 0, "the double-click listener exists");
  assert.match(dbl, /const at = document\.elementFromPoint\(ev\.clientX, ev\.clientY\) as HTMLElement \| null;/,
    "hit-tested at the pointer: the drag's pointer capture retargets the click to the box, so the head never hears it");
  assert.match(dbl, /if \(!at \|\| !at\.closest\("\.cmt-head"\) \|\| at\.closest\("\.cmt-name, \.cmt-x, \.cmt-max, button, input"\)\) return;/,
    "only the title bar's own surface; the name box and the two buttons are exempt");
  assert.match(RENDER, /head\.title = "Drag to move · double-click to maximize or restore";/, "the head says so");
  assert.match(CSS, /\.cmt-title \{ font-weight: 600; user-select: none; \}/);
  assert.match(CSS, /\.cmt-head \{ display: flex; align-items: center; user-select: none; \}/, "the whole head already refused selection; the title says it too");
});

test("toggle: maximize = cap + centered + remembered + sized; restore = the pre-maximize size, else the mode's default (create drops the inline size AND the preference)", () => {
  const fn = RENDER.split("function toggleCommentPopMax(")[1].split("\nfunction ")[0];
  assert.match(fn, /if \(isCmtPopMax\(pop\.offsetWidth, pop\.offsetHeight, W, H\)\) \{/, "the branch reads the live size, never a stored flag");
  // maximize
  assert.match(fn, /cmtPopPreMax = pop\.style\.width \? toCmtPopFrac\(pop\.offsetWidth, pop\.offsetHeight, W, H\) : null;/, "an expressed size is what restore goes back to");
  assert.match(fn, /const cap = cmtPopCapPx\(W, H\);\s*\n\s*sizeCommentPop\(pop, cap\.w, cap\.h\);\s*\n\s*saveCmtPopSize\(pop\);\s*\n\s*pop\.classList\.add\("sized"\);/);
  // restore
  assert.match(fn, /if \(cmtPopPreMax\) \{\s*\n\s*const px = clampCmtPopPx\(cmtPopPreMax, W, H\);\s*\n\s*sizeCommentPop\(pop, px\.w, px\.h\);\s*\n\s*saveCmtPopSize\(pop\);/);
  assert.match(fn, /else if \(pop\.dataset\.mode === "thread" \|\| pop\.dataset\.status === "sent"\) \{[^\n]*\n\s*sizeCommentPop\(pop, Math\.round\(W \* CMT_POP_THREAD_DEFAULT\.w\), Math\.round\(H \* CMT_POP_THREAD_DEFAULT\.h\)\);/,
    "the thread's default is today's 70%/60%");
  assert.deepEqual(CMT_POP_THREAD_DEFAULT, { w: 0.7, h: 0.6 });
  assert.match(fn, /pop\.style\.removeProperty\("width"\);\s*\n\s*pop\.style\.removeProperty\("height"\);[\s\S]*?pop\.classList\.remove\("sized"\);[\s\S]*?localStorage\.removeItem\(CMT_POP_SIZE_KEY\)/,
    "the create dialog goes back to its content size and forgets the preference");
  // both ends: centered, the position persisted like a drag's, the label repainted
  assert.match(fn, /const c = centerCmtPop\(pop\.offsetWidth, pop\.offsetHeight, W, H\);\s*\n\s*pop\.style\.left = c\.left \+ "px";\s*\n\s*pop\.style\.top = c\.top \+ "px";\s*\n\s*commentPopPos = \{ x: c\.left, y: c\.top \};/);
  assert.match(fn, /syncCmtMaxState\(pop\);\s*\n\}\s*$/, "the label is repainted last, on both ends");
  // programmatic sizing records what it measures, so the observer never writes it back as the user's
  assert.match(RENDER, /function sizeCommentPop\(pop: HTMLElement, w: number, h: number\): void \{\s*\n\s*pop\.style\.width = w \+ "px";\s*\n\s*pop\.style\.height = h \+ "px";\s*\n\s*cmtPopApplied = \{ w: pop\.offsetWidth, h: pop\.offsetHeight \};/);
});

// ── beside its passage (the user 2026-10-15) ─────────────────────────────────────────────────────────────────────
// A landing on a thread's passage (the rail tick, the turn's rail line, the box's own quote) opens the thread's box beside
// the passage, never over it: the box stays where it is when it covers none of the passage; else it goes below the passage,
// else above it, else to the roomier side at that side's height, never under the CSS floor. Only the vertical moves.
test("beside the passage: a box clear of it stays; one over it goes below, else above, else the roomier side, shrunk to it", () => {
  const box = (top: number, h: number) => ({ left: 300, right: 1000, top, bottom: top + h });
  const at = (top: number, bottom: number) => ({ left: 80, right: 600, top, bottom });
  // the thread's default box (top 120, 420 tall in a 700px window) over a passage landed at the pane's top: clear of it, stays
  assert.equal(cmtPopCovers(box(120, 420), at(47, 63)), false);
  assert.equal(cmtPopBeside(box(120, 420), at(47, 63), 700), null, "clear of the passage: it stays where it is");
  // …a passage further down, under the box: the room below it fits the box, which goes there, its height kept
  assert.deepEqual(cmtPopBeside(box(120, 420), at(150, 200), 700), { top: 208, height: 420 });
  // a passage near the window's foot (the landing clamped at the transcript's end): above it
  assert.deepEqual(cmtPopBeside(box(120, 420), at(520, 560), 700), { top: 92, height: 420 });
  // a tall passage leaves room for neither: the roomier side, the box shrunk to it
  assert.deepEqual(cmtPopBeside(box(120, 420), at(100, 350), 700), { top: 358, height: 334 });
  assert.deepEqual(cmtPopBeside(box(120, 420), at(300, 600), 700), { top: 8, height: 284 });
  // …never under the floor: a passage filling the window leaves the box at the CSS minimum, on the roomier side, in the window
  const tall = cmtPopBeside(box(120, 420), at(40, 640), 700)!;
  assert.equal(tall.height, CMT_POP_MIN_H);
  assert.ok(tall.top >= CMT_POP_EDGE && tall.top + tall.height <= 700 - CMT_POP_EDGE, "inside the window: " + JSON.stringify(tall));
  // a passage beside the box, not under it, is not covered
  assert.equal(cmtPopCovers(box(120, 420), { left: 40, right: 290, top: 200, bottom: 220 }), false);
});

test("render.ts wiring: a passage landing opens the thread beside the passage, kept there across a rebuild until the user moves it", () => {
  const fn = (name: string) => RENDER.split("function " + name + "(")[1].split("\nfunction ")[0].split("\n}\n")[0];
  const place = fn("placeBesidePassage");
  assert.match(place, /const to = \(nudge \? cmtPopNudge : cmtPopBeside\)\(r, at, window\.innerHeight\);\s*\n\s*if \(!to\) return;/, "a box clear of the passage is left alone");
  assert.match(place, /cmtPopApplied = \{ w: pop\.offsetWidth, h: pop\.offsetHeight \};/, "the height set here is never written back as the user's size");
  assert.match(place, /cmtPopBesideAt = \{ tid, top: to\.top, height: to\.height, since: commentPopPos \};/);
  assert.match(RENDER, /const beside = th && cmtPopBesideAt && cmtPopBesideAt\.tid === th\.tid && cmtPopBesideAt\.since === commentPopPos \? cmtPopBesideAt : null;/,
    "a full rebuild of the same thread keeps the spot, until a drag or a resize replaces commentPopPos");
  assert.match(RENDER, /const py = Math\.max\(8, Math\.min\(beside \? beside\.top : \(pos\?\.y \?\? 120\), window\.innerHeight - r\.height - 8\)\);/);
  assert.match(RENDER, /cmtPopLost = null; cmtPopBesideAt = null;/, "closing the box ends both");
});

test("cmtPopNudge: a passage that moved into the box moves it the least that clears it, and one clear of it moves nothing", () => {
  const box = (top: number, bottom: number) => ({ left: 292, right: 992, top, bottom });
  const at = (top: number, bottom: number) => ({ left: 80, right: 600, top, bottom });
  // the box sat 8 px above the passage; the passage came up 5 px: still clear, nothing moves (the gap is air, not a rule)
  assert.equal(cmtPopNudge(box(65, 485), at(488, 504), 700), null);
  // it came up 26 px (the late fork control under the last reply): the box goes up 26 px, its size kept
  assert.deepEqual(cmtPopNudge(box(65, 485), at(467, 483), 700), { top: 39, height: 420 });
  // a passage that came up under the box's head: below it is the nearer clear spot
  assert.deepEqual(cmtPopNudge(box(300, 500), at(290, 306), 900), { top: 314, height: 200 });
  // no room at the box's size either side: the landing's rule (the roomier side, at that side's height)
  assert.deepEqual(cmtPopNudge(box(30, 450), at(400, 416), 700), cmtPopBeside(box(30, 450), at(400, 416), 700));
  assert.equal(cmtPopNudge(box(30, 450), at(400, 416), 700)!.top >= CMT_POP_EDGE, true);
});
