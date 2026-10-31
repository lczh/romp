#!/usr/bin/env python3
"""A parentId cycle in a goal store never hangs one of the kernel's walks up the goal tree (2026-10-31).

Two judge writers can each make a legal reparent, one filing g2 under g3 and the other g3 under g2, and the second
save's rebase carries the first one's move, so the published store holds a cycle neither writer made. Five kernel
walks climbed the parent chain with no record of where they had been, and looped forever on it: the timeline
hover's card lookup (_cards_for_segments, which pinned that socket's handler thread), the jobs pass's deferral
sweep (on every tick), and the feed's permission, API-error and judge-auth floors, which spun the feed build with
its lock held, so no pane updated again. The Outline's on-path walk (_goal_tree_walk) looped the same way on a focus
in the cycle. Each now stops at the first node it revisits (judge._top_of, or a seen-set), and the three floors
share _focus_open_top, which reads a focus in or under a cycle as having no card.

Each case runs its walk on a daemon thread joined with a bound, so on the old code the case fails by name while the
thread goes on looping, instead of hanging the run.

Synthetic fixtures only: a PRIVATE synthetic sid, the notes-api demo's invented goal text, and this module's own
state root with per-session hosts pinned off."""
import contextlib
import inspect
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_parent_cycle_walks", os.path.join(BIN, "romp-kernel"))
jd = km.jd
Path(jd.STATE).mkdir(parents=True, exist_ok=True)
(Path(jd.STATE) / "session-hosts").write_text("off\n")   # this module mints its own state root: no per-session hosts

SID = "6b1f0c2e-7a3d-4c55-9e81-2d4f6a8b0c19"     # this module's PRIVATE synthetic sid
NOW = 1781300000
T0 = NOW - 3600
BOUND = 10                                       # seconds; every walk here returns in milliseconds once it terminates
HOLD = "an invented hold reason"                 # a deferral why no sweep branch retires: it stands while its goal is live


def gid(n):
    return SID + ":g%d" % n


def node(n, text, parent=None, trail=(), **kw):
    d = {"id": gid(n), "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False,
         "t": T0 + n, "mt": T0 + n, "trail": list(trail), "log": []}
    d.update(kw)
    return d


def cyclic_store(last=None):
    """g1 is a real top with g5 under it. g2 and g3 form a cycle (g2 under g3, g3 under g2), and g4 sits under it."""
    nodes = {nd["id"]: nd for nd in (
        node(1, "Add retries to the notes-api client", trail=["s1"]),
        node(2, "Wrote the retry loop", parent=gid(3), trail=["s2"]),
        node(3, "Ran the retry tests", parent=gid(2)),
        node(4, "Checked the backoff cap", parent=gid(2), trail=["s4"]),
        node(5, "Wrote the backoff helper", parent=gid(1), trail=["s5"]),
    )}
    return {"rompUuid": SID, "seq": 5, "placements": {}, "confirming": [],
            "status": {gid(1): "working"}, "nodes": nodes, "lastNode": last}


