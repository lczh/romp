#!/usr/bin/env python3
"""Tail-first pages, step 1 (2026-11-12): the GUARD. A provisional chat page is built from the end of a transcript
alone, and what keeps it from quietly using the rest of the file is a mechanism, not care: inside em.tail_scope(win)
every shared reader of the window's transcript raises em.WholeHistoryRead before it looks at a memo, a cache or the
disk, and the kernel's _piece turns that into a placeholder for the one piece that asked.

Pinned here:
  * each transcript reader refuses the scoped file (and a file the window names through `also`), cold AND after a whole
    build filled every memo, so a memo built from whole history is not served either; another file reads as ever;
  * the parse takes the window's own list by identity and nothing else; the judge's parse store is refused even when it
    holds the tree;
  * WholeHistoryRead is no ordinary error: it passes every `except Exception` arm between a reader and the piece that
    asked, only _piece and the shadow's job name it, and no handler in the three kernel files could swallow it;
  * the scope is the calling thread's, and ends on exit and on a raise;
  * the provisional build keeps nothing: no parse stored, no fold, no reader entry, no page cache, no checkpoint, and it
    is the same frame cold as warm;
  * a window reaches a scope only through em.tail_open, whose gates (an armed rollback, another file of the conversation
    written inside the window, the high-water stamps) cannot be skipped by building from tail_window's answer;
  * the kernel's memos derived from whole parses (the message-caption map, the task store's content-join answer) are
    refused inside the scope, warm or cold, and the caption map's union never misses a submap a call left stored;
  * the shadow is off unless STATE/tail-first-shadow says so, compares once per turn end and transcript state, waits
    for the background gate before each step, claims no foreground of its own, and its thread ends with the loops.
Synthetic transcripts only."""
import ast
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from romp_load import load_source
HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_tail_first_guard", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

NOW = 1781300000
T0 = NOW - 6 * 3600
SID = "aaaaaaaa-7101-4222-8333-666666666666"
OTHER = "aaaaaaaa-7102-4222-8333-666666666666"
MODEL = "claude-synthetic-a"


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def U(t, text, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def A(t, uuid, parent, text="", tool=None, stop="end_turn"):
    content = ([{"type": "text", "text": text}] if text else []) + (
        [{"type": "tool_use", "id": "tu_" + uuid, "name": tool, "input": {"command": "true"}}] if tool else [])
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "id": "msg_" + uuid, "model": MODEL, "content": content, "stop_reason": stop,
                        "usage": {"input_tokens": 100, "output_tokens": 20}}}


def R(t, uuid, parent, tool_uuid):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + tool_uuid, "content": "ok"}]}}


