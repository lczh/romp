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
const RAIL = ["function fmtUsd(v){", "var COST_WINS=", "var COST_GRAIN=", "var VIEW_TZ=", "var MISSING=", "function hasCost(u){",
  "function costDet(u,det){", "function costEst(v){", "function costUsd(v){", "function costSums(live){", "function costGaps(live){",
  "function lrRound(vals){", "function wholeUsd(n,est){", "function costCellHTML(live){", "function costZero(){", "function costRows(sets){",
  "var COST_EST_WORDS=", "function costWhen(ep){", "function costGapWords(g){", "function costTipHTML(sets){"].map(fn).join("\n");
function rail(accts: unknown[], missing: unknown[] = []) {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("ACCTS", "esc", "fmtTok", "fmtAgo", RAIL + "\n; MISSING=arguments[4]; return {fmtUsd, costDet, costCellHTML, costTipHTML, costRows, lrRound, VIEW_TZ};")(accts, esc, fmtTok, fmtAgo, missing);
}
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
/** a host's usage payload: its two windows built in this viewer's zone, by billing route, as _spend_win ships them */
const usage = (today: Record<string, number>, month: Record<string, number>, over: Record<string, unknown> = {}) => ({ spendWin: Object.assign({
  tz: TZ, today: { routes: today, tok: { tok: 60_300_000 } },
  monthToDate: { routes: month, tok: { tok: 1_670_000_000, tokCacheR: 1_530_000_000, tokCacheW: 100_000_000, tokIn: 20_000_000, tokOut: 20_000_000 } },
  at: 1_700_000_000, billing: [] }, over) });
const SEAT_ACCTS = [{ name: "Enterprise seat", kind: "seat", routes: [{ host: "TESTHOST", route: "machine", est: true }, { host: "PEERHOST", route: "login:0a0b0c0d0e0f", est: true }] },
  { name: "API key", kind: "key", routes: [{ host: "TESTHOST", route: "key", est: false }, { host: "PEERHOST", route: "key", est: false }] }];
function sets(R: ReturnType<typeof rail>, rows: Array<[string, unknown]>) {
  return rows.map(([host, u]) => { const det: Record<string, unknown> = {}; R.costDet(u, det); return { host, det }; });
}
const TWO = (): Array<[string, unknown]> => [["TESTHOST", usage({ machine: 117.15 }, { machine: 117.15, key: 3374.32 })],
  ["PEERHOST", usage({ "login:0a0b0c0d0e0f": 65.44 }, { "login:0a0b0c0d0e0f": 65.44, key: 1497.88 })]];

test("the cell sums every route at API prices and marks a figure that holds a seat's estimate; no 'API' label", () => {
  const R = rail(SEAT_ACCTS);
  const cell = R.costCellHTML(sets(R, TWO()));
  assert.equal(text(cell), "today ≈$183 this month ≈$5,055", "both hosts, every route, thousands grouped");
  assert.ok(cell.includes("data-win=today") && cell.includes("data-win=monthToDate"), "each figure says which window it is, for the click");
  assert.ok(!/>API</.test(cell), "no 'API' label: the figures are mostly a seat's");
  assert.ok(!cell.includes("ah-slot"), "the API-health dot is not in the spend cell");
  const K = rail([]);
  assert.equal(text(K.costCellHTML(sets(K, [["TESTHOST", usage({ key: 14 }, { key: 1320.4 })]]))), "today $14 this month $1,320", "a key's charge is plain");
  assert.equal(K.costCellHTML(sets(K, [["TESTHOST", {}]])), "", "no ledger: no cell");
  assert.equal(text(K.costCellHTML(sets(K, [["TESTHOST", usage({}, {})]]))), "today $0 this month $0", "a ledger with nothing in the windows: an honest zero");
});

