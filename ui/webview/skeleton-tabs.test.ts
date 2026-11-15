// Skeleton tabs — the client's copy of the set the kernel withholds after a redial (2026-09-07). The pure state
// machine in skeleton-tabs.ts is run here directly (the prebuild.ts / tab-meta.ts pattern); how render.ts wires
// it is pinned at the source in skeleton-tabs-wiring.test.ts. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { onHostSocketUp } from "./skeleton-tabs";
import { newSkeletonState, applyTabOrderSkeleton, onStatus, holdStatus, onFull, onDismiss, onSocketUp, nextPrefetch,
         renderKind, type SkeletonState } from "./skeleton-tabs";
import { onRest } from "./skeleton-tabs";

const A = "11111111-2222-3333-4444-aaaaaaaaaaaa";
const B = "11111111-2222-3333-4444-bbbbbbbbbbbb";
const C = "11111111-2222-3333-4444-cccccccccccc";
const D = "11111111-2222-3333-4444-dddddddddddd";
const all = () => true;
const none = new Set<string>();

const held = (st: SkeletonState) => [...st.ids].sort();

test("the spec's story: a reconnect strip lists [B, C]; a stale session never makes a skeleton read as loaded", () => {
  const st = newSkeletonState();
  assert.equal(applyTabOrderSkeleton(st, [B, C], [A, B, C]), true, "a set landed → changed");
  assert.deepEqual(st.order, [B, C], "the kernel's order is kept verbatim — it is the prefetch order");
  // the page HELD B before the outage (hasSession = true) — that stale entry must not paint it as loaded
  assert.equal(renderKind(st, B, true), "skeleton");
  assert.equal(renderKind(st, A, true), "loaded", "the active tab, sent in full, is a plain loaded tab");
  assert.equal(renderKind(st, D, false), "placeholder", "an unknown, session-less id is still the building placeholder");
  // status frames: stored for a skeleton (the chip reads ONLY these), passed through for anyone else
  assert.equal(onStatus(st, B, { state: "working", sinceEpoch: 1 }), "skeleton");
  assert.deepEqual(st.status.get(B), { state: "working", sinceEpoch: 1 });
  assert.equal(onStatus(st, A, { state: "idle" }), "not-skeleton");
  assert.equal(st.status.has(A), false, "a non-skeleton status is the caller's business");
  // the user clicks B → render.ts asks (pinned in the wiring test) and B sits in awaitingFull: the prefetch
  // holds while ANY skeleton is in flight, even one the user asked for (one at a time)
  assert.equal(nextPrefetch(st, B, new Set([B]), false, all), null, "one in flight → nothing else is fetched");
  // B's full lands
  assert.equal(onFull(st, B), true, "it WAS held → the caller re-shows the view instead of appending");
  assert.deepEqual(held(st), [C]);
  assert.equal(st.status.has(B), false, "its stored status goes with it");
  assert.deepEqual(st.order, [C]);
  assert.equal(onFull(st, A), false, "a full for a loaded tab is not a skeleton release");
  // a later strip WITHOUT the key (an older kernel; the set emptied so the key was dropped): keep, pruned
  assert.equal(applyTabOrderSkeleton(st, undefined, [A, C]), false, "C kept, nothing changed");
  assert.deepEqual(held(st), [C]);
  assert.equal(applyTabOrderSkeleton(st, undefined, [A]), true, "C left the strip → pruned");
  assert.deepEqual(held(st), []);
});

test("absent key = keep (pruned to the strip); an array = replace — the kernel's list is authoritative", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [B, C, D], [A, B, C, D]);
  assert.equal(applyTabOrderSkeleton(st, undefined, [A, C]), true);
  assert.deepEqual(held(st), [C], "B and D are no longer on the strip → pruned; C kept (absent = keep)");
  assert.deepEqual(st.order, [C]);
  // a second reconnect while C is outstanding: the new socket's strip names [A] — A had loaded, but it is
  // stale after the second outage, and C (not listed) is what the kernel now sends in full
  onSocketUp(st);
  assert.equal(applyTabOrderSkeleton(st, [A], [A, C]), true);
  assert.deepEqual(held(st), [A], "replaced, not merged");
  assert.deepEqual(st.order, [A]);
});

