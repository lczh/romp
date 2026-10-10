#!/usr/bin/env python3
"""A send its kernel never read survives a reload and is delivered by the page's resend (2026-10-09).

Two messages a user typed into a session were lost without a trace: their frames sat unread in a socket that then
closed, and the reload that followed took the page's only copy of the words with it. This lab drives the real /chat
page against a hermetic kernel and reproduces the page's half of that: the first send's frame is DROPPED on its way
out (the init script swallows it at the socket, exactly what an unread frame in a closing socket amounts to), then the
page is reloaded. It pins that:

  - before the reload the bubble shows the text, and no kernel answer arrives for it (the kernel never had it);
  - after the reload the bubble is back, captioned "not confirmed", with the text on it;
  - the page posts the send again, marked as a resend, once the kernel's keepalive says it recognises one;
  - the kernel answers that resend (sendAck ok) and the label goes, so the words reached the session.

Before the fix the reload dropped the text: nothing on the page carried it. SYNTHETIC fixtures only (the notes-api demo
world); skips loudly without the extension deps or a Playwright browser.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment
from test_send_bubble_visible_browser import SID, _free_port, _seed_records   # noqa: E402  the same mid-turn session

DRIVER_TIMEOUT_S = 240
TEXT = "please rebuild the notes-api search index before the release"

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
page.on("pageerror", (e) => fs.appendFileSync(cfg.consoleLog, "pageerror: " + e + "\n"));
await page.addInitScript((text) => {
  window.__sent = []; window.__acks = []; window.__firstPaint = null; window.__ackAt = null; window.__resendAt = null;
  window.addEventListener("message", (e) => { const m = e.data; if (m && m.type === "sendAck") { window.__acks.push({ qid: m.qid, ok: m.ok, dup: m.dup }); if (m.ok && window.__ackAt === null) window.__ackAt = performance.now(); } });
  // the FIRST paint of a bubble carrying the text on this page load, with its label, and when it happened: the label a restored
  // send wears is "not confirmed" only until its kernel answers, and the answer can land before the session's frame paints
  // (a poll would read whichever came first, CI 2026-10-10)
  const seen = () => {
    if (window.__firstPaint) return;
    const turn = Array.from(document.querySelectorAll("#content .turn")).find((el) => (el.textContent || "").includes(text) && el.getClientRects().length > 0);
    if (!turn) return;
    const tx = turn.textContent || "";
    window.__firstPaint = { at: performance.now(), label: tx.includes("not confirmed") ? "not confirmed" : tx.includes("sending") ? "sending" : "other" };
  };
  new MutationObserver(seen).observe(document, { subtree: true, childList: true, characterData: true });
  const origSend = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    let m = null;
    try { m = JSON.parse(d); } catch (e) {}
    if (m && m.type === "sendMessage" && typeof m.text === "string" && m.text === text) {
      window.__sent.push({ qid: m.qid, resend: !!m.resend });
      if (m.resend && window.__resendAt === null) window.__resendAt = performance.now();   // from here a kernel can know of it
      // the first page load swallows the send: the frame never reaches the kernel, as an unread frame in a closing socket does
      if (!sessionStorage.getItem("lab-dropped:" + text)) { sessionStorage.setItem("lab-dropped:" + text, "1"); return; }
    }
    return origSend.call(this, d);
  };
}, cfg.text);
const bubble = () => page.evaluate((t) => {
  const turns = Array.from(document.querySelectorAll("#content .turn"));
  const carriers = turns.filter((el) => (el.textContent || "").includes(t) && el.getClientRects().length > 0);
  return { n: carriers.length, labels: carriers.map((el) => el.textContent.includes("not confirmed") ? "not confirmed" : el.textContent.includes("sending") ? "sending" : "other") };
}, cfg.text);
await page.goto(cfg.chat);
await page.waitForSelector(".turn.turn-user", { timeout: 30000 });
await page.waitForTimeout(800);
await page.fill("#composer-input", cfg.text);
await page.press("#composer-input", "Enter");
await page.waitForTimeout(1500);
const before = { bubble: await bubble(), sent: await page.evaluate(() => window.__sent), acks: await page.evaluate(() => window.__acks) };
if (cfg.withdraw) {
  // the ✎ on the unconfirmed bubble: the words go back into the box and the send is withdrawn
  await page.evaluate((t) => {
    const turn = Array.from(document.querySelectorAll("#content .turn")).find((el) => (el.textContent || "").includes(t));
    const btn = turn && turn.querySelector('[data-act="qedit"]');
    if (btn) btn.click();
  }, cfg.text);
  await page.waitForTimeout(800);
  before.composer = await page.evaluate(() => (document.getElementById("composer-input") || {}).value || "");
  before.afterWithdraw = await bubble();
}
await page.reload();
if (cfg.withdraw) {
  await page.waitForSelector(".turn.turn-user", { timeout: 30000 });
  await page.waitForTimeout(4000);                 // several one-second keepalives: any resend would have gone by now
  const out = cfg.out;
  fs.writeFileSync(out, JSON.stringify({ before, after: { sent: await page.evaluate(() => window.__sent), bubble: await bubble() } }));
  console.log("RESULT-FILE:" + out);
  await browser.close();
  process.exit(0);
}
await page.waitForSelector(".turn.turn-user", { timeout: 30000 });
let restored = null;
for (let i = 0; i < 40 && !restored; i++) {   // loop-ok: bounded poll for the restored bubble's first paint
  const b = await bubble();
  if (b.n > 0) restored = b; else await page.waitForTimeout(250);
}
let acked = false;
try { await page.waitForFunction(() => window.__acks.some((a) => a.ok), null, { timeout: 30000 }); acked = true; } catch (e) {}
await page.waitForTimeout(500);
const after = { restored, acked, sent: await page.evaluate(() => window.__sent), acks: await page.evaluate(() => window.__acks), bubble: await bubble(),
                firstPaint: await page.evaluate(() => window.__firstPaint), ackAt: await page.evaluate(() => window.__ackAt),
                resendAt: await page.evaluate(() => window.__resendAt) };
const out = cfg.out;
fs.writeFileSync(out, JSON.stringify({ before, after }));
console.log("RESULT-FILE:" + out);
await browser.close();
"""


