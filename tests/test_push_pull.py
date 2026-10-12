#!/usr/bin/env python3
"""Federated push, the pull direction (plans/federated-push.md, 2026-10-10), in process.

Push was the one cross-machine flow the ORIGIN started (POST /push/relay toward rows in its own table), and with
ssh-attach only the attacher holds a row, so an attached machine's events never reached the phone subscribed at the
attacher; the origin also gated on its OWN switches. Now the kernel holding a row PULLS: GET /push/events on the peer,
over the row's port and token. What these pin, by layer (the two-kernel run is tests/test_push_pull_kernels.py):

  * the origin's ring — both detectors (the card diff, the turn tick) file every detected event as a candidate whatever
    the origin's master and turn switch say; an explicit card or session mute on the origin drops it, an explicit arm
    rides as bell "on", no override as "default";
  * the latent claim bug — the origin's own turn push claiming its one buzz no longer swallows the needs-you that
    follows: the card is still a candidate, and the hub's gate decides;
  * the serve — the long-poll returns the moment an event lands; a missing, foreign or overflowed cursor resets to the
    newest with its reason and count; staleness is re-validated at serve time (a card still in its column and not acted
    on, a turn end still the latest, no mute since); the route is token-gated;
  * the hub's gate — its master and turn switch decide, a "default" bell resolves to its master, the sid wears the HUB's
    name for the row, untrusted drops, and the one buzz per turn end is the hub's, keyed on the prefixed sid;
  * the puller — a reset is one loud line; the cursor lands on disk after the gate and survives a restart; an event
    passes the gate once by (row, boot, seq); a refusal is said once;
  * the transition — the legacy forward skips peers that pull, a relay from a kernel this one pulls is ignored, the
    /version poll lifts the capability, and an event offered both ways reaches the devices once;
  * bells follow the dashboard — a linked dashboard's click is stored as given (a mute recorded whatever the origin's
    master says), a local click keeps delete-if-default, the payloads carry the raw override, the switches frame reaches
    every ready pane, and the hub's gate follows the rules its own sessions follow.

Synthetic only: invented hosts (TESTHOST, boxa, boxb), private placeholder sids, invented text.
"""
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads: they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
_STATE_TD = tempfile.TemporaryDirectory()
jd._rebind_state(Path(_STATE_TD.name))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_push_pull", os.path.join(BIN, "romp-kernel"))

WEB = "efefefef-7171-4c4c-8e8e-00000000bb01"   # private synthetic sids: no other module's journal reaches them
API = "efefefef-7171-4c4c-8e8e-00000000bb02"
ROW = "TESTHOST"


def _card(iid, sid, col, text="Pick the parser's error format", name="web"):
    return {"itemId": iid, "sid": sid, "name": name, "column": col, "text": text}


def _feed(*cards, sessions=None, now=None):
    d = {"type": "feed", "asks": [dict(c) for c in cards]}
    if sessions is not None:
        d["sessions"] = [{"sid": s, "name": "s"} for s in sessions]
    if now is not None:
        d["now"] = now
    return d


def _stamp_stop(sid, t, opener="human", human_at=None):
    """The Stop hook's stamp: the settle, its opener, and the newest end of a turn a human opened (this one when a human
    opened it, else `human_at`, an earlier one, or none)."""
    d = jd.STATE / "sdk"
    d.mkdir(parents=True, exist_ok=True)
    reg = {"lastStopAt": int(t), "lastTurnOpener": opener}
    if opener == "human" or human_at:
        reg["lastHumanStopAt"] = int(t if opener == "human" else human_at)
    (d / (sid + ".json")).write_text(json.dumps(reg))


def _transcript(sid, text):
    p = jd.STATE / ("transcript-%s.jsonl" % sid[-4:])
    p.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n"
                 + json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
    return str(p)


def _serve(method, path, body=None, token=True):
    """Drive the REAL dispatcher over a fake socket: (status, body bytes)."""
    raw = json.dumps(body).encode() if body is not None else b""
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers = {"Content-Length": str(len(raw))}
    if token:
        h.headers["X-Romp-Token"] = km.TOKEN
    h.path = path
    h.command = method
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO(raw)
    h.close_connection = True
    got = {}
    h.send_response = lambda code, *a: got.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    (h.do_GET if method == "GET" else h.do_POST)()
    return got.get("status"), h.wfile.getvalue()


def _row(host=ROW, trust="trusted", push_pull=1, **kw):
    r = {"host": host, "kernel_port": 1, "local_port": 4242, "token": "tok-peer", "proc": None, "status": "up",
         "trust": trust, "push_pull": push_pull}
    r.update(kw)
    with km._remotes_lock:
        km._remotes[host] = r
    return r


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd._rebind_state(Path(self.td.name))
        km._notify_cards_cache.clear()
        km._flags_cache.clear()
        km._NOTIFY_PREV[0] = None
        km._TURN_PREV.clear()
        km._PUSH_BUZZED.clear()
        with km._push_ring_cond:
            km._PUSH_RING.clear()
            km._PUSH_RING_SEQ[0] = 0
        km._PUSH_PULL_SEEN.clear()
        km._PUSH_PULL_CURSORS[0] = None
        km._PUSH_PULL_CURSOR_LOST[0] = False
        km._PUSH_PULL_SAID.clear()
        km._PUSH_RELAY_IGNORED_SAID.clear()
        km._PUSH_GAPS.clear()
        km._PUSH_PULL_ANSWERED.clear()
        km._PUSH_PULL_LAST_OK.clear()
        km._PUSH_GAP_TOLD.clear()
        km._PUSH_GAP_TOLD_LOADED[0] = False                 # this test's life reads its own (empty) state root
        with km._remotes_lock:
            km._remotes.clear()
        with km._known_lock:
            km._known.clear()

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        km._NOTIFY_PREV[0] = None
        jd._rebind_state(self.saved)
        self.td.cleanup()

    def ring(self):
        with km._push_ring_cond:
            return [dict(e) for e in km._PUSH_RING]

    def mute(self, setter, *args):
        """An explicit mute on this machine. The card setter stores a click that differs from this machine's master (a
        click matching it returns the bell to following the master), so the mute is clicked under the master on, and
        the master then goes back off: the override stands either way. (The session setter stores every click as given
        since 2026-10-11; the helper serves both.)"""
        km._set_notify_all(True)
        setter(*args)
        km._set_notify_all(False)

    def block(self, iid=WEB + ":g1", sid=WEB):
        """A card that enters needs_input on this kernel: a seeded first build, then the block."""
        km._feed_notifications(_feed(_card(iid, sid, "working"), sessions=[sid]))
        return km._feed_notifications(_feed(_card(iid, sid, "needs_input"), sessions=[sid]))


class OriginRing(_Base):
    """Every detected event is a candidate; only an explicit bell on the origin gates it."""

    def test_a_card_entering_needs_you_is_a_candidate_whatever_this_machines_switches_say(self):
        self.assertFalse(km._notify_all_on(), "the origin's master is off")
        out = self.block()
        self.assertEqual(out, [], "this machine's own devices hear nothing: its master is off")
        (ev,) = self.ring()
        self.assertEqual((ev["kind"], ev["sid"], ev["cardId"], ev["column"], ev["bell"]),
                         ("card", WEB, WEB + ":g1", "needs_input", "default"))
        self.assertEqual(ev["title"], "Romp needs you: web")
        self.assertTrue(ev["body"].startswith("Needs you: Pick the parser"), ev["body"])
        self.assertEqual(ev["seq"], 1)

    def test_an_explicit_card_mute_or_session_mute_on_the_origin_drops_it(self):
        self.mute(km._set_notify_card, WEB + ":g1", False, WEB)
        self.block()
        self.assertEqual(self.ring(), [], "a card mute")
        km._NOTIFY_PREV[0] = None
        self.mute(km._set_notify_session, API, False)
        self.block(API + ":g1", API)
        self.assertEqual(self.ring(), [], "a session mute")

    def test_an_explicit_arm_rides_as_on(self):
        km._set_notify_card(WEB + ":g1", True, WEB)
        out = self.block()
        self.assertEqual(len(out), 1, "armed on this machine: its own devices hear it too")
        self.assertEqual(self.ring()[0]["bell"], "on")

    def test_a_human_turn_end_is_a_candidate_whatever_this_machines_switches_say(self):
        path = _transcript(API, "The fixtures load cleanly now.\n\nDetails below.")
        alive = [{"sid": API, "name": "api", "path": path}]
        with mock.patch.object(km, "_alive_sessions", return_value=alive), \
             mock.patch.object(km, "_push_notify") as pn, mock.patch.object(km, "_push_forward"):
            _stamp_stop(API, 1000)
            km._turn_notify_tick(time.time(), {API: {}})          # the first sight: a baseline
            self.assertEqual(self.ring(), [])
            _stamp_stop(API, 1001)
            km._turn_notify_tick(time.time(), {API: {}})
            pn.assert_not_called()                               # the origin's own switches are off: its devices hear nothing
        (ev,) = self.ring()
        self.assertEqual((ev["kind"], ev["sid"], ev["turnKey"], ev["bell"], ev["name"]), ("turn", API, 1001, "default", "api"))
        self.assertEqual((ev["title"], ev["body"]), ("Romp: api", "The fixtures load cleanly now."))

    def test_an_injected_turn_or_a_muted_session_is_no_candidate(self):
        path = _transcript(API, "Noted.")
        alive = [{"sid": API, "name": "api", "path": path}]
        with mock.patch.object(km, "_alive_sessions", return_value=alive), \
             mock.patch.object(km, "_push_notify"), mock.patch.object(km, "_push_forward"):
            _stamp_stop(API, 1000)
            km._turn_notify_tick(time.time(), {API: {}})
            _stamp_stop(API, 1001, opener="injected")
            km._turn_notify_tick(time.time(), {API: {}})
            self.assertEqual(self.ring(), [], "a turn the user did not open")
            self.mute(km._set_notify_session, API, False)
            _stamp_stop(API, 1002)
            km._turn_notify_tick(time.time(), {API: {}})
            self.assertEqual(self.ring(), [], "the session's own bell off on this machine")


