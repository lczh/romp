"""The settings panel tells the truth about the machines (the gear-truth review, 2026-10-23), end to end over two lab kernels.
Kernel A serves the dashboard; kernel B checks in to A as a peer (the check-in handshake, no ssh), so A polls B's /version and its
/tunnels row carries B's settings. B boots with Thinking summaries, Conserve memory and Always load whole chats ON; A with all
three off, so each row reads mixed under All kernels, and a click on a mixed box is OFF for every machine (the stated rule).

(2) A setting for a machine the page has not linked yet is delivered. The settings page links a machine only from its /tunnels
    poll, every 4 s, so for a few seconds after a machine checks in the gear shows it while the page holds no link to it; an All
    kernels click then went to the machines the page had already linked and skipped the new one without a word. Here the page's
    4 s poll never runs (its interval is not scheduled in the settings frame), B checks in after the page loaded, and the click
    on Thinking summaries reaches B all the same (the gear names the machines it shows; federation holds the setting, polls at
    once, links B and delivers on the open).
(3) After a click turns both machines off, a second click turns both on. B acknowledges the setting (settingAck), the gear asks A
    to re-read B (/tunnels?reread=TESTHOSTB), and past the gear's first re-read after the click (700 ms) the row reads plain off;
    before, A's copy of B stayed from its supervisor's last pass, the row went back to the dash, and the second click posted
    off again. The second click lands as soon as the re-read is in, and at least 800 ms after the first.
(1) A click on a mixed box while the next fill's /tunnels answer is on its way turns nothing on: the box holds the dash ticked,
    the click posts off, and the late answer draws nothing.
Synthetic throughout: TESTHOSTA, TESTHOSTB, placeholder sids, invented text. Skips LOUDLY without the extension deps or a
Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a failure)."""
import json
import os
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
from test_settings_machine_selector_served import _seed  # noqa: E402  the two-kernel lab's synthetic session

SID_A = "11111111-2222-3333-4444-555555555588"
SID_B = "11111111-2222-3333-4444-666666666688"
SELF = "TESTHOSTA"
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
// The settings page's federation polls /tunnels at its start and then every 4 s; here the 4 s tick is never scheduled in the
// settings frame, so the page links only the machines its first poll listed (none: B checks in after the load) unless something
// else makes it poll. Every other interval runs as written.
await ctx.addInitScript(() => {
  if (location.pathname !== "/settings") return;
  const si = window.setInterval.bind(window);
  window.setInterval = (fn, ms, ...a) => (ms === 4000 ? 0 : si(fn, ms, ...a));
});
const page = await ctx.newPage();
const errors = [], consoleErrors = []; page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 400)); });
const version = async (u) => (await (await fetch(u, { cache: "no-store" })).json());
const until = async (fn, tries, ms) => { for (let i = 0; i < tries; i++) { if (await fn()) return true; await new Promise((r) => setTimeout(r, ms || 200)); } return false; };
const out = { errors, consoleErrors };
// the settings frame's /tunnels reads: recorded with their query and when they finished. While `hold` is set each one is answered
// by A at once and the answer held back from the page until release, so it lands carrying the machines as they stood when asked
const tunnelsDone = []; let hold = false; const held = []; let heldArrived = null;
await ctx.route(/\/tunnels(\?|$)/, async (route) => {
  const f = route.request().frame();
  if (!hold || !f || !f.url().includes("/settings")) return route.continue();
  const response = await route.fetch();
  held.push(() => route.fulfill({ response }));
  if (heldArrived) { heldArrived(); heldArrived = null; }
});
page.on("requestfinished", (r) => { const u = new URL(r.url()); if (u.pathname === "/tunnels" && r.frame() && r.frame().url().includes("/settings")) tunnelsDone.push({ q: u.search, t: Date.now() }); });
await page.goto(cfg.landing);
await page.waitForSelector("#rail-gear", { timeout: 20000 });
const frameBy = async (part) => { let f = page.frames().find((x) => x.url().includes(part)); for (let i = 0; i < 100 && !f; i++) { await page.waitForTimeout(100); f = page.frames().find((x) => x.url().includes(part)); } return f; };
const openSettings = async (tab) => {
  await page.evaluate((tab) => window.__rompOpenSettings(tab), tab);
  await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  const f = await frameBy("/settings");
  await f.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
  return f;
};
const closeSettings = async (f) => { await f.evaluate(() => window.__rompSettingsClose && window.__rompSettingsClose()); await page.waitForTimeout(300); };
// the settings page is loaded and its federation has made its first poll (B not attached yet); its 4 s tick never comes
let f = await openSettings("chat");
await page.waitForTimeout(1500);
await closeSettings(f);
// B checks in NOW, after the settings page loaded
const ck = await fetch(cfg.checkin, { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.tokenA }, body: JSON.stringify(cfg.checkinBody) });
out.checkin = ck.status;
out.rowUp = await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.host === cfg.peer && t.status === "up" && t.settings && typeof t.settings.conserveMemory === "boolean"); }, 300);
const box = (f, id) => f.evaluate((id) => { const el = document.getElementById(id); const m = el.closest("label").querySelector(".rs-mixed");
  return { checked: el.checked, indeterminate: el.indeterminate, mark: m && !m.hidden ? m.textContent : null }; }, id);
