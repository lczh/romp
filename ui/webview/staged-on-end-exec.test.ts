// A session's end takes its staged stack with its draft, EXECUTED. dismissSession's genuine-end branch (the tab's ✕ / End,
// or the owning kernel's closed frame) cleared the draft, the quote chips, the edit pill and the attachments, but left the
// staged stack (Cmd/Ctrl+Enter) in the page and in its persisted state. A revive keeps the sid, so the revived tab's strip
// showed the old items again and the next plain send released them ahead of the typed message, as one message the user
// had discarded. A host drop or an omission keeps the stack, as it keeps the draft: the session comes back with both.
//
// dismissSession, flushStaged and persistDrafts are lifted out of render.ts, transpiled at run time and run over a stub
// scope (the chat-resync.test.ts pattern), with the real StagedStack and stagedPosts (staged-messages.ts) and the real
// focusAfterDismiss (pane-focus.ts). draft-teardown.test.ts holds the model of the other teardown rules and the source
// pins. Synthetic sessions only (the notes-api demo's web and api).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack, stagedPosts } from "./staged-messages";
import { focusAfterDismiss } from "./pane-focus";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

/** One top-level function of render.ts, from its header to the first closing brace at column 0. */
function lift(header: string): string {
  const a = RENDER.indexOf(header);
  assert.ok(a > 0, `anchor moved: ${header.slice(0, 60)}`);
  assert.equal(RENDER.indexOf(header, a + 1), -1, `anchor not unique: ${header.slice(0, 60)}`);
  const b = RENDER.indexOf("\n}\n", a);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b + 3), { loader: "ts" }).code;
}
const SRC = [
  lift("function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>): void {"),
  lift("function flushStaged(sid: string,"),
  lift("function persistDrafts(): void {"),
].join("\n");

const WEB = "5b0e2c4a-8d71-4f3e-9c26-1a7f0d3e5b91", PEER = "c3f81a26-4e9d-4b70-8a15-6d2e9f0b7c48";
type Why = "close" | "end" | "hostDrop" | "omitted";
type Post = { sid: string; text: string };

function world(active: string) {
  let saved: Record<string, unknown> = {};
  const posts: Post[] = [];
  const loaded: (string | null)[] = [];       // loadComposerFor's ids: the composer strip repainted for the box's new owner
  const viewerPaints: number[] = [];          // paintViewerStaged, with web's count at the time of the paint
  const stagedMsgs = new StagedStack();
  const S: Record<string, unknown> = {
    drafts: new Map<string, string>(), composerCitations: new Map(), composerEdits: new Map(), composerFiles: new Map(),
    stagedMsgs, cmtStaged: new StagedStack(), pendingShips: new Map(),
    vscodeApi: { getState: () => saved, setState: (s: Record<string, unknown>) => { saved = s; }, postMessage: () => {} },
    sessions: new Map<string, { name: string }>(), tabMeta: new Map(), order: [] as string[], mru: [] as string[],
    views: new Map(), ledgers: new Map(), liveAsks: new Map(), awaitingFull: new Set(), pendingFullWhy: new Map(),
    staleAnswerReasked: new Set(), resync: {}, skeletonTabs: {},
    activeId: active, peekId: null, vanishedId: null, vanishedWhy: null, vanishedName: "", vanishedByDecline: false,
    focusAfterDismiss, stagedPosts,
    hostOf: (x: string) => { const i = x.indexOf(":"); return i > 0 ? x.slice(0, i) : ""; },
    heldHere: () => true,   // one column, no split: every tab is this page's
    routeUserMessage: (sid: string, text: string) => { posts.push({ sid, text }); },
    loadComposerFor: (id: string | null) => { loaded.push(id); },
    paintViewerStaged: () => { viewerPaints.push(stagedMsgs.count(WEB)); },
    document: { getElementById: () => null },
  };
  const proxy = new Proxy(S, {
    has: (t, k) => k in t || !(k in globalThis),
    get: (t, k) => (k in t ? (t as any)[k] : (typeof k === "symbol" ? undefined : (() => undefined))),
    set: (t, k, v) => { (t as any)[k] = v; return true; },
  });
  const fns = (new Function("SCOPE", `with (SCOPE) { ${SRC}\n return { dismissSession, flushStaged, persistDrafts }; }`) as any)(proxy) as {
    dismissSession: (id: string, why: Why) => void; flushStaged: (sid: string, typed?: { text: string }) => number; persistDrafts: () => void;
  };
  const sessions = S.sessions as Map<string, { name: string }>, order = S.order as string[], mru = S.mru as string[];
  for (const [id, name] of [[WEB, "web"], [PEER, "api"]]) { sessions.set(id, { name }); order.push(id); }
  mru.push(active, active === WEB ? PEER : WEB);
  (S.drafts as Map<string, string>).set(WEB, "half-typed reply");
  stagedMsgs.push(WEB, { text: "Why does the handler retry here?", cites: [{ title: "quote", quote: "retry the request twice", uuid: null }] });
  stagedMsgs.push(WEB, { text: "This limit looks too low.", cites: [{ title: "quote", quote: "max_items = 10", uuid: null }] });
  stagedMsgs.push(PEER, { text: "Keep this one for api.", cites: [] });
  fns.persistDrafts();
  /** web's stack as a reload would restore it from the persisted state */
  const reloaded = (sid: string) => { const s = new StagedStack(); s.restore(saved.staged); return s.count(sid); };
  const revive = (id: string, name: string) => { sessions.set(id, { name }); order.push(id); };
  return { S, stagedMsgs, posts, loaded, viewerPaints, reloaded, revive, ...fns };
}

