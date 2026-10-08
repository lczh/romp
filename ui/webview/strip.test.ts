// The romp strip — the VS Code stand-in for the web shell's bottom rail
// (usage bar-pairs + the settings gear below the chat composer / feed foot).
// Pure helpers tested directly; the host opt-in + feed-over-chat wiring is
// source-pinned (chat-view/src/host-chrome.test.ts covers the builders).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { usageColor, fmtAgo, fmtReset, fmtUsd, fmtTok, usageWindows, costCell, STRIP_PANES, fillHostSelect, droppedRowsNote, asFetchError, hostPickerVerdict, tunnelsFailureLine } from "./strip";

test("fmtTok: 3 significant figures at every magnitude (the user 2026-08-13)", () => {
  assert.equal(fmtTok(1_318_619_909), "1.32B");
  assert.equal(fmtTok(13_200_000_000), "13.2B");
  assert.equal(fmtTok(132_000_000_000), "132B");
  assert.equal(fmtTok(1_300_000_000), "1.30B");   // trailing zero KEPT — the precision is the point
  assert.equal(fmtTok(433_181_617), "433M");
  assert.equal(fmtTok(4_330_000), "4.33M");
  assert.equal(fmtTok(9_990), "9.99k");
  assert.equal(fmtTok(999), "999");               // below 1k the raw count already carries 3 figures
});

test("usageColor is theme-aware (ctx-color.ts): with no body classes = CLASSIC = main's 70/90 verbatim", () => {
  // PR #763 item 1: classic keeps its exact palette; the yatharth pair is exercised in ctx-color.test.ts
  assert.equal(usageColor(0), "#54B204");
  assert.equal(usageColor(69), "#54B204");
  assert.equal(usageColor(70), "#e0b020");
  assert.equal(usageColor(89), "#e0b020");
  assert.equal(usageColor(90), "#c0392b");
});

test("fmtReset is 'soon' at rollover, else the ONE duration format (duration.ts, 2026-09-08)", () => {
  // the notice audit counted six span formats across the panes; every one rides durLabel now — the two-digit
  // minor unit keeps a ticking value from jittering, and days show where the old strip format did
  assert.equal(fmtReset(10_000, 10_100), "soon");
  assert.equal(fmtReset(10_000 + 5 * 60, 10_000), "5m 0s");
  assert.equal(fmtReset(10_000 + 2 * 3600 + 5 * 60, 10_000), "2h 05m");
  assert.equal(fmtReset(10_000 + 86400 + 3600, 10_000), "1d 01h");
});

test("usageWindows keeps only reported windows, clamps, and computes pace", () => {
  const nowS = 100_000;
  const ws = usageWindows({
    fiveHour: { pct: 91, resetsAt: nowS + 3600 },        // 1h left of 5h → 80% elapsed
    fable: { pct: 120, resetsAt: nowS + 7 * 86400 },     // over-reported → clamped, 0% elapsed
  }, nowS);
  assert.deepEqual(ws.map((w) => w.key), ["fiveHour", "fable"]);
  assert.deepEqual(ws.map((w) => w.short), ["5h", "F5"], "each window carries its compressed tag");
  assert.equal(ws[0].pct, 91);
  assert.equal(ws[0].elapsedPct, 80);
  assert.match(ws[0].title, /5 hours — used 91% · 80% through the window · resets in 1h 00m/);   // the one duration format (2026-09-08)
  assert.equal(ws[1].pct, 100);
  assert.equal(ws[1].elapsedPct, 0);
});

test("a window that reset since the last report is UNKNOWN — never a confident 0 (the user 2026-07-31)", () => {
  // a remote whose kernel had no live session to ask sat on a days-old snapshot, and the rail drew
  // 0% beside a live account's real bars — indistinguishable from a genuinely idle account. The
  // last-known reading survives (drawn faded), the readout is "?", and the title dates the gap.
  const ws = usageWindows({ fiveHour: { pct: 91, resetsAt: 50 } }, 100);
  assert.equal(ws[0].unknown, true);
  assert.equal(ws[0].pct, 91, "the last-known reading survives for the faded fill");
  assert.equal(ws[0].elapsedPct, null, "pace means nothing against a window that already ended");
  assert.match(ws[0].title, /window reset .* — current usage unknown \(last known 91%\)/);
});

