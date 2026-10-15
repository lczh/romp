// A comment thread on a LINKED machine (the review of #232, 2026-10-14): its question, its answers, its stop and its settings
// route through the federation the way a chat tab's do. The browser prefixes every session id from a linked kernel with the
// machine's name on the way in and strips it on the way out to that machine; a thread is a session too, so its `sid` in the
// comments frame (and in a create's answer) arrives prefixed like the question frames that name it, and the popover's ops
// name the thread by that id. Before: the question arrived as "HOST:<thread>" while the popover looked up the bare id, so a
// linked machine's thread showed its Needs you cue with no card, and its stop went to this machine's kernel. Also here: the
// question card's number keys are consumed (a digit answered the card AND typed itself into the thread's reply box), and
// the comments frame's capability keeps the old Break out note for a kernel that serves no thread question. Synthetic
// hosts and ids only (TESTHOST).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { prefixInbound, routeOutbound, bookkeepingKey } from "./federation";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const fn = (name: string): string => {
  const at = RENDER.indexOf("function " + name + "(");
  assert.ok(at >= 0, name + " exists");
  return RENDER.slice(at, RENDER.indexOf("\n}\n", at));
};
const PARENT = "11111111-2222-3333-4444-555555555555";
const THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa";
const row = (over: Record<string, unknown> = {}) => ({ tid: THREAD, sid: THREAD, anchorUuid: "a1", exact: "the retry cap", status: "open",
                                                       state: "picker", chip: "needsInput", ...over });

test("a linked machine's comments frame names each thread's session with the machine's prefix, as its question frames do", () => {
  const fr = prefixInbound("TESTHOST", { type: "comments", id: PARENT, threads: [row()], caps: ["threadAsks"] });
  assert.equal(fr.id, "TESTHOST:" + PARENT, "the parent, as ever");
  assert.equal(fr.threads[0].sid, "TESTHOST:" + THREAD, "the thread's session: the id its askLive arrives under");
  assert.equal(fr.threads[0].tid, THREAD, "the tid stays bare: thread ops address it under the parent's prefixed id");
  assert.deepEqual(fr.caps, ["threadAsks"], "the kernel's capabilities pass through");
  const ask = prefixInbound("TESTHOST", { type: "askLive", id: THREAD, ask: { kind: "single" } });
  assert.equal(ask.id, fr.threads[0].sid, "the question and the thread meet on one id");
  // a kernel from before the frame carried `sid`: the tid is the thread's session id
  const old = prefixInbound("TESTHOST", { type: "comments", id: PARENT, threads: [row({ sid: undefined })] });
  assert.equal(old.threads[0].sid, "TESTHOST:" + THREAD);
  // a create's answer carries the new thread's row: prefixed the same way
  const ack = prefixInbound("TESTHOST", { type: "commentCreated", id: PARENT, tid: THREAD, thread: row() });
  assert.equal(ack.thread.sid, "TESTHOST:" + THREAD);
  assert.equal(ack.tid, THREAD);
  // this machine's own frames are untouched
  const local = prefixInbound("", { type: "comments", id: PARENT, threads: [row()] });
  assert.equal(local.threads[0].sid, THREAD);
});

test("the answers, the stop, the settings and a compaction for a linked machine's thread go to that machine, by the bare id", () => {
  const sid = "TESTHOST:" + THREAD;
  for (const m of [{ type: "answerAsk", id: sid, target: 2 }, { type: "submitAsk", id: sid }, { type: "addCustomAsk", id: sid, text: "ninety seconds" },
                   { type: "cancelAsk", id: sid }, { type: "interrupt", id: sid }, { type: "setModel", id: sid, value: "opus" },
                   { type: "compactSession", id: sid }, { type: "cancelQueued", id: sid, md: "a queued line" }]) {
    const routes = routeOutbound(m, new Set(["TESTHOST"]));
    assert.equal(routes.length, 1, m.type);
    assert.equal(routes[0].host, "TESTHOST", m.type + " goes to the machine that runs the thread");
    assert.equal(routes[0].msg.id, THREAD, m.type + " names the thread as that kernel knows it");
  }
  // a thread on this machine stays here
  assert.equal(routeOutbound({ type: "answerAsk", id: THREAD, target: 1 }, new Set(["TESTHOST"]))[0].host, "");
});

test("the popover names its thread by the session id everywhere it acts: the card, the stop, the settings, its rendered controls", () => {
  assert.match(fn("threadSidOf"), /return th\.sid \|\| th\.tid;/);
  assert.match(fn("openThreadSid"), /return cur \? threadSidOf\(cur\.th\)/, "the question surface's session");
  const chip = fn("cmtStateChip");
  assert.match(chip, /b\.dataset\.sid = threadSidOf\(th\);/, "the stop posts the session id");
  assert.match(chip, /b\.dataset\.tid = th\.tid;/, "…and clears the thread's own latches by its tid");
  assert.match(RENDER, /syncMetaControls\(metaBox, threadMetaStatus\(th\), threadSidOf\(th\)\);/, "a model or effort pick routes to the thread's kernel");
  assert.match(fn("fillCommentMsgs"), /list\.dataset\.session = threadSidOf\(th\);/, "a queued line's ✕ and an error card's Retry inside the thread");
  assert.match(fn("fillCommentMsgs"), /renderingSid = threadSidOf\(th\);/, "the rendered events' own controls");
  // nothing posts a thread op by its bare tid as the session any more
  assert.doesNotMatch(RENDER, /dataset\.sid = th\.tid|dataset\.session = th\.tid|syncMetaControls\([^)]*th\.tid\)/);
});

