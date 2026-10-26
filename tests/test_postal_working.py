#!/usr/bin/env python3
"""The working-note (set_working / `romp --mail working`) goes through the kernel's store (POST /working),
the one store every backend reads — so any session can publish one and postal never touches a session
directly for it (the user 2026-06-26). Synthetic only — placeholder ids, hostname-free.
"""
import contextlib
import io
import os
import socket
import tempfile
import unittest
from romp_load import load_source
from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))


class WorkingNoteThroughKernel(unittest.TestCase):
    def setUp(self):
        self._saved = (pm._publish_working, pm.my_id)
        self.published = []
        pm._publish_working = lambda sid, text: (self.published.append((sid, text)), True)[1]
        pm.my_id = lambda: "sid-self"

    def tearDown(self):
        pm._publish_working, pm.my_id = self._saved

    def test_cli_working_posts_to_the_kernel(self):
        rc = pm.cli_working(["editing", "feed.ts"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.published, [("sid-self", "editing feed.ts")])

    def test_cli_working_clear_publishes_empty(self):
        pm.cli_working([])
        self.assertEqual(self.published, [("sid-self", "")])

    def test_source_routes_set_working_through_the_kernel(self):
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        self.assertIn('_kernel_post("/working"', src, "_publish_working POSTs to the kernel working endpoint")
        self.assertIn("_publish_working(mid, text)", src, "the set_working MCP tool routes through the kernel")


SID_A = "5a1e7c00-0000-4000-8000-00000000c1a2"     # a private synthetic sid: the notes-api demo's web session


class WorkingNoteTheKernelDidNotStore(unittest.TestCase):
    """The note is the claim peers read in list_agents to settle who owns a branch, so a note the kernel did not
    store (unreachable during a restart, slow past the POST's timeout, or refusing) is said as such: before this,
    set_working answered 'Published' with no error and `romp mail working` exited 0 whatever the POST returned,
    and a peer that saw no claim started on the same branch."""

    def setUp(self):
        self._saved = (pm._publish_working, pm.my_id, pm._self_identity, pm._LOCAL_CONFIRMED[0], pm.KERNEL_BASE)
        self.published = []
        pm._publish_working = lambda sid, text: (self.published.append((sid, text)), False)[1]
        pm.my_id = lambda: SID_A
        pm._self_identity = lambda: (SID_A, "web")
        pm._LOCAL_CONFIRMED[0] = True                    # a confirmed-local session's beat is a no-op the tool skips

    def tearDown(self):
        (pm._publish_working, pm.my_id, pm._self_identity, pm._LOCAL_CONFIRMED[0], pm.KERNEL_BASE) = self._saved

    def _cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pm.cli_working(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_set_working_answers_an_error_not_published(self):
        msg, is_err = pm._mcp_call("set_working", {"text": "editing exporter on branch api-fix"})
        self.assertEqual(self.published, [(SID_A, "editing exporter on branch api-fix")])
        self.assertTrue(is_err, "set_working said %r with no error though the note was not stored" % msg)
        self.assertNotIn("Published", msg)
        self.assertIn("may not see 'editing exporter on branch api-fix'", msg)
        self.assertIn("Try again", msg)

    def test_a_clear_that_did_not_land_is_an_error_too(self):
        msg, is_err = pm._mcp_call("set_working", {"text": ""})
        self.assertEqual(self.published, [(SID_A, "")])
        self.assertTrue(is_err, msg)
        self.assertNotIn("Cleared", msg)
        self.assertIn("may still see it", msg)

    def test_cli_working_exits_nonzero_and_says_why(self):
        rc, out, err = self._cli(["editing", "exporter"])
        self.assertEqual(self.published, [(SID_A, "editing exporter")])
        self.assertEqual(rc, 1, "romp mail working exited 0 though the note was not stored")
        self.assertEqual(out, "", "no 'working:' success line")
        self.assertIn("may not see 'editing exporter'", err)
        rc, out, err = self._cli([])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("may still see it", err)

    def test_an_unreachable_kernel_reads_as_unconfirmed_end_to_end(self):
        # the real _publish_working and _kernel_post against a closed loopback port: the kernel mid-restart
        pm._publish_working = self._saved[0]
        prior = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ.pop("ROMP_SESSIONS_FILE", None)        # the seam would answer None before any dial
        self.addCleanup(restore_env, "ROMP_SESSIONS_FILE", prior)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % s.getsockname()[1]
        s.close()
        msg, is_err = pm._mcp_call("set_working", {"text": "editing exporter on branch api-fix"})
        self.assertTrue(is_err, msg)
        self.assertNotIn("Published", msg)
        rc, out, err = self._cli(["editing", "exporter"])
        self.assertEqual((rc, out), (1, ""))


if __name__ == "__main__":
    unittest.main()
