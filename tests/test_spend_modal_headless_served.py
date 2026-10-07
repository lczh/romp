#!/usr/bin/env python3
"""T247, the served-page pins: the landing page is served from a hermetic kernel module (its own
_landing() HTML, /spend/detail and /usage/fleet answered from a synthetic ledger — placeholder uuids,
TESTHOST, the notes-api demo sessions), and a real browser clicks the usage readout. Pins the modal's
open/close contract (click opens over the 0.55 backdrop; Escape and a backdrop tap close), the table
and the stacked histogram with its unattributed stack, the toggles, and the segment tooltip. Skips
LOUDLY without a playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules holding it (CI does
not; this is the maintainer's screenshot harness too — pass ROMP_SHOTS=<path-prefix> for PNGs)."""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_spendmodal", os.path.join(BIN, "romp-kernel"))

import sys
sys.path.insert(0, HERE)
import test_spend_detail as _sd   # noqa: E402  the same synthetic world (the module, not its classes:
#                                   an imported TestCase would be collected here a second time)
write_ledger, WEB, API, TESTS = _sd.write_ledger, _sd.WEB, _sd.API, _sd.TESTS
ACCT = "a1a1a1a1a1a1"            # an invented account digest
PLID = "%012x" % 0x0A0B0C0D0E0F  # an invented stored-login record id on the peer


def _node_path():
    for cand in (os.environ.get("ROMP_PLAYWRIGHT_NODE_PATH", ""), os.path.join(ROOT, "vscode-extension", "node_modules")):
        if cand and os.path.isdir(os.path.join(cand, "playwright")):
            return cand
    return ""


