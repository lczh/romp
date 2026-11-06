#!/usr/bin/env python3
"""Progress notes read as the agent's prose on the real /chat page of a hermetic kernel (2026-11-06).

On Claude Opus 5.5 a note the model writes between tool calls comes back as a `thinking` block, and the chat drew it as a
collapsed Thought row, so a reply to a message sent mid-turn never reached the person. The CLI lists those blocks on the
frames it streams; the kernel files the list (kernel/narration.py) and the chat build draws a listed block with words as an
assistant message flagged `narration`. Here, on the served page, in compact mode and in full mode:

- a progress note before the first tool and the note answering the mid-turn message are prose (`.turn-assistant`, the
  `turn-narration` hook), in order, between the tools;
- the reasoning summary of the same turn stays a Thought row (compact) or the clamped thinking row (full);
- an older turn's note with no mark (history from before the store) stays a Thought row;
- LIVE: the turn's second half lands in the transcript while the page is open; the note answering the mid-turn message
  arrives as prose through the push, without a reload;
- after a reload the page draws the same.

The lab writes the store as the kernel's own writer would have (its marks are filed as frames arrive, so a mark can stand
before its record lands, which is how the live half is staged). With NARRATION_SHOTS=<dir> the driver writes screenshots
(compact and full, dark and light, plus option B, the subtle variant, under -optB names); NARRATION_BEFORE_ROOT=<checkout>
serves another checkout's kernel and built bundle for the before shots and skips the assertions. Skips LOUDLY without the
extension deps or a Playwright browser; the extension CI job requires it. SYNTHETIC fixtures only: the notes-api demo
world, session `web`, invented text, placeholder ids, host TESTHOST."""
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