test("a live window is not unknown, and fmtAgo dates a gap", () => {
  const ws = usageWindows({ fiveHour: { pct: 20, resetsAt: 200 } }, 100);
  assert.equal(ws[0].unknown, false);
  assert.equal(fmtAgo(100, 100 + 90060), "1d 1h 1m ago");
});

test("no usage → no windows (the strip stays quiet, never fakes bars)", () => {
  assert.deepEqual(usageWindows(null, 100), []);
  assert.deepEqual(usageWindows({}, 100), []);
});

test("the strip carries the rail's controls: refresh, network popover, pane quick-opens", () => {
  const src = fs.readFileSync(path.join(path.resolve(process.cwd(), ".."), "ui", "webview", "strip.ts"), "utf8");
  assert.ok(src.includes('"/restart"') || src.includes("/restart`"), "the refresh button restarts the kernel");
  for (const ep of ["/ssh-hosts", "/tunnels", "/tunnels/detach", "/tunnels/update", "/tunnels/start"])
    assert.ok(src.includes(ep), `the network popover must drive ${ep} (the rail twin)`);
  // the popover's /tunnels read checks the status before the body (the fourth reader of that route to gain the rule: a
  // JSON-bodied 5xx read as "No remotes attached" with the autoUpdate box mirrored off and a clientDiag filed as ok)
  assert.ok(src.includes('.then((r) => { if (!r.ok) { const e: FetchFault = new Error("/tunnels answered HTTP " + r.status); e.httpStatus = r.status; throw e; } return r.json(); })'),
    "a non-ok /tunnels answer throws, with its status on the error, instead of reading as an empty host list");
  // the popover's refresh says which failure it had, as the host picker does: a status for a non-ok answer, unreachable for a rejected fetch, a console line for both
  assert.ok(src.includes('console.error("romp: /tunnels could not be read", err)'), "the popover's refresh says its failure in the console");
  assert.ok(src.includes("if (err && err.httpStatus) return `The kernel answered HTTP ${err.httpStatus} to /tunnels; retrying…`;"), "a non-ok answer is named by its status, not called an unreachable kernel (tunnelsFailureLine, executed below)");
  // the host picker's /ssh-hosts read has the same rule and keeps the last list it read on a failure
  assert.ok(src.includes('.then((r) => { if (!r.ok) { const e: FetchFault = new Error("/ssh-hosts answered HTTP " + r.status); e.httpStatus = r.status; throw e; } return r.json(); })'),
    "a non-ok /ssh-hosts answer throws, with its status on the error, instead of painting no hosts");
  // the rule itself is executed below (hostPickerVerdict, tunnelsFailureLine, asFetchError); these pin that the two reads call it
  assert.equal(src.split(".catch((e: unknown) => { const f = asFetchError(e); f.network = true; throw f; })").length - 1, 2,
    "both /ssh-hosts and /tunnels wrap a rejected fetch's reason and mark it before the status check");
  assert.ok(src.includes("const v = hostPickerVerdict(err, lastHosts);"), "the picker's catch takes its verdict from the executed rule");
  assert.ok(src.includes("lastHosts = null;"), "nothing kept means the list is forgotten too, so a stale list never repaints after the unreachable signal");
  assert.ok(src.includes("e.textContent = tunnelsFailureLine(err,"), "the popover's refresh takes its line from the executed rule");
  assert.ok(src.includes("fillHostSelect(sel, [], v.label);   // loud, never silently empty"), "nothing kept paints the verdict's label on an empty list");
  assert.ok(src.includes('console.error("romp: ssh hosts could not be read"'), "every failure shape says so in the console");
  assert.ok(src.includes('{ type: "openPane", pane: p.key }'), "quick-opens post openPane to the host");
});

