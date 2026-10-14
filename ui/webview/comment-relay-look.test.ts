// A RELAYED comment thread looks open; only a RESOLVED one dims, and dimmed still shows (the user 2026-09-30, who pressed
// Relay on a thread and watched its comment stop showing on the transcript). Relay sends the discussion back into the
// session and leaves the thread open for more talk (T145), but the page lumped `merged` with `resolved`: the mark took
// the resolved wash, a white 8% that the light theme's cream page swallows whole, the rail tick faded to 30%, and a
// thread in the relay's transient `merging` status dropped off the rail. The status → look mapping is ONE pair of
// predicates (comments.ts threadSettled / threadOnRail), executed here, and every site that paints a thread reads them.
// The painted result is measured on the served page by tests/test_comment_relay_mark_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { threadSettled, threadOnRail, threadNeedsYou, threadCued, type CommentThread } from "./comments";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const STATUSES: CommentThread["status"][] = ["open", "resolved", "promoting", "promoted", "merging", "merged"];

test("only a RESOLVED thread wears the dimmed look; a relayed one (merging, merged) looks open", () => {
  assert.deepEqual(STATUSES.filter(threadSettled), ["resolved"]);
  assert.equal(threadSettled("merged"), false, "relayed is still an open conversation");
  assert.equal(threadSettled("merging"), false, "…and so is the relay on its way there");
});

test("the rail keeps open, resolved and relayed threads, merging included; a promoted thread's talk lives in its session", () => {
  assert.deepEqual(STATUSES.filter(threadOnRail), ["open", "resolved", "merging", "merged"]);
});

// the mark's classes and tooltip, executed from render.ts's own styleCommentMark over a minimal element shim
function styled(status: CommentThread["status"]): { classes: Set<string>; title: string } {
  const src = "function styleCommentMark(m, th) {" + RENDER.split("function styleCommentMark(m: HTMLElement, th: CommentThread): void {")[1].split("\n}")[0] + "\n}";
  const classes = new Set<string>();
  const m = { classList: { toggle(c: string, on: boolean) { if (on) classes.add(c); else classes.delete(c); } }, title: "" };
  const th = { tid: "t1", anchorUuid: "a1", exact: "the passage", status, createdT: 0, state: "", unread: false,
               promotedName: "", msgs: [] } as CommentThread;
  new Function("m", "th", "threadSettled", "commentInFlight", "threadNeedsYou", "threadCued", src + "\nstyleCommentMark(m, th);")(m, th, threadSettled, () => false, threadNeedsYou, threadCued);
  return { classes, title: m.title };
}

test("styleCommentMark: the resolved class for a resolved thread only; a relayed mark keeps the open look and says it was relayed", () => {
  for (const st of STATUSES) assert.equal(styled(st).classes.has("resolved"), st === "resolved", st);
  assert.match(styled("merged").title, /^relayed thread/, "the tooltip still tells the relay apart");
  assert.match(styled("resolved").title, /^resolved thread/);
});

test("every site that paints a thread reads the two predicates, and none lumps merged with resolved", () => {
  const style = RENDER.split("function styleCommentMark(")[1].split("\n}")[0];
  assert.match(style, /m\.classList\.toggle\("resolved", threadSettled\(th\.status\)\);/);
  const ensure = RENDER.slice(RENDER.indexOf("function ensureCommentMark("), RENDER.indexOf("function styleCommentMark("));
  assert.match(ensure, /host\.classList\.toggle\("cmt-hl-host", !threadSettled\(th\.status\)\);/, "the inline-code host tint");
  assert.match(ensure, /kat\.classList\.toggle\("cmt-hl-host", !threadSettled\(th\.status\)\);/, "the KaTeX host tint");
  const rail = RENDER.slice(RENDER.indexOf("function updateCommentRail(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function updateCommentRail(): void {")));
  assert.match(rail, /\.filter\(\(t\) => threadOnRail\(t\.status\)\);/, "the rail's set");
  assert.match(rail, /"cmt-tick" \+ \(threadSettled\(th\.status\) \? " resolved" : ""\)/, "the tick's fade");
  const nav = RENDER.slice(RENDER.indexOf("function navThreads("), RENDER.indexOf("\n}\n", RENDER.indexOf("function navThreads(")));
  assert.match(nav, /\.filter\(\(t\) => threadOnRail\(t\.status\)\)/, "the comment jumps step through the rail's set");
  assert.match(RENDER, /const threadsHere = threads\.some\(\(t\) => threadOnRail\(t\.status\)\);/);
  assert.doesNotMatch(RENDER, /status === "resolved" \|\| t?h?\.?status === "merged"|status !== "resolved" && th\.status !== "merged"/,
    "no site paints a relayed thread as a resolved one");
});

test("the resolved wash is a theme token, and no comment mark, tick or popover ground paints a white wash", () => {
  assert.match(CSS, /^mark\.cmt-hl\.resolved \{ background: var\(--cmt-hl-resolved\); \}$/m);
  const rules = (CSS.match(/^(?:mark\.cmt-hl|\.cmt-tick|\.cmt-quote|\.cmt-msg)[^{]*\{[^}]*\}/gms) || []);
  assert.ok(rules.length >= 14, "the mark, tick and popover rules are found: " + rules.length);
  for (const r of rules) assert.doesNotMatch(r, /rgba\(255, 255, 255/, "a white wash vanishes on the light theme's page: " + r.split(" {")[0]);
  // the popover's quote bar is a LINE: the comment's line ink (amber on cream, where the fill's yellow reads 1.1:1)
  assert.match(CSS, /^\.cmt-quote \{\s*\n\s*padding: 4px 8px; border-left: 2px solid var\(--cmt-hl-outline\);/m);
});
