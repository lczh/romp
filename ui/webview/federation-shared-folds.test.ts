// TWO VIEWERS OF ONE KERNEL FOLD TOGETHER (the user 2026-09-23, who wanted to collapse tag groups on the phone too, with
// the collapsed state synced to the desktop like the tab order).
//
// Executed against the real FederationManager and the real tab-groups store, twice over: a browser here is its own
// `window` and its own localStorage, and everything on this path is synchronous, so swapping the globals IS having two
// of them (federation-shared-order.test.ts's harness, the same shape). The fake kernel in the middle does what kernel.py
// does with the viewer's store: keeps the fold object it is handed without reading it, says whether it has one at all
// (`folds: null` when not), and pushes ONE viewOrder frame, the arrangement's and the folds' together, to every client
// when either CHANGES. What is pinned is the protocol between the two, not a mock of it.
//
// SYNTHETIC only: the notes-api demo world (web, api, tests), placeholder uuids.
import { test } from "node:test";
import assert from "node:assert/strict";
import { FederationManager } from "./federation";
import { planStrip, readTabGroups, setSectionCollapsed, writeTabGroups, TABGROUPS_EVENT, TABGROUPS_KEY, TABGROUPS_SHARED_KEY } from "./tab-groups";
import { viewTagUnion } from "./session-views";

const WEB = "11111111-2222-4333-8444-000000000001";
const API = "11111111-2222-4333-8444-000000000002";
const TESTS = "11111111-2222-4333-8444-000000000003";
const VIEWS = { active: "all", tags: [{ id: "g1", name: "api", color: "#1EA1EB", members: [API, TESTS] }, { id: "g2", name: "web", color: "#4EC9B0", members: [WEB] }] };
const UNIONS = viewTagUnion(VIEWS);

/** The kernel's viewer store and its push, as kernel.py implements them (_write_view_folds, _view_order_frame), with the
 *  publish's name (2026-09-29) and the count of taken publishes (2026-09-30). Every publish not dropped is TAKEN: the
 *  count rises by one whether it is written, unchanged or refused, the name of the last one taken rides the frame as
 *  `foldsBy` (an unnamed publish clears it), and the count as `foldsSeq` with the kernel's life. For each browser the
 *  kernel keeps the (n, count) pairs it took (2026-10-01): a take removes every pair with n at or under its own and
 *  appends its own, so n runs descending and the count ascending, the kept (latest) pair last and the largest first, and
 *  past PAIRS_CAP the first is forgotten. A browser's publish numbered under a pair is judged against the pair with the
 *  smallest n above its own, and dropped unless the count it says its browser had received (`by.received`) is of this
 *  life and at or above that pair's count; a drop is logged under that pair ("kept", "largest" or "between") and answered
 *  with the kernel's frame on the poster's own slot, which sends nothing when that slot already holds the same frame. */
const PAIRS_CAP = 16;                         // kernel.py _VIEW_FOLDS_PAIRS_CAP
function fakeKernel(opts: { folds?: boolean; stamps?: boolean } = {}) {
  const keepsFolds = opts.folds !== false;    // a kernel from before 2026-09-23 sends no folds half at all
  const keepsStamps = opts.stamps !== false;  // one from before 2026-09-29 keeps no name and no count, and serves neither
  let life = 1;
  const k = {
    boot: "1000.1",                           // the kernel life's id (kernel.py _BOOT_ID), a new one per restart()
    taken: 0,                                 // the fold publishes this life has taken (kernel.py _view_folds_taken)
    folds: null as Record<string, unknown> | null,
    by: null as { browser: string; n: number } | null,
    // browser -> its pairs, [n, count] with n descending, and their two ends: the kept (latest taken) and the largest
    seen: new Map<string, { n: number; at: number; top: number; topAt: number; pairs: [number, number][] }>(),
    refuse: false,
    warns: 0,
    drops: [] as string[],                    // the kernel's log line for each publish it dropped
    viewers: [] as Viewer[],
    posts: [] as unknown[],
    bys: [] as unknown[],
    slot: new Map<Viewer, string>(),          // the last viewOrder frame sent to each viewer's socket (its dedup slot)
    frame(): any {
      const fr: any = { type: "viewOrder", order: [], stored: false };
      if (keepsFolds) {
        fr.folds = k.folds === null ? null : JSON.parse(JSON.stringify(k.folds));
        if (keepsStamps && k.by) fr.foldsBy = { ...k.by };
        if (keepsStamps) fr.foldsSeq = { boot: k.boot, n: k.taken };
      }
      return fr;
    },
    send(v: Viewer, fr: any) { k.slot.set(v, JSON.stringify(fr)); v.deliver(fr); },
    post(folds: Record<string, unknown>, by?: any, from?: Viewer) {
      k.posts.push(folds);
      k.bys.push(by);
      if (!keepsFolds) return;                                      // an older kernel answers unknownOp; nothing is kept
      const st = keepsStamps && by && typeof by.browser === "string" && by.browser.length >= 1 && by.browser.length <= 64
        && Number.isInteger(by.n) && by.n >= 1 ? { browser: by.browser as string, n: by.n as number } : null;
      const r = st && by.received && typeof by.received.boot === "string" && Number.isInteger(by.received.n) ? by.received : null;
      const kept = st ? k.seen.get(st.browser) : undefined;
      const pairs: [number, number][] = kept ? kept.pairs.map((p) => [p[0], p[1]] as [number, number]) : [];
      const newer = (at: number) => !!r && r.boot === k.boot && r.n >= at;
      let above: [number, number] | undefined;           // the pair with the smallest n above this publish's: n runs descending
      if (st) for (let i = pairs.length - 1; i >= 0; i--) if (pairs[i][0] > st.n) { above = pairs[i]; break; }
      if (st && above && !newer(above[1])) {
        // this browser's overtaken state: logged under that pair, and answered on the poster's own slot
        const under = above === pairs[pairs.length - 1] ? "kept" : above === pairs[0] ? "largest" : "between";
        k.drops.push(`browser '${st.browser.slice(0, 8)}' n=${st.n} received=${r ? `'${r.boot}':${r.n}` : "none"}, ${under} n=${above[0]} taken at ${above[1]}`);
        if (from && from.connected) k.answer(from);
        return;
      }
      k.taken++;
      if (st) {
        const next: [number, number][] = [...pairs.filter((p) => p[0] > st.n), [st.n, k.taken]];
        while (next.length > PAIRS_CAP) next.shift();
        k.seen.set(st.browser, { n: st.n, at: k.taken, top: next[0][0], topAt: next[0][1], pairs: next });
      }
      k.by = st;
      if (k.refuse) { k.warns++; return; }                          // refused: a warning to the poster, no push
      if (k.folds !== null && JSON.stringify(k.folds) === JSON.stringify(folds)) return;   // unchanged: no push
      k.folds = JSON.parse(JSON.stringify(folds));
      k.push();
    },
    /** the kernel's frame to one socket on its dedup slot: nothing when the slot already holds this very frame */
    answer(v: Viewer) { const fr = k.frame(); if (k.slot.get(v) !== JSON.stringify(fr)) k.send(v, fr); },
    /** the one viewOrder push to every connected viewer (a dropped socket hears nothing): a fold change's, or an
     *  arrangement change's, which carries the folds, their name and the count as they stand */
    push() { for (const v of k.viewers) if (v.connected) k.send(v, k.frame()); },
    connect(v: Viewer) { v.connected = true; k.slot.delete(v); k.send(v, k.frame()); },
    /** the page's socket drops: the kernel no longer reaches it, and what it sends waits in the shim's queue
     *  (the page shim queues every send while its socket is down and flushes the queue when a new one opens) */
    drop(v: Viewer) { v.connected = false; },
    /** a FRESH dial: a new socket (an empty slot) opens, the shim flushes what the page queued while down, the
     *  reconnect's `wsup` frame reaches the page, then (before the kernel's viewOrder frame) whatever the connect push's
     *  earlier frames make the page write (`beforeFrame`: the views frame's rename follow, say), then the viewOrder frame
     *  of the connect push, which the ready this dial sends earns */
    reconnect(v: Viewer, beforeFrame?: () => void) {
      v.connected = true;
      k.slot.delete(v);
      for (const m of v.queued.splice(0)) k.post(m.folds, m.by, v);
      v.deliver({ type: "wsup" });
      if (beforeFrame) beforeFrame();
      k.send(v, k.frame());
    },
    /** a DECLARED redial: the shim's socket comes back without a new ready, so no connect push answers it, and the page
     *  hears no viewOrder frame until the kernel next pushes one (a change, on any device) */
    declaredReconnect(v: Viewer) {
      v.connected = true;
      for (const m of v.queued.splice(0)) k.post(m.folds, m.by, v);
      v.deliver({ type: "wsup" });
    },
    /** the kernel restarts: a new life, counting from nothing, with no name and no browsers remembered (memory only);
     *  the folds on disk stand, and every socket drops */
    restart() {
      life++;
      k.boot = "1000." + life;
      k.taken = 0;
      k.seen.clear();
      k.by = null;
      for (const v of k.viewers) v.connected = false;
    },
  };
  return k;
}
type Kernel = ReturnType<typeof fakeKernel>;

