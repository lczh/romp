// THE FOLDS ARE EVERY DEVICE'S (the user 2026-09-23, who wanted to collapse tag groups on the phone too, with the collapsed
// state synced to the desktop like the tab order): the tab-groups store in two halves. This browser's switches (whether the
// strip groups by tag, and the Sessions pane's own switch) stay in romp:tabgroups; the FOLDS (which groups are folded,
// which default-folded ones were opened, which members show through a fold, and the rename memory those pins rest on) are
// the kernel's, cached here under romp:tabgroups:shared and published when they change. Executed against the real module
// over a fake localStorage; the protocol with the kernel is federation-shared-folds.test.ts's. Synthetic ids only.
import { test } from "node:test";
import assert from "node:assert/strict";
import { adoptSharedFolds, foldsOf, foldsToPublish, hearSharedFolds, mergeFolds, parseTabGroups, phoneStandIns, planStrip, readTabGroups,
         setFoldsPublisher, setPinned, setSectionCollapsed, writeTabGroups, TABGROUPS_EVENT, TABGROUPS_KEY, TABGROUPS_PENDING_KEY, TABGROUPS_SHARED_KEY,
         type TabFolds, type TabGroupsState } from "./tab-groups";
import { viewTagUnion } from "./session-views";
import { routeOutbound } from "./federation";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

function withStore<T>(seed: Record<string, unknown>, fn: (store: Map<string, string>, events: string[]) => T): T {
  const g: any = globalThis;
  const store = new Map<string, string>();
  for (const [k, v] of Object.entries(seed)) store.set(k, typeof v === "string" ? v : JSON.stringify(v));
  const events: string[] = [];
  const win: any = new EventTarget();
  win.addEventListener(TABGROUPS_EVENT, () => events.push(TABGROUPS_EVENT));
  const saved = [g.localStorage, g.window];
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); }, removeItem: (k: string) => { store.delete(k); } };
  g.window = win;
  try { return fn(store, events); } finally { [g.localStorage, g.window] = saved; setFoldsPublisher(null); }
}
const json = (store: Map<string, string>, k: string) => JSON.parse(store.get(k)!);

test("a write splits the store: this browser's switches to romp:tabgroups, the folds to the kernel's cached copy", () => {
  withStore({}, (store) => {
    writeTabGroups({ on: false, collapsed: ["api"], expanded: ["archived"], pinned: [{ sid: "web", name: "api", id: "g1" }], followed: { g1: "api" }, timeline: true });
    assert.deepEqual(json(store, TABGROUPS_KEY), { on: false, timeline: true }, "the switches alone");
    assert.deepEqual(json(store, TABGROUPS_SHARED_KEY), { collapsed: ["api"], expanded: ["archived"], pinned: [{ sid: "web", name: "api", id: "g1" }], followed: { g1: "api" } },
      "the folds, in the canonical shape the wire carries");
    assert.deepEqual(readTabGroups(), { on: false, collapsed: ["api"], expanded: ["archived"], pinned: [{ sid: "web", name: "api", id: "g1" }], followed: { g1: "api" }, timeline: true },
      "read back whole");
  });
});

test("the pre-move fold fields are READ as the first paint and never written again: reverting hands them back", () => {
  const PRE = { on: true, collapsed: ["web"], expanded: [], pinned: [], timeline: true };
  withStore({ [TABGROUPS_KEY]: PRE }, (store) => {
    assert.deepEqual(readTabGroups().collapsed, ["web"], "no kernel copy yet: the folds this browser is used to");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", false));
    assert.deepEqual(readTabGroups().collapsed, [], "the write lands in the kernel's copy, which reads first from now on");
    assert.deepEqual(json(store, TABGROUPS_KEY), PRE, "the old key's folds are exactly as they were");
    writeTabGroups({ ...readTabGroups(), on: false });
    assert.deepEqual(json(store, TABGROUPS_KEY), { ...PRE, on: false }, "only its switch moves");
  });
});

test("a fold is PUBLISHED when the folds change, and only then; before the kernel's are heard it waits as a delta, landed over them", () => {
  withStore({}, (store) => {
    const sent: TabFolds[] = [];
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    assert.equal(sent.length, 0, "no publisher yet: a page never publishes its whole state before it has heard the kernel's");
    assert.deepEqual(json(store, TABGROUPS_PENDING_KEY), { collapsed: [], expanded: [], pinned: [] }, "the change's base is kept, origin-wide");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", false));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    assert.deepEqual(json(store, TABGROUPS_PENDING_KEY), { collapsed: [], expanded: [], pinned: [] }, "the FIRST unheard change's base stands for the ones after it");
    // the kernel's word: another device had folded web meanwhile. The change here (api folded) lands over it
    hearSharedFolds({ collapsed: ["web"], expanded: [], pinned: [] }, (f) => sent.push(f), setFoldsPublisher);
    assert.deepEqual(sent, [{ collapsed: ["web", "api"], expanded: [], pinned: [] }], "published: the kernel's folds with this browser's change over them");
    assert.equal(store.has(TABGROUPS_PENDING_KEY), false, "the delta is spent");
    assert.deepEqual(readTabGroups().collapsed, ["web", "api"]);
    writeTabGroups(setSectionCollapsed(readTabGroups(), "tests", true));
    assert.deepEqual(sent.at(-1), { collapsed: ["web", "api", "tests"], expanded: [], pinned: [] }, "heard: a change goes up whole, at once");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "tests", true));
    writeTabGroups({ ...readTabGroups(), on: false });
    assert.equal(sent.length, 2, "an unchanged rewrite and a switch flip publish nothing");
  });
});

