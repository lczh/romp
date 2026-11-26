# The Rust spike: status and step plan

This is the first step of `plans/rust-migration.md`: its one-week benchmark spike, done at a slow pace while the
work is affordable. It ports the transcript store and `build_feed`'s per-card build to a standalone Rust program
with no server. Both implementations run on one synthetic state, and the feed JSON is compared byte for byte.

**Status (keep this current: a session that stops mid-step resumes from here):**

- Step: S6 done on `romp_fix-s6` (worktree `~/romp-romp_fix-s6`): the full alternated measurement ran on fresh dumps
  (8 runs per configuration, both interpreters, CPU on the small, large and large4 worlds, memory cold and after
  appends on small and large), every run raw in `rust/notes/s6-runs.json`; see "S6 results". By the decision rule fixed
  in advance the CPU gate passes, narrowly: Python's heldWarm derivation over the port's held build is 5.24 (5.03 to
  5.51, n=8) on the large world under arm64 3.13.18, with the instructive exception that the 4-session large4 world
  gives 3.11. The steady state does not pass: at production's miss rate (7.7% in this run's `/perf` window), memoized
  Python costs 1.56 to 2.25 ms per build against the memo-less port's 3.07 ms. The memory gate is unclear: the store is
  0.248 of Python's footprint after appends on the synthetic transcripts (a pass as measured), but 0.39 to 0.42 at the
  expansion real transcripts are thought to have (a fail); real-shaped transcripts, which need lczh's OK, decide it.
  What follows in this bullet is the harness as its review left it.
  `tools/rust-spike/measure.py` (Python's replay build by process CPU, five rows round-robin, with its key, memo,
  serialization, stand-ins, frame and file work attributed apart and per session; the record cache's footprint,
  resident and traced bytes, cold and over an append schedule), `romp-spike measure` (the port's build by process CPU
  through a raw clock_gettime extern, seven rows round-robin, among them the held aggregate now ported; the store under
  either growth policy) and `tools/rust-spike/measure_drive.py` (the two alternated, every run kept raw in one JSON
  file; paired ratios, the gate's decision rule, the production blend from two `/perf` snapshots). The CPU gate's
  decision rule is now fixed in writing ("The decision rule" under "S6 method"): Python's heldWarm region against the
  port with the held aggregate, large world, arm64 3.13, median paired ratio over at least 6 runs, pass at 5.0. What
  the review changed: S4.6's informal 2.7x is retracted (it set a 38-session Rust build against a 4-session Python
  derivation); the cheap bound passed (6 to 8x on the smoke runs), so the held aggregate was ported (byte-identical on
  every world, its counters equal Python's per build), and the deciding smoke ratio now sits at the threshold (5.0 and
  5.5 on arm64 3.13, two runs each); the port's prompt index is built on demand as Python's is (built eagerly it cost 8
  to 10 ms a build); the store's growth policy is decided (whole-line blocks, `Growth::Blocks`); and the memory ratio
  measured on the synthetic transcripts (about 0.25) rescales to about 0.42 at real transcripts' expansion, so S6
  proper needs real-shaped transcripts (lczh's OK) before the memory gate can be read.
- Before it: S4.7 (layer 7, the loop, the fold and the cross-session pass) done and merged into `romp_fix` from
  `romp_fix-s47`, with its review round's fixes (two must-fix). With it every port step S4.1 to S4.7 is merged. The whole feed frame comes out of the port: `romp-spike feed DUMP` builds it from
  a dump, and its canonical and wire bytes equal the S2 oracle's on both worlds (17,878 and 114,338 bytes, 0
  differences). The edge set (154 worlds, 118 load and give the feed byte for byte, 36 refused at load as built to be,
  plus 5,797 tint points) and 24,000 random worlds (6,629,814 values, `dump_layer7.py --fuzz`) show 0 differences. 6
  plants and 48 of the first round's 52 source mutants are each caught by the edge set (the other 4 are equivalent), and
  so are the review round's 21. The review's must-fix: Python finds a parked handoff's mail by a path lookup (which drops
  `.` components and trailing slashes, and on APFS ignores letter case) where the port reads `mailNew` as exact pairs, so
  the loader now refuses a parked send whose id or recipient fails kernel._safe_id and the harness refuses a world whose
  `mailNew` this filesystem contradicts; and a float clock past i64 is refused while the postal log holds a parked send
  (the port's `int(now)` saturated). The replay's cleared set is a dict now (both worlds re-dumped byte-identical but for
  `run.json`). A Python-side finding for the bug lane: a null card in an entry's asks (the awaiting placeholder of a
  session that is not live) makes the fold raise outside build_feed's per-session guard, failing the whole build; the
  port mirrors it. See "S4.7 as built" and its review round.
- Earlier: S4.6 (layer 6, the body and its closures) done and merged into `romp_fix` from `romp_fix-s46`, with its
  review's fixes. Every alive session's entry (the dict _feed_session_entry returns, written through romp-json) equals
  Python's real memo entry byte for byte on both worlds (the S2 dump's `oracle/entries/`), on 289 synthetic worlds and
  on 24,000 random ones; 6 plants, 59 of the 60 source mutants (the 60th is equivalent) and the review's 17 more are
  each caught by the edge set. Its review (two must-fix) is under "The review round" in "S4.6 as built": Python's
  `str.upper()` now comes from a table generated from Python (Rust's follows Unicode 18.0 and differed on 76 code
  points), a current ask's non-string question or header now raises as Python does, and its 17 edge-set gaps are
  filled, each shown to catch its mutant. A Python-side finding from it: the body's `_note_peers([handoff_to] ...)`
  never adds a peer (the badge has `peerSid`, not `sid`), a dead call for the bug lane. See "S4.6 as built". S4.6's
  early signal, about 2.7x (the release build's 31 to 33 ms for the large world's 38 entries against the 87 ms of
  s2-design section 4), is retracted as an unfair comparison: the 87 ms came from a 4-session world
  (`rust/notes/probes/replay_probe.py` writes four sessions), not the 38-session one, so the two figures timed
  different worlds (S6's review, must-fix 1; "S6 method").
- Before those: S4.5 (layer 5) done and merged into `romp_fix` from `romp_fix-s45` with its review's fixes. Its review (ship,
  no must-fix) is under "The review round" in "S4.5 as built": its 10 edge-set gaps are filled, each shown to catch its
  mutant, and the memo claim is corrected. A Python-side finding from it: `_SUMMARY_ANCHOR_MEMO`'s key leaves out the
  seams, so after a seam splits a turn a later build can serve a stale summary anchor (reproduced with the real kernel
  functions). It is a candidate romp bug for the bug lane. S3 and S4.1 to S4.4 done, merged into `romp_fix` from `romp_fix-s3`, `romp_fix-s4a`, `romp_fix-s42`, `romp_fix-s43` and
  `romp_fix-s44`, each with its review's fixes. S4.4's review (ship, no must-fix) is under "The
  review round" in "S4.4 as built"; its 11 edge-set gaps are filled, each shown to catch its mutant. S4.3's review
  (ship, no must-fix) found one edge-set gap, now filled and shown to catch its mutant. S4.2's two follow-ups (the
  empty-trigger pin, `dump_layer2.py --fuzz`) were independently verified: 65,000 more random cases on fresh seeds with
  0 differences, and 12 planted mutants all caught.
  - S3: romp-transcript reads both dumps' transcripts with every summary line identical to Python's reader, records
    compared in full, and matches Python's cache read for read on 46 lockstep steps of the identity contract and on
    1,200 random ones (`tools/rust-spike/check_store.py --fuzz 6`). See "S3 as built" below.
  - S4.1: the typed snapshot and trees load and write back through romp-json byte for byte on both worlds, and the 21
    layer-1 functions match Python's own answers with zero differences: 6,370 values on the small world, 2,499,514 on
    the large, plus 1,206 on 67 synthetic edge atoms. See "S4.1 as built" below.
  - S4.2: segmentation (kernel._segs_seam and the six functions under it) matches Python's own answers with zero
    differences on every turn of both worlds (336 values small, 135,016 large), on 100 Python-made edge cases (1,210
    values and 300 counts; 10 more are refused at load, as built to be) and on 60,000 random cases (590,031 values,
    `dump_layer2.py --fuzz`). See "S4.2 as built" below.
  - S4.3: the tree facts and the anchor walk (_session_working, _open_turn_progress, the anchor maps, the plain-prompt
    time, `seg_turn`, and the four anchor functions per segment) match Python's own answers with zero differences on
    both worlds (1,801 values small, 695,672 large), on 129 Python-made edge cases (6,501 values; 7 more are refused at
    load, as built to be) and on 60,000 random sessions and position sets (8,059,862 values, `dump_layer3.py --fuzz`).
    4 planted mistakes and 21 source mutants are each caught by the edge set and by one fuzz seed. See "S4.3 as built".
  - S4.4: the store helpers (29 functions, the body's children build and its subtree walk) match Python's own answers
    with zero differences on both worlds (864 values small, 6,406 large, and every one of the 87 and 597 calls a replay
    build makes), on 225 Python-made edge cases (25,011 values; 15 more are refused at load, as built to be) and on
    60,000 random worlds and calls (30,625,221 values, `dump_layer4.py --fuzz`). 5 planted mistakes and 78 source
    mutants are each caught by the edge set; fuzz seed 1 catches all but two (see "S4.4 as built").
  - S4.5: landing, delegation and provisional (_summary_text_anchor with _opening_sentence and jd._locate_quote,
    _summary_outrun, _dictated_prompt_uuid with jd._human_prompt_record, _pure_delegation_top, _provisional_card with
    its placement, caption, prompt and segment helpers) match Python's own answers with zero differences on both worlds
    (1,735 values small, 624,750 large; every call the replay's body makes, 7 and 41), on 425 Python-made edge cases
    (414 load and match on 14,985 values; the other 11 are refused at load, as built to be) and on 60,000 random
    sessions and calls (9,054,772 values, `dump_layer5.py --fuzz`). Python's regex classes and `re.I` fold come from
    tables generated from its own engine, checked on every code point. 6 planted mistakes and 68 source mutants are each
    caught by the edge set; fuzz seed 1 catches all but seven; 4 more mutants are equivalent (see "S4.5 as built").
  - S4.6: the body and its closures (_feed_session_entry with _note_peers, _subtree, _closure_done, _fsubmax,
    _closure_blocked, _block_check_floor, _brief_landing and flatten, plus _card_warn_rows, _blocked_placeholder and
    _awaiting_card) give every entry byte for byte: both worlds' 4 and 38 entries against `oracle/entries/` (17,470 and
    114,100 bytes), 289 Python-made worlds (305 entries, 31 of them the same raise in both; 21 more worlds are refused
    at load, as built to be) and 24,000 random worlds (40,058 entries, 3,813,717 values, `dump_layer6.py --fuzz`).
    Python's line trace over the edge set runs 585 of the 592 lines; the 7 left cannot run on an admitted input.
    Python's `str.upper()` comes from a table generated from its own engine, checked on every code point.
  - S4.7: the loop, the fold and the cross-session pass (the build_feed loop minus memo and key, the fold with
    cm.age_rgb and ramp, the bells, the serving-fold join, the whoWorking stamp, the parked-handoff and notice cards, the
    frame) give the whole feed byte for byte: both worlds' canonical and wire oracles, 118 Python-made worlds (one build
    raises in both; 36 more worlds are refused at load) and 24,000 random worlds (83,744 cards, 6,629,814 values). The
    tint table checks 5,797 of Python's cm.age_rgb and cm.ramp points. Python's line trace runs 371 of the 414 lines; the
    43 left cannot run on an admitted world in a fresh replay.
  - S2 done before them (byte-identical oracle dumps across seeds and roots, "S2 as built"); S1 before that
    (romp-json matches `json.dumps` on 3,264 golden cases and 499,869 random floats).
- S2 mapping: `rust/notes/s2-design.md`, the raw maps in `rust/notes/s2-maps.json`, the probes in
  `rust/notes/probes/`. The design changed the plan; see "Revisions from the S2 map" below.
- Next action: S7, the report (S5, the serde_json::Value variant, stays in the plan and does not block it): the numbers
  against both gates from "S6 results" and `rust/notes/s6-runs.json`, what replicated and what did not, and the
  recommendation. Before S7 can read the memory gate, one item needs lczh: the OK to measure memory on real-shaped
  transcripts (a read-only temp copy, numbers only, nothing committed, the copy deleted: `measure_drive.py --mem
  --mem-dir DIR`, 8 runs), since the synthetic transcripts inflate Python's cache (S6 method, unfairness item 16) and
  that one number decides the gate (see "What would change it" under "S6 results"). Decisions S7 should put to lczh,
  not take: whether to port the memo (the steady state cannot pass without it), and whether to tune the port before
  reporting (an in-tree FxHash-style hasher for the uuid maps, LTO and one codegen unit; no new crate), which bears on
  the large4 exception. What S6 needed, from the earlier steps (every item met by the harness and the run; kept as the
  record of what it was built to):
  - CPU: Python's `time.process_time()` in a single-threaded process (`time.thread_time()` under-reports about 42x on
    this Mac, s2-design section 4), and Rust's process CPU through a raw `getrusage` or `clock_gettime` extern (no
    crate), each checked against wall time; the two alternated several times on the same load; Python timed in replay
    mode, with what its key computation and frame work cost reported apart, not charged to either side.
  - The memo rows: a build from an empty memo (s2-design's 90 ms, 87 ms of it derivation, is the 4-session large world,
    not the 38-session one: S6's review) and a memo-served build (7.7 ms, no derivation), since the port has no memo;
    either port the memo or fold in production miss rates from `/perf builds.feed.memo`. A warm Python build after a
    seam can serve a stale landing (S4.5's review), so warm runs stamp no seam between builds or clear
    `_SUMMARY_ANCHOR_MEMO` first; with the memo, a hit runs no body and writes nothing to the anchor table.
  - Memory: the store's resident size after appends, not only cold ("What the store holds" under "S3 as built": the
    large world's allocation went from 96.0 MB to 178.7 MB after one appended line per file), against Python's record
    cache under tracemalloc plus its RSS difference, with transcript mtimes near now so the cache keeps its entries.
  - Retracted, kept as a record: S4.6's informal 2.7x (31 to 33 ms of Rust wall time against Python's 87 ms
    derivation) compared the 38-session port build with a 4-session Python derivation. Profile before timing still
    holds: S6's profile found the port building a prompt index per derivation that Python builds only on demand.
  - An asymmetry to report apart (S4.7's review): Python's timed build_feed includes file work the port does not do.
    _parked_handoffs runs fold_records' cursor over the postal log (a stat, and a read when it changed) and a maildir
    stat per candidate, and _notice_cards lists the notice directory and checks its memo per file; the port takes the
    decoded `postalLog`, `mailNew` and `notices` records and does no I/O. Both worlds have no postal log and no notice
    files, so the cost is small, but measure it (time those two calls alone) and name it, rather than charge it to
    either side.
  - Inputs: `python3 tools/rust-spike/dump_world.py --world small` (or `large`), then
    `python3 tools/rust-spike/dump_layer1.py DUMP`, `python3 tools/rust-spike/dump_layer2.py DUMP`,
    `python3 tools/rust-spike/dump_layer3.py DUMP`, `python3 tools/rust-spike/dump_layer4.py DUMP` and
    `python3 tools/rust-spike/dump_layer5.py DUMP`. Layer 6 compares with the dump's own `oracle/entries/`, layer 7 with
    its `oracle/feed.canon.json` and `feed.wire.json`; `python3 tools/rust-spike/dump_layer6.py DUMP` and
    `python3 tools/rust-spike/dump_layer7.py DUMP` check their harnesses against them. Never commit a dump.
  - Checks: `romp-spike roundtrip DUMP`, `romp-spike layer1 DUMP`, `romp-spike layer2 DUMP`, `romp-spike layer3 DUMP`,
    `romp-spike layer4 DUMP`, `romp-spike layer5 DUMP`, `romp-spike layer6 DUMP`, `romp-spike feed DUMP`,
    `romp-spike layer2 rust/crates/romp-views/tests/fixtures/layer2_edge.json`,
    `romp-spike layer3 rust/crates/romp-views/tests/fixtures/layer3_edge.json`,
    `romp-spike layer4 rust/crates/romp-views/tests/fixtures/layer4_edge.json`,
    `romp-spike layer5 rust/crates/romp-views/tests/fixtures/layer5_edge.json`,
    `romp-spike layer6 rust/crates/romp-views/tests/fixtures/layer6_edge.json`,
    `romp-spike feed rust/crates/romp-views/tests/fixtures/layer7_edge.json`,
    `python3 tools/rust-spike/dump_layer2.py --fuzz 10000 --seed S`,
    `python3 tools/rust-spike/dump_layer3.py --fuzz 10000 --seed S`,
    `python3 tools/rust-spike/dump_layer4.py --fuzz 10000 --seed S`,
    `python3 tools/rust-spike/dump_layer5.py --fuzz 10000 --seed S` (seeds 1 to 6),
    `python3 tools/rust-spike/dump_layer6.py --fuzz 4000 --seed S` (seeds 1 to 6),
    `python3 tools/rust-spike/dump_layer7.py --fuzz 4000 --seed S` (seeds 1 to 6), and
    `python3 tools/rust-spike/check_store.py --dump DIR --fuzz 6` (`--dump` repeatable). Build with
    `~/.cargo/bin/cargo build --offline --release -p romp-spike`; the binary is in `rust/target/release/`.
  - Re-run check_store after any change to romp-transcript or to Python's reader. S6 should read "What the store
    holds" under "S3 as built" before measuring: its memory must be measured after appends too, not only cold.
- Priority: Mac bug fixes come first. When one is open, finish it before taking the next spike step. S2 was finished
  while the session-crash fix PR waited on its full test run.
- External crates: lczh approved `serde` and `serde_json` with their dependencies on 2026-11-25. They are pinned
  exactly: serde 1.0.229 and serde_json 1.0.154 in `Cargo.toml`, the rest by `Cargo.lock`, with hashbrown held at
  0.17.1 because 0.17.2 was 3 days old. All 15 are at least 7 days old (`tools/rust-spike/check_crate_ages.py`).
  `serde_json` 1.0.154 uses `zmij` (not `ryu`), and `hashbrown` brings `foldhash`; both are well past a week.
  Any further crate needs lczh's approval and the same check. S4.1 turned on serde_json's `float_roundtrip`
  feature: it adds no crate and leaves `Cargo.lock` unchanged, and without it the parser read 14% of fractional
  times below 2e9 one ULP off (see "S4.1 as built").
- Toolchain: Rust 1.100.0 (2026-11-09, aarch64-apple-darwin) via rustup in `~/.cargo`. Profiles are not
  modified, so call it as `~/.cargo/bin/cargo`. `rust-toolchain.toml` pins the version. Build with `--offline`:
  the crates are already fetched.

## The question the spike answers

Is a compiled port worth a month? The gate comes from the plan. The feed build must be at least **5x cheaper in
CPU** over typed structs, and the transcript store must need at most **a third of Python's resident memory** for the
same files. Passing argues for the month. Missing means the staged fallback: the store goes first behind the Python
kernel, where the memory gain does not depend on interpreter speed.

## Layout

| Path | What |
|---|---|
| `rust/Cargo.toml` | the workspace |
| `rust/crates/romp-json` | JSON written exactly as Python's default `json.dumps` writes it (separators, `\uXXXX` escapes, float repr, insertion order) |
| `rust/crates/romp-transcript` | the transcript store: raw bytes plus a per-line index, the Python cache's identity contract (inode, size, mtime, CRC), positioned reads |
| `rust/crates/romp-views` | the feed's per-card build only (the spike's slice of the views crate) |
| `rust/crates/romp-spike` | the standalone binary: load a snapshot and transcripts, build the feed, print canonical JSON; `measure` times the build and the store (`measure.rs`) |
| `tools/rust-spike/` | the Python side: the world and oracle dumper (`dump_world.py`), each port layer's Python answers (`dump_layer1.py` to `dump_layer7.py`), the measurement (`measure.py`, and `measure_drive.py`, which alternates it with `romp-spike measure`) |

## Steps

Each step lands as its own commit(s) with tests green. The status above is updated at every commit.

- **S0 scaffold.** Workspace, four crates, `rust-toolchain.toml`, pinned `Cargo.lock`, `.gitignore` for
  `target/`. Done when `cargo build` and `cargo test` pass and every locked crate is at least a week old.
- **S1 romp-json.** A Python-compatible writer, with golden tests produced by Python's `json.dumps` over a corpus
  of edge cases: non-ASCII, control characters, floats, large ints, nesting, key order.
- **S2 oracle.** `tools/rust-spike/dump_world.py` builds the synthetic world (`tests/bench_connect_push.write_world`
  plus `tests/bench_chatbuild` transcripts, small and large). It dumps the non-transcript inputs `build_feed` reads
  (goal store, judge runs in flight, judge-auth latch, states logs, live-session map) as one JSON snapshot, and
  Python's feed JSON in canonical key order as the oracle. It also records which per-card code paths the world
  exercises, which scopes S4.
- **S3 romp-transcript.** Incremental reads, the identity contract, and tests against Python's
  `_read_jsonl_incremental` on the same files, including shrink, rewrite and append.
- **S4 romp-views, typed.** Port the per-card derivation that S2 shows the world exercises. Iterate until the
  byte comparison shows zero differences.
- **S5 the serde_json::Value variant.** The same build over Rust's generic JSON tree, which measures the cost of
  porting dict-shaped code as dict-shaped data.
- **S6 measurement** (done: "S6 results"). CPU per build (warm and cold), cold load, and the store's resident
  size. Python's side uses tracemalloc over the record cache plus its RSS difference. Alternate the two several
  times on the same load.
  Warm builds: Python's _SUMMARY_ANCHOR_MEMO survives across builds and its key leaves out the segment's atoms, so a
  warm Python build after a seam is stamped can serve a landing a memo-less port does not compute (S4.5's review,
  "S4.6 as built"). A warm comparison stamps no seam between builds, or clears that memo first.
- **S7 report.** Numbers against the gate, what replicated, what did not, and the recommendation.

## Revisions from the S2 map (2026-11-25)

The mapping (`rust/notes/s2-design.md`) found three things the original plan got wrong or left out.

1. **The per-card build does not read transcripts.** It reads the cached parse tree (`_parse_cached`, kernel.py
   about line 55427). Raw records reach it only through the background-task fold and the API-error tail scan.
   Consequences:
   - S4 takes Python's parse trees as input, dumped per session, plus a small set of recorded helper answers.
     Rebuilding the feed from JSON round-tripped trees, stores and helper answers gave identical bytes
     (`full_replay_equal: true`).
   - S3, the store, becomes an independent measurement of memory and indexing.
   - The records-to-tree parse in `event_model` is Python's real cost on every append, and the original spike
     never measured it. An optional **S4b** ports and measures it, decided after S4.
2. **The card memo decides the steady state.** On the large world (one session of 24,000 records, others of
   3,000), a build from an empty memo takes 90 ms and a memo-served build 7.7 ms. A 5x faster Rust derivation
   (about 17 ms) would lose to Python's memo hits unless the memo is ported or misses are frequent. S6 reports both
   rows and folds in production miss rates from `/perf builds.feed.memo`. The timed region is the same on both
   sides: derivation plus fold.
3. **The CPU clock matters.** On this Mac, `time.thread_time()` under-reports CPU about 42x. S6 uses
   `time.process_time()` in a single-threaded process, and Rust reads process CPU through `getrusage` or
   `clock_gettime` (a raw extern, no crate). The memory run pins transcript mtimes near now, because files pinned
   in the past count as quiescent and Python's record cache drops them.

The determinism recipe that works: freeze the clock, pin mtimes in an explicit order, patch liveness, warm the
parses, run one adoption build, clear the memo, then build again. Two processes with different hash seeds, temp
roots and wall clocks gave byte-identical feeds.

## S2 as built (2026-11-25)

`tools/rust-spike/dump_world.py --world small|large [--out DIR]` writes, under DIR (default
`<system temp>/romp-spike/<world>`, refused inside any git work tree):

| Path | What |
|---|---|
| `snapshot.json` | the design's `romp-spike-snapshot/1`: alive rows in order, live map, board facts, per session its names, flags, store, captions and the recorded helper answers keyed by sid, the peer stores, the frame (plus `frameKeys`, the feed's top-level key order) |
| `trees/<sid>.json` | the parse tree the body reads (`ctx["ps"]`, `em.plain_tree` minus `dir` and `_txKey`), sorted keys |
| `transcripts/`, `states/` | byte copies with their mtimes (S3's input) |
| `oracle/feed.canon.json`, `oracle/feed.wire.json`, `oracle/entries/` | the feed in key order (the gate), in insertion order, and each session's memo entry |
| `coverage/port.json`, `coverage/full.json` | lines run in the 86 listed per-card functions, over the replay build and over the live build |
| `manifest.json`, `run.json` | every file's size and sha256 plus every self-check; `run.json` holds what differs by design (root, seed, timings) |

Self-checks, all in `manifest.json`: a replay in a fresh process over an empty state root that reads only the dump
(canonical, wire and entry bytes equal), with 6 planted input changes that must each move the cards; the guards (no
spawn, write outside the world, thread, warm call, absent session or notice); a census of the designed card shapes;
the boundary atom; a planted card mutation the comparator must catch; JSON values serde_json would read differently;
and no world root, home path, user or machine name in any output. `--check --world both` runs two dumps per world
with different `PYTHONHASHSEED`, roots and wall clocks and compares every file but `run.json`.

Where it departs from `s2-design.md`:
- The host is `TESTHOST` (the repo's synthetic name), not `spike-host`.
- `write_world` stamps the temp root into every record, names entry and registry, so the world is relocated to
  `/TESTDIR/work/<project>` before the kernel loads (records and registries rewritten, project dirs renamed).
  Without it, two roots cannot give identical transcripts or snapshots.
- The world lives under `DIR/work` and is removed after the dump, so everything the tool writes stays in DIR.
- The replay replaces `_feed_session_key` with the ctx facts it hands the body: the tree, the store,
  `_session_working` over the tree, and the recorded `_interrupting` and `_closer_pending` answers under the key's
  gate. This is the design's strongest form. It compares the whole feed, but the frame keys are spliced in from
  the snapshot, so the per-card slice is what is really rebuilt, and `sliceEqual` is the check that counts.
- The coverage list is section 2's list plus the fold's three helpers (`_feed_fold_session`, `_feed_fold_tints`,
  `_feed_fold_bell`). Calls to unlisted functions are counted by whether they ran inside the per-card code.
- `--cold` (optional in the design) is not built.
- Not exercised by either world, so not proven by the replay: the cleared set, the wait graph, stalls, session
  flags, captions with rows, bells, downtime, debug rows and the judge-auth latch. The dump refuses a non-empty
  latch, because its loginLabel resolution is not replayed.
- Two more producers add cards to `asks` outside the per-card build. After the per-session loop, `build_feed`
  appends parked-handoff cards (`_parked_handoffs`, reading `timeline/messages.jsonl`) and notice cards
  (`_notice_cards`, reading the notices dir), at kernel.py about lines 57405-57428. Both are empty in both
  worlds, and neither is in the snapshot or replayed. A world with either would fail loudly, through the
  `allCardsFromTheFold` check and the replay comparison. S4 must still know `asks` has these sources.
- The dumped tree is the session after `_merge_live_atoms`. The body's own `_parse` (in `_provisional_card`) and
  `_parse_cached` (in `_dictated_prompt_uuid`) see the unmerged parse live, while the replay serves the merged
  tree to both. They agree here because neither world has live atoms.

What S4 learns from `coverage/port.json` (the same on both worlds):
- `em.split_segment` is listed but never runs here.
- Five unlisted functions run inside the per-card code and need porting: `em.turn_scalar`,
  `em._is_segment_input`, `kernel._atom_prose_chars`, `kernel._seg_key` and `colormap.stops_for`. Most of the
  other 18 are snapshot lookups (`_names_parts`, `_session_flag`, `_session_flag_raw`) or memo and held-aggregate
  plumbing. One is not: `em.hydrate` fills lazy atoms from disk. It is a no-op here only because the dumper refuses
  lazy atoms, and long production sessions can have them, which matters for S4 and S6.
- The recorded `peerAnswered` is a tuple and the alive rows' `mtime` a float (`1781099940.0`), so the loader needs
  the int/float tag.

## S3 as built (2026-11-25)

`rust/crates/romp-transcript` replaces Python's whole-file read, `_read_jsonl_incremental` over `_JSONL_CACHE`
(kernel/event_model.py). It has five modules:
- `scan.rs` decides which lines are records, the way Python's `_scan_jsonl_stream` does. Only bytes through the last
  `\n` are consumed. Each line is split again at `\r` the way `bytes.splitlines` splits it, stripped the way
  `bytes.strip` strips it (Rust's `trim_ascii` keeps `\x0b`, so the strip is written out), and kept when Python's
  strict `json.loads` would accept it. That means any JSON value counts, along with `NaN`, `Infinity` and lone
  surrogate escapes. Raw control characters in strings, integers over 4,300 digits and nesting past 9,997 are
  refused. The same pass captures the summary fields as byte spans, last duplicate key winning, with no record built.
- `store.rs` holds the identity contract, step for step with `_read_jsonl_entry_unlocked`, using positioned reads
  (`FileExt::read_at`, no memory map).
- `decode.rs` decodes a held record on demand into `romp_json::Value` with Python's dict semantics.
- `crc32.rs` is zlib's CRC-32, written by hand.
- `canon.rs` writes the comparison text.

`romp-spike store DUMP` prints the canonical summary of a dump's `transcripts/`. `romp-spike store-serve` keeps one
store across stdin requests for the lockstep cases. `tools/rust-spike/check_store.py` builds the same summary
through Python's reader and compares it line for line. It covers both dumps, an edge corpus of 69 lines plus an
unreadable file, and 9 identity cases of 46 steps, each step stating the read kind it is built to produce. `--fuzz N`
adds two seeded randomized checks: mutated lines through the same comparison, and random file changes in lockstep.
They found the two defects the review reported, which the fixed corpus and cases had missed. It plants one defect per
comparison (`--plant`) and shows each one caught; a plant that moves nothing prints `NOT CAUGHT` and fails the run.

Python behaviors kept on purpose (parity first: the plan defers anything that changes what the user sees). Each one
serves stale data in a case Python serves stale data, and each is pinned by a Rust test and a lockstep step:
- Same size and same mtime is a hit, with neither the inode nor the bytes looked at. So a same-size rewrite that keeps
  its mtime is served stale, and so is a copy renamed over the path with its mtime preserved.
- The mtime is compared as CPython's float (`sec + nsec * 1e-9`), so two mtimes within half a float step (about
  0.12 microseconds near 1.78e9 s) count as one mtime.
- On growth, only the 64 bytes before the held offset are checked, and the inode is not. A change earlier in the
  file, or a longer file renamed over the path with the same guard bytes, is read as an append over the old prefix.
  The window's edge is pinned: a same-size change at offset - 64 is a guard failure, at offset - 65 a rewrite.
- The held-byte CRC covers `[0, offset)` only. So a same-size change confined to an unconsumed partial line passes as
  a touch, and the partial is rescanned anyway.
- A file that shrank only by its torn tail (not below the held offset) is read from zero under Python's name
  "rewrite". That includes a partial cut short that still runs past the held offset, since growth is judged against
  the size the entry saw, not its offset. A `chmod` moves only the ctime, so an unreadable file whose mtime and size stand is still served from its
  entry.

Where it departs from Python:
- The decoder refuses two kinds of record, by design and loudly. One is a record whose final value holds a lone
  surrogate, in a string or a key, which a Rust `String` cannot carry. The other is one whose final value nests past
  512 levels, since the decoder is recursive. The final value is the record Python builds. A value under a repeated
  key that a later value replaces is not part of it, so it never makes a record undecodable. The decoder marks such a
  value, skips a deep one with an iterative pass that still checks its syntax, and refuses only when a mark survives to
  the root. The index still keeps both kinds of record, and their summary fields print exactly, because strings are
  carried as UTF-16 units. The comparison names these lines as expected and checks that Python's record really has the
  property: 3 lone-surrogate and 3 deep lines in the corpus, none in either world. Four more corpus lines put the
  offending value under a replaced key, and they decode in full. Until the review's fix the decoder refused at the
  first offending value, even when a later repeat replaced it. Real transcripts never repeat keys.
- The tail guard is compared with the bytes the store holds. Python compares with a 64-byte copy that it re-reads
  from the file after each scan. The two are identical unless the file changes mid-read.
- Python's nesting limit moves with the C stack depth at the call. It measured 9,997 at top level and through the
  reader from `check_store.py`.
- A line of 4 GiB or more fails the read, because the index keeps 32-bit line lengths.
- Not ported: the checkpoint's tail entries and their restore, the LRU count cap and byte budget, the quiescent
  drop, fold cursors, the read stripes, and Python's byte counters by caller (Python also re-reads its tail copy
  after every read, so byte counts differ by design).

What the store holds, for S6 (counted, not measured):
- Per file: the raw bytes `[0, offset)`, blank and refused lines included, plus a 64-byte `LineIndex` per record
  (offset, length, kind, five 8-byte field spans, the tool-id range) and 8 bytes per tool id. Nothing decoded is
  kept.
- Python's entry holds every record decoded, plus 16 bytes of offsets per record and a 64-byte tail copy.
- On the large world: 82.7 MB of transcripts gives 82.7 MB held plus a 9.45 MB index (70 bytes per record over
  135,005 records). That is 96.0 MB allocated with the index vectors' growth slack, 1.16 bytes per file byte.
- Projected against Python's published figures for its record cache, that is about 0.41 of the decoded heap (2.86
  bytes per file byte, `_scan_jsonl_stream`'s lab measurement) and about 0.37 of the lab's resident growth (3.1
  bytes per file byte, streamed, tracemalloc off). Both are above the gate's one third.
- That projection is for a cold read only. Appends grow it. After one line was appended to each file of the large
  world, allocation went from 96.0 MB (1.16 bytes per held byte) to 178.7 MB (2.16). `Entry::extend` appends with
  `extend_from_slice`, which doubles the buffer the first read had shrunk to fit. This was reproduced with a scratch
  example program, not committed. Resident memory may grow less, since capacity never written may never become
  resident. Still, S6 must measure after appends, not only a cold index. A chunked buffer (fixed-size blocks, no copy
  on growth) or an exact reserve (a copy on every append) would bound it; S6's numbers decide which. (S6 decided:
  whole-line blocks, `Growth::Blocks`, now the store's default; S3's buffer is `Growth::Double`. See "The store's
  growth policy" under "S6 method": under S3's append Double's footprint grew 69 MB, the doubled buffers, all live;
  Blocks' 10 MB, freed index buffers the allocator keeps, with no live growth.)
- So the plan's raw-bytes design may miss the memory gate as built. The cheap moves are `shrink_to_fit` on the index
  and, much larger, holding only the index (0.11 bytes per file byte here) and fetching a record's bytes by a
  positioned read after an identity check. S6 measures it; this projection is not a result.
- Not a timing either: the release build indexed the large world in about 0.2 to 0.3 s of wall time.

## S4.1 as built (2026-11-25)

**The typed loader** (`rust/crates/romp-views/src/`: `record.rs`, `tree.rs`, `snapshot.rs`, `load.rs`). The problem
it has to solve is a load that loses nothing the build reads, and a way to prove that. The proof is a round trip:
load `snapshot.json` and every `trees/<sid>.json` into typed structs, write them back through romp-json, and compare
with the files byte for byte (`romp-spike roundtrip DUMP`).
- `record!` declares each struct: typed fields for the keys the per-card code reads (an ast scan of the listed
  functions, grouped by receiver), the other keys kept untyped, and one byte per key recording the order the keys
  came in. The order matters because a store's key order depends on its history: s004's store, never planned, has
  `placementsV` last, where the planned stores have it third, so fixed field order could not reproduce the file.
- `Num` keeps Python's int/float tag (the alive rows' `mtime` is `1781099940.0`, `peerAnswered` a tuple of times).
  Absent and null stay different (`Option<Option<T>>`), as `d.get(k, default)` needs.
