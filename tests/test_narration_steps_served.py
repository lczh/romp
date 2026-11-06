#!/usr/bin/env python3
"""While a turn is open nothing on the chat moves, and a call that can never get its result says so, on the real /chat page of
a hermetic kernel (progress notes, round six, 2026-11-06).

The fork spot under a response and the turn's "worked …" line wait for the turn to END as the kernel reads it: the status's
`turnOpen`, the event model's open turn (_session_working), the reading the running-call stamp takes. Never the status chip,
which leaves "working" mid-turn for a permission prompt, a question and a retry. A spot rides the LAST row of its turn, inline
in that row's "worked …" line, so it takes no row of its own.

- Session `web`, stepped while four pages watch it (look E compact dark and light, look A compact, full mode): a finished turn;
  then a turn of a note and an edit, a note and an edit held on a permission prompt, the prompt granted, a note and a question,
  its answer, a retry, a text block an older model writes between its tools, a note and two calls with one still running, its
  result, and the reply. At every step every row already painted keeps its place, the open turn carries no fork spot and no
  "worked …" line, and the finished turn keeps its one spot; at the end the turn's spot lands in its reply's "worked …" line.
  The chip of a permission prompt, a question and a retry is the one a running backend reports (the lab runs none): the page's
  socket carries it over the kernel's frames (needsInput, retrying), the kernel's turnOpen untouched.
- Session `api`: a turn that ends on a Thought row right after a single call; the turn ends by its state record (the CLI's
  result frame, "waiting"). The Thought row does not move, and the spot rides its "worked …" line.
- Session `tests`, look E: a cut Edit and a cut Read after notes, and a line of three calls with one cut. Each cut call wears
  A's plain ring dot and says "no result"; the line says "· 1 no result" with the ring; the rail dots of the attached rows sit
  on their labels as A's do; a one-call row and a line wear one font size; a line with a call still running keeps its dot's
  own classes (dot-nav).

With NARRATION_SHOTS_REALISTIC5=<dir> the driver writes look E screenshots, dark and light: the cut calls, and a note then a
permission prompt, before and while it is pending. Skips LOUDLY without the extension deps or a Playwright browser; the
extension CI job requires it. SYNTHETIC fixtures only: the notes-api demo world, sessions `web`, `api` and `tests`, invented
text, placeholder ids, host TESTHOST."""
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
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)
from test_narration_served import _Transcript, think, MODEL, _free_port   # noqa: E402  the records as Claude Code writes them

SID_WEB = "aaaaaaaa-6666-2222-3333-444444444444"
SID_API = "aaaaaaaa-6666-2222-3333-555555555555"
SID_TESTS = "aaaaaaaa-6666-2222-3333-666666666666"
P1 = "Which routes does the notes service have?"
NOTE_A = "Reading the router to list its routes."
R1 = "Three routes: list, get and create."
P2 = "Add an archive flag to notes."
N1 = "Adding an archived column with a migration first."
N2 = "Now the list route skips archived notes."
N3 = "One thing to settle before the search route."
QUESTION = "Should archived notes still show up in search?"
ANSWER = "Hide them"
T1 = "Running the notes tests."
N4 = "Linting and running the search tests together."
R2 = "Notes can be archived now; the list and search routes skip them. The tests and the linter pass."
E_PROMPT = "Tidy the README."
E_NOTE = "Reading the README to see what is out of date."
E_THOUGHT = "The install section still names the old port; the rest reads fine."
C_PROMPT_1 = "Rename the archived flag to hidden."
C_NOTE_1 = "Renaming the column in the migration first."
C_PROMPT_2 = "keep going"
C_NOTE_2 = "Picking up where I stopped: reading the route that uses the flag."
C_PROMPT_3 = "Run the three steps together."
C_NOTE_3 = "Reading the schema, editing the route and running the tests at once."
C_PROMPT_4 = "Carry on."
C_REPLY = "The flag is called hidden now, in the migration, the route and the tests. All tests pass."


