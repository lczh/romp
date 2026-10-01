"""A long session's chat lands at its newest row, stays still at its end, and a jump into its history stays where it was dropped
(2026-10-01). The head gap (T386 stage 2) is empty space of an estimated height, its turns times the px-per-turn the page measured off
the rows it holds, and a browser lays out no box taller than a fixed limit (33,554,432 px in Chromium at a 1x scale, divided by the
device scale and the zoom: 16,777,216 on a 2x display, 2,796,202 at 12x). A session long enough that the estimate passed the limit had
everything below it, the newest rows included, laid out AT the limit, out of reach: the bottom of the scroll range sat inside the gap,
the gap stayed in the viewport, and it asked for page after page while the reader waited at the end.

Served, on a hermetic kernel over a synthetic transcript built to cross the limit: about two thousand short turns of history, then a
tail of tall ones (each reply a long code block), so the page measures a tall turn off the tail and the head gap, at that figure, would
stand far past the limit. Two labs:

LongHistoryHeight, at a forced 2x scale (the reported browser's). The page must:
- land with the newest row on screen at the bottom, and a jump to the end must keep it there;
- keep its scroll height under the limit;
- sit still at the end: no page asked, no scroll written while the reader waits there;
- keep a scrollbar jump into the middle of the history where it was dropped (a gap is one display unit, and a re-window used to put
  its top at the viewport top: the reader was sent to the head), asking ONE page, the one holding the turn under the viewport top;
- meet a parked reader's need off a fill's own completion: a page the reader did not ask for lands while their turn is still unloaded
  and they have not moved, and the page holding their turn is asked then (the rebuilt gap's first observation asks nothing: that is
  the page's own rebuild, not the reader);
- land a jump to an early turn on that turn's row.

UnreachableEndSettles, at a forced 12x scale (limit 2,796,202), with a tail taller than the limit on its own: the end is out of reach
for a reason that is not the gap. The page must settle there: no re-window on the echo of its own write (the loop that re-rendered the
view and crept the reader up by a few px, once per render, for as long as the tab stayed open), no page walk, no error; and the gaps
are drawn at most a quarter of the limit the page measured.

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
BASE = 1789000000
LAYOUT_LIMIT_1X = 33_554_432   # the tallest box Chromium lays out at a 1x scale (2^25 layout units)
JUMP_TURN = 600       # the early turn the jump-to-turn leg lands on

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch({ args: ["--force-device-scale-factor=" + cfg.scale] }); }   // the scale divides the layout limit
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: cfg.viewport });
await page.addInitScript(() => {
  const send = WebSocket.prototype.send;
  window.__sent = [];
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && m.type) window.__sent.push(m); } catch (e) { /* not a frame */ }
    return send.call(this, d);
  };
  // every scrollTop write to the thread, the page's and the driver's: an idle reading counts the ones in its own window
  const d = Object.getOwnPropertyDescriptor(Element.prototype, "scrollTop");
  window.__writes = 0;
  Object.defineProperty(Element.prototype, "scrollTop", { configurable: true, get() { return d.get.call(this); },
    set(v) { if (this.id === "content") window.__writes++; d.set.call(this, v); } });
});
// the kernel's history replies, held at the wire on demand (the parked-reader leg): the page's ask is answered when the driver says
let holdTurns = false; const held = [];
await page.routeWebSocket((u) => /[?&]app=chat(?:&|$)/.test(u.href), (ws) => {
  const server = ws.connectToServer();
  ws.onMessage((m) => server.send(m));
  server.onMessage((m) => { if (holdTurns && typeof m === "string" && /^\{"type": ?"chatTurns"/.test(m)) held.push([ws, m]); else ws.send(m); });
  server.onClose(() => ws.close()); ws.onClose(() => server.close());
});
const release = () => { holdTurns = false; for (const [w, m] of held.splice(0)) { try { w.send(m); } catch (e) { /* closed */ } } };
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 300)));
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
await page.waitForFunction(() => document.querySelectorAll("#content .turn[data-uuid]").length >= 20, null, { timeout: 60000 });
const painted = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
const asks = () => page.evaluate(() => window.__sent.filter((m) => m.type === "loadTurns").map((m) => [m.lo, m.hi]));
const asksPast = (n, ms) => page.waitForFunction((k) => window.__sent.filter((m) => m.type === "loadTurns").length > k, n, { timeout: ms }).catch(() => {});
const setTop = async (y) => { await page.evaluate((yy) => { document.getElementById("content").scrollTop = yy; }, y); await painted(); };
// the page's view: the newest row's place, the scroll extent, the gap under the viewport top (its span, its drawn height and the turn
// under the top by its fraction), the first turn row on screen and the turn it names
const reading = () => page.evaluate((newest) => {
  const c = document.getElementById("content"), cr = c.getBoundingClientRect();
  const row = document.querySelector(`#content .turn[data-uuid="${newest}"]`);
  const r = row ? row.getBoundingClientRect() : null;
  const g = Array.from(document.querySelectorAll("#content .tx-gap")).find((x) => { if (x.offsetParent === null) return false; const b = x.getBoundingClientRect(); return b.bottom > cr.top && b.top < cr.bottom; });
  const gr = g ? g.getBoundingClientRect() : null;
  const rows = Array.from(c.querySelectorAll(".turn[data-uuid]")).filter((e) => { if (e.offsetParent === null) return false; const b = e.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top && b.top < cr.bottom; });
  return { sh: c.scrollHeight, st: c.scrollTop, ch: c.clientHeight, dist: c.scrollHeight - c.scrollTop - c.clientHeight,
           newestRendered: !!row, newestOnScreen: !!r && r.bottom > cr.top + 1 && r.top < cr.bottom - 1,
           gap: g ? { lo: +g.dataset.lo, hi: +g.dataset.hi, h: gr.height, turnAtTop: gr.top <= cr.top && gr.bottom > cr.top ? +g.dataset.lo + (+g.dataset.hi - +g.dataset.lo) * ((cr.top - gr.top) / gr.height) : null } : null,
           rowsOnScreen: rows.length, rowTurn: rows.length && rows[0].dataset.turn ? +rows[0].dataset.turn : null,
           regions: typeof window.__rompRegions === "function" ? window.__rompRegions() : null };
}, cfg.newest);
const out = { pageErrors };
// ROAD 1: the boot lands at the end; let the first measure re-size the gap and any ask it provokes go out (a page reply would land too)
await painted(); await page.waitForTimeout(2500); await painted();
out.landed = Object.assign(await reading(), { asks: await asks() });
// ROAD 2: the reader jumps to the end, twice (the second after whatever the first provoked has settled), and waits there
for (let i = 0; i < 2; i++) { await setTop(1e9); await page.waitForTimeout(1500); await painted(); }
out.jumped = Object.assign(await reading(), { asks: await asks() });
// ROAD 3: five seconds at the end with nothing new: every write and ask the page makes in that window is its own
{ const w0 = await page.evaluate(() => window.__writes), a0 = (await asks()).length;
  await page.waitForTimeout(5000);
  out.idle = { writes: (await page.evaluate(() => window.__writes)) - w0, asks: (await asks()).slice(a0) }; }
if (cfg.legs.includes("middle")) {
  // ROAD 4: a scrollbar jump to the middle of the scroll range, inside the head gap: the reader stays there, ONE page is asked, the
  // one holding the turn under the viewport top, and its rows come on screen
  const a0 = (await asks()).length, shBefore = (await reading()).sh;
  const drop = Math.round(shBefore / 2);
  await setTop(drop); await painted();
  const at = await reading();
  await asksPast(a0, 10000);
  await page.waitForTimeout(3000); await painted();
  out.middle = { drop, shBefore, at, after: await reading(), asks: (await asks()).slice(a0) };
}
if (cfg.legs.includes("parked")) {
  // ROAD 5: a reader parked in a gap while a page they did not ask for is on the wire. Back to the end; the kernel's history replies are
  // held; the reader drops at a fifth of the range (its page P1 is asked and held), then moves further down the same gap (the in-flight
  // P1 keeps that gap from asking again). P1 is released: its fill lands while the reader's own turn is unloaded and they have not
  // moved, and the page holding their turn must be asked off that completion.
  await setTop(1e9); await page.waitForTimeout(1500); await painted();
  holdTurns = true;
  const a0 = (await asks()).length, sh = (await reading()).sh;
  await setTop(Math.round(sh * 0.2)); await painted();
  await asksPast(a0, 10000);
  await page.waitForTimeout(500);
  const first = await reading(), firstAsks = (await asks()).slice(a0);
  await setTop(Math.round(sh * 0.4)); await page.waitForTimeout(1000); await painted();
  const parked = await reading(), a1 = (await asks()).length, heldN = held.length;
  release();
  await asksPast(a1, 10000);
  const afterRelease = (await asks()).slice(a1);
  await page.waitForTimeout(3000); await painted();
  out.parked = { first, firstAsks, parked, heldN, afterRelease, after: await reading(), askedWhileParked: (await asks()).slice(a0 + firstAsks.length, a1) };
}
if (cfg.legs.includes("turn")) {
  // ROAD 6: a jump to an early turn (a card's deep link): the turn's row lands on screen
  const anchor = "11111111-2222-3333-4444-" + String(2 * cfg.jumpTurn).padStart(12, "0");
  const onScreen = (u) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(); const e = document.querySelector(`#content .turn[data-uuid="${u}"]`); if (!e) return false; const b = e.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top && b.top < cr.bottom; };
  await page.evaluate((f) => window.postMessage(f, "*"), { type: "focus", id: cfg.sid, anchor, anchorT: cfg.base + 2 * cfg.jumpTurn });
  await page.waitForFunction(onScreen, anchor, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1000);
  out.turn = { anchor, onScreen: await page.evaluate(onScreen, anchor) };
}
if (cfg.legs.includes("gapDrawn")) {
  // the head gap's drawn height: a jump to the head renders its element (the last leg: it asks the head page)
  await setTop(0); await page.waitForTimeout(1000); await painted();
  out.gapDrawn = await page.evaluate(() => { const g = Array.from(document.querySelectorAll("#content .tx-gap")).find((x) => x.offsetParent !== null); return g ? { lo: +g.dataset.lo, hi: +g.dataset.hi, h: g.offsetHeight } : null; });
}
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
"""


