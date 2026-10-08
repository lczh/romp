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
    # the version canary has cleared this (fake) Claude Code: its own tests are tests/test_subagent_standard_canary.py
    be.cli_version = lambda: "2.1.282"
    Path(d, sb.SUBAGENT_STANDARD_CANARY_STORE).write_text(json.dumps({"2.1.282": {"verdict": "pass"}}))
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
        # the borrowed catalog entry is claude-opus-4-7 (2026-10-08): no refusal fallback targets it, where claude-opus-5
        # is the fallback for some refusal categories and the override sent those requests back to Opus 5.5 (verified on
        # the wire: a request for claude-opus-5 under that override went out as claude-opus-5-5)
        self.assertEqual(sb.SUBAGENT_STANDARD_OVERRIDES, {"claude-opus-4-7": "claude-opus-5-5[1m]"})
        self.assertEqual(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, "claude-opus-4-7")
        env = sb.SUBAGENT_STANDARD_ENV
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "claude-opus-5-5[1m]")
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"], "1")
        self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "128000")
        caps = env["CLAUDE_CODE_MODEL_CAPABILITIES"]
        self.assertTrue(caps.startswith("claude-opus-4-7="), "the capability edit names the override's target, never Opus 5.5")
        got = set(caps.split("=", 1)[1].split(","))
        self.assertIn("-fast_mode", got)
        # Opus 5.5's own capabilities that the borrowed entry lacks are granted, so the subagents are handled as Opus 5.5
        for cap in ("rejects_disabled_thinking", "mid_conv_system", "mid_conv_tool_change", "per_turn_effort",
                    "per_turn_timing", "lean_prompt", "refusal_fallback", "opus_5_5_prompt_bundle"):
            self.assertIn(cap, got)
        for a, b in sb.SUBAGENT_STANDARD_ENV.items():
            self.assertNotIn("claude-opus-5=", b, "nothing names claude-opus-5 any more: %s" % a)

    def test_flag_settings_carry_model_overrides_only_when_asked(self):
        d = tempfile.mkdtemp()
        p = sb.flag_settings_path(d, SID, fast=True, env=dict(ENV, **sb.SUBAGENT_STANDARD_ENV),
                                  model_overrides=sb.SUBAGENT_STANDARD_OVERRIDES)
        got = json.loads(Path(p).read_text())
        self.assertEqual(got["modelOverrides"], {"claude-opus-4-7": "claude-opus-5-5[1m]"})
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


class ShellScrub(_OptionsBase):
    """The recipe's four variables are for the CLI alone: its shell commands (Bash, a `claude -p` it runs, a kernel it
    boots) get them unset through Claude Code's session environment script (CLAUDE_ENV_FILE), verified on 2.1.282."""

    def _script(self, got):
        p = got["env"]["CLAUDE_ENV_FILE"]
        self.assertTrue(os.path.isabs(p))
        return Path(p).read_text()

    def test_the_launch_points_claude_env_file_at_a_script_that_unsets_the_recipe(self):
        s = sb.SdkSession(self.be, _reg(self.be, subagentStandard=True, env=ENV))
        got = self._settings(s)
        text = self._script(got)
        for name in list(sb.SUBAGENT_STANDARD_ENV) + ["CLAUDE_ENV_FILE"]:
            self.assertIn(name, text.split("unset", 1)[1], name)
        self.assertNotIn("FEATURE_FLAG", text, "the session's own variables stay for its shells")

    def test_a_session_env_file_of_its_own_is_sourced_first(self):
        s = sb.SdkSession(self.be, _reg(self.be, subagentStandard=True, env={"CLAUDE_ENV_FILE": "/opt/team/env.sh"}))
        got = self._settings(s)
        text = self._script(got)
        self.assertNotEqual(got["env"]["CLAUDE_ENV_FILE"], "/opt/team/env.sh")
        self.assertLess(text.index("/opt/team/env.sh"), text.index("unset"), "the session's own script runs, then the unset")

    def test_off_leaves_no_env_file(self):
        s = sb.SdkSession(self.be, _reg(self.be, env=ENV))
        self.assertNotIn("CLAUDE_ENV_FILE", self._settings(s).get("env") or {})


