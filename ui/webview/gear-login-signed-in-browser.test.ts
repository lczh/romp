// A successful Claude login says so (2026-09-30). The user signed in through the settings' Account row: the kernel reported the
// flow done and named the account, and the dashboard still read "Log in to Claude Code", because the one place that named the
// login was the row's hover popover and the dialog closed itself on success. Now the dialog shows "Signed in as <who>" with a
// Close, the paste step gone, and the row carries the login in a permanent line with the button re-running the same flow.
// Driven in headless Chromium over the REAL gear module (gear.js) and its stylesheet against a fake kernel, the
// gear-router-line-browser pattern: the fake answers /version, /logins and /tunnels from a state the gear's own posts move
// (loginStart, loginCode), so the dialog is walked the way a person walks it. Beyond the served lab
// (tests/test_login_signed_in_served.py, the real kernel and a fake CLI) this covers what that lab cannot stage: a read in
// flight across "Sign in again…" still carrying the previous flow's "done" (the dialog must not announce a sign-in that has
// not happened), and a second machine's kernel connected (the row names which machine it is about). Skips with a stated reason
// without a playwright browser (CI installs none). Synthetic values only: dev@example.com, Example Org, TESTHOST.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
const GEAR_CSS = fs.readFileSync(path.join(UI, "gear.css"), "utf8");

// the gear's post reaches the fake kernel as a request the route below answers, so a click moves the fake's state
const ENTRY = `
const { initGear } = require("./gear.js");
(window as any).__posts = [];
initGear((m: any) => { (window as any).__posts.push(m); fetch("/__post", { method: "POST", body: JSON.stringify(m) }); });
`;

function bundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "gear-login-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8><style>${GEAR_CSS}</style></head><body>
<script src=/dist/gear.js></script></body></html>`;
const FAMILIES = [{ value: "opus", label: "Opus", versions: [] }];
const MODELS = { rev: 1, models: FAMILIES, efforts: [{ value: "", label: "Default" }], router: { enabled: false, declared: [], gateway: null, error: null } };
const URL_ = "https://claude.com/cai/oauth/authorize?code=true&client_id=11111111-2222-4333-8444-555555555555&state=SYNTHETIC-STATE";
const EMAIL = "dev@example.com";
const DISPLAY = "dev@example.com · Example Org · personal";

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

// the fake kernel: the machine's login, the flow, and the other kernels connected
type Fake = { acct: string; login: { state: string; url: string; err: string; n: number }; keyHelper: boolean; hold: number; pendingStart: boolean; tunnels: unknown[] };
function version(k: Fake) {
  return { host: "TESTHOST", acctLabel: k.acct, login: { ...k.login }, judgeModel: "opus", indexModel: "opus", autoNudge: true, settingsGt: {}, updateMode: "off" };
}
function logins(k: Fake) {
  return { ok: true, logins: [], machine: { label: k.acct ? DISPLAY : "", acct: k.acct, state: k.acct ? "ok" : "none",
    keyHelper: k.keyHelper, loginUsable: !!k.acct, loginWhy: k.acct ? "" : "no Claude login signed in on this machine" } };
}

const readRow = (page: any) => page.evaluate(() => {
  const el = (id: string) => document.getElementById(id) as HTMLElement;
  const shown = (e: HTMLElement | null) => !!e && getComputedStyle(e).display !== "none" && !e.closest("[hidden]");
  return { button: el("rs-login-btn").textContent, who: el("rs-login-who").textContent, whoShown: shown(el("rs-login-who")),
    bill: el("rs-login-bill").textContent, billShown: shown(el("rs-login-bill")), modalOpen: !el("rs-login-modal").hidden };
});
const readDialog = (page: any) => page.evaluate(() => {
  const el = (id: string) => document.getElementById(id) as HTMLElement;
  const shown = (e: HTMLElement | null) => !!e && getComputedStyle(e).display !== "none" && !e.hidden;
  return { open: !el("rs-login-modal").hidden, ok: el("rs-login-ok").textContent, okShown: shown(el("rs-login-ok")),
    close: shown(el("rs-login-close")), cancel: shown(el("rs-login-cancel")), paste: shown(el("rs-login-code")),
    link: shown(el("rs-login-url")), wait: shown(el("rs-login-status")) ? el("rs-login-wait").textContent : "" };
});

test("a successful login says so in the dialog and on the Account row, a stale read never fakes it, and the row names the machine beside others", async (t) => {
  if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser leg needs it (CI installs no browsers)"); return; }
  let browser: any;
  try { browser = await pw.chromium.launch(); }
  catch (e) { t.skip("no playwright browser on this machine; the browser leg needs one (CI installs none): " + String((e as Error).message).split("\n")[0]); return; }
  const errors: string[] = [];
  const k: Fake = { acct: "", login: { state: "", url: "", err: "", n: 0 }, keyHelper: true, hold: 0, pendingStart: false, tunnels: [] };
  const start = () => { k.login = { state: "url", url: URL_, err: "", n: k.login.n + 1 }; };
  try {
    const js = bundle();
    const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    await page.route("**/*", (route: any) => {
      const req = route.request(), u = new URL(req.url());
      const json = (o: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(o) });
      if (u.hostname !== "romp.test") return route.fulfill({ status: 404, body: "" });
      if (u.pathname === "/gear") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE_HTML });
      if (u.pathname === "/dist/gear.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: js });
      if (u.pathname === "/__post") {
        const m = JSON.parse(req.postData() || "{}");
        if (m.type === "loginStart") { if (k.hold > 0) k.pendingStart = true; else start(); }
        if (m.type === "loginCode") k.login = { ...k.login, state: "verifying" };
        if (m.type === "loginCancel") k.login = { ...k.login, state: "", url: "" };
        return json({});
      }
      if (u.pathname === "/version") {
        if (k.pendingStart && k.hold > 0) { k.hold--; return json(version(k)); }   // the start not reported yet: the previous flow's read
        if (k.pendingStart) { k.pendingStart = false; start(); }
        const out = version(k);
        if (k.login.state === "verifying") { k.acct = EMAIL; k.login = { state: "done", url: "", err: "", n: k.login.n }; }   // the verdict lands on the next read
        return json(out);
      }
      if (u.pathname === "/logins") return json(logins(k));
      if (u.pathname === "/models") return json(MODELS);
      if (u.pathname === "/tunnels") return json({ tunnels: k.tunnels });
      return json({});
    });
    await page.goto("http://romp.test/gear");
    await page.waitForFunction(() => !!document.getElementById("rs-login-btn"), null, { timeout: 10000 });
    await page.evaluate(() => { window.postMessage({ romp: "openSettings", tab: "general" }, "*"); });
    await page.waitForFunction(() => (document.getElementById("rs-login-acct") as HTMLElement).textContent !== "…", null, { timeout: 10000 });

    // 1. no login on this machine: today's button, no signed-in line, no billing line
    const none = await readRow(page);
    assert.equal(none.button, "Log in to Claude Code", "with no login the button is today's");
    assert.equal(none.whoShown, false, "…and no signed-in line takes room");
    assert.equal(none.billShown, false, "…nor a billing line: there is no login to explain");

    // 2. the flow: the button opens the dialog, the link and the paste box come, a code goes in, the kernel says done
    await page.click("#rs-login-btn");
    await page.waitForFunction(() => getComputedStyle(document.getElementById("rs-login-code") as HTMLElement).display !== "none", null, { timeout: 10000 });
    await page.fill("#rs-login-input", "SYNTH-GOOD-CODE#SYNTH-STATE");
    await page.click("#rs-login-send");
    await page.waitForFunction(() => !(document.getElementById("rs-login-ok") as HTMLElement).hidden, null, { timeout: 10000 });
    await page.waitForFunction((d: string) => (document.getElementById("rs-login-ok") as HTMLElement).textContent === "Signed in as " + d, DISPLAY, { timeout: 10000 });
    const done = await readDialog(page);
    assert.equal(done.open, true, "the dialog stays up to say how it went");
    assert.equal(done.ok, "Signed in as " + DISPLAY, "…naming the login the way the Billing surfaces do");
    assert.equal(done.close, true, "Close is offered");
    assert.equal(done.cancel, false, "…in Cancel's place: there is nothing left to cancel");
    assert.equal(done.paste, false, "the paste box is gone");
    assert.equal(done.link, false, "…and so is the sign-in link");
    assert.equal(done.wait, "", "no wait is narrated");

    // 3. Close, and the row at rest names the login, says how it bills beside the key, and offers the same flow again
    await page.click("#rs-login-close");
    const rest = await readRow(page);
    assert.equal(rest.modalOpen, false, "Close closes the dialog");
    assert.equal(rest.who, "Signed in as " + DISPLAY);
    assert.equal(rest.whoShown, true, "…in a line that shows without a hover");
    assert.equal(rest.bill, "An API key is also set up here; a session uses this account only when its Billing is Login.");
    assert.equal(rest.button, "Sign in again…");
    assert.equal((await page.evaluate(() => (window as any).__posts.filter((m: any) => m.type === "loginCancel").length)), 0, "Close cancels nothing");

    // 4. Sign in again, with the kernel slow to report the start: the reads in between still carry the previous flow's "done",
    // and the dialog must keep saying it is starting rather than announce that finished flow as this one's outcome
    k.hold = 3;
    await page.click("#rs-login-btn");
    const deadline = Date.now() + 10000;
    while (k.hold > 0 && Date.now() < deadline) {   // loop-ok: bounded by the deadline; ends when the fake has answered its three stale reads
      const mid = await readDialog(page);
      assert.equal(mid.okShown, false, "a read from before the start is not this flow's outcome");
      assert.equal(mid.open, true, "…and does not close the dialog either");
      await new Promise((r) => setTimeout(r, 50));
    }
    assert.equal(k.hold, 0, "the dialog polled through the stale reads");
    const stale = await readDialog(page);
    assert.equal(stale.okShown, false, "after the last stale read the dialog still has not announced anything");
    await page.waitForFunction(() => getComputedStyle(document.getElementById("rs-login-code") as HTMLElement).display !== "none", null, { timeout: 10000 });
    const again = await readDialog(page);
    assert.equal(again.okShown, false, "the new flow reached its paste step");
    assert.equal(again.cancel, true, "…with Cancel back");
    await page.click("#rs-login-cancel");

    // 5. another machine's kernel connected: the row names which machine it is about
    k.tunnels = [{ host: "api-box", status: "up", settings: {} }];
    await page.evaluate(() => { window.postMessage({ romp: "openSettings" }, "*"); });   // the opener toggles: closed…
    await page.waitForFunction(() => (document.getElementById("rsettings") as HTMLElement).hidden, null, { timeout: 5000 });
    await page.evaluate(() => { window.postMessage({ romp: "openSettings", tab: "general" }, "*"); });   // …and open again, which re-reads
    await page.waitForFunction(() => (document.getElementById("rs-login-who") as HTMLElement).textContent!.startsWith("Signed in on "), null, { timeout: 10000 });
    assert.equal((await readRow(page)).who, "Signed in on TESTHOST as " + DISPLAY);
    assert.deepEqual(errors, [], "no page errors");
  } finally {
    await browser.close();
  }
});
