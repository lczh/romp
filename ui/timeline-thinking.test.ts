// The Sessions pane's live thinking label (2026-10-08): a lane's working chip reads "Thinking", with the subagent count it
// already wore, while the kernel's liveStatus frame names a stretch whose frames flow, and Working again once they go quiet
// (romp-timeline-view.js liveStatus / _paintThinking). The chip is laid out for the wider of its two labels, so a stretch
// starting, going quiet or ending rewrites its text in place: no column, battery or plot edge moves, and no draw runs
// (the second review of #171: the counting label widened the shared chip column and slid the plot sideways, and every
// stretch edge ran a full draw). The seconds are the chat's: its foot line and the tab's tooltip count them. Synthetic data.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

// ── the render harness (ui/timeline-render.test.ts's DOM shim: 6 px per character) ──
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) { c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    get firstChild() { return this.children[0] || null; },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1400, height: 420, left: 0, top: 0, right: 1400, bottom: 420 }; },
    closest() { return null; }, focus() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = g.document || {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; }, addEventListener() {}, removeEventListener() {},
};
g.localStorage = g.localStorage || { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = g.getComputedStyle || (() => ({ backgroundColor: "rgb(30,30,30)" }));
g.requestAnimationFrame = g.requestAnimationFrame || (() => 0);
g.addEventListener = g.addEventListener || (() => {}); g.removeEventListener = g.removeEventListener || (() => {});
g.matchMedia = g.matchMedia || (() => ({ matches: false, addEventListener() {}, addListener() {} }));
g.window = g.window || g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const VIEW = fs.readFileSync(viewPath, "utf8");
const tl = createRequire(__filename)(viewPath);

function board() {
  const now = Math.floor(Date.now() / 1000);
  const sess = (id: string, name: string, extra: any = {}) => ({
    id, name, color: "#7aa2f7", state: "working", live: true, model: "Opus 4.8", effort: "xhigh",
    context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false, ...extra,
  });
  return { now, sessions: [sess("api", "api", { subagents: [{ type: "general-purpose", agentId: "a0000000000000001" }] }), sess("web", "web")],
           turns: { api: [], web: [] }, messages: [], activeChat: null, focus: null, hover: null, usage: null };
}

test("the chip reads Thinking while the frames flow, keeps the subagent count, and says nothing once quiet", () => {
  assert.equal(tl.tlThinkingLabel({ t: 1000 }, 0), "Thinking");
  assert.equal(tl.tlThinkingLabel({ t: 1000 }, 2), "Thinking · 2 subagents", "the count the working chip wore stays");
  assert.equal(tl.tlThinkingLabel({ t: 1000 }, 1), "Thinking · 1 subagent");
  assert.equal(tl.tlThinkingLabel({ t: 1000, quiet: 1065 }, 1), null, "a silent stream: the lane's own working label again");
  assert.equal(tl.tlThinkingLabel(null, 1), null);
  assert.deepEqual(tl.tlChipCandidates("Working · 1 subagent", 1), ["Working · 1 subagent", "Thinking · 1 subagent"]);
});

test("a stretch starting, going quiet and ending moves no column and runs no draw", () => {
  const panel = new tl.TimelinePanel(makeNode("div"));
  panel.data = board();
  panel.draw();
  const left0 = panel.M.left;
  const lab = panel._workLabels.get("api");
  assert.ok(lab, "the working chip's overlay label exists");
  assert.equal(lab.textContent, "Working · 1 subagent");
  let draws = 0;
  const realDraw = panel.draw.bind(panel);
  panel.draw = () => { draws++; realDraw(); };
  panel.liveStatus({ type: "liveStatus", id: "api", thinking: { t: Math.floor(Date.now() / 1000) - 14 }, tasks: {} });
  assert.equal(lab.textContent, "Thinking · 1 subagent", "the chip's text, in place, the subagent count kept");
  assert.equal(draws, 0, "no draw on the stretch's start");
  panel.liveStatus({ type: "liveStatus", id: "api", thinking: { t: 1, quiet: 2 }, tasks: {} });
  assert.equal(lab.textContent, "Working · 1 subagent", "quiet: the pane stops claiming thinking");
  panel.liveStatus({ type: "liveStatus", id: "api", thinking: { t: 1 }, tasks: {} });
  realDraw();   // a draw for any other reason while it thinks: the same layout
  assert.equal(panel.M.left, left0, "the plot edge did not move with the label");
  assert.equal(panel._workLabels.get("api").textContent, "Thinking · 1 subagent");
  panel.draw = () => { draws++; realDraw(); };
  panel.liveStatus({ type: "liveStatus", id: "api", thinking: null, tasks: {} });
  assert.equal(panel._workLabels.get("api").textContent, "Working · 1 subagent", "the end puts the working label back");
  assert.equal(draws, 0, "no draw on the end either");
});

test("liveStatusReset drops every stretch on a new socket, or one host's on its relay's reopen", () => {
  const panel = new tl.TimelinePanel(makeNode("div"));
  for (const id of ["api", "far:web", "far:tests"]) panel.liveStatus({ id, thinking: { t: 1 }, tasks: {} });
  panel.liveStatusReset(["far"]);
  assert.deepEqual([...panel._thinking.keys()], ["api"], "a reopened relay drops only its own host's lanes'");
  panel.liveStatusReset(null);
  assert.equal(panel._thinking.size, 0, "a new socket drops them all");
});

test("the overlay text is the only thing the clock writes", () => {
  assert.match(VIEW, /if \(lab\.textContent !== text\) \{ lab\.textContent = text; lab\.dataset\.label = text; \}/);
  assert.doesNotMatch(VIEW.slice(VIEW.indexOf("  liveStatus(m) {"), VIEW.indexOf("  _positionWorkLabel(")), /this\.draw\(\)/,
    "the live-status methods never draw");
});
