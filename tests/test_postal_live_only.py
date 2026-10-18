#!/usr/bin/env python3
"""Postal addressing is LIVE-ONLY (the user 2026-06-29): the Romp Postal Service no longer
reaches outside the live fleet. find_sessions and revive_session are GONE, and a recipient
name resolves only to a currently-live session — there is no dead-session resurrection and
no parking mail for a session that isn't running. This pins that simplification so the
removed surfaces can't quietly creep back. No real session data here (synthetic UUIDs).
"""
import contextlib
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
SKILL = os.path.join(ROOT, "claude", "skills", "romp-postal", "SKILL.md")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))

ALPHA = "11111111-2222-3333-4444-555555555555"
GHOST = "99999999-8888-7777-6666-555555555555"
THREAD = "11111111-2222-3333-4444-777777777777"
PEER = "99999999-8888-7777-6666-555555555557"    # a federated peer: known here only by its heartbeat


def _tool_names():
    return {t["name"] for t in pm.MCP_TOOLS}


def _set_live(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(rows, f)
    f.close()
    os.environ["ROMP_SESSIONS_FILE"] = f.name
    return f.name


class LiveOnlyAddressing(unittest.TestCase):
    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()

    def test_live_name_resolves(self):
        _set_live([{"id": ALPHA, "name": "alpha"}])
        self.assertEqual(pm._recip_id_for("alpha"), ALPHA)

    def test_unknown_name_is_unresolvable(self):
        _set_live([{"id": ALPHA, "name": "alpha"}])
        # a name no LIVE session has resolves to nothing — no dead-history fallback
        self.assertIsNone(pm._recip_id_for("ghost"))

    def test_uuid_without_mailbox_is_unresolvable(self):
        # live-only: a bare UUID that isn't live and has no in-flight mailbox does not resolve
        _set_live([{"id": ALPHA, "name": "alpha"}])
        self.assertIsNone(pm._recip_id_for(GHOST))

    def test_a_live_thread_row_resolves_by_its_own_name(self):
        # a comment thread is hidden from the default listing; recipient resolution asks for thread
        # rows (the 2026-08-22 rule), so recall by the thread's name still finds it now that its
        # heartbeat no longer leaves a phantom remote row (2026-09-06)
        _set_live([{"id": ALPHA, "name": "alpha"},
                   {"id": THREAD, "name": "alpha-t1", "thread": True, "parent": ALPHA}])
        self.assertEqual(pm._recip_id_for("alpha-t1"), THREAD)
        self.assertEqual(pm._name_for_id(THREAD), "alpha-t1", "the thread's name lives only on its row")

    def test_heartbeat_remote_resolves(self):
        # a heartbeating remote peer is LIVE for addressing purposes
        _set_live([])
        pm.HEARTBEATS[GHOST] = ("beta", pm.time.time())
        self.assertEqual(pm._recip_id_for("beta"), GHOST)


class RecallReachesParkedMailForTheDead(unittest.TestCase):
    """The ONE deliberate carve-out from live-only addressing (2026-08-29): RECALL is not
    addressing. The sender is unsending their OWN bytes, which sit locally in the recipient's
    new/ — no delivery, no resurrection. A handoff parked for a session that then died could not
    be unsent by NAME (only the raw box id worked, which nobody has at hand); _recall now falls
    back to the durable name map when the name no longer resolves live. Ambiguity still refuses:
    two dead boxes wearing one name is not a guess the sender authorized."""

    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        _set_live([{"id": ALPHA, "name": "alpha"}])

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        for rid in (GHOST, "99999999-8888-7777-6666-555555555556", PEER):
            box = pm.MAILROOT / rid / "new"
            if box.is_dir():
                for f in box.iterdir():
                    f.unlink()

    def _park(self, rid, name, mid="m1"):
        box = pm.MAILROOT / rid / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / mid).write_text("From: alpha\nFrom-Id: %s\nX-Park: 1\n\nthe handoff body" % ALPHA)
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        (pm.NAMES_DIR / rid).write_text("%s\thost" % name)

    def test_recall_by_dead_name_finds_the_parked_box(self):
        self._park(GHOST, "ghost")
        removed = pm._recall(ALPHA, "ghost", None)
        self.assertEqual([r["id"] for r in removed], ["m1"])
        self.assertEqual(list((pm.MAILROOT / GHOST / "new").iterdir()), [])

    def test_two_dead_boxes_one_name_refuses(self):
        twin = "99999999-8888-7777-6666-555555555556"
        self._park(GHOST, "ghost", mid="m1")
        self._park(twin, "ghost", mid="m2")
        self.assertEqual(pm._recall(ALPHA, "ghost", None), [])

    def test_only_the_senders_own_mail_comes_back(self):
        self._park(GHOST, "ghost")
        self.assertEqual(pm._recall("00000000-0000-0000-0000-000000000001", "ghost", None), [])

    def test_recall_by_a_live_thread_name(self):
        # a comment thread withholds its names entry, so only its live row can carry the name
        _set_live([{"id": ALPHA, "name": "alpha"},
                   {"id": THREAD, "name": "alpha-t1", "thread": True, "parent": ALPHA}])
        box = pm.MAILROOT / THREAD / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "m7").write_text("From: alpha\nFrom-Id: %s\n\nthe reply body" % ALPHA)
        try:
            removed = pm._recall(ALPHA, "alpha-t1", None)
            self.assertEqual([(r["id"], r["to"]) for r in removed], [("m7", "alpha-t1")])
            self.assertEqual(list(box.iterdir()), [])
        finally:
            for f in box.iterdir():
                f.unlink()


    def test_recall_by_a_remote_peers_name(self):
        # a peer known here only through its heartbeat (a federated session: no local row, no names
        # entry): recall by its name resolves through the remote presence _recip_id_for folds into the
        # listing it is handed, as it did when it fetched the listing itself (2026-09-06)
        pm.HEARTBEATS[PEER] = ("ghost", pm.time.time())
        self.addCleanup(pm.HEARTBEATS.clear)
        (pm.NAMES_DIR / PEER).unlink(missing_ok=True)       # only the heartbeat may name it
        box = pm.MAILROOT / PEER / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "m8").write_text("From: alpha\nFrom-Id: %s\n\nthe body" % ALPHA)
        removed = pm._recall(ALPHA, "ghost", None)
        self.assertEqual([r["id"] for r in removed], ["m8"])
        self.assertEqual(list(box.iterdir()), [])


