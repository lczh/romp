#!/usr/bin/env python3
"""GET /sessions is served from the cycle's snapshot (plans/sessions-route-from-the-cycle.md): the pusher's cycle builds the
listing once when its exact key moved (the live rows, the names snapshot, the working-notes store, the registry revision, the
compacting bits, the launch errors) and every request serves the kept JSON; a request before the first cycle builds once and the
cycle keeps that build under its key; the ?threads=1 rows and GET /sessions/by-fsid's claims are the cycle's too, each under its
own key, and no request reads a session, a thread or a record. The postal bus reads this route for its roster (list_agents, the
send's liveness check), so a session's start, rename and death reach the roster within one cycle, and the rows keep the
fields the bus reads. Hermetic: a temp state root, two synthetic sessions on disk, the live map stubbed."""
import errno
import http.client
import inspect
from unittest import mock
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from romp_load import load_source
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_sessions_listing", os.path.join(BIN, "romp-kernel"))
jd = km.jd
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))   # the module name the kernel reads the revision through
NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-565656565656"
FSID = "11111111-2222-3333-4444-888888888888"       # a transcript id a /clear minted under SID
BUS_FIELDS = {"id", "name", "state", "dir", "bg", "fg", "lastSid", "compacting", "working", "backend"}   # what the postal bus's
#                                                                                                          roster and the picker read


class _Listing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, km.WORKING_DIR, km.Sessions.live, km._sdk)
        names = td / "names"; names.mkdir()
        proj = td / "projects"; proj.mkdir()
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"; jd.GOALDIR.mkdir()
        jd.STATE = td
        km.NAMES = names
        km.WORKING_DIR = td / "working"; km.WORKING_DIR.mkdir()
        km._sdk = lambda: None
        cdir = td / "work"; cdir.mkdir()
        self.cdir = cdir
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.pdir, self.names = pdir, names
        rec = {"type": "user", "timestamp": "2026-06-11T00:00:00.000Z", "uuid": "u1", "parentUuid": None, "promptSource": "typed",
               "message": {"role": "user", "content": "hello there"}}
        for sid, name in ((SID, "web"), (SID2, "api")):
            (pdir / (sid + ".jsonl")).write_text(json.dumps(rec) + "\n")
            (names / sid).write_text("%s\t%s\t#abcdef\n" % (name, str(cdir)))
        meta = {"state": "waiting", "since": NOW - 5, "model": "", "effort": "", "context": None, "compactPct": None,
                "color": None, "mode": "", "backend": "sdk"}
        self.row = {SID: dict(meta), SID2: dict(meta)}
        km.Sessions.live = lambda: dict(self.row)
        self._reset()
        with km._clients_lock:
            self.saved_clients = list(km._clients); km._clients[:] = []

    def _reset(self):
        reset = getattr(km, "_sessions_listing_reset", None)
        if reset is not None:
            reset()
        else:                                                          # absent at the base
            getattr(km, "_SESSIONS_LISTING", {}).update({"key": None, "rows": None, "json": None, "threads": None, "threadsKey": None,
                                                         "built": 0, "served": 0, "requestBuilt": 0, "missBy": {}})

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, km.WORKING_DIR, km.Sessions.live, km._sdk) = self.saved
        with km._clients_lock:
            km._clients[:] = self.saved_clients
        self._reset()
        for slot in ("snapshot", "sessions", "paths", "names", "files_stat", "files_dirty"):
            setattr(km._live_scope, slot, None)
        self.td.cleanup()

    def _cycle(self):
        km._pusher_cycle()

    def _body(self, threads=False):
        serve = getattr(km, "_sessions_listing_serve", None)
        if serve is None:                                            # the base: the route builds per request
            return km._session_rows() + (km._thread_rows() if threads else [])
        out = serve(threads=threads)
        if isinstance(out, tuple):                                   # (status, body): a stale listing is refused, not served
            self.assertEqual(out[0], 200, out[1])
            out = out[1]
        return json.loads(out)

    def _stats(self):
        L = getattr(km, "_SESSIONS_LISTING", {})
        return {k: L.get(k, 0) for k in ("built", "served", "requestBuilt")}, dict(L.get("missBy", {}))

    def _count_builds(self, builds):
        """A seam on the row builder: the cycle's (_session_rows_from) on the head, the request's (_session_rows) at the base."""
        name = "_session_rows_from" if hasattr(km, "_session_rows_from") else "_session_rows"
        real = getattr(km, name)
        setattr(km, name, (lambda live_map: (builds.append(1), real(live_map))[1]) if name == "_session_rows_from"
                else (lambda: (builds.append(1), real())[1]))
        return name, real


