#!/usr/bin/env python3
"""The needs-you box lab's writes to the kernel's goal store, run without a browser (a CI red on main, 2026-09-24).

The lab's driver (tests/test_needs_you_box_chat_served.py, DRIVER) edits the goal store between its scenes. It used to rewrite the
file in place, and an in-place write truncates the file before it fills it, so for an instant the store is EMPTY. A kernel read in
that instant finds bytes that do not parse: the loader moves the store aside as corrupt (judge.py _quarantine_store) and starts the
session fresh, the driver's bytes then land in the moved-aside file, the kernel's next save publishes the fresh store at the path, and
the driver's next edit finds its seeded question gone. On CI that read as all thirty of the lab's tests failing on one TypeError. The
driver now publishes (a temp file and a rename: PUBLISH_JSON_JS in tests/test_ship_reship_served.py), and its rename is a
compare-and-swap on the file's identity.

The served lab cannot open that window on demand, so this module runs the driver's own writer, cut out of the driver's source, under
node, against the kernel's own loader and saver (load_goals, save_goals) in a private state root, and HOLDS the writer at the one
moment that matters: after the file it writes is opened (an in-place write has emptied the store by then) and before a byte of it is
written. At each hold the test acts as the kernel, then lets the writer go:
  - a kernel READ there finds the whole store, and nothing is moved aside;
  - a kernel PUBLICATION there is kept: the writer's rename stands down and its edit goes round again on the new bytes;
  - a store that moves under every round ends in a named error after twenty rounds, nothing published and no temp left;
  - a node gone from the store is named (the store's revision, its node ids, any store moved aside), never a bare TypeError.
NoInPlaceWrites pins the source: neither driver that writes a file the kernel reads writes one in place, and every scene edit of the
needs-you driver reads its node through nodeOf. TornStoreChain walks the kernel half of the CI red step by step; it describes the
kernel, so it passes before the fix and after it alike.

Needs node (the lab's own runtime) and skips without it. Synthetic only: a private placeholder sid, invented text."""
import ast
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

BOX = os.path.join(HERE, "test_needs_you_box_chat_served.py")          # the lab whose writer this runs
LAB = os.path.join(HERE, "test_ship_reship_served.py")                  # the shared publisher's home
REOPEN = os.path.join(HERE, "test_comment_reopen_reload_browser.py")    # the other lab that writes a store the kernel reads

SID = "abababab-4444-5555-6666-777777777777"   # this module's own placeholder sid: no other module's journal can reach it
G1, G6, G7, G9 = (SID + ":g%d" % i for i in (1, 6, 7, 9))
T0 = 1781100000
NEW_BRIEF = "the CI job runs the Postgres loader first and the SQLite loader against its fixtures"
ASIDE = SID + ".json.corrupt-20260101T000000Z"   # a store the kernel moved aside earlier, named as its quarantine names one
# node's writers that open a file and fill it in place (truncating it first, or filling it in steps): any of them on a file the kernel reads
# reopens the window the CI red fell into, so NoInPlaceWrites looks for each, not only the one the driver happened to use
IN_PLACE_WRITES = ("fs.writeFileSync(", "fs.writeFile(", "fs.promises.writeFile(", "fs.truncateSync(", "fs.createWriteStream(")
# a scene edit that reaches a node without nodeOf (`st.nodes[cfg.g6].blockSummary = ...`); the lookahead leaves out a node's creation
# (`st.nodes[cfg.g6c] = {`), the only other shape the driver writes
BARE_NODE = re.compile(r"st\.nodes\[cfg\.\w+\](?!\s*=\s*\{)")