class LaunchCarriesTheRecipe(_OptionsBase):
    def test_on_with_its_preconditions_writes_the_recipe_into_the_sessions_own_settings(self):
        s = sb.SdkSession(self.be, _reg(self.be, subagentStandard=True, env=ENV))
        got = self._settings(s)
        self.assertTrue(got["fastMode"])
        self.assertEqual(got["modelOverrides"], sb.SUBAGENT_STANDARD_OVERRIDES)
        want = dict(ENV, **sb.SUBAGENT_STANDARD_ENV)
        self.assertEqual({k: v for k, v in got["env"].items() if k != "CLAUDE_ENV_FILE"}, want,
                         "beside the session's own env, never instead of it")
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
    """A transcript stamp, to the millisecond as Claude Code writes it."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


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
        self.s.client = object()   # connected: _sub_std_begin runs at the connect, after the client is up
        self.t0 = time.time()

    def tearDown(self):
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before

    def _append(self, path, line):
        with open(path, "a") as f:
            f.write(line)


KEY = "claude-opus-5-5[1m]"   # the model string the CLI bills the forced subagent calls under (its modelUsage key)


def _result(key_cost=None, total=0.5):
    """A ResultMessage stand-in: the CLI's cumulative total and its per-model map, the recipe key's cumulative charge."""
    class _R:
        subtype = "success"
    r = _R()
    r.total_cost_usd = total
    mu = {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 10, "cacheReadInputTokens": 0,
                              "cacheCreationInputTokens": 0, "costUSD": 0.4}}
    if key_cost is not None:
        mu[KEY] = {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                   "costUSD": key_cost}
    r.model_usage = mu
    return r


def _usage(line):
    return json.loads(line)["message"]["usage"]


class SpeedCheck(_TranscriptBase):
    def _fast_off_without_reason(self):
        """What a CLI whose main model lost its fast-mode capability reports at a turn's start: off, no reason."""
        self.s.fast, self.s.fast_reason, self.s._fast_unlocked = "off", "", True

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
        time.sleep(0.01)
        self.s._sub_std_begin({"subagentStandard": True}, True)   # a relaunch is a new episode
        self._append(self.subdir / "agent-a3.jsonl", _rec("msg_s3", "claude-opus-5-5", "fast", _iso(time.time())))
        self.s._sub_std_settle()
        self.assertEqual(len(_problems(self.be, "fast")), 2)

    def test_a_main_loop_the_cli_reports_unable_to_run_fast_is_said(self):
        # the recipe's breakage of the main loop looks like this: fast_mode_state off with no reason (the main model's
        # capability lookup says no), while fast mode is wanted and the connection carries the opt-in (lab, 2.1.282)
        self.s._sub_std_begin({"subagentStandard": True}, True)
        self._fast_off_without_reason()
        self._append(self.main, _rec("msg_m1", "claude-opus-5-5", "standard", _iso(time.time())))
        self.s._sub_std_settle()
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["verdict"], "main-slow")
        self.assertEqual(len(_problems(self.be, "main loop")), 1)

    def test_a_standard_main_call_is_not_judged_where_the_cli_explains_it(self):
        # a cooldown, a refusal with its reason, the user's own Slow, a state of "on" (a standard call there is the
        # cooldown's fallback), a connection made without the opt-in: none of these is the recipe's breakage
        cases = [("on", "", True, True), ("cooldown", "", True, True), ("off", "preference", True, True),
                 ("off", "", False, True), ("off", "", True, False), ("", "", True, True)]
        for i, (state, reason, wanted, unlocked) in enumerate(cases):
            self.be._problems.clear()
            self.s._sub_std_begin({"subagentStandard": True}, True)
            self.s.fast, self.s.fast_reason, self.s._fast_unlocked = state, reason, unlocked
            self.s.fast_opt, self.s.fast_off = wanted, not wanted
            self._append(self.main, _rec("msg_x%d" % i, "claude-opus-5-5", "standard", _iso(time.time())))
            self.s._sub_std_settle()
            self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["verdict"], "ok",
                             "judged under %r" % ((state, reason, wanted, unlocked),))
            self.assertFalse(_problems(self.be, "main loop"))

    def test_a_partial_record_is_counted_when_its_final_record_lands(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        now = _iso(time.time())
        f = self.subdir / "agent-a1.jsonl"
        self._append(f, _rec("msg_s1", "claude-opus-5-5", None, now, stop=None, usage={"output_tokens": 7}))
        self.assertEqual(self.s._sub_std_settle(_result(0.0)), 0.0)
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subStandard"], 0)
        line = _rec("msg_s1", "claude-opus-5-5", "standard", now)
        self._append(f, line)
        charged = cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, _usage(line))
        self.assertLess(self.s._sub_std_settle(_result(charged)), 0.0)
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subStandard"], 1)
        self._append(f, line)   # the same call written again
        self.assertEqual(self.s._sub_std_settle(_result(charged)), 0.0, "a call is priced once")

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
        self.s._cli_ident = lambda: "4242:s1"
        self.s._sub_std_begin({"subagentStandard": True}, True)
        f = self.subdir / "agent-a1.jsonl"
        l1 = _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time()))
        self._append(f, l1)
        c1 = cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, _usage(l1))
        first = self.s._sub_std_settle(_result(c1))
        self.assertLess(first, 0.0)
        time.sleep(0.01)
        self._append(f, _rec("msg_s2", "claude-opus-5-5", "standard", _iso(time.time())))   # while no kernel watched
        s2 = sb.SdkSession(self.be, sb.read_reg(Path(self.be.state_dir), SID))       # the next kernel's object
        s2._cli_ident = lambda: "4242:s1"
        s2._sub_std_begin({"subagentStandard": True}, False)                          # an attach to the same CLI
        self.assertAlmostEqual(s2._sub_std_settle(_result(2 * c1), ), first, places=9,
                               msg="msg_s2 alone: msg_s1 was repriced before")
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subStandard"], 2)


