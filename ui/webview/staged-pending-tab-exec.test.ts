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
// The EDGES of that flow, executed below the first set (2026-10-23, the review of #371): whatever a new session's tab
// holds is never dropped without a word. A second create while one is opening KEEPS the first one's tab, everything on it,
// and its session adopts it by name when it arrives. Enter on the opening tab holds the staged messages with the typed
// text, as one send, as Enter does on any tab. The folder question keeps the tab (a reload, an Esc, or a create under
// another name loses nothing and takes nothing), and its retry opens on the same tab. A kept tab's ✕ asks before it
// discards staged messages or text, and a session arriving late under its name adopts it. Held sends a reload put back
// in the box say so in a line above it, and a create holding sends holds the dashboard's reload. A create saved longer
// ago than its backstop comes back kept, not opening, and an adoption after a reload joins the session's own draft.
//
// openProvisional, dropProvisional, carryProvisionalBox, persistProvisionals, restoreProvisionals, adoptProvisional,
// resolveProvisionalToExisting, failProvisional, cancelProvisional, onCreateDirMissing, closeTabLocally, dismissSession,
// stashActiveDraft, flushStaged, persistDrafts and stagedLabel are lifted out of render.ts, transpiled at run time and run
// over a stub scope (the staged-on-end-exec.test.ts pattern), with the real StagedStack and stagedPosts, the real
// provisional.ts rules, the real reload-hold.ts and the real focusAfterDismiss; so are the edges' own (keepProvisional,
// takeProvisionalTab, adoptKeptProvisionals, queueProvisionalSend, closeKeptTab and its title, the held sends' note,
// the reload hold, startCreate), and the opening tab's ✕ (closeOpeningTab) and the held sends' saved lines (restoreHeldBack,
// giveHeldBack) since the review of #373. The session frame's adoption lines, the boot's reading of the persisted active tab, the
// send's provisional branch and the composer's clearBox are lifted from render.ts too. A function render.ts does not
// define (restoreProvisionals before #371, the edges' functions before 2026-10-23) resolves to the stub scope's no-op,
// so the test fails on what the page does, not on a missing anchor. Synthetic sessions only (the notes-api demo's web,
// api and tests).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack, stagedPosts, stagedRunBody, quoteReplyBody, type StagedMsg } from "./staged-messages";
import { reloadHoldReason } from "./reload-hold";
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
// the session frame's adoption (upsert): a session this page has never seen, under the name the create asked for, and
// (since 2026-10-23) a kept tab under its name on the same arrival
const ADOPT_LINE = RENDER.match(/if \(adoptsProvisional\(existed, msg\.name, pendingNewSession\)\) \{\s*\n\s*adoptProvisional\(msg\.id\);\s*\n\s*\}(\s*\n(\s*\/\/[^\n]*\n)*\s*if \(!existed\) adoptKeptProvisionals\(msg\.id, msg\.name\);)?/);
assert.ok(ADOPT_LINE, "the session frame's adoption line moved");
// the send's provisional branch (deliver, inside setupComposer): from its test to the first step of a send that passed it
const SEND_A = RENDER.indexOf("      if (isProvisionalId(sid)) {\n        // a "), SEND_B = RENDER.indexOf("      lastSent.set(activeId, text);", SEND_A);
assert.ok(SEND_A > 0 && SEND_B > SEND_A, "the send's provisional branch moved");
// the composer's one clear path (setupComposer's clearBox)
const CLEAR_A = RENDER.indexOf("  const clearBox = () => {"), CLEAR_B = RENDER.indexOf("  clearComposerBox = clearBox;", CLEAR_A);
assert.ok(CLEAR_A > 0 && CLEAR_B > CLEAR_A, "clearBox moved");
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
  lift("function startCreate(req: CreateReq, mkdir = false): void {"),
  lift("function closeTabLocally(id: string): void {"),
  lift("function keepProvisional(", true),
  lift("function takeProvisionalTab(", true),
  lift("function adoptKeptProvisionals(", true),
  lift("function queueProvisionalSend(", true),
  lift("function closeKeptTab(", true),
  lift("function closeOpeningTab(", true),
  lift("function tabDiscardTitle(", true),
  lift("function discardKeptTab(", true),
  lift("function heldBackLine(", true),
  lift("function restoreHeldBack(", true),
  lift("function clearHeldBackNote(", true),
  lift("function giveHeldBack(", true),
  lift("function createHoldHost(", true),
  lift("function paneHoldReason(", true),
  lift("function endReloadHoldIfIdle(): void {"),
  lift("function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>): void {"),
  lift("function stashActiveDraft(id: string): void {"),
  lift("function flushStaged(sid: string,"),
  lift("function persistDrafts(): void {"),
  lift("function stagedLabel(n: number, asking: boolean): string {"),
  ts(`function adoptLine(msg: any, existed: boolean): void { ${ADOPT_LINE![0]} }`),
  ts(`function bootWant(): void { ${BOOT_WANT![0]} }`),
  ts(`function deliverProvisional(sid: string, text: string, attached: string[]): void {\n${RENDER.slice(SEND_A, SEND_B)}}`),
  ts(RENDER.slice(CLEAR_A, CLEAR_B)),
].join("\n");