test("the strip quick-opens cover chat/outline/feed only (timeline is a native panel)", () => {
  assert.deepEqual(STRIP_PANES.map((p) => p.key), ["chat", "fleet", "feed"]);
  assert.deepEqual(STRIP_PANES.map((p) => p.label), ["Chat", "Outline", "Feed"]);
});

// A narrow pane must NEVER grow a horizontal scrollbar under the strip (the
// user 2026-07-13), and the ladder must be MEASURED, not width-thresholded
// (the user 2026-07-14: hardcoded @container widths compressed the labels
// while free space remained, and the bars never narrowed): the bars are
// fluid down to a floor, fit() steps the label tiers only when the bars are
// actually pinched or the strip wrapped, and flex-wrap folds whatever still
// doesn't fit onto another row.
test("the strip compresses by measurement: fluid bars, fit()-stepped tiers, wrap backstop", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  const stripRule = css.match(/#romp-strip \{[^}]*\}/)![0];
  assert.ok(stripRule.includes("flex-wrap: wrap"), "the wrap backstop: leftover buttons take another row");
  assert.ok(!css.includes("@container"), "no width-threshold ladder — tiers are measured (fit()), never guessed from pane width");
  const barsRule = css.match(/\.ru-bars \{[^}]*\}/)![0];
  assert.ok(barsRule.includes("width: 54px") && barsRule.includes("min-width: 18px") && barsRule.includes("flex: 0 1 auto"),
    "bars are fluid: a definite 54px width (intrinsic-size-proof, unlike a bare flex-basis) shrinking to an 18px floor");
  // the tier ladder rides #romp-strip[data-tier]: tag, then no %, then no labels
  assert.ok(css.includes('#romp-strip[data-tier="1"] .ru-name-full'), "tier 1 swaps the expanded label for the tag");
  assert.ok(css.includes('#romp-strip[data-tier="2"] .ru-pct'), "tier 2 drops the % readout");
  assert.ok(css.includes('#romp-strip[data-tier="3"] .ru-name'), "tier 3 drops labels — bars only");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.ok(src.includes('"ru-name-full"') && src.includes('"ru-name-short"'), "both label variants render; CSS picks one");
  assert.ok(src.includes("BAR_COMFORT") && src.includes("ResizeObserver"),
    "fit() steps tiers off real measurements, re-run on width changes (event-based)");
  assert.ok(src.includes("offsetWidth"), "measurements use layout px — zoom-independent under uiZoom");
  assert.ok(src.includes('"strip-acts"'), "the actions travel as one right-pinned cluster");
  assert.ok(!src.includes("strip-spacer"), "no spacer item — margin-left:auto keeps the pin across wrapped rows");
});

test("a buildless connected host reads 'unversioned copy', matching the web popover", () => {
  // a plain file copy (no git checkout) reports no sha/version, so drift detection is blind to it —
  // the row must say so where the build word sits, never a bare "connected" that reads as in-sync
  // (the user 2026-08-11, devbox). test_kernel_remote_update.py pins the web popover's twin wording.
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.match(src, /const unversioned = t\.status === "up" && !t\.outOfDate && !t\.kernelSha && !t\.kernelVer;/);
  assert.match(src, /if \(unversioned\) ver = " · unversioned copy";/);
  assert.match(src, /Reinstall it as a git clone to restore the build name and updates\./);
});

