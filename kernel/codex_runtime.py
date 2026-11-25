#!/usr/bin/env python3
"""Install ROMP's pinned CLI separately from the Python SDK's runtime dependency.

The CLI wheel is an official release artifact, fetched from OpenAI's GitHub
release by digest. The same wheels are on PyPI as openai-codex-cli-bin (the four
pinned digests below match its 0.169.0 upload; checked 2026-11-25), but the
pinned SDK requires its own older cli-bin, so the runtime is installed here,
apart from it. Keep its complete package layout (including sandbox and
code-mode helpers) intact.
"""
import fcntl
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = '0.169.0'
# SHA-256 digests published with OpenAI's rust-v0.169.0 GitHub release. A ChatGPT account's model list is filtered by
# the client's version: the GPT-6.2 models appear from 0.166.0 (0.165.0 lists GPT-6.1 at most; probed 2026-11-17).
_WHEELS = {
    ('Linux', 'x86_64'): ('manylinux_2_17_x86_64', '8e0a30a55b05a44f2a8228afca04bbc5e231592be375d2f98fc99eb24bd1ebb5'),
    ('Linux', 'aarch64'): ('manylinux_2_17_aarch64', '23ca658c8bf6b19807ab33cfa27d0d4fec240cf4679e9e7addd5af8870e40222'),
    ('Darwin', 'x86_64'): ('macosx_10_9_x86_64', 'd550485eb2d20d50f3b4a9cff03b8835cf4a9d7ddeec14bb1113169d4429576a'),
    ('Darwin', 'arm64'): ('macosx_11_0_arm64', '19929a5742ba1d043855fd184ec1fbce1a563478a77b1999b126c0e08c799eeb'),
}


# The bound on the staged binary's first `--version`. It is the first exec of a freshly unpacked ~100 MB executable,
# and macOS scans such a binary before it runs (syspolicyd), keyed by its path, so every setup run pays it again in a
# new staging directory: 27 s on an Intel Mac (2026-11-24), where 15 s refused every install. A later run of the same
# file answers in milliseconds, so this bound is generous only on the one call that needs it.
FIRST_RUN_TIMEOUT = 180


def _darwin_arm64():
    """Whether this Mac is Apple silicon, read from the hardware (sysctl hw.optional.arm64, absent on an Intel Mac), not
    from the running Python: an x86_64 Python under Rosetta says platform.machine() 'x86_64' on an arm64 Mac, and setup
    then installed the x86_64 runtime, which ran translated too (2026-11-25). The runtime is a standalone binary, so it
    runs natively whatever Python installed it. A direct sysctl call, not a subprocess."""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.dylib')
        val, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        ok = libc.sysctlbyname(b'hw.optional.arm64', ctypes.byref(val), ctypes.byref(size), None, ctypes.c_size_t(0)) == 0
        return ok and val.value == 1
    except Exception:
        return False


def host_platform():
    """(system, machine) the runtime is chosen for: the hardware's, so an Apple silicon Mac gets the arm64 runtime even
    when the Python running setup is x86_64 under Rosetta."""
    system, machine = platform.system(), platform.machine()
    if system == 'Darwin' and machine == 'x86_64' and _darwin_arm64():
        machine = 'arm64'
    return system, machine


def _wheel(system=None, machine=None):
    if system is None or machine is None:
        host = host_platform()
        system, machine = system or host[0], machine or host[1]
    key = (system, machine)
    try:
        tag, digest = _WHEELS[key]
    except KeyError:
        raise RuntimeError('Unsupported Codex runtime platform: %s %s' % key) from None
    name = 'openai_codex_cli_bin-%s-py3-none-%s.whl' % (VERSION, tag)
    return ('https://github.com/openai/codex/releases/download/rust-v%s/%s#sha256=%s'
            % (VERSION, name, digest)), tag, machine


def wheel_url(system=None, machine=None):
    return _wheel(system, machine)[0]


