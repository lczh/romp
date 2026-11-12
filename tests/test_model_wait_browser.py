#!/usr/bin/env python3
"""A model pick waiting for its relaunch, on the real page (the review of #455): the model badge's dots, and what they wait for.

A pick the session's older Claude Code does not know (Fable 5.2 on a CLI launched before it) waits for the quiet relaunch onto
the installed Claude Code, and that relaunch waits until nothing the session runs would be cut. With a monitor or a dev server
running, that is a long time, and the badge showed only its switching dots. Read in a browser over a hermetic kernel serving
the real /chat page, in the dark theme and in Yatharth light (`theme-light chat-theme-yatharth`):
  1. after the kernel's boot, web's model badge still wears the dots: its registry holds the saved pick (stalePick) and its
     pending mark, and the boot's heal of pending picks keeps a saved pick's mark (before the fix it healed it away, and the
     badge read the model from before the pick, with the picker's fallback mark);
  2. the badge's hover says the pick and what the relaunch waits for, above what the badge does;
  3. the tab's hover says it in the Model row, after the model the CLI runs;
  4. api, with no pick waiting, shows its model and the plain hover.
WHAT THE LAB STANDS IN FOR: the sessions are registered and alive with no CLI running this life, so the kernel's row for web is
its dormant one, which carries the pending mark from the registry but not what the relaunch waits for (a live session's
snapshot: relaunchWaits, and modelWait, the pick's name). The driver adds those two fields to web's status as the frames pass
the page's socket, and nothing else; the snapshot's own fields are pinned in tests/test_stale_cli_model_pick.py and the words
in ui/webview/status-controls.test.ts. MODEL_WAIT_SHOTS=<dir> writes the badge's hover and the tab's in both themes there.
Skips LOUDLY without the extension deps or a Playwright browser, and never otherwise. SYNTHETIC fixtures only: the notes-api
world, placeholder sids, invented prose.
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
SIDS = {"web": "ab1e0003-1111-2222-3333-444444444444", "api": "ab1e0004-1111-2222-3333-444444444444"}
PICK, PICK_LABEL = "claude-fable-5-2", "Fable 5.2"
WAITS = "1 subagent and 2 background tasks"   # what a live session's snapshot would say holds web's relaunch
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
// web's status as a live session's snapshot carries it: the waiting pick's name and what its relaunch waits for. Every frame
// naming web with a status object gets the two fields; its pending mark is the kernel's own, from its registry row
const patch = (o) => {
  if (Array.isArray(o)) { o.forEach(patch); return; }
  if (!o || typeof o !== "object") return;
  if (o.id === cfg.sidWeb && o.status && typeof o.status === "object") Object.assign(o.status, { modelWait: cfg.pickLabel, relaunchWaits: cfg.waits });
  for (const v of Object.values(o)) if (v && typeof v === "object") patch(v);
};
const tipLines = (page) => page.evaluate(() => {
  const tip = document.querySelector(".romp-tip");
  if (!tip || getComputedStyle(tip).display === "none") return null;
  return Array.from(tip.children, (d) => d.textContent);
});
const tabRow = (page, label) => page.evaluate((l) => {
  const tip = document.querySelector(".tab-tip");
  if (!tip || getComputedStyle(tip).display === "none") return null;
  const row = Array.from(tip.querySelectorAll(".tab-tip-row")).find((r) => r.children[0] && r.children[0].textContent === l);
  return row ? row.children[1].textContent : null;
}, label);
const clipAround = (bb, pad, below) => ({ x: Math.max(0, bb.x - pad), y: Math.max(0, bb.y - pad), width: Math.min(1100, bb.width + 2 * pad), height: bb.height + pad + below });
try {
for (const theme of ["dark", "light"]) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 700 } });
  if (theme === "light") await ctx.addInitScript((t) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, "yatharth-light");
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.routeWebSocket(() => true, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((m) => server.send(m));
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
  t.body = await page.evaluate(() => Array.from(document.body.classList));
  for (const name of ["web", "api"]) {
    const sid = cfg.sids[name];
    const r = t[name] = {};
    await page.click('#tabs .tab[data-id="' + sid + '"]');
    await page.waitForSelector('#statusline .meta-btn[data-kind="model"]', { timeout: 10000 });
    await page.waitForTimeout(600);
    const badge = '#statusline .meta-btn[data-kind="model"]';
    r.dots = !!(await page.$(badge + " .meta-dots"));
    r.label = await page.evaluate((b) => document.querySelector(b + " .meta-label").textContent, badge);
    await page.hover(badge);
    await page.waitForTimeout(250);
    r.tip = await tipLines(page);
    if (cfg.shots && name === "web") {
      const bb = await (await page.$(badge)).boundingBox();
      const tb = await (await page.$(".romp-tip")).boundingBox();
      const x = Math.max(0, Math.min(bb.x, tb.x) - 60), y = Math.max(0, Math.min(bb.y, tb.y) - 40);
      const x2 = Math.min(1100, Math.max(bb.x + bb.width, tb.x + tb.width) + 60), y2 = Math.min(700, Math.max(bb.y + bb.height, tb.y + tb.height) + 40);
      await page.screenshot({ path: cfg.shots + "/model-badge-hover-" + theme + ".png", clip: { x, y, width: x2 - x, height: y2 - y } });
    }
    await page.mouse.move(5, 650);
    await page.waitForTimeout(300);
    // the picker: no fallback mark beside the pick while it waits
    await page.click(badge);
    await page.waitForSelector(".meta-menu", { timeout: 5000 }).catch(() => {});
    r.requested = await page.evaluate(() => document.querySelectorAll(".meta-menu .meta-item.requested").length);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    await page.hover('#tabs .tab[data-id="' + sid + '"]');
    await page.waitForTimeout(300);
    r.tabModel = await tabRow(page, "Model");
    if (cfg.shots && name === "web") {
      const tb = await (await page.$(".tab-tip")).boundingBox();
      const tab = await (await page.$('#tabs .tab[data-id="' + sid + '"]')).boundingBox();
      const x = Math.max(0, Math.min(tb.x, tab.x) - 30), y = Math.max(0, Math.min(tb.y, tab.y) - 20);
      const x2 = Math.min(1100, Math.max(tb.x + tb.width, tab.x + tab.width) + 30), y2 = Math.min(700, Math.max(tb.y + tb.height, tab.y + tab.height) + 20);
      await page.screenshot({ path: cfg.shots + "/tab-hover-" + theme + ".png", clip: { x, y, width: x2 - x, height: y2 - y } });
    }
    await page.mouse.move(5, 650);
    await page.waitForTimeout(300);
  }
  await ctx.close();
}
} catch (e) { out.error = String(e && e.stack || e); }
fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
console.log("RESULT: ok");
"""


