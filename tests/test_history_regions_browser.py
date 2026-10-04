"""T386 stage 2 (the user 2026-09-12, plans/chat-history-regions.md Part B): the chat's history is a list of REGIONS over the
transcript's turns, runs the page holds and gaps it does not, and the tail run is always resident and live. Served, on the
window lab's hermetic kernel over a synthetic transcript longer than the wire tail (the page boots holding the tail; a head gap
stands above it):

- at boot the ONE gap runs from the head to the tail's first turn, in the top spacer, with no ask on the wire;
- a scroll to the top of the resident run brings the gap into the viewport and the gap asks for ITS page directly (one loadTurns
  for the page-aligned span at its bottom edge, the edge the reader met), and the reply fills the space in place: the gap shrinks
  to the run's start, the reader's row keeps its viewport offset through one attributed gap-fill write, and no scroll gesture is
  filed for a move the reader never made;
- a live tail arriving while the reader is up in history lands at the tail (the run's last uuid moves), with no paused strip and
  no notice: nothing pauses live updates any more;
- over the head gap hidden in the top spacer at boot, the point under the viewport top (the turn a fill anchors on) is read from where
  the spacer starts, below #content's top padding: a top anywhere in the padding reads as the gap's start, turn 0, and a top in the
  spacer's last 8 px still names a turn (ServedHiddenHeadPoint, 2026-10-02: the walk started at 0, 8 px above the spacer).

Synthetic fixtures only (placeholder uuids, invented prose); hostname TESTHOST.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.test_live_paused_window_browser import DRIVER_HEAD, TURNS, WindowLab  # noqa: E402

DRIVER = DRIVER_HEAD + r"""
const painted = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
const gestures = () => page.evaluate(() => window.__sent.filter((m) => m.what === "scrollgesture").length);
const writes = (writer) => page.evaluate((w) => window.__sent.filter((m) => m.what === "scrollwrite" && m.data && m.data.writer === w).map((m) => [m.data.before, m.data.after]), writer);
// the row under the viewport top and its offset: the fill must keep it there
const rowAtTop = () => page.evaluate(() => {
  const c = document.getElementById("content"); const cTop = c.getBoundingClientRect().top;
  for (const t of Array.from(document.querySelectorAll("#content .turn[data-uuid]"))) { const r = t.getBoundingClientRect(); if (r.bottom > cTop + 1) return { uuid: t.dataset.uuid, y: Math.round(r.top - cTop) }; }
  return null;
});
const regions = () => page.evaluate(() => (typeof window.__rompRegions === "function" ? window.__rompRegions() : null));   // the session's regions (a gap inside a spacer has no element); the base has no hook and its roads still run to their own red
// ROAD 1: the boot holds the tail; one gap from the head to the tail's start, no ask
const bootRegions = await regions(), turnsBefore = await sentOf("loadTurns");
// ROAD 2: a jump to just above the resident run's first turn (into the gap's bottom edge, the edge a reader scrolling up meets; scrollTop 0
// would be the transcript's HEAD, the gap's top edge, and asks for the head page instead); the gap enters the viewport and asks for its bottom page
// first to the top of the rendered window (the top spacer's end), then up one viewport at a time until the gap's element is on screen (bounded)
await page.evaluate(() => { const c = document.getElementById("content"); const sp = document.querySelector("#content .tx-spacer-top"); c.scrollTop = sp ? sp.offsetHeight + 1 : 0; });
await painted();
for (let i = 0; i < 60; i++) {
  if ((await sentOf("loadTurns")) > turnsBefore) break;   // the gap met the viewport and asked: one page (a further step would meet the shrunken gap and ask its next page)
  const seen = await page.evaluate(() => { const c = document.getElementById("content"); const g = document.querySelector("#content .tx-gap"); if (!g) return false; const r = g.getBoundingClientRect(), cr = c.getBoundingClientRect(); return r.bottom > cr.top && r.top < cr.bottom; });
  if (seen) { await page.waitForFunction((n) => window.__sent.filter((m) => m.type === "loadTurns").length > n, turnsBefore, { timeout: 5000 }).catch(() => {}); break; }
  await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = Math.max(0, c.scrollTop - c.clientHeight); });
  await painted();
}
await page.waitForFunction((n) => window.__sent.filter((m) => m.type === "loadTurns").length > n, turnsBefore, { timeout: 10000 }).catch(() => {});
const asks = await page.evaluate(() => window.__sent.filter((m) => m.type === "loadTurns").map((m) => ({ lo: m.lo, hi: m.hi })));
const gapsAsked = (await state()).gaps; const regionsAsked = await regions();
const rowBefore = await rowAtTop();
const gesturesBefore = await gestures();
// the reply fills the gap in place: wait for the gap's end to move down to the ask's start (the run inserted), then the paint
await page.waitForFunction((lo) => { const rs = (typeof window.__rompRegions === "function" && window.__rompRegions()) || []; return rs.length > 0 && rs[0].kind === "gap" && rs[0].hi === lo; }, asks.length ? asks[asks.length - 1].lo : -1, { timeout: 15000 }).catch(() => {});
await painted();
const filled = await state(); const regionsFilled = await regions();
const rowAfter = await rowAtTop();
const gesturesAfter = await gestures();
const fills = await writes("gap-fill");
// MEDIUM 2 (T386 stage 2): the head gap's drawn height per turn must match the rendered run's measured per-turn height (a turn is a
// user row plus its reply, so a per-display-unit average drew gaps about half true). Measure the gap element's px/turn and the rendered
// tail run's px/turn (its user rows are its turns) and compare.
const gapPerTurn = await page.evaluate(() => { const g = document.querySelector("#content .tx-gap"); if (!g) return null; const lo = Number(g.dataset.lo), hi = Number(g.dataset.hi); return hi > lo ? g.offsetHeight / (hi - lo) : null; });
const runPerTurn = await page.evaluate(() => { const c = document.getElementById("content"); let h = 0, turns = 0; for (const t of Array.from(c.querySelectorAll("#content .turn"))) { if (t.classList.contains("tx-spacer") || t.classList.contains("tx-gap")) continue; h += t.offsetHeight; if (t.classList.contains("turn-user")) turns++; } return turns > 0 ? h / turns : null; });
// ROAD 3: a live tail while the reader is up in history: it lands at the tail, nothing pauses
const k = cfg.turns;
const tail = { type: "chatTail", id: cfg.sid, afterUuid: regionsFilled ? regionsFilled[regionsFilled.length - 1].last : filled.lastUuid, events: [
  { uuid: "11111111-2222-3333-4444-" + pad(2 * k), kind: "user", md: "question number " + k + " about the notes api", ts: new Date((cfg.base + 2 * k) * 1000).toISOString() },
  { uuid: "22222222-3333-4444-5555-" + pad(2 * k + 1), kind: "assistant", md: "Answer " + k + ": the handler reads the note by id and returns it.", ts: new Date((cfg.base + 2 * k + 1) * 1000).toISOString() }] };
