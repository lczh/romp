// intentOp gates what survives a KernelPipe reconnect: user intent (typed text,
// explicit picks) delivers after the socket returns; view chatter drops, because
// the reconnect reloads the webview and resyncs it fresh (the user 2026-07-21).
// heldAcrossReload / ReloadHold are the inbound half: the kernel's refusal of a
// replayed op must outlive that same reload (review find, 2026-09-08).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { INTENT_OPS, intentOp, HELD_ACROSS_RELOAD, heldAcrossReload, ReloadHold } from "./pipe-intent";

test("typed-text ops are intent — losing them loses the user's words", () => {
  for (const t of ["sendMessage", "askFollowUp", "askText", "addCustomAsk", "sendCommand", "rewindSend"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("explicit state-changing picks are intent", () => {
  // setTimelineViews and tagEdit are the two views writes (a lens/order blob; a targeted tag edit,
  // 2026-09-05) — a tag renamed during a reconnect window must still land
  for (const t of ["setModel", "setEffort", "setMode", "setFast", "interrupt", "endSession",
    "nodeOverride", "askClear", "answerAsk", "submitAsk", "renameSession", "moveSession", "noticeAction",
    "setTimelineViews", "tagEdit", "editTag"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("a remote-tag edit is intent: dropped on reconnect, it was simply lost", () => {
  // editTag is the federation op for a tag whose home is another kernel (render.ts's union add/remove,
  // the timeline's __rompTimelineEditTag hook, 2026-08-24). The pane mirrors the edit locally beside
  // the post, but the mirror never outlives the reconnect: every panel's onReconnect reloads its
  // webview and the fresh "ready" resyncs from the kernel, where the edit never landed. So a post
  // dropped with the view chatter left the tag unchanged, with nothing saying the gesture was lost
  // (review find, 2026-09-08: the earlier wording had the mirror staying on screen, which it cannot)
  assert.ok(intentOp("editTag"), "a queued editTag must be kept across the extension's reconnect");
});

test("a refusal of a replayed intent is held across the webview's reload; state frames are not", () => {
  // the reconnect replays intent and reloads the webview in one tick; the kernel's refusal of a replayed
  // op (a remote-tag ADD right after a kernel restart: the home kernel's tunnel is not up yet, and an
  // add never queues) then lands on a page that is gone (review find, 2026-09-08). A refusal addressed
  // by name is one the fresh page can show; a state frame is resynced by its ready, and a per-page write
  // ack names a write the fresh page never made
  assert.ok(heldAcrossReload("tagEditFailed"), "the remote-tag refusal must reach the reloaded timeline");
  assert.ok(HELD_ACROSS_RELOAD.has("tagEditFailed"));
  for (const t of ["data", "bars", "caps", "ka", "pipeState", "models", "tagEditAck", "viewsAck"])
    assert.ok(!heldAcrossReload(t), `${t} is resynced by the fresh ready, or names a write the fresh page never made`);
  assert.ok(!heldAcrossReload(undefined));
  assert.ok(!heldAcrossReload(42));
});

test("the hold parks a refusal only while the webview is not ready, and releases once, in arrival order", () => {
  const h = new ReloadHold();
  const refused = { type: "tagEditFailed", host: "TESTHOST-B", name: "team",
    error: '"TESTHOST-B" is attached but not reachable right now', queued: false };
  assert.equal(h.offer({ type: "data", data: {} }, false), true, "a state frame goes into the reload; the ready resyncs it");
  assert.equal(h.offer(refused, false), false, "the refusal waits for the ready");
  assert.equal(h.offer({ type: "tagEditFailed", host: "TESTHOST-B", name: "pool", error: "refused" }, false), false);
  assert.deepEqual(h.release().map((m: any) => m.name), ["team", "pool"], "released in arrival order");
  assert.deepEqual(h.release(), [], "…and only once");
  assert.equal(h.offer(refused, true), true, "with the webview ready a refusal goes straight through");
  assert.deepEqual(h.release(), [], "…and is not parked on the way");
});

test("comment-thread ops, a queued message's withdrawal and the other session gestures are intent", () => {
  // a comment typed while the kernel restarts had its box and draft cleared at the press, and the reload after the
  // reconnect wiped the page's only copy; a dropped withdrawal let the session take the message the user had pulled
  // back (review find, 2026-10-17). Each of these is a click the user made, and each names what it acts on.
  for (const t of ["commentCreate", "commentReply", "commentResolve", "commentDelete", "commentPromote", "commentMerge",
    "cancelQueued", "compactSession", "forkSession", "stopTask", "applyNow", "cancelCreate", "askClearMany"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("a new session's create and the clicks on a session's tools are intent", () => {
  // a create made while the kernel restarts was dropped at the reconnect, and the reload took its provisional tab, so
  // the session the user named never existed and nothing said so; the web dashboard's socket sends every held op and
  // created it. The subagent speed switch, an MCP server's toggle, the restore-files rewind, a watch's Cancel, a
  // never-delivered bubble's dismiss and the feed's analysis-model button were dropped the same way (review find,
  // 2026-10-19).
  for (const t of ["createSession", "setSubagentStandard", "mcpAction", "rewindFiles", "cancelWatch", "dismissEcho",
    "setJudgeModel"]) {
    assert.ok(intentOp(t), `${t} must survive a reconnect`);
  }
});

test("every per-session op the kernel routes to a session's backend is intent, save the read mark", () => {
  // kernel.py _DRIVE_ID_OPS lists the ops that act on one session (a send, a setting, a rewind, a tool's toggle). Four
  // of them were missing from INTENT_OPS and dropped at the reconnect (review find, 2026-10-19); a new one added there
  // must be classified here too.
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const m = /\n_DRIVE_ID_OPS = \(([^)]*)\)/.exec(KERNEL);
  assert.ok(m, "kernel.py's _DRIVE_ID_OPS tuple");
  const ops = [...m![1].matchAll(/"([A-Za-z]+)"/g)].map((x) => x[1]);
  assert.ok(ops.includes("sendMessage") && ops.includes("commentSeen"), "the tuple parsed");
  // commentSeen is a thread's read watermark, posted as its popover opens: the pane's own bookkeeping, not a click
  const BOOKKEEPING = new Set(["commentSeen"]);
  assert.deepEqual(ops.filter((t) => !BOOKKEEPING.has(t) && !intentOp(t)), [],
    "a per-session op dropped at the reconnect is a click lost with nothing saying so");
});

// Every op a webview posts straight to its host that is NOT intent: view state the reconnect's reload resyncs, the
// pane's own bookkeeping, or a request the extension host answers itself.
const NOT_INTENT = [
  // where the pane is looking, and what it asks the kernel for
  "ready", "activeTab", "openSession", "openByName", "jumpSession", "requestSessions", "needSlot", "imgRequest", "redial",
  "loadOlder", "loadAround", "loadTurns", "loadEpisode", "openSubagent", "closeSubagent", "viewReadOnly",
  "showAskPath", "showOnTimeline", "dotOpen", "dotHover", "hoverHighlight", "focusComposer", "colorSync",
  "setViewFolds", "setViewOrder",   // published only while the pipe is up (the pane withdraws its publisher on the down edge)
  // the pane's bookkeeping and audit rows
  "cardOpened", "clientDiag", "locateDiag", "commentSeen", "commentDropSeen", "commentDropHidden",
  // the + picker's path completion, and requests the extension host answers on its own
  "dirComplete", "browseDir", "openLink", "pickFile", "pickResult", "readClipboard", "editorSelection",
  // left out on purpose: see the test below
  "clearAll", "answerPickerChoice",
];

test("every op a webview posts is classified: intent, or named here as not", () => {
  // createSession and five clicks on a session's tools were posted without ever being classified, and the reconnect
  // dropped them (review find, 2026-10-19). A new op posted from ui/webview fails here until someone decides which it is.
  const DIR = path.resolve(process.cwd(), "..", "ui", "webview");
  const posted = new Set<string>();
  for (const f of fs.readdirSync(DIR)) {
    if (!f.endsWith(".ts") || f.endsWith(".test.ts")) continue;
    for (const m of fs.readFileSync(path.join(DIR, f), "utf8").matchAll(/postMessage(?:\?\.)?\(\{ ?type: "([A-Za-z]+)"/g))
      posted.add(m[1]);
  }
  assert.ok(posted.has("sendMessage") && posted.has("createSession") && posted.has("ready"), "the scan found the webviews' posts");
  const notIntent = new Set(NOT_INTENT);
  assert.deepEqual([...posted].filter((t) => !intentOp(t) && !notIntent.has(t)).sort(), [],
    "add each to INTENT_OPS (pipe-intent.ts) if dropping it at the reconnect loses a click, or to NOT_INTENT above");
  assert.deepEqual(NOT_INTENT.filter((t) => intentOp(t)), [], "an op is one or the other");
});

test("Clear all and the resume-picker choice are left out of the replay on purpose", () => {
  // clearAll names no cards: replayed when the socket returns, it would clear cards that arrived while the pane could
  // not show them, where a dropped one leaves every card on the reloaded page. answerPickerChoice has no consumer in
  // the kernel or the extension. If either changes, this is the test to revisit.
  assert.ok(!intentOp("clearAll"));
  assert.ok(!intentOp("answerPickerChoice"));
});

test("view chatter is not intent — the reconnect reload resyncs it", () => {
  for (const t of ["ready", "openSession", "showAskPath", "showOnTimeline", "dotHover",
    "hoverHighlight", "loadOlder", "requestSessions", "openByName", "dotOpen", "imgRequest"]) {
    assert.ok(!intentOp(t), `${t} is view state, not user intent`);
  }
});

test("a client-diag row is view chatter: one queued while the pipe is down is dropped at the reconnect", () => {
  // ui/webview/window-sender.ts discloses this for its trace rows (noteForeign; the timeline boot's inlined copy runs
  // on the kernel's /timeline page only, never in a webview): in a VS Code webview a row filed while the kernel pipe
  // is down does not survive the reconnect, whose keep set is intent only (pipe-durability.test.ts pins that half). If
  // clientDiag ever becomes intent, that disclosure is stale.
  assert.ok(!intentOp("clientDiag"), "clientDiag is not user intent: the reconnect drops it with the view chatter");
});

test("non-strings never classify as intent", () => {
  assert.ok(!intentOp(undefined));
  assert.ok(!intentOp(null));
  assert.ok(!intentOp(42));
  assert.ok(!INTENT_OPS.has(""));
});
