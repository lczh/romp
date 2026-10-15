// A comment thread's live indicators through the chat's OWN painters (thread-live-1, 2026-10-14): a thread stopped on a
// question or a permission prompt is answered inside its popover with the chat's question card, its passage and rail tick
// wear the needs-you cue, and the popover's stop follows the chat bar's rule. One implementation per feature: the card
// paints into a SURFACE (the chat's #live-ask or the popover's .cmt-ask), never a copy of it. The kernel half (the thread
// live map, the poll, the frame's chip) is tests/test_thread_live.py; the browser run of the whole story is
// tests/test_thread_ask_served.py. Synthetic threads only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { threadChipState, threadNeedsYou, threadCued, type CommentThread } from "./comments";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const fn = (name: string): string => {
  const at = RENDER.indexOf("function " + name + "(");
  assert.ok(at >= 0, name + " exists");
  return RENDER.slice(at, RENDER.indexOf("\n}\n", at));
};
const th = (over: Partial<CommentThread>): CommentThread => ({
  tid: "t1", anchorUuid: "a1", exact: "the retry cap", status: "open", createdT: 0, state: "", unread: false,
  promotedName: "", msgs: [], ...over,
});

test("the thread's chip: the kernel's own for a running thread, the raw state's reading for a dormant one", () => {
  assert.equal(threadChipState(th({ chip: "blocked", state: "waiting" })), "blocked", "the chat's chip wins: an API error");
  assert.equal(threadChipState(th({ chip: "needsInput", state: "picker" })), "needsInput");
  assert.equal(threadChipState(th({ chip: "awaitingBg", state: "waiting" })), "awaitingBg");
  // no chip (a dormant thread, an older kernel): the popover's old reading of the raw state
  assert.equal(threadChipState(th({ state: "permission" })), "needsInput");
  assert.equal(threadChipState(th({ state: "picker" })), "needsInput");
  for (const st of ["working", "retrying", "compacting"]) assert.equal(threadChipState(th({ state: st })), st);
  for (const st of ["", "waiting", "idle"]) assert.equal(threadChipState(th({ state: st })), "ready");
});

test("a thread waits on you while it is open, can run, and stands on a question or a permission prompt", () => {
  assert.ok(threadNeedsYou(th({ chip: "needsInput" })));
  assert.ok(threadNeedsYou(th({ state: "permission" })), "a dormant-shaped row too (no chip)");
  assert.ok(!threadNeedsYou(th({ chip: "needsInput", status: "resolved" })), "a closed thread waits on no one");
  assert.ok(!threadNeedsYou(th({ chip: "needsInput", error: "the CLI could not start" })), "a thread that cannot run asks nothing");
  for (const chip of ["working", "blocked", "ready", "awaitingBg", "compacting", "interrupting"]) assert.ok(!threadNeedsYou(th({ chip })), chip);
  // the passage's cue: an unread reply, or the wait on you; neither on a read, idle thread
  assert.ok(threadCued(th({ unread: true })));
  assert.ok(threadCued(th({ chip: "needsInput" })));
  assert.ok(!threadCued(th({ unread: true, status: "resolved" })));
  assert.ok(!threadCued(th({ chip: "working" })));
});

// the mark's classes, executed from render.ts's own styleCommentMark over a minimal element shim
function marked(t: CommentThread, before: string[] = []): { classes: Set<string>; title: string } {
  const src = "function styleCommentMark(m, th) {" + RENDER.split("function styleCommentMark(m: HTMLElement, th: CommentThread): void {")[1].split("\n}")[0] + "\n}";
  const classes = new Set<string>(before);
  const m = { classList: { toggle(c: string, on: boolean) { if (on) classes.add(c); else classes.delete(c); } }, title: "" };
  new Function("m", "th", "threadSettled", "commentInFlight", "threadNeedsYou", "threadCued", src + "\nstyleCommentMark(m, th);")(
    m, t, (s: string) => s === "resolved", () => false, threadNeedsYou, threadCued);
  return { classes, title: m.title };
}