class LatentClaimBug(_Base):
    """Before the pull, the origin's own turn push claimed the turn end's one buzz (_buzz_claim), the card raised by the
    same turn end then yielded locally, and only what buzzed locally was forwarded: a hub never heard that needs-you."""

    def _turn_then_block(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)                          # the ORIGIN's turn switch on: its own turn push claims
        path = _transcript(WEB, "Which error format should the parser report?")
        alive = [{"sid": WEB, "name": "web", "path": path}]
        with mock.patch.object(km, "_alive_sessions", return_value=alive), \
             mock.patch.object(km, "_push_notify") as pn, mock.patch.object(km, "_push_forward"):
            _stamp_stop(WEB, 1000)
            km._turn_notify_tick(time.time(), {WEB: {}})
            _stamp_stop(WEB, 1001)
            km._turn_notify_tick(time.time(), {WEB: {}})
            self.assertEqual(pn.call_args[1].get("kind"), "turn", "the origin buzzed its own devices for the turn end")
        self.assertFalse(km._buzz_claim(WEB, 1001, "bell"), "…and holds the turn end's claim, so its card push yields")
        self.block()
        return self.ring()

    def test_the_needs_you_after_the_origins_own_turn_push_still_leaves_the_origin(self):
        evs = self._turn_then_block()
        self.assertEqual([(e["kind"], e["turnKey"]) for e in evs], [("turn", 1001), ("card", 1001)],
                         "the card is a candidate like any other: the origin's claim governs its own devices only")

    def test_the_hub_buzzes_it_once_by_its_own_switches(self):
        evs = self._turn_then_block()
        _row()
        with mock.patch.object(km, "_push_notify") as pn, \
             mock.patch.object(km, "_notify_turns_on", return_value=False), \
             contextlib.redirect_stderr(io.StringIO()):
            km._push_pull_gate(ROW, evs)                     # the HUB's turn switch off: the turn is held, the card buzzes
        (args, kw), = pn.call_args_list
        self.assertEqual((args[2], kw.get("kind"), kw.get("quiet", False)), (ROW + ":" + WEB, "card", False))
        km._PUSH_BUZZED.clear()
        with mock.patch.object(km, "_push_notify") as pn:
            km._push_pull_gate(ROW, evs)                     # the hub's turn switch on: the turn buzzes, the card replaces it quietly
        self.assertEqual([(c[1].get("kind"), c[1].get("quiet", False)) for c in pn.call_args_list],
                         [("turn", False), ("card", True)])


class ServeEvents(_Base):
    def add(self, **ev):
        e = {"kind": "turn", "sid": API, "title": "Romp: api", "body": "Done.", "name": "api", "bell": "default",
             "turnKey": 1001}
        e.update(ev)
        km._push_ring_add([e])
        return km._PUSH_RING_SEQ[0]

    def cursor(self, seq, boot=None):
        return "%s:%d" % (boot or km._BOOT_ID, seq)

    def test_no_cursor_resumes_at_the_newest(self):
        self.add(); self.add()
        ans = km._push_events_serve("", 5)
        self.assertEqual(ans, {"boot": km._BOOT_ID, "next": 2, "events": [], "reset": "no-cursor", "skipped": 2})

    def test_the_answer_returns_the_moment_an_event_lands(self):
        _stamp_stop(API, 1001)
        n = self.add()
        got = {}
        t = threading.Thread(target=lambda: got.update(ans=km._push_events_serve(self.cursor(n), 10), at=time.monotonic()))
        t0 = time.monotonic()
        t.start()
        time.sleep(0.3)
        self.assertTrue(t.is_alive(), "nothing newer than the cursor: the request parks")
        _stamp_stop(API, 1002)
        self.add(turnKey=1002)
        t.join(5)
        self.assertLess(got["at"] - t0, 3, "released by the event, not the wait")
        self.assertEqual([e["seq"] for e in got["ans"]["events"]], [n + 1])
        self.assertEqual(got["ans"]["next"], n + 1)
        self.assertEqual(set(got["ans"]["events"][0]), set(km._PUSH_EVENT_WIRE), "the wire form only")

    def test_an_empty_handed_answer_keeps_the_cursor(self):
        n = self.add()
        self.assertEqual(km._push_events_serve(self.cursor(n), 0.1), {"boot": km._BOOT_ID, "next": n, "events": []})

    def test_another_lifes_cursor_or_one_never_issued_resets_as_restarted(self):
        self.add(); self.add(); self.add()
        for cur in (self.cursor(1, boot="999.123"), self.cursor(9)):
            ans = km._push_events_serve(cur, 0)
            self.assertEqual((ans["reset"], ans["skipped"], ans["next"], ans["events"]), ("restarted", 3, 3, []), cur)

    def test_a_cursor_the_ring_overflowed_past_resets_as_overflow(self):
        for _ in range(km.PUSH_RING_CAP + 5):
            self.add()
        ans = km._push_events_serve(self.cursor(2), 0)
        self.assertEqual((ans["reset"], ans["skipped"], ans["next"]), ("overflow", km.PUSH_RING_CAP + 3, km.PUSH_RING_CAP + 5))
        # the oldest kept event's predecessor is still a cursor the ring covers
        oldest = km._PUSH_RING[0]["seq"]
        self.assertNotIn("reset", km._push_events_serve(self.cursor(oldest - 1), 0))

    def test_a_turn_event_is_served_only_while_it_is_the_sessions_latest_end(self):
        _stamp_stop(API, 1001)
        n = self.add(turnKey=1001)
        self.assertEqual(len(km._push_events_serve(self.cursor(n - 1), 0)["events"]), 1)
        _stamp_stop(API, 1002)                               # a newer turn end since: the old one is no longer news
        ans = km._push_events_serve(self.cursor(n - 1), 0)
        self.assertEqual((ans["events"], ans["stale"], ans["next"]), ([], 1, n), "skipped, and the cursor passes it")

    def test_a_card_event_is_served_only_while_in_its_column_and_not_acted_on(self):
        self.block()
        n = km._PUSH_RING_SEQ[0]
        at = self.ring()[0]["at"]
        self.assertEqual(len(km._push_events_serve(self.cursor(n - 1), 0)["events"]), 1)
        # the user answered it on the card: a targeted reply in the override journal
        d = jd._overrides_dir(); d.mkdir(parents=True, exist_ok=True)
        (d / (WEB + ".jsonl")).write_text(json.dumps({"node": WEB + ":g1", "op": "followup", "t": at + 5}) + "\n")
        self.assertEqual(km._push_events_serve(self.cursor(n - 1), 0)["stale"], 1, "acted on since")
        (d / (WEB + ".jsonl")).unlink()
        km._feed_notifications(_feed(_card(WEB + ":g1", WEB, "working"), sessions=[WEB]))
        self.assertEqual(km._push_events_serve(self.cursor(n - 1), 0)["stale"], 1, "no longer in that column")

    def test_a_mute_set_on_the_origin_since_drops_it_at_serve_time(self):
        _stamp_stop(API, 1001)
        n = self.add()
        self.mute(km._set_notify_session, API, False)
        self.assertEqual(km._push_events_serve(self.cursor(n - 1), 0)["stale"], 1)

    def test_protocol_2_says_caught_up_and_bells_on_and_serves_stale_events_flagged(self):
        _stamp_stop(API, 1001)
        n = self.add(turnKey=1001)
        ans = km._push_events_serve(self.cursor(n), 0, 2)
        self.assertEqual((ans["caughtUp"], ans["bellsOn"], ans["events"]), (True, False, []), "found at the newest")
        _stamp_stop(API, 1002)                               # a newer turn end: the event is stale, not dropped, at v2
        ans = km._push_events_serve(self.cursor(n - 1), 0, 2)
        self.assertEqual(ans["caughtUp"], False, "behind the newest: what it carries fired before the request")
        self.assertEqual([(e["seq"], e.get("current")) for e in ans["events"]], [(n, False)])
        self.assertEqual(ans["stale"], 1)
        self.assertEqual(km._push_events_serve(self.cursor(n - 1), 0)["events"], [], "a v1 puller is served as before")
        km._set_notify_session(API, True)                    # an explicit on anywhere here
        self.assertTrue(km._push_events_serve(self.cursor(n), 0, 2)["bellsOn"])
        km._set_notify_session(API, False)                   # a mute since: dropped at v2 too
        self.assertEqual(km._push_events_serve(self.cursor(n - 1), 0, 2)["events"], [])
        self.assertIs(km._push_events_serve("", 0, 2)["bellsOn"], False, "a reset answer says it too (the mute is no on)")
        # whether this kernel detects card events at all: the Task tracking switch, on every protocol-2 answer
        self.assertIs(km._push_events_serve("", 0, 2)["cardsOn"], True, "tracking on, the default")
        with mock.patch.object(km, "_task_tracking_on", return_value=False):
            self.assertIs(km._push_events_serve("", 0, 2)["cardsOn"], False)
            self.assertIs(km._push_events_serve(self.cursor(n), 0, 2)["cardsOn"], False)
        self.assertNotIn("cardsOn", km._push_events_serve(self.cursor(n), 0), "a v1 puller is served as before")
        st, raw = _serve("GET", "/push/events?wait=0&v=2&after=" + self.cursor(n))
        self.assertEqual((st, json.loads(raw)["caughtUp"]), (200, True))
        self.assertEqual(_serve("GET", "/push/events?wait=0&v=two")[0], 400)

    def test_the_route_is_token_gated_and_checks_its_cursor(self):
        self.assertEqual(_serve("GET", "/push/events?wait=0", token=False)[0], 403)
        self.assertEqual(_serve("GET", "/push/events?after=nonsense&wait=0")[0], 400)
        self.assertEqual(_serve("GET", "/push/events?wait=soon")[0], 400)
        self.add()
        st, raw = _serve("GET", "/push/events?wait=0")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["reset"], "no-cursor")

    def test_version_advertises_the_pull(self):
        self.assertEqual(km._version_info()["pushPull"], km.PUSH_PULL_PROTO)


