#!/usr/bin/env python3
"""T323 stage 2 (the user 2026-09-10; built 2026-09-11): ONE parse for the kernel and the judges. The judges' cache is
the shared store: the kernel's _parse delegates to jd.parsed_session, so a transcript is parsed once per file version
and its tree is held once, whichever side asked first. The store keys on every fact either side keyed on (the fileset
stat pair over the candidates and the states file, the pending cut, sdk_human), keeps a slot per pending cut and per
leaf transcript so a spent cut's tree goes and an override render of another file under the same sid never evicts
the live leaf's tree, evicts least recently used instead of clearing wholesale,
and answers the kernel's path-keyed readers through a view. Hermetic: synthetic transcripts under a temp root."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_t323s2", os.path.join(BIN, "romp-kernel"))

A = "11111111-2222-4333-8444-000000000201"
B = "22222222-2222-4333-8444-000000000202"


def _transcript(d, sid, n=2, tag=""):
    p = Path(d) / (sid + ".jsonl")
    recs, parent = [], None
    for i in range(n):
        u, a = "%su%d" % (tag, i), "%sa%d" % (tag, i)   # a tag gives a file uuids of its own (a resume fork's walk crosses files)
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": "2026-09-10T00:%02d:00Z" % i,
                     "promptSource": "typed", "message": {"role": "user", "content": "wire the fixtures %d" % i}})
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": "2026-09-10T00:%02d:30Z" % i,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "done %d" % i}], "stop_reason": "end_turn"}})
        parent = a
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return str(p)


def _entries_over(em, path):
    """The assembly entries that fold over `path`'s reader entry (its row in their recs): what a record drop would ask about."""
    with em._ASM_LOCK:
        return [k for k, e in em._ASM_CACHE.items() if e and path in (e.get("recs") or {})]


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _chat_file(d, sid, n, tag, t0, parent=None, rewind=False):
    """n settled turns under uuids <tag>u<k> and <tag>a<k>, a minute apart from t0, chained onto `parent`; with `rewind`, the
    first prompt's follow-up asked again from the first reply, so the turns after that reply leave the active branch: a rewind
    the judges' walks find inside the file."""
    p = Path(d) / (sid + ".jsonl")
    recs = []
    for k in range(n):
        u, a = "%su%d" % (tag, k), "%sa%d" % (tag, k)
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": _iso(t0 + 60 * k), "promptSource": "typed",
                     "cwd": "/w/notes-api", "message": {"role": "user", "content": "%s: page the notes-api index, step %d" % (tag, k)}})
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(t0 + 60 * k + 20), "cwd": "/w/notes-api",
                     "message": {"id": "msg_%s%d" % (tag, k), "role": "assistant", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "%s: step %d of the notes-api index is paged" % (tag, k)}]}})
        parent = a
    if rewind:
        t = t0 + 60 * n
        recs.append({"type": "user", "uuid": tag + "ru", "parentUuid": tag + "a0", "timestamp": _iso(t), "promptSource": "typed",
                     "cwd": "/w/notes-api", "message": {"role": "user", "content": "%s: no, page by cursor from step 0" % tag}})
        recs.append({"type": "assistant", "uuid": tag + "ra", "parentUuid": tag + "ru", "timestamp": _iso(t + 20), "cwd": "/w/notes-api",
                     "message": {"id": "msg_%sr" % tag, "role": "assistant", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "%s: cursor paging it is" % tag}]}})
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return str(p)


_EPI_T = [time.time() - 5000]


def _episodes(tc, sid, fsids):
    """The session's episode rows as the episode log records them: one per transcript, oldest first, the newest the current
    episode. Each write gets a distinct mtime, since the episode memo keys on it."""
    p = Path(jd.EPIDIR) / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"head": "h%d" % i, "fsid": f, "t": time.time()}) + "\n" for i, f in enumerate(fsids)))
    _EPI_T[0] += 10
    os.utime(p, (_EPI_T[0], _EPI_T[0]))
    tc.addCleanup(lambda: p.unlink(missing_ok=True))


def _open_budget(tc):
    """The pusher cycle's checkpoint budget opened wide for one test, so a drop's document write is never deferred by what
    earlier tests in this process spent, and set back after."""
    em = jd.em
    saved = dict(em._CKPT_CYCLE)
    em.checkpoint_cycle_begin(1 << 40)
    tc.addCleanup(lambda: (em._CKPT_CYCLE.clear(), em._CKPT_CYCLE.update(saved)))


def _read_bytes(path):
    with jd.em._READ_BYTES_LOCK:
        return jd.em._READ_BYTES.get(str(path), 0)


def _judge_pass(sid, leaf):
    """The judges' own walks over a session, as a pass runs them: the chain membership over the leaf's lineage, then the
    episode scan over the candidates and every episode row's file. Returns (chain verdicts, rewound set, scan failures)."""
    chain = jd._chain_membership(sid, str(leaf), "")
    out, fails = jd._per_file_rewound(sid, jd._judge_candidates(sid, [str(leaf)]))
    return chain, out, fails