def _read_const(path, name):
    """The string constant `name` assigned at the top level of the module at `path`, or None when it assigns none. Read by AST: the
    served modules are never imported here (their setup builds bundles and boots kernels), and the base of a change that adds the
    constant reads as None rather than an ImportError."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
                and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)):
            return stmt.value.value
    return None


def _block(src, name):
    """`const <name> = ` at the start of a line of `src` through the first line after it that is `};` alone: one top-level arrow
    function of a driver, or None when the driver defines none."""
    i = src.find("\nconst %s = " % name)
    if i < 0:
        return None
    j = src.find("\n};\n", i)
    return src[i + 1:j + 4] if j >= 0 else None


def _node(nid, text, t):
    return {"id": nid, "text": text, "parentId": None, "nodeComplete": False, "blocked": True, "blockWhy": text, "cleared": False,
            "trail": [], "t": t, "log": [{"ev_t": t, "src": "planner", "kind": "block", "why": "asked: " + text, "at": t}]}


def _seed():
    """The store as a scene of the lab finds it before a write: the lab's first question and the one its hidden-pane scene seeds,
    with the revision and sequence a few publications left."""
    return {"rompUuid": SID, "seq": 5, "rev": 3, "lastNode": G1, "closedTurns": [], "placements": {},
            "nodes": {G1: _node(G1, "which database does the suite target?", T0),
                      G6: _node(G6, "which of the two fixture loaders should the CI job run first?", T0 + 10)},
            "status": {G1: "blocked", G6: "blocked"}}


# The node side of the run. The driver's writer and what it needs from the driver (the shared publisher, nodeOf) are spliced in at
# CODE; kernelFeed, the driver's GET /feed.json read before a write, answers null since no kernel serves here. THE HOLD: a write of a
# file in the goals directory opens the file (an in-place write empties the store right there), says HELD on stdout and reads the
# test's word on stdin before a byte goes in; every other write passes through. The edit is the lab's own shape: a brief and its
# family stamp on an existing node, or (`missing`) a node read through nodeOf that the store does not hold.
HARNESS = r"""
import fs from "node:fs";
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
const kernelFeed = async () => null;
const say = (kind, o) => fs.writeSync(1, kind + " " + JSON.stringify(o) + "\n");
const passWrite = fs.writeFileSync;
fs.writeFileSync = (p, data, ...rest) => {
  if (typeof p !== "string" || !p.startsWith(cfg.goalsDir + "/")) return passWrite(p, data, ...rest);
  const fd = fs.openSync(p, "w");
  say("HELD", { path: p });
  fs.readSync(0, Buffer.alloc(16), 0, 16, null);
  try { fs.writeSync(fd, String(data)); } finally { fs.closeSync(fd); }
};
/*CODE*/
try {
  const edit = cfg.missing ? ((st) => { nodeOf(st, cfg.node).blockSummary = cfg.brief; })
                           : ((st) => { st.nodes[cfg.node].blockSummary = cfg.brief; st.nodes[cfg.node].briefedMt = cfg.stamp; });
  say("DONE", { floor: await writeStore(edit) });
} catch (e) { say("ERROR", { message: String((e && e.message) || e) }); }
"""


@unittest.skipUnless(shutil.which("node"), "node absent here: the lab's writer runs under node")
class LabStoreWriter(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        (root / "session-hosts").write_text("off\n")   # this module mints its own state root; nothing here starts a host either way
        jd._rebind_state(root)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.store = jd.GOALDIR / (SID + ".json")
        self.store.write_text(json.dumps(_seed()))   # whole, as the lab's setUpClass writes its seed before the kernel boots
        self.order = root / "session-order.json"
        self.order.write_text(json.dumps([SID]))     # the file the writer touches after a publish, as in the lab

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _aside(self):
        return sorted(n for n in os.listdir(jd.GOALDIR) if ".corrupt-" in n)

    def _temps(self):
        return sorted(n for n in os.listdir(jd.GOALDIR) if ".tmp." in n)

    def _read(self):
        """The kernel's READ at a hold: its loader, with the line a move-aside writes to stderr kept."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = jd.load_goals(SID)
        return {"nodes": sorted(s["nodes"]), "aside": self._aside(), "stderr": err.getvalue().strip()}

    def _publish(self, nid, text):
        """A kernel PUBLICATION at a hold: its own loader and saver (a temp file and a rename), one new card."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = jd.load_goals(SID)
            s["nodes"][nid] = _node(nid, text, T0 + 60)
            s["status"][nid] = "blocked"
            jd.save_goals(SID, s)
        return {"kernelRev": jd._disk_rev(SID), "stderr": err.getvalue().strip()}

    def _drive(self, at_hold, **cfg):
        """Run the driver's writer once under node. At each hold, `at_hold(n, path)` acts as the kernel (n counts from 0) and its record
        is kept; then the writer goes on. Returns (holds, end, other): the records, ("DONE", {...}) or ("ERROR", {...}) as the writer
        ended, and anything else it printed."""
        driver = _read_const(BOX, "DRIVER")
        write_store = _block(driver, "writeStore")
        self.assertIsNotNone(write_store, "the lab's writer is cut out of its driver's source")
        code = "\n".join((_read_const(LAB, "PUBLISH_JSON_JS") or "", _block(driver, "nodeOf") or "", write_store))
        script = Path(self.td.name, "writer.mjs")
        script.write_text(HARNESS.replace("/*CODE*/", code))
        conf = Path(self.td.name, "writer.json")
        conf.write_text(json.dumps(dict({"goalsDir": str(jd.GOALDIR), "store": str(self.store), "order": str(self.order),
                                         "node": G6, "brief": NEW_BRIEF, "stamp": T0 + 100, "missing": False}, **cfg)))
        p = subprocess.Popen(["node", str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=dict(os.environ, CFG=str(conf)))
        guard = threading.Timer(120, p.kill)   # a hang guard only: every step below waits on a line the writer prints
        guard.start()
        holds, end, other = [], None, []
        try:
            for line in iter(p.stdout.readline, ""):
                kind, _, rest = line.rstrip("\n").partition(" ")
                if kind == "HELD":
                    rec = {"path": json.loads(rest)["path"]}
                    rec.update(at_hold(len(holds), rec["path"]) or {})
                    holds.append(rec)
                    p.stdin.write("go\n")
                    p.stdin.flush()
                elif kind in ("DONE", "ERROR") and end is None:
                    end = (kind, json.loads(rest))
                else:
                    other.append(line)
            p.wait()
        finally:
            guard.cancel()
            if p.poll() is None:
                p.kill()
                p.wait()
            for pipe in (p.stdin, p.stdout):
                with contextlib.suppress(OSError):   # a writer that died leaves a broken pipe to close
                    pipe.close()
        return holds, end, "".join(other)[-2000:]

    def test_a_kernel_read_in_the_middle_of_the_labs_write_finds_the_whole_store_and_moves_nothing_aside(self):
        """The CI red's own window: the kernel reads the store while the lab's write is open. The in-place write had emptied the store
        by then, so the loader moved it aside and read a fresh store; the published write leaves the store whole until its rename."""
        holds, end, other = self._drive(lambda n, path: self._read())
        self.assertEqual(end and end[0], "DONE", "the writer finished: %r %s" % (end, other))
        self.assertTrue(holds, "premise: the writer's write into the goals directory was held: %s" % other)
        for h in holds:
            self.assertEqual((G6 in h["nodes"], h["aside"]), (True, []), "a kernel read while the lab's write is open finds the whole "
                             "store and moves nothing aside (before: the in-place write had emptied it, and the loader moved it aside as "
                             "corrupt and read a fresh store): %r" % h)
            self.assertNotEqual(h["path"], str(self.store), "the bytes go to a temp file beside the store, never into the store: %r" % h)
        final = jd.load_goals(SID)
        self.assertEqual((final["nodes"].get(G6) or {}).get("blockSummary"), NEW_BRIEF, "the rename published the edit")
        self.assertEqual((self._aside(), self._temps()), ([], []), "nothing moved aside and no temp left beside the store")

    def test_a_kernel_publication_between_the_labs_read_and_its_rename_is_kept_and_the_edit_goes_round_again(self):
        """A kernel writer publishes (its own temp file and rename) after the lab read the store and before the lab's rename. The lab's
        rename stands down on the moved identity and its edit goes round again on the kernel's bytes, so both land. A rename without the
        check would publish the bytes the lab read over the kernel's card."""
        holds, end, other = self._drive(lambda n, path: self._publish(G7, "which fixture owns the port the api listens on?") if n == 0 else {})
        self.assertEqual(end and end[0], "DONE", "the writer finished: %r %s" % (end, other))
        final = jd.load_goals(SID)
        self.assertEqual((G7 in final["nodes"], (final["nodes"].get(G6) or {}).get("blockSummary"), self._aside()), (True, NEW_BRIEF, []),
                         "the kernel's card published during the lab's write stands, the lab's edit landed on top of it, and nothing was "
                         "moved aside (before: the in-place write left the kernel a torn store, and the edit went into the moved-aside "
                         "file): nodes %r, holds %r" % (sorted(final["nodes"]), holds))
        self.assertEqual(len(holds), 2, "the first round stood down at its rename and the second published: %r" % holds)
        self.assertEqual(final.get("rev"), holds[0]["kernelRev"] + 1, "one revision past the kernel's publication")
        self.assertEqual(self._temps(), [], "the stood-down round's temp was removed")

    def test_a_store_that_moves_under_every_round_ends_in_a_named_error_with_nothing_published(self):
        """Bounded: a kernel publication at every hold stands every round down, and the twentieth ends the write in a named error, the
        edit unpublished, the kernel's last card standing and no temp left behind (never an unbounded loop, never a blind rename)."""
        holds, end, other = self._drive(lambda n, path: self._publish("%s:k%d" % (SID, n), "kernel card %d" % n))
        self.assertEqual(end and end[0], "ERROR", "the write ends in an error once every round is overtaken (before: the in-place write "
                         "went ahead): %r %s" % (end, other))
        self.assertIn("store-write-contended", end[1]["message"], "named: %r" % end[1])
        self.assertEqual(len(holds), 20, "twenty rounds: %d" % len(holds))
        final = jd.load_goals(SID)
        self.assertNotEqual((final["nodes"].get(G6) or {}).get("blockSummary"), NEW_BRIEF, "the edit was not published")
        self.assertIn(SID + ":k19", final["nodes"], "the kernel's last publication stands")
        self.assertEqual((self._aside(), self._temps()), ([], []), "nothing moved aside and no temp left beside the store")

    def test_a_node_gone_from_the_store_is_named_never_a_bare_type_error(self):
        """The CI red said only 'Cannot set properties of undefined'. A scene's edit reads its node through nodeOf, which names the loss:
        the node, the store's revision and sequence, the nodes it does hold, and any store the kernel moved aside. Nothing is written."""
        self.assertIsNotNone(_block(_read_const(BOX, "DRIVER"), "nodeOf"), "the driver reads the nodes its scenes edit through a guard (nodeOf)")
        (jd.GOALDIR / ASIDE).write_text("")
        holds, end, other = self._drive(lambda n, path: {}, node=G9, missing=True)
        self.assertEqual(end and end[0], "ERROR", "the edit of a node the store lacks ends the write in an error: %r %s" % (end, other))
        msg = end[1]["message"]
        for part in ("store-node-lost", G9, "rev 3", "seq 5", G1, G6, ASIDE):
            self.assertIn(part, msg, "the error names %r: %r" % (part, msg))
        self.assertEqual(holds, [], "nothing was written")
        self.assertNotIn(G9, jd.load_goals(SID)["nodes"])


