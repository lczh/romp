#!/usr/bin/env python3
"""Python's own answers for layer 1 of the Rust spike's port (rust/notes/s2-design.md section 2, "Text and atom
primitives"; rust/SPIKE.md step S4.1), for the Rust side to match with zero differences.

It loads the real kernel the way dump_world.py's fresh-process replay does (an empty state root, the bench guards) and
calls the real functions: every atom of every tree in a dump through each of the 18 atom functions below, every turn
through em.turn_scalar, and jd._seg_key and kernel._seg_key over every seg-id-shaped string the dump holds (turn ids,
and the stores' node ids, trails, log segs, placements, status keys, lastNode and seams). `romp-spike layer1 DUMP`
computes the same over the typed structs and compares.

    python3 tools/rust-spike/dump_layer1.py DUMP_DIR [DUMP_DIR ...]   # writes DUMP_DIR/layer1.json
    python3 tools/rust-spike/dump_layer1.py --edge-cases OUT.json     # the synthetic cases the Rust unit tests read

The edge cases are synthetic atoms, turns and seg ids built to reach the branches the dumped worlds never do (a bare
string content, whitespace Python counts and Rust's trim does not, the injected-marker and scheduled-preamble regexes,
non-dict blocks, API errors, interrupts, settle replies), plus the code points Python's str.isspace() accepts. A
function that raises records {"raises": <exception name>}; the Rust loader must refuse every such atom. Stdlib only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
SCHEMA = "romp-spike-layer1/1"
EDGE_SCHEMA = "romp-spike-layer1-edge/1"
OUT_NAME = "layer1.json"
TURN_KEYS = ("hT", "pcs")         # the keys the per-card code asks em.turn_scalar for (kernel.py, the anchor walk)


def load_dw():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_world", os.path.join(HERE, "dump_world.py"))
    dw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dw)
    return dw


def load_kernel(dw, parent):
    """The kernel in-process over an empty state root under `parent` (dump_world.replay_main's recipe)."""
    root = os.path.realpath(tempfile.mkdtemp(prefix="layer1-", dir=parent))
    dw.clean_env(root)
    state = os.path.join(root, "state", "romp")
    claude = os.path.join(root, "claude")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(os.path.join(claude, "projects"), exist_ok=True)
    _bcp, _pb, km, rec = dw.load_romp(root, state, claude)
    return root, km, rec


def sha16(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def atom_columns(km):
    """(name, function of one atom) in the order the Rust side computes them. em._content's list is compared through
    the first 16 hex digits of sha1(json.dumps(list, sort_keys=True)), so the dump does not hold every body twice."""
    em, jd = km.em, km.jd
    return [
        ("em._content", lambda a: sha16(json.dumps(em._content(a.get("message")), sort_keys=True))),
        ("em._text_of", lambda a: em._text_of(em._content(a.get("message")))),
        ("em._has_text", em._has_text),
        ("em.atom_has_text", em.atom_has_text),
        ("em._machine_written", em._machine_written),
        ("em._text_hash8", em._text_hash8),
        ("em._stop_reason", em._stop_reason),
        ("em.is_interrupt_record", em.is_interrupt_record),
        ("em._is_opener", em._is_opener),
        ("em._is_segment_input", em._is_segment_input),
        ("em._prose_chars", em._prose_chars),
        ("em.atom_prose_chars", em.atom_prose_chars),
        ("kernel._atom_prose_chars", km._atom_prose_chars),
        ("em.atom_is_bare_end", em.atom_is_bare_end),
        ("em.atom_is_settle", em.atom_is_settle),
        ("em.atom_tool_uses", lambda a: [list(x) for x in em.atom_tool_uses(a)]),
        ("kernel._atom_user_text", km._atom_user_text),
        ("jd._atom_text", jd._atom_text),
    ]


def turn_columns(km):
    return [("em.turn_scalar:%s" % k, (lambda t, _k=k: km.em.turn_scalar(t, _k))) for k in TURN_KEYS]


def seg_columns(km):
    return [("jd._seg_key", km.jd._seg_key), ("kernel._seg_key", km._seg_key)]


def plain(v):
    return json.loads(json.dumps(v))


def seg_inputs(snap, trees):
    """Every seg-id-shaped string in a dump, in a fixed order the Rust side rebuilds: per alive session its turn ids,
    then per store (the sessions' in snapshot order, then the peers') per node its id, trail and log segs, then the
    placements (key, value), the status keys, lastNode and the seams (top, segs)."""
    out = []
    for r in snap["alive"]:
        for turn in (trees.get(r["sid"]) or {}).get("turns") or []:
            out.append(turn.get("id"))
    stores = [s.get("store") for s in snap["sessions"].values()] + [p.get("store") for p in snap["peers"].values()]
    for st in stores:
        if not st:
            continue
        for nid, nd in (st.get("nodes") or {}).items():
            out.append(nid)
            out.extend(nd.get("trail") or [])
            out.extend(r["seg"] for r in nd.get("log") or [] if isinstance(r.get("seg"), str))
        for k, v in (st.get("placements") or {}).items():
            out.extend([k, v])
        out.extend(st.get("status") or {})
        if isinstance(st.get("lastNode"), str):
            out.append(st["lastNode"])
        for sm in st.get("seams") or []:
            if isinstance(sm.get("top"), str):
                out.append(sm["top"])
            out.extend(sm.get("segs") or [])
    return out


def dump_one(dw, km, out):
    snap, trees = dw.load_dump(out)
    acols, tcols, scols = atom_columns(km), turn_columns(km), seg_columns(km)
    sessions = []
    n_atoms = n_turns = 0
    for r in snap["alive"]:
        tree = trees.get(r["sid"])
        rows, trows = [], []
        for turn in (tree or {}).get("turns") or []:
            trows.append([f(turn) for _n, f in tcols])
            for a in turn["atoms"]:
                rows.append([a.get("uuid")] + [f(a) for _n, f in acols])
        n_atoms += len(rows)
        n_turns += len(trows)
        sessions.append({"sid": r["sid"], "atoms": rows, "turns": trows})
    segs = [[s] + [f(s) for _n, f in scols] for s in seg_inputs(snap, trees)]
    doc = {"schema": SCHEMA, "world": snap["world"]["name"], "python": platform.python_version(),
           "atomColumns": ["uuid"] + [n for n, _f in acols], "turnColumns": [n for n, _f in tcols],
           "segColumns": ["input"] + [n for n, _f in scols], "sessions": sessions, "segKeys": segs,
           "counts": {"sessions": len(sessions), "atoms": n_atoms, "turns": n_turns, "segKeys": len(segs)}}
    path = os.path.join(out, OUT_NAME)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return doc["counts"], os.path.getsize(path)


# ───────────────────────────── the edge cases ─────────────────────────────
BOUNDARY = "Recap: limit → 3, the naïve cache is gone; the search suite passes, once again."
INJECT = "<!-- romp-injected -->"
PREAMBLE = "[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT]"
SID = "eeeeeeee-0000-0000-0000-000000000000"


def ua(content, author="human", **kw):
    a = {"type": "user", "uuid": "u-1", "t": 100, "session_id": SID, "message": {"role": "user", "content": content}}
    if author is not None:
        a["author"] = author
    a.update(kw)
    return a


def aa(content, stop="end_turn", model="claude-sonnet-4", **kw):
    msg = {"role": "assistant", "content": content}
    if stop is not None:
        msg["stop_reason"] = stop
    if model is not None:
        msg["model"] = model
    a = {"type": "assistant", "uuid": "a-1", "t": 101, "session_id": SID, "message": msg}
    a.update(kw)
    return a


def tb(text):
    return {"type": "text", "text": text}


def tu(i, name):
    return {"type": "tool_use", "id": i, "name": name, "input": {"command": "ls", "n": 1.5}}


EDGE_ATOMS = [
    ("user text, human", ua([tb("fix the search index")])),
    ("user author sdk", ua([tb("go")], author="sdk")),
    ("user author romp", ua([tb("go")], author="romp")),
    ("user author peer dict", ua([tb("go")], author={"peer": "s002", "host": "TESTHOST"})),
    ("user author peer string", ua([tb("go")], author="peer")),
    ("user author system", ua([tb("<task-notification> done")], author="system")),
    ("user author absent", ua([tb("go")], author=None)),
    ("user author null", dict(ua([tb("go")], author=None), author=None)),
    ("user tool_result only, human", ua([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}])),
    ("assistant end_turn text", aa([tb("Done: the suite passes.")])),
    ("assistant stop_sequence text", aa([tb("x")], stop="stop_sequence")),
    ("assistant tool_use", aa([tb("running"), tu("t1", "Bash")], stop="tool_use")),
    ("assistant two tool uses, one bare", aa([tu("t1", "Bash"), {"type": "tool_use"}], stop="tool_use")),
    ("assistant no stop reason", aa([tb("partial")], stop=None)),
    ("assistant null stop reason", dict(aa([tb("partial")], stop=None), message={
        "role": "assistant", "content": [tb("partial")], "stop_reason": None})),
    ("bare end: empty content", aa([])),
    ("bare end: thinking only", aa([{"type": "thinking", "thinking": "hm", "signature": ""}])),
    ("bare end: whitespace text", aa([tb("  \n\t ")])),
    ("bare end: separator-only text", aa([tb("\x1c\x1d\x1e\x1f")])),
    ("not bare: end_turn with tool use", aa([tu("t1", "Read")])),
    ("not bare: tool_use stop, no text", aa([], stop="tool_use")),
    ("string content, user", ua("  hello  ")),
    ("string content, assistant", aa("  a bare string reply  ")),
    ("string content, whitespace only", ua("  \n ")),
    ("string content, separators only", ua("\x1c \x1f")),
    ("string content, NBSP-wrapped", ua(" hi ")),
    ("content absent", {"type": "user", "uuid": "u-2", "t": 100, "author": "human", "message": {"role": "user"}}),
    ("message absent", {"type": "user", "uuid": "u-3", "t": 100, "author": "human"}),
    ("message null", {"type": "user", "uuid": "u-4", "t": 100, "author": "human", "message": None}),
    ("message empty dict", {"type": "assistant", "uuid": "a-2", "t": 100, "message": {}}),
    ("idle atom", {"type": "idle", "uuid": None, "t": 100, "end": 200, "session_id": SID}),
    ("non-dict blocks skipped", aa(["text", 5, None, [1, 2], tb("kept")])),
    ("text block without text", aa([{"type": "text"}, tb("b")])),
    ("blocks joined then stripped", aa([tb(" a "), tb("b ")])),
    ("blocks with an untyped block", aa([{"text": "no type"}, tb("typed")])),
    ("interrupt record", ua([tb("[Request interrupted by user]")])),
    ("interrupt record for tool use", ua([tb("[Request interrupted by user for tool use]")])),
    ("interrupt record, leading space", ua([tb("   [Request interrupted by user]")])),
    ("interrupt text on an assistant", aa([tb("[Request interrupted by user]")])),
    ("injected marker", ua([tb("please continue " + INJECT)], author="romp")),
    ("injected marker, tight", ua([tb("<!--romp-injected-->")])),
    ("injected marker, unicode spaces", ua([tb("<!--　romp-injected\x1c-->")])),
    ("injected marker, nested opener", ua([tb("<!--<!-- romp-injected -->")])),
    ("injected marker, unclosed", ua([tb("<!-- romp-injected --")])),
    ("injected marker, bare word", ua([tb("the romp-injected marker")])),
    ("injected marker, suffix", ua([tb("<!-- romp-injected-x -->")])),
    ("scheduled preamble", ua([tb("  " + PREAMBLE + " run the report")], author="sdk")),
    ("scheduled preamble, not first", ua([tb("run " + PREAMBLE)], author="sdk")),
    ("scheduled origin", ua([tb("run the report")], author="sdk",
                            origin={"kind": "task-notification", "subkind": "scheduled-trigger"})),
    ("scheduled origin, no text", ua([], author="sdk", origin={"kind": "task-notification",
                                                              "subkind": "scheduled-trigger"})),
    ("origin other kind", ua([tb("x")], origin={"kind": "task-notification", "subkind": "agent"})),
    ("origin a string", ua([tb("x")], origin="task-notification")),
    ("api error", aa([tb("API Error: overloaded")], isApiError=True)),
    ("api error false", aa([tb("fine")], isApiError=False)),
    ("settle reply", aa([tb("No response requested.")])),
    ("settle reply, trailing space", aa([tb("No response requested. ")])),
    ("synthetic model", aa([tb("anything")], model="<synthetic>")),
    ("settle text on a user", ua([tb("No response requested.")])),
    ("boundary atom", aa([tb(BOUNDARY)])),
    ("boundary atom plus one", aa([tb(BOUNDARY + "!")])),
    ("astral and combining", aa([tb("ok \U0001F600 é é")])),
    ("NUL and controls", aa([tb("a\x00b\x01c")])),
    ("zero width space kept", ua([tb("​x​")])),
    ("NEL and line separators", ua([tb("\x85x  ")])),
    ("romp-goal-id text", ua([tb("reply <!-- romp-goal-id: s1:g1 -->")])),
    ("echo text and command", ua([tb("/model")], _echo_text="/model", command="model")),
    ("lazy null", ua([tb("x")], lazy=None)),
    # shapes the Rust loader refuses (its list is in the edge test): Python raises on some, answers on others
    ("REFUSE type absent", {"uuid": "u-9", "t": 100, "message": {"content": [tb("x")]}}),
    ("REFUSE t absent", {"type": "user", "uuid": "u-9", "author": "human", "message": {"content": [tb("x")]}}),
    ("REFUSE content null", ua(None)),
    ("REFUSE content dict", ua({"a": 1})),
    ("REFUSE content number", ua(5)),
    ("REFUSE text null", ua([{"type": "text", "text": None}])),
    ("REFUSE text number", ua([{"type": "text", "text": 5}])),
    ("REFUSE message a string", {"type": "user", "uuid": "u-9", "t": 100, "message": "hello"}),
    ("REFUSE stop reason a number", aa([tb("x")], stop=5)),
    ("REFUSE author a number", ua([tb("x")], author=5)),
    ("REFUSE api error a string", aa([tb("x")], isApiError="yes")),
    ("REFUSE lazy atom", ua([tb("x")], lazy={"h": "abcd1234", "nt": True, "pc": 1})),
    ("REFUSE model a number", aa([tb("x")], model=5)),
]

EDGE_TURNS = [
    ("plain turn", {"atoms": [], "t": 1, "end": 2, "id": "s:1:h", "ended": True, "trigger": None}),
    ("pre true, scalars", {"atoms": [], "t": 1, "end": 2, "pre": True, "hT": 7, "pcs": [3, 4]}),
    ("pre true, float", {"atoms": [], "t": 1, "end": 2, "pre": True, "hT": 7.0, "pcs": 2.5}),
    ("pre true, absent", {"atoms": [], "t": 1, "end": 2, "pre": True}),
    ("pre true, null", {"atoms": [], "t": 1, "end": 2, "pre": True, "hT": None}),
    ("pre 1", {"atoms": [], "t": 1, "end": 2, "pre": 1, "hT": 9}),
    ("pre false", {"atoms": [], "t": 1, "end": 2, "pre": False, "hT": 9}),
    ("pre empty string", {"atoms": [], "t": 1, "end": 2, "pre": "", "hT": 9}),
    ("pre null", {"atoms": [], "t": 1, "end": 2, "pre": None, "hT": 9}),
    ("pre empty list", {"atoms": [], "t": 1, "end": 2, "pre": [], "hT": 9}),
    ("pre dict", {"atoms": [], "t": 1, "end": 2, "pre": {"a": 1}, "pcs": {"b": [1, 2.0]}}),
]

EDGE_SEGS = [None, "", "a", "a:b", "a:b:c", "a:b:c:d", "a::c", ":", "::", ":::", "sid:1781100000:abcd1234",
             "sid:1781100000:abcd1234#d", "sid:1:h#p", "sid:1:h#live", "é:1:ü", "s:g1", "x:y:", ":y:z"]


def edge_main(out):
    dw = load_dw()
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer1-")
    try:
        _root, km, _rec = load_kernel(dw, tmp)
        acols, tcols, scols = atom_columns(km), turn_columns(km), seg_columns(km)

        def run(cols, x):
            row = []
            for _n, f in cols:
                try:
                    row.append(plain(f(x)))
                except Exception as e:                   # noqa: BLE001 (the raise is the answer recorded)
                    row.append({"raises": type(e).__name__})
            return row
        atoms = [{"name": n, "atom": plain(a), "expect": run(acols, plain(a))} for n, a in EDGE_ATOMS]
        turns = [{"name": n, "turn": plain(t), "expect": run(tcols, plain(t))} for n, t in EDGE_TURNS]
        segs = [[s] + run(scols, s) for s in EDGE_SEGS]
        spaces = [c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF and chr(c).isspace()]
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer1.py --edge-cases",
               "atomColumns": [n for n, _f in acols], "turnColumns": [n for n, _f in tcols],
               "segColumns": ["input"] + [n for n, _f in scols],
               "constants": {"END_STOPS": list(km.em.END_STOPS), "CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS,
                             "SETTLE_TEXT_H8": km.em._SETTLE_TEXT_H8},
               "isspace": spaces, "atoms": atoms, "turns": turns, "segKeys": segs}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, indent=1, ensure_ascii=True, allow_nan=False) + "\n")
    raised = sum(1 for c in atoms if any(isinstance(v, dict) and "raises" in v for v in c["expect"]))
    print("edge cases: %d atoms (%d where Python raises), %d turns, %d seg ids, %d isspace code points -> %s"
          % (len(atoms), raised, len(turns), len(segs), len(spaces), out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    a = ap.parse_args(argv)
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT")
    dw = load_dw()
    first = os.path.realpath(a.dumps[0])
    work = os.path.join(first, "work")
    made = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    try:
        root, km, rec = load_kernel(dw, work)
        for d in a.dumps:
            d = os.path.realpath(d)
            if not os.path.isfile(os.path.join(d, "snapshot.json")) or not os.path.exists(os.path.join(d, dw.MARKER)):
                print("dump_layer1: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            counts, size = dump_one(dw, km, d)
            print("%s: %d sessions, %d atoms, %d turns, %d seg ids -> %s (%.1f MB)" % (
                os.path.basename(d), counts["sessions"], counts["atoms"], counts["turns"], counts["segKeys"],
                os.path.join(d, OUT_NAME), size / 1e6))
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer1: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
