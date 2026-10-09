// A chat send is the page's until a kernel answers it (send-acks.ts, 2026-10-09). Two messages typed into a linked
// machine's session were lost without a trace: their socket closed before the kernel read them, and the reload that
// followed dropped the only copy of their words. Executed here: the pure decisions (what is kept across a reload,
// what is sent again and when, what an answer does), the federation layer's two events (a socket whose kernel says it
// recognises a resend; a socket that went down) against the real manager with a fake WebSocket, and the chat's
// wiring. Synthetic only (host TESTHOST, placeholder ids, invented text).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { newPending, type PendingSend } from "./send-pending";
import { applySendAck, defaultWire, keptSends, markHostLost, mergeKept, pendingFromKept, resendable, resendWire, restoreKept, unanswered,
         KEPT_MAX, KEPT_MAX_AGE_MS, RESEND_MAX_AGE_MS } from "./send-acks";
import { dropPending } from "./send-pending";
import { FederationManager } from "./federation";
import { hostOf } from "./host-prefix";

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const LOCAL = "11111111-2222-3333-4444-5555555500c1";
const REMOTE = "TESTHOST:11111111-2222-3333-4444-5555555500c2";
const qid = (c: string) => "echo:" + c.repeat(32);
const NOW = 1_790_000_000_000;                  // the test's clock: every send below was pressed a minute before it

function sent(sid: string, text: string, q: string): PendingSend {
  const p = newPending(text, undefined, NOW - 60_000, q);
  p.wire = defaultWire(sid, p);
  return p;
}

test("a plain send's wire is exactly what the composer posted, and a resend posts it again marked as one", () => {
  const p = newPending("add a test for the search cache", undefined, 1000, qid("a"), ["/tmp/lab/report.pdf"]);
  assert.deepEqual(defaultWire(LOCAL, p), { type: "sendMessage", id: LOCAL, text: "add a test for the search cache", qid: qid("a"), paths: ["/tmp/lab/report.pdf"] });
  p.wire = defaultWire(LOCAL, p);
  assert.deepEqual(resendWire(p), { ...p.wire, resend: true });
  // the composer's own post, for the default to match (render.ts routeUserMessage): id, text, qid, then the attachments
  assert.match(RENDER, /const att = paths && paths\.length \? \{ paths \} : \{\};/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "sendMessage", id: sid, text, qid, \.\.\.att \}\)/);
});

test("only an unanswered send is kept across a reload, and a kept send comes back saying not confirmed", () => {
  const a = sent(LOCAL, "rebuild the index", qid("a"));
  const b = sent(LOCAL, "and the docs", qid("b")); b.acked = true; b.received = true;
  const c = sent(REMOTE, "check the api tests", qid("c")); c.received = true;     // a frame showed the kernel's copy
  const kept = keptSends(new Map([[LOCAL, [a, b]], [REMOTE, [c]]]), NOW);
  assert.deepEqual(kept.map((k) => k.qid), [qid("a")], "the answered and the seen stay with their kernel");
  const back = restoreKept(JSON.parse(JSON.stringify(kept)), NOW);
  assert.equal(back.length, 1);
  const p = pendingFromKept(back[0]);
  assert.equal(p.text, "rebuild the index");
  assert.equal(p.qid, qid("a"));
  assert.equal(p.lost, "reload", "the bubble says not confirmed until a kernel answers");
  assert.equal(p.late, true, "the fresh page may hold no frame to anchor it to");
  assert.ok(unanswered(p), "and it is still owed a resend");
});

