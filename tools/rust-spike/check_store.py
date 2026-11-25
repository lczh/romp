#!/usr/bin/env python3
"""Step S3's comparison (rust/SPIKE.md): the Rust transcript store (rust/crates/romp-transcript) against Python's own
reader, kernel/event_model.py `_read_jsonl_incremental` and the cache entry it serves (`_JSONL_CACHE`).

Both sides print the same canonical summary (romp_transcript::canon): per file its size, mtime, inode, consumed
offset, record count and the CRC-32 of the bytes held; per record its offset and length (Python's offsets array), its
JSON kind, `type`, `uuid`, `parentUuid`, `timestamp`, `message.role`, the tool ids of `message.content`, and the
record itself (`json.dumps(record, sort_keys=True)`, or its CRC-32). The lines are compared byte for byte.

Three comparisons, each with a planted defect it must catch (the Rust store built with `--plant`):
  --dump DIR   every file of a dump_world.py dump's transcripts/ (repeatable). Plant: no-tool-result.
  edge corpus  one file of lines chosen at the edges of Python's acceptance (bytes.strip, bytes.splitlines, json.loads
               in strict mode: NaN, lone surrogates, the 4,300-digit integer limit, the 9,997 nesting limit, a BOM,
               invalid UTF-8, duplicate keys, escaped keys, repeated keys over values the decoder refuses).
               Plant: vt-strip.
  cases        the identity contract in lockstep on temp files: append, a torn last line, touch, same-size rewrites
               inside and outside the tail guard and at its edge (offset - 64 and offset - 65), a same-size rewrite
               that keeps its mtime, two mtimes one float apart, shrink (a partial cut short but still past the held
               offset among them), relocation by rename, deletion, a directory. Each step changes the file once, then
               Python and a long-lived `romp-spike store-serve` read it; compared: how the read was served (Python's
               ROMP_READER_TRACE kind, or hit, absent, failed), the generation (renumbered per side), and the whole
               summary with every record in full. Every step also states the kind it is built to produce, so a case
               that stopped exercising its road fails. Plant: skip-held-crc.
  --fuzz N     two randomized checks, seeds 1..N (seconds per seed): --fuzz-lines mutated lines (corpus lines and
               records with bytes changed, tokens inserted, spans cut, lines spliced, repeated keys put first or last)
               through the store comparison, and --fuzz-steps random file changes over two files in lockstep (appends,
               partials, cuts, truncations, one-byte edits near the end and anywhere, touches one second, a
               microsecond, 50 ns or nothing apart, renames over, removals). Plants: vt-strip and skip-held-crc.

Two differences are expected and named, never counted as passes silently: the Rust decoder refuses a record whose
final value (what Python keeps after a repeated key replaces an earlier value) holds a lone surrogate, in a string or a
key (a Rust String cannot carry one), or nests past 512 levels. Its `rec` reads "undecodable: ..." on those lines while
every other field must still match, and Python's record must really have the property named.

    python3 tools/rust-spike/check_store.py --dump <tmp>/romp-spike/small --dump <tmp>/romp-spike/large [--fuzz 6]

Python's module is imported with its state root, home and Claude root pointed into a temp directory, which is removed
afterwards; every case file lives there too. Nothing is installed: stdlib only.
"""
import argparse
import importlib.util
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib

sys.dont_write_bytecode = True   # the kernel module it imports would otherwise leave a .pyc in the repo

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BS = chr(92)                     # backslashes built from chr(92), so no editor turns an escape here into its character
DECODE_MAX_DEPTH = 512           # romp_transcript::decode::DECODE_MAX_DEPTH
UNDECODABLE = {"lone surrogate", "nesting too deep"}   # romp_transcript::decode::DecodeError::reason
PLANTS = {"dump": "no-tool-result", "edge": "vt-strip", "cases": "skip-held-crc"}
T0_NS = 1781100000 * 10 ** 9     # the cases' mtimes, explicit (writes would otherwise stamp the wall clock)
UUID = "11111111-2222-3333-4444-%012d"


class CheckError(Exception):
    pass


