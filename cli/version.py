#!/usr/bin/env python3
"""romp-version — what version of romp is running, across all the moving parts. `romp version`.

Three things drift independently and a stale one is the usual cause of "the UI doesn't match the code":
  - the WORKING TREE (the .py/.js sources the kernel loads straight from bin/ and vscode-extension/),
  - the RUNNING KERNEL (what it last (re)started from — reported by its /version endpoint),
  - the BUILT BUNDLES on disk vs the bundle the kernel is actually serving.
This prints all three so you can see at a glance whether a `romp refresh` (or a browser hard-reload) is
owed — instead of reading restart logs. No filesystem paths are printed (privacy).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "vscode-extension" / "dist"


def _git_sha():
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=2)
        sha = r.stdout.strip() if r.returncode == 0 else ""
        if sha:
            d = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=2)
            if d.returncode == 0 and d.stdout.strip():
                sha += "-dirty"
        return sha or None
    except Exception:
        return None


def _disk_bundles():
    out = {}
    try:
        for p in sorted(DIST.glob("*.js")) + sorted(DIST.glob("*.css")):
            try:
                out[p.name] = int(p.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return out


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
    """This romp's kernel, on the port _kernel_port() reads, or naming the value it read when that is not a port
    a kernel can listen on (then nothing is asked). Only that one port is asked; a fixed list of ports used to be
    tried in turn, taking whatever answered first."""
    port, value = _kernel_port()
    return "http://127.0.0.1:%s" % (value if port is None else port)


def _probe_kernel():
    """The running kernel's self-report. Returns ('version', dict) when /version answers,
    ('stale', url) when only /healthz answers (a kernel predating /version → restart it), or
    ('down', None) when nothing is listening. Nothing is asked when _kernel_port() reads no port a kernel
    can listen on: that is ('down', None) too."""
    import urllib.request
    if _kernel_port()[0] is None:
        return ("down", None)
    u = _kernel_base()
    try:
        with urllib.request.urlopen(u + "/version", timeout=1.5) as resp:
            d = json.loads(resp.read().decode())
            d["url"] = u
            return ("version", d)
    except Exception:
        pass
    try:
        with urllib.request.urlopen(u + "/healthz", timeout=1.0) as resp:
            if resp.read(2) == b"ok":
                return ("stale", u)
    except Exception:
        pass
    return ("down", None)


# A part that counts a share of another part's demotions, listed inside that part under the short name: (reason, part) ->
# (the part it is a share of, the name it is listed under). The prompt-id gate counts boundarySeed only for a boundary
# demotion whose prompt id a restored entry's seed named, so it is part of the boundary count, never beside it. A kernel
# that also counts the boundary arm's reasons (g:promptid:boundary:<reason>, 2026-10-05) counts the same demotions as the
# nested `seed`, and boundarySeed is then not listed (_demotes).
_DEMOTE_SHARES = {("promptid", "boundarySeed"): ("boundary", "seed")}


def _demotes(stats):
    """The demotion counters of a road block as the report lists them: each `g:<reason>` with its count, and the
    `g:<reason>:<part>` counters that break that count down (the prompt-id gate's arms) in parentheses after it. Listed
    flat, a prompt-id demotion appeared once under its reason and again under each part that named it (2026-10-03). The parts
    in a reason's parentheses add up to the reason's count. A part named `<part>:<name>` (the boundary arm's reasons,
    g:promptid:boundary:<reason>, 2026-10-05) is listed inside <part> the same way, `boundary 6 (adopted 1, offpath 3, seed 1,
    seedEpisode 1)`: a breakdown, the reasons inside a part adding up to that part's count. A part that is a share of another
    part (_DEMOTE_SHARES) is listed inside it too, `boundary 3 (seed 2)`, as a share of that part, not a breakdown of it
    (beside the arms, the seed's demotions were counted twice, review round two); boundarySeed is listed that way only when
    the nested counter it duplicates, `boundary:seed`, is absent, as from a kernel that counts no reasons."""
    tops, parts = {}, {}
    for g, n in stats.items():
        if not (isinstance(g, str) and g.startswith("g:")) or not n:
            continue
        name, _, part = g[2:].partition(":")
        if part:
            parts.setdefault(name, {})[part] = n
        else:
            tops[name] = n
    out = []
    for name in sorted(set(tops) | set(parts)):
        s = "%s %d" % (name, tops.get(name, 0))
        arms, shares = dict(parts.get(name) or {}), {}
        nested = {p for p in arms if ":" in p}
        for part in sorted(arms):
            within = _DEMOTE_SHARES.get((name, part))
            if within is None and ":" in part:
                within = tuple(part.split(":", 1))     # a nested part: listed inside the part before its colon
            if within is not None:
                n = arms.pop(part)
                if part not in nested and "%s:%s" % within in nested:
                    continue                           # an older name the nested counter carries too: listed once
                shares.setdefault(within[0], []).append("%s %d" % (within[1], n))
                arms.setdefault(within[0], 0)
        if arms:
            s += " (%s)" % ", ".join("%s %d%s" % (p, n, " (%s)" % ", ".join(shares[p]) if p in shares else "")
                                     for p, n in sorted(arms.items()))
        out.append(s)
    return ", ".join(out)


def report():
    lines = ["romp     sha=%s  (working tree the kernel loads from)" % (_git_sha() or "?")]
    kind, k = _probe_kernel()
    if kind == "version":
        up = k.get("uptime_s", 0)
        lines.append("kernel   sha=%s pid=%s up=%dm%02ds dist_ver=%s  (%s)"
                     % (k.get("kernel_sha") or "?", k.get("pid"), up // 60, up % 60,
                        k.get("dist_ver"), k.get("url")))
        pa = k.get("parse") or {}
        if pa.get("fold", 0) + pa.get("full", 0) + pa.get("fallback", 0) > 0:
            # the assembly cache's live hit rate — the deploy-verification number (T210). The
            # fallback term keeps an all-fallback kernel VISIBLE: _asm_full counts "full" as its
            # last act, so a kernel whose every parse errors into fallback has fold+full == 0.
            rate = 100.0 * pa.get("fold", 0) / max(1, pa.get("fold", 0) + pa.get("full", 0))
            gates = _demotes(pa)
            lines.append("parse    fold %.0f%% (%d fold / %d full / %d served)%s%s%s%s"
                         % (rate, pa.get("fold", 0), pa.get("full", 0), pa.get("serve", 0),
                            "  fallbacks=%d" % pa["fallback"] if pa.get("fallback") else "",
                            ("  demotes: " + gates) if gates else "",
                            "  attachedPass=%d" % pa["fold:attachedPass"] if pa.get("fold:attachedPass") else "",
                            "  ts-repair=%d" % pa["ts-repair"] if pa.get("ts-repair") else ""))
        cf = k.get("chatfold") or {}                     # absent on kernels older than the chat fold
        if cf.get("fold", 0) + cf.get("full", 0) + cf.get("fallback", 0) > 0:
            rate = 100.0 * cf.get("fold", 0) / max(1, cf.get("fold", 0) + cf.get("full", 0))
            gates = _demotes(cf)
            lines.append("chat     fold %.0f%% (%d fold / %d full)%s%s"
                         % (rate, cf.get("fold", 0), cf.get("full", 0),
                            "  fallbacks=%d" % cf["fallback"] if cf.get("fallback") else "",
                            ("  demotes: " + gates) if gates else ""))
    elif kind == "stale":
        lines.append("kernel   running at %s but predates /version — `romp refresh` to populate" % k)
    else:
        lines.append("kernel   not reachable at %s — is the manager up? (`romp up` / `romp status`)" % _kernel_base())
    served = (k.get("bundles") if kind == "version" else {}) or {}
    disk = _disk_bundles()
    lines.append("bundles  (on disk%s):" % (" / served" if served else ""))
    for name, mt in disk.items():
        s = served.get(name)
        flag = ""
        if s is not None and s != mt:
            flag = "  ⚠ disk newer than served — `romp refresh`"
        lines.append("  %-14s %s%s" % (name, mt, flag))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.write(report() + "\n")
