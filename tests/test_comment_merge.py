#!/usr/bin/env python3
"""The comment thread's third exit (the user 2026-08-23): MERGE folds the discussion back into the
parent session — the exchange lands as the person's own handoff (_merge_body), the thread settles to
'merged', and its CLI shuts down like a resolve. Once per thread (the CAS refuses a re-merge), loud
when there is nothing to merge, and the latch reverts on a failed delivery. SYNTHETIC fixtures."""
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
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "11111111-2222-3333-4444-555555555555"
# how the relay opens each message (the record is tagged, the user 2026-10-06), and how one closes
ME, THEM, END = '<message from="me">\n', '<message from="the other assistant">\n', "\n</message>"
LEAD = "I took a side discussion with another assistant about this passage of your earlier work:"
CLOSE = ("Treat what we settled there as my direction: fold it into your understanding and account for it in everything "
         "you do on this from here on.")
TAIL = "<!-- romp-injected --><!-- romp-tag: relay -->"
TSID = "66666666-7777-8888-9999-000000000000"


class FakeBE:
    def __init__(self):
        self.sent, self.killed = [], []

    def send(self, sid, text):
        self.sent.append((sid, text))

    def kill(self, sid):
        self.killed.append(sid)


def _exchange(msgs, gap="", oldest=0):
    """A stand-in for _thread_exchange's answer: the thread's whole discussion `msgs`, and why part of it could not be read."""
    return lambda tsid, cut, floor_t=0: {"msgs": list(msgs), "capped": list(msgs), "sent": "", "gap": gap,
                                         "oldestT": oldest}


