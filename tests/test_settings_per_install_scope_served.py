"""The settings' PER-INSTALL rows across two lab kernels (the settings' scope fan-out, plans/settings-across-machines.md): kernel A
serves the dashboard; kernel B checks in to A as a peer (the check-in handshake, no ssh), so A polls B's /version and its /tunnels
row carries B's settings. A boots with Thinking summaries ON, B with no store (off), so the two disagree on a setting each kernel
keeps for itself and nothing converges.

The bug this pins (the user's two kernels, 2026-10-23): under the gear's "All kernels" the Thinking summaries box read plainly
CHECKED, with no mark and no count on the selector, while the other kernel had it off; and a click on it, or on Always load whole
chats, Conserve memory or Extra models, went to the local kernel alone. Driven in a real browser over A's landing page:
(1) under All kernels the disagreeing row shows the MIXED state (indeterminate) and wears the "differs" flag naming both machines
and their values, and the selector's count includes it; (2) a click there sets EVERY kernel (both /version agree, B's store holds
the click's stamp), and Conserve memory's click does too, stamped; (3) agreeing again, the row is plain and unflagged; (4) picked
to B alone, a click applies to B and leaves A, never pinning (nothing converges a per-install store). Synthetic throughout:
TESTHOSTA, TESTHOSTB, placeholder sids, invented text. Skips LOUDLY without the extension deps or a Playwright browser (CI sets
ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a failure)."""
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

