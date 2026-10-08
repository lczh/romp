// Live status (2026-10-08): the pure half of the four indicators, EXECUTED (live-status.ts), plus source pins on the
// DOM half in render.ts, styles.css and the kernel, which no jsdom here can run. Synthetic data only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { readLiveStatus, thinkingText, thinkingShort, thinkingTip, thoughtLabel, taskProgressText, turnThoughtTokens, tokensShort } from "./live-status";
import { durLabel } from "./duration";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");

test("a frame is read defensively: a malformed or foreign field reads as nothing live", () => {
  assert.deepEqual(readLiveStatus({ thinking: { t: 100 }, tasks: {} }), { thinking: { t: 100 }, tasks: {} });
  assert.deepEqual(readLiveStatus({ thinking: { t: 100, quiet: 140 } }).thinking, { t: 100, quiet: 140 });
  assert.equal(readLiveStatus({ thinking: null }).thinking, null);
  assert.equal(readLiveStatus({ thinking: { t: "soon" } }).thinking, null, "a non-number start is no stretch");
  assert.deepEqual(readLiveStatus({ tasks: { toolu_A: { tool: "Bash", calls: 3, tokens: 1200, junk: 1 }, toolu_B: {}, toolu_C: 7 } }).tasks,
    { toolu_A: { tool: "Bash", calls: 3, tokens: 1200 } }, "empty and non-object rows drop; unknown keys never ride");
});

test("the chat line counts from the first frame and says when the stream went quiet", () => {
  assert.equal(thinkingText({ t: 1000 }, 1014), "Thinking · 14s");
  assert.equal(thinkingText({ t: 1000 }, 1080), "Thinking · 1m 20s");
  assert.equal(thinkingText({ t: 1000, quiet: 1065 }, 1080), "Thinking · 1m 20s · quiet for 15s",
    "silence reads as silence, never as a count that only goes up");
  assert.equal(thinkingShort({ t: 1000 }, 1014), "Thinking · 14s");
  assert.equal(thinkingShort({ t: 1000, quiet: 1065 }, 1080), "Thinking · quiet 15s");
  assert.match(thinkingTip({ t: 1000, quiet: 1065 }, 1080), /thinking for 1m 20s; no thinking update has arrived for 15s/);
  assert.equal(thinkingText({ t: 1000 }, 990), "Thinking · 0s", "a clock behind the kernel's never reads negative");
});

test("a thinking row's label carries the exact per-message figure, compactly", () => {
  assert.equal(thoughtLabel(2512), "Thought · 2.5k tokens");
  assert.equal(thoughtLabel(900), "Thought · 900 tokens");
  assert.equal(thoughtLabel(18234), "Thought · 18k tokens");
  assert.equal(tokensShort(6514), "6.5k");
  assert.equal(tokensShort(795232), "795k");
});

test("a running subagent's progress reads its last tool, its calls and its tokens", () => {
  assert.equal(taskProgressText({ tool: "Edit", calls: 7, tokens: 18234 }), "Edit · 7 tool calls · 18k tokens");
  assert.equal(taskProgressText({ calls: 1 }), "1 tool call");
  assert.equal(taskProgressText({ tool: "Read", summary: "Reading the router", calls: 2 }), "Reading the router · 2 tool calls",
    "the CLI's own summary, when it sends one, leads instead of the bare tool name");
  assert.equal(taskProgressText(undefined), "");
  assert.equal(taskProgressText({ calls: 0, tokens: 0 }), "", "nothing to say is said as nothing");
  assert.equal(taskProgressText({ tool: "Edit", calls: 7, tokens: 18234 }, true), "18k tokens",
    "a row that lists the agent's steps already counts the calls and names the latest tool: only the tokens are new");
  assert.equal(taskProgressText({ tool: "Read", summary: "Reading the router", calls: 2, tokens: 900 }, true), "Reading the router · 900 tokens");
});

test("the turn's thinking total sums its rows' figures back to the turn's trigger, and never a partial sum", () => {
  const evs = [
    { kind: "user" }, { kind: "thinking", thinkTokens: 300 }, { kind: "thinking" }, { kind: "tool" },
    { kind: "thinking", thinkTokens: 120 }, { kind: "assistant" },
    { kind: "user" }, { kind: "assistant" },
  ];
  assert.equal(turnThoughtTokens(evs, 5), 420);
  assert.equal(turnThoughtTokens(evs, 7), 0, "a turn with no thinking sums to nothing (the footer says nothing more)");
  assert.equal(turnThoughtTokens(evs.slice(1), 4), null, "the trigger is outside the events held: no figure at all");
});