interface Viewer {
  name: string;
  connected: boolean;
  queued: any[];   // the messages sent while the socket was down (the shim's queue)
  store: Map<string, string>;
  fm: any;
  repaints: number;
  as<T>(fn: () => T): T;
  deliver(frame: any): void;
  shared(): any;
}

function makeViewer(name: string, k: Kernel, seed: Record<string, unknown> = {}, sharedStore?: Map<string, string>): Viewer {
  const g: any = globalThis;
  const store = sharedStore ?? new Map<string, string>();   // two panes of one browser share one store
  for (const [key, val] of Object.entries(seed)) store.set(key, JSON.stringify(val));
  const win: any = new EventTarget();
  win.__rompApp = "chat";
  const localStorage = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, val: string) => { store.set(key, val); },
    removeItem: (key: string) => { store.delete(key); },
  };
  const v: Viewer = {
    name, store, fm: null, repaints: 0, connected: false, queued: [],
    as<T>(fn: () => T): T {
      const saved: Record<string, [boolean, unknown]> = {};
      for (const key of ["window", "document", "localStorage", "setInterval", "fetch"]) saved[key] = [key in g, g[key]];
      g.window = win;
      g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
      g.localStorage = localStorage;
      g.setInterval = () => 0;
      g.fetch = () => new Promise(() => {});
      try { return fn(); } finally {
        for (const key of Object.keys(saved)) { const [had, val] = saved[key]; if (had) g[key] = val; else delete g[key]; }
      }
    },
    deliver(frame: any) { v.as(() => v.fm.inbound("", frame)); },
    shared() { const raw = store.get(TABGROUPS_SHARED_KEY); return raw === undefined ? undefined : JSON.parse(raw); },
  };
  // render.ts repaints the strip on this event (window.addEventListener(TABGROUPS_EVENT, () => renderTabs())): counted here
  win.addEventListener(TABGROUPS_EVENT, () => { v.repaints++; });
  v.as(() => {
    const fm: any = new FederationManager();
    fm.start();
    fm.outbound = (m: any) => { if (m && m.type === "setViewFolds") { if (v.connected) k.post(m.folds, m.by, v); else v.queued.push(m); } };
    v.fm = fm;
  });
  k.viewers.push(v);
  return v;
}

/** The strip each viewer would paint now: the plan over its own store, the phone's layout for the phone. */
const shape = (v: Viewer, phone: boolean, active: string | null = WEB) => v.as(() =>
  planStrip([WEB, API, TESTS], UNIONS, readTabGroups(UNIONS), active, phone).items.map((i) => ("head" in i ? `#${i.head.name}${i.folded ? "(folded)" : ""}` : i.id)));
/** A fold as the strip's header click (and the phone picker's heading, through it) writes it. */
const fold = (v: Viewer, group: string, folded: boolean) => v.as(() => writeTabGroups(setSectionCollapsed(readTabGroups(UNIONS), group, folded)));

// ── the headline ─────────────────────────────────────────────────────────────────────────────────────
test("a group folded on the desktop folds on the phone, live, and opened on the phone opens on the desktop", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  assert.deepEqual(shape(phone, true), ["#api", API, TESTS, "#web", WEB], "both open to start");
  const before = phone.repaints;
  fold(desktop, "api", true);
  assert.deepEqual(k.folds, { collapsed: ["api"], expanded: [], pinned: [] }, "the kernel keeps the fold");
  assert.deepEqual(phone.shared().collapsed, ["api"], "the phone has it, off the kernel's push");
  assert.ok(phone.repaints > before, "…and was told to repaint: no reload, nothing polling");
  assert.deepEqual(shape(phone, true), ["#api(folded)", "#web", WEB], "the phone's picker lists api's heading alone");
  assert.deepEqual(shape(desktop, false), ["#api(folded)", "#web", WEB]);
  // the phone opens it again: the desktop follows
  fold(phone, "api", false);
  assert.deepEqual(k.folds, { collapsed: [], expanded: [], pinned: [] });
  assert.deepEqual(shape(desktop, false), ["#api", API, TESTS, "#web", WEB], "opened on the phone, open on the desktop");
});

test("a pushed fold that changes nothing is not announced: every viewer sees its own publish come back", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true);
  const d = desktop.repaints, p = phone.repaints, posts = k.posts.length;
  k.connect(phone);                                                // a reconnect re-serves the same folds
  assert.equal(phone.repaints, p, "the same folds again: no repaint");
  fold(phone, "api", true);                                        // folding what is already folded
  assert.equal(k.posts.length, posts, "nothing changed, nothing published");
  desktop.as(() => writeTabGroups({ ...readTabGroups(UNIONS), on: false }));   // this browser's own switch
  assert.equal(k.posts.length, posts, "the group switch is this browser's: it publishes nothing");
  assert.equal(desktop.repaints, d + 1, "…and repaints its own strip");
  assert.equal(phone.shared().collapsed.length, 1);
});

test("the pins ride with the folds: a member set to show through its fold on the desktop shows through on the phone", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  desktop.as(() => writeTabGroups({ ...setSectionCollapsed(readTabGroups(UNIONS), "api", true), pinned: [{ sid: TESTS, name: "api", id: "g1" }] }));
  assert.deepEqual(shape(phone, true), ["#api(folded)", TESTS, "#web", WEB], "the pinned member keeps its row under the folded heading");
});

