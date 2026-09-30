#!/usr/bin/env python3
"""A silent mid-turn model swap mints a COMPLETED card (the user 2026-08-23, approved 08-19 and
revived): the API fell back without a request, and the swap was invisible before this. The card
pops into Completed with a done why naming the swap; a user-driven /model pick (pending marker) or
the first learn never mints. SYNTHETIC fixtures."""
import os
import re
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
sb = load_source("romp_sdk_backend",
                      os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"


class FallbackCard(unittest.TestCase):
    """OWN sid, like DedupeBackstop below and for the same reason: load_goals replays the per-sid
    user-override journal on every load, other test modules journal user gestures against the shared
    placeholder SID, and a fresh store's first node id collides ("<sid>:g1"). The collision is live
    in the default serial order, not latent: by the time this file runs, earlier modules have left a
    resolve on <SID>:g1 plus restore and unclear rows for other node ids in the journal, and
    replaying them plants two foreign nodes in the freshly minted store. The assertions held only
    because none of those ops touches g1's nodeComplete or status; a journaled followup or move on
    <SID>:g1 replays as a reopen and turns them red, which a downstream tree carrying more test
    modules hit under parallel ordering. A sid nobody else touches keeps these tests about the mint,
    not the journal."""

    FSID = "5a5a5a5a-6b6b-7c7c-8d8d-9e9e9e9e9e9e"

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        try:
            (jd._overrides_dir() / (self.FSID + ".jsonl")).unlink()
        except OSError:
            pass

    def test_mints_a_completed_top_naming_the_swap(self):
        gid = jd.mint_fallback_card(self.FSID, "claude-fable-5", "claude-sonnet-5", ev_t=1_787_500_000)
        self.assertTrue(gid)
        store = jd.load_goals(self.FSID)
        nd = store["nodes"][gid]
        self.assertTrue(nd["nodeComplete"])
        self.assertIn("fell back to claude-sonnet-5", nd["doneWhy"])
        self.assertIn("Model changed automatically: claude-fable-5 → claude-sonnet-5", nd["text"])
        self.assertEqual(store["status"].get(gid), "completed", "pops straight into Completed")
        dones = [e for e in nd["log"] if e.get("kind") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["src"], "romp", "kernel-authored bookkeeping, never a question")

    def test_the_backend_transition_gates_are_pinned(self):
        src = open(os.path.join(HERE, "..", "kernel", "sdk_backend.py")).read()
        self.assertIn("if self.model and not self._model_pending and not cleared \\", src,
                      "first learns and user-driven picks never mint")
        self.assertIn('and not _lands_on_pick(pm, getattr(self, "chosen_model", "")) \\', src,
                      "a move onto a versioned pick's own model is that pick landing, never a fallback (the #33 follow-up)")
        self.assertIn('on_model_fallback', src)
        ksrc = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("jd.mint_fallback_card(sid, frm, to)", ksrc, "the kernel wires the hook at boot")


class DowngradeOnlyGate(unittest.TestCase):
    """The card mints ONLY on a known down-tier transition (the user 2026-08-23, whose own upgrade
    to a bigger model wore a "fallback" card): romp only sees its own picks pending, so a /model
    typed inside the CLI arrives as an unrequested transition too — and a capacity fallback never
    moves a session up-tier."""

    def test_rank_and_downgrade_shapes(self):
        # exec just the pure helper block: loading the whole backend pulls the live SDK dependency
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i = src.index("_MODEL_TIERS")
        j = src.index("\nclass SdkSession", i)
        sb = type("NS", (), {})
        ns = {"re": re}
        exec(src[i:j], ns)
        sb._model_downgrade = staticmethod(ns["_model_downgrade"])
        self.assertTrue(sb._model_downgrade("claude-fable-5", "claude-sonnet-5"))
        self.assertTrue(sb._model_downgrade("Opus 5", "claude-haiku-4-5"))
        self.assertFalse(sb._model_downgrade("claude-opus-5", "claude-fable-5"), "an upgrade is the user's doing")
        self.assertFalse(sb._model_downgrade("claude-opus-5", "some-experimental-model"), "unknown target: no card")
        self.assertFalse(sb._model_downgrade("", "claude-haiku-4-5"), "unknown source: no card")

    def test_an_older_version_of_the_same_family_is_a_downgrade(self):
        # A safeguards fallback can stay inside the family (a newer Opus answered by an older one, first seen 2026-09-23): the
        # family rank alone read it as lateral, so no card, no requested-model mark and no retry ever followed it.
        # The version decides inside a family, and only where BOTH names carry one: a bare alias ("Opus") says nothing
        # about which version it resolves to, and a date stamp is not a version.
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i = src.index("_MODEL_TIERS")
        j = src.index("\nclass SdkSession", i)
        ns = {"re": re}
        exec(src[i:j], ns)
        down = ns["_model_downgrade"]
        self.assertTrue(down("claude-opus-5-5", "claude-opus-4-8"), "the raw ids the CLI reports")
        self.assertTrue(down("Opus 5.5", "Opus 4.8"), "the badge names the learn compares")
        self.assertTrue(down("Opus 5.5", "claude-opus-5"), "a point release down: 5.5 to 5")
        self.assertTrue(down("claude-sonnet-5", "claude-sonnet-4-5"))
        self.assertTrue(down("us.anthropic.claude-opus-5-5-v1:0", "Opus 4.8"), "a provider-prefixed id reads the same")
        self.assertFalse(down("claude-opus-4-8", "claude-opus-5-5"), "a newer version is an upgrade: the user's doing")
        self.assertFalse(down("Opus 5.5", "claude-opus-5-5"), "the same model under its two names")
        self.assertFalse(down("Opus", "Opus 4.8"), "an unversioned alias label: which version it means is unknown")
        self.assertFalse(down("claude-opus-5-5", "gw-opus-mini"), "a gateway id with the family word and no version")
        self.assertFalse(down("claude-haiku-4-5-20251001", "claude-haiku-4-5"), "a date stamp is not a version")
        self.assertFalse(down("claude-3-opus-20240229", "claude-opus-4-8"), "an old-style id carries its version before the family")

    def test_the_learn_path_wires_the_gate(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        self.assertIn("and _model_downgrade(self.model, pm):", src)

    def test_a_move_onto_a_versioned_pick_is_the_pick_landing(self):
        # (2026-09-30, the #33 follow-up) a fallback moves a session AWAY from what was picked, so a move onto the very
        # model a versioned pick names is that pick landing, even where a report from before the pick had moved the live
        # name above it; an alias or the default names no version, so nothing lands on it by name
        self.assertTrue(hasattr(sb, "_lands_on_pick"), "the learn has no reading of a move onto the pick's own model")
        lands = sb._lands_on_pick
        for pm, pick in (("Opus 4.8", "claude-opus-4-8"), ("Opus 4.8", "claude-opus-4-8[1m]"), ("Opus 5", "claude-opus-5")):
            self.assertTrue(lands(pm, pick), "%s is the very model %s names: the pick landing" % (pm, pick))
        for pm, pick, why in (("Opus 5", "claude-opus-5-5", "a lower version than the pick is a fallback"),
                              ("Opus 4.8", "claude-opus-5-5", "a lower version than the pick is a fallback"),
                              ("Opus 5", "opus", "an alias names no version"),
                              ("Sonnet 5", "default", "the default names no model"),
                              ("Sonnet 5", "", "an empty pick is the default"),
                              ("Sonnet 4.8", "claude-opus-4-8", "the same number in another family"),
                              ("gw-mini", "gw-mini", "a name with no family word carries no version")):
            self.assertFalse(lands(pm, pick), "%s on a %r pick: %s" % (pm, pick, why))

    def test_a_session_rebuilt_with_a_pick_in_flight_starts_from_the_picks_name_unless_the_pick_is_above_it(self):
        # (2026-09-30, the #33 follow-up's review) the name a session rebuilt with a model pick in flight starts from:
        # the pick's own, so the pick's first report is not read as a fallback from the model it replaced; but the old
        # one for a pick ABOVE it, since the rebuilt session can attach to a CLI that never got the pick (an attach tells
        # the CLI nothing) or replay a reply from before it, and relabeled, that report of the old model read as the
        # pick falling back
        self.assertTrue(hasattr(sb, "_heal_pick_label"), "a rebuilt session has no reading of the name to start from")
        heal = sb._heal_pick_label
        for pick, old, want, why in (("claude-opus-4-8", "Opus 5.5", "Opus 4.8", "an older version: the pick's name"),
                                     ("claude-sonnet-5", "Opus 5.5", "Sonnet 5", "a lower family: the pick's name"),
                                     ("opus", "Opus 5.5", "Opus", "a Latest click names no version: the pick's name"),
                                     ("default", "Opus 5.5", "", "the account default names no model"),
                                     ("", "Opus 5.5", "", "an empty pick is the default"),
                                     ("opus", "", "Opus", "no name before the pick"),
                                     ("claude-opus-5-5", "Opus 4.8", "Opus 4.8", "a newer version: the old name stays"),
                                     ("fable", "Opus 5.5", "Opus 5.5", "a higher family: the old name stays"),
                                     ("opus", "Sonnet 5", "Sonnet 5", "a Latest click above the old family: the old name stays")):
            self.assertEqual(heal(pick, old), want, "%r picked on %r: %s" % (pick, old, why))


class SidechainNeverLearns(unittest.TestCase):
    """Subagent traffic must never teach the session its model (the user 2026-08-24): a Task/Agent
    subagent streams its OWN AssistantMessages tagged parent_tool_use_id, routinely on a LOWER tier
    than the parent, and learning one filed a false "Model changed automatically" downgrade card and
    wrote the subagent's model over the registry liveModel the statusline badge reads. _on_message
    takes the SDK message classes as PARAMETERS (the lazy-import seam), so the branch runs
    hermetically with synthetic classes."""

    class _SM:                       # SystemMessage stand-in — never instantiated here
        pass

    class _RM:                       # ResultMessage stand-in — never instantiated here
        pass

    class _AM:                       # AssistantMessage stand-in
        def __init__(self, model, ptid=None):
            self.model = model
            self.parent_tool_use_id = ptid
            self.error = None
            self.content = []

    def _session(self):
        calls = {"fallback": [], "reg": []}

        class Backend:
            state_dir = None

            def on_model_fallback(sid, frm, to):        # looked up on the CLASS, called unbound —
                calls["fallback"].append((frm, to))     # exactly how the kernel wires the hook

            def _update_reg(self, sid, **kw):
                calls["reg"].append(kw)

            def _deliver_rename_ping(self, s):
                return False   # settle hook (2026-08-25); no ping in these worlds

            def _poke(self):
                pass

            def _forward(self, sess, msg):              # the unconditional kernel forward at the branch tail
                pass

            def _log(self, *a, **k):
                pass

        s = object.__new__(sb.SdkSession)               # just the state the branch touches
        s.backend = Backend()
        s.sid = SID
        s.name = "web"
        s.model = "Fable 5"
        s._model_id = "claude-fable-5"                  # the id behind that name, as a reg-seeded session carries it
        #                                                 (a FIRST-known id is persisted even under an unchanged
        #                                                 name — test_sdk_backend covers that write)
        s._model_pending = ""
        s.retrying = False
        s.retry_count = 0
        s.retry_info = None
        return s, calls

    def _msg(self, s, msg):
        s._on_message(msg, self._AM, self._RM, self._SM)

    def test_a_tagged_downgrade_teaches_and_mints_nothing(self):
        s, calls = self._session()
        self._msg(s, self._AM("claude-opus-5", ptid="toolu_01AAAAAAAAAAAAAAAAAAAAAA"))
        self.assertEqual(calls["fallback"], [], "a subagent's own turns never read as a capacity fallback")
        self.assertEqual(s.model, "Fable 5", "self.model untouched")
        self.assertEqual(calls["reg"], [], "registry liveModel untouched")

    def test_a_genuine_main_loop_downgrade_still_mints_exactly_once(self):
        s, calls = self._session()
        self._msg(s, self._AM("claude-opus-5"))         # untagged: the main loop itself fell back
        self.assertEqual(calls["fallback"], [("Fable 5", "Opus 5")])
        self.assertEqual(s.model, "Opus 5")
        self.assertEqual(calls["reg"][-1].get("liveModel"), "Opus 5")

    def test_livemodel_stays_main_loop_across_a_subagent_burst(self):
        s, calls = self._session()
        for i in range(3):                              # an Explore fan-out: three sidechain turns, lower tier
            self._msg(s, self._AM("claude-opus-5", ptid="toolu_01BBBBBBBBBBBBBBBBBB%02d" % i))
        self._msg(s, self._AM("claude-fable-5"))        # the main loop comes back unchanged
        self.assertEqual(s.model, "Fable 5")
        self.assertEqual(calls["fallback"], [])
        self.assertEqual(calls["reg"], [], "an unchanged main-loop model is a no-op write")

    def test_the_sidechain_guard_is_pinned_at_the_source(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i = src.index("elif isinstance(msg, AssistantMessage):")
        j = src.index("elif isinstance(msg, ResultMessage):", i)
        branch = src[i:j]
        self.assertIn('if getattr(msg, "parent_tool_use_id", None):', branch)
        self.assertLess(branch.index("parent_tool_use_id"), branch.index("self._learn_model("),
                        "the sidechain drop gates the learn, mirroring msg_to_atom's drop")


class DedupeBackstop(unittest.TestCase):
    """One open card per identical swap (the user 2026-08-24): mint_fallback_card returns None while
    an identical UNCLEARED frm→to card sits on the board — existence-keyed, never a time cooldown.
    Clearing the card (either shape: the feed's view-clear in cleared.jsonl, or a /clear boundary's
    verdict flag) is the re-arming event.

    OWN sid, deliberately: load_goals replays the per-sid user-override journal on every load, and
    other test modules journal user gestures against the shared placeholder SID — a journaled resolve
    for a colliding "<SID>:g1" id replayed onto our store mid-test and folded the cleared card back
    to done/uncleared, so the dedupe (correctly, per its own rule) blocked the re-arm. A sid nobody
    else touches keeps these tests about the dedupe, not the journal."""

    DSID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        for fp in (jd.STATE / "cleared.jsonl", jd._overrides_dir() / (self.DSID + ".jsonl")):
            try:
                fp.unlink()
            except OSError:
                pass

    def test_an_identical_open_card_blocks_a_second_mint(self):
        g1 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000)
        self.assertTrue(g1)
        self.assertIsNone(jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_060))
        store = jd.load_goals(self.DSID)
        same = [nd for nd in store["nodes"].values()
                if nd.get("text") == "Model changed automatically: Fable 5 → Opus 5"]
        self.assertEqual(len(same), 1, "however many identical observations, one card")

    def test_a_different_swap_mints_beside_the_open_card(self):
        self.assertTrue(jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000))
        self.assertTrue(jd.mint_fallback_card(self.DSID, "Opus 5", "Sonnet 5", ev_t=1_787_500_001),
                        "a different frm→to is new information, not a repeat")

    def test_a_feed_view_clear_rearms_the_mint(self):
        g1 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000)
        with open(jd.STATE / "cleared.jsonl", "a") as f:      # the feed clear's authoritative record
            f.write('{"id": "%s"}\n' % g1)
        g2 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_200)
        self.assertTrue(g2, "the user's cross-off is the re-arming event")
        self.assertNotEqual(g1, g2)

    def test_a_verdict_clear_rearms_the_mint(self):
        g1 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_000)
        store = jd.load_goals(self.DSID)
        self.assertTrue(jd.record_verdict(store, store["nodes"][g1], "romp", "clear",
                                          1_787_500_100, why="dropped with the cleared conversation"))
        jd.rollup_status(store, True)
        jd.save_goals(self.DSID, store)
        g2 = jd.mint_fallback_card(self.DSID, "Fable 5", "Opus 5", ev_t=1_787_500_200)
        self.assertTrue(g2, "a boundary-settle clear re-arms too")


if __name__ == "__main__":
    unittest.main()
