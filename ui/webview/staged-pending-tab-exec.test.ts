// Messages STAGED on a new session's tab while it is still opening reach that session, EXECUTED (2026-10-23). A new
// session's tab is up the moment the create is asked for, before the kernel has minted the session's id, so it wears a
// provisional id of this page's own (provisional.ts), and what is staged on it is held under that id. When the session
// arrives, the provisional tab hands what it holds to the real one: the draft always did, the staged stack never did, so
// it stayed under an id nothing would show again and never went out (#362 deleted it there instead; #369 made staged
// messages outlive every end, which left this one place). Now the stack (and the attachments) go where the draft goes:
// onto the real session on arrival, onto the running session a create resolved to, onto the retry's tab after a folder
// question, exactly once. The create in flight survives a reload (the page's state names it and the boot puts its tab
// back, so the session's arrival still adopts it), and a create that fails keeps them on its tab, in the strip above the
// box, which the failure dialog names; the failed tab survives a reload too.
//
// openProvisional, dropProvisional, carryProvisionalBox, persistProvisionals, restoreProvisionals, adoptProvisional,
// resolveProvisionalToExisting, failProvisional, cancelProvisional, onCreateDirMissing, closeTabLocally, dismissSession,
// stashActiveDraft, flushStaged, persistDrafts and stagedLabel are lifted out of render.ts, transpiled at run time and run
// over a stub scope (the staged-on-end-exec.test.ts pattern), with the real StagedStack and stagedPosts, the real
// provisional.ts rules and the real focusAfterDismiss. The session frame's adoption line and the boot's reading of the
// persisted active tab are lifted from render.ts too. A function render.ts does not define (restoreProvisionals before
// this change) resolves to the stub scope's no-op, so the test fails on what the page does, not on a missing anchor. Synthetic sessions only
// (the notes-api demo's web and api).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack, stagedPosts, stagedRunBody, type StagedMsg } from "./staged-messages";
import { focusAfterDismiss } from "./pane-focus";
import { mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional } from "./provisional";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;

/** One top-level function of render.ts, from its header to the first closing brace at column 0; "" when render.ts has
 *  no such function and `optional` says that is a state to test, not a moved anchor. */
function lift(header: string, optional = false): string {
  const a = RENDER.indexOf(header);
  if (a < 0 && optional) return "";
  assert.ok(a > 0, `anchor moved: ${header.slice(0, 60)}`);
  assert.equal(RENDER.indexOf(header, a + 1), -1, `anchor not unique: ${header.slice(0, 60)}`);
  const b = RENDER.indexOf("\n}\n", a);
  return ts(RENDER.slice(a, b + 3));
}
// the session frame's adoption (upsert): a session this page has never seen, under the name the create asked for
const ADOPT_LINE = RENDER.match(/if \(adoptsProvisional\(existed, msg\.name, pendingNewSession\)\) \{\s*\n\s*adoptProvisional\(msg\.id\);\s*\n\s*\}/);
assert.ok(ADOPT_LINE, "the session frame's adoption line moved");
// the boot's reading of the persisted active tab: a provisional id is "gone" unless something puts its tab back
const BOOT_WANT = RENDER.match(/if \(wantActive && \(isSubId\(wantActive\) \|\| isProvisionalId\(wantActive\)\)\) \{ wantActiveGone = wantActive; wantActive = null; \}/);
assert.ok(BOOT_WANT, "the boot's persisted-active line moved");
const SRC = [
  lift("function openProvisional(req: CreateReq): void {"),
  lift("function dropProvisional(): {"),
  lift("function carryProvisionalBox(", true),
  lift("function persistProvisionals(): void {", true),
  lift("function restoreProvisionals(): void {", true),
  lift("function adoptProvisional(realId: string): void {"),
  lift("function resolveProvisionalToExisting(realId: string): void {"),
  lift("function failProvisional(why: string): void {"),
  lift("function cancelProvisional(): void {"),
  lift("function onCreateDirMissing(m: any): void {"),
  lift("function closeTabLocally(id: string): void {"),
  lift("function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>): void {"),
  lift("function stashActiveDraft(id: string): void {"),
  lift("function flushStaged(sid: string,"),
  lift("function persistDrafts(): void {"),
  lift("function stagedLabel(n: number, asking: boolean): string {"),
  ts(`function adoptLine(msg: any, existed: boolean): void { ${ADOPT_LINE![0]} }`),
  ts(`function bootWant(): void { ${BOOT_WANT![0]} }`),
].join("\n");

