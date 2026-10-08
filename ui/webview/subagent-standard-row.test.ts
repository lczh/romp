// The fast menu's "Subagents at standard speed (experimental)" row (2026-10-08): one session's main loop keeps fast mode
// while every subagent it spawns runs Opus 5.5 at standard speed. The row sits under the Fast/Slow choices, is offered where
// the switch can apply (the session's model is the one it needs) or while it is on, wears the ✓ when on, and its sub-line
// is the kernel's speed check (usage.speed read off the transcripts) or what holds the switch back. A click posts
// setSubagentStandard; the kernel refuses with the reason and applies a change at the next relaunch.
// render.ts has no jsdom harness → source pins (kernel pins ride along, as in the other meta tests).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDK = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the status carries the switch, where it is offered, and its line", () => {
  assert.match(RENDER, /subagentStandard\?: boolean; subagentStandardOffered\?: boolean; subagentSpeed\?: string;/);
  assert.match(KERNEL, /"subagentSpeed": tm\.get\("subagentSpeed", ""\)/);
  assert.match(SDK, /"subagentSpeed": self\.subagent_speed_line\(\)/);
});

test("the row lives in the fast menu only, for an SDK session that offers it, never in a thread's popover", () => {
  assert.match(RENDER, /if \(kind === "fast" && !forThread && s\.status\.backend === "sdk" && s\.status\.subagentStandardOffered\)/);
  assert.match(RENDER, /head\.textContent = "Subagents at standard speed \(experimental\)";/);
  assert.match(RENDER, /"meta-item meta-substd" \+ \(on \? " current" : ""\)/);   // the menu vocabulary's ✓ says on
});

test("the sub-line is the kernel's speed check while on, and says what it does while off", () => {
  assert.match(RENDER, /on \? \(s\.status\.subagentSpeed \|\| "applies at the next relaunch"\)/);
  assert.match(RENDER, /"main loop stays fast; subagents run Opus 5\.5 at the standard rate"/);
});

test("a click or Enter flips the switch through the kernel's op and closes the menu", () => {
  assert.match(RENDER, /postMessage\(\{ type: "setSubagentStandard", id: opSid, value: on \? "off" : "on" \}\)/);
  assert.match(KERNEL, /elif t == "setSubagentStandard" and msg\.get\("value"\) in \("on", "off"\):/);
  assert.match(KERNEL, /"Couldn't turn on Subagents at standard speed: %s\."/);   // the refusal names its reason
});

test("a switch change waiting for its relaunch shows outside the menu: the fast badge's dots and the fast menu's Apply now", () => {
  // the kernel folds it into the status the fast badge and Apply now already read (2026-10-08 review)
  assert.match(SDK, /"fastPending": bool\(getattr\(self, "_fast_pending", ""\)\) or self\._sub_std_pending\(\)/);
  assert.match(RENDER, /const waitingPick: WaitingPick \| null = kind === "effort" \? "effort" : kind === "fast" \? "fast" : null;/);
  assert.match(SDK, /"subagentStandardOffered": self\.subagent_standard_offered\(\)/);   // not offered on Slow unless on
});

test("a failed or unfinished check of the workaround offers Check again, which re-runs it without turning the switch off", () => {
  assert.match(RENDER, /subagentStandardRetry\?: boolean;/);
  assert.match(RENDER, /if \(on && s\.status\.subagentStandardRetry\)/);
  assert.match(RENDER, /ahead\.textContent = "Check again";/);
  assert.match(RENDER, /postMessage\(\{ type: "setSubagentStandard", id: opSid, value: "on" \}\)/);
  assert.match(KERNEL, /"subagentStandardRetry": bool\(tm\.get\("subagentStandardRetry"\)\)/);
  assert.match(SDK, /"subagentStandardRetry": self\.subagent_standard_retry\(\)/);
});
