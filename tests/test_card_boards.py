#!/usr/bin/env python3
"""The card boards (plans/card-boards.md). Phase two: the kernel's board table (_CODE_BOARDS) holds the feed in the one schema
the renderer's ui/webview/board-def.ts holds, checked by _board_check at import; every card the kernel builds carries its
board and its category beside its column; the bell, the phone and the badge read the board's notification set and badge
category instead of a literal. Phase three: the board STORE (STATE/boards/<id>.json) behind define_board and remove_board,
read by _boards() memoized on the directory's stat; the pure resolver _board_resolve_post the notice producer calls; the
standing count its refusals read; the snapshot entries carrying and checked against their board; the frame's boards field;
POST /board and GET /boards. Synthetic fixtures only (the notes-api demo world)."""
import contextlib
import inspect
import json
from unittest import mock
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
open(os.path.join(_XDG, "romp", "session-hosts"), "w").write("off\n")
km = load_source("romp_kernel_card_boards", os.path.join(BIN, "romp-kernel"))
KSRC = open(os.path.join(BIN, "romp-kernel")).read()
TSRC = open(os.path.join(ROOT, "ui", "webview", "board-def.ts")).read()

SID = "11111111-2222-3333-4444-555555555555"     # web


def _feed_def():
    return km._CODE_BOARDS["feed"]