const tab = async (f, name) => { await f.click('.rs-tab[data-tab="' + name + '"]'); await f.waitForTimeout(250); };
const mixed = (f, id) => f.waitForFunction((id) => document.getElementById(id).indeterminate, id, { timeout: 15000 }).then(() => true, () => false);

// (2) the page has not linked B: a click under All kernels still reaches it
f = await openSettings("chat");
await f.waitForFunction(() => { const sc = document.getElementById("rs-scope"); return !!sc && !sc.hidden; }, null, { timeout: 15000 }).catch(() => {});
out.thinksumMixed = await mixed(f, "rs-thinksum");
out.thinksumBefore = await box(f, "rs-thinksum");
await f.click("#rs-thinksum");
out.unlinkedDelivered = await until(async () => (await version(cfg.versionB)).thinkingSummaries === false, 75);
out.afterUnlinked = { a: (await version(cfg.versionA)).thinkingSummaries, b: (await version(cfg.versionB)).thinkingSummaries };

// (3) Conserve memory: the click turns both off; B's acknowledgement has A re-read it, so the row reads plain off and a second
// click turns both on
await tab(f, "general");
out.conserveMixed = await mixed(f, "rs-conserve");
const t0 = Date.now(), done0 = tunnelsDone.length;
await f.click("#rs-conserve");
const reread = await until(async () => tunnelsDone.slice(done0).some((x) => x.q === "?reread=" + cfg.peer), 100, 20);
out.rereadAfterMs = reread ? tunnelsDone.slice(done0).find((x) => x.q === "?reread=" + cfg.peer).t - t0 : null;
await page.waitForTimeout(Math.max(80, 800 - (Date.now() - t0)));   // past the gear's first re-read after the click (700 ms)
out.conserveBetween = await box(f, "rs-conserve");
out.secondClickAfterMs = Date.now() - t0;
await f.click("#rs-conserve");
out.bothOn = await until(async () => (await version(cfg.versionA)).conserveMemory === true && (await version(cfg.versionB)).conserveMemory === true, 75);
out.afterConserve = { a: (await version(cfg.versionA)).conserveMemory, b: (await version(cfg.versionB)).conserveMemory };