await page.evaluate((f) => { window.postMessage(f, "*"); }, tail);
await page.waitForFunction((u) => { const ts = Array.from(document.querySelectorAll("#content .turn[data-uuid]")); return ts.length && ts[ts.length - 1].dataset.uuid === u; }, tail.events[1].uuid, { timeout: 10000 }).catch(() => {});
const live = await state(); const regionsLive = await regions(); const rowLive = await rowAtTop();
// ROAD 2b (T386 stage 2, low 8): a fill BELOW the viewport. The head gap still stands above the filled run; a jump to the transcript
// top puts the reader at the gap's TOP edge, the edge met by scrolling DOWN, so the gap asks for its top page (lo 0), which fills in
// place with the reader's row held.
const belowBefore = await sentOf("loadTurns"); const belowGesturesBefore = await gestures();
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = 0; });
await painted();
await page.waitForFunction((n) => window.__sent.filter((m) => m.type === "loadTurns").length > n, belowBefore, { timeout: 10000 }).catch(() => {});
const belowAsks = await page.evaluate((n) => window.__sent.filter((m) => m.type === "loadTurns").slice(n).map((m) => ({ lo: m.lo, hi: m.hi })), belowBefore);
const belowRowBefore = await rowAtTop();
await page.waitForFunction(() => { const rs = (typeof window.__rompRegions === "function" && window.__rompRegions()) || []; return rs.length > 0 && rs[0].kind === "run" && rs[0].lo === 0; }, null, { timeout: 10000 }).catch(() => {});
await painted();
const belowRegions = await regions(); const belowRowAfter = await rowAtTop(); const belowGestures = await gestures();
// R3 (the client merge guard, 2026-09-19), the last measurement: after the fill, the live tail and the below-fill, the bottom of the view is
// the transcript's newest row (the synthetic live turn's reply), and the rendered rows stand in transcript order (the file's records, then
// the injected turn)
const r3 = await bottomCheck(tail.events[1].uuid, transcriptOrder().concat(tail.events.map((e) => e.uuid)));
process.stdout.write("RESULT:" + JSON.stringify({ r3, boot: { regions: bootRegions, turns: boot.turns, atBottom: boot.atBottom, notice: boot.notice, strip: boot.strip }, turnsBefore, asks, gapsAsked, regionsAsked, rowBefore, rowAfter, gesturesBefore, gesturesAfter, fills,
  filled: { regions: regionsFilled, gaps: filled.gaps, turns: filled.turns, firstUuid: filled.firstUuid, lastUuid: filled.lastUuid, top: filled.top, notice: filled.notice, strip: filled.strip },
  gapPerTurn, runPerTurn,
  rowLive, below: { asks: belowAsks, regions: belowRegions, rowBefore: belowRowBefore, rowAfter: belowRowAfter, gestures: belowGestures, gesturesBefore: belowGesturesBefore },
  live: { regions: regionsLive, lastUuid: live.lastUuid, atBottom: live.atBottom, notice: live.notice, strip: live.strip, turns: live.turns, top: live.top } }) + "\n");
