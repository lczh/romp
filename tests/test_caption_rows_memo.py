#!/usr/bin/env python3
"""The captions mapping the builds share, and its drift index (2026-10-05).

_captions(fsid) folded every row of captions/<fsid>.jsonl into a fresh dict on every call (fifteen calls per timeline
build in the build-cost lab), and _seg_caption and _seg_work_caption scanned every row on every drifted-id miss (the
open segment's message caption, not written yet, missed on every build). The mapping is now held on the reader's
version of the file (its path, the entry's generation and base, the record count) and extended by the appended rows;
a drifted lookup goes through an index of the ids by _seg_key, in the rows' order.

What must not change is the answer. The code as it stood before is copied below as the REFERENCE, and every equality
here is against it, on synthetic captions files:
  CaptionsRoad  the served mapping equals the reference fold over the reader's records, row for row and in order, at
                every step of a road: appends, overwrites that change a row's grain, a partial line and its completion,
                an in-place rewrite of as many rows, another file renamed over this one, a truncation, a removal, the
                file back, an eviction; every drifted lookup equals the reference scan at every step; a mapping once
                served never changes;
  DriftIndex    seeded random files, appended in batches: every lookup (exact, drifted, of another segment, malformed)
                equals the reference scan, the nearest-t pick with its tie rule and grain filter for work captions and
                the first '#p' row in the rows' order for message captions; an id that is not a string makes the
                lookups scan, so even the reference's raise is the answer;
  Lanes         a lane's bars derived through the shared mapping equal the bars of the scanning path;
  Cost          what failed before the change: a second call serves the same object, a write raises, the open
                segment's message-caption miss no longer calls _seg_key once per row, and an append folds only the
                appended rows;
  Bound         the memo holds nothing the reader let go, and /perf memos.captionRows reports it.
The equality classes are green on the code before the change by construction (the reference is that code); the
mutants of this change turn them red. Synthetic data only: placeholder uuids and invented captions."""
import json
import os
import random
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time. This root is the module's own,
# outside the runner's belt, so it pins the per-session hosts off itself (CLAUDE.md, 2026-09-11).
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
with open(os.path.join(_XDG, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off\n")
km = load_source("romp_kernel_caption_rows_memo", os.path.join(BIN, "romp-kernel"))
em = km.em

SID = "11111111-2222-3333-4444-0000000ca9d1"     # this module's own synthetic sids
SID2 = "11111111-2222-3333-4444-0000000ca9d2"


# ── the REFERENCE: the code before 2026-10-05, verbatim but for the module prefix ─────────────────────────────────
def ref_fold(records):
    out = {}
    for o in records:
        if isinstance(o, dict) and o.get("id"):
            out[o["id"]] = o
    return out


def ref_seg_caption(caps, seg_id):
    hit = caps.get(seg_id + "#p")
    if hit is None:
        want = km._seg_key(seg_id + "#p")
        hit = next((v for k, v in caps.items() if k.endswith("#p") and km._seg_key(k) == want), None)
    return (hit or {}).get("caption", "")


def ref_seg_work_caption(caps, seg_id):
    hit = caps.get(seg_id)
    if hit is not None:
        return hit.get("caption", "")
    parts = seg_id.split(":") if seg_id else []
    if len(parts) < 3:
        return ""
    try:
        seg_t = int(parts[-2])
    except ValueError:
        return ""
    want = km._seg_key(seg_id)
    best = None
    for k, v in caps.items():
        if v.get("grain") != "segment" or km._seg_key(k) != want:
            continue
        d = abs((v.get("t") or 0) - seg_t)
        if best is None or d < best[0]:
            best = (d, v)
    return best[1].get("caption", "") if best else ""


def outcome(fn, *a):
    """What a call answered: ("ok", value), or ("raise", the exception's type name)."""
    try:
        return ("ok", fn(*a))
    except Exception as e:                       # noqa: BLE001  the reference's raise is part of the answer
        return ("raise", type(e).__name__)


def seg(t, h, sid=SID):
    return "%s:%d:%s" % (sid, t, h)


def row(sid_t_h, grain, t, caption, sid=SID, p=False):
    t0, h = sid_t_h
    return {"id": seg(t0, h, sid) + ("#p" if p else ""), "grain": grain, "t": t, "caption": caption}


def queries(caps):
    """Seg ids to look up: every id's own segment, drifted by a few amounts either way, the same times under a hash no
    row carries, and the malformed shapes the lookups guard against."""
    qs = set()
    for k in caps:
        if not isinstance(k, str):
            continue
        base = k[:-2] if k.endswith("#p") else k
        parts = base.split(":")
        if len(parts) < 3:
            qs.add(base)
            continue
        try:
            t = int(parts[-2])
        except ValueError:
            qs.add(base)
            continue
        for d in (0, 1, -1, 5, -5, 48, -48, 150, -150, 3600):
            qs.add("%s:%d:%s" % (parts[0], t + d, parts[-1]))
            qs.add("%s:%d:%s" % (parts[0], t + d, "0badc0de"))
    qs.update(["", "weird", "a:b", SID + ":notatime:aaaa1111", SID + ":1000:ffffffff"])
    return sorted(qs)


class _CaptionsBase(unittest.TestCase):
    """Each test's own captions directory (jd.CAPDIR rebound), every write stamped with a distinct mtime so the reader
    sees each version even when two have one size."""

    def setUp(self):
        self._capdir = km.jd.CAPDIR
        self._td = tempfile.TemporaryDirectory()
        km.jd.CAPDIR = Path(self._td.name) / "captions"
        km.jd.CAPDIR.mkdir()
        self.path = km.jd.CAPDIR / (SID + ".jsonl")
        self.mtime = 1_700_000_000
        self.served = []                         # (mapping, its items as served): a served mapping never changes

    def tearDown(self):
        for p in list(km.jd.CAPDIR.glob("*.jsonl")) + [self.path]:
            with em._JSONL_CACHE_LOCK:
                em._cache_pop_locked(str(p))
            getattr(km, "_CAPTION_ROWS_MEMO", {}).pop(str(p), None)
        km.jd.CAPDIR = self._capdir
        self._td.cleanup()

    def stamp(self, path=None):
        self.mtime += 7
        os.utime(path or self.path, (self.mtime, self.mtime))

    def write(self, rows, tail="", path=None):
        with open(path or self.path, "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows) + tail)
        self.stamp(path)

    def append(self, rows, tail="", path=None):
        with open(path or self.path, "a") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows) + tail)
        self.stamp(path)

    def decoded(self):
        """The file's complete lines decoded on their own, beside the reader (the road's premise: it saw this version)."""
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return []
        out = []
        for line in text.split("\n")[:-1]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def lookups_equal(self, label, caps):
        plain = dict(caps)
        for q in queries(caps):
            self.assertEqual(outcome(km._seg_caption, caps, q), outcome(ref_seg_caption, plain, q),
                             "%s: the message caption of %r" % (label, q))
            self.assertEqual(outcome(km._seg_work_caption, caps, q), outcome(ref_seg_work_caption, plain, q),
                             "%s: the work caption of %r" % (label, q))

    def check(self, label, lookups=True, earlier=False):
        caps = km._captions(SID)
        want = ref_fold(em._read_jsonl_incremental(str(self.path)))
        self.assertEqual(list(caps.items()), list(want.items()), "%s: the rows, their values and their order" % label)
        self.assertTrue(all(caps[k] is want[k] for k in want), "%s: the reader's own row objects" % label)
        self.assertEqual(want, ref_fold(self.decoded()), "%s: (the premise) the reader read this version" % label)
        self.served.append((caps, list(caps.items())))
        for m, items in self.served:
            self.assertEqual(list(m.items()), items, "%s: a mapping served earlier is as it was served" % label)
        if lookups:
            self.lookups_equal(label, caps)
        if earlier and len(self.served) > 1 and self.served[-2][0] is not caps:
            self.lookups_equal(label + " (the mapping served before it, still answering for its own rows)", self.served[-2][0])
        return caps


A, B, C, D, E = (1000, "aaaa1111"), (2000, "bbbb2222"), (3000, "cccc3333"), (4000, "dddd4444"), (5000, "eeee5555")


def road_rows():
    """The first version of the road's file: every collision the two lookup rules decide."""
    return [
        row(A, "segment", 1000, "first continue"),
        row((1600, A[1]), "segment", 1600, "second continue"),        # the same key as A: the nearest t decides
        row(A, "prompt", 1000, "gist of the first continue", p=True),
        row((1600, A[1]), "prompt", 1600, "gist of the second", p=True),  # two '#p' rows of one key: the first wins
        row(B, "segment", 2000, "tie, earlier row"),
        row((2100, B[1]), "segment", 2100, "tie, later row"),          # 2050 is 50 from both: the earlier row wins
        row(C, "segment", 3000, "a message caption of grain segment", p=True),   # '#p' of any grain
        row(D, "turn", 4000, "a turn rollup"),                         # a turn row never serves as a work caption
        row((4100, D[1]), "segment", 4100, "the turn's segment"),
        {"id": seg(5000, E[1]), "grain": "segment", "caption": "no time"},   # t absent reads as 0
        row((0, "ffff6666"), "segment", 0, "time zero"),
        {"id": "weird", "grain": "segment", "caption": "a non-conforming id"},
        {"grain": "segment", "caption": "no id"},                      # skipped by the fold
        {"id": "", "caption": "an empty id"},                          # skipped by the fold
    ]


class CaptionsRoad(_CaptionsBase):
    def test_the_served_mapping_and_every_lookup_equal_the_reference_at_every_step(self):
        self.write(road_rows(), tail='"a line that is not a row"\n[1, 2]\n')
        self.check("first read", earlier=True)
        self.check("unchanged", earlier=True)

        self.append([row((1990, B[1]), "segment", 1990, "a later row nearer 1995"),
                     row((900, A[1]), "prompt", 900, "a later '#p' of A's key", p=True),
                     row((6000, "abab7777"), "segment", 6000, "a new segment")])
        self.check("append: new ids under standing keys", earlier=True)

        self.append([row(B, "turn", 2000, "B's first row is a turn now"),          # leaves the work candidates
                     row(D, "segment", 4000, "D's turn row is a segment now"),     # joins them, at its first place
                     row((2100, B[1]), "segment", 2060, "the later tie row moved nearer")])
        self.check("append: overwrites that change a grain and a time", earlier=True)

        self.append([row((7000, "cdcd8888"), "segment", 7000, "the line before a partial one")],
                    tail='{"id": "%s", "grain": "segment", "t": 8000, "capt' % seg(8000, "efef9999"))
        self.check("append: a partial last line", earlier=True)
        with open(self.path, "a") as fh:
            fh.write('ion": "the partial line, completed"}\n')
        self.stamp()
        self.check("the partial line completed", earlier=True)

        text = self.path.read_text()            # as many rows and as many bytes, other captions: only the generation tells
        self.assertEqual(text.count('"first continue"'), 1)
        with open(self.path, "w") as fh:
            fh.write(text.replace('"first continue"', '"FIRST CONTINUE"'))
        self.stamp()
        self.check("an in-place rewrite of as many rows and bytes", earlier=True)

        other = self.path.with_name("incoming.tmp")
        renamed = [row((9000, "a1a1a1a1"), "segment", 9000, "from the renamed file"),
                   row((9050, "a1a1a1a1"), "prompt", 9050, "its gist", p=True)] + road_rows()[4:]
        self.write(renamed, path=other)
        os.replace(other, self.path)
        self.stamp()
        self.check("another file renamed over this one", earlier=True)

        with open(self.path) as fh:
            lines = fh.read().split("\n")
        with open(self.path, "w") as fh:
            fh.write("\n".join(lines[:3]) + "\n")
        self.stamp()
        self.check("a truncation", earlier=True)

        os.unlink(self.path)
        caps = self.check("the file removed", earlier=True)
        self.assertEqual(len(caps), 0)
        self.write(road_rows()[:4])
        self.check("the file back", earlier=True)

        with em._JSONL_CACHE_LOCK:
            em._cache_pop_locked(str(self.path))
        self.check("the reader's entry evicted, the file read again", earlier=True)
        self.append([row((9900, "b2b2b2b2"), "segment", 9900, "after the eviction")])
        self.check("an append after the eviction", earlier=True)


class DriftIndex(_CaptionsBase):
    HASHES = ["%08x" % (0x1000 * i + 0xabc) for i in range(12)]

    def _row(self, rnd, ids):
        if ids and rnd.random() < 0.3:
            k = rnd.choice(ids)                  # an overwrite: the same id, perhaps another grain or time
        else:
            k = seg(rnd.randrange(0, 20000), rnd.choice(self.HASHES)) + ("#p" if rnd.random() < 0.35 else "")
        t = rnd.choice([None, 0, rnd.randrange(0, 20000)]) if rnd.random() < 0.1 else rnd.randrange(0, 20000)
        r = {"id": k, "grain": rnd.choice(["segment", "segment", "turn", "prompt"]), "caption": "c%d" % rnd.randrange(10 ** 6)}
        if t is not None:
            r["t"] = t
        ids.append(k)
        return r

    def test_seeded_files_appended_in_batches_answer_as_the_scan_does(self):
        for seed in range(4):
            with self.subTest(seed=seed):
                if self.path.exists():
                    os.unlink(self.path)          # each seed's file read from byte zero
                with em._JSONL_CACHE_LOCK:
                    em._cache_pop_locked(str(self.path))
                rnd = random.Random(seed)
                ids = []
                self.write([self._row(rnd, ids) for _ in range(60)])
                self.check("seed %d, first read" % seed)
                for batch in range(4):
                    self.append([self._row(rnd, ids) for _ in range(rnd.randrange(1, 16))])
                    caps = self.check("seed %d, batch %d" % (seed, batch))
                    for _ in range(100):          # and random times under every hash, beyond the rows' own drifts
                        q = seg(rnd.randrange(-100, 20100), rnd.choice(self.HASHES))
                        self.assertEqual(km._seg_work_caption(caps, q), ref_seg_work_caption(dict(caps), q), q)
                        self.assertEqual(km._seg_caption(caps, q), ref_seg_caption(dict(caps), q), q)

    def test_ties_go_to_the_earlier_row_and_overwrites_keep_their_place(self):
        self.write([row((2100, B[1]), "segment", 2100, "inserted first, later in time"),
                    row(B, "segment", 2000, "inserted second, earlier in time")])
        caps = self.check("a tie")
        self.assertEqual(km._seg_work_caption(caps, seg(2050, B[1])), "inserted first, later in time",
                         "50 from both: the row met first in the rows' order")
        self.append([row((2100, B[1]), "segment", 2100, "the first row, overwritten")])
        caps = self.check("the first row overwritten")
        self.assertEqual(km._seg_work_caption(caps, seg(2050, B[1])), "the first row, overwritten",
                         "an overwrite keeps the id's first place")

    def test_the_first_message_caption_in_the_rows_order_wins(self):
        self.write([row((1600, A[1]), "turn", 1600, "the first '#p' row, of grain turn", p=True),
                    row(A, "prompt", 1000, "the second '#p' row, nearer in time", p=True)])
        caps = self.check("two '#p' rows of one key")
        self.assertEqual(km._seg_caption(caps, seg(1001, A[1])), "the first '#p' row, of grain turn",
                         "no time involved: the first row in the rows' order, of any grain")

    def test_an_id_that_is_not_a_string_makes_the_lookups_scan(self):
        # The reference raises AttributeError wherever its scan meets such an id: a '#p' miss with no match before
        # it, a work miss when its row is of grain segment. Placed after the matches, of grain turn, it lets some
        # lookups answer: the index must answer exactly those, and raise where the scan raised.
        self.write(road_rows()[:9] + [{"id": 5, "grain": "turn", "t": 10, "caption": "an int id"}]
                   + road_rows()[9:])
        self.check("an int id among the rows")
        self.append([{"id": True, "grain": "segment", "t": 20, "caption": "a boolean id"}])
        self.check("a boolean id of grain segment appended")

    def test_a_non_string_id_appended_after_the_index_was_built(self):
        self.write(road_rows())
        caps = self.check("strings only")
        self.assertEqual(km._seg_work_caption(caps, seg(1599, A[1])), "second continue")   # the index is built now
        self.append([{"id": 7, "grain": "turn", "t": 30, "caption": "an int id, appended"}])
        self.check("an int id appended")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rec(kind, t, uuid, parent, text):
    if kind == "user":
        return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
                "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


class Lanes(_CaptionsBase):
    def test_a_lane_drawn_through_the_shared_mapping_draws_the_scanning_paths_bars(self):
        now = 1_800_000_000
        t0 = now - 3600
        recs, parent = [], None
        for i, ask in enumerate(["add the notes endpoint", "continue", "continue", "and paginate the search"]):
            recs.append(_rec("user", t0 + 300 * i, "u%d" % i, parent, ask))
            recs.append(_rec("assistant", t0 + 300 * i + 20, "a%d" % i, "u%d" % i, "Done with step %d." % i))
            parent = "a%d" % i
        tpath = Path(self._td.name) / (SID + ".jsonl")
        tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))
        session = km._parse(str(tpath), SID, now)
        segs = [s for turn in session["turns"] for s in km._segs_seam(turn, None)]
        self.assertGreaterEqual(len(segs), 4)

        def drifted(sid_, secs):
            p = sid_.split(":")
            return "%s:%d:%s" % (p[0], int(p[1]) - secs, p[2])
        rows = []
        for i, s in enumerate(segs):
            rows.append({"id": drifted(s["id"], 48), "grain": "segment", "t": int(s["t"]) - 48, "caption": "work %d" % i})
            if i < len(segs) - 1:                 # the newest segment's message caption is not written yet
                rows.append({"id": drifted(s["id"], 48) + "#p", "grain": "prompt", "t": int(s["t"]) - 48,
                             "caption": "gist %d" % i})
        self.write(rows)
        saved = list(km._downtime)
        km._downtime[:] = []

        def both():                               # the shared mapping, and a cold fold of the reader's records
            cold = ref_fold(em._read_jsonl_incremental(str(self.path)))
            return (km._lane_segments(SID, session, {}, km._captions(SID), True, None),
                    km._lane_segments(SID, session, {}, cold, True, None))
        try:
            shared, scanned = both()
            last = segs[-1]                       # the newest segment's message caption lands: an append
            self.append([{"id": drifted(last["id"], 48) + "#p", "grain": "prompt", "t": int(last["t"]) - 48,
                          "caption": "gist %d" % (len(segs) - 1)}])
            shared2, scanned2 = both()
        finally:
            km._downtime[:] = saved
        for label, a, b in (("first read", shared, scanned), ("after an append", shared2, scanned2)):
            self.assertEqual(json.dumps(a[0]), json.dumps(b[0]), "%s: the bars" % label)
            self.assertTrue(a[4], "%s: the captioner's marks were derived" % label)
            self.assertEqual(json.dumps(a[4]), json.dumps(b[4]), "%s: the captioner's marks" % label)
        self.assertGreaterEqual(sum(1 for b in shared[0] if b.get("c") and b.get("m")), 3,
                                "the drifted ids resolved: the comparison exercised the drift lookups")
        self.assertTrue(any(b.get("c") and not b.get("m") for b in shared[0]), "and a message-caption miss")
        self.assertGreater(sum(1 for b in shared2[0] if b.get("m")), sum(1 for b in shared[0] if b.get("m")),
                           "the appended '#p' row reached its bar through the carried index")