const WEB = "5b0e2c4a-8d71-4f3e-9c26-1a7f0d3e5b91", REAL = "7d4e1f20-3b6a-4c58-9e17-2a0c5b8f6d93";
const NAMESAKE = "c3f81a26-4e9d-4b70-8a15-6d2e9f0b7c48";
const TYPED = "Then tell me what you changed.", HELD = "What does the list endpoint return today?";
const REQ = { name: "api", backend: "sdk", dir: "", host: "" };
const ITEM: StagedMsg = { text: "Start with the pagination bug in the list endpoint.", cites: [] };
type Post = { sid: string; text: string };
type Req = { name: string; backend: string; dir: string; host: string };
type Fns = {
  openProvisional: (req: Req) => void; adoptProvisional: (id: string) => void; resolveProvisionalToExisting: (id: string) => void;
  failProvisional: (why: string) => void; onCreateDirMissing: (m: Record<string, unknown>) => void; closeTabLocally: (id: string) => void;
  restoreProvisionals: () => void; persistProvisionals: () => void; flushStaged: (sid: string, typed?: { text: string }) => number; persistDrafts: () => void;
  stagedLabel: (n: number, asking: boolean) => string; adoptLine: (msg: { id: string; name: string }, existed: boolean) => void; bootWant: () => void;
  deliverProvisional: (sid: string, text: string, attached: string[]) => void; clearBox: () => void;
  tabDiscardTitle: (name: string, staged: number, text: boolean, files: boolean, held?: number, opening?: boolean) => string;
  heldBackLine: (name: string, canSend?: boolean) => string; restoreHeldBack: (saved: unknown) => void; clearHeldBackNote: (sid: string | null) => void;
  closeOpeningTab: (id: string) => boolean;
  paneHoldReason: () => string; startCreate: (req: Req, mkdir?: boolean) => void;
};
type Confirm = { title: string; body: string; buttons: { label: string; value: string }[]; cb: (v: string | null) => void };

/** A chat page. `from` boots it from a persisted state, as a reload does: the drafts, the attachments and the staged
 *  stacks come back, the persisted active tab is read, then the boot's restore runs, all before any kernel frame. */
