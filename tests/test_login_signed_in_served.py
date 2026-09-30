#!/usr/bin/env python3
"""A successful Claude login says so, on the served dashboard (2026-09-30). The user signed in through the settings' Account row
(Log in to Claude Code, the code from the sign-in page, Submit) and the login worked: the kernel reported the flow done and named
the account. The dashboard still read "Log in to Claude Code" and gave no sign of it, because the dialog closed itself on success
and the one place naming the login was the row's hover popover.

A hermetic kernel runs a FAKE `claude auth login --claudeai` (the transcript tests/test_login_flow.py's mock follows): it prints
the sign-in link and the paste prompt, and on the synthetic good code writes a synthetic account into the kernel's own home
(~/.claude.json, the file the kernel names the login from) and the kind word into its Claude config dir, then exits 0. The
machine also has an apiKeyHelper staged in that config dir (read, never run), so the billing line has something true to say.
The driver opens the real dashboard's settings on General and reads, in the dark theme: the Account row with no login (today's
button, no signed-in line); the flow through the dialog to its outcome ("Signed in as <who>", Close in Cancel's place, the
paste box and the link gone); after Close, the row at rest ("Signed in as <who>", the billing line, "Sign in again…") and the
kernel's flow still reading done (Close cancels nothing). Then, in Yatharth light (the settings key `yatharth-light`, the product's
own light theme), a fresh page reads the row at rest from the start and re-runs the flow through "Sign in again…" to the same
outcome, each start a new flow number.

LOGIN_SHOTS=<dir> writes success-dark.png, row-dark.png, row-light.png and success-light.png there. LOGIN_DIST=<dir> serves
another tree's UI bundle (the red run's before). Skips LOUDLY without the extension deps or a Playwright browser, and never
otherwise (CI turns a skip in a served module into a failure). Synthetic throughout: dev@example.com, Example Org, TESTHOST."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

EMAIL, ORG = "dev@example.com", "Example Org"
DISPLAY = "%s · %s · personal" % (EMAIL, ORG)   # the machine login as the Billing surfaces name it (subscriptionType pro)
GOOD = "SYNTH-GOOD-CODE#SYNTH-STATE"
BILL = "An API key is also set up here; a session uses this account only when its Billing is Login."

# the fake CLI: `auth login --claudeai` as CLI 2.1.282 speaks it over pipes, with a synthetic link; anything else is refused at
# once (the lab kernel's other spawns never reach a real CLI). The good code writes the synthetic account the way the real CLI's
# success leaves it: ~/.claude.json's oauthAccount and the credentials file's subscriptionType (no token: nothing reads one here)
FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:] != ["auth", "login", "--claudeai"]:
    sys.stderr.write("fake claude: unexpected invocation\n"); sys.exit(2)
url = "https://claude.com/cai/oauth/authorize?" + "&".join([
    "code=true", "client_id=11111111-2222-4333-8444-555555555555", "response_type=code",
    "redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback", "scope=user%3Aprofile+user%3Ainference",
    "code_challenge=SYNTHETIC-CHALLENGE", "code_challenge_method=S256", "state=SYNTHETIC-STATE"])
sys.stdout.write("Opening browser to sign in…\nIf the browser didn't open, visit: " + url + "\nPaste code here if prompted > ")
sys.stdout.flush()
for line in sys.stdin:
    if line.strip() != @@GOOD@@:
        sys.stderr.write("Login failed: Request failed with status code 400\n"); sys.exit(1)
    def put(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    put(os.path.join(os.environ["HOME"], ".claude.json"), {"oauthAccount": {
        "accountUuid": "11111111-2222-3333-4444-555555555555", "emailAddress": @@EMAIL@@, "organizationName": @@ORG@@}})
    put(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".credentials.json"), {"claudeAiOauth": {"subscriptionType": "pro"}})
    sys.stdout.write("Login successful.\n"); sys.stdout.flush(); sys.exit(0)
sys.exit(1)
'''.replace("@@GOOD@@", repr(GOOD)).replace("@@EMAIL@@", repr(EMAIL)).replace("@@ORG@@", repr(ORG))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
if (cfg.shots) fs.mkdirSync(cfg.shots, { recursive: true });
// the theme the PRODUCT's way: the settings store's key, read at boot by every pane and by the shell
const THEMES = { dark: "classic", light: "yatharth-light" };
const out = { errors: [] };
const kernelLogin = async () => { const r = await fetch(cfg.version); const v = await r.json(); return { login: v.login, acct: v.acctLabel }; };

async function openSettings(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 2 });
  await ctx.addInitScript((t) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, THEMES[theme]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push(theme + ": " + String((e && e.message) || e).slice(0, 300)));
  await page.goto(cfg.url);
  await page.waitForSelector("#rail-gear", { timeout: 20000 });
  await page.waitForFunction(() => typeof window.__rompOpenSettings === "function", null, { timeout: 20000 });
  await page.evaluate(() => window.__rompOpenSettings("general"));
  let setF = null;
  for (let i = 0; i < 100 && !setF; i++) { setF = page.frames().find((f) => f.url().includes("/settings")) || null; if (!setF) await page.waitForTimeout(100); }
  if (!setF) throw new Error("the settings frame never loaded");
  await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
  await setF.waitForFunction(() => document.getElementById("rs-login-acct").textContent !== "…", null, { timeout: 15000 });
  await setF.evaluate(() => (document.fonts && document.fonts.ready) || null).catch(() => {});
  await page.waitForTimeout(300);
  return { ctx, page, setF };
}
// the reads tolerate a missing element (an older bundle served by LOGIN_DIST has none of the new ones): null says it is absent
const readRow = (setF) => setF.evaluate(() => {
  const el = (id) => document.getElementById(id), txt = (id) => (el(id) ? el(id).textContent : null);
  const shown = (e) => !!e && getComputedStyle(e).display !== "none" && e.getBoundingClientRect().height > 0;
  return { button: txt("rs-login-btn"), who: txt("rs-login-who"), whoShown: shown(el("rs-login-who")),
    bill: txt("rs-login-bill"), billShown: shown(el("rs-login-bill")), modalOpen: !el("rs-login-modal").hidden,
    body: document.body.className };
});
const readDialog = (setF) => setF.evaluate(() => {
  const el = (id) => document.getElementById(id), txt = (id) => (el(id) ? el(id).textContent : null);
  const shown = (e) => !!e && getComputedStyle(e).display !== "none" && e.getBoundingClientRect().height > 0;
  return { open: !el("rs-login-modal").hidden, ok: txt("rs-login-ok"), okShown: shown(el("rs-login-ok")),
    close: shown(el("rs-login-close")), cancel: shown(el("rs-login-cancel")), paste: shown(el("rs-login-code")),
    link: shown(el("rs-login-url")), wait: shown(el("rs-login-status")) ? txt("rs-login-wait") : "" };
});
// a shot of a box inside the settings frame, in page coordinates, with `pad` of what surrounds it
async function shot(page, setF, sel, name, pad) {
  if (!cfg.shots) return;
  const b = await setF.evaluate((s) => { const r = document.querySelector(s).getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height }; }, sel);
  const fr = await page.evaluate(() => { const r = document.getElementById("f-settings").getBoundingClientRect(); return { x: r.left, y: r.top }; });
  await page.screenshot({ path: cfg.shots + "/" + name + ".png",
    clip: { x: Math.max(0, fr.x + b.x - pad), y: Math.max(0, fr.y + b.y - pad), width: b.width + 2 * pad, height: b.height + 2 * pad } });
}
const page_settle = (f) => f.waitForTimeout(150);
// the dialog walked the way a person walks it: the button, the link, the code, Submit, the outcome
async function signIn(setF) {
  await setF.click("#rs-login-btn");
  await setF.waitForSelector("#rs-login-url a", { state: "visible", timeout: 20000 });
  const link = await setF.evaluate(() => document.querySelector("#rs-login-url a").href);
  await setF.fill("#rs-login-input", cfg.code);
  await setF.click("#rs-login-send");
  // the kernel's verdict lands: the dialog shows its outcome (or, in an older bundle, closes itself on done)
  await setF.waitForFunction(() => { const ok = document.getElementById("rs-login-ok"); return (ok && !ok.hidden) || document.getElementById("rs-login-modal").hidden; },
    null, { timeout: 20000 });
  await page_settle(setF);
  // the stored logins' read that follows names the organisation and kind beside the email
  await setF.waitForFunction((d) => { const ok = document.getElementById("rs-login-ok"); return !ok || ok.textContent === "Signed in as " + d; }, cfg.display, { timeout: 10000 }).catch(() => {});
  return link;
}
try {
  {
    const { ctx, page, setF } = await openSettings("dark");
    out.none = await readRow(setF);
    out.link1 = await signIn(setF);
    out.done1 = await readDialog(setF);
    await shot(page, setF, "#rs-login-modal .rs-login-card", "success-dark", 28);
    if (out.done1.close) await setF.click("#rs-login-close");
    await setF.waitForFunction(() => document.getElementById("rs-login-modal").hidden, null, { timeout: 5000 });
    await setF.waitForFunction(() => { const b = document.getElementById("rs-login-bill"); return !b || b.textContent !== ""; }, null, { timeout: 10000 }).catch(() => {});
    out.rest1 = await readRow(setF);
    out.kernel1 = await kernelLogin();
    await page.mouse.move(2, 2);   // the pointer off the card: a row under it would float its hover popover into the shot
    await shot(page, setF, "#rsettings .rs-card", "row-dark", 0);
    // the row under the pointer floats ONE description: the stored logins' note was a second rs-sub at the same spot
    await setF.hover("#rs-billing b"); await page.waitForTimeout(200);
    out.hover1 = await setF.evaluate(() => Array.from(document.querySelectorAll("#rs-billing .rs-sub")).filter((e) => getComputedStyle(e).display !== "none").map((e) => e.textContent));
    await page.mouse.move(2, 2);
    await ctx.close();
  }
  {
    const { ctx, page, setF } = await openSettings("light");
    await setF.waitForFunction(() => { const b = document.getElementById("rs-login-bill"); return !b || b.textContent !== ""; }, null, { timeout: 10000 }).catch(() => {});
    out.restLight = await readRow(setF);
    await page.mouse.move(2, 2);
    await shot(page, setF, "#rsettings .rs-card", "row-light", 0);
    out.link2 = await signIn(setF);
    out.done2 = await readDialog(setF);
    await shot(page, setF, "#rs-login-modal .rs-login-card", "success-light", 28);
    if (out.done2.close) await setF.click("#rs-login-close");
    out.kernel2 = await kernelLogin();
    await ctx.close();
  }
} catch (e) {
  out.error = String((e && e.stack) || e).slice(0, 1500);
}
await browser.close();
fs.writeFileSync(cfg.out, JSON.stringify(out));
"""


