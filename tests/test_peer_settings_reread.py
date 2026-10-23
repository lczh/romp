#!/usr/bin/env python3
"""A linked machine's settings are re-read the moment it says it handled one (the gear-truth review, 2026-10-23).

The gear compares the machines through this kernel's copy of each peer's settings, which the tunnel supervisor lifted off the
peer's /version every 15 s. After a click applied on a peer the panel's next read still held the peer's old value, so the row
went back to the mixed dash and the hover's "click again to turn it on" posted off a second time. Pinned here, the kernel half:
- a setting that reached a kernel over another kernel's relay (client kind "relay") is answered with settingAck once the lane
  has dispatched it; a page's own socket gets none; the acknowledged types are federation.ts KERNEL_SETTING's plus the pin;
- GET /tunnels?reread=<host> re-reads that up peer's /version and lifts its settings onto its row before the listing is built;
- two reads of one peer that land out of order keep the settings of the one that BEGAN later (the supervisor takes its number
  before its read), and the number is never saved to remotes.json.
The served end-to-end is tests/test_settings_gear_truth_served.py. Synthetic host names, loopback only, no live state.
"""
import json
import os
import re
import tempfile
import unittest

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_peer_reread", os.path.join(BIN, "romp-kernel"))

KERNEL_SRC = open(os.path.join(BIN, "romp-kernel")).read()
FEDERATION_SRC = open(os.path.join(ROOT, "ui", "webview", "federation.ts")).read()


def _sink():
    sent = []
    return sent, (lambda s: sent.append(json.loads(s)))


class Acknowledged(unittest.TestCase):
    def test_the_acknowledged_types_are_federations_kernel_settings_and_the_pin(self):
        m = re.search(r"const KERNEL_SETTING = new Set\(\[(.*?)\]\);", FEDERATION_SRC, re.S)
        self.assertIsNotNone(m, "federation.ts still declares KERNEL_SETTING")
        ts = set(re.findall(r'"(set[A-Za-z]+)"', m.group(1)))
        self.assertEqual(set(km._GEAR_SETTING_TYPES), ts, "the kernel acknowledges exactly what federation fans out as a kernel setting")
        self.assertEqual(km._ACKED_SETTING_TYPES, km._GEAR_SETTING_TYPES | {"setSettingPin"})

    def test_a_relayed_setting_is_acknowledged_with_its_type_and_stamp(self):
        sent, send = _sink()
        self.assertTrue(km._ack_setting({"kind": "relay", "send": send}, {"type": "setConserve", "enabled": True, "gt": 1700000000123}))
        self.assertEqual(sent, [{"type": "settingAck", "msgType": "setConserve", "gt": 1700000000123}])
        sent.clear()
        self.assertTrue(km._ack_setting({"kind": "relay", "send": send}, {"type": "setSettingPin", "store": "auto-nudge", "pinned": True}))
        self.assertEqual(sent, [{"type": "settingAck", "msgType": "setSettingPin"}], "no stamp, none echoed")

    def test_a_pages_own_socket_and_any_other_message_get_none(self):
        sent, send = _sink()
        self.assertFalse(km._ack_setting({"kind": "page", "send": send}, {"type": "setConserve", "enabled": True, "gt": 1}))
        self.assertFalse(km._ack_setting({"kind": "relay", "send": send}, {"type": "activeTab", "id": "x"}))
        self.assertFalse(km._ack_setting({"kind": "relay", "send": send}, None))
        self.assertEqual(sent, [])
        self.assertTrue(km._ack_setting({"kind": "relay", "send": send}, {"type": "setConserve", "enabled": True, "gt": True}))
        self.assertEqual(sent, [{"type": "settingAck", "msgType": "setConserve"}], "a boolean is no stamp")

    def test_the_lane_acknowledges_after_the_dispatch(self):
        order = []
        sent, send = _sink()

        def dispatch(msg, client):
            order.append(("dispatched", msg["type"], list(sent)))

        msg = {"type": "setThinkingSummaries", "enabled": False, "gt": 5}
        km._dispatch_then_ack(dispatch, msg, {"kind": "relay", "send": send})
        self.assertEqual(order, [("dispatched", "setThinkingSummaries", [])], "nothing went out before the setting was handled")
        self.assertEqual(sent, [{"type": "settingAck", "msgType": "setThinkingSummaries", "gt": 5}])
        self.assertIn("functools.partial(_dispatch_then_ack, self._dispatch_ws, msg, client)", KERNEL_SRC, "every lane op goes through it")