test("a kept row that is not one is skipped, never trusted", () => {
  const good = { sid: LOCAL, text: "x", qid: qid("d"), ts: NOW - 1000, wire: { type: "sendMessage", id: LOCAL, text: "x", qid: qid("d") } };
  const rows = [
    good,
    { ...good, qid: "s-1", wire: { ...good.wire, qid: "s-1" } },                      // not the press's id form
    { ...good, qid: qid("e") },                                                       // the wire names another send
    { ...good, wire: { ...good.wire, type: "renameSession" } },                       // not a send
    { ...good },                                                                      // the same send twice
    null, 7, "row",
  ];
  assert.deepEqual(restoreKept(rows, NOW).map((k) => k.qid), [qid("d")]);
  assert.deepEqual(restoreKept({ not: "a list" }, NOW), []);
});

test("a socket whose kernel recognises a resend gets every unanswered send to its host once, never another host's", () => {
  const l = sent(LOCAL, "local one", qid("1"));
  const r1 = sent(REMOTE, "remote one", qid("2"));
  const r2 = sent(REMOTE, "remote two", qid("3")); r2.acked = true; r2.received = true;
  const m = new Map([[LOCAL, [l]], [REMOTE, [r1, r2]]]);
  const on = new WeakMap<PendingSend, number>();
  const first = resendable(m, "TESTHOST", hostOf, on, 1, NOW);
  assert.deepEqual(first.map((x) => x.p.qid), [qid("2")], "the unanswered remote send alone");
  for (const x of first) on.set(x.p, 1);
  assert.deepEqual(resendable(m, "TESTHOST", hostOf, on, 1, NOW), [], "once per socket");
  assert.deepEqual(resendable(m, "TESTHOST", hostOf, on, 2, NOW).map((x) => x.p.qid), [qid("2")], "…and again on the next socket");
  assert.deepEqual(resendable(m, "", hostOf, on, 1, NOW).map((x) => x.p.qid), [qid("1")], "the page's own kernel: its own sends");
});

test("an answer ends the owing: ok clears not confirmed, a refusal keeps it and is never resent", () => {
  const ok = sent(LOCAL, "ship it", qid("4")); ok.lost = "connection";
  const no = sent(LOCAL, "and this", qid("5"));
  const list = [ok, no];
  assert.equal(applySendAck(list, qid("4"), true), ok);
  assert.equal(ok.received, true);
  assert.equal(ok.lost, undefined, "the kernel holds it: the label goes");
  assert.equal(applySendAck(list, qid("5"), false), no);
  assert.equal(no.lost, "refused");
  assert.ok(!unanswered(ok) && !unanswered(no), "neither is sent again");
  assert.equal(applySendAck(list, qid("6"), true), null, "an answer for a send a push already retired changes nothing");
});

test("a host's socket going down marks its unanswered sends not confirmed, and only that host's", () => {
  const l = sent(LOCAL, "local", qid("7"));
  const r = sent(REMOTE, "remote", qid("8"));
  const done = sent(REMOTE, "answered", qid("9")); done.acked = true; done.received = true;
  const m = new Map([[LOCAL, [l]], [REMOTE, [r, done]]]);
  assert.deepEqual(markHostLost(m, "TESTHOST", hostOf, "connection"), [REMOTE]);
  assert.equal(r.lost, "connection");
  assert.equal(done.lost, undefined, "an answered send cannot have been lost by the drop");
  assert.equal(l.lost, undefined, "another host's sends are untouched");
});

// ── the federation layer's events, against the real manager ──────────────────────────────────────
class FakeWS {
  static made: FakeWS[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: any) => void) | null = null;
  onclose: ((ev: any) => void) | null = null;
  onerror: (() => void) | null = null;
  sentFrames: any[] = [];
  constructor(public url: string) { FakeWS.made.push(this); }
  send(s: string): void { this.sentFrames.push(JSON.parse(s)); }
  open(): void { this.readyState = 1; this.onopen && this.onopen(); }
  frame(data: any): void { this.onmessage && this.onmessage({ data: JSON.stringify(data) }); }
  close(): void { this.readyState = 3; }
  die(code = 1006): void { this.readyState = 3; this.onclose && this.onclose({ code, wasClean: false }); }
}