test("a number key that answers the question card is the card's: consumed, so no box types it", () => {
  // the single card answers on a digit and the multi card toggles on one; each calls preventDefault when it acts, so the
  // type-anywhere default (which skips a handled key) never drops the digit into the thread's reply box
  assert.match(fn("onSingleKey"), /if \(idx >= 0\) \{ e\.preventDefault\(\); s\.focus = idx; answerLiveAsk\(s, opts\[idx\]\.n\); \}/);
  assert.match(fn("onMultiKey"), /if \(idx >= 0\) \{ e\.preventDefault\(\); s\.focus = idx; paintMultiFocus\(s\); toggleLiveAsk\(s, opts\[idx\]\.n\); \}/);
  assert.match(RENDER, /if \(e\.defaultPrevented \|\| e\.altKey \|\| e\.ctrlKey \|\| e\.metaKey\) return;/, "the default skips a handled key");
  // …and the popover's box takes the message box's own question gate: while its thread's card is up, the card owns the keys
  const gate = fn("typeFromAnywhereTarget");
  assert.match(gate, /if \(box && pop && box === pop\.querySelector\("\.cmt-input"\)\) \{ const tsid = openThreadSid\(\); return tsid && liveAsks\.has\(tsid\) \? null : box; \}/);
  assert.ok(gate.indexOf('pop.querySelector(".cmt-input")') < gate.indexOf("if (box !== ta) return box;"), "before the box is handed back");
});

test("a kernel that serves no thread question keeps the old note, by the comments frame's capability", () => {
  assert.match(KERNEL, /"caps": list\(COMMENT_FRAME_CAPS\)\}/);
  assert.match(KERNEL, /COMMENT_FRAME_CAPS = \("threadAsks", "seenAck"\)/);
  assert.match(RENDER, /commentThreadAsks\.set\(sid, Array\.isArray\(m\.caps\) && m\.caps\.includes\("threadAsks"\)\);/);
  assert.match(fn("fillCommentMsgs"), /if \(th\.status === "open" && threadStuck\(th\.state\) && !commentThreadAsks\.get\(sid\)\) \{\s*\n\s*const note = el\("div", "cmt-note"\);\s*\n\s*note\.textContent = "This thread hit a prompt it can't answer from here\. Break it out to continue\.";/);
});

// A read of a linked machine's thread (2026-10-15, read-latch.ts): the page holds it until that machine's frame counts it, so
// the read must reach that kernel with its number, and its answer must come back under the session's prefixed id, the key
// the page holds it by. While the relay is down the read waits on the conn's queue, the newest per thread.
test("a read of a linked machine's thread goes there by the bare id with its number, and the answer comes back under the prefixed id", () => {
  const routes = routeOutbound({ type: "commentSeen", id: "TESTHOST:" + PARENT, tid: THREAD, rid: 3 }, new Set(["TESTHOST"]));
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "TESTHOST");
  assert.deepEqual([routes[0].msg.id, routes[0].msg.tid, routes[0].msg.rid], [PARENT, THREAD, 3]);
  const ack = prefixInbound("TESTHOST", { type: "commentSeenAck", id: PARENT, tid: THREAD, rid: 3, seq: 2 });
  assert.deepEqual([ack.id, ack.tid, ack.rid, ack.seq], ["TESTHOST:" + PARENT, THREAD, 3, 2], "the tid stays bare, as the frame's does");
  const nack = prefixInbound("TESTHOST", { type: "commentSeenFailed", id: PARENT, tid: THREAD, rid: 3, error: "that thread is gone" });
  assert.deepEqual([nack.id, nack.tid, nack.rid], ["TESTHOST:" + PARENT, THREAD, 3]);
  assert.equal(bookkeepingKey({ type: "commentSeen", id: PARENT, tid: THREAD, rid: 3 }), bookkeepingKey({ type: "commentSeen", id: PARENT, tid: THREAD, rid: 4 }),
               "a relay that is down holds the newest read of the thread, the one the page's hold waits on");
  // the kernel answers the read it was sent, by its number
  assert.match(KERNEL, /_answer_comment_seen\(client, sid, str\(msg\["tid"\]\), msg\.get\("rid"\)\)/);
  assert.match(KERNEL, /"type": "commentSeenAck", "id": sid, "tid": tid, "rid": rid, "seq": seq/);
});
