// The history regions' pure rules (T386 stage 2), each executed: runs and gaps from spans, a window inserted among them, a gap's
// estimated height, the page a gap asks for at either edge, the anchor's place inside a gap, the one notice's words, the cancel.
import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as ts from "typescript";
import { DEFAULT_TURN_PX, GAPS_MAX_PX, GAPS_SHARE, HIDDEN_SHARE, LAYOUT_LIMIT_FALLBACK, OVERLAY_KINDS, gapAt, gapFraction, gapHeight, gapTurnPx, heldUnitPx, insertRun, landingCancel, landingNotice, pageAt, pagesToAsk, regionsFromRuns, runsOf, splitHeldAgainstFrame, turnsBeforeTail, type Ev, type Gap, type Region, type Run } from "./chat-regions";

const ev = (k: string) => ({ uuid: k });
const run = (lo: number, hi: number | null, keys: string[]): Run => ({ kind: "run", lo, hi, events: keys.map(ev) });
const shape = (rs: readonly Region[]) => rs.map((r) => r.kind === "gap" ? "gap[" + r.lo + "," + r.hi + ")" : "run[" + r.lo + "," + (r.hi ?? "tail") + "):" + r.events.map((e) => e.uuid).join(","));

test("the regions a tail run implies: a head gap before it, the tail open-ended; a tail from the head has no gap at all", () => {
  assert.deepEqual(shape(regionsFromRuns([run(200, null, ["t1", "t2"])])), ["gap[0,200)", "run[200,tail):t1,t2"]);
  assert.deepEqual(shape(regionsFromRuns([run(0, null, ["a"])])), ["run[0,tail):a"]);
});

test("a window inside the head gap splits it in two; a second window between them shrinks both; the order is by turn whatever the arrival order", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(96, 128, ["w1", "w2"]));
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,128):w1,w2", "gap[128,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(160, 176, ["x1"]));
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,128):w1,w2", "gap[128,160)", "run[160,176):x1", "gap[176,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(32, 48, ["v1"]));   // an earlier arrival lands earlier in the list
  assert.deepEqual(shape(rs).slice(0, 3), ["gap[0,32)", "run[32,48):v1", "gap[48,96)"]);
});

test("a window touching a held run merges into it (events in turn order), and one touching the tail run joins the tail", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(96, 128, ["w1"]));
  rs = insertRun(rs, run(128, 144, ["y1"]));   // touches the window's end: one run
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,144):w1,y1", "gap[144,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(80, 96, ["u1"]));     // touches the window's start: still one run, in order
  assert.deepEqual(shape(rs), ["gap[0,80)", "run[80,144):u1,w1,y1", "gap[144,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(176, 200, ["z1"]));   // touches the tail: the tail grows downward and stays open-ended
  assert.deepEqual(shape(rs), ["gap[0,80)", "run[80,144):u1,w1,y1", "gap[144,176)", "run[176,tail):z1,t1"]);
  assert.equal(turnsBeforeTail(rs), 176);
});

test("an overlapping window merges by the T323 order rule: the held part before, the window, the held part after, every key once", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(96, 128, ["a", "b", "c"]));
  rs = insertRun(rs, run(112, 144, ["b", "c", "d"]));   // overlaps b and c
  assert.deepEqual(shape(rs), ["gap[0,96)", "run[96,144):a,b,c,d", "gap[144,200)", "run[200,tail):t1"]);
  rs = insertRun(rs, run(80, 112, ["z", "a"]));         // overlaps a from above
  assert.deepEqual(shape(rs), ["gap[0,80)", "run[80,144):z,a,b,c,d", "gap[144,200)", "run[200,tail):t1"]);
  assert.equal(runsOf(rs).length, 2);
});

test("a window bridging two held runs joins all three into one", () => {
  let rs: Region[] = regionsFromRuns([run(200, null, ["t1"])]);
  rs = insertRun(rs, run(32, 48, ["a"]));
  rs = insertRun(rs, run(64, 80, ["c"]));
  rs = insertRun(rs, run(48, 64, ["b"]));
  assert.deepEqual(shape(rs), ["gap[0,32)", "run[32,80):a,b,c", "gap[80,200)", "run[200,tail):t1"]);
});

test("a gap's height is its turns times the measured row, the default row until measured, never under a row", () => {
  assert.equal(gapHeight({ lo: 0, hi: 96 }, null), 96 * DEFAULT_TURN_PX);
  assert.equal(gapHeight({ lo: 128, hi: 200 }, 42.5), Math.round(72 * 42.5));
  assert.equal(gapHeight({ lo: 10, hi: 10 }, 42.5), 43, "an empty span still shows a row's worth of space");
});

