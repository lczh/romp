#!/usr/bin/env python3
"""The courier's cross-host scan folds the postal log as it grows (2026-10-26).

run_courier plants a sender-side tracker for each delegate a local session sent to another host, read from the
postal log (timeline/messages.jsonl). The log only grows, yet the courier read all of it and decoded every line on
every triage pass, whether or not a row had landed since. The part of that scan that depends on the log alone (the
terminal rows that give a send back, and the sent cross-host delegates) is now an append-incremental fold over the
shared reader (_courier_xhost_rows); the pass's sessions and clock are applied after it.

Pinned two ways. Exactness: over a scripted life of the log (absent, created, unchanged, appended, a last line with
no newline yet, a torn line, rewritten shorter, longer and at the same size, deleted, recreated, and replaced between
two passes), at several session sets and clocks, the fold answers exactly what the whole-file scan answered (its code
is kept verbatim below as the oracle). Cost: end to end through run_courier, a second pass over an unchanged log
decodes no postal row, and a pass after an append decodes only the appended rows, with the same trackers planted.

Every write below stamps its own mtime, later than the one before, as writes at different moments do: the reader's
key is the file's stat (mtime and size, with the inode and a checksum deciding a same-size touch).

Synthetic fixtures only: placeholder sids, invented text, hostname TESTHOST; a temp root per test."""
import json
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_courier_xhost_fold", os.path.join(BIN, "romp-judge"))

A = "11111111-2222-3333-4444-888888888801"
B = "11111111-2222-3333-4444-888888888802"
C = "11111111-2222-3333-4444-888888888803"
RSID = "eeeeeeee-ffff-0000-1111-888888888804"   # a remote recipient's sid
T0 = 1_781_100_000
H = None   # COURIER_RETRY_HORIZON, read at setUp


def oracle(path, live_ids, now):
    """run_courier's cross-host scan before the fold, verbatim: the whole log read and decoded on every call."""
    xback = {}
    try:
        xrows = []
        for line in Path(path).read_text(errors="replace").splitlines():
            try:
                o = json.loads(line)
            except Exception:
                continue
            jd._learn_return(xback, o)
            if (o.get("ev") == "sent" and o.get("kind") == "delegate" and o.get("id")
                    and o.get("from_id") in live_ids and o.get("toName")
                    and str(o.get("to_id") or "").startswith("peer:")
                    and now - (o.get("t") or 0) <= jd.COURIER_RETRY_HORIZON):
                xrows.append(o)
    except OSError:
        xrows = []
    return [o for o in xrows if str(o["id"]) not in xback]


def xrow(i, frm, t, kind="delegate", host="TESTHOST-B", name="web", to_sid=None, body=None, mid=None):
    r = {"t": t, "ev": "sent", "id": mid or "px-%d.mail.TESTHOST" % i, "from": "api", "from_id": frm,
         "to_id": "peer:%s" % host, "toName": "%s:%s" % (host, name), "kind": kind,
         "body": body or "own the exporter work, part %d" % i}
    if to_sid:
        r["to_sid"] = to_sid
    return json.dumps(r)


def local(i, frm, to, t, kind="delegate"):
    return json.dumps({"t": t, "ev": "sent", "id": "%d.%05d_%05d.TESTHOST" % (T0, i, i), "from": "api",
                       "from_id": frm, "to_id": to, "kind": kind, "body": "check the notes-api schema, part %d" % i})


def ev(kind, mid, t, **kw):
    return json.dumps(dict({"t": t, "ev": kind, "id": mid}, **kw))


def ids(rows):
    return [o["id"] for o in rows]


class _Root(unittest.TestCase):
    def setUp(self):
        global H
        H = jd.COURIER_RETRY_HORIZON
        self._saved_state = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        self.mt = T0 * 10**9                             # the next write's mtime (ns), one second after the last

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        self.td.cleanup()

    def _stamp(self):
        self.mt += 10**9
        os.utime(jd.MESSAGES, ns=(self.mt, self.mt))

    def write(self, text):
        with open(jd.MESSAGES, "w") as fh:                # in place: the same inode, truncated and rewritten
            fh.write(text)
        self._stamp()

    def append(self, text):
        with open(jd.MESSAGES, "a") as fh:
            fh.write(text)
        self._stamp()