class RecallTakesTheFormsSendTakes(unittest.TestCase):
    """Recall finds mail under every address form the send accepted (2026-10-17). The notes-api
    world: web sends, api runs here, and a second api runs on peer host TESTHOST-B, so a bare 'api'
    is refused as ambiguous and the sender picks `TESTHOST:api`, or the short id list_agents shows,
    or the far session's rename-proof id. Recall read each of these only as a bare name and answered
    that nothing was left to recall while the message sat unread in new/ or parked in the outbox."""

    WEB = "a1b2c3d4-1111-2222-3333-444444444444"
    API = "5e6f7a8b-5555-6666-7777-888888888888"
    FAR = "9c8d7e6f-9999-0000-1111-222222222222"
    OLD = "0d0e0f10-aaaa-bbbb-cccc-dddddddddddd"      # an earlier api session here that has ended

    def setUp(self):
        self._prior = {k: os.environ.get(k) for k in ("ROMP_SESSIONS_FILE", "ROMP_POSTAL_HOST", "ROMP_POSTAL_PEERS")}
        os.environ["ROMP_POSTAL_HOST"] = "TESTHOST"
        os.environ.pop("ROMP_POSTAL_PEERS", None)                  # peer mode, the default
        _set_live([{"id": self.WEB, "name": "web"}, {"id": self.API, "name": "api"}])
        self._peers = dict(pm.PEER_STATE)
        pm.PEER_STATE.clear()
        pm.PEER_STATE["TESTHOST-B"] = {"presence": [{"id": self.FAR, "name": "api"}]}

    def tearDown(self):
        for k, v in self._prior.items():
            restore_env(k, v)
        pm.PEER_STATE.clear()
        pm.PEER_STATE.update(self._peers)
        for d in (pm.MAILROOT / self.API / "new", pm.OUTBOX / "TESTHOST-B"):
            if d.is_dir():
                for f in d.iterdir():
                    f.unlink()
        for sid in (self.API, self.OLD):
            (pm.NAMES_DIR / sid).unlink(missing_ok=True)
        shutil.rmtree(pm.MAILROOT / self.OLD, ignore_errors=True)

    def _named(self, sid, name):
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        (pm.NAMES_DIR / sid).write_text("%s\tTESTHOST" % name)

    def _unread(self):
        box = pm.MAILROOT / self.API / "new"
        return sorted(f.name for f in box.iterdir()) if box.is_dir() else []

    def _send_local(self, to):
        res = pm.resolve_recipient(to, self.WEB)
        self.assertEqual((res["kind"], res.get("agent", {}).get("id")), ("direct", self.API), res)
        pm.deliver(self.API, "web", self.WEB, "the moot ask")
        self.assertEqual(len(self._unread()), 1)
        return self._unread()[0]

    def _send_far(self, to):
        res = pm.resolve_recipient(to, self.WEB)
        self.assertEqual((res["kind"], res.get("host")), ("relay", "TESTHOST-B"), res)
        hit, mid = res["agent"], "px-" + pm._unique()
        self.assertTrue(pm.outbox_put("TESTHOST-B", {"mid": mid, "to": hit.get("name") or to,
                                                     "toId": str(hit.get("id") or ""), "frm": "web",
                                                     "frm_id": self.WEB, "body": "the moot ask",
                                                     "kind": "question", "t": 1}))
        return mid

    def test_this_hosts_qualified_name(self):
        # an ended session also named api left its box here: the qualified name, like the bare one,
        # still means the LIVE api, not a two-box name the dead-name fallback refuses
        (pm.MAILROOT / self.OLD / "new").mkdir(parents=True, exist_ok=True)
        self._named(self.OLD, "api")
        refusal = pm.resolve_recipient("api", self.WEB)
        self.assertIn("TESTHOST:api", refusal.get("error", ""), "the refusal offers this form")
        mid = self._send_local("TESTHOST:api")
        self.assertEqual([r["id"] for r in pm._recall(self.WEB, "TESTHOST:api", None)], [mid])
        self.assertEqual(self._unread(), [])

    def test_short_id_from_list_agents(self):
        mid = self._send_local(self.API[:8])
        self.assertEqual([r["id"] for r in pm._recall(self.WEB, self.API[:8], None)], [mid])
        self.assertEqual(self._unread(), [])

    def test_short_id_of_a_session_that_has_ended(self):
        # recall is not addressing: the mail still sits in the ended session's box here
        mid = self._send_local(self.API[:8])
        _set_live([{"id": self.WEB, "name": "web"}])
        self._named(self.API, "api")                             # as any session that ran has
        self.assertEqual([r["id"] for r in pm._recall(self.WEB, self.API[:8], None)], [mid])

    def test_a_short_id_two_sessions_share_recalls_nothing(self):
        twin = self.API[:8] + "-0000-0000-0000-000000000000"
        mid = self._send_local(self.API[:13])
        _set_live([{"id": self.WEB, "name": "web"}, {"id": self.API, "name": "api"},
                   {"id": twin, "name": "tests"}])
        self.assertEqual(pm._recall(self.WEB, self.API[:8], None), [], "two sessions start so: no guess")
        self.assertEqual(self._unread(), [mid])

    def test_another_hosts_qualifier_leaves_the_local_box(self):
        mid = self._send_local("TESTHOST:api")
        self.assertEqual(pm._recall(self.WEB, "TESTHOST-B:api", None), [])
        self.assertEqual(self._unread(), [mid])

    def test_far_session_id_parked_in_the_outbox(self):
        for form in (self.FAR, self.FAR[:8], "TESTHOST-B:" + self.FAR[:8]):
            mid = self._send_far(form)
            self.assertEqual([r["id"] for r in pm._recall(self.WEB, form, None)], [mid], form)
            self.assertEqual(pm.outbox_list("TESTHOST-B"), [], form)

    def test_other_qualifiers_leave_far_parked_mail(self):
        # the disambiguated local form, or a third host's qualifier, never withdraws mail parked
        # for TESTHOST-B's api
        mid = self._send_far("TESTHOST-B:api")
        self.assertEqual(pm._recall(self.WEB, "TESTHOST:api", None), [])
        self.assertEqual(pm._recall(self.WEB, "TESTHOST-C:" + self.FAR[:8], None), [])
        self.assertEqual([m["mid"] for m in pm.outbox_list("TESTHOST-B")], [mid])


