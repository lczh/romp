// A kernel setting aimed at a machine this page holds NO conn for yet (the gear-truth review, 2026-10-23). The page links a
// machine only from its /tunnels poll, every 4 s, so in the seconds after a machine checks in, attaches or reappears the gear
// can show it (its own /tunnels read) while no conn exists. A setting aimed at it then fell to the gesture drop, whose warn
// said the machine was "unreachable" (it was up) and which nothing on the settings page showed, and the gear's All kernels
// broadcast went only to the machines the page had linked, skipping the new one without a word.
// Now: such a setting is HELD (newest per type), the send starts a poll at once, and when that poll's answer lists the machine
// the held settings ride its new conn and flush on the open; an answer that does not list it drops them with a warn that says
// what the page knows (no connection), naming the host, the type and the cause. The gear's All kernels broadcast names the
// machines its panel shows (`reach`), which the route reaches beside the hosts the page knows, and strips. A poll already on
// its way when the setting was sent never judges it: its list predates the machine.
// End-to-end through the real FederationManager (the federation-send-queue harness): window, location, WebSocket and fetch
// stubbed, the poll's answer controlled per call.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { FederationManager, routeOutbound } from "./federation";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: unknown) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  send(s: string): void {
    if (this.readyState !== 1) throw new Error("send() on a socket that is not OPEN");
    this.sent.push(s);
  }
  close(): void { this.readyState = 3; }
  open(): void { this.readyState = 1; if (this.onopen) this.onopen(); }
  msgs(): any[] { return this.sent.map((s) => JSON.parse(s)); }
}

type Answer = { tunnels: any[] };
const row = (host: string, status = "up") => ({ host, status, hasToken: true, localPort: 4242 });

/** The manager with its world stubbed: `answers` are handed to the polls in order (each poll takes the next, and waits for it). */
async function withFed(fn: (fm: any, ctl: { winEvents: any[]; localSends: any[]; answer: (a: Answer) => void; polls: () => number }) => Promise<void>): Promise<void> {
  const g: any = globalThis;
  const keys = ["window", "location", "WebSocket", "fetch"] as const;
  const had: Record<string, boolean> = {}, prev: Record<string, any> = {};
  for (const k of keys) { had[k] = k in g; prev[k] = g[k]; }
  const winEvents: any[] = [], localSends: any[] = [];
  const waiting: Array<(a: Answer) => void> = [];
  let polls = 0;
  FakeSocket.instances = [];
  g.window = {
    dispatchEvent: (ev: any) => { if (ev && ev.data) winEvents.push(ev.data); return true; },
    __rompLocalSend: (m: any) => localSends.push(m),
  };
  g.location = { protocol: "http:", host: "127.0.0.1:0", search: "" };
  g.WebSocket = FakeSocket;
  g.fetch = (url: string) => {
    assert.equal(url, "/tunnels");
    polls++;
    return new Promise((res) => waiting.push((a: Answer) => res({ ok: true, status: 200, json: async () => a })));
  };
  const answer = (a: Answer) => { const w = waiting.shift(); assert.ok(w, "a poll is waiting for its answer"); w!(a); };
  try {
    await fn(new FederationManager() as any, { winEvents, localSends, answer, polls: () => polls });
  } finally {
    for (const k of keys) { if (had[k]) g[k] = prev[k]; else delete g[k]; }
  }
}
const settle = () => new Promise((r) => setTimeout(r, 0));
const diags = (localSends: any[], what: string) =>
  localSends.filter((m) => m && m.type === "clientDiag" && m.surface === "federation" && m.what === what).map((m) => m.data);

test("a setting picked for a machine the page has not linked yet is held, the send polls at once, and it is delivered on the new conn's open", async () => {
  await withFed(async (fm, ctl) => {
    fm.outbound({ type: "setThinkingSummaries", enabled: false, gt: 5, hosts: ["TESTHOSTB"], scope: "pinned" });
    assert.equal(ctl.polls(), 1, "the send itself started the poll: no wait for the 4 s tick");
    assert.equal(fm.conns.has("TESTHOSTB"), false, "no conn yet");
    assert.deepEqual(ctl.winEvents.filter((m) => m.type === "warn"), [], "held, not dropped: no warn");
    assert.deepEqual(diags(ctl.localSends, "sendqueue"), [{ host: "TESTHOSTB", msgType: "setThinkingSummaries", gt: 5, rs: -1, why: "no-conn" }]);
    ctl.answer({ tunnels: [row("TESTHOSTB")] });
    await settle();
    const sock = fm.conns.get("TESTHOSTB").ws as FakeSocket;
    assert.ok(sock, "the poll's answer opened the conn");
    assert.deepEqual(sock.sent, [], "nothing can ride a CONNECTING socket");
    sock.open();
    assert.deepEqual(sock.msgs(), [{ type: "setThinkingSummaries", enabled: false, gt: 5, origin: "remote", scope: "pinned" }],
      "the open delivered the setting, unchanged (its gesture stamp included)");
    assert.deepEqual(ctl.winEvents.filter((m) => m.type === "warn"), []);
  });
});

