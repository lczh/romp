#!/usr/bin/env python3
"""A question arriving in one chat column leaves the keyboard where the user is (the dashboard's columns, 2026-10-18).

A chat page focuses its question card when the card renders, so the arrow keys and Enter answer it at once, but never
while the user is typing (focusCardUnlessTyping). That guard read only its OWN document's focus, and the dashboard
holds several same-origin pane frames side by side: a column whose own focus sat on its body called card.focus() and
pulled the keyboard out of ANOTHER column's message box mid-sentence, and the rest of the sentence landed in the
question's column.

A hermetic kernel serves the dashboard with two sessions on the fake Agent SDK, web in column 1 and api moved into
column 2. Each question is the kernel's real push: a POST /send hands the session a line its scripted turn answers by
asking an AskUserQuestion through the backend's can_use_tool (ROMP_FAKE_SDK_ASKS). One driver run, in order:
  1. the shell holds the focus (a click on its bottom bar), nothing is typed: web's question takes the keyboard (column
     1's card), as before;
  2. typing in column 2's message box: web's next question shows in column 1 and the focus stays in column 2's box,
     and the next keystrokes land there, the box holding both halves of the sentence;
  3. stepping through api's question in column 2 with the keys: web's next question shows in column 1, and ArrowDown
     still moves column 2's highlight, not column 1's.
Skips LOUDLY without the extension deps or a Playwright browser (CI installs none). Synthetic only: placeholder sids,
invented notes-api text, host TESTHOST."""
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

WEB = "aaaaaaaa-1111-2222-3333-0000000c0f01"   # column 1
API = "aaaaaaaa-1111-2222-3333-0000000c0f02"   # column 2
FIRST = "the export stream stays uncached, and "
SECOND = "a write clears its folder"
T_SHELL = "pick the list cache window"
T_TYPING = "pick the retry cap"
T_API = "pick the export lock"
T_CARDS = "pick the page size"


def _ask(q, header, a, b):
    return {"questions": [{"question": q, "header": header, "multiSelect": False,
                           "options": [{"label": a, "description": "the current setting"},
                                       {"label": b, "description": "the longer option"}]}]}


ASKS = [{"when": T_SHELL, "tool": "AskUserQuestion", "input": _ask("How long should the list cache hold?", "Cache", "Thirty seconds", "A minute")},
        {"when": T_TYPING, "tool": "AskUserQuestion", "input": _ask("Which retry cap should the sync use?", "Retry cap", "Two minutes", "Five minutes")},
        {"when": T_API, "tool": "AskUserQuestion", "input": _ask("How long should the export lock hold?", "Lock", "Ten seconds", "A minute")},
        {"when": T_CARDS, "tool": "AskUserQuestion", "input": _ask("How many notes should a page hold?", "Page size", "Fifty", "A hundred")}]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turns(sid, t0, prompt, reply):
    """One CLOSED turn (an open one would invite the boot reconcile to resume it; no real CLI here)."""
    u, a = sid[:-4] + "a001", sid[:-4] + "b001"
    return "".join(json.dumps(r) + "\n" for r in (
        {"type": "user", "timestamp": _iso(t0), "uuid": u, "parentUuid": None, "promptSource": "typed", "sessionId": sid,
         "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "timestamp": _iso(t0 + 5), "uuid": a, "parentUuid": u, "sessionId": sid,
         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": reply}]}}))


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { errs: [] };
const page = await browser.newPage({ viewport: { width: 1500, height: 800 } });
page.on("pageerror", (e) => out.errs.push(String(e && e.stack || e).slice(0, 400)));
// a fresh split store per run, on the TOP frame only (a column's load must not wipe the split mid-flight)
await page.addInitScript(() => { try { if (window === window.top) { localStorage.removeItem("romp-chat-cols"); Object.keys(localStorage).filter((k) => k.indexOf("romp-vscode-state-chat") === 0).forEach((k) => localStorage.removeItem(k)); } } catch (e) {} });
// every question frame a column posts, recorded and sent on (the kernel answers the fake SDK's consult for real)
await page.addInitScript(() => { if (window === window.top) return; window.__asks = []; const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { try { const m = JSON.parse(d); if (m && /Ask$/.test(m.type || "")) window.__asks.push({ type: m.type, id: m.id, target: m.target }); } catch (e) {} return send.call(this, d); }; });

const frameOf = async (fid) => { const h = await page.$("#" + fid); return h ? await h.contentFrame() : null; };
// where the keyboard is, read from the shell: the shell's focused element, and each column's own focus, box, card and highlight
const where = () => page.evaluate(() => {
  const name = (el) => !el ? null : el.id ? "#" + el.id : el.tagName.toLowerCase() + (typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).join(".") : "");
  const col = (fid) => { const f = document.getElementById(fid); const d = f && f.contentDocument; if (!d) return null; const a = d.activeElement;
    const opts = Array.from(d.querySelectorAll("#live-ask .ask-live-opt"));
    return { active: name(a), onCard: !!(a && a.closest && a.closest("#live-ask .ask-card")), inBox: !!(a && a.id === "composer-input"), hasFocus: d.hasFocus(),
             box: (d.getElementById("composer-input") || {}).value ?? null, card: !!d.querySelector("#live-ask .ask-card"),
             hl: opts.findIndex((o) => o.classList.contains("focus")), asks: (f.contentWindow.__asks || []).slice() }; };
  const t = document.activeElement;
  return { top: name(t), topFrame: t && t.tagName === "IFRAME" ? t.id : null, c1: col("f-chat"), c2: col("f-chat-2") };
});
const settle = (fr) => fr.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
// the kernel's one delivery door: the session's scripted turn asks its question through the backend's can_use_tool
const send = async (sid, text) => { const r = await fetch(cfg.base + "/send?token=" + cfg.token, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: sid, text }) });
  return await r.json().catch(() => ({ ok: false, status: r.status })); };
