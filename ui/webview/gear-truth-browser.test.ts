// Three ways the settings panel could still say something untrue about the machines (the gear-truth review, 2026-10-23), and the
// screen reader's reading of a box that wears the "differs" flag. Driven in headless Chromium over the REAL gear module (gear.js)
// and its stylesheet, the gear-mixed-click-browser pattern, against a fake kernel pair this file controls: this machine
// (TESTHOSTA) answers /version, one connected peer (TESTHOSTB) rides /tunnels, and the test can hold a /tunnels answer, change
// either machine's values, and answer the re-read a peer's acknowledgement asks for (/tunnels?reread=TESTHOSTB).
// (1) A fill wrote this machine's values the moment /version answered and drew the mixed state only when /tunnels did, so for that
//     round trip a box the last fill had mixed showed its dash over this machine's value: where the setting was off here, a click
//     on the dash turned it ON for every machine. Now a fill writes once, after both reads, and a fill that began before a click
//     draws nothing. With the /tunnels answer held, the dash holds ticked, its click posts off, and the held answer, released with
//     the peer as it stood before the click, does not draw the dash back.
// (2) After a click applied on the peer, the panel compared against this kernel's copy of the peer from its last supervisor pass,
//     so the row went back to the dash and "click again to turn it on" posted off again. The peer's settingAck now makes the panel
//     draw with this kernel re-reading the peer first; past the gear's first re-read after the click the row reads plain off, and
//     the second click posts on.
// (3) The settings page shows a dropped message (federation's warn) as a notice, a dropped setting in the row's own words; a feed
//     host, which has its own surface for warns, does not.
// (4) Each kernel-backed box is named by its title alone and described by its state and what a click does.
// Skips with a stated reason without a playwright browser, and fails with it where CI requires the leg (browser-leg.ts).
// Synthetic values only.
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
initGear((m: any) => { (window as any).__posts.push(m); }, { ownPage: /[?&]own=1/.test(location.search) });
`;

function bundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "gear-truth-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8><style>${GEAR_CSS}</style></head><body>
<script src=/dist/gear.js></script></body></html>`;
const MODELS = { models: [{ value: "opus", label: "Opus", versions: [] }], efforts: [{ value: "", label: "Default" }] };

// The machines. Thinking summaries is OFF here and ON on the peer: the case where a click on a dash drawn over this machine's
// value turned it on everywhere. Task tracking and the triage judges' Fast mode differ too (the screen reader leg reads them).
type Machine = { thinkingSummaries: boolean; taskTracking: boolean; judgeFast: string };
const SELECTS = { judgeModel: "opus", judgeEffort: "", indexModel: "opus", indexEffort: "", distillModel: "triage", distillEffort: "triage",
  judgeConcurrency: "", commentModel: "session", commentEffort: "session", commentFast: "session", updateMode: "off" };
const versionOf = (m: Machine) => ({ host: "TESTHOSTA", ...SELECTS, ...m, settings: { ...SELECTS, ...m }, settingsGt: {}, settingsPinned: {} });
const peerOf = (m: Machine) => ({ host: "TESTHOSTB", status: "up", settings: { ...SELECTS, ...m }, settingsPinned: {} });

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

type Lab = {
  here: Machine; peer: Machine;   // what each machine holds now
  peerCopy: Machine;              // this kernel's copy of the peer, what a plain /tunnels read serves (the supervisor's last pass)
  hold: boolean;                  // hold every /tunnels answer, snapshotted at its request, until release()
  held: Array<() => Promise<void>>;
  tunnelsAsked: string[];         // every /tunnels URL the page asked for, query and all
  heldArrived: (() => void) | null;
};

