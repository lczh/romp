// The per-tier Fast mode boxes (T300), driven in headless Chromium over the REAL gear module (gear.js) and its
// stylesheet. gear-judge-fast.test.ts pins the source; a pin cannot see a box that fails to grey, a hint that
// never changes, or a gate that reads an unfilled select (a painted select reads its first option before /version
// answers). So the gear is opened against a fake kernel (the /models, /version, /tunnels, /palette routes answered
// with synthetic payloads), and the DOM is read after each gesture: a fill with triage on Opus, distilling following
// triage and indexing on Haiku greys only the indexing box, with the flags shown as stored, the greyed look on the
// box alone (its hint's ancestors all opaque, its word in the section grey, its hint unfaded and in the colour a live
// box's hint shows); a Triage pick of Sonnet greys the triage and distilling boxes, keeps them checked, swaps their
// hints, and posts nothing for them; picking Opus again ungreys them with the hints back; a checked box on a capable
// model whose last fast ask the CLI declined shows the refusal; ticking a box posts its op; pinning Distilling to
// Opus keeps its box live under a Sonnet triage. That payload names no model fast or not (no CLI has said), so it runs
// the seed rule. The second test serves a /models list that carries the CLI's word (Claude Code 2.1.321 runs fast mode
// on Fable 5.2 and retired it for Opus 4.8): a Fable 5.2 tier and comment model keep their boxes live, an Opus the list
// names as not running fast greys them, a model the list does not name falls to the seed, and a models frame carrying a
// new answer re-gates the boxes with no other event.
// Skips with a stated reason without a playwright browser, and fails with it where CI requires the leg
// (browser-leg.ts). Synthetic values only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { launchLeg } from "./browser-leg";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
const GEAR_CSS = fs.readFileSync(path.join(UI, "gear.css"), "utf8");

const ENTRY = `
const { initGear } = require("./gear.js");
(window as any).__posts = [];
initGear((m: any) => { (window as any).__posts.push(m); });
`;

function bundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "gear-judge-fast-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8><style>${GEAR_CSS}</style></head><body>
<script src=/dist/gear.js></script></body></html>`;
const MODELS: any = { models: [{ value: "opus", label: "Opus", versions: [] }, { value: "sonnet", label: "Sonnet", versions: [] }, { value: "haiku", label: "Haiku", versions: [] }],
  efforts: [{ value: "", label: "Default" }, { value: "low", label: "Low" }] };
// the kernel's raw picks: triage on Opus with its box on, distilling following triage with its box on, indexing on
// Haiku with its box on too (a kept value on a tier that cannot run fast: greyed, not lost); the CLI declined the
// distilling tier's last fast ask
const VERSION = { judgeModel: "opus", judgeEffort: "", indexModel: "haiku", indexEffort: "low", distillModel: "triage", distillEffort: "triage",
  judgeConcurrency: "", commentModel: "session", commentEffort: "session", commentFast: "session",
  judgeFast: "on", distillFast: "on", indexFast: "on", fastRefused: { distill: { reason: "sdk_opt_in_required", model: "opus", t: 1 } },
  autoNudge: true, settingsGt: {}, updateMode: "off" };

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

const OFF_HINT = "This tier's model doesn't run fast mode. Pick one that does to use it; the setting is kept.";
type Snap = { disabled: Record<string, boolean>; off: Record<string, boolean>; opacity: Record<string, string>;
  hintFade: Record<string, string>; wordColor: Record<string, string>; checked: Record<string, boolean>; hint: Record<string, string> };

// what the fake kernel answers: the second test swaps the list under a models frame, so the route reads it per request
type Kernel = { models: any; version: any };
async function withGear(t: any, body: (page: any, errors: string[]) => Promise<void>, kernel: Kernel = { models: MODELS, version: VERSION }): Promise<void> {
  const browser = await launchLeg(t, pw, "chromium");
  if (!browser) return;
  const errors: string[] = [];
  try {
    const js = bundle();
    const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    await page.route("**/*", (route: any) => {
      const u = new URL(route.request().url());
      const json = (o: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(o) });
      if (u.hostname !== "romp.test") return route.fulfill({ status: 404, body: "" });
      if (u.pathname === "/gear") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE_HTML });
      if (u.pathname === "/dist/gear.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: js });
      if (u.pathname === "/models") return json(kernel.models);
      if (u.pathname === "/version") return json(kernel.version);
      if (u.pathname === "/tunnels") return json({ tunnels: [] });
      return json({});   // /palette, /analytics and the rest: empty answers, nothing under test
    });
    await page.goto("http://romp.test/gear");
    await page.waitForFunction(() => Array.isArray((window as any).__posts) && !!document.getElementById("rs-judgemodel"), null, { timeout: 10000 });
    await page.evaluate(() => { window.postMessage({ romp: "openSettings", tab: "tasks" }, "*"); });   // the panel is in tabs (T379): the judge rows live on Task tracking (T400), hidden until that tab is picked
    // the fill has run once a box holds the kernel's flag: only fill() writes it, and the gate runs in the same
    // callback right after (the triage select's value is no signal: a painted select reads its first option,
    // "opus" here, before /version has answered)
    await page.waitForFunction(() => (document.getElementById("rs-indexfast") as HTMLInputElement).checked === true, null, { timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

const snap = (page: any): Promise<Snap> => page.evaluate(() => {
  const disabled: Record<string, boolean> = {}, off: Record<string, boolean> = {}, opacity: Record<string, string> = {};
  const hintFade: Record<string, string> = {}, wordColor: Record<string, string> = {};
  const checked: Record<string, boolean> = {}, hint: Record<string, string> = {};
  for (const tier of ["judgefast", "distillfast", "indexfast"]) {
    const box = document.getElementById("rs-" + tier) as HTMLInputElement;
    const wrap = document.getElementById("rs-" + tier + "-wrap") as HTMLElement;
    const sub = document.getElementById("rs-" + tier + "-sub") as HTMLElement;
    disabled[tier] = box.disabled; off[tier] = wrap.classList.contains("rs-off");
    opacity[tier] = getComputedStyle(box).opacity;   // the box's own: the greyed look fades the box alone
    // the fade the hint is seen through: opacity is not inherited, so the hint's own computed value reads 1 whatever
    // its ancestors do; the most faded ancestor below the settings backdrop is the one the eye sees the hint through
    let fade = 1;
    for (let el = sub.parentElement; el && el.id !== "rsettings"; el = el.parentElement) fade = Math.min(fade, parseFloat(getComputedStyle(el).opacity));
    hintFade[tier] = String(fade);
    wordColor[tier] = getComputedStyle(wrap).color;   // the label's colour is the word's: "Fast mode" is a bare text node
    checked[tier] = box.checked; hint[tier] = sub.textContent || "";
  }
  return { disabled, off, opacity, hintFade, wordColor, checked, hint };
});
const posts = (page: any, type: string): Promise<any[]> => page.evaluate((ty: string) => (window as any).__posts.filter((m: any) => m && m.type === ty), type);
// the judge selects are display:none behind versionMenu's button; a pick is what the user does: open the menu
// after the select, click the family row (a family with one version or none commits on the row itself)
async function pickModel(page: any, selId: string, label: string): Promise<void> {
  await page.click(`#${selId} + button.rs-vermenu-btn`);
  // the menu is a body-level div the facade positions (its inline style re-serializes, so no attribute match);
  // its rows are the focusable direct children, labelled by family
  await page.locator('body > div > div[tabindex="0"]', { hasText: label }).first().click();
  await page.waitForFunction(([id, want]: [string, string]) => {
    const sel = document.getElementById(id) as HTMLSelectElement;
    return sel && Array.from(sel.options).some((o) => o.textContent === want && o.value === sel.value);
  }, [selId, label] as [string, string], { timeout: 5000 });
}