SID_A = "11111111-2222-3333-4444-555555555577"
SID_B = "11111111-2222-3333-4444-666666666677"
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
const page = await ctx.newPage();
const errors = [], consoleErrors = []; page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 400)); });
// the kernels' /version read from NODE (kernel B is another origin: the page may not fetch it)
const version = async (u) => (await (await fetch(u, { cache: "no-store" })).json());
const until = async (fn, tries) => { for (let i = 0; i < tries; i++) { if (await fn()) return true; await new Promise((r) => setTimeout(r, 500)); } return false; };
const out = { errors, consoleErrors };
// A's row for B carries B's settings once the poll has read it after the check-in (the test checks in before the driver starts)
out.rowUp = await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.status === "up" && t.settings && typeof t.settings.conserveMemory === "boolean"); }, 120);
await page.goto(cfg.landing);
await page.waitForSelector("#rail-gear", { timeout: 20000 });
const frameBy = async (part) => { let f = page.frames().find((x) => x.url().includes(part)); for (let i = 0; i < 100 && !f; i++) { await page.waitForTimeout(100); f = page.frames().find((x) => x.url().includes(part)); } return f; };
const openSettings = async () => {
  await page.evaluate(() => window.__rompOpenSettings("chat"));
  await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  const f = await frameBy("/settings");
  await f.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
  await f.waitForFunction(() => { const sc = document.getElementById("rs-scope"); return !!sc && !sc.hidden; }, null, { timeout: 15000 }).catch(() => {});
  await f.waitForTimeout(600);   // the /tunnels read lands the marks and the count a beat after the selector shows
  return f;
};
const closeSettings = async (f) => { await f.evaluate(() => window.__rompSettingsClose && window.__rompSettingsClose()); await page.waitForTimeout(400); };
const row = (f, id) => f.evaluate((id) => {
  const el = document.getElementById(id); if (!el) return null;
  const r = el.closest("label") || el.closest(".rs-row"); const m = r && r.querySelector(".rs-mixed");
  const cnt = document.getElementById("rs-scope-count");
  return { checked: el.checked, indeterminate: el.indeterminate,
           mark: m && !m.hidden ? { text: m.textContent, differs: m.classList.contains("rs-differs"), title: m.title } : null,
           label: document.getElementById("rs-scope-label").textContent,
           count: cnt && !cnt.hidden ? cnt.textContent : "", countTitle: cnt ? cnt.title : "" };
}, id);
const tab = async (f, name) => { await f.click('.rs-tab[data-tab="' + name + '"]'); await f.waitForTimeout(250); };
// (1) All kernels, the kernels disagreeing on Thinking summaries (A on, B off)
let f = await openSettings();
out.disagree = await row(f, "rs-thinksum");
// (2) a click under All kernels sets every kernel: the first takes the mixed box to off on both, the second to on on both
await tab(f, "chat");
await f.click("#rs-thinksum");
out.firstOff = await until(async () => (await version(cfg.versionA)).thinkingSummaries === false && (await version(cfg.versionB)).thinkingSummaries === false, 30);
await f.click("#rs-thinksum");
out.bothOn = await until(async () => (await version(cfg.versionA)).thinkingSummaries === true && (await version(cfg.versionB)).thinkingSummaries === true, 30);
await tab(f, "general");
await f.click("#rs-conserve");
out.conserveBoth = await until(async () => (await version(cfg.versionA)).conserveMemory === true && (await version(cfg.versionB)).conserveMemory === true, 30);
out.afterAll = { a: await version(cfg.versionA), b: await version(cfg.versionB) };
await closeSettings(f);
// (3) agreeing now: once A's row for B carries B's new value, the re-opened row is plain
out.rowCaughtUp = await until(async () => { const d = await version(cfg.tunnels); return (d.tunnels || []).some((t) => t.host === cfg.peer && t.settings && t.settings.thinkingSummaries === true); }, 60);
f = await openSettings();
out.agree = await row(f, "rs-thinksum");
// (4) picked to B alone: the row reads B's value; a click applies to B and leaves A
await f.evaluate((peer) => { document.getElementById("rs-scope-btn").click(); const opt = Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).find((o) => o.textContent.trim() === peer); if (opt) opt.querySelector("input").click(); document.getElementById("rs-scope-btn").click(); }, cfg.peer);
await f.waitForFunction(() => document.getElementById("rs-scope-label").textContent !== "All kernels", null, { timeout: 5000 }).catch(() => {});
await f.waitForTimeout(900);
await tab(f, "chat");
out.scoped = await row(f, "rs-thinksum");
await f.click("#rs-thinksum");
out.scopedApplied = await until(async () => (await version(cfg.versionB)).thinkingSummaries === false, 30);
await page.waitForTimeout(1500);   // a stray copy to A would land in this window
out.afterScoped = { a: await version(cfg.versionA), b: await version(cfg.versionB) };
await browser.close();
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n", () => process.exit(0));
"""


class ServedPerInstallScope(unittest.TestCase):
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
        if tag == "a":   # A on, B with no store (off): the disagreement under test
            Path(state, "thinking-summaries.json").write_text(json.dumps({"enabled": True, "gt": 2000}))
        port, token = _free_port(), "testtok-perinstall-" + tag
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
        cls.lab = tempfile.mkdtemp(prefix="per-install-scope-")
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
            body = json.dumps({"host": PEER, "kernelPort": self.b["port"], "busPort": _free_port(), "token": self.b["token"]}).encode("utf-8")
            req = urllib.request.Request("http://127.0.0.1:%d/checkin" % self.a["port"], data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Romp-Token": self.a["token"]})
            with urllib.request.urlopen(req, timeout=10) as r:
                ack = json.loads(r.read().decode("utf-8"))
            base_a = "http://127.0.0.1:%d" % self.a["port"]
            base_b = "http://127.0.0.1:%d" % self.b["port"]
            cfg = os.path.join(self.lab, "cfg.json")
            with open(cfg, "w") as f:
                json.dump({"landing": base_a + "/?token=" + self.a["token"], "tunnels": base_a + "/tunnels?token=" + self.a["token"],
                           "versionA": base_a + "/version?token=" + self.a["token"], "versionB": base_b + "/version?token=" + self.b["token"],
                           "peer": PEER}, f)
            driver = os.path.join(self.lab, "per-install.mjs")
            Path(driver).write_text(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=400,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if "browser-launch-failed" in p.stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            if line is None:
                type(self)._fail = "the driver produced no RESULT (stderr: %s; check-in ack %r; kernel A: %s; kernel B: %s)" % (
                    p.stderr[-2000:], ack, open(self.a["log"]).read()[-1500:], open(self.b["log"]).read()[-800:])
                self.fail(type(self)._fail)
            type(self)._r = json.loads(line[len("RESULT:"):])
        print("PER-INSTALL:", json.dumps(self._r), file=sys.stderr)
        return self._r

    def test_a_disagreeing_per_install_row_renders_mixed_with_the_differs_flag_and_counts_on_the_selector(self):
        r = self._result()
        self.assertEqual(r["errors"], [], "no page errors")
        self.assertEqual([e for e in r["consoleErrors"] if "romp:" in e], [], "no fault said by the gear's fill")
        self.assertTrue(r["rowUp"], "A's row for B came up with B's settings")
        d = r["disagree"]
        self.assertEqual(d["label"], "All kernels")
        self.assertTrue(d["indeterminate"], "the box shows the MIXED state, never this kernel's on as every machine's: %r" % d)
        self.assertIsNotNone(d["mark"], "the row wears the flag")
        self.assertEqual((d["mark"]["text"], d["mark"]["differs"]), ("differs", True))
        self.assertIn(SELF + " (this machine) on", d["mark"]["title"])
        self.assertIn(PEER + " off", d["mark"]["title"])
        self.assertEqual(d["count"], "1 differs", "the selector counts the per-install row too")
        self.assertIn("Thinking summaries: ", d["countTitle"])

    def test_a_click_under_all_kernels_sets_every_kernel(self):
        r = self._result()
        self.assertTrue(r["firstOff"], "the click on the mixed box set off on both")
        self.assertTrue(r["bothOn"], "the next click set on on BOTH kernels, not this one alone: %r" % r["afterAll"])
        a, b = r["afterAll"]["a"], r["afterAll"]["b"]
        self.assertEqual((a["thinkingSummaries"], b["thinkingSummaries"]), (True, True))
        self.assertEqual(b["settingsGt"]["thinking-summaries"], a["settingsGt"]["thinking-summaries"], "B applied the click's own stamp")
        self.assertTrue(r["conserveBoth"], "Conserve memory's click reached both kernels too")
        self.assertEqual((a["conserveMemory"], b["conserveMemory"]), (True, True))
        self.assertGreater(b["settingsGt"]["conserve-memory"], 0, "…stamped, so a late flush cannot undo a newer pick")
        self.assertEqual(b.get("settingsPinned") or {}, {}, "nothing pinned: a broadcast is not a scope")

    def test_agreeing_again_the_row_is_plain(self):
        r = self._result()
        self.assertTrue(r["rowCaughtUp"])
        g = r["agree"]
        self.assertTrue(g["checked"])
        self.assertFalse(g["indeterminate"])
        self.assertIsNone(g["mark"], "no flag once the kernels agree")
        self.assertEqual(g["count"], "", "nothing to count")

    def test_picked_to_the_peer_a_click_applies_there_alone_and_pins_nothing(self):
        r = self._result()
        s = r["scoped"]
        self.assertEqual(s["label"], PEER)
        self.assertEqual((s["checked"], s["indeterminate"]), (True, False), "the row reads B's value")
        self.assertTrue(r["scopedApplied"], "the click applied off to B")
        a, b = r["afterScoped"]["a"], r["afterScoped"]["b"]
        self.assertEqual((a["thinkingSummaries"], b["thinkingSummaries"]), (True, False), "A untouched: the change was scoped to B")
        self.assertEqual(b.get("settingsPinned") or {}, {}, "a per-install store has nothing to pin against")


if __name__ == "__main__":
    unittest.main()