class StaleScan(_TranscriptBase):
    """A fresh launch starts a fresh episode in the registry too, and an attach resumes only its own CLI's."""

    def test_an_attach_after_a_relaunch_resumes_the_new_launch_not_the_old_one(self):
        s = self.s
        s._cli_ident = lambda: "100:a"
        s._sub_std_begin({"subagentStandard": True}, True)                      # launch A, with the recipe
        s.fast, s.fast_reason, s._fast_unlocked = "off", "", True
        self._append(self.main, _rec("msg_a_m1", "claude-opus-5-5", "standard", _iso(time.time())))
        s._sub_std_settle()
        self.assertEqual(len(_problems(self.be, "main loop")), 1)
        time.sleep(0.01)
        s._cli_ident = lambda: "200:b"
        s._sub_std_begin({"subagentStandard": False}, True)                     # launch B, without it
        self.assertIsNone(sb.read_reg(Path(self.be.state_dir), SID).get("subagentSpeedScan"),
                          "a launch without the recipe leaves no episode to resume")
        self._append(self.subdir / "agent-b1.jsonl", _rec("msg_b_s1", "claude-opus-5-5", "fast", _iso(time.time())))
        time.sleep(0.01)
        s._cli_ident = lambda: "300:c"
        s._sub_std_begin({"subagentStandard": True}, True)                      # launch C, with it again
        scan = sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeedScan"]
        self.assertEqual((scan["cli"], scan["said"], scan["counts"]["mainStandard"]), ("300:c", [], 0))
        s2 = sb.SdkSession(self.be, sb.read_reg(Path(self.be.state_dir), SID))   # a kernel restart, an attach to C
        s2._cli_ident = lambda: "300:c"
        s2._sub_std_begin({"subagentStandard": True}, False)
        self.assertEqual(s2._sub_std_said, set())
        s2._sub_std_settle()
        self.assertFalse(_problems(self.be, "subagent call ran in fast"), "B's fast call was B's, launched without it")
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeed"]["subFast"], 0)

    def test_an_attach_to_another_cli_starts_a_fresh_episode(self):
        self.s._cli_ident = lambda: "100:a"
        self.s._sub_std_begin({"subagentStandard": True}, True)
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time())))
        self.s._sub_std_settle()
        s2 = sb.SdkSession(self.be, sb.read_reg(Path(self.be.state_dir), SID))
        s2._cli_ident = lambda: "999:z"
        s2._sub_std_begin({"subagentStandard": True}, False)
        self.assertEqual(s2._sub_std_counts["subStandard"], 0, "another CLI's tallies are not this one's")
        self.assertEqual(sb.read_reg(Path(self.be.state_dir), SID)["subagentSpeedScan"]["cli"], "999:z")