// The network button must acknowledge and fail LOUDLY (the user 2026-07-14
// reported it "doing nothing" in VS Code while every repro elsewhere works):
// instant .open chrome on the button, instant popover content before any
// round-trip, a visible error line when the kernel is unreachable, and a
// clientDiag breadcrumb through the host so the kernel records what the
// click actually observed.
test("the net button acknowledges, fails loudly, and leaves a diagnostic trail", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.ok(src.includes('button.classList.toggle("open", open)'), "the button itself acknowledges the toggle");
  assert.ok(src.includes("Checking remotes…"), "the popover never opens blank");
  assert.ok(src.includes("Couldn't reach the kernel"), "an unreachable kernel reads as an error, not an empty box");
  assert.ok(src.includes("(kernel unreachable)"), "the host select fails loudly too");
  const diags = src.match(/type: "clientDiag"/g) || [];
  assert.ok(diags.length >= 2, "toggle + fetch outcome each post a clientDiag breadcrumb");
  const cssSrc = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  assert.ok(cssSrc.includes("#strip-net.open"), "the .open acknowledgment has visible chrome");
  const kernel = fs.readFileSync(path.join(ROOT, "bin", "romp-kernel"), "utf8");
  assert.ok(kernel.includes('"clientDiag"') && kernel.includes("client-diag.jsonl"),
    "the kernel persists clientDiag breadcrumbs (the locateDiag pattern, generalized)");
});

test("the feed's control bar wraps on a narrow pane instead of overflowing", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.css"), "utf8");
  const foot = css.match(/#feed-foot \{[^}]*\}/)![0];
  assert.ok(foot.includes("flex-wrap: wrap"), "#feed-foot must fold its buttons onto another row");
});

test("the chat hosts its OWN gear modal (opens over the pane it was clicked in)", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const render = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
  assert.ok(render.includes('require("./gear.js")'), "chat bundle must load the gear module");
  assert.ok(!render.includes("openRompSettings"), "no cross-pane settings hop remains");
});

test("both bundles init the strip; the web pages never opt in", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const read = (f: string) => fs.readFileSync(path.join(ROOT, "ui", "webview", f), "utf8");
  assert.ok(read("render.ts").includes("initStrip("), "chat bundle must init the strip");
  assert.ok(read("feed.ts").includes("initStrip("), "feed bundle must init the strip");
  const kernel = fs.readFileSync(path.join(ROOT, "bin", "romp-kernel"), "utf8");
  assert.ok(!kernel.includes("__rompShowStrip"), "the web shell keeps its own rail — no strip opt-in kernel-side");
});

// ── the USAGE CELL (the user 2026-10-08): every turn at API prices, by the route that billed it, for the viewer's calendar
// day and month (built by the kernel in this webview's zone, spendWin); the key's dollars are charged, any other route's
// romp's estimate, and a figure that holds any estimate wears the approx mark. The strip mirrors the rail's costCellHTML.
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const routes = (over: Record<string, unknown> = {}) => ({ spendWin: Object.assign({
  tz: TZ,
  today: { routes: { machine: 183.4 }, tok: { tok: 60_300_000 } },
  monthToDate: { routes: { key: 4871.6, machine: 183.4 }, tok: { tok: 1_670_000_000 } },
}, over) });

test("costCell sums every route per calendar window and marks a figure holding any estimate", () => {
  const cell = costCell(routes());
  assert.ok(cell);
  assert.deepEqual(cell!.segs.map((s) => [s.key, s.label, s.short]),
    [["today", "today", "today"], ["monthToDate", "this month", "month"]], "the windows in the user's words");
  assert.deepEqual(cell!.segs.map((s) => [fmtUsd(s.usd), s.est]), [["$183", true], ["$5,055", true]],
    "a seat's dollars are an estimate: today is all seat, the month mixes the key's charge with the seat's estimate");
  assert.deepEqual(cell!.segs.map((s) => Math.round(s.keyUsd)), [0, 4872], "the key's share is kept apart");
  assert.match(cell!.title, /^Usage at API prices\n/);
  assert.match(cell!.title, /this month: ≈\$5,055 \(≈\$183 on subscription seats, \$4,872 charged to the API key\) · 1\.67B tok/);
  assert.match(cell!.title, /≈ marks romp’s estimate: the tokens priced at the API’s list prices/);
  assert.ok(!cell!.title.includes(" (in "), "built in this webview's own zone: no zone named");
  assert.match(costCell(routes({ tz: TZ === "UTC" ? "Asia/Kolkata" : "UTC" }))!.title, /^Usage at API prices \(in (Asia\/Kolkata|UTC)\)/, "another zone is named");
});

