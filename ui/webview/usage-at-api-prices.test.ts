// Usage at API prices (the user 2026-10-08, who read "API" on the bottom bar's spend cell while nearly all of their usage
// bills an enterprise seat, could not tell what the dot beside it meant, and found the spend panel confusing): the cell
// sums every recorded turn at API prices by the route that billed it, marks any figure holding a seat's estimate with
// the approx sign, and names no billing it does not have; the hover splits each figure by account; the panel draws the
// seat (estimated, not charged) and the API key (charged) as two cards with their own charts, and any figure that mixes
// the two says so in words. The landing page has no jsdom harness: the pure functions are lifted from the kernel's
// inline JS (the spend-accounts.test.ts idiom) and EXECUTED on synthetic payloads (the notes-api demo: web, api, tests
// on TESTHOST and a peer, invented figures).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const USAGE_JS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
const LINES = USAGE_JS.split("\n");

/** the function (or statement) starting at the line that begins with `start`, through the line where its braces close */
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
  return LINES.slice(i, j + 1).join("\n")
    .replace(/\\u([0-9a-fA-F]{4})/g, (_m, h) => "\\u" + h)    // a \uXXXX escape reads the same to Python and to JS
    .replace(/\\\\/g, "\\");                                   // the Python string's doubled backslash is one in the page
}
const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as Record<string, string>)[c]);
const fmtTok = (n: number) => n >= 1e9 ? (n / 1e9).toFixed(2) + "B" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
const fmtAgo = () => "just now";
const text = (h: string) => h.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

// ── the bar's cell and its hover ─────────────────────────────────────────────────────────────────────────────────────
const RAIL = ["function fmtUsd(v){", "var COST_WINS=", "var COST_GRAIN=", "function hasCost(u){", "function costDet(u,det){",
  "function costEst(v){", "function costUsd(v){", "function costSums(live){", "function costCellHTML(live){", "function costZero(){",
  "function costRows(sets){", "var COST_EST_WORDS=", "function costTipHTML(sets){"].map(fn).join("\n");
function rail(accts: unknown[]) {
  const hasSpend = (u: any) => !!(u && u.spend && (u.spend.day || u.spend.fiveHour));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("ACCTS", "esc", "fmtTok", "fmtAgo", "hasSpend", RAIL + "\n; return {fmtUsd, costDet, costCellHTML, costTipHTML, costRows};")(accts, esc, fmtTok, fmtAgo, hasSpend);
}
/** a host's usage payload: its dollars by billing route for the calendar day and month, as _rail_routes ships them */
const usage = (today: Record<string, number>, month: Record<string, number>) => ({ spendRoutes: {
  today: Object.fromEntries(Object.entries(today).map(([r, v]) => [r, [v, 3]])),
  monthToDate: Object.fromEntries(Object.entries(month).map(([r, v]) => [r, [v, 30]])),
  week: {}, tok: { today: { tok: 60_300_000 }, monthToDate: { tok: 1_670_000_000, tokCacheR: 1_530_000_000, tokCacheW: 100_000_000, tokIn: 20_000_000, tokOut: 20_000_000 } },
  at: 1_700_000_000 } });
const SEAT_ACCTS = [{ name: "Enterprise seat", kind: "seat", routes: [{ host: "TESTHOST", route: "machine", est: true }, { host: "PEERHOST", route: "login:0a0b0c0d0e0f", est: true }] },
  { name: "API key", kind: "key", routes: [{ host: "TESTHOST", route: "key", est: false }, { host: "PEERHOST", route: "key", est: false }] }];
function sets(R: ReturnType<typeof rail>, rows: Array<[string, unknown]>) {
  return rows.map(([host, u]) => { const det: Record<string, unknown> = {}; R.costDet(u, det); return { host, det }; });
}