test("the three-way merge: the kernel's folds with this browser's changes since the base, per section, pin and memory entry", () => {
  const base: TabFolds = { collapsed: ["api", "old"], expanded: [], pinned: [{ sid: "web", name: "api", id: "g1" }], followed: { g1: "api" } };
  const local: TabFolds = { collapsed: ["old", "tests"], expanded: ["archived"], pinned: [{ sid: "web", name: "ops", id: "g1" }], followed: { g1: "ops" } };
  const served: TabFolds = { collapsed: ["api", "old", "docs"], expanded: [], pinned: [{ sid: "web", name: "api", id: "g1" }, { sid: "api", name: "web" }], followed: { g1: "api", g2: "web" } };
  assert.deepEqual(mergeFolds(base, local, served), {
    collapsed: ["old", "docs", "tests"],   // api opened here: opened; docs folded there: stands; tests folded here: folded
    expanded: ["archived"],                // archived opened here
    pinned: [{ sid: "api", name: "web" }, { sid: "web", name: "ops", id: "g1" }],   // the pin carried here moves; the other device's pin stands
    followed: { g1: "ops", g2: "web" },    // the memory entry changed here takes this browser's; the other's stands
  });
  assert.deepEqual(mergeFolds(base, base, served), foldsOf({ on: true, ...served }), "no change here: the kernel's, exactly");
  assert.deepEqual(mergeFolds(base, local, mergeFolds(base, local, served)), mergeFolds(base, local, served), "a change the kernel already carries applies as a no-op");
});

test("the migration decision: the kernel's folds win; with none there, this browser's go up unless it has none", () => {
  const mine: TabGroupsState = { on: true, collapsed: ["web"], expanded: [], pinned: [] };
  assert.equal(foldsToPublish({ collapsed: [], expanded: [], pinned: [] }, mine), null, "the kernel keeps folds (even emptied ones): they win");
  assert.deepEqual(foldsToPublish(null, mine), foldsOf(mine), "the kernel keeps none: publish this browser's, so they survive the move");
  assert.equal(foldsToPublish(null, parseTabGroups(null)), null, "nothing folded, opened, pinned or remembered here: nothing to publish");
  assert.deepEqual(foldsToPublish(null, { ...parseTabGroups(null), expanded: ["archived"] }), { collapsed: [], expanded: ["archived"], pinned: [] }, "an opened default-folded group is a fold state too");
  assert.equal(foldsToPublish(undefined, mine), null, "no folds half at all (an older kernel): never published");
});

// ── this browser's own publishes coming back (2026-09-29) ─────────────────────────────────────────────
// The kernel answers every fold state that changes its store with a push to every viewer, the one that published it
// included, and names on that frame the last publish it handled (`foldsBy`). Two folds inside one round trip put the
// answer to the first on the wire while the page already shows the second.
/** The folds as the kernel hands them back: kernel.py stores json.dumps(folds, sort_keys=True) and serves that, so every
 *  object comes back with its keys sorted (a pin's `id` first), not in the order this page wrote them. */
const kernelCopy = (o: unknown): any => JSON.parse(JSON.stringify(o, (_k, v) => (v && typeof v === "object" && !Array.isArray(v)
  ? Object.fromEntries(Object.keys(v).sort().map((key) => [key, v[key]])) : v)));
const NO_FOLDS: TabFolds = { collapsed: [], expanded: [], pinned: [] };
const PUBLISHES = "romp:tabgroups:publishes";   // TABGROUPS_PUBLISHES_KEY
type Sent = { f: TabFolds; by: any };
/** A page's hearing, as the callers wire it: every publish the installed publisher makes is captured with its name. */
function hearing(sent: Sent[]) {
  const send = (f: TabFolds, by: unknown) => { sent.push({ f, by }); };
  return (served: unknown, by?: unknown, seq?: unknown) => hearSharedFolds(served, send, setFoldsPublisher, by, seq);
}
const record = (store: Map<string, string>) => JSON.parse(store.get(PUBLISHES) ?? '{"live":{}}');
const SEEN = "romp:tabgroups:folds-seen";       // TABGROUPS_FOLDS_SEEN_KEY: the counts received and heard, a key of their own
const counts = (store: Map<string, string>) => JSON.parse(store.get(SEEN) ?? "{}");

test("the kernel's answer to this page's EARLIER publish does not reopen a group folded since: two folds inside one round trip", () => {
  withStore({}, (_store, events) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));                                          // heard: this page speaks for the folds
    writeTabGroups(setPinned(setSectionCollapsed(readTabGroups(), "api", true), { name: "api", localId: "g1" }, "tests", true));   // 1. api folded, tests shown through it
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));   // 2. web folded before the kernel answered the first
    assert.deepEqual(sent.map((s) => s.f.collapsed), [["api"], ["api", "web"]], "premise: two publishes, each the whole fold state");
    const painted = events.length;
    hear(kernelCopy(sent[0].f), sent[0].by);                             // 3. the kernel's answer to the FIRST arrives now
    assert.deepEqual(readTabGroups().collapsed, ["api", "web"], "4. web stays folded: that answer is older than what this page published since");
    assert.equal(events.length, painted, "and nothing repaints for it");
    hear(kernelCopy(sent[1].f), sent[1].by);                             // the answer to the second
    assert.deepEqual([readTabGroups().collapsed, readTabGroups().pinned, events.length], [["api", "web"], [{ sid: "tests", name: "api", id: "g1" }], painted],
      "the answer to the latest publish is what the page already shows, its sorted keys and all");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), { browser: "f".repeat(32), n: 1 });   // later, another device opens api
    assert.deepEqual([readTabGroups().collapsed, events.length], [["web"], painted + 1], "another device's word is adopted as ever");
  });
});

test("a group folded and opened again inside one round trip never folds on an answer", () => {
  withStore({}, () => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", false));  // opened again: the same folds as the first publish
    assert.deepEqual(sent.map((s) => s.f.collapsed), [["api"], ["api", "web"], ["api"]], "premise: three publishes, the first and the last alike");
    const shown: string[][] = [];
    for (const s of sent) { hear(kernelCopy(s.f), s.by); shown.push(readTabGroups().collapsed); }
    assert.deepEqual(shown, [["api"], ["api"], ["api"]], "the second answer names a publish older than the third, so web never folds");
  });
});

test("a dropped connection ends this page's wait: the next connection's folds are the kernel's word", () => {
  withStore({}, () => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));   // neither answered when the connection drops
    setFoldsPublisher(null);                                             // the drop withdraws the publisher (render.ts pipeState; federation.ts unhearViewOrder)
    hear(kernelCopy(sent[0].f), sent[0].by);                             // the next connection's first frame: web's fold never landed
    assert.deepEqual(readTabGroups().collapsed, ["api"], "adopted: nothing sent on the connection that dropped will be answered on this one");
  });
});

