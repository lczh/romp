"""A long session's chat lands at its newest row, stays still at its end, and a jump into its history stays where it was dropped
(2026-10-01). The head gap (T386 stage 2) is empty space of an estimated height, its turns times the px-per-turn the page measured off
the rows it holds, and a browser lays out no box taller than a fixed limit (33,554,432 px in Chromium at a 1x scale, divided by the
device scale and the zoom: 16,777,216 on a 2x display, 2,796,202 at 12x). A session long enough that the estimate passed the limit had
everything below it, the newest rows included, laid out AT the limit, out of reach: the bottom of the scroll range sat inside the gap,
the gap stayed in the viewport, and it asked for page after page while the reader waited at the end.

Served, on a hermetic kernel over a synthetic transcript built to cross the limit: about two thousand short turns of history, then a
tail of tall ones (each reply a long code block), so the page measures a tall turn off the tail and the head gap, at that figure, would
stand far past the limit. Three labs:

LongHistoryHeight, at a forced 2x scale (the reported browser's). The page must:
- land with the newest row on screen at the bottom, and a jump to the end must keep it there;
- keep its scroll height under the limit;
- sit still at the end: no page asked, no scroll written while the reader waits there;
- keep a scrollbar jump into the middle of the history where it was dropped (a gap is one display unit, and a re-window used to put
  its top at the viewport top: the reader was sent to the head), asking the page holding the turn under the viewport top, once (its
  next page may follow: below the layout limit a gap the page leaves on screen under its rows asks for it);
- fill the pane after a drop onto the last turn of a page: that page's rows fill the top of the pane, and the gap it leaves on screen
  under them asks for the next page by itself (it stood blank, with no loader and nothing asked, until the reader scrolled again);
- meet a parked reader's need once another page fills: a page the reader did not ask for lands while their turn is still unloaded
  and they have not moved, and the page holding their turn is asked then, once (its next page may follow, as for the middle drop);
- land a jump to an early turn on that turn's row;
- in the run that landing brought (rows far shorter than the average unit the tall replies set, so seventy of them above the reader
  stand inside the re-window's edge margin): re-window ONCE for a scroll there, not again on the echo of its own moving write;
  and keep a scrollbar drop into a gap hidden in the bottom spacer inside that gap, asking the page under the viewport top (the bottom
  spacer is walked by each unit's own estimate; at the average unit the drop mapped far past the gap and showed nothing).

UnreachableEndSettles, at a forced 12x scale (limit 2,796,202), with a tail taller than the limit on its own: the end is out of reach
for a reason that is not the gap. The page must settle there: no re-window on the echo of its own write (the loop that re-rendered the
view and crept the reader up by a few px, once per render, for as long as the tab stayed open), no page walk, no error; and the gaps
are drawn at most a quarter of the limit the page measured.

UnreachableEndInsideAGapSettles, the same fixture with the gaps' bound held past the limit from the boot (a served-lab hook): the end
lies inside a gap the layout clamps, and a fill's rebuilt gap there asks nothing on its first observation, so the page settles, not walks;
and a reader parked in a gap there while a page they did not ask for lands gets the page holding their turn asked once, and no other page.

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
PAGE_TURNS = 16       # the kernel's history page (kernel.py PAGE_TURNS)
FALLBACK_SHARE_PX = 16_777_216 // 4   # chat-regions.ts LAYOUT_LIMIT_FALLBACK times GAPS_SHARE: the bound UnreachableEndInsideAGapSettles holds the gaps at

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
// a class that holds the gaps' bound from the boot hands the figure to the page's lab hook (__rompGapsBound) the moment the page defines it,
// before its first render
if (cfg.gapsHeld) await page.addInitScript((px) => { let fn = null; Object.defineProperty(window, "__rompGapsBound", { configurable: true, get() { return fn; }, set(f) { fn = f; try { f(px); } catch (e) { /* not the hook */ } } }); }, cfg.gapsHeld);
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
// the visible px of the gap elements on screen, and whether each wears the loader (a page of it on the wire)
const blankPx = () => page.evaluate(() => {
  const c = document.getElementById("content"), cr = c.getBoundingClientRect(); let px = 0; const gaps = [];
  for (const g of Array.from(document.querySelectorAll("#content .tx-gap"))) { if (g.offsetParent === null) continue; const b = g.getBoundingClientRect(); const vis = Math.max(0, Math.min(b.bottom, cr.bottom) - Math.max(b.top, cr.top)); if (vis > 0) { px += vis; gaps.push({ lo: +g.dataset.lo, hi: +g.dataset.hi, top: Math.round(b.top - cr.top), vis: Math.round(vis), loading: g.classList.contains("tx-gap-loading") }); } }
  return { px: Math.round(px), gaps };
});
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
  // ROAD 4: a scrollbar jump to the middle of the scroll range, inside the head gap: the reader stays there, the page holding the turn
  // under the viewport top is asked once, and its rows come on screen (its next page follows when the drop sits in the page's last few
  // turns: below the layout limit, the gap the page leaves on screen under its rows asks for it)
  // (the drop's page reply is held until the premise is read: a fill landing first put rows under the top, and the premise read no gap)
  const a0 = (await asks()).length, shBefore = (await reading()).sh;
  const drop = Math.round(shBefore / 2);
  holdTurns = true;
  await setTop(drop); await painted();
  const at = await reading();
  await asksPast(a0, 10000);
  release();
  await page.waitForTimeout(3000); await painted();
  out.middle = { drop, shBefore, at, after: await reading(), asks: (await asks()).slice(a0) };
}
if (cfg.legs.includes("nearEnd")) {
  // ROAD 4b (2026-10-02): a drop inside the gap the middle drop's pages left below them, onto a turn just short of its page's end. That
  // page comes and its rows fill the top of the pane; the rest of the gap stands on screen under them. The layout is far under the limit,
  // so that gap asks for its next page by itself (a fill's rebuilt gaps are quiet only at the limit), and the pane fills; it used to stay
  // blank, with no loader and nothing asked, until the reader scrolled again.
  // That gap starts where the middle drop's LAST ask ends: its page's, or the next page's when the page left a gap on screen under its rows
  const P = cfg.pageTurns, m = out.middle && out.middle.asks.length ? out.middle.asks[out.middle.asks.length - 1] : null;
  const gaps = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(); return Array.from(document.querySelectorAll("#content .tx-gap")).filter((g) => g.offsetParent !== null).map((g) => { const b = g.getBoundingClientRect(); return { lo: +g.dataset.lo, hi: +g.dataset.hi, y: b.top - cr.top + c.scrollTop, h: b.height }; }); });
  const g = gaps.find((x) => m && x.lo === m[1]) || null;
  if (g) {
    const k = Math.floor((g.lo + g.hi) / 2 / P) * P, turn = k + P - 0.35;   // a page in the gap's middle, its last turn, two thirds of the way down
    const drop = Math.round(g.y + g.h * (turn - g.lo) / (g.hi - g.lo));
    const a0 = (await asks()).length;
    holdTurns = true;   // the page's reply waits at the wire until the premise is read (the fill would put the rows under the top first)
    await setTop(drop); await painted();
    const at = await reading();
    await asksPast(a0, 10000);
    release();
    await asksPast(a0 + 1, 8000);
    await page.waitForTimeout(1500); await painted();
    out.nearEnd = { gap: g, page: [k, k + P], turn, drop, at, after: await reading(), blank: await blankPx(), asks: (await asks()).slice(a0) };
  } else out.nearEnd = { gap: null, gaps, middleAsk: m };
}
if (cfg.legs.includes("parked")) {
  // ROAD 5: a reader parked in a gap while a page they did not ask for is on the wire. Back to the end; the kernel's history replies are
  // held; the reader drops at a fifth of the range (its page P1 is asked and held), then moves further down the same gap (the in-flight
  // P1 keeps that gap from asking again). P1 is released: its fill lands while the reader's own turn is unloaded and they have not
  // moved, and the page holding their turn must be asked then. The asks are read after a wait for that page's run to stand in the
  // regions and three seconds more, not in the frame the first ask is seen.
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
  const tp = parked.gap && parked.gap.turnAtTop != null ? Math.floor(parked.gap.turnAtTop) : null;
  if (tp != null) await page.waitForFunction((t) => ((typeof window.__rompRegions === "function" && window.__rompRegions()) || []).some((r) => r.kind === "run" && r.lo <= t && (r.hi == null || t < r.hi)), tp, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(3000); await painted();
  out.parked = { first, firstAsks, parked, heldN, afterRelease: (await asks()).slice(a1), after: await reading(), askedWhileParked: (await asks()).slice(a0 + firstAsks.length, a1) };
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
if (cfg.legs.includes("long")) {
  // ROAD 7 (2026-10-02), after the turn landing: its window put a run of about a hundred and twenty short turns around turn 600, rows far
  // shorter than the average unit the tall replies set, and the view renders seventy units each side of the reader, so the run's ends hide
  // in the spacers and the gap below the run in the bottom spacer.
  if (out.turn) await page.waitForFunction((u) => window.__sent.some((m) => m.type === "locateDiag" && m.anchor === u && m.ok === true), out.turn.anchor, { timeout: 8000 }).catch(() => {});   // the landing's settle has ended
  await page.waitForTimeout(500); await painted();
  const threadSel = '#content .thread[data-session="' + cfg.sid + '"]';
  const shape = () => page.evaluate((sel) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(); const t = document.querySelector(sel); const kids = t ? Array.from(t.children) : []; const units = kids.map((k) => k.dataset.unit).filter((u) => u != null).map(Number); const b = t && t.querySelector(".tx-spacer-bot");
    return { top: !!(t && t.querySelector(".tx-spacer-top")), first: units.length ? Math.min(...units) : null, last: units.length ? Math.max(...units) : null, gaps: kids.filter((k) => k.classList.contains("tx-gap")).map((k) => [+k.dataset.lo, +k.dataset.hi]),
             bTop: b ? b.getBoundingClientRect().top - cr.top + c.scrollTop : null, bH: b ? b.offsetHeight : null }; }, threadSel);
  out.long = { shape: await shape(), regions: (await reading()).regions };
  // (a) THE RE-WINDOW AND THE ECHO OF ITS OWN WRITE. Seventy short rows above the reader in the window are a band far shorter than the
  // re-window's edge margin (twenty average units), so every scroll here reads as near the window's top edge. One small scroll down
  // re-windows around the row now at the top, and that write moves the view (the rows that left the window are counted in the spacer at
  // the average unit). Its echo reads as near the edge too: it must not re-window again.
  await page.evaluate((sel) => { window.__renders = 0; const t = document.querySelector(sel); window.__renderMo = new MutationObserver((recs) => { if (recs.some((r) => r.type === "childList" && r.removedNodes.length > 0)) window.__renders++; }); if (t) window.__renderMo.observe(t, { childList: true }); }, threadSel);
  { const w0 = await page.evaluate(() => window.__writes), st0 = (await reading()).st, a0 = (await asks()).length;
    // the scroll's own position is read in the task that writes it: no scroll event, so no re-window, runs between them (read in a later
    // task, the re-window could already have moved the view, and the premise below compared its write with itself)
    const st1 = await page.evaluate((y) => { const c = document.getElementById("content"); c.scrollTop = y; return c.scrollTop; }, st0 + 120);
    await painted(); await painted(); await page.waitForTimeout(600); await painted();
    out.long.echo = { st0, st1, st2: (await reading()).st, renders: await page.evaluate(() => window.__renders), writes: (await page.evaluate(() => window.__writes)) - w0, asks: (await asks()).slice(a0), shape: await shape() };
    await page.evaluate(() => { if (window.__renderMo) window.__renderMo.disconnect(); }); }
  // (b) A DROP INTO A GAP HELD IN THE BOTTOM SPACER, a fifth of the way down the spacer: past the run's hidden rows, inside the gap below the
  // run. The bottom spacer is walked by each unit's own estimate (a gap's is up to about a million px here, an average unit thousands), so the drop stays
  // where it was dropped, inside that gap, and the page under the viewport top is asked and comes on screen.
  { const hidden = await shape();
    if (hidden.bTop != null) {
      const drop = Math.round(hidden.bTop + hidden.bH * 0.2), a0 = (await asks()).length;
      holdTurns = true;   // the page's reply waits at the wire until the premise is read
      await setTop(drop); await painted();
      const at = await reading();
      await asksPast(a0, 10000);
      release();
      await page.waitForTimeout(3000); await painted();
      out.long.drop = { hidden, drop, at, after: await reading(), asks: (await asks()).slice(a0),
        underTop: await page.evaluate((sel) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(); const t = document.querySelector(sel);   // the thread's first child on screen from the top
          const k = t ? Array.from(t.children).find((x) => { const b = x.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top + 1 && b.top < cr.bottom; }) : null;
          return k ? (k.classList.contains("turn") ? "row" : k.classList.contains("tx-gap") ? "gap" : k.classList.contains("tx-spacer") ? "spacer" : k.className) : null; }, threadSel) };
    } else out.long.drop = { hidden, noSpacer: true }; }
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
    GAPS_HELD = None      # px: the gaps' bound the page is held at from its boot (the served labs' hook), else the measured share

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
                       "scale": self.SCALE, "viewport": self.VIEWPORT, "legs": list(self.LEGS), "sid": SID, "base": BASE, "jumpTurn": JUMP_TURN,
                       "pageTurns": PAGE_TURNS, "gapsHeld": self.GAPS_HELD}, f)
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
    LEGS = ("middle", "nearEnd", "parked", "turn", "long")

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
        self.assertTrue(m["asks"], "a page was asked for the drop: %r" % m["asks"])
        lo, hi = m["asks"][0]
        self.assertTrue(lo <= t < hi, "the page asked first holds the turn under the viewport top (%.1f): %r" % (t, m["asks"]))
        # the page's next one may follow (a drop in the page's last few turns: below the layout limit, the gap the page leaves on screen
        # under its rows asks for it), but the page holding the turn is asked once, and no other page is asked
        self.assertEqual(sum(1 for a in m["asks"] if a[0] <= t < a[1]), 1, "ONE ask covers the turn under the viewport top (%.1f): %r" % (t, m["asks"]))
        self.assertIn(m["asks"][1:], ([], [[hi, hi + PAGE_TURNS]]), "any further ask is the next page, [%d, %d): %r" % (hi, hi + PAGE_TURNS, m["asks"]))
        self.assertGreater(m["after"]["rowsOnScreen"], 0, "the page's rows come on screen: %r" % self._brief(m["after"]))
        self.assertTrue(m["after"]["rowTurn"] is not None and lo <= m["after"]["rowTurn"] < hi, "…the rows of the turns asked: %r" % self._brief(m["after"]))

    def test_below_the_layout_limit_a_gap_a_fill_leaves_on_screen_under_its_page_asks_for_the_next_page(self):
        # the #68 follow-up (2026-10-02): below the layout limit a fill's rebuilt gap is not quiet, so the gap a short stretch of rows
        # leaves on screen asks for its next page on its first observation instead of standing blank until the reader scrolls again
        n = self._result()["nearEnd"]
        self.assertIsNotNone(n.get("gap"), "the middle drop's pages left a rendered gap below them to drop into (the leg's premise): %r" % n)
        k, hi = n["page"]
        t = n["at"]["gap"]["turnAtTop"] if n["at"]["gap"] else None
        self.assertTrue(t is not None and hi - 1 <= t < hi, "the drop put the last turn of page %r under the viewport top: %r" % (n["page"], self._brief(n["at"])))
        self.assertTrue(n["asks"] and n["asks"][0] == [k, hi], "the drop asked the page holding that turn first: %r" % n["asks"])
        self.assertEqual(n["asks"], [[k, hi], [hi, hi + PAGE_TURNS]], "the gap the page left on screen under its rows asked for the next page, and the pane filled (no third ask): %r (blank %r)" % (n["asks"], n["blank"]))
        self.assertEqual(n["blank"]["px"], 0, "no gap stands on screen once the next page came: %r" % n["blank"])
        self.assertGreater(n["after"]["rowsOnScreen"], 0, "the rows fill the pane: %r" % self._brief(n["after"]))

    def test_a_reader_parked_in_a_gap_gets_their_page_off_the_fill_of_another(self):
        p = self._result()["parked"]
        self.assertEqual(len(p["firstAsks"]), 1, "the first drop asks one page: %r" % p["firstAsks"])
        self.assertGreaterEqual(p["heldN"], 1, "…held at the wire")
        self.assertEqual(p["askedWhileParked"], [], "the gap with a page on the wire asks nothing more while the reader moves inside it")
        t = p["parked"]["gap"]["turnAtTop"] if p["parked"]["gap"] else None
        self.assertIsNotNone(t, "the reader is parked inside the gap: %r" % self._brief(p["parked"]))
        p1 = p["firstAsks"][0]
        self.assertFalse(p1[0] <= t < p1[1], "the parked turn is not in the held page (the leg's premise): %r %r" % (t, p1))
        a = p["afterRelease"]
        self.assertTrue(a, "a page was asked after the held page's fill: %r" % a)
        lo, hi = a[0]
        self.assertTrue(lo <= t < hi, "…first the one holding the parked reader's turn (%.1f): %r" % (t, a))
        # that page's next one may follow, as for the middle drop, but the page holding the turn is asked once, and no other page is asked
        self.assertEqual(sum(1 for x in a if x[0] <= t < x[1]), 1, "ONE ask covers the parked reader's turn (%.1f): %r" % (t, a))
        self.assertIn(a[1:], ([], [[hi, hi + PAGE_TURNS]]), "any further ask is the next page, [%d, %d): %r" % (hi, hi + PAGE_TURNS, a))
        self.assertTrue(p["after"]["rowTurn"] is not None and lo <= p["after"]["rowTurn"] < hi, "…whose rows come on screen: %r" % self._brief(p["after"]))

    def test_a_jump_to_an_early_turn_lands_on_its_row(self):
        self.assertTrue(self._result()["turn"]["onScreen"], "turn %d's row is on screen after the jump" % JUMP_TURN)

    def _long(self):
        L = self._result()["long"]
        rs = L["regions"] or []
        run = next((r for r in rs if r["kind"] == "run" and r["lo"] <= JUMP_TURN and r["hi"] is not None and JUMP_TURN < r["hi"]), None)
        self.assertTrue(run and run["n"] > 2 * 70 + 2, "the leg's premise: the turn landing's run holds more rows than a window: %r" % run)
        self.assertTrue(L["shape"]["top"] and L["shape"]["bTop"] is not None, "…both spacers stand: %r" % L["shape"])
        self.assertEqual(L["shape"]["gaps"], [], "…and the window renders no gap, so it starts and ends inside the run: %r" % L["shape"])
        return L, run

    def test_a_re_window_does_not_run_again_on_the_echo_of_its_own_write(self):
        # PR 68's own-echo skip, executed (2026-10-02): seventy short rows above the reader in the window stand far inside the edge margin
        # (twenty units at an average the tall rows set), so every scroll there reads as near the top edge; the re-window's moving write
        # owes an echo that reads the same, and re-windowing on it is the loop the skip ends (one extra render here; at a clamped end it ran
        # for as long as the tab stayed open)
        L, _ = self._long()
        e = L["echo"]
        self.assertNotEqual(e["st2"], e["st1"], "the re-window's write moved the view from where the scroll put it (st1, read in the scroll's own task), so its echo came (the leg's premise): %r" % e)
        self.assertEqual(e["asks"], [], "no page was asked, so every render in the window was a re-window's: %r" % e)
        self.assertEqual(e["renders"], 1, "ONE re-window render for the scroll: the echo of its own write did not re-window again: %r" % e)
        self.assertEqual(e["writes"], 2, "…the scroll and the re-window's one write: %r" % e)

    def test_a_drop_into_a_gap_hidden_in_the_bottom_spacer_stays_there_and_asks_the_page_under_it(self):
        # the bottom spacer walked by each unit's own estimate (#68), executed (2026-10-02): walked at the average unit instead, the drop maps
        # to a unit far below the gap and the re-window shows no row and asks nothing
        L, run = self._long()
        b = L.get("drop")
        self.assertTrue(b and not b.get("noSpacer"), "the drop ran, into a bottom spacer: %r" % b)
        g = b["at"]["gap"]
        t = g["turnAtTop"] if g else None
        self.assertIsNotNone(t, "the drop stays where it was dropped, inside a gap: %r" % self._brief(b["at"]))
        self.assertTrue(g["lo"] >= run["hi"] and [g["lo"], g["hi"]] not in b["hidden"]["gaps"], "…a gap below the run that was hidden in the bottom spacer at the drop: %r (rendered then: %r)" % (g, b["hidden"]["gaps"]))
        self.assertTrue(b["asks"], "a page was asked for the drop: %r" % b["asks"])
        plo, phi = b["asks"][0]
        self.assertTrue(plo <= t < phi, "…the one holding the turn under the viewport top (%.1f): %r" % (t, b["asks"]))
        # the page's next one may follow (a gap the fill leaves on screen under its rows asks for its next page below the layout limit),
        # but the page holding the turn is asked once, and no other page is asked
        self.assertEqual(sum(1 for a in b["asks"] if a[0] <= t < a[1]), 1, "ONE ask covers the turn under the viewport top (%.1f): %r" % (t, b["asks"]))
        self.assertIn(b["asks"][1:], ([], [[phi, phi + PAGE_TURNS]]), "any further ask is the next page, [%d, %d): %r" % (phi, phi + PAGE_TURNS, b["asks"]))
        self.assertGreater(b["after"]["rowsOnScreen"], 0, "its rows come on screen: %r" % self._brief(b["after"]))
        self.assertEqual(b["underTop"], "row", "…a row under the viewport top, not a spacer or a gap: %r" % self._brief(b["after"]))
        self.assertTrue(b["after"]["rowTurn"] is not None and plo <= b["after"]["rowTurn"] < phi, "…of the turns asked: %r" % self._brief(b["after"]))


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

class UnreachableEndInsideAGapSettles(_LongHistoryLab, unittest.TestCase):
    """The case a fill's quiet gaps exist for (2026-10-02): UnreachableEndSettles' fixture with the gaps' bound held, from the boot, at the
    fallback's share (a quarter of 16,777,216, 4,194,304 px), past this scale's whole limit, so the end lies inside a gap the layout
    clamps, a gap that cannot leave the viewport. Every fill there rebuilds that gap under the reader; were its first observation to ask,
    the page would walk the gap one page per reply for as long as pages came. Below the limit a fill's gaps are not quiet
    (LongHistoryHeight's near-end drop asks for the next page). With them quiet, the fill's completion (fillInPlace) asks the page
    holding a parked reader's turn, so the parked leg runs here too."""
    SCALE = 12
    VIEWPORT = {"width": 640, "height": 480}
    TALL_LINES = 1500
    LEGS = ("parked",)
    GAPS_HELD = FALLBACK_SHARE_PX

    def _assert_the_end_lies_inside_a_gap(self):
        J = self._result()["jumped"]
        self.assertGreaterEqual(J["sh"], self.limit - 1, "the thread stands at the layout limit: %r" % self._brief(J))
        self.assertIsNotNone(J["gap"], "…and the end lies inside a gap (the class's premise: the held bound took): %r" % self._brief(J))

    def test_the_end_lies_inside_a_gap_at_the_layout_limit(self):
        self._assert_the_end_lies_inside_a_gap()

    def test_the_page_settles_inside_the_gap_and_does_not_walk_it(self):
        self._assert_the_end_lies_inside_a_gap()   # with no gap at the end the page settles anyway: the settle means nothing without it
        self._assert_settled_at_the_end()
        r = self._result()
        self.assertLessEqual(len(r["jumped"]["asks"]), 2, "no page walk on the jumps to the end: %r" % r["jumped"]["asks"])

    def test_a_reader_parked_in_a_gap_at_the_limit_gets_their_page_off_the_fill_of_another(self):
        p = self._result()["parked"]
        self.assertEqual(len(p["firstAsks"]), 1, "the first drop asks one page: %r" % p["firstAsks"])
        self.assertGreaterEqual(p["heldN"], 1, "…held at the wire")
        self.assertEqual(p["askedWhileParked"], [], "the gap with a page on the wire asks nothing more while the reader moves inside it")
        self.assertGreaterEqual(p["parked"]["sh"], self.limit - 1, "the layout stands at the limit while the reader is parked (the leg's premise): %r" % self._brief(p["parked"]))
        t = p["parked"]["gap"]["turnAtTop"] if p["parked"]["gap"] else None
        self.assertIsNotNone(t, "the reader is parked inside a gap: %r" % self._brief(p["parked"]))
        p1 = p["firstAsks"][0]
        self.assertFalse(p1[0] <= t < p1[1], "the parked turn is not in the held page (the leg's premise): %r %r" % (t, p1))
        a = p["afterRelease"]
        self.assertTrue(a, "a page was asked after the held page's fill: %r" % a)
        lo, hi = a[0]
        self.assertTrue(lo <= t < hi, "…the one holding the parked reader's turn (%.1f): %r" % (t, a))
        self.assertEqual(len(a), 1, "that page alone, once: %r" % a)
        self.assertTrue(p["after"]["rowTurn"] is not None and lo <= p["after"]["rowTurn"] < hi, "…whose rows come on screen: %r" % self._brief(p["after"]))

if __name__ == "__main__":
    unittest.main()
