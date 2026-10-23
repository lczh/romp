#!/usr/bin/env python3
"""Compact mode's Thought rows (the user 2026-10-23, who approved the design), on the real /chat page of a hermetic kernel.

A thinking block with words (a reasoning summary, or the progress note Claude Code's thinking display returns as text) shows in
compact mode as ONE collapsed row, "▸ Thought" and the first line of the latest note; consecutive blocks are one row; a click
opens "▾ Thought · <N> tokens" over the full words, and a second click closes it. A thought that opens a stretch of work is its
own row; one after a tool rides that tool's run and shows as a Thought row when the run expands (the 2026-06-30 rule that an
expanded run never shows thinking is reversed); a textless block stays hidden.

The synthetic transcript (session `web`, the notes-api demo world) carries the case the design was drawn for: a message sent
MID-TURN ("keep the default page size at 50, not 20") that the session answers only inside its thinking, never in a reply. In
compact mode that answer must still be readable: collapsed, its row right after the message shows the answer's first line;
a real click opens it to both blocks' words and the stretch's tokens (the message's figure rides the first block), and a
second click closes it. The opened row survives a rebuild of the view (a tool run toggled open rebuilds it), because the fold
is kept in the shared fold set under think:<first block's uuid>. The first run, opened, holds its thought as a Thought row
between its tools, its textless block shows nowhere, and its head counts its four tools only.

With THOUGHT_SHOTS=<dir> the driver writes screenshots (dark and light, desktop and phone, collapsed and opened);
THOUGHT_BEFORE_DIST=<dist> serves another tree's bundle for the before shots and skips the assertions, unless
THOUGHT_BEFORE_ASSERT=1 keeps them (how the change was proven red against the bundle before it). Skips LOUDLY without
the extension deps or a Playwright browser; the extension CI job installs Chromium and runs served files with
ROMP_SERVED_TESTS_REQUIRE=1, which turns any skip into a failure there. SYNTHETIC fixtures only."""
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

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID = "aaaaaaaa-1111-2222-3333-444444444444"
MODEL = "claude-opus-5-5"
SIG = "c3ludGhldGljLXNpZ25hdHVyZQ=="   # a synthetic signature: every block is signed, as the API's are
MIDTURN = "keep the default page size at 50, not 20"
ANSWER_1 = "The default has to move from 20 to 50, and one test pins 20."
ANSWER_2 = "Got it: switching the default page size to 50 and updating the one test that pinned 20."
RUN_THOUGHT = "Both callers pass no limit, so a default page size keeps them working. A cursor on the note id stays stable when notes are added."


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _records(cwd, base):
    """The transcript, one content block per record as Claude Code writes it. Turn A: a stretch of work with a thought that
    opens it, a run of four tools with a thought and a textless block inside, the mid-turn message, its answer inside the
    thinking alone, two more tools, the reply. Turn B: a thought, then the reply."""
    recs, prev, n, t = [], [None], [0], [base]

    def add(rec):
        n[0] += 1
        rec.update(sessionId=SID, parentUuid=prev[0], uuid="bbbbbbbb-1111-2222-3333-%012d" % n[0],
                   timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t[0])))
        recs.append(rec)
        prev[0] = rec["uuid"]
        t[0] += 3

    def message(blocks, think_tokens=0, stop="tool_use"):
        mid = "msg_%06d" % (n[0] + 1)
        usage = {"input_tokens": 10, "output_tokens": 200, "output_tokens_details": {"thinking_tokens": think_tokens}} if think_tokens \
            else {"input_tokens": 10, "output_tokens": 50}
        for b in blocks:
            add({"type": "assistant", "message": {"id": mid, "role": "assistant", "model": MODEL, "stop_reason": stop, "content": [b], "usage": usage}})

    def think(text=""):
        return {"type": "thinking", "thinking": text, "signature": SIG}

    tc = [0]

    def tool(name, inp, out, thinks=(), tokens=0):
        tc[0] += 1
        tid = "toolu_thought_%02d" % tc[0]
        message(list(thinks) + [{"type": "tool_use", "id": tid, "name": name, "input": inp}], tokens)
        add({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": out}]},
             "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})

    def reply(text, thinks=(), tokens=0):
        message(list(thinks) + [{"type": "text", "text": text}], tokens, stop="end_turn")

    src = cwd + "/src/routes/notes.ts"
    add({"type": "user", "message": {"role": "user", "content": "Add cursor pagination to GET /notes."}})
    tool("Read", {"file_path": src}, "export function listNotes(db) { return db.all('select * from notes'); }",
         [think("Reading how the list handler builds its query. Today it returns every row."),
          think("I'll start with the handler and its test to see what the callers expect.")], 1840)
    tool("Read", {"file_path": cwd + "/tests/notes.test.ts"}, "test('lists notes', ...)", [think()], 420)
    tool("Edit", {"file_path": src, "old_string": "return db.all('select * from notes');",
                  "new_string": "const rows = db.all('select * from notes where id > ? order by id limit ?', cursor, limit);\nreturn { rows };"},
         "edited", [think(RUN_THOUGHT)], 2400)
    tool("Bash", {"command": "npm test -- notes", "description": "Ran the notes tests"}, "3 passing")
    add({"type": "attachment", "isSidechain": False, "attachment": {"type": "queued_command", "prompt": MIDTURN}})
    tool("Edit", {"file_path": src, "old_string": "limit = 20", "new_string": "limit = 50"}, "edited",
         [think(ANSWER_1), think(ANSWER_2)], 960)
    tool("Bash", {"command": "npm test -- notes", "description": "Ran the notes tests again"}, "3 passing", [think()], 300)
    reply("Pagination is in. `GET /notes` takes `limit` (default 50) and `cursor`, and the notes tests pass.")
    add({"type": "user", "message": {"role": "user", "content": "Does a deleted note break the cursor?"}})
    reply("No. The query pages with `id > cursor`, so a deleted note drops out of the next page.",
          [think("A cursor that points at a deleted id still sorts correctly, because the query compares with id > cursor.")], 1100)
    return recs


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const painted = (page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 60)))));
const errors = [];
const open = async (theme, w, h, scale) => {
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: scale });
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.addInitScript((th) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: th, compact: true })); } catch (e) {} }, theme);
  await page.goto(cfg.chat);
  await page.waitForFunction(() => !!document.querySelector("#content .turn-toolgroup"), null, { timeout: 30000 });
  await page.addStyleTag({ content: "#content .turn-apierror { display: none !important; }" });   // the lab kernel runs no SDK backend: its notice is not this test's
  await painted(page);
  return page;
};
// the Thought row right after the mid-turn message: what it shows, and how much room it takes
const answerRow = (page) => page.evaluate((midturn) => {
  const turns = Array.from(document.querySelectorAll("#content .turn"));
  const msg = turns.find((t) => t.classList.contains("turn-user") && (t.textContent || "").includes(midturn));
  if (!msg) return { found: false, rows: turns.map((t) => t.className) };
  let row = msg.nextElementSibling;
  while (row && !row.classList.contains("turn")) row = row.nextElementSibling;
  const vis = (n) => !!n && getComputedStyle(n).display !== "none" && n.getClientRects().length > 0;
  const q = (s) => row.querySelector(s);
  const lh = parseFloat(getComputedStyle(q(".thought-line") || row).lineHeight) || 16;
  return { found: true, cls: row.className, dataUuid: row.dataset.uuid || null, dataUuids: row.dataset.uuids || null,
           line: q(".thought-line") ? q(".thought-line").textContent : null, act: q(".thought-line") ? q(".thought-line").dataset.act : null,
           tkey: q(".thought-line") ? q(".thought-line").dataset.tkey : null,
           label: vis(q(".thought-label")) ? q(".thought-label").textContent : null,
           gist: vis(q(".thought-gist")) ? q(".thought-gist").textContent : null,
           gistItalic: q(".thought-gist") ? getComputedStyle(q(".thought-gist")).fontStyle : null,
           gistClipped: q(".thought-gist") ? getComputedStyle(q(".thought-gist")).textOverflow : null,
           meta: vis(q(".thought-meta")) ? q(".thought-meta").textContent : null,
           body: vis(q(".thought-body")) ? q(".thought-body").innerText : null,
           caret: q(".thought-caret") ? getComputedStyle(q(".thought-caret"), "::before").content : null,
           lineH: q(".thought-line") ? q(".thought-line").getBoundingClientRect().height : null, lh,
           userSelect: q(".thought-line") ? getComputedStyle(q(".thought-line")).userSelect : null };
}, cfg.midturn);
const shot = async (page, name) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await page.evaluate(() => { const c = document.getElementById("content"); const first = c && c.querySelector(".turn-user"); if (first) first.scrollIntoView({ block: "start" }); });
  await painted(page);
  await page.screenshot({ path: cfg.shots + "/" + name + (cfg.suffix || "") + ".png" });
};
const out = { shots: [] };
// the assertions' page: desktop, dark
const page = await open("yatharth", 1200, 1000, 1);
out.collapsed = await answerRow(page);
out.units = await page.evaluate(() => Array.from(document.querySelectorAll("#content .turn")).map((t) => ({ cls: t.className,
  head: t.querySelector(".toolgroup-line") ? t.querySelector(".toolgroup-line").textContent.replace(/\s+/g, " ").trim() : null,
  gist: t.querySelector(".thought-gist") ? t.querySelector(".thought-gist").textContent : null })));
