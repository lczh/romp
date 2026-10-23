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
only (the notes-api demo's web, api and tests).

The ✕ on the opening tab (the review of #373) names the staged message, the text and then the held send it would discard,
with Cancel focused, and Cancel and Escape keep them; red before the fix (it asked to End api with End session focused). The
line over a box holding sends that never went belongs to its tab: after sends held for api and for tests, and two creates that
replaced theirs, each kept tab says so over its own box, a reload included, and api's line follows its text to the session;
red before the fix (docs' create took api's line, and the reload took both).

The edges (2026-10-23), on the same page: a second create (tests) while api is opening keeps api's tab with its staged
message and its text, and api's session adopts it; Enter on the opening tab holds the staged message with the typed text,
so the session receives one message; and the failed tab's close asks before it discards, Cancel keeps everything, and the
session arriving late under its name adopts the tab. Red before the fix on each (the second create dismissed api's tab and
everything on it, Enter held the typed text alone, the close discarded at once and the late session found nothing).
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
    const title = document.querySelector("#confirm .confirm-title");
    const ph = Array.from(document.querySelectorAll(".tx-empty")).find((e) => e.offsetParent !== null);
    const tabState = (id) => { const t = document.querySelector('#tabs .tab[data-id="' + id + '"] .tab-close'); return t ? (t.dataset.dead === "1" ? "dead" : "live") : null; };
    return { strip: shown ? { sid: strip.dataset.sid, line: strip.querySelector(".staged-lbl").textContent,
                              items: Array.from(strip.querySelectorAll(".staged-row .composer-chip-label")).map((n) => n.textContent) } : null,
             active: act ? act.dataset.id : null, prov, box: document.getElementById("composer-input").value,
             confirm: confirm ? confirm.textContent : null, title: title ? title.textContent : null,
             buttons: Array.from(document.querySelectorAll("#confirm .confirm-btn")).map((b) => b.textContent),
             line: (document.querySelector("#composer-held-note .composer-note-msg") || {}).textContent || null,
             focused: document.activeElement && document.activeElement.closest("#confirm") ? document.activeElement.textContent : null,
             placeholder: ph ? ph.textContent : null, tabs: Object.fromEntries(prov.map((id) => [id, tabState(id)])) };
  });
  // a new session, as the user asks for it: the + tab, the name, Create session; its tab is up and on screen
  const create = async (name, prev) => {
    await page.click("#tabs .tab-add");
    await page.waitForSelector("#picker-search", { timeout: 10000 });
    await page.fill("#picker-search", name);
    await page.click("#picker-new-btn");
    await page.waitForFunction((prev) => { const t = document.querySelector('#tabs .tab.active[data-id^="new-"]'); return !!t && t.dataset.id !== prev; },
                               prev || null, { timeout: 10000 });
  };
  await page.goto(cfg.chat);
  await boot();
  await page.waitForFunction((sid) => window.__frames.some((m) => m.id === sid), cfg.sid, { timeout: 20000 });
  await create("api");
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
  const prov = out.staged.prov[0];
  // the ✕ on the tab while it is still opening: it asks what it would discard, and the answer `how` (a button's label, or
  // Escape) is given
  const closeOpening = async (how) => {
    const close = await page.$('#tabs .tab[data-id="' + prov + '"] .tab-close');
    if (close) await close.click();
    await page.waitForSelector("#confirm .confirm-title", { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(200);
    const ask = await read();
    if (how === "Escape") await page.keyboard.press("Escape");
    else { const b = await page.$('#confirm .confirm-btn:text-is("' + how + '")'); if (b) await b.click(); }
    await page.waitForTimeout(300);
    return { ask, after: await read() };
  };
  if (kind === "enter") {
    // its ✕ while it holds a staged message and text: Cancel keeps both
    out.closeStaged = await closeOpening("Cancel");
    // Enter on the opening tab: held for the session, with what is staged on the tab
    await page.click("#composer-input");
    await page.press("#composer-input", "Enter");
    await page.waitForTimeout(400);
    out.held = await read();
    // its ✕ while it holds that send: Escape keeps it
    out.closeHeld = await closeOpening("Escape");
    out.cancels = await page.evaluate(() => window.__sent.filter((m) => m.type === "cancelCreate").map((m) => m.name));
  }
  if (kind === "lines") {
    // a send held for api, tests started before api opens, a send held for tests, docs started before tests opens: each
    // kept tab's box holds its send, unsent, and says so in its own line, a reload included
    const tab = async (id) => { await page.click('#tabs .tab[data-id="' + id + '"]'); await page.waitForTimeout(300); return read(); };
    await page.press("#composer-input", "Enter");
    await create("tests", prov);
    const tests = await page.evaluate(() => document.querySelector("#tabs .tab.active[data-id]").dataset.id);
    await page.fill("#composer-input", "Run the list endpoint's tests.");
    await page.press("#composer-input", "Enter");
    await page.waitForTimeout(300);
    await create("docs", tests);
    await page.waitForTimeout(300);
    out.linesApi = await tab(prov);
    out.linesTests = await tab(tests);
    await page.reload();
    await boot();
    out.reloadedTests = await tab(tests);
    out.reloadedApi = await tab(prov);   // api's tab on screen when its session arrives below, which takes it, line and all
  }
  if (kind === "supersede") {
    // a second new session, tests, before api's is up
    await create("tests", prov);
    await page.waitForTimeout(400);
    out.superseded = await read();
    out.cancels = await page.evaluate(() => window.__sent.filter((m) => m.type === "cancelCreate").map((m) => m.name));
    out.created2 = await page.evaluate(() => window.__sent.filter((m) => m.type === "createSession").map((m) => m.name));
    // back to api's tab, to read what it holds (not fatal when it is gone: that is the measurement)
    const apiTab = await page.$('#tabs .tab[data-id="' + prov + '"]');
    if (apiTab) await apiTab.click();
    await page.waitForTimeout(400);
    out.kept = await read();
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
    // its close asks before it discards; Cancel keeps everything
    const close = await page.$('#tabs .tab[data-id="' + prov + '"] .tab-close');
    if (close) await close.click();
    await page.waitForSelector("#confirm .confirm-title", { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(300);
    out.ask = await read();
    const cancel = await page.$('#confirm .confirm-btn:text-is("Cancel")');
    if (cancel) await cancel.click();
    await page.waitForTimeout(300);
    out.cancelled = await read();
  }
  {
    // the session arrives (late, for the failure: a slow create the backstop gave up on)
    await arrive();
    out.arrived = await read();
    // the next send: what is in the box goes, with the staged message ahead of it
    await page.press("#composer-input", "Enter");
    await page.waitForTimeout(500);
    // one entry per copy: the kernel never acknowledges a send this test drops at the socket, so the page may send the same
    // copy again under its id (send-acks.ts), which the kernel would take once
    out.sent = await page.evaluate((id) => { const seen = new Set();
      return window.__sent.filter((m) => m.type === "sendMessage" && m.id === id && !seen.has(m.qid) && seen.add(m.qid)).map((m) => m.text); }, cfg.newSid);
    out.after = await read();
  }
  await ctx.close();
  return out;
}

const r = { arrive: await road("arrive"), reload: await road("reload"), fail: await road("fail"),
            supersede: await road("supersede"), enter: await road("enter"), lines: await road("lines") };
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

    def _arrived_and_sent(self, road, what, prov=()):
        a = road["arrived"]
        self.assertEqual(a["active"], NEW_SID, "%s: the real session took the tab: %r" % (what, a))
        self.assertEqual(a["prov"], list(prov), "%s: the opening tab is gone" % what)
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

    def test_the_failed_tabs_close_asks_before_discarding_and_a_late_session_under_its_name_adopts_it(self):
        road = self._result()["fail"]
        prov = self._staged_on_opening_tab(road)
        a = road["ask"]
        self.assertEqual(a["title"], "Discard api's 1 staged message and the text in its box?", "the close asks first: %r" % a)
        self.assertEqual(a["buttons"], ["Cancel", "Discard"])
        self.assertEqual(a["prov"], [prov], "nothing goes before the answer")
        c = road["cancelled"]
        self.assertIsNone(c["title"], "Cancel closes the question: %r" % c)
        self.assertEqual(c["prov"], [prov], "and keeps the tab: %r" % c)
        self.assertEqual(c["strip"], {"sid": prov, "line": self.LINE, "items": [STAGED]}, "with its staged message")
        self.assertEqual(c["box"], DRAFT, "and its text")
        # the create was slow, not dead: its session turns up under the name and takes the tab
        self._arrived_and_sent(road, "a late arrival after a failure")

    def test_a_second_create_while_one_opens_keeps_the_first_tab_with_everything_on_it_and_its_session_adopts_it(self):
        road = self._result()["supersede"]
        prov = self._staged_on_opening_tab(road)
        s = road["superseded"]
        self.assertEqual(road["created2"], ["api", "tests"], "both creates went to the kernel: %r" % road)
        self.assertEqual(road["cancels"], [], "api's create is not cancelled")
        self.assertEqual(len(s["prov"]), 2, "api's tab stays beside the new one: %r" % s)
        self.assertEqual(s["prov"][0], prov)
        second = s["prov"][1]
        self.assertEqual(s["active"], second, "the new session's tab is on screen")
        self.assertEqual(s["tabs"], {prov: "dead", second: "live"}, "api's tab is kept, no longer opening: %r" % s)
        self.assertIsNone(s["confirm"], "nothing failed, so no dialog")
        k = road["kept"]
        self.assertEqual(k["active"], prov)
        self.assertEqual(k["strip"], {"sid": prov, "line": self.LINE, "items": [STAGED]}, "api's tab still has its staged message: %r" % k)
        self.assertEqual(k["box"], DRAFT, "and its text")
        self.assertEqual(k["placeholder"], "You started another session before this one opened. What's in this tab is kept, "
                                           "and moves to the session if it opens.", "it says what happened: %r" % k)
        self._arrived_and_sent(road, "api's arrival after a second create", prov=[second])

    def test_enter_on_the_opening_tab_holds_the_staged_message_with_the_typed_text_as_one_send(self):
        road = self._result()["enter"]
        prov = self._staged_on_opening_tab(road)
        h = road["held"]
        self.assertIsNone(h["strip"], "the staged message went with the send: %r" % h)
        self.assertEqual(h["box"], "", "the box is cleared, as a send clears it")
        self.assertEqual(h["active"], prov)
        a = road["arrived"]
        self.assertEqual(a["active"], NEW_SID, "the session took the tab: %r" % a)
        self.assertIsNone(a["strip"], "nothing is left staged to wait for the message after: %r" % a)
        self.assertEqual(road["sent"], [STAGED + "\n\n" + DRAFT], "the session received one message, the staged item first: %r" % road["sent"])

    def test_the_x_on_the_opening_tab_names_what_it_would_discard_with_cancel_focused_and_cancel_keeps_it(self):
        # the review of #373 (2026-10-23): it asked "End “api”?" with End session focused, naming none of it
        road = self._result()["enter"]
        prov = self._staged_on_opening_tab(road)
        a, c = road["closeStaged"]["ask"], road["closeStaged"]["after"]
        self.assertEqual(a["title"], "Cancel starting api and discard its 1 staged message and the text in its box?", "it names both: %r" % a)
        self.assertEqual(a["buttons"], ["Cancel", "Discard"])
        self.assertEqual(a["focused"], "Cancel", "the safe answer has the focus: %r" % a)
        self.assertIsNone(c["title"], "Cancel closes the question: %r" % c)
        self.assertEqual(c["prov"], [prov], "and keeps the tab")
        self.assertEqual(c["tabs"], {prov: "live"}, "still opening")
        self.assertEqual(c["strip"], {"sid": prov, "line": self.LINE, "items": [STAGED]}, "with its staged message: %r" % c)
        self.assertEqual(c["box"], DRAFT, "and its text")
        h, e = road["closeHeld"]["ask"], road["closeHeld"]["after"]
        self.assertEqual(h["title"], "Cancel starting api and discard its 1 unsent message?", "a held send is named too: %r" % h)
        self.assertEqual(h["focused"], "Cancel")
        self.assertIsNone(e["title"], "Escape closes it: %r" % e)
        self.assertEqual(e["prov"], [prov])
        self.assertEqual(road["cancels"], [], "the create was never cancelled")

    def test_each_kept_tabs_line_over_its_unsent_text_survives_a_second_replaced_create_and_a_reload(self):
        # the review of #373 (2026-10-23): one line for the whole page, so docs' create took api's, and a reload took both
        road = self._result()["lines"]
        prov = self._staged_on_opening_tab(road)
        sent = STAGED + "\n\n" + DRAFT
        api = "Not sent: what you typed while api was starting is in the box."
        tests = "Not sent: what you typed while tests was starting is in the box."
        for key, line, box in (("linesApi", api, sent), ("linesTests", tests, "Run the list endpoint's tests."),
                               ("reloadedTests", tests, "Run the list endpoint's tests."), ("reloadedApi", api, sent)):
            self.assertEqual(road[key]["line"], line, "%s: %r" % (key, road[key]))
            self.assertEqual(road[key]["box"], box, "%s: the text is in the box" % key)
        self.assertEqual(road["reloadedApi"]["active"], prov)
        a = road["arrived"]
        self.assertEqual(a["active"], NEW_SID, "api's session took its tab: %r" % a)
        self.assertEqual(a["line"], api + " Send it when you're ready.", "and the line, now that it can be sent: %r" % a)
        self.assertEqual(road["sent"], [sent], "the next send carries the text")
        self.assertIsNone(road["after"]["line"], "and the line goes with it: %r" % road["after"])


if __name__ == "__main__":
    unittest.main()
