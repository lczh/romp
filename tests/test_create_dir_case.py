#!/usr/bin/env python3
"""The silent wrong-case launch (the user 2026-07-17): a new-session dir typed with the wrong case
passes the isdir check on macOS's case-insensitive filesystem and the session genuinely launches —
but the cwd STRING is load-bearing: the Claude CLI encodes its transcript/projects dir from its own
getcwd (true on-disk casing) while romp's discovery encodes the registry string, so the transcript
is never found and the launch looks silently dead. Two seams fix it:

1. create time — _resolve_create_dir canonicalizes casing via _true_case (exercised here);
2. first turn — the SDK init handler adopts the CLI-reported cwd when it differs and holds the
   transcript (registry_cwd_for, exercised here; the wiring source-pinned in the repo's established
   style). Every turn's init reports the directory the agent's shell has cd'd to, which holds no
   transcript: taking that string lost a comment thread's whole popover (2026-10-06).
"""
import shutil
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cdc", os.path.join(ROOT, "bin", "romp-kernel"))
sb = load_source("romp_sdk_backend_cdc", os.path.join(ROOT, "bin", "romp_sdk_backend.py"))


class TrueCase(unittest.TestCase):
    def test_wrong_case_component_is_corrected(self):
        # Pure listdir logic, so this corrects on case-sensitive filesystems too — no macOS gate.
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "MixedCase", "Inner")
            os.makedirs(real)
            typed = os.path.join(td, "mixedcase", "inner")
            self.assertEqual(km._true_case(typed), real)

    def test_correct_case_passes_through(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "MixedCase")
            os.makedirs(real)
            self.assertEqual(km._true_case(real), real)

    def test_ambiguous_match_keeps_the_typed_component(self):
        # Two case-variant siblings (possible only on a case-sensitive filesystem): no unique
        # match, so the component stays as typed — never guess between real directories.
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "Case"), os.path.join(td, "CASE")
            os.makedirs(a)
            try:
                os.makedirs(b)
            except OSError:
                self.skipTest("case-insensitive filesystem cannot host the ambiguity")
            typed = os.path.join(td, "case")
            self.assertEqual(km._true_case(typed), typed)

    def test_missing_component_kept_as_given(self):
        with tempfile.TemporaryDirectory() as td:
            typed = os.path.join(td, "NoSuchDir")
            self.assertEqual(km._true_case(typed), typed)