class BoardTable(unittest.TestCase):
    def test_the_feed_entry_is_in_the_schema_and_carries_todays_literals(self):
        d, err = km._board_check(_feed_def(), allow_reserved=True)
        self.assertIsNone(err)
        self.assertEqual([c["id"] for c in d["categories"]], ["working", "needs_input", "completed"])
        self.assertEqual([(c["title"], c["chip"]) for c in d["categories"]], [("Working", "working"), ("Needs you", "blocked"), ("Completed", "completed")])
        self.assertEqual((d["defaultCategory"], d["sort"], d["groupBy"], d["needsYou"]), ("working", {"key": "t", "dir": "asc"}, "session", "needs_input"))
        self.assertEqual(d["notify"], ["needs_input", "completed"])
        self.assertEqual(km._NOTIFY_COLUMNS, ("needs_input", "completed"), "the notify set the snapshot entries are checked against is the table's")
        self.assertIn('_NOTIFY_COLUMNS = tuple(_CODE_BOARDS["feed"]["notify"])', KSRC)

    def test_the_renderer_constant_and_the_kernel_table_name_the_same_feed(self):
        # ui/webview/board-def.ts FEED_BOARD, EVERY member (the 1837 read, low 3: a compare of seven members let id, title,
        # rules, subSorts and order drift, and a kernel-side order of ownerRank passed both suites), read from the source
        # (no TypeScript runtime here): the object literal's comments stripped, its keys quoted, its trailing commas dropped,
        # then json.loads, so the two copies of the one definition are compared as values
        ts = TSRC[TSRC.index("export const FEED_BOARD: Board = {") + len("export const FEED_BOARD: Board = "):]
        ts = ts[:ts.index("\n};") + 2]
        lit = re.sub(r"//[^\n]*", "", ts)                                  # the line comments
        lit = re.sub(r"(?m)^(\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', lit)       # keys at a line's start
        lit = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', lit)      # keys inside one-line objects
        lit = re.sub(r",(\s*[}\]])", r"\1", lit)                          # trailing commas
        renderer = json.loads(lit)
        self.assertEqual(sorted(renderer), sorted(_feed_def()), "the same twelve members on both sides")
        self.assertEqual(renderer, _feed_def(), "the renderer's FEED_BOARD and the kernel's _CODE_BOARDS['feed'], member for member")

    def test_the_check_refuses_a_copy_with_one_member_changed_naming_it(self):
        def refused(mut, want):
            d = json.loads(json.dumps(_feed_def()))
            mut(d)
            _, err = km._board_check(d, allow_reserved=True)
            self.assertIsNotNone(err, want)
            self.assertRegex(err, want)
        refused(lambda d: d["categories"][1].__setitem__("chip", "red"), r"chip must be one of working, blocked, completed, neutral")
        refused(lambda d: d["categories"].extend({"id": "c%d" % i, "title": "C", "chip": "neutral"} for i in range(6)), r"1 to 8")
        refused(lambda d: d["categories"][2].__setitem__("id", "working"), r"repeats")
        refused(lambda d: d.__setitem__("defaultCategory", "done"), r"defaultCategory must name")
        refused(lambda d: d.__setitem__("rules", [{"when": {"needsYou": True}, "category": "done"}]), r"rule's category must name")
        refused(lambda d: d.__setitem__("rules", [{"when": {"owner": "x"}, "category": "working"}]), r"predicate has an unknown member 'owner'")
        refused(lambda d: d.__setitem__("sort", {"key": "age", "dir": "asc"}), r"sort\.key must be one of t, session, owner, title")
        refused(lambda d: d.__setitem__("groupBy", "owner"), r'groupBy must be "session" or null')
        refused(lambda d: d.__setitem__("order", ["newestPinned"]), r"order rules must be from ownerRank")
        refused(lambda d: d.__setitem__("notify", ["done"]), r"notify names a category the board does not have")
        refused(lambda d: d.__setitem__("needsYou", "done"), r"needsYou must be one of")
        refused(lambda d: d.__setitem__("kinds", ["card"]), r"kinds must be from")
        refused(lambda d: d.__setitem__("colour", "blue"), r"unknown member 'colour'")
        refused(lambda d: d.__setitem__("id", "Feed"), r"id must match")
        self.assertEqual(km._board_check("feed"), (None, "a board definition must be a JSON object"))
        # the door refuses the reserved id; the constants pass it
        self.assertRegex(km._board_check(_feed_def())[1], r"code-defined board")
        self.assertIn("raise RuntimeError(\"code-defined board", KSRC, "a drifted constant fails at import")

    def test_the_helpers_read_the_board_a_card_names_and_fall_to_the_feed(self):
        self.assertEqual(km._board_notify("feed"), ("needs_input", "completed"))
        self.assertEqual(km._board_notify(None), ("needs_input", "completed"), "a card naming no board is the feed's")
        self.assertEqual(km._board_notify("notes"), ("needs_input", "completed"), "an id this kernel does not know reads as the feed's until phase three's store")
        self.assertEqual(km._board_needs_you("feed"), "needs_input")


class CardsCarryTheirBoard(unittest.TestCase):
    def test_every_card_family_stamps_board_and_category_beside_column(self):
        # the six builders (the quarantine card is a notice card since 2026-09-19), by their source: five carry "board": "feed" and a category equal to its column literal; the notice family its row's pair
        fams = {"_feed_session_entry": '"board": "feed", "category": column,',
                "_provisional_card": '"column": "working", "board": "feed", "category": "working",',
                "_awaiting_card": '"column": "working", "board": "feed", "category": "working",',
                "_blocked_placeholder": '"column": "needs_input", "board": "feed", "category": "needs_input",',
                "build_feed": '"column": "needs_input", "board": "feed", "category": "needs_input",',   # the parked handoff
                "_notice_cards": '"board": board_id, "category": category_id,'}   # the row's own fields since phase three (the feed with the needsYou mapping for a row without them)
        for fn, lit in fams.items():
            self.assertIn(lit, inspect.getsource(getattr(km, fn)), fn)
        self.assertEqual(KSRC.count('"board": "feed"'), 5, "five families stamp the feed literally (the quarantine card is a notice card since 2026-09-19); the notice family copies its row's board (phase three); no card built by hand without its board")
        self.assertIn('board_id = r.get("board") or "feed"', inspect.getsource(km._notice_cards), "the notice family's fallback is the feed")
        # the column expression itself is untouched: the record of the 2026-06-29 and 2026-07-07 rulings its pins hold
        self.assertIn('column = ("needs_input" if (api_block or nid == jauth_top or nid == perm_top', inspect.getsource(km._feed_session_entry))

    def test_a_notice_card_carries_the_feed_board_and_its_category_executed(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        cwd = root / "notes-api"; cwd.mkdir()
        orig_state, orig_names = km.jd.STATE, km.NAMES
        km.jd._rebind_state(root / "state")
        (km.jd.STATE / "session-hosts").parent.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "session-hosts").write_text("off\n")
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        (km.jd.NAMES / SID).write_text("web\t%s\t#1EA1EB\t#ffffff\n" % cwd)
        km.NAMES = km.jd.NAMES
        saved = (km._live_map, km._cwd_of, km._mark_views_dirty, km._push_soon)
        km._live_map = lambda: {}
        km._cwd_of = lambda sid: str(cwd) if sid == SID else None
        km._mark_views_dirty = lambda: None
        km._push_soon = lambda: None
        km._NOTICE_MEMO.clear(); km._NOTICE_SWEPT.clear()
        try:
            _, err = km.post_notice(SID, "figure", "A new version of the accuracy figure is ready", producer="figure", now=100)
            self.assertIsNone(err)
            _, err = km.post_notice(SID, "ask", "Pick the retry policy", producer="cli", needs_you=True, now=200)
            self.assertIsNone(err)
            cards = {c["notice"]["key"]: c for c in km._notice_cards(300, set())}
            self.assertEqual((cards["figure"]["board"], cards["figure"]["category"], cards["figure"]["column"]), ("feed", "completed", "completed"))
            self.assertEqual((cards["ask"]["board"], cards["ask"]["category"], cards["ask"]["column"]), ("feed", "needs_input", "needs_input"))
        finally:
            (km._live_map, km._cwd_of, km._mark_views_dirty, km._push_soon) = saved
            km.jd._rebind_state(orig_state); km.NAMES = orig_names
            km._NOTICE_MEMO.clear(); km._NOTICE_SWEPT.clear()
            td.cleanup()


class BellAndBadgeReadTheBoard(unittest.TestCase):
    def test_the_badge_counts_the_boards_needs_you_category_and_falls_to_column_for_an_older_card(self):
        feed = {"asks": [
            {"itemId": "a", "board": "feed", "category": "needs_input", "column": "needs_input"},
            {"itemId": "b", "board": "feed", "category": "needs_input", "column": "needs_input", "provisional": True},   # placeholder churn
            {"itemId": "c", "board": "feed", "category": "working", "column": "working"},
            {"itemId": "d", "board": "feed", "category": "completed", "column": "completed"},
            {"itemId": "e", "column": "needs_input"},                                                                     # an older card: column alone
        ]}
        self.assertEqual(km._needs_you_count(feed), 2)
        self.assertIn('_card_needs_you(a)', inspect.getsource(km._needs_you_count), "the badge reads the one predicate (the 1861 read)")
        self.assertIn('a.get("category", a.get("column")) == nb', inspect.getsource(km._card_needs_you), "the predicate: the card's own board's badge category, the column from an older card")
        self.assertIn('_card_needs_you(a)', inspect.getsource(km._needs_input_sids), "the ring's sids read the same predicate")
        # a board whose badge category is None counts nothing, a key-less card included (the 1837 read, low 2: None == None
        # counted a card carrying neither field); the table has no such board yet, so the helper stands in for one
        keyless = {"asks": [{"itemId": "k", "board": "notes"}, {"itemId": "a", "board": "notes", "category": "needs_input"}]}
        with mock.patch.object(km, "_board_needs_you", lambda board: None):
            self.assertEqual(km._needs_you_count(keyless), 0)
        self.assertEqual(km._needs_you_count(keyless), 1, "the same cards under the feed's fallback: the category-carrying one counts")

    def test_the_notifications_diff_reads_the_boards_notify_set(self):
        src = inspect.getsource(km._feed_notifications_diff)
        self.assertIn('col, sid, ent = a.get("category", a.get("column")), str(a.get("sid") or ""), prev.get(iid)', src,
                      "the diff reads the category with the column as the fallback, the same read as the badge (the 1837 read, low 1)")
        self.assertIn('board = a.get("board") or "feed"', src)
        self.assertIn('if col in _board_notify(board):', src)
        self.assertIn('e = {"sid": sid, "board": board, "column": col,', src, "the snapshot entry carries the board (phase three, the 1837 round-two read)")
        self.assertIn('needs_you = col == _board_needs_you(a.get("board"))', src, "the notification's words come from the board's badge category, never a literal")
        self.assertNotIn('col == "needs_input"', src)
        self.assertNotIn("col in _NOTIFY_COLUMNS", src, "no literal set in the diff; the snapshot's entry check keeps _NOTIFY_COLUMNS")
        ent_src = inspect.getsource(km._notify_prev_entry)
        self.assertIn('allowed = _board_notify(board)', ent_src, "the stored snapshot's entries are checked against their own board's set (phase three)")
        self.assertEqual(km._NOTIFY_COLUMNS, ("needs_input", "completed"), "the feed's set stands as the table's value")


if __name__ == "__main__":
    unittest.main()


class World:
    """A hermetic state root with one named session (web), the kernel rebound to it, the board and notice memos cleared."""
    def __init__(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.cwd = root / "notes-api"; self.cwd.mkdir()
        self.orig_state, self.orig_names = km.jd.STATE, km.NAMES
        km.jd._rebind_state(root / "state")
        (km.jd.STATE / "session-hosts").parent.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "session-hosts").write_text("off\n")
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        (km.jd.NAMES / SID).write_text("web\t%s\t#1EA1EB\t#ffffff\n" % self.cwd)
        km.NAMES = km.jd.NAMES
        self.saved = (km._live_map, km._cwd_of, km._mark_views_dirty, km._push_soon, km.Sessions.live)
        km._live_map = lambda: {}
        km.Sessions.live = staticmethod(lambda: {})
        km._cwd_of = lambda sid: str(self.cwd) if sid == SID else None
        self.dirty, self.pushes = [], []
        km._mark_views_dirty = lambda: self.dirty.append(1)
        km._push_soon = lambda: self.pushes.append(1)
        self.reset_memos()

    @staticmethod
    def reset_memos():
        # guarded: at a base without the store these names are absent, and each method must then red on its own behaviour,
        # never on the world's setUp (the 1845 read, low 1)
        memo = getattr(km, "_BOARDS_MEMO", None)
        if isinstance(memo, dict):
            memo["slot"] = None
        getattr(km, "_BOARDS_BAD", set()).clear()
        km._NOTICE_MEMO.clear(); km._NOTICE_SWEPT.clear(); km._CLEARED_MEMO["slot"] = None

    def close(self):
        (km._live_map, km._cwd_of, km._mark_views_dirty, km._push_soon, km.Sessions.live) = self.saved
        km.jd._rebind_state(self.orig_state); km.NAMES = self.orig_names
        self.reset_memos()
        self.td.cleanup()


NOTES = {"id": "notes", "title": "Notes",
         "categories": [{"id": "new", "title": "New", "chip": "neutral"}, {"id": "kept", "title": "Kept", "chip": "working"}, {"id": "done", "title": "Done", "chip": "completed"}],
         "defaultCategory": "new", "rules": [{"when": {"needsYou": True}, "category": "new"}, {"when": {"producer": "figure"}, "category": "kept"}],
         "sort": {"key": "t", "dir": "desc"}, "subSorts": [], "groupBy": None, "order": [], "notify": ["new"], "needsYou": "new", "kinds": ["notice"]}


def _row(sid, key, rev, t, board=None, category=None, needs_you=False):
    """A notice post row as the store holds it (the notice producer's parcel writes board and category; a row without them
    reads as the feed's with the needsYou mapping, the phase-two card rule)."""
    r = {"op": "post", "t": t, "key": key, "rev": rev, "sid": sid, "producer": "cli", "title": key, "body": "", "attachment": None,
         "actions": [], "needsYou": needs_you, "expiresAt": None, "dismissOnAction": False}
    if board is not None:
        r["board"] = board
    if category is not None:
        r["category"] = category
    return r


class TheStore(unittest.TestCase):
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_define_writes_the_file_and_the_boards_read_it_over_the_code_table(self):
        self.assertEqual(km._boards_data(), {})
        d, err = km.define_board(dict(NOTES))
        self.assertIsNone(err); self.assertEqual(d["id"], "notes")
        fp = km.jd.STATE / "boards" / "notes.json"
        self.assertTrue(fp.exists(), "the definition is one whole file under the state root")
        self.assertEqual(json.loads(fp.read_text()), NOTES)
        self.assertEqual(sorted(km._boards()), ["feed", "notes"], "code first, then the data")
        self.assertEqual(km._board_def("notes")["needsYou"], "new")
        self.assertEqual(km._board_notify("notes"), ("new",))
        self.assertEqual((len(self.w.dirty), len(self.w.pushes)), (1, 1), "the views marked dirty and the pusher woken once")
        # the frame carries the data set alone, and build_feed attaches it by this read
        self.assertEqual(km._boards_data(), {"notes": NOTES})
        self.assertIn('"boards": _boards_data(),', inspect.getsource(km.build_feed))

    def test_the_memo_serves_while_the_directory_stands_and_re_reads_when_it_moves(self):
        km.define_board(dict(NOTES))
        first = km._boards_data()
        self.assertIs(km._boards_data(), first, "the same parsed map while the directory's stat stands")
        scratch = km._default_board("scratch")
        km.define_board(scratch)
        second = km._boards_data()
        self.assertIsNot(second, first); self.assertEqual(sorted(second), ["notes", "scratch"], "a published file moves the directory: the next read re-parses")
        # a file outside the schema, or one naming another board, is skipped and said once
        (km.jd.STATE / "boards" / "bad.json").write_text(json.dumps({"id": "bad", "title": "Bad"}))
        (km.jd.STATE / "boards" / "other.json").write_text(json.dumps(dict(NOTES, id="notes2")))
        World.reset_memos()
        import io, contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(sorted(km._boards_data()), ["notes", "scratch"])
            km._BOARDS_MEMO["slot"] = None
            km._boards_data()
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 2, "each bad file said once across two reads: %r" % lines)
        self.assertTrue(any("bad.json skipped" in l and "categories" in l for l in lines), lines)
        self.assertTrue(any("other.json skipped" in l and "names board" in l for l in lines), lines)

    def test_a_file_rewritten_in_place_is_read_on_the_next_call_and_dirties_the_views(self):
        # the 1845 read (low 3): the memo was keyed on the directory's stat alone, so an edit through a shell redirection or an
        # editor (the path docs/reference.md names) was invisible until the directory moved or the kernel restarted
        km.define_board(dict(NOTES))
        self.assertEqual(km._boards_data()["notes"]["title"], "Notes")
        dirt = len(self.w.dirty)
        fp = km.jd.STATE / "boards" / "notes.json"
        fp.write_text(json.dumps(dict(NOTES, title="Lab notes")))            # in place: the directory's stat stands
        self.assertEqual(km._boards_data()["notes"]["title"], "Lab notes", "the file's own stat is in the key")
        self.assertEqual(len(self.w.dirty), dirt + 1, "a moved key after the first read marks the views dirty, once")
        self.assertEqual(km._boards_data()["notes"]["title"], "Lab notes")
        self.assertEqual(len(self.w.dirty), dirt + 1, "an unchanged key dirties nothing")
        fp.write_text(json.dumps(dict(NOTES, title="Lab notes")))            # the same bytes again: the mtime moves, the parse agrees
        km._boards_data()
        self.assertEqual(len(self.w.dirty), dirt + 2, "the key moved (mtime), one more dirty; the frame's dedup absorbs an unchanged set")

    def test_define_refuses_by_name_and_the_reserved_id(self):
        bad = dict(NOTES); bad["categories"] = [dict(c, chip="red") for c in NOTES["categories"]]
        self.assertRegex(km.define_board(bad)[1], r"chip must be one of")
        self.assertRegex(km.define_board(dict(NOTES, id="feed"))[1], r"code-defined board")
        self.assertRegex(km.define_board(dict(NOTES, colour="blue"))[1], r"unknown member 'colour'")
        self.assertEqual(km._boards_data(), {}, "a refused define writes nothing")
        self.assertEqual(self.w.dirty, [])

    def test_define_refuses_to_drop_a_category_still_holding_standing_cards_and_remove_refuses_while_a_card_names_the_board(self):
        km.define_board(dict(NOTES))
        km._notice_append(SID, _row(SID, "sweep", 1, 100, board="notes", category="kept"))
        km._notice_append(SID, _row(SID, "plot", 1, 110, board="notes", category="kept"))
        km._notice_append(SID, _row(SID, "fig", 1, 120, board="notes", category="new"))
        km._notice_append(SID, _row(SID, "ask", 1, 130, needs_you=True))            # a feed card, the phase-two mapping
        km._notice_append(SID, _row(SID, "old", 1, 90, board="notes", category="kept"))
        km._notice_append(SID, {"op": "expire", "key": "old", "rev": 1, "t": 95, "sid": SID})   # retired: not standing
        World.reset_memos()
        self.assertEqual(km._notice_standing_count("notes"), 3)
        self.assertEqual(km._notice_standing_count("notes", "kept"), 2)
        self.assertEqual(km._notice_standing_count("notes", "done"), 0)
        self.assertEqual(km._notice_standing_count("feed"), 1)
        self.assertEqual(km._notice_standing_count("feed", "needs_input"), 1)
        dropped = dict(NOTES); dropped["categories"] = [c for c in NOTES["categories"] if c["id"] != "kept"]
        dropped["rules"] = [r for r in NOTES["rules"] if r["category"] != "kept"]
        _, err = km.define_board(dropped)
        self.assertRegex(err, r"drops category 'kept', which still holds 2 standing cards")
        self.assertEqual(km._boards_data()["notes"], NOTES, "the file stands as it was")
        ok, err = km.remove_board("notes")
        self.assertEqual(ok, False); self.assertRegex(err, r"3 standing cards still name board 'notes'")
        self.assertRegex(km.remove_board("feed")[1], r"code-defined")
        self.assertRegex(km.remove_board("scratch")[1], r"no board 'scratch'")
        # the cards dismissed: the define and the remove go through
        for key in ("sweep", "plot", "fig"):
            km._notice_append(SID, {"op": "expire", "key": key, "rev": 1, "t": 200, "sid": SID})
        World.reset_memos()
        self.assertEqual(km._notice_standing_count("notes"), 0)
        self.assertIsNone(km.define_board(dropped)[1])
        self.assertEqual(km.remove_board("notes"), (True, None))
        self.assertFalse((km.jd.STATE / "boards" / "notes.json").exists())
        self.assertEqual(sorted(km._boards()), ["feed"])
        self.assertEqual(km._board_def("notes"), km._CODE_BOARDS["feed"], "a removed board's cards read as the feed's")


