#!/usr/bin/env python3
"""build_feed's fold keeps its cards (2026-10-15): a rebuild hands out the same card objects for the cards that did not
change, so the push's per-entry pass encodes only what changed.

Before, every feed build decoded every session's memoized entry string, stamped a fresh age tint on every card and tree
row, and handed the push a new object per card; the push's per-entry pass (_delta_split) serves an entry's string only
for the SAME object, so it encoded every card on the board again, every build. The fold now keeps, per session, the
string it folded and the cards it made, each beside its stamp (the tints, a placeholder's clock, the bell pair), and
hands out the kept object while the stamp stands.

Two classes:
  * OracleBothWays: a synthetic board of many cards built twice per step, once warm (the kept cards and the wire's
    memo carried from step to step) and once from scratch (both emptied, so every card is decoded, stamped and encoded
    afresh, as before the change), across steps that change cards, cross tint boundaries (the bright end, a mid-ramp
    step, the dark end), date placeholders by an int and then an equal float clock, move bells, remove cards, mute,
    depart and return sessions, change the colormap and run the clock backwards. Every step's frame, its per-entry
    strings, and each string against a fresh encode of the very object handed out, must be identical.
  * ReencodesCount: what a rebuild encodes, counted from the wire memo's own pairs (a pair the previous split did not
    hold was encoded this split), so the count is the same measurement on a kernel without the change: there an
    unchanged rebuild encodes every card; here none, a tint step encodes exactly the cards whose bytes moved, and a
    re-derived session encodes its own cards alone. /perf's counters (builds.feed.memo fold* and wire*) agree.

Harness: tests/test_feed_session_memo.py's world (a hermetic state root the kernel's judge is rebound to, names
entries and transcripts discover finds, a fixed live map), with the per-session derivation replaced by synthetic
entries (_feed_session_key and _feed_session_entry patched; the memo, the fold, the cross-session sections and the
push's split run for real), so the board can hold many cards with trees, placeholders and serving folds. Synthetic
fixtures only: private synthetic sids, the notes-api demo's session names, invented text.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_feed_fold_reuse", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# This module's PRIVATE synthetic sids (the goal-store fixture rule; nothing here mints goals, but the convention holds).
SIDS = tuple("3b7e9a10-6c2d-4f81-9e5a-c0ffee0a%04d" % i for i in range(1, 7))
WEB, API, TESTS, WEB2, API2, TESTS2 = SIDS
NAME_OF = dict(zip(SIDS, ("web", "api", "tests", "web-2", "api-2", "tests-2")))
NOW = 1781100000
T0 = NOW - 3600
FADE_LO, FADE_HI = km.cm.FADE_LO, km.cm.FADE_HI
# Ages in seconds at NOW, chosen to sit on both sides of tint steps: inside the bright clamp and just under its edge, the
# fast-moving first minutes, hours, days, and just under the dark clamp's edge. A card's tree rows take ages from the
# same list, and some rows have none (`_ageT` None: tinted as brand new).
AGES = (30, FADE_LO - 3, FADE_LO + 1, 200, 611, 1800, 3600, 7300, 86400, 3 * 86400, FADE_HI - 5, FADE_HI + 60)


def _key_for(version):
    """A key of the memo's shape (one position per labelled component) that moves only with the world's version."""
    return (version,) + (None,) * (len(km._FEED_MEMO_LABELS) - 1)


class _World:
    """The synthetic board: per session its entry (cards with `_ageT` epochs, serving-fold candidates, dot names) and a
    version the patched key returns, bumped by every edit, so the memo re-derives exactly the edited sessions."""

    def __init__(self, cards_per_session=8, serving=True):
        self.version = {sid: 1 for sid in SIDS}
        self.muted = set()
        self.entries = {}
        for k, sid in enumerate(SIDS):
            cards = [self._card(sid, i, AGES[(i + k) % len(AGES)], k) for i in range(cards_per_session)]
            self.entries[sid] = {"asks": cards, "working": NAME_OF[sid] if k % 2 else None,
                                 "awaiting": NAME_OF[sid] if k == 3 else None,
                                 "bgServices": [{"desc": "notes-api dev server", "since": T0}] if k == 1 else None,
                                 "servingFolds": [], "heal": 0, "hidden": 0, "cold": False, "peers": [], "reads": {},
                                 "faults": 0}
        # a placeholder card (no turn to date it: the build's clock dates it) on the tests session
        ph = self._card(TESTS, 90, None, 0)
        ph["provisional"] = True
        self.entries[TESTS]["asks"].append(ph)
        if serving:
            # web's first card carries a handoff row; api holds a candidate for it (joined under that row) and one whose
            # tracker is on no card (it keeps its own card)
            self.entries[WEB]["asks"][0]["tree"].append({"id": "dispatch-notes-1", "parentId": WEB + ":g0", "kind": "handoff",
                                                         "text": "hand the list endpoint to api", "status": "open",
                                                         "_ageT": NOW - 400, "children": []})
            self.entries[API]["servingFolds"] = [
                {"tracker": "dispatch-notes-1", "card": self._card(API, 50, NOW - 250, 1)},
                {"tracker": "dispatch-notes-gone", "card": self._card(API, 51, NOW - 5000, 2)}]

    @staticmethod
    def _card(sid, i, age, k):
        iid = "%s:g%d" % (sid, i)
        age_t = None if age is None else NOW - age
        rows = [{"id": iid, "parentId": None, "kind": "goal", "text": "notes-api goal %d" % i, "status": "open",
                 "_ageT": age_t, "children": [iid + "/r1"]}]
        for j in range(1, 1 + (i + k) % 4):
            rage = AGES[(i * 3 + j + k) % len(AGES)]
            rows.append({"id": "%s/r%d" % (iid, j), "parentId": iid, "kind": "goal", "text": "step %d of goal %d" % (j, i),
                         "status": "done" if j % 2 else "open",
                         "_ageT": None if (i + j) % 5 == 0 else NOW - rage, "children": []})
        return {"itemId": iid, "sid": sid, "name": NAME_OF[sid], "color": "#1EA1EB", "text": "notes-api goal %d" % i,
                "t": age_t if age_t is not None else 0, "live": True, "_ageT": age_t, "turnId": "turn-%d" % i,
                "origin": None, "summary": "where goal %d stands" % i, "blocked": None,
                "column": "working", "board": "feed", "category": "working", "tree": rows}

    def edit(self, sid, fn):
        fn(self.entries[sid])
        self.version[sid] += 1

    def key(self, s, tm, ctx, prev):
        return _key_for(self.version[s["sid"]])

    def entry(self, s, ctx):
        if s["sid"] in self.muted:
            return None
        return json.loads(json.dumps(self.entries[s["sid"]]))   # a fresh object per derivation, as a real one is


class _Board(unittest.TestCase):
    CARDS = 8
    SERVING = True

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        proj, names = td / "projects", td / "names"
        names.mkdir()
        for sid in SIDS:
            cdir = td / ("launch-" + NAME_OF[sid])
            cdir.mkdir()
            pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            pdir.mkdir(parents=True)
            (pdir / (sid + ".jsonl")).write_text(self._transcript(sid))
            (names / sid).write_text("%s\t%s\t%s\n" % (NAME_OF[sid], cdir, "#1EA1EB"))
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD)
        jd._rebind_state(td)
        jd.PROJECTS = proj
        km.NAMES = jd.NAMES
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        km._live_scope.names = None
        km._live_scope.snapshot = None
        self.live = {sid: {"state": "idle", "since": NOW - 100, "model": "", "effort": "", "context": None,
                           "compactPct": None, "color": None} for sid in SIDS}
        self.world = _World(self.CARDS, self.SERVING)
        for name, fn in (("_feed_session_key", self.world.key), ("_feed_session_entry", self.world.entry),
                         ("_feed_key_with_deps", lambda key, ctx, entry: key)):
            p = mock.patch.object(km, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        self._clear_caches()
        jd._discover_cache.clear()
        km.build_feed(NOW - 1, self.live)        # the first sight adopts every session into the persisted order
        self._clear_caches()

    def tearDown(self):
        self._clear_caches()
        jd._rebind_state(self.saved[0])
        jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD = self.saved[1:]
        km._live_scope.names = None
        km._live_scope.snapshot = None
        jd._discover_cache.clear()
        self.td.cleanup()

    @staticmethod
    def _transcript(sid):
        def iso(t):
            import datetime
            return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return "\n".join(json.dumps(r) for r in [
            {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "start on the notes-api list endpoint"}},
            {"type": "assistant", "timestamp": iso(T0 + 40), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}], "stop_reason": "end_turn"}},
        ]) + "\n"

    @staticmethod
    def _clear_caches():
        """Every cache a fold or a split reads: the session memo, the fold's kept cards (absent on a kernel without
        them), and the wire's split memos."""
        with km._feed_memo_lock:
            km._feed_memo.clear()
            km._FEED_MEMO_STATS["bytes"] = km._FEED_MEMO_STATS["entries"] = 0
        getattr(km, "_FEED_FOLD", {}).clear()
        km._delta_entry_memo.clear()
        km._delta_split_memo.clear()
        km._delta_parts_cache.clear()

    @staticmethod
    def _wire_caches():
        return (dict(getattr(km, "_FEED_FOLD", {})), dict(km._delta_entry_memo), dict(km._delta_split_memo),
                dict(km._delta_parts_cache))

    @staticmethod
    def _wire_restore(saved):
        for live, kept in zip((getattr(km, "_FEED_FOLD", {}), km._delta_entry_memo, km._delta_split_memo,
                               km._delta_parts_cache), saved):
            live.clear()
            live.update(kept)

    @staticmethod
    def _strings(parts):
        """The per-entry strings of the feed's cards in key order, as the wire carries them."""
        ents, order = parts[0]["asks"]
        return [(k, ents[k][1]) for k in order], ents

    def _split(self, feed):
        """The push's per-entry pass over this build's cards, and how many cards it ENCODED: a pair in the wire memo the
        previous split did not hold was minted this split (a hit hands back the previous pair itself)."""
        before = dict(km._delta_entry_memo.get(("feed", "asks"), {}))
        held = {id(p) for p in before.values()}
        parts = km._delta_parts("feed", feed)
        cur = km._delta_entry_memo[("feed", "asks")]
        encoded = sum(1 for p in cur.values() if id(p) not in held)
        del before
        return parts, encoded

    def _bell(self, cards=None, master=None, sessions=None):
        d = dict(cards or {})
        if master:
            d[km.NOTIFY_ALL_KEY] = True
        (jd.STATE / "notify-cards.json").write_text(json.dumps(d, sort_keys=True))
        (jd.STATE / "session-flags.json").write_text(json.dumps({s: {"notify": v} for s, v in (sessions or {}).items()}))


