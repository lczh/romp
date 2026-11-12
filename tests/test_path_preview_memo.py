#!/usr/bin/env python3
"""A chat build judges each linked file ONCE per pass, and resolves the roots it judges against once (2026-11-12).

Every message that names a file ships a verdict beside the link: may the hover preview fetch it, and if not, why
(_path_preview_verdicts over _slice_allowed). A whole-history build judged every mention of every file from nothing,
and each judgement resolved the user's home twice, the session's folder once and the file itself twice: about thirty
stat calls a link. A lab session of 5,000 replies naming 500 files (20,000 links) made 616,000 stat calls inside the
verdicts, 6.5 s of a 7.3 s build on an idle machine, and each of those calls is a turn at the interpreter lock in the
one process every page waits on after a restart. A build now carries a memo for its pass: a target is judged once,
the three roots are resolved once, and the path is resolved once per judgement (7,200 calls for the same session).

The verdict decides whether the page may ASK for a preview, so what is pinned is first that nothing about it moved:
  * the verdicts are the old function's, over a generated set of paths (allowed by each root, refused for each
    reason, missing, symlinked every way a link can lie, outside the roots), with a memo and without one; the old
    implementation is kept below as the oracle;
  * the memo is keyed on the session as well as the target, lives for one pass, and never reaches the slice route,
    which judges at the moment it serves;
  * a file created, deleted or outgrown between two builds is seen by the next;
and then the cost, counted in stat calls made inside the verdicts (never timed, so it cannot flake):
  * a build makes the same number however many messages name a file;
  * a first judgement costs one resolution of the file and two stats, and the roots are resolved once per build.

Synthetic fixtures only: invented names and text, placeholder ids, the notes-api demo world."""
import json
import os
import random
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic state BEFORE the load (import-time root)
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_pathpreviewmemo", os.path.join(BIN, "romp-kernel"))
em, jd = km.em, km.jd

SID = "7a1c0de5-0000-4000-8000-00000000e1e1"            # private to this module
PEER = "7a1c0de5-0000-4000-8000-00000000e1e2"           # a second session, in another folder
STRAY = "7a1c0de5-0000-4000-8000-00000000e1e3"          # a session the registry does not list: no folder


# ---- THE ORACLE: the verdict as it stood before the memo, verbatim but for the module prefix. Every judgement
#      resolves the home twice, the session's folder once and the file twice, and nothing is kept between two.
def _old_secret_path(real):
    if km._SECRET_NAME_RE.search(os.path.basename(real)):
        return True
    home = os.path.realpath(os.path.expanduser("~"))
    rel = real[len(home) + 1:] if real.startswith(home + os.sep) else real
    return bool(km._SECRET_DIR_RE.search(rel.replace(os.sep, "/")))


def _old_slice_confined(fp, sid):
    real = os.path.realpath(fp)
    roots = [os.path.realpath(os.path.expanduser("~"))]
    base = km._cwd_of(sid) if sid else None
    if base:
        roots.append(os.path.realpath(base))
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _old_glossary_owned(real):
    try:
        d = os.path.realpath(str(km._glossary_dir()))
    except Exception:
        return False
    return os.path.dirname(real) == d and real.endswith(".md")


def _old_slice_allowed(fp, sid):
    if not os.path.isabs(fp):
        return None, "not a file"
    real = os.path.realpath(fp)
    if not os.path.isfile(real):
        return None, "not a file"
    if _old_secret_path(real):
        return None, "a secrets-shaped name"
    kind = km._slice_kind(real)
    if not kind:
        return None, "not a kind the preview shows"
    if km._slice_kind(fp) != kind:
        return None, "a link dressed as another kind"
    if not _old_slice_confined(real, sid) and not _old_glossary_owned(real):
        return None, "outside the session's folder and your home"
    try:
        size = os.path.getsize(real)
    except OSError:
        return None, "unreadable"
    if size > (km._TEXT_MAX_BYTES if kind in ("markdown", "code") else km._PREVIEW_MAX_BYTES):
        return None, "too large to show"
    return kind, ""


