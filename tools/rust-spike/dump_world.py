#!/usr/bin/env python3
"""The Rust spike's oracle dumper (step S2, rust/SPIKE.md; the recipe is rust/notes/s2-design.md section 1).

It builds a SYNTHETIC world (the notes-api demo board of tests/bench_connect_push.py, invented prompts, placeholder ids,
host TESTHOST; no real session data), runs Python's feed build over it with the clock frozen, and writes everything the
Rust side needs to rebuild the same bytes:

  snapshot.json           the non-transcript inputs (schema romp-spike-snapshot/1): the alive rows in order, the live map,
                          the board facts, per session its names, flags, store, captions and the recorded helper answers
                          keyed by sid, the peer stores, and the frame keys the per-card build passes through
  trees/<sid>.json        the parse tree the per-card build reads (ctx["ps"]: em.plain_tree, `dir` and `_txKey` dropped)
  transcripts/<sid>.jsonl byte copies of the transcripts, mtimes kept (S3's input); states/<sid>.jsonl likewise
  oracle/feed.canon.json  the feed, json.dumps(sort_keys=True): the S4 gate
  oracle/feed.wire.json   the feed in insertion order (json.dumps defaults, what romp-json reproduces): the stricter check
  oracle/entries/<sid>.json  each session's memoized entry string (clock-free, taken before the fold)
  coverage/full.json      which lines of the per-card functions the design lists ran in the live build
  coverage/port.json      the same over the replay build, where the recorded helpers answer from the snapshot: S4's scope
  manifest.json           hashes and sizes of every file, the world's facts and every self-check's result
  run.json                what differs between two runs by design (root, seed, wall times); --check skips only this file

The self-checks (the dump fails when one fails): a fresh-process replay that rebuilds the feed from the dumped files
alone, against an empty state root, and compares bytes; the guards held (no spawn, no refused write, no thread, no warm
call, no absent session, no notices); a census of the designed card shapes; the boundary atom (79 code points, 80+ UTF-8
bytes) is load-bearing; the comparator catches a planted one-card mutation; and no output carries the world root, the
home directory, the user or the machine's name.

    python3 tools/rust-spike/dump_world.py --world small                 # -> <tmp>/romp-spike/small
    python3 tools/rust-spike/dump_world.py --world large --out DIR
    python3 tools/rust-spike/dump_world.py --check --world both          # two dumps, two hash seeds, two roots: identical
    python3 tools/rust-spike/dump_world.py --replay DIR                  # the fresh-process replay alone

Everything the tool writes lives under the output directory (the world itself under DIR/work, removed after the dump
unless --keep-world), which must sit outside any git work tree. Nothing is installed: stdlib only.
"""
import argparse
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NOW = 1781100000                 # the build clock (an int: a float prints 1781100000.0, the fold types its stamp)
T_MINT = NOW - 1800              # the judges' verdict clock: record_verdict stamps log rows' `at` from time.time()
HOST = "TESTHOST"                # the repo's synthetic host name (CLAUDE.md); feeds the frame's selfHost
FAKE_ROOT = "/TESTDIR"           # the world's directories as the records name them (see relocate_world)
SCHEMA = "romp-spike-snapshot/1"
COVERAGE_SCHEMA = "romp-spike-coverage/1"
MARKER = ".romp-spike-dump"      # an output directory this tool made, and so may clear
RUN_FILE = "run.json"            # the one output that differs between two runs by design
SIZE_CAP = 1 << 30               # nothing over ~1 GB (the spike's rule)

# The two designed sizes (s2-design.md "Sizes"): the four designed sessions, small at 80 records with s001's chat at
# 10 turns; large at 3,000 records with s001 at 24,000 (3,000 chat turns of 8 records) plus 34 fillers of 3,000 records,
# the 38-session bench board of tests/bench_connect_push.py.
WORLDS = {
    "small": {"sessions": 4, "records": 80, "chatTurns": 10, "fillers": 0},
    "large": {"sessions": 4, "records": 3000, "chatTurns": 3000, "fillers": 34},
}
BOUNDARY_TURN = 3                # the chat turn whose segment closes s001's completed g1 (its summary's landing)
# 79 code points, 82 UTF-8 bytes: one short of jd.CITE_MIN_CHARS (80) as Python counts (len, code points), past it as a
# byte count would. It is the last prose atom of g1's closing segment, so a byte-counting port lands g1's summary on it
# instead of on the 95-character wrap-up before it (the mutation self-check proves the field moves).
BOUNDARY_TEXT = "Recap: limit → 3, the naïve cache is gone; the search suite passes, once again."
assert len(BOUNDARY_TEXT) == 79 and len(BOUNDARY_TEXT.encode("utf-8")) >= 80

# The boundary helpers whose answers are recorded by sid (s2-design.md step 9): name -> (snapshot key, how the sid is
# found in the call). Their bodies read raw records, the states logs, the postal log or the SDK registry, which S4 does
# not port; Rust answers them from the snapshot and must fail loudly on a key that is absent (never called by Python).
RECORDED = {
    "_closer_pending": ("closerPending", 0),
    "_interrupting": ("interrupting", 0),
    "_session_retrying": ("sessionRetrying", 0),
    "_session_awaiting": ("sessionAwaiting", 0),
    "_bg_live_norm": ("bgLive", 0),
    "_api_error": ("apiError", "path"),
    "_interrupted_by": ("interruptedBy", 1),
    "_peer_answered": ("peerAnswered", 0),
    "_heal_session_tops": ("healSessionTops", "path"),
    "_awaiting_task_descs": ("awaitingTaskDescs", 0),
    "_warm_wanted": ("warmWanted", "row"),
    "_cap_switch_offer": ("capSwitchOffer", 0),
}
SLICE_KEYS = ("asks", "working", "awaiting", "bgServices", "servingFoldedIds", "absentSids")   # what the per-card build makes
FRAME_SKIP = SLICE_KEYS + ("now",)
STORE_TRANSIENT = ("_unread", "_relayPending")   # plus every _base* key: judge.py _NONCONTENT_KEYS, never content

# The per-card functions s2-design.md section 2 lists (S4's port, in dependency order). The coverage trace records lines
# inside these only (nested closures and comprehensions count toward the function that holds them) and counts calls of
# every other function of the four modules, so a function the design missed shows up in `outsideList`. The fold's own
# three helpers (_feed_fold_session, _feed_fold_tints, _feed_fold_bell) are added to item 7, which names the fold.
PORT_FUNCS = {
    "event_model": ["_content", "_text_of", "_has_text", "_machine_written", "_text_hash8", "_stop_reason",
                    "is_interrupt_record", "_is_opener", "atom_has_text", "atom_prose_chars", "_prose_chars",
                    "atom_is_bare_end", "atom_is_settle", "segments", "_segment_id", "split_segment"],
    "judge": ["_seg_key", "_atom_text", "apply_seams", "_prompt_anchor_uuid", "_top_of", "_block_ev_t", "_block_since",
              "_done_since", "review_boundary", "junk_quote", "_locate_quote", "_human_prompt_record", "_seg_human",
              "_seg_command", "_seg_slash_shaped", "_seg_followup", "_seg_system"],
    "kernel": ["_atom_user_text", "_segs_seam", "_turn_sans_placed_echoes", "_session_working", "_suspended_after",
               "_open_turn_progress", "_last_plain_user_turn_t", "_seg_anchors", "_seg_last_text", "_feed_seg_fold",
               "_feed_seg_walk", "_feed_seg_plain_t", "_FeedSegTurns.get", "_agent_open_set", "_parked_rows",
               "_focus_open_top", "_goal_awaiting_stamp_full", "_open_leaves", "_all_outstanding_delegated",
               "_handoff_card_fields", "_handoff_peer_identities", "_peer_identity", "_awaiting_item",
               "_awaiting_peer_items", "_newest_block_ev_t", "_session_started_face", "_node_log_rows",
               "_node_anchor_uuids", "_landing_inputs", "_login_refusal_label", "_name_color", "_name_of",
               "_summary_text_anchor", "_text_atoms", "_opening_sentence", "_dictated_prompt_uuid",
               "_pure_delegation_top", "_provisional_card", "_seg_placed", "_seg_caption", "_seg_prompt",
               "_split_reminders", "_feed_session_entry", "build_feed", "_feed_fold_session", "_feed_fold_tints",
               "_feed_fold_bell", "_feed_fold_stamp", "_feed_fold_card", "_notify_card_effective",
               "_push_explicit_bell"],
    "colormap": ["age_rgb", "ramp"],
}


class DumpError(Exception):
    pass


def raise_default(o):
    raise TypeError("unencodable %r" % type(o))


def plain_default(o):
    """Sets become sorted lists (their order is the hash seed's, so never dumped as iterated); nothing else is coerced."""
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=lambda v: json.dumps(v, sort_keys=True, default=plain_default))
    raise TypeError("unencodable %r" % type(o))


def plain(v):
    """A JSON round trip of v: tuples become lists, sets sorted lists, keys strings, exactly as the Rust side reads it."""
    return json.loads(json.dumps(v, default=plain_default, allow_nan=False))


def canon(v):
    return json.dumps(v, sort_keys=True, default=raise_default, allow_nan=False)


def wire(v):
    return json.dumps(v, default=raise_default, allow_nan=False)


