"""`romp update` and `romp version` resolve this romp's kernel's port, then talk to that one port and no
other. The port comes from the environment in the order bin/romp-manager reads it when it starts the kernel:
ROMP_SERVE_PORT, then ROMP_KERNEL_PORT, else 29855, each value decoded as UTF-8 and read as JavaScript's
Number() reads it (a value Number() reads as 0, or not as a number, counting as unset). A port no kernel can
listen on is not asked.

Both modules run a table of environments, checked for the port each resolves and the requests each command makes.
The table holds every pair of a list of values for the two names, then values with a run of 1,000 zeros in each
part of a number that may repeat, each in either name alone and in ROMP_SERVE_PORT with another port in
ROMP_KERNEL_PORT. bin/romp-manager's own expression, evaluated by node (skipped without node), is the reference for
that table; for every character of the Basic Multilingual Plane but NUL and the surrogates around a port; for byte
values in a child Python under several locale settings (LC_ALL, PYTHONUTF8); and for a list of numbers of each form
Number() reads, each with every character of a fixed list (INSIDE: every ASCII character but NUL, and every other
character of the plane that is a decimal digit or white space, with U+FEFF) inserted at each position or put in
place of each of its characters, and with each of its characters deleted. Long values are read by both modules in a
child Python held to a CPU-time limit. SYNTHETIC ports and token; urllib's urlopen is replaced by a recorder, so
nothing connects."""
import contextlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads (tests/test_state_isolation_order.py's ratchet): romp modules resolve
# their state root at import time, and only pytest runs conftest's floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ru = load_source("romp_update_kernel_port", os.path.join(BIN, "romp-update"))
ver = load_source("romp_version_kernel_port", os.path.join(BIN, "romp-version"))

TOKEN = "synthetic-serve-token"
OWN = 29900                       # this romp's kernel, moved off the default port
ALT = 40123                       # a second synthetic port
OTHER_PORTS = (29855, 7878, 7432)  # the ports the old probe list tried
MANAGER = os.path.join(BIN, "romp-manager")
NAMES = ("ROMP_SERVE_PORT", "ROMP_KERNEL_PORT")

# (value, the port the URL names when that value is the one read: the number when it is a port a kernel can
# listen on, else the value as set, trimmed; None where the value reads as 0 or as not a number)
VALUES = [
    ("", None), ("   ", None), ("\t\n\v\f\r", None), ("\xa0\u2028\ufeff\u3000", None),
    ("0", None), ("-0", None), ("0.0e5", None), ("0x0", None), ("1e-400", None),
    ("not-a-port", None), ("29_900", None), ("29900abc", None), ("29 900", None), ("1..0", None), ("++29900", None),
    ("2_9900", None), ("29900.0_0", None), ("2.99_0e4", None), ("2.99e0_4", None), (".2_99e5", None), ("1e_5", None),
    ("0_7", None), ("0_1", None), ("0_29900", None), ("0x74_cc", None), ("0xa_", None), ("0o72_314", None),
    ("0b111_010011001100", None),
    ("+", None), (".", None), ("e5", None), ("1e", None), ("1e+-5", None), ("0x", None), ("0o", None), ("0b", None),
    ("29900..", None), ("29900.e", None), ("29900. 0", None), ("..5", None), (". 5", None), ("./5", None),
    (".:5", None), ("1./", None), ("1.:", None), ("1.g", None), ("1.+5", None), (".e", None), (".5e", None),
    ("1.e+", None), ("1.e-", None), ("1.5.", None), ("1e5.5", None), ("1e5.", None), (".5g", None), (".5+", None),
    (".5 5", None), (".5e5e", None), ("1ee5", None), ("1eE5", None), ("1e2e3", None), ("1e5e", None), ("1e5g", None),
    ("1e5+", None), ("1e5 5", None), ("1e,5", None), ("1e.5", None), ("1e/5", None), ("1e:5", None), ("12+5", None),
    ("1+5", None), ("1-5", None), ("10-5", None), ("18-5", None), ("185-5", None), ("1x5", None), ("1g5", None),
    ("0eff", None), ("0-5", None), ("0+5", None), ("0-1", None), ("0+1", None), ("0 1", None), ("0.ff", None),
    ("02f", None), ("00ff", None), ("08ff", None), ("0a1", None), ("0274cc", None),
    ("0o8", None), ("0b2", None), ("x74cc", None), ("-0x74cc", None), ("Inf", None), ("infinity", None),
    ("0x 7", None), ("0x7g", None), ("0x7.", None), ("0x7+", None), ("0x74 cc", None), ("0X7G", None), ("0X7@", None),
    ("0x:", None), ("0x/", None), ("0x@", None), ("0x`", None), ("0xx5", None), ("1x74cc", None), ("00x74cc", None),
    ("0o 7", None), ("0o7g", None), ("0o7.", None), ("0o7+", None), ("0o7e", None), ("0o7x", None), ("0o/", None),
    ("0oo7", None), ("00o7", None), ("o72314", None), ("1o7", None), ("0b 1", None), ("0b1g", None), ("0b18", None),
    ("0b1.", None), ("0b1-", None), ("0b1+", None), ("0b1x", None), ("0b1e", None), ("0b1 1", None), ("0bb1", None),
    ("00b1", None), ("b1", None), ("1b1", None), ("0gf", None), ("0g7", None), ("0g1", None),
    ("Infinit", None), ("Infinityy", None), ("IInfinity", None), ("nfinity", None), ("Ifinity", None),
    ("Innfinity", None), ("Ininity", None), ("Inffinity", None), ("Infnity", None), ("Infiinity", None),
    ("Infiity", None), ("Infinnity", None), ("Infinty", None), ("Infiniity", None), ("Infiniy", None),
    ("Infinitty", None),
    ("NaN", None), ("\u0662\u0669\u0669\u0660\u0660", None), ("\x1c29900", None), ("\x8529900", None),
    ("29900.\u0660", None), ("2.99e\u0664", None), (".\u0662\u0669\u0669e5", None), ("0x\u0667\u0664cc", None),
    ("0o\u0667\u0662\u0663\u0661\u0664", None), ("0b\u0661", None),
    ("\u180e29900", None), ("\u200b29900", None),
    ("29900", "29900"), (" 29900 ", "29900"), ("\t\n29900\r\v\f", "29900"),
    ("\xa0\u1680\u2000\u2005\u200a29900\u2028\u2029\u202f\u205f\u3000\ufeff", "29900"),
    ("029900", "29900"), ("+29900", "29900"), ("29900.", "29900"), ("29900.000", "29900"),
    ("2.99e4", "29900"), ("299E2", "29900"), ("2.99e+4", "29900"), ("2990000e-2", "29900"),
    (".29900e5", "29900"), (".0299e6", "29900"), ("29900000000000e-9", "29900"),
    ("0x74cc", "29900"), ("0X74CC", "29900"), ("0o72314", "29900"), ("0O72314", "29900"),
    ("0x7539", "30009"), ("0x74ca", "29898"), ("0X74CA", "29898"), ("0X74CF", "29903"), ("0o70000", "28672"),
    ("0b111010011001100", "29900"), ("0B111010011001100", "29900"),
    ("1", "1"), ("65535", "65535"), ("40123", "40123"), ("0x7", "7"), ("0o7", "7"), ("0b1", "1"), ("0o7 ", "7"),
    ("65536", "65536"), ("70000", "70000"), ("-1", "-1"), ("29900.5", "29900.5"), (" 1e5 ", "1e5"),
    (".5", ".5"), ("Infinity", "Infinity"), ("-Infinity", "-Infinity"), ("0x10000", "0x10000"),
    (".55", ".55"), (".9", ".9"), ("1e400", "1e400"), ("0x" + "f" * 300, "0x" + "f" * 300),
]


def _resolved(env):
    """The port the URL names for `env`: VALUES' entry for the first of NAMES whose value has one, else
    29855."""
    reads = dict(VALUES)
    return next((reads[env[n]] for n in NAMES if n in env and reads[env[n]]), "29855")


# Long runs: for each part of a number that may repeat (each + and * in _JS_NUMBER), values in which that part holds
# a run of 1,000 zeros and which Number() reads as the port 29900. The zeros go before the digits of a whole number,
# of an exponent and of a 0x, 0o or 0b number; after the digits that follow a point, with a digit before the point
# and without; and before those digits, with the exponent raised to match. Each decimal value is given with no sign
# and with "+" (a "-" would make it negative); the exponents are written with no sign, with "+" and, where the value
# can still be a port, with "-"
RUN = "0" * 1000
_DECIMAL_RUNS = (
    # the digits of a whole number
    RUN + "29900", RUN + "29900.", RUN + "29900.0", RUN + "2990e1", RUN + "2990e+1", RUN + "299000e-1",
    # the digits after a point with a digit before it
    "29900." + RUN, "2.99" + RUN + "e4", "2.99" + RUN + "e+4", "299000." + RUN + "e-1", "0." + RUN + "299e1005",
    "0." + RUN + "299e+1005",
    # the digits after a leading point
    ".299" + RUN + "e5", ".299" + RUN + "e+5", "." + RUN + "299e1005", "." + RUN + "299e+1005",
    # the digits of an exponent
    "2.99e" + RUN + "4", "2.99e+" + RUN + "4", "2990000e-" + RUN + "2", ".299e" + RUN + "5", ".299e+" + RUN + "5")
# (long value, the port the URL names when that value is the one read)
LONG_PORTS = [(sign + value, "29900") for sign in ("", "+") for value in _DECIMAL_RUNS] + [
    (prefix + RUN + digits, "29900") for prefix, digits in (("0x", "74cc"), ("0X", "74CC"), ("0o", "72314"),
                                                           ("0O", "72314"), ("0b", "111010011001100"),
                                                           ("0B", "111010011001100"))]

# (environment, the port the URL names): every pair of VALUES, or unset, for the two names; then each LONG_PORTS
# value in either name with the other unset, and in ROMP_SERVE_PORT with ALT in ROMP_KERNEL_PORT (three rows each,
# where pairing them with VALUES would add a row for every pairing)
RESOLUTION = [(env, _resolved(env)) for env in
              ({n: v for n, v in zip(NAMES, pair) if v is not None}
               for pair in itertools.product([None] + [v for v, _ in VALUES], repeat=2))] + [
    (env, port) for value, port in LONG_PORTS
    for env in ({NAMES[0]: value}, {NAMES[1]: value}, {NAMES[0]: value, NAMES[1]: str(ALT)})]

# one number of each form Number() reads, most of them a port: a whole number, with and without a sign; with a
# fraction (no digit after the point, and one) and with an exponent (with and without its sign); starting at the
# point; Infinity, with and without a sign; and a 0x, 0o and 0b number. Each repeated part stands at its least count
# in at least one of them (one digit where one or more may stand, none where any number may), and each optional part
# is present in one and absent in another
NUMBERS = ("7", "29900", "+29900", "-29900", "29900.", "29900.0", "3e4", "2.99e4", "2.99e+4", "2990000e-2", ".3e5",
           "+.3e5", ".299e5", "Infinity", "+Infinity", "-Infinity", "0x7", "0x74cc", "0o7", "0o72314", "0b1",
           "0b111010011001100")
# the characters put inside them: every ASCII character but NUL (each one the forms name, and those on either side
# of each range of them), and every other character of the Basic Multilingual Plane that is a decimal digit or white
# space, with U+FEFF, which Number() trims as white space
INSIDE = [c for c in map(chr, range(1, 0x10000)) if c.isascii() or c.isdecimal() or c.isspace() or c == "\ufeff"]


def _edits(value, chars):
    """Each value one edit away from `value`: a character of `chars` inserted at a position or put in place of the
    character there, or one character deleted."""
    for i in range(len(value) + 1):
        for c in chars:
            yield value[:i] + c + value[i:]
            if i < len(value):
                yield value[:i] + c + value[i + 1:]
        if i < len(value):
            yield value[:i] + value[i + 1:]


# values given as bytes: characters as UTF-8, and byte sequences that are not UTF-8
BYTE_VALUES = [c.encode("utf-8") for c in " \t\xa0\u1680\u2000\u2028\u2029\u202f\u3000\ufeff"
               "\x85\u180e\u200b\u2060\ufffd\U0001f600"] + [
    b"\xa0", b"\x85", b"\xff", b"\xc0\xa0", b"\xe2\x80", b"\xed\xa0\x80", b"\xef\xbb", b"\xf4\x90\x80\x80"]
LOCALE_SETTINGS = ({"LC_ALL": "C.UTF-8"}, {"LC_ALL": "C"}, {"LC_ALL": "C", "PYTHONUTF8": "1"},
                   {"LC_ALL": "C", "PYTHONUTF8": "0"})

# (long value, the port the URL names when both names hold it), and the CPU time the child reading them all may use
LONG = 100000
LONG_VALUES = [("1" * LONG + "x", "29855"), ("1" * LONG + "." + "1" * LONG + "x", "29855"),
               ("1" * LONG + "e" + "1" * LONG + "x", "29855"), ("." + "1" * LONG + "x", "29855"),
               ("0x" + "f" * LONG + "x", "29855"), ("1" * LONG, "1" * LONG)]
CPU_LIMIT_S = 10

OTHER = {"/version": {"kernel_sha": "other00", "pid": 111, "uptime_s": 5, "dist_ver": 1, "bundles": {}},
         "/healthz": b"ok",
         "/tunnels": {"tunnels": [{"host": "TESTHOST", "outOfDate": True}]},
         "/tunnels/update": {"ok": True, "detail": "updated by the other kernel"}}
MINE = {"/version": {"kernel_sha": "own0000", "pid": 222, "uptime_s": 5, "dist_ver": 1, "bundles": {}},
        "/healthz": b"ok",
        "/tunnels": {"tunnels": []}}


class _Resp:
    status = 200

    def __init__(self, body):
        self._b = body

    def read(self, n=-1):
        return self._b if n is None or n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_net(listening):
    """listening = {port: {path: body}}. A port not listed refuses the connection; a path a listener does not
    serve answers 401. Returns (urlopen, calls), calls being (port, path, X-Romp-Token or None) per request,
    the port as the URL writes it (an int when it is all digits)."""
    calls = []

    def urlopen(req, data=None, timeout=None, **kw):
        if isinstance(req, urllib.request.Request):
            url, tok = req.full_url, req.get_header("X-romp-token")
        else:
            url, tok = req, None
        p = urllib.parse.urlsplit(url)
        port = p.netloc.rpartition(":")[2]
        port = int(port) if port.isascii() and port.isdigit() else port
        calls.append((port, p.path, tok))
        routes = listening.get(port)
        if routes is None:
            raise urllib.error.URLError("connection refused")
        if p.path not in routes:
            raise urllib.error.HTTPError(url, 401, "unauthorized", {}, io.BytesIO(b"{}"))
        body = routes[p.path]
        return _Resp(body if isinstance(body, bytes) else json.dumps(body).encode())

    return urlopen, calls


def _env(**ports):
    """The environment for one case: the synthetic token, and exactly the port variables given (the suite's
    dead-port floor removed, so an unset variable is really unset)."""
    env = {k: v for k, v in os.environ.items() if k not in ("ROMP_KERNEL_PORT", "ROMP_SERVE_PORT")}
    env["ROMP_SERVE_TOKEN"] = TOKEN
    env.update({k: str(v) for k, v in ports.items()})
    return mock.patch.dict(os.environ, env, clear=True)


def _others():
    """Another listener answering on every port the old probe list tried."""
    return {p: OTHER for p in OTHER_PORTS}


def _set_ports(env):
    """Inside _env(): exactly the port variables in `env`, each set as its value's UTF-8 bytes."""
    for name in NAMES:
        value = env.get(name)
        if value is None:
            os.environb.pop(name.encode(), None)
        else:
            os.environb[name.encode()] = value.encode("utf-8")


def _listenable(port):
    """Whether the port text names a port a kernel can listen on: a whole number from 1 to 65535."""
    return port.isascii() and port.isdigit() and 0 < int(port) < 65536


def _asks(mod, listening=()):
    """The ports one command asks in the current environment (`romp update` through ru._kernel, `romp version`
    through ver._probe_kernel), with a kernel on each port in `listening`."""
    urlopen, calls = fake_net({p: MINE for p in listening})
    with mock.patch.object(urllib.request, "urlopen", urlopen):
        (ru._kernel if mod is ru else ver._probe_kernel)()
    return {p for p, _, _ in calls}


def _asked(listening=()):
    """The ports `romp update` and `romp version` each ask in the current environment, with a kernel on each
    port in `listening`."""
    return _asks(ru, listening), _asks(ver, listening)


_MANAGER_EVAL = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const m = src.match(/^const KERNEL_PORT = (.+);$/m);
if (!m || !m[1].includes('ROMP_SERVE_PORT') || !m[1].includes('ROMP_KERNEL_PORT')) {
  process.stderr.write('no `const KERNEL_PORT = ...;` line naming both variables in ' + process.argv[1] + '\n');
  process.exit(3);
}
const pick = new Function('process', 'return (' + m[1] + ');');
const envs = process.argv[2] === '-' ? JSON.parse(require('fs').readFileSync(0, 'utf8')) : [process.env];
process.stdout.write(JSON.stringify(envs.map((env) => String(pick({ env })))));
"""


def _manager(args, **kw):
    """bin/romp-manager's own `const KERNEL_PORT = ...;` expression, read from its source and evaluated by node,
    as the port it passes the kernel (String() of it). Keyed on that line, so a manager that moves the
    expression elsewhere fails here by name rather than passing."""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    r = subprocess.run([node, "-e", _MANAGER_EVAL, MANAGER] + args, capture_output=True, timeout=120, **kw)
    if r.returncode != 0:
        raise AssertionError("node failed:\n" + r.stderr.decode(errors="replace"))
    return json.loads(r.stdout)


def _manager_ports(envs):
    """The manager's port for each environment in `envs`."""
    return _manager(["-"], input=json.dumps(envs).encode())


_CHILD = r"""
import json, os, sys, urllib.request
sys.path.insert(0, sys.argv[1])
from romp_load import load_source
mods = ((load_source("romp_update_kernel_port_child", sys.argv[2]), "_kernel"),
        (load_source("romp_version_kernel_port_child", sys.argv[3]), "_probe_kernel"))
calls = []
def urlopen(req, *a, **kw):
    calls.append(getattr(req, "full_url", req).rsplit("/", 1)[0])
    raise OSError("connection refused")
urllib.request.urlopen = urlopen
out = []
for row in json.load(sys.stdin):
    for name in (b"ROMP_SERVE_PORT", b"ROMP_KERNEL_PORT"):
        os.environb.pop(name, None)
    for name, value in row.items():
        os.environb[name.encode()] = bytes.fromhex(value)
    seen = []
    for mod, ask in mods:
        del calls[:]
        getattr(mod, ask)()
        seen.append([mod._kernel_base(), sorted(set(calls))])
    out.append(seen)
json.dump(out, sys.stdout)
"""

_LONG_CHILD = r"""
import json, os, resource, sys
cap = int(sys.argv[4])
hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
if hard != resource.RLIM_INFINITY:
    cap = min(cap, hard)
resource.setrlimit(resource.RLIMIT_CPU, (cap, cap))
sys.path.insert(0, sys.argv[1])
from romp_load import load_source
mods = (load_source("romp_update_kernel_port_long", sys.argv[2]),
        load_source("romp_version_kernel_port_long", sys.argv[3]))
out = []
for value in json.load(sys.stdin):
    for name in (b"ROMP_SERVE_PORT", b"ROMP_KERNEL_PORT"):
        os.environb[name] = value.encode()
    out.append([mod._kernel_base() for mod in mods])
json.dump(out, sys.stdout)
"""


def _floor():
    """The environment a child Python gets: the suite's path, home, temp and state directories, nothing else."""
    return {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "XDG_STATE_HOME") if k in os.environ}