def bounded(test, what, fn, *args, **kw):
    """fn(*args) on a daemon thread joined with BOUND. A walk that loops on the cycle fails the case by name; its
    thread is left looping, but as a daemon it never holds the run open."""
    out = {}

    def run():
        try:
            out["value"] = fn(*args, **kw)
        except BaseException as e:               # re-raised on the test's own thread below
            out["error"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(BOUND)
    test.assertFalse(th.is_alive(), "%s looped on the parentId cycle" % what)
    if "error" in out:
        raise out["error"]
    return out.get("value")


class TimelineHover(unittest.TestCase):
    def test_the_card_lookup_returns_and_a_rooted_segment_still_lights_its_card(self):
        store = cyclic_store()
        with mock.patch.object(jd, "load_goals_or_fault", lambda sid: (store, None)):
            tops = bounded(self, "the hover's card lookup", km._cards_for_segments, SID, ["s1", "s2", "s4", "s5"])
            self.assertIn(gid(1), tops, "a segment under a real top still lights that top's card")
            self.assertEqual(km._cards_for_segments(SID, ["s5"]), [gid(1)], "a child's segment lights its top")


class DeferralSweep(unittest.TestCase):
    def setUp(self):
        self._d = {"nudged": {}, "deferred": {gid(n): {"at": NOW - 300, "why": HOLD, "sid": SID} for n in (1, 2, 4, 5)}}
        store = cyclic_store()
        self._patches = [mock.patch.object(km, "_auto_nudge_data", lambda: self._d),
                         mock.patch.object(km, "_write_auto_nudge", lambda d: self._d.update(d)),
                         mock.patch.object(km, "_retry_paused_on", lambda: False),
                         mock.patch.object(km, "_path_of", lambda sid: ""),
                         mock.patch.object(jd, "active_runs", lambda: []),
                         mock.patch.object(jd, "load_goals_shared", lambda sid: store)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def test_the_sweep_returns_and_a_goal_in_or_under_the_cycle_reads_as_gone(self):
        bounded(self, "the jobs pass's deferral sweep", km._deferral_sweep_tick, NOW)
        self.assertEqual(sorted(self._d["deferred"]), [gid(1), gid(5)],
                         "a goal in the cycle (g2) or under it (g4) reaches no real top, so it has no card and its record "
                         "reads as gone; the records under the live real top stand")


class FocusOpenTop(unittest.TestCase):
    def test_a_focus_in_or_under_a_cycle_floors_nothing(self):
        s = cyclic_store()
        for last in (gid(2), gid(3), gid(4)):
            self.assertIsNone(bounded(self, "the focus walk from %s" % last, km._focus_open_top,
                                      s["nodes"], last, s["status"]), last)

    def test_a_rooted_focus_floors_its_open_top_as_before(self):
        s = cyclic_store()
        s["nodes"][gid(6)] = node(6, "Listed the retry codes", parent=SID + ":g99")   # a parent no node carries
        self.assertEqual(km._focus_open_top(s["nodes"], gid(5), s["status"]), gid(1))
        self.assertEqual(km._focus_open_top(s["nodes"], gid(1), s["status"]), gid(1))
        self.assertEqual(km._focus_open_top(s["nodes"], gid(5), {gid(1): "blocked"}), gid(1))
        for st in ("completed", "cleared"):
            self.assertIsNone(km._focus_open_top(s["nodes"], gid(5), {gid(1): st}), st)
        self.assertIsNone(km._focus_open_top(s["nodes"], gid(6), s["status"]), "a chain to a missing parent has no top")
        self.assertIsNone(km._focus_open_top(s["nodes"], None, s["status"]), "no focus")
        self.assertIsNone(km._focus_open_top(s["nodes"], SID + ":g98", s["status"]), "a focus no node carries")

    def test_every_live_floor_takes_the_helper(self):
        src = inspect.getsource(km._feed_session_entry)
        self.assertEqual(src.count('_focus_open_top(nodes, store.get("lastNode"), status)'), 3,
                         "the permission, API-error and judge-auth floors")
        self.assertNotIn('.get("parentId") is not None:', src, "no bare parent walk is left in the feed entry")


class OutlineWalk(unittest.TestCase):
    def test_a_focus_in_the_cycle_marks_no_path_and_the_walk_returns(self):
        tree, roots = bounded(self, "the Outline's goal-tree walk", km._goal_tree_walk, SID, cyclic_store(gid(2)),
                              anchors=False)
        self.assertEqual(sorted(r["id"] for r in tree), [gid(1), gid(5)], "the real top's tree; the cycle hangs off none")
        self.assertFalse(any(r["onpath"] for r in tree), "the focus sits in the cycle, so no shown row is on its path")
        self.assertEqual([r["text"] for r in roots], ["Add retries to the notes-api client"])

    def test_a_rooted_focus_still_marks_its_path(self):
        tree, _ = km._goal_tree_walk(SID, cyclic_store(gid(5)), anchors=False)
        self.assertEqual(sorted(r["id"] for r in tree if r["onpath"]), [gid(1), gid(5)])


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FeedBuildOnAPermissionPrompt(unittest.TestCase):
    """The headline case end to end: the session sits on a live permission prompt while its focus is in the cycle."""

    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        recs = [{"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "sdk",
                 "message": {"role": "user", "content": "add retries to the notes-api client"}},
                {"type": "assistant", "timestamp": iso(T0 + 60), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Working on the retries."}],
                             "stop_reason": "end_turn"}}]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"
        names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        (td / "session-hosts").write_text("off\n")
        km.NAMES = names
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                      "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._bgall_cache.clear()
        km._feed_memo.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map) = self.saved
        km._feed_memo.clear()
        self.td.cleanup()

    def _write(self, nodes, last):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": len(nodes), "lastNode": last, "nodes": nodes, "placements": {},
             "status": {k: "working" for k, nd in nodes.items() if nd["parentId"] is None}, "confirming": []}))

    def _asks(self):
        def go():
            km._parse(str(self.tpath), SID, NOW)
            with contextlib.redirect_stderr(io.StringIO()):
                f = km.build_feed(NOW)
            return [(a.get("itemId"), (a.get("blocked") or {}).get("state")) for a in f["asks"] if a.get("sid") == SID]
        return bounded(self, "the feed build", go)

    def test_a_rooted_focus_floors_its_top_as_before(self):
        self._write({gid(2): node(2, "Wrote the retry loop", parent=gid(3), promptUuid="u1"),
                     gid(3): node(3, "Ship the notes-api release")}, last=gid(2))
        self.assertIn((gid(3), "permission"), self._asks())

    def test_a_focus_in_the_cycle_builds_and_floors_nothing_in_it(self):
        self._write({gid(2): node(2, "Wrote the retry loop", parent=gid(3), promptUuid="u1"),
                     gid(3): node(3, "Ship the notes-api release", parent=gid(2))}, last=gid(2))
        asks = self._asks()
        self.assertFalse([a for a in asks if a[0] in (gid(2), gid(3))], "no card is keyed on a node in the cycle: %r" % asks)


if __name__ == "__main__":
    unittest.main()
