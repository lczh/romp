#!/usr/bin/env python3
"""A stored login LINKED to a machine's sign-in (the user 2026-10-01: one enterprise seat, reached as one machine's own
sign-in and as a stored login's setup-token on another machine, read as two accounts in the spend breakdown, because the
token cannot tell romp its account).

`romp login link <login> --same-as <host>` records the user's statement that the stored login IS that machine's
signed-in account. The pieces under test:
  * kernel/logins.py: link copies a sign-in's identity (account digest, organisation digest, organisation, kind word,
    email) onto the record under `identity`, marked source "linked" with the host and the moment; unlink takes it back;
    a record that names its own account refuses; display shows the linked pieces the record lacks.
  * The kernel reads the identity from that machine's OWN kernel: this machine's sign-in for its own name or "local",
    else a connected kernel's GET /logins over the tunnel with that machine's token. It refuses, writing nothing, when
    the machine has no sign-in, cannot be reached, is not connected, is unknown, or is an older build.
  * GET /logins reports the machine's sign-in identity (what another machine's link copies) and each stored login's
    link; POST /logins {"link"} / {"unlink"} are the doors.
  * The spend breakdown folds a linked login into the account it is linked to, under PR #74's rules, and splits it
    again after an unlink; the hover says the link is the user's.

Synthetic only: dev@example.com, Example Org, invented digests and record ids, hosts TESTHOST / PEERHOST."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic state before any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)
os.environ.pop("ROMP_SUPERVISED", None)
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, HERE)
from romp_load import load_source   # noqa: E402

km = load_source("romp_kernel_loginlink", os.path.join(BIN, "romp-kernel"))
lg = km.lg

DIGEST = "a1a1a1a1a1a1"            # an invented account digest (the shape _claude_account prints)
ORGD = "c3c3c3c3c3c3"              # an invented organisation digest
LID = "%012x" % 0x0A0B0C0D0E0F     # an invented stored-login record id
PEER_TOKEN = "peer-serve-token-synthetic"


def _signed_in(acct=DIGEST, kind="enterprise"):
    """A machine's sign-in as _machine_identity reads it."""
    return {"state": "ok", "acct": acct, "org": "Example Org", "orgDigest": ORGD, "kind": kind, "email": "dev@example.com"}