test("only this browser's OWN earlier publishes are passed over: another device's folds are adopted while this page's publish goes unanswered", () => {
  withStore({}, () => {
    const hear = hearing([]);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));   // the kernel refuses it (a full disk, say): no answer ever comes
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), { browser: "f".repeat(32), n: 1 });   // another device folds web
    assert.deepEqual(readTabGroups().collapsed, ["web"], "adopted at once: a page that held every frame until its own answer came would be stranded by the refusal");
  });
});

test("the browser's id is minted once, kept only once a publish needs it, and its number rises across connections", () => {
  withStore({}, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    assert.equal(store.has(PUBLISHES), false, "a page that has published nothing leaves nothing behind");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const id = record(store).browser;
    assert.match(id, /^[0-9a-f]{32}$/, "16 random bytes, as hex");
    setFoldsPublisher(null);                                             // a drop, and the next connection's first frame
    hear(kernelCopy(sent[0].f), sent[0].by);
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    assert.deepEqual(sent.map((s) => s.by), [{ browser: id, n: 1 }, { browser: id, n: 2 }]);
  });
});

test("a record that came back older than the kernel's word: the next publish is numbered past the last of this browser's that a frame named", () => {
  // a browser killed before its storage was written: the kernel handled publish 45 from it and drops anything of this
  // browser's below that as an older state, while the record here says 1. Another device's number counts for nothing
  const E = "e".repeat(32);
  withStore({ [PUBLISHES]: { browser: E, n: 1, live: {} } }, () => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS), { browser: E, n: 45 });                  // the connect push names this browser's publish 45
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), { browser: "f".repeat(32), n: 99 });   // then another device folds docs
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    assert.deepEqual(sent.map((s) => s.by), [{ browser: E, n: 46 }, { browser: E, n: 47 }],
      "past the kernel's 45, so neither is dropped there; the other device's frame neither raised the number nor wiped it");
  });
});

test("a frame passed over is passed over WHOLE: no cache write, no repaint, the pending change kept, the publisher installed", () => {
  withStore({}, (store, events) => {
    const sent: Sent[] = [];
    const send = (f: TabFolds, by: unknown) => { sent.push({ f, by }); };
    const installed: unknown[] = [];
    const install = (fn: any) => { installed.push(fn); setFoldsPublisher(fn); };
    hearSharedFolds(kernelCopy(NO_FOLDS), send, install);
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    const base = JSON.stringify({ collapsed: ["api", "web"], expanded: [], pinned: [] });
    store.set(TABGROUPS_PENDING_KEY, base);                              // a change another pane of this browser made before hearing
    const copy = store.get(TABGROUPS_SHARED_KEY), painted = events.length, published = sent.length;
    hearSharedFolds(kernelCopy(sent[0].f), send, install, sent[0].by);
    assert.deepEqual([store.get(TABGROUPS_SHARED_KEY), events.length, sent.length], [copy, painted, published], "nothing cached, announced or published");
    assert.equal(store.get(TABGROUPS_PENDING_KEY), base, "the pending change waits for the kernel's current folds");
    assert.equal(installed.length, 2, "the publisher is installed as on any hearing");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "tests", true));
    assert.deepEqual(sent.at(-1)!.by.n, 3, "and it publishes on, numbered after the ones before");
    assert.equal(Object.keys(record(store).live).length, 1, "on the same connection");
  });
});

test("a frame naming this browser's publish at or above every one still out is heard, and the pending change lands over it", () => {
  withStore({}, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const base = JSON.stringify({ collapsed: ["api"], expanded: [], pinned: [] });
    store.set(TABGROUPS_PENDING_KEY, base);                              // another pane folded tests before hearing
    store.set(TABGROUPS_SHARED_KEY, JSON.stringify({ collapsed: ["api", "tests"], expanded: [], pinned: [] }));
    hear(kernelCopy(sent[0].f), sent[0].by);                             // the answer to the latest publish
    assert.equal(store.has(TABGROUPS_PENDING_KEY), false, "the change is spent");
    assert.deepEqual(sent.at(-1)!.f.collapsed, ["api", "tests"], "published over the kernel's folds");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), { browser: sent[0].by.browser, n: 99 });
    assert.deepEqual(readTabGroups().collapsed, ["docs"], "a number above every one out: heard");
  });
});

test("a stamp from another browser, none at all, or a malformed one is heard as before", () => {
  withStore({}, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    for (const g of ["api", "web", "tests"]) writeTabGroups(setSectionCollapsed(readTabGroups(), g, true));   // numbers 1 to 3, the last still out
    const id = record(store).browser;
    const stamps: unknown[] = [undefined, null, { browser: "f".repeat(32), n: 1 }, { browser: 7, n: 1 }, { browser: "", n: 1 }, { browser: "a".repeat(65), n: 1 },
      { browser: id, n: 0 }, { browser: id, n: -1 }, { browser: id, n: true }, { browser: id, n: 1.5 }, { browser: id, n: "2" }, { browser: id }, [id, 1], "x"];
    stamps.forEach((by, i) => {
      hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs" + i] }), by);
      assert.deepEqual(readTabGroups().collapsed, ["docs" + i], "heard: " + JSON.stringify(by));
    });
  });
});

test("a new connection's first hearing ends the entry of the one before it", () => {
  withStore({}, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const [before] = Object.keys(record(store).live);
    setFoldsPublisher(null);                                             // the drop
    hear(kernelCopy(sent[0].f));
    assert.deepEqual(record(store).live, {}, "its entry is gone");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    const after = Object.keys(record(store).live);
    assert.equal(after.length, 1);
    assert.notEqual(after[0], before, "and the new connection has a new id");
  });
});

test("pagehide ends the current connection's entry, and only its: a page that went away will never have its publish answered", () => {
  withStore({ [PUBLISHES]: { browser: "e".repeat(32), n: 1, live: { sibling: 1 } } }, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));   // numbers 2 and 3
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    (globalThis as any).window.dispatchEvent(new Event("pagehide"));
    assert.deepEqual(record(store).live, { sibling: { n: 1 } }, "a sibling pane's entry stands: its own answer may still be on its socket");
    hear(kernelCopy(sent[0].f), sent[0].by);
    assert.deepEqual(readTabGroups().collapsed, ["api"], "heard: nothing of this browser's still out is later than it");
  });
});