const WEB = "5b0e2c4a-8d71-4f3e-9c26-1a7f0d3e5b91", REAL = "7d4e1f20-3b6a-4c58-9e17-2a0c5b8f6d93";
const NAMESAKE = "c3f81a26-4e9d-4b70-8a15-6d2e9f0b7c48";
const REQ = { name: "api", backend: "sdk", dir: "", host: "" };
const ITEM: StagedMsg = { text: "Start with the pagination bug in the list endpoint.", cites: [] };
type Post = { sid: string; text: string };
type Fns = {
  openProvisional: (req: typeof REQ) => void; adoptProvisional: (id: string) => void; resolveProvisionalToExisting: (id: string) => void;
  failProvisional: (why: string) => void; onCreateDirMissing: (m: Record<string, unknown>) => void; closeTabLocally: (id: string) => void;
  restoreProvisionals: () => void; persistProvisionals: () => void; flushStaged: (sid: string, typed?: { text: string }) => number; persistDrafts: () => void;
  stagedLabel: (n: number, asking: boolean) => string; adoptLine: (msg: { id: string; name: string }, existed: boolean) => void; bootWant: () => void;
};

/** A chat page. `from` boots it from a persisted state, as a reload does: the drafts, the attachments and the staged
 *  stacks come back, the persisted active tab is read, then the boot's restore runs, all before any kernel frame. */
function world(from?: Record<string, unknown>) {
  let saved: Record<string, unknown> = from ? { ...from } : {};
  const posts: Post[] = [], wire: Record<string, unknown>[] = [], confirms: { title: string; body: string }[] = [];
  const bubbles: Post[] = [];
  const box = { value: "", focus() {}, blur() {} };
  let strip: string | null = null;   // the session whose stack the strip above the box shows
  const stagedMsgs = new StagedStack();
  const S: Record<string, any> = {
    drafts: new Map<string, string>(), composerCitations: new Map(), composerEdits: new Map(), composerFiles: new Map<string, string[]>(),
    stagedMsgs, cmtStaged: new StagedStack(), pendingShips: new Map(), closingTabs: new Map<string, number>(), pendingSent: new Map(),
    vscodeApi: { getState: () => saved, setState: (s: Record<string, unknown>) => { saved = s; }, postMessage: (m: Record<string, unknown>) => { wire.push(m); } },
    sessions: new Map<string, any>(), tabMeta: new Map(), order: [] as string[], mru: [] as string[],
    views: new Map(), ledgers: new Map(), liveAsks: new Map(), awaitingFull: new Set(), pendingFullWhy: new Map(),
    staleAnswerReasked: new Set(), resync: {}, skeletonTabs: {}, failedProvisionals: new Set<string>(), provisionalQueue: [] as string[],
    activeId: null, peekId: null, provisionalId: null, pendingNewSession: null, provisionalTags: [], provisionalTimer: undefined,
    pendingCarry: "", pendingCarryStaged: [], pendingCarryFiles: [], lastCreate: null,
    wantActive: null, wantActiveGone: null, vanishedId: null, vanishedWhy: null, vanishedName: "", vanishedByDecline: false,
    PROVISIONAL_WAIT_MS: 90_000,
    focusAfterDismiss, stagedPosts, mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional,
    isSubId: () => false,
    hostOf: (x: string) => { const i = x.indexOf(":"); return i > 0 ? x.slice(0, i) : ""; },
    heldHere: () => true,   // one column, no split: every tab is this page's
    setTimeout: () => 0, clearTimeout: () => {},   // the 90 s backstop is armed, never fired: a failure is driven explicitly
    routeUserMessage: (sid: string, text: string) => { posts.push({ sid, text }); },
    registerOptimistic: (sid: string, text: string) => { bubbles.push({ sid, text }); },
    isClearCmd: (t: string) => /^\/clear(\s|$)/.test(t.trim()),
    showConfirm: (title: string, body: string) => { confirms.push({ title, body }); },
    createDirPrompt: () => "That folder isn't there.",
    quietly: (fn: () => void) => fn(),
    renderStagedStrip: (id: string | null) => { strip = id; },
    loadComposerFor: (id: string | null) => { box.value = (id && S.drafts.get(id)) || ""; strip = id; },
    document: { getElementById: (k: string) => (k === "composer-input" ? box : null) },
  };
  // the switch, as render.ts's setActive does it for the box: the leaving tab's text stashed, the entering tab's draft
  // and staged strip shown, the choice persisted (persistActive), the drafts persisted
  S.setActive = (id: string) => {
    if (S.activeId === id) return;
    if (S.activeId) { if (box.value) S.drafts.set(S.activeId, box.value); else S.drafts.delete(S.activeId); }
    box.value = S.drafts.get(id) ?? "";
    S.activeId = id; S.wantActive = null; S.wantActiveGone = null;
    strip = id;
    saved = { ...saved, activeId: id };
    fns.persistDrafts();
  };
  const proxy = new Proxy(S, {
    has: (t, k) => k in t || !(k in globalThis),
    get: (t, k) => (k in t ? (t as any)[k] : (typeof k === "symbol" ? undefined : (() => undefined))),
    set: (t, k, v) => { (t as any)[k] = v; return true; },
  });
  const fns = (new Function("SCOPE", `with (SCOPE) { ${SRC}\n return { openProvisional, adoptProvisional, resolveProvisionalToExisting,
    failProvisional, onCreateDirMissing, closeTabLocally, restoreProvisionals, persistProvisionals,
    flushStaged, persistDrafts, stagedLabel, adoptLine, bootWant }; }`) as any)(proxy) as Fns;
  if (from) {   // render.ts's load, in its order: the maps from the persisted state, the active tab read, then the boot's restore
    for (const [k, v] of Object.entries((saved.drafts || {}) as Record<string, string>)) S.drafts.set(k, v);
    for (const [k, v] of Object.entries((saved.files || {}) as Record<string, string[]>)) S.composerFiles.set(k, v);
    stagedMsgs.restore(saved.staged);
    S.wantActive = (saved.activeId as string) || null;
    fns.bootWant();
    fns.restoreProvisionals();
  }
  /** a session frame: the session lands on the strip, then the adoption line runs (a fresh page has seen nothing) */
  const arrive = (id: string, name: string, existed = false) => {
    if (!S.sessions.has(id)) { S.sessions.set(id, { id, name, events: [], status: { state: "idle" } }); S.order.push(id); }
    fns.adoptLine({ id, name }, existed);
  };
  /** Cmd/Ctrl+Enter on what is in the box, as setupComposer's stageComposer does it */
  const stage = (text: string) => { box.value = ""; stagedMsgs.push(S.activeId, { text, cites: [] }); S.drafts.delete(S.activeId); fns.persistDrafts(); strip = S.activeId; };
  const type = (text: string) => { box.value = text; S.drafts.set(S.activeId, text); fns.persistDrafts(); };   // the input handler keeps drafts current
  /** what the strip above the box reads, for the session it shows */
  const stripLine = () => (strip && stagedMsgs.count(strip) ? fns.stagedLabel(stagedMsgs.count(strip), false) : null);
  /** a sid's stack as the persisted state holds it, which is what a reload restores */
  const persistedCount = (sid: string) => { const s = new StagedStack(); s.restore(saved.staged); return s.count(sid); };
  return { S, box, posts, wire, confirms, bubbles, stagedMsgs, arrive, stage, type, stripLine, strip: () => strip, persistedCount,
           persisted: () => saved, ...fns };
}

