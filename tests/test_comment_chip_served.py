#!/usr/bin/env python3
"""Comment in the message box's quote-chip row, and finger-sized controls in that row and the staged strip, on the real
/chat page (the user 2026-09-30: on a phone the floating Comment pill under a selection was often covered by the system's
own selection menu, which a page can neither query nor avoid; and the row's buttons had to be tapped too exactly).

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web: six exchanges). Each page stages two
messages first (a quoted passage through the row's Stage, then a typed note through Ctrl+Enter), so the staged strip is up
with its caret, Send now and one discard per item, and then selects a second passage, which seeds the quote chip.
Asserted in the browser, in the dark theme and the light one (the settings key `yatharth-light`), on a phone (390x844
under touch emulation: a coarse pointer; Playwright's WebKit when it is installed, Chromium otherwise, and the result
names the engine) and on a desktop:
  the chip row reads chip, Comment, Stage, in that order;
  on the phone every control there and in the staged strip (Comment, Stage, the chip's x, Send now, each staged item's x,
  the collapse caret) owns a tap area at least 44px each way, measured with elementFromPoint from the control's centre out
  to each edge and at the corners, and no two of those areas overlap, nor any of them the message box;
  on the desktop each control's tap area is its own box, as before;
  once the selection has collapsed (the tap elsewhere that dismisses a selection on iOS), Comment opens the comment box in
  its create mode on the chip's turn, quoting exactly the chip's passage, under that passage.
With COMMENT_CHIP_SHOTS=<dir> the driver writes a screenshot of each page and a close-up of each message box. Skips LOUDLY
without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host
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

WEB = "aaaaaaaa-1111-2222-3333-cdecdecdecde"
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
N = 6
TARGET = 4   # the reply the passages are selected in


def reply(k):
    return ("Step %d of the notes-api export streams rows through a bounded buffer, so a slow client never holds the whole "
            "export in memory. The retry cap stays at two minutes, and the header is written once per file." % k)


STAGED = "streams rows through a bounded buffer"      # the first passage: staged through the row's Stage
PASSAGE = "The retry cap stays at two minutes"        # the second: the chip Comment opens on
NOTE = "Check the header is written once per file"    # the typed note staged on its own
CONTROLS = {   # the controls whose tap areas are measured, by what the user calls them
    "comment": "#composer-chips .composer-comment-btn",
    "stage": "#composer-chips .composer-stage-btn",
    "chip-x": "#composer-chips .composer-chip .composer-chip-x",
    "send-now": "#composer-staged .staged-go",
    "staged-x": "#composer-staged .staged-list .composer-chip-x",
    "caret": "#composer-staged .staged-caret",
}


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
const out = { engine, runs: {} };
const done = async (why) => { if (why) out.died = why; fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n"); await browser.close(); process.exit(0); };
const shots = cfg.shots;
if (shots) fs.mkdirSync(shots, { recursive: true });
const THEMES = { dark: "yatharth", light: "yatharth-light" };   // the settings store's keys, read by theme.ts at boot
const frames = (p) => p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true)))));

// the target reply's `text`, selected exactly (a long-press or a drag stands in for it), its box reported
const select = (p, text) => p.evaluate(([u, text]) => {
  const turn = document.querySelector(`#content .turn[data-uuid="${u}"]`);
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  if (!node) return null;
  const at = node.textContent.indexOf(text);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length); sel.addRange(r);
  return { text: sel.toString() };
}, [cfg.target, text]);
// where the passage is on screen now, read off the text itself (no selection needed)
const passageBox = (p, text) => p.evaluate(([u, text]) => {
  const turn = document.querySelector(`#content .turn[data-uuid="${u}"]`);
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  if (!node) return null;
  const at = node.textContent.indexOf(text);
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length);
  const b = r.getBoundingClientRect();
  return { left: b.left, top: b.top, bottom: b.bottom };
}, [cfg.target, text]);

// Each control's tap area, measured the way a finger meets it: from the control's centre, elementFromPoint walks out to
// each edge (the last point that still lands on the control or inside it, bisected to a hundredth of a pixel), and the
// four corners of that box must land on it too. An enlarged hit box (an invisible pseudo-element) counts; a neighbour
// painted over part of it does not, which is how an overlap shows up as a short side.
const HITS = (sels) => {
  const on = (el, x, y) => {
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
    const h = document.elementFromPoint(x, y);
    return !!h && (h === el || el.contains(h));
  };
  const reach = (el, cx, cy, dx, dy) => {
    let d = 0;
    while (d < 150 && on(el, cx + dx * (d + 1), cy + dy * (d + 1))) d += 1;
    let lo = d, hi = d + 1;
    for (let i = 0; i < 7; i++) { const m = (lo + hi) / 2; if (on(el, cx + dx * m, cy + dy * m)) lo = m; else hi = m; }
    return lo;
  };
  const one = (el) => {
    const r = el.getBoundingClientRect();
    const vis = { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height };
    const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    if (!on(el, cx, cy)) { const h = document.elementFromPoint(cx, cy); return { vis, covered: h ? h.tagName + "." + h.className : "nothing" }; }
    const l = cx - reach(el, cx, cy, -1, 0), rr = cx + reach(el, cx, cy, 1, 0), t = cy - reach(el, cx, cy, 0, -1), b = cy + reach(el, cx, cy, 0, 1);
    const corners = [[l + 0.25, t + 0.25], [rr - 0.25, t + 0.25], [l + 0.25, b - 0.25], [rr - 0.25, b - 0.25]].every(([x, y]) => on(el, x, y));
    return { vis, hit: { l, r: rr, t, b, w: rr - l, h: b - t }, corners, text: (el.textContent || "").trim() };
  };
  const res = {};
  for (const [name, sel] of Object.entries(sels)) res[name] = [...document.querySelectorAll(sel)].map(one);
  const ta = document.getElementById("composer-input").getBoundingClientRect();
  res.box = { l: ta.left, r: ta.right, t: ta.top, b: ta.bottom };
  return res;
};
const ROW = () => [...document.querySelectorAll("#composer-chips > *")].map((e) => e.classList.contains("composer-chip") ? "chip" : (e.textContent || "").trim());
const POP = () => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const r = p.getBoundingClientRect();
  return { mode: p.dataset.mode, tid: p.dataset.tid, quote: ((p.querySelector(".cmt-quote") || {}).textContent || "").trim(),
           left: r.left, top: r.top, w: r.width, h: r.height, vw: window.innerWidth, vh: window.innerHeight };
};
const shotComposer = async (p, name) => {
  if (!shots) return;
  await p.screenshot({ path: shots + "/" + name + ".png" });
  const c = await p.evaluate(() => { const r = document.getElementById("composer").getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  const y = Math.max(0, c.y - 12);
  await p.screenshot({ path: shots + "/" + name + "-closeup.png", clip: { x: Math.max(0, c.x), y, width: Math.min(c.w, await p.evaluate(() => innerWidth)), height: c.y + c.h - y } });
};

const run = async (kind, theme) => {
  const phone = kind === "phone";
  const ctx = await browser.newContext(phone
    ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: engine === "chromium", hasTouch: true }
    : { viewport: { width: 1200, height: 800 }, deviceScaleFactor: 2 });
  await ctx.addInitScript((t) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) { /* no storage */ } }, THEMES[theme]);
  const p = await ctx.newPage();
  const r = { errs: [] };
  p.on("pageerror", (e) => r.errs.push(String(e && e.stack || e).slice(0, 400)));
  const press = (sel) => phone ? p.tap(sel, { timeout: 5000 }) : p.click(sel, { timeout: 5000 });
  await p.goto(cfg.chat);
  try {
    await p.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
    await p.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
    await p.waitForFunction((u) => !!document.querySelector(`#content .turn[data-uuid="${u}"]`), cfg.target, { timeout: 30000 });
  } catch (e) { await done(kind + "/" + theme + ": the chat never showed the reply: " + String(e).split("\n")[0]); }
  await p.waitForTimeout(600);
  r.coarse = await p.evaluate(() => matchMedia("(pointer: coarse)").matches);
  r.bodyClass = await p.evaluate(() => document.body.className);
  // two staged messages: the first passage through the row's Stage, then a typed note through Ctrl+Enter
  r.staged0 = await select(p, cfg.staged);
  await p.waitForSelector("#composer-chips .composer-stage-btn", { timeout: 5000 }).catch(() => {});
  await press("#composer-chips .composer-stage-btn").catch((e) => { r.errs.push("stage: " + String(e).split("\n")[0]); });
  await p.fill("#composer-input", cfg.note);
  await p.press("#composer-input", "Control+Enter");
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  r.stagedCount = await p.evaluate(() => document.querySelectorAll("#composer-staged .staged-chip").length);
  // put the target reply in the upper part of the pane, then select the passage Comment opens on
  await p.evaluate((u) => { const t = document.querySelector(`#content .turn[data-uuid="${u}"]`); const c = document.getElementById("content"); c.scrollTop += t.getBoundingClientRect().top - c.getBoundingClientRect().top - 60; }, cfg.target);
  await frames(p);
  r.sel = await select(p, cfg.passage);
  await p.waitForSelector("#composer-chips .composer-comment-btn", { timeout: 5000 }).catch(() => {});
  await frames(p);
  r.row = await p.evaluate(ROW);
  r.hits = await p.evaluate(HITS, cfg.controls);
  await shotComposer(p, kind + "-" + theme);
  // the selection collapses (on iOS the tap elsewhere dismisses it); the chip keeps what it captured, and Comment opens the box on that
  await p.evaluate(() => window.getSelection().removeAllRanges());
  await frames(p);
  r.collapsed = await p.evaluate(() => window.getSelection().isCollapsed || !window.getSelection().rangeCount);
  r.rowAfterCollapse = await p.evaluate(ROW);
  r.passage = await passageBox(p, cfg.passage);
  const hasComment = await p.evaluate(() => !!document.querySelector("#composer-chips .composer-comment-btn"));
  if (hasComment) {
    await press("#composer-chips .composer-comment-btn").catch((e) => { r.errs.push("comment: " + String(e).split("\n")[0]); });
    await p.waitForFunction(() => !!document.getElementById("cmt-pop"), null, { timeout: 8000 }).catch(() => {});
  }
  r.pop = await p.evaluate(POP);
  if (r.pop && shots) await p.screenshot({ path: shots + "/" + kind + "-" + theme + "-comment-box.png" });
  await ctx.close();
  return r;
};

for (const kind of ["phone", "desktop"]) for (const theme of ["dark", "light"]) out.runs[kind + "-" + theme] = await run(kind, theme);
await done();
"""


