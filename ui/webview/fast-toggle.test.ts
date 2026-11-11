// The chat statusline's FAST badge — a fourth meta control (mode · model · effort · fast) toggling the
// CLI's fast mode (/fast, Opus-only research preview). The badge exists only when the session REPORTS a
// fast state — the SDK init's fast_mode_state ("on"/"off"/"cooldown"), threaded kernel → status → badge —
// AND the model can run fast mode at all (fastAvailable: the CLI reports "off" with an EMPTY
// disabled_reason on a non-Opus session, verified 2026-08-10 against 2.1.226 on a fable session, so
// state alone would leave a dead toggle there). The label is ONE WORD (the user 2026-08-10, on a
// phone-width statusline) and the word carries the state: "Fast" on, "Slow" off (the user 2026-08-11 —
// tint alone didn't say which side the toggle was on). Picking On/Off posts setFast; the kernel delivers
// the literal "/fast on|off", which the SDK input stream interprets (its CLI descriptor is marked
// supportsNonInteractive, unlike /model).
// render.ts has no jsdom harness → source pins (kernel pins ride along, as in the other meta tests).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { fastAvailable, modelFastKnown } from "./status-controls";   // the gate itself, runnable: no DOM at module scope

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const MODULE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "status-controls.ts"), "utf8");   // the status line's controls moved here from render.ts (T415 part two)
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDK = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the fast badge appears only when the session reports a state AND the model can run it", () => {
  assert.match(RENDER, /fast\?: string;/);                                   // status carries it
  assert.match(MODULE, /export type MetaKind = "mode" \| "model" \| "effort" \| "fast";/);   // billing moved to the tab menu (2026-08-09); the kinds live with the controls (T415 part two)
  assert.match(MODULE, /st\.fast && fastAvailable\(st\) \? st\.fast : ""/);  // no state / unsupported model → no badge
  assert.match(MODULE, /meta\.appendChild\(metaButton\("fast", prettyFast\(fast\), forSid, hooks\)\)/);   // sid-scoped: the popover statusline shares this builder (2026-08-25)
  // the availability gate: opus-family (or unknown/default — the account default may be Opus)
  const gate = MODULE.match(/function fastAvailable\(st: MetaStatus\): boolean \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(gate, /!m \|\| m === "default" \|\| m\.includes\("opus"\)/);
});

test("the badge label is one word, and the word carries the state: Fast on, Slow off", () => {
  const pf = MODULE.match(/function prettyFast[\s\S]*?\n\}/)?.[0] || "";
  assert.match(pf, /"Cooldown"/);
  assert.match(pf, /s === "on" \? "Fast" : "Slow"/);   // off is not a second "Fast" (the user 2026-08-11)
  assert.doesNotMatch(pf, /Fast on|Fast off/);
});

