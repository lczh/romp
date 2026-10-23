// Thinking summaries (2026-09-01), pinned at the source like the other webview tests (the chat
// renderer has no jsdom harness):
//  - the FEED rule: a thinking block is opaque ("Thinking…") only when it has a signature AND no
//    text. The old `ev.encrypted ? "Thinking…" : ev.text` hid every summary once the kernel asked
//    the API for them, because a summarized block carries both a signature and its text. The
//    kernel computes the flag the same way; the renderer re-checks the text so a bundle talking to
//    an older kernel (flag = signature only) still shows any text it is handed.
//  - the GEAR toggle: a per-install kernel-side checkbox beside the other kernel toggles, stamped
//    with its gesture time like every kernel-side setting the gear posts, filled from /version, named
//    in the stale-gesture toast. Per-install means each kernel keeps its own and the poll never
//    converges it; the CLICK follows the gear's machine selector (federation's KERNEL_SETTING since the
//    settings' scope fan-out: under All kernels it reached the local kernel alone while the box read
//    as every machine's value), and a disagreeing row wears the differs flag.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const RENDER = read("ui", "webview", "render.ts");
const GEAR = read("ui", "webview", "gear.js");
const FED = read("ui", "webview", "federation.ts");
const KERNEL = read("bin", "romp-kernel");