// ── the migration ─────────────────────────────────────────────────────────────────────────────────
test("a browser carrying folds of its own into a kernel with none publishes them, and the others take them", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k, { [TABGROUPS_KEY]: { on: true, collapsed: ["web"], expanded: [], pinned: [] } });   // folded before the move
  const phone = makeViewer("phone", k);
  k.connect(phone);
  assert.equal(k.posts.length, 0, "a phone with no folds of its own publishes nothing");
  k.connect(desktop);
  assert.deepEqual(k.folds, { collapsed: ["web"], expanded: [], pinned: [] }, "the upgrading browser's folds survive the move");
  assert.deepEqual(shape(phone, true), ["#api", API, TESTS, "#web(folded)"], "and the phone converges on them");
  assert.deepEqual(JSON.parse(desktop.store.get(TABGROUPS_KEY)!).collapsed, ["web"], "the pre-move key is left where it was");
});

test("a kernel that already keeps folds wins over a browser's own, and the browser's key is left in place", () => {
  const k = fakeKernel();
  k.folds = { collapsed: ["api"], expanded: [], pinned: [] };       // another device folded first
  const phone = makeViewer("phone", k, { [TABGROUPS_KEY]: { on: true, collapsed: ["web"], expanded: [], pinned: [] } });
  // before the kernel's word lands, the first paint is the fold this browser is used to
  assert.deepEqual(shape(phone, true), ["#api", API, TESTS, "#web(folded)"], "the first paint: this browser's pre-move folds");
  k.connect(phone);
  assert.equal(k.posts.length, 0, "nothing published over the kernel's");
  assert.deepEqual(shape(phone, true), ["#api(folded)", "#web", WEB], "the kernel's folds win");
  assert.deepEqual(JSON.parse(phone.store.get(TABGROUPS_KEY)!).collapsed, ["web"], "…without destroying the local key: reverting hands it back");
  // an EMPTIED kernel state (every group opened) is somebody's answer, not a missing one: never refilled from an old key
  const k2 = fakeKernel();
  k2.folds = { collapsed: [], expanded: [], pinned: [] };
  const laptop = makeViewer("laptop", k2, { [TABGROUPS_KEY]: { on: true, collapsed: ["web"], expanded: [], pinned: [] } });
  k2.connect(laptop);
  assert.deepEqual([k2.posts.length, shape(laptop, false)], [0, ["#api", API, TESTS, "#web", WEB]]);
});

test("a fold made before the kernel's folds reach the page lands OVER them: the gesture stands, and so does what another device folded", () => {
  // the connect push serves the strip (a header a person can click, and the views frame a rename follow runs on) before
  // the folds: a whole-state publish from then would put this browser's older copy over what another device folded since,
  // and adopting the kernel's on arrival would undo the click. The change waits as a delta and is merged over the kernel's
  const k = fakeKernel();
  k.folds = { collapsed: ["api"], expanded: [], pinned: [] };          // another device folded api
  const phone = makeViewer("phone", k);
  fold(phone, "web", true);                                            // clicked before the kernel's word arrived
  assert.equal(k.posts.length, 0, "not heard yet: nothing published");
  k.connect(phone);
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [] }, "both: the other device's fold and this click");
  assert.deepEqual(shape(phone, true), ["#api(folded)", "#web(folded)"]);
  fold(phone, "web", false);
  assert.deepEqual(k.folds, { collapsed: ["api"], expanded: [], pinned: [] }, "heard: from now on a fold here is published whole, at once");
});

test("a browser with folds from before the move that clicks before hearing: the kernel's folds win, the click lands on them", () => {
  const k = fakeKernel();
  k.folds = { collapsed: [], expanded: [], pinned: [] };               // the kernel's: every group open (somebody's answer)
  const desktop = makeViewer("desktop", k, { [TABGROUPS_KEY]: { on: true, collapsed: ["web"], expanded: [], pinned: [] } });
  fold(desktop, "api", true);                                          // over the pre-move folds the page painted first
  k.connect(desktop);
  assert.deepEqual(k.folds, { collapsed: ["api"], expanded: [], pinned: [] }, "web stays open (the kernel's), api folds (the click)");
  assert.deepEqual(JSON.parse(desktop.store.get(TABGROUPS_KEY)!).collapsed, ["web"], "the pre-move key still untouched");
});

test("two panes of ONE browser: a click in the pane that has not heard lands, whichever pane hears the kernel first", () => {
  // the sibling-pane race a real browser showed (tests/test_cold_boot_diet_browser.py): the unheard pane's click wrote the
  // browser's copy, and a later adoption of the kernel's older folds put the section back. The delta is origin-wide, so
  // the first pane of the browser to hear lands it
  for (const firstToHear of ["clicked", "other"] as const) {
    const k = fakeKernel();
    k.folds = { collapsed: ["api"], expanded: [], pinned: [] };
    const shared = new Map<string, string>();
    const clicked = makeViewer("clicked pane", k, {}, shared), other = makeViewer("other pane", k, {}, shared);
    shared.set(TABGROUPS_SHARED_KEY, JSON.stringify(k.folds));         // the browser's copy from its last visit: api folded
    fold(clicked, "api", false);                                       // opened before either pane heard
    if (firstToHear === "clicked") { k.connect(clicked); k.connect(other); } else { k.connect(other); k.connect(clicked); }
    assert.deepEqual(k.folds, { collapsed: [], expanded: [], pinned: [] }, firstToHear + " pane heard first: the click landed");
    assert.deepEqual(shape(other, false), ["#api", API, TESTS, "#web", WEB], firstToHear + ": and every pane shows it");
  }
});

test("a kernel that keeps no folds (one from before 2026-09-23) sends no folds half: the folds stay this browser's, nothing is published", () => {
  const k = fakeKernel({ folds: false });
  const phone = makeViewer("phone", k, { [TABGROUPS_KEY]: { on: true, collapsed: ["web"], expanded: [], pinned: [] } });
  k.connect(phone);
  fold(phone, "api", true);
  assert.equal(k.posts.length, 0, "no publisher was ever installed");
  assert.deepEqual(shape(phone, true), ["#api(folded)", "#web(folded)"], "the folds still work, per browser, as before");
});

test("a REMOTE kernel's folds are ignored: they belong to whoever sits in front of that machine", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k);
  k.connect(desktop);
  desktop.as(() => desktop.fm.inbound("TESTHOST", { type: "viewOrder", order: [], stored: true, folds: { collapsed: ["api", "web"], expanded: [], pinned: [] } }));
  assert.deepEqual(shape(desktop, false), ["#api", API, TESTS, "#web", WEB], "untouched");
});

// ── a dropped connection: the page stops speaking for the folds until the next one hears the kernel's ─────────────
test("a page whose connection drops while another device folds adopts the kernel's folds on reconnect and publishes nothing stale", () => {
  // the order half's per-connection gate (#2062, view-order.ts hearSharedOrder), applied to the folds: the publisher is the
  // current connection's, granted by its viewOrder frame and withdrawn by the reconnect. Kept across the drop, the page put
  // its pre-drop copy over the kernel's with the first write its reconnect made before that frame (here the connect push's
  // views frame, whose rename follow writes the memory): the fold another device made meanwhile was lost on every device
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true);
  k.drop(phone);                                                       // the phone goes offline…
  fold(desktop, "web", true);                                          // …while the desktop folds web
  assert.deepEqual(phone.shared().collapsed, ["api"], "premise: the phone never heard of web's fold");
  const before = k.posts.length;
  k.reconnect(phone, () => phone.as(() => writeTabGroups({ ...readTabGroups(UNIONS), followed: { g2: "web" } })));
  const fromPhone = k.posts.slice(before);
  assert.ok(fromPhone.every((f: any) => f.collapsed.includes("web")), "nothing the phone published drops the desktop's fold: " + JSON.stringify(fromPhone));
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [], followed: { g2: "web" } },
    "the kernel's folds stand, the phone's own change (the follow) landed over them");
  assert.deepEqual(shape(phone, true), ["#api(folded)", "#web(folded)"], "the phone adopted the desktop's fold");
  assert.deepEqual(shape(desktop, false), ["#api(folded)", "#web(folded)"]);
  // and a reconnect with no change of its own publishes nothing at all
  k.drop(phone); fold(desktop, "web", false);
  const quiet = k.posts.length;
  k.reconnect(phone);
  assert.equal(k.posts.length, quiet, "adopted, not answered");
  assert.deepEqual(shape(phone, true), ["#api(folded)", "#web", WEB]);
});

