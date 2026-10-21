#!/usr/bin/env python3
"""Whether the chat page takes End or leaves it to the browser, and what End then scrolls or moves, executed in a real browser on
test_end_key_served.py's lab (its kernel, its sessions, its transcript and its driver's helpers) with classic scrollbars and, added to
web's transcript, a Bash call with a short output, a reply with two videos (one with controls, its clip written into the message as a
data: address, one with none and a tabindex) and an
audio whose clip holds no frames, an incoming question by mail whose long body has a link in its first line (its sent row in the postal
log), and a long reply. One driver run, on the page's own keys:
  1. a click on a line of the reply, then a press on the scrollbar of the opened Bash output box, then End; the same with the scrollbar
     dragged;
  2. a click on the text of that box at its top, then a press on the short Bash call's fold toggle, then End;
  3. two words typed in the message box, a press on that fold toggle, then the selection collapsed outside the transcript by a script,
     then End;
  4. a click on a line of the reply, then a press on the strip's resize grip, then End;
  5. End with focus on the video that has controls, its clip loaded, then on the one that has none, its clip loaded;
  6. End with focus on the link in the mail's body, the body at its top;
  7. End with focus on an audio with controls whose clip holds no frames;
  8. step 4's road, then step 1's (one press on the scrollbar), each with text then written into the message box by a script, then
     End.
Listeners the lab adds on the window after the page's own record whether the page took each End (defaultPrevented) and the times of
the last selectionchange and the last pointerdown. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures
only (the notes-api demo world)."""
import base64
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import unittest
import wave
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_end_key_served as E   # noqa: E402  the lab: its boot, its transcript and its driver's helpers
import test_file_tabs_served as F   # noqa: E402

BAR = "#" * 44
MAIL = ("Which of the notes-api rules should stay? The list is in [the rules page](https://example.test/rules).\n\n"
        + "".join("- rule %d: the handler keeps the note's id and title\n" % i for i in range(1, 61)))


def _tone_data_url():
    """Three seconds of a tone as a data: address, 8-bit at 8 kHz so the message stays small. The video with controls plays
    it: since 2026-10-21 the chat turns a video on a web address into a still that opens the lightbox and has no controls
    (preview.ts makeVideoStill), so the transcript's video with its own controls is one the lightbox never opens, a clip
    written into the message, which plays where it is."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(bytes(int(128 + 100 * math.sin(2 * math.pi * 440 * i / 8000)) for i in range(3 * 8000)))
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


VIDEOS = ("Clips of the notes api, two as video and one as audio.\n\n"
          '<video id="lab-video" controls src="' + _tone_data_url() + '"></video>\n\n'
          '<video id="lab-video-bare" tabindex="0" src="https://media.test/clip.wav"></video>\n\n'
          '<audio id="lab-empty" controls src="https://media.test/empty.wav"></audio>')
TAIL = "The rest of the plan for the notes api.\n\n" + "".join("- Step line %d: the handler writes the note back.\n" % i for i in range(1, 81))

STEPS = r"""
// the times of the last selectionchange and the last pointerdown
await page.evaluate(() => { window.__selT = -1; window.__pressT = -1;
  window.addEventListener("selectionchange", (e) => { window.__selT = e.timeStamp; }, true);
  window.addEventListener("pointerdown", (e) => { window.__pressT = e.timeStamp; }, true); });
// the Bash call's long output opened (a box that scrolls on its own), and the short Bash call's fold toggle
await page.locator("#content .tool-fold-toggle", { hasText: /^200 lines$/ }).click();
await wait(page, () => { const f = Array.from(document.querySelectorAll("#content .tool-io-fold")).find((e) => e.offsetParent !== null); return !!f && f.scrollHeight > f.clientHeight + 100; }, null, "the tool output never opened as a scroll box");
await page.evaluate(() => { window.__box = () => Array.from(document.querySelectorAll("#content .tool-io-fold")).find((e) => e.offsetParent !== null && e.scrollHeight > e.clientHeight + 100) || null;
  window.__short = () => Array.from(document.querySelectorAll("#content .tool-fold-toggle")).find((e) => e.offsetParent !== null && !/^200 lines$/.test(e.textContent.trim())) || null; });
