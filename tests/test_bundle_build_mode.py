#!/usr/bin/env python3
"""An INSTALL must ship a production bundle, and the two things that can build one must agree.

Without --production the dashboard shipped a development build: render.js — the chat pane's
code — was 578 KB of unminified JS the browser parsed before anything appeared (a slow chat load
on a fresh install, the user 2026-07-27). Minified it is 297 KB, with no sourcemaps emitted.

The drift this guards is subtle and silent: vscode-extension/install.sh builds dist at install
time, and the kernel's _ensure_bundles() REBUILDS it whenever a .ts/.css looks newer. If only one
passed --production, any later source touch would swap the served dashboard back to the slow
bundle on the next kernel restart, with nothing saying so. Source-level assertions, because the
real build needs npm install and a network; the executed class at the end runs both kernel builders
over a recording stand-in for subprocess, so it needs no build either.
"""
import glob
import os
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
# Hermetic state BEFORE the loads: the executed class below loads bin/romp-kernel, and romp code
# resolves its state root at import time; only pytest runs conftest's floor (a bare unittest or
# script run otherwise writes REAL state). tests/test_state_isolation_order.py holds the order.
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
KERNEL = os.path.join(ROOT, "kernel", "kernel.py")
EXT_INSTALL = os.path.join(ROOT, "vscode-extension", "install.sh")
ESBUILD = os.path.join(ROOT, "vscode-extension", "esbuild.js")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class BundleBuildMode(unittest.TestCase):
    def test_esbuild_ties_minify_and_sourcemaps_to_the_production_flag(self):
        """The flag has to actually mean something — this is what the other two rely on. The webview build
        minifies whitespace and syntax only: identifiers are kept in every mode, because the browser's
        long-frame attribution (ui/webview/perf-telemetry.ts) names a callback by its compile-time name, and
        a mangled build reports a different letter after every rebuild. The extension host keeps `minify`."""
        src = _read(ESBUILD)
        self.assertIn('const production = process.argv.includes("--production")', src)
        self.assertIn("minify: production", src)              # the extension-host bundle
        self.assertIn("sourcemap: !production", src)
        self.assertIn("minifyWhitespace: production", src)    # the webview bundles: whitespace and syntax…
        self.assertIn("minifySyntax: production", src)
        self.assertIn("minifyIdentifiers: false", src)        # …but never identifiers

    def test_the_installer_builds_production(self):
        src = _read(EXT_INSTALL)
        self.assertIn("--production", src,
                      "vscode-extension/install.sh must build a minified bundle for an install")

    def test_the_kernel_rebuild_also_builds_production(self):
        """The rebuild path is the one that silently undoes the installer's work."""
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        self.assertIsNotNone(m, "_ensure_bundles not found")
        body = m.group(0)
        self.assertIn("esbuild.js", body)
        self.assertIn("--production", body,
                      "_ensure_bundles must match the installer, or a .ts/.css touch reverts "
                      "the served dashboard to the unminified bundle")

    def test_both_honour_the_same_dev_opt_out(self):
        """One knob for a UI dev loop, spelled the same in both places — two names would mean
        turning it off in one builder and silently not the other."""
        self.assertIn("ROMP_EXT_DEV_BUILD", _read(EXT_INSTALL))
        self.assertIn("ROMP_EXT_DEV_BUILD", _read(KERNEL))

    def test_a_failed_build_retries_once_after_npm_install(self):
        """The common build failure is DEP DRIFT: a merged commit imports a package this machine's
        node_modules predates, so every restart's rebuild fails and the kernel silently serves the
        old bundle (2026-08-10: the katex import kept an eight-day-old render.js live — no fast
        toggle, no attach fixes — with only a stderr line saying so). npm install is exactly the
        cure for that class, so a failed build must refresh deps and retry once — and still be
        loud when that isn't the cure."""
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        body = m.group(0)
        self.assertIn('"npm", "install"', body,
                      "a failed build must refresh UI deps — dep drift is the common cause")
        self.assertEqual(body.count("subprocess.run(argv"), 2,
                         "…and retry the same build command once after the refresh")
        self.assertIn("UI may be stale", body,
                      "a retry that still fails must stay loud")

    def test_the_staleness_scan_covers_every_source_root_esbuild_reads(self):
        """esbuild.js builds the webview entrypoints from ../ui/webview (render.ts, styles.css,
        feed.ts, …), but _ensure_bundles used to scan only vscode-extension/src — so a
        ui/webview-only edit never marked the bundle stale, and the fix sat unshipped through
        every kernel restart with the ?v= cache token frozen (found 2026-08-09 hunting the
        optimistic-echo bug: the docstring promised a rebuild the check couldn't see)."""
        self.assertIn("ui/webview", _read(ESBUILD).replace("\\", "/"),
                      "esbuild reads the shared webview sources — the premise of this guard")
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        body = m.group(0)
        self.assertIn('ROOT / "ui" / "webview"', body,
                      "the staleness scan must watch ui/webview, where the webview sources live")
        self.assertIn("for src in srcs", body, "…as one scan over every source root")

    def test_webview_js_modules_required_by_the_bundles_are_watched(self):
        """esbuild follows require("./x.js") into plain-JS webview modules (gear.js), so they are
        bundle sources exactly like the .ts files — but the staleness scan globbed only *.ts/*.css,
        so a gear.js-only edit never marked dist stale and sat unshipped through every kernel
        restart. Derive the requirement from the sources rather than pinning a filename list, so
        the next required .js module is covered the day it is added, whatever it is named."""
        webview = os.path.join(ROOT, "ui", "webview")
        mods = set()
        for ts in glob.glob(os.path.join(webview, "*.ts")):
            mods.update(re.findall(r'require\("\./([\w-]+\.js)"\)', _read(ts)))
        self.assertIn("gear.js", mods,
                      "the derivation lost its known case — did the require() shape change?")
        for mod in sorted(mods):
            self.assertTrue(os.path.exists(os.path.join(webview, mod)),
                            "%s is require()d by a webview source but does not exist" % mod)
        src = _read(KERNEL)
        m = re.search(r"def _ensure_bundles\(\):.*?(?=\ndef )", src, re.S)
        body = m.group(0)
        self.assertIn('rglob("*.js")', body,
                      "the staleness scan must watch the plain-JS modules the bundles require()")


