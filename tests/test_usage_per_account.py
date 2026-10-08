"""Fleet usage: one PAYLOAD ROW PER HOST, one AGGREGATED rendering (the user 2026-08-08).

The 5h / 7d windows are account-wide allowances, so the collapsed rail draws ONE set of bars — per
window, the worst known reading across every account (a shared login reports the same number from every
host and the max collapses it for free) — plus one API cell for everything key-billed. The per-host
story lives in the hover, so /usage/fleet now emits every reporting host, INCLUDING a key-only host with
no account digest at all (the old per-account dedup dropped those entirely, and a mixed host's key spend
never rendered anywhere).

The account is read from Claude Code's own ~/.claude.json (`oauthAccount.accountUuid`) — the identity the
CLI itself uses, with no API that reports it. It travels as an opaque digest, never the email: the only
question is "same login or not" (the client's aggregation and a reader of the hover both lean on it),
and an email is a personal identifier that would otherwise ride to every federated host.

Synthetic accounts and hosts only; no real uuid, email or hostname appears here.
"""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_acctusage", os.path.join(BIN, "romp-kernel"))

ACCT_A = "aaaaaaaaaaaa"
ACCT_B = "bbbbbbbbbbbb"


def _usage(acct):
    return {"fiveHour": {"pct": 12, "resetsAt": None, "color": [1, 2, 3]},
            "sevenDay": None, "fable": None, "t": 1000, "acct": acct, "limited": None}


class AccountIdentity(unittest.TestCase):
    def test_it_is_a_digest_and_never_the_raw_identifier(self):
        # the read mechanics live in _acct_read now: ONE cached parse feeds the digest AND the
        # display label (the user 2026-08-09 added the label; the digest's own rules stand)
        src = inspect.getsource(km._acct_read)
        self.assertIn("accountUuid", src, "the account identity the CLI itself uses")
        self.assertIn("sha256", src)
        # the digest is short and opaque: enough to answer "same or not", carrying nothing back —
        # and it hashes the uuid ALONE, never the email, so cross-host equality carries no identity
        self.assertIn("hexdigest()[:12]", src)
        self.assertIn('hashlib.sha256(str(uuid).encode("utf-8"))', src)
        # the label is the deliberate, narrower exception: display-only (picker Billing row, tab
        # hover), read through _claude_account_label — the digest path itself never returns it
        self.assertIn('return _ACCT_CACHE["val"]', inspect.getsource(km._claude_account))
        self.assertNotIn('_ACCT_CACHE["label"]', inspect.getsource(km._claude_account))

    def test_a_missing_or_unreadable_file_is_no_account_not_a_crash(self):
        old = os.environ.get("HOME")
        try:
            os.environ["HOME"] = tempfile.mkdtemp()      # a home with no ~/.claude.json at all
            km._ACCT_CACHE["mtime"] = -1.0
            self.assertEqual(km._claude_account(), "")
        finally:
            if old is not None:
                os.environ["HOME"] = old
            km._ACCT_CACHE["mtime"] = -1.0

    def test_the_reading_is_cached_on_the_file_and_not_reparsed_per_poll(self):
        self.assertIn('_ACCT_CACHE["mtime"] == m', inspect.getsource(km._acct_read))

    def test_the_usage_payload_carries_it(self):
        self.assertIn('"acct": _claude_account(),', inspect.getsource(km._usage))
        # …and, since 2026-08-09, the login's NAME beside it — display for the hover only (the
        # rail's cross-host dedup stays on the digest, which carries nothing). Named by its kind since
        # 2026-10-01, the spend detail's words: the email no longer rides the usage payload at all.
        self.assertIn('"acctName": _acct_name(),', inspect.getsource(km._usage))
        self.assertNotIn("_claude_account_label", inspect.getsource(km._usage))