class Cost(_CaptionsBase):
    def test_a_second_call_on_an_unchanged_file_serves_the_same_mapping(self):
        self.write(road_rows())
        first = km._captions(SID)
        self.assertIs(km._captions(SID), first, "nothing moved: no row is folded again")
        self.append([row((6000, "abab7777"), "segment", 6000, "a new segment")])
        second = km._captions(SID)
        self.assertIsNot(second, first, "an append is a new version")
        self.assertIs(km._captions(SID), second)

    def test_a_served_mapping_refuses_every_write(self):
        self.write(road_rows())
        caps = km._captions(SID)
        k = next(iter(caps))
        for label, op in (("set", lambda: caps.__setitem__("x", {})), ("del", lambda: caps.__delitem__(k)),
                          ("pop", lambda: caps.pop(k)), ("popitem", lambda: caps.popitem()),
                          ("clear", lambda: caps.clear()), ("update", lambda: caps.update({"x": {}})),
                          ("setdefault", lambda: caps.setdefault("x", {}))):
            with self.subTest(op=label):
                with self.assertRaises(TypeError):
                    op()
        with self.assertRaises(TypeError):
            caps |= {"x": {}}
        self.assertEqual(list(km._captions(SID).items()), list(ref_fold(em._read_jsonl_incremental(str(self.path))).items()))
        plain = dict(caps)
        plain["x"] = {"caption": "a copy is a plain dict"}
        self.assertNotIn("x", caps)

    def test_the_open_segments_message_caption_miss_does_not_call_seg_key_per_row(self):
        # the lab's shape (F1): every closed segment has a work and a '#p' row, the open one only its work row, so
        # its message caption misses on every build; the scan called _seg_key once per '#p' row on each of them
        n = 300
        rows = []
        for i in range(n):
            rows.append(row((1000 + 60 * i, "%08x" % (0x10000 + i)), "segment", 1000 + 60 * i, "work %d" % i))
            rows.append(row((1000 + 60 * i, "%08x" % (0x10000 + i)), "prompt", 1000 + 60 * i, "gist %d" % i, p=True))
        open_id = seg(1000 + 60 * n, "%08x" % (0x10000 + n))
        rows.append({"id": open_id, "grain": "segment", "t": 1000 + 60 * n, "caption": "the open segment's work"})
        self.write(rows)
        real, calls = km.jd._seg_key, [0]

        def counted(seg_id):
            calls[0] += 1
            return real(seg_id)

        def build():
            caps = km._captions(SID)
            self.assertEqual(km._seg_caption(caps, open_id), "")
            self.assertEqual(km._seg_work_caption(caps, open_id), "the open segment's work")

        with mock.patch.object(km.jd, "_seg_key", counted):
            build()                                   # the first build may index the rows once
            calls[0] = 0
            build()
            self.append([row((90000, "%08x" % 0x9999), "segment", 90000, "a caption for another segment")])
            build()
            build()
        self.assertLessEqual(calls[0], 6, "three builds and an appended row: about one _seg_key call each, where the "
                                          "scan made %d calls per build" % (n + 1))

    def test_an_append_folds_only_the_appended_rows(self):
        self.write(road_rows())
        km._captions(SID)
        real, folded = km._caption_rows_fold, []

        def fold(out, records):
            folded.append(len(records))
            return real(out, records)

        with mock.patch.object(km, "_caption_rows_fold", fold):
            self.append([row((6000, "abab7777"), "segment", 6000, "one"), row((6100, "abab7777"), "segment", 6100, "two")])
            km._captions(SID)
            km._captions(SID)
        self.assertEqual(folded, [2], "the two appended records, once")