test("a send that throws sent nothing: the connection's entry goes back to what it was", () => {
  withStore({}, (store) => {
    let fail = true;
    const sent: Sent[] = [];
    hearSharedFolds(kernelCopy(NO_FOLDS), (f, by) => { if (fail) throw new Error("socket gone"); sent.push({ f, by }); }, setFoldsPublisher);
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));  // the first send throws (writeTabGroups keeps the fold here)
    assert.deepEqual(record(store).live, {}, "no entry for a publish that never left");
    fail = false;
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));
    const [conn] = Object.keys(record(store).live);
    assert.deepEqual([record(store).live[conn], sent[0].by.n], [{ n: 2 }, 2], "the number moved on all the same");
    fail = true;
    writeTabGroups(setSectionCollapsed(readTabGroups(), "tests", true));
    assert.deepEqual(record(store).live, { [conn]: { n: 2 } }, "back to the last publish that left");
  });
});

test("a browser that cannot keep the record publishes unnamed: the message is exactly the one before the name", () => {
  withStore({}, () => {
    const g: any = globalThis;
    const inner = g.localStorage;
    g.localStorage = { ...inner, setItem: (k: string, v: string) => { if (k === PUBLISHES) throw new Error("QuotaExceededError"); inner.setItem(k, v); } };
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    assert.deepEqual(sent, [{ f: { collapsed: ["api"], expanded: [], pinned: [] }, by: null }], "sent, without a name");
  });
});

test("the record keeps at most 32 connections, dropping the smallest number and never the publishing one's", () => {
  // kept largest first, so the order the record holds them in is not the order of their numbers: a cap that dropped
  // the entries it met first would drop the nine LARGEST
  const live: Record<string, number> = {};
  for (let i = 40; i >= 1; i--) live["c" + i] = i;
  withStore({ [PUBLISHES]: { browser: "e".repeat(32), n: 40, live } }, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const kept = record(store).live;
    const mine = Object.keys(kept).filter((c) => !(c in live));
    assert.deepEqual([Object.keys(kept).length, mine.length, kept[mine[0]], sent[0].by], [32, 1, { n: 41 }, { browser: "e".repeat(32), n: 41 }]);
    assert.equal(Math.min(...Object.keys(kept).filter((c) => c !== mine[0]).map((c) => kept[c].n)), 10, "the nine smallest went");
  });
});

// ── how far the kernel had got (2026-09-30) ──────────────────────────────────────────────────────────────
// A name says whose publish a frame reflects, not whether this browser's latest had landed when the frame was built: an
// arrangement push built just before a fold landed names another device's publish, or nothing. So the frame carries the
// kernel's count of taken fold publishes (`foldsSeq`), each publish carries the highest count this browser had received,
// and a frame no newer than that, while the publish still stands, is passed over; so is a frame older than one received.
const LIFE = "4242.1700000000";                 // a kernel life's id (the shape of kernel.py _BOOT_ID)
const at = (n: number, boot = LIFE) => ({ boot, n });
const OTHER = "f".repeat(32);                   // another device's browser id

test("a frame no newer than what this browser had received when it published is passed over while the publish stands, whoever it names", () => {
  withStore({}, (store, events) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), { browser: OTHER, n: 3 }, at(4));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const id = record(store).browser;                                   // minted, and kept by this first publish
    assert.deepEqual(sent.map((s) => s.by), [{ browser: id, n: 1, received: at(4) }], "the publish carries the count this browser had received");
    const painted = events.length;
    for (const by of [{ browser: OTHER, n: 3 }, undefined]) {
      // an arrangement push built before the fold was taken: another device's name, or none (a restarted kernel, an older page)
      hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), by, at(4));
      assert.deepEqual([readTabGroups().collapsed, events.length], [["docs", "api"], painted], "passed over, api stays folded: " + JSON.stringify(by));
    }
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs", "api"] }), { browser: id, n: 1 }, at(5));   // the publish's own answer
    assert.deepEqual([counts(store).received, counts(store).heard], [at(5), at(4)],
      "heard, and the received count raised; the heard count is not, since the frame names this browser's own publish");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), { browser: OTHER, n: 4 }, at(6));      // later, another device's change
    assert.deepEqual(readTabGroups().collapsed, ["web"], "a frame built after the publish landed is heard, whoever it names");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), { browser: OTHER, n: 3 }, at(4));      // a copy of the old push, very late
    assert.deepEqual(readTabGroups().collapsed, ["web"], "older than a frame received: passed over, nothing reverted");
  });
});

test("a hearing notes the frame's count in one write of the counts' own key, only when a count rises, null folds included, and never writes the record", () => {
  withStore({}, (store) => {
    const g: any = globalThis;
    const inner = g.localStorage;
    let writes = 0, recordWrites = 0;
    g.localStorage = { ...inner, setItem: (k: string, v: string) => { if (k === SEEN) writes++; if (k === PUBLISHES) recordWrites++; inner.setItem(k, v); } };
    const hear = hearing([]);
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), { browser: OTHER, n: 2 }, at(7));
    assert.deepEqual([writes, counts(store).received, counts(store).heard], [1, at(7), at(7)], "one write raises both");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), { browser: OTHER, n: 2 }, at(7));
    assert.equal(writes, 1, "the same count again raises nothing: no write");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), { browser: OTHER, n: 1 }, at(6));   // a sibling's slower socket: an older frame
    assert.deepEqual([readTabGroups().collapsed, writes], [["web"], 1], "passed over, and nothing written");
    hear(kernelCopy(NO_FOLDS), undefined, at(9, "4243.1700000001"));                          // a new kernel life's frame
    assert.deepEqual([writes, counts(store).received, counts(store).heard], [2, at(9, "4243.1700000001"), at(9, "4243.1700000001")],
      "another life's count replaces both, lower or not");
    assert.equal(recordWrites, 0, "no hearing wrote the record: a tab in another renderer process would write back a copy it has not updated yet");
    g.localStorage = inner;
    hear(null, undefined, at(10, "4243.1700000001"));                                          // a kernel whose folds are gone: null
    assert.deepEqual([counts(store).received, counts(store).heard], [at(10, "4243.1700000001"), at(10, "4243.1700000001")], "null folds count too");
  });
});

