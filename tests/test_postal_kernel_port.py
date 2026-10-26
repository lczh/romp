#!/usr/bin/env python3
"""The postal bus dials the kernel on the port bin/romp dials: ROMP_KERNEL_PORT, then ROMP_SERVE_PORT, else 29855.

The two names are one port (bin/romp-serve's note), and the manager starts the kernel on ROMP_SERVE_PORT alone. The
bus used to read ROMP_KERNEL_PORT only, so a bus started from a shell that moved the kernel by ROMP_SERVE_PORT (as
`romp refresh` restarts it, from the caller's shell) dialed the default port: every check of whether a recipient is
live failed, and every push to the kernel missed.

KERNEL_BASE is read once, at import, so each case loads the bus in a child Python given the environment under test,
with a state root of its own. SYNTHETIC ports; nothing listens and nothing connects.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
BUS = os.path.join(os.path.dirname(HERE), "postal", "postal_service.py")

_PRINT_KERNEL_BASE = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('romp_postal_port_probe', sys.argv[1])\n"
    "bus = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(bus)\n"
    "print(bus.KERNEL_BASE)\n")


class BusKernelPort(unittest.TestCase):
    def _kernel_base(self, **ports):
        """The bus's KERNEL_BASE when it starts with only the given port names set."""
        state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, state, True)
        env = dict(os.environ)
        for name in ("ROMP_KERNEL_PORT", "ROMP_SERVE_PORT", "ROMP_STATE_DIR"):
            env.pop(name, None)
        env.update(ports)
        env["XDG_STATE_HOME"] = state
        run = subprocess.run([sys.executable, "-c", _PRINT_KERNEL_BASE, BUS], env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout.strip().splitlines()[-1]

    def test_a_kernel_moved_by_serve_port_alone_is_the_one_the_bus_dials(self):
        self.assertEqual(self._kernel_base(ROMP_SERVE_PORT="30123"), "http://127.0.0.1:30123")

    def test_kernel_port_alone(self):
        self.assertEqual(self._kernel_base(ROMP_KERNEL_PORT="30123"), "http://127.0.0.1:30123")

    def test_kernel_port_comes_first_when_both_are_set(self):
        # bin/romp's order (_romp_kport), so the CLI and a bus it restarts never dial two different kernels
        self.assertEqual(self._kernel_base(ROMP_KERNEL_PORT="30124", ROMP_SERVE_PORT="30123"),
                         "http://127.0.0.1:30124")

    def test_an_empty_name_counts_as_unset(self):
        # as bin/romp's ${...:-} reads it; this used to give a base with no port at all
        self.assertEqual(self._kernel_base(ROMP_KERNEL_PORT="", ROMP_SERVE_PORT="30123"), "http://127.0.0.1:30123")

    def test_neither_name_is_the_default_port(self):
        self.assertEqual(self._kernel_base(), "http://127.0.0.1:29855")


if __name__ == "__main__":
    unittest.main()
