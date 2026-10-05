"""A page's held turns are set aside (`rebased`) only when the conversation ABANDONED them, never when the history went
missing from where the kernel looks (2026-10-05).

What happened, synthetically: a session was born as a fork of another live session. Moving the fork to a new folder
renamed the PARENT's transcript into the fork's folder too (the fork's episode log names the parent's file, and the move
read it as one of the fork's own; tests/test_session_move.py pins that cause). The parent's CLI went on appending to a
fresh file at its own path, whose first record links to a parent in the file that moved away. The kernel's parse of that
fresh file holds none of the earlier turns, so _key_in_transcript read the page's held tail key as gone and the full frame
carried `rebased`: the page set 3,000 turns aside under "this session was continued".

Pinned here:
  - a parse whose first record links to history no file it reads holds keeps the held key (no rebased);
  - a genuine rewind (the held turn is on an abandoned branch of a conversation that begins in the file) still rebases,
    and so does a key the whole conversation never had;
  - the first-record read is memoized per inode, so a file renamed away and re-created at the same path is read again;
  - a fork's earlier-episode fold (build_episode) finds the parent's transcript in the PARENT's folder after the fork
    moved, since the parent's file now stays where its owner reads it.

Synthetic data only: placeholder uuids, invented prompts, the notes-api demo domain.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load: it resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_heldkey_missing", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-4333-8444-000000001005"      # the session whose history moved away (private to this module)
FORK = "11111111-2222-4333-8444-000000002005"     # a session born as a fork of SID
NOW = int(time.time())


def U(i):
    return "11111111-2222-3333-4444-%012d" % (100000 + i)


def A(i):
    return "11111111-2222-3333-4444-%012d" % (200000 + i)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _pair(i, parent, cwd, fsid=SID):
    t0 = NOW - 7200 + i * 30
    return [
        {"type": "user", "uuid": U(i), "parentUuid": parent, "sessionId": fsid, "cwd": cwd, "timestamp": iso(t0),
         "promptSource": "typed", "message": {"role": "user", "content": "notes-api step %d?" % i}},
        {"type": "assistant", "uuid": A(i), "parentUuid": U(i), "sessionId": fsid, "cwd": cwd, "timestamp": iso(t0 + 10),
         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "step %d done." % i}]}},
    ]


def _chain(lo, hi, cwd, parent=None, fsid=SID):
    rows = []
    for i in range(lo, hi):
        rows += _pair(i, parent, cwd, fsid)
        parent = A(i)
    return rows


def _write(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


class Base(unittest.TestCase):
    def setUp(self):
        self._saved_state, self._saved_projects = jd.STATE, jd.PROJECTS
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd.PROJECTS = Path(self.td) / "projects"
        Path(self.td, "session-hosts").write_text("off\n")   # a state root this test mints: no real session host
        self._reset_caches()
        self.cwd = os.path.realpath(os.path.join(self.td, "notes-api"))
        self.other = os.path.realpath(os.path.join(self.td, "notes-web"))
        for d in (self.cwd, self.other):
            os.makedirs(d, exist_ok=True)
        self.proj = jd._proj_dir(self.cwd)
        self.proj_other = jd._proj_dir(self.other)
        for d in ("names", "sdk", "episodes", "states"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        self._session(SID, "api", self.cwd)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        jd.PROJECTS = self._saved_projects
        self._reset_caches()
        shutil.rmtree(self.td, ignore_errors=True)

    @staticmethod
    def _reset_caches():
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        km._FIRST_LINK_MEMO.clear()
        km._HELD_ABSENT_SAID.clear()

    def _session(self, sid, name, cwd, **reg):
        (jd.STATE / "names" / sid).write_text("%s\t%s\t\t\n" % (name, cwd))
        (jd.STATE / "sdk" / (sid + ".json")).write_text(json.dumps(
            dict({"sid": sid, "name": name, "cwd": cwd, "lastSid": sid, "alive": True}, **reg)))


class HeldKeyMembership(Base):
    def test_history_moved_away_keeps_the_held_key(self):
        # the earlier 40 turns sit in the session's file under ANOTHER folder (renamed there by a fork's move); the file at
        # the session's own path is the CLI's continuation, its first record linking into the moved file
        _write(self.proj_other / (SID + ".jsonl"), _chain(0, 40, self.cwd))
        _write(self.proj / (SID + ".jsonl"), _chain(40, 45, self.cwd, parent=A(39)))
        self.assertTrue(km._key_in_transcript(SID, U(3), NOW),
                        "the parse begins at a record whose parent no file it reads holds: missing history, never abandoned")
        whole, why = km._parse_starts_whole(SID, str(self.proj / (SID + ".jsonl")))
        self.assertFalse(whole)
        self.assertIn(A(39), why, "the reason names the link the parse could not follow")

    def test_a_key_the_parse_holds_is_kept(self):
        _write(self.proj / (SID + ".jsonl"), _chain(0, 10, self.cwd))
        self.assertTrue(km._key_in_transcript(SID, U(2), NOW))
        self.assertTrue(km._key_in_transcript(SID, A(9), NOW))

    def test_a_key_the_whole_conversation_never_had_rebases(self):
        _write(self.proj / (SID + ".jsonl"), _chain(0, 10, self.cwd))
        self.assertFalse(km._key_in_transcript(SID, U(75), NOW),
                         "the conversation begins in this file and has no such turn: genuinely not part of it")

    def test_a_genuine_rewind_still_rebases(self):
        # turns 0-5, then a rewind: turn 50 re-parents onto turn 2's reply, so turns 3-5 sit on an abandoned branch
        rows = _chain(0, 6, self.cwd) + _chain(50, 53, self.cwd, parent=A(2))
        _write(self.proj / (SID + ".jsonl"), rows)
        self.assertFalse(km._key_in_transcript(SID, U(4), NOW),
                         "a held turn on the branch a rewind abandoned is set aside, as before")
        self.assertTrue(km._key_in_transcript(SID, U(1), NOW), "the kept part of the conversation stays")

    def test_the_first_record_is_read_again_when_the_file_is_replaced(self):
        p = self.proj / (SID + ".jsonl")
        _write(p, _chain(0, 10, self.cwd))
        self.assertEqual(km._first_record_link(str(p)), (U(0), None))
        self.proj_other.mkdir(parents=True, exist_ok=True)
        os.rename(p, self.proj_other / (SID + ".jsonl"))          # renamed away (the inode leaves the path)...
        _write(p, _chain(10, 12, self.cwd, parent=A(9)))          # ...and the CLI re-creates the file at the old path
        self.assertEqual(km._first_record_link(str(p)), (U(10), A(9)), "a new inode at the path is a new first record")

    def test_an_absent_key_is_said_once_with_its_outcome(self):
        import io
        import contextlib
        _write(self.proj_other / (SID + ".jsonl"), _chain(0, 20, self.cwd))
        _write(self.proj / (SID + ".jsonl"), _chain(20, 22, self.cwd, parent=A(19)))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._key_in_transcript(SID, U(3), NOW)
            km._key_in_transcript(SID, U(3), NOW)
        lines = [ln for ln in err.getvalue().splitlines() if ln.startswith("chat2: held key")]
        self.assertEqual(len(lines), 1, "said once per session, key and outcome: %r" % lines)
        self.assertIn(U(3), lines[0])
        self.assertIn("kept, no rebased", lines[0])
        self.assertIn("road", lines[0], "the lookup path is named: the parse's turns and the road that served them")


class ForkEpisodeAfterMove(Base):
    def test_a_moved_forks_earlier_episode_is_read_from_the_parents_folder(self):
        # the parent (SID) stays in notes-api; the fork moved to notes-web, and its first episode row names the parent's
        # transcript, which the move now leaves in the parent's folder
        _write(self.proj / (SID + ".jsonl"), _chain(0, 6, self.cwd))
        self._session(FORK, "web", self.other, forkedFrom={"sid": SID, "name": "api", "cut": A(3), "t": NOW - 3000})
        _write(self.proj_other / (FORK + ".jsonl"), _chain(60, 63, self.other, fsid=FORK))
        _write(jd.STATE / "episodes" / (FORK + ".jsonl"), [
            {"head": U(0), "fsid": SID, "t": NOW - 7200},
            {"head": A(3), "fsid": FORK, "t": NOW - 3000},
        ])
        self._reset_caches()
        ep = km.build_episode(FORK, NOW)
        self.assertFalse(ep.get("error"), "the parent's transcript is found where its owner keeps it: %r" % ep.get("error"))
        self.assertIn("step 1 done.", json.dumps(ep["events"]), "the fork's pre-branch history is shown")
        self.assertEqual(km._episode_file(FORK, SID), self.proj / (SID + ".jsonl"))

    def test_an_episode_file_beside_the_session_is_still_preferred(self):
        _write(self.proj / (SID + ".jsonl"), _chain(0, 6, self.cwd))
        self._session(FORK, "web", self.cwd, forkedFrom={"sid": SID, "name": "api", "cut": A(3), "t": NOW - 3000})
        _write(self.proj / (FORK + ".jsonl"), _chain(60, 63, self.cwd, fsid=FORK))
        self._reset_caches()
        self.assertEqual(km._episode_file(FORK, SID), self.proj / (SID + ".jsonl"))

    def test_a_file_in_neither_folder_still_fails_loudly(self):
        self._session(FORK, "web", self.other, forkedFrom={"sid": SID, "name": "api", "cut": A(3), "t": NOW - 3000})
        _write(self.proj_other / (FORK + ".jsonl"), _chain(60, 63, self.other, fsid=FORK))
        _write(jd.STATE / "episodes" / (FORK + ".jsonl"), [
            {"head": U(0), "fsid": "11111111-2222-4333-8444-00000000dead", "t": NOW - 7200},
            {"head": A(3), "fsid": FORK, "t": NOW - 3000},
        ])
        self._reset_caches()
        ep = km.build_episode(FORK, NOW)
        self.assertIn("missing", ep.get("error", ""))
        self.assertEqual(ep["events"], [])


if __name__ == "__main__":
    unittest.main()