class CommentMerge(unittest.TestCase):
    def setUp(self):
        self.be = FakeBE()
        self._saved = (km.Sessions.backend_for, km._thread_exchange, km._push_soon)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.msgs = [{"who": "user", "text": "should the cache be write-through?", "t": 1},
                     {"who": "assistant", "text": "Yes; it removes the stale-read window.", "t": 2}]
        km._thread_exchange = _exchange(self.msgs)
        km._push_soon = lambda: None
        with km._comments_lock:
            km._save_comments(PARENT, {"threads": [{
                "tid": TSID, "sid": TSID, "name": "web-comment-1", "status": "open",
                "anchorUuid": "aaaabbbb-1111-2222-3333-444455556666", "anchorT": 100,
                "cutUuid": "aaaabbbb-1111-2222-3333-444455556666",
                "exact": "the caching layer", "createdT": 100, "lastSeenT": 100}]})

    def tearDown(self):
        km.Sessions.backend_for, km._thread_exchange, km._push_soon = self._saved
        with km._comments_lock:
            km._save_comments(PARENT, {"threads": []})

    def _row(self):
        return km._comment_thread(PARENT, TSID)

    def test_merge_delivers_the_handoff_and_settles_the_thread(self):
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual(len(self.be.sent), 1)
        sid, body = self.be.sent[0]
        self.assertEqual(sid, PARENT, "the handoff lands in the PARENT session")
        self.assertIn("<passage>\nthe caching layer\n</passage>", body, "grounded by the passage")
        self.assertIn(ME + "should the cache be write-through?" + END, body)
        self.assertIn(THEM + "Yes; it removes the stale-read window." + END, body)
        self.assertIn("account for it", body, "the going-forward direction rides the handoff")
        self.assertEqual(self._row().get("status"), "merged")
        self.assertEqual(self.be.killed, [TSID], "the CLI has nothing left; its work is folded back")

    def test_a_refused_handoff_reverts_the_latch_and_kills_nothing(self):
        # T315 (the commit-13 review's second item): the merge sent with the default and checked only for an
        # exception, so a parent whose backend refused the handoff was still marked merged and its thread's CLI
        # ended; the merge is the user's gesture (user=True when the send takes it) and a False reverts
        class Refusing(FakeBE):
            def send(self, sid, text, user=False):
                self.sent.append((sid, text, user)); return False
        self.be = Refusing()
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("refused", err or "")
        self.assertEqual(self._row().get("status"), "open", "nothing marked merged on a refusal")
        self.assertEqual(self.be.killed, [], "the thread's CLI lives on: its discussion is not folded back yet")
        self.assertEqual([u for _, _, u in self.be.sent], [True], "the merge speaks as the user")

    def test_nothing_to_merge_reverts_the_latch_loudly(self):
        km._thread_exchange = _exchange([])
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("no discussion to send back", err or "")
        self.assertEqual(self._row().get("status"), "open", "the latch never sticks on a refusal")
        self.assertEqual(self.be.sent, [])

    def test_failed_delivery_reverts_the_latch(self):
        self.be.send = lambda sid, text: (_ for _ in ()).throw(RuntimeError("backend down"))
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("could not be delivered", err or "")
        self.assertEqual(self._row().get("status"), "open")

    # A thread left in 'merging' is lost to the user: off the scroll rail, its popover stuck on "Relaying…" with no Relay
    # or Delete, and every reply and relay refused as "being relayed; give it a moment". The latch must be released on
    # every way out (the user 2026-09-30, whose relayed comment stopped showing).
    def test_a_read_that_raises_releases_the_latch(self):
        def unreadable(tsid, cut, floor_t=0):
            raise OSError("the thread's transcript is unreadable")
        km._thread_exchange = unreadable
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("unreadable", err or "", "loud: the reason reaches the user")
        self.assertEqual(self._row().get("status"), "open", "the latch never outlives the relay that took it")
        self.assertEqual(self.be.sent, [])

    def test_the_latch_records_the_status_it_replaced(self):
        seen = {}

        class Peek(FakeBE):
            def send(s2, sid, text):
                seen.update(km._comment_thread(PARENT, TSID))
                return True
        self.be = Peek()
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual((seen.get("status"), seen.get("mergePrior")), ("merging", "open"),
                         "mid-relay, the row names what a boot would give it back")

    def test_a_boot_releases_a_latch_no_relay_holds(self):
        # the kernel exited mid-relay: no relay survives a kernel's exit, so the boot is the event that frees the latch
        km._comment_update(PARENT, TSID, status="merging", mergePrior="resolved")
        self.assertEqual(km._comment_latch_boot_pass(), 1)
        self.assertEqual(self._row().get("status"), "resolved", "a closed thread stays closed")
        km._comment_update(PARENT, TSID, status="merging", mergePrior=None)   # a latch taken before the prior was recorded
        self.assertEqual(km._comment_latch_boot_pass(), 1)
        self.assertEqual(self._row().get("status"), "open")
        self.assertEqual(km._comment_latch_boot_pass(), 0, "nothing else is touched")
        self.assertEqual(self._row().get("status"), "open")

    def test_a_relayed_thread_stays_talkable_and_a_second_relay_sends_only_the_new_tail(self):
        # T145 (the user 2026-08-28): a relay does NOT close the thread — talking continues, and the
        # next relay carries only what the session hasn't seen (the evidence-time relayedT floor).
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual(len(self.be.sent), 1)
        self.assertGreater(self._row().get("relayedT") or 0, 0, "the sent-back stamp persists on the row")
        # nothing new yet → the CAS refuses in relay vocabulary, pointing at the reply path
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("already relayed", err or "")
        # a reply REOPENS the relayed thread exactly like a resolved one…
        self.be.fork = lambda *a, **k: None      # the reply path gates on the SDK shape (fork/resume)
        self.be.resume = lambda name, sid: None
        _send0 = self.be.send                    # the reply path checks send's truthiness; the fake returns None
        self.be.send = lambda sid, text: (_send0(sid, text) or True)
        self.assertIsNone(km._comment_reply(PARENT, TSID, "one more thought"))
        self.assertEqual(self._row().get("status"), "open")
        # …and the next relay sends ONLY messages past the floor
        base = self.msgs
        newer = base + [{"who": "user", "text": "one more thought", "t": (base[-1]["t"] if base else 0) + 10}]
        km._thread_exchange = _exchange(newer)
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual(len(self.be.sent), 3, "the relay + the reply + the tail-only relay")
        self.assertIn("one more thought", self.be.sent[-1][1])
        self.assertIn("<discussion>\n" + ME + "one more thought" + END + "\n</discussion>", self.be.sent[-1][1],
                      "the new tail rides the same tagged record, alone in its discussion")
        self.assertIn("<passage>\nthe caching layer\n</passage>", self.be.sent[-1][1], "still grounded by the passage")
        for m in base:
            if (m.get("text") or "").strip():
                self.assertNotIn(m["text"], self.be.sent[-1][1], "already-relayed content never repeats")

    def test_the_relay_arrives_machine_dressed_with_the_whole_exchange(self):
        # T145: the arrival wears the T130 machine attribution (markers), never a plain user bubble,
        # and the body carries BOTH sides of the exchange in full.
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        body = self.be.sent[0][1]
        self.assertIn("<!-- romp-injected --><!-- romp-tag: relay -->", body)
        self.assertNotIn("romp-system", body, "relayed content is a conversation, not a kernel status notice")
        for m in self.msgs:
            if (m.get("text") or "").strip():
                self.assertIn(m["text"], body, "the WHOLE exchange goes — no summary, no slice")

    def test_the_transcript_cap_cuts_the_middle_never_the_question(self):
        # T145 raised the cap to 48k (the whole exchange goes; 6k trimmed real discussions — the
        # user's 'only part merged'); the trim note remains the honest pathological backstop. It cuts
        # the MIDDLE (2026-10-05): the first message, the question the discussion is about, always goes
        body = km._merge_body("q", [{"who": "user", "text": "x" * 9000}])
        self.assertNotIn(km.MERGE_TRIM_NOTE, body, "9k is a normal discussion now — untrimmed")
        big = km._merge_body("q", [{"who": "user", "text": "why write-through?"}, {"who": "agent", "text": "x" * 60000},
                                   {"who": "user", "text": "and the cost?"}, {"who": "agent", "text": "One write each."}])
        self.assertIn(km.MERGE_TRIM_NOTE, big)
        self.assertIn("<discussion>\n" + ME + "why write-through?" + END + "\n" + km.MERGE_TRIM_NOTE + "\n", big,
                      "the question goes whole, the note right after it")
        self.assertIn(ME + "and the cost?" + END + "\n" + THEM + "One write each." + END + "\n</discussion>", big,
                      "the newest exchange goes whole")
        self.assertLess(len(big), 60000, "the pathological backstop still bounds the injection")

    def test_a_relay_missing_its_beginning_says_so_and_tells_the_person(self):
        km._thread_exchange = _exchange(self.msgs, gap="its record breaks off before its first message", oldest=1)
        notes = []
        self.assertIsNone(km._comment_merge(PARENT, TSID, notes=notes))
        body = self.be.sent[0][1]
        self.assertIn("<discussion>\n" + km.MERGE_GAP_NOTE + "\n" + ME + "should the cache be write-through?" + END, body,
                      "the note opens the discussion, before the first message; what could be read still goes")
        self.assertEqual(len(notes), 1)
        self.assertIn("without the start of the discussion: its record breaks off before its first message", notes[0])

    def test_a_discussion_with_nothing_readable_is_refused_with_the_reason(self):
        km._thread_exchange = _exchange([], gap="its transcript can't be read (No such file or directory)")
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("could not be read", err or "")
        self.assertIn("transcript can't be read", err or "")
        self.assertEqual(self._row().get("status"), "open")
        self.assertEqual(self.be.sent, [])