await shot(page, "thought-rows-dark-desktop");
if (!cfg.before && (out.collapsed.cls || "").includes("turn-thought")) {   // (no Thought row: the assertions say so, no click to time out on)
  // a real click on the row's line opens it
  const line = page.locator("#content .turn-user", { hasText: cfg.midturn }).locator("xpath=following-sibling::div[contains(concat(' ', normalize-space(@class), ' '), ' turn ')][1]").locator(".thought-line");
  await line.click();
  await painted(page);
  out.opened = await answerRow(page);
  // the first run, opened: a rebuild of the view (toggleToolGroup) that must keep the thought open, and the run's own rows
  await page.locator("#content .turn-toolgroup .toolgroup-line").first().click();
  await page.waitForFunction(() => !!document.querySelector("#content .tg-child"), null, { timeout: 10000 });
  await painted(page);
  out.afterRebuild = await answerRow(page);
  out.run = await page.evaluate(() => {
    const head = document.querySelector("#content .turn-toolgroup");
    const rows = [];
    for (let n = head.nextElementSibling; n && n.classList.contains("tg-child"); n = n.nextElementSibling)
      rows.push({ cls: n.className, text: (n.textContent || "").replace(/\s+/g, " ").trim().slice(0, 140) });
    return { head: head.textContent.replace(/\s+/g, " ").trim(), rows };
  });
  await shot(page, "thought-rows-opened-dark-desktop");
  // close the run again, then a second click on the line closes the thought
  await page.locator("#content .turn-toolgroup .toolgroup-line").first().click();
  await page.waitForFunction(() => !document.querySelector("#content .tg-child"), null, { timeout: 10000 });
  await line.click();
  await painted(page);
  out.closed = await answerRow(page);
  out.textless = await page.evaluate(() => Array.from(document.querySelectorAll("#content .turn-thinking")).filter((t) => !t.classList.contains("turn-thought")).length);
}
await page.close();
if (cfg.shots) {
  for (const [theme, tname] of [["yatharth", "dark"], ["yatharth-light", "light"]]) {
    for (const [w, h, scale, wname] of [[1200, 1000, 1, "desktop"], [390, 844, 2, "phone"]]) {
      if (tname === "dark" && wname === "desktop") continue;   // taken above
      const p = await open(theme, w, h, scale);
      out.shots.push({ theme: tname, light: await p.evaluate(() => document.body.classList.contains("theme-light")) });
      await shot(p, "thought-rows-" + tname + "-" + wname);
      if (!cfg.before) {
        await p.locator("#content .thought-line", { hasText: "Got it" }).click();
        await p.locator("#content .turn-toolgroup .toolgroup-line").first().click();
        await p.waitForFunction(() => !!document.querySelector("#content .tg-child"), null, { timeout: 10000 });
        await shot(p, "thought-rows-opened-" + tname + "-" + wname);
      }
      await p.close();
    }
  }
}
out.errors = errors;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedThoughtRows(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:          # a skip OR a failure: never leave a kernel or a lab behind
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them; CI's extension job has them and requires this file to run")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        cls.lab = tempfile.mkdtemp(prefix="thought-rows-")
        cls.before = os.environ.get("THOUGHT_BEFORE_DIST", "")
        if not cls.before:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(cls.before or os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # this lab mints its own state root: no per-session host (root CLAUDE.md)
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True,
             "model": MODEL, "liveModel": "Opus 5.5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        cls.records = _records(cwd, int(time.time()) - 1800)
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in cls.records))
        cls.port, cls.token = _free_port(), "testtok-thoughtrows"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill()
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "midturn": MIDTURN,
                       "shots": os.environ.get("THOUGHT_SHOTS", ""), "before": bool(self.before) and not os.environ.get("THOUGHT_BEFORE_ASSERT"),
                       "suffix": "-before" if self.before else ""}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-2000:])
        return json.loads(line[len("RESULT:"):])

    def test_a_midturn_message_answered_only_in_thinking_reads_as_a_collapsed_thought_that_opens_and_closes(self):
        r = self._drive()
        if self.before and not os.environ.get("THOUGHT_BEFORE_ASSERT"):
            self.skipTest("a before-the-change dist: screenshots only, the assertions describe the change")
        self.assertEqual(r["errors"], [], "no page errors")
        first_answer_uuid = next(rec["uuid"] for rec in self.records
                                 if rec["type"] == "assistant" and rec["message"]["content"][0].get("thinking") == ANSWER_1)
        # collapsed: one line right after the message, "▸ Thought" and the LAST block's first line, clipped, italic; the words
        # and the tokens are not shown
        c = r["collapsed"]
        self.assertTrue(c["found"], "the mid-turn message renders: %r" % c)
        self.assertIn("turn-thought", c["cls"], "the row right after the mid-turn message is a Thought row: %r" % c)
        self.assertEqual(c["label"], "Thought")
        self.assertEqual(c["gist"], ANSWER_2, "the gist is the latest note's first line, the answer: %r" % c)
        self.assertEqual((c["gistItalic"], c["gistClipped"]), ("italic", "ellipsis"))
        self.assertIsNone(c["body"], "collapsed: the full words are not shown")
        self.assertIsNone(c["meta"], "…nor the tokens")
        self.assertEqual(c["caret"], '"▸"')
        self.assertLess(c["lineH"], 1.6 * c["lh"], "one line: %r" % c)
        self.assertEqual(c["userSelect"], "none", "the line is a fold head: unselectable")
        self.assertEqual((c["act"], c["tkey"]), ("thinkfold", "think:" + first_answer_uuid),
                         "the click rides the body delegate, keyed under the first block's uuid: %r" % c)
        self.assertEqual(c["dataUuid"], first_answer_uuid, "the row anchors on its first block")
        # opened by a real click: both blocks' words and the stretch's tokens (the message's figure, on its first block)
        o = r["opened"]
        self.assertIn("expanded", o["cls"].split())
        self.assertEqual(o["caret"], '"▾"')
        self.assertIsNone(o["gist"], "open: the gist gives way to the words")
        self.assertEqual(o["meta"], "· 960 tokens")
        self.assertEqual(o["body"], ANSWER_1 + "\n\n" + ANSWER_2, "both blocks' words, a blank line apart: %r" % o["body"])
        # a rebuild of the view (a tool run toggled open) keeps it open: the fold is in the shared fold set
        a = r["afterRebuild"]
        self.assertIn("expanded", a["cls"].split(), "still open after the view rebuilt: %r" % a)
        self.assertEqual(a["body"], o["body"])
        # the run, opened: four tools and the thought that came after a tool, as a Thought row between them; the textless block
        # inside the run shows nowhere; the head counts the tools only
        run = r["run"]
        kinds = ["thought" if "turn-thought" in row["cls"] else "tool" if "turn-tool" in row["cls"] else row["cls"] for row in run["rows"]]
        self.assertEqual(kinds, ["tool", "tool", "thought", "tool", "tool"], "the run's rows: %r" % run["rows"])
        self.assertIn(RUN_THOUGHT[:40], run["rows"][2]["text"], "the thought between the run's tools: %r" % run["rows"][2])
        self.assertNotIn("Thought", run["head"], "the open run's head is its caret alone: %r" % run["head"])
        # closed by a second click
        x = r["closed"]
        self.assertNotIn("expanded", x["cls"].split())
        self.assertEqual(x["gist"], ANSWER_2)
        self.assertIsNone(x["body"])
        # the turns: a thought opens each stretch of work, the textless blocks show nowhere, the closed run counts its tools
        units = r["units"]
        heads = [u["head"] for u in units if "turn-toolgroup" in u["cls"]]
        self.assertEqual(len(heads), 2, "two runs: %r" % [u["cls"] for u in units])
        self.assertTrue(heads[0].startswith("▸ Read 2 files, edited a file, ran a command"), "the head counts the run's four tools, not its thought: %r" % heads)
        self.assertEqual(r["textless"], 0, "no textless thinking row in compact mode")
        gists = [u["gist"] for u in units if "turn-thought" in u["cls"]]
        self.assertEqual(gists, ["I'll start with the handler and its test to see what the callers expect.", ANSWER_2,
                                 "A cursor that points at a deleted id still sorts correctly, because the query compares with id > cursor."],
                         "a Thought row opens each stretch of work, the gist its latest note's first line: %r" % gists)


if __name__ == "__main__":
    unittest.main()