await browser.close();
"""


class ServedHistoryRegions(WindowLab):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._r = None

    def _result(self):
        if self._r is None:
            type(self)._r = self._drive(DRIVER, "regions", extra={"turns": TURNS})
            print("REGIONS:", json.dumps(self._r), file=sys.stderr)
        return self._r

    def test_the_boot_holds_the_tail_under_one_head_gap_and_asks_for_nothing(self):
        r = self._result()
        b = r["boot"]
        self.assertIsNotNone(r["boot"]["regions"] if "regions" in r["boot"] else r.get("trace1", {}).get("regions"), "the page holds no regions (the base has none: no runs and gaps, only the old window protocol)")
        self.assertTrue(b["atBottom"], "the page boots at the bottom of the tail: %r" % b)
        self.assertEqual([x["kind"] for x in b["regions"]], ["gap", "run"], "one gap, the head to the tail's start, then the tail run: %r" % b["regions"])
        self.assertEqual(b["regions"][0]["lo"], 0, "the gap starts at the head: %r" % b["regions"])
        self.assertGreater(b["regions"][0]["hi"], 0, "the tail does not start at the head (the transcript is longer than the wire tail): %r" % b["regions"])
        self.assertEqual(b["regions"][1]["lo"], b["regions"][0]["hi"], "the tail run starts where the gap ends")
        self.assertIsNone(b["regions"][1]["hi"], "the tail run is open-ended")
        self.assertEqual(r["turnsBefore"], 0, "no page ask before the reader reaches a gap")
        self.assertFalse(b["notice"] or b["strip"], "nothing shows at a plain boot: %r" % b)

    def test_a_gap_entering_the_viewport_asks_for_its_own_page_and_the_reply_fills_the_space_in_place(self):
        r = self._result()
        self.assertIsNotNone(r["boot"]["regions"], "the page holds no regions (the base has none: no runs and gaps, only the old window protocol)")
        tail_lo = r["boot"]["regions"][0]["hi"]
        self.assertEqual(len(r["asks"]), 1, "the gap met the viewport once and asked once: %r" % r["asks"])
        first = r["asks"][0]
        self.assertEqual(first["hi"], tail_lo, "the ask meets the gap at its bottom edge, the edge the reader scrolled up to: %r" % first)
        self.assertLessEqual(first["hi"] - first["lo"], 16, "one page-aligned span, not the whole gap: %r" % first)
        self.assertEqual(first["lo"] % 16, 0, "the span is aligned to the kernel's pages: %r" % first)
        f = r["filled"]
        self.assertEqual([x["kind"] for x in f["regions"]], ["gap", "run"], "the gap shrank rather than split (the run joins the tail): %r" % f["regions"])
        self.assertEqual(f["regions"][0]["hi"], first["lo"], "the gap ends where the new run starts: %r" % f["regions"])
        self.assertEqual(f["regions"][1]["lo"], first["lo"], "…and the tail run now starts there")
        self.assertGreater(f["regions"][-1]["n"], r["boot"]["regions"][-1]["n"], "the tail run grew by the page's events: %r → %r" % (r["boot"]["regions"][-1], f["regions"][-1]))
        self.assertIsNone(f["regions"][-1]["hi"], "the tail is still the tail, open-ended: %r" % f["regions"][-1])
        self.assertEqual(r["gesturesAfter"], r["gesturesBefore"], "the fill filed no scroll gesture for a move the reader never made")
        self.assertGreaterEqual(len(r["fills"]), 1, "the fill's one attributed write (gap-fill) was filed: %r" % r["fills"])
        self.assertIsNotNone(r["rowBefore"], "a row sat under the viewport top before the fill")
        self.assertEqual(r["rowAfter"]["uuid"], r["rowBefore"]["uuid"], "the reader's row is still the row under the viewport top: %r → %r" % (r["rowBefore"], r["rowAfter"]))
        self.assertLessEqual(abs(r["rowAfter"]["y"] - r["rowBefore"]["y"]), 2, "…at its offset: %r → %r" % (r["rowBefore"], r["rowAfter"]))
        self.assertFalse(f["notice"], "a scroll-driven fill shows no notice: the glyph inside the gap is its cue")

    def test_a_gap_met_from_above_asks_for_its_top_page_and_fills_below_the_viewport(self):
        # T386 stage 2, low 8: the head gap's TOP edge, met by scrolling down, asks for the top page (lo 0) and fills with the row held
        r = self._result()
        b = r["below"]
        self.assertGreaterEqual(len(b["asks"]), 1, "the gap met from above asked for no page: %r" % b)
        self.assertEqual(b["asks"][0]["lo"], 0, "the top edge asks for the head page (lo 0): %r" % b["asks"])
        self.assertLessEqual(b["asks"][0]["hi"] - b["asks"][0]["lo"], 16, "one page-aligned span, not the whole gap: %r" % b["asks"])
        self.assertEqual(b["regions"][0]["kind"], "run", "the head page filled into a run at the top: %r" % b["regions"])
        self.assertEqual(b["regions"][0]["lo"], 0, "…starting at the head: %r" % b["regions"])
        self.assertLessEqual(b["gestures"] - b["gesturesBefore"], 1, "the below-fill added no gesture beyond the reader's own jump into the gap: %r" % b)
        if b["rowBefore"] and b["rowAfter"]:
            self.assertEqual(b["rowAfter"]["uuid"], b["rowBefore"]["uuid"], "the reader's row held through the below-fill: %r → %r" % (b["rowBefore"], b["rowAfter"]))

    def test_the_head_gap_is_drawn_at_the_rendered_runs_per_turn_height(self):
        # MEDIUM 2: a gap's height counts TURNS times px-per-turn, not display units; within ten percent of the rendered run's per-turn height
        r = self._result()
        gpt, rpt = r["gapPerTurn"], r["runPerTurn"]
        self.assertIsNotNone(gpt, "the head gap was measured: %r" % r.get("filled"))
        self.assertIsNotNone(rpt, "the rendered run's per-turn height was measured")
        self.assertLessEqual(abs(gpt - rpt) / rpt, 0.10, "the gap's px/turn (%r) is within ten percent of the run's (%r)" % (gpt, rpt))

    def test_a_live_tail_lands_while_the_reader_is_up_in_history_and_nothing_pauses(self):
        r = self._result()
        live = r["live"]
        self.assertIsNotNone(r["boot"]["regions"] if "regions" in r["boot"] else r.get("trace1", {}).get("regions"), "the page holds no regions (the base has none: no runs and gaps, only the old window protocol)")
        self.assertEqual(live["regions"][-1]["n"], r["filled"]["regions"][-1]["n"] + 2, "the live tail's two events landed at the end of the tail run (the rendered rows are the reader's window, far above): %r → %r" % (r["filled"]["regions"][-1], live["regions"][-1]))
        self.assertFalse(live["atBottom"], "the reader is still up in history: %r" % live)
        self.assertFalse(live["strip"], "no paused strip exists any more")
        self.assertFalse(live["notice"], "no notice for a live tail")
        self.assertEqual(r["rowLive"]["uuid"], r["rowAfter"]["uuid"], "the tail's arrival did not move the reader: the same row under the viewport top: %r → %r" % (r["rowAfter"], r["rowLive"]))
        self.assertLessEqual(abs(r["rowLive"]["y"] - r["rowAfter"]["y"]), 2, "…at its offset (the spacer estimate above may re-size, the row does not move): %r → %r" % (r["rowAfter"], r["rowLive"]))

    def test_after_the_history_actions_the_bottom_of_the_view_is_the_transcripts_newest_row(self):
        # R3 (the client merge guard, 2026-09-19): scrolled to the bottom, the last rendered row is the transcript's newest, the view is at
        # the bottom, and the rendered rows stand in transcript order (a full frame behind the page, or a window placed with no regions,
        # put older content at the bottom)
        r = self._result()
        b = r["r3"]
        self.assertEqual(b["last"], b["newest"], "the bottom of the view is the transcript's newest row: %r" % b)
        self.assertTrue(b["atBottom"], "…and the view is at the bottom: %r" % b)
        self.assertTrue(b["ordered"], "the rendered rows stand in transcript order (misordered at %r): %r" % (b["misordered"], b))
        self.assertTrue(b["runsOrdered"], "the runs are ordered by lo with the open-ended run last: %r" % b["regions"])


# The point under the viewport top over the hidden head gap (2026-10-02, the #68 follow-up), on a fresh boot: every scrollTop write and its
# reading through the page's own hook happen in ONE task, so no scroll handler, re-window or ask runs between them; the position is put back
# before the task ends, and nothing else runs on this page.
POINT_DRIVER = DRIVER_HEAD + r"""
const probe = await page.evaluate((sid) => {
  const c = document.getElementById("content");
  const t = document.querySelector('#content .thread[data-session="' + sid + '"]');
  const sp = t && t.querySelector(":scope > .tx-spacer-top");
  const spY = sp ? sp.getBoundingClientRect().top - c.getBoundingClientRect().top + c.scrollTop : null, spH = sp ? sp.offsetHeight : null;
  const headEl = Array.from(document.querySelectorAll("#content .tx-gap")).some((g) => Number(g.dataset.lo) === 0);
  const regions = typeof window.__rompRegions === "function" ? (window.__rompRegions() || []).map((r) => [r.kind, r.lo, r.hi]) : null;
  const read = (y) => { c.scrollTop = y; return { at: y, st: c.scrollTop, p: typeof window.__rompTurnUnderTop === "function" ? window.__rompTurnUnderTop() : "nohook" }; };
  const st0 = c.scrollTop;
  const pad = [0, 4, 8].map(read);                          // the top of #content's padding, inside it, and its end (where the spacer starts)
  const last = sp ? read(Math.round(spY + spH - 4)) : null;  // 4 px above the spacer's end, inside its last 8 px
  c.scrollTop = st0;
  return { spacer: !!sp, spY, spH, headEl, regions, pad, last };
}, cfg.sid);
process.stdout.write("RESULT:" + JSON.stringify(probe) + "\n");
await browser.close();
"""


class ServedHiddenHeadPoint(WindowLab):
    """turnUnderTop walks the units hidden in the top spacer from where the spacer starts (topSpacerY), below #content's top padding,
    and reads a top in that padding as the spacer's start (2026-10-02). It walked from 0: scrollTop 0 read turn 0, but a top further into
    the padding or at the spacer's start named a point past the gap's start that grew with the top (the base read 0.029 at scrollTop 4,
    inside the padding, and 0.058 at 8, where the spacer starts), and a top in the spacer's last 8 px named none."""

    def test_the_point_over_the_hidden_head_gap_is_read_from_where_the_top_spacer_starts(self):
        r = self._drive(POINT_DRIVER, "point")
        r.pop("_klog", None)
        print("POINT:", json.dumps(r), file=sys.stderr)
        rs = r["regions"] or []
        self.assertTrue(rs and rs[0][0] == "gap" and rs[0][1] == 0, "the boot holds a head gap (the premise): %r" % r)
        self.assertTrue(r["spacer"] and not r["headEl"], "…hidden in the top spacer, with no element of its own (the premise): %r" % r)
        self.assertGreaterEqual(r["spY"], 8, "the spacer starts at or below scrollTop 8, the end of #content's 8 px top padding, so the reads at 0, 4 and 8 are all at or above it (the premise): %r" % r)
        for got in r["pad"]:
            self.assertEqual(got["st"], got["at"], "the write took: %r" % got)
            self.assertIsNotNone(got["p"], "a top at scrollTop %r names a point: %r" % (got["at"], r["pad"]))
            self.assertLess(abs(got["p"]), 1e-6, "a top at scrollTop %r, at or above where the spacer starts (%r), reads as the head gap's start, turn 0: %r" % (got["at"], r["spY"], r["pad"]))
        last = r["last"] or {}
        self.assertTrue(last.get("st") == last.get("at") and last.get("at", 0) > r["spY"] + r["spH"] - 8, "the last read sits inside the spacer's last 8 px: %r (spacer at %r, %r px)" % (last, r["spY"], r["spH"]))
        self.assertIsNotNone(last.get("p"), "a top inside the spacer's last 8 px names a turn: %r" % last)


if __name__ == "__main__":
    unittest.main()