class KernelSilenceIsNotDeadness(unittest.TestCase):
    """The liveness source not ANSWERING is different information from "nobody by that name is
    live" (the authoritative-sources rule: fail loudly, never degrade silently). _kernel_sessions
    collapsed both to [], so a send during a kernel restart was refused with a false deadness
    claim about a demonstrably live peer (sighting 2026-08-29; the retry 101s later delivered).
    resolve_recipient now probes the source once on the refusal path and answers 503-honestly."""

    def setUp(self):
        self._prior_seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # no seam: the source is the kernel
        self._base = pm.KERNEL_BASE
        pm.KERNEL_BASE = "http://127.0.0.1:9"      # nothing listens: every fetch fails fast

    def tearDown(self):
        pm.KERNEL_BASE = self._base
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()

    def test_unanswered_source_refuses_without_claiming_death(self):
        res = pm.resolve_recipient("ghost")
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("didn't answer", res["error"])
        self.assertNotIn("no live romp session", res["error"], "the false deadness claim is the bug")

    def test_an_answered_empty_listing_still_refuses_as_not_live(self):
        _set_live([])
        res = pm.resolve_recipient("ghost")
        self.assertEqual((res["kind"], res["status"]), ("error", 404))
        self.assertIn("no live romp session named 'ghost'", res["error"])