SID = "aaaaaaaa-5555-2222-3333-444444444444"
MODEL = "claude-opus-5-5"
SIG = "c3ludGhldGljLXNpZ25hdHVyZQ=="   # a synthetic signature: every block is signed, as the API's are
HISTORY_NOTE = "Looking at the router file to list the routes before answering."
REASONING = "The list handler builds one query with no limit. A cursor on the note id stays stable when notes are added, an offset does not."
NOTE_1 = "The list handler returns every row. I'll read it and its test, then add a cursor on the note id with a default page size."
MIDTURN = "keep the default page size at 50, not 20"
NOTE_2 = "Got it: the default page size is 50, not 20. I'm changing it and updating the one test that pinned 20."
REPLY = "Pagination is in. `GET /notes` takes `limit` (default 50) and `cursor`, and the notes tests pass."
REPLY_SHOWN = REPLY.replace("`", "")            # the reply as the page draws it (inline code is markup)
OPTION_B_CSS = (".turn-narration { padding-top: 3px; padding-bottom: 3px; }"
                " .turn-narration > .dot.ring { background: var(--dim); }")


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _records(cwd, base):
    """(first part, live part, the uuids to mark). One content block per record, as Claude Code writes it. An older turn
    whose note carries no mark; then the turn under test: a reasoning summary and a note before the first tool, a second
    tool, the mid-turn message, then (the live part) the note answering it, two tools and the reply."""
    recs, prev, n, t = [], [None], [0], [base]
    marks = []

    def add(rec):
        n[0] += 1
        rec.update(sessionId=SID, parentUuid=prev[0], uuid="bbbbbbbb-5555-2222-3333-%012d" % n[0],
                   timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t[0])))
        recs.append(rec)
        prev[0] = rec["uuid"]
        t[0] += 3
        return rec["uuid"]

    def message(blocks, think_tokens=0, stop="tool_use", mark=()):
        mid = "msg_%06d" % (n[0] + 1)
        usage = {"input_tokens": 10, "output_tokens": 200, "output_tokens_details": {"thinking_tokens": think_tokens}} if think_tokens \
            else {"input_tokens": 10, "output_tokens": 50}
        for i, b in enumerate(blocks):
            u = add({"type": "assistant", "message": {"id": mid, "role": "assistant", "model": MODEL, "stop_reason": stop,
                                                       "content": [b], "usage": usage}})
            if i in mark:
                marks.append(u)

    def think(text=""):
        return {"type": "thinking", "thinking": text, "signature": SIG}

    tc = [0]

    def tool(name, inp, out, thinks=(), tokens=0, mark=()):
        tc[0] += 1
        tid = "toolu_narr_%02d" % tc[0]
        message(list(thinks) + [{"type": "tool_use", "id": tid, "name": name, "input": inp}], tokens, mark=mark)
        add({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": out}]},
             "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})

    src = cwd + "/src/routes/notes.ts"
    add({"type": "user", "message": {"role": "user", "content": "Which routes does the notes service have?"}})
    tool("Bash", {"command": "grep -n router src/routes/notes.ts", "description": "Listed the routes"}, "3 routes",
         [think(HISTORY_NOTE)], 600)
    message([{"type": "text", "text": "Three: list, get and create."}], stop="end_turn")
    add({"type": "user", "message": {"role": "user", "content": "Add cursor pagination to GET /notes."}})
    tool("Read", {"file_path": src}, "export function listNotes(db) { return db.all('select * from notes'); }",
         [think(REASONING), think(NOTE_1)], 1840, mark=(1,))
    tool("Read", {"file_path": cwd + "/tests/notes.test.ts"}, "test('lists notes', ...)", [think()], 420)
    add({"type": "attachment", "isSidechain": False, "attachment": {"type": "queued_command", "prompt": MIDTURN}})
    first = len(recs)
    tool("Edit", {"file_path": src, "old_string": "limit = 20", "new_string": "limit = 50"}, "edited", [think(NOTE_2)], 960, mark=(0,))
    tool("Bash", {"command": "npm test -- notes", "description": "Ran the notes tests"}, "3 passing", [think()], 300)
    message([{"type": "text", "text": REPLY}], stop="end_turn")
    return recs[:first], recs[first:], marks


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
const open = async (theme, compact, w, h, scale, wait) => {
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: scale });
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.addInitScript(([th, c]) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: th, compact: c })); } catch (e) {} }, [theme, compact]);
  await page.goto(cfg.chat);
  await page.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), wait, { timeout: 30000 });
  await page.addStyleTag({ content: "#content .turn-apierror { display: none !important; }" });   // the lab kernel runs no SDK backend: its notice is not this test's
  await painted(page);
  return page;
};
// the agent's side of the chat, in order: what each row is and the words it shows
const rows = (page) => page.evaluate(() => Array.from(document.querySelectorAll("#content .turn")).map((t) => {
  const c = t.className;
  const kind = c.includes("turn-thought") ? "thought" : c.includes("turn-thinking") ? "thinking" : c.includes("turn-assistant") ? "assistant"
             : c.includes("turn-user") ? "user" : c.includes("turn-toolgroup") ? "toolgroup" : c.includes("turn-tool") ? "tool" : "other";
  const body = t.querySelector(".assistant.md") || t.querySelector(".thought-gist") || t.querySelector(".thinking") || t.querySelector(".user-bubble");
  return { kind, narration: c.includes("turn-narration"), opaque: !!t.querySelector(".thinking.encrypted"), fork: !!t.querySelector(".fork-spot"), text: body ? (body.innerText || body.textContent || "").replace(/\s+/g, " ").trim() : "",
           uuid: t.dataset.uuid || null };
}));
const shot = async (page, name) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await page.evaluate((p) => { const t = Array.from(document.querySelectorAll("#content .turn-user")).find((n) => (n.textContent || "").includes(p)); if (t) t.scrollIntoView({ block: "start" }); }, cfg.prompt);
  await painted(page);
  await page.screenshot({ path: cfg.shots + "/" + name + ".png" });
};
const out = {};
// LIVE: the page holds the first half; the second half lands while it is open and arrives through the push
const live = await open("yatharth", true, 1200, 1000, 1, cfg.midturn);
out.beforeLand = await rows(live);
fs.writeFileSync(cfg.landFlag, "go");
await live.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.replyStart, { timeout: 30000 });
await painted(live);
out.live = await rows(live);
await live.close();
// AFTER RELOAD, both modes, and the shots
for (const [compact, mname] of [[true, "compact"], [false, "full"]]) {
  for (const [theme, tname] of [["yatharth", "dark"], ["yatharth-light", "light"]]) {
    const p = await open(theme, compact, 1200, 1000, 1, cfg.replyStart);
    if (tname === "dark") out[mname] = await rows(p);
    out[mname + "Light_" + tname] = await p.evaluate(() => document.body.classList.contains("theme-light"));
    await shot(p, "narration-" + mname + "-" + tname);
    if (cfg.shots && !cfg.before) {
      await p.addStyleTag({ content: cfg.optB });
      await painted(p);
      await shot(p, "narration-" + mname + "-" + tname + "-optB");
    }
    await p.close();
  }
}
out.errors = errors;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedNarration(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="narration-served-")
        cls.before = os.environ.get("NARRATION_BEFORE_ROOT", "")
        root = cls.before or ROOT
        if not cls.before:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(root, "vscode-extension", "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states", "narration"):
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
        cls.first, cls.rest, cls.marks = _records(cwd, int(time.time()) - 1800)
        cls.tpath = os.path.join(proj, SID + ".jsonl")
        Path(cls.tpath).write_text("".join(json.dumps(r) + "\n" for r in cls.first))
        # the store as the kernel's writer leaves it: one row per frame that listed a note, filed as the frame arrived (so
        # the live half's mark stands before its record lands)
        Path(state, "narration", SID + ".jsonl").write_text("".join(json.dumps({"u": u, "i": [0], "k": [0]}) + "\n" for u in cls.marks))
        cls.port, cls.token = _free_port(), "testtok-narration"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(root, "bin", "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
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
        flag = os.path.join(self.lab, "land.flag")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "midturn": MIDTURN,
                       "prompt": "Add cursor pagination", "replyStart": "Pagination is in.", "landFlag": flag,
                       "shots": os.environ.get("NARRATION_SHOTS", ""), "before": bool(self.before), "optB": OPTION_B_CSS}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.Popen(["node", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        # the live half lands once the driver holds the first: the driver drops the flag, the transcript grows
        deadline = time.time() + 120
        while not os.path.exists(flag) and p.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if os.path.exists(flag):
            with open(self.tpath, "a") as f:
                f.write("".join(json.dumps(r) + "\n" for r in self.rest))
        stdout, stderr = p.communicate(timeout=300)
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + stdout[-3000:] + stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + stdout[-3000:] + stderr[-2000:])
        return json.loads(line[len("RESULT:"):])

    def _agent_rows(self, rows, after_prompt):
        """The rows from the turn under test's prompt on, tools and the tool runs collapsed to one kind."""
        i = next(k for k, r in enumerate(rows) if r["kind"] == "user" and after_prompt in r["text"])
        return [(r["kind"] if r["kind"] not in ("toolgroup",) else "tool", r["narration"], r["text"]) for r in rows[i + 1:]
                if r["kind"] != "other" and not r.get("opaque")]   # a textless block's token line (full mode) is not under test

    def test_progress_notes_read_as_prose_live_and_after_reload_in_compact_and_full_mode(self):
        r = self._drive()
        if self.before:
            self.skipTest("a before-the-change checkout: screenshots only, the assertions describe the change")
        self.assertEqual(r["errors"], [], "no page errors")
        note = lambda t: ("assistant", True, t)
        # LIVE, compact: before the second half lands, the first note is prose and the reasoning a Thought row
        pre = self._agent_rows(r["beforeLand"], "Add cursor pagination")
        self.assertIn(note(NOTE_1), pre, "the first note is prose before anything lands: %r" % pre)
        self.assertEqual([x for x in pre if x[0] in ("thought", "assistant")][:2], [("thought", False, REASONING), note(NOTE_1)],
                         "the reasoning summary stays a Thought row, and the note follows it as prose: %r" % pre)
        live = self._agent_rows(r["live"], "Add cursor pagination")
        self.assertIn(note(NOTE_2), live, "the answer to the mid-turn message arrives as prose through the push: %r" % live)
        mid = next(k for k, x in enumerate(live) if x[0] == "user" and MIDTURN in x[2])
        self.assertEqual(live[mid + 1], note(NOTE_2), "the answer sits right after the mid-turn message: %r" % live[mid:])
        self.assertEqual(live[-1], ("assistant", False, REPLY_SHOWN), "the reply is a reply, not a note")
        # AFTER RELOAD: compact, and full, where the reasoning is the clamped thinking row
        compact = self._agent_rows(r["compact"], "Add cursor pagination")
        self.assertEqual([x for x in compact if x[0] != "tool"],
                         [("thought", False, REASONING), note(NOTE_1), ("user", False, MIDTURN), note(NOTE_2),
                          ("assistant", False, REPLY_SHOWN)], "compact after reload: %r" % compact)
        full = self._agent_rows(r["full"], "Add cursor pagination")
        self.assertEqual([x for x in full if x[0] in ("thinking", "assistant", "user") and x[2]],
                         [("thinking", False, REASONING), note(NOTE_1), ("user", False, MIDTURN), note(NOTE_2),
                          ("assistant", False, REPLY_SHOWN)], "full after reload: %r" % full)
        # a note is the agent speaking mid-turn, not a response a fork cuts below: no fork row under it
        notes = [x for x in r["compact"] + r["full"] if x["narration"]]
        self.assertEqual(len(notes), 4)
        self.assertFalse(any(x["fork"] for x in notes), "no fork row under a note: %r" % notes)
        # history with no mark keeps its Thought row
        hist = [x for x in r["compact"] if x["kind"] in ("thought", "assistant") and HISTORY_NOTE[:30] in x["text"]]
        self.assertEqual([(x["kind"], x["narration"]) for x in hist], [("thought", False)], "unmarked history: %r" % hist)


if __name__ == "__main__":
    unittest.main()
