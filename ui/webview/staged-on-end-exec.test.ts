// A session's end takes its DRAFT and keeps its STAGED stack, EXECUTED, whoever ended it (the user 2026-10-23). The draft
// is what the user was still typing, and it goes with an ended session (the user 2026-08-04). Staged messages are held on
// purpose (Cmd/Ctrl+Enter), so they outlive the end: in the page, in its persisted state across a reload, and on a revive,
// which keeps the sid, the strip shows them again and the next plain send releases them ahead of the typed message, as it
// did before #362 dropped them here. A host drop or an omission keeps both, as before.
//
// dismissSession, closeTabLocally, flushStaged, persistDrafts and stagedLabel are lifted out of render.ts, transpiled at
// run time and run over a stub scope (the chat-resync.test.ts pattern), with the real StagedStack, stagedPosts and
// stagedRunBody (staged-messages.ts) and the real focusAfterDismiss (pane-focus.ts). The kernel's `closed` frame is routed
// through render.ts's own routing line, lifted too. draft-teardown.test.ts holds the model of the other teardown rules and
// the source pins. Synthetic sessions only (the notes-api demo's web and api).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack, stagedPosts, stagedRunBody, type StagedMsg } from "./staged-messages";
import { focusAfterDismiss } from "./pane-focus";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;

/** One top-level function of render.ts, from its header to the first closing brace at column 0. */
function lift(header: string): string {
  const a = RENDER.indexOf(header);
  assert.ok(a > 0, `anchor moved: ${header.slice(0, 60)}`);
  assert.equal(RENDER.indexOf(header, a + 1), -1, `anchor not unique: ${header.slice(0, 60)}`);
  const b = RENDER.indexOf("\n}\n", a);
  return ts(RENDER.slice(a, b + 3));
}
// the message handler's routing of the kernel's `closed` frame, the one line every end that is not this page's own ✕
// reaches it by: another session's `romp end`, the session's own idle end, End pressed on another page, a stale closeTab
const CLOSED_ROUTE = RENDER.match(/else if \(m\.type === "closed"\) dismissSession\(m\.id, m\.hostDrop === true \? "hostDrop" : "end"\);/);
assert.ok(CLOSED_ROUTE, "the closed frame's routing line moved");
const SRC = [
  lift("function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>): void {"),
  lift("function closeTabLocally(id: string): void {"),
  lift("function flushStaged(sid: string,"),
  lift("function persistDrafts(): void {"),
  lift("function stagedLabel(n: number, asking: boolean): string {"),
  ts(`function onFrame(m: any): void { if (false) {} ${CLOSED_ROUTE![0]} }`),
].join("\n");

const WEB = "5b0e2c4a-8d71-4f3e-9c26-1a7f0d3e5b91", PEER = "c3f81a26-4e9d-4b70-8a15-6d2e9f0b7c48";
type Why = "close" | "end" | "hostDrop" | "omitted";
type Post = { sid: string; text: string };
const WEB_STAGED: StagedMsg[] = [
  { text: "Why does the handler retry here?", cites: [{ title: "quote", quote: "retry the request twice", uuid: null }] },
  { text: "This limit looks too low.", cites: [{ title: "quote", quote: "max_items = 10", uuid: null }] },
];