test("render.ts: the frame is dispatched to the in-place painters, and the 1 s timer drives the clocks", () => {
  assert.match(RENDER, /else if \(m\.type === "liveStatus"\) applyLiveStatus\(m\);/);
  assert.match(RENDER, /setInterval\(\(\) => \{ apiRetryTick\(\); retryingTick\(\); liveStatusTick\(\); \}, 1000\);/,
    "the one 1 s timer, no second scheduler");
  const apply = RENDER.slice(RENDER.indexOf("function applyLiveStatus("), RENDER.indexOf("function liveStatusEl("));
  assert.match(apply, /paintTabThinking\(id\);[\s\S]*paintLiveThinking\(\);[\s\S]*paintTaskProgress\(id\);/);
  assert.doesNotMatch(apply, /renderTabs\(|syncView|showActive\(/, "a live status repaints in place: no strip rebuild, no chat sync");
});

test("render.ts: the transcript's foot line keeps its room whether or not it shows, and threads go above it", () => {
  assert.match(RENDER, /content\?\.insertBefore\(elv, document\.getElementById\("live-status"\) \|\| document\.getElementById\("live-ask"\)\);/);
  assert.match(CSS, /#live-status \{[^}]*height: 18px;/, "a fixed height: a stretch starting or ending moves nothing on screen");
  assert.match(CSS, /\.live-thinking\.quiet \.meta-dots i \{ animation: none;/, "a silent stream stills the dots");
  // never made at load: the pane loader lifts at #content's first child other than the picker host (kernel _pane_spin),
  // so the box waits for a painted transcript, and a bottom reader stays at the bottom when it adds its room
  assert.doesNotMatch(RENDER, /\nliveStatusEl\(\);/, "no foot line at load, ahead of the transcript");
  const mk = RENDER.slice(RENDER.indexOf("function liveStatusEl("), RENDER.indexOf("function paintLiveThinking("));
  assert.match(mk, /if \(!content \|\| !v \|\| !v\.el\.children\.length\) return null;/);
  assert.match(mk, /const atEnd = atBottom\(content\);[\s\S]*content\.insertBefore\(n, document\.getElementById\("live-ask"\)\);\s*\n\s*if \(atEnd\) writeScroll\(content, content\.scrollHeight, "live-status", true\);/);
});

test("render.ts: a live status lives as long as its socket, and an unreachable host's session shows none", () => {
  assert.match(RENDER, /if \(m\.type === "wsup" \|\| m\.type === "hostUp"\) dropLiveStatus\(m\.type === "hostUp" \? \(Array\.isArray\(m\.hosts\) \? m\.hosts : \[\]\) : null\);/);
  assert.match(RENDER, /!hostIsDown\(activeId\) \? liveStatus\.get\(activeId\)\?\.thinking : null/);
  assert.match(RENDER, /const th = hostIsDown\(id\) \? undefined : liveStatus\.get\(id\)\?\.thinking;/);
});

test("render.ts + styles.css: the tab's cue is a static ring that cannot move the strip", () => {
  assert.match(CSS, /\.tab-dot\.thinking \{ outline: 1\.5px solid color-mix\(in srgb, var\(--st-working-bg\) 55%, transparent\); outline-offset: 1\.5px; \}/);
  assert.doesNotMatch(CSS, /\.tab-dot\.thinking[^{]*\{[^}]*animation/, "no pulse on the tab dot (dropped as distracting, 2026-06-16)");
  assert.match(RENDER, /if \(s\.id\) applyTabThinking\(tab, s\.id\);/, "every tab build re-applies the cue");
});

test("render.ts: a waiting tool runs only on the kernel's word, and a cut one never wears the done tick", () => {
  assert.match(RENDER, /const running = ev\.running \?\? agentRunning;/, "an unstamped event (an older kernel) keeps the old rule");
  assert.match(RENDER, /const noResult = ev\.running === false && !ev\.agentRunning && !ev\.output && !ev\.isError;/);
  assert.match(RENDER, /dot\(ev\.isError \? "ring" : \(running \|\| ev\.agentRunning\) \? "working" : noResult \? "ring" : "green"\)/);
  assert.match(RENDER, /const t = el\("span", "tool-elapsed"\); t\.dataset\.since = String\(since\);/, "the elapsed time ticks from the call's own stamp");
});

test("render.ts: thinking rows carry the exact figure, and the footer the turn's total", () => {
  assert.match(RENDER, /typeof ev\.thinkTokens === "number" && ev\.thinkTokens > 0 \? thoughtLabel\(ev\.thinkTokens\) : ""/);
  assert.match(RENDER, /f\.textContent = durLabel\(secs\) \+ \(thought \? ` · thought \$\{tokensShort\(thought\)\} tokens` : ""\);/);
  assert.match(RENDER, /const f = elapsedFooter\(secs, turnThoughtTokens\(s\.events, ev\)\);/, "the tail path's patch builds the same footer");
});

test("kernel: the frame rides the pusher for chat and Sessions-pane clients, outside every build", () => {
  assert.match(KERNEL, /_send_tab_order\(c, tab_order, tab_meta, live_map\)\n\s*_send_live_status\(c, tab_order, live_map\)/);
  assert.match(KERNEL, /for c in tl_clients:\n\s*_send_live_status\(c, live_map, live_map\)\n\s*_PERF_STATS\.stage\("push\.timeline"/,
    "the Sessions pane's frames go after its lanes and bars, never ahead of the cold-frame guard");
  assert.match(KERNEL, /else if\(m\.type==="liveStatus"&&panel\.liveStatus\)panel\.liveStatus\(m\);/, "the browser timeline's inline boot routes it");
});

test("durLabel stays the one duration format the live clocks read", () => {
  assert.equal(durLabel(14), "14s");
  assert.equal(durLabel(80), "1m 20s");
});