/** A page with web open, then a new session called api asked for: its tab is up, opening, and active. */
function opening() {
  const w = world();
  w.arrive(WEB, "web");
  w.S.setActive(WEB);
  w.openProvisional(REQ);
  const prov = w.S.provisionalId as string;
  assert.ok(isProvisionalId(prov) && w.S.activeId === prov, "the new session's tab is up and active before the kernel answers");
  return { w, prov };
}

test("a message staged while the new session opens is the real session's when it arrives: the strip shows 1 staged and the next send carries it", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type("Then tell me what you changed.");
  assert.equal(w.stripLine(), "1 staged — sends with your next message", "staged on the opening tab");
  w.arrive(REAL, "api");
  assert.equal(w.S.activeId, REAL, "the real session takes the tab");
  assert.equal(w.strip(), REAL, "the strip shows the real session's stack");
  assert.equal(w.stripLine(), "1 staged — sends with your next message", "…and the item staged while it opened is on it");
  assert.equal(w.stagedMsgs.count(prov), 0, "nothing is left under the provisional id");
  assert.equal(w.persistedCount(REAL), 1, "the persisted state holds it under the real id…");
  assert.equal(w.persistedCount(prov), 0, "…and not under the provisional one");
  assert.equal(w.box.value, "Then tell me what you changed.", "the draft came across as it always did");
  assert.deepEqual(w.persisted().provisionals, [], "no create left in flight");
  assert.equal(w.flushStaged(REAL, { text: "Then tell me what you changed." }), 1, "the next send releases it");
  assert.deepEqual(w.posts, [{ sid: REAL, text: stagedRunBody([ITEM], { text: "Then tell me what you changed." }) }],
    "one message to the real session: the staged item first, the typed message last");
  assert.equal(w.stagedMsgs.count(REAL), 0, "sent, so the stack is empty");
});

