"""A history gap is drawn at the height its turns will take once loaded, on a session that takes much mail (2026-10-01).

The head gap (T386 stage 2) is empty space standing for turns [lo, hi) in the kernel's numbering, drawn at that count times a
px-per-turn the page measures off the rows it holds. The page used to count those rows' turns itself, one per typed row, while the
kernel opens a turn on a peer session's message too: on a session whose turns alternate between a typed prompt and a peer's message,
the measure read half the turns and the gap stood about twice the height its turns took once they loaded. The kernel now stamps every
event with its turn (`turn`, caps `chatTurn`) and the page counts those.

Served, on a hermetic kernel over a synthetic transcript of four hundred turns, the even ones typed, the odd ones a peer's message
(hydrated into its card from a synthetic postal log), each answered by a one-line reply. Two page loads against the one kernel:

- the kernel as it is: the gap's drawn px per turn, read with the head page's reply held at the wire, matches the height per turn the
  head page's rows take once released, within a tolerance;
- the same kernel dressed as an older one at the wire (the stamps stripped from every frame, chatTurn dropped from the caps): the page
  falls back to its typed count, so the gap stands about twice the rows' height, and says so in one turnCountTyped client-diag row.

Synthetic fixtures only (placeholder uuids, invented notes-api prose).
"""
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
from tests.test_live_paused_window_browser import BIN, EXT, _free_port  # noqa: E402
import tests.test_ship_reship_served as _lab  # noqa: E402  the lab kernel's environment

SID = "cccccccc-1111-2222-3333-555555555555"     # the viewed session ("web")
PEER = "dddddddd-1111-2222-3333-555555555555"    # the session the mail comes from ("api")
TURNS = 400           # the wire tail holds the last ~125 turns, the head gap the rest
BASE = 1789041600     # noon UTC: the transcript's half hour crosses no midnight, so no day divider stands between the turns
REAL_FROM, REAL_TO = 2, 14   # the head page's turns whose rendered height is the truth (clear of the head cards above turn 0)
TOLERANCE = 0.2


def _uuid(k):
    return "11111111-2222-3333-4444-%012d" % k   # the user record (typed or delivered) that opens turn k


def _mid(k):
    return "33333333-4444-5555-6666-%012d" % k


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const painted = (page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));