async function withGear(t: any, opts: { own?: boolean }, body: (page: any, lab: Lab, errors: string[]) => Promise<void>): Promise<void> {
  const browser = await launchLeg(t, pw, "chromium");
  if (!browser) return;
  const errors: string[] = [];
  const lab: Lab = {
    here: { thinkingSummaries: false, taskTracking: true, judgeFast: "off" },
    peer: { thinkingSummaries: true, taskTracking: false, judgeFast: "on" },
    peerCopy: { thinkingSummaries: true, taskTracking: false, judgeFast: "on" },
    hold: false, held: [], tunnelsAsked: [], heldArrived: null,
  };
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
      if (u.pathname === "/version") return json(versionOf(lab.here));
      if (u.pathname === "/tunnels") {
        lab.tunnelsAsked.push(u.search);
        // the kernel re-reads the peer when asked (kernel.py _reread_peer_settings): its copy is the peer as it stands from then on
        if (u.searchParams.get("reread") === "TESTHOSTB") lab.peerCopy = { ...lab.peer };
        const answer = { tunnels: [peerOf(lab.peerCopy)] };   // the answer as it stands at the request
        if (lab.hold) {
          lab.held.push(() => json(answer));
          if (lab.heldArrived) { lab.heldArrived(); lab.heldArrived = null; }
          return undefined;
        }
        return json(answer);
      }
      return json({});   // /palette, /logins, /analytics and the rest: empty answers, nothing under test
    });
    await page.goto("http://romp.test/gear" + (opts.own ? "?own=1" : ""));
    await page.waitForFunction(() => Array.isArray((window as any).__posts) && !!document.getElementById("rs-thinksum"), null, { timeout: 10000 });
    await page.evaluate(() => { window.postMessage({ romp: "openSettings", tab: "chat" }, "*"); });
    // the first fill has drawn once the box reads mixed (only a fill's drawing sets the dash)
    await page.waitForFunction(() => (document.getElementById("rs-thinksum") as HTMLInputElement).indeterminate, null, { timeout: 10000 });
    await body(page, lab, errors);
  } finally {
    await browser.close();
  }
}

const box = (page: any, id: string) => page.evaluate((id: string) => {
  const el = document.getElementById(id) as HTMLInputElement;
  return { checked: el.checked, indeterminate: el.indeterminate };
}, id);
const postsOf = (page: any, type: string): Promise<any[]> => page.evaluate((ty: string) => (window as any).__posts.filter((m: any) => m && m.type === ty), type);

