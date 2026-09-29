#!/usr/bin/env python3
"""The documents name the opener policy, the sender checks and the URL-pane tab limit (2026-09-29).

The sender hardening (every page the kernel serves declares Cross-Origin-Opener-Policy: same-origin-allow-popups, a
state-root pane's own files aside, and every window message listener checks its sender) shipped with no line in any
document. SECURITY.md is where the repository points security readers, and plans/panes-as-data.md section 5 is the
pane protocol's contract, which `romp pane`'s help points to. Each claim is read against the code it describes, so the
text cannot state a header value, a file or a sandbox the tree does not have:
  - SECURITY.md's list of what is already hardened has a Window isolation bullet: the header and its value as
    Handler._send sends it, the state-root exemption, what it keeps from a page on another origin (a handle, which the
    popup lab witnesses), the sender checks in the listeners romp installs and their helper's path, and the diagnostic
    row a pane's listener files for its first drop from each sender origin, up to window-sender.ts's cap a page load;
  - its residual cautions say where browsers ignore the policy (a plain-http address other than localhost and
    127.0.0.1), and that the sender checks are what remains there;
  - the plan's section 5 says a URL pane cannot open one of the dashboard's pages in a new tab, why (the inherited
    sandbox against the policy), what lifting it would take short of dropping the policy, and names its witness, a
    test the popup lab defines; the paragraph follows "The pin:", which pins the bullets above it; the URL frame's
    sandbox in the kernel lacks the token that would lift it.
Reads the tree only: no kernel, no browser.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _flat(text):
    """The text with every run of whitespace as one space, so a claim reads the same however its lines wrap."""
    return re.sub(r"\s+", " ", text)


def _section(text, head, next_head):
    i = text.index(head)
    j = text.index(next_head, i + len(head))
    return text[i:j]


def _bullet(section, label):
    """The one top-level bullet of `section` whose text starts with `label`, up to the next bullet or the end."""
    hits = [m.start() for m in re.finditer(r"(?m)^- " + re.escape(label), section)]
    if len(hits) != 1:
        raise AssertionError("%d bullets start with %r" % (len(hits), label))
    rest = section[hits[0]:]
    nxt = re.search(r"(?m)^(- |#)", rest[2:])
    return rest if not nxt else rest[:nxt.start() + 2]


class SecurityDoc(unittest.TestCase):
    def setUp(self):
        self.doc = _read("SECURITY.md")
        self.kernel = _read("kernel", "kernel.py")

    def test_the_hardened_list_names_the_opener_policy_as_the_kernel_sends_it(self):
        hardened = _section(self.doc, "\n## What is already hardened\n", "\n## ")
        b = _flat(_bullet(hardened, "**Window isolation:**"))
        sent = re.findall(r'send_header\("Cross-Origin-Opener-Policy", "([^"]+)"\)', self.kernel)
        self.assertEqual(sent, ["same-origin-allow-popups"], "Handler._send sends the header once, with this value")
        self.assertIn("`Cross-Origin-Opener-Policy: %s`" % sent[0], b)
        self.assertIn("a state-root pane's own files excepted", b, "the exemption Handler._send's opener_policy=False makes")
        self.assertEqual(self.kernel.count("opener_policy=False)"), 1, "one exempt route: a state-root pane's own files")

    OPENER_WITNESS = "test_a_page_on_another_origin_that_opens_the_shell_gets_a_closed_handle"

    def test_the_hardened_list_says_what_the_policy_keeps_from_an_opener_and_the_popup_lab_witnesses_it(self):
        hardened = _section(self.doc, "\n## What is already hardened\n", "\n## ")
        b = _flat(_bullet(hardened, "**Window isolation:**"))
        self.assertIn("so a page on another origin that opens the dashboard gets no handle to post messages through", b)
        # the popup lab's leg 3 opens the shell from a page on another origin and reads a closed handle and no opener
        lab = _read("tests", "test_pane_popup_opener_served.py")
        self.assertEqual(len(re.findall(r"(?m)^    def %s\(self\):" % self.OPENER_WITNESS, lab)), 1)

    def test_the_hardened_list_names_the_per_listener_sender_checks_and_their_trace(self):
        hardened = _section(self.doc, "\n## What is already hardened\n", "\n## ")
        b = _flat(_bullet(hardened, "**Window isolation:**"))
        # the listeners romp installs: a state-root pane's own page runs its author's scripts, whose listeners are theirs
        self.assertIn("every window message listener romp installs in those pages checks the sender", b)
        self.assertIn("one of the shell's own panes (in the shell)", b, "the shell's listeners read its source check")
        self.assertIn("the page itself, its parent or a window on its origin (in a pane, `ui/webview/window-sender.ts`,", b)
        helper = _read("ui", "webview", "window-sender.ts")
        self.assertIn("export function windowSender(", helper)
        self.assertIn("export function noteForeign(", helper)
        # the trace is the panes' (window-sender.ts noteForeign); the shell's source check drops without one. A listener
        # files one row per sender origin a page life, so a repeat from that origin files none, and at most NOTE_MAX rows
        cap = re.findall(r"(?m)^export const NOTE_MAX = (\d+);$", helper)
        self.assertEqual(len(cap), 1, "window-sender.ts caps the rows a page files")
        self.assertIn("if (seen.has(key) || seen.size >= NOTE_MAX) return;", helper, "the row per site and origin, and the cap")
        self.assertIn("(in a pane, `ui/webview/window-sender.ts`, where each listener's first drop from a given sender origin"
                      " leaves a diagnostic row, up to %s rows per page load)" % cap[0], b)
        self.assertEqual(len(re.findall(r"\.(?:\s|$)", b)), 1, "one sentence: its only full stop ends it")

    def test_the_residual_cautions_say_where_the_policy_is_ignored_and_what_remains(self):
        cautions = _section(self.doc, "\n## Residual cautions on shared machines\n", "\n## ")
        b = _flat(_bullet(cautions, "On a plain-http address"))
        self.assertIn("other than `localhost` or `127.0.0.1`", b)
        self.assertIn("`ROMP_SERVE_HOST`", b)
        self.assertIn("browsers ignore the opener policy", b)
        self.assertIn("they enforce it only in a secure context", b)
        self.assertIn("the sender checks in each page's listeners are the remaining protection", b)
        self.assertIn("the https route through `tailscale serve` are secure contexts", b)

    def test_no_em_dash_in_the_new_lines(self):
        for label, head in (("**Window isolation:**", "\n## What is already hardened\n"),
                            ("On a plain-http address", "\n## Residual cautions on shared machines\n")):
            with self.subTest(bullet=label):
                self.assertNotIn("\u2014", _bullet(_section(self.doc, head, "\n## "), label))


class PanesPlan(unittest.TestCase):
    WITNESS = "test_residual_a_url_panes_popup_onto_a_kernel_page_shows_no_page"

    def setUp(self):
        self.sec5 = _section(_read("plans", "panes-as-data.md"), "\n## 5. The pane protocol", "\n## 6. ")
        self.kernel = _read("kernel", "kernel.py")

    def _para(self):
        at = self.sec5.index("**A URL pane cannot open one of the dashboard's pages in a new tab.**")
        end = self.sec5.find("\n\n", at)
        return self.sec5[at:end if end >= 0 else len(self.sec5)]

    def test_section_five_says_a_url_pane_cannot_open_a_dashboard_page_in_a_tab_and_why(self):
        p = _flat(self._para())
        self.assertIn("inherits the frame's sandbox", p)
        self.assertIn("only when that response's `Cross-Origin-Opener-Policy` is `unsafe-none`", p)
        self.assertIn("declares `same-origin-allow-popups` except a state-root pane's own files", p)
        self.assertIn("A URL pane can open a state-root pane's own files.", p)
        self.assertIn("Short of dropping the policy, lifting the limit would take `allow-popups-to-escape-sandbox` on the frame", p)
        self.assertNotIn("\u2014", p)

    def test_the_tab_limit_follows_the_pin_so_the_pin_reads_as_the_bullets_own(self):
        # "The pin:" pins the URL-pane bullets' source check; the tab limit comes after it, not between them
        last_bullet = "- it keeps the ring as its one grab surface (section 4) and toggles, docks and resizes like any pane.\n\nThe pin: "
        self.assertEqual(self.sec5.count(last_bullet), 1, "the pin paragraph directly follows the bullets")
        self.assertLess(self.sec5.index("\nThe pin: "), self.sec5.index("**A URL pane cannot open one of the dashboard's pages in a new tab.**"))

    def test_the_url_frame_has_no_token_that_lifts_the_limit(self):
        frames = re.findall(r"""sandbox="([^"]*)\"""", self.kernel)
        self.assertEqual(frames, ["allow-scripts allow-forms allow-popups"], "the URL pane's frame, the one sandboxed iframe")
        self.assertNotIn("allow-popups-to-escape-sandbox", frames[0])

    def test_the_named_witness_is_a_test_the_popup_lab_defines(self):
        p = _flat(self._para())
        self.assertIn("`tests/test_pane_popup_opener_served.py` witnesses the refusal (`%s`)" % self.WITNESS, p)
        lab = _read("tests", "test_pane_popup_opener_served.py")
        self.assertEqual(len(re.findall(r"(?m)^    def %s\(self\):" % self.WITNESS, lab)), 1)


if __name__ == "__main__":
    unittest.main()