class OneListingPerChange(_Listing):
    def test_two_cycles_with_nothing_moved_build_once_and_serve_equal_bodies(self):
        builds = []
        name, real = self._count_builds(builds)
        try:
            self._cycle(); self._cycle(); self._cycle()
            a, b = self._body(), self._body()
        finally:
            setattr(km, name, real)
        self.assertEqual(len(builds), 1, "one build across three quiet cycles (the base built per request, never per cycle)")
        self.assertEqual(a, b)
        self.assertEqual({r["id"] for r in a}, {SID, SID2})
        st, miss = self._stats()
        self.assertEqual((st["built"], st["served"], st["requestBuilt"]), (1, 2, 0))
        self.assertEqual(miss, {"first": 1})
        for r in a:
            self.assertTrue(BUS_FIELDS <= set(r), "the fields the bus reads stand: %r" % sorted(r))

    def test_each_key_input_moving_rebuilds_once_and_the_served_row_carries_the_change(self):
        self._cycle()
        self.row[SID]["state"] = "working"                                           # a state change (the live row)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["state"], "working")
        (self.names / SID).write_text("web-two\t%s\t#abcdef\n" % str(self.cdir))     # a rename (the names snapshot)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["name"], "web-two")
        (km.WORKING_DIR / SID2).write_text("editing the listing")                    # a working note (the notes store)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID2)["working"], "editing the listing")
        sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": SID2})   # a registry write (lastSid rides it)
        self._cycle()
        st, miss = self._stats()
        self.assertEqual(st["built"], 5, "one build per moved input: %r" % (miss,))
        self.assertEqual(set(miss), {"first", "rows", "names", "notes", "registry"}, "each miss names its input: %r" % miss)
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], 5, "and a quiet cycle builds nothing")

    def test_a_rows_launch_error_rides_the_listing_and_its_change_rebuilds_once(self):
        # `romp compact --wait` reads the row's launch error beside its compacting bit (the second review of the native
        # compaction, 2026-09-21): a compaction that ended loudly drops the compacting bit exactly as a clean end does and
        # leaves the notice on the backend's launch error, so the row carries that record, and it is a key input, or the
        # kept listing would serve the pre-failure row until some other input moved.
        errs, reads = {}, []
        saved = km._launch_error
        km._launch_error = lambda sid: (reads.append(str(sid)), errs.get(str(sid)))[1]
        self.addCleanup(setattr, km, "_launch_error", saved)
        self._cycle()
        self.assertIsNone(next(r for r in self._body() if r["id"] == SID)["launchError"], "no failure: the field is there and empty")
        # The shared read is the refresh's pair memo (listing_pairs), which bounds the listing to one read per session:
        # the cycle's launch-error memo is filled by the key's read and hit by no reader after it, kept as a backstop for a
        # second cycle reader outside the pair (the post-merge review of the listing pairing, 2026-09-22).
        self.assertEqual(reads.count(SID), 1, "one backend read per session per cycle: the key and the row share the refresh's pair memo, listing_pairs (%r)" % reads)
        errs[SID] = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                     "at": NOW + 1.5, "limit": False, "noRetry": True}
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["launchError"], errs[SID], "the backend's record, whole")
        self.assertEqual(self._stats()[0]["built"], 2, "the notice is a key input: one rebuild")
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], 2, "and a quiet cycle builds nothing")
        # a second compaction failing the same way leaves the same words at a new stamp, and `romp compact --wait`
        # tells that end from the standing notice by the stamp; so the stamp is half of the key, or the kept listing
        # would serve the old record over the fresh loud end and the wait would print done (the post-merge review of
        # the exit clause, 2026-09-21: a text-only key was pinned by nothing)
        errs[SID] = dict(errs[SID], at=NOW + 7.5)
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["launchError"], errs[SID], "the new stamp is served")
        self.assertEqual(self._stats()[0]["built"], 3, "the same words at a new stamp are a key input: one rebuild")
        errs.pop(SID)                                                                  # the next accepted turn clears it
        self._cycle()
        self.assertIsNone(next(r for r in self._body() if r["id"] == SID)["launchError"])
        self.assertEqual(self._stats()[0]["built"], 4)

    def _compaction_seams(self, comp, errs, reads=None, land=None):
        """The listing's two per-row backend reads stubbed as the world's compacting bit (`comp`, sid to bool) and launch
        error (`errs`, sid to record), the seams the launch-error test above uses; `land`, when given, runs after each
        read for SID (the hook a loud end lands through, between two reads)."""
        saved = (km._launch_error, km._compacting_now)

        def launch_error(sid):
            v = errs.get(str(sid))
            if reads is not None:
                reads.append(str(sid))
            if land is not None and str(sid) == SID:
                land()
            return v

        def compacting(sid, tm=None, path=None):
            v = comp.get(str(sid), False)
            if land is not None and str(sid) == SID:
                land()
            return v
        km._launch_error, km._compacting_now = launch_error, compacting
        self.addCleanup(lambda: (setattr(km, "_launch_error", saved[0]), setattr(km, "_compacting_now", saved[1])))

    def test_a_loud_end_between_the_keys_read_and_the_rows_build_is_never_served_as_a_clean_end(self):
        """The row read compacting fresh while its launch error came from the cycle memo filled at the key's read, so a
        loud end landing between the two reads (the bit falls, the notice lands) built a row reading compacting False with
        no notice, a clean end's shape, whenever another key input moved in the same cycle or on the first cycle (a quiet
        cycle serves the prior rows and never showed it), and `romp compact --wait` printed done over an uncompacted thread (the post-merge review
        of the native compaction, 2026-09-21). The key now reads the pair once per sid per cycle, compacting then notice,
        and the row takes that read: the served row carries the pair as the key read it, and the next cycle's key reads
        the end."""
        comp, errs, reads = {SID: True, SID2: False}, {}, []
        self._compaction_seams(comp, errs, reads)
        notice = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                  "at": NOW + 1.5, "limit": False, "noRetry": True}
        self._cycle()
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (True, None), "mid-compaction, no notice")
        real = km._session_rows_from

        def build_after_the_end(live_map):                  # the loud end lands after the key's read, before the rows' build
            comp[SID] = False; errs[SID] = notice
            return real(live_map)
        km._session_rows_from = build_after_the_end
        try:
            self.row[SID2]["state"] = "working"             # another key input moves: this cycle rebuilds the listing
            reads.clear()
            self._cycle()
        finally:
            km._session_rows_from = real
        self.assertEqual(self._stats()[0]["built"], 2, "the moved input rebuilt the listing this cycle")
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (True, None),
                         "the row carries the pair as the key read it, never (False, None), the clean end's shape the base built")
        self.assertEqual(reads.count(SID), 1, "still one backend read per session per cycle: %r" % reads)
        self._cycle()                                       # the next cycle's key reads the end: the bit down, the notice up
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (False, notice), "the loud end, one cycle on")
        st, miss = self._stats()
        self.assertEqual((st["built"], miss.get("rows")), (3, 2), "one rebuild per cycle the rows moved in: %r" % miss)

    def test_a_loud_end_between_the_pairs_two_reads_yields_compacting_with_the_notice(self):
        """The pair is read compacting then notice, so a loud end landing between its two reads yields (True, notice),
        which the wait already judges as a loud end; read notice then compacting it would yield (False, None), the clean
        end's shape (2026-09-21)."""
        comp, errs, landed = {SID: True, SID2: False}, {}, []
        notice = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                  "at": NOW + 1.5, "limit": False, "noRetry": True}

        def land():                                         # the loud end lands right after the first of SID's two reads
            if landed == ["armed"]:
                landed[:] = ["landed"]; comp[SID] = False; errs[SID] = notice
        self._compaction_seams(comp, errs, land=land)
        real_key = km._sessions_listing_key

        def key_arming_the_hook(live_map, names):           # armed inside the listing's key alone: a read of the same seams
            if arm[0]:                                      #  from elsewhere in the cycle cannot land the end
                landed.append("armed")
            try:
                return real_key(live_map, names)
            finally:
                if landed and landed[-1] == "armed":
                    landed.pop()
        arm = [False]
        km._sessions_listing_key = key_arming_the_hook
        self.addCleanup(setattr, km, "_sessions_listing_key", real_key)
        self._cycle()
        self.row[SID2]["state"] = "working"                 # another key input moves: this cycle rebuilds the listing
        arm[0] = True
        self._cycle()
        self.assertEqual(landed, ["landed"], "the end landed between the pair's reads, inside the listing's key")
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertEqual((row["compacting"], row["launchError"]), (True, notice),
                         "compacting read first: the end is read with the bit still up, a loud end to the wait")
        self.assertEqual(self._stats()[0]["built"], 2)

    def _loud_end_landing_on_the_first_read(self):
        """The two seams stubbed as the order case above stubs them: SID mid-compaction with no notice, and the Codex loud
        end (the compacting bit down and the launch-error notice up, written together) landing on SID's first seam read
        after the returned list is armed, once, since the seams call the hook after every read for SID and a second landing
        would say nothing about the order (2026-09-22). Returns (landed, notice, memos): append "armed" to `landed` right
        before the read under test, and it reads ["landed"] once the end has landed; `memos` records the thread's pair memo
        as it stood at that read, where the memo-less branch is decided (a memo checked absent after the build alone says
        only that none was LEFT open: a build that opened one around itself and closed it in a finally would pass that
        check while the branch this pin is about is no longer the one taken, the review of this pin, 2026-09-22)."""
        comp, errs, landed, memos = {SID: True, SID2: False}, {}, [], []
        notice = {"text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was",
                  "at": NOW + 1.5, "limit": False, "noRetry": True}

        def land():
            if landed == ["armed"]:
                memo = getattr(km._live_scope, "listing_pairs", None)
                memos.append(None if memo is None else dict(memo))    # as it stood at this read, not as it is filled later
                landed[:] = ["landed"]; comp[SID] = False; errs[SID] = notice
        self._compaction_seams(comp, errs, land=land)
        return landed, notice, memos

    def test_a_requests_own_row_build_with_no_refresh_open_reads_the_pair_compacting_then_notice(self):
        """A row built outside a refresh (a direct _session_rows() call; the routes serve the cycle's kept rows, and the one
        request-side build before the first cycle opens the refresh's memo like the cycle) runs with no pair memo open on its
        thread, so the pair reader takes its memo-less branch and reads the two seams fresh, compacting then notice. The order case above arms its hook
        inside the listing's key, the refresh path, and pinned nothing here: with the memo-less branch rewritten to read
        the launch error before the compacting bit, this module and every sibling that stubs the two seams stayed green
        (the post-merge review of the listing pairing, 2026-09-22). Under that rewrite a loud end landing between the two
        reads gives such a row (False, None), the clean end's shape, which `romp compact --wait` reads as done over an
        uncompacted thread.
        The pin: the memo asserted absent before one direct row build on this thread and at the read the end lands on,
        the row's name and dir checked as the head's before its pair is read (the builder's catch-all fallback row, which
        any other field's raise produces, carries (False, None) too, so a red on the pair alone could not tell the order
        from a raise elsewhere), then (True, notice), and no memo left open after."""
        landed, notice, memos = self._loud_end_landing_on_the_first_read()
        self.assertIsNone(getattr(km._live_scope, "listing_pairs", None), "no refresh is open on this thread: the memo-less branch")
        landed.append("armed")
        row = km._session_listing_row(SID, self.row[SID], {}, None)
        self.assertEqual(landed, ["landed"], "the end landed between the pair's two reads, inside the one row build")
        self.assertEqual((row["name"], row["dir"]), ("web", str(self.cdir)),
                         "the head's row, not the builder's catch-all fallback (the sid's first eight characters, no dir), whose "
                         "pair is (False, None) as well: a red on the pair below is the read order, not a raise elsewhere")
        self.assertEqual((row["compacting"], row["launchError"]), (True, notice),
                         "compacting read first on the request path too: the end is read with the bit still up, a loud end to the wait")
        self.assertEqual(memos, [None], "no pair memo at the read the end landed on: the memo-less branch, not one the build opened around itself")
        self.assertIsNone(getattr(km._live_scope, "listing_pairs", None), "and none left open after the build")

    def test_the_by_fsid_route_serves_the_listings_kept_row_and_builds_none(self):
        """GET /sessions/by-fsid answers with the row the listing keeps, built by the cycle's refresh under its pair memo
        (compacting then notice, the order the two cases above pin), so its compacting bit and launch error are the
        listing's and no ask reads either seam (2026-10-07: it built its own row per ask, on the request thread)."""
        comp, errs, reads = {SID: True, SID2: False}, {}, []
        self._compaction_seams(comp, errs, reads)
        self._cycle()
        built = []
        real = km._session_listing_row
        km._session_listing_row = lambda *a, **k: (built.append(1), real(*a, **k))[1]
        self.addCleanup(setattr, km, "_session_listing_row", real)
        del reads[:]
        code, row = km._session_by_fsid(SID)
        self.assertEqual(code, 200, row)
        self.assertEqual(row, next(r for r in self._body() if r["id"] == SID), "the listing's own row")
        self.assertIs(row["compacting"], True)
        self.assertEqual((built, reads), ([], []), "no row is built and no seam is read at the ask")

    def test_the_compacting_bit_alone_moving_rebuilds_once_each_way_and_the_row_carries_it(self):
        """The compacting bit is a key input in its own right (the design's compacting edge): a compaction starting and
        clearing with nothing else moving rebuilds the listing once each way and the served row carries the bit, so the
        pair's key half cannot be the notice's identity alone (2026-09-21)."""
        comp, errs = {SID: False, SID2: False}, {}
        self._compaction_seams(comp, errs)
        self._cycle()
        n0 = self._stats()[0]["built"]
        comp[SID] = True                                    # the compaction starts: the bit alone moves
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], n0 + 1, "the bit rising rebuilt once")
        self.assertTrue(next(r for r in self._body() if r["id"] == SID)["compacting"], "and the served row reads compacting")
        comp[SID] = False                                   # it clears: the bit alone moves back
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], n0 + 2, "the bit falling rebuilt once")
        self.assertFalse(next(r for r in self._body() if r["id"] == SID)["compacting"], "and the served row reads not compacting")
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], n0 + 2, "a quiet cycle builds nothing")

    def test_a_rows_compaction_end_record_rides_the_listing_and_moves_the_key_on_its_own(self):
        # The record `romp compact --wait` judges (the post-merge review of the wait, 2026-09-21): the backend's
        # bracket-end counter with the last end's kind, words and stamp, which the next accepted turn does not erase (it
        # clears launchError, within milliseconds when a message parked behind the compaction drains at a loud end's
        # poke). The row carries it whole, and it is a key input ON ITS OWN: in that race every other input stands still
        # (the state back to waiting, compacting False, launchError None again), so a record outside the key would leave
        # the row built before the end served until something else moved, and a wait reading it would never see the end.
        recs, reads = {}, []
        saved = km._compact_end
        km._compact_end = lambda sid: (reads.append(str(sid)), recs.get(str(sid)))[1]
        self.addCleanup(setattr, km, "_compact_end", saved)
        self._cycle()
        row = next(r for r in self._body() if r["id"] == SID)
        self.assertIn("compactEnd", row, "the field is there")
        self.assertIsNone(row["compactEnd"], "a backend that keeps no record: empty, never absent")
        self.assertEqual(reads.count(SID), 1, "one backend read per session per cycle: the key and the row share the memo (%r)" % reads)
        recs[SID] = {"ends": 0, "kind": "", "text": "", "at": None}                       # a Codex row before any end
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["compactEnd"], recs[SID], "the record, whole")
        self.assertEqual(self._stats()[0]["built"], 2, "the record is a key input: one rebuild")
        loud = {"ends": 1, "kind": "loud", "at": NOW + 1.5,
                "text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was"}
        recs[SID] = loud                     # the loud end with its notice already erased: launchError None, compacting False, state waiting
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["compactEnd"], loud)
        self.assertEqual(self._stats()[0]["built"], 3, "the end alone moved the key, nothing else did")
        self._cycle()
        self.assertEqual(self._stats()[0]["built"], 3, "and a quiet cycle builds nothing")
        recs[SID] = {"ends": 2, "kind": "clean", "text": "", "at": NOW + 9.0}
        self._cycle()
        self.assertEqual(next(r for r in self._body() if r["id"] == SID)["compactEnd"]["kind"], "clean")
        self.assertEqual(self._stats()[0]["built"], 4)
        self.assertEqual(reads.count(SID), 5, "still one read per cycle (%r)" % reads)

    def test_the_rows_compaction_end_record_is_read_from_the_owning_backend(self):
        # The live path (the post-merge review, 2026-09-21): the kernel's own read of the backend's compact_end, not a
        # stub of it. SID's backend exposes the method; SID2's has none (the SDK backend's shape, which inherits the
        # contract's None default; a backend from before the method reads the same way); and a record that is not a
        # dict reads as None, never as a row that raises.
        rec = {"ends": 1, "kind": "loud", "at": NOW,
               "text": "Codex could not compact this conversation (it reported systemError); the conversation continues as it was"}
        holder = {"rec": rec}

        class WithRecord:
            def compact_end(self, sid):
                return holder["rec"] if str(sid) == SID else None
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: WithRecord() if str(sid) == SID else object())
        self.addCleanup(setattr, km.Sessions, "backend_for", saved)
        self._cycle()
        rows = {r["id"]: r for r in self._body()}
        self.assertEqual(rows[SID]["compactEnd"], rec, "the backend's record, read live and served whole")
        self.assertIsNone(rows[SID2]["compactEnd"], "a backend without the method: None, and the row is still built")
        self.assertEqual(rows[SID2]["name"], "api", "the row is the full row, not the minimal one a raise leaves")
        holder["rec"] = ["not", "a", "record"]
        self._cycle()
        rows = {r["id"]: r for r in self._body()}
        self.assertIsNone(rows[SID]["compactEnd"], "a record that is not a dict reads as none")
        self.assertEqual(rows[SID]["name"], "web")

    def test_a_start_a_rename_and_a_death_reach_the_roster_within_one_cycle(self):
        """The postal bus's roster (list_agents, the send's liveness check) reads this route: a session that started is
        listed after one cycle, a renamed one carries its name, a dead one is gone (absence reads as death downstream)."""
        del self.row[SID2]
        self._cycle()
        self.assertEqual({r["id"] for r in self._body()}, {SID})
        self.row[SID2] = dict(self.row[SID])                                          # the start
        self._cycle()
        self.assertEqual({r["id"] for r in self._body()}, {SID, SID2})
        del self.row[SID]                                                             # the death
        self._cycle()
        self.assertEqual({r["id"] for r in self._body()}, {SID2})

    def test_a_request_before_the_first_cycle_builds_once_and_is_served_from_the_kept_listing_by_the_next(self):
        builds = []
        name, real = self._count_builds(builds)
        try:
            a = self._body(); b = self._body()
            self.assertEqual(len(builds), 1, "one build, from the registry (no cycle yet), served to both requests (the base built twice)")
            self._cycle()
            self.assertEqual(len(builds), 1, "the build is kept under its key: the first cycle finds nothing moved (rule 1)")
            self.assertEqual(self._body(), a)
            self.row[SID]["state"] = "working"
            self._cycle()
            self.assertEqual(len(builds), 2, "and rebuilds once something does")
        finally:
            setattr(km, name, real)
        st, _ = self._stats()
        self.assertEqual((st["requestBuilt"], st["built"]), (1, 2))

    def test_a_fault_in_the_listing_never_skips_the_parked_ops_and_requests_are_refused_until_a_build_lands(self):
        """1752 round two, the medium: the listing job sat inside the pending ops' try, so a raise in the key or the build skipped
        _apply_pending_ops for the cycle and the stderr line blamed pending operations; now the job has its own try and line.
        While the kept listing is stale from a fault a request is refused, loudly, and never builds for itself (2026-10-07:
        the per-request build was the walk that hung the kernel; ListingFaultAndFirstBuild below drives the route)."""
        import io
        self._cycle()
        ops = []
        real_ops = km._apply_pending_ops
        real_build = km._session_rows_from
        km._apply_pending_ops = lambda: ops.append(1)
        km._session_rows_from = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the build failed"))
        err = io.StringIO()
        try:
            self.row[SID]["state"] = "working"                       # the key moves: the cycle rebuilds and the build raises
            with mock.patch.object(sys, "stderr", err):
                self._cycle()
            self.assertEqual(ops, [1], "the parked ops still ran this cycle (the base skipped them)")
            self.assertIn("sessions-listing:", err.getvalue(), "the line names the listing, not pending operations: %r" % err.getvalue()[:200])
            code, body = km._sessions_listing_serve()
            self.assertEqual(code, 503, body)
            self.assertIn("the build failed", json.loads(body)["error"], "the refusal names the fault")
            km._session_rows_from = real_build
            self._cycle()                                              # the next cycle's build lands: served from memory again
            self.assertIsNone(km._SESSIONS_LISTING["view"]["fault"])
            self.assertEqual(next(r for r in self._body() if r["id"] == SID)["state"], "working")
        finally:
            km._apply_pending_ops = real_ops
            km._session_rows_from = real_build

    def test_a_plain_rebuild_keeps_the_thread_rows_under_their_own_key(self):
        self._cycle()
        L = km._SESSIONS_LISTING
        tkey = L["threadsKey"]
        self.assertIsNotNone(tkey, "the cycle built the thread rows and keyed them")
        self.assertEqual(L["threadsBuilt"], 1)
        self.row[SID]["state"] = "working"                           # a plain input moves: the listing rebuilds
        self._cycle()
        self.assertEqual((L["threadsKey"], L["threadsBuilt"]), (tkey, 1), "the thread rows keep their key and their build")
        self.assertIsNotNone(L["view"]["threads"])
        (jd.STATE / "session-flags.json").write_text(json.dumps({SID: {"postalServiceOff": True}}))   # the mailbox fields' store
        self._cycle()
        self.assertNotEqual(L["threadsKey"], tkey, "the flags store's stat is in the threads key")
        self.assertEqual(L["threadsMissBy"].get("flags"), 1, "and the miss names it")

    def test_the_notes_key_carries_the_inode_as_the_notes_memo_does(self):
        key = getattr(km, "_sessions_listing_key", None)
        self.assertIsNotNone(key, "the listing key exists")
        (km.WORKING_DIR / SID).write_text("a note")
        k = key(self.row, {})
        notes = k[2]
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(notes[0]), 4, "(name, mtime_ns, size, ino): %r" % (notes[0],))

    def test_the_route_serves_the_kept_json_and_threads_ride_their_own_key(self):
        self._cycle()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def get(path):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                c.request("GET", path, headers={"X-Romp-Token": km.TOKEN})
                resp = c.getresponse(); body = resp.read().decode(); c.close()
                return resp.status, body
            status, body = get("/sessions")
            self.assertEqual(status, 200)
            self.assertEqual(body, km._SESSIONS_LISTING["view"]["json"], "the kept JSON, byte for byte (the base keeps none)")
            self.assertEqual({r["id"] for r in json.loads(body)}, {SID, SID2})
            status, body = get("/sessions?threads=1")
            self.assertEqual(status, 200)
            rows = json.loads(body)
            self.assertIsInstance(rows, list)
            self.assertEqual({r["id"] for r in rows}, {SID, SID2}, "no thread sessions: the same rows")
        finally:
            srv.shutdown(); srv.server_close()

    def test_a_registry_write_of_a_field_no_row_reads_keeps_the_listing_across_five_cycles_and_a_read_field_rebuilds_once(self):
        """The design's rule 1 (one listing per change) on the registry input: on the deploy boot of 2026-09-15 the listing was
        rebuilt every cycle (built 778 in 776 s, missBy registry 767) because the key carried REG_REV, which counts every
        write, and the backend writes fields no row reads each cycle. The key's registry component is the rows revision,
        moved only by a change to lastSid, threadOf or alive."""
        builds = []
        name, real = self._count_builds(builds)
        sdkdir_saved = jd.SDKDIR
        jd.SDKDIR = Path(str(jd.STATE)) / "sdk"                  # the row's lastSid read (jd._sdk_last_sid) over this test's registry
        try:
            sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": SID})
            self._cycle()
            n0 = len(builds)
            miss0 = self._stats()[1].get("registry", 0)
            for i in range(5):
                sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": SID, "lastActivity": NOW + i, "ctxTokens": 100 + i})
                self._cycle()
            self.assertEqual(len(builds), n0, "five writes of fields no row reads: the listing is kept (the base rebuilt on each)")
            self.assertEqual(self._stats()[1].get("registry", 0), miss0, "and none counted as a registry miss")
            sb.write_reg(str(jd.STATE), SID, {"sid": SID, "alive": True, "lastSid": FSID, "lastActivity": NOW + 9})
            self._cycle()
            self.assertEqual(len(builds), n0 + 1, "a change to a field a row reads rebuilds once")
            self.assertEqual(self._stats()[1].get("registry", 0), miss0 + 1, "counted as the registry miss it is")
            row = [r for r in self._body() if r["id"] == SID][0]
            self.assertEqual(row["lastSid"], FSID, "and the served row carries the new transcript id")
            self._cycle()
            self.assertEqual(len(builds), n0 + 1, "the next cycle keeps it")
            sb.write_reg(str(jd.STATE), SID2, {"sid": SID2, "alive": True, "threadOf": SID})
            self._cycle()
            self.assertEqual(len(builds), n0 + 2, "a registration becoming a comment thread rebuilds once (threadOf is read)")
        finally:
            setattr(km, name, real)
            jd.SDKDIR = sdkdir_saved

    def test_the_registry_revision_moves_on_the_one_write_path_and_every_writer_goes_through_it(self):
        """Every write, replace or removal of a file under the SDK registry directory (STATE/sdk/<sid>.json), across kernel/,
        cli/, postal/ and bin/, goes through write_reg: the census walks each write site and reads the path expression
        behind it for the SDK registry's marks (`_reg_path(state_dir`, `/ "sdk" /`, `"sdk"` beside `.json`); the Codex
        backend's registry.json is its own table, which no listing field reads."""
        src = inspect.getsource(sb)
        self.assertIn("REG_REV[0] += 1", inspect.getsource(sb.write_reg), "the write path bumps the revision (the base has none)")
        rev = getattr(sb, "reg_rev", lambda: 0)
        rev0 = rev()
        sb.write_reg(str(jd.STATE), SID2, {"sid": SID2, "alive": True})
        self.assertEqual(rev(), rev0 + 1)
        writers = [m.start() for m in re.finditer(r"\bwrite_reg\(", src)]
        self.assertGreaterEqual(len(writers), 10, "the registry's writers all call write_reg")
        root = Path(os.path.dirname(HERE))
        marks = re.compile(r"_reg_path\((?:self\.)?state_dir|/ \"sdk\" /|\"sdk\"[^\n]*\.json|\.json[^\n]*\"sdk\"")   # the SDK registry's
        #                                                              marks alone: the Codex backend's registry.json is its own table, read by no listing field
        offenders = []
        for sub in ("kernel", "cli", "postal", "bin"):
            for f in sorted((root / sub).glob("*")):
                if not f.is_file() or f.suffix not in (".py", "") or f.name.endswith((".bats", ".sh", ".md")):
                    continue
                try:
                    text = f.read_text()
                except (OSError, UnicodeDecodeError):
                    continue
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if not re.search(r"\.write_text\(|os\.replace\(|\.unlink\(|os\.unlink\(|os\.remove\(", line):
                        continue
                    window = "\n".join(lines[max(0, i - 6):i + 1])
                    if marks.search(window) and "def write_reg" not in "\n".join(lines[max(0, i - 12):i + 1]):
                        offenders.append("%s:%d" % (f.relative_to(root), i + 1))
        self.assertEqual(offenders, [], "registry files written or removed outside write_reg")