def sha256(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def in_git_tree(path):
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def prepare_out(out):
    """Make `out` an empty directory this tool owns. Refused inside a git work tree (the repo, or ~/Desktop, which is a
    repo): the world under it must not sit in one, and nothing this tool writes belongs in the repo."""
    out = os.path.realpath(out)
    tree = in_git_tree(out if os.path.exists(out) else os.path.dirname(out))
    if tree:
        raise DumpError("--out %s is inside the git work tree at %s; pick a directory outside any repo" % (out, tree))
    if os.path.exists(out):
        if os.listdir(out) and not os.path.exists(os.path.join(out, MARKER)):
            raise DumpError("--out %s exists, is not empty and was not made by this tool; refusing to clear it" % out)
        shutil.rmtree(out)
    os.makedirs(out)
    write_text(os.path.join(out, MARKER), "romp spike dump directory (tools/rust-spike/dump_world.py)\n")
    return out


def load_bcp():
    spec = importlib.util.spec_from_file_location("romp_spike_bcp", os.path.join(REPO, "tests", "bench_connect_push.py"))
    bcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bcp)
    return bcp


def chat_gen_class(sid):
    """tests/bench_chatbuild.py's Gen (wrap-ups of about 95 characters, above jd.CITE_MIN_CHARS), extracted by ast: importing
    that file runs its bench (it loads a kernel and resets XDG_STATE_HOME at module level)."""
    src = open(os.path.join(REPO, "tests", "bench_chatbuild.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.ClassDef) and n.name == "Gen")
            or (isinstance(n, ast.FunctionDef) and n.name == "iso")]
    if len(keep) != 2:
        raise DumpError("tests/bench_chatbuild.py no longer defines Gen and iso at top level")
    ns = {"datetime": datetime, "timezone": timezone, "SID": sid, "T0": NOW - 2 * 86400}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "bench_chatbuild_gen", "exec"), ns)

    class ChatGen(ns["Gen"]):
        def uid(self):                               # a per-session prefix, so no uuid collides with another session's
            self.n += 1
            return "cccccccc-0001-0000-0000-%012d" % self.n

        def turn(self, i):
            out = super().turn(i)
            if i == BOUNDARY_TURN:                   # the planted boundary atom, after the wrap-up (see BOUNDARY_TEXT)
                out.append(self.rec("assistant", {"role": "assistant", "model": "claude-sonnet-4",
                                                  "content": [{"type": "text", "text": BOUNDARY_TEXT}],
                                                  "stop_reason": "end_turn"}))
            return out
    return ChatGen


def munge(path):
    return "".join(ch if ch.isalnum() else "-" for ch in path)


def relocate_world(root, state, claude):
    """Rewrite the world's directories from <root>/work/<name> to /TESTDIR/work/<name> before the kernel reads anything
    (2026-11-25: write_world stamps the temp root into every record's cwd, every names entry and every registry, so two
    roots could never give byte-identical transcripts, trees or snapshots, which --check requires). The project
    directories are renamed to match, as the CLI names them from the cwd. Nothing else in the world changes."""
    src = os.path.join(root, "work")
    for base in (os.path.join(claude, "projects"), os.path.join(state, "names"), os.path.join(state, "sdk")):
        for dp, _dn, fn in os.walk(base):
            for f in fn:
                p = os.path.join(dp, f)
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
                if root in text:
                    with open(p, "w", encoding="utf-8", newline="") as fh:
                        fh.write(text.replace(src, FAKE_ROOT + "/work"))
    proj = os.path.join(claude, "projects")
    for d in sorted(os.listdir(proj)):
        new = d.replace(munge(src), munge(FAKE_ROOT + "/work"))
        if new != d:
            os.rename(os.path.join(proj, d), os.path.join(proj, new))
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            with open(os.path.join(dp, f), "rb") as fh:
                if root.encode() in fh.read():
                    raise DumpError("relocation left the world root in %s" % os.path.relpath(os.path.join(dp, f), root))


def clean_env(root):
    """The environment, before any romp import (STATE, PROJECTS and NAMES bind at import): no romp, Anthropic, 1Password
    or Claude variables, HOME inside the world, UTC, git ceilinged at the world root, the synthetic host name."""
    for k in list(os.environ):
        if k.startswith(("ROMP_", "ANTHROPIC_", "OP_", "CLAUDE_")) or k in ("TMUX", "TMUX_PANE"):
            os.environ.pop(k)
    os.environ.update({"HOME": os.path.join(root, "home"), "TZ": "UTC", "GIT_CEILING_DIRECTORIES": root,
                       "ROMP_HOST_NAME": HOST, "ROMP_UPDATE_CHECK": "off"})
    os.makedirs(os.environ["HOME"], exist_ok=True)
    time.tzset()


def load_romp(root, state, claude):
    """perf-bench's loader and guards (s2-design.md step 3): the kernel imported in-process, the SDK backend built without
    its constructor, every spawn refused (git answered as failure), writes outside the world refused, threads counted."""
    bcp = load_bcp()
    pb = bcp.load_bench_tools()
    private = os.path.join(root, "private")
    os.makedirs(private, exist_ok=True)
    pb.prepare_env(state, claude, private)
    os.environ["ROMP_HOST_NAME"] = HOST
    km, sbmod = pb.load_kernel(REPO)
    jd = km.jd
    for what, p in (("STATE", jd.STATE), ("PROJECTS", jd.PROJECTS)):
        if not os.path.realpath(str(p)).startswith(root + os.sep):
            raise DumpError("jd.%s is %s, outside the world root" % (what, p))
    km._sdk_backend = pb.make_backend(sbmod, state, False, False)
    km._codex_backend = False
    rec = pb.new_recorder()
    pb.install_guards(km, sbmod, pb.StateShadow(state, state, rec["refused_writes"]), rec, no_git=True)
    km._GLOBAL_CLAUDE_MD = os.path.join(root, "missing")
    return bcp, pb, km, rec


# ───────────────────────────── the coverage trace ─────────────────────────────
def _code_family(code):
    out = [code]
    for c in code.co_consts:
        if hasattr(c, "co_lines"):
            out.extend(_code_family(c))
    return out


def _resolve(mod, dotted):
    obj = mod
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return getattr(obj, "__code__", None)


class LineTrace:
    """sys.settrace over one build: line events inside the listed functions only, call counts for every other function
    of the four modules. Code objects are looked up by identity (a code object's own hash walks its constants)."""

    def __init__(self, mods):
        self.mods = mods
        self.owner = {}                  # id(code) -> label (module, name, first line)
        self.top = {}                    # id(code) -> label, the listed function's own code only
        self.family = {}                 # label -> [codes]
        self.unresolved = []
        self._keep = []
        for mname, names in PORT_FUNCS.items():
            for qn in names:
                code = _resolve(mods[mname], qn)
                if code is None:
                    self.unresolved.append("%s.%s" % (mname, qn))
                    continue
                label = (mname, qn, code.co_firstlineno)
                fam = _code_family(code)
                self.family[label] = fam
                self.top[id(code)] = label
                for c in fam:
                    self.owner[id(c)] = label
                    self._keep.append(c)
        self.files = {}
        for mname, m in mods.items():
            for v in vars(m).values():
                c = getattr(v, "__code__", None)
                if c is not None and os.path.realpath(c.co_filename) == os.path.realpath(m.__file__):
                    self.files[c.co_filename] = mname
                    break
        self.lines = defaultdict(set)
        self.calls = Counter()
        self.other = Counter()           # (module, qualname, first line, inside the per-card code) -> calls
        # the per-card code: one session's derivation and its fold. An unlisted function called beneath these is one S4
        # must port or answer from the snapshot; one called elsewhere in build_feed is frame or memo plumbing
        self.card_roots = {id(km_code) for km_code in (
            getattr(getattr(mods["kernel"], "_feed_session_entry", None), "__code__", None),
            getattr(getattr(mods["kernel"], "_feed_fold_session", None), "__code__", None)) if km_code is not None}
        self.in_card = 0

    def _global(self, frame, event, arg):
        code = frame.f_code
        label = self.owner.get(id(code))
        if label is None:
            m = self.files.get(code.co_filename)
            if m is not None:
                self.other[(m, code.co_qualname, code.co_firstlineno, self.in_card > 0)] += 1
            return None
        if id(code) in self.top:
            self.calls[label] += 1
        hit = self.lines[label]
        root = id(code) in self.card_roots
        if root:
            self.in_card += 1

        def local(fr, ev, a):
            if ev == "line":
                hit.add(fr.f_lineno)
            elif ev == "return" and root:
                self.in_card -= 1
            return local
        return local

    def run(self, fn):
        sys.settrace(self._global)
        try:
            return fn()
        finally:
            sys.settrace(None)

    @staticmethod
    def _ranges(lines):
        out = []
        for n in sorted(lines):
            if out and n == out[-1][1] + 1:
                out[-1][1] = n
            else:
                out.append([n, n])
        return out

    def _executable(self, label):
        lines = set()
        for c in self.family[label]:
            own = {ln for _s, _e, ln in c.co_lines() if ln is not None}
            own.discard(c.co_firstlineno)            # the def line runs when the function is defined, not called
            lines |= own
        return lines

    def _entry_statements(self, label):
        """_feed_session_entry's statements, each run or not: the top level, and every statement at any depth (closure
        bodies included). A simple statement ran when a line of it ran; a compound one when a line of its header ran,
        or, for a header with no code of its own (`try:`), when a statement under it ran. `missed` lists the outermost
        statements that never ran, as [first line, last line]."""
        km = self.mods["kernel"]
        code = km._feed_session_entry.__code__
        executed = self.lines.get(label, set())
        exe = self._executable(label)
        src = open(code.co_filename, encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
                  and n.name == "_feed_session_entry" and n.lineno == code.co_firstlineno)

        def body_of(node):
            b = list(node.body)
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
                    and isinstance(b[0].value.value, str) and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                b = b[1:]
            return b

        def children(st):
            out = []
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return body_of(st)
            for field in ("body", "orelse", "finalbody"):
                out.extend(getattr(st, field, None) or [])
            for h in getattr(st, "handlers", None) or []:
                out.extend(h.body)
            for c in getattr(st, "cases", None) or []:
                out.extend(c.body)
            return [c for c in out if isinstance(c, ast.stmt)]

        counts = {"all": 0, "ran": 0}
        missed = []

        def ran(st, record):
            kids = children(st)
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                head = range(st.lineno, st.lineno + 1)
            elif kids:
                head = range(st.lineno, kids[0].lineno)
            else:
                head = range(st.lineno, st.end_lineno + 1)
            has_code = any(ln in exe for ln in head)
            kid_ran = [ran(k, record) for k in kids]
            r = any(ln in executed for ln in head) if has_code else any(kid_ran)
            if record:
                counts["all"] += 1
                counts["ran"] += int(r)
            return r

        top = body_of(fn)
        top_ran = 0
        for st in top:
            r = ran(st, True)
            top_ran += int(r)

        def collect(st):                             # the outermost statements that never ran
            if not ran(st, False):
                missed.append([st.lineno, st.end_lineno])
                return
            for k in children(st):
                collect(k)
        for st in top:
            collect(st)
        return {"topLevel": len(top), "topLevelRan": top_ran, "statements": counts["all"], "statementsRan": counts["ran"],
                "missed": missed}

    def report(self, build, note):
        funcs, not_reached = [], []
        tot_lines = tot_exec = 0
        for label in sorted(self.family, key=lambda l: (l[0], l[2])):
            exe = self._executable(label)
            ran = self.lines.get(label, set()) & exe
            row = {"module": label[0], "name": label[1], "firstLine": label[2], "calls": self.calls.get(label, 0),
                   "lines": len(exe), "executed": len(ran), "missed": self._ranges(exe - ran)}
            if not row["calls"] and not ran:
                not_reached.append({"module": label[0], "name": label[1], "firstLine": label[2], "lines": len(exe)})
                continue
            tot_lines += len(exe)
            tot_exec += len(ran)
            funcs.append(row)
        entry_label = next(l for l in self.family if l[1] == "_feed_session_entry")
        merged = {}
        for (m, n, ln, inside), c in self.other.items():
            row = merged.setdefault((m, ln, n), {"module": m, "name": n, "firstLine": ln, "callsInCard": 0,
                                                 "callsElsewhere": 0})
            row["callsInCard" if inside else "callsElsewhere"] += c
        others = [merged[k] for k in sorted(merged)]
        in_card = [o for o in others if o["callsInCard"]]
        return {"schema": COVERAGE_SCHEMA, "build": build, "note": note,
                "totals": {"listed": sum(len(v) for v in PORT_FUNCS.values()), "reached": len(funcs),
                           "notReached": len(not_reached), "linesInReached": tot_lines, "linesExecuted": tot_exec,
                           "outsideListInCard": len(in_card), "outsideListElsewhere": len(others) - len(in_card)},
                "entryBody": self._entry_statements(entry_label),
                "functions": funcs, "notReached": not_reached, "unresolved": sorted(self.unresolved),
                "outsideListInCard": in_card, "outsideListElsewhere": [o for o in others if not o["callsInCard"]]}


