// The usage hover over the bottom bar's API cell names accounts the way the spend detail does and stops presenting
// readings that are not there (the user 2026-10-01). Its head was the signed-in login's email; three window bars from a
// reading weeks old sat under it as the account's allowance, with "lagging" beside one of two machines both that old;
// and its spend figures summed a subscription seat's estimate into the key's dollars, unmarked. Now: the account block is
// headed by the account's kind ("Enterprise seat"); an account that answered it has no usage windows says so in one line,
// with no bars, no age and no lagging note, even beside a machine still holding an old reading; and one by-account line
// splits the week between the key and each seat, the seat's dollars wearing the approx mark. The kernel half (the
// recorder, the payload, the fold) executes in tests/test_sdk_backend.py and tests/test_usage_per_account.py. The landing
// page has no jsdom harness: the pure functions are lifted from the kernel's inline JS and EXECUTED on synthetic rows.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const USAGE_JS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
const LINES = USAGE_JS.split("\n");

/** the function starting at the line that begins with `start`, through the line where its braces close (the
 *  spend-accounts.test.ts idiom; a block may carry no backslash escape but \uXXXX, which reads the same in Python) */
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

const SRC = ["function shareFreshest(live){", "function barRows(d){", "function winsHTML(d){", "function spendLimitResets(m,now){", "function spendLimitWords(m){", "function monthlyHTML(d){",
  "function machineHTML(name){", "function acctBlocksHTML(sets,manyMachines){"].map(fn).join("\n");
const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as Record<string, string>)[c]);
const fmtUsd = (v: number) => "$" + String(Math.round(v));
const fmtAgo = (ep: number) => Math.round((Date.now() / 1000 - ep) / 86400) + "d ago";
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const F = new Function("esc", "fmtUsd", "fmtAgo", SRC + "\n; return {shareFreshest, acctBlocksHTML};")(esc, fmtUsd, fmtAgo);

const NOW = Math.floor(Date.now() / 1000), DAY = 86400, ACCT = "a1".repeat(6);
const old = (pct: number) => ({ pct, resetsAt: NOW - 25 * DAY, color: [84, 178, 4] });

/** TESTHOSTA answered just now that its account has no windows; TESTHOSTB, on the same account and an older build, still
 *  holds a reading from weeks ago with the login's email beside it */
function rows(): any[] {
  return [
    { host: "", acct: ACCT, usage: { noWindows: true, t: NOW - 60, acct: ACCT, acctName: "Enterprise seat" } },
    { host: "TESTHOSTB", acct: ACCT, usage: { fiveHour: old(71), sevenDay: old(16), fable: old(27), t: NOW - 26 * DAY, acct: ACCT,
      acctLabel: "user@example.test", spend: { week: { usd: 1, tok: 1, turns: 1 } } } },
  ];
}

/** the LAST entry renderRows builds, with winDet's two account fields (the bars are absent here by construction) */
function sets(rs: any[]): any[] {
  return rs.map((r) => {
    const det: Record<string, unknown> = {};
    if (r.usage.acctName) det._acct = r.usage.acctName;
    if (r.usage.noWindows) det._noWin = true;
    for (const w of ["fiveHour", "sevenDay", "fable"]) if (r.usage[w]) det[w] = { name: w, pct: r.usage[w].pct, col: "#5196B8", tp: null, unk: true, ago: "25d ago", reset: null };
    return { host: r.host || "TESTHOSTA", det, acct: r.usage.acct || "", ownT: r.ownT, lag: !!r.lag };
  });
}

test("a machine's old reading yields to the account's answer that it has no windows, and is not called lagging", () => {
  const rs = rows();
  F.shareFreshest(rs);
  const b = rs[1].usage;
  assert.equal(b.fiveHour, undefined, "the old bars are not the account's");
  assert.equal(b.noWindows, true);
  assert.equal(b.acctName, "Enterprise seat", "the account's name travels with the answer");
  assert.equal(b.acctLabel, "user@example.test", "(an older build's own field stays on its row: nothing renders it)");
  assert.ok(b.spend, "each machine keeps its own spend");
  assert.equal(rs[1].lag, false, "beside an account with no windows there is nothing to lag behind");
});

test("the block is headed by the account's kind and says it has no windows, with no age and no email", () => {
  const rs = rows();
  F.shareFreshest(rs);
  const h = F.acctBlocksHTML(sets(rs), true).join("");
  assert.ok(h.includes("<div class=ru-tip-acct>Enterprise seat</div>"), "the spend detail's name for the account");
  assert.ok(h.includes("no 5-hour or weekly limits on this account"));
  assert.ok(!h.includes("ru-tip-win"), "no bars");
  assert.ok(!h.includes("lagging") && !h.includes("updated"), "no age: nothing is read");
  assert.ok(h.includes("TESTHOSTA") && h.includes("TESTHOSTB"), "the machines signed into it are named");
  assert.ok(!h.includes("@"), "no email");
});

test("the head reads the payload's account name, never the email", () => {
  assert.ok(USAGE_JS.includes("if(u.acctName)det._acct=u.acctName;"));
  assert.ok(!USAGE_JS.includes("det._acct=u.acctLabel"));
  assert.ok(USAGE_JS.includes("['fiveHour','sevenDay','fable','t','limited','acctName','noWindows','monthly'].forEach"),
    "the freshest member's account fields are shared across the group");
});