test("the gaps together stand under the browser's layout limit: a long session's px-per-turn is lowered, a short one's is the measure (2026-10-01)", () => {
  const LIMIT_2X = 16_777_216;   // the tallest box Chrome lays out on a 2x display, the smallest limit a browser was seen to impose
  assert.ok(GAPS_MAX_PX <= LIMIT_2X / 4, "the gaps' bound leaves most of the smallest limit to the rows the page holds");
  // the reported shape: a head gap of 1,441 turns above the tail, measured at 14,800 px a turn, drew 21 million px, past the limit
  const long: Region[] = regionsFromRuns([run(1441, null, ["t1"])]);
  const per = gapTurnPx(long, 14_800);
  assert.ok(per < 14_800, "the long session's gap is drawn at a lower figure than the measure");
  assert.ok(gapHeight(long[0] as Gap, per) <= GAPS_MAX_PX, "…so the head gap stands at most the bound: " + gapHeight(long[0] as Gap, per));
  assert.ok(gapHeight(long[0] as Gap, 14_800) > LIMIT_2X, "(at the measured figure it would pass the limit)");
  // a short session keeps its measured figure, so its gaps are drawn true
  assert.equal(gapTurnPx(regionsFromRuns([run(200, null, ["t1"])]), 42.5), 42.5);
  assert.equal(gapTurnPx(regionsFromRuns([run(200, null, ["t1"])]), null), DEFAULT_TURN_PX, "unmeasured: the default figure");
  assert.equal(gapTurnPx(regionsFromRuns([run(0, null, ["t1"])]), 14_800), 14_800, "no gap at all: the measure");
  // several gaps (a landing split the head gap) share ONE figure, the bound over their turns together
  let split: Region[] = regionsFromRuns([run(3000, null, ["t1"])]);
  split = insertRun(split, run(1200, 1232, ["w"]));
  const gaps = split.filter((r): r is Gap => r.kind === "gap");
  assert.equal(gaps.length, 2);
  const per2 = gapTurnPx(split, 9_000);
  const total = gaps.reduce((n, g) => n + gapHeight(g, per2), 0);
  assert.ok(total <= GAPS_MAX_PX + gaps.length, "the gaps together stand at most the bound (rounding aside): " + total);
  // display units count their gap units only (render.ts's items: events, tool groups and gaps)
  const items = [{ kind: "gap", lo: 0, hi: 1441, before: 0 }, { kind: "event", index: 0 }, { kind: "toolgroup", indices: [1, 2] }];
  assert.equal(gapTurnPx(items, 14_800), gapTurnPx(long, 14_800), "the display units' gaps give the regions' figure");
  // the bound is a SHARE of the layout limit the page measures (a fixed figure fits one scale and zoom only): at 2x with the page zoomed
  // to 300 percent Chrome's limit is 2^25 / 6, and four million px of gaps plus the tail run passed it
  assert.equal(GAPS_MAX_PX, LAYOUT_LIMIT_FALLBACK * GAPS_SHARE, "the default bound is the fallback limit's share");
  const limit6x = Math.floor(33_554_432 / 6);
  const per6 = gapTurnPx(long, 14_800, limit6x * GAPS_SHARE);
  assert.ok(gapHeight(long[0] as Gap, per6) <= limit6x * GAPS_SHARE + 1, "a smaller measured limit draws the gaps smaller: " + gapHeight(long[0] as Gap, per6));
});

test("a held run's hidden units share one budget with the gaps, a short session keeps the measure (2026-10-02)", () => {
  // the budget at Chrome's limit on a 2x display (LAYOUT_LIMIT_FALLBACK, the smallest the reports have shown): half of it, written out
  // here, so the share's value is pinned by the assertion below rather than read back from the constant it checks
  const max = LAYOUT_LIMIT_FALLBACK / 2;
  assert.equal(HIDDEN_SHARE, 0.5, "the estimates' share of the layout limit is half");
  const units = (n: number, kind = "event") => Array.from({ length: n }, () => ({ kind }));
  // a short session: its units at the measured average stand far under the budget beside a small gap, so they take the measure
  assert.equal(heldUnitPx([{ kind: "gap" }, ...units(250)], 60, 5_000, max), 60);
  // a reader who climbed back through a long session from a tall tail: the gaps at their bound and 1,001 held units at an average of
  // 21,600 px would stand 26 million px together, past the limit; the units' figure is lowered until gaps and units fill the budget
  const climbed = [{ kind: "gap" }, ...units(1000), { kind: "toolgroup" }, { kind: "gap" }];
  const per = heldUnitPx(climbed, 21_600, GAPS_MAX_PX, max);
  assert.ok(GAPS_MAX_PX + 1001 * 21_600 > LAYOUT_LIMIT_FALLBACK, "(at the measure they would pass the limit)");
  assert.equal(per, (max - GAPS_MAX_PX) / 1001, "the budget's rest after the gaps, over every unit that is not a gap, rendered or hidden");
  assert.ok(GAPS_MAX_PX + 1001 * per <= max + 1e-6, "…so the gaps and the held units stand at most the budget together");
  // at the edge: 100 units beside the gaps at their bound, at an average half a percent past, then half a percent under, the rest's share
  const room = max - GAPS_MAX_PX, hundred = [{ kind: "gap" }, ...units(100)];
  assert.equal(heldUnitPx(hundred, (room / 100) * 1.005, GAPS_MAX_PX, max), room / 100, "half a percent past the rest's share: lowered to it");
  assert.equal(heldUnitPx(hundred, (room / 100) * 0.995, GAPS_MAX_PX, max), (room / 100) * 0.995, "half a percent under the rest's share: the measure");
  assert.equal(heldUnitPx([{ kind: "gap" }], 21_600, GAPS_MAX_PX, max), 21_600, "no held unit: the measure");
  assert.equal(heldUnitPx(climbed, 21_600, max + 10, max), 0, "gaps past the budget leave the held units no height, never a negative one");
});

