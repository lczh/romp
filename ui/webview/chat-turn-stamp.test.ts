// The px per turn a history gap is drawn at counts the KERNEL's turns (2026-10-01). A gap stands for turns [lo, hi) in the kernel's
// numbering, and its height is that count times a px-per-turn the page measures off the rows it holds. The page used to count only the
// typed rows as turns, while the kernel opens a turn on a peer session's message too, so on a session that takes much mail the measure
// read about half the turns and every gap stood about twice its height. The kernel now stamps each event with its turn (`turn`, caps
// `chatTurn`); here, executed: the pure measure over a synthetic window of typed and peer turns, the old-kernel fallback with its one
// client-diag row, and render.ts's turnOfEvents and measureRows lifted and driven over stubs. Synthetic rows only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { kernelTurnOf, measureTurnPx, runsOf, type MeasuredRow, type Run } from "./chat-regions";
import { hostOf } from "./host-prefix";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

/** A render.ts span, transpiled (TS → JS) with esbuild at run time (the chat-exact-tail-exec.test.ts pattern). */
function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

// A window of the tail run: turns `from`..`to`-1, the even ones typed (a user row and the reply), the odd ones opened by a peer session's
// message (its card and the reply). Every turn is 160 px tall, so the true px per turn is 160.
function windowRows(from: number, to: number, stamped: boolean): MeasuredRow[] {
  const rows: MeasuredRow[] = [];
  for (let k = from; k < to; k++) {
    const kturn = stamped ? k : null;
    if (k % 2 === 0) rows.push({ h: 64, user: true, kturn }, { h: 96, user: false, kturn });
    else rows.push({ h: 56, user: false, kturn }, { h: 104, user: false, kturn });
  }
  return rows;
}

test("the kernel's turn is read off an event only as a whole, non-negative number", () => {
  assert.equal(kernelTurnOf({ kind: "user", turn: 0 }), 0);
  assert.equal(kernelTurnOf({ kind: "assistant", turn: 1441 }), 1441);
  for (const e of [{ kind: "user" }, { turn: -1 }, { turn: 2.5 }, { turn: "3" }, null, undefined, 7])
    assert.equal(kernelTurnOf(e), null, JSON.stringify(e));
});

test("a window of typed and peer turns measures px per KERNEL turn; the typed count it replaced read twice that", () => {
  const stamped = measureTurnPx(windowRows(200, 240, true));
  assert.equal(stamped.road, "kernel");
  assert.equal(stamped.turns, 40, "every turn counts, the ones a peer's message opened too");
  assert.equal(stamped.px, 160, "the true height of a turn");
  const typed = measureTurnPx(windowRows(200, 240, false));
  assert.equal(typed.road, "typed", "rows that carry no kernel turn (an older kernel) fall back to the typed rows");
  assert.equal(typed.turns, 20);
  assert.equal(typed.px, 320, "…which counts half the turns and reads twice the height: the gaps stood twice as tall");
});

test("the kernel's turns are counted per stretch of rows: a gap element between two runs adds none of its own turns", () => {
  const rows = [...windowRows(10, 20, true), { h: 0, user: false, kturn: null, gap: true }, ...windowRows(30, 40, true)];
  const m = measureTurnPx(rows);
  assert.equal(m.turns, 20, "turns 10-19 and 30-39; the gap's 20-29 stand in the gap, not in these rows");
  assert.equal(m.px, 160);
});

test("a turn that renders no row still counts (the gap counts its span); a row the page injected adds height but no turn", () => {
  const rows = [...windowRows(0, 4, true), ...windowRows(5, 8, true)];   // turn 4 renders nothing (an idle span, say)
  assert.equal(measureTurnPx(rows).turns, 8, "turns 0-7: the last minus the first plus one");
  const withEcho = [...windowRows(0, 4, true), { h: 40, user: true, kturn: null }];   // an optimistic echo: not a kernel turn yet
  const m = measureTurnPx(withEcho);
  assert.equal(m.road, "kernel");
  assert.equal(m.turns, 4);
  assert.equal(m.px, (4 * 160 + 40) / 4);
});

test("no rows, or rows with no height (a hidden view), measure nothing", () => {
  assert.equal(measureTurnPx([]).px, null);
  assert.equal(measureTurnPx([{ h: 0, user: true, kturn: 3 }]).px, null);
  assert.equal(measureTurnPx([{ h: 0, user: true, kturn: null }]).px, null);
});

// ── render.ts's turnOfEvents, executed ───────────────────────────────────────────────────────────────────────────────
function liftTurnOfEvents(): (s: { events: any[]; regions: any[] | null }) => number[] {
  const js = liftBetween("function turnOfEvents(s: Session): number[] {", "/** The point under the viewport top as a TURN");
  return new Function("runsOf", "kernelTurnOf", js + "\nreturn turnOfEvents;")(runsOf, kernelTurnOf);
}