class ResolveCreateDir(unittest.TestCase):
    def test_wrong_case_resolves_to_on_disk_casing(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(os.path.realpath(td), "ProjHome")
            os.makedirs(real)
            typed = os.path.join(td, "projhome")
            if not os.path.isdir(typed):   # case-sensitive filesystem: the typed path errors loudly
                _, err = km._resolve_create_dir(typed)
                self.assertIn("directory not found", err)
                return
            path, err = km._resolve_create_dir(typed)   # case-insensitive: silently launching a
            self.assertIsNone(err)                      # mismatched-string session is the bug
            self.assertEqual(path, real)

    def test_trailing_slash_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(os.path.realpath(td), "ProjHome")
            os.makedirs(real)
            path, err = km._resolve_create_dir(real + "/")
            self.assertIsNone(err)
            self.assertEqual(path, real)

    def test_missing_dir_still_errors_loudly(self):
        path, err = km._resolve_create_dir("/no/such/dir/anywhere")
        self.assertIsNone(path)
        self.assertIn("directory not found", err)


class InitCwdAdoptPins(unittest.TestCase):
    """The runtime heal for any residual variant (symlink text, a mount alias): the CLI's init
    message reports its own cwd, and the SDK session adopts into self.cwd + the registry the
    directory registry_cwd_for names, the moment it differs."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "kernel", "sdk_backend.py")) as f:
            cls.src = f.read()

    def test_init_branch_adopts_the_directory_that_holds_the_transcript(self):
        self.assertIn('cli_cwd = d.get("cwd")', self.src)
        self.assertIn("registry_cwd_for(self.cwd, cli_cwd, (fsid, loaded_sid))", self.src,
                      "the transcript the CLI loaded and the one it writes both witness the directory")
        self.assertIn("self.cwd = adopt", self.src)
        self.assertIn("_update_reg(self.sid, cwd=adopt)", self.src)
        self.assertNotIn("self.cwd = cli_cwd", self.src, "never the CLI's string unchecked")

    def test_adoption_is_guarded_and_logged(self):
        self.assertIn("cli_cwd != self.cwd", self.src, "same string adopts nothing")
        self.assertIn("if adopt != self.cwd:", self.src)
        self.assertIn("adopting CLI cwd", self.src, "the heal is loud in the backend log")


class InitCwdFollowsTheTranscript(unittest.TestCase):
    """registry_cwd_for: the registry's cwd is the directory whose projects dir holds the transcript, since every read
    of the transcript is keyed on it. SYNTHETIC dirs under a temp root, a temp CLAUDE_CONFIG_DIR."""

    FSID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.td = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.td, "claude")
        self.addCleanup(lambda: os.environ.__setitem__("CLAUDE_CONFIG_DIR", saved) if saved is not None
                        else os.environ.pop("CLAUDE_CONFIG_DIR", None))
        self.launch = os.path.join(self.td, "notes-api")
        self.sub = os.path.join(self.launch, "tests", "fixtures")
        self.other = os.path.join(self.launch, "docs")
        self.outside = os.path.join(self.td, "scratch")
        for d in (self.sub, self.other, self.outside):
            os.makedirs(d)

    def _transcript_under(self, cwd, fsid=None):
        p = sb.transcript_path(cwd, fsid or self.FSID)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        return p

    def test_a_shell_cd_leaves_the_launch_directory(self):
        self._transcript_under(self.launch)
        self.assertEqual(sb.registry_cwd_for(self.launch, self.sub, (self.FSID,)), self.launch)
        self.assertEqual(sb.registry_cwd_for(self.launch, self.outside, (self.FSID,)), self.launch,
                         "a cd outside the launch tree keeps the registry's own, which holds it")

    def test_a_registry_an_earlier_adoption_moved_is_mended(self):
        self._transcript_under(self.launch)
        self.assertEqual(sb.registry_cwd_for(self.sub, self.other, (self.FSID,)), self.launch)
        self.assertEqual(sb.registry_cwd_for(self.sub, self.launch, (self.FSID,)), self.launch)

    def test_a_directory_the_transcript_moved_to_is_adopted(self):
        # a worktree (EnterWorktree) or a live move: the CLI moved the transcript under its new cwd
        self._transcript_under(self.other)
        self.assertEqual(sb.registry_cwd_for(self.launch, self.other, (self.FSID,)), self.other)

    def test_the_file_the_cli_loaded_witnesses_a_resume_that_mints_a_new_id(self):
        # the init that lands a new fsid comes before the CLI writes that file
        self._transcript_under(self.launch)
        self.assertEqual(sb.registry_cwd_for(self.launch, self.sub, ("77777777-8888-9999-aaaa-bbbbbbbbbbbb", self.FSID)),
                         self.launch)

    def test_nothing_written_yet_takes_the_clis_string(self):
        # a fresh session's first init: the 2026-07-17 variant spelling, adopted as before
        link = os.path.join(self.td, "notes-api-link")
        os.symlink(self.launch, link)
        self.assertEqual(sb.registry_cwd_for(link, self.launch, (self.FSID, None)), self.launch)

    def test_a_variant_spelling_that_holds_the_transcript_is_adopted(self):
        link = os.path.join(self.td, "notes-api-link")
        os.symlink(self.launch, link)
        self._transcript_under(self.launch)
        self.assertEqual(sb.registry_cwd_for(link, self.launch, (self.FSID,)), self.launch)

if __name__ == "__main__":
    unittest.main()
