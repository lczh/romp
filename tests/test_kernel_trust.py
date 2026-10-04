#!/usr/bin/env python3
"""Per-host trust model, kernel side: the remotes registry stores a trust level (trusted|directed|
isolated), defaulting to directed; set_trust validates + persists it; _remote_public/_tunnels expose it
(the channel the bus reads); the /tunnels/trust route drives it; and the held-mail backfill surfaces a held
message from a directed peer as a needs-you NOTICE card whose Approve and Deny are actions of the quarantine kind.

Synthetic only — hermetic temp STATE, placeholder hostnames/mids, invented notes-domain sessions.
"""
import contextlib
import http.client
import io
import json
import sys
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _row(host, **extra):
    r = {"host": host, "kernel_port": 29855, "local_port": 5000, "bus_port": 5001, "token": "t",
         "proc": None, "status": "up", "detail": "", "sids": [], "trust": "directed"}
    r.update(extra)
    return r


class SetTrust(unittest.TestCase):
    def setUp(self):
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")

    def test_default_is_directed_in_public_view(self):
        pub = km._remote_public(km._remotes["TESTHOST"])
        self.assertEqual(pub["trust"], "directed")

    def test_set_trust_updates_and_persists(self):
        pub, err = km.set_trust("TESTHOST", "trusted")
        self.assertIsNone(err)
        self.assertEqual(pub["trust"], "trusted")
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted")
        # persistence: reload from remotes.json and confirm the level survived
        km._remotes_save()
        km._remotes.clear()
        km._remotes_load()
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted")

    def test_set_trust_rejects_bad_level(self):
        pub, err = km.set_trust("TESTHOST", "whatever")
        self.assertIsNone(pub)
        self.assertIn("trust must be one of", err)

    def test_set_trust_unattached_host_is_origin_only(self):
        # Trust is judged BY ORIGIN at delivery (the user 2026-07-25): a host with no tunnel here —
        # its mail arrives relayed through a hub — can carry a tier. The level lands in the
        # remembered-hosts table and reaches the bus as an origin-only row.
        calls = []
        saved = km._notify_bus_origin_trust
        km._notify_bus_origin_trust = lambda h, t: calls.append((h, t)) or True
        try:
            pub, err = km.set_trust("FARBOX", "trusted")
        finally:
            km._notify_bus_origin_trust = saved
        self.assertIsNone(err)
        self.assertEqual(pub, {"host": "FARBOX", "trust": "trusted", "originOnly": True})
        self.assertEqual(km.known_trust("FARBOX"), "trusted", "the remembered table IS the store")
        self.assertEqual(calls, [("FARBOX", "trusted")], "the bus learns the origin row now")

    def test_push_origin_trust_rows_covers_only_unattached(self):
        # The supervisor pushes remembered-but-unattached tiers once per (host, level); attached
        # hosts stay the (up, trust)-keyed full notify's job.
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")
        km._known.clear()                         # order-independent: other tests seed this table
        km._known_note("TESTHOST", "trusted")     # attached → not this path's job
        km._known_note("FARBOX", "isolated")      # unattached → pushed
        km._origin_trust_pushed.clear()
        calls = []
        saved = km._notify_bus_origin_trust
        km._notify_bus_origin_trust = lambda h, t: calls.append((h, t)) or True
        try:
            km._push_origin_trust_rows()
            km._push_origin_trust_rows()          # memoized: no duplicate push
            km._known_note("FARBOX", "directed")  # a level CHANGE re-pushes
            km._push_origin_trust_rows()
        finally:
            km._notify_bus_origin_trust = saved
        self.assertEqual(calls, [("FARBOX", "isolated"), ("FARBOX", "directed")])

    def test_load_defaults_missing_trust_to_directed(self):
        # a pre-trust remotes.json row (no "trust" key) reads back as directed
        km._remotes.clear()
        km._remotes["OLD"] = {k: v for k, v in _row("OLD").items() if k != "trust"}
        km._remotes_save()
        km._remotes.clear()
        km._remotes_load()
        self.assertEqual(km._remotes["OLD"]["trust"], "directed")


class TrustRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", path, json.dumps(body),
                  {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_route_sets_trust(self):
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")
        code, data = self._post("/tunnels/trust", {"host": "TESTHOST", "trust": "isolated"})
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["tunnel"]["trust"], "isolated")

    def test_route_rejects_bad_level(self):
        km._remotes.clear()
        km._remotes["TESTHOST"] = _row("TESTHOST")
        code, data = self._post("/tunnels/trust", {"host": "TESTHOST", "trust": "bogus"})
        self.assertEqual(code, 400)
        self.assertFalse(data["ok"])

    def test_route_unattached_host_sets_origin_trust(self):
        km._remotes.clear()
        saved = km._notify_bus_origin_trust
        km._notify_bus_origin_trust = lambda h, t: True
        try:
            code, data = self._post("/tunnels/trust", {"host": "GHOST", "trust": "trusted"})
        finally:
            km._notify_bus_origin_trust = saved
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["tunnel"], {"host": "GHOST", "trust": "trusted", "originOnly": True})


WEB = "11111111-2222-3333-4444-000000000901"     # the recipient session the names registry knows: a PRIVATE synthetic sid (the
#                                                  suite runs every module in one process over one state root, and a names entry
#                                                  left under the shared placeholder sid made a later module's rename of that sid
#                                                  to "web" its own-name no-op, 2026-09-19); _HeldMailFixture removes what these write, nothing else


OTHER = "11111111-2222-3333-4444-000000000903"   # a second session: the recipient of a message another card must not decide
LATE = "11111111-2222-3333-4444-000000000904"    # a recipient the names registry learns only after its hold was posted owner-less
_HELD_SIDS = (WEB, OTHER, LATE)
_HELD_MIDS = set()                               # every held file these tests write, removed at their end
_HELD_STAMP = [0]                                # the last mtime _held_dir_moved set


def _held_dir_moved():
    """Move the hold directory's mtime past every value a build has read. The backfill's pass is keyed on the directory's
    stat, and on a filesystem without fine-grained timestamps (every one before Linux 6.13) a change inside the timestamp tick
    of a build's read leaves the stat as that build read it, so a test that changes the directory and expects the next build
    to look moves the stat itself, as a later tick would."""
    q = km.jd.STATE / "postal" / "quarantine"
    _HELD_STAMP[0] = max(os.stat(q).st_mtime_ns, _HELD_STAMP[0]) + 1000
    os.utime(q, ns=(_HELD_STAMP[0], _HELD_STAMP[0]))


class _HeldMailFixture:
    """The held-mail tests write into the run's SHARED state root (every kernel test module runs in one process over one root):
    they must remove only what they wrote (the manager's review of PR 1885, low 3). Their own sids' files (names, notices,
    archive, index) and their held files go; the shared files they append to (the owner-less home, its archive and index,
    the cleared ledger) are restored to the bytes they had before the test."""

    @staticmethod
    def _shared():
        out = {}
        f = km.jd.STATE / "cleared.jsonl"
        out["cleared.jsonl"] = f.read_bytes() if f.exists() else None
        for d in ("notices", "notices-archive"):
            dd = km.jd.STATE / d
            if dd.exists():
                for f in dd.iterdir():
                    if f.name.startswith("notes"):
                        out[d + "/" + f.name] = f.read_bytes()
        return out

    @staticmethod
    def begin(tc):
        tc._held_before = _HeldMailFixture._shared()
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        km.NAMES = km.jd.NAMES                          # the direct registry read, off any cycle's names snapshot an earlier class left
        km._live_scope.names = None
        _HeldMailFixture.memos()

    @staticmethod
    def memos():
        getattr(km, "_HELD_MAIL_MEMO", {})["slot"] = None; getattr(km, "_HELD_MAIL_SAID", set()).clear()
        km._NOTICE_MEMO.clear(); km._CLEARED_MEMO["slot"] = None

    @staticmethod
    def end(tc):
        for sid in _HELD_SIDS:
            for f in (km.jd.NAMES / sid, km.jd.STATE / "notices" / (sid + ".jsonl"), km.jd.STATE / "notices-archive" / (sid + ".jsonl"),
                      km.jd.STATE / "notices-archive" / (sid + ".revs.json")):
                if f.exists():
                    f.unlink()
        qdir = km.jd.STATE / "postal" / "quarantine"
        for mid in list(_HELD_MIDS):
            f = qdir / (mid + ".json")
            if f.exists():
                f.unlink()
        _HELD_MIDS.clear()
        after = _HeldMailFixture._shared()
        for rel in set(after) | set(tc._held_before):
            f = km.jd.STATE / rel
            before = tc._held_before.get(rel)
            if before is None:
                if f.exists():
                    f.unlink()                          # the test created it
            elif after.get(rel) != before:
                f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(before)
        _HeldMailFixture.memos()


