// A comment thread on a passage from BEFORE the session's last compaction (the user 2026-09-30): the thread is a
// tip fork whose opening message carries, beyond the quote and the comment, the turns around the passage and where
// the full transcript is saved. That text goes out on the user's behalf, so the popover says so under the opening
// message: one dim line, collapsed by default, and the fold shows the words exactly as sent. The kernel ships them
// from the thread row (priorContext), so a reload shows the note again. Source pins for the renderer (the repo
// convention: no jsdom harness); the executed guard is tests/test_comment_prior_note_served.py. Synthetic text only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CMT_PRIOR_GIST, type CommentThread } from "./comments";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const RENDER = read("ui", "webview", "render.ts");
const CSS = read("ui", "webview", "styles.css");
const KERNEL = read("kernel", "kernel.py");
const NOTE = RENDER.split("function cmtPriorNote(th: CommentThread): HTMLElement | null {")[1]?.split("\nfunction ")[0] || "";
const FILL = RENDER.split("function fillCommentMsgs(list: HTMLElement, th: CommentThread, sid: string): void {")[1]?.split("\nfunction ")[0] || "";

test("the thread type carries the verbatim context, optional: an ordinary thread has none", () => {
  const plain: CommentThread = { tid: "t1", anchorUuid: "a1", exact: "the passage", status: "open", createdT: 0,
                                 state: "", unread: false, promotedName: "", msgs: [] };
  assert.equal(plain.priorContext, undefined);
  const pre: CommentThread = { ...plain, priorContext: "This is from before our conversation was summarized." };
  assert.ok(pre.priorContext);
});

test("the note is THE notice, nested, with the words as sent in a plain-text body (never markdown)", () => {
  assert.ok(NOTE.length > 50, "cmtPriorNote is defined");
  assert.match(NOTE, /if \(!th\.priorContext\) return null;/, "an ordinary thread renders no note");
  assert.match(NOTE, /const body = el\("div", "notice-prose cmt-prior-text"\);\s*\n\s*body\.textContent = th\.priorContext;/,
               "verbatim: textContent in the line-keeping plain body, so the quote marks and the path read exactly as sent");
  assert.doesNotMatch(NOTE, /innerHTML|md\(/, "not rendered as markdown: that would restyle what was sent");
  assert.match(NOTE, /notice\(\{ src: "romp", glyph: "romp", sev: "romp", gist: CMT_PRIOR_GIST, body, key: "cmtprior:" \+ th\.tid,\s*\n\s*nested: true, cls: "cmt-prior" \}\)/);
});

test("collapsed by default: the fold is keyed per thread and never seeded open", () => {
  assert.doesNotMatch(NOTE, /open: true/, "the one line is the default; the words are one click deeper");
  // the keyed fold lives in openFolds through the shared notice builder, toggled by the body delegate: a refill (every
  // comments frame) keeps whichever state the user left it in
  assert.match(RENDER, /const fkey = spec\.key \? "notice:" \+ spec\.key : undefined;/);
  assert.match(RENDER, /applyFold\(card, "notice-open", fkey\);/);
});

test("the note sits under the opening message: inside its turn on the events arm, after the first bubble on the msgs arm", () => {
  assert.match(FILL, /const prior = quoteHost \? cmtPriorNote\(th\) : null;\s*\/\/ under the opening message, inside its turn\s*\n\s*if \(prior\) quoteHost!\.appendChild\(prior\);/);
  assert.match(FILL, /const prior = !priorPlaced && m\.who === "you" \? cmtPriorNote\(th\) : null;/);
  assert.equal((FILL.match(/cmtPriorNote\(th\)/g) || []).length, 2, "both arms, nowhere else");
});

test("the one line says what was sent and why, in the user's terms", () => {
  assert.match(CMT_PRIOR_GIST, /^From before the summary/);
  assert.match(CMT_PRIOR_GIST, /also sent the turns around it/);
  assert.ok(CMT_PRIOR_GIST.length <= 100, "one line in the popover's default width: " + CMT_PRIOR_GIST.length);
  assert.match(CMT_PRIOR_GIST, /where the full conversation is saved/);
  assert.doesNotMatch(CMT_PRIOR_GIST, /compaction|transcript|jsonl|uuid/i, "no mechanics in the glance");
  assert.match(CSS, /\.cmt-prior \.notice-gist \{ color: var\(--dim\); white-space: normal; \}/, "dim, and it wraps rather than cuts off");
});

test("the kernel ships the row's words, and only for such a thread", () => {
  assert.match(KERNEL, /\*\*\(\{"priorContext": str\(th\.get\("priorContext"\)\)\} if th\.get\("priorContext"\) else \{\}\),/);
  assert.match(KERNEL, /row\["priorContext"\] = prior/);
});