def transcript(tag="", turns=8):
    """`turns` plain turns of the notes-api work, each a prompt, a tool call, its result and a reply."""
    recs, prev = [], None
    for i in range(turns):
        t = T0 + i * 100
        u, a, r, z = ("%s%s%d" % (k, tag, i) for k in "uarz")
        recs += [U(t, "step %d of the tag filter" % i, u, prev), A(t + 10, a, u, tool="Bash", stop="tool_use"),
                 R(t + 20, r, a, a), A(t + 30, z, r, text="step %d done" % i)]
        prev = z
    return recs


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.saved_state = jd.STATE
        jd._rebind_state(self.td / "state")
        for d in ("states", "goals", "sdk", "checkpoints", "timeline"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")                 # a state root of this test's own: no session host
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        self.proj = self.td / "proj"
        self.proj.mkdir()
        self.leaf = str(self.proj / (SID + ".jsonl"))
        self.other = str(self.proj / (OTHER + ".jsonl"))
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in transcript()))
        Path(self.other).write_text("".join(json.dumps(r) + "\n" for r in transcript("o")))
        self.rows = [{"sid": SID, "name": "web", "path": self.leaf, "mtime": NOW, "anchor": SID},
                     {"sid": OTHER, "name": "api", "path": self.other, "mtime": NOW, "anchor": OTHER}]
        self.saved = (km._sessions, km._live_map)
        km._sessions = lambda now, **kw: list(self.rows)
        km._live_map = lambda: {}
        self.fresh()
        self.reset_shadow()
        self.win, why = em.tail_open(self.leaf, files=[self.leaf], cut_armed=False, min_events=6)
        self.assertIsNotNone(self.win, why)

    def tearDown(self):
        km._sessions, km._live_map = self.saved
        self.reset_shadow()
        em.set_checkpoint_dir(None)
        jd._rebind_state(self.saved_state)
        shutil.rmtree(self.td, ignore_errors=True)

    def reset_shadow(self):
        with km._TAIL_FIRST_LOCK:
            km._TAIL_FIRST_QUEUE.clear()
            km._TAIL_FIRST_STATS.update(offered=0, dropped=0, unmoved=0, attempts=0, refused={}, skipped={}, matched=0,
                                        mismatched=0, ms=0.0, msMax=0.0)
            km._TAIL_FIRST_ENDS.clear()
            km._TAIL_FIRST_LAST.clear()
        km._TAIL_FIRST_SAID.clear()
        km._TAIL_FIRST_WAKE.clear()

    def turn_ended(self, sid, at):
        """The Stop hook's stamp of a turn end on the session's registry entry (lastStopAt), the event the offer keys on."""
        (jd.STATE / "sdk" / (sid + ".json")).write_text(json.dumps({"lastStopAt": int(at)}))

    def fresh(self):
        """A kernel restart's in-memory side (the caches a whole build fills)."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        em._FILE_HEAD_T.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear()
            em._HYDRATED_BYTES[0] = 0
        em._LAZY_FILES.clear()
        with km._chat_fold_lock:
            km._chat_fold.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._parse_mode.clear()
        km._built_chat.clear()
        km._prev_chat_events.clear()
        km._RENDER_FLOOR.clear()
        with em._TAIL_STAMPS_LOCK:
            em._TAIL_STAMPS.clear()

    def warm(self):
        """A whole build of the session and a read through each memoizing reader: every memo a whole read fills is full."""
        self.assertIsNotNone(km.build_session(SID, NOW, {}, floor=0))
        self.assertEqual(len(em.cached_whole_records(self.leaf) or ()), 32, "the reader holds the file's records")
        self.assertTrue(jd._PARSE_CACHE, "the whole build stored its parse")
        self.assertIsNotNone(em._file_head_t(self.leaf))

    def readers(self, path):
        """Every shared transcript reader, as a call over `path`: (name, thunk)."""
        lazy = {"uuid": "u0", "lazy": {"at": (0, 10)}, "message": em._LazyBody("u0", path)}
        return [
            ("_read_jsonl", lambda: list(em._read_jsonl(path))),
            ("_read_jsonl_incremental", lambda: em._read_jsonl_incremental(path)),
            ("_read_jsonl_entry", lambda: em._read_jsonl_entry(path)),
            ("_read_jsonl_entry_unlocked", lambda: em._read_jsonl_entry_unlocked(path)),
            ("cached_whole_records", lambda: em.cached_whole_records(path)),
            ("read_records_detached", lambda: em.read_records_detached(path)),
            ("serve_records", lambda: em.serve_records({path: []}).__enter__()),
            ("fold_records", lambda: em.fold_records({}, path, lambda: 0, lambda s, r: s + 1)),
            ("_read_record_at", lambda: em._read_record_at(path, (0, 10), "u0")),
            ("hydrate", lambda: em.hydrate([lazy])),
            ("_assemble", lambda: em._assemble(path, [path], {}, SID, {}, False, None)),
            ("_file_head_t", lambda: em._file_head_t(path)),
            ("parse_session", lambda: em.parse_session(path)),
            ("jd.parsed_session", lambda: jd.parsed_session(os.path.basename(path)[:-6], [path], NOW)),
            ("jd.parsed_session_peek", lambda: jd.parsed_session_peek(os.path.basename(path)[:-6], [path])),
            ("tail_stamps", lambda: em.tail_stamps(path)),
            ("tail_open", lambda: em.tail_open(path, files=[path], cut_armed=False)),
        ]


class TestEveryReaderRefusesTheScopedFile(Harness):
    def refused(self, path, when):
        for name, call in self.readers(path):
            with em.tail_scope(self.win):
                with self.assertRaises(em.WholeHistoryRead, msg="%s, %s" % (name, when)) as cm:
                    call()
            self.assertIn(name.split(".")[-1], str(cm.exception), "the refusal names its reader")

    def test_a_whole_history_read_inside_the_scope_raises(self):
        self.refused(self.leaf, "cold")

    def test_a_memo_built_from_whole_history_is_not_served_inside_the_scope(self):
        """The hard spot: after a whole build the reader's entry, the assembly entry, the head stamp, the fold cursors and
        the judge's parse store all hold answers for this file. Inside the scope none is served: the guard is each reader's
        first statement, ahead of its lookup."""
        self.warm()
        with em._JSONL_CACHE_LOCK:
            self.assertIn(self.leaf, {str(k) for k in em._JSONL_CACHE}, "the reader holds the file's records")
        self.refused(self.leaf, "after a whole build")
        # ...and a fold whose cursor already stands at the file's end (a pure memo hit outside the scope)
        cache = {}
        step = lambda s, r: s + 1
        n = em.fold_records(cache, self.leaf, lambda: 0, step)
        self.assertEqual(n, 32)
        self.assertEqual(em.fold_records(cache, self.leaf, lambda: 0, step), 32, "outside the scope: the memo answers")
        with em.tail_scope(self.win):
            with self.assertRaises(em.WholeHistoryRead):
                em.fold_records(cache, self.leaf, lambda: 0, step)

    def test_the_guard_is_ahead_of_the_memo_by_position(self):
        """Statically: in every reader, the guard call is the function's first statement after its docstring (the two that
        guard inside a loop over files, serve_records and hydrate, guard before that file's first read)."""
        tree = ast.parse(open(os.path.join(ROOT, "kernel", "event_model.py")).read())
        first, later = set(), set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name == "_tail_guard":
                continue
            body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
            calls = [n for s in fn.body for n in ast.walk(s) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == "_tail_guard"]
            own = [c for c in calls if not any(c in ast.walk(g) for g in ast.walk(fn)
                                               if isinstance(g, (ast.FunctionDef, ast.AsyncFunctionDef)) and g is not fn)]
            if not own:
                continue
            head = body[0] if body else None
            is_first = isinstance(head, ast.Expr) and head.value in own
            (first if is_first else later).add(fn.name)
        self.assertEqual(first, {"_read_jsonl", "_read_jsonl_incremental", "_read_jsonl_entry", "_read_jsonl_entry_unlocked",
                                 "cached_whole_records", "read_records_detached", "fold_records", "_read_record_at",
                                 "_assemble", "_file_head_t", "tail_stamps", "tail_open"})
        self.assertEqual(later, {"__enter__", "hydrate"}, "serve_records.__enter__ and hydrate guard per file, before its read")

    def test_another_file_reads_as_ever_inside_the_scope(self):
        with em.tail_scope(self.win):
            self.assertEqual(len(list(em._read_jsonl(self.other))), 32)
            self.assertEqual(len(em.read_records_detached(self.other)), 32)
            self.assertIsNotNone(em._file_head_t(self.other))
            self.assertEqual(em.fold_records({}, self.other, lambda: 0, lambda s, r: s + 1), 32)
            tree = em.parse_session(self.other)
            self.assertEqual(len(tree["turns"]), 8)
        self.assertIsNotNone(km.build_session(OTHER, NOW, {}, floor=0))

    def test_a_file_the_window_names_is_refused_too(self):
        """The session's other transcript files (a conversation resumed into a second file) join the scope through `also`."""
        self.win.also([self.other])
        self.assertTrue(self.win.holds(self.other))
        self.refused(self.other, "a named sibling file")

    def test_a_path_spelled_another_way_is_the_same_file(self):
        link = str(self.td / "link.jsonl")
        os.symlink(self.leaf, link)
        dotted = os.path.join(os.path.dirname(self.leaf), ".", os.path.basename(self.leaf))
        with em.tail_scope(self.win):
            for p in (link, dotted, Path(self.leaf)):
                with self.assertRaises(em.WholeHistoryRead):
                    em.cached_whole_records(p)

    def test_outside_a_scope_nothing_changes(self):
        for name, call in self.readers(self.leaf):
            if name in ("_read_record_at", "hydrate", "serve_records"):     # (their arguments here are stand-ins)
                continue
            call()
        self.assertIsNone(em.tail_scope_window())