test("an array is cleaned: strings only, deduped; status entries for ids that left are dropped", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [B, C], [A, B, C]);
  onStatus(st, B, { state: "idle" });
  onStatus(st, C, { state: "idle" });
  assert.equal(applyTabOrderSkeleton(st, [C, 7, null, C, ""], [A, B, C]), true);
  assert.deepEqual(held(st), [C]);
  assert.deepEqual(st.order, [C]);
  assert.equal(st.status.has(B), false, "B left the set → its status is gone");
  assert.equal(st.status.has(C), true);
  assert.equal(applyTabOrderSkeleton(st, [C], [A, B, C]), false, "the same list again → unchanged (no repaint)");
  assert.equal(applyTabOrderSkeleton(st, [], [A, B, C]), true, "an empty array empties the set");
  assert.deepEqual(held(st), []);
});

test("a list that names a tab whose full already arrived on THIS socket is a stale relay, never re-adopted", () => {
  // The kernel releases a sid as it sends its full and enqueues both under one lock, so a strip naming X can only
  // be ahead of X's full — never behind it. The federation merge re-attaches its per-host copy of the list to
  // every merged strip and is never told the last id loaded (the kernel's key just disappears): without this
  // rule the last skeleton re-skeletoned on every push, asked, loaded, and re-skeletoned again — a flap.
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [C], [A, C]);
  assert.equal(onFull(st, C), true);
  assert.equal(applyTabOrderSkeleton(st, [C], [A, C]), false, "the stale relay of [C] is ignored");
  assert.deepEqual(held(st), []);
  // a NEW socket is new information about every tab: the same list is adopted after the socket reopens
  onSocketUp(st);
  assert.equal(applyTabOrderSkeleton(st, [C], [A, C]), true);
  assert.deepEqual(held(st), [C]);
});

test("nextPrefetch: null when hidden, null while one is in flight, else the first held id in order that is in view and not active", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [C, B, D], [A, B, C, D]);   // the kernel's order: ascending transcript size
  assert.equal(nextPrefetch(st, A, none, true, all), null, "hidden → nothing (bytes nobody sees)");
  assert.equal(nextPrefetch(st, A, new Set([D]), false, all), null, "a skeleton in flight → wait for its upsert");
  assert.equal(nextPrefetch(st, A, new Set([A]), false, all), C, "a non-skeleton in flight does not hold the chain");
  assert.equal(nextPrefetch(st, A, none, false, all), C, "the first of the kernel's order");
  assert.equal(nextPrefetch(st, C, none, false, all), B, "…skipping the active tab (its own click path asks)");
  assert.equal(nextPrefetch(st, A, none, false, (id) => id !== C), B, "…and a view-hidden tab (it loads on click)");
  onFull(st, C); onFull(st, B);
  assert.equal(nextPrefetch(st, A, none, false, all), D, "the chain walks the list one upsert at a time");
  onFull(st, D);
  assert.equal(nextPrefetch(st, A, none, false, all), null, "nothing left");
  assert.equal(nextPrefetch(newSkeletonState(), A, none, false, all), null, "a fresh page has no set");
});

