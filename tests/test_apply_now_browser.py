#!/usr/bin/env python3
"""Apply now on the real page (2026-10-01): a billing or effort pick waiting for a quiet session offers one row, and its
click relaunches at once after Restart session's confirm.

Since that day a billing, effort, fast, bypass-mode or env pick relaunches the session's CLI only once nothing would be
cut, and a session whose long-running monitors never let it go quiet kept its pick waiting for hours. The pick's own
control offers the way through, at the cost it names. Read in a browser over a hermetic kernel serving the real /chat
page, in the dark theme and in Yatharth light (the settings key `yatharth-light`):
  1. web's Billing row says what its pick waits on, and its flyout carries, behind a rule under the picks and above Set
     default billing, ONE row: "Apply now" with the sub-line naming what it ends, in the menu vocabulary's sub-line dress
     (0.82em, 0.6 opacity); api, with nothing waiting, has no such row;
  2. the click closes the menu and opens the confirm in Restart session's shape (the title asks, the detail names what it
     ends and what the session keeps, two plain buttons); Cancel sends nothing;
  3. the confirm's own Apply now sends ONE applyNow op for web, the kernel takes it (its log names the door), and no
     refusal toast follows;
  4. the effort badge's picker carries the same row behind its own rule.
WHAT THE LAB STANDS IN FOR: these sessions are registered and alive with no CLI running this life, so the kernel's row for
web is its dormant one, which carries the waiting picks' marks from the registry (authPending, effortPending, and the
relaunchWait record apply_now reads) but not what holds them: that is a live session's snapshot (relaunchWaits, and
relaunchCuts for the row). The driver adds those two fields to web's status as the frames pass the page's socket, and
nothing else; the op goes to the real kernel, whose apply_now finds the recorded pick and takes the dormant road (the
connect is the relaunch). The live road, the notice the session gets and its words, run in
tests/test_reconnect_waits_for_work.py; the row's rules in ui/webview/apply-now.test.ts.
APPLY_NOW_DIST=<dir> serves another tree's UI bundle (the red run's before); APPLY_NOW_SHOTS=<dir> writes the flyout,
the confirm and the effort picker in both themes there. Skips LOUDLY without the extension deps or a Playwright browser,
and never otherwise (CI turns a skip in a served module into a failure). SYNTHETIC fixtures only: the notes-api world,
placeholder sids, invented prose.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

NAMES = ["web", "api"]
SIDS = {"web": "ab1e0001-1111-2222-3333-444444444444", "api": "ab1e0002-1111-2222-3333-444444444444"}
WAITS = "2 background tasks"          # what a live session's snapshot would say holds web's picks
CUTS = "2 background tasks"           # …and what pushing them through would end
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#54B204", "#ffffff")]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { themes: {} };
const sent = [];
// web's status as a live session's snapshot carries it: what holds its waiting picks, what pushing them through ends, and
// its effort pick's pending mark (a kernel's boot heals a dormant session's effort mark as moot, since its next launch
// carries the pick; a live session keeps it while the pick waits). Every frame naming web with a status object gets the
// three fields; nothing else is touched, and web's billing mark is the kernel's own, from its registry row.
const patch = (o) => {
  if (Array.isArray(o)) { o.forEach(patch); return; }
  if (!o || typeof o !== "object") return;
  if (o.id === cfg.sidWeb && o.status && typeof o.status === "object") Object.assign(o.status, { relaunchWaits: cfg.waits, relaunchCuts: cfg.cuts, effortPending: true });
  for (const v of Object.values(o)) if (v && typeof v === "object") patch(v);
};
const readFly = (page) => page.evaluate(() => {
  const fly = document.querySelector(".ctx-sub-billing");
  if (!fly) return null;
  const kids = Array.from(fly.children);
  const lines = kids.map((k) => k.classList.contains("ctx-sep") ? "---" : k.classList.contains("ctx-item-setdefault") ? "[set default]"
    : k.classList.contains("ctx-item-apply") ? "[apply] " + k.querySelector(".ctx-item-label").textContent + " | " + k.querySelector(".ctx-item-sub").textContent
    : k.textContent.trim());
  const row = fly.querySelector(".ctx-item-apply");
  const pick = kids.find((k) => k.classList.contains("ctx-item") && !k.classList.contains("ctx-item-apply") && !k.classList.contains("ctx-item-setdefault"));
  const px = (e) => e ? parseFloat(getComputedStyle(e).fontSize) : null;
  const rowEl = document.querySelector(".ctx-menu .ctx-item-billing .ctx-item-sub");
  return { lines, billingSub: rowEl ? rowEl.textContent : null,
    sizes: row ? { pick: px(pick), label: px(row.querySelector(".ctx-item-label")), sub: px(row.querySelector(".ctx-item-sub")),
                   subOpacity: getComputedStyle(row.querySelector(".ctx-item-sub")).opacity } : null };
});
const openBilling = async (page, sid) => {
  await page.click('#tabs .tab[data-id="' + sid + '"]', { button: "right" });
  await page.waitForSelector(".ctx-menu .ctx-item-billing", { timeout: 5000 });
  const row = await page.$(".ctx-menu .ctx-item-billing"); const bb = await row.boundingBox();
  await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2);
  await page.waitForSelector(".ctx-sub-billing", { timeout: 1500 }).catch(async () => { await row.click(); });
  await page.waitForSelector(".ctx-sub-billing", { timeout: 3000 });
  await page.waitForTimeout(150);
};
const readConfirm = (page) => page.evaluate(() => {
  const c = document.getElementById("confirm");
  if (!c) return null;
  return { title: c.querySelector(".confirm-title").textContent, detail: c.querySelector(".confirm-detail").textContent,
           buttons: Array.from(c.querySelectorAll(".confirm-btn"), (b) => ({ label: b.textContent.trim(), danger: b.classList.contains("danger") })) };
});
let darkPage = null, darkCtx = null;
try {
for (const theme of ["dark", "light"]) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 700 } });
  if (theme === "light") await ctx.addInitScript((t) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, "yatharth-light");
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.routeWebSocket(() => true, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((m) => { try { const f = JSON.parse(m); if (f && f.type === "applyNow") sent.push({ theme, op: f }); } catch (e) { /* not JSON */ } server.send(m); });
    server.onMessage((m) => {
      if (typeof m === "string" && m.includes(cfg.sidWeb)) { try { const f = JSON.parse(m); patch(f); m = JSON.stringify(f); } catch (e) { /* not JSON: as is */ } }
      ws.send(m);
    });
    server.onClose(() => ws.close()); ws.onClose(() => server.close());
  });
  await page.goto(cfg.chat);
  await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await page.waitForTimeout(800);
  const t = out.themes[theme] = { errors };
  t.light = await page.evaluate(() => document.body.classList.contains("theme-light"));
  // 1. web's flyout, and api's
  await openBilling(page, cfg.sidWeb);
  t.web = await readFly(page);
  if (cfg.shots) await page.screenshot({ path: cfg.shots + "/billing-flyout-" + theme + ".png", clip: { x: 0, y: 0, width: 1100, height: 520 } });
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  await openBilling(page, cfg.sidApi);
  t.api = await readFly(page);
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  // 2. the click: the menu goes, the confirm comes; Cancel sends nothing
  await openBilling(page, cfg.sidWeb);
  await page.click(".ctx-sub-billing .ctx-item-apply");
  await page.waitForSelector("#confirm", { timeout: 5000 });
  t.menuAfterClick = !!(await page.$(".ctx-menu"));
  t.confirm = await readConfirm(page);
  if (cfg.shots) await page.screenshot({ path: cfg.shots + "/confirm-" + theme + ".png", clip: { x: 0, y: 0, width: 1100, height: 700 } });
  await page.click("#confirm .confirm-btn:nth-child(2)");
  await page.waitForFunction(() => !document.getElementById("confirm"), null, { timeout: 5000 });
  await page.waitForTimeout(300);
  t.sentAfterCancel = sent.filter((s) => s.theme === theme).length;
  // 4. the effort picker on the active tab (web): the same row behind its own rule
  await page.click('#tabs .tab[data-id="' + cfg.sidWeb + '"]');
  await page.waitForSelector('#statusline .meta-btn[data-kind="effort"]', { timeout: 10000 });
  await page.click('#statusline .meta-btn[data-kind="effort"]');
  await page.waitForSelector(".meta-menu", { timeout: 5000 });
  t.effort = await page.evaluate(() => {
    const m = document.querySelector(".meta-menu");
    const kids = Array.from(m.children);
    const last = kids.slice(-2);
    return { tail: last.map((k) => k.classList.contains("meta-sep") ? "---" : k.classList.contains("meta-apply")
      ? "[apply] " + k.firstElementChild.textContent + " | " + k.querySelector(".meta-item-sub").textContent : k.textContent.trim()) };
  });
  if (cfg.shots) {
    const box = await page.$(".meta-menu"); const bb = await box.boundingBox();
    const x = Math.max(0, bb.x - 40), y = Math.max(0, bb.y - 40);
    await page.screenshot({ path: cfg.shots + "/effort-picker-" + theme + ".png", clip: { x, y, width: Math.min(1100 - x, bb.width + 80), height: Math.min(700 - y, bb.height + 120) } });
  }
  await page.keyboard.press("Escape"); await page.waitForTimeout(150);
  if (theme === "dark") { darkPage = page; darkCtx = ctx; } else await ctx.close();
}
// 3. last, on the dark page (the relaunch it starts ends the pick's wait, and with it every row above): the confirm's own
// Apply now sends the op once, and the kernel takes it
const page = darkPage;
await openBilling(page, cfg.sidWeb);
await page.click(".ctx-sub-billing .ctx-item-apply");
await page.waitForSelector("#confirm", { timeout: 5000 });
await page.click("#confirm .confirm-btn:nth-child(1)");
await page.waitForFunction(() => !document.getElementById("confirm"), null, { timeout: 5000 });
const until = Date.now() + 8000;
while (Date.now() < until && !fs.readFileSync(cfg.klog, "utf8").includes("apply now: " + cfg.sidWeb)) await page.waitForTimeout(200);   // loop-ok: the kernel's own log line is the event
await page.waitForTimeout(800);
out.toasts = await page.evaluate(() => Array.from(document.querySelectorAll(".warn-toast-msg"), (e) => e.textContent));
} catch (e) { out.error = String(e && e.stack || e); }
if (darkCtx) await darkCtx.close().catch(() => {});
out.sent = sent;
fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
console.log("RESULT: ok");
"""