- Refused at load, each with the path of the refused part: a lazy atom (em.hydrate is not ported), a restored
  pre-cut turn (`pre`, its stored spans), a content that is null, a dict or a number, a text that is not a string, a
  missing `type` or `t` on an atom, another snapshot schema, an alive row without facts, and any key the per-card
  code reads that the structs do not type yet: 26 on nodes (`NODE_UNPORTED`: `born`, `followupPending`,
  `summaryAnchor`, ...), 2 on handoffs and origins, 5 on log rows, `segs` on turns. None is present in either world.
- Result: snapshot (23,075 and 111,574 bytes) and every tree (4 of 4, 192,756 bytes; 38 of 38, 79,601,651 bytes)
  identical on the small and large worlds. Planted: `--plant float-tag` writes the mtime as `1781099940` and one
  atom's `t` as a float, and the comparison names both bytes (exit 1).
- A finding: serde_json's default float parser reads some of Python's float reprs one ULP off
  (`-1.5432835417340557e+88` came back `...556e+88`). Of random bit patterns, 59,330 of 199,913 came back wrong; of
  times drawn uniformly below 2e9 (the shape of a fractional mtime), 14,434 of 100,000; of `random()` values, 10,421
  of 100,000. The integral floats in these worlds survive it, so the round trip alone would not have shown it. The
  workspace now enables serde_json's `float_roundtrip` feature, after which all three sets come back exact;
  `record::tests::floats_parse_as_python_wrote_them` fails without it.

**Layer 1** (`layer1.rs`, `pystr.rs`, `sha1.rs`). The 16 functions section 2 lists (em._content, _text_of, _has_text,
_machine_written, _text_hash8, _stop_reason, is_interrupt_record, _is_opener, atom_has_text, atom_prose_chars,
_prose_chars, atom_is_bare_end, atom_is_settle; jd._seg_key, jd._atom_text; kernel._atom_user_text), the four
unlisted ones `coverage/port.json` shows running in the per-card code (em.turn_scalar, em._is_segment_input,
kernel._atom_prose_chars, kernel._seg_key; the fifth, colormap.stops_for, is the fold's tint, layer 7), and
em.atom_tool_uses, which atom_is_bare_end calls. em.hydrate is the one layer-1 function not ported: the loader
refuses the lazy atoms it would fill. SHA-1 is written by hand (FIPS vectors and Python's digests across the padding
boundaries), and the two regexes (ROMP_INJECT_RE, SCHEDULED_PREAMBLE_RE) are matched by hand.
- The oracle: `tools/rust-spike/dump_layer1.py DUMP` loads the real kernel the way the fresh-process replay does and
  calls the real functions on every atom (18 atom columns), every turn (turn_scalar for `hT` and `pcs`) and every
  seg-id-shaped string in the dump (turn ids, node ids, trails, log segs, placements, status keys, lastNode, seams),
  writing `DUMP/layer1.json` (0.1 MB small, 36.1 MB large). `romp-spike layer1 DUMP` computes the same over the
  typed structs, checks the atoms line up by uuid and the seg-id inputs are the same list, and compares.
- Result: small, 329 atoms, 42 turns, 182 seg ids, 6,370 values; large, 135,043 atoms, 16,877 turns, 17,493 seg ids,
  2,499,514 values; 0 differences on both. Planted: `--plant bytes` counts atom_prose_chars in UTF-8 bytes, and the
  comparison reports exactly one difference on each world, the boundary atom (s001 atom 32,
  `cccccccc-0001-0000-0000-000000000033`: Rust 82, Python 79).
- Why the dumps alone are not enough: in both worlds em._machine_written, is_interrupt_record, atom_is_bare_end and
  atom_is_settle are false on every atom, turn_scalar is None on every turn, and the boundary atom is the only
  non-ASCII prose atom. The other sides come from the edge cases: `dump_layer1.py --edge-cases` writes
  `rust/crates/romp-views/tests/fixtures/layer1_edge.json` (synthetic, regenerates byte-identical), and
  `tests/layer1_edge.rs` checks 80 atoms against it: 67 load and match Python on all 18 functions (1,206 values), 13
  are refused, and those 13 include all 6 on which some Python function raises. It also checks 11 turns, 18 seg
  ids, and Python's whitespace on every code point.
- Three semantics a naive port would get wrong, each pinned by a test: Python's `strip()` and `\s` treat U+001C to
  U+001F as whitespace and Rust's `trim` does not (the only 4 of Python's 29 whitespace code points Rust misses);
  `len` counts code points (79 against 82 bytes); and a bare-string content reads three ways (em._content makes it a
  text block, em._prose_chars counts 0, jd._atom_text iterates its characters and finds "").

## S4.2 as built (2026-11-26)

**The problem.** A card is built per segment: a turn is cut at each new prompt, and when a goal settles while its
segment is still growing, the rest of that segment is split off as its own "seam" segment, so the later work is not
filed under the finished goal. Every card keys its anchors on these segments' ids. A port that cuts a turn one atom
differently, or prints a time differently inside an id, moves cards. Layer 2 is that cutting: `layer2.rs` ports
kernel._segs_seam and what it calls (kernel._turn_sans_placed_echoes, em.segments with em._segment_id, jd.apply_seams,
em.split_segment with em._seam_real_work), line for line over the typed tree and store.
- A `Segment` borrows its atoms (`Vec<&Atom>`): em.split_segment filters a segment's atoms by time, so a piece is a
  list of references, not a slice.
- Times keep the int/float tag and compare the way Python compares them (`Num::py_cmp`). An int and a float compare
  exactly, so 2**53 + 1 is greater than 2.0**53, where a conversion to f64 would call them equal. An id prints its
  time with `"%d"`, which truncates a float toward zero (-0.5 prints 0). It does so exactly at any size: past 2**63
  `Num::percent_d` expands the float by hand in base-1e9 limbs.
- `min(hits, key=lambda s: s["t"])` keeps the first of equal times. It also runs its key on every hit, so a hit seam
  with no `t` raises KeyError, even though the hit test read the absent `t` as 0.
- `seamOf.text` is `sm.get("text", "")`: an absent text gives "", a null one stays null.
- The seam floor (SEAM_PROSE_FLOOR, 80) counts code points of the stripped, joined text (em._text_of). It does not
  count bytes, and it is not layer 1's unstripped em._prose_chars. An API-error atom counts like any other.
- Python raises KeyError on four inputs the loader accepts: no `session_id` on a turn's first atom, no `trigger` on a
  turn without an input atom, a trigger dict without `uuid`, and a hit seam without `t`. The port returns
  `Err(Raise)` carrying the exception's name and `str(e)`, and the comparison matches both.
- The restored pre-cut branch of em.segments stays refused at load (`tree::validate_turn`, factored out of
  `tree::validate` with the same messages). The loader now admits three shapes Python reads without error: a null
  entry in `placedEchoes` (it drops the atoms that have no uuid), a null `seams` and a null `segs` (both skip). The
  round trip and layer 1 still match on both worlds.

**The oracle.** `tools/rust-spike/dump_layer2.py DUMP` loads the real kernel as dump_layer1.py does and calls
kernel._segs_seam(turn, store) on every turn, with that session's store from `snapshot.json`. It writes
`DUMP/layer2.json` (0.02 MB small, 8.9 MB large). Each segment is Python's dict, keys in their order, with `atoms`
written as uuids and `atomIdx` added: each atom's index in its turn. Idle atoms have null uuids, so the uuids alone
cannot prove which atoms a piece holds. Through wrappers on jd._seg_key and em.split_segment it also counts the pieces
apply_seams examined, the splits it tried, and the splits made. `romp-spike layer2 DUMP` computes the same over the
typed structs. It compares field by field: the key order, then each key as romp-json writes it, so an int/float tag
or a negative zero counts. It also compares the counts.

**Result on the worlds: zero differences.**
- Small: 4 sessions, 42 turns, 42 segments, 336 values. Large: 38 sessions, 16,877 turns, 16,877 segments, 135,016
  values (74 ms). The counts match Python's.
