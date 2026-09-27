#!/usr/bin/env python3
"""The browser timeline's window listener hands a message on only from the senders windowSender hears (2026-09-27).

The kernel's /timeline page runs the inline boot (kernel.py _TIMELINE_BOOT), the browser twin of the VS Code webview's
timeline-main.ts. Its one window message listener used to hand every message to the frame listener, so any window
that could post to the page (a page on another origin that opened /timeline, a sandboxed frame) could draw lanes and
drive the panel's host bridges. The boot now spells ui/webview/window-sender.ts's rule inline as heardSender in front
of the frame listener: this page's own dispatch (the shim's and federation.js's frames, a MessageEvent with no source
and no origin), this window, its parent (the shell), or a window on this origin. The check runs outside the page's
performance collector's wrapper, so a foreign message is neither drawn nor counted.

ui/webview/timeline-boot-senders.test.ts pins the boot to windowSender itself over a grid of receiving windows, senders
and origins, with and without a real collector. The rows here hold the same rule in the kernel's own suite over named
senders, on a page on a loopback origin, on a page whose own origin is opaque (no window is a peer of it) and on a page
with no location. Synthetic only: loopback and example origins.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads (they resolve their state root at import time; only pytest runs conftest's floor,
# and a bare unittest or script run would otherwise write REAL state). No session connects here; session-hosts is
# written off anyway, as for every root a test mints.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _f:
    _f.write("off")
km = load_source("romp_kernel_timeline_boot_senders", os.path.join(BIN, "romp-kernel"))

# The boot, run in node: stand-ins for the few browser names it touches (HTMLElement for the DOM shims, the host
# bridge acquireVsCodeApi, the shell as window.parent, the shell's own parent as window.top), a fake panel, and the one
# window message listener it registers. ROMP_TEST_OWN is the page's own origin ("-" for a page with no location);
# ROMP_TEST_PERF=1 publishes a performance collector on window.__rompPerf first, as federation.js does on the kernel's
# page, in the shape of perf-telemetry.ts's wrapFrameHandler, counting every message it is handed.
_BOOT_HARNESS = r"""
'use strict';
const ORIGIN = 'http://127.0.0.1:7777', OTHER = 'https://elsewhere.example';
const OWN = process.env.ROMP_TEST_OWN;
const LISTENERS = [], UPDATES = [], COUNTED = [];
global.HTMLElement = function () {};
global.window = global;
const GRAND = { name: "the shell's own parent" };
const SHELL = { name: 'shell', parent: GRAND };
global.parent = SHELL;
global.top = GRAND;
const OPENER = { name: 'a page on another origin that opened /timeline' };
global.opener = OPENER;
global.location = OWN === '-' ? undefined : { origin: OWN };
global.acquireVsCodeApi = () => ({ postMessage() {} });
global.addEventListener = (t, f) => { if (t === 'message') LISTENERS.push(f); };
if (process.env.ROMP_TEST_PERF === '1') {
  global.__rompPerf = { wrapFrameHandler: (h) => (e) => { COUNTED.push(e && e.data && e.data.data && e.data.data.from); return h(e); } };
}
BOOT
window.__rompConnectTimeline({ update: (d) => UPDATES.push(d.from) });
const SENDERS = {
  dispatch: [null, ''],          // the shim's and federation.js's frames: a MessageEvent with no source and no origin
  fedDirect: [undefined, undefined],   // federation.js's direct call with a bare event
  self: [window, ORIGIN],
  embedder: [SHELL, ORIGIN],      // the shell, this frame's parent
  peer: [{}, ORIGIN],             // another window on this origin
  opener: [OPENER, OTHER],        // the page on another origin that opened /timeline: this window's opener
  top: [GRAND, OTHER],            // the top window, the shell's own parent on another origin: not this frame's parent
  stranger: [{}, OTHER],          // a window on another origin this one does not know
  sandboxed: [{}, 'null'],        // a sandboxed frame
  sandboxedSibling: [{ parent: SHELL }, 'null'],   // a sandboxed frame beside this one in the shell
  sandboxedChild: [{ parent: window }, 'null'],    // a sandboxed frame inside this one
  overlappingOrigin: [{}, ORIGIN + '0'],           // another port whose text begins with this origin
  noPort: [{}, 'http://127.0.0.1'],                // this origin's scheme and host on another port (the default)
  otherPort: [{}, 'http://127.0.0.1:7778'],        // this origin's scheme and host on another port
  sourcelessElsewhere: [null, OTHER],
  sourcelessOpaque: [null, 'null'],                // a sandboxed frame gone after it posted
};
Object.keys(SENDERS).forEach((k) => {
  const e = { data: { type: 'data', data: { from: k } } };
  if (SENDERS[k][0] !== undefined) { e.source = SENDERS[k][0]; e.origin = SENDERS[k][1]; }
  LISTENERS.forEach((f) => f(e));
});
process.stdout.write(JSON.stringify({ listeners: LISTENERS.length, updates: UPDATES, counted: COUNTED,
                                      perf: typeof global.__rompPerf === 'object' }));
