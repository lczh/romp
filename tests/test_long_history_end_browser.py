"""End pressed from far back in a long session brings its newest row on screen (2026-10-01). These classes stood in
tests/test_long_history_height_browser.py, whose lab and driver they import, and have a file of their own (2026-10-11) so the CI sharder
can deal them to another shard. Served, at 2x, on that lab's hermetic kernel over its synthetic transcript: about two thousand short
turns of history, then a tail of tall replies.

The classes run in four fixture families, each on one kernel and one page (_EndFamily): A, the 2400-line tail with no tool rows; B, the
2400-line tail with a Bash call in each short turn; C, the 1800-line tail with those calls; D, C's fixture with every eighth short
turn's output 200 lines long and classic scrollbars. On a family's page the reader drops into the head gap at 15% of the scroll range
and climbs back eight history pages with the wheel, once. Then each class in turn, in the family's order: what the class before it left
is cleared (the selection, focus, the message box's text and quote chips, opened tool rows, and the page's last press, set outside the
transcript by a press on a spot the lab adds for it and removes), the reader goes back far by scroll writes into the run the climb
loaded (the family's first class stands where the climb left it, far back already, as each class alone did), the class's own road runs
(below), the page goes quiet (an idle period, with no task over 50 ms ended in the last 400 ms) or 3 s pass, whichever comes first,
and End is pressed once and read. Each class's tests read its own readings from where it stood before its road on, the family's drop
and the first END_PAGES steps of its climb, and three runs of page errors: those raised before the family's first class starts (its
boot, its drop and climb, and the lab's setup after them), those raised in the class's own window (from its start to its last
reading), and those raised after the family's last class. A road that throws fails its own class's tests, and the family's run goes on
with the next class.

Why the press waits for a quiet page, and the first class makes no go-back: with the page's End handler gone, End is the browser's
smooth scroll, which the page's first re-window cuts short of the bottom, so these tests fail; work left over from the road or the
go-back, run at the press, delays that re-window, and past the scroll's end the reader reaches the bottom with no handler at all.

LongHistoryEnd, over LongHistoryClimb's taller tail: from far back, the reader clicks in the transcript (at its centre, or at the
nearest point straight above or below it where no reply's element stands: family A's markups stand in the short answers) and presses
End once. The newest row must then be on screen, within a screen of the bottom.

LongHistoryEndAfterAFoldToggle, LongHistoryEnd's road over a history whose short turns each carry a Bash call: the click lands on a
tool row's fold toggle with nothing selected before, and leaves the selection with no anchor. The newest row must then be on screen,
within a screen of the bottom.

LongHistoryEndAfterTypingAndAFoldToggle, that road at TALL_LINES 1800 with two words typed in the message box before the click: the
selection's anchor stays in the footer, outside the transcript, and the press on the toggle comes after it. The page must take
End, and the newest row must then be on screen, within a screen of the bottom.

LongHistoryEndAfterTypingAFoldToggleAndASelectionOnTheStrip, LongHistoryEndAfterAFoldToggleAndSelectAll and
LongHistoryEndAfterAFoldToggleAndACommentBoxClosedWithEscape, the typing class's road (the last two with nothing typed) with a step
after the click: the selection collapsed on the strip by a script; Ctrl+A; a word of a reply double-clicked, the comment key pressed,
two words typed in its box, then Escape. Each leaves the selection's anchor outside the transcript, changed after the last press, which
lies inside it. The page must take End, and the newest row must then be on screen, within a screen of the bottom.

LongHistoryEndAfterACaretInAToolOutputBoxAndAShiftClickOutsideIt, LongHistoryEndAfterAClickOutsideAToolOutputBoxAndAFindInsideIt,
LongHistoryEndAfterACaretInAToolOutputBoxAFoldToggleAndTextWrittenIntoTheMessageBox and
LongHistoryEndAfterACaretInAToolOutputBoxAndAFoldToggle, the typing class's road with nothing typed, every eighth short turn's Bash
output 200 lines long and classic scrollbars, and after the click one such output opened at its top: a click on a word in its box, then
Shift+click on a word of a reply outside it; a click on a word of a reply, then window.find of a line in the box; a click on a word in
the box, a press on another tool row's fold toggle, then the message box's text written by a script; the same without the write. Each
leaves the selection's anchor in the box, which can still scroll down, and the last press in the transcript outside it; in the first
three the selection changed after that press. The page must take End, and the newest row must then be on screen, within a screen of
the bottom.

LongHistoryEndAfterAFoldToggleAndAPage, LongHistoryEndAfterAFoldToggle's road with one more history page climbed between the click and
End, by which point the pressed toggle is no longer in the document. The newest row must then be on screen, within a screen of the
bottom.

LongHistoryEndWithNoSelectionAfterAClickAndAPage, LongHistoryEnd's road with the selection emptied after the click and one more
history page climbed before End, by which point the clicked element is no longer in the document. The newest row must then be on
screen, within a screen of the bottom.

The classes after it, but the last, run LongHistoryEnd's road with an element reached in place of the click (END_AT; family A's
END_MARKUPs, each element with an id of its own, rotate across every second short answer): pressed, with focus then on the page body
and no selection (a reply's role="menu" element, its role="listbox" element, its task-list checkbox), or focused (a link or a Copy
button inside a reply's role="listbox" element, a question card's checkbox, a reply's audio whose clip has ended, failed to load, is
not preloaded or has no source, a reply's video with no controls and a tabindex, its clip loaded). Each checks where End meets the
element; the newest row must then be on screen, within a screen of the bottom. The question card's class runs last in its family, since
the card stays on the page.

LongHistoryEndFromAReplysAudioPlayControl, the last, runs that road with focus on the play control of a reply's audio, reached by Tab: the
page does not receive End, and no scroll event of the transcript at its end comes while focus is on the audio.

Synthetic fixtures only (placeholder uuids, invented prose).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.test_long_history_height_browser import _LongHistoryLab  # noqa: E402  the lab and its driver (a mixin, no TestCase)

ASK_CARD = {"kind": "multi", "header": "Tables", "question": "Which tables should the notes-api keep?",   # a question card the End press meets
            "options": [{"n": 1, "label": "notes", "checked": False, "selected": True}, {"n": 2, "label": "tags", "checked": False, "selected": False}],
            "cursor": 1, "cursorFound": True, "multiSelect": True, "sig": "lab-end-far-1"}
FAMILY_RUNS = {}   # a family's run in this process, by the family's name: its driver's result, or how it failed or skipped


class _EndFamily(_LongHistoryLab):
    """A fixture family's lab (not a TestCase): one kernel and one page for the End classes that share its fixture, MEMBERS, driven
    through ROAD 11 once, in the order MEMBERS lists them, the first time one of them reads its result. Its fixture (TALL_LINES,
    HISTORY_TOOLS, LONG_OUTPUT, BARS, SCALE, VIEWPORT) is each member's own (checked at import: _check_families), and it serves the clips
    when a member's road needs them. The members' END_MARKUP rotate across the short answers, every second one carrying the next in
    turn, so each recurs every 2 x (their count) turns, inside every window the go-back builds."""
    SCALE = 2
    LEGS = ("end",)
    BOOT = "measure"      # no class of a family reads ROADs 1 to 3
    END_PAGES = 8         # the climb: the longest a member reads (each member reads its own END_PAGES first steps)
    GO_BACK_UNITS = 40    # each later member's go-back: a write this many units under the loaded run's top builds a window over the run's first rows,
    GO_BACK_TURNS = 20    # and a second write lands on its row this many turns under the run's top, the head gap above the window
    TIMEOUT = 540         # s: the family's driver run, under the 600 s the project CI gives one test
    MEMBERS = ()

    @classmethod
    def _markup(cls, k):
        marks = [m.END_MARKUP for m in cls.MEMBERS if m.END_MARKUP]
        return marks[k // 2 % len(marks)] if marks and k % 2 == 0 else ""

    @classmethod
    def _run(cls, test):
        """The family's driver result, run once in this process (FAMILY_RUNS), for the first member test that reads it: the kernel
        booted, the driver run over every member's road, the kernel stopped and the lab removed. A run that failed fails every member
        test that reads it, with how it failed; one that could not run skips them (fails them under ROMP_SERVED_TESTS_REQUIRE=1)."""
        run, first = FAMILY_RUNS.get(cls.__name__), False
        if run is None:
            run, first = FAMILY_RUNS.setdefault(cls.__name__, {"by": test.id()}), True
            try:
                cls._boot()
                cfg = dict(cls._cfg(), endPages=cls.END_PAGES, goBackUnits=cls.GO_BACK_UNITS, goBackTurns=cls.GO_BACK_TURNS,
                           variants=[m._variant() for m in cls.MEMBERS])
                run["r"], run["failed"], _ = cls._drive(cfg, cls.TIMEOUT)
            except unittest.SkipTest as e:
                run["skip"] = str(e)
            except Exception as e:   # _skip under ROMP_SERVED_TESTS_REQUIRE=1 raises AssertionError; anything else is a failed run too
                run["failed"] = "%s: %s" % (type(e).__name__, e)
            finally:
                cls.tearDownClass()
            if run.get("r") is not None:
                print("LONG-HISTORY:", cls.__name__, json.dumps(run["r"]), file=sys.stderr)
        if run.get("skip") is not None:
            raise unittest.SkipTest(run["skip"])
        if run.get("failed") is not None:
            test.fail(run["failed"] if first else "the %s run (by %s) failed, and is not run again: %s" % (cls.__name__, run["by"], run["failed"]))
        return run["r"]


class LongHistoryEnd(_LongHistoryLab, unittest.TestCase):
    SCALE = 2
    TALL_LINES = 2400     # LongHistoryClimb's tail
    LEGS = ("end",)
    END_PAGES = 8

    @classmethod
    def _boot(cls):
        """Nothing of the class's own: its family boots the kernel and the page for all its classes (_EndFamily._run)."""

    @classmethod
    def _variant(cls):
        """The class's road, as its family's driver run takes it (ROAD 11's vc)."""
        return {"name": cls.__name__, "endPress": cls.END_PRESS, "endType": cls.END_TYPE, "endAfter": cls.END_AFTER,
                "endUnselect": cls.END_UNSELECT, "endPageAfter": cls.END_PAGE_AFTER, "endAt": cls.END_AT, "endBox": cls.END_BOX}

    def _result(self):
        """The class's readings from its family's run, shaped as a run of the class alone was: the family's drop and the first
        END_PAGES steps of its climb; the class's own readings (where it stood before its staging, the staging's readings and the End
        press's); and the page errors raised before the family's first class started (pageErrors up to nShared: the boot, the drop and
        climb, and the lab's setup after them), in the class's own window, and after the family's last class (from lastErr). A road
        that threw fails the class's tests, with the error its record holds."""
        fam = FAMILY_OF[type(self)]
        r = fam._run(self)
        e = r["end"]
        v = e["variants"].get(type(self).__name__)
        if v is None:
            self.fail("the %s run holds no record of this class (it holds %r)" % (fam.__name__, sorted(e["variants"])))
        if v.get("error"):
            self.fail("this class's road failed in the %s run, before its readings: %s" % (fam.__name__, v["error"]))
        return {"pageErrors": r["pageErrors"][:e["nShared"]] + v["pageErrors"] + r["pageErrors"][e["lastErr"]:],
                "end": {"drop": e["drop"], "steps": e["steps"][:self.END_PAGES], "climbed": v["climbed"], "clicked": v["clicked"],
                        "pressed": v["pressed"]}}

    def _assert_the_click(self, k):
        self.assertTrue(k["bodyFocused"] and k["anchorInContent"], "the click left focus on the page body and the selection's anchor in the transcript: %r" % k)

    def test_the_first_end_from_far_back_shows_the_newest_row(self):
        e = self._result()["end"]
        self.assertTrue(e["drop"]["gap"] and e["drop"]["gap"]["turnAtTop"] is not None, "the drop lands inside the head gap: %r" % self._brief(e["drop"]))
        self.assertEqual([len(x["asked"]) for x in e["steps"]], [1] * self.END_PAGES, "the wheel climbs %d pages, one asked per step: %r" % (self.END_PAGES, e["steps"]))
        c = e["climbed"]
        self.assertGreater(c["dist"], 100 * c["ch"], "the reader stands more than a hundred screens from the bottom: %r" % self._brief(c))
        self._assert_the_click(e["clicked"])
        p = e["pressed"]
        self.assertTrue(p["newestOnScreen"], "the newest row is on screen after the first End: %r" % self._brief(p))
        self.assertLessEqual(p["dist"], p["ch"], "…within a screen of the bottom: %r" % self._brief(p))


