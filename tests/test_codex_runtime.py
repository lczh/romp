"""The managed runtime is independent of PATH and the SDK's older dependency."""
import contextlib
import json
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)

HERE = Path(__file__).resolve().parents[1]
rt = load_source('test_romp_codex_runtime', str(HERE / 'kernel/codex_runtime.py'))


# The executables of a 0.169.0 wheel (all four platforms ship the first four; the Linux wheels add bwrap).
LAYOUT = ('bin/codex', 'bin/codex-code-mode-host', 'bin/codex-exec-server', 'codex-path/rg', 'codex-resources/bwrap')


def package(target, version=None, files=LAYOUT):
    root = target / 'codex_cli_bin'
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic executable')
        path.chmod(0o755)
    (root / 'codex-package.json').write_text(json.dumps({'version': version or rt.VERSION}))
    return root / 'bin/codex'


@contextlib.contextmanager
def _state_dir(prefix=''):
    """A temp state directory handed to the product as its REAL path: install_runtime and _codex_config resolve the paths they
    are given (Path.resolve), and a test that compares against the unresolved mint fails wherever the temp root is reached
    through a symlink (macOS: /var/folders resolves under /private/var; 2026-09-16)."""
    with tempfile.TemporaryDirectory(prefix=prefix) as d:
        yield os.path.realpath(d)