class TestTheParseTakesTheWindowsOwnRecords(Harness):
    def test_only_the_windows_list_by_identity(self):
        with em.tail_scope(self.win):
            with self.assertRaises(em.WholeHistoryRead):
                em.parse_session(self.leaf)                                    # the whole file
            with self.assertRaises(em.WholeHistoryRead):
                em.parse_session(self.leaf, tail_records=list(self.win.records))   # an equal list is not the window's
            with self.assertRaises(em.WholeHistoryRead):
                em.parse_session(self.leaf, tail_records=transcript())         # the whole file, handed in as a "tail"
            tree = em.parse_session(self.leaf, tail_records=self.win.records)
        self.assertEqual((tree["turns"][0]["trigger"] or {}).get("uuid"), self.win.uuid)

    def test_tail_parse_opens_at_the_boundary_and_stores_nothing(self):
        kept = sorted(os.listdir(jd.STATE / "checkpoints"))
        self.assertEqual([n for n in kept if not n.endswith(".tail-stamps")], [], "only the high-water entry tail_open keeps")
        tree = em.tail_parse(self.win, rompuuid=SID, now=NOW, postal_log=str(jd.MESSAGES))   # this root's log, as the kernel passes
        self.assertEqual([t["trigger"]["uuid"] for t in tree["turns"]], ["u6", "u7"])
        self.assertIsNone(em.tail_scope_window(), "the scope ended with the parse")
        with em._JSONL_CACHE_LOCK:
            self.assertFalse(em._JSONL_CACHE, "no reader entry")
        with em._ASM_LOCK:
            self.assertFalse(em._ASM_CACHE, "no assembly entry")
        self.assertEqual(sorted(os.listdir(jd.STATE / "checkpoints")), kept, "no checkpoint")

    def test_the_judges_parse_store_is_refused_even_when_it_holds_the_tree(self):
        self.warm()
        tree, key, cands = jd.parsed_session_peek(SID, [self.leaf], states=str(jd.STATE / "states" / (SID + ".jsonl")),
                                                  sdk_human=km._display_sdk_human(SID))
        self.assertIsNotNone(tree, "outside the scope the store answers")
        with em.tail_scope(self.win):
            with self.assertRaises(em.WholeHistoryRead):
                jd.parsed_session(SID, [self.leaf], NOW)
            with self.assertRaises(em.WholeHistoryRead):
                jd.parsed_session_peek(SID, [self.leaf])

    def test_a_window_is_the_files_own_bytes(self):
        """What tail_window hands over: the records from the boundary to the end, read at the stat it names."""
        st = os.stat(self.leaf)
        self.assertEqual(self.win.stat, (st.st_mtime_ns, st.st_size, st.st_ino))
        self.assertEqual((self.win.uuid, self.win.prev_uuid, self.win.prev_model, self.win.n), ("u6", "z5", MODEL, 8))
        with open(self.leaf, "rb") as fh:
            fh.seek(self.win.start)
            self.assertEqual([json.loads(ln) for ln in fh], self.win.records)
        self.assertEqual(self.win.end, st.st_size)


