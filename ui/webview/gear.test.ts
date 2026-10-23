// The settings gear moved from kernel-inline strings into the shared feed
// bundle (gear.js + feed.css's gear section) so both hosts render the SAME
// modal (the user 2026-07-13). These pins keep that single-source shape:
// undoing the extraction, or adding a host-blind fetch/post, breaks here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const KERNEL = read("bin", "romp-kernel");
const GEAR = read("ui", "webview", "gear.js");
const FEED = read("ui", "webview", "feed.ts");
const GEAR_CSS = read("ui", "webview", "gear.css");
const EXT = read("vscode-extension", "src", "extension.ts");

test("the kernel no longer carries an inline gear (single source: the feed bundle)", () => {
  for (const twin of ["_GEAR_CSS", "_GEAR_JS", "_gear_html"])
    assert.ok(!KERNEL.includes(twin), `${twin} must stay deleted from the kernel`);
});

test("the feed bundle builds and wires the gear where it hosts it (VS Code's feed panel), the settings page always", () => {
  assert.ok(FEED.includes('require("./gear.js")'), "feed.ts must load the gear module");
  assert.ok(FEED.includes("if (hostsGear(window)) initGear("), "feed.ts inits the gear on its kernel channel, unless the kernel's feed page said the gear is on /settings");
  const PAGE = read("ui", "webview", "settings-page.ts");
  assert.ok(PAGE.includes('require("./gear.js")') && PAGE.includes("initGear("), "the settings page is the dashboard's gear host");
  assert.ok(GEAR.includes("module.exports = { initGear }"));
});

test("the gear opens on the shared {romp:'openSettings'} message on BOTH hosts", () => {
  assert.ok(GEAR.includes("e.data.romp === 'openSettings'"), "gear must listen for the open message");
  assert.ok(KERNEL.includes("openSettings"), "the web shell's rail must still post the open message");
  assert.ok(EXT.includes('{ romp: "openSettings" }'), "the VS Code menu must post the open message");
});