class HubGate(_Base):
    def setUp(self):
        super().setUp()
        _row()
        km._set_notify_all(True)
        km._set_notify_turns(True)

    def gate(self, evs):
        with mock.patch.object(km, "_push_notify") as pn, contextlib.redirect_stderr(io.StringIO()) as err:
            n = km._push_pull_gate(ROW, evs)
        return n, pn.call_args_list, err.getvalue()

    CARD = {"seq": 1, "kind": "card", "sid": WEB, "title": "Romp needs you: web", "body": "Needs you: pick a format",
            "name": "web", "cardId": WEB + ":g1", "bell": "default", "turnKey": 0}
    TURN = {"seq": 2, "kind": "turn", "sid": API, "title": "Romp: api", "body": "Done.", "name": "api",
            "cardId": None, "bell": "default", "turnKey": 1001}

    def test_both_kinds_buzz_under_the_hubs_name_for_the_row(self):
        n, calls, _ = self.gate([self.CARD, self.TURN])
        self.assertEqual(n, 2)
        (a1, k1), (a2, k2) = calls
        self.assertEqual(a1, ("Romp needs you: web", "Needs you: pick a format", ROW + ":" + WEB))
        self.assertEqual(k1, {"kind": "card", "card_id": WEB + ":g1", "host": ROW, "name": "web"})
        self.assertEqual(a2[2], ROW + ":" + API)
        self.assertEqual(k2, {"kind": "turn", "card_id": "", "host": ROW, "name": "api"})
        self.assertNotIn("badge", k1, "the origin's count is not this kernel's")

    def test_the_hubs_master_and_turn_switch_decide(self):
        km._set_notify_all(False)
        n, calls, err = self.gate([self.CARD, self.TURN])
        self.assertEqual((n, calls), (0, []))
        self.assertIn("held 2 event(s) pulled from 'TESTHOST'", err)
        km._set_notify_all(True)
        km._set_notify_turns(False)
        n, calls, err = self.gate([self.CARD, self.TURN])
        self.assertEqual([c[1]["kind"] for c in calls], ["card"], "the turn switch holds the turn events only")
        self.assertIn("1 turn-finished event(s) under its turn switch", err)

    def test_an_explicit_on_beats_both_switches_for_a_turn_end_too(self):
        km._set_notify_all(False)
        km._set_notify_turns(False)
        n, calls, err = self.gate([dict(self.TURN, bell="on"), dict(self.TURN, seq=3, sid=WEB, bell="default")])
        self.assertEqual([c[0][2] for c in calls], [ROW + ":" + API], "the armed session's turn end buzzes; no choice is held")
        km._set_notify_all(True)
        n, calls, err = self.gate([dict(self.TURN, seq=4, turnKey=1002, bell="on"), dict(self.TURN, seq=5, sid=WEB, bell="default")])
        self.assertEqual([c[0][2] for c in calls], [ROW + ":" + API], "the turn switch off holds no choice, not an explicit on")
        self.assertIn("1 turn-finished event(s) under its turn switch", err)

    def test_a_burst_over_the_cap_is_collapsed_into_one_notification_and_one_loud_line(self):
        evs = [dict(self.CARD, seq=i, bell="on", cardId="%s:g%d" % (WEB, i)) for i in range(1, 21)]
        evs += [dict(self.TURN, seq=30 + i, sid="efefefef-7171-4c4c-8e8e-0000000000c%d" % i, turnKey=2000 + i) for i in range(3)]
        n, calls, err = self.gate(evs)
        self.assertEqual(len(calls), km.PUSH_PULL_BATCH_CAP + 1, "the cap, then one notification for the rest")
        (args, kw) = calls[-1]
        self.assertEqual(args, ("Romp: more notifications", "TESTHOST · 7 more: 3 sessions finished turns, 4 need you", ""))
        self.assertEqual(kw, {"kind": "more", "host": ROW})
        self.assertIn("7 event(s) pulled from 'TESTHOST' in one answer were past the cap of 16 notifications; collapsed "
                      "into one", err)

    def test_default_and_on_pass_and_anything_else_does_not(self):
        evs = [dict(self.CARD, seq=i, bell=b, cardId="%s:g%d" % (WEB, i)) for i, b in enumerate(("on", "default", "off", None, "?"))]
        n, calls, _ = self.gate(evs)
        self.assertEqual([c[1]["card_id"] for c in calls], [WEB + ":g0", WEB + ":g1"])

    def test_a_row_below_trusted_buzzes_nothing(self):
        _row(trust="directed")
        n, calls, err = self.gate([self.CARD])
        self.assertEqual((n, calls), (0, []))
        self.assertIn("its trust here is 'directed'", err)

    def test_one_buzz_per_turn_end_is_the_hubs_both_orders(self):
        turn = dict(self.TURN, sid=WEB)
        card = dict(self.CARD, turnKey=1001)
        _, calls, _ = self.gate([turn, card])
        self.assertEqual([(c[1]["kind"], c[1].get("quiet", False)) for c in calls], [("turn", False), ("card", True)])
        km._PUSH_BUZZED.clear()
        _, calls, _ = self.gate([card, turn])
        self.assertEqual([c[1]["kind"] for c in calls], ["card"], "the card buzzed first: the turn end yields")
        km._PUSH_BUZZED.clear()
        _, calls, _ = self.gate([card, dict(card, cardId=WEB + ":g2")])
        self.assertEqual(len(calls), 2, "two cards never suppress each other")


class Puller(_Base):
    def setUp(self):
        super().setUp()
        _row()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _stamp_stop(API, 1001)

    def once(self, answer):
        asked = []

        def fake_get(r, path, timeout):
            asked.append(path)
            return answer
        with mock.patch.object(km, "_push_pull_get", side_effect=fake_get), \
             mock.patch.object(km, "_push_notify") as pn, contextlib.redirect_stderr(io.StringIO()) as err:
            ok = km._push_pull_once(ROW, dict(km._remotes[ROW]))
        return ok, asked, pn.call_args_list, err.getvalue()

    EV = {"seq": 4, "kind": "turn", "sid": API, "title": "Romp: api", "body": "Done.", "name": "api", "cardId": None,
          "bell": "default", "turnKey": 1001}

    def test_a_reset_is_one_loud_line_and_the_cursor_lands_on_disk(self):
        ok, asked, calls, err = self.once((200, {"boot": "77.1", "next": 3, "events": [], "reset": "restarted", "skipped": 3}))
        self.assertTrue(ok)
        self.assertEqual(asked, ["/push/events?wait=%d&v=2" % km.PUSH_PULL_WAIT], "no cursor held: none sent")
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("events from 'TESTHOST' resume at its newest — its kernel restarted since this kernel last heard from "
                      "it; 3 event(s) it fired meanwhile skipped", err)
        disk = json.loads((jd.STATE / "push-pull-cursors.json").read_text())
        self.assertEqual(disk, {ROW: "77.1:3"})
        self.assertEqual(oct((jd.STATE / "push-pull-cursors.json").stat().st_mode & 0o777), "0o600")

    def test_the_next_ask_carries_the_cursor_and_it_survives_a_restart(self):
        self.once((200, {"boot": "77.1", "next": 3, "events": [], "caughtUp": True}))
        km._PUSH_PULL_CURSORS[0] = None                      # a new kernel life: read back from disk
        km._PUSH_PULL_ANSWERED.clear()
        ok, asked, calls, _ = self.once((200, {"boot": "77.1", "next": 4, "events": [self.EV], "caughtUp": True}))
        self.assertEqual(asked, ["/push/events?wait=%d&v=2&after=77.1%%3A3" % km.PUSH_PULL_WAIT])
        self.assertEqual(len(calls), 1, "nothing fired while it was down (it found its cursor at the newest): the event "
                                        "that fired while it listened is delivered as it arrives")
        self.assertEqual(km._push_pull_cursor(ROW), "77.1:4")

    def test_an_event_passes_the_gate_once(self):
        ans = (200, {"boot": "77.1", "next": 4, "events": [self.EV]})
        self.assertEqual(len(self.once(ans)[2]), 1)
        self.assertEqual(len(self.once(ans)[2]), 0, "the same (row, boot, seq) again: the backstop holds it")
        self.assertEqual(len(self.once((200, {"boot": "78.2", "next": 4, "events": [dict(self.EV, turnKey=1002)]}))[2]), 1,
                         "another life's seq 4 is another event")

    def test_an_event_is_marked_decided_only_after_the_gate_has_decided_it(self):
        # the puller crashing inside the gate (here: the gate raising) must not lose the event: the next life re-offers
        # it, and the (row, boot, seq) backstop still holds it to once
        ans = (200, {"boot": "77.1", "next": 4, "events": [self.EV]})
        with mock.patch.object(km, "_push_pull_get", return_value=ans), \
             mock.patch.object(km, "_push_notify", side_effect=RuntimeError("the gate fell over")):
            with self.assertRaises(RuntimeError):
                km._push_pull_once(ROW, dict(km._remotes[ROW]))
        self.assertNotIn((ROW, "77.1", 4), km._PUSH_PULL_SEEN, "not decided, so not marked")
        self.assertEqual(km._push_pull_cursor(ROW), "", "and the cursor did not move")
        self.assertEqual(len(self.once(ans)[2]), 1, "re-offered and delivered")
        self.assertEqual(len(self.once(ans)[2]), 0, "once")

    def test_a_refusal_is_said_once_and_moves_nothing(self):
        km._push_pull_cursor_set(ROW, "77.1:3")
        ok, _, _, err = self.once((403, {}))
        self.assertFalse(ok)
        self.assertIn("'TESTHOST' answered the event pull with HTTP 403", err)
        self.assertEqual(self.once((403, {}))[3], "", "the same refusal again: said once")
        self.assertEqual(km._push_pull_cursor(ROW), "77.1:3")
        self.assertEqual(self.once((0, {"error": "refused"}))[:1], (False,), "unreachable: the loop backs off")

    def test_the_gate_runs_before_the_cursor_moves(self):
        order = []
        with mock.patch.object(km, "_push_pull_gate", side_effect=lambda *a, **k: order.append("gate")), \
             mock.patch.object(km, "_push_pull_cursor_set", side_effect=lambda *a: order.append("cursor")), \
             mock.patch.object(km, "_push_pull_get", return_value=(200, {"boot": "77.1", "next": 4, "events": [self.EV]})):
            km._push_pull_once(ROW, dict(km._remotes[ROW]))
        self.assertEqual(order, ["gate", "cursor"], "a crash between them re-offers the events rather than losing them")