test("the gear's All kernels click reaches every machine its panel shows (`reach`), the unlinked one included, and the field never leaves the page", async () => {
  await withFed(async (fm, ctl) => {
    fm.openRemote("TESTHOSTA", true);
    const a = fm.conns.get("TESTHOSTA").ws as FakeSocket;
    a.open();
    fm.outbound({ type: "setThinkingSummaries", enabled: true, gt: 7, reach: ["TESTHOSTA", "TESTHOSTB"] });
    assert.deepEqual(ctl.localSends.filter((m) => m.type === "setThinkingSummaries"), [{ type: "setThinkingSummaries", enabled: true, gt: 7, origin: "local" }]);
    assert.deepEqual(a.msgs(), [{ type: "setThinkingSummaries", enabled: true, gt: 7, origin: "remote" }], "the linked machine hears it once, no reach field");
    ctl.answer({ tunnels: [row("TESTHOSTA"), row("TESTHOSTB")] });
    await settle();
    const b = fm.conns.get("TESTHOSTB").ws as FakeSocket;
    b.open();
    assert.deepEqual(b.msgs(), [{ type: "setThinkingSummaries", enabled: true, gt: 7, origin: "remote" }], "…and so does the one the page had not linked");
  });
});

test("the route: `reach` widens the broadcast and is stripped from every copy, a scoped copy included", () => {
  const known = new Set(["TESTHOSTA"]);
  assert.deepEqual(routeOutbound({ type: "setConserve", enabled: true, gt: 1, reach: ["TESTHOSTA", "TESTHOSTB", "", 3] }, known), [
    { host: "", msg: { type: "setConserve", enabled: true, gt: 1, origin: "local" } },
    { host: "TESTHOSTA", msg: { type: "setConserve", enabled: true, gt: 1, origin: "remote" } },
    { host: "TESTHOSTB", msg: { type: "setConserve", enabled: true, gt: 1, origin: "remote" } },
  ]);
  assert.deepEqual(routeOutbound({ type: "setConserve", enabled: true, gt: 1, hosts: ["TESTHOSTB"], scope: "pinned", reach: ["TESTHOSTA"] }, known), [
    { host: "TESTHOSTB", msg: { type: "setConserve", enabled: true, gt: 1, origin: "remote", scope: "pinned" } },
  ], "a pick goes to the picked machines alone");
});

test("a held setting for a machine the poll does not list is dropped LOUDLY, in words that say what the page knows", async () => {
  await withFed(async (fm, ctl) => {
    fm.outbound({ type: "setConserve", enabled: true, gt: 9, hosts: ["TESTHOSTB"], scope: "pinned" });
    ctl.answer({ tunnels: [] });
    await settle();
    const warns = ctl.winEvents.filter((m) => m.type === "warn");
    assert.deepEqual(warns, [{ type: "warn", host: "TESTHOSTB", msgType: "setConserve", cause: "unlinked",
                               text: "this page has no connection to TESTHOSTB — “setConserve” was not delivered" }],
      "never \"unreachable\": the page knows only that it has no link to that machine");
    assert.deepEqual(diags(ctl.localSends, "senddrop"), [{ host: "TESTHOSTB", msgType: "setConserve", why: "no-conn" }]);
    assert.equal(fm.conns.has("TESTHOSTB"), false);
  });
});

test("a poll already on its way when the setting was sent does not judge it: its list predates the machine", async () => {
  await withFed(async (fm, ctl) => {
    void fm.poll();   // the 4 s tick, begun before the machine checked in
    assert.equal(ctl.polls(), 1);
    fm.outbound({ type: "setConserve", enabled: false, gt: 11, hosts: ["TESTHOSTB"], scope: "pinned" });
    assert.equal(ctl.polls(), 2, "the send started its own poll");
    ctl.answer({ tunnels: [] });   // the earlier poll's answer: no TESTHOSTB yet
    await settle();
    assert.deepEqual(ctl.winEvents.filter((m) => m.type === "warn"), [], "not judged by an answer that began before the send");
    ctl.answer({ tunnels: [row("TESTHOSTB")] });   // the send's own poll
    await settle();
    const b = fm.conns.get("TESTHOSTB").ws as FakeSocket;
    b.open();
    assert.deepEqual(b.msgs().map((m: any) => [m.type, m.enabled]), [["setConserve", false]], "delivered");
  });
});

test("held newest per type: two picks of one setting before the link opens deliver only the newer", async () => {
  await withFed(async (fm, ctl) => {
    fm.outbound({ type: "setConserve", enabled: true, gt: 20, hosts: ["TESTHOSTB"], scope: "pinned" });
    fm.outbound({ type: "setConserve", enabled: false, gt: 21, hosts: ["TESTHOSTB"], scope: "pinned" });
    assert.equal(diags(ctl.localSends, "sendqueue")[1].superseded, 20, "the replaced pick is named");
    ctl.answer({ tunnels: [row("TESTHOSTB")] });
    ctl.answer({ tunnels: [row("TESTHOSTB")] });
    await settle();
    const b = fm.conns.get("TESTHOSTB").ws as FakeSocket;
    b.open();
    assert.deepEqual(b.msgs().map((m: any) => [m.enabled, m.gt]), [[false, 21]]);
  });
});

test("a gesture to a conn whose socket is still opening says so, not that the machine is unreachable", async () => {
  await withFed(async (fm, ctl) => {
    fm.openRemote("TESTHOSTB", true);   // CONNECTING
    fm.outbound({ type: "createSession", host: "TESTHOSTB", name: "web", dir: "~/notes-api" });
    const w = ctl.winEvents.filter((m) => m.type === "warn");
    assert.equal(w.length, 1);
    assert.equal(w[0].text, "this page's connection to TESTHOSTB is still opening — “createSession” was not delivered");
    assert.equal(w[0].cause, "opening");
  });
});

test("the remote kernel's settingAck arrives stamped with its host, so the gear knows which machine to re-read", async () => {
  const { prefixInbound } = await import("./federation");
  assert.deepEqual(prefixInbound("TESTHOSTB", { type: "settingAck", msgType: "setConserve", gt: 3 }),
    { type: "settingAck", msgType: "setConserve", gt: 3, host: "TESTHOSTB" });
});