# The kernel's own words when a product of its listing faulted at its last build (kernel.py
# _listing_refused), synthetic: it answers GET /sessions with a 503 and this as the body's "error".
LISTING_FAULT = ("the session listing's rows cannot be served: its build has failed since 12:00:00 "
                 "(2 builds; the last: KeyError: 'dir'), so the kept copy may be stale; the kernel log "
                 "has the traceback")
WEB = "11111111-2222-3333-4444-555555555555"


class _ListingKernel:
    """A real loopback HTTP server standing in for the kernel's GET /sessions, so the bus's REAL fetch
    path (urllib, the token header, the HTTPError) runs: `status` 503 answers with the kernel's refusal
    body, 200 with `rows`. A request without the bus's token is answered 401, as the kernel does."""

    def __init__(self, token):
        self.status, self.rows, self.hits = 503, [], 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.hits += 1
                if self.headers.get("X-Romp-Token") != token:
                    code, body = 401, {"ok": False, "error": "unauthorized"}
                elif fake.status == 200:
                    code, body = 200, fake.rows
                else:
                    code, body = fake.status, {"ok": False, "error": LISTING_FAULT}
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):            # keep the server's access log out of the captured stderr
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class KernelRefusalIsAnAnswer(unittest.TestCase):
    """A kernel that answers GET /sessions with a 503 (a product of its listing faulted at its last
    build) ANSWERED: the bus caught the HTTPError with every other failure and told the sender the
    kernel "didn't answer — likely mid-restart", discarding the kernel's own reason without logging it
    anywhere (2026-10-08). The refusal a sender sees now says the kernel answered and quotes its
    reason; the listing still cannot vouch for liveness, so no session is ruled dead on it; an
    unreachable kernel keeps the mid-restart reading; and the kernel's refusal is logged once per
    episode (consecutive refused fetches), with one line when the listing answers again, never once
    per fetch: every session's postal service fetches the listing constantly."""

    TOKEN = "listing-test-token"

    def setUp(self):
        self._prior_seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # no seam: the source is the kernel
        self._base, self._token = pm.KERNEL_BASE, pm.SERVE_TOKEN
        self.kernel = _ListingKernel(self.TOKEN)
        pm.KERNEL_BASE, pm.SERVE_TOKEN = self.kernel.base, self.TOKEN
        self._answer_once()                          # an episode an earlier test left open ends here, unseen
        self.kernel.status = 503

    def tearDown(self):
        self._answer_once()                          # this test's episode ends, so the next starts on an answer
        pm.KERNEL_BASE, pm.SERVE_TOKEN = self._base, self._token
        self.kernel.close()
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()
        for f in pm.QUARANTINE.glob("lr-*.json") if pm.QUARANTINE.is_dir() else ():
            f.unlink()

    def _answer_once(self):
        self.kernel.status = 200
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(pm._kernel_sessions_checked()[1])

    def test_the_refusal_quotes_the_kernel_and_claims_neither_silence_nor_death(self):
        res = pm.resolve_recipient("web")
        self.assertGreater(self.kernel.hits, 1, "the real fetch path reached the server")
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn(LISTING_FAULT, res["error"], "the kernel's own reason reaches the sender")
        self.assertIn("answered", res["error"])
        self.assertNotIn("didn't answer", res["error"], "the kernel DID answer")
        self.assertNotIn("mid-restart", res["error"])
        self.assertIn("NOT a claim that 'web' is dead", res["error"])

    def test_a_refused_listing_rules_no_session_dead(self):
        for who in ("web", "api", WEB, WEB[:8]):
            res = pm.resolve_recipient(who)
            self.assertEqual((res["kind"], res["status"]), ("error", 503), who)
            self.assertNotIn("no live romp session", res["error"], who)
        rows, answered = pm.local_agents_checked(threads=True)
        self.assertEqual(rows, [])
        self.assertFalse(answered, "a refused listing never vouches for liveness")

    def test_an_approve_on_a_refused_listing_quotes_the_kernel(self):
        self.assertTrue(pm._quarantine_put("TESTHOST", {"mid": "lr-appr-1", "to": "web", "frm": "api",
                                                        "frm_id": "", "body": "the held text"}, WEB))
        ok, err = pm.quarantine_decide("lr-appr-1", "approve")
        self.assertFalse(ok)
        self.assertIn(LISTING_FAULT, err)
        self.assertNotIn("didn't answer", err)
        self.assertIn("The held message is untouched", err)
        self.assertIsNotNone(pm.quarantine_get("lr-appr-1"), "the hold stands")

    def test_an_unreachable_kernel_keeps_the_mid_restart_reading(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                    # a closed port: nothing answers there
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % port
        try:
            res = pm.resolve_recipient("web")
        finally:
            pm.KERNEL_BASE = self.kernel.base
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("didn't answer — likely mid-restart", res["error"])
        self.assertNotIn(LISTING_FAULT, res["error"])
        self.assertNotIn("no live romp session", res["error"])

    def test_the_refusal_is_logged_once_per_episode(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                pm.resolve_recipient("web")          # two fetches each: the pool and the probe
            pm.local_agents_checked()
            self.kernel.status, self.kernel.rows = 200, [{"id": WEB, "name": "web"}]
            for _ in range(3):
                self.assertTrue(pm.local_agents_checked()[1])
            self.kernel.status = 503                 # a second episode is said again, once
            for _ in range(2):
                pm.local_agents_checked()
        lines = err.getvalue().splitlines()
        said = [i for i, l in enumerate(lines) if LISTING_FAULT in l]
        healed = [i for i, l in enumerate(lines) if "session listing answers again" in l]
        self.assertEqual(len(said), 2, "one line per episode, not one per fetch: %r" % lines)
        self.assertIn("HTTP 503", lines[said[0]])
        self.assertEqual(len(healed), 1, "one line when the listing answers again: %r" % lines)
        self.assertLess(said[0], healed[0])
        self.assertLess(healed[0], said[1])


class AnsweredButAbsentIsNotDeadness(unittest.TestCase):
    """The OTHER false-refusal arm (2026-08-31): the kernel ANSWERED but its listing transiently
    omitted a live session (a restart-settle blink), and the bus converted that into a hard "not
    live" — two verified specimens, one by id and one by name, one of which mis-routed a warning
    mail. A pool miss now corroborates against the DURABLE per-session registry (and the probe
    fetch's own rows) before ruling death: reg alive=true → soft retry-shortly, never a death
    claim. A name with no reg anywhere keeps the hard 404 — a typo stays a typo."""

    GHOST_SID = "99999999-8888-7777-6666-000000000001"

    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        _set_live([{"id": ALPHA, "name": "alpha"}])     # answered listing, target absent
        self._sdk = pm.STATE.parent / "sdk"
        self._sdk.mkdir(parents=True, exist_ok=True)
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()
        for f in list(self._sdk.iterdir()) + list(pm.NAMES_DIR.iterdir()):
            f.unlink()

    def _reg(self, sid, name, alive=True):
        (self._sdk / (sid + ".json")).write_text(json.dumps({"sid": sid, "name": name, "alive": alive}))
        (pm.NAMES_DIR / sid).write_text("%s\t/tmp" % name)

    def test_id_addressed_with_a_live_reg_refuses_soft(self):
        self._reg(self.GHOST_SID, "blinky")
        res = pm.resolve_recipient(self.GHOST_SID)
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])
        self.assertIn("NOT a claim", res["error"])

    def test_name_addressed_with_a_live_reg_refuses_soft(self):
        self._reg(self.GHOST_SID, "blinky")
        res = pm.resolve_recipient("blinky")
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])

    def test_a_dead_reg_keeps_the_hard_404_both_forms(self):
        self._reg(self.GHOST_SID, "gone4good", alive=False)
        for who in (self.GHOST_SID, "gone4good"):
            res = pm.resolve_recipient(who)
            self.assertEqual((res["kind"], res["status"]), ("error", 404), who)
            self.assertIn("no live romp session", res["error"])

    def test_no_reg_anywhere_keeps_the_hard_404(self):
        res = pm.resolve_recipient("typo-name")
        self.assertEqual((res["kind"], res["status"]), ("error", 404))

    def test_the_probe_fetchs_own_rows_count_as_presence(self):
        # the pool (all_agents) missed the target but the probe's fresh listing has it — the
        # kernel came back between the two reads; that is a blink, never a death ruling
        saved = pm.all_agents
        pm.all_agents = lambda threads=False: [{"id": ALPHA, "name": "alpha", "remote": False}]
        try:
            _set_live([{"id": ALPHA, "name": "alpha"}, {"id": self.GHOST_SID, "name": "blinky"}])
            res = pm.resolve_recipient("blinky")
        finally:
            pm.all_agents = saved
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])

    def test_a_host_qualified_miss_keeps_the_hard_404(self):
        # a host qualifier naming somebody ELSE takes every local out of the running by design —
        # the local listing/registry can say nothing about that host, so the blink arms must not
        # fire on a same-named LOCAL row (review find: a typo'd host became an endless retry-shortly)
        self._reg(self.GHOST_SID, "blinky")
        _set_live([{"id": self.GHOST_SID, "name": "blinky"}])
        res = pm.resolve_recipient("otherhost:blinky")
        self.assertEqual((res["kind"], res["status"]), ("error", 404))

    def test_short_id_blink_refuses_soft(self):
        # the third address form (the 8-char id prefix every list_agents row shows) is
        # blink-protected like the other two
        self._reg(self.GHOST_SID, "blinky")
        res = pm.resolve_recipient(self.GHOST_SID[:8])
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])

    def test_short_id_with_two_matching_regs_keeps_the_404(self):
        twin = self.GHOST_SID[:-1] + "f"
        self._reg(self.GHOST_SID, "blinky")
        self._reg(twin, "blinky2")
        res = pm.resolve_recipient(self.GHOST_SID[:8])
        self.assertEqual((res["kind"], res["status"]), ("error", 404),
                         "two registry hits on one prefix is the ambiguity family: refuse, never guess")

    def test_durable_session_reads(self):
        self._reg(self.GHOST_SID, "blinky")
        self.assertTrue(pm._durable_session(self.GHOST_SID, by_id=True))
        self.assertTrue(pm._durable_session("blinky", by_id=False))
        self.assertFalse(pm._durable_session("nobody", by_id=False))
        self.assertFalse(pm._durable_session("99999999-8888-7777-6666-000000000002", by_id=True))


class RemovedSurfacesAreGone(unittest.TestCase):
    def test_removed_mcp_tools_absent(self):
        names = _tool_names()
        self.assertNotIn("find_sessions", names)
        self.assertNotIn("revive_session", names)

    def test_live_tools_still_present(self):
        names = _tool_names()
        for n in ("send_message", "check_inbox", "list_agents",
                  "set_working", "check_sent", "recall_message"):
            self.assertIn(n, names)

    def test_removed_functions_absent(self):
        for fn in ("_dead_id_for_name", "_revive", "_resolve_session",
                   "_find_sessions", "_session_records", "format_find"):
            self.assertFalse(hasattr(pm, fn), f"{fn} should be removed from the postal service")

    def test_instructions_are_live_only(self):
        ins = pm.MCP_INSTRUCTIONS.lower()
        self.assertNotIn("find_sessions", ins)
        self.assertNotIn("revive_session", ins)
        self.assertIn("live-only", ins)

    def test_skill_prose_is_live_only(self):
        with open(SKILL, encoding="utf-8") as f:
            text = f.read().lower()
        self.assertNotIn("find_sessions", text)
        self.assertNotIn("revive_session", text)
        self.assertIn("live-only", text)


if __name__ == "__main__":
    unittest.main()