class FleetRollup(unittest.TestCase):
    def setUp(self):
        self._usage_real = km._usage
        with km._remotes_lock:
            km._remotes.clear()

    def tearDown(self):
        km._usage = self._usage_real
        with km._remotes_lock:
            km._remotes.clear()

    def _remote(self, host, acct, status="up"):
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "status": status, "usage": _usage(acct) if acct else None}

    def test_every_reporting_host_rides_along_shared_login_or_not(self):
        # the rail aggregates and the hover breaks down per host, so a shared login is NOT deduped
        # server-side any more — each host's row carries the same acct and the client collapses it
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_A)
        self._remote("gpu", ACCT_A)
        rows, missing = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], ["", "api", "gpu"])
        self.assertEqual(missing, [], "every machine current: nothing left out")
        self.assertEqual({r["acct"] for r in rows}, {ACCT_A}, "the digest is what lets the client collapse them")

    def test_a_second_account_gets_its_own_row(self):
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_B)
        rows, _missing = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], ["", "api"])
        self.assertEqual(rows[1]["acct"], ACCT_B)

    def test_a_disconnected_host_contributes_nothing_and_is_named_as_missing(self):
        # 2026-10-08 review: a machine down or restarting dropped out of the rail's figures with nothing said; it is now
        # named, with when its usage was last read, so the cell can say its figures are incomplete
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_B, status="down")
        with km._remotes_lock:
            km._remotes["api"]["_usage_at"] = 1_790_000_000
        rows, missing = km._fleet_usage()
        self.assertEqual(len(rows), 1, "its last reading is never summed as if current")
        self.assertEqual(missing, [{"host": "api", "why": "not reachable", "at": 1_790_000_000}])

    def test_a_machine_whose_usage_read_failed_or_never_answered_is_named_too(self):
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", ACCT_B)
        self._remote("gpu", "")
        with km._remotes_lock:
            km._remotes["api"]["_usage_err"] = "could not reach its kernel: timed out"
            km._remotes["api"]["_usage_at"] = 1_790_000_000
        rows, missing = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], [""], "a failed read is not current, and a machine with no reading has none")
        self.assertEqual(missing, [{"host": "api", "why": "could not reach its kernel: timed out", "at": 1_790_000_000},
                                   {"host": "gpu", "why": "no usage reading yet", "at": None}])
        # the poll records the failure as an event and clears it on the next good read
        src = inspect.getsource(km._poll_remote_usage)
        self.assertIn('r["_usage_err"] = "could not reach its kernel: %s" % e', src)
        self.assertIn('r.pop("_usage_err", None)', src)

    def test_a_key_only_host_is_included_even_with_no_account_to_report(self):
        # the old per-account dedup dropped an acct-less row entirely, so a remote key-only host's
        # spend could never reach the rail (found 2026-08-08 while adding per-session auth): the host
        # itself is the identity for the spend half of a payload
        km._usage = lambda: _usage(ACCT_A)
        self._remote("api", "")
        with km._remotes_lock:
            km._remotes["api"]["usage"] = {"apiKey": True, "spend": {"fiveHour": {"usd": 1.0}}, "t": 1, "acct": ""}
        rows, missing = km._fleet_usage()
        self.assertEqual([r["host"] for r in rows], ["", "api"])
        self.assertEqual(rows[1]["acct"], "")
        self.assertEqual(missing, [{"host": "api", "why": "older build: it sends no hourly usage", "at": None}],
                         "its key windows still feed the bars, but its spend is not in the figures, and it is named")

    def test_the_local_row_is_always_first_so_the_notices_read_off_this_machine(self):
        km._usage = lambda: _usage(ACCT_A)
        self._remote("aaa-sorts-first", ACCT_B)
        self.assertEqual(km._fleet_usage()[0][0]["host"], "")