// (1) Always load whole chats: the next fill's /tunnels answer held, the click on the dash posts off, and nothing turns on
await tab(f, "chat");
out.wholeMixed = await mixed(f, "rs-wholechat");
await closeSettings(f);
hold = true;
const arrived = new Promise((r) => { heldArrived = r; });
f = await openSettings("chat");
await Promise.race([arrived, new Promise((r) => setTimeout(r, 10000))]);
await page.waitForTimeout(150);
out.wholeDuring = await box(f, "rs-wholechat");
await f.click("#rs-wholechat");
await page.waitForTimeout(400);
hold = false;
for (const release of held.splice(0)) await release().catch(() => {});
await page.waitForTimeout(3000);   // the click's copies land and the re-reads after it run
out.afterWhole = { a: (await version(cfg.versionA)).wholeChatFrames, b: (await version(cfg.versionB)).wholeChatFrames };
out.wholeAfter = await box(f, "rs-wholechat");
await browser.close();
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n", () => process.exit(0));
"""


class ServedGearTruth(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def _boot(cls, tag):
        lab = os.path.join(cls.lab, tag)
        state = os.path.join(lab, "xdg", "romp")
        claude = os.path.join(lab, "claude")
        _seed(state, os.path.join(lab, "notes-api"), claude, SID_A if tag == "a" else SID_B, "web" if tag == "a" else "api")
        if tag == "b":   # B on for all three, A with no stores (off): the disagreements under test
            for name in ("thinking-summaries", "conserve-memory", "whole-chat-frames"):
                Path(state, name + ".json").write_text(json.dumps({"enabled": True, "gt": 2000}))
        port, token = _free_port(), "testtok-geartruth-" + tag
        env = _lab.kernel_env(lab, claude, cls.dist, port, token, ROMP_HOST_NAME=(SELF if tag == "a" else PEER))
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
        cls.lab = tempfile.mkdtemp(prefix="gear-truth-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        cls.dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), cls.dist)
        cls.a = cls._boot("a")
        cls.b = cls._boot("b")
        cls._r = None

    @classmethod
    def tearDownClass(cls):
        for k in ("a", "b"):
            k = getattr(cls, k, None)
            if k and k.get("proc"):
                k["proc"].kill()
                k["proc"].wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

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
                           "checkin": base_a + "/checkin", "tokenA": self.a["token"],
                           "checkinBody": {"host": PEER, "kernelPort": self.b["port"], "busPort": _free_port(), "token": self.b["token"]},
                           "peer": PEER}, f)
            driver = os.path.join(self.lab, "gear-truth.mjs")
            Path(driver).write_text(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=400,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if "browser-launch-failed" in p.stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            if line is None:
                type(self)._fail = "the driver produced no RESULT (stderr: %s; kernel A: %s; kernel B: %s)" % (
                    p.stderr[-2000:], open(self.a["log"]).read()[-1500:], open(self.b["log"]).read()[-800:])
                self.fail(type(self)._fail)
            type(self)._r = json.loads(line[len("RESULT:"):])
        print("GEAR-TRUTH:", json.dumps(self._r), file=sys.stderr)
        return self._r

    def test_the_lab_ran_clean(self):
        r = self._result()
        self.assertEqual(r["errors"], [], "no page errors")
        self.assertEqual([e for e in r["consoleErrors"] if "romp:" in e], [], "no fault said by the gear's fill")
        self.assertEqual(r["checkin"], 200)
        self.assertTrue(r["rowUp"], "A's row for B came up with B's settings")

    def test_a_click_reaches_a_machine_the_page_had_not_linked_yet(self):
        r = self._result()
        self.assertTrue(r["thinksumMixed"], "the gear showed B (its own read) while the page held no link to it: %r" % r["thinksumBefore"])
        self.assertEqual(r["thinksumBefore"]["mark"], "differs")
        self.assertTrue(r["unlinkedDelivered"], "the All kernels click reached B with the page's 4 s poll out of the picture: %r" % r["afterUnlinked"])
        self.assertEqual(r["afterUnlinked"], {"a": False, "b": False}, "off on both, the stated rule for a mixed box")

    def test_after_a_click_turns_both_off_a_second_click_turns_both_on(self):
        r = self._result()
        self.assertTrue(r["conserveMixed"])
        self.assertIsNotNone(r["rereadAfterMs"], "B's acknowledgement had the gear ask A to re-read B")
        self.assertEqual(r["conserveBetween"], {"checked": False, "indeterminate": False, "mark": None},
                         "past the gear's first re-read after the click the row reads plain off (A's copy of B is B as it stands), "
                         "not the dash that made the second click post off again")
        self.assertLess(r["secondClickAfterMs"], 1000, "the second click came within a second of the first")
        self.assertTrue(r["bothOn"], "the second click turned it on on BOTH machines: %r" % r["afterConserve"])
        self.assertEqual(r["afterConserve"], {"a": True, "b": True})

    def test_a_click_while_the_next_answer_is_on_its_way_turns_nothing_on(self):
        r = self._result()
        self.assertTrue(r["wholeMixed"])
        self.assertEqual((r["wholeDuring"]["indeterminate"], r["wholeDuring"]["checked"]), (True, True),
                         "while the answer is on its way the dash holds ticked (it used to hold this machine's off): %r" % r["wholeDuring"])
        self.assertEqual(r["afterWhole"], {"a": False, "b": False}, "nothing turned on: the click was off for every machine")
        self.assertEqual((r["wholeAfter"]["checked"], r["wholeAfter"]["indeterminate"]), (False, False), "and the row reads plain off")


if __name__ == "__main__":
    unittest.main()
