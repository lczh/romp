#!/usr/bin/env python3
"""Messages STAGED on a new session's tab while the session is still opening reach that session (2026-10-23), on the real
/chat page. A new session's tab is up the moment the create is asked for, under a provisional id of the page's own, and what
is staged there is held under that id. The page used to hand only the draft to the real session when it arrived: the staged
list stayed under the provisional id, never shown and never sent (#362 deleted it there; #369 made staged messages outlive
every end, which left this one place). Now it moves with the draft, a reload in between included, and a create that fails
keeps it on its tab and says so.

The lab drives the real page over the queued lab's boot (a hermetic kernel with one session, web): the + tab, a name and
Create session open the new tab as a user does, and the page's own createSession is dropped at the socket so the create stays
in flight for as long as the test needs. The text is staged with Ctrl+Enter. The kernel's answer is then posted to the page in
its own shape: a `session` frame for a session named api (built from web's frame, so every field the page reads is real) or,
for the failure, the sid-less `warn` a create's refusal is. Three roads, each in its own browser context: the arrival, a
reload before the arrival, and the failure (with a reload after it). Red before the fix on each road (the arrival leaves the
real tab's strip empty, the reload loses the tab, the failure's dialog says nothing of the staged message and the reload loses
it). Skips LOUDLY without the extension deps or a browser (a failure under ROMP_SERVED_TESTS_REQUIRE=1). SYNTHETIC fixtures
only (the notes-api demo's web and api).
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_queued_rescind_browser import QueuedLab, SID   # noqa: E402  the shared boot: one session, web

NEW_SID = "7d4e1f20-3b6a-4c58-9e17-2a0c5b8f6d93"   # the id the kernel mints for api (synthetic)
STAGED = "Start with the pagination bug in the list endpoint."
DRAFT = "Then tell me what you changed."

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }

async function road(kind) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 760 } });   // a fresh localStorage per road
  // every frame the page sends is recorded; the create is dropped (the kernel never hears it, so it stays in flight) and so
  // is any send to the synthetic session (the kernel does not hold it). Every session frame the page receives is kept: web's
  // is the template for the kernel's answer.
  await ctx.addInitScript((newSid) => {
    window.__sent = []; window.__frames = [];
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function (d) {
      try { const m = JSON.parse(d); if (m && m.type) { window.__sent.push(m); if (m.type === "createSession" || m.id === newSid) return; } } catch (e) { /* not a frame */ }
      return send.call(this, d);
    };
    window.addEventListener("message", (e) => { const m = e.data; if (m && m.type === "session" && Array.isArray(m.events)) window.__frames.push(m); });
  }, cfg.newSid);
  const page = await ctx.newPage();
  const boot = async () => {
    await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
    await page.waitForSelector("#composer-input", { timeout: 20000 });
    await page.waitForTimeout(700);
  };
  const read = () => page.evaluate(() => {
    const strip = document.getElementById("composer-staged");
    const shown = !!strip && strip.style.display !== "none" && !!strip.querySelector(".staged-lbl");
    const act = document.querySelector("#tabs .tab.active[data-id]");
    const prov = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id).filter((id) => id.startsWith("new-"));
    const confirm = document.querySelector("#confirm .confirm-detail");
    return { strip: shown ? { sid: strip.dataset.sid, line: strip.querySelector(".staged-lbl").textContent,
                              items: Array.from(strip.querySelectorAll(".staged-row .composer-chip-label")).map((n) => n.textContent) } : null,
             active: act ? act.dataset.id : null, prov, box: document.getElementById("composer-input").value,
             confirm: confirm ? confirm.textContent : null };
  });
  await page.goto(cfg.chat);
  await boot();
  await page.waitForFunction((sid) => window.__frames.some((m) => m.id === sid), cfg.sid, { timeout: 20000 });
  // a new session called api, as the user asks for it: the + tab, the name, Create session
  await page.click("#tabs .tab-add");
  await page.waitForSelector("#picker-search", { timeout: 10000 });
  await page.fill("#picker-search", "api");
  await page.click("#picker-new-btn");
  await page.waitForFunction(() => !!document.querySelector('#tabs .tab.active[data-id^="new-"]'), null, { timeout: 10000 });
  // stage a message on it (Ctrl+Enter), then start typing the next one
  await page.fill("#composer-input", cfg.staged);
  await page.press("#composer-input", "Control+Enter");
  await page.waitForSelector("#composer-staged .staged-lbl", { timeout: 10000 });
  await page.fill("#composer-input", cfg.draft);
  await page.waitForTimeout(300);
  const out = { staged: await read(), created: await page.evaluate(() => window.__sent.filter((m) => m.type === "createSession").map((m) => m.name)) };
  const arrive = async () => {
    // the kernel's answer: the created session's first full frame, named as the create asked
    await page.evaluate((s) => {
      const base = window.__frames.filter((m) => m.id === s.sid).pop();
      window.postMessage({ ...base, id: s.newSid, name: "api", events: [], status: { ...base.status, state: "idle" } }, "*");
    }, cfg);
    // not fatal: a page that adopts nothing is a measurement the assertions read, not a driver failure
    await page.waitForFunction((id) => !!document.querySelector('#tabs .tab.active[data-id="' + id + '"]'), cfg.newSid, { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(400);
  };
  if (kind === "reload") {
    await page.reload();
    await boot();
    out.reloaded = await read();
  }
  if (kind === "fail") {
    // the create's refusal: a warn naming no session, while the create is in flight
    await page.evaluate(() => window.postMessage({ type: "warn", text: "The folder could not be read." }, "*"));
    await page.waitForSelector("#confirm .confirm-detail", { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(300);
    out.failed = await read();
    await page.reload();
    await boot();
    out.failedReloaded = await read();
  } else {
    await arrive();
    out.arrived = await read();
    // the next send: what is in the box goes, with the staged message ahead of it
    await page.press("#composer-input", "Enter");
    await page.waitForTimeout(500);
    out.sent = await page.evaluate((id) => window.__sent.filter((m) => m.type === "sendMessage" && m.id === id).map((m) => m.text), cfg.newSid);
    out.after = await read();
  }
  await ctx.close();
  return out;
}

const r = { arrive: await road("arrive"), reload: await road("reload"), fail: await road("fail") };
await browser.close();
process.stdout.write("RESULT:" + JSON.stringify(r) + "\n", () => process.exit(0));
"""