test("the cell sums every route at API prices and marks a figure that holds a seat's estimate; no 'API' label", () => {
  const R = rail(SEAT_ACCTS);
  const live = sets(R, [["TESTHOST", usage({ machine: 117.15 }, { machine: 117.15, key: 3374.32 })],
    ["PEERHOST", usage({ "login:0a0b0c0d0e0f": 65.44 }, { "login:0a0b0c0d0e0f": 65.44, key: 1497.88 })]]);
  const cell = R.costCellHTML(live);
  assert.equal(text(cell), "today ≈$183 this month ≈$5,055", "both hosts, every route, thousands grouped");
  assert.ok(!/>API</.test(cell), "no 'API' label: the figures are mostly a seat's");
  assert.ok(!cell.includes("ah-slot"), "the API-health dot is not in the spend cell");
  // a key-only world: the dollars were charged, so no approx mark
  const K = rail([]);
  assert.equal(text(K.costCellHTML(sets(K, [["TESTHOST", usage({ key: 14 }, { key: 1320.4 })]]))), "today $14 this month $1,320");
  // a host with no ledger: no cell at all; a ledger whose windows hold nothing: an honest zero
  assert.equal(K.costCellHTML(sets(K, [["TESTHOST", {}]])), "");
  assert.equal(text(K.costCellHTML(sets(K, [["TESTHOST", usage({}, {})]]))), "today $0 this month $0");
});

test("the hover splits each figure by account, the key's row says charged, and the total says it mixes them", () => {
  const R = rail(SEAT_ACCTS);
  const live = sets(R, [["TESTHOST", usage({ machine: 117.15 }, { machine: 117.15, key: 3374.32 })],
    ["PEERHOST", usage({ "login:0a0b0c0d0e0f": 65.44 }, { "login:0a0b0c0d0e0f": 65.44, key: 1497.88 })]]);
  const t = text(R.costTipHTML(live));
  assert.match(t, /^Usage at API prices today this month Enterprise seat ≈\$183 ≈\$183 API key charged \$0 \$4,872 Total ≈\$183 ≈\$5,055 tokens 120\.6M 3\.34B/,
    "one seat reached two ways is ONE row (the kernel's fold); the rows add up to the cell");
  assert.match(t, /≈ marks an estimate: what that usage would cost at API prices, since a subscription seat is not billed per token\./);
  assert.match(t, /by machine this month: TESTHOST ≈\$3,491 · PEERHOST ≈\$1,563/, "every machine in the figures, by name");
  assert.match(t, /this month: 3\.06B cache read · 200\.0M cache write · 40\.0M in · 40\.0M out/, "the month's tokens by kind");
  assert.match(t, /last turn recorded just now/);
});

test("spend the kernel has not folded into an account yet is still counted: the key's joins API key, a seat's an 'other' row", () => {
  const R = rail([]);   // before the first /usage/fleet read (or a route recorded since)
  const rows = R.costRows(sets(R, [["TESTHOST", usage({ machine: 10 }, { machine: 10, key: 90 })]]));
  assert.deepEqual(rows.map((r: any) => [r.name, Math.round(r.v.monthToDate.usd)]), [["API key", 90], ["other", 10]]);
});

test("a single account needs no total row, and an older build's host is named rather than summed", () => {
  const R = rail([SEAT_ACCTS[0]]);
  const live = sets(R, [["TESTHOST", usage({ machine: 117.15 }, { machine: 400 })], ["OLDHOST", { spend: { day: { usd: 3 } } }]]);
  const t = text(R.costTipHTML(live));
  assert.ok(!t.includes("Total"), "the one row is the total");
  assert.match(t, /OLDHOST: older build, its usage is not in these figures/);
  assert.equal(text(R.costCellHTML(live)), "today ≈$117 this month ≈$400", "the older host's key windows are not summed as these");
});

// ── the panel: two cards, two charts, one list ───────────────────────────────────────────────────────────────────────
const PANEL = ["var SP_RANGE_BUCKETS=", "function spSeries(d){", "function spTail(ser,keep){", "function spKey(d,s){", "function spSumArr(a){",
  "function spRound4(v){", "var SP_GRAIN=", "function spHostScope(d,host){", "function spKeyArr(d,st,meas){", "function spPart(d,st,sec,meas){",
  "function spUsd(v,est){", "function spRouteSums(d){", "function spAccountRows(d){", "function spAcctName(d,s){", "function spRangeView(d){",
  "function spRangeWords(ser){", "function spAllowHTML(){", "function spCards(d){", "function spCardHTML(d,c,sec){", "function spHosts(d){",
  "function spMany(d){", "function spName(s){", "function spColor(s){", "var SP_OTHER=", "function spTitle(s,many){", "function spTagChip(s){",
  "function spApplyViewOrder(seed,view){", "function spOrdered(d){", "function spRows(d){", "function spDashedHosts(d){",
  "function sessionTable(d){"].map(fn).join("\n");