T1 = "66666666-7777-8888-9999-0000000000b1"          # web's comment threads (synthetic)
T2 = "66666666-7777-8888-9999-0000000000b2"
T3 = "66666666-7777-8888-9999-0000000000b3"
OLD_WEB = "aaaaaaaa-2222-3333-4444-5555555555c1"     # web's transcript before its /clear
OLD_T1 = "aaaaaaaa-2222-3333-4444-5555555555b1"      # T1's transcript before a resume landed on a fresh head
NEW_T1 = "aaaaaaaa-2222-3333-4444-5555555555b2"      # the head that resume landed on: T1's lastSid now
UNKNOWN = "aaaaaaaa-2222-3333-4444-5555555555ff"


class _Running:
    """A comment thread whose session runs in this process, as the backend keeps one: a live thread object and the state the
    session reports. snapshot() counts its calls: the listing reads the state alone, never the whole snapshot."""

    def __init__(self, state):
        self.state, self.snapshots = state, 0
        self.thread = type("Alive", (), {"is_alive": staticmethod(lambda: True)})()

    def live_state(self, subs=None):
        return self.state, ""

    def snapshot(self, retry_on=None):
        self.snapshots += 1
        return {"state": self.state, "backend": "sdk"}


class _Threads(_Listing):
    """Two sessions and two comment threads of web, over a real SDK backend on this test's state root (its registry and its
    two transcript ledgers), with the kernel's real route in front: what the postal services ask, over HTTP."""

    def setUp(self):
        super().setUp()
        self.root = Path(self.td.name)
        (self.root / "session-hosts").write_text("off\n")                  # a backend over a root of its own starts no host
        self.saved_dirs = (jd.SDKDIR, jd.EPIDIR)
        jd.SDKDIR, jd.EPIDIR = self.root / "sdk", self.root / "episodes"    # the rows' lastSid read and the episode writer
        self.be = sb.SdkBackend(self.root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        km._sdk = lambda: self.be
        for sid in (SID, SID2):
            sb.write_reg(self.root, sid, {"sid": sid, "cwd": str(self.cdir), "alive": True, "lastSid": sid})
        self.comments = []
        self._thread(T1, "web-comment-1")
        self._thread(T2, "web-comment-2")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        jd.SDKDIR, jd.EPIDIR = self.saved_dirs
        super().tearDown()

    def _write_comments(self):
        p = self.root / "comments" / (SID + ".json")
        p.parent.mkdir(exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"threads": self.comments}))
        os.replace(tmp, p)                                                  # the store's own publish: a rename

    def _thread(self, tsid, name, **reg):
        """A comment thread of web as the kernel keeps one: its row in web's comments store and a registration carrying
        threadOf, written by the backend's one registry writer."""
        self.comments = [t for t in self.comments if t["tid"] != tsid] + [{"tid": tsid, "sid": tsid, "name": name, "status": "open"}]
        self._write_comments()
        sb.write_reg(self.root, tsid, dict({"sid": tsid, "cwd": str(self.cdir), "alive": True, "threadOf": SID, "lastSid": tsid}, **reg))

    def _get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path, headers={"X-Romp-Token": km.TOKEN})
        resp = c.getresponse()
        body = resp.read().decode()
        c.close()
        return resp.status, body

    def _rows(self):
        status, body = self._get("/sessions?threads=1")
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def _row(self, sid):
        return next((r for r in self._rows() if r["id"] == sid), None)

    def _count_walks(self):
        """Every read the thread rows or the by-fsid answer can cost: the backend's thread table and registry scan, the row
        builders, and each session's transcript records. Returns the list the reads append their names to."""
        walks = []

        def wrap(obj, name, instance=False):
            real = getattr(obj, name, None)
            self.assertIsNotNone(real, "the seam %s names something that exists: a missing one would count nothing" % name)
            setattr(obj, name, lambda *a, **k: (walks.append(name), real(*a, **k))[1])
            self.addCleanup((lambda: obj.__dict__.pop(name, None)) if instance else (lambda: setattr(obj, name, real)))

        for name in ("thread_sessions", "fsid_ledgers", "fork_parents", "fork_children"):
            wrap(self.be, name, instance=True)
        for name in ("_thread_rows", "_session_rows_from", "_session_rows", "_thread_reg_read", "_recorded_fsids",
                     "_fold_records"):                                       # the shared reader the ledgers are folded through
            wrap(km, name)
        for name in ("list_regs", "known_fsids"):
            wrap(sb, name)
        return walks

    def _foreign_write(self, sid, reg):
        """A registration published the way write_reg publishes one (a temp file renamed over it), by ANOTHER process: the
        outgoing kernel during a handover. This process's registry revisions do not move."""
        p = self.root / "sdk" / (sid + ".json")
        tmp = p.with_name(p.name + ".9999.foreign.tmp")
        tmp.write_text(json.dumps(reg))
        os.replace(tmp, p)

    def _faulting_reads(self, *sids):
        """Reads of these registrations raise EIO while the returned list holds True (the EMFILE/EIO a loaded host returns)."""
        armed = [True]
        targets = {str(self.root / "sdk" / (sid + ".json")) for sid in sids}
        real = Path.read_text

        def read_text(path, *a, **k):
            if armed[0] and str(path) in targets:
                raise OSError(errno.EIO, "Input/output error", str(path))
            return real(path, *a, **k)
        patcher = mock.patch.object(Path, "read_text", read_text)
        patcher.start()
        self.addCleanup(patcher.stop)
        return armed

    def _count_thread_builds(self):
        builds = []
        real = km._thread_rows
        km._thread_rows = lambda *a, **k: (builds.append(1), real(*a, **k))[1]
        self.addCleanup(setattr, km, "_thread_rows", real)
        return builds