// where focus, the selection's anchor and the last press sit, against the box, the short toggle, the strip's resize grip and the footer
// (anchorInFooter); the box's scroll and the transcript's; the two times; the message box's text
const at = () => page.evaluate(() => {
  const c = document.getElementById("content"), s = document.getSelection(), a = s && s.anchorNode, ae = a && (a.nodeType === 1 ? a : a.parentElement), f = document.activeElement;
  const p = window.__press, pe = p && (p.nodeType === 1 ? p : p.parentElement), b = window.__box(), t = window.__short();
  return { bodyFocused: f === document.body, anchorIn: !ae ? null : c.contains(ae) ? "content" : "other", anchorInBox: !!b && !!ae && b.contains(ae),
           anchorInFooter: !!ae && !!ae.closest("#footer"), pressIn: !pe ? null : c.contains(pe) ? "content" : "other", pressIsBox: !!b && p === b,
           pressOnGrip: !!pe && pe.id === "tabbar-resize",
           pressOnShort: !!t && !!pe && t.contains(pe), box: b ? { st: Math.round(b.scrollTop), max: b.scrollHeight - b.clientHeight } : null,
           top: Math.round(c.scrollTop), dist: Math.round(c.scrollHeight - c.scrollTop - c.clientHeight), ch: c.clientHeight,
           selT: window.__selT, pressT: window.__pressT, draft: document.getElementById("composer-input").value }; });
const step = async () => { const before = await at(); const r = await press("End"); return { before, taken: r.taken, after: await at() }; };
const boxTop = async () => { await page.evaluate(() => { const b = window.__box(); b.scrollTop = 0; b.scrollIntoView({ block: "center" }); }); await settle(); };
const pressShort = async () => { await page.evaluate(() => window.__short().scrollIntoView({ block: "center" })); await settle();
  const p = await page.evaluate(() => { const b = window.__short().getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; });
  await page.mouse.click(p.x, p.y); await settle(); };

// 1. a caret on a line of the reply, then a press on the box's scrollbar thumb (then the thumb dragged 40 px), then End
for (const [k, drag] of [["sbar", 0], ["sbarDrag", 40]]) {
  await boxTop(); await unselect();
  await page.evaluate(() => Array.from(document.querySelectorAll("#content li")).find((e) => e.offsetParent !== null && /Plan line 3:/.test(e.textContent)).scrollIntoView({ block: "center" }));
  await settle(); await line(3).click({ position: { x: 20, y: 4 } }); await settle();
  await boxTop();
  const g = await page.evaluate(() => { const b = window.__box(), r = b.getBoundingClientRect(), cs = getComputedStyle(b);
    const w = b.offsetWidth - b.clientWidth - (parseFloat(cs.borderLeftWidth) || 0) - (parseFloat(cs.borderRightWidth) || 0);
    return { x: Math.round(r.right - (parseFloat(cs.borderRightWidth) || 0) - Math.max(2, w / 2)), y: Math.round(r.top + 10), w }; });
  await page.mouse.move(g.x, g.y); await page.mouse.down();
  if (drag) await page.mouse.move(g.x, g.y + drag, { steps: 5 });
  await page.mouse.up(); await settle();
  out[k] = Object.assign({ thumb: g }, await step());
}

// 2. a caret in the box at its top, then a press on the short Bash call's fold toggle, then End
await toTop(); await settle(); await boxTop(); await unselect();
const fb = await page.evaluate(() => { const r = window.__box().getBoundingClientRect(); return { x: r.left + 120, y: r.top + r.height / 2 }; });
await page.mouse.click(fb.x, fb.y); await settle();
await pressShort();
out.box = await step();