class OracleBothWays(_Board):
    """The warm build (kept cards, the wire's memo carried over) equals a from-scratch build at every step, frame and
    per-entry strings alike, and every string the wire served is the encode of the object handed out."""

    _prev = None

    def _both(self, now, what):
        feed_a = km.build_feed(now, self.live)
        parts_a, _enc = self._split(feed_a)
        if self._prev is not None:
            self.assertEqual(json.dumps(self._prev[0], default=str), self._prev[1],
                             "%s: building this frame wrote a card of the previous one" % what)
        warm = self._wire_caches()
        self._wire_restore(({}, {}, {}, {}))     # from scratch: nothing kept, nothing in the wire's memo
        try:
            feed_b = km.build_feed(now, self.live)
            parts_b, _ = self._split(feed_b)
        finally:
            self._wire_restore(warm)
        self.assertEqual(json.dumps(feed_a, default=str), json.dumps(feed_b, default=str),
                         "%s: the warm frame differs from a from-scratch build" % what)
        sa, ents_a = self._strings(parts_a)
        sb, _ = self._strings(parts_b)
        self.assertEqual(sa, sb, "%s: the per-entry strings differ from a from-scratch split" % what)
        enc = json.JSONEncoder(default=str).encode
        for k, s in sa:
            self.assertEqual(s, enc(ents_a[k][0]),
                             "%s: the string served for %s is not the encode of the card handed out" % (what, k))
        self._prev = (feed_a, json.dumps(feed_a, default=str))
        return feed_a

    def test_every_step_builds_the_same_frame_both_ways(self):
        w = self.world
        f = self._both(NOW, "cold")
        self.assertGreater(len(f["asks"]), 40, "a board of many cards")
        tracker = next(a for a in f["asks"] if a["itemId"] == WEB + ":g0")
        self.assertIn(API + ":g50", [r["id"] for r in tracker["tree"]], "the join hosted the candidate under its tracker")
        self.assertIn(API + ":g51", [a["itemId"] for a in f["asks"]], "...and the one without a tracker kept its own card")
        self.assertFalse([a["itemId"] for a in f["asks"] if "_ageT" in a], "the fold pops every card's tint epoch")
        self._both(NOW, "unchanged")
        self._both(NOW + 1, "one second on")
        self._both(NOW + 4, "the cards just under the bright clamp's edge cross it")
        self._both(NOW + 9, "the dark clamp's edge crossed")
        self._both(NOW + 9.0, "the same clock as a float: a placeholder's date encodes differently")
        self._both(NOW + 9, "...and back to the int")
        w.edit(API, lambda e: e["asks"][2].update(text="notes-api goal 2, reworded"))
        self._both(NOW + 9, "one card's text changed")
        w.edit(TESTS, lambda e: e["asks"].pop(3))
        self._both(NOW + 10, "a card removed")
        w.edit(API, lambda e: e["servingFolds"].pop(1))
        self._both(NOW + 10, "a serving candidate gone")
        self._bell({API + ":g51": True, WEB + ":g0": True, TESTS + ":g1": False})
        self._both(NOW + 10, "bells armed on a session card, a joined card and a muted one")
        w.edit(API, lambda e: e["servingFolds"].append(
            {"tracker": "dispatch-notes-gone", "card": _World._card(API, 51, NOW - 5000, 2)}))
        self._bell({API + ":g51": True, WEB + ":g0": True}, master=True, sessions={API2: False})
        self._both(NOW + 10, "the master on, a session muted, the own-card candidate back with its bell armed")
        self._bell({API + ":g51": False}, sessions={API2: True})
        self._both(NOW + 11, "bells moved again")
        w.edit(WEB2, lambda e: None)                  # re-derived, the same entry: the kept cards serve by equal string
        self._both(NOW + 11, "a re-derivation that changed nothing")
        w.muted.add(TESTS2)
        w.version[TESTS2] += 1
        self._both(NOW + 12, "a session muted (its entry is null)")
        w.muted.discard(TESTS2)
        w.version[TESTS2] += 1
        self._both(NOW + 12, "...and unmuted")
        gone = self.live.pop(API2)
        self._both(NOW + 13, "a session departed")
        self.live[API2] = gone
        self._both(NOW + 13, "...and returned")
        (jd.STATE / "colormap").write_text("viridis")
        self._both(NOW + 14, "another colormap")
        self._both(NOW + 5, "the clock read backwards (an earlier build's moment)")
        for step in range(1, 6):
            self._both(NOW + 14 + 37 * step, "minutes passing, step %d" % step)