class ThreadRowsFromTheCycle(_Threads):
    """The thread rows are a product of the pusher's cycle, like the base rows (plans/sessions-route-from-the-cycle.md, rule 3):
    built once per change under an exact key, and every request serves them from memory. Every session's and thread's postal
    service asks for this listing; when each request walked every thread for itself (the key on every request, a rebuild on a
    miss) the work grew as threads times callers, and on a host with ~150 live threads and ~240 postal services the requests
    outran the callers' 6 s cap and the kernel stopped accepting connections (2026-10-07)."""

    def test_after_a_cycle_concurrent_requests_walk_nothing_and_serve_one_body(self):
        self._cycle()
        walks = self._count_walks()
        got, errs = [], []

        def one(path):
            try:
                got.append((path,) + self._get(path))
            except Exception as e:                                          # surfaced by the assertion below
                errs.append(e)

        paths = ["/sessions?threads=1"] * 20 + ["/sessions/by-fsid?fsid=" + T1] * 10 + ["/sessions"] * 5
        ts = [threading.Thread(target=one, args=(p,)) for p in paths]
        for t in ts:
            t.start()
        for t in ts:
            t.join(15)
        self.assertEqual(errs, [])
        self.assertEqual(len(got), len(paths), "every request answered")
        self.assertEqual({s for _, s, _ in got}, {200})
        self.assertEqual(walks, [], "no request read a thread, a registry file or a transcript record: they serve the cycle's rows")
        listing = {b for p, _, b in got if p == "/sessions?threads=1"}
        self.assertEqual(len(listing), 1, "every request got the same body")
        rows = json.loads(listing.pop())
        self.assertEqual({r["id"] for r in rows}, {SID, SID2, T1, T2})
        by = {b for p, _, b in got if p.startswith("/sessions/by-fsid")}
        self.assertEqual(len(by), 1)
        self.assertEqual(json.loads(by.pop()), next(r for r in rows if r["id"] == T1), "by-fsid serves the listing's own row")

    def test_each_thread_input_moving_rebuilds_once_in_the_next_cycle_and_the_row_carries_it(self):
        builds = self._count_thread_builds()
        self._cycle()
        self._cycle()
        self.assertEqual(len(builds), 1, "the first cycle builds the thread rows, a quiet one keeps them")
        walks = self._count_walks()

        def moves(what, step, check):
            n = len(builds)
            step()
            del walks[:]
            self._get("/sessions?threads=1")
            self.assertEqual(walks, [], "%s: a request before the cycle reads nothing" % what)
            self._cycle()
            self._cycle()
            self.assertEqual(len(builds), n + 1, "%s: one rebuild in the next cycle, none in the one after" % what)
            check()

        moves("a thread starting", lambda: self._thread(T3, "web-comment-3"),
              lambda: self.assertEqual(self._row(T3)["name"], "web-comment-3"))

        def rename():
            next(t for t in self.comments if t["tid"] == T1)["name"] = "web-comment-renamed"
            self._write_comments()
        moves("a rename (the comments store)", rename, lambda: self.assertEqual(self._row(T1)["name"], "web-comment-renamed"))
        self.be.sessions[T1] = _Running("working")
        moves("its state changing", lambda: None, lambda: self.assertEqual(self._row(T1)["state"], "working"))
        moves("its state changing back", lambda: setattr(self.be.sessions[T1], "state", "waiting"),
              lambda: self.assertEqual(self._row(T1)["state"], "waiting"))
        self.assertEqual(self.be.sessions[T1].snapshots, 0, "the state alone is read, never the whole snapshot")
        self.assertEqual(self._row(T1)["mailOffWhy"], "thread", "a thread's mail is off until it is broken out")
        moves("a flags-store write", lambda: (jd.STATE / "session-flags.json").write_text(json.dumps({T1: {"threadMail": True}})),
              lambda: self.assertEqual((self._row(T1)["mailOffWhy"], self._row(T1)["postalServiceOff"]), ("", False)))
        moves("its transcript id moving", lambda: sb.write_reg(self.root, T1, {"sid": T1, "cwd": str(self.cdir), "alive": True,
                                                                                "threadOf": SID, "lastSid": NEW_T1}),
              lambda: self.assertEqual(self._row(T1)["lastSid"], NEW_T1))
        moves("its names entry (the row's dir)", lambda: (self.names / T1).write_text("web-comment-1\t/tmp/notes-api\n"),
              lambda: self.assertEqual(self._row(T1)["dir"], "/tmp/notes-api"))
        moves("a thread ending", lambda: sb.write_reg(self.root, T2, {"sid": T2, "alive": False, "threadOf": SID, "lastSid": T2}),
              lambda: self.assertIsNone(self._row(T2)))
        if os.geteuid() != 0:                                               # root reads a mode-0 file, so the case cannot arise
            reg = self.root / "sdk" / (T1 + ".json")
            try:
                moves("its registration becoming unreadable", lambda: os.chmod(reg, 0),
                      lambda: self.assertEqual(self._row(T1)["mailOffWhy"], "unreadable"))
            finally:
                os.chmod(reg, 0o644)

    def test_the_registry_is_scanned_once_per_move_of_its_directory_and_never_on_a_quiet_cycle(self):
        """The thread set is the registry's rows table, kept until the registry directory moves (every writer's publish
        moves it), so a quiet cycle scans nothing and a write is read by one scan, however many readers ask that cycle (the
        listing's thread set and lineage, and every chat build's branch chips)."""
        self._cycle()
        walks = self._count_walks()
        for _ in range(3):
            self._cycle()
            self.be.fork_children()
        self.assertEqual(walks.count("list_regs"), 0, "quiet cycles: the registry is not scanned")
        sb.write_reg(self.root, T1, {"sid": T1, "cwd": str(self.cdir), "alive": True, "threadOf": SID, "lastSid": T1,
                                     "ctxTokens": 100})
        self._cycle()
        self.be.fork_children()
        self.be.fork_parents()
        self.assertEqual(walks.count("list_regs"), 1, "a write: one scan, shared by every reader")
        self._thread(T3, "web-comment-3")
        self._cycle()
        self.assertEqual(walks.count("list_regs"), 2)
        self.assertIn(T3, {r["id"] for r in self._rows()})

    def test_by_fsid_answers_from_the_cycle_with_the_listings_row_and_reads_no_record(self):
        """GET /sessions/by-fsid serves the same kept rows, through a claims map the cycle keeps beside them: the sid, the
        current transcript, each /clear head and both ends of every resume fork, after the lineage rule."""
        jd.append_episode(SID, "head-1", OLD_WEB, 1)                        # web /cleared once: its episode log holds the old head
        sb.write_reg(self.root, T1, {"sid": T1, "cwd": str(self.cdir), "alive": True, "threadOf": SID, "lastSid": NEW_T1})
        sb.append_resume_fork(self.root, T1, OLD_T1, NEW_T1, 2)            # T1 resumed onto a fresh head
        self._cycle()
        rows = self._rows()
        walks = self._count_walks()
        for fsid, sid in ((OLD_WEB, SID), (SID, SID), (OLD_T1, T1), (NEW_T1, T1), (T2, T2)):
            status, body = self._get("/sessions/by-fsid?fsid=" + fsid)
            self.assertEqual(status, 200, (fsid, body))
            self.assertEqual(json.loads(body), next(r for r in rows if r["id"] == sid), "the listing's row for %s" % fsid)
        status, body = self._get("/sessions/by-fsid?fsid=" + UNKNOWN)
        self.assertEqual(status, 404, body)
        self.assertEqual(walks, [], "no request read a record: the claims are the cycle's")
        sb.append_resume_fork(self.root, SID2, OLD_WEB, UNKNOWN, 3)        # api's records now hold web's old transcript too
        self.assertEqual(self._get("/sessions/by-fsid?fsid=" + OLD_WEB)[0], 200, "a cycle old: the new record waits for the cycle")
        self._cycle()
        status, body = self._get("/sessions/by-fsid?fsid=" + OLD_WEB)
        self.assertEqual(status, 409, body)
        self.assertEqual(json.loads(self._get("/sessions/by-fsid?fsid=" + UNKNOWN)[1])["id"], SID2)

    def test_a_growing_ledger_is_read_from_where_the_last_read_ended(self):
        """A session's states ledger grows by a row per state change, so the cycle's claims fold each ledger through the
        shared incremental reader (event_model.fold_records): only what was appended is read, a row still being written is
        read whole next time, and a rewrite that leaves the file larger is caught by the reader's guard and read again
        from its start (a reader trusting the size alone would splice the new bytes onto the old rows)."""
        self._cycle()
        p = self.root / "states" / (SID2 + ".jsonl")
        read = lambda: sb._em._READ_BYTES.get(str(p), 0)
        sb.append_state(self.root, SID2, "working")
        self._cycle()
        before = read()
        self.assertGreater(before, 0, "api's states ledger appeared: read")
        self._cycle()
        self.assertEqual(read(), before, "nothing appended: nothing read")
        row = json.dumps({"t": 3, "resumeFork": {"from": OLD_WEB, "to": UNKNOWN}}) + "\n"
        with open(p, "a") as f:
            f.write(row)
        self._cycle()
        self.assertLess(read() - before, 2 * len(row) + 200, "only the appended bytes (and the reader's guard) are read")
        self.assertEqual(json.loads(self._get("/sessions/by-fsid?fsid=" + UNKNOWN)[1])["id"], SID2)
        with open(p, "w") as f:                                             # rewritten in place, larger, with other rows
            f.write(json.dumps({"t": 9, "resumeFork": {"from": "aaaaaaaa-2222-3333-4444-5555555555e1",
                                                       "to": "aaaaaaaa-2222-3333-4444-5555555555e2"}}) + "\n" + "\n" * 300)
        self._cycle()
        self.assertEqual(self._get("/sessions/by-fsid?fsid=" + UNKNOWN)[0], 404, "the rewrite dropped the row: read again whole")
        self.assertEqual(json.loads(self._get("/sessions/by-fsid?fsid=aaaaaaaa-2222-3333-4444-5555555555e2")[1])["id"], SID2)

    def test_a_faulted_registry_scan_is_never_kept(self):
        """A registration that will not read on the scan (EIO, EMFILE on a loaded host) is dropped from it when it was never
        read, and served from its last good copy when it was: a scan with a fault is used for that cycle and scanned again
        on the next, so a new thread is listed and an ended one leaves the moment the read heals."""
        self._cycle()
        armed = self._faulting_reads(T3, T2)
        self._thread(T3, "web-comment-3")                                   # a new thread, its registration unreadable
        sb.write_reg(self.root, T2, {"sid": T2, "alive": False, "threadOf": SID, "lastSid": T2})   # T2 ends, unreadable too
        self._cycle()
        ids = {r["id"] for r in self._rows()}
        self.assertNotIn(T3, ids, "never read: not listed while the read faults")
        self.assertIn(T2, ids, "read before: its last good copy stands while the read faults")
        armed[0] = False
        self._cycle()
        ids = {r["id"] for r in self._rows()}
        self.assertIn(T3, ids, "the read healed: the next cycle scans again and lists it")
        self.assertNotIn(T2, ids, "and the ended thread leaves")

    def test_another_processs_registry_write_is_seen_by_the_next_cycle(self):
        """The thread set and the fork lineage follow the registry directory, which every writer moves (each publish renames
        a temp file into it), not a revision only this process's writes move: a thread another process registered, an end it
        wrote, and a fork it recorded are each read by the next cycle."""
        self._cycle()
        self._foreign_write(T3, {"sid": T3, "cwd": str(self.cdir), "alive": True, "threadOf": SID, "lastSid": T3})
        self.comments.append({"tid": T3, "sid": T3, "name": "web-comment-3", "status": "open"})
        self._write_comments()
        self._cycle()
        self.assertEqual(self._row(T3)["name"], "web-comment-3", "another process's new thread is listed")
        self._foreign_write(T2, {"sid": T2, "cwd": str(self.cdir), "alive": False, "threadOf": SID, "lastSid": T2})
        self._cycle()
        self.assertIsNone(self._row(T2), "and its end is read")
        self._foreign_write(SID2, {"sid": SID2, "cwd": str(self.cdir), "alive": True, "lastSid": SID2,
                                   "forkedFrom": {"sid": SID, "name": "web", "cut": "c1", "t": 5}})
        self.assertEqual(self.be.fork_parents().get(SID2), SID, "a fork it recorded is in the lineage")
        self.assertEqual([k["sid"] for k in self.be.fork_children().get(SID, [])], [SID2], "the branch chips read the same")

    def test_a_scan_in_the_same_timestamp_tick_as_the_last_write_is_scanned_again(self):
        """The directory's timestamp is coarse: a write landing after a scan but in the same tick leaves it unchanged. A scan
        taken while the clock the timestamps come from had not yet passed the directory's own is not trusted, and the next
        call scans again (the racy-index rule)."""
        d = self.root / "sdk"
        self.be.thread_sessions()
        st = d.stat()
        with mock.patch.object(sb, "_fs_clock_ns", lambda: st.st_mtime_ns):   # the scan below lands inside the write's tick
            self.be._rows_table_memo = None
            self.be.thread_sessions()
        self._foreign_write(T3, {"sid": T3, "alive": True, "threadOf": SID, "lastSid": T3})
        os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))                    # …and the write lands in that same tick
        self.assertIn(T3, self.be.thread_sessions(), "the racy scan is not kept: the next call scans and sees the write")

    def test_the_thread_rows_lastsid_is_the_registration_the_key_read(self):
        """Two writes of a registration inside one timestamp tick: the key reads it by inode and ctime and moves, and the row
        must read the same record, never a memo keyed on the timestamp alone, which served the first write's lastSid."""
        self._cycle()
        reg = self.root / "sdk" / (T1 + ".json")
        sb.write_reg(self.root, T1, {"sid": T1, "cwd": str(self.cdir), "alive": True, "threadOf": SID, "lastSid": NEW_T1})
        self._cycle()
        self.assertEqual(self._row(T1)["lastSid"], NEW_T1)
        st = reg.stat()
        sb.write_reg(self.root, T1, {"sid": T1, "cwd": str(self.cdir), "alive": True, "threadOf": SID, "lastSid": OLD_T1})
        os.utime(reg, ns=(st.st_atime_ns, st.st_mtime_ns))                  # the second write in the first one's tick
        self._cycle()
        self.assertEqual(self._row(T1)["lastSid"], OLD_T1, "the row carries the record the key read")

    def test_a_sessions_lastsid_written_twice_in_one_tick_reaches_its_row_and_its_claims(self):
        self._cycle()
        reg = self.root / "sdk" / (SID2 + ".json")
        sb.write_reg(self.root, SID2, {"sid": SID2, "cwd": str(self.cdir), "alive": True, "lastSid": NEW_T1})
        self._cycle()
        st = reg.stat()
        sb.write_reg(self.root, SID2, {"sid": SID2, "cwd": str(self.cdir), "alive": True, "lastSid": OLD_T1})
        os.utime(reg, ns=(st.st_atime_ns, st.st_mtime_ns))
        self._cycle()
        self.assertEqual(self._row(SID2)["lastSid"], OLD_T1, "the main row's lastSid is the record, not a tick-keyed memo")
        self.assertEqual(json.loads(self._get("/sessions/by-fsid?fsid=" + OLD_T1)[1])["id"], SID2, "and so are the claims")