# ───────────────────────────── the world ─────────────────────────────
def build_world(root, spec, bcp):
    """The world's files, written with the clock frozen at NOW (write_world stamps from time.time())."""
    n = spec["sessions"] + spec["fillers"]
    # big=0: s001's transcript is replaced by the chat generator below either way (spec["chatTurns"] x 8 records)
    with mock.patch.object(bcp.time, "time", return_value=float(NOW)):
        state, claude, sids = bcp.write_world(root, n, spec["records"], 0)
    relocate_world(root, state, claude)
    paths = {}
    for dp, _dn, fn in os.walk(os.path.join(claude, "projects")):
        for f in fn:
            if f.endswith(".jsonl"):
                paths[f[:-len(".jsonl")]] = os.path.join(dp, f)
    s1 = sids[0]
    gen = chat_gen_class(s1)()
    with open(paths[s1], "w", encoding="utf-8") as f:
        for i in range(spec["chatTurns"]):
            for r in gen.turn(i):
                r["cwd"] = FAKE_ROOT + "/work/notes-api"
                f.write(json.dumps(r) + "\n")
    # s003 and s004 end on an open turn (a typed prompt, then a tool_use with no result): "working" is the transcript's
    # open turn, not the live row (_session_working)
    for sid, tag, text in ((sids[2], "dddddddd", "now add pagination to the list endpoint and cover it with a test"),
                           (sids[3], "eeeeeeee", "write the release notes for the search change")):
        with open(paths[sid], encoding="utf-8") as f:
            last = json.loads(f.readlines()[-1])
        t = NOW - 300
        u1, u2 = "%s-0000-0000-0000-000000000001" % tag, "%s-0000-0000-0000-000000000002" % tag
        recs = [{"type": "user", "timestamp": bcp.iso(t), "uuid": u1, "parentUuid": last["uuid"], "sessionId": sid,
                 "cwd": last["cwd"], "version": "2.1.0", "gitBranch": "main", "promptSource": "typed",
                 "message": {"role": "user", "content": text}},
                {"type": "assistant", "timestamp": bcp.iso(t + 5), "uuid": u2, "parentUuid": u1, "sessionId": sid,
                 "cwd": last["cwd"], "version": "2.1.0", "gitBranch": "main",
                 "message": {"role": "assistant", "model": "claude-sonnet-4", "stop_reason": "tool_use",
                             "content": [{"type": "text", "text": "Reading the endpoint first."},
                                         {"type": "tool_use", "id": "toolu_open_" + tag[:4], "name": "Read",
                                          "input": {"file_path": "/TESTDIR/notes-api/api.py"}}]}}]
        with open(paths[sid], "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
    return state, claude, sids, paths


def live_rows(sids):
    def row(st):
        return {"state": st, "since": NOW - 100, "model": "", "effort": "", "context": None, "compactPct": None,
                "color": None}
    live = {sids[0]: row("idle"), sids[1]: row("permission"), sids[2]: row("working"), sids[3]: row("working")}
    for sid in sids[4:]:
        live[sid] = row("idle")
    return plain(live)


def mint_stores(km, sids, paths):
    """The goal stores, minted under the judges' own writers with real segment ids from the parse (s2-design.md step 5),
    under a frozen clock at T_MINT. The designed board: s001 completed / blocked / delegating, s002 planted by s001 and
    on a permission prompt, s003 working with a failed nudge on record, s004 no store (the provisional placeholder); every
    filler one goal with one done sub."""
    jd, em = km.jd, km.em
    S1, S2, S3, _S4 = sids[:4]

    def segs_of(sid):
        ps = km._parse(paths[sid], sid, NOW)
        return [sg for t in ps["turns"] for sg in em.segments(t)]

    def new_store(sid):
        return {"rompUuid": sid, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}

    def plan(store, seg, ops, menu=(), key=None, **kw):
        jd.apply_plan(store, seg["id"], seg["t"], ops, [{"id": m} for m in menu], place_key=key, **kw)

    # s001: a completed top with a summary, a blocked top with a brief, a dictated top delegating to s003 and s002
    sg = segs_of(S1)
    if len(sg) < 7:
        raise DumpError("s001 has %d segments; the designed store needs 7" % len(sg))
    st = new_store(S1)
    g = lambda n: "%s:g%d" % (S1, n)
    plan(st, sg[0], [{"do": "mint", "why": "opening ask", "text": "tighten the notes-api search index"}],
         prompt_uuid=sg[0]["trigger"], quote="step 0: tighten the search index")
    plan(st, sg[1], [{"do": "sub", "under": 1, "why": "a step", "text": "raise the search limit"}], [g(1)])
    plan(st, sg[2], [{"do": "done", "goal": 1, "why": "limit raised"}], [g(2)], key=sg[2]["id"] + "#d")
    plan(st, sg[BOUNDARY_TURN], [{"do": "done", "goal": 1, "why": "index tightened"}], [g(1)],
         key=sg[BOUNDARY_TURN]["id"] + "#d")
    plan(st, sg[4], [{"do": "mint", "why": "second ask", "text": "split the notes-api router — façade or modules"}])
    plan(st, sg[5], [{"do": "mint", "why": "third ask", "text": "coordinate the client and test changes"}],
         prompt_uuid=sg[5]["trigger"])
    plan(st, sg[5], [{"do": "sub", "under": 1, "why": "delegated", "text": "↪ delegated to s003: update the client"}],
         [g(4)], key=sg[5]["id"] + "#s1")
    plan(st, sg[5], [{"do": "sub", "under": 1, "why": "delegated", "text": "↪ delegated to s002: add the tests"}],
         [g(4)], key=sg[5]["id"] + "#s2")
    st["nodes"][g(5)]["handoff"] = {"peer": S3, "msgId": "m-0001", "goalId": None}
    st["nodes"][g(6)]["handoff"] = {"peer": S2, "msgId": "m-0002", "goalId": None}
    plan(st, sg[-1], [{"do": "block", "goal": 1, "why": "needs the user's call on the router split"}], [g(3)])
    st["nodes"][g(1)]["summary"] = "The search index is tighter: the limit was raised and the suite passes."
    # its opening sentence is the last turn's wrap-up word for word, so the brief's landing takes the located-quote road
    st["nodes"][g(3)]["blockSummary"] = ("Step %d done: the index is tighter and `tests/test_search.py` passes. "
                                         "Decide whether the router splits — façade or modules." % (len(sg) - 1))
    jd.rollup_status(st, session_closed=True, now=T_MINT)
    jd.save_goals(S1, st)

    # s002: a goal planted by s001 (the origin badge) and a goal of its own
    sg = segs_of(S2)
    st = new_store(S2)
    plan(st, sg[0], [{"do": "mint", "why": "planted by a peer", "text": "add the notes-api list tests"}])
    st["nodes"]["%s:g1" % S2]["origin"] = {"peer": S1, "goalId": g(6), "peerName": "s001", "msgId": "m-0002"}
    plan(st, sg[2], [{"do": "mint", "why": "own ask", "text": "fix the flaky search test (ünïcode names, 日本語)"}])
    jd.rollup_status(st, session_closed=True, now=T_MINT)
    jd.save_goals(S2, st)

    # s003: open turn, a working goal with a done sub, a failed nudge on record
    sg = segs_of(S3)
    st = new_store(S3)
    plan(st, sg[0], [{"do": "mint", "why": "ask", "text": "update the notes-api client"}])
    plan(st, sg[1], [{"do": "sub", "under": 1, "why": "step", "text": "regenerate the client types"}], ["%s:g1" % S3])
    plan(st, sg[2], [{"do": "done", "goal": 1, "why": "types regenerated"}], ["%s:g2" % S3], key=sg[2]["id"] + "#d")
    jd.rollup_status(st, session_closed=False, now=T_MINT)
    jd.save_goals(S3, st)
    # s004: no goal store (the provisional placeholder)

    for i, sid in enumerate(sids[4:]):
        sg = segs_of(sid)
        st = new_store(sid)
        plan(st, sg[0], [{"do": "mint", "why": "ask", "text": "notes-api chore %d: tidy the search module" % (i + 5)}])
        plan(st, sg[1], [{"do": "sub", "under": 1, "why": "step", "text": "rerun the search suite"}], ["%s:g1" % sid])
        plan(st, sg[2], [{"do": "done", "goal": 1, "why": "suite passes"}], ["%s:g2" % sid], key=sg[2]["id"] + "#d")
        jd.rollup_status(st, session_closed=True, now=T_MINT)
        jd.save_goals(sid, st)

    state = str(jd.STATE)
    write_text(os.path.join(state, "auto-nudge.json"),
               json.dumps({"enabled": True, "nudged": {"%s:g1" % S3: {"count": 2, "failed": True,
                                                                     "failedAt": NOW - 600}}}))
    write_text(os.path.join(state, "nudge-events.jsonl"),
               "".join(json.dumps({"gid": "%s:g1" % S3, "t": t, "verdict": "fired"}) + "\n"
                       for t in (NOW - 1500, NOW - 900)))


def pin_mtimes(root, sids, paths):
    """Distinct mtimes in a designed order (s2-design.md step 6): the alive order is newest transcript first and sets the
    asks order, so the transcripts get s001 newest, then s002, ...; every other file a value from its sorted relative
    path, all at or below NOW and inside the 48 h window. The S2 probe's os.walk order flipped the asks order between
    runs, because APFS enumerates the root-named project directories differently (2026-11-25)."""
    tx = {paths[sid]: NOW - 60 - i for i, sid in enumerate(sids)}
    rest = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            if p not in tx:
                rest.append(os.path.relpath(p, root))
    rest.sort()
    for i, rel in enumerate(rest):
        ns = (NOW - 3600 - len(rest) + i) * 10 ** 9
        os.utime(os.path.join(root, rel), ns=(ns, ns))
    for p, t in tx.items():
        os.utime(p, ns=(t * 10 ** 9, t * 10 ** 9))
    return tx


def clear_build_memos(km):
    km._feed_memo_forget(set())                      # drops _FEED_SEG_HELD as well
    km._FEED_FOLD.clear()
    km._SUMMARY_ANCHOR_MEMO.clear()


# ───────────────────────────── recording ─────────────────────────────
class Recorder:
    """The recording build's wrappers: each recorded helper's answer by sid (a second call for the same sid must agree),
    the ctx facts the body reads, the stores the body loads, the captions it reads and the build's board reads."""

    def __init__(self, km, path_sid):
        self.km, self.jd = km, km.jd
        self.path_sid = path_sid
        self.answers = defaultdict(dict)             # snapshot key -> {sid: answer} or {sid: {sub: answer}}
        self.types = defaultdict(set)
        self.conflicts = []
        self.ctx = {}                                # sid -> the ctx facts at the body's entry
        self.board = None
        self.store_reads = []                        # (loader, psid) the body asked for
        self.caption_reads = {}
        self.in_body = 0
        self._saved = []

    def sid_of(self, how, a, k):
        if how == "path":
            return self.path_sid[str(a[0])]
        if how == "row":
            return a[0]["sid"]
        return str(a[how])

    @staticmethod
    def sub_of(name, a, k):
        if name != "_session_awaiting":
            return None
        idle = a[2] if len(a) > 2 else k.get("idle")
        stamp = a[3] if len(a) > 3 else k.get("stamp", False)
        return "idle=%s" % json.dumps(bool(idle)) + (",stamp=true" if stamp else "")

    def _patch(self, obj, name, fn):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, fn)

    def install(self):
        km, jd = self.km, self.jd
        for name, (key, how) in RECORDED.items():
            real = getattr(km, name)

            def wrap(*a, _real=real, _name=name, _key=key, _how=how, **k):
                r = _real(*a, **k)
                sid = self.sid_of(_how, a, k)
                sub = self.sub_of(_name, a, k)
                self.types[_key].add(type(r).__name__)
                v = plain(r)
                slot = self.answers[_key]
                if sub is not None:
                    slot = slot.setdefault(sid, {})
                    tag = sub
                else:
                    tag = sid
                if tag in slot and slot[tag] != v:
                    self.conflicts.append("%s(%s%s): %r then %r" % (_name, sid[-6:], "," + sub if sub else "",
                                                                     slot[tag], v))
                slot[tag] = v
                return r
            self._patch(km, name, wrap)
        real_entry = km._feed_session_entry

        def entry(s, ctx):
            if self.board is None:
                self.board = {k: ctx.get(k) for k in ("cleared", "dbg_rows", "wmap", "stalls", "jauth_map", "jactive",
                                                      "nudge_records", "nudge_times")}
            self.ctx[s["sid"]] = {k: ctx.get(k) for k in ("ps", "who_working", "interrupting", "store", "closer",
                                                          "hide", "leaf_ok")}
            self.in_body += 1
            try:
                return real_entry(s, ctx)
            finally:
                self.in_body -= 1
        self._patch(km, "_feed_session_entry", entry)
        for loader in ("load_goals_shared_or_fault", "load_goals_shared", "load_goals"):
            real = getattr(jd, loader)

            def lw(psid, *a, _real=real, _loader=loader, **k):
                if self.in_body:
                    self.store_reads.append((_loader, str(psid)))
                return _real(psid, *a, **k)
            self._patch(jd, loader, lw)
        real_caps = km._captions

        def caps(fsid, *a, **k):
            r = real_caps(fsid, *a, **k)
            if self.in_body:
                self.caption_reads[str(fsid)] = plain(dict(r) if r is not None else None)
            return r
        self._patch(km, "_captions", caps)
        self.reads = {}
        for obj, name in ((km, "_colormap"), (km, "_notify_cards")):
            real = getattr(obj, name)

            def rw(*a, _real=real, _name=name, **k):
                r = _real(*a, **k)
                self.reads[_name] = plain(r)
                return r
            self._patch(obj, name, rw)

    def uninstall(self):
        for obj, name, v in reversed(self._saved):
            setattr(obj, name, v)
        self._saved = []