class GapPolicy(_Base):
    """Never late, never silent (the user 2026-10-11; plans/federated-push.md, "Gaps"): events are delivered one by one
    while the link holds; after any gap this kernel knows of (an outage, its own restart with or without its record, the
    row's restart, an overflow) none of the missed events is delivered late, and ONE summary per gap per row says what
    would have buzzed, or that notifications may have been missed when the count cannot be known, and nothing at all
    when nothing could have passed. A flapping link makes one summary: the gap closes only on an answer that found the
    puller caught up."""

    EP = "https://push.example.net/send/desk"           # a browser that replaces a notification by its tag (FCM, Mozilla)
    LAPTOP = "https://push.example.net/send/laptop"     # another such browser
    PHONE = "https://web.push.apple.com/TESTPHONE0000"  # an iPhone's Home Screen app: Apple's push service, WebKit's webpushd

    def setUp(self):
        super().setUp()
        _row()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.subscribe(self.EP)
        self.pushed = []
        self.err = io.StringIO()

    def subscribe(self, *eps):
        km._save_push_subs({ep: {"endpoint": ep, "keys": {"p256dh": "k", "auth": "a"}} for ep in eps})

    def sent(self):
        """Each summary as (device, body, quiet), once per device it went to, in the order sent."""
        return [(ep, a[1], k.get("quiet", False)) for a, k in self.summaries() for ep in k.get("endpoints", ())]

    def comes_forward(self, ep, says_missed=True):
        """A page holding the subscription `ep` comes forward: its GET /push/pending, asked on every load, show and focus.
        `says_missed`: the page says what its device missed (this build's page asks with missed=1; a page loaded from an
        older build does not). Returns the answer."""
        with mock.patch.object(km, "_self_host", return_value="boxa"):
            st, body = _serve("GET", "/push/pending?endpoint=" + quote(ep, safe="") + ("&missed=1" if says_missed else ""))
        self.assertEqual(st, 200)
        return json.loads(body)

    def hub_restarts(self):
        """This kernel restarts: every in-memory record of the life that ended goes, and what is on disk stays."""
        km._PUSH_GAP_TOLD.clear()
        km._PUSH_GAP_TOLD_LOADED[0] = False
        km._PUSH_PULL_CURSORS[0] = None
        km._PUSH_PULL_SEEN.clear()
        km._PUSH_GAPS.clear()
        km._PUSH_PULL_ANSWERED.clear()
        km._PUSH_PULL_LAST_OK.clear()
        km._PUSH_BUZZED.clear()

    def once(self, answer):
        with mock.patch.object(km, "_push_pull_get", return_value=answer), \
             mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: self.pushed.append((a, k))), \
             mock.patch.object(km, "_self_host", return_value="boxa"), contextlib.redirect_stderr(self.err):
            return km._push_pull_once(ROW, dict(km._remotes[ROW]))

    def drop(self):
        """The link drops: a call that gets no answer (named so as not to shadow unittest's fail)."""
        return self.once((0, {"error": "connection refused"}))

    SEQ = [10]

    def turn(self, sid, bell="default", current=True):
        self.SEQ[0] += 1
        e = {"seq": self.SEQ[0], "kind": "turn", "sid": sid, "title": "Romp: s", "body": "Done.", "name": "s",
             "cardId": None, "bell": bell, "turnKey": 1000 + self.SEQ[0], "needsYou": None}
        if not current:
            e["current"] = False
        return e

    def card(self, needs_you=True, bell="default"):
        self.SEQ[0] += 1
        return {"seq": self.SEQ[0], "kind": "card", "sid": WEB, "title": "Romp needs you: web" if needs_you else "Romp: web",
                "body": "Needs you: pick" if needs_you else "Completed: done", "name": "web",
                "cardId": "%s:g%d" % (WEB, self.SEQ[0]), "bell": bell, "turnKey": 0, "needsYou": needs_you}

    def answer(self, events=(), caught=False, boot="77.1", **kw):
        d = {"boot": boot, "next": max([e["seq"] for e in events] + [self.SEQ[0]]), "events": list(events),
             "caughtUp": caught, "bellsOn": False, "cardsOn": False}
        d.update(kw)
        return (200, d)

    def restarted(self, boot, bells=False, cards=False):
        """The row's kernel restarted: its answer to the old cursor resets, and the events of its old life are gone."""
        return (200, {"boot": boot, "next": 1, "events": [], "reset": "restarted", "skipped": 1, "bellsOn": bells,
                      "cardsOn": cards})

    def summaries(self):
        return [(a, k) for a, k in self.pushed if k.get("kind") == "missed"]

    def singles(self):
        return [(a, k) for a, k in self.pushed if k.get("kind") != "missed"]

    def steady(self):
        """A healthy link: an answer that found the puller caught up."""
        self.once(self.answer(caught=True))

    def test_continuous_operation_delivers_each_event_as_it_arrives(self):
        self.steady()
        self.once(self.answer([self.turn(API)], caught=True))
        self.once(self.answer([self.card()], caught=True))
        self.assertEqual([k["kind"] for _, k in self.pushed], ["turn", "card"])

    def test_a_link_outage_is_one_summary_and_nothing_late(self):
        self.steady()
        km._PUSH_PULL_LAST_OK[ROW] = time.time() - 38 * 60  # its last answer, 38 minutes before it is back
        self.drop()
        c = "efefefef-7171-4c4c-8e8e-0000000000c1"
        self.once(self.answer([self.turn(API), self.turn(WEB), self.turn(API), self.turn(c, current=False),
                               self.card(needs_you=True)]))
        self.assertEqual(self.pushed, [], "the backlog it missed is not delivered late")
        self.once(self.answer(caught=True))
        ((args, kw),) = self.pushed
        self.assertEqual(args, ("Romp: missed notifications", "TESTHOST was unreachable for 38 min · 3 sessions finished "
                                "turns, 1 needs you", ""), "a duration, never a clock time in the kernel's zone")
        self.assertEqual(kw, {"kind": "missed", "host": ROW, "quiet": False, "endpoints": (self.EP,)},
                         "sid-less: a tap opens the dashboard; it sounds")
        self.once(self.answer([self.turn(API)], caught=True))
        self.assertEqual([k["kind"] for _, k in self.pushed], ["missed", "turn"], "then each event as it arrives again")

    def test_a_flapping_link_is_one_summary(self):
        self.steady()
        for _ in range(3):
            self.drop()
            self.once(self.answer([self.turn(API), self.card(needs_you=False)]))
        self.drop()
        self.once(self.answer(caught=True))
        self.assertEqual(len(self.summaries()), 1)
        self.assertEqual(self.singles(), [])
        self.assertTrue(self.summaries()[0][0][1].endswith("· 1 session finished a turn, 3 completed"), self.summaries())

    def test_a_gap_where_nothing_would_have_passed_buzzes_nothing(self):
        km._set_notify_all(False)
        km._set_notify_turns(False)
        self.steady()
        self.drop()
        self.once(self.answer([self.turn(API), self.card()]))
        self.once(self.answer(caught=True))
        self.assertEqual(self.pushed, [])
        self.assertIn("nothing it may have missed would have reached the devices here, so no notification", self.err.getvalue())
        self.drop()                                          # an armed session's turn end would have passed: said
        self.once(self.answer([self.turn(API, bell="on")]))
        self.once(self.answer(caught=True))
        self.assertTrue(self.summaries()[0][0][1].endswith("· 1 session finished a turn"))

    def test_the_rows_restart_says_notifications_may_have_been_missed_without_a_number(self):
        self.steady()
        self.drop()
        self.once((200, {"boot": "78.2", "next": 3, "events": [], "reset": "restarted", "skipped": 3, "bellsOn": False}))
        self.once(self.answer(caught=True, boot="78.2"))
        self.assertEqual([a[1] for a, _ in self.summaries()], ["TESTHOST restarted · notifications may have been missed"])
        # nothing could have passed (this master off, no explicit on over there): no buzz
        km._push_gap_seen(self.EP)
        km._set_notify_all(False)
        self.pushed.clear()
        self.once((200, {"boot": "79.3", "next": 1, "events": [], "reset": "restarted", "skipped": 1, "bellsOn": False}))
        self.once(self.answer(caught=True, boot="79.3"))
        self.assertEqual(self.pushed, [])
        self.once((200, {"boot": "80.4", "next": 1, "events": [], "reset": "restarted", "skipped": 1, "bellsOn": True}))
        self.once(self.answer(caught=True, boot="80.4"))
        self.assertEqual(len(self.summaries()), 1, "an explicit on over there could have passed: said")

    def test_an_overflow_during_an_outage_has_no_number(self):
        self.steady()
        km._PUSH_PULL_LAST_OK[ROW] = time.time() - (2 * 3600 + 5 * 60)
        self.drop()
        self.once((200, {"boot": "77.1", "next": 400, "events": [], "reset": "overflow", "skipped": 300, "bellsOn": False}))
        self.once(self.answer(caught=True))
        self.assertEqual([a[1] for a, _ in self.summaries()],
                         ["TESTHOST was unreachable for 2 h 5 min · notifications may have been missed"])

    def test_an_unknown_count_is_said_only_when_something_could_have_passed(self):
        # the review's reproduction: this master on, the turn switch off, no bell set anywhere, and card detection off
        # over there (Task tracking off). Nothing the user's switches and bells allow could have been among the lost
        # events, so a restart of that kernel (every deploy) must not buzz
        km._set_notify_turns(False)
        self.steady()
        self.once(self.restarted("78.2"))
        self.once(self.answer(caught=True, boot="78.2"))
        self.assertEqual(self.pushed, [])
        self.assertIn("nothing it may have missed would have reached the devices here", self.err.getvalue())
        for why, kw, switches in (("card detection on over there, this master on", {"cards": True}, (True, False)),
                                  ("an explicit on over there", {"bells": True}, (False, False)),
                                  ("turn ends by default: this master and turn switch on", {}, (True, True))):
            with self.subTest(why):
                km._push_gap_seen(self.EP)
                km._set_notify_all(switches[0])
                km._set_notify_turns(switches[1])
                self.pushed.clear()
                self.SEQ[0] += 1
                boot = "9%d.1" % self.SEQ[0]
                self.once(self.restarted(boot, **kw))
                self.once(self.answer(caught=True, boot=boot))
                self.assertEqual([a[1] for a, _ in self.summaries()], ["TESTHOST restarted · notifications may have been missed"])
        km._push_gap_seen(self.EP)
        km._set_notify_all(False)
        km._set_notify_turns(True)                           # the turn switch alone passes nothing: turn ends need the master
        self.pushed.clear()
        self.once(self.restarted("99.9", cards=True))
        self.once(self.answer(caught=True, boot="99.9"))
        self.assertEqual(self.pushed, [])

    def test_a_crash_looping_kernel_buzzes_once_and_updates_the_summary_quietly_until_it_is_seen(self):
        self.steady()
        for i in range(3):                                   # three restarts, each surviving one long-poll
            self.once(self.restarted("8%d.1" % i))
            self.once(self.answer(caught=True, boot="8%d.1" % i))
        self.assertEqual([(a[1], k.get("quiet", False)) for a, k in self.summaries()],
                         [("TESTHOST restarted · notifications may have been missed", False),
                          ("TESTHOST restarted 2 times · notifications may have been missed", True),
                          ("TESTHOST restarted 3 times · notifications may have been missed", True)],
                         "one sound; the same notification (its tag) updated without one after")
        self.assertIn("folded into the summary not yet seen, updated without a sound", self.err.getvalue())
        # the user brings a subscribed page forward (its GET /push/pending): seen, so the next restart is news
        self.comes_forward(self.EP)
        self.pushed.clear()
        self.once(self.restarted("84.1"))
        self.once(self.answer(caught=True, boot="84.1"))
        self.assertEqual([(a[1], k.get("quiet", False)) for a, k in self.summaries()],
                         [("TESTHOST restarted · notifications may have been missed", False)])
        # a notification delivered normally, outside any gap, is a steady row again: the next gap sounds too
        self.once(self.answer([self.turn(API)], caught=True, boot="84.1"))
        self.pushed.clear()
        self.once(self.restarted("85.1"))
        self.once(self.answer(caught=True, boot="85.1"))
        self.assertEqual([k.get("quiet", False) for _, k in self.summaries()], [False])

    def test_a_device_coming_forward_re_arms_its_own_summary_only(self):
        # the review of #220: one device's page asking /push/pending (a desktop browser asks on every focus) re-armed the
        # summary for every device, so a crash loop buzzed the phone at every restart while the user sat at the desktop
        self.subscribe(self.EP, self.LAPTOP, self.PHONE)
        self.steady()
        self.once(self.restarted("81.1"))
        self.once(self.answer(caught=True, boot="81.1"))
        self.comes_forward(self.EP)
        self.pushed.clear()
        self.once(self.restarted("82.1"))
        self.once(self.answer(caught=True, boot="82.1"))
        self.assertEqual(self.sent(), [(self.EP, "TESTHOST restarted · notifications may have been missed", False),
                                       (self.LAPTOP, "TESTHOST restarted 2 times · notifications may have been missed", True)],
                         "the device that saw the dashboard hears the next one; the others still hold theirs unseen")
        self.comes_forward("https://push.example.net/send/never-subscribed")   # no such device: re-arms nothing
        self.pushed.clear()
        self.once(self.restarted("83.1"))
        self.once(self.answer(caught=True, boot="83.1"))
        self.assertEqual([(ep, q) for ep, _, q in self.sent()], [(self.EP, True), (self.LAPTOP, True)],
                         "every device holds an unseen summary; the phone's share waits on the hub")

    def test_an_iphone_is_never_sent_an_update_it_would_add_beside_the_summary(self):
        # WebKit's webpushd files every notification under its own fresh id, never under its tag, so on an iPhone an
        # "update" is a second notification lighting the lock screen again (silent, but there). The devices that replace
        # by tag get the quiet update; the iPhone's share is folded on the hub and said by the next summary it is sent
        self.subscribe(self.EP, self.PHONE)
        self.steady()
        for i in range(3):
            self.once(self.restarted("8%d.1" % i))
            self.once(self.answer(caught=True, boot="8%d.1" % i))
        self.assertEqual(self.sent(), [(self.EP, "TESTHOST restarted · notifications may have been missed", False),
                                       (self.PHONE, "TESTHOST restarted · notifications may have been missed", False),
                                       (self.EP, "TESTHOST restarted 2 times · notifications may have been missed", True),
                                       (self.EP, "TESTHOST restarted 3 times · notifications may have been missed", True)])
        self.assertIn("[1 updated without a sound; 1 held for their next summary (they cannot replace a notification in "
                      "place)]", self.err.getvalue())
        # real missed turn ends while the phone's summary is unseen (perhaps swiped away): folded into its count, not dropped
        self.drop()
        self.once(self.answer([self.turn(API), self.turn(WEB)], boot="82.1"))
        self.once(self.answer(caught=True, boot="82.1"))
        self.assertEqual(self.sent()[4:], [(self.EP, "TESTHOST restarted 3 times · 2 sessions finished turns; more may have "
                                            "been missed", True)])
        self.once(self.answer([self.turn(API)], caught=True, boot="82.1"))   # the row delivers normally: steady, re-armed
        self.pushed.clear()
        self.once(self.restarted("83.1"))
        self.once(self.answer(caught=True, boot="83.1"))
        self.assertEqual(self.sent(), [(self.EP, "TESTHOST restarted · notifications may have been missed", False),
                                       (self.PHONE, "TESTHOST restarted 3 times · 2 sessions finished turns; more may have "
                                        "been missed", False)],
                         "the phone's next summary sounds and carries what it was never shown")
        self.comes_forward(self.PHONE)                       # the dashboard is in front of the user on the phone
        self.pushed.clear()
        self.once(self.restarted("84.1"))
        self.once(self.answer(caught=True, boot="84.1"))
        self.assertEqual(self.sent(), [(self.EP, "TESTHOST restarted 2 times · notifications may have been missed", True),
                                       (self.PHONE, "TESTHOST restarted · notifications may have been missed", False)])

    def phone_holds_a_share(self):
        """The desk and the phone each hold an unseen summary; a later outage misses two turn ends: the desk's summary is
        updated in place, and the phone's share is held on the hub (it cannot replace a notification in place)."""
        self.subscribe(self.EP, self.PHONE)
        self.steady()
        self.once(self.restarted("81.1"))
        self.once(self.answer(caught=True, boot="81.1"))
        self.drop()
        self.once(self.answer([self.turn(API), self.turn(WEB)], boot="81.1"))
        self.once(self.answer(caught=True, boot="81.1"))
        self.assertEqual([(ep, q) for ep, _, q in self.sent()], [(self.EP, False), (self.PHONE, False), (self.EP, True)])
        self.pushed.clear()

    OWED = "TESTHOST was unreachable for under a minute · 2 sessions finished turns"

    def test_what_an_iphone_was_never_shown_is_said_on_its_dashboard_when_romp_opens_there(self):
        # the review of #221: the phone's summary swiped away unopened, a later gap's count held for it, and the next normal
        # notification re-armed the phone without carrying the count, which then waited for another gap that might never
        # come, and opening Romp on the phone only cleared it. The count now reaches the user the moment Romp is in front
        # of them on that device, in the dashboard, without a sound
        self.phone_holds_a_share()
        self.once(self.answer([self.turn(API)], caught=True, boot="81.1"))   # the row delivers normally: re-armed, still owed
        self.assertEqual(self.comes_forward(self.EP)["missed"], [], "the desk was shown everything: nothing to say there")
        (m,) = self.comes_forward(self.PHONE)["missed"]
        self.assertEqual((m["host"], m["text"]), (ROW, self.OWED), "said by the dashboard on the phone, in the summary's own words")
        self.assertEqual(self.logged(self.PHONE, [m["id"]])["cleared"], 1, "the page put it in its Log")
        self.assertEqual(self.comes_forward(self.PHONE)["missed"], [], "said once")
        self.pushed.clear()
        self.once(self.restarted("82.1"))
        self.once(self.answer(caught=True, boot="82.1"))
        self.assertEqual(self.sent(), [(self.EP, "TESTHOST restarted · notifications may have been missed", False),
                                       (self.PHONE, "TESTHOST restarted · notifications may have been missed", False)],
                         "the phone's next summary carries only what it has not been told")

    def test_a_page_from_an_older_build_re_arms_its_device_and_keeps_what_it_is_owed(self):
        # a page loaded before this build (open across a deploy until it reloads) asks without missed=1 and would drop the
        # words on the floor: the device is re-armed as before, and its share waits for a page that says it, or its next
        # summary
        self.phone_holds_a_share()
        self.assertEqual(self.comes_forward(self.PHONE, says_missed=False)["missed"], [])
        self.once(self.restarted("82.1"))
        self.once(self.answer(caught=True, boot="82.1"))
        self.assertEqual([(ep, q) for ep, _, q in self.sent()], [(self.EP, True), (self.PHONE, False)],
                         "re-armed: the phone's next summary sounds")
        self.assertEqual(self.sent()[1][1], "TESTHOST restarted · 2 sessions finished turns; more may have been missed",
                         "…and carries what it was owed")

    def test_what_each_device_holds_survives_a_restart_of_this_kernel(self):
        # the review of #221: the record was in memory only, so every restart of this kernel (every deploy) dropped what a
        # device was owed, and made a summary still unseen on a device sound again
        self.phone_holds_a_share()
        p = jd.STATE / "push-gap-told.json"
        self.assertEqual(p.stat().st_mode & 0o777, 0o600, "its keys are endpoints, capability URLs: the subscription store's rule")
        self.hub_restarts()
        self.once(self.answer([self.turn(API)], boot="81.1"))   # this life's first answer: what fired while it was down
        self.once(self.answer(caught=True, boot="81.1"))
        self.assertEqual([(ep, q) for ep, _, q in self.sent()], [(self.EP, True)],
                         "the desk still holds its summary unseen: updated without a sound; the phone's share grows on disk")
        self.hub_restarts()
        self.assertEqual([(m["host"], m["text"]) for m in self.comes_forward(self.PHONE)["missed"]],
                         [(ROW, "boxa restarted · TESTHOST: 2 sessions finished turns")],
                         "two lives later, the phone's dashboard says what it was never shown: both gaps, merged")

    def logged(self, ep, ids):
        """The page holding `ep` put those `missed` entries in its Log: POST /push/missed-logged, its word that it drew them.
        Returns the answer."""
        st, body = _serve("POST", "/push/missed-logged", {"endpoint": ep, "ids": list(ids)})
        self.assertEqual(st, 200, body)
        return json.loads(body)

    def test_what_a_device_is_owed_stays_owed_until_its_page_says_it_drew_the_entry(self):
        # the review of #222: the share was cleared the moment the kernel answered, so an answer lost on the way (the phone
        # leaving the network mid-request, the app suspended before it read the reply) lost the count unsaid. It is cleared
        # now on the page's word that the entry is in its Log (POST /push/missed-logged), and handed again until then
        self.phone_holds_a_share()
        (m,) = self.comes_forward(self.PHONE)["missed"]
        self.assertEqual((m["host"], m["text"]), (ROW, self.OWED))
        self.assertEqual(self.comes_forward(self.PHONE)["missed"], [m],
                         "that answer never reached the page: handed again, under the same id, so a page that did draw it can tell")
        self.hub_restarts()
        self.assertEqual(self.comes_forward(self.PHONE)["missed"], [m], "kept on disk across a restart, id and all")
        self.assertEqual(self.logged(self.EP, [m["id"]])["cleared"], 0, "another device's word clears nothing here")
        self.assertEqual(self.logged(self.PHONE, ["x" * 22])["cleared"], 0, "nor an id this device was never handed")
        self.assertEqual(self.logged(self.PHONE, [m["id"]])["cleared"], 1)
        self.assertEqual(self.comes_forward(self.PHONE)["missed"], [], "said once")
        self.assertNotIn(self.PHONE, (jd.STATE / "push-gap-told.json").read_text(), "nothing left on file for the device")
        for bad in ({"endpoint": self.PHONE, "ids": "x" * 22}, {"endpoint": self.PHONE, "ids": ["no spaces allowed here"]},
                    {"ids": [m["id"]]}):
            self.assertEqual(_serve("POST", "/push/missed-logged", bad)[0], 400, bad)

    def test_a_late_word_never_clears_a_share_that_was_said_and_grew_again_since(self):
        self.phone_holds_a_share()
        (m,) = self.comes_forward(self.PHONE)["missed"]      # handed; the page's word is slow to come
        self.once(self.restarted("82.1"))                    # the phone came forward, so the next gap sounds there, its share inside
        self.once(self.answer(caught=True, boot="82.1"))
        self.assertEqual(self.sent()[-1], (self.PHONE, "TESTHOST restarted · 2 sessions finished turns; more may have been missed", False))
        self.once(self.restarted("83.1"))                    # …and the one after is held for it
        self.once(self.answer(caught=True, boot="83.1"))
        self.assertEqual(self.logged(self.PHONE, [m["id"]])["cleared"], 0, "the word is about a share since said and replaced")
        (n,) = self.comes_forward(self.PHONE)["missed"]
        self.assertNotEqual(n["id"], m["id"])
        self.assertEqual(n["text"], "TESTHOST restarted · notifications may have been missed", "the newer share still owed")

    def test_a_device_that_unsubscribes_takes_its_record_with_it(self):
        self.phone_holds_a_share()
        self.assertIn(self.PHONE, (jd.STATE / "push-gap-told.json").read_text())
        km._del_push_sub(self.PHONE)
        self.assertNotIn(self.PHONE, (jd.STATE / "push-gap-told.json").read_text())
        self.assertEqual(self.comes_forward(self.PHONE)["missed"], [])

    def test_turning_this_device_off_says_what_it_was_owed_in_its_log(self):
        # the review of #222: turning This device off took the record with it, so what the device was still owed was dropped
        # unsaid, and turning it back on (a new subscription, a new endpoint) could not bring it back. The page turning it
        # off is the dashboard in front of the user on that device: its answer hands the share over as /push/pending's does,
        # kept until the page says it drew it
        self.phone_holds_a_share()
        st, body = _serve("POST", "/push/unsubscribe", {"endpoint": self.PHONE, "missed": 1})
        self.assertEqual(st, 200, body)
        (m,) = json.loads(body)["missed"]
        self.assertEqual((m["host"], m["text"]), (ROW, self.OWED))
        self.assertNotIn(self.PHONE, km._push_subs(), "unsubscribed")
        self.assertIn(m["id"], (jd.STATE / "push-gap-told.json").read_text(), "kept until the page's word")
        self.assertEqual(self.logged(self.PHONE, [m["id"]])["cleared"], 1)
        self.assertNotIn(self.PHONE, (jd.STATE / "push-gap-told.json").read_text())
        st, body = _serve("POST", "/push/unsubscribe", {"endpoint": self.EP})   # a page from an older build: forgotten, as before
        self.assertEqual((st, json.loads(body)["missed"]), (200, []))
        self.assertNotIn(self.EP, (jd.STATE / "push-gap-told.json").read_text())

    def test_a_torn_record_is_named_for_what_it_held(self):
        # the review of #222: a torn push-gap-told.json was announced as "the settings it held start over empty until you
        # set them again", but it holds no setting: it holds what each device is owed
        (jd.STATE / "push-gap-told.json").write_text("{torn")
        notices = []
        with mock.patch.object(km, "_sync_notice", side_effect=lambda text, ok=True, kind="sync": notices.append((text, ok, kind))), \
             contextlib.redirect_stderr(io.StringIO()):
            km._push_gap_told_load()
        ((text, ok, kind),) = notices
        self.assertEqual((ok, kind), (False, "refused"))
        self.assertIn("push-gap-told.json could not be parsed and was moved aside to push-gap-told.json.corrupt-", text)
        self.assertIn("what each device was still owed by the missed-notification summaries is lost, and the next such "
                      "summary sounds on every device", text)
        self.assertNotIn("settings", text)

    def test_the_fan_out_reaches_only_the_devices_named(self):
        self.subscribe(self.EP, self.LAPTOP, self.PHONE)
        seen, done = [], threading.Event()

        def fake_send(sub, payload):
            seen.append(sub["endpoint"])
            if sub["endpoint"] == self.LAPTOP:
                done.set()
            return True

        with mock.patch.object(km, "_push_send_one", side_effect=fake_send), \
             mock.patch.object(km, "_push_crypto", return_value=True):
            km._push_notify("Romp: missed notifications", "TESTHOST restarted", "", kind="missed", host=ROW,
                            endpoints=(self.LAPTOP,))
            self.assertTrue(done.wait(5), "the send thread ran")
        self.assertEqual(seen, [self.LAPTOP], "the store is read in key order, so an unfiltered fan-out reaches desk first")

    def test_an_origin_that_does_not_say_cards_on_counts_as_detecting_cards(self):
        # the safe side: an origin built before cardsOn (protocol 2's first build) may detect cards, so with this master on
        # its restart could have lost one, and the summary says so; cardsOn false says nothing (the test above)
        km._set_notify_turns(False)
        self.steady()
        st, r = self.restarted("78.2")
        r.pop("cardsOn")
        self.once((st, r))
        st, a = self.answer(caught=True, boot="78.2")
        a.pop("cardsOn")
        self.once((st, a))
        self.assertEqual([x[1] for x, _ in self.summaries()], ["TESTHOST restarted · notifications may have been missed"])

    def test_the_cap_note_and_the_summary_wear_different_tags(self):
        cap = km._push_payload("Romp: more notifications", "b", "", kind="more", host=ROW)
        gap = km._push_payload("Romp: missed notifications", "b", "", kind="missed", host=ROW)
        self.assertNotEqual(cap["tag"], gap["tag"], "the cap's note never replaces a summary on the lock screen")
        evs = [dict(HubGate.CARD, seq=i, bell="on", cardId="%s:g%d" % (WEB, i)) for i in range(1, 19)]
        with mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: self.pushed.append((a, k))), \
             contextlib.redirect_stderr(io.StringIO()):
            km._push_pull_gate(ROW, evs)
        self.assertEqual(self.pushed[-1][1], {"kind": "more", "host": ROW})

    def test_durations_in_the_users_terms(self):
        for secs, words in ((20, "under a minute"), (38 * 60 + 10, "38 min"), (3600, "1 h"), (2 * 3600 + 5 * 60, "2 h 5 min"),
                            (86400 + 7200, "1 day"), (3 * 86400, "3 days")):
            self.assertEqual(km._span_words(secs), words, secs)

    def test_this_kernels_restart_with_its_record_summarizes_what_fired_meanwhile(self):
        km._push_pull_cursor_set(ROW, "77.1:10")             # where the previous life had got to
        self.once(self.answer([self.turn(API)]))             # this life's first answer: the backlog
        self.once(self.answer(caught=True))
        self.assertEqual([a[1] for a, _ in self.pushed], ["boxa restarted · TESTHOST: 1 session finished a turn"])
        # a restart during which nothing fired: the first answer finds the cursor at the newest, and nothing is said
        km._PUSH_PULL_ANSWERED.clear()
        self.pushed.clear()
        self.once(self.answer(caught=True))
        self.assertEqual(self.pushed, [])

    def test_this_kernels_restart_without_its_record_has_no_number_and_a_new_row_is_no_gap(self):
        self.once((200, {"boot": "77.1", "next": 5, "events": [], "reset": "no-cursor", "skipped": 5, "bellsOn": False}))
        self.once(self.answer(caught=True))
        self.assertEqual(self.pushed, [], "a row new to this kernel missed nothing")
        km._PUSH_PULL_CURSOR_LOST[0] = True                  # the record of where it had got to could not be read
        km._PUSH_PULL_CURSORS[0] = {}
        km._PUSH_PULL_ANSWERED.clear()
        self.once((200, {"boot": "77.1", "next": 5, "events": [], "reset": "no-cursor", "skipped": 5, "bellsOn": False}))
        self.once(self.answer(caught=True))
        self.assertEqual([a[1] for a, _ in self.pushed], ["boxa restarted · notifications from TESTHOST may have been missed"])

    def test_the_answer_that_closes_a_gap_delivers_its_own_events_after_the_summary(self):
        self.steady()
        self.drop()
        self.once(self.answer([self.turn(API)]))
        self.once(self.answer([self.turn(WEB)], caught=True))   # fired while it listened: live
        self.assertEqual([k["kind"] for _, k in self.pushed], ["missed", "turn"])
        self.assertEqual(self.pushed[1][0][2], ROW + ":" + WEB)

    def test_an_older_row_closes_a_gap_on_an_empty_handed_answer(self):
        self.steady()
        self.drop()
        self.once((200, {"boot": "77.1", "next": 12, "events": [self.turn(API)]}))   # protocol 1: no caughtUp
        self.assertEqual(self.pushed, [])
        self.once((200, {"boot": "77.1", "next": 12, "events": []}))
        self.assertEqual(len(self.summaries()), 1)