- Why that is not enough. In both worlds every turn has exactly one input atom (so one segment per turn), every time
  is an int, and no turn has leading atoms or placed echoes. The one seam (s001's settled g1) lists 4 segment keys,
  and none of those segments' spans holds its time. So apply_seams examined 10 pieces on the small world and 3,000 on
  the large, and never called em.split_segment. Nothing in the worlds tests the split, the floor or a float id.

**The edge set.** `dump_layer2.py --edge-cases` writes `rust/crates/romp-views/tests/fixtures/layer2_edge.json`
(synthetic; it regenerates byte-identical). Its 110 cases take their seam keys from the real em.segments, so a seam
names the segment it is meant to hit. They cover:
- seams: a straddling split; the strict bounds (a seam at the segment's `t` or `end` does not hit); an atom at the seam
  time staying in the head; equal int and float times both ways; an int past 2**53 against a float; equal seam times
  (the first listed wins) and an earlier seam listed second; absent and null seam text and top; recursive splits two
  and three levels deep; a second seam inside the head (it re-hits the first seam, and that split finds no tail);
  empty heads and empty tails; seams, stores and `segs` that are null, empty or absent;
- the floor: 79, 80 and 81 code points of non-ASCII prose (88, 89 and 93 bytes), 79 and 80 ASCII, 79 after the strip,
  80 across two joined blocks, 80 in a bare-string content, an API error, a user atom, a tool use without id or name;
- em.segments: every trigger shape, leading atoms, inputs of every author, absent and null `session_id`, text-less and
  machine-written anchors, placed echoes, and `%d` of fractional, negative and huge float times (1e20,
  1.7976931348623157e308). One case pins the empty trigger: Python's `if trigger_uuid:` skips the anchor search
  when the turn's trigger uuid is "", so a turn with no input, trigger `{"uuid": ""}` and atoms a1 ("x") and one
  whose uuid is "" ("y") gets an id ending in sha1("x"). A port that searched for "" would anchor on the second atom
  and hash "y". `layer2.rs` skips the search the same way (`.filter(|u| !u.is_empty())` in `segment_id`); with that
  filter removed, this case is the fixture's one difference (the fixture before it had none);
- five direct em.split_segment calls.

Result: 100 cases load and match on every value (1,210) and every per-case count (300), 0 differences. The other 10
are refused at load, and they are exactly the cases named REFUSE. Python raises on 8: the 5 KeyErrors match the same
exception here, and the 3 TypeErrors are on refused shapes. Over the loaded cases the port tried 89 splits and made 38.
Refused at load where Python answers, each checked against Python by the review; none is in either world:
- a truthy `pre` with or without stored `segs`, and stored `segs` on a plain turn;
- a seam that is not a dict, a seam text or key that is not a string, a seam `top` that is not a string (Python copies
  any value into `seamOf.top`), and a string `segs` on a seam (Python matches it as a substring and really splits);
- a `placedEchoes` that is a string or a dict, or that holds an int (Python makes a set of it);
- a bool time on an atom or a seam, and an int trigger uuid;
- a turn with no `t` whose first atom is an input (Python never reads `turn["t"]` there).

**Planted mistakes, each caught.** `--plant end` ends each segment at its own last atom. It is caught on the small
world (4 differences), the large (38) and the edge set (97). On the worlds it moves only the one turn per session that
ends in an idle span: that turn's end is the idle span's end (the build clock), while every other turn, the open one
included, ends at its last atom's time. `--plant bytes` counts the
floor in UTF-8 bytes. The worlds cannot see it, since no split is ever tried there. The edge set catches it on exactly
its two 79-code-point tails, "tail prose 79 code points, 88 bytes" and "tail prose 79 after strip (83 before)", and
on their counts.

Tests: 8 new unit tests (6 in `layer2.rs`, 2 in `record.rs` for `py_cmp` and `percent_d`, with Python's digits for
1e20, 2**63 and f64::MAX), `tests/layer2_edge.rs` (5 tests over the fixture, both plants among them) and 1 in
romp-spike for the comparison's messages. The workspace's 86 pass.

**The randomized check** (added after the review). The edge set holds only the cases someone thought of, so the
review asked for random ones too. `python3 tools/rust-spike/dump_layer2.py --fuzz N --seed S` makes N random turns
and stores, answers each with the real kernel._segs_seam (one case in twelve: em.split_segment on the first segment),
writes them in the edge-set format to a temporary file and runs `romp-spike layer2` on it. Any difference, or any
case the loader refuses, fails it. The same seed makes the same file byte for byte, so a failure replays.
- What it draws: atoms of every kind, uuids that repeat, are "" or null or absent; texts with non-ASCII (2, 3 and
  4 UTF-8 bytes, a combining accent), whitespace Python strips (U+001C to U+001F, which Rust's trim keeps, and
  U+0085, U+00A0, U+3000), the romp-injected marker, the scheduled preamble and the interrupt record (and near
  misses), and prose lengths clustered at the 80-code-point floor; content as blocks, a bare string, absent; int
  and float times with ties, the other tag of an equal time, -0.0, times just under 2**53 and past 2**62, floats
  past 2**63 up to f64::MAX, and out-of-order atoms; every trigger shape; placed echoes (null entries among them);
  absent, null and empty stores and seams. Seam keys and times come from the turn's own em.segments and the tails
  em.split_segment cuts from them, up to three levels down, so seams hit, straddle a span, sit on its strict
  bounds, re-split their tails and tie. Seam text and top are absent, null or a string.
- Result, seeds 1 to 6 at 10,000 cases each (18 s for all six): 60,000 cases (54,922 kernel._segs_seam, 5,078
  direct splits), 590,031 values and 180,000 counts compared, **0 differences**, 0 refused. Python raised on 4,712
  (KeyError 'uuid' 1,867, 'trigger' 1,626, 'session_id' 1,104, 't' 115), each matched as the same exception. The
  port examined 66,522 pieces, tried 24,931 splits and made 5,972; 5,449 cases split and 481 split more than once;
  939 seam tails carry the text "" (an absent or empty text) and 391 a null one; 1,346 segments have the trigger "" and
  a later atom whose uuid is "", where a search for "" would choose another anchor.
- Each mistake below is caught by seed 1 alone (10,000 cases). The plants are romp-spike's own (`--plant end` and
  `--plant bytes`, passed through by `dump_layer2.py --fuzz ... --plant NAME`, which then passes only when the plant
  is caught). The others were edits to `layer2.rs`, each built, run and reverted. The fixture columns show where the
  edge set alone also catches it; the empty-trigger mutant is the one the fixture before this step missed.

| Mistake | Fixture before (109 cases) | Fixture now (110) | Fuzz, seed 1 |
|---|---|---|---|
| `--plant end` (a segment ends at its last atom) | 97 | 97 | 5,197 |
| `--plant bytes` (the floor in UTF-8 bytes) | 8 | 8 | 216 |
| a trigger uuid of "" is searched for | 0 | 1 | 153 |
| an anchor uuid of "" is the hash basis | 1 | 1 | 225 |
| the last of equal seam times wins | 2 | 2 | 225 |
| an absent seam text reads as null | 1 | 1 | 97 |
| a seam at the piece's end hits | 1 | 1 | 1,005 |
| the split compares times as f64 | 4 | 4 | 116 |
| the id prints its time through f64 as i64 | 5 | 5 | 3,666 |
| a hit seam without `t` reads as 0, no KeyError | 8 | 8 | 48 |
| an absent session_id in a split tail's id reads as None | 1 | 1 | 3 |
| a null placedEchoes entry drops nothing | 2 | 2 | 715 |
| an API-error atom is not real work | 4 | 4 | 58 |
| leading atoms keep the input's `t` | 7 | 7 | 5,037 |

The weakest catch is the absent session_id (3 of 10,000): it needs a split of a segment whose first atom has no
`session_id` (2% of atoms), and not the turn's first segment, where em.segments raises KeyError before any split.

## S4.3 as built (2026-11-26)

**The problem.** Every card carries links into the chat: the title lands on the prompt that asked for the work, the
summary on the reply that closed it, and a working card shows how long its turn has run and how many tools it called.
Layer 3 computes the facts those links and badges come from. Per session: whether it is working (its last turn is open
and the laptop has not slept since its last activity), the open turn's start and tool count, and the latest time the
user typed a plain prompt. Per segment: four anchors (the reply, the first atom the chat can land on, the prompt atom,
the latest substantial prose), folded into maps keyed by a segment key that ignores the time. A port can match Python
on every ordinary session and still go wrong where two segments share a key, where segmenting a turn fails partway,
where two equal times differ only in being an int or a float, or where an id is the empty string. The worlds have none
of those.

`layer3.rs` ports kernel._session_working with _suspended_after, _open_turn_progress, _last_plain_user_turn_t,
_seg_anchors, _seg_jump (unlisted; the fold calls it when a segment has no reply), jd._prompt_anchor_uuid,
_seg_last_text, _feed_seg_fold, _feed_seg_walk, _feed_seg_plain_t and _FeedSegTurns.get, line for line. The held
aggregate (_FEED_SEG_HELD and its keys) is left out, so the walk folds every turn. What the notes flagged, as built:
- The maps are Python dicts. Two prompts with the same text in two turns share a key, and the later segment overwrites
  the earlier one's values but keeps its place. The four maps are always written together, so `SegMaps` holds one
  insertion-ordered map (`PyDict`, overwrite in place) of a struct with the four values, plus the citable set.
- A segmentation raise (layer 2's `Err(Raise)`) stops the walk: `ok` is false and the maps hold every turn before the
  raising one. The plain-prompt time is still read over every turn, since it does not segment. `seg_turn.get` answers
  None and counts a fault when its turn raises, or when the position names a turn past the end (Python's IndexError is
  inside the same try). Nothing panics.
- `max(best, x)` keeps the first of equal values, so the plain-prompt time is 5 after 5 then 5.0, and 5.0 after 5.0
  then 5. `a.get("t") or turn.get("t") or 0` skips a zero time. `_feed_seg_plain_t`'s two steps (closed turns, then
  the open one) are kept as written.
- _open_turn_progress counts tool_use blocks over `(message or {}).get("content") or []`, as Python does, not through
  em._content. The review found the two readings agree on every shape the loader admits (a bare-string content,
  non-dict blocks, `{}` and null): kept for fidelity, not a pinned trap. The same holds for the closed/open split of
  `_feed_seg_plain_t` and its trailing `or 0`.
- `x or y` over uuids treats "" as false: a reply whose uuid is "" falls to the settle, then to _seg_jump.
- With no trigger uuid, _last_plain_user_turn_t's anchor search looks for an atom whose uuid equals None. An idle span
  (uuid null) matches before `atoms[0]` is tried, so the prompt before it is not the anchor.
- `board.downtime` is now typed as `[start, end]` number pairs. Python unpacks each entry as `(start, _e)` and
  compares `start > (t or 0)`, so anything else is refused at load. Both worlds' lists are empty; the round trip still
  matches byte for byte.
- Two Python branches cannot run on a tree the loader admits. _feed_seg_fold's stored-prose branch needs a restored
  pre-cut turn, which is refused. _open_turn_progress's per-atom `except` needs a message that is not a dict, or content
  that is a dict or a number, also refused.

**The oracle.** `tools/rust-spike/dump_layer3.py DUMP` loads the real kernel as the earlier layers' dumpers do. It
installs the snapshot's downtime list as kernel._downtime. Then, per alive session, it asks the real functions what
the per-card build asks them:
- _session_working inside the key's try;
- _open_turn_progress, asked whether or not the session works (the build asks only when it does);
- _feed_seg_walk with _FEED_SEG_HELD cleared and no leaf head, then _feed_seg_plain_t on it;
- `k in seg_turn` and `seg_turn.get(k)` for every key, and for one key no fold writes, with the faults counted;
- per turn, per segment of kernel._segs_seam, the answers of _seg_anchors, _seg_jump, jd._prompt_anchor_uuid and
  _seg_last_text, or the exception segmentation raised.

It writes `DUMP/layer3.json` (0.03 MB small, 12.2 MB large). The held aggregate the port leaves out gets a self-check:
each session is walked again with a leaf head, twice, the second walk served from what the first stored, and both must
give the same maps and time as the walk with nothing held. `romp-spike layer3 DUMP` builds the same answer from the
typed structs and compares it value by value. Each object's key order and each list's length count as one value, and
every leaf is compared as romp-json writes it, so an int/float tag counts.

**Result on the worlds: zero differences.**
- Small: 4 sessions, 42 turns, 42 keys, 1,801 values. Large: 38 sessions, 16,877 turns, 16,877 keys, 695,672 values.
- The held aggregate agreed on 4 of 4 and 38 of 38 sessions. It served 38 and 16,839 closed turns, which is every
  closed turn, so leaving it out changes no answer on either world.
- Why that is not enough. No key repeats in either world. No walk raises, no session is suspended, and no segment has a
  settle, an attachment, an API error or an empty uuid. No anchor comes from _seg_jump, and every `seg_turn` position
  matches its segment. Only 2 sessions per world are working. The one layer-3 branch the worlds do reach off the
  common path is the 80-code-point floor, at the boundary atom.

**The edge set.** `dump_layer3.py --edge-cases` writes `rust/crates/romp-views/tests/fixtures/layer3_edge.json`
(synthetic, 0.31 MB; it regenerates byte for byte under another hash seed). Its 136 cases:
- 112 sessions: empty and ended turns, idle tails, trailing command atoms (truthy and falsy), downtime before, at and
  after the last activity (an int start against a float end, a zero end), the progress count over every content
  shape, echo turns, `_echo_text`, romp-goal-id prompts (in a list and in a bare string), every trigger shape, zero,
  negative and tied times of both tags (and an int past 2**53), settle and synthetic replies, API errors, bare ends,
  empty and missing uuids, attachment triggers, the floor at 79 and 80 code points, repeated keys, raises in the first,
  a middle and the open turn, seam tails, placed echoes;
- 11 direct _FeedSegTurns cases over hand-made positions, as a held aggregate would hand them: the matching ordinal,
  another key at the ordinal (the turn's last segment with the key is taken), an ordinal past the end, no such key, a
  raising turn (a fault on every get), a turn index past the end, no turns;
- 12 direct _suspended_after reads, including `t` null, 0 and -0.0.

Result: 129 cases load and match on every value (6,501), 0 differences. The other 7 are refused at load, and they are
exactly the cases named REFUSE. Python raises on 3 of them: an AttributeError on a string message (em._stop_reason, in
_seg_anchors), a ValueError on a downtime entry of three values, a TypeError on a null start. On the other 4 Python
answers: a content that is a dict, a restored pre-cut turn with stored prose counts, a trigger that is a string, and a
downtime start that is a bool.

**The randomized check.** `python3 tools/rust-spike/dump_layer3.py --fuzz N --seed S` makes N random sessions from
layer 2's random turns (dump_layer2.fuzz_turn, its atom, text, time and trigger generators). It decorates them with
what layer 3 reads: ended flags, idle tails, command atoms, echoes, romp-goal-id prompts, settles and synthetic
replies, bare ends, API errors, attachment triggers, prose at the floor, zero times, an input repeated inside a turn,
earlier turns repeated (sometimes with every time's tag flipped, so ties differ only in tag), downtime around the last
activity, and seams aimed at the turns' segments (dump_layer2.fuzz_store). One case in twelve instead hands
_FeedSegTurns the walk's positions perturbed: another ordinal, another turn, past the end. Python answers each with the
real functions, `romp-spike layer3` compares, and any difference or refusal fails the run. The same seed makes the same
file, so a failure replays.
- Result, seeds 1 to 6 at 10,000 cases each (about 6 s apiece): 60,000 cases (55,487 sessions, 4,513 position sets),
  8,059,862 values, **0 differences**, 0 refused.
- What they reached: 145,312 turns and 224,311 segments; 28,379 keys overwritten; 7,621 walks stopped by a raise;
  6,054 sessions suspended and 23,978 working; 9,853 plain-prompt times that are floats; 31,890 segments whose anchor
  came from _seg_jump; 2,178 `seg_turn` faults and 10,551 default answers over the perturbed positions.

**Planted mistakes, each caught.** The first four are romp-spike's `--plant` options (`dump_layer3.py --fuzz ...
--plant NAME` passes only when the plant is caught). The other 21 were edits to `layer3.rs`, each built, run on all
four inputs and reverted by a scratch script. Each cell is differences, then the sessions or cases they fell in.

| Mistake | Small | Large | Edge set (135) | Fuzz, seed 1 (10,000) |
|---|---|---|---|---|
| `--plant max-last` (the last of equal times kept) | 0 | 0 | 5 in 5 | 57 in 57 |
| `--plant overwrite-moves` (a repeated key moves to the end) | 0 | 0 | 57 in 2 | 36,400 in 937 |
| `--plant bytes` (the substantive floor in UTF-8 bytes) | 3 in 1 | 3 in 1 | 4 in 1 | 4,649 in 1,481 |
| `--plant raise-skips` (a raising turn skipped, the walk ok) | 0 | 0 | 17 in 6 | 5,857 in 1,324 |
| trailing command atoms are not skipped | 0 | 0 | 1 in 1 | 34 in 34 |
| a suspension at the last activity counts (`>=`) | 0 | 0 | 5 in 3 | 1,740 in 1,096 |
| progress counts tool uses of every atom kind | 0 | 0 | 1 in 1 | 1,985 in 1,985 |
| progress dates from the first atom, not the turn | 0 | 0 | 1 in 1 | 1,140 in 1,140 |
| with no trigger uuid, `atoms[0]` is the anchor | 0 | 0 | 3 in 3 | 537 in 537 |
| a zero atom time is kept (no `or` to the turn's) | 0 | 0 | 1 in 1 | 125 in 125 |
| echoTurn is not read | 0 | 0 | 1 in 1 | 167 in 167 |
| romp-goal-id read through jd._atom_text | 0 | 0 | 1 in 1 | 53 in 53 |
| the plain-prompt time reads the open turn only | 0 | 0 | 13 in 13 | 1,649 in 1,649 |
| the settle beats the reply | 0 | 0 | 9 in 5 | 1,793 in 826 |
| _seg_anchors does not skip API errors | 0 | 0 | 8 in 3 | 1,728 in 715 |
| a reply uuid "" is kept (Rust's `Option::or`) | 0 | 0 | 1 in 1 | 36 in 35 |
| the attachment branch is dropped | 0 | 0 | 4 in 2 | 160 in 76 |
| the substantive floor is strict (`> 80`) | 0 | 0 | 15 in 7 | 1,307 in 576 |
| prose counted on the stripped text | 0 | 0 | 3 in 1 | 738 in 306 |
| the citable set admits a uuid "" | 0 | 0 | 1 in 1 | 223 in 223 |
| an overwrite keeps the earlier position | 0 | 0 | 64 in 28 | 9,744 in 2,726 |
| the fold segments without the store's seams | 0 | 0 | 22 in 4 | 3,043 in 599 |
| a raise discards the maps | 0 | 0 | 26 in 5 | 4,267 in 802 |
| `seg_turn.get`'s fallback takes the first segment with the key | 0 | 0 | 2 in 2 | 57 in 30 |
| `seg_turn.get` caches a raising turn (one fault, not one per get) | 0 | 0 | 1 in 1 | 8 in 8 |

The worlds catch one mistake of the 25, the byte count, and only at the boundary atom: s001's fourth segment key
names `...-000000000033` (79 code points, 82 bytes) as its latest substantial prose where Python names `...-032`,
and the citable set gains it. Everything else rests on the edge set and the fuzz, and each of them catches every row.

The first round of this table was weaker, and each check found what the other missed:
- The edge set missed the reply uuid "" mutant (0 cases), and the fuzz caught it (63). That needs a settle whose uuid
  is "", so `reply or settle` yields "" and the fold must go on to _seg_jump. A case was added.
- The fuzz missed the zero-time and first-fallback mutants (0 each) that the edge set caught. It drew no human anchor
  at time 0 under a later turn time, and no turn with the same input twice. Both were added. A plain-prompt tie
  differing only in tag came up in 5 of 10,000 cases, and after the tag-flipped repeats in 57.
- The weakest catch is still `seg_turn.get` caching a raising turn, 8 of 10,000. It needs a perturbed position on a
  raising turn, asked twice.
- `--plant overwrite-moves` falls in only 2 of the edge set's 35 overwritten keys. Moving a session's only key leaves
  it where it was, so a check needs a repeat with another key between. The same holds for the worlds' sessions, which
  have no repeat at all.

Tests: 16 new, 102 pass in the workspace. 11 unit tests are in `layer3.rs`. `tests/layer3_edge.rs` has 4 tests over the
fixture: every case answers as Python does, the branch counts equal the fixture's own, and each plant is caught on
exactly the cases built for it. 1 is in romp-spike, for the comparison's messages.

What this feeds: layer 4 (the store helpers) reads these maps directly. _node_anchor_uuids looks up `seg_trig` and
`seg_uuid`, and _node_log_rows looks up `seg_uuid`. Layer 5's summary landing reads `seg_best`, `cite_uuids` and
`seg_turn`, and the body's working card reads `who_working` and the open turn's progress. A wrong key order or tag here
would surface there as a card linking to the wrong message.

## S4.4 as built (2026-11-26)

**The problem.** Between the anchor walk and the card literal, the per-card body asks the goal store about thirty
small questions. Which nodes does the agent's own to-do list still owe? Which open rows did younger siblings' handoffs
pass over? Which open goal does the session's focus sit under? Is a goal waiting on someone (the judge's stamp), on
whom, and did that peer already answer? When was its block asked and its outcome landed? Are all its open leaves
handed to peers, and what should a delegated card be titled and whom should its badge name? Where do a node's title and
work links land? Each answer is a few lines of Python. Each leans on a Python habit a port gets wrong in a way no
ordinary store shows: `max` keeping the first of equal values (and with it a 5 or a 5.0), `x or y` skipping a zero or
an empty string, a dict's insertion order, `int()` of a float, the regex `$`, Unicode `lower()`, and one table that
carries a node's anchors from one session's lookup to the next. Neither world has an awaiting stamp, a parked row, an
agent to-do item, a born step, a heal, a review advance, a cold anchor lookup, an auth error or a peer the registry
does not know, so the worlds alone prove little here.

`layer4.rs` ports the 25 functions section 2 lists and the four unlisted ones they call (_peer_stamp_superseded,
_stamp_written_at, _title_shaped, _names_parts), with _HANDOFF_LABEL_RE, _UUIDISH_RE, _AWAIT_ITEM_KINDS and
jd._JUNK_QUOTES, line for line. It also ports the body's own `children` build (the healed parents, the hidden set, a
born step whose parent is gone becoming a root) and its `_subtree` walk, since the helpers take them as input. What the
notes flagged, as built:
- The awaiting stamp is the `max` over `(awaitingAt or 0, why, kind or "", peers)`, so equal anchors break ties on the
  why, then the kind, then the peer list, and a full tie keeps the first stamp the stack walk meets, with its tag.
  `kind or ""` makes a missing kind and an empty one tie.
- `int(since)` truncates toward zero at any size: 1e20 is a 21-digit int (romp-json's `BigInt`), -0.5 is 0, and a
  falsy since (0, 0.0, -0.0) is None.
- The `or` fallbacks: _block_ev_t takes `ev_t or at` and drops a falsy result; _done_since takes `ev_t or at or 0` and
  keeps it, so a done row with no time answers 0, not the node's mt. `mt or t or 0`, `awaitingAt or 0`,
  `promptUuid or seg_trig[...]` (an empty uuid falls through) and `prompt or held[0]` are each written as Python's `or`.
  When every operand is falsy, `or` yields the last one, so `mt or t or 0` and a done row's `ev_t or at or 0` answer
  the literal int 0, never the node's 0.0 or -0.0; and _parked_rows' `(t or 0) > t0` reads a sibling with no t as 0,
  younger than a node whose t is negative.
- `is None` against truthiness: an own `born: {}` is not None, so _session_started_face never reads the heal's
  record, and being falsy it answers no face. _node_anchor_uuids never writes or reads the table for an empty node id
  (`if nid`), while an empty work anchor is warm (`is not None`).
- Repeats, splits and `\s`: the stamp's peers keep their repeats (`[U1, U1, "peer:" + U1]` is three identities), where
  _handoff_peer_identities deduplicates. The handoff label's `host:name` splits at its last colon (`rpartition`), the
  postal log's pairing at its first (`partition`). The title's sentence cut takes any Unicode `\s` after the stop
  (U+001C, U+0085, U+00A0, U+3000), as a Python str regex does.
- _agent_open_set runs over the body's healed children, not the nodes' own parents: an open to-do the heal re-parents
  marks its new parent, and one on a hidden top marks nothing.
- Order: the children map keeps its keys in first-seen order, _parked_rows answers in that order, _open_leaves in
  document order (pre-order, the open kids taken before any recursion), _handoff_peer_identities sorted by name, stably.
  _agent_open_set is a set, compared sorted.
- Absent and null: an awaited row writes `agentId`, `detail` and `stoppable` only when truthy; _node_log_rows answers
  None for no rows, not []; a recorded peer-answered that is absent means Python never asked, and then the port asks
  nothing about the stamp.
- State across calls: `AnchorTable` is _node_anchor_last. A warm work anchor is remembered by the node's `id` field,
  and a later cold lookup of the same id serves it, from whichever session it came. It is carried in the alive order
  across a dump and across a world's sessions; the oracle clears the kernel's table first. The feed passes no sid, so
  _node_anchor_rev is not ported. Which nodes write the table, and in what order, is not this layer's: the body calls
  _node_anchor_uuids only inside its `flatten`, so the port's per-node calls in store order are a test harness, not
  the body's sequence (built in S4.6 from the order carried forward from S4.4's review: "S4.6 as built").
- Recursion: every walk is iterative. _agent_open_set and _open_leaves recurse in Python and raise RecursionError near
  1,000 levels, so the loader refuses a parent chain past `MAX_NODE_DEPTH` (500 nodes, counting a cycle's distinct
  members), and `body_children` refuses a healed forest past it. _goal_awaiting_stamp_full and the body's `_subtree`
  have no seen-set and loop forever from a node inside a parentId cycle; the body calls them on card tops only, which no
  cycle reaches, and here they return `Err(Loops)` at the first repeated node. The oracle checks the same walk before
  calling and records `{"loops": true}`.
- _peer_identity's whole ladder is ported. The rungs past the registry read the attached hosts' snapshot
  (kernel._remotes) and the postal log's pairing, which the port takes as recorded inputs (`PeerWorld`); in a dump's
  replay both are empty, and dump_layer4.py checks that. A row is found by its sids, its name is then looked up under
  the row its `host` field names, which may be another row.
- Three readings the edge set pins with a mutant each: `_UUIDISH_RE` ends in `$`, which in Python also matches before
  one trailing newline, so `"<uuid>\n"` takes the uuid rungs; jd.junk_quote lowercases with Unicode `lower()`, and the
  Kelvin sign (U+212A) is the only code point outside ASCII whose lowercase is all ASCII ("o" then a Kelvin sign is
  the stub "ok"); _HANDOFF_LABEL_RE's first group is lazy and spans lines (`re.S`).
- _login_refusal_label raises AttributeError on a truthy api-error answer that is not a dict; the port returns that
  exception (`Err(Raise)`) with Python's message.

Loader changes (layers 1 to 3 re-run, zero differences): nodes now type agentTask, awaitingWhy, awaitingAt,
awaitingKind, awaitingPeers, rolledUp, born, deltaSince, distilledMt and summaryAnchor; log rows synth and lift (the
lift key, read by _stamp_written_at, was missing from the unported list because the ast scan did not cover that
unlisted function); live rows authLogin, authLoginLive and authLabel; the recorded healSessionTops is typed as
`[parent or null, born record]` pairs; a names entry may be null. `snapshot::validate` is split into `validate_store`
and `validate_node` so the edge set loads stores through the same checks. Refused at load where Python answers, none in
either world: an agentTask that is a truthy non-dict (Python raises), a born that is not a dict or null (Python ignores
it), awaitingPeers that is not a list of strings (Python iterates a string's characters), an awaitingWhy or a live row's
login that is not a string, a heal answer that is not a pair, a null per-peer answer time, and a chain past the limit.
The review round added one more: an integer literal past i64, anywhere in the snapshot or in an edge or fuzz case's
inputs. Python's `json` reads one as an int, while serde_json reads one past u64 as a float, and `Num` took the float
(it already refused 2**63 to 2**64 - 1, which serde_json hands it as a u64). The port then answered an mt of 10**20
with blockSince `1e+20` where Python has the int, named an api error of 10**20 a 'float' in the AttributeError's text,
and gave an awaited row's since of 2**64 + 1 back as 2**64. The check decodes the bytes again with romp-transcript's
decoder, which reads numbers as Python does (`load::refuse_big_ints`); romp-spike also checks the layer 1 to 3 answer
files and the layer 2 and 3 edge files whole, so an answer past i64 there cannot be misread either. Trees are not
checked: the second decode would triple their load (150 ms to 450 ms on the large world's 79.6 MB), and no dumped tree
holds such an integer (both worlds scanned).

**The oracle.** `tools/rust-spike/dump_layer4.py DUMP` loads the real kernel as the earlier dumpers do, installs the
dump's names registry as the replay scopes it, empty peer facts and a cleared _node_anchor_last, and per alive session
calls the real helpers on what the body hands them: the store, the recorded heal and peer-answered answers (the tuple
the live helper returns), the live row, the recorded api error, and layer 3's seg_uuid and seg_trig from the real
_feed_seg_walk. The body's `children` comes from kernel._feed_session_entry's own source: its `_hidden` and `children`
statements are compiled as a function, so the oracle builds the input with the kernel's lines. Per node in store order
it records the twenty per-node answers (the stamp, its peers and rows, the subtree's newest block time, the open
leaves, delegation, the handoff card, the four times, the review boundary, the started face, the log rows, the anchors,
the junk test, and the 12 landing inputs for every state and column the body passes). Every sid the dump names gets
_name_of, _name_color and _peer_identity. It writes `DUMP/layer4.json`, and `romp-spike layer4 DUMP` builds the same
answers from the typed structs and compares them value by value, as layer 3's comparison does. Python's answers are
read with romp-transcript's decoder, because serde_json reads an integer past u64 as a float.

The build check: dump_layer4.py also runs one replay build of the dump (dump_world's harness) with the real helpers
wrapped, and compares every call the per-card body makes, with its real arguments, against the oracle's answer for the
same session and node (the children the body built included). A helper's own nested calls are not the body's and are
skipped.

**Result on the worlds: zero differences.**
- Small: 4 sessions, 10 nodes, 6 names, 864 values. Large: 38 sessions, 78 nodes, 40 names, 6,406 values (29 ms).
- Build check: small 87 calls (91 comparisons with the children), large 597 (635), 0 unmatched, 0 mismatches.
- Why that is not enough. The worlds reach none of the stamps, parked rows, agent to-dos, born steps, heals, review
  advances, cold anchors, auth errors or registry misses, and their only handoff label is resolved by the registry.

**The edge set.** `dump_layer4.py --edge-cases` writes `rust/crates/romp-views/tests/fixtures/layer4_edge.json`
(synthetic, 0.46 MB; it regenerates byte for byte under another hash seed). Its 225 cases:
- 137 worlds of one to four sessions sharing an anchor table, with the registry and peer facts where the case names
  peers: heals that nest a top, hide one, or name an empty parent; born steps whose parent is gone; agent to-dos under
  flat-done umbrellas and every falsy agentTask; open leaves in document order and all-delegated tops; parentId cycles
  (the stamp walk loops, the cycle-safe readers answer) and orphans; parked rows (younger handoffs, equal times, synth
  rows, a sibling inside a cycle, the children map's order); focus tops over every rollup status, a missing focus, a
  cycle and the empty key; stamps tied on the anchor in both tags and broken on why, kind and peers, rolledUp, empty
  whys, falsy anchors, peer answers that supersede and stamps written after them, lift rows; block and done rows with
  ev_t, at, both, neither and zeros; every review-boundary branch; started faces from the node and from the heal; log
  rows past the cap of 8 with and without anchors; warm and cold anchors, an empty work anchor, junk quotes and the
  same id in two sessions and twice in one store; 23 junk quotes; 20 handoff cards over every peer and label shape;
  17 login-label inputs, five of which raise; the identity ladder over 35 query shapes with and without peer facts;
  a parent chain and a healed forest at the depth limit (their answers kept as a SHA-1 of Python's json.dumps);
- 88 direct calls: 21 awaited rows (every kind, three bad ones, since of every sign and size, both ends of i64, three
  integers past it), 30 titles, 11 supersede tests (tuple and scalar answers), 5 stamp walks without `children` (one in
  a cycle), 21 landing inputs.
- 20 of these came in the review round (9 worlds, 11 direct calls), each aimed at a mutant the 205 missed: see "The
  review round" below.

Result: 210 cases load and match on every value (25,011), 0 differences. The other 15 are refused at load, and they
are exactly the cases named REFUSE: Python raises on 2 (an AttributeError on an agentTask string, a RecursionError on
a chain of 1,000) and answers the other 13 (five of them integers past i64). One past the depth limit (a chain, a
healed forest, a cycle of 501) is refused where Python still answers; the Rust unit tests pin those three, kept out of
the fixture for its size. Python's line trace over the edge set ran 273 of the 278 lines of the 29 functions and the
body's children lines. The 5 it did not run cannot run in the per-card build: _node_anchor_uuids' revision bump needs
a sid, which the feed never passes, and _names_parts' registry-file read needs no names scope, which the replay always
sets.

**The randomized check.** `python3 tools/rust-spike/dump_layer4.py --fuzz N --seed S` draws worlds of one to three
sessions over the shared registry and peer facts (node ids that repeat across sessions or differ from their keys;
parents in chains, orphans and two- or three-node cycles; int and float times with ties, zeros, -0.0 and absents; log
rows of every kind with every time present, zero, null or absent, synth and lift flags; stamps, a third of them the
previous stamp again with its anchor's tag flipped; peer answers aimed at the stamps' peers; handoffs whose peers take
every identity shape and texts around the label; mangled junk quotes; heals, hidden tops and born steps; live rows and
api errors of every shape), and, one case in five, a direct call. The review round added a negative node t (-5) and
U7, a peer the postal log pairs with a name holding a colon. Python answers each with the real functions,
`romp-spike layer4` compares, and any difference or refusal fails the run. The same seed makes the same file byte for byte
(checked under another hash seed), so a failure replays.
- Result, seeds 1 to 6 at 10,000 cases each (about 15 s apiece): 60,000 cases (47,971 worlds with 83,900 sessions and
  347,312 nodes; 12,029 direct calls), 30,625,221 values, **0 differences**, 0 refused. Python raised on 559 cases, each
  an AssertionError on the awaited-row kind "task" (the generator's one raising input), matched as the same
  exception.
- What they reached: 80,604 stamps and 25,203 stamp walks that loop; 5,490 parked rows; 18,330 agent-open ids; 8,178
  hidden tops; 61,832 started faces; 58,571 review boundaries set; 73,687 subtrees whose newest block time is unknown;
  40,149 handoff badges (18,140 named by the label); 74,213 registry misses, 28,278 remote and postal names, 10,461
  composites; 21,057 login labels that raise. Python's line trace on every seed: the same 273 of 278 lines.

**Planted mistakes, each caught.** The first five are romp-spike's `--plant` options (`dump_layer4.py --fuzz ...
--plant NAME` passes only when the plant is caught). The other 78 are edits to the source, each built, run on all four
inputs and reverted by a scratch script (not committed): the first round's 56, the review's 18 (R1 to R18, the
reviewer's own edits reused verbatim), three order mutants added in the review round (X1 to X3), and the input-side
refusal of an integer past i64 taken out (B1, in `EdgeCase::load_checked`). The table is the review round's run, on the
final bytes, the 225-case edge set and the regenerated fuzz seed 1. Each cell is differences, then the sessions or
cases they fell in.

| Mistake | Small | Large | Edge set (225) | Fuzz, seed 1 (10,000) |
|---|---|---|---|---|
| `--plant max-last` (the last of equal stamps kept) | 0 | 0 | 1 in 1 | 380 in 293 |
| `--plant int-round` (an awaited row's since rounded) | 0 | 0 | 5 in 5 | 205 in 205 |
| `--plant uuid-strict` (no trailing newline in the uuid shape) | 0 | 0 | 4 in 2 | 2,329 in 780 |
| `--plant table-off` (no warm anchors remembered) | 0 | 0 | 12 in 3 | 2,517 in 1,214 |
| `--plant parked-sorted` (parked rows in id order) | 0 | 0 | 9 in 2 | 32 in 11 |
| block_ev_t's max keeps the last of equal times | 0 | 0 | 6 in 1 | 296 in 126 |
| block_ev_t keeps a falsy time | 0 | 0 | 2 in 1 | 1,323 in 565 |
| block_ev_t reads ev_t only (no `or at`) | 0 | 0 | 8 in 3 | 6,312 in 2,270 |
| a timeless done row is skipped (falls to mt) | 0 | 0 | 1 in 1 | 873 in 811 |
| block_since falls to mt or 0 (no t) | 0 | 0 | 30 in 7 | 14,415 in 5,744 |
| the stamp's max compares the anchor only | 0 | 0 | 5 in 4 | 338 in 96 |
| the stamp's max ignores the peer lists | 0 | 0 | 1 in 1 | 13 in 6 |
| a kindless stamp sorts after every kind (no `kind or ""`) | 0 | 0 | 26 in 9 | 3,584 in 1,770 |
| rolledUp stamps count | 0 | 0 | 3 in 1 | 1,008 in 475 |
| a job stamp can be superseded | 0 | 0 | 3 in 3 | 1,643 in 763 |
| a stamp written at the answer's time is superseded (<=) | 0 | 0 | 3 in 1 | 718 in 290 |
| lift rows count as writes | 0 | 0 | 1 in 1 | 7 in 4 |
| the pair map is ignored (any answer supersedes) | 0 | 0 | 15 in 4 | 2,584 in 628 |
| a falsy since is int(0), not None | 0 | 0 | 3 in 3 | 81 in 81 |
| an absent agentId is written as null | 4 in 1 | 4 in 1 | 81 in 42 | 7,536 in 3,523 |
| an awaited row's label is not stripped | 0 | 0 | 1 in 1 | 45 in 45 |
| a nameless peer row has no "a peer" label | 0 | 0 | 3 in 2 | 229 in 141 |
| a title collapses whitespace without a newline | 0 | 0 | 6 in 6 | 971 in 910 |
| a title's first sentence may end before the 13th code point | 0 | 0 | 4 in 4 | 46 in 46 |
| a clamped title trims with Rust's trim_end | 0 | 0 | 2 in 2 | 2 in 2 |
| the handoff label's first group is greedy | 0 | 0 | 3 in 1 | 1,315 in 526 |
| the label renames a registry peer | 0 | 0 | 3 in 2 | 781 in 578 |
| a label without a host clears the peer host | 0 | 0 | 1 in 1 | 686 in 645 |
| a composite's uuid tail is not cut to 8 | 0 | 0 | 18 in 3 | 1,231 in 778 |
| an empty postal tail names nothing (not the stub) | 0 | 0 | 2 in 1 | 2,644 in 1,511 |
| the remote name is read from the owning row, not by its host field | 0 | 0 | 1 in 1 | 3,054 in 946 |
| handoff peers are not sorted | 8 in 1 | 8 in 1 | 14 in 1 | 116 in 9 |
| handoff peers are not deduplicated | 0 | 0 | 2 in 1 | 2 in 1 |
| junk quotes lowercase ASCII only | 0 | 0 | 3 in 3 | 2,560 in 1,759 |
| junk quotes keep a trailing ellipsis | 0 | 0 | 2 in 2 | 2,238 in 1,556 |
| a slash command with a space is junk | 0 | 0 | 2 in 2 | 1,216 in 903 |
| an empty work anchor reads cold (truthiness, not `is not None`) | 0 | 0 | 1 in 1 | 955 in 537 |
| a cold lookup's remembered prompt overrides its own | 0 | 0 | 1 in 1 | 209 in 194 |
| an empty promptUuid does not fall through (Option::or) | 0 | 0 | 1 in 1 | 7,324 in 4,177 |
| a junk quote keeps the prompt anchor | 0 | 0 | 5 in 2 | 6,109 in 3,693 |
| no log rows answer [] (not None) | 6 in 3 | 40 in 37 | 283 in 114 | 22,883 in 6,553 |
| the oldest eight log rows, not the newest | 0 | 0 | 24 in 1 | 224,548 in 4,112 |
| the state decides before a working column | 33 in 3 | 237 in 37 | 987 in 127 | 209,280 in 7,370 |
| the login label skips the used-login check | 0 | 0 | 1 in 1 | 294 in 288 |
| a falsy non-dict api error raises | 0 | 0 | 1 in 1 | 1,392 in 1,306 |
| an empty parentText does not fall back to the parent's text | 0 | 0 | 2 in 1 | 749 in 690 |
| the heal's born beats the node's own | 0 | 0 | 3 in 3 | 966 in 653 |
| the review boundary advances on an equal deltaSince (>=) | 0 | 0 | 1 in 1 | 32 in 31 |
| a blank summary counts for the review boundary | 0 | 0 | 1 in 1 | 279 in 270 |
| _agent_open_set stops at the first open child | 0 | 0 | 1 in 1 | 152 in 73 |
| synthesized rows count as history in _parked_rows | 0 | 0 | 1 in 1 | 21 in 21 |
| an equal-time sibling leapfrogs (>=) | 0 | 0 | 3 in 3 | 407 in 394 |
| open leaves do not pierce a done marker | 0 | 0 | 5 in 2 | 1,494 in 574 |
| no leaves reads as all delegated | 3 in 2 | 37 in 36 | 14 in 7 | 18,857 in 6,252 |
| a cleared root is a focus top | 0 | 0 | 4 in 1 | 5,045 in 2,107 |
| a born orphan is not made a root | 0 | 0 | 3 in 3 | 1,008 in 634 |
| an empty healed parent counts | 0 | 0 | 1 in 1 | 948 in 466 |
| a hidden top keeps its place | 0 | 0 | 3 in 2 | 2,602 in 1,223 |
| an unknown block time is skipped, not None | 0 | 0 | 6 in 3 | 12,416 in 4,633 |
| a registry color need not start with # | 0 | 0 | 2 in 1 | 1,579 in 801 |
| an empty registry name is a name | 0 | 0 | 26 in 2 | 2,178 in 856 |
| stamp tie-break: kind before why (R1) | 0 | 0 | 11 in 2 | 240 in 54 |
| pair-aware answer: the first named peer, not the max (R2) | 0 | 0 | 9 in 1 | 245 in 65 |
| stamp write time reads ev_t before at (R3) | 0 | 0 | 1 in 1 | 139 in 53 |
| stamp write time not floored at awaitingAt (R4) | 0 | 0 | 3 in 1 | 1,637 in 561 |
| stamp peers deduplicated (Python keeps repeats) (R5) | 0 | 0 | 2 in 1 | 116 in 28 |
| _agent_open_set over the nodes' own parents (heal ignored) (R6) | 0 | 0 | 2 in 1 | 265 in 255 |
| an empty own born falls to the heal's record (truthiness, not `is None`) (R7) | 0 | 0 | 1 in 1 | 156 in 153 |
| anchor table keyed by an empty node id too (no `if nid`) (R8) | 0 | 0 | 4 in 1 | 13 in 10 |
| cold lookup: summaryAnchor before the remembered anchor (R9) | 0 | 0 | 2 in 2 | 268 in 176 |
| title sentence cut: ASCII whitespace lookahead only (R10) | 0 | 0 | 5 in 5 | 63 in 63 |
| label rename splits host at the first colon (not rpartition) (R11) | 0 | 0 | 2 in 1 | 1,104 in 530 |
| postal pairing split at the last colon (not partition) (R12) | 0 | 0 | 2 in 1 | 2,230 in 757 |
| block_since keeps a falsy t's float tag (`mt or t or 0` without the literal 0) (R13) | 0 | 0 | 3 in 1 | 1,826 in 1,573 |
| review boundary's reopen reads `ev_t or at` (R14) | 0 | 0 | 1 in 1 | 3 in 3 |
| open leaves: agent-open pierces cleared too (R15) | 0 | 0 | 2 in 1 | 568 in 237 |
| login label: an empty authLabel kept (no `or "login"`) (R16) | 0 | 0 | 1 in 1 | 80 in 80 |
| parked: a sibling with no t never counts (no `or 0`) (R17) | 0 | 0 | 1 in 1 | 23 in 23 |
| done_since keeps a falsy at's float tag (`ev_t or at or 0` without the literal 0) (R18) | 0 | 0 | 3 in 1 | 206 in 197 |
| open leaves sorted by id (not document order) (X1) | 0 | 0 | 13 in 2 | 628 in 255 |
| equal-named handoff identities ordered by sid (not first seen) (X2) | 0 | 0 | 4 in 1 | 16 in 4 |
| the children map iterated in key order (not first seen) (X3) | 0 | 0 | 5 in 1 | 0 |
| no input-side refusal of an integer past i64 (B1) | 0 | 0 | 4 in 4 | 0 (none drawn) |

The worlds catch 5 of the 83: the null `agentId` and the unsorted handoff peers on s001's delegating goal (two
handoffs, s003 and s002), and three on shapes nearly every store has: the empty log answered as [], the state read
before a working column, and a top with no open leaves read as delegated. Everything else rests on the edge set and the
fuzz. The edge set catches every row. Fuzz seed 1 catches every row but two: X3 (it reorders only _parked_rows'
output across parents, and the fuzz rarely parks rows under two parents whose keys are out of order) and B1 (the fuzz
draws no integer past i64, since any refusal fails a fuzz run; the edge set's REFUSE cases hold it, and taking out the
snapshot-side check fails the unit test `snapshot::tests::an_integer_past_i64_is_refused`).

The first round of this table was weaker, and each check found what the other missed:
- The edge set missed 4 of the 56 source mutants, which the fuzz caught: a falsy block time kept (no node had only falsy
  block times), Rust's `trim_end` at the title clamp (no clamp cut landed on U+001C..U+001F), a greedy first label group
  (no label had two ": "), and an empty work anchor read as cold (the difference shows only in a later cold lookup of the
  same id). A case was added for each.
- The fuzz missed the `trim_end` mutant too, and caught `--plant max-last` in 1 case of 10,000; the edge set's own
  full-tie case first missed that plant, because its first and last stamps were both ints. The case now meets 5 first and
  5.0 last, and the fuzz repeats a third of its stamps with the anchor's tag flipped.
- The review then found 11 more mutants the edge set missed (below), two of which the fuzz missed as well.

**The review round.** The review had no must-fix. Its should-fix items, all applied:
- Nine semantics the notes flag were caught only by the fuzz, and two by nothing (R12, R17). Each now has an edge case,
  and each mutant fails the new edge set on exactly that case (the old set's 205 cases give 0 on every one):

| Mutant (the review's number) | Edge set, 205 | Edge set, 225 | The case that catches it |
|---|---|---|---|
| _stamp_written_at reads `ev_t or at`, not `at or ev_t` (R3) | 0 | 1 in 1 | `superseded 45`: awaitingAt 40, an awaiting row with ev_t 50 and at 30, a scalar answer 45 (Python: 45) |
| the stamp's peers deduplicated (R5) | 0 | 2 in 1 | awaitingPeers `[U1, U1, "peer:" + U1]` on a peer stamp (three identities) |
| _agent_open_set over the nodes' own parents (R6) | 0 | 2 in 1 | an open to-do the heal re-parents under a, another on a hidden top (Python: a, b) |
| an own `born: {}` falls through to the heal's record (R7) | 0 | 1 in 1 | `born: {}` with a heal entry for the same node (Python: no face) |
| the anchor table written and read for id "" (R8) | 0 | 4 in 1 | a warm node with id "", then two cold ones with id "" in a later session |
| the title cut's lookahead takes ASCII whitespace only (R10) | 0 | 5 in 5 | five titles whose stop at index 16 is followed by U+001C, U+00A0, U+3000, U+000B or U+0085 |
| the label's host:name split at the first colon (R11) | 0 | 2 in 1 | `↪ delegated to a:b:c: work` on a registry miss (Python: peer c, host a:b) |
| the postal pairing split at the last colon (R12) | 0 | 2 in 1 | U7 paired with `h7:delta:x` in POSTAL, asked in the identity ladder (Python: delta:x on h7) |
| `mt or t or 0` keeps a falsy t's float tag (R13) | 0 | 3 in 1 | mt 0 with t 0.0, and with t -0.0, and t 0.0 alone (Python: the int 0) |
| `(t or 0) > t0` skips a sibling with no t (R17) | 0 | 1 in 1 | p, a with t -5, b a handoff with no t (Python: a parked once) |
| a done row's `ev_t or at or 0` keeps a falsy at's tag (R18) | 0 | 3 in 1 | done rows with at 0.0 or -0.0 and ev_t absent, 0 or None (Python: the int 0) |
| no input-side refusal of an integer past i64 (B1) | 0 | 4 in 4 | four of the five integers past i64 load (2**63 is still refused by `Num` itself) |

- R12 and R17 also passed the old fuzz: POSTAL held no name with a colon, and no node t was negative. U7 joined POSTAL,
  the identity queries and the fuzz's peers, and -5 joined the fuzz's node times; seed 1 now catches them (2,230 and 23
  differences).
- The loader read an integer of 2**64 or more as a float (pre-existing, reachable through layer 4); it is now refused
  on the input side (see the loader paragraph above). Not done: trees are not checked, for the load time.
- SPIKE.md now carries the body's call order for _node_anchor_uuids forward to layer 6 (then under "What layer 5
  needs", now the first bullet of "What layer 6 needs"), and the
  nits are fixed (jd._seg_command_worked; layer4.rs's header now lists only readings the edge set pins with a mutant).

**Findings beyond the port.**
- dump_world's replay answered the recorded `_peer_answered` with the JSON round trip's list. _peer_stamp_superseded
  branches on `isinstance(answered, tuple)`, so the list took the legacy scalar path, and with any stamp that has an
  anchor compared a number with a list: `_goal_awaiting_stamp_full(nodes, top, None, answered_at=[0, {}])` raises
  TypeError where the tuple answers `(40, 'w', None, ())`. Neither world has a stamp, so no dump was wrong. The replay
  now hands back a tuple; both worlds were dumped again and every file but `run.json` is byte-identical (23 and 159),
  with the replay checks passing.
- The comparisons of the earlier layers read Python's answers through serde_json, which turns an integer past u64 into
  a float. No earlier answer holds one (layer 2's times past 2**63 are floats), but layer 4's `int(since)` does,
  so `romp-spike layer4` reads Python's answers with romp-transcript's decoder (the first edge run showed `1e+20` against
  the port's 21 digits).

## S4.5 as built (2026-11-26)

**The problem.** Layer 5 decides three things a card shows. Where a click on a card's brief or summary lands: the
assistant reply that carries the summary's first sentence, else the segment's newest substantial reply. Whether a top
whose every leaf is a handoff to a peer is the user's own dictated ask (a card) or only coordination (hidden). And the
dotted "Working: ..." or "Analyzing: ..." placeholder a live session shows while the planner has not yet filed its
newest prompt. Each is a few dozen lines of Python, and each leans on Python habits a port gets wrong without any
ordinary session showing it: `re.I` folding one code point at a time with its own table, lengths and slices in code
points, `\d`, `\w` and `\s` as Unicode classes, a regex `findall` consuming the text it matched, `is` against `==`, and
`int()` truncating before a comparison. Neither world has a reminder, a slash command, a follow-up, an attachment, a
drifted placement, a caption, a cleared card, an ask latch, or a quote found only case-insensitively.

`layer5.rs` ports, line for line, kernel._summary_text_anchor with _text_atoms, _opening_sentence and jd._locate_quote;
_summary_outrun; _dictated_prompt_uuid with jd._human_prompt_record (and em.postal_pairs, jd.NUDGE_MARKER_RE) and
_pure_delegation_top; _provisional_card with _seg_placed, _live_replanned, _live_cleared_under, _card_gone (unlisted, as
_provisional_card and _live_cleared_under call it), _seg_caption, _seg_prompt, _split_reminders and its inline comment
strip, and jd._seg_human, _seg_command, _seg_command_worked, _seg_slash_shaped, _seg_followup and _seg_system. Every
regex is matched by hand, as Python's engine matches it (the leftmost match, a lazy group as short as it can be).
`pyre.rs` holds Python's own classes and case tables (`pyre_tables.rs`, generated from Python's `re` and `_sre` by
`dump_layer5.py --tables`), and `tests/layer5_edge.rs` checks them on every code point against the tables Python
writes into the fixture. What the notes flagged, as built:
- jd._locate_quote tries an exact `find`, then the quote's words joined by `\s+` under `re.I`. sre folds one code point
  at a time: an uncased pattern character matches only itself, a cased one any character whose simple lowercase
  (`_sre.unicode_tolower`) is its own, or one listed for it in `re._casefix._EXTRA_CASES`. So the long s matches `s`,
  the dotless ı `i`, the final ς `σ`, the micro sign `μ`, and `İ` (whose simple lowercase is `i`) `i`. A comparison
  of lowercased characters gets all of these wrong; it gets the Kelvin sign right, because K lowercases to `k`. So
  one reading in the notes did not hold: their example of what a lowercase comparison misses was the Kelvin sign,
  which it does not miss; the characters above are the real cases. (`ß` and `ẞ` match each other both ways: sre takes
  `ß` as cased, its uppercase being the first letter of "SS".) The quote is stripped the Python way first, so a
  quote that begins with U+001C is found without it.
- _opening_sentence: paragraphs split at `\n\s*\n` (a whitespace run holding two newlines, Unicode whitespace
  included); a leading list marker whose digits are any Unicode decimal (`١.` and `１.` are markers, `².` is not, and
  the marker needs whitespace after it); the first line; the cut after the first `.`, `!` or `?` past 12 code points
  that Unicode whitespace or the end follows (a stop at index 12 counts); `[:200]` then `strip()`. `str(span)[:300]`
  and the offset `find` returns count code points too: a located span of 160 two-byte code points (320 bytes) is kept
  whole.
- _summary_text_anchor searches jd._atom_text, which reads a bare-string content as "", while _text_atoms admits that
  atom through em.atom_has_text; such an atom can only be the stub. The turn's other text atoms leave out the
  segment's by identity (`a is s`), so a second atom with a repeated uuid is still searched. The stub tier takes the
  segment's last text atom, and the rest of the turn's only when the segment has none. The memo is left out, as layer
  3 left out the held aggregate, and the oracle checks that a hit answers as a miss within one build. It is a pure
  cache only within one build, or across builds of the same parse and seams, which is all the spike's fresh-process
  comparisons meet: its key, `(fsid, nid, seg key, hash(line), the turn's end or t, stub_ok)`, leaves out the
  segment's atoms, and only eviction clears it. A seam stamped between two builds keeps the head segment's key and the
  turn's end, so the second build's hit serves the landing found over the unsplit segment (see "The review round"
  below and "S4.6 as built").
- _summary_outrun compares with `max(int(distilledMt or 0), int(briefedMt or 0))`, so 100.9 covers a segment at
  100.5 only as far as 100: that segment outruns it. `int()` truncates toward zero (-2.5 reads -2, not -3), and a
  trail key missing from `seg_best` reads 0, which outruns a negative coverage. A coverage past i64 compares exactly
  (an integral float).
- The segment predicates find the trigger atom by `uuid == seg["trigger"]`: with no trigger, the first atom without a
  uuid is the trigger, before `atoms[0]`. _seg_prompt instead searches only for a truthy trigger, so a trigger of ""
  falls to the first user atom.
- em.postal_pairs is a findall: a `romp-msg-kind` marker whose lazy value runs over a `romp-msg-id` marker consumes it,
  and no pair is made, so such an attachment is still a dictated record. A kind binds only to an id that has none yet,
  so a second kind after it is dropped. _pure_delegation_top's userAsk test is `isinstance(..., dict)`: an empty `{}`
  still exempts the top.
- _provisional_card reads `_captions(fsid)` before it looks at whether the segment is a system notice: an unrecorded
  caption read fails even where the caption is never shown (the port returns `NotRecorded`, as the oracle's reader
  raises). `pre + g` raises TypeError on a truthy caption that is not a string, with Python's message. The turn is open
  only if no atom anywhere in it is an idle span, unlike _session_working's trailing test. A seam tail shows its own
  prompt when it has one (a system-authored note, say); "Working past" is only for a seam tail whose prompt is empty.
  BARE_SLASH_RE's `\w` is Python's (a combining vowel sign is not one, so `/kaा` is not slash-shaped), and its `$`
  matches before a final newline. _split_reminders strips each reminder the Python way (U+001C included).
- Not pinned, because no input the loader admits shows a difference. Four were tried as mutants and moved nothing on
  any check: the `$` in the cut's `(?=\s|$)` (a stop at the very end cuts nothing off, the same as no stop, and the
  first line holds no newline; M58); the `endswith("#p")` test in _seg_caption's drift scan (a key whose jd._seg_key
  equals `_seg_key(seg + "#p")` already ends in `#p`; N1); `any(v is None)` in _live_cleared_under (the
  `all(isinstance(v, str) and ...)` after it is already false then; N2); FOLLOWUP_RE's lazy group (`[^\s>]` cannot
  cross the `>` of a `-->`, so a greedy group finds the same id; N3). One needs no mutant: `held.get("t", lt["t"])`
  never meets an absent `t` (every segment carries one, and a turn without `t` is refused at load). em.postal_pairs'
  lazy group, unlike FOLLOWUP_RE's, is pinned: `\S` crosses a `-->`, so `<!-- romp-msg-id: a-->b -->` is the id `a`,
  where a greedy group reads `a-->b` (N4).

Loader changes (round trip and layers 1 to 4 re-run on fresh dumps, every number as before): a placement's value may
be null (a retired placement, still a ruling) and the map itself may be null (`or {}`); nodes now type `askAnchor` and
`userAsk` (any value; only the strings and the dict test are read), `followupAt` and `briefedMt` (numbers or null), and
all but `briefedMt` leave `NODE_UNPORTED`. An ast scan of the node keys read by the body and the layer 1 to 5
functions found no other untyped key outside that list. The session's recorded captions are typed (id to a row whose
`caption` is any value). An atom whose `text` is truthy and not a string is refused (`tree::validate_atom`):
jd._human_prompt_record reads `str(a.get("text") or "")`, and Python's `str()` of a number, list or dict is not
ported; no dumped atom carries `text`. Refused at load where Python answers or raises, none in either world: a
truthy non-string atom text, a placement value that is not a string or null, a caption row that is not a dict (Python
raises AttributeError), a `followupAt` or `briefedMt` that is a string or a bool (Python raises TypeError on the
first, reads `int("500")` and `True` as numbers), null `nodes`, captions that are not a dict, and an integer past i64.