test("a fold made while offline is kept and merged over the kernel's folds when the next connection hears them", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true);
  k.drop(phone);
  phone.as(() => (globalThis as any).window.dispatchEvent(new Event("romp:wsdown")));   // the shim's own drop event
  fold(desktop, "web", true);                                          // the desktop folds web meanwhile
  fold(phone, "api", false);                                           // the phone, offline, opens api
  assert.deepEqual(phone.queued, [], "nothing queued to publish blind: the drop withdrew the publisher");
  assert.deepEqual(shape(phone, true), ["#api", API, TESTS, "#web", WEB], "the phone shows its own gesture meanwhile");
  k.reconnect(phone);
  assert.deepEqual(k.folds, { collapsed: ["web"], expanded: [], pinned: [] }, "api opened (the phone's), web folded (the desktop's)");
  assert.deepEqual(shape(desktop, false), ["#api", API, TESTS, "#web(folded)"], "and the desktop follows");
  fold(phone, "tests", true);
  assert.deepEqual(k.folds.collapsed, ["web", "tests"], "heard again: a fold here is published at once");
});

// ── this browser's own publishes coming back late (2026-09-29) ───────────────────────────────────────────────────
// The kernel answers a fold with a push to every viewer, the page that published it included. On a link with latency
// the answer to one fold can arrive after the next fold, and every pane of a browser hears it, each on its own socket
// and at its own moment, while they all share one cached copy and one pending change. Each publish is named
// ({browser, n}), the kernel names the last publish it handled (`foldsBy`), and a pane passes over a frame naming one of
// this browser's publishes older than one an open connection has sent since (tab-groups.ts hearSharedFolds).
const PENDING = "romp:tabgroups:pending";
const PUBLISHES = "romp:tabgroups:publishes";   // tab-groups.ts TABGROUPS_PUBLISHES_KEY
/** From here on the kernel's frames to `v` wait on the wire until `flush` delivers them, oldest first (a relayed host, a
 *  loaded box); what the page sends still reaches the kernel at once. */
function slowLink(v: Viewer) {
  const deliver = v.deliver;
  const inflight: any[] = [];
  v.deliver = (frame: any) => { inflight.push(frame); };
  return { inflight, flush(n = inflight.length) { for (const fr of inflight.splice(0, n)) deliver(fr); } };
}
/** From here on what `v` sends waits until `flush` hands it to the kernel (its socket is slow the other way). */
function slowSend(v: Viewer) {
  const send = v.fm.outbound;
  const held: any[] = [];
  v.fm.outbound = (m: any) => { held.push(m); };
  return { held, flush() { v.fm.outbound = send; for (const m of held.splice(0)) send(m); } };
}
/** From here on what `v` sends is lost on the wire, until `restore`. */
function loseSends(v: Viewer) {
  const send = v.fm.outbound;
  v.fm.outbound = () => {};
  return { restore() { v.fm.outbound = send; } };
}
const BOTH = ["#api(folded)", "#web(folded)"];

test("two groups folded inside one round trip stay folded: the kernel's answer to the first fold does not reopen the second", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  const link = slowLink(desktop);
  fold(desktop, "api", true);
  fold(desktop, "web", true);                                          // before the kernel's answer to the first reached the page
  assert.deepEqual([k.folds, link.inflight.length], [{ collapsed: ["api", "web"], expanded: [], pinned: [] }, 2],
    "premise: the kernel keeps both folds, and its two answers are on the wire");
  const painted = desktop.repaints;
  link.flush(1);                                                       // the answer to the first fold lands
  assert.deepEqual(shape(desktop, false), BOTH, "web stays folded (before: it reopened until the second answer landed)");
  assert.equal(desktop.repaints, painted, "and the strip is not repainted for it");
  link.flush();
  assert.deepEqual([shape(desktop, false), shape(phone, true)], [BOTH, BOTH], "the answer to the second is what both show");
  fold(phone, "api", false);                                           // another device's fold afterwards is the kernel's word
  link.flush();
  assert.deepEqual(shape(desktop, false), ["#api", API, TESTS, "#web(folded)"], "the desktop follows the phone as before");
});

test("a group folded and opened again inside one round trip never folds on an answer", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k);
  k.connect(desktop);
  const link = slowLink(desktop);
  fold(desktop, "api", true); fold(desktop, "web", true); fold(desktop, "web", false);   // the first and the last publish alike
  const shown: string[][] = [];
  for (let i = 0; i < 3; i++) { link.flush(1); shown.push(desktop.as(() => readTabGroups(UNIONS).collapsed)); }
  assert.deepEqual(shown, [["api"], ["api"], ["api"]], "the second answer is older than the third publish: web never folds");
});

test("a sibling pane that hears the late answer first leaves the browser's copy alone, so the clicked pane stays folded", () => {
  // every pane of a dashboard is a document of its own with its own socket, and every one hears every frame; they share
  // the browser's copy, and the clicked pane repaints from it (the storage event), so the sibling's hearing is the one
  // that decides what the clicked pane shows
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [clicked, sibling, phone]) k.connect(v);
  const l1 = slowLink(clicked), l2 = slowLink(sibling);
  fold(clicked, "api", true);
  fold(clicked, "web", true);
  l2.flush(1);                                                         // the sibling hears the answer to api's fold first
  assert.deepEqual(clicked.shared().collapsed, ["api", "web"], "the browser's copy keeps web folded");
  assert.deepEqual(shape(clicked, false), BOTH, "so the clicked pane, repainting from it, shows both");
  l1.flush(1);
  assert.deepEqual(shape(clicked, false), BOTH);
  l1.flush(); l2.flush();
  assert.deepEqual([shape(clicked, false), shape(sibling, false), shape(phone, true)], [BOTH, BOTH, BOTH]);
  fold(phone, "api", false);
  l1.flush(); l2.flush();
  assert.deepEqual(shape(sibling, false), ["#api", API, TESTS, "#web(folded)"], "the phone's change afterwards is taken by every pane");
});

test("a change a third pane made before hearing lands over the kernel's current folds, even when a sibling hears the late answer first", () => {
  // the pending change is merged over whatever frame a pane of this browser hears next; merged over the late answer, it
  // put that older state back up whole and the second fold was lost on the kernel, not only flapped here
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared), unheard = makeViewer("unheard pane", k, {}, shared);
  k.connect(clicked); k.connect(sibling);                             // the third pane's socket has not connected yet
  const l1 = slowLink(clicked), l2 = slowLink(sibling);
  fold(clicked, "api", true);
  fold(clicked, "web", true);
  const pin = { sid: TESTS, name: "api", id: "g1" };
  unheard.as(() => writeTabGroups({ ...readTabGroups(UNIONS), pinned: [pin] }));   // no publisher there: kept as a delta
  assert.ok(shared.has(PENDING), "premise: the unheard pane's change waits as a delta");
  l2.flush(1);                                                         // the sibling hears the answer to api's fold
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [] }, "nothing published over the late answer");
  assert.ok(shared.has(PENDING), "and the change still waits for the kernel's current folds");
  for (let i = 0; i < 3; i++) { l1.flush(); l2.flush(); }
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [pin] }, "the pin lands, web's fold kept");
  assert.deepEqual([shape(clicked, false), shape(sibling, false)], [["#api(folded)", TESTS, "#web(folded)"], ["#api(folded)", TESTS, "#web(folded)"]]);
});

