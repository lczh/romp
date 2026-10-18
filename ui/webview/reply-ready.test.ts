// Replies ready (the user 2026-09-08): a reply lands on a comment you scrolled away from and you forget to
// come back. Two chips counted the unread replies above and below the viewport until 2026-09-24, when they
// folded into the jump cluster's badge, which counts every one of them wherever it sits since 2026-10-15
// (jump-nav.ts unreadCount, jump-nav.test.ts, jump-cluster.test.ts). This file drives the pure half
// (reply-ready.ts) behaviorally: the predicate, a windowed-out turn's placement, the phrase. Synthetic text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { isReplyReady, placeWindowed, replyWord, WINDOWED_OUT } from "./reply-ready";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

// ── the predicate ─────────────────────────────────────────────────────────────────────────────────
test("a reply is ready when the kernel's unread bit sits on an OPEN thread — the mark's own .unread rule", () => {
  assert.equal(isReplyReady({ unread: true, status: "open" }), true);
  assert.equal(isReplyReady({ unread: false, status: "open" }), false, "read");
  assert.equal(isReplyReady({ unread: true, status: "resolved" }), false, "a resolved thread's stale bit waits on nobody");
  assert.equal(isReplyReady({ unread: true, status: "merged" }), false);
  assert.equal(isReplyReady({ unread: true, status: "promoted" }), false);
  // the same expression the mark and the rail tick read, so the count can never count a mark that shows no ring
  assert.match(RENDER, /m\.classList\.toggle\("unread", !!th\.unread && th\.status === "open"\);/);
  assert.match(RENDER, /\(th\.unread && th\.status === "open" \? " unread" : ""\)/);
});

// ── placement of a windowed-out turn ────────────────────────────────────────────────────────────────
test("a windowed-out anchor takes its direction from event order and a distance tier past every pixel", () => {
  // the rendered window spans units [10, 30)
  assert.deepEqual(placeWindowed(4, 10, 30), { dir: "above", dist: WINDOWED_OUT + 6 });
  assert.deepEqual(placeWindowed(9, 10, 30), { dir: "above", dist: WINDOWED_OUT + 1 }, "just before the window");
  assert.deepEqual(placeWindowed(30, 10, 30), { dir: "below", dist: WINDOWED_OUT + 1 }, "the first unit after the window");
  assert.deepEqual(placeWindowed(45, 10, 30), { dir: "below", dist: WINDOWED_OUT + 16 });
  assert.deepEqual(placeWindowed(-1, 10, 30), { dir: "above", dist: WINDOWED_OUT * 2 }, "older than the resident head: above, farthest");
  assert.deepEqual(placeWindowed(15, 10, 30), { dir: "in", dist: 0 }, "inside the window with no turn of its own (a folded run): on screen in folded form");
  assert.ok(WINDOWED_OUT > 1e6, "no screen is a million pixels tall — every rendered mark is nearer than a windowed-out one");
});

// ── copy ──────────────────────────────────────────────────────────────────────────────────────────
test("the phrase is the user's words, singular handled — 'reply', never 'comment' (the jump cluster's badge tip reads it)", () => {
  assert.equal(replyWord(1), "1 reply unread");
  assert.equal(replyWord(2), "2 replies unread");
  assert.doesNotMatch(replyWord(1) + replyWord(2), /comment|thread|card|board/, "no romp nouns, and the unread thing is the ANSWER to their comment");
});

// The render.ts wiring moved with the chips into the jump cluster (2026-09-24): jump-cluster.test.ts pins the badge's
// count (every unread thread, jump-nav.ts unreadCount), its landing and the unread semantics.
