// Select, quote, stage, in a comment thread (the user 2026-10-07, who wanted the message box's behaviours in comment
// threads): the staged stack keys on the THREAD's own id, never the parent session's or the chat's, and the release is
// ONE reply through the comment reply door (commentReply), its body the chat's own staged run. The rules are executed
// (comments.ts stageThreadReply / threadReply); the popover's wiring in render.ts is pinned at source; the served test
// (tests/test_composer_parity_served.py) drives the real popover. Synthetic ids: the notes-api demo's session `web` and
// a thread of its own.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { StagedStack, quoteLeadIn } from "./staged-messages";
import { stageThreadReply, threadReply } from "./comments";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const WEB = "7a1c0e52-3b9d-4f61-a2e8-5d4c3b2a1f00";      // the parent session
const THREAD = "7a1c0e52-3b9d-4f61-a2e8-5d4c3b2a1f01";   // the thread: a session with its own id
const OTHER = "7a1c0e52-3b9d-4f61-a2e8-5d4c3b2a1f02";    // another thread on the same session
const Q1 = { title: "t", quote: "The export streams rows through a bounded buffer.", uuid: "r1" };
const Q2 = { title: "t", quote: "The retry cap stays at two minutes.", uuid: "r2" };
const LEAD = "Replying to this part of the conversation:";

test("a thread quote wears the conversation's own lead-in, the approved one, no new wording", () => {
  assert.equal(quoteLeadIn(Q1), LEAD, "the thread's messages are its own conversation");
});

test("Cmd/Ctrl+Enter stages onto the THREAD's stack, keyed by its id; nothing else's stack moves", () => {
  const stack = new StagedStack();
  assert.equal(stageThreadReply(stack, THREAD, "Why bounded?", [Q1]), true);
  assert.equal(stageThreadReply(stack, THREAD, "", [Q2]), true, "a quote alone stages");
  assert.equal(stageThreadReply(stack, THREAD, "", []), false, "nothing at all stages nothing");
  assert.equal(stageThreadReply(stack, THREAD, "", [{ title: "t", quote: "" }]), false, "an empty quote is no quote");
  assert.equal(stack.count(THREAD), 2);
  assert.equal(stack.count(WEB), 0, "never the parent session's");
  assert.equal(stack.count(OTHER), 0, "never another thread's");
  assert.deepEqual(stack.list(THREAD).map((m) => [m.text, (m.cites as typeof Q1[]).map((c) => c.quote)]),
                   [["Why bounded?", [Q1.quote]], ["", [Q2.quote]]]);
});

test("the staged item holds its own copy of the chips: clearing the box's chips after staging leaves it whole", () => {
  const stack = new StagedStack();
  const chips = [Q1];
  stageThreadReply(stack, THREAD, "Why bounded?", chips);
  chips.length = 0;
  assert.equal((stack.list(THREAD)[0].cites as unknown[]).length, 1);
});

test("Enter releases the run and the typed reply as ONE commentReply to the thread, addressed through its parent", () => {
  const stack = new StagedStack();
  stageThreadReply(stack, THREAD, "Why bounded?", [Q1]);
  stageThreadReply(stack, THREAD, "And this cap?", [Q2]);
  stageThreadReply(stack, OTHER, "elsewhere", []);
  const frame = threadReply(stack, WEB, THREAD, { text: "Both before the release, please.", cites: [] });
  assert.deepEqual(frame, { type: "commentReply", id: WEB, tid: THREAD, text:
    LEAD + "\n> " + Q1.quote + "\n\nWhy bounded?\n\n"
    + LEAD + "\n> " + Q2.quote + "\n\nAnd this cap?\n\n"
    + "Both before the release, please." });
  assert.equal(stack.count(THREAD), 0, "the release is one-shot");
  assert.equal(stack.count(OTHER), 1, "another thread's stack is untouched");
});

test("an empty Enter with a stack sends the stack; the typed reply's own chips ride last; nothing at all sends nothing", () => {
  const stack = new StagedStack();
  stageThreadReply(stack, THREAD, "held", []);
  assert.equal(threadReply(stack, WEB, THREAD, null)?.text, "held", "Send now / an empty Enter: the stack alone");
  assert.equal(threadReply(stack, WEB, THREAD, null), null, "and then there is nothing to send");
  assert.equal(threadReply(stack, WEB, THREAD, { text: "", cites: [] }), null);
  assert.equal(threadReply(stack, WEB, THREAD, { text: "plain", cites: [] })?.text, "plain", "with nothing staged it is the plain reply it always was");
  assert.equal(threadReply(stack, WEB, THREAD, { text: "this part?", cites: [Q1] })?.text, LEAD + "\n> " + Q1.quote + "\n\nthis part?");
});