/** A tab in ANOTHER renderer process: `fn` runs in a document of its own over `copy`, that tab's view of the storage, which
 *  this tab's latest writes have not reached yet; each key it writes then reaches this tab, the last write winning, as the
 *  storage event delivers it. Returns the keys it wrote. */
function inAnotherProcess(store: Map<string, string>, copy: Map<string, string>, fn: () => void): string[] {
  const g: any = globalThis;
  const saved = [g.localStorage, g.window];
  const wrote = new Map<string, string | null>();
  g.localStorage = { getItem: (k: string) => copy.get(k) ?? null, setItem: (k: string, v: string) => { copy.set(k, v); wrote.set(k, v); },
                     removeItem: (k: string) => { copy.delete(k); wrote.set(k, null); } };
  g.window = new EventTarget();
  try { fn(); } finally { [g.localStorage, g.window] = saved; }
  for (const [k, v] of wrote) { if (v === null) store.delete(k); else store.set(k, v); }
  return [...wrote.keys()];
}

test("a tab in another renderer process hears this tab's earlier answer before this tab's later publish reaches it: this tab still passes that answer over", () => {
  // that tab judges the answer without the later publish, and hears it. Its hearing writes only the counts, so this tab's
  // entry and number stand; and a frame naming one of this browser's publishes never raises the heard count, so the
  // count that ends an entry is not raised past this tab's entry by the very frame the entry holds back
  withStore({}, (store, events) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS), { browser: OTHER, n: 1 }, at(3));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));   // publish 1
    const copy = new Map(store);                                         // the other tab's view: publish 1 has reached it
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));   // publish 2, which has not
    const answer = kernelCopy(sent[0].f), by1 = { browser: sent[0].by.browser, n: 1 };
    const wrote = inAnotherProcess(store, copy, () => hearSharedFolds(answer, () => {}, () => {}, by1, at(4)));   // the answer to 1 reaches that tab first
    assert.ok(!wrote.includes(TABGROUPS_SHARED_KEY), "premise: the answer changes nothing that tab holds, so it caches nothing: " + JSON.stringify(wrote));
    const rec = record(store);
    assert.deepEqual([rec.n, Object.values(rec.live)], [2, [{ n: 2, received: at(3) }]], "this tab's number and its entry for publish 2 stand");
    const painted = events.length;
    hear(answer, by1, at(4));                                            // then the same answer reaches this tab
    assert.deepEqual([readTabGroups().collapsed, events.length], [["api", "web"], painted], "passed over: web stays folded");
    assert.deepEqual([wrote, counts(store)], [[SEEN], { received: at(4), heard: at(3) }], "that tab wrote the counts alone, the heard one unraised");
  });
});

test("a frame passed over as older than one received raises no heard count, so a slower pane still passes over this browser's earlier answer", () => {
  // the kernel takes another device's publish (count 7), then this browser's publish 1 (8); publish 2 is still out. A pane
  // that connects now hears its connect push, 1's answer at 8, first and passes it over. A sibling on a slower socket then
  // hears the other device's 7, older than the 8 received (passed over), and then 1's answer at 8
  withStore({}, (store, events) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS), { browser: OTHER, n: 1 }, at(6));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));   // publish 1, having received 6
    writeTabGroups(setSectionCollapsed(readTabGroups(), "web", true));   // publish 2, before 1 is answered
    const answer = kernelCopy(sent[0].f), by1 = { browser: sent[0].by.browser, n: 1 };
    const g: any = globalThis, win = g.window;
    g.window = new EventTarget();                                        // the pane that connects now
    try { hearSharedFolds(answer, () => {}, () => {}, by1, at(8)); } finally { g.window = win; }
    assert.deepEqual(counts(store), { received: at(8), heard: at(6) }, "premise: passed over, the received count raised alone");
    const painted = events.length;
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), { browser: OTHER, n: 2 }, at(7));   // the slower socket: the other device's 7
    hear(answer, by1, at(8));                                            // then 1's answer
    assert.deepEqual([readTabGroups().collapsed, events.length], [["api", "web"], painted], "both passed over: web stays folded");
    assert.deepEqual(counts(store), { received: at(8), heard: at(6) }, "and no heard count was raised");
  });
});

test("an entry stops counting once a newer frame is heard, or once a frame of a new kernel life is received", () => {
  const E = "e".repeat(32);
  // a sibling pane's publish 5, made having received count 4, that never landed; its page went away without pagehide
  withStore({ [PUBLISHES]: { browser: E, n: 5, live: { gone: { n: 5, received: at(4) } } }, [SEEN]: { received: at(4), heard: at(4) } }, () => {
    const hear = hearing([]);
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["a"] }), { browser: E, n: 3 }, at(4));
    assert.deepEqual(readTabGroups().collapsed, [], "it stands: a frame at its count, naming this browser's publish 3, is passed over");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["b"] }), { browser: OTHER, n: 1 }, at(5));
    assert.deepEqual(readTabGroups().collapsed, ["b"], "a later publish, another device's, taken and heard");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["c"] }), { browser: E, n: 4 }, at(6));
    assert.deepEqual(readTabGroups().collapsed, ["c"], "and from then on the entry holds nothing of this browser's back");
  });
  withStore({ [PUBLISHES]: { browser: E, n: 5, live: { gone: { n: 5, received: at(4, "old") } } }, [SEEN]: { received: at(4, "old"), heard: at(4, "old") } }, () => {
    const hear = hearing([]);
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["a"] }), { browser: E, n: 3 }, at(4, "old"));
    assert.deepEqual(readTabGroups().collapsed, [], "in its own kernel life it stands");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["b"] }), undefined, at(0, "new"));          // the restarted kernel's first frame
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["c"] }), { browser: E, n: 3 }, at(1, "new"));
    assert.deepEqual(readTabGroups().collapsed, ["c"], "the first frame received from the new life ended it: this browser's publish 3, taken there, is heard");
  });
});

