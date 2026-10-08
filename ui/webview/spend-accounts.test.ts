// The spend panel names each Claude account one way and marks seat dollars as estimates (the user 2026-10-01, who read an
// email in the spend display and could not tell which account it was, nor how the breakdown worked). The kernel folds
// every host's billing routes into accounts by identity (tests/test_spend_accounts.py pins that half); the landing page
// sums each account's routes over the charts' range. Since 2026-10-08 the accounts live in the panel's two cards (the
// user, who wanted the enterprise seat and the API key drawn apart): the seat's card is named by its one account, or
// lists them when its dollars come from more than one, each figure with the approx mark; the key's card is plain. The
// landing page has no jsdom harness: the pure functions are lifted from the kernel's inline JS (the
// spend-range-list.test.ts idiom) and EXECUTED on a synthetic payload.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const USAGE_JS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
const LINES = USAGE_JS.split("\n");

/** the function (or statement) starting at the line that begins with `start`, through the line where its braces close.
 *  The file text is a Python string: a backslash escape there may mean something else to Python than to JS, so a block
 *  may carry none, except a \uXXXX escape, which reads the same in both (the constants' approx mark and middle dot). */
function fn(start: string): string {
  const i = LINES.findIndex((l) => l.startsWith(start));
  assert.ok(i >= 0, "the landing JS carries a line starting " + JSON.stringify(start));
  let depth = 0, j = i;
  for (; j < LINES.length; j++) {
    let q: string | null = null;
    for (const ch of LINES[j]) {
      if (q) { if (ch === q) q = null; continue; }
      if (ch === "'" || ch === '"') q = ch;
      else if (ch === "{") depth++;
      else if (ch === "}") depth--;
    }
    if (depth <= 0 && j >= i) break;
  }
  const src = LINES.slice(i, j + 1).join("\n");
  assert.ok(!src.replace(/\\u[0-9a-fA-F]{4}/g, "").includes("\\"), "no backslash escape but \\uXXXX in an extracted block: " + start);
  return src;
}

const SRC = ["var SP_RANGE_BUCKETS=", "function spSeries(d){", "function spTail(ser,keep){", "function spKey(d,s){",
  "function spSumArr(a){", "function spRound4(v){", "var SP_GRAIN=", "function spUsd(v,est){", "function spHostScope(d,host){",
  "function spKeyArr(d,st,meas){", "function spPart(d,st,sec,meas){", "function spRouteSums(d){", "function spAccountRows(d){",
  "function spAcctName(d,s){", "function spRangeView(d){", "function spRangeWords(ser){", "function spAllowHTML(){", "function spCards(d){",
  "function spCardHTML(d,c,sec){"].map(fn).join("\n");

/** the lifted functions over a fresh SP state */
function lift(range: string) {
  const SP: Record<string, unknown> = { range, measure: "usd", order: "spend", merge: false };
  const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as Record<string, string>)[c]);
  const fmtUsd = (v: number) => "$" + Math.round(v).toLocaleString("en-US");
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("SP", "esc", "fmtUsd", "LAST", "WINS", SRC + "\n; return {SP, spAccountRows, spCards, spCardHTML, spRangeView, spUsd, spAcctName, SP_GRAIN, SP_APPROX};")(SP, esc, fmtUsd, [], []);
}

const H = 192, D = 90;
const arr = <T,>(n: number, f: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => f(i));

/** TESTHOST signed into the enterprise seat (its own sign-in) and billing a key; PEERHOST reaching the same seat through a
 *  stored login and holding spend from a sign-in that is signed out now; every hour spends on each route */
