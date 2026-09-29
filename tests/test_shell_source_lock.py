#!/usr/bin/env python3
"""The shell's source check is locked once defined, and every window message listener the kernel serves is of a listed
kind (2026-09-27).

The shell page (kernel.py _landing) runs fourteen inline window message listeners for its panes' words, and each reads
the shell's one source check, window.__rompPaneSourceOk, FAIL-CLOSED as its first statement: a message counts only
when its immediate source is a same-origin iframe of the shell not marked data-protocol=none (plans/panes-as-data.md,
section 5; tests/test_pane_registry.py takes the census of the shell's inline and bundled listeners and runs the
check's truth table). The check is a property of the window, and before this change nothing stopped a later script
from replacing it: an assignment, a defineProperty, a delete, under any spelling, would leave every listener reading
the replacement. The line right after the definition locks it, read-only and non-configurable
(try{Object.defineProperty(window,'__rompPaneSourceOk',{writable:false,configurable:false});}catch(x){}), so a later
write does not land. The definition's three lines are not edited: TheCheckAndItsLock pins them by the sha256 of their
text as it was before the lock was added.

CheckLocked runs the served shell's inline scripts in a stand-in browser (node's vm, _HARNESS below), reads the
property's descriptor after boot, and tries each way a script can replace the check; the same attempts on the shell
without its lock line each replace it, so a replacement that lands is one the harness sees. ShellListenersExecuted runs
the same scripts through every callback they leave for later and every handler they register, then forges a message
from each sender the shell must refuse and from a pane, and reads which function the check is when each is delivered.
KernelListenerCensus reads all of kernel.py and every page it serves: each window message listener opens with one of
the listed heads (the shell's gate, the timeline boot's heardSender, tests/test_timeline_boot_senders.py), and no
addEventListener escapes the census by an alias or a computed event type. ServedPagesExecuted runs every other page's
scripts in the same stand-in and counts the listeners each registers.

What no leg here runs: code behind a condition the stand-in does not meet (a secure-context test, a user agent, a stored
setting, a key the stand-in event does not carry), an arm keyed other than by comparing a field with a string, and a
registration through another object's method. A write of the check on such a road is refused by the lock when it runs
after the boot script. Synthetic only: no session data, a loopback origin.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads (they resolve their state root at import time; only pytest runs conftest's floor,
# and a bare unittest or script run would otherwise write REAL state). No session connects here; session-hosts is
# written off anyway, as for every root a test mints.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _f:
    _f.write("off")
km = load_source("romp_kernel_shell_source_lock", os.path.join(BIN, "romp-kernel"))

# sha256 of the definition's text (its three lines, joined by newlines, no trailing newline) as the tree held it before
# the lock was added: from its first character, "window.__rompPaneSourceOk=function(e){", through its last,
# "return false;}catch(x){return false;}};". 319 characters. The lock goes after them; they are not edited.
DEFINITION_SHA256 = "97e0342292b56cf7b1df96a91d40c7bf8db3ee3a0949abc681c0e3457908f7bb"
DEFINITION_LEN = 319
REGION_HEAD = "window.__rompPaneSourceOk=function(e){"
REGION_TAIL = "return false;}catch(x){return false;}};"

# The read every shell listener opens with (tests/test_pane_registry.py's GUARD).
GATE = "if(!window.__rompPaneSourceOk||!window.__rompPaneSourceOk(e))return;"
# The line right after the definition: it locks the check, read-only and non-configurable, so no later script
# replaces, redefines or deletes it. CheckLocked fails without it.
LOCK = "try{Object.defineProperty(window,'__rompPaneSourceOk',{writable:false,configurable:false});}catch(x){}"

# Every window message listener the shell runs, by a phrase only its own body carries. A listener added to the
# shell must join this list (the census below fails until it does), which is where its senders get decided.
LISTENERS = {
    "boot splash ready (_LANDING_BOOT_JS)": "e.data.romp==='ready')hide()",
    "log count ask (_LANDING_ERRS_JS)": "m.romp==='logUnseenQuery')tell()",
    "notify (_LANDING_ERRS_JS)": "m.romp==='notify'&&m.text",
    "connection state (_LANDING_ERRS_JS)": "m.romp!=='wsState'",
    "usage bars (_LANDING_USAGE_JS)": "m.romp==='usage')render(m.usage)",
    "activeTab relay (_LANDING_FOCUS_JS)": "m.romp!=='activeTab'",
    "toggleFleet (_LANDING_FLEET_JS)": "m.romp!=='toggleFleet'",
    "settings and file relay (_LANDING_SETTINGS_JS)": "if(m.romp==='settings'){document.body.classList.toggle('settings-open'",
    "pending hosts (_LANDING_REMOTES_JS)": "m.romp!=='hostsPending'",
    "phone tab switch (_LANDING_MOBILE_JS)": "if(m.romp==='reveal'&&m.pane)reveal(m.pane)",
    "push reveal (_LANDING_REVEAL_JS)": "m.romp==='ready'&&m.app==='feed'",
    "open tabs of the chat columns (_LANDING_COLLAPSE_JS)": "m.romp!=='chatTabs'",
    "split columns (_LANDING_SPLIT_JS)": "m.romp==='tabDrag'",
    "reload prompt (_STALE_JS)": "m.romp==='wsStale'",
}

LISTEN_OPEN = "addEventListener('message',function(e){"

# An onmessage handler assigned to the window: window., self. or globalThis. (or a computed ['onmessage'] member of
# one of them), or a bare onmessage with no receiver. A plain `=` only: == and === compare.
_ONMESSAGE = re.compile(r"(?:(?<![\w$.])(?:window|self|globalThis)\s*(?:\.\s*onmessage|\[\s*(['\"`])onmessage\1\s*\])"
                        r"|(?<![\w$.])onmessage)\s*=(?!=)")


def _window_onmessage(text):
    """Every onmessage handler assigned to the window in `text` (the matches, for the failure message)."""
    return [m.group(0) for m in _ONMESSAGE.finditer(text)]


def _inline_scripts(html):
    """The bodies of the page's inline <script> elements, in page order, whatever attributes the tag carries (a
    <script src=...> is a bundle, not here)."""
    return re.findall(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", html, re.S | re.I)


def _served_pages():
    """Every page the kernel serves as a document, by route, and /sw.js (the service worker's script): what a script on
    romp's origin that the kernel writes can be."""
    return {"/": km._landing(), "/chat": km._chat_page(), "/feed": km._feed_page(), "/fleet": km._fleet_page(),
            "/files": km._files_page(), "/artifacts": km._artifacts_page(), "/settings": km._settings_page(),
            "/timeline": km._timeline_page(), "the sign-in page": km._TOKEN_LOGIN_HTML,
            "the too-large page": km._too_large_page("too large", "a.pdf", {}), "/sw.js": km._sw_js()}


def _kernel_code():
    """kernel.py's text less the Python comment lines that describe the served JavaScript (every served line is code)."""
    with open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"), encoding="utf-8") as f:
        src = f.read()
    return "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))


def _region(text):
    i = text.index(REGION_HEAD)
    return text[i:text.index(REGION_TAIL, i) + len(REGION_TAIL)]


class TheCheckAndItsLock(unittest.TestCase):
    """The definition is the tree's text from before the lock, byte for byte, defined once ahead of every shell
    listener, and the lock is the line right after it."""

    def test_the_definition_is_unedited(self):
        region = _region(km._LANDING_BOOT_JS)
        self.assertEqual(len(region), DEFINITION_LEN)
        self.assertEqual(hashlib.sha256(region.encode("utf-8")).hexdigest(), DEFINITION_SHA256,
                         "the definition's three lines were edited: the lock goes after them, and nothing inside them changes")
        self.assertEqual(region.count("\n"), 2, "three lines")

    def test_the_lock_is_the_line_right_after_the_definition(self):
        boot = km._LANDING_BOOT_JS
        self.assertIn(_region(boot) + "\n" + LOCK + "\n", boot, "the lock is the next line after the definition")
        self.assertEqual(boot.count(LOCK), 1)

    def test_it_is_defined_and_locked_once_on_the_served_shell_ahead_of_every_listener(self):
        html = km._landing()
        self.assertEqual(html.count(REGION_HEAD), 1)
        self.assertEqual(html.count("window.__rompPaneSourceOk="), 1, "one definition, no second copy to drift")
        self.assertEqual(html.count(LOCK), 1, "the lock, once")
        # every mention of the name on the page is the definition, the lock, or one listener's fail-closed read (two
        # mentions each), so no assignment in another spelling, and no other reader, sits anywhere in the served shell
        self.assertEqual(html.count("__rompPaneSourceOk"), 2 + 2 * len(LISTENERS),
                         "the name appears only in the definition, the lock and each named listener's gate")
        self.assertEqual(html.count(GATE), len(LISTENERS), "each named listener's gate, once")
        scripts = _inline_scripts(html)
        where = [n for n, s in enumerate(scripts) if REGION_HEAD in s]
        self.assertEqual(len(where), 1)
        first_listener = min(n for n, s in enumerate(scripts) if LISTEN_OPEN in s)
        self.assertLessEqual(where[0], first_listener, "defined before (or in) the first script that listens")
        self.assertLess(scripts[where[0]].index(LOCK), scripts[where[0]].index(LISTEN_OPEN), "locked before any listener registers")

    def test_the_kernel_names_it_only_in_the_definition_the_lock_and_the_gates(self):
        # the Python comment lines aside, every mention is in the served JavaScript (the definition's own JavaScript
        # comment line above it names the check's file, not the check)
        code = _kernel_code()
        self.assertEqual(code.count(REGION_HEAD), 1, "the definition, once")
        self.assertEqual(code.count(LOCK), 1, "the lock, once")
        self.assertEqual(code.count(GATE), len(LISTENERS), "each gate, once")
        self.assertEqual(code.count("__rompPaneSourceOk"), 2 + 2 * len(LISTENERS),
                         "the name appears in kernel.py's code only in the definition, the lock and the gates")

    def test_no_page_but_the_shell_carries_it(self):
        pages = _served_pages()
        del pages["/"]
        for name, page in pages.items():
            with self.subTest(page=name):
                self.assertGreater(len(page), 100, "the page was built")
                self.assertNotIn("__rompPaneSourceOk", page)


class ShellListenerCensus(unittest.TestCase):
    """Every window message listener in the served shell reads the check fail-closed as its first statement; the
    service worker's channel, which no window can post on, is the one listener without it."""

    def setUp(self):
        self.html = km._landing()

    def test_every_window_listener_in_the_shell_is_named_and_gated(self):
        opens = [m.start() for m in re.finditer(re.escape("window." + LISTEN_OPEN), self.html)]
        self.assertEqual(len(opens), len(LISTENERS),
                         "a window message listener in the shell that LISTENERS does not name (or a named one gone): "
                         "name it there and decide its senders")
        for at in opens:
            head = self.html[at + len("window." + LISTEN_OPEN):][:len(GATE)]
            with self.subTest(at=self.html[at:at + 160]):
                self.assertEqual(head, GATE, "the check, fail-closed, is the listener's first statement")
        every = _MESSAGE_LISTEN.findall(self.html)
        self.assertEqual(len(every), len(LISTENERS) + 1, "the named window listeners and the service worker's")
        self.assertEqual(self.html.count("swc.addEventListener('message',function(ev){"), 1,
                         "the service worker's own channel: exempt, a window cannot post on it")
        self.assertEqual(_window_onmessage(self.html), [], "an onmessage handler on the shell's window")

    def test_the_onmessage_census_reads_each_spelling(self):
        for src in ("window.onmessage=function(e){}", "self.onmessage = f", "globalThis['onmessage']=f", "window [\"onmessage\"] =f",
                    "window[`onmessage`]=f", ";onmessage=function(e){}", "\n  onmessage = f"):
            with self.subTest(src=src):
                self.assertEqual(len(_window_onmessage(src)), 1)
        for src in ("ws.onmessage=function(ev){}", "d.onopen=d.onmessage=d.onclose=null", "ch.port1.onmessage=flush",
                    "if(window.onmessage===f)go()", "var x_onmessage=1"):
            with self.subTest(src=src):
                self.assertEqual(_window_onmessage(src), [])

    def test_each_named_listener_opens_with_the_check(self):
        for name, phrase in LISTENERS.items():
            with self.subTest(listener=name):
                self.assertEqual(self.html.count(phrase), 1, "the phrase names one place in the shell")
                at = self.html.rindex(LISTEN_OPEN, 0, self.html.index(phrase))
                self.assertEqual(self.html[at + len(LISTEN_OPEN):][:len(GATE)], GATE,
                                 "%s acts on a message only after the source check admits it" % name)


# ── every window message listener the kernel serves ──
# The census above reads the served shell. This one reads all of kernel.py and every page it serves, so a listener added
# to any other page (a pane page's inline script, the sign-in page), in any quoting, fails here too. Each window message
# listener in kernel.py's code opens with one of the heads below, from its receiver through its first statement, and
# each head occurs the number of times listed. A new listener, or a listed one gone, fails until this table names it,
# which is where its senders get decided.
KERNEL_LISTENER_KINDS = {
    "shell (the source check)": ("window.", "addEventListener('message',function(e){" + GATE, len(LISTENERS)),
    "timeline boot (heardSender; tests/test_timeline_boot_senders.py)":
        ("window.", 'addEventListener("message",function(e){if(!heardSender(e)){noteForeign("timeline-boot",e);return;}', 1),
    "the service worker's channel (exempt: no window posts on it)": ("swc.", "addEventListener('message',function(ev){", 1),
}
# What each served page carries, by kind: the shell's listeners and the service worker channel on the shell, and the
# timeline boot's on /timeline. The pane shim (every pane page) registers no window message listener.
PAGE_LISTENERS = {
    "/": {"shell (the source check)": len(LISTENERS),
          "the service worker's channel (exempt: no window posts on it)": 1},
    "/timeline": {"timeline boot (heardSender; tests/test_timeline_boot_senders.py)": 1},
    "the sign-in page": {}, "the too-large page": {}, "/sw.js": {},
}
for _page in ("/chat", "/feed", "/fleet", "/files", "/artifacts", "/settings"):
    PAGE_LISTENERS[_page] = {}

# A message listener in any spelling text can show: the method named or reached by a computed member, the event type in
# any quotes. (A registration that reaches the method with no such text, window['add'+'EventListener'], is the executed
# legs' to catch: the shell's and every served page's below.)
_MESSAGE_LISTEN = re.compile(r"(?:(?<![\w$])addEventListener|\[\s*(['\"`])addEventListener\1\s*\])\s*\(\s*(['\"`])message\2")
# Every addEventListener in the kernel's code, named: each must be a call with a literal event type, so that the census
# above reads the type (a bind, a call or apply, a comma-operator call, a variable holding the method, or a computed type
# are the ways around it). Two other shapes stand: a feature test followed at once by the same receiver's literal call
# (`x.addEventListener)x.addEventListener('load',...)`), and the calls with a computed type below, each over a literal
# list that holds no "message".
_ADD_TOKEN = re.compile(r"(?<![\w$])addEventListener(?![\w$])")
_LITERAL_CALL = re.compile(r"\s*\(\s*(['\"`])[\w:.-]*\1")
COMPUTED_TYPE_CALLS = {
    "document.addEventListener(END[k],ended,true)":
        "var END=['pointerup','touchend','touchcancel','scrollend','dragend','drop','selectionchange','input','focusout'];",
}


def _listener_kind(text, at):
    """The KERNEL_LISTENER_KINDS name of the message listener whose match starts at `at` in `text`, or None."""
    for name, (receiver, head, _n) in KERNEL_LISTENER_KINDS.items():
        if text.startswith(head, at) and text[max(0, at - len(receiver)):at] == receiver:
            return name
    return None


def _message_listeners(text):
    """[(kind or None, the call's first 160 characters)] for every message listener `text` spells."""
    return [(_listener_kind(text, m.start()), text[max(0, m.start() - 8):m.start() + 160])
            for m in _MESSAGE_LISTEN.finditer(text)]


_RECEIVER = re.compile(r"(?<![\w$.])([\w$]+(?:\.[\w$]+)*)\.$")


_AFTER_TEST = re.compile(r"\)\s*\{?\s*")


def _feature_test(code, m):
    """True when the addEventListener token matched by `m` is a feature test followed at once by the same receiver's call
    with a literal event type, bare or as the first statement of the block the test opens:
    `x.addEventListener)x.addEventListener('load',...)`, `x.addEventListener){x.addEventListener('keydown',...)`."""
    after = _AFTER_TEST.match(code, m.end())
    if not after:
        return False
    r = _RECEIVER.search(code, max(0, m.start() - 200), m.start())
    if not r:
        return False
    call = r.group(1) + ".addEventListener"
    return code.startswith(call, after.end()) and bool(_LITERAL_CALL.match(code, after.end() + len(call)))


def _loose_add_tokens(code):
    """Every addEventListener in `code` that is not a call with a literal event type, a feature test ahead of one, or a
    listed computed-type call, with its surroundings."""
    loose = []
    for m in _ADD_TOKEN.finditer(code):
        if _LITERAL_CALL.match(code, m.end()) or _feature_test(code, m):
            continue
        if any(code.startswith(site, m.start() - site.index("addEventListener")) for site in COMPUTED_TYPE_CALLS):
            continue
        loose.append(code[max(0, m.start() - 40):m.end() + 40])
    return loose


class KernelListenerCensus(unittest.TestCase):
    """Every window message listener in kernel.py, and on every page it serves, is one of the listed kinds: the shell's
    (the source check), the timeline boot's (heardSender), and the service worker's channel. No onmessage handler is
    assigned to a window anywhere in the kernel's code or its pages, and no addEventListener in the kernel's code
    escapes the census by an alias or a computed event type."""

    def test_every_message_listener_in_the_kernels_code_is_a_listed_kind(self):
        found = _message_listeners(_kernel_code())
        unknown = [at for kind, at in found if kind is None]
        self.assertEqual(unknown, [], "a message listener in kernel.py that KERNEL_LISTENER_KINDS does not list: list its "
                                      "kind there, which is where its senders are decided")
        for name, (_receiver, _head, n) in KERNEL_LISTENER_KINDS.items():
            with self.subTest(kind=name):
                self.assertEqual(sum(1 for kind, _ in found if kind == name), n, "how many listeners of this kind the kernel has")
        self.assertEqual(len(found), sum(n for _r, _h, n in KERNEL_LISTENER_KINDS.values()))

    def test_no_onmessage_handler_on_a_window_in_the_kernels_code(self):
        self.assertEqual(_window_onmessage(_kernel_code()), [], "an onmessage handler assigned to a window in kernel.py")

    def test_every_add_event_listener_in_the_kernels_code_names_its_event_type(self):
        code = _kernel_code()
        self.assertEqual(_loose_add_tokens(code), [],
                         "an addEventListener in kernel.py that is not a call with a literal event type (an alias, a bind, "
                         "a call or apply, a computed type): the message census cannot read it")
        for site, defn in COMPUTED_TYPE_CALLS.items():
            with self.subTest(site=site):
                self.assertEqual(code.count(site), 1, "the computed-type call, once")
                self.assertEqual(code.count(defn), 1, "its literal list of event types, once")
                self.assertNotIn("message", defn)
                name = defn[len("var "):defn.index("=")]
                self.assertIsNone(re.search(r"(?<![\w$.])%s\s*(?:\.\s*(?:push|unshift|splice|concat)|\[[^\]]*\]\s*=(?!=))" % name, code),
                                  "the list is never added to")
        self.assertGreater(len(_ADD_TOKEN.findall(code)), 150, "the census read the kernel's listeners")

    def test_every_served_page_carries_only_its_listed_listeners(self):
        pages = _served_pages()
        self.assertEqual(set(pages), set(PAGE_LISTENERS), "every page the census reads has its expected kinds listed")
        for name, page in pages.items():
            with self.subTest(page=name):
                found = _message_listeners(page)
                self.assertEqual([at for kind, at in found if kind is None], [], "a message listener of no listed kind")
                got = {}
                for kind, _ in found:
                    got[kind] = got.get(kind, 0) + 1
                self.assertEqual(got, PAGE_LISTENERS[name])
                self.assertEqual(_window_onmessage(page), [], "an onmessage handler on the page's window")
                self.assertIsNone(re.search(r"<[a-z][^>]*\sonmessage\s*=", page, re.I), "an onmessage attribute on an element")

    def test_the_census_reads_each_spelling(self):
        listener = "function(e){var m=e&&e.data;}"
        for src in ("window.addEventListener('message'," + listener + ");",
                    'window.addEventListener("message",' + listener + ");",
                    "window.addEventListener(`message`," + listener + ");",
                    "addEventListener ( 'message' ," + listener + ");",
                    "window['addEventListener']('message'," + listener + ");",
                    "self [ \"addEventListener\" ] (\"message\"," + listener + ");",
                    'window.addEventListener("message",frameListener);'):
            with self.subTest(src=src):
                self.assertEqual([k for k, _ in _message_listeners(src)], [None], "one listener, of no listed kind")
        for name, (receiver, head, _n) in KERNEL_LISTENER_KINDS.items():
            with self.subTest(kind=name):
                self.assertEqual([k for k, _ in _message_listeners(receiver + head + "});")], [name])
                self.assertEqual([k for k, _ in _message_listeners("x." + head + "});")], [None], "on another receiver")
        for src in ("window.addEventListener('resize'," + listener + ");", "var s='messages';", "removeEventListener('message',f)"):
            with self.subTest(src=src):
                self.assertEqual(_message_listeners(src), [])

    def test_the_add_event_listener_census_refuses_every_way_around_the_literal(self):
        for src in ("var add=window.addEventListener;add('message',f);",
                    "window.addEventListener.bind(window)('message',f);",
                    "window.addEventListener.call(window,'message',f);",
                    "(0,window.addEventListener)('message',f);",
                    "window.addEventListener(TYPES[i],f);",
                    "if(x.addEventListener)y.addEventListener('load',f);",
                    "if(x.addEventListener)x.addEventListener(t,f);",
                    "if(x.addEventListener){y.addEventListener('load',f);}",
                    "if(x.addEventListener){x.addEventListener(t,f);}",
                    "if(x.addEventListener){go();x.addEventListener('load',f);}"):
            with self.subTest(src=src):
                self.assertGreaterEqual(len(_loose_add_tokens(src)), 1, "refused")
        for src in ("window.addEventListener('load',f);", "el.addEventListener( \"click\" ,f);",
                    "if(swc&&swc.addEventListener)swc.addEventListener('message',f);",
                    "if(MQ.addEventListener)MQ.addEventListener('change',f);",
                    "if(d&&d.addEventListener){d.addEventListener('keydown',esc,true);}",
                    "for(var k=0;k<END.length;k++)document.addEventListener(END[k],ended,true);"):
            with self.subTest(src=src):
                self.assertEqual(_loose_add_tokens(src), [], "accepted")


# A stand-in browser for the shell's inline scripts: node's vm runs them in a context whose global answers every name
# it does not hold with an inert stub (callable, constructible, every property another stub, 0 as a number), so the
# scripts boot far enough to register their listeners without a DOM. What the checks read is real: location (the
# shell's origin), document.querySelectorAll('iframe') (the shell's frames), window.parent/top (the shell is the top
# window). Before any message is tested it runs what the scripts left for later and the exercise (ShellListenersExecuted
# names the roads). The harness then hands each registered window message listener a message from each sender and counts
# how often the listener reads the message's data: a listener that returns before reading it acts on nothing.
_HARNESS = r"""
'use strict';
const vm = require('vm');
const fs = require('fs');
const SCRIPTS = JSON.parse(fs.readFileSync(process.env.ROMP_TEST_SCRIPTS, 'utf8'));
const ORIGIN = 'http://127.0.0.1:7777', ELSEWHERE = 'https://elsewhere.example';
// every other listener and handler the scripts register (on the window, the document, a frame or any element the
// stand-in hands them: resize, visibilitychange, storage, focus, keydown, click, a frame's load, ...) and every function
// they hand to a stand-in (an observer's callback, a fetch's then, a stand-in element's forEach): the exercise below runs
// each once. A window message listener is not here; it is heard by the window's own addEventListener
const OTHER = [];
// each once per event type and text: a render that runs again hands over fresh closures of the same code, which ran already
const OTHER_SEEN = new Set();
function other(type, f) {
  if (typeof f !== 'function') return;
  type = String(type);
  const key = type + '\u0000' + Function.prototype.toString.call(f);
  if (OTHER_SEEN.has(key)) return;
  OTHER_SEEN.add(key);
  OTHER.push({ type, f });
}
function onWrite(k, v) { if (typeof k === 'string' && /^on[a-z]/.test(k)) other(k.slice(2), v); }
function handedOn(args) { for (const a of args) other('callback', a); }
const ADD_ON_STUB = function (type, f) { other(type, f); };
const stubHandler = {
  get(t, k) {
    if (k === Symbol.toPrimitive) return () => 0;
    if (k === Symbol.iterator) return function* () {};
    if (typeof k === 'symbol') return undefined;
    if (k === 'length') return 0;
    if (k === 'addEventListener') return ADD_ON_STUB;
    return STUB;
  },
  set(t, k, v) { onWrite(k, v); return true; }, has() { return false; }, deleteProperty() { return true; },
  apply(t, self, args) { handedOn(args); return STUB; }, construct(t, args) { handedOn(args); return STUB; },
};
const STUB = new Proxy(function () {}, stubHandler);
function stubbed(o) {
  return new Proxy(o, { get(t, k) { return (k in t) ? t[k] : stubHandler.get(t, k); },
                        set(t, k, v) { onWrite(k, v); t[k] = v; return true; } });
}
// windows: a pane of the shell (the chat), the Files pane, a sandboxed frame of the shell, a frame of the shell on
// another origin, a same-origin window the shell does not hold (a popup, a frame nested in a pane), a page on another
// origin that opened the shell
const POSTED = [];
function win(name) { return stubbed({ name, postMessage(m) { POSTED.push([name, m]); }, focus() {} }); }
const CHAT = win('chat'), FILES = win('files'), SANDBOXED = win('sandboxed'), XFRAME = win('xframe'),
      STRAY = win('stray'), OPENER = win('opener');
function frame(id, w) { return stubbed({ id, contentWindow: w, getAttribute(n) { return n === 'id' ? id : null; },
                                         addEventListener(type, f) { other(type, f); }, removeEventListener() {} }); }
const FRAMES = [frame('f-chat', CHAT), frame('f-files', FILES), frame('f-url', SANDBOXED), frame('f-x', XFRAME)];
const BYID = {}; FRAMES.forEach((f) => { BYID[f.id] = f; });
// what the page leaves for later: timer, animation-frame, idle and microtask callbacks, and the listeners for the page's
// load events (window's and document's) and an on<load event> handler. All run after the scripts and before any
// message is delivered, so a write the page defers is in place when the listeners are tested
const LATE = [];
const LATE_EVENTS = new Set(['load', 'DOMContentLoaded', 'pageshow', 'readystatechange']);
function later(f) { if (typeof f === 'function') LATE.push(f); return LATE.length; }
const document = stubbed({
  querySelectorAll(sel) { return sel === 'iframe' ? FRAMES.slice() : []; },
  getElementById(id) { return BYID[id] || STUB; },
  addEventListener(type, f) { if (LATE_EVENTS.has(type)) later(f); else other(type, f); },
  removeEventListener() {},
});
const LISTENERS = [];
const target = {};
// every value the scripts assign to the check, whatever the assignment's spelling (window.x=, window['x']=, a bare
// global, a defineProperty): each lands on the context's global, so each is heard here. vm reports one assignment as
// more than one trap (creating the property is a set, a define and a set again), so a value is kept once, by identity,
// in the order first heard; an accessor's descriptor is kept as itself
const CHECK = '__rompPaneSourceOk', ASSIGNED = [];
function heard(v) { if (!ASSIGNED.includes(v)) ASSIGNED.push(v); }
// every value the scripts assign to the window's onmessage, however spelled: an onmessage handler is a window message
// listener addEventListener never sees
const ONMESSAGE = [];
function onGlobalWrite(k, v) {
  if (k === CHECK) heard(v);
  if (k === 'onmessage') ONMESSAGE.push(textOf(v));
  if ((k === 'onload' || k === 'onpageshow') && typeof v === 'function') later(v);
  else if (k !== 'onmessage') onWrite(k, v);
}
function textOf(v) { return typeof v === 'function' ? Function.prototype.toString.call(v) : typeof v; }
const BUILTINS = new Set(['Object', 'Array', 'JSON', 'Math', 'Date', 'String', 'Number', 'Boolean', 'RegExp', 'Error',
  'TypeError', 'RangeError', 'SyntaxError', 'ReferenceError', 'Map', 'Set', 'WeakMap', 'WeakSet', 'Symbol', 'Promise',
  'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'encodeURIComponent', 'decodeURIComponent', 'encodeURI', 'decodeURI',
  'Infinity', 'NaN', 'undefined', 'Intl', 'URL', 'URLSearchParams', 'Reflect', 'Proxy', 'BigInt', 'Function']);
// The global keeps a browser's rules for a locked property (the shell's check after its lock): a write to a read-only
// property keeps the value (a strict-mode script's throws), and a define or delete the property refuses is refused.
// (vm's global answers most of these before a trap runs, from the property's descriptor, which is read through
// getOwnPropertyDescriptor below.) A define counts as a write only when it carries a value or an accessor; one that
// changes attributes alone, as the lock's {writable:false,configurable:false} does, writes nothing and the property keeps
// its value. vm hands this trap a define that carries no value as one whose value is undefined (node's contextify
// definer fills the missing value in), so on a data property an undefined value is taken as no value: the property
// keeps what it holds, as in a browser. (A define that really sets the check to undefined is taken the same way, and
// missed: an undefined check refuses every message, which is no widening for the tests here to catch.)
// One cost of that, in this harness only: the lock's own define throws here. The trap applies the lock with the value the
// property holds, and the proxy then checks the define it was handed (value undefined) against the property, now
// read-only with another value, and throws a TypeError (its invariant for a non-configurable property). The property is
// locked with its value by then, and the lock line's own try absorbs the throw, so the boot script runs on; a browser
// throws nothing there. A lock written without its try would abort the boot script here and not in a browser, so read
// such an abort as the harness, not the page (CheckLocked's trap test runs the define bare and pins both halves).
// The set trap's read-only clause below is a belt: vm refuses a write to a read-only property from its descriptor before
// the trap runs, so the clause is not reached today.
const G = new Proxy(target, {
  has() { return true; },
  get(t, k) { if (k in t) return t[k]; if (typeof k === 'symbol') return undefined; if (BUILTINS.has(k)) return globalThis[k]; return STUB; },
  set(t, k, v) {
    const d = Object.getOwnPropertyDescriptor(t, k);
    if (d && ('value' in d ? !d.writable : !d.set)) return false;
    onGlobalWrite(k, v); t[k] = v; return true;
  },
  defineProperty(t, k, d) {
    const held = Object.getOwnPropertyDescriptor(t, k);
    if ('value' in d && d.value === undefined && held && 'value' in held) d = Object.assign({}, d, { value: held.value });
    else if ('value' in d) onGlobalWrite(k, d.value);
    else if ('get' in d || 'set' in d) onGlobalWrite(k, d);
    return Reflect.defineProperty(t, k, d);
  },
  getOwnPropertyDescriptor(t, k) { return Object.getOwnPropertyDescriptor(t, k); },
  deleteProperty(t, k) { return Reflect.deleteProperty(t, k); },
});
Object.assign(target, {
  window: G, self: G, top: G, parent: G, globalThis: G, document,
  location: stubbed({ origin: ORIGIN, protocol: 'http:', host: '127.0.0.1:7777', hostname: '127.0.0.1', port: '7777',
                      pathname: '/', search: '', hash: '', href: ORIGIN + '/', reload() {}, replace() {}, assign() {} }),
  localStorage: stubbed({ getItem() { return null; }, setItem() {}, removeItem() {} }),
  sessionStorage: stubbed({ getItem() { return null; }, setItem() {}, removeItem() {} }),
  setTimeout: later, clearTimeout() {}, setInterval: later, clearInterval() {},
  requestAnimationFrame: later, cancelAnimationFrame() {}, requestIdleCallback: later, cancelIdleCallback() {},
  queueMicrotask: later,
  innerWidth: 1280, innerHeight: 800,
  addEventListener(type, f) {
    if (type === 'message') LISTENERS.push({ f, src: String(f), checkDefined: typeof target.__rompPaneSourceOk === 'function' });
    else if (LATE_EVENTS.has(type)) later(f);
    else other(type, f);
  },
  removeEventListener() {},
});
const ctx = vm.createContext(G);
const ERRORS = [];
SCRIPTS.forEach((body, n) => {
  try { vm.runInContext(body, ctx, { filename: 'landing-script-' + n + '.js', timeout: 5000 }); }
  catch (e) { ERRORS.push([n, String(e && e.message || e).slice(0, 200)]); }
});
(async () => {
// then what the scripts left for later, in rounds (a callback may leave more), each callback once, every one bounded:
// the scripts' promise reactions first (a macrotask turn drains the microtask queue the context shares with this one),
// then the queued callbacks, each handed a stand-in load event
const LATE_RUN = { queued: 0, ran: 0, errors: 0, rounds: 0 };
async function drain() {
  for (let round = 0; round < 6 && (round === 0 || LATE.length); round++) {
    await new Promise((r) => setImmediate(r));
    const batch = LATE.splice(0, 5000);
    if (!batch.length) break;
    LATE_RUN.rounds++;
    LATE_RUN.queued += batch.length;
    for (const f of batch) {
      target.__hLate = f;
      LATE_RUN.ran++;
      try { vm.runInContext("__hLate.call(window,{type:'load',persisted:false,timeStamp:0})", ctx, { timeout: 2000 }); }
      catch (x) { LATE_RUN.errors++; }
    }
  }
  await new Promise((r) => setImmediate(r));
  LATE_RUN.left = LATE.length;
}
await drain();
// the senders: [name, source, origin]
const SENDERS = {
  opener: [OPENER, ELSEWHERE],             // a page on another origin that opened the dashboard
  sandboxedFrame: [SANDBOXED, 'null'],    // a sandboxed iframe of the shell (opaque origin)
  otherOriginFrame: [XFRAME, ELSEWHERE],  // an iframe of the shell showing another origin
  strayWindow: [STRAY, ORIGIN],           // same origin, but not a frame of this document (a popup, a nested frame)
  shellItself: [vm.runInContext('window', ctx), ORIGIN],   // the shell's own window, as its scripts see it
  dispatch: [null, ''],                    // no source, no origin: an event this document dispatched
  sourcelessElsewhere: [null, ELSEWHERE], // no source, another origin
  sourcelessOpaque: [null, 'null'],       // no source, the opaque origin: a sandboxed frame gone after it posted
  pane: [CHAT, ORIGIN],                    // a pane of the shell: the one sender heard
};
const IN_EFFECT = new Set();   // the check's text at every delivery
function deliver(l, source, origin, data) {
  IN_EFFECT.add(textOf(target.__rompPaneSourceOk));
  let reads = 0;
  const e = { type: 'message', source, origin, get data() { reads++; return data; } };
  target.__hF = l.f; target.__hE = e;
  let threw = null;
  try { vm.runInContext('__hF(__hE)', ctx, { timeout: 2000 }); } catch (x) { threw = String(x && x.message || x).slice(0, 120); }
  return { reads, threw };
}
// the words a window message listener's arms compare against: every `.field === 'value'` (or !==, ==, !=) in its text,
// each handed over alone, with a filler of the fields arms commonly also read, and with the listener's other compared
// fields as well (the first value of each), so an arm keyed on two fields is reached too
const FILLER = { on: true, text: 'a line', kind: 'info', pane: 'chat', app: 'chat', tab: 'general', state: 'open', build: 1,
                 sid: '11111111-2222-3333-4444-555555555555', path: 'notes/a.md', usage: {} };
function wordsOf(src) {
  const pairs = [], seen = new Set(), first = {};
  const re = /\.\s*([A-Za-z_$][\w$]*)\s*[!=]==?\s*(['"])([^'"\\]*)\2/g;
  let m;
  while ((m = re.exec(src))) {
    const key = m[1] + '\u0000' + m[3];
    if (seen.has(key)) continue;
    seen.add(key); pairs.push([m[1], m[3]]);
    if (!(m[1] in first)) first[m[1]] = m[3];
  }
  const words = [];
  for (const [k, v] of pairs) words.push({ [k]: v }, Object.assign({}, FILLER, { [k]: v }), Object.assign({}, FILLER, first, { [k]: v }));
  return words;
}
function standIn(type) {
  return stubbed({ type, persisted: false, timeStamp: 0, isTrusted: true, key: '', code: '', data: undefined,
                   detail: undefined, relatedTarget: null, target: STUB, currentTarget: STUB,
                   preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} });
}
const MODE = process.env.ROMP_TEST_MODE || 'reads';
// before any message is tested, what else the page does, once each: every other listener and handler the scripts
// registered, handed a stand-in event of its type; every function they handed to a stand-in; and every word each window
// message listener's arms compare against, from each pane; then whatever those left for later. In rounds, since one may
// register, hand over or queue more (a listener registered here is a listener the census and the reads below count)
const EXERCISE = { ran: 0, errors: 0, words: 0, rounds: 0, left: 0, types: {} };
if (MODE === 'reads' || MODE === 'census' || MODE === 'overwrite') {
  const worded = new Set();
  for (let round = 0; round < 6; round++) {
    const batch = OTHER.splice(0, OTHER.length);
    const fresh = LISTENERS.filter((l) => !worded.has(l));
    if (!batch.length && !fresh.length) break;
    EXERCISE.rounds++;
    for (const o of batch) {
      target.__hLate = o.f; target.__hEv = standIn(o.type);
      EXERCISE.ran++; EXERCISE.types[o.type] = (EXERCISE.types[o.type] || 0) + 1;
      try { vm.runInContext('__hLate.call(window,__hEv)', ctx, { timeout: 2000 }); } catch (x) { EXERCISE.errors++; }
    }
    for (const l of fresh) {
      worded.add(l);
      for (const w of wordsOf(l.src)) for (const p of [CHAT, FILES]) { EXERCISE.words++; deliver(l, p, ORIGIN, w); }
    }
    await drain();
  }
  EXERCISE.left = OTHER.length + LATE.length + LISTENERS.filter((l) => !worded.has(l)).length;
}
// the check the listeners will read, as the scripts, everything they left for later and the exercise left it
const AFTER_BOOT = textOf(target.__rompPaneSourceOk);
// the check's property as a script on the page reads it: its attributes, and whether it holds a value or an accessor
function descriptorSeen() {
  const d = vm.runInContext("Object.getOwnPropertyDescriptor(window,'__rompPaneSourceOk')", ctx);
  if (!d) return null;
  return { writable: 'value' in d ? d.writable : null, configurable: d.configurable, enumerable: d.enumerable,
           value: 'value' in d ? textOf(d.value) : null, accessor: 'get' in d || 'set' in d };
}
const out = { errors: ERRORS, listeners: LISTENERS.map((l) => ({ src: l.src, checkDefined: l.checkDefined })),
              assigns: ASSIGNED.map(textOf), afterBoot: AFTER_BOOT, late: LATE_RUN, exercise: EXERCISE, onmessage: ONMESSAGE,
              descriptor: descriptorSeen(),
              // how often a planted road ran, by the counter a plant bumps (window.__hReached): a road the lock leaves
              // nothing to hear on shows it ran here
              reached: typeof target.__hReached === 'number' ? target.__hReached : 0 };
const REFUSED_SENDERS = Object.keys(SENDERS).filter((k) => k !== 'pane');
if (MODE === 'reads' || MODE === 'nocheck') {
  // the check missing (held as undefined: a name the context lacks answers with a stub): fail-closed hears nothing
  if (MODE === 'nocheck') target.__rompPaneSourceOk = undefined;   // the page defined none (_without_check), so nothing is locked
  out.reads = LISTENERS.map((l) => {
    const r = {};
    Object.keys(SENDERS).forEach((k) => { r[k] = deliver(l, SENDERS[k][0], SENDERS[k][1], { romp: 'none-of-yours' }).reads; });
    return r;
  });
} else if (MODE === 'effects') {
  // two arms with an effect the harness can see: the relay forwarding an editorSelection into the chat pane, and a
  // pane's notify landing in the Log
  const NOTES = [];
  target.__rompNotify = function (kind, text) { NOTES.push([kind, text]); };
  target.__rompChatTarget = null;            // the split's column lookup: none, so the relay aims at #f-chat
  target.__rompPaneToggle = function () {};
  const relay = LISTENERS.find((l) => l.src.indexOf("m.type==='editorSelection'") >= 0);
  const notify = LISTENERS.find((l) => l.src.indexOf("m.romp==='notify'&&m.text") >= 0);
  const sel = { type: 'editorSelection', text: 'a quoted passage', sid: '11111111-2222-3333-4444-555555555555' };
  const note = { romp: 'notify', kind: 'info', text: 'a planted line' };
  const senders = Object.assign({}, SENDERS, { pane: [FILES, ORIGIN] });   // the Files pane's viewer posts it up
  out.effects = {};
  Object.keys(senders).forEach((k) => {
    POSTED.length = 0; NOTES.length = 0;
    deliver(relay, senders[k][0], senders[k][1], sel);
    deliver(notify, senders[k][0], senders[k][1], note);
    out.effects[k] = { toChat: POSTED.filter((p) => p[0] === 'chat').map((p) => p[1]), notes: NOTES.slice() };
  });
} else if (MODE === 'overwrite') {
  // after the scripts, what they left for later and the exercise: a script on the page tries to replace the check with
  // one that admits every sender, in each way a script can. Each attempt starts from the check as the boot left it (one
  // that replaced it on an unlocked page is undone first; on a locked page there is nothing to undo), then every listener
  // is handed a message from each refused sender and from a pane, and the check a script reads is compared with the
  // function the boot left
  const ADOPTED = target.__rompPaneSourceOk, ORIG = Object.getOwnPropertyDescriptor(target, CHECK);
  const same = (a, b) => !!a && !!b && ['value', 'get', 'set', 'writable', 'configurable', 'enumerable'].every((k) => a[k] === b[k]);
  const ANY = 'function(e){return true;}';
  const ATTEMPTS = {
    'an assignment on window': "window.__rompPaneSourceOk=" + ANY + ";",
    'an assignment by a computed name': "window['__romp'+'PaneSourceOk']=" + ANY + ";",
    'an assignment to the bare global': "__rompPaneSourceOk=" + ANY + ";",
    'an assignment on self': "self.__rompPaneSourceOk=" + ANY + ";",
    'an assignment in strict mode': "'use strict';window.__rompPaneSourceOk=" + ANY + ";",
    'a var declaration': "var __rompPaneSourceOk=" + ANY + ";",
    'Reflect.set': "Reflect.set(window,'__rompPaneSourceOk'," + ANY + ");",
    'Object.assign': "Object.assign(window,{__rompPaneSourceOk:" + ANY + "});",
    'defineProperty with a value': "Object.defineProperty(window,'__rompPaneSourceOk',{value:" + ANY + "});",
    'defineProperty with a getter': "Object.defineProperty(window,'__rompPaneSourceOk',{get:function(){return " + ANY + ";}});",
    'a delete, then an assignment': "delete window.__rompPaneSourceOk;window.__rompPaneSourceOk=" + ANY + ";",
    'an unlock, then an assignment': "Object.defineProperty(window,'__rompPaneSourceOk',{writable:true,configurable:true});" +
                                     "window.__rompPaneSourceOk=" + ANY + ";",
  };
  out.attempts = {};
  for (const name of Object.keys(ATTEMPTS)) {
    const now = Object.getOwnPropertyDescriptor(target, CHECK);
    if (!same(now, ORIG) && (!now || now.configurable)) Object.defineProperty(target, CHECK, ORIG);
    let threw = null;
    try { vm.runInContext(ATTEMPTS[name], ctx, { timeout: 2000 }); } catch (x) { threw = String(x && x.message || x).slice(0, 120); }
    const seen = vm.runInContext('window.__rompPaneSourceOk', ctx);
    let refused = 0, pane = 0;
    for (const l of LISTENERS) {
      for (const k of REFUSED_SENDERS) refused += deliver(l, SENDERS[k][0], SENDERS[k][1], { romp: 'none-of-yours' }).reads;
      pane += deliver(l, CHAT, ORIGIN, { romp: 'none-of-yours' }).reads;
    }
    out.attempts[name] = { threw: threw !== null, kept: seen === ADOPTED, refusedReads: refused, paneReads: pane };
  }
} else if (MODE === 'trap') {
  // the global's define trap, reached from a page's script through vm and driven directly: a define that carries a value
  // is a write, one that carries an accessor is a write, and one that changes attributes alone writes nothing and leaves
  // the value in place (vm hands it over with an undefined value; driven directly, it carries none)
  vm.runInContext("window.__rompPaneSourceOk=function(e){return false;};", ctx);
  vm.runInContext("Object.defineProperty(window,'__rompPaneSourceOk',{writable:true,enumerable:true});", ctx);
  Reflect.defineProperty(G, CHECK, { configurable: true });
  const kept = textOf(target.__rompPaneSourceOk);
  vm.runInContext("Object.defineProperty(window,'__rompPaneSourceOk',{get:function(){return function(e){return true;};}});", ctx);
  out.trap = { kept, writes: ASSIGNED.map((v) => (typeof v === 'function' ? textOf(v)
                                                  : (v && typeof v.get === 'function' ? 'an accessor' : 'another write: ' + String(v)))) };
  // the lock's define without its try, on a property of another name holding a function: it throws here (the proxy's
  // invariant, see the comment at G), and the property is locked with its value all the same
  vm.runInContext("window.__hLockProbe=function(e){return false;};", ctx);
  let lockThrew = null;
  try { vm.runInContext("Object.defineProperty(window,'__hLockProbe',{writable:false,configurable:false});", ctx); }
  catch (x) { lockThrew = (x && x.constructor && x.constructor.name) + ': ' + String(x && x.message || x).slice(0, 160); }
  const pd = Object.getOwnPropertyDescriptor(target, '__hLockProbe');
  out.trap.bareLock = { threw: lockThrew, writable: pd.writable, configurable: pd.configurable, value: textOf(pd.value) };
}
out.inEffect = [...IN_EFFECT];
process.stdout.write('\n' + JSON.stringify(out));
})().catch((x) => { process.stderr.write(String(x && x.stack || x)); process.exit(1); });
"""


def _run_landing(mode):
    return _run_scripts(_inline_scripts(km._landing()), mode)


def _without(html, text):
    """`html` with `text`, which it holds once, taken out."""
    assert html.count(text) == 1, "the page holds it once"
    return html.replace(text, "", 1)


def _without_lock(html):
    """The shell with the lock line taken out: what the page was before it."""
    return _without(html, LOCK + "\n")


def _without_check(html):
    """The shell with the definition and the lock taken out: a page on which no script defines the check."""
    i = html.index(REGION_HEAD)
    return _without(html, html[i:html.index(REGION_TAIL, i) + len(REGION_TAIL)] + "\n" + LOCK + "\n")


def _run_scripts(bodies, mode):
    """Run `bodies` (a page's inline scripts, in order) in the stand-in browser under `mode`: reads (the exercise, then each
    listener handed each sender's message), nocheck (each listener handed each sender's message with the check held as
    undefined, no exercise; for a page that does not define it), effects (two arms' visible effects, no exercise), census
    (the exercise and nothing delivered after it), overwrite (the exercise, then each way a script can try to replace the
    check, and every listener handed each sender's message after each) or trap (the global's define trap alone)."""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    fx = tempfile.mkdtemp()
    try:
        scripts = os.path.join(fx, "scripts.json")
        with open(scripts, "w") as f:
            json.dump(bodies, f)
        path = os.path.join(fx, "run.js")
        with open(path, "w") as f:
            f.write(_HARNESS)
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, ROMP_TEST_SCRIPTS=scripts, ROMP_TEST_MODE=mode))
    finally:
        shutil.rmtree(fx, ignore_errors=True)
    if r.returncode != 0:
        raise AssertionError("node failed:\n" + r.stderr[-3000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def _name_of(src):
    names = [n for n, phrase in LISTENERS.items() if phrase in src]
    return names[0] if len(names) == 1 else "unnamed listener: " + src[:120]


class ShellListenersExecuted(unittest.TestCase):
    """The served shell's scripts, run: no listener reads a message from a sender that is not one of its panes, and the
    check every listener reads is the defined function, whatever the page does first.

    The run drives these roads before any message is tested: the scripts themselves; every callback they queue (a timer,
    an animation frame, an idle callback, a microtask, a load or pageshow listener), each once; every other listener or
    handler they register on the window, the document, a frame or an element, once per distinct text, with a stand-in
    event of its type; every function they hand to a stand-in (an observer's callback, a fetch's then), once per distinct
    text; and, from each pane, every word a window message listener's text compares a field of with a string literal
    (m.romp==='settings'); in rounds until none is left. So a write of the check in any spelling that lands on the window
    (window[...]=, a bare global, a defineProperty), or a registration through the window's own addEventListener however
    it is reached, is caught on those roads (test_the_exercise_reaches_a_write_planted_on_each_road, run on the shell
    without its lock, where such a write lands; on the shell as served the lock refuses it). Not run: a road behind a
    condition the stand-in does not meet (location.protocol === 'https:', a user agent, a stored setting, a key its
    stand-in event lacks), an arm keyed some other way than such a comparison (a switch, a lookup table), and a
    registration through another object's method (EventTarget.prototype.addEventListener.call). A registration there is
    caught only when its text names addEventListener (KernelListenerCensus); under a computed name, no test catches it. A
    write of the check there is refused by the lock (CheckLocked)."""

    REFUSED = ("opener", "sandboxedFrame", "otherOriginFrame", "strayWindow", "shellItself", "dispatch",
               "sourcelessElsewhere", "sourcelessOpaque")

    @classmethod
    def setUpClass(cls):
        cls.run_ = _run_landing("reads")

    def test_every_listener_registers_with_the_check_already_defined(self):
        got = sorted(_name_of(l["src"]) for l in self.run_["listeners"])
        self.assertEqual(got, sorted(LISTENERS), "every named listener registered (script errors: %r)" % self.run_["errors"])
        for l in self.run_["listeners"]:
            with self.subTest(listener=_name_of(l["src"])):
                self.assertTrue(l["checkDefined"], "the boot script defines the check before any listener registers")

    def test_no_listener_reads_a_message_from_a_sender_that_is_not_a_pane(self):
        self.assertEqual(len(self.run_["reads"]), len(LISTENERS))
        for l, reads in zip(self.run_["listeners"], self.run_["reads"]):
            name = _name_of(l["src"])
            for sender in self.REFUSED:
                with self.subTest(listener=name, sender=sender):
                    self.assertEqual(reads[sender], 0, "%s read a message from %s" % (name, sender))
            with self.subTest(listener=name, sender="pane"):
                self.assertGreater(reads["pane"], 0, "%s still hears its panes" % name)

    def test_the_check_every_listener_reads_is_the_defined_function(self):
        # whatever spelling a second assignment used (window['...']=, a bare global, a defineProperty), it lands on the
        # page's global, if it runs on a road the stand-in drives: the scripts assign the check once, the defined
        # function, and it is the function in effect at every delivery. The lock's own define carries no new value, so
        # it is no second assignment
        fn = _region(km._landing())[len("window.__rompPaneSourceOk="):-1]
        self.assertTrue(fn.startswith("function(e){") and fn.endswith("}"), "the definition assigns one function expression")
        self.assertEqual(self.run_["assigns"], [fn], "the scripts assign the check once, and assign the defined function")
        self.assertEqual(self.run_["afterBoot"], fn, "after the scripts run, the check is the defined function")
        self.assertEqual(self.run_["inEffect"], [fn], "at every delivery, the check is the defined function")

    def test_the_run_includes_what_the_page_leaves_for_later(self):
        late = self.run_["late"]
        self.assertGreater(late["ran"], 0, "the scripts queue callbacks for later, and the harness ran them: %r" % late)
        self.assertEqual(late["left"], 0, "every callback queued, one queued by another included, ran: %r" % late)

    def test_the_run_includes_every_other_listener_handler_callback_and_arm(self):
        ex = self.run_["exercise"]
        self.assertGreater(ex["ran"], 0, "the scripts register listeners and handlers, and the harness ran them: %r" % ex)
        self.assertGreater(ex["words"], 0, "the listeners' arms were reached from a pane: %r" % ex)
        self.assertEqual(ex["left"], 0, "every listener, handler, callback and message listener was exercised: %r" % ex)

    def test_no_script_assigns_an_onmessage_handler_to_the_window(self):
        self.assertEqual(self.run_["onmessage"], [], "an onmessage handler on the shell's window")

    # A write of the check under a computed name, planted on each road the exercise drives. On the shell without its lock,
    # each is heard and replaces the defined function before the listeners are tested (what the tests above would then
    # red on), so the exercise reaches every road. On the shell as served, each road runs (the plant's counter says so)
    # and the lock leaves the defined function in place (CheckLocked tries every other spelling). The first road is a
    # later inline script's own body, which runs when the parser reaches it, before the page's load event and possibly
    # before a timer the boot script set: a lock the boot script deferred to either would run after the write and lock
    # the planted function in (the harness runs every script before any callback, the order that shows it).
    PLANT_WRITE = "window['__romp'+'PaneSourceOk']=function(e){return !!e;};"
    PLANT_REACH = "window.__hReached=(window.__hReached||0)+1;"
    PLANTS = {
        "a later inline script's body": "WRITE",
        "a window listener (resize)": "window.addEventListener('resize',function(){WRITE});",
        "a document listener (visibilitychange)": "document.addEventListener('visibilitychange',function(){WRITE});",
        "an element's listener (click)": "document.getElementById('none').addEventListener('click',function(){WRITE});",
        "a handler property on the window (onstorage)": "window.onstorage=function(){WRITE};",
        "a fetch's then": "fetch('/version').then(function(r){WRITE});",
        "an observer's callback": "new MutationObserver(function(){WRITE});",
        "a message listener's arm, after the check": "window.addEventListener('message',function(e){" + GATE +
            "var m=e.data;if(m&&m.romp==='planted'){WRITE}});",
        "a timer set by a listener": "window.addEventListener('focus',function(){setTimeout(function(){WRITE},0);});",
    }

    def test_the_exercise_reaches_a_write_planted_on_each_road(self):
        fn = self.run_["assigns"][0]
        unlocked = _inline_scripts(_without_lock(km._landing()))
        for road, plant in self.PLANTS.items():
            with self.subTest(road=road):
                run = _run_scripts(unlocked + [plant.replace("WRITE", self.PLANT_WRITE)], "reads")
                self.assertEqual(run["assigns"][0], fn)
                self.assertGreaterEqual(len(run["assigns"]), 2, "the planted write was heard")
                self.assertNotEqual(run["afterBoot"], fn, "and replaced the check before the listeners were tested")
                self.assertNotEqual(run["inEffect"], [fn], "so a delivery read a check that is not the defined function")

    def test_with_the_lock_a_write_planted_on_each_road_leaves_the_defined_function(self):
        fn = self.run_["assigns"][0]
        base = _inline_scripts(km._landing())
        for road, plant in self.PLANTS.items():
            with self.subTest(road=road):
                run = _run_scripts(base + [plant.replace("WRITE", self.PLANT_REACH + self.PLANT_WRITE)], "reads")
                self.assertGreater(run["reached"], 0, "the planted road ran")
                self.assertEqual(run["assigns"], [fn], "no write but the definition landed")
                self.assertEqual(run["afterBoot"], fn)
                self.assertEqual(run["inEffect"], [fn], "every delivery read the defined function")

    def test_a_listener_registered_from_a_later_road_is_counted(self):
        run = _run_scripts(_inline_scripts(km._landing()) + [
            "window.addEventListener('resize',function(){window['add'+'EventListener']('message',function(e){});});"], "reads")
        self.assertEqual(len(run["listeners"]), len(LISTENERS) + 1, "the listener the resize handler registered is counted")
        self.assertEqual([n for n in (_name_of(l["src"]) for l in run["listeners"]) if n.startswith("unnamed listener")],
                         ["unnamed listener: function(e){}"])

    def test_without_the_check_no_listener_reads_anything(self):
        run = _run_scripts(_inline_scripts(_without_check(km._landing())), "nocheck")
        self.assertEqual(len(run["reads"]), len(LISTENERS))
        for l, reads in zip(run["listeners"], run["reads"]):
            with self.subTest(listener=_name_of(l["src"])):
                self.assertEqual(sum(reads.values()), 0, "fail-closed: no check, no message, not even a pane's")


class ServedPagesExecuted(unittest.TestCase):
    """Every other page the kernel serves, its inline scripts run in the same stand-in, the exercise included: the window
    message listeners each registers, by whatever spelling reaches the window's addEventListener, are its listed kinds
    (PAGE_LISTENERS); none assigns the window an onmessage handler; and none writes the shell's check, under any name, on
    its window or its parent (in the stand-in they are one window). The shell's run is ShellListenersExecuted's; /sw.js
    runs in a worker, where no window posts."""

    # a registered listener's text opens with its kind's head from the function on
    HEADS = {name: head[head.index(",") + 1:] for name, (receiver, head, _n) in KERNEL_LISTENER_KINDS.items()
             if receiver == "window."}

    @classmethod
    def setUpClass(cls):
        cls.runs = {name: _run_scripts(_inline_scripts(page), "census")
                    for name, page in _served_pages().items() if name not in ("/", "/sw.js")}

    def test_each_page_registers_only_its_listed_listeners(self):
        for name, run in self.runs.items():
            with self.subTest(page=name):
                got = {}
                for l in run["listeners"]:
                    kind = next((k for k, h in self.HEADS.items() if l["src"].startswith(h)), "unlisted: " + l["src"][:160])
                    got[kind] = got.get(kind, 0) + 1
                self.assertEqual(got, PAGE_LISTENERS[name], "script errors: %r" % run["errors"])
                self.assertEqual(run["onmessage"], [], "an onmessage handler on the page's window")
                self.assertEqual(run["assigns"], [], "a pane page writes the shell's check")

    def test_each_run_went_through_the_page_and_what_it_leaves(self):
        for name, run in self.runs.items():
            with self.subTest(page=name):
                self.assertEqual(run["late"]["left"], 0, "every callback the page queued ran: %r" % run["late"])
                self.assertEqual(run["exercise"]["left"], 0, "every listener, handler and handed-over callback ran: %r"
                                 % run["exercise"])
        for name in ("/chat", "/feed", "/timeline"):
            with self.subTest(page=name):
                self.assertGreater(self.runs[name]["exercise"]["ran"], 0, "the pane page's handlers ran")


class ShellArmsExecuted(unittest.TestCase):
    """Two arms whose effect is visible: the relay forwards a Files-pane selection into the chat's composer, and a
    pane's notify lands in the Log; from any other sender neither happens."""

    @classmethod
    def setUpClass(cls):
        cls.effects = _run_landing("effects")["effects"]

    def test_the_relay_forwards_an_editor_selection_only_from_a_pane(self):
        sel = {"type": "editorSelection", "text": "a quoted passage", "sid": "11111111-2222-3333-4444-555555555555"}
        self.assertEqual(self.effects["pane"]["toChat"], [sel], "the Files pane's selection reaches the chat once")
        for sender in ShellListenersExecuted.REFUSED:
            with self.subTest(sender=sender):
                self.assertEqual(self.effects[sender]["toChat"], [], "nothing forwarded into the chat from " + sender)

    def test_a_log_line_is_planted_only_by_a_pane(self):
        self.assertEqual(self.effects["pane"]["notes"], [["info", "a planted line"]])
        for sender in ShellListenersExecuted.REFUSED:
            with self.subTest(sender=sender):
                self.assertEqual(self.effects[sender]["notes"], [], "no Log line from " + sender)


class CheckLocked(unittest.TestCase):
    """The lock, the line right after the definition: once the boot script has run, the check's property is read-only
    and non-configurable and holds the defined function, so no later script replaces, redefines or deletes it, however
    it spells the attempt, and every listener still reads the defined function after each. The same attempts on the
    shell without its lock each replace the check, so the harness can see a replacement when one lands. A script that
    runs BEFORE the boot script is not refused by the lock: the served shell's earlier scripts are run here, and the
    descriptor read after boot would show what they left."""

    ATTEMPTS = ("an assignment on window", "an assignment by a computed name", "an assignment to the bare global",
                "an assignment on self", "an assignment in strict mode", "a var declaration", "Reflect.set", "Object.assign",
                "defineProperty with a value", "defineProperty with a getter", "a delete, then an assignment",
                "an unlock, then an assignment")

    @classmethod
    def setUpClass(cls):
        # the served shell, as it is: the pins below read this run alone, so they fail on their own assertions when the
        # lock is missing or weakened (the contrast builds its page in its own test)
        html = km._landing()
        cls.locked = _run_scripts(_inline_scripts(html), "overwrite")
        cls.fn = _region(html)[len("window.__rompPaneSourceOk="):-1]

    def test_after_boot_the_check_is_read_only_non_configurable_and_the_defined_function(self):
        self.assertEqual(self.locked["errors"], [], "the shell's scripts ran")
        self.assertEqual(self.locked["descriptor"], {"writable": False, "configurable": False, "enumerable": True,
                                                     "value": self.fn, "accessor": False})

    def test_no_attempt_to_replace_the_check_leaves_anything_but_the_defined_function(self):
        got = self.locked["attempts"]
        self.assertEqual(sorted(got), sorted(self.ATTEMPTS), "every attempt ran")
        for name in self.ATTEMPTS:
            with self.subTest(attempt=name):
                self.assertTrue(got[name]["kept"], "the check a script reads is still the defined function")
                self.assertEqual(got[name]["refusedReads"], 0, "no listener reads a message from a sender that is not a pane")
                self.assertGreater(got[name]["paneReads"], 0, "and the listeners still hear their panes")
                # a browser refuses some of these with a TypeError and the rest without a word; node's vm global drops
                # a strict-mode write without one, so whether it threw is not read here, only that nothing landed
        self.assertEqual(self.locked["inEffect"], [self.fn], "every delivery read the defined function")

    def test_without_the_lock_each_attempt_replaces_the_check(self):
        # the contrast: the same page less its lock line. Each attempt runs without an error and lands, and the listeners
        # then read messages from senders the check refuses: so every attempt above is a working replacement, and a
        # replacement that lands is one these tests see
        unlocked = _run_scripts(_inline_scripts(_without_lock(km._landing())), "overwrite")
        self.assertEqual(unlocked["descriptor"], {"writable": True, "configurable": True, "enumerable": True,
                                                  "value": self.fn, "accessor": False})
        got = unlocked["attempts"]
        self.assertEqual(sorted(got), sorted(self.ATTEMPTS))
        for name in self.ATTEMPTS:
            with self.subTest(attempt=name):
                self.assertFalse(got[name]["threw"], "nothing refused it")
                self.assertFalse(got[name]["kept"], "it replaced the check")
                self.assertGreater(got[name]["refusedReads"], 0, "and a listener read a message the check refuses")

    def test_the_harness_counts_a_define_as_a_write_only_when_it_carries_a_value_or_an_accessor(self):
        # the lock's define changes attributes alone ({writable:false,configurable:false}): no write, and the check keeps
        # its value, so a page that locks the check still assigns it once and its listeners still read the defined
        # function (ShellListenersExecuted). vm fills a missing value in as undefined before the trap sees it
        trap = _run_scripts([], "trap")["trap"]
        self.assertEqual({k: trap[k] for k in ("kept", "writes")}, {"kept": "function(e){return false;}",
                                                                    "writes": ["function(e){return false;}", "an accessor"]})

    def test_in_this_harness_the_lock_define_throws_and_still_locks_so_its_try_is_what_lets_the_boot_run_on(self):
        # the harness quirk the comment at the stand-in global describes, pinned so the comment cannot go stale: run bare,
        # the lock's define throws a TypeError here (a browser's does not), and the property is locked with its value
        bare = _run_scripts([], "trap")["trap"]["bareLock"]
        self.assertIsNotNone(bare["threw"], "the define throws in this harness")
        self.assertTrue(bare["threw"].startswith("TypeError: "), bare["threw"])
        self.assertIn("defineProperty", bare["threw"], "the proxy's defineProperty invariant, not a script error")
        self.assertEqual({k: bare[k] for k in ("writable", "configurable", "value")},
                         {"writable": False, "configurable": False, "value": "function(e){return false;}"},
                         "locked, and holding the function it held")
        self.assertIn("try{", LOCK, "the served lock line carries the try that absorbs it")


if __name__ == "__main__":
    unittest.main()