test("the spacers and every walk over them take the bounded figure for a hidden held unit (2026-10-02)", () => {
  // Source pins: they guard the text, read with the compiler's own parser (as writer-census.ts reads render.ts) so a comment is never
  // taken for code, and a reverted line left behind as a comment cannot satisfy them. The served climb
  // (tests/test_long_history_height_browser.py LongHistoryClimb) executes sizeSpacers, the figure's setting (renderWindowItems and
  // heldPxOf: its estimates check, at most 4 px past half the limit, also fails when heldPxOf drops the gaps' sum, and its check that
  // the figure stays put while the window moves fails when heldPxOf counts the hidden units alone), the go-to-bottom chip's walk into
  // the bottom spacer (unitAtScroll, hiddenUnitTop) and the keyed paint's re-size of the bottom spacer for replies that land below the
  // window (sizeBottomSpacer: its append test, test_rows_appended_below_the_window_take_the_held_figure_until_the_next_build). The 12x
  // lab (UnreachableEndSettles) executes heldPxOf's measured limit: its estimates check fails when the fallback limit stands in for
  // it. The 2x landing (LongHistoryHeight) executes heldPxOf's average unit: its check of the figure a hidden row takes fails when
  // heldPxOf reads the px per turn in its place. The two-session lab (LongHistoryBuiltHidden) executes the first-measure recompute in
  // sizeSpacers: a view built while hidden, then shown (the first measure of both figures), and a settings change while it is hidden,
  // then the switch back (the average unit's alone). Over a taller tail (LongHistoryBuiltHiddenBounded) the bound engages at the
  // show, and its check that the show takes the figure the next window build sets fails when the recompute drops the bound or runs
  // before the gaps are re-mapped. turnUnderTop, yOfTurn, preJumpIntoGap, the ruler's band and the recompute's px-per-turn term are
  // guarded by these pins only.
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  const sf = ts.createSourceFile("render.ts", RENDER, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  // CODE: the source with every comment blanked to spaces, its line breaks kept, so the text pins below match code only
  const ranges = new Map<number, number>();
  const collect = (n: ts.Node): void => {
    for (const r of [...(ts.getLeadingCommentRanges(RENDER, n.getFullStart()) ?? []), ...(ts.getTrailingCommentRanges(RENDER, n.getEnd()) ?? [])]) ranges.set(r.pos, r.end);
    for (const c of n.getChildren(sf)) if (c.kind < ts.SyntaxKind.FirstJSDocNode || c.kind > ts.SyntaxKind.LastJSDocNode) collect(c);   // a doc comment is blanked whole, as its owner's leading comment
  };
  collect(sf);
  let CODE = "", at = 0;
  for (const [pos, end] of [...ranges].sort((a, b) => a[0] - b[0])) { if (pos < at) continue; CODE += RENDER.slice(at, pos) + RENDER.slice(pos, end).replace(/[^\n]/g, " "); at = end; }
  CODE += RENDER.slice(at);
  assert.equal(CODE.length, RENDER.length);
  assert.ok(ranges.size > 1000 && RENDER.split("/**").length > 100 && !CODE.includes("/**"), "the comments are found and blanked, every doc comment with them");
  const fn = (head: string) => { const i = CODE.indexOf(head); assert.ok(i >= 0, head); return CODE.slice(i, CODE.indexOf("\n}\n", i)); };
  assert.match(CODE, /v\.gapUnits = gapUnitsOf\(items, v\.pxPerTurn\);\s*\n\s*v\.heldPx = heldPxOf\(v, items\);/, "the figure is set with the spacer map (renderWindowItems)");
  assert.match(fn("function sizeSpacers("), /const hadPerTurn = v\.pxPerTurn != null, hadAvg = v\.avgTurnH != null;\s*\n\s*measureRows\(v\);/, "a first measure is read off the figures as they stood before measureRows (executed: LongHistoryBuiltHidden)");
  assert.match(fn("function sizeSpacers("), /if \(\(\(!hadPerTurn && v\.pxPerTurn != null\) \|\| \(!hadAvg && v\.avgTurnH != null\)\) && v\.painted\) v\.heldPx = heldPxOf\(v, v\.painted\.items\);/, "…and the figure is set again on a first measure (executed: LongHistoryBuiltHidden, and LongHistoryBuiltHiddenBounded for the bound; its px-per-turn term by this pin only)");
  assert.match(fn("function heldPxOf("), /if \(v\.gapUnits\) for \(const h of v\.gapUnits\.values\(\)\) gaps \+= h;\s*\n\s*return heldUnitPx\(items, v\.avgTurnH \?\? 60, gaps, layoutLimitPx\(\) \* HIDDEN_SHARE\);/, "off the gaps as the spacer map draws them, under a share of the measured limit (executed: LongHistoryClimb, LongHistoryHeight for the average unit, and UnreachableEndSettles for the measured limit)");
  assert.match(CODE, /function unitPx\(v: View\): number \{ return v\.heldPx \?\? v\.avgTurnH \?\? 60; \}/, "unitPx: the held figure, else the measured average, else 60");
  for (const head of ["function sizeSpacers(", "function hiddenUnitTop(", "function unitAtScroll(", "function yOfTurn("]) assert.match(fn(head), /const avg = unitPx\(v\);/, head);
  assert.match(fn("function turnUnderTop("), /\bavg = unitPx\(v\);/, "turnUnderTop: avg = unitPx(v)");
  assert.match(fn("function sizeBottomSpacer("), /hiddenHeight\(v, total - \(v\.spacerCountBot \?\? 0\), total, unitPx\(v\)\)/, "the bottom spacer re-sized by the keyed paint (executed: LongHistoryClimb's append test)");
  assert.match(fn("function preJumpIntoGap("), /const avg = unitPx\(v\), own = hiddenHeight\(v, idx, idx \+ 1, avg\);/, "the landing's place for a gap inside a spacer");
  assert.match(fn("function paintGlowRuler("), /botH: botSp \? botSp\.offsetHeight : 0,\s*\n\s*avg: unitPx\(v\) \}\)\);/, "the ruler's band for a hovered unit inside a spacer");
  // The census: every READ of the raw average (any `.avgTurnH` that is not an assignment's target), by the function it is read in. A
  // read anywhere else is a spacer estimate or a walk that must take unitPx(v), or a new reader to name here.
  const owner = (n: ts.Node): string => {
    let hook: string | null = null;
    for (let p: ts.Node | undefined = n.parent; p; p = p.parent) {
      if ((ts.isFunctionDeclaration(p) || ts.isMethodDeclaration(p)) && p.name) return p.name.getText(sf);
      if (!hook && (ts.isArrowFunction(p) || ts.isFunctionExpression(p)) && ts.isBinaryExpression(p.parent) && ts.isPropertyAccessExpression(p.parent.left)) hook = p.parent.left.name.text;
    }
    return hook ?? "(module)";
  };
  const reads: Record<string, number> = {}, routes: string[] = [];
  const where = (n: ts.Node) => (sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1) + ": " + n.parent.getText(sf).slice(0, 80);
  const visit = (n: ts.Node): void => {
    if (ts.isPropertyAccessExpression(n) && n.name.text === "avgTurnH") {
      const written = ts.isBinaryExpression(n.parent) && n.parent.left === n && n.parent.operatorToken.kind === ts.SyntaxKind.EqualsToken;
      if (!written) { const k = owner(n); reads[k] = (reads[k] ?? 0) + 1; }
    } else if (ts.isIdentifier(n) && n.text === "avgTurnH" && !(ts.isPropertyAccessExpression(n.parent) && n.parent.name === n) && !(ts.isPropertySignature(n.parent) && n.parent.name === n)) routes.push(where(n));
    else if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && n.text === "avgTurnH") routes.push(where(n));
    ts.forEachChild(n, visit);
  };
  visit(sf);
  assert.deepEqual(routes, [], "the raw average is reached as a property only (a destructure, a shorthand or a string key would pass the census unread)");
  assert.deepEqual(reads, {
    heldPxOf: 1, unitPx: 1,            // the figure's own two reads
    sizeSpacers: 2, measureRows: 1,    // null checks: a first measure (hadAvg and the recompute's), and measuring once
    contentOffsetFrame: 1,             // the scrollbar marks' frame, for a unit its cache holds no height for (a gap is never cached) while it sums; it places marks only, no walk or jump reads it
    virtualizeToViewport: 1,           // the re-window's margin, a distance into rendered rows, which stand at their real height
    __rompViewMeasure: 1,              // the long-history lab's read of the measures
  }, "every read of the raw average, by the function that reads it");
});