class Polling(unittest.TestCase):
    def test_a_remote_is_polled_at_most_once_a_minute(self):
        src = inspect.getsource(km._poll_remote_usage)
        self.assertIn("REMOTE_USAGE_EVERY", src)
        self.assertGreaterEqual(km.REMOTE_USAGE_EVERY, 60)
        self.assertIn('r.get("_usage_at")', src)

    def test_a_blip_keeps_the_last_good_reading_rather_than_blanking_the_bars(self):
        src = inspect.getsource(km._poll_remote_usage)
        self.assertEqual(src.count('return r.get("usage")'), 3)

    def test_the_supervisor_polls_it_beside_the_version(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("ruse = _poll_remote_usage(r) if up else None", src)
        self.assertIn('r["usage"] = ruse', src)

    def test_the_snapshot_never_reaches_the_credential_file(self):
        # remotes.json is 0600 because every row holds a serve token; rewriting it every minute to store
        # a usage snapshot would be pure churn on a file that exists to survive a restart
        self.assertIn("usage", km._NOT_SAVED)
        self.assertIn("_usage_at", km._NOT_SAVED)
        self.assertIn("k not in _NOT_SAVED", inspect.getsource(km._remotes_rows_for_save))


class RailRendering(unittest.TestCase):
    js = km._LANDING_USAGE_JS

    def test_the_rail_reads_the_fleet_view(self):
        self.assertIn("fetch('/usage/fleet'", self.js)
        self.assertIn("function renderRows(rows,selfHost)", self.js)

    def test_the_rail_aggregates_and_never_repeats_the_windows_per_host(self):
        # collapsed = one set of bars (worst window across hosts) + one usage cell; the per-host
        # .ru-set/.ru-host rail markup is gone (the user 2026-08-08) — hosts appear only in the hover
        self.assertIn("function aggBarsHTML(live)", self.js)
        self.assertIn("function costCellHTML(live)", self.js)
        self.assertIn("aggBarsHTML(LAST)+costCellHTML(LAST)", self.js)
        self.assertNotIn("class=ru-set", self.js)
        self.assertNotIn("class=ru-host>", self.js)
        self.assertIn("if(!best||d.pct>best.pct)best=d;", self.js, "worst known reading wins the bar")

    def test_the_usage_cell_is_figures_under_window_names_with_no_label_and_no_bars(self):
        # 2026-10-08: the constant 'API' label claimed a billing the figures mostly were not (an enterprise seat bills
        # them): each window wears its ONE display name LEFT of its figure (the user 2026-08-09), the figure every turn
        # at API prices, approximate where a seat billed any of it; no tokens on the bar; still no key fragment and no
        # spend bars anywhere (2026-08-08)
        self.assertNotIn("<div class=ru-name>API</div>", self.js)
        self.assertNotIn("_tail", self.js)
        self.assertIn("var COST_WINS=[['today','today'],['monthToDate','this month']];", self.js)
        self.assertIn("return '<div class=ru-name>'+w[1]+'</div><div class=ru-pct data-win='+w[0]+'>'+costUsd(s[w[0]])+'</div>';", self.js)
        self.assertNotIn("spendColor", self.js)
        self.assertNotIn("spendWinsHTML", self.js)

    def test_the_hover_is_one_block_per_account_with_the_machines_named_in_the_strips_host_dress(self):
        # the user 2026-09-19: three machines on one login drew three identical columns; the block is per account, the
        # machines one line beneath it in the tab strip's quiet italic host dress (the per-host .ru-tip-host heading is gone)
        css = km._landing()
        self.assertNotIn(".ru-tip-host{", css)
        self.assertIn(".ru-tip-machines .host-prefix{color:var(--dim,#9aa0a6);font-weight:400;font-style:italic;font-size:0.86em}", css)
        self.assertIn("function acctBlocksHTML(sets,manyMachines)", self.js)
        # a host section can carry BOTH its login's windows and its key's spend (per-session auth),
        # and the spend rows are numbers only — no track span
        self.assertIn("function winDet(u,det)", self.js)
        self.assertIn("winDet(r.usage,det);costDet(r.usage,det);", self.js)
        # the usage is ONE section for every machine at once (the user 2026-08-13: never a per-host repeat), split by
        # account (2026-10-08), every machine named on one line when several
        self.assertIn("function costTipHTML(sets){", self.js)
        self.assertIn("<span>Usage at API prices</span>", self.js)
        self.assertIn("by machine this month: ", self.js)

    def test_the_account_wide_notices_read_off_THIS_machine(self):
        # a limit pauses THIS kernel's retries and judges; a remote account's limit does not
        self.assertIn("function notices(u)", self.js)
        self.assertIn("var local=rows.length?rows[0].usage:null;\nnotices(local);", self.js)

    def test_the_tooltip_names_the_machines_only_when_there_is_more_than_one(self):
        self.assertIn("var manyMachines=(sets.length+NOREPORT.length)>1;", self.js)
        self.assertIn("if(manyMachines)h+='<div class=ru-tip-machines>signed in on '", self.js)   # what the names are (2026-10-08 review)
        self.assertIn("if(d._acct)h+='<div class=ru-tip-acct>'+esc(d._acct)+'</div>';", self.js, "the account line heads its block whenever the login is known")
        self.assertIn('"noReport": _usage_no_report()', open(os.path.join(BIN, "romp-kernel")).read(), "the route names the machines up with no report")
        with km._remotes_lock:
            saved = dict(km._remotes); km._remotes.clear()
            km._remotes.update({"TESTHOSTB": {"host": "TESTHOSTB", "status": "up", "usage": {"t": 1}},
                                "TESTHOSTC": {"host": "TESTHOSTC", "status": "up"}, "TESTHOSTD": {"host": "TESTHOSTD", "status": "down"}})
        try:
            self.assertEqual(km._usage_no_report(), ["TESTHOSTC"], "up with no report: named; a reporting or a down machine is not")
        finally:
            with km._remotes_lock:
                km._remotes.clear(); km._remotes.update(saved)

    def test_the_route_reads_the_cached_poll_rather_than_dialling_per_request(self):
        # a dashboard refresh must not cost an ssh round-trip per attached host: _fleet_usage reads the
        # rows the tunnel supervisor already filled, and does no network of its own
        src = inspect.getsource(km._fleet_usage)
        self.assertIn("_remotes.values()", src)
        for net in ("HTTPConnection", "_poll_remote_usage", "subprocess"):
            self.assertNotIn(net, src, "the route must answer from the cache")



class AccountWithoutWindows(unittest.TestCase):
    """An account that ANSWERED it has no usage windows (the snapshot writer's noWindows, stamped with the login that
    answered: an enterprise seat meters a monthly spend limit instead) is said, named by its kind, with the key's spend
    beside it (the user 2026-10-01, whose hover showed a 26-day-old reading as that account's allowance, its email as
    the head). Before, a signed-in machine with no windows on file answered None, dropping its key spend with it."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.saved = (km.jd.STATE, km._claude_account, km._kind_read, km._auth_key_present)
        km.jd.STATE = __import__("pathlib").Path(self.d)
        (km.jd.STATE / "session-hosts").write_text("off")   # its own state root: no per-session host (T348)
        km._claude_account = lambda: ACCT_A
        km._kind_read = lambda: "enterprise"
        km._auth_key_present = lambda: True
        day, hour = km.time.strftime("%Y-%m-%d"), km.time.strftime("%Y-%m-%dT%H")
        b = {"usd": 9.0, "turns": 3, "tokIn": 90, "tokOut": 0, "tokCacheR": 0, "tokCacheW": 0,
             "key": {"usd": 4.0, "turns": 1, "tok": 40, "tokIn": 40, "tokOut": 0, "tokCacheR": 0, "tokCacheW": 0},
             "bySid": {"11111111-2222-3333-4444-555555555555": {"usd": 9.0, "turns": 3, "tok": 90}}}
        (km.jd.STATE / "spend.json").write_text(json.dumps({"days": {day: b}, "hours": {hour: b}}))

    def tearDown(self):
        km.jd.STATE, km._claude_account, km._kind_read, km._auth_key_present = self.saved

    def _file(self, acct):
        (km.jd.STATE / "usage.json").write_text(json.dumps(
            {"t": 1000, "acct": acct, "five_hour": None, "seven_day": None, "fable": None, "noWindows": True}))

    def test_it_is_said_named_by_kind_and_the_key_spend_rides_beside_it(self):
        self._file(ACCT_A)
        u = km._usage()
        self.assertIsNotNone(u, "a known answer, never dropped as nothing known")
        self.assertTrue(u["noWindows"])
        self.assertEqual(u["acctName"], "Enterprise seat", "the spend detail's name for the account")
        self.assertEqual(u["spend"]["day"]["usd"], 4.0, "the key's dollars, as on the windows arm")
        self.assertNotIn("fiveHour", u)
        self.assertNotIn("acctLabel", u, "no email on the rail")

    def test_an_answer_from_another_login_is_not_this_ones(self):
        self._file(ACCT_B)
        self.assertIsNone(km._usage())

    def test_the_windows_arm_names_the_account_by_kind_too(self):
        (km.jd.STATE / "usage.json").write_text(json.dumps(
            {"t": 1000, "acct": ACCT_A, "five_hour": {"pct": 30, "resets_at": None}, "seven_day": None, "fable": None}))
        km._kind_read = lambda: "personal"
        self.assertEqual(km._usage()["acctName"], "Personal plan")
        km._kind_read = lambda: ""
        self.assertEqual(km._usage()["acctName"], "Claude account", "a kind that cannot be read is still no email")


class SeatMonthlyAllowance(AccountWithoutWindows):
    """The account's monthly spend limit (2026-10-01; reshaped 2026-10-08): an account with no windows whose answer carried
    its extra-usage figures (the snapshot writer's `monthly`, as answered: minor units of the currency) is served with what
    it has spent of its limit this month in dollars, the limit and the percent; a reading from an earlier calendar month is
    served stale, with no figure; a plan with no limit serves its spend alone. Claude Code's schema calls the figure the
    plan's extra-usage spend for the billing period, and whose spend it counts (this member's or the organisation's) the
    answer does not say, so the shell shows it in Claude Code's words; the answer carries no reset date, so none is served.
    Synthetic figures only."""
    test_it_is_said_named_by_kind_and_the_key_spend_rides_beside_it = None
    test_an_answer_from_another_login_is_not_this_ones = None
    test_the_windows_arm_names_the_account_by_kind_too = None

    EU = {"is_enabled": True, "monthly_limit": 5000000, "used_credits": 1150000, "utilization": 23.0, "currency": "USD"}

    def _monthly_file(self, t, **eu):
        (km.jd.STATE / "usage.json").write_text(json.dumps(
            {"t": t, "acct": ACCT_A, "five_hour": None, "seven_day": None, "fable": None, "noWindows": True,
             "monthly": dict(self.EU, **eu)}))

    @staticmethod
    def _at(y, mo, d, h=12):
        return km.time.mktime((y, mo, d, h, 0, 0, 0, 0, -1))   # local time, as the reading's month is read

    def test_it_is_served_in_dollars_with_its_limit_and_percent_and_no_reset_date(self):
        now = km.time.time()
        self._monthly_file(int(now))
        u = km._usage()
        mo = u["monthly"]
        self.assertEqual((mo["used"], mo["limit"], mo["pct"], mo["currency"]), (11500.0, 50000.0, 23, "USD"),
                         "minor units (cents) to dollars, as Claude Code's /usage screen shows them")
        self.assertEqual(mo["month"], km.time.strftime("%Y-%m", km.time.localtime(now)))
        self.assertNotIn("resetsOn", mo, "the answer carries no reset date: none is invented")
        self.assertEqual(len(mo["color"]), 3, "the windows' fill colours, for the same bar")
        self.assertNotIn("stale", mo)
        self.assertTrue(u["noWindows"])
        self.assertEqual(u["acctName"], "Enterprise seat")

    def test_a_reading_from_an_earlier_month_is_stale_and_the_percent_falls_back_to_used_over_limit(self):
        o = {"t": self._at(2026, 7, 15), "monthly": dict(self.EU)}
        self.assertEqual(km._monthly_allowance(o, now=self._at(2026, 7, 31, 23))["pct"], 23)
        self.assertEqual(km._monthly_allowance(o, now=self._at(2026, 8, 1, 0)), {"stale": True, "month": "2026-07"})
        no_util = {"t": o["t"], "monthly": dict(self.EU, utilization=None)}
        self.assertEqual(km._monthly_allowance(no_util, now=o["t"])["pct"], 23, "used over limit where no utilization")

    def test_a_reading_from_before_the_month_is_served_with_no_figure(self):
        self._monthly_file(int(km.time.time()) - 40 * 86400)
        mo = km._usage()["monthly"]
        self.assertTrue(mo["stale"])
        self.assertNotIn("pct", mo)
        self.assertNotIn("color", mo)

    def test_a_plan_with_no_limit_serves_its_spend_and_no_figures_serves_nothing(self):
        self._file(ACCT_A)
        self.assertNotIn("monthly", km._usage(), "no extra_usage in the answer")
        for eu in ({"monthly_limit": None}, {"monthly_limit": 0}):
            self._monthly_file(int(km.time.time()), **eu)
            mo = km._usage()["monthly"]
            self.assertEqual((mo["used"], mo["limit"], mo["pct"]), (11500.0, None, None), eu)
            self.assertNotIn("color", mo, "no limit, no bar")
        self._monthly_file(int(km.time.time()), used_credits=None, utilization=None)
        u = km._usage()
        self.assertTrue(u["noWindows"])
        self.assertNotIn("monthly", u, "no figures at all: nothing to show")

    def test_another_logins_allowance_is_not_this_ones(self):
        self._monthly_file(int(km.time.time()))
        km._claude_account = lambda: ACCT_B
        self.assertIsNone(km._usage())


class RailByAccount(unittest.TestCase):
    """The usage hover's accounts (the user 2026-10-01, who read seat dollars summed unmarked into the hover's figures):
    each host's usage carries its two windows by billing route with the routes' identities, no email (spendWin), and
    /usage/fleet folds every host's by the spend detail's own rule (_rail_accounts), so one seat reached by one machine's
    sign-in and another's stored token is one entry."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.saved = (km.jd.STATE, km._self_host, km._machine_identity, km._usage)
        km.jd.STATE = __import__("pathlib").Path(self.d)
        km._self_host = lambda: "TESTHOSTA"

    def tearDown(self):
        km.jd.STATE, km._self_host, km._machine_identity, km._usage = self.saved

    def test_the_payload_carries_the_windows_by_route_on_every_branch_and_no_email(self):
        km._machine_identity = lambda: {"state": "ok", "acct": ACCT_A, "org": "Example Org", "orgDigest": "o1",
                                        "kind": "enterprise", "email": "user@example.test"}
        hour = km.time.strftime("%Y-%m-%dT%H")
        b = {"usd": 10.0, "turns": 4, "key": {"usd": 3.0, "turns": 1, "tok": 1},
             "byLogin": {"aaaa1111": {"usd": 5.0, "turns": 2, "tok": 2}}, "bySid": {}}
        (km.jd.STATE / "spend.json").write_text(json.dumps({"days": {km.time.strftime("%Y-%m-%d"): b}, "hours": {hour: b}}))
        # 2026-10-08 review: the figures rode only the branches that return a reading; with none, they still ride
        km._usage = lambda: None
        u = km._usage_payload()
        sw = u["spendWin"]
        self.assertEqual(sw["today"]["routes"], {"key": 3.0, "login:aaaa1111": 5.0, "machine": 2.0},
                         "the hour's turns, split as the spend detail splits them")
        machine = [m for m in sw["billing"] if m["route"] == "machine"][0]
        self.assertEqual(machine["acct"], ACCT_A)
        self.assertNotIn("user@example.test", json.dumps(u), "the email never rides the rail")
        self.assertNotIn("spendHours", u, "the raw hours ride only a peer's ask")
        self.assertIn("spendHours", km._usage_payload(with_hours=True))

    def test_one_seat_two_routes_is_one_entry_and_an_older_build_is_named(self):
        seat = {"acct": ACCT_A, "kind": "enterprise", "org": "Example Org"}
        win = lambda routes: {"routes": routes, "tok": {"tok": 0}}   # noqa: E731
        rows = [{"host": "", "usage": {"spendWin": {"today": win({"machine": 3.0}), "monthToDate": win({"key": 10.0, "machine": 3.0}),
                                                   "billing": [{"route": "key"}, dict(seat, route="machine", state="ok")]}}},
                {"host": "TESTHOSTB", "usage": {"spendWin": {"today": win({}), "monthToDate": win({"key": 5.0, "login:aaaa1111": 7.0}),
                                                            "billing": [{"route": "key"}, dict(seat, route="login:aaaa1111", label="work")]}}},
                {"host": "TESTHOSTC", "usage": {"spend": {"week": {"usd": 1.0}}}}]
        accts, missing = km._rail_accounts(rows)
        self.assertEqual([(a["name"], a["kind"]) for a in accts], [("Enterprise seat", "seat"), ("API key", "key")])
        self.assertEqual(accts[0]["routes"], [{"host": "TESTHOSTA", "route": "machine", "est": True},
                                              {"host": "TESTHOSTB", "route": "login:aaaa1111", "est": True}])
        self.assertEqual(missing, ["TESTHOSTC"], "spend from an older build that the fold cannot read is counted out by name")

    def test_the_route_ships_the_fold_and_the_missing_machines(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('accts, accts_missing = _rail_accounts(rows)', src)
        self.assertIn('"missing": missing}', src)


if __name__ == "__main__":
    unittest.main()