class FoldMatchesTheWholeFileScan(_Root):
    """At every step of a scripted log life, for every session set and clock below, the fold's rows equal the
    whole-file scan's, in content and order; each step is asked twice, so the second answer is the cursor's hit."""

    def views(self):
        return [({A, B}, T0 + 100), ({A}, T0 + 100), ({B, C}, T0 + 100),
                ({A, B, C}, T0 + 12 + H),                 # the horizon falls inside the log: rows before T0 + 12 age out
                (set(), T0 + 100)]

    def check(self, step):
        """Compare at every view; the whole-file scan's answer for the first view ({A, B} at T0 + 100)."""
        first = None
        for live, now in self.views():
            for ask in ("first", "hit"):
                want = oracle(jd.MESSAGES, live, now)
                got = jd._courier_xhost_rows(live, now)
                self.assertEqual(got, want, "step %s, sessions %s, clock T0%+d, %s ask"
                                 % (step, sorted(s[-2:] for s in live), now - T0, ask))
            if first is None:
                first = want
        return first

    def test_every_step_of_a_scripted_log_life(self):
        self.assertEqual(self.check("absent"), [])
        rows = [xrow(1, A, T0 + 10), local(2, A, B, T0 + 11), xrow(3, B, T0 + 12, to_sid=RSID),
                ev("exec", "%d.%05d_%05d.TESTHOST" % (T0, 2, 2), T0 + 13),
                xrow(4, C, T0 + 14), xrow(5, A, T0 + 15, kind="coordinate"), xrow(6, A, T0 - 60),
                "not json at all", "", "   ", local(7, B, A, T0 + 16, kind="question")]
        self.write("\n".join(rows) + "\n")
        self.assertEqual(ids(self.check("created")), ["px-1.mail.TESTHOST", "px-3.mail.TESTHOST", "px-6.mail.TESTHOST"])
        self.check("unchanged")
        self.append(ev("bounced", "px-1.mail.TESTHOST", T0 + 20, why="refused") + "\n"
                    + ev("relayed", "px-3.mail.TESTHOST", T0 + 21, host="TESTHOST-B") + "\n"
                    + xrow(8, A, T0 + 22) + "\n"
                    + xrow(9, B, T0 + 23, mid="%d.00009_00009.TESTHOST" % T0) + "\n")
        self.assertEqual(ids(self.check("appended: a bounce gives an earlier row back")),
                         ["px-3.mail.TESTHOST", "px-6.mail.TESTHOST", "px-8.mail.TESTHOST",
                          "%d.00009_00009.TESTHOST" % T0])
        self.append(ev("recall", "px-8.mail.TESTHOST", T0 + 24) + "\n"                     # an outbox recall: not terminal
                    + ev("recall", "%d.00009_00009.TESTHOST" % T0, T0 + 25, box="new") + "\n")   # a maildir one: terminal
        self.assertEqual(ids(self.check("appended: two recalls")),
                         ["px-3.mail.TESTHOST", "px-6.mail.TESTHOST", "px-8.mail.TESTHOST"])
        self.append(xrow(10, B, T0 + 26))                 # a complete row whose newline has not landed yet
        self.assertIn("px-10.mail.TESTHOST", ids(self.check("a last line with no newline")))
        self.append("\n" + xrow(11, A, T0 + 27) + "\n")
        self.assertEqual(ids(self.check("the newline lands, and one more row"))[-2:],
                         ["px-10.mail.TESTHOST", "px-11.mail.TESTHOST"])
        torn = xrow(12, A, T0 + 28)
        self.append(torn[:40])                            # a torn write: half a row
        self.assertNotIn("px-12.mail.TESTHOST", ids(self.check("a torn last line")))
        self.append(torn[40:] + "\n")
        self.assertIn("px-12.mail.TESTHOST", ids(self.check("the torn row completes")))
        self.write("\n".join([xrow(13, A, T0 + 30), ev("bounced", "px-13.mail.TESTHOST", T0 + 31)]) + "\n")
        self.assertEqual(self.check("rewritten shorter"), [])
        self.write("\n".join([xrow(14, B, T0 + 32), local(15, A, C, T0 + 33), xrow(16, A, T0 + 34),
                              xrow(17, C, T0 + 35, body="own the importer work"), xrow(18, B, T0 + 36),
                              xrow(19, A, T0 + 37), ev("bounced", "px-18.mail.TESTHOST", T0 + 38)]) + "\n")
        self.assertEqual(ids(self.check("rewritten longer")),
                         ["px-14.mail.TESTHOST", "px-16.mail.TESTHOST", "px-19.mail.TESTHOST"])
        size = os.path.getsize(jd.MESSAGES)
        text = jd.MESSAGES.read_text().replace("px-14.mail", "px-41.mail").replace("px-18.mail", "px-81.mail", 1)
        self.write(text)                                  # the bounce now names a row the log never sent
        self.assertEqual(os.path.getsize(jd.MESSAGES), size, "a same-size rewrite")
        self.assertEqual(ids(self.check("rewritten at the same size")),
                         ["px-41.mail.TESTHOST", "px-16.mail.TESTHOST", "px-81.mail.TESTHOST", "px-19.mail.TESTHOST"])
        jd.MESSAGES.unlink()
        self.assertEqual(self.check("deleted"), [])
        self.write("\n".join([xrow(20, A, T0 + 40), xrow(21, B, T0 + 41)]) + "\n")
        self.assertEqual(ids(self.check("recreated")), ["px-20.mail.TESTHOST", "px-21.mail.TESTHOST"])
        jd.MESSAGES.unlink()                              # replaced between two passes: no read sees the gap
        self.write("\n".join([xrow(22, C, T0 + 42), ev("bounced", "px-22.mail.TESTHOST", T0 + 43),
                              xrow(23, A, T0 + 44)]) + "\n")
        self.assertEqual(ids(self.check("replaced")), ["px-23.mail.TESTHOST"])


