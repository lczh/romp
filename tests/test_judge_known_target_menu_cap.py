#!/usr/bin/env python3
"""A planner call that already knows its card reads that card even when it is older than the session's
20 newest open nodes (2026-10-20).

Three planner branches start from a known target: a nudge reply resolves the card the nudge asked
about, a peer's delegation files under the card the courier picked, and a card reply's continuation
files under the card the user replied on. Each built its menu by filtering open_menu(store), which
keeps only the 20 newest open nodes. In a busy session (open to-do items count too) an older working
card fell off that list, and then:
  - the nudge branch reopened the card, found it missing, and skipped without placing the reply, so
    every pass appended another 'reopened (nudge)' row and saved the store, the reply was never
    judged, and the session's later nudges were held behind the unplaced reply;
  - the delegation branch took the missing card for one the user had cleared and retired the unit, so
    the handed-off work was never filed;
  - the continuation lost the card it had just reopened and sent the reply to free placement, which
    filed it somewhere else (here, a newly minted card).
The known-target menus now read the uncapped open set (_scoped_menu, and an append in the
continuation, as its first menu already does). Synthetic fixtures only: an invented private uuid, the
notes-api demo, hostname TESTHOST.
"""
import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_known_target_menu_cap", os.path.join(BIN, "romp-judge"))

SID = "7d4e2f19-3b6a-4c8e-9f15-0a2b4c6d8e10"      # private to this module (goal-store fixture rule)
SENDER = "7d4e2f19-3b6a-4c8e-9f15-0a2b4c6d8e11"
MID = "1781199000.000002_22222.TESTHOST"
G = SID + ":g1"
N_NEWER = 21                                       # one more than open_menu's cap: G falls off it


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _node(nid, text, t, parent=None):
    return {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False,
            "cleared": False, "trail": [], "t": t, "mt": t}