class NoInPlaceWrites(unittest.TestCase):
    def test_the_labs_write_no_file_the_kernel_reads_in_place(self):
        """Every file these two drivers write for the kernel (the goal store, the judge-auth latch, a comments store) goes through the
        shared publisher; an in-place write anywhere in them, by any of node's in-place writers, reopens the window the CI red fell into.
        And every scene edit of the needs-you driver reads its node through nodeOf, so a lost node is named, never a bare TypeError again."""
        for path in (BOX, REOPEN):
            name = os.path.basename(path)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            in_place = [ln.strip()[:160] for ln in _read_const(path, "DRIVER").splitlines() if any(m in ln for m in IN_PLACE_WRITES)]
            self.assertEqual(in_place, [], "%s: its driver writes no file in place" % name)   # the lines themselves, never the whole driver
            self.assertTrue("_lab.PUBLISH_JSON_JS + " in src, "%s: its driver is written with the shared publisher in front" % name)
        self.assertTrue("publishJson(cfg.judgeAuth, " in _read_const(BOX, "DRIVER"), "the judge-auth seed is published too")
        bare = [ln.strip()[:160] for ln in _read_const(BOX, "DRIVER").splitlines() if BARE_NODE.search(ln)]
        self.assertEqual(bare, [], "every scene edit reads the node it edits through nodeOf, so a lost node is named")