test("a frame of the previous kernel life delivered late is heard; until the new life's next frame, no publish made in the new life counts for the pass-over", () => {
  withStore({}, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), undefined, at(4, "new"));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));    // publish 1
    writeTabGroups(setSectionCollapsed(readTabGroups(), "docs", true));   // publish 2, both made having received new:4
    const id = record(store).browser;
    assert.deepEqual(sent.map((s) => s.by.received), [at(4, "new"), at(4, "new")], "premise: both carry new:4");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["old"] }), undefined, at(9, "old"));    // the old life's frame, very late
    assert.deepEqual([readTabGroups().collapsed, counts(store)], [["old"], { received: at(9, "old"), heard: at(9, "old") }],
      "heard, and both counts set back to the old life: another life's count says nothing about this one's");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web", "api"] }), { browser: id, n: 1 }, at(5, "new"));   // publish 1's own answer, publish 2 still out
    assert.deepEqual(readTabGroups().collapsed, ["web", "api"],
      "heard: publish 2's entry, made in the new life, no longer counts, so the older state shows until publish 2's answer lands");
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web", "api", "docs"] }), { browser: id, n: 2 }, at(6, "new"));
    assert.deepEqual(readTabGroups().collapsed, ["web", "api", "docs"], "publish 2's answer: the new life's word");
  });
  withStore({}, (store) => {
    const hear = hearing([]);
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["web"] }), undefined, at(4, "new"));
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["old"] }), undefined, at(9, "old"));
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["docs"] }), undefined, at(3, "new"));   // an older frame of the new life, later still
    assert.deepEqual([readTabGroups().collapsed, counts(store).received], [["docs"], at(3, "new")],
      "heard, and the new life's received count comes back lower than it was (4, now 3)");
  });
});

test("a record from before the count: a bare-number entry is an entry with no count, which stands as before, and is written back as one", () => {
  const E = "e".repeat(32);
  withStore({ [PUBLISHES]: { browser: E, n: 2, live: { sibling: 2 } } }, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS), { browser: OTHER, n: 1 }, at(3));
    hear(kernelCopy({ ...NO_FOLDS, collapsed: ["a"] }), { browser: E, n: 1 }, at(4));
    assert.deepEqual(readTabGroups().collapsed, [], "this browser's publish 1, while the sibling's 2 is out: passed over");
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const live = record(store).live;
    const [mine] = Object.keys(live).filter((c) => c !== "sibling");
    assert.deepEqual(live, { sibling: { n: 2 }, [mine]: { n: 3, received: at(4) } });
    assert.deepEqual(sent.map((s) => s.by), [{ browser: E, n: 3, received: at(4) }]);
  });
});

test("the record is written before the send: an answer heard inside the send finds the entry, and nothing puts back what its hearing wrote", () => {
  // the kernel's answer can arrive inside the send; on a first hearing it starts the connection afresh, retiring the entry
  // the publish was made on. A record written after the send would put the retired entry back, standing
  withStore({ [TABGROUPS_KEY]: { on: true, collapsed: ["web"], expanded: [], pinned: [] } }, (store) => {
    let answers = 0;
    const send = (f: TabFolds, by: any) => {
      if (answers++ > 0 || !by) return;
      hearSharedFolds(kernelCopy(f), send, setFoldsPublisher, { browser: by.browser, n: by.n }, at(1));   // answered at once
    };
    hearSharedFolds(null, send, setFoldsPublisher, undefined, at(0));   // a kernel with no folds: this browser's go up
    assert.deepEqual([answers, record(store).live, counts(store)], [1, {}, { received: at(1), heard: at(0) }],
      "the entry retired, and the answer, naming this browser's publish, raised the received count alone");
  });
});

test("a record whose shape is wrong reads as a fresh one: a new browser id, number 1, and none of its entries", () => {
  withStore({ [PUBLISHES]: { browser: 7, n: 9, live: { a: { n: 12, received: at(2) } } } }, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS), { browser: OTHER, n: 1 }, at(3));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const rec = record(store);
    assert.match(rec.browser, /^[0-9a-f]{32}$/);
    assert.deepEqual(sent.map((s) => s.by), [{ browser: rec.browser, n: 1, received: at(3) }]);
    assert.deepEqual(Object.values(rec.live), [{ n: 1, received: at(3) }], "the malformed record's entry went with it");
  });
});

test("a record's malformed entries are dropped one by one, and a malformed count in an entry drops the count alone", () => {
  const E = "e".repeat(32);
  const live = { good: { n: 9, received: at(3) }, old: 8, badCount: { n: 4, received: { boot: "", n: 1 } },
                 zero: 0, text: "7", frac: 1.5, none: null, list: [9], noN: { received: at(3) }, badN: { n: 0 } };
  withStore({ [PUBLISHES]: { browser: E, n: 9, live } }, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));                                          // a frame with no count: nothing noted
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const rec = record(store);
    const [mine] = Object.keys(rec.live).filter((c) => !(c in live));
    assert.deepEqual(rec.live, { good: { n: 9, received: at(3) }, old: { n: 8 }, badCount: { n: 4 }, [mine]: { n: 10 } });
    assert.deepEqual(sent.map((s) => s.by), [{ browser: E, n: 10 }], "no count noted: the publish says it received none");
  });
});

test("the counts' own key read malformed: a malformed count is dropped on its own, and a key that is not an object reads as none", () => {
  const E = "e".repeat(32);
  const cases: [unknown, unknown][] = [
    [{ received: at(3), heard: "x" }, at(3)],
    [{ received: { boot: "", n: 3 }, heard: at(3) }, undefined],
    [{ received: { boot: LIFE, n: -1 } }, undefined],
    [{ received: { boot: LIFE, n: 1.5 } }, undefined],
    ["{not json", undefined],
    [[at(3)], undefined],
  ];
  for (const [seed, received] of cases) {
    withStore({ [PUBLISHES]: { browser: E, n: 1, live: {} }, [SEEN]: seed }, (store) => {
      const sent: Sent[] = [];
      const hear = hearing(sent);
      hear(kernelCopy(NO_FOLDS));                                        // a frame with no count: nothing noted
      writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
      assert.deepEqual(sent.map((s) => s.by), [received ? { browser: E, n: 2, received } : { browser: E, n: 2 }], JSON.stringify(seed));
      assert.deepEqual(Object.values(record(store).live), [received ? { n: 2, received } : { n: 2 }], "and the entry alike: " + JSON.stringify(seed));
    });
  }
});