class Reread(unittest.TestCase):
    HOST = "TESTHOSTB"

    def setUp(self):
        self._poll = km._poll_remote_version
        with km._remotes_lock:
            self._had = km._remotes.get(self.HOST)
            km._remotes[self.HOST] = {"host": self.HOST, "status": "up", "local_port": 1, "kernel_port": 2, "token": "t",
                                      "settings": {"thinkingSummaries": True}, "settingsGt": {"thinking-summaries": 2000},
                                      "settingsPinned": {}, "auto_nudge": True, "kernel_sha": "abc1234"}

    def tearDown(self):
        km._poll_remote_version = self._poll
        with km._remotes_lock:
            if self._had is None:
                km._remotes.pop(self.HOST, None)
            else:
                km._remotes[self.HOST] = self._had

    def _peer_answers(self, settings, gts, an=False):
        asked = []

        def poll(r):
            asked.append(r.get("host"))
            return {"sha": "abc1234", "autoNudge": an, "settings": settings, "settingsGt": gts, "settingsPinned": {}}
        km._poll_remote_version = poll
        return asked

    def test_a_reread_lifts_the_peers_settings_onto_its_row(self):
        asked = self._peer_answers({"thinkingSummaries": False}, {"thinking-summaries": 3000})
        self.assertTrue(km._reread_peer_settings(self.HOST))
        self.assertEqual(asked, [self.HOST])
        row = km._remotes[self.HOST]
        self.assertEqual((row["settings"], row["settingsGt"], row["auto_nudge"]), ({"thinkingSummaries": False}, {"thinking-summaries": 3000}, False))
        pub = km._remote_public(row)
        self.assertEqual(pub["settings"], {"thinkingSummaries": False}, "the /tunnels row the gear reads carries it")

    def test_a_row_that_is_not_up_or_not_attached_is_left_alone(self):
        asked = self._peer_answers({"thinkingSummaries": False}, {"thinking-summaries": 3000})
        km._remotes[self.HOST]["status"] = "down"
        self.assertFalse(km._reread_peer_settings(self.HOST))
        self.assertFalse(km._reread_peer_settings("TESTHOSTZ"))
        self.assertEqual(asked, [], "no read of a machine that is not up")
        self.assertEqual(km._remotes[self.HOST]["settings"], {"thinkingSummaries": True})

    def test_a_failed_read_leaves_the_row(self):
        km._poll_remote_version = lambda r: None
        self.assertFalse(km._reread_peer_settings(self.HOST))
        self.assertEqual(km._remotes[self.HOST]["settings"], {"thinkingSummaries": True})

    def test_the_read_that_began_later_wins_whichever_lands_last(self):
        row = km._remotes[self.HOST]
        early, late = next(km._SETTINGS_READS), next(km._SETTINGS_READS)
        self.assertTrue(km._lift_peer_settings(row, {"settings": {"thinkingSummaries": False}, "settingsGt": {}}, late))
        self.assertFalse(km._lift_peer_settings(row, {"settings": {"thinkingSummaries": True}, "settingsGt": {}}, early),
                         "a supervisor pass whose read began before the re-read does not put the old value back")
        self.assertEqual(row["settings"], {"thinkingSummaries": False})
        # the supervisor takes its number BEFORE its read, and lifts through the one function
        i_seq, i_read = KERNEL_SRC.index("rseq = next(_SETTINGS_READS)"), KERNEL_SRC.index("rver = _poll_remote_version(r) if up else None")
        self.assertLess(i_seq, i_read)
        self.assertIn("_lift_peer_settings(r, rver, rseq)", KERNEL_SRC)

    def test_the_read_number_is_never_saved(self):
        km._remotes[self.HOST]["_settings_seq"] = 41
        self.assertIn("_settings_seq", km._NOT_SAVED)
        rows = [r for r in km._remotes_rows_for_save() if r.get("host") == self.HOST]
        self.assertNotIn("_settings_seq", rows[0], "it moves every pass: saved, remotes.json would be rewritten every pass")

    def test_tunnels_takes_the_reread_ask(self):
        self.assertEqual(km._reread_asked({"reread": [self.HOST]}), self.HOST)
        self.assertEqual(km._reread_asked({}), "")
        self.assertEqual(km._reread_asked({"reread": [""]}), "")
        route = KERNEL_SRC[KERNEL_SRC.index('if p == "/tunnels":'):KERNEL_SRC.index('if p == "/tunnels/pairs":')]
        self.assertIn("_reread_peer_settings(_reread_asked(q))", route, "the listing is built after the re-read")


if __name__ == "__main__":
    unittest.main()