test("the picker speaks the badge's words — Fast/Slow, never On/Off — and posts setFast", () => {
  // one vocabulary for one toggle (the user 2026-08-11: a badge reading "Slow" opened a menu of
  // "On"/"Off"); the VALUES stay the wire's on/off — only the labels wear the badge's words
  assert.match(RENDER, /\{ label: "Fast", value: "on" \}/);
  assert.match(RENDER, /\{ label: "Slow", value: "off" \}/);
  assert.doesNotMatch(RENDER, /\{ label: "On", value: "on" \}/);
  assert.match(RENDER, /fast: FAST_CHOICES/);
  assert.match(RENDER, /kind === "fast" \? "setFast"/);                      // the pick posts the op
  assert.match(MODULE, /"on" \? "var\(--fast\)" : ""/);                      // ON tint, off/cooldown default
  assert.match(CSS, /--fast: #ff6a00;/);                                     // the CLI's own fastMode orange
});

test("the kernel threads fast_mode_state from the SDK init to the chat status", () => {
  assert.match(SDK, /d\.get\("fast_mode_state"\)/);                          // init is the authoritative source
  assert.match(SDK, /"fast": self\.fast/);                                   // snapshot carries it
  assert.match(KERNEL, /"fast": st\.get\("fast", ""\)/);                     // merged into the live map
  // a disabled_reason (org-gated / unsupported) hides the toggle rather than offering a dead control
  assert.match(KERNEL, /"fast": "" if tm\.get\("fastReason"\) else tm\.get\("fast", ""\)/);
});

test("a fast opt-in waiting for its relaunch wears the badge's dots, never an early Fast (2026-10-01)", () => {
  // the first opt-in relaunches the CLI with the flag, and that relaunch waits until nothing the session runs would be cut;
  // the pick used to flip the badge to Fast at once, while the session still ran without the flag
  assert.match(SDK, /s\._fast_pending = "on"\s+# the badge's dots until a launch carries the flag/);
  assert.doesNotMatch(SDK, /s\.fast = "on"\s+# optimistic for the badge; init re-asserts the truth/, "no early flip at the pick");
  assert.match(SDK, /"fastPending": bool\(getattr\(self, "_fast_pending", ""\)\)/);   // snapshot carries it
  assert.match(KERNEL, /"fastPending": bool\(st\.get\("fastPending"\)\)/);           // merged into the live map
  assert.match(KERNEL, /"fastPending": bool\(tm\.get\("fastPending"\)\)/);           // …and the chat status
  assert.match(RENDER, /fastPending\?: boolean; \}/);
  assert.match(MODULE, /\(kind === "fast" && !!st\.fastPending\)/);
});

test("a refusal answering the user's ask is loud — warn toast, ask cleared, badge restored", () => {
  // Behavior is covered in tests/test_sdk_backend.py; these pins keep the shape findable from the
  // badge's own test. Before this, the CLI's refusal (e.g. extra_usage_disabled) just hid the toggle
  // the user had JUST clicked — a silently vanishing button (the user 2026-08-11, on a phone).
  const adopt = SDK.match(/def _adopt_fast_state[\s\S]*?\n    async def /)?.[0] || "";
  assert.match(adopt, /refused_ask = bool\(reason\) and self\.fast_opt/);
  assert.match(adopt, /_FAST_REFUSALS/);                 // humanized reason in the toast
  assert.match(adopt, /"type": "warn"/);                 // fail loudly, not a vanishing control
  assert.match(adopt, /self\.request_reconnect\(why="fast mode refusal"\)/);    // flagless reconnect → the badge comes back (once nothing would be cut, 2026-10-01)
});

test("setFast is a drive op that parks like /model and /effort", () => {
  assert.match(KERNEL, /"setModel", "setEffort", "setMode", "setFast",/);    // in the drive-op allowlist
  assert.match(KERNEL, /def _set_fast_or_park\(be, sid, value\)/);
  assert.match(KERNEL, /op\[0\] in \("model", "effort", "fast", "env", "cwd"\)/);   // repeat pick replaces in place (env and cwd joined too)
  assert.match(KERNEL, /elif op\[0\] == "fast":/);                           // parked delivery branch
});

// Fast mode is no longer Opus-only (2026-11-11: Claude Code 2.1.321 runs it on Fable 5.2, and retired it for Opus
// 4.8), so the gate reads the KERNEL's word first — st.fastCapable, computed from the CLI's own connect-time report of
// which models support fast mode (kernel model_fast.py; the Opus seed until one lands) — and keeps the spelling rule
// only where the kernel cannot say.
test("the kernel's word on the model gates the badge; the spelling rule stands only without it", () => {
  assert.equal(fastAvailable({ model: "Fable 5.2", fast: "off", fastCapable: true }), true, "a non-Opus model the CLI runs fast shows the toggle");
  assert.equal(fastAvailable({ model: "Opus 4.8", fast: "off", fastCapable: false }), false, "an Opus the CLI retired fast mode for hides it");
  assert.equal(fastAvailable({ model: "Fable 5.2", fast: "off" }), false, "no word (an older kernel): the seed rule, Fable is not Opus");
  assert.equal(fastAvailable({ model: "Opus 5", fast: "off" }), true, "…and Opus is");
  assert.equal(fastAvailable({ model: "Fable 5.2", fast: "off", fastCapable: null }), false, "null is unknown, the same as absent");
  assert.equal(fastAvailable({ model: "", fast: "off", fastCapable: null }), true, "unknown model stays offered: the account default may run fast");
  assert.equal(fastAvailable({ model: "default", fast: "off" }), true);
  // the status carries the field, from the kernel's per-session read (jd.fast_capable_or_unknown over the live id)
  assert.match(RENDER, /fastCapable\?: boolean \| null;/);
  assert.match(MODULE, /fastCapable\?: boolean \| null;/);
  assert.match(KERNEL, /"fastCapable": _fast_capable_status\(tm\),/);
  assert.match(KERNEL, /def _fast_capable_status\(tm\):/);
});

test("the comment dialog's fast chip reads the /models frame's capability before its spelling rule", () => {
  // the frame as the kernel ships it: a family's `fast` is its default's, each version carries its own
  const choices = [
    { label: "Fable", value: "fable", fast: true, versions: [{ label: "Fable 5.2", value: "claude-fable-5-2", fast: true }, { label: "Fable 5.1", value: "claude-fable-5-1", fast: null }] },
    { label: "Opus", value: "opus", fast: true, versions: [{ label: "Opus 5.5", value: "claude-opus-5-5", fast: true }, { label: "Opus 4.8", value: "claude-opus-4-8", fast: false }] },
    { label: "Sonnet", value: "sonnet", fast: null, versions: [] },
    { label: "Default", value: "default" }];
  assert.equal(modelFastKnown(choices, "fable"), true, "a family alias by its row");
  assert.equal(modelFastKnown(choices, "claude-fable-5-2"), true, "a version by its row");
  assert.equal(modelFastKnown(choices, "claude-fable-5-2[1m]"), true, "the tagged spelling");
  assert.equal(modelFastKnown(choices, "Fable 5.2"), true, "the badge's label (st.model is the fallback the dialog reads)");
  assert.equal(modelFastKnown(choices, "claude-opus-4-8"), false, "the CLI retired it: false, not the family's true");
  assert.equal(modelFastKnown(choices, "claude-fable-5-1"), null, "a row whose answer is unknown");
  assert.equal(modelFastKnown(choices, "sonnet"), null);
  assert.equal(modelFastKnown(choices, "default"), null, "no `fast` on the client's own Default sentinel");
  assert.equal(modelFastKnown(choices, "gw-6-astra"), null, "an id no row carries");
  assert.equal(modelFastKnown(choices, ""), null);
  assert.equal(modelFastKnown(undefined, "opus"), null);
  // render.ts's canFast consults the cached frame first, the seed rule after (source pin: no jsdom harness)
  const canFast = RENDER.match(/const canFast = \(m: string\) => \{[\s\S]*?\n    \};/)?.[0] || "";
  assert.match(canFast, /const known = modelFastKnown\(MODEL_CHOICES, m\);/);
  assert.match(canFast, /if \(typeof known === "boolean"\) return known;/);
  assert.match(canFast, /!v \|\| v === "default" \|\| v\.includes\("opus"\)/, "the seed stands where no CLI has named the model");
  assert.match(RENDER, /versions\?: \{ label: string; value: string; learned\?: boolean; fast\?: boolean \| null \}\[\] \}\[\] = \[\];/, "the cached frame's type carries the field");
  assert.match(KERNEL, /fast=jd\.fast_known\(v\["value"\]\)/, "each version row carries the CLI's word");
  assert.match(KERNEL, /fast=jd\.fast_known\(_picks\.get\(c\["value"\]\) or c\["value"\]\)/, "each family carries its default's");
});