test("nextPrefetch: a tab whose relay is closed is neither fetched nor held in flight, so the other machines' tabs keep filling (2026-10-11)", () => {
  // two linked machines' skeletons interleaved in the kernel-merged order; TESTHOST's relay drops while its tab B is in flight
  const RB = "TESTHOST:" + B, RC = "OTHERHOST:" + C, RD = "TESTHOST:" + D;
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [RB, RC, RD], [A, RB, RC, RD]);
  const up = () => true;
  const testhostDown = (id: string) => !id.startsWith("TESTHOST:");
  assert.equal(nextPrefetch(st, A, new Set([RB]), false, all, up), null, "premise: one in flight holds the chain while its relay is open");
  assert.equal(nextPrefetch(st, A, new Set([RB]), false, all, testhostDown), RC,
    "its relay closed: the ask nobody will answer no longer holds the chain, and the other machine's tab is next");
  assert.equal(nextPrefetch(st, A, none, false, all, testhostDown), RC, "the closed relay's tabs are skipped, not asked");
  onFull(st, RC);
  assert.equal(nextPrefetch(st, A, none, false, all, testhostDown), null, "only the closed relay's tabs left: nothing to ask yet");
  assert.equal(nextPrefetch(st, A, none, false, all, up), RB, "the relay reopens: its tabs resume in order");
  assert.equal(nextPrefetch(st, A, none, false, all), RB, "no predicate: every id reachable, as before");
});

test("holdStatus: a status ahead of its strip is kept for the array that lists the id and dropped by one that does not", () => {
  // The pane shim's FIFO delivers a newer tabOrder at the END of a burst, so a skeleton's status frames can land before
  // the strip that names the set (a later chat column's open sends two strips, 2026-09-11). The status waits for the
  // strip; an ask in its place loaded the whole board behind a column opened as a view of one session.
  const st = newSkeletonState();
  assert.equal(onStatus(st, C, { state: "working" }), "not-skeleton", "no set yet: the caller's fallthrough, which holds");
  holdStatus(st, C, { state: "working" }); holdStatus(st, D, { state: "idle" });
  assert.deepEqual(held(st), [], "a held status makes no skeleton");
  assert.equal(renderKind(st, C, false), "placeholder", "…and draws nothing as one");
  assert.equal(nextPrefetch(st, A, none, false, all), null, "…and the idle walk has nothing to fetch");
  applyTabOrderSkeleton(st, [C], [A, C, D]);
  assert.deepEqual(held(st), [C]);
  assert.deepEqual(st.status.get(C), { state: "working" }, "the held status is the chip's the moment the strip lists the id");
  assert.equal(st.status.has(D), false, "a status for an id the array does not list is dropped with the strip");
  onFull(st, C);
  assert.equal(st.status.has(C), false, "the full clears it as any skeleton's");
});

test("onDismiss: a tab that left the strip has nothing left to load", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [C], [A, C]);
  onStatus(st, C, { state: "working" });
  onDismiss(st, C);
  assert.deepEqual(held(st), []);
  assert.deepEqual(st.order, []);
  assert.equal(st.status.size, 0);
  onDismiss(st, C);   // idempotent
  assert.deepEqual(held(st), []);
  assert.equal(nextPrefetch(st, A, none, false, all), null);
});

test("renderKind with a stale session present: skeleton wins; without the set the old two states are unchanged", () => {
  const st = newSkeletonState();
  assert.equal(renderKind(st, B, true), "loaded");
  assert.equal(renderKind(st, B, false), "placeholder");
  applyTabOrderSkeleton(st, [B], [A, B]);
  assert.equal(renderKind(st, B, true), "skeleton", "the stale pre-outage entry is kept for the append, never painted");
  assert.equal(renderKind(st, B, false), "skeleton", "a page that opened a socket and received nothing: still a skeleton, loads on click/idle");
});

test("an array never adopts an id the strip does not list: a session that ended before it loaded is gone", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, ["B", "Z"], ["A", "B"]);
  assert.deepEqual(st.order, ["B"], "Z is not on the strip → not a skeleton either (the prefetch must never ask for it)");
  assert.equal(st.ids.has("Z"), false);
});

