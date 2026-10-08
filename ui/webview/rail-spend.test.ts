// SPEND on the rail. Redesigned 2026-10-08 (the user, who read "API" on the cell while nearly all of their usage bills an
// enterprise seat, and wanted the seat's usage in the figure as the dollars it would cost at API prices, marked as an
// estimate): the cell is USAGE AT API PRICES, every recorded turn at Claude Code's own cost (total_cost_usd, its tokens
// at the API's published prices) by the route that billed it, for the calendar day and month; the key's dollars are
// charged, any other route's an estimate, and a figure holding any estimate wears the approx mark. Spend is NUMBERS,
// never bars (the user 2026-08-08). The recorder accumulates per ResultMessage into spend.json's day AND hour buckets,
// each carrying a `key` sub-count for key-billed turns and a `byLogin` map for stored logins; the kernel ships each
// host's dollars by route (spendRoutes, _rail_routes). The behaviour executes in usage-at-api-prices.test.ts; these are
// the source pins. No jsdom harness → source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const STRIP = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
const STRIPCSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
const BACKEND = fs.readFileSync(path.join(ROOT, "kernel", "sdk_backend.py"), "utf8");

test("the kernel serves spend windows for BOTH payload shapes, keyed-only beside bars", () => {
  // the spend-only view arms on the legacy apiKey marker OR a login-less machine with recorded spend,
  // and keeps TOTAL sums (everything there bills the key; legacy files predate the split), except in a
  // bucket where a stored login billed turns, which reads its key sub-count (2026-10-01: a seat's turns)
  assert.ok(KERNEL.includes('if o.get("apiKey") or (not _claude_account() and (jd.STATE / "spend.json").exists()):'));
  assert.ok(KERNEL.includes('out = {"apiKey": True, "spend": _spend_windows(login_split=True),'));
  // the key windows still ride for an older shell (rolling hour/day/week/month and the calendar month, T235)
  assert.ok(KERNEL.includes('"hour": _rolling(1),'));
  // …and every arm with recorded spend carries the host's dollars by billing route for the rail's calendar windows,
  // the seat's turns included (2026-10-08): the cell's figure, its approx mark and the hover's split are one read
  assert.ok(KERNEL.includes('out = {"today": today, "monthToDate": mtd, "week": week,'));
  assert.equal((KERNEL.match(/return _with_rail_routes\(out\)/g) || []).length, 3, "the key-only arm, the no-windows arm and the windows arm");
  // …and the $/hour series rides beside the windows for the hover graph (the user 2026-08-13)
  assert.ok(KERNEL.includes('out["spendSeries"] = ss'));
  // the bars payload attaches the KEYED split only — a login turn's computed cost there would be
  // dollars nobody is billed — and only when key turns actually exist (the user 2026-08-08)
  assert.ok(KERNEL.includes("def _spend_windows(keyed_only=False, login_split=False):"));
  assert.ok(KERNEL.includes("ksp = _spend_windows(keyed_only=True)"));
  assert.match(KERNEL, /if any\(\(ksp\.get\(k\) or \{\}\)\.get\("turns"\) for k in \("day", "week", "month"\)\):/);
  // no fragment of the key rides ANY payload (the user 2026-08-08, evening): the tail plumbing is
  // gone from the kernel wholesale, and the keyed-spend gate is a plain existence check
  assert.ok(KERNEL.includes("if _auth_key_present():"));
  assert.ok(!KERNEL.includes("apiTail"), "no key material in any usage payload");
  assert.ok(!KERNEL.includes("authTail"), "no key material in the status payload either");
  // rolling day/week read the HOUR buckets (192h = 8 days fits both); month-to-date reads the day
  // ledger. fiveHour/sevenDay stay emitted ONE release for version skew (the user 2026-08-13).
  assert.match(KERNEL, /"day": _rolling\(24\), "week": _rolling\(7 \* 24\)/);
  assert.match(KERNEL, /"fiveHour": _rolling\(5\), "sevenDay": _rolling\(7 \* 24\)/);
  assert.ok(KERNEL.includes("k.startswith(month)"));
  // the accumulator: cumulative-per-process DELTAS, and each bucket splits out the key's own turns
  assert.ok(BACKEND.includes("delta = total - self._last_cost_total if total >= self._last_cost_total else total"));
  // the TOKEN watermarks diff the cumulative modelUsage map, never the flat `usage` dict — that one is
  // the TURN's own total on the current CLI, and diffing it recorded a fraction of every turn (the user
  // 2026-09-06). Each spend window also carries the by-KIND split of its tokens for the hover.
  assert.ok(BACKEND.includes("turn_u = self._turn_usage(msg)"));
  assert.ok(BACKEND.includes('mu = getattr(msg, "model_usage", None)'));
  assert.ok(BACKEND.includes("out[k] = v - last if v >= last else v"), "a shrunken running total is a reset we missed → fold whole");
  assert.ok(KERNEL.includes('KINDS = ("tokIn", "tokOut", "tokCacheR", "tokCacheW")'));
  assert.ok(BACKEND.includes("sid=self.thread_of or self.sid,"),   // T346: the stored login the launch carried rides beside it
    "the settle threads the OWNING sid — a comment thread bills its owner (T144), a plain session itself (T100)");
  assert.ok(BACKEND.includes("if keyed or ke:   # carry an existing key split forward even on a login turn"));
  assert.ok(BACKEND.includes("_fold(days, day, 90)"));
  assert.ok(BACKEND.includes("_fold(hours, hour, 192)"), "8 days of hour buckets feed the rolling windows");
});

