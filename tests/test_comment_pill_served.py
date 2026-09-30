#!/usr/bin/env python3
"""The touch "Comment" pill on the real /chat page, on a phone (the user 2026-09-30: a comment could not be started on
iOS at all, since a long-press selection in Safari never fires contextmenu and the right-click menu's Comment never opens).

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web: six exchanges, long enough to scroll on
a phone). The page runs in Chromium under touch emulation (a 390x844 phone, isMobile, hasTouch: a coarse pointer);
Playwright's WebKit is used instead when it is installed (cfg engine), and the result names the engine that ran.
Asserted in the browser:
  a passage selected in a reply shows the pill, at least 40px tall, just below the selection (above it only when the room
  below runs into the message box), never over the selection, inside the viewport and clear of the message box;
  while a finger is down the pill gets out of the way of a changing selection, and the finger's lift places it on the
  settled selection (a handle drag, stood in for by a CDP touch held while the selection changes);
  a tap on the pill opens the comment box in its create mode on that turn, quoting exactly the settled selection and
  anchored under it, and the pill goes;
  a scroll the page makes itself carries the pill along with its selection, a scroll under a finger hides it until the
  finger lifts, and a collapsed selection takes it away;
  on a desktop (a fine pointer) a selection shows no pill.
With COMMENT_KEY_SHOTS=<dir> the driver writes phone-pill-dark.png, phone-pill-light.png and phone-popover.png. Skips
LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host
TESTHOST)."""
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-bcdbcdbcdbcd"
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
N = 6
TARGET = 4   # the reply the passages are selected in: high enough on the phone's screen to have room below it


def reply(k):
    return ("Step %d of the notes-api export streams rows through a bounded buffer, so a slow client never holds the whole "
            "export in memory. The retry cap stays at two minutes, and the header is written once per file." % k)


FIRST = "streams rows through a bounded buffer"
SETTLED = "The retry cap stays at two minutes"


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
const pw = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
// WebKit when this box has it (the engine iOS Safari runs), Chromium with touch emulation otherwise
let engine = "chromium", browser;
try { if (fs.existsSync(pw.webkit.executablePath())) engine = "webkit"; } catch (e) { /* no webkit build here */ }
try { browser = await pw[engine].launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { engine };
const done = async (why) => { if (why) out.died = why; fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n"); await browser.close(); process.exit(0); };
const shot = async (p, name) => { if (!cfg.shots) return; fs.mkdirSync(cfg.shots, { recursive: true }); await p.screenshot({ path: cfg.shots + "/" + name + ".png" }); };
// arm a one-shot flag for the next `type` event on the document, then wait for it (Chromium holds a selectionchange made
// while a touch is down for a while: the read waits for the event itself, never a guessed delay)
const arm = (p, type) => p.evaluate((type) => { window.__seen = window.__seen || {}; window.__seen[type] = false; document.addEventListener(type, () => { window.__seen[type] = true; }, { once: true, capture: true }); }, type);
const seen = (p, type) => p.waitForFunction((type) => window.__seen && window.__seen[type], type, { timeout: 8000 }).then(() => true, () => false);
const frames = (p) => p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true)))));   // the queued selectionchange has run

