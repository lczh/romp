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
 *  publish's name (2026-09-29): the name of the last publish it handled (written, unchanged or refused) rides the frame
 *  as `foldsBy`, an unnamed publish clears it, and a browser's publish older than one it already handled from that
 *  browser is dropped. */
function fakeKernel(opts: { folds?: boolean; stamps?: boolean } = {}) {
  const keepsFolds = opts.folds !== false;    // a kernel from before 2026-09-23 sends no folds half at all
  const keepsStamps = opts.stamps !== false;  // one from before 2026-09-29 keeps no name and serves none
  const k = {
    folds: null as Record<string, unknown> | null,
    by: null as { browser: string; n: number } | null,
    seen: new Map<string, number>(),
    refuse: false,
    warns: 0,
    viewers: [] as Viewer[],
    posts: [] as unknown[],
    bys: [] as unknown[],
    frame(): any {
      const fr: any = { type: "viewOrder", order: [], stored: false };
      if (keepsFolds) {
        fr.folds = k.folds === null ? null : JSON.parse(JSON.stringify(k.folds));
        if (keepsStamps && k.by) fr.foldsBy = { ...k.by };
      }
      return fr;
    },
    post(folds: Record<string, unknown>, by?: any) {
      k.posts.push(folds);
      k.bys.push(by);
      if (!keepsFolds) return;                                      // an older kernel answers unknownOp; nothing is kept
      const st = keepsStamps && by && typeof by.browser === "string" && by.browser.length >= 1 && by.browser.length <= 64
        && Number.isInteger(by.n) && by.n >= 1 ? { browser: by.browser as string, n: by.n as number } : null;
      if (st && st.n < (k.seen.get(st.browser) ?? 0)) return;      // this browser's older state, overtaken on another socket
      if (st) k.seen.set(st.browser, st.n);
      k.by = st;
      if (k.refuse) { k.warns++; return; }                          // refused: a warning to the poster, no push
      if (k.folds !== null && JSON.stringify(k.folds) === JSON.stringify(folds)) return;   // unchanged: no push
      k.folds = JSON.parse(JSON.stringify(folds));
      k.push();
    },
    /** the one viewOrder push to every connected viewer (a dropped socket hears nothing): a fold change's, or an
     *  arrangement change's, which carries the folds and their name as they stand */
    push() { for (const v of k.viewers) if (v.connected) v.deliver(k.frame()); },
    connect(v: Viewer) { v.connected = true; v.deliver(k.frame()); },
    /** the page's socket drops: the kernel no longer reaches it, and what it sends waits in the shim's queue
     *  (the page shim queues every send while its socket is down and flushes the queue when a new one opens) */
    drop(v: Viewer) { v.connected = false; },
    /** a new socket opens: the shim flushes what the page queued while down, the reconnect's `wsup` frame reaches the
     *  page, then (before the kernel's viewOrder frame) whatever the connect push's earlier frames make the page write
     *  (`beforeFrame`: the views frame's rename follow, say), then the viewOrder frame */
    reconnect(v: Viewer, beforeFrame?: () => void) {
      v.connected = true;
      for (const m of v.queued.splice(0)) k.post(m.folds, m.by);
      v.deliver({ type: "wsup" });
      if (beforeFrame) beforeFrame();
      v.deliver(k.frame());
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
    fm.outbound = (m: any) => { if (m && m.type === "setViewFolds") { if (v.connected) k.post(m.folds, m.by); else v.queued.push(m); } };
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
  assert.deepEqual([shape(phone, true), shape(strip, false)], [BOTH, BOTH]);
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
