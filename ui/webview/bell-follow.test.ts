// Bells follow the dashboard (plans/federated-push.md, 2026-10-10): a LINKED kernel's session and card bells paint against
// the switches of the kernel this page is served by, the kernel whose gate decides what reaches the devices subscribed at
// it, from the linked kernel's raw override; a click on one goes as given. Executed: the rule itself (bell-follow.ts), the
// federation layer keeping only the local kernel's switches frame and passing the raw field through, and the timeline's
// inlined twin (laneBell and its pending reconcile). Pinned at the source: the chat pane's two frame roads and its click,
// and both timeline boots' dispatch and bridge. The feed's card bell is run in feed-render-incremental.test.ts.
// Synthetic only: placeholder ids, invented hosts (boxb).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { bellState, bellClickFields, legacyBellNote, setHubSwitches, hubMaster } from "./bell-follow";
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

test("the timeline's inlined twin follows the same rule, confirms a linked bell on what it paints, and sends it as given", () => {
  const { laneBell, TimelinePanel } = view;
  const panel = Object.create(TimelinePanel.prototype);
  panel.setNotifySwitches({ type: "notifySwitches", all: true, turns: false });
  assert.deepEqual(laneBell({ id: REMOTE, notify: false, notifyRaw: null }), { on: true, legacy: false });
  assert.deepEqual(laneBell({ id: REMOTE, notify: true }), { on: true, legacy: true });
  assert.deepEqual(laneBell({ id: SID, notify: false, notifyRaw: null }), { on: false, legacy: false });
  // a click to off on the linked lane: held by its override until a frame paints off. That kernel's own effective value
  // (notify false: its master never set) agrees with the click BEFORE it landed, and must not confirm it
  panel._pendingFlags = { [REMOTE]: { notify: false } };
  panel.data = { sessions: [{ id: REMOTE, notify: false, notifyRaw: null }] };
  panel._reconcilePendingFlags();
  assert.deepEqual(panel._pendingFlags, { [REMOTE]: { notify: false } }, "still pending: what it paints is on");
  assert.equal(laneBell(panel.data.sessions[0]).on, false, "…and it paints the click meanwhile");
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
  assert.match(RENDER, /import \{ bellState, bellClickFields, legacyBellNote, setHubSwitches, hubMaster \} from "\.\/bell-follow";/);
  assert.match(RENDER, /notifyRaw: \("notifyRaw" in msg\) \? \(msg\.notifyRaw \?\? null\) : \(prev \? prev\.notifyRaw : undefined\),/, "the full frame keeps the override, undefined for a kernel that never sent one");
  assert.match(RENDER, /applyFrameFlags\(s, frameBell\(msg\.id, msg, s\.notifyRaw\), pendingFlags, msg\.id\);/);
  assert.match(RENDER, /if \("notifyRaw" in msg\) s\.notifyRaw = msg\.notifyRaw \?\? null;/, "the tail carries it too");
  assert.match(RENDER, /const extra = flag === "notify" \? bellClickFields\(id, s\) : \{\};[\s\S]*?vscodeApi\.postMessage\(\{ type: "setSessionFlag", id, flag, value, \.\.\.extra \}\);/);
  assert.match(RENDER, /else if \(m\.type === "notifySwitches"\) onNotifySwitches\(m\);/);
  assert.match(RENDER, /\+ \(bellLegacy \? " · " \+ legacyBellNote\(id\) : ""\)/, "the tab menu says whose switch an older kernel's bell follows");
});

test("both timeline boots route the switches frame and carry asGiven on the bridge", () => {
  assert.match(BOOT, /if \(m\.type === "notifySwitches" && panel\.setNotifySwitches\) \{ panel\.setNotifySwitches\(m\); return true; \}/);
  assert.match(KERNEL, /else if\(m\.type==="notifySwitches"&&panel\.setNotifySwitches\)panel\.setNotifySwitches\(m\);/);
  assert.match(KERNEL, /window\.__rompTimelineSetFlag=function\(id,flag,value,asGiven\)\{post\(asGiven===true\?\{type:"setSessionFlag",id:id,flag:flag,value:!!value,asGiven:true\}:\{type:"setSessionFlag",id:id,flag:flag,value:!!value\}\);\};/);
});