class KnownTargetMenuCap(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self.calls = []
        self._saved = (jd.plan_llm, jd.opener_llm, jd._group_store, jd.MESSAGES)

        def fake_plan(text, menu_text, **k):
            if k.get("nudge"):
                kind, out = "nudge", '{"ops":[{"do":"block","goal":1,"why":"approve the api schema change"}]}'
            elif k.get("followup"):
                kind = "followup"
                out = ('{"ops":[{"do":"sub","under":%d,"text":"update the web client for the new spec",'
                       '"why":"follow-up on this card"}]}' % k.get("goal_num"))
            elif k.get("goal_num") == 1 and k.get("human") is False:
                kind = "delegation"
                out = ('{"ops":[{"do":"sub","under":1,"text":"run the api contract tests",'
                       '"why":"work handed off from a peer"}]}')
            else:
                kind, out = "free", '{"ops":[{"do":"mint","text":"Free-placed card","why":"new work"}]}'
            self.calls.append(kind)
            return out
        jd.plan_llm = fake_plan
        jd.opener_llm = lambda *a, **k: ""
        jd._group_store = lambda *a, **k: None

    def tearDown(self):
        jd.plan_llm, jd.opener_llm, jd._group_store, jd.MESSAGES = self._saved
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    # ---- fixtures ----
    def _store(self, n_newer=N_NEWER, g_steps=0):
        """G (t=1000, working) plus `n_newer` newer open tops, and `g_steps` open steps under G."""
        nodes = {G: _node(G, "Ship the api schema change", 1000)}
        seq = 1
        for i in range(n_newer):
            seq += 1
            nid = SID + ":g%d" % seq
            nodes[nid] = _node(nid, "notes-api task %d" % i, 1100 + i)
        for i in range(g_steps):
            seq += 1
            nid = SID + ":g%d" % seq
            nodes[nid] = _node(nid, "schema step %d" % i, 1001 + i, parent=G)
        store = {"rompUuid": SID, "seq": seq, "placementsV": jd.PLACEMENTS_V, "nodes": nodes,
                 "placements": {}, "status": {nid: "working" for nid in nodes}, "lastNode": G}
        jd.save_goals(SID, store)
        menu_ids = [nd["id"] for nd in jd.open_menu(jd.load_goals(SID))]
        self.assertNotIn(G, menu_ids, "premise: G is older than the 20 newest open nodes")
        return store

    def _write(self, recs):
        path = os.path.join(self.td, SID + ".jsonl")
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        return path

    def _turn(self, user_text, reply, reply_dt=2, **user_extra):
        t = 3000
        user = {"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": _iso(t),
                "message": {"role": "user", "content": user_text}}
        user.update(user_extra)
        return self._write([
            user,
            {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": _iso(t + reply_dt),
             "message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": reply}]}}])

    def _reopens(self):
        return sum(1 for e in jd.load_goals(SID)["nodes"][G].get("log", []) if e.get("kind") == "reopen")

    def _steps_under_g(self):
        return [nd.get("text") for nd in jd.load_goals(SID)["nodes"].values() if nd.get("parentId") == G]

    # ---- the three branches ----
    def test_nudge_reply_on_an_older_card_is_placed_once(self):
        self._store()
        path = self._turn("Where does this stand: what's done, what's left, and is anything blocked?"
                          "<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: %s -->" % G,
                          "It is blocked on you: approve the schema change.")
        units = [u for u in jd.plan_units(jd.parsed_session(SID, [path], time.time())) if u[1] == "nudge"]
        self.assertEqual([u[5] for u in units], [G], "premise: one nudge unit, aimed at G")
        key = units[0][0]
        reopens = []
        for _ in range(3):
            jd._plan_session(SID, path, time.time())
            reopens.append(self._reopens())
        st = jd.load_goals(SID)
        self.assertEqual(self.calls, ["nudge"], "the reply reaches the planner once")
        self.assertIn(key, st["placements"], "the nudge unit is placed")
        self.assertTrue(st["nodes"][G].get("blocked"), "the reply's blocker lands on G")
        self.assertEqual(reopens, [1, 1, 1], "one reopen row, not one more per pass")

    def test_delegation_to_an_older_card_files_under_it(self):
        self._store()
        jd.MESSAGES = Path(self.td) / "messages.jsonl"
        jd.MESSAGES.write_text(json.dumps({"t": 2999, "ev": "sent", "id": MID, "from_id": SENDER,
                                           "to_id": SID}) + "\n")
        path = self._turn("Please run the api contract tests against the new schema.\n"
                          "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID,
                          "Contract tests ran; all green.", reply_dt=60, promptSource="sdk")
        units = [u for u in jd.plan_units(jd.parsed_session(SID, [path], time.time())) if u[1] == "delegation"]
        self.assertEqual(len(units), 1, "premise: one delegation unit")
        seg_id = units[0][0]
        st = jd.load_goals(SID)
        st["placements"][seg_id] = G                    # the courier's verdict: file under G
        jd.save_goals(SID, st)
        jd._plan_session(SID, path, time.time())
        self.assertEqual(self.calls, ["delegation"], "the handed-off work reaches the planner")
        self.assertIn("run the api contract tests", self._steps_under_g(), "and is filed under G")
        self.assertIsNotNone(jd.load_goals(SID)["placements"].get(seg_id + "#d"),
                             "the unit is placed, not retired as if the user had cleared G")

    def test_card_reply_on_an_older_card_files_under_it(self):
        self._store()
        path = self._turn("The api spec changed, please update the web client too.<!-- romp-goal-id: %s -->" % G,
                          "Updated the web client for the new spec.")
        jd._plan_session(SID, path, time.time())
        st = jd.load_goals(SID)
        self.assertEqual(self.calls, ["followup"], "one planner call, no fall-through to free placement")
        self.assertIn("update the web client for the new spec", self._steps_under_g(), "the reply files under G")
        self.assertFalse([nd for nd in st["nodes"].values() if nd.get("text") == "Free-placed card"],
                         "no stray card is minted for the reply")

    # ---- the scoped menu keeps its prompt bound ----
    def test_scoped_menu_keeps_the_card_and_its_newest_open_steps(self):
        store = self._store(g_steps=25)
        sub = jd._scoped_menu(jd.load_goals(SID), G)
        self.assertEqual(len(sub), 20, "the scoped menu stays within open_menu's cap")
        self.assertEqual(sub[0]["id"], G, "the card itself is #1")
        newest = sorted((nd for nd in store["nodes"].values() if nd.get("parentId") == G),
                        key=lambda nd: nd["t"])[-19:]
        self.assertEqual([nd["id"] for nd in sub[1:]], [nd["id"] for nd in newest],
                         "then its 19 newest open steps, oldest first")


if __name__ == "__main__":
    unittest.main()