test("a first session, with no other tab on the strip: its box and its strip get the draft and the staged message", () => {
  // the provisional tab's dismissal reselects the strip's only other tab, the real one, before the adoption switches to it
  const w = world();
  w.openProvisional(REQ);
  w.stage(ITEM.text);
  w.type("Then tell me what you changed.");
  w.arrive(REAL, "api");
  assert.equal(w.S.activeId, REAL);
  assert.equal(w.box.value, "Then tell me what you changed.", "the draft is in the box, not only in the map a switch-away would overwrite");
  assert.equal(w.strip(), REAL);
  assert.equal(w.stripLine(), "1 staged — sends with your next message");
});

test("the move happens exactly once: a later frame of the session, another new session under the same name and a reload move nothing again", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.arrive(REAL, "api");
  w.arrive(REAL, "api", true);   // the session's next full frame
  w.arrive(NAMESAKE, "api");         // somebody else's new session under the same name, after the create resolved
  assert.equal(w.stagedMsgs.count(REAL), 1, "still one item on the real session");
  assert.equal(w.stagedMsgs.count(NAMESAKE), 0, "nothing for the namesake");
  assert.equal(w.stagedMsgs.count(prov), 0);
  const r = world(w.persisted());
  assert.equal(r.S.sessions.has(prov), false, "no provisional tab comes back: the create resolved");
  assert.equal(r.S.provisionalId, null);
  assert.equal(r.stagedMsgs.count(REAL), 1, "the reloaded page holds the item once, under the real id");
  r.arrive(REAL, "api");
  assert.equal(r.stagedMsgs.count(REAL), 1, "the session's frame after the reload adds nothing");
});

test("a reload while the session opens: the tab comes back with what was staged and typed, and the session's arrival hands it over", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type("Then tell me what you changed.");
  const r = world(w.persisted());   // the page reloads before the kernel's answer reaches it
  assert.equal(r.S.sessions.get(prov)?.status?.state, "opening", "the new session's tab is back, still opening");
  assert.equal(r.S.provisionalId, prov, "…and is the create in flight, so the session's frame adopts it");
  assert.equal(r.S.activeId, prov, "it was the tab on screen, so it is again (not a \"gone\" tab)");
  assert.equal(r.stripLine(), "1 staged — sends with your next message", "its strip shows the staged item");
  assert.equal(r.box.value, "Then tell me what you changed.", "its box holds the draft");
  r.arrive(WEB, "web");
  r.arrive(REAL, "api");
  assert.equal(r.S.activeId, REAL);
  assert.equal(r.strip(), REAL);
  assert.equal(r.stripLine(), "1 staged — sends with your next message", "the real tab shows the item staged before the reload");
  assert.equal(r.stagedMsgs.count(prov), 0);
  assert.equal(r.box.value, "Then tell me what you changed.");
  r.flushStaged(REAL, { text: "Then tell me what you changed." });
  assert.deepEqual(r.posts, [{ sid: REAL, text: stagedRunBody([ITEM], { text: "Then tell me what you changed." }) }], "and the next send carries it");
});

test("a reload while the session opens keeps the sends it held: they come back as text in the box, which goes to the session, never sent on their own", () => {
  const { w, prov } = opening();
  // the provisional branch of the send path, as written: the text queued, its bubble, the box cleared, the state persisted
  const branch = RENDER.slice(RENDER.indexOf("        provisionalQueue.push(text);\n        registerOptimistic(sid,"), RENDER.indexOf("        clearBox();\n        return;\n      }\n      lastSent.set("));
  assert.ok(branch.includes("persistProvisionals();"), "the provisional send path writes the held send to the page's state");
  w.S.provisionalQueue.push("What does the list endpoint return today?");
  w.type("Then tell me what you changed.");
  w.persistProvisionals();
  const r = world(w.persisted());
  assert.equal(r.S.activeId, prov);
  assert.deepEqual(r.S.provisionalQueue, [], "not held to be sent on its own after the reload…");
  assert.equal(r.box.value, "What does the list endpoint return today?\n\nThen tell me what you changed.",
    "…but back in the tab's box, ahead of the draft");
  assert.equal(world(r.persisted()).box.value, "What does the list endpoint return today?\n\nThen tell me what you changed.",
    "once: a second reload finds it in the draft, not in the held sends again");
  r.arrive(REAL, "api");
  assert.deepEqual(r.wire.filter((m) => m.type === "sendMessage"), [], "the adoption sends nothing the user has not sent again");
  assert.equal(r.box.value, "What does the list endpoint return today?\n\nThen tell me what you changed.", "the real session's box holds it");
  assert.deepEqual(w.persisted().provisionals, [{ id: prov, name: "api", tags: [], queued: ["What does the list endpoint return today?"] }],
    "(the page's state named the held send before the reload)");
});

