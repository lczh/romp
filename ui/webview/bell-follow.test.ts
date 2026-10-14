// Bells follow the dashboard (plans/federated-push.md, 2026-10-10): a LINKED kernel's session and card bells paint against
// the switches of the kernel this page is served by, the kernel whose gate decides what reaches the devices subscribed at
// it, from the linked kernel's raw override; a click on one goes as given. Executed: the rule itself (bell-follow.ts), the
// federation layer keeping only the local kernel's switches frame and passing the raw field through, and the timeline's
// inlined twin (laneBell and its pending reconcile). Pinned at the source: the chat pane's two frame roads and its click,
// and both timeline boots' dispatch and bridge. The feed's card bell is run in feed-render-incremental.test.ts.
// The SESSION bell (2026-10-11): a setting the user makes, which beats the defaults: on when the session notifies for
// everything a session can (its own setting on, or none under the master AND turn-finished switches), a click stores the
// opposite as its own, and the sub-line says whose setting it is and what it means. Executed: sessionBell for the chat's
// tab menu and bell command, laneBell for the timeline gear, and the pending guard confirming on the setting.
// Synthetic only: placeholder ids, invented hosts (boxb).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { bellState, bellClickFields, legacyBellNote, setHubSwitches, hubMaster, sessionBell } from "./bell-follow";
import { notePendingFlag, applyFrameFlags, type PendingFlags } from "./flag-pending";
import { FederationManager } from "./federation";

const SID = "11111111-2222-3333-4444-aaaaaaaaaaaa";
const REMOTE = "boxb:" + SID;
const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const BOOT = fs.readFileSync(path.join(ROOT, "ui", "webview", "timeline-boot.ts"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "bin", "romp-kernel"), "utf8");
const view = createRequire(__filename)(path.join(ROOT, "ui", "romp-timeline-view.js"));

test("a local bell paints as its kernel sent it, whatever this master says", () => {
  setHubSwitches({ all: true });
  assert.deepEqual(bellState(SID, { notify: false, notifyRaw: null }), { on: false, legacy: false });
  assert.deepEqual(bellState(SID, { notify: true }), { on: true, legacy: false });
  assert.deepEqual(bellClickFields(SID, { notify: false, notifyRaw: null }), {}, "a local click is the message it always was");
});

test("a linked kernel's bell: its override stands, no override follows THIS kernel's master", () => {
  setHubSwitches({ all: true });
  assert.equal(hubMaster(), true);
  // the user's scenario: that kernel's master never set, so its own reading is off; this master on decides
  assert.deepEqual(bellState(REMOTE, { notify: null, notifyRaw: null }), { on: true, legacy: false });
  assert.deepEqual(bellState(REMOTE, { notify: true, notifyRaw: false }), { on: false, legacy: false }, "a mute stands");
  assert.equal(setHubSwitches({ all: false }), true, "the master moved");
  assert.equal(setHubSwitches({ all: false }), false, "…and the same word again moves nothing");
  assert.deepEqual(bellState(REMOTE, { notify: true, notifyRaw: null }), { on: false, legacy: false }, "no override follows the master off");
  assert.deepEqual(bellState(REMOTE, { notify: false, notifyRaw: true }), { on: true, legacy: false }, "an arm stands");
  assert.deepEqual(bellClickFields(REMOTE, { notifyRaw: null }), { asGiven: true }, "a click is stored there as given");
});

test("an older linked kernel (no raw field) paints as it reports it, says so, and its click is unchanged", () => {
  setHubSwitches({ all: true });
  assert.deepEqual(bellState(REMOTE, { notify: false }), { on: false, legacy: true }, "never a guess at the new rule");
  assert.deepEqual(bellState(REMOTE, { notify: true, notifyRaw: undefined }), { on: true, legacy: true });
  assert.deepEqual(bellClickFields(REMOTE, { notify: true }), {});
  assert.equal(legacyBellNote(REMOTE), "follows boxb's own notification switch: that machine runs an older romp");
});

function withManager(fn: (fm: any, emitted: any[]) => void): void {
  const emitted: any[] = [];
  const store = new Map<string, string>();
  const g: any = globalThis;
  const hadWindow = "window" in g, prevWindow = g.window;
  const hadLS = "localStorage" in g, prevLS = g.localStorage;
  g.window = { dispatchEvent: (ev: any) => { if (ev && ev.data) emitted.push(ev.data); } };
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  try { fn(new FederationManager(), emitted); } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
    if (hadLS) g.localStorage = prevLS; else delete g.localStorage;
  }
}