class OneParseForBoth(unittest.TestCase):
    def setUp(self):
        km._display_sdk_human(A)                # builds the backend once: its setter installs the owner hook and
        #                                          clears the store, which must never happen mid-test (review find D)
        jd.parse_cache_clear()
        km._PERF_STATS.reset()
        self.d = tempfile.mkdtemp()
        self.now = int(time.time())

    def _misses(self):
        return jd.parse_misses()

    def test_kernel_then_judges_share_one_cold_parse_and_one_object(self):
        p = _transcript(self.d, A)
        m0 = self._misses()
        s1 = km._parse(p, A, self.now)
        s2 = jd.parsed_session(A, [p], self.now)
        self.assertIs(s1, s2, "the judges receive the very tree the kernel rendered")
        self.assertEqual(self._misses() - m0, 1, "one cold parse for the pair")
        snap = km._PERF_STATS.snapshot()["parses"]
        self.assertEqual(snap["kernel"], 1, "the kernel's ask was the cold one")
        self.assertGreaterEqual(snap["sharedHits"], 1, "the judges' ask was served from the store")

    def test_judges_then_kernel_share_too(self):
        p = _transcript(self.d, B)
        m0 = self._misses()
        s1 = jd.parsed_session(B, [p], self.now)
        s2 = km._parse(p, B, self.now)
        self.assertIs(s1, s2)
        self.assertEqual(self._misses() - m0, 1)
        snap = km._PERF_STATS.snapshot()["parses"]
        self.assertEqual((snap["kernel"], snap["hits"]), (0, 1), "the kernel's ask was a hit on the judges' parse")

    def test_an_appended_record_is_one_new_cold_parse_for_both(self):
        p = _transcript(self.d, A)
        km._parse(p, A, self.now); jd.parsed_session(A, [p], self.now)
        m0 = self._misses()
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u9", "parentUuid": "a1", "timestamp": "2026-09-10T01:00:00Z",
                                "promptSource": "typed", "message": {"role": "user", "content": "one more"}}) + "\n")
        s1 = jd.parsed_session(A, [p], self.now); s2 = km._parse(p, A, self.now)
        self.assertIs(s1, s2)
        self.assertEqual(self._misses() - m0, 1, "the grown file costs one parse, shared")

    def test_a_leaf_named_after_the_cli_session_is_found_by_path(self):
        """Review find (A): after a /clear or a resume fork the leaf is <lastSid>.jsonl while the romp sid is another
        id; the cache-only read and the view's drop go by leaf path and the entry's own sid, never a filename stem."""
        other = "55555555-6666-4777-8888-000000000777"
        p = _transcript(self.d, other, n=3)                  # a leaf named after the CLI session id
        states = Path(jd.STATE) / "states" / (A + ".jsonl")  # the romp sid's states log: part of the key, so a read that
        states.parent.mkdir(parents=True, exist_ok=True)     #  derived the states path from the leaf's stem would miss (review find)
        states.write_text(json.dumps({"t": self.now, "state": "idle"}) + "\n")
        self.addCleanup(lambda: states.unlink(missing_ok=True))
        km._parse(p, A, self.now)                            # parsed for romp sid A
        st = states.stat()
        stored = jd._PARSE_CACHE[A][0]                       # the STORED key: (fileset pair, cut)
        self.assertIn([st.st_mtime, st.st_size], stored[0], "the stored key carries states/<A>.jsonl's stat")
        stem_states = Path(jd.STATE) / "states" / (other + ".jsonl")   # a states path derived from the leaf's stem
        self.assertFalse(stem_states.exists())
        self.assertNotEqual(jd._fileset_key(jd._parse_key_files(A, [p], str(stem_states))[2]), stored[0],
                            "a key built over the stem-derived states path would not match the stored one")
        self.assertIsNotNone(km._parse_cached(p), "the cache-only read finds the display's tree by leaf path")
        self.assertIs(km._parse_cached(p), jd._PARSE_CACHE[A][1])
        self.assertIn(p, km._parse_cache)
        km._parse_cache.pop(p, None)
        self.assertNotIn(p, km._parse_cache, "pop by path drops the tree parsed under the romp sid")
        self.assertIsNone(km._parse_cached(p))
        self.assertNotIn(A, jd._PARSE_CACHE)

    def test_a_store_hit_reports_the_assembly_mode_so_the_chat_fold_can_fold(self):
        """Review find (B): the judges parse an appended transcript first; the kernel's build hits the store and must
        learn the mode of the parse that built the tree (fold or serve), else the fold gate rebuilds the whole chat."""
        p = _transcript(self.d, B, n=2)
        km._parse(p, B, self.now)
        self.assertEqual(km._parse_mode[p], "full", "the first parse is a full assembly")
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u9", "parentUuid": "a1", "timestamp": "2026-09-10T00:20:00.000Z",
                                "message": {"role": "user", "content": "one more"}}) + "\n")
        jd.parsed_session(B, [p], self.now + 1)              # a judge sees the append first (no asm_mode_out of its own)
        m0 = self._misses()
        km._parse(p, B, self.now + 1)                        # the kernel's build: a hit
        self.assertEqual(self._misses() - m0, 0)
        self.assertIn(km._parse_mode[p], ("fold", "serve"), "the hit carries the judges' assembly mode, never a blank read as full: %r" % km._parse_mode[p])
        ent = jd._PARSE_CACHE[B]
        self.assertEqual((ent[4], ent[5]), (B, km._parse_mode[p]), "the entry names its sid and the mode that built it")

    def test_two_answers_in_one_slot_in_a_hookless_process(self):
        """Review find (F), the case the per-flag trees exist for: with no owner hook, the display passes True (its
        backend owns the session) while the judges compute False (no registry file); each keeps its own tree in the
        slot and neither reads the other's."""
        p = _transcript(self.d, A, n=2)
        saved = jd._SDK_OWNER_FN
        jd._SDK_OWNER_FN = None
        try:
            jd.parse_cache_clear()
            judge_tree = jd.parsed_session(A, [p], self.now)                      # the judges: registry absent → False
            disp_tree = jd.parsed_session(A, [p], self.now, sdk_human=True)      # the display: its backend owns → True
            self.assertIsNot(judge_tree, disp_tree)
            self.assertEqual(sorted(jd._PARSE_CACHE[(A, "", p)].keys()), [False, True], "one tree per answer in the slot")
            m0 = self._misses()
            self.assertIs(jd.parsed_session(A, [p], self.now), judge_tree, "the judges hit their own tree")
            self.assertIs(jd.parsed_session(A, [p], self.now, sdk_human=True), disp_tree, "the display hits its own")
            self.assertEqual(self._misses() - m0, 0, "no stale hit either way, no re-parse either")
        finally:
            jd._SDK_OWNER_FN = saved

    def test_different_pending_cuts_get_separate_slots(self):
        """The manager's rule: when the kernel and the judges would read different cuts, each gets its own entry
        rather than one reading the other's view."""
        p = _transcript(self.d, A, n=3)
        saved = jd._PENDING_CUT_FN
        try:
            jd.set_pending_cut_provider(lambda fsid: "")
            plain = jd.parsed_session(A, [p], self.now)
            jd.set_pending_cut_provider(lambda fsid: "a1")          # a bare rollback armed: the world cut at a1
            cut = jd.parsed_session(A, [p], self.now)
            self.assertIsNot(plain, cut)
            self.assertIn((A, "a1", p), jd._PARSE_CACHE)
            self.assertNotIn((A, "", p), jd._PARSE_CACHE, "a slot stored under a new cut drops the fsid's other cut slots: one tree per session (review find)")
            jd.set_pending_cut_provider(lambda fsid: "")        # the cut is spent (the next record landed)
            m0 = self._misses()
            again = jd.parsed_session(A, [p], self.now)
            self.assertEqual(self._misses() - m0, 1, "the spent cut's tree is gone, the plain world is parsed once more")
            self.assertNotIn((A, "a1", p), jd._PARSE_CACHE, "and the spent cut's slot went with it")
            self.assertIs(jd._PARSE_CACHE[A][1], again, "a bare id reads the newest slot")
            self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == A]), 1)
        finally:
            jd.set_pending_cut_provider(saved)

    def test_an_override_render_keeps_its_own_slot_beside_the_live_leaf(self):
        """Review find (2026-09-11): build_session(path_override=agent file) parses ANOTHER transcript under the parent
        romp sid (a subagent viewer open on a running agent, an episode render). With a slot per (sid, cut) the two keys
        never matched, so each miss stored over the other's tree: two builds per cycle for one session, the chat fold
        seeing a new object every build, the feed's cache-only read flipping with build order. The leaf is in the slot:
        both hit, one tree each, no eviction, in either order."""
        p = _transcript(self.d, A, n=3)                                  # the live leaf
        os.makedirs(os.path.join(self.d, "subagents"), exist_ok=True)
        sub = _transcript(os.path.join(self.d, "subagents"), "agent-aaaa", n=2)   # an agent's own transcript
        cut = jd._pending_cut(A)
        for order in ((sub, p), (p, sub)):
            jd.parse_cache_clear()
            m0 = self._misses()
            first = km._parse(order[0], A, self.now)
            second = km._parse(order[1], A, self.now)
            self.assertEqual(self._misses() - m0, 2, "two transcripts, two cold parses")
            self.assertIs(km._parse(order[0], A, self.now), first, "the first still hits after the second parsed")
            self.assertIs(km._parse(order[1], A, self.now), second)
            self.assertEqual(self._misses() - m0, 2, "and neither evicted the other")
            self.assertIn((A, cut, p), jd._PARSE_CACHE)
            self.assertIn((A, cut, sub), jd._PARSE_CACHE)
            leaf_tree = first if order[0] == p else second
            self.assertIs(km._parse_cached(p), leaf_tree, "the feed's cache-only read sees the live leaf's tree while the agent's is held")
            self.assertIs(jd.parsed_session(A, [p], self.now), leaf_tree, "a judge pass reads the live leaf's tree")
            self.assertEqual(self._misses() - m0, 2)
        self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == A]), 2, "one slot per transcript under the sid")
        self.assertEqual(km._parse_cache.pop(sub)[1], second if order[1] == sub else first)
        self.assertIn(p, km._parse_cache, "dropping the agent's slot leaves the leaf's")

    def test_sdk_human_comes_from_the_owner_hook_and_is_part_of_the_match(self):
        p = _transcript(self.d, B)
        saved = jd._SDK_OWNER_FN
        try:
            jd.set_sdk_owner_provider(lambda fsid: False)
            s_no = jd.parsed_session(B, [p], self.now)
            self.assertFalse(jd._PARSE_CACHE[B][3])
            jd.set_sdk_owner_provider(lambda fsid: True)
            m0 = self._misses()
            s_yes = jd.parsed_session(B, [p], self.now)
            self.assertEqual(self._misses() - m0, 1, "a flipped owner answer is a different parse, never a stale hit")
            self.assertIsNot(s_no, s_yes)
            self.assertTrue(jd._sdk_owned(B))
            jd.set_sdk_owner_provider(lambda fsid: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertFalse(jd._sdk_owned(B), "a failing hook falls back to the registry file (absent here)")
        finally:
            jd._SDK_OWNER_FN = saved
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("jd.set_sdk_owner_provider(", src, "the kernel installs the backends' owns() as the one answer")

    def test_a_clear_releases_the_previous_leafs_tree(self):
        """Review find (2026-09-11): with the leaf in the slot, a /clear (the SDK registry's lastSid flips to a new
        fsid, discover hands out <lastSid>.jsonl) left the pre-clear leaf's full tree resident until restart: no cut
        changed, the LRU is 256 deep, nothing popped it. discover drops the previous leaf's slots when it first hands
        out the new one, so exactly one slot remains per session; twice, for two clears."""
        import re
        C = "33333333-2222-4333-8444-000000000303"
        L1, L2 = "44444444-2222-4333-8444-000000000441", "44444444-2222-4333-8444-000000000442"
        saved = (jd.STATE, jd.NAMES, jd.PROJECTS, os.environ.get("CLAUDE_CONFIG_DIR"))
        td = Path(tempfile.mkdtemp())
        try:
            jd._rebind_state(td / "state")
            cfg = td / "claude"; os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
            cdir = td / "launchdir"; cdir.mkdir()
            proj_dir = cfg / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            proj_dir.mkdir(parents=True)
            names = td / "names"; names.mkdir()
            (names / C).write_text("worker\t%s\t#abcdef\n" % cdir)
            jd.NAMES, jd.PROJECTS = names, cfg / "projects"
            jd.SDKDIR.mkdir(parents=True, exist_ok=True)
            reg = jd.SDKDIR / (C + ".json")
            anchor_path = _transcript(str(proj_dir), C, n=2)
            reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir)}))
            now = time.time()

            def current_leaf():
                rows = [r for r in jd.discover(now) if r[0] == C]
                self.assertEqual(len(rows), 1, rows)
                return str(rows[0][1])

            def slots():
                return [k for k in jd._PARSE_CACHE if k[0] == C]

            self.assertEqual(current_leaf(), anchor_path)
            t0 = km._parse(anchor_path, C, now)
            jd.parsed_session(C, [anchor_path], now)
            self.assertEqual(len(slots()), 1)
            for i, L in enumerate((L1, L2), start=1):
                leaf = _transcript(str(proj_dir), L, n=1)         # /clear: a fresh-headed transcript under a new fsid
                reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir), "lastSid": L}))
                os.utime(reg, (now + i, now + i))                  # the registry read memoizes on mtime: move it
                #                                                    explicitly, never by a sleep (coarse timestamps)
                self.assertEqual(current_leaf(), leaf, "clear %d: discover hands out the new leaf" % i)
                t = km._parse(leaf, C, now)
                self.assertIsNot(t, t0)
                jd.parsed_session(C, [leaf], now)                  # the judges read the same slot
                self.assertEqual(slots(), [(C, jd._pending_cut(C), leaf)],
                                 "clear %d: exactly one slot remains for the session, the new leaf's" % i)
                self.assertIsNone(km._parse_cached(anchor_path if i == 1 else prev_leaf), "the previous leaf's tree is gone")
                prev_leaf, t0 = leaf, t
            # a pass that snapshotted its rows before the clear parses the retired leaf AFTER the flip: it gets its
            # tree and the store keeps nothing, since the release was a one-shot and nothing would drop it again
            m0 = self._misses()
            late = jd.parsed_session(C, [anchor_path], now)
            self.assertEqual(self._misses() - m0, 1)
            self.assertTrue(late["turns"], "the late caller still gets a parse")
            self.assertEqual(slots(), [(C, jd._pending_cut(C), prev_leaf)], "a store under a retired leaf is refused")
            self.assertIsNone(km._parse_cached(anchor_path))
            self.assertTrue(jd._leaf_retired(C, anchor_path)); self.assertFalse(jd._leaf_retired(C, prev_leaf))
        finally:
            jd.NAMES, jd.PROJECTS = saved[1], saved[2]
            if saved[3] is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved[3]
            jd._rebind_state(saved[0])

    def test_the_clear_race_window_hands_out_the_previous_leaf_not_the_anchor(self):
        """Carried from the stage 2 review (2026-09-11): between the registry's lastSid rewrite and the new transcript's
        first record, discover handed out the ANCHOR, so from the second clear on a build landing in the window parsed
        the pre-clear anchor cold (its tree released at the first clear) and the noted leaf flipped twice. discover
        hands out the leaf it handed out last until the named file exists."""
        import re
        C = "33333333-2222-4333-8444-000000000304"
        L1, L2 = "44444444-2222-4333-8444-000000000451", "44444444-2222-4333-8444-000000000452"
        saved = (jd.STATE, jd.NAMES, jd.PROJECTS, os.environ.get("CLAUDE_CONFIG_DIR"))
        td = Path(tempfile.mkdtemp())
        try:
            jd._rebind_state(td / "state")
            cfg = td / "claude"; os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
            cdir = td / "launchdir"; cdir.mkdir()
            proj_dir = cfg / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            proj_dir.mkdir(parents=True)
            names = td / "names"; names.mkdir()
            (names / C).write_text("worker\t%s\t#abcdef\n" % cdir)
            jd.NAMES, jd.PROJECTS = names, cfg / "projects"
            jd.SDKDIR.mkdir(parents=True, exist_ok=True)
            reg = jd.SDKDIR / (C + ".json")
            anchor_path = _transcript(str(proj_dir), C, n=2)
            leaf1 = _transcript(str(proj_dir), L1, n=1)
            reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir), "lastSid": L1}))
            now = time.time()

            def current_leaf():
                rows = [r for r in jd.discover(now) if r[0] == C]
                self.assertEqual(len(rows), 1, rows)
                return str(rows[0][1])

            self.assertEqual(current_leaf(), leaf1)                       # after the first clear
            km._parse(leaf1, C, now)
            reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir), "lastSid": L2}))
            os.utime(reg, (now + 1, now + 1))                             # the second clear's registry write lands first
            m0 = self._misses()
            self.assertEqual(current_leaf(), leaf1, "the window: lastSid names a file not on disk, the previous leaf stands")
            self.assertEqual(self._misses() - m0, 0)
            self.assertFalse(jd._leaf_retired(C, leaf1), "no flip was noted in the window")
            self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == C], [(C, jd._pending_cut(C), leaf1)], "the anchor was never parsed")
            leaf2 = _transcript(str(proj_dir), L2, n=1)                  # the CLI writes the new transcript's first record
            self.assertEqual(current_leaf(), leaf2, "the file exists: discover hands out the new leaf")
            self.assertTrue(jd._leaf_retired(C, leaf1)); self.assertFalse(jd._leaf_retired(C, anchor_path))
        finally:
            jd.NAMES, jd.PROJECTS = saved[1], saved[2]
            if saved[3] is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved[3]
            jd._rebind_state(saved[0])

    def test_a_fork_childs_flip_leaves_the_parents_slot(self):
        """Review find (2026-09-11): a fork child's SDK registry is born with lastSid = the PARENT's fsid until its
        own init flips it, so discover hands the child the parent's transcript first; the child's flip to its own
        file must drop the child's slots of that leaf only, never the parent's live tree."""
        P, CH = A, "66666666-2222-4333-8444-000000000666"
        parent_leaf = _transcript(self.d, P, n=2)
        child_leaf = _transcript(self.d, CH, n=1)
        jd._note_leaf(P, parent_leaf)
        jd._note_leaf(CH, parent_leaf)                     # the child's registry still names the parent's file
        parent_tree = km._parse(parent_leaf, P, self.now)
        jd.parsed_session(CH, [parent_leaf], self.now)     # a pass parsed the child's row over the parent's file
        m0 = self._misses()
        jd._note_leaf(CH, child_leaf)                      # the child's init flipped its lastSid
        self.assertIs(km._parse(parent_leaf, P, self.now), parent_tree, "the parent's live slot stands")
        self.assertEqual(self._misses() - m0, 0, "the parent is not re-parsed cold by the child's flip")
        self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == CH], [], "the child's slot of the parent's file went")
        self.assertTrue(jd._leaf_retired(CH, parent_leaf)); self.assertFalse(jd._leaf_retired(P, parent_leaf))
        jd.parsed_session(CH, [child_leaf], self.now)
        self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == CH]), 1, "the child's own leaf stores")

    def test_a_flip_releases_that_sessions_assembly_entry_of_the_retired_leaf_and_keeps_the_records(self):
        """Release on the events that end a file's use (2026-10-01): the flip dropped the retired leaf's parse slots and left
        its assembly entry (an adapter holding every record of the leaf, its atoms and maps) resident until the assembly
        cache's count cap reached it. The flip now releases that session's assembly entry of the retired leaf. Another
        session's entry of the same file (the parent's, when a fork child flips) stays, and so does the record cache: a
        /clear's anchor is the new leaf's candidate, which its parse reads through the reader, not from disk again."""
        em = jd.em
        P, CH = A, "66666666-2222-4333-8444-000000000668"
        parent_leaf = _transcript(self.d, P, n=2)
        child_leaf = _transcript(self.d, CH, n=1)
        jd._note_leaf(P, parent_leaf)
        jd._note_leaf(CH, parent_leaf)                     # the child's registry still names the parent's file
        km._parse(parent_leaf, P, self.now)
        jd.parsed_session(CH, [parent_leaf], self.now)
        rp = os.path.realpath(parent_leaf)

        def owners(path_real):
            with em._ASM_LOCK:
                return sorted(k[1] for k in em._ASM_CACHE if k[0] == path_real)
        self.assertEqual(owners(rp), sorted([P, CH]), "an entry per session that parsed the parent's file")
        with em._JSONL_CACHE_LOCK:
            rec0 = em._JSONL_CACHE.get(parent_leaf)
        jd._note_leaf(CH, child_leaf)                      # the child's init flipped its lastSid
        self.assertEqual(owners(rp), [P], "the child's entry of the parent's file is released; the parent's stays")
        with em._JSONL_CACHE_LOCK:
            self.assertIs(em._JSONL_CACHE.get(parent_leaf), rec0, "the record cache is not the flip's to touch")
        m0 = self._misses()
        km._parse(parent_leaf, P, self.now)
        self.assertEqual(self._misses() - m0, 0, "the parent's tree is still a store hit")
        # a /clear: the anchor was the session's leaf, the new leaf takes it as a candidate
        C = "77777777-2222-4333-8444-000000000777"
        anchor = _transcript(self.d, C, n=3)
        leaf1 = _transcript(self.d, "77777777-2222-4333-8444-000000000778", n=1)
        jd._note_leaf(C, anchor)
        km._parse(anchor, C, self.now)
        self.assertEqual(owners(os.path.realpath(anchor)), [C])
        jd._note_leaf(C, leaf1)                            # the /clear's flip
        self.assertEqual(owners(os.path.realpath(anchor)), [], "the pre-clear leaf's entry is released at the flip")
        with em._READ_BYTES_LOCK:
            b0 = em._READ_BYTES.get(anchor, 0)
        with em._JSONL_CACHE_LOCK:
            self.assertIn(anchor, em._JSONL_CACHE, "the anchor's records stay for the new leaf's parse")
        km._parse(leaf1, C, self.now)
        with em._READ_BYTES_LOCK:
            self.assertEqual(em._READ_BYTES.get(anchor, 0), b0, "the new leaf's parse read the anchor from the reader, not the disk")

    def test_a_parse_of_the_retired_leaf_after_the_flip_leaves_no_assembly_entry(self):
        """The flip releases the retired leaf's assembly entry once. A parse of that leaf afterwards (a pass that took its rows
        before a mid-pass /clear) is refused its parse slot, since nothing would drop it again; the assembly entry the parse
        installed is released there too, for the same reason, instead of staying until the cache's count cap."""
        em = jd.em
        C = "88888888-2222-4333-8444-000000000881"
        anchor = _transcript(self.d, C, n=3)
        leaf1 = _transcript(self.d, "88888888-2222-4333-8444-000000000882", n=1)
        rp = os.path.realpath(anchor)

        def entries(sid=C):
            with em._ASM_LOCK:
                return [k for k in em._ASM_CACHE if k[0] == rp and k[1] == sid]
        jd._note_leaf(C, anchor)
        km._parse(anchor, C, self.now)
        self.assertTrue(entries())
        jd._note_leaf(C, leaf1)                            # the /clear's flip
        self.assertEqual(entries(), [], "released at the flip")
        peer = "88888888-2222-4333-8444-000000000889"     # another session's parse of the same file (a fork child's first leaf)
        km._parse(anchor, peer, self.now)
        self.assertTrue(entries(peer), "the premise: a peer session's entry of the retired file")
        tree = jd.parsed_session(C, [anchor], self.now)   # the pass's replay over the rows it took before the flip
        self.assertTrue(tree["turns"], "the caller is handed its parse")
        self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == C and k[2] == anchor], [], "no slot stored for the retired leaf")
        self.assertEqual(entries(), [], "and no assembly entry left behind for it")
        self.assertTrue(entries(peer), "the release at the refused store is this session's only: the peer's entry stays")

    def test_a_release_that_raises_never_fails_the_flip_or_the_parse(self):
        """The flip's release does file I/O inside discover, which every pane rides (a stat, a sidecar read, the states log, a
        fold document's write), and the refused store's release inside a parse: a raise in either is noted once per file and
        the event goes on as it did before the release existed (round one of the review: discover raised)."""
        em = jd.em
        C = "88888888-2222-4333-8444-00000000088a"
        anchor = _transcript(self.d, C, n=3)
        leaf1 = _transcript(self.d, "88888888-2222-4333-8444-00000000088b", n=1, tag="r")
        jd._note_leaf(C, anchor)
        km._parse(anchor, C, self.now)
        f0 = em._ASM_STATS.get("releaseFailed", 0)
        err = io.StringIO()
        with mock.patch.object(em, "release_assembly", side_effect=OSError("permission denied")), \
                contextlib.redirect_stderr(err):
            jd._note_leaf(C, leaf1)                        # discover's flip
            tree = jd.parsed_session(C, [anchor], self.now)   # a parse of the retired leaf: its release raises as well
            jd.parsed_session(C, [anchor], self.now)
        self.assertEqual(jd._LEAF_SEEN.get(C), leaf1, "the flip took effect")
        self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == C and k[2] == anchor], [], "and its slots went as before")
        self.assertTrue(tree["turns"], "the parse is handed back")
        self.assertEqual(em._ASM_STATS.get("releaseFailed", 0) - f0, 3, "every failure counted")
        self.assertEqual((err.getvalue().count("leaf flip"), err.getvalue().count("retired parse")), (1, 1),
                         "said once per file and event: %r" % err.getvalue())

    def test_a_lineage_that_cannot_be_read_at_the_flip_is_counted_and_said_and_drops_nothing(self):
        """The flip's record drop reads the new leaf's lineage first (its candidates, the states log's resume links) to know
        what a live session still reads. A read that raises leaves the retired leaf's records in place, since nothing is
        dropped on a guess, and it is counted under releaseFailed and said on stderr like every failed release (fold round 2
        of the review: it returned in silence, so a drop that could not run was invisible)."""
        em = jd.em
        _open_budget(self)                                 # the budget is no reason for the records to stay
        C, L1, L2 = ("88888888-2222-4333-8444-00000000088c", "88888888-2222-4333-8444-00000000088d",
                     "88888888-2222-4333-8444-00000000088e")
        anchor = _transcript(self.d, C, n=3)
        leaf1 = _transcript(self.d, L1, n=2, tag="l1")
        leaf2 = _transcript(self.d, L2, n=1, tag="l2")
        jd._note_leaf(C, anchor)
        km._parse(anchor, C, self.now)
        jd._note_leaf(C, leaf1)                            # the first /clear
        km._parse(leaf1, C, self.now)                      # the first clear's file is read while it is the leaf
        with em._JSONL_CACHE_LOCK:
            rec0 = em._JSONL_CACHE.get(leaf1)
        self.assertIsNotNone(rec0, "the premise: the first clear's file has a record entry")
        f0 = em._ASM_STATS.get("releaseFailed", 0)
        err = io.StringIO()
        with mock.patch.object(jd, "_judge_candidates", side_effect=OSError("permission denied")), \
                contextlib.redirect_stderr(err):
            jd._note_leaf(C, leaf2)                        # the second /clear: its lineage read raises
        self.assertEqual(jd._LEAF_SEEN.get(C), leaf2, "the flip took effect")
        self.assertEqual(em._ASM_STATS.get("releaseFailed", 0) - f0, 1, "the drop that could not run is counted")
        self.assertEqual(err.getvalue().count("leaf flip lineage"), 1, "and said: %r" % err.getvalue())
        with em._JSONL_CACHE_LOCK:
            self.assertIs(em._JSONL_CACHE.get(leaf1), rec0, "nothing is dropped on a guess: the retired leaf's records stand")

    def test_a_second_clear_drops_the_first_clears_leaf_from_the_record_cache_and_keeps_the_anchor(self):
        """After a first /clear the retired leaf is the session's anchor, a candidate of the new leaf's parse: its records stay.
        After a second one the retired leaf is the first clear's file, which no live lineage reads: its record entry, whole
        from its days as the leaf, goes at the flip (unless a whole assembly entry still folds over it), where it stayed
        until the reader's LRU reached it. The judges' episode scan still walks that file, after the flip in the same pass
        (discover flips before the judges run), so the drop writes the file's fold document first, the scan's rewound memo in
        it, and the scan restores the memo from the document at the guard bytes' cost (round one of the review: the drop
        popped with no write, and the scan read the whole file again and kept what it read)."""
        em = jd.em
        _open_budget(self)
        C, L1, L2 = ("88888888-2222-4333-8444-000000000883", "88888888-2222-4333-8444-000000000884",
                     "88888888-2222-4333-8444-000000000885")
        t0 = self.now - 86400
        anchor = _chat_file(self.d, C, 3, "sca", t0)
        leaf1 = _chat_file(self.d, L1, 30, "scb", t0 + 3600, rewind=True)
        leaf2 = _chat_file(self.d, L2, 1, "scc", t0 + 7200)

        def held(p):
            with em._JSONL_CACHE_LOCK:
                return p in em._JSONL_CACHE
        jd._note_leaf(C, anchor)
        _episodes(self, C, [C])
        km._parse(anchor, C, self.now)
        jd._note_leaf(C, leaf1)                            # the first /clear
        _episodes(self, C, [C, L1])
        self.assertTrue(held(anchor), "the anchor is the new leaf's candidate: kept")
        km._parse(leaf1, C, self.now)
        _, rewound1, _ = _judge_pass(C, leaf1)             # the judges' pass while L1 is the leaf: its walks read it whole
        self.assertTrue(any(u.startswith("scb") for u in rewound1), "the premise: the scan finds L1's rewind")
        self.assertTrue(held(leaf1) and held(anchor))
        d0 = em.record_cache_stats()["dropped"]
        jd._note_leaf(C, leaf2)                            # the second /clear
        _episodes(self, C, [C, L1, L2])
        self.assertFalse(held(leaf1), "the first clear's file is in no live lineage: dropped at the flip")
        self.assertTrue(held(anchor), "the anchor is still the new leaf's candidate")
        self.assertEqual(em.record_cache_stats()["dropped"] - d0, 1)
        km._parse(leaf2, C, self.now)                      # the new leaf parses over the anchor the reader still holds
        b0 = _read_bytes(leaf1)
        _, rewound2, fails = _judge_pass(C, leaf2)         # the same pass's episode scan walks L1: its episode row names it
        read = _read_bytes(leaf1) - b0
        self.assertEqual(fails, 0)
        self.assertEqual({u for u in rewound2 if u.startswith("scb")}, {u for u in rewound1 if u.startswith("scb")},
                         "the scan's verdicts on L1 are the ones it found while L1 was the leaf")
        self.assertLessEqual(read, 4 * em._JSONL_TAIL_GUARD, "the scan restored L1's memo from its fold document, reading %d bytes "
                             "of the %d-byte file" % (read, os.path.getsize(leaf1)))
        with em._JSONL_CACHE_LOCK:
            ent = em._JSONL_CACHE.get(leaf1)
        self.assertTrue(ent is None or not ent[4], "and L1's records stay out of memory: %s" % (None if ent is None else len(ent[4])))

    def test_a_fork_childs_flip_keeps_the_parents_live_leaf_in_the_record_cache_with_no_entry_pinning_it(self):
        """A fork child's first leaf is its parent's live transcript; the child's flip to its own file must not drop that
        file's record entry, even when no assembly entry folds over it: it is the leaf discover hands out for the parent."""
        em = jd.em
        P, CH = "88888888-2222-4333-8444-000000000886", "88888888-2222-4333-8444-000000000887"
        parent_leaf = _transcript(self.d, P, n=2)
        child_leaf = _transcript(self.d, CH, n=1)
        jd._note_leaf(P, parent_leaf)
        jd._note_leaf(CH, parent_leaf)
        jd.parsed_session(CH, [parent_leaf], self.now)    # the child's row parsed over the parent's file
        with em._JSONL_CACHE_LOCK:
            rec0 = em._JSONL_CACHE.get(parent_leaf)
        self.assertIsNotNone(rec0)
        jd._note_leaf(CH, child_leaf)                      # the child's flip releases its own entry of the parent's file
        with em._ASM_LOCK:
            self.assertFalse([k for k in em._ASM_CACHE if k[0] == os.path.realpath(parent_leaf)], "no entry pins the file")
        with em._JSONL_CACHE_LOCK:
            self.assertIs(em._JSONL_CACHE.get(parent_leaf), rec0, "the parent's live leaf keeps its records")

    def test_a_resume_forks_flip_keeps_the_resumed_from_file_the_new_leafs_parse_reads(self):
        """The flip's record keep follows the new leaf's lineage, not only its candidates: after a /clear to L1, a resume fork
        (a states resumeFork row, L1 to L2) retires L1, and L2's parse reads L1 through the lineage closure. The flip has just
        released the session's entry of L1, so nothing pins its record entry; dropping it would send L2's parse to the disk for
        the whole of L1 again, a release that costs more than it frees."""
        em = jd.em
        C = "99999999-2222-4333-8444-000000000991"
        L1, L2 = "99999999-2222-4333-8444-000000000992", "99999999-2222-4333-8444-000000000993"
        anchor = _transcript(self.d, C, n=3)
        leaf1 = _transcript(self.d, L1, n=2, tag="l1")
        leaf2 = _transcript(self.d, L2, n=1, tag="l2")
        jd._note_leaf(C, anchor)
        km._parse(anchor, C, self.now)
        jd._note_leaf(C, leaf1)                            # the /clear
        km._parse(leaf1, C, self.now)
        states = Path(jd.STATESDIR) / (C + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text(json.dumps({"t": self.now, "resumeFork": {"from": L1, "to": L2}}) + "\n")
        self.addCleanup(lambda: states.unlink(missing_ok=True))
        jd._note_leaf(C, leaf2)                            # the resume fork's flip retires L1
        self.assertEqual(_entries_over(em, leaf1), [], "the flip released the session's entry of L1: nothing pins its records")
        with em._JSONL_CACHE_LOCK:
            self.assertIn(leaf1, em._JSONL_CACHE, "L1 is in the new leaf's lineage: its records stay")
        with em._READ_BYTES_LOCK:
            b0 = em._READ_BYTES.get(leaf1, 0)
        km._parse(leaf2, C, self.now)
        with em._READ_BYTES_LOCK:
            self.assertEqual(em._READ_BYTES.get(leaf1, 0), b0, "L2's parse read L1 from the reader, not the disk")

    def test_a_childs_flip_off_a_registered_sessions_own_file_keeps_its_records(self):
        """The flip keeps a registered session's own file, as the judges' episode scan does. A fork child first handed its
        parent's file flips to its own after the parent cleared to L1: the parent's file is then no session's current leaf and
        outside the child's lineage, and with both sessions' entries of it released nothing pins it. It is still the parent's
        anchor, which the parent's next parse of L1 reads through the reader."""
        em = jd.em
        P, CH = "99999999-2222-4333-8444-000000000994", "99999999-2222-4333-8444-000000000995"
        L1 = "99999999-2222-4333-8444-000000000996"
        Path(jd.SDKDIR).mkdir(parents=True, exist_ok=True)
        reg = Path(jd.SDKDIR) / (P + ".json")
        reg.write_text(json.dumps({"sid": P, "name": "api", "cwd": "/TESTDIR"}))
        self.addCleanup(lambda: reg.unlink(missing_ok=True))
        self.assertTrue(jd._sdk_owned(P), "the parent is a registered session")
        parent_file = _transcript(self.d, P, n=3)
        leaf1 = _transcript(self.d, L1, n=1, tag="l1")
        child_leaf = _transcript(self.d, CH, n=1, tag="ch")
        jd._note_leaf(P, parent_file)
        km._parse(parent_file, P, self.now)
        jd._note_leaf(P, leaf1)                            # the parent's /clear: its own file is its anchor now
        jd._note_leaf(CH, parent_file)                     # a fork child's registry first names the parent's file
        jd.parsed_session(CH, [parent_file], self.now)
        with em._JSONL_CACHE_LOCK:
            rec0 = em._JSONL_CACHE.get(parent_file)
        self.assertIsNotNone(rec0)
        jd._note_leaf(CH, child_leaf)                      # the child's init flipped its lastSid
        self.assertEqual(_entries_over(em, parent_file), [], "both sessions' entries of the parent's file were released: nothing pins it")
        with em._JSONL_CACHE_LOCK:
            self.assertIs(em._JSONL_CACHE.get(parent_file), rec0, "a registered session's own file keeps its records")
        with em._READ_BYTES_LOCK:
            b0 = em._READ_BYTES.get(parent_file, 0)
        km._parse(leaf1, P, self.now)
        with em._READ_BYTES_LOCK:
            self.assertEqual(em._READ_BYTES.get(parent_file, 0), b0, "the parent's parse of L1 read its anchor from the reader")

    def test_the_kernel_view_reads_the_store(self):
        p = _transcript(self.d, A)
        self.assertNotIn(p, km._parse_cache)
        self.assertIsNone(km._parse_cached(p), "cache-only read: nothing yet, and no parse ran")
        s = km._parse(p, A, self.now)
        self.assertIn(p, km._parse_cache)
        self.assertIs(km._parse_cache.get(p)[1], s)
        self.assertIs(km._parse_cache[p][1], s)
        self.assertIs(km._parse_cached(p), s, "the live-key read answers from the store")
        self.assertIn(p, list(km._parse_cache))
        self.assertEqual(len(km._parse_cache), 1)
        km._parse_cache.pop(p, None)
        self.assertNotIn(p, km._parse_cache, "a dead lane's drop empties every cut's slot")
        km._parse(p, A, self.now)
        km._parse_cache.clear()
        self.assertEqual(len(jd._PARSE_CACHE), 0)

    def test_least_recently_used_eviction_never_clears_wholesale(self):
        saved = jd._PARSE_CACHE_MAX
        jd._PARSE_CACHE_MAX = 3
        try:
            paths = [_transcript(self.d, "3333333%d-2222-4333-8444-00000000030%d" % (i, i)) for i in range(4)]
            sids = [os.path.splitext(os.path.basename(p))[0] for p in paths]
            for p, sid in zip(paths[:3], sids[:3]):
                jd.parsed_session(sid, [p], self.now)
            jd.parsed_session(sids[0], [paths[0]], self.now)        # touch the oldest: it becomes newest
            jd.parsed_session(sids[3], [paths[3]], self.now)        # a fourth: evicts ONE, the least recently used (sids[1])
            self.assertEqual(len(jd._PARSE_CACHE), 3)
            self.assertIn(sids[0], jd._PARSE_CACHE); self.assertNotIn(sids[1], jd._PARSE_CACHE)
            self.assertIn(sids[2], jd._PARSE_CACHE); self.assertIn(sids[3], jd._PARSE_CACHE)
        finally:
            jd._PARSE_CACHE_MAX = saved

    def test_parse_cached_never_parses_and_matches_the_live_key(self):
        p = _transcript(self.d, B)
        m0 = self._misses()
        self.assertIsNone(jd.parse_cached(B, [p]))
        self.assertEqual(self._misses() - m0, 0)
        s = jd.parsed_session(B, [p], self.now)
        self.assertIs(jd.parse_cached(B, [p]), s)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u7", "parentUuid": "a1", "timestamp": "2026-09-10T02:00:00Z",
                                "promptSource": "typed", "message": {"role": "user", "content": "grown"}}) + "\n")
        self.assertIsNone(jd.parse_cached(B, [p]), "a moved file is not the cached version")
        self.assertEqual(self._misses() - m0, 1, "and the cache-only read still parsed nothing")


