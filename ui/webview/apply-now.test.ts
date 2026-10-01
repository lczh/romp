// Apply now (2026-10-01): a billing, effort or fast pick relaunches the session's CLI, and the relaunch waits until nothing
// would be cut; a session whose monitors never let it go quiet kept its pick waiting for hours. The pick's control offers
// one row while it waits, saying what pushing it through ends, and the click confirms first in Restart session's shape.
//
// Executed here: the module's rules (when the row shows, its words, the confirm, the gesture with and without something
// to end). Pinned: the two places render.ts draws the row (the Billing flyout, the effort and fast pickers), the click
// resolving the session by id, the op, the refusal toast and an older kernel's unknownOp, and the kernel's door. The
// served lab (tests/test_apply_now_browser.py) drives the flyout row in a real browser over a hermetic kernel. Synthetic
// values only: the notes-api world.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { applyNowOffered, applyNowSub, applyNowTitle, applyNowDetail, requestApplyNow, APPLY_NOW_BUTTONS, APPLY_NOW_LABEL } from "./apply-now";
import { RESTART_STANDING } from "./clear-confirm";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(UI, "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the row shows only while that control's own pick waits and something holds it", () => {
  const holds = { relaunchWaits: "2 background tasks", relaunchCuts: "2 background tasks" };
  assert.equal(applyNowOffered({ ...holds, authPending: true }, "billing"), true);
  assert.equal(applyNowOffered({ ...holds, effortPending: true }, "effort"), true);
  assert.equal(applyNowOffered({ ...holds, fastPending: true }, "fast"), true);
  assert.equal(applyNowOffered({ ...holds, authPending: true }, "effort"), false, "a billing pick is the Billing flyout's row, not the effort picker's");
  assert.equal(applyNowOffered({ ...holds, effortPending: true }, "billing"), false);
  assert.equal(applyNowOffered({ ...holds }, "billing"), false, "no pick waits: nothing to apply");
  assert.equal(applyNowOffered({ authPending: true, relaunchWaits: "" }, "billing"), false, "nothing holds it: it is applying already");
  assert.equal(applyNowOffered({ authPending: true }, "billing"), false, "an older kernel sends no relaunchWaits: no row");
  assert.equal(applyNowOffered(null, "billing"), false);
});

test("the row says what the click ends, in the kernel's words", () => {
  assert.equal(APPLY_NOW_LABEL, "Apply now");
  assert.equal(applyNowSub({ relaunchCuts: "2 background tasks" }), "ends 2 background tasks");
  assert.equal(applyNowSub({ relaunchCuts: "the running turn, 1 subagent and 2 background tasks" }), "ends the running turn, 1 subagent and 2 background tasks");
  assert.equal(applyNowSub({ relaunchCuts: "" }), "relaunches it now", "only queued messages hold it: they survive the relaunch, so nothing is ended");
});

test("the confirm is Restart session's shape: the title asks, the detail names the cost and then what the session keeps", () => {
  assert.equal(applyNowTitle("web"), "Apply the change to “web” now?");
  assert.equal(applyNowDetail("2 background tasks"), "Applying it now ends 2 background tasks. " + RESTART_STANDING);
  assert.deepEqual(APPLY_NOW_BUTTONS, [{ label: "Apply now", value: "apply" }, { label: "Cancel", value: "" }], "no danger mark: nothing is destroyed");
  for (const s of [applyNowTitle("web"), applyNowDetail("1 subagent")]) assert.doesNotMatch(s, /\bkill|\bdestroy|\bdelete/i);
});

test("a click with something to end confirms first, Cancel posts nothing, the dialog's own button posts once; with nothing to end it posts at once", () => {
  const confirms: Array<{ title: string; detail: string }> = [];
  let answer: ((v: string | null) => void) | null = null;
  let posts = 0;
  const ctx = (cuts: string) => ({ name: "web", cuts,
    confirm: (title: string, detail: string, _b: unknown, cb: (v: string | null) => void) => { confirms.push({ title, detail }); answer = cb; },
    post: () => { posts++; } });
  requestApplyNow(ctx("2 background tasks"));
  assert.equal(confirms.length, 1, "it ends work: the dialog first");
  assert.equal(posts, 0, "the click alone posts nothing");
  assert.match(confirms[0].detail, /^Applying it now ends 2 background tasks\./);
  answer!(null);
  assert.equal(posts, 0, "Cancel, Escape, the backdrop: nothing posted");
  requestApplyNow(ctx("2 background tasks"));
  answer!("apply");
  assert.equal(posts, 1, "the dialog's Apply now posts the op once");
  requestApplyNow(ctx(""));
  assert.equal(confirms.length, 2, "nothing to end: no dialog");
  assert.equal(posts, 2, "…and the op at once");
});