function world(from?: Record<string, unknown>) {
  let saved: Record<string, unknown> = from ? { ...from } : {};
  const posts: Post[] = [], wire: Record<string, unknown>[] = [], confirms: Confirm[] = [], toasts: string[] = [];
  const bubbles: Post[] = [];
  const box = { value: "", style: {} as Record<string, string>, focus() {}, blur() {} };
  // the timers the page arms (the create's 90 s backstop): kept, never run on their own, run by fireTimers when a test says
  // the backstop's time is up; a cleared one is gone
  const timers = new Map<number, { fn: () => void; ms: number }>();
  let tid = 0, ended = 0;
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
    supersededProvisionals: new Set<string>(), provisionalStartedAt: 0, heldBack: new Map<string, string>(), shipGateSid: null,
    sendOnShip: new Set<string>(), histWalk: new Map(), draftStartedAt: new Map(), composerManualH: null,
    window: { __rompReload: { ended: () => { ended++; } }, __rompFed: undefined as unknown },
    focusAfterDismiss, stagedPosts, quoteReplyBody, reloadHoldReason, mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional,
    isSubId: () => false,
    hostOf: (x: string) => { const i = x.indexOf(":"); return i > 0 ? x.slice(0, i) : ""; },
    heldHere: () => true,   // one column, no split: every tab is this page's
    setTimeout: (fn: () => void, ms: number) => { timers.set(++tid, { fn, ms }); return tid; },
    clearTimeout: (h: number) => { timers.delete(h); },
    routeUserMessage: (sid: string, text: string) => { posts.push({ sid, text }); },
    registerOptimistic: (sid: string, text: string) => { bubbles.push({ sid, text }); },
    isClearCmd: (t: string) => /^\/clear(\s|$)/.test(t.trim()),
    showConfirm: (title: string, body: string, buttons: Confirm["buttons"], cb: Confirm["cb"]) => { confirms.push({ title, body, buttons, cb }); },
    ephemeralWarnToast: (msg: string) => { toasts.push(msg); },
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
    flushStaged, persistDrafts, stagedLabel, adoptLine, bootWant, deliverProvisional, clearBox,
    tabDiscardTitle, heldBackLine, restoreHeldBack, clearHeldBackNote, closeOpeningTab, paneHoldReason, startCreate }; }`) as any)(proxy) as Fns;
  S.ta = box;   // clearBox's own name for the box
  if (from) {   // render.ts's load, in its order: the maps from the persisted state, the active tab read, then the boot's restore
    for (const [k, v] of Object.entries((saved.drafts || {}) as Record<string, string>)) S.drafts.set(k, v);
    fns.restoreHeldBack(saved.heldBack);
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
  /** Enter on the box, as setupComposer's sendComposer does it for a tab with no session behind it (deliver's branch) */
  const enter = () => fns.deliverProvisional(S.activeId, box.value.trim(), []);
  /** the backstop's time is up: every timer still armed runs */
  const fireTimers = () => { const due = [...timers.values()]; timers.clear(); for (const t of due) t.fn(); };
  return { S, box, posts, wire, confirms, toasts, bubbles, stagedMsgs, arrive, stage, type, stripLine, strip: () => strip, persistedCount,
           persisted: () => saved, enter, fireTimers, timers, ended: () => ended, ...fns };
}

/** A page with web open, then a new session called api asked for (the picker's create): its tab is up, opening, and active. */
function opening() {
  const w = world();
  w.arrive(WEB, "web");
  w.S.setActive(WEB);
  w.startCreate(REQ);
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
  // the provisional branch of the send path writes the held send to the page's state (queueProvisionalSend since
  // 2026-10-23; executed by the Enter tests below)
  const branch = RENDER.slice(RENDER.indexOf("function queueProvisionalSend("), RENDER.indexOf("\n}\n", RENDER.indexOf("function queueProvisionalSend(")));
  assert.ok(branch.includes("provisionalQueue.push(body);") && branch.includes("persistProvisionals();"), "the provisional send path writes the held send to the page's state");
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
  assert.deepEqual(w.persisted().provisionals, [{ id: prov, name: "api", tags: [], queued: ["What does the list endpoint return today?"], t: w.S.provisionalStartedAt }],
    "(the page's state named the held send, and when the create was asked for, before the reload)");
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
  r.closeTabLocally(prov);   // the failed tab's ✕: a local discard of the tab and everything it held, once the user says so
  r.confirms[r.confirms.length - 1].cb("discard");
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

test("a folder question keeps the tab with what it holds, its retry opens on that same tab, and from there it goes to the session", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  w.onCreateDirMissing({ name: "api", dir: "~/notes-api" });   // the create asked about its folder
  const q = w.confirms[0];
  assert.equal(q.title, "That folder isn't there");
  assert.equal(w.S.activeId, prov, "the question is over the tab it is about, which stays");
  assert.ok(w.S.failedProvisionals.has(prov), "kept, as a failed create's tab is, until the answer");
  assert.equal(w.S.provisionalId, null, "no create in flight while the question is up");
  q.cb("create");   // "Create it and start": the same create, sent again with the folder made
  assert.deepEqual(w.wire.filter((m) => m.type === "createSession").map((m) => [m.name, m.mkdir]), [["api", undefined], ["api", true]]);
  assert.equal(w.S.provisionalId, prov, "the retry opens on the same tab, not beside it");
  assert.equal(w.S.sessions.get(prov)?.status?.state, "opening");
  assert.ok(!w.S.failedProvisionals.has(prov), "no longer kept: it is the create in flight again");
  assert.equal(w.stagedMsgs.count(prov), 1, "with its staged message");
  assert.equal(w.box.value, TYPED, "and its text");
  assert.equal(w.S.order.filter((id: string) => id === prov).length, 1, "one tab on the strip");
  w.arrive(REAL, "api");
  assert.equal(w.stagedMsgs.count(REAL), 1, "and the session gets it");
  assert.equal(w.stripLine(), "1 staged — sends with your next message");
  assert.equal(w.box.value, TYPED);
});

test("the boot restores a create in flight before it tells the kernel it is ready", () => {
  // the first frame after the ready may be the created session's: its tab has to be there to adopt it
  assert.match(RENDER, /\nrestoreProvisionals\(\);\nif \(vscodeApi\) vscodeApi\.postMessage\(\{ type: "ready", proto: 2 \}\);/);
});

// ── the edges (2026-10-23): whatever a new session's tab holds is never dropped without a word ──────────────────────────
const TESTS_REQ = { name: "tests", backend: "sdk", dir: "", host: "" };
const COMMAND: StagedMsg = { text: "/compact", cites: [] };
const kept = (w: ReturnType<typeof world>, id: string) => ({
  state: w.S.sessions.get(id)?.status?.state, kept: w.S.failedProvisionals.has(id), replaced: w.S.supersededProvisionals.has(id),
  staged: w.stagedMsgs.list(id).map((m) => m.text), draft: w.S.drafts.get(id) ?? "",
});

test("1: a second new session started while the first is opening keeps the first one's tab with everything on it, and the first session adopts it when it arrives", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.S.provisionalQueue.push(HELD); w.persistProvisionals();   // a message sent on the tab, held for the session
  w.type(TYPED);
  w.startCreate(TESTS_REQ);   // + again, before api's session is up
  const second = w.S.provisionalId as string;
  assert.ok(second && second !== prov, "the new create is the one in flight");
  assert.equal(w.S.activeId, second, "and its tab is on screen");
  assert.deepEqual(kept(w, prov), { state: "closed", kept: true, replaced: true, staged: [ITEM.text], draft: HELD + "\n\n" + TYPED },
    "api's tab stays, kept: its staged message, and the held send back in its box ahead of the draft");
  assert.deepEqual(w.wire.filter((m) => m.type === "cancelCreate"), [], "api's create is not cancelled: the kernel goes on with it");
  assert.deepEqual(w.confirms, [], "nothing failed, so no dialog");
  assert.deepEqual(w.persisted().provisionals, [
    { id: second, name: "tests", tags: [], queued: [], t: w.S.provisionalStartedAt },
    { id: prov, name: "api", failed: true, superseded: true }], "both tabs ride the page's state");
  // a reload keeps the kept tab as it was
  const r = world(w.persisted());
  assert.deepEqual(kept(r, prov), { state: "closed", kept: true, replaced: true, staged: [ITEM.text], draft: HELD + "\n\n" + TYPED });
  // api's session arrives while tests is still opening: it takes the kept tab, in the background
  w.arrive(REAL, "api");
  assert.equal(w.S.sessions.has(prov), false, "the kept tab gives way to its session");
  assert.deepEqual(w.stagedMsgs.list(REAL).map((m) => m.text), [ITEM.text], "the session has the staged message");
  assert.equal(w.S.drafts.get(REAL), HELD + "\n\n" + TYPED, "and the text, in its box");
  assert.equal(w.persistedCount(REAL), 1);
  assert.equal(w.S.activeId, second, "an arrival in the background moves nobody");
  assert.equal(w.S.provisionalId, second, "tests is still the create in flight");
  assert.deepEqual(w.wire.filter((m) => m.type === "sendMessage"), [], "nothing is sent on its own");
  assert.deepEqual(w.persisted().provisionals, [{ id: second, name: "tests", tags: [], queued: [], t: w.S.provisionalStartedAt }]);
});

test("1: a kept tab's send is refused with a word, its text left in the box", () => {
  const { w, prov } = opening();
  w.type(TYPED);
  w.startCreate(TESTS_REQ);
  w.S.setActive(prov);
  w.enter();
  assert.deepEqual(w.toasts, ["“api” hasn't opened, so there's nowhere to send this yet. It stays in the box, and goes to the session if it opens."]);
  assert.deepEqual(w.S.provisionalQueue, [], "nothing queued onto the create in flight, which is another session's");
  assert.equal(w.box.value, TYPED, "the text stays where it was typed");
});

test("2: Enter on the opening tab holds the staged messages with the typed text, in order, as one send, and the session gets that one message", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  w.enter();
  const body = stagedRunBody([ITEM], { text: TYPED });
  assert.deepEqual(w.S.provisionalQueue, [body], "one held send: the staged item first, the typed message last");
  assert.deepEqual(w.bubbles, [{ sid: prov, text: body }], "one bubble for it");
  assert.equal(w.stagedMsgs.count(prov), 0, "nothing left staged to wait for the message after");
  assert.equal(w.persistedCount(prov), 0);
  assert.equal(w.box.value, "", "the box is cleared, as a send clears it");
  w.arrive(REAL, "api");
  assert.deepEqual(w.wire.filter((m) => m.type === "sendMessage").map((m) => m.text), [body], "the session receives the one message");
  assert.equal(w.stagedMsgs.count(REAL), 0, "and nothing stays staged on it");
  assert.equal(w.stripLine(), null);
});

test("2: a staged slash command keeps its place and goes on its own, as on any tab", () => {
  const { w } = opening();
  w.stage(ITEM.text);
  w.stage(COMMAND.text);
  w.type(TYPED);
  w.enter();
  assert.deepEqual(w.S.provisionalQueue, [stagedRunBody([ITEM]), "/compact", TYPED], "the item, then the command alone, then the typed message");
});

test("3: Esc on the folder question leaves the tab kept with what it holds, and a create under another name takes none of it", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  w.onCreateDirMissing({ name: "api", dir: "~/notes-api" });
  w.confirms[0].cb(null);   // Esc, or a click on the backdrop
  assert.deepEqual(kept(w, prov), { state: "closed", kept: true, replaced: false, staged: [ITEM.text], draft: TYPED },
    "the tab is still there with its staged message and its text");
  w.startCreate(TESTS_REQ);
  const other = w.S.provisionalId as string;
  assert.notEqual(other, prov);
  assert.equal(w.stagedMsgs.count(other), 0, "the unrelated create's tab gets no staged message");
  assert.equal(w.box.value, "", "…and no text");
  assert.deepEqual(kept(w, prov), { state: "closed", kept: true, replaced: false, staged: [ITEM.text], draft: TYPED }, "they stay on api's tab");
  w.arrive(NAMESAKE, "tests");
  assert.equal(w.stagedMsgs.count(NAMESAKE), 0, "and tests' session never sees them");
  assert.equal(w.S.drafts.get(NAMESAKE) ?? "", "");
});

test("3: a reload while the folder question is up loses nothing", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  w.onCreateDirMissing({ name: "api", dir: "~/notes-api" });
  const r = world(w.persisted());
  assert.deepEqual(kept(r, prov), { state: "closed", kept: true, replaced: false, staged: [ITEM.text], draft: TYPED });
  assert.equal(r.S.activeId, prov, "the tab is back on screen");
  assert.equal(r.box.value, TYPED);
  assert.equal(r.stripLine(), "1 staged — sends with your next message");
});

test("4: a kept tab's ✕ asks before it discards staged messages or text, and Cancel keeps everything", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  w.failProvisional("The folder could not be read.");
  w.closeTabLocally(prov);
  const ask = w.confirms[w.confirms.length - 1];
  assert.equal(ask.title, "Discard api's 1 staged message and the text in its box?");
  assert.deepEqual(ask.buttons.map((b) => b.label), ["Cancel", "Discard"]);
  assert.ok(w.S.sessions.has(prov), "nothing goes before the answer");
  ask.cb("");   // Cancel
  assert.deepEqual(kept(w, prov), { state: "closed", kept: true, replaced: false, staged: [ITEM.text], draft: TYPED });
  w.closeTabLocally(prov);
  w.confirms[w.confirms.length - 1].cb("discard");
  assert.equal(w.S.sessions.has(prov), false, "Discard discards");
  assert.equal(w.persistedCount(prov), 0);
  assert.deepEqual(w.persisted().provisionals, []);
  // the question names what is there, and a tab with nothing in it goes at once
  assert.equal(w.tabDiscardTitle("api", 2, false, false), "Discard api's 2 staged messages?");
  assert.equal(w.tabDiscardTitle("api", 0, true, false), "Discard the text in api's box?");
  assert.equal(w.tabDiscardTitle("api", 1, true, true), "Discard api's 1 staged message and the text and attachments in its box?");
  const e = opening();
  e.w.failProvisional("The folder could not be read.");
  const n = e.w.confirms.length;
  e.w.closeTabLocally(e.prov);
  assert.equal(e.w.confirms.length, n, "an empty kept tab is not asked about");
  assert.equal(e.w.S.sessions.has(e.prov), false);
});

test("4: a session arriving under a failed tab's name after the backstop adopts the tab, a reload in between included", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  w.fireTimers();   // 90 s and nothing came back: the create counts as failed
  assert.equal(w.confirms[0].title, "Couldn't start api");
  w.arrive(REAL, "api");   // …and then it did come up (a slow create)
  assert.equal(w.S.sessions.has(prov), false, "the failed tab gives way to its session");
  assert.equal(w.S.activeId, REAL, "it was on screen, so its session is");
  assert.equal(w.stripLine(), "1 staged — sends with your next message");
  assert.equal(w.box.value, TYPED);
  assert.deepEqual(w.persisted().provisionals, []);
  assert.deepEqual(w.wire.filter((m) => m.type === "sendMessage"), []);
  // the same after a reload: the failed tab comes back, and the late session's frame adopts it
  const o = opening();
  o.w.stage(ITEM.text);
  o.w.fireTimers();
  const r = world(o.w.persisted());
  assert.ok(r.S.failedProvisionals.has(o.prov));
  r.arrive(REAL, "api");
  assert.equal(r.S.sessions.has(o.prov), false);
  assert.equal(r.stagedMsgs.count(REAL), 1);
});

test("5: held sends a reload put back in the box say so in a line above it, which follows the text and goes when the box is sent", () => {
  const { w, prov } = opening();
  w.S.provisionalQueue.push(HELD); w.persistProvisionals();
  const r = world(w.persisted());
  assert.equal(r.box.value, HELD);
  assert.deepEqual([...r.S.heldBack], [[prov, "api"]], "the line stands for the tab's box");
  assert.equal(r.heldBackLine("api"), "Not sent: what you typed while api was starting is in the box. Send it when you're ready.");
  r.arrive(REAL, "api");
  assert.deepEqual([...r.S.heldBack], [[REAL, "api"]], "the text moved to the session's box, and the line with it");
  r.clearBox();   // the send that takes it
  assert.deepEqual([...r.S.heldBack], [], "gone with the text");
  assert.equal(world(r.persisted()).S.heldBack.size, 0, "and from the page's state");
});

test("5: a create holding sends holds the dashboard's reload while its session can still arrive, and its arrival ends the hold", () => {
  const { w } = opening();
  assert.equal(w.paneHoldReason(), "", "nothing held");
  w.type(TYPED);
  w.enter();
  assert.equal(w.paneHoldReason(), "held-send", "a send held for the session holds the reload, as a send held on an upload does");
  const before = w.ended();
  w.arrive(REAL, "api");
  assert.equal(w.paneHoldReason(), "");
  assert.ok(w.ended() > before, "the arrival tells the reload core the hold is over");
  // a create on a host whose relay is down has no arrival coming: it holds nothing
  const d = opening();
  d.w.S.window.__rompFed = { hosts: () => ["gpu"], down: () => ["gpu"] };
  d.w.startCreate({ ...TESTS_REQ, host: "gpu" });
  d.w.type(TYPED);
  d.w.enter();
  assert.equal(d.w.S.provisionalQueue.length, 1);
  assert.equal(d.w.paneHoldReason(), "");
});

test("6: a create saved longer ago than its backstop comes back kept, never opening, and never says nothing came back", () => {
  const { w, prov } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  const saved = w.persisted();
  for (const t of [Date.now() - 3 * 24 * 3600_000, undefined]) {   // days old, or written before the record had a time
    const r = world({ ...saved, provisionals: (saved.provisionals as Record<string, unknown>[]).map((p) => ({ ...p, t })) });
    assert.deepEqual(kept(r, prov), { state: "closed", kept: true, replaced: false, staged: [ITEM.text], draft: TYPED }, "kept, with everything on it");
    assert.equal(r.S.provisionalId, null, "not a create in flight");
    r.fireTimers();
    assert.deepEqual(r.confirms, [], "no \"Couldn't start api … nothing came back\" about a create long over");
    r.arrive(REAL, "api");   // its session is running: its frame adopts the tab
    assert.equal(r.stagedMsgs.count(REAL), 1);
    assert.equal(r.box.value, TYPED);
  }
  // one reloaded a moment after it was asked for is still in flight, with the rest of its own backstop
  const fresh = world({ ...saved, provisionals: (saved.provisionals as Record<string, unknown>[]).map((p) => ({ ...p, t: Date.now() - 30_000 })) });
  assert.equal(fresh.S.provisionalId, prov);
  assert.equal(fresh.S.sessions.get(prov)?.status?.state, "opening");
  const [timer] = [...fresh.timers.values()];
  assert.ok(timer && timer.ms <= 60_000 && timer.ms > 55_000, "the backstop counts from the create: " + timer?.ms);
});

test("6: after a reload, the hand-over to a running session under the name joins its draft and its staged list", () => {
  const { w } = opening();
  w.stage(ITEM.text);
  w.type(TYPED);
  const saved = w.persisted();
  // api is running already, with a draft and a staged message of its own in the page's state
  const st = new StagedStack(); st.restore(saved.staged); st.push(REAL, { text: "Already staged on the running api.", cites: [] });
  const r = world({ ...saved, drafts: { ...(saved.drafts as Record<string, string>), [REAL]: "A note I was writing to api." }, staged: st.entries() });
  r.arrive(REAL, "api");   // a fresh page has seen no session: the running api's first frame adopts the tab
  assert.equal(r.S.drafts.get(REAL), "A note I was writing to api.\n\n" + TYPED, "its draft first, the tab's after it");
  assert.equal(r.box.value, "A note I was writing to api.\n\n" + TYPED);
  assert.deepEqual(r.stagedMsgs.list(REAL).map((m) => m.text), ["Already staged on the running api.", ITEM.text]);
});

// The review of #373 (2026-10-23): two roads where what a new session's tab holds could still go without a word.
const DOCS_REQ = { name: "docs", backend: "sdk", dir: "", host: "" };
// the tab strip's ✕ delegate (setupTabs' `close`): from its key to the End question a running session's ✕ asks
const CLOSE_A = RENDER.indexOf("    close: (el) => {"), CLOSE_B = RENDER.indexOf("showConfirm(`End “${nm}”?`", CLOSE_A);
assert.ok(CLOSE_A > 0 && CLOSE_B > CLOSE_A, "the tab strip's ✕ delegate moved");

test("7: the ✕ on a tab still opening names the staged messages, text and held sends it would discard, Cancel first; Cancel keeps all of it", () => {
  const { w, prov } = opening();
  w.type("What does the list endpoint return today?"); w.enter();
  w.type("And what does it return for an empty page?"); w.enter();   // two sends held for the session
  w.stage(ITEM.text);
  w.type(TYPED);
  assert.equal(w.closeOpeningTab(prov), true, "it asks");
  const ask = w.confirms[w.confirms.length - 1];
  assert.equal(ask.title, "Cancel starting api and discard its 1 staged message, 2 unsent messages and the text in its box?");
  assert.deepEqual(ask.buttons.map((b) => b.label), ["Cancel", "Discard"], "Cancel first, so it has the focus");
  ask.cb("");   // Cancel
  ask.cb(null);   // …or Esc, or the backdrop
  assert.equal(w.S.provisionalId, prov, "still the create in flight");
  assert.equal(w.S.sessions.get(prov)?.status?.state, "opening");
  assert.equal(w.S.provisionalQueue.length, 2, "its held sends are still held");
  assert.deepEqual(w.stagedMsgs.list(prov).map((m) => m.text), [ITEM.text], "its staged message is still staged");
  assert.equal(w.box.value, TYPED, "its text is in the box");
  assert.deepEqual(w.wire.filter((m) => m.type === "cancelCreate"), [], "and the create goes on");
  // Discard cancels the create and discards it all
  w.closeOpeningTab(prov);
  w.confirms[w.confirms.length - 1].cb("discard");
  assert.equal(w.S.sessions.has(prov), false);
  assert.equal(w.S.provisionalId, null);
  assert.deepEqual(w.wire.filter((m) => m.type === "cancelCreate"), [{ type: "cancelCreate", name: "api" }]);
  assert.equal(w.persistedCount(prov), 0);
  assert.deepEqual(w.persisted().provisionals, []);
  // what the question names, thing by thing
  assert.equal(w.tabDiscardTitle("api", 0, false, false, 1, true), "Cancel starting api and discard its 1 unsent message?");
  assert.equal(w.tabDiscardTitle("api", 0, true, false, 0, true), "Cancel starting api and discard the text in its box?");
  assert.equal(w.tabDiscardTitle("api", 2, false, true, 0, true), "Cancel starting api and discard its 2 staged messages and the attachments in its box?");
  // an empty opening tab is asked what it was asked before (the caller's End question)
  const e = opening();
  assert.equal(e.w.closeOpeningTab(e.prov), false);
  assert.deepEqual(e.w.confirms, []);
  // the session arriving while the question is up takes what the tab held, and a Discard after it discards nothing
  const a = opening();
  a.w.stage(ITEM.text);
  a.w.closeOpeningTab(a.prov);
  a.w.arrive(REAL, "api");
  a.w.confirms[a.w.confirms.length - 1].cb("discard");
  assert.deepEqual(a.w.stagedMsgs.list(REAL).map((m) => m.text), [ITEM.text]);
  assert.deepEqual(a.w.wire.filter((m) => m.type === "cancelCreate"), []);  // and the ✕ on the strip goes through it: a live tab's ✕ asks the opening tab's question before the End one
  assert.ok(RENDER.slice(CLOSE_A, CLOSE_B).includes("if (closeOpeningTab(id)) return;"), "the tab strip's ✕ asks it");
});

test("7: the line over a box of sends that never went stands for each tab, through two replaced creates and a reload", () => {
  const { w, prov } = opening();
  w.type(HELD); w.enter();                 // a send held for api
  w.startCreate(TESTS_REQ);                // tests, before api opens: api's tab is kept, its send back in its box
  const tests = w.S.provisionalId as string;
  w.type(TYPED); w.enter();                // a send held for tests
  w.startCreate(DOCS_REQ);                 // docs, before tests opens
  assert.deepEqual(Object.fromEntries(w.S.heldBack), { [prov]: "api", [tests]: "tests" }, "each kept tab keeps its own line");
  assert.equal(w.S.drafts.get(prov), HELD);
  assert.equal(w.heldBackLine("api", false), "Not sent: what you typed while api was starting is in the box.",
    "a kept tab cannot send, so its line says only where the text is");
  const r = world(w.persisted());          // a reload
  assert.deepEqual(Object.fromEntries(r.S.heldBack), { [prov]: "api", [tests]: "tests" }, "both lines come back with the text they name");
  assert.equal(r.S.drafts.get(prov), HELD);
  r.arrive(REAL, "api");                   // api's session opens after all and takes its tab
  assert.deepEqual(Object.fromEntries(r.S.heldBack), { [tests]: "tests", [REAL]: "api" }, "api's line follows its text");
  r.S.setActive(REAL);
  r.clearBox();                            // the send that takes it
  assert.deepEqual(Object.fromEntries(r.S.heldBack), { [tests]: "tests" }, "api's line goes with its text; tests' stands");
  r.clearHeldBackNote(tests);              // the line's own ✕
  assert.equal(world(r.persisted()).S.heldBack.size, 0, "and stays gone after a reload");
  // a reload after a failure keeps the line with the text
  const f = opening();
  f.w.type(HELD); f.w.enter();
  f.w.failProvisional("The folder could not be read.");
  const g = world(f.w.persisted());
  assert.equal(g.box.value, HELD);
  assert.deepEqual(Object.fromEntries(g.S.heldBack), { [f.prov]: "api" });
  // a discarded tab takes its line with it
  g.closeTabLocally(f.prov);
  g.confirms[g.confirms.length - 1].cb("discard");
  assert.equal(g.S.heldBack.size, 0);
  assert.equal(world(g.persisted()).S.heldBack.size, 0);
});
