#!/usr/bin/env python3
"""The kernel keeps the VIEWER'S arrangement, as opaque data (the user 2026-09-23).

The order sessions are shown in is computed in the browser and always will be: it spans every kernel the
dashboard has attached, and no kernel can ORDER sids belonging to a machine it has never heard of (the
2026-07-31 ruling, commit e9870995). What moved here is where the finished list is KEPT, so a phone and a
desktop looking at the same sessions read the same order instead of two unreconcilable ones.

Storing is not ordering, and that is what these pin: an arrangement naming hosts this kernel does not own
round-trips through view-order.json byte for byte, in the viewer's order, with no prefix parsed, no entry
matched against a live session and nothing pruned or re-sorted; the frame that serves it says whether this
kernel has an arrangement AT ALL (the browser's migration turns on exactly that); and a write reports
whether it CHANGED, because the change is the only thing worth pushing to the viewer's other devices.

Synthetic ids and hostnames only (the notes-api demo world, hosts TESTHOST / TESTHOST2).
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the load — the kernel resolves its state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run would otherwise write REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)   # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_view_order", os.path.join(BIN, "romp-kernel"))

# Sessions on THIS kernel, and sessions on two machines it has never heard of — the ids arrive already
# prefixed by the browser's federation layer, which is the only party that knows what a prefix means.
LOCAL_A = "11111111-2222-4333-8444-000000000001"
LOCAL_B = "11111111-2222-4333-8444-000000000002"
FAR_WEB = "TESTHOST:11111111-2222-4333-8444-000000000003"
FAR_API = "TESTHOST2:11111111-2222-4333-8444-000000000004"


def _forget_fold_stamps():
    """The fold publishes this kernel has handled are process memory (kernel.py _view_folds_by, and the count of those
    it took, _view_folds_taken): each test starts with none. getattr, so the module still loads against a kernel from
    before the stamp or the count, where every such test fails on its assertions rather than here."""
    by, seen = getattr(km, "_view_folds_by", None), getattr(km, "_view_folds_seen", None)
    taken = getattr(km, "_view_folds_taken", None)
    if by is not None:
        by[0] = None
    if seen is not None:
        seen.clear()
    if taken is not None:
        taken[0] = 0


class ViewOrderStore(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)
        # Per-session hosts are ON by default over a state root with no file (T348); this root mints none
        # and this test starts no backend, so it pins the switch off rather than leaving a host to spawn.
        (km.jd.STATE / "session-hosts").write_text("off")
        km._view_order_lkg[0] = None
        km._view_folds_lkg[0] = None
        _forget_fold_stamps()
        km._view_files_lkg[0] = None

    def tearDown(self):
        km.jd.STATE = self._state
        km._view_order_lkg[0] = None
        km._view_folds_lkg[0] = None
        _forget_fold_stamps()
        km._view_files_lkg[0] = None
        self.td.cleanup()

    def stored_file(self):
        return json.loads((km.jd.STATE / "view-order.json").read_text())

    def assertFrame(self, fr, want, msg=None):
        """A viewOrder frame compared whole, its fold count apart (2026-09-30): whenever the frame carries the folds half,
        an object or null, it carries foldsSeq, {boot: this kernel life's id, n: an int}; without that half it carries
        none; and everything else is `want` exactly."""
        fr = dict(fr)
        seq = fr.pop("foldsSeq", None)
        if "folds" in fr:
            self.assertTrue(isinstance(seq, dict) and set(seq) == {"boot", "n"} and seq["boot"] == km._BOOT_ID
                            and isinstance(seq["n"], int) and not isinstance(seq["n"], bool),
                            "the fold count beside the folds: %r (%s)" % (seq, msg))
        else:
            self.assertIsNone(seq, "no folds half, no count (%s)" % msg)
        self.assertEqual(fr, want, msg)

    def assertPushed(self, seen, clients, want, msg=None):
        """`seen` is one (client, slot, frame) per client in `clients`, on the viewOrder slot, all one frame, `want`."""
        self.assertEqual([(c, k) for c, k, _ in seen], [(c, ("vieworder",)) for c in clients], msg)
        for _, _, fr in seen:
            self.assertEqual(fr, seen[0][2], "one frame, built once (%s)" % msg)
            self.assertFrame(fr, want, msg)

    # ── the round trip ────────────────────────────────────────────────────────────────────────────
    def test_an_arrangement_over_hosts_this_kernel_does_not_own_round_trips_verbatim(self):
        # the crux: two of these ids name machines this kernel has never heard of, and one interleaves
        # them with its own — the arrangement no kernel could have computed
        arrangement = [FAR_WEB, LOCAL_A, FAR_API, LOCAL_B]
        self.assertTrue(km._write_view_order(arrangement))
        self.assertEqual(self.stored_file(), arrangement, "written whole, in the viewer's order")
        self.assertEqual(km._view_order_served(), (arrangement, True), "served back the same way")

    def test_nothing_is_parsed_pruned_or_re_sorted(self):
        # an entry for a session that is long gone, one for a host that is detached, and one whose prefix
        # is not a host this kernel could resolve: all opaque, all kept exactly where the viewer put them
        arrangement = [FAR_API, "ghost:not-a-uuid", LOCAL_B, FAR_WEB]
        km._write_view_order(arrangement)
        self.assertEqual(km._view_order_served()[0], arrangement)

    def test_non_strings_are_dropped_and_the_list_is_bounded(self):
        km._write_view_order([LOCAL_A, 7, None, {"sid": LOCAL_B}, FAR_WEB])
        self.assertEqual(km._view_order_served()[0], [LOCAL_A, FAR_WEB])
        # the backstop mirrors the browser's own (view-order.ts VIEW_ORDER_CAP): the viewer's prune is the
        # real bound, this only stops a client bug growing the file without limit — and it keeps the TAIL,
        # the most recent arrangement, as the browser's cap does
        km._write_view_order(["s%d" % i for i in range(km._VIEW_ORDER_CAP + 10)])
        kept = km._view_order_served()[0]
        self.assertEqual(len(kept), km._VIEW_ORDER_CAP)
        self.assertEqual(kept[-1], "s%d" % (km._VIEW_ORDER_CAP + 9))

    # ── what the frame says ───────────────────────────────────────────────────────────────────────
    def test_a_kernel_with_no_arrangement_says_so_and_one_with_an_empty_one_says_otherwise(self):
        # the browser's migration turns on exactly this: with no arrangement here, a browser carrying one
        # publishes it; with an EMPTY arrangement here, it does not (the emptiness is somebody's answer)
        self.assertFrame(km._view_order_frame(), {"type": "viewOrder", "order": [], "stored": False, "folds": None, "files": None})
        km._write_view_order([])
        self.assertFrame(km._view_order_frame(), {"type": "viewOrder", "order": [], "stored": True, "folds": None, "files": None})

    def test_the_frame_serves_what_was_written(self):
        km._write_view_order([FAR_WEB, LOCAL_A])
        self.assertFrame(km._view_order_frame(),
                         {"type": "viewOrder", "order": [FAR_WEB, LOCAL_A], "stored": True, "folds": None, "files": None})

    # ── the change event ──────────────────────────────────────────────────────────────────────────
    def test_a_write_reports_whether_it_changed_anything(self):
        self.assertTrue(km._write_view_order([LOCAL_A, FAR_WEB]), "a first arrangement is a change")
        self.assertFalse(km._write_view_order([LOCAL_A, FAR_WEB]),
                         "a viewer republishing the list it was served pushes nothing")
        self.assertTrue(km._write_view_order([FAR_WEB, LOCAL_A]), "a drag is a change")

    def test_the_change_is_pushed_to_every_connected_client_on_one_dedup_slot(self):
        # the send itself is _send_client's (per-client dedup: a client already holding this arrangement
        # byte for byte is sent nothing, so the connect push and the change push never double up)
        seen = []
        saved_send, saved_clients = km._send_client, list(km._clients)
        km._send_client = lambda c, key, msg, *a, **kw: seen.append((c["cid"], key, msg))
        km._clients[:] = [{"cid": "phone"}, {"cid": "desktop"}]
        try:
            km._write_view_order([FAR_WEB, LOCAL_A])
            km._broadcast_view_order()
        finally:
            km._send_client, km._clients[:] = saved_send, saved_clients
        frame = {"type": "viewOrder", "order": [FAR_WEB, LOCAL_A], "stored": True, "folds": None, "files": None}
        self.assertPushed(seen, ["phone", "desktop"], frame, "every viewer of this kernel hears the change, on the one slot")

    # ── the store survives a kernel that cannot read it ───────────────────────────────────────────
    def _raise_on_read(self):
        saved = km._read_state_json
        km._read_state_json = lambda *a, **kw: (_ for _ in ()).throw(
            km._StateUnreadable(km._view_order_path(), "read failed: EIO"))
        self.addCleanup(lambda: setattr(km, "_read_state_json", saved))

    def test_an_unreadable_store_serves_the_last_known_good_and_still_says_stored(self):
        # A file we could not read is not a file that is MISSING. Answering "no arrangement" under an EIO
        # would invite the next browser to publish its own over an order that is still sitting there.
        km._write_view_order([LOCAL_A, FAR_WEB])
        self._raise_on_read()
        self.assertEqual(km._view_order_served(), ([LOCAL_A, FAR_WEB], True))
        # (the folds and files halves read through the same patched reader and have no known-good copy: they are left out)
        self.assertEqual(km._view_order_frame(), {"type": "viewOrder", "order": [LOCAL_A, FAR_WEB], "stored": True})

    def test_an_unreadable_store_with_nothing_known_good_says_NOTHING_rather_than_a_guess(self):
        # The fail-loudly rule, applied to a push: [] would have every viewer adopt an empty arrangement
        # over the one they are showing, and "stored" would be a claim about a file we have not read. No
        # frame goes; every page keeps what it has, and the read fault is filed once per episode.
        self._raise_on_read()
        self.assertIsNone(km._view_order_served())
        self.assertIsNone(km._view_order_frame())
        seen = []
        saved_send, saved_clients = km._send_client, list(km._clients)
        km._send_client = lambda c, key, msg, *a, **kw: seen.append(msg)
        km._clients[:] = [{"cid": "phone"}]
        try:
            km._send_view_order(km._clients[0])
            km._broadcast_view_order()
        finally:
            km._send_client, km._clients[:] = saved_send, saved_clients
        self.assertEqual(seen, [])

    def test_corrupt_bytes_read_as_no_arrangement_so_the_next_viewer_refills_it(self):
        # _read_state_json quarantines the bad bytes aside (they survive for forensics) and the store
        # starts empty — which says "no arrangement", and the first browser to connect publishes its own.
        km._write_view_order([LOCAL_A, FAR_WEB])
        km._view_order_lkg[0] = None                 # a kernel that restarted onto the bad file
        (km.jd.STATE / "view-order.json").write_text("{ not json")
        self.assertEqual(km._view_order_served(), ([], False))


# The fold state the browser keeps beside the arrangement (tab-groups.ts TabFolds): tag names folded, a
# default-folded one opened, a member pinned through its fold, and the rename memory those pins rest on.
FOLDS = {"collapsed": ["api"], "expanded": ["archived"],
         "pinned": [{"sid": FAR_WEB, "name": "api", "id": "g2"}], "followed": {"g2": "api"}}


class ViewFoldsStore(ViewOrderStore):
    """The viewer's FOLDS, kept beside the arrangement (the user 2026-09-23, who wanted the phone to fold too
    and the folded groups shared across devices like the order). Same store, same frame, same push: the
    viewOrder frame carries `folds` beside `order`, and a change to either is a push of that one frame. The
    object is opaque here as the list is: a round trip returns it as written, with nothing inside it read.

    Inherits the store's setUp (a private state root, session-hosts off) and so reruns its tests too, which
    is the point: the arrangement's behaviour is unchanged by the half riding beside it."""

    def stored_folds(self):
        return json.loads((km.jd.STATE / "view-folds.json").read_text())

    def test_folds_round_trip_verbatim_and_ride_the_arrangements_frame(self):
        self.assertTrue(km._write_view_folds(FOLDS))
        self.assertEqual(self.stored_folds(), FOLDS, "written whole")
        self.assertEqual(km._view_folds_served(), FOLDS)
        km._write_view_order([FAR_WEB, LOCAL_A])
        self.assertFrame(km._view_order_frame(),
                         {"type": "viewOrder", "order": [FAR_WEB, LOCAL_A], "stored": True, "folds": FOLDS, "files": None},
                         "one frame: the arrangement and the folds side by side")

    def test_no_folds_is_null_and_an_empty_fold_state_is_not(self):
        # the browser's migration turns on this, as it does on `stored`: with nothing here, a browser that
        # has folds of its own publishes them; with an emptied state here (every group opened), it must not
        # refill it from its own old key
        self.assertIsNone(km._view_order_frame()["folds"])
        empty = {"collapsed": [], "expanded": [], "pinned": []}
        km._write_view_folds(empty)
        self.assertEqual(km._view_order_frame()["folds"], empty)

    def test_a_fold_write_reports_whether_it_changed(self):
        self.assertTrue(km._write_view_folds(FOLDS), "a first fold state is a change")
        self.assertFalse(km._write_view_folds(json.loads(json.dumps(FOLDS))),
                         "a viewer republishing the state it was served pushes nothing")
        self.assertTrue(km._write_view_folds(dict(FOLDS, collapsed=[])), "opening a group is a change")

    def test_a_non_object_or_an_oversized_one_is_refused_whole(self):
        with self.assertRaises(km._StateUnwritable):
            km._write_view_folds(["api"])
        with self.assertRaises(km._StateUnwritable):
            km._write_view_folds({"collapsed": ["x" * 64] * (km._VIEW_FOLDS_CAP // 64)})
        self.assertFalse((km.jd.STATE / "view-folds.json").exists(), "nothing written: a cut object is a different state")

    def test_the_socket_message_writes_and_pushes_one_frame_to_every_viewer(self):
        # through the real dispatcher: setViewFolds stores the object and pushes the one viewOrder frame on
        # its slot to every connected client; an unchanged republish pushes nothing
        seen = []
        saved_send, saved_clients = km._send_client, list(km._clients)
        km._send_client = lambda c, key, msg, *a, **kw: seen.append((c["cid"], key, msg))
        km._clients[:] = [{"cid": "phone"}, {"cid": "desktop"}]
        handler = object.__new__(km.Handler)
        poster = {"cid": "desktop", "app": "chat", "alive": True, "send": lambda s: seen.append(("reply", json.loads(s)))}
        try:
            km.Handler._dispatch_ws(handler, {"type": "setViewFolds", "folds": FOLDS, "files": None}, poster)
            first = list(seen)
            del seen[:]
            km.Handler._dispatch_ws(handler, {"type": "setViewFolds", "folds": FOLDS, "files": None}, poster)
            again = list(seen)
        finally:
            km._send_client, km._clients[:] = saved_send, saved_clients
        frame = {"type": "viewOrder", "order": [], "stored": False, "folds": FOLDS, "files": None}
        self.assertPushed(first, ["phone", "desktop"], frame)
        self.assertEqual(again, [], "the same folds again: no change, no push")
        self.assertEqual(self.stored_folds(), FOLDS)

    def test_a_refused_fold_write_is_said_to_the_viewer_who_folded(self):
        seen = []
        handler = object.__new__(km.Handler)
        poster = {"cid": "desktop", "app": "chat", "alive": True, "send": lambda s: seen.append(json.loads(s))}
        saved = km._write_state_json
        km._write_state_json = lambda *a, **kw: (_ for _ in ()).throw(km._StateUnwritable(km._view_folds_path(), "[Errno 28] No space left on device"))
        try:
            km.Handler._dispatch_ws(handler, {"type": "setViewFolds", "folds": FOLDS, "files": None}, poster)
        finally:
            km._write_state_json = saved
        self.assertEqual([m["type"] for m in seen], ["warn"])
        self.assertIn("folded groups", seen[0]["text"])

    def test_an_unreadable_fold_store_serves_its_last_known_good_else_leaves_the_half_out(self):
        km._write_view_folds(FOLDS)
        km._write_view_order([LOCAL_A])
        saved = km._read_state_json

        def flaky(path, *a, **kw):
            if Path(path).name == "view-folds.json":
                raise km._StateUnreadable(path, "read failed: EIO")
            return saved(path, *a, **kw)
        km._read_state_json = flaky
        self.addCleanup(lambda: setattr(km, "_read_state_json", saved))
        self.assertEqual(km._view_order_frame()["folds"], FOLDS, "a file we could not read is not a missing one")
        km._view_folds_lkg[0] = None                 # a kernel that has never read it
        self.assertEqual(km._view_order_frame(), {"type": "viewOrder", "order": [LOCAL_A], "stored": True, "files": None},
                         "no folds half at all: every page keeps the folds it shows rather than adopting a guess, "
                         "and the arrangement still goes")


    # ── which publish a frame reflects (2026-09-29) ─────────────────────────────────────────────────────
    # A page that folded two groups inside one round trip heard the answer to its first fold after publishing the
    # second, and adopting it reopened the second group. Each publish now carries its name, {browser, n}, and the frame
    # names the last publish this kernel handled (`foldsBy`), so the page can pass over its own earlier state coming back.
    B = "b" * 32                                   # two browsers' ids (a page mints 32 hex characters)
    C = "c" * 32

    def dispatch(self, *msgs):
        """Each message through the real dispatcher, from the desktop's socket, with the phone and the desktop connected:
        per message, what each client was sent on the viewOrder slot (no dedup here: the real slot's is in the drop test),
        and the replies to the poster. The kernel's log lines for the whole call land in self.logged."""
        seen = []
        saved_send, saved_clients = km._send_client, list(km._clients)
        km._send_client = lambda c, key, msg, *a, **kw: seen.append((c["cid"], key, msg))
        km._clients[:] = [{"cid": "phone"}, {"cid": "desktop"}]
        handler = object.__new__(km.Handler)
        poster = {"cid": "desktop", "app": "chat", "alive": True, "send": lambda s: seen.append(("reply", json.loads(s)))}
        out = []
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                for m in msgs:
                    del seen[:]
                    km.Handler._dispatch_ws(handler, m, poster)
                    out.append(list(seen))
        finally:
            km._send_client, km._clients[:] = saved_send, saved_clients
            self.logged = err.getvalue().splitlines()
        return out

    @staticmethod
    def publish(folds, by="absent"):
        m = {"type": "setViewFolds", "folds": folds}
        if by != "absent":
            m["by"] = by
        return m

    def test_a_named_publish_is_pushed_with_its_name_to_every_viewer_and_the_file_keeps_the_folds_alone(self):
        by = {"browser": self.B, "n": 1}
        [sent] = self.dispatch(self.publish(FOLDS, by))
        frame = {"type": "viewOrder", "order": [], "stored": False, "folds": FOLDS, "foldsBy": by, "files": None}
        self.assertPushed(sent, ["phone", "desktop"], frame,
                          "every viewer, the one that folded included: its echo is how its page hears the publish land")
        self.assertEqual((km.jd.STATE / "view-folds.json").read_text(), json.dumps(FOLDS, sort_keys=True),
                         "the browser's object byte for byte: the name is memory, never the file's")

    def test_an_unchanged_publish_pushes_nothing_and_its_name_is_the_one_served_next(self):
        [first, again] = self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 1}), self.publish(FOLDS, {"browser": self.B, "n": 2}))
        self.assertEqual(len(first), 2)
        self.assertEqual(again, [], "no change, no push")
        self.assertEqual(km._view_order_frame().get("foldsBy"), {"browser": self.B, "n": 2},
                         "the next frame (an arrangement's push, a connect) names the unchanged publish: what the page last said")

    def test_a_refused_publish_is_warned_and_its_name_is_the_one_served_next(self):
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 1}))
        saved = km._write_state_json
        km._write_state_json = lambda *a, **kw: (_ for _ in ()).throw(km._StateUnwritable(km._view_folds_path(), "[Errno 28] No space left on device"))
        try:
            [full] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 2}))
        finally:
            km._write_state_json = saved
        self.assertEqual([m[1]["type"] for m in full], ["warn"], "a warning to the poster, no push")
        self.assertEqual(km._view_order_frame().get("foldsBy"), {"browser": self.B, "n": 2})
        [big] = self.dispatch(self.publish({"collapsed": ["x" * 64] * (km._VIEW_FOLDS_CAP // 64)}, {"browser": self.B, "n": 3}))
        self.assertEqual([m[1]["type"] for m in big], ["warn"])
        self.assertEqual(km._view_order_frame().get("foldsBy"), {"browser": self.B, "n": 3}, "an object over the cap, the same")
        self.assertEqual(self.stored_folds(), FOLDS, "and nothing written by either")

    def test_an_unnamed_publish_after_a_named_one_leaves_the_frame_unnamed(self):
        # a page from before the name: its frame reflects a publish nobody named, and every page adopts it as before
        [_, plain] = self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 1}), self.publish(dict(FOLDS, collapsed=[])))
        self.assertEqual([("foldsBy" in m[2]) for m in plain], [False, False])
        self.assertNotIn("foldsBy", km._view_order_frame())

    def test_a_malformed_name_counts_as_none(self):
        junk = [None, "b", [self.B, 1], {"browser": 7, "n": 1}, {"browser": "", "n": 1}, {"browser": "x" * 65, "n": 1},
                {"browser": self.B, "n": 0}, {"browser": self.B, "n": -1}, {"browser": self.B, "n": True}, {"browser": self.B, "n": 1.5},
                {"browser": self.B, "n": "2"}, {"browser": self.B, "n": 2 ** 53 + 1}, {"browser": self.B}]
        for i, by in enumerate(junk):
            [sent] = self.dispatch(self.publish(dict(FOLDS, collapsed=["g%d" % i]), by))
            self.assertEqual([("foldsBy" in m[2], m[2]["folds"]["collapsed"]) for m in sent], [(False, ["g%d" % i])] * 2, repr(by))
        [edge] = self.dispatch(self.publish(FOLDS, {"browser": "x" * 64, "n": 2 ** 53}))
        self.assertEqual(edge[0][2].get("foldsBy"), {"browser": "x" * 64, "n": 2 ** 53}, "the bounds themselves are a name")

    def test_the_folds_their_name_and_the_count_are_read_in_one_acquisition_of_the_stores_lock(self):
        # A publish landing at the first moment the lock is free after the folds are read (here, injected at that very
        # release) must pair neither its name nor its count with the folds before it: all three describe the earlier
        # publish. A second acquisition for either read, or a read after the release, would take the injected one's.
        # And the folds themselves are read with the lock held (2026-09-30, the second review round): read above the
        # block, a publish landing between that read and the acquisition would put its name and count on the older folds,
        # which the injection at the release cannot see.
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 1}))
        want = km._view_order_frame()
        real, saved_served = km._view_order_lock, km._view_folds_served
        state = {"read": False, "injected": False, "locked": None}

        def served():
            state["read"] = True
            state["locked"] = real.locked()
            return saved_served()

        C = self.C

        class InjectAtRelease:
            """The store's lock, which lets a publish in at the first release after the folds were read."""
            def __enter__(self):
                real.acquire()
                return self

            def __exit__(self, *exc):
                real.release()
                if state["read"] and not state["injected"]:
                    state["injected"] = True
                    km._write_view_folds(dict(FOLDS, collapsed=["web"]), {"browser": C, "n": 1})
                return False

            def acquire(self, *a, **kw):
                return real.acquire(*a, **kw)

            def release(self):
                real.release()

            def locked(self):
                return real.locked()
        km._view_order_lock, km._view_folds_served = InjectAtRelease(), served
        try:
            fr = km._view_order_frame()
        finally:
            km._view_order_lock, km._view_folds_served = real, saved_served
        self.assertTrue(state["injected"], "premise: a publish landed at the release after the folds were read")
        self.assertIs(state["locked"], True, "the folds were read with the store's lock held")
        self.assertEqual(fr, want, "the folds, their name and the count: all the earlier publish's")
        after = km._view_order_frame()
        self.assertEqual((after["foldsBy"], after["foldsSeq"]["n"]), ({"browser": self.C, "n": 1}, want["foldsSeq"]["n"] + 1),
                         "and the next frame is the injected publish's")

    def test_every_publish_taken_raises_the_count_by_one_and_the_count_names_this_kernel_life(self):
        # 2026-09-30: changed, unchanged, refused, named or not, each is taken, and a frame built after it says so
        seq = lambda: km._view_order_frame()["foldsSeq"]
        self.assertEqual(seq(), {"boot": km._BOOT_ID, "n": 0}, "none taken yet")
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 1}))
        self.assertEqual(seq(), {"boot": km._BOOT_ID, "n": 1}, "a change")
        [same] = self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 2}))
        self.assertEqual((same, seq()["n"]), ([], 2), "an unchanged publish: no push, and counted")
        [big] = self.dispatch(self.publish({"collapsed": ["x" * 64] * (km._VIEW_FOLDS_CAP // 64)}, {"browser": self.B, "n": 3}))
        self.assertEqual(([m[1]["type"] for m in big], seq()["n"]), (["warn"], 3), "a refused publish: warned, and counted")
        self.dispatch(self.publish(dict(FOLDS, collapsed=[])))
        self.assertEqual(seq()["n"], 4, "an unnamed publish (a page from before the name)")
        [sent] = self.dispatch(self.publish(FOLDS, {"browser": self.C, "n": 1}))
        self.assertEqual([m[2]["foldsSeq"] for m in sent], [{"boot": km._BOOT_ID, "n": 5}] * 2, "and the push carries it")

    def test_a_kernel_with_no_folds_serves_its_count_on_the_connect_push(self):
        # the page's very first frame sets what its first publish says it had received, null folds or not
        seen = []
        saved = km._send_client
        km._send_client = lambda c, key, msg, *a, **kw: seen.append((key, msg))
        try:
            km._send_view_order({"cid": "phone"})     # what the ready handler sends (the connect push)
        finally:
            km._send_client = saved
        self.assertEqual(seen, [(("vieworder",), {"type": "viewOrder", "order": [], "stored": False, "folds": None,
                                                  "foldsSeq": {"boot": km._BOOT_ID, "n": 0}, "files": None})])

    def test_a_browsers_publish_older_than_one_already_handled_is_dropped_and_another_browsers_is_not(self):
        # two panes of one browser, two sockets: the later state landed first, and the earlier one must not put itself back
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 2}))
        [stale] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 1}))
        self.assertEqual([(m[0], m[1]) for m in stale], [("desktop", ("vieworder",))],
                         "dropped: no push, and the poster is answered with the kernel's frame on its own slot")
        self.assertEqual(self.stored_folds(), FOLDS, "nothing written")
        self.assertEqual(km._view_order_frame().get("foldsBy"), {"browser": self.B, "n": 2}, "and the name served is still the later one")
        # an EQUAL number is not older: two tabs of one browser racing on its record can both mint it, and only a number
        # below the one handled is a state that browser has already replaced
        again = dict(FOLDS, collapsed=["tests"])
        [equal] = self.dispatch(self.publish(again, {"browser": self.B, "n": 2}))
        self.assertEqual([(m[0], m[2].get("foldsBy")) for m in equal], [("phone", {"browser": self.B, "n": 2}), ("desktop", {"browser": self.B, "n": 2})],
                         "an equal number is taken and pushed to every viewer")
        self.assertEqual(self.stored_folds(), again)
        self.assertEqual(km._view_folds_seen[self.B], [[2, 2]],
                         "kept at the count it took now, the one pair left, since a take removes every pair at or under its "
                         "own n (2026-10-01): both the kept and the largest")
        other = dict(FOLDS, collapsed=["web"])
        [taken] = self.dispatch(self.publish(other, {"browser": self.C, "n": 1}))
        self.assertEqual([m[2].get("foldsBy") for m in taken], [{"browser": self.C, "n": 1}] * 2, "another browser's first publish is its own count")
        self.assertEqual(self.stored_folds(), other)

    def test_a_frame_with_no_folds_half_carries_no_name_either(self):
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 1}))
        km._write_view_order([LOCAL_A])
        self.assertEqual(km._view_order_frame().get("foldsBy"), {"browser": self.B, "n": 1}, "premise: named")
        saved = km._read_state_json

        def flaky(path, *a, **kw):
            if Path(path).name == "view-folds.json":
                raise km._StateUnreadable(path, "read failed: EIO")
            return saved(path, *a, **kw)
        km._read_state_json = flaky
        self.addCleanup(lambda: setattr(km, "_read_state_json", saved))
        km._view_folds_lkg[0] = None
        self.assertEqual(km._view_order_frame(), {"type": "viewOrder", "order": [LOCAL_A], "stored": True, "files": None})

    def test_the_browsers_remembered_are_bounded_and_the_one_heard_from_longest_ago_goes_first(self):
        cap = getattr(km, "_VIEW_FOLDS_SEEN_CAP", 256)
        self.dispatch(*[self.publish(FOLDS, {"browser": "b%03d" % i, "n": 5}) for i in range(cap)])
        # the first browser heard from again: it is now the one heard from most recently, and the second the longest ago
        self.dispatch(self.publish(FOLDS, {"browser": "b000", "n": 6}), self.publish(FOLDS, {"browser": "b%03d" % cap, "n": 5}))
        [forgotten] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": "b001", "n": 1}))
        self.assertEqual(len(forgotten), 2, "the second was forgotten when the %dth browser arrived: its publish is taken as new" % (cap + 1))
        [kept] = self.dispatch(self.publish(dict(FOLDS, collapsed=["web"]), {"browser": "b000", "n": 1}))
        self.assertEqual([m[0] for m in kept], ["desktop"],
                         "the first, heard from since, is still remembered: its older publish is dropped (the poster answered)")

    # ── how far this kernel has got (2026-09-30) ─────────────────────────────────────────────────────────
    # A browser whose record came back older numbers its publishes under the one kept here, and learned the kernel's
    # number only from a frame naming it; another device's publish meant no such frame came, and its folds were dropped
    # with no trace. Each publish now carries the count its browser had received (`by.received`), and a lower number is
    # taken when that count is at or above the one the kept publish was taken at; a drop is logged and answered.
    def test_a_lower_number_is_taken_when_its_browser_had_received_a_frame_built_after_the_kept_one(self):
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 3}))
        at = km._view_folds_seen[self.B][-1][1]     # the count the kept pair, the last, was taken at
        self.dispatch(self.publish(dict(FOLDS, collapsed=["web"]), {"browser": self.C, "n": 1}))   # another device
        seq = km._view_order_frame()["foldsSeq"]       # the connect push a reload of B hears: it names the other device
        self.assertEqual((seq["boot"], seq["n"] > at), (km._BOOT_ID, True), "premise: built after B's kept publish was taken")
        again = dict(FOLDS, collapsed=["tests"])
        [sent] = self.dispatch(self.publish(again, {"browser": self.B, "n": 1, "received": seq}))
        self.assertEqual([(m[0], m[2].get("foldsBy")) for m in sent], [("phone", {"browser": self.B, "n": 1}), ("desktop", {"browser": self.B, "n": 1})],
                         "numbered under the kept 3, and taken: pushed to every viewer, named without the count")
        self.assertEqual(self.stored_folds(), again)
        self.assertEqual(km._view_folds_seen[self.B], [[3, at], [1, seq["n"] + 1]],
                         "remembered at its own number now, the last pair, with the count it took, after the largest taken from it and that one's count")
        self.assertEqual(self.logged, [], "nothing dropped")
        [on] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 2, "received": seq}))
        self.assertEqual(len(on), 2, "and the browser's next publish, made before the answer to the last, is taken on its own number")

    def test_only_a_count_of_this_kernel_life_at_or_above_the_kept_publishes_takes_a_lower_number(self):
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 3}))
        at = km._view_folds_seen[self.B][-1][1]     # the count the kept pair, the last, was taken at
        refused = [{"boot": km._BOOT_ID, "n": at - 1}, {"boot": "another life", "n": at + 5}, None, "x", {"boot": km._BOOT_ID},
                   {"boot": km._BOOT_ID, "n": True}, {"boot": km._BOOT_ID, "n": float(at)}, {"boot": km._BOOT_ID, "n": -1},
                   {"boot": km._BOOT_ID, "n": 2 ** 53 + 1}, {"boot": "", "n": at}, {"boot": "x" * 65, "n": at}, {"boot": 7, "n": at}]
        for received in refused:
            [out] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 1, "received": received}))
            self.assertEqual([m[0] for m in out], ["desktop"], "dropped, the poster answered: %r" % (received,))
            self.assertEqual(len(self.logged), 1, repr(received))
        self.assertEqual(self.stored_folds(), FOLDS, "none of them written")
        self.assertEqual(km._view_folds_seen[self.B], [[3, at]], "and the kept publish is as it was")
        [out] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 1, "received": {"boot": km._BOOT_ID, "n": at}}))
        self.assertEqual([m[0] for m in out], ["phone", "desktop"], "exactly the kept publish's count: taken")

    def test_an_overtaken_publish_is_dropped_whole_logged_and_answered_on_the_posters_own_slot(self):
        # B:3 is kept; B:1 and B:2 arrive late from a pane whose socket was slow. Both are older states: nothing written,
        # named, counted or remembered, one log line each, no warn, no push to anyone else, and the poster sent the
        # kernel's frame on its own slot (the real one, with its dedup: the second answer is the same frame, and goes nowhere)
        km._write_view_folds(FOLDS, {"browser": self.B, "n": 3})
        before = (json.dumps(self.stored_folds()), dict(km._view_folds_by[0]), km._view_folds_taken[0],
                  json.dumps(km._view_folds_seen, sort_keys=True))
        at = km._view_folds_seen[self.B][-1][1]     # the count the kept pair, the last, was taken at
        got = {"phone": [], "desktop": []}
        phone = {"cid": "phone", "app": "chat", "alive": True, "send": lambda s: got["phone"].append(json.loads(s))}
        desktop = {"cid": "desktop", "app": "chat", "alive": True, "send": lambda s: got["desktop"].append(json.loads(s))}
        saved_clients = list(km._clients)
        km._clients[:] = [phone, desktop]
        handler = object.__new__(km.Handler)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                km.Handler._dispatch_ws(handler, self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 1}), desktop)
                km.Handler._dispatch_ws(handler, self.publish(dict(FOLDS, collapsed=["web"]),
                                                              {"browser": self.B, "n": 2, "received": {"boot": km._BOOT_ID, "n": at - 1}}), desktop)
        finally:
            km._clients[:] = saved_clients
        self.assertEqual((json.dumps(self.stored_folds()), km._view_folds_by[0], km._view_folds_taken[0],
                          json.dumps(km._view_folds_seen, sort_keys=True)), before,
                         "the store, the name served, the count and the browsers remembered: all as B:3 left them")
        self.assertEqual(got["phone"], [], "no push")
        self.assertEqual(got["desktop"], [km._view_order_frame()], "the poster: the kernel's frame once (the second deduped), and no warn")
        self.assertEqual(err.getvalue().splitlines(), [
            "romp-kernel: setViewFolds dropped: browser 'bbbbbbbb' n=1 received=none, kept n=3 taken at %d" % at,
            "romp-kernel: setViewFolds dropped: browser 'bbbbbbbb' n=2 received=%r:%d, kept n=3 taken at %d" % (km._BOOT_ID, at - 1, at)])

    def test_a_drops_log_line_stays_one_line_whatever_the_page_sent(self):
        # the browser id and the received boot are the page's own strings, written with %r, so a newline in either cannot
        # forge a second kernel line
        forged = "b\nromp-kernel: forged"
        self.dispatch(self.publish(FOLDS, {"browser": forged, "n": 3}))
        self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": forged, "n": 1, "received": {"boot": "x\nromp-kernel: forged", "n": 0}}))
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=1 received=%r:0, kept n=3 taken at 1"
                                       % (forged[:8], "x\nromp-kernel: forged")])

    # ── the largest number taken (2026-09-30, the second review round) ───────────────────────────────────
    # A lower number taken on its count replaced the kept n, so an older publish numbered between it and the largest taken
    # was then taken as newer. The largest n taken from each browser is kept beside the latest, with the count it took.
    def test_a_lower_number_taken_on_its_count_lets_no_older_publish_between_it_and_the_largest_back_in(self):
        # In tab T, pane A folds api on a slow socket (publish 11) and pane B folds web over it (12), which lands first. A
        # pane in tab Y, another renderer process whose copy of the record lacks both, has 12's count from a sibling that
        # heard its frame, and folds tests as publish 10: taken on that count. Then A's 11 lands, made before its browser
        # received any frame built after 12 was taken. It is not under the kept 10, but it is under the largest, 12, and is
        # dropped (before: taken, and the kernel went back to api alone)
        self.dispatch(self.publish({"collapsed": [], "expanded": [], "pinned": []}, {"browser": self.B, "n": 9}))
        seq9 = km._view_order_frame()["foldsSeq"]       # what every pane of the browser had received when A and B folded
        api = {"collapsed": ["api"], "expanded": [], "pinned": []}
        both = {"collapsed": ["api", "web"], "expanded": [], "pinned": []}
        three = {"collapsed": ["api", "web", "tests"], "expanded": [], "pinned": []}
        [b12] = self.dispatch(self.publish(both, {"browser": self.B, "n": 12, "received": seq9}))
        seq12 = b12[0][2]["foldsSeq"]                   # the frame Y's sibling pane heard
        [y10, a11] = self.dispatch(self.publish(three, {"browser": self.B, "n": 10, "received": seq12}),
                                   self.publish(api, {"browser": self.B, "n": 11, "received": seq9}))
        self.assertEqual([m[0] for m in y10], ["phone", "desktop"], "premise: Y's 10, on 12's count, taken and pushed")
        self.assertEqual(self.stored_folds(), three, "all three folds stand (before: api alone)")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=%r:%d, largest n=12 taken at %d"
                                       % (self.B[:8], km._BOOT_ID, seq9["n"], seq12["n"])])

    # The test above pins the rule in one shape; these three run the same sequence with one more publish or with a count
    # that proves nothing (the third review round). In each, pane A's n is not under the kept n (11 over Y's 10, or equal
    # to Y's second 11), so it is judged against the largest, 12, and nothing else can drop it first.
    def tab_y_ten(self):
        """The sequence above up to tab Y's publish: B:9, B:12 (api and web) on 9's count, and Y's B:10 (api, web and
        tests) on 12's count. Returns the counts 9 and 12 were taken at, as frames serve them."""
        self.dispatch(self.publish(self.NONE, {"browser": self.B, "n": 9}))
        seq9 = km._view_order_frame()["foldsSeq"]
        [b12] = self.dispatch(self.publish(dict(self.NONE, collapsed=["api", "web"]), {"browser": self.B, "n": 12, "received": seq9}))
        seq12 = b12[0][2]["foldsSeq"]
        [y10] = self.dispatch(self.publish(dict(self.NONE, collapsed=["api", "web", "tests"]),
                                           {"browser": self.B, "n": 10, "received": seq12}))
        self.assertEqual([m[0] for m in y10], ["phone", "desktop"], "premise: Y's 10, on 12's count, taken and pushed")
        return seq9, seq12

    def test_tab_y_publishing_again_before_the_slow_publish_lands_leaves_the_largest_at_12(self):
        # Y's second publish, B:11 on 12's count, folding docs too: under 12, on 12's count, so taken; it removes Y's 10 (at
        # or under its own n) and leaves 12 on 12's count. Pane A's B:11 on 9's count is then judged against 12 and dropped.
        # A take compared against the kept n instead of each pair's (here: one at or above the kept n clearing the list)
        # takes A's publish and ends at api alone
        seq9, seq12 = self.tab_y_ten()
        four = dict(self.NONE, collapsed=["api", "web", "tests", "docs"])
        [y11, a11] = self.dispatch(self.publish(four, {"browser": self.B, "n": 11, "received": seq12}),
                                   self.publish(dict(self.NONE, collapsed=["api"]), {"browser": self.B, "n": 11, "received": seq9}))
        self.assertEqual([m[0] for m in y11], ["phone", "desktop"], "premise: Y's 11 taken and pushed")
        self.assertEqual(km._view_folds_seen[self.B], [[12, seq12["n"]], [11, seq12["n"] + 2]],
                         "the largest, the first pair, stays 12 on 12's count, and Y's 11 replaced Y's 10")
        self.assertEqual(self.stored_folds(), four, "all four folds stand")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=%r:%d, largest n=12 taken at %d"
                                       % (self.B[:8], km._BOOT_ID, seq9["n"], seq12["n"])])

    def test_under_the_largest_a_count_of_an_earlier_kernel_life_proves_nothing(self):
        # a pane's send queued across a kernel restart and replayed with the earlier life's count, numbered between the kept
        # n and the largest; a count above 12's, so only the life check drops it
        seq9, seq12 = self.tab_y_ten()
        earlier = {"boot": "an earlier life", "n": seq12["n"] + 5}
        [a11] = self.dispatch(self.publish(dict(self.NONE, collapsed=["api"]), {"browser": self.B, "n": 11, "received": earlier}))
        self.assertEqual(self.stored_folds(), dict(self.NONE, collapsed=["api", "web", "tests"]), "all three folds stand")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=%r:%d, largest n=12 taken at %d"
                                       % (self.B[:8], "an earlier life", seq12["n"] + 5, seq12["n"])])

    def test_under_the_largest_a_publish_with_no_count_proves_nothing(self):
        # such a publish from a page bundle that predates the count
        seq9, seq12 = self.tab_y_ten()
        [a11] = self.dispatch(self.publish(dict(self.NONE, collapsed=["api"]), {"browser": self.B, "n": 11}))
        self.assertEqual(self.stored_folds(), dict(self.NONE, collapsed=["api", "web", "tests"]), "all three folds stand")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=none, largest n=12 taken at %d"
                                       % (self.B[:8], seq12["n"])])

    def test_a_record_that_came_back_older_still_lands_every_publish_even_in_quick_succession(self):
        # the browser was killed before its storage was written: the kernel keeps its 5, and its record says 1. Each publish
        # it makes from then on carries the count of its connect push, built after 5 was taken: the first is taken under
        # the kept 5, and each made before the answer to the one before it is taken under the largest, 5, on that count.
        # A single kept n raised to the largest, or a largest whose count each lower take refreshed, would drop those
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 5}))
        seq = km._view_order_frame()["foldsSeq"]        # the connect push the browser hears when it comes back
        sent = self.dispatch(*[self.publish(dict(FOLDS, collapsed=["g%d" % n]), {"browser": self.B, "n": n, "received": seq})
                               for n in (2, 3, 4)])
        self.assertEqual([[m[0] for m in out] for out in sent], [["phone", "desktop"]] * 3, "each taken and pushed")
        self.assertEqual(self.stored_folds(), dict(FOLDS, collapsed=["g4"]))
        self.assertEqual(self.logged, [], "nothing dropped")

    # ── the takes numbered above a publish (2026-10-01, the third review round) ──────────────────────────────────
    # A largest taken before the browser's record came back older was taken at a count under every count the returned
    # browser sends, so it held nothing back, and the kernel judged that browser's publishes on the kept n alone. The
    # kernel now keeps each take that no later take at or above it replaced, (n, count) pairs with n descending, and
    # judges a publish against the pair with the smallest n above its own.
    NONE = {"collapsed": [], "expanded": [], "pinned": []}

    def returned_browser(self, y_n, b45=True):
        """B:45 is taken (when b45; the browser is then killed before its storage was written, so its record comes back
        at 9), then another device's C:1. The returned browser publishes B:10 on its connect push's count; in its tab T,
        pane A folds api as B:11 on 10's count on a slow socket and pane B folds web over it as B:12 on that count, which
        lands first; a pane in tab Y, whose copy of the record lacks both, has 12's count from a sibling and folds tests
        as B:<y_n>; and pane A's B:11 lands last. Returns what A's publish sent, the counts 10 and 12 were taken at, and
        the folds Y published."""
        none = self.NONE
        if b45:
            self.dispatch(self.publish(dict(none, collapsed=["docs"]), {"browser": self.B, "n": 45}))
        self.dispatch(self.publish(dict(none, collapsed=["notes"]), {"browser": self.C, "n": 1}))
        connect = km._view_order_frame()["foldsSeq"]    # the returned browser's connect push: it names C:1
        [b10] = self.dispatch(self.publish(none, {"browser": self.B, "n": 10, "received": connect}))
        seq10 = b10[0][2]["foldsSeq"]
        [b12] = self.dispatch(self.publish(dict(none, collapsed=["api", "web"]), {"browser": self.B, "n": 12, "received": seq10}))
        seq12 = b12[0][2]["foldsSeq"]
        three = dict(none, collapsed=["api", "web", "tests"])
        [y, a11] = self.dispatch(self.publish(three, {"browser": self.B, "n": y_n, "received": seq12}),
                                 self.publish(dict(none, collapsed=["api"]), {"browser": self.B, "n": 11, "received": seq10}))
        self.assertEqual([[m[0] for m in out] for out in (b10, b12, y)], [["phone", "desktop"]] * 3,
                         "premise: 10, 12 and Y's publish each taken and pushed")
        return a11, seq10["n"], seq12["n"], three

    def test_a_largest_taken_before_the_record_came_back_older_lets_no_older_publish_back_in(self):
        # B:45 was taken at a count under every count the returned browser sends, and the kept n is Y's 11, which A's 11 is
        # not under, so a kernel that judged on the kept and the largest alone took A's publish, logged nothing and went back
        # to api alone. A's publish is judged against 12, the smallest n above its own, and 12 was taken at a count above
        # the one A's publish carries
        a11, at10, at12, three = self.returned_browser(11)
        self.assertEqual(self.stored_folds(), three, "all three folds stand (before: api alone)")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=%r:%d, between n=12 taken at %d"
                                       % (self.B[:8], km._BOOT_ID, at10, at12)], "logged under 12, a pair at neither end")
        self.assertEqual(km._view_folds_seen[self.B], [[45, 1], [12, at12], [11, at12 + 1]],
                         "45 held first, then 12, then Y's 11, which replaced nothing")

    def test_the_same_when_tab_y_numbers_its_publish_10(self):
        a11, at10, at12, three = self.returned_browser(10)
        self.assertEqual(self.stored_folds(), three, "all three folds stand (before: api alone)")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=%r:%d, between n=12 taken at %d"
                                       % (self.B[:8], km._BOOT_ID, at10, at12)])
        self.assertEqual(km._view_folds_seen[self.B], [[45, 1], [12, at12], [10, at12 + 1]])

    def test_the_same_without_a_largest_from_before_the_record_came_back(self):
        # with no B:45, 12 is the largest held, and A's publish is dropped under it (as it was before the list)
        a11, at10, at12, three = self.returned_browser(11, b45=False)
        self.assertEqual(self.stored_folds(), three, "all three folds stand")
        self.assertEqual([m[0] for m in a11], ["desktop"], "A's 11 dropped, the poster answered")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=11 received=%r:%d, largest n=12 taken at %d"
                                       % (self.B[:8], km._BOOT_ID, at10, at12)])
        self.assertEqual(km._view_folds_seen[self.B], [[12, at12], [11, at12 + 1]])

    def take_on_count(self, n, folds=FOLDS):
        """B:n, carrying the count of the frame the kernel would serve now."""
        return self.dispatch(self.publish(folds, {"browser": self.B, "n": n, "received": km._view_order_frame()["foldsSeq"]}))

    def fill_to_one_past_the_cap(self):
        """B:200, then takes numbered 198, 196, ..., each under the one before and on the count it took, so each is
        appended: the cap's pairs, then one more, which forgets the first. Asserts the list at the cap and one past it."""
        cap = km._VIEW_FOLDS_PAIRS_CAP
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 200}))
        for i in range(1, cap):
            self.take_on_count(200 - 2 * i)
        pairs = km._view_folds_seen[self.B]
        self.assertEqual((len(pairs), pairs[0], pairs[-1]), (cap, [200, 1], [202 - 2 * cap, cap]), "at the cap, every pair held")
        self.take_on_count(200 - 2 * cap)
        pairs = km._view_folds_seen[self.B]
        self.assertEqual((len(pairs), pairs[0], pairs[-1]), (cap, [198, 2], [200 - 2 * cap, cap + 1]),
                         "one past it, the first pair (the largest n, the oldest take) is forgotten")

    def test_the_pairs_held_for_a_browser_are_capped_and_what_the_cap_loses(self):
        # 16 pairs per browser (kernel.py _VIEW_FOLDS_PAIRS_CAP): past it the first pair goes. Forgetting a pair removes a
        # reason to drop and adds none, so at the publish it judges the cap can only make the kernel take one it would have
        # dropped, and what the cap loses is the drop of a publish between the forgotten pair's n and the next one's, made
        # before its browser received a frame built after that oldest take: taken, the base's last write wins. Once taken,
        # that publish is held as a pair like any other, so a later publish under it, made before its browser received a
        # frame built after it, is dropped under it, even where the full list would have taken it
        self.assertEqual(km._VIEW_FOLDS_PAIRS_CAP, 16)
        self.fill_to_one_past_the_cap()
        [held] = self.dispatch(self.publish(dict(FOLDS, collapsed=["g197"]),
                                            {"browser": self.B, "n": 197, "received": {"boot": km._BOOT_ID, "n": 1}}))
        self.assertEqual([m[0] for m in held], ["desktop"], "the pairs still held still judge: 197 on count 1 is under 198, taken at 2")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=197 received=%r:1, largest n=198 taken at 2"
                                       % (self.B[:8], km._BOOT_ID)])
        [newer] = self.dispatch(self.publish(dict(FOLDS, collapsed=["g199"]),
                                             {"browser": self.B, "n": 199, "received": {"boot": km._BOOT_ID, "n": 1}}))
        self.assertEqual([m[0] for m in newer], ["phone", "desktop"],
                         "199 on 200's count, made after its browser received the frame 200 was taken on: taken, as without the cap")
        self.assertEqual(self.stored_folds(), dict(FOLDS, collapsed=["g199"]))
        _forget_fold_stamps()
        self.fill_to_one_past_the_cap()
        [lost] = self.dispatch(self.publish(dict(FOLDS, collapsed=["g199", "lost"]),
                                            {"browser": self.B, "n": 199, "received": {"boot": km._BOOT_ID, "n": 0}}))
        self.assertEqual([m[0] for m in lost], ["phone", "desktop"],
                         "199 on count 0, which 200 would have dropped: taken once 200 is forgotten, the base's last write wins")
        self.assertEqual(self.logged, [])
        self.assertEqual(km._view_folds_seen[self.B], [[199, 18]], "199's take removed every pair, each under it")
        [after] = self.dispatch(self.publish(dict(FOLDS, collapsed=["g197"]),
                                             {"browser": self.B, "n": 197, "received": {"boot": km._BOOT_ID, "n": 2}}))
        self.assertEqual([m[0] for m in after], ["desktop"],
                         "197 on 198's count, which the full list takes (judged against 198, taken at 2): dropped under 199")
        self.assertEqual(self.logged, ["romp-kernel: setViewFolds dropped: browser %r n=197 received=%r:2, kept n=199 taken at 18"
                                       % (self.B[:8], km._BOOT_ID)])
        self.assertEqual(self.stored_folds(), dict(FOLDS, collapsed=["g199", "lost"]), "the kernel keeps 199's state")

    def test_a_new_kernel_life_counts_from_nothing_and_a_count_from_the_last_life_proves_nothing(self):
        self.dispatch(self.publish(FOLDS, {"browser": self.B, "n": 3}))
        old = km._view_order_frame()["foldsSeq"]
        saved_boot = km._BOOT_ID
        self.addCleanup(setattr, km, "_BOOT_ID", saved_boot)
        km._BOOT_ID = "99999.1"                        # a restart: a new life, and the memory of the last one gone
        _forget_fold_stamps()
        self.assertFrame(km._view_order_frame(), {"type": "viewOrder", "order": [], "stored": False, "folds": FOLDS, "files": None},
                         "the folds on disk, named by nobody")
        self.assertEqual(km._view_order_frame()["foldsSeq"], {"boot": "99999.1", "n": 0}, "the new life's count, from nothing")
        [first] = self.dispatch(self.publish(dict(FOLDS, collapsed=["web"]), {"browser": self.B, "n": 5, "received": old}))
        self.assertEqual([m[2]["foldsSeq"] for m in first], [{"boot": "99999.1", "n": 1}] * 2, "a publish carrying the last life's count: taken, nothing kept here yet")
        [stale] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 2, "received": dict(old, n=old["n"] + 99)}))
        self.assertEqual([m[0] for m in stale], ["desktop"], "under the kept 5, with a count of another life: dropped")
        [taken] = self.dispatch(self.publish(dict(FOLDS, collapsed=[]), {"browser": self.B, "n": 2, "received": {"boot": "99999.1", "n": 1}}))
        self.assertEqual([m[0] for m in taken], ["phone", "desktop"], "with this life's count at the kept one's: taken")


