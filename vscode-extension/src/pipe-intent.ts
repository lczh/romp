// Which webview→kernel ops carry USER INTENT — a typed message or an explicit
// state-changing pick — versus view chatter (focus, hover, scroll, fold state).
// The KernelPipe holds intent ops while its socket is down and STILL DELIVERS
// them after a reconnect: the old reconnect path wiped the whole queue, which
// silently ate a card reply sent during a kernel-restart window (the user
// 2026-07-21, roof). View chatter stays droppable — the reconnect reloads the
// webview, and its fresh "ready" resyncs all view state from the kernel.
// The inbound half (HELD_ACROSS_RELOAD, below): which kernel→webview frames must
// outlive that same reload, because they answer a replayed op and come only once.
export const INTENT_OPS: ReadonlySet<string> = new Set([
  // typed text — losing these loses the user's words
  "sendMessage", "askFollowUp", "askText", "addCustomAsk", "sendCommand", "rewindSend",
  // explicit clicks that mutate kernel/session state
  "interrupt", "apiRetry", "rewindDelete",
  "setModel", "setEffort", "setMode", "setFast", "setAuth",
  "renameSession", "moveSession", "endSession", "reviveSession", "restartSession",
  "nodeOverride", "askClear", "undoClear", "cardMove", "cardNotify", "redistill",
  "noticeAction",   // a notice card's button: a re-send of the user's own words, a click that mutates kernel state (T370)
  "answerAsk", "submitAsk", "toggleAsk", "cancelAsk",
  "setSessionFlag", "setSessionColor", "setGlobalRetryPaused", "setTimelineViews", "tagEdit", "openTagsDialog",
  // the REMOTE-tag edit (a tag homed on another kernel). Dropped with the view chatter it was simply
  // lost: the reconnect's reload wipes the pane's optimistic mirror too, and the resynced pane showed
  // the tag unchanged with nothing saying so (review find, 2026-09-08: the mirror never outlives the reload)
  "editTag",
  "reorderTabs", "closeTab",
  // comment threads. A new comment or a reply clears its box and draft at the press and keeps the words only in
  // the page's memory, which the reconnect's reload wipes, so one dropped here was lost with nothing saying so
  // (review find, 2026-10-17). A held frame never reached the kernel, so its replay is the first delivery; a
  // create also carries its createId, which the kernel answers with the same thread if it ever comes twice.
  "commentCreate", "commentReply", "commentResolve", "commentDelete", "commentPromote", "commentMerge",
  // the ✕ or ✎ on a queued message. Dropped, the session still took the message the user withdrew, and after a
  // ✎ it could take both the old words and the edited resend. It names its copy (qid), so it cancels only that one.
  "cancelQueued",
  // other clicks that act on a session, each naming what it acts on (a session, a task, a list of cards)
  "compactSession", "forkSession", "stopTask", "applyNow", "cancelCreate", "askClearMany",
  // the + picker's create. Dropped, the session the user named never existed, and the reload took its provisional tab
  // with it, so nothing said so; the web dashboard's socket sends every held op and created it (review find,
  // 2026-10-19). A held frame never reached the kernel, so the replay is the first delivery, and a create naming a
  // session that already runs is answered by focusing that session, never by a second one.
  "createSession",
  // the same kind of click on one session's tools, each naming what it acts on: the subagent speed switch, an MCP
  // server's enable, disable or reconnect, the "restore files" rewind, a watch's Cancel (the sibling of stopTask), and
  // the dismiss on a never-delivered bubble. Dropped, the reloaded page showed the old state with nothing saying the
  // click was lost. Every op the kernel routes to one session's backend (kernel.py _DRIVE_ID_OPS) is intent but the
  // read mark commentSeen, and pipe-intent.test.ts holds that list and this one together.
  "setSubagentStandard", "mcpAction", "rewindFiles", "cancelWatch", "dismissEcho",
  // the feed's "Run analysis on Opus" button. It is stamped at the click, so the kernel orders a replay against any
  // later pick and refuses one that is stale.
  "setJudgeModel",
  // Left out on purpose: clearAll names no cards, so a replay would clear whatever the kernel holds when the
  // socket returns, including cards that arrived while this pane could not show them; dropped, the reload shows
  // every card still there. answerPickerChoice has no consumer (the kernel lists it as a host op and this
  // extension does not take it), so holding it would deliver nothing.
]);

export function intentOp(type: unknown): boolean {
  return typeof type === "string" && INTENT_OPS.has(type);
}

// The INBOUND half: a kernel→webview frame that must survive the panel's own webview RELOAD. The
// reconnect path replays held intent and rebuilds the webview's html in the same tick, so the kernel's
// answer to a replayed op lands on a page that is gone: a frame posted between the reload and the
// fresh page's "ready" is dropped by the webview. State frames may go (the "ready" resyncs them), but a
// REFUSAL comes once, and one addressed by NAME (host + tag, not a per-page write id) is one the fresh
// page can still show. The case that found it (review find, 2026-09-08): a remote-tag ADD replayed
// right after a KERNEL restart is refused (the restarted kernel marks every attached host down until
// its tunnel supervisor dials it, and an add never queues), and the tagEditFailed frame hit the
// reloading timeline. Per-page write acks (tagEditAck, viewsAck) stay out on purpose: the fresh page
// tracks none of the old page's write ids and would drop them unread.
export const HELD_ACROSS_RELOAD: ReadonlySet<string> = new Set(["tagEditFailed"]);

export function heldAcrossReload(type: unknown): boolean {
  return typeof type === "string" && HELD_ACROSS_RELOAD.has(type);
}

// The hold itself, pure so the node runner can drive it. `offer` takes a frame the pipe received and
// says whether to deliver it now (false means it is parked); `release` hands back what was parked, in
// arrival order, for the webview's "ready" edge to deliver (extension.ts KernelPipe.webviewReady).
export class ReloadHold {
  private held: unknown[] = [];
  offer(m: any, webviewReady: boolean): boolean {
    if (webviewReady || !heldAcrossReload(m?.type)) return true;
    this.held.push(m);
    return false;
  }
  release(): unknown[] { return this.held.splice(0); }
}
