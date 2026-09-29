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
  return (served: unknown, by?: unknown) => hearSharedFolds(served, send, setFoldsPublisher, by);
}
const record = (store: Map<string, string>) => JSON.parse(store.get(PUBLISHES) ?? '{"live":{}}');

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
    assert.deepEqual(record(store).live, { sibling: 1 }, "a sibling pane's entry stands: its own answer may still be on its socket");
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
    assert.deepEqual([record(store).live[conn], sent[0].by.n], [2, 2], "the number moved on all the same");
    fail = true;
    writeTabGroups(setSectionCollapsed(readTabGroups(), "tests", true));
    assert.deepEqual(record(store).live, { [conn]: 2 }, "back to the last publish that left");
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
  const live: Record<string, number> = {};
  for (let i = 1; i <= 40; i++) live["c" + i] = i;
  withStore({ [PUBLISHES]: { browser: "e".repeat(32), n: 40, live } }, (store) => {
    const sent: Sent[] = [];
    const hear = hearing(sent);
    hear(kernelCopy(NO_FOLDS));
    writeTabGroups(setSectionCollapsed(readTabGroups(), "api", true));
    const kept = record(store).live;
    const mine = Object.keys(kept).filter((c) => !(c in live));
    assert.deepEqual([Object.keys(kept).length, mine.length, kept[mine[0]], sent[0].by], [32, 1, 41, { browser: "e".repeat(32), n: 41 }]);
    assert.equal(Math.min(...Object.keys(kept).filter((c) => c !== mine[0]).map((c) => kept[c])), 10, "the nine smallest went");
  });
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
  // …each publish carrying its name when it has one, and the frame's name for the publish it reflects handed in (2026-09-29)
  assert.match(FED, /if \("folds" in m\) \{\s*\n\s*hearSharedFolds\(m\.folds, \(f, by\) => this\.outbound\(\{ type: "setViewFolds", folds: f, \.\.\.\(by \? \{ by \} : \{\}\) \}\), \(fn\) => \{ w\.__rompPublishViewFolds = fn; \}, m\.foldsBy\);/);
  assert.match(FED, /if \(m && m\.type === "viewOrder"\) \{\s*\n\s*if \(host !== LOCAL\) return;\s*\n\s*const w = window as any;/, "…`w` the frame's window, shared with the arrangement half");
  assert.ok(FED.indexOf('if (m && m.type === "viewOrder") {') < FED.indexOf('if ("folds" in m) {') && /if \(m && m\.type === "viewOrder"\) \{\s*\n\s*if \(host !== LOCAL\) return;/.test(FED),
    "a remote kernel's folds are never adopted: they belong to whoever sits in front of that machine");
  assert.doesNotMatch(FED.slice(FED.indexOf("  start(): void {"), FED.indexOf("this.poll();", FED.indexOf("  start(): void {"))), /__rompPublishViewFolds =|__rompPublishViewOrder =/,
    "start() installs no publisher of either half: a page speaks for the folds and the order only once it has heard them");
  assert.match(FED, /w\.__rompWriteTabGroups = \(blob: unknown\) => writeTabGroups\(parseTabGroups\(JSON\.stringify\(blob \?\? \{\}\)\)\);/, "the raw timeline view's one write");
  // a VS Code chat webview: its own frame branch, publishing through its host pipe
  assert.match(RENDER, /if \("folds" in m\) hearSharedFolds\(m\.folds, \(f, by\) => vscodeApi\?\.postMessage\(\{ type: "setViewFolds", folds: f, \.\.\.\(by \? \{ by \} : \{\}\) \}\), setFoldsPublisher, m\.foldsBy\);/);
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