class LongHistoryEndAfterAFoldToggle(LongHistoryEnd):
    HISTORY_TOOLS = True
    END_PRESS = "toggle"

    def _assert_the_click(self, k):
        self.assertTrue(k["toggle"] and k["toggle"]["ranges"] == 0, "a fold toggle on screen, nothing selected before the click: %r" % k)
        self.assertTrue(k["bodyFocused"] and not k["anchor"] and k["pressOnToggle"],
                        "the click left focus on the page body, the selection with no anchor and the last press on the toggle: %r" % k)


class LongHistoryEndAfterTypingAndAFoldToggle(LongHistoryEndAfterAFoldToggle):
    TALL_LINES = 1800     # a tail the base lays out under the layout limit
    END_TYPE = "a draft"

    def _assert_the_click(self, k):
        self.assertTrue(k["toggle"] and k["bodyFocused"] and k["pressOnToggle"],
                        "a fold toggle on screen; the click left focus on the page body and the last press on the toggle: %r" % k)
        self.assertTrue(k["anchor"] and not k["anchorInContent"] and k["anchorInFooter"], "…and the selection's anchor in the footer (anchorInFooter): %r" % k)
        self.assertGreater(k["pressT"], k["selT"], "…and the press on the toggle after the selection's last change: %r" % k)

    def test_the_page_takes_the_first_end_from_far_back_and_shows_the_newest_row(self):
        e = self._result()["end"]
        self.assertEqual([len(x["asked"]) for x in e["steps"]], [1] * self.END_PAGES, "the wheel climbs %d pages, one asked per step: %r" % (self.END_PAGES, e["steps"]))
        self.assertGreater(e["climbed"]["dist"], 100 * e["climbed"]["ch"], "the reader stands more than a hundred screens from the bottom: %r" % self._brief(e["climbed"]))
        self._assert_the_click(e["clicked"])
        p = e["pressed"]
        self.assertEqual((e["clicked"]["taken"], p["newestOnScreen"], p["dist"] <= p["ch"]), (True, True, True),
                         "the page took End, and the newest row is on screen within a screen of the bottom: taken %r, %r" % (e["clicked"]["taken"], self._brief(p)))