class ServedStagedPendingTab(QueuedLab):
    _r = None
    LINE = "1 staged — sends with your next message"

    def _result(self):
        cls = type(self)
        if cls._r is not None:
            return cls._r
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID, "newSid": NEW_SID,
                       "staged": STAGED, "draft": DRAFT}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(self.EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        print("RESULT:" + json.dumps(r), file=sys.stderr)
        cls._r = r
        return r

    def _staged_on_opening_tab(self, road):
        s = road["staged"]
        self.assertEqual(road["created"], ["api"], "the page asked for the session: %r" % road)
        self.assertEqual(len(s["prov"]), 1, "one opening tab: %r" % s)
        self.assertEqual(s["active"], s["prov"][0])
        self.assertEqual(s["strip"], {"sid": s["prov"][0], "line": self.LINE, "items": [STAGED]}, "staged on the opening tab: %r" % s)
        self.assertEqual(s["box"], DRAFT)
        return s["prov"][0]

    def _arrived_and_sent(self, road, what):
        a = road["arrived"]
        self.assertEqual(a["active"], NEW_SID, "%s: the real session took the tab: %r" % (what, a))
        self.assertEqual(a["prov"], [], "%s: the opening tab is gone" % what)
        self.assertEqual(a["strip"], {"sid": NEW_SID, "line": self.LINE, "items": [STAGED]},
                         "%s: the real tab's strip shows the message staged while it opened: %r" % (what, a))
        self.assertEqual(a["box"], DRAFT, "%s: the draft came across" % what)
        self.assertEqual(road["sent"], [STAGED + "\n\n" + DRAFT], "%s: the next send carries it, ahead of the typed message" % what)
        self.assertIsNone(road["after"]["strip"], "%s: sent, so the strip is empty: %r" % (what, road["after"]))

    def test_a_message_staged_while_the_session_opens_is_on_the_real_tab_when_it_arrives_and_goes_with_the_next_send(self):
        road = self._result()["arrive"]
        self._staged_on_opening_tab(road)
        self._arrived_and_sent(road, "arrival")

    def test_a_reload_while_the_session_opens_brings_the_tab_back_and_the_arrival_still_hands_the_staged_message_over(self):
        road = self._result()["reload"]
        prov = self._staged_on_opening_tab(road)
        b = road["reloaded"]
        self.assertEqual(b["prov"], [prov], "the opening tab is back after the reload: %r" % b)
        self.assertEqual(b["active"], prov, "and on screen, as it was")
        self.assertEqual(b["strip"], {"sid": prov, "line": self.LINE, "items": [STAGED]}, "with its staged message: %r" % b)
        self.assertEqual(b["box"], DRAFT, "and its draft")
        self._arrived_and_sent(road, "reload, then arrival")

    def test_a_create_that_fails_keeps_the_staged_message_on_its_tab_says_so_and_keeps_it_across_a_reload(self):
        road = self._result()["fail"]
        prov = self._staged_on_opening_tab(road)
        f = road["failed"]
        self.assertEqual(f["active"], prov, "the failed tab is on screen: %r" % f)
        self.assertEqual(f["strip"], {"sid": prov, "line": self.LINE, "items": [STAGED]}, "its strip still shows the staged message: %r" % f)
        self.assertEqual(f["box"], DRAFT, "its box still holds the text")
        self.assertEqual(f["confirm"], "The folder could not be read.\n\nWhat you typed is in this tab's message box, and what you "
                                       "staged is still staged above it.", "the dialog names both: %r" % f)
        g = road["failedReloaded"]
        self.assertEqual(g["prov"], [prov], "the failed tab survives a reload: %r" % g)
        self.assertEqual(g["active"], prov)
        self.assertEqual(g["strip"], {"sid": prov, "line": self.LINE, "items": [STAGED]}, "with its staged message: %r" % g)
        self.assertEqual(g["box"], DRAFT, "and its text")


if __name__ == "__main__":
    unittest.main()