# ───────────────────────────── Python's side ─────────────────────────────
def load_event_model(root):
    """kernel/event_model.py under a private name, its STATE, HOME and Claude roots inside `root` (they bind at import)."""
    for k in list(os.environ):
        if k.startswith(("ROMP_", "CLAUDE_")):
            os.environ.pop(k)
    for sub in ("state", "xdg", "home", "claude"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    os.environ.update({"ROMP_STATE_DIR": os.path.join(root, "state"), "XDG_STATE_HOME": os.path.join(root, "xdg"),
                       "HOME": os.path.join(root, "home"), "CLAUDE_CONFIG_DIR": os.path.join(root, "claude")})
    spec = importlib.util.spec_from_file_location("romp_spike_event_model", os.path.join(REPO, "kernel", "event_model.py"))
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    if not os.path.realpath(str(em.STATE)).startswith(os.path.realpath(root) + os.sep):
        raise CheckError("event_model.STATE is %s, outside the temp root" % em.STATE)
    return em


TRACE_RE = re.compile(r"^reader: (\S+) (.+) base=(\d+) gen=(\d+) size=(\d+) by ")


def py_read(em, path):
    """(kind, entry) for one `_read_jsonl_incremental(path)`: the kind from Python's own reader trace (the line it writes
    for every read that pulled bytes), "hit" when it pulled none, "absent" or "failed" when it served no entry."""
    fails, err = [], io.StringIO()
    em._READER_TRACE = True
    real, sys.stderr = sys.stderr, err
    try:
        em._read_jsonl_incremental(path, on_fail=fails.append)
    finally:
        sys.stderr = real
        em._READER_TRACE = False
    ent = em._LAST_ENTRY.ent
    kinds = [m.group(1) for m in map(TRACE_RE.match, err.getvalue().splitlines()) if m and m.group(2) == str(path)]
    if ent is None:
        return ("failed" if fails else "absent"), None
    if len(kinds) > 1:
        raise CheckError("one read traced %d times: %r" % (len(kinds), kinds))
    return (kinds[0] if kinds else "hit"), ent


def kind_of(v):
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    if isinstance(v, str):
        return "string"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if v is None:
        return "null"
    raise CheckError("a record value of type %s" % type(v).__name__)


def field_value(v):
    return v if (v is None or isinstance(v, str)) else {"nonString": kind_of(v)}


def tool_ids(rec):
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    out = []
    for b in content if isinstance(content, list) else ():
        if not isinstance(b, dict):
            continue
        key = {"tool_use": "id", "tool_result": "tool_use_id"}.get(b.get("type")) if isinstance(b.get("type"), str) else None
        if key and isinstance(b.get(key), str):
            out.append(b[key])
    return out


def header(name, ent):
    return json.dumps({"file": name, "size": ent[1], "mtime": ent[0], "inode": ent[8], "consumed": ent[2],
                       "records": len(ent[4]), "crc32": ent[10]})


def record_line(i, off, ln, rec, full):
    """(prefix, tail) of record i's summary line; the line is prefix + tail, and tail carries the record."""
    d = {"i": i, "off": off, "len": ln, "kind": kind_of(rec)}
    if isinstance(rec, dict):
        for k in ("type", "uuid", "parentUuid", "timestamp"):
            if k in rec:
                d[k] = field_value(rec[k])
        msg = rec.get("message")
        if isinstance(msg, dict) and "role" in msg:
            d["role"] = field_value(msg["role"])
        d["toolIds"] = tool_ids(rec)
    try:
        canon = json.dumps(rec, sort_keys=True)
    except (RecursionError, ValueError) as e:
        canon, why = None, "python: %s" % type(e).__name__
    key = "record" if full else "rec"
    if canon is None:
        tail = ', "%s": %s}' % (key, json.dumps(why))
    elif full:
        tail = ', "record": %s}' % canon
    else:
        tail = ', "rec": %d}' % zlib.crc32(canon.encode())
    return json.dumps(d)[:-1], tail


def py_summary(em, path, name, full):
    """[(prefix, tail, record or None)] for one file: its header line, then one line per record."""
    kind, ent = py_read(em, path)
    if ent is None:
        return [(json.dumps({"file": name, "unreadable": True}), "", None)]
    out = [(header(name, ent), "", None)]
    offs = ent[7]
    for i, rec in enumerate(ent[4]):
        out.append(record_line(i, offs[2 * i], offs[2 * i + 1], rec, full) + (rec,))
    return out


def has_lone_surrogate(v):
    stack = [v]
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in x):
                return True
        elif isinstance(x, dict):
            stack.extend(x.keys())
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return False


def depth_of(v):
    best, stack = 0, [(v, 1)]
    while stack:
        x, d = stack.pop()
        if isinstance(x, (dict, list)):
            best = max(best, d)
            stack.extend((y, d + 1) for y in (x.values() if isinstance(x, dict) else x))
    return best


def expected_deviation(prefix, tail, rec, rust):
    """The named reason when `rust` differs from Python's line only where the Rust decoder refuses the record, and
    Python's record really is one it must refuse; else None."""
    if rec is None or not rust.startswith(prefix):
        return None
    m = re.fullmatch(r', "(?:rec|record)": "undecodable: ([a-z ]+)"\}', rust[len(prefix):])
    if not m or m.group(1) not in UNDECODABLE:
        return None
    if m.group(1) == "lone surrogate" and has_lone_surrogate(rec):
        return m.group(1)
    if m.group(1) == "nesting too deep" and depth_of(rec) > DECODE_MAX_DEPTH:
        return m.group(1)
    return None


# ───────────────────────────── the Rust side ─────────────────────────────
def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=os.path.join(REPO, "rust"),
                   check=True)
    return os.path.join(REPO, "rust", "target", "release", "romp-spike")


def rust_store(binary, d, full, plant=None):
    cmd = [binary, "store", d] + (["--records"] if full else []) + (["--plant", plant] if plant else [])
    p = subprocess.run(cmd, capture_output=True, check=True)
    return p.stdout.decode("utf-8").splitlines(), p.stderr.decode("utf-8").strip()


# ───────────────────────────── the store comparison (dumps and the edge corpus) ─────────────────────────────
def compare_store_dir(em, binary, d, full, plant=None):
    tdir = os.path.join(d, "transcripts")
    names = sorted(n for n in os.listdir(tdir) if os.path.isfile(os.path.join(tdir, n)))
    t = time.process_time()
    py = []
    for n in names:
        py.extend(py_summary(em, os.path.join(tdir, n), "transcripts/" + n, full))
    py_cpu = time.process_time() - t
    rs, footer = rust_store(binary, d, full, plant)
    res = {"dir": os.path.basename(d.rstrip(os.sep)), "files": len(names), "lines": len(py),
           "records": len(py) - len(names), "rustLines": len(rs), "mismatches": 0, "expected": {}, "first": [],
           "rustFooter": footer, "pythonCpuS": round(py_cpu, 2), "full": full, "plant": plant}
    if len(rs) != len(py):
        res["mismatches"] += abs(len(rs) - len(py))
        res["first"].append("line counts differ: Python %d, Rust %d" % (len(py), len(rs)))
    for n, ((prefix, tail, rec), r) in enumerate(zip(py, rs)):
        line = prefix + tail
        if line == r:
            continue
        why = expected_deviation(prefix, tail, rec, r)
        if why:
            res["expected"][why] = res["expected"].get(why, 0) + 1
            continue
        res["mismatches"] += 1
        if len(res["first"]) < 4:
            at = next((k for k, (a, b) in enumerate(zip(line, r)) if a != b), min(len(line), len(r)))
            res["first"].append("line %d at char %d: python %r / rust %r" % (n, at, line[max(0, at - 40):at + 60],
                                                                           r[max(0, at - 40):at + 60]))
    return res