class ReencodesCount(_Board):
    """What a rebuild encodes and decodes, counted. No serving candidates here: the join's copies are new objects every
    build by design, so they would add a fixed count to every rebuild."""
    SERVING = False

    def _build(self, now):
        feed = km.build_feed(now, self.live)
        parts, encoded = self._split(feed)
        return feed, encoded

    @staticmethod
    def _card_bytes(feed):
        return {a["itemId"]: json.dumps(a) for a in feed["asks"]}

    def test_a_rebuild_encodes_only_the_cards_whose_bytes_moved(self):
        f0, enc0 = self._build(NOW)
        n = len(f0["asks"])
        self.assertGreater(n, 40)
        self.assertEqual(enc0, n, "a cold build encodes every card")
        r0 = km._feed_memo_report()
        f1, enc1 = self._build(NOW)
        self.assertEqual(self._card_bytes(f1), self._card_bytes(f0))
        self.assertEqual(enc1, 0, "an unchanged rebuild encodes no card (before the fold kept its cards: every one)")
        f2, enc2 = self._build(NOW + 4)
        before, after = self._card_bytes(f1), self._card_bytes(f2)
        moved = sorted(k for k in after if after[k] != before[k])
        self.assertTrue(0 < len(moved) < n, "the step moves some tints and not others: %d of %d" % (len(moved), n))
        self.assertEqual(enc2, len(moved), "a tint step encodes exactly the cards whose bytes moved")
        self.world.edit(API, lambda e: e["asks"][0].update(text="notes-api goal 0, reworded"))
        f3, enc3 = self._build(NOW + 4)
        api_cards = [a for a in f3["asks"] if a["sid"] == API]
        self.assertEqual(enc3, len(api_cards), "a re-derived session encodes its own cards alone")
        r1 = km._feed_memo_report()
        d = {k: r1[k] - r0[k] for k in ("foldKept", "foldStamped", "foldFresh", "foldDecodes", "wireEncoded", "wireReused")}
        self.assertEqual(d["foldDecodes"], 1, "one entry string decoded: the re-derived session's")
        self.assertEqual(d["foldFresh"], len(api_cards))
        self.assertEqual(d["foldStamped"], len(moved), "the tint step's cards, re-stamped onto copies")
        self.assertEqual(d["foldKept"], n + (n - len(moved)) + (n - len(api_cards)))
        self.assertEqual(d["wireEncoded"], enc1 + enc2 + enc3, "the report counts what the split encoded")
        self.assertEqual(d["wireReused"], 3 * n - (enc1 + enc2 + enc3))

    def test_each_sessions_bell_flag_is_read_once_per_build(self):
        """The fold stamps a bell pair on every card every build, so the session's own flag is read through the build's
        reader, once per session (before: twice per card, each a stat of the flags file)."""
        self._bell(sessions={API: True, WEB2: False})
        self._build(NOW)
        real, calls = km._session_flag_raw, []
        with mock.patch.object(km, "_session_flag_raw", side_effect=lambda sid, flag: calls.append((sid, flag)) or real(sid, flag)):
            f, _ = self._build(NOW + 4)
        self.assertEqual(sorted(sid for sid, flag in calls if flag == "notify"), sorted(SIDS),
                         "one read of each session's bell per build")
        by_sid = {a["sid"]: a["notifyRaw"] for a in f["asks"]}
        self.assertEqual((by_sid[API], by_sid[WEB2], by_sid[WEB]), (True, False, None), "...and the cards carry it")

    def test_a_kept_card_is_the_same_object_and_a_moved_one_a_new_object(self):
        f0, _ = self._build(NOW)
        f1, _ = self._build(NOW)
        self.assertTrue(all(a is b for a, b in zip(f0["asks"], f1["asks"])), "every card handed out again")
        s1 = json.dumps(f1["asks"])
        f2, _ = self._build(NOW + 4)
        moved = 0
        for a, b in zip(f1["asks"], f2["asks"]):
            if json.dumps(a) == json.dumps(b):
                self.assertIs(a, b, a["itemId"])
            else:
                self.assertIsNot(a, b, a["itemId"])
                moved += 1
        self.assertGreater(moved, 0)
        self.assertEqual(json.dumps(f1["asks"]), s1, "re-stamping wrote copies: the earlier build's cards stand as they were")


if __name__ == "__main__":
    unittest.main()
