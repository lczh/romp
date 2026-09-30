// What romp sent with a comment (the user 2026-09-30, one rule for every thread: they can always see exactly what went
// out on their behalf). A thread's opening message wraps the user's words in text written for the thread's agent: the
// split-off line and the quote, and on a passage from before the session's last compaction also the turns around it,
// where the transcript is saved and the ask's label. The popover says so under the opening message: one dim line,
// collapsed by default, and the fold shows those words exactly as sent. The kernel ships them from the thread row, or
// reads them back from the stored opening message of a thread made before rows kept them, so a reload shows the note
// again. Source pins for the renderer (the repo convention: no jsdom harness); the executed guard is
// tests/test_comment_sent_note_served.py. Synthetic text only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CMT_SENT_GIST, CMT_SENT_GIST_PRIOR, type CommentThread } from "./comments";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const RENDER = read("ui", "webview", "render.ts");
const CSS = read("ui", "webview", "styles.css");
const KERNEL = read("kernel", "kernel.py");
const NOTE = RENDER.split("function cmtSentNote(th: CommentThread): HTMLElement | null {")[1]?.split("\nfunction ")[0] || "";
const FILL = RENDER.split("function fillCommentMsgs(list: HTMLElement, th: CommentThread, sid: string): void {")[1]?.split("\nfunction ")[0] || "";

test("the thread type carries what romp sent, verbatim and optional: a thread romp added nothing to has none", () => {
  const plain: CommentThread = { tid: "t1", anchorUuid: "a1", exact: "the passage", status: "open", createdT: 0,
                                 state: "", unread: false, promotedName: "", msgs: [] };
  assert.equal(plain.sent, undefined);
  const pre: CommentThread = { ...plain, sent: "About this part of the conversation:\n\n> the passage", sentBeforeSummary: true };
  assert.ok(pre.sent && pre.sentBeforeSummary);
});

test("the note is THE notice, nested, with the words as sent in a plain-text body (never markdown)", () => {
  assert.ok(NOTE.length > 50, "cmtSentNote is defined");
  assert.match(NOTE, /if \(!th\.sent\) return null;/, "no note when romp added nothing");
  assert.match(NOTE, /const body = el\("div", "notice-prose cmt-sent-text"\);\s*\n\s*body\.textContent = th\.sent;/,
               "verbatim: textContent in the line-keeping plain body, so the quote marks and the path read exactly as sent");
  assert.doesNotMatch(NOTE, /innerHTML|md\(/, "not rendered as markdown: that would restyle what was sent");
  assert.match(NOTE, /notice\(\{ src: "romp", glyph: "romp", sev: "romp", gist: th\.sentBeforeSummary \? CMT_SENT_GIST_PRIOR : CMT_SENT_GIST,\s*\n\s*body, key: "cmtsent:" \+ th\.tid, nested: true, cls: "cmt-sent" \}\)/);
});

test("collapsed by default: the fold is keyed per thread and never seeded open", () => {
  assert.doesNotMatch(NOTE, /open: true/, "the one line is the default; the words are one click deeper");
  // the keyed fold lives in openFolds through the shared notice builder, toggled by the body delegate: a refill (every
  // comments frame) keeps whichever state the user left it in
  assert.match(RENDER, /const fkey = spec\.key \? "notice:" \+ spec\.key : undefined;/);
  assert.match(RENDER, /applyFold\(card, "notice-open", fkey\);/);
});

test("the note sits under the opening message: inside its turn on the events arm, after the first bubble on the msgs arm", () => {
  assert.match(FILL, /const sentNote = quoteHost \? cmtSentNote\(th\) : null;\s*\/\/ under the opening message, inside its turn\s*\n\s*if \(sentNote\) quoteHost!\.appendChild\(sentNote\);/);
  assert.match(FILL, /const sentNote = !sentPlaced && m\.who === "you" \? cmtSentNote\(th\) : null;/);
  assert.equal((FILL.match(/cmtSentNote\(th\)/g) || []).length, 2, "both arms, nowhere else");
});

test("the lines the user chose, dim, and wrapping rather than cut off", () => {
  assert.equal(CMT_SENT_GIST, "What romp sent with your comment");
  assert.equal(CMT_SENT_GIST_PRIOR, "What romp sent with your comment, including the turns around it (from before the summary)");
  assert.match(CSS, /\.cmt-sent \.notice-gist \{ color: var\(--dim\); white-space: normal; \}/);
});

test("the kernel ships it for every thread: the row's copy, else read back from the stored opening message", () => {
  assert.match(KERNEL, /\*\*_thread_sent_fields\(th, tsid, status\),/);
  assert.match(KERNEL, /sent = str\(th\.get\("sent"\) or ""\) or _thread_opener_sent\(tsid\)/);
  assert.match(KERNEL, /row\["sent"\] = sent/);
});