test("a reader INSIDE a gap asks for the page holding their turn, page-aligned and clipped to the gap (2026-10-01)", () => {
  assert.deepEqual(pageAt({ lo: 0, hi: 2005 }, 1344.6, 16), { lo: 1344, hi: 1360 }, "the page around the turn under the viewport top");
  assert.deepEqual(pageAt({ lo: 16, hi: 2005 }, 20, 16), { lo: 16, hi: 32 }, "an aligned page");
  assert.deepEqual(pageAt({ lo: 130, hi: 2005 }, 131, 16), { lo: 130, hi: 144 }, "clipped to the gap's start");
  assert.deepEqual(pageAt({ lo: 0, hi: 2005 }, 2004.9, 16), { lo: 2000, hi: 2005 }, "clipped to the gap's end");
  assert.deepEqual(pageAt({ lo: 100, hi: 200 }, 5, 16), { lo: 100, hi: 112 }, "a turn before the gap clamps to its first page");
  assert.deepEqual(pageAt({ lo: 100, hi: 200 }, 900, 16), { lo: 192, hi: 200 }, "…one after it to its last");
});

test("every gap height render.ts draws goes through the bound (2026-10-01): the element, its unit's signature, the spacer map, the scroll walk", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.doesNotMatch(RENDER, /gapHeight\(\s*it\s*,\s*(v\.)?pxPerTurn\s*\)/, "no gap height is drawn at the raw measure");
  const calls = RENDER.split("\n").filter((l) => !l.startsWith("import ")).join("\n");
  assert.equal((calls.match(/gapTurnPx\(/g) || []).length, (calls.match(/gapTurnPx\([^;\n]*?, gapsMaxPx\(\)\)/g) || []).length, "every figure takes the measured bound (a third argument)");
  assert.match(RENDER, /const per = gapTurnPx\(items, pxPerTurn, gapsMaxPx\(\)\);[^\n]*\n\s*for \(let u = 0; u < items\.length; u\+\+\) \{ const it = items\[u\]; if \(it\.kind === "gap"\) gu\.set\(u, gapHeight\(it, per\)\); \}/, "the spacer map (gapUnitsOf)");
  assert.match(RENDER, /g\.style\.height = gapHeight\(it, gapTurnPx\(s\.regions \?\? \[\], v\.pxPerTurn, gapsMaxPx\(\)\)\) \+ "px";/, "the gap element (drawn and measured in a browser by tests/test_long_history_height_browser.py UnreachableEndSettles)");
  assert.match(RENDER, /"gap\|" \+ it\.lo \+ "\|" \+ it\.hi \+ "\|" \+ it\.before \+ "\|" \+ gapTurnPx\(s\.regions \?\? \[\], v\.pxPerTurn, gapsMaxPx\(\)\) \+ "\|"/, "a gap unit's signature names the figure it is drawn at, so a change repaints it (this pin guards the source text only)");
  assert.equal((RENDER.match(/v\.gapUnits\?\.get\(u\) \?\? gapHeight\(it, gapTurnPx\(items, v\.pxPerTurn, gapsMaxPx\(\)\)\)/g) || []).length, 2, "the two scroll walks' fallbacks (this pin guards the source text only)");
  // the bound is a share of the limit MEASURED by a fixed probe, forgotten on a resize (a zoom change is one)
  assert.match(RENDER, /function gapsMaxPx\(\): number \{ return gapsBoundHeld \?\? layoutLimitPx\(\) \* GAPS_SHARE; \}/, "the share of the measured limit (a served lab may hold another figure)");
  // the held figure has ONE writer, the lab's hook: one assignment to it in render.ts, any assignment operator at any spacing (the
  // declaration's initializer reads `gapsBoundHeld: number | null = null`, not counted), and it sits in the hook's body
  assert.equal((RENDER.match(/\bgapsBoundHeld\s*(?:\?\?|\|\||&&|\*\*|<<|>>>?|[-+*\/%&|^])?=(?!=)/g) || []).length, 1, "one assignment to the held figure in render.ts");
  assert.match(RENDER, /\(window as any\)\.__rompGapsBound = \(px: number \| null\): void => \{ gapsBoundHeld = /, "…in the lab's hook (__rompGapsBound)");
  assert.match(RENDER, /probe\.style\.cssText = "position:fixed;[^"]*height:1000000000px;[^"]*";\s*\n\s*document\.body\.appendChild\(probe\);\s*\n\s*const h = probe\.offsetHeight;\s*\n\s*probe\.remove\(\);/, "the probe: fixed, a billion px asked, the clamp read back, removed in the same task");
  assert.match(RENDER, /window\.addEventListener\("resize", \(\) => \{ layoutLimit = null; \}\);/, "a resize forgets the measure");
});

test("the loops an unreachable end ran settle: each side acts on new information, not on the other's write (2026-10-01)", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  const virt = RENDER.slice(RENDER.indexOf("function virtualizeToViewport(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function virtualizeToViewport(): void {")));
  // the re-window ⟷ its own write's echo: the writer is recorded with the move, the echo names it, the re-window skips its own
  assert.match(RENDER, /if \(after !== before\) \{ lastScrollWriteAfter = after; lastScrollWriter = writer; \}/, "a moving write records its writer");
  assert.match(RENDER, /if \(gv\) gv\.echoOf = cls === "write-echo" \? lastScrollWriter : undefined;/, "the scroll event names the write it echoes");
  assert.match(virt, /const ownEcho = v\.echoOf === "rewindow";\s*\n\s*v\.echoOf = undefined;/, "virtualizeToViewport reads (and consumes) whether this is its own echo");
  assert.match(virt, /if \(ownEcho \|\| \(!nearTopEdge && !nearBotEdge\)\) return;/, "…and does not re-window on it");
  assert.ok(virt.indexOf("requestOlder(activeId, v, content); return; }") < virt.indexOf("if (ownEcho ||"), "the older-history ask still runs on any event");
  // a hidden unit keeps the reader's offset into it: a jump into a gap stays where it was dropped
  assert.match(virt, /const hiddenTop = before \? null : hiddenUnitTop\(v, content, c\);/, "the re-window reads a hidden unit's estimated top");
  assert.match(virt, /: \(hiddenTop != null \? hiddenTop - content\.scrollTop : 0\);/, "…and keeps the reader's offset into it");
  // the observer ⟷ the fill's rebuild: a gap a fill built asks nothing on its first observation; the fill's completion asks for a parked
  // reader; a reader's gesture over a gap in view asks; a reader inside a gap asks the page under them
  assert.match(RENDER, /if \(fillBuilding\) quietGaps\.add\(g\);\s*\n\s*gapObserver\?\.observe\(g\);/, "a fill-built gap element is marked quiet");
  assert.match(RENDER, /if \(quietGaps\.has\(g\)\) \{ quietGaps\.delete\(g\); continue; \}/, "…and its first observation asks nothing");
  // …at the measured limit only (layoutLimitPx, 2026-10-02): the fill reads once, before either rebuild, whether the layout stands at the
  // measured limit, and both its rebuilds mark their gaps quiet by that reading; below the limit a gap the fill leaves on screen asks on its
  // first observation like any gap (one under the page the fill brought asks for the next page). Source pins on where the reading sits.
  // Executed in tests/test_long_history_height_browser.py: the first rebuild's mark below the limit by LongHistoryHeight's near-end drop leg,
  // the reading at the limit by UnreachableEndInsideAGapSettles, and the completion's ask (pinned below) by that class's parked leg, where
  // the fill's gaps are quiet (below the limit, in LongHistoryHeight's parked leg, the rebuilt gap's first observation asks the same page,
  // so that leg passes without the completion's ask). Pinned by source text only: the second rebuild's mark (the re-window when no row is
  // left on screen; no assertion observes its mark), and the reading's 1 px tolerance (the 12x lab stands 1 px past the limit, so a reading
  // without it reads the same there)
  const fill = RENDER.slice(RENDER.indexOf("function fillInPlace("), RENDER.indexOf("\n}\n", RENDER.indexOf("function fillInPlace(")));
  assert.match(fill, /const atLimit = content\.scrollHeight >= layoutLimitPx\(\) - 1;/, "the fill reads whether the layout stands at the measured limit");
  assert.ok(fill.indexOf("const atLimit =") >= 0 && fill.indexOf("const atLimit =") < fill.indexOf("fillBuilding = atLimit"), "…before either rebuild");
  assert.equal((fill.match(/fillBuilding = atLimit;[^\n]*\n\s*try \{/g) || []).length, 2, "both rebuilds (the window around the reader, and the re-window when no row is left on screen) mark their gaps quiet by that reading, each inside a try");
  assert.equal((fill.match(/finally \{ fillBuilding = false; \}/g) || []).length, 2, "…and clear the mark however the rebuild ends");
  assert.doesNotMatch(RENDER, /fillBuilding = true/, "no rebuild marks its gaps quiet whatever the layout");
  assert.match(RENDER, /if \(pointBefore != null && s\.regions\) \{ const gp = gapAt\(s\.regions, Math\.floor\(pointBefore\)\); if \(gp\) requestTurns\(sid, gp, "inside", pointBefore\); \}/, "the fill's completion asks the page holding the parked reader's turn");
  assert.match(RENDER, /if \(cls === "gesture" && gv && activeId\) askGapsInView\(activeId, gv, c\);/, "a reader's gesture asks for the gaps in view");
  assert.match(RENDER, /if \(gr\.height > 0 && gr\.top < rootTop - 0\.5 && gr\.bottom > rootBottom \+ 0\.5\) requestTurns\(sid, \{ lo, hi \}, "inside", lo \+ \(hi - lo\) \* \(\(rootTop - gr\.top\) \/ gr\.height\)\);/, "inside a gap: the turn under the viewport top");
  assert.match(RENDER, /const span = edge === "inside" && at != null \? pageAt\(gap, at, s\.pageTurns \?\? 16\) : pagesToAsk\(gap, edge === "top" \? "top" : "bottom", s\.pageTurns \?\? 16\);/, "…asked as the page holding it");
});