class ServedModelWait(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="model-wait-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own (the conftest rule): no real session host
        Path(state, "retry-upgrade").write_text("on\n")    # Retry upgrades after downgrades on, as on the machines it shipped to
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, name in enumerate(NAMES):
            sid = SIDS[name]
            bg, fg = PALETTE[i % len(PALETTE)]
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            reg = {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                   "model": "opus", "liveModel": "Opus 5.5", "liveModelId": "claude-opus-5-5", "servedModel": "Opus 5.5"}
            if name == "web":
                # web's pick of Fable 5.2 was refused by its older Claude Code and kept for the relaunch onto the installed one:
                # the saved pick and its pending mark, as _keep_pick_for_newer_cli leaves the registry
                reg.update(model=PICK, modelPending=True, stalePick={"pick": PICK, "back": "opus"})
            Path(state, "sdk", sid + ".json").write_text(json.dumps(reg))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "keep an eye on the %s side of notes-api while it deploys" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "Watching the %s deploy; I will say when it settles." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        home = os.path.join(cls.lab, "home")
        os.makedirs(home, exist_ok=True)
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-model-wait"
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
                k.kill()
                k.wait()
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
        shots = os.environ.get("MODEL_WAIT_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": out,
                       "sidWeb": SIDS["web"], "sids": SIDS, "waits": WAITS, "pickLabel": PICK_LABEL, "shots": shots}, f)
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
        self.assertNotIn("theme-light", r["themes"]["dark"]["body"])
        self.assertTrue({"theme-light", "chat-theme-yatharth"} <= set(r["themes"]["light"]["body"]), r["themes"]["light"]["body"])

    def test_1_after_the_boot_the_waiting_pick_keeps_its_dots_and_no_fallback_mark(self):
        r = self._run()
        reg = json.loads(Path(self.state, "sdk", SIDS["web"] + ".json").read_text())
        self.assertTrue(reg.get("modelPending"), "the boot's heal kept the saved pick's mark: %r" % reg)
        for theme, t in r["themes"].items():
            self.assertTrue(t["web"]["dots"], theme + ": the badge's switching dots: " + json.dumps(t["web"]))
            self.assertEqual(t["web"]["requested"], 0, theme + ": no fallback mark beside the pick in the picker")

    def test_2_the_badges_hover_says_the_pick_and_what_its_relaunch_waits_for(self):
        r = self._run()
        for theme, t in r["themes"].items():
            self.assertEqual(t["web"]["tip"], ["%s applies after %s" % (PICK_LABEL, WAITS), "change model (sends /model)"],
                             theme + ": " + json.dumps(t["web"]))

    def test_3_the_tabs_hover_says_it_after_the_model_the_cli_runs(self):
        r = self._run()
        for theme, t in r["themes"].items():
            self.assertEqual(t["web"]["tabModel"], "Opus 5.5 (%s applies after %s)" % (PICK_LABEL, WAITS), theme)

    def test_4_a_session_with_no_pick_waiting_reads_as_before(self):
        r = self._run()
        for theme, t in r["themes"].items():
            a = t["api"]
            self.assertEqual((a["dots"], a["label"], a["tip"], a["tabModel"]),
                             (False, "Opus 5.5", ["change model (sends /model)"], "Opus 5.5"), theme + ": " + json.dumps(a))


if __name__ == "__main__":
    unittest.main()