test("the hover's usage section is the accounts split by window, from the /usage/fleet route's fold (2026-10-08)", () => {
  // the one-line by-account summary of the rolling week became the usage section's table, an account per row and the
  // cell's two windows as columns; its behaviour executes in usage-at-api-prices.test.ts
  assert.ok(!USAGE_JS.includes("function acctLineHTML("), "the one-line week summary is gone");
  assert.ok(USAGE_JS.includes("(ACCTS||[]).forEach(function(a){var r={name:a.name,kind:a.kind,v:costZero()};"), "the rows are the kernel's accounts");
  assert.ok(USAGE_JS.includes("ACCTS=(d&&Array.isArray(d.accounts))?d.accounts:[];"));
});

// THE ACCOUNT'S MONTHLY SPEND LIMIT (2026-10-01; reworded 2026-10-08): an account with no windows whose answer carried its
// monthly spend limit draws that limit as its one bar, with Claude Code's own words for it ("$X of $Y spent") attributed to
// Claude Code, never as this member's usage, and the reset dated as Claude Code's /usage screen dates it (the reading
// carries none: the first of the next month in the viewer's zone, only when there is a limit). Here the payload goes through
// the REAL winDet (lifted with a document stub), so the field mapping executes too. The kernel half (the recorder, minor
// units to dollars, staleness by month) executes in tests/test_usage_per_account.py.
const DOC = { body: { classList: { contains: () => false } } };   // the classic palette: the reading's own colour
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const G = new Function("esc", "fmtUsd", "fmtAgo", "fmtR", "document", "WINS",
  SRC + "\n" + ["function segCol(seg,pct){", "function fmtDay(s){", "function winDet(u,det){"].map(fn).join("\n")
  + "\n; return {winDet, acctBlocksHTML};")(esc, fmtUsd, fmtAgo, () => "1d", DOC, [["fiveHour", 5 * 3600, "5 hours"]]);
const SEAT = { noWindows: true, t: NOW - 60, acct: ACCT, acctName: "Enterprise seat" };
function seatBlock(usage: any): string {
  const det: Record<string, unknown> = {};
  G.winDet(usage, det);
  return G.acctBlocksHTML([{ host: "TESTHOSTA", det, acct: ACCT, ownT: usage.t, lag: false }], false).join("");
}

const resetsWords = (d = new Date()) => "resets " + new Date(d.getFullYear(), d.getMonth() + 1, 1).toLocaleDateString("en-US", { month: "short", day: "numeric" });
test("an account with no windows and a monthly spend limit shows one bar, Claude Code's own figures and the reset", () => {
  const h = seatBlock({ ...SEAT, monthly: { pct: 23, used: 11500, limit: 50000, currency: "USD", month: "2026-10", color: [84, 178, 4], tone: [81, 150, 184] } });
  assert.ok(h.includes("<div class=ru-tip-acct>Enterprise seat</div>"), "the account's kind names the block");
  assert.ok(h.includes("<span>Monthly spend limit</span><span class=ru-tip-reset>" + resetsWords() + "</span>"), "the reset date in the heading");
  assert.equal((h.match(/class=ru-tip-track/g) || []).length, 1, "one bar");
  assert.ok(h.includes("width:23%;background:rgb(84,178,4)") && h.includes("<span class=ru-tip-v>23%</span>"), "the percent used");
  assert.ok(h.includes("$11,500 of $50,000 spent this month, as Claude Code reads it"), "the figures in Claude Code's words, attributed");
  assert.ok(!/allowance/.test(h), "not called an allowance of this member's");
  assert.ok(!h.includes("no 5-hour or weekly limits"), "the bar takes the one-line note's place");
  assert.ok(!h.includes("updated"), "no age line");
});

test("a reading from an earlier month draws no bar and says so; a plan with no limit says what it spent", () => {
  const h = seatBlock({ ...SEAT, monthly: { stale: true, month: "2026-09" } });
  assert.ok(h.includes("<span>Monthly spend limit</span><span class=ru-tip-reset>no reading this month yet</span>"));
  assert.ok(!h.includes("ru-tip-track") && !h.includes("%"), "no bar and no figure");
  const u = seatBlock({ ...SEAT, monthly: { pct: null, used: 812.4, limit: null, currency: "USD", month: "2026-10" } });
  assert.ok(u.includes("$812 spent this month (no limit), as Claude Code reads it") && !u.includes("ru-tip-track"), "no limit: no bar, the spend in words");
  assert.ok(u.includes("<span class=ru-tip-reset>this month</span>") && !/resets/.test(u), "no limit: no reset date, as on Claude Code's screen");
});

test("no monthly fields: nothing about a limit, the one-line note as before", () => {
  const h = seatBlock(SEAT);
  assert.ok(!h.includes("spend limit") && !h.includes("ru-tip-track"));
  assert.ok(h.includes("no 5-hour or weekly limits on this account"));
  const w = seatBlock({ ...SEAT, noWindows: undefined, monthly: { pct: 23, used: 1, limit: 2, color: [84, 178, 4] },
    fiveHour: { pct: 40, resetsAt: NOW + 3600, color: [84, 178, 4] } });
  assert.ok(w.includes("5 hours") && !w.includes("spend limit"), "an account with windows draws its windows, never the limit");
});

test("the hover's bar track keeps a visible wash on the light card", () => {
  assert.ok(KERNEL.includes('"body.theme-light .ru-track,body.theme-light .ru-tip-track{background:rgba(0,0,0,0.10)}"'),
    "the dark theme's white wash vanished on the white card, leaving the bar with no scale");
});
