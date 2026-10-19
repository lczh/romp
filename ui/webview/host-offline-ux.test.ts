// What a disconnected host looks like from inside the chat (the user 2026-07-30, watching a remote flap).
//
// Three changes, one complaint: the only cue was a strikethrough on the "host:" token, which said the
// wrong thing in the wrong place and said nothing at all about the message you were about to lose.
//
//   1. Strikethrough reads as CANCELLED. The session is fine; the LINK is down and what is on screen is
//      the last state romp got. That is "stale", and romp already has a treatment for stale.
//   2. The tab mark is peripheral once you are reading. The transcript itself should say why it ends.
//   3. Nothing stopped a send into a session whose kernel is unreachable.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const HOSTPFX = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "host-prefix.ts"), "utf8");

test("an unreachable host reads as STALE, not as struck out", () => {
  assert.doesNotMatch(CSS, /\.host-prefix\.off \{[^}]*line-through/,
    "strikethrough claims the session is gone; it is the link that is down");
  assert.match(CSS, /\.host-prefix\.off \{[\s\S]*?text-decoration: underline dotted 1px/);
  assert.match(CSS, /\.host-prefix\.off \{[\s\S]*?font-style: italic/);
});

test("it is the SAME stale vocabulary the network panel already uses", () => {
  // one language for "remembered, not live" across surfaces — .rnet-stale is the kernel-side twin
  const rule = CSS.slice(CSS.indexOf(".host-prefix.off"));
  assert.match(rule, /text-underline-offset: 2px/);
});

test("the mark still goes on the host token, never the session name", () => {
  // a struck WHOLE name already means a dead session; the two claims must stay distinguishable
  assert.match(HOSTPFX, /h\.className = off \? "host-prefix off" : "host-prefix";/);
  assert.match(HOSTPFX, /the STALE treatment, not strikethrough/);
});

test("the transcript says why it ends, at the END, where the eye lands", () => {
  assert.match(RENDER, /function syncHostOfflineFoot\(keepPlace = true\): void \{/);
  assert.match(RENDER, /is disconnected — this is the last romp got from it\. Reconnecting\./);
  // 2026-09-08 (the notice-vocabulary pass): a slim SESSION notice in the warn severity — the one row that sat
  // outside the vocabulary (centred italic); .notice-foot lays it out like a thread (below)
  assert.match(RENDER, /foot\.id = "host-offline-foot";/);
  // `live` since 2026-09-10: the row is live while a dial to the host is in flight, and the romp swirl sits after
  // the gist as its flex sibling (host-dial-live.test.ts pins that rule) — the wording itself unchanged
  assert.match(RENDER, /notice\(\{ src: "session", glyph: "api", sev: "warn", gist: text, nested: true, live: dialing,/);
  assert.match(CSS, /\.notice-foot \{/);
  assert.doesNotMatch(CSS, /\.tx-hostoff/);
});

test("the foot keeps the thread's column and gutter, so the go-to-bottom chip beside it never covers its first letters", () => {
  // a sibling of the threads, not a row inside one, so it reserves the gutter itself (2026-10-18: it started at the pane's
  // 24px, and the chip, whose slot it sits beside, covered its glyph whenever the reader was a few lines up; the served lab,
  // tests/test_jump_cluster_served.py ServedHostOfflineFoot, measures it)
  const foot = CSS.match(/\n\.notice-foot \{([^}]*)\}/)?.[1] || "";
  assert.match(foot, /max-width: var\(--chat-col\); margin: 0 auto;/, "the thread's column");
  assert.ok(foot.includes("padding: 6px 24px 10px var(--thread-gutter);"), `the thread's gutter: ${foot}`);
});

test("the foot is a SIBLING of the view, never a child of it", () => {
  // syncView counts v.el's children to track what it has rendered — a foot inside would read as transcript
  assert.match(RENDER, /content\.appendChild\(foot\);/);
  assert.match(RENDER, /A sibling of the view element, never a child/);
});

test("it is not a top banner — one covered the tab strip and was removed", () => {
  assert.match(RENDER, /Deliberately NOT a top banner/);
  assert.doesNotMatch(RENDER, /id = "rhostoff"/);
});

test("it repaints on both render paths, before the scroll maths", () => {
  assert.match(RENDER, /syncView\(activeId, stick\);\s*\n\s*syncHostOfflineFoot\(false\);/);
  assert.match(RENDER, /syncHostOfflineFoot\(\);\s*\/\/ the tab we just switched to/);
});

test("a reader following the tail stays at the bottom as the foot comes or goes, and the chip re-reads (the review of #308)", () => {
  // outside appendActive (the host dropping mid-read, a dial, a re-show) the browser kept scrollTop as the end moved: a reader at
  // the bottom was left the foot's height above it with the notice under the pane's edge, and nothing re-read the go-to-bottom chip
  // (tests/test_jump_cluster_served.py ServedHostOfflineFoot measures it in a browser)
  const body = RENDER.split("\nfunction footChange(")[1].split("\n}\n")[0];
  assert.match(body, /if \(!keepPlace \|\| !v \|\| v\.el\.style\.display === "none" \|\| content\.clientHeight <= 0\) \{ change\(\); return; \}/,
    "only the view on screen has a place to keep; appendActive's own maths, and a switch's landing, decide theirs");
  assert.match(body, /const stick = followingTail\(content, v\);\s*\n\s*const before = content\.scrollTop;\s*\n\s*change\(\);/, "the append's following read, BEFORE the change");
  assert.match(body, /if \(stick\) writeScroll\(content, content\.scrollHeight, "host-foot", true, before\);\s*\n\s*else writeScroll\(content, before, "host-foot", false, before\);\s*\n\s*updateJumpBtn\(\);/);
  assert.match(RENDER, /footChange\(content, keepPlace, \(\) => \{ existing\?\.remove\(\); content\.appendChild\(foot\); \}\);/, "the foot comes up (or is rebuilt) through it");
});

test("it clears itself the moment the host is back", () => {
  assert.match(RENDER, /if \(!activeId \|\| !hostIsDown\(activeId\)\) \{ if \(existing\) footChange\(content, keepPlace, \(\) => existing\.remove\(\)\); return; \}/);
});

test("a send into a disconnected session is refused, and the text is KEPT", () => {
  assert.match(RENDER, /if \(hostIsDown\(sid\)\) \{/);
  assert.match(RENDER, /is disconnected, so this wasn't sent\. It's still in the box/);
  // the refusal itself is DEMAND (the user 2026-08-16, on flaky wifi): it asks the kernel to
  // re-dial the host's tunnel NOW, so the toast's "re-dialing the link now" is literally true
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "redial", host \}\);/);
  assert.match(RENDER, /re-dialing the link now; send again when it's back\./);
  // the refusal must come BEFORE the box is cleared, or the message is gone
  const deliver = RENDER.slice(RENDER.indexOf("const deliver = () =>"));
  const guard = deliver.indexOf("if (hostIsDown(sid)) {");
  const clear = deliver.indexOf('ta.value = "";');
  assert.ok(guard >= 0 && clear >= 0 && guard < clear,
    "clearing the composer and delivering nothing is the one outcome to rule out");
});
