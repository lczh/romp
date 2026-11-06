// A Thought row's token figure, EXECUTED through the keyed paint (2026-10-23, the follow-up to the Thought rows): render.ts
// tailDiff and unitSig, the real appendItem and the Thought row's renderers, lifted and driven over a minimal fake DOM the
// way chat-exact-tail-exec.test.ts lifts chatTail. A thought's tokens ride its message's FIRST thinking row, often a textless
// one just before the words, and a textless row belongs to no display unit. Signed by its members alone, a Thought row
// stood as painted when the figure landed on that textless row in a later frame, so an opened row read "Thought" with no
// "· N tokens". The signature now covers every row the Thought row reads (unit-diff.ts unitReads). Synthetic events only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import * as unitDiff from "./unit-diff";   // the whole module: the lifted unitSig names whichever helper it calls
import { compactDisplay, itemAnchor, runRows, thoughtHasWords, thoughtKey, thoughtOf } from "./compact";
import { tokensShort } from "./live-status";
import { DayWalk } from "./time-marker";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

/** Enough of a DOM node for the keyed paint and the Thought row: children with parent links, a class list, data-*,
 *  insertBefore of a fragment (its children move), nextSibling, and the one spacer selector tailDiff asks for. */
class FakeEl {
  children: FakeEl[] = []; parentNode: FakeEl | null = null; dataset: Record<string, string> = {}; textContent = "";
  constructor(public tag: string, public className = "") {}
  get classList() {
    const cls = () => this.className.split(/\s+/).filter(Boolean);
    return {
      contains: (c: string) => cls().includes(c),
      add: (c: string) => { if (!cls().includes(c)) this.className = [...cls(), c].join(" "); },
    };
  }
  get nextSibling(): FakeEl | null { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] ?? null : null; }
  appendChild(c: FakeEl): FakeEl { return this.insertBefore(c, null); }
  insertBefore(c: FakeEl, ref: FakeEl | null): FakeEl {
    const moving = c.tag === "#fragment" ? c.children.splice(0) : [c];
    for (const n of moving) n.parentNode?.removeChild(n);
    const at = ref ? this.children.indexOf(ref) : -1;
    this.children.splice(at < 0 ? this.children.length : at, 0, ...moving);
    for (const n of moving) n.parentNode = this;
    return c;
  }
  removeChild(c: FakeEl): FakeEl { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  querySelector(sel: string): FakeEl | null {
    if (/^:scope > \.tx-spacer-(top|bot)$/.test(sel)) return null;   // a window from the top to the tail: no spacer
    throw new Error("unsupported selector " + sel);
  }
  /** The first descendant carrying class `c` (the test's own lookup). */
  find(c: string): FakeEl | null {
    for (const k of this.children) { if (k.classList.contains(c)) return k; const d = k.find(c); if (d) return d; }
    return null;
  }
}

type Paint = { tailDiff: (v: any, s: any, items: any[], working: boolean, atTail: boolean) => boolean; openFolds: Set<string> };

function liftPaint(): Paint {
  const js = liftBetween("function tailDiff(", "/** The bottom spacer re-sized")
           + liftBetween("function appendItem(", "// Full (re)build of the window")
           + liftBetween("function thoughtEvent(", "// Stable identity for a collapsed tool run");
  const prelude = `
    const H = HOOKS;
    const { contentHash, sameUnit, sameUnits, unitEvents, unitReads, unitLastEvent } = H.unitDiff;
    const { itemAnchor, runRows, thoughtOf } = H.compact;
    const tokensShort = H.tokensShort, DayWalk = H.DayWalk;
    const document = { createDocumentFragment: () => new H.FakeEl("#fragment") };
    const openFolds = new Set();
    const el = (tag, cls) => new H.FakeEl(tag, cls || "");
    const dot = (kind) => el("span", "dot " + kind);
    const applyFold = (target, cls, key) => { if (key && openFolds.has(key)) target.classList.add(cls); };
    const setTip = () => {};
    // what the real renderEvent does for these rows: a Thought row through renderThought (renderEventInner), any other event
    // as a plain turn; both carry the event's uuid
    const renderEvent = (ev) => { const n = ev.kind === "thinking" && ev.row ? renderThought(ev, ev.row) : el("div", "turn turn-" + ev.kind); n.dataset.uuid = ev.uuid || ""; return n; };
    const renderToolGroup = () => el("div", "turn turn-toolgroup");
    const captionRun = () => {};   // option C's caption under a closed run (2026-11-06): not this test's
    const toolGroupKey = (first) => "tg:" + first.uuid;
    const noticeGroupKey = (first) => "ng:" + first.uuid;
    const eventEpoch = (ev) => (ev && ev.t != null ? ev.t : null);
    const itemFirstEvent = (it) => (it.kind === "event" ? it.index : it.kind === "gap" ? it.before : it.indices[0]);
    const prevTimedEpoch = () => null;
    const dayWalkBefore = () => new DayWalk();
    const dayDividerFor = () => null;
    const stampWalkDay = () => {};
    const unitExit = () => null;
    const kernelTurnOf = () => null;
    const turnFoot = () => [null, null];
    const turnOfEvents = () => [];
    const gapUnitsOf = () => undefined;
    const patchWorkedFooters = () => {};
  `;
  return new Function("HOOKS", prelude + js + "\nreturn { tailDiff, openFolds };")(
    { FakeEl, unitDiff, compact: { itemAnchor, runRows, thoughtOf }, tokensShort, DayWalk }) as Paint;
}