**The oracle.** `tools/rust-spike/dump_layer5.py DUMP` loads the real kernel as the earlier dumpers do and, per alive
session, asks the real functions what the per-card build asks them, with the session's tree served as its parse, its
recorded captions as `_captions(fsid)` (a session whose captions were never read raises `NotRecorded`) and the board's
cleared set as `_cleared_ids_display()`: _provisional_card (whatever the body's gate), per segment of every turn the six
predicates, _seg_prompt, _split_reminders, the comment strip (the kernel's own line; the dumper refuses a kernel whose
_provisional_card no longer holds it), _seg_placed, _live_replanned, _live_cleared_under, _seg_caption and _text_atoms;
per atom jd._human_prompt_record; per node _card_gone, _dictated_prompt_uuid, _pure_delegation_top (one memo per
session, as the body's), and over the body's subtree of the node (layer 4's children, from the kernel's own lines)
_summary_outrun and _summary_text_anchor for every trail key of the subtree, every line and both stub_ok values, with
layer 3's real walk. It writes `DUMP/layer5.json`; `romp-spike layer5 DUMP` builds the same from the typed structs and
compares value by value. Two self-checks: every landing call again with a memo key, twice (a miss, then a hit), must
answer as without one, with no fault; and one replay build with _provisional_card, _pure_delegation_top,
_summary_text_anchor and _summary_outrun wrapped compares every call the body makes with the oracle's answer for the
same session, node, key, line and stub_ok.

**Result on the worlds: zero differences.**
- Small: 4 sessions, 42 turns, 10 nodes, 1,735 values. Large: 38 sessions, 16,877 turns, 78 nodes, 624,750 values
  (129 ms).
- Build check: small 7 calls (1 provisional card, 5 delegation tops, 1 landing), large 41 (1, 39, 1); 0 unmatched,
  0 mismatches. The body never calls _summary_outrun in either world (no node has a cited anchor). Memo check: 12 of 12
  calls per world agree, 12 hits, 0 faults.
- Why that is not enough. Every segment of both worlds is a human prompt with no marker, command, reminder or
  caption; nothing is cleared, latched or seamed; only 12 landing calls are made per world (2 land on a quote); and 36
  of 38 sessions' provisional card reads captions the dump never recorded (both sides raise `NotRecorded` there).

**The edge set.** `dump_layer5.py --edge-cases` writes `rust/crates/romp-views/tests/fixtures/layer5_edge.json`
(synthetic, 0.57 MB, Python's tables included). Its 425 cases: 172 sessions (every gate of the provisional card,
follow-ups over standing, cleared, absent and replanned targets, every placement phase and drift, retired and
cleared-out placements, captions of every shape, seam tails, every trigger lookup, landings by quote, by fold, by
substantive and stub atoms, identity against uuid, outrun stamps, every latch and anchor of the delegation test) and
253 direct calls (opening sentences, quote location, reminders, postal pairs, BARE_SLASH_RE, FOLLOWUP_RE, the comment
strip, prompt records, outrun, card_gone, placements, captions, the landing over hand-made segments). 10 of them (2
sessions, 8 direct calls) came in the review round, each aimed at a mutant the first 415 missed.

Result: 414 cases load and match on every value (14,985), 0 differences. The other 11 are refused at load, exactly
the cases named REFUSE: Python raises on 3 and answers 8. Python's line trace ran 263 of the 275 lines of the 23
ported functions. The 12 it did not run cannot run in the port's input set: the memo's key, hit and put, the
lazy-body read and its fault (the loader refuses lazy atoms), `if not segs: return None` (em.segments always answers a
segment), and the caption drift index (the replay hands _seg_caption a plain dict, which scans; the index gives the
same answer by design).

**The randomized check.** `python3 tools/rust-spike/dump_layer5.py --fuzz N --seed S` draws sessions from layer 3's
random trees, decorated with prompts carrying follow-up, system, nudge and postal markers, slash commands, reminders,
comments and interrupts, bare-string and `text`-field prompts, assistant prose from a pool of sentences, and stores
whose summaries are opening sentences taken from the tree's own prose and mangled (case, whitespace, the long s, the
Kelvin sign, the dotless i, list markers, paragraphs), whose trails name the tree's segments (some at drifted times),
with coverage stamps around the segments' times, handoffs, latches, userAsk and origin shapes, seams, placements and
captions under every phase and drift, cleared sets and heals; one case in five is a direct call. Any difference or
refusal fails the run, and the same seed makes the same file.
- Result, seeds 1 to 6 at 10,000 cases each (about 9 s apiece): 60,000 cases (48,113 sessions, 11,887 direct calls),
  9,054,772 values, **0 differences**, 0 refused. The same seed makes the same file byte for byte under another hash
  seed, and so does the edge set.
- What they reached: 127,717 turns and 198,142 segments (1,328 seam tails); 6,324 provisional cards (292 resuming,
  140 "Working past", 422 captioned), 38,577 gates that close and 3,212 raises (unrecorded captions, a non-string
  caption, segmentation); 350,600 landing calls (35,432 on a located quote); 129,187 nodes (6,664 dictated anchors,
  21,467 machine ones, 34,091 pure delegations, 13,900 outruns, 3,414 subtrees in a cycle); 143,152 prompt records;
  12,654 reminders, 10,009 follow-ups, 2,011 slash-shaped and 3,202 system triggers, 37,145 placed and 7,268
  cleared-out segments.

**Planted mistakes, each caught.** The six plants are romp-spike's `--plant` options (`dump_layer5.py --fuzz ... --plant NAME` passes only
when the plant is caught). The 72 source mutants were edits to `layer5.rs` and `pyre.rs`, each built, run on all four
inputs and reverted by a scratch script (not committed): the first round's 62, of which 58 are below and 4 are the
equivalent ones named above (M58, N1 to N3), which no check can catch because no admitted input tells them apart, and
the review round's 10 (the last rows). Each cell is differences, then the sessions or cases they fell in. The
edge-set column is the final 425-case set (414 loaded) for the plants (`tests/layer5_edge.rs` checks each is caught
on exactly the cases built for it), for the review's 10, and for the 25 first-round rows the review round rebuilt from
their descriptions (M01, M04, M08 to M15, M18 to M20, M24, M25, M29, M31, M32, M40, M44, M45, M47, M49, M55, M56).
Each rebuild gives its row's first-round cells on the 415-case set and fuzz seed 1, so it is the same edit in effect
(M19 took two tries: coverage from distilledMt alone gives other cells, briefedMt read as 0 gives the row's). Five of
them catch more on the 425-case set: M10 (the 160-code-point span), M19 (the missing trail key), and M40, M47 and M49
(the seam tail's segment rows and card). The other 33 rows are the 415-case run. Read against the 10 added cases (not
rebuilt), none of their edits changes an answer there: the cases hold no list marker or paragraph break, no settle
reply or API error, no string-content or attachment prompt, interrupt, command, slash text, comment, unclosed or
spaced reminder, caption, unrecorded captions, placement, cleared node, follow-up, latch or promptUuid, no quote that
only the regex finds, and no postal value that runs into a `-->` (N4). Added cases can only add differences.

| Mistake | Small | Large | Edge set (414) | Fuzz, seed 1 (10,000) |
|---|---|---|---|---|
| `--plant lowercase` (`re.I` as Rust's lowercase) | 0 | 0 | 28 in 9 | 797 in 247 |
| `--plant em-text` (the quote searched in em._text_of) | 0 | 0 | 3 in 1 | 385 in 77 |
| `--plant uuid-exclude` (the turn's atoms excluded by uuid) | 0 | 0 | 14 in 5 | 27 in 6 |
| `--plant outrun-float` (no `int()` of the coverage) | 0 | 0 | 2 in 2 | 729 in 686 |
| `--plant prompt-bytes` (`prompt[:140]` in UTF-8 bytes) | 0 | 0 | 1 in 1 | 103 in 103 |
| `--plant idle-tail` (only a trailing idle span closes the turn) | 0 | 0 | 1 in 1 | 250 in 240 |
| the exact find is skipped (the regex alone) (M01) | 0 | 0 | 2 in 1 | 96 in 37 |
| words joined by exactly one whitespace (no `\s+` run) (M02) | 0 | 0 | 4 in 2 | 476 in 142 |
| the match's `\s+` takes ASCII whitespace only (M03) | 0 | 0 | 4 in 2 | 408 in 124 |
| the quote splits on ASCII whitespace only (M04) | 0 | 0 | 6 in 3 | 404 in 126 |
| paragraphs split at consecutive newlines only (no `\s*` between) (M05) | 0 | 0 | 1 in 1 | 0 |
| list-marker digits ASCII only (M06) | 0 | 0 | 6 in 3 | 1,086 in 300 |
| a list marker needs no whitespace after it (M07) | 0 | 0 | 4 in 4 | 58 in 26 |
| the sentence cut searches from 0, not past 12 (M08) | 0 | 0 | 6 in 6 | 1,483 in 462 |
| the cut's lookahead takes ASCII whitespace only (M09) | 0 | 0 | 4 in 4 | 136 in 55 |
| the 200 cap counts UTF-8 bytes (M10) | 0 | 0 | 2 in 2 | 1 in 1 |
| the located span is not cut at 300 (M11) | 0 | 0 | 2 in 1 | 0 |
| the turn's other atoms searched before the segment's (M12) | 0 | 0 | 2 in 1 | 146 in 35 |
| the quote search runs oldest first (M13) | 0 | 0 | 18 in 6 | 472 in 115 |
| the substantive fallback takes the first, not the newest (M14) | 0 | 0 | 2 in 1 | 1,466 in 338 |
| the stub is the first text atom, not the last (M15) | 0 | 0 | 4 in 4 | 806 in 326 |
| _text_atoms keeps the settle reply (M16) | 0 | 0 | 7 in 2 | 1,465 in 671 |
| _text_atoms keeps API errors (M17) | 0 | 0 | 2 in 1 | 1,571 in 656 |
| outrun compares with >= (an equal stamp outruns) (M18) | 0 | 0 | 4 in 2 | 634 in 571 |
| outrun ignores briefedMt (M19) | 0 | 0 | 5 in 5 | 1,211 in 1,071 |
| outrun has no zero-coverage return (M20) | 10 in 3 | 78 in 37 | 32 in 20 | 7,466 in 4,060 |
| a string prompt record is stripped (M21) | 0 | 0 | 47 in 47 | 902 in 788 |
| the interrupt record counts as dictated (M22) | 0 | 0 | 96 in 49 | 413 in 375 |
| an attachment's nudge marker is not checked (M23) | 0 | 0 | 48 in 46 | 219 in 188 |
| a kind match does not consume what it spans (postal findall) (M24) | 0 | 0 | 49 in 47 | 0 |
| any kind binds (no _POSTAL_KINDS check) (M25) | 0 | 0 | 1 in 1 | 7 in 7 |
| the latch "scheduled" does not exempt (M26) | 0 | 0 | 1 in 1 | 9 in 9 |
| an unresolvable anchor does not fail open (None suppresses) (M27) | 0 | 0 | 3 in 3 | 149 in 140 |
| an origin dict does not skip the latch (M28) | 0 | 0 | 1 in 1 | 15 in 15 |
| one handoff leaf is enough (any, not all) (M29) | 0 | 0 | 2 in 2 | 603 in 524 |
| the prompt index keeps the last atom per uuid (M30) | 0 | 0 | 20 in 13 | 305 in 248 |
| no trigger: atoms[0], not the first atom without a uuid (M31) | 0 | 0 | 4 in 2 | 654 in 502 |
| _seg_prompt searches for a trigger of "" (M32) | 0 | 0 | 3 in 1 | 647 in 193 |
| BARE_SLASH_RE's `\w` as Rust's is_alphanumeric (M33) | 0 | 0 | 4 in 3 | 165 in 139 |
| BARE_SLASH_RE's `$` ignores a final newline (M34) | 0 | 0 | 1 in 1 | 0 |
| the comment strip is greedy (the last `-->`) (M35) | 0 | 0 | 1 in 1 | 150 in 141 |
| a reminder seam with a blank line rejoins with one newline (M36) | 0 | 0 | 2 in 2 | 29 in 18 |
| an unclosed reminder is dropped (M37) | 0 | 0 | 1 in 1 | 809 in 757 |
| no drift match for placements (exact only) (M38) | 0 | 0 | 8 in 7 | 741 in 630 |
| _card_gone checks the node only, not its ancestors (M39) | 0 | 0 | 6 in 4 | 1,319 in 804 |
| an absent caption reads null, not "" (M40) | 11 in 1 | 376 in 1 | 387 in 145 | 24,897 in 5,537 |
| captions read after the system check (M41) | 0 | 0 | 1 in 1 | 15 in 15 |
| the follow-up resurrect ignores whether the turn is open (M42) | 0 | 0 | 1 in 1 | 428 in 428 |
| an absent follow-up target counts as gone without clear evidence (M43) | 0 | 0 | 1 in 1 | 43 in 43 |
| the seam text is not cut at 100 (M44) | 0 | 0 | 1 in 1 | 1 in 1 |
| an empty seam text is kept (no "a completed goal") (M45) | 0 | 0 | 2 in 2 | 5 in 5 |
| a command that put the model to work gets no card (M46) | 0 | 0 | 2 in 2 | 28 in 28 |
| judging is the turn being open (inverted) (M47) | 1 in 1 | 1 in 1 | 68 in 68 | 1,043 in 1,043 |
| a non-string caption is formatted, not a TypeError (M48) | 0 | 0 | 6 in 6 | 6 in 6 |
| the card's t is the turn's, not the held segment's (M49) | 0 | 0 | 28 in 14 | 1,080 in 540 |
| the placed gate resurrects on an ended turn too (M50) | 0 | 0 | 1 in 1 | 32 in 32 |
| re.I without re._casefix's extra cases (M51) (the table test passes) | 0 | 0 | 21 in 7 | 797 in 247 |
| cased read from Rust's is_lowercase/is_uppercase (M52) (the table test fails) | 0 | 0 | 4 in 2 | 20 in 4 |
| the human branch skips the interrupt test for a seg (jd._seg_human) (M53) | 0 | 0 | 94 in 47 | 383 in 334 |
| a falsy command flag counts (command key present) (M54) | 0 | 0 | 3 in 1 | 1,472 in 1,152 |
| the located offset counts UTF-8 bytes (M55) | 0 | 0 | 2 in 2 | 13 in 13 |
| the sentence cut starts past 12 bytes, not 12 code points (M56) | 0 | 0 | 1 in 1 | 43 in 21 |
| list-marker digits are any numeric character (is_numeric) (M57) | 0 | 0 | 1 in 1 | 915 in 258 |
| em.postal_pairs' value group is greedy (N4) | 0 | 0 | 1 in 1 | 1 in 1 |
| the stub tier takes the rest of the turn's last stub before the segment's (R09) | 0 | 0 | 1 in 1 | 259 in 105 |
| `int()` floors instead of truncating (R40) | 0 | 0 | 1 in 1 | 10 in 10 |
| a trail key missing from seg_best is skipped, not read as 0 (R43) | 0 | 0 | 1 in 1 | 4 in 4 |
| an empty userAsk `{}` does not exempt (truthiness, not isinstance) (R48) | 0 | 0 | 1 in 1 | 20 in 19 |
| a seam tail says "Working past" even when its prompt is not empty (R52) | 0 | 0 | 1 in 1 | 2 in 2 |
| the sentence cut starts past 13 code points, not 12 (R70) | 0 | 0 | 1 in 1 | 175 in 62 |
| the located span is cut at 300 UTF-8 bytes (R76) | 0 | 0 | 1 in 1 | 0 |
| a second kind rebinds an id that already has one (R33) | 0 | 0 | 1 in 1 | 1 in 1 |
| the quote is stripped with Rust's `trim` (U+001C kept) (R37) | 0 | 0 | 2 in 1 | 0 |
| a closed reminder's text is stripped with Rust's `trim` (R65) | 0 | 0 | 1 in 1 | 0 |

The worlds catch 3 of the 74: a zero coverage no longer stops _summary_outrun (every node with a trail outruns), an
absent caption read as null (s004's recorded captions are `{}`, so every segment row of it changes), and `judging`
inverted (s004's provisional card). The edge set catches every row. Fuzz seed 1 catches every row but seven: the
paragraph split (M05: only a list marker whose `\s+` would run across a whitespace-only line into the next paragraph
shows it), the 300 cut (M11: no fuzz span reaches 300 code points), the postal findall's consumption (M24: the fuzz
makes no kind marker whose value runs into an id marker) and `$` before a final newline (M34: _seg_slash_shaped strips
its text, so only a direct call on an unstripped line shows it, and the fuzz drew none), and three of the review's:
the 300 cut in bytes (R76: no fuzz span is over 150 code points of two-byte text) and Rust's `trim` on a quote or a
reminder (R37, R65: they show only where a U+001C to U+001F sits at the edge of a quote or of a reminder's text, and
seeds 1 and 101 put none there). M52, the case table read from
Rust's `is_lowercase` and `is_uppercase`, is caught by the edge set through the titlecase `ǅ` (cased to sre, neither
case to Rust) and fails `tests/layer5_edge.rs`'s every-code-point table test; M51 (no extra cases) passes that test, as
it changes no table, and fails the comparisons.

The first rounds of this table were weaker, and each check found what the other missed. The edge set missed the
paragraph split (its multi-paragraph line cut at the first line anyway), the cut's Unicode lookahead (its stops sat
inside the first 12 code points), the tier order (no quote in both the segment and the rest of its turn) and the
titlecase case table; cases were added for each, and each now fails on the cases added for it. The fuzz missed the follow-up
gate on a closed turn (no follow-up named a cleared card) and the case table; the generator now aims follow-ups at
cleared or cleared-logged cards on open and closed turns and draws the titlecase digraph. The cut's `$`, which the
notes' reading suggested, turned out to be equivalent (M58).

**The review round.** The review had no must-fix. Its should-fix items, all applied:
- Six semantics the notes flag were caught only by the fuzz (R09, R40, R43, R48, R52, R70), and four by no committed
  check: R76, and three nits that only direct calls or the oracle's segment rows show (R33, R37, R65). Each now has an
  edge case, and each mutant gives 0 on the old 415 cases and fails the new set on exactly its added case:

| Mutant (the review's number) | Edge set, 415 | Edge set, 425 | The case that catches it |
|---|---|---|---|
| the stub tier prefers the rest of the turn (R09) | 0 | 1 in 1 | turn [u1, c1 "tiny one", u2, c2 "tiny two"], the segment atoms 0 and 1, line "Nothing matches.", stub_ok (Python: c1) |
| `int()` floors (R40) | 0 | 1 in 1 | _summary_outrun with distilledMt -2.5, briefedMt -7, followupAt -2.5 (Python: False) |
| a missing trail key is skipped (R43) | 0 | 1 in 1 | distilledMt -5, briefedMt -3, followupAt -10, a trail key not in seg_best (Python: True) |
| an empty userAsk does not exempt (R48) | 0 | 1 in 1 | a top with userAsk `{}` over two handoff leaves, no parse (Python: not pure) |
| a seam tail with a prompt says "Working past" (R52) | 0 | 1 in 1 | an open turn [u1 "ask", a1, t1, n1 "background note" authored system at 106, t2, a2], a seam at 103 on the head (Python: the card says "background note") |
| the cut starts past 13 (R70) | 0 | 1 in 1 | `_opening_sentence("abcdefghijkl. rest of it here.")` (Python: "abcdefghijkl.") |
| the span cut at 300 bytes (R76) | 0 | 1 in 1 | the landing on a reply of "é" * 159 + "." with that line (Python keeps all 160 code points, 320 bytes) |
| a second kind rebinds (R33) | 0 | 1 in 1 | postal_pairs of an id `a`, a question kind, then a delegate kind (Python: [[a, question]]) |
| the quote trimmed by Rust (R37) | 0 | 2 in 1 | `jd._locate_quote("\x1cab", "\x1cab")` (Python: [1, "ab"]) |
| a reminder trimmed by Rust (R65) | 0 | 1 in 1 | `_split_reminders("a<system-reminder>\x1cctx\x1c</system-reminder>b")` (Python: ["ab", ["ctx"]]) |

- R76 is the one claim the header made with no mutant behind it: M11 pinned only that a cut exists, and the edge set's
  long span was ASCII. The case makes the claim true instead of rewording it. A real card can show the difference: the
  anchorQuote of a non-ASCII opening sentence of 151 to 200 code points.
- R09, R40, R43, R48, R52, R70 and R33 also fail fuzz seed 1; seed 101, the review's, catches the first six. R76, R37
  and R65 fail neither seed.
- The memo claim was wrong across builds. Reproduced with the real kernel functions (kernel._segs_seam and
  _summary_text_anchor, in a scratch script): one segment [u1, a1 carrying the line, t1, a2, t2 at 107, a3 at 108
  carrying the line and more]; build 1, with the memo key `(fsid, nid, head key)`, lands on a3. A seam at 105 on the
  head's key then splits off [t2, a3]; the head keeps its key and the turn its end. Build 2's call hits the memo and
  answers a3, while the same call without the memo answers a1, the head's own atom. Within one build, and in the
  spike's fresh-process comparisons, nothing changes; the wording is fixed in `layer5.rs`, above, in "What layer 6
  needs" and in step S6. It is also a Python-side stale landing (after the seam, a click on the head's brief lands on a
  reply that now belongs to the tail), flagged for lczh in the round's report; nothing is filed upstream.
- Nits: the `ß` sentence now names the reading that failed (the notes' Kelvin example); S4.4's two pointers to
  "Carried forward for layer 6" now point at "What layer 6 needs"; _brief_landing's last resort is described there by
  its real order (the kernel docstring's "the newest trail segment's work anchor" is loose).
- Not done: the fuzz generator is unchanged, so R76, R37 and R65 stay caught by the edge set alone. A generator change
  redraws every seed and would void the table's fuzz column for the 33 first-round rows that were not rebuilt (their
  scratch mutants are gone).

Tests: 17 new, 148 pass in the workspace (the review round added cases, not tests): 7 unit tests in `layer5.rs`, 3 in
`pyre.rs`, 1 in `tree.rs` (the `text` refusal), `tests/layer5_edge.rs` (5: every case answers as Python does, each
plant is caught on exactly the cases built for it, every code point against Python's tables, the line trace's missed
lines, the schema) and 1 in romp-spike for the comparison's messages.

What this feeds: layer 6's `_brief_landing` calls _summary_outrun and _summary_text_anchor with the walk's maps, the
card loop calls _pure_delegation_top per top, and the session's tail calls _provisional_card; "S4.6 as built" below
has the gates and the order as built.

## S4.6 as built (2026-11-26)

**The problem.** Layers 1 to 5 answer the small questions; layer 6 is the function that asks them and assembles the
answer: kernel._feed_session_entry, the per-session body of build_feed. For one session it decides which goals become
cards, which column each card sits in (working, needs you, completed), what time and badges it wears (awaiting, blocked,
stalled, interrupted, retrying, nudge failed), where a click on its title, its summary and each tree row lands, and
which placeholder a session gets when no card covers it. The answer is one dict per session, which build_feed memoizes
as a JSON string. That string is the strongest oracle the spike has: the S2 dump keeps Python's real one for every
session of both worlds (`oracle/entries/<sid>.json`). So the check here is the bytes themselves, not values picked out
of them. What can go wrong in a port is everything the earlier layers guarded against, now in combination: the order
the body calls _node_anchor_uuids in (it changes what later lookups serve), Python's `max` and `min` keeping the first of
equal values and their int/float tag, `x or y` against `.get(k, d)` against `is None`, the order of a dict's keys
(including three conditional keys in the middle of the card), and which body reads raise, since a raise fails the whole
entry.

`layer6.rs` ports, line for line, kernel._feed_session_entry with its closures _note_peers, _subtree, _closure_done,
_fsubmax, _closure_blocked, _block_check_floor, _brief_landing and flatten, and the three builders it calls that no
earlier layer ported: _card_warn_rows (the debug-mode join), _blocked_placeholder and _awaiting_card. `Build` holds what
one build reads across sessions (the snapshot's board, live map, names, flags and stores), and `Build::entries` runs
every alive session in order with one anchor table, as a build does. What `_feed_session_key` hands the body through ctx
is taken as the S2 replay's key takes it (the tree, _session_working over it, the recorded `_interrupting` answer, and
`_closer_pending` under the key's gate). Every helper answer the body needs that the snapshot never recorded raises
`NotRecorded`, as the replay harness raises, so a port that asks a helper Python did not ask fails loudly. What the notes
flagged, as built:
- _node_anchor_uuids runs inside flatten, once per flattened node in flatten's order (the card top, then its children by
  `_fsubmax` descending, stably, each recursively before the next). The anchor table is carried across a build's
  sessions in alive order. A node never flattened writes nothing: a hidden node, a cleared top, a pure-delegation top,
  a parentId-cycle member, an orphan, and a session whose entry raises before its flatten (the card literal reads the
  awaiting tasks before the tree, so an unrecorded tasks answer raises first). The `anchor-store-order` plant calls it
  per node in store order instead, as layer 4's harness did; the worlds cannot tell (no id repeats), the edge set
  catches it on 7 cases.
- `max` and `min` keep the first of equal values and its tag: _fsubmax (`mt`, else `t`, else 0, against each child), the
  completed card's `max([settledAt or 0] + dts)`, the blocked card's `max(bts)`, `max(disp_t, followupAt or 0)`, the
  delegation's `max([t or 0 ...] or [0]) or None`. `min` in _block_check_floor cannot show its tie: `int()` makes every
  candidate an int. `int()` truncates toward zero: the watermark (200.5 reads 200, so a reply at 200.3 re-judges) and
  the nudge count (2.7 reads 2).
- `.get(k, d)`, `or` and `is None`: a row's `mt` is `nd.get("mt", nd["t"])` (a present 0 stays 0); `_born or None` (an
  empty born dict is null); `relayCarried`, `briefParts`, `summaryParts`, `warns`, `failLog` and `stallSummary` are
  `or None` while `background`, `followupPending` and `summaryQuote` pass through raw; `followupAt or None`;
  `awaitingAt or None` for the stamp's since; `jerr.get("note") or ""`; the landing's quote is `q or None` while the
  card's cited `summaryQuote` is raw. `_sa_u is None and ps is None` serves the raw citation only without a parse.
  _brief_landing's tiers test `u is None`, which the notes suggested a "" uuid would show; it cannot: _text_atoms admits
  only atoms with a truthy uuid, so every tier yields a truthy uuid or None, and `not u` is equivalent (mutant M52).
- Order: `reads` has `usage` before `nudges`; `handoffTo`, `delegTracked` and `satellite` sit between `origin` and
  `followupPending`; `peers` is sorted; the card loop runs over `children[None]` after the floor un-nest, which appends a
  healed or hidden top at the end and removes a healed one from its host's list (its host keeps its now shorter list,
  which no reader can tell from a removed key).
- `isinstance(n, int)` is true of a bool, so an awaiting count of `true` is kept (an int must also be positive on a card;
  on the awaiting placeholder 0 and negatives pass); Python's `str.upper()` of the placeholder's first code point is the
  full case mapping at Python's Unicode version, 15.0 (`ß` gives `SS`, and `ɤ` stays `ɤ`), read from a table generated
  from Python (`pystr::upper`; Rust's `char::to_uppercase` follows Unicode 18.0, see "The review round" below).
- Raises that fail the entry, each matched by name and message: `nodes[nid]["t"]` under the wait graph, a row's
  `nd["text"]` and `nd["t"]` (after its anchors are written and its landing resolved), a stall record's `why` and
  `since`, a session-awaiting answer's `why` and `kind`, segmentation in the placeholders, a current ask's truthy
  `question` or `header` that is not a string (AttributeError from `.strip()`), and every recorded answer the body
  reaches unrecorded. Two recorded reads sit inside a `try` in Python and read False or None when unrecorded:
  `_interrupted_by` and the live backend's `current_ask` (only the call: the ask's question and header are read after
  the `try`).
- Left out, as layers 3 and 5 left them out: the held aggregate (_FEED_SEG_HELD) and _SUMMARY_ANCHOR_MEMO, whose seam
  staleness (S4.5's "The review round") only a warm build after a seam can meet; the oracles run fresh
  processes and clear both. `lg.mark_refused` (a write to the login registry, no part of the entry) is not ported.
  `faults` is the walk's `seg_turn` faults, always 0 on a tree the loader admits.

Loader changes (round trip and layers 1 to 5 re-run on both worlds, every number as before): nodes type the 13 keys
layer 6 reads (`background`, `followupPending`, `summaryQuote`, `briefParts`, `summaryParts`, `relayCarried`,
`stallSummary`, `warns`, `failLog`, `umbrella` and `serving` as any JSON, `blockCheckT` as a number or null,
`summaryAnchors` as any JSON that layer 6 checks), handoffs and origins type `tracked` and `peerHost`, and
`NODE_UNPORTED` and the handoff and origin lists are empty. The log-row list (`a`, `q`, `login`, `fsid`) was an artifact
of the ast scan's grouping by receiver name: those are reads of a summaryAnchors item, a debug row and build_feed's
judge-auth entries, never of a log row, so it is empty too. The recorded answers gain `bgOwnerTops` and `bgServiceDescs`
(`tools/rust-spike/dump_world.py` now records _bg_owner_tops and _bg_service_descs, which only a session with live
background tasks reaches; no world does, and both worlds re-dumped byte-identical but for `run.json`) and `currentAsk`
(recorded by dump_layer6.py's harness only). `layer6::validate` refuses, each with its path, the shapes found that layer
6 would read differently from Python and does not mirror; none is in either world: a session without a `store` key,
truthy `flags` that are not a dict, a wait-graph entry that is not a dict with a number `since`, a truthy stall record
or judge-auth entry that is not a dict, a judge-auth entry with a truthy `login` (build_feed resolves its label through
the SDK, which the replay does not) or a non-string label, debug rows that are not a list of dicts or hold a non-string
`seg` or an unhashable `goal` item, non-string `confirming` items, a truthy `summaryAnchors` that is not a list or holds
a truthy item without `a`, and recorded answers of a shape the body would `str()` or iterate differently (a
session-awaiting `why` that is not a string, its `items` not a list, a peer sid that is not a string, owner tops not
`{since: number, descs: [str]}`, a truthy current ask that is not a dict). These are the shapes found, not every shape
by construction: the review found a current ask whose question or header is truthy and not a string, which the port now
mirrors with Python's raise ("The review round" below).

**The oracle.** For the worlds it is the dump's own `oracle/entries/<sid>.json`, compared byte for byte. For synthetic
worlds, `tools/rust-spike/dump_layer6.py` answers each case with one replay build_feed over it (dump_world's Replay
harness, the fold stubbed: layer 7's), takes every entry from the memo put (the exact string build_feed memoizes, kept
with its SHA-1), or the raise that failed the session (the harness's DumpError as `NotRecorded` with the helper and sid).
_node_anchor_last and the build memos are cleared first, so a case starts as a fresh process does. Its own check:
`dump_layer6.py DUMP` runs this harness over a dump's inputs, and its entries equal `oracle/entries/` on 4 of 4 and 38 of
38 sessions. `romp-spike layer6` compares the bytes and, on a difference, names the first differing values by path.

**Result on the worlds: zero differences.**
- Small: 4 sessions, 7 cards, 10 tree rows, 17,470 entry bytes (755 values). Large: 38 sessions, 41 cards, 78 tree
  rows, 114,100 entry bytes (5,107 values).
- Why that is not enough. The worlds run 424 of the 592 lines of the body and its builders. No id repeats, nothing is
  hidden, healed, cleared, waited on, stalled, latched, nudged or floored, no card has a stamp, a delegation, an origin,
  a citation or a placeholder but s004's provisional card, and none of the six plants moves either world.

**The edge set.** `dump_layer6.py --edge-cases` writes `rust/crates/romp-views/tests/fixtures/layer6_edge.json`
(synthetic, 1.7 MB; it regenerates byte for byte under another hash seed). Its 310 worlds (283 as first built, 27 from
the review round) of one to three sessions: every
session-level read (hide by the session's flags and by a peer row's, the cold warm ask, downtime, every interrupted
answer, interrupting, retrying, the judges in flight and the closer, a faulted store, every recorded answer unrecorded
where the body reaches it), the heal and the hidden set (counts that include absent nodes, born records, every floor's
un-nest), the permission, picker, api-error (each flag, the login label, the offer) and judge-auth floors, debug rows
(by goal, goal list and placement phase, the cap of 20), the card loop (cleared tops, pure delegation, the wait graph,
owned tasks, the service chip, stamps, delegations, tracked handoffs and satellites, origins, handoff cards, every card
time, re-check and re-judging, nudges, stalls, the pass-through keys, the serving fold), flatten (recency order and
ties, the anchor table within and across sessions, rows), every landing tier, the three placeholders, peers and reads;
and 21 worlds the loader must refuse.

Result: 289 worlds load and give all 305 entries byte for byte (334 cards, 357 tree rows, 31,321 values), 0 differences;
31 entries are the same raise in both, 3 are a hidden session's null. (As first built: 262 worlds, 276 entries, 28,878
values, 25 raises.) The other 21 worlds are refused at load, exactly
the cases named REFUSE. Python's line trace ran 585 of the 592 lines. The 7 it did not run cannot run on an admitted
input: _closure_done's absent node and _block_check_floor's memo hit (every caller passes a node of the tree, visited
once), and the two placeholders' parse failures inside their `try` (the replay serves the same tree as the parse).

**The randomized check.** `python3 tools/rust-spike/dump_layer6.py --fuzz N --seed S` draws worlds of one to three
sessions. Each session's tree and store come from layer 5's generator (its sid renamed; node ids repeat across sessions,
so the anchor table is shared), then every node gets what layer 6 reads (text, t and mt of both tags with ties and zeros,
flags, log rows of every kind and source, stamps, watermarks, citations, the pass-through keys, serving, born records,
tracked handoffs and origins), the store a status map, a focus and a confirming set, the session random recorded answers
(one in 150 dropped), flags and a live row, and the board cleared sets, waits, stalls, latches, judges in flight, nudge
records, debug rows and downtime. Any difference or refusal fails the run, and the same seed makes the same file byte for
byte under another hash seed.
- Result, seeds 1 to 6 at 4,000 worlds each (about 25 s apiece): 24,000 worlds, 40,058 entries (34,536 cards, 42,303
  tree rows), 3,813,717 values, **0 differences**, 0 refused. 4,112 entries are the same raise in both (KeyError on
  `text` 305, `t` 298, `uuid` 250, `session_id` 237, `trigger` 211, `since` 144; NotRecorded for a peer store 739, the
  captions 454, and the 13 recorded helpers 1,474) and 4,004 are a hidden session's null. Python's line trace on seeds 1
  to 3: the same 585 of 592 lines as the edge set.

**Planted mistakes, each caught but one equivalent mutant.** The six plants are romp-spike's `--plant` options (`dump_layer6.py --fuzz ... --plant
NAME` passes only when the plant is caught; `tests/layer6_edge.rs` checks each is caught on exactly the cases built for
it). The 60 source mutants were edits to `layer6.rs`, each built, run on all four inputs and reverted by a scratch script
(not committed). Each cell is differences, then the sessions (worlds) or cases they fell in.

| Mistake | Small | Large | Edge set (262) | Fuzz, seed 1 (4,000) |
|---|---|---|---|---|
| `--plant anchor-store-order` (_node_anchor_uuids per node in store order, not from flatten) | 0 | 0 | 16 in 7 | 169 in 97 |
| `--plant fsubmax-last` (_fsubmax keeps the last of equal values) | 0 | 0 | 12 in 3 | 322 in 89 |
| `--plant mt-or` (a row's mt read as `mt or t`) | 0 | 0 | 3 in 2 | 566 in 495 |
| `--plant land-first-equal` (the landing's newest trail segment keeps the first of equal times) | 0 | 0 | 2 in 2 | 12 in 6 |
| `--plant peers-unsorted` (the peers in read order, not sorted) | 0 | 0 | 6 in 3 | 282 in 131 |
| `--plant born-empty-kept` (an empty born record ships as `{}`) | 0 | 0 | 4 in 3 | 290 in 274 |
| flatten orders children oldest first (M01) | 0 | 0 | 238 in 8 | 14,329 in 401 |
| equal-recency children in reverse store order (M02) | 10 in 1 | 10 in 1 | 243 in 19 | 2,433 in 85 |
| _fsubmax reads t before mt (M03) | 12 in 2 | 216 in 36 | 51 in 9 | 14,398 in 1,578 |
| `reviewedEarlier` needs a done strictly before the boundary (M04) | 0 | 0 | 1 in 1 | 24 in 21 |
| the heal's born record beats the node's own (M05) | 0 | 0 | 2 in 1 | 21 in 20 |
| a handoff row's `who` is the whole sid, not its first 8 code points (M06) | 0 | 0 | 3 in 3 | 331 in 300 |
| `parked` shows on any status (M07) | 0 | 0 | 2 in 2 | 6 in 6 |
| a completed card counts rolled-down descendants (M08) | 0 | 0 | 2 in 1 | 2 in 1 |
| a completed card takes the candidate even when it is 0 (M09) | 0 | 0 | 32 in 16 | 750 in 350 |
| a working card ignores its follow-up stamp (M10) | 0 | 0 | 6 in 3 | 426 in 193 |
| re-judging always reads the top's own watermark (M11) | 0 | 0 | 2 in 2 | 0 |
| _block_check_floor without `int()` (M12) | 0 | 0 | 1 in 1 | 0 |
| _block_check_floor takes the newest watermark (max) (M13) | 0 | 0 | 2 in 2 | 0 |
| a diary row at the nudge's own time retires the failure (`>=`) (M14) | 0 | 0 | 1 in 1 | 0 |
| the nudge count is not `int()` (M15) | 0 | 0 | 1 in 1 | 187 in 162 |
| a stall on a done-confirming card floors it (M16) | 0 | 0 | 1 in 1 | 25 in 9 |
| an in-flight stall does not light the swirl (M17) | 0 | 0 | 1 in 1 | 81 in 74 |
| `waitingOn` on any column (M18) | 0 | 0 | 2 in 2 | 164 in 145 |
| the wait counts only goals minted strictly before the question (M19) | 0 | 0 | 2 in 2 | 89 in 58 |
| an awaiting count of `true` is dropped (M20) | 0 | 0 | 1 in 1 | 75 in 64 |
| delegation names not deduplicated (M21) | 0 | 0 | 1 in 1 | 0 |
| the delegation's since keeps the last of equal times (M22) | 0 | 0 | 1 in 1 | 0 |
| a registry-named sender keeps the plant-time host (M23) | 0 | 0 | 1 in 1 | 51 in 50 |
| the satellite hides on a needs-you card too (M24) | 0 | 0 | 1 in 1 | 0 |
| the card's quote is always the landing's (the cited span dropped) (M25) | 0 | 0 | 2 in 2 | 14 in 14 |
| the cold fallback serves the citation even with a parse (M26) | 0 | 0 | 1 in 1 | 739 in 550 |
| the completed tail keeps the last of equal times (`>=`) (M27) | 0 | 0 | 2 in 1 | 0 |
| the latest-prose walk compares the time before substantive (M28) | 0 | 0 | 2 in 1 | 52 in 45 |
| the last resort reads each trail from its start (M29) | 0 | 0 | 1 in 1 | 5 in 5 |
| the landing keeps a falsy quote (no `q or None`) (M30) | 0 | 0 | 1 in 1 | 1 in 1 |
| the nudges read before the usage read (M31) | 0 | 0 | 21 in 21 | 594 in 565 |
| the heal count counts only nodes in the store (M32) | 0 | 0 | 1 in 1 | 0 |
| the un-nest leaves the top under its host too (M33) | 0 | 0 | 6 in 2 | the run dies (signal 9) |
| an unrecorded interrupted answer reads true (M34) | 0 | 0 | 1 in 1 | 7 in 6 |
| the awaiting card capitalizes ASCII only (M35) | 0 | 0 | 3 in 3 | 171 in 169 |
| the long question's cut is not right-stripped (M36) | 0 | 0 | 1 in 1 | 0 |
| warn rows keep the first 20, not the last (M37) | 0 | 0 | 20 in 1 | 0 |
| warn rows ignore the `#live` phase (M38) | 0 | 0 | 19 in 1 | 4 in 4 |
| an auth error outranks a spend limit in the badge's text (M39) | 0 | 0 | 1 in 1 | 0 |
| the judge-auth floor yields only to the api floor (M40) | 0 | 0 | 1 in 1 | 5 in 5 |
| a needs-you serving mirror folds too (M41) | 0 | 0 | 2 in 1 | 112 in 56 |
| a whitespace summary counts for `summaryStale` (M42) | 0 | 0 | 1 in 1 | 9 in 9 |
| `doneConfirming` on any column (M43) | 0 | 0 | 1 in 1 | 88 in 81 |
| a provisional card despite a live floor (M44) | 0 | 0 | 5 in 5 | 208 in 206 |
| a completed card's time keeps the last of equal values (M45) | 0 | 0 | 4 in 2 | 2 in 1 |
| a blocked card's time keeps the last of equal values (M46) | 0 | 0 | 2 in 1 | 0 |
| a working card's follow-up floor takes the stamp on a tie (M47) | 0 | 0 | 2 in 1 | 130 in 63 |
| the card's relayNote passes a falsy value through (no `or None`) (M48) | 0 | 0 | 1 in 1 | 238 in 229 |
| the card's followupAt passes 0 through (no `or None`) (M49) | 0 | 0 | 1 in 1 | 155 in 153 |
| the stamp's since keeps a falsy anchor (no `or None`) (M50) | 0 | 0 | 1 in 1 | 74 in 73 |
| the judge-auth text keeps a null note (no `or ""`) (M51) | 0 | 0 | 1 in 1 | 0 |
| the landing's tiers test truthiness, not `u is None` (equivalent) (M52) | 0 | 0 | 0 | 0 |
| the `satellite` key after `followupPending` (M53) | 0 | 0 | 1 in 1 | 2 in 2 |
| an unrecorded current ask raises (outside the `try`) (M54) | 0 | 0 | 2 in 2 | 2 in 2 |
| the anchor table starts empty for each session (M55) | 0 | 0 | 7 in 3 | 173 in 97 |
| a node without `t` under the wait graph reads 0, no KeyError (M56) | 0 | 0 | 2 in 1 | 0 |
| a stall record without `since` reads null, no KeyError (M57) | 0 | 0 | 1 in 1 | 19 in 14 |
| the card's briefParts passes a falsy value through (no `or None`) (M58) | 0 | 0 | 1 in 1 | 204 in 196 |
| the card's background drops a falsy value (`or None`) (M59) | 0 | 0 | 1 in 1 | 204 in 187 |
| a row's `text` reads null when absent, no KeyError (M60) | 0 | 0 | 1 in 1 | 54 in 48 |

The worlds catch 2 of the 66: equal-recency siblings in reverse store order (s001's delegating goal has two handoff rows
of one time) and _fsubmax reading `t` before `mt` (every touched node). The edge set catches every row but M52, which
is equivalent: _text_atoms admits only atoms with a truthy uuid, so every landing tier yields a truthy uuid or None, and
`not u` cannot differ from `u is None` on an admitted input. (The review's R44, the delegation's names deduplicated but
not sorted, is equivalent too: layer4::handoff_peer_identities already returns the identities sorted by name, so
`names.sort()` is a no-op and the dedup meets equal names side by side; its scratch mutant gives 0 on both edge sets.)
M33 makes the fuzz run die (leaving a healed top under its
host as well as under the roots lets flatten walk a cycle). Fuzz seed 1 misses 16 rows (M11 to M14, M21, M22, M24, M27,
M32, M36, M37, M39, M46, M51, M52, M56). The generator draws none of their inputs: blocked children with distinct
watermarks around a fractional reply time (M11 to M13), a diary row at exactly the nudge block's time (M14), two peers
under one registry name (M21), equal times of both tags in the delegation, the completed tail or the blocked card (M22,
M27, M46), a tracked satellite on a card that stays needs-you (M24), heal entries for nodes the store lacks (M32), a
long question whose cut lands on whitespace (M36), more than 20 debug rows on one card (M37), an api error with both
`authErr` and `spendLimit` (M39), a judge-auth entry whose note is null rather than absent (M51), and a raise under the
wait graph with the same id in a later session (M56).

The first sweep of the 60 mutants over the then 273-world edge set missed 12. Three of them (M18, M24, M41) were cases
that meant to test a needs-you card but dropped to working by re-judging, since the tree's later plain prompt postdated
the block; a watermark past the reply keeps them needs-you. The others got a world each (M05, M07 twice, M21, M26, M27,
M28, M43, M56, plus a completed card under a wait for M18), 10 worlds in all, and each of the 11 now fails the 283-world
set; M52 is the equivalent one. M36 and M45 to M51 were written against claims in `layer6.rs`'s header that had no
mutant, and the cases they needed (a question cut on whitespace, ties of both tags in three times, a fractional reply
time, falsy pass-through keys, a follow-up stamp of 0) went in before the run.

**Findings beyond the port.**
- The S2 replay's `_cleared_ids_display` answers a set, and build_feed's frame code after the session loop reads it as a
  dict (`_cleared_foreign(cleared)` calls `cleared.get(i)`), so any world with a non-empty cleared set raises there in the
  replay. The body only tests membership, so the entries are right, but layer 7 compares frames: the replay must hand a
  dict (ids to clear times) first. Both worlds' cleared sets are empty, so no dump was wrong.
- The release build makes the large world's 38 entries in 31 to 33 ms of wall time (the command's own clock, ten runs
  on a quiet machine; readings taken while other jobs ran were 40 to 90 ms). Python's derivation from an empty memo took
  87 ms (s2-design section 4), so about 2.7x. It is not a measurement (S6 times process CPU, alternates the two, and
  matches the timed regions), but it is a warning: most of the work is segmenting and walking the 16,877 turns (layers
  2 and 3), the port allocates freely (cloned keys, owned strings, hash maps per derivation), and as written it is short
  of the gate's 5x. S6 should profile before it times.
  **Retracted (S6's review, must-fix 1):** the 2.7x compared two different worlds. The 87 ms was measured by
  `rust/notes/probes/replay_probe.py`, which writes a 4-session world (`write_world(root, 4, ...)`; s2-design section 1
  says the 38-session board was not run), while the 31 to 33 ms is the port's 38-session build. On the same 38 sessions
  the smoke runs put Python's empty-memo derivation at about 220 ms (arm64 3.13) and 350 ms (x86_64 3.12) of process CPU,
  against the port's 21 ms for the same work; the two are compared under "S6 method".

**The review round.** Two must-fix, both applied:
- Python's `str.upper()` follows Python's Unicode version (15.0 for 3.12); the port's `char::to_uppercase` followed the
  toolchain's (18.0, `char::UNICODE_VERSION` on Rust 1.100). Over every code point the two disagree on 76 (U+019B,
  U+0264, U+0277, U+027C, U+1C8A, U+A7CD and 70 more: the case pairs Unicode 16 to 18 added; recounted here with a
  scratch program over Rust's own mapping). So a recorded session-awaiting `why` of `ɤait on the build`, which the loader
  admits, gave the awaiting placeholder the headline `Ꟛait on the build` where Python keeps `ɤait on the build`.
  `pystr::upper` now reads Python's own table, generated the way layer 5 generates its regex tables:
  `dump_layer6.py --tables` writes `pystr_tables.rs` (194 runs of one-code-point mappings and 102 longer ones, such as
  `ß` to `SS`), and `tests/layer6_edge.rs` checks it on every code point against the table Python writes into the
  fixture (1,525 code points change). The same test covers the one other case mapping layer 6 reads, the blocked
  placeholder's `h.lower() != "permission"`, which keeps Rust's lowercase: on every code point Rust lowers to ASCII
  letters only exactly where Python does (53 code points: A to Z, a to z and the Kelvin sign), and to the same letters,
  so that comparison cannot tell the two apart. Two edge worlds: three sessions whose `why` begins with U+0264, U+A7CD
  and U+10D70, and one beginning with the ligature `ﬀ` (Python: `FF`, a two-code-point mapping). The claim "the full case
  mapping (`ß` gives `SS`)" in `layer6.rs` and above held only for letters Unicode 15 already cased; both now name the
  version.
- In kernel._blocked_placeholder only the `current_ask` call sits inside the `try`. `(ask.get("question") or
  "").strip()` and the header's run after it, so a truthy value that is not a string raises AttributeError and fails the
  entry; the port read such a value as "" and rendered the placeholder. It now raises as Python does, with Python's
  message (`'int' object has no attribute 'strip'`, or `bool`, `float`, `list`, `dict`): the question's raise first, and
  the header's even when the question is a string. Eight edge worlds: question 5, header 7, question `["a"]`, a string
  question with a dict header, question `true` with header 2.5, and question 2.5 (six raises), and the falsy non-strings
  that read as empty (question 0 with header `[]`; question `{}` with header " Plan "). The claim that
  `layer6::validate` refuses every shape Python would read differently was wrong. `layer6.rs`, the loader paragraph
  above and this round's commit now say it refuses the shapes found, and that this one is mirrored, not refused. An
  audit of the body's other lenient reads (each `as_str`, `as_object`, `as_array` and `filter_map` against the Python
  line it ports) found no other: each is refused at load (`agentTask` and `born` by the snapshot loader; the typed node,
  log-row and nudge keys; the recorded shapes in `validate`), or reads as Python does (a stall `why` that is a list
  compares unequal to every string in the `WHY_IN_FLIGHT` tuple, without raising).
- The build before this round fails exactly the seven new worlds that raise or begin with a newly cased letter (9
  differences); the build after passes all 310. Put back as scratch mutants, the old upper fails 1 case (3
  differences) and the old ask reading 6 cases, and both give 0 on the 283-world set.

Should-fix, applied:
- 17 edge worlds, one per review mutant the first edge set missed: R06, R29 and R74 (missed by every check, the fuzz
  included), R13 (caught by both worlds and the fuzz, not by the edge set) and the 13 the fuzz alone caught. The review's
  own worlds and mutants were not handed over, so each world was rebuilt from its description with dump_layer6's
  builders, and each mutant as an edit to the fixed `layer6.rs` in a scratch copy. Each mutant gives 0 on the old
  283-world set and fails the new 310-world set on its own case:

| Mutant (the review's number) | Edge set, 283 | Edge set, 310 | The case that catches it |
|---|---|---|---|
| _closure_blocked ignores agent_open (R03) | 0 | 6 in 2 | c1 nodeComplete, blocked and agent-open under a working g1 (Python: both rows read question); R06's world too |
| _block_check_floor ignores agent_open (R06) | 0 | 1 in 1 | status-blocked g1 over c1 nodeComplete, blocked, agent-open, blockCheckT 300; the plain reply at 200 (Python: needs you, not re-judging) |
| agent_open and parked not rebuilt after the un-nest (R08) | 0 | 1 in 1 | r1 nodeComplete, g1 healed under r1 with an open agent task, a permission floor on g1 (Python: r1's row reads done) |
| recheck ignores the hard floors (R12) | 0 | 1 in 1 | status-blocked g1 with followupPending, lastNode g1, apiError {status: 500} (Python: needs you, recheck false) |
| re-judging when the plain reply ties the card time, `>=` (R13) | 0 | 1 in 1 | g1 blocked by a planner row at ev_t 200, the plain reply at 200 (Python: needs you) |
| a diary row's time read as ev_t before at (R25) | 0 | 1 in 1 | the nudge's block row {at 150, ev_t 100}, a planner row {at 120, ev_t 200}, the nudge failed (Python: nudgeFailed) |
| failedAt preferred over the nudge's block row (R26) | 0 | 1 in 1 | the block row at 150, failedAt 100, a planner row at 120 (Python: nudgeFailed) |
| the last block's source read from the first block row (R27) | 0 | 1 in 1 | an interrupt block at 150, a planner block at 160, blockCheckT 250, interrupting recorded (Python: not interrupting) |
| the landing memo key ignores `completed` (R29) | 0 | 1 in 1 | g1 nodeComplete and blocked, status blocked, blockCheckT 300, summary and blockSummary both SUB1, trail [k2, k1] (Python: the card lands on b2, its done row on a1) |
| judging ignores who_working (R36) | 0 | 1 in 1 | an open turn, the board's jactive [sid] (Python: judging false) |
| the stamp's peers unsorted (R43) | 0 | 10 in 1 | awaitingPeers ["s9", PEER] (Python: peer9, then s9) |
| a handoff row wears the session's working bit (R46) | 0 | 1 in 1 | an open turn, dictated g1, handoff child h1 (Python: h1's whoWorking false) |
| the stall note passes a falsy value (R58) | 0 | 1 in 1 | stallSummary "" with a stall record (Python: note null) |
| the awaiting dot ignores who_working (R66) | 0 | 2 in 2 | an open turn and an awaiting stamp on g1 (Python: the entry's awaiting null); R46's world too |
| an open leaf whose handoff is `{}` is not a delegation leaf (R74) | 0 | 1 in 1 | dictated g1, c1 with handoff {} at t 150, h2 a handoff at t 120 (Python: awaiting since 150) |
| the cited quote shipped for an empty cold citation (R76) | 0 | 2 in 1 | no tree, summaryAnchor "", summaryQuote and summaryAnchors set (Python: quote and paragraphs null) |
| a healed floor top un-nested when it is not under its host (R78) | 0 | 3 in 1 | g1 with a born record, healed under a missing r9, lastNode g1, a permission floor (Python: one card, heal 1) |

- R44 is equivalent (the note after the first table). Its scratch mutant gives 0 on both edge sets.
- "What layer 7 needs" said a placeholder's `_ageT` is null. It is null only while the placeholder's `t` is the build's
  clock: the blocked placeholder dates from its held segment and the awaiting one from the last turn's `t` whenever there
  is one. The review also counted the provisional card among the null ones; it is not (its `_ageT` is its held
  segment's `t`, as s004's card shows in both worlds, and so do all 59 provisional cards in the edge set). Reworded.
- Where the port leaves out the body's `_note_peers([handoff_to] if handoff_to else None)`, a comment now says why: the
  handoffTo badge carries `peerSid`, not the `sid` _note_peers reads, so Python's call adds nothing (flatten notes the
  top's own handoff peer when it writes the card's first row). On the Python side that dependency record never fires: a
  dead call, with no effect on the entry, for the bug lane.
- The re-run: fresh dumps of both worlds; the round trip and layers 1 to 6 on both (every number as before); the
  harness's own check (4 of 4, 38 of 38); the layer 2 to 6 edge sets (layer 6: 310 worlds, 289 load and give all 305
  entries byte for byte, 31,321 values, 31 raise in both, 21 refused); `dump_layer6.py --fuzz` seeds 1 to 6 at 4,000
  (40,058 entries, 3,813,717 values, 0 differences, the same numbers as first built: the generator is unchanged); layers
  2 to 5's fuzz at 10,000 per seed, seeds 1 to 6; check_store over both dumps with 6 fuzz seeds; the edge fixture and
  `pystr_tables.rs` regenerate byte for byte (the fixture under hash seeds 7 and 12345). Python's line trace: 585 of
  592 lines on the edge set and on fuzz seeds 1 to 3, as before.
- Not done: the fuzz generator is unchanged, so the two must-fix shapes are pinned by the edge set alone. A scratch run
  with them added to the generator (a `why` starting with U+0264, U+A7CD or `ﬀ`; a current ask with question 5, a list
  header, or question 0 with a header) gave 0 differences on seed 5 at 4,000 worlds (106 of its entries were the new
  AttributeError raises in both). It is not committed because a generator change redraws every seed and voids the
  first table's fuzz column, whose 60 scratch mutants are gone (S4.5's review round made the same call). The first
  commit's message, which repeats the `validate` claim, is not rewritten; this round's commit corrects it.

Tests: 10 new, 158 pass in the workspace: 3 unit tests in `layer6.rs`, 1 in `pystr.rs` (Python's upper at its
Unicode version), the snapshot test rewritten for the typed keys, `tests/layer6_edge.rs` (5: every entry byte for byte
with its SHA-1, each plant caught on exactly its cases, the case tables on every code point, the line trace's missed
lines, the schema) and 1 in romp-spike for the comparison's messages.

What this feeds: layer 7's loop folds these entries (the tint from each card's `_ageT`, the bells, the working and
awaiting dots, the service chip, the serving-fold join, absent sessions); "What layer 7 needs" below.

