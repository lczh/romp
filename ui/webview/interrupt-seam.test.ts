// The interrupt SEAM (the user 2026-07-09): the model's null settle-reply ("No response requested.")
// closing an interrupted turn renders as part of the seam — a slim rail marker — not a full assistant
// bubble; and the interrupt marker itself names the CAUSE when romp's resume notice says so (a kernel
// restart / crash cut is not the user pressing stop). Source-level pin like feed-interrupting.test.ts
// (the chat renderer has no jsdom harness); the kernel flags are behavior-tested in
// tests/test_kernel_interrupt_seam.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the null settle-reply renders as a seam marker, never an assistant bubble", () => {
  assert.match(R, /if \(\(ev as any\)\.interruptSettle\) \{/);
  // 2026-09-08 (the notice-vocabulary pass): a slim SESSION notice; the words drop the italic and read as a fact
  assert.match(R, /const SETTLE_GIST = "turn settled with no response";/);
  assert.match(R, /the model closed the interrupted turn with nothing to add; the real work resumes below/);
  // it reuses the interrupt marker's own chrome (turn-interrupt + interrupt-line), minus the stop-square
  // glyph — the square means a stop happened HERE; the settle line is its echo, not a second stop
  const settle = R.slice(R.indexOf("interruptSettle"), R.indexOf("interruptSettle") + 600);
  assert.match(settle, /notice\(\{ src: "session", glyph: "session", gist: SETTLE_GIST, cls: "turn-interrupt",/);
  assert.doesNotMatch(settle, /interrupt-line|interrupt-square/);
});

test("the interrupt marker names the cause when the kernel stamped one", () => {
  // rendered through the lifted seam (renderSeam, below)
  const restart = renderSeam({ kind: "user", interruptMarker: true, interruptCause: "restart" });
  assert.equal(restart.gist, "interrupted — kernel restart");
  assert.equal(restart.tip, "a romp kernel restart cut this turn; the session was resumed automatically");
  assert.equal(renderSeam({ kind: "user", interruptMarker: true, interruptCause: "crash" }).gist, "interrupted — process died");
  // the unlabeled seam keeps the user-stop reading
  assert.equal(renderSeam({ kind: "user", interruptMarker: true }).tip, "you stopped this turn here (the stop button / Ctrl+C)");
});

// The seam's own code, lifted out of render.ts and RUN (the #169 review asked for a render, not a regex): the interrupt
// branch of renderEventInner with a recording notice(), and the compact-mode brief's interruptGist.
const requireCjs = createRequire(__filename);
function lift(startAnchor: string, endAnchor: string): string {
  const a = R.indexOf(startAnchor), b = R.indexOf(endAnchor, a);
  assert.ok(a >= 0 && b > a, `render.ts still holds ${startAnchor}`);
  return requireCjs("esbuild").transformSync(R.slice(a, b), { loader: "ts" }).code;
}
const GIST_SRC = lift("type SpendFigures =", "const SETTLE_GIST");
const SEAM_SRC = lift("    if ((ev as any).interruptMarker) {", "    // A romp SYSTEM notice (kernel restart/resume, Retry)");
type Spec = { src: string; glyph: string; gist: string; cls?: string; sev?: string; tip?: string };
function renderSeam(ev: Record<string, unknown>): Spec {
  const run = new Function("ev", "notice", GIST_SRC + "\n" + SEAM_SRC + "\nreturn null;");
  return run(ev, (spec: Spec) => spec) as Spec;
}

test("a stop the spend guard made names the guard and its rate, never the user (the user 2026-10-08)", () => {
  const spend = renderSeam({ kind: "user", interruptMarker: true, interruptCause: "spend", interruptSpend: { rate: "1,240", ceiling: "1,000" } });
  assert.equal(spend.gist, "stopped by the spend guard at $1,240 an hour");
  assert.equal(spend.sev, "warn");
  assert.equal(spend.tip, "romp's spend guard stopped this turn: the session was spending about $1,240 an hour, over the $1,000 an hour ceiling");
  assert.equal(spend.cls, "turn-interrupt");
  // the user's own stop keeps its reading, and the restart cut its own
  const own = renderSeam({ kind: "user", interruptMarker: true });
  assert.equal(own.gist, "interrupted");
  assert.equal(own.tip, "you stopped this turn here (the stop button / Ctrl+C)");
  assert.equal(own.sev, undefined);
  assert.equal(renderSeam({ kind: "user", interruptMarker: true, interruptCause: "restart" }).gist, "interrupted — kernel restart");
  // the compact-mode brief reads the same gist (noticeBrief)
  assert.match(R, /if \(\(ev as any\)\.interruptMarker\) return \{ src: "session", glyph: "session", gist: interruptGist\(ev\.interruptCause, ev\.interruptSpend\) \};/);
});

test("a card anchored at a DROPPED settle's uuid lands on the seam that replaced it", () => {
  // A machine-cut turn's settle event is dropped server-side (tests/test_interrupt_settle_machine_cut.py),
  // but that settle atom is the cut turn's LAST assistant atom — exactly where verdicts/cards get
  // anchored — so the click honest-failed "couldn't locate" though the seam it closed was right there
  // (the user 2026-08-25, a card on a dozens-of-restarts session). The kernel aliases the dropped
  // uuids onto the marker (settleUuids) and the seam answers to them, event-based — never nearest-time.
  assert.match(R, /const su = \(ev as any\)\.settleUuids as string\[\] \| undefined;/);
  assert.match(R, /if \(su && su\.length\) turn\.dataset\.uuids = su\.join\(" "\);/);
  // BOTH anchor lookups honor the alias — the initial query and the post-re-render re-query (the
  // data-mids precedent: one selector short and the recovery still honest-fails pointer-not-rendered)
  const both = R.split('data-uuids~=').length - 1;
  assert.ok(both >= 2, "data-uuids must be in BOTH the initial query and the re-query");
  // …and the events search finds the alias too, so the window re-renders around the seam
  assert.match(R, /\(\(\(e as \{ settleUuids\?: string\[\] \}\)\.settleUuids \|\| \[\]\)\.includes\(uuid\)\)/);
});
