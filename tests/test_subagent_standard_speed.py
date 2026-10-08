#!/usr/bin/env python3
"""Subagents at standard speed (experimental): one session's main loop runs in fast mode while the subagents it spawns
run Opus 5.5 at standard speed.

Claude Code has no per-agent speed: a subagent copies its parent's fast-mode flag, and the request sends `speed: "fast"`
when that flag is on AND the request's model is fast-capable (2.1.282). The workaround makes the subagents' model look
not fast-capable while it stays Opus 5.5 on the wire: every subagent runs `claude-opus-5-5[1m]`
(CLAUDE_CODE_SUBAGENT_MODEL + _FORCE), a `modelOverrides` entry maps that string to the catalog's `claude-opus-5`, and
CLAUDE_CODE_MODEL_CAPABILITIES strips `fast_mode` from `claude-opus-5` (and gives it Opus 5.5's prompt capabilities);
CLAUDE_CODE_MAX_OUTPUT_TOKENS keeps the subagents' output cap at Opus 5.5's. Verified in a lab on 2.1.282 (main requests
`speed: "fast"`, subagent requests no speed, both `model: claude-opus-5-5`).

The mechanics under test:
  * the setter refuses, with the reason, unless the session's model is exactly claude-opus-5-5 and fast mode is on;
    off is always accepted; a change persists in the registry and relaunches on the quiet road;
  * the launch carries the recipe in the session's OWN flag-settings file (modelOverrides + the four env vars beside the
    session's own env), and nothing when the switch is off or a precondition no longer holds (said once);
  * the launch record says whether the recipe rode the launch, so a relaunch is asked for when it did not;
  * after launch the subagents' and the main loop's `usage.speed` are read off the transcripts, and a fast subagent call
    or a main loop that never ran fast is said once per launch;
  * the subagent calls Claude Code priced at Opus 5 rates are recorded at Opus 5.5's standard rates, from each call's
    own usage through kernel/cli_prices.py, and the turn row keeps the CLI's own figure beside the repricing.

Synthetic fixtures only: placeholder sids, an invented session (`web`), invented message ids.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_substd", os.path.join(BIN, "romp_sdk_backend.py"))
cpr = load_source("romp_cli_prices", os.path.join(os.path.dirname(HERE), "kernel", "cli_prices.py"))

SID = "11111111-2222-4333-8444-00000000c5d1"   # private to this module (CLAUDE.md, goal-store fixtures)
ENV = {"FEATURE_FLAG": "1"}


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    logs = []
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda m, problem=False, **k: logs.append((str(m), problem)))
    be._logs = logs
    return be


def _reg(be, **fields):
    r = {"sid": SID, "name": "web", "cwd": str(be.state_dir), "alive": True, "model": "claude-opus-5-5", "fast": True}
    r.update(fields)
    sb.write_reg(Path(be.state_dir), SID, r)
    return r


def _live(be, **fields):
    r = _reg(be, **fields)
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    s.asks = []
    s.request_reconnect = lambda *a, **k: s.asks.append(k.get("why"))
    be.sessions[SID] = s
    return s


def _problems(be, word):
    """The problem rows (the ring the dashboard's error center reads) that mention `word`."""
    return [r["text"] for r in be._problems if word in r["text"]]


class Recipe(unittest.TestCase):
    def test_the_recipe_is_the_one_verified_in_the_lab(self):
        self.assertEqual(sb.SUBAGENT_STANDARD_MODEL, "claude-opus-5-5")
        self.assertEqual(sb.SUBAGENT_STANDARD_OVERRIDES, {"claude-opus-5": "claude-opus-5-5[1m]"})
        env = sb.SUBAGENT_STANDARD_ENV
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "claude-opus-5-5[1m]")
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"], "1")
        self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "128000")
        caps = env["CLAUDE_CODE_MODEL_CAPABILITIES"]
        self.assertTrue(caps.startswith("claude-opus-5="), "the capability edit names the override's target, never Opus 5.5")
        self.assertIn("-fast_mode", caps.split("=", 1)[1].split(","))
        self.assertIn("opus_5_5_prompt_bundle", caps.split("=", 1)[1].split(","))

    def test_flag_settings_carry_model_overrides_only_when_asked(self):
        d = tempfile.mkdtemp()
        p = sb.flag_settings_path(d, SID, fast=True, env=dict(ENV, **sb.SUBAGENT_STANDARD_ENV),
                                  model_overrides=sb.SUBAGENT_STANDARD_OVERRIDES)
        got = json.loads(Path(p).read_text())
        self.assertEqual(got["modelOverrides"], {"claude-opus-5": "claude-opus-5-5[1m]"})
        self.assertTrue(got["fastMode"])
        p2 = sb.flag_settings_path(d, SID, fast=True)
        self.assertNotIn("modelOverrides", json.loads(Path(p2).read_text()), "rewritten per use: off leaves no override behind")

    def test_the_refusal_names_what_is_missing(self):
        self.assertEqual(sb.subagent_standard_refusal("claude-opus-5-5", True), "")
        for model in ("opus", "claude-opus-5-5[1m]", "claude-fable-5-1", ""):
            why = sb.subagent_standard_refusal(model, True)
            self.assertIn("claude-opus-5-5", why, "the refusal names the model it needs: %r" % model)
        self.assertIn("fast mode", sb.subagent_standard_refusal("claude-opus-5-5", False))