class ServedLoginSignedIn(unittest.TestCase):
    maxDiff = None
    result = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="login-signed-in-")
        before = os.environ.get("LOGIN_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        # a lab root writes its own session-hosts off (the conftest rule): hosts are on by default
        Path(state, "session-hosts").write_text("off\n")
        claude = os.path.join(cls.lab, "claude")
        os.makedirs(claude, exist_ok=True)
        # the key side: an apiKeyHelper in the kernel's Claude config dir (read, never run), so the billing line applies
        helper = Path(claude, "helper.sh")
        helper.write_text("#!/bin/sh\necho not-a-real-key\n")
        helper.chmod(0o700)
        Path(claude, "settings.json").write_text(json.dumps({"apiKeyHelper": str(helper)}))
        # the kernel's own home: no ~/.claude.json until the fake CLI's success writes one (no login on this machine yet)
        cls.home = os.path.join(cls.lab, "home")
        os.makedirs(cls.home, exist_ok=True)
        fake = os.path.join(cls.lab, "bin", "claude")
        os.makedirs(os.path.dirname(fake), exist_ok=True)
        Path(fake).write_text(FAKE_CLI)
        os.chmod(fake, 0o755)
        cls.port, cls.token = _free_port(), "testtok-login-signed-in"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST", HOME=cls.home,
                              ROMP_CLAUDE_BIN=fake)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.terminate()
            try:
                k.wait(timeout=10)
            except subprocess.TimeoutExpired:
                k.kill(); k.wait()
            time.sleep(0.5)
        lab = getattr(cls, "lab", "")
        shutil.rmtree(lab, ignore_errors=True)
        time.sleep(0.3)
        shutil.rmtree(lab, ignore_errors=True)

    @classmethod
    def _run(cls):
        if cls.result is not None:
            if isinstance(cls.result, BaseException):
                raise cls.result
            return cls.result
        try:
            cls.result = cls._drive()
        except BaseException as e:
            cls.result = e
            raise
        return cls.result

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        base = "http://127.0.0.1:%d" % cls.port
        with open(cfg, "w") as f:
            json.dump({"url": base + "/?token=" + cls.token, "version": base + "/version?token=" + cls.token, "out": out,
                       "code": GOOD, "display": DISPLAY, "shots": os.environ.get("LOGIN_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0 or not os.path.exists(out):
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        result = json.loads(Path(out).read_text())
        if result.get("error"):
            raise AssertionError("the driver threw: " + result["error"] + "\n  read so far: " + json.dumps(result)[:1500]
                                 + "\nkernel:\n" + open(cls.klog).read()[-1200:])
        return result

    def test_with_no_login_the_row_offers_todays_button_and_no_signed_in_line(self):
        r = self._run()["none"]
        self.assertEqual(r["button"], "Log in to Claude Code", r)
        self.assertEqual(r["who"], "", "the signed-in line is there and says nothing: %r" % r)
        self.assertFalse(r["whoShown"], "…so it takes no room: %r" % r)
        self.assertFalse(r["billShown"], "no billing line without a login: %r" % r)

    def test_after_done_the_dialog_says_who_with_close_and_the_paste_box_is_gone(self):
        r = self._run()
        self.assertTrue(r["link1"].startswith("https://claude.com/cai/oauth/authorize?code=true"), "the fake CLI's link reached the dialog: %r" % r["link1"])
        d = r["done1"]
        self.assertEqual(d["ok"], "Signed in as " + DISPLAY, d)
        self.assertTrue(d["open"] and d["okShown"], "the dialog stays up to say how it went: %r" % d)
        self.assertTrue(d["close"], "Close is offered: %r" % d)
        self.assertFalse(d["cancel"], "…in Cancel's place: %r" % d)
        self.assertFalse(d["paste"], "the paste box is gone: %r" % d)
        self.assertFalse(d["link"], "…and the sign-in link: %r" % d)
        self.assertEqual(d["wait"], "", "no wait is narrated: %r" % d)

    def test_at_rest_the_row_reads_signed_in_as_with_the_billing_line_and_sign_in_again(self):
        r = self._run()
        for key in ("rest1", "restLight"):
            row = r[key]
            self.assertFalse(row["modalOpen"], "%s: %r" % (key, row))
            self.assertEqual(row["who"], "Signed in as " + DISPLAY, "%s: %r" % (key, row))
            self.assertTrue(row["whoShown"], "%s: the line shows without a hover: %r" % (key, row))
            self.assertEqual(row["bill"], BILL, "%s: an apiKeyHelper is configured here, so the line says how the login bills: %r" % (key, row))
            self.assertTrue(row["billShown"], "%s: %r" % (key, row))
            self.assertEqual(row["button"], "Sign in again…", "%s: %r" % (key, row))
        self.assertEqual(r["kernel1"]["login"]["state"], "done", "Close cancels nothing: the kernel still reads the flow done")
        self.assertEqual(r["kernel1"]["acct"], EMAIL)

    def test_sign_in_again_runs_the_same_flow_to_the_same_outcome_in_yatharth_light(self):
        r = self._run()
        body = r["restLight"]["body"].split()
        self.assertIn("theme-light", body, "the product's light theme: %r" % body)
        self.assertIn("chat-theme-yatharth", body, "…Yatharth light, not a bare theme-light: %r" % body)
        d = r["done2"]
        self.assertEqual(d["ok"], "Signed in as " + DISPLAY, d)
        self.assertTrue(d["close"] and not d["cancel"] and not d["paste"], d)
        self.assertEqual((r["kernel1"]["login"]["n"], r["kernel2"]["login"]["n"]), (1, 2), "each start is a new flow number")
        self.assertEqual(r["kernel2"]["login"]["state"], "done")

    def test_the_hovered_row_floats_one_description(self):
        shown = self._run()["hover1"]
        self.assertEqual(len(shown), 1, "one popover under the pointer, never two stacked: %r" % shown)
        self.assertIn("Each machine keeps its own Claude login.", shown[0])
        self.assertIn("No other Claude logins stored on this machine.", shown[0], "the stored logins' note rides the same one")

    def test_no_page_errors(self):
        self.assertEqual(self._run()["errors"], [])


if __name__ == "__main__":
    unittest.main()