// 3. two words typed in the message box, a press on the short toggle, then the selection collapsed on the strip's tab of web, then End
await toTop(); await settle(); await unselect();
await page.click("#composer-input"); await page.keyboard.type("a draft"); await settle();
await pressShort();
out.later = { afterPress: await at() };
await page.evaluate((sid) => { const tab = document.querySelector('#tabs .tab[data-id="' + sid + '"]');
  const w = document.createTreeWalker(tab, NodeFilter.SHOW_TEXT, { acceptNode: (n) => n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP });
  document.getSelection().collapse(w.nextNode(), 1); }, cfg.web);
await settle();
Object.assign(out.later, await step());

// 4. a caret on a line of the reply, then a press on the strip's resize grip (outside the transcript), then End
await toTop(); await settle(); await unselect();
await line(3).click({ position: { x: 20, y: 4 } }); await settle();
const grip = await page.evaluate(() => { const g = document.getElementById("tabbar-resize"), b = g.getBoundingClientRect(), x = Math.round(b.left + b.width / 4), y = Math.round(b.top + b.height / 2);
  return { x, y, hit: document.elementFromPoint(x, y) === g }; });
await page.mouse.click(grip.x, grip.y); await settle();
out.pressOut = Object.assign({ grip }, await step());

// 5. each video, brought to the middle of the pane with its clip loaded at 0 s, focused, then End
const video = (sel) => page.evaluate((s) => { const v = window.__vis(s), c = document.getElementById("content");
  return { t: v.currentTime, d: v.duration, ended: v.ended, controls: v.controls, readyState: v.readyState, focused: document.activeElement === v, inContent: c.contains(v) }; }, sel);
for (const [k, sel] of [["video", '[id="user-content-lab-video"]'], ["videoBare", '[id="user-content-lab-video-bare"]']]) {
  await unselect();
  await page.evaluate((s) => window.__vis(s).scrollIntoView({ block: "center" }), sel); await settle();
  await wait(page, (s) => window.__vis(s).readyState >= 1, sel, "the video's clip never loaded");
  await page.evaluate((s) => { const v = window.__vis(s); v.currentTime = 0; v.focus({ preventScroll: true }); }, sel); await settle();
  const v0 = await video(sel), s = await step();
  out[k] = Object.assign(s, { v0, v1: await video(sel) });
}

// 6. the mail's body opened (a click on its head when it is folded) at its top, brought to the middle of the pane, focus on its link,
//    then End
const mail = () => page.evaluate(() => { const b = Array.from(document.querySelectorAll("#content .turn-postal-service .notice-body")).find((e) => e.offsetParent !== null), c = document.getElementById("content"), f = document.activeElement;
  return b ? { st: Math.round(b.scrollTop), max: b.scrollHeight - b.clientHeight, oy: getComputedStyle(b).overflowY, linkFocused: !!f && f.tagName === "A" && b.contains(f), inContent: c.contains(b) } : null; });
await unselect();
await wait(page, () => !!document.querySelector("#content .turn-postal-service"), null, "the mail never showed");
if (await page.evaluate(() => { const b = document.querySelector("#content .turn-postal-service .notice-body"); return !b || b.offsetParent === null; })) {
  await page.evaluate(() => document.querySelector("#content .turn-postal-service").scrollIntoView({ block: "center" })); await settle();
  await page.locator("#content .turn-postal-service .notice-head").first().click(); await settle();
}
await wait(page, () => Array.from(document.querySelectorAll("#content .turn-postal-service .notice-body a")).some((e) => e.offsetParent !== null), null, "the mail's body never showed its link");
await page.evaluate(() => { const b = Array.from(document.querySelectorAll("#content .turn-postal-service .notice-body")).find((e) => e.offsetParent !== null); b.scrollTop = 0; b.scrollIntoView({ block: "center" }); });
await settle();
await page.evaluate(() => Array.from(document.querySelectorAll("#content .turn-postal-service .notice-body a")).find((e) => e.offsetParent !== null).focus({ preventScroll: true }));
await settle();
{ const m0 = await mail(), s = await step(); out.mailLink = Object.assign(s, { m0, m1: await mail() }); }