const boot = async (p) => {
  await p.goto(cfg.chat);
  try {
    await p.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
    await p.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
    await p.waitForFunction((u) => !!document.querySelector(`#content .turn[data-uuid="${u}"]`), cfg.target, { timeout: 30000 });
  } catch (e) { await done("the chat never showed the reply: " + String(e).split("\n")[0]); }
  await p.waitForTimeout(800);
};
// the target reply's `text`: scrolled into the upper part of the pane, selected exactly, its box reported
const select = (p, text) => p.evaluate(([u, text]) => {
  const turn = document.querySelector(`#content .turn[data-uuid="${u}"]`);
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  if (!node) return null;
  const at = node.textContent.indexOf(text);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length); sel.addRange(r);
  const b = r.getBoundingClientRect();
  return { text: sel.toString(), top: b.top, bottom: b.bottom, left: b.left, right: b.right };
}, [cfg.target, text]);
const PILL = () => {
  const p = document.getElementById("cmt-pill");
  const c = document.getElementById("composer"), cr = c ? c.getBoundingClientRect() : null;
  if (!p) return { exists: false };
  const r = p.getBoundingClientRect();
  const s = window.getSelection(), sr = s && s.rangeCount && !s.isCollapsed ? s.getRangeAt(s.rangeCount - 1).getBoundingClientRect() : null;
  return { exists: true, shown: !p.hidden && r.height > 0, top: r.top, bottom: r.bottom, left: r.left, right: r.right, h: r.height, w: r.width,
           sel: sr ? { top: sr.top, bottom: sr.bottom, left: sr.left, right: sr.right } : null,
           text: p.textContent.trim(), vw: window.innerWidth, vh: window.innerHeight, composerTop: cr ? cr.top : null,
           bg: getComputedStyle(p).backgroundColor, color: getComputedStyle(p).color };
};
const pill = (p) => p.evaluate(PILL);
const POP = () => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const r = p.getBoundingClientRect();
  return { mode: p.dataset.mode, tid: p.dataset.tid, quote: ((p.querySelector(".cmt-quote") || {}).textContent || "").trim(),
           left: r.left, top: r.top, w: r.width, h: r.height, vw: window.innerWidth, vh: window.innerHeight };
};

const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: engine === "chromium", hasTouch: true });
const page = await phone.newPage();
await boot(page);
// put the target reply in the upper part of the pane (the page opens at the bottom), then let the scroll settle
await page.evaluate((u) => { const t = document.querySelector(`#content .turn[data-uuid="${u}"]`); const c = document.getElementById("content"); c.scrollTop += t.getBoundingClientRect().top - c.getBoundingClientRect().top - 60; }, cfg.target);
await page.waitForTimeout(400);
out.coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);

// 1. a selection shows the pill below it
out.sel1 = await select(page, cfg.first);
await page.waitForFunction(() => { const p = document.getElementById("cmt-pill"); return !!p && !p.hidden; }, null, { timeout: 5000 }).catch(() => {});
await frames(page);   // the quote chip the selection seeded shrank the pane: the clamp's scroll event lands on the next frame
out.pill1 = await pill(page);
await shot(page, "phone-pill-dark");
// the light theme re-lays the text out (its own face), and the scroll anchoring that follows hides the pill like any
// scroll: the same passage is selected again there
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(250);
out.selLight = await select(page, cfg.first);
await frames(page);
out.pillLight = await pill(page);
await shot(page, "phone-pill-light");
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(250);

// 2. a finger down while the selection changes (a handle drag's stand-in): out of the way, then placed on the settled selection
let cdp = null;
if (engine === "chromium") {
  cdp = await phone.newCDPSession(page);
  const at = await page.evaluate(() => { const c = document.getElementById("content").getBoundingClientRect(); return { x: c.right - 20, y: c.top + 30 }; });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: at.x, y: at.y }] });
  await arm(page, "selectionchange");
  out.sel2 = await select(page, cfg.settled);
  out.sawChange = await seen(page, "selectionchange");
  await frames(page);
  out.pillDuring = await pill(page);
  await arm(page, "touchcancel");
  await cdp.send("Input.dispatchTouchEvent", { type: "touchCancel", touchPoints: [] });
  out.sawLift = await seen(page, "touchcancel");
  await frames(page);
  out.pillSettled = await pill(page);
} else {
  out.sel2 = await select(page, cfg.settled);
  await frames(page);
  out.pillSettled = await pill(page);
}

// 3. a tap on the pill opens the box on the settled selection, and the pill goes
await page.tap("#cmt-pill");
await page.waitForFunction(() => !!document.getElementById("cmt-pop"), null, { timeout: 8000 }).catch(() => {});
out.pop = await page.evaluate(POP);
out.pillAfterTap = await pill(page);
await shot(page, "phone-popover");
await page.evaluate(() => document.querySelector("#cmt-pop .cmt-x") && document.querySelector("#cmt-pop .cmt-x").click());
await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});