class CourierDecodesOnlyWhatLanded(_Root):
    """End to end through run_courier: the trackers are the ones the whole-file scan planted, and the log's rows are
    decoded once, as they land."""

    def setUp(self):
        super().setUp()
        self._saved_discover = jd.discover
        nowhere = str(Path(self.td.name) / "none.jsonl")
        jd.discover = lambda now: [(A, nowhere, None, "api")]   # one local sender; the recipients are remote

    def tearDown(self):
        jd.discover = self._saved_discover
        super().tearDown()

    def courier_pass(self, now):
        """One courier pass; the number of postal rows it decoded (a decoded object carrying the log's `ev`)."""
        real, seen = json.loads, [0]

        def counting(s, *a, **k):
            o = real(s, *a, **k)
            if isinstance(o, dict) and "ev" in o:
                seen[0] += 1
            return o
        with mock.patch.object(json, "loads", counting):
            jd.run_courier(now=now)
        return seen[0]

    def trackers(self):
        st = jd.load_goals(A)
        return sorted(nd["handoff"]["msgId"] for nd in st["nodes"].values() if isinstance(nd.get("handoff"), dict))

    def test_an_unchanged_log_decodes_nothing_and_an_append_decodes_its_rows(self):
        rows = [xrow(1, A, T0 + 10)] + [local(100 + i, B, C, T0 + i) for i in range(40)] + [xrow(2, A, T0 + 11)]
        self.write("\n".join(rows) + "\n")
        self.assertEqual(self.courier_pass(T0 + 100), len(rows), "the first pass reads the log once")
        self.assertEqual(self.trackers(), ["px-1.mail.TESTHOST", "px-2.mail.TESTHOST"])
        self.assertEqual(self.courier_pass(T0 + 200), 0,
                         "an unchanged log is a stat (main decoded all %d rows again)" % len(rows))
        self.append(ev("bounced", "px-2.mail.TESTHOST", T0 + 210) + "\n" + xrow(3, A, T0 + 211) + "\n")
        self.assertEqual(self.courier_pass(T0 + 300), 2,
                         "an append decodes the rows it added (main decoded all %d)" % (len(rows) + 2))
        self.assertEqual(self.trackers(), ["px-1.mail.TESTHOST", "px-2.mail.TESTHOST", "px-3.mail.TESTHOST"],
                         "the new delegate plants; a tracker planted before its bounce stays, as before")
        self.assertEqual(self.courier_pass(T0 + 400), 0)


if __name__ == "__main__":
    unittest.main()
