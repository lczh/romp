"""The settings' MACHINE SELECTOR over two lab kernels (plans/settings-across-machines.md, phase two; the user 2026-09-19): kernel A
serves the dashboard; kernel B checks in to A as a peer (the check-in handshake, no ssh), so A polls B's /version and its
/tunnels row carries B's settings, stamps and pins. B boots with task tracking OFF while A has it on, so the two disagree.

Driven in a real browser over A's landing page: (1) before B checks in the selector is hidden and the settings look as they did;
(2) with B up, the selector stands above the tabs reading All kernels, with "1 differs" beside it, and the Task tracking row wears
the "differs" badge naming both machines and their values, its pin glyph shown and unlit; (3) picking B scopes the row to B's value
(off); clicking it applies ON to B alone (B's /version: taskTracking true, settingsPinned task-tracking true) and A keeps its own
stamp, the glyph lights; (4) un-pinning from the glyph clears B's pin (B keeps on: no newer different value stands elsewhere) and
the glyph unlights; (5) back under All kernels a click applies to BOTH kernels (both /version read the same value); (6) a scoped
click that makes them disagree raises no card; (7) picked to both kernels while they disagree, the row is mixed and held ticked
under the dash, its hover names the two, a click turns it OFF on both and a second click ON (the #377 review: a click on a mixed
box lands on off, the side that does nothing). B checks in only after the one-kernel survey, by the driver itself, so the order
is the event's, never a clock's. Synthetic
throughout: TESTHOSTB, placeholder sids, invented text. Skips LOUDLY without the extension deps or a Playwright browser (CI sets
ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a failure)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
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

SID_A = "11111111-2222-3333-4444-555555555555"
SID_B = "11111111-2222-3333-4444-666666666666"
PEER = "TESTHOSTB"

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
const page = await ctx.newPage();
const errors = [], consoleErrors = []; page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 400)); });   // the gear's fill says its faults here (a resource 404 is the lab's, not the gear's)
// the kernels' /version read from NODE (kernel B is another origin: the page may not fetch it)
const version = async (u) => (await (await fetch(u, { cache: "no-store" })).json());
const until = async (fn, tries) => { for (let i = 0; i < tries; i++) { if (await fn()) return true; await new Promise((r) => setTimeout(r, 500)); } return false; };
await page.goto(cfg.landing);
await page.waitForSelector("#rail-gear", { timeout: 20000 });
const frameBy = async (part) => { let f = page.frames().find((x) => x.url().includes(part)); for (let i = 0; i < 100 && !f; i++) { await page.waitForTimeout(100); f = page.frames().find((x) => x.url().includes(part)); } return f; };
const openSettings = async () => {
  await page.evaluate(() => window.__rompOpenSettings("tasks"));
  await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  const f = await frameBy("/settings");
  await f.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
  await f.waitForFunction(() => document.getElementById("rs-tasktrack") !== null, null, { timeout: 15000 });
  await f.waitForFunction(() => document.getElementById("rs-scope") !== null, null, { timeout: 3000 }).catch(() => {});   // a gear without the selector (the base): the survey says so
  await f.waitForFunction(() => { const sc = document.getElementById("rs-scope"); return !!sc && !sc.hidden; }, null, { timeout: 8000 }).catch(() => {});   // the selector, once the rows are read
  await f.waitForTimeout(400);
  return f;
};
const closeSettings = async (f) => { await f.evaluate(() => window.__rompSettingsClose && window.__rompSettingsClose()); await page.waitForTimeout(400); };   // the gear's own close, in its frame: the next open re-fills
const survey = (f) => f.evaluate(() => {
  const q = (s) => document.querySelector(s);
  const scope = q("#rs-scope"), lbl = q("#rs-scope-label"), cnt = q("#rs-scope-count"), tabs = q("#rs-tabs");
  const row = document.getElementById("rs-tasktrack").closest("label");
  const mark = row && row.querySelector(".rs-mixed"), pin = row && row.querySelector(".rs-pin");
  return { scopeHidden: !scope || scope.hidden || getComputedStyle(scope).display === "none", label: lbl ? lbl.textContent : null,
           count: cnt && !cnt.hidden ? cnt.textContent : "", countTitle: cnt ? cnt.title : "",
           aboveTabs: !!(scope && tabs && scope.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING),
           mark: mark && !mark.hidden ? { text: mark.textContent, differs: mark.classList.contains("rs-differs"), title: mark.title, color: getComputedStyle(mark).color } : null,
           pin: pin ? { hidden: pin.hidden, lit: pin.getAttribute("aria-pressed") === "true", title: pin.title } : null,
           checked: document.getElementById("rs-tasktrack").checked, indeterminate: document.getElementById("rs-tasktrack").indeterminate };
});
const out = { errors, consoleErrors };
// (1) one kernel: the selector hidden, the settings as today
let setF = await openSettings();
out.alone = await survey(setF);
await closeSettings(setF);
// only now does B check in to A, from here: the one-kernel survey has read the settings first by construction (the test used to
// check in from Python after a fixed sleep, and on a loaded runner the check-in could land before the page's first read)
try {
  const r = await fetch(cfg.checkin.url, { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.checkin.token }, body: JSON.stringify(cfg.checkin.body) });
  out.checkinAck = await r.json();
} catch (e) { out.checkinAck = { error: String(e) }; }
// A polls B; wait for the tunnels row
const readTunnels = () => page.evaluate(async (u) => { try { const d = await (await fetch(u, { cache: "no-store" })).json(); return (d.tunnels || []).map((t) => ({ host: t.host, status: t.status, detail: t.detail, settings: t.settings, pinned: t.settingsPinned, checkin: t.checkinPeer })); } catch (e) { return { error: String(e) }; } }, cfg.tunnels);
out.tunnelsWait = 0;
for (let i = 0; i < 90; i++) {   // the check-in lands a few seconds into the run; the poll makes the row up with B's settings a pass later
  out.tunnels = await readTunnels();
  if (Array.isArray(out.tunnels) && out.tunnels.some((t) => t.status === "up" && t.settings && typeof t.settings.taskTracking === "boolean")) break;
  out.tunnelsWait = i + 1; await page.waitForTimeout(1000);
}
setF = await openSettings();
// the settings page's federation dials B on its own /tunnels poll, every few seconds: a setting sent to B before the page holds a
// link there has nothing to ride and is dropped, so the clicks below wait for the open link, the event, before they go
out.fedOpen = await setF.waitForFunction((peer) => !!window.__rompFed && window.__rompFed.open(peer), cfg.peer, { timeout: 30000 }).then(() => true).catch(() => false);
out.two = await survey(setF);
out.two.options = await setF.evaluate(() => { const b = document.getElementById("rs-scope-btn"); if (!b) return []; b.click(); return Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).map((o) => o.textContent.trim()); });   // no button (a gear without the selector): report, do not throw
// (3) pick B: the row reads B's value; a click applies to B alone and pins it there
const picked = await setF.evaluate((peer) => { const opt = Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).find((o) => o.textContent.trim() === peer); if (!opt) return false; opt.querySelector("input").click(); return true; }, cfg.peer);
if (!picked) { await browser.close(); process.stdout.write("RESULT:" + JSON.stringify(out) + "\n", () => process.exit(0)); }
await setF.evaluate(() => { const l = document.getElementById("rs-scope-list"); if (l && !l.hidden) document.getElementById("rs-scope-btn").click(); });   // the list stays open for several picks; close it before the row is clicked
await setF.waitForFunction(() => document.getElementById("rs-scope-label").textContent !== "All kernels", null, { timeout: 5000 }).catch(() => {});
await setF.waitForTimeout(900);
out.scopedB = await survey(setF);
await setF.click("#rs-tasktrack");
out.scopedApplied = await until(async () => (await version(cfg.versionB)).taskTracking === true, 30);
// A's row for B must carry B's new pin before the gear can light the glyph: how long the poll takes, and what the row says
const t0 = Date.now();
out.rowPinned = await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.host === cfg.peer && t.settingsPinned && t.settingsPinned["task-tracking"]); }, 60);
out.rowPinnedAfterMs = Date.now() - t0;
out.rowAfterScoped = (await version(cfg.tunnels)).tunnels.map((t) => ({ host: t.host, status: t.status, pinned: t.settingsPinned, tt: t.settings && t.settings.taskTracking }));
// the glyph lights once A's next poll of B brings B's pin back to this dashboard (the gear re-fills a few times after a scoped change)
await setF.waitForFunction(() => { const row = document.getElementById("rs-tasktrack").closest("label"); const g = row.querySelector(".rs-pin"); return g && g.getAttribute("aria-pressed") === "true"; }, null, { timeout: 25000 }).catch(() => {});
out.afterScopedClick = { a: await version(cfg.versionA), b: await version(cfg.versionB), gear: await survey(setF) };
// (4) un-pin from the glyph: B's pin clears
await setF.evaluate(() => { const row = document.getElementById("rs-tasktrack").closest("label"); row.querySelector(".rs-pin").click(); });
out.unpinned = await until(async () => { const v = await version(cfg.versionB); return !(v.settingsPinned && v.settingsPinned["task-tracking"]); }, 30);
await setF.waitForFunction(() => { const row = document.getElementById("rs-tasktrack").closest("label"); const g = row.querySelector(".rs-pin"); return g && g.getAttribute("aria-pressed") === "false"; }, null, { timeout: 25000 }).catch(() => {});
out.afterUnpin = { b: await version(cfg.versionB), gear: await survey(setF) };
// (5) All kernels: a click applies to both
await setF.evaluate(() => { document.getElementById("rs-scope-btn").click(); Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).find((o) => o.textContent.trim() === "All kernels").click(); });
await setF.waitForFunction(() => document.getElementById("rs-scope-label").textContent === "All kernels", null, { timeout: 5000 }).catch(() => {});
await setF.waitForTimeout(900);
out.allAgain = await survey(setF);
await setF.click("#rs-tasktrack");
out.bothApplied = await until(async () => { const a = await version(cfg.versionA), b = await version(cfg.versionB); return a.taskTracking === false && b.taskTracking === false; }, 40);
out.afterAllClick = { a: await version(cfg.versionA), b: await version(cfg.versionB) };
// (6) the verifier's HIGH: a scoped click that makes the kernels DISAGREE (B on, A off) must raise NO proposal card on A; the
// flag says they differ; and the Remote kernels popover marks B as pinned
// A's row for B must first catch up with the All-kernels click (B off), or the scoped box still reads B's old value and the
// click would make the kernels AGREE again (the first cut of this leg did exactly that)
out.rowCaughtUp = await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.host === cfg.peer && t.settings && t.settings.taskTracking === false); }, 60);
await setF.evaluate((peer) => { document.getElementById("rs-scope-btn").click(); const opt = Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).find((o) => o.textContent.trim() === peer); opt.querySelector("input").click(); document.getElementById("rs-scope-btn").click(); }, cfg.peer);
await setF.waitForFunction(() => document.getElementById("rs-scope-label").textContent !== "All kernels", null, { timeout: 5000 }).catch(() => {});
await setF.waitForFunction(() => { const b = document.getElementById("rs-tasktrack"); return !b.indeterminate && b.checked === false; }, null, { timeout: 15000 }).catch(() => {});
out.beforeDisagreeClick = await survey(setF);
await setF.click("#rs-tasktrack");
out.disagreeApplied = await until(async () => { const b = await version(cfg.versionB); return b.taskTracking === true && b.settingsPinned && b.settingsPinned["task-tracking"]; }, 30);
// A's poll of B reads B's newer stamp AND its pin: wait for A's row to carry the pin, then two more polls' worth, then read A
await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.host === cfg.peer && t.settingsPinned && t.settingsPinned["task-tracking"]); }, 60);
await new Promise((r) => setTimeout(r, 12000));
await setF.waitForFunction(() => { const row = document.getElementById("rs-tasktrack").closest("label"); const m = row.querySelector(".rs-mixed"); return m && !m.hidden && m.textContent === "differs"; }, null, { timeout: 25000 }).catch(() => {});
out.disagree = { a: await version(cfg.versionA), b: await version(cfg.versionB), gear: await survey(setF) };
await closeSettings(setF);
await page.click("#rail-net").catch(() => {});
await page.waitForFunction(() => { const b = document.getElementById("rnet-back"); return !!b && !b.hidden; }, null, { timeout: 8000 }).catch(() => {});
await page.waitForFunction((peer) => Array.from(document.querySelectorAll("#rnet-list .rnet-row")).some((r) => r.textContent.includes(peer)), cfg.peer, { timeout: 15000 }).catch(() => {});
out.popover = await page.evaluate((peer) => { const row = Array.from(document.querySelectorAll("#rnet-list .rnet-row")).find((r) => r.textContent.includes(peer)); if (!row) return null; const pin = row.querySelector(".rnet-pin");
  return { text: row.textContent.replace(/\s+/g, " ").trim().slice(0, 120), pin: pin ? { text: pin.textContent, title: pin.title } : null }; }, cfg.peer);
await page.keyboard.press("Escape").catch(() => {});
// (7) a click on a MIXED box under a pick (the #377 review): picked to both kernels while they disagree (A off, B on from (6)),
// the row is mixed, held ticked under the dash, and its hover names the two; the click turns it OFF on both, and once the row
// reads the kernels' new agreement a second click turns it ON on both
setF = await openSettings();
await setF.evaluate((peer) => {
  if (document.getElementById("rs-scope-list").hidden) document.getElementById("rs-scope-btn").click();
  for (const opt of Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt"))) {
    const name = opt.textContent.trim(), inp = opt.querySelector("input");
    if (inp && !inp.checked && (name === peer || name.includes("(this machine)"))) inp.click();
  }
  if (!document.getElementById("rs-scope-list").hidden) document.getElementById("rs-scope-btn").click();
}, cfg.peer);
await setF.waitForFunction((peer) => { const lbl = document.getElementById("rs-scope-label").textContent, b = document.getElementById("rs-tasktrack"), m = b.closest("label").querySelector(".rs-mixed");
  return lbl.includes(peer) && lbl.includes("(this machine)") && b.indeterminate && !!m && !m.hidden && / turns it off for /.test(m.title); }, cfg.peer, { timeout: 25000 }).catch(() => {});
out.pickBoth = await survey(setF);
await setF.click("#rs-tasktrack");
out.pickBothOff = await until(async () => { const a = await version(cfg.versionA), b = await version(cfg.versionB); return a.taskTracking === false && b.taskTracking === false; }, 30);
// the second click meets the row as the kernels now stand: A's row for B carries B's off, and a fresh read draws the row plain
out.pickBothCaughtUp = await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.host === cfg.peer && t.settings && t.settings.taskTracking === false); }, 60);
await closeSettings(setF);
setF = await openSettings();
await setF.waitForFunction(() => { const b = document.getElementById("rs-tasktrack"); return !b.indeterminate && !b.checked; }, null, { timeout: 15000 }).catch(() => {});
out.pickBothBetween = await survey(setF);
await setF.click("#rs-tasktrack");
out.pickBothOn = await until(async () => { const a = await version(cfg.versionA), b = await version(cfg.versionB); return a.taskTracking === true && b.taskTracking === true; }, 30);
out.afterPickBoth = { a: (await version(cfg.versionA)).taskTracking, b: (await version(cfg.versionB)).taskTracking };
await browser.close();
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n", () => process.exit(0));
"""