class ListingFaultAndFirstBuild(_Threads):
    def test_requests_before_the_first_cycle_build_once_and_the_cycle_keeps_that_build(self):
        """Rule 1: a request arriving before the first cycle builds once (however many arrive), keeps the result under the
        same key, and the first cycle rebuilds only what moved since."""
        base, threads = [], []
        real_base, real_threads = km._session_rows_from, km._thread_rows
        km._session_rows_from = lambda *a, **k: (base.append(1), real_base(*a, **k))[1]
        km._thread_rows = lambda *a, **k: (threads.append(1), real_threads(*a, **k))[1]
        self.addCleanup(setattr, km, "_session_rows_from", real_base)
        self.addCleanup(setattr, km, "_thread_rows", real_threads)
        N = 12
        entered, all_in = [], threading.Event()
        real_serve = km._sessions_listing_serve

        def serve(*a, **k):
            entered.append(1)
            if len(entered) >= N:
                all_in.set()
            return real_serve(*a, **k)
        km._sessions_listing_serve = serve
        self.addCleanup(setattr, km, "_sessions_listing_serve", real_serve)
        held = km._session_rows_from
        km._session_rows_from = lambda *a, **k: (all_in.wait(10), held(*a, **k))[1]   # the build ends after every request arrived
        got = []
        ts = [threading.Thread(target=lambda: got.append(self._get("/sessions?threads=1"))) for _ in range(N)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(15)
        km._session_rows_from = held
        self.assertEqual(len(got), N)
        self.assertEqual({s for s, _ in got}, {200})
        self.assertEqual(len({b for _, b in got}), 1, "one body")
        self.assertEqual((len(base), len(threads)), (1, 1), "one build of each before the first cycle, for %d requests" % N)
        self._cycle()
        self.assertEqual((len(base), len(threads)), (1, 1), "the first cycle keeps that build: nothing moved")
        self.row[SID]["state"] = "working"
        self._cycle()
        self.assertEqual((len(base), len(threads)), (2, 1), "and rebuilds what moved, alone")

    def test_a_fault_in_the_cycles_build_is_answered_loudly_until_a_build_lands(self):
        """The cycle is the listing's one builder: when its build fails the kept rows may be stale, and the route says so (a
        503 naming the fault, which the postal bus reads as a kernel that did not answer) rather than serving them, or
        rebuilding per request, the walk that hung the kernel."""
        self._cycle()
        real = km._session_rows_from
        km._session_rows_from = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the build failed"))
        self.addCleanup(setattr, km, "_session_rows_from", real)
        self.row[SID]["state"] = "working"
        import io
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self._cycle()
        self.assertIn("sessions-listing:", err.getvalue())
        walks = self._count_walks()
        for path in ("/sessions", "/sessions?threads=1", "/sessions/by-fsid?fsid=" + T1):
            status, body = self._get(path)
            self.assertEqual(status, 503, (path, body))
            self.assertIn("the build failed", json.loads(body)["error"], path)
        self.assertEqual(walks, [], "no request builds for itself")
        km._session_rows_from = real
        self._cycle()
        self.assertEqual(self._row(SID)["state"], "working", "the next build landed: served again")

    def test_a_fault_in_the_listings_key_is_answered_loudly_too(self):
        self._cycle()
        real = km._sessions_listing_key
        km._sessions_listing_key = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the key failed"))
        self.addCleanup(setattr, km, "_sessions_listing_key", real)
        import io
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self._cycle()
        status, body = self._get("/sessions")
        self.assertEqual(status, 503, "a key that cannot be read cannot vouch for the kept rows: %s" % body)
        km._sessions_listing_key = real
        self._cycle()
        self.assertEqual(self._get("/sessions")[0], 200)

    def test_a_fault_in_the_thread_rows_leaves_the_plain_listing_served(self):
        self._cycle()
        real = km._thread_rows
        km._thread_rows = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the thread rows failed"))
        self.addCleanup(setattr, km, "_thread_rows", real)
        self._thread(T3, "web-comment-3")                                   # a thread input moves: the cycle rebuilds them
        import io
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self._cycle()
        self.assertEqual(self._get("/sessions")[0], 200, "the plain listing does not carry the threads")
        self.assertEqual(self._get("/sessions?threads=1")[0], 503)
        self.assertEqual(self._get("/sessions/by-fsid?fsid=" + SID)[0], 503, "the claims ride the thread rows")
        km._thread_rows = real
        self._cycle()
        self.assertIn(T3, {r["id"] for r in self._rows()})


class ThreadStateReadAlone(unittest.TestCase):
    """SdkSession.live_state() is the state half of snapshot(), and what the listing reads of every running comment thread
    each cycle: driven through each of its branches on a real session, the two agree, and the backend's thread_state reads
    the same answer without taking a snapshot."""

    def test_every_branch_of_the_state_half_agrees_with_the_snapshot(self):
        root = Path(tempfile.mkdtemp())
        (root / "session-hosts").write_text("off\n")
        be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        reg = {"sid": T1, "name": "web-comment-1", "cwd": "/tmp/notes-api", "alive": True, "threadOf": SID, "lastSid": T1}
        sb.write_reg(root, T1, reg)
        s = sb.SdkSession(be, reg)
        s.thread = type("Alive", (), {"is_alive": lambda self: True})()
        be.sessions[T1] = s
        snaps = []
        real_snapshot = s.snapshot
        s.snapshot = lambda *a, **k: (snaps.append(1), real_snapshot(*a, **k))[1]
        sb.append_state(root, T1, "permission")                             # the log's last state, for the at-rest branch

        def at(**f):                                                        # the session's fields set to one case alone
            s.inflight, s.retrying, s._interrupted = 0, False, False
            for k, v in f.items():
                setattr(s, k, v)
        for what, fields, want in (("the log's last state", {}, "permission"),
                                   ("a turn in flight", {"inflight": 1}, "working"),
                                   ("an API retry", {"inflight": 1, "retrying": True}, "retrying"),
                                   ("an interrupt settling", {"inflight": 1, "_interrupted": True}, "waiting")):
            at(**fields)
            del snaps[:]
            self.assertEqual(be.thread_state(T1), want, what)
            self.assertEqual(snaps, [], "%s: the thread's state is read without a snapshot" % what)
            self.assertEqual(real_snapshot()["state"], want, "%s: the snapshot says the same" % what)
        at()
        with s._sub_lock:
            s._subagents["agent-1"] = {"type": "general", "since": 7}
        self.assertEqual((be.thread_state(T1), real_snapshot()["state"]), ("working", "working"),
                         "a background subagent outliving the turn: working, both ways")


class ThreadRegMemoBound(unittest.TestCase):
    def test_past_five_hundred_and_twelve_registrations_a_second_pass_reads_no_file(self):
        """_thread_reg_read's memo is bounded by the registrations that exist, not by a constant: with more of them than the
        old cap of 512 (a long-lived host keeps one per session it ever ran) a FIFO past the cap evicted each entry before its
        next use, and every lookup in every cycle read its file again."""
        root = Path(tempfile.mkdtemp())
        saved = jd.STATE
        jd.STATE = root
        self.addCleanup(setattr, jd, "STATE", saved)
        km._thread_reg_memo.clear()
        self.addCleanup(km._thread_reg_memo.clear)
        sids = ["11111111-2222-3333-4444-%012d" % i for i in range(600)]
        for sid in sids:
            sb.write_reg(root, sid, {"sid": sid, "alive": False})
        for sid in sids:
            km._thread_reg_read(sid)
        reads = []
        real = Path.read_text
        with mock.patch.object(Path, "read_text", lambda p, *a, **k: (reads.append(str(p)), real(p, *a, **k))[1]):
            for sid in sids:
                self.assertEqual(km._thread_reg_read(sid)[0], "ok")
        self.assertEqual(reads, [], "every registration is remembered: the second pass reads none of them")
        (root / "sdk" / (sids[0] + ".json")).unlink()
        self.assertEqual(km._thread_reg_read(sids[0])[0], "missing")
        self.assertNotIn(sids[0], km._thread_reg_memo, "a registration that is gone leaves the memo")


class ListingFaultSaidOncePerEpisode(_Threads):
    def test_a_fault_is_said_once_when_it_starts_and_once_when_it_heals(self):
        """The cycle runs twice a second or more: a fault that persists wrote its traceback every cycle. It is said once
        when it starts (with the traceback), once when it heals, and again if it comes back."""
        import io
        self._cycle()
        real = km._session_rows_from
        km._session_rows_from = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the build failed"))
        self.addCleanup(setattr, km, "_session_rows_from", real)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            for i in range(4):
                self.row[SID]["state"] = "working" if i % 2 else "waiting-%d" % i   # the key keeps moving: each cycle builds
                self._cycle()
            self.assertEqual(err.getvalue().count("Traceback (most recent call last)"), 1, err.getvalue()[-600:])
            km._session_rows_from = real
            self._cycle()
            self.assertEqual(err.getvalue().count("sessions-listing: the rows build again"), 1, err.getvalue()[-600:])
            km._session_rows_from = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the build failed"))
            self.row[SID]["state"] = "idle"
            self._cycle()
            self.assertEqual(err.getvalue().count("Traceback (most recent call last)"), 2, "a new episode is said again")


if __name__ == "__main__":
    unittest.main()