class _LongHistoryLab:
    """The lab, mixed into each TestCase below (a mixin, so the runner collects no lab of its own): a hermetic kernel over the
    synthetic transcript, the real /chat page served from a copy of the built bundle, driven at the class's SCALE and VIEWPORT with
    the class's LEGS."""
    maxDiff = None
    SCALE = 2
    VIEWPORT = {"width": 1000, "height": 600}
    TALL_LINES = 600      # lines in each tall reply's code block (~18 px each)
    LEGS = ()

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
        tall = "```\n" + "\n".join("n%d" % i for i in range(cls.TALL_LINES)) + "\n```"
        recs, prev = [], None
        for k in range(TURNS):
            u = "11111111-2222-3333-4444-%012d" % (2 * k)
            a = "22222222-3333-4444-5555-%012d" % (2 * k + 1)
            tu = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(BASE + 2 * k))
            ta = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(BASE + 2 * k + 1))
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

    @property
    def limit(self):
        return LAYOUT_LIMIT_1X // self.SCALE

    def _result(self):
        if type(self)._r is not None:
            return type(self)._r
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "newest": self.newest,
                       "scale": self.SCALE, "viewport": self.VIEWPORT, "legs": list(self.LEGS), "sid": SID, "base": BASE, "jumpTurn": JUMP_TURN}, f)
        driver = os.path.join(self.lab, "drive.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
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
        print("LONG-HISTORY:", type(self).__name__, json.dumps(type(self)._r), file=sys.stderr)
        return type(self)._r

    @staticmethod
    def _brief(x):
        return {k: x[k] for k in x if k != "regions"} if isinstance(x, dict) else x

    def _assert_settled_at_the_end(self):
        idle = self._result()["idle"]
        self.assertEqual(idle["writes"], 0, "five idle seconds at the end: the page wrote the scroll position %d times" % idle["writes"])
        self.assertEqual(idle["asks"], [], "…and asked for history pages: %r" % idle["asks"])

    def test_the_page_raised_no_error(self):
        self.assertEqual(self._result()["pageErrors"], [])


class LongHistoryHeight(_LongHistoryLab, unittest.TestCase):
    SCALE = 2
    TALL_LINES = 600      # a turn of ~11,000 px: the head gap at that figure would stand ~22 million px, past the 2x limit
    LEGS = ("middle", "parked", "turn")

    def test_the_lab_holds_a_gap_of_most_of_the_transcript_above_the_tail_run(self):
        rs = self._result()["landed"]["regions"] or []
        self.assertTrue(rs and rs[-1]["kind"] == "run" and rs[-1]["hi"] is None, "the tail run is last and open-ended: %r" % rs)
        widest = max((x["hi"] - x["lo"] for x in rs if x["kind"] == "gap"), default=0)
        self.assertGreater(widest, 1900, "a gap holds most of the transcript's turns (the estimate the lab is built to push past the limit): %r" % rs)

    def test_the_page_lands_with_the_newest_row_on_screen(self):
        L = self._result()["landed"]
        self.assertTrue(L["newestOnScreen"], "the newest row is on screen at the landing: %r" % self._brief(L))
        self.assertLessEqual(L["dist"], 2, "the landing is at the bottom: %r" % L["dist"])

    def test_a_jump_to_the_end_shows_the_newest_row(self):
        J = self._result()["jumped"]
        self.assertTrue(J["newestOnScreen"], "a jump to the end shows the newest row: %r" % self._brief(J))
        self.assertLessEqual(J["dist"], 2, "…at the bottom: %r" % J["dist"])

    def test_the_scroll_height_stays_under_the_layout_limit(self):
        r = self._result()
        for road in ("landed", "jumped"):
            self.assertLess(r[road]["sh"], self.limit, "%s: the thread fits the tallest box Chrome lays out at this scale" % road)

    def test_the_end_asks_for_no_history_page_and_sits_still(self):
        r = self._result()
        self.assertEqual(r["landed"]["asks"], [], "the gap is far above the landing, so no page is asked: %r" % r["landed"]["asks"])
        self.assertEqual(r["jumped"]["asks"], [], "…nor after a jump to the end: %r" % r["jumped"]["asks"])
        self._assert_settled_at_the_end()

    def test_a_jump_into_the_middle_of_the_history_stays_there_and_asks_the_page_under_it(self):
        m = self._result()["middle"]
        self.assertGreater(m["at"]["st"], m["shBefore"] * 0.25, "the reader stays where the jump dropped them, not at the head: %r" % self._brief(m["at"]))
        t = m["at"]["gap"]["turnAtTop"] if m["at"]["gap"] else None
        self.assertIsNotNone(t, "the drop lands inside the head gap: %r" % self._brief(m["at"]))
        self.assertEqual(len(m["asks"]), 1, "ONE page is asked for the drop: %r" % m["asks"])
        lo, hi = m["asks"][0]
        self.assertTrue(lo <= t < hi, "the page asked holds the turn under the viewport top (%.1f): %r" % (t, m["asks"]))
        self.assertGreater(m["after"]["rowsOnScreen"], 0, "the page's rows come on screen: %r" % self._brief(m["after"]))
        self.assertTrue(m["after"]["rowTurn"] is not None and lo <= m["after"]["rowTurn"] < hi, "…the rows of the turns asked: %r" % self._brief(m["after"]))

    def test_a_reader_parked_in_a_gap_gets_their_page_off_the_fill_of_another(self):
        p = self._result()["parked"]
        self.assertEqual(len(p["firstAsks"]), 1, "the first drop asks one page: %r" % p["firstAsks"])
        self.assertGreaterEqual(p["heldN"], 1, "…held at the wire")
        self.assertEqual(p["askedWhileParked"], [], "the gap with a page on the wire asks nothing more while the reader moves inside it")
        t = p["parked"]["gap"]["turnAtTop"] if p["parked"]["gap"] else None
        self.assertIsNotNone(t, "the reader is parked inside the gap: %r" % self._brief(p["parked"]))
        p1 = p["firstAsks"][0]
        self.assertFalse(p1[0] <= t < p1[1], "the parked turn is not in the held page (the leg's premise): %r %r" % (t, p1))
        self.assertEqual(len(p["afterRelease"]), 1, "the held page's fill asks ONE page off its own completion: %r" % p["afterRelease"])
        lo, hi = p["afterRelease"][0]
        self.assertTrue(lo <= t < hi, "…the one holding the parked reader's turn (%.1f): %r" % (t, p["afterRelease"]))
        self.assertTrue(p["after"]["rowTurn"] is not None and lo <= p["after"]["rowTurn"] < hi, "…whose rows come on screen: %r" % self._brief(p["after"]))

    def test_a_jump_to_an_early_turn_lands_on_its_row(self):
        self.assertTrue(self._result()["turn"]["onScreen"], "turn %d's row is on screen after the jump" % JUMP_TURN)


class UnreachableEndSettles(_LongHistoryLab, unittest.TestCase):
    SCALE = 12            # the layout limit is 2,796,202 px
    VIEWPORT = {"width": 640, "height": 480}   # a smaller page, so the 12x raster costs what a 1000 x 600 page does at 8x
    TALL_LINES = 1500     # a turn of ~27,000 px: the tail run alone (~3.4 million px) stands past the limit, whatever the gap
    LEGS = ("gapDrawn",)

    def test_the_end_is_out_of_reach_for_a_reason_that_is_not_the_gap(self):
        J = self._result()["jumped"]
        self.assertGreaterEqual(J["sh"], self.limit - 1, "the thread stands at the layout limit (the lab's premise): %r" % self._brief(J))
        self.assertFalse(J["newestOnScreen"], "…so the newest row is out of reach: %r" % self._brief(J))

    def test_the_gaps_are_drawn_at_most_a_quarter_of_the_measured_limit(self):
        g = self._result()["gapDrawn"]
        self.assertIsNotNone(g, "the head gap's element renders at the head")
        self.assertLessEqual(g["h"], self.limit / 4 + 1, "the head gap stands at most a quarter of the limit at this scale: %r" % g)

    def test_the_page_settles_at_the_unreachable_end(self):
        self._assert_settled_at_the_end()
        r = self._result()
        self.assertLessEqual(len(r["jumped"]["asks"]), 1, "no page walk at the end: %r" % r["jumped"]["asks"])


if __name__ == "__main__":
    unittest.main()