test("every walk over the units hidden above the window starts where the top spacer does (2026-10-02)", () => {
  // topSpacerY (below #content's top padding) is the origin of all five: four walked from 0 and hiddenUnitTop from the spacer, so a pre-jump
  // to the head gap's start rested 8 px above the gap, where no turn is named. Source pins on the walks' origin. Executed: preJumpIntoGap's
  // by tests/test_landing_notice_browser.py road 16 (a cancelled jump into the hidden head gap rests where the thread's content starts, its
  // point named); turnUnderTop's (its origin, a top in the padding read as the spacer's start, and the spacer's last 8 px) by
  // tests/test_history_regions_browser.py ServedHiddenHeadPoint. The origins of unitAtScroll and yOfTurn are pinned by source text only:
  // aligned with the others, each moves a mapping by the padding's 8 px
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  const body = (sig: string) => { const i = RENDER.indexOf(sig); assert.ok(i >= 0, sig + " is in render.ts"); return RENDER.slice(i, RENDER.indexOf("\n}\n", i)); };
  assert.match(RENDER, /function topSpacerY\(v: View, content: HTMLElement\): number \{\s*\n\s*const top = v\.el\.querySelector\("\.tx-spacer-top"\) as HTMLElement \| null;\s*\n\s*return top \? top\.getBoundingClientRect\(\)\.top - content\.getBoundingClientRect\(\)\.top \+ content\.scrollTop : 0;/, "the origin: the spacer's own place in scroll space");
  const hut = body("function hiddenUnitTop("), uas = body("function unitAtScroll("), tut = body("function turnUnderTop("), yot = body("function yOfTurn("), pj = body("function preJumpIntoGap(");
  assert.match(hut, /let y = topSpacerY\(v, content\);\s*\n\s*for \(let k = 0; k < u; k\+\+\)/, "hiddenUnitTop");
  assert.match(uas, /topY = topSpacerY\(v, content\);\s*\n\s*if \(st < topY \+ topH\)/, "unitAtScroll: inside the spacer by where it starts");
  assert.match(uas, /return Math\.max\(0, Math\.floor\(\(st - topY\) \/ avg\)\);\s*\n\s*let y = topY;/, "…and walks from there, by its own heights or unitPx");
  assert.match(tut, /topY = topSpacerY\(v, content\);\s*\n\s*if \(spacer && top < topY \+ topH\)/, "turnUnderTop: inside the spacer by where it starts");
  assert.match(tut, /const at = Math\.max\(top, topY\), avg = [^;]+; let y = topY;/, "…walks from there, a top in the padding above it read as its start");
  assert.match(yot, /let y = topSpacerY\(v, content\);/, "yOfTurn's hidden walk");
  assert.match(pj, /if \(idx < \(v\.winStart \?\? 0\)\) y = topSpacerY\(v, content\) \+ hiddenHeight\(v, 0, idx, avg\) \+ frac \* own;/, "preJumpIntoGap's hidden road");
  const walks: [string, string][] = [["unitAtScroll", uas], ["turnUnderTop", tut], ["yOfTurn", yot]];
  for (const [name, b] of walks) {
    const starts = Array.from(b.matchAll(/let y = ([^;]+);/g), (m) => m[1]);
    assert.ok(starts.length > 0, name + " has a walk to read");
    for (const at of starts) assert.ok(["topY", "topSpacerY(v, content)", "bTop"].includes(at), name + " starts a walk at " + at + ", not at a spacer's own place");
  }
});

test("the page a gap asks for: scrolling up meets the bottom edge (the last aligned page, clipped to the gap); scrolling down the top edge", () => {
  assert.deepEqual(pagesToAsk({ lo: 0, hi: 96 }, "bottom", 16), { lo: 80, hi: 96 });
  assert.deepEqual(pagesToAsk({ lo: 0, hi: 96 }, "top", 16), { lo: 0, hi: 16 });
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 200 }, "bottom", 16), { lo: 192, hi: 200 }, "the last page is clipped to the gap's end");
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 200 }, "top", 16), { lo: 130, hi: 144 }, "the first page runs from the gap's start to the next boundary");
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 140 }, "top", 16), { lo: 130, hi: 140 }, "a gap inside one page asks for exactly itself");
  assert.deepEqual(pagesToAsk({ lo: 130, hi: 140 }, "bottom", 16), { lo: 130, hi: 140 });
});

