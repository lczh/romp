// A hot key on a linked machine's session survives a reload (review 2026-10-23). On a fresh page the local kernel's
// strip reaches the chat pane before a linked machine's, and the pane judged the hot-key set against that partial
// strip: the linked session's entry and its chord were deleted for good on every load, ~250 ms before its host's
// strip brought the tab back. Executed end to end: the real FederationManager (through start(), so the pane reads the
// router's real host lists) merges the frames, and the pane's own syncTabKeysWithStrip, lifted from render.ts, runs on
// each over the real tab-keys and keybindings stores. tab-keys.test.ts executes the rule itself and pins the wiring.
// Synthetic only: the notes-api demo, web and api local, tests on TESTHOST, placeholder ids.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { FederationManager } from "./federation";
import { listenForFrames } from "./frame-listener";
import { hotkeyCommandId, loadTabKeys, rememberTabKey, forgetTabKey, goneTabKeys, renamedTabKeys, tabChord } from "./tab-keys";
import { saveOverride, loadOverrides } from "./keybindings";
import { reconcileTabOrder, retainLiveOmitted, stripHost } from "./tab-order";
import { reachableFrom } from "./tab-groups";

const WEB = "11111111-2222-4333-8444-0000000c1001";
const PEER = "11111111-2222-4333-8444-0000000c1002";      // the local api session
const ENDED = "11111111-2222-4333-8444-0000000c1003";     // a local session that really ended: its hot key goes
const TESTS = "11111111-2222-4333-8444-0000000c1004";     // the tests session on TESTHOST
const REMOTE = "TESTHOST:" + TESTS;                       // how the merged strip names it
const tabs = (...ids: string[]) => ids.map((id) => ({ id, name: id.slice(-4) }));

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

// The pane's syncTabKeysWithStrip, verbatim from render.ts, bound to the real helpers and the pane state it reads.
function liftSync(order: string[], sessions: Map<string, { name: string }>, hostsSeen: Set<string>, reachableHosts: () => Set<string>): () => void {
  const head = "function syncTabKeysWithStrip(): void {";
  const at = RENDER.indexOf(head);
  assert.ok(at >= 0, "render.ts still defines syncTabKeysWithStrip");
  const src = "function syncTabKeysWithStrip() {" + RENDER.slice(at + head.length, RENDER.indexOf("\n}\n", at) + 2);
  const make = new Function("inRompShell", "loadTabKeys", "localStorage", "goneTabKeys", "forgetTabKey", "saveOverride",
    "hotkeyCommandId", "renamedTabKeys", "rememberTabKey", "sessions", "order", "hostsSeen", "reachableHosts",
    src + "\nreturn syncTabKeysWithStrip;");
  return make(() => true, loadTabKeys, (globalThis as any).localStorage, goneTabKeys, forgetTabKey, saveOverride,
    hotkeyCommandId, renamedTabKeys, rememberTabKey, sessions, order, hostsSeen, reachableHosts);
}

// The parts of the pane this mirrors are pinned at source, so the mirror cannot drift from what the page runs.
test("render.ts: the pieces the end-to-end page mirrors are the pane's own", () => {
  assert.match(RENDER, /^function reachableHosts\(\): Set<string> \{ return reachableFrom\(\(window as any\)\.__rompFed\); \}$/m);
  assert.match(RENDER, /applyTabOrder\(m\.order, m\.tabs, \{ reemit: m\.reemit === true, freshHost: typeof m\.freshHost === "string" \? m\.freshHost : undefined \}, m\.live\);/);
  assert.match(RENDER, /const stripFrom = stripHost\(report\);[^\n]*\n\s*if \(stripFrom !== null\) hostsSeen\.add\(stripFrom\);/);
});

type Page = { chord: (sid: string) => string; inSet: (sid: string) => boolean; land: (host: string, ids: string[]) => any };