class TestWholeHistoryReadIsNoOrdinaryError(Harness):
    def test_it_passes_an_except_exception_arm(self):
        self.assertFalse(issubclass(em.WholeHistoryRead, Exception))

        def builder_with_a_default():                    # the shape of the builders between a reader and its piece
            try:
                return len(em.read_records_detached(self.leaf))
            except Exception:
                return 0                                 # a guess shown as fact, were the refusal an Exception
        self.assertEqual(builder_with_a_default(), 32)
        with em.tail_scope(self.win):
            with self.assertRaises(em.WholeHistoryRead):
                builder_with_a_default()

    def test_piece_is_the_real_value_or_a_placeholder(self):
        fn = lambda: len(em.read_records_detached(self.leaf))
        self.assertEqual(km._piece("recordCount", fn), 32)
        with em.tail_scope(self.win):
            got = km._piece("recordCount", fn)
            self.assertIsInstance(got, km._Pending)
            self.assertEqual(got, {"pending": "recordCount"})
            self.assertEqual(json.loads(json.dumps(got)), {"pending": "recordCount"})
            self.assertEqual(km._piece("fromAStore", lambda: {"pending": "no"}), {"pending": "no"})
            self.assertNotIsInstance(km._piece("fromAStore", lambda: {"pending": "no"}), km._Pending,
                                     "a real value shaped like a placeholder is told from one by its type")
            with self.assertRaises(ValueError):          # nothing else is caught: a broken piece is still a loud failure
                km._piece("broken", lambda: int("x"))

    def test_no_handler_in_the_kernel_could_swallow_it(self):
        """A census. em.WholeHistoryRead is a BaseException so the `except Exception` arms cannot answer it with a default;
        that holds only while nothing on a build's path catches BaseException or everything. The event model and the judge
        hold no such handler; the kernel's sit in functions no chat build runs through; and the refusal is caught BY NAME
        in two places, the piece wrapper and the shadow's job."""
        allowed = {"kernel.py": {"_claim_session_name", "_drive", "_start_remote", "_save_file"}, "event_model.py": set(),
                   "judge.py": set()}
        named = {}
        for fname, ok in allowed.items():
            tree = ast.parse(open(os.path.join(ROOT, "kernel", fname)).read())
            owner = {}
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for n in ast.walk(fn):
                        if isinstance(n, ast.ExceptHandler):
                            owner[n] = fn.name            # the innermost function wins: ast.walk reaches it last
            wide = set()
            for h in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
                types = [h.type] if h.type is not None and not isinstance(h.type, ast.Tuple) else list(getattr(h.type, "elts", []))
                names = {ast.unparse(t) for t in types}
                if h.type is None or "BaseException" in names:
                    wide.add(owner.get(h, "<module>"))
                if any(n.endswith("WholeHistoryRead") for n in names):
                    named.setdefault(fname, set()).add(owner.get(h, "<module>"))
            self.assertEqual(wide, ok, "%s: handlers that catch BaseException or everything" % fname)
        self.assertEqual(named, {"kernel.py": {"_piece", "_tail_first_shadow_job"}})


class TestTheScopeIsTheCallingThreads(Harness):
    def test_another_thread_reads_as_ever(self):
        got = {}

        def reader():
            got["window"] = em.tail_scope_window()
            got["n"] = len(em.read_records_detached(self.leaf))
        with em.tail_scope(self.win):
            self.assertIs(em.tail_scope_window(), self.win)
            th = threading.Thread(target=reader)
            th.start()
            th.join(10)
        self.assertEqual(got, {"window": None, "n": 32})

    def test_it_ends_on_exit_on_a_raise_and_nests(self):
        self.assertIsNone(em.tail_scope_window())
        with self.assertRaises(em.WholeHistoryRead):
            with em.tail_scope(self.win):
                em.read_records_detached(self.leaf)
        self.assertIsNone(em.tail_scope_window(), "a refusal that escaped the block ended the scope")
        self.assertEqual(len(em.read_records_detached(self.leaf)), 32)
        other, why = em.tail_open(self.other, files=[self.other], cut_armed=False, min_events=2)
        with em.tail_scope(self.win):
            with em.tail_scope(other):
                self.assertIs(em.tail_scope_window(), other)
                self.assertEqual(len(em.read_records_detached(self.leaf)), 32, "the inner scope holds the other file")
            self.assertIs(em.tail_scope_window(), self.win)
            with em.tail_scope(None):
                self.assertEqual(len(em.read_records_detached(self.leaf)), 32)
        self.assertIsNone(em.tail_scope_window())


class TestTheProvisionalBuildKeepsNothing(Harness):
    def stores(self):
        with em._JSONL_CACHE_LOCK:
            readers = {str(k): id(v) for k, v in em._JSONL_CACHE.items()}
        with em._ASM_LOCK:
            asm = {repr(k): id(v) for k, v in em._ASM_CACHE.items()}
        with km._chat_fold_lock:
            fold = {k: id(v) for k, v in km._chat_fold.items()}
        return {"readers": readers, "assembly": asm, "fold": fold, "parses": {repr(k): id(v) for k, v in jd._PARSE_CACHE.items()},
                "built": dict(km._built_chat), "prev": {k: id(v) for k, v in km._prev_chat_events.items()},
                "floor": dict(km._RENDER_FLOOR), "mode": dict(km._parse_mode),
                "checkpoints": sorted(os.listdir(jd.STATE / "checkpoints")),
                "headStamps": dict(em._FILE_HEAD_T)}

    def test_cold_it_fills_no_cache(self):
        before = self.stores()
        self.assertEqual({k: v for k, v in before.items() if v and k != "checkpoints"}, {}, "a kernel just restarted")
        self.assertEqual([n for n in before["checkpoints"] if not n.endswith(".tail-stamps")], [],
                         "nothing but the high-water entry tail_open kept, before the build")
        prov = km._tail_first_frame(SID, NOW, {}, self.win)
        self.assertEqual([e["uuid"] for e in prov["events"] if e.get("kind") == "user"], ["u6", "u7"])
        self.assertEqual(self.stores(), before, "no parse, fold, reader entry, page cache or checkpoint was written")

    def test_warm_it_changes_no_cache_and_reads_none(self):
        """The same frame cold as warm: nothing a whole build left behind reaches the provisional one."""
        cold = json.dumps(km._tail_first_frame(SID, NOW, {}, self.win), sort_keys=True)
        self.warm()
        before = self.stores()
        self.assertTrue(before["readers"] and before["parses"])
        warm = json.dumps(km._tail_first_frame(SID, NOW, {}, self.win), sort_keys=True)
        self.assertEqual(warm, cold)
        self.assertEqual(self.stores(), before)

    def test_the_frame_declares_what_it_does_not_know(self):
        prov = km._tail_first_frame(SID, NOW, {}, self.win)
        self.assertEqual(prov["type"], "session")
        self.assertEqual(prov["wm"], {"leaf": self.leaf, "tail": [self.win.stat[0], self.win.stat[1]], "from": self.win.start})
        self.assertLessEqual({"turnNumbers", "ledger", "firstSeen", "scrollBack", "status", "queued", "liveTail",
                              "bgTasks", "system"}, set(prov["provisional"]))
        for name in ("bgTasks", "system"):                                # folds over the whole transcript
            self.assertEqual(prov[name], {"pending": name})
        for name in ("episodes", "forks", "flags"):                       # from stores
            self.assertNotIsInstance(prov[name], km._Pending, name)
        for e in prov["events"]:
            self.assertNotIn("turn", e, "no absolute turn number")
        self.assertEqual(sorted({e["tailTurn"] for e in prov["events"] if "tailTurn" in e}), [0, 1])

    def test_the_real_build_is_what_it_was(self):
        """The provisional build's branches are behind `tail_first`: a whole build before and after one is the same."""
        a = json.dumps(km.build_session(SID, NOW, {}, floor=0), sort_keys=True, default=str)
        km._tail_first_frame(SID, NOW, {}, self.win)
        self.fresh()
        b = json.dumps(km.build_session(SID, NOW, {}, floor=0), sort_keys=True, default=str)
        self.assertEqual(a, b)
        self.assertNotIn("tailTurn", a)
        self.assertNotIn("provisional", a)
        self.assertNotIn('"pending"', a)


