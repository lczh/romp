#!/usr/bin/env python3
"""The usage panel BY ACCOUNT on the served landing page (plans/usage-panel-by-account.md; the user 2026-09-19, whose three
machines on one login drew three identical columns): the landing page is served from a hermetic kernel module (its own
_landing() HTML, /usage/fleet answered from a synthetic four-machine mesh), and a real browser hovers the usage readout.
Two machines on one account (TESTHOSTA, this machine, and TESTHOSTB, whose own snapshot lagged with a different reading)
render ONE block: the account line as its head, the meters once, two names beneath in the tab strip's quiet host dress,
TESTHOSTB named lagging with its own report's age, one updated-ago line (the oldest of the pair). A third machine on
another account (TESTHOSTC) is a second block beside it. A fourth machine attached and up with no report (TESTHOSTD) is
named after the blocks with its state, never a column. The rail's own aggregate bars are unchanged. Each block is headed
by the account's kind, the spend detail's name for it, never an email (2026-10-01). A second lab (NoWindowsServed) puts
this machine on an account that answered it has no usage windows beside a machine still holding a weeks-old reading on
the same account, with spend split by account: one block, headed by the kind, saying so in one line, no bars, no age, no
lagging note, and the usage section's rows by account with the seat's dollars marked as an estimate (the user 2026-10-01;
the rows per calendar window since 2026-10-08). Synthetic only: TESTHOST names and an invented email that must never render. Skips LOUDLY without a playwright (set
ROMP_PLAYWRIGHT_NODE_PATH to a node_modules holding it) unless ROMP_SERVED_TESTS_REQUIRE=1 makes that a failure."""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # the hermetic state root BEFORE the kernel loads (tests/test_state_isolation_order.py)
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_usage_by_account", os.path.join(BIN, "romp-kernel"))

ACCT1, ACCT2 = "d1" * 20, "d2" * 20          # opaque digests, as the kernel serves them
NAME1, NAME2 = "Personal plan", "Enterprise seat"   # each account named by its kind, as the kernel names it
EMAIL = "user@example.test"                          # an older build's acctLabel: carried on a row, never rendered


def _node_path():
    for cand in (os.environ.get("ROMP_PLAYWRIGHT_NODE_PATH", ""), os.path.join(ROOT, "vscode-extension", "node_modules")):
        if cand and os.path.isdir(os.path.join(cand, "playwright")):
            return cand
    return ""