/** A page: `active` owns the box. `from` boots it from a persisted state (a reload) instead of staging afresh. */
function world(active: string, from?: Record<string, unknown>) {
  let saved: Record<string, unknown> = from ? { ...from } : {};
  const posts: Post[] = [];
  const stagedMsgs = new StagedStack();
  const S: Record<string, unknown> = {
    drafts: new Map<string, string>(), composerCitations: new Map(), composerEdits: new Map(), composerFiles: new Map(),
    stagedMsgs, cmtStaged: new StagedStack(), pendingShips: new Map(), closingTabs: new Map<string, number>(),
    vscodeApi: { getState: () => saved, setState: (s: Record<string, unknown>) => { saved = s; }, postMessage: () => {} },
    sessions: new Map<string, { name: string }>(), tabMeta: new Map(), order: [] as string[], mru: [] as string[],
    views: new Map(), ledgers: new Map(), liveAsks: new Map(), awaitingFull: new Set(), pendingFullWhy: new Map(),
    staleAnswerReasked: new Set(), resync: {}, skeletonTabs: {}, failedProvisionals: new Set<string>(),
    heldBack: new Map<string, string>(),   // the held sends' lines, per tab (an end retires its tab's with the draft)
    activeId: active, peekId: null, provisionalId: null, vanishedId: null, vanishedWhy: null, vanishedName: "", vanishedByDecline: false,
    focusAfterDismiss, stagedPosts,
    isProvisionalId: () => false,
    hostOf: (x: string) => { const i = x.indexOf(":"); return i > 0 ? x.slice(0, i) : ""; },
    heldHere: () => true,   // one column, no split: every tab is this page's
    routeUserMessage: (sid: string, text: string) => { posts.push({ sid, text }); },
    loadComposerFor: () => {},
    document: { getElementById: () => null },
  };
  const proxy = new Proxy(S, {
    has: (t, k) => k in t || !(k in globalThis),
    get: (t, k) => (k in t ? (t as any)[k] : (typeof k === "symbol" ? undefined : (() => undefined))),
    set: (t, k, v) => { (t as any)[k] = v; return true; },
  });
  const fns = (new Function("SCOPE", `with (SCOPE) { ${SRC}\n return { dismissSession, closeTabLocally, flushStaged, persistDrafts, stagedLabel, onFrame }; }`) as any)(proxy) as {
    dismissSession: (id: string, why: Why) => void; closeTabLocally: (id: string) => void;
    flushStaged: (sid: string, typed?: { text: string }) => number; persistDrafts: () => void;
    stagedLabel: (n: number, asking: boolean) => string; onFrame: (m: { type: string; id: string; hostDrop?: boolean }) => void;
  };
  const sessions = S.sessions as Map<string, { name: string }>, order = S.order as string[], mru = S.mru as string[];
  for (const [id, name] of [[WEB, "web"], [PEER, "api"]]) { sessions.set(id, { name }); order.push(id); }
  mru.push(active, active === WEB ? PEER : WEB);
  const drafts = S.drafts as Map<string, string>;
  if (from) {   // render.ts's load: the drafts and the stack come back from the persisted state
    for (const [k, v] of Object.entries((saved.drafts || {}) as Record<string, string>)) drafts.set(k, v);
    stagedMsgs.restore(saved.staged);
  } else {
    drafts.set(WEB, "half-typed reply");
    for (const it of WEB_STAGED) stagedMsgs.push(WEB, it);
    stagedMsgs.push(PEER, { text: "Keep this one for api.", cites: [] });
    fns.persistDrafts();
  }
  /** a sid's stack as a reload would restore it from the persisted state */
  const reloaded = (sid: string) => { const s = new StagedStack(); s.restore(saved.staged); return s.count(sid); };
  const revive = (id: string, name: string) => { sessions.set(id, { name }); order.push(id); };   // a revive keeps the sid
  const persisted = () => saved;
  return { S, drafts, stagedMsgs, posts, reloaded, revive, persisted, ...fns };
}

// Every kind of end, as this page meets it: the user's own End runs closeTabLocally (the tab's ✕ and the confirm's End
// button both do); every other end reaches the page as the owning kernel's `closed` frame.
const ENDS: { kind: string; active: string; end: (w: ReturnType<typeof world>) => void; back?: string }[] = [
  { kind: "the user's own End on the tab they are in", active: WEB, end: (w) => w.closeTabLocally(WEB) },
  { kind: "the user's own End on another tab", active: PEER, end: (w) => w.closeTabLocally(WEB) },
  { kind: "another session's romp end", active: PEER, end: (w) => w.onFrame({ type: "closed", id: WEB }) },
  { kind: "the session ending itself when idle, while the user reads it", active: WEB, end: (w) => w.onFrame({ type: "closed", id: WEB }) },
  { kind: "End pressed on another page", active: PEER, end: (w) => w.onFrame({ type: "closed", id: WEB }) },
  // an older client's closeTab on a session that is still running: the kernel ignores the hide but still broadcasts closed
  { kind: "a stale closeTab on a live session", active: WEB, end: (w) => w.onFrame({ type: "closed", id: WEB }), back: "its tab back on the next push" },
];