class HeldMailCards(unittest.TestCase):
    """A message held from a DIRECTED peer is a NOTICE CARD (plans/notice-cards.md, "Action kinds and the held-mail card",
    2026-09-19): the kernel's backfill posts one under the RECIPIENT for every held file the notice store has no row for,
    key = the message id, producer postal, Approve and Deny of the quarantine kind, dismissed on the decision. Idempotent over
    the live rows and the archive, memoized as _held_mail_backfill says; a recipient the kernel does not know files owner-less
    with no actions. Synthetic: a placeholder recipient sid, invented session names and text."""

    def _write_held(self, mid, frm="api", to="web", to_id=WEB, origin="TESTHOST", body="ship the parser fix", at=1000):
        qdir = km.jd.STATE / "postal" / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / (mid + ".json")).write_text(json.dumps(
            {"mid": mid, "to": to, "toId": to_id, "frm": frm, "frmId": "id-api", "body": body, "kind": "coordinate", "origin": origin, "at": at}))
        _HELD_MIDS.add(mid)
        _held_dir_moved()

    def _drop_held(self, mid):                           # the bus deletes a decided hold's file (quarantine_del)
        (km.jd.STATE / "postal" / "quarantine" / (mid + ".json")).unlink()
        _held_dir_moved()

    def _name(self, sid, name):
        (km.jd.NAMES / sid).write_text("%s\t%s\t#1EA1EB\t#ffffff\n" % (name, km.jd.STATE / "notes-api"))

    def setUp(self):
        _HeldMailFixture.begin(self)
        HeldMailCards._name(self, WEB, "web")           # by the class: HeldMailDecision and BusPortRecord borrow this setUp

    def tearDown(self):
        _HeldMailFixture.end(self)

    def _cards(self, alive=()):
        return km._notice_cards(2000, km._cleared_ids(), set(alive))

    def test_a_held_message_is_a_needs_you_notice_card_under_the_recipient_with_approve_and_deny_of_the_quarantine_kind(self):
        self._write_held("qc-1")
        cards = self._cards()
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual((c["itemId"], c["sid"], c["name"]), ("notice:%s:qc-1:1" % WEB, WEB, "web"), "keyed by the message id, under the recipient")
        self.assertEqual((c["column"], c["category"], c["board"]), ("needs_input", "needs_input", "feed"))
        self.assertEqual(c["text"], "New message from api")
        self.assertEqual(c["t"], 1000, "the card's time is the hold's, never the build's clock")
        n = c["notice"]
        self.assertEqual(n["producer"], "postal")
        self.assertEqual(n["body"], "from TESTHOST:api to web, held because peer TESTHOST is DIRECTED\n\nship the parser fix", "the route line, then the message text")
        self.assertEqual(n["actions"], [{"label": "Approve", "kind": "quarantine", "body": {"mid": "qc-1", "verdict": "approve"}},
                                        {"label": "Deny", "kind": "quarantine", "body": {"mid": "qc-1", "verdict": "deny"}}], "two actions of the KIND, no Edit")
        self.assertTrue(n["dismissOnAction"]); self.assertIsNone(c["blocked"], "no hand-built blocked flavour")

    def test_live_is_the_recipients_from_the_alive_roster(self):
        # 2026-09-19: every notice card said live False, so the card modal struck the recipient's name through as a dead session's
        self._write_held("qc-2")
        self.assertEqual([c["live"] for c in self._cards()], [False])
        self.assertEqual([c["live"] for c in self._cards(alive={WEB})], [True])

    def test_the_backfill_is_idempotent_and_memoized_on_the_directory(self):
        self._write_held("qc-3")
        self.assertEqual(km._held_mail_backfill(), 1)
        self.assertEqual(km._held_mail_backfill(), 0, "the memo: an unmoved directory posts nothing")
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(km._held_mail_backfill(), 0, "a live post row for the key: never a second card")
        self._write_held("qc-4", body="another")
        self.assertEqual(km._held_mail_backfill(), 1, "a moved directory: only the new hold is posted")
        self.assertEqual(sorted(c["itemId"] for c in self._cards()), ["notice:%s:qc-3:1" % WEB, "notice:%s:qc-4:1" % WEB])
        self.assertEqual([r["op"] for r in km._notice_rows(WEB)], ["post", "post"], "two rows, one per hold")

    def test_a_decided_or_dismissed_message_is_never_re_posted_even_after_the_archive_pass(self):
        self._write_held("qc-5"); self._write_held("qc-6")
        km._held_mail_backfill()
        iid5 = "notice:%s:qc-5:1" % WEB
        with km._notice_lock:                          # the decision's retirement, as the runner writes it
            km._notice_append(WEB, {"op": "expire", "t": 1500, "key": "qc-5", "rev": 1, "sid": WEB})
        km._clear_ask("notice:%s:qc-6:1" % WEB)        # the user's Clear on the other
        self.assertEqual([c["itemId"] for c in self._cards()], [], "the decided card and the dismissed card are both off the board")
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(km._held_mail_backfill(), 0, "the live rows hold both keys")
        moved = km._compact_notices(now=5000)          # the retention pass moves the expired and the dismissed rows to the archive
        self.assertGreater(moved, 0)
        km._HELD_MAIL_MEMO["slot"] = None; km._NOTICE_MEMO.clear()
        self.assertEqual(km._held_mail_backfill(), 0, "the archive's revision index holds both keys: a decided card stays decided")
        self.assertEqual([c["itemId"] for c in self._cards()], [])
        self.assertNotIn(iid5, [c["itemId"] for c in self._cards()])

    def test_an_unknown_recipient_files_owner_less_with_no_actions(self):
        self._write_held("qc-7", to="gone", to_id="11111111-2222-3333-4444-999999999999")
        cards = self._cards()
        self.assertEqual(len(cards), 1); c = cards[0]
        self.assertEqual((c["sid"], c["name"], c["column"]), (km.NOTICE_OWNERLESS_SID, km.NOTICE_OWNERLESS_NAME, "completed"), "no session to route the decision: the owner-less run, informational")
        self.assertEqual(c["text"], "New message from api for gone"); self.assertEqual(c["notice"]["actions"], [])

    def test_a_held_file_whose_id_is_no_key_is_skipped_and_an_empty_directory_posts_nothing(self):
        self._write_held("bad id!")
        self.assertEqual(km._held_mail_backfill(), 0); self.assertEqual(self._cards(), [])
        qdir = km.jd.STATE / "postal" / "quarantine"
        for f in qdir.iterdir():
            f.unlink()
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(km._held_mail_backfill(), 0)
        import shutil
        shutil.rmtree(qdir)
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(km._held_mail_backfill(), 0, "no directory: nothing held, no crash")

    def test_a_hold_posted_owner_less_is_re_homed_once_its_recipient_is_known_and_a_dismissed_one_is_not(self):
        # the manager's review of PR 1885, low 1 (executed: three cards for two holds): the recipient unknown at the first build,
        # the card files owner-less; the names entry lands and another hold moves the directory: the first hold must not gain a
        # second card beside the first, it moves home (the actionable card under the recipient, the informational one expired)
        self._write_held("rh-1", to="late", to_id=LATE)
        self.assertEqual(km._held_mail_backfill(), 1)
        self.assertEqual([(c["sid"], len(c["notice"]["actions"])) for c in self._cards() if c["notice"]["key"] == "rh-1"], [(km.NOTICE_OWNERLESS_SID, 0)], "owner-less, informational")
        self._name(LATE, "late"); km._live_scope.names = None
        self._write_held("rh-2", to="late", to_id=LATE, body="a second hold")
        self.assertEqual(km._held_mail_backfill(), 2, "the second hold and the first, re-homed")
        mine = [c for c in self._cards() if c["notice"]["key"] in ("rh-1", "rh-2")]
        self.assertEqual(sorted((c["sid"], c["notice"]["key"], len(c["notice"]["actions"])) for c in mine),
                         [(LATE, "rh-1", 2), (LATE, "rh-2", 2)], "two holds, two cards, both under the recipient with their decisions")
        self.assertIn(("expire", "rh-1"), [(r["op"], r["key"]) for r in km._notice_rows(km.NOTICE_OWNERLESS_SID)], "the owner-less card was expired, not left beside")
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(km._held_mail_backfill(), 0, "and nothing posts again")
        # a hold dismissed while owner-less stays dismissed: the user's Clear is a decision about that message's card
        (km.jd.NAMES / LATE).unlink(); km._live_scope.names = None
        self._write_held("rh-3", to="late", to_id=LATE, body="a third hold")
        self.assertEqual(km._held_mail_backfill(), 1)
        km._clear_ask("notice:%s:rh-3:1" % km.NOTICE_OWNERLESS_SID)
        self.assertGreater(km._compact_notices(now=10**9), 0, "the pass archived the dismissed row")
        self._name(LATE, "late"); km._live_scope.names = None; km._HELD_MAIL_MEMO["slot"] = None; km._NOTICE_MEMO.clear()
        self.assertEqual(km._held_mail_backfill(), 0, "an archived (dismissed) owner-less card is not re-posted under the recipient")
        self.assertNotIn("rh-3", [c["notice"]["key"] for c in self._cards()])

    def test_a_quarantine_action_naming_a_message_held_for_another_session_is_refused_at_the_post(self):
        # the manager's review of PR 1885, medium: a producer's card under one session's name and colour must not route the
        # user's click at another session's mail; the held file's recipient must be the card's owner (a mid with no file is the
        # bus's to refuse at the click)
        self._name(OTHER, "api")
        self._write_held("xo-1", to="api", to_id=OTHER)
        row, err = km.post_notice(WEB, "xo-1", "New message from api", producer="postal", actions=km._held_mail_actions("xo-1"), needs_you=True, now=100)
        self.assertEqual((row, err), (None, "a quarantine action's message is held for another session, not this card's owner"))
        row, err = km.post_notice(OTHER, "xo-1", "New message from api", producer="postal", actions=km._held_mail_actions("xo-1"), needs_you=True, now=100)
        self.assertIsNone(err, "the recipient's own card is accepted")
        row, err = km.post_notice(WEB, "nofile", "t", producer="postal", actions=km._held_mail_actions("nofile-1"), needs_you=True, now=100)
        self.assertIsNone(err, "no held file to consult: the click's bus answers")

    def test_the_feed_payload_names_this_machine(self):
        import inspect
        self.assertIn('"selfHost": _self_host(),', inspect.getsource(km.build_feed))

    def test_build_feed_lists_no_hand_built_quarantine_family(self):
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertNotIn("_quarantine_cards(", src, "the family is gone: the held message rides _notice_cards")
        self.assertIn("_notice_cards(now, cleared, {s[\"sid\"] for s in alive})", src, "the notice cards take the build's alive roster")
        self.assertFalse(hasattr(km, "_quarantine_cards"))

    def test_a_held_id_longer_than_sixty_four_characters_gets_a_card_whose_verdict_carries_that_id(self):
        # a relayed id is "px-" + "<epoch>.<pid>_<32 hex>.<sender host>" (postal _unique): 48 + pid digits + host length
        # characters, so a 13-character host passes 64 at a 4-digit pid; the bus holds any id up to its _safe_id bound, 128
        mids = ["px-1700000000.%d_%s.TESTHOSTPADXY" % (pid, "0123456789abcdef" * 2) for pid in (4321, 54321, 654321, 4194303)] + ["q" * 128]
        self.assertEqual([len(m) for m in mids], [65, 66, 67, 68, 128])
        for m in mids:
            self._write_held(m)
        cards = {c["notice"]["key"]: c for c in self._cards()}
        self.assertEqual(sorted(cards), sorted(mids), "one card per held id")
        mid = mids[0]
        iid = "notice:%s:%s:1" % (WEB, mid)
        self.assertEqual((cards[mid]["itemId"], cards[mid]["notice"]["actions"]), (iid, km._held_mail_actions(mid)))
        acts, saved = [], km._bus_quarantine_act
        km._bus_quarantine_act = lambda body: (acts.append(body) or (True, ""))
        try:
            self.assertEqual(km._notice_action(iid, "quarantine", {"mid": mid, "verdict": "deny"}), (True, ""))
        finally:
            km._bus_quarantine_act = saved
        self.assertEqual(acts, [{"mid": mid, "action": "deny", "sid": WEB}])
        self.assertNotIn(iid, [c["itemId"] for c in self._cards()])

    def test_a_held_message_that_gets_no_card_is_named_on_stderr_once(self):
        # no card: an id outside the key rule [A-Za-z0-9_.-]{1,128}, which the bus could not hold, a text that passes the
        # notice body cap once the card's route line is added, a held file that cannot be read or parsed, and a hold not yet
        # carded while the revision index of a home it could be posted to cannot be read; a hold already carded is not named,
        # nor one whose owner-less card stands while its recipient's revision index cannot be read, nor one whose card the
        # user cleared and the sweep archived while the owner-less revision index cannot be read
        prefix = "[notice] held message "

        def two_passes():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(km._held_mail_backfill(), 0)
                km._HELD_MAIL_MEMO["slot"] = None
                self.assertEqual(km._held_mail_backfill(), 0)
            lines = [l for l in err.getvalue().splitlines() if l.startswith(prefix)]
            said = dict(l[len(prefix):].split(": ", 1) for l in lines)
            self.assertEqual(len(said), len(lines), "each named once over two passes: %r" % lines)
            return said
        qdir = km.jd.STATE / "postal" / "quarantine"
        self._write_held("qc-ok")
        self.assertEqual(km._held_mail_backfill(), 1)
        self._write_held("bad id!"); self._write_held("q" * 129); self._write_held("qc-big", body="x" * km.NOTICE_BODY_MAX)
        (qdir / "qc-dir.json").mkdir()                 # a read that raises (IsADirectoryError, an OSError)
        (qdir / "qc-nj.json").write_text("not json"); _HELD_MIDS.add("qc-nj")
        real_listdir = os.listdir

        def listdir(p="."):                            # a file listed, then gone before its read: decided mid-pass, not named
            return real_listdir(p) + (["qc-gone.json"] if str(p) == str(qdir) else [])
        try:
            with mock.patch.object(km.os, "listdir", listdir):
                said = two_passes()
        finally:
            (qdir / "qc-dir.json").rmdir()
        self.assertEqual(sorted(said), sorted(["bad id!", "q" * 129, "qc-big", "qc-dir", "qc-nj"]))
        self.assertEqual([said["bad id!"], said["q" * 129]], ["no card (the key must match [A-Za-z0-9_.-]{1,128})"] * 2)
        self.assertTrue(said["qc-big"].startswith("no card ("))
        self.assertIn("the body is too long", said["qc-big"])
        self.assertEqual([said["qc-dir"][:19], said["qc-nj"][:19]], ["could not be read ("] * 2)
        (qdir / "qc-big.json").unlink(); (qdir / "qc-nj.json").unlink()
        archive = km.jd.STATE / "notices-archive"
        archive.mkdir(parents=True, exist_ok=True)
        self._write_held("qc-rh", to="late", to_id=LATE)
        self.assertEqual(km._held_mail_backfill(), 1, "owner-less: its recipient is not known yet")
        self._name(LATE, "late"); km._live_scope.names = None
        (archive / (LATE + ".revs.json")).write_text("not json")
        self.assertEqual(two_passes(), {})
        self.assertEqual([c["sid"] for c in self._cards() if c["notice"]["key"] == "qc-rh"], [km.NOTICE_OWNERLESS_SID], "the owner-less card stands")
        (archive / (LATE + ".revs.json")).unlink()
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(km._held_mail_backfill(), 1, "moved to its recipient once that home reads")
        self._write_held("qc-ar")                      # a hold whose card the user cleared and the sweep archived
        self.assertEqual(km._held_mail_backfill(), 1)
        km._clear_ask("notice:%s:qc-ar:1" % WEB)
        self.assertGreater(km._compact_notices(now=10**9), 0)
        self._write_held("qc-ix")
        (archive / (km.NOTICE_OWNERLESS_SID + ".revs.json")).write_text("not json")
        said = two_passes()
        self.assertEqual(sorted(said), ["qc-ix"])
        self.assertIn("revision index could not be read", said["qc-ix"])
        self.assertEqual(sorted((c["sid"], c["notice"]["key"]) for c in self._cards()), sorted([(WEB, "qc-ok"), (LATE, "qc-rh")]))

    def test_a_card_whose_message_is_no_longer_held_leaves_the_board_at_the_next_build_and_cards_of_other_shapes_stay(self):
        # the bus deletes a decided hold's file (quarantine_del). The other five cards are not in the shape the backfill posts:
        # the held-mail producer's with Approve and Deny on another id, the same producer's with no action in a session's home,
        # another producer's in the owner-less home, the held-mail producer's with an action of another kind naming its own key,
        # and the held-mail producer's with Approve on its own key beside an action of another kind
        self._write_held("qc-d1"); self._write_held("qc-d2"); self._write_held("qc-d3", to="gone", to_id="11111111-2222-3333-4444-999999999999")
        km.post_notice(WEB, "qc-d4", "New message from api", producer="postal", actions=km._held_mail_actions("qc-d9"), needs_you=True, now=100, t=100)
        km.post_notice("", "qc-d5", "a note", producer="cli", now=100, t=100)
        km.post_notice(WEB, "qc-d6", "a note", producer="postal", needs_you=True, now=100, t=100)
        send = {"label": "Go", "kind": "send", "body": {"text": "hi", "mid": "qc-d7"}}
        self.assertIsNone(km.post_notice(WEB, "qc-d7", "a note", producer="postal", actions=[send], needs_you=True, now=100, t=100)[1])
        self.assertIsNone(km.post_notice(WEB, "qc-d8", "a note", producer="postal", actions=[km._held_mail_actions("qc-d8")[0], send],
                                         needs_you=True, now=100, t=100)[1])
        keys = lambda: sorted(c["notice"]["key"] for c in self._cards())
        self.assertEqual(keys(), ["qc-d1", "qc-d2", "qc-d3", "qc-d4", "qc-d5", "qc-d6", "qc-d7", "qc-d8"])
        self._drop_held("qc-d1"); self._drop_held("qc-d3")
        saved = km._feed_needs_input[0]
        km._feed_needs_input[0] = frozenset()
        try:
            self.assertEqual(keys(), ["qc-d2", "qc-d4", "qc-d5", "qc-d6", "qc-d7", "qc-d8"])
            self.assertEqual(sorted(r["key"] for r in km._chat_notices(WEB)), ["qc-d2", "qc-d4", "qc-d6", "qc-d7", "qc-d8"])
        finally:
            km._feed_needs_input[0] = saved
        km._HELD_MAIL_MEMO["slot"] = None
        self.assertEqual(keys(), ["qc-d2", "qc-d4", "qc-d5", "qc-d6", "qc-d7", "qc-d8"], "a later pass")
        ops = lambda sid, k: [r["op"] for r in km._notice_rows(sid) if r["key"] == k]
        self.assertEqual([ops(WEB, "qc-d1"), ops(WEB, "qc-d2"), ops(km.NOTICE_OWNERLESS_SID, "qc-d3")], [["post", "expire"], ["post"], ["post", "expire"]], "one expire for each card whose file is gone")

    def test_a_card_whose_expiry_cannot_be_written_stays_is_said_once_and_the_next_build_retires_it(self):
        self._write_held("qc-w1"); self._write_held("qc-w2")
        keys = lambda: sorted(c["notice"]["key"] for c in self._cards())
        self.assertEqual(keys(), ["qc-w1", "qc-w2"])
        self._drop_held("qc-w1")
        real, real_known, looked = km._notice_append, km._notice_session_known, []

        def refuse(sid, row):
            return "the notice could not be saved (disk full)" if row.get("op") == "expire" else real(sid, row)

        def known(sid):                                # the posting half asks once for each held file it reads whose card stands
            looked.append(sid)
            return real_known(sid)
        err = io.StringIO()
        with mock.patch.object(km, "_notice_append", refuse), mock.patch.object(km, "_notice_session_known", known), \
                contextlib.redirect_stderr(err):
            self.assertEqual(keys(), ["qc-w1", "qc-w2"])
            self.assertEqual(looked, [WEB], "the build after the directory moved looks at the hold still there, qc-w2")
            del looked[:]
            self.assertEqual(keys(), ["qc-w1", "qc-w2"])
            self.assertEqual(looked, [], "a build with the directory unmoved runs the retire alone")
        self.assertEqual([l for l in err.getvalue().splitlines() if "qc-w1" in l],
                         ["[notice] held message qc-w1: its card could not be retired (the notice could not be saved (disk full))"])
        self.assertEqual(keys(), ["qc-w2"], "a write that works retires it, with the hold directory unchanged")

    def test_a_hold_store_that_cannot_be_read_or_is_missing_retires_no_card_and_each_episode_is_said_once(self):
        qdir = km.jd.STATE / "postal" / "quarantine"
        away = qdir.with_name("quarantine-away")
        real_listdir, real_stat = os.listdir, os.stat

        def listdir(p="."):
            if str(p) == str(qdir):
                raise PermissionError(13, "Permission denied", str(p))
            return real_listdir(p)

        def stat(p, *a, **kw):
            if str(p) == str(qdir):
                raise PermissionError(13, "Permission denied", str(p))
            return real_stat(p, *a, **kw)

        def file_stat(p, *a, **kw):
            if str(p) == str(qdir / "qc-u-file.json"):
                raise PermissionError(13, "Permission denied", str(p))
            return real_stat(p, *a, **kw)

        @contextlib.contextmanager
        def missing():
            qdir.rename(away)
            try:
                yield
            finally:
                away.rename(qdir)
        # the listdir and stat refusals carry one text: the second is said only because the working read between re-armed it
        directory, retire = "no held-mail card was posted or retired", "no held-mail card was retired"
        for name, fault, what in (("listdir", lambda: mock.patch.object(km.os, "listdir", listdir), directory),
                                  ("stat", lambda: mock.patch.object(km.os, "stat", stat), directory), ("missing", missing, retire),
                                  ("file", lambda: mock.patch.object(km.os, "stat", file_stat), retire)):
            mid = "qc-u-" + name
            self._write_held(mid)
            self.assertEqual([c["notice"]["key"] for c in self._cards()], [mid])
            self._drop_held(mid)                          # a read that worked would retire the card
            err = io.StringIO()
            with fault(), contextlib.redirect_stderr(err):
                self.assertEqual([c["notice"]["key"] for c in self._cards()], [mid], "a failed %s retires nothing" % name)
                self.assertEqual([c["notice"]["key"] for c in self._cards()], [mid])
            said = [l for l in err.getvalue().splitlines() if l.startswith("[notice] the held messages could not be read")]
            self.assertEqual(len(said), 1, "a failed %s, said once: %r" % (name, err.getvalue()))
            self.assertTrue(said[0].endswith("): " + what), said[0])
            self.assertEqual(self._cards(), [], "a read that works retires it")
        # two failed stats with the directory unmoved between them: the build between finds it unmoved and re-arms the line
        self._write_held("qc-u-again")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-u-again"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(2):
                with mock.patch.object(km.os, "stat", stat):
                    self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-u-again"])
                self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-u-again"])
        said = [l for l in err.getvalue().splitlines() if l.startswith("[notice] the held messages could not be read")]
        self.assertEqual(len(said), 2, "one line per episode: %r" % err.getvalue())

    def test_a_notice_store_that_cannot_be_read_retires_no_card_is_said_once_and_the_next_build_retires_it(self):
        self._write_held("qc-l1")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-l1"])
        self._drop_held("qc-l1")
        ndir, home = km._notice_dir(), km._notice_path(WEB)
        aside, real_listdir = home.with_name(home.name + ".aside"), os.listdir
        ops = lambda: [r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-l1"]

        def listdir(p="."):
            if str(p) == str(ndir):
                raise PermissionError(13, "Permission denied", str(p))
            return real_listdir(p)

        @contextlib.contextmanager
        def home_unreadable():                         # a read that raises (IsADirectoryError, an OSError), the memo emptied
            home.rename(aside); home.mkdir(); km._NOTICE_MEMO.clear()
            try:
                yield
            finally:
                home.rmdir(); aside.rename(home)
        for name, fault in (("listing", lambda: mock.patch.object(km.os, "listdir", listdir)), ("home", home_unreadable)):
            err = io.StringIO()
            with fault(), contextlib.redirect_stderr(err):
                km._held_mail_backfill()
                km._held_mail_backfill()
            said = [l for l in err.getvalue().splitlines() if l.startswith("[notice] the notice cards could not be read (")]
            self.assertEqual(len(said), 1, "a failed %s, said once: %r" % (name, err.getvalue()))
            self.assertTrue(said[0].endswith("): no held-mail card was retired"), said[0])
            self.assertEqual(ops(), ["post"], "a failed %s retires nothing" % name)
        err = io.StringIO()
        with mock.patch.object(km, "_notice_dir", lambda: ndir.with_name("notices-absent")), contextlib.redirect_stderr(err):
            self.assertEqual(km._held_mail_retire(set()), (0, False), "no notice directory: no card to retire, nothing unresolved")
        self.assertEqual(err.getvalue(), "")

        def listdir_ghost(p="."):                      # a home listed, then gone before its read: no rows, nothing to say
            return real_listdir(p) + (["ghost.jsonl"] if str(p) == str(ndir) else [])
        err = io.StringIO()
        with mock.patch.object(km.os, "listdir", listdir_ghost), contextlib.redirect_stderr(err):
            self.assertEqual(km._held_mail_backfill(), 0)
        self.assertNotIn("[notice] the notice cards", err.getvalue())
        self.assertEqual(ops(), ["post", "expire"], "a read that works retires it, with the hold directory unchanged")
        self.assertEqual(self._cards(), [])

    def test_a_hold_that_lands_while_its_recipients_notice_home_cannot_be_read_raises_nothing(self):
        # the posting half reads the recipient's home leniently: a read that raises is no rows there, the post the store then
        # refuses is said once, and the hold is carded at the next pass over a moved directory; a raise would leave the build
        self._write_held("qc-l0")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-l0"])
        home = km._notice_path(WEB)
        aside = home.with_name(home.name + ".aside")
        home.rename(aside); home.mkdir(); km._NOTICE_MEMO.clear()   # a read that raises (IsADirectoryError, an OSError)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self._write_held("qc-l9")
                km._held_mail_backfill()
                km._held_mail_backfill()
        finally:
            home.rmdir(); aside.rename(home)
        said = [l for l in err.getvalue().splitlines() if l.startswith("[notice] held message qc-l9: no card (")]
        self.assertEqual(len(said), 1, "the refused post, said once: %r" % err.getvalue())
        _held_dir_moved()
        self.assertEqual(sorted(c["notice"]["key"] for c in self._cards()), ["qc-l0", "qc-l9"])

    def test_a_hold_that_lands_while_another_build_is_mid_pass_keeps_its_card(self):
        # build A lists the hold directory; the hold lands; build B runs its whole pass and posts the card; A then retires
        # against its own listing, which lacks the hold
        qdir = km.jd.STATE / "postal" / "quarantine"
        self._write_held("qc-r0")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-r0"])
        real, seen = os.listdir, []

        def listdir(p="."):
            out = real(p)
            if str(p) == str(qdir) and not seen:
                seen.append(True)
                self._write_held("qc-r1")
                km._HELD_MAIL_MEMO["slot"] = None
                self.assertEqual(km._held_mail_backfill(), 1, "B posts the card")
            return out
        km._HELD_MAIL_MEMO["slot"] = None
        with mock.patch.object(km.os, "listdir", listdir):
            self.assertEqual(km._held_mail_backfill(), 0)
        self.assertEqual(seen, [True])
        self.assertEqual([r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-r1"], ["post"])
        self.assertEqual(sorted(c["notice"]["key"] for c in self._cards()), ["qc-r0", "qc-r1"])

    def _decide_while_b_posts(self, fault=None):
        """Build B reads a new hold's file, qc-v1, and waits at its post while the bus decides the hold; build C starts after
        the decision, runs its retire before B posts (no card to expire yet) and finishes last, so the memo holds the directory
        as the decision left it with nothing unresolved, and no later build looks at the hold again unless B's pass sees to
        it. `fault`: "write" refuses B's expiries after its post, "stat" makes B's stats of qc-v1's file after its post raise.
        Returns what the two builds said on stderr."""
        qdir = km.jd.STATE / "postal" / "quarantine"
        self._write_held("qc-v0")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-v0"])
        self._write_held("qc-v1")
        real_post, real_retire, real_append, real_stat = km.post_notice, km._held_mail_retire, km._notice_append, os.stat
        role, done, posted = threading.local(), {}, []
        b_at_post, b_go, c_retired, c_go = (threading.Event() for _ in range(4))
        b_after_post = lambda: getattr(role, "name", "") == "B" and bool(posted)

        def post(*a, **kw):
            if getattr(role, "name", "") == "B" and a[1] == "qc-v1":
                b_at_post.set()
                b_go.wait(10)
                out = real_post(*a, **kw)
                posted.append(a[1])
                return out
            return real_post(*a, **kw)

        def retire(held):
            out = real_retire(held)
            if getattr(role, "name", "") == "C":
                c_retired.set()
                c_go.wait(10)
            return out

        def append(sid, row):
            if fault == "write" and b_after_post() and row.get("op") == "expire":
                return "the notice could not be saved (disk full)"
            return real_append(sid, row)

        def stat(p, *a, **kw):
            if fault == "stat" and b_after_post() and str(p) == str(qdir / "qc-v1.json"):
                raise PermissionError(13, "Permission denied", str(p))
            return real_stat(p, *a, **kw)

        def build(name):
            role.name = name
            try:
                done[name] = km._held_mail_backfill()
            except Exception as e:                    # a raise fails the test below
                done[name] = repr(e)
        b, c = threading.Thread(target=build, args=("B",)), threading.Thread(target=build, args=("C",))
        err = io.StringIO()
        with mock.patch.object(km, "post_notice", post), mock.patch.object(km, "_held_mail_retire", retire), \
                mock.patch.object(km, "_notice_append", append), mock.patch.object(km.os, "stat", stat), \
                contextlib.redirect_stderr(err):
            try:
                b.start()
                self.assertTrue(b_at_post.wait(10), "B read qc-v1's file and waits at its post")
                self._drop_held("qc-v1")              # the bus decides it
                c.start()
                self.assertTrue(c_retired.wait(10), "C ran its retire after the decision")
                b_go.set(); b.join(10)
                c_go.set(); c.join(10)
            finally:
                b_go.set(); c_go.set()
                for t in (b, c):
                    if t.ident is not None:
                        t.join(10)
        self.assertEqual(done, {"B": 1, "C": 0})
        so = os.stat(qdir)
        self.assertEqual((km._HELD_MAIL_MEMO["slot"][2:], km._HELD_MAIL_MEMO["retry"]),
                         ((so.st_mtime_ns, so.st_size, so.st_ino, so.st_ctime_ns), False), "C wrote the memo last")
        return err.getvalue()

    def test_a_hold_decided_after_a_pass_read_its_file_loses_the_card_that_pass_posts(self):
        self._decide_while_b_posts()
        self.assertEqual([r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-v1"], ["post", "expire"], "B expires the card it posted")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-v0"])

    def test_a_pass_that_cannot_expire_the_card_it_posted_for_a_decided_hold_leaves_it_to_the_next_build(self):
        ops = lambda: [r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-v1"]
        said = self._decide_while_b_posts("write")
        self.assertEqual(ops(), ["post"], "B's expiry was refused")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-v0"], "the next build retires it, though C wrote the memo last")
        self.assertEqual(ops(), ["post", "expire"])
        self.assertEqual([l for l in (said + err.getvalue()).splitlines() if l.startswith("[notice]")],
                         ["[notice] held message qc-v1: its card could not be retired (the notice could not be saved (disk full))"])

    def test_a_pass_that_cannot_stat_a_hold_after_posting_its_card_says_so_once_and_leaves_the_card_to_the_next_build(self):
        ops = lambda: [r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-v1"]
        said = self._decide_while_b_posts("stat")
        self.assertEqual(ops(), ["post"], "B could not tell whether the hold was decided")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-v0"], "the next build retires it, though C wrote the memo last")
        self.assertEqual(ops(), ["post", "expire"])
        e = PermissionError(13, "Permission denied", str(km.jd.STATE / "postal" / "quarantine" / "qc-v1.json"))
        self.assertEqual([l for l in (said + err.getvalue()).splitlines() if l.startswith("[notice]")],
                         ["[notice] the held messages could not be read (%s): no held-mail card was retired" % e])

    def test_a_dismissed_hold_decided_meanwhile_stays_off_the_board_after_undo(self):
        self._write_held("qc-z")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-z"])
        km._clear_ask("notice:%s:qc-z:1" % WEB)
        self.assertGreater(km._compact_notices(now=10**9), 0, "the pass archived the dismissed row")
        self._drop_held("qc-z")
        self.assertEqual(self._cards(), [])
        km._undo_clear()
        self.assertEqual(self._cards(), [], "the restored card's message is no longer held")
        self.assertEqual([r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-z"], ["post", "expire"], "restored, then retired")

    def test_an_undo_during_a_pass_restores_a_decided_card_that_the_next_build_retires(self):
        # the restore lands while the pass posts another hold, after its retire: the pass ends after the restore
        self._write_held("qc-h")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-h"])
        km._clear_ask("notice:%s:qc-h:1" % WEB)
        self.assertGreater(km._compact_notices(now=10**9), 0, "the pass archived the dismissed row")
        self._drop_held("qc-h")
        self._write_held("qc-n")
        real, undone = km._held_mail_title, []

        def title(rec):
            if not undone:
                undone.append(km._undo_clear())
            return real(rec)
        with mock.patch.object(km, "_held_mail_title", title):
            self.assertEqual(km._held_mail_backfill(), 1)
        self.assertEqual(len(undone), 1)
        self.assertEqual([r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-h"], ["post"], "restored during the pass")
        self.assertEqual([c["notice"]["key"] for c in self._cards()], ["qc-n"])
        self.assertEqual([r["op"] for r in km._notice_rows(WEB) if r["key"] == "qc-h"], ["post", "expire"])

    def test_a_held_card_stands_beside_fifty_newer_notices_and_the_sweep_keeps_it(self):
        # in the recipient's home and, for a recipient this kernel does not know, in the owner-less home
        self._write_held("qc-cap", at=1000); self._write_held("qc-capo", to="gone", to_id="11111111-2222-3333-4444-999999999999", at=1000)
        homes = ((WEB, "qc-cap"), ("", "qc-capo"))
        for sid, _ in homes:
            for i in range(50):
                km.post_notice(sid, "n%02d" % i, "card %d" % i, producer="cli", now=2000 + i, t=2000 + i)
        keys = lambda sid: [c["notice"]["key"] for c in self._cards() if c["sid"] == (sid or km.NOTICE_OWNERLESS_SID)]
        for sid, held in homes:
            self.assertEqual(keys(sid), [held] + ["n%02d" % i for i in range(50)], "the held card, the oldest, stands with the fifty")
            km.post_notice(sid, "n50", "card 50", producer="cli", now=2050, t=2050)
            self.assertEqual(keys(sid), [held] + ["n%02d" % i for i in range(1, 51)], "a fifty-first newer notice pushes out the oldest other card")
        km._compact_notices(now=5000)
        for sid, held in homes:
            self.assertIn(("post", held), [(r["op"], r["key"]) for r in km._notice_rows(sid or km.NOTICE_OWNERLESS_SID)], "the sweep leaves the held card's row live")
        km._HELD_MAIL_MEMO["slot"] = None; km._NOTICE_MEMO.clear()
        self.assertEqual(km._held_mail_backfill(), 0)
        self.assertEqual([keys(sid)[0] for sid, _ in homes], ["qc-cap", "qc-capo"])

    def test_a_postal_card_not_in_the_held_shape_yields_to_the_cap_and_the_sweep_while_the_held_card_stays(self):
        # the cap passes over the held-mail shape (_held_mail_row), not the producer label: a session's postal card with no
        # actions (romp card --producer postal -s web), the held-mail producer's card with Approve and Deny naming another id,
        # one whose one action is a send naming its own key, and one with Approve on its own key beside a send, each hold a
        # slot, in the projection and in the sweep
        self._write_held("qc-cn", at=1000)
        km.post_notice(WEB, "qc-cn-note", "a note", producer="postal", now=1001, t=1001)
        self.assertIsNone(km.post_notice(WEB, "qc-cn-other", "New message from api", producer="postal", actions=km._held_mail_actions("qc-cn-zz"),
                                         needs_you=True, now=1002, t=1002)[1])
        send = lambda k: {"label": "Go", "kind": "send", "body": {"text": "hi", "mid": k}}
        self.assertIsNone(km.post_notice(WEB, "qc-cn-send", "a note", producer="postal", actions=[send("qc-cn-send")],
                                         needs_you=True, now=1003, t=1003)[1])
        self.assertIsNone(km.post_notice(WEB, "qc-cn-mix", "a note", producer="postal", actions=[km._held_mail_actions("qc-cn-mix")[0], send("qc-cn-mix")],
                                         needs_you=True, now=1004, t=1004)[1])
        for i in range(50):
            km.post_notice(WEB, "n%02d" % i, "card %d" % i, producer="cli", now=2000 + i, t=2000 + i)
        fifty = ["n%02d" % i for i in range(50)]
        keys = lambda: [c["notice"]["key"] for c in self._cards() if c["sid"] == WEB]
        self.assertEqual(keys(), ["qc-cn"] + fifty, "the four postal cards yield to the fifty newer cards; the held card stands")
        live = lambda: sorted({r["key"] for r in km._notice_rows(WEB) if r["op"] == "post"} & {"qc-cn", "qc-cn-note", "qc-cn-other", "qc-cn-send", "qc-cn-mix"})
        self.assertEqual(live(), ["qc-cn", "qc-cn-mix", "qc-cn-note", "qc-cn-other", "qc-cn-send"])
        km._compact_notices(now=5000)
        self.assertEqual(live(), ["qc-cn"], "the sweep archives the four postal cards' rows and leaves the held card's live")
        self.assertEqual(keys(), ["qc-cn"] + fifty)

    def test_a_hold_that_lands_after_fifty_notices_takes_no_slot_and_the_sweep_archives_none(self):
        # the held card is the NEWEST card of its home, so a cap that counted it and only spared it would push off the oldest
        # of the fifty: in the recipient's home and, for a recipient this kernel does not know, in the owner-less home
        homes = ((WEB, "qc-new"), ("", "qc-newo"))
        for sid, _ in homes:
            for i in range(50):
                km.post_notice(sid, "n%02d" % i, "card %d" % i, producer="cli", now=2000 + i, t=2000 + i)
        self._write_held("qc-new", at=3000); self._write_held("qc-newo", to="gone", to_id="11111111-2222-3333-4444-999999999999", at=3000)
        fifty = ["n%02d" % i for i in range(50)]
        home = lambda sid: sid or km.NOTICE_OWNERLESS_SID
        keys = lambda sid: [c["notice"]["key"] for c in self._cards() if c["sid"] == home(sid)]
        for sid, held in homes:
            self.assertEqual(keys(sid), fifty + [held], "the fifty older notices and the held card all stand")
        km._compact_notices(now=5000)
        for sid, held in homes:
            live = {r["key"] for r in km._notice_rows(home(sid)) if r["op"] == "post"}
            self.assertEqual(sorted(live & set(fifty + [held])), sorted(fifty + [held]), "the sweep archives none of the fifty")
            self.assertEqual(keys(sid), fifty + [held])

    def test_a_pass_reads_no_notice_home_under_the_lock_for_a_hold_whose_card_stands(self):
        # held cards hold no cap slot, so a home keeps a row for every waiting hold, and a read of its file under the lock for
        # each held file made a pass quadratic in the waiting holds: a hold whose card stands in the home it would be posted
        # to is passed over on that home's memoized rows, read once a pass. A new hold is still read and carded, and so is a
        # hold whose owner-less card moves to its recipient once known, though the owner-less rows, read for another hold,
        # list its key
        for i in range(3):
            self._write_held("qc-p%d" % i)
        self._write_held("qc-po", to="gone", to_id="11111111-2222-3333-4444-999999999999")
        self._write_held("qc-pr", to="late", to_id=LATE)
        self.assertEqual(km._held_mail_backfill(), 5)
        real, reads = km._notice_rows_unlocked, []
        real_rows, memo_reads = km._notice_rows, []

        def counted(sid, *a, **kw):                    # the posting half's reads (the retire's are strict)
            if not (a and a[0]) and not kw.get("strict"):
                reads.append(sid)
            return real(sid, *a, **kw)

        def rows_counted(sid):
            memo_reads.append(sid)
            return real_rows(sid)
        with mock.patch.object(km, "_notice_rows_unlocked", counted), mock.patch.object(km, "_notice_rows", rows_counted):
            _held_dir_moved()
            self.assertEqual(km._held_mail_backfill(), 0)
            self.assertEqual(reads, [], "a pass over carded holds reads no home under the lock")
            self.assertLessEqual(memo_reads.count(WEB), 2, "the retire's read and one pass-over read of the home, not one per held file")
            self._write_held("qc-p9")
            self.assertEqual(km._held_mail_backfill(), 1)
        self.assertIn(WEB, reads, "the new hold's home is read under the lock")
        self._name(LATE, "late"); km._live_scope.names = None
        _held_dir_moved()
        self.assertEqual(km._held_mail_backfill(), 1, "qc-pr moves to its recipient")
        self.assertEqual(sorted((c["sid"], c["notice"]["key"]) for c in self._cards()),
                         sorted([(WEB, "qc-p0"), (WEB, "qc-p1"), (WEB, "qc-p2"), (WEB, "qc-p9"), (km.NOTICE_OWNERLESS_SID, "qc-po"), (LATE, "qc-pr")]))

    def test_a_build_that_forgets_the_said_lines_while_another_build_says_one_does_not_raise(self):
        # feed builds can run at once (the pusher's, GET /feed.json's, each connection's clearAll): one forgets the store-wide
        # lines (_held_mail_rearm) while another says a line. A walk of the live set in bytecode raises "Set changed size
        # during iteration" out of the build. The test runs for one second
        self.addCleanup(sys.setswitchinterval, sys.getswitchinterval())
        sys.setswitchinterval(1e-6)
        km._HELD_MAIL_SAID.update(("qc-s%d" % i, "why") for i in range(200))
        errs, stop, t0 = [], threading.Event(), time.monotonic()

        def rearm():
            try:
                while not errs and time.monotonic() - t0 < 1:
                    km._held_mail_rearm()
            except Exception as e:                    # any raise fails the test, and the other thread stops
                errs.append(repr(e))
            finally:
                stop.set()

        def say():
            i = 0
            try:
                while not stop.is_set() and time.monotonic() - t0 < 5:
                    i += 1
                    km._held_mail_say(("qc-t%d" % (i % 500), "why"), "x")
                    km._held_mail_unreadable(OSError("episode %d" % (i % 50)))
            except Exception as e:
                errs.append(repr(e))
        threads = [threading.Thread(target=rearm), threading.Thread(target=say)]
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errs, [])


class HeldMailDecision(unittest.TestCase):
    """The decision is a notice ACTION of the quarantine kind over the noticeAction op: the kernel runs the STORED action
    through the bus's act road with the card's owner as the recipient, a deny's note as the bus's feedback, and answers the
    asking pane by the card's id (noticeActionDone), so a refusal re-arms that card's buttons alone and says why. The
    quarantineDecision and quarantineRefused ops left with the hand-built family (2026-09-19)."""

    def setUp(self):
        HeldMailCards.setUp(self)
        self._saved = km._bus_quarantine_act, km._mark_views_dirty
        self.sent, self.dirtied, self.acts = [], [], []
        km._mark_views_dirty = lambda: self.dirtied.append(True)
        self.client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: self.sent.append(json.loads(raw))}
        HeldMailCards._write_held(self, "qc-7"); getattr(km, "_held_mail_backfill", lambda: 0)()
        self.dirtied.clear()                            # the backfill's post dirtied the views; the decisions below are what is measured
        self.iid = "notice:%s:qc-7:1" % WEB

    def tearDown(self):
        km._bus_quarantine_act, km._mark_views_dirty = self._saved
        HeldMailCards.tearDown(self)

    def _op(self, body, inp=None, kind="quarantine"):
        msg = {"type": "noticeAction", "itemId": self.iid, "sid": WEB, "kind": kind, "body": body}
        if inp is not None:
            msg["input"] = inp
        km.Handler._dispatch_ws(None, msg, self.client)

    def test_a_refused_verdict_answers_the_card_by_its_id_and_changes_no_view(self):
        km._bus_quarantine_act = lambda body: (self.acts.append(body) or (False, "the recipient is no longer live"))
        self._op({"mid": "qc-7", "verdict": "approve"})
        self.assertEqual(self.acts, [{"mid": "qc-7", "action": "approve", "sid": WEB}], "the stored body as the bus's act, the card's owner as the recipient")
        self.assertEqual(self.sent, [{"type": "noticeActionDone", "itemId": self.iid, "ok": False, "error": "the recipient is no longer live"}],
                         "the reply names the card, so the feed re-arms that card's buttons alone")
        self.assertEqual(self.dirtied, [], "a refused verdict changes no view")
        self.assertEqual([r["op"] for r in km._notice_rows(WEB)], ["post"], "no retirement on a refusal")
        self.assertEqual([c["itemId"] for c in km._notice_cards(2000, km._cleared_ids())], [self.iid], "the card stays")

    def test_an_accepted_verdict_retires_the_card_and_rebuilds_the_views(self):
        km._bus_quarantine_act = lambda body: (self.acts.append(body) or (True, ""))
        self._op({"mid": "qc-7", "verdict": "deny"}, {"note": "  not now,   ask after the release "})
        self.assertEqual(self.acts, [{"mid": "qc-7", "action": "deny", "sid": WEB, "feedback": "not now, ask after the release"}], "a deny's note rides as the bus's feedback, whitespace collapsed")
        self.assertEqual(self.sent, [{"type": "noticeActionDone", "itemId": self.iid, "ok": True, "error": ""}])
        self.assertEqual([r["op"] for r in km._notice_rows(WEB)], ["post", "expire", "acted"], "the decision expires the card and marks the action spent")
        self.assertIn(self.iid, km._cleared_ids(), "dismissOnAction: the card is off the board at once")
        self.assertEqual(km._notice_cards(2000, km._cleared_ids()), [])
        self.assertTrue(self.dirtied)
        # a second click on the same card: the bus is never asked again
        self._op({"mid": "qc-7", "verdict": "approve"})
        self.assertEqual(len(self.acts), 1); self.assertEqual(self.sent[-1]["ok"], False)

    def test_the_click_may_add_only_the_input_the_kind_names(self):
        km._bus_quarantine_act = lambda body: (self.acts.append(body) or (True, ""))
        self._op({"mid": "qc-7", "verdict": "approve"}, {"note": "why"})
        self.assertEqual((self.sent[-1]["ok"], self.sent[-1]["error"]), (False, "the action takes no 'note' from the click"), "an approve carries no note")
        self._op({"mid": "qc-7", "verdict": "deny"}, {"text": "edited words"})
        self.assertEqual((self.sent[-1]["ok"], self.sent[-1]["error"]), (False, "the action takes no 'text' from the click"), "nobody edits held mail")
        self._op({"mid": "qc-7", "verdict": "edit"})
        self.assertEqual((self.sent[-1]["ok"], self.sent[-1]["error"]), (False, "no such action on that card"), "a verdict the card never stored")
        self._op({"mid": "other", "verdict": "approve"})
        self.assertEqual(self.sent[-1]["error"], "no such action on that card", "another message's id is not this card's action")
        self.assertEqual(self.acts, [], "the bus never heard of any of it")

    def test_a_verdict_on_a_message_held_for_another_session_is_refused_before_the_bus(self):
        # the manager's review of PR 1885, medium (executed at 178a61af: the click on a card under api delivered web's held
        # message): a row that names another session's held message, written before the post-time check or by hand, is
        # refused at the click; the bus is never asked
        HeldMailCards._name(self, OTHER, "api")
        HeldMailCards._write_held(self, "xo-7", to="api", to_id=OTHER)
        with km._notice_lock:
            km._notice_append(WEB, {"op": "post", "t": 100, "key": "xo-7", "rev": 1, "sid": WEB, "title": "New message from api", "body": "", "producer": "postal",
                                    "needsYou": True, "dismissOnAction": True, "actions": km._held_mail_actions("xo-7")})
        km._bus_quarantine_act = lambda body: (self.acts.append(body) or (True, ""))
        iid = "notice:%s:xo-7:1" % WEB
        km.Handler._dispatch_ws(None, {"type": "noticeAction", "itemId": iid, "sid": WEB, "kind": "quarantine", "body": {"mid": "xo-7", "verdict": "approve"}}, self.client)
        self.assertEqual(self.sent[-1], {"type": "noticeActionDone", "itemId": iid, "ok": False, "error": "that message is held for another session, not this card's owner"})
        self.assertEqual(self.acts, [], "the bus was never asked")
        self.assertEqual([r["op"] for r in km._notice_rows(WEB) if r.get("key") == "xo-7"], ["post"], "the card stands, undecided")
        self.assertTrue((km.jd.STATE / "postal" / "quarantine" / "xo-7.json").exists(), "the message is still held")

    def test_the_older_ops_are_gone(self):
        km._bus_quarantine_act = lambda body: (self.acts.append(body) or (True, ""))
        km.Handler._dispatch_ws(None, {"type": "quarantineDecision", "mid": "qc-7", "action": "approve", "sid": WEB}, self.client)
        self.assertEqual((self.acts, self.sent), ([], [{"type": "unknownOp", "op": "quarantineDecision"}]), "quarantineDecision is an op the kernel no longer knows; the bus is never asked")
        import inspect
        self.assertNotIn("quarantineRefused", inspect.getsource(km.Handler._dispatch_ws))

    def test_a_verdict_the_bus_carried_out_after_the_kernel_gave_up_retires_the_card_at_the_next_build(self):
        held = km.jd.STATE / "postal" / "quarantine" / "qc-7.json"

        def late(body):
            self.acts.append(body)
            held.unlink(); _held_dir_moved()           # the bus decided and deleted the hold after the kernel's request timed out
            return False, "postal bus unreachable: timed out"
        km._bus_quarantine_act = late
        self._op({"mid": "qc-7", "verdict": "deny"})
        self.assertEqual(self.sent[-1], {"type": "noticeActionDone", "itemId": self.iid, "ok": False, "error": "postal bus unreachable: timed out"})
        self.assertEqual(self.dirtied, [])
        pushed = []
        with mock.patch.object(km, "_push_soon", lambda: pushed.append(True)):
            self.assertEqual(km._notice_cards(2000, km._cleared_ids()), [])
        self.assertEqual([r["op"] for r in km._notice_rows(WEB)], ["post", "expire"])
        self.assertTrue(self.dirtied, "the expiry rebuilds the views")
        self.assertTrue(pushed, "and wakes the pusher")


class MirrorTrust(unittest.TestCase):
    """mirror_trust (the user 2026-07-26): sets OUR level for a host as ITS level for US, through the
    tunnel forward + that machine's serve token — the human with both tokens acting on both kernels.
    Deliberately the ONLY reciprocity: a peer can never open our gate by declaring trust."""

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.pop("boxa", None)

    def _stub_remote(self, seen):
        from http.server import BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                seen["token"] = self.headers.get("X-Romp-Token")
                seen["body"] = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                out = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_mirror_posts_our_level_through_the_tunnel_with_the_remote_token(self):
        seen = {}
        port = self._stub_remote(seen)
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=port, token="remote-tok", trust="trusted")
        res, err = km.mirror_trust("boxa")
        self.assertIsNone(err)
        self.assertEqual(res, {"host": "boxa", "trust": "trusted"})
        self.assertEqual(seen["path"], "/tunnels/trust", "the remote's own persisting route does the write")
        self.assertEqual(seen["token"], "remote-tok", "authorized with THAT machine's serve token")
        self.assertEqual(seen["body"], {"host": km._self_host(), "trust": "trusted"},
                         "the remote is told to hold THIS machine at our current level")

    def test_mirror_without_a_tunnel_errors_plainly(self):
        res, err = km.mirror_trust("nosuchhost")
        self.assertIsNone(res)
        self.assertTrue(err.startswith("no attached"), err)

    def test_mirror_without_a_token_errors_plainly(self):
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=1, token="", trust="trusted")
        res, err = km.mirror_trust("boxa")
        self.assertIsNone(res)
        self.assertIn("no admin path", err)


def _stub_kernel(cleanup_with, seen=None, tunnels=None):
    """A pretend remote kernel on a loopback port: records any POST into `seen` (mirror/remote-trust
    tests) and answers GET /tunnels with `tunnels` (pairs tests)."""
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def _out(self, payload):
            out = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_POST(self):
            if seen is not None:
                seen["path"] = self.path
                seen["token"] = self.headers.get("X-Romp-Token")
                seen["body"] = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self._out({"ok": True})

        def do_GET(self):
            self._out(tunnels or {})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cleanup_with(srv.shutdown)
    return srv.server_address[1]


class RemoteTrust(unittest.TestCase):
    """remote_trust (the user 2026-08-11): the hub sets ON one attached machine ITS trust level for
    another host — the popover's 'Between your machines' rows. Same boundary as mirror_trust: the
    write crosses the on-host tunnel with that machine's own serve token, and that machine's own
    /tunnels/trust route persists it; a peer can never raise its own standing."""

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.pop("boxa", None)

    def test_writes_the_pair_level_through_the_holders_tunnel(self):
        seen = {}
        port = _stub_kernel(self.addCleanup, seen=seen)
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=port, token="remote-tok", trust="directed")
        res, err = km.remote_trust("boxa", "boxb", "trusted")
        self.assertIsNone(err)
        self.assertEqual(res, {"onHost": "boxa", "host": "boxb", "trust": "trusted"})
        self.assertEqual(seen["path"], "/tunnels/trust", "the holder's own persisting route does the write")
        self.assertEqual(seen["token"], "remote-tok", "authorized with the HOLDING machine's serve token")
        self.assertEqual(seen["body"], {"host": "boxb", "trust": "trusted"},
                         "boxa is told to hold boxb at the chosen level")

    def test_bad_level_refused_before_any_dial(self):
        res, err = km.remote_trust("boxa", "boxb", "bogus")
        self.assertIsNone(res)
        self.assertIn("trust must be one of", err)

    def test_without_a_tunnel_errors_plainly(self):
        res, err = km.remote_trust("nosuchhost", "boxb", "trusted")
        self.assertIsNone(res)
        self.assertTrue(err.startswith("no attached"), err)

    def test_a_machine_never_tiers_its_own_mail(self):
        res, err = km.remote_trust("boxa", "boxa", "trusted")
        self.assertIsNone(res)
        self.assertIn("own mail", err)


class PairsSnapshot(unittest.TestCase):
    """pairs_snapshot: the hub reads each attached machine's own trust table over its tunnel and
    assembles per-pair directions. '' = the holder has no explicit row (the bus treats a relayed
    origin as directed); None + a named per-host error = that machine was unreadable this pass —
    surfaced, never silently dropped (fail loudly)."""

    def setUp(self):
        # these tests assert the WHOLE hosts map, and the kernel module is ONE object per process for
        # every test file that loads it under this name — so start from an empty map and put back
        # exactly what was there (under xdist another file's leftover row landed in the snapshot,
        # 2026-09-02); popping only this class's own hosts left any other file's row in place
        with km._remotes_lock:
            self._saved = dict(km._remotes)
            km._remotes.clear()

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved)

    def test_pairs_read_both_directions_from_each_machines_own_table(self):
        # boxa holds boxb via an attached-tunnel row; boxb holds boxa via a relay (viaReach) row —
        # the three row kinds a tier can live on are all consulted.
        pa = _stub_kernel(self.addCleanup, tunnels={"tunnels": [{"host": "boxb", "trust": "trusted"}],
                                                    "known": [], "viaReach": []})
        pb = _stub_kernel(self.addCleanup, tunnels={"tunnels": [], "known": [],
                                                    "viaReach": [{"host": "boxa", "trust": "isolated"}]})
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=pa, token="ta")
            km._remotes["boxb"] = _row("boxb", local_port=pb, token="tb")
        snap = km.pairs_snapshot()
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["hosts"], {"boxa": {"ok": True}, "boxb": {"ok": True}})
        self.assertEqual(snap["pairs"], [{"a": "boxa", "b": "boxb", "ab": "trusted", "ba": "isolated"}])

    def test_no_explicit_row_reads_empty_never_an_invented_level(self):
        pa = _stub_kernel(self.addCleanup, tunnels={"tunnels": [], "known": [], "viaReach": []})
        pb = _stub_kernel(self.addCleanup, tunnels={"tunnels": [],
                                                    "known": [{"host": "boxa", "trust": "trusted"}],
                                                    "viaReach": []})
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=pa, token="ta")
            km._remotes["boxb"] = _row("boxb", local_port=pb, token="tb")
        snap = km.pairs_snapshot()
        self.assertEqual(snap["pairs"], [{"a": "boxa", "b": "boxb", "ab": "", "ba": "trusted"}])

    def test_an_unreadable_machine_carries_its_error_not_a_guess(self):
        pa = _stub_kernel(self.addCleanup, tunnels={"tunnels": [{"host": "boxb", "trust": "directed"}]})
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=pa, token="ta")
            km._remotes["boxb"] = _row("boxb", status="down")
        snap = km.pairs_snapshot()
        self.assertEqual(snap["hosts"]["boxb"], {"ok": False, "error": "not connected"})
        self.assertEqual(snap["pairs"], [{"a": "boxa", "b": "boxb", "ab": "directed", "ba": None}])