class ServedResendAfterReload(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="send-resend-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a test that mints its own state root pins the hosts off (CLAUDE.md)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in _seed_records(int(time.time()) - 900)))
        cls.port = _free_port()
        cls.token = "testtok-sendresend"
        # a one-second keepalive: the page learns that the kernel recognises a resend within a beat of the reload
        seams = {"ROMP_WS_KEEPALIVE": "1"}
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, **seams)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self, text, withdraw=False):
        cfg = os.path.join(self.lab, "cfg-%d.json" % int(withdraw))
        console_log = os.path.join(self.lab, "console.log")
        Path(console_log).write_text("")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "text": text, "withdraw": withdraw,
                       "consoleLog": console_log, "out": os.path.join(self.lab, "result-%d.json" % int(withdraw))}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        Path(driver).write_text(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=DRIVER_TIMEOUT_S,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired:
            self.fail("the driver ran past its %d s budget\nkernel:\n%s" % (DRIVER_TIMEOUT_S, open(self.klog).read()[-2500:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-2500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT-FILE:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        self.assertEqual(Path(console_log).read_text().strip(), "", "the page threw")
        return json.loads(Path(line[len("RESULT-FILE:"):]).read_text())

    def test_a_send_withdrawn_with_the_pencil_stays_withdrawn_across_the_reload(self):
        # the review of #198 (2026-10-09): the withdrawal did not rewrite the kept list, so the reload restored the send and
        # resent it while the box still held its words: sent once by the resend, and again when the user pressed Enter
        text = "please drop the unused import from the notes-api router"
        r = self._drive(text, withdraw=True)
        before, after = r["before"], r["after"]
        print("SEND-WITHDRAW:", json.dumps(r))
        self.assertEqual(before["acks"], [], "the swallowed send was never answered")
        self.assertEqual(before["afterWithdraw"]["n"], 0, "the ✎ took the bubble away")
        self.assertIn(text, before["composer"], "and put the words back in the box")
        self.assertEqual([s for s in after["sent"] if s["resend"]], [], "the reload resent nothing")
        self.assertEqual(after["bubble"]["n"], 0, "and drew no bubble for the withdrawn send")

    def test_an_unread_send_survives_the_reload_and_is_delivered_by_the_resend(self):
        cfg = os.path.join(self.lab, "cfg.json")
        console_log = os.path.join(self.lab, "console.log")
        Path(console_log).write_text("")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "text": TEXT,
                       "consoleLog": console_log, "out": os.path.join(self.lab, "result.json")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        Path(driver).write_text(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=DRIVER_TIMEOUT_S,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired:
            self.fail("the driver ran past its %d s budget\nkernel:\n%s" % (DRIVER_TIMEOUT_S, open(self.klog).read()[-2500:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-2500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT-FILE:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(Path(line[len("RESULT-FILE:"):]).read_text())
        before, after = r["before"], r["after"]
        print("SEND-RESEND:", json.dumps(r))
        self.assertEqual(Path(console_log).read_text().strip(), "", "the page threw")
        self.assertGreaterEqual(before["bubble"]["n"], 1, "the press painted its bubble")
        self.assertEqual(before["acks"], [], "the kernel never had the dropped frame, so nothing answered it")
        self.assertEqual([s["resend"] for s in before["sent"]], [False], "one post, swallowed")
        qid = before["sent"][0]["qid"]
        self.assertIsNotNone(after["restored"], "after the reload the send is back on the page: the reload no longer drops the words")
        fp, resend_at = after["firstPaint"], after["resendAt"]
        self.assertIsNotNone(fp, "the page painted the restored bubble")
        if resend_at is None or fp["at"] < resend_at:
            # painted before the resend left the page, so before any kernel could know of the send: it must say so. Painted
            # later, the kernel's answer (or its queued copy) may already have taken the label away, which is the design
            self.assertEqual(fp["label"], "not confirmed", "…captioned not confirmed until a kernel answers it")
        self.assertIn({"qid": qid, "resend": True}, after["sent"], "the page posted the very send again, marked as a resend")
        self.assertTrue(after["acked"], "the kernel answered the resend")
        self.assertIn(qid, [a["qid"] for a in after["acks"] if a["ok"]])
        self.assertGreaterEqual(after["bubble"]["n"], 1, "the text is still on the page")
        self.assertNotIn("not confirmed", after["bubble"]["labels"], "and the answer took the label away")


if __name__ == "__main__":
    unittest.main()