class _ASelectionAfterTheClick:
    """ROAD 11 with END_AFTER (mixed in ahead of LongHistoryEndAfterTypingAndAFoldToggle): a selection change after the press."""

    def _assert_the_click(self, k):
        self.assertTrue(k["toggle"] and k["bodyFocused"] and k["pressInContent"],
                        "a fold toggle on screen; focus on the page body and the last press inside the transcript: %r" % k)
        self.assertTrue(k["anchor"] and not k["anchorInContent"] and k["selT"] > k["pressT"],
                        "…then the selection changed after the press, its anchor outside the transcript: %r" % k)


class LongHistoryEndAfterTypingAFoldToggleAndASelectionOnTheStrip(_ASelectionAfterTheClick, LongHistoryEndAfterTypingAndAFoldToggle):
    END_AFTER = "collapse"


class LongHistoryEndAfterAFoldToggleAndSelectAll(_ASelectionAfterTheClick, LongHistoryEndAfterTypingAndAFoldToggle):
    END_TYPE = None
    END_AFTER = "selectAll"


class LongHistoryEndAfterAFoldToggleAndACommentBoxClosedWithEscape(_ASelectionAfterTheClick, LongHistoryEndAfterTypingAndAFoldToggle):
    END_TYPE = None
    END_AFTER = "comment"

    def _assert_the_click(self, k):
        c = k.get("comment") or {}
        self.assertTrue(c.get("downs") == [1, 2] and c.get("pressOnWord"), "after the click, a double click on a word of a reply, which made the last press: %r" % c)
        self.assertTrue(c.get("opened") and c.get("focused") and c.get("text") == "a note",
                        "…then the comment key opened the comment box, with focus and the two words typed in it: %r" % c)
        super()._assert_the_click(k)