test("the hover splits each figure by account, the key's row says charged, and the rows add up to the Total", () => {
  const R = rail(SEAT_ACCTS);
  const t = text(R.costTipHTML(sets(R, TWO())));
  assert.match(t, /^Usage at API prices today this month Enterprise seat ≈\$183 ≈\$183 API key charged \$0 \$4,872 Total ≈\$183 ≈\$5,055 tokens 120\.6M 3\.34B/,
    "one seat reached two ways is ONE row (the kernel's fold); the rows add up to the cell");
  assert.match(t, /≈ marks romp’s estimate: the tokens priced at the API’s list prices, not what the account is charged\./);
  assert.ok(!t.includes("not billed per token"), "no claim about how a seat is billed (it may be metered against a spend limit)");
  assert.match(t, /by machine this month: TESTHOST ≈\$3,492 · PEERHOST ≈\$1,563/, "every machine in the figures, by name, adding up to the month: 3,492 + 1,563 = 5,055");
  assert.match(t, /this month: 3\.06B cache read · 200\.0M cache write · 40\.0M in · 40\.0M out/, "the month's tokens by kind");
  assert.match(t, /last turn recorded just now/);
  assert.ok(!t.includes(" in " + TZ), "the windows are this browser's own: no zone named");
});

test("whole dollars that add up: a column of rows rounds to its Total, never a dollar off (lrRound)", () => {
  const R = rail([]);
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
  for (const vals of [[100.6, 200.6], [0.4, 0.4, 0.4], [3491.47, 1563.32], [0.5, 0.5], [12.2, 7.9, 0.4]]) {
    const r = R.lrRound(vals);
    assert.equal(sum(r), Math.round(sum(vals)), "the rows add up to the total's own rounding: " + JSON.stringify([vals, r]));
    r.forEach((v: number, i: number) => assert.ok(Math.abs(v - vals[i]) < 1, "each row within a dollar of its own figure"));
  }
  assert.equal(sum(R.lrRound([100.6, 200.6])), 301, "301.2 shows as 301; rounding each row alone would show 101 + 201 = 302");
  const t = text(R.costTipHTML(sets(R, [["TESTHOST", usage({ machine: 100.6, key: 200.6 }, { machine: 100.6, key: 200.6 })]])));
  const m = /API key charged \$(\d+) \$\d+ other ≈\$(\d+) ≈\$\d+ Total ≈\$301 ≈\$301/.exec(t);
  assert.ok(m && +m[1] + +m[2] === 301, "the table's rows add up to its Total: " + t);
});

test("spend the kernel has not folded into an account yet is still counted: the key's joins API key, a seat's an 'other' row", () => {
  const R = rail([]);
  const rows = R.costRows(sets(R, [["TESTHOST", usage({ machine: 10 }, { machine: 10, key: 90 })]]));
  assert.deepEqual(rows.map((r: any) => [r.name, Math.round(r.v.monthToDate.usd)]), [["API key", 90], ["other", 10]]);
});

test("a machine whose usage is not in the figures is named on the cell itself and, in the hover, with why and since when", () => {
  const lastRead = Math.floor(new Date(2026, 9, 8, 1, 8).getTime() / 1000);
  const R = rail([SEAT_ACCTS[0]], [{ host: "spare", why: "not reachable", at: lastRead }]);
  const live = sets(R, [["TESTHOST", usage({ machine: 117.15 }, { machine: 400 })]]);
  assert.equal(text(R.costCellHTML(live)), "today ≈$117 this month ≈$400 spare missing", "the figures, and the machine they leave out");
  const t = text(R.costTipHTML(live));
  assert.match(t, /spare: not reachable, last read (01:08|Oct 8, 01:08); its usage is not in these figures/, t);
  assert.ok(!t.includes("Total"), "one account: the row is the total");
  // two machines left out: counted on the cell, each named in the hover
  const R2 = rail([], [{ host: "spare", why: "not reachable", at: null }, { host: "OLDHOST", why: "older build: it sends no hourly usage", at: null }]);
  const live2 = sets(R2, [["TESTHOST", usage({ key: 1 }, { key: 2 })]]);
  assert.equal(text(R2.costCellHTML(live2)), "today $1 this month $2 2 machines incomplete");
  assert.match(text(R2.costTipHTML(live2)), /OLDHOST: older build: it sends no hourly usage; its usage is not in these figures/);
});

