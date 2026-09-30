#!/usr/bin/env python3
"""The automatic update of a peer, when another push of the same commit lands first (2026-09-30).

A deploy moves this checkout's HEAD and restarts the kernel seconds later. The kernel being restarted
can already have started the automatic push of the new HEAD to a peer (its supervisor saw the peer
behind the new HEAD): the restart kills that kernel, but its `git push` child outlives it and lands,
while the apply that was the dead kernel's next step (reset the peer, restart it) never runs. The new
kernel then pushes the same commit, and git's ref update, a compare-and-swap against the value the peer
advertised when the push began (--force or not), rejects it: the scratch ref was created in between
("cannot lock ref ... reference already exists"). _update_remote read that as a failed push, so the
peer stayed on its old build through two deploys in a row, with its scratch ref holding exactly the
commit being pushed. And the failure's reason was cut from the head of the push's stderr, which is
where the local pre-push hook's scanner prints its progress, so the row and the Log said nothing.

Real git end to end: a local repo pushes through an ssh stub to a peer repo whose pre-receive hook runs
the other push at exactly that moment, after the advertisement and before the ref update. Synthetic
repos under a temp HOME, placeholder host TESTHOST, no network.
"""
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from romp_load import load_source
from git_fixture import forbid_background, git, init_repo

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")                     # this root is the test's own: no per-session hosts under it
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

# What the local pre-push hook's scanner prints on a clean push, colour codes and all: it leads the
# push's stderr, ahead of anything git says.
SCANNER_CHATTER = ("\x1b[90m10:16PM\x1b[0m \x1b[32mINF\x1b[0m \x1b[1m0 commits scanned.\x1b[0m\n"
                   "\x1b[90m10:16PM\x1b[0m \x1b[32mINF\x1b[0m \x1b[1mscanned ~0 bytes (0) in 917ms\x1b[0m\n"
                   "\x1b[90m10:16PM\x1b[0m \x1b[32mINF\x1b[0m \x1b[1mno leaks found\x1b[0m\n")