test("a click on a mixed box while the next fill's /tunnels answer is on its way posts OFF, and that late answer draws nothing", { timeout: 90000 }, async (t) => {
  await withGear(t, {}, async (page, lab, errors) => {
    assert.deepEqual(await box(page, "rs-thinksum"), { checked: true, indeterminate: true }, "the first fill: mixed, held ticked under the dash");
    // a second fill, its /tunnels answer held: /version has answered (it is asked first) once the held /tunnels request arrives
    lab.hold = true;
    const arrived = new Promise<void>((res) => { lab.heldArrived = res; });
    await page.evaluate(() => { (window as any).__rompSettingsClose(); window.postMessage({ romp: "openSettings", tab: "chat" }, "*"); });
    await arrived;
    await page.waitForTimeout(150);   // the /version answer's handler has long run: the old fill wrote this machine's off here
    const during = await box(page, "rs-thinksum");
    assert.deepEqual(during, { checked: true, indeterminate: true },
      "while the answer is on its way the box shows the last drawing and HOLDS it: the dash, ticked (it held this machine's off under the dash)");
    await page.click("#rs-thinksum");
    const ps = await postsOf(page, "setThinkingSummaries");
    assert.deepEqual(ps.map((m) => m.enabled), [false], "the click on the dash posted OFF for every machine, never on");
    // the click applied on both; the held answer still carries the peer from before it
    lab.here = { ...lab.here, thinkingSummaries: false }; lab.peer = { ...lab.peer, thinkingSummaries: false };
    lab.hold = false;
    for (const f of lab.held.splice(0)) await f();
    await page.waitForTimeout(200);   // the late answer's handler runs; the gear's first re-read after the click is 700 ms out
    assert.deepEqual(await box(page, "rs-thinksum"), { checked: false, indeterminate: false },
      "the fill that began before the click drew nothing: its answer would have put the dash back");
    assert.deepEqual((await postsOf(page, "setThinkingSummaries")).map((m) => m.enabled), [false], "and nothing else was posted");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("the peer's acknowledgement makes the panel re-read it, so after a click turns both off a second click turns both on", { timeout: 90000 }, async (t) => {
  await withGear(t, {}, async (page, lab, errors) => {
    await page.click("#rs-thinksum");
    assert.deepEqual((await postsOf(page, "setThinkingSummaries")).map((m) => m.enabled), [false], "the first click: off for every machine");
    // both machines apply it; this kernel's copy of the peer is still its last pass's (on) until something re-reads the peer
    lab.here = { ...lab.here, thinkingSummaries: false }; lab.peer = { ...lab.peer, thinkingSummaries: false };
    // the peer says it handled the setting (federation dispatches its frame, the host stamped)
    await page.evaluate(() => window.dispatchEvent(new MessageEvent("message",
      { data: { type: "settingAck", host: "TESTHOSTB", msgType: "setThinkingSummaries", gt: 1 } })));
    await page.waitForTimeout(900);   // past the gear's first re-read after the click (700 ms), which drew the dash back before
    assert.ok(lab.tunnelsAsked.includes("?reread=TESTHOSTB"), "the acknowledgement asked this kernel to re-read the peer: " + JSON.stringify(lab.tunnelsAsked));
    assert.deepEqual(await box(page, "rs-thinksum"), { checked: false, indeterminate: false }, "both machines off: the row reads plain off, no dash");
    const mark = await page.evaluate(() => { const m = document.getElementById("rs-thinksum")!.closest("label")!.querySelector(".rs-mixed") as HTMLElement; return m.hidden ? null : m.textContent; });
    assert.equal(mark, null, "no differs flag");
    await page.click("#rs-thinksum");
    assert.deepEqual((await postsOf(page, "setThinkingSummaries")).map((m) => m.enabled), [false, true], "the second click, within a second, turns it on");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("under All kernels a click names the machines the panel shows, so a machine the page has not linked yet is still reached", { timeout: 90000 }, async (t) => {
  await withGear(t, {}, async (page, _lab, errors) => {
    await page.click("#rs-thinksum");
    const ps = await postsOf(page, "setThinkingSummaries");
    assert.equal(ps.length, 1);
    assert.deepEqual(ps[0].reach, ["TESTHOSTB"], "the broadcast carries the shown machines (federation reaches each of them)");
    assert.ok(!ps[0].hosts && !ps[0].scope, "still the broadcast, never a scope");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("the settings page shows a dropped message as a notice, a dropped setting in the row's words; a feed host does not", { timeout: 90000 }, async (t) => {
  const warn = (page: any, data: any) => page.evaluate((d: any) => window.dispatchEvent(new MessageEvent("message", { data: d })), data);
  const toasts = (page: any): Promise<string[]> => page.evaluate(() => Array.from(document.querySelectorAll("#rs-stale-toasts .rs-stale-toast-msg")).map((x) => x.textContent || ""));
  await withGear(t, { own: true }, async (page, _lab, errors) => {
    await warn(page, { type: "warn", host: "TESTHOSTB", msgType: "setThinkingSummaries", cause: "unlinked",
                       text: "this page has no connection to TESTHOSTB — “setThinkingSummaries” was not delivered" });
    await warn(page, { type: "warn", text: "No stored login with that id; it may already be gone." });
    assert.deepEqual(await toasts(page), ["Thinking summaries: not applied on TESTHOSTB. This page has no connection to it.",
                                          "No stored login with that id; it may already be gone."]);
    assert.deepEqual(errors, [], "no page errors");
  });
  await withGear(t, {}, async (page, _lab, errors) => {
    await warn(page, { type: "warn", text: "No stored login with that id; it may already be gone." });
    assert.deepEqual(await toasts(page), [], "a feed host has its own surface for a warn");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a box is named by its title alone and described by its state, what a click does, then its line", { timeout: 90000 }, async (t) => {
  await withGear(t, {}, async (page, _lab, errors) => {
    const named = async (name: string) => page.getByRole("checkbox", { name, exact: true, includeHidden: true }).count();
    assert.equal(await named("Task tracking"), 1, "Task tracking's box: its title, not \"Task trackingdiffers\" and the pin's label");
    assert.equal(await named("Thinking summaries"), 1);
    assert.equal(await named("Fast mode (triage judges)"), 1, "a Fast mode box, whose label holds no title, by the gear's name for it");
    const said = (id: string) => page.evaluate((id: string) => {
      const el = document.getElementById(id)!;
      const ids = (el.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      const mark = el.closest("label")!.querySelector(".rs-mixed")!;
      return { parts: ids.map((x) => (document.getElementById(x)?.textContent || "").trim()), markHidden: mark.getAttribute("aria-hidden") };
    }, id);
    const tk = await said("rs-tasktrack");
    assert.equal(tk.markHidden, "true", "the visible flag is not read twice");
    assert.equal(tk.parts[0], "differs. The kernels disagree: TESTHOSTA (this machine) on, TESTHOSTB off. "
      + "A click turns it off for every machine; click again to turn it on. Pick a machine above to set it alone.",
      "the description opens with the state and the click rule, not only the hover");
    assert.match(tk.parts[1], /^romp reads every session/, "…then the row's line");
    const jf = await said("rs-judgefast");
    assert.match(jf.parts[0], /^differs\. The kernels disagree: TESTHOSTA \(this machine\) off, TESTHOSTB on\. A click turns it off for every machine; click again to turn it on\.$/);
    assert.deepEqual(errors, [], "no page errors");
  });
});