class ServedCommentChip(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-chip-")
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
        cls.port, cls.token = _free_port(), "testtok-commentchip"
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
        cls.result = cls._drive()

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
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "sid": WEB,
                       "target": REPLY_UUID % TARGET, "staged": STAGED, "passage": PASSAGE, "note": NOTE,
                       "controls": CONTROLS, "shots": os.environ.get("COMMENT_CHIP_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=400,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        assert p.returncode == 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:]
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        assert line is not None, "driver printed no result:\n" + p.stdout[-3000:]
        r = json.loads(line[len("RESULT:"):])
        assert "died" not in r, r
        print("ENGINE", r["engine"])
        return r

    def _run(self, name):
        r = self.result["runs"][name]
        self.assertEqual(r["errs"], [], "%s: the page threw: %r" % (name, r["errs"]))
        light = name.endswith("light")
        self.assertEqual("theme-light" in r["bodyClass"].split(), light, "%s: the theme under test: %r" % (name, r["bodyClass"]))
        self.assertIn("chat-theme-yatharth", r["bodyClass"].split(), r["bodyClass"])
        self.assertEqual(r["coarse"], name.startswith("phone"), "%s: the pointer under test: %r" % (name, r))
        self.assertEqual(r["stagedCount"], 2, "%s: the quoted passage and the typed note are staged: %r" % (name, r))
        self.assertEqual((r["sel"] or {}).get("text"), PASSAGE, r["sel"])
        return r

    def _chip_row_then_comment(self, name):
        r = self._run(name)
        # the row: the chip, then Comment, then Stage (Comment first, as in the right-click menu)
        self.assertEqual(r["row"], ["chip", "Comment", "Stage"], "%s: the chip row: %r" % (name, r["row"]))
        # the selection collapsed; the chip and its Comment stay (a collapse never clears a chip)
        self.assertTrue(r["collapsed"], r)
        self.assertEqual(r["rowAfterCollapse"], ["chip", "Comment", "Stage"], "%s: after the collapse: %r" % (name, r["rowAfterCollapse"]))
        # Comment opened the box on the chip's own passage and turn, under the passage
        pop, at = r["pop"], r["passage"]
        self.assertIsNotNone(pop, "%s: Comment opened no comment box: %r" % (name, r))
        self.assertEqual(pop["mode"], "create", pop)
        self.assertEqual(pop["tid"], REPLY_UUID % TARGET, "%s: on the reply's turn: %r" % (name, pop))
        self.assertEqual(pop["quote"], PASSAGE, "%s: quoting exactly the chip's passage: %r" % (name, pop))
        want_x = max(8, min(at["left"], pop["vw"] - pop["w"] - 8))
        want_y = max(8, min(at["bottom"] + 6, pop["vh"] - pop["h"] - 8))
        self.assertLess(abs(pop["left"] - want_x), 1.5, "%s: at the passage's left: %r vs %r" % (name, pop, at))
        self.assertLess(abs(pop["top"] - want_y), 1.5, "%s: just under the passage: %r vs %r" % (name, pop, at))
        return r

    def _boxes(self, name, r):
        hits = r["hits"]
        boxes = []
        for key in CONTROLS:
            found = hits[key]
            want = 2 if key == "staged-x" else 1
            self.assertEqual(len(found), want, "%s: %s on the page: %r" % (name, key, found))
            for i, h in enumerate(found):
                self.assertIn("hit", h, "%s: %s[%d] is covered at its centre: %r" % (name, key, i, h))
                boxes.append(("%s[%d]" % (key, i), h))
        return boxes

    def test_phone_dark(self):
        self._phone("phone-dark")

    def test_phone_light(self):
        self._phone("phone-light")

    def _phone(self, name):
        r = self._chip_row_then_comment(name)
        boxes = self._boxes(name, r)
        for label, h in boxes:
            hit = h["hit"]
            self.assertGreaterEqual(hit["w"], 44 - 0.02, "%s: %s's tap area is at least 44px wide: %r" % (name, label, h))
            self.assertGreaterEqual(hit["h"], 44 - 0.02, "%s: %s's tap area is at least 44px tall: %r" % (name, label, h))
            self.assertTrue(h["corners"], "%s: %s owns the corners of its tap area: %r" % (name, label, h))
        ta = r["hits"]["box"]
        for i, (la, a) in enumerate(boxes):
            A = a["hit"]
            self.assertFalse(min(A["r"], ta["r"]) - max(A["l"], ta["l"]) > 0.05 and min(A["b"], ta["b"]) - max(A["t"], ta["t"]) > 0.05,
                             "%s: %s's tap area reaches into the message box: %r vs %r" % (name, la, A, ta))
            for lb, b in boxes[i + 1:]:
                B = b["hit"]
                w = min(A["r"], B["r"]) - max(A["l"], B["l"])
                h = min(A["b"], B["b"]) - max(A["t"], B["t"])
                self.assertFalse(w > 0.05 and h > 0.05, "%s: the tap areas of %s and %s overlap: %r vs %r" % (name, la, lb, A, B))

    def test_desktop_dark(self):
        self._desktop("desktop-dark")

    def test_desktop_light(self):
        self._desktop("desktop-light")

    def _desktop(self, name):
        r = self._chip_row_then_comment(name)
        for label, h in self._boxes(name, r):
            hit, vis = h["hit"], h["vis"]
            for side in ("l", "r", "t", "b"):
                self.assertLess(abs(hit[side] - vis[side]), 1.01, "%s: %s's tap area is its own box, as before: %r" % (name, label, h))
            self.assertLess(vis["h"], 30, "%s: %s keeps its compact desktop size: %r" % (name, label, h))


if __name__ == "__main__":
    unittest.main()