class _ABoxRoad:
    """ROAD 11 with END_BOX (mixed in ahead of LongHistoryEndAfterTypingAndAFoldToggle, nothing typed): a road in a tool output box
    after the click, every eighth short turn's output 200 lines long, classic scrollbars. BOX_PREMISE: readings right before End."""
    END_TYPE = None
    LONG_OUTPUT = True
    BARS = True
    BOX_PREMISE = {}

    def _assert_the_click(self, k):
        r = k.get("box") or {}
        b = r.get("before") or {}
        self.assertTrue(k["toggle"] and b.get("bodyFocused") and b["anchorInContent"] and b["anchorInBox"] and b["box"] and b["box"]["canScroll"],
                        "a fold toggle on screen; focus on the page body, the selection's anchor in a tool output box that can still scroll down: toggle %r, %r" % (k["toggle"], r))
        self.assertTrue(b["pressInContent"] and not b["pressInBox"] and b["dist"] > b["ch"],
                        "…the last press in the transcript outside that box, more than a screen from the bottom: %r" % r)
        self.assertEqual({x: b[x] for x in self.BOX_PREMISE}, self.BOX_PREMISE, "…and %r: %r" % (self.BOX_PREMISE, r))


class LongHistoryEndAfterACaretInAToolOutputBoxAndAShiftClickOutsideIt(_ABoxRoad, LongHistoryEndAfterTypingAndAFoldToggle):
    END_BOX = "shift"
    BOX_PREMISE = {"later": "selection"}