class Repricing(_TranscriptBase):
    def test_the_correction_is_the_real_cost_less_what_the_cli_charged(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        now = _iso(time.time())
        one_h = {"cache_creation": {"ephemeral_5m_input_tokens": 500, "ephemeral_1h_input_tokens": 1500}}
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s1", "claude-opus-5-5", "standard", now))
        self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s2", "claude-opus-5-5", "standard", now, usage=one_h))
        recs = [_usage(l) for l in (self.subdir / "agent-a1.jsonl").read_text().splitlines()]
        charged = sum(cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, u) for u in recs)
        want = sum(cpr.call_cost("claude-opus-5-5", u) for u in recs) - charged
        self.assertAlmostEqual(self.s._sub_std_settle(_result(charged)), want, places=9)
        # spot check of the arithmetic: input/output/cache writes at 0.8 of Opus 5's, cache reads at 0.4
        u = recs[0]
        self.assertAlmostEqual(cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, u), (1000 * 5 + 500 * 25 + 2000 * 6.25 + 10000 * 0.5) / 1e6)
        self.assertAlmostEqual(cpr.call_cost("claude-opus-5-5", u), (1000 * 4 + 500 * 20 + 2000 * 5 + 10000 * 0.2) / 1e6)

    def test_a_cli_that_already_charged_the_real_rate_is_not_corrected_and_says_so_once(self):
        # a server-side price for the key (or any other cause) that bills these calls at Opus 5.5's own rates: romp
        # subtracting the Opus 5 premium again would under-record them
        self.s._sub_std_begin({"subagentStandard": True}, True)
        cum = 0.0
        for i in range(3):
            line = _rec("msg_s%d" % i, "claude-opus-5-5", "standard", _iso(time.time()))
            self._append(self.subdir / "agent-a1.jsonl", line)
            cum += cpr.call_cost("claude-opus-5-5", _usage(line))
            self.assertEqual(self.s._sub_std_settle(_result(cum)), 0.0)
        said = _problems(self.be, "charged")
        self.assertEqual(len(said), 1, said)

    def test_a_call_the_result_has_not_paid_for_yet_waits_for_the_result_that_has(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        t = time.time()
        a = _rec("msg_s1", "claude-opus-5-5", "standard", _iso(t))
        b = _rec("msg_s2", "claude-opus-5-5", "standard", _iso(t + 60))   # finished after this result was emitted
        self._append(self.subdir / "agent-a1.jsonl", a)
        self._append(self.subdir / "agent-a2.jsonl", b)
        ca, cb = (cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, _usage(x)) for x in (a, b))
        ra, rb = (cpr.call_cost("claude-opus-5-5", _usage(x)) for x in (a, b))
        self.assertAlmostEqual(self.s._sub_std_settle(_result(ca), now=t + 5), ra - ca, places=9)
        self.assertAlmostEqual(self.s._sub_std_settle(_result(ca + cb), now=t + 65), rb - cb, places=9)
        self.assertFalse(_problems(self.be, "charged"))

    def test_a_result_without_the_clis_per_model_charge_corrects_nothing_and_says_so_once(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        for i in range(2):
            self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s%d" % i, "claude-opus-5-5", "standard", _iso(time.time())))
            r = _result(None)
            del r.model_usage   # an SDK that drops the map (sdk_backend.usage_fallback_is_sdk)
            self.assertEqual(self.s._sub_std_settle(r), 0.0)
        self.assertEqual(len(_problems(self.be, "per-model")), 1)

    def test_a_map_without_the_recipes_key_is_a_disagreement_said_once(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        for i in range(3):
            self._append(self.subdir / "agent-a1.jsonl", _rec("msg_s%d" % i, "claude-opus-5-5", "standard", _iso(time.time())))
            self.assertEqual(self.s._sub_std_settle(_result(None)), 0.0)
        self.assertEqual(len(_problems(self.be, "charged")), 1)

    def _live_calls(self, n, start=0):
        """n new standard subagent calls, each followed by its live result: (cumulative charge, corrections)."""
        f = self.subdir / "agent-a1.jsonl"
        corrs = []
        for i in range(start, start + n):
            line = _rec("msg_s%d" % i, "claude-opus-5-5", "standard", _iso(time.time()))
            self._append(f, line)
            self._cum += cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, _usage(line))
            corrs.append((self.s._sub_std_settle(_result(self._cum)),
                          cpr.call_cost("claude-opus-5-5", _usage(line)) - cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, _usage(line))))
        return corrs

    def test_a_replayed_result_never_moves_the_charge_mark(self):
        # after a kernel restart the host's replay hands over results the ledger already holds (the spend fold's
        # `duplicate`), an older one included: its charge is below the mark, and moving the mark there made the next live
        # result's charge span calls already paired (a false disagreement row, one result left at Opus 5 rates)
        self._cum = 0.0
        self.s._sub_std_begin({"subagentStandard": True}, True)
        self._live_calls(2)
        mark = self.s._sub_std_charged
        self.assertEqual(self.s._sub_std_settle(_result(mark / 2), replayed=True), 0.0)   # the first result, replayed
        self.assertEqual(self.s._sub_std_charged, mark, "a replayed result leaves the mark where the live ones put it")
        for got, want in self._live_calls(2, start=2):
            self.assertAlmostEqual(got, want, places=9)
        self.assertFalse(_problems(self.be, "charged"))

    def test_an_unknown_baseline_moves_the_mark_and_corrects_nothing(self):
        # an attach with no watermark for the CLI: the spend fold records nothing for the span, so nothing is repriced;
        # the mark starts there, and the next live result corrects its own calls only
        self._cum = 0.0
        self.s._sub_std_begin({"subagentStandard": True}, True)
        f = self.subdir / "agent-a1.jsonl"
        line = _rec("msg_u0", "claude-opus-5-5", "standard", _iso(time.time()))
        self._append(f, line)
        self._cum += cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, _usage(line)) + 1.0   # plus history the kernel never saw
        self.assertEqual(self.s._sub_std_settle(_result(self._cum), unknown=True), 0.0)
        self.assertEqual(self.s._sub_std_charged, self._cum)
        for got, want in self._live_calls(1, start=1):
            self.assertAlmostEqual(got, want, places=9)
        self.assertFalse(_problems(self.be, "charged"))

    def test_the_settle_hands_the_spend_folds_replay_verdict_to_the_repricing(self):
        src = Path(os.path.dirname(HERE), "kernel", "sdk_backend.py").read_text()
        self.assertTrue("corr = self._sub_std_settle(msg, replayed=duplicate and not unknown, unknown=unknown)" in src,
                        "the settle passes the spend fold's duplicate/unknown verdict on")

    def test_the_expected_charge_follows_the_canonical_model_the_cli_reports(self):
        # the result's modelUsage names the catalog entry Claude Code priced the key's calls as (canonicalModel): a CLI
        # launched with an earlier recipe priced them as claude-opus-5, and one whose override did not take as Opus 5.5
        # itself, at its real rates, which needs no correction and is no disagreement
        self.s._sub_std_begin({"subagentStandard": True}, True)
        line = _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time()))
        self._append(self.subdir / "agent-a1.jsonl", line)
        real = cpr.call_cost("claude-opus-5-5", _usage(line))
        r = _result(real)
        r.model_usage[KEY]["canonicalModel"] = "claude-opus-5-5"
        self.assertEqual(self.s._sub_std_settle(r), 0.0)
        line2 = _rec("msg_s2", "claude-opus-5-5", "standard", _iso(time.time()))
        self._append(self.subdir / "agent-a1.jsonl", line2)
        charged = cpr.call_cost("claude-opus-5", _usage(line2))
        r2 = _result(real + charged)
        r2.model_usage[KEY]["canonicalModel"] = "claude-opus-5"
        self.assertAlmostEqual(self.s._sub_std_settle(r2), cpr.call_cost("claude-opus-5-5", _usage(line2)) - charged, places=9)
        self.assertFalse(_problems(self.be, "charged"))

    def test_a_fast_subagent_call_is_charged_as_opus_5_5_fast_and_left_alone(self):
        self.s._sub_std_begin({"subagentStandard": True}, True)
        line = _rec("msg_s1", "claude-opus-5-5", "fast", _iso(time.time()))
        self._append(self.subdir / "agent-a1.jsonl", line)
        self.assertAlmostEqual(self.s._sub_std_settle(_result(cpr.call_cost("claude-opus-5-5", _usage(line)))), 0.0, places=9)

    def test_the_turn_records_the_repriced_dollars_and_keeps_the_clis_figure_on_the_row(self):
        s = self.s

        async def _noop():
            pass
        s._do_refresh_context = _noop
        s._do_refresh_usage = _noop
        s._seed_spend_watermarks()
        s._sub_std_begin({"subagentStandard": True}, True)
        line = _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time()))
        self._append(self.subdir / "agent-a1.jsonl", line)
        u = _usage(line)
        charged = cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, u)
        corr = cpr.call_cost("claude-opus-5-5", u) - charged
        r = _result(charged)

        async def go():
            s._on_message(r, type("A", (), {}), type(r), type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(go())
        day = json.loads(Path(self.be.state_dir, "spend.json").read_text())["days"][time.strftime("%Y-%m-%d")]
        self.assertAlmostEqual(day["usd"], 0.5 + corr, places=6)
        row = [json.loads(l) for l in Path(self.be.state_dir, "turns.jsonl").read_text().splitlines()][-1]
        self.assertAlmostEqual(row["usd"], 0.5 + corr, places=6)
        self.assertAlmostEqual(row["cliUsd"], 0.5, places=6)
        self.assertAlmostEqual(row["subagentRepriceUsd"], corr, places=6)
        self.assertAlmostEqual(row["cumulativeUsd"], 0.5, places=6, msg="the CLI's cumulative stays the CLI's own")

    def test_the_charge_watermark_follows_the_cost_state_a_resumed_cli_restores(self):
        # a resumed CLI restores its modelUsage from the transcript's cost-state record: the key's charge there is
        # history, not this launch's
        with open(self.main, "a") as f:
            f.write(json.dumps({"type": "cost-state", "totalCostUSD": 3.0,
                                "modelUsage": {KEY: {"inputTokens": 1, "outputTokens": 1, "costUSD": 2.0}}}) + "\n")
        self.s.resume_sid = SID
        self.s._seed_spend_watermarks()
        self.s._sub_std_begin({"subagentStandard": True}, True)
        line = _rec("msg_s1", "claude-opus-5-5", "standard", _iso(time.time()))
        self._append(self.subdir / "agent-a1.jsonl", line)
        u = _usage(line)
        got = self.s._sub_std_settle(_result(2.0 + cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, u)))
        self.assertAlmostEqual(got, cpr.call_cost("claude-opus-5-5", u) - cpr.call_cost(sb.SUBAGENT_STANDARD_CLI_PRICED_AS, u), places=9)


