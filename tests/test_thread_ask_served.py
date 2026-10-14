#!/usr/bin/env python3
"""A comment thread stopped on a question or a permission prompt is answered inside its popover, on the real /chat page
(thread-live-1, 2026-10-14). Before, the popover said the thread hit a prompt it could not answer and to break it out:
the kernel's question poll read only the live sessions that drive tabs, and a thread is deliberately not one of them.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web: two prompts, two replies) whose parent
runs on the fake Agent SDK, so each comment is the kernel's real create door (the fork, the opening message) and the
thread's turn is a real SdkSession turn. The fake SDK's scripted consults (ROMP_FAKE_SDK_ASKS) make the first thread's
turn ask an AskUserQuestion and the second's ask permission for a Bash command, through the backend's own can_use_tool,
and each reply names what came back. The driver comments the user's way (select, Ctrl+/, type, Enter) and asserts:
  the question: the chat's own picker card shows in the popover (its question and its two options), the popover's chip
  says Needs you with no stop, the passage's highlight and its rail tick wear the needs-you cue, and no "break it out"
  dead end is left; the strip still holds the one tab, web;
  a reload: the question comes back with the thread (the kernel's poll replays it from the thread live map, not only the
  ask's first push);
  the answer: a click on the second option posts answerAsk with the THREAD's id (the chat's own frame), the thread's reply
  names that option, the card goes and the passage's cue with it;
  the permission prompt: Allow and Deny in the popover, Deny reaches the thread (its reply says it was denied);
  GET /sessions lists web alone, ?threads=1 lists the threads too.
With THREAD_ASK_SHOTS=<dir> the driver writes the popover with the question up. Skips LOUDLY without the extension deps
or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host TESTHOST)."""
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

WEB = "aaaaaaaa-1111-2222-3333-7417ea5ced00"
A1 = ("Cache the notes-api list responses for thirty seconds, keyed by the query string. The export stream stays "
      "uncached, and a write clears the cache for its folder.")
A2 = ("Keep the retry cap at two minutes on every sync path. Longer caps only delay the error the user will see anyway, "
      "and the build folder can be cleaned before the next release.")
P_ASK = "Keep the retry cap at two minutes"
P_PERM = "the build folder can be cleaned"
P_OWN = "keyed by the query string"
P_KEY = "a write clears the cache for its folder"
C_ASK = "Which cap should the sync use?"
C_PERM = "Clean the build folder please"
C_OWN = "How long should the cache hold?"
OWN = "Ninety seconds"
C_KEY0 = "What does the folder's cache lock guard?"   # the keyboard thread's first exchange, answered plainly
C_KEY = "How long should the export lock hold?"
QUESTION = "Which retry cap should the notes-api sync use?"
ASKS = [{"when": C_ASK, "tool": "AskUserQuestion",
         "input": {"questions": [{"question": QUESTION, "header": "Retry cap", "multiSelect": False,
                                  "options": [{"label": "Two minutes", "description": "the cap the sync has today"},
                                              {"label": "Five minutes", "description": "rides out a longer outage"}]}]}},
        {"when": C_PERM, "tool": "Bash", "input": {"command": "rm -rf build/", "description": "Clean the build folder"}},
        {"when": C_OWN, "tool": "AskUserQuestion",
         "input": {"questions": [{"question": "How long should the list cache hold?", "header": "Cache", "multiSelect": False,
                                  "options": [{"label": "Thirty seconds", "description": "today's window"},
                                              {"label": "A minute", "description": "fewer list calls"}]}]}},
        {"when": C_KEY, "tool": "AskUserQuestion",
         "input": {"questions": [{"question": "How long should the export lock hold?", "header": "Lock", "multiSelect": False,
                                  "options": [{"label": "Ten seconds", "description": "the export's usual length"},
                                              {"label": "A minute", "description": "covers the slowest export"}]}]}}]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text, sid):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "sessionId": sid, "message": {"role": "user", "content": text}}


def agent(t, uuid, parent, text, sid):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": sid,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


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
const ctx = await browser.newContext({ viewport: { width: 1200, height: 860 } });
await ctx.addInitScript(() => {
  // every question frame the page posts, recorded (and sent on: the kernel answers the thread for real)
  window.__asks = [];
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && /Ask$/.test(m.type || "")) window.__asks.push({ type: m.type, id: m.id, target: m.target }); } catch (e) {}
    return send.call(this, d);
  };
});
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));

