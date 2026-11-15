"""A tab nobody is watching goes to rest, in a real browser (2026-11-15; the kernel half is tests/test_unwatched_tabs.py).

A hermetic kernel over EIGHT synthetic sessions, the real /chat page served from a copy of the built bundle, driven by
Playwright, one browser. The page loads (the diet: the selected tab whole, the rest filled by its idle prefetch), then every
session appends a turn the way a working session does, several times. What the roads pin, on the page and on the wire:

  (1) the dial: the chat pane's socket says it can take a loaded tab back (rest=1);
  (2) the rest: every tab but the one on screen turns into a skeleton tab whose title says it is not up to date, on a status
      frame that carries `rest`, and from then on the page is sent no chat frame for those tabs however often their
      transcripts move, the kernel builds none of them (/perf), and the page's idle prefetch asks for none of them again;
  (3) the watched tab: its deltas keep coming and its newest turn is on screen;
  (4) the click: the loader is up at once, the page asks for the tab whole, the transcript that lands holds the newest turn,
      and the tab is a loaded tab again;
  (5) the tab just left: it goes to rest at its next move.

Synthetic only (placeholder ids, invented text)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.dist_copy import copy_dist  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab  # noqa: E402  the lab kernel's environment
from test_live_paused_window_browser import _free_port  # noqa: E402

N_SESSIONS = 8
TURNS_EACH = 20
GROUPS = ("web", "api", "tests")


def sid_of(i):
    return "%08d-1212-2222-3333-%012d" % (i, i)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def _turn(i, k, prev, t0, sid, mark=""):
    u, a = "%08d-1212-5555-6666-%012d" % (i, 2 * k), "%08d-1212-5555-6666-%012d" % (i, 2 * k + 1)
    return a, [{"type": "user", "uuid": u, "parentUuid": prev, "timestamp": _iso(t0), "sessionId": sid,
                "message": {"role": "user", "content": "turn %d: a question about the notes api" % k}},
               {"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(t0 + 1), "sessionId": sid,
                "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                            "content": [{"type": "text", "text": "%sanswer %d: the notes api keeps its shape." % (mark, k)}]}}]


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(cfg.launch || {}); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const context = await browser.newContext({ viewport: { width: 1300, height: 760 } });
const page = await context.newPage();
const pageEvents = [];
page.on("pageerror", (e) => pageEvents.push("pageerror:" + String(e).slice(0, 300)));
// every dial's URL, every frame the page receives and every message it sends its kernel, from before the page's scripts run
await page.addInitScript(() => {
  window.__dials = []; const W = window.WebSocket;
  window.WebSocket = function (url, protos) { window.__dials.push(String(url)); return protos === undefined ? new W(url) : new W(url, protos); };
  window.WebSocket.prototype = W.prototype; window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1; window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
  window.__frames = []; window.__sent = []; const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { try { const m = JSON.parse(d); if (m && m.type) window.__sent.push({ type: m.type, id: m.id, why: m.why }); } catch (e) { /* not a frame */ } return send.call(this, d); };
  window.addEventListener("message", (e) => { const m = e.data; if (!m || !m.type) return;
    window.__frames.push({ type: m.type, id: m.id || null, rest: m.rest === true }); });
});
// the transcripts, appended to as a working session appends: a prompt and a reply, chained to the last record
const S = cfg.sessions;
const iso = (t) => new Date(t * 1000).toISOString().replace(/\.\d+Z$/, ".000Z");
const pad = (n, w) => String(n).padStart(w, "0");
function append(s, mark) {
  const k = s.k++;
  const u = pad(s.i, 8) + "-1212-5555-6666-" + pad(2 * k, 12), a = pad(s.i, 8) + "-1212-5555-6666-" + pad(2 * k + 1, 12);
  s.t += 2;
  const recs = [{ type: "user", uuid: u, parentUuid: s.prev, timestamp: iso(s.t), sessionId: s.sid, message: { role: "user", content: "turn " + k + ": a question about the notes api" } },
    { type: "assistant", uuid: a, parentUuid: u, timestamp: iso(s.t + 1), sessionId: s.sid, message: { role: "assistant", model: "claude-fable-5-1", stop_reason: "end_turn",
      content: [{ type: "text", text: mark + " answer " + k + ": the notes api keeps its shape." }] } }];
  s.prev = a;
  fs.appendFileSync(s.path, recs.map((r) => JSON.stringify(r) + "\n").join(""));
}
const appendAll = (mark) => { for (const s of S) append(s, mark); };
const tabs = () => page.evaluate(() => Array.from(document.querySelectorAll("#tabs .tab")).map((t) => ({ id: t.dataset.id, skeleton: t.classList.contains("tab-skeleton"), title: t.title })));
const perf = async () => { const j = await (await fetch(cfg.perf)).json(); const c = j.builds.chat; return { built: c.built, bg: c.bg_built, active: c.active_built, rested: c.rested, coldSkipped: c.coldSkipped }; };
const timedOut = (e) => { if (e && e.name === "TimeoutError") return false; throw e; };
const R = {};
await page.goto(cfg.chat);
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab").length >= n, S.length, { timeout: 60000 });
// the first load settles: no placeholder, and no skeleton the page has never held (the idle prefetch has had every tab once; a
// tab whose build could not be cached on a booting kernel is at rest already, which is a state of this test's own subject)
R.filled = await page.waitForFunction(() => document.querySelectorAll("#tabs .tab-placeholder").length === 0
  && !Array.from(document.querySelectorAll("#tabs .tab-skeleton")).some((t) => t.title.includes("Not loaded yet")), null, { timeout: 120000 }).then(() => true, timedOut);
R.dial = await page.evaluate(() => window.__dials.find((d) => d.includes("app=chat")) || null);
R.watched = await page.evaluate(() => document.querySelector("#tabs .tab.active")?.dataset.id || null);
// ROAD 2: every session appends; every tab but the watched one goes to rest
appendAll("restmark-1");
R.allRested = await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab-skeleton").length >= n, S.length - 1, { timeout: 60000 }).then(() => true, timedOut);
R.tabsAtRest = await tabs();
const sent0 = await page.evaluate(() => window.__sent.length), fr0 = await page.evaluate(() => window.__frames.length);
R.perf0 = await perf();
for (let i = 2; i <= 4; i++) {   // loop-ok: three more rounds of appends, each given a moment to reach the kernel's next cycle
  appendAll("restmark-" + i);
  await page.waitForFunction((m) => document.querySelector("#content")?.textContent.includes(m), "restmark-" + i, { timeout: 30000 }).catch(timedOut);
}
// ROAD 3: the watched tab shows its newest turn
R.watchedShowsNewest = await page.evaluate(() => document.querySelector("#content")?.textContent.includes("restmark-4"));
R.perf1 = await perf();
R.asksWhileAtRest = await page.evaluate((n) => window.__sent.slice(n).filter((m) => m.type === "needFull"), sent0);
const fr = await page.evaluate((n) => window.__frames.slice(n), fr0);
R.restFramesTotal = await page.evaluate(() => window.__frames.filter((f) => f.type === "status" && f.rest).length);
R.chatFramesForRested = fr.filter((f) => (f.type === "session" || f.type === "chatTail") && f.id !== R.watched).length;
R.tailsForWatched = fr.filter((f) => f.type === "chatTail" && f.id === R.watched).length;
R.statusFramesForRested = fr.filter((f) => f.type === "status" && f.id !== R.watched).length;
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/unwatched-rest.png" });
// ROAD 4: the click on a tab at rest
const target = (R.tabsAtRest.filter((t) => t.skeleton)[2] || R.tabsAtRest.find((t) => t.skeleton) || {}).id || null;
R.target = target;
if (target) {
  await page.click(`#tabs .tab[data-id="${target}"]`);
  R.loaderAtClick = await page.evaluate(() => { const w = document.getElementById("tab-loading"); return w ? w.textContent : null; });
  R.freshAfterClick = await page.waitForFunction(() => !document.getElementById("tab-loading") && document.querySelector("#content")?.textContent.includes("restmark-4"), null, { timeout: 60000 }).then(() => true, timedOut);
  R.asksForTarget = await page.evaluate((id) => window.__sent.filter((m) => m.type === "needFull" && m.id === id).map((m) => m.why), target);
  R.targetAfter = (await tabs()).find((t) => t.id === target);
  R.perf2 = await perf();
  // ROAD 5: the tab just left goes to rest at its next move; the one opened stays loaded
  appendAll("restmark-5");
  R.leftTabRested = await page.waitForFunction((id) => document.querySelector(`#tabs .tab[data-id="${id}"]`)?.classList.contains("tab-skeleton"), R.watched, { timeout: 60000 }).then(() => true, timedOut);
  R.leftTab = (await tabs()).find((t) => t.id === R.watched);
  R.targetStillLoaded = !(await tabs()).find((t) => t.id === target).skeleton;
  R.targetShowsNewest = await page.waitForFunction(() => document.querySelector("#content")?.textContent.includes("restmark-5"), null, { timeout: 30000 }).then(() => true, timedOut);
}
R.pageEvents = pageEvents;
console.log("RESULT:" + JSON.stringify(R));
await browser.close();
"""