async function withManager(fn: (fm: any, events: { type: string; host?: string }[]) => void): Promise<void> {
  const events: { type: string; host?: string }[] = [];
  const g: any = globalThis;
  const saved: Record<string, any> = {};
  const set = (k: string, v: any) => { saved[k] = { had: k in g, v: g[k] }; g[k] = v; };
  FakeWS.made = [];
  set("WebSocket", FakeWS);
  set("location", { protocol: "http:", host: "TESTHOST.local:1" });
  set("localStorage", { getItem: () => null, setItem: () => {} });
  set("window", {
    dispatchEvent: (ev: any) => { if (ev && typeof ev.type === "string" && ev.type.startsWith("romp:")) events.push({ type: ev.type, host: ev.detail && ev.detail.host }); },
    __rompLocalSend: () => {},
    sessionStorage: { getItem: () => "" },
    parent: { postMessage: () => {} },
  });
  const realTimeout = g.setTimeout;
  g.setTimeout = () => 0;              // the manager's 2 s redial after a close is driven by hand below
  try {
    fn(new FederationManager(), events);
  } finally {
    g.setTimeout = realTimeout;
    for (const [k, r] of Object.entries(saved)) { if (r.had) g[k] = r.v; else delete g[k]; }
  }
}

test("a relay socket says once that its kernel recognises a resend, and says when it went down", async () => {
  await withManager((fm, events) => {
    fm.openRemote("TESTHOST", true);
    const ws = FakeWS.made[0];
    ws.open();
    ws.frame({ type: "ka", dv: 1 });                                       // an older kernel's keepalive: no word
    assert.deepEqual(events.filter((e) => e.type === "romp:hostAcks"), [], "nothing is ever sent twice to a kernel that did not say so");
    assert.equal(fm.conns.get("TESTHOST").acksSeen, false);
    ws.frame({ type: "ka", dv: 1, acks: 1 });
    ws.frame({ type: "ka", dv: 1, acks: 1 });
    assert.deepEqual(events.filter((e) => e.type === "romp:hostAcks"), [{ type: "romp:hostAcks", host: "TESTHOST" }], "once per socket");
    ws.die();
    assert.deepEqual(events.filter((e) => e.type === "romp:hostRelayDown"), [{ type: "romp:hostRelayDown", host: "TESTHOST" }]);
    assert.equal(fm.conns.get("TESTHOST").acksSeen, false, "the next socket has said nothing yet");
    fm.connect(fm.conns.get("TESTHOST"));
    const ws2 = FakeWS.made[1];
    ws2.open();
    ws2.frame({ type: "ka", dv: 1, acks: 1 });
    assert.equal(events.filter((e) => e.type === "romp:hostAcks").length, 2, "…and the next socket says it again");
    fm.conns.get("TESTHOST").closed = true;
  });
});

