#!/usr/bin/env python3
"""Which models run Claude Code's fast mode, learned from the CLI (2026-11-11). Four consumers — the judges' per-tier
gate (jd.fast_capable / _tier_fast), the Always fast switch (sdk_backend.fast_capable / fast_effective), the chat's
fast badge (the status's fastCapable) and the pickers (the /models payload's per-version and per-family `fast`) —
carried the rule "the id names Opus" while fast mode was an Opus-only research preview. Claude Code 2.1.321 runs it
on Fable 5.2 and no longer on Opus 4.8, so the rule hid a live toggle and offered a dead one. The CLI's connect-time
initialize answer (the SDK's get_server_info) lists every model option with supportsFastMode; the backend writes a
kernel-wide table from it (model_fast.py, STATE/model-fast.json, keyed by alias AND resolved id), and a per-turn init
reporting fast mode ON names the model it runs on, which is proof. Pinned here: the table's resolution and
write-on-change; the backend's two write events and its reader; the judge's reader, the seed where nothing is known,
and the tier gate following it; the /models payload's capability fields; the status's fastCapable and its event in
the view signature. Hermetic state; synthetic sids, hostnames and payloads; no CLI, no network."""
import asyncio
import http.client
import inspect
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_fastcap", os.path.join(BIN, "romp-kernel"))
jd = km.jd
mf = jd._mf                                   # the shared table module, as the judges loaded it
sb = load_source("romp_sdk_backend_fastcap", os.path.join(BIN, "romp_sdk_backend.py"))
km.jd.STATE.mkdir(parents=True, exist_ok=True)
(km.jd.STATE / "session-hosts").write_text("off")   # this root is outside conftest's belt (repo rule, 2026-09-11)

SID = "11111111-2222-4333-8444-000000000711"


def _server_info(**over):
    """A synthetic initialize answer in the CLI's shape (field names as 2.1.321's control_response carries them:
    `models` options with value / resolvedModel / displayName / supportsFastMode, beside fast_mode_state and the
    responder's own claude_code_version, absent on a CLI that predates the field). The
    world it describes: the default and Fable run fast, Opus 5.5 does, Opus 4.8 is listed without the claim
    (retired), Sonnet carries an explicit None."""
    d = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required", "claude_code_version": "2.1.321",
         "models": [
             {"value": "default", "resolvedModel": "claude-fable-5-2", "displayName": "Default (Fable 5.2)",
              "supportsFastMode": True, "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"]},
             {"value": "fable", "resolvedModel": "claude-fable-5-2", "displayName": "Fable 5.2", "supportsFastMode": True},
             {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5", "supportsFastMode": True},
             {"value": "claude-opus-4-8", "resolvedModel": "claude-opus-4-8", "displayName": "Opus 4.8"},
             {"value": "sonnet", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet 5", "supportsFastMode": None},
             "not an option", {"value": "", "resolvedModel": None}]}
    d.update(over)
    return d