# The file tabs the browser keeps beside them (ui/webview/file-tabs.ts FileTabsState): two files open beside sessions on
# two machines, one dragged to the head of a group. Synthetic paths in the notes-api world.
FILES = {"v": 1, "tabs": [
    {"id": "ftab-1a2b3c", "path": "~/notes-api/README.md", "sid": LOCAL_A, "anchor": LOCAL_A},
    {"id": "ftab-4d5e6f", "path": "~/notes-api/src/app.py", "sid": FAR_WEB, "anchor": LOCAL_B, "lead": True}]}


class ViewFilesStore(ViewOrderStore):
    """The viewer's FILE TABS, kept beside the arrangement and the folds (the user 2026-09-30, who wanted files opened as
    tabs beside their sessions to follow them across devices like the tab order). Its own file and its own frame half,
    so a page from before file tabs, which knows nothing of them, never drops them; opaque here as the others are: a
    round trip returns the object as written, nothing inside it read. Inherits the store's setUp and reruns its tests,
    which is the point: the arrangement and the folds are unchanged by the half riding beside them."""

    def stored_files(self):
        return json.loads((km.jd.STATE / "view-files.json").read_text())

    def test_file_tabs_round_trip_verbatim_and_ride_the_arrangements_frame(self):
        self.assertTrue(km._write_view_files(FILES))
        self.assertEqual(self.stored_files(), FILES, "written whole")
        self.assertEqual(km._view_files_served(), FILES)
        km._write_view_order([FAR_WEB, LOCAL_A])
        self.assertFrame(km._view_order_frame(),
                         {"type": "viewOrder", "order": [FAR_WEB, LOCAL_A], "stored": True, "folds": None, "files": FILES},
                         "one frame: the arrangement, the folds and the file tabs side by side")

    def test_no_file_tabs_is_null_and_an_emptied_list_is_not(self):
        # the browser's migration turns on this: with nothing here a browser holding tabs publishes them; with an
        # emptied list here (every tab closed) it must not reopen them from its own copy
        self.assertIsNone(km._view_order_frame()["files"])
        empty = {"v": 1, "tabs": []}
        km._write_view_files(empty)
        self.assertEqual(km._view_order_frame()["files"], empty)

    def test_a_file_tabs_write_reports_whether_it_changed(self):
        self.assertTrue(km._write_view_files(FILES), "a first list is a change")
        self.assertFalse(km._write_view_files(json.loads(json.dumps(FILES))), "a republish of the list served pushes nothing")
        self.assertTrue(km._write_view_files(dict(FILES, tabs=FILES["tabs"][:1])), "closing a tab is a change")

    def test_a_non_object_or_an_oversized_one_is_refused_whole(self):
        with self.assertRaises(km._StateUnwritable):
            km._write_view_files([FILES])
        with self.assertRaises(km._StateUnwritable):
            km._write_view_files({"tabs": [{"path": "x" * 64}] * (km._VIEW_FILES_CAP // 64)})
        self.assertFalse((km.jd.STATE / "view-files.json").exists(), "nothing written: a cut list is a different list")

    def test_the_socket_message_writes_and_pushes_one_frame_to_every_viewer(self):
        seen = []
        saved_send, saved_clients = km._send_client, list(km._clients)
        km._send_client = lambda c, key, msg, *a, **kw: seen.append((c["cid"], key, msg))
        km._clients[:] = [{"cid": "phone"}, {"cid": "desktop"}]
        handler = object.__new__(km.Handler)
        poster = {"cid": "desktop", "app": "chat", "alive": True, "send": lambda s: seen.append(("reply", json.loads(s)))}
        try:
            km.Handler._dispatch_ws(handler, {"type": "setViewFiles", "files": FILES}, poster)
            first = list(seen)
            del seen[:]
            km.Handler._dispatch_ws(handler, {"type": "setViewFiles", "files": FILES}, poster)
            again = list(seen)
        finally:
            km._send_client, km._clients[:] = saved_send, saved_clients
        frame = {"type": "viewOrder", "order": [], "stored": False, "folds": None, "files": FILES}
        self.assertPushed(first, ["phone", "desktop"], frame)
        self.assertEqual(again, [], "the same list again: no change, no push")
        self.assertEqual(self.stored_files(), FILES)

    def test_a_refused_file_tabs_write_is_said_to_the_viewer(self):
        seen = []
        handler = object.__new__(km.Handler)
        poster = {"cid": "desktop", "app": "chat", "alive": True, "send": lambda s: seen.append(json.loads(s))}
        saved = km._write_state_json
        km._write_state_json = lambda *a, **kw: (_ for _ in ()).throw(km._StateUnwritable(km._view_files_path(), "[Errno 28] No space left on device"))
        try:
            km.Handler._dispatch_ws(handler, {"type": "setViewFiles", "files": FILES}, poster)
        finally:
            km._write_state_json = saved
        self.assertEqual([m["type"] for m in seen], ["warn"])
        self.assertIn("file tabs", seen[0]["text"])

    def test_an_unreadable_file_tabs_store_serves_its_last_known_good_else_leaves_the_half_out(self):
        km._write_view_files(FILES)
        km._write_view_order([LOCAL_A])
        saved = km._read_state_json

        def flaky(path, *a, **kw):
            if Path(path).name == "view-files.json":
                raise km._StateUnreadable(path, "read failed: EIO")
            return saved(path, *a, **kw)
        km._read_state_json = flaky
        self.addCleanup(lambda: setattr(km, "_read_state_json", saved))
        self.assertEqual(km._view_order_frame()["files"], FILES, "a file we could not read is not a missing one")
        km._view_files_lkg[0] = None                 # a kernel that has never read it
        self.assertFrame(km._view_order_frame(), {"type": "viewOrder", "order": [LOCAL_A], "stored": True, "folds": None},
                         "no files half at all: every page keeps the tabs it shows rather than adopting a guess")

    def test_the_kernel_advertises_it(self):
        self.assertIn("viewFiles", km.KERNEL_WS_CAPS, "a page reads the cap; an older kernel lacks it and sends no half")


if __name__ == "__main__":
    unittest.main()