class TestTheSwitch(Harness):
    def flip(self, word):
        p = jd.STATE / km.TAIL_FIRST_FILE
        if word is None:
            if p.exists():
                p.unlink()
        else:
            p.write_text(word)
            os.utime(p, ns=(time.time_ns(), time.time_ns()))

    def test_off_unless_the_file_says_so(self):
        self.assertEqual(km.TAIL_FIRST_FILE, "tail-first-shadow")
        self.assertEqual(km._tail_first_mode(), "off", "no file: off")
        self.assertFalse(km._tail_first_offer(SID))
        self.assertEqual(km._tail_first_report()["queued"], 0)
        for word in ("", "off\n", "0", "no", "maybe", "serve"):
            self.flip(word)
            self.assertEqual(km._tail_first_mode(), "off", repr(word))
            self.assertFalse(km._tail_first_offer(SID))
        for word in ("on\n", "ON", "shadow", "1", "true"):
            self.flip(word)
            self.assertEqual(km._tail_first_mode(), "shadow", repr(word))
        self.assertFalse(km._tail_first_offer(SID), "no turn has ended: nothing to compare yet")
        self.turn_ended(SID, NOW - 50)
        self.assertTrue(km._tail_first_offer(SID))
        self.turn_ended(SID, NOW - 10)
        self.assertTrue(km._tail_first_offer(SID), "a session already waiting is offered again at its next turn end, and waits once")
        rep = km._tail_first_report()
        self.assertEqual((rep["mode"], rep["offered"], rep["queued"], rep["attempts"]), ("shadow", 2, 1, 0))
        self.assertTrue(km._TAIL_FIRST_WAKE.is_set(), "an offer wakes the shadow's thread")
        self.flip(None)
        self.assertEqual(km._tail_first_mode(), "off", "the file removed: off again, no restart")
        self.assertFalse(km._tail_first_offer(OTHER))

    def test_the_report_block(self):
        self.assertEqual(set(km._tail_first_report()), {"mode", "offered", "dropped", "unmoved", "queued", "attempts", "refused",
                                                        "skipped", "matched", "mismatched", "ms", "msMax"})
        self.warm()
        self.assertEqual(km._tail_first_shadow_job(SID, now=NOW, live_map={}, min_events=6), ("matched", None))
        self.assertEqual(km._tail_first_shadow_job(SID, now=NOW, live_map={}, min_events=400), ("refused", "too-short"))
        self.fresh()
        self.assertEqual(km._tail_first_shadow_job(SID, now=NOW, live_map={}, min_events=6), ("skipped", "no-tree"),
                         "no whole build to compare against: the shadow parses nothing of its own")
        self.assertEqual(jd._PARSE_CACHE, {})
        self.assertEqual(km._tail_first_shadow_job("aaaaaaaa-7199-4222-8333-666666666666", now=NOW, live_map={}),
                         ("skipped", "no-transcript"))
        rep = km._tail_first_report()
        self.assertEqual((rep["attempts"], rep["matched"], rep["mismatched"]), (4, 1, 0))
        self.assertEqual((rep["refused"], rep["skipped"]), ({"too-short": 1}, {"no-tree": 1, "no-transcript": 1}))
        self.assertGreater(rep["ms"], 0)
        self.assertGreaterEqual(rep["ms"], rep["msMax"])
        json.dumps(rep)

    def test_a_comparison_follows_a_turn_end_never_a_streaming_build(self):
        """Every chat build offers; only the first one after a turn end joins the queue. A comparison re-walks the history
        before the window, so it is paid once per turn, not once per build of a streaming turn."""
        self.flip("on")
        for _ in range(5):
            self.assertFalse(km._tail_first_offer(SID), "builds while the first turn streams")
        self.turn_ended(SID, NOW - 100)
        self.assertTrue(km._tail_first_offer(SID))
        for _ in range(5):
            self.assertFalse(km._tail_first_offer(SID), "builds after that end, the next turn streaming")
        self.turn_ended(SID, NOW - 10)
        self.assertTrue(km._tail_first_offer(SID))
        self.assertEqual(km._tail_first_report()["offered"], 2)

    def test_one_comparison_per_transcript_state(self):
        """A second turn end that wrote nothing to the transcript (a stop on the same bytes) is counted, not compared."""
        self.assertTrue(km._tail_first_due(SID))
        self.assertFalse(km._tail_first_due(SID))
        self.assertEqual(km._tail_first_report()["unmoved"], 1)
        with open(self.leaf, "a") as fh:
            fh.write(json.dumps(U(T0 + 900, "step 9 of the tag filter", "u9", "z7")) + "\n")
        self.assertTrue(km._tail_first_due(SID))
        self.assertTrue(km._tail_first_due(OTHER), "per session")

    def test_the_queue_is_bounded(self):
        self.flip("on")
        for i in range(km._TAIL_FIRST_QUEUE_MAX + 5):
            self.turn_ended("sid-%d" % i, NOW - 10)
            km._tail_first_offer("sid-%d" % i)
        rep = km._tail_first_report()
        self.assertEqual((rep["queued"], rep["dropped"]), (km._TAIL_FIRST_QUEUE_MAX, 5))
        self.assertEqual(next(iter(km._TAIL_FIRST_QUEUE)), "sid-5", "the oldest waiting ones were dropped")

    def test_perf_carries_the_block(self):
        src = open(os.path.join(ROOT, "kernel", "kernel.py")).read()
        self.assertIn('"tailFirst": _tail_first_report()', src)

    def test_the_reference_documents_the_block_and_the_switch(self):
        doc = open(os.path.join(ROOT, "docs", "reference.md"), encoding="utf-8").read()
        at = doc.index("- `tailFirst`:")
        entry = " ".join(doc[at:doc.index("\n- `", at + 1)].split())
        for key in km._tail_first_report():
            self.assertIn("`%s`" % key, entry, "the /perf block's key is in its entry")
        self.assertIn("`%s`" % km.TAIL_FIRST_FILE, entry)
        for word in km._TAIL_FIRST_ON_WORDS:
            self.assertIn("`%s`" % word, entry, "an on word")
        self.assertIn("serves nothing", entry)