const open = async () => {
  await page.goto(cfg.chat);
  await page.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
  await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
  await page.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.pAsk, { timeout: 60000 });
};
const select = (text) => page.evaluate((text) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).reverse().find((n) => (n.textContent || "").includes(text));
  if (!turn) return false;
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  if (!node) return false;
  const at = node.textContent.indexOf(text);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length); sel.addRange(r);
  return true;
}, text);
const comment = async (passage, words) => {
  await select(passage);
  await page.keyboard.press("Control+/");
  await page.waitForSelector("#cmt-pop .cmt-input", { timeout: 8000 });
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type(words);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("cmt-pop")?.dataset.mode === "thread", null, { timeout: 60000 });
};
const card = () => page.waitForSelector("#cmt-pop .cmt-ask .ask-card", { timeout: 60000 }).then(() => true, () => false);
// the popover, the thread's mark and its rail tick, as the reader sees them
const read = (passage) => page.evaluate((passage) => {
  const p = document.getElementById("cmt-pop");
  const host = p && p.querySelector(".cmt-ask");
  const c = host && host.querySelector(".ask-card");
  const marks = Array.from(document.querySelectorAll("#content mark.cmt-hl")).filter((m) => passage.includes((m.textContent || "").trim()) && (m.textContent || "").trim().length > 3);
  const tid = p ? p.dataset.tid : (marks[0] && marks[0].dataset.tid);
  const tick = tid ? document.querySelector(`.cmt-tick[data-tid="${tid}"]`) : null;
  const box = tid ? document.querySelector(`.cmt-outline[data-tid="${tid}"]`) : null;
  return {
    open: !!p, tid,
    card: c ? { q: (c.querySelector(".ask-qtext") || {}).textContent || "", options: Array.from(c.querySelectorAll(".ask-live-opt .ask-optlabel")).map((o) => o.textContent),
                inPopover: !!c.closest("#cmt-pop"), outsideList: !c.closest(".cmt-msgs"), shown: host.style.display !== "none" && c.getBoundingClientRect().height > 0 } : null,
    chip: p ? ((p.querySelector("#cmt-state .chip") || {}).textContent || "") : "",
    stop: !!(p && p.querySelector("#cmt-state .cmt-stop")),
    msgs: p ? ((p.querySelector(".cmt-msgs") || {}).textContent || "") : "",
    deadEnd: /Break it out to continue/.test(document.body.textContent || ""),
    needs: marks.length > 0 && marks.every((m) => m.classList.contains("needs")),
    cue: marks.some((m) => m.classList.contains("cmt-ring")) || !!box,
    tick: tick ? { needs: tick.classList.contains("needs"), halo: getComputedStyle(tick).boxShadow } : null,
    tabs: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id),   // the session tabs (the strip's + carries none)
    liveAsk: !!document.querySelector("#live-ask .ask-card"),
  };
}, passage);
// the thread's comments frame carrying its chip (the frame rides the pusher's cycle, the card the ask's own push): the chip
// says Needs you and the passage's marks wear the cue, then the rail's rAF pass; an event to wait on, bounded, never a clock
const waitCued = async (passage) => {
  await page.waitForFunction((passage) => /Needs you/.test((document.querySelector("#cmt-pop #cmt-state .chip") || {}).textContent || "")
    && Array.from(document.querySelectorAll("#content mark.cmt-hl")).some((m) => passage.includes((m.textContent || "").trim()) && m.classList.contains("needs")),
    passage, { timeout: 30000 }).catch(() => {});
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
};
const closePop = async () => { await page.evaluate(() => document.querySelector("#cmt-pop .cmt-x")?.click()); await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {}); };

try { await open(); } catch (e) { await done("the chat never showed the replies: " + String(e).split("\n")[0]); }

// 1. a comment whose thread asks a question
await comment(cfg.pAsk, cfg.cAsk);
out.askShown = await card();
await waitCued(cfg.pAsk);
out.ask = await read(cfg.pAsk);
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/thread-ask-question.png" }); }

