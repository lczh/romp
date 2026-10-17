// Stop/interrupt button beside the state badge (the user 2026-06-19): a less-fiddly alternative to the
// composer's Ctrl+C. It posts the SAME {type:"interrupt"} message the host turns into an Esc into the
// pane, and renders ONLY while the session is busy (working/compacting) — there's nothing to interrupt
// when idle, so it isn't drawn at all. No jsdom harness for the renderer, so pin the wiring at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { chipStops } from "./status-chip";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the stop button posts the same interrupt message as the composer's Ctrl+C", () => {
  assert.match(RENDER, /function stopButton\(state\?: ChipState\)/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "interrupt", id: activeId \}\)/);
  // three interrupt senders now, all the SAME path: the composer Ctrl+C, this button, and the retrying
  // card's "Stop retrying" (the user 2026-07-24 — the CLI owns the api_retry backoff and the SDK exposes no
  // handle on it, so interrupting the stalled turn is the one honest stop).
  const senders = RENDER.match(/postMessage\(\{ type: "interrupt", id: activeId \}\)/g) || [];
  assert.equal(senders.length, 3, "Ctrl+C, the stop button, and Stop retrying — one interrupt path");
});

test("the button renders while busy (working/compacting) AND while stuck retrying/blocked, never when idle", () => {
  // retrying/blocked were added (the user 2026-07-06): there the interrupt doubles as the per-thread
  // auto-retry off-switch. Still omitted in ready/idle/awaiting/interrupting — nothing to stop.
  // one rule, chipStops (status-chip.ts), which the comment popover's stop reads too (thread-live-1, 2026-10-14)
  assert.match(RENDER, /if \(chipStops\(s\.status\.state\)\) left\.appendChild\(stopButton\(s\.status\.state\)\);/);
  for (const st of ["working", "compacting", "retrying", "blocked"]) assert.ok(chipStops(st), st);
  for (const st of ["ready", "idle", "awaitingBg", "needsInput", "interrupting", "opening", "closed", ""]) assert.ok(!chipStops(st), st);
  // no busy/idle CLASS toggle — it's drawn only when there's something to stop, so the bare .stop-btn is the live look
  assert.ok(!/"stop-btn" \+ \(busy \? " active" : ""\)/.test(RENDER), "no idle variant — omitted, not grayed");
});

test("it carries a stop icon + a state-aware tooltip and aria-label", () => {
  assert.match(RENDER, /el\("span", "stop-icon"\)/);
  assert.match(RENDER, /same as Ctrl\+C/);
  // idle-busy states keep the plain label; the stuck states get the retry-specific one
  assert.match(RENDER, /setAttribute\("aria-label", stuck \? "Stop retrying this session" : "Interrupt session"\)/);
});

test("every stop says plainly that the session's background agents stop with it", () => {
  // the user 2026-10-17: a Stop ends the turn AND every background agent the session is running, and those agents can
  // never be picked back up (the CLI records them as stopped by the user). The behaviour stays; the button says so, in
  // the person's words: the chat bar's stop in both its forms, and the comment popover's stop on a thread's session.
  assert.match(RENDER, /"Stop\\ninterrupt this session \(same as Ctrl\+C\); its background agents stop too"/);
  assert.match(RENDER, /"Stop retrying\\ninterrupt this thread and hold its auto-retry off until you send it a message; its background agents stop too"/);
  assert.match(RENDER, /b\.title = "Stop — interrupt this thread's turn; its background agents stop too";/);
});

test("it's a white square that reveals the pale-red stop tint ONLY on hover, with a press flash", () => {
  const base = (CSS.match(/\.stop-btn \{[^}]*\}/) || [""])[0];
  assert.match(base, /color: var\(--fg\)/, "neutral white-ish square by default");
  assert.match(base, /cursor: pointer;/);
  assert.ok(!/st-blocked-bg/.test(base), "no red until you hover");
  const hoverRule = (CSS.match(/\.stop-btn:hover \{[^}]*\}/) || [""])[0];
  assert.match(hoverRule, /color: var\(--st-blocked-bg\)/, "pale red square on hover");
  assert.match(hoverRule, /background: rgba\(229, 72, 77/, "pale red background on hover");
  assert.doesNotMatch(CSS, /stop-flash/, "the old flash rule is fully gone (its JS was removed; 2026-07-07 CSS audit)");
  assert.match(CSS, /\.stop-icon \{[^}]*background: currentColor/, "the square stop glyph");
});