## What layer 7 needs (the loop, the fold and the cross-session pass)

(Kept as the scope S4.7 was built to; "S4.7 as built" below answers each item.)

Section 2 item 7: the build_feed loop minus memo and key (57255-57320 at this HEAD), _feed_fold_session, _feed_fold_stamp
and _feed_fold_card, cm.age_rgb and ramp (colormap.py), _notify_card_effective, _push_explicit_bell, the handoff
whoWorking stamp, the serving-fold join, and the frame assembled from the passthrough keys. Found while building layer 6:
- The fold reads the entry string, and layer 6's entry is byte-identical to it, so the fold can take layer 6's value
  directly. Every card's and row's `_ageT` is its tint's epoch, the provisional card's included (its held segment's
  `t`). Only the blocked and awaiting placeholders can carry a null `_ageT`, and only while their `t` is the build's
  clock (which the fold restamps): _blocked_placeholder sets `t` and `_ageT` to the last turn's held segment's `t` when
  the session has a segment, and _awaiting_card to the last turn's `t` when it is not null. In the edge set 22 of the 23
  placeholders of those two kinds carry a time; the one null is the blocked placeholder with no turns.
- A session whose derivation raises: build_feed serves its previous entry when the memo holds one, else lists the sid in
  `absentSids` and folds nothing. The spike has no memo, so a raising session is absent. 4,112 of the fuzz's 40,058
  entries raise, so layer 7's fuzz meets this often.
- The replay's cleared set is a Python set where the frame's `_cleared_foreign` reads a dict (the first finding above):
  fix the harness before comparing a frame with a cleared set.
- A handoff row ships `whoWorking: false`; build_feed stamps the recipient's working bit after the session loop, from
  the sids behind `working`. A serving-fold candidate (`servingFolds`) commits only when its tracker row rendered this
  build, else the candidate keeps its own card. Two more producers add cards to `asks` after the loop (parked handoffs
  and notices, "S2 as built"), empty in both worlds.
- The bells: the fold stamps `notify` and `notifyRaw` from `_notify_cards` and each session's raw flag; the body reads
  neither, so layer 6 met no bell. `heal`, `hidden` and `cold` only feed build_feed's log line and the background warm.
- The anchor table carries across a build's sessions in alive order and across builds; with the memo (S6's warm
  builds), a hit runs no body and writes nothing to the table that build.

## S4.7 as built (2026-11-26)

**The problem.** Layer 6 makes one session's entry, but the feed a dashboard receives is more than the entries side by
side. build_feed folds each entry into the board in alive order, and the fold writes what no entry holds: every card's
and every tree row's age tint (`trgb`, from the card's `_ageT` and the build's clock through colormap.py), the clock
itself on a placeholder whose time is the clock, and the two bell fields (`notify`, `notifyRaw`) from the bells store and
the session's own flag. Then come the passes across sessions. A worker's mirror card folds its rows under the handoff row
of the card that dispatched it, when that row rendered this build. A handoff row's working dot takes its recipient's
bit. The parked handoffs (a send to a dead session whose mail still waits) and the notice cards join the board, a bell
pass stamps every card the fold did not, and the frame wraps it all in the keys build_feed passes through. A port can
get every entry right and still ship a different feed: a tint rounded half up, a bell override of `false` read as no
override, a mirror folded under the wrong row, a re-sent message moved to the end of its list, a notice cap that counts
the wrong cards, a conditional frame key in the wrong place. The worlds reach the fold, the tint and one whoWorking
stamp, and nothing else here: no mirror, parked handoff, notice, bell override, cleared id, raising session or float
clock.

`layer7.rs` ports, line for line, the build_feed loop minus the memo and its key, _feed_fold_session's fresh path with
_feed_fold_tints, _feed_fold_stamp, _feed_fold_card and _feed_fold_bell, cm.age_rgb, cm.ramp and cm.stops_for,
_notify_card_effective and _push_explicit_bell, the serving-fold join, the handoff whoWorking stamp, _parked_handoffs
with _parked_fold_step, the parked-handoff card, _notice_cards with _notice_rows' row filter, _notice_projection,
_held_mail_row, _notice_item_id and _notice_action_kind, the bell pass, and the frame. `build_feed` takes the snapshot and
trees, runs layer 6's `Build::entries`, and answers the frame in Python's key order (`Feed::wire`, `Feed::canon` for the
sorted gate), or the raise that fails the whole build. `romp-spike feed DUMP` compares it with the dump's
oracle/feed.canon.json and feed.wire.json, byte for byte (exit 1 on any difference). What the notes flagged, as built:
- The cleared set: dump_world's replay now answers a dict, as the real reader does (see "The replay fix" below).
- A raising session is absent: build_feed has no previous entry to serve in a fresh process, so the sid goes in
  `absentSids` (right after `now` in the frame, with `servingFoldedIds` before it when the join folded a candidate) and
  nothing of it folds. A hidden session's null entry folds nothing and is not absent.
- The serving fold: a candidate whose tracker row (a handoff row of that id, the last one on the board winning)
  rendered this build adds its first row's id to that row's children and every one of its rows to that card's tree; its
  item id goes in `servingFoldedIds`. Otherwise it lands as its own card after every session card, and the bell pass
  stamps it (the fold stamps no bell on a candidate).
- The handoff row's working bit: build_feed stamps `whoWorking` on handoff rows only, from the sids whose fold added a
  `working` name, so a recipient that raised, is hidden, or works under the name "" (a falsy name adds none) stays dark.
- The bells: a card override is read with `is None`, so a stored `false` or `0` is an explicit mute that never falls to
  the session flag or the master; `notify` is `true` or null, never false; `notifyRaw` never reads the master.
- The tint: the age is `now - _ageT` in Python arithmetic (int minus int exactly, then `float()`: with a clock past 2**53,
  an age of 1000 s reads 1024 s through floats, and the tint moves), the log position, `ramp(1 - f)`, each channel
  rounded half to even (59.5 reads 60, 50.5 reads 50). The colormap name goes through `lower()`, and an unknown or null
  name is colormap.DEFAULT, "aurora": the module docstring still says the default is "hawaii". age_rgb clamps the age to
  [120, 345600] s and ramp clamps `v` to [0, 1]; the two overlap, so taking out the age clamp moves no feed (M03 and M04
  are equivalent), and ramp's clamp (M02) shows only on a direct ramp call outside [0, 1], which the tint table makes.
- What the S2 notes did not know: the fold runs outside build_feed's per-session guard, and a null card in an entry's
  `asks` reaches it. _awaiting_card answers None for a session that is not live, and the body appends that None. The
  fold's `card.get("_ageT")` then raises AttributeError, and the whole build fails, every session's cards with it. The
  port answers that raise (see "Findings beyond the port").