// 2. a reload: the thread's question comes back from the kernel's poll; the thread opens from its highlight
await page.reload();
try { await open(); } catch (e) { await done("the chat never came back after the reload: " + String(e).split("\n")[0]); }
await page.waitForFunction((passage) => Array.from(document.querySelectorAll("#content mark.cmt-hl")).some((m) => passage.includes((m.textContent || "").trim())), cfg.pAsk, { timeout: 30000 }).catch(() => {});
if (!(await page.evaluate(() => !!document.getElementById("cmt-pop")))) {
  await page.evaluate((passage) => Array.from(document.querySelectorAll("#content mark.cmt-hl")).find((m) => passage.includes((m.textContent || "").trim()))?.click(), cfg.pAsk);
}
out.reloadShown = await card();
out.reload = await read(cfg.pAsk);

// 3. the answer: the second option, clicked in the popover
const asksBefore = await page.evaluate(() => window.__asks.length);
await page.click("#cmt-pop .cmt-ask .ask-live-opt >> nth=1");
out.answered = await page.waitForFunction(() => /You chose Five minutes/.test((document.querySelector("#cmt-pop .cmt-msgs") || {}).textContent || ""), null, { timeout: 60000 }).then(() => true, () => false);
await page.waitForFunction(() => !document.querySelector("#cmt-pop .cmt-ask .ask-card"), null, { timeout: 30000 }).catch(() => {});
await page.waitForFunction((passage) => !Array.from(document.querySelectorAll("#content mark.cmt-hl")).some((m) => passage.includes((m.textContent || "").trim()) && m.classList.contains("needs")), cfg.pAsk, { timeout: 30000 }).catch(() => {});
await page.waitForTimeout(400);
out.after = await read(cfg.pAsk);
out.answerFrames = await page.evaluate((n) => window.__asks.slice(n), asksBefore);
await closePop();

// 4. a comment whose thread asks permission for a command: Deny, from the popover
await comment(cfg.pPerm, cfg.cPerm);
out.permShown = await card();
await waitCued(cfg.pPerm);
out.perm = await read(cfg.pPerm);
const permBefore = await page.evaluate(() => window.__asks.length);
await page.evaluate(() => Array.from(document.querySelectorAll("#cmt-pop .cmt-ask .ask-live-opt")).find((o) => /Deny/.test(o.textContent || ""))?.click());
out.denied = await page.waitForFunction(() => /Denied, so I left Bash alone/.test((document.querySelector("#cmt-pop .cmt-msgs") || {}).textContent || ""), null, { timeout: 60000 }).then(() => true, () => false);
out.permFrames = await page.evaluate((n) => window.__asks.slice(n), permBefore);
out.permAfter = await read(cfg.pPerm);
await closePop();

// 5. an answer of your own, typed into the popover's box (the question's free-text slot, as the message box serves it)
await comment(cfg.pOwn, cfg.cOwn);
out.ownShown = await card();
out.own = await read(cfg.pOwn);
out.ownBox = await page.evaluate(() => { const b = document.querySelector("#cmt-pop .cmt-input"); return { answering: b.classList.contains("answering"), ph: b.placeholder }; });
const ownBefore = await page.evaluate(() => window.__asks.length);
await page.focus("#cmt-pop .cmt-input");
await page.keyboard.type(cfg.own);
await page.keyboard.press("Enter");
out.ownAnswered = await page.waitForFunction((own) => new RegExp("You chose " + own).test((document.querySelector("#cmt-pop .cmt-msgs") || {}).textContent || ""), cfg.own, { timeout: 60000 }).then(() => true, () => false);
out.ownFrames = await page.evaluate((n) => window.__asks.slice(n), ownBefore);
out.ownAfter = await page.evaluate(() => { const b = document.querySelector("#cmt-pop .cmt-input"); return { value: b.value, answering: b.classList.contains("answering") }; });
await closePop();

