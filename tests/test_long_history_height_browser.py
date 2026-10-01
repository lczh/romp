"""A long session's chat lands at its newest row and stays there (2026-10-01). The head gap (T386 stage 2) is empty space of an
estimated height, its turns times the px-per-turn the page measured off the rows it holds, and a browser lays out no box taller than a
fixed limit (33,554,432 px in Chromium at a 1x scale, half that in Chrome on a 2x display, whose layout units carry the device scale).
A session long enough that the estimate passed the limit had everything below it, the newest rows included, laid out AT the limit,
out of reach: the bottom of the scroll range sat inside the gap, the gap stayed in the viewport, and it asked for page after page
while the reader waited at the end.

Served, on a hermetic kernel over a synthetic transcript built to cross the limit, in Chromium at a forced 2x scale (the reported
browser's): about two thousand short turns of history, then a tail of tall ones (each reply a long code block), so the page measures
a tall turn off the tail and the head gap, at that figure, would stand about 22 million px. The page must:

- land with the newest row on screen at the bottom, and a jump to the end must keep it there;
- keep its scroll height under the smallest limit a browser lays out (Chrome on a 2x display), the gap's share bounded;
- ask for no history page while the reader sits at the end (the gap is far above, not in the viewport).

Synthetic fixtures only (placeholder uuids, invented prose).
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.dist_copy import copy_dist  # noqa: E402
from tests.test_live_paused_window_browser import BIN, EXT, _free_port  # noqa: E402
import tests.test_ship_reship_served as _lab  # noqa: E402  the lab kernel's environment

SID = "bbbbbbbb-1111-2222-3333-555555555555"
TURNS = 2130          # turns in the transcript: the wire tail holds the last ~125, the head gap the rest
TALL = 130            # the last TALL turns' replies are tall: the page measures its px-per-turn off them
TALL_LINES = 600      # lines in each tall reply's code block (~18 px each): a turn of ~11,000 px
BROWSER_LIMIT_2X = 16_777_216   # the tallest box Chrome lays out on a 2x display (2^25 layout units at two device px per CSS px)

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch({ args: ["--force-device-scale-factor=2"] }); }   // a 2x display: the smaller layout limit
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
await page.addInitScript(() => {
  const send = WebSocket.prototype.send;
  window.__sent = [];
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && m.type) window.__sent.push(m); } catch (e) { /* not a frame */ }
    return send.call(this, d);
  };
});
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 300)));
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
await page.waitForFunction(() => document.querySelectorAll("#content .turn[data-uuid]").length >= 20, null, { timeout: 60000 });
const painted = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
// the page's view of the end: the newest row's place against the viewport, the scroll extent, the gap's drawn share, the page asks
const reading = () => page.evaluate((newest) => {
  const c = document.getElementById("content"), cr = c.getBoundingClientRect();
  const row = document.querySelector(`#content .turn[data-uuid="${newest}"]`);
  const r = row ? row.getBoundingClientRect() : null;
  const top = document.querySelector("#content .tx-spacer-top");
  const gaps = Array.from(document.querySelectorAll("#content .tx-gap")).map((g) => g.offsetHeight);
  return { sh: c.scrollHeight, st: c.scrollTop, ch: c.clientHeight, dist: c.scrollHeight - c.scrollTop - c.clientHeight,
           newestRendered: !!row, newestOnScreen: !!r && r.bottom > cr.top + 1 && r.top < cr.bottom - 1,
           topSpacer: top ? top.offsetHeight : 0, gapEls: gaps,
           regions: typeof window.__rompRegions === "function" ? window.__rompRegions() : null,
           asks: window.__sent.filter((m) => m.type === "loadTurns").map((m) => [m.lo, m.hi]) };
}, cfg.newest);
// ROAD 1: the boot lands at the end; let the first measure re-size the gap and any ask it provokes go out (a page reply would land too)
await painted(); await page.waitForTimeout(2500); await painted();
const landed = await reading();
// ROAD 2: the reader jumps to the end, twice (the second after whatever the first provoked has settled), and waits there
for (let i = 0; i < 2; i++) {
  await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; });
  await painted(); await page.waitForTimeout(1500); await painted();
}
const jumped = await reading();
process.stdout.write("RESULT:" + JSON.stringify({ landed, jumped, pageErrors }) + "\n");
await browser.close();
"""