class _SyncThread:
    """Stands in for threading.Thread inside the kernel module only: _push_notify's send runs before the call returns."""

    def __init__(self, target=None, daemon=None, **kw):
        self.target = target

    def start(self):
        self.target()


class QuietFollowUps(_Base):
    """A quiet push REPLACES a notification already shown, without a sound: the card that yields the buzz to the turn end
    that already sounded (#937), the update of a gap's summary. A device that cannot replace one in place (Apple's:
    _push_tag_replaces) would show it BESIDE the old one, a second notification for news the device already had (the
    review of #221). It goes there only when it carries the needs-you badge, which a closed app learns only from a push."""

    DESK = "https://push.example.net/send/desk"
    PHONE = "https://web.push.apple.com/TESTPHONE0000"

    def setUp(self):
        super().setUp()
        _row()
        km._save_push_subs({ep: {"endpoint": ep, "keys": {"p256dh": "k", "auth": "a"}} for ep in (self.DESK, self.PHONE)})

    def sends(self, **kw):
        seen = []
        with mock.patch.object(km, "threading", mock.Mock(Thread=_SyncThread)), \
             mock.patch.object(km, "_push_send_one", side_effect=lambda sub, payload: seen.append(sub["endpoint"]) or True), \
             mock.patch.object(km, "_push_crypto", return_value=True), contextlib.redirect_stderr(io.StringIO()):
            km._push_notify("Romp needs you: web", "Needs you: pick", "%s:%s" % (ROW, WEB), kind="card", host=ROW, **kw)
        return seen

    def test_a_quiet_push_without_a_badge_goes_only_where_it_replaces(self):
        self.assertEqual(self.sends(quiet=True), [self.DESK], "the pulled card after its turn end: nothing for the iPhone")
        self.assertEqual(self.sends(quiet=True, badge=2), [self.DESK, self.PHONE], "the local card carries the count: sent")
        self.assertEqual(self.sends(), [self.DESK, self.PHONE], "a push with a sound goes everywhere")

    def test_the_pulled_card_after_its_turn_end_is_quiet_and_carries_no_badge(self):
        turn = dict(HubGate.TURN, sid=WEB, name="web", turnKey=77)
        card = dict(HubGate.CARD, seq=3, turnKey=77)
        pushed = []
        km._set_notify_all(True)
        km._set_notify_turns(True)
        with mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: pushed.append(k)), \
             contextlib.redirect_stderr(io.StringIO()):
            km._push_pull_gate(ROW, [turn, card])
        self.assertEqual([(k["kind"], k.get("quiet", False), "badge" in k) for k in pushed],
                         [("turn", False, False), ("card", True, False)])


