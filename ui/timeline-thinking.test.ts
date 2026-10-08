// The Sessions pane's live thinking label (2026-10-08): a lane's working chip reads "Thinking · 14s" while the kernel's
// liveStatus frame names a stretch, counted by the view (romp-timeline-view.js liveStatus / _tickThinking). The view loads
// standalone (Obsidian, VS Code, the browser page), so its duration format is a twin of the chat's durLabel: pinned equal
// here, value for value. Synthetic data only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { durLabel } from "./webview/duration";
import { thinkingShort } from "./webview/live-status";

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const VIEW = fs.readFileSync(viewPath, "utf8");
const tl = createRequire(__filename)(viewPath);

test("tlDur is the chat's durLabel, value for value", () => {
  for (const s of [0, 1, 9, 10, 59, 60, 61, 119, 3599, 3600, 3661, 86399, 86400, 90061, -5, 12.7]) {
    assert.equal(tl.tlDur(s), durLabel(s), "at " + s + "s");
  }
});

test("tlThinkingLabel is live-status.ts thinkingShort's twin", () => {
  for (const [th, now] of [[{ t: 1000 }, 1014], [{ t: 1000 }, 1100], [{ t: 1000, quiet: 1065 }, 1080], [{ t: 1000 }, 4700]] as const) {
    assert.equal(tl.tlThinkingLabel(th, now), thinkingShort(th as any, now));
  }
});

test("the chip is laid out for a two-digit count, so it is resized once per minute's shape, not every second", () => {
  assert.equal(tl.tlThinkReserve("Thinking · 9s"), "Thinking · 00s");
  assert.equal(tl.tlThinkReserve("Thinking · 14s"), "Thinking · 00s", "9s and 14s share one width");
  assert.equal(tl.tlThinkReserve("Thinking · 1m 5s"), "Thinking · 0m 00s");
  assert.equal(tl.tlThinkReserve("Thinking · 1m 59s"), "Thinking · 0m 00s");
  assert.equal(tl.tlThinkReserve("Thinking · quiet 7s"), "Thinking · quiet 00s");
});

test("liveStatus keeps the stretch per lane and lays the lanes out only when a working lane starts or stops thinking", () => {
  const self: any = {
    _thinking: new Map(), _thinkTimer: null, _workLabels: new Map(), _thinkReserve: new Map(),
    data: { sessions: [{ id: "api", state: "working" }, { id: "web", state: "ready" }] },
    draws: 0, ticks: 0,
    _liveRelayout() { this.draws++; }, _tickThinking() { this.ticks++; },
  };
  const live = tl.TimelinePanel.prototype.liveStatus;
  const savedSet = (globalThis as any).setInterval, savedClear = (globalThis as any).clearInterval;
  let armed = 0, cleared = 0;
  (globalThis as any).setInterval = () => { armed++; return 7; };
  (globalThis as any).clearInterval = () => { cleared++; };
  try {
    live.call(self, { id: "api", thinking: { t: 1000 } });
    assert.deepEqual(self._thinking.get("api"), { t: 1000, quiet: 0 });
    assert.equal(self.draws, 1, "a working lane began thinking: one layout for its chip");
    assert.equal(armed, 1, "the 1 s clock runs while a lane thinks");
    live.call(self, { id: "api", thinking: { t: 1000, quiet: 1050 } });
    assert.equal(self.draws, 1, "the quiet stamp is a text change the tick makes in place");
    assert.equal(self.ticks, 1);
    live.call(self, { id: "web", thinking: { t: 1000 } });
    assert.equal(self.draws, 1, "a lane not drawn as working gets no layout for it");
    live.call(self, { id: "web", thinking: null });
    live.call(self, { id: "api", thinking: null });
    assert.equal(self._thinking.size, 0);
    assert.equal(self.draws, 2, "the working lane stopped thinking: one layout to put Working back");
    assert.equal(cleared, 1, "no lane thinks: the clock stops");
  } finally {
    (globalThis as any).setInterval = savedSet; (globalThis as any).clearInterval = savedClear;
  }
});

test("liveStatusReset drops every stretch on a new socket, or one host's on its return", () => {
  const self: any = { _thinking: new Map([["api", { t: 1 }], ["far:web", { t: 2 }], ["far:tests", { t: 3 }]]), _thinkTimer: 5, draws: 0,
                      _liveRelayout() { this.draws++; } };
  const savedClear = (globalThis as any).clearInterval; let cleared = 0;
  (globalThis as any).clearInterval = () => { cleared++; };
  try {
    tl.TimelinePanel.prototype.liveStatusReset.call(self, ["far"]);
    assert.deepEqual([...self._thinking.keys()], ["api"], "a returning host drops only its own lanes'");
    assert.equal(self.draws, 1);
    tl.TimelinePanel.prototype.liveStatusReset.call(self, null);
    assert.equal(self._thinking.size, 0, "a new socket drops them all");
    assert.equal(cleared, 1, "no lane thinks: the clock stops");
    tl.TimelinePanel.prototype.liveStatusReset.call(self, null);
    assert.equal(self.draws, 2, "nothing dropped, nothing laid out");
  } finally { (globalThis as any).clearInterval = savedClear; }
});

test("draw() sizes a thinking lane's chip for its reserve and hands the overlay the live label", () => {
  assert.match(VIEW, /const label = tlThinkingLabel\(th, Date\.now\(\) \/ 1000\);\s*\n\s*return Object\.assign\(\{\}, b, \{ label, reserve: tlThinkReserve\(label\) \}\);/);
  assert.match(VIEW, /const maxChip = Math\.max\(0, \.\.\.visB\.map\(\(b\) => \(b \? this\.badgeWidth\(b\.reserve \|\| b\.label\) \+ 12 : 0\)\)\);/);
  assert.match(VIEW, /w = Math\.ceil\(this\.badgeWidth\(bdg\.reserve \|\| bdg\.label\)\) \+ padX \* 2/);
  assert.match(VIEW, /if \(lab\.textContent !== text\) \{ lab\.textContent = text; lab\.dataset\.label = text; \}/,
    "the tick rewrites the persistent overlay div's text (and its teal twin's), never the SVG");
});
