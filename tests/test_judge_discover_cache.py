#!/usr/bin/env python3
"""discover() is CACHED behind a directory-mtime fingerprint (the user 2026-06-25, who found startup still slow).

It was re-walking ~80 project dirs and reading every fork's head 2-4× per push (feed + timeline + chat each
call it) — ~60-250ms, the single biggest slice of a build. Its OUTPUT only changes when a session is
added/renamed or a FORK appears (a .jsonl added to a project dir bumps that dir's mtime); a plain transcript
APPEND adds no directory entry, so the fingerprint — and the cached list — stay put. Same (mtime)
change-detection idiom as the parse cache. Synthetic only: placeholder UUIDs, hermetic temp STATE.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
FORK = "66666666-7777-8888-9999-aaaaaaaaaaaa"
NAME = "TESTHOST-session"


class DiscoverCacheTest(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"           # _proj_dir maps a launch dir under here
        jd._discover_cache["fp"] = None                      # module-global → reset between tests
        jd._discover_cache["result"] = None
        self.cdir = str(Path(self._td) / "work")            # synthetic launch dir
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, self.cdir))
        self._write_transcript(SID)                          # the anchor transcript (recent → within WINDOW)

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_transcript(self, sid, title=None):
        p = self.proj / (sid + ".jsonl")
        head = json.dumps({"type": "custom-title", "customTitle": title or NAME}) + "\n"
        p.write_text(head + json.dumps({"type": "user", "uuid": "u1"}) + "\n")

    def test_repeated_discover_returns_the_cached_list_object(self):
        now = int(time.time())
        a = jd.discover(now)
        b = jd.discover(now)
        self.assertIs(a, b, "an unchanged namespace returns the SAME cached list (no re-walk)")
        self.assertIn(SID, [t[0] for t in a])

    def test_a_transcript_append_does_NOT_bust_the_cache(self):
        now = int(time.time())
        a = jd.discover(now)
        with (self.proj / (SID + ".jsonl")).open("a") as f:   # append a turn — no new directory entry
            f.write(json.dumps({"type": "assistant", "uuid": "a1"}) + "\n")
        self.assertIs(jd.discover(now), a, "an append changes no DIR mtime → the cached list still serves")

    def test_a_new_fork_busts_the_cache_and_appears(self):
        now = int(time.time())
        a = jd.discover(now)
        self.assertNotIn(FORK, [t[0] for t in a])
        self._write_transcript(FORK, title=NAME)              # same customTitle fork → new .jsonl bumps the proj dir mtime
        b = jd.discover(now)
        self.assertIsNot(b, a, "a new fork (new dir entry) busts the fingerprint")
        self.assertIn(FORK, [t[0] for t in b], "...and the fork now shows up")

    def test_an_idle_session_reenters_the_moment_its_transcript_is_touched(self):
        """WINDOW's contract: an untouched store re-enters the moment its transcript is touched again. An
        append adds no directory entry, so the fingerprint holds still; a cache filled while the session sat
        outside the window kept serving the list without it, and every judge stage skipped the session until
        some unrelated session added a file."""
        anchor = self.proj / (SID + ".jsonl")
        idle = time.time() - 3 * 86400                       # three days idle: outside the 48h window
        os.utime(anchor, (idle, idle))
        jd._discover_cache.clear()                            # a cold fill, as after a kernel restart
        a = jd.discover(time.time())
        self.assertNotIn(SID, [t[0] for t in a])
        fp = jd._discover_fingerprint()
        with anchor.open("a") as f:                           # the user types into it again
            f.write(json.dumps({"type": "user", "uuid": "u2"}) + "\n")
        self.assertEqual(jd._discover_fingerprint(), fp, "an append moves no fingerprint input")
        b = jd.discover(time.time())
        self.assertIn(SID, [t[0] for t in b], "the touched session is back in the list without a namespace change")

    def test_a_session_leaving_the_window_drops_out_of_the_cached_list(self):
        now = time.time()
        a = jd.discover(now)
        self.assertIn(SID, [t[0] for t in a])
        self.assertIs(jd.discover(now + 60), a, "time passing with nothing crossing the cutoff still serves the cache")
        later = jd.discover(now + jd.WINDOW + 3600)           # nothing touched it for longer than the window
        self.assertNotIn(SID, [t[0] for t in later])

    def test_a_fork_lane_leaving_the_window_drops_out_while_its_session_stays(self):
        self._write_transcript(FORK, title=NAME)
        fork = self.proj / (FORK + ".jsonl")
        now = time.time()
        aged = now - jd.WINDOW + 600                          # ten minutes from leaving the window
        os.utime(fork, (aged, aged))
        self.assertIn(FORK, [t[0] for t in jd.discover(now)])
        later = jd.discover(now + 3600)
        self.assertIn(SID, [t[0] for t in later])
        self.assertNotIn(FORK, [t[0] for t in later], "the lane aged out exactly as a cold walk would drop it")

    def test_a_new_session_name_busts_the_cache(self):
        now = int(time.time())
        a = jd.discover(now)
        other = "99999999-0000-1111-2222-333333333333"
        cdir2 = str(Path(self._td) / "work2")
        proj2 = jd._proj_dir(cdir2); proj2.mkdir(parents=True, exist_ok=True)
        (proj2 / (other + ".jsonl")).write_text(json.dumps({"type": "user", "uuid": "u1"}) + "\n")
        (jd.NAMES / other).write_text("%s\t%s" % ("TESTHOST-two", cdir2))   # new names/ entry
        b = jd.discover(now)
        self.assertIsNot(b, a)
        self.assertIn(other, [t[0] for t in b])


if __name__ == "__main__":
    unittest.main()