class TheResolver(unittest.TestCase):
    """_board_resolve_post is PURE: it names where a post files and hands back the definition to write, never writing."""
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_no_board_is_the_feed_and_its_category_follows_needs_you(self):
        self.assertEqual(km._board_resolve_post("", "", needs_you=True), ("feed", "needs_input", None, None))
        self.assertEqual(km._board_resolve_post(None, None), ("feed", "completed", None, None))
        self.assertEqual(km._board_resolve_post("feed", "working"), ("feed", "working", None, None))
        b, c, err, pending = km._board_resolve_post("feed", "done")
        self.assertRegex(err, r"the feed has no category 'done'"); self.assertIsNone(pending)

    def test_needs_you_on_an_unknown_board_is_refused_before_the_defaults_are_minted(self):
        # the 1845 read (medium): the first post with needs_you on a new board resolved to the defaults and dropped the flag
        # (no badge, no bell) while the identical second post was refused; one behaviour for both, the refusal naming the flag
        b, c, err, pending = km._board_resolve_post("scratch", "", needs_you=True, producer="cli", key="k")
        self.assertEqual((b, c, pending), (None, None, None))
        self.assertRegex(err, r"board 'scratch' would be created without a needs-you category")
        self.assertRegex(err, r"needsYou|--needs-you")
        self.assertEqual(km._boards_data(), {}, "nothing minted")
        # the same board defined with a badge category: the flag files there, first post and later posts alike
        km.define_board(dict(NOTES, id="scratch"))
        self.assertEqual(km._board_resolve_post("scratch", "", needs_you=True)[:2], ("scratch", "new"))
        self.assertEqual(km._board_resolve_post("scratch", "", needs_you=True)[:2], ("scratch", "new"))

    def test_an_unknown_board_is_created_on_first_use_with_the_defaults(self):
        b, c, err, pending = km._board_resolve_post("scratch", "", producer="cli", key="k")
        self.assertEqual((b, c, err), ("scratch", "notes", None))
        self.assertEqual(pending, km._default_board("scratch"))
        self.assertEqual(pending["categories"], [{"id": "notes", "title": "Notes", "chip": "neutral"}])
        self.assertEqual((pending["sort"], pending["groupBy"], pending["notify"], pending["needsYou"], pending["kinds"]), ({"key": "t", "dir": "desc"}, None, [], None, ["notice"]))
        self.assertEqual(km._boards_data(), {}, "pure: nothing written")
        b, c, err, pending = km._board_resolve_post("scratch", "todo")
        self.assertEqual((b, c, pending["categories"][0]["id"]), ("scratch", "todo", "todo"))
        self.assertRegex(km._board_resolve_post("Scratch", "")[2], r"board id must match")
        self.assertRegex(km._board_resolve_post("scratch", "To Do")[2], r"category id must match")

    def test_a_known_board_files_by_the_named_category_the_rules_or_the_default(self):
        km.define_board(dict(NOTES))
        self.assertEqual(km._board_resolve_post("notes", "kept"), ("notes", "kept", None, None))
        self.assertEqual(km._board_resolve_post("notes", "", needs_you=True)[:2], ("notes", "new"), "the first rule")
        self.assertEqual(km._board_resolve_post("notes", "", producer="figure")[:2], ("notes", "kept"), "the second rule")
        self.assertEqual(km._board_resolve_post("notes", "", producer="cli")[:2], ("notes", "new"), "no rule: the default")
        self.assertEqual(km._board_resolve_post("notes", "kept", needs_you=True)[:2], ("notes", "kept"), "a named category wins over needs_you")
        # an unknown category on a DATA board is appended in neutral dress; on the code-defined feed refused
        b, c, err, pending = km._board_resolve_post("notes", "later")
        self.assertEqual((b, c, err), ("notes", "later", None))
        self.assertEqual(pending["categories"][-1], {"id": "later", "title": "Later", "chip": "neutral"})
        self.assertEqual(pending["categories"][:3], NOTES["categories"])
        self.assertEqual(km._boards_data()["notes"], NOTES, "pure: the file unchanged until the producer writes pending")
        self.assertIsNone(km.define_board(pending)[1]); self.assertEqual(len(km._boards_data()["notes"]["categories"]), 4)
        # needs_you on a board with no badge category is refused
        quiet = dict(km._default_board("quiet")); km.define_board(quiet)
        self.assertRegex(km._board_resolve_post("quiet", "", needs_you=True)[2], r"has no needs-you category")
        self.assertEqual(km._board_resolve_post("quiet", "notes", needs_you=True)[2] is None, False)