function payload(): Record<string, any> {
  const hk = arr(H, (i) => "h" + i), dk = arr(D, (i) => "d" + i);
  const route = (host: string, r: string, perHour: number, turns: number) => ({ host, route: r, usd: arr(H, () => perHour), turns: arr(H, () => turns) });
  const hours = { keys: hk, epochs: hk.map((_, i) => 1000 + i), stacks: [
    // a key-only session on TESTHOST (keyed scope: its rows count key turns only), and a seat session on PEERHOST (total
    // scope: every turn counts, its key dollars a sub-count)
    { kind: "sid", host: "TESTHOST", sid: "s-web", name: "web", bg: "#1EA1EB", live: true, usd: arr(H, () => 1), tok: arr(H, () => 100), turns: arr(H, () => 1), keyUsd: arr(H, () => 1) },
    { kind: "sid", host: "PEERHOST", sid: "s-api", name: "api", bg: "#54B204", live: true, usd: arr(H, () => 2), tok: arr(H, () => 100), turns: arr(H, () => 1), keyUsd: arr(H, () => 0) },
    { kind: "sid", host: "PEERHOST", sid: "s-tests", name: "tests", bg: "#4EA8A9", live: true, usd: arr(H, () => 0.5), tok: arr(H, () => 100), turns: arr(H, () => 1), keyUsd: arr(H, () => 0.5) },
  ], routes: [
    route("TESTHOST", "machine", 3, 2), route("TESTHOST", "key", 1, 1), route("PEERHOST", "login:0a0b0c0d0e0f", 2, 1),
    route("PEERHOST", "key", 0.5, 1), route("PEERHOST", "machine", 0.25, 1),
    { host: "PEERHOST", route: "untracked", usd: arr(H, (i) => (i < 30 ? 1 : 0)), turns: arr(H, (i) => (i < 30 ? 1 : 0)) },   // the oldest hours: in the week, not the day
  ] };
  const days = { keys: dk, stacks: [], routes: [] };
  const seat = [{ host: "TESTHOST", route: "machine", via: "TESTHOST sign-in", estimate: true },
    { host: "PEERHOST", route: "login:0a0b0c0d0e0f", via: "token on PEERHOST", estimate: true }];
  return { host: "TESTHOST", scope: "keyed", order: [], tags: [], unattributed: { usd: 0, tok: 0, turns: 0 }, hours, days,
    hosts: [{ host: "TESTHOST", status: "ok", scope: "keyed" }, { host: "PEERHOST", status: "ok", scope: "total" }],
    sessions: [{ sid: "s-web", host: "TESTHOST", name: "web", live: true, usd: 0, tok: 0, turns: 0 },
      { sid: "s-api", host: "PEERHOST", name: "api", live: true, usd: 0, tok: 0, turns: 0, login: "enterprise · dev@example.com", loginId: "0a0b0c0d0e0f", loginLabel: "enterprise" },
      { sid: "s-tests", host: "PEERHOST", name: "tests", live: true, usd: 0, tok: 0, turns: 0 }],
    accounts: [
      { kind: "seat", name: "Enterprise seat", title: "dev@example.com · Example Org · enterprise", routes: seat },
      { kind: "key", name: "API key", title: "Billed to the API key per token.", routes: [
        { host: "TESTHOST", route: "key", via: "TESTHOST", estimate: false }, { host: "PEERHOST", route: "key", via: "PEERHOST", estimate: false }] },
      { kind: "unknown", name: "Unknown", title: "Spend whose account romp cannot name.", routes: [
        { host: "PEERHOST", route: "machine", via: "PEERHOST's own sign-in, signed out now", estimate: true },
        { host: "PEERHOST", route: "untracked", via: "before billing was recorded", estimate: false }] }] };
}

test("one account row per Claude account, summed over the chart's range, the key plain and Unknown last", () => {
  const { spAccountRows } = lift("day");
  const rows = spAccountRows(payload());
  assert.deepEqual(rows.map((r: any) => r.name), ["Enterprise seat", "API key", "Unknown"]);
  const [seat, key, unk] = rows;
  assert.equal(seat.usd, 24 * (3 + 2), "the TESTHOST sign-in and the PEERHOST token, over the last 24 hours");
  assert.equal(seat.turns, 24 * (2 + 1));
  assert.deepEqual(seat.via, ["TESTHOST sign-in", "token on PEERHOST"]);
  assert.equal(seat.est, true);
  assert.equal(key.usd, 24 * 1.5);
  assert.equal(key.est, false, "key-billed dollars are what the key was billed");
  assert.deepEqual(unk.via, ["PEERHOST's own sign-in, signed out now"], "a route with nothing in this range drops from the words");
  assert.equal(unk.est, true);
  // the 7-day range reaches the untracked hours, and the seat sums grow with the range
  const week = lift("hours").spAccountRows(payload());
  assert.equal(week[0].usd, 168 * 5);
  assert.deepEqual(week[2].via, ["PEERHOST's own sign-in, signed out now", "before billing was recorded"]);
});