test("the passage says the thread waits on you, and stops saying it the moment the question is answered", () => {
  const up = marked(th({ chip: "needsInput", state: "picker" }), ["cmt-ring"]);
  assert.ok(up.classes.has("needs"), "the needs-you class");
  assert.ok(up.classes.has("cmt-ring"), "the painter's ring stays while the cue does");
  assert.ok(!up.classes.has("unread") && !up.classes.has("busy"), "neither landed nor in flight: it waits on you");
  assert.match(up.title, /waiting on your answer/);
  const answered = marked(th({ chip: "working", state: "working", replyOwed: true }), ["needs", "cmt-ring"]);
  assert.ok(!answered.classes.has("needs") && !answered.classes.has("cmt-ring"), "the answer's frame takes the cue and its ring off, on the same pass");
});

test("the cue rides the existing painter, rail and click pick: one predicate for all of them", () => {
  assert.match(fn("paintCommentOutlines"), /querySelectorAll\("mark\.cmt-hl\.unread, mark\.cmt-hl\.needs"\)/);
  const rail = fn("updateCommentRail");
  assert.match(rail, /\+ \(threadNeedsYou\(th\) \? " needs" : ""\)/, "the tick's class");
  assert.match(rail, /\(threadNeedsYou\(t\.th\) \? 1 : 0\)/, "the bit rides the rail's signature: the answer repaints the tick");
  assert.match(fn("markChain"), /unread: !!th && threadCued\(th\)/, "the ring under the pointer is the thread that opens");
  assert.match(fn("hasCuedOpenThread"), /threadCued\(t\)/, "the geometry hooks' gate");
});

