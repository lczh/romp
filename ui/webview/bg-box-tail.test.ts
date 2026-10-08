// The background box's rows follow the backend's live task set on every frame (2026-10-08). The rows read the session's
// `bgTasks` (the tracked tasks: their status and Stop handle) beside the status's awaitingItems; the status rode every
// chatTail delta while bgTasks rode only the full session frame, so a caught-up page kept the set it was last handed whole:
// three background agents that had finished half an hour earlier still wore the running dot and Stop under a header that
// counted the one still working. The kernel now puts bgTasks on a delta whenever the set moved (kernel _chat_bgt_digest,
// executed in tests/test_bg_box_tail.py); this pins the page's half: the tail's set replaces the held one, a tail without
// it keeps it, and the box repaints from that frame. Source pins (no jsdom for the chat renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const tail = RENDER.split("function chatTail(msg: any) {")[1].split("\n}")[0];

test("a tail's task set replaces the held one, and a tail without one keeps it", () => {
  assert.match(tail, /const bgMoved = "bgTasks" in msg;\s*\n\s*if \(bgMoved\) s\.bgTasks = msg\.bgTasks;/,
    "the key's presence is the kernel's word that the set moved (it sends it only then)");
  assert.doesNotMatch(tail, /s\.bgTasks = msg\.bgTasks \|\|/, "an absent key never clears the held set");
});

test("the box repaints from the frame that carried the new set, not only on a status change", () => {
  assert.match(tail, /if \(awaitKey\(s\.status\) !== before \|\| bgMoved\) awaitChanged\(msg\.id\);/);
  assert.match(RENDER, /function awaitChanged\(sid: string\): void \{\s*\n\s*if \(sid === activeId\) renderBgTasks\(\);/);
});

test("the box's rows read that set: a tracked task offers Stop only while it runs", () => {
  assert.match(RENDER, /const tasks: BgTask\[\] = \(s && s\.bgTasks && s\.bgTasks\.tasks\) \|\| \[\];/);
  assert.match(RENDER, /stopId: status === "running" \? t\.id : null,/, "a finished tracked task wears no Stop");
});

test("the kernel's deltas carry the set when it moved, on both wires", () => {
  const n = (KERNEL.match(/if _chat_bgt_rides\(c, sid, m\):[^\n]*\n\s*tail\["bgTasks"\] = m\.get\("bgTasks"\)/g) || []).length;
  assert.equal(n, 2, "the uuid wire's delta and the index wire's");
  assert.match(KERNEL, /"bgt": _chat_bgt_digest\(m\)\}/, "the view record keeps the set the page holds");
});