def _short(env):
    """`env` as a failure message shows it: each value longer than 60 characters cut to its first 40 and its
    length."""
    return {n: v if len(v) <= 60 else "%s... (%d characters)" % (v[:40], len(v)) for n, v in env.items()}


class BothModulesResolveOnePort(unittest.TestCase):
    """Both modules against one table of environments, and against bin/romp-manager's own expression."""
    maxDiff = None  # a failure shows the first ten environments that differ in full

    def _match_manager(self, envs):
        got = _manager_ports(envs)
        self.assertEqual(len(got), len(envs), got)
        bad = []
        with _env():
            for env, port in zip(envs, got):
                _set_ports(env)
                if _listenable(port):
                    seen, want = (ru._kernel_base(), ver._kernel_base()), ("http://127.0.0.1:" + port,) * 2
                else:
                    seen, want = _asked(), (set(), set())
                if seen != want:
                    bad.append((_short(env), port, seen))
        self.assertEqual(bad[:10], [], "%d of %d environments resolve otherwise than the manager"
                         % (len(bad), len(envs)))

    def _match_table(self, check):
        """check(mod, port) for each module in each RESOLUTION row's environment, returning what the module gave
        and what the row expects of it. The rows where the two differ are collected per module, and the first ten
        of each, with a count, are reported."""
        bad = {ru: [], ver: []}
        with _env():
            for env, port in RESOLUTION:
                _set_ports(env)
                for mod, rows in bad.items():
                    try:
                        seen, want = check(mod, port)
                    except Exception as e:
                        seen, want = "raised %r" % e, None
                    if seen != want:
                        rows.append((_short(env), seen, want))
        self.assertEqual([rows[:10] for rows in bad.values()], [[], []],
                         "%d environments in update and %d in version, of %d, differ from the table"
                         % (len(bad[ru]), len(bad[ver]), len(RESOLUTION)))

    def test_each_environment_resolves_to_the_same_port_in_both_modules(self):
        self._match_table(lambda mod, port: (mod._kernel_base(), "http://127.0.0.1:" + port))

    def test_each_command_asks_only_a_port_a_kernel_can_listen_on(self):
        def check(mod, port):
            want = {int(port)} if _listenable(port) else set()
            return _asks(mod, want), want
        self._match_table(check)

    def test_the_resolved_port_is_the_one_the_manager_starts_the_kernel_on(self):
        self._match_manager([env for env, _ in RESOLUTION])

    def test_every_bmp_character_around_a_port_is_read_as_the_manager_reads_it(self):
        """Each character of the Basic Multilingual Plane but NUL and the surrogates, before and after a port in
        ROMP_SERVE_PORT, with ALT in ROMP_KERNEL_PORT."""
        self._match_manager([{"ROMP_SERVE_PORT": c + str(OWN) + c, "ROMP_KERNEL_PORT": str(ALT)}
                             for c in map(chr, range(1, 0x10000)) if not "\ud800" <= c <= "\udfff"])

    def test_each_number_edited_by_one_INSIDE_character_or_one_deletion_is_read_as_the_manager_reads_it(self):
        """Each NUMBERS entry with each character of INSIDE inserted at each position or put in place of each of
        its characters, and with each of its characters deleted, in ROMP_SERVE_PORT, with ALT in ROMP_KERNEL_PORT."""
        self._match_manager([{"ROMP_SERVE_PORT": value, "ROMP_KERNEL_PORT": str(ALT)}
                             for value in dict.fromkeys(v for number in NUMBERS for v in _edits(number, INSIDE))])

    def test_each_value_is_decoded_as_the_manager_decodes_it_under_each_locale_setting(self):
        rows = [{"ROMP_SERVE_PORT": b + str(OWN).encode() + b, "ROMP_KERNEL_PORT": str(ALT).encode()}
                for b in BYTE_VALUES]
        want = []
        for row in rows:
            env = {b"PATH": os.environb.get(b"PATH", b"/usr/bin:/bin")}
            env.update({k.encode(): v for k, v in row.items()})
            port, = _manager([], env=env)
            base = "http://127.0.0.1:" + port
            want.append([[base, [base]]] * 2 if _listenable(port) else None)
        floor = _floor()
        for setting in LOCALE_SETTINGS:
            with self.subTest(setting=setting):
                r = subprocess.run([sys.executable, "-B", "-c", _CHILD, HERE, os.path.join(BIN, "romp-update"),
                                    os.path.join(BIN, "romp-version")],
                                   input=json.dumps([{k: v.hex() for k, v in row.items()} for row in rows]),
                                   env={**floor, **setting}, capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr)
                for row, seen, w in zip(rows, json.loads(r.stdout), want):
                    if w is None:
                        self.assertEqual([asked for _, asked in seen], [[], []], row)
                    else:
                        self.assertEqual(seen, w, row)

    def test_long_values_are_read_within_a_cpu_time_limit(self):
        """Both modules read LONG_VALUES, each set in both names, in a child Python whose CPU time is capped at
        CPU_LIMIT_S (RLIMIT_CPU): the child finishes, and each module builds the URL the table gives."""
        try:
            r = subprocess.run([sys.executable, "-B", "-c", _LONG_CHILD, HERE, os.path.join(BIN, "romp-update"),
                                os.path.join(BIN, "romp-version"), str(CPU_LIMIT_S)],
                               input=json.dumps([v for v, _ in LONG_VALUES]), env=_floor(), capture_output=True,
                               text=True, timeout=600)
        except subprocess.TimeoutExpired:
            self.fail("the child reading the long values did not finish within 600 s")
        self.assertEqual(r.returncode, 0, "the child reading the long values was stopped after %d s of CPU time, or "
                         "failed: %s" % (CPU_LIMIT_S, r.stderr[-2000:]))
        got = json.loads(r.stdout)
        want = [["http://127.0.0.1:" + port] * 2 for _, port in LONG_VALUES]
        self.assertTrue(got == want, "the URLs differ: %r" % [[u if len(u) < 60 else "%s... (%d characters)"
                                                             % (u[:40], len(u)) for u in row] for row in got])