class FailureLineTail(unittest.TestCase):
    """A guard on a pair of constants neither side reads from the other. The kernel's _rebuild_dist keeps
    the last N characters of the script's stderr for the notice that says the served UI is stale, and
    esbuild.js's failureSummary cuts its last line to N from the END (N minus three, then an ellipsis) so
    that tail is the line's head: what failed and what is unchanged. Should the kernel's tail shrink alone, it
    shows the end of the line's last clause and drops its head; should the script's cut shrink alone, the line
    loses its location for nothing. Neither drift fails any other test."""

    def test_the_scripts_cut_is_the_kernels_tail(self):
        kernel = re.search(r"def _rebuild_dist\(\):.*?(?=\n\S)", _read(KERNEL), re.S)   # to the next top-level line
        self.assertIsNotNone(kernel, "_rebuild_dist not found")
        tail = re.search(r"\.strip\(\)\[-(\d+):\]", kernel.group(0))
        self.assertIsNotNone(tail, "_rebuild_dist keeps a tail of the script's stderr")
        n = int(tail.group(1))
        fit = re.search(r"const fit = \(s\) => s\.length > (\d+)\s*\?\s*s\.slice\(0,\s*(\d+)\)", _read(ESBUILD))
        self.assertIsNotNone(fit, "failureSummary's fit cuts the line")
        self.assertEqual(int(fit.group(1)), n, "the script cuts its line where the kernel cuts its stderr")
        self.assertEqual(int(fit.group(2)), n - len("..."), "the cut keeps room for the ellipsis")
        # the node tests stand in for the kernel's tail with a constant of their own; it follows the kernel too
        for name in ("esbuild-build.test.ts", "esbuild-failure-cap.test.ts"):
            ts = _read(os.path.join(ROOT, "vscode-extension", "src", name))
            k = re.search(r"^const KERNEL_TAIL = (\d+);$", ts, re.M)
            self.assertIsNotNone(k, name + " names the kernel's tail")
            self.assertEqual(int(k.group(1)), n, name)


