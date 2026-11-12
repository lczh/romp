#!/usr/bin/env python3
"""A session's status carries `turnOpen`, whether its turn is OPEN as the event model reads it (progress notes, round six,
2026-11-06).

The chat keys its fork spots and its "worked …" line on the turn's END, never on the status chip: a permission prompt, a
question, a retry and a restart's reattach move the chip while the turn goes on, and a spot or a footer keyed on the chip came
mid-turn and went again when the turn resumed, moving the rows under it. turnOpen is the reading the running-call stamp takes
(_session_working over the CLI's own turns): pinned here over a synthetic session, true with the row reading working, a
permission prompt, a picker, a retry and a dormant row's "waiting" while the turn is open (each with the chip it moves to), and
false once the turn ended (a result's "waiting" in the state log, an interrupt's "idle", the reply's end_turn). Synthetic
transcripts only (placeholder uuids, invented notes-api prose)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from test_chat_pages import NOW, SID, Harness, jd, km   # noqa: E402


def _iso(t):
    import datetime
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _recs(end=False):
    """A prompt, a call with its result, a second call still waiting for its result; with `end`, its result and the reply."""
    def rec(k, t, typ, content, parent, stop=None):
        r = {"type": typ, "uuid": "11111111-2222-3333-4444-%012d" % k, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
             "message": {"role": typ, "content": content}}
        if typ == "assistant":
            r["message"].update(model="claude-opus-5-5", stop_reason=stop)
        return r
    tool = lambda tid, cmd: [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd, "description": "ran " + cmd}}]
    res = lambda tid: [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]
    out = [rec(1, NOW - 100, "user", "run the notes-api tests", None),
           rec(2, NOW - 95, "assistant", tool("toolu_A", "pytest -q"), None, "tool_use"),
           rec(3, NOW - 90, "user", res("toolu_A"), None),
           rec(4, NOW - 85, "assistant", tool("toolu_B", "pytest -q tests/test_api.py"), None, "tool_use")]
    if end:
        out += [rec(5, NOW - 75, "user", res("toolu_B"), None),
                rec(6, NOW - 70, "assistant", [{"type": "text", "text": "The notes-api tests pass."}], None, "end_turn")]
    for prev, r in zip(out, out[1:]):
        r["parentUuid"] = prev["uuid"]
    return out


class StatusTurnOpen(Harness):
    def setUp(self):
        super().setUp()
        (jd.STATE / "session-hosts").write_text("off\n")       # a state root of its own: no session host (T348)

    def _states(self, *recs):
        (jd.STATE / "states" / (SID + ".jsonl")).write_text("".join(json.dumps({"t": t, "state": st}) + "\n" for t, st in recs))

    def _status(self, state):
        self.fresh()
        row = {"state": state, "since": "", "model": "Opus 5.5", "effort": "high", "context": None}
        return km.build_session(SID, NOW, {SID: row}, floor=0)["status"]

    def test_turn_open_holds_through_a_prompt_a_question_a_retry_and_a_reattach(self):
        self.write(_recs())
        self._states((NOW - 100, "working"), (NOW - 84, "permission"))
        chips = {}
        for state in ("working", "permission", "picker", "retrying", "waiting"):
            st = self._status(state)
            chips[state] = st["state"]
            self.assertIs(st["turnOpen"], True, "%s: the turn is still open" % state)
        self.assertEqual((chips["working"], chips["permission"], chips["picker"], chips["retrying"]),
                         ("working", "needsInput", "needsInput", "retrying"), "the chip moved while the turn did not")

    def test_turn_open_turns_false_when_the_turn_ends(self):
        for end in ("waiting", "idle"):        # a result, an interrupt: the state log's stop records
            self.write(_recs())
            self._states((NOW - 100, "working"), (NOW - 80, end))
            self.assertIs(self._status("waiting")["turnOpen"], False, "%s: the turn ended" % end)
        self.write(_recs(end=True))            # the reply's end_turn
        self._states((NOW - 100, "working"))
        self.assertIs(self._status("waiting")["turnOpen"], False, "end_turn: the turn ended")


def _rec(k, t, typ, content, stop=None, **extra):
    r = {"type": typ, "uuid": "11111111-2222-3333-5555-%012d" % k, "parentUuid": None, "timestamp": _iso(t), "sessionId": SID}
    if typ == "system":
        r.update(extra)
        return r
    r["message"] = {"role": typ, "content": content}
    if typ == "assistant":
        r["message"].update(model="claude-opus-5-5", stop_reason=stop)
    r.update(extra)
    return r


def _chain(recs):
    for prev, r in zip(recs, recs[1:]):
        if r.get("type") != "system":
            r["parentUuid"] = prev["uuid"]
    return recs


_TOOL = lambda tid, cmd: [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd, "description": "ran " + cmd}}]
_RES = lambda tid: [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]
# a Stop hook's block, as Claude Code records its feedback: isMeta, the hook's words; romp's postal hook adds its marker
_HOOK = "Stop hook feedback:\nThe notes-api tests have not run yet; run them before you stop."
_POSTAL = ("Stop hook feedback:\nNew message(s) from your romp peers:\n\n— from api:\nThe search route is merged; rebase on it.\n"
           "<!-- romp-msg-id: 1700000000.11111_22222.TESTHOST -->\n<!-- romp-msg-kind: coordinate -->")


class TurnOpenThroughItsWindows(Harness):
    """The turn stays open through the windows where the transcript alone reads it closed (round seven): the reply's end_turn
    before a Stop hook's block and its continuation, with and without romp's postal marker on the hook's feedback, and a
    compaction mid-turn until the continuation's first record. The states log still has the turn open (no result frame came)
    and the running session's row says so; the turn ends at the result frame's "waiting". A turn whose CLI kept dying and
    that the backend gave up (turnCut) is over, and a later state record reopens. Each step is one build over the transcript
    as it stood then."""

    def setUp(self):
        super().setUp()
        (jd.STATE / "session-hosts").write_text("off\n")
        self.recs = []
        self.states = [(NOW - 300, "working")]

    def _add(self, *recs):
        self.recs += list(recs)
        self.write(_chain(list(self.recs)))

    def _open(self, row_state="working"):
        (jd.STATE / "states" / (SID + ".jsonl")).write_text(
            "".join(json.dumps({"t": t, "state": st} if isinstance(st, str) else dict(st, t=t)) + "\n" for t, st in self.states))
        self.fresh()
        row = {"state": row_state, "since": "", "model": "Opus 5.5", "effort": "high", "context": None}
        return km.build_session(SID, NOW, {SID: row}, floor=0)["status"]["turnOpen"]

    def test_a_stop_hooks_block_and_its_continuation_keep_the_turn_open_until_the_result(self):
        self._add(_rec(1, NOW - 300, "user", "run the notes-api tests"),
                  _rec(2, NOW - 290, "assistant", _TOOL("toolu_A", "pytest -q"), "tool_use"),
                  _rec(3, NOW - 285, "user", _RES("toolu_A")),
                  _rec(4, NOW - 280, "assistant", [{"type": "text", "text": "The notes-api tests pass."}], "end_turn"))
        self.assertIs(self._open(), True, "the reply's end_turn, the Stop hook not yet run: no result frame yet")
        self.assertIs(self._open("waiting"), False, "…and with no CLI running the turn, the log's open turn is a lost result")
        self._add(_rec(5, NOW - 279, "user", _HOOK, isMeta=True))
        self.assertIs(self._open(), True, "a block without romp's marker (the parse skips its feedback)")
        self._add(_rec(6, NOW - 270, "assistant", _TOOL("toolu_B", "pytest -q tests/test_api.py"), "tool_use"),
                  _rec(7, NOW - 265, "user", _RES("toolu_B")),
                  _rec(8, NOW - 260, "assistant", [{"type": "text", "text": "The API tests pass too."}], "end_turn"))
        self.assertIs(self._open(), True, "the continuation's end_turn, before its own Stop hook ran")
        self._add(_rec(9, NOW - 259, "user", _POSTAL, isMeta=True))
        self.assertIs(self._open(), True, "a postal block's feedback")
        self._add(_rec(10, NOW - 250, "assistant", [{"type": "text", "text": "Rebased on the merged search route."}], "end_turn"))
        self.assertIs(self._open(), True, "its continuation's end_turn")
        self.states.append((NOW - 249, "waiting"))
        self.assertIs(self._open("waiting"), False, "the result frame ends the turn")

    def test_a_compaction_mid_turn_keeps_the_turn_open_until_the_result(self):
        self._add(_rec(1, NOW - 300, "user", "migrate the notes table"),
                  _rec(2, NOW - 290, "assistant", _TOOL("toolu_A", "npm run migrate"), "tool_use"),
                  _rec(3, NOW - 285, "user", _RES("toolu_A")),
                  _rec(4, NOW - 280, "system", None, subtype="compact_boundary", isMeta=False, logicalParentUuid=None,
                       compactMetadata={"trigger": "auto", "preTokens": 190000, "postTokens": 9000}),
                  _rec(5, NOW - 279, "user", "summary of the conversation so far", isCompactSummary=True, isVisibleInTranscriptOnly=True))
        self.recs[3]["logicalParentUuid"] = self.recs[2]["uuid"]
        self.write(_chain(list(self.recs)))
        self.states.append((NOW - 281, "compacting"))
        self.assertIs(self._open("compacting"), True, "the boundary, the continuation not yet written")
        self.states.append((NOW - 278, "working"))
        self.assertIs(self._open(), True, "…the compaction done, the continuation still to come")
        self._add(_rec(6, NOW - 270, "assistant", _TOOL("toolu_B", "npm test"), "tool_use"))
        self.assertIs(self._open(), True, "the continuation's first record")
        self.states.append((NOW - 260, "waiting"))
        self.assertIs(self._open("waiting"), False, "the result frame ends the turn")

    def test_a_turn_the_backend_gave_up_is_over_and_a_new_drive_reopens(self):
        self._add(_rec(1, NOW - 300, "user", "run the e2e suite"),
                  _rec(2, NOW - 290, "assistant", _TOOL("toolu_A", "npm run e2e"), "tool_use"))
        self.assertIs(self._open("waiting"), True, "a dormant row while the CLI reattaches: the turn is still open")
        self.states.append((NOW - 200, {"turnCut": "crash-loop"}))
        self.assertIs(self._open("waiting"), False, "the CLI kept dying and the heal declined: the turn is over")
        self.states.append((NOW - 100, "working"))
        self.assertIs(self._open(), True, "the next drive reopens")


class GivenUpTurn(unittest.TestCase):
    """The backend files the event a given-up turn ends on (sdk_backend.append_turn_cut): its CLI died mid-turn, the heal
    resumed it once, the CLI died again before a turn completed, and the heal declined (the crash loop). The first heal, which
    resumes, files none; the record carries its own key, so the boot reconcile still finds the turn cut and resumes it."""

    def test_the_declined_heal_files_the_turn_cut_and_the_resuming_one_does_not(self):
        import shutil, types
        sb = sys.modules.get("romp_sdk_backend") or load_source(   # the kernel's own copy when it loaded one
            "romp_sdk_backend", os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "kernel", "sdk_backend.py"))
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (Path(d) / "session-hosts").write_text("off\n")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        be._ensure = lambda sid: None                     # no CLI is started here
        sid = "11111111-2222-3333-4444-0000000000c7"      # a private synthetic sid
        sb.write_reg(Path(d), sid, {"sid": sid, "name": "web", "alive": True, "lastSid": sid})
        sess = types.SimpleNamespace(sid=sid, name="web")
        sb.append_state(Path(d), sid, "working")          # the turn the CLI dies in
        states = Path(d) / "states" / (sid + ".jsonl")
        rows = lambda: [json.loads(l) for l in states.read_text().splitlines()] if states.exists() else []
        be._heal_cut_session(sess)
        self.assertFalse([r for r in rows() if "turnCut" in r], "the first death is resumed: the turn goes on")
        be._heal_cut_session(sess)
        self.assertEqual([r.get("turnCut") for r in rows() if "turnCut" in r], ["crash-loop"], "the declined heal ends the turn")
        self.assertIsNotNone(sb.cut_turn_start(Path(d), sid), "no state record of its own: the next boot still finds the turn cut")


if __name__ == "__main__":
    unittest.main()