class LongHistoryEndAfterAClickOutsideAToolOutputBoxAndAFindInsideIt(_ABoxRoad, LongHistoryEndAfterTypingAndAFoldToggle):
    END_BOX = "find"
    BOX_PREMISE = {"later": "selection", "selTarget": "document"}

    def _assert_the_click(self, k):
        self.assertIs((k.get("box") or {}).get("found"), True, "window.find found the line in the box: %r" % k.get("box"))
        super()._assert_the_click(k)


class LongHistoryEndAfterACaretInAToolOutputBoxAFoldToggleAndTextWrittenIntoTheMessageBox(_ABoxRoad, LongHistoryEndAfterTypingAndAFoldToggle):
    END_BOX = "toggleWrite"
    BOX_PREMISE = {"later": "selection", "pressOnToggle": True, "selTarget": "TEXTAREA#composer-input"}


class LongHistoryEndAfterACaretInAToolOutputBoxAndAFoldToggle(_ABoxRoad, LongHistoryEndAfterTypingAndAFoldToggle):
    END_BOX = "toggle"
    BOX_PREMISE = {"later": "press", "pressOnToggle": True}


class _APageAfterTheClick:
    """ROAD 11 with one more history page climbed between the click and End (mixed in ahead of an End class): the class reads the
    first seven steps (END_PAGES) of its family's eight-page climb, and climbs its own page after the click."""
    END_PAGES = 7
    END_PAGE_AFTER = True

    def _assert_the_click(self, k):
        super()._assert_the_click(k)
        a = k["after"]
        self.assertEqual(len(a["asked"]), 1, "after the click the wheel asks one history page: %r" % a)
        self.assertTrue(a["gapLoading"] == 0 and a["measure"]["units"] > a["before"]["units"], "…which lands and fills: %r" % a)
        self.assertIs(a["pressConnected"], False, "…and the element the click pressed is no longer in the document: %r" % a)
        p = k.get("atPress")
        self.assertTrue(p and p["bodyFocused"] and not p["anchor"], "…and when End is pressed, focus on the page body and no anchor in the selection: %r" % p)


class LongHistoryEndAfterAFoldToggleAndAPage(_APageAfterTheClick, LongHistoryEndAfterAFoldToggle):
    pass


class LongHistoryEndWithNoSelectionAfterAClickAndAPage(_APageAfterTheClick, LongHistoryEnd):
    END_UNSELECT = True

    def _assert_the_click(self, k):
        super()._assert_the_click(k)
        self.assertTrue(k["unselected"], "the selection was emptied after the click: %r" % k)