class TheBuildersRunTheProductionProfile(unittest.TestCase):
    """EXECUTED, not read. A text pin on a builder's body is satisfied by a comment that names the flag and the
    knob, so the two kernel builders are run here against a recording stand-in for subprocess and the argv each
    hands it is asserted whole. Three knob states: unset and the empty string build --production (install.sh's
    `-n` test reads the empty string as unset too); "1" builds the dev profile."""

    CASES = ((None, ["node", "esbuild.js", "--production"]),
             ("", ["node", "esbuild.js", "--production"]),
             ("1", ["node", "esbuild.js"]))

    @classmethod
    def setUpClass(cls):
        cls.km = load_source("romp_kernel_build_mode", os.path.join(ROOT, "bin", "romp-kernel"))

    def _run_with(self, knob, fn):
        """Call `fn` with ROMP_EXT_DEV_BUILD in state `knob` (None = unset) and the kernel's subprocess swapped
        for a recorder whose every run succeeds; return the argv of each run() it made, in order. The swap is
        the kernel module's attribute, never subprocess.run itself: the real module is shared by everything
        in this process."""
        km = self.km
        calls = []

        def fake_run(argv, *a, **kw):
            calls.append(list(argv))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        real = km.subprocess
        stand_in = types.SimpleNamespace(run=fake_run, TimeoutExpired=real.TimeoutExpired,
                                         CalledProcessError=real.CalledProcessError)
        lock_dir = tempfile.mkdtemp(prefix="romp-build-lock-")       # the builders' lock, off the checkout under test
        self.addCleanup(lambda: __import__("shutil").rmtree(lock_dir, ignore_errors=True))
        with mock.patch.dict(os.environ), mock.patch.object(km, "subprocess", stand_in), \
                mock.patch.object(km, "_build_lock_path", lambda: Path(lock_dir) / ".romp-build.lock"):
            os.environ.pop("ROMP_EXT_DEV_BUILD", None)
            if knob is not None:
                os.environ["ROMP_EXT_DEV_BUILD"] = knob
            fn()
        return calls

    def test_the_converge_rebuild_passes_production_unless_the_dev_knob_is_set(self):
        """_rebuild_dist is the converge's builder: the in-place converge after a fast-forward, the dist check
        at boot and on every drift pass, and the build before a restart. A bare `node esbuild.js` there served
        unminified bundles with sourcemaps after every fast-forward, and _ensure_bundles never re-minified
        them, since it judges staleness by mtime against dist/render.js and not by profile."""
        for knob, want in self.CASES:
            with self.subTest(knob=knob):
                calls = self._run_with(knob, lambda: self.assertTrue(
                    self.km._rebuild_dist()[0], "every recorded run succeeds, so the rebuild reports ok"))
                self.assertEqual(calls, [want])

    def test_the_boot_build_passes_production_unless_the_dev_knob_is_set(self):
        """_ensure_bundles builds only when dist/render.js is missing or older than an input, so the kernel is
        pointed at a synthetic checkout with a node_modules dir, no dist and no inputs: stale, one build,
        recorded. The recorder never raises, so the npm-install retry is never entered."""
        km = self.km
        with tempfile.TemporaryDirectory(prefix="romp-build-mode-") as d:
            tmp = Path(d)
            (tmp / "vscode-extension" / "node_modules").mkdir(parents=True)
            with mock.patch.object(km, "ROOT", tmp), mock.patch.object(km, "DIST", tmp / "dist"):
                for knob, want in self.CASES:
                    with self.subTest(knob=knob):
                        calls = self._run_with(knob, km._ensure_bundles)
                        self.assertEqual(calls, [want])


