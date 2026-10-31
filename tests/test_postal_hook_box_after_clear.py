#!/usr/bin/env python3
"""The turn-end drain and the revive wake reach the session's mailbox after a /clear (2026-10-31).

Claude Code hands its hooks the CLI's CURRENT transcript id as the payload's session_id. A /clear, or a resume onto a
later transcript, moves that id off the stable sid the bus files mail under, for good. The Stop hook ran
`romp-postal-service drain --id <payload id>` and the revive hook looked for mail-pending/<payload id>, so from the
first /clear on the drain read a box nobody delivers to and the revive hook never asked for the wake. Mail the live
push could not carry (the nopush sentinel, an oversize message from a script or another host, a kernel mid-restart)
then waited unread until the agent happened to check.

The fix maps the payload id to ROMP_SID, the session the kernel launched this CLI for, when that session's SDK
registry row names the id as its current transcript (lastSid). Pinned here end to end: the real hooks run as child
processes against the real bus Handler on loopback, with only the revive wake's push stubbed. The guards are pinned
too: a `claude -p` child inherits ROMP_SID but keeps its own id and its own empty box, and an id that is another
session's transcript never reaches that session's mail.

SYNTHETIC fixtures only: invented text, private placeholder uuids, the notes-api demo's web and api sessions."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from romp_load import load_source

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
BIN = os.path.join(REPO, "bin")
HOOKS = os.path.join(REPO, "hooks")

# Hermetic state BEFORE the load: the bus resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_ROOT = os.environ["XDG_STATE_HOME"]
_SESS = os.path.join(_ROOT, "sessions.json")

WEB = "5c1e7a90-3b2d-4f68-9e41-0a7d2c8b6f13"        # web's stable sid, the one its mail is filed under
FORK = "5c1e7a90-3b2d-4f68-9e41-0a7d2c8b6f14"       # web's transcript after its /clear (its registry's lastSid)
PEER = "8f04b2d6-71ce-4a39-b5e8-2d6c9f1a3e70"       # api, the sender
PEER_FORK = "8f04b2d6-71ce-4a39-b5e8-2d6c9f1a3e71"  # api's current transcript
CHILD = "2a9d6e13-c4f7-4b80-8d25-e7b1f03c9a46"      # a `claude -p` that web ran from its Bash tool
BODY = "the notes-api schema is merged; please rebase"
SERVE = "bus-test-token-DO-NOT-USE"                 # invented, never a real credential

Path(_SESS).write_text(json.dumps([
    {"id": WEB, "name": "web", "dir": "~/notes-api", "state": "waiting", "working": "", "lastSid": FORK},
    {"id": PEER, "name": "api", "dir": "~/notes-api", "state": "working", "working": "", "lastSid": PEER_FORK}]))
_prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
_prior_serve = os.environ.get("ROMP_SERVE_TOKEN")
os.environ["ROMP_SESSIONS_FILE"] = _SESS
os.environ["ROMP_SERVE_TOKEN"] = SERVE
try:
    ps = load_source("romp_postal_hook_box", os.path.join(BIN, "romp-postal-service"))
finally:
    # put both back the moment the bus has captured them: an import-time write that stayed would change what every
    # sibling module collected after this one reads (the hazard tests/test_postal_token.py describes)
    restore_env("ROMP_SESSIONS_FILE", _prior_seam)
    restore_env("ROMP_SERVE_TOKEN", _prior_serve)
assert ps.SERVE_TOKEN == SERVE

# the kernel's SDK registry rows, as the kernel keeps them once each CLI's init has reached it
_SDK = ps.STATE.parent / "sdk"
_SDK.mkdir(parents=True, exist_ok=True)
(_SDK / (WEB + ".json")).write_text(json.dumps({"sid": WEB, "lastSid": FORK, "alive": True}))
(_SDK / (PEER + ".json")).write_text(json.dumps({"sid": PEER, "lastSid": PEER_FORK, "alive": True}))


class _Bus(unittest.TestCase):
    """The real bus Handler on a loopback port, the sessions seam (no live kernel) and one message for web per test."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ps._LoopbackServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.home, ".claude"), exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        prior = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS
        self.addCleanup(restore_env, "ROMP_SESSIONS_FILE", prior)
        for d in (ps.MAILROOT, ps.MAILPENDING):
            shutil.rmtree(d, ignore_errors=True)
        ps.STREAKS.clear()
        self.mid = ps.deliver(WEB, "api", PEER, BODY, kind="coordinate")
        self.assertTrue((ps.MAILROOT / WEB / "new" / self.mid).is_file())
        self.assertTrue((ps.MAILPENDING / WEB).is_file())

    def _hook(self, name, payload, **env):
        """Run a real hook the way Claude Code does: the payload on stdin, the CLI's environment around it."""
        e = {k: v for k, v in os.environ.items() if not k.startswith("ROMP_SUMMARIZING")}
        for k in ("ROMP_STATE_DIR", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "ROMP_SID"):
            e.pop(k, None)
        e.update({"HOME": self.home, "XDG_STATE_HOME": _ROOT, "ROMP_SESSIONS_FILE": _SESS,
                  "ROMP_POSTAL_PORT": str(self.port), "ROMP_SERVE_TOKEN": SERVE,
                  "PATH": os.path.dirname(sys.executable) + os.pathsep + e.get("PATH", "")})
        e.update(env)
        p = subprocess.run(["bash", os.path.join(HOOKS, name)], input=json.dumps(payload, separators=(",", ":")),
                           capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return p.stdout

    def _web_unread(self):
        return (ps.MAILROOT / WEB / "new" / self.mid).is_file()


class TurnEndDrainAfterClear(_Bus):

    def test_the_drain_after_a_clear_hands_over_the_sessions_mail(self):
        out = self._hook("romp-postal-drain.sh", {"session_id": FORK, "hook_event_name": "Stop"}, ROMP_SID=WEB)
        self.assertTrue(out.strip(), "the turn-end drain of a /cleared session handed over nothing")
        d = json.loads(out)
        self.assertEqual(d["decision"], "block")
        self.assertIn(BODY, d["reason"])
        self.assertFalse(self._web_unread(), "the drain claims the message it handed over")

    def test_the_payload_id_that_is_the_sid_drains_as_before(self):
        out = self._hook("romp-postal-drain.sh", {"session_id": WEB, "hook_event_name": "Stop"}, ROMP_SID=WEB)
        self.assertIn(BODY, json.loads(out)["reason"])
        self.assertFalse(self._web_unread())

    def test_a_claude_p_child_keeps_its_own_empty_box(self):
        # the child inherits ROMP_SID from web's Bash tool; its own id is no transcript of web's
        out = self._hook("romp-postal-drain.sh", {"session_id": CHILD, "hook_event_name": "Stop"}, ROMP_SID=WEB)
        self.assertEqual(out.strip(), "", "a child's turn end must never take its parent's mail")
        self.assertTrue(self._web_unread())

    def test_an_id_that_is_another_sessions_transcript_never_reaches_its_mail(self):
        api_mid = ps.deliver(PEER, "web", WEB, "the api tests are green", kind="coordinate")
        buf = io.StringIO()
        with patch.dict(os.environ, {"ROMP_SID": WEB}), patch.object(ps, "ensure", lambda: True), \
                patch.object(ps, "BASE", "http://127.0.0.1:%d" % self.port), contextlib.redirect_stdout(buf):
            ps.cli_drain(["--id", PEER_FORK])
        self.assertEqual(buf.getvalue().strip(), "")
        self.assertTrue((ps.MAILROOT / PEER / "new" / api_mid).is_file(), "api's mail stays api's")
        self.assertTrue(self._web_unread())


class ReviveWakeAfterClear(_Bus):

    def _record_wakes(self):
        calls, woke = [], threading.Event()

        def record(sid):
            calls.append(sid)
            woke.set()
        saved = ps._wake_when_ready
        ps._wake_when_ready = record          # the /wake route looks it up when it starts the thread
        self.addCleanup(setattr, ps, "_wake_when_ready", saved)
        return calls, woke

    def test_a_resume_after_a_clear_wakes_the_sessions_mail(self):
        calls, woke = self._record_wakes()
        self._hook("romp-postal-revive.sh", {"session_id": FORK, "source": "resume"}, ROMP_SID=WEB)
        self.assertTrue(woke.wait(10), "the revive hook of a /cleared session never asked the bus for the wake")
        self.assertEqual(calls, [WEB])

    def test_a_claude_p_childs_start_never_wakes_the_sessions_mail(self):
        calls, woke = self._record_wakes()
        self._hook("romp-postal-revive.sh", {"session_id": CHILD, "source": "startup"}, ROMP_SID=WEB)
        woke.wait(3)    # a wake, if the hook asks for one, is started before the bus answers it
        self.assertNotIn(WEB, calls, "the child's id is no transcript of web's, so web's mail is not its to wake")


class HookBoxResolution(unittest.TestCase):
    """_hook_box on its own: every case that is not this session's current transcript keeps the id as given."""

    def test_cases(self):
        bad = ps.STATE.parent / "sdk" / "3b7e0c52-a1d9-4e64-9f08-c25d7e1a4b39.json"
        bad.write_text("{not json")
        self.addCleanup(bad.unlink)
        cases = [
            (None, FORK, FORK),                                     # not a romp session's CLI
            (WEB, WEB, WEB),                                        # the id already is the sid
            (WEB, FORK, WEB),                                       # the registry names it as web's transcript
            (WEB, CHILD, CHILD),                                    # a child's own id
            (WEB, PEER_FORK, PEER_FORK),                            # another session's transcript
            ("6e2f9a14-0b7c-4d35-a8e1-f94c3b2d7a50", FORK, FORK),   # no registry row for ROMP_SID
            (bad.stem, FORK, FORK),                                 # a registry row that cannot be read
        ]
        for own, given, want in cases:
            with self.subTest(own=own, given=given), patch.dict(os.environ):
                os.environ.pop("ROMP_SID", None)
                if own is not None:
                    os.environ["ROMP_SID"] = own
                self.assertEqual(ps._hook_box(given), want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