const cardUp = (fr, up) => fr.waitForFunction((up) => !!document.querySelector("#live-ask .ask-card") === up, up, { timeout: 60000 });
const answer = async (fr) => { await fr.locator("#live-ask .ask-live-opt").first().click(); await cardUp(fr, false); };

try {
  await page.goto(cfg.url);
  await page.waitForFunction((sid) => { const d = document.getElementById("f-chat")?.contentDocument; return !!(d && d.querySelector('#tabs .tab[data-id="' + sid + '"]')); }, cfg.api, { timeout: 40000 });
  out.move = await page.evaluate((sid) => { const f = window.__rompMoveTab(sid, "new"); return f ? f.id : null; }, cfg.api);
  await page.waitForFunction(() => !!document.getElementById("f-chat-2"), null, { timeout: 20000 });
  const c1 = await frameOf("f-chat"), c2 = await frameOf("f-chat-2");
  await c2.waitForLoadState("load", { timeout: 30000 });
  await c1.waitForFunction((sid) => document.querySelector("#tabs .tab.active[data-id]")?.getAttribute("data-id") === sid && !!document.querySelector('#content .thread[data-session="' + sid + '"] .turn'), cfg.web, { timeout: 60000 });
  await c2.waitForFunction((sid) => document.querySelector("#tabs .tab.active[data-id]")?.getAttribute("data-id") === sid && !!document.querySelector('#content .thread[data-session="' + sid + '"] .turn'), cfg.api, { timeout: 60000 });

  // 1. the shell holds the focus, nothing typed: web's question takes the keyboard. The new column took the focus when it
  // opened, so the user clicks an empty stretch of the shell's bottom bar (its own chrome, no control under the pointer)
  out.spot = await page.evaluate(() => { const rail = document.querySelector(".pane-rail"); if (!rail) return null; const b = rail.getBoundingClientRect(), y = b.y + b.height / 2;
    for (let x = b.x + 4; x < b.right; x += 8) { const hit = document.elementFromPoint(x, y); if (hit === rail || (hit && hit.classList.contains("rail-scroll"))) return { x, y }; }   // loop-ok: bounded by the bar's width
    return null; });
  if (out.spot) await page.mouse.click(out.spot.x, out.spot.y);
  out.shellBefore = await where();
  out.sendShell = await send(cfg.web, "Please " + cfg.tShell + " for the notes-api.");
  await cardUp(c1, true); await settle(c1);
  out.shell = await where();
  await answer(c1);

  // 2. reading column 1, then typing in column 2's box: web's next question must leave the box its keys
  await c1.locator('#content .thread[data-session="' + cfg.web + '"] .turn').first().click({ position: { x: 40, y: 10 } });
  await c2.click("#composer-input");
  await page.keyboard.type(cfg.first);
  out.typingBefore = await where();
  out.sendTyping = await send(cfg.web, "Please " + cfg.tTyping + " for the notes-api.");
  await cardUp(c1, true); await settle(c1); await settle(c2);
  out.typing = await where();
  await page.keyboard.type(cfg.second);
  await settle(c2);
  out.typed = await where();
  await answer(c1);

  // 3. stepping through api's question in column 2 with the keys: web's next question must not take them
  out.sendApi = await send(cfg.api, "Please " + cfg.tApi + " for the notes-api.");
  await cardUp(c2, true);
  await c2.click("#live-ask .ask-qtext");
  out.cardsBefore = await where();
  out.sendCards = await send(cfg.web, "Please " + cfg.tCards + " for the notes-api.");
  await cardUp(c1, true); await settle(c1); await settle(c2);
  out.cardsUp = await where();
  await page.keyboard.press("ArrowDown");
  await settle(c2);
  out.cards = await where();
} catch (e) {
  out.died = String(e && e.stack || e).slice(0, 1500);
  try { out.atDeath = await where(); } catch (e2) { /* the page is gone */ }
}
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedAskCardFocusColumns(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
            cls.out = cls._drive()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served focus lab needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served focus lab needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="ask-focus-cols-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3600
        for sid, name, color, prompt, reply in (
                (WEB, "web", "#9cd2ff\t#0c1a2e", "how should the notes-api cache its list responses?",
                 "Thirty seconds, keyed by the query string; I will check the window with you before wiring it."),
                (API, "api", "#ffd29c\t#2e1a0c", "and the export endpoint?",
                 "The export streams uncached; its lock length is the one open question.")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\n" % (name, cwd, color))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "default", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            Path(proj, sid + ".jsonl").write_text(_turns(sid, t0, prompt, reply))
        cls.port, cls.token = _free_port(), "testtok-askfocuscols"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              PYTHONPATH=FAKE_SDK,   # both sessions run on the fake Agent SDK
                              ROMP_FAKE_SDK_ASKS=json.dumps(ASKS))   # …whose scripted turns ask through the backend's can_use_tool
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

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        base = "http://127.0.0.1:%d" % cls.port
        with open(cfg, "w") as f:
            json.dump({"url": "%s/?token=%s" % (base, cls.token), "base": base, "token": cls.token, "web": WEB, "api": API,
                       "first": FIRST, "second": SECOND, "tShell": T_SHELL, "tTyping": T_TYPING, "tApi": T_API, "tCards": T_CARDS}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        Path(driver).write_text(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served focus lab needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        r = json.loads(line[len("RESULT:"):])
        print("ASKFOCUSCOLS %s" % json.dumps(r), file=sys.stderr)
        return r

    def _klog(self):
        try:
            return open(self.klog).read()[-2500:]
        except OSError:
            return ""

    def _ran(self):
        r = self.out
        self.assertNotIn("died", r, "driver died: %s\nat: %r\nkernel:\n%s" % (r.get("died"), r.get("atDeath"), self._klog()))
        self.assertEqual(r["move"], "f-chat-2", "api moved into a second column: %r" % r)
        self.assertEqual(r["errs"], [], "no page errors")
        return r

    def test_1_with_the_shell_focused_and_nothing_typed_the_card_takes_the_keyboard(self):
        r = self._ran()
        self.assertTrue(r["sendShell"].get("ok"), r["sendShell"])
        b, s = r["shellBefore"], r["shell"]
        self.assertIsNotNone(r["spot"], "an empty stretch of the shell's bottom bar to click")
        self.assertIsNone(b["topFrame"], "the premise: the shell itself holds the focus, no column does: %r" % b)
        self.assertEqual(s["topFrame"], "f-chat", "the question's column took the keyboard: %r" % s)
        self.assertTrue(s["c1"]["onCard"], "…on its question card, so the arrows and Enter answer it: %r" % s)

    def test_2_typing_in_another_column_keeps_the_keyboard(self):
        r = self._ran()
        self.assertTrue(r["sendTyping"].get("ok"), r["sendTyping"])
        b, t, d = r["typingBefore"], r["typing"], r["typed"]
        self.assertEqual((b["topFrame"], b["c2"]["inBox"], b["c2"]["box"]), ("f-chat-2", True, FIRST),
                         "the premise: the user is typing in column 2's box: %r" % b)
        self.assertTrue(t["c1"]["card"], "web's question is up in column 1: %r" % t)
        self.assertEqual(t["topFrame"], "f-chat-2", "the keyboard stayed in column 2: %r" % t)
        self.assertTrue(t["c2"]["inBox"], "…in its message box: %r" % t)
        self.assertFalse(t["c1"]["onCard"], "column 1's card did not take the focus: %r" % t)
        self.assertEqual(d["c2"]["box"], FIRST + SECOND, "the next keystrokes landed in column 2's box: %r" % d)
        self.assertEqual(d["c1"]["box"], "", "…and none in column 1's: %r" % d)
        self.assertEqual(d["c1"]["asks"], [{"type": "answerAsk", "id": WEB, "target": 1}],
                         "column 1 sent only step 1's answer, nothing from the typing: %r" % d["c1"]["asks"])

    def test_3_stepping_through_a_question_in_another_column_keeps_the_keyboard(self):
        r = self._ran()
        self.assertTrue(r["sendApi"].get("ok"), r["sendApi"])
        self.assertTrue(r["sendCards"].get("ok"), r["sendCards"])
        b, u, c = r["cardsBefore"], r["cardsUp"], r["cards"]
        self.assertEqual((b["topFrame"], b["c2"]["onCard"], b["c2"]["hl"]), ("f-chat-2", True, 0),
                         "the premise: column 2's question card holds the keyboard on its first option: %r" % b)
        self.assertTrue(u["c1"]["card"], "web's question is up in column 1: %r" % u)
        self.assertEqual(u["topFrame"], "f-chat-2", "the keyboard stayed in column 2: %r" % u)
        self.assertTrue(u["c2"]["onCard"], "…on its question card: %r" % u)
        self.assertEqual((c["c2"]["hl"], c["c1"]["hl"]), (1, 0), "ArrowDown moved column 2's highlight, not column 1's: %r" % c)


if __name__ == "__main__":
    unittest.main()