class TheConvergeRebuildRetriesAfterNpmInstall(unittest.TestCase):
    """EXECUTED. _rebuild_dist is the build a kernel serves a new commit with WITHOUT restarting (the in-place
    converge, and since 2026-11-12 the answer to a peer's push that changed no kernel code). A build that failed
    there sent the converge down the restart path, where _ensure_bundles cured the common cause, a merged commit
    importing a package this machine's node_modules predates, with its `npm install` retry: a restart for a
    page-only change. The converge's builder now carries the same one retry. A stand-in subprocess answers each
    run() in turn; the argv of every run is asserted whole."""

    BUILD = ["node", "esbuild.js", "--production"]
    NPM = ["npm", "install", "--no-audit", "--no-fund"]
    MISSING = 'Could not resolve "a-package-added-by-the-merge"'

    @classmethod
    def setUpClass(cls):
        cls.km = load_source("romp_kernel_build_mode", os.path.join(ROOT, "bin", "romp-kernel"))

    def _rebuild(self, *answers):
        """_rebuild_dist() against runs that answer, in order, with each (returncode, stderr) given:
        ((ok, err), [(argv, cwd) of each run])."""
        km, calls, script = self.km, [], list(answers)

        def fake_run(argv, *a, **kw):
            calls.append((list(argv), kw.get("cwd")))
            rc, err = script.pop(0)
            return types.SimpleNamespace(returncode=rc, stdout="", stderr=err)

        real = km.subprocess
        stand_in = types.SimpleNamespace(run=fake_run, TimeoutExpired=real.TimeoutExpired,
                                         CalledProcessError=real.CalledProcessError)
        lock_dir = tempfile.mkdtemp(prefix="romp-build-lock-")
        self.addCleanup(lambda: __import__("shutil").rmtree(lock_dir, ignore_errors=True))
        with mock.patch.dict(os.environ), mock.patch.object(km, "subprocess", stand_in), \
                mock.patch.object(km, "_build_lock_path", lambda: Path(lock_dir) / ".romp-build.lock"):
            os.environ.pop("ROMP_EXT_DEV_BUILD", None)
            out = km._rebuild_dist()
        return out, calls

    def test_a_failed_build_is_retried_once_after_npm_install_and_can_succeed(self):
        out, calls = self._rebuild((1, self.MISSING), (0, ""), (0, ""))
        self.assertEqual(out, (True, ""), "the retry built: the converge serves the commit, no restart")
        self.assertEqual([c[0] for c in calls], [self.BUILD, self.NPM, self.BUILD],
                         "the same build command again, after the dependency refresh")
        self.assertEqual({c[1] for c in calls}, {str(self.km.ROOT / "vscode-extension")}, "all three in the extension's directory")

    def test_an_npm_install_that_fails_reports_the_builds_own_error_and_builds_no_more(self):
        out, calls = self._rebuild((1, self.MISSING), (1, "npm ERR! network"))
        self.assertEqual(out, (False, self.MISSING), "the build's words are the news, not npm's")
        self.assertEqual([c[0] for c in calls], [self.BUILD, self.NPM])

    def test_a_retry_that_fails_too_is_the_last_one(self):
        out, calls = self._rebuild((1, self.MISSING), (0, ""), (1, "error: Unexpected token"))
        self.assertEqual(out, (False, "error: Unexpected token"), "what the build says after the refresh")
        self.assertEqual([c[0] for c in calls], [self.BUILD, self.NPM, self.BUILD], "one retry, never a loop")

    def test_a_build_that_succeeds_runs_nothing_else(self):
        out, calls = self._rebuild((0, ""))
        self.assertEqual((out, [c[0] for c in calls]), ((True, ""), [self.BUILD]))


