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
  * A machine's sign-in is named by the account signed in now, and its account's hover says so; a session row names the
    stored login its last launch carried, never the pick (the #74 review's account-switch and bills-suffix findings).

Synthetic only: the notes-api world of tests/test_spend_detail.py (placeholder uuids, TESTHOST), dev@example.com,
Example Org, invented digests and record ids."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic state before any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)              # a live kernel's export outranks the XDG floor
sys.path.insert(0, HERE)
import test_spend_detail as _sd   # noqa: E402  the same synthetic world (the module, not its classes)

km = _sd.km
NOW, WEB, API = _sd.NOW, _sd.WEB, _sd.API
DIGEST = "a1a1a1a1a1a1"            # an invented account digest (the shape _claude_account prints)
OTHER = "b2b2b2b2b2b2"
ORGD = "c3c3c3c3c3c3"
ORGD2 = "d4d4d4d4d4d4"
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

    def test_a_shared_email_never_makes_two_accounts_one(self):
        # one address on two accounts: another organisation, or another kind, says so, and where the identities cannot
        # tell the email does not decide either; it is never the identity
        accts = km._spend_accounts([("TESTHOST", [_machine()]),
                                    ("PEERHOST", [_stored(org="Other Org", kind="enterprise", email="dev@example.com")])])
        self.assertEqual(_names(accts), ["Enterprise seat · Example Org", "Enterprise seat · Other Org"], json.dumps(accts))
        accts = km._spend_accounts([("TESTHOST", [_machine()]),
                                    ("PEERHOST", [_stored(org="Example Org", kind="personal", email="dev@example.com")])])
        self.assertEqual(_names(accts), ["Enterprise seat", "Personal plan"], json.dumps(accts))
        accts = km._spend_accounts([("THIRDHOST", [_machine(kind="", org="")]),
                                    ("PEERHOST", [_stored(org="Example Org", kind="enterprise", email="dev@example.com")])])
        self.assertEqual(len(accts), 2, "a digest-only sign-in and an organisation-only login cannot tell: " + json.dumps(accts))

    def test_a_route_joins_a_group_only_when_no_member_refuses_it(self):
        # PEERHOST's stored login knows only its organisation and kind, so it joins the machine's seat; THIRDHOST's names
        # another account digest; PEERHOST's login matches it (the same organisation and kind) but the machine's sign-in
        # refuses it, and one refusal keeps it out. Asserted by property: the other account's row carries no route of
        # this one
        machine, peer = _machine(), _stored(org="Example Org", kind="enterprise")
        third = _stored(lid=LID2, label="work", acct=OTHER, org="Example Org", kind="enterprise")
        # the world this needs, asserted so that a change to the account rule rebuilds the test instead of leaving it green
        # and testing nothing: THIRDHOST faces a group with one member that matches it and one that refuses it
        self.assertEqual([km._same_account(dict(machine), dict(peer)), km._same_account(dict(peer), dict(third)),
                          km._same_account(dict(machine), dict(third))], [True, True, False],
                         "machine~PEERHOST, PEERHOST~THIRDHOST, machine~THIRDHOST: rebuild this world if the rule changed")
        accts = km._spend_accounts([("TESTHOST", [machine]), ("PEERHOST", [peer]), ("THIRDHOST", [third])])
        seat = next(a for a in accts if any(r["route"] == "machine" for r in a["routes"]))
        self.assertIn(("PEERHOST", "login:" + LID), [(r["host"], r["route"]) for r in seat["routes"]],
                      "PEERHOST's login sits in the machine's row, so THIRDHOST meets both verdicts: " + json.dumps(accts))
        other = next(a for a in accts if any(r["route"] == "login:" + LID2 for r in a["routes"]))
        self.assertNotIn(("TESTHOST", "machine"), [(r["host"], r["route"]) for r in other["routes"]],
                         "two account digests never share a row: " + json.dumps(accts))

    # every road _same_account takes, as two logins' identities and its verdict on them: where they tell the two apart
    # (False) and where they cannot tell (None)
    ROADS = (
        ("the account digests differ", {"acct": DIGEST}, {"acct": OTHER}, False),
        ("a kind word is missing", {"acct": DIGEST}, {"org": "Example Org", "kind": "enterprise"}, None),
        ("the organisation digests differ", {"org": "Example Org", "orgDigest": ORGD, "kind": "enterprise"},
         {"org": "Example Org", "orgDigest": ORGD2, "kind": "enterprise"}, False),
        ("the kind words differ under one organisation digest", {"org": "Example Org", "orgDigest": ORGD, "kind": "enterprise"},
         {"org": "Example Org", "orgDigest": ORGD, "kind": "personal"}, False),
        ("the organisations differ", {"org": "Example Org", "kind": "enterprise"}, {"org": "Other Org", "kind": "enterprise"}, False),
        ("an organisation is missing", {"acct": DIGEST, "kind": "enterprise"}, {"org": "Example Org", "kind": "enterprise"}, None),
        ("the kind words differ under one organisation name", {"org": "Example Org", "kind": "enterprise"},
         {"org": "Example Org", "kind": "personal"}, False),
    )

    def test_a_shared_label_or_email_never_makes_two_accounts_one_on_any_road(self):
        # the label is the user's word and an email is an address two accounts can share: neither is the identity. Two
        # logins under one label and one email stay two accounts on every road, so a rule by either fails here wherever
        # in _same_account it sits
        for road, a, b, verdict in self.ROADS:
            with self.subTest(road=road):
                self.assertIs(km._same_account(dict(a), dict(b)), verdict, "the world gets the verdict its road gives")
                accts = km._spend_accounts([("PEERHOST", [_stored(label="work", email="dev@example.com", **a)]),
                                            ("THIRDHOST", [_stored(lid=LID2, label="work", email="dev@example.com", **b)])])
                self.assertEqual([[r["host"] for r in x["routes"]] for x in accts], [["PEERHOST"], ["THIRDHOST"]],
                                 "two accounts: " + json.dumps(accts))

    def test_the_hover_says_a_machines_sign_in_is_named_by_the_account_signed_in_now(self):
        # the ledger stamps no account per turn, so a machine's sign-in spend is filed under the account signed in now
        # and a switch of account moves its earlier sign-in spend to the new account's row: the hover says so (the #74
        # review's account-switch finding)
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("PEERHOST", [_stored(acct=DIGEST)])])
        title = accts[0]["title"]
        self.assertTrue(title.startswith("dev@example.com · Example Org · enterprise. "), "the account's identity first: " + title)
        self.assertIn("TESTHOST is signed in to this account now", title)
        # …and says what that means after a switch, which docs/reference.md promises the hover says
        self.assertIn("including any from before a switch of account", title, "the switch clause, one host: " + title)
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("THIRDHOST", [_machine(kind="")]), ("PEERHOST", [_machine()])])
        self.assertIn("TESTHOST, THIRDHOST and PEERHOST are signed in to this account now", accts[0]["title"])
        self.assertIn("including any from before a switch of account", accts[0]["title"], "the switch clause, three hosts: " + accts[0]["title"])
        # a sign-in whose identity names only its account digest (no email, organisation or kind): the hover is the
        # sentence alone, with no separator before it
        accts = km._spend_accounts([("TESTHOST", [_machine(kind="", org="", email="")])])
        self.assertTrue(accts[0]["title"].startswith("TESTHOST is signed in to this account now"), accts[0]["title"])
        # a stored login's token is no sign-in: its account is its record's, and a linked one's is the account copied at
        # the link, so neither is said to be the one signed in now
        accts = km._spend_accounts([("PEERHOST", [_stored(acct=DIGEST, org="Example Org", kind="enterprise", linked="TESTHOST")])])
        self.assertNotIn("signed in to this account now", accts[0]["title"], accts[0]["title"])
        self.assertIn("Token on PEERHOST linked by you to TESTHOST's sign-in.", accts[0]["title"])
        # a sign-in and a linked token in one account: the sentence comes before the link's
        accts = km._spend_accounts([("TESTHOST", [_machine()]), ("PEERHOST", [_stored(acct=DIGEST, linked="TESTHOST")])])
        title = accts[0]["title"]
        self.assertEqual(len(accts), 1, json.dumps(accts))
        self.assertIn("linked by you", title)
        self.assertIn("TESTHOST is signed in to this account now", title)
        self.assertLess(title.index("TESTHOST is signed in to this account now"), title.index("linked by you"), title)

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
        self.assertEqual([r["estimate"] for r in unk["routes"]], [True, True, True],
                         "a sign-in's turns are a seat's (an estimate); untracked spend is no known charge to the key either "
                         "(2026-10-08 review: its row lacked the approx mark its card carried)")
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

    def test_a_linked_stored_login_is_the_sign_in_its_linked_to_its_spend_from_before_the_link_included(self):
        # `romp login link` (2026-10-01): the record is what is linked, so every turn the ledger filed under the login,
        # all of them older than the link here, counts as that account, and an unlink splits it off again
        rec = km.lg.read_record(self.state, LID)
        rec.pop("acctDigest")
        km.lg.write_record(self.state, rec)
        d = km._spend_detail(now=NOW)
        self.assertEqual([a["name"] for a in d["accounts"]], ["Enterprise seat", "enterprise", "API key", "Unknown"])
        km.lg.link(self.state, LID, {k: v for k, v in _machine().items() if k != "route"}, "TESTHOST")
        d = km._spend_detail(now=NOW)
        self.assertEqual([a["name"] for a in d["accounts"]], ["Enterprise seat", "API key", "Unknown"])
        self.assertEqual(_vias(d["accounts"][0]), ["TESTHOST sign-in", "token on TESTHOST"])
        self.assertIn("Token on TESTHOST linked by you to TESTHOST's sign-in.", d["accounts"][0]["title"])
        km.lg.unlink(self.state, LID)
        d = km._spend_detail(now=NOW)
        self.assertEqual([a["name"] for a in d["accounts"]], ["Enterprise seat", "enterprise", "API key", "Unknown"])

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

    def test_an_account_switch_on_the_machine_names_its_spend_by_the_account_signed_in_now_and_says_so(self):
        # the same ledger read under the account signed in before and after a switch: the machine's sign-in spend moves to
        # the new account's row (the ledger keeps no per-turn account), and that row's hover says why (the #74 review's
        # account-switch finding)
        before = km._spend_detail(now=NOW)["accounts"]
        km._machine_identity = lambda: {k: v for k, v in _machine(acct=OTHER, kind="personal", org="", email="me@example.com").items()
                                        if k != "route"}
        after = km._spend_detail(now=NOW)["accounts"]
        self.assertEqual(_vias(before[0]), ["TESTHOST sign-in", "token on TESTHOST"])
        seat = next(a for a in after if "TESTHOST sign-in" in _vias(a))
        self.assertEqual(seat["name"], "Personal plan")
        self.assertIn("TESTHOST is signed in to this account now", seat["title"])

    def _session_row(self, reg):
        """WEB's row in the local half, its reg row as given (the backend's module, as the kernel loads it)."""
        from romp_load import load_source
        sbm = load_source("romp_sdk_backend_spendaccts", os.path.join(os.path.dirname(HERE), "bin", "romp_sdk_backend.py"))
        be = sbm.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)
        km._sdk = lambda: be
        sbm.write_reg(Path(self.state), WEB, dict({"sid": WEB, "name": "web", "cwd": "/tmp", "alive": True}, **reg))
        return next(s for s in km._spend_detail_local(now=NOW)["sessions"] if s["sid"] == WEB)

    def test_a_session_on_a_stored_login_carries_the_record_id_and_label(self):
        reg = km.lg   # the registry module the kernel loaded
        row = self._session_row({"auth": "login", "authLogin": LID, "launchedLogin": LID, "authLoginLive": LID})
        self.assertEqual((row["loginId"], row["loginLabel"], row["login"]), (LID, "enterprise", reg.display(reg.read_record(self.state, LID))))

    def test_the_row_names_the_login_its_launch_carried_never_the_pick(self):
        # the recorder files a turn's byLogin dollars under the login its launch carried (the reg's launchedLogin), so the
        # row reads that field (the #74 review's bills-suffix finding). A token the CLI did not use leaves the pick set and
        # clears the launch's login, and the key or the machine's sign-in billed: no stored login is named
        row = self._session_row({"auth": "login", "authLogin": LID, "launchedLogin": "", "authLoginLive": ""})
        self.assertFalse({"login", "loginId", "loginLabel"} & set(row), "a refused pick billed nothing to it: " + json.dumps(row))
        # a session with no pick, launched on the stored login the machine defaults to: its dollars sit in that login's row
        row = self._session_row({"launchedLogin": LID, "authLoginLive": LID})
        self.assertEqual((row.get("loginId"), row.get("loginLabel")), (LID, "enterprise"), json.dumps(row))
        # a value that is not a record id is no login
        row = self._session_row({"authLogin": LID, "launchedLogin": "../" + LID})
        self.assertNotIn("loginId", row)
        # nor is a reg that is not a mapping (read_reg returns whatever JSON the file holds, a corrupt one's list included)
        for junk in ([LID], "launchedLogin", None):
            self.assertEqual(km._reg_launched_login(junk), "", repr(junk))
        # nor a value that is not a string: a corrupt reg's twelve-digit JSON number, whose digits alone would pass the
        # record id's pattern
        self.assertEqual(km._reg_launched_login({"launchedLogin": 123456789012}), "", "a number is no record id")


if __name__ == "__main__":
    unittest.main()
