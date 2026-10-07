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
  "function spKeyedHosts(d){", "function accountsHTML(d", "function spPaintAccts(){", "function spRangeView(d){",
  "function spRangeWords(ser){"].map(fn).join("\n");

/** the lifted functions over a fresh SP state; `document` is a stand-in for spPaintAccts (no jsdom on the landing page) */
function lift(range: string, document?: unknown) {
  const SP: Record<string, unknown> = { range, measure: "usd", order: "spend", merge: false, acctsOpen: false };
  const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as Record<string, string>)[c]);
  const fmtUsd = (v: number) => "$" + String(Math.round(v));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("SP", "esc", "fmtUsd", "document", SRC + "\n; return {SP, spAccountRows, accountsHTML, spPaintAccts, spRangeView, spUsd, spAcctName, SP_GRAIN, SP_APPROX, SP_EST_TIP};")(SP, esc, fmtUsd, document);
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

test("a keyed host's other turns are named under the summary: counted here, left out of the chart, the list and Totals", () => {
  // the #74 review's keyed-host finding: the summary sums every route whatever the host's billing rule (on purpose: it
  // answers who billed what), while a keyed host's chart, list and Totals count key-billed turns only. Decided per host,
  // as spSeatPart decides: the fixture's TESTHOST is keyed and its sign-in feeds the seat, PEERHOST counts every turn
  const { accountsHTML } = lift("day");
  /** each keyed-host note under the summary, as the hosts it names and its pronoun (the words between are the copy's) */
  const keyed = (h: string) => (h.match(/<div class=rsp-note>[^<]*key-billed[^<]*<\/div>/g) || []).map((n) => {
    const t = n.replace(/<[^>]*>/g, "");
    return [t.slice(0, t.indexOf(": ")), (/\b(its|their)\b/.exec(t) || [""])[0]];
  });
  assert.deepEqual(keyed(accountsHTML(payload())), [["TESTHOST", "its"]]);
  assert.deepEqual(keyed(accountsHTML(payload(), "open")), [["TESTHOST", "its"]], "the opened fold says it too");
  for (const scope of ["total", "computed"]) {
    const p = payload(); p.hosts[0].scope = scope;
    assert.deepEqual(keyed(accountsHTML(p)), [], "a " + scope + " host's chart and list count every turn: nothing to say");
  }
  const keyOnly = payload(); keyOnly.hours.routes = keyOnly.hours.routes.filter((r: any) => r.host !== "TESTHOST" || r.route === "key");
  assert.deepEqual(keyed(accountsHTML(keyOnly)), [], "a keyed host with only key spend in the range leaves nothing out");
  // either measure alone names the host, since the kernel files a sign-in route on either (_bucket_routes: dollars at the
  // grain or more, or any turns)
  const signin = (usd: number, turns: number) => { const p = payload(); p.hours.routes = p.hours.routes.map((r: any) => (r.host === "TESTHOST" && r.route === "machine"
    ? { ...r, usd: arr(H, () => usd), turns: arr(H, () => turns) } : r)); return p; };
  assert.deepEqual(keyed(accountsHTML(signin(0, 2))), [["TESTHOST", "its"]], "turns with no dollars");
  assert.deepEqual(keyed(accountsHTML(signin(3, 0))), [["TESTHOST", "its"]], "dollars with no turns");
  const oneTurn = payload();
  oneTurn.hours.routes = oneTurn.hours.routes.map((r: any) => (r.host === "TESTHOST" && r.route === "machine"
    ? { ...r, usd: arr(H, () => 0), turns: arr(H, (i) => (i === H - 1 ? 1 : 0)) } : r));
  assert.deepEqual(keyed(accountsHTML(oneTurn)), [["TESTHOST", "its"]], "one turn with no dollars");
  // the range decides: TESTHOST's sign-in spends only in the oldest 30 of the 192 hours, 6 of which fall inside the 7-day
  // range (its last 168 hours) and none inside the day's
  const early = payload();
  early.hours.routes = early.hours.routes.map((r: any) => (r.host === "TESTHOST" && r.route === "machine"
    ? { ...r, usd: arr(H, (i) => (i < 30 ? 3 : 0)), turns: arr(H, (i) => (i < 30 ? 2 : 0)) } : r));
  assert.deepEqual(keyed(lift("day").accountsHTML(early)), [], "in the last 24 hours only its key spent: nothing left out");
  assert.deepEqual(keyed(lift("hours").accountsHTML(early)), [["TESTHOST", "its"]], "the 7-day range holds its sign-in's turns");
  const both = payload(); both.hosts[1].scope = "keyed";
  assert.deepEqual(keyed(accountsHTML(both)), [["TESTHOST, PEERHOST", "their"]]);
  // a single-host payload carries no hosts list: its own scope decides
  const one = payload(); delete one.hosts; one.hours.routes = one.hours.routes.filter((r: any) => r.host === "TESTHOST");
  assert.equal(keyed(accountsHTML(one)).length, 1);
  one.scope = "total";
  assert.equal(keyed(accountsHTML(one)).length, 0);
  // a host's name reaches the page escaped
  const marked = JSON.parse(JSON.stringify(payload()).split("TESTHOST").join("TEST<i>HOST</i>"));
  assert.deepEqual(keyed(accountsHTML(marked)), [["TEST&lt;i&gt;HOST&lt;/i&gt;", "its"]]);
});