test("a thinking block is opaque only when signed AND textless — a summary renders its text", () => {
  const at = RENDER.indexOf('if (ev.kind === "thinking") {');
  assert.ok(at > 0, "the thinking branch exists");
  const branch = RENDER.slice(at, at + 2000);
  assert.match(branch, /const opaque = ev\.encrypted && !\(ev\.text \|\| ""\)\.trim\(\);/,
    "the renderer re-derives opacity from the text it was handed, never from the flag alone");
  // (an opaque row with the message's exact thinking tokens reads "Thought · 2.5k tokens" instead: the thinking is done,
  // and what it cost is all there is to show — live-status.ts thoughtLabel, 2026-10-08)
  assert.match(branch, /t\.textContent = opaque \? \(thought \|\| "Thinking…"\) : ev\.text;/);
  assert.match(branch, /if \(opaque\) \{ turn\.appendChild\(t\); return turn; \}/,
    "only the opaque block is the one-liner; a text-bearing block falls through to the clamp");
  assert.ok(!/ev\.encrypted \? "Thinking…"/.test(branch), "the old flag-only rule is gone");
  // the text-bearing path keeps progressive disclosure: clamped to ~2 lines, click to expand, state keyed
  assert.match(branch, /el\("div", "think-clamp"\)/);
  assert.match(branch, /applyFold\(clamp, "expanded", tkey\)/);
  // the click rides the body delegate, never a listener on the rebuilt node (ui/CLAUDE.md click-safety; 2026-10-23)
  assert.match(branch, /clamp\.dataset\.act = "thinkfold"; if \(tkey\) clamp\.dataset\.tkey = tkey;/);
  assert.doesNotMatch(branch, /clamp\.addEventListener/, "no per-node listener");
  assert.match(RENDER, /thinkfold: \(el\) => \{\s*\n\s*const box = el\.classList\.contains\("thought-line"\) \? el\.closest\("\.turn-thought"\) as HTMLElement \| null : el;\s*\n\s*if \(!box\) return;\s*\n\s*rememberFold\(box, "expanded", el\.dataset\.tkey \|\| undefined\);/,
    "one delegated fold for both shapes, the clamp and compact mode's Thought row, keyed in the shared fold set");
});

test("the kernel computes the flag by the same rule (signature AND no text)", () => {
  assert.ok(KERNEL.includes('"encrypted": bool(b.get("signature")) and not (b.get("thinking") or "").strip()'),
    "the ChatEvent builder's flag means opaque, not merely signed");
});

test("the gear has a Thinking summaries checkbox among the kernel-side toggles, gesture-stamped, filled from /version", () => {
  assert.ok(GEAR.includes("id=rs-thinksum"), "the checkbox exists in the gear markup");
  const at = GEAR.indexOf("id=rs-thinksum");
  assert.ok(GEAR.indexOf("id=rs-conserve") < at, "…after Conserve memory, with the other kernel-side toggles");
  assert.ok(GEAR.indexOf("data-pane=chat") > 0 && GEAR.indexOf("data-pane=feed") > 0, "both panes exist (indexOf's -1 would pass the order check)");
  assert.ok(GEAR.indexOf("data-pane=chat") < at && at < GEAR.indexOf("data-pane=feed"), "…in the Chat tab's Thinking section (T404: it creates, so Chat, not Display; Task tracking before)");
  const row = GEAR.slice(at, at + 1200);
  assert.match(row, /<b>Thinking summaries<\/b>/);
  assert.ok(/new Claude Code session/.test(row) && /running session picks the change up at its next reconnect/.test(row),   // the backend's name since T288
    "the sub-copy is honest that a running session is not switched live");
  // …and names the events that actually reconnect one (sdk_backend's request_reconnect callers), never a
  // model switch: set_model applies live over the SDK control channel and reconnects nothing, so copy that
  // named a model switch as a trigger would send the user to switch models and wait forever.
  assert.ok(/reconnect: an effort or billing switch, the first fast-mode opt-in, or a kernel restart/.test(row),
    "the real reconnect triggers are named");
  assert.ok(/Switching the model applies live and does not reconnect/.test(row),
    "a model switch is called out as NOT one");
  assert.ok(!/model or effort triggers one/.test(row), "no clause names a model switch as a trigger");
  // The CLI resolves --thinking adaptive ahead of MAX_THINKING_TOKENS and alwaysThinkingEnabled:false, so
  // on an install with thinking off the toggle turns adaptive thinking on; the copy owns that.
  assert.ok(/turns adaptive thinking on/.test(row),
    "the copy says the toggle also turns adaptive thinking on where it was off");
  // 2026-10-23: compact mode (default on) shows a thought as one collapsed Thought line, summaries included, no longer hidden
  assert.ok(/or to one Thought line under Compact transcript/.test(row) && !/still hides them/.test(row),
    "…and how the compact view (default on) shows them: one Thought line");
  assert.ok(/A summary costs nothing extra/.test(row) && /and that thinking is billed/.test(row) && !/pays for/.test(row),
    "the cost in plain words: the summary is free, turned-on thinking is billed (the API's pricing docs)");
  assert.ok(GEAR.includes("post(scoped({ type: 'setThinkingSummaries', enabled: ths.checked, gt: gclock.stamp('thinking-summaries') }))"),
    "the click posts the kernel's designed message with the gesture stamp minted in the literal, to the kernels in the selector's scope");
  assert.match(row, /<b>Thinking summaries<\/b><span class=rs-mixed hidden><\/span>/, "the row carries the flag's slot");
  assert.ok(/Applies on every connected machine’s kernel\./.test(row) && !/keeps its own copy/.test(row),
    "the sub-copy no longer claims this kernel alone");
  assert.ok(GEAR.includes("ths.checked = !!v.thinkingSummaries"),
    "the box always shows the kernel's persisted answer, never a page default");
  assert.match(GEAR, /STALE_LABELS = \{[\s\S]*?'thinking-summaries': 'Thinking summaries'/,
    "a stood-down gesture toasts under the row's own name");
});

test("Thinking summaries is per-install but follows the selector: a KERNEL_SETTING, never converged, marked where kernels differ", () => {
  const setSrc = FED.match(/const KERNEL_SETTING = new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(setSrc, "federation.ts's KERNEL_SETTING set located");
  assert.ok(setSrc![1].includes('"setThinkingSummaries"'), "a click under All kernels reaches every kernel (queued per host while a socket is down)");
  assert.ok(!KERNEL.includes('"thinkingSummaries", _set_thinking_summaries'),
    "…but nothing converges it: not in the /judge-settings propagation table on the kernel side");
  const mesh = KERNEL.match(/_MESH_ADOPTED_SETTINGS = \(([\s\S]*?)\)\n_SETTINGS_STORES/);
  assert.ok(mesh && !mesh[1].includes("thinking"), "…nor the poll's mesh-adopted table: each kernel keeps its own");
  assert.ok(KERNEL.includes('"thinkingSummaries": _thinking_summaries_on(), "wholeChatFrames": _whole_chat_frames_on(),'),
    "/version's settings dict carries it, so a peer's row shows where the kernels disagree");
  assert.match(GEAR, /\['thinkingSummaries', ths\]/, "the row is in the gear's mixed marks");
  assert.match(GEAR, /var PER_INSTALL_KEYS = \[[^\]]*'thinkingSummaries'/, "…and follows the selector under a pick");
});