// 7. the audio whose clip holds no frames, brought to the middle of the pane, focused, then End
const EMPTY = '[id="user-content-lab-empty"]';
const empty = () => page.evaluate((s) => { const m = window.__vis(s), c = document.getElementById("content");
  return { d: String(m.duration), finite: isFinite(m.duration), ended: m.ended, controls: m.controls, readyState: m.readyState, focused: document.activeElement === m, inContent: c.contains(m) }; }, EMPTY);
await unselect();
await page.evaluate((s) => window.__vis(s).scrollIntoView({ block: "center" }), EMPTY); await settle();
await wait(page, (s) => window.__vis(s).readyState >= 1, EMPTY, "the clip with no frames never loaded");
await page.evaluate((s) => window.__vis(s).focus({ preventScroll: true }), EMPTY); await settle();
{ const a0 = await empty(), s = await step(); out.emptyAudio = Object.assign(s, { a0 }); }

// 8. step 4's road, then 1's (the box's scrollbar pressed), each with text then written into the message box by a script with an input
//    event (the box not focused), then End
const draft = () => page.evaluate(() => { const ta = document.getElementById("composer-input"); ta.value += " back"; ta.dispatchEvent(new Event("input", { bubbles: true })); });
await toTop(); await settle(); await unselect();
await line(3).click({ position: { x: 20, y: 4 } }); await settle();
{ const g = await page.evaluate(() => { const g = document.getElementById("tabbar-resize"), b = g.getBoundingClientRect(), x = Math.round(b.left + b.width / 4), y = Math.round(b.top + b.height / 2);
    return { x, y, hit: document.elementFromPoint(x, y) === g }; });
  await page.mouse.click(g.x, g.y); await settle();
  const afterPress = await at(); await draft(); await settle();
  out.pressOutDraft = Object.assign({ grip: g, afterPress }, await step()); }
await boxTop(); await unselect();
await page.evaluate(() => Array.from(document.querySelectorAll("#content li")).find((e) => e.offsetParent !== null && /Plan line 3:/.test(e.textContent)).scrollIntoView({ block: "center" }));
await settle(); await line(3).click({ position: { x: 20, y: 4 } }); await settle();
await boxTop();
{ const g = await page.evaluate(() => { const b = window.__box(), r = b.getBoundingClientRect(), cs = getComputedStyle(b);
    const w = b.offsetWidth - b.clientWidth - (parseFloat(cs.borderLeftWidth) || 0) - (parseFloat(cs.borderRightWidth) || 0);
    return { x: Math.round(r.right - (parseFloat(cs.borderRightWidth) || 0) - Math.max(2, w / 2)), y: Math.round(r.top + 10), w }; });
  await page.mouse.move(g.x, g.y); await page.mouse.down(); await page.mouse.up(); await settle();
  const afterPress = await at(); await draft(); await settle();
  out.sbarDraft = Object.assign({ thumb: g, afterPress }, await step()); }
