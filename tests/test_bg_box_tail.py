#!/usr/bin/env python3
"""The chat's background box follows the backend's live task set on every frame, not only on full ones (2026-10-08).

The box's rows come from two fields of the session payload: the status's awaitingItems (what the session waits on, and
the header's words and count) and `bgTasks` (every tracked background task, which lends the rows their Stop handle and
lists the ones the wait does not name). The status rides every chatTail delta; `bgTasks` rode only the FULL session
frame, so a caught-up page kept the set it was last handed whole. A session that launched four background agents and
saw three of them finish showed, half an hour on, "Awaiting agent · 1 background agent still working · 4 agents" over
four rows with the running dot and Stop: the header (the status) was current, the rows (the held bgTasks) were the set
from before the three ended. The backend's live set and its registry mirror were right all along; the delta never
carried the change. Now a delta carries `bgTasks` whenever the set the page holds differs from the build's (its
membership, statuses, labels: everything but a running command's output tail, which grows every cycle and stays a
full-frame field as before), and never otherwise. Synthetic data only.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_bgbox", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-00000000b0c5"


def _agent(n, status="running"):
    return {"id": "toolu_0000000000000000000000a%d" % n, "status": status, "summary": "review part %d of the notes-api" % n,
            "command": "Review part %d." % n, "output": "", "agentId": "a00000000000000%d" % n}


def _bg(*tasks):
    return {"count": len(tasks), "tasks": list(tasks)}


def _m(events, bg, why="1 background agent still working"):
    return {"type": "session", "id": SID, "name": "api", "events": events, "color": None, "ledger": None,
            "status": {"state": "awaitingBg", "awaitingWhy": why, "awaitingCount": 1, "awaitingKind": "agents"},
            "bgTasks": bg}


def _client(proto=None):
    sent = []
    c = {"send": sent.append, "sent": {}}
    if proto:
        c["proto"] = proto
    return c, sent


EVS = [{"uuid": "u1", "kind": "user"}, {"uuid": "a1", "kind": "assistant"}]


class TailCarriesTheLiveSet(unittest.TestCase):
    def _three_finish(self, proto):
        c, sent = _client(proto)
        four = _bg(_agent(1), _agent(2), _agent(3), _agent(4))
        km._send_chat(c, _m(EVS, four, why="4 background agents still working"), None, 0, False)
        full = json.loads(sent[-1])
        self.assertEqual(full["type"], "session")
        self.assertEqual(len(full["bgTasks"]["tasks"]), 4)
        # three agents end: the backend's live set (and so the build's bgTasks) holds one; no event changed
        km._send_chat(c, _m(EVS, _bg(_agent(4))), None, km._chat_diff(EVS, EVS), False)
        t = json.loads(sent[-1])
        self.assertEqual(t["type"], "chatTail", "a delta, not a full: the page is caught up")
        self.assertIn("bgTasks", t, "the delta carries the set the rows read, or the page keeps four running agents")
        self.assertEqual([x["id"] for x in t["bgTasks"]["tasks"]], [_agent(4)["id"]])
        return c, sent

    def test_the_index_wire(self):
        self._three_finish(None)

    def test_the_uuid_wire(self):
        self._three_finish(2)

    def test_an_unchanged_set_rides_no_delta(self):
        for proto in (None, 2):
            c, sent = self._three_finish(proto)
            one = _bg(_agent(4))
            evs2 = EVS + [{"uuid": "a2", "kind": "assistant"}]
            km._send_chat(c, _m(evs2, one), None, km._chat_diff(EVS, evs2), False)
            t = json.loads(sent[-1])
            self.assertEqual(t["type"], "chatTail")
            self.assertNotIn("bgTasks", t, "a set the page already holds is not re-sent (proto %r)" % proto)

    def test_a_status_change_alone_sends_the_tail_that_carries_it(self):
        # the empty-suffix tail is skipped when the page already holds the view; a changed set is part of that view
        c, sent = _client(2)
        km._send_chat(c, _m(EVS, _bg(_agent(1), _agent(2))), None, 0, False)
        n = len(sent)
        km._send_chat(c, _m(EVS, _bg(_agent(1), _agent(2, "completed"))), None, km._chat_diff(EVS, EVS), False)
        self.assertEqual(len(sent), n + 1, "the change goes even with nothing else moving")
        self.assertEqual([x["status"] for x in json.loads(sent[-1])["bgTasks"]["tasks"]], ["running", "completed"])

    def test_a_growing_output_tail_alone_sends_nothing(self):
        # a running command's output tail moves every cycle; it stays a full-frame field, as it always was
        c, sent = _client(2)
        cmd = dict(_agent(1), agentId=None, output="line 1")
        km._send_chat(c, _m(EVS, _bg(cmd)), None, 0, False)
        n = len(sent)
        km._send_chat(c, _m(EVS, _bg(dict(cmd, output="line 1\nline 2"))), None, km._chat_diff(EVS, EVS), False)
        self.assertEqual(len(sent), n, "no frame for an output tail alone")

    def test_a_full_frame_resets_what_the_page_holds(self):
        c, sent = _client(2)
        km._send_chat(c, _m(EVS, _bg(_agent(1))), None, 0, False)
        c["echat"].pop(SID, None)                                  # the base is forgotten (a needFull): the next is a full
        km._send_chat(c, _m(EVS, _bg(_agent(1), _agent(2))), None, 0, False)
        self.assertEqual(json.loads(sent[-1])["type"], "session")
        km._send_chat(c, _m(EVS, _bg(_agent(1), _agent(2))), None, km._chat_diff(EVS, EVS), False)
        self.assertEqual(json.loads(sent[-1])["type"], "session", "nothing new after the full: no tail at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