test("a window that begins before a machine's hourly record is marked partial, with where the record begins", () => {
  const from = Math.floor(new Date(2026, 9, 3, 14, 0).getTime() / 1000);
  const R = rail([]);
  const live = sets(R, [["TESTHOST", usage({ key: 5 }, { key: 50 }, { monthToDate: { routes: { key: 50 }, tok: { tok: 0 }, from } })]]);
  assert.equal(text(R.costCellHTML(live)), "today $5 this month $50 TESTHOST partial");
  assert.match(text(R.costTipHTML(live)), /TESTHOST: counted from Oct 3, 14:00, where its hourly record begins; earlier usage is not in these figures/);
});

test("windows a kernel could not build in this browser's zone say whose they are", () => {
  const R = rail([]);
  const t = text(R.costTipHTML(sets(R, [["TESTHOST", usage({ key: 1 }, { key: 2 }, { tz: TZ === "Asia/Kolkata" ? "UTC" : "Asia/Kolkata" })]])));
  assert.match(t, /^Usage at API prices in (Asia\/Kolkata|UTC) /, t);
});

// ── the panel: two cards, two charts, one list ───────────────────────────────────────────────────────────────────────
const PANEL = ["var SP_RANGE_BUCKETS=", "function spSeries(d){", "function spTail(ser,keep){", "function spKey(d,s){", "function spSumArr(a){",
  "function spRound4(v){", "var SP_GRAIN=", "function spHostScope(d,host){", "function spKeyArr(d,st,meas){", "function spPart(d,st,sec,meas){",
  "function spUsd(v,est){", "function spRouteSums(d){", "function spAccountRows(d){", "function spAcctName(d,s){", "function spRangeView(d){",
  "function spRangeWords(ser){", "function spNothing(ser){", "function spWinFigs(d){", "function spendLimitResets(m,now){", "function spendLimitWords(m){", "function spAllowHTML(){",
  "function spCards(d){", "function spCardHTML(d,c,sec){", "function spHosts(d){",
  "function spMany(d){", "function spName(s){", "function spColor(s){", "var SP_OTHER=", "function spTitle(s,many){", "function spTagChip(s){",
  "function spApplyViewOrder(seed,view){", "function spOrdered(d){", "function spRows(d){", "function spDashedHosts(d){",
  "function sessionTable(d){"].map(fn).join("\n");
function panel(data: unknown, last: unknown[] = [], range = "hours") {
  const SP: Record<string, unknown> = { data, range, measure: "usd", order: "spend", merge: false };
  const fmtUsd = (v: number) => "$" + Math.round(v).toLocaleString("en-US");
  const WINS = [["fiveHour", 5 * 3600, "5 hours"], ["sevenDay", 7 * 86400, "7 days"]];
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("SP", "LAST", "WINS", "esc", "fmtUsd", "fmtTok", "spViewOrder", PANEL + "\n; return {SP, spCards, spCardHTML, spRangeView, sessionTable, spPart, spendLimitResets};")(
    SP, last, WINS, esc, fmtUsd, fmtTok, () => []);
}
const H = 192, arr = <T,>(n: number, f: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => f(i));
/** two machines on scope "all" (every turn, the key's part beside it): web billed the key until the last day and the seat
 *  since; api bills the seat through a stored login on PEERHOST. `today` is the last day's hours as the viewer's own (the
 *  kernel's regrouping), and `wins` the cell's figures for today and this month */