test("each tier's box greys on its own effective model, keeps its value, says why, and posts only its own clicks", { timeout: 90000 }, async (t) => {
  await withGear(t, async (page, errors) => {
    // 1. filled: triage Opus (live), distilling follows triage (live, but the CLI declined its last ask), indexing Haiku (greyed, value kept)
    let s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: false, distillfast: false, indexfast: true }, "greyed for the Haiku tier only");
    assert.deepEqual(s.off, { judgefast: false, distillfast: false, indexfast: true });
    assert.equal(s.opacity.indexfast, "0.4", "the greyed look renders on the box"); assert.equal(s.opacity.judgefast, "1");
    assert.deepEqual(s.hintFade, { judgefast: "1", distillfast: "1", indexfast: "1" }, "the hint's ancestors are all opaque: the greyed reason stays legible");
    assert.notEqual(s.wordColor.indexfast, s.wordColor.judgefast, "the greyed word takes the section grey; the live word keeps the row's colour");
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true }, "the boxes reflect the kernel's raw flags, greyed or not");
    assert.match(s.hint.judgefast, /^This tier's judge calls run in Claude Code's fast mode/, "a live box: the description");
    assert.match(s.hint.distillfast, /^Fast mode was declined by Claude Code for the last distilling call \(sdk_opt_in_required\)/, "a declined ask: the CLI's reason");
    assert.equal(s.hint.indexfast, OFF_HINT, "a greyed box: why");
    for (const ty of ["setJudgeFast", "setDistillFast", "setIndexFast"]) assert.deepEqual(await posts(page, ty), [], `${ty}: a fill posts nothing`);
    // hovering the greyed box shows its hint unfaded, in the colour a live box's hint shows: the label's grey stops at
    // the word, and no rule fades the hint itself (its own opacity reads 1 before the fix too, so this is a guard)
    const hovered = (tier: string) => page.evaluate((id: string) => {
      const sub = document.getElementById("rs-" + id + "-sub") as HTMLElement, wrap = document.getElementById("rs-" + id + "-wrap") as HTMLElement;
      return { display: getComputedStyle(sub).display, color: getComputedStyle(sub).color, opacity: getComputedStyle(sub).opacity, word: getComputedStyle(wrap).color };
    }, tier);
    await page.hover("#rs-indexfast-wrap");
    const shown = await hovered("indexfast");
    await page.hover("#rs-judgefast-wrap");
    const live = await hovered("judgefast");
    assert.equal(shown.display, "block", "hovering the greyed box shows its hint");
    assert.equal(shown.opacity, "1", "the hint itself is not faded");
    assert.notEqual(shown.color, shown.word, "the hint keeps its own colour, not the greyed word's");
    assert.equal(live.display, "block"); assert.equal(shown.color, live.color, "the greyed box's hint shows in the colour a live box's hint does");

    // 2. the user picks Sonnet for triage: the model post, both dependent boxes grey and KEEP their value, hints swap, no box post
    await pickModel(page, "rs-judgemodel", "Sonnet");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: true, distillfast: true, indexfast: true }, "no tier can run fast now");
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true }, "the values are kept: nothing was unchecked");
    for (const tier of ["judgefast", "distillfast"]) assert.equal(s.hint[tier], OFF_HINT, `${tier}: greyed, says why`);
    const model = await posts(page, "setJudgeModel");
    assert.equal(model.length, 1); assert.equal(model[0].model, "sonnet"); assert.equal(typeof model[0].gt, "number");
    for (const ty of ["setJudgeFast", "setDistillFast", "setIndexFast"]) assert.deepEqual(await posts(page, ty), [], `${ty}: a model pick posts nothing for the boxes`);

    // 3. Opus again: live again, still checked, the hints back (the declined one included: the record stands until the kernel clears it)
    await pickModel(page, "rs-judgemodel", "Opus");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: false, distillfast: false, indexfast: true });
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true });
    assert.match(s.hint.judgefast, /^This tier's judge calls/); assert.match(s.hint.distillfast, /^Fast mode was declined/);

    // 4. clicking the triage box posts its own stamped op, and only it
    await page.uncheck("#rs-judgefast");
    const jOff = await posts(page, "setJudgeFast");
    assert.deepEqual(jOff.map((m: any) => m.enabled), [false]); assert.equal(typeof jOff[0].gt, "number");
    assert.deepEqual(await posts(page, "setDistillFast"), []);
    // ...and the hint follows the box: unticking the distilling box (a standing refusal) drops the refusal wording for a
    // tier that no longer asks; ticking it again brings it back (the record stands until the kernel clears it)
    await page.uncheck("#rs-distillfast");
    s = await snap(page);
    assert.match(s.hint.distillfast, /^This tier's judge calls/, "unticked: the plain description");
    await page.check("#rs-distillfast");
    s = await snap(page);
    assert.match(s.hint.distillfast, /^Fast mode was declined/, "ticked again: the refusal is said again");
    assert.deepEqual((await posts(page, "setDistillFast")).map((m: any) => m.enabled), [false, true]);

    // 5. distilling pinned to Opus stays live under a Sonnet triage; a greyed box refuses the click (disabled)
    await pickModel(page, "rs-distillmodel", "Opus");
    await pickModel(page, "rs-judgemodel", "Sonnet");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: true, distillfast: false, indexfast: true }, "a pinned Opus distilling tier stays fast-capable");
    assert.equal(await page.evaluate(() => (document.getElementById("rs-indexfast") as HTMLInputElement).disabled), true);
    assert.deepEqual(errors, [], "no page errors");
  });
});