test("the Billing flyout draws the row behind a rule under the picks, closes the menu and resolves the session by id at the click", () => {
  const fly = RENDER.slice(RENDER.indexOf("const openBillingFly = ()"), RENDER.indexOf("// ── Set default billing (T387"));
  assert.match(fly, /if \(applyNowOffered\(st, "billing"\)\) \{\s*\n\s*sub\.appendChild\(el\("div", "ctx-sep"\)\);\s*\n\s*sub\.appendChild\(applyNowRow\("ctx", st, \(\) => \{ dismissTabMenu\(\); applyNowFor\(id, "billing"\); \}\)\);/);
  assert.ok(fly.indexOf("applyNowOffered(st, \"billing\")") > fly.indexOf("for (const c of choices)"), "under the picks it applies");
});

test("the effort and fast pickers draw the row behind a rule under the choices, and the thread popovers never offer it", () => {
  const menu = RENDER.slice(RENDER.indexOf("function toggleMetaMenu("), RENDER.indexOf('document.addEventListener("click", () => closeMetaMenu());'));
  assert.match(menu, /const waitingPick: WaitingPick \| null = kind === "effort" \? "effort" : kind === "fast" \? "fast" : null;/);
  assert.match(menu, /if \(waitingPick && applyNowOffered\(s\.status, waitingPick\)\) \{\s*\n\s*menu\.appendChild\(el\("div", "meta-sep"\)\);\s*\n\s*menu\.appendChild\(applyNowRow\("meta", s\.status, \(\) => \{ closeMetaMenu\(\); applyNowFor\(opSid, waitingPick\); \}\)\);/);
  const thread = RENDER.slice(RENDER.indexOf("function threadMetaStatus("), RENDER.indexOf("function cmtStateChip("));
  assert.doesNotMatch(thread, /Pending|relaunch/, "a thread's status carries no pending mark, so its pickers draw no row");
  assert.match(CSS, /\.meta-sep \{ height: 1px; margin: 4px 6px; background: var\(--box-border\); \}/, "the tab menu's rule, in the picker");
});

test("the click reads the session as it stands, posts one op, and a refusal or an older kernel is said", () => {
  const fn = RENDER.slice(RENDER.indexOf("function applyNowFor("), RENDER.indexOf("function dismissTabMenu()"));
  assert.match(fn, /const s = liveSession\(sid\);/);
  assert.match(fn, /if \(!st \|\| !applyNowOffered\(st, pick\)\) return;/, "applied meanwhile: nothing left to push, nothing posted");
  assert.match(fn, /cuts: st\.relaunchCuts \|\| ""/, "what it ends, read at the click");
  assert.match(fn, /confirm: \(title, detail, buttons, cb\) => showConfirm\(title, detail, buttons, cb\)/, "Restart session's dialog");
  assert.match(fn, /post: \(\) => \{ vscodeApi\?\.postMessage\(\{ type: "applyNow", id: sid \}\); \}/, "one op, the id alone");
  assert.match(RENDER, /else if \(m\.type === "applyFailed" && m\.id\) \{\s*\n\s*warnToast\(`Couldn’t apply the change to “\$\{String\(m\.name \|\| m\.id\)\}” — \$\{String\(m\.text \|\| "unknown error"\)\}`\);/);
  assert.match(RENDER, /if \(m\.op === "applyNow"\) \{[\s\S]{0,200}warnToast\("This romp kernel is older than the dashboard and has no Apply now/);
});

test("the kernel answers the op off the request thread, advertises it, and applies through the owning backend", () => {
  assert.match(KERNEL, /elif msg and msg\.get\("type"\) == "applyNow" and msg\.get\("id"\):\s*\n(?:\s*#[^\n]*\n)*\s*threading\.Thread\(target=_apply_now, args=\(msg\["id"\], client\), daemon=True\)\.start\(\)/);
  assert.match(KERNEL, /KERNEL_WS_CAPS = \([^)]*"applyNow"[^)]*\)/);
  assert.match(KERNEL, /return be\.apply_now\(sid\) if hasattr\(be, "apply_now"\) else sb\.SessionBackend\.apply_now\(be, sid\)/);
});