class TheSnapshotKnowsItsBoard(unittest.TestCase):
    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def test_an_entry_is_checked_against_its_own_boards_notify_set(self):
        km.define_board(dict(NOTES))
        ent = {"sid": SID, "board": "notes", "column": "new", "announced": "new", "announcedAt": 100}
        self.assertEqual(km._notify_prev_entry(ent), dict(ent), "a category the feed lacks stands under its own board")
        self.assertIsNone(km._notify_prev_entry(dict(ent, board="feed")), "the same values under the feed are outside its set")
        self.assertIsNone(km._notify_prev_entry(dict(ent, column="kept")), "kept is not in notes' notify set")
        old = {"sid": SID, "column": "needs_input", "announced": None, "announcedAt": None}
        self.assertEqual(km._notify_prev_entry(old), dict(old, board="feed"), "an entry written before the boards reads as the feed's")
        self.assertIsNone(km._notify_prev_entry({"sid": SID, "board": "gone", "column": "new", "announced": None, "announcedAt": None}),
                          "a removed board's entry falls to the feed's set, where new is not a category: dropped, so it re-announces once")


class TheBellRoundTripOnADataBoard(unittest.TestCase):
    """The 1845 read (low 2): the bell on a data board, EXECUTED. A card entering a category the feed lacks, on a board whose
    notify names it, announces once; the entry is written with its board; a fresh kernel life reloads it under that board and
    announces nothing (the same-pair rule); before the entries carried their board, the reload dropped the entry as outside
    the feed's set and the card announced again."""
    def setUp(self):
        self.w = World()
        km._notify_cards_cache.clear(); km._flags_cache.clear()
        km._NOTIFY_PREV[0] = None; km._NOTIFY_PREV_DISK[0] = None
        km._set_notify_all(True)                                        # the master bell: every card notifies

    def tearDown(self):
        km._NOTIFY_PREV[0] = None; km._NOTIFY_PREV_DISK[0] = None
        km._notify_cards_cache.clear(); km._flags_cache.clear()
        self.w.close()

    def _feed(self, category, now):
        card = {"itemId": "notice:%s:sweep:1" % SID, "sid": SID, "name": "web", "text": "The sweep finished", "board": "notes",
                "category": category, "column": "completed"}
        return {"type": "feed", "asks": [card], "sessions": [{"sid": SID, "name": "web"}], "now": now}

    def test_a_card_entering_a_data_boards_category_announces_once_and_is_remembered_across_a_life(self):
        import io, contextlib
        km.define_board(dict(NOTES))                                    # notify ["new"], needsYou "new"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._feed_notifications(self._feed("kept", 1000)), [], "the first build of a life seeds silently")
        out = km._feed_notifications(self._feed("new", 1010))
        self.assertEqual([(o[0], o[3]) for o in out], [("Romp needs you: web", "notice:%s:sweep:1" % SID)],
                         "entering new announces once, worded from the board's badge category")
        disk = json.loads((km.jd.STATE / "notify-prev.json").read_text())
        ent = disk["notice:%s:sweep:1" % SID]
        self.assertEqual((ent["board"], ent["column"], ent["announced"]), ("notes", "new", "new"), "the entry carries its board")
        self.assertEqual(km._feed_notifications(self._feed("new", 1020)), [], "holding the category is not news")
        km._NOTIFY_PREV[0] = None; km._NOTIFY_PREV_DISK[0] = None      # a fresh kernel life reads the file
        with contextlib.redirect_stderr(err):
            again = km._feed_notifications(self._feed("new", 2000))
        self.assertEqual(again, [], "the reloaded entry stands under its board: the same pair is not announced again")
        self.assertIn("seeded 1 cards from disk", err.getvalue())
        self.assertEqual(km._NOTIFY_PREV[0]["notice:%s:sweep:1" % SID]["board"], "notes")