test("a dismissed tab leaves the loaded set, so its re-listing is a skeleton again and the pane asks for its frame (T357)", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, ["A"], ["A", "B"]);
  onFull(st, "A");                                    // its frame landed on this socket
  assert.ok(st.loaded.has("A")); assert.ok(!st.ids.has("A"));
  applyTabOrderSkeleton(st, ["A"], ["A", "B"]);       // re-listed as a skeleton while loaded: refused, the page has it
  assert.ok(!st.ids.has("A"), "a loaded id is never re-skeletoned on the same socket");
  onDismiss(st, "A");                                 // its tab left the strip (a host drop, an omission): the view went with it
  assert.ok(!st.loaded.has("A"), "no longer loaded here");
  applyTabOrderSkeleton(st, ["A"], ["A", "B"]);       // the host re-attached: the strip names it a skeleton again
  assert.ok(st.ids.has("A"), "…and now it is one, so the pane asks for its frame");
});

test("a relay's reopen forgets what loaded for that host on the dead relay socket: a restarted remote kernel's skeleton list is adopted, not refused (2026-09-23)", () => {
  // The audit's lead: `loaded` cleared only on the LOCAL socket's flip, so after a REMOTE kernel restart the relay's redial got a
  // skeleton list naming tabs this page had loaded before, refused it as a stale relay, took status frames alone for them and
  // froze their transcripts until clicked. The per-host twin clears that host's ids alone.
  const st = newSkeletonState();
  const R1 = "TESTHOST:11111111-2222-4333-8444-555555550101", R2 = "TESTHOST:11111111-2222-4333-8444-555555550102";
  const L1 = "11111111-2222-4333-8444-555555550103";
  onFull(st, R1); onFull(st, R2); onFull(st, L1);                      // all three loaded on the sockets of before
  applyTabOrderSkeleton(st, [R1, R2], [L1, R1, R2]);
  assert.deepEqual([...st.ids], [], "before the fix's event: the loaded ids are refused, the remote's tabs read as loaded");
  onHostSocketUp(st, (id) => id.startsWith("TESTHOST:"));
  assert.ok(!st.loaded.has(R1) && !st.loaded.has(R2), "that host's loaded record is gone");
  assert.ok(st.loaded.has(L1), "the local tab's stays: its socket did not move");
  applyTabOrderSkeleton(st, [R1, R2], [L1, R1, R2]);
  assert.deepEqual([...st.ids].sort(), [R1, R2].sort(), "the restarted kernel's list is adopted: the tabs are skeletons, so the page asks for their frames");
});

// ── a tab at REST (2026-11-15): the kernel takes back a tab this page had loaded, because its transcript moved while
// no connected page was watching it (kernel.py _rest_unwatched; tests/test_unwatched_tabs.py drives the kernel half) ──

test("a rest frame takes a loaded tab back: a skeleton again, drawn from the kernel's status, though its full arrived on this socket", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [B], [A, B, C]);
  onFull(st, B);                                    // the page loaded B (its click, the prefetch)
  assert.equal(renderKind(st, B, true), "loaded");
  assert.equal(onRest(st, B, { state: "working", provisional: true }), true, "it was not a skeleton: the caller re-shows it when it is on screen");
  assert.equal(renderKind(st, B, true), "skeleton", "the copy the page holds is behind: never painted as current");
  assert.deepEqual(st.status.get(B), { state: "working", provisional: true }, "the chip reads the frame's status");
  assert.equal(st.rested.has(B), true);
  assert.equal(st.loaded.has(B), false, "its loaded record goes, so the kernel's own re-listing is taken");
  // the kernel's next strip lists it, and the statuses that follow are stored like any skeleton's
  assert.equal(applyTabOrderSkeleton(st, [B], [A, B, C]), false, "already held: nothing changes");
  assert.equal(onStatus(st, B, { state: "ready", provisional: true }), "skeleton");
  assert.deepEqual(st.status.get(B), { state: "ready", provisional: true });
  // a tab that was never loaded here can be taken back too (the page holds nothing of it): the same skeleton
  assert.equal(onRest(st, C, { state: "working" }), true);
  assert.equal(renderKind(st, C, false), "skeleton");
  assert.equal(onRest(st, C, { state: "ready" }), false, "a second frame for a tab already at rest only refreshes its status");
  assert.deepEqual(st.status.get(C), { state: "ready" });
});