const U = (n: number) => "11111111-2222-4333-8444-" + String(n).padStart(12, "0");
const prompt = { kind: "user", uuid: U(1), md: "Add cursor pagination to GET /notes.", human: true };
const textless = (uuid: string, thinkTokens?: number) => ({ kind: "thinking", uuid, text: "", encrypted: true, ...(thinkTokens ? { thinkTokens } : {}) });
const words = (uuid: string, text: string) => ({ kind: "thinking", uuid, text });
const tool = (uuid: string, name: string) => ({ kind: "tool", uuid, name, input: "{}" });

/** Compact mode's units over the session's events, as syncViewInner builds them (thoughts: the blocks with words). */
const unitsOf = (evs: any[]) => compactDisplay(evs.map((e) => e.kind), evs.map((e) => e.name), undefined, evs.map((e) => thoughtHasWords(e)));

/** One chat view painted by the keyed paint: the first frame paints every unit, a later frame swaps the events the way
 *  chatTail's passes do (the changed object replaced) and lowers `rendered` to the first one replaced (repaintFromChange). */
function world(evs: any[], open: string[]) {
  const P = liftPaint();
  for (const k of open) P.openFolds.add(k);
  const v: any = { el: new FakeEl("div", "thread"), winStart: 0, rendered: 0, painted: { items: [], sig: [], out: [] } };
  const s: any = { id: "web", events: evs };
  assert.equal(P.tailDiff(v, s, unitsOf(s.events), true, true), true, "the first frame paints through the keyed paint");
  return {
    v, s,
    frame(next: any[]) {
      const at = unitDiff.firstReplaced(s.events, next);
      if (at >= 0) v.rendered = Math.min(v.rendered, at);
      s.events = next;
      assert.equal(P.tailDiff(v, s, unitsOf(s.events), true, true), true, "the later frame takes the keyed paint too");
    },
    row: (cls: string) => v.el.children.find((n: FakeEl) => n.classList.contains(cls)) as FakeEl | undefined,
  };
}
const meta = (row: FakeEl | undefined) => row?.find("thought-meta")?.textContent ?? null;

test("an opened Thought row repaints with its tokens when the figure lands on the textless block before it in a later frame", () => {
  const first = textless(U(2)), answer = words(U(3), "The default has to move from 20 to 50, and one test pins 20.");
  const w = world([prompt, first, answer], [thoughtKey(answer)!]);
  assert.deepEqual(unitsOf(w.s.events), [{ kind: "event", index: 0 }, { kind: "thought", indices: [2] }], "the textless block is in no unit");
  const before = w.row("turn-thought"), prompted = w.row("turn-user");
  assert.ok(before && before.classList.contains("expanded"), "the row is open (its fold key is in the fold set)");
  assert.equal(meta(before), null, "no figure yet: the message's usage has not arrived");
  // the message completes: its thinking tokens ride its FIRST thinking row, the textless one
  w.frame([prompt, textless(U(2), 1840), answer]);
  const after = w.row("turn-thought");
  assert.equal(meta(after), "· 1.8k tokens", "the opened row shows the figure");
  assert.notEqual(after, before, "…because it was repainted: a row it reads changed");
  assert.ok(after!.classList.contains("expanded"), "…and it is still open");
  assert.equal(w.row("turn-user"), prompted, "the prompt above it stands as painted");
});

test("a Thought row inside an opened run repaints with its tokens too", () => {
  const a = tool(U(11), "Read"), b = tool(U(12), "Edit"), note = words(U(14), "Both callers pass no limit, so a default keeps them working.");
  const w = world([prompt, a, b, textless(U(13)), note], [thoughtKey(note)!, "tg:" + a.uuid]);
  assert.deepEqual(unitsOf(w.s.events)[1], { kind: "toolgroup", indices: [1, 2, 4] }, "the run holds its tools and the thought, not the textless block");
  const before = w.row("turn-thought");
  assert.ok(before && before.classList.contains("tg-child"), "the open run draws the thought as a row of its own");
  assert.equal(meta(before), null);
  w.frame([prompt, a, b, textless(U(13), 2400), note]);
  assert.equal(meta(w.row("turn-thought")), "· 2.4k tokens", "the run's Thought row shows the figure");
});

test("a frame that changes nothing the Thought row reads leaves it as painted", () => {
  const answer = words(U(3), "Got it: switching the default page size to 50.");
  const w = world([prompt, textless(U(2), 960), answer], [thoughtKey(answer)!]);
  const before = w.row("turn-thought");
  assert.equal(meta(before), "· 960 tokens");
  w.frame([prompt, textless(U(2), 960), answer, { kind: "assistant", uuid: U(4), md: "Done." }]);
  assert.equal(w.row("turn-thought"), before, "a reply appended below it does not repaint it");
});