// ── the chat's wiring ────────────────────────────────────────────────────────────────────────────
test("the chat keeps, answers, resends and restores its sends through send-acks.ts", () => {
  const reg = RENDER.slice(RENDER.indexOf("\nfunction registerOptimistic("), RENDER.indexOf("\nfunction registerOptimistic(") + 2500);
  assert.match(reg, /p\.wire = wire \|\| defaultWire\(id, p\);/, "every send keeps the message it posted");
  assert.match(reg, /persistKeptSends\(\);/, "…and is kept across a reload from the press");
  assert.match(RENDER, /if \(m\.type === "sendAck"\) \{ if \(typeof m\.id === "string" && typeof m\.qid === "string"\) onSendAck\(m\.id, m\.qid, !!m\.ok\); return; \}/);
  assert.match(RENDER, /window\.addEventListener\("romp:hostAcks", /, "a socket that says it recognises a resend gets the unanswered sends");
  assert.match(RENDER, /window\.addEventListener\("romp:hostRelayDown", /, "a relay that went down marks its sends not confirmed");
  assert.match(RENDER, /restoreKept\(\(\(vscodeApi\?\.getState\?\.\(\) \|\| \{\}\) as any\)\.pendingSends\)/, "a fresh page reads back what the last one kept");
  assert.match(RENDER, /vscodeApi\?\.setState\?\.\(\{ \.\.\.st, pendingSends: mergeKept\(mine, restoreKept\(st\.pendingSends, now\), heldQids, now, hostGone\) \}\);/,
               "the write merges into what the store holds now, every other tab's sends kept");
  assert.match(RENDER, /pendingSent\.delete\(s\.id\);\n  persistKeptSends\(\);/, "a landing that retires a send updates what is kept");
  // a follow-up keeps its own wire (the goal it answers rides it)
  assert.match(RENDER, /const fu = \{ type: "askFollowUp", itemId: goalCite\.itemId, text, sid, qid, \.\.\.att \}; vscodeApi\.postMessage\(fu\); registerOptimistic\(sid, text, imgPaths, qid, paths, false, fu\);/);
});

// ── the review of #198 (2026-10-09): withdrawals, tabs, sessions still being created, the age caps ──────────────
test("a send withdrawn with the ✎ or the ✕ leaves the store with that write, so a reload never sends it", () => {
  const a = sent(LOCAL, "rebuild the index", qid("a")), b = sent(LOCAL, "and the docs", qid("b"));
  a.lost = b.lost = "connection";                                       // the socket is down: both unanswered
  const pending = new Map([[LOCAL, [a, b]]]);
  const held = new Set([qid("a"), qid("b")]);
  let store = mergeKept(keptSends(pending, NOW), [], held, NOW);
  assert.deepEqual(store.map((k) => k.qid), [qid("a"), qid("b")]);
  dropPending(pending.get(LOCAL)!, "", undefined, qid("a"));            // the ✎: the words go back in the box
  store = mergeKept(keptSends(pending, NOW), restoreKept(store, NOW), held, NOW);
  assert.deepEqual(restoreKept(store, NOW).map((k) => k.qid), [qid("b")], "the reload restores only what is still owed");
  // every removal in the chat writes the store (the ✕/✎ handler, the refusal warn, a provisional tab's teardown)
  assert.match(RENDER, /pendingSent\.delete\(sidQ\); \}\n    persistKeptSends\(\);/, "the ✕/✎ handler");
  assert.match(RENDER, /else pendingSent\.delete\(m\.sid\);\n          persistKeptSends\(\);/, "the refusal that names the press");
  assert.equal((RENDER.match(/pendingSent\.delete\(id\);[^\n]*\n\s*persistKeptSends\(\);/g) || []).length, 2, "both provisional teardowns");
});

test("two tabs share the store: one tab's write keeps the other's unanswered sends", () => {
  const mineA = sent(LOCAL, "tab A's message", qid("1")), mineB = sent(LOCAL, "tab B's message", qid("2"));
  const storeA = mergeKept(keptSends(new Map([[LOCAL, [mineA]]]), NOW), [], new Set([qid("1")]), NOW);
  const storeAB = mergeKept(keptSends(new Map([[LOCAL, [mineB]]]), NOW), restoreKept(storeA, NOW), new Set([qid("2")]), NOW);
  assert.deepEqual(storeAB.map((k) => k.qid).sort(), [qid("1"), qid("2")], "tab B's save did not wipe tab A's send");
  mineB.acked = true; mineB.received = true;                            // tab B's send answered
  const after = mergeKept(keptSends(new Map([[LOCAL, [mineB]]]), NOW), restoreKept(storeAB, NOW), new Set([qid("2")]), NOW);
  assert.deepEqual(after.map((k) => k.qid), [qid("1")], "B's answered send leaves; A's stays");
});

test("the store is bounded: old sends drop, the newest are kept, a host no longer attached loses its sends", () => {
  const rows = Array.from({ length: KEPT_MAX + 5 }, (_, i) => ({ sid: LOCAL, text: "m" + i, qid: "echo:" + i.toString(16).padStart(32, "0"),
    ts: NOW - (KEPT_MAX + 5 - i) * 1000, wire: { type: "sendMessage", id: LOCAL, text: "m" + i, qid: "echo:" + i.toString(16).padStart(32, "0") } }));
  const capped = mergeKept([], restoreKept(rows, NOW), new Set(), NOW);
  assert.equal(capped.length, KEPT_MAX);
  assert.equal(capped[capped.length - 1].text, "m" + (KEPT_MAX + 4), "the newest kept");
  const old = { ...rows[0], ts: NOW - KEPT_MAX_AGE_MS - 1 };
  assert.deepEqual(restoreKept([old], NOW), [], "past the age cap: not restored");
  const remote = { ...rows[1], sid: REMOTE, wire: { ...rows[1].wire, id: REMOTE } };
  assert.deepEqual(mergeKept([], [remote], new Set(), NOW, (sid) => hostOf(sid) === "TESTHOST"), [], "its host was detached");
});

test("a send older than the kernel's memory is never resent: it keeps its label and its words", () => {
  const stale = sent(LOCAL, "from last week", qid("7")); stale.ts = NOW - RESEND_MAX_AGE_MS - 1; stale.lost = "reload";
  const fresh = sent(LOCAL, "from today", qid("8"));
  const r = resendable(new Map([[LOCAL, [stale, fresh]]]), "", hostOf, new WeakMap(), 1, NOW);
  assert.deepEqual(r.map((x) => x.p.qid), [qid("8")]);
  assert.ok(RESEND_MAX_AGE_MS < 7 * 86400_000, "inside the kernel's seven days (kernel.py _SEND_ACCEPTS_TTL_S)");
  assert.equal(stale.lost, "reload", "still says not confirmed");
});

test("a send typed into a session still being created is neither kept nor resent", () => {
  const PROV = "new-notesapi1";
  const p = sent(PROV, "set up the repo", qid("9"));
  assert.deepEqual(keptSends(new Map([[PROV, [p]]]), NOW), []);
  assert.deepEqual(resendable(new Map([[PROV, [p]]]), "", hostOf, new WeakMap(), 1, NOW), []);
  assert.deepEqual(restoreKept([{ sid: PROV, text: "x", qid: qid("9"), ts: NOW, wire: { type: "sendMessage", id: PROV, text: "x", qid: qid("9") } }], NOW), []);
  assert.match(RENDER, /if \(!isProvisionalId\(id\)\) \{ p\.wire = wire \|\| defaultWire\(id, p\); if \(p\.qid\) heldQids\.add\(p\.qid\); \}/,
               "the chat gives a provisional tab's bubble no wire");
});

test("a send dropped while its host is down says it is kept, once that host has said it recognises a resend", async () => {
  await withManager((fm, events) => {
    const warns: string[] = [];
    (globalThis as any).window.dispatchEvent = (ev: any) => { if (ev && ev.data && ev.data.type === "warn") warns.push(ev.data.text); };
    fm.openRemote("TESTHOST", true);
    FakeWS.made[0].open();
    FakeWS.made[0].die();
    fm.outbound({ type: "sendMessage", id: REMOTE, text: "rebuild the index", qid: qid("a") });
    assert.match(warns[0], /“sendMessage” was not delivered/, "a host that never said so: the old words");
    fm.connect(fm.conns.get("TESTHOST"));
    FakeWS.made[1].open();
    FakeWS.made[1].frame({ type: "ka", dv: 1, acks: 1 });
    FakeWS.made[1].die();
    fm.outbound({ type: "sendMessage", id: REMOTE, text: "rebuild the index", qid: qid("b") });
    assert.match(warns[1], /your message is kept and goes out when the link is back/, "no retyping, no double send");
    fm.conns.get("TESTHOST").closed = true;
    void events;
  });
});
