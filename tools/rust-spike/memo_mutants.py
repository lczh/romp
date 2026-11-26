#!/usr/bin/env python3
"""Source mutants of the port's memo (step S6.1, rust/crates/romp-views/src/memo.rs and its hooks), for a sweep that
shows the memo's checks catch each one (rust/SPIKE.md, "S6.1 memo port and tuning"). Each mutant is one edit: a search
string that must occur exactly once in its file, and its replacement.

    python3 tools/rust-spike/memo_mutants.py --list                  # the mutants, numbered, and a check that each applies
    python3 tools/rust-spike/memo_mutants.py --apply N --tree DIR    # copy rust/ to DIR/rust and apply mutant N there
                                                                      # (N = the count: the control, unmutated)

The sweep (tools/rust-spike/orcd/mutants.sbatch, one array task per mutant, never on lczh's Mac) applies a mutant to a
scratch copy, builds it under the `mutant` profile (release without LTO, for build time) and runs the memo edge set
(`romp-spike memo tests/fixtures/memo_edge.json`) and one memo fuzz seed against it. A mutant is caught when either
reports a difference. The sweep runs no tests, and a mutant whose build fails or whose binary crashes (the profile
inherits panic = "abort") is recorded under its own status, not as caught, so the table cannot be misread.
Stdlib only; writes nothing in the repo.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MEMO = "rust/crates/romp-views/src/memo.rs"
L6 = "rust/crates/romp-views/src/layer6.rs"

# (name, file, search, replace): each search occurs exactly once in its file
MUTANTS = [
    ("names: the names entry's fields left out", MEMO,
     "tuple_of_strs(b.names.parts(fsid).unwrap_or_default().iter().map(String::as_str)),\n    ]);",
     "KV::None,\n    ]);"),
    ("cleared: the session's cleared slice left out", MEMO,
     "let cleared = bf.cleared(fsid);", "let cleared = KV::None;"),
    ("row: the retry fields left out", MEMO, "        KV::Tuple(retry),\n", "        KV::Tuple(Vec::new()),\n"),
    ("row: the subagents left out", MEMO,
     "KV::Tuple(live_dicts(tm, \"subagents\").map(|a| KV::Tuple(AGENT_FIELDS.iter().map(|k| get_kv(a, k)).collect())).collect()),",
     "KV::Tuple(Vec::new()),"),
    ("ask: never read", MEMO, "Some(st) if layer6::NEEDS_INPUT_STATES.contains(&st) =>", "Some(st) if st.is_empty() && false =>"),
    ("bg: never read", MEMO, "    let bg = if ps_some {", "    let bg = if false {"),
    ("parse: the last turn's end left out", MEMO, "let parse = KV::Tuple(vec![KV::Bool(ps_some), end]);",
     "let parse = KV::Tuple(vec![KV::Bool(ps_some), KV::None]);"),
    ("wait: never read", MEMO, "let wait = sorted_items(b.board.wmap.as_ref(), fsid);", "let wait = KV::None;"),
    ("jauth: never read", MEMO, "let jauth = sorted_items(b.board.jauth.as_ref(), fsid);", "let jauth = KV::None;"),
    ("jactive: never read", MEMO, "let jactive = bf.jactive.contains(fsid);", "let jactive = false;"),
    ("stalls: keyed on no entry's reads", MEMO, "    let stalls = stalls_key(b, prev, plant)?;",
     "    let stalls = stalls_key(b, None, plant)?;"),
    ("nudge: keyed on no entry's reads", MEMO, "    let nudge = nudge_key(b, prev);", "    let nudge = nudge_key(b, None);"),
    ("usage: never gated on the entry's reads", MEMO,
     "let usage = if reads_usage(prev) { bf.usage.clone() } else { KV::None };", "let usage = KV::None;"),
    ("interrupting: left out", MEMO, "KV::Bool(interrupting), KV::Bool(closer), peers,",
     "KV::Bool(false), KV::Bool(closer), peers,"),
    ("closer: left out", MEMO, "KV::Bool(interrupting), KV::Bool(closer), peers,",
     "KV::Bool(interrupting), KV::Bool(false), peers,"),
    ("downtime, debug and auth: left out", MEMO, "bf.auth.clone(),\n        bf.downtime.clone(), bf.debug.clone(),",
     "KV::None,\n        KV::None, KV::None,"),
    ("store: the fault bit left out", MEMO, "KV::Bool(st_none && !hide),", "KV::Bool(false),"),
    ("store: the replay bit ignores replayWrote", MEMO, "} else if sk(\"replayWrote\") == Some(&J::Bool(false)) {",
     "} else if false {"),
    ("peers: a peer's names left out of its facts", MEMO,
     "let f = KV::Tuple(vec![store_id, goal, KV::Bool(false), names, bf.cleared(p),",
     "let f = KV::Tuple(vec![store_id, goal, KV::Bool(false), KV::None, bf.cleared(p),"),
    ("peers: a peer's cleared slice left out of its facts", MEMO,
     "let f = KV::Tuple(vec![store_id, goal, KV::Bool(false), names, bf.cleared(p),",
     "let f = KV::Tuple(vec![store_id, goal, KV::Bool(false), names, KV::None,"),
    ("deps: nudge not re-taken over the new entry", MEMO, "    key[NUDGE] = nudge_key(b, entry);\n", ""),
    ("deps: stalls not re-taken over the new entry", MEMO, "    key[STALLS] = stalls_key(b, entry, plant)?;\n", ""),
    ("deps: usage not re-taken over the new entry", MEMO,
     "    key[USAGE] = if reads_usage(entry) { bf.usage.clone() } else { KV::None };\n", ""),
    ("a miss's entry is not put", MEMO, "                        m.put(fsid, key, entry.clone());",
     "                        let _ = key;"),
    ("the byte bound sheds the most recently served", MEMO,
     "let k = self.map.iter().min_by_key(|(_, e)| e.used)", "let k = self.map.iter().max_by_key(|(_, e)| e.used)"),
    ("a raising session's null entry is not dropped", MEMO, "                        if held.is_some() {\n                            m.drop_entry(fsid);",
     "                        if held.is_some() && false {\n                            m.drop_entry(fsid);"),
    ("the loop goes on past a fold that raises", MEMO, "let stop = got.as_ref().is_ok_and(fold_raises);", "let stop = false;"),
    ("miss labels unsorted", MEMO, "            out.sort_unstable();\n            out\n", "            out\n"),
    ("a miss is attributed to no component", MEMO, ".filter(|(_, (a, b))| a != b)", ".filter(|(_, (a, b))| a != b && false)"),
    ("the held walk's head ignores the inode", L6,
     ".map(|ino| (self.row.transcript.as_deref().unwrap_or(\"\"), ino));",
     ".map(|_ino| (self.row.transcript.as_deref().unwrap_or(\"\"), 0));"),
    ("an entry is served on a hit without a key compare of its hide", MEMO, "KV::Bool(jactive), KV::Bool(hide), watch,",
     "KV::Bool(jactive), KV::Bool(false), watch,"),
    ("the transcript identity left out", MEMO, "let transcript = KV::Tuple(vec![tident, KV::Str(path.to_string())]);",
     "let transcript = KV::Tuple(vec![KV::None, KV::Str(path.to_string())]);"),
    # the recorded-fact reads (S6.1's review: the mutants above cover the computed components, none of the plain lookups)
    ("states: the states logs' identities left out", MEMO,
     '.map(|k| ident(snap, &format!("states:{k}"))).collect());', ".map(|_k| KV::None).collect());"),
    ("captions: the captions file's identity left out", MEMO,
     'let captions = ident(snap, &format!("captions:{fsid}"));', "let captions = KV::None;"),
    ("reg: the registry signature left out", MEMO,
     'let reg = KV::Tuple(vec![sk("reg").map_or(KV::None, KV::from_json),', "let reg = KV::Tuple(vec![KV::None,"),
    ("cut: the pending cut left out", MEMO,
     'let cut = sk("cut").map_or_else(|| KV::Str(String::new()), KV::from_json);', "let cut = KV::Str(String::new());"),
    ("live: the live revision left out", MEMO,
     'let live_rev = sk("live").map_or(KV::Int(0), KV::from_json);', "let live_rev = KV::Int(0);"),
    ("watch: the watch record left out", MEMO, 'let watch = dumps_sorted(sk("watch"));', "let watch = dumps_sorted(None);"),
    ("postal: the postal slice left out", MEMO,
     'let postal = sk("postal").map_or_else(|| KV::Tuple(vec![KV::Tuple(Vec::new()), KV::Tuple(Vec::new())]), KV::from_json);',
     "let postal = KV::Tuple(vec![KV::Tuple(Vec::new()), KV::Tuple(Vec::new())]);"),
    ("anchors: the anchors revision left out", MEMO,
     'let anchors = sk("anchors").map_or(KV::Int(0), KV::from_json);', "let anchors = KV::Int(0);"),
    ("store: the rewind hold left out", MEMO,
     'let hold = match sk("hold").and_then(J::as_object).filter(|m| !m.is_empty()) {',
     "let hold = match None::<&Map<String, J>> {"),
    ("store: the user-write mark left out", MEMO,
     '        sk("userWrite").map_or(KV::Float(0.0), KV::from_json),\n', "        KV::Float(0.0),\n"),
    ("store: the punch record left out", MEMO,
     '        sk("snapDone").map_or(KV::None, KV::from_json),\n', "        KV::None,\n"),
    # the cache's bookkeeping
    ("the LRU: a get is not a use (the entry keeps its age)", MEMO,
     "            e.used = t;\n", "            let _ = (e, t);\n"),
    ("the held aggregate: a departed session's aggregate is not forgotten", MEMO,
     "        h.borrow_mut().forget(&alive);\n", "        let _ = (h, &alive);\n"),
    # the review's must-fix, as a regression mutant
    ("cleared: an id listed twice kept twice (Python's key reads the cleared set)", MEMO,
     "            v.sort_unstable();\n            v.dedup();\n", "            v.sort_unstable();\n"),
]


def read(rel, root=REPO):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def check_all(root=REPO):
    bad = []
    for i, (name, rel, search, _rep) in enumerate(MUTANTS):
        n = read(rel, root).count(search)
        if n != 1:
            bad.append((i, name, n))
    return bad


def apply(n, tree):
    """Copy rust/ to tree/rust and apply mutant n there; n == len(MUTANTS) is the control: the copy, unmutated."""
    dst = os.path.join(tree, "rust")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(REPO, "rust"), dst, ignore=shutil.ignore_patterns("target", "vendor"))
    if n == len(MUTANTS):
        return "control"
    name, rel, search, rep = MUTANTS[n]
    path = os.path.join(tree, rel)
    src = read(rel, tree)
    if src.count(search) != 1:
        raise SystemExit("memo_mutants: mutant %d (%s) does not apply once in %s" % (n, name, rel))
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(search, rep, 1))
    return name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply", type=int)
    ap.add_argument("--tree")
    ap.add_argument("--count", action="store_true", help="print the number of mutants")
    a = ap.parse_args(argv)
    if a.count:
        print(len(MUTANTS))
        return 0
    if a.list:
        bad = check_all()
        for i, (name, rel, _s, _r) in enumerate(MUTANTS):
            print("%2d  %s  (%s)" % (i, name, os.path.basename(rel)))
        print("%d mutants, %d that do not apply exactly once%s" % (len(MUTANTS), len(bad), (": %r" % bad) if bad else ""))
        return 1 if bad else 0
    if a.apply is not None and a.tree:
        name = apply(a.apply, os.path.realpath(a.tree))
        print(json.dumps({"mutant": "control" if name == "control" else a.apply,
                          "name": "the unmutated memo" if name == "control" else name}))
        return 0
    ap.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