class ServedApplyNow(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="apply-now-")
        before = os.environ.get("APPLY_NOW_DIST", "")
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
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        # a lab root writes its own session-hosts off (the conftest rule): hosts are on by default, and Apply now's dormant
        # road connects web, which must start no real session host here
        Path(state, "session-hosts").write_text("off\n")
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, name in enumerate(NAMES):
            sid = SIDS[name]
            bg, fg = PALETTE[i % len(PALETTE)]
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            reg = {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                   "model": "claude-opus-5", "liveModel": "Opus 5"}
            if name == "web":
                # web's billing and effort picks wait for its relaunch: the pending marks the dormant row reports, and the
                # record a kernel keeps of which picks wait (relaunchWait, #70), which apply_now reads for a dormant session
                reg.update(auth="login", authPending=True, effort="max", effortPending=True,
                           relaunchWait={"why": "billing", "t": t0, "picks": {"billing": {"auth": "login", "authLogin": ""},
                                                                              "effort": {"effort": "max"}}})
            Path(state, "sdk", sid + ".json").write_text(json.dumps(reg))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "keep an eye on the %s side of notes-api while it deploys" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "Watching the %s deploy; I will say when it settles." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        # both billing sides on this machine (the Billing flyout lab's staging): a staged apiKeyHelper, never run, and a
        # synthetic Claude account in the kernel's own home
        helper = Path(claude, "helper.sh"); helper.write_text("#!/bin/sh\necho not-a-real-key\n"); helper.chmod(0o700)
        Path(claude, "settings.json").write_text(json.dumps({"apiKeyHelper": str(helper)}))
        home = os.path.join(cls.lab, "home"); os.makedirs(home, exist_ok=True)
        Path(home, ".claude.json").write_text(json.dumps({"oauthAccount": {"accountUuid": "11111111-2222-3333-4444-555555555555",
                                                                             "emailAddress": "notes-api@example.com", "organizationName": "Example Org"}}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-apply-now"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST", HOME=home)
        cls.state = state
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
        shots = os.environ.get("APPLY_NOW_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": out,
                       "sidWeb": SIDS["web"], "sidApi": SIDS["api"], "waits": WAITS, "cuts": CUTS, "klog": cls.klog, "shots": shots}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        result = json.loads(Path(out).read_text())
        if result.get("error"):
            raise AssertionError("the driver threw: " + result["error"] + "\n  " + json.dumps(result)[:2500] + "\nkernel:\n" + open(cls.klog).read()[-1200:])
        return result

    def test_0_the_pages_threw_nothing_and_each_theme_is_the_one_asked_for(self):
        r = self._run()
        for theme, t in r["themes"].items():
            self.assertEqual(t["errors"], [], theme)
        self.assertEqual((r["themes"]["dark"]["light"], r["themes"]["light"]["light"]), (False, True), "dark, then Yatharth light")

    def test_1_the_waiting_billing_pick_offers_one_row_under_the_picks_saying_what_it_ends(self):
        r = self._run()
        for theme, t in r["themes"].items():
            f = t["web"]
            table = "\n  %s: %s" % (theme, json.dumps(f))
            self.assertIsNotNone(f, "the flyout was read" + table)
            self.assertEqual(f["billingSub"], "applies after " + WAITS, "the Billing row says what the pick waits on" + table)
            self.assertEqual(f["lines"][-4:], ["---", "[apply] Apply now | ends " + CUTS, "---", "[set default]"],
                             "behind a rule under the picks, above Set default billing" + table)
            s = f["sizes"]
            self.assertAlmostEqual(s["label"], s["pick"], delta=0.05, msg="the label wears the picks' size" + table)
            self.assertAlmostEqual(s["sub"], s["label"] * 0.82, delta=0.3, msg="the menu vocabulary's sub-line, 0.82em" + table)
            self.assertEqual(s["subOpacity"], "0.6", "…at 0.6 opacity" + table)

    def test_2_a_session_with_nothing_waiting_has_no_row(self):
        r = self._run()
        for theme, t in r["themes"].items():
            self.assertNotIn("[apply]", " ".join(t["api"]["lines"]), theme + ": " + json.dumps(t["api"]))

    def test_3_the_click_closes_the_menu_and_confirms_in_restart_sessions_shape_and_cancel_sends_nothing(self):
        r = self._run()
        for theme, t in r["themes"].items():
            self.assertFalse(t["menuAfterClick"], theme + ": the card goes first: the dialog is not layered over an open menu")
            c = t["confirm"]
            self.assertEqual(c["title"], "Apply the change to “web” now?", theme)
            self.assertTrue(c["detail"].startswith("Applying it now ends " + CUTS + ". It keeps its name, its place and its whole conversation"),
                            theme + ": " + c["detail"])
            self.assertEqual(c["buttons"], [{"label": "Apply now", "danger": False}, {"label": "Cancel", "danger": False}], theme)
            self.assertEqual(t["sentAfterCancel"], 0, theme + ": Cancel sends nothing")

    def test_4_the_confirms_apply_now_sends_one_op_the_kernel_takes_it_and_no_refusal_follows(self):
        r = self._run()
        self.assertEqual(r["sent"], [{"theme": "dark", "op": {"type": "applyNow", "id": SIDS["web"]}}],
                         "one op, the id alone, and only from the confirm's own button (both themes' Cancel sent none)")
        self.assertIn("apply now: %s via applyNow WS op" % SIDS["web"], open(self.klog).read(), "the kernel's door took it")
        self.assertEqual(r["toasts"], [], "no refusal: the recorded pick was there to apply")

    def test_5_the_effort_picker_offers_the_same_row_behind_its_own_rule(self):
        r = self._run()
        for theme, t in r["themes"].items():
            self.assertEqual(t["effort"]["tail"], ["---", "[apply] Apply now | ends " + CUTS], theme + ": " + json.dumps(t["effort"]))


if __name__ == "__main__":
    unittest.main()