def _old_path_preview_verdicts(links, sid):
    kinds, whys = {}, {}
    for tok, target in (links or {}).items():
        fp = km._resolve_open_path(target, sid)
        kind, why = _old_slice_allowed(fp, sid)
        if kind and kind == "markdown":
            why = km._slice_warm_why(fp)
            if why:
                kind = None
        if kind:
            kinds[tok] = kind
        else:
            whys[tok] = why or "not previewed"
    return kinds, whys


TEXT_CAP = 4096                                         # the text cap for these tests: "too large" is a 5 KB file
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class _World(unittest.TestCase):
    """A machine of this test's own: a home, a session folder OUTSIDE it (so each root admits files the other does not),
    a second session's folder, a config directory with the glossaries folder outside both, and a place outside
    everything. The state root is the test's too, so it writes `off` into its session-hosts."""

    def setUp(self):
        self.lab = os.path.realpath(tempfile.mkdtemp(prefix="pathpreview-"))
        self.home = self.mk("home/dev")
        self.cwd = self.mk("work/notes-api")
        self.peer_cwd = self.mk("work/billing")
        self.cfg = self.mk("config")
        self.mk("config/glossaries")
        self.out = self.mk("elsewhere")
        td = Path(self.mk("state"))
        (td / "session-hosts").write_text("off")        # a state root of this test's own: no session host
        proj = td / "projects"
        self.pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", self.cwd)
        self.pdir.mkdir(parents=True)
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % self.cwd)
        (names / PEER).write_text("api\t%s\t#fedcba\n" % self.peer_cwd)
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "CLAUDE_CONFIG_DIR": self.cfg})
        self.env.start()
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map,
                      km._GLOBAL_CLAUDE_MD, km._TEXT_MAX_BYTES)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE = td / "captions", td / "archive", td / "goals", td
        km.NAMES, km._GLOBAL_CLAUDE_MD = names, td / "no-global-claude.md"
        km._TEXT_MAX_BYTES = TEXT_CAP
        self.now = int(time.time())
        self.tm = {}
        km._live_map = lambda: self.tm
        self._uuid = 0
        self._reset()

    def _reset(self):
        km._chat_fold.clear(); km._parse_cache.clear(); km._PATH_LINK_CACHE.clear(); km._SLICE_CACHE.clear()
        if isinstance(jd._discover_cache, dict):
            jd._discover_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map,
         km._GLOBAL_CLAUDE_MD, km._TEXT_MAX_BYTES) = self.saved
        self.env.stop()
        self._reset()
        import shutil
        shutil.rmtree(self.lab, ignore_errors=True)

    def mk(self, rel):
        d = os.path.join(self.lab, rel)
        os.makedirs(d, exist_ok=True)
        return d

    def w(self, fp, body="# T\n\ntext\n"):
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(body if isinstance(body, bytes) else body.encode("utf-8"))
        return fp

    def ln(self, target, link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(target, link)
        return link

    # ---- a session whose replies name files, and one whole-history build of it
    def session(self, replies, sid=SID):
        """The transcript of session `sid`, in the notes-api folder: one turn per reply, a typed prompt then the reply's
        prose. A test that needs two transcripts gives each its own session, never one path written twice (the record
        reader takes a file that grew for the file it already read plus an append)."""
        (Path(str(km.NAMES)) / sid).write_text("web\t%s\t#abcdef\n" % self.cwd)
        self.tm[sid] = {"state": "idle", "since": self.now - 100, "model": "", "effort": "", "context": None,
                        "compactPct": None, "color": None}
        tpath = self.pdir / (sid + ".jsonl")
        self.assertFalse(tpath.exists(), "one transcript per session in these tests")
        recs, t, last = [], self.now - 86400, None
        for i, text in enumerate(replies):
            for kind, content, extra in (("user", "step %d: tidy the notes-api module" % i, {}),
                                         ("assistant", [{"type": "text", "text": text}],
                                          {"model": "claude-sonnet-4", "stop_reason": "end_turn"})):
                t += 5
                self._uuid += 1
                u = "cccccccc-0000-0000-0000-%012d" % self._uuid
                r = {"type": kind, "timestamp": _iso(t), "uuid": u, "parentUuid": last, "cwd": self.cwd,
                     "version": "2.1.0", "gitBranch": "main", "message": dict({"role": kind, "content": content}, **extra)}
                if kind == "user":
                    r["promptSource"] = "typed"
                last = u
                recs.append(r)
        with open(tpath, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def build(self, sid=SID):
        """build_session over the whole history, under the names snapshot a push builds under. Returns what the built
        events say of each link: ({token: kind}, {token: why}), every event's merged."""
        km._live_scope.names = km._names_snapshot()
        try:
            m = km.build_session(sid, self.now, self.tm, floor=0)
        finally:
            km._live_scope.names = None
        self.events = [e for e in m["events"] if e.get("pathLinks") is not None]
        kinds, whys = {}, {}
        for e in self.events:
            kinds.update(e.get("pathPreview") or {})
            whys.update(e.get("pathPreviewWhy") or {})
        return kinds, whys


def _corpus(w, rng):
    """[(case, target)]: link targets as a message's pathLinks carries them (relative to the session's folder,
    absolute, or under ~), over every way a verdict can come out. `w` is the _World that owns the tree; `rng` picks
    names, depths and the form of each target, so two seeds lay the same cases out differently."""
    out = []
    stem = lambda: "%s-%d" % (rng.choice(["plan", "notes", "report", "route", "pane", "index"]), rng.randrange(10 ** 6))
    sub = lambda: os.path.join(*rng.sample(["docs", "design", "src", "ui", "tests", "assets"], rng.randrange(0, 4)) or ["."])
    leak = "# L\n\n" + "api" + "_key" + " = " + "Z" * 24 + "\n"     # assembled here: no credential-shaped literal in the repo

    def put(case, fp):
        """The target for `fp` in one of the forms that reach it."""
        forms = [fp]
        if fp.startswith(w.cwd + os.sep):
            rel = os.path.relpath(fp, w.cwd)
            forms += [rel, rel, "./" + rel, os.path.join("docs", "..", rel)]
        else:
            forms.append(os.path.relpath(fp, w.cwd))                # ../../elsewhere/x.md: a relative walk out of the folder
        if fp.startswith(w.home + os.sep):
            forms += ["~/" + os.path.relpath(fp, w.home)] * 2
        out.append((case, rng.choice(forms)))

    w.mk("work/notes-api/docs")
    for _ in range(3):                                              # ---- allowed, by each root
        put("folder-md", w.w(os.path.join(w.cwd, sub(), stem() + rng.choice([".md", ".markdown", ".mdx"]))))
        put("folder-code", w.w(os.path.join(w.cwd, sub(), stem() + rng.choice([".py", ".ts", ".json", ".sh"])), "x = 1\n"))
        put("folder-image", w.w(os.path.join(w.cwd, sub(), stem() + rng.choice([".png", ".jpg", ".svg"])), PNG))
        put("folder-pdf", w.w(os.path.join(w.cwd, sub(), stem() + ".pdf"), b"%PDF-1.4\n"))
        put("home-md", w.w(os.path.join(w.home, sub(), stem() + ".md")))
        put("home-code", w.w(os.path.join(w.home, sub(), stem() + ".py"), "y = 2\n"))
        put("glossary", w.w(os.path.join(w.cfg, "glossaries", stem() + ".md"), "# G\n\n## Term\n\nmeaning\n"))
        real = w.w(os.path.join(w.cwd, sub(), stem() + ".md"))
        put("link-same-kind", w.ln(real, os.path.join(w.cwd, sub(), stem() + ".md")))
        put("link-into-the-folder", w.ln(real, os.path.join(w.out, stem() + ".md")))
        d = os.path.join(w.cwd, "dir-" + stem()); w.w(os.path.join(d, "inner.md"))
        put("through-a-folder-link-inside", os.path.join(w.ln(d, os.path.join(w.cwd, "ln-" + stem())), "inner.md"))
    for _ in range(3):                                              # ---- refused, for each reason
        put("secret-name", w.w(os.path.join(w.cwd, sub(), rng.choice([".env", ".env.local", "id_ed25519", "service.pem",
                                                                    "credentials.json", "token-notes.md"])), "x\n"))
        put("secret-directory", w.w(os.path.join(w.home, rng.choice([".ssh", ".aws", ".config/gh", ".kube"]), stem() + ".md")))
        put("kind", w.w(os.path.join(w.cwd, sub(), stem() + rng.choice([".zip", ".bin", ".sqlite"])), b"PK\x03\x04"))
        code = w.w(os.path.join(w.cwd, sub(), stem() + ".py"), "z = 3\n")
        put("dressed", w.ln(code, os.path.join(w.cwd, sub(), stem() + ".md")))
        secret = w.w(os.path.join(w.cwd, "sec-" + stem(), ".env"), "x\n")
        put("link-to-a-secret", w.ln(secret, os.path.join(w.cwd, sub(), stem() + ".md")))
        blob = w.w(os.path.join(w.cwd, sub(), stem() + ".zip"), b"PK\x03\x04")
        put("link-to-a-refused-kind", w.ln(blob, os.path.join(w.cwd, sub(), stem() + ".md")))
        outside = w.w(os.path.join(w.out, sub(), stem() + ".md"))
        put("outside", outside)
        put("outside", w.w(os.path.join(w.peer_cwd, stem() + ".py"), "p = 4\n"))        # another session's folder
        put("link-out", w.ln(outside, os.path.join(w.cwd, sub(), stem() + ".md")))
        pkg = os.path.join(w.out, "pkg-" + stem()); w.w(os.path.join(pkg, "readme.md"))
        put("through-a-folder-link-out", os.path.join(w.ln(pkg, os.path.join(w.cwd, "vendor-" + stem())), "readme.md"))
        put("config-not-glossary", w.w(os.path.join(w.cfg, stem() + ".md")))
        put("config-not-glossary", w.w(os.path.join(w.cfg, "glossaries", "sub-" + stem(), "deep.md")))
        put("config-not-glossary", w.w(os.path.join(w.cfg, "glossaries", stem() + ".txt"), "plain\n"))
        put("too-large", w.w(os.path.join(w.cwd, sub(), stem() + ".md"), "# Big\n" + "line\n" * (TEXT_CAP // 4)))
        put("directory", w.mk(os.path.join("work/notes-api", "folder-" + stem() + ".md")))
        put("secret-text", w.w(os.path.join(w.cwd, sub(), stem() + ".md"), leak))
        put("not-text", w.w(os.path.join(w.cwd, sub(), stem() + ".md"), b"# B\n\x00\x00\x00"))
    for _ in range(3):                                              # ---- missing
        put("missing", os.path.join(w.cwd, sub(), stem() + ".md"))
        put("missing", os.path.join(w.cwd, "no-such-folder", stem() + ".py"))
        put("dangling-link", w.ln(os.path.join(w.cwd, "gone-" + stem() + ".md"), os.path.join(w.cwd, sub(), stem() + ".md")))
        loop = os.path.join(w.cwd, sub(), stem() + ".md")
        put("link-to-itself", w.ln(loop, loop))
    rng.shuffle(out)
    return out


EXPECT = {      # case -> the verdict every instance of it must get (a kind, or the refusal's words)
    "folder-md": "markdown", "folder-code": "code", "folder-image": "image", "folder-pdf": "pdf", "home-md": "markdown",
    "home-code": "code", "glossary": "markdown", "link-same-kind": "markdown", "link-into-the-folder": "markdown",
    "through-a-folder-link-inside": "markdown",
    "secret-name": "a secrets-shaped name", "secret-directory": "a secrets-shaped name",
    "kind": "not a kind the preview shows", "dressed": "a link dressed as another kind",
    "link-to-a-secret": "a secrets-shaped name", "link-to-a-refused-kind": "not a kind the preview shows",
    "outside": "outside the session's folder and your home", "link-out": "outside the session's folder and your home",
    "through-a-folder-link-out": "outside the session's folder and your home",
    "config-not-glossary": "outside the session's folder and your home", "too-large": "too large to show",
    "directory": "not a file", "secret-text": "looks like a secret", "not-text": "not text",
    "missing": "not a file", "dangling-link": "not a file", "link-to-itself": "not a file",
}


class SameVerdictsAsTheUnmemoizedFunction(_World):
    def test_every_verdict_matches_the_oracle_with_a_memo_and_without_one(self):
        for seed in (1, 2, 3):
            rng = random.Random(seed)
            corpus = _corpus(self, rng)
            links = {"t%03d" % i: target for i, (_case, target) in enumerate(corpus)}
            want = _old_path_preview_verdicts(links, SID)
            self.assertEqual(km._path_preview_verdicts(links, SID), want, "seed %d, no memo" % seed)
            memo = {}
            self.assertEqual(km._path_preview_verdicts(links, SID, memo), want, "seed %d, a fresh memo" % seed)
            self.assertEqual(km._path_preview_verdicts(links, SID, memo), want, "seed %d, the same memo read back" % seed)
            # the way a build asks: a few links per message, in any order, the same target in many messages, one memo
            memo, got = {}, ({}, {})
            toks = list(links) * 3
            rng.shuffle(toks)
            while toks:
                part = {tok: links[tok] for tok in toks[:rng.randrange(1, 6)]}
                del toks[:len(part)]
                kinds, whys = km._path_preview_verdicts(part, SID, memo)
                for tok in part:
                    self.assertEqual((kinds.get(tok), whys.get(tok)), (want[0].get(tok), want[1].get(tok)),
                                     "seed %d: %r judged message by message" % (seed, links[tok]))
                got[0].update(kinds); got[1].update(whys)
            self.assertEqual(got, want)
            # ...and the judgement under it, for a session with this folder, one with another, and none at all
            for sid in (SID, PEER, STRAY, None):
                memo = {}
                for _case, target in corpus:
                    fp = km._resolve_open_path(target, sid)
                    old = _old_slice_allowed(fp, sid)
                    self.assertEqual(km._slice_allowed(fp, sid), old, "seed %d: %r for %r" % (seed, target, sid))
                    self.assertEqual(km._slice_allowed(fp, sid, memo), old, "seed %d: %r for %r, with a memo" % (seed, target, sid))
                    self.assertEqual(km._slice_confined(fp, sid, memo), _old_slice_confined(fp, sid), repr(target))
                    self.assertEqual(km._secret_path(os.path.realpath(fp), memo), _old_secret_path(os.path.realpath(fp)), repr(target))
                    self.assertEqual(km._glossary_owned(os.path.realpath(fp), memo), _old_glossary_owned(os.path.realpath(fp)), repr(target))

    def test_the_corpus_reaches_every_verdict_it_claims(self):
        # the comparison above is only as good as its paths: each case comes out the way its name says, in every seed,
        # so every kind and every refusal the verdict can give is in the set
        for seed in (1, 2, 3):
            corpus = _corpus(self, random.Random(seed))
            links = {"t%03d" % i: target for i, (_case, target) in enumerate(corpus)}
            kinds, whys = km._path_preview_verdicts(links, SID, {})
            for i, (case, target) in enumerate(corpus):
                tok = "t%03d" % i
                self.assertEqual(kinds.get(tok) or whys.get(tok), EXPECT[case], "seed %d: %s, %r" % (seed, case, target))
            self.assertEqual({c for c, _ in corpus}, set(EXPECT))
            forms = {"relative" if not os.path.isabs(os.path.expanduser(t)) else "home" if t.startswith("~") else "absolute"
                     for _c, t in corpus}
            self.assertEqual(forms, {"relative", "home", "absolute"}, "targets in every form a link carries")


class TheMemoIsOnePassOfOneSession(_World):
    def test_a_memo_shared_by_two_sessions_judges_each_against_its_own_folder(self):
        # the folders are outside the home, so a file in one is allowed for the session that works there and for no
        # other. A memo that forgot the session would hand the first session's "allowed" to the second
        mine = self.w(os.path.join(self.cwd, "docs", "plan.md"))
        theirs = self.w(os.path.join(self.peer_cwd, "docs", "plan.md"))
        for first, second in ((SID, PEER), (PEER, SID)):
            memo = {}
            for sid in (first, second, first):
                for fp in (mine, theirs):
                    self.assertEqual(km._path_preview_verdicts({"plan": fp}, sid, memo),
                                     _old_path_preview_verdicts({"plan": fp}, sid), "%r for %r" % (fp, sid))
                    self.assertEqual(km._slice_allowed(fp, sid, memo), _old_slice_allowed(fp, sid))
        # the same relative target is a different file for each session
        memo = {}
        os.remove(theirs)
        self.assertEqual(km._path_preview_verdicts({"docs/plan.md": "docs/plan.md"}, SID, memo), ({"docs/plan.md": "markdown"}, {}))
        self.assertEqual(km._path_preview_verdicts({"docs/plan.md": "docs/plan.md"}, PEER, memo), ({}, {"docs/plan.md": "not a file"}))

    def test_a_new_pass_judges_every_file_again(self):
        # each pass has its own memo; what the last one held is nobody's
        fp = os.path.join(self.cwd, "docs", "report.md")
        links = {"docs/report.md": "docs/report.md"}
        self.assertEqual(km._path_preview_verdicts(links, SID, {}), ({}, {"docs/report.md": "not a file"}))
        self.w(fp)
        self.assertEqual(km._path_preview_verdicts(links, SID, {}), ({"docs/report.md": "markdown"}, {}), "created since: allowed")
        self.w(fp, "# Big\n" + "line\n" * TEXT_CAP)
        self.assertEqual(km._path_preview_verdicts(links, SID, {}), ({}, {"docs/report.md": "too large to show"}), "outgrew the cap: refused")
        os.remove(fp)
        self.ln(self.w(os.path.join(self.cwd, ".env"), "x\n"), fp)
        self.assertEqual(km._path_preview_verdicts(links, SID, {}), ({}, {"docs/report.md": "a secrets-shaped name"}), "re-pointed at a secret: refused")
        os.remove(fp)
        self.assertEqual(km._path_preview_verdicts(links, SID, {}), ({}, {"docs/report.md": "not a file"}), "deleted: refused")

    def test_the_roots_are_read_again_by_a_new_pass(self):
        # the session moves: the old folder's files are outside for the next pass, the new folder's inside
        old_file = self.w(os.path.join(self.cwd, "docs", "plan.md"))
        new_home = self.mk("work/notes-api-moved")
        new_file = self.w(os.path.join(new_home, "docs", "plan.md"))
        memo = {}
        self.assertEqual(km._slice_allowed(old_file, SID, memo), ("markdown", ""))
        (Path(str(km.NAMES)) / SID).write_text("web\t%s\t#abcdef\n" % new_home)
        memo = {}
        self.assertEqual(km._slice_allowed(old_file, SID, memo), (None, "outside the session's folder and your home"))
        self.assertEqual(km._slice_allowed(new_file, SID, memo), ("markdown", ""))

    def test_the_route_judges_at_the_moment_it_serves_whatever_a_pass_decided(self):
        # the verdict a build ships says only whether the page may ask. The answer to the ask is the route's own
        # judgement, made with no memo, so a file that turned into something the preview refuses after the build
        # judged it is refused when it is asked for
        fp = self.w(os.path.join(self.cwd, "docs", "report.md"))
        self.session(["Wrote `docs/report.md`."])
        self.assertEqual(self.build(), ({"docs/report.md": "markdown"}, {}))
        status, body, _ctype = km._slice_body(fp, SID, None)
        self.assertEqual((status, body["allowed"]), (200, True))
        os.remove(fp)
        self.ln(self.w(os.path.join(self.cwd, ".env"), "x\n"), fp)
        status, body, _ctype = km._slice_body(fp, SID, None)
        self.assertEqual((status, body["allowed"], body["why"]), (403, False, "a secrets-shaped name"))
        os.remove(fp)
        status, body, _ctype = km._slice_body(fp, SID, None)
        self.assertEqual((status, body["why"]), (403, "not a file"))


class TheNextBuildSeesWhatChanged(_World):
    def test_a_file_created_after_one_build_is_previewed_by_the_next(self):
        self.w(os.path.join(self.cwd, "docs", "plan.md"))
        self.session(["I followed `docs/plan.md` and will write `docs/report.md` next."])
        self.assertEqual(self.build(), ({"docs/plan.md": "markdown"}, {}), "the report does not exist yet: no link, no verdict")
        self.w(os.path.join(self.cwd, "docs", "report.md"))
        self.assertEqual(self.build(), ({"docs/plan.md": "markdown", "docs/report.md": "markdown"}, {}),
                         "the next build links the file the agent has written since and says it previews")

    def test_a_file_deleted_outgrown_or_restored_between_builds_is_judged_as_it_stands(self):
        # the session's last turn, which every build shapes again (an earlier turn can come from the fold with the
        # verdicts it was built with, as it always could)
        fp = self.w(os.path.join(self.cwd, "docs", "report.md"))
        self.w(os.path.join(self.cwd, "src", "route.py"), "x = 1\n")
        self.session(["Changed `src/route.py`; `docs/report.md` covers it. See `docs/report.md` for the rest."])
        both = {"docs/report.md": "markdown", "src/route.py": "code"}
        self.assertEqual(self.build(), (both, {}))
        os.remove(fp)
        self.assertEqual(self.build(), ({"src/route.py": "code"}, {"docs/report.md": "not a file"}), "deleted since: refused")
        self.w(fp, "# Big\n" + "line\n" * TEXT_CAP)
        self.assertEqual(self.build(), ({"src/route.py": "code"}, {"docs/report.md": "too large to show"}), "back, but past the cap")
        self.w(fp)
        self.assertEqual(self.build(), (both, {}), "restored: previewed again")

    def test_every_event_of_a_build_carries_the_oracles_verdicts(self):
        # the whole path: a session whose replies name the generated corpus's files, a few per message and each in
        # several messages; every built event's verdicts are the old function's for that event's links
        rng = random.Random(7)
        targets = [t for _case, t in _corpus(self, rng)]
        replies = []
        for _ in range(60):
            replies.append("Looked at " + ", ".join("`%s`" % t for t in rng.sample(targets, rng.randrange(1, 5))) + " for this step.")
        self.session(replies)
        kinds, whys = self.build()
        self.assertGreater(len(self.events), 50)
        for e in self.events:
            want = _old_path_preview_verdicts(e["pathLinks"], SID)
            self.assertEqual((e.get("pathPreview", {}), e.get("pathPreviewWhy", {})), want, "the event naming %r" % sorted(e["pathLinks"]))
        self.assertGreaterEqual(set(kinds.values()), {"markdown", "code", "image", "pdf"})
        self.assertGreaterEqual(set(whys.values()), {"a secrets-shaped name", "not a kind the preview shows", "a link dressed as another kind",
                                                     "outside the session's folder and your home", "too large to show",
                                                     "looks like a secret", "not text"}, "the build met every refusal a file that exists can get")


class _StatCalls(object):
    """Counts the stat-family calls (os.stat, os.lstat, os.readlink: what resolving a path and reading a file's facts
    cost) made INSIDE _path_preview_verdicts, and the links it was asked about. The verdict function is wrapped where
    the build looks it up, with whatever arguments the build passes, so the same count reads the old code and the new.
    Only the thread that entered the counter is counted: the patch on `os` is process-wide, and a kernel thread of
    its own (a sweep, a watcher) that stats a file while the build runs is not the build's cost."""

    def __enter__(self):
        self.calls = self.links = self.inside = 0
        self._tid = threading.get_ident()
        self._os = {n: getattr(os, n) for n in ("stat", "lstat", "readlink")}
        for n, real in self._os.items():
            setattr(os, n, self._counted(real))
        self._pv = km._path_preview_verdicts
        km._path_preview_verdicts = self._verdicts
        return self

    def _counted(self, real):
        def call(*a, **k):
            if self.inside and threading.get_ident() == self._tid:
                self.calls += 1
            return real(*a, **k)
        return call

    def _verdicts(self, links, *a, **k):
        if threading.get_ident() != self._tid:
            return self._pv(links, *a, **k)
        self.links += len(links or {})
        self.inside += 1
        try:
            return self._pv(links, *a, **k)
        finally:
            self.inside -= 1

    def cost(self, fn):
        """The stat-family calls `fn()` makes, measured on this machine's tree (not added to the count)."""
        before, self.inside = self.calls, self.inside + 1
        try:
            fn()
        finally:
            self.inside -= 1
            n, self.calls = self.calls - before, before
        return n

    def __exit__(self, *exc):
        km._path_preview_verdicts = self._pv
        for n, real in self._os.items():
            setattr(os, n, real)


class TheCostIsPerFileNotPerMention(_World):
    def _build_naming(self, files, n):
        """A whole-history build of a session of `n` replies that each name every one of `files`: its stat calls inside
        the verdicts. Each `n` is a session of its own."""
        sid = "7a1c0de5-0000-4000-8000-0000e1e%05d" % n
        self._reset()
        self.session(["Step %d touched %s." % (i, " and ".join("`%s`" % f for f in files)) for i in range(n)], sid)
        with _StatCalls() as s:
            kinds, whys = self.build(sid)
        self.assertEqual((sorted(kinds), whys), (sorted(files), {}), "every file named is linked and previews")
        self.assertEqual(s.links, n * len(files), "the build asked about every mention")
        return s

    def test_a_build_makes_the_same_stat_calls_however_many_messages_name_a_file(self):
        files = ["docs/plan.md", "src/route.py", "assets/shot.png"]
        self.w(os.path.join(self.cwd, files[0])); self.w(os.path.join(self.cwd, files[1]), "x = 1\n")
        self.w(os.path.join(self.cwd, files[2]), PNG)
        few, many = self._build_naming(files, 3), self._build_naming(files, 40)
        self.assertEqual(many.calls, few.calls,
                         "a build naming three files in 40 messages makes the stat calls of one naming them in 3 "
                         "(%d and %d): judged from nothing at every mention, the 40 cost thirteen times the 3" % (many.calls, few.calls))

    def test_a_first_judgement_resolves_the_file_once_and_a_build_resolves_the_roots_once(self):
        files = ["src/mod_%02d.py" % i for i in range(12)] + ["ui/webview/panes/pane_%02d.ts" % i for i in range(12)]
        for f in files:
            self.w(os.path.join(self.cwd, f), "x = 1\n")
        self._reset()
        self.session(["Step %d touched `%s`." % (i, f) for i, f in enumerate(files)])
        with _StatCalls() as s:
            resolve = [s.cost(lambda f=f: os.path.realpath(os.path.join(self.cwd, f))) for f in files]
            roots = s.cost(lambda: os.path.realpath(self.home)) + s.cost(lambda: os.path.realpath(self.cwd))
            kinds, whys = self.build()
        self.assertEqual((sorted(kinds), set(kinds.values()), whys), (sorted(files), {"code"}, {}))
        self.assertGreater(min(resolve), 3, "resolving a path costs a stat per component: these files sit several folders down")
        # The accounting, exactly. Per file: one resolution of its path (os.path.realpath, measured above on this tree
        # as `resolve`), then two stats of the real path, os.path.isfile and os.path.getsize; a code file is not
        # warmed, so nothing else reads it. Per build: the home and the session's folder resolved once each (`roots`),
        # the folder read from the names snapshot, which costs no stat. The glossaries folder is never resolved: every
        # file is confined by the session's folder, so the glossary check is not reached. Equal, not at most: a call
        # more is a resolution or a stat the change was meant to remove, and a call fewer is a check that went missing.
        expected = sum(r + 2 for r in resolve) + roots
        self.assertEqual(s.calls, expected,
                         "24 files named once each: one resolution of each file and two stats of it (is it a file, how "
                         "large), and the home and the session's folder resolved once for the build (%d calls expected; "
                         "%d made). Each judgement used to resolve its file twice, the home twice and the folder once"
                         % (expected, s.calls))

    def test_a_judgement_read_back_from_the_memo_touches_no_file(self):
        corpus = _corpus(self, random.Random(11))
        links = {"t%03d" % i: target for i, (_case, target) in enumerate(corpus)}
        memo = {}
        with _StatCalls() as s:
            first = km._path_preview_verdicts(links, SID, memo)
            judged = s.calls
            for _ in range(25):
                self.assertEqual(km._path_preview_verdicts(links, SID, memo), first)
        self.assertGreater(judged, len(links), "the first pass over the set stats every file")
        self.assertEqual(s.calls, judged, "and 25 more passes with the same memo make no stat call at all")


if __name__ == "__main__":
    unittest.main()