class OtherPushLandsFirst(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", HOME=self.home)
        self.peer = os.path.join(self.home, "romp")
        self.local = os.path.join(self.home, "local")
        os.makedirs(self.peer)
        init_repo(self.peer, "-q", "-b", "main", env=self.env)
        self.A = self._commit(self.peer, "a\n", "A")               # where the peer sits
        git(self.home, "clone", "-q", self.peer, self.local, env=self.env)
        forbid_background(self.local, env=self.env)
        self.C = self._commit(self.local, "c\n", "C")              # another build, also a child of A
        git(self.local, "branch", "-q", "other", env=self.env)
        git(self.local, "reset", "-q", "--hard", self.A, env=self.env)
        self.B = self._commit(self.local, "b\n", "B")              # this machine's head, a child of A
        hook = os.path.join(self.local, ".git", "hooks", "pre-push")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\nprintf %s >&2\nexit 0\n" % shlex.quote(SCANNER_CHATTER))
        os.chmod(hook, 0o755)
        gitdirs = [os.path.dirname(shutil.which("git")),
                   subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()]
        self.path = ":".join([d for d in gitdirs if d] + ["/usr/bin", "/bin"])
        stub = os.path.join(self.home, "ssh")
        with open(stub, "w") as fh:            # every ssh leg (probe, git transport, apply) runs locally
            fh.write('#!/usr/bin/env bash\nfor last in "$@"; do :; done\n'
                     'exec env -i HOME=%s PATH=%s ROMP_REPO_ROOT=%s GIT_CONFIG_GLOBAL=/dev/null '
                     'GIT_CONFIG_NOSYSTEM=1 bash -c "$last"\n'
                     % (shlex.quote(self.home), shlex.quote(self.path), shlex.quote(self.peer)))
        os.chmod(stub, 0o755)
        for patch in (mock.patch.object(km, "SSH_BIN", stub),
                      mock.patch.object(km, "ROOT", pathlib.Path(self.local)),
                      mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}),
                      mock.patch.dict(km._remotes, {"TESTHOST": {"host": "TESTHOST"}}),
                      mock.patch.dict(km._HEAD_CACHE, {"ts": 0.0, "full": None, "short": None})):
            patch.start()
            self.addCleanup(patch.stop)

    def _commit(self, repo, text, msg):
        with open(os.path.join(repo, "f"), "w") as fh:
            fh.write(text)
        git(repo, "add", "f", env=self.env)
        git(repo, "commit", "-qm", msg, env=self.env, ident=("romp-test@TESTHOST", "romp-test"))
        return git(repo, "rev-parse", "HEAD", env=self.env).stdout.strip()

    def _other_push_lands_first(self, sha):
        """Arm the peer so that, once, between the advertisement of our push and its ref update, another
        push of `sha` to the scratch ref runs to completion: what the killed kernel's surviving push did."""
        marker = os.path.join(self.home, "armed")
        hook = os.path.join(self.peer, ".git", "hooks", "pre-receive")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\n"
                     "if [ -e %(m)s ]; then rm -f %(m)s; env -i HOME=%(h)s PATH=%(p)s GIT_CONFIG_GLOBAL=/dev/null "
                     "GIT_CONFIG_NOSYSTEM=1 git -C %(l)s push -q --no-verify --force %(r)s %(s)s:refs/heads/%(ref)s "
                     ">/dev/null 2>&1 || exit 1; fi\nexit 0\n"
                     % {"m": shlex.quote(marker), "h": shlex.quote(self.home), "p": shlex.quote(self.path),
                        "l": shlex.quote(self.local), "r": shlex.quote(self.peer), "s": sha, "ref": km._P2P_REF})
        os.chmod(hook, 0o755)
        open(marker, "w").close()

    def _peer(self, *args):
        return git(self.peer, *args, env=self.env, check=False).stdout.strip()

    def _scratch(self):
        return self._peer("rev-parse", "--verify", "--quiet", "refs/heads/" + km._P2P_REF)

    def test_the_same_commit_landing_first_is_adopted_and_the_peer_is_updated(self):
        self._other_push_lands_first(self.B)
        ok, detail = km._update_remote("TESTHOST")
        # the fixture peer has no launcher, so the apply stops at NOLAUNCH right after the reset: the push
        # was taken as done and the apply ran, which is what the old code never reached
        self.assertNotIn("git push to", detail, "the push must not read as failed: the ref holds our commit")
        self.assertIn("launcher", detail)
        self.assertFalse(ok)
        self.assertEqual(self._peer("rev-parse", "HEAD"), self.B, "the peer was reset to this machine's head")
        self.assertEqual(self._scratch(), "", "the apply cleaned up the scratch ref")

    def test_a_different_commit_landing_first_refuses_with_gits_own_reason(self):
        self._other_push_lands_first(self.C)
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("git push to TESTHOST failed", detail)
        self.assertIn("cannot lock ref", detail, "the reason is git's rejection, not the hook's progress lines")
        self.assertNotIn("INF", detail)
        self.assertNotIn("\x1b", detail)
        self.assertEqual(self._peer("rev-parse", "HEAD"), self.A, "nothing was reset")
        self.assertEqual(self._scratch(), self.C, "the other push's ref is left alone")


class PushFailureReason(unittest.TestCase):
    """_git_push_reason: the verdict of a failed push, out of stderr that leads with the hook's output."""

    def test_an_ssh_failure_behind_the_scanner_chatter_is_what_is_said(self):
        err = SCANNER_CHATTER + ("ssh: connect to host TESTHOST port 22: Connection refused\n"
                                 "fatal: Could not read from remote repository.\n\n"
                                 "Please make sure you have the correct access rights\nand the repository exists.\n")
        why = km._git_push_reason(err)
        self.assertTrue(why.startswith("ssh: connect to host TESTHOST port 22: Connection refused"), why)
        self.assertNotIn("INF", why)

    def test_the_hooks_own_refusal_is_kept_and_the_generic_line_dropped(self):
        err = ("romp pre-push: BLOCKED. gitleaks found a credential in a pushed commit.\n"
               "  To bypass for one push (you are sure it is fine): git push --no-verify\n"
               "error: failed to push some refs to 'TESTHOST:/srv/romp'\n")
        why = km._git_push_reason(err)
        self.assertIn("BLOCKED", why)
        self.assertNotIn("failed to push some refs", why)

    def test_output_with_no_recognisable_verdict_keeps_its_last_lines(self):
        self.assertEqual(km._git_push_reason("one\ntwo\nthree\nfour\n"), "two; three; four")


if __name__ == "__main__":
    unittest.main()