test("federation keeps only the local kernel's switches frame, and a linked kernel's raw overrides ride through", () => {
  withManager((fm, emitted) => {
    fm.inbound("boxb", { type: "notifySwitches", all: false, turns: false });
    assert.equal(emitted.filter((m) => m.type === "notifySwitches").length, 0, "a linked kernel's master would read as this one's");
    fm.inbound("", { type: "notifySwitches", all: true, turns: true });
    assert.deepEqual(emitted.filter((m) => m.type === "notifySwitches"), [{ type: "notifySwitches", all: true, turns: true }]);
    fm.inbound("boxb", { type: "feed", asks: [{ itemId: SID + ":g1", sid: SID, notify: null, notifyRaw: false }], sessions: [], working: [], awaiting: [], order: [] });
    const merged = emitted.filter((m) => m.type === "feed").pop();
    const ask = merged.asks.find((a: any) => a.itemId === SID + ":g1");
    assert.equal(ask.sid, REMOTE, "the card wears the host prefix");
    assert.equal(ask.notifyRaw, false, "and its raw override, untouched");
  });
});

test("the timeline's inlined twin follows the same rule, confirms a linked bell on its own setting, and sends it as given", () => {
  const { laneBell, TimelinePanel } = view;
  const panel = Object.create(TimelinePanel.prototype);
  panel.setNotifySwitches({ type: "notifySwitches", all: true, turns: true });
  assert.equal(laneBell({ id: REMOTE, notify: false, notifyRaw: null }).fullOn, true, "no setting: this kernel's switches decide");
  assert.deepEqual(laneBell({ id: REMOTE, notify: true }), { fullOn: true, explicit: false, legacy: true, sub: "system notification when its work blocks on you or completes" });
  assert.equal(laneBell({ id: SID, notify: false, notifyRaw: null }).fullOn, true, "a local lane with no setting follows the same switches");
  // a click to off on the linked lane: held by its setting until a frame carries the stored off. That kernel's own effective
  // value (notify false: its master never set) agrees with the click BEFORE it landed, and must not confirm it
  panel._pendingFlags = { [REMOTE]: { notify: false } };
  panel.data = { sessions: [{ id: REMOTE, notify: false, notifyRaw: null }] };
  panel._reconcilePendingFlags();
  assert.deepEqual(panel._pendingFlags, { [REMOTE]: { notify: false } }, "still pending: its setting is still none");
  assert.equal(laneBell(panel.data.sessions[0]).fullOn, false, "…and it paints the click meanwhile");
  assert.equal(panel.data.sessions[0].notifyRaw, false, "…as the session's own setting");
  panel.data = { sessions: [{ id: REMOTE, notify: false, notifyRaw: false }] };   // the stored mute arrives
  panel._reconcilePendingFlags();
  assert.deepEqual(panel._pendingFlags, {}, "confirmed");
  const sent: any[] = [];
  const g: any = globalThis;
  const prev = g.window;
  g.window = { __rompTimelineSetFlag: (...a: any[]) => sent.push(a) };
  try {
    panel._setSessionFlag({ id: REMOTE }, "notify", false, true);
    panel._setSessionFlag({ id: SID }, "notify", true);
  } finally { if (prev === undefined) delete g.window; else g.window = prev; }
  assert.deepEqual(sent, [[REMOTE, "notify", false, true], [SID, "notify", true]]);
});

