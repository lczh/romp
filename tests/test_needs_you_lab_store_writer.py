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
  - a store REWRITTEN IN PLACE there, at the inode the writer read, is kept the same way (the identity's modification time and size,
    which tell two files at one inode apart, as they must on ext4, where a freed inode comes back);
  - a store that moves under every round ends in a named error after twenty rounds, nothing published and no temp left;
  - a node gone from the store is named (the store's revision, its node ids, any store moved aside), never a bare TypeError.
One more test holds the writer earlier, right after it opens the store, and publishes there as the kernel: the edit is kept, because the
identity comes from that descriptor, not from the path.
NoInPlaceWrites pins the source, by the spellings its constants list: no driver that writes a file the kernel reads (the needs-you
box's, the comment reopen's, the tab badge's) writes one in place, each such write is a publishJson call, and every scene edit of the
needs-you driver reads its node through nodeOf.
TornStoreChain walks the kernel half of the CI red step by step; it describes the kernel, so it passes before the fix and after it
alike.
TabBadgeLabClears replays the tab-badge lab's clears against a judge stage that held the store across them (a second CI red on main,
2026-10-18): a clear the lab writes must survive that stage's save.

The writer's runs need node (the lab's own runtime) and skip without it. Synthetic only: private placeholder sids, invented text."""
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
TAB_BADGE = os.path.join(HERE, "test_tab_badge_browser.py")            # and the tab-badge lab, which clears needs-you cards in its store

SID = "abababab-4444-5555-6666-777777777777"   # this module's own placeholder sid: no other module's journal can reach it
TB_SID = "cdcdcdcd-4444-5555-6666-777777777777"   # and TabBadgeLabClears' own
G1, G6, G7, G9 = (SID + ":g%d" % i for i in (1, 6, 7, 9))
T0 = 1781100000
NEW_BRIEF = "the CI job runs the Postgres loader first and the SQLite loader against its fixtures"
ASIDE = SID + ".json.corrupt-20260101T000000Z"   # a store the kernel moved aside earlier, named as its quarantine names one
# node's writers that open a file and fill it in place (truncating it first, or filling it in steps): any of them on a file the kernel reads
# reopens the window the CI red fell into, so NoInPlaceWrites looks for each, not only the one the driver happened to use. It matches these
# exact spellings on a line of a driver: writeFile (sync, callback and promise), truncate and cp in the same three forms, copyFile in the
# same three (copyFile truncates the target and keeps its inode; cp puts a new file at the path instead), createWriteStream, and
# TRUNCATING_OPEN below. A writer reached through an alias of fs is not matched, and an "r+" open and a numeric flag are not matched.
IN_PLACE_WRITES = ("fs.writeFileSync(", "fs.writeFile(", "fs.promises.writeFile(", "fs.truncateSync(", "fs.truncate(", "fs.promises.truncate(",
                   "fs.createWriteStream(", "fs.copyFileSync(", "fs.copyFile(", "fs.promises.copyFile(", "fs.cpSync(", "fs.cp(",
                   "fs.promises.cp(")
# an open that truncates, to be followed by a write (the harness's own in-place shape): fs.openSync, fs.open or fs.promises.open with the
# flag "w" or "w+". It keys on the flag, since a bare `fs.openSync(` would match the writer's own read-only open of the store too.
TRUNCATING_OPEN = re.compile(r"""fs\.(?:openSync|open|promises\.open)\([^;]*?,\s*(["'`])w\+?\1""")
# a scene edit that reaches a node without nodeOf: `st.nodes[<key>]` or `st["nodes"][<key>]`, the key any expression with at most one
# level of brackets in it (cfg.g6, cfg["g6"], a string literal). The lookahead leaves out a node's creation (`st.nodes[cfg.g6c] = {`), the
# only other shape the driver writes. A node reached through an alias (`const ns = st.nodes; ns[id]`) is not matched.
BARE_NODE = re.compile(r"""\bst(?:\.nodes|\[(["'`])nodes\1\])\[(?:[^\[\]]|\[[^\[\]]*\])*\](?!\s*=\s*\{)""")


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


def _lab_funcs(path, *names):
    """The top-level functions `names` of the module at `path`, compiled alone with json in scope (the served module is never imported,
    as _read_const says), in the order asked; None for a name the module does not define."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    ns = {"json": json}
    defs = [stmt for stmt in tree.body if isinstance(stmt, ast.FunctionDef) and stmt.name in names]
    exec(compile(ast.Module(body=defs, type_ignores=[]), path, "exec"), ns)
    return [ns.get(n) for n in names]


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
# CODE; kernelFeed, the driver's GET /feed.json read before a write, answers null since no kernel serves here. THE HOLDS, chosen by
# cfg.holdAt: at "write" (the default), a write of a file in the goals directory opens the file (an in-place write empties the store
# right there), says HELD on stdout and reads the test's word on stdin before a byte goes in; at "open", the writer's open of the store
# says HELD and waits right after the descriptor comes back. Every other write and open passes through. The edit is the lab's own shape:
# a brief and its family stamp on an existing node, or (`missing`) a node read through nodeOf that the store does not hold.
HARNESS = r"""
import fs from "node:fs";
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
const kernelFeed = async () => null;
const say = (kind, o) => fs.writeSync(1, kind + " " + JSON.stringify(o) + "\n");
const hold = (at, o) => { say("HELD", Object.assign({ at }, o)); fs.readSync(0, Buffer.alloc(16), 0, 16, null); };
const passWrite = fs.writeFileSync;
fs.writeFileSync = (p, data, ...rest) => {
  if (!cfg.holdAt.includes("write") || typeof p !== "string" || !p.startsWith(cfg.goalsDir + "/")) return passWrite(p, data, ...rest);
  const fd = fs.openSync(p, "w");
  hold("write", { path: p });
  try { fs.writeSync(fd, String(data)); } finally { fs.closeSync(fd); }
};
const passOpen = fs.openSync;
fs.openSync = (p, ...rest) => {
  const fd = passOpen(p, ...rest);
  if (cfg.holdAt.includes("open") && p === cfg.store) hold("open", { path: p });
  return fd;
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

    def _rewrite_in_place(self, nid, text):
        """A store rewritten IN PLACE at a hold: one new card and the revision advanced, written into the file the path already names
        (an open that truncates, then a write, as in TornStoreChain), so the inode stays and the size moves. It stands for two
        publications between the writer's read and its rename that bring the inode back, as ext4 can: a change the inode alone misses."""
        st = json.loads(self.store.read_text())
        st["nodes"][nid] = _node(nid, text, T0 + 60)
        st["status"][nid] = "blocked"
        st["rev"] = st.get("rev", 0) + 1
        before = os.stat(self.store)
        with open(self.store, "w", encoding="utf-8") as f:
            f.write(json.dumps(st))
        after = os.stat(self.store)
        return {"sameInode": before.st_ino == after.st_ino, "sizeMoved": before.st_size != after.st_size, "rewrittenRev": st["rev"]}

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
                                         "node": G6, "brief": NEW_BRIEF, "stamp": T0 + 100, "missing": False, "holdAt": ["write"]}, **cfg)))
        p = subprocess.Popen(["node", str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=dict(os.environ, CFG=str(conf)))
        guard = threading.Timer(120, p.kill)   # a hang guard only: every step below waits on a line the writer prints
        guard.start()
        holds, end, other = [], None, []
        try:
            for line in iter(p.stdout.readline, ""):
                kind, _, rest = line.rstrip("\n").partition(" ")
                if kind == "HELD":
                    info = json.loads(rest)
                    rec = {"at": info["at"], "path": info.get("path")}
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

    def test_a_store_rewritten_in_place_at_the_inode_the_lab_read_is_kept_and_the_edit_goes_round_again(self):
        """The identity is the inode, the modification time in nanoseconds and the size, and the last two are what tell apart two files
        at one inode: on ext4 a freed inode comes back, so two kernel publications between the lab's read and its rename can leave the
        store at the inode the lab read. The store is rewritten in place at the hold, which keeps its inode on any filesystem and moves
        its size: the lab's rename stands down and its edit goes round again on the rewritten bytes, so both land. An identity of the
        inode alone would publish the bytes the lab read over the rewrite."""
        holds, end, other = self._drive(lambda n, path: self._rewrite_in_place(G7, "which fixture owns the port the api listens on?") if n == 0 else {})
        self.assertEqual(end and end[0], "DONE", "the writer finished: %r %s" % (end, other))
        self.assertTrue(holds and (holds[0]["sameInode"], holds[0]["sizeMoved"]) == (True, True),
                        "premise: the rewrite kept the store's inode and moved its size: %r" % holds)
        final = jd.load_goals(SID)
        self.assertEqual((G7 in final["nodes"], (final["nodes"].get(G6) or {}).get("blockSummary"), self._aside()), (True, NEW_BRIEF, []),
                         "the card written in place at the store's own inode stands and the lab's edit landed on top of it (an identity of "
                         "the inode alone takes the rewritten store for the one the lab read and publishes over it): nodes %r, holds %r"
                         % (sorted(final["nodes"]), holds))
        self.assertEqual(len(holds), 2, "the first round stood down at its rename and the second published: %r" % holds)
        self.assertEqual(final.get("rev"), holds[0]["rewrittenRev"] + 1, "one revision past the rewrite")
        self.assertEqual(self._temps(), [], "the stood-down round's temp was removed")

    def test_a_kernel_publication_between_the_labs_open_and_its_read_is_kept_because_the_identity_is_the_descriptors(self):
        """The identity is taken from the descriptor the bytes are read through. Here the writer is held right after it opens the store,
        before the identity is taken or a byte is read, and the kernel publishes: the writer reads the file it opened, the old one, and
        holds that file's identity, so its rename stands down and the edit goes round again on the kernel's bytes. An identity taken by
        path after the open, before the read or after it, would be the kernel's new file while the bytes are the old one's, and the
        rename would publish them over the kernel's card."""
        holds, end, other = self._drive(lambda n, path: self._publish(G7, "which fixture owns the port the api listens on?") if n == 0 else {},
                                        holdAt=["open"])
        self.assertEqual(end and end[0], "DONE", "the writer finished: %r %s" % (end, other))
        self.assertTrue(holds and all(h["at"] == "open" for h in holds), "premise: the writer was held right after each open of the store: %r"
                        % holds)
        final = jd.load_goals(SID)
        self.assertEqual((G7 in final["nodes"], (final["nodes"].get(G6) or {}).get("blockSummary"), self._aside()), (True, NEW_BRIEF, []),
                         "the kernel's card published between the lab's open and its read stands and the lab's edit landed on top of it (an "
                         "identity taken by path after the open, before the read or after it, names the kernel's file, not the bytes' own, and "
                         "the rename publishes over the card): nodes %r, holds %r" % (sorted(final["nodes"]), holds))
        self.assertEqual(len(holds), 2, "the first round read the old file and stood down, the second read the kernel's: %r" % holds)
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
        """Every file these drivers write for the kernel (the goal store, the judge-auth latch, a comments store) goes through the
        shared publisher; an in-place write anywhere in them reopens the window the CI red fell into. The pin checks the spellings listed
        at IN_PLACE_WRITES and TRUNCATING_OPEN, and that each known write of a kernel-read file is a publishJson call. And every scene
        edit of the needs-you driver reads its node through nodeOf (the spellings at BARE_NODE), so a lost node is named, never a bare
        TypeError again."""
        spellings = ", ".join(IN_PLACE_WRITES) + ' and an fs open with the flag "w" or "w+"'
        for path in (BOX, REOPEN, TAB_BADGE):
            name = os.path.basename(path)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            in_place = [ln.strip()[:160] for ln in _read_const(path, "DRIVER").splitlines()
                        if any(m in ln for m in IN_PLACE_WRITES) or TRUNCATING_OPEN.search(ln)]
            # the lines themselves, never the whole driver
            self.assertEqual(in_place, [], "%s: its driver writes no file in place (the spellings checked: %s) (an alias of fs is not matched)"
                             % (name, spellings))
            self.assertTrue("_lab.PUBLISH_JSON_JS + " in src, "%s: its driver is written with the shared publisher in front" % name)
        self.assertTrue("publishJson(cfg.judgeAuth, " in _read_const(BOX, "DRIVER"), "the judge-auth seed is published too")
        self.assertTrue("publishJson(cfg.store, " in _read_const(REOPEN, "DRIVER"), "the comment-reopen lab's comments store is published too")
        self.assertTrue("publishJson(cfg.oneGoalPath, " in _read_const(TAB_BADGE, "DRIVER"), "the tab-badge lab's goal store is published too")
        bare = [ln.strip()[:160] for ln in _read_const(BOX, "DRIVER").splitlines() if BARE_NODE.search(ln)]
        self.assertEqual(bare, [], "every scene edit reads the node it edits through nodeOf, so a lost node is named (the spellings checked: "
                         'st.nodes[<key>] and st["nodes"][<key>] other than a node\'s creation) (an alias of st.nodes is not matched)')


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


class TabBadgeLabClears(unittest.TestCase):
    """The tab-badge lab's two clears (tests/test_tab_badge_browser.py: the live leg's 2 -> 1, the keycap leg's 1 -> 0) against a judge
    stage that holds the store across the clear (CI on main, 2026-10-18: "the badge never dropped back to 1 after the card cleared" and
    "the badge never cleared after the card was removed", with the lab bracketed back to before that day's merges).

    The lab kernel runs the real judge tiers, and each write the driver makes to the `one` store sets off a pass whose stages (planner,
    closer, grouper, consolidator, distiller) each load the store and save it. A stage that loaded it before a clear and saves after
    finds the file moved and rebases onto it (save_goals' CAS). The rebase folds in the other writer's diary rows and adopts the nodes
    it minted, but a node merely ABSENT from that writer's file is one it has not seen, never one it deleted: the store's deletions are
    tombstoned (mergedFrom, rewindSwept) and its clears are diary rows. A clear written as a deletion was therefore published back at
    the stage's save, the card with it, and the badge held its count until the driver's 40 s wait ran out. The kernel's own loader and
    saver play the stage here, at the one moment that matters, over the stores the lab writes (its one_stores)."""

    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        (root / "session-hosts").write_text("off\n")
        jd._rebind_state(root)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.store = jd.GOALDIR / (TB_SID + ".json")

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _publish(self, text):
        """The lab's write, as the shared publisher makes it: a temp beside the store and a rename."""
        tmp = self.store.with_name(self.store.name + ".tmp.0.0.1")
        tmp.write_text(text)
        os.replace(tmp, self.store)

    @staticmethod
    def _needs_you(store):
        """The store's needs-you cards: its open root goals that the rollup reads as blocked."""
        status = store.get("status") or {}
        return sorted(nid for nid, nd in (store.get("nodes") or {}).items()
                      if nd.get("parentId") is None and not nd.get("cleared") and status.get(nid) == "blocked")

    def test_a_card_the_lab_clears_stays_cleared_when_a_judge_stage_held_the_store_across_the_clear(self):
        _, one_stores = _lab_funcs(TAB_BADGE, "blocked_store", "one_stores")
        self.assertIsNotNone(one_stores, "the lab builds the stores its driver writes in one_stores")
        stores = one_stores(TB_SID, T0)
        for before, clear, want in (("oneStore2", "oneStore1", 1), ("oneStore1", "oneStore0", 0)):   # the driver's two clears, in its order
            with self.subTest(clear=clear):
                self._publish(stores[before])
                held = jd.load_goals(TB_SID)                 # a stage's load, mid-pass
                self.assertEqual(len(self._needs_you(held)), want + 1, "premise: the stage holds the card the lab is about to clear")
                self._publish(stores[clear])                 # the lab's clear lands
                self.assertEqual(len(self._needs_you(jd.load_goals(TB_SID))), want, "premise: the clear alone reads as the lab means it")
                jd.save_goals(TB_SID, held)                  # the stage's save
                after = self._needs_you(jd.load_goals(TB_SID))
                self.assertEqual(len(after), want, "%s: the card the lab cleared stays cleared through the stage's save (a deletion is "
                                 "published back by the rebase, so the badge never moves): %r" % (clear, after))


if __name__ == "__main__":
    unittest.main()