class LiveChanges(unittest.TestCase):
    """A live change that ends the session's qualification relaunches it without the recipe once quiet."""

    def setUp(self):
        self.be = _backend()
        self.s = _live(self.be, subagentStandard=True)
        self.s.set_model_live = lambda *a, **k: None
        self.s.client = object()   # connected
        self.s._sub_std_begin(dict(self.s.launch_values()), True)
        self.assertTrue(self.s._sub_std_launched)

    def test_a_live_model_pick_that_breaks_it_relaunches_once_quiet_and_the_row_says_it_is_still_active(self):
        for model in ("claude-opus-5-5[1m]",):
            self.assertTrue(self.be.set_model(SID, model))
            self.assertIn("subagent speed", self.s.asks)
            self.assertIn("subagentStandard", sb.read_reg(Path(self.be.state_dir), SID)["relaunchWait"]["picks"])
            snap = self.s.snapshot()
            self.assertTrue(snap["subagentSpeed"].startswith("still active until the relaunch"), snap["subagentSpeed"])
            self.assertTrue(snap["fastPending"], "the fast badge says a change waits for its relaunch")
            self.assertTrue(snap["subagentStandardOffered"])

    def test_a_live_fast_off_relaunches_once_quiet(self):
        self.s._fast_unlocked = True
        self.be.send = lambda *a, **k: True
        self.assertTrue(self.be.set_fast(SID, "off"))
        self.assertIn("subagent speed", self.s.asks)

    def test_a_pick_that_keeps_it_qualifying_asks_nothing(self):
        self.assertTrue(self.be.set_model(SID, "claude-opus-5-5"))
        self.assertNotIn("subagent speed", self.s.asks)
        self.assertFalse(self.s.snapshot()["fastPending"])

    def test_turning_it_off_says_still_active_until_the_relaunch(self):
        self.assertTrue(self.be.set_subagent_standard(SID, "off"))
        self.assertEqual(self.s.snapshot()["subagentSpeed"], "still active until the relaunch")


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

    def test_a_session_set_to_slow_is_not_offered_it_unless_it_is_on(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be, fast=False, fastOff=True))
        self.assertFalse(s.snapshot()["subagentStandardOffered"], "Slow: the switch would buy nothing")
        self.assertFalse(be._live_row(sb.read_reg(Path(be.state_dir), SID), SID)["subagentStandardOffered"])
        s = sb.SdkSession(be, _reg(be, fast=False, fastOff=True, subagentStandard=True))
        self.assertTrue(s.snapshot()["subagentStandardOffered"], "on: offered, so it can be turned off")
        self.assertIn("fast mode", s.snapshot()["subagentSpeed"])

    def test_the_kernel_routes_the_op_and_threads_the_status(self):
        kern = Path(os.path.dirname(HERE), "kernel", "kernel.py").read_text()
        for pin in ('"setSubagentStandard"', 't == "setSubagentStandard"', '"subagentSpeed": st.get("subagentSpeed"',
                    '"subagentSpeed": tm.get("subagentSpeed"'):
            self.assertTrue(pin in kern, "kernel.py lacks %s" % pin)


if __name__ == "__main__":
    unittest.main()