test("the popover wires the chips, the strip and the release through these rules (source pins)", () => {
  // the stack of its own, persisted with the drafts; the chat's stack never holds a thread
  assert.match(RENDER, /const cmtStaged = new StagedStack\(\);/);
  assert.match(RENDER, /cmtStaged: cmtStaged\.entries\(\),/, "persisted with the drafts");
  assert.match(RENDER, /cmtStaged\.restore\(/, "and restored at load");
  // Enter: one frame for the run, through the reply door
  const send = RENDER.split("function commentSendFromPop(")[1].split("\nfunction ")[0];
  assert.match(send, /const frame = threadReply\(cmtStaged, cur\.sid, cur\.th\.tid, text \|\| cites\.length \? \{ text, cites \} : null\);/);
  assert.match(send, /if \(frame\) postThreadReply\(pop, cur, frame, true\);/);
  assert.doesNotMatch(send, /type: "commentReply"/, "no hand-built reply frame: threadReply builds it");
  const post = RENDER.split("function postThreadReply(")[1].split("\nfunction ")[0];
  assert.match(post, /vscodeApi\.postMessage\(frame\);/);
  assert.doesNotMatch(post, /routeUserMessage|flushStaged|sendMessage/, "never the chat's send");
  // Cmd/Ctrl+Enter stages, Enter sends, Escape drops the chips first, in the box's own keydown
  assert.match(RENDER, /if \(ev\.key === "Enter" && \(ev\.metaKey \|\| ev\.ctrlKey\) && !ev\.shiftKey && stageable\) \{ ev\.preventDefault\(\); stageFromPop\(pop\); \}/);
  assert.match(RENDER, /if \(stageable && cmtCites\.has\(th!\.tid\)\) \{ ev\.preventDefault\(\); cmtCites\.delete\(th!\.tid\); paintCmtChips\(\); return; \}\s*\n\s*closeCommentPop\(\);/);
  const stage = RENDER.split("function stageFromPop(")[1].split("\nfunction ")[0];
  assert.match(stage, /if \(!stageThreadReply\(cmtStaged, tid, box\.value\.trim\(\), cmtCites\.get\(tid\) \|\| \[\]\)\) return;/);
  assert.match(stage, /paintCmtStaged\(\{ reveal: "last" \}\);/);
  // the strip is the chat's own component over the thread's stack; its Send now releases the stack alone
  const strip = RENDER.split("function paintCmtStaged(")[1].split("\nfunction ")[0];
  assert.match(strip, /paintStagedStrip\(strip, cur \? cur\.th\.tid : null, \{\s*\n\s*stack: cmtStaged,/);
  assert.match(strip, /threadReply\(cmtStaged, now\.sid, now\.th\.tid, null\)/);
  assert.match(RENDER, /paintStagedStrip\(strip, id, \{\s*\n\s*stack: stagedMsgs,/, "the chat's strip is the same painter over the chat's stack");
  // the chips: a selection in the popover's messages, the message box's gesture rules, the shared pill
  assert.match(RENDER, /const q = threadSelection\(\);\s*\n\s*if \(q\) seedThreadQuote\(q\.text, q\.uuid\);/);
  const sel = RENDER.split("function threadSelection(")[1].split("\n}\n")[0];
  assert.match(sel, /if \(r\.collapsed \|\| !list\.contains\(r\.startContainer\) \|\| !list\.contains\(r\.endContainer\)\) return null;/, "both ends in the thread's messages");
  assert.match(RENDER, /paintQuoteChips\(strip, cmtCites\.get\(tid\) \|\| \[\], /, "the viewer bar's chip painter, shared");
  // only a thread that takes replies (open or relayed) gets them; a resolved thread keeps its plain reply
  assert.match(RENDER, /function cmtStageable\(th: CommentThread \| null \| undefined\): boolean \{ return !!th && threadTakesReplies\(th\.status\); \}/);
  // the strips are exempt from the whole-box drag, so their clicks land
  assert.match(RENDER, /\.cmt-quote, \.cmt-staged, \.cmt-chips, \.cmt-ask, \.cmt-bg, button,/);
  assert.match(CSS, /\.cmt-chips \{ flex: 0 0 auto; display: flex; flex-flow: row wrap;/);
  assert.match(CSS, /\.cmt-staged \{ flex: 0 0 auto; flex-direction: column;/);
});