class SpendModalServed(unittest.TestCase):
    def setUp(self):
        np = _node_path()
        if not np:
            self.skipTest("no playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules that holds it")
        self.node_path = np
        self.td = tempfile.TemporaryDirectory()
        state = Path(self.td.name)
        self._saved = (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host,
                       km._claude_account, km._auth_key_present, km.time)
        # The kernel reads the fixture's clock, not the wall's. /spend/detail was already served at NOW, but /usage,
        # which fills the rail and the modal's Totals, read the wall clock. Its "this month" row is calendar
        # month-to-date, so it held the fixture's September spend, on two lines, only while the real month was
        # September 2026. On 2026-10-01 it read $0 on one line, the chart rose 14 px, and the short-window tooltip found
        # room below the pointer. Every other attribute stays the real module's.
        km.time = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")})
        km.time.time = lambda: _sd.NOW
        km.jd.STATE = state
        km.NAMES = state / "names"
        km.NAMES.mkdir()
        (km.NAMES / WEB).write_text("web\t/tmp/notes-api\t#1EA1EB\t#ffffff\n")
        (km.NAMES / API).write_text("api\t/tmp/notes-api\t#54B204\t#ffffff\n")
        (km.NAMES / TESTS).write_text("tests\t/tmp/notes-api\t#4EA8A9\t#ffffff\n")
        km._live_names = lambda tm: {"web": WEB, "api": API}
        km._live_map = lambda: []
        km._self_host = lambda: "TESTHOST"
        km._claude_account = lambda: ""
        km._auth_key_present = lambda: True
        (state / "usage.json").write_text(json.dumps({"apiKey": True}))
        write_ledger(state, extra_sids=20)     # enough sessions to scroll the pane (T247e)
        # The By account summary (2026-10-01): this machine's own sign-in is an enterprise seat (a synthetic identity), api
        # bills the API key, and the peer below reaches the SAME seat through a stored login, so the summary reads one
        # "Enterprise seat" row across both hosts, the key's dollars plain and the seat's marked as estimates
        self._saved_ident = km._machine_identity
        km._machine_identity = lambda: {"state": "ok", "acct": ACCT, "org": "Example Org", "orgDigest": "c3c3c3c3c3c3",
                                        "kind": "enterprise", "email": "dev@example.com"}
        led = json.loads((state / "spend.json").read_text())
        for b in list(led["hours"].values()) + list(led["days"].values()):
            a = (b.get("bySid") or {}).get(API)
            if a:
                a["key"] = {"usd": a["usd"], "turns": a["turns"], "tok": a["tok"]}
                b["key"] = dict(a["key"])
        (state / "spend.json").write_text(json.dumps(led))
        (state / "session-order.json").write_text(json.dumps([API, WEB]))   # the tab strip's order: api before web (T247f)
        # T247g: two tags — "team" (web + api) and "ops" (api + the peer's worker): api is under both
        (state / "timeline-views.json").write_text(json.dumps({"active": "all", "tags": [
            {"id": "g1", "name": "team", "color": "#C2410C", "members": [WEB, API]},
            {"id": "g2", "name": "ops", "color": "#0F766E", "members": [API, "PEERHOST:22222222-3333-4444-5555-000000000001"]}]}))
        # T247c: a two-host world — a peer kernel whose sessions merge in, and an older peer reported by name
        self._saved_remotes = dict(km._remotes)
        km._remotes.clear()
        sd = _sd.SpendDetail("test_the_route_and_the_shell_are_wired")
        off = int((time.localtime(_sd.NOW).tm_gmtoff or 0) // 60) + 180
        pp = sd._peer_payload.__func__(self, off)
        # the peer is a current build: its worker's turns and key dollars per bucket, its billing routes, and the worker
        # billed to a stored login whose record carries the seat's account digest
        for rng, per in (("hours", 1), ("days", 30)):
            w = pp[rng]["stacks"][0]
            w["turns"], w["keyUsd"] = [per if v else 0 for v in w["usd"]], [0.0] * len(w["usd"])
            pp[rng]["routes"] = [{"host": "PEERHOST", "route": "login:" + PLID, "usd": w["usd"], "turns": w["turns"]}]
        un = pp["hours"]["stacks"][1]
        pp["hours"]["routes"].append({"host": "PEERHOST", "route": "untracked", "usd": un["usd"], "turns": [1 if v else 0 for v in un["usd"]]})
        pp["billing"] = [{"route": "login:" + PLID, "label": "enterprise", "display": "enterprise", "acct": ACCT}, {"route": "untracked"}]
        pp["sessions"][0].update({"login": "enterprise", "loginId": PLID, "loginLabel": "enterprise"})
        peer_port = sd._peer_server.__func__(self, pp)
        km._remotes["PEERHOST"] = {"host": "PEERHOST", "status": "up", "local_port": peer_port, "token": "peer-tok",
                                   "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}
        km._remotes["OLDHOST"] = {"host": "OLDHOST", "status": "up", "local_port": sd._peer_server.__func__(self, status=404), "token": "peer-tok",
                                  "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}
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
                if p == "/spend/detail":
                    # the fixture's OWN clock (review find: served with the wall clock, the 192-hour window
                    # slid off the anchored ledger within days and the hourly pins became a time bomb)
                    return self._out(200, json.dumps(km._spend_detail(now=_sd.NOW)).encode(), "application/json")
                if p == "/usage/fleet":
                    return self._out(200, json.dumps({"rows": [{"host": "", "acct": "", "usage": km._usage()}],
                                                      "host": "TESTHOST"}).encode(), "application/json")
                if p == "/usage":
                    return self._out(200, json.dumps(km._usage() or {}).encode(), "application/json")
                if p.startswith("/media/") or p.startswith("/dist/"):
                    f = os.path.join(ROOT, p.lstrip("/"))
                    if p == "/dist/styles.css" and not os.path.isfile(f):
                        f = os.path.join(ROOT, "ui", "webview", "styles.css")   # the source, when no build ran here
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

    def tearDown(self):
        if hasattr(self, "srv"):
            self.srv.shutdown()
            self.srv.server_close()
        if hasattr(self, "_saved"):
            (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host,
             km._claude_account, km._auth_key_present, km.time) = self._saved
            km._remotes.clear()
            km._remotes.update(getattr(self, "_saved_remotes", {}))
            if hasattr(self, "_saved_ident"):
                km._machine_identity = self._saved_ident
            self.td.cleanup()

    def test_click_opens_the_modal_with_table_and_stacked_histogram_and_escape_closes_it(self):
        shots = os.environ.get("ROMP_SHOTS", "")
        env = dict(os.environ, NODE_PATH=self.node_path)
        r = subprocess.run(["node", os.path.join(HERE, "spend_modal_headless.js"), "http://127.0.0.1:%d/" % self.port, shots],
                           capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(r.returncode, 0, "the driver failed:\n" + r.stderr[-3000:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(o["hiddenBefore"], "closed until the click")
        # T247d: the desktop hover ends in the affordance line, in the footnote style; the phone panel does not carry it
        self.assertEqual(o["hoverHint"]["text"], "Click for the full breakdown by session.", o["hoverHint"])
        self.assertEqual((o["hoverHint"]["cls"], o["hoverHint"]["font"], o["hoverHint"]["opacity"]), ("ru-tip-hint", "10px", "0.55"), o["hoverHint"])
        self.assertIs(o["mobile"]["panelHint"], False, "the phone panel keeps its button and no click hint")
        self.assertTrue(o["loaderSeen"], "the loader (or the content) is up the instant the modal opens")
        self.assertEqual(o["out"]["backdrop"], "rgba(0, 0, 0, 0.55)", "the panel rule's backdrop")
        self.assertEqual(o["out"]["head"], "API spend · 2 machines", "the header names the machines when several contribute (review find)")
        rows = o["out"]["rows"]
        # T247c: two hosts contribute, so every row names its host in the tab strip's host-prefix voice. T353: the
        # list follows the chart's range, seven days by hour at open, and sorts by the spend IN that range: web
        # (an hourly dollar over the ledger's 60 hours), then api, then the peer's worker
        self.assertTrue(rows[0].startswith("TESTHOST:web"), rows[0])
        self.assertTrue(rows[1].startswith("TESTHOST:api"), rows[1])
        self.assertTrue(rows[2].startswith("PEERHOST:worker"), rows[2])
        self.assertTrue(any("OLDHOST" in n and "older build" in n for n in o["out"]["notes"]), o["out"]["notes"])
        self.assertFalse(any("this machine only" in n for n in o["out"]["notes"]))
        self.assertTrue(any("aligned by clock time" in n for n in o["out"]["notes"]), "hosts in different zones: the timezone rule is stated")
        # T247e: the chart comes first, has no legend; the list is a scroll pane of EVERY session under a
        # sticky header, each row its tab title in its identity color, no swatch, no fold
        self.assertTrue(o["out"]["chartFirst"], "the chart section precedes the session list")
        self.assertEqual(o["out"]["legendNodes"], 0, "no legend")
        self.assertEqual(o["out"]["swatches"], 0, "no swatch in a session row")
        # T353: the seven days shown carry the three hourly-ledger sessions, the peer's worker and the unattributed
        # hour; the twenty filler sessions spend by the day only, so they appear once the 90-day range is shown
        self.assertEqual(len(rows), 5, "the sessions with spend in the range shown: " + " | ".join(rows))
        self.assertEqual(len(o["days"]["rows"]), 3 + 20 + 1 + 1, "the 90-day range lists every session across both hosts, plus the unattributed row")
        self.assertEqual(o["out"]["title"]["color"], "rgb(30, 161, 235)", "web's title wears its identity color")
        self.assertEqual(o["out"]["title"]["weight"], "600")
        self.assertEqual(o["out"]["title"]["prefix"], "TESTHOST:")
        self.assertTrue(o["days"]["pane"]["scrolls"], "the full list scrolls in its pane (T247e): " + str(o["days"]["pane"]))
        self.assertEqual(o["out"]["pane"]["sticky"], "sticky")
        self.assertEqual(o["out"]["pane"]["thOpacity"], "1", "the sticky header occludes: muted by color, not opacity (review find)")
        self.assertFalse(o["out"]["pane"]["panelScrolls"], "the card itself does not scroll at 820px: the pane takes the room under the chart (review find)")
        self.assertFalse(o["days"]["pane"]["panelScrolls"], "nor with the full list: the pane scrolls, not the card")
        # T247f: "your order" — the strip's order (api before web from session-order.json, then the peer's own
        # order), unknown sessions trailing by spend; the chart's bottom stack follows; a viewer arrangement
        # (the strip's localStorage key) reorders both; the choice persists with the other toggles
        yo = o["yourOrder"]
        self.assertTrue(yo["rows"][0].startswith("TESTHOST:api") and yo["rows"][1].startswith("TESTHOST:web"), yo["rows"][:3])
        self.assertTrue(yo["rows"][2].startswith("PEERHOST:worker"), yo["rows"][:3])
        self.assertTrue(yo["rows"][3].startswith("TESTHOST:tests"), "sessions the order does not know trail, by spend: " + yo["rows"][3])
        self.assertEqual(yo["bottomFill"], "#54B204", "the chart's bottom stack is the first row (api)")
        self.assertTrue(yo["viewRows"][0].startswith("TESTHOST:web"), "the viewer's own arrangement wins over the seed: " + yo["viewRows"][0])
        self.assertEqual(yo["prefs"].get("order"), "yours")
        self.assertTrue(yo["pressed"])
        self.assertTrue(o["mobile"]["persisted"]["pressed"], "the persisted choice is read back on a reload (review find: only the write was pinned)")
        # T247g: merge by tag — one row per tag (summed, tag color), untagged sessions as themselves, a session
        # under two tags counted in each with the footer saying so; the stacks follow; "1 day" is 24 hourly buckets
        mg = o["merge"]
        self.assertTrue(mg["rows"][0].startswith("team · 2 sessions"), mg["rows"][:3])
        self.assertTrue(mg["rows"][1].startswith("ops · 2 sessions"), mg["rows"][:3])
        self.assertFalse(any(r.startswith("TESTHOST:web") or r.startswith("TESTHOST:api") or r.startswith("PEERHOST:worker") for r in mg["rows"]), "tagged sessions merge away")
        # the merged rows are read in the 90-day range (the driver left it there), where the fixture's whole ledger
        # lies: the sums over that range are the ledger's totals (T353: the list follows the chart's range)
        self.assertIn("$1200", mg["rows"][0].replace(",", ""), "team = web 960 + api 240: " + mg["rows"][0])
        self.assertIn("$540", mg["rows"][1].replace(",", ""), "ops = api 240 + worker 300: " + mg["rows"][1])
        self.assertEqual(mg["tagColor"], "rgb(194, 65, 12)", "the tag row wears the tag store's color")
        self.assertEqual((mg["tagBorder"], mg["tagWeight"]), ("rgb(194, 65, 12)", "400"), "…as the one tag chip: its colour on a thin border, never bold (T321)")
        self.assertTrue(any("1 session carries several tags" in n for n in mg["notes"]), mg["notes"])
        self.assertIn("team", mg["stackNames"]); self.assertIn("ops", mg["stackNames"])
        self.assertLessEqual(mg["dayBuckets"], 24, "1 day · by hour = the last 24 hourly buckets")
        self.assertTrue(any(":00" in x for x in mg["dayLabels"]), mg["dayLabels"])
        self.assertEqual(mg["prefs"].get("merge"), True)
        self.assertTrue(o["mobile"]["persisted"]["first"].strip().startswith("TESTHOST:api"), o["mobile"]["persisted"])
        self.assertTrue(any("tests" in r and "not running" in r for r in rows), "a dead session keeps its name, dimmed")
        self.assertTrue(any(r.strip().startswith("unattributed") for r in rows), "pre-attribution spend is a row of its own (its hatch mark leads)")
        self.assertGreaterEqual(o["out"]["deadRows"], 2)
        self.assertGreater(o["days"]["segs"], 24 * 30, "every session's daily bars, no fold")
        self.assertGreater(o["out"]["segs"], 60)
        self.assertGreaterEqual(o["out"]["hatched"], 1, "the unattributed stack wears the hatch, not a hue")
        self.assertIn("1 hour", o["out"]["windows"], "the same window rows the hover shows")
        self.assertIn("1 month", "".join(o["out"]["windows"]))
        self.assertTrue(o["out"]["ylabels"] and o["out"]["ylabels"][0].startswith("$"), o["out"]["ylabels"])
        self.assertTrue(o["out"]["xlabels"], "weekday initials at the ledger's midnights")
        self.assertTrue(o["days"]["segs"] > 0 and not o["days"]["ylabels"][0].startswith("$"), "tokens on the toggle")
        self.assertTrue(any("/" in x for x in o["days"]["xlabels"]), "date labels on the daily range")
        self.assertIn("·", o["tip"], "the bucket tooltip's header reads total · other measure · stamp")
        # T293 (the user 2026-09-09): the crosshair. Hovering the chart clear of every bar draws the hairline at the
        # pointer's bucket and a stamp in words; the tooltip lists that bucket's sessions in spend order in their stack
        # colours; over a bar the same box names the bar's session on its emphasised row; the marks take no pointer
        # events, move nothing under the pointer, and leave with it
        xh = o["xh"]
        self.assertTrue(xh["lineShown"] and xh["stampShown"] and xh["tipShown"], xh)
        self.assertRegex(xh["stamp"], r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} \d{1,2}, \d{1,2} (AM|PM)$")
        self.assertIn(xh["stamp"], xh["head"], "the tooltip's header carries the stamp")
        self.assertTrue(xh["head"].startswith("$"), "the bucket's total leads: " + xh["head"])
        self.assertGreaterEqual(len(xh["rows"]), 2, xh["rows"])
        def _num(s):   # fmtTok's three-significant-figure spelling: 480k, 96.0k, 1.00k, 1.2M
            m = re.match(r"^([\d.]+)([kMB]?)$", s.replace(",", "").strip())
            self.assertIsNotNone(m, s)
            return float(m.group(1)) * {"": 1, "k": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
        self.assertTrue(all(r["dot"] for r in xh["rows"]), "every row wears its stack's colour")
        self.assertFalse(any(r["on"] for r in xh["rows"]), "no bar under the pointer, no emphasised row")
        self.assertEqual((xh["lineInert"], xh["stampInert"], xh["tipInert"]), ("none", "none", "none"), "pointer-inert marks")
        self.assertEqual(xh["stampFont"], "10px", "the surface's annotation size")
        self.assertEqual(xh["chartH"], o["hBefore"], "nothing moves under the pointer")
        self.assertNotEqual(xh["movedX1"], xh["x1"], "the line follows the pointer to another bucket")
        self.assertTrue(xh["flip"], "near the right edge the stamp sits to the line's left")
        self.assertFalse(xh["flipLeft"], "…and at the first bucket to its right")
        self.assertGreaterEqual(xh["stampLeft"], xh["gyRight"], "the stamp starts past the ceiling label, never over it (review find)")
        self.assertEqual(o["afterResize"], {"tip": "none", "line": "none", "stamp": "none"}, "a rebuild under a still pointer takes all three down (review find)")
        self.assertEqual(o["xhAfter"], {"line": "none", "stamp": "none", "tip": "none"}, "all three leave with the pointer")
        # the middle range is 7 days: the label, and seven local midnights on the axis (192 hours carried eight). The
        # fixture's NOW is 15:30 local and the keys are the recorder's local hours, so the 168-hour tail holds seven T00
        # keys in any zone whose clock change falls at 1-3 AM; a zone that springs forward AT midnight would drop one:
        # the runner's zone, not the code, is what this pin assumes
        self.assertEqual(o["week"]["label"], "7 days · by hour")
        self.assertEqual(len(o["out"]["xlabels"]), 7, o["out"]["xlabels"])
        # the 1-day view stamps hours too; the 90-day view stamps dates and folds dozens of sessions into one line
        self.assertRegex(o["xhDay"]["stamp"], r", \d{1,2} (AM|PM)$")
        self.assertTrue(o["xhDay"]["tipShown"], o["xhDay"])
        xd = o["xhDays"]
        self.assertRegex(xd["stamp"], r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} \d{1,2}$")
        self.assertEqual(len(xd["rows"]), 6, "the top several: " + json.dumps(xd["rows"]))
        self.assertRegex(xd["more"] or "", r"^\+\d+ more$")
        tv = [_num(r["v"]) for r in xd["rows"]]
        self.assertEqual(tv, sorted(tv, reverse=True), "spend order: " + json.dumps(xd["rows"]))
        self.assertGreater(tv[0], tv[-1], "rows whose values differ, so the order pin can fail (review find: the hourly rows tied at $1)")
        # a short window: with no room below the pointer the box goes above the chart, never over the stamp
        xs = o["xhShort"]
        self.assertTrue(xs["tipShown"] and xs["stampShown"], xs)
        tr, sr = xs["tipRect"], xs["stampRect"]
        self.assertLess(tr["bottom"], sr["top"], "the box sits above the chart, clear of the stamp: " + json.dumps({"tip": tr, "stamp": sr}))
        # over a bar: the same box with the bar's row emphasised and named, the line still up
        self.assertTrue(o["tipOn"]["name"], o["tipOn"])
        self.assertTrue(o["tipOn"]["lineShown"])
        self.assertTrue(o["hiddenAfter"], "Escape closes it")
        self.assertTrue(o["hiddenAfterTap"], "a backdrop tap closes it")
        self.assertFalse(o["hiddenAfterDrag"], "…but a drag-select that ends over the backdrop does not (review find)")
        self.assertTrue(o["mobile"]["railHidden"] and o["mobile"]["panelOpened"] and o["mobile"]["modalOpened"],
                        "on a phone the Usage panel's 'By session' button is the door (review find): " + json.dumps(o["mobile"]))
        self.assertEqual(o["lightErr"], "rgb(154, 51, 36)", "the error line has a light-theme step")
        # T247b: the pressed toggle in the light theme wears the accent chip with --accent-fg text and no hairline
        self.assertEqual(o["lightBtn"], {"color": "rgb(255, 248, 242)", "border": "rgba(0, 0, 0, 0)", "bg": "rgb(194, 65, 12)"}, o["lightBtn"])
        # T247b: dim once — the annotation inside a dead row is at the row's level
        self.assertEqual(o["dim"]["row"], "0.55", o["dim"])
        self.assertEqual(o["dim"]["ann"], "1", "no second dimming inside a dimmed row")
        # T247b: the loader's backstop lands on the error + retry path, and names the timeout (the AbortError
        # mapping is pinned, not just the shared prefix — review find); a re-open over a pending fetch keeps
        # the loader up: the superseded fetch's abort paints nothing (review find)
        self.assertTrue(o["timeout"]["err"] and "no answer from the kernel after 1 s" in o["timeout"]["err"], o["timeout"])
        self.assertTrue(o["timeout"]["retry"])
        self.assertEqual(o["timeout"]["early"], {"err": False, "loader": True}, o["timeout"])
        # T247b: the phone's door is a real button — the hover's size, full opacity, not an annotation
        self.assertEqual(o["mobile"]["btn"]["font"], "11px", o["mobile"]["btn"])
        self.assertEqual(o["mobile"]["btn"]["opacity"], "1", o["mobile"]["btn"])
        self.assertIs(o["mobile"]["btn"]["inAge"], False)
        # 2026-10-01: the By account summary — one row per Claude account over the range shown, the seat reached from both
        # hosts one row with its routes beside its name, the key plain, Unknown last; the email only in the hover words
        ac = o["accts"]
        self.assertEqual([r["name"] for r in ac["rows"]], ["Enterprise seat", "API key", "Unknown"], ac)
        seat, key = ac["rows"][0], ac["rows"][1]
        self.assertIn("TESTHOST sign-in, token on PEERHOST", seat["text"])
        self.assertTrue(seat["usd"].startswith("\u2248$"), "seat dollars wear the estimate mark: " + seat["usd"])
        self.assertTrue(key["usd"].startswith("$"), "key dollars stay plain: " + key["usd"])
        self.assertNotIn("@", " ".join(r["text"] for r in ac["rows"]), "no email on a label")
        self.assertIn("dev@example.com", seat["tip"], "the email is in the hover words")
        self.assertIn("dev@example.com", ac["seatTip"], "…which the modal's own tooltip shows on hover")
        self.assertIn("not billed per token", ac["estTip"], "the mark's reason, in the modal's own tooltip on hover")
        self.assertTrue(any("not billed per token" in n for n in ac["notes"]), "…and once under the summary")
        self.assertLess(ac["top"], ac["listTop"], "By account sits above the session list")
        self.assertEqual(ac["dayRows"][0]["name"], "Enterprise seat", "a range switch re-sums the summary")
        self.assertNotEqual(ac["dayRows"][0]["usd"], seat["usd"])
        self.assertTrue(any(r.startswith("PEERHOST:worker") and "bills Enterprise seat" in r for r in rows), "a stored-login row bills its account by name")
        self.assertFalse(any("By login" in n for n in o["out"]["notes"]))
        web = next(r for r in rows if r.startswith("TESTHOST:web"))
        api = next(r for r in rows if r.startswith("TESTHOST:api"))
        self.assertIn("\u2248$", web, "web's turns are the seat's: marked")
        self.assertNotIn("\u2248", api, "api billed the key: plain")
        self.assertIn("TESTHOST is signed in to this account now", ac["seatTip"], "the hover says the sign-in is named by the account signed in now")
        self.assertFalse(ac["fold"], "on the desktop card the summary sits beside Totals, open")
        # the narrow card (the phone step), where the summary stacked under Totals and its rows pushed every session row
        # off the card (the #74 review's narrow-card finding): one closed line, the list starting lower than with no
        # summary at all by less than two lines and the sections' 12px row gap, a click opening it, a range switch keeping
        # it open, a resize across the narrow width unfolding and folding it, the pane refitted when it opens
        ph = o["mobile"]["accts"]
        fo, no = ph["folded"], ph["none"]
        self.assertEqual((fo["expanded"], fo["acctRows"]), ("false", 0), "one closed line: " + json.dumps(ph))
        self.assertIn("Enterprise seat \u2248$", fo["line"], "each account's dollars on the closed line, the seat's marked")
        self.assertLess(fo["acctH"], 2 * fo["lineH"], "one line tall: " + json.dumps(fo))
        self.assertLess(fo["top"] - no["top"], 2 * fo["lineH"] + 12 + 1, "the list moves down by less than two lines and the gap: " + json.dumps(ph))
        self.assertGreaterEqual(fo["rows"], max(1, no["rows"] - 1), "session rows still show: " + json.dumps(ph))
        # the fold button looks like the section head it stands for, with none of a browser's button chrome
        lk = ph["look"]
        self.assertIsNotNone(lk, json.dumps(ph))
        self.assertEqual(lk["font"], lk["headFont"], "a section head's font and colour: " + json.dumps(lk))
        self.assertEqual({k: lk[k] for k in ("bg", "border", "pad", "cursor")},
                         {"bg": "rgba(0, 0, 0, 0)", "border": "none none none none", "pad": "0px 0px 0px 0px", "cursor": "pointer"},
                         "no face, border or padding, and a control's pointer: " + json.dumps(lk))
        self.assertEqual((ph["opened"]["expanded"], ph["opened"]["acctRows"]), ("true", 3), json.dumps(ph["opened"]))
        self.assertEqual(ph["focus"], "accts:toggle", "the keyboard's place stays on the rebuilt button")
        # the caret: unturned on the closed line, turned a quarter when open (the sighted cue beside aria-expanded)
        self.assertEqual(fo["caret"], "none", json.dumps(fo))
        m = re.match(r"^matrix\(([-\d.e]+), ([-\d.e]+), ([-\d.e]+), ([-\d.e]+), 0, 0\)$", ph["opened"]["caret"] or "")
        self.assertTrue(m and abs(float(m.group(1))) < 1e-6 and abs(float(m.group(2)) - 1) < 1e-6, "turned 90 degrees when open: " + json.dumps(ph["opened"]))
        # …and its box turns with it (width and height swap): a transform applies only to a box that is not inline, and an
        # inline caret reports the quarter turn while it still draws unturned
        cb, ob = fo["caretBox"], ph["opened"]["caretBox"]
        self.assertTrue(cb and ob and abs(cb[1] - cb[0]) > 1 and abs(ob[0] - cb[1]) < 0.6 and abs(ob[1] - cb[0]) < 0.6,
                        "the caret's box turned a quarter when open: " + json.dumps([cb, ob]))
        # …in a box of its own width, upright
        cr = lk["caret"]
        self.assertIsNotNone(cr, json.dumps(lk))
        self.assertLess(abs(cr["w"] - 0.9 * cr["em"]), 0.2, "the caret's box is 0.9em wide: " + json.dumps(cr))
        self.assertEqual((cr["fontStyle"], cr["transition"]), ("normal", "transform 0.12s"), "the caret's font style and transition: " + json.dumps(cr))
        # the closed line's text sits on the head's baseline
        bh, bl = lk["baselines"]
        self.assertTrue(bh is not None and bl is not None and abs(bh - bl) < 0.5, "the closed line on the head's baseline: " + json.dumps(lk))
        # the keyboard: Enter toggles the focused fold, the rebuilt button keeps the focus and shows the fold's own ring
        # (the browser's default ring is outline-style auto), set off from the button
        self.assertEqual(ph["kbdClosed"], {"expanded": "false", "focused": True, "ring": "solid 1px 2px"}, json.dumps(ph["kbdClosed"]))
        self.assertEqual((ph["kbdOpened"]["expanded"], ph["kbdOpened"]["acctRows"]), ("true", 3), json.dumps(ph["kbdOpened"]))
        self.assertEqual((ph["afterRange"]["expanded"], ph["afterRange"]["acctRows"]), ("true", 3), "a range switch keeps the user's open fold")
        # …and repaints it: the open fold's head names the range it now sums
        self.assertIn("last 7 days", ph["opened"]["line"], json.dumps(ph["opened"]))
        self.assertIn("last 24 hours", ph["afterRange"]["line"], "the range switch repainted the summary: " + json.dumps(ph["afterRange"]))
        self.assertEqual((ph["closed"]["expanded"], ph["closed"]["top"]), ("false", fo["top"]), json.dumps(ph["closed"]))
        self.assertEqual((ph["widened"]["expanded"], ph["widened"]["acctRows"]), (None, 3),
                         "widened past the narrow width, the summary sits beside Totals in full: " + json.dumps(ph["widened"]))
        self.assertEqual((ph["narrowed"]["expanded"], ph["narrowed"]["acctRows"]), ("false", 0),
                         "narrowed again, it folds: " + json.dumps(ph["narrowed"]))
        tall = ph["tall"]
        self.assertEqual(tall["folded"]["expanded"], "false", "folded on a tall narrow card: " + json.dumps(tall))
        self.assertLess(tall.get("openedMax", tall["foldedMax"]), tall["foldedMax"], "opening the fold refits the pane: " + json.dumps(tall))
        self.assertEqual(tall.get("closedMax"), tall["foldedMax"], "closing it gives the room back: " + json.dumps(tall))
        # the fold repainted BEFORE the pane is sized, in the resize handler and in the first paint: a resize from the wide
        # card to the tall narrow one, and the modal reopened there, each fit the pane to the folded summary at once
        wt, ro = ph["wideTall"], ph["reopened"]
        self.assertEqual((wt["wide"]["expanded"], wt["wide"]["acctRows"]), (None, 3), "beside Totals in full before the resize: " + json.dumps(wt))
        self.assertEqual((wt["geo"]["expanded"], ro["geo"]["expanded"]), ("false", "false"), "folded after the resize and on the reopen: " + json.dumps(ph))
        self.assertEqual((wt["max"], wt["again"]), (tall["foldedMax"], tall["foldedMax"]),
                         "the resize that folded the summary refitted the pane in the same pass: " + json.dumps(wt))
        self.assertEqual((ro["max"], ro["again"]), (tall["foldedMax"], tall["foldedMax"]),
                         "the first paint sized the pane after folding the summary: " + json.dumps(ro))
        clip = ph["clip"]
        self.assertEqual(clip["expanded"], "false", json.dumps(clip))
        self.assertLess(clip["acctH"], 2 * clip["lineH"], "clipped to one line, never wrapped: " + json.dumps(clip))
        self.assertTrue(clip["overflows"], "the closed line holds more than fits at 320px, so the clip is what keeps it one line: " + json.dumps(clip))
        self.assertLessEqual(clip["spill"], 0, "cut short inside the card, which does not scroll sideways: " + json.dumps(clip))
        self.assertEqual(clip["cut"], "ellipsis", "the cut wears an ellipsis: " + json.dumps(clip))
        # the open fold lives as long as the page: kept when the modal is closed and opened again, closed after a reload
        self.assertEqual(ph["kept"], {"closed": "false", "opened": "true", "reopened": "true", "reloaded": "false"}, json.dumps(ph["kept"]))
        # a tap on the closed line's estimate-marked dollars (a touch screen hovers as well as clicks) opens the fold and
        # leaves no hover words up over the rows it opened
        tap = o["mobile"]["tap"]
        self.assertTrue(tap["at"] and tap["at"]["expanded"] == "false" and tap["at"]["onLine"], "the tap lands on the closed line: " + json.dumps(tap))
        self.assertEqual(tap.get("after"), {"expanded": "true", "tip": ""}, "opened, no hover words up: " + json.dumps(tap))
        # scrollbars that take room: at 676x728 the card's scrollbar alone decides whether the sections wrap; the card keeps
        # its room whether it scrolls or not, so the fold decided at open holds
        cl = o["classic"]
        self.assertGreaterEqual(cl["open"]["room"], 10, "this browser's scrollbars take room (launched without --hide-scrollbars): " + json.dumps(cl))
        self.assertTrue(cl["open"]["stacked"], "the sections wrap at this width: " + json.dumps(cl))
        self.assertEqual((cl["open"]["expanded"], cl["open"]["acctRows"]), ("false", 0), "folded the moment the modal opens: " + json.dumps(cl))
        self.assertEqual(cl["settled"], cl["open"], "and so after the layout settles and a resize event runs: " + json.dumps(cl))
        # at 940x820 the card does not scroll and keeps no room for a scrollbar
        self.assertFalse(cl["wide"]["scrolls"], "the card does not scroll at 940x820: " + json.dumps(cl))
        self.assertEqual(cl["wide"]["room"], 0, "no room kept for a scrollbar the card does not show: " + json.dumps(cl))
        # at 600x880 opening the fold makes the card scroll, and the content width and the chart stay as they were
        nw = cl["narrow"]
        self.assertEqual((nw["closed"]["scrolls"], (nw.get("opened") or {}).get("scrolls")), (False, True),
                         "opening the fold makes the card scroll here: " + json.dumps(nw))
        self.assertEqual((nw["opened"]["contentW"], nw["opened"]["chartW"]), (nw["closed"]["contentW"], nw["closed"]["chartW"]),
                         "the content width and the chart stay as they were: " + json.dumps(nw))
        # the room the unscrolled card keeps there is one scrollbar's width; its overflow is auto, so the card shows a
        # scrollbar only when it scrolls
        self.assertEqual(nw["closed"]["room"], cl["scrollbarW"], "one scrollbar's room: " + json.dumps(cl))
        self.assertEqual(nw["closed"]["overflowY"], "auto", "a scrollbar only when the card scrolls: " + json.dumps(nw))
        own = [e for e in o["errs"] if "rsp" in e or "Spend" in e or "spend" in e]
        self.assertEqual(own, [], "no page errors from the modal's own code")


if __name__ == "__main__":
    unittest.main()
