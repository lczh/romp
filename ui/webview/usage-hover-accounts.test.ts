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

const SRC = ["function shareFreshest(live){", "function barRows(d){", "function winsHTML(d){", "function machineHTML(name){",
  "function acctBlocksHTML(sets,manyMachines){", "function acctLineHTML(accts,miss){"].map(fn).join("\n");
const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as Record<string, string>)[c]);
const fmtUsd = (v: number) => "$" + String(Math.round(v));
const fmtAgo = (ep: number) => Math.round((Date.now() / 1000 - ep) / 86400) + "d ago";
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const F = new Function("esc", "fmtUsd", "fmtAgo", SRC + "\n; return {shareFreshest, acctBlocksHTML, acctLineHTML};")(esc, fmtUsd, fmtAgo);

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
  assert.ok(USAGE_JS.includes("['fiveHour','sevenDay','fable','t','limited','acctName','noWindows'].forEach"),
    "the freshest member's account fields are shared across the group");
});

test("the by-account line splits the week between the key and the seat, the seat's dollars marked as an estimate", () => {
  const h = F.acctLineHTML([{ name: "Enterprise seat", kind: "seat", usd: 10.2, est: true }, { name: "API key", kind: "key", usd: 15, est: false }], ["TESTHOSTC"]);
  assert.ok(h.includes("by account · 1 week"));
  assert.ok(h.includes("Enterprise seat ≈$10"), "the seat wears the approx mark");
  assert.ok(h.includes("API key $15") && !h.includes("API key ≈"), "the key's dollars stay plain");
  assert.ok(h.includes("TESTHOSTC not counted (older build)"), "a machine the fold cannot read is counted out by name");
  assert.equal(F.acctLineHTML([{ name: "API key", kind: "key", usd: 15, est: false }], []), "", "a key alone is the rows above already");
  assert.equal(F.acctLineHTML([], []), "");
});

test("the line rides the hover's spend section, from the fleet route's fold", () => {
  assert.ok(USAGE_JS.includes("h+=acctLineHTML(ACCTS,ACCTS_MISS);"));
  assert.ok(USAGE_JS.includes("ACCTS=(d&&Array.isArray(d.accounts))?d.accounts:[];"));
});