test("a named fold goes to the LOCAL kernel whole, its name with it", () => {
  const msg = { type: "setViewFolds", folds: { collapsed: ["api"], expanded: [], pinned: [] }, by: { browser: "e".repeat(32), n: 3 } };
  assert.deepEqual(routeOutbound(msg, new Set(["TESTHOST"])), [{ host: "", msg }]);
});

test("an adoption caches and announces only a CHANGE, and a malformed half is ignored", () => {
  withStore({}, (store, events) => {
    assert.equal(adoptSharedFolds({ collapsed: ["api"], expanded: [], pinned: [] }), true);
    assert.deepEqual([json(store, TABGROUPS_SHARED_KEY).collapsed, events.length], [["api"], 1], "cached, and the strip told to repaint");
    assert.equal(adoptSharedFolds({ collapsed: ["api"], expanded: [], pinned: [] }), false, "its own publish coming back");
    assert.equal(events.length, 1, "…announces nothing");
    for (const junk of [null, undefined, ["api"], "api", 7]) assert.equal(adoptSharedFolds(junk), false, "junk: " + JSON.stringify(junk));
    assert.equal(adoptSharedFolds({ collapsed: ["api", 3, null], pinned: "no", expanded: [] }), false, "parsed through the one parser: junk entries drop, and what is left is what was held");
  });
});

// ── the phone ─────────────────────────────────────────────────────────────────────────────────────
const VIEWS = { active: "all", tags: [{ id: "g1", name: "api", color: "#1EA1EB", members: ["api", "tests"] }, { id: "g2", name: "web", color: "#4EC9B0", members: ["web"] }] };
const U = viewTagUnion(VIEWS);

test("the phone paints the folded-away ACTIVE tab once, right after the heading that stands in for it, and nothing when it shows", () => {
  const folded = setSectionCollapsed(parseTabGroups(null), "api", true);
  const p = planStrip(["web", "api", "tests"], U, folded, "api", true);
  const paint = phoneStandIns(p.items, "api", p.folded);
  assert.deepEqual(paint.map((i) => ("head" in i ? "#" + i.head.name : ("away" in i ? "away:" : "") + i.id)), ["#api", "away:api", "#web", "web"],
    "the picker reads the away node for its current-session chip, and lists api's heading alone");
  assert.equal(phoneStandIns(p.items, "web", p.folded), p.items, "an active tab on the strip: the plan's items themselves");
  assert.equal(phoneStandIns(p.items, null, p.folded), p.items, "no active tab: nothing");
  const pinned = { ...folded, pinned: [{ sid: "api", name: "api", id: "g1" }] };
  const pp = planStrip(["web", "api", "tests"], U, pinned, "api", true);
  assert.equal(phoneStandIns(pp.items, "api", pp.folded), pp.items, "pinned through the fold: the tab itself shows, no stand-in node");
});