# The cputype in a thin 64-bit Mach-O header (<mach/machine.h>): which Mac an installed runtime was built for.
_MACHO_MAGIC_64 = 0xFEEDFACF
_MACHO_CPU = {0x01000007: 'x86_64', 0x0100000C: 'arm64'}


def _macho_machine(path):
    """'x86_64' or 'arm64' for a thin 64-bit Mach-O executable, else None (Linux's ELF, a universal binary, a test's stand-in,
    an unreadable file): None never asks for a reinstall."""
    try:
        with open(path, 'rb') as f:
            head = f.read(8)
    except OSError:
        return None
    if len(head) < 8 or int.from_bytes(head[:4], 'little') != _MACHO_MAGIC_64:
        return None
    return _MACHO_CPU.get(int.from_bytes(head[4:8], 'little'))


def _package_path(target):
    root = Path(target) / 'codex_cli_bin'
    try:
        metadata = json.loads((root / 'codex-package.json').read_text())
        if metadata.get('version') != VERSION:
            raise ValueError('wrong runtime version')
        # The executables every 0.169.0 wheel ships. The bundled zsh left the package after 0.153.3 (no wheel
        # carries codex-resources/zsh any more); bin/codex-exec-server arrived, named by codex-package.json.
        files = ['bin/codex', 'bin/codex-code-mode-host', 'bin/codex-exec-server', 'codex-path/rg']
        if platform.system() == 'Linux':
            files += ['codex-resources/bwrap']
        if any(not (root / name).is_file() or not os.access(root / name, os.X_OK)
               for name in files):
            raise ValueError('missing runtime executable or helper')
    except (OSError, ValueError, AttributeError) as e:
        raise RuntimeError('Codex runtime %s is missing or incomplete. '
                           'Run romp-codex-setup, then restart the ROMP kernel.' % VERSION) from e
    return root / 'bin/codex'


def runtime_path(state_dir):
    return _package_path(Path(state_dir) / 'codex-runtime' / VERSION)


def install_runtime(state_dir):
    url, tag, machine = _wheel()  # Refuse unsupported hosts before creating an installation.
    base = Path(state_dir).resolve() / 'codex-runtime'
    base.mkdir(parents=True, exist_ok=True)
    target = base / VERSION
    # Serialize concurrent setup calls and publish only a validated full package.
    with (base / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            exe = _package_path(target)
            # An installation built for the other Mac (the x86_64 runtime an x86_64 Python under Rosetta chose on Apple
            # silicon) is replaced by the hardware's own; one whose build cannot be read stands.
            if _macho_machine(exe) in (None, machine):
                return exe
        except RuntimeError:
            pass
        with tempfile.TemporaryDirectory(prefix='.install-', dir=base) as scratch:
            staging = Path(scratch) / 'runtime'
            # --platform: pip checks a wheel against the RUNNING interpreter's tags, so an x86_64 Python refuses the arm64
            # wheel without it; naming the wheel's own tag changes nothing where the two agree.
            subprocess.run([
                sys.executable, '-m', 'pip', '--isolated', 'install', '--quiet',
                '--disable-pip-version-check', '--no-index', '--no-deps',
                '--only-binary=:all:', '--platform', tag, '--require-hashes', '--target', str(staging), url,
            ], check=True)
            exe = _package_path(staging)
            result = subprocess.run([str(exe), '--version'], check=True,
                                    capture_output=True, text=True, timeout=FIRST_RUN_TIMEOUT)
            if result.stdout.strip() != 'codex-cli ' + VERSION:
                raise RuntimeError('Downloaded Codex runtime has an unexpected version')
            # A corrupt previous installation can be repaired; other versions remain intact.
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
            staging.rename(target)
    return _package_path(target)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: codex_runtime.py STATE_DIR')
    try:
        print(install_runtime(sys.argv[1]))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        sys.exit('romp-codex-setup: %s' % exc)