test("VS Code strip: the usage cell is the rail's twin, armed on the calendar routes' presence", () => {
  assert.match(STRIP, /export function costCell\(usage: any\): CostCell \| null/);
  assert.ok(STRIP.includes("const sr = usage && usage.spendRoutes;"));
  assert.ok(STRIP.includes("if (!sr || !(sr.today || sr.monthToDate)) return null;"), "an older kernel's week-only routes arm nothing");
  assert.ok(STRIP.includes('if (r === "key") keyUsd += v;'), "the key's route is the charged one");
  assert.ok(STRIP.includes("const est = usd - keyUsd >= COST_GRAIN;"), "anything else is an estimate");
  assert.doesNotMatch(STRIP, /export function apiCell/, "the API-key cell is gone");
  assert.doesNotMatch(STRIP, /spendWindows/, "spend never renders as window rows");
  assert.doesNotMatch(STRIP, /spendChip/);
  assert.doesNotMatch(STRIPCSS, /\.ru-spend/);
});

test("the web rail's usage cell: window names left of their figures, no label, no tokens, no spend bars", () => {
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("function costCellHTML(live){"));
  assert.ok(usageJS.includes("return '<div class=ru-name>'+w[1]+'</div><div class=ru-pct>'+costUsd(s[w[0]])+'</div>';}).join('')+'</div>';}"),
    "each window's ONE display name in the name font, LEFT of its figure (the user 2026-08-09)");
  assert.ok(!usageJS.includes("<div class=ru-name>API</div>"), "no 'API' label: the figures are mostly a seat's");
  assert.ok(!usageJS.includes("ah-slot"), "the API-health dot is not in the spend cell");
  assert.ok(usageJS.includes("function costUsd(v){return (costEst(v)?'\\u2248':'')+fmtUsd(v.usd);}"), "the approx mark on a figure holding an estimate");
  assert.ok(usageJS.includes("function costEst(v){return (v.usd-v.key)>=COST_GRAIN;}"));
  assert.ok(!usageJS.includes("_tail"), "no tail plumbing survives in the rail JS");
  // the graph and the budget fills are gone: no spend track, no spend color ramp
  assert.ok(!usageJS.includes("spendColor"), "the budget-fill ramp died with the spend bars");
  assert.ok(!usageJS.includes("spendWinsHTML"), "spend never renders as window rows with tracks");
  assert.ok(usageJS.includes("function hasCost(u){var sr=u&&u.spendRoutes;return !!(sr&&(sr.today||sr.monthToDate));}"), "presence-keyed, like the strip");
  assert.ok(usageJS.includes("var live=ROWS.filter(function(r){return hasBars(r.usage)||hasCost(r.usage)||hasSpend(r.usage)||!!(r.usage&&r.usage.noWindows);});"),
    "a host with spend from an older build still joins, to be named in the hover");
});