class Setter(unittest.TestCase):
    def setUp(self):
        self.be = _backend()

    def test_on_persists_and_relaunches_on_the_quiet_road(self):
        s = _live(self.be)
        self.assertTrue(self.be.set_subagent_standard(SID, "on"))
        self.assertIs(sb.read_reg(Path(self.be.state_dir), SID).get("subagentStandard"), True)
        self.assertTrue(s.subagent_standard)
        self.assertEqual(s.asks, ["subagent speed"])
        self.assertIn("subagent speed", sb.PICK_RELAUNCH_WHYS, "a pick's own ask: dropped once a launch carries it")
        self.assertTrue(self.be.set_subagent_standard(SID, "on"))
        self.assertEqual(s.asks, ["subagent speed"], "an unchanged ask relaunches nothing")

    def test_off_is_always_accepted_and_restores_the_normal_launch_at_the_relaunch(self):
        s = _live(self.be, subagentStandard=True, model="claude-fable-5-1", fast=False)
        self.assertTrue(self.be.set_subagent_standard(SID, "off"))
        self.assertIs(sb.read_reg(Path(self.be.state_dir), SID).get("subagentStandard"), False)
        self.assertFalse(s.subagent_standard)
        self.assertEqual(s.asks, ["subagent speed"])

    def test_on_is_refused_with_the_reason_when_the_model_is_not_opus_5_5(self):
        for model in ("opus", "claude-opus-5-5[1m]", "claude-fable-5-1"):
            _live(self.be, model=model)
            self.assertFalse(self.be.set_subagent_standard(SID, "on"), model)
            self.assertIn("claude-opus-5-5", self.be.last_subagent_standard_refusal)
            self.assertFalse(sb.read_reg(Path(self.be.state_dir), SID).get("subagentStandard"))

    def test_on_is_refused_when_fast_mode_is_off(self):
        _live(self.be, fast=False, fastOff=True)
        self.assertFalse(self.be.set_subagent_standard(SID, "on"))
        self.assertIn("fast mode", self.be.last_subagent_standard_refusal)

    def test_a_dormant_session_is_judged_from_its_registry(self):
        _reg(self.be, fast=False, fastOff=True)
        self.assertFalse(self.be.set_subagent_standard(SID, "on"))
        _reg(self.be)
        self.assertTrue(self.be.set_subagent_standard(SID, "on"))
        reg = sb.read_reg(Path(self.be.state_dir), SID)
        self.assertIs(reg.get("subagentStandard"), True)
        # a host may keep a CLI launched before the change: the pick waits, so an attach compares it with that CLI's record
        self.assertIn("subagentStandard", reg["relaunchWait"]["picks"])

    def test_an_attach_to_a_cli_launched_without_it_relaunches(self):
        s = _live(self.be, subagentStandard=True,
                  relaunchWait={"why": "subagent speed", "t": 1, "picks": {"subagentStandard": {"subagentStandard": True}}})
        s._switch_wanted = ""
        s._reconcile_waiting_picks({"effort": "medium", "auth": "", "authLogin": "", "fast": True, "mode": "",
                                    "env": sb.env_digest({})}, False)
        self.assertEqual(s._switch_wanted, "subagent speed", "the surviving CLI lacks the recipe: relaunch once quiet")
        s._switch_wanted = ""
        s._reconcile_waiting_picks(dict(s.launch_values()), False)
        self.assertEqual(s._switch_wanted, "", "a CLI launched with it carries the pick")
        self.assertNotIn("relaunchWait", sb.read_reg(Path(self.be.state_dir), SID) or {})

    def test_unknown_session_and_bad_value(self):
        self.assertFalse(self.be.set_subagent_standard(SID, "on"), "no such session")
        _reg(self.be)
        self.assertFalse(self.be.set_subagent_standard(SID, "maybe"))