class LongHistoryHeight(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here) — the served lab needs them")
        cls.lab = tempfile.mkdtemp(prefix="long-history-height-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no session host (T348)
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        tall = "```\n" + "\n".join("n%d" % i for i in range(TALL_LINES)) + "\n```"
        recs, prev, base = [], None, 1789000000
        for k in range(TURNS):
            u = "11111111-2222-3333-4444-%012d" % (2 * k)
            a = "22222222-3333-4444-5555-%012d" % (2 * k + 1)
            tu = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base + 2 * k))
            ta = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base + 2 * k + 1))
            recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": tu, "sessionId": SID,
                         "message": {"role": "user", "content": "question number %d about the notes api" % k}})
            text = ("Answer %d: the notes index, row by row.\n\n" % k + tall) if k >= TURNS - TALL else \
                "Answer %d: the handler reads the note by id and returns it." % k
            recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ta, "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": text}]}})
            prev = a
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.newest = "22222222-3333-4444-5555-%012d" % (2 * TURNS - 1)
        cls.port = _free_port()
        cls.token = "testtok-longhistory"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            cls._skip("hermetic kernel never served /healthz here")
        cls._r = None

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _result(self):
        if type(self)._r is not None:
            return type(self)._r
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "newest": self.newest}, f)
        driver = os.path.join(self.lab, "drive.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            self._skip("no playwright browser on this box — the served lab needs one")
        klog = ""
        try:
            with open(self.klog) as f:
                klog = f.read()[-2500:]
        except OSError:
            pass
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + klog)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        type(self)._r = json.loads(line[len("RESULT:"):])
        print("LONG-HISTORY:", json.dumps(type(self)._r), file=sys.stderr)
        return type(self)._r

    def test_the_lab_holds_a_gap_of_most_of_the_transcript_above_the_tail_run(self):
        rs = self._result()["landed"]["regions"] or []
        self.assertTrue(rs and rs[-1]["kind"] == "run" and rs[-1]["hi"] is None, "the tail run is last and open-ended: %r" % rs)
        widest = max((x["hi"] - x["lo"] for x in rs if x["kind"] == "gap"), default=0)
        self.assertGreater(widest, 1900, "a gap holds most of the transcript's turns (the estimate the lab is built to push past the limit): %r" % rs)

    def test_the_page_lands_with_the_newest_row_on_screen(self):
        L = self._result()["landed"]
        self.assertTrue(L["newestOnScreen"], "the newest row is on screen at the landing: %r" % {k: L[k] for k in L if k != "regions"})
        self.assertLessEqual(L["dist"], 2, "the landing is at the bottom: %r" % L["dist"])

    def test_a_jump_to_the_end_shows_the_newest_row(self):
        J = self._result()["jumped"]
        self.assertTrue(J["newestOnScreen"], "a jump to the end shows the newest row: %r" % {k: J[k] for k in J if k != "regions"})
        self.assertLessEqual(J["dist"], 2, "…at the bottom: %r" % J["dist"])

    def test_the_scroll_height_stays_under_the_smallest_browser_limit(self):
        r = self._result()
        for road in ("landed", "jumped"):
            self.assertLess(r[road]["sh"], BROWSER_LIMIT_2X, "%s: the thread fits the tallest box Chrome lays out on a 2x display" % road)

    def test_the_end_asks_for_no_history_page(self):
        r = self._result()
        self.assertEqual(r["landed"]["asks"], [], "the gap is far above the landing, so no page is asked: %r" % r["landed"]["asks"])
        self.assertEqual(r["jumped"]["asks"], [], "…nor after a jump to the end: %r" % r["jumped"]["asks"])

    def test_the_page_raised_no_error(self):
        self.assertEqual(self._result()["pageErrors"], [])


if __name__ == "__main__":
    unittest.main()