function page(fn: (p: Page) => void): void {
  const g: any = globalThis;
  const saved: Record<string, [boolean, unknown]> = {};
  for (const k of ["window", "document", "localStorage", "setInterval", "fetch"]) saved[k] = [k in g, g[k]];
  const win: any = new EventTarget();
  win.__rompApp = "chat";
  const store = new Map<string, string>();
  g.window = win;
  g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  g.setInterval = () => 0;
  g.fetch = () => new Promise(() => {});   // start()'s /tunnels poll never answers here
  try {
    // what the user set up before the reload: hot keys on web, on a local session that has since ended, and on TESTHOST's tests
    rememberTabKey(g.localStorage, WEB, "web"); saveOverride(hotkeyCommandId(WEB), "Ctrl+Alt+1");
    rememberTabKey(g.localStorage, ENDED, "old"); saveOverride(hotkeyCommandId(ENDED), "Ctrl+Alt+2");
    rememberTabKey(g.localStorage, REMOTE, "tests"); saveOverride(hotkeyCommandId(REMOTE), "Ctrl+Alt+3");
    const fm: any = new FederationManager();
    fm.start();
    const frames: any[] = [];
    listenForFrames((e: MessageEvent) => { if (e.data && e.data.type === "tabOrder") frames.push(e.data); });
    // the fresh pane: nothing listed, no host heard
    const order: string[] = [];
    const kernelListed = new Set<string>();
    const sessions = new Map<string, { name: string }>();
    const hostsSeen = new Set<string>();
    const sync = liftSync(order, sessions, hostsSeen, () => reachableFrom(win.__rompFed));
    // applyTabOrder's steps up to the sync (render.ts), with the real helpers: the order adopted, the frame's host noted
    const apply = (f: any) => {
      const kernelOrder: string[] = f.order;
      const liveSet = new Set<string>(Array.isArray(f.live) ? f.live : []);
      const next = reconcileTabOrder(retainLiveOmitted(kernelOrder, order, liveSet), order, (id) => sessions.has(id), (id) => kernelListed.has(id));
      order.length = 0; for (const id of next) order.push(id);
      for (const id of kernelOrder) kernelListed.add(id);
      const from = stripHost({ reemit: f.reemit === true, freshHost: typeof f.freshHost === "string" ? f.freshHost : undefined });
      if (from !== null) hostsSeen.add(from);
      sync();
    };
    fm.inbound("", { type: "viewOrder", order: [], stored: false });
    fm.ensureHost("TESTHOST");   // TESTHOST is linked; its strip has not reached this page yet
    fn({
      chord: (sid) => tabChord(sid, loadOverrides(), false),
      inSet: (sid) => sid in loadTabKeys(g.localStorage),
      land: (host, ids) => {
        fm.inbound(host, { type: "tabOrder", order: ids, tabs: tabs(...ids), live: ids });
        const f = frames.at(-1);
        assert.equal(f.freshHost, host, "the pane's frame is this host's fresh word");
        apply(f);
        return f;
      },
    });
  } finally {
    for (const k of Object.keys(saved)) { const [had, v] = saved[k]; if (had) g[k] = v; else delete g[k]; }
  }
}

test("a reload: the local strip lands first and drops only the local session that ended; TESTHOST's hot key is still bound when its strip arrives", () => {
  page(({ chord, inSet, land }) => {
    assert.equal(chord(REMOTE), "Ctrl+Alt+3");
    const f = land("", [WEB, PEER]);
    assert.deepEqual(f.order, [WEB, PEER], "TESTHOST has not reported: none of its sessions are on the strip");
    assert.equal(inSet(ENDED), false, "the local kernel no longer lists it: its hot key goes");
    assert.equal(chord(ENDED), "");
    assert.equal(chord(WEB), "Ctrl+Alt+1", "a listed local session keeps its hot key");
    assert.equal(inSet(REMOTE), true, "TESTHOST has said nothing yet: its session is not judged");
    assert.equal(chord(REMOTE), "Ctrl+Alt+3");
    const g = land("TESTHOST", [TESTS]);
    assert.ok(g.order.includes(REMOTE), "the tests tab is on the strip");
    assert.equal(chord(REMOTE), "Ctrl+Alt+3", "and Ctrl+Alt+3 still switches to it");
  });
});

test("TESTHOST's strip landing first drops none of the local hot keys; the local strip then judges its own", () => {
  page(({ chord, inSet, land }) => {
    assert.deepEqual(land("TESTHOST", [TESTS]).order, [REMOTE]);
    assert.equal(chord(WEB), "Ctrl+Alt+1", "TESTHOST's strip says nothing about the local sessions");
    assert.equal(inSet(ENDED), true);
    land("", [WEB, PEER]);
    assert.equal(inSet(ENDED), false, "the local kernel's own strip does");
    assert.equal(chord(WEB), "Ctrl+Alt+1");
    assert.equal(chord(REMOTE), "Ctrl+Alt+3");
  });
});

test("TESTHOST's own strip omitting its session drops that hot key: the rule still prunes where the host has spoken", () => {
  page(({ chord, inSet, land }) => {
    land("", [WEB, PEER]);
    land("TESTHOST", [TESTS]);
    assert.equal(chord(REMOTE), "Ctrl+Alt+3");
    land("TESTHOST", []);
    assert.equal(inSet(REMOTE), false, "its own host stopped listing it: the session ended");
    assert.equal(chord(REMOTE), "");
    assert.equal(chord(WEB), "Ctrl+Alt+1");
  });
});