class LocalRestart(_Base):
    """The same rule for this kernel's OWN sessions across its restart: _turn_notify_tick's memo is written to disk, and
    the first tick of a life says the human turn ends the previous life never saw in one summary, never one by one."""

    def setUp(self):
        super().setUp()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.alive = [{"sid": s, "name": n, "path": _transcript(s, "Done.")} for s, n in ((WEB, "web"), (API, "api"))]
        self.pushed = []
        self.err = io.StringIO()

    def tick(self):
        with mock.patch.object(km, "_alive_sessions", return_value=self.alive), \
             mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: self.pushed.append((a, k))), \
             mock.patch.object(km, "_push_forward"), mock.patch.object(km, "_self_host", return_value="boxa"), \
             contextlib.redirect_stderr(self.err):
            return km._turn_notify_tick(time.time(), {WEB: {}, API: {}})

    def restart(self):
        km._TURN_BOOTED[0] = False
        km._TURN_PREV.clear()
        km._TURN_MEMO_DISK[0] = None

    def test_turn_ends_while_this_kernel_was_down_are_one_summary_never_late_buzzes(self):
        _stamp_stop(WEB, 1000)
        _stamp_stop(API, 1000)
        self.restart()
        self.tick()                                          # the previous life: its memo lands on disk
        self.assertEqual(json.loads((jd.STATE / "turn-notify-memo.json").read_text()), {WEB: 1000, API: 1000})
        self.restart()                                       # down …
        _stamp_stop(WEB, 1005)                               # … the human's turn ended
        _stamp_stop(API, 1007, opener="injected", human_at=1004)   # a human turn, then a notification's turn, ended
        fired = self.tick()                                  # … and up again
        self.assertEqual(fired, [], "nothing delivered one by one, late")
        self.assertEqual(self.pushed, [(("Romp: missed notifications", "boxa restarted · 2 sessions finished turns", ""),
                                        {"kind": "missed"})])
        self.assertEqual(self.tick(), [])
        self.assertEqual(len(self.pushed), 1, "said once")
        _stamp_stop(WEB, 1010)
        self.assertEqual(len(self.tick()), 1, "then each turn end as it happens")

    def test_only_what_would_have_buzzed_is_counted_and_nothing_is_no_summary(self):
        _stamp_stop(WEB, 1000)
        _stamp_stop(API, 1000)
        self.restart()
        self.tick()
        km._set_notify_turns(False)                          # turn ends no longer buzz by default …
        self.restart()
        _stamp_stop(WEB, 1005)
        _stamp_stop(API, 1005, opener="injected")            # (and this one was never the human's)
        self.tick()
        self.assertEqual(self.pushed, [])
        km._set_notify_session(WEB, True)                    # … but an armed session's would have
        self.restart()
        _stamp_stop(WEB, 1006)
        self.tick()
        self.assertEqual([a[1] for a, _ in self.pushed], ["boxa restarted · 1 session finished a turn"])

    def test_no_record_from_a_previous_life_is_a_silent_baseline_said_in_the_log(self):
        _stamp_stop(WEB, 1005)
        self.restart()
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.pushed, [])
        self.assertIn("no record of the previous kernel life's turn ends", self.err.getvalue())