def _tid(tr):
    return "toolu_%s_%02d" % (tr.prefix[:8], tr.tc)


def _result(tr, tid, out):
    tr.add({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": out}]},
            "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})


def _calls(tr, note, calls):
    """A note, then several calls in ONE message (parallel tool uses), as the CLI writes them: one record per block. Returns
    the calls' ids."""
    ids, blocks = [], [think(note)]
    for name, inp in calls:
        tr.tc += 1
        ids.append(_tid(tr))
        blocks.append({"type": "tool_use", "id": ids[-1], "name": name, "input": inp})
    tr.message(blocks, 300, mark=(0,))
    return ids


def _web(cwd, base):
    """Session `web`: (the records the page opens on, the steps, the uuids to mark). A step is the records it appends and the
    chip a running backend would report while the step stands (None: the kernel's own)."""
    tr = _Transcript(SID_WEB, "eeeeeeee-6666-2222-3333", base)
    src = cwd + "/src/routes/notes.ts"
    tr.user(P1)
    tr.note_tool(NOTE_A, "Read", {"file_path": src}, "3 routes")
    tr.reply(R1)
    tr.user(P2)
    tr.note_tool(N1, "Edit", {"file_path": cwd + "/db/migrations/007.sql", "old_string": "", "new_string": "alter table notes add archived"}, "edited")
    steps = []

    def step(name, chip, wait, fn=None):
        n = len(tr.recs)
        if fn:
            fn()
        steps.append({"name": name, "chip": chip, "wait": wait, "recs": [json.dumps(r) for r in tr.recs[n:]]})

    first = len(tr.recs)
    held = {}

    def pending_edit():
        tr.tool("Edit", {"file_path": src, "old_string": "where", "new_string": "where archived = 0 and"}, "", [think(N2)], 300, mark=(0,), pending=True)
        held["edit"] = _tid(tr)
    step("note-then-edit", None, N2, pending_edit)
    step("permission-pending", "needsInput", None)
    step("permission-granted", None, None, lambda: _result(tr, held["edit"], "edited"))

    def ask():
        tr.tool("AskUserQuestion", {"questions": [{"question": QUESTION, "header": "Search", "multiSelect": False,
                                                    "options": [{"label": ANSWER, "description": "Search skips archived notes"},
                                                                {"label": "Keep them", "description": "Search shows every note"}]}]},
                "", [think(N3)], 300, mark=(0,), pending=True)
        held["ask"] = _tid(tr)
    step("question", "needsInput", N3, ask)
    step("answered", None, None, lambda: _result(tr, held["ask"], 'User has answered your questions: "%s"="%s". You can now continue with the user\'s answers in mind.' % (QUESTION, ANSWER)))
    step("retrying", "retrying", None)
    step("text-between-tools", None, T1, lambda: tr.text_tool(T1, "Bash", {"command": "npm test -- notes", "description": "Ran the notes tests"}, "12 passing"))

    def line():
        held["line"] = _calls(tr, N4, [("Bash", {"command": "npm run lint", "description": "Ran the linter"}),
                                      ("Bash", {"command": "npm test -- search", "description": "Ran the search tests"})])
        _result(tr, held["line"][0], "clean")
    step("line-running", None, N4, line)
    step("line-done", None, None, lambda: _result(tr, held["line"][1], "9 passing"))
    step("reply", None, R2[:30], lambda: tr.reply(R2))
    return tr.recs[:first], steps, tr.marks


def _api(cwd, base):
    """Session `api`: a turn that has a note, one call and a Thought row (a reasoning block the CLI did not list), still open."""
    tr = _Transcript(SID_API, "ffffffff-6666-2222-3333", base)
    tr.user(E_PROMPT)
    tr.note_tool(E_NOTE, "Read", {"file_path": cwd + "/README.md"}, "# notes-api")
    tr.message([think(E_THOUGHT)], 400, stop=None)
    return tr.recs, tr.marks


def _tests(cwd, base):
    """Session `tests`: cut calls. The CLI stopped twice with a call in flight, then once with one of three parallel calls in
    flight; the session carried on and answered."""
    tr = _Transcript(SID_TESTS, "abababab-6666-2222-3333", base)
    tr.user(C_PROMPT_1)
    tr.tool("Edit", {"file_path": cwd + "/db/migrations/008.sql", "old_string": "archived", "new_string": "hidden"}, "", [think(C_NOTE_1)], 300, mark=(0,), pending=True)
    tr.user(C_PROMPT_2)
    tr.tool("Read", {"file_path": cwd + "/src/routes/notes.ts"}, "", [think(C_NOTE_2)], 300, mark=(0,), pending=True)
    tr.user(C_PROMPT_3)
    ids = _calls(tr, C_NOTE_3, [("Read", {"file_path": cwd + "/src/db/schema.ts"}),
                                ("Edit", {"file_path": cwd + "/src/routes/notes.ts", "old_string": "archived", "new_string": "hidden"}),
                                ("Bash", {"command": "npm test", "description": "Ran the tests"})])
    _result(tr, ids[0], "export const notes = table(...)")
    _result(tr, ids[2], "all passing")
    tr.user(C_PROMPT_4)
    tr.reply(C_REPLY)
    return tr.recs, tr.marks


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
const showing = (t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => n.getClientRects().length > 0 && (n.textContent || "").includes(t));
// one page: the theme, compact or full, the look, the session's tab, a text it must show. Its socket carries the chip a running
// backend would report for `web` while a step forces one (needsInput, retrying), the kernel's turnOpen untouched, and can replay
// the session's last frame with that chip (a forced chip with no kernel change has no frame of its own)
const open = async (name, theme, compact, look, sid, wait) => {
  const page = await browser.newPage({ viewport: { width: 1200, height: 1500 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => errors.push(name + ": " + String(e).slice(0, 300)));
  const io = { forced: null, last: null, ws: null, frames: 0, status: {} };
  await page.routeWebSocket(() => true, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((m) => server.send(m));
    server.onMessage((m) => {
      if (typeof m === "string" && m.includes('"status"')) {
        try {
          const f = JSON.parse(m);
          if (f && f.id && f.status && (f.type === "session" || f.type === "chatTail")) io.status[f.id] = { state: f.status.state, turnOpen: f.status.turnOpen };
          if (f && f.id === cfg.web && f.status && (f.type === "session" || f.type === "chatTail")) {
            io.frames++;
            io.last = JSON.parse(m);
            if (io.forced) { f.status.state = io.forced; m = JSON.stringify(f); }
          }
        } catch (e) { /* not JSON: as is */ }
      }
      ws.send(m);
    });
    server.onClose(() => ws.close()); ws.onClose(() => server.close());
    io.ws = ws;
  });
  await page.addInitScript(([th, c, lk]) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: th, compact: c })); if (lk) localStorage.setItem("romp:narration-look", lk); } catch (e) {} }, [theme, compact, look]);
  await page.goto(cfg.chat);
  await page.waitForSelector('#tabs .tab[data-id="' + sid + '"]', { state: "attached", timeout: 30000 });
  const active = await page.evaluate((id) => !!document.querySelector('#tabs .tab.active[data-id="' + id + '"]'), sid);
  if (!active) await page.locator('#tabs .tab[data-id="' + sid + '"]:visible').first().click();
  await page.waitForFunction(showing, wait, { timeout: 30000 });
  await page.addStyleTag({ content: "#content .turn-apierror { display: none !important; }" });   // the lab kernel runs no SDK backend
  await page.mouse.move(700, 1500 - 24);   // over the composer, off the tab strip: a tab's tooltip names the machine's login
  await painted(page);
  return { name, page, io };
};
// every painted row, top to bottom: its kind, an identity that survives a re-render (its uuid), its place relative to the first
// row (so a list anchored either way reads the same), and what it carries
const geom = (page) => page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("#content .turn")).filter((t) => t.getClientRects().length > 0 && !(t.parentElement && t.parentElement.closest(".turn")));
  if (!rows.length) return [];
  const y0 = rows[0].getBoundingClientRect().top;
  const seen = {};
  return rows.map((t) => {
    const kind = (t.className.match(/\bturn-(user|assistant|toolgroup|tool|thought|thinking|ask|queued|notice)\b/) || [])[1] || "other";
    const words = (t.innerText || "").replace(/\s+/g, " ").trim();
    seen[kind] = (seen[kind] || 0) + 1;
    const r = t.getBoundingClientRect();
    const d = t.querySelector(":scope > .dot");
    return { key: kind + "|" + (t.dataset.uuid || "#" + seen[kind]), kind, text: words.slice(0, 80), top: Math.round((r.top - y0) * 10) / 10,
             h: Math.round(r.height * 10) / 10, spot: !!t.querySelector(".fork-spot"), footSpot: !!t.querySelector(":scope > .turn-elapsed .fork-spot"),
             foot: !!t.querySelector(":scope > .turn-elapsed"), attached: /\b(toolgroup|tool)-attached\b/.test(t.className),
             dot: d ? d.className : "" };
  });
});
const shot = async (p, dir, name, prompt) => {
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await p.page.evaluate((t) => { const u = Array.from(document.querySelectorAll("#content .turn-user")).find((n) => (n.textContent || "").includes(t)); if (u) u.scrollIntoView({ block: "start" }); }, prompt);
  await p.page.mouse.move(700, 1500 - 24);
  await painted(p.page);
  await p.page.screenshot({ path: dir + "/" + name + ".png", clip: { x: 0, y: 0, width: 1200, height: 900 } });
};
const out = { steps: {}, edge: {}, cuts: {}, turnOpen: [] };
const dir5 = cfg.shots5;
// THE STEPPED TURN (web): four pages watch one session
const pages = [await open("E", "yatharth", true, "E", cfg.web, cfg.p2), await open("A", "yatharth", true, "A", cfg.web, cfg.p2),
               await open("full", "yatharth", false, "", cfg.web, cfg.p2), await open("E-light", "yatharth-light", true, "E", cfg.web, cfg.p2)];
