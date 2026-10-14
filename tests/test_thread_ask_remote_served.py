#!/usr/bin/env python3
"""A comment thread on a LINKED machine stops on a question, and the question is answered from the hub's page, reaching the
right kernel and the right thread (the review of #232, 2026-10-14).

The browser prefixes every session id from a linked kernel with the machine's name on the way in, and strips it on the way
out to that machine. Before this fix the comments frame left a thread's own session id bare while the question frame that
names the thread arrived prefixed, so the popover found no question for a linked machine's thread: the passage said it
waited on you and there was no card to answer; and the thread's stop and its other ops went to the hub's own kernel.

Two hermetic kernels: TESTHOST owns the session (the notes-api demo world, session web) and runs it on the fake Agent SDK,
whose scripted consult (ROMP_FAKE_SDK_ASKS) makes the thread's turn ask an AskUserQuestion through the backend's own
can_use_tool; the HUB has no session of its own and shows TESTHOST's through the relay, after the check-in handshake a
mobile machine makes (no ssh). The driver opens the hub's page, opens the linked tab, comments the user's way, and asserts:
the question's card shows in the popover, the popover's thread names its session with the machine's prefix, a click on the
second option leaves on TESTHOST's relay socket as answerAsk with the bare thread id, and the thread's reply names that
option (TESTHOST's kernel answered the right thread); the hub's own socket carried no question frame. Skips LOUDLY without
the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host TESTHOST)."""
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
FAKE_SDK = os.path.join(HERE, "fixtures", "fake_agent_sdk")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-7e57a5ced0aa"
REPLY = ("Keep the retry cap at two minutes on every sync path. Longer caps only delay the error the user will see "
         "anyway, and the header is written once per export.")
