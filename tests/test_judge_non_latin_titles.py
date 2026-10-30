#!/usr/bin/env python3
"""Titles and captions written in a non-Latin script survive the judges' degenerate-text guards (2026-10-30).

GIST_SYS asks the model to keep the request's own vocabulary, so a request typed in Cyrillic or Chinese
comes back with a title in that script. The guards that drop junk counted ASCII letters only, so every
such title had none: the opener's mint was dropped, the planner coerced the ask into the newest card as
a step titled with the raw message, and the gist, caption, archive headline and grouper retitles were
discarded too. The guards now count letters in any script; text of digits and punctuation alone is
still dropped. All fixtures are SYNTHETIC (invented text, a private placeholder sid)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_non_latin", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
T0 = NOW - 3600
SID = "6c0d1a2e-4b7f-4e93-9a15-2f8e7c3b5d01"   # this module's PRIVATE synthetic sid (CLAUDE.md's goal-store rule)
RU = "Тёмная тема в настройках"                 # "a dark theme in the settings"
ZH = "添加深色模式开关"                          # "add a dark-mode toggle"
EL = "Διόρθωση εξαγωγής"                        # "fix the export"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def ops_json(*ops):
    return json.dumps({"ops": [dict({"why": "w"}, **o) for o in ops]}, ensure_ascii=False)


def mint(text):
    return ops_json({"do": "mint", "text": text})


class PlannerOps(unittest.TestCase):
    def test_a_mint_titled_in_cyrillic_or_chinese_is_kept(self):
        for title in (RU, ZH):
            ops = jd._parse_plan(mint(title), 0)
            self.assertIsNotNone(ops, "the mint %r was dropped" % title)
            self.assertEqual([(o["do"], o["text"]) for o in ops], [("mint", title)])

    def test_a_sub_and_a_retitle_in_greek_are_kept(self):
        ops = jd._parse_plan(ops_json({"do": "sub", "under": 1, "text": EL},
                                      {"do": "retitle", "goal": 1, "text": RU}), 1)
        self.assertEqual([(o["do"], o["text"]) for o in ops or []], [("sub", EL), ("retitle", RU)])

    def test_text_without_any_letter_is_still_dropped(self):
        self.assertEqual(jd._parse_plan(mint("Dark theme in settings"), 0)[0]["text"], "Dark theme in settings")
        self.assertIsNone(jd._parse_plan(mint("123 -- 456"), 0))
        self.assertIsNone(jd._parse_plan(ops_json({"do": "retitle", "goal": 1, "text": "#42 / 7"}), 1))


class CaptionsAndHeadlines(unittest.TestCase):
    def test_a_caption_or_gist_in_cyrillic_or_chinese_is_kept(self):
        self.assertEqual(jd._clean_caption(RU), RU)
        self.assertEqual(jd._clean_caption(ZH), ZH)
        self.assertEqual(jd._clean_caption("a dark-mode toggle for settings"), "a dark-mode toggle for settings")

    def test_a_caption_without_three_letters_is_still_rejected(self):
        self.assertEqual(jd._clean_caption("12 -- 34"), "")
        self.assertEqual(jd._clean_caption("ab 12"), "")

    def test_an_archive_headline_and_abstract_in_cyrillic_parse(self):
        got = jd._parse_archive("HEADLINE: %s\nABSTRACT: Сессия добавила тёмную тему. Осталось проверить." % RU)
        self.assertEqual(got and got["headline"], RU)
        self.assertIsNone(jd._parse_archive("HEADLINE: 12 34\nABSTRACT: Сессия добавила тёмную тему."))


class GrouperOps(unittest.TestCase):
    def test_a_retitle_and_a_split_retitle_in_cyrillic_or_chinese_are_kept(self):
        ops = jd._parse_group(ops_json({"do": "retitle", "goal": 1, "text": RU},
                                       {"do": "split", "goal": 2, "retitle": ZH}), 2)
        self.assertEqual([(o["do"], o.get("text") or o.get("retitle")) for o in ops],
                         [("retitle", RU), ("split", ZH)])

    def test_a_retitle_without_any_letter_is_still_dropped(self):
        ops = jd._parse_group(ops_json({"do": "retitle", "goal": 1, "text": "#42"},
                                       {"do": "split", "goal": 2, "retitle": "--"}), 2)
        self.assertEqual(ops, [{"do": "split", "why": "w", "goal": 2}])


class OpenerMintsTheAsk(unittest.TestCase):
    """End to end: an ended English card, then an OPEN ask whose opener reply mints its own card. On the
    ASCII-only guard the Cyrillic mint parsed to nothing and the ask was coerced in as a step of the old card."""

    def tearDown(self):
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()   # this sid's override journal, never left for another module
        except FileNotFoundError:
            pass

    def _board(self, ask, reply):
        recs = [uline(T0, "Fix the export", "u1"),
                aline(T0 + 10, "Fixed.", "a1", "u1"),
                uline(T0 + 20, ask, "u2", "a1"),
                aline(T0 + 30, "on it", "a2", "u2", stop=None)]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            jd.plan_llm = lambda *a, **k: mint("Fix the export")
            jd.opener_llm = lambda *a, **k: reply
            jd._group_store = lambda *a, **k: None
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
        tops = sorted(nd["text"] for nd in store["nodes"].values() if nd["parentId"] is None)
        subs = [(nd["text"], nd.get("why")) for nd in store["nodes"].values() if nd["parentId"] is not None]
        return tops, subs

    def test_an_english_ask_mints_its_own_card(self):
        tops, subs = self._board("Add a dark theme to the settings", mint("Dark theme in settings"))
        self.assertEqual(tops, ["Dark theme in settings", "Fix the export"])
        self.assertEqual(subs, [])

    def test_a_cyrillic_ask_mints_its_own_card_rather_than_a_step_of_the_newest(self):
        tops, subs = self._board("Добавь тёмную тему в настройки", mint(RU))
        self.assertEqual(tops, sorted([RU, "Fix the export"]),
                         "the ask was not minted; steps under the old card: %r" % (subs,))
        self.assertEqual(subs, [])


if __name__ == "__main__":
    unittest.main()