def edge_lines():
    """[(label, bytes)] at the edges of what Python's reader keeps. Each is written followed by a newline."""
    def esc(h):
        return BS + "u" + h
    o = lambda **kw: json.dumps(kw)                                   # noqa: E731
    deep = lambda n: "[" * n + "]" * n                                # noqa: E731
    full = json.dumps({"type": "assistant", "uuid": UUID % 1, "parentUuid": UUID % 0, "timestamp": "2026-06-08T14:00:06Z",
                       "message": {"role": "assistant", "content": [
                           {"type": "text", "text": "Recap: limit → 3, the naïve cache is gone."},
                           {"type": "tool_use", "id": "toolu_e1", "name": "Edit", "input": {"file_path": "/TESTDIR/notes-api/search.py"}},
                           {"type": "tool_result", "tool_use_id": "toolu_e0", "content": "ok"}]}}, ensure_ascii=False)
    lines = [
        ("a full record, raw UTF-8", full),
        ("the same, ensure_ascii escapes", json.dumps(json.loads(full))),
        ("blank", ""),
        ("whitespace only", "   \t "),
        ("not json", "not json at all"),
        ("vertical tab and form feed around a record (bytes.strip removes both)", "\x0b" + o(type="vt", uuid="v1") + "\x0c"),
        ("\\x1c before a record (not bytes whitespace)", "\x1c{}"),
        ("CRLF ending", o(type="crlf") + "\r"),
        ("a bare CR splits one line into two records", o(type="cr1") + "\r" + o(type="cr2")),
        ("a bare CR then junk", o(type="cr3") + "\rjunk"),
        ("a record then CR CR LF (a blank piece between)", o(type="crcr") + "\r\r"),
        ("a CR alone", "\r"),
        ("a raw CR inside a string splits it into two refused pieces", '{"type": "x\ry"}'),
        ("top-level array", "[1, 2]"),
        ("top-level string", '"just a string"'),
        ("top-level numbers", "5"), ("negative zero", "-0"), ("a float", "1.5"), ("true", "true"), ("null", "null"),
        ("NaN", "NaN"), ("Infinity", "Infinity"), ("-Infinity", "-Infinity"),
        ("NaN inside a record", '{"type": "nan", "v": [NaN, -Infinity, 1e400, -0.0, 1E2]}'),
        ("a UTF-8 BOM before a record", chr(0xFEFF) + "{}"),
        ("a lone surrogate in uuid and type", '{"uuid": "%s", "type": "%sx"}' % (esc("d800"), esc("dc00"))),
        ("a lone surrogate under a repeated key the last value replaces (decodes)", '{"type": "%s", "type": "ok"}' % esc("d800")),
        ("nesting 600 under a repeated key the last value replaces (decodes)", '{"a": %s, "a": 1}' % deep(600)),
        ("a lone surrogate role in a message a later message replaces (decodes)",
         '{"message": {"role": "%s"}, "message": {"role": "user"}}' % esc("dc00")),
        ("a lone surrogate key under a replaced value (decodes)", '{"m": {"%s": 1}, "m": 2}' % esc("dc00")),
        ("a lone surrogate the last value keeps (Rust's decoder refuses)", '{"type": "ok", "type": "%s"}' % esc("d800")),
        ("nesting 600 the last value keeps (Rust's decoder refuses)", '{"a": 1, "a": %s}' % deep(600)),
        ("a lone surrogate key (Rust's decoder refuses)", '{"%s": 1}' % esc("d800")),
        ("a surrogate pair and every escape in type", '{"type": "a%sb"}' % "".join((
            BS + '"', BS + BS, BS + "/", BS + "b", BS + "f", BS + "n", BS + "r", BS + "t", esc("0001"), esc("007f"),
            esc("00e9"), esc("d83d") + esc("de00")))),
        ("escaped keys", '{"%sype": "user", "%suid": "u1", "message": {"%sole": "user"}}' % (esc("0074"), esc("0075"), esc("0072"))),
        ("duplicate keys: the last value wins, a later message resets role and tools",
         '{"type": "a", "type": "b", "message": {"role": "r1", "content": [{"type": "tool_use", "id": "T0"}]}, '
         '"message": {"role": "r2", "content": [{"type": "tool_use", "id": "T1"}]}}'),
        ("a later non-object message drops role and tools",
         '{"message": {"role": "r", "content": [{"type": "tool_use", "id": "T"}]}, "message": "x"}'),
        ("non-string summary fields", '{"type": 5, "uuid": true, "parentUuid": {"a": 1}, "timestamp": [1], "message": {"role": null}}'),
        ("tool blocks of every shape",
         '{"message": {"content": [{"type": "tool_use", "id": 5}, {"type": ["tool_use"], "id": "X"}, '
         '{"type": "tool_use", "type": "text", "id": "Y"}, {"id": "Z", "type": "tool_result", "tool_use_id": "W"}, "str", '
         '[{"type": "tool_use", "id": "nested"}], {"type": "tool_use", "id": "A", "id": "B"}, '
         '{"%sype": "tool_%sse", "%sd": "E1"}]}}' % (esc("0074"), esc("0075"), esc("0069"))),
        ("content that is not a list", '{"message": {"content": {"type": "tool_use", "id": "T"}}}'),
        ("a message that is not an object", '{"type": "user", "message": "hello"}'),
        ("a raw DEL in a string", '{"type": "a\x7fb"}'),
        ("a raw control character in a string", '{"type": "a\x01b"}'),
        ("a raw tab in a string", '{"type": "a\tb"}'),
        ("4,300 digits", "7" * 4300), ("4,301 digits", "7" * 4301), ("minus 4,300 digits", "-" + "7" * 4300),
        ("5,000 digits as a float", "7" * 5000 + ".5"),
        ("past i64", '{"type": "big", "v": [9223372036854775808, -9223372036854775809, 9223372036854775807]}'),
        ("leading zero", "01"), ("trailing dot", "1."), ("leading dot", ".5"), ("bare exponent", "1e"), ("bare minus", "-"),
        ("trailing comma in an object", '{"a": 1,}'), ("trailing comma in an array", "[1,]"),
        ("missing colon", '{"a" 1}'), ("extra data", "{} {}"), ("trailing junk", "{}x"),
        ("whitespace inside", ' { "type" :\t"ws" , "uuid" : null } '),
        ("form feed alone", "\x0c"),
        ("nesting 512 (decodes)", deep(DECODE_MAX_DEPTH)), ("nesting 513 (Rust's decoder refuses)", deep(DECODE_MAX_DEPTH + 1)),
        ("nesting 9,997 (Python's limit)", deep(9997)), ("nesting 9,998 (past it)", deep(9998)),
        ("a 1 MB string", o(type="long", text="x" * (1 << 20))),
    ]
    out = []
    for label, text in lines:
        out.append((label, text.encode("utf-8", "surrogatepass")))
    # Bytes that are not UTF-8: inside strings (kept, as U+FFFD), and outside (refused).
    out.append(("invalid UTF-8 inside strings", b'{"type": "caf\xe9", "uuid": "\xff\xfe", "timestamp": "\xe2\x82"}'))
    out.append(("invalid UTF-8 outside a string", b'{"type": "x"}\xff'))
    out.append(("UTF-8 for a surrogate code point (invalid)", b'{"type": "\xed\xa0\x80"}'))
    return out


