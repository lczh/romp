#!/usr/bin/env python3
"""The nudge walk parses only what one of its legs can act on, and yields to a page that connects mid-pass (2026-10-23).

Every look of the walk parses its session's transcript before it reaches a leg. With the nudge toggle and Task tracking
both off, no leg can act (tracking off sends the look down the untracked road, whose one leg, the debt reminder, rides the
toggle), so a box with both off parsed every alive transcript on every pass for nothing: about two minutes of a cold first
pass after a reboot. Now:
  - both off: no look, no parse; the compaction suggestion (toggle-gated) is off with them;
  - the toggle alone on: only the sessions that owe a reply (an open inbound ask, or a reminder already sent) are looked
    at; the compaction suggestion, which reads no transcript, still runs for every session;
  - tracking on: every session, as before; with the toggle off that is the wake-only walk, kept for the awaiting dead-man.
And the yield to a connected page is asked before each look rather than once at the pass's top, so a page that connects
while a cold walk is running gets the thread back after the look in progress.

The REAL pass and the real look, with the look's session gates stubbed open and the parse counted. Hermetic: a temp state
root with session-hosts off, private synthetic sids, ROMP_CLAUDE_BIN at /bin/false."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from romp_load import load_source   # noqa: E402

BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)            # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_KERNEL_PORT", "0")    # never the live kernel's port
Path(os.environ["XDG_STATE_HOME"], "romp").mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off")
km = load_source("romp_kernel_nudge_switches", os.path.join(BIN, "romp-kernel"))

SID_A = "77777777-2222-4333-8444-00000000070a"     # private synthetic sids, newest first by transcript time
SID_B = "77777777-2222-4333-8444-00000000070b"
SID_C = "77777777-2222-4333-8444-00000000070c"
ASKER = "77777777-2222-4333-8444-0000000007ff"
ALL = [SID_A, SID_B, SID_C]
STOPPED = [{"id": "t1", "t": 1000, "ended": True, "atoms": [{"t": 1000, "type": "user"}]}]


class _Walk(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        env = mock.patch.dict(os.environ, {"ROMP_CLAUDE_BIN": "/bin/false"})   # no judge reaches a real CLI (set per test,
        env.start(); self.addCleanup(env.stop)                                 #  restored after: tests/test_kernel_env_floor.py)
        self.root = Path(tempfile.mkdtemp())
        (self.root / "session-hosts").write_text("off")
        self._saved_state = km.jd.STATE
        km.jd._rebind_state(self.root)
        self.addCleanup(lambda: (km.jd._rebind_state(self._saved_state), shutil.rmtree(self.root, ignore_errors=True)))
        now = time.time()
        self.rows = [self._row(sid, now - 60 * i) for i, sid in enumerate(ALL)]
        self.ledger = {"enabled": False, "nudged": {}}
        self.tracking = False
        self.asks = {}
        self.parsed, self.compact, self.gates = [], [], []
        self.on_parse = None
        km._TICK_SEEN.clear()
        km._NUDGE_WALK_CURSOR[0] = None
        for k, v in list(km._NUDGE_WALK_STATS.items()):
            km._NUDGE_WALK_STATS[k] = {} if isinstance(v, dict) else 0
        patches = mock.patch.multiple(
            km,
            _alive_sessions=lambda now, live_map: list(self.rows),
            _auto_nudge_data=lambda: dict(self.ledger),
            _task_tracking_on=lambda: self.tracking,
            _nudge_asks_by_target=lambda: dict(self.asks),
            _wait_for_graph=lambda now, ids: {},
            _cleared_ids=lambda: set(),
            _session_flag=lambda sid, flag: False,
            _compacting_now=lambda *a, **k: False,
            _api_error=lambda path: False,
            _backend_rewind_pending=lambda sid: False,
            _session_working=lambda turns: True,              # the look ends on its first gate after the parse
            _compact_suggest_tick=lambda sid, tm, now: self.compact.append(sid) or False,
            _put_walk_gate=lambda key, gate, now: self.gates.append((key, gate)),
            _pop_walk_gate=lambda key: self.gates.append((key, None)),
            _relay_tick=lambda now, ids: None,
            _debt_backstop_tick=lambda now: None,
            _dead_wait_sweep=lambda ids, nudged, now: None,
            _awaiting_wake_outcomes=lambda now, ids: False,
            _push_soon=lambda: None)
        patches.start(); self.addCleanup(patches.stop)
        parse = mock.patch.object(km.jd, "parsed_session", side_effect=self._parse)
        parse.start(); self.addCleanup(parse.stop)
        cold = mock.patch.object(km.jd, "_parse_entry", side_effect=lambda sid, session=None, turns=None: None)   # every parse cold
        cold.start(); self.addCleanup(cold.stop)
        self.addCleanup(self._drop_pages)

    def _row(self, sid, mtime):
        p = self.root / (sid + ".jsonl")
        p.write_text(json.dumps({"type": "user", "uuid": sid[:8], "timestamp": "2026-10-23T00:00:00Z",
                                 "message": {"role": "user", "content": "x"}}) + "\n")
        os.utime(p, (mtime, mtime))
        return {"sid": sid, "path": str(p), "name": "web", "mtime": mtime}

    def _parse(self, sid, paths, now, **kw):
        self.parsed.append(sid)
        if self.on_parse:
            self.on_parse(sid)
        return {"turns": STOPPED}

    def _drop_pages(self):
        with km._clients_lock:
            km._clients[:] = [c for c in km._clients if c.get("wid") != "lab"]

    def _connect_page(self):
        with km._clients_lock:
            km._clients.append({"app": "feed", "wid": "lab", "send": lambda *a, **k: None, "alive": True, "dedup": {}})

    def _pass(self):
        km._auto_nudge_pass(int(time.time()), {}, True)


class TheSwitchesDecideWhatIsParsed(_Walk):
    def test_both_switches_off_parse_no_transcript(self):
        self._pass()
        self.assertEqual(self.parsed, [], "no leg can act: no look, no parse (every alive session was parsed before)")
        self.assertEqual(km._NUDGE_WALK_STATS["notWanted"], 3)
        self.assertEqual((km._NUDGE_WALK_STATS["looks"], km._NUDGE_WALK_STATS["wakeOnly"]), (0, 0))
        self.assertEqual(self.gates, [], "the walk-gate journal keeps what the last look left: nothing written")
        self.assertEqual(self.compact, [], "the compaction suggestion rides the toggle, off")

    def test_the_toggle_alone_parses_only_the_sessions_that_owe_a_reply(self):
        self.ledger = {"enabled": True, "nudged": {},
                       "debtNudged": {"%s>%s:%d" % (ASKER, SID_C, 1000): 1001}}   # a reminder already sent to C, its outcome owed
        self.asks = {SID_B: [ASKER]}                                                # an open ask to B
        self._pass()
        self.assertEqual(sorted(self.parsed), [SID_B, SID_C], "B owes a reply, C's reminder awaits its outcome; A owes nothing")
        self.assertEqual(km._NUDGE_WALK_STATS["notWanted"], 1)
        self.assertEqual(self.compact, ALL, "the compaction suggestion reads no transcript and still runs for every session")

    def test_tracking_alone_still_walks_every_session_wake_only(self):
        self.tracking = True                                    # the toggle off: the awaiting dead-man is the walk's errand
        self._pass()
        self.assertEqual(self.parsed, ALL)
        self.assertEqual(km._NUDGE_WALK_STATS["wakeOnly"], 3)
        self.assertEqual(km._NUDGE_WALK_STATS["notWanted"], 0)

    def test_both_on_walks_every_session(self):
        self.tracking, self.ledger = True, {"enabled": True, "nudged": {}}
        self._pass()
        self.assertEqual(self.parsed, ALL)
        self.assertEqual(self.compact, ALL)

    def test_a_switch_turned_on_walks_again_on_the_next_pass(self):
        self._pass()
        self.assertEqual(self.parsed, [])
        self.tracking = True
        self._pass()
        self.assertEqual(self.parsed, ALL, "the first pass that reads the switch on looks at every session")


class AMidPassPageYields(_Walk):
    def setUp(self):
        super().setUp()
        self.tracking, self.ledger = True, {"enabled": True, "nudged": {}}

    def test_a_page_connecting_during_a_cold_look_yields_before_the_next_parse(self):
        self.on_parse = lambda sid: self._connect_page() if sid == SID_A else None   # the page connects while A's cold parse runs
        self._pass()
        self.assertEqual(self.parsed, [SID_A], "the walk yielded after the look in progress (it walked on to all three before)")
        self.assertEqual(km._NUDGE_WALK_STATS["deferredSessions"], 2)
        self.assertEqual(km._NUDGE_WALK_CURSOR[0], SID_B, "the next pass resumes at the first deferred session")
        self._drop_pages()
        self.on_parse = None
        self._pass()
        self.assertEqual(self.parsed, [SID_A, SID_B, SID_C], "with the page gone, the rotated walk reaches the rest (A's "
                         "unchanged files skip its parse)")
        self.assertEqual(km._NUDGE_WALK_STATS["skippedParses"], 1)

    def test_no_page_walks_the_whole_pass(self):
        self._pass()
        self.assertEqual(self.parsed, ALL)
        self.assertEqual(km._NUDGE_WALK_STATS["deferredSessions"], 0)


if __name__ == "__main__":
    unittest.main()