def _seed(state, cwd, claude, sid, name, task_tracking_off=False):
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    Path(state, "names", sid).write_text("%s\t%s\t#1EA1EB\t#ffffff\n" % (name, cwd))
    Path(state, "sdk", sid + ".json").write_text(json.dumps(
        {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
         "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
    t0 = int(time.time()) - 3600
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
    recs = [{"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": iso(t0), "sessionId": sid,
             "message": {"role": "user", "content": "a question about the notes api"}},
            {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": iso(t0 + 2), "sessionId": sid,
             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "the notes api keeps its shape."}]}}]
    Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    if task_tracking_off:
        Path(state, "task-tracking.json").write_text(json.dumps({"enabled": False, "gt": 2000}))


class ServedMachineSelector(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def _boot(cls, tag, seed_off):
        lab = os.path.join(cls.lab, tag)
        state = os.path.join(lab, "xdg", "romp")
        claude = os.path.join(lab, "claude")
        _seed(state, os.path.join(lab, "notes-api"), claude, SID_A if tag == "a" else SID_B, "web" if tag == "a" else "api", task_tracking_off=seed_off)
        port, token = _free_port(), "testtok-selector-" + tag
        env = _lab.kernel_env(lab, claude, cls.dist, port, token, ROMP_HOST_NAME=("TESTHOSTA" if tag == "a" else PEER))
        log = os.path.join(lab, "kernel.log")
        proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            proc.kill()
            cls._skip("hermetic kernel %s never served /healthz here" % tag)
        return {"proc": proc, "port": port, "token": token, "log": log, "state": state}

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here): the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="machine-selector-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        cls.dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), cls.dist)
        cls.a = cls._boot("a", seed_off=False)
        cls.b = cls._boot("b", seed_off=True)
        cls._r = None

    @classmethod
    def tearDownClass(cls):
        for k in ("a", "b"):
            k = getattr(cls, k, None)
            if k and k.get("proc"):
                k["proc"].kill()
                k["proc"].wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _checkin_cfg(self):
        """B's check-in to A as a peer (the mobile handshake's shape), for the driver to post: A then polls B directly on
        loopback, no ssh."""
        return {"url": "http://127.0.0.1:%d/checkin" % self.a["port"], "token": self.a["token"],
                "body": {"host": PEER, "kernelPort": self.b["port"], "busPort": _free_port(), "token": self.b["token"]}}

    def _rows(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (self.a["port"], self.a["token"]), timeout=5) as r:
                rows = json.loads(r.read().decode("utf-8")).get("tunnels") or []
        except Exception as e:
            return {"error": str(e)}
        return [{k: t.get(k) for k in ("host", "status", "detail", "checkinPeer", "settings", "settingsPinned")} for t in rows]

    def _result(self):
        if getattr(type(self), "_fail", None):
            self.fail(type(self)._fail)
        if self._r is None:
            base_a = "http://127.0.0.1:%d" % self.a["port"]
            base_b = "http://127.0.0.1:%d" % self.b["port"]
            cfg = os.path.join(self.lab, "cfg.json")
            with open(cfg, "w") as f:
                json.dump({"landing": base_a + "/?token=" + self.a["token"], "tunnels": base_a + "/tunnels?token=" + self.a["token"],
                           "versionA": base_a + "/version?token=" + self.a["token"], "versionB": base_b + "/version?token=" + self.b["token"],
                           "peer": PEER, "checkin": self._checkin_cfg()}, f)
            driver = os.path.join(self.lab, "selector.mjs")
            Path(driver).write_text(DRIVER)
            # the driver surveys the one-kernel state first, then checks B in itself and waits for A's row (the order is the
            # event's: a check-in from here after a fixed sleep raced the page's first read on a loaded runner)
            p = subprocess.Popen(["node", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            try:
                stdout, stderr = p.communicate(timeout=400)
            except subprocess.TimeoutExpired:
                p.kill(); stdout, stderr = p.communicate()
            type(self)._rows_seen = self._rows()   # the rows A keeps, as the run left them: what the poll made of the check-in
            if "browser-launch-failed" in stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in stdout.splitlines() if ln.startswith("RESULT:")), None)
            if line is None:
                type(self)._fail = "the driver produced no RESULT (stderr: %s; kernel A: %s; kernel B: %s; rows %r)" % (
                    stderr[-2000:], open(self.a["log"]).read()[-1500:], open(self.b["log"]).read()[-800:], type(self)._rows_seen)
                self.fail(type(self)._fail)
            type(self)._r = json.loads(line[len("RESULT:"):])
            type(self)._checkin_ack = self._r.get("checkinAck") or {}
        print("SELECTOR:", json.dumps(self._r), file=sys.stderr)
        return self._r

    def test_with_one_kernel_the_selector_is_hidden_and_the_settings_look_as_today(self):
        r = self._result()
        self.assertEqual(r["errors"], [], "no page errors")
        self.assertEqual([e for e in r["consoleErrors"] if "romp:" in e], [], "no fault said by the gear's fill")
        a = r["alone"]
        self.assertTrue(a["scopeHidden"], "one kernel: no selector")
        self.assertIsNone(a["mark"], "no disagreement to flag with one kernel")
        self.assertTrue(a["pin"]["hidden"], "no pin glyph with one kernel")

    def test_with_two_kernels_the_selector_stands_above_the_tabs_and_the_disagreeing_row_wears_the_flag(self):
        r = self._result()
        self.assertTrue(type(self)._checkin_ack.get("ok"), type(self)._checkin_ack)
        self.assertTrue(r["fedOpen"], "the settings page's link to B opened before any click aimed there")
        t = r["two"]
        self.assertFalse(t["scopeHidden"], "two kernels: the selector shows (A's rows: %r; the page's read: %r; kernel A's log tail: %s)"
                         % (type(self)._rows_seen, r.get("tunnels"), open(self.a["log"]).read()[-1200:]))
        self.assertTrue(t["aboveTabs"], "…above the tabs")
        self.assertEqual(t["label"], "All kernels")
        self.assertEqual(t["count"], "1 differs", "the count of differing rows on the selector itself")
        self.assertIn("Task tracking: ", t["countTitle"], "the row named as the gear names it")
        self.assertIsNotNone(t["mark"], "the Task tracking row wears the flag")
        self.assertEqual((t["mark"]["text"], t["mark"]["differs"]), ("differs", True))
        self.assertIn(PEER + " off", t["mark"]["title"]); self.assertIn("TESTHOSTA (this machine) on", t["mark"]["title"])
        self.assertEqual(t["mark"]["color"], "rgb(215, 162, 58)", "the heads-up amber, through the token")
        self.assertEqual((t["pin"]["hidden"], t["pin"]["lit"]), (False, False), "the glyph shows, unlit: nothing pinned yet")
        self.assertTrue(t["indeterminate"], "All kernels shows the MIXED state while the kernels disagree, never this kernel's on as everyone's")
        self.assertTrue(t["checked"], "…held TICKED under the dash, so a click turns it OFF for every kernel, the one rule a mixed box"
                                      " follows: off is the side that does nothing (the #377 review; #377 held it unticked, so a click turned on)")
        self.assertIn("A click turns it off for every machine; click again to turn it on. Pick a machine above to set it alone.",
                      t["mark"]["title"], "…and the hover says so")
        self.assertEqual(t["options"][:1], ["All kernels"]); self.assertIn(PEER, t["options"]); self.assertTrue(any("this machine" in o for o in t["options"]))

    def test_picking_the_peer_scopes_the_row_to_its_value_and_a_click_applies_there_alone_and_pins_it(self):
        r = self._result()
        s = r["scopedB"]
        self.assertEqual(s["label"], PEER, "the button names the picked kernel")
        self.assertFalse(s["checked"], "the row reads B's value (off)")
        self.assertFalse(s["indeterminate"])
        after = r["afterScopedClick"]
        self.assertTrue(after["b"]["taskTracking"], "the click applied ON to B")
        self.assertEqual(after["b"]["settingsPinned"], {"task-tracking": True}, "…and PINNED it there")
        self.assertEqual(after["b"]["settingsGt"]["task-tracking"], after["b"]["settingsGt"]["task-tracking"])
        self.assertTrue(after["a"]["taskTracking"], "A unchanged (it was on)")
        self.assertEqual(after["a"].get("settingsPinned"), {}, "A not pinned: the change was scoped to B")
        self.assertTrue(after["gear"]["pin"]["lit"], "the glyph lights for the picked kernel")
        self.assertIn("Pinned on " + PEER, after["gear"]["pin"]["title"])

    def test_a_scoped_click_that_makes_the_kernels_disagree_raises_no_card_here_and_the_popover_marks_the_pinned_machine(self):
        r = self._result()
        self.assertTrue(r["disagreeApplied"], "the scoped click applied ON to B and pinned it while A stayed off")
        d = r["disagree"]
        self.assertEqual((d["a"]["taskTracking"], d["b"]["taskTracking"]), (False, True), "the kernels disagree now")
        self.assertEqual(d["b"]["settingsPinned"], {"task-tracking": True})
        self.assertEqual(d["a"].get("settingsProposals") or {}, {}, "B's PINNED store raises no proposal on A (the verifier's HIGH: the card's Apply would undo the scoping)")
        recs = json.loads(Path(self.a["state"], "settings-proposals.json").read_text()) if os.path.exists(os.path.join(self.a["state"], "settings-proposals.json")) else {}
        self.assertEqual((recs.get("task-tracking") or {}).get("hosts") or {}, {}, "no record on A for B's pinned store")
        notes = os.path.join(self.a["state"], "notices", "notes.jsonl")
        posted = [json.loads(l) for l in open(notes)] if os.path.exists(notes) else []
        self.assertEqual([p for p in posted if p.get("op") == "post" and str(p.get("key", "")).startswith("proposal.task-tracking")], [], "no card posted on A")
        self.assertIsNotNone(d["gear"]["mark"], "the flag is the surface that says they differ")
        self.assertEqual(d["gear"]["mark"]["text"], "differs")
        self.assertEqual(d["gear"]["count"], "1 differs")
        self.assertIsNotNone(r["popover"], "the Remote kernels popover lists B")
        self.assertIsNotNone(r["popover"]["pin"], "…with the pinned mark: %r" % r["popover"])
        self.assertEqual(r["popover"]["pin"]["text"], "pinned"); self.assertIn("task-tracking", r["popover"]["pin"]["title"])

    def test_un_pinning_from_the_glyph_clears_the_peers_pin_and_all_kernels_applies_to_both(self):
        r = self._result()
        u = r["afterUnpin"]
        self.assertEqual(u["b"].get("settingsPinned"), {}, "the un-pin reached B through the scope")
        self.assertTrue(u["b"]["taskTracking"], "no newer different value stands elsewhere: B keeps on")
        self.assertFalse(u["gear"]["pin"]["lit"])
        self.assertEqual(r["allAgain"]["label"], "All kernels")
        both = r["afterAllClick"]
        self.assertEqual((both["a"]["taskTracking"], both["b"]["taskTracking"]), (False, False), "under All kernels one click sets both")

    def test_picked_to_both_disagreeing_kernels_a_click_turns_the_row_off_on_both_and_a_second_click_on(self):
        r = self._result()
        p = r["pickBoth"]
        self.assertIn(PEER, p["label"]); self.assertIn("(this machine)", p["label"])
        self.assertTrue(p["indeterminate"], "the picked kernels disagree (A off, B on): the row is mixed: %r" % p)
        self.assertTrue(p["checked"], "…held TICKED under the dash, so the click lands on off")
        self.assertIsNotNone(p["mark"])
        self.assertTrue(p["mark"]["title"].endswith(". A click turns it off for " + p["label"] + "."), "the hover names the picked kernels: %r" % p["mark"])
        self.assertTrue(r["pickBothOff"], "the click turned it OFF on both picked kernels")
        self.assertTrue(r["pickBothCaughtUp"], "A's row for B caught up with B's off")
        b = r["pickBothBetween"]
        self.assertEqual((b["checked"], b["indeterminate"]), (False, False), "agreeing off, the row reads plain off: %r" % b)
        self.assertTrue(r["pickBothOn"], "the second click turned it ON on both: %r" % r["afterPickBoth"])


if __name__ == "__main__":
    unittest.main()