test("one display name per window, worn everywhere: bars, hover sections, API cell, notices", () => {
  // the user 2026-08-09: '5 hours' on the bars, '5h' on the API numbers and 'Session (5h)' in the
  // hover were three vocabularies for one thing — WINS now carries the ONE display name per window
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("var WINS=[['fiveHour',5*3600,'5 hours'],"));
  assert.ok(usageJS.includes("['sevenDay',7*86400,'7 days'],"));
  assert.ok(usageJS.includes("['fable',7*86400,'Fable 5']];"));
  assert.ok(usageJS.includes("'<div class=ru-name>'+w[2]+'</div>'"), "the collapsed bars wear it");
  // the hover window heading is the bare name — no '(5h)' span, no Session/Weekly third vocabulary
  assert.ok(usageJS.includes("'<div class=ru-tip-win><div class=ru-tip-name><span>'+esc(v.name)+'</span>'"));
  assert.ok(!usageJS.includes("'Session'"), "the Session/Weekly names are retired");
  assert.ok(!usageJS.includes("Weekly (7d)"));
  // the limit notice speaks the same windows, prose-shaped
  assert.ok(usageJS.includes("names.push('5-hour')"));
  assert.ok(usageJS.includes("names.push('7-day')"));
});

test("dollars are WHOLE everywhere, thousands grouped — no cents on any spend surface", () => {
  // the user 2026-08-09: cents are noise at these magnitudes; 2026-10-08: a five-figure month reads at a glance ($55,183)
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("function fmtUsd(v){return '$'+Math.round(v).toLocaleString('en-US');}"));
  assert.ok(!usageJS.includes("toFixed(2)"), "no cents anywhere in the rail JS");
  assert.ok(STRIP.includes('export function fmtUsd(v: number): string { return "$" + Math.round(v).toLocaleString("en-US"); }'),
    "the strip rounds through the same one formatter");
  assert.doesNotMatch(STRIP, /usd\.toFixed\(2\)/);
});

test("the rich tip is the ONE hover surface: no native titles, account blocks, one usage section", () => {
  // NO native title attributes anywhere on the rail (the user 2026-08-08, who got the browser's flat
  // yellow box on top of the rich hover): the usage JS may mention titles only in comments
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  for (const line of usageJS.split("\n")) {
    const code = line.split("//")[0];
    assert.ok(!/\btitle\s*=/.test(code) && !code.includes(".title="), `native title in usage JS: ${line.trim()}`);
  }
  assert.ok(usageJS.includes("sets.forEach(function(e){if(!['fiveHour','sevenDay','fable'].some(function(k){return e.det[k];})&&!e.det._noWin)return;"),
    "a machine with no window block joins no account block (2026-09-19), unless its account answered it has none (2026-10-01)");
  // ONE usage section for every machine at once, appended even when no host has window blocks
  assert.ok(usageJS.includes("h+=costTipHTML(sets);"));
  assert.ok(usageJS.includes("var h='<div class=\"ru-tip-win ru-tip-cost\"><div class=ru-tip-name><span>Usage at API prices</span></div>';"));
  // every machine in the figures BY NAME when there is more than one (the user 2026-08-13: a machine missing from the
  // hover read as a bug)
  assert.ok(usageJS.includes("h+='<div class=ru-tip-note>by machine this month: '+hosts.map(function(x){return esc(x.host)+' '+costUsd(x.v);}).join(' \\u00b7 ')+'</div>';}}"));
  // the tip anchors ABOVE the rail, centered on the CURSOR — never pinned to the container edge
  assert.ok(usageJS.includes("var x=(ev&&typeof ev.clientX==='number')?ev.clientX:(r.left+r.width/2);"));
  assert.ok(usageJS.includes("x-tip.offsetWidth/2"));
  assert.ok(usageJS.includes("r.top-tip.offsetHeight-8"));
});

test("same-account hosts share the FRESHEST window reading — one truth per login", () => {
  // the user 2026-08-09: the windows are account-wide allowances, so a host that hadn't polled in
  // hours sat beside the live number contradicting it. Grouped on the acct digest, freshest t wins,
  // window fields shared; each host keeps its OWN key spend (dollars are host-local).
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("function shareFreshest(live)"));
  assert.ok(usageJS.includes("var a=r.usage&&r.usage.acct;r.ownT=(typeof r.usage.t==='number')?r.usage.t:null;r.lag=false;if(a)(by[a]=by[a]||[]).push(r);"),
    "grouped on the digest; key-only hosts (no acct) stand alone; each member keeps its OWN report time for the machines line (2026-09-19)");
  assert.ok(usageJS.includes("r.lag=!b.noWindows&&['fiveHour','sevenDay','fable'].some(function(w){var x=u[w],y=b[w];"),
    "a member whose own reading differed from the freshest is marked lagging (never beside an account with no windows)");
  assert.ok(usageJS.includes("if(tr>tb)best=r;"), "freshest reading wins the group");
  assert.ok(usageJS.includes("['fiveHour','sevenDay','fable','t','limited','acctName','noWindows','monthly'].forEach"), "window fields shared (the seat's monthly allowance with them) — spend deliberately not");
  assert.ok(usageJS.includes("shareFreshest(live);"), "…and it runs on every render");
});