function panel(data: unknown, last: unknown[] = []) {
  const SP: Record<string, unknown> = { data, range: "hours", measure: "usd", order: "spend", merge: false };
  const fmtUsd = (v: number) => "$" + Math.round(v).toLocaleString("en-US");
  const WINS = [["fiveHour", 5 * 3600, "5 hours"], ["sevenDay", 7 * 86400, "7 days"]];
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("SP", "LAST", "WINS", "esc", "fmtUsd", "fmtTok", "spViewOrder", PANEL + "\n; return {SP, spCards, spCardHTML, spRangeView, sessionTable, spPart};")(
    SP, last, WINS, esc, fmtUsd, fmtTok, () => []);
}
const H = 192, arr = <T,>(n: number, f: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => f(i));
/** two machines on scope "all" (every turn, the key's part beside it): web billed the key until the last day and the seat
 *  since; api bills the seat through a stored login on PEERHOST */
function detail(over: Record<string, unknown> = {}): Record<string, any> {
  const hk = arr(H, (i) => "2026-10-0" + (1 + Math.floor(i / 24)) + "T" + String(i % 24).padStart(2, "0"));
  const recent = (i: number) => i >= H - 24;
  const hours = { keys: hk, epochs: hk.map((_, i) => 1000 + i), stacks: [
    { kind: "sid", host: "TESTHOST", sid: "s-web", name: "web", bg: "#1EA1EB", live: true, usd: arr(H, () => 2), tok: arr(H, () => 100), turns: arr(H, () => 1),
      keyUsd: arr(H, (i) => (recent(i) ? 0 : 2)), keyTok: arr(H, (i) => (recent(i) ? 0 : 100)) },
    { kind: "sid", host: "PEERHOST", sid: "s-api", name: "api", bg: "#54B204", live: true, usd: arr(H, () => 1), tok: arr(H, () => 50), turns: arr(H, () => 1),
      keyUsd: arr(H, () => 0), keyTok: arr(H, () => 0) },
  ], routes: [
    { host: "TESTHOST", route: "key", usd: arr(H, (i) => (recent(i) ? 0 : 2)), turns: arr(H, (i) => (recent(i) ? 0 : 1)) },
    { host: "TESTHOST", route: "machine", usd: arr(H, (i) => (recent(i) ? 2 : 0)), turns: arr(H, (i) => (recent(i) ? 1 : 0)) },
    { host: "PEERHOST", route: "login:0a0b0c0d0e0f", usd: arr(H, () => 1), turns: arr(H, () => 1) },
  ] };
  return Object.assign({ host: "TESTHOST", scope: "all", order: [], tags: [], unattributed: { usd: 0, tok: 0, turns: 0 }, hours, days: { keys: [], stacks: [], routes: [] },
    hosts: [{ host: "TESTHOST", status: "ok", scope: "all" }, { host: "PEERHOST", status: "ok", scope: "all" }],
    sessions: [{ sid: "s-web", host: "TESTHOST", name: "web", live: true }, { sid: "s-api", host: "PEERHOST", name: "api", live: true }],
    accounts: [{ kind: "seat", name: "Enterprise seat", title: "dev@example.com · Notes Co · enterprise", routes: [
      { host: "TESTHOST", route: "machine", via: "TESTHOST sign-in", estimate: true }, { host: "PEERHOST", route: "login:0a0b0c0d0e0f", via: "token on PEERHOST", estimate: true }] },
      { kind: "key", name: "API key", title: "Billed to the API key per token.", routes: [{ host: "TESTHOST", route: "key", via: "TESTHOST", estimate: false }] }] }, over);
}