class _Peer:
    """A stand-in for another machine's kernel: GET /logins answers `machine` (what that machine's kernel reports about
    its own sign-in), and the token every request carried is recorded."""

    def __init__(self, machine):
        self.machine = machine
        self.tokens = []
        self.post_answer = (200, {"ok": True})
        self.posts = []
        peer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                peer.tokens.append(self.headers.get("X-Romp-Token"))
                body = json.dumps({"ok": True, "logins": [], "machine": peer.machine}).encode()
                self.send_response(200 if self.path == "/logins" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                peer.posts.append(json.loads(self.rfile.read(n).decode() or "null"))
                st, ans = peer.post_answer
                body = json.dumps(ans).encode()
                self.send_response(st)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _peer_machine(**kw):
    """GET /logins' machine block from a current build signed in as the invented account."""
    m = {"label": "dev@example.com · Example Org · enterprise", "acct": "dev@example.com", "state": "ok",
         "identity": {"acct": DIGEST, "orgDigest": ORGD, "org": "Example Org", "kind": "enterprise", "email": "dev@example.com"}}
    m.update(kw)
    return m


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = Path(self.td.name)
        (self.state / "session-hosts").write_text("off")     # this test mints its own state root (the rule in CLAUDE.md)
        self._saved = (km.jd.STATE, km._self_host, km._machine_identity, dict(km._remotes))
        km.jd.STATE = self.state
        km._self_host = lambda: "TESTHOST"
        km._machine_identity = lambda: _signed_in()
        km._remotes.clear()
        self.peers = []
        lg.write_record(self.state, {"id": LID, "label": "enterprise", "tokenCmd": "token-read 'romp login enterprise'",
                                     "addedAt": int(time.time()) - 86400})

    def tearDown(self):
        for p in self.peers:
            p.close()
        km.jd.STATE, km._self_host, km._machine_identity, saved = self._saved
        km._remotes.clear()
        km._remotes.update(saved)
        self.td.cleanup()

    def peer(self, machine, host="PEERHOST", status="up"):
        p = _Peer(machine)
        self.peers.append(p)
        km._remotes[host] = {"host": host, "status": status, "local_port": p.port, "token": PEER_TOKEN}
        return p


class Records(Base):
    def test_link_copies_the_identity_marks_it_linked_and_unlink_takes_it_back(self):
        rec = lg.link(self.state, LID, {"acct": DIGEST, "orgDigest": ORGD, "org": "Example  Org", "kind": "team",
                                        "email": "dev@example.com", "extra": "dropped"}, "PEERHOST", now=1800000000)
        want = {"source": "linked", "host": "PEERHOST", "at": 1800000000, "acct": DIGEST, "orgDigest": ORGD,
                "org": "Example Org", "kind": "enterprise", "email": "dev@example.com"}
        self.assertEqual(rec["identity"], want, "the CLI word folds to the kind word; nothing but identity fields rides")
        self.assertEqual(lg.read_record(self.state, LID)["identity"], want, "written to the record")
        self.assertEqual(lg.linked(lg.read_record(self.state, LID))["host"], "PEERHOST")
        self.assertEqual(lg.display(lg.read_record(self.state, LID)), "enterprise · dev@example.com · Example Org · enterprise",
                         "the login surfaces show the linked pieces the record does not hold itself")
        self.assertIn("token-read", lg.read_record(self.state, LID)["tokenCmd"], "the rest of the record is untouched")
        self.assertTrue(lg.unlink(self.state, LID))
        self.assertNotIn("identity", lg.read_record(self.state, LID))
        self.assertIsNone(lg.linked(lg.read_record(self.state, LID)))
        self.assertEqual(lg.display(lg.read_record(self.state, LID)), "enterprise")
        self.assertFalse(lg.unlink(self.state, LID), "nothing left to take back")

    def test_link_refuses_without_an_account_and_over_a_record_that_names_its_own(self):
        with self.assertRaises(ValueError):
            lg.link(self.state, LID, {"org": "Example Org", "kind": "enterprise"}, "PEERHOST")
        self.assertNotIn("identity", lg.read_record(self.state, LID), "a refusal writes nothing")
        rec = lg.read_record(self.state, LID)
        rec["acctDigest"] = "b2b2b2b2b2b2"
        lg.write_record(self.state, rec)
        with self.assertRaises(ValueError) as cm:
            lg.link(self.state, LID, {"acct": DIGEST}, "PEERHOST")
        self.assertIn("already names its account", str(cm.exception))
        self.assertIsNone(lg.linked({"identity": {"source": "verified", "acct": DIGEST, "host": "PEERHOST"}}),
                          "only the user's link reads as one")


class Source(Base):
    def test_this_machines_sign_in_by_name_or_local(self):
        for h in ("TESTHOST", "local"):
            idn, host, err = km._login_link_source(h)
            self.assertEqual((host, err), ("TESTHOST", ""))
            self.assertEqual(idn, {"acct": DIGEST, "orgDigest": ORGD, "org": "Example Org", "kind": "enterprise",
                                   "email": "dev@example.com"})

    def test_a_connected_kernels_sign_in_is_read_from_that_kernel_with_its_token(self):
        p = self.peer(_peer_machine())
        idn, host, err = km._login_link_source("PEERHOST")
        self.assertEqual((host, err), ("PEERHOST", ""))
        self.assertEqual(idn["acct"], DIGEST)
        self.assertEqual(idn["email"], "dev@example.com")
        self.assertEqual(p.tokens, [PEER_TOKEN], "that machine's own serve token, the transport the spend detail uses")
        self.assertEqual(km._login_link_source("peerhost")[2], "", "host names are case-blind")

    def test_refuses_a_machine_with_no_sign_in(self):
        km._machine_identity = lambda: {"state": "none"}
        idn, _h, err = km._login_link_source("local")
        self.assertIsNone(idn)
        self.assertIn("has no Claude sign-in", err)
        km._machine_identity = lambda: {"state": "unreadable"}
        self.assertIn("could not be read just now", km._login_link_source("TESTHOST")[2])
        self.peer(_peer_machine(state="none", label="", acct="", identity=None))
        idn, _h, err = km._login_link_source("PEERHOST")
        self.assertIsNone(idn)
        self.assertIn("PEERHOST has no Claude sign-in", err)

    def test_refuses_a_host_it_cannot_reach(self):
        km._remotes["PEERHOST"] = {"host": "PEERHOST", "status": "up", "local_port": _free_port(), "token": PEER_TOKEN}
        idn, _h, err = km._login_link_source("PEERHOST")
        self.assertIsNone(idn)
        self.assertIn("could not reach PEERHOST's kernel", err)
        km._remotes["PEERHOST"]["status"] = "down"
        km._remotes["PEERHOST"]["detail"] = "ssh: connection refused"
        self.assertIn("not connected just now (ssh: connection refused)", km._login_link_source("PEERHOST")[2])
        err = km._login_link_source("OTHERHOST")[2]
        self.assertIn("no connected kernel is named 'OTHERHOST'", err)
        self.assertIn("connected: PEERHOST", err, "the refusal names what is connected")

    def test_refuses_an_older_build_that_does_not_report_its_sign_ins_account(self):
        old = _peer_machine()
        old.pop("identity")
        self.peer(old)
        self.assertIn("older build", km._login_link_source("PEERHOST")[2])


class Served(Base):
    """The kernel's own handler on a loopback port, and one authed request to /logins."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _req(self, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d/logins" % self.port, data=data,
                                     headers={"Content-Type": "application/json", "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())


class Doors(Served):
    """GET /logins and POST /logins {"link"} / {"unlink"} through the kernel's own handler."""

    def test_get_reports_the_sign_ins_identity_for_another_machines_link(self):
        st, d = self._req()
        self.assertEqual(st, 200)
        self.assertEqual(d["machine"]["identity"], {"acct": DIGEST, "orgDigest": ORGD, "org": "Example Org",
                                                    "kind": "enterprise", "email": "dev@example.com"})
        self.assertIsNone(d["logins"][0]["linked"])
        self.assertFalse(d["logins"][0]["ownAccount"])
        km._machine_identity = lambda: {"state": "none"}
        st, d = self._req()
        self.assertNotIn("identity", d["machine"], "no account signed in: nothing to copy")

    def test_link_by_label_then_unlink(self):
        self.peer(_peer_machine())
        st, d = self._req({"link": {"login": "enterprise", "sameAs": "PEERHOST"}})
        self.assertEqual((st, d["ok"]), (200, True), d)
        self.assertEqual(d["linked"]["host"], "PEERHOST")
        self.assertEqual(d["account"], "dev@example.com · Example Org · enterprise")
        self.assertEqual(lg.linked(lg.read_record(self.state, LID))["acct"], DIGEST)
        st, d = self._req()
        row = d["logins"][0]
        self.assertEqual(row["linked"]["host"], "PEERHOST", "the list says which machine the user linked it to")
        self.assertEqual(row["display"], "enterprise · dev@example.com · Example Org · enterprise")
        self.assertNotIn("token-read", json.dumps(d), "the command itself never rides the list")
        st, d = self._req({"unlink": LID})
        self.assertEqual((st, d["ok"]), (200, True), d)
        self.assertIsNone(lg.linked(lg.read_record(self.state, LID)))
        st, d = self._req({"unlink": "enterprise"})
        self.assertEqual((st, d["ok"]), (200, False))
        self.assertIn("not linked", d["error"])

    def test_a_refused_link_writes_nothing(self):
        self.peer(_peer_machine(state="none", identity=None))
        for target in ("PEERHOST", "OTHERHOST", ""):
            st, d = self._req({"link": {"login": "enterprise", "sameAs": target}})
            self.assertEqual((st, d["ok"]), (200, False), d)
            self.assertNotIn("identity", lg.read_record(self.state, LID))
        st, d = self._req({"link": {"login": "Nope", "sameAs": "local"}})
        self.assertIn("no stored login named", d["error"])
        st, d = self._req({"link": "enterprise"})
        self.assertEqual(st, 400)


class Relay(Served):
    """`romp login link … --on <host>`: attachments often run one way (one machine's ssh reaches the other's kernel and
    not back), so the kernel that reaches both reads the sign-in from its source and asks the kernel holding the stored
    login to record the identity it read. Here the record's kernel is this very handler, attached as PEERHOST."""

    def attach_self(self):
        km._remotes["PEERHOST"] = {"host": "PEERHOST", "status": "up", "local_port": self.port,
                                   "token": os.environ["ROMP_SERVE_TOKEN"]}

    def test_link_and_unlink_on_a_connected_kernel_carry_the_sign_in_read_here(self):
        self.attach_self()
        st, d = self._req({"link": {"login": "enterprise", "sameAs": "local"}, "on": "PEERHOST"})
        self.assertEqual((st, d["ok"], d["on"]), (200, True, "PEERHOST"), d)
        idn = lg.linked(lg.read_record(self.state, LID))
        self.assertEqual((idn["host"], idn["acct"], idn["email"]), ("TESTHOST", DIGEST, "dev@example.com"),
                         "the sign-in read here, under this machine's own name")
        st, d = self._req({"unlink": "enterprise", "on": "PEERHOST"})
        self.assertEqual((st, d["ok"]), (200, True), d)
        self.assertIsNone(lg.linked(lg.read_record(self.state, LID)))
        st, d = self._req({"link": {"login": "Nope", "sameAs": "local"}, "on": "PEERHOST"})
        self.assertEqual(d["error"], "PEERHOST's kernel refused: no stored login named 'Nope'")
        st, d = self._req({"link": {"login": "enterprise", "sameAs": "local"}, "on": "local"})
        self.assertEqual((d["ok"], d.get("on")), (True, None), "on this machine is no relay")

    def test_the_relay_refuses_what_it_cannot_read_or_reach_and_an_older_build(self):
        km._machine_identity = lambda: {"state": "none"}
        self.attach_self()
        st, d = self._req({"link": {"login": "enterprise", "sameAs": "local"}, "on": "PEERHOST"})
        self.assertIn("has no Claude sign-in", d["error"], "the sign-in is read before anything is asked of the record's kernel")
        km._machine_identity = lambda: _signed_in()
        old = self.peer(_peer_machine(), host="OLDHOST")
        old.post_answer = (400, {"ok": False, "error": "add {label, tokenCmd} or remove (a stored login's id or label) required"})
        st, d = self._req({"link": {"login": "enterprise", "sameAs": "local"}, "on": "OLDHOST"})
        self.assertIn("OLDHOST's kernel is an older build that cannot record a link", d["error"])
        self.assertEqual(old.posts[0]["link"]["identity"]["acct"], DIGEST, "the identity rides to the record's kernel")
        km._remotes["GONEHOST"] = {"host": "GONEHOST", "status": "up", "local_port": _free_port(), "token": PEER_TOKEN}
        st, d = self._req({"unlink": "enterprise", "on": "GONEHOST"})
        self.assertIn("could not reach GONEHOST's kernel", d["error"])
        self.assertNotIn("identity", lg.read_record(self.state, LID), "nothing was written anywhere")
        st, d = self._req({"link": {"login": "enterprise", "identity": {"acct": DIGEST}}})
        self.assertEqual(st, 400, "a relayed identity names the machine it is from")


class SpendAccounts(Base):
    """Two hosts' rows in the spend breakdown merge after a link and split after an unlink. The stored login lives on
    PEERHOST (this state root plays its kernel: _spend_billing reads the record there), the sign-in on TESTHOST."""

    def accounts(self):
        machine = dict(_signed_in(), route="machine")
        return km._spend_accounts([("TESTHOST", [machine]), ("PEERHOST", km._spend_billing({"login:" + LID}))])

    def test_rows_merge_after_a_link_and_split_after_an_unlink(self):
        before = self.accounts()
        self.assertEqual([a["name"] for a in before], ["Enterprise seat", "enterprise"])
        self.assertEqual(before[1]["note"], "account not recorded")
        self.assertIn("romp login link", before[1]["title"], "the unrecorded row says how to record it")
        lg.link(self.state, LID, _signed_in(), "TESTHOST")
        billing = km._spend_billing({"login:" + LID})
        self.assertEqual((billing[0]["acct"], billing[0]["linked"]), (DIGEST, "TESTHOST"))
        after = self.accounts()
        self.assertEqual([a["name"] for a in after], ["Enterprise seat"], json.dumps(after))
        self.assertEqual([r["via"] for r in after[0]["routes"]], ["TESTHOST sign-in", "token on PEERHOST"])
        lg.unlink(self.state, LID)
        self.assertEqual([a["name"] for a in self.accounts()], ["Enterprise seat", "enterprise"])

    def test_the_hover_says_the_link_is_the_users(self):
        lg.link(self.state, LID, _signed_in(), "TESTHOST")
        title = self.accounts()[0]["title"]
        self.assertIn("Token on PEERHOST linked by you to TESTHOST's sign-in.", title)
        self.assertTrue(title.startswith("dev@example.com · Example Org · enterprise. "), title)
        self.assertNotIn("verified", title)
        # a machine that signed into another account since the link: the link holds the account copied, so they split
        machine = dict(_signed_in(acct="b2b2b2b2b2b2"), route="machine")
        accts = km._spend_accounts([("TESTHOST", [machine]), ("PEERHOST", km._spend_billing({"login:" + LID}))])
        self.assertEqual(len(accts), 2, json.dumps(accts))
        self.assertIn("linked by you", accts[1]["title"], "a linked login alone still says whose statement it is")

    def test_a_record_that_names_its_own_account_outranks_a_link(self):
        lg.link(self.state, LID, _signed_in(acct="b2b2b2b2b2b2"), "TESTHOST")
        rec = lg.read_record(self.state, LID)
        rec["acctDigest"] = DIGEST
        lg.write_record(self.state, rec)
        billing = km._spend_billing({"login:" + LID})
        self.assertEqual(billing[0]["acct"], DIGEST)
        self.assertNotIn("linked", billing[0])


if __name__ == "__main__":
    unittest.main()