test("a key-only figure carries no approx mark, and fmtUsd groups thousands", () => {
  const cell = costCell(routes({ today: { routes: { key: 14 }, tok: { tok: 0 } }, monthToDate: { routes: { key: 1320.4 }, tok: { tok: 0 } } }));
  assert.deepEqual(cell!.segs.map((s) => [fmtUsd(s.usd), s.est]), [["$14", false], ["$1,320", false]]);
  assert.match(cell!.title, /today: \$14 charged to the API key/);
  assert.ok(!cell!.title.includes("marks an estimate"), "no estimate, no footnote");
  assert.equal(fmtUsd(55183.4), "$55,183");
  assert.equal(fmtUsd(12.4), "$12", "whole dollars, no cents (the user 2026-08-09)");
});

test("no viewer-zone windows → no cell (an older kernel ships none); an unreadable ledger is no cell either; no key material", () => {
  assert.equal(costCell(null), null);
  assert.equal(costCell({ spend: { day: { usd: 3 } } }), null, "the old key windows alone do not arm it");
  assert.equal(costCell({ spendRoutes: { today: { key: [3, 1] } } }), null, "nor the previous build's kernel-day routes");
  assert.equal(costCell({ spendWin: { error: "the spend ledger could not be read" } }), null, "an unreadable ledger is no confident figure");
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.ok(!src.includes("apiTail") && !src.includes("authTail"), "no key-tail plumbing");
  assert.ok(!src.includes('lbl.textContent = "API"'), "no 'API' label on the cell: its figures are mostly a seat's");
});

test("the cell's figures survive every tier; labels fold at tier 3 (source pins)", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.match(src, /box\.className = "ru-w ru-api";/);
  assert.match(src, /val\.className = "ru-apiv";/, "figures wear their own class, never .ru-pct");
  assert.match(src, /val\.textContent = \(s\.est \? "\\u2248" : ""\) \+ fmtUsd\(s\.usd\);/, "the approx mark rides the figure");
  assert.doesNotMatch(src, /ru-apitok/, "no tokens on the bar (they ride the hover title)");
  assert.doesNotMatch(src, /spendWindows/, "spend-as-rows is gone, not merely unused");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  assert.match(css, /\.ru-apiv \{ font: 600 10px/);
  assert.doesNotMatch(css, /ru-apitok/);
  assert.doesNotMatch(css, /ru-textonly/, "the ghost-row mechanism died with spend-as-rows");
});

test("an unknown window is not drawn on the bar at all — its last-known lives on hover (the user 2026-08-13)", () => {
  // supersedes the 2026-07-31 '?' slot: the bar shows only what we know. The strip has no rich hover
  // panel, so the hidden rows' last-known text rides the usage wrap's own title, labelled as such.
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  const css = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
  assert.match(src, /if \(w\.unknown\) \{ unknownLines\.push\(w\.title\); continue; \}/);
  assert.match(src, /usageWrap\.title = unknownLines\.length\s*\n\s*\? "Not shown \(no current reading\):\\n" \+ unknownLines\.join\("\\n"\) : "";/);
  assert.doesNotMatch(src, /ru-qmark/, "the '?' slot is gone");
  assert.doesNotMatch(css, /ru-qmark/);
  assert.doesNotMatch(css, /\.ru-w\.ru-unk \.ru-fill \{ opacity: 0\.3; \}/, "the faded fill stays gone");
});