def _norm(uuids):
    """A run's uuids without the run's one-letter prefix, so two runs over the same world compare."""
    return sorted(str(u)[1:] for u in uuids)


class ReleasesKeepTheJudgesVerdicts(unittest.TestCase):
    """Judge verdict parity, driven through the events themselves (round one of the review: the earlier check called the event
    model's walks directly and handed the releases an empty keep set, so no keep or pin rule could fail it). Each event runs
    the way discover, the parse store and the timeline build run it: the leaf flips (_note_leaf) of a first /clear, a second
    /clear and a resume fork, a fork child flipping off its registered parent's file, and a dead lane's release (the step the
    timeline build takes, _release_dead_lane), each followed by the judges' own pass over the session (_chain_membership,
    then the episode scan _per_file_rewound). The same world runs twice, once as shipped and once with the releases stubbed
    out, and every pass must give the same verdicts, make the same whole reads and read the same bytes of every transcript,
    but for the two costs the releases are documented to pay: after the second /clear the episode scan restores the first
    clear's file from its fold document (the guard bytes, never the file), and a session revived after its dead lane let go
    reads its leaf and its anchor at most once more each. A keep that let a live lineage's file go (the anchor at a /clear,
    the resumed-from file at a resume fork, a registered session's own file at a child's flip) shows as a whole read the
    stubbed run never makes. The world is synthetic: placeholder ids, the notes-api demo wording."""

    def setUp(self):
        km._display_sdk_human(A)
        jd.parse_cache_clear()
        self.now = int(time.time())
        _open_budget(self)

    def _world(self, run, stubbed):
        em = jd.em
        d = tempfile.mkdtemp()

        def sid(k):
            return "eeeeeeee-2222-4333-8444-%012d" % (run * 100 + k)
        C, L1, L2, L3, P, PL1, CH = (sid(k) for k in range(1, 8))
        r, t0 = "xy"[run], self.now - 86400                # the run's uuid prefix: the hydrated memo is keyed by uuid alone
        f = {"C": _chat_file(d, C, 6, r + "c", t0, rewind=True),
             "L1": _chat_file(d, L1, 30, r + "l", t0 + 3600, rewind=True),
             "L2": _chat_file(d, L2, 4, r + "m", t0 + 7200),
             "L3": _chat_file(d, L3, 4, r + "n", t0 + 9000, parent=r + "ma3"),   # resumed from L2's last record
             "P": _chat_file(d, P, 6, r + "p", t0, rewind=True),
             "PL1": _chat_file(d, PL1, 3, r + "q", t0 + 3600),
             "CH": _chat_file(d, CH, 2, r + "h", t0 + 5000)}
        Path(jd.SDKDIR).mkdir(parents=True, exist_ok=True)
        reg = Path(jd.SDKDIR) / (P + ".json")
        reg.write_text(json.dumps({"sid": P, "name": "api", "cwd": "/TESTDIR"}))
        self.addCleanup(lambda: reg.unlink(missing_ok=True))
        states = Path(jd.STATESDIR) / (C + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: states.unlink(missing_ok=True))
        steps, facts = [], {}

        def step(name, sess, leaf, event):
            b0 = {k: _read_bytes(p) for k, p in f.items()}
            w0 = em.record_cache_stats().get("wholeReads") or {}
            event()
            chain, out, fails = _judge_pass(sess, f[leaf])
            w1 = em.record_cache_stats().get("wholeReads") or {}
            steps.append({"step": name, "chain": {k: _norm(v) for k, v in chain.items()}, "rewound": _norm(out), "fails": fails,
                          "bytes": {k: _read_bytes(p) - b0[k] for k, p in f.items()},
                          "whole": {k: v.get("count", 0) - (w0.get(k) or {}).get("count", 0) for k, v in w1.items()
                                    if v.get("count", 0) != (w0.get(k) or {}).get("count", 0)}})

        def start():
            jd._note_leaf(C, f["C"]); _episodes(self, C, [C]); km._parse(f["C"], C, self.now)

        def first_clear():
            jd._note_leaf(C, f["L1"]); _episodes(self, C, [C, L1]); km._parse(f["L1"], C, self.now)

        def second_clear():
            jd._note_leaf(C, f["L2"]); _episodes(self, C, [C, L1, L2]); km._parse(f["L2"], C, self.now)

        def resume_fork():
            states.write_text(json.dumps({"t": self.now, "resumeFork": {"from": L2, "to": L3}}) + "\n")
            jd._note_leaf(C, f["L3"])
            tree = km._parse(f["L3"], C, self.now)
            facts["doc"] = em.asm_checkpoint_write(f["L3"], C, sdk_human=bool(km._display_sdk_human(C)), tree=tree)
            facts["docWhy"] = dict(em.asm_checkpoint_stats()["skipped"])

        def fork_child():
            jd._note_leaf(P, f["P"]); km._parse(f["P"], P, self.now)
            jd._note_leaf(P, f["PL1"])                     # the parent's /clear: its own file is its anchor now
            jd._note_leaf(CH, f["P"]); jd.parsed_session(CH, [f["P"]], self.now)   # a fork child's registry first names it
            jd._note_leaf(CH, f["CH"])                     # the child's init flips it
            km._parse(f["PL1"], P, self.now)

        def dead_lane():
            km._parse_cache.pop(f["L3"], None)             # the lever's own pop, then the lane's release
            km._release_dead_lane(C, f["L3"], ("v1",), [{"sid": C, "path": f["L3"]}], {})
            with em._JSONL_CACHE_LOCK:
                facts["deadHeld"] = (f["L3"] in em._JSONL_CACHE, f["C"] in em._JSONL_CACHE)

        with contextlib.ExitStack() as stack:
            if stubbed:
                stack.enter_context(mock.patch.object(em, "release_assembly", lambda *a, **k: 0))
                stack.enter_context(mock.patch.object(em, "release_records", lambda *a, **k: 0))
            step("start", C, "C", start)
            step("first clear", C, "L1", first_clear)
            step("second clear", C, "L2", second_clear)
            step("resume fork", C, "L3", resume_fork)
            step("fork child", P, "PL1", fork_child)
            step("dead lane", C, "L3", dead_lane)       # the session revived: the judges pass over it again
        return f, steps, facts

    def test_every_release_leaves_the_judges_verdicts_and_their_reads_as_they_were(self):
        f, live, lf = self._world(0, stubbed=False)
        _, base, bf = self._world(1, stubbed=True)
        self.assertTrue(lf["doc"] and bf["doc"], "the premise: the resumed leaf's document stands (%s)" % lf.get("docWhy"))
        self.assertEqual((lf["deadHeld"], bf["deadHeld"]), ((False, False), (True, True)),
                         "the premise: the dead lane let its leaf and anchor go, and the stubbed run kept them")
        by, bb = {s["step"]: s for s in live}, {s["step"]: s for s in base}
        self.assertEqual(list(by), list(bb))
        self.assertTrue(any(u.startswith("l") for u in by["second clear"]["rewound"]) and
                        any(u.startswith("c") for u in by["second clear"]["rewound"]),
                        "the premise: the scan finds the anchor's and the first clear's rewinds: %s" % by["second clear"]["rewound"])
        guard = 4 * jd.em._JSONL_TAIL_GUARD
        self.assertGreater(os.path.getsize(f["L1"]), 10 * guard)
        for name in by:
            a, b = by[name], bb[name]
            self.assertEqual((a["chain"], a["rewound"], a["fails"]), (b["chain"], b["rewound"], b["fails"]),
                             "%s: the judges' verdicts are the same with the releases as without" % name)
            want = dict(b["bytes"])
            if name == "second clear":
                self.assertEqual(b["bytes"]["L1"], 0)
                self.assertLessEqual(a["bytes"]["L1"], guard, "the episode scan restored the first clear's file from its fold "
                                     "document, reading %d bytes of %d" % (a["bytes"]["L1"], os.path.getsize(f["L1"])))
                want["L1"] = a["bytes"]["L1"]
            if name == "dead lane":
                for role in ("L3", "C"):
                    self.assertLessEqual(a["bytes"][role], os.path.getsize(f[role]) + guard,
                                         "the revived session reads its %s at most once more" % role)
                    want[role] = a["bytes"][role]
            else:
                self.assertEqual(a["whole"], b["whole"], "%s: the same whole reads with the releases as without" % name)
            self.assertEqual(a["bytes"], want, "%s: every transcript read the bytes it read with the releases stubbed out" % name)