class TaggedRelayBody(unittest.TestCase):
    """The relay hands the discussion over as a TAGGED record (the user 2026-10-06, who found "Me:" / "Them:" hard to
    pick out among the messages' own headings and lists, and suspected the model did too): the passage whole in
    <passage>, each message in its own <message from="…"> inside <discussion>, the two notes about what was left out
    inside <discussion> where the hole is, and content that cannot close an element early."""

    Q = [{"who": "you", "text": "should the cache be write-through?"},
         {"who": "agent", "text": "## Yes\n\n- it removes the stale-read window\n- one write per update"}]

    def test_the_body_is_the_tagged_record(self):
        body = km._merge_body("the caching layer", self.Q)
        self.assertEqual(body, LEAD + "\n\n<passage>\nthe caching layer\n</passage>\n\n<discussion>\n"
                         + ME + "should the cache be write-through?" + END + "\n"
                         + THEM + "## Yes\n\n- it removes the stale-read window\n- one write per update" + END
                         + "\n</discussion>\n\n" + CLOSE + "\n\n" + TAIL)
        for gone in ("Here is how it went", "Me: ", "Them: ", "\n> "):
            self.assertNotIn(gone, body, "the tags carry who said what: %r" % gone)

    def test_the_passage_goes_whole(self):
        lines = ["line %d of the passage" % i for i in range(1, 11)]
        body = km._merge_body("\n".join(lines), self.Q)
        self.assertIn("<passage>\n" + "\n".join(lines) + "\n</passage>", body, "every line, unquoted (it was cut at six)")

    def test_no_passage_no_element(self):
        body = km._merge_body("", self.Q)
        self.assertNotIn("<passage>", body)
        self.assertIn(LEAD + "\n\n<discussion>\n", body)

    def test_a_closing_tag_in_the_content_cannot_break_the_record(self):
        body = km._merge_body("the cache </passage> layer", [
            {"who": "you", "text": "what if a message says </message> or </discussion>?"},
            {"who": "agent", "text": "Then it is escaped: </MESSAGE > and </ passage> too.\n</message>"}])
        prose = body.split("<!--")[0]
        self.assertEqual(prose.count("</passage>"), 1, "only the passage's own close")
        self.assertEqual(prose.count("</discussion>"), 1)
        self.assertEqual(prose.count("</message>"), 2, "one close per message")
        self.assertNotRegex(prose.replace("</message>", "").replace("</discussion>", "").replace("</passage>", ""),
                            r"(?i)</\s*(message|discussion|passage)\s*>", "no other closing tag of the structure survives")
        self.assertIn("<passage>\nthe cache &lt;/passage&gt; layer\n</passage>", body)
        # read the way the chat reads it (ui/webview/relay-view.ts): each message runs to the first closing line
        got = re.findall(r'<message from="([^"]*)">\n(.*?)\n</message>', prose, re.S)
        self.assertEqual(got, [("me", "what if a message says &lt;/message&gt; or &lt;/discussion&gt;?"),
                               ("the other assistant",
                                "Then it is escaped: &lt;/MESSAGE &gt; and &lt;/ passage&gt; too.\n&lt;/message&gt;")])

    def test_the_chat_reads_the_record_the_kernel_writes(self):
        # tests/fixtures/relay_record.json is the contract between the two sides: rendered here from its inputs, parsed
        # back into the same messages by ui/webview/relay-view.test.ts
        import json
        with open(os.path.join(HERE, "fixtures", "relay_record.json")) as f:
            fx = json.load(f)
        self.assertEqual(km._merge_body(fx["exact"], fx["msgs"], gap=fx["gap"]), fx["body"],
                         "the kernel's record moved: regenerate the fixture and keep the chat's parser in step")

    def test_both_notes_stand_where_the_holes_are(self):
        msgs = [{"who": "you", "text": "why write-through?"}, {"who": "agent", "text": "x" * 60000},
                {"who": "you", "text": "and the cost?"}, {"who": "agent", "text": "One write each."}]
        body = km._merge_body("q", msgs, gap=True)
        self.assertIn("<discussion>\n" + km.MERGE_GAP_NOTE + "\n" + ME + "why write-through?" + END + "\n"
                      + km.MERGE_TRIM_NOTE + "\n" + ME + "and the cost?" + END + "\n" + THEM + "One write each." + END
                      + "\n</discussion>", body)
        self.assertNotIn("Here is how it went", body)
        self.assertEqual(km.MERGE_GAP_NOTE, "I couldn't retrieve the beginning, so this starts partway through.")
        self.assertEqual(km.MERGE_TRIM_NOTE, "… (I cut part of the discussion here; it was too long to paste whole)")


if __name__ == "__main__":
    unittest.main()