class TestOneEntryPoint(Harness):
    """em.tail_open is the only way to a window a scope takes: the gates that need more than the records in hand live there,
    behind one call, and the scope refuses a window that did not come through it."""

    def test_a_window_the_search_hands_out_is_refused_by_the_scope(self):
        win, why = em.tail_window(self.leaf, 6)
        self.assertIsNone(why)
        with self.assertRaises(em.TailRefused) as cm:
            with em.tail_scope(win):
                pass
        self.assertEqual(cm.exception.reason, "unopened")
        self.assertIsNone(em.tail_scope_window())
        for build in (lambda: km._tail_first_frame(SID, NOW, {}, win), lambda: em.tail_parse(win, rompuuid=SID, now=NOW)):
            with self.assertRaises(em.TailRefused) as cm:
                build()
            self.assertEqual(cm.exception.reason, "unopened")
        self.assertTrue(self.win.opened, "tail_open's window")

    def test_tail_open_runs_every_gate_and_names_the_files(self):
        with self.assertRaises(TypeError):
            em.tail_open(self.leaf)                                    # the files and the rollback state are stated, always
        self.assertEqual(em.tail_open(self.leaf, files=[self.leaf], cut_armed=True, min_events=6), (None, "pending-cut"))
        first = str(self.proj / "first.jsonl")                         # the conversation's earlier file, all of it an hour older
        recs = transcript("f", turns=2)
        for r in recs:
            r["timestamp"] = iso(em.parse_z(r["timestamp"]) - 3600)
        Path(first).write_text("".join(json.dumps(r) + "\n" for r in recs))
        os.utime(first, (T0 + 650, T0 + 650))                          # written after the boundary's stamp
        self.assertEqual(em.tail_open(self.leaf, files=[self.leaf, first], cut_armed=False, min_events=6),
                         (None, "sibling-file-active"))
        os.utime(first, (T0 - 60, T0 - 60))                            # last written before it
        win, why = em.tail_open(self.leaf, files=[self.leaf, first], cut_armed=False, min_events=6)
        self.assertIsNone(why)
        self.assertTrue(win.holds(first), "the scope refuses the other file too")
        with em.tail_scope(win):
            with self.assertRaises(em.WholeHistoryRead):
                em.read_records_detached(first)
        os.utime(self.other, (T0 - 60, T0 - 60))                       # a file whose records are stamped inside the window
        self.assertEqual(em.tail_open(self.leaf, files=[self.leaf, self.other], cut_armed=False, min_events=6),
                         (None, "sibling-high-water"))

    def test_the_kernels_road_reads_the_rollback_and_the_files(self):
        saved = jd._PENDING_CUT_FN
        jd._PENDING_CUT_FN = lambda fsid: "a1" if fsid == SID else ""
        try:
            self.assertEqual(km._tail_first_open(SID, self.leaf, 6), (None, "pending-cut"))
        finally:
            jd._PENDING_CUT_FN = saved
        win, why = km._tail_first_open(SID, self.leaf, 6)
        self.assertEqual((win.uuid, why, win.opened), ("u6", None, True))
        src = open(os.path.join(ROOT, "kernel", "kernel.py")).read()
        job = src[src.index("def _tail_first_shadow_job("):src.index("def _tail_first_mismatch(")]
        self.assertIn("_tail_first_open(sid, path, min_events)", job, "the shadow takes its window from the one road")
        for gone in ("em.tail_window(", "_pending_cut(", ".also("):
            self.assertNotIn(gone, job, "no gate of the road's is the job's own")