class _EndAt:
    """ROAD 11 with END_AT in place of the click (mixed in ahead of LongHistoryEnd), read where End meets the element."""

    def _assert_the_click(self, k):
        a = k.get("atEnd")
        self.assertTrue(k["picked"] and a and a["inContent"], "End reached the page with the element in the transcript: %r" % k)
        self.assertGreater(a["dist"], 100 * a["ch"], "…more than a hundred screens from the bottom: %r" % k)
        if self.END_AT["way"] == "press":
            self.assertTrue(a["bodyFocused"] and not a["anchor"] and a["pressIn"], "…with focus on the page body, no anchor and the last press on the element: %r" % k)
        else:
            self.assertTrue(a["focused"] and not a["anchor"] and not a["pressInContent"], "…with focus on the element, no anchor and no press in the transcript: %r" % k)


class LongHistoryEndAfterAPressOnAReplysMenu(_EndAt, LongHistoryEnd):
    END_MARKUP = '<div id="lab-end-menu" role="menu">The note menu: open, rename or delete a note.</div>'
    END_AT = {"sel": '[id="user-content-lab-end-menu"]', "way": "press", "intoView": True}


class LongHistoryEndAfterAPressOnAReplysListbox(_EndAt, LongHistoryEnd):
    END_MARKUP = '<div id="lab-end-listbox" role="listbox">The note list: notes one, two and three.</div>'
    END_AT = {"sel": '[id="user-content-lab-end-listbox"]', "way": "press", "intoView": True}


class LongHistoryEndWithFocusOnALinkInAReplysListbox(_EndAt, LongHistoryEnd):
    END_MARKUP = '<div role="listbox">The note list: <a id="lab-end-link" href="https://example.test/note">a note link</a>.</div>'
    END_AT = {"sel": '[id="user-content-lab-end-link"]', "way": "focus", "intoView": True}


class LongHistoryEndWithFocusOnACopyButtonInAReplysListbox(_EndAt, LongHistoryEnd):
    END_MARKUP = '<div id="lab-list" role="listbox">The note list:\n\n```\nfenced block inside the listbox\n```\n\n</div>'
    END_AT = {"sel": '[id="user-content-lab-list"] pre.has-copy > .code-copy', "way": "focus", "intoView": True}


class LongHistoryEndAfterAPressOnAReplysTaskCheckbox(_EndAt, LongHistoryEnd):
    END_MARKUP = "- [ ] an open task item\n- [x] a done task item"
    END_AT = {"sel": "#content .md li input", "way": "press", "intoView": True}


class LongHistoryEndWithFocusOnAQuestionCardCheckbox(_EndAt, LongHistoryEnd):
    END_AT = {"sel": "#live-ask .ask-check input", "way": "focus", "ask": ASK_CARD}


class _AMediaElement(_EndAt):
    """END_AT on a reply's audio or video, focused (mixed in ahead of LongHistoryEnd); MEDIA_STATE checks its state at the press."""
    END_MEDIA = True
    END_AT = {"way": "focus", "intoView": True}   # each class adds the selector of its own element's id

    def _assert_the_click(self, k):
        super()._assert_the_click(k)
        m = k["atEnd"]["media"]
        self.assertTrue(m is not None and all(m[x] == v for x, v in self.MEDIA_STATE.items()), "…a medium with %r: %r" % (self.MEDIA_STATE, m))


class LongHistoryEndWithFocusOnAnAudioThatHasEnded(_AMediaElement, LongHistoryEnd):
    END_MARKUP = '<audio id="lab-end-ended" controls src="https://media.test/clip.wav"></audio>'
    END_AT = dict(_AMediaElement.END_AT, sel='[id="user-content-lab-end-ended"]', media="ended")
    MEDIA_STATE = {"ended": True, "duration": 3}


class LongHistoryEndWithFocusOnAnAudioThatFailedToLoad(_AMediaElement, LongHistoryEnd):
    END_MARKUP = '<audio id="lab-end-failed" controls src="https://media.test/bad.wav"></audio>'
    END_AT = dict(_AMediaElement.END_AT, sel='[id="user-content-lab-end-failed"]', media="failed")
    MEDIA_STATE = {"error": 4, "readyState": 0, "duration": None}


class LongHistoryEndWithFocusOnAnAudioThatPreloadsNothing(_AMediaElement, LongHistoryEnd):
    END_MARKUP = '<audio id="lab-end-preload" controls preload="none" src="https://media.test/clip.wav"></audio>'
    END_AT = dict(_AMediaElement.END_AT, sel='[id="user-content-lab-end-preload"]')
    MEDIA_STATE = {"error": None, "readyState": 0, "duration": None, "networkState": 1}