test("an anchor's place inside a gap is its time's proportion between the neighbours; one neighbour or none is the honest edge", () => {
  assert.equal(gapFraction(150, 100, 200), 0.5);
  assert.equal(gapFraction(50, 100, 200), 0, "before the run above's last: clamped to the gap's start");
  assert.equal(gapFraction(250, 100, 200), 1, "after the run below's first: clamped to the gap's end");
  assert.equal(gapFraction(150, 100, null), 1, "only the run above: the gap's end");
  assert.equal(gapFraction(150, null, 200), 0, "only the run below: the gap's start");
  assert.equal(gapFraction(150, null, null), 0);
});

test("the one notice's words, with the reader's clock and without a time", () => {
  assert.equal(landingNotice(1789000000, (t) => "7:41 AM (" + t + ")"), "Going to the message from 7:41 AM (1789000000), click to stay here");
  assert.equal(landingNotice(null, () => "never"), "Going to the earlier message, click to stay here");
});

test("the cancel drops the target and the notice and leaves the ask in flight: its reply still inserts the run", () => {
  assert.deepEqual(landingCancel({ target: "u-1", notice: true, askInFlight: true }), { target: null, notice: false, askInFlight: true });
  assert.deepEqual(landingCancel({ target: "u-1", notice: true, askInFlight: false }), { target: null, notice: false, askInFlight: false });
});

