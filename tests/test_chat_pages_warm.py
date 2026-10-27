#!/usr/bin/env python3
"""Warming the chat's pages cache for the feed's cards (2026-09-11; the user 2026-09-10: a click on a distilled summary
far in the past took long to load, and they asked whether the transcript behind the active cards could be cached). The
pusher, after its send stage, with a board client and a proto-2 chat client connected, renders into the pages cache the
pages the cards' anchors would ask for (what loadAround serves), so the click lands from the cache. Pinned: the anchors
are the summary's own click targets first (a completed card's too), then the active columns' cards' heads and open rows
(a done row and a handoff row are not); every anchor's pages are probed, so an unchanged board settles to a probe and an
evicted page is warmed again; the render budget per cycle is half the cache and the rest waits, counted; the warm stands
down while the pusher is over its budget; a pre-floor page's key survives a turn; the counts are the warm's own; a warmed
window is a cache hit for loadAround with no render. Since 2026-10-25: the set is held against eviction, so a reader's
pages make room among themselves; each page counts at its own size against the byte bound, so a window the cache cannot
hold is rendered once and then waits, by itself when it alone is over half the cache; the warm keys its pages on the
floor a click's reply pages under; a floor of 0 settles on the session's signature; a session the pusher has not built
is neither parsed nor warmed, and settles. Since 2026-10-27: a reader's requests are counted apart from the warm's renders
(clientHits, clientRendered, warmServed, warmUnserved); the warm renders a page at most once between two readers'
requests, so a page it rendered that is gone (evicted as the board's order changed, or re-keyed) before a reader asked
waits for the next reader's request (warmSpent), and the warm checks no page's live inputs (the reader it is served to
does). Synthetic transcript only."""
import collections
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import test_chat_pages as P                                   # noqa: E402  the render-floor harness and its kernel load

km, em, jd = P.km, P.em, P.jd
SID, NOW = P.SID, P.NOW