class UpdateUsesTheConfiguredKernelPort(unittest.TestCase):
    def _update(self, net, **ports):
        urlopen, calls = fake_net(net)
        out, err = io.StringIO(), io.StringIO()
        with _env(**ports), mock.patch("urllib.request.urlopen", urlopen), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            base = ru._kernel()
            rc = ru.main([])
        return base, rc, out.getvalue(), err.getvalue(), calls

    def test_the_token_goes_only_to_the_kernel_on_ROMP_KERNEL_PORT(self):
        base, rc, out, err, calls = self._update({**_others(), OWN: MINE}, ROMP_KERNEL_PORT=OWN)
        self.assertEqual(base, "http://127.0.0.1:%d" % OWN)
        self.assertEqual((rc, err), (0, ""), out + err)
        self.assertIn("no remotes attached", out, "this romp's own remotes were listed")
        self.assertEqual({p for p, _, tok in calls if tok}, {OWN}, "the serve token went to another port: %r" % calls)
        self.assertEqual({p for p, _, _ in calls}, {OWN}, "no other port is even asked: %r" % calls)

    def test_ROMP_SERVE_PORT_is_the_same_port_by_its_other_name(self):
        base, rc, out, err, calls = self._update({**_others(), OWN: MINE}, ROMP_SERVE_PORT=OWN)
        self.assertEqual(base, "http://127.0.0.1:%d" % OWN)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_ROMP_SERVE_PORT_is_asked_before_ROMP_KERNEL_PORT(self):
        base, rc, out, err, calls = self._update({**_others(), OWN: MINE, ALT: OTHER},
                                                 ROMP_SERVE_PORT=OWN, ROMP_KERNEL_PORT=ALT)
        self.assertEqual(base, "http://127.0.0.1:%d" % OWN)
        self.assertEqual((rc, err), (0, ""), out + err)
        self.assertIn("no remotes attached", out)
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_a_port_outside_the_range_is_not_dialled(self):
        base, rc, out, err, calls = self._update({**_others(), OWN: MINE}, ROMP_SERVE_PORT="70000",
                                                 ROMP_KERNEL_PORT=OWN)
        self.assertIsNone(base)
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [], "no request was made for an out-of-range port: %r" % calls)
        self.assertIn("127.0.0.1:70000", err, "the refusal names the resolved port")

    def test_a_down_kernel_is_reported_and_no_other_listener_takes_its_place(self):
        # this romp's kernel is down; every port the old list tried answers. Nothing may be listed or pushed
        # through another listener, and the refusal names the port it asked
        base, rc, out, err, calls = self._update(_others(), ROMP_KERNEL_PORT=OWN)
        self.assertIsNone(base)
        self.assertEqual(rc, 2)
        self.assertNotIn("updating", out, "nothing is pushed through another kernel")
        self.assertIn("127.0.0.1:%d" % OWN, err, "the refusal names the port it asked")
        self.assertEqual([c for c in calls if c[2]], [], "no request carried the serve token")
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_with_no_port_variable_the_default_port_is_used(self):
        base, rc, out, err, calls = self._update({29855: MINE})
        self.assertEqual(base, "http://127.0.0.1:29855")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("no remotes attached", out)