class Bound(_CaptionsBase):
    def test_the_memo_lets_go_of_what_the_reader_let_go(self):
        other = km.jd.CAPDIR / (SID2 + ".jsonl")
        self.write(road_rows())
        self.write(road_rows()[:3], path=other)
        km._captions(SID)
        km._captions(SID2)
        self.assertIn(str(self.path), km._CAPTION_ROWS_MEMO)
        swept = km._CAPTION_ROWS_STATS["swept"]
        with em._JSONL_CACHE_LOCK:
            em._cache_pop_locked(str(self.path))      # the record cache lets this file go (its budget, its count cap)
        self.append([row((6000, "abab7777"), "segment", 6000, "a new row")], path=other)
        km._captions(SID2)                            # a new version is held: the sweep runs
        self.assertNotIn(str(self.path), km._CAPTION_ROWS_MEMO, "no rows held past the reader's eviction")
        self.assertIn(str(other), km._CAPTION_ROWS_MEMO)
        self.assertEqual(km._CAPTION_ROWS_STATS["swept"], swept + 1)

    def test_a_removed_file_holds_nothing(self):
        self.write(road_rows())
        km._captions(SID)
        os.unlink(self.path)
        self.assertEqual(len(km._captions(SID)), 0)
        self.assertNotIn(str(self.path), km._CAPTION_ROWS_MEMO)

    def test_perf_reports_the_memo(self):
        self.write(road_rows())
        before = dict(km._caption_rows_report())
        caps = km._captions(SID)
        km._captions(SID)
        km._seg_work_caption(caps, seg(1599, A[1]))
        rep = km._PerfStats().snapshot()["memos"]["captionRows"]
        self.assertEqual(set(rep), {"entries", "rows", "bytes", "hit", "extend", "build", "uncached", "swept",
                                    "indexBuild", "indexLookup", "scanLookup"})
        self.assertTrue(all(type(v) is int for v in rep.values()), rep)
        self.assertGreaterEqual(rep["entries"], 1)
        self.assertGreaterEqual(rep["rows"], len(caps))
        self.assertEqual(rep["build"] - before["build"], 1)
        self.assertEqual(rep["hit"] - before["hit"], 1)
        self.assertEqual(rep["indexBuild"] - before["indexBuild"], 1)
        self.assertEqual(rep["indexLookup"] - before["indexLookup"], 1)
        self.assertEqual(rep["scanLookup"] - before["scanLookup"], 0, "the builds' mappings never scan")

    def test_the_reference_names_every_key(self):
        doc = (Path(HERE).parent / "docs" / "reference.md").read_text(encoding="utf-8")
        at = doc.index("`captionRows` is the captions mapping")
        passage = doc[at:doc.index("Four memos cover", at)]
        for k in km._caption_rows_report():
            self.assertIn("`%s`" % k, passage, k)


if __name__ == "__main__":
    unittest.main()