// One page load. olderKernel: every frame the kernel sends is rewritten at the wire as an older kernel would have sent it, the events
// without `turn` and the caps without chatTurn.
async function leg(olderKernel) {
  const page = await browser.newPage({ viewport: cfg.viewport });
  await page.addInitScript(() => {
    const send = WebSocket.prototype.send;
    window.__sent = [];
    WebSocket.prototype.send = function (d) {
      try { const m = JSON.parse(d); if (m && m.type) window.__sent.push(m); } catch (e) { /* not a frame */ }
      return send.call(this, d);
    };
  });
  const asOlder = (m) => {
    if (!olderKernel || typeof m !== "string") return m;
    let f; try { f = JSON.parse(m); } catch (e) { return m; }
    if (!f || typeof f !== "object" || Array.isArray(f)) return m;
    if (f.type === "caps" && Array.isArray(f.caps)) f.caps = f.caps.filter((c) => c !== "chatTurn");
    for (const k of ["events", "headCards"]) if (Array.isArray(f[k])) for (const e of f[k]) if (e && typeof e === "object") delete e.turn;
    return JSON.stringify(f);
  };
  let hold = false; const held = [];
  await page.routeWebSocket((u) => /[?&]app=chat(?:&|$)/.test(u.href), (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((m) => server.send(m));
    server.onMessage((m) => { const out = asOlder(m); if (hold && typeof out === "string" && /^\{"type": ?"chatTurns"/.test(out)) held.push([ws, out]); else ws.send(out); });
    server.onClose(() => ws.close()); ws.onClose(() => server.close());
  });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 300)));
  await page.goto(cfg.chat);
  await page.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll("#content .turn[data-uuid]").length >= 20, null, { timeout: 60000 });
  await painted(page); await page.waitForTimeout(2500); await painted(page);
  // the rows the page measured at its landing: how many typed rows and peer cards, and how many carry the kernel's turn
  const landed = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll("#content .turn[data-unit]"));
    return { typed: rows.filter((r) => r.classList.contains("turn-user")).length, peer: rows.filter((r) => r.classList.contains("turn-postal-service")).length,
             stamped: rows.filter((r) => r.dataset.kturn != null).length, regions: typeof window.__rompRegions === "function" ? window.__rompRegions() : null };
  });
  // the head gap as drawn: a jump to the head renders its element and asks its page, held at the wire so the gap stays
  hold = true;
  const asks0 = await page.evaluate(() => window.__sent.filter((m) => m.type === "loadTurns").length);
  await page.evaluate(() => { document.getElementById("content").scrollTop = 0; });
  await painted(page);
  await page.waitForFunction((n) => window.__sent.filter((m) => m.type === "loadTurns").length > n, asks0, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(500); await painted(page);
  const gap = await page.evaluate(() => {
    const g = Array.from(document.querySelectorAll("#content .tx-gap")).find((x) => x.offsetParent !== null);
    return g ? { lo: +g.dataset.lo, hi: +g.dataset.hi, h: g.offsetHeight } : null;
  });
  // released: the head page's rows land, and their own height is the truth the gap stood for
  hold = false;
  for (const [w, m] of held.splice(0)) { try { w.send(m); } catch (e) { /* closed */ } }
  await page.waitForFunction(() => { const rs = (typeof window.__rompRegions === "function" && window.__rompRegions()) || []; return rs.length > 0 && rs[0].kind === "run" && rs[0].lo === 0; }, null, { timeout: 15000 }).catch(() => {});
  await page.evaluate(() => { document.getElementById("content").scrollTop = 0; });
  await painted(page); await page.waitForTimeout(500); await painted(page);
  const real = await page.evaluate(([a, b]) => {
    const top = (u) => { const e = document.querySelector(`#content .turn[data-uuid="${u}"]`); return e ? e.getBoundingClientRect().top : null; };
    const ta = top(a), tb = top(b);
    return { ta, tb, extent: ta != null && tb != null ? tb - ta : null };
  }, [cfg.realFrom, cfg.realTo]);
  const diag = await page.evaluate(() => window.__sent.filter((m) => m.type === "clientDiag" && m.what === "turnCountTyped").map((m) => m.data));
  await page.close();
  return { landed, gap, real, diag, pageErrors };
}
const out = { stamped: await leg(false), older: await leg(true) };
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
"""


class TurnStampedHeight(unittest.TestCase):
    maxDiff = None
    VIEWPORT = {"width": 1000, "height": 700}

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here) — the served lab needs them")
        cls.lab = tempfile.mkdtemp(prefix="turn-stamped-height-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "timeline"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no session host (T348)
        Path(state, "names", SID).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "names", PEER).write_text("api\t%s\t#1EA1EB\t#ffffff\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        recs, log, prev = [], [], None
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
        for k in range(TURNS):
            u, a, t = _uuid(k), "22222222-3333-4444-5555-%012d" % k, BASE + 4 * k
            if k % 2 == 0:
                recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": iso(t), "sessionId": SID, "promptSource": "typed",
                             "message": {"role": "user", "content": "what is left on the notes index, step %d?" % k}})
            else:
                body = "The retry budget in notes-api is ready for review, part %d." % k
                recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": iso(t), "sessionId": SID, "promptSource": "sdk",
                             "message": {"role": "user", "content": "\n".join(["## from api", body, "<!-- romp-msg-id: %s -->" % _mid(k),
                                                                                "<!-- romp-msg-kind: coordinate -->"])}})
                log.append({"t": t - 1, "ev": "sent", "id": _mid(k), "from": "api", "from_id": PEER, "to_id": SID, "body": body,
                            "kind": "coordinate", "from_host": ""})
            recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": iso(t + 2), "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": "Done with step %d of the notes index." % k}]}})
            prev = a
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(state, "timeline", "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in log))
        cls.port = _free_port()
        cls.token = "testtok-turnstamp"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
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
        if type(self)._r is not None:
            return type(self)._r
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "viewport": self.VIEWPORT,
                       "realFrom": _uuid(REAL_FROM), "realTo": _uuid(REAL_TO)}, f)
        driver = os.path.join(self.lab, "drive.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            self._skip("no playwright browser on this box — the served lab needs one")
        klog = ""
        try:
            with open(self.klog) as f:
                klog = f.read()[-2500:]
        except OSError:
            pass
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + klog)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        type(self)._r = json.loads(line[len("RESULT:"):])
        print("TURN-STAMPED-HEIGHT:", json.dumps(type(self)._r), file=sys.stderr)
        return type(self)._r

    def _ratio(self, leg):
        """The gap's drawn px per turn over the px per turn the same kind of turns take once rendered."""
        g, real = leg["gap"], leg["real"]
        self.assertIsNotNone(g, "the head gap's element rendered at the head: %r" % leg)
        self.assertIsNotNone(real["extent"], "the head page's rows rendered once released: %r" % leg)
        est = g["h"] / (g["hi"] - g["lo"])
        truth = real["extent"] / (REAL_TO - REAL_FROM)
        return est / truth, est, truth

    def test_the_lab_holds_a_head_gap_and_a_tail_of_typed_and_peer_turns(self):
        L = self._result()["stamped"]["landed"]
        rs = L["regions"] or []
        self.assertTrue(rs and rs[0]["kind"] == "gap" and rs[0]["hi"] > 250, "a head gap stands for most of the transcript: %r" % rs)
        self.assertGreater(L["typed"], 10)
        self.assertLessEqual(abs(L["peer"] - L["typed"]), 2, "half the measured turns were opened by a peer's message: %r" % L)

    def test_the_gap_stands_at_the_height_its_turns_take_once_loaded(self):
        leg = self._result()["stamped"]
        ratio, est, truth = self._ratio(leg)
        self.assertLess(abs(ratio - 1), TOLERANCE,
                        "the gap's %.1f px per turn against the rows' %.1f (x%.2f): counted by typed rows it read about twice" % (est, truth, ratio))
        self.assertGreater(leg["landed"]["stamped"], 0, "the rows carry the kernel's turn: %r" % leg["landed"])
        self.assertEqual(leg["diag"], [], "the kernel road files nothing")

    def test_on_an_older_kernel_the_page_counts_typed_rows_and_says_so(self):
        leg = self._result()["older"]
        self.assertEqual(leg["landed"]["stamped"], 0, "the wire carried no stamp (the lab's premise): %r" % leg["landed"])
        ratio, est, truth = self._ratio(leg)
        self.assertGreater(ratio, 1.6, "the typed count reads half the turns: the gap stands about twice the rows (x%.2f)" % ratio)
        self.assertEqual([d.get("cause") for d in leg["diag"]], ["olderKernel"], "…and the page says so once, in client-diag: %r" % leg["diag"])
        self.assertEqual(leg["diag"][0].get("sid"), SID)

    def test_the_pages_raised_no_error(self):
        r = self._result()
        self.assertEqual(r["stamped"]["pageErrors"] + r["older"]["pageErrors"], [])


if __name__ == "__main__":
    unittest.main()