class VersionReportsTheConfiguredKernel(unittest.TestCase):
    def _probe(self, net, **ports):
        urlopen, calls = fake_net(net)
        with _env(**ports), mock.patch("urllib.request.urlopen", urlopen):
            return ver._probe_kernel(), calls

    def test_the_kernel_on_ROMP_KERNEL_PORT_is_reported_not_the_default_ports(self):
        (kind, k), calls = self._probe({**_others(), OWN: MINE}, ROMP_KERNEL_PORT=OWN)
        self.assertEqual(kind, "version")
        self.assertEqual((k.get("kernel_sha"), k.get("pid"), k.get("url")),
                         ("own0000", 222, "http://127.0.0.1:%d" % OWN))

    def test_the_kernel_is_found_when_nothing_answers_on_the_default_port(self):
        (kind, k), calls = self._probe({OWN: MINE}, ROMP_SERVE_PORT=OWN)
        self.assertEqual((kind, k.get("kernel_sha")), ("version", "own0000"))

    def test_ROMP_SERVE_PORT_is_asked_before_ROMP_KERNEL_PORT(self):
        (kind, k), calls = self._probe({**_others(), OWN: MINE, ALT: OTHER},
                                       ROMP_SERVE_PORT=OWN, ROMP_KERNEL_PORT=ALT)
        self.assertEqual((kind, k.get("kernel_sha"), k.get("url")),
                         ("version", "own0000", "http://127.0.0.1:%d" % OWN))
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_a_port_outside_the_range_is_not_dialled(self):
        urlopen, calls = fake_net({**_others(), OWN: MINE})
        with _env(ROMP_SERVE_PORT="70000", ROMP_KERNEL_PORT=OWN), \
                mock.patch("urllib.request.urlopen", urlopen), \
                mock.patch.object(ver, "_git_sha", lambda: "abc1234"), \
                mock.patch.object(ver, "_disk_bundles", lambda: {}):
            kind, k = ver._probe_kernel()
            out = ver.report()
        self.assertEqual((kind, k), ("down", None))
        self.assertEqual(calls, [], "no request was made for an out-of-range port: %r" % calls)
        self.assertNotIn("own0000", out)
        self.assertIn("not reachable at http://127.0.0.1:70000", out, "the report names the resolved port")

    def test_a_kernel_predating_version_on_the_configured_port_is_stale(self):
        (kind, k), calls = self._probe({**_others(), OWN: {"/healthz": b"ok"}}, ROMP_KERNEL_PORT=OWN)
        self.assertEqual((kind, k), ("stale", "http://127.0.0.1:%d" % OWN))

    def test_a_down_kernel_is_down_whatever_else_answers(self):
        urlopen, calls = fake_net(_others())
        with _env(ROMP_KERNEL_PORT=OWN), mock.patch("urllib.request.urlopen", urlopen), \
                mock.patch.object(ver, "_git_sha", lambda: "abc1234"), \
                mock.patch.object(ver, "_disk_bundles", lambda: {}):
            kind, k = ver._probe_kernel()
            out = ver.report()
        self.assertEqual((kind, k), ("down", None))
        self.assertNotIn("other00", out, "another kernel's sha is never reported as this romp's")
        self.assertIn("not reachable at http://127.0.0.1:%d" % OWN, out, "the report names the port it asked")
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_with_no_port_variable_the_default_port_is_used(self):
        (kind, k), calls = self._probe({29855: MINE})
        self.assertEqual((kind, k.get("url")), ("version", "http://127.0.0.1:29855"))


if __name__ == "__main__":
    unittest.main()
