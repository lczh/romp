#!/usr/bin/env python3
"""Every window.open in romp's own pages leaves the tab it opens without an opener (2026-09-27).

Handler._send declares Cross-Origin-Opener-Policy: same-origin-allow-popups. A page on another origin that opens a
dashboard page gets a closed handle, while a popup that a page on this origin opens keeps its opener, which a data
pane's popup needs (the _send comment says why; tests/test_pane_popup_opener_served.py drives both in a browser). So a
tab that romp's own code opens onto another origin, or onto a page whose links may lead there, would hold the dashboard
as its opener, and could post to it and navigate it. Each call therefore passes noopener (or noreferrer, which implies
it) in its features, or severs the opener on the handle it gets back, as ui/webview/preview.ts openFileTab does: its
caller needs the handle to tell a blocked popup from an open one, and the noopener feature returns null either way.

CallsLeaveNoOpener reads every window.open( call in the page sources: ui/ and vscode-extension/src, less tests and
built bundles, and kernel/kernel.py, whose inline scripts the kernel serves. It holds each call to one of those two
shapes and holds the census to the five calls the tree has, so a new call is read here before it lands. A spelling of
the call the census cannot read (self.open, top.open, globalThis.open, parent.open, or open read off window by a
computed key) fails it. Not read: a bare open( call, which in a page script resolves to window.open (ui modules define
functions of their own by that name, so the text cannot tell them apart), and a call through an alias of window.

TimelineFallbackExecuted runs the kernel's served timeline boot in node and calls its external-link hook: an https link
opens through window.open with noopener, and a vscode: deep link opens nothing.
Synthetic only: example origins, placeholder names.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load (it resolves its state root at import time; only pytest runs conftest's floor, and a
# bare unittest or script run would otherwise write REAL state). No session connects here; session-hosts is written off
# anyway, as for every root a test mints.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _f:
    _f.write("off")
km = load_source("romp_kernel_window_open_opener", os.path.join(BIN, "romp-kernel"))

# The page sources: every file a browser runs that romp wrote, as a tree under the repository root, less tests (the
# suffixes below) and built output (dist/, out-tests/, node_modules/).
_TREES = ("ui", os.path.join("vscode-extension", "src"))
_KERNEL = os.path.join("kernel", "kernel.py")
_TEST_SUFFIXES = (".test.ts", ".test.js", ".test.mjs")
_SKIP_DIRS = {"dist", "out-tests", "node_modules"}

# The calls the tree holds, by file: equality, so a call added or removed anywhere is read by this test.
EXPECTED = {
    "kernel/kernel.py": 1,          # the timeline boot's external-link fallback
    "ui/webview/file-view.ts": 1,   # a URL anchor in the file viewer
    "ui/webview/pr-links.ts": 1,    # a pull request link
    "ui/webview/preview.ts": 1,     # openFileTab: a /file tab, the opener severed on the handle
    "ui/webview/render.ts": 1,      # a link in the chat
}

_CALL = "window.open("
# Spellings of the same call the census does not read: each one fails it, so a call cannot leave the census by its spelling.
_UNREAD = re.compile(r"\b(?:self|top|globalThis|parent)\s*\.\s*open\s*\(|\bwindow\s*\[\s*[\"'`]open[\"'`]\s*\]")


def _sources():
    """(repo-relative path, text) for every page source."""
    out = []
    for tree in _TREES:
        for d, dirs, files in os.walk(os.path.join(ROOT, tree)):
            dirs[:] = sorted(x for x in dirs if x not in _SKIP_DIRS)
            for f in sorted(files):
                if not f.endswith((".ts", ".js", ".mjs")) or f.endswith(_TEST_SUFFIXES):
                    continue
                p = os.path.join(d, f)
                with open(p, encoding="utf-8") as fh:
                    out.append((os.path.relpath(p, ROOT).replace(os.sep, "/"), fh.read()))
    with open(os.path.join(ROOT, _KERNEL), encoding="utf-8") as fh:
        out.append((_KERNEL.replace(os.sep, "/"), fh.read()))
    return out


def _args(text, i):
    """The argument text of the call whose "(" is at text[i]: up to the matching ")", strings skipped."""
    depth, j, quote = 0, i, None
    while j < len(text):
        c = text[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
        j += 1
    raise AssertionError("unbalanced call at offset %d" % i)


def _features(args):
    """The window features a call passes: the tokens of its third argument's string literal, lower-cased, or () when the
    call passes none. Split on the top-level commas of the argument text (the calls here pass no nested commas)."""
    parts, depth, quote, cur = [], 0, None, ""
    for c in args:
        if quote:
            cur += c
            if c == quote:
                quote = None
            continue
        if c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += c
    parts.append(cur.strip())
    if len(parts) < 3:
        return ()
    m = re.fullmatch(r"([\"'])(.*)\1", parts[2])
    if not m:
        raise AssertionError("a features argument that is not a string literal: %r" % parts[2])
    return tuple(t.strip().lower() for t in m.group(2).split(",") if t.strip())


def _severs_on_handle(text, i):
    """True when the call at text[i] (its "window" word) is `const|let|var NAME = window.open(...)` and a statement
    `NAME.opener = null` follows within the next three lines."""
    line_start = text.rfind("\n", 0, i) + 1
    m = re.search(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*$", text[line_start:i])
    if not m:
        return False
    after = "\n".join(text[i:].split("\n")[:4])
    return re.search(r"\b" + re.escape(m.group(1)) + r"\.opener\s*=\s*null\b", after) is not None


def census():
    """{path: [(line, verdict)]} over every window.open( call; verdict is "noopener", "severed" or "keeps its opener"."""
    out = {}
    for path, text in _sources():
        for m in re.finditer(re.escape(_CALL), text):
            i = m.start()
            if i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_$."):
                continue   # another name ending in window (a property, an identifier): not the global
            feats = _features(_args(text, m.end() - 1))
            if "noopener" in feats or "noreferrer" in feats:
                verdict = "noopener"
            elif _severs_on_handle(text, i):
                verdict = "severed"
            else:
                verdict = "keeps its opener"
            out.setdefault(path, []).append((text.count("\n", 0, i) + 1, verdict))
    return out


class CallsLeaveNoOpener(unittest.TestCase):
    """Each window.open in romp's page sources passes noopener or severs the opener on its handle, and the census reads
    the calls the tree holds and no spelling it cannot read."""

    def test_every_call_passes_noopener_or_severs_the_opener_on_its_handle(self):
        found = census()
        kept = ["%s:%d" % (p, ln) for p, rows in sorted(found.items()) for ln, v in rows if v == "keeps its opener"]
        self.assertEqual(kept, [], "a tab romp opens keeps the dashboard as its opener; pass noopener, or sever it on the handle")

    def test_the_census_reads_the_calls_the_tree_holds(self):
        found = census()
        self.assertEqual({p: len(rows) for p, rows in found.items()}, EXPECTED,
                         "the window.open calls moved; read each new one and list it in EXPECTED")
        self.assertEqual([v for ln, v in found["ui/webview/preview.ts"]], ["severed"], "openFileTab severs on the handle")
        self.assertEqual([v for ln, v in found["kernel/kernel.py"]], ["noopener"], "the timeline boot's fallback passes noopener")

    def test_no_page_source_spells_the_call_another_way(self):
        hits = ["%s:%d: %s" % (p, text.count("\n", 0, m.start()) + 1, m.group(0))
                for p, text in _sources() for m in _UNREAD.finditer(text)]
        self.assertEqual(hits, [], "a call the census does not read: spell it window.open( so it is read")

    def test_the_census_tells_the_shapes_apart(self):
        # the three verdicts over synthetic calls, so a census that called every call noopener could not pass above
        cases = (
            ('window.open(href, "_blank", "noopener,noreferrer");', "noopener"),
            ('window.open(href, "_blank", "noreferrer");', "noopener"),
            ('window.open(href, "_blank");', "keeps its opener"),
            ('window.open(href, "_blank", "popup");', "keeps its opener"),
            ('const w = window.open(u, "_blank");\n  if (!w) return false;\n  try { w.opener = null; } catch {}', "severed"),
            ('const w = window.open(u, "_blank");\n  if (!w) return false;\n  try { v.opener = null; } catch {}', "keeps its opener"),
            ('var w=window.open(f(a,b),"_blank");w.opener=null;', "severed"),
        )
        for src, want in cases:
            with self.subTest(src=src):
                i = src.index(_CALL)
                feats = _features(_args(src, i + len(_CALL) - 1))
                got = "noopener" if ("noopener" in feats or "noreferrer" in feats) else (
                    "severed" if _severs_on_handle(src, i) else "keeps its opener")
                self.assertEqual(got, want)
        for spelled in ("self.open(u)", "top.open(u)", "globalThis.open(u)", "parent.open(u)", 'window["open"](u)', "window['open'](u)"):
            self.assertRegex(spelled, _UNREAD, spelled + " is refused")


# The timeline boot, run in node: the few browser names it touches at load, a top-level page on a loopback origin, and a
# window.open that records its arguments. Its external-link hook is then called with an https link and a vscode: one.
_HOOK_HARNESS = r"""
'use strict';
const OPENS = [], POSTS = [];
global.HTMLElement = function () {};
global.window = global;
global.parent = global;
global.top = global;
global.location = { origin: 'http://127.0.0.1:7777' };
global.acquireVsCodeApi = () => ({ postMessage(m) { POSTS.push(m); } });
global.addEventListener = () => {};
global.open = function () { OPENS.push(Array.prototype.slice.call(arguments)); return null; };
BOOT
window.__rompTimelineOpenExternal('https://example.invalid/notes-api/pull/1');
window.__rompTimelineOpenExternal('vscode://romp.romp/open?session=web&anchor=11111111-2222-3333-4444-555555555555');
process.stdout.write(JSON.stringify({ opens: OPENS, posts: POSTS }));
"""


class TimelineFallbackExecuted(unittest.TestCase):
    """The browser timeline opens an external link in a tab with no opener, and a VS Code deep link in no tab."""

    def test_an_external_link_opens_with_noopener_and_a_deep_link_opens_no_tab(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node not installed")
        fx = tempfile.mkdtemp()
        try:
            path = os.path.join(fx, "hook.js")
            with open(path, "w") as f:
                f.write(_HOOK_HARNESS.replace("BOOT", km._TIMELINE_BOOT))
            r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(fx, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout)
        self.assertEqual(len(got["opens"]), 1, "one tab, for the https link: %r" % got["opens"])
        url, target, feats = (got["opens"][0] + [None, None, None])[:3]
        self.assertEqual((url, target), ("https://example.invalid/notes-api/pull/1", "_blank"))
        tokens = [t.strip().lower() for t in (feats or "").split(",")]
        self.assertTrue("noopener" in tokens or "noreferrer" in tokens, "the tab gets no opener: features %r" % feats)
        self.assertEqual([p.get("type") for p in got["posts"]], ["deepLink"], "the vscode: link goes to the host, not a tab")


if __name__ == "__main__":
    unittest.main()