test("the seat's card lists its accounts when its dollars come from more than one, each marked, the email only in the hover", () => {
  const { spCards, spCardHTML, SP_APPROX } = lift("day");
  assert.equal(SP_APPROX, "≈");
  const p = payload(), c = spCards(p);
  assert.equal(c.seat.name, "Subscription logins", "the seat and a signed-out sign-in's spend: the card names neither alone");
  const h: string = spCardHTML(p, c, "seat");
  const rows = h.split("<tr ").slice(1);
  assert.equal(rows.length, 2, "the seat's accounts: the enterprise seat and Unknown; never the key");
  assert.match(rows[0], /^data-acct="seat"><td class=rsp-name data-tip="dev@example.com · Example Org · enterprise">Enterprise seat<span class=ru-tip-reset> · TESTHOST sign-in, token on PEERHOST<\/span><\/td><td class=n>≈\$120<\/td>/);
  assert.match(rows[1], /^data-acct="unknown"><td class=rsp-name data-tip="Spend whose account romp cannot name\.">Unknown<span class=ru-tip-reset> · PEERHOST's own sign-in, signed out now<\/span><\/td><td class=n>≈\$6<\/td>/);
  const visible = h.replace(/data-tip="[^"]*"/g, "");
  assert.ok(!visible.includes("@"), "the email is off every label: " + visible);
  const key: string = spCardHTML(p, c, "key");
  assert.ok(!key.includes("≈") && !key.includes("<tr "), "the key's card: plain dollars, no account rows");
});

test("one seat account names the card itself, its identity in the hover words, no account rows", () => {
  const { spCards, spCardHTML } = lift("day");
  const p = payload(); p.accounts = p.accounts.filter((a: any) => a.kind !== "unknown");
  const c = spCards(p);
  assert.equal(c.seat.name, "Enterprise seat");
  const h: string = spCardHTML(p, c, "seat");
  assert.ok(h.includes('<span class=rsp-card-t data-tip="dev@example.com · Example Org · enterprise">Enterprise seat</span>'), h);
  assert.ok(!h.includes("<tr "), "nothing to list");
});

test("a session row's two halves follow its host's billing rule, and a stored login names its account", () => {
  const { spRangeView, spUsd, spAcctName } = lift("day");
  const v = spRangeView(payload());
  const by = (n: string) => v.sessions.find((s: any) => s.name === n);
  assert.deepEqual([by("web").keyUsd, by("web").seatUsd], [24, 0], "an older keyed host's rows are its key's turns alone");
  assert.deepEqual([by("api").keyUsd, by("api").seatUsd], [0, 48], "every turn, the key billing none: all the seat's");
  assert.deepEqual([by("tests").keyUsd, by("tests").seatUsd], [12, 0], "…and the key's when the key billed it all");
  assert.equal(spUsd(48, true), "≈$48", "a seat's figure wears the mark");
  assert.equal(spUsd(24, false), "$24", "the key's is plain");
  assert.equal(spUsd(0, true), "$0", "a zero is no estimate");
  assert.equal(by("api").loginId, "0a0b0c0d0e0f");
  assert.equal(spAcctName(payload(), by("api")), "Enterprise seat");
});

test("the renderer wires the halves, the marks and the account name into the panel", () => {
  assert.ok(USAGE_JS.includes("var cell=function(r,sec){var v=sec==='key'?(r.keyUsd||0):(r.seatUsd||0);return '<td class=n>'+(v>=SP_GRAIN?spUsd(v,sec==='seat'):'\\u2014')+'</td>';};"),
    "a session row's dollars: one column per half, the seat's marked, a dash for nothing");
  assert.ok(USAGE_JS.includes("<span class=ru-tip-reset data-tip=\"'+esc(s.s.login)+'\"> \\u00b7 bills '+esc(spAcctName(d,s.s)||s.s.loginLabel||'a stored login')+'</span>"),
    "a stored-login row bills its account by name, its display (and any email) in the hover");
  assert.ok(!USAGE_JS.includes("By account"), "the separate summary is gone: the accounts live in the cards");
  assert.ok(!USAGE_JS.includes("accts:toggle") && !USAGE_JS.includes("spPaintAccts"), "…and its narrow-card fold with it");
  assert.ok(!USAGE_JS.includes("By login: "), "the one-line note is gone");
  // the hover words ride the panel's own tooltip from data-tip, delegated on the stable panel (no native title: rail-spend.test.ts)
  assert.ok(USAGE_JS.includes("var box=spTipBox();box.textContent=t.getAttribute('data-tip');spTipPlace(e.clientX,e.clientY,null);"), "textContent: user words, never markup");
  assert.ok(USAGE_JS.includes("if(spPanel){spPanel.addEventListener('mouseover',function(e){var t=spWordsAt(e.target);"), "one listener on the stable panel");
});