test("the chat pane: both frame roads resolve a linked bell before the guard, the click goes as given, the switches frame re-resolves", () => {
  assert.match(RENDER, /import \{ bellState, bellClickFields, legacyBellNote, setHubSwitches, hubMaster, sessionBell \} from "\.\/bell-follow";/);
  assert.match(RENDER, /notifyRaw: \("notifyRaw" in msg\) \? \(msg\.notifyRaw \?\? null\) : \(prev \? prev\.notifyRaw : undefined\),/, "the full frame keeps the override, undefined for a kernel that never sent one");
  assert.match(RENDER, /applyFrameFlags\(s, frameBell\(msg\.id, msg, s\.notifyRaw\), pendingFlags, msg\.id\);/);
  // the tail carries it too, and it lands under the click's guard (applyFrameFlags), never written past it
  assert.doesNotMatch(RENDER, /if \("notifyRaw" in msg\) s\.notifyRaw = msg\.notifyRaw \?\? null;/, "a tail built before a click cannot repaint the setting the click made");
  assert.match(RENDER, /const extra = flag === "notify" \? bellClickFields\(id, s\) : \{\};[\s\S]*?vscodeApi\.postMessage\(\{ type: "setSessionFlag", id, flag, value, \.\.\.extra \}\);/);
  assert.match(RENDER, /else if \(m\.type === "notifySwitches"\) onNotifySwitches\(m\);/);
  assert.match(RENDER, /\+ \(bellLegacy \? " · " \+ legacyBellNote\(id\) : ""\)/, "the tab menu says whose switch an older kernel's bell follows");
  // the session bell (2026-10-11): the tab menu's row and the bell command both read sessionBell and store the opposite
  assert.match(RENDER, /const bell = sessionBell\(id, s\);[^\n]*\n\s*const onBell = bell\.fullOn;/);
  assert.match(RENDER, /toggle\("bell", !onBell,\s*onBell \? "Stop notifying" : "Notify me",\s*bell\.sub\s*\+ \(bellLegacy/);
  assert.match(RENDER, /\(\) => setSessionFlag\(id, "notify", !onBell\)\);/);
  assert.match(RENDER, /const on = !sessionBell\(activeId, s\)\.fullOn;[^\n]*\n\s*setSessionFlag\(activeId, "notify", on\);/);
  // the click is the session's own setting on the copy, for every kernel that sends one (a legacy linked kernel sends none)
  assert.match(RENDER, /if \(s && flag === "notify" && s\.notifyRaw !== undefined\) s\.notifyRaw = value;/);
  // a refused bell takes its own setting from the refusal frame when the frame carries it
  assert.match(RENDER, /if \(s && m\.flag === "notify" && s\.notifyRaw !== undefined && frameRaw\(m\) !== undefined\) s\.notifyRaw = frameRaw\(m\);/);
});

test("both timeline boots route the switches frame and carry asGiven on the bridge", () => {
  assert.match(BOOT, /if \(m\.type === "notifySwitches" && panel\.setNotifySwitches\) \{ panel\.setNotifySwitches\(m\); return true; \}/);
  assert.match(KERNEL, /else if\(m\.type==="notifySwitches"&&panel\.setNotifySwitches\)panel\.setNotifySwitches\(m\);/);
  assert.match(KERNEL, /window\.__rompTimelineSetFlag=function\(id,flag,value,asGiven\)\{post\(asGiven===true\?\{type:"setSessionFlag",id:id,flag:flag,value:!!value,asGiven:true\}:\{type:"setSessionFlag",id:id,flag:flag,value:!!value\}\);\};/);
});

// ── the session bell: a setting the user makes, which beats the defaults (2026-10-11) ──────────────────────────────

const LOCAL = "11111111-2222-3333-4444-555555555555";
const LINKED = "TESTHOST:" + LOCAL;
const EVERYTHING = "when a turn you started ends, or it blocks on you or completes";
/** The tab menu's row as render.ts builds it from sessionBell: label, slashed icon, sub-line, and the value a click stores. */
function tabRow(id: string, it: { notify?: boolean | null; notifyRaw?: boolean | null }) {
  const b = sessionBell(id, it);
  return { label: b.fullOn ? "Stop notifying" : "Notify me", slashed: !b.fullOn, sub: b.sub, click: !b.fullOn, explicit: b.explicit, legacy: b.legacy };
}

test("session bell: an explicit on stands with both switches off, and says it is the session's own setting", () => {
  setHubSwitches({ all: false, turns: false });
  for (const id of [LOCAL, LINKED]) {
    assert.deepEqual(tabRow(id, { notify: true, notifyRaw: true }),
      { label: "Stop notifying", slashed: false, sub: "your setting: " + EVERYTHING, click: false, explicit: true, legacy: false }, id);
  }
});

test("session bell: an explicit off stands with both switches on", () => {
  setHubSwitches({ all: true, turns: true });
  for (const id of [LOCAL, LINKED]) {
    assert.deepEqual(tabRow(id, { notify: false, notifyRaw: false }),
      { label: "Notify me", slashed: true, sub: "your setting: no notifications", click: true, explicit: true, legacy: false }, id);
  }
});

test("session bell: no setting of its own follows the switches; master on and turns off offers Notify me, and the click opts it in", () => {
  setHubSwitches({ all: true, turns: false });
  for (const id of [LOCAL, LINKED]) {
    // notify (the card-level effective value) is on under the master, but its turn ends would not notify: not everything
    assert.deepEqual(tabRow(id, { notify: true, notifyRaw: null }),
      { label: "Notify me", slashed: true, sub: "default: when it blocks on you or completes", click: true, explicit: false, legacy: false }, id);
  }
  assert.equal(setHubSwitches({ all: true, turns: true }), true, "the turn-finished switch alone moving is a move");
  assert.equal(setHubSwitches({ all: true, turns: true }), false, "…and the same word again is not");
  for (const id of [LOCAL, LINKED]) {
    assert.deepEqual(tabRow(id, { notify: true, notifyRaw: null }),
      { label: "Stop notifying", slashed: false, sub: "default: " + EVERYTHING, click: false, explicit: false, legacy: false }, id);
  }
  setHubSwitches({ all: false, turns: true });
  assert.deepEqual(tabRow(LOCAL, { notify: false, notifyRaw: null }),
    { label: "Notify me", slashed: true, sub: "default: no notifications", click: true, explicit: false, legacy: false }, "turns alone notifies nothing");
});

test("session bell: a linked kernel on an older romp (no setting sent) keeps the bell it reports and its old wording", () => {
  for (const sw of [{ all: true, turns: true }, { all: false, turns: false }]) {
    setHubSwitches(sw);
    assert.deepEqual(tabRow(REMOTE, { notify: true }),
      { label: "Stop notifying", slashed: false, sub: "no more system notifications for this session", click: false, explicit: false, legacy: true });
    assert.deepEqual(tabRow(REMOTE, { notify: false, notifyRaw: undefined }),
      { label: "Notify me", slashed: true, sub: "system notification when its work blocks on you or completes", click: true, explicit: false, legacy: true });
    assert.deepEqual(bellClickFields(REMOTE, { notify: false }), {}, "its click is the message it always was");
  }
  assert.equal(legacyBellNote(REMOTE), "follows boxb's own notification switch: that machine runs an older romp", "the row adds this note (render.ts)");
});

test("session bell: the timeline gear's twin reads the same, word for word", () => {
  const { laneBell, TimelinePanel } = view;
  const panel = Object.create(TimelinePanel.prototype);
  const cases: [any, any][] = [
    [{ all: false, turns: false }, { id: LOCAL, notify: true, notifyRaw: true }],
    [{ all: true, turns: true }, { id: LINKED, notify: false, notifyRaw: false }],
    [{ all: true, turns: false }, { id: LOCAL, notify: true, notifyRaw: null }],
    [{ all: true, turns: true }, { id: LINKED, notify: true, notifyRaw: null }],
    [{ all: false, turns: true }, { id: LOCAL, notify: false, notifyRaw: null }],
  ];
  for (const [sw, lane] of cases) {
    setHubSwitches(sw);
    panel.setNotifySwitches({ type: "notifySwitches", ...sw });
    const want = sessionBell(lane.id, lane);
    assert.deepEqual(laneBell(lane), want, JSON.stringify([sw, lane]));
  }
  // legacy keeps the gear's own wording (its row's label already says on or off)
  assert.deepEqual(laneBell({ id: REMOTE, notify: false }), { fullOn: false, explicit: false, legacy: true, sub: "system notification when its work blocks on you or completes" });
});

test("session bell: the chat's pending guard confirms a click on the session's own setting, not on the effective value", () => {
  setHubSwitches({ all: true, turns: false });
  for (const id of [LOCAL, LINKED]) {
    const pending: PendingFlags = new Map();
    // no setting, master on: the effective value is already on. The click (Notify me) stores on as the session's own
    const s: { notify?: boolean; notifyRaw?: boolean | null } = { notify: true, notifyRaw: null };
    const click = tabRow(id, s).click;
    assert.equal(click, true);
    s.notify = click; notePendingFlag(pending, id, "notify", click); s.notifyRaw = click;   // what setSessionFlag does
    assert.equal(tabRow(id, s).label, "Stop notifying", "painted at once");
    // a frame built before the click: its effective value agrees, its setting does not. It must not confirm, nor repaint
    applyFrameFlags(s, { notify: true, notifyRaw: null }, pending, id);
    assert.ok(pending.get(id)?.has("notify"), id + ": still pending");
    assert.equal(s.notifyRaw, true, "the copy keeps the clicked setting");
    assert.equal(tabRow(id, s).label, "Stop notifying");
    // the stored setting arrives: confirmed, and the frame's word is the copy's again
    applyFrameFlags(s, { notify: true, notifyRaw: true }, pending, id);
    assert.equal(pending.size, 0, id + ": confirmed");
    applyFrameFlags(s, { notify: false, notifyRaw: false }, pending, id);
    assert.deepEqual(s, { notify: false, notifyRaw: false }, "a later frame (another window's click) lands");
  }
  // a legacy linked kernel's frame carries no setting: confirmed on notify, as before, and no setting is invented
  const pending: PendingFlags = new Map();
  const old: { notify?: boolean; notifyRaw?: boolean | null } = { notify: true };
  notePendingFlag(pending, REMOTE, "notify", false); old.notify = false;
  applyFrameFlags(old, { notify: true }, pending, REMOTE);
  assert.equal(old.notify, false, "held");
  applyFrameFlags(old, { notify: false }, pending, REMOTE);
  assert.equal(pending.size, 0, "confirmed on what it reports");
  assert.equal("notifyRaw" in old, false);
});