test("the gap a turn falls in", () => {
  const rs = insertRun(regionsFromRuns([run(200, null, ["t1"])]), run(96, 128, ["w1"]));
  assert.deepEqual(gapAt(rs, 10), { kind: "gap", lo: 0, hi: 96 });
  assert.deepEqual(gapAt(rs, 150), { kind: "gap", lo: 128, hi: 200 });
  assert.equal(gapAt(rs, 100), null, "a turn in a run is in no gap");
  assert.equal(gapAt(rs, 250), null, "the tail is in no gap");
});

// ── a full frame against a held run (the client merge guard, 2026-09-19) ─────────────────────────────────────────────────────
const H = (ks: string[]) => ks.map((k) => ({ uuid: k, kind: "user" })) as Ev[];
const keys = (es: readonly Ev[]) => es.map((e) => e.uuid);
const transcript = (e: Ev) => !OVERLAY_KINDS.has(String(e.kind)) && !String(e.uuid).startsWith("optimistic:") && !String(e.uuid).startsWith("held:");

test("the mirrored overlay set is the kernel's own (_OVERLAY_KINDS), member for member", () => {
  const kernel = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const m = /_OVERLAY_KINDS = frozenset\(\(([^)]*)\)\)/.exec(kernel);
  assert.ok(m, "the kernel names its overlay kinds in one frozenset");
  const kinds = Array.from(m![1].matchAll(/"([^"]+)"/g), (x) => x[1]).sort();
  assert.deepEqual(Array.from(OVERLAY_KINDS).sort(), kinds, "the client skips exactly the cards the kernel's anchor rule skips");
});