test("on a narrow card the summary is one closed line carrying the fold's toggle; opened, the full summary under it; beside Totals no fold", () => {
  // the #74 review's narrow-card finding: stacked under Totals on a phone (390x844), the rows pushed every session row off
  // the card
  const { accountsHTML } = lift("day");
  const p = payload();
  /** whether the html opens with the fold's toggle in the given state */
  const toggle = (h: string, expanded: string) => {
    const b = /^<button\b[^>]*>/.exec(h);
    return !!b && /\bdata-act=accts:toggle\b/.test(b[0]) && new RegExp("\\baria-expanded=" + expanded + "\\b").test(b[0]);
  };
  const closed: string = accountsHTML(p, "closed");
  assert.ok(toggle(closed, "false"), "closed: the toggle, not expanded: " + closed);
  assert.ok(closed.endsWith("</button>") && closed.split("<button").length === 2 && !closed.includes("<table") && !closed.includes("rsp-note"),
    "one line, nothing under it, the notes included (the fixture's keyed host is named once the fold opens): " + closed);
  assert.match(closed.replace(/<[^>]*>/g, ""), /By account\s*Enterprise seat ≈\$120, API key \$36, Unknown ≈\$6$/, "each account and its dollars, the seat's marked");
  // the closed line is the fold's button: its marks carry no hover words, since a tap on a touch screen hovers as well as
  // clicks, and the words would be left up over the rows the tap opens; the open rows keep them
  assert.ok(!closed.includes("data-tip"), "no hover words inside the closed line: " + closed);
  assert.ok(accountsHTML(p, "open").includes("data-tip"), "the opened rows keep theirs");
  const named = payload(); named.accounts[0].name = "work <i>x</i>";   // a stored login's label is the user's own text
  const nc: string = accountsHTML(named, "closed");
  assert.ok(nc.includes("work &lt;i&gt;x&lt;/i&gt; ") && !nc.includes("<i>x"), "an account's name reaches the closed line escaped: " + nc);
  const open: string = accountsHTML(p, "open");
  assert.ok(toggle(open, "true"), "opened: the same toggle, expanded: " + open);
  assert.equal(open.split("</button>").length - 1, 1, "opened: the toggle closes once: " + open);
  assert.ok(open.indexOf("</button>") < open.indexOf("<table"), "…before the rows, so a tap on a row never closes the fold: " + open);
  assert.ok(open.replace(/<[^>]*>/g, "").includes("last 24 hours"), "opened: the range is named");
  assert.equal(open.split("<tr ").length - 1, 3, "opened: every row");
  const beside: string = accountsHTML(p);
  assert.ok(!beside.includes("<button") && !beside.includes("accts:toggle"), "beside Totals nothing folds: " + beside);
  assert.equal(beside.split("<tr ").length - 1, 3, "beside Totals: every row");
  assert.equal(accountsHTML(p, ""), beside);
  assert.ok(accountsHTML({ ...p, accounts: [], accountsMissing: ["OLDHOST"] }, "closed").replace(/<[^>]*>/g, "").includes("OLDHOST: older build"),
    "only an older peer: named");
  const om: string = accountsHTML({ ...p, accounts: [], accountsMissing: ["OLD<i>HOST</i>"] }, "closed");
  assert.ok(om.includes("OLD&lt;i&gt;HOST&lt;/i&gt;: older build") && !om.includes("<i>HOST"), "an older peer's name reaches the closed line escaped: " + om);
  // with account rows, the closed line names no older peer; the opened summary does
  const older = { ...p, accountsMissing: ["OLDHOST"] };
  const oc: string = accountsHTML(older, "closed");
  assert.ok(!oc.replace(/<[^>]*>/g, "").includes("OLDHOST"), "a closed line with rows names no older peer: " + oc);
  assert.ok(accountsHTML(older, "open").replace(/<[^>]*>/g, "").includes("OLDHOST: older build, not in this breakdown"), "the opened summary names it");
});