test("a sibling that has heard every answer does not end the wait for the clicked pane, which hears the first answer last", () => {
  // the panes hear one frame at different moments, so the first pane to hear an answer cannot retire the wait for the
  // others: the clicked pane still has the late answer ahead of it on its own socket
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared);
  for (const v of [clicked, sibling]) k.connect(v);
  const l1 = slowLink(clicked), l2 = slowLink(sibling);
  fold(clicked, "api", true);
  fold(clicked, "web", true);
  const copies: string[] = [];
  const set = shared.set.bind(shared);
  shared.set = (key: string, val: string) => { if (key === TABGROUPS_SHARED_KEY) copies.push(val); return set(key, val); };
  const painted = clicked.repaints;
  l2.flush();                                                          // the sibling hears both answers
  l1.flush(1);                                                         // then the clicked pane hears the first
  assert.deepEqual(shape(clicked, false), BOTH, "the clicked pane still shows both");
  assert.deepEqual(clicked.shared().collapsed, ["api", "web"]);
  assert.equal(clicked.repaints, painted, "and was not repainted");
  assert.deepEqual(copies, [], "the browser's copy was never rewritten, so no pane was told to repaint either");
  l1.flush();
  assert.deepEqual([shape(clicked, false), shape(sibling, false)], [BOTH, BOTH]);
});

test("a change a pane made before hearing lands over the kernel's current folds, not over a late answer to this browser's earlier fold", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const heard = makeViewer("heard pane", k, {}, shared), unheard = makeViewer("unheard pane", k, {}, shared);
  k.connect(heard);                                                    // the sibling's socket has not connected yet
  const link = slowLink(heard);
  fold(heard, "api", true);
  fold(heard, "web", true);                                            // both answers still on the wire
  const pin = { sid: TESTS, name: "api", id: "g1" };
  unheard.as(() => writeTabGroups({ ...readTabGroups(UNIONS), pinned: [pin] }));   // no publisher there: kept as a change over what the browser showed
  link.flush(1);                                                       // the answer to api's fold
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [] }, "nothing published over the late answer");
  assert.ok(shared.has(PENDING), "the change waits for the next frame");
  link.flush();                                                        // the answer to web's fold: the kernel's current folds
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [pin] }, "the sibling's pin lands over them, web's fold kept (before: lost on the kernel)");
  link.flush();
  assert.deepEqual(shape(heard, false), ["#api(folded)", TESTS, "#web(folded)"], "and the pane shows both folds with the pin");
});

test("two panes of one browser fold inside one round trip and their publishes land out of order: the kernel keeps the later state", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const strip = makeViewer("chat pane", k, {}, shared), lanes = makeViewer("sessions pane", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [strip, lanes, phone]) k.connect(v);
  const out = slowSend(strip);
  fold(strip, "api", true);
  fold(lanes, "web", true);
  assert.deepEqual([out.held.length, (k.folds as any).collapsed], [1, ["api", "web"]], "premise: the Sessions pane's publish, carrying both folds, landed first");
  out.flush();
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [] }, "the chat pane's older publish does not put api alone back");
  assert.equal(k.drops.length, 1, "dropped as an older state, and logged (its answer is the frame that pane's slot already holds)");
  assert.deepEqual([shape(phone, true), shape(strip, false)], [BOTH, BOTH]);
});

test("a browser whose record of its publishes comes back older (killed before its storage was written) still lands its next fold", () => {
  // the kernel drops a browser's publish numbered below one it already handled from that browser, so a record set back
  // would have its next folds dropped with no push and no warning; the frame naming this browser's last publish says
  // how far the kernel got, and the next publish is numbered past it
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const desktop = makeViewer("desktop", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true); fold(desktop, "web", true); fold(desktop, "web", false);
  const rec = JSON.parse(shared.get(PUBLISHES)!);
  assert.deepEqual([rec.n, k.seen.get(rec.browser)?.n], [3, 3], "premise: three publishes, all handled");
  k.drop(desktop);
  shared.set(PUBLISHES, JSON.stringify({ browser: rec.browser, n: 1, live: {} }));   // the browser comes back with the record of its first
  const again = makeViewer("desktop after the restart", k, {}, shared);
  k.connect(again);                                                    // its connect push names publish 3
  fold(again, "api", false);
  assert.deepEqual([(k.bys.at(-1) as any).n, k.folds], [4, { collapsed: [], expanded: [], pinned: [] }], "numbered past 3, so the kernel takes it");
  assert.deepEqual(shape(phone, false), ["#api", API, TESTS, "#web", WEB], "and the phone opens api too");
});

test("every publish is named: one browser id and one rising number across its panes, another browser's own id", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const a = makeViewer("pane a", k, {}, shared), b = makeViewer("pane b", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [a, b, phone]) k.connect(v);
  fold(a, "api", true); fold(b, "web", true); fold(phone, "api", false);
  const [s1, s2, s3] = k.bys as any[];
  assert.ok(s1 && s2 && s3, "named: " + JSON.stringify(k.bys));
  assert.equal(s1.browser, s2.browser, "one browser, one id");
  assert.ok(s1.n < s2.n, "one number across the panes");
  assert.notEqual(s3.browser, s1.browser);
});

test("another device's change landing between this browser's publishes is taken when it arrives", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  const link = slowLink(desktop);
  fold(desktop, "api", true);
  const pin = { sid: TESTS, name: "api", id: "g1" };
  phone.as(() => writeTabGroups({ ...readTabGroups(UNIONS), pinned: [pin] }));
  fold(desktop, "web", true);
  assert.equal(link.inflight.length, 3, "premise: three frames on the wire");
  link.flush(2);
  assert.deepEqual(shape(desktop, false), ["#api(folded)", TESTS, "#web", WEB], "the phone's pin is new information: taken the moment it arrives");
  link.flush();
  assert.deepEqual(shape(desktop, false), BOTH, "and the desktop's later fold, the kernel's last write, after it");
  assert.deepEqual(k.folds, { collapsed: ["api", "web"], expanded: [], pinned: [] });
});

test("a publish the kernel already holds gets no answer, and nothing waits for one", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  const link = slowLink(desktop);
  fold(desktop, "api", true);
  fold(phone, "web", true);
  fold(desktop, "web", true);
  assert.deepEqual([link.inflight.length, (k.folds as any).collapsed], [2, ["api", "web"]], "premise: the answer to the first and the phone's push, nothing for the second");
  link.flush();
  assert.deepEqual(shape(desktop, false), BOTH);
  fold(phone, "api", false);
  link.flush();
  assert.deepEqual(shape(desktop, false), ["#api", API, TESTS, "#web(folded)"], "the next change is taken");
  k.push(); link.flush();
  assert.deepEqual(shape(desktop, false), ["#api", API, TESTS, "#web(folded)"]);
});