class _OptionsBase(unittest.TestCase):
    def setUp(self):
        self.be = _backend()
        self._fake_sdk = "claude_agent_sdk" not in sys.modules and not sb.sdk_importable()
        if self._fake_sdk:
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake

    def tearDown(self):
        if self._fake_sdk:
            sys.modules.pop("claude_agent_sdk", None)

    def _settings(self, s):
        kw = self.be._options(s, dict)
        return json.loads(Path(kw["settings"]).read_text()) if kw.get("settings") else {}


class LaunchCarriesTheRecipe(_OptionsBase):
    def test_on_with_its_preconditions_writes_the_recipe_into_the_sessions_own_settings(self):
        s = sb.SdkSession(self.be, _reg(self.be, subagentStandard=True, env=ENV))
        got = self._settings(s)
        self.assertTrue(got["fastMode"])
        self.assertEqual(got["modelOverrides"], sb.SUBAGENT_STANDARD_OVERRIDES)
        self.assertEqual(got["env"], dict(ENV, **sb.SUBAGENT_STANDARD_ENV), "beside the session's own env, never instead of it")
        self.assertIs(s._launch_record["subagentStandard"], True)
        self.assertEqual(sb.PICK_LAUNCH_KEYS["subagentStandard"], ("subagentStandard",))

    def test_off_launches_exactly_as_before(self):
        s = sb.SdkSession(self.be, _reg(self.be, env=ENV))
        got = self._settings(s)
        self.assertNotIn("modelOverrides", got)
        self.assertEqual(got["env"], ENV)
        self.assertIs(s._launch_record["subagentStandard"], False)

    def test_on_without_its_preconditions_launches_plainly_and_says_so_once(self):
        s = sb.SdkSession(self.be, _reg(self.be, subagentStandard=True, model="claude-fable-5-1"))
        for _ in range(2):
            got = self._settings(s)
            self.assertNotIn("modelOverrides", got)
            self.assertFalse(set(sb.SUBAGENT_STANDARD_ENV) & set(got.get("env") or {}))
        said = _problems(self.be, "standard speed")
        self.assertEqual(len(said), 1, said)
        self.assertIn("claude-opus-5-5", said[0])
        self.assertIs(s._launch_record["subagentStandard"], False)


def _rec(mid, model, speed, ts, stop="end_turn", usage=None, sidechain=False):
    u = {"input_tokens": 1000, "output_tokens": 500, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 10000,
         "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 0}, "service_tier": "standard",
         "inference_geo": "global"}
    if usage:
        u.update(usage)
    if speed is not None:
        u["speed"] = speed
    return json.dumps({"type": "assistant", "isSidechain": sidechain, "timestamp": ts,
                       "message": {"id": mid, "model": model, "role": "assistant", "stop_reason": stop,
                                   "content": [{"type": "text", "text": "ok"}], "usage": u}}) + "\n"


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class _TranscriptBase(unittest.TestCase):
    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self.be = _backend()
        self.be._forward = lambda sess, msg: None
        self.be._turn_completed = lambda sid: None
        self.work = tempfile.mkdtemp()
        _reg(self.be, cwd=self.work, subagentStandard=True)
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.be.state_dir), SID))
        self.main = Path(sb.transcript_path(self.work, SID))
        self.subdir = self.main.with_suffix("") / "subagents"
        self.subdir.mkdir(parents=True, exist_ok=True)
        self.main.write_text(_rec("msg_old_main", "claude-opus-5-5", "standard", _iso(time.time() - 3600)))   # history
        self.t0 = time.time()

    def tearDown(self):
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before

    def _append(self, path, line):
        with open(path, "a") as f:
            f.write(line)


