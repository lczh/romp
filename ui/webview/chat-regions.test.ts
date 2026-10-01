// The history regions' pure rules (T386 stage 2), each executed: runs and gaps from spans, a window inserted among them, a gap's
// estimated height, the page a gap asks for at either edge, the anchor's place inside a gap, the one notice's words, the cancel.
import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { DEFAULT_TURN_PX, GAPS_MAX_PX, OVERLAY_KINDS, gapAt, gapFraction, gapHeight, gapTurnPx, insertRun, landingCancel, landingNotice, pagesToAsk, regionsFromRuns, runsOf, splitHeldAgainstFrame, turnsBeforeTail, type Ev, type Gap, type Region, type Run } from "./chat-regions";

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
});

test("every gap height render.ts draws goes through the bound (2026-10-01): the element, its unit's signature, the spacer map, the scroll walk", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.doesNotMatch(RENDER, /gapHeight\(\s*it\s*,\s*(v\.)?pxPerTurn\s*\)/, "no gap height is drawn at the raw measure");
  assert.match(RENDER, /const per = gapTurnPx\(items, pxPerTurn\);[^\n]*\n\s*for \(let u = 0; u < items\.length; u\+\+\) \{ const it = items\[u\]; if \(it\.kind === "gap"\) gu\.set\(u, gapHeight\(it, per\)\); \}/, "the spacer map (gapUnitsOf)");
  assert.match(RENDER, /g\.style\.height = gapHeight\(it, gapTurnPx\(s\.regions \?\? \[\], v\.pxPerTurn\)\) \+ "px";/, "the gap element");
  assert.match(RENDER, /"gap\|" \+ it\.lo \+ "\|" \+ it\.hi \+ "\|" \+ it\.before \+ "\|" \+ gapTurnPx\(s\.regions \?\? \[\], v\.pxPerTurn\) \+ "\|"/, "a gap unit's signature names the figure it is drawn at, so a change repaints it");
  assert.equal((RENDER.match(/v\.gapUnits\?\.get\(u\) \?\? gapHeight\(it, gapTurnPx\(items, v\.pxPerTurn\)\)/g) || []).length, 2, "the two scroll walks' fallbacks");
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