def strip_store(st):
    if st is None:
        return None
    return {k: v for k, v in plain(st).items() if not k.startswith("_base") and k not in STORE_TRANSIENT}


def plain_tree(em, ps):
    if ps is None:
        return None
    t = em.plain_tree(ps)
    t.pop("dir", None)
    t.pop("_txKey", None)
    return plain(t)


def feed_slice(feed):
    return {k: feed[k] for k in SLICE_KEYS if k in feed}


def first_diff(a, b, path="$"):
    """The first path at which two decoded JSON values differ, or None: the comparator the mutation check exercises."""
    if type(a) is not type(b):
        return "%s: type %s vs %s" % (path, type(a).__name__, type(b).__name__)
    if isinstance(a, dict):
        if list(a) != list(b):
            if set(a) != set(b):
                return "%s: keys %s vs %s" % (path, sorted(set(a) - set(b)), sorted(set(b) - set(a)))
            return "%s: key order" % path
        for k in a:
            d = first_diff(a[k], b[k], "%s.%s" % (path, k))
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return "%s: length %d vs %d" % (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, "%s[%d]" % (path, i))
            if d:
                return d
        return None
    return None if a == b else "%s: %r vs %r" % (path, a, b)


# ───────────────────────────── the replay harness ─────────────────────────────
class Replay:
    """Patches a loaded kernel so build_feed reads only the dumped inputs: the alive rows, live map and board facts from
    snapshot.json, the trees from trees/, the stores and peer stores from the snapshot, the recorded helpers answered by
    sid, and _feed_session_key replaced by the ctx facts it hands the body (the memo key machinery is not ported). A
    reader the harness misses reads the replay's empty state root, so its slice differs and the check fails."""

    def __init__(self, km, snap, trees):
        self.km, self.jd, self.snap = km, km.jd, snap
        self.trees = trees
        self.missing = []
        self.path_sid = {}
        self.rows = []
        for r in snap["alive"]:
            p = "$ROOT/" + r["transcript"]            # a path that does not exist: nothing may read the transcript
            self.path_sid[p] = r["sid"]
            self.rows.append({"sid": r["sid"], "name": r["name"], "anchor": r["anchor"], "path": p, "mtime": r["mtime"]})
        # a store loaded as a peer (jd.load_goals_shared_or_fault and kin) is the peer entry, as Python's loader read it;
        # a session's own store (ctx["store"], _feed_goals_keyed) is the session entry; a peer read of a session that has
        # no peer entry falls back to its own store (2026-11-25: with the session entry winning, a planted change to a
        # peer store moved nothing, and the negative control caught it)
        self.stores = {sid: s["store"] for sid, s in snap["sessions"].items()}
        for psid, p in snap["peers"].items():
            self.stores[psid] = p["store"]
        self._saved = []

    def _patch(self, obj, name, fn):
        if not hasattr(obj, name):
            raise DumpError("the kernel has no %s to patch" % name)
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, fn)

    def answer(self, name, key, how, a, k):
        if how == "path":
            sid = self.path_sid.get(str(a[0]))
        elif how == "row":
            sid = a[0]["sid"]
        else:
            sid = str(a[how])
        rec = (self.snap["sessions"].get(sid) or {}).get("recorded") or {}
        sub = Recorder.sub_of(name, a, k)
        if key not in rec or (sub is not None and sub not in rec[key]):
            self.missing.append("%s(%s%s)" % (name, sid, "," + sub if sub else ""))
            raise DumpError("no recorded answer for %s(%s%s): Python never called it in the dump" % (name, sid, sub or ""))
        v = rec[key][sub] if sub is not None else rec[key]
        return copy.deepcopy(v)

    def install(self):
        km, jd, snap = self.km, self.jd, self.snap
        board, world = snap["board"], snap["world"]
        live = copy.deepcopy(snap["liveMap"])
        names = {}
        for psid, p in snap["peers"].items():
            if p["names"] is not None:
                names[psid] = list(p["names"])
        for sid, s in snap["sessions"].items():
            if s["names"] is not None:
                names[sid] = list(s["names"])
        flags = {}
        for psid, p in snap["peers"].items():
            if p.get("flags") is not None:
                flags[psid] = p["flags"]
        for sid, s in snap["sessions"].items():
            if s["flags"] is not None:
                flags[sid] = s["flags"]
        self._patch(km, "_live_map", lambda: live)
        km._live_scope.snapshot = live
        km._live_scope.paths = {}
        km._live_scope.sessions = {}
        km._live_scope.names = names
        self._patch(km, "_alive_sessions", lambda now, lm: [dict(r) for r in self.rows])
        self._patch(km, "_cleared_ids_display", lambda: set(board["cleared"]))
        self._patch(jd, "_debug_mode", lambda: bool(board["debug"]))
        self._patch(km, "_judge_error_rows", lambda now: copy.deepcopy(board["debugRows"]))
        self._patch(km, "_wait_for_graph", lambda now, sids: copy.deepcopy(board["wmap"]))
        self._patch(km, "_stalled_goals", lambda: copy.deepcopy(board["stalls"]))
        self._patch(jd, "_auth_down_map", lambda: copy.deepcopy(board["jauth"]))
        self._patch(jd, "active_runs", lambda: [{"fsid": f} for f in board["jactive"]])
        self._patch(km, "_colormap", lambda: world["colormap"])
        self._patch(km, "_notify_cards", lambda: copy.deepcopy(board["notifyCards"]))
        self._patch(km, "_session_flags", lambda: flags)
        km._downtime[:] = [tuple(x) for x in board["downtime"]]

        def captions(fsid, *a, **k):
            s = snap["sessions"].get(str(fsid))
            if s is None or s.get("captions") is None:
                self.missing.append("_captions(%s)" % fsid)
                raise DumpError("no recorded captions for %s" % fsid)
            return copy.deepcopy(s["captions"])
        self._patch(km, "_captions", captions)

        def store_of(psid):
            if str(psid) not in self.stores:
                self.missing.append("store(%s)" % psid)
                raise DumpError("no dumped store for %s" % psid)
            return copy.deepcopy(self.stores[str(psid)])
        self._patch(jd, "load_goals_shared_or_fault", lambda psid, *a, **k: (store_of(psid), None))
        self._patch(jd, "load_goals_shared", lambda psid, *a, **k: store_of(psid))
        self._patch(jd, "load_goals", lambda psid, *a, **k: store_of(psid))
        self._patch(km, "_feed_goals_keyed", lambda sid: (copy.deepcopy(snap["sessions"][str(sid)]["store"]), None))
        self._patch(km, "_parse_cached", lambda path: self.trees.get(self.path_sid.get(str(path))))
        self._patch(km, "_parse", lambda path, sid, now: self.trees.get(self.path_sid.get(str(path))))
        for name, (key, how) in RECORDED.items():
            self._patch(km, name, lambda *a, _n=name, _k=key, _h=how, **k: self.answer(_n, _k, _h, a, k))
        nudge_records = board["nudgeRecords"]
        nudge_times = {g: tuple(v) for g, v in board["nudgeTimes"].items()}

        def key(s, tm, ctx, prev):
            # what _feed_session_key hands the body through ctx (its docstring's list), taken from the dump: the tree and
            # the store as read, _session_working over the tree (ported), the two recorded answers under the key's gate
            sid = s["sid"]
            S = snap["sessions"][sid]
            ctx["nudge_records"], ctx["nudge_times"], ctx["peer_facts"] = nudge_records, nudge_times, {}
            hide = bool((S["flags"] or {}).get("hideFromFeed"))
            ps = self.trees.get(sid) if not hide else None
            try:
                who = km._session_working(ps["turns"]) if ps else False
            except Exception:
                who = False
            inter = self.answer("_interrupting", "interrupting", 0, (sid,), {}) if not hide else False
            st = copy.deepcopy(S["store"])
            jact = sid in ctx["jactive"]
            closer = bool(not hide and tm is not None and ps and not who and not jact
                          and self.answer("_closer_pending", "closerPending", 0, (sid,), {}))
            ctx.update(ps=ps, who_working=who, interrupting=inter, store=st, closer=closer, hide=hide,
                       leaf_ok=S["leafOk"], leaf_head=(s["path"], 0) if S["leafOk"] else None)
            return ("replay", sid)
        self._patch(km, "_feed_session_key", key)
        self._patch(km, "_feed_key_with_deps", lambda k, ctx, entry: k)

    def uninstall(self):
        for obj, name, v in reversed(self._saved):
            setattr(obj, name, v)
        self._saved = []

    def assemble(self, feed):
        """The replay's per-card slice spliced into the dumped frame, in the oracle's key order (what Rust ships)."""
        frame = self.snap["frame"]
        out = {}
        for k in self.snap["frameKeys"]:
            if k == "now":
                out[k] = self.snap["world"]["now"]
            elif k in SLICE_KEYS:
                out[k] = feed.get(k)
            else:
                out[k] = frame[k]
        return out