class TornStoreChain(unittest.TestCase):
    """The kernel half of the CI red, one step at a time, with the kernel's own loader and saver. It describes the kernel, which this
    change leaves alone, so it passes before the fix and after it; it is what explains why the driver met a missing node (a TypeError)
    and not a missing file."""

    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        (root / "session-hosts").write_text("off\n")
        jd._rebind_state(root)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.store = jd.GOALDIR / (SID + ".json")

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def test_an_emptied_store_is_moved_aside_the_bytes_land_there_and_the_next_save_publishes_a_store_without_the_node(self):
        full = json.dumps(_seed())
        self.store.write_text(full)
        fd = os.open(self.store, os.O_WRONLY | os.O_TRUNC)   # the in-place write's first step: the file opened and emptied
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                fresh = jd.load_goals(SID)
            self.assertEqual(fresh["nodes"], {}, "1. a load in that instant reads a fresh store")
            self.assertFalse(self.store.exists(), "and the store is moved aside")
            aside = sorted(n for n in os.listdir(jd.GOALDIR) if ".corrupt-" in n)
            self.assertEqual(len(aside), 1, "into one sidecar: %r" % aside)
            self.assertIn("line 1 column 1 (char 0)", err.getvalue(), "the kernel log's words for an empty file, the CI red's line: %r" % err.getvalue())
            os.write(fd, full.encode())   # the write's second step, into the descriptor it opened
        finally:
            os.close(fd)
        self.assertIn(G6, json.loads((jd.GOALDIR / aside[0]).read_text())["nodes"], "2. the writer's bytes land in the moved-aside file")
        self.assertFalse(self.store.exists(), "not at the store's path")
        jd.save_goals(SID, fresh)
        after = json.loads(self.store.read_text())
        self.assertNotIn(G6, after["nodes"], "3. the kernel's next save publishes the fresh store: the path holds a store without the "
                         "node, so the driver's next edit met a missing node, not a missing file: %r" % sorted(after["nodes"]))


if __name__ == "__main__":
    unittest.main()