test("the hover names WHICH login the window bars belong to (the tab hover's label)", () => {
  // the user 2026-08-09: the usage tip says whose account the windows are, like the tab hover;
  // the cross-host dedup stays on the opaque acct digest — the name is display only, by the account's kind since
  // 2026-10-01 (the spend detail's words; the email stays off the rail)
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("if(u.acctName)det._acct=u.acctName;"));
  assert.ok(usageJS.includes("if(d._acct)h+='<div class=ru-tip-acct>'+esc(d._acct)+'</div>';"),
    "…as the account block's HEAD (2026-09-19); a key-only host has no block, so its spend already says whose dollars");
  assert.ok(KERNEL.includes('"acctName": _acct_name(),'));
  assert.ok(KERNEL.includes(".ru-tip-acct{"));
});

test("a multi-account breakdown lays ACCOUNTS side by side, one block each, the machines named under their block", () => {
  // the user 2026-08-09: the per-host breakdown used to stack every host into one tall pillar; the user 2026-09-19: three
  // machines on one login drew three identical columns, so the column is per ACCOUNT now (the meters once, the account
  // line as the head, the machines one line beneath in the strip's host dress, a lagging one named, updated-ago once, the
  // oldest of the group); flex-wrap still folds the mobile modal back to a stack on its own
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("'<div class=ru-tip-cols>'+blocks.map(function(b){return '<div class=ru-tip-col>'+b+'</div>';}).join('')+'</div>'"));
  assert.ok(usageJS.includes("function acctBlocksHTML(sets,manyMachines)"));
  assert.ok(usageJS.includes("var a=e.acct||('host:'+e.host);var g=by[a];if(!g){g={members:[]};by[a]=g;groups.push(g);}g.members.push(e);"), "grouped by the account digest, in the panel's order");
  assert.ok(usageJS.includes("if(manyMachines)h+='<div class=ru-tip-machines>signed in on '+g.members.map(function(e){return machineHTML(e.host)"),
    "the machines line, only when the mesh has more than one machine, saying what the names are (2026-10-08 review)");
  assert.ok(usageJS.includes("+(e.lag?'<span class=ru-tip-lag> lagging'+(e.ownT?', updated '+fmtAgo(e.ownT):'')+'</span>':'');}).join(', ')+'</div>';"), "a lagging machine named beside its name with its own report's age");
  assert.ok(usageJS.includes("if(ts.length&&wh)h+='<div class=ru-tip-age>updated '+fmtAgo(Math.min.apply(null,ts))+'</div>';"),
    "updated-ago once per block: the oldest report of the group (none on a block with no windows: nothing is read)");
  assert.ok(usageJS.includes("if(NOREPORT.length)h+='<div class=ru-tip-machines>no usage report yet from '+NOREPORT.map(machineHTML).join(', ')+'</div>';"), "a machine not reporting is named, never a column");
  assert.ok(usageJS.includes("function machineHTML(name){return '<span class=\"ru-tip-machine host-prefix\">'+esc(name)+'</span>';}"), "the strip's quiet host dress");
  assert.ok(KERNEL.includes(".ru-tip-machines .host-prefix{color:var(--dim,#9aa0a6);font-weight:400;font-style:italic;font-size:0.86em}"), "…byte for byte the tab's host-prefix declarations");
  assert.ok(!usageJS.includes("function setHTML(e,many)") && !usageJS.includes("ru-tip-host"), "the per-host column and its heading are gone");
  // a single account keeps its plain un-columned layout: the column wrapper exists only when more
  // than one account reports (blocks.length? guards the all-keyed box, whose hosts have NO window
  // blocks at all — see expected-auth.test.ts)
  assert.ok(usageJS.includes("var h=blocks.length?(blocks.length>1?"));
  assert.ok(KERNEL.includes(".ru-tip-cols{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}"));
  assert.ok(KERNEL.includes(".ru-tip-col{flex:0 1 auto;min-width:200px}"));   // 200px floor since the sparks span the column (the user 2026-08-14)
});

