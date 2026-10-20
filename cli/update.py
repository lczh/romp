#!/usr/bin/env python3
"""usage: romp update [host...]

Push THIS machine's committed romp to attached REMOTE kernels and restart them, so each runs exactly the
local code. With no host, every attached remote that is out of date (running a different commit than local)
is updated; naming hosts updates just those. The local kernel pushes its committed HEAD straight to each
host over ssh and restarts it: peer-to-peer, no `git pull` from origin, no round trip through GitHub.
Uncommitted local edits are NOT sent; commit first. To update THIS machine, `git pull` or check out the
commit you want, then `romp refresh`.

  -h, --help   print this and do nothing else. There are no other options: an unknown one is refused,
               and nothing is pushed.
"""
# Peer-to-peer, no GitHub, by the user's 2026-07-04 decision. The docstring doubles as the -h text, so it
# reads as usage first and keeps attributions down here.
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


# What JavaScript's Number() drops from both ends of a string (ECMA-262's StrWhiteSpaceChar: white space and
# line terminators), and the literals it reads from what remains: a decimal, with an optional sign, fraction
# and exponent, or Infinity; or a 0x, 0o or 0b integer.
_JS_SPACE = ("\t\n\v\f\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
             "\u2028\u2029\u202f\u205f\u3000\ufeff")
_JS_NUMBER = re.compile(r"[+-]?(?:Infinity|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
                        r"|0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+")


def _js_number(value):
    """The number JavaScript's Number() reads from the string `value`, or None where it reads NaN; an empty
    or all-space string reads as 0."""
    s = value.strip(_JS_SPACE)
    if not s:
        return 0.0
    if not _JS_NUMBER.fullmatch(s):
        return None
    base = {"0x": 16, "0o": 8, "0b": 2}.get(s[:2].lower())
    if not base:
        return float(s)
    try:
        return float(int(s[2:], base))
    except OverflowError:                       # past the largest double, which Number() reads as Infinity
        return float("inf")


def _kernel_port():
    """The kernel's port as (port, value), read from the environment as bin/romp-manager reads it when it starts
    the kernel: `Number(ROMP_SERVE_PORT) || Number(ROMP_KERNEL_PORT) || 29855`. So ROMP_SERVE_PORT, then
    ROMP_KERNEL_PORT, else 29855: each value is decoded as UTF-8, as node decodes the environment, and read as
    Number() reads it (_js_number), and a value read as 0, or not as a number, counts as unset. `port` is the
    number read, as an int, when it is a port a kernel can listen on (a whole number from 1 to 65535), else
    None; `value` is the value it was read from, trimmed as Number() trims it."""
    for name in (b"ROMP_SERVE_PORT", b"ROMP_KERNEL_PORT"):
        raw = os.environb.get(name)
        if raw is None:
            continue
        value = raw.decode("utf-8", "replace").strip(_JS_SPACE)
        n = _js_number(value)
        if n:
            return (int(n) if n.is_integer() and 0 < n < 65536 else None), value
    return 29855, "29855"


def _kernel_base():
    """The local kernel's base URL, on the port _kernel_port() reads, or naming the value it read when that is
    not a port a kernel can listen on (then nothing is asked). Only that one port is asked; this used to try
    29855, 7878 and 7432 in turn and take the first that answered."""
    port, value = _kernel_port()
    return "http://127.0.0.1:%s" % (value if port is None else port)


def _token():
    """The serve token — required on every kernel request, loopback included (Jupyter's model).
    Same resolution the kernel uses: env override, else the 0600 state file."""
    t = (os.environ.get("ROMP_SERVE_TOKEN") or "").strip()
    if t:
        return t
    try:
        root = Path(os.environ.get("ROMP_STATE_DIR")   # per-kernel state root (plans/multi-kernel.md)
                    or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")
        return (root / "serve-token").read_text().strip()
    except OSError:
        return ""


def _kernel():
    """The base URL of the running LOCAL kernel (it owns the remote tunnels), or None. Nothing is asked when
    _kernel_port() reads no port a kernel can listen on."""
    if _kernel_port()[0] is None:
        return None
    u = _kernel_base()
    try:
        with urllib.request.urlopen(u + "/version", timeout=1.5) as r:   # /version is auth-exempt
            if r.status == 200:
                return u
    except Exception:
        pass
    return None


def _get(u, path):
    req = urllib.request.Request(u + path, headers={"X-Romp-Token": _token()})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode() or "{}")


def _post(u, path, body):
    req = urllib.request.Request(u + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Romp-Token": _token()}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:          # git pull + restart can take a while
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:                              # 502 → {ok:false, detail:...}
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:
            return {"ok": False, "detail": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def main(argv):
    # bin/romp hands over every word after `update`, and this command defines no option, so a dash-prefixed
    # word is either a help request or a mistake. Both are answered here, before anything is pushed. Dropping
    # them unread, as the hosts line alone used to, sent `romp update --help` down the no-host path, which
    # force-pushed the local HEAD to every out-of-date remote and restarted it, cutting its sessions' turns.
    flags = [a for a in argv if a.startswith("-")]
    if "-h" in flags or "--help" in flags:                          # help needs no kernel and touches nothing
        sys.stdout.write(__doc__)
        return 0
    if flags:
        sys.stderr.write("romp update: unknown option %s (usage: romp update [host...])\n" % flags[0])
        return 2
    u = _kernel()
    if not u:
        sys.stderr.write("romp update: no running kernel at %s (is romp on? try `romp status`)\n" % _kernel_base())
        return 2
    hosts = list(argv)
    if not hosts:                                                   # no host → the out-of-date attached remotes
        try:
            tuns = _get(u, "/tunnels?fresh=1").get("tunnels", [])    # judged against the head this checkout is at NOW, not the dashboard's 15 s cache
        except Exception as e:
            sys.stderr.write("romp update: couldn't list remotes: %s\n" % e)
            return 2
        if not tuns:
            sys.stdout.write("romp update: no remotes attached (attach one from the rail's network popover).\n")
            return 0
        hosts = [t["host"] for t in tuns if t.get("outOfDate")]
        if not hosts:
            sys.stdout.write("romp update: all attached remotes are up to date.\n")
            return 0
    rc = 0
    for h in hosts:
        sys.stdout.write("updating %s … " % h)
        sys.stdout.flush()
        body = _post(u, "/tunnels/update", {"host": h})
        if body.get("ok"):
            sys.stdout.write("ok — %s\n" % (body.get("detail") or "updated + restarting"))
        else:
            sys.stdout.write("FAILED — %s\n" % (body.get("detail") or "unknown"))
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
