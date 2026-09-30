#!/usr/bin/env python3
"""The opener policy on the REAL dashboard, both directions (2026-09-27). Handler._send declares
Cross-Origin-Opener-Policy: same-origin-allow-popups on every response but a state-root pane's own files; the comment in
_security_headers, which writes it, says why that value and not same-origin, and why those files carry none. A hermetic
kernel serves the dashboard on 127.0.0.1, a secure context where browsers enforce the policy, with two data panes
(plans/panes-as-data.md): a state-root pane (pane:notes, a page and a callback page under STATE/panes/notes/, framed on
this origin) and a URL pane (a page a second origin serves, framed sandboxed with allow-popups). That second origin also
serves a page that posts back to window.opener, a page that opens windows, and a sign-in provider that sends its popup
back. A Chromium driver reads:
  1. a popup the URL pane opens: window.open returns a handle, the popup has an opener, and the pane hears its post;
  2. a popup the state-root pane opens onto the second origin: the handle stays open, the popup has an opener, and the
     pane hears its post. Under same-origin, 1 got a null handle and 2 a closed one, and neither pane heard its popup;
  3. a page on the second origin that opens the dashboard's shell gets a closed handle and the shell gets no opener: the
     isolation the policy exists for (with no policy the handle stays open and the shell's opener is that page);
  4. the RESIDUAL the comment in _security_headers discloses: a tab the URL pane opens onto one of the dashboard's pages
     (a /file text) shows no page, not the file, because the popup inherits the pane's sandbox and a browser shows the
     response of a sandboxed tab or popup (a top-level navigation) only under the unsafe-none policy. The same /file
     opened by the state-root pane shows the file with its handle open, so the refusal is the sandbox's, not the
     route's; and the URL pane's tab onto a state-root pane's own page, which carries no policy (6), shows it. This leg
     is the disclosure's witness: it turns red if the refusal ever lifts (the frame's sandbox gains
     allow-popups-to-escape-sandbox, or the header goes), and the disclosure is then to be revisited. Engines differ in
     what they show instead, and the leg asserts only the absence of the file: Chromium shows its error page and closes
     the handle; Firefox shows a blocked page and WebKit an empty one, each leaving the handle open (LAB_ENGINE=firefox
     or webkit runs the driver in that engine);
  5. the timeline page's external-link fallback opens a tab on the second origin with no opener: the direction the
     policy leaves to each call (a popup this origin opens keeps its opener unless the call passes noopener);
  6. a sign-in round trip: the state-root pane's popup goes to the second origin, which sends it back to a callback page
     the pane serves beside its own page on this origin, once by a script redirect and once by a 302; the callback
     posts to its opener and the pane hears it. A state-root pane's own files carry no opener policy for this: a
     callback that declared one would start a browsing context group of its own on arrival (its policy does not match
     the second origin's page it replaces), leaving it no opener and the pane a closed handle;
  7. the cost of 6, and its bound: a page on the second origin that opens a state-root pane's page keeps a live handle
     to it (the pane shim has no window listener; the page's own are its author's), and when it sends that window on
     to the shell, the shell's policy severs the handle and the shell has no opener.
Runs when ROMP_SERVED_TESTS_REQUIRE=1 (CI); skips where no Chromium is installed. LAB_ENGINE picks another Playwright
engine for a manual run; CI runs Chromium. Synthetic fixtures only."""
import http.server
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
SID = "11111111-2222-4333-8444-000000000351"
MARK = "a synthetic line from the notes-api log"   # the /file text's one line: read in the tab that shows it

import sys
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

