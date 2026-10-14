// A comments frame for a session whose store the kernel could not read (an EMFILE, an EIO, a truncated file or one that
// is not JSON; 2026-10-02) carries `error` and no `threads`. Before, the kernel sent an empty thread list instead and
// remembered it, and the page, which replaces a session's list with each frame's, dropped every highlight, closed an
// open popover and discarded unsent reply drafts. The page now keeps what it holds and says so once per failure, even
// if the error changes; the next readable frame replaces the list. The comments arms are lifted out of render.ts
// verbatim, transpiled and executed over the page state they touch.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const RENDER = fs.readFileSync(path.resolve(EXT, "..", "ui", "webview", "render.ts"), "utf8");
const SID = "11111111-2222-3333-4444-555555555555";

/** Every `m.type === "comments"` arm of the chat's frame handler, up to the arm after them, behind an `if (false) {}` so
 *  the lifted `else if` chain parses on its own, returning the open popover's key as the arms leave it. */
function liftCommentArms(): string {
  const start = RENDER.indexOf('else if (m.type === "comments" && m.id');
  const end = RENDER.indexOf('else if (m.type === "commentCreateFailed"', start);
  assert.ok(start > 0 && end > start, "anchors not found: re-anchor on render.ts's comments arms");
  const body = "if (false) {}\n" + RENDER.slice(start, end) + "\nreturn openCommentKey;";
  return requireCjs("esbuild").transformSync(body, { loader: "ts", target: "es2020" }).code;
}

const NAMES = ["m", "commentThreads", "commentReadError", "commentPending", "commentDrafts", "cmtAwaitBase", "cmtCreateInFlight",
  "synthSuperseded", "cmtLatchReleased", "pendingAdopt", "pendingCommentAnchor", "adoptCommentThread", "openCommentKey",
  "vscodeApi", "applyCommentMarks", "renderCommentPopover", "warnToast", "ephemeralWarnToast", "tabName", "commentThreadAsks"];
const thread = (tid: string) => ({ tid, anchorUuid: "u-" + tid, exact: "the passage", status: "open", createdT: 1, state: "",
  unread: false, promotedName: "", msgs: [] });

/** A page holding one thread on SID with an unsent reply draft, a pending reply bubble and its popover open. */
function page() {
  const arms = new Function(...NAMES, liftCommentArms());
  const st = {
    threads: new Map<string, unknown[]>([[SID, [thread("t1")]]]),
    readError: new Set<string>(),
    pending: new Map<string, unknown[]>([["t1", [{ text: "a reply on its way", t: 2 }]]]),
    drafts: new Map<string, string>([["t1", "half a reply"]]),
    toasts: [] as string[],
    replayed: [] as string[],                                     // warnToast: a notice a reload would replay
    open: { sid: SID, tid: "t1" } as { sid: string; tid: string } | null,
  };
  const deliver = (m: object) => {
    st.open = arms(m, st.threads, st.readError, st.pending, st.drafts, new Map(), new Map(),
      () => false, () => false, null, null, () => {}, st.open, null, () => {}, () => {},
      (t: string) => st.replayed.push(t), (t: string) => st.toasts.push(t), (sid: string) => (sid === SID ? "web" : undefined), new Map());
  };
  return { st, deliver };
}
const tids = (list: unknown[] | undefined) => (list || []).map((t) => (t as { tid: string }).tid);

test("an unreadable store keeps the page's threads, drafts, pending replies and open popover, and says so once", () => {
  const { st, deliver } = page();
  const failed = { type: "comments", id: SID, error: "Too many open files" };
  deliver(failed);
  deliver(failed);                                                // the kernel's 60 s repost of the same frame
  deliver({ type: "comments", id: SID, error: "Expecting value: line 1 column 87 (char 86)" });   // the cause changed
  deliver(failed);
  assert.deepEqual(tids(st.threads.get(SID)), ["t1"], "the page keeps the threads it holds");
  assert.equal(st.drafts.get("t1"), "half a reply", "the unsent reply survives");
  assert.equal(st.pending.get("t1")?.length, 1, "the pending reply bubble survives");
  assert.deepEqual(st.open, { sid: SID, tid: "t1" }, "the open popover stays open");
  assert.deepEqual(st.replayed, [], "the notice is the kind a reload does not replay (ephemeralWarnToast)");
  assert.equal(st.toasts.length, 1, "one notice for the failure, not one per frame or per error text");
  assert.match(st.toasts[0], /web/);
  assert.match(st.toasts[0], /Too many open files/);
});

test("the next readable frame replaces the list, and a later failure is said again", () => {
  const { st, deliver } = page();
  deliver({ type: "comments", id: SID, error: "Too many open files" });
  deliver({ type: "comments", id: SID, threads: [thread("t1"), thread("t2")] });
  assert.deepEqual(tids(st.threads.get(SID)), ["t1", "t2"], "the readable frame replaces the list");
  assert.equal(st.readError.size, 0, "a readable frame ends the failure");
  deliver({ type: "comments", id: SID, error: "Too many open files" });
  assert.equal(st.toasts.length, 2, "the later failure is said again");
  assert.deepEqual(tids(st.threads.get(SID)), ["t1", "t2"], "and the list it found stays");
});