// ── the attach box's host list is built from ELEMENTS, never markup (2026-09-08) ─────────────────────
// An ssh alias is whatever ~/.ssh/config says. loadHosts used to render `<option value="${h}">${h}</option>`
// through innerHTML, so an alias that closed the attribute carried a handler into the popover. Executed
// against a minimal element stand-in whose innerHTML setter THROWS, so the only way to pass is to set
// value/textContent on option elements.
class FakeEl {
  tagName: string;
  value = "";
  children: FakeEl[] = [];
  attrs: Record<string, string> = {};
  private _text = "";
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  get textContent(): string { return this._text; }
  set textContent(v: string) { this._text = v; this.children = []; }   // the DOM's: assigning text drops children
  set innerHTML(_v: string) { throw new Error("innerHTML is not how host options are built"); }
  appendChild(c: FakeEl): FakeEl { this.children.push(c); return c; }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; }
}
function withDocument(fn: () => void): void {
  const g: any = globalThis;
  const had = "document" in g, prev = g.document;
  g.document = { createElement: (t: string) => new FakeEl(t) };
  try { fn(); } finally { if (had) g.document = prev; else delete g.document; }
}

test("fillHostSelect: a crafted alias is an option's VALUE and TEXT, never markup; non-strings are dropped", () => {
  withDocument(() => {
    const sel: any = new FakeEl("select");
    const hostile = ['x"onmouseover="alert(1)', "<img src=x onerror=alert(1)>"];
    fillHostSelect(sel, [...hostile, 42, "", null], "(none)");
    assert.equal(sel.children.length, 2, "the two strings; the number, the empty string and the null are dropped");
    assert.deepEqual(sel.children.map((o: any) => o.tagName), ["OPTION", "OPTION"]);
    assert.deepEqual(sel.children.map((o: any) => o.value), hostile, "the value IS the string, character for character");
    assert.deepEqual(sel.children.map((o: any) => o.textContent), hostile, "…and so is the label");
    for (const o of sel.children) assert.deepEqual(o.attrs, {}, "nothing went through the attribute/markup path");
  });
});

test("fillHostSelect: an empty or malformed list still says why, and the list is capped", () => {
  withDocument(() => {
    const sel: any = new FakeEl("select");
    fillHostSelect(sel, [], "(kernel unreachable)");
    assert.equal(sel.children.length, 1);
    assert.equal(sel.children[0].value, "");
    assert.equal(sel.children[0].textContent, "(kernel unreachable)", "loud, never silently empty");
    fillHostSelect(sel, "not-a-list", "(no ~/.ssh/config hosts)");
    assert.equal(sel.children.length, 1, "a previous fill is replaced, not appended to");
    assert.equal(sel.children[0].textContent, "(no ~/.ssh/config hosts)");
    fillHostSelect(sel, Array.from({ length: 600 }, (_, i) => "h" + i), "x");
    assert.equal(sel.children.length, 513, "512 real options, then ONE marker for the rest");
    assert.deepEqual(sel.children.slice(0, 512).map((o: any) => o.value), Array.from({ length: 512 }, (_, i) => "h" + i));
    const marker = sel.children[512];
    assert.equal(marker.textContent, "… 88 more not shown", "a cut list says how much it left out, never silently");
    assert.equal(marker.disabled, true, "…and the marker is not a pickable host");
    assert.ok(sel.children.slice(0, 512).every((o: any) => !o.disabled));
    fillHostSelect(sel, Array.from({ length: 512 }, (_, i) => "h" + i), "x");
    assert.equal(sel.children.length, 512, "exactly at the cap: no marker");
  });
});

test("droppedRowsNote: the sub-panel names the peer rows the kernel left out, in one sentence both panels share", () => {
  assert.equal(droppedRowsNote("TESTHOST", 2), "2 rows from TESTHOST had no usable host and were left out");
  assert.equal(droppedRowsNote("TESTHOST", 1), "1 row from TESTHOST had no usable host and was left out");
  for (const none of [0, -1, "2", undefined, null, NaN]) assert.equal(droppedRowsNote("TESTHOST", none), "", `${String(none)} is not a count`);
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  const sub = src.slice(src.indexOf("function renderSub(via: string)"), src.indexOf("function subRow(via: string"));
  assert.match(sub, /const note = droppedRowsNote\(via, d\.dropped\);/, "renderSub reads the kernel's count");
  assert.match(sub, /if \(!rows\.length && !note\)/, "a list emptied by the drop is not 'no hosts attached'");
  assert.match(sub, /e\.textContent = note;/, "…and the note is rendered as text beneath the rows");
});