test("the panel's two cards: the seat's dollars (every turn less the key's) and the key's, apart, each with its own figure", () => {
  const d = detail(), P = panel(d);
  const c = P.spCards(d);
  // over the 7-day range (168 hours): web's last 24 hours bill the seat (24 × $2), api's every hour (168 × $1)
  assert.equal(Math.round(c.seat.usd), 24 * 2 + 168, "the seat's part");
  assert.equal(Math.round(c.key.usd), (168 - 24) * 2, "the key's part");
  assert.equal(c.seat.name, "Enterprise seat", "one account holds the seat's dollars: the card is named by it");
  assert.equal(c.key.name, "API key");
  assert.ok(c.seat.live && c.key.live);
  const seat = text(P.spCardHTML(d, c, "seat")), key = text(P.spCardHTML(d, c, "key"));
  assert.match(seat, /^Enterprise seat Estimated at API prices; the seat is not billed per token · last 7 days/);
  assert.match(key, /^API key Charged per token · last 7 days/);
  assert.ok(P.spCardHTML(d, c, "seat").includes('data-tip="dev@example.com · Notes Co · enterprise"'), "the identity rides the hover words, off the card");
});

test("a card whose kind holds nothing in the range is one quiet line, and a kind no account has is not drawn", () => {
  const d = detail(), P = panel(d);
  P.SP.range = "day";   // the last 24 hours: web billed the seat throughout, nothing billed the key
  const c = P.spCards(d);
  assert.ok(c.key.show && !c.key.live, "an API key exists in the ledger's reach, so its card shows, empty");
  assert.match(text(P.spCardHTML(d, c, "key")), /Nothing in the last 24 hours\.$/);
  assert.ok(!P.spCardHTML(d, c, "key").includes("rsp-chart"), "no chart for an empty card");
  const seatOnly = detail({ accounts: [detail().accounts[0]] });
  for (const st of seatOnly.hours.stacks) { st.keyUsd = arr(H, () => 0); st.keyTok = arr(H, () => 0); }
  const c2 = panel(seatOnly).spCards(seatOnly);
  assert.ok(!c2.key.show, "no key account and no key dollars: the API key card is not drawn at all");
});

test("the list has one dollar column per kind, headed by the card's title, the seat's figures approximate", () => {
  const d = detail(), P = panel(d);
  const h = P.sessionTable(P.spRangeView(d));
  assert.match(text(h), /^session Enterprise seat API key turns tokens/);
  assert.match(text(h), /web ≈\$48 \$288 168 16\.8k/, "web: the seat's part and the key's, side by side");
  assert.match(text(h), /api ≈\$168 — 168 8\.4k/, "api billed no key: a dash, never $0");
});

test("an older machine that sends its key's turns alone (scope keyed) is drawn as key dollars only", () => {
  const d = detail({ hosts: [{ host: "TESTHOST", status: "ok", scope: "all" }, { host: "PEERHOST", status: "ok", scope: "keyed" }] });
  const P = panel(d);
  const api = d.hours.stacks[1];
  assert.deepEqual(P.spPart(d, api, "key", "usd").slice(0, 2), [1, 1], "its usd is the key's");
  assert.deepEqual(P.spPart(d, api, "seat", "usd").slice(0, 2), [0, 0], "and nothing of it is the seat's");
});

test("the seat card carries the allowance from the bar's own rows: an enterprise seat's monthly allowance", () => {
  const d = detail();
  const last = [{ host: "TESTHOST", acct: "acct1", det: { _acct: "Enterprise seat", _monthly: { pct: 34, col: "#5196B8", reset: "Nov 1" } } }];
  const P = panel(d, last);
  assert.match(text(P.spCardHTML(d, P.spCards(d), "seat")), /monthly allowance 34% used · resets Nov 1/);
});

test("the API-health dot lives apart from the spend figures, named, at the head of the bar's right-hand cluster", () => {
  const acts = KERNEL.indexOf('"<div class=rail-acts>"'), dot = KERNEL.indexOf('"<div id=rail-api ');
  assert.ok(acts > 0 && dot > acts, "the cell sits inside the right-hand cluster, after its opening");
  assert.ok(KERNEL.includes('"<i class=ah-dot></i><span class=ru-name>API health</span></div>"'), "the dot and its words");
  assert.ok(!USAGE_JS.includes("ah-slot") && !USAGE_JS.includes("moveApiCell"), "the readout no longer adopts the dot");
  assert.ok(KERNEL.includes("How Anthropic\\u2019s API is answering your sessions, whichever account they bill."), "its hover says what it reports");
  // in the light theme a healthy API is blue, never the accent orange that sits beside the error red on a 7 px dot
  assert.ok(KERNEL.includes("body.theme-light #rail-api[data-dot=fine] .ah-dot,body.theme-light .ah-dot[data-dot=fine]{background:#356890}"));
});