test("every gear fetch routes through the kernel base + token (VS Code's webview origin is synthetic)", () => {
  assert.ok(!/fetch\(['"`]\//.test(GEAR), "no bare same-origin fetches in gear.js — use ku()");
  assert.ok(!/fetch\(kb\(\) \+/.test(GEAR), "kb()-only fetches skip the serve token — use ku()");
  const kuFetches = GEAR.match(/fetch\(ku\(/g) || [];
  assert.ok(kuFetches.length >= 4, `expected the /palette, /models, /version, /analytics fetches via ku(), got ${kuFetches.length}`);
  assert.ok(EXT.includes("window.__rompKernelBase="), "the VS Code feed builder must inject the base");
  assert.ok(EXT.includes("window.__rompKernelToken="), "the VS Code feed builder must inject the serve token (the kernel gates every request, loopback included)");
  assert.ok(EXT.includes("connect-src ${kernelBase}"), "the feed webview CSP must allow the kernel origin");
});

test("the gear posts kernel ops through ONE shared channel (never re-acquires the VS Code API)", () => {
  assert.ok(!GEAR.includes("acquireVsCodeApi"), "a second acquire throws in a real webview");
  for (const op of ["setAutoNudge", "setJudgeModel", "setIndexModel", "setJudgeEffort", "setIndexEffort", "setJudgeConcurrency",
    "setDistillModel", "setDistillEffort", "setCommentModel", "setCommentEffort", "setCommentFast",
    "setJudgeFast", "setDistillFast", "setIndexFast", "setFileEditing", "setColormap", "setPalette", "setDefaultDir", "browseDir"])
    assert.ok(GEAR.includes(`'${op}'`), `gear must post ${op}`);
});

test("Fast mode for the judges is a kernel setting: a stamped emitter under its own store name, a toast name, a mixed mark, and it follows to every machine", () => {
  assert.ok(GEAR.includes("post({ type: 'setJudgeFast', enabled: jf.checked, gt: gclock.stamp('judge-fast') })"),
    "the click posts the kernel's designed message with the gesture stamp minted in the literal");
  assert.ok(GEAR.includes("jf.checked = v.judgeFast === 'on'"),
    "the checkbox shows the kernel's persisted answer (RAW on/off), never a page default");
  assert.match(GEAR, /STALE_LABELS = \{[\s\S]*?'judge-fast': 'Fast mode \(triage judges\)'/,
    "a stood-down gesture toasts under the control's own name (T300: one box per tier, named by its tier)");
  assert.match(GEAR, /STALE_TYPE = \{[\s\S]*?'judge-fast': 'setJudgeFast'/,
    "the store maps to its message type (the toast's Apply anyway whitelist)");
  assert.match(GEAR, /\['judgeFast', jf\]/, "the row carries the mixed mark where machines disagree");
  const FED = read("ui", "webview", "federation.ts");
  const setSrc = FED.match(/const KERNEL_SETTING = new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(setSrc && setSrc[1].includes('"setJudgeFast"'),
    "one click writes every attached kernel, like the other judge settings");
});

test("the judges' fast-mode box sits on the Triage model row in the chat's words, and greys when no tier is on Opus", () => {
  // The user 2026-09-10: say Fast mode, the word the chat's statusline badge and its tip already use
  // (render.ts FAST_CHOICES / "toggle fast mode"), never a coinage of the gear's own; and put the box
  // with the model rather than on a row of its own under a paragraph. The ids and the message stay,
  // so the kernel side and the cross-machine propagation are untouched.
  assert.ok(!GEAR.includes("Fast judging"), "the old label is gone from the gear");
  assert.match(GEAR, /<select id=rs-judgemodel><\/select>" \+[\s\S]{0,600}?<label class=rs-fastin id=rs-judgefast-wrap><input type=checkbox id=rs-judgefast>Fast mode<span class=rs-mixed hidden><\/span>/,
    "the box follows the triage picker inside the same row, with its own mixed mark");
  assert.ok(GEAR.includes("<span class=rs-sub id=rs-judgefast-sub>"), "its hint is a row hint like its neighbours', not a paragraph");
  assert.match(GEAR, /var JUDGEFAST_SUB = "[^"]*Opus-only[^"]*premium/, "the one-line hint carries the Opus-only condition and the premium");
  // the gate, mirroring cmtFastGate: the opt-in rides only a call whose model is Opus, so with no tier on
  // Opus the box is inert; the gear greys it and the hint says why, instead of a dead control
  assert.ok(GEAR.includes("function judgeFastGate"), "the availability gate must exist");
  // T300: one box per tier, each greyed on ITS tier's effective model (gear-judge-fast.test.ts holds the rest)
  assert.ok(GEAR.includes("t.box.disabled = !can;"), "a box disables when its tier is not on Opus");
  assert.ok(GEAR.includes("sub.textContent = !can ? JUDGEFAST_SUB_OFF : (r ? judgeFastSubRefused(t.word, r) : JUDGEFAST_SUB)"), "the hint says why while greyed");
  assert.ok(GEAR.includes("v === 'triage' ? (jm ? (jm.value || '') : '') : v"), "distilling on Follow triage counts as the triage pick");
  for (const store of ["judge-model", "index-model", "distill-model"])
    assert.match(GEAR, new RegExp("gclock\\.stamp\\('" + store + "'\\) \\}\\); judgeFastGate\\(\\);"), `a ${store} pick re-runs the gate`);
  assert.match(GEAR, /cmtFastGate\(false\);\n\s*judgeFastGate\(\);/, "fill() re-checks availability after the tiers are set");
  // the mixed mark is the one inside the box's own label, not the picker's (both share the row now)
  assert.ok(GEAR.includes("el.closest('label') || el.closest('.rs-row')"), "fillMixedMarks scopes to the control's own label first");
  const CSS = read("ui", "webview", "gear.css");
  // the greyed look fades the BOX alone: opacity on the whole label faded the hint span inside it too, and made the
  // label a stacking context the rows beneath paint over, so the reason the box was greyed read dim and overdrawn
  assert.ok(CSS.includes("#rsettings .rs-fastin.rs-off input { opacity: .4;"), "the greyed look fades the box alone");
  assert.doesNotMatch(CSS, /#rsettings \.rs-fastin\.rs-off \{[^}]*opacity/,
    "no opacity on the label: the hint span lives inside it and would fade with it, and the label would become a stacking context the rows beneath paint over");
  assert.match(CSS, /#rsettings \.rs-fastin\.rs-off \{[^}]*color: var\(--text-faint/, "the word greys by token, not by fading");
  assert.ok(CSS.includes("#rsettings .rs-row:has(.rs-fastin:hover) > .rs-sub { display: none; }"), "one hover description at a time");
  assert.ok(CSS.includes("#rsettings .rs-fastin .rs-sub { white-space: normal; }"), "the hint wraps: the label's nowrap (box + word on one line) must not reach the hint, or it runs off the card");
  assert.ok(CSS.includes("#rsettings .rs-row:has(.rs-fastin .rs-mixed:hover) .rs-fastin .rs-sub { display: none; }"), "the box's mixed mark keeps its title alone: no hint under it");
});

test("EVERY queued-class kernel setting is emitted with its gesture time (completeness-pinned to federation's own set)", () => {
  // federation queues KERNEL_SETTING sends per host across a down socket and flushes them on
  // reconnect (federation-send-queue.test.ts), and the kernel orders applies by `gt`, standing
  // stale stamps down — so the stamp must be minted at the CLICK, inside the message literal
  // itself, never at send or flush time (a late flush must carry the original gesture's time).
  // This pin reads federation.ts's ACTUAL KERNEL_SETTING set rather than enumerating literals:
  // an earlier six-literal enumeration let three members (setJudgeEffort, setIndexEffort,
  // setUpdateMode) ship unstamped, and an unstamped member is WORSE than the pre-gt world — its
  // stale flush takes the no-stamp compat path, records a forged-fresh arrival stamp, and the
  // judge tiers fan that stamp mesh-wide over genuinely newer gestures. Adding a member without
  // a stamped emitter goes red here: zero emitters found, or an emitter literal without gt.
  // The stamp is minted through the gesture clock (gesture-clock.js) under the emitter's own STORE
  // name — stamp('judge-model') for setJudgeModel — so it lands above every stamp the page has seen
  // for that store; a bare Date.now() is the bug the clock replaced (a device whose clock runs ahead
  // stamps every store into the future and locks every other device out until the skew elapses).
  // STALE_TYPE (gear.js) is the store→type map, the same one the stale toast's Apply anyway trusts.
  const FED = read("ui", "webview", "federation.ts");
  const setSrc = FED.match(/const KERNEL_SETTING = new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(setSrc, "federation.ts's KERNEL_SETTING set located");
  const members = Array.from(setSrc![1].matchAll(/"([A-Za-z]+)"/g)).map((m) => m[1]);
  assert.ok(members.length >= 9, `the set parsed (${members.length} members)`);
  const typeSrc = GEAR.match(/var STALE_TYPE = \{([\s\S]*?)\};/);
  assert.ok(typeSrc, "gear.js's STALE_TYPE map located");
  const storeType: Record<string, string> = {};
  for (const m of typeSrc![1].matchAll(/'([a-z-]+)':\s*'(set[A-Za-z]+)'/g)) storeType[m[1]] = m[2];
  for (const type of members)
    assert.ok(Object.values(storeType).includes(type), `STALE_TYPE names a store for ${type}`);
  // scan every webview source that could emit one (the gear, the file viewer's consent post,
  // the feed's judge-limit switch, and any future emitter under ui/webview)
  const srcDir = path.join(ROOT, "ui", "webview");
  const sources = fs.readdirSync(srcDir)
    .filter((f) => (f.endsWith(".ts") || f.endsWith(".js")) && !f.endsWith(".test.ts") && f !== "federation.ts")
    .map((f) => ({ f, text: fs.readFileSync(path.join(srcDir, f), "utf8") }));
  for (const type of members) {
    let emitters = 0;
    for (const { f, text } of sources) {
      const re = new RegExp(`\\{\\s*type:\\s*['"]${type}['"][^}]*\\}`, "g");
      for (const lit of text.match(re) || []) {
        emitters++;
        const stamp = lit.match(/\bgt:\s*gclock\.stamp\(['"]([a-z-]+)['"]\)/);
        assert.ok(stamp, `${f} must stamp the gesture through the clock inside the ${type} message literal itself: ${lit}`);
        assert.equal(storeType[stamp![1]], type, `${f} stamps ${type} under its own store name: ${lit}`);
        assert.doesNotMatch(lit, /Date\.now\(\)/, `${f}: the bare wall clock is the bug the clock replaced: ${lit}`);
      }
    }
    assert.ok(emitters >= 1,
      `a stamped emitter exists for ${type} — a queued setting nobody stamps rides the compat path with a forged-fresh stamp`);
  }
});

test("model/effort options are written exactly once — the single-flight /models fetch (2026-08-30)", () => {
  // The user's "my Distilling pick keeps resetting": fillChoices memoized its RESULT, so a settings
  // open racing the page-load fetch fired a second /models fetch, and whichever resolved last
  // REWROTE every select's options AFTER fill() had set their values — a rewritten select falls
  // back to its first option ("Follow triage" for distill; invisible on Triage model, whose first
  // option coincides with the stored value). The promise is the memo now; a failed fetch clears it.
  assert.ok(GEAR.includes("var choicesP = null;"), "the promise is the memo");
  assert.ok(GEAR.includes("if (choicesP) return choicesP;"), "second callers reuse the in-flight fetch");
  assert.ok(GEAR.includes("choicesP = null; return null;"), "a failed fetch clears the memo for retry");
  assert.ok(!GEAR.includes("if (choices) return Promise.resolve(choices);"), "the result-memo race is gone");
});

test("the login flow lives in a modal behind one button (the user 2026-08-30)", () => {
  // "it would just be a login button… then it would pop up another modal that says paste the code
  // so it doesn't always sit there taking up space" — the paste-code UI collapses behind the
  // Account button; the modal wears the panel treatment (centered card over a dimmed backdrop).
  assert.ok(GEAR.includes("id=rs-login-modal"), "the modal exists");
  assert.ok(GEAR.includes("Log in to Claude Code"), "the button says what it does");
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.css"), "utf8");
  assert.match(CSS, /#rs-login-modal \{ position: fixed; inset: 0; z-index: \d+; background: var\(--overlay-dim, rgba\(0, 0, 0, 0\.55\)\);/);   // tokened with its literal fallback (2026-08-30 merge)
  assert.match(CSS, /\.rs-login-card \{ background: var\(--surface-raised, #252526\); border: 1px solid rgba\(255,255,255,0\.12\);/);
  // mid-flow the button REOPENS the modal, never restarts the flow; terminal state closes it;
  // Cancel + Escape + backdrop close; the code input stays a pure pass-through (T157)
  assert.ok(GEAR.includes("if (lgLive === 'url' || lgLive === 'starting' || lgLive === 'verifying') return;"));
  // a terminal state closes it unless it is the outcome this dialog followed, which it shows (the test below)
  assert.ok(GEAR.includes("if (!(lgOk && !lgOk.hidden)) lgModal(false);"));
  // every wait inside the dialog says what it waits on beside the loader's dots, and the kernel's err (a refusal in
  // the CLI's words, or its complaint about a half-copied code) shows in the dialog itself (2026-09-30)
  assert.ok(GEAR.includes("id=rs-login-status"), "the dialog's own status line");
  assert.ok(GEAR.includes("'Starting Claude Code…'") && GEAR.includes("'Checking the code…'"));
  assert.match(CSS, /#rs-login-modal \.rs-login-dots i \{[^}]*background: var\(--accent, #9cd2ff\);/, "the loader's accent dots");
  assert.ok(CSS.includes("#rs-login-modal .rs-login-code[hidden] { display: none; }") && GEAR.includes("<div id=rs-login-code class=rs-login-code hidden>"),
            "the paste row's flex must not defeat [hidden]: it shows only while a code is awaited");
  assert.ok(GEAR.includes("lgE.classList.toggle('bad', f.state === 'error' || f.state === 'url');"));
  assert.ok(GEAR.includes("if (e.key === 'Escape' && lgM && !lgM.hidden) lgModal(false);"));
  assert.ok(GEAR.includes("if (e.target === lgM) lgModal(false);"));
  assert.ok(GEAR.includes("post({ type: 'loginCode', code: code });"), "pass-through untouched");
});

test("a successful login says so: the dialog's outcome with Close, the row at rest names the login and its machine (2026-09-30)", () => {
  // The user signed in and the dashboard read exactly as before: the dialog closed itself on done, and the only place naming
  // the login was the row's hover popover. The behaviour is walked in gear-login-signed-in-browser.test.ts and, on the real
  // kernel with a fake CLI, in tests/test_login_signed_in_served.py; these pins hold the shape where no browser runs.
  assert.ok(GEAR.includes("<div id=rs-login-ok class=rs-login-ok hidden></div>"), "the dialog has an outcome line, hidden until done");
  assert.ok(GEAR.includes("<button id=rs-login-close type=button hidden"), "…and a Close beside it");
  assert.ok(GEAR.includes("lgSuccess(who ? 'Signed in as ' + who : 'Signed in.');"), "done in the flow this dialog followed says who");
  assert.ok(GEAR.includes("if (on) { lgU.hidden = true; lgC.hidden = true; lgStatus({ state: 'done', err: '' }); }"),
    "the link, the paste box and every wait go with the outcome");
  assert.ok(GEAR.includes("if (lgX) lgX.hidden = on;"), "Close stands in Cancel's place");
  assert.ok(GEAR.includes("if (lgClose) lgClose.addEventListener('click', function () { lgModal(false); });"), "Close cancels nothing");
  assert.ok(!GEAR.includes("if (!f.state || f.state === 'done') lgModal(false);"), "done no longer closes the dialog out from under the person");
  // a read in flight across the start carries the previous flow's number, and its "done" is never announced as this flow's
  assert.ok(GEAR.includes("if (lgFrom !== null && n !== null && n <= lgFrom) {"));
  assert.ok(GEAR.includes("lgFrom = lgN; lgStale = 0; lgSaw = false;"));
  // the row at rest: a permanent line (rs-line, not the rs-sub popover), the machine named beside other kernels, the same flow
  assert.ok(GEAR.includes(`"<span id=rs-login-who class='rs-line rs-login-who'></span>" +`), "the signed-in line is permanent");
  assert.ok(GEAR.includes("var line = who ? (many ? 'Signed in on ' + where + ' as ' + who : 'Signed in as ' + who)"));
  assert.ok(GEAR.includes("lgB.textContent = mid ? 'Show login code…' : (who ? 'Sign in again…' : 'Log in to Claude Code');"),
    "a login re-runs the same flow; none keeps today's button");
  // the billing line only from the kernel's reading (/logins' machine block), never guessed
  assert.ok(GEAR.includes("if (m && m.keyHelper === true && m.loginUsable === true) bill = 'An API key is also set up here; a session uses this account only when its Billing is Login.';"));
  // one popover under the pointer: the stored logins' note rides the row's one description (as an rs-sub of its own it
  // floated a second popover over the first), and the list's heading, when there is a list, sits in the flow
  assert.ok(!GEAR.includes("head.className = 'rs-sub';"), "the stored logins' head is no second rs-sub");
  assert.ok(GEAR.includes("+ (lgStored ? ' ' + lgStored : '');"));
  assert.ok(GEAR_CSS.includes("#rsettings .rs-line.rs-login-who { color: var(--fg, #ccc);"), "the signed-in line wears the body ink");
  assert.ok(GEAR_CSS.includes("#rs-login-modal .rs-login-ok { margin-top: 8px; color: var(--fg, #ccc);"));
});

test("the distilling tier is its own gear pair, defaulting to follow-triage", () => {
  // The user 2026-08-14: the card-prose judges (distiller, briefer, staller) split out of triage so
  // what you READ can run a richer model than the placement judges. The stored sentinel "triage"
  // means follow the triage pick live — today's behavior until the user pins — so the selects must
  // lead with that option and fill from the RAW /version value, never the resolved model.
  for (const id of ["rs-distillmodel", "rs-distilleffort"])
    assert.ok(GEAR.includes(id), `the gear must render ${id}`);
  assert.ok(GEAR.includes('"triage">Follow triage'), "the follow-triage option leads both selects");
  assert.ok(GEAR.includes('"none">Default'), "the effort's no-flag pin is the stored sentinel, never ''");
  assert.ok(GEAR.includes("v.distillModel"), "fill() reads the RAW distill model value");
  assert.ok(GEAR.includes("v.distillEffort"), "fill() reads the RAW distill effort value");
});

test("the default-comment trio is its own gear group, defaulting to same-as-the-session", () => {
  // The user 2026-08-29: every new comment thread on one model/effort/fast pick regardless of the
  // session it branches from. The stored sentinel "session" means inherit the parent — today's
  // behavior until the user pins — so the selects lead with it and fill from the RAW /version value.
  for (const id of ["rs-cmtmodel", "rs-cmteffort", "rs-cmtfast"])
    assert.ok(GEAR.includes(id), `the gear must render ${id}`);
  assert.ok(GEAR.includes('"session">Same as the session'), "the sentinel option leads both selects");
  assert.ok(GEAR.includes("v.commentModel"), "fill() reads the RAW comment model value");
  assert.ok(GEAR.includes("v.commentEffort"), "fill() reads the RAW comment effort value");
  assert.ok(GEAR.includes("v.commentFast === 'on'"), "the fast box reflects the stored ask");
  // fast is an Opus-only research preview: a pinned non-Opus comment model disables the box, and a
  // model pick that strands a checked box unchecks it as part of that gesture — never a silent flap
  assert.ok(GEAR.includes("function cmtFastGate"), "the availability gate must exist");
  assert.ok(GEAR.includes("cmtFastGate(true)"), "the model pick runs the gate as a user gesture");
  assert.ok(GEAR.includes("cmtFastGate(false)"), "fill() re-checks availability without posting");
});

test("every kernel-side select says so when connected machines disagree (the autoNudge rule generalized)", () => {
  // The user 2026-08-14: everything stays in sync; on disagreement the gear ASKS by marking the row —
  // hover names the hosts, one pick sets every machine — and a machine that never reported (an older
  // kernel) is unknown, never a disagreement to click away. Since the settings' scope fan-out every
  // such row wears the one "differs" flag, a box shows the mixed state, and the selector counts them all.
  assert.ok(GEAR.includes("fillMixedMarks"), "the generalized reconcile must exist and be called");
  assert.ok(/t\.settings && typeof t\.settings\[key\] !== 'undefined'/.test(GEAR),
    "non-reporting rows are excluded, not read as disagreeing");
  assert.ok(GEAR.includes("mark.title = 'The kernels disagree: ' + vals.join(', ')"), "the hover names the machines and their values");
  assert.ok(GEAR.includes("if (box) mixBox(el);"), "a disagreeing box shows the mixed state, never this kernel's value as everyone's");
  // ONE rule for a click on a mixed box: it turns the setting OFF for every machine in scope, the side that does nothing, and
  // a second click turns it on (the #377 review; #377 had it land on, which for most of these boxes spends or acts). The box
  // holds checked=true under the dash, so the click's toggle lands on off, and the flag's hover ends with what a click does
  // there. gear-mixed-click-browser.test.ts clicks every mixable box in a real browser; these pin the wiring
  assert.ok(GEAR.includes("function mixBox(el) { el.indeterminate = true; el.checked = true; }"), "a mixed box holds TICKED under its dash");
  assert.ok(!GEAR.includes("el.checked = false; }"), "…never unticked, where a click would land on");
  assert.ok(GEAR.includes("mark.title = 'The kernels disagree: ' + vals.join(', ') + '. ' + clickRule(key, isBox, mixedHere);"), "the hover says what a click does");
  assert.ok(GEAR.includes("if (isBox && mixed) return picked ? 'A click turns it off for ' + where + '.'"), "…the rule, in words, under a pick");
  assert.ok(GEAR.includes(": 'A click turns it off for every machine; click again to turn it on.' + (followsScope(key) ? ' Pick a machine above to set it alone.' : '');"),
    "…and under All kernels, with the way back and, on a row that follows the picker, how to set one machine alone");
  assert.ok(!/turns it on for/.test(GEAR), "no copy left that says a click turns a mixed box on");
  assert.ok(GEAR.includes("lastDiffs.push({ label: STALE_LABELS[storeOf(key)] || key, vals: vals });") && GEAR.includes("var diffs = lastDiffs;"),
    "the selector's count counts every disagreeing kernel-side row");
  const mixedSpans = GEAR.match(/class=rs-mixed hidden/g) || [];
  assert.ok(mixedSpans.length >= 10, `every kernel-side select carries a marker span (got ${mixedSpans.length})`);
});

test("a picked kernel that never reports a row (an older version) shows it unknown, never a fake mixed box (the #374 review)", () => {
  // picked alone, an older kernel's Thinking summaries read mixed with no flag and went back to mixed after a click; the served
  // two-kernel lab drives it (tests/test_settings_per_install_scope_served.py), these pin the wiring
  assert.ok(GEAR.includes("if (fillUnknown(el, key)) { if (el.type !== 'checkbox') repaint = true; return; }"), "the per-install rows ask first");
  assert.ok(GEAR.includes("if (box && scopeOn() && !fillUnknown(box, key)) {"), "…and the synchronized rows too");
  assert.ok(GEAR.includes("return connected(h) && !reports(h, key);"), "a kernel that went down is not an older one");
  assert.ok(GEAR.includes("mark.textContent = typeof one === 'undefined' ? 'unknown' : 'unconfirmed';"), "the grey word, never the amber flag");
  assert.ok(GEAR.includes("this screen cannot confirm it applied."), "the hover says a click there cannot be confirmed");
  assert.ok(GEAR.includes("noteSent(m);"), "scoped() remembers what it sent, so the row shows it after a click");
  assert.ok(GEAR_CSS.includes("#rsettings input[type=checkbox].rs-unknown { opacity: .45; }"), "the box dimmed");
});

test("the Auto Nudge box speaks for every attached machine, and says so when they disagree", () => {
  // /version answers for the kernel serving this page only, so the box used to show one machine's
  // setting as everyone's — the user 2026-08-14 unchecked it and the other machine kept nudging with
  // nothing on screen to say so. The reconcile reads each attached row's own setting off /tunnels.
  assert.ok(GEAR.includes("fetch(ku('/tunnels' + q)"), "the box must check the OTHER kernels, not just /version");
  assert.ok(GEAR.includes("mixBox(an);"), "hosts that disagree put the box in the mixed state, whose click turns it off (the one rule)");
  assert.ok(GEAR.includes("rs-autonudge-split"), "…said in words too — a tri-state box alone reads past");
  assert.ok(GEAR_CSS.includes(".rs-mixed"), "…and that marker needs its styling, or it inherits nothing");
  assert.ok(/t\.status === 'up'/.test(GEAR) && /typeof t\.autoNudge === 'boolean'/.test(GEAR),
    "only a CONNECTED host that actually reported a setting counts — never guess for a silent one");
  // The description is the one level down: it names the machines, so it cannot be a frozen literal.
  assert.ok(/asub\.textContent = AUTONUDGE_SUB\s*\n?\s*\+/.test(GEAR) && GEAR.includes("split.join("),
    "the hover line must name who differs");
});

test("the /compact suggestion is a real settings checkbox beside Auto Nudge (the user 2026-09-01)", () => {
  // T208 shipped the kernel toggle with no UI; the user ruled it must be an ordinary settings
  // checkbox next to Auto Nudge — off by default for new installs, one click to turn on.
  assert.ok(GEAR.includes("id=rs-suggestcompact"), "the checkbox exists in the gear markup");
  const auto = GEAR.indexOf("data-pane=automation"), next = GEAR.indexOf("data-pane=tasks");   // the nudges are the Automation tab since T404 (Task tracking before, Automatic before that)
  assert.ok(auto > 0 && next > 0, "both panes exist (indexOf's -1 would pass every order check below)");
  const at = GEAR.indexOf("id=rs-suggestcompact");
  assert.ok(auto < at && at < next, "…in the Automation tab, with Auto Nudge (T379 regrouped the panel into tabs; T400 renamed this one; T404 cut Automation)");
  assert.ok(GEAR.indexOf("id=rs-autonudge") < at && at < next,
    "…directly after Auto Nudge, where the user asked for it");
  assert.ok(/csg\.addEventListener\('change'/.test(GEAR)
    && GEAR.includes("post(scoped({ type: 'setCompactSuggest', enabled: csg.checked, gt: gclock.stamp('compact-suggest') }))"),   // scoped(): the machine selector's pick rides along (phase two)
    "the click posts the kernel's designed setCompactSuggest message — gesture-stamped, like "
    + "every kernel-side setting the gear emits");
  assert.ok(GEAR.includes("csg.checked = !!v.compactSuggest"),
    "…and the box always shows the kernel's persisted answer, never a page default");
  assert.ok(GEAR.includes("['compactSuggest', csg]"),
    "attached machines that disagree get the same mixed mark as every kernel-side setting");
});

test("the gear owns its browseResult (the reply lands in the FEED document, not the chat's)", () => {
  assert.ok(GEAR.includes("'browseResult'") && GEAR.includes("'gear'"));
});

test("gear.css carries the modal styling for every pane that hosts it", () => {
  for (const sel of ["#rsettings", ".rs-card", "#rs-cmap-btn", "#rs-pal-btn", ".ra-openbtn", "#ranalytics"])
    assert.ok(GEAR_CSS.includes(sel), `gear.css must style ${sel}`);
  assert.ok(KERNEL.includes("/dist/gear.css"), "the kernel settings page must link the extracted stylesheet");
});

test("one tooltip per settings row: the Account row's live status is NOT a second rs-sub", () => {
  // the rs-sub CSS floats EVERY rs-sub in a hovered row at the same top:100% box, so a second one
  // stacks a second bordered popover — the 2026-09-02 stacked double tooltip (even empty it painted
  // a box). #rs-login-state is a live inline status, not a description: it wears rs-note.
  assert.ok(GEAR.includes("id=rs-login-state class=rs-note"), "the login status line is an inline note");
  const billing = GEAR.slice(GEAR.indexOf("id=rs-billing"), GEAR.indexOf("id=rs-panes-sec"));   // the Account row leads the General tab, the Panes section next (T379, re-cut T400)
  assert.equal((billing.match(/class=rs-sub/g) || []).length, 1, "the Account row keeps ONE description popover");
  assert.ok(GEAR_CSS.includes("#rsettings .rs-note {") && GEAR_CSS.includes("#rsettings .rs-note:empty { display: none; }"),
    "rs-note is inline, hidden while it has nothing to say");
  // native titles never stack on the row popover: the picker buttons speak through aria-label, and
  // the description stands down while a picker dropdown is open or the 'mixed' mark is hovered
  assert.doesNotMatch(GEAR, /id=rs-cmap-btn type=button title=/);
  assert.doesNotMatch(GEAR, /id=rs-pal-btn type=button title=/);
  assert.ok(GEAR.includes("aria-label='Pick the recency colormap'") && GEAR.includes("aria-label='Pick the session palette'"));
  assert.ok(GEAR_CSS.includes("#rsettings .rs-row:has(#rs-cmap-list:not([hidden])) .rs-sub"), "open cmap menu owns the row");
  assert.ok(GEAR_CSS.includes("#rsettings .rs-row:has(.rs-mixed:hover) .rs-sub { display: none; }"), "the mixed mark's title stands alone");
});

test("the analytics legend swatch matches its bar (PR #886 review: they split in classic)", () => {
  // the sessions BAR moved to var(--text-faint, #7d8590) while the legend swatch stayed a literal —
  // dark resolves --text-faint to #6e7681, so bar and legend no longer agreed in classic
  assert.ok(GEAR.includes('"ra-sw" style="background:var(--text-faint, #7d8590)"'),
    "the legend swatch reads the same token as the bar it explains");
  assert.doesNotMatch(GEAR, /"ra-sw" style="background:#7d8590"/);
});