test("spPaintAccts folds the summary only where it stacked under Totals, and keeps the user's open fold", () => {
  // a stand-in document: the two sections' tops (the row wrapped when the summary's top is below the totals'), and the
  // summary's node; the layout itself is the served lab's (tests/spend_modal_headless.js, the phone step)
  const node = (top: number) => ({ top, innerHTML: "", hidden: false, __rspH: undefined as unknown, getBoundingClientRect() { return { top: this.top }; } });
  const world = (accTop: number) => { const ac = node(accTop), tt = node(100); return { ac, doc: { getElementById: (id: string) => (id === "rsp-accts" ? ac : id === "rsp-totals" ? tt : null) } }; };
  const side = world(100), sideL = lift("day", side.doc);
  sideL.SP.data = payload(); sideL.spPaintAccts();
  assert.ok(side.ac.innerHTML.includes("<table") && !side.ac.innerHTML.includes("accts:toggle") && !side.ac.hidden, "beside Totals: the full summary");
  const stack = world(260), stackL = lift("day", stack.doc);
  stackL.SP.data = payload(); stackL.spPaintAccts();
  assert.ok(stack.ac.innerHTML.includes("aria-expanded=false") && !stack.ac.innerHTML.includes("<table"), "stacked: one line");
  stackL.SP.acctsOpen = true; stackL.spPaintAccts();
  const openPaint = stack.ac.innerHTML;
  assert.ok(openPaint.includes("aria-expanded=true") && openPaint.includes("<table"), "the user's open fold: every row");
  stack.ac.innerHTML = "kept"; stackL.spPaintAccts();
  assert.equal(stack.ac.innerHTML, "kept", "a paint that changes nothing is skipped (a resize keeps the fold's focus)");
  stackL.SP.data = { ...payload(), accounts: [] }; stackL.spPaintAccts();
  assert.deepEqual([stack.ac.innerHTML, stack.ac.hidden], ["", true], "nothing to show: no section");
  // a range with something to show again (a range switch back): the section shows, painted afresh, never left hidden or
  // skipped as unchanged against the paint before the empty one
  stackL.SP.data = payload(); stackL.spPaintAccts();
  assert.deepEqual([stack.ac.hidden, stack.ac.innerHTML], [false, openPaint], "the section is back with its rows");
});

test("the renderer wires the summary, the marks and the account name into the modal", () => {
  assert.ok(USAGE_JS.includes("'<td class=n>'+spUsd(s.usd,s.seat>SP_GRAIN)+'</td>'"), "a session row's dollars");
  assert.ok(USAGE_JS.includes("<span class=ru-tip-reset data-tip=\"'+esc(s.s.login)+'\"> \\u00b7 bills '+esc(spAcctName(d,s.s)||s.s.loginLabel||'a stored login')+'</span>"),
    "a stored-login row bills its account by name, its display (and any email) in the hover");
  assert.ok(USAGE_JS.includes("h+='<div class=rsp-sum><div class=rsp-sec id=rsp-totals>'+totalsHTML(d)+'</div><div class=rsp-sec id=rsp-accts'+(ah?'':' hidden')+'>'+ah+'</div></div>';"), "the section, beside the totals");
  // where spPaintAccts runs. Source pins: each guards only that a route still reaches the function. The behaviour is
  // executed in the served lab's phone step (tests/spend_modal_headless.js: folded on open, a range switch keeping the
  // open fold and naming the new range in its head, a resize across the narrow width folding and unfolding it, opening it
  // on a tall narrow card refitting the pane, a resize from the wide card to the tall narrow one and a reopen there each
  // fitting the pane to the folded summary at once) and in the spPaintAccts test above (the mode rule)
  const rb = USAGE_JS.indexOf("if(m[1]==='order'||m[1]==='range'){"), rp = USAGE_JS.indexOf("spPaintAccts();}", rb);
  assert.ok(rb > 0 && rp > rb && rp < USAGE_JS.indexOf("var sib=", rb),
    "the range branch still repaints the summary (executed: the lab's range switch, the phone's open fold naming the new range and the desktop's rows re-summed)");
  assert.ok(USAGE_JS.includes("spPanel.innerHTML=h;spPaintAccts();renderChart();spSizePane();}"),
    "the first paint still reaches spPaintAccts before the pane is sized (executed: the lab's phone card folded on open, and its reopen on the tall narrow card fitting the pane at once)");
  assert.ok(USAGE_JS.includes("if(SP.open&&SP.data){spPaintAccts();renderChart();spSizePane();}"),
    "the resize handler still reaches spPaintAccts before the pane is sized (executed: the lab's resize across the narrow width, and from the wide card to the tall narrow one fitting the pane at once)");
  const tog = USAGE_JS.indexOf("if(a==='accts:toggle'){SP.acctsOpen=!SP.acctsOpen;spPaintAccts();spSizePane();");
  assert.ok(tog > 0 && tog < USAGE_JS.indexOf("var m=/^(range|measure|order):"),
    "the fold's click, on the stable panel's one listener, still refits the pane (executed: the lab's tall narrow card)");
  assert.ok(USAGE_JS.indexOf("id=rsp-accts") < USAGE_JS.indexOf("<span>By session"), "By account sits above By session");
  assert.ok(!USAGE_JS.includes("By login: "), "the one-line note is gone");
  // the hover words ride the modal's own tooltip from data-tip, delegated on the stable panel (no native title: rail-spend.test.ts)
  assert.ok(USAGE_JS.includes("var box=spTipBox();box.textContent=t.getAttribute('data-tip');spTipPlace(e.clientX,e.clientY,null);"), "textContent: user words, never markup");
  assert.ok(USAGE_JS.includes("if(spPanel){spPanel.addEventListener('mouseover',function(e){var t=spWordsAt(e.target);"), "one listener on the stable panel");
  assert.ok(!USAGE_JS.includes("machine\\u2019s own login and the API key"), "…and its remainder wording");
});