class SpeedCheck(_TranscriptBase):
    def test_a_clean_launch_records_main_fast_and_subagents_standard(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        now = _iso(time.time())
        self._append(self.main, _rec("msg_m1", "claude-opus-5-5", "fast", now))
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "standard", now))
        self.s._sub_std_settle()
        chk = sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]
        self.assertEqual(chk["verdict"], "ok")
        self.assertEqual((chk["mainFast"], chk["mainStandard"], chk["subFast"], chk["subStandard"]), (1, 0, 0, 1),
                         "the hour-old main call is history, not this launch's")
        self.assertFalse(_problems(self.be, "subagent"))
        self.assertIn("standard", self.s.snapshot()["subagentSpeed"])

    def test_a_fast_subagent_call_is_said_once_per_launch(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        now = _iso(time.time())
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "fast", now))
        self.s._sub_std_settle()
        self._append(self.subdir / "agent-a2.jsonl", _rec("msg_s2", "claude-opus-5-5", "fast", now))
        self.s._sub_std_settle()
        said = _problems(self.be, "fast")
        self.assertEqual(len(said), 1, said)
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["verdict"], "subagent-fast")
        self.s._sub_std_begin({"subagentStandard": True}, True)   # a relaunch is a new episode
        self._append(self.subdir / "agent-a3.jsonl", _rec("msg_s3", "claude-opus-5-5", "fast", _iso(time.time())))
        self.s._sub_std_settle()
        self.assertEqual(len(_problems(self.be, "fast")), 2)

    def test_a_main_loop_that_never_ran_fast_is_said(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        self._append(self.main, _rec("msg_m1", "claude-opus-5-5", "standard", _iso(time.time())))
        self.s._sub_std_settle()
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["verdict"], "main-slow")
        self.assertEqual(len(_problems(self.be, "main loop")), 1)

    def test_a_partial_record_is_counted_when_its_final_record_lands(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        now = _iso(time.time())
        f = self.subdir / "agent-a1.jsonl"
        self._append(f, _rec("msg_s1", "claude-opus-5-5", None, now, stop=None, usage={"output_tokens": 7}))
        self.assertEqual(self.s._sub_std_settle(), 0.0)
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subStandard"], 0)
        self._append(f, _rec("msg_s1", "claude-opus-5-5", "standard", now))
        self.assertLess(self.s._sub_std_settle(), 0.0)
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subStandard"], 1)
        self._append(f, _rec("msg_s1", "claude-opus-5-5", "standard", now))   # the same call written again
        self.assertEqual(self.s._sub_std_settle(), 0.0, "a call is priced once")

    def test_a_launch_without_the_recipe_reads_nothing(self):
        # the switch is on but the serving CLI was launched before it (an attach, a relaunch still waiting for quiet)
        self.s._sub_std_begin({"subagentStandard": False}, True)
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "fast", _iso(time.time())))
        self.assertEqual(self.s._sub_std_settle(), 0.0)
        chk = sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]
        self.assertEqual((chk["verdict"], chk["line"]), ("pending", "applies at the next relaunch"))
        self.assertFalse(_problems(self.be, "fast"), "a CLI launched without the recipe is not judged by it")
        self.assertEqual(self.s.snapshot()["subagentSpeed"], "applies at the next relaunch")

    def test_an_older_hosts_launch_record_reads_as_launched_without_it(self):
        self.s._sub_std_begin({"effort": "high"}, False)   # a record written before the key existed
        self.assertFalse(self.s._sub_std_launched)
        self.assertEqual(sb.launch_value({"effort": "high"}, "subagentStandard"), False)

    def test_an_attach_resumes_the_episode_and_reprices_what_no_kernel_watched_once(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        f = self.subdir / "agent-a1.jsonl"
        self._append(f, _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time())))
        first = self.s._sub_std_settle()
        self.assertLess(first, 0.0)
        time.sleep(0.01)
        self._append(f, _rec("msg_s2", "claude-opus-5-5", "standard", _iso(time.time() + 1)))   # while no kernel watched
        s2 = sb.SdkSession(self.be, sb.read_reg(Path(self.be.state_dir), SID))       # the next kernel's object
        s2._sub_std_begin({"subagentStandard": True}, False)                          # an attach to the same CLI
        self.assertAlmostEqual(s2._sub_std_settle(), first, places=9, msg="msg_s2 alone: msg_s1 was repriced before")
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subStandard"], 2)