REQUIRE = os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(cwd, pairs):
    out, parent, t = [], None, 1_700_000_000
    for i in range(pairs):
        u = "11111111-2222-4333-8444-0000000e%04x" % i
        a = "11111111-2222-4333-8444-0000000f%04x" % i
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": SID, "cwd": cwd,
                    "message": {"role": "user", "content": "please tidy the notes index (part %d)" % (i + 1)}})
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": SID, "cwd": cwd,
                    "message": {"id": "msg_popup_%04d" % i, "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                                "content": [{"type": "text", "text": "Index %d tidied." % (i + 1)}], "stop_reason": "end_turn"}})
        parent = a
    return "\n".join(json.dumps(r) for r in out) + "\n"


# Each pane page, and the second origin's opener page: records every message it hears, and opens a window on request,
# keeping the handle.
_PROBE = ("window.__labMsgs=[];window.addEventListener('message',function(e){window.__labMsgs.push(e.data);});"
          "window.__labOpen=function(u){var w=window.open(u);window.__labW=w;return w!==null;};")
NOTES_PAGE = "<!doctype html><html><head><meta charset=utf-8></head><body><h1>Notes</h1><script>" + _PROBE + "</script></body></html>"
DOCS_PAGE = "<!doctype html><html><head><meta charset=utf-8></head><body><h1>Docs</h1><script>" + _PROBE + "</script></body></html>"
OPENER_PAGE = "<!doctype html><html><head><meta charset=utf-8></head><body><h1>Elsewhere</h1><script>" + _PROBE + "</script></body></html>"
# the popup a pane opens: it posts back to its opener, as a sign-in or a picker does, and names in its title whether it
# had one to post to
BACK_PAGE = ("<!doctype html><html><head><meta charset=utf-8></head><body><script>var had=!!window.opener;"
             "try{if(window.opener)window.opener.postMessage({lab:'back'},'*');}catch(e){}"
             "document.title='back:'+had;</script></body></html>")
# a sign-in provider's page that sends the popup back to the callback named in its ?next=, as a provider does once the
# user has signed in
SIGNIN_PAGE = ("<!doctype html><html><head><meta charset=utf-8></head><body><script>"
               "location.replace(new URLSearchParams(location.search).get('next'));</script></body></html>")


class _Elsewhere(http.server.BaseHTTPRequestHandler):
    """The second origin: the URL pane's page, the popup that posts back, a page that opens windows, and a sign-in
    provider that sends its popup back to ?next=, by a script (/signin.html) or by a 302 (/redirect)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/redirect":
            self.send_response(302); self.send_header("Location", parse_qs(query)["next"][0]); self.send_header("Content-Length", "0"); self.end_headers()
            return
        page = {"/docs/": DOCS_PAGE, "/back.html": BACK_PAGE, "/opener.html": OPENER_PAGE, "/signin.html": SIGNIN_PAGE}.get(path)
        if page is None:
            self.send_response(404); self.end_headers(); return
        body = page.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const engines = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await engines[process.env.LAB_ENGINE || "chromium"].launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const context = await browser.newContext({ viewport: { width: 1500, height: 900 } });
const page = await context.newPage();
const out = {};
const die = async (why) => { fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n"); await browser.close(); process.exit(0); };
const frameEnding = async (suffix) => { let fr = null; for (let i = 0; i < 150 && !fr; i++) { fr = page.frames().find((f) => f.url().split("?")[0].endsWith(suffix)); if (!fr) await page.waitForTimeout(100); } return fr; };
// Open `url` from `frame` (its page's __labOpen) and follow the window it opens: whether window.open returned a handle,
// the popup's title and address once it has loaded, the text it shows, and whether the opening page heard its post.
// `until`, when given, is the path the popup is to end on: the popup is followed there first (arrived), for a popup that
// is sent on from the page it opened on.
const openFrom = async (frame, url, until) => {
  await frame.evaluate(() => { window.__labMsgs = []; window.__labW = null; });   // this open's messages and handle only
  const next = context.waitForEvent("page", { timeout: 15000 }).catch(() => null);
  const got = await frame.evaluate((u) => window.__labOpen(u), url);
  const pop = await next;
  const r = { got, opened: !!pop };
  if (pop) {
    if (until) r.arrived = await pop.waitForURL((a) => a.pathname === until, { timeout: 15000 }).then(() => true).catch(() => false);
    await pop.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
    r.url = pop.url();
    r.title = await pop.title().catch(() => null);
    r.text = await pop.evaluate(() => (document.body ? document.body.innerText : "")).catch(() => null);
  }
  r.heard = await frame.waitForFunction(() => (window.__labMsgs || []).some((m) => m && m.lab === "back"), null, { timeout: 5000 }).then(() => true).catch(() => false);
  r.closed = await frame.evaluate(() => (window.__labW ? window.__labW.closed : null));
  r.pop = pop;
  return r;
};
const record = (r) => { const { pop, ...rest } = r; return rest; };

await page.goto(cfg.url);
const nf = await frameEnding("/pane/notes/");
if (!nf) await die("no /pane/notes/ frame");
const df = await frameEnding("/docs/");
if (!df) await die("no /docs/ frame");
for (const f of [nf, df]) {
  const ok = await f.waitForFunction(() => typeof window.__labOpen === "function", null, { timeout: 15000 }).then(() => true).catch(() => false);
  if (!ok) await die("a pane page never ran its probe: " + f.url());
}
out.secure = await page.evaluate(() => window.isSecureContext);
out.docsSandbox = await page.evaluate(() => document.getElementById("f-docs").getAttribute("sandbox"));

// ---- 1. the URL pane's popup ----
const u = await openFrom(df, cfg.elsewhere + "/back.html");
out.urlPane = record(u);
if (u.pop) await u.pop.close();

// ---- 2. the state-root pane's popup onto the second origin ----
const n = await openFrom(nf, cfg.elsewhere + "/back.html");
out.notesPane = record(n);
if (n.pop) await n.pop.close();

// ---- 3. a page on the second origin opens the shell ----
const fp = await context.newPage();
await fp.goto(cfg.elsewhere + "/opener.html");
await fp.waitForFunction(() => typeof window.__labOpen === "function", null, { timeout: 15000 }).catch(() => {});
const next = context.waitForEvent("page", { timeout: 15000 }).catch(() => null);
const gotShell = await fp.evaluate((url) => window.__labOpen(url), cfg.api + "/");
const shell = await next;
out.foreign = { got: gotShell, opened: !!shell };
if (shell) {
  await shell.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
  out.foreign.shellOpener = await shell.evaluate(() => (window.opener === null ? "none" : "a window")).catch((e) => "unread: " + e);
  out.foreign.shellBooted = await shell.waitForSelector("#chat-pane", { timeout: 15000 }).then(() => true).catch(() => false);
}
out.foreign.closed = await fp.waitForFunction(() => !!window.__labW && window.__labW.closed, null, { timeout: 5000 }).then(() => true).catch(() => false);
if (shell) await shell.close();
await fp.close();

// ---- 4. a kernel page opened from each pane ----
const res = await openFrom(df, cfg.api + cfg.fileQuery);
out.residual = record(res);
if (res.pop) await res.pop.close();
const own = await openFrom(nf, cfg.api + cfg.fileQuery);
out.ownTab = record(own);
if (own.pop) await own.pop.close();
const toPane = await openFrom(df, cfg.api + "/pane/notes/back.html");
out.urlPaneToPane = record(toPane);
if (toPane.pop) await toPane.pop.close();

// ---- 5. the timeline's external link: a tab romp's own code opens onto another origin ----
const tp = await context.newPage();
await tp.goto(cfg.api + "/timeline");
const hook = await tp.waitForFunction(() => typeof window.__rompTimelineOpenExternal === "function", null, { timeout: 15000 }).then(() => true).catch(() => false);
out.timeline = { hook };
if (hook) {
  const nextTab = context.waitForEvent("page", { timeout: 15000 }).catch(() => null);
  await tp.evaluate((u) => window.__rompTimelineOpenExternal(u), cfg.elsewhere + "/back.html");
  const tab = await nextTab;
  out.timeline.opened = !!tab;
  if (tab) {
    await tab.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
    out.timeline.title = await tab.title().catch(() => null);
    await tab.close();
  }
}
await tp.close();

// ---- 6. a sign-in round trip back to a callback page the state-root pane serves on this origin ----
const callback = encodeURIComponent(cfg.api + "/pane/notes/back.html");
const byScript = await openFrom(nf, cfg.elsewhere + "/signin.html?next=" + callback, "/pane/notes/back.html");
out.signInScript = record(byScript);
if (byScript.pop) await byScript.pop.close();
const by302 = await openFrom(nf, cfg.elsewhere + "/redirect?next=" + callback, "/pane/notes/back.html");
out.signInRedirect = record(by302);
if (by302.pop) await by302.pop.close();

// ---- 7. a page on the second origin opens the state-root pane's page, then sends that window on to the shell ----
const fq = await context.newPage();
await fq.goto(cfg.elsewhere + "/opener.html");
await fq.waitForFunction(() => typeof window.__labOpen === "function", null, { timeout: 15000 }).catch(() => {});
const nextPane = context.waitForEvent("page", { timeout: 15000 }).catch(() => null);
const gotPane = await fq.evaluate((url) => window.__labOpen(url), cfg.api + "/pane/notes/back.html");
const pw = await nextPane;
out.foreignPane = { got: gotPane, opened: !!pw };
if (pw) {
  await pw.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
  out.foreignPane.title = await pw.title().catch(() => null);
  out.foreignPane.heard = await fq.waitForFunction(() => (window.__labMsgs || []).some((m) => m && m.lab === "back"), null, { timeout: 5000 }).then(() => true).catch(() => false);
  out.foreignPane.openBefore = await fq.evaluate(() => !!window.__labW && !window.__labW.closed);
  out.foreignPane.sent = await fq.evaluate((u) => { try { window.__labW.location.href = u; return true; } catch (e) { return String(e); } }, cfg.api + "/");
  out.foreignPane.shellBooted = await pw.waitForSelector("#chat-pane", { timeout: 15000 }).then(() => true).catch(() => false);
  out.foreignPane.shellOpener = await pw.evaluate(() => (window.opener === null ? "none" : "a window")).catch((e) => "unread: " + e);
  out.foreignPane.closedAfter = await fq.waitForFunction(() => !!window.__labW && window.__labW.closed, null, { timeout: 5000 }).then(() => true).catch(() => false);
  await pw.close();
}
await fq.close();

fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedPanePopupOpener(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            if REQUIRE:
                raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the extension deps are absent")
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="pane-popup-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(state, "names"), exist_ok=True)
        os.makedirs(os.path.join(state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, SID + ".jsonl").write_text(_transcript(cwd, 3))
        cls.note = os.path.join(cwd, "server.log")
        Path(cls.note).write_text(MARK + "\n")
        # the second origin: the URL pane's page, the popup that posts back, the page that opens the shell
        cls.elsewhere_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Elsewhere)
        cls.elsewhere = "http://127.0.0.1:%d" % cls.elsewhere_srv.server_address[1]
        threading.Thread(target=cls.elsewhere_srv.serve_forever, daemon=True).start()
        panes = Path(state, "panes"); (panes / "notes").mkdir(parents=True)
        (panes / "notes.json").write_text(json.dumps({"id": "notes", "title": "Notes", "source": "pane:notes", "on": True, "experimental": False, "protocol": "romp"}))
        (panes / "docs.json").write_text(json.dumps({"id": "docs", "title": "Docs", "source": cls.elsewhere + "/docs/", "on": True, "experimental": False, "protocol": "none"}))
        (panes / "notes" / "index.html").write_text(NOTES_PAGE)
        (panes / "notes" / "back.html").write_text(BACK_PAGE)   # the pane's own callback page, beside its index
        cls.port = _free_port()
        cls.token = "testtok-panepopup"
        cls.env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=cls.env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            try:
                os.kill(k.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            k.wait()
        s = getattr(cls, "elsewhere_srv", None)
        if s:
            s.shutdown()
            s.server_close()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    _res = None

    def _drive(self):
        from urllib.parse import quote
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (self.port, self.token), "api": "http://127.0.0.1:%d" % self.port,
                       "elsewhere": self.elsewhere, "fileQuery": "/file?path=" + quote(self.note, safe="") + "&sid=" + SID}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode()
            self.fail("driver timed out; partial output:\n%s" % so[-3000:])
        if p.returncode == 3:
            if REQUIRE:
                self.fail("ROMP_SERVED_TESTS_REQUIRE=1 but no browser launched: " + p.stderr[-500:])
            raise unittest.SkipTest("no playwright browser on this box: the served leg needs one")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertNotIn("died", r, "driver aborted early: %r" % r)
        self.assertTrue(r["secure"], "the dashboard on 127.0.0.1 is a secure context, where the browser enforces the policy")
        return r

    def _result(self):
        # one drive per class (the driver runs every leg in one browser session); a failure, a skip or a launch error is
        # cached and re-raised by every test, so the record is measured once and each leg's verdict stands on its own
        cls = type(self)
        if cls._res is None:
            try:
                cls._res = ("ok", self._drive())
            except Exception as e:
                cls._res = ("err", e)
        kind, val = cls._res
        if kind == "err":
            raise val
        return val

    def test_a_url_panes_popup_gets_a_handle_and_the_pane_hears_it_post_back(self):
        self.assertEqual(self._result()["docsSandbox"], "allow-scripts allow-forms allow-popups", "the frame as the kernel renders it")
        r = self._result()["urlPane"]
        self.assertTrue(r["opened"], "the popup opened: %r" % r)
        self.assertTrue(r["got"], "window.open returned a handle (null under same-origin): %r" % r)
        self.assertEqual(r["title"], "back:true", "the popup has an opener to post to: %r" % r)
        self.assertTrue(r["heard"], "the pane heard its popup's post: %r" % r)

    def test_a_state_root_panes_popup_on_another_origin_keeps_its_handle_and_posts_back(self):
        r = self._result()["notesPane"]
        self.assertTrue(r["opened"] and r["got"], "the popup opened and window.open returned a handle: %r" % r)
        self.assertIs(r["closed"], False, "the handle is still open after the popup loaded (closed under same-origin): %r" % r)
        self.assertEqual(r["title"], "back:true", "the popup has an opener to post to: %r" % r)
        self.assertTrue(r["heard"], "the pane heard its popup's post: %r" % r)

    def test_a_page_on_another_origin_that_opens_the_shell_gets_a_closed_handle(self):
        r = self._result()["foreign"]
        self.assertTrue(r["opened"] and r["shellBooted"], "the shell opened and booted: %r" % r)
        self.assertTrue(r["closed"], "the opening page's handle to the shell is closed: %r" % r)
        self.assertEqual(r["shellOpener"], "none", "the shell has no opener: %r" % r)

    def test_the_timelines_external_link_opens_a_tab_with_no_opener(self):
        # the other direction the policy leaves open: a popup this origin opens keeps its opener unless the call says
        # noopener, so the timeline boot's fallback says it (tests/test_window_open_opener.py holds every call to it)
        r = self._result()["timeline"]
        self.assertTrue(r["hook"] and r["opened"], "the /timeline page opened the link in a tab: %r" % r)
        self.assertEqual(r["title"], "back:false", "the tab has no opener to post to or navigate: %r" % r)

    def test_residual_a_url_panes_popup_onto_a_kernel_page_shows_no_page(self):
        # the witness of the residual the comment in _security_headers discloses; the state-root pane's tab is the
        # control
        own = self._result()["ownTab"]
        self.assertTrue(own["opened"] and own["got"], "the state-root pane opened the /file tab: %r" % own)
        self.assertIs(own["closed"], False, "a tab on this origin keeps its handle: %r" % own)
        self.assertIn(MARK, own["text"] or "", "the /file tab shows the file when a pane on this origin opens it: %r" % own)
        r = self._result()["residual"]
        self.assertTrue(r["opened"], "the URL pane's popup opened: %r" % r)
        self.assertNotIn(MARK, r["text"] or "", "the URL pane's tab onto /file shows no file (the disclosed residual): %r" % r)
        self.assertEqual(self._result()["docsSandbox"], "allow-scripts allow-forms allow-popups", "the frame as the kernel renders it")
        # the refusal's bound: a state-root pane's own page carries no policy, so the URL pane's tab onto it shows it
        p = self._result()["urlPaneToPane"]
        self.assertEqual(p["title"], "back:true", "the URL pane's tab onto a state-root pane's page shows it, with an opener: %r" % p)
        self.assertTrue(p["heard"], "the URL pane heard that page's post: %r" % p)

    def _round_trip(self, key):
        r = self._result()[key]
        self.assertTrue(r["opened"] and r["got"], "the popup opened and window.open returned a handle: %r" % r)
        self.assertTrue(r["arrived"], "the provider sent the popup back to the pane's callback page: %r" % r)
        self.assertEqual(r["title"], "back:true", "the callback page has an opener to post to: %r" % r)
        self.assertTrue(r["heard"], "the pane heard its callback's post: %r" % r)
        self.assertIs(r["closed"], False, "the pane's handle to its popup is still open: %r" % r)

    def test_a_sign_in_round_trip_back_to_the_state_root_panes_own_callback_reaches_the_pane(self):
        self._round_trip("signInScript")

    def test_a_sign_in_round_trip_whose_provider_answers_with_a_302_reaches_the_pane_too(self):
        self._round_trip("signInRedirect")

    def test_a_page_on_another_origin_keeps_its_handle_to_a_panes_page_but_not_once_it_sends_it_to_the_shell(self):
        r = self._result()["foreignPane"]
        self.assertTrue(r["opened"] and r["got"], "the pane's page opened: %r" % r)
        # the disclosed cost of 6: a state-root pane's own page carries no policy, so a page on another origin that
        # opens it keeps its handle, and the page has it as its opener
        self.assertEqual(r["title"], "back:true", "the pane's page has the opening page as its opener: %r" % r)
        self.assertTrue(r["heard"] and r["openBefore"], "the opening page's handle is live and hears the page: %r" % r)
        # its bound: the shell declares the policy, so sending that window on to it starts a group of its own
        self.assertIs(r["sent"], True, "the opening page sent the window on to the shell: %r" % r)
        self.assertTrue(r["shellBooted"], "the shell loaded in that window and booted: %r" % r)
        self.assertEqual(r["shellOpener"], "none", "the shell has no opener: %r" % r)
        self.assertTrue(r["closedAfter"], "the opening page's handle is closed once the shell loads: %r" % r)


if __name__ == "__main__":
    unittest.main()