PASSAGE = "Keep the retry cap at two minutes"
COMMENT = "Which cap should the sync use?"
QUESTION = "Which retry cap should the notes-api sync use?"
ASKS = [{"when": COMMENT, "tool": "AskUserQuestion",
         "input": {"questions": [{"question": QUESTION, "header": "Retry cap", "multiSelect": False,
                                  "options": [{"label": "Two minutes", "description": "the cap the sync has today"},
                                              {"label": "Five minutes", "description": "rides out a longer outage"}]}]}}]


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
const out = {};
const done = async (why) => { if (why) out.died = why; fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n"); await browser.close(); process.exit(0); };
const page = await browser.newPage({ viewport: { width: 1200, height: 860 } });
const errs = [];
page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
// every question frame the page sends, with the socket it left on (and sent on: the linked kernel answers the thread)
await page.addInitScript(() => {
  window.__asks = [];
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && /Ask$/.test(m.type || "")) window.__asks.push({ url: this.url, type: m.type, id: m.id, target: m.target }); } catch (e) {}
    return send.call(this, d);
  };
});
await page.goto(cfg.chat);
const tab = page.locator("#tabs .tab", { hasText: "TESTHOST" }).first();
try { await tab.waitFor({ timeout: 45000 }); } catch (e) { await done("the linked machine's tab never showed"); }
await tab.click();
try {
  await page.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.passage, { timeout: 30000 });
} catch (e) { await done("the linked session's reply never rendered"); }
await page.waitForTimeout(300);
// comment the user's way: select the passage, Ctrl+/, type, Enter
await page.evaluate((text) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).reverse().find((n) => (n.textContent || "").includes(text));
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  const at = node.textContent.indexOf(text);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length); sel.addRange(r);
}, cfg.passage);
await page.keyboard.press("Control+/");
try { await page.waitForSelector("#cmt-pop .cmt-input", { timeout: 8000 }); } catch (e) { await done("no comment box"); }
await page.focus("#cmt-pop .cmt-input");
await page.keyboard.type(cfg.comment);
await page.keyboard.press("Enter");
try { await page.waitForFunction(() => document.getElementById("cmt-pop")?.dataset.mode === "thread", null, { timeout: 60000 }); }
catch (e) { await done("the linked machine's thread never opened in the box"); }
out.cardShown = await page.waitForSelector("#cmt-pop .cmt-ask .ask-card", { timeout: 60000 }).then(() => true, () => false);
out.card = await page.evaluate(() => {
  const c = document.querySelector("#cmt-pop .cmt-ask .ask-card");
  return c ? { q: (c.querySelector(".ask-qtext") || {}).textContent || "", options: Array.from(c.querySelectorAll(".ask-live-opt .ask-optlabel")).map((o) => o.textContent) } : null;
});
out.tid = await page.evaluate(() => (document.getElementById("cmt-pop") || {}).dataset?.tid || "");
const before = await page.evaluate(() => window.__asks.length);
if (out.cardShown) await page.click("#cmt-pop .cmt-ask .ask-live-opt >> nth=1");
out.answered = await page.waitForFunction(() => /You chose Five minutes/.test((document.querySelector("#cmt-pop .cmt-msgs") || {}).textContent || ""), null, { timeout: 60000 }).then(() => true, () => false);
out.frames = await page.evaluate((n) => window.__asks.slice(n), before);
out.cardAfter = await page.evaluate(() => !!document.querySelector("#cmt-pop .cmt-ask .ask-card"));
out.toasts = await page.evaluate(() => Array.from(document.querySelectorAll(".warn-toast-msg, .warn-toast")).map((t) => t.textContent));
out.errs = errs;
await done();
"""


def _kernel(lab, name, port, token, records=None, sid=None, **seams):
    """Boot one hermetic kernel: its own state root, dist, and (optionally) one session with a transcript."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    cwd = os.path.join(lab, name, "proj")
    for d in ("names", "sdk", "states", "comments"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
    os.makedirs(cwd, exist_ok=True)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    if records is not None:
        Path(state, "names", sid).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", sid + ".json").write_text(json.dumps(
            {"sid": sid, "name": "web", "cwd": cwd, "mode": "default", "effort": "high",
             "lastSid": sid, "alive": True, "model": "claude-opus-5", "liveModel": "Opus 5"}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    env = _lab.kernel_env(os.path.join(lab, name), claude, os.path.join(lab, "dist"), port, token,
                          ROMP_HOST_NAME=name.upper(), **seams)
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log
        except Exception:
            time.sleep(0.5)
    proc.kill(); proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


class ServedThreadAskRemote(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:          # a skip OR a failure: never leave a kernel or a lab behind
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
        cls.lab = tempfile.mkdtemp(prefix="thread-ask-remote-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.procs = []
        t0 = int(time.time()) - 900
        recs = [{"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": WEB,
                 "message": {"role": "user", "content": "what cap should the notes-api retry loop use?"}},
                {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": WEB,
                 "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": REPLY}]}}]
        # TESTHOST owns the session and runs it on the fake Agent SDK; the HUB shows it through the relay
        cls.rport, cls.rtoken = _free_port(), "testtok-thread-remote"
        cls.hport, cls.htoken = _free_port(), "testtok-thread-hub"
        rp, cls.rlog = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, records=recs, sid=WEB,
                               PYTHONPATH=FAKE_SDK, ROMP_FAKE_SDK_ASKS=json.dumps(ASKS))
        cls.procs.append(rp)
        hp, cls.hlog = _kernel(cls.lab, "hub", cls.hport, cls.htoken)
        cls.procs.append(hp)
        # the check-in handshake a mobile machine makes through its reverse forward: the hub records the peer like an
        # attached remote (no ssh of its own) and probes it on the port given, here the linked kernel's own
        body = json.dumps({"host": "TESTHOST", "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ans = json.loads(resp.read().decode())
        if not ans.get("ok"):
            raise unittest.SkipTest("the hub refused the check-in: %r" % ans)
        for _ in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (cls.hport, cls.htoken), timeout=3) as r2:
                    rows = json.loads(r2.read().decode()).get("tunnels") or []
            except Exception:
                rows = []
            row = next((t for t in rows if t.get("host") == "TESTHOST"), None)
            if row and row.get("status") == "up" and row.get("hasToken"):
                break
            time.sleep(0.5)
        else:
            raise unittest.SkipTest("the hub never reported the checked-in peer up: %r" % (rows,))

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", []):
            try:
                p.kill(); p.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_a_linked_machines_thread_question_is_answered_from_the_hub_and_reaches_its_thread(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.hport, self.htoken), "passage": PASSAGE, "comment": COMMENT}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            def _dec(b):
                return "" if b is None else (b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else b)
            self.fail("driver ran past its 420 s cap:\n%s%s" % (_dec(e.stdout)[-3000:], _dec(e.stderr)[-3000:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        logs = "\nhub:\n" + open(self.hlog).read()[-1500:] + "\nTESTHOST:\n" + open(self.rlog).read()[-1500:]
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + logs)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertNotIn("died", r, "%s%s" % (r.get("died"), logs))
        self.assertEqual(r["errs"], [], "no page errors")
        self.assertTrue(r["cardShown"], "the linked machine's thread question shows in the hub's popover: %r%s" % (r, logs))
        self.assertEqual(r["card"]["q"], QUESTION)
        self.assertEqual(r["card"]["options"], ["1. Two minutes", "2. Five minutes"])
        tid = r["tid"]
        self.assertTrue(tid and ":" not in tid, "the popover's thread is the bare tid the owning kernel knows: %r" % tid)
        answers = [f for f in r["frames"] if f["type"] == "answerAsk"]
        self.assertEqual(len(answers), 1, r["frames"])
        self.assertIn("/remote/TESTHOST/ws", answers[0]["url"], "the answer left on the linked machine's relay socket: %r" % answers[0])
        self.assertEqual((answers[0]["id"], answers[0]["target"]), (tid, 2), "…naming the thread as its kernel knows it")
        self.assertTrue(r["answered"], "the linked machine's thread answered with the option picked%s" % logs)
        self.assertFalse(r["cardAfter"], "the card left with the question")
        self.assertFalse([t for t in r["toasts"] if "not delivered" in (t or "")], "nothing was refused: %r" % r["toasts"])


if __name__ == "__main__":
    unittest.main()