test("every tip string carries data — the narration is gone and stays gone", () => {
  // The de-inking pass (the user 2026-08-08, who found the tip overly verbose): the host name alone
  // heads a section, the spend rows label themselves, config hints live in the docs, and the reader
  // is already hovering the bars when the refresh hint shows.
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  const code = usageJS.split("\n").map((l) => l.split("//")[0]).join("\n");
  assert.ok(!code.includes("its own allowance"), "the host heading is the host name, bare");
  assert.ok(!code.includes("one scale"), "the spend rows are their own labels");
  assert.ok(!code.includes("no budget set"), "no config instructions in a glance surface");
  assert.ok(!code.includes("current usage unknown"), "the ? row already says unknown");
  assert.ok(!code.includes("click the bars"), "the short hint replaced it");
  assert.ok(code.includes("window reset '+esc(v.ago)+'; no reading since"), "the rolled note keeps only its facts");
});

test("token counts carry 3 significant figures, and both fmtTok twins share the formula (the user 2026-08-13)", () => {
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  // one adaptive-decimals helper in each copy — 1.32B / 13.2B / 132B, trailing zeros kept
  assert.ok(usageJS.includes("function fmtSig3(v){return v.toFixed(v>=100?0:v>=10?1:2);}"));
  assert.ok(usageJS.includes("if(n>=1e9)return fmtSig3(n/1e9)+'B';"));
  assert.match(STRIP, /return v\.toFixed\(v >= 100 \? 0 : v >= 10 \? 1 : 2\);/);
  assert.match(STRIP, /if \(n >= 1e9\) return fmtSig3\(n \/ 1e9\) \+ "B";/);
  // the old 1-decimal + strip-trailing-zero form is gone from both
  assert.ok(!usageJS.includes("toFixed(1).replace"));
  assert.ok(!STRIP.includes('toFixed(1).replace'));
});

test("the hover carries no graph: the charts are the panel's, one click away (2026-10-08); the per-window sparks stay gone", () => {
  // the hover's $/h line graph (2026-08-13/14) drew the key's dollars alone; the panel draws the seat and the key as
  // their own charts, so the hover stays the glance: the figures, split by account
  assert.ok(!KERNEL.includes("function moneyGraph("), "no graph in the hover");
  assert.ok(!KERNEL.includes('out["winSeries"]'), "no per-window series rides the usage payload");
  assert.ok(!KERNEL.includes("_winSeries"), "the client neither captures nor reads a window series");
  assert.ok(!KERNEL.includes("sparkHTML"), "the per-window sparkline renderer is gone");
  assert.ok(!KERNEL.includes("ru-tip-spark"), "no spark markup or styles remain");
  assert.ok(BACKEND.includes("def _record_usage_history"), "the utilization ledger keeps recording");
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("tip.innerHTML=h+'<div class=ru-tip-hint>Click for the charts and the breakdown by session.</div>';"), "the hover says where the charts are");
  // "click to refresh" misread on a hover: the hint is gone, the 60s auto-pull stands, and an
  // already-open hover tip re-renders in place when fresh data lands
  assert.ok(!usageJS.includes("click to refresh"), "no refresh hint on a hover surface");
  assert.ok(usageJS.includes("setInterval(function(){pull(false);},60000);"), "the 60s auto-pull stands");
  assert.ok(usageJS.includes("if(tip.style.display==='block'&&!tip.classList.contains('ru-modal'))"),
    "an open tip follows data landings");
});

test("the mobile usage modal is height-capped with a scroll pane, and taps on it don't fall through", () => {
  // The user 2026-08-14: the $/h chart + multi-host rows outgrew a phone screen, and the centered
  // translate(-50%,-50%) pushed the TOP off-screen with nothing to scroll. The modal variant caps its
  // height to the DYNAMIC viewport (dvh overrides vh where supported — mobile browser chrome collapses)
  // and scrolls; pointer-events comes back on (the base #ru-tip is a hover tooltip, pointer-events:none),
  // which is what makes scrolling possible AND stops a tap on the panel closing it through the backdrop.
  const modal = KERNEL.split("#ru-tip.ru-modal{")[1].split("}")[0];
  assert.ok(modal.includes("max-height:84vh"), "a vh cap for browsers without dvh");
  assert.ok(modal.includes("max-height:84dvh"), "the dvh override tracks mobile chrome");
  assert.ok(modal.indexOf("max-height:84vh") < modal.indexOf("max-height:84dvh"),
    "the dvh declaration must come second to win where supported");
  assert.ok(modal.includes("overflow-y:auto"), "the scroll pane");
  assert.ok(modal.includes("pointer-events:auto"), "scrollable — and panel taps no longer dismiss");
});