class Transition(_Base):
    """Mixed versions: one mechanism per pair, and no event reaches the devices twice."""

    def test_the_legacy_forward_skips_peers_that_pull(self):
        _row("boxa", push_pull=0)
        _row("boxb", push_pull=1)
        calls, done = [], threading.Event()

        def fake(r, method, path, body=None, timeout=8):
            calls.append(r["host"]); done.set()
            return 200, {}
        with mock.patch.object(km, "_peer_call", side_effect=fake):
            km._push_forward([{"title": "Romp: web", "body": "Done.", "sid": WEB, "kind": "turn"}])
            done.wait(5)
            time.sleep(0.2)
        self.assertEqual(calls, ["boxa"])

    def relay(self, origin):
        km._set_notify_all(True)
        body = {"origin": origin, "events": [{"title": "Romp: web", "body": "Done.", "sid": WEB, "kind": "turn"}]}
        km._set_notify_turns(True)
        with mock.patch.object(km, "_push_notify") as pn, contextlib.redirect_stderr(io.StringIO()) as err:
            st, raw = _serve("POST", "/push/relay", body)
        return st, json.loads(raw), pn.call_count, err.getvalue()

    def test_a_relay_from_a_kernel_this_one_pulls_is_ignored(self):
        _row(ROW, self_name="boxa")                          # the row's key and the name that kernel declares differ
        st, ans, n, err = self.relay("boxa")
        self.assertEqual((st, ans, n), (200, {"ok": True, "mirrored": 0, "pulled": True}, 0))
        self.assertIn("ignoring events relayed by 'boxa'", err)
        self.assertEqual(self.relay("boxa")[3], "", "said once")

    def test_a_relay_from_a_kernel_that_does_not_serve_the_pull_still_lands(self):
        _row(ROW, push_pull=0)
        st, ans, n, _ = self.relay(ROW)
        self.assertEqual((ans["mirrored"], n), (1, 1))

    def test_an_event_offered_both_ways_reaches_the_devices_once(self):
        # stage one: the hub has not yet seen the origin advertise; the origin relays, the hub does not pull
        _row(ROW, push_pull=0)
        self.assertFalse(km._push_pull_eligible(dict(km._remotes[ROW])))
        self.assertEqual(self.relay(ROW)[2], 1)
        # stage two: the hub's poll learned the capability: the relay of the next event is ignored, the pull delivers it
        km._remotes[ROW]["push_pull"] = 1
        self.assertEqual(self.relay(ROW)[2], 0)
        km._set_notify_turns(True)
        _stamp_stop(WEB, 1001)
        ev = {"seq": 1, "kind": "turn", "sid": WEB, "title": "Romp: web", "body": "Done.", "name": "web", "cardId": None,
              "bell": "default", "turnKey": 1001}
        with mock.patch.object(km, "_push_pull_get", return_value=(200, {"boot": "5.5", "next": 1, "events": [ev]})), \
             mock.patch.object(km, "_push_notify") as pn, contextlib.redirect_stderr(io.StringIO()):
            km._push_pull_once(ROW, dict(km._remotes[ROW]))
            km._push_pull_once(ROW, dict(km._remotes[ROW]))   # the same answer twice (a retried call): still once
        self.assertEqual(pn.call_count, 1)

    def test_the_version_poll_lifts_the_capability(self):
        answers = {}

        class V(BaseHTTPRequestHandler):
            def do_GET(self):
                b = json.dumps(answers["v"]).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), V)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = {"host": ROW, "local_port": srv.server_address[1], "token": "tok"}
            answers["v"] = {"kernel_sha": "abc1234", "pushPull": 1}
            self.assertEqual(km._poll_remote_version(r)["pushPull"], 1)
            answers["v"] = {"kernel_sha": "abc1234"}         # an older build
            self.assertEqual(km._poll_remote_version(r)["pushPull"], 0)
            answers["v"] = {"kernel_sha": "abc1234", "pushPull": True}
            self.assertEqual(km._poll_remote_version(r)["pushPull"], 0, "a bool is not a protocol number")
        finally:
            srv.shutdown(); srv.server_close()

    def test_the_supervisor_stores_it_and_reconciles_the_pullers(self):
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('r["push_pull"] = (rver or {}).get("pushPull") or 0', src)
        self.assertIn("_push_pull_reconcile()", src)

    def test_pullers_start_for_eligible_rows_only(self):
        _row("boxa")                                         # trusted, advertising: pulled
        _row("boxb", push_pull=0)                            # an older build
        _row("boxc", trust="directed")                       # below trusted
        _row("boxd", token="")                               # no credentials
        started = []
        with mock.patch.object(km, "_push_pull_loop", side_effect=lambda name: started.append(name)):
            km._push_pull_reconcile()
            for t in list(km._push_pullers.values()):
                t.join(2)
        self.assertEqual(started, ["boxa"])
        self.assertTrue(km._remote_public(km._remotes["boxa"])["pushPulled"])
        self.assertFalse(km._remote_public(km._remotes["boxb"])["pushPulled"])


