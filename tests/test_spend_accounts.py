#!/usr/bin/env python3
"""The spend modal names each Claude account one way (the user 2026-10-01, who read an email in the spend display and
could not tell whether it was their enterprise account or something connected before, nor how the breakdown worked).

One enterprise seat reached two ways, a machine's own sign-in on one host and a stored login's token on another, is
ONE account in GET /spend/detail's `accounts`, named plainly ("Enterprise seat"), its routes the quiet words beside the
name ("TESTHOST sign-in", "token on PEERHOST") and the email only in the hover. The pieces under test:
  * _bucket_routes splits each ledger bucket by WHO billed it: the key sub-count, each stored login's byLogin row, the
    machine's own sign-in (the rest), and "untracked" for a bucket from before the ledger tracked billing.
  * _spend_accounts folds routes into accounts by identity alone: the account digest, else the organisation plus the
    kind word. Different accounts stay apart; a stored login whose record names no account is its own row, said to be
    unrecorded, never matched by its label; a signed-out machine's sign-in is Unknown.
  * The local half carries the route series and the routes' identities; the single-host and merged payloads carry the
    accounts, the merged one naming an older peer whose series carry no routes.

Synthetic only: the notes-api world of tests/test_spend_detail.py (placeholder uuids, TESTHOST), dev@example.com,
Example Org, invented digests and record ids."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_spend_detail as _sd   # noqa: E402  the same synthetic world (the module, not its classes)

km = _sd.km
NOW, WEB, API = _sd.NOW, _sd.WEB, _sd.API
DIGEST = "a1a1a1a1a1a1"            # an invented account digest (the shape _claude_account prints)
OTHER = "b2b2b2b2b2b2"
ORGD = "c3c3c3c3c3c3"
LID = "%012x" % 0x0A0B0C0D0E0F     # an invented stored-login record id
LID2 = "%012x" % 0x0F0E0D0C0B0A


def _machine(acct=DIGEST, kind="enterprise", org="Example Org", email="dev@example.com"):
    return {"route": "machine", "state": "ok", "acct": acct, "org": org, "orgDigest": ORGD if org else "",
            "kind": kind, "email": email}


def _stored(lid=LID, label="enterprise", **kw):
    m = {"route": "login:" + lid, "label": label, "display": label, "email": "", "org": "", "kind": ""}
    m.update(kw)
    return m


def _names(accts):
    return [a["name"] for a in accts]


def _vias(a):
    return [r["via"] for r in a["routes"]]


class BucketRoutes(unittest.TestCase):
    def test_a_bucket_splits_into_the_key_each_stored_login_and_the_machines_sign_in(self):
        e = {"usd": 10.0, "turns": 10, "bySid": {WEB: {"usd": 10.0, "turns": 10, "tok": 1}},
             "key": {"usd": 4.0, "turns": 3, "tok": 1}, "byLogin": {LID: {"usd": 2.5, "turns": 2, "tok": 1}}}
        self.assertEqual(km._bucket_routes(e), {"key": (4.0, 3), "login:" + LID: (2.5, 2), "machine": (3.5, 5)})

    def test_a_bucket_from_before_billing_was_tracked_is_untracked_whole(self):
        self.assertEqual(km._bucket_routes({"usd": 3.0, "turns": 4}), {"untracked": (3.0, 4)},
                         "no key split and no per-session map: its non-key part cannot be told from a key turn")
        self.assertEqual(km._bucket_routes({"usd": 0.0, "turns": 0}), {})

    def test_a_tracked_bucket_with_no_key_turns_is_the_machines_and_rounding_is_no_route(self):
        self.assertEqual(km._bucket_routes({"usd": 2.0, "turns": 2, "bySid": {}}), {"machine": (2.0, 2)})
        e = {"usd": 1.000002, "turns": 1, "key": {"usd": 1.0, "turns": 1}}
        self.assertEqual(km._bucket_routes(e), {"key": (1.0, 1)}, "a recorder's rounding residue is not a machine route")


class Accounts(unittest.TestCase):
    def test_the_machines_sign_in_and_a_stored_login_sharing_an_account_digest_are_one_account(self):
        accts = km._spend_accounts([("TESTHOST", [_machine(), {"route": "key"}]),
                                    ("PEERHOST", [_stored(acct=DIGEST)])])
        seat = accts[0]
        self.assertEqual(seat["name"], "Enterprise seat", "one plain name for the seat")
        self.assertEqual(seat["kind"], "seat")
        self.assertEqual(_vias(seat), ["TESTHOST sign-in", "token on PEERHOST"], "both routes, the machine's first")
        self.assertTrue(all(r["estimate"] for r in seat["routes"]), "seat dollars are estimates at API prices")
        self.assertNotIn("@", seat["name"], "the email stays off the main label")
        self.assertIn("dev@example.com", seat["title"], "…and in the hover")
        self.assertIn("Example Org", seat["title"])
        self.assertEqual([a["kind"] for a in accts], ["seat", "key"], "one seat row, then the key: " + json.dumps(accts))
        key = accts[1]
        self.assertEqual((key["name"], _vias(key)), ("API key", ["TESTHOST"]))
        self.assertFalse(key["routes"][0]["estimate"], "key-billed dollars are what the key was billed")

    def test_the_organisation_plus_the_kind_word_is_one_account_when_no_digest_is_recorded(self):
        accts = km._spend_accounts([("TESTHOST", [_machine()]),
                                    ("PEERHOST", [_stored(org="example  org", kind="enterprise")])])
        self.assertEqual(_names(accts), ["Enterprise seat"])
        self.assertEqual(_vias(accts[0]), ["TESTHOST sign-in", "token on PEERHOST"])
        # the same organisation under another kind is another account
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("PEERHOST", [_stored(org="Example Org", kind="personal")])])
        self.assertEqual(_names(accts), ["Enterprise seat", "Personal plan"])

    def test_two_machines_signed_into_one_account_are_one_row(self):
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("THIRDHOST", [_machine(kind="")])])
        self.assertEqual(_names(accts), ["Enterprise seat"], "a keychain machine reads no kind word; its digest still matches")
        self.assertEqual(_vias(accts[0]), ["TESTHOST sign-in", "THIRDHOST sign-in"])

    def test_different_accounts_stay_separate(self):
        accts = km._spend_accounts([("TESTHOST", [_machine()]),
                                    ("PEERHOST", [_stored(acct=OTHER, org="Example Org", kind="enterprise")])])
        self.assertEqual(len(accts), 2, "a digest that differs is another account, whatever the organisation and kind say")
        self.assertEqual(_names(accts), ["Enterprise seat · TESTHOST sign-in", "Enterprise seat · token on PEERHOST"],
                         "two seats in one organisation: told apart by where each is reached, never by an email")
        accts = km._spend_accounts([("TESTHOST", [_machine()]),
                                    ("PEERHOST", [_stored(acct=OTHER, org="Other Org", kind="enterprise")])])
        self.assertEqual(_names(accts), ["Enterprise seat · Example Org", "Enterprise seat · Other Org"])
        accts = km._spend_accounts([("TESTHOST", [_machine(kind="personal")]), ("PEERHOST", [_stored(acct=OTHER, kind="enterprise")])])
        self.assertEqual(_names(accts), ["Personal plan", "Enterprise seat"])

    def test_a_stored_login_whose_record_names_no_account_is_said_to_be_unrecorded_never_matched_by_its_label(self):
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("PEERHOST", [_stored(label="enterprise")])])
        self.assertEqual(_names(accts), ["Enterprise seat", "enterprise"], "the label matches the kind word, and still no merge")
        unrec = accts[1]
        self.assertEqual(unrec["note"], "account not recorded")
        self.assertIn("isn't recorded", unrec["title"])
        self.assertFalse(unrec["title"].startswith("enterprise."), "a display that is only the label is not repeated")
        self.assertEqual(_vias(unrec), ["token on PEERHOST"])
        self.assertTrue(unrec["routes"][0]["estimate"], "a stored login is a subscription token: its dollars are estimates")
        # a kind word with no organisation is not an identity either
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("PEERHOST", [_stored(kind="enterprise")])])
        self.assertEqual(accts[1].get("note"), "account not recorded")
        # nor does a digest-only machine match a stored login known only by organisation and kind: cannot tell
        accts = km._spend_accounts([("THIRDHOST", [_machine(kind="")]), ("PEERHOST", [_stored(org="Example Org", kind="enterprise")])])
        self.assertEqual(len(accts), 2, json.dumps(accts))
        # a removed record says so
        accts = km._spend_accounts([("PEERHOST", [{"route": "login:" + LID2, "label": "login " + LID2, "missing": True}])])
        self.assertEqual((accts[0]["name"], accts[0]["note"]), ("login " + LID2, "record missing"))

    def test_a_signed_out_or_unreadable_sign_in_and_untracked_spend_are_unknown(self):
        accts = km._spend_accounts([("TESTHOST", [{"route": "machine", "state": "unreadable"}, {"route": "untracked"}]),
                                    ("PEERHOST", [{"route": "machine", "state": "none"}, {"route": "key"}])])
        self.assertEqual([a["kind"] for a in accts], ["key", "unknown"])
        unk = accts[1]
        self.assertEqual(unk["name"], "Unknown")
        self.assertEqual(_vias(unk), ["TESTHOST's own sign-in, unreadable just now", "before billing was recorded",
                                      "PEERHOST's own sign-in, signed out now"])
        self.assertEqual([r["estimate"] for r in unk["routes"]], [True, False, True],
                         "a sign-in's turns are a seat's (an estimate); untracked spend may be the key's")
        self.assertEqual(_vias(accts[0]), ["PEERHOST"])


class Payload(unittest.TestCase):
    """The routes and the accounts through the real local half and both payload paths."""

    def setUp(self):
        self.t = _sd.SpendDetail("test_sessions_are_named_colored_sorted_and_flagged_live")
        self.t.setUp()
        self.state = km.jd.STATE
        (self.state / "session-hosts").write_text("off")     # this test mints its own state root (the rule in CLAUDE.md)
        self._saved = (km._machine_identity, km._sdk)
        km._machine_identity = lambda: {k: v for k, v in _machine().items() if k != "route"}
        km._sdk = lambda: None
        km.lg.write_record(self.state, {"id": LID, "label": "enterprise", "tokenCmd": "token-read 'romp login enterprise'",
                                        "addedAt": int(time.time()) - 86400, "acctDigest": DIGEST})
        d = json.loads((self.state / "spend.json").read_text())
        for e in d["hours"].values():
            if "bySid" in e:     # each attributed hour: a key turn of api's and a turn of web's on the stored login
                e["key"] = {"usd": 0.5, "turns": 1, "tok": 8000}
                e["byLogin"] = {LID: {"usd": 0.25, "turns": 1, "tok": 3000}}
        (self.state / "spend.json").write_text(json.dumps(d))

    def tearDown(self):
        km._machine_identity, km._sdk = self._saved
        self.t.tearDown()

    def test_the_local_half_carries_route_series_and_their_identities(self):
        p = km._spend_detail_local(now=NOW)
        routes = {r["route"]: r for r in p["hours"]["routes"]}
        self.assertEqual(sorted(routes), ["key", "login:" + LID, "machine", "untracked"])
        self.assertEqual({r["host"] for r in p["hours"]["routes"]}, {"TESTHOST"})
        self.assertAlmostEqual(sum(routes["key"]["usd"]), 0.5 * 60, places=3)
        self.assertEqual(sum(routes["login:" + LID]["turns"]), 60)
        self.assertAlmostEqual(sum(routes["untracked"]["usd"]), 3.0, places=3, msg="the hour from before attribution")
        n = len(p["hours"]["keys"])
        self.assertTrue(all(len(r["usd"]) == n and len(r["turns"]) == n for r in p["hours"]["routes"]), "on the series' axis")
        hour_total = sum(sum(s["usd"]) for s in p["hours"]["stacks"])
        self.assertAlmostEqual(sum(sum(r["usd"]) for r in p["hours"]["routes"]), hour_total, places=3,
                               msg="the routes split the same dollars the stacks draw")
        billing = {m["route"]: m for m in p["billing"]}
        self.assertEqual(billing["login:" + LID]["acct"], DIGEST, "a stored login's recorded account digest")
        self.assertEqual(billing["machine"]["acct"], DIGEST)
        self.assertNotIn("tokenCmd", json.dumps(p["billing"]), "labels and identities only, never the token command")

    def test_the_single_host_payload_folds_the_sign_in_and_the_stored_login_into_one_account(self):
        d = km._spend_detail(now=NOW)
        self.assertNotIn("billing", d, "the identities are folded, not shipped twice")
        self.assertEqual([a["name"] for a in d["accounts"]], ["Enterprise seat", "API key", "Unknown"])
        self.assertEqual(_vias(d["accounts"][0]), ["TESTHOST sign-in", "token on TESTHOST"])

    def test_the_merged_payload_folds_routes_across_hosts_and_names_an_older_peer(self):
        local = km._spend_detail_local(now=NOW)
        n = len(local["hours"]["keys"])
        usd = [0.0] * n; usd[-1] = 7.0
        trn = [0] * n; trn[-1] = 3
        peer = {"host": "PEERHOST", "scope": "total", "tz": "PEER", "tzOffsetMin": local["tzOffsetMin"],
                "sessions": [], "order": [], "unattributed": {"usd": 0, "tok": 0, "turns": 0},
                "billing": [{"route": "login:" + LID2, "label": "work", "display": "work", "acct": DIGEST}],
                "hours": {"keys": local["hours"]["keys"], "epochs": local["hours"]["epochs"], "stacks": [],
                          "routes": [{"host": "PEERHOST", "route": "login:" + LID2, "usd": usd, "turns": trn}]},
                "days": {"keys": local["days"]["keys"], "stacks": [], "routes": []}}
        old = {"host": "OLDHOST", "sessions": [], "unattributed": {}, "hours": {"keys": [], "stacks": []},
               "days": {"keys": [], "stacks": []}}
        hosts = [{"host": "TESTHOST", "status": "ok"}, {"host": "PEERHOST", "status": "ok"}, {"host": "OLDHOST", "status": "ok"}]
        d = km._merge_spend_details([("TESTHOST", local), ("PEERHOST", peer), ("OLDHOST", old)], hosts, local)
        self.assertNotIn("billing", d)
        seat = d["accounts"][0]
        self.assertEqual(seat["name"], "Enterprise seat")
        self.assertEqual(_vias(seat), ["TESTHOST sign-in", "token on TESTHOST", "token on PEERHOST"],
                         "the peer's stored login is the same account by its digest")
        pr = [r for r in d["hours"]["routes"] if r["host"] == "PEERHOST"]
        self.assertEqual(len(pr), 1)
        self.assertEqual((pr[0]["usd"][-1], pr[0]["turns"][-1]), (7.0, 3), "the peer's route series on our axis")
        self.assertEqual(d["accountsMissing"], ["OLDHOST"], "an older build's dollars are not in the summary: said by name")

    def test_a_session_on_a_stored_login_carries_the_record_id_and_label(self):
        from romp_load import load_source
        reg = km.lg   # the registry module the kernel loaded
        sbm = load_source("romp_sdk_backend_spendaccts", os.path.join(os.path.dirname(HERE), "bin", "romp_sdk_backend.py"))
        be = sbm.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)
        km._sdk = lambda: be
        sbm.write_reg(Path(self.state), WEB, {"sid": WEB, "name": "web", "cwd": "/tmp", "auth": "login", "authLogin": LID, "alive": True})
        row = next(s for s in km._spend_detail_local(now=NOW)["sessions"] if s["sid"] == WEB)
        self.assertEqual((row["loginId"], row["loginLabel"], row["login"]), (LID, "enterprise", reg.display(reg.read_record(self.state, LID))))


if __name__ == "__main__":
    unittest.main()