def _windows(now, five, seven, fable, t):
    """One machine's usage payload: three windows with resets in the future, the report's own time t."""
    def seg(pct, span):
        return {"pct": pct, "resetsAt": now + span // 3, "color": [84, 178, 4]}
    return {"fiveHour": seg(five, 5 * 3600), "sevenDay": seg(seven, 7 * 86400), "fable": seg(fable, 7 * 86400), "t": t}


class UsageByAccountServed(unittest.TestCase):
    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    def setUp(self):
        np = _node_path()
        if not np:
            self._skip("no playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules that holds it")
        self.node_path = np
        self.td = tempfile.TemporaryDirectory()
        state = Path(self.td.name)
        self._saved = (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host, km._claude_account, km._auth_key_present)
        km.jd.STATE = state
        km.NAMES = state / "names"
        km.NAMES.mkdir()
        km._live_names = lambda tm: {}
        km._live_map = lambda: []
        km._self_host = lambda: "TESTHOSTA"
        km._claude_account = lambda: ACCT1
        km._auth_key_present = lambda: False
        now = int(time.time())
        # this machine's fresh reading; TESTHOSTB on the SAME account, an hour behind with a different 5-hour figure (lagging);
        # TESTHOSTC on another account; TESTHOSTD attached and up, no report yet
        rows, extra = self._rows(now)
        payload = json.dumps(dict({"rows": rows, "host": "TESTHOSTA", "noReport": ["TESTHOSTD"]}, **extra)).encode()
        html = km._landing().encode()
        blank = b"<!doctype html><html><body style='margin:0;background:#1e1e1e'></body></html>"

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                p = self.path.split("?", 1)[0]
                if p == "/":
                    return self._out(200, html, "text/html; charset=utf-8")
                if p == "/usage/fleet":
                    return self._out(200, payload, "application/json")
                if p == "/usage":
                    return self._out(200, json.dumps(rows[0]["usage"]).encode(), "application/json")
                if p.startswith("/media/") or p.startswith("/dist/"):
                    f = os.path.join(ROOT, p.lstrip("/"))
                    if p == "/dist/styles.css" and not os.path.isfile(f):
                        f = os.path.join(ROOT, "ui", "webview", "styles.css")
                    if os.path.isfile(f):
                        ct = "image/svg+xml" if f.endswith(".svg") else "application/javascript" if f.endswith(".js") else "application/octet-stream"
                        return self._out(200, open(f, "rb").read(), ct)
                    return self._out(404, b"", "text/plain")
                if p in ("/chat", "/feed", "/timeline", "/fleet", "/dashboard"):
                    return self._out(200, blank, "text/html; charset=utf-8")
                return self._out(200, b"{}", "application/json")

            def do_POST(self):
                return self._out(200, b"{}", "application/json")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _rows(self, now):
        return ([{"host": "", "acct": ACCT1, "usage": dict(_windows(now, 42, 17, 8, now - 120), acct=ACCT1, acctName=NAME1)},
                 {"host": "TESTHOSTB", "acct": ACCT1, "usage": dict(_windows(now, 31, 17, 8, now - 3600), acct=ACCT1, acctName=NAME1)},
                 {"host": "TESTHOSTC", "acct": ACCT2, "usage": dict(_windows(now, 73, 55, 12, now - 300), acct=ACCT2, acctName=NAME2)}], {})

    def tearDown(self):
        if hasattr(self, "srv"):
            self.srv.shutdown()
            self.srv.server_close()
        if hasattr(self, "_saved"):
            (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host, km._claude_account, km._auth_key_present) = self._saved
            self.td.cleanup()

    def _drive(self):
        shots = os.environ.get("ROMP_SHOTS", "")
        env = dict(os.environ, NODE_PATH=self.node_path)
        r = subprocess.run(["node", os.path.join(HERE, "usage_by_account_headless.js"), "http://127.0.0.1:%d/" % self.port, shots],
                           capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(r.returncode, 0, "the driver failed:\n" + r.stderr[-3000:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(o["errs"], [], "no page errors")
        return o

    def test_two_machines_on_one_account_are_one_block_a_third_on_another_a_second_and_the_unreported_one_is_named(self):
        o = self._drive()
        t = o["tip"]
        self.assertEqual(t["display"], "block", "the hover is up")
        self.assertEqual(len(t["blocks"]), 2, "one block per distinct login, not per machine: %r" % [b["head"] for b in t["blocks"]])
        self.assertEqual(t["cols"], 2, "two accounts side by side, one column each")
        self.assertEqual(t["colsDisplay"], "flex")
        self.assertEqual(t["hosts"], 0, "no per-machine column heading anywhere")
        one, two = t["blocks"]
        # the shared account's block: the account line as its head, the meters ONCE, this machine's fresh figures
        self.assertEqual(one["head"], NAME1)
        self.assertEqual(one["windows"], ["5 hours", "7 days", "Fable 5"], "the three meters, once")
        self.assertEqual(one["used"], ["42%", "17%", "8%"], "the freshest member's reading (this machine's), not TESTHOSTB's 31%")
        self.assertEqual(len(one["resets"]), 3, "each meter's reset, once")
        self.assertEqual(one["machines"], ["TESTHOSTA", "TESTHOSTB"], "the machines logged into it, in the panel's order (this machine first)")
        self.assertEqual(one["lag"], [" lagging, updated 1h ago"], "TESTHOSTB's own reading differed and is an hour old: named lagging with its age")
        self.assertIn("TESTHOSTB lagging, updated 1h ago", one["machinesLine"])
        self.assertEqual(one["age"], "updated 1h ago", "updated-ago once per block: the OLDEST report of the group")
        # the strip's quiet host dress on the names: italic, regular weight, a step smaller than the line
        self.assertEqual((one["dress"]["style"], one["dress"]["weight"]), ("italic", "400"), one["dress"])
        self.assertLess(float(one["dress"]["size"].rstrip("px")), 11.0, "a step smaller than the 11px line: %r" % one["dress"])
        # the other account's block
        self.assertEqual(two["head"], NAME2)
        self.assertEqual(two["used"], ["73%", "55%", "12%"])
        self.assertEqual(two["machines"], ["TESTHOSTC"])
        self.assertEqual(two["lag"], [], "alone on its account: nothing to lag behind")
        self.assertEqual(two["age"], "updated 5m ago")
        self.assertEqual(t["accts"], 2, "the login line once per block")
        # the machine attached and up with no report: named with its state, never a column
        self.assertEqual(len(t["noReport"]), 1, t["noReport"])
        self.assertEqual(t["noReport"][0], "no usage report yet from TESTHOSTD")
        self.assertFalse(t["spend"], "no key anywhere: no spend section")

    def test_the_rails_own_meter_is_unchanged_one_aggregate_set_of_bars(self):
        o = self._drive()
        r = o["rail"]
        self.assertEqual(r["bars"], 3, "the rail draws the three windows once, aggregated: %r" % r)
        self.assertEqual(r["cost"], "", "no spend recorded: no usage cell")



class NoWindowsServed(UsageByAccountServed):
    """This machine's account answered that it has no usage windows; TESTHOSTB, signed into the same account on an older
    build, still holds a reading from weeks ago and ships the login's email; this machine's usage splits between the key
    and the seat, and TESTHOSTC's older build sends no routes (the user 2026-10-01; per calendar window 2026-10-08)."""
    test_two_machines_on_one_account_are_one_block_a_third_on_another_a_second_and_the_unreported_one_is_named = None
    test_the_rails_own_meter_is_unchanged_one_aggregate_set_of_bars = None

    def _rows(self, now):
        def old(pct):
            return {"pct": pct, "resetsAt": now - 25 * 86400, "color": [84, 178, 4]}
        spend = {w: {"usd": 5.0, "tok": 10, "turns": 1} for w in ("hour", "day", "week", "month", "monthToDate")}
        win = {"routes": {"key": 5.0, "machine": 10.4}, "tok": {"tok": 2_000_000}}
        sw = {"tz": "", "today": win, "monthToDate": win, "billing": []}   # this machine's windows (built in the viewer's zone)
        rows = [{"host": "", "acct": ACCT2, "usage": {"noWindows": True, "t": now - 60, "acct": ACCT2, "acctName": NAME2, "spend": spend,
                                                      "spendWin": sw}},
                {"host": "TESTHOSTB", "acct": ACCT2, "usage": {"fiveHour": old(71), "sevenDay": old(16), "fable": old(27),
                                                               "t": now - 26 * 86400, "acct": ACCT2, "acctLabel": EMAIL}},
                {"host": "TESTHOSTC", "acct": "", "usage": {"apiKey": True, "acct": "", "spend": spend}}]
        return rows, {"accounts": [{"name": NAME2, "kind": "seat", "usd": 10.4, "est": True,
                                    "routes": [{"host": "TESTHOSTA", "route": "machine", "est": True}]},
                                   {"name": "API key", "kind": "key", "usd": 5.0, "est": False,
                                    "routes": [{"host": "TESTHOSTA", "route": "key", "est": False}]}],
                      "accountsMissing": ["TESTHOSTC"],
                      "missing": [{"host": "TESTHOSTC", "why": "older build: it sends no hourly usage", "at": None}]}

    def test_an_account_without_windows_is_said_once_and_the_spend_splits_by_account(self):
        o = self._drive()
        t = o["tip"]
        self.assertEqual(t["display"], "block", "the hover is up")
        self.assertEqual(len(t["blocks"]), 1, [b["head"] for b in t["blocks"]])
        b = t["blocks"][0]
        self.assertEqual(b["head"], NAME2, "headed by the account's kind")
        self.assertEqual(b["nowin"], "no 5-hour or weekly limits on this account")
        self.assertEqual(b["windows"], [], "no bars: TESTHOSTB's weeks-old reading is not the account's")
        self.assertEqual(b["machines"], ["TESTHOSTA", "TESTHOSTB"], "both machines signed into it")
        self.assertEqual(b["lag"], [], "nothing to lag behind")
        self.assertEqual(b["age"], "", "no age: nothing is read")
        self.assertFalse(t["email"], "no email anywhere in the hover")
        self.assertTrue(t["spend"], "the usage section")
        self.assertEqual(t["costRows"], ["Enterprise seat | \u2248$10 | \u2248$10", "API key charged | $5 | $5",
                                         "Total | \u2248$15 | \u2248$15", "tokens | 2.00M | 2.00M"], t["costRows"])
        self.assertIn("TESTHOSTC: older build: it sends no hourly usage; its usage is not in these figures", t["costNotes"], t["costNotes"])
        self.assertTrue(any(n.startswith("\u2248 marks romp\u2019s estimate") for n in t["costNotes"]), t["costNotes"])
        self.assertEqual(o["rail"]["cost"], "today\u2248$15this month\u2248$15TESTHOSTC missing",
                         "the cell: every route, the seat's estimate marked, and the machine its figures leave out named")
        self.assertEqual((o["rail"]["windows"], o["rail"]["pcts"]), ([], []), "no window bars on the rail: %r" % o["rail"])


class MonthlyAllowanceServed(NoWindowsServed):
    """The same account, now answering with its monthly spend limit (2026-10-01): the block draws that one bar, the
    percent used and the date it resets, in place of the one-line note."""
    test_an_account_without_windows_is_said_once_and_the_spend_splits_by_account = None

    def _rows(self, now):
        rows, extra = NoWindowsServed._rows(self, now)
        rows[0]["usage"]["monthly"] = {"pct": 23, "used": 11500.0, "limit": 50000.0, "currency": "USD", "month": "2026-10",
                                       "color": [84, 178, 4], "tone": [81, 150, 184]}
        return rows, extra

    def test_the_seats_monthly_allowance_is_its_one_bar(self):
        o = self._drive()
        b = o["tip"]["blocks"][0]
        self.assertEqual(b["head"], NAME2, "headed by the account's kind")
        self.assertEqual((b["windows"], b["used"]), (["Monthly spend limit"], ["23%"]), "the account's spend limit, one bar")
        # the reading carries no reset date: the browser dates it as Claude Code's /usage screen does, the first of
        # the next calendar month in the viewer's zone ("resets Nov 1"), only for a plan with a limit to reset
        self.assertEqual(len(b["resets"]), 1)
        self.assertRegex(b["resets"][0], r"^resets [A-Z][a-z]{2} 1$")
        self.assertEqual(b["nowin"], "", "the bar takes the one-line note's place")
        self.assertEqual(b["age"], "", "no age line")
        self.assertFalse(o["tip"]["email"])
        self.assertEqual((o["rail"]["windows"], o["rail"]["pcts"]), ([], []), "the hover's bar, not the rail's")

if __name__ == "__main__":
    unittest.main()