class ManagedRuntime(unittest.TestCase):
    def test_all_supported_platforms_have_pinned_official_wheels(self):
        for system, machine in [('Linux', 'x86_64'), ('Linux', 'aarch64'),
                                ('Darwin', 'x86_64'), ('Darwin', 'arm64')]:
            with self.subTest(system=system, machine=machine):
                url = rt.wheel_url(system, machine)
                self.assertTrue(url.startswith('https://github.com/openai/codex/releases/download/rust-v0.169.0/'))
                self.assertRegex(url, r'\.whl#sha256=[0-9a-f]{64}$')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
            rt.wheel_url('Linux', 'riscv64')

    def test_missing_runtime_fails_with_setup_hint(self):
        with _state_dir() as state:
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)

    def test_incomplete_or_wrong_version_is_not_usable(self):
        with _state_dir() as state:
            target = Path(state) / 'codex-runtime' / rt.VERSION
            exe = package(target, '0.144.4')
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)
            package(target)
            self.assertEqual(rt.runtime_path(state), exe)
            (target / 'codex_cli_bin/codex-path/rg').unlink()
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)

    def test_the_current_layout_is_required_without_the_retired_zsh(self):
        # The wheels after 0.153.3 dropped codex-resources/zsh and added bin/codex-exec-server (0.166.0 and 0.169.0,
        # 2026-11-17): a package without zsh is complete, and one without the exec server is not.
        with _state_dir() as state:
            target = Path(state) / 'codex-runtime' / rt.VERSION
            exe = package(target)
            self.assertFalse((target / 'codex_cli_bin/codex-resources/zsh').exists())
            self.assertEqual(rt.runtime_path(state), exe)
            (target / 'codex_cli_bin/bin/codex-exec-server').unlink()
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)

    def test_install_stages_verified_wheel_and_is_idempotent(self):
        with _state_dir(prefix='romp runtime ') as state:
            def run(args, **kwargs):
                if '--target' in args:
                    target = Path(args[args.index('--target') + 1])
                    self.assertNotEqual(target, Path(state) / 'codex-runtime' / rt.VERSION)
                    package(target)
                    self.assertIn('--require-hashes', args)
                    self.assertIn('--no-deps', args)
                    self.assertIn('--no-index', args)
                    self.assertIn('--only-binary=:all:', args)
                    self.assertIn('#sha256=', args[-1])
                else:
                    # the first run of the staged binary gets the macOS first-exec scan's bound, not a tight one
                    self.assertEqual(kwargs.get('timeout'), rt.FIRST_RUN_TIMEOUT)
                    self.assertGreaterEqual(rt.FIRST_RUN_TIMEOUT, 120)
                return subprocess.CompletedProcess(args, 0, 'codex-cli ' + rt.VERSION + '\n')
            with mock.patch.object(rt.subprocess, 'run', side_effect=run) as install:
                exe = rt.install_runtime(state)
                self.assertEqual(exe, rt.runtime_path(state))
                calls = install.call_count
                self.assertEqual(rt.install_runtime(state), exe)
                self.assertEqual(install.call_count, calls)

    def test_an_apple_silicon_mac_gets_the_arm64_runtime_under_an_x86_64_python(self):
        # An x86_64 Python under Rosetta says machine() x86_64 on an arm64 Mac; setup installed the x86_64 runtime, which
        # then ran translated too (2026-11-25). The hardware decides; an Intel Mac and Linux are unchanged.
        cases = [(('Darwin', 'x86_64'), True, 'macosx_11_0_arm64'), (('Darwin', 'x86_64'), False, 'macosx_10_9_x86_64'),
                 (('Darwin', 'arm64'), True, 'macosx_11_0_arm64'), (('Linux', 'x86_64'), False, 'manylinux_2_17_x86_64')]
        for (system, machine), arm64, tag in cases:
            with self.subTest(system=system, machine=machine, arm64=arm64):
                with mock.patch.object(rt.platform, 'system', return_value=system), \
                        mock.patch.object(rt.platform, 'machine', return_value=machine), \
                        mock.patch.object(rt, '_darwin_arm64', return_value=arm64):
                    self.assertIn('-%s.whl' % tag, rt.wheel_url())

    def _install_on_apple_silicon(self, state, existing_header=None):
        """install_runtime on an arm64 Mac (host_platform pinned), over an existing complete runtime whose bin/codex starts
        with `existing_header` when given; returns the pip calls it made."""
        target = Path(state) / 'codex-runtime' / rt.VERSION
        if existing_header is not None:
            package(target).write_bytes(existing_header + b'\0' * 24)
        pips = []

        def run(args, **kwargs):
            if '--target' in args:
                pips.append(list(args))
                package(Path(args[args.index('--target') + 1]))
            return subprocess.CompletedProcess(args, 0, 'codex-cli ' + rt.VERSION + '\n')
        with mock.patch.object(rt, 'host_platform', return_value=('Darwin', 'arm64')), \
                mock.patch.object(rt.subprocess, 'run', side_effect=run):
            rt.install_runtime(state)
        return pips

    def test_pip_is_told_the_wheels_own_platform(self):
        # pip checks a wheel against the running interpreter's tags: without --platform an x86_64 Python refuses the arm64 wheel.
        with _state_dir() as state:
            (args,) = self._install_on_apple_silicon(state)
            self.assertEqual(args[args.index('--platform') + 1], 'macosx_11_0_arm64')
            self.assertIn('-macosx_11_0_arm64.whl', args[-1])

    def test_a_runtime_built_for_the_other_mac_is_replaced_and_a_matching_one_kept(self):
        def macho(cpu):
            return (0xFEEDFACF).to_bytes(4, 'little') + cpu.to_bytes(4, 'little')
        with _state_dir() as state:
            self.assertEqual(len(self._install_on_apple_silicon(state, macho(0x01000007))), 1,
                             "an x86_64 runtime on an arm64 Mac is reinstalled")
        with _state_dir() as state:
            self.assertEqual(self._install_on_apple_silicon(state, macho(0x0100000C)), [], "an arm64 runtime stands")
        with _state_dir() as state:
            self.assertEqual(self._install_on_apple_silicon(state, b'#!/bin/sh'), [],
                             "one whose build cannot be read stands")

    def test_failed_install_never_publishes_partial_runtime(self):
        with _state_dir() as state:
            old = package(Path(state) / 'codex-runtime/0.144.4', '0.144.4')
            with mock.patch.object(rt.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pip')):
                with self.assertRaises(subprocess.CalledProcessError):
                    rt.install_runtime(state)
            self.assertTrue(old.exists())
            self.assertFalse((Path(state) / 'codex-runtime' / rt.VERSION).exists())

    def test_setup_repairs_an_incomplete_existing_target(self):
        for kind in ('file', 'directory'):
            with self.subTest(kind=kind), _state_dir() as state:
                target = Path(state) / 'codex-runtime' / rt.VERSION
                target.parent.mkdir(parents=True)
                if kind == 'file':
                    target.write_text('incomplete installation')
                else:
                    target.mkdir()
                def run(args, **kwargs):
                    if '--target' in args:
                        package(Path(args[args.index('--target') + 1]))
                    return subprocess.CompletedProcess(args, 0, 'codex-cli ' + rt.VERSION + '\n')
                with mock.patch.object(rt.subprocess, 'run', side_effect=run):
                    self.assertEqual(rt.install_runtime(state), rt.runtime_path(state))

    def test_wrong_executable_version_is_not_published(self):
        with _state_dir() as state:
            def run(args, **kwargs):
                if '--target' in args:
                    package(Path(args[args.index('--target') + 1]))
                return subprocess.CompletedProcess(args, 0, 'codex-cli 0.144.4\n')
            with mock.patch.object(rt.subprocess, 'run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'version'):
                    rt.install_runtime(state)
            self.assertFalse((Path(state) / 'codex-runtime' / rt.VERSION).exists())


class BackendRuntimeSelection(unittest.TestCase):
    def test_managed_executable_and_helpers_win_over_path(self):
        cb = load_source('runtime_selection_backend', str(HERE / 'kernel/codex_backend.py'))
        with _state_dir() as state:
            exe = package(Path(state) / 'codex-runtime' / rt.VERSION)
            with mock.patch.dict('os.environ', {'PATH': '/TESTBIN'}):
                config = cb._codex_config(lambda **kwargs: kwargs, None, state)
            self.assertEqual(config['codex_bin'], str(exe))
            self.assertEqual(config['env']['PATH'], str(exe.parent.parent / 'codex-path') + ':/TESTBIN')
            profile = config['config_overrides'][0]
            for path in (exe, exe.parent / 'codex-code-mode-host',
                         exe.parent.parent / 'codex-package.json',
                         exe.parent.parent / 'codex-resources', exe.parent.parent / 'codex-path'):
                self.assertIn(json.dumps(str(path)) + ' = "read"', profile)
            self.assertNotIn(json.dumps(state) + ' = "read"', profile)
            self.assertNotIn(json.dumps(str(exe.parent.parent)) + ' = "read"', profile)
            self.assertNotIn('":root"', profile)

    def test_missing_runtime_does_not_fall_back_to_sdk_or_path(self):
        cb = load_source('runtime_missing_backend', str(HERE / 'kernel/codex_backend.py'))
        with _state_dir() as state:
            config = mock.Mock()
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                cb._codex_config(config, None, state)
            config.assert_not_called()

    def test_explicit_packaged_runtime_keeps_its_matching_helpers(self):
        cb = load_source('runtime_override_backend', str(HERE / 'kernel/codex_backend.py'))
        with _state_dir() as state:
            exe = package(Path(state) / 'custom')
            config = cb._codex_config(lambda **kwargs: kwargs, str(exe))
            self.assertEqual(config['codex_bin'], str(exe))
            self.assertTrue(config['env']['PATH'].startswith(str(exe.parent.parent / 'codex-path')))

    def test_bare_executable_does_not_grant_reads_to_its_parent(self):
        cb = load_source('runtime_bare_backend', str(HERE / 'kernel/codex_backend.py'))
        with _state_dir(prefix='romp runtime ') as state:
            exe = Path(state) / 'codex'
            exe.touch()
            (Path(state) / 'private.json').write_text('synthetic private fixture')
            profile = cb._codex_config(lambda **kwargs: kwargs, str(exe))['config_overrides'][0]
            self.assertIn(json.dumps(str(exe)) + ' = "read"', profile)
            self.assertNotIn(json.dumps(state) + ' = "read"', profile)
            self.assertNotIn('private.json', profile)