class LongHistoryEndWithFocusOnAnAudioWithNoSource(_AMediaElement, LongHistoryEnd):
    END_MARKUP = '<audio id="lab-end-nosource" controls></audio>'
    END_AT = dict(_AMediaElement.END_AT, sel='[id="user-content-lab-end-nosource"]')
    MEDIA_STATE = {"error": None, "readyState": 0, "duration": None, "networkState": 0}


class LongHistoryEndWithFocusOnAReplysVideoWithNoControls(_AMediaElement, LongHistoryEnd):
    END_MARKUP = '<video id="lab-end-video" tabindex="0" src="https://media.test/clip.wav"></video>'
    END_AT = dict(_AMediaElement.END_AT, sel='[id="user-content-lab-end-video"]', media="loaded")
    MEDIA_STATE = {"controls": False, "error": None, "duration": 3, "ended": False}


class LongHistoryEndFromAReplysAudioPlayControl(LongHistoryEnd):
    """The page does not receive End from a reply's audio play control, and the transcript's own scroll does not reach its end while
    focus is there."""
    END_MEDIA = True
    END_MARKUP = ('<a href="https://example.test/pre-audio">pre audio</a>\n\n<audio id="lab-end-play" controls src="https://media.test/clip.wav"></audio>\n\n'
                  '<div role="menu">media menu:\n\n<a href="https://example.test/pre-menu-audio">pre menu audio</a>\n\n'
                  '<audio controls src="https://media.test/clip.wav"></audio>\n\n</div>')
    END_AT = {"sel": '[id="user-content-lab-end-play"]', "way": "tab", "tab": 2, "pre": "https://example.test/pre-audio", "intoView": True,
              "media": "loaded", "scrolls": True}
    test_the_first_end_from_far_back_shows_the_newest_row = None

    def test_the_page_does_not_receive_end_and_the_scroll_does_not_reach_its_end_while_focus_is_on_the_audio(self):
        e = self._result()["end"]
        self.assertEqual([len(x["asked"]) for x in e["steps"]], [1] * self.END_PAGES, "the wheel climbs %d pages, one asked per step: %r" % (self.END_PAGES, e["steps"]))
        k = e["clicked"]
        s, a = k["staged"], k["armed"]
        self.assertTrue(k["picked"] and s["inContent"] and "button:play" in (k["ax"] or []) and a["connected"] and a["focused"],
                        "focus on the play control of a reply's audio in the transcript at the press: %r" % k)
        self.assertTrue(s["media"]["readyState"] >= 1 and s["media"]["duration"] == 3, "…its clip loaded: %r" % k)
        self.assertGreater(a["dist"], 100 * a["ch"], "…more than a hundred screens from the bottom: %r" % k)
        self.assertEqual(a["others"], [{"tag": "AUDIO", "inMenu": True, "controls": True, "focused": False}],
                         '…not on the reply\'s second audio, which has controls and sits in its role="menu" element: %r' % k)
        self.assertIsNone(k["atEnd"], "End does not reach the page: %r" % k)
        ev = k["scrolls"]
        self.assertTrue(ev and ev[0]["inMedia"], "the browser scrolls the transcript, focus in a media element at its first scroll event: %r" % ev)
        self.assertFalse(k["inMediaAtRead"], "focus had left the audio when the scroll events were read: %r" % ev)
        self.assertEqual([x for x in ev if x["inMedia"] and abs(x["st"] + x["ch"] - x["sh"]) <= 1], [],
                         "no scroll event at the transcript's end comes while focus is on the audio: %r" % ev)


