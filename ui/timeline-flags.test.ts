// Per-session toggles on the timeline lane. History: an EYE (2026-06-22) became a direct-toggle
// checkbox, gained a postal mailbox (2026-06-23) and a notification bell (2026-07-28) — and at THREE
// toggles the icons crowded the lane, so they folded into ONE settings GEAR whose drop-down lists each
// toggle with its icon, state, and a plain-language line (the user 2026-07-28, round 3 — superseding
// the 2026-06-22 "no menu" rule, which held for a single flag). The timeline has no headless render
// harness for the lane header, so — like timeline-view.test.ts — pin the wiring at the source level
// against the shared ui/romp-timeline-view.js (the same file the web dashboard serves verbatim).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("ONE gear column sits between the name and the model (live lanes only)", () => {
  assert.match(SRC, /const eyeColX = PADL \+ Math\.ceil\(maxName\) \+ COLGAP;/);
  assert.match(SRC, /const modelColX = eyeColX \+ \(anyLive \? EYE_W \+ EYE_GAP : 0\);/);
  // the three separate icon columns are gone
  assert.doesNotMatch(SRC, /const mailColX/, "the mailbox column folded into the gear");
  assert.doesNotMatch(SRC, /const bellColX/, "the bell column folded into the gear");
  assert.match(SRC, /if \(s\.live\) \{[\s\S]*?gearIcon\(gcx, gcy, MODEL_FG\)/);
});

