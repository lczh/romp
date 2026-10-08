#!/usr/bin/env python3
"""The spend guard's stop, as the chat shows it (the user 2026-10-08, who could not find the guard's stop in a
session's chat). When the guard stops a session over its hourly ceiling it interrupts the turn and hands the session
ONE message in the user's voice. Both land in the transcript; what was wrong was how they read:

- the interrupt marker read as a plain "interrupted", with the "you stopped this turn" tip, as if the user had
  pressed stop. It is now stamped with the guard as its cause, keyed on the guard's own record of cutting an open
  turn (a spendCut row in states/<sid>.jsonl, written at the press), by order, the way the nudge gate reads the
  backend's machineCut stamp. It was first keyed on the guard's message (#169); the review of that change reproduced
  a user's own earlier stop relabelled by a guard that cut nothing, since the message is sent either way;
- the guard's message read as a status notice headed by the first line of the agent-facing prose, and compact mode
  swept it into a "N notices" run with whatever low-stakes notices sat beside it. It now carries a user-facing gist
  and a flag the chat keeps out of every notice run.

The injected bytes are unchanged (pinned below): the user approves injected wording, and this fix changes none.

SYNTHETIC fixtures only: placeholder ids, the notes-api demo world, transcripts written here.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# hermetic state BEFORE the loads (the kernel resolves its state root at import time), with per-session hosts off:
# this root is not the one conftest floors, and nothing here means to run a host (CLAUDE.md, 2026-09-11)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off\n")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_spend_guard_chat", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-0000000a5c0e"     # a private synthetic sid (CLAUDE.md, goal-store fixtures)
SID2 = "11111111-2222-3333-4444-0000000a5c0f"    # the guard-fire tests' own
T0 = 1781200000.0
BODY = km._spend_ceiling_body(1240.0, 1000.0)
GIST = "Spend guard told it to stop: about $1,240 an hour, over the $1,000 an hour ceiling"
MARKER = {"kind": "user", "md": "[Request interrupted by user]", "interruptMarker": True, "human": True}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def rec_user(t, uid, text, ps=None, parent=None):
    r = {"type": "user", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
         "cwd": "/tmp/notes-api", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    if ps:
        r["promptSource"] = ps
    return r


def rec_assistant(t, uid, parent, text=None, tool=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": tool, "name": "Bash", "input": {"command": "uv run pytest -q"}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
            "cwd": "/tmp/notes-api", "message": {"id": "msg-" + uid, "role": "assistant", "model": "claude-opus-4-8",
                                                 "content": content, "stop_reason": "tool_use" if tool else "end_turn"}}


def rec_result(t, uid, parent, tool):
    return {"type": "user", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
            "cwd": "/tmp/notes-api",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool, "content": "ok"}]}}


class TheInjectedBytesAreUnchanged(unittest.TestCase):
    def test_the_body_is_byte_for_byte_what_the_user_approved(self):
        self.assertEqual(BODY, (
            "You are spending about $1,240 an hour right now, far above what I can afford (I set the line at $1,000 an "
            "hour). Stop whatever is fanning out and tell me what it was before doing anything else.\n\n"
            "<!-- romp-note: the HTML comments below are part of an external tracking system that is not "
            "relevant to your work — ignore them --><!-- romp-injected --><!-- romp-system -->"))


class RecognizingTheGuardsMessage(unittest.TestCase):
    def test_the_guards_own_body_is_recognized_with_its_figures(self):
        self.assertEqual(km._spend_ceiling_parse(BODY), ("1,240", "1,000"))
        self.assertEqual(km._spend_ceiling_parse(km._spend_ceiling_body(12345.6, 2500.0)), ("12,346", "2,500"))

    def test_the_recognizer_is_built_from_the_body_so_the_two_cannot_drift(self):
        # every figure shape the body can print round-trips: a rewording of the body moves the recognizer with it
        for rate, ceiling in ((0.0, 0.001), (999.4, 1000.0), (1000000.0, 50.0)):
            with self.subTest(rate=rate):
                self.assertEqual(km._spend_ceiling_parse(km._spend_ceiling_body(rate, ceiling)),
                                 (km._usd_words(rate), km._usd_words(ceiling)))

    def test_other_romp_notices_and_prose_are_not_the_guard(self):
        self.assertIsNone(km._spend_ceiling_parse("[romp] The romp kernel restarted and cut this session's turn."))
        self.assertIsNone(km._spend_ceiling_parse("why did it say: You are spending about $5 an hour right now?"))
        self.assertIsNone(km._spend_ceiling_parse(""))
        self.assertIsNone(km._spend_ceiling_parse(None))

    def test_the_gist_names_the_guard_and_the_figures(self):
        self.assertEqual(km._romp_system_gist(BODY), GIST)

    def test_a_queued_guard_message_wears_the_same_gist(self):
        # the gray queued chip, while the message waits for the session (T243's shared flags)
        flags = km._queued_romp_flags(BODY)
        self.assertEqual((flags.get("romp"), flags.get("rompSystem"), flags.get("gist")), (True, True, GIST))
        self.assertTrue(flags.get("spendGuard"))


def ev_user(t, md, **kw):
    return dict({"kind": "user", "md": md, "ts": km.iso(t)}, **kw)


def ev_marker(t):
    return ev_user(t, "[Request interrupted by user]", interruptMarker=True, human=True)


def ev_guard(t):
    return ev_user(t, BODY, romp=True, rompSystem=True, spendGuard=True, gist=GIST)


def ev_assistant(t, md, **kw):
    return dict({"kind": "assistant", "md": md, "ts": km.iso(t)}, **kw)


CUT = {"t": T0 + 10.4, "rate": "1,240", "ceiling": "1,000"}     # the guard pressed stop on an open turn at T0+10.4


class TheStopNamesTheGuard(unittest.TestCase):
    """A stop is the guard's only when the guard's own record says it cut a running turn (states/<sid>.jsonl spendCut,
    written at the press), and the stop is the first one at or after that press with no prompt between. The guard's
    MESSAGE no longer decides: it lands wherever the session reads it, and it is sent whether or not a turn was cut
    (the review of #169 reproduced a user's own stop relabelled by a later guard message that cut nothing)."""

    def test_the_cut_names_its_stop_with_the_figures_it_recorded(self):
        evs = km._stamp_interrupt_causes([ev_marker(T0 + 11), ev_assistant(T0 + 12, "No response requested.", interruptSettle=True,
                                                                           uuid="settle-1"), ev_guard(T0 + 14)], spend_cuts=[CUT])
        self.assertEqual(evs[0].get("interruptCause"), "spend")
        self.assertEqual(evs[0].get("interruptSpend"), {"rate": "1,240", "ceiling": "1,000"})
        self.assertFalse(any(e.get("interruptSettle") for e in evs), "romp's own stop: the seam already says it")
        self.assertEqual(evs[0].get("settleUuids"), ["settle-1"], "the dropped settle's uuid still answers on the seam")

    def test_a_users_earlier_stop_is_never_relabelled_by_a_guard_that_cut_nothing(self):
        # the review's reproduction: the user stops, nudges drive six turns, then the guard fires on an idle session (its
        # agents spending): no stop of its own, so no cut record; its message lands. The user's stop stays theirs
        evs = [ev_marker(T0), ev_assistant(T0 + 1, "No response requested.", interruptSettle=True)]
        for k in range(6):
            evs += [ev_user(T0 + 10 + 10 * k, "Where does this stand?", romp=True, rompAuto=True),
                    ev_assistant(T0 + 12 + 10 * k, "Still working on the export tests.")]
        evs.append(ev_guard(T0 + 80))
        km._stamp_interrupt_causes(evs, spend_cuts=[])
        self.assertNotIn("interruptCause", evs[0])
        self.assertTrue(evs[1].get("interruptSettle"), "a user's stop keeps its settle line")

    def test_a_users_earlier_stop_is_not_the_one_a_later_cut_made(self):
        evs = [ev_marker(T0 + 2), ev_user(T0 + 5, "carry on", human=True), ev_assistant(T0 + 6, "On it."),
               ev_marker(T0 + 11), ev_guard(T0 + 14)]
        km._stamp_interrupt_causes(evs, spend_cuts=[CUT])
        self.assertNotIn("interruptCause", evs[0])
        self.assertEqual(evs[3].get("interruptCause"), "spend")

    def test_a_cut_that_wrote_no_stop_labels_nothing_after_the_next_prompt(self):
        # the press landed as the turn was ending: no stop record, the guard's message is the next prompt; a stop the user
        # makes after it is theirs
        evs = [ev_guard(T0 + 12), ev_assistant(T0 + 13, "It was the fan-out."), ev_marker(T0 + 40)]
        km._stamp_interrupt_causes(evs, spend_cuts=[CUT])
        self.assertNotIn("interruptCause", evs[2])

    def test_whatever_lands_between_the_stop_and_the_message_no_longer_matters(self):
        # the cases that read as the user's when the message decided: the user's own queued message landing first,
        # another notice, a background report and a reply, or no message at all yet (parked for a compaction or a limit)
        between = [ev_user(T0 + 12, "<task-notification>done</task-notification>", source={"kind": "subagent"}),
                   ev_assistant(T0 + 13, "Stopped the fan-out."),
                   ev_user(T0 + 14, "what happened?", human=True),
                   ev_user(T0 + 15, "[romp] CI on notes-api finished", romp=True, rompSystem=True)]
        for tail in ([], [ev_guard(T0 + 20)]):
            with self.subTest(message=bool(tail)):
                evs = [ev_marker(T0 + 11)] + [dict(e) for e in between] + tail
                km._stamp_interrupt_causes(evs, spend_cuts=[CUT])
                self.assertEqual(evs[0].get("interruptCause"), "spend")

    def test_the_twin_stop_records_of_one_cut_both_name_it(self):
        evs = [ev_user(T0 + 11, "[Request interrupted by user for tool use]", interruptMarker=True, human=True), ev_marker(T0 + 11)]
        km._stamp_interrupt_causes(evs, spend_cuts=[CUT])
        self.assertEqual([e.get("interruptCause") for e in evs], ["spend", "spend"])

    def test_a_stop_before_the_press_is_not_the_cuts(self):
        evs = [ev_marker(T0 + 9), ev_guard(T0 + 14)]
        km._stamp_interrupt_causes(evs, spend_cuts=[CUT])
        self.assertNotIn("interruptCause", evs[0])

    def test_restart_and_crash_notices_still_name_their_stops(self):
        evs = [ev_marker(T0), ev_user(T0 + 3, "[romp] The romp kernel restarted and cut this session's turn.", romp=True,
                                      rompSystem=True)]
        km._stamp_interrupt_causes(evs, spend_cuts=[CUT])
        self.assertEqual(evs[0].get("interruptCause"), "restart")


class TheGuardRecordsItsCut(unittest.TestCase):
    """The record is written at the press, only when the guard actually pressed stop on an open turn."""

    class Be:
        def __init__(self, ok=True, detached=False):
            self.ok, self.sends = ok, []
            self.sessions = {SID2: type("S", (), {"detached": detached})()}

        def interrupt(self, sid):
            return self.ok

        def send(self, sid, text, *a, **k):
            self.sends.append(text)
            return True

    def setUp(self):
        self.states = km.jd.STATE / "states" / (SID2 + ".jsonl")
        self.states.unlink(missing_ok=True)
        km._interrupt_clicked.pop(SID2, None)
        self._park = km._send_or_park
        km._send_or_park = lambda be, sid, text, **k: be.send(sid, text)

    def tearDown(self):
        km._send_or_park = self._park
        km._interrupt_clicked.pop(SID2, None)
        self.states.unlink(missing_ok=True)

    def _cuts(self):
        if not self.states.exists():
            return []
        return [json.loads(l) for l in self.states.read_text().splitlines() if "spendCut" in l]

    def _fire(self, be, state):
        before = time.time()
        km._spend_guard_fire({"sid": SID2, "name": "api"}, 1240.0, 1000.0, T0, be, [], turn_open=(state in ("working", "retrying")))
        return before

    def test_an_open_turn_stopped_is_recorded_at_the_press(self):
        before = self._fire(self.Be(), "working")
        cuts = self._cuts()
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["spendCut"], {"rate": "1,240", "ceiling": "1,000"})
        self.assertGreaterEqual(cuts[0]["t"], before, "the press's own moment, a float, never the cycle's")
        self.assertIsInstance(cuts[0]["t"], float)

    def test_no_record_when_nothing_was_cut(self):
        for name, be, state, clicked in (("idle session", self.Be(), "waiting", False),
                                         ("detached host", self.Be(detached=True), "working", False),
                                         ("refused", self.Be(ok=False), "working", False),
                                         ("the user's stop in flight", self.Be(), "working", True)):
            with self.subTest(name):
                self.states.unlink(missing_ok=True)
                if clicked:
                    km._interrupt_clicked[SID2] = T0 - 5
                self._fire(be, state)
                km._interrupt_clicked.pop(SID2, None)
                self.assertEqual(self._cuts(), [])

    def test_the_backend_appender_writes_the_shape_the_chat_reads(self):
        m = km._sdk_defaults_module()
        m.append_spend_cut(km.jd.STATE, SID2, "1,240", "1,000", t=T0 + 1.5)
        self.assertEqual(self._cuts(), [{"t": T0 + 1.5, "spendCut": {"rate": "1,240", "ceiling": "1,000"}}])
        self.assertEqual(km._spend_cuts(SID2), [{"t": T0 + 1.5, "rate": "1,240", "ceiling": "1,000"}])