def load_dump(out):
    with open(os.path.join(out, "snapshot.json"), encoding="utf-8") as f:
        snap = json.load(f)
    trees = {}
    for r in snap["alive"]:
        if r["tree"] is not None:
            with open(os.path.join(out, r["tree"]), encoding="utf-8") as f:
                trees[r["sid"]] = json.load(f)
    return snap, trees


def replay_build(km, snap, trees, trace=None):
    """One replay build over the dumped inputs: returns (feed assembled with the frame, the raw feed, the harness)."""
    rp = Replay(km, snap, trees)
    clear_build_memos(km)
    rp.install()
    try:
        with mock.patch("time.time", return_value=float(NOW)):
            km._STARTED = float(NOW)
            fn = lambda: km.build_feed(NOW, km._live_map())
            feed = trace.run(fn) if trace is not None else fn()
        entries = {sid: km._feed_memo[sid][1] for sid in km._feed_memo}
    finally:
        rp.uninstall()
    return rp.assemble(feed), feed, rp, entries


def compare_replay(out, full, raw, rp, entries):
    with open(os.path.join(out, "oracle", "feed.canon.json"), encoding="utf-8") as f:
        oracle_canon = f.read()
    with open(os.path.join(out, "oracle", "feed.wire.json"), encoding="utf-8") as f:
        oracle_wire = f.read()
    oracle = json.loads(oracle_canon)
    ent_diff = []
    for r in rp.snap["alive"]:
        p = os.path.join(out, "oracle", "entries", r["sid"] + ".json")
        want = open(p, encoding="utf-8").read() if os.path.exists(p) else None
        if entries.get(r["sid"]) != want:
            ent_diff.append(r["sid"])
    return {
        "sliceEqual": canon(feed_slice(raw)) == canon(feed_slice(oracle)),
        "canonEqual": canon(full) == oracle_canon,
        "wireEqual": wire(full) == oracle_wire,
        "entriesEqual": not ent_diff, "entriesDiffer": ent_diff,
        "firstDiff": first_diff(json.loads(canon(full)), oracle),
        "missingRecorded": sorted(set(rp.missing)),
        "absentSids": raw.get("absentSids") or [],
    }


# ───────────────────────────── the dump ─────────────────────────────
def scan_outputs(out, needles):
    """No output may carry the world root, the home directory, the user, the machine's name or the repo path."""
    hits = []
    for dp, dn, fn in os.walk(out):
        dn[:] = [d for d in dn if d != "work"]
        for f in fn:
            if f == RUN_FILE:
                continue
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                data = fh.read()
            for label, s in needles.items():
                if s and s.encode() in data:
                    hits.append("%s in %s" % (label, os.path.relpath(p, out)))
    return hits


def file_rows(out, mtimes):
    rows = []
    total = 0
    for dp, dn, fn in os.walk(out):
        dn[:] = [d for d in dn if d != "work"]
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, out)
            if rel in ("manifest.json", RUN_FILE, MARKER):
                continue
            with open(p, "rb") as fh:
                data = fh.read()
            total += len(data)
            row = {"path": rel, "size": len(data), "sha256": sha256(data)}
            if rel in mtimes:
                row["mtimeNs"] = mtimes[rel]
            rows.append(row)
    rows.sort(key=lambda r: r["path"])
    return rows, total


