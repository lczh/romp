// A click on a MIXED checkbox (the machines disagree) turns the setting OFF for every machine in scope, and one more click
// turns it on: one rule for every mixable box in the gear. Off is the side that does nothing: on runs the judges (Task
// tracking), writes into sessions (Auto Nudge), writes to disk (File editing), costs more (the fast and upgrade boxes) or
// acts on sessions (Suggest /compact, Conserve memory), so a click that has to pick a side for every machine lands on the
// one that is safe by mistake (the #377 review; #377 had it land on).
// Driven in headless Chromium over the REAL gear module (gear.js) and its stylesheet, the gear-judge-fast-browser pattern,
// against a fake kernel pair: this machine (TESTHOSTA) answers /version, and one connected peer (TESTHOSTB) rides /tunnels
// with every one of the fourteen boxes set the other way, half of them on here and half off, so the rule cannot be read as
// "the opposite of this machine's value". After the fill every box is mixed, held ticked under the dash, and the hover says
// what a click does; each box then takes two clicks in one synchronous step (the gear's re-reads after a click cannot land
// between them), the first posting off and the second on. Under a pick of both machines the hover names them and a click
// posts off to them alone. The comment model's Fast side effect holds under the new rendering: a non-Opus comment pick
// while the Fast box is mixed turns it off everywhere, unticked and greyed. Skips with a stated reason without a playwright
// browser, and fails with it where CI requires the leg (browser-leg.ts). Synthetic values only.
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
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "gear-mixed-click-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8><style>${GEAR_CSS}</style></head><body>
<script src=/dist/gear.js></script></body></html>`;
const MODELS = { models: [{ value: "opus", label: "Opus", versions: [] }, { value: "sonnet", label: "Sonnet", versions: [] }],
  efforts: [{ value: "", label: "Default" }, { value: "low", label: "Low" }] };

// every mixable box: its id, its /version key, the message its click posts, and its value on THIS machine (the peer holds the
// other). `raw`: a RAW on/off string store (commentFast's off is "session")
type Box = { id: string; key: string; type: string; here: boolean; raw?: boolean };
const BOXES: Box[] = [
  { id: "rs-tasktrack", key: "taskTracking", type: "setTaskTracking", here: true },   // on here: off would disable the judge rows
  { id: "rs-autonudge", key: "autoNudge", type: "setAutoNudge", here: false },
  { id: "rs-fileedit", key: "fileEditing", type: "setFileEditing", here: true },
  { id: "rs-suggestcompact", key: "compactSuggest", type: "setCompactSuggest", here: false },
  { id: "rs-cmtfast", key: "commentFast", type: "setCommentFast", here: true, raw: true },
  { id: "rs-judgefast", key: "judgeFast", type: "setJudgeFast", here: false, raw: true },
  { id: "rs-distillfast", key: "distillFast", type: "setDistillFast", here: true, raw: true },
  { id: "rs-indexfast", key: "indexFast", type: "setIndexFast", here: false, raw: true },
  { id: "rs-alwaysfast", key: "alwaysFast", type: "setAlwaysFast", here: true, raw: true },
  { id: "rs-retryupgrade", key: "retryUpgrade", type: "setRetryUpgrade", here: false, raw: true },
  { id: "rs-thinksum", key: "thinkingSummaries", type: "setThinkingSummaries", here: true },
  { id: "rs-wholechat", key: "wholeChatFrames", type: "setWholeChatFrames", here: false },
  { id: "rs-conserve", key: "conserveMemory", type: "setConserve", here: true },
  { id: "rs-router", key: "routerModels", type: "setRouterModels", here: false },
];
const stored = (b: Box, on: boolean): boolean | string => b.raw ? (on ? "on" : (b.key === "commentFast" ? "session" : "off")) : on;
const SELECTS = { judgeModel: "opus", judgeEffort: "", indexModel: "opus", indexEffort: "", distillModel: "triage", distillEffort: "triage",
  judgeConcurrency: "", commentModel: "session", commentEffort: "session", updateMode: "off" };   // the same on both: only the boxes differ
const valuesOf = (here: boolean) => Object.fromEntries(BOXES.map((b) => [b.key, stored(b, here ? b.here : !b.here)]));
const VERSION = { host: "TESTHOSTA", ...SELECTS, ...valuesOf(true), settings: { ...SELECTS, ...valuesOf(true) }, settingsGt: {}, settingsPinned: {} };
const PEER = { host: "TESTHOSTB", status: "up", autoNudge: !BOXES[1].here, settings: { ...SELECTS, ...valuesOf(false) }, settingsPinned: {} };
const posted = (m: any): boolean => typeof m.enabled === "boolean" ? m.enabled : m.fast === "on";

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

async function withGear(t: any, body: (page: any, errors: string[]) => Promise<void>): Promise<void> {
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
      if (u.pathname === "/models") return json(MODELS);
      if (u.pathname === "/version") return json(VERSION);
      if (u.pathname === "/tunnels") return json({ tunnels: [PEER] });
      return json({});   // /palette, /analytics and the rest: empty answers, nothing under test
    });
    await page.goto("http://romp.test/gear");
    await page.waitForFunction(() => Array.isArray((window as any).__posts) && !!document.getElementById("rs-tasktrack"), null, { timeout: 10000 });
    await page.evaluate(() => { window.postMessage({ romp: "openSettings", tab: "chat" }, "*"); });
    // the cross-machine fill has run once every box reads mixed (only the /tunnels read sets the dash)
    await page.waitForFunction((ids: string[]) => ids.every((id) => (document.getElementById(id) as HTMLInputElement).indeterminate),
      BOXES.map((b) => b.id), { timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

// a box's state and the hover of the mark nearest it (its own <label>, else the row: the gear's own lookup)
const look = (page: any, id: string) => page.evaluate((id: string) => {
  const el = document.getElementById(id) as HTMLInputElement;
  const row = el.closest("label") || el.closest(".rs-row"); const mark = row && row.querySelector(".rs-mixed") as HTMLElement | null;
  return { checked: el.checked, indeterminate: el.indeterminate, disabled: el.disabled, mark: mark && !mark.hidden ? mark.textContent : null, title: mark ? mark.title : "" };
}, id);
const postsOf = (page: any, type: string): Promise<any[]> => page.evaluate((ty: string) => (window as any).__posts.filter((m: any) => m && m.type === ty), type);

test("every mixable box reads mixed held ticked under the dash; its first click posts off, its second on", { timeout: 90000 }, async (t) => {
  await withGear(t, async (page, errors) => {
    for (const b of BOXES) {
      const s = await look(page, b.id);
      assert.deepEqual([s.indeterminate, s.checked, s.disabled], [true, true, false], `${b.id}: mixed, held TICKED under the dash so a click lands on off, and live`);
      assert.equal(s.mark, "differs", `${b.id}: the row wears the flag`);
    }
    // the hover ends with what a click does: a row that follows the machine picker says how to set one machine alone, one
    // that does not (the judge, comment and Model rows apply on every machine) stops at the rule
    const ths = await look(page, "rs-thinksum");
    assert.equal(ths.title, "The kernels disagree: TESTHOSTA (this machine) on, TESTHOSTB off. A click turns it off for every machine; click again to turn it on. Pick a machine above to set it alone.");
    const jf = await look(page, "rs-judgefast");
    assert.equal(jf.title, "The kernels disagree: TESTHOSTA (this machine) off, TESTHOSTB on. A click turns it off for every machine; click again to turn it on.");
    const asub = await page.evaluate(() => (document.getElementById("rs-autonudge-sub") as HTMLElement).textContent || "");
    assert.ok(asub.endsWith(" Right now these still have it on: TESTHOSTB. A click turns it off for every machine; click again to turn it on. Pick a machine above to set it alone."),
      "Auto Nudge's line under the label says the same rule: " + asub);
    assert.deepEqual(await page.evaluate(() => (window as any).__posts.filter((m: any) => m && /^set/.test(m.type))), [], "a fill posts nothing");

    // two clicks per box in ONE synchronous step: a click clears the dash and toggles the box, as the user's does
    const clicks = await page.evaluate((ids: string[]) => ids.map((id) => {
      const el = document.getElementById(id) as HTMLInputElement;
      el.click(); const first = { checked: el.checked, indeterminate: el.indeterminate };
      el.click(); const second = { checked: el.checked, indeterminate: el.indeterminate };
      return { id, first, second };
    }), BOXES.map((b) => b.id));
    for (const c of clicks) {
      assert.deepEqual(c.first, { checked: false, indeterminate: false }, `${c.id}: the first click shows off, the dash gone`);
      assert.deepEqual(c.second, { checked: true, indeterminate: false }, `${c.id}: the second shows on`);
    }
    for (const b of BOXES) {
      const ps = await postsOf(page, b.type);
      assert.deepEqual(ps.map(posted), [false, true], `${b.id}: the first click posted OFF, whichever way this machine stood (${b.here ? "on" : "off"} here); the second ON`);
      assert.ok(ps.every((m) => typeof m.gt === "number"), `${b.id}: each post stamped`);
      assert.ok(ps.every((m) => !m.hosts && !m.scope), `${b.id}: under All kernels the posts are the broadcast, never scoped`);
    }
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("under a pick of both machines a mixed row's hover names them, and a click posts off to them alone", { timeout: 90000 }, async (t) => {
  await withGear(t, async (page, errors) => {
    const pick = (name: string) => page.evaluate((name: string) => {
      const opt = Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).find((o) => (o.textContent || "").trim() === name);
      (opt!.querySelector("input") as HTMLInputElement).click();
    }, name);
    await pick("TESTHOSTA (this machine)");
    await page.waitForFunction(() => document.getElementById("rs-scope-label")!.textContent === "TESTHOSTA (this machine)", null, { timeout: 10000 });
    await pick("TESTHOSTB");
    await page.waitForFunction(() => {
      const el = document.getElementById("rs-thinksum") as HTMLInputElement, m = el.closest("label")!.querySelector(".rs-mixed") as HTMLElement;
      return document.getElementById("rs-scope-label")!.textContent === "TESTHOSTA (this machine), TESTHOSTB" && / for TESTHOSTA/.test(m.title);
    }, null, { timeout: 10000 });
    const ths = await look(page, "rs-thinksum");
    assert.deepEqual([ths.indeterminate, ths.checked], [true, true], "the picked machines disagree: mixed, held ticked");
    assert.ok(ths.title.endsWith(". A click turns it off for TESTHOSTA (this machine), TESTHOSTB."), "the hover names the picked machines: " + ths.title);
    const tk = await look(page, "rs-tasktrack");
    assert.ok(tk.title.endsWith(". A click turns it off for TESTHOSTA (this machine), TESTHOSTB."), "a synchronized row the same: " + tk.title);
    const jf = await look(page, "rs-judgefast");
    assert.ok(jf.title.endsWith(". A click turns it off for every machine; click again to turn it on."), "a row that ignores the pick keeps every machine: " + jf.title);
    const sent = await page.evaluate(() => { const el = document.getElementById("rs-thinksum") as HTMLInputElement; el.click(); return { checked: el.checked, indeterminate: el.indeterminate }; });
    assert.deepEqual(sent, { checked: false, indeterminate: false });
    const ps = await postsOf(page, "setThinkingSummaries");
    assert.equal(ps.length, 1);
    assert.equal(ps[0].enabled, false, "the click on the mixed box posted OFF");
    assert.deepEqual([ps[0].hosts, ps[0].scope], [["", "TESTHOSTB"], "pinned"], "…to the picked machines alone");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a non-Opus comment model pick while the Fast box is mixed turns Fast off on every machine, unticked and greyed", { timeout: 90000 }, async (t) => {
  await withGear(t, async (page, errors) => {
    const before = await look(page, "rs-cmtfast");
    assert.deepEqual([before.indeterminate, before.checked, before.disabled], [true, true, false], "mixed (on here, off on the peer), live under Same as the session");
    // the comment model select is display:none behind versionMenu's button: open it and click the family row, as the user does
    await page.click("#rs-cmtmodel + button.rs-vermenu-btn");
    await page.locator('body > div > div[tabindex="0"]', { hasText: "Sonnet" }).first().click();
    await page.waitForFunction(() => (document.getElementById("rs-cmtmodel") as HTMLSelectElement).value === "sonnet", null, { timeout: 5000 });
    const after = await look(page, "rs-cmtfast");
    assert.deepEqual([after.indeterminate, after.checked, after.disabled], [false, false, true], "Fast is unticked, the dash gone, the box greyed: Sonnet cannot run fast");
    assert.deepEqual((await postsOf(page, "setCommentModel")).map((m) => m.model), ["sonnet"]);
    const fast = await postsOf(page, "setCommentFast");
    assert.deepEqual(fast.map((m) => m.fast), ["session"], "one post turns Fast off, the broadcast that reaches the peer that had it off and this machine that had it on");
    assert.ok(!fast[0].hosts && typeof fast[0].gt === "number");
    assert.deepEqual(errors, [], "no page errors");
  });
});