// ── the wiring: every kind of page hears the folds through the one implementation, and a fold goes to the LOCAL kernel ──
test("a fold goes to the LOCAL kernel whole, and every page hears the folds through hearSharedFolds", () => {
  // the folds name tags and host-prefixed pins as this viewer sees them: never split by host the way an order[] is
  assert.deepEqual(routeOutbound({ type: "setViewFolds", folds: { collapsed: ["api"], expanded: [], pinned: [{ sid: "TESTHOST:web", name: "api" }] } }, new Set(["TESTHOST"])),
    [{ host: "", msg: { type: "setViewFolds", folds: { collapsed: ["api"], expanded: [], pinned: [{ sid: "TESTHOST:web", name: "api" }] } } }]);
  // a page with a manager: the frame's folds half, only from the local kernel, and the window slot installed there
  // …each publish carrying its name when it has one, and the frame's name for the publish it reflects handed in (2026-09-29),
  // with the frame's count of taken publishes (2026-09-30)
  assert.match(FED, /if \("folds" in m\) \{\s*\n\s*hearSharedFolds\(m\.folds, \(f, by\) => this\.outbound\(\{ type: "setViewFolds", folds: f, \.\.\.\(by \? \{ by \} : \{\}\) \}\), \(fn\) => \{ w\.__rompPublishViewFolds = fn; \}, m\.foldsBy, m\.foldsSeq\);/);
  assert.match(FED, /if \(m && m\.type === "viewOrder"\) \{\s*\n\s*if \(host !== LOCAL\) return;\s*\n\s*const w = window as any;/, "…`w` the frame's window, shared with the arrangement half");
  assert.ok(FED.indexOf('if (m && m.type === "viewOrder") {') < FED.indexOf('if ("folds" in m) {') && /if \(m && m\.type === "viewOrder"\) \{\s*\n\s*if \(host !== LOCAL\) return;/.test(FED),
    "a remote kernel's folds are never adopted: they belong to whoever sits in front of that machine");
  assert.doesNotMatch(FED.slice(FED.indexOf("  start(): void {"), FED.indexOf("this.poll();", FED.indexOf("  start(): void {"))), /__rompPublishViewFolds =|__rompPublishViewOrder =/,
    "start() installs no publisher of either half: a page speaks for the folds and the order only once it has heard them");
  assert.match(FED, /w\.__rompWriteTabGroups = \(blob: unknown\) => writeTabGroups\(parseTabGroups\(JSON\.stringify\(blob \?\? \{\}\)\)\);/, "the raw timeline view's one write");
  // a VS Code chat webview: its own frame branch, publishing through its host pipe (executed below, lifted out of render.ts)
  assert.match(RENDER, /if \("folds" in m\) hearSharedFolds\(m\.folds, \(f, by\) => vscodeApi\?\.postMessage\(\{ type: "setViewFolds", folds: f, \.\.\.\(by \? \{ by \} : \{\}\) \}\), setFoldsPublisher, m\.foldsBy, m\.foldsSeq\);/);
});

// The VS Code chat webview's arm, EXECUTED (the pins above only read it): render.ts's chat frame handler lifted out
// verbatim, transpiled and run over stubs, as chat-foreign-frame.test.ts runs it. Every free identifier it reaches is an
// inert stub except these: paneArranges (the page has its manager, or not), hearSharedFolds and the host pipe
// (vscodeApi.postMessage), which record, hearSharedOrder (hears nothing), windowSender (this document's own dispatch), and
// setFoldsPublisher, a sentinel the arm must hand over as the publisher's installer.
const requireCjs = createRequire(path.join(process.cwd(), "package.json"));   // esbuild from the extension
const CHAT_HEAD = 'listenForFrames(perfFrameHandler("chat", (m) => vscodeApi?.postMessage(m), ';
function liftChatHandler(): string {
  const at0 = RENDER.indexOf(CHAT_HEAD);
  assert.ok(at0 > 0, "anchor not found: render.ts's chat frame listener moved; re-anchor");
  const start = at0 + CHAT_HEAD.length;
  const ends = ["\n}));\n", "\n}), vscodeApi);\n"].map((close) => RENDER.indexOf(close, start)).filter((i) => i > start);
  assert.ok(ends.length > 0, "the listener's closing not found");
  return RENDER.slice(start, Math.min(...ends) + 2);
}
const SET_FOLDS = function setFoldsPublisherStandIn(): void { /* the sentinel */ };
function chatArmOverStubs(arranges: boolean) {
  const heard: unknown[][] = [];
  const posted: unknown[] = [];
  const stub: any = new Proxy(function () { /* inert */ }, {
    get: (_t, k) => (k === Symbol.toPrimitive ? () => "" : stub),
    apply: () => undefined,
    set: () => true,
  });
  const named: Record<string, unknown> = {
    windowSender: () => "self",
    paneArranges: () => arranges,
    hearSharedFolds: (...a: unknown[]) => { heard.push(a); },
    hearSharedOrder: () => false,
    setFoldsPublisher: SET_FOLDS,
    vscodeApi: { postMessage: (m: unknown) => { posted.push(m); } },
    window: stub, document: stub, localStorage: stub, location: stub,   // the stub, even where an earlier test left a global
  };
  const scope = new Proxy(named, {
    has: (t, k) => typeof k === "string" && (k in t || !(k in globalThis)),   // real globals (JSON, Object, Array...) stay real
    get: (t, k) => (typeof k !== "string" ? undefined : (k in t ? t[k] : stub)),
    set: (t, k, v) => { if (typeof k === "string") t[k] = v; return true; },
  });
  const code = requireCjs("esbuild").transformSync("const __handler = " + liftChatHandler() + ";\n", { loader: "ts", target: "es2020" }).code;
  const make = new Function("__scope", "with (__scope) {\n" + code + "\nreturn __handler;\n}") as (sc: unknown) => (e: unknown) => void;
  const handler = make(scope);
  return { handle: (data: unknown) => handler({ data, source: null, origin: "" }), heard, posted };
}

test("the VS Code chat webview's arm, executed: it hands hearSharedFolds the frame's folds, name and count, and publishes through its host pipe", () => {
  const folds = { collapsed: ["api"], expanded: [], pinned: [] };
  const frame = { type: "viewOrder", folds, foldsBy: { browser: "e".repeat(32), n: 2 }, foldsSeq: { boot: "4242.1700000000", n: 5 } };
  const h = chatArmOverStubs(true);
  h.handle(frame);
  assert.equal(h.heard.length, 1, "heard once");
  const [served, sender, install, by, seq] = h.heard[0] as any[];
  assert.deepEqual([served, install === SET_FOLDS, by, seq], [folds, true, frame.foldsBy, frame.foldsSeq], "the folds, the installer, the name and the count");
  const named = { browser: "e".repeat(32), n: 3, received: { boot: "4242.1700000000", n: 5 } };
  sender({ collapsed: ["web"], expanded: [], pinned: [] }, named);
  sender({ collapsed: [], expanded: [], pinned: [] }, null);
  assert.deepEqual(h.posted, [{ type: "setViewFolds", folds: { collapsed: ["web"], expanded: [], pinned: [] }, by: named },
                              { type: "setViewFolds", folds: { collapsed: [], expanded: [], pinned: [] } }],
    "through the host pipe: named when the publish has a name, the message exactly as before when it has none");
  const missing = chatArmOverStubs(false);
  missing.handle(frame);
  assert.equal(missing.heard.length, 0, "a page whose manager is missing never hears the folds");
  const noHalf = chatArmOverStubs(true);
  noHalf.handle({ type: "viewOrder", order: [], stored: true });
  assert.equal(noHalf.heard.length, 0, "a frame with no folds half: nothing heard");
});

test("the folds' publisher is the CURRENT connection's: a drop withdraws it with the arrangement's, on every kind of page", () => {
  // the order half's per-connection gate (#2062): the viewOrder frame grants both publishers and a drop withdraws both, so a
  // page reconnecting after an outage cannot publish a stale fold copy before it hears the kernel's current one (executed
  // end to end in federation-shared-folds.test.ts: the reconnect and the offline fold)
  const unhear = FED.slice(FED.indexOf("private unhearViewOrder(): void {"), FED.indexOf("private lastFeedCounts"));
  assert.match(unhear, /delete \(window as any\)\.__rompPublishViewOrder;/);
  assert.match(unhear, /delete \(window as any\)\.__rompPublishViewFolds;/, "the socket's drop (romp:wsdown) and a new connection (wsup) withdraw the folds' too");
  assert.match(FED, /w\.addEventListener\("romp:wsdown", \(\) => this\.unhearViewOrder\(\)\);/);
  assert.match(FED, /if \(m && m\.type === "wsup" && host === LOCAL\) this\.unhearViewOrder\(\);/);
  // the VS Code chat webview's pipe going down withdraws both of its publishers
  assert.match(RENDER, /if \(m\.type === "pipeState" && !m\.up && paneArranges\(window as any\)\) \{ setViewOrderPublisher\(null\); setFoldsPublisher\(null\); \}/);
});