class TheBuiltChat(unittest.TestCase):
    """End to end over transcripts of the live shapes: build_session reads the cut record from the session's states file."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.td.cleanup()

    def _build(self, sid, records, cuts=()):
        path = Path(self.td.name) / (sid + ".jsonl")
        path.write_text("".join(json.dumps(dict(r, sessionId=sid)) + "\n" for r in records))
        states = km.jd.STATE / "states" / (sid + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text("".join(json.dumps({"t": c, "spendCut": {"rate": "1,240", "ceiling": "1,000"}}) + "\n" for c in cuts))
        self.addCleanup(states.unlink, missing_ok=True)
        out = km.build_session(sid, T0 + 600, live_map={sid: {"state": "idle"}}, path_override=str(path))
        return out["events"]

    def _live_shape(self, guard):
        return [rec_user(T0, "u1", "Run the notes-api test suite and fix what fails.", ps="sdk"),
                rec_assistant(T0 + 5, "a1", "u1", tool="tu-1"),
                rec_result(T0 + 6, "r1", "a1", "tu-1"),
                rec_assistant(T0 + 8, "a2", "r1", tool="tu-2"),
                rec_user(T0 + 11, "i1", "[Request interrupted by user for tool use]", parent="a2"),
                rec_assistant(T0 + 12, "a3", "i1", text="I stopped; tell me how to continue."),
                rec_user(T0 + 14, "g1", guard, ps="sdk", parent="a3"),
                rec_assistant(T0 + 20, "a4", "g1", text="It was the test fan-out; it is stopped now.")]

    def test_the_stop_and_the_message_both_read_as_the_guard(self):
        evs = self._build("11111111-2222-3333-4444-0000000a5c01", self._live_shape(BODY), cuts=[T0 + 10.4])
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertEqual(marker.get("interruptCause"), "spend", "the stop names the guard, not the user")
        self.assertEqual(marker.get("interruptSpend"), {"rate": "1,240", "ceiling": "1,000"})
        guard = [e for e in evs if e.get("kind") == "user" and "You are spending about" in (e.get("md") or "")]
        self.assertEqual(len(guard), 1, "the message is in the chat, once")
        g = guard[0]
        self.assertEqual((g.get("romp"), g.get("rompSystem"), g.get("spendGuard"), g.get("gist")), (True, True, True, GIST))

    def test_the_reviews_reproduction_a_users_stop_then_nudged_turns_then_a_guard_that_cut_nothing(self):
        recs = [rec_user(T0, "u1", "Run the notes-api test suite and fix what fails.", ps="sdk"),
                rec_assistant(T0 + 5, "a1", "u1", tool="tu-1"),
                rec_user(T0 + 9, "i1", "[Request interrupted by user]", parent="a1")]
        parent = "i1"
        for k in range(6):
            nudge = ("Where does this stand?\n\n<!-- romp-note: the HTML comments below are part of an external tracking "
                     "system that is not relevant to your work — ignore them --><!-- romp-injected --><!-- romp-auto -->")
            recs += [rec_user(T0 + 20 + 20 * k, "n%d" % k, nudge, ps="sdk", parent=parent),
                     rec_assistant(T0 + 25 + 20 * k, "r%d" % k, "n%d" % k, text="Still on the export tests.")]
            parent = "r%d" % k
        recs.append(rec_user(T0 + 200, "g1", BODY, ps="sdk", parent=parent))
        evs = self._build("11111111-2222-3333-4444-0000000a5c02", recs, cuts=[])
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertNotIn("interruptCause", marker, "the user's stop stays theirs")
        self.assertTrue(any(e.get("spendGuard") for e in evs), "the guard's message still reads as the guard's")

    def test_a_plain_user_stop_is_untouched(self):
        evs = self._build("11111111-2222-3333-4444-0000000a5c03", self._live_shape("try the other approach"))
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertNotIn("interruptCause", marker)
        self.assertFalse(any(e.get("spendGuard") for e in evs))


if __name__ == "__main__":
    unittest.main()