test("a held run against a frame's tail: the prefix before the first shared key stays above, what the frame lacks from that key on goes, and no shared key keeps the whole run", () => {
  let r = splitHeldAgainstFrame(H(["h1", "h2", "t1", "t2", "x", "t3"]), H(["t1", "t2", "t3", "t4"]), transcript);
  assert.deepEqual(keys(r.before), ["h1", "h2"], "history the frame did not carry");
  assert.deepEqual(keys(r.dropped), ["x"], "a resident row inside the frame's span the frame lacks: retracted");
  assert.equal(r.behind, false, "the frame carries t4 past the last shared key: it is newer than the page, not behind");
  r = splitHeldAgainstFrame(H(["t1", "t2"]), H(["live1", "live2"]), transcript);
  assert.deepEqual(keys(r.before), ["t1", "t2"], "no shared key: the whole held run is history above the frame (the floor-cut shape)");
  assert.deepEqual([keys(r.dropped), r.behind], [[], false]);
});

test("behind: the frame's last transcript key is resident and a transcript row the page holds sits after it", () => {
  let r = splitHeldAgainstFrame(H(["t1", "t2", "t3"]), H(["t1", "t2"]), transcript);
  assert.deepEqual([keys(r.before), keys(r.dropped), r.behind, r.afterLast], [[], ["t3"], true, 1], "a same-list stale full: the newest row is missing from it");
  r = splitHeldAgainstFrame(H(["t1", "t2", "t3"]), [...H(["t1", "t2"]), { uuid: "todo:9", kind: "todo" }], transcript);
  assert.equal(r.behind, true, "a frame ending in an overlay card whose key is not resident reads its last TRANSCRIPT key (the kernel's anchor rule), so the behind frame is still seen");
  r = splitHeldAgainstFrame([...H(["t1", "t2"]), { uuid: "apiError:1", kind: "apiError" }], H(["t1", "t2"]), transcript);
  assert.deepEqual([keys(r.dropped), r.behind], [["apiError:1"], false], "a dropped overlay card is no evidence: those come and go between builds");
  r = splitHeldAgainstFrame([...H(["t1", "t2"]), { uuid: "optimistic:5", kind: "queued" }], H(["t1", "t2"]), transcript);
  assert.deepEqual([keys(r.dropped), r.behind], [["optimistic:5"], false], "the client's own bubble is no evidence either");
});

test("the echo landing: the held echo after the last shared key is dropped, not filed above, and the frame carrying the record and the reply is not behind", () => {
  const r = splitHeldAgainstFrame(H(["t1", "t2", "t3", "echo:x"]), H(["t3", "rec", "reply"]), transcript);
  assert.deepEqual(keys(r.before), ["t1", "t2"]);
  assert.deepEqual(keys(r.dropped), ["echo:x"], "the echo the record replaced leaves with the frame");
  assert.equal(r.behind, false);
});