class WarmTheCards(P.Harness):
    def setUp(self):
        super().setUp()
        km._PAGE_STATS.update(warmed=0, warmMs=0.0, warmCycles=0, warmSkipped=0, warmPending=0)   # the warm counters, per test
        km._WARM_MEMO.update(anchors=(), keys=frozenset(), sigs={}, asks=None)
        km._PERF_STATS.pusher["cycle_ms_last"] = 20.0

    def _reader(self, lo=None):
        """A reader's request for a page away from the anchors' windows (a scroll into older history): the page just below
        the pusher's floor, or the one at `lo`. Returns the pages it rendered (0 or 1)."""
        f = km._RENDER_FLOOR[SID]
        lo = f - km.PAGE_TURNS if lo is None else lo
        st = {}
        self.assertTrue(km._chat_history_page(SID, lo, lo + km.PAGE_TURNS, NOW, stats=st))
        return st.get("rendered", 0)

    def _restored(self, turns=200, compact_every=25):
        """The whole build's events (a cold parse), then the document, then the pusher's floor'd build (its floor set)."""
        recs = P.transcript(NOW - 86400, turns=turns, compact_every=compact_every)
        self.write(recs)
        whole = self.whole()
        self.document()
        m = self.restored()
        self.assertGreater(m["floor"], 0)
        return whole, m

    @staticmethod
    def _card(anchors, column="working", done=False, item="n0", summary=None, paras=None):
        rows = [{"id": "%s-%d" % (item, i), "kind": "ask", "status": "done" if (done and i) else "open", "anchorUuid": u, "promptAnchorUuid": None}
                for i, u in enumerate(anchors)]
        return {"itemId": item, "sid": SID, "column": column, "tree": rows, "summaryAnchorUuid": summary, "summaryAnchorsPara": paras}

    def _feed(self, *cards):
        return {"type": "feed", "asks": list(cards)}

    def _hit(self, uuid):
        """loadAround on the anchor, asserting the window came from the cache: no render."""
        misses = km._PAGE_STATS["misses"]
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": uuid}, NOW)
        self.assertIn(uuid, [e["uuid"] for e in r["events"]])
        return km._PAGE_STATS["misses"] == misses

    def test_the_summarys_own_targets_are_warmed_first_a_completed_cards_too_and_the_click_hits(self):
        whole, m = self._restored()
        deep, para, row = whole[5]["uuid"], whole[60]["uuid"], whole[120]["uuid"]
        feed = self._feed(self._card([], column="completed", item="c", summary=deep, paras=[{"u": para, "q": "quoted"}, None]),
                          self._card([row], column="working", item="w"))
        self.assertEqual(km._card_anchors(feed), [(SID, deep), (SID, para), (SID, row)],
                         "the completed card's summary line and paragraph targets first, then the active card's row")
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0, "pages rendered for the anchors' windows")
        st = dict(km._PAGE_STATS)
        self.assertEqual((st["warmed"], st["warmCycles"], st["warmSkipped"], st["warmPending"]), (n, 1, 0, 0))
        for u in (deep, para, row):
            self.assertTrue(self._hit(u), "the click's window came from the cache: no render (%s)" % u)
        self.assertGreater(km._PAGE_STATS["hits"], 0)

    def test_the_probe_costs_nothing_while_resident_and_warms_again_after_an_eviction_once_a_reader_asks(self):
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"], whole[60]["uuid"]]))
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0)
        self.assertEqual(km._WARM_MEMO["anchors"], tuple(km._card_anchors(feed)), "the set is fully resident: remembered")
        self.assertEqual(len(km._WARM_MEMO["keys"]), n)
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "every page resident: the probe renders nothing")
        self.assertEqual(km._PAGE_STATS["warmCycles"], 2, "…and the cycle is counted")
        with km._page_lock:                                           # the pages left the cache before any reader asked for one
            km._PAGE_CACHE.clear(); km._PAGE_STATS["pages"] = 0; km._PAGE_STATS["bytes"] = 0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "gone before a reader asked: not rendered again")
        self.assertEqual(km._PAGE_STATS["warmSpent"], 2, "…both anchors wait for a reader")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "…on every pass")
        self.assertEqual(self._reader(), 1, "a reader scrolls into older history")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), n, "the same pages are rendered again")
        self.assertEqual((km._PAGE_STATS["warmed"], km._PAGE_STATS["warmSpent"]), (2 * n, 0))
        with km._page_lock:
            k0 = next(iter(km._WARM_MEMO["keys"])); km._PAGE_STATS["bytes"] -= km._PAGE_CACHE.pop(k0)[1]; km._PAGE_STATS["pages"] -= 1
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "one page evicted before a reader asked: it waits")
        self._reader()
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 1, "after one: the remembered set is probed and that page alone rendered")
        km._RENDER_FLOOR[SID] = m["floor"] - 16                       # a floor move re-keys the pages: warmed again once a reader asks
        self._reader()
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)

    def test_the_warm_set_is_bounded_to_half_the_cache_and_settles_and_a_board_change_admits_the_rest(self):
        """The warming review (item 3): an unbounded set over the cache re-rendered itself every cycle for the kernel's life
        (23 windows: 14 to 16 renders on each of eight cycles, 92 evictions). The SET is bounded: anchors past it wait."""
        whole, m = self._restored(turns=600, compact_every=150)
        floor = m["floor"]
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        anchors = []                                                  # one per two pages: disjoint windows of two pages each
        for p in range(0, floor - 2 * km.PAGE_TURNS, 2 * km.PAGE_TURNS):
            j = p + km.PAGE_TURNS // 2                                # a turn in the page's second half: its window is [p, p+32)
            anchors.append(next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid")))
        self.assertGreater(2 * len(anchors), km.WARM_PAGES_MAX, "more pages than the set's bound")
        feed = self._feed(self._card(anchors))
        n1 = km._warm_history_pages(feed, NOW, {})
        admitted = km.WARM_PAGES_MAX // 2                             # windows of two pages each
        self.assertEqual(n1, km.WARM_PAGES_MAX, "one cycle renders the set's bound")
        self.assertEqual(km._PAGE_STATS["warmPending"], len(anchors) - admitted, "the anchors past the bound wait")
        ev0 = km._PAGE_STATS["evictions"]
        for _ in range(4):                                            # the cycles after: the set settles, nothing re-rendered
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "a bounded set settles: no render")
        self.assertEqual(km._PAGE_STATS["evictions"], ev0, "…and evicts nothing")
        self.assertEqual(km._PAGE_STATS["warmCycles"], 5)
        self.assertTrue(self._hit(anchors[0]), "the first anchor's window stays resident")
        self.assertFalse(self._hit(anchors[-1]), "the last anchor's waits for a board change")
        # a board change: the first four cards left; their windows' place admits the next four
        feed2 = self._feed(self._card(anchors[4:]))
        n2 = km._warm_history_pages(feed2, NOW, {})
        self.assertEqual(n2, 2 * 4, "the next four windows admitted and rendered (the click above rendered the LAST anchor's, still waiting)")
        self.assertEqual(km._PAGE_STATS["warmPending"], len(anchors) - 4 - admitted)
        self.assertEqual(km.WARM_PAGES_MAX, km._PAGE_CACHE_MAX // 2)

    def test_done_rows_handoffs_completed_rows_and_tail_anchors_are_not_warmed(self):
        whole, m = self._restored()
        deep = whole[5]["uuid"]
        tail_anchor = m["events"][-1]["uuid"]                         # in the resident tail: nothing to warm
        feed = {"type": "feed", "asks": [
            {"itemId": "c1", "sid": SID, "column": "completed", "tree": [{"id": "a", "kind": "ask", "status": "done", "anchorUuid": deep}]},
            {"itemId": "c2", "sid": SID, "column": "working", "tree": [{"id": "b", "kind": "ask", "status": "open", "anchorUuid": tail_anchor},
                                                                        {"id": "c", "kind": "ask", "status": "done", "anchorUuid": deep},
                                                                        {"id": "d", "kind": "handoff", "status": "open", "anchorUuid": deep}]}]}
        self.assertEqual(km._card_anchors(feed), [(SID, tail_anchor)], "a completed card's rows, a done row and a handoff row do not count")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "a tail anchor is resident already")
        self.assertEqual(km._WARM_MEMO["anchors"], tuple(km._card_anchors(feed)), "a resolved board with every anchor in the tail SETTLES (an empty set)")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertEqual(km._PAGE_STATS["warmCycles"], 2, "…and the next cycle is the probe alone")
        self.assertEqual(km._warm_history_pages({"type": "feed", "asks": []}, NOW, {}), 0, "no anchors: no cycle")
        self.assertEqual(km._PAGE_STATS["warmCycles"], 2)

    def test_a_slow_pusher_stands_down_and_the_next_cycle_in_budget_warms(self):
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"]]))
        km._PERF_STATS.pusher["cycle_ms_last"] = 5000.0              # the pusher is behind
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertEqual((km._PAGE_STATS["warmSkipped"], km._PAGE_STATS["warmCycles"]), (1, 0), "counted, nothing rendered")
        km._PERF_STATS.pusher["cycle_ms_last"] = 20.0
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0, "the next cycle in budget warms")

    def test_a_pre_floor_pages_key_survives_a_turn_and_the_render_count_is_the_calls_own(self):
        """A page rendered from the pre-cut prefix cannot change while a turn streams: its key reads none of the live tail."""
        whole, m = self._restored()
        st = {}
        page = km._chat_history_page(SID, 0, km.PAGE_TURNS, NOW, stats=st)
        self.assertEqual(st, {"rendered": 1})
        self.assertEqual(P._strip(page), whole[:len(page)], "the first page is the whole's first slice")
        km._chat_history_page(SID, 0, km.PAGE_TURNS, NOW, stats=st)
        self.assertEqual(st, {"rendered": 1}, "a hit is not a render")
        recs = P.transcript(NOW - 86400, turns=200, compact_every=25)
        last = next(r for r in reversed(recs) if r.get("uuid"))
        t_last = em.parse_z(last["timestamp"])
        more = [P.G.uline(t_last + 60, "one more step", "u_more_1", last["uuid"]),
                P.G.aline(t_last + 90, "done with the step", "a_more_1", "u_more_1", stop="end_turn")]
        self.write(recs + more)                                       # a turn streamed: the transcript's stat moved
        km._live_scope.chat_floor0 = False
        try:
            m2 = km.build_session(SID, NOW + 100, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(len(m2["events"]), len(m["events"]) + 2, "the tail grew")
        misses = km._PAGE_STATS["misses"]
        self.assertEqual(P._strip(km._chat_history_page(SID, 0, km.PAGE_TURNS, NOW + 100, stats=st)), P._strip(page))
        self.assertEqual((km._PAGE_STATS["misses"], st["rendered"]), (misses, 1), "the page's key survived the turn: a hit")

    def test_a_pages_key_reads_the_regs_fork_value_not_the_file(self):
        """The warming review (item 2): the queue and echo mirrors rewrite STATE/sdk/<sid>.json on every send, so a key on
        the file's identity re-keyed every page of the session the user was prompting."""
        whole, m = self._restored()
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.write_text(json.dumps({"forkedFrom": None, "queue": []}))
        k1 = km._page_sig(self.rows[0], SID, NOW)
        reg.write_text(json.dumps({"forkedFrom": None, "queue": [{"text": "a send"}], "echoes": [1]}))   # a send: the mirror rewrote
        self.assertEqual(km._page_sig(self.rows[0], SID, NOW), k1, "the same fork value: the same key")
        reg.write_text(json.dumps({"forkedFrom": {"sid": "22222222-3333-4444-5555-666666666666", "uuid": whole[3]["uuid"]}}))
        self.assertNotEqual(km._page_sig(self.rows[0], SID, NOW), k1, "a fork lineage: another key (the branch marker moves)")

    def test_a_judge_publish_that_stamps_no_seam_keeps_the_pages_and_a_seam_re_keys_them(self):
        """2026-10-22: a page reads the goal store by its seams alone (the build's segments and the caption scan's), so the
        key holds them by value (_seams_fp) where it held the store's identity: a publish that moves nodes and no seam keeps
        every page and the warm settles to its probe; a seam stamped re-keys them and the warm renders them again. On stock
        every publish re-keyed every page of the session."""
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"], whole[60]["uuid"]]))
        store = jd.GOALDIR / (SID + ".json")
        store.write_text(json.dumps({"nodes": {"g1": {"title": "tidy the api"}}, "status": {}, "seams": []}))
        k1 = km._page_sig(self.rows[0], SID, NOW)
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0)
        store.write_text(json.dumps({"nodes": {"g1": {"title": "tidy the api"}, "g2": {"title": "then the docs"}},
                                     "status": {"g1": "completed"}, "seams": []}))
        self.assertEqual(km._page_sig(self.rows[0], SID, NOW), k1, "a publish with the same seams: the same key")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "…and the warm renders nothing")
        store.write_text(json.dumps({"nodes": {"g1": {"title": "tidy the api"}}, "status": {"g1": "completed"},
                                     "seams": [{"segs": {"seg-a": 1}, "t": NOW - 3600, "top": "g1", "text": "tidy the api"}]}))
        self.assertNotEqual(km._page_sig(self.rows[0], SID, NOW), k1, "a seam stamped: another key")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "…the set rendered under the old key went unread: it waits")
        self._reader()
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), n, "…and once a reader asks the set is rendered again")

    def test_a_floor_of_0_settles_on_its_signature_and_the_floors_return_warms(self):
        """Round 3, A, kept by the signature (2026-10-25): with an index client connected every tab's floor is 0, so no anchor
        has a page below it, and the floor's return must find the warm awake. The set is remembered with every session's
        page signature, which holds its floor, so a floor-0 board settles to a probe (it used to stay unresolved and walk
        every anchor on every pass for as long as one session's floor was 0, a whole parse's included) and the floor's
        return moves the signature and warms. A session with no row settles the same way, and its row's arrival wakes it."""
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"], whole[60]["uuid"]]))
        km._RENDER_FLOOR[SID] = 0                                     # a proto-1 client holds every tab at turn 0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "nothing to warm below a floor of 0")
        self.assertEqual(km._WARM_MEMO["anchors"], tuple(km._card_anchors(feed)), "the empty set settles")
        sig0 = km._page_sig(self.rows[0], SID, NOW)
        self.assertEqual(km._WARM_MEMO["sigs"], {SID: sig0}, "…on the session's signature at floor 0")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertEqual(km._PAGE_STATS["warmPending"], 0)
        km._RENDER_FLOOR[SID] = m["floor"]                            # it left: the floor is back
        self.assertNotEqual(km._page_sig(self.rows[0], SID, NOW), sig0, "the floor's return moves the signature")
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0, "the next cycle warms the windows")
        self.assertTrue(self._hit(whole[5]["uuid"]))
        # a second session with no row beside a resolved one (the pusher built it at a floor of 0; its row has left the
        # listing): remembered with no signature, and its row's arrival wakes the warm
        other = "99999999-0000-4000-8000-000000000099"
        self.addCleanup(km._RENDER_FLOOR.pop, other, None)
        km._RENDER_FLOOR[other] = 0
        feed2 ={"type": "feed", "asks": feed["asks"] + [{"itemId": "z", "sid": other, "column": "working",
                                                           "tree": [{"id": "r", "kind": "ask", "status": "open", "anchorUuid": "u-nowhere"}]}]}
        self.assertEqual(km._warm_history_pages(feed2, NOW, {}), 0, "the first session's windows are resident")
        self.assertEqual(km._WARM_MEMO["anchors"], tuple(km._card_anchors(feed2)), "a board with a row-less session settles")
        self.assertIsNone(km._WARM_MEMO["sigs"][other])
        leaf2 = self.proj / (other + ".jsonl")
        leaf2.write_text(Path(self.leaf).read_text())
        self.rows.append({"sid": other, "name": "api", "path": str(leaf2), "mtime": NOW, "anchor": other})
        self.assertEqual(km._warm_history_pages(feed2, NOW, {}), 0, "its transcript has no floor yet: nothing to render")
        self.assertIsNotNone(km._WARM_MEMO["sigs"][other], "…but the row's arrival walked the board again")

    def test_the_set_is_bounded_in_bytes_too_and_a_readers_page_beside_it_survives(self):
        """Round 3, B: the cache evicts on bytes as well as count; a set bounded by count alone re-rendered itself every cycle
        once its pages passed the byte bound."""
        whole, m = self._restored(turns=600, compact_every=150)
        floor = m["floor"]
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        anchors = [next(a["uuid"] for a in turns[p + km.PAGE_TURNS // 2]["atoms"] if a.get("uuid"))
                   for p in range(0, floor - 2 * km.PAGE_TURNS, 2 * km.PAGE_TURNS)]
        reader = km._chat_history_page(SID, floor - km.PAGE_TURNS, floor, NOW)        # a page the reader scrolled into, beside the set
        self.assertTrue(reader)
        with km._page_lock:
            page_bytes = max(b for _, b in km._PAGE_CACHE.values())
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = page_bytes * 9                        # nine pages of room: the set may take half (four to five)
        try:
            feed = self._feed(self._card(anchors))
            n1 = km._warm_history_pages(feed, NOW, {})
            self.assertGreater(n1, 0); self.assertLess(n1, km.WARM_PAGES_MAX, "the byte bound closed the set before the page bound")
            self.assertGreater(km._PAGE_STATS["warmPending"], 0)
            ev0 = km._PAGE_STATS["evictions"]
            for _ in range(4):
                self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "the set settles under the byte bound")
            self.assertEqual(km._PAGE_STATS["evictions"], ev0, "…and evicts nothing")
            misses = km._PAGE_STATS["misses"]
            km._chat_history_page(SID, floor - km.PAGE_TURNS, floor, NOW)
            self.assertEqual(km._PAGE_STATS["misses"], misses, "the reader's page beside the set survived the warm")
            self.assertTrue(self._hit(anchors[0]))
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_an_over_budget_pusher_stands_down_before_the_probe(self):
        """Round 3, C: the stand-down comes first, so a slow pusher pays not even the settled probe; the probe's time is the warm's."""
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"]]))
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)
        cycles, ms = km._PAGE_STATS["warmCycles"], km._PAGE_STATS["warmMs"]
        km._PERF_STATS.pusher["cycle_ms_last"] = 5000.0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertEqual((km._PAGE_STATS["warmSkipped"], km._PAGE_STATS["warmCycles"], km._PAGE_STATS["warmMs"]), (1, cycles, ms),
                         "stood down before the probe: no cycle, no time")
        km._PERF_STATS.pusher["cycle_ms_last"] = 20.0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "the remembered set: a probe")
        self.assertEqual(km._PAGE_STATS["warmCycles"], cycles + 1); self.assertGreater(km._PAGE_STATS["warmMs"], ms, "…counted")
        src = open(os.path.join(P.BIN, "romp-kernel")).read()
        fn = src[src.index("def _warm_history_pages("):src.index("def _turn_of_uuid(")]
        self.assertLess(fn.index("WARM_SKIP_MS:"), fn.index('_WARM_MEMO["anchors"]:'), "the stand-down precedes the probe")

    def test_a_page_two_windows_share_is_counted_once_in_the_sets_bytes(self):
        """The follow-up's low: the page bound treated a shared page as free while the byte count added it twice, closing the set
        early. The four pages' sizes are learned first, so the bound is exact: half the cache is the four pages' bytes."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        for lo in (0, 16, 32, 48):
            km._chat_history_page(SID, lo, lo + km.PAGE_TURNS, NOW)
        with km._page_lock:
            total = sum(b for _, b in km._PAGE_CACHE.values())
            km._PAGE_CACHE.clear(); km._PAGE_DEPS.clear(); km._PAGE_STATS.update(pages=0, bytes=0)   # the sizes stay known
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = 2 * total + 2                          # half the bound: the four pages, exactly
        try:
            # two anchors in one window (the same two pages) and a third in another: with the shared pages counted once the
            # set holds two pages after the first two anchors, so the third window is admitted; counted twice it was closed
            feed = self._feed(self._card([first(8), first(9), first(40)]))
            n = km._warm_history_pages(feed, NOW, {})
            self.assertEqual(n, 4, "both windows rendered: four pages")
            self.assertEqual(km._PAGE_STATS["warmPending"], 0)
        finally:
            km._PAGE_CACHE_BYTES = saved

    def _sizes_then_empty(self, los):
        """Render the pages starting at `los` to learn their bytes, then empty the cache (the sizes stay known to the warm,
        _PAGE_SIZES); returns {lo: bytes}."""
        for lo in los:
            km._chat_history_page(SID, lo, lo + km.PAGE_TURNS, NOW)
        with km._page_lock:
            sizes = {k[1]: b for k, (_, b) in km._PAGE_CACHE.items()}
            km._PAGE_CACHE.clear(); km._PAGE_DEPS.clear(); km._PAGE_STATS.update(pages=0, bytes=0)
        return sizes

    def test_a_readers_pages_past_the_bound_evict_their_own_never_the_warm_set(self):
        """2026-10-25 (the live pages cache: no hit in hours, one eviction per warmed page): the cache evicted by insertion
        order and the warm's probes never refresh it, so once a reader's pages filled the other half, the reader's next page
        evicted the SET's oldest; the next warm rendered that one, whose store evicted the next page of the set, and so on
        through the set. The set is held against eviction now: the reader's own oldest page goes and the next warm is the
        probe alone; and while a pass admits a new card's window it is the reader's pages that make room, not the pages of
        the set before it, which the same pass probes next."""
        whole, m = self._restored(turns=600, compact_every=150)
        floor = m["floor"]
        self.assertGreater(floor, 256)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        sizes = self._sizes_then_empty([0, 16, 32, 48])
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = 2 * sum(sizes.values()) + 2            # the set (four pages) is half the cache
        try:
            feed = self._feed(self._card([first(8), first(40)]))      # windows [0, 32) and [32, 64)
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 4)
            held = set(km._WARM_MEMO["keys"])
            self.assertEqual(len(held), 4)
            for lo in range(64, 224, km.PAGE_TURNS):                  # a reader scrolls through ten pages past the set
                km._chat_history_page(SID, lo, lo + km.PAGE_TURNS, NOW)
            self.assertGreater(km._PAGE_STATS["evictions"], 0, "the reader's pages went past the bound")
            with km._page_lock:
                self.assertTrue(held <= set(km._PAGE_CACHE), "…and evicted none of the set")
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "the next warm is the probe alone")
            # a new card's window at the top of the board: its renders evict the reader's pages, never those of the set
            # before it (the probe touched none of them: they are the cache's oldest), which the same pass probes next
            feed2 = self._feed(self._card([first(232)]), self._card([first(8), first(40)], item="n1"))
            self.assertEqual(km._warm_history_pages(feed2, NOW, {}), 2, "the new window's two pages alone")
            with km._page_lock:
                self.assertTrue(held <= set(km._PAGE_CACHE), "the set before the pass stayed")
            self.assertTrue(self._hit(first(8)) and self._hit(first(40)), "the cards' clicks land from the cache")
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_a_window_larger_than_the_cache_is_rendered_once_then_waits(self):
        """2026-10-25: the byte bound was tested before an anchor's window, so the first window was admitted whatever its size,
        and one the cache could not hold evicted its own first page with its second: every pass rendered both, none settled.
        A window's size is known after its render now, and from then on it waits without one."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        sizes = self._sizes_then_empty([0, 16])
        with km._page_lock:
            km._PAGE_SIZES.clear()                                    # nothing known: the first pass learns the sizes
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = max(sizes.values()) * 3 // 2           # a page and a half: the window of two does not fit
        try:
            feed = self._feed(self._card([first(8)]))
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 2, "the first pass renders the window and learns its size")
            self.assertEqual(km._PAGE_STATS["warmPending"], 1, "…over the room: it waits")
            for _ in range(3):
                self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "from then on it waits without a render")
            self.assertEqual(km._PAGE_STATS["warmed"], 2)
            km._chat_history_page(SID, 32, 48, NOW)                  # a reader's page: the window's pages leave the cache
            with km._page_lock:
                self.assertFalse(any(k[1] < 32 for k in km._PAGE_CACHE), "nothing of the window is resident")
            feed2 = self._feed(self._card([first(9)]), self._card([first(8)], item="n1"))   # a board change: walked again
            self.assertEqual(km._warm_history_pages(feed2, NOW, {}), 0, "its size is known: no render")
            self.assertEqual(km._PAGE_STATS["warmPending"], 2)
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_a_window_over_half_the_cache_waits_alone_and_the_cards_after_it_are_warmed(self):
        """A window whose pages alone are over half the cache, though under the whole, can never be held. It closed the set
        when its size was learned (and again on every pass that knew it), so while its card stayed on top no card after it
        was warmed, and nothing was. It waits by itself now: rendered once to learn its size and left to the cache's own
        order, while the next cards' windows are admitted, held, and served to their clicks. Such a render is one a pass:
        the windows of unknown size after it wait for the next pass, which the board, not settled, gets (a board of large
        windows rendered them all in one pass)."""
        recs = P.transcript(NOW - 86400, turns=600, compact_every=150)
        for r in recs:                                                # the first 32 turns' replies eight times as long
            u = r.get("uuid") or ""
            if r.get("type") == "assistant" and u[1:].isdigit() and int(u[1:]) < 32:
                for b in r["message"]["content"]:
                    b["text"] = " ".join([b["text"]] * 8)
        self.write(recs)
        self.document()
        self.assertGreater(self.restored()["floor"], 160)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        sizes = self._sizes_then_empty([0, 16, 32, 48, 64, 80, 128, 144])
        with km._page_lock:
            km._PAGE_SIZES.clear()                                    # nothing known: the first pass learns the sizes
        big, w1, w2, w3 = (sizes[a] + sizes[a + km.PAGE_TURNS] for a in (0, 64, 128, 32))
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = big * 3 // 2                           # the big window: over half the cache, under the whole
        try:
            half = km._PAGE_CACHE_BYTES // 2
            self.assertGreater(big, half); self.assertLessEqual(w1 + w2 + w3, half)
            cards = [self._card([first(8)], item="big"), self._card([first(72)], item="n1"), self._card([first(136)], item="n2")]
            feed = self._feed(*cards)                                 # windows [0, 32), [64, 96), [128, 160)
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 2, "the big window rendered once, learning its size")
            self.assertEqual(km._PAGE_STATS["warmPending"], 3, "…and the next two, of unknown size, wait for the next pass")
            self.assertEqual(km._WARM_MEMO["anchors"], (), "…which the board, not settled, gets")
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 4, "the next pass renders the next two windows")
            self.assertEqual(km._PAGE_STATS["warmPending"], 1, "the big window alone waits")
            held = set(km._WARM_MEMO["keys"])
            self.assertEqual(sorted(k[1] for k in held), [64, 80, 128, 144], "the next cards' windows are the set")
            for lo in range(192, 352, km.PAGE_TURNS):                 # a reader scrolls through ten pages elsewhere
                km._chat_history_page(SID, lo, lo + km.PAGE_TURNS, NOW)
            with km._page_lock:
                self.assertTrue(held <= set(km._PAGE_CACHE), "the reader's pages evicted none of the set")
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "the board settles to its probe")
            self.assertTrue(self._hit(first(72)) and self._hit(first(136)), "the next cards' clicks land from the cache")
            # a board change walks the board again, the big window's size known: it waits without a render and does not
            # keep a new card's window [32, 64) out
            self.assertEqual(km._warm_history_pages(self._feed(*cards, self._card([first(40)], item="n3")), NOW, {}), 2,
                             "the new card's window alone is rendered")
            self.assertEqual(km._PAGE_STATS["warmPending"], 1)
            self.assertTrue(self._hit(first(40)))
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_the_warm_keys_its_pages_on_the_floor_a_clicks_reply_pages_under(self):
        """2026-10-25: the warm took the floor the pusher's last build recorded, and a click's reply the floor its own build
        follows (_follower_floor). They differ when the tree's cut has passed a floor the pusher took from an older tree: the
        warm keyed its pages on a floor no click asked for. It takes the click's floor now. A session the pusher has not built
        (no record) is still neither parsed nor warmed (a parse here would be a cold one on the pusher, and would change the
        session's feed card, which reads the parse store without parsing); it no longer keeps the board from settling, and
        the pusher's first build of it wakes the warm."""
        whole, m = self._restored(turns=600, compact_every=150)
        cut = m["floor"]
        deep = whole[5]["uuid"]
        feed = self._feed(self._card([deep]))
        self.addCleanup(km._RENDER_FLOOR_CUT.pop, SID, None)
        km._RENDER_FLOOR[SID] = km._RENDER_FLOOR_CUT[SID] = cut - 32  # the pusher's floor was an older tree's cut
        self.assertEqual(km._follower_floor(SID, cut, rebuild=False), cut, "a click's build follows this tree's cut")
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertTrue(self._hit(deep), "the click's window came from the cache")
        km._RENDER_FLOOR.pop(SID, None); km._RENDER_FLOOR_CUT.pop(SID, None)   # a session the pusher has not built
        km._WARM_MEMO.update(anchors=(), keys=frozenset(), sigs={})
        with km._page_lock:
            km._PAGE_CACHE.clear(); km._PAGE_DEPS.clear(); km._PAGE_STATS.update(pages=0, bytes=0)
        parses = collections.Counter()
        real_parse = km._parse

        def parse(path, sid, now):
            parses[str(sid)] += 1
            return real_parse(path, sid, now)
        with mock.patch.object(km, "_parse", parse):
            for _ in range(3):
                self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "not built: nothing warmed")
        self.assertEqual(parses[SID], 0, "…and never parsed, by the pass or by the probe")
        self.assertEqual(km._WARM_MEMO["anchors"], tuple(km._card_anchors(feed)), "the board settles")
        self.assertEqual(km._WARM_MEMO["sigs"], {SID: km._WARM_UNBUILT})
        km._RENDER_FLOOR[SID] = cut                                   # the pusher's first build of the session
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0, "its first build wakes the warm")
        self.assertTrue(self._hit(deep), "…and the click lands from the cache")

    def test_a_known_anchor_is_resolved_without_a_walk_and_the_shared_components_are_read_once_a_pass(self):
        """2026-10-25: every full pass (any board change) rebuilt the uuid-to-turn map over every atom below each session's
        floor, and each page signature it took read the shared components afresh. An anchor resolved under the session's
        current signature now stands (the signature holds the prefix it was resolved in), the map is built only for a pass
        with a new anchor, and the shared components are read once a pass."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        anchors = [first(8), first(40), first(100)]
        feed = self._feed(self._card(anchors))
        calls = {"map": 0, "shared": 0}
        real_map, real_shared = km._warm_turn_map, km._chat_sig_shared

        def tmap(t, f):
            calls["map"] += 1
            return real_map(t, f)

        def shared():
            calls["shared"] += 1
            return real_shared()
        with mock.patch.object(km, "_warm_turn_map", tmap), mock.patch.object(km, "_chat_sig_shared", shared):
            self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)
            self.assertEqual(calls["map"], 1, "new anchors: the session's map built once")
            calls.update(map=0, shared=0)
            self.assertEqual(km._warm_history_pages(self._feed(self._card(list(reversed(anchors)))), NOW, {}), 0,
                             "a board change over the same anchors: a full pass, every page resident")
            self.assertEqual(calls["map"], 0, "…and no map built")
            with km._page_lock:                                       # one page gone: the settled probe fails, the pass walks the board
                k0 = next(iter(km._WARM_MEMO["keys"]))
                km._PAGE_STATS["bytes"] -= km._PAGE_CACHE.pop(k0)[1]; km._PAGE_STATS["pages"] = len(km._PAGE_CACHE)
            self._reader()                                            # (a reader's request since: the page may be rendered again)
            calls.update(map=0, shared=0)
            self.assertEqual(km._warm_history_pages(self._feed(self._card(list(reversed(anchors)))), NOW, {}), 1)
            self.assertEqual(calls["shared"], 1, "the probe and the pass share one read of the shared components")
            km._RENDER_FLOOR[SID] = m["floor"] - 16                   # a floor move re-keys the session: its anchors resolve again
            self._reader()
            calls.update(map=0, shared=0)
            self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)
            self.assertEqual(calls["map"], 1)
        self.assertTrue(self._hit(anchors[1]), "the anchors' windows as the click asks for them")

    def test_an_anchor_left_waiting_is_known_when_its_card_rises(self):
        """An anchor past the set's bound, of a session whose map the pass built anyway, is resolved by a lookup as the pass
        ends, so its card rising to the top of the board costs no map."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        sizes = self._sizes_then_empty([0, 16, 32, 48])
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = 2 * max(sizes[0] + sizes[16], sizes[32] + sizes[48]) + 2   # the set holds either window, not both
        try:
            anchors = [first(8), first(40), first(100)]
            calls = [0]
            real = km._warm_turn_map

            def tmap(t, f):
                calls[0] += 1
                return real(t, f)
            with mock.patch.object(km, "_warm_turn_map", tmap):
                self.assertEqual(km._warm_history_pages(self._feed(self._card(anchors)), NOW, {}), 2, "the first window alone")
                self.assertEqual((km._PAGE_STATS["warmPending"], calls[0]), (2, 1), "the rest wait; one map built")
                calls[0] = 0
                km._warm_history_pages(self._feed(self._card(list(reversed(anchors)))), NOW, {})
                self.assertEqual(calls[0], 0, "the waiting anchors were resolved: their card rose without a map")
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_the_anchor_map_reads_a_restored_turns_uuids_and_builds_no_atom(self):
        """2026-10-27: the anchor map iterated the atoms of every turn below the floor, and a restored pre-cut turn's atoms
        are lazy, so each map built every atom of the session's history, in a pass that reads no body. It reads a restored
        turn's uuids now: the same map, no atom built, and a warm pass over new anchors builds atoms only in its renders."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns, floor = km._parse(self.leaf, SID, NOW)["turns"], m["floor"]
        self.assertGreater(sum(1 for t in turns[:floor] if t.get("pre")), floor // 2, "the history below the floor is restored")
        built = em._ASM_INDEX_STATS["materialized"]
        got = km._warm_turn_map(turns, floor)
        self.assertEqual(em._ASM_INDEX_STATS["materialized"], built, "the map built no atom")
        want = {a["uuid"]: i for i, t in enumerate(turns[:floor]) for a in t.get("atoms") or [] if a.get("uuid")}
        self.assertGreater(em._ASM_INDEX_STATS["materialized"], built, "(the atoms themselves, built)")
        self.assertEqual(got, want, "the same map as the atoms give")
        at = {}
        for u, j in want.items():
            at.setdefault(j, u)
        anchors = [at[8], at[40], at[100]]
        self.restored()                                           # a fresh restore: no atom built
        inside, real = [0], km._chat_history_page

        def chp(*a, **kw):
            b0 = em._ASM_INDEX_STATS["materialized"]
            try:
                return real(*a, **kw)
            finally:
                inside[0] += em._ASM_INDEX_STATS["materialized"] - b0
        built = em._ASM_INDEX_STATS["materialized"]
        with mock.patch.object(km, "_chat_history_page", chp):
            self.assertGreater(km._warm_history_pages(self._feed(self._card(anchors)), NOW, {}), 0)
        self.assertGreater(inside[0], 0, "the renders build their pages' atoms")
        self.assertEqual(em._ASM_INDEX_STATS["materialized"] - built, inside[0], "and nothing else in the pass builds one")
        self.assertTrue(self._hit(anchors[1]), "the anchors' windows as the click asks for them")

    def test_a_readers_requests_are_counted_apart_from_the_warms_renders(self):
        """2026-10-27: `hits` and `misses` count the warm's renders as misses, so a cache that had served no reader for hours
        read as one that missed every reader. A reader's pages are counted apart, served or rendered, and of the warm's
        pages, those a reader was then served (once each) and those that left the cache before any reader was."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        st = km._PAGE_STATS
        self.assertEqual(km._warm_history_pages(self._feed(self._card([first(8), first(40)])), NOW, {}), 4)
        self.assertEqual((st["warmed"], st["misses"], st["hits"], st["clientHits"], st["clientRendered"], st["warmServed"],
                          st["warmUnserved"]), (4, 4, 0, 0, 0, 0, 0), "the warm's renders: no reader's")
        self.assertTrue(self._hit(first(8)), "a click on the first window")
        self.assertEqual((st["hits"], st["clientHits"], st["warmServed"]), (2, 2, 2), "two pages served, both the warm's")
        self.assertTrue(self._hit(first(8)))
        self.assertEqual((st["clientHits"], st["warmServed"]), (4, 2), "served again: a warm page counts once")
        self.assertEqual(self._reader(), 1)
        self.assertEqual((st["clientRendered"], st["misses"]), (1, 5), "a page no warm rendered: the reader's render")
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = 1                                      # the next store leaves only itself: every other page goes
        try:
            self._reader(lo=km._RENDER_FLOOR[SID] - 2 * km.PAGE_TURNS)
        finally:
            km._PAGE_CACHE_BYTES = saved
        self.assertEqual((st["warmUnserved"], st["warmServed"]), (2, 2), "the second window's pages left unread")

    def test_a_page_the_warm_rendered_that_left_unread_is_not_rendered_again_until_a_reader_asks(self):
        """2026-10-27, the live pattern: hours of a board whose order changed as sessions worked, and no reader asking for a
        page below a floor, read on /perf as a warm render for nearly every eviction and no hit. Three boards take turns on
        top, each with two windows; the set holds one board's and the cache two boards' pages. Each time a board came back
        its pages had been evicted by the others' and the warm rendered them again, every rotation, for nobody. A page the
        warm rendered that is gone with no reader's request since is not rendered again now; a reader's request re-arms it."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        sizes = self._sizes_then_empty(range(0, 192, km.PAGE_TURNS))   # the pages' sizes known: the byte bound is exact
        km._PAGE_STATS.update(hits=0, misses=0, evictions=0, clientHits=0, clientRendered=0, warmed=0, warmServed=0, warmUnserved=0)
        gb = [sum(sizes[lo] for lo in range(64 * k, 64 * k + 64, km.PAGE_TURNS)) for k in range(3)]
        boards = [self._feed(self._card([first(64 * k + 8), first(64 * k + 40)])) for k in range(3)]   # [64k, 64k+32), [64k+32, 64k+64)
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = 2 * max(gb) + 2                        # the set: one board's four pages; the cache: two boards'
        try:
            self.assertGreater(sum(gb), km._PAGE_CACHE_BYTES, "three boards' pages overflow the cache")
            renders = [km._warm_history_pages(boards[i % 3], NOW, {}) for i in range(9)]
            self.assertEqual(renders[:3], [4, 4, 4], "each board's windows rendered once")
            self.assertEqual(renders[3:], [0] * 6, "a board coming back to evicted pages: nothing rendered again")
            st = km._PAGE_STATS
            self.assertGreater(st["evictions"], 0)
            self.assertEqual(st["warmUnserved"], st["evictions"], "every eviction a warm page no reader was served")
            self.assertEqual((st["hits"], st["clientHits"], st["clientRendered"], st["warmed"]), (0, 0, 0, 12))
            self.assertEqual(km._warm_history_pages(boards[0], NOW, {}), 0)
            self.assertGreater(st["warmSpent"], 0, "the first board's evicted window waits for a reader")
            self.assertTrue(self._hit(first(72)), "a reader's click on the second board's card, served from the cache")
            self.assertEqual((st["clientHits"], st["warmServed"]), (2, 2))
            self.assertGreater(km._warm_history_pages(boards[0], NOW, {}), 0, "re-armed: the board on top is warmed again")
            self.assertEqual(km._PAGE_STATS["warmSpent"], 0)
            self.assertTrue(self._hit(first(8)) and self._hit(first(40)), "…and its clicks land from the cache")
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_a_window_waiting_for_a_reader_keeps_its_room_so_the_set_stays_the_boards_first_cards(self):
        """The set is the board's first cards (2026-10-25). A window waiting for a reader keeps its room in the set, so the
        warm does not go on down the board and render the next card's window in its place, which would be as speculative
        and further from what a reader clicks."""
        whole, m = self._restored(turns=600, compact_every=150)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        first = lambda j: next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid"))
        sizes = self._sizes_then_empty([0, 16, 32, 48])
        top, nxt = sizes[0] + sizes[16], sizes[32] + sizes[48]
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = 2 * max(top, nxt) + 2                  # half the cache: either card's window, not both
        try:
            feed = self._feed(self._card([first(8)], item="top"), self._card([first(40)], item="next"))   # [0, 32), [32, 64)
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 2, "the top card's window")
            self.assertEqual(km._PAGE_STATS["warmPending"], 1, "the next card's waits past the set's bytes")
            with km._page_lock:                                       # the top window's pages leave before any reader asked
                for k in [k for k in km._PAGE_CACHE if k[1] < 32]:
                    km._PAGE_STATS["bytes"] -= km._PAGE_CACHE.pop(k)[1]
                km._PAGE_STATS["pages"] = len(km._PAGE_CACHE)
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "neither window rendered")
            self.assertEqual((km._PAGE_STATS["warmSpent"], km._PAGE_STATS["warmPending"]), (1, 1),
                             "the top window waits for a reader and keeps its room: the next card's still waits")
            self._reader()
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 2, "a reader's request: the top card's window again")
            self.assertEqual((km._PAGE_STATS["warmSpent"], km._PAGE_STATS["warmPending"]), (0, 1))
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_a_set_re_keyed_before_a_reader_asked_waits_for_one(self):
        """2026-10-27, the other way warmed pages went unread: a page's key moves with the shared components of the chat's
        signature (a card cleared on the board, a rename), with a seam, with the floor, and every move had the warm render
        the whole set again under the new key, though no reader had asked for a page of the set under the old one. The set
        waits for a reader's request now, which re-arms it under the key that stands then."""
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"], whole[60]["uuid"]]))
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0)
        for i in range(3):                                            # a card cleared on the board, three times: every key moves
            sig = km._page_sig(self.rows[0], SID, NOW)
            with open(jd.STATE / "cleared.jsonl", "a") as fh:
                fh.write(json.dumps({"id": "n%d" % i, "t": NOW + i, "op": "clear"}) + "\n")
            self.assertNotEqual(km._page_sig(self.rows[0], SID, NOW), sig)
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "re-keyed with no reader since: nothing rendered")
        self.assertEqual((km._PAGE_STATS["warmed"], km._PAGE_STATS["warmSpent"]), (n, 2))
        self._reader()
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), n, "a reader's request re-arms the set under the current key")
        self.assertTrue(self._hit(whole[5]["uuid"]) and self._hit(whole[60]["uuid"]))

    def test_the_warm_checks_no_pages_live_inputs_and_its_reader_does(self):
        """2026-10-27: the warm checked every held page's live inputs on every pass (an Agent card's file and report, an
        output tail, a path token), to render a moved page ahead of a click. It may not render a page again before a reader
        asks anyway, so it looks at residency alone; the reader a page is served to checks them (_page_resident) and
        renders a moved page before serving it, as it always did."""
        whole, m = self._restored()
        anchors = [whole[5]["uuid"], whole[60]["uuid"]]
        calls = [0]
        real = km._page_live_moved

        def spy(*a, **kw):
            calls[0] += 1
            return real(*a, **kw)
        with mock.patch.object(km, "_page_live_moved", spy):
            self.assertGreater(km._warm_history_pages(self._feed(self._card(anchors)), NOW, {}), 0)
            self.assertEqual(km._warm_history_pages(self._feed(self._card(anchors)), NOW, {}), 0, "the settled probe")
            self.assertEqual(km._warm_history_pages(self._feed(self._card(list(reversed(anchors)))), NOW, {}), 0, "a full pass")
            self.assertEqual(calls[0], 0, "no live input checked by the warm")
            self.assertTrue(self._hit(anchors[0]))
            self.assertGreater(calls[0], 0, "the click's serve checks them")

    def test_the_window_is_two_aligned_pages_around_the_anchor(self):
        w = km._window_turns
        self.assertEqual(w(3, 400), (0, 32), "the head's page and the next")
        self.assertEqual(w(20, 400), (0, 32), "the first half of a page: the page before it")
        self.assertEqual(w(24, 400), (16, 48), "the second half: the page after it")
        self.assertEqual(w(390, 400), (368, 400), "clipped at the floor")
        self.assertEqual(w(8, 12), (0, 12))

    def test_the_wiring(self):
        src = open(os.path.join(P.BIN, "romp-kernel")).read()
        send = src.rindex('_PERF_STATS.stage("push.send", time.monotonic() - _t_stage)')   # the send stage's close, after the bars; the feed's send closes the same stage earlier, before the timeline (2026-10-03)
        block = src[send:src.index("def _broadcast_restarting", send)]
        self.assertIn("_warm_history_pages(_fs, now, live_map)", block, "the pusher warms AFTER its send stage")
        self.assertIn('if any(c["app"] in ("feed", "fleet") for c in targets) and _fs and not connect and any(c.get("proto") == 2 for c in chat_clients):', block,
                      "only with a board client (feed or fleet, never the chat alone) among the targets and a proto-2 chat client connected")
        self.assertIn('_PERF_STATS.stage("push.warm", time.monotonic() - _t_stage)', block, "its own stage key")
        self.assertNotIn("_warm_history_pages(feed_src", src, "the hook before the ledgers and the timeline is gone")
        self.assertIn("lo, hi = _window_turns(j, floor)", src, "loadAround's window is the warm's")
        self.assertEqual(sorted(k for k in km._PAGE_STATS if k.startswith("warm")),
                         ["warmCycles", "warmMs", "warmPending", "warmServed", "warmSkipped", "warmSpent", "warmUnserved", "warmed"])
        self.assertEqual(sorted(k for k in km._PAGE_STATS if k.startswith("client")), ["clientHits", "clientRendered"])
        self.assertEqual(km.WARM_ANCHORS_MAX, 32)
        self.assertEqual(sorted(km._WARM_MEMO), ["anchors", "asks", "keys", "sigs"],
                         "the remembered resident set: the anchor list, its page keys, the signatures, the readers' count")
        hist = src[src.index("def _history_pages("):src.index("# ── warming the pages cache")]
        self.assertNotIn("warm=True", hist, "a history reply's pages are a reader's")
        warm = src[src.index("def _warm_history_pages("):src.index("def _turn_of_uuid(")]
        self.assertIn("warm=True", warm, "the warm's renders are the warm's")
        self.assertNotIn("_page_resident(", warm, "the warm checks no page's live inputs")


if __name__ == "__main__":
    unittest.main()