"""
_OWN_ORIGIN = "http://127.0.0.1:7777"   # the harness's ORIGIN
_HEARD = ["dispatch", "fedDirect", "self", "embedder", "peer"]
_HEARD_NO_PEER = ["dispatch", "fedDirect", "self", "embedder"]


def _run_boot(own, perf):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    fx = tempfile.mkdtemp()
    try:
        path = os.path.join(fx, "boot.js")
        with open(path, "w") as f:
            f.write(_BOOT_HARNESS.replace("BOOT", km._TIMELINE_BOOT))
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, ROMP_TEST_OWN=own, ROMP_TEST_PERF="1" if perf else "0"))
    finally:
        shutil.rmtree(fx, ignore_errors=True)
    if r.returncode != 0:
        raise AssertionError("node failed:\n" + r.stderr[-2000:])
    got = json.loads(r.stdout)
    if got["perf"] != perf:
        raise AssertionError("the harness published a collector: %r, asked for %r" % (got["perf"], perf))
    return got


class TimelineBootSenders(unittest.TestCase):
    """The browser timeline's window listener hands a message on only from the senders windowSender hears
    (ui/webview/window-sender.ts): this page's own dispatch, this window, its parent (the shell), a window on this
    origin. A message from any other sender (a page on another origin, the top window above the shell, a sandboxed
    frame) is dropped there, before the page's performance collector sees it: neither drawn nor counted."""

    def test_a_data_frame_is_drawn_from_every_heard_sender_and_from_no_other(self):
        got = _run_boot(_OWN_ORIGIN, perf=False)
        self.assertEqual(got["listeners"], 1, "the boot registers one window message listener")
        self.assertEqual(got["updates"], _HEARD,
                         "drawn once from each heard sender, never from a page on another origin (the one that opened "
                         "this page included), the top window above the shell, a sandboxed frame (beside or inside this "
                         "one, or gone after it posted), an origin that overlaps this one's text or differs only in its "
                         "port, or a sourceless post that names another origin")

    def test_with_the_pages_collector_a_foreign_message_is_neither_drawn_nor_counted(self):
        # the collector wraps the frame listener; the sender check runs outside it, so the collector counts exactly
        # the frames the page draws
        got = _run_boot(_OWN_ORIGIN, perf=True)
        self.assertEqual(got["listeners"], 1)
        self.assertEqual(got["updates"], _HEARD)
        self.assertEqual(got["counted"], _HEARD, "the collector counted only the heard senders' frames")

    def test_on_a_page_whose_own_origin_is_opaque_no_window_is_a_peer(self):
        # a sandboxed page's origin is "null", and so is every sandboxed frame's: the same text is no shared origin
        for perf in (False, True):
            with self.subTest(perf=perf):
                got = _run_boot("null", perf=perf)
                self.assertEqual(got["updates"], _HEARD_NO_PEER, "a sandboxed frame (origin \"null\") is no peer")
                self.assertEqual(got["counted"], _HEARD_NO_PEER if perf else [])

    def test_on_a_page_with_no_location_no_window_is_a_peer(self):
        for perf in (False, True):
            with self.subTest(perf=perf):
                got = _run_boot("-", perf=perf)
                self.assertEqual(got["updates"], _HEARD_NO_PEER)
                self.assertEqual(got["counted"], _HEARD_NO_PEER if perf else [])

    def test_source_the_check_heads_the_window_listener_outside_the_collector(self):
        boot = km._TIMELINE_BOOT
        self.assertIn('window.addEventListener("message",function(e){if(!heardSender(e))return;frameListener(e);});', boot)
        self.assertEqual(boot.count("function heardSender(e){"), 1)
        self.assertEqual(boot.count("heardSender("), 2, "defined once, called once: by the window listener")
        self.assertLess(boot.index("function heardSender(e){"), boot.index('window.addEventListener("message",'))
        # the registry path takes the listener as it is: only federation.js calls it, with a MessageEvent it built
        self.assertIn("window.__rompFed.onFrame(frameListener);", boot)


if __name__ == "__main__":
    unittest.main()