for (const { kind, active, end } of ENDS) test(`${kind}: the draft goes, the staged stack stays, in the page and in the persisted state`, () => {
  const w = world(active);
  end(w);
  assert.equal(w.drafts.has(WEB), false, kind + ": the draft goes with the ended session");
  assert.equal((w.S.sessions as Map<string, unknown>).has(WEB), false, kind + ": the tab is gone");
  assert.equal(w.stagedMsgs.count(WEB), 2, kind + ": the staged stack stays");
  assert.deepEqual(w.stagedMsgs.list(WEB), WEB_STAGED, kind + ": every item, quotes and all, in stage order");
  assert.equal(w.reloaded(WEB), 2, kind + ": …and so does the persisted copy a reload restores");
  assert.equal(w.stagedMsgs.count(PEER), 1, kind + ": another session's stack is untouched");
  assert.equal(w.reloaded(PEER), 1, kind);
  assert.deepEqual(w.posts, [], kind + ": nothing is sent by the end itself");
});

for (const { kind, active, end, back } of ENDS) test(`${kind}, then ${back ?? "Revive"}: the strip shows the staged stack and the next plain send carries it ahead of the typed message`, () => {
  const w = world(active);
  end(w);
  w.revive(WEB, "web");   // the same sid back on the strip, by a revive or (still live) the kernel's next push
  assert.equal(w.stagedLabel(w.stagedMsgs.count(WEB), false), "2 staged — sends with your next message", kind + ": the strip's line on revive");
  assert.equal(w.flushStaged(WEB, { text: "carry on" }), 2, kind + ": both staged items ride the next send");
  assert.deepEqual(w.posts, [{ sid: WEB, text: stagedRunBody(WEB_STAGED, { text: "carry on" }) }],
    kind + ": one message, the staged items in order and the typed message last");
  assert.ok(w.posts[0].text.endsWith("carry on") && w.posts[0].text.includes("> retry the request twice"), kind);
  assert.equal(w.stagedMsgs.count(WEB), 0, kind + ": sent, so the stack is empty");
  assert.equal(w.reloaded(WEB), 0, kind + ": …and so is the persisted copy");
});

test("End, a reload, then Revive: the stack comes back from the persisted state and goes with the next message", () => {
  const before = world(WEB);
  before.closeTabLocally(WEB);
  const after = world(PEER, before.persisted());   // a fresh page over what the ended one left behind
  assert.equal(after.drafts.has(WEB), false, "the draft did not survive the end");
  assert.equal(after.stagedMsgs.count(WEB), 2, "the staged stack did");
  after.revive(WEB, "web");
  assert.equal(after.stagedLabel(after.stagedMsgs.count(WEB), false), "2 staged — sends with your next message");
  after.flushStaged(WEB, { text: "carry on" });
  assert.deepEqual(after.posts, [{ sid: WEB, text: stagedRunBody(WEB_STAGED, { text: "carry on" }) }]);
});

test("a host drop or an omission keeps the stack, as it keeps the draft: the session comes back with both", () => {
  for (const why of ["hostDrop", "omitted"] as const) {
    const w = world(PEER);
    if (why === "hostDrop") w.onFrame({ type: "closed", id: WEB, hostDrop: true });
    else w.dismissSession(WEB, why);
    assert.equal(w.drafts.get(WEB), "half-typed reply", why);
    assert.equal(w.stagedMsgs.count(WEB), 2, why + " keeps the staged stack");
    assert.equal(w.reloaded(WEB), 2, why + " keeps the persisted copy");
  }
});