test("ONE question card, painted into a surface: the chat's for the active tab, the popover's for the open thread", () => {
  assert.match(RENDER, /const chatAsk: AskSurface = \{ host: \(\) => document\.getElementById\("live-ask"\), sid: \(\) => activeId,/);
  assert.match(RENDER, /const threadAsk: AskSurface = \{ host: \(\) => document\.querySelector\("#cmt-pop \.cmt-ask"\) as HTMLElement \| null,\s*\n\s*sid: \(\) => openThreadSid\(\),/);
  assert.match(fn("renderLiveAsk"), /paintAskCard\(chatAsk, ask\);/, "the chat paints through it");
  assert.match(fn("renderThreadAsk"), /paintAskCard\(threadAsk, ask\);/, "and the popover through the same function");
  const paint = fn("paintAskCard");
  for (const c of ["renderMultiCard(s, ask)", "renderSubmitCard(s, ask)", "renderSingleCard(s, ask)", "renderAskPreview(s)"]) assert.ok(paint.includes(c), c);
  // the question arriving or clearing repaints whichever surface shows that session
  assert.match(fn("setLiveAsk"), /if \(id === threadAsk\.sid\(\)\) renderThreadAsk\(\);/);
  assert.match(fn("clearLiveAsk"), /if \(id === threadAsk\.sid\(\)\) renderThreadAsk\(\);/);
  // each surface keeps its own highlight and guard: no module-level focus left for the two to fight over
  assert.doesNotMatch(RENDER, /let liveAskFocus\b|let sendingGuard\b|let sendingTimer\b/);
});

test("every answer posts the chat's own frame with the SURFACE's session, never the active tab's", () => {
  for (const [name, type] of [["answerLiveAsk", "answerAsk"], ["toggleLiveAsk", "toggleAsk"], ["addCustomLiveAsk", "addCustomAsk"],
                              ["submitLiveAsk", "submitAsk"], ["cancelLiveAsk", "cancelAsk"], ["sendTextLiveAsk", "askText"]]) {
    const body = fn(name);
    assert.match(body, /const sid = s\.sid\(\);/, name + " reads its surface's session");
    assert.match(body, new RegExp('type: "' + type + '", id: sid'), name + " posts " + type + " for it");
    assert.doesNotMatch(body, /activeId/, name + " never reaches for the active tab");
  }
});

test("the popover hosts the card beside its conversation, keeps it across refills, and lets its clicks through", () => {
  const pop = fn("renderCommentPopover");
  assert.match(pop, /const ask = el\("div", "cmt-ask"\);/, "a host of its own, outside .cmt-msgs: a frame's refill never rebuilds the card under a click");
  assert.match(pop, /if \(th && th\.status === "open"\) \{\s*\n\s*renderThreadAsk\(\);/, "painted once the popover is in the page");
  assert.match(pop, /\.cmt-chips, \.cmt-ask, \.cmt-bg, button, input,/, "the box's drag lets the card's and the background box's presses through (a captured press retargeted their clicks)");
  // the note that points at Break out shows only where the thread's kernel serves no thread question (the comments
  // frame's caps), never beside the card (the review of #232: a page newer than its kernel showed the cue and nothing)
  assert.match(fn("fillCommentMsgs"), /if \(th\.status === "open" && threadStuck\(th\.state\) && !commentThreadAsks\.get\(sid\)\) \{/);
  assert.match(RENDER, /commentThreadAsks\.set\(sid, Array\.isArray\(m\.caps\) && m\.caps\.includes\("threadAsks"\)\);/);
});

test("the popover's box is the question's 'add your own' field, by the message box's own rule", () => {
  assert.match(fn("composerAnswersAsk"), /return activeId \? boxAnswersAsk\(activeId\) : null;/);
  const send = fn("commentSendFromPop");
  assert.match(send, /const askRoute = text \? boxAnswersAsk\(asid\) : null;/);
  assert.match(send, /if \(askRoute === "custom"\) addCustomLiveAsk\(threadAsk, text\); else sendTextLiveAsk\(threadAsk, text\);/);
  assert.match(fn("syncCmtBoxAskMode"), /box\.placeholder = "add your own answer…  \(⏎ submit\)";/, "the message box's words in answer mode");
});

test("the popover's chip and stop are the chat bar's: its chip state, and the one stop rule", () => {
  const chip = fn("cmtStateChip");
  assert.match(chip, /const st = threadChipState\(th\);/);
  assert.match(chip, /wrap\.appendChild\(statusChip\(chipWords\(threadMetaStatus\(th\)\)\)\);/, "Needs you, API error, Awaiting command and the rest from the shared chip");
  assert.match(chip, /if \(chipStops\(st\)\) wrap\.appendChild\(stopBtn\(\)\);/);
  assert.match(fn("threadMetaStatus"), /state: threadChipState\(th\) as ChipState,/);
});

// ── thread-live-2: the thinking line, the subagents' progress, the background box and the context gauge ─────────────────

test("the thread's status is its build's: the gauge's fields, the box's rows and the Awaiting chip's words", () => {
  assert.match(fn("threadMetaStatus"), /return \{ \.\.\.\(\(th\.live && th\.live\.status\) \|\| \{\}\),/, "the build's status projection first, the chip and the badges over it");
  assert.match(fn("cmtStateChip"), /statusChip\(chipWords\(threadMetaStatus\(th\)\)\)/, "Awaiting command, not Awaiting alone");
});

test("the thinking line: the chat's own painter, in the popover's fixed-height foot, ticked by the one clock", () => {
  assert.match(fn("paintLiveThinking"), /paintThinkingLine\(n, th\);/);
  const thread = fn("paintThreadThinking");
  assert.match(thread, /let n = pop\.querySelector\("\.cmt-live-status"\) as HTMLElement \| null;/);
  assert.match(thread, /n = el\("div", "cmt-live-status"\);\s*\n\s*list\.after\(n\);/, "made once, by the first stretch shown (the chat's foot-line rule)");
  assert.match(thread, /const th = thinkingShown\(openThreadSid\(\)\);/, "the same gate: only while the thread works");
  assert.match(thread, /paintThinkingLine\(n, th\);/, "the one painter");
  assert.match(fn("thinkingShown"), /\?\? openThreadState\(id\)/, "a thread's working state is its chat chip");
  assert.match(fn("liveStatusTick"), /paintLiveThinking\(\);\s*\n\s*paintThreadThinking\(\);/);
  assert.match(fn("applyLiveStatus"), /if \(id === openThreadSid\(\)\) paintThreadThinking\(\);/);
  assert.doesNotMatch(fn("renderCommentPopover"), /cmt-live-status/, "no room reserved for a thread that never thinks while its box is open");
});

test("a thread's subagents' progress reads the THREAD's frame and repaints in its popover", () => {
  assert.match(RENDER, /liveStatus\.get\(\(renderingIntoThread \? renderingSid : renderingOwnerSid \|\| renderingSid\) \|\| ""\)\?\.tasks\[ev\.toolUseId\]/);
  assert.match(fn("paintTaskProgress"), /const list = id === openThreadSid\(\) \? document\.querySelector\("#cmt-pop \.cmt-msgs"\)/);
});

test("ONE background box painter: the chat's #bg-tasks, or the thread's .cmt-bg from its build, the presses on each stable host", () => {
  const box = fn("renderBgTasks");
  assert.match(box, /const host = target \? target\.host : document\.getElementById\("bg-tasks"\);/);
  assert.match(box, /host\.dataset\.sid = sid \|\| "";/, "whose box: its rows' Stop and Cancel post for this session");
  const install = fn("installBgBox");
  for (const t of ["stopTask", "cancelWatch"]) assert.match(install, new RegExp('type: "' + t + '", id: sid,'), t + " by the box's own session");
  assert.doesNotMatch(install, /activeId/, "never the active tab's");
  assert.match(RENDER, /\(\(\) => \{ const host = document\.getElementById\("bg-tasks"\); if \(host\) installBgBox\(host\); \}\)\(\);/);
  const thread = fn("renderThreadBg");
  assert.match(thread, /const sid = cur \? threadSidOf\(cur\.th\) : null;\s*\n\s*renderBgTasks\(\{ host, sid,/);
  assert.match(fn("renderCommentPopover"), /const bg = el\("div", "cmt-bg"\);[\s\S]{0,200}installBgBox\(bg\);/, "installed once per popover, on its stable host");
  assert.match(fn("renderCommentPopover"), /if \(th\) renderThreadBg\(\);\s+\/\/ the background box, from this frame/, "repainted by each frame");
});

test("the context gauge: the chat's battery over the thread's status, a click compacting the THREAD by its state at the click", () => {
  const pop = fn("renderCommentPopover");
  assert.match(pop, /const ctx = buildCtxBar\(compactOpenThread\);/, "never a state read when the popover was built (the review of #240)");
  const click = fn("compactOpenThread");
  assert.match(click, /const cur = openCommentThread\(\);/, "the open thread as it stands now");
  assert.match(click, /compactSessionOf\(bar, threadSidOf\(cur\.th\), threadMetaStatus\(cur\.th\)\.state\);/, "the chat's own rules over its current state");
  assert.match(fn("paintThreadCtx"), /setCtxBar\(bar, st\.ctx, st\.state === "compacting", pickTone\(st\.ctxColor, st\.ctxTone\), st\.ctxOver\);/);
  assert.match(fn("compactSessionOf"), /vscodeApi\.postMessage\(\{ type: "compactSession", id: sid \}\);/);
  assert.match(fn("compactActiveSession"), /compactSessionOf\(bar, activeId, s\.status\.state\);/, "the chat's battery through the same post");
});

// ── the review of #240: the conversation keeps its bottom, and the box's output tails ride their own frame ──────────────

test("ONE rule keeps a following reader on the conversation's bottom whatever part of the box takes its room", () => {
  const keep = fn("keepCmtTail");
  assert.match(keep, /new ResizeObserver\(/, "the list's own size, observed: every part of the box that changes it at once");
  assert.match(keep, /list\.addEventListener\("scroll", \(\) => \{ following = cmtFollowing\(list\); \}/, "the record: the list's scroll events");
  assert.match(keep, /if \(lastH >= 0 && \(w !== lastW \|\| h !== lastH\) && wasFollowing\) list\.scrollTop = sh;/);
  assert.match(fn("fillCommentMsgs"), /const atTail = cmtFollowing\(list\);/, "the refill's follow reads the same predicate");
  assert.match(fn("renderCommentPopover"), /if \(msgs\) \{ msgs\.scrollTop = msgs\.scrollHeight; keepCmtTail\(msgs\); \}/, "installed once per popover");
});

test("a running thread's output tails: their own frame, merged into the box's rows by row id", () => {
  assert.match(RENDER, /else if \(m\.type === "threadBgOut"\) applyThreadBgOut\(m\);/);
  assert.match(fn("applyThreadBgOut"), /if \(m\.id === openThreadSid\(\)\) renderThreadBg\(\);/, "the open thread's box repaints at once");
  assert.match(fn("renderThreadBg"), /withThreadBgOut\(/);
  assert.match(fn("withThreadBgOut"), /typeof out\[t\.id\] === "string" \? \{ \.\.\.t, output: out\[t\.id\] \} : t/, "a row the frame names takes its tail");
});

test("a broken-out thread's Open the session names the session as the strip does (host-prefixed on a linked machine)", () => {
  assert.match(fn("renderCommentPopover"), /open\.dataset\.sid = threadSidOf\(th\);/);
  assert.match(RENDER, /cmtopensession: \(elx\) => \{\s*\n\s*const sid = elx\.dataset\.sid;\s*\n\s*closeCommentPop\(\);\s*\n\s*if \(sid\) setActive\(sid\);/);
});