class OneBuildOfACheckoutAtATime(unittest.TestCase):
    """EXECUTED, across real processes. Every bundle builder of a checkout holds one fcntl lock (_bundle_build_lock) around
    its build and its `npm install` retry, and hands the lock's descriptor to the children it runs. Before it, a kernel
    stopped mid-build (the restart a push falls back to when the in-place build outlasts the ask, which is exactly when the
    npm retry runs) left that `npm install` going, and the next kernel's boot build and dist check started their own in
    the same node_modules (the review of the push converge, 2026-11-12). The other holder here is a real process this test
    starts and stops by pid; the builds themselves are a recording stand-in for subprocess, so no node runs."""

    BUILD = ["node", "esbuild.js", "--production"]
    NPM = ["npm", "install", "--no-audit", "--no-fund"]
    HOLD = ("import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "sys.stdin.read()\n")

    @classmethod
    def setUpClass(cls):
        cls.km = load_source("romp_kernel_build_mode", os.path.join(ROOT, "bin", "romp-kernel"))
        cls.lock_path = staticmethod(cls.km._build_lock_path)       # the real one; setUp points the builders elsewhere

    def setUp(self):
        import shutil
        self.dir = Path(tempfile.mkdtemp(prefix="romp-build-lock-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lock = self.dir / "dist" / ".romp-build.lock"
        self.calls, self.kws = [], []
        self.real_sp = self.km.subprocess
        self.answer = lambda argv, kw: types.SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_run(argv, *a, **kw):
            self.calls.append(list(argv))
            self.kws.append(kw)
            r = self.answer(argv, kw)
            if kw.get("check") and r.returncode:
                raise self.real_sp.CalledProcessError(r.returncode, argv)
            return r

        stand_in = types.SimpleNamespace(run=fake_run, TimeoutExpired=self.real_sp.TimeoutExpired,
                                         CalledProcessError=self.real_sp.CalledProcessError)
        for patch in (mock.patch.dict(os.environ), mock.patch.object(self.km, "subprocess", stand_in),
                      mock.patch.object(self.km, "_build_lock_path", lambda: self.lock),
                      mock.patch.object(self.km, "_BUILD_LOCK_WAIT_S", 30)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("ROMP_EXT_DEV_BUILD", None)

    def _hold(self, argv=None, pass_fds=()):
        """A process of this test's own holding the lock until its stdin closes; stopped (by pid) at cleanup."""
        import subprocess as sp
        import sys
        if argv is None:
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            argv = [sys.executable, "-c", self.HOLD, str(self.lock)]
        p = sp.Popen(argv, stdin=sp.PIPE, stdout=sp.PIPE, text=True, pass_fds=pass_fds)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(10)))
        if argv[-1] == str(self.lock):
            self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def _release(self, p):
        p.stdin.close()
        p.wait(10)

    def _free(self):
        """Whether another process could take the lock now (a fresh open, one try)."""
        import fcntl
        fd = os.open(str(self.lock), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    def test_a_build_waits_for_another_process_building_the_checkout_and_runs_after_it(self):
        import threading
        import time
        other = self._hold()
        out = []
        t = threading.Thread(target=lambda: out.append(self.km._rebuild_dist()))
        t.start()
        time.sleep(1.0)
        self.assertEqual(self.calls, [], "nothing runs while the other build holds the checkout")
        self._release(other)
        t.join(10)
        self.assertEqual((out, self.calls), ([(True, "")], [self.BUILD]), "and the build runs once it ends")

    def test_a_build_that_waits_past_its_bound_says_so_and_runs_nothing(self):
        self._hold()
        with mock.patch.object(self.km, "_BUILD_LOCK_WAIT_S", 0.5):
            ok, why = self.km._rebuild_dist()
        self.assertFalse(ok)
        self.assertEqual(why, "another build of this checkout held its build lock past the 0.5 s a builder waits")
        self.assertEqual(self.calls, [], "no esbuild and no npm beside the other build")

    def test_the_npm_install_a_build_starts_keeps_the_checkout_after_the_kernel_lets_go(self):
        # the retry's npm is the child that outlives a stopped kernel: here it is a real process started with the fds the
        # builder passed, still running after _rebuild_dist returned and closed the kernel's own descriptor, as it would
        # be after the kernel died
        import sys
        lingering = []

        def answer(argv, kw):
            if argv == self.NPM and not lingering:
                lingering.append(self._hold([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                            pass_fds=kw.get("pass_fds", ())))
            rc = 1 if (argv == self.BUILD and len(self.calls) == 1) else 0
            return types.SimpleNamespace(returncode=rc, stdout="", stderr="a package the merge added")
        self.answer = answer
        self.assertEqual(self.km._rebuild_dist(), (True, "a package the merge added"))
        self.assertEqual(self.calls, [self.BUILD, self.NPM, self.BUILD])
        self.assertFalse(self._free(), "the npm the build started still holds the checkout's lock")
        self.calls.clear()
        with mock.patch.object(self.km, "_BUILD_LOCK_WAIT_S", 0.5):
            self.assertFalse(self.km._rebuild_dist()[0], "so the next kernel's build does not run beside it")
        self.assertEqual(self.calls, [])
        self._release(lingering[0])
        self.assertTrue(self._free(), "it lets go when it ends")
        self.assertEqual(self.km._rebuild_dist(), (True, "a package the merge added"))

    def test_the_boot_build_does_not_hold_the_boot_for_another_build_and_builds_after_it(self):
        import threading
        import time
        km = self.km
        (self.dir / "vscode-extension" / "node_modules").mkdir(parents=True)
        other = self._hold()
        with mock.patch.object(km, "ROOT", self.dir), mock.patch.object(km, "DIST", self.dir / "no-dist"):
            t0 = time.monotonic()
            km._ensure_bundles()
            self.assertLess(time.monotonic() - t0, 5.0, "the boot goes on")
            self.assertEqual(self.calls, [], "and builds nothing beside the other build")
            waiter = [th for th in threading.enumerate() if th.name == "romp-boot-bundles"]
            self.assertEqual(len(waiter), 1, "the check waits on a thread of its own")
            self._release(other)
            waiter[0].join(10)
        self.assertEqual(self.calls, [self.BUILD], "the bundles are built once the other build ends")

    def test_the_boot_build_takes_the_same_lock(self):
        km = self.km
        (self.dir / "vscode-extension" / "node_modules").mkdir(parents=True)
        seen = []
        self.answer = lambda argv, kw: (seen.append(self._free()),
                                        types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
        with mock.patch.object(km, "ROOT", self.dir), mock.patch.object(km, "DIST", self.dir / "no-dist"):
            km._ensure_bundles()
        self.assertEqual((self.calls, seen), ([self.BUILD], [False]), "held while its esbuild runs")
        self.assertTrue(self._free(), "and let go after")

    def test_a_host_where_the_lock_cannot_work_still_builds(self):
        # the re-review, 2026-11-12: any flock error but "held" read as "not taken", so on a filesystem without locks
        # (ENOLCK, as some NFS mounts are) the boot build, the dist check, the converge and the pre-restart build all stood
        # down for good and the UI never built again. Such a host builds unlocked, as before the lock, and says so once
        import errno
        import fcntl
        import io
        import threading
        km = self.km
        (self.dir / "vscode-extension" / "node_modules").mkdir(parents=True)
        (self.dir / "a-file").write_text("")

        def failing(code):
            def flock(fd, op):
                raise OSError(code, os.strerror(code))
            return types.SimpleNamespace(flock=flock, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB, LOCK_UN=fcntl.LOCK_UN)
        cases = (("ENOLCK", dict(fcntl=failing(errno.ENOLCK)), "No locks available"),
                 ("EBADF", dict(fcntl=failing(errno.EBADF)), "Bad file descriptor"),
                 ("the lock file cannot be opened", dict(_build_lock_path=lambda: self.dir / "a-file" / "x.lock"), "a-file"))
        for name, over, words in cases:
            for builder in ("_rebuild_dist", "_ensure_bundles"):
                with self.subTest(name, builder=builder):
                    self.calls.clear(); self.kws.clear()
                    err = io.StringIO()
                    with mock.patch.multiple(km, **over), mock.patch.object(km, "ROOT", self.dir), \
                            mock.patch.object(km, "DIST", self.dir / "no-dist"), mock.patch("sys.stderr", err):
                        out = getattr(km, builder)()
                    if builder == "_rebuild_dist":
                        self.assertEqual(out, (True, ""), "the converge serves the build")
                    self.assertEqual(self.calls, [self.BUILD], "it builds")
                    self.assertEqual(self.kws[0].get("pass_fds"), (), "with no lock to hand its children")
                    said = [ln for ln in err.getvalue().splitlines() if "without the checkout's build lock" in ln]
                    self.assertEqual(len(said), 1, err.getvalue())
                    self.assertIn(words, said[0])
                    self.assertFalse([t for t in threading.enumerate() if t.name == "romp-boot-bundles"],
                                     "the boot builds there and then, with no waiter")

    def test_a_thread_waiting_out_another_process_holds_nothing_the_others_need(self):
        # the re-review, 2026-11-12: the process's own build queue (_REBUILD_LOCK) was held through the whole wait for
        # another process's build, so every other thread that wanted a build stacked behind it with no bound of its own
        import threading
        import time
        km = self.km
        other = self._hold()
        out = []
        t = threading.Thread(target=lambda: out.append(km._rebuild_dist()))
        t.start()
        time.sleep(0.5)
        self.assertTrue(t.is_alive(), "it is waiting for the other process")
        free = km._REBUILD_LOCK.acquire(blocking=False)
        if free:
            km._REBUILD_LOCK.release()
        self.assertTrue(free, "and holds the in-process queue meanwhile only once it has the checkout")
        self._release(other)
        t.join(10)
        self.assertEqual((out, self.calls), ([(True, "")], [self.BUILD]))

    def test_the_lock_lives_where_git_ignores_it_at_every_commit(self):
        # anywhere else in the tree it would read as an uncommitted edit and turn the kernel's sha '-dirty'
        km = self.km
        self.assertEqual(self.lock_path(), km.ROOT / "vscode-extension" / "dist" / ".romp-build.lock")
        self.assertIn("dist/", _read(os.path.join(ROOT, "vscode-extension", ".gitignore")).split())
        self.assertIn("dist/.romp-build.lock", _read(os.path.join(ROOT, "vscode-extension", ".vscodeignore")).split(),
                      "and stays out of the packaged extension")


if __name__ == "__main__":
    unittest.main()