test("turnOfEvents names each event's turn by the kernel's stamp, so a peer's turn is a turn; unstamped events fall back to the typed count", () => {
  const turnOfEvents = liftTurnOfEvents();
  const kinds = ["user", "assistant", "postal-service", "assistant", "user", "assistant", "postal-service", "assistant"];
  const stamped = kinds.map((kind, i) => ({ kind, uuid: "e" + i, turn: 100 + Math.floor(i / 2) }));
  const run = (events: any[]): Run => ({ kind: "run", lo: 100, hi: null, events });
  assert.deepEqual(turnOfEvents({ events: stamped, regions: [{ kind: "gap", lo: 0, hi: 100 }, run(stamped)] }),
    [100, 100, 101, 101, 102, 102, 103, 103], "the kernel's numbering: four turns");
  const bare = stamped.map(({ turn, ...e }) => e);
  assert.deepEqual(turnOfEvents({ events: bare, regions: [{ kind: "gap", lo: 0, hi: 100 }, run(bare)] }),
    [100, 100, 100, 100, 101, 101, 101, 101], "an older kernel: the typed rows alone open turns, as before");
  const echo = [...stamped, { kind: "user", uuid: "opt:1" }];   // the page's optimistic echo after the kernel's rows: the next turn
  assert.deepEqual(turnOfEvents({ events: echo, regions: [{ kind: "gap", lo: 0, hi: 100 }, run(echo)] }).slice(-2), [103, 104]);
  const head = [{ kind: "system", uuid: "system:1" }, ...stamped.slice(0, 2).map((e) => ({ ...e, turn: 0 }))];   // head cards above turn 0
  assert.deepEqual(turnOfEvents({ events: head, regions: [{ kind: "run", lo: 0, hi: null, events: head }] }), [0, 0, 0]);
});

// ── render.ts's measureRows and its client-diag row, executed ────────────────────────────────────────────────────────
type Row = { what: string; data: any };
function liftMeasureRows(caps: string[]): { measure: (v: any) => void; views: Map<string, any>; rows: Row[] } {
  const js = liftBetween("function measureRows(v: View): void {", "\n/** The estimated top, in scroll space");
  const views = new Map<string, any>(), rows: Row[] = [];
  const chatDiagRow = (what: string, data: any) => { rows.push({ what, data }); };
  const measure = new Function("measureTurnPx", "views", "hostOf", "kernelCaps", "chatDiagRow", js + "\nreturn measureRows;")(
    measureTurnPx, views, hostOf, new Set(caps), chatDiagRow);
  return { measure, views, rows };
}
/** A view whose children are the rows of `measured`, as the DOM would hold them (a gap's element, turn rows with data-kturn). */
function fakeView(measured: MeasuredRow[]): any {
  const children = measured.map((r) => {
    const cls = new Set(r.gap ? ["tx-gap"] : ["turn", r.user ? "turn-user" : "turn-assistant"]);
    const dataset: Record<string, string> = {};
    if (r.kturn != null) dataset.kturn = String(r.kturn);
    return { classList: { contains: (c: string) => cls.has(c) }, offsetHeight: r.h, dataset };
  });
  return { el: { children } };
}

test("measureRows takes the kernel's turns off data-kturn and files nothing", () => {
  const { measure, views, rows } = liftMeasureRows(["chatProto2", "chatTurn"]);
  const v = fakeView(windowRows(200, 240, true));
  views.set("aaaaaaaa-1111-2222-3333-444444444444", v);
  measure(v);
  assert.equal(v.pxPerTurn, 160);
  assert.deepEqual(rows, [], "the kernel road is the normal one: no row");
});

test("on an older kernel measureRows falls back to the typed count and says so once per session in client-diag", () => {
  const { measure, views, rows } = liftMeasureRows(["chatProto2"]);   // a kernel without chatTurn
  const sid = "aaaaaaaa-1111-2222-3333-444444444444";
  const v = fakeView(windowRows(200, 240, false));
  views.set(sid, v);
  measure(v);
  assert.equal(v.pxPerTurn, 320, "today's count, unchanged on an older kernel");
  assert.deepEqual(rows, [{ what: "turnCountTyped", data: { sid, cause: "olderKernel", typed: 20, rows: 80 } }], "…and said, not silent");
  const v2 = fakeView(windowRows(200, 240, false));   // the same session's view built again: said once per page load
  views.set(sid, v2);
  measure(v2);
  assert.equal(rows.length, 1);
});

test("the typed road names its cause: a remote host the page holds no caps for, or a kernel that announced chatTurn and sent no stamps", () => {
  const remote = liftMeasureRows(["chatProto2", "chatTurn"]);
  const rv = fakeView(windowRows(0, 10, false));
  remote.views.set("TESTHOST:bbbbbbbb-1111-2222-3333-444444444444", rv);
  remote.measure(rv);
  assert.equal(remote.rows[0]?.data.cause, "remote");
  const local = liftMeasureRows(["chatProto2", "chatTurn"]);
  const lv = fakeView(windowRows(0, 10, false));
  local.views.set("cccccccc-1111-2222-3333-444444444444", lv);
  local.measure(lv);
  assert.equal(local.rows[0]?.data.cause, "unstamped", "a local kernel that promised the field and sent none: the row says so");
});

test("a hidden view measures nothing and files nothing; a later visible measure decides", () => {
  const { measure, views, rows } = liftMeasureRows(["chatProto2"]);
  const v = fakeView(windowRows(0, 10, false).map((r) => ({ ...r, h: 0 })));
  views.set("dddddddd-1111-2222-3333-444444444444", v);
  measure(v);
  assert.equal(v.pxPerTurn, undefined);
  assert.deepEqual(rows, []);
});

test("every row a unit paints carries the kernel's turn beside the regions' turn, from the unit's own event", () => {
  const append = RENDER.slice(RENDER.indexOf("function appendItem("), RENDER.indexOf("\nfunction ", RENDER.indexOf("function appendItem(") + 1));
  assert.match(append, /const kt = f0 >= 0 && f0 < s\.events\.length \? kernelTurnOf\(s\.events\[f0\]\) : null;/);
  assert.match(append, /if \(turnOf != null\) node\.dataset\.turn = turnOf; if \(kt != null\) node\.dataset\.kturn = String\(kt\);/);
});

test("the kernel announces chatTurn in its caps", () => {
  assert.match(KERNEL, /KERNEL_WS_CAPS = \([^)]*"chatTurn"[^)]*\)/, "advertised, so the page's diag row can tell an older kernel from a missing stamp");
});