def json_hazards(v, path="$", out=None):
    """Values serde_json reads differently from Python (s2-design.md section 3): ints outside 64 bits and lone
    surrogates. NaN and infinity are refused by allow_nan=False at every write. Returns at most 10 paths."""
    out = [] if out is None else out
    if len(out) >= 10:
        return out
    if isinstance(v, bool) or v is None or isinstance(v, float):
        return out
    if isinstance(v, int):
        if not -(1 << 63) <= v < (1 << 64):
            out.append("%s: int %d" % (path, v))
    elif isinstance(v, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in v):
            out.append("%s: lone surrogate" % path)
    elif isinstance(v, dict):
        for k, x in v.items():
            json_hazards(k, path + ".<key>", out)
            json_hazards(x, "%s.%s" % (path, k), out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            json_hazards(x, "%s[%d]" % (path, i), out)
    return out


def census(feed, sids):
    """The designed card shapes (s2-design.md self-check c): a kernel change that drops one fails the dump instead of
    quietly shrinking S4's scope."""
    S1, S2, S3, S4 = sids[:4]
    cards = defaultdict(list)
    for c in feed["asks"]:
        cards[c["sid"]].append(c)

    def find(sid, suffix):
        return next((c for c in cards[sid] if c["itemId"].endswith(suffix)), None)

    def handoffs(c):
        return [r for r in (c or {}).get("tree") or [] if r.get("kind") == "handoff"]
    c1, c3, c4 = find(S1, ":g1"), find(S1, ":g3"), find(S1, ":g4")
    d1, d2 = find(S2, ":g1"), find(S2, ":g2")
    e1 = find(S3, ":g1")
    checks = {
        "s001 g1 completed, summary landed": bool(c1 and c1["column"] == "completed" and c1.get("summaryAnchorUuid")),
        "s001 g3 needs input with a brief": bool(c3 and c3["column"] == "needs_input" and c3.get("blockSummary")),
        "s001 g4 awaiting a peer, 2 handoff rows, whoWorking [true, false]": bool(
            c4 and (c4.get("awaiting") or {}).get("kind") == "peer"
            and [r.get("whoWorking") for r in handoffs(c4)] == [True, False]),
        "s002 g1 working, origin badge, judging": bool(d1 and d1["column"] == "working" and d1.get("origin")
                                                       and d1.get("judging")),
        "s002 g2 needs input, blocked on a permission": bool(d2 and d2["column"] == "needs_input"
                                                            and (d2.get("blocked") or {}).get("state") == "permission"),
        "s003 g1 working, open turn, nudged and failed": bool(e1 and e1.get("sessState") == "open"
                                                             and (e1.get("working") or {}).get("toolUses") == 1
                                                             and e1.get("nudged") and e1.get("nudgeFailed")),
        "s004 provisional placeholder": len(cards[S4]) == 1 and ":g" not in cards[S4][0]["itemId"],
        "every anchored goal card has a prompt anchor": all(
            (c.get("tree") or [{}])[0].get("promptAnchorUuid") for sid in sids for c in cards[sid]
            if ":g" in c["itemId"]),
        "a located-quote landing (some card carries summaryAnchorQuote)": any(
            c.get("summaryAnchorQuote") for c in feed["asks"]),
        "working dots are s003 and s004": sorted(feed["working"]) == ["s003", "s004"],
        "awaiting dot is s001": feed["awaiting"] == ["s001"],
        "every filler has one goal card": all(len(cards[sid]) == 1 for sid in sids[4:]),
        "non-ASCII text on the board": any(any(ord(ch) > 127 for ch in c.get("text", "")) for c in feed["asks"]),
    }
    return checks


def dump(world, out, keep_world=False, replay=True):
    spec = WORLDS[world]
    t_start = time.time()
    out = prepare_out(out)
    os.makedirs(os.path.join(out, "work"))
    root = os.path.realpath(tempfile.mkdtemp(prefix="world-", dir=os.path.join(out, "work")))
    real_home = os.path.expanduser("~")
    needles = {"world root": root, "home directory": real_home, "repo path": REPO,
               "machine name": platform.node().split(".")[0], "user": os.environ.get("USER") or ""}
    if len(needles["user"]) < 4:
        needles.pop("user")
    clean_env(root)
    run = {"root": root, "pythonHashSeed": os.environ.get("PYTHONHASHSEED", "random"), "started": t_start}
    bcp = load_bcp()
    state, claude, sids, paths = build_world(root, spec, bcp)
    _bcp, pb, km, rec = load_romp(root, state, claude)
    jd, em = km.jd, km.em
    live = live_rows(sids)
    km._live_map = lambda: live
    km._live_scope.snapshot = live
    km._live_scope.paths = {}
    km._live_scope.sessions = {}
    km._live_scope.names = km._names_snapshot()
    km._STARTED = float(NOW)

    with mock.patch("time.time", return_value=float(T_MINT)):
        mint_stores(km, sids, paths)
    tx_mtimes = pin_mtimes(root, sids, paths)

    t0 = time.process_time()
    freeze = mock.patch("time.time", return_value=float(NOW))
    freeze.start()
    try:
        with jd._active_lock:
            jd._active["spike-run"] = {"fsid": sids[1], "kind": "planner", "t": NOW - 20}
        km._live_scope.names = km._names_snapshot()
        jd._discover_cache.clear()
        alive = km._alive_sessions(NOW, live)
        if [s["sid"] for s in alive] != list(sids):
            raise DumpError("alive order %s is not the designed order" % [s["name"] for s in alive])
        for s in alive:
            km._parse(s["path"], s["sid"], NOW)
        km.build_feed(NOW, live)                     # the adoption build (session-order.json), then a clean memo
        clear_build_memos(km)
        wire_default0 = km._wire_stats["default_str"]
        stderr0 = sys.stderr
        cap_err = io.StringIO()
        sys.stderr = cap_err
        path_sid = {s["path"]: s["sid"] for s in alive}
        recd = Recorder(km, path_sid)
        recd.install()
        try:
            t_build = time.process_time()
            feed = km.build_feed(NOW, live)
            t_build = time.process_time() - t_build
        finally:
            recd.uninstall()
            sys.stderr = stderr0
        entries = {sid: km._feed_memo[sid][1] for sid in km._feed_memo}
        oracle_canon, oracle_wire = canon(feed), wire(feed)
        # the traced live build, kept apart from the oracle build (s2-design.md: traced builds never time or produce the
        # oracle): the same inputs, so the same bytes
        clear_build_memos(km)
        mods = {"kernel": km, "judge": jd, "event_model": em, "colormap": km.cm}
        tr_full = LineTrace(mods)
        feed_traced = tr_full.run(lambda: km.build_feed(NOW, live))
        traced_equal = canon(feed_traced) == oracle_canon
        # peers: every store the bodies loaded, every sid an entry names, every origin sender
        peer_sids = set(p for _l, p in recd.store_reads)
        for js in entries.values():
            peer_sids.update((json.loads(js) or {}).get("peers") or ())
        for sid in sids:
            st = recd.ctx.get(sid, {}).get("store") or {}
            for nd in (st.get("nodes") or {}).values():
                o = nd.get("origin")
                if isinstance(o, dict) and o.get("peer"):
                    peer_sids.add(str(o["peer"]))
        peers = {}
        for psid in sorted(peer_sids):
            pst, pfault = jd.load_goals_shared_or_fault(psid)
            if pfault is not None:
                raise DumpError("peer %s's store faulted: %r" % (psid, pfault))
            peers[psid] = {"names": km._names_parts(psid), "flags": km._session_flags().get(psid),
                           "store": strip_store(pst)}
        flags_all = km._session_flags()
    finally:
        freeze.stop()
    t_oracle = time.process_time() - t0

    # ── the files ──
    lazy = 0
    trees = {}
    for s in alive:
        ps = recd.ctx[s["sid"]]["ps"]
        if ps is not None:
            lazy += sum(1 for t in ps["turns"] for a in t["atoms"] if a.get("lazy") is not None)
        trees[s["sid"]] = plain_tree(em, ps)
    if lazy:
        raise DumpError("%d lazy atoms in the parse trees: a dumped tree would need the transcript to hydrate" % lazy)
    board = recd.board
    if board["jauth_map"]:
        raise DumpError("the judge-auth latch is set; its loginLabel resolution is not replayed (not exercised)")
    for k in ("wmap", "stalls", "nudge_records", "nudge_times"):
        if not isinstance(board[k], dict):
            raise DumpError("board %s is a %s; the snapshot schema expects a dict" % (k, type(board[k]).__name__))
    snap = {
        "schema": SCHEMA,
        "world": {"name": world, "now": NOW, "mintClock": T_MINT, "colormap": recd.reads.get("_colormap"),
                  "hostName": HOST, "repoHead": pb.git_head(REPO), "python": platform.python_version(),
                  "sizes": {"sessions": spec["sessions"], "fillers": spec["fillers"], "records": spec["records"],
                            "chatTurns": spec["chatTurns"], "chatRecords": spec["chatTurns"] * 8 + 1}},
        "alive": [{"sid": s["sid"], "name": s["name"], "anchor": s["anchor"], "mtime": s["mtime"],
                   "tree": ("trees/%s.json" % s["sid"]) if trees[s["sid"]] is not None else None,
                   "transcript": "transcripts/%s.jsonl" % s["sid"]} for s in alive],
        "liveMap": live,
        "board": {"cleared": sorted(board["cleared"] or ()), "jactive": sorted(board["jactive"] or ()),
                  "jauth": plain(board["jauth_map"]), "wmap": plain(board["wmap"]), "stalls": plain(board["stalls"]),
                  "nudgeRecords": plain(board["nudge_records"]), "nudgeTimes": plain(board["nudge_times"]),
                  "debug": board["dbg_rows"] is not None, "debugRows": plain(board["dbg_rows"]),
                  "notifyCards": recd.reads.get("_notify_cards"), "downtime": plain(list(km._downtime))},
        "sessions": {},
        "peers": peers,
        "frameKeys": list(feed.keys()),
        "frame": {k: plain(v) for k, v in feed.items() if k not in FRAME_SKIP},
    }
    for s in alive:
        sid = s["sid"]
        c = recd.ctx[sid]
        recorded = {}
        for _name, (key, _how) in RECORDED.items():
            if sid in recd.answers.get(key, {}):
                recorded[key] = recd.answers[key][sid]
        snap["sessions"][sid] = {"names": km._names_parts(sid), "flags": flags_all.get(sid), "leafOk": c["leaf_ok"],
                                 "store": strip_store(c["store"]), "captions": recd.caption_reads.get(sid),
                                 "recorded": recorded}
    write_text(os.path.join(out, "snapshot.json"), wire(snap))
    for sid, t in trees.items():
        if t is not None:
            write_text(os.path.join(out, "trees", sid + ".json"), json.dumps(t, sort_keys=True, default=raise_default,
                                                                           allow_nan=False))
    mtimes = {}
    for s in alive:
        dst = os.path.join(out, "transcripts", s["sid"] + ".jsonl")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(s["path"], dst)
        ns = tx_mtimes[s["path"]] * 10 ** 9
        os.utime(dst, ns=(ns, ns))
        mtimes[os.path.relpath(dst, out)] = os.stat(s["path"]).st_mtime_ns
    states_dir = os.path.join(state, "states")
    for f in sorted(os.listdir(states_dir)):
        src, dst = os.path.join(states_dir, f), os.path.join(out, "states", f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        st_ = os.stat(src)
        os.utime(dst, ns=(st_.st_mtime_ns, st_.st_mtime_ns))
        mtimes[os.path.relpath(dst, out)] = st_.st_mtime_ns
    write_text(os.path.join(out, "oracle", "feed.canon.json"), oracle_canon)
    write_text(os.path.join(out, "oracle", "feed.wire.json"), oracle_wire)
    for sid, js in entries.items():
        write_text(os.path.join(out, "oracle", "entries", sid + ".json"), js)
    write_text(os.path.join(out, "coverage", "full.json"),
               json.dumps(tr_full.report("live", "the recording build's inputs, the recorded helpers live"), indent=1))

    # ── the in-process replay (dumped files), the boundary mutation, the comparator's own check ──
    snap_r, trees_r = load_dump(out)
    with mock.patch("time.time", return_value=float(NOW)):
        full_r, raw_r, rp, ent_r = replay_build(km, snap_r, trees_r)
    inproc = compare_replay(out, full_r, raw_r, rp, ent_r)
    s1 = sids[0]
    b_atom = None
    for t in trees_r[s1]["turns"]:
        for a in t["atoms"]:
            for blk in ((a.get("message") or {}).get("content") or []):
                if isinstance(blk, dict) and blk.get("text") == BOUNDARY_TEXT:
                    b_atom = (a, blk)
    boundary = {"codePoints": len(BOUNDARY_TEXT), "utf8Bytes": len(BOUNDARY_TEXT.encode("utf-8")),
                "citeMinChars": jd.CITE_MIN_CHARS, "inTree": b_atom is not None}
    if b_atom is not None:
        b_atom[1]["text"] = BOUNDARY_TEXT + "!"     # 80 code points: substantive as Python counts
        mut_full, _raw, _rp, _ent = replay_build(km, snap_r, trees_r)
        b_atom[1]["text"] = BOUNDARY_TEXT
        d = first_diff(json.loads(canon(mut_full)), json.loads(oracle_canon))
        boundary["mutationMoves"] = d is not None
        boundary["mutationFirstDiff"] = d
    mutated = json.loads(oracle_canon)
    mutated["asks"][0]["text"] = mutated["asks"][0]["text"] + " (mutated)"
    comparator_catches = first_diff(mutated, json.loads(oracle_canon)) is not None and \
        canon(mutated) != oracle_canon

    # ── the fresh-process replay: the dumped files only, an empty state root ──
    fresh = None
    if replay:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
        env["HOME"] = real_home
        proc = subprocess.run([sys.executable, os.path.realpath(__file__), "--replay", out], env=env,
                              capture_output=True, text=True, cwd=out)
        try:
            fresh = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            fresh = {"error": "replay exited %d: %s" % (proc.returncode, (proc.stderr or proc.stdout)[-2000:])}
        if proc.stderr.strip():
            fresh["stderr"] = proc.stderr.strip().splitlines()[-20:]
        # timings differ run to run, so they go to run.json, never the manifest (2026-11-25: the first --check run
        # differed in this one field)
        run["replayFreshCpu"] = fresh.pop("cpu", None)

    guards = {"spawns": len(rec["spawns"]), "refusedWrites": len(rec["refused_writes"]),
              "threadStarts": rec["thread_starts"], "warmCalls": len(rec["warm_calls"]),
              "absentSids": feed.get("absentSids") or [],
              "notices": {k: len(feed.get(k) or []) for k in ("sdkNotices", "syncNotices", "clearNotices")},
              "defaultStrDelta": km._wire_stats["default_str"] - wire_default0,
              "buildStderr": cap_err.getvalue().splitlines()[-10:],
              "recordConflicts": recd.conflicts, "unexpectedLoads": sorted(set(
                  "%s(%s)" % (l, p[-6:]) for l, p in recd.store_reads if l == "load_goals"))}
    guards_ok = (not guards["spawns"] and not guards["refusedWrites"] and not guards["threadStarts"]
                 and not guards["warmCalls"] and not guards["absentSids"] and not any(guards["notices"].values())
                 and not guards["defaultStrDelta"] and not guards["buildStderr"] and not guards["recordConflicts"]
                 and not guards["unexpectedLoads"])
    shapes = census(feed, sids)
    fold_cards = sum(len((json.loads(js) or {}).get("asks") or []) for js in entries.values())
    checks = {
        "replayFresh": fresh,
        "replayInProcess": inproc,
        "liveTracedBuildEqual": traced_equal,
        "guards": guards,
        "census": shapes,
        "boundary": boundary,
        "comparatorCatchesMutation": comparator_catches,
        "everyAliveSidHasEntry": all(s["sid"] in entries for s in alive),
        "allCardsFromTheFold": fold_cards == len(feed["asks"]),
        "answerTypes": {k: sorted(v) for k, v in sorted(recd.types.items())},
        "jsonHazards": json_hazards(json.loads(oracle_wire), "feed") + json_hazards(snap, "snapshot")
                       + [h for sid, t in trees.items() for h in json_hazards(t, "tree:" + sid[-6:])],
    }
    fresh_ok = (bool(fresh) and not fresh.get("error") and fresh.get("canonEqual") and fresh.get("wireEqual")
                and fresh.get("sliceEqual") and fresh.get("entriesEqual")
                and all(v is True for v in (fresh.get("controls") or {"none": False}).values())) if replay else True
    ok = {
        "replayFresh": fresh_ok,
        "replayInProcess": inproc["canonEqual"] and inproc["wireEqual"] and inproc["entriesEqual"],
        "liveTracedBuildEqual": traced_equal,
        "guards": guards_ok,
        "census": all(shapes.values()),
        "boundary": boundary["codePoints"] == jd.CITE_MIN_CHARS - 1 and boundary["utf8Bytes"] >= jd.CITE_MIN_CHARS
                    and bool(boundary.get("mutationMoves")),
        "comparatorCatchesMutation": comparator_catches,
        "everyAliveSidHasEntry": checks["everyAliveSidHasEntry"],
        "allCardsFromTheFold": checks["allCardsFromTheFold"],
        "noJsonHazards": not checks["jsonHazards"],
    }
    leaks = scan_outputs(out, needles)
    ok["noIdentifiers"] = not leaks
    checks["identifierHits"] = leaks
    files, total = file_rows(out, mtimes)
    ok["underSizeCap"] = total < SIZE_CAP
    manifest = {"schema": "romp-spike-manifest/1", "world": snap["world"], "ok": all(ok.values()), "checksOk": ok,
                "checks": checks, "totalBytes": total, "files": files,
                "oracle": {"canonSha256": sha256(oracle_canon), "wireSha256": sha256(oracle_wire),
                           "canonBytes": len(oracle_canon), "cards": len(feed["asks"])}}
    write_text(os.path.join(out, "manifest.json"), json.dumps(manifest, indent=1))
    run.update({"seconds": round(time.time() - t_start, 2), "oracleSetupCpuS": round(t_oracle, 3),
                "oracleBuildCpuMs": round(t_build * 1000, 2)})
    write_text(os.path.join(out, RUN_FILE), json.dumps(run, indent=1))
    if not keep_world:
        shutil.rmtree(os.path.join(out, "work"), ignore_errors=True)
    return manifest, run


def replay_main(out):
    """The fresh-process replay: a new interpreter, an EMPTY state root, the kernel loaded under the same guards, every
    input taken from the dump directory. Writes coverage/port.json and prints one JSON line of results."""
    t0 = time.process_time()
    out = os.path.realpath(out)
    snap, trees = load_dump(out)
    work = os.path.join(out, "work")                 # the empty state root lives in the dump directory too
    made_work = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    root = os.path.realpath(tempfile.mkdtemp(prefix="replay-", dir=work))
    try:
        clean_env(root)
        state = os.path.join(root, "state", "romp")
        claude = os.path.join(root, "claude")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(os.path.join(claude, "projects"), exist_ok=True)
        _bcp, _pb, km, rec = load_romp(root, state, claude)
        mods = {"kernel": km, "judge": km.jd, "event_model": km.em, "colormap": km.cm}
        tr = LineTrace(mods)
        t_build = time.process_time()
        full, raw, rp, entries = replay_build(km, snap, trees, trace=tr)
        t_build = time.process_time() - t_build
        res = compare_replay(out, full, raw, rp, entries)
        res["controls"] = negative_controls(out, km, snap, trees)
        res["guards"] = {"spawns": len(rec["spawns"]), "refusedWrites": len(rec["refused_writes"]),
                         "threadStarts": rec["thread_starts"], "warmCalls": len(rec["warm_calls"])}
        write_text(os.path.join(out, "coverage", "port.json"),
                   json.dumps(tr.report("replay", "the per-card build over the dumped inputs only: the recorded "
                                                  "helpers answered from snapshot.json, an empty state root"),
                              indent=1))
        res["cpu"] = {"tracedBuildS": round(t_build, 3), "totalS": round(time.process_time() - t0, 3)}
    finally:
        shutil.rmtree(work if made_work else root, ignore_errors=True)
    print(json.dumps(res))
    return 0 if (res["canonEqual"] and res["wireEqual"] and res["entriesEqual"]) else 1


def negative_controls(out, km, snap, trees):
    """Planted changes to inputs the designed board depends on, each through the same replay: every one must move the
    per-card slice. They show the replay reads those inputs from the snapshot (a reader that went to the empty state
    root instead would leave the slice unmoved), so its equality is not an accident of empty inputs."""
    with open(os.path.join(out, "oracle", "feed.canon.json"), encoding="utf-8") as f:
        oracle_slice = canon(feed_slice(json.loads(f.read())))
    sids = [r["sid"] for r in snap["alive"]]
    S1, S2, S3 = sids[0], sids[1], sids[2]

    def jactive(s):                                  # s002 judges from the run in flight; without it, the key's gate asks
        s["board"]["jactive"] = []                   # the closer, which Python never asked for s002: answered "settled"
        s["sessions"][S2]["recorded"]["closerPending"] = False

    def nudges(s):
        s["board"]["nudgeRecords"] = {}

    def live_row(s):
        s["liveMap"][S2]["state"] = "idle"

    def rename(s):
        for where in (s["sessions"], s["peers"]):
            if S3 in where and where[S3]["names"]:
                where[S3]["names"][0] = "s003-renamed"

    def closer(s):
        s["sessions"][S1]["recorded"]["closerPending"] = False

    def peer_store(s):                               # s002's origin badge reads its sender's node: complete -> not live
        s["peers"][S1]["store"]["nodes"]["%s:g6" % S1]["nodeComplete"] = True
    res = {}
    for name, fn in (("judge run in flight dropped", jactive), ("nudge record dropped", nudges),
                     ("s002 live row idle", live_row), ("s003 renamed", rename),
                     ("s001 closer answer flipped", closer), ("s001's store as s002's peer: origin node complete",
                                                              peer_store)):
        s2 = copy.deepcopy(snap)
        try:
            fn(s2)
            _full, raw, rp, _e = replay_build(km, s2, trees)
            # a control that only broke the replay proves nothing (2026-11-25: dropping the run in flight first "moved"
            # the slice by faulting s002's derivation on a closer answer Python never recorded)
            if rp.missing or raw.get("absentSids"):
                res[name] = "broke the replay: missing %s, absent %s" % (sorted(set(rp.missing)),
                                                                        raw.get("absentSids"))
            else:
                res[name] = canon(feed_slice(raw)) != oracle_slice
        except Exception as e:                       # noqa: BLE001 - a control that cannot run is a failed control
            res[name] = "error: %r" % e
    return res


def compare_dirs(a, b):
    def listing(d):
        out = {}
        for dp, dn, fn in os.walk(d):
            dn[:] = [x for x in dn if x != "work"]
            for f in fn:
                rel = os.path.relpath(os.path.join(dp, f), d)
                if rel != RUN_FILE:
                    out[rel] = os.path.join(dp, f)
        return out
    la, lb = listing(a), listing(b)
    diffs = sorted(set(la) ^ set(lb))
    same = 0
    for rel in sorted(set(la) & set(lb)):
        with open(la[rel], "rb") as fa, open(lb[rel], "rb") as fb:
            if fa.read() == fb.read():
                same += 1
            else:
                diffs.append(rel)
    return {"files": len(set(la) | set(lb)), "identical": same, "differ": diffs}


def check_main(worlds, out, keep):
    """Two dumps per world in separate processes, each with its own PYTHONHASHSEED, temp root and wall clock; every
    output but run.json must be byte-identical, and each dump's own self-checks (its replay included) must pass."""
    out = os.path.realpath(out)
    tree = in_git_tree(out if os.path.exists(out) else os.path.dirname(out))
    if tree:
        raise DumpError("--out %s is inside the git work tree at %s" % (out, tree))
    os.makedirs(out, exist_ok=True)
    report = {}
    for world in worlds:
        runs = []
        for tag, seed in (("a", "11"), ("b", "29")):
            d = os.path.join(out, world, tag)
            tmp = os.path.join(out, world, "tmp-" + tag)
            os.makedirs(tmp, exist_ok=True)
            env = dict(os.environ, PYTHONHASHSEED=seed, TMPDIR=tmp)
            t0 = time.time()
            proc = subprocess.run([sys.executable, os.path.realpath(__file__), "--world", world, "--out", d],
                                  env=env, capture_output=True, text=True)
            runs.append({"dir": d, "seed": seed, "rc": proc.returncode, "seconds": round(time.time() - t0, 1),
                         "tail": (proc.stdout + proc.stderr).strip().splitlines()[-6:]})
            shutil.rmtree(tmp, ignore_errors=True)
        cmp_ = compare_dirs(runs[0]["dir"], runs[1]["dir"]) if all(os.path.isdir(r["dir"]) for r in runs) else None
        roots = []
        for r in runs:
            try:
                with open(os.path.join(r["dir"], RUN_FILE)) as f:
                    roots.append(json.load(f)["root"])
            except Exception:
                roots.append(None)
        ok = all(r["rc"] == 0 for r in runs) and cmp_ is not None and not cmp_["differ"] and roots[0] != roots[1]
        report[world] = {"ok": ok, "runs": runs, "compare": cmp_, "rootsDiffer": roots[0] != roots[1]}
        if ok and not keep:
            shutil.rmtree(runs[1]["dir"], ignore_errors=True)
    write_text(os.path.join(out, "check.json"), json.dumps(report, indent=1))
    for world, r in report.items():
        c = r["compare"] or {}
        print("check %-5s %s: rc %s, %d/%d files identical, differ %s, seeds %s, %s s" % (
            world, "PASS" if r["ok"] else "FAIL", [x["rc"] for x in r["runs"]], c.get("identical", 0),
            c.get("files", 0), (c.get("differ") or [])[:8], [x["seed"] for x in r["runs"]],
            [x["seconds"] for x in r["runs"]]))
        if not r["ok"]:
            for x in r["runs"]:
                print("  run %s tail:\n    %s" % (x["seed"], "\n    ".join(x["tail"])))
    return 0 if all(r["ok"] for r in report.values()) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", default="small", choices=["small", "large", "both"])
    ap.add_argument("--out", help="output directory (default <tmp>/romp-spike/<world>, or <tmp>/romp-spike-check)")
    ap.add_argument("--keep-world", action="store_true", help="keep the world under <out>/work after the dump")
    ap.add_argument("--no-replay", action="store_true", help="skip the fresh-process replay")
    ap.add_argument("--check", action="store_true", help="two dumps, two seeds, two roots: assert identical outputs")
    ap.add_argument("--keep", action="store_true", help="with --check, keep the second dump of each world")
    ap.add_argument("--replay", metavar="DIR", help="run the fresh-process replay over a dump directory")
    a = ap.parse_args(argv)
    try:
        if a.replay:
            return replay_main(a.replay)
        worlds = ["small", "large"] if a.world == "both" else [a.world]
        if a.check:
            return check_main(worlds, a.out or os.path.join(tempfile.gettempdir(), "romp-spike-check"), a.keep)
        rc = 0
        for w in worlds:
            if a.out and len(worlds) == 1:
                out = a.out
            else:
                out = os.path.join(a.out or os.path.join(tempfile.gettempdir(), "romp-spike"), w)
            manifest, run = dump(w, out, keep_world=a.keep_world, replay=not a.no_replay)
            fr = manifest["checks"]["replayFresh"] or {}
            print("%s: %s, %d cards, oracle %d bytes (sha %s), %d files, %.1f MB, %.1f s, replay fresh canon %s "
                  "wire %s entries %s" % (
                      w, "OK" if manifest["ok"] else "FAILED", manifest["oracle"]["cards"],
                      manifest["oracle"]["canonBytes"], manifest["oracle"]["canonSha256"][:12],
                      len(manifest["files"]), manifest["totalBytes"] / 1e6, run["seconds"], fr.get("canonEqual"),
                      fr.get("wireEqual"), fr.get("entriesEqual")))
            print("  -> %s" % os.path.realpath(out))
            if not manifest["ok"]:
                print("  failed: %s" % [k for k, v in manifest["checksOk"].items() if not v])
                rc = 1
        return rc
    except DumpError as e:
        print("dump_world: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