"""

# a clip with no frames at media.test/empty.wav, routed on the page (a page's route goes before its context's)
EMPTY_CLIP = r"""
await page.route("https://media.test/empty.wav", (route) => {
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36, 4); h.write("WAVE", 8); h.write("fmt ", 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(8000, 24); h.writeUInt32LE(16000, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(0, 40);
  const range = route.request().headers()["range"], m = range && /bytes=(\d+)-(\d*)/.exec(range);
  if (!m) return route.fulfill({ status: 200, body: h, headers: { "content-type": "audio/wav", "accept-ranges": "bytes" } });
  const a = +m[1], b = m[2] ? Math.min(+m[2], h.length - 1) : h.length - 1;
  if (a >= h.length) return route.fulfill({ status: 416, body: "", headers: { "content-range": "bytes */" + h.length } });
  return route.fulfill({ status: 206, body: h.subarray(a, b + 1), headers: { "content-type": "audio/wav", "accept-ranges": "bytes", "content-range": "bytes " + a + "-" + b + "/" + h.length } });
});
"""

_LAUNCH = "try { browser = await chromium.launch(); }"
_PAGE = "\nconst page = await ctx.newPage();\n"
assert F.DRIVER.count(_LAUNCH) == 1 and E.RUN.count("// 1. the transcript") == 1 and E.RUN.count("}\nawait run().then(") == 1 and E.RUN.count(_PAGE) == 1
RUN = E.RUN[:E.RUN.index("// 1. the transcript")].replace(_PAGE, _PAGE + EMPTY_CLIP) + STEPS + E.RUN[E.RUN.index("}\nawait run().then("):]
DRIVER = (F.DRIVER[:F.DRIVER.index("async function run() {")]
          .replace(_LAUNCH, 'try { browser = await chromium.launch({ ignoreDefaultArgs: ["--hide-scrollbars"] }); }') + RUN)


class EndKeyLocationServed(unittest.TestCase):
    maxDiff = None
    result = None

    # the lab is test_end_key_served.py's (test_file_tabs_served.py's kernel and sessions, its transcript and clip)
    _boot = classmethod(F.FileTabsServed._boot.__func__)
    tearDownClass = classmethod(F.FileTabsServed.tearDownClass.__func__)
    _run = classmethod(E.EndKeyServed._run.__func__)

    @classmethod
    def setUpClass(cls):
        E.EndKeyServed.setUpClass.__func__(cls)
        try:
            cwd = os.path.join(cls.lab, "notes-api")
            proj = os.path.join(cls.lab, "claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
            sid, t = F.SIDS["web"], int(time.time()) - 300
            banner = "\n".join([BAR, "## \U0001F4EC from api · 10:00", BAR, MAIL, "<!-- romp-msg-id: m-lab-1 -->", "<!-- romp-msg-kind: question -->", BAR,
                                '(to reply, only if substantive: romp mail send --kind delegate|coordinate|question api "...")'])
            recs = [{"type": "user", "timestamp": F.iso(t), "uuid": "u5", "parentUuid": "a4", "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "count the notes, then show the clips as video"}},
                    {"type": "assistant", "timestamp": F.iso(t + 2), "uuid": "a5", "parentUuid": "u5", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
                                 "content": [{"type": "tool_use", "id": "tu10", "name": "Bash", "input": {"command": "python3 count.py"}}]}},
                    {"type": "user", "timestamp": F.iso(t + 3), "uuid": "u6", "parentUuid": "a5", "sessionId": sid,
                     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu10", "content": "notes: 3\ntags: 2\nstatus: ok"}]}},
                    {"type": "assistant", "timestamp": F.iso(t + 5), "uuid": "a6", "parentUuid": "u6", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": VIDEOS}]}},
                    {"type": "user", "timestamp": F.iso(t + 7), "uuid": "u7", "parentUuid": "a6", "promptSource": "sdk", "sessionId": sid,
                     "message": {"role": "user", "content": banner}},
                    {"type": "assistant", "timestamp": F.iso(t + 9), "uuid": "a7", "parentUuid": "u7", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": TAIL}]}}]
            with open(os.path.join(proj, sid + ".jsonl"), "a") as f:
                f.write("".join(json.dumps(r) + "\n" for r in recs))
            # the mail's sent row in the postal log, which names its sender (the kernel joins the banner's id on it)
            os.makedirs(os.path.join(cls.state, "timeline"), exist_ok=True)
            with open(os.path.join(cls.state, "timeline", "messages.jsonl"), "a") as f:
                f.write(json.dumps({"t": t + 6, "ev": "sent", "id": "m-lab-1", "from": "api", "from_id": F.SIDS["api"], "to_id": sid,
                                    "body": MAIL, "kind": "question", "from_host": ""}) + "\n")
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(F.NAMES), "out": out,
                       "web": F.SIDS["web"], "ask": E.ASK, "clip": cls.clip}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(F.EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served lab needs one")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        result = json.loads(Path(out).read_text())
        print("END-KEY-LOCATION:", json.dumps(result), file=sys.stderr)
        if result.get("died"):
            raise AssertionError("driver stopped: %s\nso far: %s\nkernel:\n%s" % (result["died"], json.dumps(result)[-3000:], open(cls.klog).read()[-1500:]))
        return result

    def test_end_after_a_press_on_a_tool_output_boxs_scrollbar_with_a_caret_elsewhere_in_the_transcript_scrolls_the_box(self):
        r = self._run()
        for k in ("sbar", "sbarDrag"):
            s, b = r[k], r[k]["before"]
            self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and not b["anchorInBox"] and b["pressIsBox"] and b["pressT"] > b["selT"],
                            "%s: focus on the page body, a caret in the transcript outside the box, then a press on the box's scrollbar: %r" % (k, s))
            self.assertLess(b["box"]["st"], b["box"]["max"] - 2, "%s: the box can still scroll down: %r" % (k, s))
            self.assertEqual(s["taken"], [False], "%s: the page left End to the browser: %r" % (k, s))
            a = s["after"]
            self.assertTrue(a["box"]["st"] >= a["box"]["max"] - 2 and abs(a["top"] - b["top"]) <= 1, "%s: the box scrolled to its end, the transcript stayed: %r" % (k, s))

    def test_end_with_the_anchor_in_a_box_and_a_later_press_elsewhere_in_the_transcript_is_taken_by_the_page_and_goes_to_the_bottom(self):
        s = self._run()["box"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorInBox"] and b["box"]["st"] < b["box"]["max"] - 2, "focus on the page body, a caret in the box, which can still scroll down: %r" % s)
        self.assertTrue(b["pressOnShort"] and b["pressIn"] == "content" and b["pressT"] > b["selT"] and b["dist"] > b["ch"],
                        "…then a press on another tool row's fold toggle, more than a screen from the bottom: %r" % s)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_after_a_press_in_the_transcript_and_a_later_selection_change_outside_it_is_taken_by_the_page_and_goes_to_the_bottom(self):
        """With the selection's anchor outside the transcript and the last press inside it, Chromium's own End follows the press, not
        the selection, so the page takes End there."""
        s = self._run()["later"]
        p, b = s["afterPress"], s["before"]
        self.assertTrue(p["bodyFocused"] and p["anchorInFooter"] and p["pressOnShort"] and p["pressT"] > p["selT"],
                        "two words typed in the message box, then a press on a tool row's fold toggle in the transcript: %r" % s)
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "other" and b["selT"] > b["pressT"] and b["pressOnShort"] and b["dist"] > b["ch"],
                        "…then the selection moved to the strip, still outside the transcript, after the press, more than a screen from the bottom: %r" % s)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_with_the_anchor_in_the_transcript_and_a_later_press_outside_it_is_left_to_the_browser(self):
        s = self._run()["pressOut"]
        b = s["before"]
        self.assertTrue(s["grip"]["hit"] and b["bodyFocused"] and b["anchorIn"] == "content" and b["pressOnGrip"] and b["pressT"] > b["selT"] and b["dist"] > b["ch"],
                        "focus on the page body, a caret on a line of the transcript, then a press on the strip's resize grip, more than a screen from the bottom: %r" % s)
        self.assertEqual(s["taken"], [False], "the page left End to the browser: %r" % s)

    def test_end_on_a_focused_video_with_controls_and_its_clip_loaded_moves_the_playhead_and_is_left_to_the_browser(self):
        s = self._run()["video"]
        v0, b = s["v0"], s["before"]
        self.assertTrue(v0["focused"] and v0["inContent"] and v0["controls"] and v0["readyState"] >= 1 and v0["d"] == 3 and v0["t"] == 0 and b["dist"] > b["ch"],
                        "focus on a reply's video with controls in the transcript, its clip loaded, at 0 s, more than a screen from the bottom: %r" % s)
        self.assertEqual(s["taken"], [False], "the page left End to the browser: %r" % s)
        self.assertEqual((s["v1"]["t"], s["after"]["top"]), (3, b["top"]), "the playhead went to the end; the transcript stayed: %r" % s)

    def test_end_on_a_focused_video_with_no_controls_is_taken_by_the_page_and_goes_to_the_bottom(self):
        s = self._run()["videoBare"]
        v0, b = s["v0"], s["before"]
        self.assertTrue(v0["focused"] and v0["inContent"] and not v0["controls"] and v0["readyState"] >= 1 and v0["d"] == 3 and b["dist"] > b["ch"],
                        "focus on a reply's video with no controls in the transcript, its clip loaded, more than a screen from the bottom: %r" % s)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_on_a_focused_link_in_a_mail_body_that_can_still_scroll_scrolls_the_body(self):
        s = self._run()["mailLink"]
        m0, b = s["m0"], s["before"]
        self.assertTrue(m0 and m0["linkFocused"] and m0["inContent"] and m0["oy"] == "auto" and m0["st"] == 0 and m0["max"] > 0,
                        "focus on the link in a mail's body in the transcript, the body at its top with room to scroll: %r" % s)
        self.assertEqual(s["taken"], [False], "the page left End to the browser: %r" % s)
        self.assertTrue(s["m1"]["st"] >= s["m1"]["max"] - 2 and abs(s["after"]["top"] - b["top"]) <= 1, "the body scrolled to its end, the transcript stayed: %r" % s)

    def test_end_on_a_focused_audio_whose_clip_holds_no_frames_is_taken_by_the_page_and_goes_to_the_bottom(self):
        s = self._run()["emptyAudio"]
        a0, b = s["a0"], s["before"]
        self.assertTrue(a0["focused"] and a0["inContent"] and a0["controls"] and a0["readyState"] >= 1 and not a0["finite"] and not a0["ended"] and b["dist"] > b["ch"],
                        "focus on a reply's audio with controls in the transcript, its clip holding no frames (a duration that is not finite), more than a screen from the bottom: %r" % s)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_with_the_anchor_in_the_transcript_a_later_press_outside_it_and_then_text_written_into_the_message_box_is_taken_by_the_page(self):
        s = self._run()["pressOutDraft"]
        p, b = s["afterPress"], s["before"]
        self.assertTrue(s["grip"]["hit"] and p["bodyFocused"] and p["anchorIn"] == "content" and p["pressOnGrip"] and p["pressT"] > p["selT"],
                        "focus on the page body, a caret on a line of the transcript, then a press on the strip's resize grip: %r" % s)
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["pressOnGrip"] and b["draft"] != p["draft"] and b["selT"] > b["pressT"] and b["dist"] > b["ch"],
                        "…then text written into the message box, a selection change after the press, more than a screen from the bottom: %r" % s)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_after_a_press_on_a_tool_output_boxs_scrollbar_and_then_text_written_into_the_message_box_is_taken_by_the_page(self):
        s = self._run()["sbarDraft"]
        p, b = s["afterPress"], s["before"]
        self.assertTrue(p["bodyFocused"] and p["anchorIn"] == "content" and not p["anchorInBox"] and p["pressIsBox"] and p["pressT"] > p["selT"],
                        "focus on the page body, a caret in the transcript outside the box, then a press on the box's scrollbar: %r" % s)
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["pressIsBox"] and b["draft"] != p["draft"] and b["selT"] > b["pressT"]
                        and b["box"]["st"] < b["box"]["max"] - 2 and b["dist"] > b["ch"],
                        "…then text written into the message box, a selection change after the press, the box able to scroll down: %r" % s)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_no_page_error(self):
        self.assertEqual(self._run()["errors"], [])


if __name__ == "__main__":
    unittest.main()