class TestTheKernelsDerivedMemos(Harness):
    """Memos the kernel keeps of whole parses: inside a scope they are refused before they are looked at, warm or cold,
    so a provisional build shows the piece pending whatever an earlier build left behind."""

    def test_the_caption_map_is_refused_inside_the_scope_even_when_current(self):
        km._msg_sum_cache.clear()
        saved = km._msg_sum_scan_session
        km._msg_sum_scan_session = lambda sid, path, now: {"m-%s" % sid[:4]: "a synthetic caption"}
        try:
            warm = km._msg_summaries()
            self.assertTrue(warm, "outside the scope the map answers")
            table = dict(km._msg_sum_cache["per"])
            with em.tail_scope(self.win):
                with self.assertRaises(em.WholeHistoryRead):
                    km._msg_summaries()
                km._live_scope.msgsum = [warm]                         # a pusher cycle's slot, already fetched
                try:
                    with self.assertRaises(em.WholeHistoryRead):
                        km._msg_summaries_scoped()
                finally:
                    del km._live_scope.msgsum
            self.assertEqual(km._msg_sum_cache["per"], table, "the refusal touched nothing")
            self.assertIs(km._msg_summaries(), warm)
        finally:
            km._msg_sum_scan_session = saved
            km._msg_sum_cache.clear()

    def test_the_caption_maps_union_never_misses_a_stored_submap(self):
        """A call that leaves early (a BaseException past the scan's `except Exception`) after one session's submap moved:
        the next call's union holds it, even when that next call itself changes nothing (the session that raised had no
        submap yet, and is gone)."""
        class Stop(BaseException):
            pass
        km._msg_sum_cache.clear()
        caps = {SID: "first caption"}
        saved = (km._msg_sum_scan_session, km._msg_sum_key, self.rows)
        km._msg_sum_key = lambda s: caps.get(s["sid"])

        def scan(sid, path, now):
            if sid == OTHER:
                raise Stop()
            return {"m-" + sid: caps[sid]}
        km._msg_sum_scan_session = scan
        try:
            self.rows = [r for r in saved[2] if r["sid"] == SID]
            self.assertEqual(km._msg_summaries(), {"m-" + SID: "first caption"})
            caps[SID] = "first caption, revised"
            self.rows = list(saved[2])                                 # a second session appears, and its first scan raises
            with self.assertRaises(Stop):
                km._msg_summaries()
            self.rows = [r for r in saved[2] if r["sid"] == SID]       # and it has ended: this call changes nothing
            self.assertEqual(km._msg_summaries(), {"m-" + SID: "first caption, revised"})
        finally:
            km._msg_sum_scan_session, km._msg_sum_key, self.rows = saved
            km._msg_sum_cache.clear()

    def test_the_task_stores_content_join_answer_is_refused_inside_the_scope(self):
        base = self.td / "claude"
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(base)
        (base / "tasks" / "team-synthetic").mkdir(parents=True)
        km._task_dir_hint[SID] = "team-synthetic"                      # what an earlier whole build's join found
        try:
            self.assertEqual(km._task_store_known(SID), base / "tasks" / "team-synthetic")
            with em.tail_scope(self.win):
                with self.assertRaises(em.WholeHistoryRead):
                    km._task_store_known(SID)
                prov = km._tail_first_pieces(SID, self.leaf, {}, None, only={"todo"})
            self.assertEqual(prov["todo"], {"pending": "todo"})
            (base / "tasks" / SID).mkdir()
            with em.tail_scope(self.win):
                self.assertEqual(km._task_store_known(SID), base / "tasks" / SID, "a store named after the session is no join")
        finally:
            km._task_dir_hint.pop(SID, None)
            if saved is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved


class TestTheGuardsOwnCost(Harness):
    def test_read_jsonl_refuses_at_the_call(self):
        """A generator's body waits for the first next(): an iterator taken inside the scope and drained outside it would
        have read the whole file. The guard fires when the call is made."""
        with em.tail_scope(self.win):
            with self.assertRaises(em.WholeHistoryRead):
                em._read_jsonl(self.leaf)                              # not iterated
        self.assertEqual(len(list(em._read_jsonl(self.leaf))), 32)

    def test_the_scope_is_a_thread_local_with_a_class_default(self):
        self.assertIsInstance(em._TAIL_SCOPE, threading.local)
        self.assertIsNone(type(em._TAIL_SCOPE).win, "a thread that never opened a scope reads the class's None")
        seen = []
        with em.tail_scope(self.win):
            th = threading.Thread(target=lambda: seen.append(em.tail_scope_window()))
            th.start()
            th.join(10)
        self.assertEqual(seen, [None])
        n = 200000
        t0 = time.perf_counter()
        for _ in range(n):
            em._tail_guard(self.leaf, "probe")
        inactive = (time.perf_counter() - t0) / n * 1e6
        plain = threading.local()
        t0 = time.perf_counter()
        for _ in range(n):
            getattr(plain, "win", None)
        getattr_default = (time.perf_counter() - t0) / n * 1e6
        print("\nthe guard outside a scope: %.3f us a call (getattr with a default on a plain threading.local: %.3f us)"
              % (inactive, getattr_default))