// 6. the keyboard: a thread with an exchange behind it, opened from its highlight, puts the keys on its card (the box takes
// the caret only for a thread with nothing said yet), and a number answers the card, typing nothing
await comment(cfg.pKey, cfg.cKey0);
await page.waitForFunction(() => (document.querySelector("#cmt-pop .cmt-msgs") || {}).textContent?.includes("Rebuilt the search index"), null, { timeout: 60000 }).catch(() => {});
await page.focus("#cmt-pop .cmt-input");
await page.keyboard.type(cfg.cKey);
await page.keyboard.press("Enter");
out.keyShown = await card();
await closePop();
await page.evaluate((passage) => Array.from(document.querySelectorAll("#content mark.cmt-hl")).find((m) => passage.includes((m.textContent || "").trim()))?.click(), cfg.pKey);
await card();
out.keyFocus = await page.evaluate(() => ({ onCard: !!(document.activeElement && document.activeElement.closest && document.activeElement.closest("#cmt-pop .cmt-ask .ask-card")),
                                            tid: (document.getElementById("cmt-pop") || {}).dataset?.tid || "" }));
const keyBefore = await page.evaluate(() => window.__asks.length);
await page.keyboard.press("2");
out.keyAnswered = await page.waitForFunction(() => /You chose A minute/.test((document.querySelector("#cmt-pop .cmt-msgs") || {}).textContent || ""), null, { timeout: 60000 }).then(() => true, () => false);
out.keyFrames = await page.evaluate((n) => window.__asks.slice(n), keyBefore);
out.keyBox = await page.evaluate(() => (document.querySelector("#cmt-pop .cmt-input") || {}).value);
out.errs = errs;
await done();
"""


class ServedThreadAsk(unittest.TestCase):
    maxDiff = None

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
        cls.lab = tempfile.mkdtemp(prefix="thread-ask-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "default", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        parent = [user(t0, "u1", None, "how long should the notes-api cache its list responses?", WEB),
                  agent(t0 + 5, "a1", "u1", A1, WEB),
                  user(t0 + 60, "u2", "a1", "and retries?", WEB),
                  agent(t0 + 65, "a2", "u2", A2, WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        cls.store = os.path.join(state, "comments", WEB + ".json")
        cls.port, cls.token = _free_port(), "testtok-threadask"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              PYTHONPATH=FAKE_SDK,   # the parent runs on the fake Agent SDK: every create forks for real
                              ROMP_FAKE_SDK_ASKS=json.dumps(ASKS))   # …and each thread's turn consults the backend's can_use_tool
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
            k.kill()
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _sessions(self, threads=False):
        url = "http://127.0.0.1:%d/sessions?token=%s%s" % (self.port, self.token, "&threads=1" if threads else "")
        return json.loads(urllib.request.urlopen(url, timeout=10).read().decode("utf-8"))

    def test_a_threads_question_and_permission_prompt_are_answered_in_its_popover(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB,
                       "pAsk": P_ASK, "pPerm": P_PERM, "cAsk": C_ASK, "cPerm": C_PERM, "pOwn": P_OWN, "cOwn": C_OWN, "own": OWN, "pKey": P_KEY, "cKey0": C_KEY0, "cKey": C_KEY,
                       "shots": os.environ.get("THREAD_ASK_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            def _dec(b):
                return "" if b is None else (b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else b)
            self.fail("driver ran past its 420 s cap:\n%s%s\nkernel:\n%s" % (_dec(e.stdout)[-3000:], _dec(e.stderr)[-3000:], open(self.klog).read()[-2000:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-2000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        klog = open(self.klog).read()[-2500:]
        self.assertNotIn("died", r, "%s\nkernel:\n%s" % (r.get("died"), klog))
        self.assertEqual(r["errs"], [], "no page errors")

        # 1. the question, in the popover: the chat's card, its options, the chip, the cue; no dead end; no new tab
        a = r["ask"]
        self.assertTrue(r["askShown"], "the thread's question card showed in its popover: %r\nkernel:\n%s" % (a, klog))
        tid = a["tid"]
        self.assertTrue(tid and not tid.startswith("pending:"), a)
        self.assertEqual(a["card"]["q"], QUESTION, a)
        self.assertEqual(a["card"]["options"], ["1. Two minutes", "2. Five minutes"], a)
        self.assertTrue(a["card"]["inPopover"] and a["card"]["outsideList"] and a["card"]["shown"],
                        "the chat's card, in the popover's own host beside the conversation: %r" % a)
        self.assertFalse(a["liveAsk"], "the chat's own picker host stays empty: the question is the thread's")
        self.assertEqual(a["chip"], "Needs you", a)
        self.assertFalse(a["stop"], "no stop while the thread waits on you (the chat bar's rule): %r" % a)
        self.assertFalse(a["deadEnd"], "no 'break it out' dead end")
        self.assertTrue(a["needs"], "the passage's highlight says the thread waits on you: %r" % a)
        self.assertTrue(a["cue"], "…with the needs-you ring or box: %r" % a)
        self.assertTrue(a["tick"] and a["tick"]["needs"], "the rail tick too: %r" % a)
        self.assertRegex(a["tick"]["halo"], r"rgb\(192, 57, 43\) 0px 0px 0px 3px", "in the needs-you red: %r" % a["tick"])
        self.assertEqual(a["tabs"], [WEB], "the thread gets no tab of its own: %r" % a["tabs"])

        # 2. after a reload the question is back with the thread
        self.assertTrue(r["reloadShown"], "the kernel's poll replays the thread's question to a page that connects later: %r" % r["reload"])
        self.assertEqual(r["reload"]["card"]["q"], QUESTION)

        # 3. the answer reached the thread through the chat's own frame, by the thread's id
        self.assertTrue(r["answered"], "the thread's reply names the option picked: %r\nkernel:\n%s" % (r["after"], klog))
        self.assertEqual(r["answerFrames"], [{"type": "answerAsk", "id": tid, "target": 2}], r["answerFrames"])
        af = r["after"]
        self.assertIsNone(af["card"], "the card left with the question: %r" % af)
        self.assertFalse(af["needs"], "…and the passage's needs-you cue with it: %r" % af)
        self.assertFalse(af["tick"]["needs"], af)

        # 4. a permission prompt: Allow and Deny in the popover; Deny reached the thread
        pm = r["perm"]
        self.assertTrue(r["permShown"], "the permission prompt showed in the popover: %r" % pm)
        self.assertIn("Allow", " ".join(pm["card"]["options"]))
        self.assertIn("Deny", " ".join(pm["card"]["options"]))
        self.assertEqual(pm["chip"], "Needs you")
        self.assertTrue(pm["needs"], pm)
        deny = [f for f in r["permFrames"] if f["type"] == "answerAsk"]
        self.assertEqual(len(deny), 1, r["permFrames"])
        self.assertEqual(deny[0]["id"], pm["tid"])
        self.assertTrue(r["denied"], "the thread's reply says it was denied: %r\nkernel:\n%s" % (r["permAfter"], klog))

        # 5. an answer of your own: the popover's box is the question's "add your own" field, as the message box is the chat's
        self.assertTrue(r["ownShown"], "the third thread's question showed")
        self.assertEqual(r["ownBox"], {"answering": True, "ph": "add your own answer…  (⏎ submit)"}, r["ownBox"])
        own = [f for f in r["ownFrames"] if f["type"] == "addCustomAsk"]
        self.assertEqual(len(own), 1, "the typed words went to the question's free-text slot, nothing else: %r" % r["ownFrames"])
        self.assertEqual(own[0]["id"], r["own"]["tid"], "…of the thread's own question")
        self.assertTrue(r["ownAnswered"], "the thread's reply names the typed answer\nkernel:\n%s" % klog)
        self.assertEqual(r["ownAfter"], {"value": "", "answering": False}, "the box empties and is a reply box again: %r" % r["ownAfter"])

        # 6. the keyboard: the reopened thread's card holds the keys, "2" answers it, and the reply box stays empty
        self.assertTrue(r["keyShown"])
        self.assertTrue(r["keyFocus"]["onCard"], "a thread opened from its highlight puts the keys on its question card: %r" % r["keyFocus"])
        keyed = [f for f in r["keyFrames"] if f["type"] == "answerAsk"]
        self.assertEqual([(f["id"], f["target"]) for f in keyed], [(r["keyFocus"]["tid"], 2)], r["keyFrames"])
        self.assertTrue(r["keyAnswered"], "the thread's reply names the option the key picked\nkernel:\n%s" % klog)
        self.assertEqual(r["keyBox"], "", "the number answered the card and typed nothing into the reply box")

        # the listing: the threads are no session of the board's; only ?threads=1 names them
        ids = [row.get("id") for row in self._sessions()]
        self.assertEqual(ids, [WEB], ids)
        tids = {row.get("id") for row in self._sessions(threads=True) if row.get("thread")}
        self.assertIn(tid, tids)
        self.assertIn(pm["tid"], tids)


if __name__ == "__main__":
    unittest.main()