test("a publish the kernel refuses gets a warning and no frame: the next frame is taken, as before", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true);
  k.refuse = true;
  fold(desktop, "web", true);
  assert.deepEqual([(k.folds as any).collapsed, k.warns], [["api"], 1], "premise: refused, said, nothing pushed");
  assert.deepEqual(shape(desktop, false), BOTH, "the page keeps what it was clicked to, as before");
  k.refuse = false;
  k.push();                                                            // an arrangement change, say: the folds as they stand
  assert.deepEqual(shape(desktop, false), ["#api(folded)", "#web", WEB], "the next push is taken: web's fold was never kept");
});

test("a connection that drops with a publish lost on the wire: the next connection's folds are the kernel's word in every pane", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared);
  for (const v of [clicked, sibling]) k.connect(v);
  fold(clicked, "api", true);
  const lost = loseSends(clicked);
  fold(clicked, "web", true);
  assert.deepEqual((k.folds as any).collapsed, ["api"], "premise: web's fold never reached the kernel");
  lost.restore(); k.drop(clicked); k.reconnect(clicked);
  assert.deepEqual([shape(clicked, false), shape(sibling, false)], [["#api(folded)", "#web", WEB], ["#api(folded)", "#web", WEB]], "the kernel's word, in both panes");
  k.push();
  assert.deepEqual(shape(sibling, false), ["#api(folded)", "#web", WEB], "and the sibling takes the kernel's frames again");
});

test("a pane that goes away with a publish lost takes its wait with it: its sibling takes the next frame", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared);
  for (const v of [clicked, sibling]) k.connect(v);
  fold(clicked, "api", true);
  loseSends(clicked);
  fold(clicked, "web", true);
  clicked.as(() => (globalThis as any).window.dispatchEvent(new Event("pagehide")));
  k.drop(clicked);
  k.push();
  assert.deepEqual(shape(sibling, false), ["#api(folded)", "#web", WEB], "the kernel's folds: the closed pane's fold will never come");
});

test("a sibling pane that reconnects ends only its own wait: the clicked pane's late answer is still passed over", () => {
  // the connection id is the DOCUMENT's (kept on its window): a sibling's new connection retires the sibling's entry, and
  // the clicked pane, whose answers are still on its own socket, keeps waiting for them
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const sibling = makeViewer("sibling pane", k, {}, shared), clicked = makeViewer("clicked pane", k, {}, shared);
  for (const v of [sibling, clicked]) k.connect(v);                   // the clicked pane connected last
  const link = slowLink(clicked);
  fold(clicked, "api", true);
  fold(clicked, "web", true);
  k.drop(sibling); k.reconnect(sibling);                              // the sibling's socket drops and comes back
  assert.deepEqual(shape(sibling, false), BOTH, "premise: the sibling's new connection heard the kernel's current folds");
  link.flush(1);                                                       // the clicked pane's late answer to api's fold
  assert.deepEqual(shape(clicked, false), BOTH, "web stays folded");
  link.flush();
  assert.deepEqual([shape(clicked, false), shape(sibling, false)], [BOTH, BOTH]);
});

test("a kernel that names no publish (one from before the name): every frame is taken as before", () => {
  const k = fakeKernel({ stamps: false });
  const desktop = makeViewer("desktop", k);
  k.connect(desktop);
  const link = slowLink(desktop);
  fold(desktop, "api", true); fold(desktop, "web", true);
  link.flush(1);
  assert.deepEqual(shape(desktop, false), ["#api(folded)", "#web", WEB], "without a name the answer cannot be told from another device's word");
  link.flush();
  assert.deepEqual(shape(desktop, false), BOTH);
});

test("a REMOTE kernel's frame is ignored even when it names this browser's own publish", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k);
  k.connect(desktop);
  fold(desktop, "api", true); fold(desktop, "web", true);
  let browser = "unknown";
  try { browser = JSON.parse(desktop.store.get(PUBLISHES)!).browser; } catch { /* a page that names nothing */ }
  desktop.as(() => desktop.fm.inbound("TESTHOST", { type: "viewOrder", order: [], stored: true, folds: { collapsed: [], expanded: [], pinned: [] }, foldsBy: { browser, n: 99 } }));
  assert.deepEqual(shape(desktop, false), BOTH, "untouched");
  k.push();
  assert.deepEqual(shape(desktop, false), BOTH, "and the local kernel's next frame is taken");
});

// ── a frame built before this browser's publish was taken, whoever it names (2026-09-30) ─────────────────────────────
// A name says whose publish a frame reflects, not whether this browser's latest had landed when it was built: a push for
// an arrangement change (a drag on either device, a session arriving, closing or pruned) built inside one round trip
// before a fold names another device's publish, or nothing, and lacks the fold. The kernel's count of taken publishes
// (`foldsSeq`) on every frame, and the count each publish says its browser had received, tell such a frame apart.
test("a push built before this browser's fold landed does not reopen the group when it names another device", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(phone, "web", true);                                            // the kernel's last publish is the phone's
  const link = slowLink(desktop);
  k.push();                                                            // a drag on the phone: its push is on the wire…
  fold(desktop, "api", true);                                          // …when the desktop folds api
  assert.equal(link.inflight.length, 2, "premise: the drag's push, then the fold's answer");
  link.flush(1);                                                       // the drag's push: it names the phone and lacks api's fold
  assert.deepEqual(shape(desktop, false), BOTH, "api stays folded (before: it reopened until its own answer landed)");
  link.flush();
  assert.deepEqual([shape(desktop, false), shape(phone, true)], [BOTH, BOTH]);
});

test("…nor when it names no publish at all (a page from before the name published last)", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k);
  k.connect(desktop);
  k.post({ collapsed: ["web"], expanded: [], pinned: [] });            // an older page folds web: unnamed, so frames name nobody
  const link = slowLink(desktop);
  k.push();                                                            // a session arrives: a push naming nothing
  fold(desktop, "api", true);
  link.flush(1);
  assert.deepEqual(shape(desktop, false), BOTH, "api stays folded");
  link.flush();
  assert.deepEqual(shape(desktop, false), BOTH);
});

test("a change waiting in an unheard pane is not merged over a push built before this browser's fold landed: the fold stands on every device", () => {
  // merged over that push, the change went up without the fold, the kernel took it, and every device showed the group open
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [clicked, phone]) k.connect(v);
  fold(phone, "web", true);
  const unheard = makeViewer("unheard pane", k, {}, shared);
  const l3 = slowLink(unheard);
  k.connect(unheard);                                                  // its connect push is still on the wire
  const l1 = slowLink(clicked);
  k.push();                                                            // a drag's push, on the wire to both panes…
  fold(clicked, "api", true);                                          // …when the clicked pane folds api
  const pin = { sid: TESTS, name: "api", id: "g1" };
  unheard.as(() => writeTabGroups({ ...readTabGroups(UNIONS), pinned: [pin] }));   // no publisher there: kept as a change
  l3.flush(2);                                                         // the unheard pane hears the connect push, then the drag's
  assert.deepEqual(k.folds, { collapsed: ["web", "api"], expanded: [], pinned: [] }, "nothing merged over either: api's fold stands on the kernel");
  assert.ok(shared.has(PENDING), "the change waits for a frame built after the fold");
  l3.flush(); l1.flush();
  assert.deepEqual(k.folds, { collapsed: ["web", "api"], expanded: [], pinned: [pin] }, "then lands over it");
  assert.deepEqual(shape(phone, true), ["#api(folded)", TESTS, "#web(folded)"], "and every device shows both folds and the pin");
});