SID_API = "22222222-3333-4444-5555-666666666666"       # api
SID_TESTS = "33333333-4444-5555-6666-777777777777"     # tests
_SESSIONS = ((SID, "web"), (SID_API, "api"), (SID_TESTS, "tests"))
_FEED_CATS = ("working", "needs_input", "completed")
_NOTES_CATS = ("new", "kept", "done")


def _many_cards(n, step):
    """n cards over the three sessions, the even ones goals on the feed and the odd ones notice cards on the notes board,
    each moved one category along per step (so a run of steps enters cards into each board's notify set and out of it)."""
    asks = []
    for i in range(n):
        sid, name = _SESSIONS[i % 3]
        if i % 2:
            cat = _NOTES_CATS[(i + step) % 3]
            asks.append({"itemId": "notice:%s:result-%d:1" % (sid, i), "sid": sid, "name": name, "text": "result %d is in" % i,
                         "board": "notes", "category": cat, "column": "needs_input" if cat == "new" else "completed"})
        else:
            cat = _FEED_CATS[(i + step) % 3]
            asks.append({"itemId": "%s:g%d" % (sid, i), "sid": sid, "name": name, "text": "route %d answers 500" % i, "board": "feed",
                         "category": cat, "column": cat, "live": True, "blocked": None,
                         "blockSummary": "which fixture does it read?" if cat == "needs_input" else None})
    return asks


