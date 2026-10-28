#!/usr/bin/env python3
"""The stall floor files only a card that is still open (2026-10-28).

The hard rule of 2026-08-13 files a card that romp's nudge gate is holding (a deferral record) under Needs
you while its session is idle. The floor never read the card's own state, and the walk mints one such
record for exactly the cards the rollup exports as confirming (done verdict in, settle pending): "the card
is already complete and merely unsettled". So a done-confirming card left Working, lost its "done,
confirming" cue, and the bells diff rang a "Needs you" push for it. A completed card whose record the
jobs thread's sweep had not popped yet read Needs you the same way.

End to end through build_feed, the record minted by the walk's own pair (_revivers_pending, then
_nudge_deferred_ok) and read back through the real _stalled_goals. A genuinely open card with a standing
record still floors, so the rule itself stands. SYNTHETIC fixtures only: the notes-api demo's web session,
a private synthetic sid."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
from romp_load import load_source
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_stall_floor_state", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1_788_300_000
T0 = NOW - 3600
SID = "f11e0c28-1111-4222-8333-000000001028"    # private synthetic sid, never the shared placeholder
G1 = SID + ":g1"
TITLE = "Rename the notes-api export flag"
CONFIRMING_WHY = "the card is already complete and merely unsettled"
SYNC_WHY = "the agent's to-do sync is due"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def top(**kw):
    d = {"id": G1, "text": TITLE, "parentId": None, "nodeComplete": True,
         "blocked": False, "cleared": False, "trail": [], "t": T0, "mt": T0 + 60,
         "log": [{"ev_t": T0 + 60, "src": "closer", "kind": "done", "at": T0 + 70}]}
    d.update(kw)
    return d


class StallFloorReadsTheCardState(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [{"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
                 "message": {"role": "user", "content": "rename the notes-api export flag"}},
                {"type": "assistant", "timestamp": iso(T0 + 60), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Done: the flag is renamed."}],
                             "stop_reason": "end_turn"}}]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"
        names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._live_map, km._GLOBAL_CLAUDE_MD, jd.gist_llm, km._NOTIFY_PREV[0])
        jd.gist_llm = lambda p: ""
        km._autonudge_cache.clear()
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        # the session is idle: no open turn, nothing it is awaiting
        km._live_map = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                     "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (td / "session-hosts").write_text("off")

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._live_map, km._GLOBAL_CLAUDE_MD, jd.gist_llm, km._NOTIFY_PREV[0]) = self.saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _write(self, store):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))

    def _confirming_store(self):
        store = {"rompUuid": SID, "seq": 1, "lastNode": G1, "nodes": {G1: top()}, "placements": {}}
        jd.rollup_status(store, session_closed=False, now=NOW)   # the judge's own export: the settle is held
        return store

    def _mint(self, store):
        """The walk's own pair at its last-resort gate."""
        turns = jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"]
        why = km._revivers_pending(SID, store, turns, G1)
        km._nudge_deferred_ok(G1, why, NOW, SID)
        return why

    def _card(self):
        km._parse(str(self.tpath), SID, NOW)
        feed = km.build_feed(NOW)
        return feed, next(a for a in feed["asks"] if a.get("itemId") == G1)

    def test_the_rollup_holds_the_done_top_as_confirming(self):
        # the premise the other cases rest on
        store = self._confirming_store()
        self.assertEqual(store["status"].get(G1), "working")
        self.assertIn(G1, store.get("confirming") or [])

    def test_a_confirming_card_stays_in_working_under_its_own_hold(self):
        store = self._confirming_store()
        self._write(store)
        _, c0 = self._card()
        self.assertEqual(c0["column"], "working")
        self.assertTrue(c0.get("doneConfirming"))
        self.assertEqual(self._mint(store), CONFIRMING_WHY)
        self.assertIn(G1, km._stalled_goals())
        _, c = self._card()
        self.assertEqual(c["column"], "working", "a done-confirming card is not on the user")
        self.assertTrue(c.get("doneConfirming"), "it keeps its done, confirming cue")
        self.assertFalse((c.get("stalled") or {}).get("blocked"))

    def test_no_needs_you_bell_for_the_confirming_card(self):
        store = self._confirming_store()
        self._write(store)
        (jd.STATE / "notify-cards.json").write_text(json.dumps({"*": True}))
        km._NOTIFY_PREV[0] = {}                      # a running life, not a first boot's silent seed
        self.assertEqual(km._feed_notifications(self._card()[0]), [])
        self._mint(store)
        self.assertEqual(km._feed_notifications(self._card()[0]), [],
                         "a done card must not ring Needs you")

    def test_a_completed_card_with_a_standing_record_stays_completed(self):
        # the record was minted while the card read working; the judges then completed and settled it, and
        # the jobs thread's sweep has not popped the record yet
        self._write({"rompUuid": SID, "seq": 1, "lastNode": G1, "nodes": {G1: top()}, "placements": {},
                     "status": {G1: "working"}, "confirming": []})
        km._nudge_deferred_ok(G1, SYNC_WHY, NOW - 50, SID)
        self._write({"rompUuid": SID, "seq": 2, "lastNode": G1,
                     "nodes": {G1: top(settledDone=True, settledAt=T0 + 80)}, "placements": {},
                     "status": {G1: "completed"}, "confirming": []})
        self.assertIn(G1, km._stalled_goals())
        _, c = self._card()
        self.assertEqual(c["column"], "completed")
        self.assertIsNone(c.get("stalled"))

    def test_an_open_card_with_a_standing_record_still_files_under_needs_you(self):
        # the 2026-08-13 rule itself: a held card that is still open, on an idle session, needs eyes
        self._write({"rompUuid": SID, "seq": 1, "lastNode": G1,
                     "nodes": {G1: top(nodeComplete=False, log=[])}, "placements": {},
                     "status": {G1: "working"}, "confirming": []})
        km._nudge_deferred_ok(G1, SYNC_WHY, NOW - 50, SID)
        _, c = self._card()
        self.assertEqual(c["column"], "needs_input")
        self.assertTrue((c.get("stalled") or {}).get("blocked"))


if __name__ == "__main__":
    unittest.main()