// 4. a scroll the page makes carries the pill with its selection; a scroll under a finger hides it until the lift; a collapse
// takes it away
await select(page, cfg.first);
await frames(page);
out.pillAgain = await pill(page);
await page.evaluate(() => { document.getElementById("content").scrollTop -= 40; });
await frames(page);
out.pillPageScrolled = await pill(page);
if (cdp) {
  const at = await page.evaluate(() => { const c = document.getElementById("content").getBoundingClientRect(); return { x: c.right - 20, y: c.top + 30 }; });
  await arm(page, "touchstart");
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: at.x, y: at.y }] });
  await seen(page, "touchstart");
  await arm(page, "scroll");
  await page.evaluate(() => { document.getElementById("content").scrollTop += 30; });
  await seen(page, "scroll");
  await frames(page);
  out.pillFingerScroll = await pill(page);
  await arm(page, "touchcancel");
  await cdp.send("Input.dispatchTouchEvent", { type: "touchCancel", touchPoints: [] });
  await seen(page, "touchcancel");
  await frames(page);
  out.pillAfterLift = await pill(page);
}
await page.evaluate(() => window.getSelection().removeAllRanges());
await frames(page);
out.pillCollapsed = await pill(page);

// 5. a desktop: a fine pointer shows no pill
const desk = await browser.newContext({ viewport: { width: 1200, height: 800 } });
const dpage = await desk.newPage();
await boot(dpage);
out.deskCoarse = await dpage.evaluate(() => matchMedia("(pointer: coarse)").matches);
out.deskSel = await select(dpage, cfg.first);
await frames(dpage);
out.deskPill = await pill(dpage);
await done();
"""


class ServedCommentPill(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-pill-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        fakebin = os.path.join(cls.lab, "fakebin")   # the machine's user manager is not the lab's
        os.makedirs(fakebin)
        for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
            Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
            os.chmod(os.path.join(fakebin, tool), 0o755)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3600
        recs, prev = [], None
        for k in range(N):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "timestamp": iso(t0 + 300 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": WEB, "message": {"role": "user", "content": "notes-api: step %d, how does the export look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 300 * k + 20), "uuid": a, "parentUuid": u, "sessionId": WEB,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply(k)}]}})
            prev = a
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.port, cls.token = _free_port(), "testtok-commentpill"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        env["PATH"] = fakebin + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
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

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB,
                       "target": REPLY_UUID % TARGET, "first": FIRST, "settled": SETTLED,
                       "shots": os.environ.get("COMMENT_KEY_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertNotIn("died", r, r)
        return r

    def _placed(self, pill, where):
        """Shown, thumb-sized, beside the live selection and never over it (below unless the message box leaves no room),
        inside the viewport and clear of the message box."""
        self.assertTrue(pill["shown"], "%s: the pill shows: %r" % (where, pill))
        sel = pill["sel"]
        self.assertIsNotNone(sel, "%s: a selection is live: %r" % (where, pill))
        self.assertGreaterEqual(pill["h"], 40, "%s: a thumb's target: %r" % (where, pill))
        self.assertEqual(pill["text"], "Comment", pill)
        floor = min(pill["vh"], pill["composerTop"] if pill["composerTop"] is not None else pill["vh"])
        room_below = sel["bottom"] + 14 + pill["h"] <= floor - 8
        if room_below:
            self.assertLess(abs(pill["top"] - (sel["bottom"] + 14)), 1.5, "%s: just below the selection: %r" % (where, pill))
        else:
            self.assertLessEqual(pill["bottom"], sel["top"], "%s: no room below, so above it: %r" % (where, pill))
        self.assertGreaterEqual(pill["left"], 7.5, pill)
        self.assertLessEqual(pill["right"], pill["vw"] - 7.5, pill)
        self.assertLessEqual(pill["bottom"], floor - 7.5, "%s: clear of the message box: %r" % (where, pill))
        return room_below

    def test_a_phone_selection_gets_a_comment_pill_whose_tap_opens_the_box(self):
        r = self._drive()
        print("ENGINE", r["engine"])
        self.assertTrue(r["coarse"], "the phone context is a coarse pointer: %r" % r)
        # 1. shown below the selection
        self.assertEqual((r["sel1"] or {}).get("text"), FIRST, r["sel1"])
        self.assertTrue(self._placed(r["pill1"], "the first selection"), "the target reply leaves room below: %r" % r["pill1"])
        self._placed(r["pillLight"], "the light theme")
        self.assertNotEqual(r["pillLight"]["bg"], r["pill1"]["bg"], "the light theme dresses it in its own palette: %r" % r)
        # 2. out of the way while a finger is down, placed on the settled selection at the lift
        if r["engine"] == "chromium":
            self.assertTrue(r["sawChange"] and r["sawLift"], "the page saw the change under the finger and the lift: %r" % r)
            self.assertFalse(r["pillDuring"]["shown"], "a selection changing under a finger hides the pill: %r" % r["pillDuring"])
        self.assertEqual((r["sel2"] or {}).get("text"), SETTLED, r["sel2"])
        self._placed(r["pillSettled"], "the settled selection")
        # 3. a tap opens the box on the settled selection, anchored under it, and the pill goes
        pop, sel = r["pop"], r["pillSettled"]["sel"]
        self.assertIsNotNone(pop, "a tap on the pill opened no comment box: %r" % r)
        self.assertEqual(pop["mode"], "create", pop)
        self.assertEqual(pop["tid"], REPLY_UUID % TARGET, "on the reply's turn: %r" % pop)
        self.assertEqual(pop["quote"], SETTLED, "quoting exactly the settled selection: %r" % pop)
        want_x = max(8, min(sel["left"], pop["vw"] - pop["w"] - 8))
        want_y = max(8, min(sel["bottom"] + 6, pop["vh"] - pop["h"] - 8))
        self.assertLess(abs(pop["left"] - want_x), 1.5, "anchored at the selection's left: %r vs %r" % (pop, sel))
        self.assertLess(abs(pop["top"] - want_y), 1.5, "anchored just under the selection: %r vs %r" % (pop, sel))
        self.assertFalse(r["pillAfterTap"]["shown"], "the pill goes once the box opens: %r" % r["pillAfterTap"])
        # 4. a scroll the page makes carries it; a scroll under a finger hides it until the lift; a collapse takes it away
        self._placed(r["pillAgain"], "selected again")
        moved = r["pillPageScrolled"]
        self._placed(moved, "after a scroll the page made")
        self.assertGreater(moved["sel"]["top"] - r["pillAgain"]["sel"]["top"], 30, "the scroll moved the selection: %r" % moved)
        self.assertLess(abs((moved["top"] - r["pillAgain"]["top"]) - (moved["sel"]["top"] - r["pillAgain"]["sel"]["top"])), 1.5,
                        "the pill moved with it: %r vs %r" % (moved, r["pillAgain"]))
        if r["engine"] == "chromium":
            self.assertFalse(r["pillFingerScroll"]["shown"], "a scroll under a finger hides it: %r" % r["pillFingerScroll"])
            self._placed(r["pillAfterLift"], "after the finger lifted")
        self.assertFalse(r["pillCollapsed"]["shown"], "a collapsed selection: no pill: %r" % r["pillCollapsed"])
        # 5. a desktop keeps its right-click and its key: no pill
        self.assertFalse(r["deskCoarse"], r)
        self.assertEqual((r["deskSel"] or {}).get("text"), FIRST, r["deskSel"])
        self.assertFalse(r["deskPill"].get("shown", False), "a fine pointer shows no pill: %r" % r["deskPill"])


if __name__ == "__main__":
    unittest.main()