// The CLI's word, as the kernel's /models carries it (`fast` on a family row for its default and on each version row,
// absent where no CLI has named the id). Synthetic: Fable runs fast mode, Fable 5.2 named so; Opus and its 4.8 named
// not, its 4.7 listed but never named; Sonnet and Haiku never named.
const LEARNED = { rev: 2, models: [
  { value: "fable", label: "Fable", fast: true, versions: [{ value: "claude-fable-5-2", label: "Fable 5.2", fast: true }, { value: "claude-fable-5-1", label: "Fable 5.1", fast: false }] },
  { value: "opus", label: "Opus", fast: false, versions: [{ value: "claude-opus-4-8", label: "Opus 4.8", fast: false }, { value: "claude-opus-4-7", label: "Opus 4.7" }] },
  { value: "sonnet", label: "Sonnet", versions: [] }, { value: "haiku", label: "Haiku", versions: [] }],
  efforts: [{ value: "", label: "Default" }, { value: "low", label: "Low" }] };
// triage on the Fable 5.2 version, distilling following it, indexing on an Opus id the list does not carry (the gear
// shows it as off-list; the seed reads the spelling), comments pinned to Fable 5.2 with their box on
const LEARNED_VERSION = { ...VERSION, judgeModel: "claude-fable-5-2", indexModel: "claude-opus-4-1", distillModel: "triage",
  commentModel: "claude-fable-5-2", commentFast: "on", fastRefused: {} };
const cmt = (page: any): Promise<{ disabled: boolean; checked: boolean }> => page.evaluate(() => {
  const b = document.getElementById("rs-cmtfast") as HTMLInputElement;
  return { disabled: b.disabled, checked: b.checked };
});