test("loadHosts routes both outcomes through fillHostSelect — no innerHTML host rendering remains in strip.ts", () => {
  const ROOT = path.resolve(process.cwd(), "..");
  const src = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
  assert.match(src, /fillHostSelect\(sel, lastHosts, "\(no ~\/\.ssh\/config hosts\)"\)/);   // the list read, kept across a failed refresh
  assert.match(src, /fillHostSelect\(sel, \[\], v\.label\)/);   // the failure label comes from the executed verdict (kernel unreachable among them)
  assert.doesNotMatch(src, /<option value="\$\{h\}">/, "the template that rendered an alias as markup");
});

// The pickers' failure rule, EXECUTED over every shape (the fifth tidy: the rule was pinned by source text alone, which a
// semantically equivalent rewrite could hollow out with every pin green). hostPickerVerdict is the one rule both surfaces
// use; asFetchError is the rejection wrap; tunnelsFailureLine the popover's three wordings.
test("asFetchError keeps an Error, an object's message or its JSON, names null, and strings the rest", () => {
  const e = new Error("boom");
  assert.equal(asFetchError(e), e);
  assert.equal(asFetchError({ message: "socket closed" }).message, "socket closed");
  assert.equal(asFetchError({ code: 7 }).message, '{"code":7}');
  assert.equal(asFetchError(null).message, "fetch rejected");
  assert.equal(asFetchError(undefined).message, "fetch rejected");
  assert.equal(asFetchError("down").message, "down");
});

test("hostPickerVerdict: a status or a parse fault keeps the list once read; a rejected fetch never does; a first failure is named by kind", () => {
  const status500 = Object.assign(new Error("HTTP 500"), { httpStatus: 500 });
  const network = Object.assign(new Error("down"), { network: true });
  const parse = new SyntaxError("Unexpected token <");
  const list = ["web", "api"];
  assert.deepEqual(hostPickerVerdict(status500, list), { keep: true, label: "(no ~/.ssh/config hosts)" });
  assert.deepEqual(hostPickerVerdict(parse, list), { keep: true, label: "(no ~/.ssh/config hosts)" });
  assert.deepEqual(hostPickerVerdict(network, list), { keep: false, label: "(kernel unreachable)" }, "a dead kernel never hides behind a stale list");
  assert.deepEqual(hostPickerVerdict(network, null), { keep: false, label: "(kernel unreachable)" });
  assert.deepEqual(hostPickerVerdict(status500, null), { keep: false, label: "(the kernel answered HTTP 500)" }, "a first load's 500 is not an unreachable kernel");
  assert.deepEqual(hostPickerVerdict(parse, null), { keep: false, label: "(the kernel's answer could not be read)" });
  assert.deepEqual(hostPickerVerdict(status500, []), { keep: true, label: "(no ~/.ssh/config hosts)" }, "an empty list read is a list: a later 500 keeps it and shows the no-hosts label");
});

test("tunnelsFailureLine names a status, an unreachable kernel, or an answer that would not parse", () => {
  assert.equal(tunnelsFailureLine(Object.assign(new Error("x"), { httpStatus: 502 }), "same origin"), "The kernel answered HTTP 502 to /tunnels; retrying…");
  assert.match(tunnelsFailureLine(Object.assign(new Error("x"), { network: true }), "http://127.0.0.1:1"), /^Couldn't reach the kernel \(http:\/\/127\.0\.0\.1:1\)/);
  assert.equal(tunnelsFailureLine(new SyntaxError("bad json"), "same origin"), "The kernel's answer to /tunnels could not be read; retrying…");
  assert.equal(tunnelsFailureLine(null, "same origin"), "The kernel's answer to /tunnels could not be read; retrying…");
});