test("the tab's ✕ on the active session takes its staged stack with the draft, in the page and in the persisted state", () => {
  const w = world(WEB);
  w.dismissSession(WEB, "close");
  assert.equal((w.S.drafts as Map<string, string>).has(WEB), false, "the close rule clears the draft (unchanged)");
  assert.equal(w.stagedMsgs.count(WEB), 0, "the staged stack goes with the session");
  assert.equal(w.reloaded(WEB), 0, "…and with it the persisted copy a reload would restore");
  assert.equal(w.stagedMsgs.count(PEER), 1, "another session's stack is untouched");
  assert.equal(w.reloaded(PEER), 1);
  assert.deepEqual(w.loaded, [PEER], "the composer strip is repainted for the tab that now owns the box");
});

test("the kernel's closed frame for a background session: the stack goes, and an open viewer bar's staged line repaints", () => {
  const w = world(PEER);
  w.dismissSession(WEB, "end");
  assert.equal(w.stagedMsgs.count(WEB), 0);
  assert.equal(w.reloaded(WEB), 0);
  assert.deepEqual(w.viewerPaints, [0], "a viewer bar naming web repaints once, after its stack emptied");
  assert.deepEqual(w.loaded, [], "the box never changed hands");
  assert.equal(w.stagedMsgs.count(PEER), 1);
});

test("End, then Revive under the same sid: the next plain send carries only what was typed", () => {
  const w = world(WEB);
  w.dismissSession(WEB, "close");
  w.revive(WEB, "web");                                  // a revive keeps the sid
  assert.equal(w.stagedMsgs.count(WEB), 0, "the revived tab's strip has nothing to show");
  assert.equal(w.flushStaged(WEB, { text: "carry on" }), 0, "no discarded item rides the send");
  assert.deepEqual(w.posts, [{ sid: WEB, text: "carry on" }]);
});

test("a host drop or an omission keeps the stack, as it keeps the draft: the session comes back with both", () => {
  for (const why of ["hostDrop", "omitted"] as const) {
    const w = world(PEER);
    w.dismissSession(WEB, why);
    assert.equal((w.S.drafts as Map<string, string>).get(WEB), "half-typed reply", why);
    assert.equal(w.stagedMsgs.count(WEB), 2, why + " keeps the staged stack");
    assert.equal(w.reloaded(WEB), 2, why + " keeps the persisted copy");
    assert.deepEqual(w.viewerPaints, [], why + ": nothing moved, nothing to repaint");
  }
});
