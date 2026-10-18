// Stop/interrupt button beside the state badge (the user 2026-06-19): a less-fiddly alternative to the
// composer's Ctrl+C. It posts the SAME {type:"interrupt"} message the host turns into an Esc into the
// pane, and renders ONLY while the session is busy (working/compacting) — there's nothing to interrupt
// when idle, so it isn't drawn at all. No jsdom harness for the renderer, so pin the wiring at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { chipStops } from "./status-chip";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the stop button posts the same interrupt message as the composer's Ctrl+C", () => {
  assert.match(RENDER, /function stopButton\(state\?: ChipState\)/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "interrupt", id: activeId \}\)/);
  // two interrupt senders aim at the active tab: the composer Ctrl+C and this button, both of which only ever
  // act on the tab in front of the user. The retrying card's "Stop retrying" (the user 2026-07-24 — the CLI owns
  // the api_retry backoff and the SDK exposes no handle on it, so interrupting the stalled turn is the one honest
  // stop) sends the same message to the session that owns the card instead (below).
  const senders = RENDER.match(/postMessage\(\{ type: "interrupt", id: activeId \}\)/g) || [];
  assert.equal(senders.length, 2, "Ctrl+C and the stop button — the active tab's interrupt path");
  assert.match(RENDER, /stopRetrying: \(el\) => \{\s*\n\s*const own = owningSidOf\(el as HTMLElement\);\s*\n\s*if \(!own \|\| !vscodeApi\) return;\s*\n\s*vscodeApi\.postMessage\(\{ type: "interrupt", id: own \}\);/);
});

test("Stop retrying, executed: the card inside a comment thread's popover interrupts the THREAD, the chat's card its own session", () => {
  // a comment thread's popover draws the thread's events through the chat's renderer, so a thread stuck in an
  // API retry storm shows the same retrying card, inside a list stamped with the thread's session. Its stop used
  // to interrupt the active tab (the thread's parent), cutting that session's running turn while the thread kept
  // retrying. owningSidOf and the delegated handler, lifted from render.ts and transpiled at run time, against a
  // small stand-in DOM whose closest("[data-session]") walks the parent chain.
  const fnSrc = (name: string) => { const i = RENDER.indexOf("function " + name + "("); assert.ok(i >= 0, name); return RENDER.slice(i, RENDER.indexOf("\n}\n", i) + 3); };
  const head = "    stopRetrying: (el) => {";
  const at = RENDER.indexOf(head);
  assert.ok(at >= 0 && RENDER.indexOf(head, at + 1) < 0, "one stopRetrying handler");
  const handler = "const stopRetrying = " + RENDER.slice(at + "    stopRetrying: ".length, RENDER.indexOf("\n    },", at) + "\n    }".length) + ";";
  const req = createRequire(__filename);
  const ts = (src: string) => req("esbuild").transformSync(src, { loader: "ts" }).code;
  type Posted = { type: string; id?: string | null };
  type N = { dataset: Record<string, string>; parent: N | null; disabled: boolean; textContent: string; closest: (s: string) => N | null };
  const node = (dataset: Record<string, string>, parent: N | null): N => {
    const n: N = {
      dataset, parent, disabled: false, textContent: "",
      closest(sel: string) {
        assert.equal(sel, "[data-session]");
        for (let c: N | null = n; c; c = c.parent) if (c.dataset.session !== undefined) return c;
        return null;
      },
    };
    return n;
  };
  const harness = (activeId: string) => new Function("activeId",
    "const posted = []; const vscodeApi = { postMessage: (m) => posted.push(m) };\n" + ts(fnSrc("owningSidOf") + "\n" + handler) +
    "\nreturn { stopRetrying, posted };")(activeId) as { stopRetrying: (e: N) => void; posted: Posted[] };
  const TAB = "api-11111111-2222-3333-4444-555555555555";       // the active tab: the parent session, mid-turn
  const THREAD = "thr-11111111-2222-3333-4444-666666666666";    // the comment thread's own session, retrying

  // inside the popover: the list carries the thread's session (fillCommentMsgs), the card sits in it
  const inPop = harness(TAB);
  const btn = node({ act: "stopRetrying" }, node({}, node({ session: THREAD }, node({}, null))));
  inPop.stopRetrying(btn);
  assert.deepEqual(inPop.posted.map((m) => [m.type, m.id]), [["interrupt", THREAD]], "the thread is stopped, the active tab is left alone");
  assert.deepEqual([btn.disabled, btn.textContent], [true, "Stopping…"], "acknowledged at once");

  // in the chat: the view's thread root carries the tab's session (ensureView)
  const inChat = harness(TAB);
  inChat.stopRetrying(node({ act: "stopRetrying" }, node({}, node({ session: TAB }, null))));
  assert.deepEqual(inChat.posted.map((m) => [m.type, m.id]), [["interrupt", TAB]]);

  // no session anywhere and no active tab: nothing is sent
  const none = harness("");
  none.stopRetrying(node({ act: "stopRetrying" }, null));
  assert.deepEqual(none.posted, []);
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
