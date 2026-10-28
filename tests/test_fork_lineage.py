#!/usr/bin/env python3
"""Branch lineage (the user 2026-08-13: branching a session must SHOW in the UI). A fork's reg
carries a durable forkedFrom record (the one-shot forkOf launch flags are spent at init); the
child's chat gets a branch divider right after the branch-point record, the parent's chat gets a
chip on the turn the fork departed from, and both deep-link across. All fixtures SYNTHETIC."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_fl", os.path.join(BIN, "romp_sdk_backend.py"))

km._limit_hold = lambda sid: None

PARENT = "11111111-2222-3333-4444-555555555555"
CHILD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
THREAD = "99999999-8888-7777-6666-555555555555"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class ForkStampsLineage(unittest.TestCase):
    """sdk_backend.fork(): the durable forkedFrom record."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.claude = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude   # transcript_path resolves through this
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.cwd = self.td
        self.be.spawn("parent", self.cwd, sid=PARENT)

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        shutil.rmtree(self.td, ignore_errors=True)
        shutil.rmtree(self.claude, ignore_errors=True)

    def _reg(self, sid):
        return json.loads((Path(self.td) / "sdk" / (sid + ".json")).read_text())

    def test_an_explicit_cut_is_the_recorded_branch_point(self):
        self.be.fork("child", PARENT, "a1", sid=CHILD)
        ff = self._reg(CHILD).get("forkedFrom")
        self.assertEqual(ff["sid"], PARENT)
        self.assertEqual(ff["cut"], "a1")
        self.assertEqual(ff["name"], "parent")
        self.assertGreater(ff["t"], 0)

    def test_a_tip_fork_stamps_the_parents_current_leaf(self):
        t = int(time.time()) - 60
        p = Path(sb.transcript_path(self.cwd, PARENT))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r) for r in [
            uline(t, "the ask", "u1"), aline(t + 5, "the answer", "a9", parent="u1")]) + "\n")
        self.be.fork("child", PARENT, "", sid=CHILD)
        self.assertEqual(self._reg(CHILD)["forkedFrom"]["cut"], "a9",
                         "no cut given = the histories diverge at the parent's leaf")

    def test_fork_model_and_effort_overrides_land_in_the_reg_only(self):
        self.be.fork("child", PARENT, "a1", sid=CHILD, model="haiku", effort="low")
        reg = self._reg(CHILD)
        self.assertEqual(reg.get("model"), "haiku")
        self.assertEqual(reg.get("effort"), "low")
        self.assertNotIn("model", self._reg(PARENT), "the parent's reg is untouched")

    def test_fork_model_default_clears_the_inherited_pick(self):
        (Path(self.td) / "sdk" / (PARENT + ".json")).write_text(json.dumps(
            {**self._reg(PARENT), "model": "opus"}))
        self.be.fork("child", PARENT, "a1", sid=CHILD, model="default", effort="not-a-level")
        reg = self._reg(CHILD)
        self.assertNotIn("model", reg, "'default' = back to the account default, not a literal")
        self.assertNotEqual(reg.get("effort"), "not-a-level", "an unknown effort is ignored")

    def test_fork_children_indexes_by_parent_and_skips_threads(self):
        self.be.fork("child", PARENT, "a1", sid=CHILD)
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        kids = self.be.fork_children().get(PARENT) or []
        self.assertEqual([k["sid"] for k in kids], [CHILD],
                         "a comment thread's anchor is its highlight, never a branch chip")
        self.assertEqual(kids[0]["cut"], "a1")

    def test_a_promoted_thread_joins_the_children(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertEqual(self.be.fork_children().get(PARENT), None)
        self.be.promote_thread(THREAD, "sidework", "#123456", "#ffffff")
        kids = self.be.fork_children().get(PARENT) or []
        self.assertEqual([k["sid"] for k in kids], [THREAD])
        self.assertEqual(kids[0]["name"], "sidework")


class ThreadPicksStayOnTheThread(unittest.TestCase):
    """A comment thread's model, effort or mode pick (its popover's statusline routes setModel, setEffort and
    setMode to the THREAD's sid) is the thread's alone, the rule fork() keeps for its own overrides. The setters
    wrote the shared new-session seed (sdk-defaults.json) for a thread exactly as for a tab, so after a Haiku, low
    effort, plan mode pick in one side conversation, the next new session opened on Haiku, at low effort and in
    plan mode. A tab's pick still seeds (the control), and so does a thread's once it is broken out."""

    NEWSID = "99999999-8888-7777-6666-444444444444"
    SEEDED = ("model", "effort", "mode")

    def setUp(self):
        self.td = tempfile.mkdtemp()
        (Path(self.td) / "session-hosts").write_text("off")   # a minted state root starts no session host
        self.claude = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be.spawn("web", self.td, sid=PARENT)
        self.be.fork("thread-x", PARENT, "", sid=THREAD, thread_of=PARENT)
        self.assertEqual(self.be.thread_of(THREAD), PARENT)
        self.assertEqual(self._seed(), {"model": None, "effort": None, "mode": None}, "the seed starts empty")

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        shutil.rmtree(self.td, ignore_errors=True)
        shutil.rmtree(self.claude, ignore_errors=True)

    def _seed(self):
        d = sb.read_sdk_defaults(Path(self.td))
        return {k: d.get(k) for k in self.SEEDED}

    def _new_session(self):
        self.be.spawn("api", self.td, sid=self.NEWSID)
        return sb.read_reg(Path(self.td), self.NEWSID)

    def _assert_new_sessions_untouched(self):
        self.assertEqual(self._seed(), {"model": None, "effort": None, "mode": None},
                         "a thread's pick became the seed for every new session")
        reg = self._new_session()
        self.assertNotIn("model", reg)
        self.assertEqual(reg.get("effort"), sb.DEFAULT_EFFORT)
        self.assertEqual(reg.get("mode"), "acceptEdits")

    def test_a_dormant_threads_picks_stay_on_the_thread(self):
        self.assertTrue(self.be.set_model(THREAD, "haiku"))
        self.assertTrue(self.be.set_effort(THREAD, "low"))
        self.assertTrue(self.be.set_mode(THREAD, "plan"))
        reg = sb.read_reg(Path(self.td), THREAD)
        self.assertEqual((reg.get("model"), reg.get("effort"), reg.get("mode")), ("haiku", "low", "plan"),
                         "the picks apply to the thread itself")
        self._assert_new_sessions_untouched()

    def test_a_live_threads_model_pick_leaves_no_pending_seed_write(self):
        # the live road writes the seed optimistically under a token until the CLI rules (_seed_write_pending);
        # a thread's pick takes no token, so its verdict has nothing in the shared store to unwind
        self.be.sessions[THREAD] = sb.SdkSession(self.be, sb.read_reg(Path(self.td), THREAD))
        self.assertTrue(self.be.set_model(THREAD, "haiku"))
        self.assertEqual(sb.read_reg(Path(self.td), THREAD).get("model"), "haiku")
        self.assertEqual(self.be._seed_writes, {})
        self._assert_new_sessions_untouched()

    def test_a_tabs_picks_still_seed_new_sessions(self):
        self.be.set_model(PARENT, "haiku")
        self.be.set_effort(PARENT, "low")
        self.be.set_mode(PARENT, "plan")
        self.assertEqual(self._seed(), {"model": "haiku", "effort": "low", "mode": "plan"})
        reg = self._new_session()
        self.assertEqual((reg.get("model"), reg.get("effort"), reg.get("mode")), ("haiku", "low", "plan"))

    def test_a_broken_out_thread_seeds_like_any_session(self):
        self.assertTrue(self.be.promote_thread(THREAD, "sidework", "#123456", "#ffffff"))
        self.be.set_effort(THREAD, "low")
        self.assertEqual(self._seed()["effort"], "low")


class BuildSessionLineage(unittest.TestCase):
    """kernel build_session: the divider event, the top-level branch, the parent's branches."""

    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        t = self.now - 600
        self.records = [uline(t, "how should the retry loop back off?", "u1"),
                        aline(t + 5, "Use exponential backoff.", "a1", parent="u1"),
                        uline(t + 60, "and the cap?", "u2", parent="a1"),
                        aline(t + 65, "Cap the delay at two minutes.", "a2", parent="u2")]
        for sid, name in ((PARENT, "parent"), (CHILD, "child")):
            (jd.NAMES / sid).write_text("%s\t%s" % (name, self.cdir))
            (self.proj / (sid + ".jsonl")).write_text(
                "\n".join(json.dumps(r) for r in self.records) + "\n")
        (jd.SDKDIR / (CHILD + ".json")).write_text(json.dumps(
            {"sid": CHILD, "name": "child", "cwd": self.cdir, "lastSid": CHILD, "alive": True,
             "forkedFrom": {"sid": PARENT, "name": "parent", "cut": "a1", "t": self.now - 300}}))
        self._saved_sdk = km._sdk
        kids = {PARENT: [{"sid": CHILD, "name": "child", "cut": "a1", "t": self.now - 300}]}

        class FakeBe:
            def fork_children(self):
                return kids

            def owns(self, sid):
                return False

            def __getattr__(self, name):        # every other backend accessor answers "nothing"
                return lambda *a, **k: None

        self._fake_be = FakeBe()
        km._sdk = lambda: self._fake_be

    def tearDown(self):
        km._sdk = self._saved_sdk
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def test_the_child_gets_its_divider_right_after_the_branch_point(self):
        m = km.build_session(CHILD, self.now, live_map={})
        self.assertEqual(m["branch"]["fromSid"], PARENT)
        self.assertEqual(m["branch"]["fromName"], "parent")
        evs = m["events"]
        at = next(i for i, e in enumerate(evs) if e.get("uuid") == "a1")
        self.assertEqual(evs[at + 1]["kind"], "branch",
                         "the divider sits right after the branch-point record")
        self.assertEqual(evs[at + 1]["uuid"], "branch:a1")
        self.assertEqual(evs[at + 1]["fromName"], "parent")

    def test_the_parent_gets_its_children_list(self):
        m = km.build_session(PARENT, self.now, live_map={})
        self.assertIsNone(m["branch"], "the parent is not itself a fork")
        self.assertEqual([k["sid"] for k in m["branches"]], [CHILD])
        self.assertEqual(m["branches"][0]["cut"], "a1")

    def test_an_unforked_session_carries_no_lineage(self):
        (jd.SDKDIR / (CHILD + ".json")).write_text(json.dumps(
            {"sid": CHILD, "name": "child", "cwd": self.cdir, "lastSid": CHILD, "alive": True}))
        km._sdk = lambda: None
        m = km.build_session(CHILD, self.now, live_map={})
        self.assertIsNone(m["branch"])
        self.assertIsNone(m["branches"])
        self.assertFalse(any(e.get("kind") == "branch" for e in m["events"]))


if __name__ == "__main__":
    unittest.main()