class OneReadPerBuild(unittest.TestCase):
    """The board store is read ONCE per feed build (2026-10-22). The needs-you set, the box's rows, the per-session counts, the
    badge and the bell diff each asked _boards() once per card, and every ask listed the boards directory and stat'd its files
    to key the memo: thousands of directory reads per build on a board of a thousand cards. The pusher's build now reads the
    store at its first ask, answers every per-card read from that read, and keys the memo once more as it returns. The bells,
    the pushes, the badge, the rows and the snapshot come out as the per-card reads made them; an edit after the read marks
    the views dirty and reaches the next build; another thread, and the doors' own check-then-write, read the store as it
    stands."""

    def setUp(self):
        self.w = World()
        self.saved = (list(km._built_feed), km._feed_needs_input[0], km._feed_needs_rows[0], km._feed_needs_input_count[0],
                      km._NOTIFY_PREV[0], km._NOTIFY_PREV_DISK[0], km._NOTIFY_BOARD[0])
        km._notify_cards_cache.clear(); km._flags_cache.clear()
        self._fresh_life()
        km._set_notify_all(True)                                        # the master bell: every card notifies
        km.define_board(dict(NOTES))                                    # notify ["new"], needsYou "new"
        self.n, self.step, self.before, self.during = 0, 0, None, None
        self.fired, self.pushed, self.badges = [], [], []
        self.patches = [mock.patch.object(km, "build_feed", self._build), mock.patch.object(km, "_task_tracking_on", lambda: True),
                        mock.patch.object(km, "_system_notify", lambda t, b: self.fired.append((t, b))),
                        mock.patch.object(km, "_push_notify", lambda *a, **k: self.pushed.append((a, sorted(k.items())))),
                        mock.patch.object(km, "_push_forward", lambda events: None),
                        mock.patch.object(km, "_badge_push", self.badges.append),
                        mock.patch.object(km, "_buzz_claim", lambda sid, key, kind: True),
                        mock.patch.object(km, "_turn_end_key", lambda sid, reg=None: 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        (built, km._feed_needs_input[0], km._feed_needs_rows[0], km._feed_needs_input_count[0],
         km._NOTIFY_PREV[0], km._NOTIFY_PREV_DISK[0], km._NOTIFY_BOARD[0]) = self.saved
        km._built_feed[:] = built
        km._notify_cards_cache.clear(); km._flags_cache.clear(); km._pusher_wake.clear()
        self.w.close()

    @staticmethod
    def _fresh_life():
        """A kernel life before its first feed build: no snapshot in memory or on disk, no build stored."""
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._feed_needs_input[0] = km._feed_needs_rows[0] = km._feed_needs_input_count[0] = None
        km._NOTIFY_PREV[0] = None; km._NOTIFY_PREV_DISK[0] = None; km._NOTIFY_BOARD[0] = frozenset()
        (km.jd.STATE / "notify-prev.json").unlink(missing_ok=True)

    def _build(self, now, live_map=None):
        """build_feed's stand-in: this step's cards, and the frame's boards read as build_feed reads them (the build's first
        ask), with a hook before that read and one after it."""
        if self.before is not None:
            self.before()
        boards = km._boards_data()
        if self.during is not None:
            self.during()
        return {"type": "feed", "asks": _many_cards(self.n, self.step), "items": [], "working": [], "awaiting": [], "stateUnknown": [],
                "sessions": [{"sid": s, "name": nm} for s, nm in _SESSIONS], "now": now, "boards": boards}

    def _push(self, now):
        """One pusher feed build through the single-flight door (_cached_feed), the stored build cleared so it builds."""
        km._built_feed[:] = [None, None, 0.0, 0.0]
        feed = km._cached_feed(now, {}, "sig-%d" % now)
        self.assertIsNone(getattr(km._BOARDS_BUILD, "read", None), "the build's read ends with the build")
        return feed

    def _run(self, steps, n=90):
        """The builds of `steps` from a fresh life: per build, what it rang, pushed and counted, and the snapshot it left."""
        self._fresh_life()
        self.n, out = n, []
        for k, step in enumerate(steps):
            del self.fired[:], self.pushed[:], self.badges[:]
            self.step = step
            feed = self._push(1000 + 10 * k)
            out.append({"fired": list(self.fired), "pushed": list(self.pushed), "badge": list(self.badges),
                        "needs": km._feed_needs_input[0], "rows": km._feed_needs_rows[0], "counts": km._feed_needs_input_count[0],
                        "snapshot": json.loads(json.dumps(km._NOTIFY_PREV[0])), "boards": feed["boards"]})
        return out

    def test_a_build_lists_the_boards_directory_twice_whatever_the_card_count(self):
        bdir, real, seen = str(km._board_dir()), os.listdir, []

        def listdir(p="."):
            if str(p) == bdir:
                seen.append(p)
            return real(p)

        for n in (30, 300):
            self._fresh_life()
            self.n = n
            for k, step in enumerate((0, 1)):                       # the silent seed, then a build that rings
                del seen[:], self.fired[:]
                self.step = step
                with mock.patch.object(km.os, "listdir", listdir):
                    self._push(2000 + 10 * k)
                self.assertEqual(len(seen), 2, "%d cards, build %d: the boards directory listed twice (the build's read and its closing "
                                               "re-key), not once per card and question" % (n, k))
            self.assertTrue(self.fired, "the second build rang: the bell diff ran its per-card reads")

    def test_the_bells_pushes_badge_rows_and_snapshot_are_the_per_card_reads(self):
        steps = (0, 1, 2, 2, 0)
        with mock.patch.object(km, "_boards_read_once", contextlib.nullcontext):
            per_card = self._run(steps)                             # every read asks the store, as before the one read
        once = self._run(steps)
        self.assertEqual(once, per_card, "one read per build decides every bell, push, badge, row and snapshot entry as the per-card reads did")
        # not a comparison of empties: both boards' sets decided something
        bodies = [b for build in per_card for _t, b in build["fired"]]
        self.assertEqual(per_card[0]["fired"], [], "the first build of a life seeds silently")
        self.assertTrue(any(b.startswith("Needs you: result") for b in bodies), "a notes card entering new rings as needs-you (notes' badge category)")
        self.assertTrue(any(b.startswith("Completed: route") for b in bodies), "a feed goal entering completed rings")
        self.assertFalse(any(b.startswith("Completed: result") for b in bodies), "notes' done is outside its notify set: never rung")
        self.assertTrue(all(build["badge"] and build["badge"][0] > 0 for build in per_card))
        self.assertEqual(per_card[-1]["boards"], {"notes": NOTES})

    def test_an_edit_after_the_builds_read_marks_the_views_dirty_reaches_the_next_build_and_another_thread_reads_it_at_once(self):
        self.n, self.step = 2, 0                                    # a feed goal in working, a notes card in kept
        fp = km.jd.STATE / "boards" / "notes.json"
        self._push(3000)                                            # the silent seed
        dirt = len(self.w.dirty)
        # after the build's read, kept joins notes' notify set by a rewrite in place, and nothing in the build reads the store again
        self.during = lambda: fp.write_text(json.dumps(dict(NOTES, notify=["new", "kept"])))
        del self.fired[:]
        feed = self._push(3010)
        self.assertEqual(len(self.w.dirty), dirt + 1, "the build's closing re-key saw the edit and marked the views dirty once, after "
                                                      "the build started: the next check rebuilds instead of serving this frame until the bucket")
        self.assertEqual(feed["boards"]["notes"]["notify"], ["new"], "the frame's boards are the read the bells used")
        self.assertEqual(self.fired, [], "kept was outside the set this build read")
        self.during = None
        feed = self._push(3020)
        self.assertEqual(feed["boards"]["notes"]["notify"], ["new", "kept"])
        self.assertEqual(self.fired, [("Romp: api", "Completed: result 1 is in")], "the next build reads the edit, and the card in kept rings once")
        # an edit after the build started but before its first ask is that build's: the read waits for the ask, so a define's
        # file landing before it is read, and one landing after it is the closing re-key's (the dirty floor is the build's start)
        self.before = lambda: fp.write_text(json.dumps(NOTES))
        del self.fired[:]
        feed = self._push(3030)
        self.assertEqual(feed["boards"]["notes"]["notify"], ["new"], "the build read the store at its first ask, after the edit")
        self.assertEqual(self.fired, [])
        self.before = None
        # inside a build, its thread keeps the build's read while another thread and a fresh read see the file as it stands
        seen = {}

        def probe():
            fp.write_text(json.dumps(dict(NOTES, notify=["new", "kept"])))
            other = []
            t = threading.Thread(target=lambda: other.append(km._boards_data()["notes"]["notify"]))
            t.start(); t.join()
            seen["other thread"] = other[0]
            seen["this thread"] = km._boards()["notes"]["notify"]
            seen["fresh"] = km._boards_data(fresh=True)["notes"]["notify"]

        self.during = probe
        self._push(3040)
        self.assertEqual(seen, {"other thread": ["new", "kept"], "this thread": ["new"], "fresh": ["new", "kept"]},
                         "the build keeps its one read; another thread and a fresh read see the file as it stands")

    def test_the_doors_read_the_store_fresh_inside_a_build(self):
        scratch = dict(NOTES, id="scratch", title="Scratch")
        with km._boards_read_once():
            self.assertEqual(sorted(km._boards()), ["feed", "notes"], "the build's first ask takes its read, before scratch exists")
            self.assertIsNone(km.define_board(dict(scratch))[1])
            self.assertNotIn("scratch", km._boards(), "the build's readers keep its one read")
            km._notice_append(SID, _row(SID, "sweep", 1, 100, board="scratch", category="kept"))
            World.reset_memos()
            dropped = dict(scratch, categories=[c for c in scratch["categories"] if c["id"] != "kept"],
                           rules=[r for r in scratch["rules"] if r["category"] != "kept"])
            self.assertRegex(km.define_board(dropped)[1] or "", r"drops category 'kept', which still holds 1 standing card",
                             "define checks the definition the store holds, not the build's read")
            km._notice_append(SID, {"op": "expire", "key": "sweep", "rev": 1, "t": 200, "sid": SID})
            World.reset_memos()
            self.assertEqual(km.remove_board("scratch"), (True, None), "remove finds the board the store holds")
        self.assertNotIn("scratch", km._boards())

    def test_a_build_that_raises_releases_its_read(self):
        def boom():
            raise RuntimeError("a synthetic build failure")

        self.during = boom                                          # after the build's read
        km._built_feed[:] = [None, None, 0.0, 0.0]
        with self.assertRaises(RuntimeError):
            km._cached_feed(4000, {}, "sig-4000")
        self.assertIsNone(getattr(km._BOARDS_BUILD, "read", None), "the read ends with the build, even one that raised")
        (km.jd.STATE / "boards" / "notes.json").write_text(json.dumps(dict(NOTES, notify=["new", "kept"])))
        self.assertEqual(km._boards()["notes"]["notify"], ["new", "kept"], "this thread reads the store again after the failed build")


class TheDoors(unittest.TestCase):
    """POST /board and GET /boards in /watch's shape."""
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self): self.w = World()
    def tearDown(self): self.w.close()

    def _call(self, path, body=None, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode() or "{}"
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    def test_the_routes_answer_in_the_watch_shape(self):
        self.assertNotEqual(self._call("/board", NOTES, token=False)[0], 200, "no token: refused")
        self.assertNotEqual(self._call("/boards", token=False)[0], 200)
        st, r = self._call("/board", b"[]"); self.assertEqual(st, 400)
        st, r = self._call("/board", {}); self.assertEqual(st, 400); self.assertIn("board definition", r["error"])
        st, r = self._call("/board", dict(NOTES, id="feed")); self.assertEqual((st, r["ok"]), (200, False)); self.assertIn("code-defined", r["error"])
        st, r = self._call("/board", dict(NOTES, sort={"key": "age", "dir": "asc"})); self.assertEqual(r["ok"], False); self.assertIn("sort.key", r["error"])
        st, r = self._call("/board", NOTES); self.assertEqual((st, r["ok"]), (200, True)); self.assertEqual(r["board"], NOTES)
        st, r = self._call("/boards")
        self.assertEqual(st, 200)
        self.assertEqual([(b["id"], b["source"]) for b in r["boards"]], [("feed", "code"), ("notes", "data")])
        self.assertEqual({k: v for k, v in r["boards"][1].items() if k != "source"}, NOTES)
        st, r = self._call("/board", {"remove": "scratch"}); self.assertEqual((st, r["ok"]), (200, False)); self.assertIn("no board", r["error"])
        st, r = self._call("/board", {"remove": "notes"}); self.assertEqual((st, r), (200, {"ok": True}))
        st, r = self._call("/boards"); self.assertEqual([b["id"] for b in r["boards"]], ["feed"])