class Repricing(_TranscriptBase):
    def test_the_correction_is_opus_5_5_less_opus_5_for_each_standard_subagent_call(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        now = _iso(time.time())
        one_h = {"cache_creation": {"ephemeral_5m_input_tokens": 500, "ephemeral_1h_input_tokens": 1500}}
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "standard", now))
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s2", "claude-opus-5-5", "standard", now, usage=one_h))
        recs = [json.loads(l)["message"]["usage"] for l in (self.subdir / "agent-a1.jsonl").read_text().splitlines()]
        want = sum(cpr.call_cost("claude-opus-5-5", u) - cpr.call_cost("claude-opus-5", u) for u in recs)
        got = self.s._sub_std_settle()
        self.assertAlmostEqual(got, want, places=9)
        # spot check of the arithmetic: input/output/cache writes at 0.8 of Opus 5's, cache reads at 0.4
        u = recs[0]
        self.assertAlmostEqual(cpr.call_cost("claude-opus-5", u), (1000 * 5 + 500 * 25 + 2000 * 6.25 + 10000 * 0.5) / 1e6)
        self.assertAlmostEqual(cpr.call_cost("claude-opus-5-5", u), (1000 * 4 + 500 * 20 + 2000 * 5 + 10000 * 0.2) / 1e6)

    def test_a_fast_subagent_call_is_not_repriced(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "fast", _iso(time.time())))
        self.assertEqual(self.s._sub_std_settle(), 0.0, "a fast call was not priced as Opus 5 standard: left as the CLI priced it")

    def test_the_turn_records_the_repriced_dollars_and_keeps_the_clis_figure_on_the_row(self):
        s = self.s

        async def _noop():
            pass
        s._do_refresh_context = _noop
        s._do_refresh_usage = _noop
        s._seed_spend_watermarks()
        s._sub_std_begin({"subagentStandard": True}, True)
        u = json.loads(_rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time())))["message"]["usage"]
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time())))
        corr = cpr.call_cost("claude-opus-5-5", u) - cpr.call_cost("claude-opus-5", u)

        class _R:
            subtype = "success"
        r = _R()
        r.total_cost_usd = 0.5
        r.model_usage = {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 10, "cacheReadInputTokens": 0,
                                             "cacheCreationInputTokens": 0, "costUSD": 0.4},
                         "claude-opus-5-5[1m]": {"inputTokens": 1000, "outputTokens": 500, "cacheReadInputTokens": 10000,
                                                 "cacheCreationInputTokens": 2000, "costUSD": 0.1}}

        async def go():
            s._on_message(r, type("A", (), {}), _R, type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(go())
        day = json.loads(Path(self.be.state_dir, "spend.json").read_text())["days"][time.strftime("%Y-%m-%d")]
        self.assertAlmostEqual(day["usd"], 0.5 + corr, places=6)
        row = [json.loads(l) for l in Path(self.be.state_dir, "turns.jsonl").read_text().splitlines()][-1]
        self.assertAlmostEqual(row["usd"], 0.5 + corr, places=6)
        self.assertAlmostEqual(row["cliUsd"], 0.5, places=6)
        self.assertAlmostEqual(row["subagentRepriceUsd"], corr, places=6)
        self.assertAlmostEqual(row["cumulativeUsd"], 0.5, places=6, msg="the CLI's cumulative stays the CLI's own")


class Surfaces(unittest.TestCase):
    def test_the_snapshot_and_the_dormant_row_carry_the_switch(self):
        be = _backend()
        r = _reg(be, subagentStandard=True,
                 subagentSpeed={"verdict": "ok", "line": "main loop fast, 3 subagent calls at standard speed"})
        s = sb.SdkSession(be, r)
        snap = s.snapshot()
        self.assertIs(snap["subagentStandard"], True)
        self.assertTrue(snap["subagentStandardOffered"])
        dormant = be._live_row(sb.read_reg(Path(be.state_dir), SID), SID)
        self.assertIs(dormant["subagentStandard"], True)
        self.assertIn("standard speed", dormant["subagentSpeed"])

    def test_the_switch_is_offered_only_where_it_can_apply_or_is_on(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be, model="claude-fable-5-1"))
        self.assertFalse(s.snapshot()["subagentStandardOffered"])

    def test_the_kernel_routes_the_op_and_threads_the_status(self):
        kern = Path(os.path.dirname(HERE), "kernel", "kernel.py").read_text()
        for pin in ('"setSubagentStandard"', 't == "setSubagentStandard"', '"subagentSpeed": st.get("subagentSpeed"',
                    '"subagentSpeed": tm.get("subagentSpeed"'):
            self.assertTrue(pin in kern, "kernel.py lacks %s" % pin)


if __name__ == "__main__":
    unittest.main()