class DeadCodexSessionKeepsItsAuthor(unittest.TestCase):
    """2026-09-11: the display's sdk_human (_display_sdk_human) and the judges' (the owner hook the kernel installs)
    answered for Codex with CodexBackend.owns, which is live-only, so a Codex session's typed prompts (promptSource
    "sdk", the Codex convention) re-authored from human to programmatic the moment its registry row was marked dead
    (an end, a kernel restart over a dead row) — the same transcript, nothing new learned — while a dead SDK session's
    kept theirs (SdkBackend.owns is the reg file's existence). Both sides answer by record presence now. A REAL
    CodexBackend on a private state root, its registry holding a dead row and a live one the way a restart loads
    them, stands in for the kernel's singleton; nothing else is stubbed."""
    DEAD = "77777777-2222-4333-8444-000000000771"
    LIVE = "77777777-2222-4333-8444-000000000772"
    NONE = "77777777-2222-4333-8444-000000000773"

    def setUp(self):
        km._display_sdk_human(A)               # builds the SDK backend once: its setter installs the owner hook
        self.assertIsNotNone(jd._SDK_OWNER_FN, "the kernel installed its owner hook")
        cx0 = km._codex()
        self.assertIsNotNone(cx0, "the kernel's Codex backend module loads in this process")
        root = Path(tempfile.mkdtemp())
        (root / "codex").mkdir()
        (root / "codex" / "registry.json").write_text(json.dumps({
            self.DEAD: {"tid": self.DEAD, "name": "web", "cwd": "/TESTDIR", "dead": True},
            self.LIVE: {"tid": self.LIVE, "name": "api", "cwd": "/TESTDIR", "dead": False}}))
        self.cx = type(cx0)(root, log=lambda m: None)   # the real backend class, loading the rows as a restart does
        saved = km._codex
        km._codex = lambda: self.cx
        self.addCleanup(setattr, km, "_codex", saved)
        jd.parse_cache_clear()
        self.d = tempfile.mkdtemp()
        self.now = int(time.time())

    def _codex_transcript(self, sid):
        p = Path(self.d) / (sid + ".jsonl")
        recs = [{"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": "2026-09-10T00:00:00Z",
                 "promptSource": "sdk", "message": {"role": "user", "content": [{"type": "text", "text": "wire the fixtures"}]}},
                {"type": "assistant", "uuid": "a0", "parentUuid": "u0", "timestamp": "2026-09-10T00:00:30Z",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}]
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return str(p)

    @staticmethod
    def _prompt_authors(tree):
        return [a["author"] for t in tree["turns"] for a in t["atoms"] if a.get("type") == "user"]

    def test_a_dead_codex_row_answers_human_on_both_sides(self):
        self.assertFalse(self.cx.owns(self.DEAD), "owns() stays live-only: send routing relies on it")
        self.assertTrue(km._display_sdk_human(self.DEAD), "the display: a dead row is still the session's record")
        self.assertTrue(jd._sdk_owned(self.DEAD), "the judges, through the kernel's hook: the same answer")
        p = self._codex_transcript(self.DEAD)
        disp = km._parse(p, self.DEAD, self.now)
        self.assertEqual(self._prompt_authors(disp), ["human"], "the typed prompt keeps its human bubble after the death")
        judges = jd.parsed_session(self.DEAD, [p], self.now)
        self.assertIs(judges, disp, "one slot: the judges read the display's tree, never a second flag's")
        self.assertTrue(jd._PARSE_CACHE[self.DEAD][3])

    def test_a_live_row_and_no_row_answer_as_before(self):
        self.assertTrue(self.cx.owns(self.LIVE))
        self.assertTrue(km._display_sdk_human(self.LIVE)); self.assertTrue(jd._sdk_owned(self.LIVE))
        self.assertFalse(km._display_sdk_human(self.NONE), "no record on any backend: not a backend's session")
        self.assertFalse(jd._sdk_owned(self.NONE))
        p = self._codex_transcript(self.NONE)
        self.assertEqual(self._prompt_authors(km._parse(p, self.NONE, self.now)), ["sdk"],
                         "an unowned transcript's promptSource sdk stays programmatic")


if __name__ == "__main__":
    unittest.main()