for (const p of pages) out.steps[p.name] = [{ step: "open", rows: await geom(p.page) }];
for (const st of cfg.steps) {
  const before = pages.map((p) => p.io.frames);
  for (const p of pages) p.io.forced = st.chip;
  if (st.recs.length) fs.appendFileSync(cfg.webPath, st.recs.map((r) => r + "\n").join(""));
  for (const [i, p] of pages.entries()) {
    if (st.wait) await p.page.waitForFunction(showing, st.wait, { timeout: 30000 });
    else if (st.recs.length) {
      const t0 = Date.now();
      while (p.io.frames <= before[i] && Date.now() - t0 < 30000) await p.page.waitForTimeout(50);   // loop-ok: the step's frame, bounded
      await p.page.waitForTimeout(300);
    } else if (p.io.last) {
      // a chip with no kernel change: the session's last frame again, carrying it
      const f = JSON.parse(JSON.stringify(p.io.last)); f.status.state = st.chip; p.io.ws.send(JSON.stringify(f));
      await p.page.waitForTimeout(300);
    }
    await painted(p.page);
    out.steps[p.name].push({ step: st.name, rows: await geom(p.page) });
  }
  out.turnOpen.push([st.name, pages[0].io.status[cfg.web] ? pages[0].io.status[cfg.web].turnOpen : null]);
  if (st.name === "note-then-edit") await shot(pages[0], dir5, "permission-before-E-dark", cfg.p2), await shot(pages[3], dir5, "permission-before-E-light", cfg.p2);
  if (st.name === "permission-pending") await shot(pages[0], dir5, "permission-pending-E-dark", cfg.p2), await shot(pages[3], dir5, "permission-pending-E-light", cfg.p2);
  if (st.name === "line-running") {
    // the running line's dot: the working dot, its own classes kept (dot-nav: the pointer, the hit area, the hover growth)
    out.runningDot = await pages[0].page.evaluate(() => { const h = Array.from(document.querySelectorAll("#content .toolgroup-attached")).pop(); const d = h && h.querySelector(":scope > .dot"); return d ? d.className : null; });
  }
}
out.lastStatus = Object.fromEntries(pages.map((p) => [p.name, p.io.last ? { state: p.io.last.status.state, turnOpen: p.io.last.status.turnOpen } : null]));
for (const p of pages) await p.page.close();
// THE TURN THAT ENDS ON A THOUGHT ROW (api): the turn ends by its state record
const edge = [await open("E", "yatharth", true, "E", cfg.api, cfg.eThought.slice(0, 30)), await open("A", "yatharth", true, "A", cfg.api, cfg.eThought.slice(0, 30)),
              await open("full", "yatharth", false, "", cfg.api, cfg.eThought.slice(0, 30))];