class PairRoutes(unittest.TestCase):
    """Route wiring for the pair section: GET /tunnels/pairs answers the snapshot; POST
    /tunnels/trust-remote proxies the write and maps errors to honest statuses."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        with km._remotes_lock:
            km._remotes.clear()

    def _call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, None if body is None else json.dumps(body),
                  {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_pairs_route_answers_empty_world(self):
        code, data = self._call("GET", "/tunnels/pairs")
        self.assertEqual(code, 200)
        self.assertEqual(data, {"ok": True, "hosts": {}, "pairs": []})

    def test_trust_remote_route_round_trips_to_the_stub(self):
        seen = {}
        port = _stub_kernel(self.addCleanup, seen=seen)
        with km._remotes_lock:
            km._remotes["boxa"] = _row("boxa", local_port=port, token="remote-tok")
        code, data = self._call("POST", "/tunnels/trust-remote",
                                {"onHost": "boxa", "host": "boxb", "trust": "trusted"})
        self.assertEqual(code, 200)
        self.assertEqual(data, {"ok": True, "set": {"onHost": "boxa", "host": "boxb", "trust": "trusted"}})
        self.assertEqual(seen["body"], {"host": "boxb", "trust": "trusted"})

    def test_trust_remote_route_maps_errors_to_statuses(self):
        code, data = self._call("POST", "/tunnels/trust-remote",
                                {"onHost": "boxa", "host": "boxb", "trust": "bogus"})
        self.assertEqual((code, data["ok"]), (400, False))
        code, data = self._call("POST", "/tunnels/trust-remote",
                                {"onHost": "ghost", "host": "boxb", "trust": "trusted"})
        self.assertEqual((code, data["ok"]), (404, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BusPortRecord(unittest.TestCase):
    """The kernel's loopback dials of the bus read the bus's own port record ahead of the environment (2026-09-18): a kernel
    and a bus that read ROMP_POSTAL_PORT from different environments dialed different ports, and a held message's approve
    reached a bus that never held it ("no held message"). Hermetic: the record under this test's state root, a stub bus on a
    free port standing for the bus that holds the file."""

    def setUp(self):
        self.rec = km.jd.STATE / "postal" / "postal-port"
        self.rec.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.rec.unlink()
        except FileNotFoundError:
            pass
        km._BUS_PORT_SAID[0] = None
        self._saved_ens = km._BUS_ENSURED[0]; km._BUS_ENSURED[0] = True   # this kernel ensured its bus: the record may be trusted
        self._err, self._saved_bp = io.StringIO(), km.BUS_PORT
        self._saved_stderr = sys.stderr; sys.stderr = self._err

    def tearDown(self):
        sys.stderr = self._saved_stderr
        km.BUS_PORT = self._saved_bp
        km._BUS_ENSURED[0] = self._saved_ens
        km._BUS_PORT_SAID[0] = None
        try:
            self.rec.unlink()
        except FileNotFoundError:
            pass

    def _rec(self, port, pid=None, tok=None):
        """A record as the bus writes it: this kernel's token mark unless a foreign one is asked for."""
        return json.dumps({"port": port, "pid": os.getpid() if pid is None else pid, "tok": km._bus_token_mark() if tok is None else tok})

    def _stub_bus(self, seen):
        from http.server import BaseHTTPRequestHandler   # the module's own idiom: imported where the stub is built
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                seen.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
                out = json.dumps({"ok": True}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_the_record_wins_over_the_environment_and_the_environment_is_the_fallback(self):
        km.BUS_PORT = 1                                            # the environment's word: a port nothing answers on
        self.assertEqual(km._bus_port(), 1, "no record: the environment")
        self.rec.write_text(self._rec(45678))
        self.assertEqual(km._bus_port(), 45678, "the record names the bound port, a live pid and this kernel's token mark: it wins")
        self.rec.write_text(self._rec(45679, pid=2 ** 22 + 12345))   # a pid that does not run: a stale record
        self.assertEqual(km._bus_port(), 1, "a stale record (its pid gone) is ignored: the environment")
        self.rec.write_text(self._rec(45680, tok="0123456789abcdef"))   # another bus's record: a token mark that is not ours
        self.assertEqual(km._bus_port(), 1, "a foreign record (another bus, another world, a reused pid) is ignored: the environment")
        self.rec.write_text(json.dumps({"port": 45681, "pid": os.getpid()}))   # a record with no mark (an older bus): never trusted
        self.assertEqual(km._bus_port(), 1)
        # a kernel that ensured NO bus (client-only, a lab's, an in-process test's) dials the environment whatever the record says
        self.rec.write_text(self._rec(45682))
        km._BUS_ENSURED[0] = False
        self.assertEqual(km._bus_port(), 1, "no ensure, no record: the environment")
        km._BUS_ENSURED[0] = True
        self.assertEqual(km._bus_port(), 45682, "the ensure is the event that makes the bus this kernel's")
        self.rec.write_text("torn")
        self.assertEqual(km._bus_port(), 1, "a torn record: the environment, never a raise")
        self.rec.write_text(self._rec(0))
        self.assertEqual(km._bus_port(), 1, "a record with no port: the environment")

    def test_the_census_line_says_the_port_and_its_source_once_and_names_a_mismatch_with_the_environment(self):
        km.BUS_PORT = 25302
        km._bus_port(); km._bus_port()
        lines = [l for l in self._err.getvalue().splitlines() if "postal bus dialed" in l]
        self.assertEqual(lines, ["romp-kernel: postal bus dialed on 127.0.0.1:25302 from the environment"], "said once, no mismatch when the environment is the source")
        self.rec.write_text(self._rec(45678))
        km._bus_port(); km._bus_port()
        lines = [l for l in self._err.getvalue().splitlines() if "postal bus dialed" in l]
        self.assertEqual(len(lines), 2, "a change is said again, once")
        self.assertIn("127.0.0.1:45678 from the record (ROMP_POSTAL_PORT says 25302: the environment and the bus disagree; the record wins)", lines[1])

    def test_a_kernel_on_one_port_and_a_bus_bound_on_another_still_reach_the_bus_that_holds_the_file(self):
        # the fault of 2026-09-18, red at main: the kernel's environment names a port nothing answers on while the bus that holds
        # the message is bound elsewhere and says so in its record; the approve reaches the record's bus
        seen = []
        bus_port = self._stub_bus(seen)
        km.BUS_PORT = 1
        self.rec.write_text(self._rec(bus_port))
        ok, err = km._bus_quarantine_act({"mid": "px-1.2_abc.TESTHOST", "action": "approve", "sid": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual((ok, err), (True, ""), "the record's bus answered ok: an empty error on a success (the review of PR 1885, low 2): %r" % err)
        self.assertEqual(seen[0][0], "/quarantine/act"); self.assertEqual(seen[0][1]["mid"], "px-1.2_abc.TESTHOST"); self.assertEqual(seen[0][1]["sid"], "11111111-2222-3333-4444-555555555555")

    def test_a_record_another_world_left_cannot_redirect_a_dial_a_test_or_an_operator_pointed_elsewhere(self):
        # the suite found it first (2026-09-18): every kernel test module shares one event-model state root, so a record one
        # world wrote outlived it, and a module that pointed BUS_PORT at its own stub bus reached the machine's real bus
        # instead ("token required"). The mark closes it: a record is trusted only when its token mark is this kernel's own.
        seen = []
        stub = self._stub_bus(seen)
        km.BUS_PORT = stub
        self.rec.write_text(json.dumps({"port": 25302, "pid": os.getpid(), "tok": "not-this-kernels-mark"}))
        ok, err = km._bus_quarantine_act({"mid": "px-1.2_abc.TESTHOST", "action": "approve"})
        self.assertEqual((ok, err), (True, ""), "the dial followed the override to the stub, not the foreign record")
        self.assertEqual(seen[0][0], "/quarantine/act")

    def test_the_ensure_exiting_zero_is_what_arms_the_record(self):
        # the flag is set by _ensure_postal_bus on a zero exit and by nothing else; a refused ensure (the fixed port under a test)
        # leaves it off, so a hermetic kernel never trusts a record
        saved = km.subprocess.run
        class R:
            def __init__(self, code): self.returncode, self.stderr = code, "refused"
        try:
            km._BUS_ENSURED[0] = False
            km.subprocess.run = lambda *a, **kw: R(1)
            km._ensure_postal_bus()
            self.assertFalse(km._BUS_ENSURED[0], "a refused ensure arms nothing")
            km.subprocess.run = lambda *a, **kw: R(0)
            km._ensure_postal_bus()
            self.assertTrue(km._BUS_ENSURED[0], "the ensure that answered arms the record")
        finally:
            km.subprocess.run = saved

    def test_the_decision_carries_the_recipient_sid_to_the_bus(self):
        # the decision is a notice action of the quarantine kind since 2026-09-19: the recipient the bus is told is the CARD's
        # owner, read from the stored row, never a word the pane sent; a deny's note from the click is the bus's feedback
        HeldMailCards.setUp(self); self.addCleanup(HeldMailCards.tearDown, self)
        HeldMailCards._write_held(self, "px-1.2_abc.TESTHOST"); getattr(km, "_held_mail_backfill", lambda: 0)()
        bodies = []
        saved = km._bus_quarantine_act, km._mark_views_dirty
        km._bus_quarantine_act = lambda body: (bodies.append(body), (True, ""))[1]
        km._mark_views_dirty = lambda: None
        try:
            sent = []
            client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
            km.Handler._dispatch_ws(None, {"type": "noticeAction", "itemId": "notice:%s:px-1.2_abc.TESTHOST:1" % WEB, "sid": "TESTHOST:" + WEB, "kind": "quarantine",
                                           "body": {"mid": "px-1.2_abc.TESTHOST", "verdict": "deny"}, "input": {"note": "not now"}}, client)
        finally:
            km._bus_quarantine_act, km._mark_views_dirty = saved
        self.assertEqual(bodies, [{"mid": "px-1.2_abc.TESTHOST", "action": "deny", "sid": WEB, "feedback": "not now"}],
                         "the bus is told which session the decision is for: the card's owner, bare (the pane's prefixed word is not read)")
        self.assertEqual(sent, [{"type": "noticeActionDone", "itemId": "notice:%s:px-1.2_abc.TESTHOST:1" % WEB, "ok": True, "error": ""}])


if __name__ == "__main__":
    unittest.main()