def write_edge(root):
    d = os.path.join(root, "edge")
    os.makedirs(os.path.join(d, "transcripts"))
    p = os.path.join(d, "transcripts", "edge.jsonl")
    lines = edge_lines()
    with open(p, "wb") as f:
        for _, b in lines:
            f.write(b + b"\n")
        f.write(b'{"partial": ')                                         # a torn last line: never consumed
    os.utime(p, ns=(T0_NS, T0_NS))
    if os.geteuid() != 0:                                            # a file neither side can open: both print it unreadable
        q = os.path.join(d, "transcripts", "unreadable.jsonl")
        put(q, recs(0, 2), T0_NS)
        os.chmod(q, 0)
    return d, len(lines)


# ───────────────────────────── the identity cases, in lockstep ─────────────────────────────
def rec(i):
    """A transcript-shaped synthetic record; odd ones carry a tool_use, even ones (past 0) its tool_result."""
    if i % 2:
        content = [{"type": "text", "text": "Round %d: adjusting the search limit (naïve → bounded)." % i},
                   {"type": "tool_use", "id": "toolu_%03d" % i, "name": "Edit", "input": {"file_path": "/TESTDIR/notes-api/search.py"}}]
    else:
        content = [{"type": "tool_result", "tool_use_id": "toolu_%03d" % max(0, i - 1), "content": "ok"}]
    r = {"type": "assistant" if i % 2 else "user", "uuid": UUID % i, "parentUuid": UUID % (i - 1) if i else None,
         "timestamp": "2026-06-08T14:%02d:%02dZ" % (i // 60 % 60, i % 60),
         "message": {"role": "assistant" if i % 2 else "user", "content": content}}
    return (json.dumps(r, ensure_ascii=bool(i % 3)) + "\n").encode("utf-8")


def recs(a, b):
    return b"".join(rec(i) for i in range(a, b))


def ns(k, extra=0):
    return T0_NS + k * 10 ** 9 + extra


def put(p, data, t):
    with open(p, "wb") as f:
        f.write(data)
    os.utime(p, ns=(t, t))


def add(p, data, t):
    with open(p, "ab") as f:
        f.write(data)
    os.utime(p, ns=(t, t))


def swap(p, data, t):
    q = p + ".new"
    put(q, data, t)
    os.replace(q, p)


def touch(p, t):
    os.utime(p, ns=(t, t))


def change(data, old, new, where):
    """`data` with the first (where="first") or last occurrence of `old` replaced by `new`, which is the same length."""
    assert len(old) == len(new)
    at = data.find(old) if where == "first" else data.rfind(old)
    assert at >= 0, old
    return data[:at] + new + data[at + len(old):], at


def flip(data, at):
    """`data` with the alphanumeric byte at `at` replaced by another one (the same size, still the same JSON shape)."""
    c = data[at:at + 1]
    assert c.isalnum(), (at, c)
    return data[:at] + (b"Y" if c == b"X" else b"X") + data[at + 1:]


def cases(root):
    """[(case, path, [(step, action, expected kind)])]: each action changes the file once (None: no change). The
    premises a step relies on (a byte inside or outside the 64-byte guard, two mtimes one float apart) are asserted
    where the step is built."""
    P = lambda name: os.path.join(root, name + ".jsonl")              # noqa: E731
    out = []

    a = P("append")
    half = rec(5)[:40]
    out.append(("append and a torn last line", a, [
        ("first read", lambda: put(a, recs(0, 3), ns(1)), "zero"),
        ("unchanged", None, "hit"),
        ("two records appended", lambda: add(a, recs(3, 5), ns(2)), "grown"),
        ("half a line appended", lambda: add(a, half, ns(3)), "grown"),
        ("unchanged with the partial", None, "hit"),
        ("the line completed", lambda: add(a, rec(5)[40:], ns(4)), "grown"),
        ("a refused line and a blank one appended", lambda: add(a, b"not json\n\n", ns(5)), "grown"),
        ("a line ending in a bare CR, no newline yet", lambda: add(a, rec(6).rstrip(b"\n") + b"\r", ns(6)), "grown"),
        ("its newline (the CR piece and a blank piece)", lambda: add(a, b"\n", ns(7)), "grown"),
    ]))

    b = P("touch")
    tb = recs(0, 4) + b'{"partial": "abc'
    out.append(("touch", b, [
        ("first read, a partial at the end", lambda: put(b, tb, ns(1)), "zero"),
        ("mtime moved, bytes kept", lambda: touch(b, ns(2)), "touched"),
        ("a byte of the partial changed, same size", lambda: put(b, tb[:-1] + b"X", ns(3)), "touched"),
        ("the partial completed", lambda: add(b, b'"}\n', ns(4)), "grown"),
    ]))

    c = P("rewrite")
    base = recs(0, 6)
    outside, at1 = change(base, b"toolu_001", b"toolu_00X", "first")
    inside, at2 = change(outside, b"search.py", b"search.pz", "last")
    assert at1 < len(base) - 64 and at2 >= len(base) - 64, "the guard-window premises"
    again, _ = change(inside, b"toolu_003", b"toolu_00Y", "first")
    s3 = T0_NS // 10 ** 9 + 3                                        # CPython's st_mtime is sec + nsec * 1e-9 in doubles
    assert s3 + 50 * 1e-9 == s3 + 0 * 1e-9 and s3 + 1000 * 1e-9 != s3 + 0 * 1e-9, "the one-float premise"
    out.append(("same-size rewrites", c, [
        ("first read", lambda: put(c, base, ns(1)), "zero"),
        ("a byte outside the guard window, new mtime", lambda: put(c, outside, ns(2)), "rewrite"),
        ("a byte inside the guard window, new mtime", lambda: put(c, inside, ns(3)), "guard"),
        ("a byte changed, the mtime kept (served stale)", lambda: put(c, again, ns(3)), "hit"),
        ("the mtime 50 ns later: the same float (served stale)", lambda: touch(c, ns(3, 50)), "hit"),
        ("the mtime 1000 ns later: a new float, the held CRC fails", lambda: touch(c, ns(3, 1000)), "rewrite"),
    ]))

    d = P("shrink")
    out.append(("shrink", d, [
        ("first read, a 10-byte partial at the end", lambda: put(d, recs(0, 4) + b'{"torn": 1', ns(1)), "zero"),
        ("the partial cut to 5 bytes (smaller, still past the held offset)", lambda: put(d, recs(0, 4) + b'{"tor', ns(2)),
         "rewrite"),
        ("the partial dropped (smaller, not below the held offset)", lambda: put(d, recs(0, 4), ns(3)), "rewrite"),
        ("two records dropped", lambda: put(d, recs(0, 2), ns(4)), "shrunk"),
        ("truncated to nothing", lambda: put(d, b"", ns(5)), "shrunk"),
        ("one record appended", lambda: add(d, rec(0), ns(6)), "grown"),
    ]))

    w = P("window")
    wb = recs(0, 6)
    w64, w65 = flip(wb, len(wb) - 64), flip(flip(wb, len(wb) - 64), len(wb) - 65)
    out.append(("the tail guard's edge: the 64 bytes before the held offset", w, [
        ("first read", lambda: put(w, wb, ns(1)), "zero"),
        ("a byte at exactly offset - 64 changed, same size, new mtime", lambda: put(w, w64, ns(2)), "guard"),
        ("a byte at offset - 65 changed, same size, new mtime", lambda: put(w, w65, ns(3)), "rewrite"),
    ]))

    e = P("relocate")
    e2, _ = change(base, b"toolu_001", b"toolu_00X", "first")
    e5, at5 = change(e2 + rec(6), b"toolu_005", b"toolu_00Z", "last")   # inside the last 64 bytes the entry holds
    assert at5 >= len(e2 + rec(6)) - 64, "the guard-window premise"
    out.append(("relocation by rename", e, [
        ("first read", lambda: put(e, base, ns(1)), "zero"),
        ("same size, new inode, new mtime", lambda: swap(e, e2, ns(2)), "rewrite"),
        ("same size, new inode, same mtime (served stale)", lambda: swap(e, base, ns(2)), "hit"),
        ("longer, new inode, guard intact (read as an append)", lambda: swap(e, e2 + rec(6), ns(3)), "grown"),
        ("longer, new inode, a guard byte changed", lambda: swap(e, e5 + rec(7), ns(4)), "guard"),
    ]))

    f = P("prefix")
    f2, _ = change(base, b"toolu_001", b"toolu_00X", "first")
    out.append(("a growth that changed a byte before the guard (Python serves the old prefix)", f, [
        ("first read", lambda: put(f, base, ns(1)), "zero"),
        ("grown, a byte changed before the guard window", lambda: put(f, f2 + rec(6), ns(2)), "grown"),
    ]))

    g = P("gone")

    def to_dir():
        os.remove(g)
        os.mkdir(g)
        touch(g, ns(9))
    steps = [
        ("first read", lambda: put(g, recs(0, 2), ns(1)), "zero"),
        ("removed", lambda: os.remove(g), "absent"),
        ("still absent", None, "absent"),
        ("written again", lambda: put(g, recs(0, 2), ns(1)), "zero"),
    ]
    if os.geteuid() != 0:                                            # root reads a mode-000 file anyway
        steps += [
            ("made unreadable, mtime kept (served from the entry)", lambda: os.chmod(g, 0), "hit"),
            ("unreadable, mtime moved", lambda: touch(g, ns(2)), "failed"),
            ("readable again", lambda: os.chmod(g, 0o644), "zero"),
        ]
    steps.append(("replaced by a directory", to_dir, "failed"))
    out.append(("deletion, permissions and a directory", g, steps))

    h = P("empty")
    out.append(("an empty file", h, [
        ("first read", lambda: put(h, b"", ns(1)), "zero"),
        ("one record appended", lambda: add(h, rec(0), ns(2)), "grown"),
        ("emptied", lambda: put(h, b"", ns(3)), "shrunk"),
    ]))
    return out


class RustServe:
    def __init__(self, binary, plant=None):
        self.p = subprocess.Popen([binary, "store-serve"] + (["--plant", plant] if plant else []),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, path):
        self.p.stdin.write(b"read\t" + path.encode() + b"\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise CheckError("romp-spike store-serve exited")
        return line.decode("utf-8").rstrip("\n")

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=30)


GEN_RE = re.compile(r'^\{"kind": "([a-z]+)", "generation": (\d+|null), ')


class Lockstep:
    """Python's reader and a long-lived `romp-spike store-serve`, read in turn: one `read(path)` gives both answers as
    comparable lines (how the read was served, the generation renumbered per side and path, the whole summary with
    every record in full)."""

    def __init__(self, em, binary, plant=None):
        self.em, self.rs, self.gens = em, RustServe(binary, plant), ({}, {})

    def renumber(self, side, path, raw):
        if raw is None:
            return "null"
        ranks = self.gens[side].setdefault(path, {})
        return str(ranks.setdefault(raw, len(ranks) + 1))

    def read(self, path):
        """(Python's kind, Rust's kind, Python's line, Rust's line)."""
        kind, ent = py_read(self.em, path)
        if ent is None:
            body = '"file": null, "lines": []'
        else:
            body = '"file": %s, "lines": [%s]' % (header(path, ent), ", ".join(py_summary_from_entry(path, ent)))
        py = '{"kind": "%s", "generation": %s, %s}' % (kind, self.renumber(0, path, ent[6] if ent else None), body)
        r = self.rs.read(path)
        m = GEN_RE.match(r)
        if not m:
            raise CheckError("unexpected store-serve line: %r" % r[:120])
        raw = None if m.group(2) == "null" else int(m.group(2))
        r = '{"kind": "%s", "generation": %s, ' % (m.group(1), self.renumber(1, path, raw)) + r[m.end():]
        return kind, m.group(1), py, r

    def close(self):
        self.rs.close()


def run_cases(em, binary, root, plant=None):
    ls = Lockstep(em, binary, plant)
    res = {"plant": plant, "cases": 0, "steps": 0, "mismatches": 0, "kindMisses": 0, "first": [], "kinds": {}}
    try:
        for case, path, steps in cases(root):
            res["cases"] += 1
            for label, act, want in steps:
                if act:
                    act()
                kind, rkind, py, r = ls.read(path)
                res["steps"] += 1
                res["kinds"][kind] = res["kinds"].get(kind, 0) + 1
                if kind != want:
                    res["kindMisses"] += 1
                    res["first"].append("%s / %s: Python read it as %s, the step is built for %s" % (case, label, kind, want))
                if py != r:
                    res["mismatches"] += 1
                    if len(res["first"]) < 6:
                        res["first"].append("%s / %s: Python %s, Rust %s" % (case, label, kind, rkind))
    finally:
        ls.close()
    return res


def py_summary_from_entry(path, ent):
    offs = ent[7]
    return ["".join(record_line(i, offs[2 * i], offs[2 * i + 1], r, True)) for i, r in enumerate(ent[4])]


# ───────────────────────────── the randomized checks (--fuzz) ─────────────────────────────
# Seeded, so a failure replays: `--fuzz N` runs seeds 1..N of both. The fixed corpus and cases above missed a decoder
# refusal under a repeated key and a growth judged against the held offset; these two checks found both.
FUZZ_BYTES = b'"' + bytes([92]) + b"{}[],:0123456789-+eE.ntrufalsNIy \t\r\x0b\x0c\x1c\x00\x7f\xff\xed\xa0"
FUZZ_KEYS = ["type", "uuid", "parentUuid", "timestamp", "message", "role", "content", "id", "tool_use_id", "a"]
FUZZ_WRAPS = [b"", b"", b" ", b"\t", b"\x0b", b"\x0c", b"\x1c", b"\r", b"\x0b\x0c "]


def fuzz_pieces():
    """(values a repeated key may carry, tokens to insert): what the Rust decoder refuses, and ordinary JSON."""
    e = lambda h: BS + "u" + h                                        # noqa: E731
    deep = "[" * 600 + "]" * 600
    values = ['"%s"' % e("d800"), '"x%s"' % e("dc00"), deep, "[%s]" % deep, '{"role": "%s"}' % e("dc00"),
              '{"%s": 1}' % e("d800"), '"ok"', "5", "null", "[1, 2]", '"%s"' % (e("d83d") + e("de00")),
              '{"role": "r", "content": [{"type": "tool_use", "id": "X"}]}', '{"type": "tool_result", "tool_use_id": "W"}']
    tokens = ['"', BS, BS + "u", e("d800"), e("dc00"), e("d83d") + e("de00"), "NaN", "-Infinity", "1e400", "-0", "[", "]",
              "{", "}", ",", ":", " ", '"type": ', '"message": ', "[" * 600, "]" * 600]
    return ([v.encode() for v in values],
            [t.encode() for t in tokens] + [b"\r", b"\x0b", b"\x0c", b"\x1c", b"\x00", b"\x7f", b"\xff", b"\xed\xa0\x80",
                                            "﻿".encode()])


def fuzz_lines(rng, n):
    """`n` lines, each a corpus line or a transcript-shaped record changed one to three times at random."""
    pool = [b for _, b in edge_lines() if len(b) <= 4096] + [rec(i).rstrip(b"\n") for i in range(12)]
    values, tokens = fuzz_pieces()
    plain = [v for v in values if BS.encode() not in v and len(v) < 600] * 2     # a kept value is mostly an ordinary one
    out = []
    for _ in range(n):
        b = bytearray(rng.choice(pool))
        for _ in range(rng.choice((1, 1, 1, 1, 2, 3))):
            op, at = rng.choice((0, 1, 2, 3, 3, 3, 3, 4, 4, 5, 5, 6)), rng.randrange(len(b) + 1)
            if op == 0 and b:                                        # one byte changed
                b[min(at, len(b) - 1)] = rng.choice(FUZZ_BYTES)
            elif op == 1:                                            # a token inserted
                b[at:at] = rng.choice(tokens)
            elif op == 2:                                            # a span deleted
                del b[at:at + rng.randint(1, 8)]
            elif op == 3 and b.find(b"{") >= 0:                      # a member put first: a later repeat replaces it
                i = b.find(b"{") + 1
                b[i:i] = b'"%s": %s, ' % (rng.choice(FUZZ_KEYS).encode(), rng.choice(values))
            elif op == 4 and b.rfind(b"}") >= 0:                     # a member put last: it replaces an earlier one
                i = b.rfind(b"}")
                b[i:i] = b', "%s": %s' % (rng.choice(FUZZ_KEYS).encode(), rng.choice(values + plain))
            elif op == 5:                                            # bytes.strip's edges
                b = bytearray(rng.choice(FUZZ_WRAPS) + bytes(b) + rng.choice(FUZZ_WRAPS))
            else:                                                    # two lines spliced
                other = rng.choice(pool)
                b = b[:at] + other[rng.randrange(len(other) + 1):]
        out.append(bytes(b).replace(b"\n", b" "))
    return out


def run_line_fuzz(em, binary, root, seed, n, plant=None):
    """The store comparison over one file of `fuzz_lines` (seed `seed`), with the edge corpus's expected deviations."""
    d = os.path.join(root, "fuzz-lines-%d-%s" % (seed, plant or "clean"))
    os.makedirs(os.path.join(d, "transcripts"))
    p = os.path.join(d, "transcripts", "fuzz.jsonl")
    with open(p, "wb") as f:
        for b in fuzz_lines(random.Random(seed), n):
            f.write(b + b"\n")
    os.utime(p, ns=(T0_NS, T0_NS))
    try:
        return compare_store_dir(em, binary, d, True, plant)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def fuzz_op(rng, path, clock):
    """One random change to `path`, or none; its label. `clock` is a one-item list, the last mtime in ns: a step moves
    it by a second, a microsecond, 50 ns (the same float) or not at all."""
    def stamp():
        clock[0] += rng.choice((10 ** 9, 10 ** 9, 10 ** 9, 1000, 50, 0))
        return clock[0]
    data = open(path, "rb").read() if os.path.isfile(path) else None
    j = rng.randrange(100)
    op = rng.choice(("append", "append", "partial", "rest", "truncate", "cut", "edit-tail", "edit", "touch", "swap",
                     "remove", "nothing")) if data is not None else rng.choice(("write", "write", "nothing"))
    if op == "write" or (op != "nothing" and data is not None and not data and op.startswith("edit")):
        put(path, recs(j, j + rng.randint(0, 4)), stamp())
    elif op == "append":
        add(path, recs(j, j + rng.randint(1, 3)), stamp())
    elif op == "partial":
        add(path, rec(j)[:rng.randrange(1, len(rec(j)))], stamp())
    elif op == "rest":
        add(path, rec(j)[rng.randrange(len(rec(j))):], stamp())
    elif op == "truncate":
        put(path, data[:rng.randrange(len(data) + 1)], stamp())
    elif op == "cut":                                                # a few bytes off the end: often a partial cut short
        put(path, data[:max(0, len(data) - rng.randint(1, 12))], stamp())
    elif op in ("edit-tail", "edit"):                                # one byte, the same size
        at = max(0, len(data) - rng.randint(1, 130)) if op == "edit-tail" else rng.randrange(len(data))
        put(path, data[:at] + bytes([rng.choice(b"XY0 {}")]) + data[at + 1:], stamp())
    elif op == "touch":
        touch(path, stamp())
    elif op == "swap":                                               # renamed over: the same bytes, or more after them
        swap(path, data + (recs(j, j + 1) if rng.random() < 0.5 else b""), stamp())
    elif op == "remove":
        os.remove(path)
    return op


def run_lockstep_fuzz(em, binary, root, seed, n, plant=None):
    """`n` random steps over two files, both sides reading after each; every line must match."""
    rng = random.Random(seed)
    d = os.path.join(root, "fuzz-steps-%d-%s" % (seed, plant or "clean"))
    os.makedirs(d)
    paths = [os.path.join(d, "f%d.jsonl" % k) for k in range(2)]
    ls, clock = Lockstep(em, binary, plant), [T0_NS]
    res = {"plant": plant, "steps": 0, "mismatches": 0, "first": [], "kinds": {}}
    try:
        for step in range(n):
            path = rng.choice(paths)
            op = fuzz_op(rng, path, clock)
            kind, rkind, py, r = ls.read(path)
            res["steps"] += 1
            res["kinds"][kind] = res["kinds"].get(kind, 0) + 1
            if py != r:
                res["mismatches"] += 1
                if len(res["first"]) < 4:
                    res["first"].append("seed %d step %d (%s on %s): Python %s, Rust %s" % (
                        seed, step, op, os.path.basename(path), kind, rkind))
    finally:
        ls.close()
        shutil.rmtree(d, ignore_errors=True)
    return res


def run_fuzz(em, binary, root, seeds, nlines, nsteps, plants):
    """Both randomized checks over seeds 1..seeds, then (with `plants`) each once more under a planted defect."""
    lines = {"seeds": seeds, "linesPerSeed": nlines, "records": 0, "mismatches": 0, "expected": {}, "first": []}
    steps = {"seeds": seeds, "stepsPerSeed": nsteps, "steps": 0, "mismatches": 0, "kinds": {}, "first": []}
    for seed in range(1, seeds + 1):
        r = run_line_fuzz(em, binary, root, seed, nlines)
        lines["records"] += r["records"]
        lines["mismatches"] += r["mismatches"]
        for k, v in r["expected"].items():
            lines["expected"][k] = lines["expected"].get(k, 0) + v
        lines["first"].extend(("seed %d %s" % (seed, f)) for f in r["first"][:4 - len(lines["first"])])
        r = run_lockstep_fuzz(em, binary, root, seed, nsteps)
        steps["steps"] += r["steps"]
        steps["mismatches"] += r["mismatches"]
        for k, v in r["kinds"].items():
            steps["kinds"][k] = steps["kinds"].get(k, 0) + v
        steps["first"].extend(r["first"][:4 - len(steps["first"])])
    planted = {}
    if plants:
        planted["fuzz lines"] = run_line_fuzz(em, binary, root, 1, nlines, PLANTS["edge"])
        planted["fuzz steps"] = run_lockstep_fuzz(em, binary, root, 1, nsteps, PLANTS["cases"])
    return lines, steps, planted


# ───────────────────────────── main ─────────────────────────────
def say_store(res, what):
    exp = ", ".join("%d %s" % (n, k) for k, n in sorted(res["expected"].items())) or "none"
    print("%s %s: %d files, %d records, %d lines compared, mismatches %d, expected deviations: %s%s" % (
        what, res["dir"], res["files"], res["records"], res["lines"], res["mismatches"], exp,
        " (records in full)" if res["full"] else " (records by CRC-32)"))
    print("    %s; Python summary %.2f s CPU" % (res["rustFooter"], res["pythonCpuS"]))
    for f in res["first"]:
        print("    " + f)


def say_plant(plant, where, n, unit, first):
    print("planted defect %s (%s): %s, %d mismatching %s; first: %s" % (
        plant, where, "caught" if n > 0 else "NOT CAUGHT", n, unit, (first or ["none"])[0][:200]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="append", default=[], help="a dump_world.py output directory (repeatable)")
    ap.add_argument("--full", action="store_true", help="compare the dumps' records in full, not by CRC-32")
    ap.add_argument("--bin", help="a built romp-spike (default: cargo build --release)")
    ap.add_argument("--no-plants", action="store_true", help="skip the planted-defect self-checks")
    ap.add_argument("--fuzz", type=int, default=0, metavar="SEEDS",
                    help="also run the two randomized checks over seeds 1..SEEDS (each seed: --fuzz-lines mutated lines "
                         "through the store comparison, --fuzz-steps random file changes in lockstep)")
    ap.add_argument("--fuzz-lines", type=int, default=30000, metavar="N", help="mutated lines per seed (default 30000)")
    ap.add_argument("--fuzz-steps", type=int, default=200, metavar="N", help="lockstep steps per seed (default 200)")
    ap.add_argument("--json", help="write the results here")
    args = ap.parse_args(argv)
    binary = rust_binary(args.bin)
    root = tempfile.mkdtemp(prefix="romp-s3-check-")
    results, ok = {"dumps": [], "edge": None, "cases": None, "plants": {}, "fuzz": None}, True
    try:
        em = load_event_model(os.path.join(root, "py"))
        for d in args.dump:
            tdir = os.path.join(d, "transcripts")
            full = args.full or sum(os.path.getsize(os.path.join(tdir, n)) for n in os.listdir(tdir)) < 8 << 20
            res = compare_store_dir(em, binary, os.path.realpath(d), full)
            results["dumps"].append(res)
            say_store(res, "dump")
            ok &= res["mismatches"] == 0 and not res["expected"]
        edir, nedge = write_edge(os.path.join(root, "files"))
        res = compare_store_dir(em, binary, edir, True)
        res["corpus"] = nedge
        results["edge"] = res
        say_store(res, "edge corpus (%d lines written)" % nedge)
        ok &= res["mismatches"] == 0
        croot = os.path.join(root, "files", "cases")
        os.makedirs(croot)
        res = run_cases(em, binary, croot)
        results["cases"] = res
        print("identity cases: %d cases, %d steps in lockstep, mismatches %d, steps not read as built %d; kinds %s" % (
            res["cases"], res["steps"], res["mismatches"], res["kindMisses"],
            ", ".join("%s %d" % kv for kv in sorted(res["kinds"].items()))))
        for f in res["first"]:
            print("    " + f)
        ok &= res["mismatches"] == 0 and res["kindMisses"] == 0
        fuzz_planted = {}
        if args.fuzz > 0:
            froot = os.path.join(root, "files", "fuzz")
            os.makedirs(froot)
            t = time.monotonic()
            lines, steps, fuzz_planted = run_fuzz(em, binary, froot, args.fuzz, args.fuzz_lines, args.fuzz_steps,
                                                  not args.no_plants)
            results["fuzz"] = {"lines": lines, "steps": steps, "wallS": round(time.monotonic() - t, 1)}
            exp = ", ".join("%d %s" % (n, k) for k, n in sorted(lines["expected"].items())) or "none"
            print("fuzz lines: %d seeds x %d lines, %d records kept by Python, mismatches %d, expected deviations: %s" % (
                args.fuzz, args.fuzz_lines, lines["records"], lines["mismatches"], exp))
            for f in lines["first"]:
                print("    " + f)
            print("fuzz lockstep: %d seeds x %d steps, mismatches %d; kinds %s" % (
                args.fuzz, args.fuzz_steps, steps["mismatches"], ", ".join("%s %d" % kv for kv in sorted(steps["kinds"].items()))))
            for f in steps["first"]:
                print("    " + f)
            ok &= lines["mismatches"] == 0 and steps["mismatches"] == 0
        if not args.no_plants:
            planted = {}
            if args.dump:
                planted["dump"] = compare_store_dir(em, binary, os.path.realpath(args.dump[0]), False, PLANTS["dump"])
            planted["edge"] = compare_store_dir(em, binary, edir, True, PLANTS["edge"])
            proot = os.path.join(root, "files", "cases-planted")
            os.makedirs(proot)
            planted["cases"] = run_cases(em, binary, proot, PLANTS["cases"])
            planted.update(fuzz_planted)
            results["plants"] = {k: {"plant": v["plant"], "mismatches": v["mismatches"], "first": v["first"][:2]}
                                 for k, v in planted.items()}
            for k, v in planted.items():
                say_plant(v["plant"], k, v["mismatches"], "steps" if k in ("cases", "fuzz steps") else "lines", v["first"])
            ok &= all(v["mismatches"] > 0 for v in planted.values())
        leftovers = [os.path.join(dp, n) for dp, _, fs in os.walk(os.path.join(root, "py")) for n in fs]
        if leftovers:
            print("note: Python's module wrote %d file(s) under its temp roots" % len(leftovers))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    results["ok"] = ok
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=1, sort_keys=True)
    print("check_store: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