for (const p of edge) out.edge[p.name] = [await geom(p.page)];
// the CLI's result frame: the backend files "waiting" (a stop a few seconds back, so the span it opens has a length at the build
// that reads it)
fs.appendFileSync(cfg.apiStates, JSON.stringify({ t: Math.floor(Date.now() / 1000) - 3, state: "waiting" }) + "\n");
for (const p of edge) {
  try { await p.page.waitForFunction(() => Array.from(document.querySelectorAll("#content .turn-elapsed .fork-spot")).some((n) => n.getClientRects().length > 0), null, { timeout: 15000 }); }
  catch (e) { out.edgeDebug = out.edgeDebug || {}; out.edgeDebug[p.name] = { status: p.io.status[cfg.api], rows: await geom(p.page) }; }
  await painted(p.page);
  out.edge[p.name].push(await geom(p.page));
  await p.page.close();
}
// THE CUT CALLS (tests): look E, and look A beside it for the dots
for (const [name, theme, look] of [["E", "yatharth", "E"], ["E-light", "yatharth-light", "E"], ["A", "yatharth", "A"]]) {
  const p = await open(name, theme, true, look, cfg.tests, cfg.cReply.slice(0, 30));
  out.cuts[name] = await p.page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll("#content .turn")).filter((t) => t.getClientRects().length > 0 && /\bturn-(tool|toolgroup)\b/.test(t.className) && !t.classList.contains("tg-child"));
    const mid = (n) => { if (!n) return null; const r = n.getBoundingClientRect(); return r.top + r.height / 2; };
    return rows.map((t) => {
      const d = t.querySelector(":scope > .dot");
      const label = t.querySelector(":scope > .tool-head .tool-label, :scope > .toolgroup-line .toolgroup-head");
      const fs = label ? parseFloat(getComputedStyle(label).fontSize) : null;
      return { group: t.classList.contains("turn-toolgroup"), attached: /\b(toolgroup|tool)-attached\b/.test(t.className), dot: d ? d.className : "",
               dotShown: !!d && getComputedStyle(d).visibility === "visible", dotOff: d && label ? Math.round((mid(d) - mid(label)) * 10) / 10 : null,
               label: label ? (label.textContent || "") : "", fontSize: fs, text: (t.innerText || "").replace(/\s+/g, " ").trim(),
               head: ((t.querySelector(":scope > .tool-head, :scope > .toolgroup-line") || {}).innerText || "").replace(/\s+/g, " ").trim() };
    });
  });
  await shot(p, dir5, name === "E" ? "cuts-E-dark" : name === "A" ? "cuts-A-dark" : "cuts-E-light", cfg.cPrompt1);   // A beside E: the dots
  await p.page.close();
}
out.errors = errors;
fs.writeFileSync(cfg.resultFile, JSON.stringify(out));
fs.writeSync(1, "RESULT:ok\n");
await browser.close();
process.exit(0);
"""


class ServedNarrationSteps(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="narration-steps-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states", "narration"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # this lab mints its own state root: no per-session host (root CLAUDE.md)
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        now = int(time.time())
        web_first, cls.steps, web_marks = _web(cwd, now - 3600)
        api_recs, api_marks = _api(cwd, now - 1800)
        tests_recs, tests_marks = _tests(cwd, now - 900)
        # the store as the kernel's writer leaves it: a row per frame that listed a note, filed as the frame arrived (the stepped
        # turn's marks stand before their records land)
        for sid, name, recs, marks in ((SID_WEB, "web", web_first, web_marks), (SID_API, "api", api_recs, api_marks),
                                       (SID_TESTS, "tests", tests_recs, tests_marks)):
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": MODEL, "liveModel": "Opus 5.5"}))
            Path(state, "narration", sid + ".jsonl").write_text("".join(json.dumps({"u": u, "i": [0], "k": [0]}) + "\n" for u in marks))
        cls.web_path = os.path.join(proj, SID_WEB + ".jsonl")
        cls.api_states = os.path.join(state, "states", SID_API + ".jsonl")
        cls.port, cls.token = _free_port(), "testtok-narration-steps"
        # the fake Agent SDK on the kernel's path (tests/fixtures/fake_agent_sdk): with no SDK at all every idle session wears the
        # missing-SDK error card at its end, a row no real turn ends on (it would take the turn's "worked …" line). No session
        # here is driven, so the fake runs nothing
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              PYTHONPATH=os.path.join(HERE, "fixtures", "fake_agent_sdk"))
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(ROOT, "bin", "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
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

    @classmethod
    def _result(cls):
        if getattr(cls, "_r", None) is None:          # one drive serves every test (the steps land once)
            try:
                cls._r = ("ok", cls._drive())
            except BaseException as e:                # a skip or a failure is the same for the next test
                cls._r = ("raise", e)
        if cls._r[0] == "raise":
            raise cls._r[1]
        return cls._r[1]

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "web": SID_WEB, "api": SID_API,
                       "tests": SID_TESTS, "p2": P2, "steps": cls.steps, "webPath": cls.web_path, "apiStates": cls.api_states,
                       "eThought": E_THOUGHT, "cReply": C_REPLY, "cPrompt1": C_PROMPT_1,
                       "shots5": os.environ.get("NARRATION_SHOTS_REALISTIC5", ""),
                       "resultFile": os.path.join(cls.lab, "result.json")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        if p.returncode != 0 or "RESULT:ok" not in p.stdout:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        with open(os.path.join(cls.lab, "result.json")) as f:
            return json.load(f)

    def _moved(self, before, after):
        """The rows painted in `before` whose place changed in `after` (more than half a pixel)."""
        now = {r["key"]: r for r in after}
        return [(r["key"], r["text"][:40], r["top"], now[r["key"]]["top"]) for r in before
                if r["key"] in now and abs(now[r["key"]]["top"] - r["top"]) > 0.5]

    @staticmethod
    def _split(rows, prompt):
        i = next(k for k, r in enumerate(rows) if r["kind"] == "user" and prompt in r["text"])
        return rows[:i], rows[i + 1:]

    def test_while_the_turn_is_open_no_painted_row_moves_and_no_spot_or_footer_appears_in_any_look(self):
        r = self._result()
        self.assertEqual(r["errors"], [])
        for page, seq in r["steps"].items():
            self.assertEqual([s["step"] for s in seq], ["open"] + [s["name"] for s in self.steps], page)
            for prev, cur in zip(seq, seq[1:]):
                where = "%s, %s -> %s" % (page, prev["step"], cur["step"])
                self.assertEqual(self._moved(prev["rows"], cur["rows"]), [], "a painted row moved: " + where)
                done, live = self._split(cur["rows"], P2)
                # the finished turn keeps its one spot, inline in its last row's "worked …" line, from the first step to the last
                self.assertEqual([x["footSpot"] for x in done if x["spot"]], [True], where)
                self.assertTrue(done[-1]["footSpot"], where)
                if cur["step"] != "reply":
                    self.assertEqual([x["text"][:40] for x in live if x["spot"] or x["foot"]], [],
                                     "the open turn carries a fork spot or a worked line: " + where)
            # the turn's end: its spot lands in the reply's worked line, the turn's last row, and nowhere else in the turn
            _, live = self._split(seq[-1]["rows"], P2)
            self.assertIn(R2[:30], live[-1]["text"], page)
            self.assertEqual([x["kind"] for x in live if x["spot"]], ["assistant"], (page, r["lastStatus"]))
            self.assertTrue(live[-1]["footSpot"], page)
        # the kernel's turnOpen, as the frames carried it: open at every step but the reply's, whatever chip the step forced
        self.assertEqual(r["turnOpen"], [[s["name"], s["name"] != "reply"] for s in self.steps])
        self.assertIn("working", r["runningDot"])
        self.assertIn("dot-nav", r["runningDot"], "the running line's dot keeps its own classes")
        self.assertNotIn("green", r["runningDot"])

    def test_a_turn_that_ends_on_a_thought_row_after_one_call_moves_nothing_at_its_end(self):
        r = self._result()
        for page, (before, after) in r["edge"].items():
            self.assertEqual(self._moved(before, after), [], page)
            self.assertEqual([x["kind"] for x in before if x["spot"] or x["foot"]], [], page + ": nothing before the turn ends")
            self.assertEqual([x["kind"] for x in after if x["spot"]], [after[-1]["kind"]], (page, r.get("edgeDebug")))
            self.assertTrue(after[-1]["footSpot"], (page, after[-1]))
            self.assertIn(after[-1]["kind"], ("thought", "thinking"), page)

    def test_a_cut_call_says_no_result_on_its_row_and_on_a_line_in_look_e(self):
        r = self._result()
        e = [x for x in r["cuts"]["E"] if x["attached"]]
        singles = [x for x in e if not x["group"]]
        lines = [x for x in e if x["group"]]
        a = r["cuts"]["A"]
        a_singles = [x for x in a if not x["group"]]
        self.assertEqual(len(singles), 2, e)
        self.assertEqual(len(a_singles), 2, a)
        for x, ax in zip(singles, a_singles):   # the cut Edit and the cut Read: A's plain ring, shown, and A's words
            self.assertRegex(x["dot"], r"\bring\b")
            self.assertNotRegex(x["dot"], r"\b(green|err|working)\b")
            self.assertTrue(x["dotShown"], x)
            self.assertRegex(ax["dot"], r"\bring\b")
            self.assertEqual(x["head"], ax["head"], "the row says what A's row says")
        self.assertEqual(len(lines), 1, e)
        self.assertRegex(lines[0]["dot"], r"\bring\b")
        self.assertTrue(lines[0]["dotShown"], lines[0])
        self.assertIn("· 1 no result", lines[0]["text"])
        # one font size for a one-call row and a line (the font rule)
        self.assertEqual({x["fontSize"] for x in e}, {lines[0]["fontSize"]}, e)
        # the dots sit on their labels as A's do (A draws the same calls as rows and a group)
        a_single = [x["dotOff"] for x in a if not x["group"]]
        a_group = [x["dotOff"] for x in a if x["group"]]
        self.assertTrue(a_single and a_group, a)
        for x in singles:
            self.assertLessEqual(abs(x["dotOff"] - a_single[0]), 1.5, (x, a_single))
        self.assertLessEqual(abs(lines[0]["dotOff"] - a_group[0]), 1.5, (lines[0], a_group))


if __name__ == "__main__":
    unittest.main()