test("a tab at rest stays a skeleton until its full: a strip whose list lacks it, or whose order drops it for a moment, releases nothing", () => {
  const st = newSkeletonState();
  onFull(st, B); onFull(st, C);
  onRest(st, B, { state: "working" });
  // a merged strip re-presenting a host's list copied BEFORE the rest (the federated merge keeps a copy per host), an
  // empty list, and an older kernel's keyless strip: none is the kernel releasing the tab, which it does only by a full
  for (const list of [[], [C], undefined] as unknown[]) {
    applyTabOrderSkeleton(st, list, [A, B, C]);
    assert.equal(st.ids.has(B), true, "still a skeleton after " + JSON.stringify(list));
    assert.equal(renderKind(st, B, true), "skeleton");
  }
  assert.equal(st.ids.has(C), false, "…while a list naming a tab loaded on this socket is still refused as a stale relay");
  applyTabOrderSkeleton(st, [], [A, C]);            // the order omits B for one frame (a transient read failure): the pane keeps the tab
  assert.equal(renderKind(st, B, true), "skeleton", "and it must not turn back into a loaded tab over a copy that is behind");
  assert.equal(onFull(st, B), true, "the full is the release: it was held");
  assert.equal(st.rested.has(B), false);
  assert.equal(renderKind(st, B, true), "loaded");
  applyTabOrderSkeleton(st, [], [A, B, C]);
  assert.equal(st.ids.has(B), false, "nothing brings it back but a new rest frame");
  // a tab torn down while at rest leaves no record behind
  onRest(st, C, { state: "working" });
  onDismiss(st, C);
  assert.deepEqual([st.ids.has(C), st.rested.has(C), st.status.has(C)], [false, false, false]);
  applyTabOrderSkeleton(st, [], [A, B]);
  assert.equal(st.ids.has(C), false);
});

test("the idle prefetch never fetches a tab at rest: it fills a tab this page never held, once, and a tab that went behind waits for the click", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [B, C, D], [A, B, C, D]);
  // the chain fills B, C and D, one per idle, each once
  const fetched: string[] = [];
  for (let i = 0; i < 10; i++) {
    const next = nextPrefetch(st, A, none, false, all);
    if (!next) break;
    fetched.push(next);
    onFull(st, next);
  }
  assert.deepEqual(fetched, [B, C, D], "one build per tab the page had never held");
  // every one of them is working: each goes behind the moment it lands, and the kernel takes it back
  for (let round = 0; round < 5; round++) {
    for (const id of [B, C, D]) onRest(st, id, { state: "working" });
    applyTabOrderSkeleton(st, [B, C, D], [A, B, C, D]);
    assert.equal(nextPrefetch(st, A, none, false, all), null, "round " + round + ": nothing is fetched again in idle");
  }
  assert.deepEqual(held(st), [B, C, D]);
  // a new socket changes nothing for them (their copies are still behind), while a tab never loaded is fetched as before
  onSocketUp(st);
  applyTabOrderSkeleton(st, [B, C, D], [A, B, C, D]);
  assert.equal(nextPrefetch(st, A, none, false, all), null);
  st.rested.delete(D);                              // D as a plain skeleton: withheld, never loaded here
  assert.equal(nextPrefetch(st, A, none, false, all), D);
  // the click is not the prefetch: a tab at rest on screen is asked for by showActive, and its ask in flight holds the chain
  assert.equal(nextPrefetch(st, B, new Set([B]), false, all), null, "one in flight at a time, the click's included");
});