test("the boxes follow the CLI's word in /models: live on Fable 5.2, greyed on an Opus it names not fast, the seed for an unnamed model, re-gated by a models frame", { timeout: 90000 }, async (t) => {
  const kernel: Kernel = { models: LEARNED, version: LEARNED_VERSION };
  await withGear(t, async (page, errors) => {
    // 1. filled: every tier can run fast (Fable 5.2 by the list, distilling through triage, the off-list Opus id by the seed)
    let s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: false, distillfast: false, indexfast: false }, "Fable 5.2 runs fast by the list; the unnamed Opus id by the seed");
    assert.deepEqual(s.off, { judgefast: false, distillfast: false, indexfast: false });
    assert.match(s.hint.judgefast, /^This tier's judge calls run in Claude Code's fast mode, billed at a premium\./, "a live box: the description");
    assert.equal(await page.evaluate(() => (document.getElementById("rs-indexmodel") as HTMLSelectElement).selectedOptions[0].textContent),
      "claude-opus-4-1 — not in this kernel's list", "the index tier is a model the list does not name");
    assert.deepEqual(await cmt(page), { disabled: false, checked: true }, "a comment model of Fable 5.2 keeps the comment box live");

    // 2. the Opus family, which the list names as not running fast (its row's `fast` is its default's): both dependent boxes grey, kept
    await pickModel(page, "rs-judgemodel", "Opus");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: true, distillfast: true, indexfast: false }, "the CLI's false greys an Opus tier");
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true }, "the values are kept");
    for (const tier of ["judgefast", "distillfast"]) assert.equal(s.hint[tier], OFF_HINT, `${tier}: greyed, says why without naming a family`);

    // 3. the Fable family: live again by its row's own word
    await pickModel(page, "rs-judgemodel", "Fable");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: false, distillfast: false, indexfast: false }, "the Fable family runs fast by the list");
    assert.equal(await page.evaluate(() => (document.getElementById("rs-judgemodel") as HTMLSelectElement).value), "fable");

    // 4. the seed where the list is silent: Sonnet is listed with no word, and its spelling is not Opus
    await pickModel(page, "rs-indexmodel", "Sonnet");
    s = await snap(page);
    assert.equal(s.disabled.indexfast, true, "an unnamed non-Opus model greys by the seed");
    assert.equal(s.hint.indexfast, OFF_HINT);

    // 5. the comment model (its rows live on the Chat tab): an Opus pick (named not fast) disables the box and, as the
    // user's own gesture, unchecks it
    await page.evaluate(() => { window.postMessage({ romp: "openSettings", tab: "chat" }, "*"); });
    await page.waitForFunction(() => !(document.querySelector(".rs-pane[data-pane=chat]") as HTMLElement).hidden, null, { timeout: 5000 });
    await pickModel(page, "rs-cmtmodel", "Opus");
    assert.deepEqual(await cmt(page), { disabled: true, checked: false }, "the comment box greys and unchecks on the pick");
    assert.deepEqual((await posts(page, "setCommentFast")).map((m: any) => m.fast), ["session"], "the uncheck is posted, once");
    await pickModel(page, "rs-cmtmodel", "Fable");
    assert.deepEqual(await cmt(page), { disabled: false, checked: false }, "Fable brings the box back; the uncheck stands");

    // 6. a models frame carrying a new answer re-gates with no other event: the CLI now says the Fable family's default
    // does not run fast, and Sonnet does (synthetic flips, to see both directions)
    kernel.models = { ...LEARNED, rev: 3, models: LEARNED.models.map((m: any) => m.value === "fable" ? { ...m, fast: false }
      : m.value === "sonnet" ? { ...m, fast: true } : m) };
    await page.evaluate(() => { window.postMessage({ type: "models" }, "*"); });
    await page.waitForFunction(() => (document.getElementById("rs-judgefast") as HTMLInputElement).disabled === true, null, { timeout: 5000 });
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: true, distillfast: true, indexfast: false }, "the frame's list greys the Fable tiers and frees the Sonnet one");
    assert.equal(s.hint.judgefast, OFF_HINT); assert.match(s.hint.indexfast, /^This tier's judge calls/);
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true }, "a repaint unchecks nothing");
    assert.deepEqual(await cmt(page), { disabled: true, checked: false }, "the comment box follows the same list");
    assert.deepEqual((await posts(page, "setCommentFast")).length, 1, "a repaint posts nothing");
    for (const ty of ["setJudgeFast", "setDistillFast", "setIndexFast"]) assert.deepEqual(await posts(page, ty), [], `${ty}: no gate posts a box`);
    assert.deepEqual(errors, [], "no page errors");
  }, kernel);
});
