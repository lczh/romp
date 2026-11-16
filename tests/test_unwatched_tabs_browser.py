"""A tab nobody is watching goes to rest, in a real browser (2026-11-15; the kernel half is tests/test_unwatched_tabs.py).

A hermetic kernel over EIGHT synthetic sessions, the real /chat page served from a copy of the built bundle, driven by
Playwright, one browser. The page loads (the diet: the selected tab whole, the rest filled by its idle prefetch), then every
session appends a turn the way a working session does, several times. What the roads pin, on the page and on the wire:

  (1) the dial: the chat pane's socket says it can take a loaded tab back (rest=1);
  (2) the rest: every tab but the one on screen is taken back on a status frame that carries `rest`, and from then on the
      page is sent no chat frame for those tabs however often their transcripts move, the kernel builds none of them
      (/perf), and the page's idle prefetch asks for none of them. The page keeps each copy: none turns into a skeleton tab;
  (3) the watched tab: its deltas keep coming and its newest turn is on screen;
  (4) the click on a tab at rest: the copy the page holds paints at once, no loader, with the line above the message box
      saying it is catching up and the message box usable; the page asks for the tab whole; the full that lands holds the
      newest turn, takes the line down, and replaces nothing that was painted (the rows on screen are the same nodes);
  (5) the flip: the page watches the last tabs it showed (two here, ROMP_WATCHED_TABS in the lab kernel's environment; four
      by default), so the tab just left is not taken back while both sessions go on writing, and flipping back to it and
      forth again shows each at once with its newest turn: no line, no ask;
  (6) the displacement: opening a third tab takes the oldest of the three out of the set, and that one is taken back at its
      next move; opened again it shows its copy and catches up.

With ROMP_UNWATCHED_SHOTS naming a directory, the driver also saves the catching-up state there: the dark and the light
theme, at a desktop and at a phone width.

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
// every dial's URL, every frame the page receives and every message it sends its kernel, from before the page's scripts run.
// While window.__holdAsks is set, what a click tells the kernel (activeTab, whose push would bring the tab whole, and the
// page's own needFull) is kept back (window.__held) until release() sends it on in order: the state between the click and the
// answer stands still to be read
const init = () => {
  window.__dials = []; const W = window.WebSocket;
  window.WebSocket = function (url, protos) { window.__dials.push(String(url)); return protos === undefined ? new W(url) : new W(url, protos); };
  window.WebSocket.prototype = W.prototype; window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1; window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
  window.__frames = []; window.__sent = []; window.__held = []; const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    let m = null; try { m = JSON.parse(d); } catch (e) { /* not a frame */ }
    if (m && m.type) window.__sent.push({ type: m.type, id: m.id, why: m.why });
    if (m && (m.type === "needFull" || m.type === "activeTab") && window.__holdAsks) { window.__held.push([this, d]); return; }
    return send.call(this, d); };
  window.__release = () => { window.__holdAsks = false; for (const [ws, d] of window.__held.splice(0)) send.call(ws, d); };
  window.addEventListener("message", (e) => { const m = e.data; if (!m || !m.type) return;
    window.__frames.push({ type: m.type, id: m.id || null, rest: m.rest === true }); });
};
await page.addInitScript(init);
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
const tabs = (p = page) => p.evaluate(() => Array.from(document.querySelectorAll("#tabs .tab")).map((t) => ({ id: t.dataset.id, skeleton: t.classList.contains("tab-skeleton"), title: t.title })));
const perf = async () => { const j = await (await fetch(cfg.perf)).json(); const c = j.builds.chat; return { built: c.built, bg: c.bg_built, active: c.active_built, rested: c.rested, coldSkipped: c.coldSkipped }; };
const timedOut = (e) => { if (e && e.name === "TimeoutError") return false; throw e; };
const restedIds = (p = page) => p.evaluate(() => Array.from(new Set(window.__frames.filter((f) => f.type === "status" && f.rest).map((f) => f.id))));
const settled = (p = page) => p.waitForFunction((n) => document.querySelectorAll("#tabs .tab").length >= n && document.querySelectorAll("#tabs .tab-placeholder").length === 0
  && !Array.from(document.querySelectorAll("#tabs .tab-skeleton")).some((t) => t.title.includes("Not loaded yet")), S.length, { timeout: 120000 }).then(() => true, timedOut);
// what the pane shows right now: the loader, the catching-up line, the message box, the visible thread's words
const pane = (p = page) => p.evaluate(() => {
  const line = document.querySelector("#statusline .catching-up-line"), box = document.getElementById("composer-input");
  const th = Array.from(document.querySelectorAll("#content .thread")).find((t) => t.style.display !== "none");
  return { loader: !!document.getElementById("tab-loading"), line: line ? line.textContent : null, lineTitle: line ? line.title : null,
           boxUsable: !!box && !box.disabled, text: th ? th.textContent : "", rows: th ? th.querySelectorAll(".turn").length : 0 };
});
const R = {};
await page.goto(cfg.chat);
R.filled = await settled();
R.dial = await page.evaluate(() => window.__dials.find((d) => d.includes("app=chat")) || null);
R.watched = await page.evaluate(() => document.querySelector("#tabs .tab.active")?.dataset.id || null);
// ROAD 2: every session appends; every tab but the watched one is taken back, and stays a loaded tab
appendAll("restmark-1");
R.allRested = await page.waitForFunction((n) => new Set(window.__frames.filter((f) => f.type === "status" && f.rest).map((f) => f.id)).size >= n, S.length - 1, { timeout: 60000 }).then(() => true, timedOut);
R.rested = await restedIds();
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
R.chatFramesForRested = fr.filter((f) => (f.type === "session" || f.type === "chatTail") && f.id !== R.watched).length;
R.tailsForWatched = fr.filter((f) => f.type === "chatTail" && f.id === R.watched).length;
// ROAD 4: the click on a tab at rest. The ask is held back so the state between the click and the answer can be read
const target = R.rested.filter((id) => id !== R.watched)[2] || R.rested[0] || null;
R.target = target;
if (target) {
  await page.evaluate(() => { window.__holdAsks = true; });
  await page.click(`#tabs .tab[data-id="${target}"]`);
  await page.waitForFunction(() => !!document.querySelector("#statusline .catching-up-line"), null, { timeout: 10000 }).catch(timedOut);
  R.atClick = await pane();
  R.atClick.heldAsks = await page.evaluate(() => window.__held.filter(([, d]) => JSON.parse(d).type === "needFull").length);
  R.asksForTarget = await page.evaluate((id) => window.__sent.filter((m) => m.type === "needFull" && m.id === id).map((m) => m.why), target);
  // the transcript's rows on screen now are marked, to be found again once the full has landed (the card at the tail that is
  // drawn from the session's live state, here the lab's own "no agent backend" card, is drawn anew with every status)
  R.marked = await page.evaluate(() => { const th = Array.from(document.querySelectorAll("#content .thread")).find((t) => t.style.display !== "none");
    const rows = th ? Array.from(th.querySelectorAll(".turn:not(.turn-apierror)")) : []; rows.forEach((r, i) => { r.__heldCopy = i + 1; }); return rows.length; });
  await page.evaluate(() => window.__release());
  R.freshAfterClick = await page.waitForFunction(() => !document.querySelector("#statusline .catching-up-line") && document.querySelector("#content")?.textContent.includes("restmark-4"), null, { timeout: 60000 }).then(() => true, timedOut);
  R.afterFull = await pane();
  R.keptRows = await page.evaluate(() => { const th = Array.from(document.querySelectorAll("#content .thread")).find((t) => t.style.display !== "none");
    return th ? Array.from(th.querySelectorAll(".turn")).map((r) => r.__heldCopy || 0).filter((i) => i) : []; });
  R.targetAfter = (await tabs()).find((t) => t.id === target);
  R.perf2 = await perf();
  // ROAD 5: the flip. Both sessions keep writing; the tab just left is not taken back, and each flip shows its tab at once
  const flip = async (id, mark) => {
    const asks0 = await page.evaluate(() => window.__sent.filter((m) => m.type === "needFull").length);
    await page.click(`#tabs .tab[data-id="${id}"]`);
    const at = await pane();
    const fresh = await page.waitForFunction((m) => !document.getElementById("tab-loading") && document.querySelector("#content")?.textContent.includes(m), mark, { timeout: 30000 }).then(() => true, timedOut);
    return { loader: at.loader, line: at.line, fresh, asks: (await page.evaluate(() => window.__sent.filter((m) => m.type === "needFull").length)) - asks0 };
  };
  appendAll("restmark-5");
  R.targetShowsNewest = await page.waitForFunction(() => document.querySelector("#content")?.textContent.includes("restmark-5"), null, { timeout: 30000 }).then(() => true, timedOut);
  const fr1 = await page.evaluate(() => window.__frames.length);
  R.flipBack = await flip(R.watched, "restmark-5");          // back to the first tab: it took its deltas while the page was elsewhere
  appendAll("restmark-6");
  R.flipForth = await flip(target, "restmark-6");            // and forth again
  R.flipRestFrames = (await page.evaluate((n) => window.__frames.slice(n), fr1)).filter((f) => f.type === "status" && f.rest && (f.id === R.watched || f.id === target)).length;
  // ROAD 6: a third tab displaces the oldest of the set (the first tab), which is taken back at its next move
  const third = R.rested.filter((id) => id !== R.watched && id !== target)[0] || null;
  R.third = third;
  if (third) {
    // The frames are marked BEFORE the click (2026-11-16): the click is the moment the first tab leaves the set, and it is
    // taken back at the first cycle that finds its transcript past its cached build. That can be the turn appended below,
    // or, on a slow runner, the turn appended for the flip above, which the cycle had not built for it yet: its rest frame
    // then lands while this road is still waiting for the third tab, ahead of a mark read after that wait, and no second
    // one follows (a tab is taken back once). Marked after the wait, the road timed out on a frame that had already come
    // (main's CI on the merge of PR 473; the reopen below still showed the tab catching up)
    const fr2 = await page.evaluate(() => window.__frames.length);
    await page.click(`#tabs .tab[data-id="${third}"]`);
    R.thirdFresh = await page.waitForFunction(() => !document.querySelector("#statusline .catching-up-line") && document.querySelector("#content")?.textContent.includes("restmark-6"), null, { timeout: 60000 }).then(() => true, timedOut);
    R.displacedBeforeAppend = await page.evaluate(([n, id]) => window.__frames.slice(n).some((f) => f.type === "status" && f.rest && f.id === id), [fr2, R.watched]);   // for the record: which of the two moves took it back
    appendAll("restmark-7");
    R.displacedRested = await page.waitForFunction(([n, id]) => window.__frames.slice(n).some((f) => f.type === "status" && f.rest && f.id === id), [fr2, R.watched], { timeout: 60000 }).then(() => true, timedOut);
    R.keptAfterThird = !(await page.evaluate(([n, id]) => window.__frames.slice(n).some((f) => f.type === "status" && f.rest && f.id === id), [fr2, target]));
    R.displacedTab = (await tabs()).find((t) => t.id === R.watched);
    // …and opened again it shows its copy and catches up
    await page.evaluate(() => { window.__holdAsks = true; });
    await page.click(`#tabs .tab[data-id="${R.watched}"]`);
    await page.waitForFunction(() => !!document.querySelector("#statusline .catching-up-line"), null, { timeout: 10000 }).catch(timedOut);
    R.reopened = await pane();
    await page.evaluate(() => window.__release());
    R.reopenedFresh = await page.waitForFunction(() => !document.querySelector("#statusline .catching-up-line") && document.querySelector("#content")?.textContent.includes("restmark-7"), null, { timeout: 60000 }).then(() => true, timedOut);
  }
}
R.pageEvents = pageEvents;
// THE CATCHING-UP STATE, SAVED (ROMP_UNWATCHED_SHOTS): each theme at a desktop and at a phone width, a page of its own
if (cfg.shots) {
  R.shots = [];
  let n = 7;
  for (const [theme, name, w, h] of [["yatharth", "dark-desktop", 1300, 760], ["yatharth-light", "light-desktop", 1300, 760],
                                     ["yatharth", "dark-phone", 390, 844], ["yatharth-light", "light-phone", 390, 844]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    const p = await ctx.newPage();
    await p.addInitScript(init);
    await p.addInitScript((t) => { try { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}"); s.theme = t; localStorage.setItem("romp:settings", JSON.stringify(s)); } catch (e) { /* no storage */ } }, theme);
    await p.goto(cfg.chat);
    await settled(p);
    const on = await p.evaluate(() => document.querySelector("#tabs .tab.active")?.dataset.id || null);
    appendAll("restmark-" + (++n));
    await p.waitForFunction((k) => new Set(window.__frames.filter((f) => f.type === "status" && f.rest).map((f) => f.id)).size >= k, 3, { timeout: 60000 }).catch(timedOut);
    const at = (await restedIds(p)).filter((id) => id !== on)[0];
    if (at) {
      await p.evaluate(() => { window.__holdAsks = true; });
      await p.evaluate((id) => { const t = document.querySelector(`#tabs .tab[data-id="${id}"]`); if (t) t.click(); }, at);
      await p.waitForFunction(() => !!document.querySelector("#statusline .catching-up-line"), null, { timeout: 10000 }).catch(timedOut);
      await p.screenshot({ path: cfg.shots + "/catching-up-" + name + ".png" });
      R.shots.push({ name, state: await pane(p) });
      await p.evaluate(() => window.__release());
    }
    await ctx.close();
  }
}
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
        cls.env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_WATCHED_TABS="2")   # two, so a third tab shows a displacement
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
        self.assertTrue(r["filled"], "the first load settled: every tab loaded once")
        self.assertIn("&rest=1", r["dial"] or "", "the shim states the capability for the chat app: %r" % r["dial"])
        self.assertIn("&skeleton=1", r["dial"] or "", "beside the diet, as before")

    def test_every_tab_but_the_one_on_screen_is_taken_back_keeps_its_copy_and_is_neither_built_nor_fetched_again(self):
        r = self._result()
        self.assertTrue(r["allRested"], "every unwatched tab was taken back: %r" % r["rested"])
        self.assertEqual(len(r["rested"]), N_SESSIONS - 1)
        self.assertNotIn(r["watched"], r["rested"], "the tab on screen is not")
        self.assertEqual([t for t in r["tabsAtRest"] if t["skeleton"]], [], "none turns into a skeleton tab: the page keeps each copy")
        # three more rounds of appends to all eight sessions: no chat frame for a tab at rest, no ask from the page, no build
        self.assertEqual(r["chatFramesForRested"], 0, "the kernel sends a resting tab no chat frame")
        self.assertEqual(r["asksWhileAtRest"], [], "the idle prefetch leaves a tab at rest alone")
        self.assertEqual(r["perf1"]["bg"] - r["perf0"]["bg"], 0, "no tab nobody is watching was built: %r -> %r" % (r["perf0"], r["perf1"]))
        self.assertGreaterEqual(r["perf1"]["coldSkipped"] - r["perf0"]["coldSkipped"], N_SESSIONS - 1, "each was skipped, cycle after cycle")
        self.assertGreaterEqual(r["perf0"]["rested"], 1, "/perf counts the rests")

    def test_the_watched_tab_keeps_its_deltas(self):
        r = self._result()
        self.assertGreaterEqual(r["tailsForWatched"], 1, "the tab on screen is still sent its changes as deltas")
        self.assertTrue(r["watchedShowsNewest"], "and its newest turn is on screen")
        self.assertGreaterEqual(r["perf1"]["active"] - r["perf0"]["active"], 1, "the watched tab is the one the kernel builds")

    def test_a_click_on_a_resting_tab_paints_its_copy_at_once_says_it_is_catching_up_and_the_full_replaces_nothing(self):
        r = self._result()
        self.assertTrue(r["target"], "a tab at rest to click")
        at = r["atClick"]
        self.assertFalse(at["loader"], "no loader: the copy the page holds is on screen")
        self.assertGreater(at["rows"], 0, "…its turns painted")
        self.assertIn("answer", at["text"]); self.assertNotIn("restmark-4", at["text"], "the copy is behind: it lacks what the session wrote since")
        self.assertTrue((at["line"] or "").startswith("Catching up"), "the line above the message box says so: %r" % at["line"])
        self.assertIn("as it was when it was last open here", at["lineTitle"] or "")
        self.assertTrue(at["boxUsable"], "the message box takes input meanwhile")
        self.assertEqual((r["asksForTarget"], at["heldAsks"]), (["rest-open"], 1), "the page asks for the tab whole, once")
        self.assertTrue(r["freshAfterClick"], "the full lands: the session's newest turn is on screen and the line is gone")
        self.assertIsNone(r["afterFull"]["line"])
        self.assertEqual(r["keptRows"], list(range(1, r["marked"] + 1)),
                         "every transcript row painted from the copy is still there, the same node in the same order: nothing torn down and rebuilt")
        self.assertGreater(r["afterFull"]["rows"], at["rows"] - 1)
        self.assertFalse(r["targetAfter"]["skeleton"])
        self.assertLessEqual(r["perf2"]["built"] - r["perf1"]["built"], 3, "one build for the click (and the watched tab's own): %r -> %r" % (r["perf1"], r["perf2"]))

    def test_flipping_between_the_last_two_tabs_shown_meets_no_wait_and_each_is_current(self):
        r = self._result()
        self.assertTrue(r["targetShowsNewest"], "the tab now on screen takes its own deltas")
        for what, f in (("back", r["flipBack"]), ("forth", r["flipForth"])):
            self.assertEqual((f["loader"], f["line"], f["asks"]), (False, None, 0), "the flip %s shows the tab at once, current, with no ask: %r" % (what, f))
            self.assertTrue(f["fresh"], "…holding the turn its session wrote while the page was on the other tab: %r" % f)
        self.assertEqual(r["flipRestFrames"], 0, "neither was taken back")

    def test_a_third_tab_displaces_the_oldest_which_is_taken_back_and_catches_up_when_opened_again(self):
        r = self._result()
        self.assertTrue(r["third"] and r["thirdFresh"], "a third tab opened and caught up")
        self.assertTrue(r["displacedRested"], "the first tab is no longer among the last two shown: taken back at its next move "
                        "(the frame counted from the click that displaced it, whichever turn that move was)")
        self.assertTrue(r["keptAfterThird"], "the second stays watched")
        self.assertFalse(r["displacedTab"]["skeleton"], "…and stays a loaded tab on the strip")
        re_ = r["reopened"]
        self.assertEqual((re_["loader"], (re_["line"] or "")[:11]), (False, "Catching up"), "opened again: its copy, and the line: %r" % re_)
        self.assertTrue(r["reopenedFresh"])

if __name__ == "__main__":
    unittest.main()