def _server_info_without_the_field(**over):
    """An initialize answer from a Claude Code that predates supportsFastMode: the options carry value, resolvedModel
    and displayName and not one of them the field (the CLI emits it only when true, so this is also what an account
    none of whose models runs fast would send)."""
    d = {"fast_mode_state": "off", "fast_mode_disabled_reason": "",
         "models": [{"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (Opus 5.5)"},
                    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5", "supportsEffort": True},
                    {"value": "fable", "resolvedModel": "claude-fable-5-1", "displayName": "Fable 5.1"}]}
    d.update(over)
    return d


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    be._pokes = []
    be._poke = lambda *a, **k: be._pokes.append(1)
    return be


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp"}
    r.update(reg)
    sb.write_reg(be.state_dir, r["sid"], dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    be.sessions[r["sid"]] = s
    return s


def _table(root):
    return json.loads((Path(root) / mf.MODEL_FAST_FILE).read_text())


async def _init_on(s, sys_cls, data):
    """Feed session `s` one init system message (the shape _on_message reads) and let the loop settle."""
    s._on_message(sys_cls(data), type("A", (), {}), type("R", (), {}), sys_cls)
    await asyncio.sleep(0)


class TheTable(unittest.TestCase):
    """model_fast.py: the keys a spelling resolves to, the write-on-change, the reader's three answers."""

    def test_the_keys_a_spelling_is_looked_up_under(self):
        self.assertEqual(mf.keys_for("fable"), ["fable"], "a family alias is its own key, never a version's")
        self.assertEqual(mf.keys_for("default"), ["default"])
        self.assertEqual(mf.keys_for("claude-fable-5-2"), ["claude-fable-5-2"])
        self.assertEqual(mf.keys_for("claude-opus-4-6[1m]"), ["claude-opus-4-6[1m]", "claude-opus-4-6"], "the tag stripped second")
        self.assertEqual(mf.keys_for("claude-opus-4-5-20251101"), ["claude-opus-4-5-20251101", "claude-opus-4-5"],
                         "a dated snapshot falls to its dateless alias")
        self.assertEqual(mf.keys_for("Fable 5.2"), ["fable 5.2", "claude-fable-5-2"], "the badge's pretty name to its id")
        self.assertEqual(mf.keys_for("Opus 5"), ["opus 5", "claude-opus-5"])
        self.assertIn("claude-opus-4-6", mf.keys_for("us.anthropic.claude-opus-4-6-v1:0"), "a provider prefix is dropped")
        self.assertEqual(mf.keys_for(""), [])
        self.assertEqual(mf.keys_for(None), [])
        for k in mf.keys_for("claude-opus-4-8"):
            self.assertNotEqual(k, "opus", "a version id never reads its family's entry: the alias is the family's HEAD")

    def test_write_on_change_and_the_readers_three_answers(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(mf.fast_known("opus", d), "no file: nothing is known")
        self.assertEqual(mf.read_table(d), {})
        self.assertTrue(mf.note(d, {"fable": True, "claude-fable-5-2": True, "claude-opus-4-8": False, "default": True}, cli="2.1.321"))
        t = _table(d)
        self.assertEqual({k: v["fast"] for k, v in t.items()},
                         {"fable": True, "claude-fable-5-2": True, "claude-opus-4-8": False, "default": True})
        self.assertEqual(t["fable"]["cli"], "2.1.321")
        self.assertIsInstance(t["fable"]["t"], int)
        mt = os.stat(Path(d) / mf.MODEL_FAST_FILE).st_mtime_ns
        self.assertFalse(mf.note(d, {"fable": True, "claude-opus-4-8": False}, cli="2.1.321"), "nothing new: no change")
        self.assertEqual(os.stat(Path(d) / mf.MODEL_FAST_FILE).st_mtime_ns, mt, "…and the same version's confirmation touches no file")
        self.assertFalse(mf.note(d, {"fable": True}, cli="2.1.320"), "an older CLI's confirmation: no change")
        self.assertEqual(os.stat(Path(d) / mf.MODEL_FAST_FILE).st_mtime_ns, mt, "…and no write")
        self.assertEqual(_table(d)["fable"]["cli"], "2.1.321", "…the stamp stands")
        self.assertFalse(mf.note(d, {"fable": True, "claude-opus-4-8": False}, cli="2.1.322"),
                         "a NEWER CLI confirming an answer is no change either (no frame)…")
        self.assertEqual(_table(d)["fable"]["cli"], "2.1.322", "…but its stamp advances: the confirmation counts against older binaries")
        self.assertEqual(_table(d)["claude-opus-4-8"]["cli"], "2.1.322")
        self.assertEqual(_table(d)["default"]["cli"], "2.1.321", "an id the writer did not name keeps its stamp")
        stamp = _table(d)["fable"]["t"]
        self.assertTrue(mf.note(d, {"claude-opus-4-8": True}, cli="2.1.330"), "a flipped answer is a change")
        self.assertTrue(_table(d)["claude-opus-4-8"]["fast"])
        self.assertEqual(_table(d)["claude-opus-4-8"]["cli"], "2.1.330")
        self.assertEqual(_table(d)["fable"]["t"], stamp, "the others' stamps stand")
        self.assertFalse(mf.note(d, {}, cli="x"), "nothing to say writes nothing")
        self.assertFalse(mf.note(None, {"opus": True}), "no state dir (a bare test double): no raise, no write")
        # the reader
        self.assertTrue(mf.fast_known("fable", d))
        self.assertTrue(mf.fast_known("Fable 5.2", d), "the badge's spelling")
        self.assertTrue(mf.fast_known("claude-fable-5-2[1m]", d), "the tagged spelling")
        self.assertTrue(mf.fast_known("claude-fable-5-2-20261101", d), "a dated snapshot by its alias")
        self.assertTrue(mf.fast_known("default", d))
        self.assertIsNone(mf.fast_known("claude-fable-5-1", d), "an id no report named: unknown, the caller's seed applies")
        self.assertIsNone(mf.fast_known("opus", d), "a family alias without an entry does not borrow a version's")
        self.assertIsNone(mf.fast_known("", d))
        self.assertGreater(mf.rev(d), 0)
        self.assertEqual(mf.rev(tempfile.mkdtemp()), 0)

    def test_the_newest_clis_word_stands_whichever_connects_last(self):
        # the 2.1.320 → 2.1.321 line: Fable 5.2 gained fast mode, Opus 4.8 lost it. Two sessions on one kernel, one
        # binary each (a session host keeps its binary across an update), connecting in either order.
        older, newer = {"fable": False, "claude-opus-4-8": True}, {"fable": True, "claude-opus-4-8": False}
        self.assertEqual(mf.version_tuple("2.1.321"), (2, 1, 321))
        self.assertEqual(mf.version_tuple("2.1.321 (Claude Code)"), (2, 1, 321))
        self.assertIsNone(mf.version_tuple(""))
        self.assertIsNone(mf.version_tuple("latest"))
        self.assertIsNone(mf.version_tuple(None))
        for first, second, label in ((("2.1.320", older), ("2.1.321", newer), "old then new"),
                                     (("2.1.321", newer), ("2.1.320", older), "new then old")):
            d = tempfile.mkdtemp()
            self.assertTrue(mf.note(d, first[1], cli=first[0]), label)
            self.assertEqual(mf.note(d, second[1], cli=second[0]), second[0] == "2.1.321",
                             "%s: the newer word is the change, the older one is refused" % label)
            self.assertEqual({k: v["fast"] for k, v in _table(d).items()}, newer, "%s: 2.1.321's answers stand" % label)
            self.assertEqual({v["cli"] for v in _table(d).values()}, {"2.1.321"}, label)
            self.assertTrue(mf.fast_known("fable", d), label)
            self.assertFalse(mf.fast_known("claude-opus-4-8", d), label)
            # the older binary connects again and again (every turn's relaunch): never a write
            mt = os.stat(Path(d) / mf.MODEL_FAST_FILE).st_mtime_ns
            self.assertFalse(mf.note(d, older, cli="2.1.320"))
            self.assertEqual(os.stat(Path(d) / mf.MODEL_FAST_FILE).st_mtime_ns, mt, "%s: no flap, no file touched" % label)
        # the same version disagreeing with itself (another login's model list): last word wins within a version
        self.assertTrue(mf.note(d, {"fable": False}, cli="2.1.321"))
        self.assertFalse(mf.fast_known("fable", d))
        # a writer of unknown version: new ids yes, a stamped row's answer never, an unstamped row's yes
        self.assertTrue(mf.note(d, {"sonnet": False, "fable": True}, cli=""), "the new id is written")
        self.assertEqual(set(_table(d)), {"fable", "claude-opus-4-8", "sonnet"})
        self.assertFalse(mf.fast_known("fable", d), "…and the stamped row's answer stands against an unknown writer")
        self.assertEqual(_table(d)["sonnet"]["cli"], "")
        self.assertTrue(mf.note(d, {"sonnet": True}, cli=""), "an unstamped row takes an unknown writer's flip")
        self.assertTrue(mf.note(d, {"sonnet": False}, cli="2.1.300"), "…and any versioned one's")
        self.assertEqual(_table(d)["sonnet"]["cli"], "2.1.300")
        self.assertFalse(mf.note(d, {"sonnet": False}, cli=""), "an unknown writer confirming: nothing")
        self.assertEqual(_table(d)["sonnet"]["cli"], "2.1.300", "…the stamp is not blanked")
        # a hand edit that stamps a row with no version is unstamped, not newest
        (Path(d) / mf.MODEL_FAST_FILE).write_text(json.dumps({"opus": {"fast": True, "t": 1, "cli": "latest"}}))
        self.assertTrue(mf.note(d, {"opus": False}, cli="2.1.300"))

    def test_a_garbled_file_reads_as_nothing_known(self):
        d = tempfile.mkdtemp()
        (Path(d) / mf.MODEL_FAST_FILE).write_text("{not json")
        self.assertEqual(mf.read_table(d), {})
        self.assertIsNone(mf.fast_known("opus", d))
        (Path(d) / mf.MODEL_FAST_FILE).write_text(json.dumps({"opus": {"fast": "yes"}, "fable": {"fast": True, "t": 1, "cli": ""}, "x": 3}))
        self.assertEqual(set(mf.read_table(d)), {"fable"}, "a row whose answer is not a bool is dropped, not read as one")

    def test_support_from_a_payload_keys_alias_and_resolved_id(self):
        s = mf.support_from_payload(_server_info())
        self.assertEqual(s, {"default": True, "claude-fable-5-2": True, "fable": True, "opus": True, "claude-opus-5-5": True,
                             "claude-opus-4-8": False, "sonnet": False, "claude-sonnet-5": False})
        self.assertEqual(mf.support_from_payload({"fast_mode_state": "on"}), {}, "a payload without the list says nothing")
        self.assertEqual(mf.support_from_payload(None), {})

    def test_a_list_no_option_of_which_carries_the_field_teaches_nothing(self):
        # the CLI emits supportsFastMode only when true: a list without it on ANY option is an older CLI as readily as
        # an account whose models all lack fast mode, so it says nothing — never "no" for every id, Opus included
        self.assertEqual(mf.support_from_payload(_server_info_without_the_field()), {})
        self.assertEqual(mf.support_from_payload({"models": [{"value": "opus", "resolvedModel": "claude-opus-5-5"}, "junk"]}), {})
        # …while one option carrying it, even false or None, makes the silence of the others a "no"
        self.assertEqual(mf.support_from_payload({"models": [{"value": "opus", "supportsFastMode": False}, {"value": "fable"}]}),
                         {"opus": False, "fable": False})
        self.assertEqual(mf.support_from_payload({"models": [{"value": "opus", "supportsFastMode": None}, {"value": "fable"}]}),
                         {"opus": False, "fable": False})


class TheBackendWrites(unittest.TestCase):
    """The two write events — the connect-time initialize answer and a per-turn init running fast — and the switch's reader."""

    def setUp(self):
        self._hook = getattr(sb.SdkBackend, "on_model_fast_changed", None)   # the class-level hook a test wires is undone after

    def tearDown(self):
        if self._hook is None:
            try:
                del sb.SdkBackend.on_model_fast_changed
            except AttributeError:
                pass
        else:
            sb.SdkBackend.on_model_fast_changed = self._hook

    def test_the_connect_time_answer_fills_the_table_and_tells_the_pickers(self):
        be = _backend()
        s = _sess(be, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
        frames = []
        sb.SdkBackend.on_model_fast_changed = staticmethod(lambda: frames.append(1))
        s._cli_version_seen = "2.1.300"       # a PREVIOUS connection's init: not this CLI's version

        class _Client:
            async def get_server_info(self):
                return _server_info()
        s.client = _Client()
        asyncio.run(s._do_adopt_server_info())
        t = _table(be.state_dir)
        self.assertEqual({k: v["fast"] for k, v in t.items()},
                         {"default": True, "claude-fable-5-2": True, "fable": True, "opus": True, "claude-opus-5-5": True,
                          "claude-opus-4-8": False, "sonnet": False, "claude-sonnet-5": False},
                         "every option by alias AND resolved id; true claims true, absent or None false")
        self.assertEqual(t["fable"]["cli"], "2.1.321", "stamped with the version the answer itself names")
        self.assertEqual(s._cli_version_seen, "2.1.321", "…which this session now remembers")
        self.assertIsNone(s._fast_support_connect, "the answer named its version: nothing for the init to re-say")
        self.assertEqual(frames, [1], "the change sends the models frame once")
        self.assertTrue(be._pokes, "…and a status push, so the badge follows")
        self.assertTrue(any("fast support (web)" in m and "claude-fable-5-2" in m for m in be._logs), be._logs)
        self.assertEqual(s.fast, "off", "the fast state is adopted beside it, as before")
        # a second identical answer changes nothing: no frame, no write
        frames.clear(); be._pokes.clear()
        asyncio.run(s._do_adopt_server_info())
        self.assertEqual(frames, [], "write-on-change: an unchanged answer sends no frame")
        # the reader every consumer in this module shares
        self.assertTrue(sb.fast_capable("claude-fable-5-2", be.state_dir), "the CLI's word")
        self.assertTrue(sb.fast_capable("Fable 5.2", be.state_dir))
        self.assertTrue(sb.fast_capable("fable", be.state_dir))
        self.assertFalse(sb.fast_capable("claude-opus-4-8", be.state_dir), "listed without the claim: a dead toggle is not offered")
        self.assertTrue(sb.fast_capable("claude-opus-4-8"), "no state dir: the seed, as before")
        self.assertTrue(sb.fast_capable("claude-opus-4-7", be.state_dir), "an id no report named: the Opus seed")
        self.assertFalse(sb.fast_capable("claude-fable-5-1", be.state_dir), "…and a non-Opus stranger stays out")

    def test_an_answer_without_a_model_list_writes_nothing(self):
        be = _backend()
        s = _sess(be)

        class _Client:
            async def get_server_info(self):
                return {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}
        s.client = _Client()
        asyncio.run(s._do_adopt_server_info())
        self.assertFalse((Path(be.state_dir) / mf.MODEL_FAST_FILE).exists(), "an older CLI's answer teaches nothing")
        self.assertEqual(s.fast, "off", "…and the fast state is still adopted")

    def test_two_sessions_on_different_cli_versions_the_newest_word_stands(self):
        # 2.1.321 gained fast mode on Fable 5.2 and lost it on Opus 4.8; a hosted session still on 2.1.320 connects
        # after (and before) one on 2.1.321. Neither order flaps the table, the frame or the badge.
        newer = _server_info()
        older = _server_info(claude_code_version="2.1.320", models=[
            {"value": "fable", "resolvedModel": "claude-fable-5-2", "displayName": "Fable 5.2"},
            {"value": "claude-opus-4-8", "resolvedModel": "claude-opus-4-8", "displayName": "Opus 4.8", "supportsFastMode": True}])

        def connect(s, info):
            class _Client:
                async def get_server_info(self):
                    return info
            s.client = _Client()
            asyncio.run(s._do_adopt_server_info())
        for order in ("new then old", "old then new"):
            be = _backend()
            frames = []
            sb.SdkBackend.on_model_fast_changed = staticmethod(lambda: frames.append(1))
            a = _sess(be, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
            b = _sess(be, sid="11111111-2222-4333-8444-000000000722", name="api", liveModel="Opus 4.8", liveModelId="claude-opus-4-8")
            first, second = ((a, newer), (b, older)) if order == "new then old" else ((b, older), (a, newer))
            connect(*first)
            self.assertEqual(frames, [1], order)
            connect(*second)
            self.assertEqual(frames, [1, 1] if order == "old then new" else [1],
                             "%s: the newer word is the one frame more; the older binary's connect sends none" % order)
            be._pokes.clear()
            connect(b, older)
            connect(b, older)
            self.assertEqual(frames, [1, 1] if order == "old then new" else [1], "%s: the old binary reconnecting: no frame" % order)
            self.assertTrue(sb.fast_capable("fable", be.state_dir), order)
            self.assertTrue(sb.fast_capable("claude-fable-5-2", be.state_dir), order)
            self.assertFalse(sb.fast_capable("claude-opus-4-8", be.state_dir), "%s: 2.1.321's word stands" % order)
            self.assertEqual(_table(be.state_dir)["fable"]["cli"], "2.1.321", order)

    def test_the_init_re_says_a_connect_time_answer_whose_cli_named_no_version(self):
        be = _backend()
        mf.note(be.state_dir, {"fable": True, "claude-fable-5-2": True}, cli="2.1.321")   # another session's newer word
        s = _sess(be, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
        frames = []
        sb.SdkBackend.on_model_fast_changed = staticmethod(lambda: frames.append(1))
        unversioned = _server_info(claude_code_version=None, models=[
            {"value": "fable", "resolvedModel": "claude-fable-5-2", "displayName": "Fable 5.2"},
            {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5", "supportsFastMode": True}])

        class _Client:
            async def get_server_info(self):
                return unversioned
        s.client = _Client()
        asyncio.run(s._do_adopt_server_info())
        t = _table(be.state_dir)
        self.assertTrue(t["fable"]["fast"], "an unversioned connect cannot override a stamped row's answer")
        self.assertEqual(t["fable"]["cli"], "2.1.321")
        self.assertEqual({k: v["fast"] for k, v in t.items() if k in ("opus", "claude-opus-5-5")}, {"opus": True, "claude-opus-5-5": True},
                         "…but it writes the ids nobody named")
        self.assertEqual(t["opus"]["cli"], "", "unstamped: the answer named no version and this session has seen no init")
        self.assertEqual(frames, [1], "the new ids were a change")
        self.assertEqual(s._fast_support_connect, mf.support_from_payload(unversioned), "the list waits for the init's version")

        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data

        async def _noop(): pass
        s._do_refresh_context = _noop

        async def init(data):
            s._on_message(_Sys(data), type("A", (), {}), type("R", (), {}), _Sys)
            await asyncio.sleep(0)
        asyncio.run(init({"fast_mode_state": "off", "model": "claude-fable-5-2", "claude_code_version": "2.1.322"}))
        t = _table(be.state_dir)
        self.assertFalse(t["fable"]["fast"], "the init names 2.1.322, newer than the row's 2.1.321: the connect-time list now flips it")
        self.assertFalse(t["claude-fable-5-2"]["fast"])
        self.assertEqual(t["fable"]["cli"], "2.1.322")
        self.assertEqual(t["opus"]["cli"], "2.1.322", "…and stamps the rows it wrote unversioned")
        self.assertEqual(frames, [1, 1], "the flip is one frame")
        self.assertIsNone(s._fast_support_connect, "said once")
        asyncio.run(init({"fast_mode_state": "off", "model": "claude-fable-5-2", "claude_code_version": "2.1.322"}))
        self.assertEqual(frames, [1, 1], "a second init re-says nothing")
        # the other way: the init names an OLDER version than the row's — the newer word stands, the stamp too
        be2 = _backend()
        mf.note(be2.state_dir, {"fable": True}, cli="2.1.321")
        s2 = _sess(be2, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
        s2.client = _Client()
        asyncio.run(s2._do_adopt_server_info())
        s2._do_refresh_context = _noop
        asyncio.run(_init_on(s2, _Sys, {"fast_mode_state": "off", "model": "claude-fable-5-2", "claude_code_version": "2.1.320"}))
        self.assertTrue(_table(be2.state_dir)["fable"]["fast"], "2.1.320's word does not beat 2.1.321's")
        self.assertEqual(_table(be2.state_dir)["fable"]["cli"], "2.1.321")
        self.assertEqual(_table(be2.state_dir)["opus"]["cli"], "2.1.320", "the ids it alone named carry its version")
        # …and with the connect's stamp borrowed from a previous init of this session, the rows carry that version
        be3 = _backend()
        s3 = _sess(be3, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
        s3._cli_version_seen = "2.1.319"
        s3.client = _Client()
        asyncio.run(s3._do_adopt_server_info())
        self.assertEqual(_table(be3.state_dir)["fable"]["cli"], "2.1.319", "the last init's version, when the answer names none")
        self.assertIsNotNone(s3._fast_support_connect, "…still re-said at the init, which names the running CLI's version")

    def test_an_answer_whose_options_lack_the_field_writes_nothing_and_the_seed_stands(self):
        be = _backend()
        s = _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5")
        frames = []
        sb.SdkBackend.on_model_fast_changed = staticmethod(lambda: frames.append(1))

        class _Client:
            async def get_server_info(self):
                return _server_info_without_the_field()
        s.client = _Client()
        asyncio.run(s._do_adopt_server_info())
        self.assertFalse((Path(be.state_dir) / mf.MODEL_FAST_FILE).exists(),
                         "a CLI that predates the field said nothing: no row, Opus is not learned as off")
        self.assertEqual(frames, [], "…and no models frame")
        self.assertIsNone(mf.fast_known("opus", be.state_dir))
        self.assertTrue(sb.fast_capable("opus", be.state_dir), "the seed stands")
        self.assertTrue(sb.fast_capable("claude-opus-5-5", be.state_dir))
        self.assertFalse(sb.fast_capable("fable", be.state_dir))
        self.assertEqual(s.fast, "off", "the fast state is still adopted")

    def test_a_turn_running_fast_is_proof_for_the_model_it_names(self):
        be = _backend()
        s = _sess(be, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")

        class _Sys:
            def __init__(self, data): self.subtype = "init"; self.data = data

        class _Asst:
            pass

        class _Res:
            pass

        async def _noop(): pass
        s._do_refresh_context = _noop

        async def run(data):
            s._on_message(_Sys(data), _Asst, _Res, _Sys)
            await asyncio.sleep(0)
        asyncio.run(run({"fast_mode_state": "off", "model": "claude-fable-5-2", "claude_code_version": "2.1.321"}))
        self.assertFalse((Path(be.state_dir) / mf.MODEL_FAST_FILE).exists(), "off proves nothing: the CLI may lack the opt-in")
        self.assertEqual(s._cli_version_seen, "2.1.321", "the init's version stamps later connect-time rows")
        asyncio.run(run({"fast_mode_state": "on", "model": "claude-fable-5-2", "claude_code_version": "2.1.321"}))
        t = _table(be.state_dir)
        self.assertEqual(t["claude-fable-5-2"], {"fast": True, "t": t["claude-fable-5-2"]["t"], "cli": "2.1.321"},
                         "running fast on it is proof of support")
        self.assertEqual(set(t), {"claude-fable-5-2"}, "the init names no other model")
        self.assertTrue(sb.fast_capable("claude-fable-5-2", be.state_dir))
        self.assertTrue(jd._mf is mf and sb._mf is mf, "one module: the judges and the backend read the same table code")

    def test_the_always_fast_switch_arms_the_flag_where_the_cli_says(self):
        be = _backend()
        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        fable = _sess(be, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
        self.assertFalse(fable.fast_effective(), "before any report: the seed, Fable is not Opus")
        mf.note(be.state_dir, mf.support_from_payload(_server_info()), cli="2.1.321")
        self.assertTrue(fable.fast_effective(), "the CLI says Fable 5.2 runs fast: the switch arms the flag")
        old = _sess(be, sid="11111111-2222-4333-8444-000000000712", liveModel="Opus 4.8", liveModelId="claude-opus-4-8", model="claude-opus-4-8")
        self.assertFalse(old.fast_effective(), "the CLI lists Opus 4.8 without the claim: the seed is overridden the other way too")
        head = _sess(be, sid="11111111-2222-4333-8444-000000000713", model="opus")
        self.assertTrue(head.fast_effective(), "the opus alias by its own entry")
        # never the "default" alias: the CLI's default is per login, and the table's one row is whoever connected last
        self.assertTrue(mf.fast_known("default", be.state_dir), "the row is there (the fixture's default runs fast)")
        dflt = _sess(be, sid="11111111-2222-4333-8444-000000000714", model="default")
        self.assertFalse(dflt.fast_effective(), "a default pick with no live id arms nothing on another login's row")
        dflt._model_id = "claude-fable-5-2"
        self.assertTrue(dflt.fast_effective(), "…the live id decides once it lands")
        dflt._model_id, dflt.model = "", "Default"
        self.assertFalse(dflt.fast_effective(), "nor the label spelling of it")

    def test_the_live_row_carries_the_raw_id_for_the_kernels_read(self):
        be = _backend()
        s = _sess(be, liveModel="Fable 5.2", liveModelId="claude-fable-5-2")
        self.assertEqual(s.snapshot()["modelId"], "claude-fable-5-2")
        s.thread = type("T", (), {"is_alive": lambda self: False})()
        row = be.live_sessions()[SID]
        self.assertEqual(row["modelId"], "claude-fable-5-2", "the dormant row reads the persisted id")
        self.assertEqual(row["model"], "Fable 5.2")


class TheJudgeFollows(unittest.TestCase):
    """jd.fast_capable: the CLI's word, the seed where nothing is known; _tier_fast and the status read follow it."""

    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def _clear(self):
        for name in (jd.MODEL_FAST_FILE, "judge-fast", "judge-fast.gt", "distill-fast", "index-fast"):
            try:
                (jd.STATE / name).unlink()
            except OSError:
                pass
        jd._state_cache.clear()

    def test_the_seed_until_the_first_report_then_the_cli_in_both_directions(self):
        self.assertTrue(jd.fast_capable("opus"), "the seed: the opus family")
        self.assertTrue(jd.fast_capable("claude-opus-4-8"))
        self.assertFalse(jd.fast_capable("fable"))
        self.assertFalse(jd.fast_capable("claude-fable-5-2"))
        self.assertIsNone(jd.fast_known("fable"), "nothing known yet")
        mf.note(jd.STATE, mf.support_from_payload(_server_info()), cli="2.1.321")
        self.assertTrue(jd.fast_capable("fable"), "the CLI claims fast mode for Fable")
        self.assertTrue(jd.fast_capable("claude-fable-5-2"))
        self.assertTrue(jd.fast_capable("claude-fable-5-2-20261101"), "a dated snapshot by its alias")
        self.assertTrue(jd.fast_capable("claude-fable-5-2[1m]"))
        self.assertFalse(jd.fast_capable("claude-opus-4-8"), "listed without the claim: no longer offered")
        self.assertTrue(jd.fast_capable("opus"), "the alias by its own entry (resolved to Opus 5.5)")
        self.assertTrue(jd.fast_capable("claude-opus-4-7"), "an id the CLI did not list keeps the seed")
        self.assertFalse(jd.fast_capable("claude-fable-5-1"))
        self.assertFalse(jd.fast_capable("sonnet"))
        self.assertEqual(jd.fast_known("default"), True, "the default alias by its own entry")

    def test_the_tier_gate_follows_the_table(self):
        self.assertIsNotNone(km._set_judge_fast("on"))
        self.assertFalse(jd._tier_fast("triage", "fable"), "seed: a Fable tier asks nothing")
        self.assertTrue(jd._tier_fast("triage", "opus"))
        mf.note(jd.STATE, mf.support_from_payload(_server_info()), cli="2.1.321")
        self.assertTrue(jd._tier_fast("triage", "fable"), "the CLI says Fable runs fast: the triage tier asks for it")
        self.assertTrue(jd._tier_fast("triage", "claude-fable-5-2"))
        self.assertFalse(jd._tier_fast("triage", "claude-opus-4-8"), "…and an Opus the CLI retired it for asks nothing")
        self.assertTrue(jd._tier_fast("triage", "opus"))
        self.assertFalse(jd._tier_fast("index", "fable"), "each tier still has its own flag")

    def test_the_status_read_is_a_bool_where_something_can_be_said_else_none(self):
        self.assertIsNone(jd.fast_capable_or_unknown(""), "no model: the badge keeps its own rule")
        self.assertIsNone(jd.fast_capable_or_unknown("default"), "default with no entry: unknown")
        self.assertTrue(jd.fast_capable_or_unknown("Opus 5"), "the seed for a family it can read")
        self.assertFalse(jd.fast_capable_or_unknown("Fable 5.2"))
        self.assertIsNone(jd.fast_capable_or_unknown("gw-6-astra"), "a stranger: unknown")
        self.assertIsNone(km._fast_capable_status(None))
        self.assertIsNone(km._fast_capable_status({"model": "", "modelId": ""}))
        self.assertTrue(km._fast_capable_status({"model": "Opus 5", "modelId": "claude-opus-5"}))
        mf.note(jd.STATE, mf.support_from_payload(_server_info()), cli="2.1.321")
        self.assertTrue(jd.fast_capable_or_unknown("default"), "the default's own entry")
        self.assertTrue(km._fast_capable_status({"model": "Fable 5.2", "modelId": "claude-fable-5-2"}), "the id first")
        self.assertTrue(km._fast_capable_status({"model": "Fable 5.2", "modelId": ""}), "the label when no id landed")
        self.assertFalse(km._fast_capable_status({"model": "Opus 4.8", "modelId": "claude-opus-4-8"}))
        self.assertTrue(km._fast_capable_status({"model": "Fable", "modelId": ""}), "a fresh pick's alias label")
        # the CLI's own state outranks the table: a session running fast is on a model that can, whatever a row says
        self.assertTrue(km._fast_capable_status({"model": "Opus 4.8", "modelId": "claude-opus-4-8", "fast": "on"}),
                        "the table says no (an older binary's row), the CLI is running it: the CLI")
        self.assertTrue(km._fast_capable_status({"model": "Opus 4.8", "modelId": "claude-opus-4-8", "fast": "cooldown"}),
                        "rate-limited in fast mode is running it")
        self.assertFalse(km._fast_capable_status({"model": "Opus 4.8", "modelId": "claude-opus-4-8", "fast": "off"}), "off proves nothing")
        self.assertFalse(km._fast_capable_status({"model": "Opus 4.8", "modelId": "claude-opus-4-8", "fast": "on", "fastReason": "org"}),
                         "a state the CLI gave with a disabled reason proves nothing")
        self.assertTrue(km._fast_capable_status({"model": "gw-6-astra", "modelId": "", "fast": "on"}), "…a stranger running fast too")

    def test_the_wiring_the_badge_depends_on(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"fastCapable": _fast_capable_status(tm)', src, "the chat status carries the kernel's word")
        self.assertIn('"modelId": str(st.get("modelId") or "")', inspect.getsource(km._sdk_live_norm), "the live map keeps the raw id")
        self.assertIn("_chat_ident(jd.STATE / jd.MODEL_FAST_FILE)", inspect.getsource(km._chat_sig_shared),
                      "a table write is the event that rebuilds every cached tab's status")
        self.assertNotIn("MODEL_FAST_FILE", inspect.getsource(km._fleet_view_sig),
                         "the feed and the lanes read no fast capability: no input they do not read")
        self.assertIn("on_model_fast_changed = staticmethod(_models_changed)", inspect.getsource(km),
                      "the backend's change tells every picker through the models frame")
        self.assertIn("self._learn_fast_support(info", inspect.getsource(sb.SdkSession._do_adopt_server_info))
        self.assertIn('info.get("claude_code_version")', inspect.getsource(sb.SdkSession._do_adopt_server_info),
                      "the connect-time rows are stamped with the version the answer itself names")
        self.assertIn("self._learn_fast_support(d", inspect.getsource(sb.SdkSession._on_message))
        self.assertIn("self._note_fast_support(_pending", inspect.getsource(sb.SdkSession._on_message),
                      "the init re-says an unversioned connect's list under its own version")


class TheModelsPayload(unittest.TestCase):
    """GET /models: each version row's `fast` and each family's, for its default alias — true, false, or null."""

    def setUp(self):
        self._codex = km._codex_backend
        km._codex_backend = False            # never build the real Codex backend from the /models handler
        self._orig_changed = km._models_changed
        km._models_changed = lambda: None
        self.err = io.StringIO()
        self._p = mock.patch.object(km.sys, "stderr", self.err)
        self._p.start()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        for name in (jd.MODEL_FAST_FILE, km.MODEL_PICKS_FILE_NAME):
            try:
                (jd.STATE / name).unlink()
            except OSError:
                pass

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self._p.stop()
        km._models_changed = self._orig_changed
        km._codex_backend = self._codex
        for name in (jd.MODEL_FAST_FILE, km.MODEL_PICKS_FILE_NAME):
            try:
                (jd.STATE / name).unlink()
            except OSError:
                pass

    def _models(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/models", headers={"X-Romp-Token": km.TOKEN})
            r = conn.getresponse()
            self.assertEqual(r.status, 200)
            return json.loads(r.read())
        finally:
            conn.close()

    def test_every_row_carries_the_capability(self):
        d = self._models()
        fams = {m["value"]: m for m in d["models"]}
        self.assertIn("fast", fams["opus"], "a family carries the field")
        self.assertIsNone(fams["opus"]["fast"], "nothing learned: null, and the picker keeps its seed")
        for fam in fams.values():
            for v in fam["versions"]:
                self.assertIn("fast", v)
                self.assertIsNone(v["fast"])
        mf.note(jd.STATE, mf.support_from_payload(_server_info()), cli="2.1.321")
        d = self._models()
        fams = {m["value"]: m for m in d["models"]}
        self.assertTrue(fams["fable"]["fast"], "the family's default is its alias: the CLI's claim for it")
        self.assertTrue(fams["opus"]["fast"])
        self.assertFalse(fams["sonnet"]["fast"], "listed without the claim")
        self.assertIsNone(fams["haiku"]["fast"], "never named: unknown")
        vers = {v["value"]: v["fast"] for v in fams["opus"]["versions"]}
        self.assertFalse(vers["claude-opus-4-8"], "the version row the CLI retired fast mode for")
        self.assertIsNone(vers["claude-opus-4-7"], "a version no report named")
        # a pinned default reads the pinned version's own answer, not the alias's
        (jd.STATE / km.MODEL_PICKS_FILE_NAME).write_text(json.dumps({"opus": "claude-opus-4-8"}))
        d = self._models()
        fams = {m["value"]: m for m in d["models"]}
        self.assertEqual(fams["opus"]["default"], "claude-opus-4-8")
        self.assertFalse(fams["opus"]["fast"], "the family's default is the pin, and the pin cannot run fast")


if __name__ == "__main__":
    unittest.main()