class BellsFollowTheDashboard(_Base):
    """The merged dashboard paints a linked kernel's bells against the HUB's switches (bell-follow.ts) and sends a click on
    one as given; the origin stores it exactly as sent, so an explicit mute is recorded whatever the origin's own master
    says, and the hub's gate resolves no override against its own switches by the rules its own sessions follow. A click
    on the origin's own dashboard keeps the delete-if-default rule."""

    def ws(self, msg):
        sent = []
        client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}
        with mock.patch.object(km, "_mark_views_dirty"):
            km.Handler._dispatch_ws(None, msg, client)
        return sent

    def test_a_remote_click_is_stored_as_given_whatever_this_master_says(self):
        self.assertFalse(km._notify_all_on(), "the origin's master never set: off")
        self.assertEqual(self.ws({"type": "cardNotify", "itemId": WEB + ":g1", "sid": WEB, "value": False, "asGiven": True}), [])
        self.assertIs(km._notify_cards().get(WEB + ":g1"), False, "a mute matching this master is stored all the same")
        self.ws({"type": "setSessionFlag", "id": API, "flag": "notify", "value": False, "asGiven": True})
        self.assertIs(km._session_flag_raw(API, "notify"), False)
        self.ws({"type": "cardNotify", "itemId": WEB + ":g1", "sid": WEB, "value": None, "asGiven": True})
        self.assertNotIn(WEB + ":g1", km._notify_cards(), "a null as given is the bell returned to its default on purpose")
        self.ws({"type": "setSessionFlag", "id": API, "flag": "notify", "value": None, "asGiven": True})
        self.assertIsNone(km._session_flag_raw(API, "notify"))

    def test_a_local_card_click_keeps_delete_if_default_and_a_session_click_is_stored_as_clicked(self):
        km._set_notify_card(WEB + ":g1", False, WEB)          # matches the master (off): no override, as before
        self.assertNotIn(WEB + ":g1", km._notify_cards())
        km._set_notify_session(API, False)                    # the session bell: every click is the user's explicit choice
        self.assertIs(km._session_flag_raw(API, "notify"), False, "stored though it matches the master (2026-10-11)")
        km._set_notify_card(WEB + ":g1", True, WEB)
        self.assertIs(km._notify_cards().get(WEB + ":g1"), True)
        self.ws({"type": "cardNotify", "itemId": WEB + ":g1", "sid": WEB, "value": None})
        self.assertNotIn(WEB + ":g1", km._notify_cards(),
                         "without asGiven a null keeps its old meaning (absent, so false), judged against this master: deleted")

    def test_the_users_scenario_a_mute_stops_the_buzz_and_an_arm_buzzes(self):
        # the hub's master on, the origin's never set. The merged dashboard paints the card on (no override, the hub's
        # master); a click to off arrives as given and is stored; the card's next entry is no candidate, so nothing
        # reaches the hub. A click to on is stored as an arm, and the hub buzzes for it.
        self.ws({"type": "setSessionFlag", "id": WEB, "flag": "notify", "value": False, "asGiven": True})
        self.block()
        self.assertEqual(self.ring(), [], "muted on the origin: the event never leaves it")
        km._NOTIFY_PREV[0] = None
        self.ws({"type": "setSessionFlag", "id": WEB, "flag": "notify", "value": True, "asGiven": True})
        self.block()
        (ev,) = self.ring()
        self.assertEqual(ev["bell"], "on")
        _row()
        with mock.patch.object(km, "_notify_all_on", return_value=True), mock.patch.object(km, "_push_notify") as pn:
            km._push_pull_gate(ROW, [dict(ev)])
        self.assertEqual(pn.call_count, 1, "the hub buzzes")

    def test_the_hub_gate_follows_the_rules_its_own_sessions_follow(self):
        _row()
        card = {"seq": 1, "kind": "card", "sid": WEB, "title": "t", "body": "b", "name": "web", "cardId": WEB + ":g1",
                "bell": "on", "turnKey": 0}
        turn = {"seq": 2, "kind": "turn", "sid": API, "title": "t", "body": "b", "name": "api", "cardId": None,
                "bell": "on", "turnKey": 1001}
        with mock.patch.object(km, "_push_notify") as pn, contextlib.redirect_stderr(io.StringIO()):
            km._push_pull_gate(ROW, [card, turn, dict(card, seq=3, bell="default", cardId=WEB + ":g2")])
        self.assertEqual([(c[1]["kind"], c[1]["card_id"]) for c in pn.call_args_list], [("card", WEB + ":g1"), ("turn", "")],
                         "master off: an armed card buzzes and so does an armed session's turn end (an explicit on beats the "
                         "defaults, 2026-10-11), and a card with no override follows the master")

    def test_a_refused_session_bell_click_says_the_bells_own_setting(self):
        # the menus paint a session bell from its own setting and the switches (bell-follow.ts sessionBell), so a refusal
        # repaints from the same field the frames carry
        km._set_notify_session(API, True)
        with mock.patch.object(km, "_session_flags_proved", side_effect=km._StateUnreadable(jd.STATE / "session-flags.json", "EIO")):
            sent = self.ws({"type": "setSessionFlag", "id": API, "flag": "notify", "value": False})
            st, raw = _serve("POST", "/flag", {"id": API, "flag": "notify", "value": False})
        (fr,) = sent
        self.assertEqual((fr["type"], fr["flag"], fr["notifyRaw"]), ("settingRefused", "notify", True))
        self.assertEqual((st, json.loads(raw)["notifyRaw"]), (200, True))

    def test_the_payloads_carry_the_override_alone(self):
        import inspect
        self.assertIn('"notifyRaw": _session_flag_raw(sid, "notify")', inspect.getsource(km.build_session))
        self.assertIn('_a["notifyRaw"] = _push_explicit_bell(str(_a.get("sid") or ""), _a["itemId"], _ncards)', inspect.getsource(km.build_feed))
        self.assertIn('"notifyRaw": _session_flag_raw(sid, "notify")})', inspect.getsource(km.build_timeline))
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertEqual(src.count('"notifyRaw": m.get("notifyRaw")}'), 2, "both chat tails carry it")
        key = json.loads(km._chat_view_key({"notify": True, "notifyRaw": None}, 1))
        self.assertNotEqual(key, json.loads(km._chat_view_key({"notify": True, "notifyRaw": True}, 1)),
                            "an override-only change (the effective value unchanged) still moves the view, so a tail goes")

    def test_the_switches_frame_reaches_every_ready_pane_at_ready_and_on_a_flip(self):
        self.assertEqual(km._notify_switches_frame(), {"type": "notifySwitches", "all": False, "turns": False})
        sent = []
        ready = {"app": "feed", "ready": True, "send": None}
        early = {"app": "chat", "send": None}
        with km._clients_lock:
            saved = list(km._clients)
            km._clients[:] = [ready, early]
        try:
            with mock.patch.object(km, "_client_send", side_effect=lambda c, raw, key=None: sent.append((c["app"], json.loads(raw)))):
                st, raw = _serve("POST", "/notify-all", {"on": True})
        finally:
            with km._clients_lock:
                km._clients[:] = saved
        self.assertEqual(st, 200)
        self.assertIn(("feed", {"type": "notifySwitches", "all": True, "turns": False}), sent)
        self.assertNotIn("chat", [a for a, _ in sent], "a socket still connecting gets it at its own ready, after its pushes")
        import inspect
        src = inspect.getsource(km.Handler._dispatch_ws)
        self.assertLess(src.index("_send_caps(client, views_seq=views_seq)"), src.index("_send_notify_switches(client)"),
                        "at ready, after caps")


if __name__ == "__main__":
    unittest.main()