function detail(over: Record<string, unknown> = {}): Record<string, any> {
  const hk = arr(H, (i) => "2026-10-0" + (1 + Math.floor(i / 24)) + "T" + String(i % 24).padStart(2, "0"));
  const recent = (i: number) => i >= H - 24;
  const stacks = [
    { kind: "sid", host: "TESTHOST", sid: "s-web", name: "web", bg: "#1EA1EB", live: true, usd: arr(H, () => 2), tok: arr(H, () => 100), turns: arr(H, () => 1),
      keyUsd: arr(H, (i) => (recent(i) ? 0 : 2)), keyTok: arr(H, (i) => (recent(i) ? 0 : 100)) },
    { kind: "sid", host: "PEERHOST", sid: "s-api", name: "api", bg: "#54B204", live: true, usd: arr(H, () => 1), tok: arr(H, () => 50), turns: arr(H, () => 1),
      keyUsd: arr(H, () => 0), keyTok: arr(H, () => 0) },
  ];
  const routes = [
    { host: "TESTHOST", route: "key", usd: arr(H, (i) => (recent(i) ? 0 : 2)), turns: arr(H, (i) => (recent(i) ? 0 : 1)) },
    { host: "TESTHOST", route: "machine", usd: arr(H, (i) => (recent(i) ? 2 : 0)), turns: arr(H, (i) => (recent(i) ? 1 : 0)) },
    { host: "PEERHOST", route: "login:0a0b0c0d0e0f", usd: arr(H, () => 1), turns: arr(H, () => 1) },
  ];
  const hours = { keys: hk, epochs: hk.map((_, i) => 1000 + i), stacks, routes };
  const tail = (x: any) => Object.assign({}, x, { usd: x.usd.slice(-24), tok: x.tok.slice(-24), turns: x.turns && x.turns.slice(-24), keyUsd: x.keyUsd && x.keyUsd.slice(-24), keyTok: x.keyTok && x.keyTok.slice(-24) });
  const today = { keys: hk.slice(-24), stacks: stacks.map(tail), routes: routes.map((r) => Object.assign({}, r, { usd: r.usd.slice(-24), turns: r.turns.slice(-24) })) };
  return Object.assign({ host: "TESTHOST", scope: "all", order: [], tags: [], unattributed: { usd: 0, tok: 0, turns: 0 }, hours, days: { keys: [], stacks: [], routes: [] }, today,
    hosts: [{ host: "TESTHOST", status: "ok", scope: "all" }, { host: "PEERHOST", status: "ok", scope: "all" }],
    sessions: [{ sid: "s-web", host: "TESTHOST", name: "web", live: true }, { sid: "s-api", host: "PEERHOST", name: "api", live: true }],
    wins: [{ host: "TESTHOST", today: { routes: { machine: 48.4 } }, monthToDate: { routes: { machine: 48.4, key: 288.6 } } },
      { host: "PEERHOST", today: { routes: { "login:0a0b0c0d0e0f": 24.4 } }, monthToDate: { routes: { "login:0a0b0c0d0e0f": 168.4 } } }],
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
  const seat = text(P.spCardHTML(d, c, "seat")), key = text(P.spCardHTML(d, c, "key"));
  assert.match(seat, /^Enterprise seat ≈ romp’s estimate: the tokens priced at the API’s list prices · last 7 days/);
  assert.ok(!seat.includes("not billed per token"), "no claim about how the seat is billed");
  assert.match(key, /^API key Charged per token at the API’s prices · last 7 days/);
  assert.ok(P.spCardHTML(d, c, "seat").includes('data-tip="dev@example.com · Notes Co · enterprise"'), "the identity rides the hover words, off the card");
});

test("today and this month: the cards show the cell's own figures (every machine's window), the charts the same turns", () => {
  const d = detail(), P = panel(d, [], "month");
  const c = P.spCards(d);
  assert.equal(Math.round(c.seat.usd * 10) / 10, 48.4 + 168.4, "the seat: every machine's month, as the cell sums it");
  assert.equal(Math.round(c.key.usd * 10) / 10, 288.6);
  const T = panel(d, [], "today");
  const ct = T.spCards(d);
  assert.equal(Math.round(ct.seat.usd * 10) / 10, 48.4 + 24.4, "today's");
  assert.equal(ct.key.usd, 0);
  assert.match(text(T.spCardHTML(d, ct, "key")), /Nothing today\.$/, "the key's card says the range holds nothing, in the range's own words");
  const partial = detail({ wins: [{ host: "TESTHOST", monthToDate: { routes: { key: 5 }, from: 1_790_000_000 } }] });
  assert.deepEqual(panel(partial, [], "month").spCards(partial).from, [{ host: "TESTHOST", at: 1_790_000_000 }], "a machine whose hours start later is carried for the note");
});

test("a card whose kind holds nothing in the range is one quiet line, and a kind no account has is not drawn", () => {
  const d = detail(), P = panel(d, [], "today");
  const c = P.spCards(d);
  assert.ok(c.key.show && !c.key.live, "an API key exists in the ledger's reach, so its card shows, empty");
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

// the reading carries no reset date, so the browser dates it as Claude Code's /usage screen does: the first of the next
// calendar month in the viewer's zone, and only for a plan with a limit to reset
const resetsWords = (d = new Date()) => "resets " + new Date(d.getFullYear(), d.getMonth() + 1, 1).toLocaleDateString("en-US", { month: "short", day: "numeric" });
test("the spend limit resets on the first of the next month in the viewer's zone, the date Claude Code's /usage gives", () => {
  const P = panel(detail());
  const lim = { used: 17000, limit: 50000, pct: 34 };
  assert.equal(P.spendLimitResets(lim, new Date(2026, 9, 8, 14)), "resets Nov 1");
  assert.equal(P.spendLimitResets(lim, new Date(2026, 11, 31, 23, 59)), "resets Jan 1", "December rolls into the new year");
  assert.equal(P.spendLimitResets(lim, new Date(2026, 1, 1, 0, 0)), "resets Mar 1");
  assert.equal(P.spendLimitResets({ used: 812, limit: null, pct: null }, new Date(2026, 9, 8)), "", "no limit: nothing to reset, as on Claude Code's screen");
  assert.equal(P.spendLimitResets({ stale: true }, new Date(2026, 9, 8)), "", "a stale reading carries no figures to reset");
});

test("the seat card carries the account's monthly spend limit in Claude Code's words, attributed, with its reset date", () => {
  const d = detail();
  const last = [{ host: "TESTHOST", acct: "acct1", det: { _acct: "Enterprise seat", _monthly: { pct: 34, col: "#5196B8", used: 17000, limit: 50000, cur: "USD" } } }];
  const P = panel(d, last);
  const h = P.spCardHTML(d, P.spCards(d), "seat"), t = text(h);
  assert.ok(t.includes("monthly spend limit: $17,000 of $50,000 spent this month 34% " + resetsWords() + " · as Claude Code reads it"), t);
  assert.ok(h.includes("<span class=rsp-allow-src>" + resetsWords() + " \u00b7 as Claude Code reads it</span>"), "the reset and the attribution sit on their own line");
  const stale = panel(d, [{ host: "TESTHOST", acct: "acct1", det: { _monthly: { stale: true } } }]);
  assert.match(text(stale.spCardHTML(d, stale.spCards(d), "seat")), /monthly spend limit: no reading this month yet/);
  const unlimited = panel(d, [{ host: "TESTHOST", acct: "acct1", det: { _monthly: { pct: null, used: 1234.5, limit: null, cur: "USD" } } }]);
  const ut = text(unlimited.spCardHTML(d, unlimited.spCards(d), "seat"));
  assert.match(ut, /monthly spend limit: \$1,235 spent this month \(no limit\) as Claude Code reads it/);
  assert.ok(!/resets/.test(ut), "no limit: no reset date, as on Claude Code's screen");
});

test("the API-health dot lives apart from the spend figures, named, its keyboard focus the accent ring", () => {
  const acts = KERNEL.indexOf('"<div class=rail-acts>"'), dot = KERNEL.indexOf('"<div id=rail-api ');
  assert.ok(acts > 0 && dot > acts, "the cell sits inside the right-hand cluster, after its opening");
  assert.ok(KERNEL.includes('"<i class=ah-dot></i><span class=ru-name>API health</span></div>"'), "the dot and its words");
  assert.ok(!USAGE_JS.includes("ah-slot") && !USAGE_JS.includes("moveApiCell"), "the readout no longer adopts the dot");
  assert.ok(KERNEL.includes("How Anthropic\\u2019s API is answering your sessions, whichever account they bill."), "its hover says what it reports");
  assert.ok(KERNEL.includes("body.theme-light #rail-api[data-dot=fine] .ah-dot,body.theme-light .ah-dot[data-dot=fine]{background:#356890}"));
  assert.ok(KERNEL.includes('"#rail-api:focus-visible{outline:1px solid var(--accent,#9cd2ff);outline-offset:-1px}"'), "focus looks like focus, never like hover");
  assert.ok(!KERNEL.includes("#rail-api:focus-visible{background"), "the hover's wash is the hover's alone");
  assert.ok(KERNEL.includes('aria-label=\\"Usage at API prices\\"'), "the panel's dialog is named what it shows");
  assert.ok(!KERNEL.includes('aria-label=\\"API spend\\"'));
});

// ── the charts' x axes (2026-10-08 review: single weekday initials read "F S S M T W T") ─────────────────────────────
test("every range's x labels are unambiguous: the week's days by weekday and date, the day's hours, the 90 days' dates", () => {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const { spXTicks } = new Function(["var SP_DOW=", "function spDayOf(k){", "function spXTicks(keys,range){"].map(fn).join("\n") + "\n; return {spXTicks};")();
  const pad = (v: number) => String(v).padStart(2, "0");
  // 168 hourly keys ending Thu 2026-10-08 15:00: from Thu Oct 1 16:00
  const hours: string[] = [];
  for (let i = 0; i < 168; i++) { const t = new Date(2026, 9, 1, 16 + i); hours.push(`${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}T${pad(t.getHours())}`); }
  const week = spXTicks(hours, "hours");
  assert.deepEqual(week.map((t: any) => t.label), ["Thu 1", "Fri 2", "Sat 3", "Sun 4", "Mon 5", "Tue 6", "Wed 7", "Thu 8"],
    "eight calendar days, no label repeated: a weekday alone would say Thu twice");
  assert.equal(week[0].at, 4, "centred under the first day's eight hours");
  assert.equal(week[1].at, 8 + 12, "a whole day: under its noon");
  // an edge day with under six hours in range is too narrow to name
  assert.deepEqual(spXTicks(hours.slice(4), "hours").map((t: any) => t.label)[0], "Fri 2", "four hours of Thu 1 left: unnamed");
  const day = spXTicks(hours.slice(-24), "day");
  assert.deepEqual(day.map((t: any) => t.label), ["18:00", "Thu 8", "06:00", "12:00"], "the hours, and the new day named at its midnight");
  const days: string[] = [];
  for (let i = 0; i < 90; i++) { const t = new Date(2026, 6, 11 + i); days.push(`${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`); }
  assert.deepEqual(spXTicks(days, "days").map((t: any) => t.label), ["Jul 15", "Aug 1", "Aug 15", "Sep 1", "Sep 15", "Oct 1"],
    "the 1st and 15th with the month's name, never a 10/1 that reads two ways");
  const month = days.filter((k) => k >= "2026-10-01").concat(Array.from({ length: 20 }, (_, i) => `2026-10-${pad(11 + i)}`));
  assert.deepEqual(spXTicks(month, "month").map((t: any) => t.label), ["Oct 1", "Oct 8", "Oct 15", "Oct 22", "Oct 29"], "this month by day: weekly dates");
  assert.deepEqual(spXTicks(hours.slice(-16), "today").map((t: any) => t.label), ["Thu 8", "06:00", "12:00"], "today by hour: the day at its midnight, then the hours");
});

test("both charts keep a gutter for their y labels: bars start right of it, labels end inside it", () => {
  assert.ok(USAGE_JS.includes("var GUT=SP_GUT,top=niceTop(mx),slot=(W-GUT)/n,"), "the plot's buckets share the width right of the gutter");
  assert.ok(USAGE_JS.includes("for(var i=0;i<n;i++){if(!(tots[i]>0))continue;var x=GUT+i*slot+gap/2,segs=[];"), "every bar starts past it");
  assert.ok(USAGE_JS.includes("ylab+='<span class=ru-tip-gy style=\"top:'+(gy-4).toFixed(0)+'px;width:'+(GUT-5)+'px\">'+al+'</span>'"), "each label is sized to end inside it");
  assert.ok(USAGE_JS.includes("i=spBucketAt(e.clientX-r.left-GUT*k,r.width-GUT*k,n)"), "the crosshair reads buckets from the plot's own edge");
  assert.ok(KERNEL.includes(".rsp-chart .ru-tip-gy{left:0;text-align:right;transform:none}"), "the labels right-aligned against the plot");
});