test("a create that fails keeps the staged messages on its tab, says so, and keeps them across a reload; its ✕ discards them with the tab", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.failProvisional("The folder could not be read.");
  assert.equal(w.S.activeId, prov, "the failed tab is foregrounded");
  assert.equal(w.stripLine(), "1 staged — sends with your next message", "its strip still shows the staged item");
  assert.equal(w.confirms.length, 1);
  assert.match(w.confirms[0].body, /^The folder could not be read\.\n\nWhat you staged is still staged above this tab's message box\.$/,
    "the dialog names them, as it names the typed text");
  assert.equal(w.persistedCount(prov), 1, "kept in the persisted state");
  const r = world(w.persisted());
  assert.equal(r.S.sessions.get(prov)?.status?.state, "closed", "after a reload the failed tab is back, failed");
  assert.ok(r.S.failedProvisionals.has(prov));
  assert.equal(r.S.provisionalId, null, "not a create in flight: nothing will adopt it");
  assert.equal(r.S.activeId, prov);
  assert.equal(r.stripLine(), "1 staged — sends with your next message", "with its staged item in the strip");
  r.closeTabLocally(prov);   // the failed tab's ✕: a local discard of the tab and everything it held
  assert.equal(r.stagedMsgs.count(prov), 0);
  assert.equal(r.persistedCount(prov), 0, "nothing left behind under an id no session will ever have");
  assert.deepEqual(r.persisted().provisionals, []);
  assert.equal(world(r.persisted()).S.sessions.has(prov), false, "and the next reload brings nothing back");
});

test("a create that fails with typed text and staged messages names both", () => {
  const { w } = opening();
  w.stage(ITEM.text);
  w.type("Then tell me what you changed.");
  w.failProvisional("The folder could not be read.");
  assert.match(w.confirms[0].body, /What you typed is in this tab's message box, and what you staged is still staged above it\.$/);
  assert.equal(w.box.value, "Then tell me what you changed.");
});

test("a create answered by a session that already runs under that name hands the staged messages to it, after its own", () => {
  const w = world();
  w.arrive(REAL, "api");
  w.S.stagedMsgs.push(REAL, { text: "Already staged on the running api.", cites: [] });
  w.S.setActive(REAL);
  w.openProvisional(REQ);
  const prov = w.S.provisionalId as string;
  w.stage(ITEM.text);
  w.resolveProvisionalToExisting(REAL);   // the kernel focused the running api instead of creating one
  assert.deepEqual(w.stagedMsgs.list(REAL).map((m) => m.text), ["Already staged on the running api.", ITEM.text]);
  assert.equal(w.stagedMsgs.count(prov), 0);
  assert.equal(w.persistedCount(REAL), 2);
  assert.deepEqual(w.posts, [], "held, not sent");
});

test("a folder question carries the staged messages to the retry's tab, and from there to the session", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.onCreateDirMissing({ name: "api", dir: "~/notes-api" });   // the create asked about its folder: the tab is retired
  assert.equal(w.S.sessions.has(prov), false);
  w.openProvisional(REQ);   // "Create it and start" sends the same create again
  const retry = w.S.provisionalId as string;
  assert.notEqual(retry, prov);
  assert.equal(w.stagedMsgs.count(retry), 1, "the retry's tab holds what the first one had staged");
  w.arrive(REAL, "api");
  assert.equal(w.stagedMsgs.count(REAL), 1, "and the session gets it");
  assert.equal(w.stripLine(), "1 staged — sends with your next message");
});

test("the boot restores a create in flight before it tells the kernel it is ready", () => {
  // the first frame after the ready may be the created session's: its tab has to be there to adopt it
  assert.match(RENDER, /\nrestoreProvisionals\(\);\nif \(vscodeApi\) vscodeApi\.postMessage\(\{ type: "ready", proto: 2 \}\);/);
});