test("a push saying the kernel keeps no folds, built before this browser's fold was taken, is passed over too: a change waiting in an unheard pane lands", () => {
  // the kernel keeps none (its first run since the folds were shared, a new state directory, a lost store). Heard, the null
  // push spent a change that leaves nothing folded, opened or pinned (against none a page publishes only folds that hold
  // something), and once the fold's answer was heard the group was folded on every device, the change on none
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const first = makeViewer("first pane", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [first, phone]) k.connect(v);                        // both hear that the kernel keeps none
  const unheard = makeViewer("unheard pane", k, {}, shared);
  const l2 = slowLink(unheard);
  k.connect(unheard);                                                  // its connect push, folds null, is on the wire…
  const l1 = slowLink(first);
  fold(first, "web", true);                                            // …when the first pane folds web
  assert.deepEqual(k.folds, { collapsed: ["web"], expanded: [], pinned: [] }, "premise: the kernel took the fold");
  fold(unheard, "web", false);                                         // the unheard pane opens it again: kept as a change
  l2.flush(1);                                                         // the null push, built before the fold was taken
  assert.ok(shared.has(PENDING), "passed over: the change waits (before: spent on the null push, and nothing published)");
  l2.flush(); l1.flush();                                              // the fold's answer, heard by both panes
  assert.deepEqual(k.folds, { collapsed: [], expanded: [], pinned: [] }, "the change landed over the fold");
  const open = ["#api", API, TESTS, "#web", WEB];
  assert.deepEqual([shape(first, false), shape(unheard, false), shape(phone, true)], [open, open, open], "and web is open on every device");
});

test("a group opened while a push built before it is on the wire never shows folded again, for the push or for its own late answer", () => {
  // before: the push was taken, folding api back, and api's own answer, older than the web fold made after it, was passed
  // over, so api stayed folded one frame longer than on a page that took every frame
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true);
  phone.as(() => writeTabGroups({ ...readTabGroups(UNIONS), pinned: [{ sid: WEB, name: "web", id: "g2" }] }));   // the phone published last
  const link = slowLink(desktop);
  k.push();                                                            // a drag's push: api folded, as the kernel has it
  fold(desktop, "api", false);                                         // the desktop opens api…
  fold(desktop, "web", true);                                          // …and folds web, inside one round trip
  const shown: string[][] = [];
  for (let i = 0; i < 3; i++) { link.flush(1); shown.push(desktop.as(() => readTabGroups(UNIONS).collapsed)); }
  assert.deepEqual(shown, [["web"], ["web"], ["web"]], "api open throughout, web folded");
});

test("a browser whose record comes back older lands its next fold even when another device published first, so no frame named it", () => {
  // the kernel keeps this browser's publish 3 and the record says 1; the connect push names the phone, so nothing raised
  // the number past 3 and the publish goes out as 2. It carries the count that connect push had, built after 3 was taken,
  // so the kernel takes it (before: dropped, with no push, no warning and no log line)
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const desktop = makeViewer("desktop", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true); fold(desktop, "web", true); fold(desktop, "web", false);
  const rec = JSON.parse(shared.get(PUBLISHES)!);
  k.drop(desktop);
  shared.set(PUBLISHES, JSON.stringify({ browser: rec.browser, n: 1, live: {} }));   // the browser comes back with the record of its first
  fold(phone, "web", true);                                            // meanwhile the phone publishes
  const again = makeViewer("desktop after the restart", k, {}, shared);
  k.connect(again);
  fold(again, "api", false);
  assert.deepEqual([(k.bys.at(-1) as any).n, k.drops], [2, []], "numbered under the kernel's 3, and taken");
  assert.deepEqual(k.folds, { collapsed: ["web"], expanded: [], pinned: [] });
  assert.deepEqual(shape(phone, false), ["#api", API, TESTS, "#web(folded)"], "and the phone opens api");
});

test("a publish dropped as an older state is logged and answered: the page shows the kernel's folds at once, and its next fold lands", () => {
  // two renderer processes of one browser publishing inside the storage delay: this tab's record lags the other's, so its
  // publish goes out numbered under the one the kernel kept, before it received any frame built after that one
  const k = fakeKernel();
  const E = "e".repeat(32);
  k.folds = { collapsed: ["api"], expanded: [], pinned: [] };
  const tab = makeViewer("tab", k, { [PUBLISHES]: { browser: E, n: 2, live: {} } });
  k.connect(tab);
  k.post({ collapsed: ["api"], expanded: [], pinned: [] }, { browser: E, n: 5 });   // the other tab's publish 5: unchanged, so no push
  fold(tab, "web", true);                                              // numbered 3 here, made before any frame built after 5
  assert.deepEqual([(k.bys.at(-1) as any).n, k.drops.length, k.folds], [3, 1, { collapsed: ["api"], expanded: [], pinned: [] }], "dropped, and logged");
  assert.deepEqual(shape(tab, false), ["#api(folded)", "#web", WEB], "answered with the kernel's frame: the tab shows the kernel's folds, not a fold no device keeps");
  fold(tab, "web", true);
  assert.deepEqual([(k.bys.at(-1) as any).n, k.folds], [6, { collapsed: ["api", "web"], expanded: [], pinned: [] }], "numbered past the 5 the answer named: taken");
});

test("a lower number taken on its count lets no older publish between it and the largest back in: two tabs, and all three folds stand", () => {
  // two tabs of one browser in separate renderer processes. In tab T, pane A folds api on a slow socket (publish 10) and
  // pane B folds web over it (11), which lands first. Tab Y, whose copy of the storage lacks both, hears 11's count through
  // a sibling pane and folds tests as its own publish 10, taken on that count. Pane A's 10 then lands: not under the kept
  // 10, but under the largest taken, 11, and made before its browser received a frame built after 11 was taken, so it is
  // dropped (a kernel that kept only the latest took it, and went back to api alone)
  const k = fakeKernel();
  k.folds = { collapsed: [], expanded: [], pinned: [] };
  const E = "e".repeat(32);
  const t = new Map<string, string>([[PUBLISHES, JSON.stringify({ browser: E, n: 9, live: {} })]]);
  const y = new Map(t);                                                // tab Y's copy of the storage
  const a = makeViewer("pane A", k, {}, t), b = makeViewer("pane B", k, {}, t);
  const ySibling = makeViewer("Y's sibling pane", k, {}, y), yPane = makeViewer("Y's pane", k, {}, y);
  for (const v of [a, b, ySibling, yPane]) k.connect(v);
  const out = slowSend(a);
  fold(a, "api", true);                                                // T's publish 10, held on the slow socket
  const yLink = slowLink(yPane);
  fold(b, "web", true);                                                // T's publish 11, taken first; Y's sibling hears it
  fold(yPane, "tests", true);                                          // Y's publish 10, on 11's count
  out.flush();                                                         // T's publish 10 lands last
  assert.deepEqual(k.bys.map((by: any) => [by.n, by.received.n]), [[11, 0], [10, 1], [10, 0]], "premise: the three publishes, in the order they landed");
  assert.deepEqual(k.folds, { collapsed: ["api", "web", "tests"], expanded: [], pinned: [] }, "all three folds stand");
  assert.deepEqual(k.drops, ["browser 'eeeeeeee' n=10 received='1000.1':0, largest n=11 taken at 1"], "T's 10 dropped under the largest");
  yLink.flush();
  assert.deepEqual(yPane.as(() => readTabGroups(UNIONS).collapsed), ["api", "web", "tests"], "tab Y shows all three");
  assert.deepEqual(a.as(() => readTabGroups(UNIONS).collapsed), ["api", "web"],
    "tab T passes Y's frame over, since it names a number under T's own entry for 11, and shows tests only once a later frame is heard");
});