class TestTheShadowNeverRunsBesideABuild(Harness):
    def setUp(self):
        super().setUp()
        self.gate = dict(em._ASM_BG)
        self.stage_fns = (em._READ_STAGE_FN[0], em._SET_STAGE_FN[0])
        em.set_read_stage_provider(km._current_read_stage)   # several kernel loads share one event model in a pytest process:
        em.set_stage_provider(km._set_stage)                 #  the providers must be THIS load's, whose thread-local the marks set

    def tearDown(self):
        em._READ_STAGE_FN[0], em._SET_STAGE_FN[0] = self.stage_fns
        with em._ASM_BG_COND:
            em._ASM_BG.update(self.gate)
            em._ASM_BG_COND.notify_all()
        km._LOOPS_STOP.clear()
        super().tearDown()

    def test_the_job_asks_the_gate_before_each_step_and_stops_when_told(self):
        self.warm()
        calls = []
        self.assertEqual(km._tail_first_shadow_job(SID, now=NOW, live_map={}, min_events=6,
                                                   wait=lambda: calls.append(1) or True), ("matched", None))
        self.assertEqual(len(calls), 3, "before the window's read, the provisional build and the reference build")
        for stop_at in (1, 2, 3):
            seen = []
            self.assertEqual(km._tail_first_shadow_job(SID, now=NOW, live_map={}, min_events=6,
                                                       wait=lambda: seen.append(1) or len(seen) < stop_at),
                             ("skipped", "stopping"), "told to stop at step %d" % stop_at)
            self.assertEqual(len(seen), stop_at)

    def test_the_gate_holds_while_the_pusher_works_or_a_page_builds(self):
        """em.asm_background_wait: open only when the pusher sleeps AND no foreground parse is in flight; every opening wakes
        it (an event, no timer); a stop ends the wait with False."""
        def waiter(out, stop=None):
            out.append(em.asm_background_wait(stop=stop))
        with em._ASM_BG_COND:
            em._ASM_BG.update(idle=False, fg=0)
        got = []
        th = threading.Thread(target=waiter, args=(got,), daemon=True)
        th.start()
        th.join(0.3)
        self.assertTrue(th.is_alive(), "the pusher is working: the shadow waits")
        with em.asm_foreground():
            em.asm_background_idle(True)
            th.join(0.3)
            self.assertTrue(th.is_alive(), "the pusher sleeps but a page's parse is in flight: still waiting")
        th.join(10)
        self.assertEqual(got, [True], "the parse ended: the gate opened and the wait returned")
        em.asm_background_idle(False)
        stop, got = threading.Event(), []
        th = threading.Thread(target=waiter, args=(got, stop), daemon=True)
        th.start()
        th.join(0.3)
        self.assertTrue(th.is_alive())
        stop.set()
        em.asm_rebuild_wait_release()
        th.join(10)
        self.assertEqual(got, [False], "stopped: the wait says so and the job skips")

    def test_the_shadows_parse_claims_no_foreground(self):
        """Under the shadow thread's stage mark the window's parse does not count as a page waiting (it would hold the
        rebuild starter, and the gate the shadow itself waits on, shut); an unmarked caller's does."""
        seen = []
        saved = em._load_states
        em._load_states = lambda states: seen.append(em._ASM_BG["fg"]) or saved(states)
        try:
            log = str(jd.MESSAGES)
            km._stage_marked("warm.tailFirstShadow")(lambda: em.tail_parse(self.win, rompuuid=SID, now=NOW, postal_log=log))()
            em.tail_parse(self.win, rompuuid=SID, now=NOW, postal_log=log)
        finally:
            em._load_states = saved
        self.assertEqual(seen, [self.gate["fg"], self.gate["fg"] + 1])
        self.assertEqual(em._ASM_BG["fg"], self.gate["fg"])

    def test_the_thread_compares_what_the_pusher_offers_and_ends_with_the_loops(self):
        """End to end, the thread main() starts: asleep until an offer, one comparison when the gate is open, none while it
        is shut, and gone when the loops stop."""
        self.warm()
        (jd.STATE / km.TAIL_FIRST_FILE).write_text("on\n")
        saved, km._TAIL_FIRST_MIN_EVENTS = km._TAIL_FIRST_MIN_EVENTS, 6
        with em._ASM_BG_COND:
            em._ASM_BG.update(idle=False, fg=0)                           # the pusher is mid-cycle
        th = threading.Thread(target=km._tail_first_shadow_loop, daemon=True)
        th.start()
        try:
            self.turn_ended(SID, NOW - 10)
            self.assertTrue(km._tail_first_offer(SID))
            deadline = time.time() + 10
            while km._tail_first_report()["attempts"] < 1 and time.time() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)
            rep = km._tail_first_report()
            self.assertEqual((rep["attempts"], rep["matched"], rep["queued"]), (1, 0, 0), "taken off the queue, held at the gate")
            em.asm_background_idle(True)                                  # the cycle ended: the gate opens
            deadline = time.time() + 10
            while km._tail_first_report()["matched"] < 1 and time.time() < deadline:
                time.sleep(0.01)
            rep = km._tail_first_report()
            self.assertEqual((rep["attempts"], rep["matched"], rep["mismatched"]), (1, 1, 0))
            self.assertTrue(km._TAIL_FIRST_LOOP["running"])
        finally:
            km._TAIL_FIRST_MIN_EVENTS = saved
            km._LOOPS_STOP.set()
            em.asm_rebuild_wait_release()
            km._TAIL_FIRST_WAKE.set()
            th.join(10)
        self.assertFalse(th.is_alive(), "the loops' stop ends the thread")
        self.assertFalse(km._TAIL_FIRST_LOOP["running"])


if __name__ == "__main__":
    unittest.main()