class _FamilyA(_EndFamily):
    """The 2400-line tail with no tool rows. The question card's class runs last: the card stays on the page."""
    TALL_LINES = 2400
    END_MEDIA = True
    MEMBERS = (LongHistoryEnd, LongHistoryEndWithNoSelectionAfterAClickAndAPage, LongHistoryEndAfterAPressOnAReplysMenu,
               LongHistoryEndAfterAPressOnAReplysListbox, LongHistoryEndWithFocusOnALinkInAReplysListbox,
               LongHistoryEndWithFocusOnACopyButtonInAReplysListbox, LongHistoryEndAfterAPressOnAReplysTaskCheckbox,
               LongHistoryEndWithFocusOnAnAudioThatHasEnded, LongHistoryEndWithFocusOnAnAudioThatFailedToLoad,
               LongHistoryEndWithFocusOnAnAudioThatPreloadsNothing, LongHistoryEndWithFocusOnAnAudioWithNoSource,
               LongHistoryEndWithFocusOnAReplysVideoWithNoControls, LongHistoryEndFromAReplysAudioPlayControl,
               LongHistoryEndWithFocusOnAQuestionCardCheckbox)


class _FamilyB(_EndFamily):
    """The 2400-line tail with a Bash call in each short turn (HISTORY_TOOLS)."""
    TALL_LINES = 2400
    HISTORY_TOOLS = True
    MEMBERS = (LongHistoryEndAfterAFoldToggle, LongHistoryEndAfterAFoldToggleAndAPage)


class _FamilyC(_EndFamily):
    """The 1800-line tail with a Bash call in each short turn: a tail the base lays out under the layout limit, so the typing class
    fails there for the routing, not the height."""
    TALL_LINES = 1800
    HISTORY_TOOLS = True
    MEMBERS = (LongHistoryEndAfterTypingAndAFoldToggle, LongHistoryEndAfterTypingAFoldToggleAndASelectionOnTheStrip,
               LongHistoryEndAfterAFoldToggleAndSelectAll, LongHistoryEndAfterAFoldToggleAndACommentBoxClosedWithEscape)


class _FamilyD(_EndFamily):
    """Family C's tail with every eighth short turn's Bash output 200 lines long (LONG_OUTPUT) and classic scrollbars (BARS)."""
    TALL_LINES = 1800
    HISTORY_TOOLS = True
    LONG_OUTPUT = True
    BARS = True
    MEMBERS = (LongHistoryEndAfterACaretInAToolOutputBoxAndAShiftClickOutsideIt, LongHistoryEndAfterAClickOutsideAToolOutputBoxAndAFindInsideIt,
               LongHistoryEndAfterACaretInAToolOutputBoxAFoldToggleAndTextWrittenIntoTheMessageBox,
               LongHistoryEndAfterACaretInAToolOutputBoxAndAFoldToggle)


FAMILIES = (_FamilyA, _FamilyB, _FamilyC, _FamilyD)
FAMILY_OF = {c: f for f in FAMILIES for c in f.MEMBERS}


def _check_families():
    """Every End class of this module runs in exactly one family, whose fixture is its own: the class's SCALE, VIEWPORT, TALL_LINES,
    HISTORY_TOOLS, LONG_OUTPUT, BARS and LEGS equal its family's, and a family serves the clips when one of its classes needs them.
    Raised at import, so a class added outside the families, or into one whose fixture is not its own, stops the collection."""
    ends = {c for c in globals().values() if isinstance(c, type) and issubclass(c, LongHistoryEnd)}
    listed = [c for f in FAMILIES for c in f.MEMBERS]
    if len(listed) != len(set(listed)) or set(listed) != ends:
        raise AssertionError("each End class runs in one family: listed %r, defined %r" % (sorted(c.__name__ for c in listed), sorted(c.__name__ for c in ends)))
    for f in FAMILIES:
        for c in f.MEMBERS:
            for a in ("SCALE", "VIEWPORT", "TALL_LINES", "HISTORY_TOOLS", "LONG_OUTPUT", "BARS", "LEGS"):
                if getattr(c, a) != getattr(f, a):
                    raise AssertionError("%s's %s is %r, its family %s's %r" % (c.__name__, a, getattr(c, a), f.__name__, getattr(f, a)))
        if f.END_MEDIA != any(c.END_MEDIA for c in f.MEMBERS):
            raise AssertionError("%s serves the clips (END_MEDIA %r) exactly when one of its classes needs them" % (f.__name__, f.END_MEDIA))
        if f.END_PAGES != max(c.END_PAGES for c in f.MEMBERS):
            raise AssertionError("%s climbs the most pages one of its classes reads" % f.__name__)


_check_families()


if __name__ == "__main__":
    unittest.main()
