#!/usr/bin/env python3
"""A live floor keyed on a pure-delegation top still reaches a card.

A top whose every leaf is a handoff to a peer is coordination, so the feed shows no card for it
(_pure_delegation_top). The live floors pick the top of the session's focus: a permission or picker prompt
(perm_top), an API error (api_top) and the judges' refused login (jauth_top). A manager that fanned one ask out
to two workers has exactly that shape as its focus, and when it then stopped on a prompt the card loop skipped
the top before the floor could file it, while the blocked placeholder stood down because perm_top was set. The
session was stopped on the user with no card, no badge and no bell. A floor's top now comes back as a card, the
same rule T333 gives a hidden top.

Controls pin the harness: the same top with no handoffs floors to a card, and the same pure-delegation top while
the session is idle still has no card, so the suppression itself stands. SYNTHETIC fixtures only: a private
invented sid, hostname TESTHOST, the notes-api demo's web/api/tests sessions."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_pd_floor", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781200000
T0 = NOW - 3600
SID = "b7c1d2e3-1111-4222-8333-0000000000a1"     # private synthetic sids (never the shared placeholder)
PEER = "b7c1d2e3-1111-4222-8333-0000000000a2"
WK1 = "b7c1d2e3-1111-4222-8333-0000000000a3"
WK2 = "b7c1d2e3-1111-4222-8333-0000000000a4"
MID0 = "1781196000.000001_1.TESTHOST"
MID1 = "1781196100.000002_1.TESTHOST"
MID2 = "1781196100.000003_1.TESTHOST"
TOP = SID + ":g1"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "sdk", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def err_line(t, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isApiErrorMessage": True, "apiErrorStatus": 500, "error": "server_error",
            "message": {"role": "assistant", "stop_reason": "stop_sequence",   # the failed turn ENDS on the record
                        "content": [{"type": "text", "text": "API Error: 500 server_error"}]}}


MAIL = ("DELEGATE: add retries to the notes-api client\n"
        "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID0)


class FloorOnPureDelegationTop(unittest.TestCase):

    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self._transcript()
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        (td / "session-hosts").write_text("off\n")   # a state root of its own: no per-session host
        km.NAMES = names
        self.live("idle")
        jd.GOALDIR.mkdir(parents=True)
        km._bgall_cache.clear()
        km._feed_memo.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map) = self.saved
        km._feed_memo.clear()
        self.td.cleanup()

    def _transcript(self, api_error=False):
        recs = [uline(T0, MAIL, "u1"),
                aline(T0 + 60, "Handing the client half to api and the tests to tests.", "a1", "u1")]
        if api_error:
            recs.append(err_line(T0 + 120, "e1", "a1"))
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def live(self, state):
        km._live_map = lambda: {SID: {"state": state, "since": NOW - 10, "model": "", "effort": "",
                                      "context": None, "compactPct": None, "color": None}}

    @staticmethod
    def _node(nid, text, parent=None, t=T0, **kw):
        d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False,
             "trail": [], "t": t, "mt": t, "log": []}
        d.update(kw)
        return d

    def _store(self, shape="machine", trackers=True):
        h1, h2 = SID + ":g2", SID + ":g3"
        if shape == "machine":       # planner-minted from a peer's mail: the latch read the anchor as a machine record
            kw = {"promptUuid": "u1", "askAnchor": "machine"}
        else:                        # courier-planted, with no chain-proven userAsk
            kw = {"promptUuid": "u1", "origin": {"peer": PEER, "goalId": PEER + ":g4", "msgId": MID0}}
        nodes = {TOP: self._node(TOP, "Add retries to the notes-api client", **kw)}
        if trackers:
            nodes[h1] = self._node(h1, "↪ delegated to api: the client half", parent=TOP, t=T0 + 61,
                                   handoff={"peer": WK1, "msgId": MID1, "quiet": True})
            nodes[h2] = self._node(h2, "↪ delegated to tests: the retry tests", parent=TOP, t=T0 + 62,
                                   handoff={"peer": WK2, "msgId": MID2, "quiet": True})
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": len(nodes), "lastNode": TOP, "nodes": nodes, "placements": {}, "status": {}}))
        return nodes

    def _feed(self):
        km._parse(str(self.tpath), SID, NOW)          # warm the parse, so the placeholder's road is open too
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            f = km.build_feed(NOW)
        return [a for a in f["asks"] if a.get("sid") == SID]

    def _assert_floored(self, state, column="needs_input"):
        cards = self._feed()
        self.assertTrue(cards, "the session is stopped on the user (%s) and has no card at all" % state)
        self.assertEqual([c["itemId"] for c in cards], [TOP], "the floor files its card on the focus top")
        self.assertEqual((cards[0].get("blocked") or {}).get("state"), state)
        if column:
            self.assertEqual(cards[0]["column"], column)
        return cards[0]

    # --- controls -----------------------------------------------------------------------------------------
    def test_control_a_top_with_no_handoffs_floors_to_a_needs_input_card(self):
        self._store(trackers=False)
        self.live("permission")
        self._assert_floored("permission")

    def test_control_an_idle_pure_delegation_top_still_has_no_card(self):
        nodes = self._store()
        self.assertTrue(km._pure_delegation_top(nodes, TOP, sid=SID, path=str(self.tpath)))
        self.assertEqual(self._feed(), [], "nothing needs the user: the coordination top stays off the board")

    # --- the floors ---------------------------------------------------------------------------------------
    def test_a_permission_prompt_on_a_pure_delegation_top_reaches_a_card(self):
        self._store("machine")
        self.live("permission")
        card = self._assert_floored("permission")
        self.assertLessEqual({SID + ":g2", SID + ":g3"}, {r["id"] for r in card["tree"]},
                             "the card is the top itself, with its two handoff rows")

    def test_a_picker_on_a_pure_delegation_top_reaches_a_card(self):
        self._store("machine")
        self.live("picker")
        self._assert_floored("picker")

    def test_a_permission_prompt_on_a_courier_planted_top_reaches_a_card(self):
        self._store("origin")
        self.live("permission")
        self._assert_floored("permission")

    def test_an_api_error_on_a_pure_delegation_top_shows_its_badge(self):
        self._transcript(api_error=True)
        self._store("machine")
        self._assert_floored("apiError", column=None)   # a plain server error keeps its card's column; the badge is the floor

    def test_the_judges_refused_login_on_a_pure_delegation_top_reaches_a_card(self):
        self._store("machine")
        latch = {SID: {"t": NOW - 100, "mode": "key", "note": "invalid x-api-key"}}
        with mock.patch.object(jd, "_auth_down_map", lambda: latch):
            self._assert_floored("judgeAuth")


if __name__ == "__main__":
    unittest.main()