class UnwatchedTabsInABrowser(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="unwatched-tabs-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "session-hosts").write_text("off\n")
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        base = int(time.time()) - 3 * 3600
        cls.sessions = []
        for i in range(1, N_SESSIONS + 1):
            sid, name = sid_of(i), "%s-%02d" % (GROUPS[i % 3], i)
            Path(cls.state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
            recs, prev, t = [], None, base + i * 60
            for k in range(TURNS_EACH):
                t += 2
                prev, two = _turn(i, k, prev, t, sid)
                recs += two
            path = os.path.join(proj, sid + ".jsonl")
            Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
            cls.sessions.append({"sid": sid, "name": name, "path": path, "i": i, "k": TURNS_EACH, "prev": prev, "t": t})
        cls.port = _free_port()
        cls.token = "testtok-unwatched"
        cls.env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "a"), stderr=subprocess.STDOUT, env=cls.env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            cls._skip("hermetic kernel never served /healthz here")
        cls._r = None

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _result(self):
        if getattr(type(self), "_fail", None):   # the driver failed once: every test reports that failure instead of re-driving the browser
            self.fail(type(self)._fail)
        if self._r is None:
            base = "http://127.0.0.1:%d" % self.port
            cfg = os.path.join(self.lab, "cfg.json")
            with open(cfg, "w") as f:
                json.dump({"chat": base + "/chat?token=" + self.token, "perf": base + "/perf?token=" + self.token,
                           "sessions": self.sessions, "shots": os.environ.get("ROMP_UNWATCHED_SHOTS", "")}, f)
            driver = os.path.join(self.lab, "unwatched.mjs")
            Path(driver).write_text(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=500,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if "browser-launch-failed" in p.stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            if line is None:
                type(self)._fail = "the driver produced no RESULT (stderr: %s)" % p.stderr[-2000:]
                self.fail(type(self)._fail)
            type(self)._r = json.loads(line[len("RESULT:"):])
        print("UNWATCHED:", json.dumps(self._r), file=sys.stderr)   # every test's call: pytest shows the failing test's captured stderr alone
        return self._r

    def test_the_chat_panes_dial_says_it_takes_a_loaded_tab_back(self):
        r = self._result()
        self.assertEqual(r["pageEvents"], [], "no page error anywhere in the run")
        self.assertTrue(r["filled"], "the first load settled: every tab loaded once or already at rest")
        self.assertIn("&rest=1", r["dial"] or "", "the shim states the capability for the chat app: %r" % r["dial"])
        self.assertIn("&skeleton=1", r["dial"] or "", "beside the diet, as before")

    def test_every_tab_but_the_one_on_screen_goes_to_rest_and_is_neither_built_nor_fetched_again(self):
        r = self._result()
        self.assertTrue(r["allRested"], "every unwatched tab turned into a skeleton tab: %r" % r["tabsAtRest"])
        rested = [t for t in r["tabsAtRest"] if t["skeleton"]]
        self.assertEqual(len(rested), N_SESSIONS - 1)
        self.assertNotIn(r["watched"], [t["id"] for t in rested], "the tab on screen stays loaded")
        self.assertEqual({t["title"] for t in rested}, {"Not up to date, click to load"}, "the title says what the tab is")
        self.assertGreaterEqual(r["restFramesTotal"], 1, "told by a status frame that carries the mark")
        # three more rounds of appends to all eight sessions: no chat frame for a tab at rest, no ask from the page, no build
        self.assertEqual(r["chatFramesForRested"], 0, "the kernel sends a resting tab no chat frame")
        self.assertEqual(r["asksWhileAtRest"], [], "the idle prefetch leaves a tab at rest to the click")
        self.assertEqual(r["perf1"]["bg"] - r["perf0"]["bg"], 0, "no tab nobody is watching was built: %r -> %r" % (r["perf0"], r["perf1"]))
        self.assertGreaterEqual(r["perf1"]["coldSkipped"] - r["perf0"]["coldSkipped"], N_SESSIONS - 1, "each was skipped, cycle after cycle")
        self.assertGreaterEqual(r["perf0"]["rested"], 1, "/perf counts the rests")

    def test_the_watched_tab_keeps_its_deltas(self):
        r = self._result()
        self.assertGreaterEqual(r["tailsForWatched"], 1, "the tab on screen is still sent its changes as deltas")
        self.assertTrue(r["watchedShowsNewest"], "and its newest turn is on screen")
        self.assertGreaterEqual(r["perf1"]["active"] - r["perf0"]["active"], 1, "the watched tab is the one the kernel builds")

    def test_a_click_on_a_resting_tab_shows_the_loader_asks_for_it_whole_and_lands_its_newest_turn(self):
        r = self._result()
        self.assertTrue(r["target"], "a tab at rest to click")
        self.assertIn("loading", r["loaderAtClick"] or "", "the loader is the click's acknowledgement, not the copy that went behind")
        self.assertIn("skeleton-click", r["asksForTarget"], "the page asks for the tab whole: %r" % r["asksForTarget"])
        self.assertTrue(r["freshAfterClick"], "the transcript that lands holds the session's newest turn")
        self.assertFalse(r["targetAfter"]["skeleton"], "and the tab is a loaded tab again")
        self.assertLessEqual(r["perf2"]["built"] - r["perf1"]["built"], 3, "one build for the click (and the watched tab's own): %r -> %r" % (r["perf1"], r["perf2"]))

    def test_the_tab_just_left_goes_to_rest_at_its_next_move(self):
        r = self._result()
        self.assertTrue(r["leftTabRested"], "nobody is watching it any more")
        self.assertEqual(r["leftTab"]["title"], "Not up to date, click to load")
        self.assertTrue(r["targetStillLoaded"], "the tab now on screen stays loaded")
        self.assertTrue(r["targetShowsNewest"], "and takes its own deltas")


if __name__ == "__main__":
    unittest.main()
