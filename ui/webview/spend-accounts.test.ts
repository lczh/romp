// The spend modal names each Claude account one way and marks seat dollars as estimates (the user 2026-10-01, who read an
// email in the spend display and could not tell which account it was, nor how the breakdown worked). The kernel folds
// every host's billing routes into accounts by identity (tests/test_spend_accounts.py pins that half); the landing page
// sums each account's routes over the chart's range and renders the By account summary, and a session row's dollars
// wear the approx mark when a subscription seat billed them. The landing page has no jsdom harness: the pure functions
// are lifted from the kernel's inline JS (the spend-range-list.test.ts idiom) and EXECUTED on a synthetic payload.
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
  "function spSeatPart(d,st,usd){", "function spRouteSums(d){", "function spAccountRows(d){", "function spAcctName(d,s){",
  "function accountsHTML(d){", "function spRangeView(d){", "function spRangeWords(ser){"].map(fn).join("\n");

function lift(range: string) {
  const SP = { range, measure: "usd", order: "spend", merge: false };
  const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as Record<string, string>)[c]);
  const fmtUsd = (v: number) => "$" + String(Math.round(v));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("SP", "esc", "fmtUsd", SRC + "\n; return {spAccountRows, accountsHTML, spRangeView, spUsd, spAcctName, SP_GRAIN, SP_APPROX, SP_EST_TIP};")(SP, esc, fmtUsd);
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

test("the summary's rendered labels: the approx mark on seat dollars, none on key dollars, the email only in the hover", () => {
  const { accountsHTML, SP_APPROX, SP_EST_TIP } = lift("day");
  assert.equal(SP_APPROX, "≈");
  const h: string = accountsHTML(payload());
  const rows = h.split("<tr ").slice(1);
  assert.equal(rows.length, 3);
  const seatRow = rows[0], keyRow = rows[1];
  assert.match(seatRow, /^data-acct="seat"><td class=rsp-name data-tip="dev@example.com · Example Org · enterprise">Enterprise seat<span class=ru-tip-reset> · TESTHOST sign-in, token on PEERHOST<\/span><\/td>/);
  assert.ok(seatRow.includes("<span class=rsp-est data-tip=\"" + SP_EST_TIP + "\">≈$120</span>"), seatRow);
  assert.ok(keyRow.includes("<td class=n>$36</td>"), keyRow);
  assert.ok(!keyRow.includes("≈") && !keyRow.includes("rsp-est"), "no estimate mark on the key's dollars: " + keyRow);
  const visible = h.replace(/data-tip="[^"]*"/g, "");
  assert.ok(!visible.includes("@"), "the email is off every label: " + visible);
  assert.ok(h.includes("<div class=rsp-note>≈ " + SP_EST_TIP + ".</div>"), "the mark is explained once under the summary");
  assert.ok(h.includes("<span>By account</span><span class=ru-tip-reset>last 24 hours</span>"), "the range is named");
  // an older peer whose series carry no routes is named, never silently missing
  const p = payload(); p.accountsMissing = ["OLDHOST"];
  assert.ok(accountsHTML(p).includes("<div class=rsp-note>OLDHOST: older build, not in this breakdown</div>"));
  // nothing to show: no section
  assert.equal(accountsHTML({ ...payload(), accounts: [] }), "");
});

test("a session row's dollars wear the mark when a seat billed them, by its host's billing rule", () => {
  const { spRangeView, spUsd, SP_GRAIN } = lift("day");
  const v = spRangeView(payload());
  const by = (n: string) => v.sessions.find((s: any) => s.name === n);
  assert.equal(by("web").seat, 0, "a keyed host's rows count key-billed turns only");
  assert.equal(by("api").seat, 48, "a total host's row: everything the key did not bill");
  assert.equal(by("tests").seat, 0, "…and nothing when the key billed it all");
  assert.ok(spUsd(by("api").usd, by("api").seat > SP_GRAIN).startsWith("<span class=rsp-est"), "the seat row is marked");
  assert.equal(spUsd(by("web").usd, by("web").seat > SP_GRAIN), "$24", "the key row is plain");
  // a host where nothing bills a key: every dollar is a seat's
  const p = payload(); p.hosts[0].scope = "computed";
  assert.equal(lift("day").spRangeView(p).sessions.find((s: any) => s.name === "web").seat, 24);
  // the roster's stored login rides into the range view, and names its account
  assert.equal(by("api").loginId, "0a0b0c0d0e0f");
  assert.equal(lift("day").spAcctName(payload(), by("api")), "Enterprise seat");
});

test("the renderer wires the summary, the marks and the account name into the modal", () => {
  assert.ok(USAGE_JS.includes("'<td class=n>'+spUsd(s.usd,s.seat>SP_GRAIN)+'</td>'"), "a session row's dollars");
  assert.ok(USAGE_JS.includes("<span class=ru-tip-reset data-tip=\"'+esc(s.s.login)+'\"> \\u00b7 bills '+esc(spAcctName(d,s.s)||s.s.loginLabel||'a stored login')+'</span>"),
    "a stored-login row bills its account by name, its display (and any email) in the hover");
  assert.ok(USAGE_JS.includes("h+='<div class=rsp-sum><div class=rsp-sec id=rsp-totals>'+totalsHTML(d)+'</div><div class=rsp-sec id=rsp-accts'+(ah?'':' hidden')+'>'+ah+'</div></div>';"), "the section, beside the totals");
  assert.ok(USAGE_JS.includes("var ac=document.getElementById('rsp-accts');if(ac){var ah=accountsHTML(SP.data);ac.innerHTML=ah;ac.hidden=!ah;}"), "a range switch re-sums it");
  assert.ok(USAGE_JS.indexOf("id=rsp-accts") < USAGE_JS.indexOf("<span>By session"), "By account sits above By session");
  assert.ok(!USAGE_JS.includes("By login: "), "the one-line note is gone");
  // the hover words ride the modal's own tooltip from data-tip, delegated on the stable panel (no native title: rail-spend.test.ts)
  assert.ok(USAGE_JS.includes("var box=spTipBox();box.textContent=t.getAttribute('data-tip');spTipPlace(e.clientX,e.clientY,null);"), "textContent: user words, never markup");
  assert.ok(USAGE_JS.includes("if(spPanel){spPanel.addEventListener('mouseover',function(e){var t=spWordsAt(e.target);"), "one listener on the stable panel");
  assert.ok(!USAGE_JS.includes("machine\\u2019s own login and the API key"), "…and its remainder wording");
});
