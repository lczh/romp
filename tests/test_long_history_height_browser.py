"""A long session's chat lands at its newest row, stays still at its end, and a jump into its history stays where it was dropped
(2026-10-01). The head gap (T386 stage 2) is empty space of an estimated height, its turns times the px-per-turn the page measured off
the rows it holds, and a browser lays out no box taller than a fixed limit (33,554,432 px in Chromium at a 1x scale, divided by the
device scale and the zoom: 16,777,216 on a 2x display, 2,796,202 at 12x). A session long enough that the estimate passed the limit had
everything below it, the newest rows included, laid out AT the limit, out of reach: the bottom of the scroll range sat inside the gap,
the gap stayed in the viewport, and it asked for page after page while the reader waited at the end.

Served, on a hermetic kernel over a synthetic transcript built to cross the limit: about two thousand short turns of history, then a
tail of tall ones (each reply a long code block), so the page measures a tall turn off the tail and the head gap, at that figure, would
stand far past the limit. The labs:

LongHistoryHeight, at a forced 2x scale (the reported browser's). The page must:
- land with the newest row on screen at the bottom, and a jump to the end must keep it there;
- keep its scroll height under the limit;
- at the landing, estimate a row hidden in a spacer at the average unit it measured, the units it holds standing at that average
  under a fifth of the limit;
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
Its premise is read off the page: a box asked for 20,000,000 px lays out at most the 2x limit, and the head gap at the px per turn the
page measured would stand past that limit by more than a fifth.

LongHistoryClimb, at 2x over a taller tail: a reader drops into the head gap and climbs back through the history with the wheel for
CLIMB_PAGES pages, one page asked per step, by which point the rows the page holds, at the average unit it measured off the tail, would
stand past the limit by more than a fifth. The scroll height must stay under the limit at every page, the estimates in it (the spacers
and any gap drawn) at most half of it; the go-to-bottom chip's first press must bring the newest row on screen at the bottom, and its
second leave the reader there. Then the reader goes back far into the history the climb loaded (one scroll write, END_BACK_UNITS units
under its top), clicks a reply's text and presses End, which must bring the newest row on screen at the bottom too (the window a press
builds dresses its rows after the press lands, and those rows grew under a reader left 25 px short of the bottom; 2026-10-15). Then
replies land below the window while the reader stands in the history (the residual chat-regions.ts HIDDEN_SHARE discloses): the reader
drops back to the tail run's first rows, under the gap above them, one reply lands, then APPEND_TURNS more in one write, which the keyed
paint puts in the bottom spacer at the held figure as it stands. The
estimates must grow by that write's unit count (two units a turn) times that figure, within 2 px, and pass the half by at most that,
and the next window build (a press of the chip) must bring them back under it. Then the reader goes back into the climbed run as for
End, and one more reply lands below the window, taller than the held figure, with its turn left open, so it takes the place of the
error card the lab's session ends on (the card stands only while no turn is open) as the newest unit; the chip's press must bring that
reply on screen at the bottom (the window the press builds anchored the reader in the newest unit at their offset into its held
estimate, which left them tens of thousands of px inside a reply taller than the figure; 2026-10-15). Before any reply lands, the
window moves while the unit
count holds (the climb's last page, the chip's second press, ROAD 9's drop), and the held figure must not move with it. Each test that reads
the climb checks its premise first, so a short climb fails every one of them on its own. A failed run is not re-run over the transcript
ROAD 9 grew, and when the driver fails after printing the climb's record (before ROAD 9), the tests that read the climb alone read that
record.

LongHistoryBuiltHidden, at 2x beside a second, short session. When the page lands on that session (the first-show test checks it first), the
idle prebuild builds the long session's view while it is hidden, where nothing can be measured. Its first show keeps that window, and
the figure a row hidden in a spacer takes must then be the average unit measured there. A settings change while the reader is on the
short session rebuilds the long view hidden again (its average unit forgotten, its px per turn kept), and at the switch back a hidden
row must stand at the average measured then.

LongHistoryBuiltHiddenBounded, the same two sessions over LongHistoryClimb's taller tail, where the long session's rows at the average
unit measured at its first show would stand past the room the gaps leave in half the limit. A scroll above the window after that show
builds a window over the same units, and a hidden row must take, at the show, the figure that build sets.

LongHistoryToggleKeepsPlace, at 2x over a tail whose every turn opens its reply with a tool run, tall enough that hidden rows take the
held figure (ROAD 12, 2026-10-21): the reader drops into the tail run, its lower rows rendered and the units above them hidden in the top
spacer, and puts a collapsed tool run's row 200 px under the viewport top; one reply lands, the reader expands the run; three more land,
the reader collapses it. Each toggle must leave the row where it was clicked, within 2 px, and the held figure and the top spacer where
they stood (the toggle's rebuild recomputed the figure over the landed replies, which lowered it, and the top spacer shrank under the
reader). LongHistoryToggleKeepsPlaceUnbounded runs the same road over a shorter tail, where hidden rows take the average unit, which a
new unit does not move.

UnreachableEndSettles, at a forced 12x scale (limit 2,796,202), with replies tall enough that the thread stands at the limit and its
newest row is out of reach. The page must settle there: no re-window on the echo of its own write (the loop that re-rendered the view
and crept the reader up by a few px, once per render, for as long as the tab stayed open), no page walk, no error; the gaps are drawn
at most a quarter of the limit the page measured, and the estimates in the scroll height (the spacers and any gap drawn) at most half
of it.

Every check that the estimates in the scroll height stand at most a bound allows 4 px past it.

UnreachableEndInsideAGapSettles, the same fixture at TALL_LINES 1500 (UnreachableEndSettles' is 3000) with the gaps' bound held past the limit from the boot (a served-lab hook): the end
lies inside a gap the layout clamps, and a fill's rebuilt gap there asks nothing on its first observation, so the page settles, not walks;
and a reader parked in a gap there while a page they did not ask for lands gets the page holding their turn asked once, and no other page.

The End classes (End pressed from far back after a wheel climb) are in tests/test_long_history_end_browser.py, one kernel and one
page for each fixture family; they import this lab, and ROAD 11 in its driver is their road.

LongHistoryHeight and the two classes at 12x read the landing, the jump to the end and the idle window at the end (ROADs 1 to 3).
The other classes read none of those and wait instead on the event the first road stands for: the first measure (the view's px per
turn set, no gap page on the wire), or, for the two built-hidden classes, the idle prebuild of the long session's view while it is
hidden.

Synthetic fixtures only (placeholder uuids, invented prose).
"""
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
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
SID2 = "cccccccc-1111-2222-3333-666666666666"   # LongHistoryBuiltHidden's second session, "api": short
SHORT_TURNS = 15      # its turns
_BUILD = []   # the bundle's build (node esbuild.js), run once per module: the first lab to boot runs it, every lab copies its dist

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
const variants = cfg.variants || [];   // ROAD 11: the End classes of one fixture family, each staged in turn on this page
let browser;
try { browser = await chromium.launch({ args: ["--force-device-scale-factor=" + cfg.scale], ...(cfg.bars ? { ignoreDefaultArgs: ["--hide-scrollbars"] } : {}) }); }   // the scale divides the layout limit; cfg.bars: classic scrollbars
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
  // the thread's scroll events, counted, and the frame callbacks requested while one is dispatched that have not run yet: the page's
  // re-window (render.ts virtualizeToViewport) runs in a frame callback its scroll listener requests, so a scroll write's re-window
  // is done once that callback has run (the driver's settlePage reads both)
  window.__labScrolls = 0;
  document.addEventListener("scroll", (e) => { if (e.target && e.target.id === "content") window.__labScrolls++; }, true);
  const raf = window.requestAnimationFrame, caf = window.cancelAnimationFrame, waiting = new Map();
  window.__labScrollRafs = () => waiting.size;
  window.requestAnimationFrame = function (cb) {
    const e = window.event;
    if (!(e && e.type === "scroll" && e.target && e.target.id === "content") || typeof cb !== "function") return raf.call(window, cb);
    const tok = {}, id = raf.call(window, (t) => { waiting.delete(tok); return cb(t); });
    waiting.set(tok, id); return id;
  };
  window.cancelAnimationFrame = function (id) { for (const [k, v] of waiting) if (v === id) { waiting.delete(k); break; } return caf.call(window, id); };
  // a frame with neither since the last reading (q.s: the count then)
  window.__labQuiet = (q) => { const s = window.__labScrolls, quiet = s === q.s && waiting.size === 0; q.s = s; return quiet; };
});
// ROAD 11 with a reply's element the End press meets (a variant's endAt): a live range at each pointerdown's target, and at the press,
// before the page's own listener, where focus, the selection's anchor and that range sit against the element (window.__atEnd stays
// null when the key never reaches the window; each variant sets it null before its staging)
if (variants.some((v) => v.endAt)) await page.addInitScript(() => {
  window.__endRange = null; window.__atEnd = null;
  // the element a variant's staging reaches (stageEnd), held by keys the page renders: its row's data-uuid and its place among the
  // selector's matches in that row (in #content, for an element in no row). The page can re-window between any two of the driver's
  // steps (one frame after a scroll event near the window's edge), emptying the thread and building fresh rows, so a mark set on the
  // element in one step was gone in the next. Each step finds the element by its keys and acts on it in the same task (__labEndAt);
  // node is the element the staging's action that End reads (its focus, the focus on the link before it, its press, found after the
  // click) acted on, and a later step that finds another element there fails, naming the class and both steps
  window.__labEnd = null;
  window.__labEndAt = (step, keep) => {
    const k = window.__labEnd, c = document.getElementById("content");
    if (!k) throw new Error("no END_AT element was picked before " + step);
    const scope = k.uuid == null ? c : c.querySelector('.turn[data-uuid="' + k.uuid + '"]');
    const t = (scope && scope.querySelectorAll(k.sel)[k.i]) || null, where = k.sel + " (match " + k.i + (k.uuid == null ? " in #content)" : " in row " + k.uuid + ")");
    if (!t) throw new Error(k.name + ": the END_AT element " + where + " is gone at " + step + ": no rendered row holds it");
    if (k.node && t !== k.node) throw new Error(k.name + ": the END_AT element " + where + " was rebuilt by a re-window between " + k.step + " and " + step + ", so what " + k.step + " left on it is gone");
    if (keep && !k.node) { k.node = t; k.step = step; }
    return t;
  };
  // the clip state a staging puts on its element (want, the variant's at.media), read on the element its keys find: "loaded", its
  // metadata in, at 0 s and half volume; "ended", seeked to its end at half volume and ended; "failed", its error set; anything else,
  // nothing. The first reading that finds an element's metadata in seeks it (once per element), so an element a re-window rebuilt
  // before the staging's action is staged again
  const seeked = new WeakSet();
  window.__labMedia = (t, want) => {
    if (want === "failed") return !!t.error;
    if (want !== "loaded" && want !== "ended") return true;
    if (!(t.readyState >= 1)) return false;
    if (!seeked.has(t)) { seeked.add(t); t.volume = 0.5; t.currentTime = want === "ended" ? t.duration : 0; }
    return want === "ended" ? t.ended : true;
  };
  window.addEventListener("pointerdown", (e) => { window.__endRange = null; if (e.target instanceof Node) { window.__endRange = document.createRange(); window.__endRange.setStart(e.target, 0); } }, true);
  window.addEventListener("keydown", (e) => {
    if (e.key !== "End") return;
    const t = window.__labEnd && window.__labEnd.node, c = document.getElementById("content"), a = document.getSelection().anchorNode, r = window.__endRange, f = document.activeElement;
    window.__atEnd = { inContent: !!t && c.contains(t), focused: !!t && f === t, bodyFocused: f === document.body, anchor: !!a, pressIn: !!t && !!r && t.contains(r.startContainer),
                       pressInContent: !!r && c.contains(r.startContainer),
                       dist: c.scrollHeight - c.scrollTop - c.clientHeight, ch: c.clientHeight,
                       media: t instanceof HTMLMediaElement ? { readyState: t.readyState, duration: t.duration, ended: t.ended, networkState: t.networkState, error: t.error ? t.error.code : null, controls: t.controls } : null };
  }, true);
});
// ROAD 11: the page's long tasks (the Long Tasks API: over 50 ms), each [start, end] in the page's clock, the latest end, and the time
// of each End's keydown: the wait before a variant's End reads the latest end (quiet), and the variant records the tasks around its press
if (variants.length) await page.addInitScript(() => {
  window.__labLong = []; window.__labLongEnd = null; window.__labPressT = null;
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) { const end = e.startTime + e.duration; window.__labLong.push([e.startTime, end]); window.__labLongEnd = Math.max(window.__labLongEnd ?? end, end); } }).observe({ type: "longtask", buffered: true }); }
  catch (e) { /* no Long Tasks API: the wait reads no task */ }
  window.addEventListener("keydown", (e) => { if (e.key === "End") window.__labPressT = performance.now(); }, true);
});
// a reply's audio clips (cfg.endMedia: name to file), served at media.test with byte ranges
if (cfg.endMedia) await page.route("https://media.test/**", async (route) => {
  const f = cfg.endMedia[route.request().url().split("/").pop()];
  if (!f) return route.fulfill({ status: 404, body: "" });
  const body = fs.readFileSync(f), range = route.request().headers()["range"], m = range && /bytes=(\d+)-(\d*)/.exec(range);
  if (!m) return route.fulfill({ status: 200, body, headers: { "content-type": "audio/wav", "accept-ranges": "bytes" } });
  const a = +m[1], b = m[2] ? Math.min(+m[2], body.length - 1) : body.length - 1;
  return route.fulfill({ status: 206, body: body.subarray(a, b + 1), headers: { "content-type": "audio/wav", "accept-ranges": "bytes", "content-range": "bytes " + a + "-" + b + "/" + body.length } });
});
// the kernel's history replies, held at the wire on demand: the page's ask is answered when the driver says
let holdTurns = false; const held = [];
// ROAD 11's question card is the lab's own askLive frame, which the kernel knows nothing of: from its dispatch on, the kernel's
// askLiveClear for the session (sent again with each once-a-minute repost of unchanged frames) is dropped at the wire, and counted
let dropAskClear = false, askClearsDropped = 0;
const isAskClear = (m) => { if (typeof m !== "string" || !/^\{"type": ?"askLiveClear"/.test(m)) return false; try { return JSON.parse(m).id === cfg.sid; } catch (e) { return false; } };
// ROAD 10's classes (cfg.boot "hidden") stand on the page landing on the short session cfg.sid2, and render.ts adopts whichever
// session's full frame reaches the page first, an order the kernel's push timing decides: a full for the long session that comes
// first waits at the wire, with every frame behind it, until the short session's full has passed
let landHold = cfg.boot === "hidden" ? [] : null;
const sessionOf = (m) => { const k = typeof m === "string" && /^\{"type": ?"session"/.test(m) ? /"id": ?"([^"]+)"/.exec(m) : null; return k ? k[1] : null; };
await page.routeWebSocket((u) => /[?&]app=chat(?:&|$)/.test(u.href), (ws) => {
  const server = ws.connectToServer();
  ws.onMessage((m) => server.send(m));
  const deliver = (m) => { if (dropAskClear && isAskClear(m)) askClearsDropped++; else if (holdTurns && typeof m === "string" && /^\{"type": ?"chatTurns"/.test(m)) held.push([ws, m]); else ws.send(m); };
  server.onMessage((m) => {
    if (landHold) {
      const id = sessionOf(m);
      if (id === cfg.sid2) { const q = landHold; landHold = null; deliver(m); q.forEach(deliver); return; }
      if (id === cfg.sid || landHold.length) { landHold.push(m); return; }
    }
    deliver(m);
  });
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
// the page settled after a scroll write, before a step that picks an element or stages what End reads: two frames running, each with no
// scroll event of the thread since the frame before and no frame callback one requested still waiting (the write's re-window, and its
// own write's echo, have run), read after each frame (requestAnimationFrame), capped at 10 s; on the cap the step fails, naming what
// it waited for and its last reading; settleRead returns the reading instead (window.__labQuiet is the frame's test, for a wait that
// reads it with conditions of its own)
const settleRead = () => page.evaluate((cap) => new Promise((resolve) => {
  const t0 = performance.now(), q = { s: window.__labScrolls }; let n = 0;
  const tick = () => {
    n = window.__labQuiet(q) ? n + 1 : 0;
    const ms = Math.round(performance.now() - t0);
    if (n >= 2) resolve({ met: true, ms }); else if (ms >= cap) resolve({ met: false, ms, scrolls: q.s, rafs: window.__labScrollRafs() }); else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}), 10000);
const settlePage = async (what) => {
  const r = await settleRead();
  if (!r.met) throw new Error(what + ": the page did not settle within 10 s (a scroll event of the thread, or a frame callback one requested, in every frame): " + JSON.stringify(r));
  return r;
};
// the page's view: the newest row's place, the scroll extent, the gap under the viewport top (its span, its drawn height and the turn
// under the top by its fraction), the first turn row on screen and the turn it names. The newest row is the fixture's last reply as
// written at setup (cfg.newest) unless the caller names another: ROAD 9 names the last reply it appended for the chip press after
// both its landings
const reading = (uuid = cfg.newest) => page.evaluate((newest) => {
  const c = document.getElementById("content"), cr = c.getBoundingClientRect();
  const row = document.querySelector(`#content .turn[data-uuid="${newest}"]`);
  const r = row ? row.getBoundingClientRect() : null;
  const g = Array.from(document.querySelectorAll("#content .tx-gap")).find((x) => { if (x.offsetParent === null) return false; const b = x.getBoundingClientRect(); return b.bottom > cr.top && b.top < cr.bottom; });
  const gr = g ? g.getBoundingClientRect() : null;
  const rows = Array.from(c.querySelectorAll(".turn[data-uuid]")).filter((e) => { if (e.offsetParent === null) return false; const b = e.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top && b.top < cr.bottom; });
  // the estimates in the scroll height: the spacers and every gap drawn (the rest is rows at their real height)
  const est = Array.from(c.querySelectorAll(".tx-spacer, .tx-gap")).filter((e) => e.offsetParent !== null).reduce((t, e) => t + e.offsetHeight, 0);
  return { sh: c.scrollHeight, st: c.scrollTop, ch: c.clientHeight, dist: c.scrollHeight - c.scrollTop - c.clientHeight, est,
           newestRendered: !!row, newestOnScreen: !!r && r.bottom > cr.top + 1 && r.top < cr.bottom - 1,
           gap: g ? { lo: +g.dataset.lo, hi: +g.dataset.hi, h: gr.height, turnAtTop: gr.top <= cr.top && gr.bottom > cr.top ? +g.dataset.lo + (+g.dataset.hi - +g.dataset.lo) * ((cr.top - gr.top) / gr.height) : null } : null,
           rowsOnScreen: rows.length, rowTurn: rows.length && rows[0].dataset.turn ? +rows[0].dataset.turn : null,
           regions: typeof window.__rompRegions === "function" ? window.__rompRegions() : null };
}, uuid);
// the visible px of the gap elements on screen, and whether each wears the loader (a page of it on the wire)
const blankPx = () => page.evaluate(() => {
  const c = document.getElementById("content"), cr = c.getBoundingClientRect(); let px = 0; const gaps = [];
  for (const g of Array.from(document.querySelectorAll("#content .tx-gap"))) { if (g.offsetParent === null) continue; const b = g.getBoundingClientRect(); const vis = Math.max(0, Math.min(b.bottom, cr.bottom) - Math.max(b.top, cr.top)); if (vis > 0) { px += vis; gaps.push({ lo: +g.dataset.lo, hi: +g.dataset.hi, top: Math.round(b.top - cr.top), vis: Math.round(vis), loading: g.classList.contains("tx-gap-loading") }); } }
  return { px: Math.round(px), gaps };
});
// the active view's measures, read through the page's hook (render.ts __rompViewMeasure)
const measure = () => page.evaluate(() => typeof window.__rompViewMeasure === "function" ? window.__rompViewMeasure() : null);
// the tallest box this launch lays out: a box asked for 20,000,000 px reads what the layout clamps it to (the scale flag took)
const limitProbe = await page.evaluate(() => { const d = document.createElement("div"); d.style.cssText = "position:absolute;left:0;top:0;width:1px;height:20000000px;visibility:hidden"; document.body.appendChild(d); const h = d.getBoundingClientRect().height; d.remove(); return h; });
const out = { pageErrors, scale: cfg.scale, limitProbe };
if (cfg.boot === "roads") {
  // ROAD 1: the boot lands at the end; let the first measure re-size the gap and any ask it provokes go out (a page reply would land too)
  await painted(); await page.waitForTimeout(2500); await painted();
  out.landed = Object.assign(await reading(), { asks: await asks(), measure: await measure() });
  // ROAD 2: the reader jumps to the end, twice (the second after whatever the first provoked has settled), and waits there
  for (let i = 0; i < 2; i++) { await setTop(1e9); await page.waitForTimeout(1500); await painted(); }
  out.jumped = Object.assign(await reading(), { asks: await asks() });
  // ROAD 3: five seconds at the end with nothing new: every write and ask the page makes in that window is its own
  { const w0 = await page.evaluate(() => window.__writes), a0 = (await asks()).length;
    await page.waitForTimeout(5000);
    out.idle = { writes: (await page.evaluate(() => window.__writes)) - w0, asks: (await asks()).slice(a0) }; }
} else {
  // a class that reads none of ROADs 1 to 3 waits instead on the event the first road stands for, read on the page each frame: the
  // first measure (cfg.boot "measure": the active view's px per turn set, no gap page on the wire), or ROAD 10's idle prebuild of the
  // long session's view while it is hidden (cfg.boot "hidden": its thread at display none, with children). On the cap the driver
  // fails, naming the event and its last reading
  const [what, ready] = cfg.boot === "hidden"
    ? ["the idle prebuild of the long session's view while it is hidden (its thread at display none, with children)", (sid) => {
        const t = document.querySelector(`#content .thread[data-session="${sid}"]`);
        window.__labBoot = t ? { display: t.style.display, children: t.children.length } : { thread: null };
        return !!t && t.style.display === "none" && t.children.length > 0; }]
    : ["the first measure (the active view's px per turn set, no gap page on the wire)", () => {
        const m = typeof window.__rompViewMeasure === "function" ? window.__rompViewMeasure() : null;
        const a = typeof window.__rompAskState === "function" ? window.__rompAskState() : null;
        window.__labBoot = { pxPerTurn: m ? m.pxPerTurn : null, gapLoading: a ? a.gapLoading : null };
        return !!m && m.pxPerTurn != null && !!a && a.gapLoading === 0; }];
  const met = await page.waitForFunction(ready, cfg.sid, { timeout: 20000, polling: "raf" }).then(() => true, () => false);
  if (!met) throw new Error("the boot's wait for " + what + " ended at its 20 s cap: last reading " + JSON.stringify(await page.evaluate(() => window.__labBoot)));
  await painted();
}
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
  // the gap is read and the drop written in one task: a re-window in a task between them could move the gap first
  const a0 = (await asks()).length;
  holdTurns = true;   // the page's reply waits at the wire until the premise is read (the fill would put the rows under the top first)
  const { gaps, g, k, turn, drop } = await page.evaluate(([lo, P]) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
    const gaps = Array.from(document.querySelectorAll("#content .tx-gap")).filter((g) => g.offsetParent !== null).map((g) => { const b = g.getBoundingClientRect(); return { lo: +g.dataset.lo, hi: +g.dataset.hi, y: b.top - cr.top + c.scrollTop, h: b.height }; });
    const g = gaps.find((x) => lo != null && x.lo === lo) || null;
    if (!g) return { gaps, g };
    const k = Math.floor((g.lo + g.hi) / 2 / P) * P, turn = k + P - 0.35;   // a page in the gap's middle, its last turn, two thirds of the way down
    const drop = Math.round(g.y + g.h * (turn - g.lo) / (g.hi - g.lo));
    c.scrollTop = drop;
    return { gaps, g, k, turn, drop }; }, [m ? m[1] : null, P]);
  if (g) {
    await painted(); await painted();
    const at = await reading();
    await asksPast(a0, 10000);
    release();
    await asksPast(a0 + 1, 8000);
    await page.waitForTimeout(1500); await painted();
    out.nearEnd = { gap: g, page: [k, k + P], turn, drop, at, after: await reading(), blank: await blankPx(), asks: (await asks()).slice(a0) };
  } else { release(); out.nearEnd = { gap: null, gaps, middleAsk: m }; }
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
  // (f: (b)'s drop, at that fraction down the bottom spacer, written in the task that reads the spacer, and returned as drop)
  const shape = (f = null) => page.evaluate(([sel, f]) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(); const t = document.querySelector(sel); const kids = t ? Array.from(t.children) : []; const units = kids.map((k) => k.dataset.unit).filter((u) => u != null).map(Number); const b = t && t.querySelector(".tx-spacer-bot");
    const s = { top: !!(t && t.querySelector(".tx-spacer-top")), first: units.length ? Math.min(...units) : null, last: units.length ? Math.max(...units) : null, gaps: kids.filter((k) => k.classList.contains("tx-gap")).map((k) => [+k.dataset.lo, +k.dataset.hi]),
                bTop: b ? b.getBoundingClientRect().top - cr.top + c.scrollTop : null, bH: b ? b.offsetHeight : null };
    if (f != null && s.bTop != null) { s.drop = Math.round(s.bTop + s.bH * f); c.scrollTop = s.drop; }
    return s; }, [threadSel, f]);
  out.long = { shape: await shape(), regions: (await reading()).regions };
  // (a) THE RE-WINDOW AND THE ECHO OF ITS OWN WRITE. Seventy short rows above the reader in the window are a band far shorter than the
  // re-window's edge margin (twenty average units), so every scroll here reads as near the window's top edge. One small scroll down
  // re-windows around the row now at the top, and that write moves the view (the rows that left the window are counted in the spacer at
  // the average unit). Its echo reads as near the edge too: it must not re-window again.
  await page.evaluate((sel) => { window.__renders = 0; const t = document.querySelector(sel); window.__renderMo = new MutationObserver((recs) => { if (recs.some((r) => r.type === "childList" && r.removedNodes.length > 0)) window.__renders++; }); if (t) window.__renderMo.observe(t, { childList: true }); }, threadSel);
  { const w0 = await page.evaluate(() => window.__writes), a0 = (await asks()).length;
    // the scroll's start and its own position are read in the task that writes it: no scroll event, so no re-window, runs between them
    // (read in a later task, the re-window could already have moved the view, and the premise below compared its write with itself; read
    // in an earlier one, a re-window could move the view before the write, which was then no small scroll from where the reader stood)
    const [st0, st1] = await page.evaluate(() => { const c = document.getElementById("content"), y = c.scrollTop; c.scrollTop = y + 120; return [y, c.scrollTop]; });
    await painted(); await painted(); await page.waitForTimeout(600); await painted();
    out.long.echo = { st0, st1, st2: (await reading()).st, renders: await page.evaluate(() => window.__renders), writes: (await page.evaluate(() => window.__writes)) - w0, asks: (await asks()).slice(a0), shape: await shape() };
    await page.evaluate(() => { if (window.__renderMo) window.__renderMo.disconnect(); }); }
  // (b) A DROP INTO A GAP HELD IN THE BOTTOM SPACER, a fifth of the way down the spacer: past the run's hidden rows, inside the gap below the
  // run. The bottom spacer is walked by each unit's own estimate (a gap's is up to about a million px here, an average unit thousands), so the drop stays
  // where it was dropped, inside that gap, and the page under the viewport top is asked and comes on screen.
  // (the spacer is read and the drop written in one task, shape's f: a re-window in a task between them could move the spacer first)
  { const a0 = (await asks()).length;
    holdTurns = true;   // the page's reply waits at the wire until the premise is read
    const { drop, ...hidden } = await shape(0.2);
    if (drop != null) {
      await painted(); await painted();
      const at = await reading();
      await asksPast(a0, 10000);
      release();
      await page.waitForTimeout(3000); await painted();
      out.long.drop = { hidden, drop, at, after: await reading(), asks: (await asks()).slice(a0),
        underTop: await page.evaluate((sel) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(); const t = document.querySelector(sel);   // the thread's first child on screen from the top
          const k = t ? Array.from(t.children).find((x) => { const b = x.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top + 1 && b.top < cr.bottom; }) : null;
          return k ? (k.classList.contains("turn") ? "row" : k.classList.contains("tx-gap") ? "gap" : k.classList.contains("tx-spacer") ? "spacer" : k.className) : null; }, threadSel) };
    } else { release(); out.long.drop = { hidden, noSpacer: true }; } }
}
if (cfg.legs.includes("climb")) {
  // ROAD 8: a reader drops into the head gap, climbs back through the history page by page with the wheel, then presses the
  // go-to-bottom chip twice (the second from where the first left them), then goes back far into the history the climb loaded and
  // presses End
  const loaded = () => page.waitForFunction(() => window.__rompAskState().gapLoading === 0, null, { timeout: 15000 }).catch(() => {});
  const a0 = (await asks()).length;
  holdTurns = true;   // the drop is read before its page can land
  await setTop(Math.round((await reading()).sh * 0.15));
  const drop = await reading();
  release();
  await asksPast(a0, 10000); await loaded(); await page.waitForTimeout(500); await painted();
  const box = await page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.mouse.move(box.x, box.y);
  const steps = [];
  for (let p = 0; p < cfg.climbPages; p++) {
    const n = (await asks()).length;
    let w = 0;
    while (w < 40 && (await asks()).length === n) { await page.mouse.wheel(0, -1200); await painted(); w++; }
    if ((await asks()).length === n) break;
    await loaded(); await painted();
    const r = await reading();
    steps.push({ wheels: w, sh: r.sh, st: r.st, est: r.est, asked: (await asks()).slice(n) });
  }
  const climbed = Object.assign(await reading(), { measure: await measure() });
  // the loaded run's top in scroll coordinates, read while the head gap stands drawn above the reader: the head gap's height is its
  // estimate whether it is drawn or hidden in the top spacer, so the run starts there until another page lands (as ROAD 11 reads it)
  const runTop = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
    const g = Array.from(document.querySelectorAll("#content .tx-gap")).find((x) => x.offsetParent !== null && +x.dataset.lo === 0);
    if (!g) return null; const b = g.getBoundingClientRect(); return { y: Math.round(b.bottom - cr.top + c.scrollTop), hi: +g.dataset.hi }; });
  const press = async (uuid) => { await page.click("#jump-bottom", { timeout: 5000 }).catch(() => {}); await page.waitForTimeout(1500); await painted(); return Object.assign(await reading(uuid), { measure: await measure() }); };
  const chip1 = await press();
  const chip2 = await press();
  // End's first press after the climb (2026-10-15). The reader goes back far into the run the climb loaded by one scroll write,
  // cfg.endBackUnits units under the run's top (each unit of the run hidden in the top spacer stands for the held figure, else the
  // average unit), where the page builds a window over the run's middle rows and the tail stands hidden in the bottom spacer again,
  // as the climb left it; the write's event, read on the page each frame and capped at 10 s, is every row on screen one of the run's
  // and no gap page on the wire. Then a click on the text of a reply on screen (the selection's anchor and the last press in the
  // transcript, focus on the page body, so the page takes End), and End, read as the chip's presses are. ROAD 9's last press goes
  // back the same way first (goBack)
  const goBack = async () => {
    const m = await measure(), a0 = (await asks()).length;
    const to = runTop && m && (m.heldPx ?? m.avgUnit) ? Math.round(runTop.y + cfg.endBackUnits * (m.heldPx ?? m.avgUnit)) : null;
    let back = { runTop, to, met: false };
    if (to != null) {
      await setTop(to);
      const met = await page.waitForFunction((lo) => {
        const c = document.getElementById("content"), cr = c.getBoundingClientRect();
        const run = ((typeof window.__rompRegions === "function" && window.__rompRegions()) || []).find((r) => r.kind === "run" && r.hi != null && r.lo <= lo && lo < r.hi) || null;
        const turns = Array.from(c.querySelectorAll(".turn[data-uuid]")).filter((e) => { if (e.offsetParent === null || e.dataset.turn == null) return false; const b = e.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top && b.top < cr.bottom; }).map((e) => +e.dataset.turn);
        return !!run && turns.length > 0 && turns.every((t) => t >= run.lo && t < run.hi) && window.__rompAskState().gapLoading === 0;
      }, runTop.hi, { timeout: 10000, polling: "raf" }).then(() => true, () => false);
      await painted(); await page.waitForTimeout(500); await painted();
      back = Object.assign(await reading(), { runTop, to, met, asks: (await asks()).slice(a0), measure: await measure() });
    }
    return back;
  };
  let end1 = null;
  if (cfg.endBackUnits) {
    const back = await goBack();
    // the page settled after the go-back's write, before the click picks a reply's text (settleRead: the re-window that write schedules
    // has run), read into the go-back's verdict, so a page that does not settle fails the End test with its reading (back.settled)
    if (back.met) { back.settled = await settleRead(); back.met = back.settled.met; }
    // the reply text the click lands on: the paragraph of a reply wholly on screen nearest the pane's centre, 12 px into its first line
    const at = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
      const ps = Array.from(c.querySelectorAll(".turn-assistant .md p")).filter((p) => { const b = p.getBoundingClientRect(); return b.height > 0 && b.top >= cr.top + 20 && b.bottom <= cr.bottom - 20 && /^Answer \d+/.test(p.textContent); });
      const mid = (p) => Math.abs(p.getBoundingClientRect().top - cr.top - cr.height / 2);
      const p = ps.sort((a, b) => mid(a) - mid(b))[0]; if (!p) return null;
      const r = document.createRange(), t = document.createTreeWalker(p, NodeFilter.SHOW_TEXT).nextNode(); r.setStart(t, 0); r.setEnd(t, 6); const b = r.getBoundingClientRect();
      const x = Math.round(b.left + 12), y = Math.round(b.top + b.height / 2), e = document.elementFromPoint(x, y);
      return { x, y, onText: !!e && p.contains(e) }; });
    if (at) { await page.mouse.click(at.x, at.y); await painted(); await page.waitForTimeout(300); }
    const clicked = Object.assign({ at }, await page.evaluate(() => { const c = document.getElementById("content"), a = document.getSelection()?.anchorNode, ae = a && (a.nodeType === 1 ? a : a.parentElement);
      return { bodyFocused: document.activeElement === document.body, anchorInContent: !!ae && c.contains(ae) }; }));
    // whether the page took End: a window keydown listener added after the page's own reads End's default prevented (null when the key
    // never reaches the window)
    await page.evaluate(() => { window.__endTaken = null; window.addEventListener("keydown", (e) => { if (e.key === "End") window.__endTaken = e.defaultPrevented; }); });
    await page.keyboard.press("End");
    await page.waitForTimeout(1500); await painted();
    end1 = Object.assign(await reading(), { measure: await measure(), back, clicked, taken: await page.evaluate(() => window.__endTaken) });
  }
  // the climb's own record, printed before ROAD 9 grows the transcript: when a later road fails, the tests that read the climb alone
  // still read it (the lab's _climb takes this line when the driver failed or printed no result)
  process.stdout.write("CLIMB:" + JSON.stringify({ drop, steps, climbed, chip1, chip2, end1 }) + "\n");
  // ROAD 9 (the class's appended turns): replies land below the window while the reader stands in the history. The reader drops
  // back to the tail run's first rows, cfg.append.into units under its top (the units between that one and the top spacer's end are
  // all tail units, at the held figure each), so the window built there holds the climbed run's last units, the gap above the tail
  // run and the tail run's first units (in the lab's runs 140 units: 67, the gap, then 72, 36 of them tall replies): the reader
  // stands far from both its edges (no re-window follows a write there), and a bottom spacer holds the units under it. One reply
  // lands first; then the rest land in one write, which the keyed paint (render.ts tailDiff) puts in the bottom spacer at the held
  // figure as it stands. A press of the chip is the next window build.
  // Each landing is read after its units arrive or a 20 s wait ends, its newest row still the fixture's last reply as written at
  // setup (cfg.newest); the chip press after both landings names the last reply appended.
  // Then the reader goes back into the climbed run as before End (goBack), and one more reply lands (cfg.append.tall), taller than the
  // held figure, its turn left open: the error card the lab's session ends on stands only while no turn is open, so the reply takes
  // its place as the newest unit, hidden in the bottom spacer, and the unit count stands, the held figure with it. Read after the
  // regions name it the tail run's last unit or a 20 s wait ends. Then the chip, its press read with the reply's height. (The press
  // above, the first build after the appends, lowers the held figure, and the thread shrinking under a reader in follow mode puts them
  // at the bottom on its own: render.ts tail-shrink)
  let below = null;
  if (cfg.append) {
    const land = async (file, units) => {
      const u0 = (await measure()).units;
      fs.appendFileSync(cfg.append.transcript, fs.readFileSync(file, "utf8"));
      await page.waitForFunction((n) => { const m = window.__rompViewMeasure(); return !!m && m.units >= n; }, u0 + units, { timeout: 20000 }).catch(() => {});
      await painted(); await page.waitForTimeout(500); await painted();
      return Object.assign(await reading(), { measure: await measure() });
    };
    // (the drop is computed and written in one task: a re-window in a task between them could move the top spacer first)
    const to = await page.evaluate((into) => {
      const c = document.getElementById("content"), cr = c.getBoundingClientRect(), t = c.querySelector(".tx-spacer-top"), m = window.__rompViewMeasure();
      const tail = (typeof window.__rompRegions === "function" ? window.__rompRegions() : []).find((r) => r.kind === "run" && r.hi == null);
      if (!t || !m || !tail) return null;
      const above = m.winStart - (m.units - tail.n) - into;   // hidden tail units between the drop's unit and the top spacer's end
      if (!(above > 0)) return null;
      const y = Math.round(t.getBoundingClientRect().bottom - cr.top + c.scrollTop - above * (m.heldPx ?? m.avgUnit));
      c.scrollTop = y; return y;
    }, cfg.append.into);
    if (to != null) { await painted(); await page.waitForTimeout(1000); await painted(); }
    const dropped = Object.assign(await reading(), { measure: await measure() });
    const first = await land(cfg.append.first, 2);
    const rest = await land(cfg.append.lines, cfg.append.units);
    const next = await press(cfg.append.newest);
    const back = cfg.endBackUnits ? await goBack() : null, t = cfg.append.tall;
    fs.appendFileSync(cfg.append.transcript, JSON.stringify({ type: "assistant", uuid: t.uuid, parentUuid: cfg.append.newest, sessionId: cfg.sid,
      timestamp: new Date().toISOString(), message: { role: "assistant", model: "claude-fable-5-1", content: [{ type: "text", text: t.text }] } }) + "\n");
    const last = await page.waitForFunction((u) => { const r = ((typeof window.__rompRegions === "function" && window.__rompRegions()) || []).find((x) => x.kind === "run" && x.hi == null);
      return !!r && r.last === u; }, t.uuid, { timeout: 20000, polling: "raf" }).then(() => true, () => false);
    await painted(); await page.waitForTimeout(500); await painted();
    const landed = Object.assign(await reading(t.uuid), { last, measure: await measure() });
    const pressed = Object.assign(await press(t.uuid), { newestH: await page.evaluate((u) => { const r = document.querySelector(`#content .turn[data-uuid="${u}"]`); return r ? r.offsetHeight : null; }, t.uuid) });
    below = { to, dropped, first, rest, next, tall: { back, landed, pressed } };
  }
  out.climb = { drop, steps, climbed, chip1, chip2, end1, below };
}
if (cfg.legs.includes("keep")) {
  // ROAD 12 (2026-10-21): a tool run toggled in the history after replies land keeps its row where the reader clicked it. The reader
  // drops into the tail run by two scroll writes, each onto a unit hidden in a spacer so the page re-windows around it: first near the
  // run's top (cfg.keep.first of the units the page holds, in the top spacer the landing left), then far down it (cfg.keep.at, in the
  // bottom spacer the first drop left), so the window stands over the run's lower units, the units above it hidden in the top spacer.
  // Then, leg by leg, the collapsed tool run in that window nearest its middle is put 200 px under the viewport top by the reader's
  // write, the leg's replies land (the keyed paint adds them at the tail, the reader far above), and the run's toggle is clicked:
  // the first leg expands the run, the next collapses it, and so on. Each reading is the row's top under the viewport top, whether it
  // is expanded, the top spacer's height and the view's measures, read after the step's frames have run
  const K = cfg.keep;
  const keepAt = (key) => page.evaluate((k) => {
    const c = document.getElementById("content"), cr = c.getBoundingClientRect(), t = c.querySelector(".tx-spacer-top");
    const line = c.querySelector(`.toolgroup-line[data-gkey="${k}"]`), row = line && line.closest(".turn-toolgroup"), b = row ? row.getBoundingClientRect() : null;
    return { y: b ? Math.round((b.top - cr.top) * 100) / 100 : null, expanded: row ? row.classList.contains("expanded") : null, members: row ? c.querySelectorAll(".tg-child").length : null,
             st: c.scrollTop, topPx: t ? parseFloat(t.style.height) || 0 : null, measure: window.__rompViewMeasure() };
  }, key);
  // a drop onto a unit hidden in a spacer, by its estimate (the held figure for every hidden unit between it and the window, none of
  // them a gap: the head gap is the first unit, above every unit dropped on), computed and written in one task (a re-window in a task
  // between them could move a spacer first); null when the unit is rendered
  const dropOn = async (at, what) => {
    const d = await page.evaluate((a) => {
      const c = document.getElementById("content"), cr = c.getBoundingClientRect(), m = window.__rompViewMeasure(), unit = Math.round(m.units * a), held = m.heldPx ?? m.avgUnit;
      const t = c.querySelector(".tx-spacer-top"), b = c.querySelector(".tx-spacer-bot");
      let y = null;
      if (t && unit < m.winStart) y = Math.round(t.getBoundingClientRect().bottom - cr.top + c.scrollTop - (m.winStart - unit) * held);
      else if (b && unit >= m.winEnd) y = Math.round(b.getBoundingClientRect().top - cr.top + c.scrollTop + (unit - m.winEnd) * held);
      if (y == null) return null;
      c.scrollTop = y; return { unit, y, measure: m };
    }, at);
    if (d) { await painted(); await settlePage(what); await page.waitForTimeout(500); await painted(); d.after = await measure(); }
    return d;
  };
  const first = await dropOn(K.first, "ROAD 12's first drop");
  const to = first ? await dropOn(K.at, "ROAD 12's second drop") : null;
  // the collapsed tool run nearest the window's middle, picked once (its key: the toggle's data-gkey, which a rebuild keeps)
  const placed = to ? await page.evaluate(() => {
    const c = document.getElementById("content"), m = window.__rompViewMeasure(), mid = (m.winStart + m.winEnd) / 2;
    const rows = Array.from(c.querySelectorAll(".turn-toolgroup:not(.expanded)")).filter((r) => r.dataset.unit != null && r.querySelector('.toolgroup-line[data-gkey^="tg:"]'));
    rows.sort((a, b) => Math.abs(+a.dataset.unit - mid) - Math.abs(+b.dataset.unit - mid));
    const r = rows[0];
    return r ? { key: r.querySelector(".toolgroup-line").dataset.gkey, unit: +r.dataset.unit, measure: m } : null;
  }) : null;
  // the reader puts the run's row 200 px under the viewport top (a write computed and made in one task), before each leg
  const place = async () => {
    const y = await page.evaluate((k) => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(), l = c.querySelector(`.toolgroup-line[data-gkey="${k}"]`), r = l && l.closest(".turn-toolgroup");
      if (!r) return null; const y = Math.round(r.getBoundingClientRect().top - cr.top + c.scrollTop - 200); c.scrollTop = y; return y; }, placed.key);
    if (y != null) { await painted(); await settlePage("ROAD 12's place"); await page.waitForTimeout(300); await painted(); }
    return y;
  };
  // a click on the run's caret, read after its frames have run
  const click = async () => {
    const p = await page.evaluate((k) => { const l = document.querySelector(`#content .toolgroup-line[data-gkey="${k}"]`); if (!l) return null;
      const b = (l.querySelector(".toolgroup-caret") || l).getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; }, placed.key);
    if (p) { await page.mouse.click(p.x, p.y); await painted(); await page.waitForTimeout(300); await painted(); }
    return p;
  };
  const legs = [];
  if (placed) for (const leg of K.legs) {
    const at = await place(), before = await keepAt(placed.key), n0 = before.measure.units;
    fs.appendFileSync(K.transcript, fs.readFileSync(leg.file, "utf8"));
    const met = await page.waitForFunction((n) => { const m = window.__rompViewMeasure(); return !!m && m.units >= n; }, n0 + leg.units, { timeout: 20000 }).then(() => true, () => false);
    await painted(); await page.waitForTimeout(500); await painted();
    const landed = Object.assign(await keepAt(placed.key), { met });
    const pressed = await click(), toggled = await keepAt(placed.key);
    legs.push({ replies: leg.replies, units: leg.units, at, before, landed, toggled, pressed });
  }
  out.keep = { first, to, placed, legs };
}
if (cfg.legs.includes("shown")) {
  // ROAD 10 (the class's second session): a view built while hidden takes its measures when it is first shown. When the page lands on
  // the short session cfg.sid2 (the first-show tests check it), the idle prebuild builds the long session's view hidden (display none: nothing
  // measures there, and its hidden rows are sized at the fallback figure). Its first show keeps that window (render.ts landActive,
  // sizeSpacers). Then the page's settings event (render.ts rerenderAll: every view emptied, its average unit forgotten, its px per
  // turn kept) while the reader is on the short session; the idle prebuild builds the long view hidden again, and the switch back
  // shows it. A mark set on the hidden view's top spacer before each show says whether the show kept the prebuilt window or built
  // another one
  const thread = (sid) => page.evaluate((s) => {
    const t = document.querySelector(`#content .thread[data-session="${s}"]`);
    if (!t) return null;
    const top = t.querySelector(".tx-spacer-top");
    return { display: t.style.display, children: t.children.length, topPx: top ? parseFloat(top.style.height) || 0 : null,
             kept: !!(top && top.dataset.lab === "kept") };
  }, sid);
  const mark = (sid) => page.evaluate((s) => { const t = document.querySelector(`#content .thread[data-session="${s}"] .tx-spacer-top`); if (t) t.dataset.lab = "kept"; return !!t; }, sid);
  const show = async (sid) => { await page.evaluate((i) => window.postMessage({ type: "focus", id: i }, "*"), sid); await page.waitForTimeout(2500); await painted(); };
  const regions = (sid) => page.evaluate((s) => typeof window.__rompRegions === "function" ? window.__rompRegions(s) : null, sid);
  const boot = { active: await measure(), long: await thread(cfg.sid), marked: await mark(cfg.sid) };
  await show(cfg.sid);
  const first = { measure: await measure(), long: await thread(cfg.sid), regions: await regions(cfg.sid) };
  if (cfg.legs.includes("rebuild")) {
    // the bounded class: a scroll 20 hidden units above the window's start builds a window of its own over the same units, which sets
    // the figure afresh (render.ts renderWindowItems); then back to the end, where the show left the reader (the scroll is computed and
    // written in one task: a re-window in a task between them could move the window's start first)
    const to = await page.evaluate((s) => {
      const c = document.getElementById("content"), cr = c.getBoundingClientRect(), m = window.__rompViewMeasure();
      const t = c.querySelector(`.thread[data-session="${s}"] .tx-spacer-top`);
      if (!t || !m) return null;
      const y = Math.round(t.getBoundingClientRect().bottom - cr.top + c.scrollTop - 20 * (m.heldPx ?? m.avgUnit));
      c.scrollTop = y; return y;
    }, cfg.sid);
    if (to != null) { await painted(); await page.waitForTimeout(2500); await painted(); }
    first.rebuilt = { to, measure: await measure() };
    await setTop(1e9); await page.waitForTimeout(1500); await painted();
  }
  await show(cfg.sid2);
  await page.evaluate(() => window.dispatchEvent(new Event("romp:settings")));
  await page.waitForTimeout(5000); await painted();
  const hidden = { active: await measure(), long: await thread(cfg.sid), marked: await mark(cfg.sid) };
  await show(cfg.sid);
  const again = { measure: await measure(), long: await thread(cfg.sid), regions: await regions(cfg.sid) };
  out.shown = { boot, first, hidden, again };
}
if (cfg.legs.includes("end")) {
  // ROAD 11: End from far back, for the End classes of one fixture family (tests/test_long_history_end_browser.py: cfg.variants, in the
  // family's order) on this one page. Once for the family, the reader drops into the head gap at 15% of the scroll range and climbs
  // cfg.endPages history pages with the wheel (each page's reply held at the wire until its ask is seen). Then for each variant: what
  // the last one left is cleared (clearLast), the reader goes back far by scroll writes into the run the climb loaded (goBack; the
  // first variant stands where the climb left it, far back already, as each class's own road did), the variant's own staging runs (a
  // click in the transcript, its centre or a tool row's fold toggle: vc.endPress; vc.endType: words typed in the message box before
  // the toggle; vc.endAfter: a step after it; or vc.endAt's element, reached in place of the click), the page goes quiet (quiet), and
  // End is pressed once and read. A variant's record holds its own readings and the page errors raised in its window, from its start
  // to its last reading; those raised before the first variant starts (the boot, the drop, the climb and the setup after it:
  // out.pageErrors up to out.end.nShared), or after the last variant (from out.end.lastErr), are every variant's. When a variant's
  // staging throws, its record holds the error instead, and the run goes on with the next variant
  const loaded = () => page.waitForFunction(() => window.__rompAskState().gapLoading === 0, null, { timeout: 15000 }).catch(() => {});
  const a0 = (await asks()).length;
  holdTurns = true;   // the drop is read before its page can land
  await setTop(Math.round((await reading()).sh * 0.15));
  const drop = await reading();
  release();
  await asksPast(a0, 6000); await loaded(); await page.waitForTimeout(800); await painted();
  const box = await page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.mouse.move(box.x, box.y);
  const steps = [];
  for (let p = 0; p < cfg.endPages; p++) {
    const n = (await asks()).length;
    holdTurns = true;
    let w = 0;
    while (w < 80 && (await asks()).length === n) { await page.mouse.wheel(0, -1200); await painted(); w++; }
    if ((await asks()).length === n) { release(); break; }
    await page.waitForTimeout(150); await painted();
    release();
    await loaded(); await painted(); await page.waitForTimeout(150); await painted();
    const r = await reading();
    steps.push({ wheels: w, sh: r.sh, st: r.st, asked: (await asks()).slice(n) });
  }
  // the loaded run's top in scroll coordinates: the head gap's lower edge, read while that gap is drawn above the reader (after the
  // climb, and after a variant's own page climb). Nothing stands above the run but the head gap, whose height is its estimate whether
  // it is drawn or hidden in the top spacer, so the run starts there until another page lands; each of the run's units hidden in a
  // spacer stands for the figure unitPx takes (the held figure, else the average unit)
  const readRunTop = () => page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
    const g = Array.from(document.querySelectorAll("#content .tx-gap")).find((x) => x.offsetParent !== null && +x.dataset.lo === 0);
    if (!g) return null; const b = g.getBoundingClientRect(); return { y: Math.round(b.bottom - cr.top + c.scrollTop), hi: +g.dataset.hi }; });
  let runTop = await readRunTop();
  // (nShared and lastErr are set right before the first variant starts: every error raised until then is every variant's)
  out.end = { drop, steps, climbed: Object.assign(await reading(), { measure: await measure() }), runTop, nShared: null, lastErr: null, variants: {} };
  // vc.endAt instead of the click: the element at.sel, one wholly on screen, else the rendered one nearest the viewport's centre,
  // brought to the centre when at.intoView, in the task that picks it, and picked again once the page settled after that scroll (a
  // window build the scroll provokes can replace it); its audio staged (at.media: "loaded" at 0 s and half volume, "ended" seeked to its
  // end, "failed" read once its error is set); then reached by at.way: "focus" (element.focus), "tab" (Tab at.tab times from the last
  // link before it to at.pre) or "press" (a real click on it, then focus returned to the page body and the selection emptied). Then,
  // before End: where focus sits against it, the focused node in the accessibility tree, its audio's state and the distance from the
  // bottom. Each step after the pick finds the element by its keys in the task that acts on it (window.__labEndAt; name: the class, for
  // its failures)
  const stageEnd = async (at, name) => {
    const pick = (scroll) => page.evaluate(([sel, name, scroll]) => {
      const c = document.getElementById("content"), cr = c.getBoundingClientRect();
      const all = Array.from(document.querySelectorAll(sel)).filter((e) => c.contains(e) && (e.getClientRects().length > 0 || e.tagName === "AUDIO"));
      const on = all.filter((e) => { const b = e.getBoundingClientRect(); return b.height > 0 && b.top >= cr.top + 4 && b.bottom <= cr.bottom - 4; });
      const mid = (e) => Math.abs(e.getBoundingClientRect().top - cr.top - cr.height / 2);
      const t = on[0] || all.sort((x, y) => mid(x) - mid(y))[0], row = t ? t.closest(".turn[data-uuid]") : null;
      window.__labEnd = t ? { name, sel, uuid: row ? row.getAttribute("data-uuid") : null, i: Array.from((row || c).querySelectorAll(sel)).indexOf(t), node: null, step: null } : null;
      if (t && scroll && !on[0]) t.scrollIntoView({ block: "center" });
      return t ? (on[0] ? "on screen" : "nearest") : null; }, [at.sel, name, scroll && !!at.intoView]);
    let picked = await pick(true);
    if (at.intoView && picked === "nearest") {
      await painted(); await page.waitForTimeout(1200); await painted();
      await settlePage(name + ": the scroll that brought its END_AT element into view");
      picked = await pick(false);
    }
    if (!picked) return { picked };
    // the clip's waits keep nothing: each finds the element by its keys and drives its clip there (window.__labMedia; the seek runs in
    // the task that first finds the clip's metadata in), so a re-window that rebuilt the row before the staging's action stages the
    // fresh element again. A wait that ends at its cap lets the staging go on, and the readings decide
    const metOn = (step, met) => page.waitForFunction(([step, met, want]) => { const m = window.__labEndAt(step), staged = window.__labMedia(m, want);
      return met === "metadata" ? m.readyState >= 1 : staged; }, [step, met, at.media], { timeout: met === "end" ? 5000 : 15000 }).catch(() => {});
    if (at.media === "loaded" || at.media === "ended") {
      await metOn("the wait for its clip's metadata, and its seek", "metadata");
      if (at.media === "ended") await metOn("the wait for its end", "end");
    } else if (at.media === "failed") await metOn("the wait for its error", "error");
    // the staging's action that End reads, in the task that finds the element by its keys with its clip as the waits left it (an element
    // a re-window rebuilt is staged again first), read each frame up to 15 s, then taken as things stand: focus on the element ("focus")
    // or on the last link before it ("link"); it keeps the element (__labEndAt's keep)
    const act = (step, how) => page.evaluate(([step, how, want, pre, cap]) => new Promise((resolve, reject) => {
      const t0 = performance.now();
      const tick = () => { try {
        const t = window.__labEndAt(step);
        if (!window.__labMedia(t, want) && performance.now() - t0 < cap) { requestAnimationFrame(tick); return; }
        window.__labEndAt(step, true);
        if (how === "focus") t.focus({ preventScroll: true });
        else { const ps = Array.from(document.querySelectorAll('a[href="' + pre + '"]')).filter((a) => a.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING); if (ps.length) ps[ps.length - 1].focus({ preventScroll: true }); }
        resolve();
      } catch (e) { reject(e); } };
      tick();
    }), [step, how, at.media || null, at.pre || null, 15000]);
    if (at.way === "focus") await act("its focus", "focus");
    else if (at.way === "tab") {
      await act("the focus on the link before it", "link");
      for (let i = 0; i < at.tab; i++) await page.keyboard.press("Tab");
    } else {
      // aimed at the element its keys find, the real click, then the element its keys find after the click kept, with the last press
      // inside it: a re-window between the aim and the click rebuilds the row under the pointer, and the click lands on the fresh element
      const p = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(), b = window.__labEndAt("its press").getBoundingClientRect();
        return b.height > 0 && b.top >= cr.top && b.bottom <= cr.bottom ? { x: b.left + Math.min(12, b.width / 2), y: b.top + b.height / 2 } : null; });
      if (p) await page.mouse.click(p.x, p.y);
      await painted();
      await page.evaluate(([p, name]) => { const t = window.__labEndAt("the click of its press", true), r = window.__endRange;
        if (p && !(r && t.contains(r.startContainer))) throw new Error(name + ": the last press is not inside the END_AT element its keys find after the click at " + Math.round(p.x) + "," + Math.round(p.y) +
          " (the press sits at " + (r ? r.startContainer.nodeName + (r.startContainer.id ? "#" + r.startContainer.id : "") : "nothing") + "): the click landed elsewhere, or a re-window rebuilt the row after it");
        const f = document.activeElement; if (f && f !== document.body && f.blur) f.blur(); document.getSelection().removeAllRanges(); }, [p, name]);
    }
    await painted(); await page.waitForTimeout(150);
    let ax = null;
    if (at.way === "tab") {
      const s = await page.context().newCDPSession(page);
      try { ax = (await s.send("Accessibility.getFullAXTree")).nodes.filter((n) => n.role && n.role.value !== "RootWebArea" && (n.properties || []).some((q) => q.name === "focused" && q.value && q.value.value === true)).map((n) => (n.role && n.role.value) + ":" + (n.name && n.name.value)); }
      finally { await s.detach().catch(() => {}); }
    }
    const staged = await page.evaluate(() => { const c = document.getElementById("content"), t = window.__labEndAt("the reading before End"), f = document.activeElement;
      return { inContent: c.contains(t), focused: f === t, bodyFocused: f === document.body, anchor: !!document.getSelection().anchorNode,
               dist: c.scrollHeight - c.scrollTop - c.clientHeight, ch: c.clientHeight,
               media: t instanceof HTMLMediaElement ? { readyState: t.readyState, duration: t.duration, ended: t.ended, currentTime: t.currentTime, paused: t.paused } : null }; });
    return { picked, ax, staged };
  };
  // every open tool row closed, each by a script click on its fold toggle (a script click dispatches no pointerdown, mousedown or
  // selectionchange); returns how many
  const closeFolds = () => page.evaluate(() => { let n = 0;
    for (const t of Array.from(document.querySelectorAll("#content .turn.fold-open"))) { const g = t.querySelector(".tool-fold-toggle"); if (g) { g.click(); n++; } }
    return n; });
  // what the last variant left, cleared before the next one's staging: the message box's quote chips (each chip's remove button, by a
  // script click) and its text (written empty, with an input event); focus returned to the page body; every open tool row closed; the
  // page's last press set outside #content by a real press on a spot the lab puts over the corner of the tab strip and removes at once
  // (a spot of its own, so the press meets no control of the page: nothing changes but focus, the selection and the last press); then
  // the selection emptied, and the pointer back at the transcript's centre, where the climb left it. The press and the emptying are the
  // last events of the clearing that a variant's End reads, older than every event of the variant
  const clearLast = async () => {
    const did = await page.evaluate(() => {
      let chips = 0;
      for (let x = document.querySelector("#composer-chips .composer-chip-x"); x && chips < 50; x = document.querySelector("#composer-chips .composer-chip-x")) { x.click(); chips++; }
      const ta = document.getElementById("composer-input"), text = ta ? ta.value : null;
      if (ta && text) { ta.value = ""; ta.dispatchEvent(new Event("input", { bubbles: true })); }
      const f = document.activeElement; if (f && f !== document.body && f.blur) f.blur();
      return { chips, text };
    });
    did.folds = await closeFolds();
    await page.evaluate(() => { const d = document.createElement("div"); d.id = "lab-neutral-press";
      d.style.cssText = "position:fixed;left:0;top:0;width:8px;height:8px;z-index:2147483647"; document.body.appendChild(d); });
    await page.mouse.click(4, 4);
    await page.evaluate(() => { const d = document.getElementById("lab-neutral-press"); if (d) d.remove(); document.getSelection().removeAllRanges(); });
    await page.mouse.move(box.x, box.y);
    await painted();
    return did;
  };
  // what the clearing must have left: focus on the page body, no selection, an empty message box with no chip, no open tool row in
  // the transcript and no comment box shown; a variant whose clearing did not take fails (its record holds this reading)
  const leftOver = () => page.evaluate(() => { const p = document.getElementById("cmt-pop"), ta = document.getElementById("composer-input");
    return { bodyFocused: document.activeElement === document.body, anchor: !!document.getSelection().anchorNode, text: ta ? ta.value : null,
             chips: document.querySelectorAll("#composer-chips .composer-chip").length, open: document.querySelectorAll("#content .turn.fold-open").length,
             commentBox: !!p && p.getClientRects().length > 0 }; });
  const clean = (l) => l.bodyFocused && !l.anchor && !l.text && !l.chips && !l.open && !l.commentBox;
  // the page's garbage collected (CDP HeapProfiler.collectGarbage) in each go-back, once the window over the run stands: what the last
  // variant built and the go-back took down (the tall tail's rows among them) is let go there, so it does not pile up in the one page
  // the family runs on, and no collection of it falls on the variant's End press; the go-back's last write, after it, renders the
  // window around the reader again before the staging. The page's DOM node count and JS heap are read after it (metrics: the CDP
  // Performance domain enabled for that read alone, so no press runs under its probes)
  const cdp = await page.context().newCDPSession(page);
  const metrics = async () => { await cdp.send("Performance.enable").catch(() => {}); const m = (await cdp.send("Performance.getMetrics").catch(() => ({ metrics: [] }))).metrics;
    await cdp.send("Performance.disable").catch(() => {});
    const get = (n) => { const x = m.find((q) => q.name === n); return x ? Math.round(x.value) : null; };
    return { nodes: get("Nodes"), jsHeapMB: get("JSHeapUsedSize") != null ? Math.round(get("JSHeapUsedSize") / 1048576) : null, layoutObjects: get("LayoutObjects") }; };
  const collect = async () => { await cdp.send("HeapProfiler.collectGarbage"); return metrics(); };
  // back far by scroll writes into the loaded run: first to the end (where End left the reader, or did not), so the run stands hidden in
  // the top spacer; then cfg.goBackUnits units under the run's top, where the page builds a window over the run's first rows (its
  // re-window can anchor the reader below those rows, by about one unit's estimate); then, that window built and the garbage collected,
  // to the top of its rendered row nearest cfg.goBackTurns turns under the run's top. Each step's event is read on the page each frame,
  // capped at 10 s (on the cap the variant fails, naming its last reading): a row of the run rendered, no gap page on the wire; then
  // every row on screen one of the run's, no gap page on the wire and the page settled (window.__labQuiet: the re-window that last
  // write schedules has run, and the staging after it meets the rows it built), over two frames running
  const goBack = async () => {
    await setTop(1e9);
    await loaded(); await painted();
    const m = await measure();
    if (!runTop || !m || !(m.heldPx ?? m.avgUnit)) throw new Error("go back: no loaded run's top was read (no head gap drawn above the reader after the climb) or the view has no unit figure: " + JSON.stringify({ runTop, measure: m }));
    const to = Math.round(runTop.y + cfg.goBackUnits * (m.heldPx ?? m.avgUnit));
    await setTop(to);
    const row = await page.evaluate(([lo, want, cap]) => new Promise((resolve) => {
      const t0 = performance.now(); let last = null;
      const tick = () => {
        const c = document.getElementById("content"), cr = c.getBoundingClientRect();
        const run = ((typeof window.__rompRegions === "function" && window.__rompRegions()) || []).find((r) => r.kind === "run" && r.hi != null && r.lo <= lo && lo < r.hi) || null;
        const rows = run ? Array.from(c.querySelectorAll(".turn[data-uuid]")).filter((e) => e.offsetParent !== null && e.dataset.turn != null && +e.dataset.turn >= run.lo && +e.dataset.turn < run.hi && e.getBoundingClientRect().height > 0) : [];
        const gl = window.__rompAskState().gapLoading;
        last = { run: run ? [run.lo, run.hi] : null, rendered: rows.length, gapLoading: gl, st: c.scrollTop };
        if (rows.length && gl === 0) {
          const best = rows.reduce((a, e) => (Math.abs(+e.dataset.turn - want) < Math.abs(+a.dataset.turn - want) ? e : a));
          resolve({ met: true, turn: +best.dataset.turn, y: Math.round(best.getBoundingClientRect().top - cr.top + c.scrollTop), last });
        } else if (performance.now() - t0 >= cap) resolve({ met: false, last });
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }), [runTop.hi, runTop.hi + cfg.goBackTurns, 10000]);
    if (!row.met) throw new Error("go back: no row of the loaded run was rendered within 10 s of the write to " + to + " px: last reading " + JSON.stringify(row.last));
    const heap = await collect();
    await setTop(row.y);
    const got = await page.evaluate(([lo, cap]) => new Promise((resolve) => {
      const t0 = performance.now(), q = { s: window.__labScrolls }; let n = 0, last = null;
      const tick = () => {
        const c = document.getElementById("content"), cr = c.getBoundingClientRect();
        const run = ((typeof window.__rompRegions === "function" && window.__rompRegions()) || []).find((r) => r.kind === "run" && r.hi != null && r.lo <= lo && lo < r.hi) || null;
        const turns = Array.from(c.querySelectorAll(".turn[data-uuid]")).filter((e) => { if (e.offsetParent === null || e.dataset.turn == null) return false; const b = e.getBoundingClientRect(); return b.height > 0 && b.bottom > cr.top && b.top < cr.bottom; }).map((e) => +e.dataset.turn);
        const gl = window.__rompAskState().gapLoading, quiet = window.__labQuiet(q);
        last = { run: run ? [run.lo, run.hi] : null, turns: turns.length ? [Math.min(...turns), Math.max(...turns)] : null, gapLoading: gl, st: c.scrollTop, quiet, scrollRafs: window.__labScrollRafs() };
        n = quiet && run && turns.length && turns.every((t) => t >= run.lo && t < run.hi) && gl === 0 ? n + 1 : 0;
        const ms = Math.round(performance.now() - t0);
        if (n >= 2) resolve({ met: true, ms, last }); else if (ms >= cap) resolve({ met: false, ms, last }); else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }), [runTop.hi, 10000]);
    if (!got.met) throw new Error("go back: the rows on screen were not the loaded run's, with the page settled, within 10 s of the write to turn " + row.turn + "'s row at " + row.y + " px: last reading " + JSON.stringify(got.last));
    return { to, row: { turn: row.turn, y: row.y }, heap, ms: got.ms, at: got.last };
  };
  // the text click's point: the transcript's centre, or, when a reply's own element (a link, a list, a code block, a medium, a menu or
  // listbox element: the family's END_MARKUP rotates across the short answers) stands under it, the nearest point straight above or
  // below it with none
  const plainPoint = () => page.evaluate((b) => {
    const c = document.getElementById("content"), cr = c.getBoundingClientRect();
    const plain = (y) => { const e = document.elementFromPoint(b.x, y); return !!e && c.contains(e) && !e.closest(".md a, .md ul, .md ol, .md pre, .md audio, .md video, .md [role], .md button, .md input"); };
    for (let d = 0; d < cr.height / 2 - 8; d += 4) for (const y of d ? [b.y - d, b.y + d] : [b.y]) if (plain(y)) return { x: b.x, y, moved: y - b.y };
    return { x: b.x, y: b.y, moved: null };
  }, box);
  // after End: the newest row on screen within a screen of the bottom over two frames running, capped at 4 s (on the cap the readings
  // follow as they stand, and the test's own assertion decides). Read on the page after each frame it produces (requestAnimationFrame,
  // then a task queued from it), so a reading never runs ahead of the page's own work in that frame (its re-window among it)
  const endSettle = () => page.evaluate(([u, cap]) => new Promise((resolve) => {
    const t0 = performance.now(); let n = 0;
    const next = () => requestAnimationFrame(() => setTimeout(check, 0));
    const check = () => {
      const c = document.getElementById("content"), cr = c.getBoundingClientRect(), row = document.querySelector(`#content .turn[data-uuid="${u}"]`), r = row ? row.getBoundingClientRect() : null;
      n = r && r.bottom > cr.top + 1 && r.top < cr.bottom - 1 && c.scrollHeight - c.scrollTop - c.clientHeight <= c.clientHeight ? n + 1 : 0;
      const ms = Math.round(performance.now() - t0);
      if (n >= 2) resolve({ met: true, ms }); else if (ms >= cap) resolve({ met: false, ms }); else next();
    };
    next();
  }), [cfg.newest, 4000]);
  // before End, the page quiet: an idle period granted (requestIdleCallback, not its timeout) with no long task (over 50 ms) ended in
  // the last 400 ms, read at each idle period, capped at 3 s (on the cap End is pressed as things stand, and the reading says so). With
  // the page's End handler gone, End is the browser's smooth scroll, which the page's first re-window cuts short of the bottom; work
  // left over from the staging or the go-back, run at the press, delays that re-window, and past the scroll's end the reader reaches
  // the bottom with no handler at all
  const quiet = () => page.evaluate(([gap, cap]) => new Promise((resolve) => {
    const t0 = performance.now();
    const check = (d) => {
      const now = performance.now(), ms = Math.round(now - t0), since = window.__labLongEnd == null ? null : Math.round(now - window.__labLongEnd);
      if (!d.didTimeout && (since == null || since >= gap)) resolve({ met: true, ms, since });
      else if (ms >= cap) resolve({ met: false, ms, since });
      else requestIdleCallback(check, { timeout: 100 });
    };
    requestIdleCallback(check, { timeout: 100 });
  }), [400, 3000]);
  // the long tasks from 3 s before the press to 1.5 s after it, each [start, duration] in ms from the press
  const pressTasks = () => page.evaluate(() => { const p = window.__labPressT; if (p == null) return null;
    return window.__labLong.filter((x) => x[1] > p - 3000 && x[0] < p + 1500).map((x) => [Math.round(x[0] - p), Math.round(x[1] - x[0])]); });
  const runVariant = async (vc, k) => {
    const cleared = await clearLast();
    cleared.left = await leftOver();
    if (!clean(cleared.left)) throw new Error("clearing what the last variant left did not take: " + JSON.stringify(cleared));
    // the lab's own readings, each the variant's: set null here, and each road below sets its own as it starts
    await page.evaluate(() => { window.__atEnd = null; window.__press = null; window.__pressRange = null; window.__selT = null; window.__pressT = null; window.__selTarget = null; window.__labPressT = null; });
    // (vc.endAt.ask: a question card at the transcript's end first, the kernel's askLive frame dispatched by the lab; the kernel's
    // askLiveClear for the session dropped at the wire from here on)
    if (vc.endAt && vc.endAt.ask) {
      dropAskClear = true;
      await page.evaluate((m) => window.dispatchEvent(new MessageEvent("message", { data: m })), { type: "askLive", id: cfg.sid, ask: vc.endAt.ask });
      await page.waitForFunction(() => !!document.querySelector("#live-ask .ask-check input"), null, { timeout: 10000 }); await painted();
    }
    // the first variant stands where the family's climb left the reader, near the loaded run's top, far back already: each class's own
    // road pressed End from there, on a page fresh from its climb, with no collection forced on it. Every later one goes back far first
    const back = k ? await goBack() : null;
    if (back) {
      // open tool rows the go-back's window rendered again (a fold's open state outlives its row's leaving the window) closed too
      cleared.foldsAfterGoBack = await closeFolds();
      if (cleared.foldsAfterGoBack) { await painted(); const l = await leftOver(); if (l.open) throw new Error("an open tool row stayed open after the go-back: " + JSON.stringify(l)); }
    }
    // the page settled before the road (settlePage): what the go-back's last write, a fold closed after it or, for the first variant, the
    // climb's last page scheduled has run, so the road picks and presses on the rows it built
    await settlePage(vc.name + ": before its road");
    const climbed = Object.assign(await reading(), { measure: await measure() });
    let toggle = null, comment = null, clickAt = null;
    if (vc.endAt) { /* no click: stageEnd reaches the element */ }
    else if (vc.endPress === "toggle") {
      // the times of the last selectionchange and the last pointerdown, read by listeners added after the page's own
      await page.evaluate(() => { window.__selT = -1; window.__pressT = -1;
        window.addEventListener("selectionchange", (e) => { window.__selT = e.timeStamp; }, true);
        window.addEventListener("pointerdown", (e) => { window.__pressT = e.timeStamp; }, true); });
      if (vc.endType) { await page.click("#composer-input"); await page.keyboard.type(vc.endType); await painted(); await page.waitForTimeout(300); }
      // the first tool row's fold toggle wholly on screen, the selection's ranges counted before the click, the click's press recorded
      // (window.__press, the pressed node; window.__pressRange, a live range at its start, as the page's End handler holds its last
      // press: the DOM moves it to a removed node's parent, so a re-window that rebuilds the row after the press leaves it in the
      // transcript, where End still reads it)
      toggle = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
        const t = Array.from(c.querySelectorAll(".tool-fold-toggle")).find((e) => { const b = e.getBoundingClientRect(); return b.height > 0 && b.top >= cr.top && b.bottom <= cr.bottom; });
        window.__press = null; window.__pressRange = null;
        window.addEventListener("pointerdown", (e) => { window.__press = e.target; window.__pressRange = document.createRange(); window.__pressRange.setStart(e.target, 0); }, true);
        if (!t) return null; const b = t.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2, ranges: document.getSelection().rangeCount }; });
      if (toggle) await page.mouse.click(toggle.x, toggle.y);
      // vc.endAfter, after the click: "collapse" (the selection collapsed on the strip's first tab by a script), "selectAll" (Ctrl+A), or
      // "comment" (a word of a reply on screen double-clicked, the comment key pressed, two words typed in its box, then Escape)
      if (toggle && vc.endAfter) {
        await painted(); await page.waitForTimeout(300);
        if (vc.endAfter === "collapse") await page.evaluate(() => { const tab = document.querySelector("#tabs .tab");
          const w = document.createTreeWalker(tab, NodeFilter.SHOW_TEXT, { acceptNode: (n) => n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP });
          document.getSelection().collapse(w.nextNode(), 1); });
        else if (vc.endAfter === "selectAll") await page.keyboard.press("Control+A");
        else {
          // (read before Escape: the click count of each press of the double click, whether the last press lies in the reply's paragraph,
          // and the comment box, its focus and its text. The page settled after the toggle's click first: a tool row it opened can move
          // the view, and the re-window that follows near an edge rebuilds the rows. The paragraph is held by keys the page renders, its
          // row's data-uuid and its place among that row's paragraphs, so a re-window between this task and the double click, which
          // rebuilds the row under the pointer, still names the paragraph the double click lands on. The press is the node the double
          // click pressed, read by the same keys; a re-window after the double click leaves it in the removed row, whose keys read the
          // same, as a containment check did. Whether the last press lies in the transcript is read after Escape, off window.__pressRange)
          await settlePage(vc.name + ": the toggle's click, before the double click");
          const w = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
            const ps = Array.from(c.querySelectorAll(".turn-assistant .md p")).filter((p) => { const b = p.getBoundingClientRect(); return b.height > 0 && b.top >= cr.top + 20 && b.bottom <= cr.bottom - 20 && /^Answer \d+/.test(p.textContent); });
            const p = ps[Math.floor(ps.length / 2)]; if (!p) return null;
            window.__labWordOf = (e) => { const q = e.closest(".md p"), row = q && q.closest(".turn[data-uuid]"); return row ? row.getAttribute("data-uuid") + " " + Array.from(row.querySelectorAll(".md p")).indexOf(q) : null; };
            window.__labWord = window.__labWordOf(p); window.__downs = []; window.addEventListener("mousedown", (e) => { window.__downs.push(e.detail); }, true);
            const r = document.createRange(), t = document.createTreeWalker(p, NodeFilter.SHOW_TEXT).nextNode(); r.setStart(t, 0); r.setEnd(t, 6); const b = r.getBoundingClientRect();
            return { x: Math.round(b.left + 12), y: Math.round(b.top + b.height / 2) }; });
          if (w) { await page.mouse.dblclick(w.x, w.y); await painted(); await page.waitForTimeout(300); }
          await page.keyboard.press("Control+Slash"); await painted(); await page.waitForTimeout(600);
          await page.keyboard.type("a note"); await page.waitForTimeout(200);
          comment = await page.evaluate(() => { const b = document.querySelector("#cmt-pop .cmt-input"), p = window.__press, pe = p && (p.nodeType === 1 ? p : p.parentElement);
            return { downs: window.__downs || null, pressOnWord: !!pe && !!window.__labWord && window.__labWordOf(pe) === window.__labWord, opened: !!b, focused: !!b && document.activeElement === b, text: b ? b.value : null }; });
          await page.keyboard.press("Escape");
        }
        await painted(); await page.waitForTimeout(300);
      }
    } else {
      if (vc.endPageAfter) await page.evaluate(() => { window.__press = null; window.__pressRange = null;
        window.addEventListener("pointerdown", (e) => { window.__press = e.target; window.__pressRange = document.createRange(); window.__pressRange.setStart(e.target, 0); }, true); });
      clickAt = await plainPoint();
      await page.mouse.click(clickAt.x, clickAt.y);
    }
    // vc.endBox, after the click: a closed tool row whose output holds 200 lines opened, its box (one that scrolls on its own) named by its
    // row's data-uuid, so a window build that rebuilds the row still finds it, and brought into view at its top until the same element
    // stays there through a settle; then "shift" (a click on a word inside the box, then Shift+click on a word of a reply outside it),
    // "find" (a click on a word of a reply outside the box, then window.find of a line inside it), "toggle" (the pane scrolled so that the
    // nearest other tool row's fold toggle above the box stands 20 px under the top with the box's top lines on screen, a click on a word
    // inside the box, then a click on that toggle) or "toggleWrite" ("toggle", then the message box's text written by a script with an
    // input event). Read right before End and after it: focus, the selection's anchor and the last press against the box, which of the
    // last selectionchange and the last pointerdown came later, the last selectionchange's target, and the box's scroll
    let boxRoad = null, boxRead = null;
    if (vc.endBox) {
      await painted(); await page.waitForTimeout(300);
      await page.evaluate(() => { window.__selTarget = null;
        window.addEventListener("selectionchange", (e) => { const t = e.target; window.__selTarget = !t ? null : t === document ? "document" : t.tagName + (t.id ? "#" + t.id : ""); }, true);
        const J = window.__labBox = { row: null, tog: null };
        J.box = () => J.row && Array.from(document.querySelectorAll('#content .turn[data-uuid="' + J.row + '"] .tool-io-fold')).find((e) => e.offsetParent !== null && e.scrollHeight > e.clientHeight + 100) || null;
        J.toggle = () => J.tog && Array.from(document.querySelectorAll('#content .turn[data-uuid="' + J.tog + '"] .tool-fold-toggle')).find((e) => e.getClientRects().length > 0) || null; });
      // (the last press against the transcript and the box: where window.__pressRange sits, as the page's End handler reads its own;
      // on the toggle: the pressed node is a fold toggle's, and that press is in the transcript)
      boxRead = () => page.evaluate(() => { const c = document.getElementById("content"), s = document.getSelection(), a = s && s.anchorNode, ae = a && (a.nodeType === 1 ? a : a.parentElement);
        const p = window.__press, pe = p && (p.nodeType === 1 ? p : p.parentElement), b = window.__labBox.box();
        const r = window.__pressRange, re = r && (r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement), rin = !!re && c.contains(re);
        return { bodyFocused: document.activeElement === document.body, anchorInContent: !!ae && c.contains(ae), anchorInBox: !!b && !!ae && b.contains(ae),
                 pressInContent: rin, pressInBox: !!b && !!re && b.contains(re), pressOnToggle: rin && !!pe && !!pe.closest(".tool-fold-toggle"),
                 later: window.__pressT > window.__selT ? "press" : "selection", selTarget: window.__selTarget,
                 box: b ? { st: Math.round(b.scrollTop), max: b.scrollHeight - b.clientHeight, canScroll: b.scrollTop + b.clientHeight < b.scrollHeight - 1 } : null,
                 dist: Math.round(c.scrollHeight - c.scrollTop - c.clientHeight), ch: c.clientHeight }; });
      boxRoad = { road: vc.endBox };
      // the long output's fold toggle: one wholly on screen, else the one nearest the pane's middle brought there and picked again
      let lt = null;
      for (let i = 0; i < 3 && !lt; i++) {
        lt = await page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect();
          const ts = Array.from(c.querySelectorAll(".tool-fold-toggle")).filter((e) => e.getClientRects().length > 0 && /^200 lines$/.test(e.textContent.trim()) && !(e.closest(".turn") && e.closest(".turn").classList.contains("fold-open")));
          const on = ts.find((e) => { const b = e.getBoundingClientRect(); return b.top >= cr.top + 4 && b.bottom <= cr.bottom - 4; });
          const row = on && on.closest(".turn[data-uuid]");
          if (row) { const b = on.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2, row: row.getAttribute("data-uuid") }; }
          const mid = (e) => Math.abs(e.getBoundingClientRect().top - cr.top - cr.height / 2);
          const t = ts.sort((x, y) => mid(x) - mid(y))[0]; if (t) t.scrollIntoView({ block: "center" }); return null; });
        if (!lt) { await painted(); await page.waitForTimeout(1200); await painted(); await settlePage(vc.name + ": the scroll that brought a long output's fold toggle into view"); }
      }
      boxRoad.row = lt && lt.row;
      if (lt) { await page.evaluate((u) => { window.__labBox.row = u; }, lt.row); await page.mouse.click(lt.x, lt.y); await painted(); await page.waitForTimeout(600); await painted(); }
      // into view at its top until the same element is still wholly on screen after a settle
      boxRoad.settled = false;
      for (let i = 0; i < 8 && !boxRoad.settled; i++) {
        await page.evaluate(() => { const b = window.__labBox.box(); if (!b) return; b.__labSame = 1; b.scrollTop = 0;
          const r = b.getBoundingClientRect(), cr = document.getElementById("content").getBoundingClientRect();
          if (!(r.top >= cr.top + 20 && r.bottom <= cr.bottom - 20)) b.scrollIntoView({ block: "center" }); });
        await painted(); await page.waitForTimeout(1200); await painted(); await settlePage(vc.name + ": the scroll that brought the output box into view");
        boxRoad.settled = await page.evaluate(() => { const b = window.__labBox.box(); if (!b) return false;
          const r = b.getBoundingClientRect(), cr = document.getElementById("content").getBoundingClientRect();
          return b.__labSame === 1 && r.top >= cr.top + 20 && r.bottom <= cr.bottom - 20 && b.scrollTop === 0; });
      }
      if (vc.endBox === "toggle" || vc.endBox === "toggleWrite") {
        // the nearest other tool row's fold toggle above the box (not a 200-line one), named by its row's data-uuid; the pane scrolled so it
        // stands 20 px under the top with the box's top lines on screen, until both still stand so after a settle
        boxRoad.arranged = false;
        for (let i = 0; i < 6 && !boxRoad.arranged; i++) {
          await page.evaluate(() => { const J = window.__labBox, c = document.getElementById("content"), cr = c.getBoundingClientRect(), b = J.box(); if (!b) return;
            b.__labSame = 1; b.scrollTop = 0;
            const row = b.closest(".turn"), br = b.getBoundingClientRect();
            let t = J.toggle();
            if (!t) {
              t = Array.from(c.querySelectorAll(".tool-fold-toggle")).filter((e) => e.getClientRects().length > 0 && !(row && row.contains(e)) && !/^200 lines$/.test(e.textContent.trim()) && e.getBoundingClientRect().bottom <= br.top)
                .sort((x, y) => y.getBoundingClientRect().top - x.getBoundingClientRect().top)[0] || null;
              const tr = t && t.closest(".turn[data-uuid]"); J.tog = tr ? tr.getAttribute("data-uuid") : null; if (!J.tog) return;
            }
            const tt = t.getBoundingClientRect().top;
            if (Math.abs(tt - (cr.top + 20)) > 2) c.scrollTop += tt - (cr.top + 20); });
          await painted(); await page.waitForTimeout(1200); await painted(); await settlePage(vc.name + ": the scroll that put a fold toggle above the output box");
          boxRoad.arranged = await page.evaluate(() => { const J = window.__labBox, cr = document.getElementById("content").getBoundingClientRect(), b = J.box(), t = J.toggle();
            if (!b || !t) return false; const br = b.getBoundingClientRect(), tr = t.getBoundingClientRect();
            return b.__labSame === 1 && tr.top >= cr.top + 4 && tr.bottom <= cr.bottom - 4 && br.top >= cr.top + 4 && br.top + 80 <= cr.bottom - 4 && b.scrollTop === 0; });
        }
      }
      await page.evaluate(() => document.getSelection().removeAllRanges());
      // a word inside the box near its top (" line 3:"), and a word of a reply on screen outside the box
      const inBox = async () => {
        const w = boxRoad.inBox = await page.evaluate(() => { const b = window.__labBox.box(); if (!b) return null;
          const bb = b.getBoundingClientRect(), cr = document.getElementById("content").getBoundingClientRect(), tw = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
          for (let n = tw.nextNode(); n; n = tw.nextNode()) { const i = n.textContent.indexOf(" line 3:"); if (i < 0) continue;
            const r = document.createRange(); r.setStart(n, i + 1); r.setEnd(n, i + 5); const q = r.getBoundingClientRect();
            if (q.height > 0 && q.top >= bb.top && q.bottom <= bb.bottom && q.top >= cr.top && q.bottom <= cr.bottom) return { x: Math.round(q.left + 6), y: Math.round(q.top + q.height / 2) }; }
          return null; });
        if (w) { await page.mouse.click(w.x, w.y); await painted(); await page.waitForTimeout(300); }
      };
      const outBox = () => page.evaluate(() => { const c = document.getElementById("content"), cr = c.getBoundingClientRect(), b = window.__labBox.box(), bb = b ? b.getBoundingClientRect() : null;
        const ps = Array.from(c.querySelectorAll(".turn-assistant .md p")).filter((p) => { const q = p.getBoundingClientRect();
          return q.height > 0 && q.top >= cr.top + 20 && q.bottom <= cr.bottom - 20 && /^Answer \d+/.test(p.textContent) && (!bb || q.bottom < bb.top - 4 || q.top > bb.bottom + 4); });
        const p = ps[Math.floor(ps.length / 2)]; if (!p) return null;
        const n = document.createTreeWalker(p, NodeFilter.SHOW_TEXT).nextNode(), r = document.createRange(); r.setStart(n, 0); r.setEnd(n, 6); const q = r.getBoundingClientRect();
        return { x: Math.round(q.left + 12), y: Math.round(q.top + q.height / 2) }; });
      if (vc.endBox === "shift") {
        await inBox();
        const w = boxRoad.out = await outBox();
        if (w) { await page.keyboard.down("Shift"); await page.mouse.click(w.x, w.y); await page.keyboard.up("Shift"); await painted(); await page.waitForTimeout(300); }
      } else if (vc.endBox === "find") {
        const w = boxRoad.out = await outBox();
        if (w) { await page.mouse.click(w.x, w.y); await painted(); await page.waitForTimeout(300); }
        boxRoad.found = await page.evaluate(() => { const b = window.__labBox.box(), m = b && /note (\d+) line 1:/.exec(b.textContent); return m ? window.find("note " + m[1] + " line 120:", false, false, true) : null; });
        await painted(); await page.waitForTimeout(600); await painted();
      } else {
        await inBox();
        const t = boxRoad.toggle = await page.evaluate(() => { const t = window.__labBox.toggle(); if (!t) return null; const b = t.getBoundingClientRect(); return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) }; });
        if (t) { await page.mouse.click(t.x, t.y); await painted(); await page.waitForTimeout(300); }
        if (vc.endBox === "toggleWrite") {
          await page.evaluate(() => { const ta = document.getElementById("composer-input"); ta.value = "a draft"; ta.dispatchEvent(new Event("input", { bubbles: true })); });
          await painted(); await page.waitForTimeout(300);
        }
      }
      boxRoad.before = await boxRead();
    }
    // where the click left focus, the selection's anchor and the last press (where window.__pressRange sits, as the page's End handler
    // reads its own; on the toggle: the pressed node is a fold toggle's, and that press is in the transcript) (vc.endAt: the staged
    // element's reading)
    const clicked = vc.endAt ? await stageEnd(vc.endAt, vc.name) : await page.evaluate(() => { const c = document.getElementById("content"), a = document.getSelection()?.anchorNode; const ae = a && (a.nodeType === 1 ? a : a.parentElement);
      const p = window.__press, pe = p && (p.nodeType === 1 ? p : p.parentElement);
      const r = window.__pressRange, re = r && (r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement), rin = !!re && c.contains(re);
      return { bodyFocused: document.activeElement === document.body, anchor: !!a, anchorInContent: !!ae && c.contains(ae), anchorInFooter: !!ae && !!ae.closest("#footer"),
               pressOnToggle: rin && !!pe && !!pe.closest(".tool-fold-toggle"), pressInContent: rin,
               selT: window.__selT ?? null, pressT: window.__pressT ?? null }; });
    clicked.toggle = toggle;
    if (clickAt) clicked.clickAt = clickAt;
    if (comment) clicked.comment = comment;
    if (boxRoad) clicked.box = boxRoad;
    if (vc.endUnselect) clicked.unselected = await page.evaluate(() => { document.getSelection().removeAllRanges(); return !document.getSelection().anchorNode; });
    if (vc.endPageAfter) {
      // one more history page between the click and End, climbed as above; then whether the element the click pressed is still in the
      // document
      const n = (await asks()).length, before = await measure();
      holdTurns = true;
      let w = 0;
      while (w < 80 && (await asks()).length === n) { await page.mouse.wheel(0, -1200); await painted(); w++; }
      await page.waitForTimeout(150); await painted();
      release();
      await loaded(); await painted(); await page.waitForTimeout(150); await painted();
      clicked.after = { wheels: w, asked: (await asks()).slice(n), before, measure: await measure(),
                        gapLoading: await page.evaluate(() => window.__rompAskState().gapLoading),
                        pressConnected: await page.evaluate(() => window.__press ? window.__press.isConnected : null) };
      // the page this climb loaded moved the run's top: read it again while the head gap stands drawn above the reader, for the next
      // variant's go-back
      const rt = await readRunTop();
      if (rt) runTop = rt;
      clicked.after.runTop = rt;
    }
    // the page quiet (quiet), before the play control's scroll listener is armed and End is pressed
    const still = await quiet();
    // (vc.endAt.scrolls: where focus and the element sit right before End, and the other media elements of its reply, then every #content
    // scroll event from the press on, read by a listener added after the page's own: its time from the press, the position in that event
    // and whether focus was in a media element)
    if (vc.endAt && vc.endAt.scrolls) clicked.armed = await page.evaluate(() => {
      const c = document.getElementById("content"), t = window.__labEnd && window.__labEnd.node, J = window.__endScrolls = { t0: performance.now(), events: [] };
      J.on = () => { const f = document.activeElement;
        J.events.push({ t: Math.round(performance.now() - J.t0), st: c.scrollTop, ch: c.clientHeight, sh: c.scrollHeight, active: f ? f.tagName : null, inMedia: f instanceof HTMLMediaElement }); };
      c.addEventListener("scroll", J.on, { passive: true });
      const row = t && t.closest(".turn");
      return { focused: !!t && document.activeElement === t, connected: !!t && t.isConnected, dist: c.scrollHeight - c.scrollTop - c.clientHeight, ch: c.clientHeight,
               others: row ? Array.from(row.querySelectorAll("audio, video")).filter((m) => m !== t).map((m) => ({ tag: m.tagName, inMenu: !!m.closest('[role="menu"]'), controls: m.controls, focused: document.activeElement === m })) : null };
    });
    // whether the page took End: a window keydown listener added after the page's own reads End's default prevented (null when the key
    // never reaches the window)
    await page.evaluate(() => { window.__endTaken = null; window.addEventListener("keydown", (e) => { if (e.key === "End") window.__endTaken = e.defaultPrevented; }); });
    // (vc.endPageAfter: where focus and the selection sit when End is pressed, read by a window keydown listener in the capture phase,
    // before the page's own)
    if (vc.endPageAfter) await page.evaluate(() => { window.__atPress = null; window.addEventListener("keydown", (e) => {
      if (e.key === "End") window.__atPress = { bodyFocused: document.activeElement === document.body, anchor: !!document.getSelection().anchorNode }; }, true); });
    // (vc.endAt: the element its staging reached is still the one its row holds, right before the press; a re-window that rebuilt the
    // row since took focus, a press or a clip's state with the old element, and the road fails here, naming the class)
    if (vc.endAt) await page.evaluate(() => { window.__labEndAt("End"); });
    await page.keyboard.press("End");
    // the play-control class reads the transcript's scroll events across a fixed window of 4 s; every other class waits for the newest
    // row on screen within a screen of the bottom (endSettle)
    let settle = null;
    if (vc.endAt && vc.endAt.scrolls) await page.waitForTimeout(4000); else settle = await endSettle();
    await painted();
    clicked.taken = await page.evaluate(() => window.__endTaken);
    if (vc.endPageAfter) clicked.atPress = await page.evaluate(() => window.__atPress);
    if (boxRoad) boxRoad.after = await boxRead();
    if (vc.endAt) clicked.atEnd = await page.evaluate(() => window.__atEnd);
    // the scroll events, and, read with them, whether focus is in a media element (the listener removed after)
    if (vc.endAt && vc.endAt.scrolls) Object.assign(clicked, await page.evaluate(() => { const J = window.__endScrolls; document.getElementById("content").removeEventListener("scroll", J.on);
      return { scrolls: J.events, inMediaAtRead: document.activeElement instanceof HTMLMediaElement }; }));
    // where the newest row ends, in px above the bottom of the transcript's pane (null when it is not rendered)
    const bottomPx = await page.evaluate((u) => { const c = document.getElementById("content"), r = document.querySelector(`#content .turn[data-uuid="${u}"]`);
      return r ? Math.round(c.getBoundingClientRect().bottom - r.getBoundingClientRect().bottom) : null; }, cfg.newest);
    if (dropAskClear) clicked.askClearsDropped = askClearsDropped;
    const pressed = Object.assign(await reading(), { measure: await measure(), bottomPx });
    return { cleared, back, climbed, clicked, quiet: still, settle, pressed, pressTasks: await pressTasks() };
  };
  out.end.heap = await metrics();
  // the errors every variant reads end here, with no await before the first variant's own window opens: those raised during the boot,
  // the drop, the climb and the setup after it (out.pageErrors up to nShared); each variant's window then runs from its start to its
  // last reading, the next one's opening where it closes, and those after the last variant (from lastErr) are every variant's too
  out.end.nShared = out.end.lastErr = pageErrors.length;
  const T0 = Date.now();
  for (const [k, vc] of variants.entries()) {
    const e0 = pageErrors.length, t0 = Date.now() - T0;
    let rec;
    try { rec = await runVariant(vc, k); }
    catch (err) {
      rec = { error: String((err && err.stack) || err).slice(0, 3000) };
      release(); await page.keyboard.up("Shift").catch(() => {});   // a staging cut short leaves no reply held at the wire, no key down
    }
    await painted();
    out.end.lastErr = pageErrors.length;
    out.end.variants[vc.name] = Object.assign(rec, { pageErrors: pageErrors.slice(e0, out.end.lastErr), ms: [t0, Date.now() - T0] });
  }
  await cdp.detach().catch(() => {});
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
    """The lab, mixed into each TestCase below and into the End families of tests/test_long_history_end_browser.py (a mixin, so the
    runner collects no lab of its own): a hermetic kernel over the synthetic transcript, the real /chat page served from a copy of the
    built bundle, driven at the class's SCALE and VIEWPORT with the class's LEGS."""
    maxDiff = None
    SCALE = 2
    VIEWPORT = {"width": 1000, "height": 600}
    TALL_LINES = 600      # lines in each tall reply's code block (~18 px each)
    LEGS = ()
    BOOT = "roads"        # before the LEGS: "roads" (ROADs 1 to 3, for the classes that read the landing, the jump to the end and the
    #                       idle window), "measure" (a wait for the first measure) or "hidden" (a wait for ROAD 10's hidden prebuild)
    GAPS_HELD = None      # px: the gaps' bound the page is held at from its boot (the served labs' hook), else the measured share
    CLIMB_PAGES = 0       # the history pages the climb's wheel loads after the drop's own page, before it presses the go-to-bottom chip
    APPEND_TURNS = 0      # ROAD 9: the turns the transcript grows by in one write after the climb's presses and one first turn
    APPEND_INTO = 2       # where ROAD 9's reader drops: this many units under the top of the tail run
    END_BACK_UNITS = 0    # ROAD 8: after the chip's presses, the reader goes back this many units under the top of the run the climb
    #                       loaded and presses End (0: no End)
    SECOND_SESSION = False   # ROAD 10: a second, short session (SID2) beside the long one
    END_PAGES = 0         # ROAD 11: the history pages the wheel climbs before the click (with END_AT, before the element is reached);
    #                       an End family climbs its longest member's, and each class reads the first END_PAGES steps of that climb
    END_PRESS = "text"    # ROAD 11: what the click before End lands on: "text" (the transcript's centre) or "toggle" (a tool row's fold toggle)
    END_TYPE = None       # ROAD 11 with END_PRESS "toggle": words typed in the message box before the click
    END_AFTER = None      # ROAD 11 with END_PRESS "toggle": a step after the click (the driver's endAfter)
    END_UNSELECT = False     # ROAD 11: the selection emptied after the click
    END_PAGE_AFTER = False   # ROAD 11: one more history page asked and filled between the click and End
    HISTORY_TOOLS = False   # each short turn's reply opens with a Bash call: a tool row with a fold toggle
    END_MARKUP = ""     # ROAD 11: a reply's own markup, for END_AT to reach (an End family rotates its classes' markups across the
    #                     short answers: _markup)
    END_AT = None       # ROAD 11: instead of the click, the element End meets and how it is reached (the driver's stageEnd)
    END_MEDIA = False   # ROAD 11: serve the clips a reply's audio plays (clip.wav, three seconds; bad.wav, which decodes as nothing)
    END_BOX = None      # ROAD 11 with END_PRESS "toggle": a road in a tool output box after the click (the driver's endBox)
    LONG_OUTPUT = False   # with HISTORY_TOOLS: every eighth short turn's Bash call prints 200 lines
    BARS = False        # classic scrollbars (the browser launched without --hide-scrollbars)
    TAIL_TOOLS = False  # each tall turn's reply opens with two Bash calls: a tool run, which compact mode folds into one row with a toggle
    KEEP_REPLIES = ()   # ROAD 12: the replies that land below the window before each expand and collapse of a tool run, leg by leg
    KEEP_FIRST = 0.15   # ROAD 12: where the reader drops first, as a share of the units the page holds (in the top spacer the landing left)
    KEEP_AT = 0.7       # …and then (in the bottom spacer the first drop left): the window it builds holds the tail run's lower units

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here) — the served lab needs them")
        cls._boot()

    @classmethod
    def _markup(cls, k):
        """A reply's own markup for short answer k: none here (tests/test_long_history_end_browser.py's families rotate their classes'
        END_MARKUP across the short answers)."""
        return ""

    @classmethod
    def _boot(cls):
        """The lab: a copy of the built bundle, the synthetic transcript and its state root, and a hermetic kernel serving them. The
        bundle is built once per module (_BUILD), each lab copying it."""
        cls.lab = tempfile.mkdtemp(prefix="long-history-height-")
        if not _BUILD:
            _BUILD.append(subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True))
        b = _BUILD[0]
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
                "Answer %d: the handler reads the note by id and returns it." % k + ("\n\n" + cls._markup(k) if cls._markup(k) else "")
            if cls.HISTORY_TOOLS and k < TURNS - TALL:
                t, r = "55555555-6666-7777-8888-%012d" % k, "66666666-7777-8888-9999-%012d" % k
                recs.append({"type": "assistant", "uuid": t, "parentUuid": u, "timestamp": ta, "sessionId": SID,
                             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                                         "content": [{"type": "tool_use", "id": "toolu_%d" % k, "name": "Bash",
                                                      "input": {"command": "python3 notes.py --show %d" % k}}]}})
                recs.append({"type": "user", "uuid": r, "parentUuid": t, "timestamp": ta, "sessionId": SID,
                             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_%d" % k,
                                                                      "content": "".join("note %d line %d: the notes table row was read\n" % (k, i) for i in range(1, 201))
                                                                      if cls.LONG_OUTPUT and k % 8 == 0 else "note %d\ntitle: notes api\nstatus: ok" % k}]}})
                u = r
            if cls.TAIL_TOOLS and k >= TURNS - TALL:
                for j in range(2):   # two Bash calls in a row, each answered: one tool run
                    t, r = "77777777-8888-9999-aaaa-%012d" % (2 * k + j), "88888888-9999-aaaa-bbbb-%012d" % (2 * k + j)
                    recs.append({"type": "assistant", "uuid": t, "parentUuid": u, "timestamp": ta, "sessionId": SID,
                                 "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                                             "content": [{"type": "tool_use", "id": "toolu_t%d_%d" % (k, j), "name": "Bash",
                                                          "input": {"command": "python3 notes.py --index %d --part %d" % (k, j)}}]}})
                    recs.append({"type": "user", "uuid": r, "parentUuid": t, "timestamp": ta, "sessionId": SID,
                                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_t%d_%d" % (k, j),
                                                                          "content": "index %d part %d: ok" % (k, j)}]}})
                    u = r
            recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ta, "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": text}]}})
            prev = a
        cls.transcript = os.path.join(proj, SID + ".jsonl")
        Path(cls.transcript).write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.newest = "22222222-3333-4444-5555-%012d" % (2 * TURNS - 1)
        more = []   # the turns ROAD 9 appends (one, then APPEND_TURNS), chained on from the last; their replies are short (a unit below
        #             the window stands for the held figure whatever its height, and short rows keep the page's memory down)
        for k in range(TURNS, TURNS + 1 + cls.APPEND_TURNS if cls.APPEND_TURNS else TURNS):
            u = "11111111-2222-3333-4444-%012d" % (2 * k)
            a = "22222222-3333-4444-5555-%012d" % (2 * k + 1)
            more.append({"type": "user", "uuid": u, "parentUuid": prev, "sessionId": SID,
                         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(BASE + 2 * k)),
                         "message": {"role": "user", "content": "question number %d about the notes api" % k}})
            more.append({"type": "assistant", "uuid": a, "parentUuid": u, "sessionId": SID,
                         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(BASE + 2 * k + 1)),
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": "Answer %d: the handler reads the note by id and returns it." % k}]}})
            prev = a
        cls.append_first = os.path.join(cls.lab, "append-first.jsonl")
        Path(cls.append_first).write_text("".join(json.dumps(r) + "\n" for r in more[:2]))
        cls.append_lines = os.path.join(cls.lab, "append.jsonl")
        Path(cls.append_lines).write_text("".join(json.dumps(r) + "\n" for r in more[2:]))
        cls.append_newest = more[-1]["uuid"] if more else None
        # the turns ROAD 12 lands, leg by leg (KEEP_REPLIES), chained on from the transcript's last reply; short replies, as ROAD 9's
        assert not (cls.KEEP_REPLIES and cls.APPEND_TURNS), "ROAD 9 and ROAD 12 each chain their turns on from the transcript's last reply"
        cls.keep_legs, k = [], TURNS
        for n in cls.KEEP_REPLIES:
            leg = []
            for _ in range(n):
                u = "11111111-2222-3333-4444-%012d" % (2 * k)
                a = "22222222-3333-4444-5555-%012d" % (2 * k + 1)
                leg.append({"type": "user", "uuid": u, "parentUuid": prev, "sessionId": SID,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(BASE + 2 * k)),
                            "message": {"role": "user", "content": "question number %d about the notes api" % k}})
                leg.append({"type": "assistant", "uuid": a, "parentUuid": u, "sessionId": SID,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(BASE + 2 * k + 1)),
                            "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                        "content": [{"type": "text", "text": "Answer %d: the handler reads the note by id and returns it." % k}]}})
                prev, k = a, k + 1
            path = os.path.join(cls.lab, "keep-%d.jsonl" % len(cls.keep_legs))
            Path(path).write_text("".join(json.dumps(r) + "\n" for r in leg))
            cls.keep_legs.append({"file": path, "replies": n, "units": 2 * n})   # a prompt and a text reply: two display units a turn
        # the reply ROAD 9 lands last, chained on the last appended one by the driver with its turn left open: a tall reply, as the
        # tail's are, taller than the held figure
        kt = TURNS + 1 + cls.APPEND_TURNS
        cls.append_tall = {"uuid": "22222222-3333-4444-5555-%012d" % (2 * kt + 1), "text": "Answer %d: the notes index, row by row.\n\n" % kt + tall}
        if cls.SECOND_SESSION:   # "api": a few short turns, every one newer than the long session's last
            Path(state, "names", SID2).write_text("api\t%s\t\t\n" % cwd)
            Path(state, "sdk", SID2 + ".json").write_text(json.dumps(
                {"sid": SID2, "name": "api", "cwd": cwd, "mode": "auto", "effort": "high",
                 "lastSid": SID2, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
            short, prev2, base2 = [], None, BASE + 2 * (TURNS + 1 + cls.APPEND_TURNS) + 1000
            for k in range(SHORT_TURNS):
                u = "33333333-4444-5555-6666-%012d" % (2 * k)
                a = "44444444-5555-6666-7777-%012d" % (2 * k + 1)
                short.append({"type": "user", "uuid": u, "parentUuid": prev2, "sessionId": SID2,
                              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base2 + 2 * k)),
                              "message": {"role": "user", "content": "check %d on the notes api tests" % k}})
                short.append({"type": "assistant", "uuid": a, "parentUuid": u, "sessionId": SID2,
                              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base2 + 2 * k + 1)),
                              "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                          "content": [{"type": "text", "text": "Result %d: the test passes." % k}]}})
                prev2 = a
            Path(proj, SID2 + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in short))
        cls.media = None
        if cls.END_MEDIA:
            cls.media = {"clip.wav": os.path.join(cls.lab, "clip.wav"), "bad.wav": os.path.join(cls.lab, "bad.wav")}
            with wave.open(cls.media["clip.wav"], "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(3 * 8000)))
            Path(cls.media["bad.wav"]).write_bytes(b"RIFF" + struct.pack("<I", 4036) + b"WAVEjunk" + bytes((i * 37) % 251 for i in range(4028)))
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
        cls._failed = None        # the driver's failure, once it has failed (_result)
        cls._climb_early = None   # the climb's record, printed before ROAD 9 (_climb)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @property
    def limit(self):
        return LAYOUT_LIMIT_1X // self.SCALE

    @classmethod
    def _cfg(cls):
        """The driver's cfg for a run of this lab (an End family's run adds its own: tests/test_long_history_end_browser.py)."""
        return {"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "newest": cls.newest,
                "scale": cls.SCALE, "viewport": cls.VIEWPORT, "legs": list(cls.LEGS), "boot": cls.BOOT, "sid": SID, "sid2": SID2, "base": BASE, "jumpTurn": JUMP_TURN,
                "pageTurns": PAGE_TURNS, "gapsHeld": cls.GAPS_HELD,
                "climbPages": cls.CLIMB_PAGES, "endBackUnits": cls.END_BACK_UNITS, "endMedia": cls.media, "bars": cls.BARS,
                # every appended event is a display unit of its own here (a prompt, a text reply)
                "append": {"transcript": cls.transcript, "first": cls.append_first, "lines": cls.append_lines,
                           "units": 2 * cls.APPEND_TURNS, "newest": cls.append_newest, "into": cls.APPEND_INTO, "tall": cls.append_tall}
                if cls.APPEND_TURNS else None,
                "keep": {"transcript": cls.transcript, "first": cls.KEEP_FIRST, "at": cls.KEEP_AT, "legs": cls.keep_legs} if cls.KEEP_REPLIES else None}

    @classmethod
    def _drive(cls, cfg, timeout):
        """One run of the driver over cfg in this lab, killed after `timeout` s: (its result, None, the climb's record) or (None, how it
        failed, the climb's record), the climb's record being the CLIMB line ROAD 8 prints before ROAD 9, or None. A driver that exits 3
        found no browser: the run skips (_skip)."""
        path = os.path.join(cls.lab, "cfg.json")
        with open(path, "w") as f:
            json.dump(cfg, f)
        driver = os.path.join(cls.lab, "drive.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=timeout,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=path))
            rc, stdout, stderr = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:   # the output so far comes back as bytes
            rc = "none: killed after %d s" % timeout
            stdout, stderr = ((b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")) for b in (e.stdout, e.stderr))
        if rc == 3:
            cls._skip("no playwright browser on this box: the served lab needs one")
        klog = ""
        try:
            with open(cls.klog) as f:
                klog = f.read()[-2500:]
        except OSError:
            pass
        lines = stdout.splitlines()
        early = next((ln for ln in lines if ln.startswith("CLIMB:")), None)
        try:
            climb = json.loads(early[len("CLIMB:"):]) if early else None
        except ValueError:   # cut off mid-line by a kill
            climb = None
        line = next((ln for ln in lines if ln.startswith("RESULT:")), None)
        if rc != 0 or line is None:
            shown = "\n".join(ln for ln in lines if not ln.startswith(("CLIMB:", "RESULT:")))   # the records are read, not shown
            return None, (("driver failed (exit %s)" % rc) if rc != 0 else "driver printed no result") + ":\n" + shown[-3000:] + "\n" \
                + stderr[-3000:] + "\nkernel:\n" + klog, climb
        return json.loads(line[len("RESULT:"):]), None, climb

    def _result(self):
        cls = type(self)
        if cls._r is not None:
            return cls._r
        if cls._failed is not None:
            # a stored failure (ROAD 9 appends to the transcript, so a second run would drive another fixture): every later call fails
            # with it
            self.fail("an earlier driver run failed (not re-run): " + cls._failed)
        r, failed, cls._climb_early = cls._drive(cls._cfg(), 300)
        if failed is not None:
            cls._failed = failed
            self.fail(cls._failed)
        cls._r = r
        print("LONG-HISTORY:", cls.__name__, json.dumps(cls._r), file=sys.stderr)
        return cls._r

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

    def test_the_head_gap_at_the_measured_px_per_turn_would_stand_past_the_layout_limit(self):
        r = self._result()
        self.assertLessEqual(r["limitProbe"], self.limit, "the %dx scale took: a box asked for 20,000,000 px lays out at most the limit: %r" % (self.SCALE, r["limitProbe"]))
        m = r["landed"]["measure"]
        self.assertTrue(m and m["pxPerTurn"], "the page measured a px per turn: %r" % m)
        widest = max((x["hi"] - x["lo"] for x in r["landed"]["regions"] or [] if x["kind"] == "gap"), default=0)
        self.assertGreater(widest * m["pxPerTurn"], 1.2 * self.limit, "the head gap at the measured figure stands past the limit with a margin: %d turns x %.1f px" % (widest, m["pxPerTurn"]))

    def test_a_row_hidden_at_the_landing_takes_the_measured_average_unit(self):
        # chat-regions.ts heldUnitPx lowers the figure only when the gaps and the units the page holds, at the average unit, would stand
        # past half the limit. The figure a hidden row takes is render.ts unitPx's: the held figure, or the average where none is set
        m = self._result()["landed"]["measure"]
        self.assertTrue(m, "the page reports its view's measures (render.ts __rompViewMeasure): %r" % m)
        self.assertGreater(m["winStart"], 1, "rows are hidden above the window, past the head gap's one unit: %r" % m)
        self.assertLess(m["units"] * m["avgUnit"], self.limit / 5, "the units the page holds, at the average unit it measured, stand under a fifth of the limit: %r" % m)
        figure = m["heldPx"] if m["heldPx"] is not None else m["avgUnit"]
        self.assertEqual(figure, m["avgUnit"], "a row hidden in a spacer takes the measured average unit: %r" % m)

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


class LongHistoryClimb(_LongHistoryLab, unittest.TestCase):
    SCALE = 2
    TALL_LINES = 2400     # an average unit of ~21,600 px off the tall tail: each page the climb loads holds 32 rows
    LEGS = ("climb",)
    BOOT = "measure"      # it reads none of ROADs 1 to 3
    CLIMB_PAGES = 24
    APPEND_TURNS = 100    # 200 units, past the 140 the window renders, so at the held figure they carry the estimates past the half
    END_BACK_UNITS = 200  # the run the climb loads holds about 800 units: a window built 200 units under its top holds neither gap

    def _climb(self):
        """The climb's record, its premise checked first, so each test that reads the climb fails on a short or misplaced climb by
        itself (run alone, or with the premise test gone): the drop lands inside the head gap, the wheel loads CLIMB_PAGES pages, one
        asked per step, and the units the page then holds, at the average unit it measured, stand past the limit by more than a fifth.
        When the driver failed after the climb (in ROAD 9), the record it printed before that road is the climb's record, so each test
        that reads the climb alone still reads it; the two tests that read ROAD 9 too (the append test and the held-figure test) and the
        page-error test report the failure."""
        try:
            c = self._result()["climb"]
        except AssertionError:
            c = type(self)._climb_early
            if c is None:
                raise
            print("LONG-HISTORY:", type(self).__name__, self._testMethodName, "reads the climb record the failed driver printed before ROAD 9", file=sys.stderr)
        self.assertTrue(c["drop"]["gap"] and c["drop"]["gap"]["turnAtTop"] is not None, "the drop lands inside the head gap: %r" % self._brief(c["drop"]))
        self.assertEqual(len(c["steps"]), self.CLIMB_PAGES, "the wheel climb loads %d pages, one per step: %r" % (self.CLIMB_PAGES, c["steps"]))
        self.assertEqual([len(x["asked"]) for x in c["steps"]], [1] * self.CLIMB_PAGES, "…each step asking one page: %r" % c["steps"])
        m = c["climbed"]["measure"]
        self.assertTrue(m, "the page reports its view's measures (render.ts __rompViewMeasure): %r" % m)
        self.assertGreater(m["units"] * m["avgUnit"], 1.2 * self.limit, "the units the page holds, at the average unit it measured, stand past the limit with a margin: %r" % m)
        return c

    def test_the_climb_holds_rows_whose_estimate_at_the_measured_average_would_pass_the_limit(self):
        self._climb()

    def test_the_scroll_height_stays_under_the_layout_limit_through_the_climb(self):
        c = self._climb()
        sh = [x["sh"] for x in c["steps"]] + [c["chip1"]["sh"], c["chip2"]["sh"]]
        self.assertLess(max(sh), self.limit, "the thread fits the tallest box Chrome lays out at this scale, page by page: %r" % sh)

    def test_the_estimates_stand_at_most_half_the_layout_limit_through_the_climb(self):
        # chat-regions.ts HIDDEN_SHARE: at every reading of the climb and of the chip presses after it, the spacers and the gaps drawn
        # stand at most 4 px past half the limit
        c = self._climb()
        est = [x["est"] for x in c["steps"]] + [c["chip1"]["est"], c["chip2"]["est"]]
        self.assertTrue(all(e > 0 for e in est), "every reading holds a spacer or a gap: %r" % est)
        self.assertLessEqual(max(est), self.limit / 2 + 4, "the estimates in the scroll height, page by page: %r" % est)

    def test_the_go_to_bottom_chip_shows_the_newest_row_after_the_climb(self):
        c = self._climb()
        self.assertTrue(c["chip1"]["newestOnScreen"], "the newest row is on screen after the first press: %r" % self._brief(c["chip1"]))
        self.assertLessEqual(c["chip1"]["dist"], 2, "…at the bottom (the window the press builds dresses its rows after it lands, and a reader in follow mode stays at the bottom through that): %r" % self._brief(c["chip1"]))
        self.assertTrue(c["chip2"]["newestOnScreen"], "…and after the second: %r" % self._brief(c["chip2"]))
        self.assertLessEqual(c["chip2"]["dist"], 2, "…at the bottom: %r" % self._brief(c["chip2"]))

    def test_end_from_far_back_in_the_climbed_history_lands_at_the_bottom(self):
        # End takes the chip's road (render.ts goToBottom); its first press, from far back in the history the climb loaded with the
        # tail hidden in the bottom spacer, lands at the bottom too
        c = self._climb()
        e = c["end1"]
        self.assertIsNotNone(e, "the class pressed End after the climb")
        b = e["back"]
        self.assertTrue(b["met"], "the go-back's write put the climbed run's rows on screen within 10 s (none when no run's top was read): %r" % self._brief(b))
        self.assertTrue(b["asks"] == [] and not b["newestRendered"] and b["dist"] > 100 * b["ch"],
                        "the go-back stands in the climbed run, more than a hundred screens from the bottom, the newest row not rendered, no page asked: %r" % self._brief(b))
        self.assertEqual(b["measure"]["units"], c["chip2"]["measure"]["units"], "no unit landed in the go-back: %r" % b["measure"])
        k = e["clicked"]
        self.assertTrue(k["at"] and k["at"]["onText"] and k["bodyFocused"] and k["anchorInContent"],
                        "the click landed on a reply's text, focus on the page body and the selection's anchor in the transcript: %r" % k)
        self.assertIs(e["taken"], True, "the page took End: %r" % self._brief(e))
        self.assertTrue(e["newestOnScreen"], "the newest row is on screen after End: %r" % self._brief(e))
        self.assertLessEqual(e["dist"], 2, "…at the bottom: %r" % self._brief(e))

    def test_rows_appended_below_the_window_take_the_held_figure_until_the_next_build(self):
        # ROAD 9, the residual chat-regions.ts HIDDEN_SHARE and render.ts heldPxOf disclose: the keyed paint (tailDiff) keeps the held
        # figure, so the units it appends below the window are estimated at it, and can carry the estimates past the half, by at most
        # their count times it, until the next window build (a press of the chip here) sizes every hidden unit afresh.
        # The code before this change has no held figure, and over it this test fails before any check of the figure (without the
        # measure hook the driver fails; with only the hook added, the road's premise fails); a change that closes the residual
        # rewrites this test along with the disclosure.
        # The result first, then the climb's premise: when ROAD 9 fails and this test ran the driver (it is collected first), the
        # failure is reported as it came ("driver failed ..."); with _climb first, the climb's record would be read around it and this
        # test would raise the stored failure as one an earlier test met
        res = self._result()
        self._climb()
        b = res["climb"]["below"]   # ROAD 9's own record: a failure in the road fails this test, never read around
        self.assertIsNotNone(b, "the class appended replies after the climb")
        d, f, r, n = b["dropped"], b["first"], b["rest"], b["next"]
        md, mf, mr = d["measure"], f["measure"], r["measure"]
        # the road's premise: the first reply landed, units are hidden below the window, and the held figure is under the average
        self.assertEqual(mf["units"] - md["units"], 2, "the first reply landed: %r then %r" % (md, mf))
        self.assertTrue(mf["winEnd"] < mf["units"], "units are hidden below the window the reader stands in: %r" % mf)
        self.assertTrue(mf.get("heldPx") is not None and mf["heldPx"] < mf["avgUnit"], "the held figure is under the average unit: %r" % mf)
        half = self.limit / 2
        self.assertLessEqual(f["est"], half + 4, "after the first reply the estimates stand at most 4 px past the half: %r" % self._brief(f))
        k = mr["units"] - mf["units"]
        self.assertEqual(k, 2 * self.APPEND_TURNS, "every appended event landed as a unit of its own: %r then %r" % (mf, mr))
        self.assertEqual((mr["winStart"], mr["winEnd"]), (mf["winStart"], mf["winEnd"]), "the window stayed where it was: %r then %r" % (mf, mr))
        self.assertEqual(mr["heldPx"], mf["heldPx"], "the keyed paint keeps the held figure (a rebuild would recompute it, over the appended units too)")
        self.assertAlmostEqual(r["est"] - f["est"], k * mf["heldPx"], delta=2,
                               msg="the bottom spacer grew by the appended units at the held figure: %d then %d, %d units at %.2f px" % (f["est"], r["est"], k, mf["heldPx"]))
        self.assertGreater(r["est"], half, "the appended units carried the estimates past the half (the residual's state, reached): %d" % r["est"])
        self.assertLessEqual(r["est"], half + k * mf["heldPx"] + 4, "…by at most their count times the figure: %d" % r["est"])
        self.assertLessEqual(n["est"], half + 4, "the next window build (a press of the chip) brings the estimates back under the half: %r" % self._brief(n))
        self.assertLess(max(x["sh"] for x in (d, f, r, n)), self.limit, "the thread fits the tallest box through the road: %r" % [x["sh"] for x in (d, f, r, n)])

    def test_the_chip_lands_at_the_bottom_when_the_newest_reply_is_taller_than_the_held_figure(self):
        # ROAD 9's last press: the newest unit, hidden in the bottom spacer at the held figure, is a reply far taller than it. The
        # press's window build anchors the reader in that unit at their offset into its estimate, and a re-window that starts with the
        # reader in follow mode at the bottom ends with them there (render.ts virtualizeToViewport, 2026-10-15); before that the
        # reader stood tens of thousands of px inside the reply, the chip still shown.
        # The result first, then the climb's premise, as in the append test
        res = self._result()
        self._climb()
        b = res["climb"]["below"]
        self.assertIsNotNone(b, "the class appended replies after the climb")
        k, t, p = b["tall"]["back"], b["tall"]["landed"], b["tall"]["pressed"]
        self.assertTrue(k and k["met"] and not k["newestRendered"] and k["dist"] > 100 * k["ch"],
                        "the go-back put the climbed run's rows on screen, more than a hundred screens from the bottom: %r" % self._brief(k))
        self.assertTrue(t["last"] and not t["newestRendered"] and t["measure"]["winEnd"] < t["measure"]["units"],
                        "the tall reply landed as the session's last unit, the error card gone with its turn open, hidden in the bottom spacer: %r" % self._brief(t))
        self.assertEqual((p["measure"]["units"], p["measure"]["heldPx"]), (k["measure"]["units"], t["measure"]["heldPx"]),
                         "the unit count and the held figure stand through the landing and the press's window build (the reply took the card's "
                         "place), so the thread does not shrink under the reader: %r, %r, %r" % (k["measure"], t["measure"], p["measure"]))
        self.assertTrue(p["newestH"] is not None and p["newestH"] > t["measure"]["heldPx"] + p["ch"],
                        "the reply stands taller than the held figure by more than a screen: its height %r, %r" % (p["newestH"], t["measure"]))
        self.assertTrue(p["newestOnScreen"], "the reply is on screen after the press: %r" % self._brief(p))
        self.assertLessEqual(p["dist"], 2, "…at the bottom: %r" % self._brief(p))

    def test_the_held_figure_stays_put_while_the_window_moves(self):
        # chat-regions.ts heldUnitPx counts every unit that is not a gap, rendered or hidden, so window builds over the same units set
        # the same figure: after the climb's last page (the window at the top of the loaded rows), after the chip's second press (at
        # the end) and after ROAD 9's drop (at the tail run's first rows), with no unit landing in between.
        # The result first, then the climb's premise, as in the append test: the drop's reading is ROAD 9's record
        res = self._result()
        self._climb()
        c = res["climb"]
        self.assertIsNotNone(c["below"], "the class dropped back to the tail run after the climb")
        ms = [("the climb's last page", c["climbed"]["measure"]), ("the chip's second press", c["chip2"]["measure"]),
              ("ROAD 9's drop", c["below"]["dropped"]["measure"])]
        self.assertEqual(len({m["units"] for _, m in ms}), 1, "no unit landed between the three readings: %r" % ms)
        self.assertEqual(len({(m["winStart"], m["winEnd"]) for _, m in ms}), 3, "each reading stands in a window of its own: %r" % ms)
        self.assertTrue(all(m["heldPx"] is not None and m["heldPx"] < m["avgUnit"] for _, m in ms), "the held figure is set, under the average unit: %r" % ms)
        self.assertEqual(len({m["heldPx"] for _, m in ms}), 1, "the held figure stays put while the window moves: %r" % ms)


class _ToggleKeepsPlace(_LongHistoryLab):
    """ROAD 12, mixed into the two classes below (a mixin, so the runner collects no lab of its own): a tool run in the history,
    expanded after one reply lands and collapsed after three more, keeps its row where the reader clicked it. The rebuild a toggle
    makes keeps the window, and the toggle puts back the scrollTop it read before it (render.ts toggleToolGroup), which names the same
    place only while the top spacer stands as it was."""
    SCALE = 2
    LEGS = ("keep",)
    BOOT = "measure"      # it reads none of ROADs 1 to 3
    TAIL_TOOLS = True
    KEEP_REPLIES = (1, 3)
    BOUND = None          # whether hidden units take the held figure under the average unit (heldUnitPx's bound engaged)

    def _legs(self):
        """ROAD 12's legs, the road's premise checked first, so each test fails on its own when the road did not set up what it reads:
        both drops landed in a spacer, and in every leg the run's row stood collapsed or expanded as the leg found it 200 px under the
        viewport top, units hidden above the window, the leg's replies landed without moving the window's top or the row, the click flipped the
        run, no unit landed during it, and the held figure stood where the class says it does."""
        k = self._result()["keep"]
        self.assertTrue(k["first"] and k["to"] and k["placed"], "both drops were written into a spacer, and a collapsed tool run is in the window the second built: %r" % k)
        self.assertEqual([g["replies"] for g in k["legs"]], list(self.KEEP_REPLIES), "every leg ran: %r" % k)
        for i, g in enumerate(k["legs"]):
            b, l, t = g["before"], g["landed"], g["toggled"]
            mb, ml = b["measure"], l["measure"]
            self.assertTrue(b["y"] is not None and abs(b["y"] - 200) <= 2 and b["expanded"] is (i % 2 == 1),
                            "the run's row stands %s 200 px under the viewport top before leg %d: %r" % ("expanded" if i % 2 else "collapsed", i, b))
            self.assertTrue(l["met"] and ml["units"] - mb["units"] == g["units"], "the leg's %d replies landed: %r then %r" % (g["replies"], mb, ml))
            self.assertTrue(ml["winStart"] == mb["winStart"] and ml["winEnd"] in (mb["winEnd"], ml["units"]),
                            "…at the tail, the window's top where it was: %r then %r" % (mb, ml))
            self.assertGreater(ml["winStart"], 40, "units stand hidden above the window, in the top spacer: %r" % ml)
            self.assertLessEqual(abs(l["y"] - b["y"]), 2, "…and left the row where it was (render.ts appendActive's anchor restore): %r then %r" % (b, l))
            self.assertTrue(g["pressed"] and t["expanded"] is (i % 2 == 0), "the click %s the run: %r" % ("expanded" if i % 2 == 0 else "collapsed", g))
            self.assertEqual(t["measure"]["units"], ml["units"], "no unit landed during the click: %r" % t["measure"])
            if self.BOUND:
                self.assertTrue(ml["heldPx"] is not None and ml["heldPx"] < ml["avgUnit"] - 1, "hidden units take the held figure, under the average unit: %r" % ml)
            else:
                self.assertTrue(ml["heldPx"] is not None and abs(ml["heldPx"] - ml["avgUnit"]) < 1e-6, "hidden units take the average unit (the bound not engaged): %r" % ml)
        return k["legs"]

    def _moved(self, g):
        return "after %d replies, the row the reader clicked moved from %.2f px to %.2f px under the viewport top (the top spacer %r px, then %r px; the held figure %r, then %r)" % (
            g["replies"], g["landed"]["y"], g["toggled"]["y"], g["landed"]["topPx"], g["toggled"]["topPx"],
            g["landed"]["measure"]["heldPx"], g["toggled"]["measure"]["heldPx"])

    def test_the_clicked_row_stays_put_when_its_tool_run_expands_after_a_reply_lands(self):
        g = self._legs()[0]
        self.assertLessEqual(abs(g["toggled"]["y"] - g["landed"]["y"]), 2, "the expand: " + self._moved(g))

    def test_the_clicked_row_stays_put_when_its_tool_run_collapses_after_three_more_land(self):
        g = self._legs()[1]
        self.assertLessEqual(abs(g["toggled"]["y"] - g["landed"]["y"]), 2, "the collapse: " + self._moved(g))

    def test_the_top_spacer_and_the_held_figure_stand_through_each_toggle(self):
        # the cause, read directly: the toggle's rebuild over the same units above the window keeps the figure, so the top spacer
        # stands to the pixel (render.ts syncViewInner keepHeld)
        for g in self._legs():
            self.assertEqual((g["toggled"]["measure"]["heldPx"], g["toggled"]["topPx"]), (g["landed"]["measure"]["heldPx"], g["landed"]["topPx"]),
                             "the held figure and the top spacer stand through the toggle: " + self._moved(g))


class LongHistoryToggleKeepsPlace(_ToggleKeepsPlace, unittest.TestCase):
    """ROAD 12 over a tail twice LongHistoryClimb's height (the wire tail holds half as many turns, four events each with the tool
    run), where the units the page holds at its landing, at the average unit it measured, pass the room the gaps leave in half the
    limit: hidden units take the held figure, which every new unit lowers. Before the rebuild kept it, the expand after one reply moved
    the row 14,487 px up the screen (64 hidden units above the window, the figure 230 px lower), and the collapse after three more
    41,705 px."""
    TALL_LINES = 4800
    BOUND = True


class LongHistoryToggleKeepsPlaceUnbounded(_ToggleKeepsPlace, unittest.TestCase):
    """ROAD 12 over LongHistoryHeight's tail, where the units at the average unit stand under that room: hidden units take the average
    unit, which a new unit does not move, and the row stands still through the toggles whether or not the rebuild keeps the figure."""
    TALL_LINES = 600
    BOUND = False


class _BuiltHiddenLab(_LongHistoryLab):
    """ROAD 10's two sessions, mixed into LongHistoryBuiltHidden and LongHistoryBuiltHiddenBounded below (a mixin, so the runner
    collects no lab of its own)."""
    SCALE = 2
    LEGS = ("shown",)
    BOOT = "hidden"       # they read none of ROADs 1 to 3: the boot waits for the long session's view built hidden, which ROAD 10 reads
    SECOND_SESSION = True
    BOUND = False         # whether the long session's units, at the average unit measured at a show, engage the bound (heldUnitPx)

    def _shown(self, show):
        """ROAD 10's record at one show of the long session ("first", or "again" after the settings change), its premises checked first,
        so each test fails on its own when the road did not set up what it reads: before the show the reader was on the short session
        (at the first show, where the page landed) and the long view stood built while hidden; the show kept that window (a window
        build sets the figure itself, render.ts renderWindowItems, and would leave the first measure's setting unread); and the bound is
        engaged or not as the class says (chat-regions.ts heldUnitPx). Not engaged (BOUND false): the units at the measured average
        stand under a fifth of the limit, so with the gaps at their quarter of it they stand under half, and the figure a hidden row
        takes is the measured average itself. Engaged: the head gap at the measured px per turn stands past its quarter of the limit,
        so it is drawn at that quarter, and the units at the measured average stand past the quarter it leaves of the half by more
        than a tenth."""
        s = self._result()["shown"]
        before, r = (s["boot"] if show == "first" else s["hidden"]), s[show]
        m = r["measure"]
        self.assertTrue(m and m["avgUnit"], "the long session is shown and measured: %r" % r)
        self.assertTrue(before["active"] and before["active"]["units"] * 4 < m["units"],
                        "before the show the reader was on the short session: %r, then the long one %r" % (before["active"], m))
        self.assertTrue(before["long"] and before["long"]["display"] == "none" and before["long"]["children"] > 0 and before["marked"],
                        "before the show the long view stood built while hidden, its top spacer marked: %r" % before)
        self.assertTrue(r["long"] and r["long"]["display"] != "none" and r["long"]["kept"], "the show kept the window built hidden: %r" % r)
        self.assertGreater(m["winStart"], 1, "rows are hidden above the window, past the head gap's one unit: %r" % m)
        if not self.BOUND:
            self.assertLess(m["units"] * m["avgUnit"], self.limit / 5, "the units at the measured average stand under a fifth of the limit: %r" % m)
        else:
            gap = max((x["hi"] - x["lo"] for x in r.get("regions") or [] if x["kind"] == "gap"), default=0)
            self.assertGreater(gap * m["pxPerTurn"], self.limit / 4, "the head gap at the measured px per turn stands past its quarter of the limit: %d turns, %r" % (gap, m))
            self.assertGreater(m["units"] * m["avgUnit"], 1.1 * self.limit / 4, "the units at the measured average stand past the quarter of the limit the gaps leave of its half: %r" % m)
        return s


class LongHistoryBuiltHidden(_BuiltHiddenLab, unittest.TestCase):
    TALL_LINES = 600      # LongHistoryHeight's tail: the units the page holds, at the average unit, stand under a fifth of the limit

    def test_at_the_first_show_of_a_view_built_hidden_the_hidden_row_figure_is_the_measured_average(self):
        # render.ts sizeSpacers: the first measure of the average unit (with the px per turn's, here) sets the held figure off it. The
        # figure a hidden row takes is render.ts unitPx's: the held figure, or the average where none is set
        s = self._shown("first")
        m = s["first"]["measure"]
        figure = m["heldPx"] if m["heldPx"] is not None else m["avgUnit"]
        self.assertEqual(figure, m["avgUnit"], "the figure a hidden row takes (render.ts unitPx) is the average measured at the show: %r" % m)

    def test_after_a_settings_change_the_switch_back_sizes_hidden_rows_at_the_average_measured_then(self):
        # render.ts sizeSpacers: the first measure of the average unit alone (the px per turn kept through the settings change) sets
        # the held figure off it; the top spacer, sized at the fallback figure while hidden, takes it. The figure a hidden row takes is
        # render.ts unitPx's, as above
        s = self._shown("again")
        m = s["again"]["measure"]
        figure = m["heldPx"] if m["heldPx"] is not None else m["avgUnit"]
        self.assertEqual(figure, m["avgUnit"], "a row hidden in a spacer takes the average measured at the switch back: %r" % m)
        self.assertGreater(s["again"]["long"]["topPx"], s["hidden"]["long"]["topPx"],
                           "the top spacer grew from the fallback figure to the measured one: %r then %r" % (s["hidden"]["long"], s["again"]["long"]))


class LongHistoryBuiltHiddenBounded(_BuiltHiddenLab, unittest.TestCase):
    TALL_LINES = 2400     # LongHistoryClimb's tail: an average unit of ~21,600 px, so the long session's units engage the bound at the show
    LEGS = ("shown", "rebuild")
    BOUND = True

    def test_at_the_first_show_the_hidden_row_figure_is_the_one_the_next_window_build_sets(self):
        # render.ts sizeSpacers: the first measure re-maps the gaps at the measured px per turn and sets the held figure off them, under
        # the share of the limit (heldPxOf), as a window build does. The scroll after the show builds a window over the same units, so
        # the figure that build sets is the one the show must have set. The figure a hidden row takes is render.ts unitPx's: the held
        # figure, or the average where none is set
        s = self._shown("first")
        m, b = s["first"]["measure"], (s["first"].get("rebuilt") or {}).get("measure")
        self.assertTrue(b, "the scroll after the show was read: %r" % s["first"].get("rebuilt"))
        self.assertEqual((b["units"], b["avgUnit"]), (m["units"], m["avgUnit"]), "no unit landed, and the average stood, between the show and the build: %r then %r" % (m, b))
        self.assertNotEqual((b["winStart"], b["winEnd"]), (m["winStart"], m["winEnd"]), "the scroll built a window of its own: %r then %r" % (m, b))
        shown, built = (x["heldPx"] if x["heldPx"] is not None else x["avgUnit"] for x in (m, b))
        self.assertEqual(shown, built, "the figure a hidden row takes (render.ts unitPx) at the show is the one the window build sets: %r then %r" % (m, b))


class UnreachableEndSettles(_LongHistoryLab, unittest.TestCase):
    SCALE = 12            # the layout limit is 2,796,202 px
    VIEWPORT = {"width": 640, "height": 480}   # a smaller page, so the 12x raster costs what a 1000 x 600 page does at 8x
    TALL_LINES = 3000     # tall enough that the thread stands at the limit with its newest row out of reach (the premise test below)
    LEGS = ("gapDrawn",)

    def test_the_end_is_out_of_reach_for_a_reason_that_is_not_the_gap(self):
        J = self._result()["jumped"]
        self.assertGreaterEqual(J["sh"], self.limit - 1, "the thread stands at the layout limit (the lab's premise): %r" % self._brief(J))
        self.assertFalse(J["newestOnScreen"], "…so the newest row is out of reach: %r" % self._brief(J))

    def test_the_estimates_stand_at_most_half_the_layout_limit_at_the_end(self):
        # chat-regions.ts HIDDEN_SHARE at this scale: at the landing and after the jump to the end, the spacers and the gaps drawn
        # stand at most 4 px past half the limit
        r = self._result()
        for road in ("landed", "jumped"):
            self.assertGreater(r[road]["est"], 0, "%s: the reading holds a spacer or a gap: %r" % (road, self._brief(r[road])))
            self.assertLessEqual(r[road]["est"], self.limit / 2 + 4, "%s: the estimates in the scroll height: %r" % (road, self._brief(r[road])))

    def test_the_gaps_are_drawn_at_most_a_quarter_of_the_measured_limit(self):
        g = self._result()["gapDrawn"]
        self.assertIsNotNone(g, "the head gap's element renders at the head")
        self.assertLessEqual(g["h"], self.limit / 4 + 1, "the head gap stands at most a quarter of the limit at this scale: %r" % g)

    def test_the_page_settles_at_the_unreachable_end(self):
        self._assert_settled_at_the_end()
        r = self._result()
        self.assertLessEqual(len(r["jumped"]["asks"]), 1, "no page walk at the end: %r" % r["jumped"]["asks"])

class UnreachableEndInsideAGapSettles(_LongHistoryLab, unittest.TestCase):
    """The case a fill's quiet gaps exist for (2026-10-02): UnreachableEndSettles' fixture at TALL_LINES 1500 (that class's is 3000) with the gaps' bound held, from the boot, at the
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