test("a largest taken before the record came back older holds nothing open: the older publish is judged against the take just above it", () => {
  // the browser's publish 45 was taken before it was killed with its storage unwritten, so its record comes back at 9, and
  // the phone's publish follows. In tab T, pane B folds docs as 10 on the connect push's count, taken under 45. Pane A then
  // folds api on a slow socket (11) and pane B folds web over it (12), which lands first. Tab Y, whose copy of the storage
  // has 10 but neither 11 nor 12, hears 12's count through a sibling pane and folds tests as its own 11, taken on that
  // count. Pane A's 11 lands last: not under the kept 11, and on a count past 45's, but under 12, taken at a count above
  // it, so it is dropped under 12 (a kernel judging on the kept and the largest alone took it, and went back to docs and api)
  const k = fakeKernel();
  const none = { collapsed: [], expanded: [], pinned: [] };
  k.folds = { ...none };
  const E = "e".repeat(32);
  k.post({ ...none }, { browser: E, n: 45 });                          // before the kill: taken, unchanged, so no push
  k.post({ ...none }, { browser: "c".repeat(32), n: 1 });               // the phone's publish
  const t = new Map<string, string>([[PUBLISHES, JSON.stringify({ browser: E, n: 9, live: {} })]]);
  const a = makeViewer("pane A", k, {}, t), b = makeViewer("pane B", k, {}, t);
  for (const v of [a, b]) k.connect(v);                                // the connect push names the phone's publish
  fold(b, "docs", true);                                               // T's 10
  const y = new Map(t);                                                // tab Y's copy of the storage, which has 10
  const ySibling = makeViewer("Y's sibling pane", k, {}, y), yPane = makeViewer("Y's pane", k, {}, y);
  for (const v of [ySibling, yPane]) k.connect(v);
  const out = slowSend(a);
  fold(a, "api", true);                                                // T's 11, held on the slow socket
  const yLink = slowLink(yPane);
  fold(b, "web", true);                                                // T's 12, taken first; Y's sibling hears it
  fold(yPane, "tests", true);                                          // Y's 11, on 12's count
  out.flush();                                                         // T's 11 lands last
  assert.deepEqual(k.bys.slice(2).map((by: any) => [by.n, by.received.n]), [[10, 2], [12, 3], [11, 4], [11, 3]],
    "premise: the four publishes, in the order they landed, with the counts they carry");
  assert.deepEqual(k.folds, { collapsed: ["docs", "api", "web", "tests"], expanded: [], pinned: [] }, "every fold stands");
  assert.deepEqual(k.drops, ["browser 'eeeeeeee' n=11 received='1000.1':3, between n=12 taken at 4"], "T's 11 dropped under 12, a pair at neither end");
  assert.deepEqual(k.seen.get(E)!.pairs, [[45, 1], [12, 4], [11, 5]], "45 first, then 12, then Y's 11");
  yLink.flush();
  assert.deepEqual(yPane.as(() => readTabGroups(UNIONS).collapsed), ["docs", "api", "web", "tests"], "tab Y shows every fold");
  assert.deepEqual(a.as(() => readTabGroups(UNIONS).collapsed), ["docs", "api", "web"],
    "tab T passes Y's frame over, since it names a number under T's own entry for 12, and shows tests only once a later frame is heard");
});

test("a declared redial gets no frame until a change is pushed: a fold made meanwhile waits, and lands over the next push", () => {
  // reconnect() above is a fresh dial, whose ready earns a connect push; a redial the shim declares sends no new ready, so
  // the page stays unheard (its publisher withdrawn at wsup) until the kernel pushes a change
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  k.drop(desktop);
  desktop.as(() => (globalThis as any).window.dispatchEvent(new Event("romp:wsdown")));
  k.declaredReconnect(desktop);
  fold(desktop, "api", true);
  assert.equal(k.posts.length, 0, "no frame heard on this socket yet: nothing published");
  fold(phone, "web", true);                                            // another device's change: the push the desktop hears next
  assert.deepEqual(k.folds, { collapsed: ["web", "api"], expanded: [], pinned: [] }, "the waiting fold landed over it");
  assert.deepEqual([shape(desktop, false), shape(phone, true)], [BOTH, BOTH]);
});

test("a restarted kernel: its new life's frames end every entry of the old one, so a publish lost with a page gone without pagehide holds nothing back", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared);
  for (const v of [clicked, sibling]) k.connect(v);
  k.drop(clicked);                                                     // its socket is gone, and the page has not noticed
  fold(clicked, "api", true);                                          // publish 1, queued by the shim
  loseSends(sibling);
  fold(sibling, "web", true);                                          // publish 2, lost; then the sibling's page goes, no pagehide
  k.viewers.splice(k.viewers.indexOf(sibling), 1);
  k.restart();
  k.reconnect(clicked);                                                // the shim flushes publish 1 into the new life
  assert.deepEqual(k.folds, { collapsed: ["api"], expanded: [], pinned: [] }, "premise: the new life took publish 1, and never saw 2");
  k.push();                                                            // a drag: the new life's frame names publish 1 again
  assert.deepEqual(shape(clicked, false), ["#api(folded)", "#web", WEB], "the kernel's folds: the old life's lost publish holds nothing back");
});

test("a publish lost with a page gone without pagehide is held for only until a later publish is taken and heard", () => {
  const k = fakeKernel();
  const shared = new Map<string, string>();
  const clicked = makeViewer("clicked pane", k, {}, shared), sibling = makeViewer("sibling pane", k, {}, shared), phone = makeViewer("phone", k);
  for (const v of [clicked, sibling, phone]) k.connect(v);
  const out = slowSend(clicked);
  fold(clicked, "api", true);                                          // publish 1, held on a slow socket
  loseSends(sibling);
  fold(sibling, "web", true);                                          // publish 2, lost; then the sibling's page goes, no pagehide
  k.drop(sibling);
  phone.as(() => writeTabGroups({ ...readTabGroups(UNIONS), pinned: [{ sid: TESTS, name: "api", id: "g1" }] }));   // a later publish, taken and heard
  out.flush();                                                         // publish 1 lands after it
  assert.deepEqual(k.folds, { collapsed: ["api"], expanded: [], pinned: [] }, "premise: the kernel's last word is publish 1");
  assert.deepEqual(shape(clicked, false), ["#api(folded)", "#web", WEB], "its answer is heard: the lost web fold holds it back no longer");
});

// ── the active session on the phone ───────────────────────────────────────────────────────────────
test("the phone folds the active session's group like the desktop does: the heading stands in for it, and it is marked", () => {
  const k = fakeKernel();
  const desktop = makeViewer("desktop", k), phone = makeViewer("phone", k);
  for (const v of [desktop, phone]) k.connect(v);
  fold(desktop, "api", true);
  const [dplan, pplan] = [false, true].map((ph) => (ph ? phone : desktop).as(() => planStrip([WEB, API, TESTS], UNIONS, readTabGroups(UNIONS), API, ph)));
  const head = (p: typeof dplan) => p.items.find((i) => "head" in i && i.head.name === "api") as any;
  assert.deepEqual([head(pplan).folded, head(pplan).active, head(pplan).hidden], [true, true, [API, TESTS]], "the phone: folded, its heading the active session's stand-in");
  assert.deepEqual([head(dplan).folded, head(dplan).active], [true, true], "…exactly as the desktop's header");
  assert.ok(pplan.folded.has(API), "the active session is folded away on both");
});