**The inputs.** The snapshot gains three optional board keys, which the S2 dumper never writes (neither world has any)
and dump_layer7.py's worlds carry: `postalLog` (the postal log's records, decoded, in file order), `mailNew` (each
maildir file that stands, `[recipient, message id]`) and `notices` (each notice file's lines, decoded). They are file
reads, the same boundary as the recorded helpers: the port folds the postal log with _parked_fold_step's rules and
filters and projects the notice rows, while the reading, caching and checkpointing of the files stay Python's. The
held-mail backfill, which runs before _notice_cards reads the files, is a writer (it posts and retires held-mail rows
from the hold directory); its effect is in the rows, and the harness stubs it. `layer7::validate` refuses, each with its
path, the shapes found that Python would raise on or read in a way the port does not mirror; none is in either world: an
alive name that is not a string (bgServices is keyed by it), a world without a colormap key, a bells store that is not a
dict, a serving `goalId` that is a list or a dict (the join hashes it), postal rows whose id, recipient or sender is not
a string on a parked send (or not hashable on a recall), whose truthy time is a string (`int(str)` is not ported) or
truncates past i64, or whose truthy body is neither a string nor a list; a parked send whose id or recipient fails
kernel._safe_id's rule (Python finds the mail with `Path(base/"mail"/to/"new"/mid).exists()`, a path lookup, where the
port looks the exact pair up in `mailNew`; added in the review round); a float clock whose `int()` is past i64 while the
postal log holds a parked send (a parked send with no time reads `int(now)`, which Python keeps exact; added in the
review round); notice rows (op post, expire or acted) whose key is not a string, whose truthy rev, time or expiry is not
a number or a bool within i64, whose truthy actions are not a list of dicts, or whose action has a truthy kind or route
that is not a string or a truthy body that is not a dict, a notice file sid that is empty or holds `/`, and frame keys
without `now` or naming a key the frame lacks. What no loader can see is whether two different safe names reach one file
on the filesystem Python runs on (APFS ignores letter case, so a send `M1` finds a file `m1`): `mailNew` is a record of
the files that stand, and the harness answers no world whose `mailNew` its own filesystem contradicts (see "The
oracle").

**The replay fix.** S4.6 found that the replay's `_cleared_ids_display` answered a set where build_feed's frame code
reads a dict (`_cleared_foreign(cleared)` calls `cleared.get(i)`), so any world with a cleared id raised after the
session loop. It now answers `dict.fromkeys(board["cleared"])`: the snapshot keeps the ids, sorted, and the clear times,
which only order the frame's clearedForeign (a key the replay passes through from the dump), read as 0. Both worlds were
dumped before and after the fix at the same HEAD: every file but run.json is byte-identical (23 of 23 and 159 of 159).
The layer 6 edge fixture, regenerated, differs only in losing the one case note that recorded the old raise.

**The oracle.** For the worlds it is the dump's own oracle/feed.canon.json and feed.wire.json. For synthetic worlds,
`tools/rust-spike/dump_layer7.py` answers each case with one replay build_feed over it, the real fold and every pass
after the loop included: dump_world's Replay harness and dump_layer6's stubs, the postal log served to the real fold
through em.serve_records, the maildir and notice files written into the replay's empty state root for the real readers
(the notice memo cleared first), the clock the case's `world.now`. The harness answers no world whose files would not
read back as the case says: notice files that collide on this filesystem, and (since the review round) a parked
candidate whose maildir `exists()` here disagrees with its `mailNew` membership, found by folding the postal log with
the real _parked_fold_step before the build (`Unfaithful`; a REFUSE case is answered as Python reads it). A case's frame
keys are the build's own (its keys minus the two conditional ones, which the port decides), and the harness checks that
this assembly gives Python's wire bytes back. The expected answer is the feed, decoded from Python's wire string, with
the SHA-1 of Python's canonical and wire strings, or build_feed's raise. Its own check: `dump_layer7.py DUMP` runs this
harness over a dump's inputs with the dump's frame spliced in, and its canonical and wire bytes equal the oracle's on
both worlds.

**Result on the worlds: zero differences.** Small: 4 sessions, 7 cards, 10 tree rows, the canonical and wire feeds
identical (17,878 bytes each), 826 values. Large: 38 sessions, 41 cards, 78 tree rows, 114,338 bytes each, 5,348 values.
Of the six plants, only `who-owner` moves the worlds (one value each: s001's handoff row to s003, which works).
Python runs 185 of the 414 ported lines on either world.

**The edge set.** `dump_layer7.py --edge-cases` writes `rust/crates/romp-views/tests/fixtures/layer7_edge.json`
(synthetic, 1.8 MB; it regenerates byte for byte under another hash seed). Its 154 worlds, built with dump_layer6's
builders: the tint over every colormap name, null, empty, upper and title case, an unknown name and one with a trailing
space; ages at the clamp's edges (0, 119, 120, 121, 345,600 and 345,601 s, a future time, a fractional age, a negative
time), two ages whose channel lands on an exact half, an age where ramp's float order shows, an int clock minus a float
time, a float clock (fractional and integral), a small int clock, a clock past 2**53, a float clock past i64 with no
parked send; the placeholders dated by the clock and by their own time; the bells (nothing set, the master on, off and
truthy, the turn switch alone, card overrides true, false, 0, "", null, 1 and "yes", a card whose itemId is "", session
flags of every shape with the master on and off, a peer row's flags against the session's own); the session fold
(raising sessions absent in alive order, a hidden session, working and awaiting names, a cold session with heal counts,
bgServices under a repeated name, the null card that fails the build); the serving join (a candidate folded before and
after its tracker's session, no rendered tracker, a tracker id two cards render, an ask row of that id, two candidates
into one card, a completed and a needs-you candidate, a numeric tracker, joined handoff rows taking the stamp, a tracker
row that already has children, a candidate aimed at a joined row or at a kept candidate's handoff row); the whoWorking
stamp (a working, idle, absent, hidden or not-alive recipient, a working owner, a working session named "", a handoff to
oneself); parked handoffs (a dead recipient with its mail standing, an alive recipient, consumed mail, mail under
another box, recall, bounce and re-send, every non-parked shape, every time shape, ties, names from the registry, an
empty name, a non-ASCII sender and an unknown recipient, a 128-character id, ids a path lookup misreads on rows the fold
does not park, a recipient alive but absent, a cleared id, a re-sent id, non-dict rows, a float clock, bells); notices
(the owner-less home, a session's file, an unknown sid, revisions, expires, acted rows, expiries around the clock and a
float clock, a cleared id, the sort within and across files, the cap with and without held mail, held-mail recognition,
boards and categories, actions of every kind and route shape, absent fields, lines that are not rows, other ops, float
and bool revs and times, bells, non-ASCII text); a cleared set over every producer; the frame's two conditional keys
together; every producer at once. And 36 worlds the loader must refuse, 12 of them added in the review round
(kernel._safe_id's rule on a parked send, a clock past i64). It also carries Python's cm.age_rgb over 4,037 (colormap,
age) points and cm.ramp over 1,760 (v, colormap) points.

Result: 118 worlds load and give the whole feed byte for byte in both key orders (181 sessions, 408 cards, 227 tree
rows, 32,013 values; 6 absent sessions, 11 folded candidates, 31 parked and 202 notice cards), 0 differences; one build
raises in both (the null card). The other 36 are refused at load, exactly the cases named REFUSE. The 5,797 tint and ramp
points equal Python's. Python's line trace ran 371 of the 414 lines of the 19 ported functions. The 43 it did not run
cannot run on an admitted world in a fresh replay: build_feed's memo and key arms (a held entry, a hit, a raise served
from the memo, the live map read when the caller passes none, the subagent reads the replay's derivation never makes; 10
lines), the fold's kept-record path (20), the maildir stat that raises (2), and _notice_rows' absent, memoized,
unreadable, blank and undecodable lines (11; a decoded snapshot holds none). The first round said all 43 cannot run in a
fresh replay; that was wrong for the maildir stat. When the recipient's new/ directory exists, an id longer than the
filesystem's name limit makes `Path.exists()` raise OSError 63 (file name too long), which it does not swallow, so
`except OSError: continue` runs (the reviewer checked it on this Mac; the card list is the same either way). Since the
review round the loader refuses an id over kernel._safe_id's 128 characters, so no admitted world reaches it.

**The randomized check.** `python3 tools/rust-spike/dump_layer7.py --fuzz N --seed S` draws worlds from dump_layer6's
generator, then what layer 7 reads: the clock (NOW most of the time, an integral and a fractional float, clocks near the
trees' own times so the tints spread), the colormap, handoff peers aimed at the world's alive sessions, serving trackers
aimed at its node keys and at planted handoff rows (now and then the same tracker id in two stores), service chips and
repeated or empty session names, bells of every shape on the world's item ids and session flags, a postal log of parked
sends, recalls, bounces, re-sends around a tie and noise with a maildir that stands for some, notice files with
revisions, expires, acted rows, expiries around the clock, boards, categories, actions and held-mail rows (now and then
past the cap), and cleared parked and notice ids. A parked send's recipient is a safe id (the loader refuses others since
the review round); the non-ASCII sid is drawn as a sender and on rows the fold does not park, where its first 8 code
points still show. Any difference or refusal fails the run (a world the harness cannot answer faithfully raises out of
it), and the same seed makes the same file byte for byte under another hash seed (seed 1 checked again under
PYTHONHASHSEED=7 after the review round).
- Result after the review round's generator change, seeds 1 to 6 at 4,000 worlds each (about 20 s apiece): 24,000
  worlds, 39,019 sessions, 83,744 cards (8,785 parked, 42,609 notices) and 43,207 tree rows, 6,629,814 values, **0
  differences**, 0 refused. 4,202 sessions are absent (their derivation raised), 771 candidates folded, and 563 builds
  raise in both (the null card). Python's line trace on seeds 1 to 3: the same 371 of 414 lines as the edge set. (The
  first round's generator gave 24,000 worlds, 85,434 cards and 6,692,301 values, also 0 differences.)

**Planted mistakes, each caught but four equivalent mutants.** The six plants are romp-spike's `--plant` options
(`dump_layer7.py --fuzz ... --plant NAME` passes only when the plant is caught; `tests/layer7_edge.rs` checks each is
caught on exactly the cases built for it). The 52 source mutants were edits to `layer7.rs`, each built, run on all four
inputs and reverted by a scratch script (not committed). The table is the first round's second sweep, on that round's
final bytes: the 131-world edge set and that round's generator's fuzz seed 1. Each cell is differences, then the worlds
or cases they fell in; "tint points" are the fixture's cm.age_rgb and cm.ramp table, part of the edge set. The review
round re-ran eight of its rows (M24 to M31, the parked block, whose names world and generator pools changed) on the final
bytes and added 21 mutants of its own; see its table under "The review round" below. The other rows were not re-run:
their edge cells are on the 131-world set, and their fuzz cells on the first round's generator.

| Mistake | Small | Large | Edge set (131) | Fuzz, seed 1 (4,000) |
|---|---|---|---|---|
| `--plant bell-falsy-skip` (a falsy card override falls through to the flag) | 0 | 0 | 12 in 5 | 519 in 350 |
| `--plant serving-first-row` (the join keys the first handoff row of an id) | 0 | 0 | 2 in 1 | 12 in 6 |
| `--plant who-owner` (a handoff row's dot is its owner's) | 1 | 1 | 3 in 3 | 409 in 338 |
| `--plant parked-unsorted` (parked handoffs in the fold's order) | 0 | 0 | 66 in 3 | 1,925 in 83 |
| `--plant notice-cap-held` (the cap counts held mail) | 0 | 0 | 3 in 3 | 1 in 1 |
| `--plant absent-last` (absentSids at the end of the frame; wire only) | 0 | 0 | 4 in 4 | 648 in 648 |
| ramp rounds half away from zero, Rust's `round` (M01) | 0 | 0 | 6 in 2, and 105 tint points | 0 |
| ramp does not clamp v to [0, 1] (M02) | 0 | 0 | the run dies (a panic) | 0 |
| age_rgb has no lower clamp at FADE_LO (equivalent) (M03) | 0 | 0 | 0 | 0 |
| age_rgb has no upper clamp at FADE_HI (equivalent) (M04) | 0 | 0 | 0 | 0 |
| the colormap name is not lowercased (M05) | 0 | 0 | 12 in 2, and 527 tint points | 9,855 in 528 |
| an unknown colormap falls back to hawaii, the module docstring's word (M06) | 0 | 0 | 1,719 in 97, and 1,581 tint points | 19,642 in 1,126 |
| an int clock minus an int time computed in floats (M07) | 0 | 0 | 3 in 1 | 0 |
| the card keeps _ageT (M08) | 7 | 41 | 161 in 106 | 5,432 in 2,877 |
| the rows keep _ageT (M09) | 10 | 78 | 207 in 104 | 7,390 in 2,206 |
| a placeholder's t is not restamped with the clock (equivalent in a fresh build) (M10) | 0 | 0 | 0 | 0 |
| a placeholder's t is restamped as `int(now)`, the clock's tag lost (M11) | 0 | 0 | 2 in 2 | 21 in 21 |
| rows tinted from the card's _ageT (M12) | 0 | 4 | 29 in 10 | 3,525 in 694 |
| the bell pair written before trgb, a key-order difference (M13) | 7 | 41 | 157 in 106 | 4,338 in 2,556 |
| notifyRaw falls back to the master (M14) | 0 | 0 | 24 in 15 | 3,605 in 1,061 |
| notify writes false, not null (M15) | 7 | 41 | 363 in 100 | 10,203 in 2,825 |
| the session flag read as _session_flag, absent reading false (M16) | 7 | 41 | 395 in 105 | 13,429 in 3,095 |
| a kept candidate's own card gets no bells, the pass starting at the parked cards (M17) | 0 | 0 | 4 in 4 | 1,094 in 927 |
| the candidate's first row id is not appended to the tracker row's children (M18) | 0 | 0 | 9 in 8 | 112 in 112 |
| the candidate's rows do not join the tracker card's tree (M19) | 0 | 0 | 8 in 8 | 112 in 112 |
| a candidate with no rendered tracker row lands first, not last (M20) | 0 | 0 | 49 in 4 | the run dies (a panic) |
| servingFoldedIds lists the tracker, not the candidate's item id (M21) | 0 | 0 | 10 in 8 | 116 in 110 |
| every row kind takes the whoWorking stamp, not only handoff rows (M22) | 0 | 0 | 1 in 1 | 92 in 40 |
| every session with an entry counts as working for the stamp (M23) | 1 | 1 | 3 in 3 | 426 in 353 |
| a parked handoff to an alive recipient still shows (M24) | 0 | 0 | 1 in 1 | 271 in 271 |
| a parked handoff shows without its maildir file (M25) | 0 | 0 | 2 in 2 | 432 in 432 |
| a parked handoff's missing time reads 0, not int(now) (M26) | 0 | 0 | 42 in 2 | 3,067 in 387 |
| an unnamed sid falls back to the whole sid, not its first 8 code points (M27) | 0 | 0 | 25 in 5 | 8,783 in 1,679 |
| an unnamed sid falls back to its first 8 bytes (M28) | 0 | 0 | 5 in 1 | 1,712 in 317 |
| a re-sent message id moves to the end of the fold (M29) | 0 | 0 | 18 in 1 | 1,742 in 296 |
| a recall or bounce does not remove its id (M30) | 0 | 0 | 1 in 1 | 68 in 40 |
| a cleared parked id still shows (M31) | 0 | 0 | 2 in 2 | 175 in 175 |
| `int()` of a float rounds instead of truncating (M32) | 0 | 0 | 13 in 3 | 3,687 in 1,106 |
| an expiry applies only past the clock (>), not at it (M33) | 0 | 0 | 1 in 1 | 328 in 328 |
| an equal rev posted later does not replace (>) (M34) | 0 | 0 | 4 in 2 | 12,027 in 773 |
| an acted card keeps its actions (M35) | 0 | 0 | 1 in 1 | 47 in 47 |
| an acted card's acted flag is the row's own (M36) | 0 | 0 | 1 in 1 | 165 in 160 |
| a file's notices sorted by key alone (M37) | 0 | 0 | 26 in 1 | 2,577 in 51 |
| notice cards across files sorted by itemId alone (M38) | 0 | 0 | 508 in 6 | 39,063 in 896 |
| the owner-less home's name from the registry (M39) | 0 | 0 | 88 in 19 | 2,707 in 772 |
| a card on another board takes its category as its column (M40) | 0 | 0 | 1 in 1 | 47 in 47 |
| a route is not read as a kind (M41) | 0 | 0 | 3 in 1 | 128 in 123 |
| an action's kind is not stripped (M42) | 0 | 0 | 2 in 1 | 423 in 366 |
| an action's kind is stripped with Rust's `trim`, U+001C kept (M43) | 0 | 0 | 1 in 1 | 196 in 183 |
| a notice line without an op counts as a row (equivalent) (M44) | 0 | 0 | 0 | 0 |
| an expire row retires every rev of its key (M45) | 0 | 0 | 1 in 1 | 1,418 in 474 |
| an action's kind appended at the end even when the action has one (M46) | 0 | 0 | 6 in 2 | 1,044 in 727 |
| servingFoldedIds written even when no candidate folded (M47) | 1 | 1 | 98 in 98 | 3,784 in 3,784 |
| a repeated bgServices name moves to the end (M48) | 0 | 0 | 1 in 1 | 0 |
| a hidden session is listed absent (M49) | 0 | 0 | 2 in 2 | 636 in 636 |
| a null card is skipped, not a raise of the whole build (M50) | 0 | 0 | 1 in 1 | 104 in 104 |
| every card's t restamped with the clock, not only a null _ageT's (M51) | 7 | 41 | 157 in 105 | 5,296 in 2,817 |
| a notice line with a falsy key counts as a row (M52) | 0 | 0 | the run dies (a panic) | the run dies (a panic) |

The worlds catch 10 of the 58 rows, nine mutants and the who-owner plant: every card's and row's `_ageT` and tint (M08, M09, M12 on the
large world), the bell pair's place and values (M13, M15, M16), the restamp of every card (M51), servingFoldedIds written
empty (M47), and the one whoWorking stamp both worlds make (s001's handoff row to s003, which works: M23 and who-owner).
The edge set catches every row but the four equivalent ones, two of them (M02, M52) by killing the run with a panic.
Fuzz seed 1 catches every row but those four and four more: M01 (no fuzz age puts a channel on an exact half; the edge
set's two exact-half worlds and the tint table catch it), M02 (no feed reads ramp outside [0, 1]), M07 (the fuzz draws no
clock past 2**53) and M48 (it needs three sessions with service chips, the first and the last under one name). M13 and
`absent-last` change only key order: the canonical bytes, the gate, are identical, and only the wire comparison
catches them, which is why both are compared.

The equivalent four, each tried as a mutant and moving nothing on any check: age_rgb's clamp to [120, 345600] s and ramp's
clamp of `v` overlap, so taking out the age clamp's low side (M03) or high side (M04) gives every age the same tint (ramp's
own clamp, M02, shows only on a direct call outside [0, 1]); a placeholder whose `_ageT` is null already carries the clock
as its `t` in a fresh build, so the fold's restamp changes nothing (M10; it matters for an entry the memo serves from an
earlier build, and its tag and its "only" are pinned by M11 and M51); and _notice_rows' truthy-op filter is redundant with
the projection, which reads only post, expire and acted rows (M44; the truthy-key half is pinned by M52).

The first sweep (the 129-world edge set and the generator before its last changes) found three gaps, each closed and
shown closed in the second sweep. The edge set caught M01 only through the tint table, not in any feed: two worlds whose
clock is an age that puts a channel on an exact half (a card at time 0) now catch it in the feed. It missed M22 entirely:
its "working session named \"\"" case was not one, because dump_layer6's `S()` reads a falsy name as its default; the case
now sets the alive row's name directly and catches M22. Fuzz seed 1 missed M22, M29, M48 and the serving-first-row plant;
the generator now draws re-sends around a tie, the same tracker id in two stores, service chips with repeated and empty
session names, and catches all but M48.

**Findings beyond the port.**
- The null card (above): reproduced through the replay with a recorded _session_awaiting answer for a session with no
  live row ("fold: a null card in an entry ..." in the edge set; 637 fuzz builds raise this way). In production it
  needs _session_awaiting to answer, with `stamp` off, for a session that is not live: the states overlay's awaiting:true
  row (the comments say no producer writes one now) or a live-row source answering without a live row. Not reproduced
  live; a candidate for the bug lane. The memo does not protect the board: the raise is in the fold, after the guard.
- colormap.py's docstring names "hawaii" as the default; the code's DEFAULT is "aurora" (M06).
- age_rgb's clamp and ramp's clamp overlap (M03, M04).
- An informal signal, not a measurement: the release build makes the large world's whole feed in 25.7 to 27.0 ms of
  wall time (the command's own clock, ten runs) and its 38 entries alone in 25.3 to 26.3 ms (five runs), on a busy
  machine (load average about 21), so the fold, the passes and the frame add about a millisecond. S4.6 read 31 to 33 ms
  for the entries on a quiet machine; the spread between those readings is itself a reason S6 times process CPU.

**The review round.** Two must-fix, both applied:
- The parked mail's maildir test. Python asks `Path(base/"mail"/to_id/"new"/mid).exists()`, a path lookup: pathlib
  drops `.` components and trailing slashes, and APFS (the macOS filesystem) ignores letter case. The port looks the
  exact (recipient, id) pair up in `mailNew`, and `validate` checked only that the id, to_id and from_id were strings, so
  the module's contract (refuse what Python reads in a way the port does not mirror) did not hold. The reviewer's four
  probe worlds, rebuilt here and answered by the real kernel through this harness, each show a `parked:` card in Python
  and none in the port (4 differences in 4 cases): a parked send with id `.` while another file stands in the
  recipient's new/ (the path is new/ itself), id `m1/` with a file `m1` standing, id `M1` with a file `m1`, and a
  recipient `<sid>/` with `<sid>/new/m1` standing. The loader now refuses a parked send whose id or to_id fails
  kernel._safe_id's rule: empty, over 128 characters, or not `[A-Za-z0-9][A-Za-z0-9._-]*` (Python's pattern is a
  literal ASCII range, so `/`, `\`, NUL, a leading dot and every non-ASCII letter are out; APFS also folds Unicode
  normalization). The sender is never part of a path and is not checked. `layer7::safe_id` agrees with the real
  kernel._safe_id on the 22 strings of its unit test. Ten REFUSE worlds: `.`, `m1/`, a recipient ending in `/`, `.m1`,
  a backslash, NUL, 129 characters, a space, a leading hyphen, a non-ASCII recipient. The rule is the kernel's, whole:
  only `.`, a trailing slash and (through APFS's Unicode folding) a non-ASCII name are read as another path; the
  others are refused for the rule's sake, not because Python reads them differently. A case-only collision
  passes the rule (`M1` and `m1` are both safe) and depends on the filesystem, so the harness now does for the maildir
  what it did for notice files that collide: before each build it folds the postal log with the real
  _parked_fold_step and refuses to answer (`Unfaithful`) a world where a candidate's `exists()` on this filesystem
  disagrees with its `mailNew` membership. On this Mac it refuses a send `M1` with a file `m1`, and `m1` with `M1`; with
  the check disabled, both are answered and the port differs on both (2 differences in 2). So of the reviewer's four,
  three are refused at load and the fourth by the harness. The edge set's one non-ASCII recipient (the names world) is
  refused now: that recipient became an unknown ASCII sid, and the non-ASCII sid stays as a sender, where the
  first-8-code-points fallback still shows (M28 is caught there). The fuzz draws recipients from safe ids only and the
  non-ASCII sid among senders and on rows the fold does not park.
- A float clock past i64. `int_now` cast `f.trunc() as i64`, which saturates at the i64 limit, where Python's
  `int(now)` is exact. The reviewer's two worlds (now 1e19 and -1e19, each with a parked send that has no time), rebuilt
  here, gave 5 differences in 2 cases: at 1e19 Python ships t 10000000000000000000 with the brightest tint (age 0), the
  port 9223372036854775807 with the darkest; at -1e19 only t differs. The loader now refuses a float clock whose
  `int()` is past i64 while the postal log holds a parked send (`int(now)` has no other reader), with two REFUSE
  worlds, and `int_now` asserts it; a float clock past i64 with no parked send stays admitted (an edge world: 1e19, every
  tint the darkest stop). The validate paragraphs in `layer7.rs` and under "The inputs" above say so. Carrying `int(now)`
  as a big integer was the other option; a refusal keeps the parked times i64 everywhere they sort and subtract.

Should-fix, all applied:
- The edge-set gaps from the reviewer's mutants: 8 worlds, each rebuilt from the review's description with
  dump_layer6's builders and each mutant as an edit to the final `layer7.rs` in a scratch copy. Each gives 0 on the old
  set and fails the new one on its own world (the table below). R35 (ramp mixed as `a * (1 - fr) + b * fr`, equal on
  paper) was caught only by the tint table; the world for it is a card at time 0 under the clock 146335.799933855, found
  by a scratch search over every colormap for an age whose channel the two orders round apart (474 such ages). R07
  (`log10` for `ln`) was already caught in a feed and needed nothing.
- The claim that the 43 lines Python never ran "cannot run in a fresh replay" was wrong for the two maildir-stat lines
  (see the edge set's result above). With the 128-character cap it now holds for admitted worlds, and the docs and
  `tests/layer7_edge.rs` say so, with the reason.
- S6's next action names the I/O asymmetry: Python's timed build includes the postal log's fold_records cursor, the
  maildir stats and the notice directory's listing and memo checks; the port takes decoded records and does no I/O.
- The float-order nit: the R35 world above is that feed world.

The review round's mutants, on the final bytes: the old edge set (130 of its 131 worlds; the 131st, the names world with
its non-ASCII recipient, is refused now), the new 154-world set, and fuzz seed 1 of the final generator. Neither world
catches any of them. The validate mutants that loosen a rule cannot show in the fuzz, which draws only admitted worlds;
the edge set's REFUSE worlds catch them.

| Mutant | Edge set, old 130 | Edge set, 154 | Fuzz, seed 1 (4,000) | The world that catches it |
|---|---|---|---|---|
| notifyRaw reads a card override when the itemId is "" (R01) | 0 | 2 in 2 | 0 | the two itemId "" worlds: muted with the master on (Python: notifyRaw null), armed with the session's flag false (Python: false) |
| notify reads a truthy override that is not a bool (1, "yes") as false (R25a) | 0 | 2 in 1 | 125 in 115 | card overrides 1 and "yes", the master off (Python: notify true) |
| notifyRaw reads it as false (R25b) | 0 | 2 in 1 | 125 in 115 | the same world (Python: notifyRaw true) |
| a kept candidate's handoff rows become trackers (R10) | 0 | 1 in 1 | 27 in 25 | a later candidate aimed at a kept candidate's handoff row (Python: its own card) |
| joined handoff rows become trackers (R11) | 0 | 2 in 1 | 2 in 1 | a second candidate aimed at a joined row of the first (Python: its own card) |
| parked and notice cards read the alive set minus the absent sessions (R36) | 0 | 1 in 1 | 218 in 114 | a recipient and a notice file's owner alive but absent (Python: no parked card, the notice live) |
| an int clock minus a float time truncates the time (R06) | 0 | 3 in 1 | 5 in 2 | a card and its row at NOW - 121.5 (Python [144, 136, 240], truncated [143, 136, 240]) |
| ramp mixes `a * (1 - fr) + b * fr` (R35) | 0, and 3 tint points | 2 in 1, and 3 tint points | 0 | the clock 146335.799933855, the card at 0 (Python [12, 180, 100], the mutant [11, 180, 100]) |
| age_rgb's log position through `log10`, not `ln` (R07) | 4 in 1, and 15 tint points | 4 in 1, and 15 tint points | 0 | an exact-half world (caught before the round) |
| no safe-id check on a parked send (V01) | 0 | 10 in 10 | 0 | the ten safe-id REFUSE worlds load |
| the check on the id only (V02) | 0 | 2 in 2 | 0 | a recipient ending in `/`, a non-ASCII recipient |
| the check on the recipient only (V03) | 0 | 8 in 8 | 0 | the eight id REFUSE worlds |
| the sender checked too (V04) | 0 | 2 in 2 | 334 in 334 | the names world and the misread-ids world, each with a non-ASCII sender, are refused |
| every sent row checked, parked or not (V05) | 0 | 1 in 1 | 0 | ids a path lookup misreads on rows the fold does not park (refused) |
| no 128-character cap (V06) | 0 | 1 in 1 | 0 | a 129-character id loads |
| the cap off by one, `>= 128` (V07) | 0 | 1 in 1 | 0 | a 128-character id is refused |
| the first character may be `.`, `_` or `-` (V08) | 0 | 1 in 1 | 0 | a leading hyphen loads (a leading dot has its own check) |
| Unicode alphanumerics, not ASCII (V09) | 0 | 1 in 1 | 0 | a non-ASCII recipient loads |
| no clock check (V10) | 0 | 2 in 2 | 0 | the two clocks past i64 load |
| the clock check without its parked-send condition (V11) | 0 | 1 in 1 | 0 | the clock 1e19 with no parked send is refused |
| the clock check on the upper bound only (V12) | 0 | 1 in 1 | 0 | the clock -1e19 loads |
| the harness's maildir check answers nothing (`parked_unfaithful` returns []) | - | - | - | not an edge world (the loader admits both): a send `M1` with a file `m1`, and `m1` with `M1`, are answered and the port differs, 2 in 2; with the check both are refused |
| re-run: a parked handoff to an alive recipient still shows (M24) | 1 in 1 | 2 in 2 | 318 in 318 | first round: 1 in 1; the absent-recipient world now too |
| re-run: a parked handoff shows without its maildir file (M25) | 2 in 2 | 2 in 2 | 432 in 432 | first round: 2 in 2 |
| re-run: a missing time reads 0, not `int(now)` (M26) | 42 in 2 | 42 in 2 | 2,439 in 345 | first round: 42 in 2 |
| re-run: an unnamed sid falls back to the whole sid (M27) | 17 in 4 | 26 in 6 | 8,174 in 1,616 | first round: 25 in 5, the names world's 8 in 1 among them |
| re-run: an unnamed sid falls back to its first 8 bytes (M28) | 0 | 2 in 2 | 126 in 122 | first round: 5 in 1, all in the names world; now the non-ASCII senders of the names and misread-ids worlds |
| re-run: a re-sent message id moves to the end of the fold (M29) | 18 in 1 | 18 in 1 | 1,654 in 281 | first round: 18 in 1 |
| re-run: a recall or bounce does not remove its id (M30) | 1 in 1 | 1 in 1 | 49 in 39 | first round: 1 in 1 |
| re-run: a cleared parked id still shows (M31) | 2 in 2 | 2 in 2 | 175 in 175 | first round: 2 in 2 |

Not done: the first round's other 44 mutant rows were not re-run on the final bytes. The edge set only gained worlds
(and the names world's recipient), and the generator changed only its postal pools, so the re-run covered the parked
block those touch (M24 to M31, each matching its first-round edge cell on the old worlds, M27 and M28 less the names
world) and the review's own mutants; the other rows' fuzz cells are the first round's generator's.

Tests: 15 new, 173 pass in the workspace: 8 unit tests in `layer7.rs` (the tint, Python's age arithmetic, `int()`, the
action kinds and held mail, the parked fold, and from the review round `safe_id` against the kernel's answers and `int()`
of a clock past i64), `tests/layer7_edge.rs` (6: every case byte for byte in both key orders with both SHA-1s, every tint
and ramp point, each plant caught on exactly its cases, the line trace's missed lines, the schema, and from the review
round each of its 12 refusals naming its rule, the message compared whole) and 1 in romp-spike for the feed check's
messages and its tint-table reader.

What this feeds: S6. The whole feed comes out of the port, so a timed region can be the same on both sides (derivation
plus fold), as s2-design section 4 asks.

## S6 method (2026-11-26, revised by its design review)

**The problem.** The gate asks two plain questions: does the port build the feed for at least five times less CPU, and
does its transcript store hold the same files in at most a third of the memory Python's record cache needs? Timing each
side's "build" naively answers neither. Python's build_feed does work the port never does: it computes a memo key per
session, serializes each derived entry for the memo and decodes it again in the fold, lists and reads the postal log and
the notice directory, and assembles a frame of a dozen keys from other subsystems. The port takes all of those as
recorded inputs. Python's memo also means most production builds derive almost nothing, and its held anchor aggregate
means a production miss walks only the turns since the last build, while a naive port walks every turn every time.
Python's `time.thread_time()` read about 42x low here (s2-design section 4), and wall time on this shared machine moved
by a third for the same Rust build. Memory measured once after a cold read misses the growth S3 found after appends, and
synthetic transcripts may not expand in Python's cache the way real ones do. The method below exists to keep each of
those from deciding the answer.

**What runs.** Three tools, each side in a fresh process per run:
- `python3 tools/rust-spike/measure.py cpu DUMP [--builds K] [--instrumented M]`: Python's build in replay mode.
- `romp-spike measure cpu DUMP [--builds K]` (`rust/crates/romp-spike/src/measure.rs`): the port's build.
- `measure.py mem DIR PAYLOAD [--appends K] [--tracemalloc] [--relief]` and `romp-spike measure mem DIR PAYLOAD
  [--appends K] [--growth blocks|double] [--relief]`: the record cache and the store over the same files and the same
  append schedule (PAYLOAD/<round>/<file>).
- `measure.py perf --out FILE`: the live kernel's `/perf` `builds.feed` (hit, miss, built, cached, the held-walk
  counters), read with a GET and the token from its state directory (never printed), nothing else kept.
- `python3 tools/rust-spike/measure_drive.py --dump DIR [--dump DIR] --runs N --python EXE [--python EXE] --cpu --mem
  [--builds K] [--instrumented M] [--appends K] [--schedule active|all] [--active N] [--growth blocks,double] [--relief]
  [--mem-dir DIR] [--perf BEFORE --perf AFTER] [--out FILE]`: the alternating driver. Run i measures every side once per
  dump: even runs Python first (each interpreter in the order given) then Rust, odd runs Rust first then the
  interpreters reversed, so neither side always meets the machine first. Every run records its load average before and
  after, its exit status and its whole JSON line; the file (default `<system temp>/romp-s6-measure-<UTC>.json`, never in
  the repo) keeps every run raw, and `--summarize FILE` recomputes the summary from it. Interpreters are named by
  version and architecture, dumps by directory name, never by a path under the home directory.
- `dump_world.py --world large4`: the large world's four designed sessions without the 34 fillers, the world
  s2-design's 87 ms came from, kept as a continuity check against S2 (never a gate world).

**Which interpreter.** Both, every run: the miniforge CPython 3.12 (x86_64, under Rosetta on this Apple-silicon Mac;
the interpreter every earlier step used) and the live kernel's own arm64 CPython 3.13.18
(`~/.local/share/romp-python/cpython-3.13.18/bin/python3.13`), which is what users run. The gate is judged on arm64
3.13. Rosetta translation slows the x86_64 interpreter and runs a second OS thread beside it, so judging on it would
flatter the port; its rows are kept for continuity with S2's figures. The 42x thread_time under-report is Rosetta's: on
arm64 3.13, thread_time agrees with the wall clock over the calibration spin (0.97 to 1.00), on x86_64 it reads 0.024.

**CPU: the clocks.** Python reads `time.process_time_ns()`, with `thread_time_ns`, getrusage's user plus system time
and `perf_counter_ns` beside it. Rust reads `clock_gettime(CLOCK_PROCESS_CPUTIME_ID)` through a raw extern, with
getrusage, `CLOCK_THREAD_CPUTIME_ID` and `Instant` beside it. Every process opens and closes with a 100 ms spin that
checks process CPU against wall time (a clock that under-reports shows as a ratio far under 1) and counts its loop
iterations per CPU microsecond: CPU time is time on whichever core the process got, so a run whose rate moves more than
10% from its side's median (a slower core or clock, an E-core) is flagged in the summary. Both sides run under the
default scheduling policy; `taskpolicy` is not used, since clamping both would only push them to the efficiency cores,
and the rate flag catches a run that landed there. On macOS the process CPU clock is answered from the same task
counters as getrusage at microsecond resolution, so those two agree by construction; the independent checks are the
wall clock and the thread clock. The process must be single-threaded for process CPU to be the build's: measure.py
records the OS thread count (task_threads) before and after the rows. It is 1 on arm64 3.13 and 2 on the Rosetta
interpreter (Rosetta's own thread, idle during the spin).

**CPU: the rows.** Both sides time several rows round-robin, one build of each per round, and alternate their order
between rounds: a row's predecessor moves it (in the smoke runs Python's held rows by about 10%, and the port's held row
by about 25%: 2.6 ms after the light bound build, 3.4 ms after a full walk), so no row may always follow the same one.
Each row's figure for a run is the mean of its even-round and odd-round medians, its first build dropped (heldWarm
drifted from 21.8 to 17.4 ms within one row in the review's runs) and any build that ran a full (gen2) collection
dropped and counted. Rounds default to 11, odd, so both positions keep as many builds.
- Python (`km.build_feed(NOW, live_map)` under dump_world's Replay harness: the dumped trees, stores and recorded
  helper answers; an empty state root; perf-bench's guards, which must record no spawn, thread start or refused write):
  - `cold`: the first build in the process, every memo empty and the interpreter's own caches cold.
  - `noHold`: every memo cleared as for emptyMemo, and the replay key hands no leaf head (`ctx["leaf_head"] = None`,
    read only by `_feed_seg_walk`, kernel.py:56038), so the walk computes no hold key, copies no map and stores no
    aggregate: the port's algorithm, the same work on both sides.
  - `emptyMemo`: each build after clearing the feed memo, the fold's record, `_FEED_SEG_HELD`, `_SUMMARY_ANCHOR_MEMO`
    and `_node_anchor_last`: every session derives and every turn is walked, and the walk also builds the hold keys and
    stores each aggregate (3 to 5% over noHold here). It is not the port's algorithm; the review corrected that claim.
  - `heldWarm`: each build after clearing only the feed memo and the fold's record: every session derives, but the walk
    serves its closed turns from the held aggregates and the landing memo is warm, which is how a production miss
    re-derives. No seam is stamped between builds, so S4.5's stale warm landing cannot arise.
  - `heldNoLanding`: heldWarm with `_SUMMARY_ANCHOR_MEMO` cleared too: the size of the one Python cache the port still
    lacks. In the smoke runs it moved the large world's heldWarm region by 1% or less once position was balanced.
  - `memoServed`: nothing cleared: every session is a memo hit (the replay's key is constant), so nothing derives.
  Odd rounds run `emptyMemo, heldNoLanding, heldWarm, memoServed, noHold` (the held rows still follow emptyMemo with no
  noHold between, and memoServed follows a build that filled the memo). Every build keeps the held walk's counters it
  moved (segWalks, segHeld, segWalkTurns, segReset, segPartial).
- Rust (each region row makes its `Build` outside the clock, then times `layer7::fold_on`: layer 6's entries for every
  session plus the per-session fold, the region Python's entry plus fold computes):
  - `warm`: the whole `layer7::build_feed` (the serving join, whoWorking, the bells and the frame included).
  - `region`: `fold_on` with every turn walked (Python's noHold).
  - `bound`: `fold_on` with each session's closed-turn maps and plain-prompt time folded ahead outside the clock
    (`layer3::closed_part`), so only the open turn is walked: the cheap bound of must-fix 3(a), which flatters the port.
  - `held`: `fold_on` with the held aggregate ported (`layer3::SegHeld`: `_turn_hold_key`, `_seams_fp`, the prefix
    compare, `_feed_seg_copy`, the fold of the turns not held, the store under the byte bound, `_feed_seg_build_begin`
    and `_feed_seg_forget`), one table for the process's held builds, filled by one build before the rounds: Python's
    heldWarm.
  - `heldCold`: `fold_on` over a fresh table each build (every turn walked, keys built, every aggregate stored):
    Python's emptyMemo.
  - `entries` (`Build::new` plus `entries`, S6's first harness) and `buildNew` (`Build::new` alone, 0.01 ms: the maps a
    build reads across sessions, outside the region like Python's pre-loop reads).
  Odd rounds run the rows in reverse. Loading the dump is timed apart as `load`; `validate` and the oracle checks are
  not timed.
- A build that differs is never timed. Rust checks its cold build, the bound's build and two held builds (the filling
  one and a served one) against the dump's canonical and wire oracle; Python checks the cold build and the last build of
  every row (canonical, wire and every memo entry) through dump_world's own comparison. Both refuse to report (exit 2)
  when the oracle is changed by one character. The driver also requires Python's heldWarm counters to equal the port's
  held build's, and emptyMemo's the heldCold build's (large world: 38 walks, 16,839 turns held and 38 walked; 0 held and
  16,877 walked), which shows the held path really runs on both sides.

**CPU: the region and what is apart.** Python's region is `_feed_session_entry` plus `_feed_fold_session` plus the
`_session_working` call inside the key (the port computes working inside its derivation), less four things the port
does not have, each reported apart: the memo's two serialization costs together (`memoSerdeMs`: the fold's
`json.loads` of the entry string and the miss path's `json.dumps`, which production pays only as a memoizing Python;
`foldDecodes` 1,499 against 2,875 misses in the review's `/perf`), the memo bookkeeping the fold calls
(`_feed_fold_kept`), and the replay's helper stand-ins (`standinsMs`, 265 deep copies of recorded answers per large
build, 0.4 to 0.8 ms; the port looks the same answers up in its snapshot). It is measured in M instrumented builds per
row, round-robin like the plain rows: a wrapper times every function of build_feed's parts by process CPU, each call
kept under its label, the part it was called from, and the session whose turn of the loop it ran in. The wrappers' own
cost (a no-op wrapped 20,000 times, 0.7 us per call on arm64 3.13) is subtracted for every call made inside the region
(half of it for the calls subtracted, whose own clock reads sit partly inside what was subtracted; the other half is
reported as the uncertainty), giving `regionCorrected`. Reported apart and charged to neither side: the key's own work
(the replay's stand-in key), the memo's bookkeeping, the frame's helpers, the pre-loop reads, `_parked_handoffs` and
`_notice_cards` (file work the port does not do, near zero here), and the residual (the build total less every
top-level part: the inline loop, the serving join, the whoWorking and bell passes, context managers). Two checks guard
the attribution: the residual must be at least -(top-level calls x 2 us), since 1 us clock steps are unbiased and cannot
make a steady negative residual; and the instrumented total less the wrappers must be within 10% of the plain build
(flagged in the summary; the tiny small-world builds trip it).

**The decision rule (fixed before S6 proper runs).**
- The deciding comparison: Python's `heldWarm` region (wrappers subtracted) over the port's `held` region, on the large
  world, arm64 CPython 3.13.18.
- The statistic: per run, each side's row figure as above; the paired ratio of each Python run over the Rust run of the
  same run index (the two ran next to each other); the median of those ratios over at least 6 runs, reported with its
  minimum, maximum and n.
- The threshold: pass when that median is at least 5.0, fail below. A median at or above 5.0 with a minimum below it is
  reported as a marginal pass.
- The secondary row, labeled "same work, interpreter vs compiled": Python's `noHold` region over the port's `region`,
  same statistic. It does not decide the gate.
- Reported, not deciding: heldNoLanding over held (the landing memo the port lacks; if the deciding row fails narrowly
  while this passes, porting `_SUMMARY_ANCHOR_MEMO` is the named next step, for lczh to decide), the cheap bound,
  emptyMemo over heldCold, the memo hit, every plain-build ratio (labeled unfair upper bounds: they carry the frame,
  key, memo and file work), and the production blend.
- The production blend cannot pass: the port has no memo, so it derives every session every build, while production
  Python serves most sessions from its memo. At the miss rates measured so far (2.9% in a 13-minute window, 7.45% since
  boot), Python's blended cost per build is a fraction of the memo-less port's (below). Passing in steady state would
  need the memo ported too.
- `measure_drive.py` computes the deciding ratio and a verdict (`summary.large.gate`), "insufficient runs" under 6.

**The memo rows and production.** The summary takes the live kernel's counters from two `/perf` snapshots around an
active window (`--perf BEFORE --perf AFTER`; the difference, refused when the kernel restarted between them; one
snapshot gives the counters since boot, flagged, cold boot builds included). From the window: m = miss / (hit + miss)
per session per build, misses per build, sessions per build, the share of feed requests that ran build_feed at all
(`built` over `built + cached`: the review saw 6,430 of 39,621), and the share of closed turns the held aggregate
served.
What a hit and a miss cost come from the instrumented builds, per session, with the key left out of both (the replay's
stand-in key understates a production hit about 10x: 0.7 ms against S2's 7.7 ms on the 4-session world; leaving it out
flatters Python): hit = the memo's bookkeeping + its serialization + the fold (memoServed row); miss = the same + the
derivation + working, stand-ins out (heldWarm row, and emptyMemo beside it). Two blends per interpreter: uniform, every
world session missing at rate m (hit_all + m x sum of (miss_s - hit_s)), and largest-session, production's misses per
build all on the largest, growing session (hit_all + misses per build x (miss_s001 - hit_s001)). Both are set against
the port's held build, which derives every session.

**Memory.** For every memory run the driver copies the transcripts into a fresh scratch directory with mtimes near now
(now - 5 s, a millisecond apart in name order, so nothing reads as quiescent) and writes the append schedule once per
source, the same bytes for both sides: `--schedule active` (the default) appends to the 4 largest files only, 6 rounds,
each round one to four of the file's own records picked at random under a fixed seed, so record sizes vary as real
appends do; `--schedule all` appends every file's last line each round (S3's check). Each side reads every file, then
for each round appends and reads every file again (an appended file must read as an append, any other as a hit).
- The figure is phys_footprint (TASK_VM_INFO), the bytes macOS charges the process; resident size is reported beside
  it, and the summary flags any reading where resident minus footprint moved more than 8 MB (or 5%) from the baseline,
  and any compressed bytes (resident under-reads then). The default malloc zone's bytes in use and total (mstats) are
  read too: for the store they part live allocations from what the allocator keeps. A `--relief` run per side adds a
  reading after `malloc_zone_pressure_relief(NULL, 0)` at every point (raw extern in Rust, ctypes in Python); on this
  macOS it released 0 bytes at every reading on both sides and changed nothing, so mstats does the parting.
- Python: event_model alone (check_store's loader, its state roots in a scratch directory), `_read_jsonl_incremental`
  over every file. Each run checks that every entry holds its whole file, that every append read kept the entry's
  generation and grew the held bytes by exactly the appended bytes, and that the cache neither evicted (count or byte
  budget) nor dropped anything. The traced bytes come from a separate run with tracemalloc (tracing inflates resident
  size); after the last round the cache is cleared and the traced bytes must fall back to the baseline within 1% of the
  cold growth, with a 64 KiB floor: the reader's read counters by path and caller (`_READ_BYTES` and the byte tables of
  `_read_jsonl_entry_unlocked`) are not the cache and stay (11 KB on the small world, 1.2% of its 0.9 MB growth; 18 KB
  on the large world's 373 MB).
- Rust: the store's process from a fresh start, under the decided growth policy and, for continuity, S3's.
- The ratio is Rust's footprint delta over Python's, cold and after every round (the curve), with each side's bytes
  per file byte beside it and two projections that rescale Python to real transcripts' expansion: Rust over the file
  bytes x 2.85 (event_model's `decodedMultiple`) and x 3.1 (S3's lab resident growth). Rust's allocated capacity over
  Python's traced bytes is a second view at the allocator level. The record count must be the same on both sides.
- Real-shaped transcripts: `--mem-dir DIR` measures a directory of transcripts besides the dumps (copied first, never
  written). Running it on a read-only temp copy of real transcripts, numbers only, nothing committed and the copy
  deleted, needs lczh's OK; it has not been run.

**The store's growth policy (decided; S3 left it to S6).** `Growth::Blocks`, romp-transcript's default now: each
read's consumed bytes, whole lines only, become one block of exactly their size, so an append copies and frees nothing
it held and a record never spans two blocks (the tail guard gathers its 64 bytes across blocks; a line is found by a
binary search over the block starts); the index vectors are shrunk to fit after every read. Exact reserve was set aside
because it copies the whole file on every append and frees the old buffer each time, and the index-only design because
it changes what the plan measures (every record fetched by a positioned read); it stays the fallback if the store misses
the memory gate. `Growth::Double` keeps S3's store as built for comparison. What the policy buys, in the smoke runs on
the large world: appending every file's last line once (S3's case), Double went from 94.1 to 162.7 MB of footprint (its
live allocation from 96.3 to 178.9 MB: the doubled buffers), Blocks from 96.7 to 107.2 MB with its live bytes unchanged
(93.2 MB) and the malloc zone's total up 8.4 MB: the index's freed buffers, which the allocator keeps. Under the active
schedule the two grew 3 and 6 MB. Holding the index in blocks too would remove the frees; not needed at these sizes.

**Ways the comparison can be unfair, known now.** Each is either handled above or open:
1. The held aggregate (handled: ported). The port holds the closed turns' maps as Python does, with the same keys,
   fingerprint, prefix compare, copy, store and bound, and the same counters per build. Its held maps borrow strings
   from the loaded trees, where a production port would own them or share them by reference count; Python's copy
   shares its strings by reference too.
2. No memo in the port (reported, blended; cannot pass in steady state, see the decision rule).
3. The replay's key and helper stand-ins are not production's (the key is left out of the blend and the region; the
   stand-ins are subtracted from the region). Production's key stats files and reads stores, and its helpers read
   files; the live helpers' share of a derivation is not measured.
4. The memo's serialization (both costs out of the region, reported together), as its decode and its dumps come from
   the same memo; production pays both on a miss only as a memoizing Python.
5. The frame, the pre-loop reads and the file work are outside both regions (the port's region excludes its serving
   join, whoWorking, bells and frame, as Python's excludes its inline passes); neither world has a postal log or
   notices, so the file work is near zero here.
6. Loading is not comparable to production (timed apart): the port parses JSON trees; production Python builds its tree
   from records (the records-to-tree parse, its real per-append cost, is not measured at all: S4b).
7. The world is synthetic and covers only the planted shapes: no lazy atoms (em.hydrate is not ported), no split seams,
   no parked handoffs, notices or bells.
8. The world is not production-sized (should-fix 9): 38 sessions with s001 at 3,000 turns and 37 of about 375, where
   production had 6 memo entries and about 19 to 22 held turns per walk; and it has no restored pre-cut turns (`pre`)
   and no lazy atoms, both of which the port's loader refuses, while long production histories read their stored
   `segs` instead of segmenting atoms, a different mix of work per turn. A production-sized parity world would need the
   port to admit both; not built.
9. The mix of sessions moves the ratio. On the 4-session large world the deciding ratio was 3.3 (arm64) against 5.5 on
   the 38-session one, because one per-derivation dict over s001's 24,000 atoms (`_dictated_prompt_uuid`'s prompt index,
   which both sides build on demand) is 0.9 ms of the port's 1.2 ms held build there, and the port's std HashMap hashes
   every uuid with SipHash where CPython reuses each string's cached hash.
10. "Cold" differs by side: Python's first build pays lazy initialization in the interpreter and the kernel; the port's
    first build pays page faults on fresh allocations. Neither is a production boot. The plain cold ratio is labeled
    an unfair upper bound.
11. The region figures come from instrumented builds; the per-call wrapper cost is subtracted as an estimate (the
    uncertainty is reported), and the process clock's microsecond resolution makes the small parts noisy.
12. Rosetta (handled by judging on arm64 3.13; the x86_64 rows include Rosetta's thread in process CPU).
13. Garbage collection: the harness freezes the loaded heap and applies the kernel's trigger, but production freezes on
    its own schedule, so a build may meet a collection there that it does not meet here, or the reverse. 3.12 and 3.13
    also differ in their default first-generation threshold (700 and 2000). Builds that ran a full collection are
    dropped and counted.
14. Machine load and position: a shared machine (load average 3 to 8 of 16 cores during the smoke runs). Alternation
    spreads drift over both sides but not contention; S6 proper should run on a quiet machine and keep the load
    readings. A row's predecessor moves it by 10 to 25% on both sides; alternating orders and the balanced statistic
    handle it (before them, one smoke run's plain median gave 4.92 where the balanced figure was 5.52).
15. What holds the port back (should-fix 12): the default release profile (no LTO beyond thin-local, 16 codegen units,
    no target-cpu), the system malloc, and std's SipHash HashMap. Each makes the port look slower, not faster.
16. Memory, the synthetic transcripts: Python traced 4.51 bytes and its footprint grew 4.84 bytes per file byte, against
    event_model's own estimate for real transcripts (2.85) and S3's lab figures (2.86 traced, 3.1 resident). The
    synthetic records average about 613 bytes and are mostly small dicts, so they expand more than real ones, which
    flatters the store's ratio. Rescaled to 2.85 the ratio is about 0.41, over the one-third gate. Open until
    real-shaped transcripts are measured.
17. Memory, what the store gives up: it holds bytes and decodes on demand, so its memory gain comes with a decode on
    every read that neither gate measures. In the kernel the parse trees share strings with the cache's records, so the
    kernel-wide gain can differ from cache against store.
18. Memory, process history: both sides are measured in fresh processes, the best case for Python's fragmentation; the
    append schedule decides the store's growth, so it is reported with the curve; the default schedule grows only the
    4 largest files.
19. Memory, production's own cache: it drops quiescent files' records through its folds and bounds itself by budget,
    so production may hold fewer files than "every transcript"; the gate's "same files" holds here by construction.
20. The miss rate moves: 2.9% in a 13-minute window against 7.45% since boot (cold boot builds and earlier load
    included), so the blend is reported with its window.

**Smoke runs (2026-11-26, after the review).** On fresh dumps of the small, large and large4 worlds (each
`dump_world.py` replay check passing), `measure_drive.py --dump small --dump large --dump large4 --runs 2 --python
<3.13 arm64> --python <3.12 x86_64> --cpu --mem --builds 4 --instrumented 3 --appends 6 --relief --perf <before>
--perf <after>`: 78 runs, 0 failed, load average 3 to 8; plus S3's every-file schedule (`--schedule all --appends 1`)
and a last pair on the final bytes (52 runs, 0 failed). Every Python row's checked build equaled the oracle, so did the
port's cold, bound and held builds; the held counters agreed on every world; the guards saw nothing; every spin's CPU
agreed with its wall time (one Rust spin's loop rate was flagged, 21% under its median, on the small world); both sides
counted the same records after every round; no cache problem; no residual under its floor. Two runs are evidence, not
results; S6 proper quotes the figures. What they showed:
- The deciding comparison sits at the threshold. Large world, arm64 3.13: Python's heldWarm region about 16.6 ms, the
  port's held build about 3.0 ms, paired ratio 5.52 (5.28 to 5.76, n=2); the last pair, at load average 6 to 9, gave
  5.02 (4.84 to 5.20); an earlier pair gave 5.56 by the unbalanced statistic, and the 5.52 runs gave 4.92 by it.
  x86_64 3.12: 6.63. The secondary row (noHold 209 ms over the port's region 20.2 ms) is about 10.4 on arm64 and 17.3
  on x86_64. The small world is too small for memory: Python's footprint barely moves over 0.2 MB of files.
- The cheap bound passed before the port: heldWarm over bound 6 to 8x, so must-fix 3(b) was done. Before the prompt
  index was made lazy the bound was 10.8 ms, about 1.6x: the port built `_dictated_prompt_uuid`'s index over every
  atom of every session on every derivation, where Python builds it on the first call past its guard; the port now
  does the same (layer 5's harness, edge set and fuzz agree, 0 differences).
- The continuity check reproduces S2: the 4-session large world's empty-memo build is 89.7 to 90.3 ms and its
  derivation plus fold 88.5 to 89.6 ms on 3.12 (S2: 90 and 87 ms), 56 to 57 and 55 to 56 ms on 3.13.
- The production blend (13-minute window: 193 builds, 1,045 cached requests, m = 2.9%, 0.18 misses per build, 6
  sessions per build, 95% of closed turns held): arm64 Python about 0.78 ms per build uniform and 1.03 ms with the
  misses on s001, against the port's 3.0 ms; with emptyMemo-cost misses, 6.7 and 7.6 ms.
- Memory, large world: the store's footprint grew 96.4 MB cold (1.17 bytes per file byte) and 99.6 MB after the active
  schedule's 6 rounds (1.20); Python's 400.1 and 401.1 MB (4.84, 4.85). The ratio is 0.241 cold and 0.248 after the
  rounds, under the one-third gate as measured; rescaled to 2.85 decoded bytes per file byte, 0.41 and 0.42, over it.

**The review round (2026-11-26).** The design review (6 must-fix, 12 should-fix) and what was done:
- Must-fix 1 (the 2.7x compared a 38-session port build with a 4-session Python derivation): retracted in the status,
  the next action, "S4.6 as built" and the log; `--world large4` added as the continuity check.
- Must-fix 2 (the deciding comparison was not fixed): "The decision rule" above, computed by the driver.
- Must-fix 3 (the held aggregate): the cheap bound first (`bound`), which passed; then the faithful port
  (`layer3::SegHeld`, `layer6::Build::with_held`), byte-identical on every world, with Python's counters per build. Unit
  tests pin the served walk against the full one, the reset and partial paths, the byte bound and forget.
- Must-fix 4 (the blend): hit and miss from matching parts (the fold and the bookkeeping taken from each row rather
  than the reviewer's served + m x (entry + working + dumps), which charges a miss the kept fold instead of the fresh
  one), the key left out of both terms and said so, per-session attribution with the largest-session blend, two
  `/perf` snapshots and built against cached.
- Must-fix 5 (synthetic transcripts): bytes per file byte for both sides beside every ratio, the 2.85 and 3.1
  projections, and `--mem-dir` for real-shaped transcripts, waiting on lczh's OK.
- Must-fix 6 (after-appends memory): the growth policy decided (Blocks), pressure relief readings on both sides (they
  released nothing; mstats added to part live bytes from the allocator's), the active schedule and the curve.
- Should-fix 1 (footprint as the figure, drift and compressed flags), 2 (noHold, and the "same algorithm" sentence
  corrected), 3 (paired ratios, first build dropped, round-robin, gen2 builds dropped and counted; and, beyond the
  review, alternating orders with a position-balanced statistic), 4 (spin rates at start and end, flagged at 10%;
  taskpolicy not used, as above), 5 (both serialization costs out of the region, reported together), 6 (the port's
  region is `fold_on` with its `Build` outside the clock), 7 (the negative residual: emptyMemo's clear ran the wrapped
  `_feed_memo_forget(set())` between builds, about 1.5 ms charged to the next build's memo part; what the wrappers
  record between builds is now discarded, and the residual is about +0.6 ms on every row; the floor check and the
  wrapper check added), 8 (the tracemalloc self-check raises, with the 64 KiB floor and why; byte-budget evictions
  checked), 9 (unfairness item 8; the production-sized world not built, as said there), 10 (every plain-build ratio
  labeled an unfair upper bound), 11 (stand-ins subtracted), 12 (unfairness item 15).

Next for S6 proper (as written before it ran): lczh's OK for real-shaped transcripts; then on a quiet machine
`measure_drive.py --dump large --dump large4 --dump small --runs 6 --python <3.13 arm64> --python <3.12 x86_64> --cpu
--mem --relief` with the defaults (11 rounds, 5 instrumented, 6 append rounds, the active schedule, both growth
policies) and two `/perf` snapshots taken around an active window; the verdict is `summary.large.gate`. It ran with 8
runs as two invocations (CPU, then memory), without the real-shaped transcripts, which still wait on lczh's OK: "S6
results".

## S6 results (2026-11-26)

**What the run had to answer.** The plan's gate is two plain questions. Does the port build the feed for at least five
times less CPU than Python, over typed structs? Does its transcript store hold the same files in at most a third of the
memory Python's record cache needs? "S6 method" lists the ways a naive timing answers neither: Python's build does work
the port never does (the memo key, the memo's serialization, the frame, file reads), its memo means production rarely
derives at all, its held anchor aggregate means a production miss walks only the newest turn, the thread clock reads 42x
low under Rosetta, a row's predecessor moves its time, and synthetic transcripts may expand in Python's cache more than
real ones. The CPU gate's decision rule was written down before this run ("The decision rule" under "S6 method"):
Python's heldWarm region over the port's held build, large world, arm64 CPython 3.13.18, the median of the paired ratios
over at least 6 runs, pass at 5.0. The memory gate has no rule finer than the plan's sentence.

**What ran.** At afdaaf242 with a clean tree, the release binary current and the 180 Rust tests passing, on fresh dumps
of the small, large and large4 worlds (each dump's own replay check passing; oracles 17,878, 114,338 and 17,890 bytes;
`romp-spike feed` identical on all three):
- CPU: `measure_drive.py --dump large --dump large4 --dump small --runs 8 --python <3.13.18 arm64> --python <3.12.11
  x86_64> --cpu` with the defaults (11 rounds, 5 instrumented builds per row): 72 runs, 0 failed, 12:46 to 12:51 UTC.
- Memory: `measure_drive.py --dump large --dump small --runs 8 --python <both> --mem --relief` with the defaults (6 append
  rounds, the active schedule, both growth policies): 160 runs, 0 failed, 12:51 to 12:54 UTC.
- The live kernel's `/perf` read with a GET before and after both (12:46:27 to 12:54:24 UTC); the kernel did not
  restart between them.
- Every run is kept raw in `rust/notes/s6-runs.json` (the two invocations merged, both `/perf` snapshots, and the
  summary; `measure_drive.py --summarize rust/notes/s6-runs.json` recomputes the summary identically). With 8 runs each
  side went first 4 times.

The machine (Apple M4 Max, 12 performance and 4 efficiency cores): no pytest, cargo, dump or other workflow's build was
running, and the CPU was 80 to 89% idle just before. The load average was 5.1 to 10.8 during the CPU runs and 4.6 to 5.5
during the memory runs; this desktop idles at about 5 to 7. The 10.8 is run 0, which started as the three dumps
finished, while the one-minute average still carried them. Its deciding ratio (5.30) is mid-pack, and leaving it out
keeps the median at 5.24. Every calibration spin's process CPU agreed with its wall time (1.00), no spin rate was flagged
(all within 10% of their side's median), and no build ran a full collection. arm64 3.13 ran on 1 OS thread and x86_64
3.12 on 2 (Rosetta's; its thread clock read 0.024 of wall, as before). Every checked build equaled the oracle. The held
counters agreed on all three worlds (large: Python's heldWarm and the port's held build both 38 walks, 16,839 turns held
and 38 walked per build; emptyMemo and heldCold 0 held and 16,877 walked).

### The CPU gate: passes as written, narrowly

**In one sentence:** on the large world, under the interpreter romp runs, Python's derivation costs 5.24 times the port's,
and all 8 paired runs were over 5.0 (5.03 to 5.51), so the rule fixed in advance says pass, not a marginal pass.

The figures behind it, in ms per build, median over 8 runs (minimum to maximum): Python's heldWarm region 16.14 (15.66 to
16.91) on arm64 3.13 and 20.83 (20.35 to 22.10) on x86_64 3.12, against the port's held build at 3.07 (3.03 to 3.13). The
per-run ratios, in run order, were 5.30, 5.25, 5.51, 5.19, 5.03, 5.29, 5.20 and 5.24. The comparisons beside it on the
large world, as paired-ratio medians (minimum to maximum, n=8):

| Comparison | arm64 3.13.18 | x86_64 3.12.11 |
|---|---|---|
| Deciding: heldWarm region / held | 5.24 (5.03 to 5.51) | 6.80 (6.51 to 7.20) |
| Same work, interpreter vs compiled: noHold region / region | 10.47 (10.38 to 10.78) | 16.74 (16.46 to 17.15) |
| Empty memo: emptyMemo region / heldCold | 11.19 (11.15 to 11.37) | 17.97 (17.61 to 18.18) |
| Without the landing memo: heldNoLanding region / held | 5.28 (5.14 to 5.49) | 6.79 (6.56 to 7.50) |
| The cheap bound: heldWarm region / bound | 7.26 (6.91 to 7.51) | 9.46 (8.65 to 10.06) |

The rows the run was asked for, as whole plain builds: Python's empty-memo build is 222.7 ms (217.4 to 224.8) on arm64 and
353.3 (348.6 to 368.4) on x86_64; its memo-served build is 1.46 (1.38 to 1.47) and 2.35 (2.31 to 2.43). The port's
builds: held 3.07, heldCold 19.62 (19.36 to 19.80), every turn walked 20.14, the whole build_feed 20.19, the first build
in the process 24.0, and loading the dump 136 ms. On the small world the empty-memo and memo-served builds are 1.37 and
0.39 ms on arm64 (2.22 and 0.67 on x86_64), the port's held and heldCold builds 0.058 and 0.087 ms; the deciding ratio
there is 8.38 (7.97 to 8.82) on arm64 and 9.23 on x86_64.

What the replay's Python build does outside the region, reported apart and charged to neither side (large world, arm64,
heldWarm's instrumented builds, ms): the key's own work 0.68, the memo's bookkeeping 0.42, its serialization 0.73, the
helper stand-ins 0.40 (265 deep copies), the frame 0.52, the pre-loop reads 0.01, `_parked_handoffs` 0.02 and
`_notice_cards` 0.02 (the file work S4.7's review named, near zero since neither world has a postal log or notices), and
the residual 0.59 (the inline loop, the serving join, the whoWorking and bell passes). The instrumented build totals 19.66
ms, 18.95 less the wrappers' estimated cost, against 18.63 for the plain build (the wrapper check, 1.02). On x86_64 the
same parts are 0.98, 0.69, 1.17, 0.80, 0.86, 0.02, 0.03, 0.03 and 0.92.

**Why believe it, and how close it is.** The margin is 5%, and the method's own correction choices move the figure by
about that much, so the pass is real under the rule but thin:
- The wrapper correction's stated uncertainty (0.11 ms either way) gives 5.21 to 5.28, with the minimum run between 4.99
  and 5.07.
- Rescaling the region so the instrumented build less its wrappers equals the plain build (the 1.02 above charged
  entirely to the region) gives 5.15 (4.96 to 5.28): a median pass with one run under 5.0, which the rule calls a
  marginal pass.
- A cruder estimate, the plain build less every part reported apart, gives 4.95 (4.73 to 5.10). It is biased low,
  because each part's figure includes the wrappers' cost inside it (about 0.57 ms outside the region in all); adding that
  back gives about 5.13.
- The smoke figures shrank or held: the smoke pairs gave 5.52 and 5.02 (n=2 each), and this run gives 5.24 (n=8);
  x86_64's deciding ratio went from 6.63 to 6.80 and the secondary row from 10.4 to 10.47. The two sides' counters agree
  per build, and the port's held build is byte-identical to the oracle, so the held path really ran on both sides.
- What leans which way: the port's default release profile, system malloc and SipHash maps make it slower than a tuned
  port would be (unfairness item 15). Python's helper stand-ins are subtracted, while its live helpers, which read
  files, probably cost more than the stand-ins; the port reads the same answers from its snapshot and a production port
  would have to compute them (item 3). Neither is measured.

**The instructive exception: large4, 3.11 on arm64 (2.96 to 3.59) and 4.26 on x86_64 (3.91 to 4.63).** This is the same
rule on the large world's four designed sessions without its 34 fillers. The port's held build is 1.22 ms there, and the
cheap bound, which walks no closed turns at all, is already 1.04 ms. Almost all of it is one dictionary:
`_dictated_prompt_uuid`'s prompt index over s001's 24,000 atoms (0.9 ms in the review's profile). Both sides build it on
demand, once per derivation of s001. Python's dict reuses each uuid string's cached hash, while the port's std HashMap
hashes every uuid with SipHash on every build. On the 38-session world that index is a small share among 37 other
sessions' work, which is why the ratio there is 5.2. So the ratio belongs to the session mix, not to the port as a single
number: 8.4 on four short sessions, 5.2 on 38 mid-sized ones, 3.1 when one long session dominates. As a continuity check,
large4 on x86_64 3.12 reproduces S2's figures again: an empty-memo build of 90.3 ms (87.8 to 91.3) and a region of 88.7,
against S2's 90 and 87 ms.

**A label in the summary to discount.** The driver labels every plain-build ratio "unfair upper bound". That is right for
the noHold and emptyMemo plain builds over the port's whole build (10.5 and 11.0 on arm64): they carry Python's frame,
key, memo and file work. It is wrong for the heldWarm, heldNoLanding and memoServed plain builds over it (0.92, 0.92 and
0.07): the port's whole-build row walks every turn with no held aggregate (20.2 ms), so those three set Python's held or
memo-served build against the port's full walk, and they are unfair to the port, not to Python. Nothing here uses them.
The plain cold ratio (9.8) stays an unfair upper bound (item 10).

### The steady state: memoized Python is cheaper than the memo-less port

The deciding row compares derivation with derivation. In production, most sessions in a build are memo hits and derive
nothing, and the port has no memo. The `/perf` window (477 s) had 140 builds and 740 cached feed requests, so 15.9% of
feed requests ran build_feed. Those builds made 840 session lookups, 775 hits and 65 misses: a miss rate of 7.7% per
session per build, 0.46 misses per build over 6 sessions. 95% of the closed turns those misses walked were served from
the held aggregate (19 per walk). Since boot (13.6 hours) the rate was 7.25%; the review's 13-minute window had 2.9%. The
window includes this measurement's own session, whose transcript grew while it ran.

At that rate, the large world's 38 sessions cost memoized Python 1.56 ms per build on arm64 (1.52 to 1.61) with misses
spread evenly, and 2.25 ms (2.20 to 2.33) with production's 0.46 misses per build all on s001, the largest session. The
port's held build costs 3.07 ms. So memoized Python costs 0.51 and 0.73 of the port, and on x86_64 2.14 and 3.04 ms (0.70
and 0.99). The key is left out of both the hit and the miss, which flatters Python (the replay's stand-in key understates a
production hit about tenfold, "S6 method"). The port would draw level with memoized Python at a miss rate of about 17% (12% on x86_64),
or 0.65 misses per build on s001. To be five times cheaper it would need a miss rate of about 89%. Charged at emptyMemo's
cost, with no held aggregate, the blend would be 17.3 and 19.6 ms (5.6 and 6.4 times the port), but production served
95% of closed turns from the aggregate, so that is not its path. On the memo-served row alone, Python's whole memo-served
build (1.46 ms, frame and key included) is under the port's 3.07 ms derivation, and its hit without the key (0.48 ms) is
0.16 of it.

So, as the decision rule said in advance, the steady state cannot pass without the memo ported too. With the memo it
would compare hit with hit, which neither side has measured.

### The memory gate: unclear, and leaning toward a fail as built

**In one sentence:** on the synthetic transcripts the store needs a quarter of Python's memory, which passes, but those
transcripts swell in Python's cache far more than real ones are thought to, and at the real expansion the same store
would need about 0.39 to 0.42 of Python's memory, which fails.

The figures (large world: 82.7 MB of transcripts in 38 files, 135,005 records cold; 6 append rounds to the 4 largest
files added 62 records and 37,958 bytes; footprint growth over each process's own baseline, in MB, median of 8 with
minimum to maximum):

| | Cold | After 6 rounds | Bytes per file byte, after |
|---|---|---|---|
| Store, whole-line blocks (the decided policy) | 96.5 (96.4 to 96.5) | 99.6 (99.5 to 99.6) | 1.20 |
| Store, S3's doubling buffer | 94.1 | 99.9 | 1.21 |
| Python cache, arm64 3.13.18 | 400.1 (400.1 to 402.1) | 400.8 (400.4 to 403.6) | 4.84 |
| Python cache, x86_64 3.12.11 | 402.3 (400.8 to 403.2) | 403.8 (402.8 to 406.5) | 4.88 |
| Python cache, traced (both interpreters) | 373.2 | 373.4 | 4.51 |

The store over Python's cache, paired by run: 0.241 (0.240 to 0.241) cold and 0.248 (0.247 to 0.249) after the rounds
against arm64; 0.240 and 0.247 against x86_64; S3's doubling buffer 0.235 and 0.249. Rescaled so Python expands the files
as event_model's own estimate for real transcripts says (2.85 bytes per file byte): 0.409 cold and 0.422 after. Rescaled
to S3's lab resident growth (3.1): 0.376 and 0.388.

After appends, the whole-line blocks grew 2.4 MB at the first round and 0.7 MB over the next five. Their live bytes did
not move (93.2 to 93.3 MB in use) while the malloc zone's total went from 94.8 to 99.0 MB at round 1 and stayed there:
the index buffers that each read's shrink-to-fit replaces, freed and kept by the allocator and reused afterward. The
doubling buffer's live bytes went from 96.3 to 117.0 MB (the doubled buffers of the 4 grown files). Pressure relief
handed back 0 bytes at every reading, on both sides. Python's footprint moved 0.7 MB on arm64 and 1.5 MB on x86_64.

Why believe the measured figures: both sides counted the same records after every round (135,067); the cache evicted and
dropped nothing; clearing it gave back all but 0.019 MB of its 373 traced MB; and no reading's resident size drifted from
its footprint or showed compressed bytes. The small world is too small to read: Python's footprint does not move for 0.2
MB of files (a growth of 0.0 MB, so no ratio), though its traced growth, 0.90 MB, is the same 4.5 bytes per file byte.

**What would change it.** The store keeps every file's raw bytes, so it needs at least 1.0 byte per file byte. It can
pass only if Python's cache expands real transcripts by more than 3.0 times, and by more than 3.6 times at this world's
index overhead (1.20 bytes per file byte after appends). The two real-transcript figures on record, 2.85 and 3.1, are
under both. Real records are larger than the synthetic ones (613 bytes on average), so the index costs less per byte
there and the store's own figure would fall toward 1.0, but not by enough on these figures. The deciding measurement is
`measure_drive.py --mem --mem-dir DIR` on a read-only temp copy of real transcripts, which needs lczh's OK. If it fails,
the fallback is already named ("The store's growth policy"): hold only the index (0.11 bytes per file byte here) and
fetch each record by a positioned read, which would pass at any plausible expansion, at the cost of a read per record.

### The verdicts

- **CPU gate: pass, by the rule fixed before the run**, at 5.24 (5.03 to 5.51, n=8). As a claim about production it is
  unclear. The margin is within the method's own correction choices (one reasonable alternative gives 5.15 with a run at
  4.96). The ratio depends on the session mix (3.11 on large4, 8.38 on small), and no world matches production's shape
  (6 sessions per build with about 20 closed turns each, plus lazy atoms and stored segments the port's loader refuses).
  What would move it: a production-sized world, which needs the loader to admit pre-cut turns and lazy atoms; tuning the
  port without a new crate (an in-tree FxHash-style hasher for the uuid maps, which large4 points at, then LTO and one
  codegen unit); or a quieter rerun, though the 8 runs agree to within 10%.
- **The steady state: fails.** Memoized Python costs 0.51 to 0.73 of the memo-less port per build at production's miss
  rate. Only a ported memo can change this.
- **Memory gate: unclear, leaning toward a fail as built.** It passes as measured (0.248 after appends) and fails at
  real transcripts' expected expansion (0.39 to 0.42). Real-shaped transcripts decide it.
- **For the plan's question (S7):** the port's derivation is about five times cheaper only on a favorable session mix
  and only before the memo is counted, and the raw-bytes store probably misses a third. That points toward the plan's
  staged fallback ("The question the spike answers"): the store first, behind the Python kernel, redesigned to hold only
  its index if real transcripts confirm the miss. The recommendation is S7's to make, after the real-transcript number.

## Log

- 2026-11-25: spike started (lczh: "when we are on the A Fellows org, start working on the rust migration, pace
  yourself"). Toolchain installed and pinned.
- 2026-11-25: S0 scaffold. S1 romp-json. One finding: Rust's shortest float formatting (`{:e}`) rounds an exact
  tie at the last digit up, while Python keeps the even digit (`-271556098175374.125` gives `...374.12` in
  Python). It took 8 of 3,000 random floats. Fixed by taking only the digit count from `{:e}` and the digits from
  the exact formatter at that length.
- 2026-11-25: S2 oracle (`tools/rust-spike/dump_world.py`).
  - Small: 4 sessions, 7 cards, a 17,878-byte canonical oracle, a 0.6 MB dump, 1.6 s.
  - Large: 38 sessions (s001 24,001 records, the others 3,000, and s003 and s004 3,002 each with their appended
    open turn), 41 cards, a 114,338-byte oracle, a 163 MB dump
    (trees 79.6 MB, transcripts 82.7 MB), 27 s.
  - The fresh-process replay from the dump alone matched canonical, wire and entry bytes on both worlds. All 6 of 6
    planted input changes moved the cards.
  - `--check`: 23 of 23 and 159 of 159 files identical across seeds 11 and 29 and two roots. A third standalone
    run with a random seed wrote the same manifests.
  - Coverage: 85 of 86 listed functions reached, 1,156 of 1,591 of their lines, and `_feed_session_entry` 309 of
    362 statements (all 90 top-level ones).
  - The boundary atom (79 code points, 82 UTF-8 bytes) moves s001 g1's `summaryAnchorUuid` when made 80 code
    points long, so a byte-counting port fails the gate.
  - The oracle build takes 405 ms of CPU on the large world under the recording wrappers. That is not a timing
    (timing is S6).
- 2026-11-25: S3 romp-transcript (`tools/rust-spike/check_store.py`).
  - Dumps: small, 4 files and 325 records; large, 38 files and 135,005 records (82.7 MB). Every summary line is
    identical to Python's, with records compared in full on both worlds (the large one also by CRC). Mismatches 0.
  - Edge corpus: 62 lines (40 kept by Python) plus an unreadable file, all identical except the 3 named decoder
    refusals (1 lone surrogate, 2 nested past 512).
  - Identity cases: 8 cases, 42 lockstep steps, mismatches 0. Every step was read by Python as it was built to be:
    zero 10, grown 11, hit 6, rewrite 4, shrunk 3, guard 2, touched 2, absent 2, failed 2.
  - Planted defects, each caught: no-tool-result by 120 lines of the small world, vt-strip by 40 corpus lines (its
    record goes missing and every later index shifts), skip-held-crc by 5 steps, starting at the same-size rewrite
    outside the guard, which Python reads as a rewrite and the planted store as a touch.
  - Rust tests: 27 (13 unit, 14 identity cases on temp files). No new crate: `romp-transcript` depends only on
    `romp-json`.
  - Held for the large world: 82.7 MB of bytes plus a 9.45 MB index, 96.0 MB allocated. Projected at 0.37 to 0.41 of
    Python's per-byte cache figures, above the one-third gate (S6 measures).
- 2026-11-25: S3 review fixes (`check_store.py --dump small --dump large --fuzz 6`: OK in 20.5 s of wall time).
  - Decoder: a value under a repeated key that a later value replaced could still refuse the record. The reviewer's
    differential fuzz found 154 of about 103,000 records. Now only a lone surrogate or deep nesting that survives to
    Python's record refuses one.
  - Edge corpus: 69 lines (7 added: 4 now decode in full, 3 are named refusals), mismatches 0, expected 3 lone
    surrogate and 3 deep. Identity cases: 9 cases, 46 steps, mismatches 0. The added steps are a partial cut short
    past the held offset (rewrite) and same-size edits at offset - 64 (guard) and offset - 65 (rewrite).
  - `--fuzz 6`: 180,000 mutated lines (73,296 records kept by Python), mismatches 0, with 15,205 lone-surrogate and
    5,894 deep records named. 1,200 random lockstep steps, mismatches 0.
  - Each new check fails on its target. The committed decoder fails 4 corpus lines and 8,272 fuzzed records, every
    one a wrong refusal. Mutant m1 (growth judged against the held offset) fails the new shrink step and 805 fuzz
    steps. Mutant m3 (a 32-byte guard) fails the offset - 64 step and 101 fuzz steps. The vt-strip and skip-held-crc
    plants fail 12,154 fuzz lines and 141 fuzz steps.
  - Rust tests: 32 (16 unit, 16 identity cases). Both dumps still match line for line, mismatches 0 (small: 325
    records in full; large: 135,005 records by CRC).
  - Memory: after one appended line per file of the large world, 178.7 MB allocated (2.16 bytes per held byte), up
    from 96.0 MB cold. S6 must measure after appends.
- 2026-11-25: S4.1, the typed loader and layer 1.
  - Round trip through the typed structs and romp-json: snapshot and trees identical on both worlds (small 4 of 4
    trees, large 38 of 38, 79.6 MB). A planted float-tag loss is caught.
  - Layer 1, 21 functions against Python's own answers: 6,370 values on the small world and 2,499,514 on the large, 0
    differences; 1,206 on 67 synthetic edge atoms, 13 more refused (all 6 where Python raises among them). A planted
    byte count is caught on exactly the boundary atom (82 against 79) in both worlds.
  - serde_json's default parser read 14,434 of 100,000 fractional times below 2e9 (and 59,330 of 199,913 random
    bit patterns) one ULP off; `float_roundtrip` (no added crate) reads them all exactly.
  - Rust tests: 27 unit tests in romp-views, 7 edge tests, 2 in romp-spike; the workspace's 40 pass.
- 2026-11-26: S4.2, layer 2 (segmentation), on fresh dumps (`dump_layer2.py`, `romp-spike layer2`).
  - Worlds: small 42 turns, 42 segments, 336 values; large 16,877 turns, 16,877 segments, 135,016 values; 0
    differences, counts equal to Python's. Neither world calls em.split_segment (10 and 3,000 pieces examined, 0 split
    calls).
  - Edge set: 109 cases; 99 match on 1,202 values and 297 counts, 0 differences; 10 refused at load (every REFUSE
    case); Python's 5 KeyErrors matched as the same exception; 89 splits tried, 38 made.
  - Planted: `end` caught on small (4), large (38) and the edge set (97); `bytes` caught only by the edge set, on its
    two 79-code-point tails.
  - Re-run after the loader change (null `placedEchoes` entries, `seams`, `segs`): round trip identical and layer 1 at
    0 differences on both worlds (6,370 and 2,499,514 values). Rust tests: 86 pass.
- 2026-11-26: S4.2 review follow-ups.
  - Empty trigger pinned: an edge case (no input, trigger uuid "", a later atom with uuid "") makes the fixture
    110 cases, 100 loaded (1,210 values, 300 counts), 0 differences. Removing `layer2.rs`'s filter on "" now fails it
    on exactly that case (it passed the 109-case fixture). The fixture's header coverage no longer counts the
    generator's own em.split_segment and jd._seg_key calls (pieces 139, split calls 96, splits 41, down from 180, 100
    and 45); the cases are unchanged apart from the added one.
  - `dump_layer2.py --fuzz`: seeds 1 to 6 at 10,000 cases, 590,031 values and 180,000 counts, 0 differences. Seed 1
    catches both plants (end 5,197, bytes 216) and all 12 source mutants tried (3 to 5,037 differences each).
  - Rust tests: 86 pass.
- 2026-11-26: S4.3, layer 3 (tree facts and the anchor walk), on fresh dumps (`dump_layer3.py`, `romp-spike layer3`).
  - Worlds: small 4 sessions, 42 turns, 42 keys, 1,801 values; large 38 sessions, 16,877 turns, 16,877 keys, 695,672
    values; 0 differences. The held aggregate the port leaves out agreed on every session (4 of 4, 38 of 38), serving
    every closed turn (38 and 16,839).
  - Edge set: 135 cases; 128 load and match on 6,444 values, 0 differences; 7 refused at load (every REFUSE case),
    Python raising on 3 of them.
  - `dump_layer3.py --fuzz`: seeds 1 to 6 at 10,000 cases, 60,000 cases (55,487 sessions, 4,513 position sets),
    8,059,862 values, 0 differences, 0 refused; 28,379 overwritten keys, 7,621 walks stopped by a raise, 6,054
    suspended sessions reached.
  - Planted: 4 `--plant` options and 21 source mutants, every one caught by the edge set and by fuzz seed 1 (1 to
    36,400 differences); the worlds catch only the byte count, at the boundary atom. The first round's edge set missed
    one mutant (an empty reply uuid kept) and its fuzz missed two (a zero time kept, the first fallback); each was
    closed with a case or a generator change.
  - `board.downtime` typed as number pairs; the round trip and layers 1 and 2 still match on both worlds (6,370 and
    2,499,514 layer-1 values, 336 and 135,016 layer-2 values). Rust tests: 102 pass.
- 2026-11-26: S4.3 review follow-ups. Verdict ship, no must-fix. Added the edge case the review found missing: two
  substantive atoms in one segment, then a short one. A port that cited only the segment's last substantive atom passed
  the worlds and the old edge set; only the fuzz caught it. The edge set is now 136 cases, 129 loaded, 6,501 values, 0
  differences. The docs no longer present the raw-content tool count, the plain-prompt split or its `or 0` as pinned
  traps: they agree with the simpler readings on every shape the loader admits. layer3.rs states the one-map design's
  dependency (nothing raises between `pos[k]` and `seg_best[k]`).
- 2026-11-26: S4.4, layer 4 (the store helpers), on fresh dumps (`dump_layer4.py`, `romp-spike layer4`).
  - Worlds: small 4 sessions, 10 nodes, 6 names, 864 values; large 38 sessions, 78 nodes, 40 names, 6,406 values; 0
    differences. The build check compared every helper call the replay's per-card body makes (87 and 597), 0 mismatches.
  - Edge set: 205 cases; 195 load and match on 23,302 values, 0 differences; 10 refused at load (every REFUSE case),
    Python raising on 2 of them. Python ran 273 of the 278 lines of the 29 functions; the 5 left cannot run in the feed.
  - `dump_layer4.py --fuzz`: seeds 1 to 6 at 10,000 cases, 30,602,877 values, 0 differences, 0 refused.
  - Planted: 5 `--plant` options and 56 source mutants, every one caught by the edge set and by fuzz seed 1; the worlds
    catch 5 of the 61.
    The first round's edge set missed 4 source mutants and the full-tie plant; the fuzz missed 1; each was closed with a
    case or a generator change, and the table was run again.
  - Findings: dump_world's replay handed _peer_stamp_superseded a list where the live helper returns a tuple (a
    TypeError on any anchored stamp; fixed, both dumps byte-identical after); Python's `$` and `lower()` readings
    pinned; Python's big ints in answer files are read with romp-transcript's decoder.
  - Loader types for layer 4 added; the round trip and layers 1 to 3 still match on both worlds (6,370 and 2,499,514
    layer-1 values, 336 and 135,016 layer-2 values, 1,801 and 695,672 layer-3 values). Rust tests: 126 pass.
- 2026-11-26: S4.4 review follow-ups (no must-fix), on fresh dumps.
  - Edge set: 225 cases (20 added); 210 load and match on 25,011 values, 0 differences; 15 refused at load (every
    REFUSE case). Each of the 11 mutants the review showed the edge set missing (R3, R5, R6, R7, R8, R10, R11, R12, R13,
    R17, R18) gives 0 on the old 205 cases and fails the new set on exactly its added case.
  - `dump_layer4.py --fuzz`: U7 (a postal name with a colon) and a negative node t added; seeds 1 to 6 at 10,000 cases,
    30,625,221 values, 0 differences, 0 refused. R12 and R17, which no check caught before, now fail seed 1 (2,230 and
    23 differences).
  - Mutant table rerun on the final bytes: 5 plants and 78 source mutants (the first round's 56, the review's 18, 3
    order mutants, the big-int refusal removed), every one caught by the edge set; fuzz seed 1 misses 2 (X3, B1); the
    worlds catch 5.
  - Loader: an integer literal past i64 in the snapshot or an edge or fuzz case's input is refused (serde_json read one
    past u64 as a float); romp-spike checks the layer 1 to 3 answer files and the layer 2 and 3 edge files whole.
    Trees are not checked (it would triple their load). Re-run: the round trip and layers 1 to 4 match on both worlds
    (6,370 and 2,499,514 layer-1 values, 336 and 135,016 layer-2, 1,801 and 695,672 layer-3, 864 and 6,406 layer-4),
    the layer 2 and 3 edge sets and one fuzz seed of each (97,398 values and 30,000 counts; 1,345,684 values), 0
    differences.
  - SPIKE.md carries the body's flatten order for _node_anchor_uuids forward to layer 6. Rust tests: 131 pass.
- 2026-11-26: S4.5, layer 5 (landing, delegation and provisional), on fresh dumps (`dump_layer5.py`,
  `romp-spike layer5`).
  - Worlds: small 4 sessions, 42 turns, 10 nodes, 1,735 values; large 38 sessions, 16,877 turns, 78 nodes, 624,750
    values; 0 differences. Build check: every layer-5 call the replay's body makes (7 and 41), 0 unmatched, 0
    mismatches; memo check 12 of 12 per world.
  - Edge set: 415 cases; 404 load and match on 14,856 values, 0 differences; 11 refused at load (every REFUSE case),
    Python raising on 3. Python ran 263 of the 275 lines of the 23 functions; the 12 left cannot run in the port's
    input set.
  - `dump_layer5.py --fuzz`: seeds 1 to 6 at 10,000 cases, 9,054,772 values, 0 differences, 0 refused.
  - Planted: 6 `--plant` options and 58 source mutants, every one caught by the edge set; fuzz seed 1 misses 4; the
    worlds catch 3. 4 more source mutants are equivalent (no admitted input tells them apart) and are documented as not
    pinned. One reading in the notes did not hold: their example of what a lowercase comparison misses, the Kelvin
    sign, is one it gets right (corrected in the review round; this line first named `ß`, which the notes never
    mention).
  - Python's `\d`, `\w` and case tables generated from its engine (`dump_layer5.py --tables`, `pyre_tables.rs`) and
    checked on every code point.
  - Loader: placements may be null or hold null values, nodes type askAnchor, userAsk, followupAt and briefedMt,
    captions are typed, a truthy non-string atom `text` is refused. Re-run: the round trip and layers 1 to 4 match on
    both worlds (6,370 and 2,499,514 layer-1 values, 336 and 135,016 layer-2, 1,801 and 695,672 layer-3, 864 and 6,406
    layer-4) and on the layer 2 to 4 edge sets, 0 differences. Rust tests: 148 pass.
- 2026-11-26: S4.5 review follow-ups (no must-fix), on fresh dumps.
  - Edge set: 425 cases (10 added: 2 sessions, 8 direct calls); 414 load and match on 14,985 values, 0 differences; 11
    refused at load (every REFUSE case). Each of the 10 mutants the review showed the edge set missing (R09, R40, R43,
    R48, R52, R70, R76, R33, R37, R65) gives 0 on the old 415 cases and fails the new set on exactly its added case.
    The same file under PYTHONHASHSEED=7.
  - Mutant table: 6 plants and 68 source mutants, every one caught by the edge set; fuzz seed 1 misses 7 (M05, M11,
    M24, M34, R76, R37, R65); the worlds catch 3. 25 first-round mutants rebuilt from their descriptions (each gives
    its first-round cells on the 415-case set and seed 1); on the 425-case set five catch more (M10 2 in 2, M19 5 in 5,
    M40 387 in 145, M47 68 in 68, M49 28 in 14).
  - Re-run: the round trip and layers 1 to 5 on both worlds (6,370 and 2,499,514 layer-1 values, 336 and 135,016
    layer-2, 1,801 and 695,672 layer-3, 864 and 6,406 layer-4, 1,735 and 624,750 layer-5; build check 7 and 41 calls,
    0 mismatches; memo check 12 of 12 per world), the layer 2 to 4 edge sets, and `dump_layer5.py --fuzz` seeds 1 to 6
    at 10,000 cases (9,054,772 values) and seed 101 (1,490,668), 0 differences, 0 refused. Rust tests: 148 pass.
  - The summary-anchor memo is a pure cache only within one build or across builds of the same parse and seams: a seam
    stamped between builds makes a warm hit serve the pre-seam landing (reproduced with the real kernel functions).
    Reworded in `layer5.rs` and SPIKE.md, carried into "What layer 6 needs" and step S6; flagged for lczh as a
    Python-side stale landing.
- 2026-11-26: S4.6, layer 6 (the body and its closures), on fresh dumps (`romp-spike layer6`, `dump_layer6.py`).
  - Worlds: every entry byte for byte against the S2 dump's `oracle/entries/`: small 4 sessions, 7 cards, 17,470 bytes;
    large 38 sessions, 41 cards, 114,100 bytes; 0 differences. The harness reproduces those entries itself (4 of 4,
    38 of 38) and runs 424 of the body's 592 lines on the worlds.
  - Edge set: 283 worlds; 262 load and give all 276 entries byte for byte (304 cards, 28,878 values), 0 differences;
    21 refused at load (every REFUSE case); 25 entries raise identically. Python ran 585 of the body's 592 lines; the 7
    left cannot run on an admitted input.
  - `dump_layer6.py --fuzz`: seeds 1 to 6 at 4,000 worlds, 40,058 entries, 3,813,717 values, 0 differences, 0 refused;
    4,112 entries raise identically in both.
  - Planted: 6 `--plant` options and 60 source mutants. The edge set catches every plant and 59 mutants (M52 is
    equivalent); fuzz seed 1 catches every plant and 44 mutants (M33 by crashing the run); the worlds catch 2. The first
    sweep's edge set missed 12: 10 worlds were added and 3 cases kept needs-you, and each of the 11 now fails.
  - Loader: the 13 node keys, handoff and origin `tracked` and `peerHost`, and three recorded answers typed;
    `layer6::validate` refuses the shapes layer 6 would read differently. dump_world.py records _bg_owner_tops and
    _bg_service_descs; both worlds re-dumped byte-identical (every file but `run.json`). Re-run: the round trip and
    layers 1 to 5 on both worlds and the layer 2 to 5 edge sets, every number as before. Rust tests: 156 pass.
  - Findings: the replay's cleared set is a Python set where the frame's `_cleared_foreign` reads a dict (layer 7);
    the release build makes the large world's entries in 31 to 33 ms (ten runs), against Python's 87 ms derivation
    (about 2.7x, short of the 5x gate; not a measurement), which S6 should profile before it times. [Retracted by
    S6's review: the 87 ms is a 4-session world's, the 31 to 33 ms the 38-session world's; see "S4.6 as built".]
- 2026-11-26: S4.6's review round (two must-fix), on fresh dumps.
  - Must-fix: `pystr::upper` reads Python's own `str.upper()` table (`dump_layer6.py --tables` writes
    `pystr_tables.rs`), checked on every code point; Rust's `char::to_uppercase` (Unicode 18.0) differed from Python
    3.12's (15.0) on 76 code points, which the awaiting placeholder's headline showed. A current ask's truthy question or
    header that is not a string now raises AttributeError as Python does (only the call is inside the `try`). 10 edge
    worlds pin both; the build before the round fails 7 of them (9 differences).
  - 17 edge worlds, one per review mutant the first edge set missed; each mutant, rebuilt in a scratch copy, gives 0 on
    the old 283 worlds and fails the new 310 on its own case. R44 is equivalent.
  - Re-run: the round trip and layers 1 to 6 on both worlds, every number as before; the layer 2 to 6 edge sets (layer
    6: 289 load, 305 entries, 31,321 values, 0 differences, 21 refused); the fuzz of layers 2 to 6, seeds 1 to 6 (layer
    6: 3,813,717 values; layers 2 to 5: 590,031, 8,059,862, 30,625,221 and 9,054,772), 0 differences; check_store,
    mismatches 0. Rust tests: 158 pass.
  - Docs: the placeholders' `_ageT` in "What layer 7 needs", the `validate` and upper claims, and a comment on the
    left-out `_note_peers([handoff_to] ...)`, a dead call in Python (for the bug lane).
- 2026-11-26: S4.7, layer 7 (the loop, the fold and the cross-session pass), on fresh dumps (`romp-spike feed`,
  `dump_layer7.py`).
  - Worlds: the whole feed built in Rust, canonical and wire bytes equal to oracle/feed.canon.json and feed.wire.json:
    small 4 sessions, 7 cards, 17,878 bytes, 826 values; large 38 sessions, 41 cards, 114,338 bytes, 5,348 values; 0
    differences. The harness reproduces both oracles itself.
  - Edge set: 131 worlds; 107 load and give the whole feed byte for byte in both key orders (165 sessions, 391 cards,
    29,926 values), 0 differences, one build raising in both; 24 refused at load (every REFUSE case); 5,797 tint and ramp
    points equal Python's. Python ran 371 of the 414 lines; the 43 left cannot run in a fresh replay.
  - `dump_layer7.py --fuzz`: seeds 1 to 6 at 4,000 worlds, 24,000 worlds, 85,434 cards (9,186 parked, 44,017 notices),
    6,692,301 values, 0 differences, 0 refused; 637 builds raise in both.
  - Planted: 6 `--plant` options and 52 source mutants. The edge set catches every plant and 48 mutants (two by a
    panic); the other 4 are equivalent (the overlapping clamps, the fresh build's restamp, the op filter). Fuzz seed 1
    misses 4 more (M01, M02, M07, M48); the worlds catch 10 rows. Of the first sweep's gaps, M01 in a feed and M22 were
    closed with two worlds and a fixed case, and three of the fuzz's four misses with generator changes (M48 stays a
    fuzz miss); the table was run again on the final bytes.
  - The replay's cleared set answers a dict (dump_world.py); both worlds re-dumped at one HEAD, every file but
    `run.json` byte-identical (23 of 23, 159 of 159). The layer 6 fixture, regenerated, loses only the note of the old
    raise. Re-run: the round trip and layers 1 to 6 on both worlds, every number as before; the layer 1 to 7 edge sets
    (`cargo test`). Rust tests: 170 pass.
  - Findings: a null card in an entry's asks fails the whole Python build (the fold runs outside the per-session guard;
    bug lane); colormap.py's docstring names a stale default; age_rgb's and ramp's clamps overlap.
- 2026-11-26: S4.7's review round (two must-fix), on fresh dumps.
  - Must-fix: a parked send whose id or recipient fails kernel._safe_id is refused at load (Python finds its mail by a
    path lookup that drops `.` components and trailing slashes; the port reads `mailNew` as exact pairs), and the harness
    refuses a world whose `mailNew` its own filesystem contradicts (APFS: a send `M1` finds a file `m1`); a float clock
    past i64 is refused while the postal log holds a parked send (the port's `int(now)` saturated). The reviewer's six
    probe worlds gave 9 differences in 6 before the fix; after it, five are refused at load and the case-only collision
    by the harness. 12 REFUSE worlds.
  - Should-fix: 8 edge worlds, one per review mutant the edge set missed (R01, R25a, R25b, R10, R11, R36, R06, R35), each
    0 on the old set and caught by the new; the missed-lines claim corrected for the maildir stat; S6's next action
    names the I/O asymmetry.
  - Edge set: 154 worlds; 118 load and give the feed byte for byte (32,013 values), 0 differences; 36 refused (every
    REFUSE case); 5,797 tint points equal. `dump_layer7.py --fuzz` seeds 1 to 6 at 4,000 (recipients now safe ids):
    6,629,814 values, 0 differences, 0 refused. The review round's 21 mutants and 8 re-run first-round mutants are each
    caught by the edge set; the six plants by the edge set and fuzz seed 1.
  - Re-run: the round trip and layers 1 to 7 on both worlds, every number as before (the feed's 17,878 and 114,338
    bytes identical), the harness against both oracles, the fuzz of layers 2 to 6 at seeds 1 to 6 (590,031, 8,059,862,
    30,625,221, 9,054,772 and 3,813,717 values), 0 differences; check_store, mismatches 0. Rust tests: 173 pass.
- 2026-11-26: S6, the measurement harness (`measure.py`, `romp-spike measure`, `measure_drive.py`), on fresh dumps of
  both worlds; the method and the known unfairness list are under "S6 method".
  - Smoke runs only, no figure quoted: 32 alternated runs (2 per side, both worlds, CPython 3.12 x86_64 and 3.13.18
    arm64, CPU and memory), 0 failed. Every Python row's checked build equaled the oracle (canonical, wire, entries);
    the guards saw nothing; every calibration spin's CPU agreed with wall time; both sides counted the same records
    after every append round; the store's counted allocation equals S3's (96.0 MB cold, 178.7 MB after one round).
  - Planted: an oracle changed by one character makes both sides refuse to time (exit 2); an append that adds nothing is
    flagged by both memory sides.
  - Found: the 42x thread_time under-report is Rosetta's (arm64 3.13's thread_time agrees with wall time); s2-design's
    87 ms empty-memo derivation matches neither Python row (a profile puts the empty-memo build in the walk over all
    16,877 turns); the held anchor aggregate, which the port lacks, moves Python's derivation by about an order of
    magnitude. Both are open for S6 proper.
  - Rust tests: 175 pass (2 new in `measure.rs`: the C struct layouts, the CPU clock against a spin and the memory reads).
- 2026-11-26: S6's design review applied (6 must-fix, 12 should-fix; "The review round" under "S6 method"), smoke runs
  only, on fresh dumps of the small, large and large4 worlds.
  - Retracted: S4.6's informal 2.7x, which set the port's 38-session build against s2-design's 87 ms from a 4-session
    world; `dump_world.py --world large4` reproduces S2's 90 and 87 ms (89.7 to 90.3 and 88.5 to 89.6 ms on 3.12).
  - The CPU gate's decision rule written down before S6 proper: Python's heldWarm region over the port's held build,
    large world, arm64 3.13, median paired ratio over at least 6 runs, pass at 5.0; the secondary row noHold over the
    port's region ("same work, interpreter vs compiled"). The memo-less steady state cannot pass without the memo.
  - Ported: the held anchor aggregate (`layer3::SegHeld`), after the cheap bound passed (6 to 8x); byte-identical on all
    three worlds, its counters equal to Python's per build (large: 38 walks, 16,839 turns held, 38 walked). The port's
    `_dictated_prompt_uuid` index is built on demand as Python's is (built eagerly it was 8 to 10 ms of a large build).
    `layer7::fold_on` times the region alone. The store's growth policy decided: whole-line blocks (`Growth::Blocks`,
    the default), S3's buffer kept as `Growth::Double`.
  - Harness: five Python rows (noHold, emptyMemo, heldWarm, heldNoLanding, memoServed) and seven Rust rows, round-robin
    with alternating orders and a position-balanced statistic; paired ratios; gen2 builds dropped; spin rates at both
    ends; per-session attribution; the overlap behind the negative residual found and removed (the clears' wrapped
    `_feed_memo_forget`); the residual floor and wrapper checks; the production blend from two `/perf` snapshots;
    memory by footprint with resident, mstats and pressure-relief readings beside it, an append schedule and its curve,
    bytes per file byte and the 2.85 and 3.1 projections; the tracemalloc self-check enforced.
  - Smoke figures, not results: the deciding ratio 5.52 (5.28 to 5.76, n=2) and, in a later pair on the final bytes,
    5.02 (4.84 to 5.20) on arm64 3.13, so it sits at the threshold; 6.63 on x86_64 3.12; the 5.52 runs gave 4.92 by a
    plain median before the position balance. Large4: 3.3 (one prompt index over s001's 24,000
    atoms is most of the port's held build there). Blend: arm64 Python 0.78 to 1.03 ms per build against the port's 3.0
    ms. Memory: 0.25 measured, 0.41 to 0.42 rescaled to real transcripts' expansion, so the memory gate waits on
    real-shaped transcripts (lczh's OK).
  - Re-run: the round trip, layer 6 and the feed on all three worlds and layers 3 and 5 on the small and large ones
    (every number as before; large4's feed 17,890 bytes identical); the layer 2 to 7 edge sets; `dump_layer3.py` and `dump_layer5.py --fuzz 10000 --seed 1`,
    `dump_layer6.py` and `dump_layer7.py --fuzz 4000 --seed 1`, 0 differences; check_store (both dumps, the edge corpus,
    46 lockstep steps, 6 fuzz seeds of lines and of steps), mismatches 0. Rust tests: 180 pass (5 new: three for the
    held aggregate, two for the growth policies).
- 2026-11-26: S6 proper, the full alternated measurement ("S6 results"), on fresh dumps of the small, large and large4
  worlds at afdaaf242 (Rust tests: 180 pass). 8 runs per configuration: CPU on all three worlds under arm64 CPython
  3.13.18 and x86_64 3.12.11 (72 runs), memory cold and over 6 append rounds on small and large with both growth
  policies and the relief readings (160 runs); 0 failed. Every run is raw in `rust/notes/s6-runs.json`, and the summary
  recomputes from it identically. The two `/perf` snapshots were read with a GET only, with no restart between them.
  - CPU gate, by the rule fixed in advance: pass, narrowly. heldWarm region over held is 5.24 (5.03 to 5.51, n=8) on
    arm64 3.13 (16.14 ms against 3.07) and 6.80 on x86_64. The secondary row (same work) is 10.47 and 16.74. The smoke
    pairs' 5.52 and 5.02 settle at 5.24. Under one alternative wrapper correction it is 5.15 with one run at 4.96.
  - The instructive exception: large4 gives 3.11 (one prompt index over s001's 24,000 atoms, SipHash in the port against
    CPython's cached string hashes); small gives 8.38. Large4 on 3.12 reproduces S2's 90 and 87 ms (90.3 and 88.7).
  - Steady state: the window's miss rate is 7.7% (65 of 840; 7.25% since boot). Memoized Python costs 1.56 to 2.25 ms
    per build against the memo-less port's 3.07 ms, and the port draws level only at about a 17% miss rate.
  - Memory: the store over Python's footprint is 0.241 cold and 0.248 after appends (96.5 and 99.6 MB against 400.1 and
    400.8). Rescaled to real transcripts' expected expansion, 0.39 to 0.42 after appends. Unclear until real-shaped transcripts are
    measured (lczh's OK). Relief released nothing; the whole-line blocks' live bytes stayed flat over the rounds.
  - Flagged: the driver labels the plain heldWarm, heldNoLanding and memoServed builds over the port's whole build
    "unfair upper bound", but the port's whole-build row walks every turn, so those three are unfair to the port.