test("the gear is DRAWN (hollow toothed ring) and opens the menu on POINTERDOWN (redraw-proof)", () => {
  assert.match(SRC, /function gearIcon\(cx, cy, color\)/);
  // hollow: no hub dot (the user 2026-07-28), matching the ⛭ the rail's settings button wears
  const gear = SRC.slice(SRC.indexOf("function gearIcon"), SRC.indexOf("const LANE_TOGGLES"));
  assert.doesNotMatch(gear, /fill: color/, "the centre stays empty");
  assert.match(gear, /r: 3\.9, fill: 'none'/);
  assert.match(SRC, /const ghit = el\('rect', \{[^}]*fill: 'transparent', 'pointer-events': 'all'/);
  // pointerdown, not click: a lane redraw between mousedown and mouseup replaced the hit-rect so a
  // plain 'click' never fired (the original direct-toggle lesson, 2026-06-23) — the menu opens on press
  assert.match(SRC, /ghit\.addEventListener\('pointerdown', \(e\) => \{[\s\S]*?this\._openLaneMenu\(s, ghit\);/);
  assert.match(SRC, /Session settings<div style='opacity:\.65;margin-top:2px'>feed cards, postal service, notifications<\/div>/);
});

test("the menu lists all three toggles with icons, state words, and plain-language explanations", () => {
  assert.match(SRC, /const LANE_TOGGLES = \[/);
  assert.match(SRC, /\{ flag: 'hideFromFeed', label: 'Feed cards', icon: feedCheckIcon,/);
  assert.match(SRC, /\{ flag: 'postalServiceOff', label: 'Postal service', icon: mailboxIcon,/);
  assert.match(SRC, /\{ flag: 'notify', label: 'Notifications', icon: bellIcon,/);
  // each row explains itself (the user asked for an explanation per toggle, not bare labels)
  assert.match(SRC, /its prompts make cards on the feed; off, the lane stays here but new prompts mint none/);
  assert.match(SRC, /visible to peer sessions, can send and receive their messages; off = fully isolated/);
  assert.match(SRC, /system notification when its work blocks on you or completes/);
  // polarity is encoded per toggle: two off-flags, one on-flag
  assert.match(SRC, /enabled: \(s\) => !s\.hideFromFeed, value: \(enable\) => !enable,/);
  assert.match(SRC, /enabled: \(s\) => laneBell\(s\)\.fullOn, value: \(enable\) => enable,/);   // the session's own setting, else the defaults (bell-follow.ts sessionBell)
  // the bell's sub-line is the session bell's own: its setting or the defaults, and what that means (executed below)
  assert.match(SRC, /desc: \(s\) => laneBell\(s\)\.sub \},/);
  assert.match(SRC, /const sub = body\.createDiv\(\{ text: \(typeof t\.desc === 'function' \? t\.desc\(s\) : t\.desc\) \+ \(legacyBell/);
});

test("menu rows toggle with the SAME optimistic + sticky + reconcile-before-draw treatment as the old icons", () => {
  assert.match(SRC, /const next = t\.value\(!on\);/);
  assert.match(SRC, /\(this\._pendingFlags\[s\.id\] = this\._pendingFlags\[s\.id\] \|\| \{\}\)\[t\.flag\] = next;/);
  assert.match(SRC, /this\._setSessionFlag\(s, t\.flag, next, asGiven\);\s*\n\s*this\._reconcilePendingFlags\(\);\s*\n\s*this\.draw\(\);/);
  // the panel STAYS OPEN and repaints in place — it's a settings panel, not a command
  assert.match(SRC, /this\.draw\(\);\s*\n\s*build\(\);/);
  // inside clicks must not reach the document-level closer that dismisses the menu
  assert.match(SRC, /menu\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
});

test("the gear menu closes on outside click / Escape / teardown, alongside the meta menu", () => {
  assert.match(SRC, /_closeLaneMenu\(\) \{\s*\n\s*if \(!this\._laneMenu\) return;\s*\n\s*this\._laneMenu\.remove\(\); this\._laneMenu = null;/);
  assert.match(SRC, /this\._onDocClick = \(\) => \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \};/);
  assert.match(SRC, /if \(e\.key === 'Escape'\) \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \}/);
});

test("the icon drawers survive (they render inside the menu now): ON = romp blue, OFF = slashed gray", () => {
  assert.match(SRC, /function feedCheckIcon\(off, cx, cy, color\)/);
  assert.match(SRC, /function mailboxIcon\(off, cx, cy, color\)/);
  assert.match(SRC, /function bellIcon\(off, cx, cy, color\)/);
  assert.match(SRC, /const ROMP_BLUE = '#9cd2ff';/);
  assert.match(SRC, /t\.icon\(!on, 8\.5, 8\.5, on \? ACCENT : MODEL_FG\)/);   // ACCENT = ROMP_BLUE in dark; clay under body.theme-light
});

test("setSessionFlag posts via the web host hook, and through the kernel's POST /flag in Obsidian", () => {
  assert.match(SRC, /_setSessionFlag\(s, flag, value, asGiven\)/);   // asGiven: a linked kernel's bell (bell-follow.ts)
  assert.match(SRC, /window\.__rompTimelineSetFlag === 'function'/);
  assert.match(SRC, /window\.__rompTimelineSetFlag\(s\.id, flag, value\)/);
  assert.match(SRC, /this\._kernelPost\('\/flag', \{ id: s\.id, flag, value: !!value \}\)/, "Obsidian: the kernel's setter, through its route");
  assert.doesNotMatch(SRC, /writeFileSync|renameSync/, "the panel writes none of the kernel's state files itself (2026-09-08)");
});

test("the Obsidian writer is the kernel's route, never a file of its own (2026-09-08; PR #1020's fallback superseded)", () => {
  // Pinned on the writer's OWN slice. PR #1020 gave the old fallback writer the discipline of its two
  // siblings (the Electron guard, the kernel's state root, tmp + rename); this change removes the
  // fallback: the panel was a SECOND WRITER of session-flags.json with no lock against the kernel's own
  // setter, and now posts through it, with the kernel's refusal landing in the settingRefused door the
  // lane gear already renders from. Kernel down → the gesture is refused and says so, never written to a
  // file the kernel cannot check. The writer is RUN from tests/test_kernel_session_flags.py
  // (TimelineFlagWriterPostsThroughTheKernel) and ui/timeline-kernel-post.test.ts.
  const w = SRC.slice(SRC.indexOf("_setSessionFlag(s, flag, value, asGiven) {"), SRC.indexOf("_kernelHost() {"));
  assert.ok(w.length > 0 && w.length < 4000, "the writer's slice");
  assert.match(w, /if \(!this\._kernelHost\(\)\) return;/, "Electron-or-nothing, through the one shared guard");
  assert.match(w, /this\._kernelPost\('\/flag'/);
  assert.match(w, /this\.settingRefused\(\{ gesture: 'flag', sid: s\.id, flag, value: painted,/, "a refusal takes the door the socket op's refusal takes");
  assert.doesNotMatch(w, /readFileSync|writeFileSync|renameSync|session-flags\.json'/, "no file of its own, read or written");
  const h = SRC.slice(SRC.indexOf("_kernelHost() {"), SRC.indexOf("_kernelPost(route, body) {"));
  assert.match(h, /if \(typeof process === 'undefined' \|\| !process\.versions \|\| !process\.versions\.electron\) return null;/,
    "a bare-node run never posts");
  assert.match(h, /const base = process\.env\.XDG_STATE_HOME \|\| path\.join\(os\.homedir\(\), '\.local', 'state'\);\s*\n\s*const root = process\.env\.ROMP_STATE_DIR \|\| path\.join\(base, 'romp'\);/,
    "the kernel's state root, resolved as the kernel resolves it — where its serve-token and serve-port records are (the exact line tests/test_state_dir_override.py pins)");
});

test("the sticky-flag machinery survives: pendingFlags reconcile on every update (no flicker-back)", () => {
  assert.match(SRC, /this\._pendingFlags = \{\};/);
  assert.match(SRC, /this\.data = data;\s*\n(?:[^\n]*\n){0,4}\s*this\._reconcilePendingFlags\(\);/);
  // a bell click confirms on the session's own setting and holds by it (laneBell), local and linked alike; a legacy linked
  // kernel's on notify, and every other flag as it always did
  assert.match(SRC, /_reconcilePendingFlags\(\) \{[\s\S]*?const rawBell = flag === 'notify' && s\.notifyRaw !== undefined;\s*\n\s*const cur = rawBell \? s\.notifyRaw : s\[flag\];\s*\n\s*if \(cur === p\[flag\]\) delete p\[flag\];[\s\S]*?else \{ s\[flag\] = p\[flag\]; if \(rawBell\) s\.notifyRaw = p\[flag\]; \}/);
});

test("every timeline dot's white border is thin (0.75px) — romp + user dots alike (the user 2026-06-23)", () => {
  // the shared dot() helper strokes #e8eef5 at 0.75 (was 1.5) for EVERY dot — prompt dots, the romp swirl
  // dot, etc. (r is lit-conditional since 2026-07-17: a cross-lit dot draws grown in its own color.)
  assert.match(SRC, /el\('circle', \{ cx, cy, r: lit \? DOT_R \+ 2 : DOT_R, fill: color, stroke: PAL\(\)\.dotRing, 'stroke-width': 0\.75 \}\)/);
  assert.doesNotMatch(SRC, /stroke: '#e8eef5', 'stroke-width': 1\.5/, "the old 1.5px dot border is gone");
});

test("the lane toggle hands the kernel a JSON boolean", () => {
  // the sender: the web host hook coerces to a real boolean before posting, never a string. The
  // receiver's side (setSessionFlag refuses a non-boolean on the settingRefused frame this page renders,
  // writes nothing, and no handler coerces with bool()) is pinned in the kernel's own lane,
  // tests/test_kernel_session_flags.py WsFlagsMustBeBooleans, by driving the dispatcher rather than
  // reading kernel.py as text from here (review find, 2026-09-08: a cross-lane source pin fails the
  // extension build on a kernel edit that keeps the behaviour).
  const BOOT = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "timeline-boot.ts"), "utf8");
  assert.match(BOOT, /__rompTimelineSetFlag: \(id: string, flag: string, value: unknown, asGiven\?: unknown\) =>\s*\n\s*post\(asGiven === true \? \{ type: "setSessionFlag", id, flag, value: !!value, asGiven: true \} : \{ type: "setSessionFlag", id, flag, value: !!value \}\)/);
});

// ── the session bell (2026-10-11): the user's own setting beats the defaults ─────────────────────────────────────────
// EXECUTED on the real view: laneBell (the twin of bell-follow.ts sessionBell), the pending reconcile and the refusal. The
// gear's rows themselves are opened and clicked in timeline-views-ack.test.ts, which carries the fake DOM. Synthetic ids.
const view = createRequire(__filename)(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"));
const LOCAL = "11111111-2222-3333-4444-555555555555";
const LINKED = "TESTHOST:" + LOCAL;
const EVERYTHING = "when a turn you started ends, or it blocks on you or completes";

test("executed: the gear's bell reads the session's own setting first, then this kernel's master and turn-finished switch", () => {
  const { laneBell, TimelinePanel } = view;
  const panel = Object.create(TimelinePanel.prototype);
  panel.setNotifySwitches({ type: "notifySwitches", all: false, turns: false });
  assert.deepEqual(laneBell({ id: LOCAL, notify: true, notifyRaw: true }), { fullOn: true, explicit: true, legacy: false, sub: "your setting: " + EVERYTHING }, "an explicit on stands with both switches off");
  panel.setNotifySwitches({ type: "notifySwitches", all: true, turns: true });
  assert.deepEqual(laneBell({ id: LINKED, notify: false, notifyRaw: false }), { fullOn: false, explicit: true, legacy: false, sub: "your setting: no notifications" }, "an explicit off stands with both on");
  assert.deepEqual(laneBell({ id: LOCAL, notify: true, notifyRaw: null }), { fullOn: true, explicit: false, legacy: false, sub: "default: " + EVERYTHING });
  panel.setNotifySwitches({ type: "notifySwitches", all: true, turns: false });
  assert.deepEqual(laneBell({ id: LOCAL, notify: true, notifyRaw: null }), { fullOn: false, explicit: false, legacy: false, sub: "default: when it blocks on you or completes" },
    "master on, turns off: not everything, so the row offers on and its click opts the session in");
  panel.setNotifySwitches({ type: "notifySwitches", all: false, turns: true });
  assert.equal(laneBell({ id: LOCAL, notify: false, notifyRaw: null }).sub, "default: no notifications");
  // a linked kernel on an older romp sends no setting: the bell it reports, today's wording (the row adds whose switch)
  assert.deepEqual(laneBell({ id: "boxb:" + LOCAL, notify: true }), { fullOn: true, explicit: false, legacy: true, sub: "system notification when its work blocks on you or completes" });
});

test("executed: a gear bell click is pending until the frame's own setting is the clicked value, for a local lane too", () => {
  const { TimelinePanel } = view;
  const panel = Object.create(TimelinePanel.prototype);
  panel.setNotifySwitches({ type: "notifySwitches", all: true, turns: false });
  // no setting under the master: the effective value is already on when the click stores on
  panel._pendingFlags = { [LOCAL]: { notify: true } };
  panel.data = { sessions: [{ id: LOCAL, notify: true, notifyRaw: null }] };
  panel._reconcilePendingFlags();
  assert.deepEqual(panel._pendingFlags, { [LOCAL]: { notify: true } }, "the effective value agreeing confirms nothing");
  assert.equal(panel.data.sessions[0].notifyRaw, true, "the frame's copy carries the clicked setting meanwhile");
  assert.equal(view.laneBell(panel.data.sessions[0]).fullOn, true);
  panel.data = { sessions: [{ id: LOCAL, notify: true, notifyRaw: true }] };
  panel._reconcilePendingFlags();
  assert.deepEqual(panel._pendingFlags, {}, "the stored setting confirms it");
  // a legacy linked lane (no setting) confirms on notify, as it always did
  const OLD = "boxb:" + LOCAL;
  panel._pendingFlags = { [OLD]: { notify: false } };
  panel.data = { sessions: [{ id: OLD, notify: true }] };
  panel._reconcilePendingFlags();
  assert.deepEqual([panel._pendingFlags, panel.data.sessions[0].notify, "notifyRaw" in panel.data.sessions[0]], [{ [OLD]: { notify: false } }, false, false]);
  panel.data = { sessions: [{ id: OLD, notify: false }] };
  panel._reconcilePendingFlags();
  assert.deepEqual(panel._pendingFlags, {});
});

test("executed: a refused gear bell takes its own setting back from the refusal frame when the frame carries it", () => {
  const { TimelinePanel } = view;
  const g: any = globalThis;
  const prev = g.window;
  g.window = { parent: { postMessage() {} } };
  try {
    const panel = Object.create(TimelinePanel.prototype);
    panel._pendingFlags = { [LOCAL]: { notify: true } };
    panel.data = { sessions: [{ id: LOCAL, notify: true, notifyRaw: true }] };   // the click's optimistic copy
    panel._laneMenu = { _sid: LOCAL, _session: { id: LOCAL, notify: true, notifyRaw: true } };
    panel._laneMenuBuild = () => {}; panel.draw = () => {};
    panel.settingRefused({ type: "settingRefused", gesture: "flag", sid: LOCAL, flag: "notify", value: true, notifyRaw: null, text: "couldn't save that setting" });
    assert.deepEqual(panel._pendingFlags, {});
    assert.equal(panel.data.sessions[0].notifyRaw, null, "the frame's session: no setting, as the kernel still holds");
    assert.equal(panel._laneMenu._session.notifyRaw, null, "…and the copy the open gear built from");
    // a refusal frame without the setting leaves it alone
    panel.data.sessions[0].notifyRaw = true;
    panel.settingRefused({ type: "settingRefused", gesture: "flag", sid: LOCAL, flag: "notify", value: true, text: "x" });
    assert.equal(panel.data.sessions[0].notifyRaw, true);
  } finally { if (prev === undefined) delete g.window; else g.window = prev; }
});
