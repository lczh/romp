# The Rust spike: status and step plan

This is the first step of `plans/rust-migration.md`: its one-week benchmark spike, done at a slow pace while the
work is affordable. It ports the transcript store and `build_feed`'s per-card build to a standalone Rust program
with no server. Both implementations run on one synthetic state, and the feed JSON is compared byte for byte.

**Status (keep this current: a session that stops mid-step resumes from here):**

- Step: S4.5 (layer 5) done on branch `romp_fix-s45`, not yet reviewed or merged into `romp_fix`. S3 and S4.1 to
  S4.4 done, merged into `romp_fix` from `romp_fix-s3`, `romp_fix-s4a`, `romp_fix-s42`, `romp_fix-s43` and
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
    (1,735 values small, 624,750 large; every call the replay's body makes, 7 and 41), on 415 Python-made edge cases
    (14,856 values; 11 more are refused at load, as built to be) and on 60,000 random sessions and calls (9,054,772
    values, `dump_layer5.py --fuzz`). Python's regex classes and `re.I` fold come from tables generated from its own
    engine, checked on every code point. 6 planted mistakes and 58 source mutants are each caught by the edge set; fuzz
    seed 1 catches all but four; 4 more mutants are equivalent (see "S4.5 as built").
  - S2 done before them (byte-identical oracle dumps across seeds and roots, "S2 as built"); S1 before that
    (romp-json matches `json.dumps` on 3,264 golden cases and 499,869 random floats).
- S2 mapping: `rust/notes/s2-design.md`, the raw maps in `rust/notes/s2-maps.json`, the probes in
  `rust/notes/probes/`. The design changed the plan; see "Revisions from the S2 map" below.
- Next action: S4.6, layer 6 (the body and its closures), scoped in "What layer 6 needs" below, which carries the
  body's call order for _node_anchor_uuids forward from S4.4. S4.5 waits for its review and merge.
  - Inputs: `python3 tools/rust-spike/dump_world.py --world small` (or `large`), then
    `python3 tools/rust-spike/dump_layer1.py DUMP`, `python3 tools/rust-spike/dump_layer2.py DUMP`,
    `python3 tools/rust-spike/dump_layer3.py DUMP`, `python3 tools/rust-spike/dump_layer4.py DUMP` and
    `python3 tools/rust-spike/dump_layer5.py DUMP`. Never commit a dump.
  - Checks: `romp-spike roundtrip DUMP`, `romp-spike layer1 DUMP`, `romp-spike layer2 DUMP`, `romp-spike layer3 DUMP`,
    `romp-spike layer4 DUMP`, `romp-spike layer5 DUMP`,
    `romp-spike layer2 rust/crates/romp-views/tests/fixtures/layer2_edge.json`,
    `romp-spike layer3 rust/crates/romp-views/tests/fixtures/layer3_edge.json`,
    `romp-spike layer4 rust/crates/romp-views/tests/fixtures/layer4_edge.json`,
    `romp-spike layer5 rust/crates/romp-views/tests/fixtures/layer5_edge.json`,
    `python3 tools/rust-spike/dump_layer2.py --fuzz 10000 --seed S`,
    `python3 tools/rust-spike/dump_layer3.py --fuzz 10000 --seed S`,
    `python3 tools/rust-spike/dump_layer4.py --fuzz 10000 --seed S` and
    `python3 tools/rust-spike/dump_layer5.py --fuzz 10000 --seed S` (seeds 1 to 6), and
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
| `rust/crates/romp-spike` | the standalone binary: load a snapshot and transcripts, build the feed, print canonical JSON and timings |
| `tools/rust-spike/` | the Python side: the world and oracle dumper (`dump_world.py`), each port layer's Python answers (`dump_layer1.py` to `dump_layer5.py`), the measurement |

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
- **S6 measurement.** CPU per build (warm and cold), cold load, and the store's resident size. Python's side uses
  tracemalloc over the record cache plus its RSS difference. Alternate the two several times on the same load.
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
  on growth) or an exact reserve (a copy on every append) would bound it; S6's numbers decide which.
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
  the body's sequence (see "Carried forward for layer 6" under "What layer 5 needs").
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
- SPIKE.md now carries the body's call order for _node_anchor_uuids forward to layer 6 ("What layer 5 needs"), and the
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
  of lowercased characters gets all of these wrong; it gets the Kelvin sign right, because K lowercases to `k`. One
  reading in the notes did not hold: `ß` and `ẞ` match each other both ways (sre takes `ß` as cased, its uppercase
  being the first letter of "SS"), and no pair is asymmetric.
- _opening_sentence: paragraphs split at `\n\s*\n` (a whitespace run holding two newlines, Unicode whitespace
  included); a leading list marker whose digits are any Unicode decimal (`١.` and `１.` are markers, `².` is not, and
  the marker needs whitespace after it); the first line; the cut after the first `.`, `!` or `?` past 12 code points
  that Unicode whitespace or the end follows; `[:200]` then `strip()`. `str(span)[:300]` and the offset `find`
  returns count code points too.
- _summary_text_anchor searches jd._atom_text, which reads a bare-string content as "", while _text_atoms admits that
  atom through em.atom_has_text; such an atom can only be the stub. The turn's other text atoms leave out the
  segment's by identity (`a is s`), so a second atom with a repeated uuid is still searched. The memo is left out (a
  cache: a hit returns what a miss computes), as layer 3 left out the held aggregate; the oracle checks it.
- _summary_outrun compares with `max(int(distilledMt or 0), int(briefedMt or 0))`, so 100.9 covers a segment at
  100.5 only as far as 100: that segment outruns it. A coverage past i64 compares exactly (an integral float).
- The segment predicates find the trigger atom by `uuid == seg["trigger"]`: with no trigger, the first atom without a
  uuid is the trigger, before `atoms[0]`. _seg_prompt instead searches only for a truthy trigger, so a trigger of ""
  falls to the first user atom.
- em.postal_pairs is a findall: a `romp-msg-kind` marker whose lazy value runs over a `romp-msg-id` marker consumes it,
  and no pair is made, so such an attachment is still a dictated record.
- _provisional_card reads `_captions(fsid)` before it looks at whether the segment is a system notice: an unrecorded
  caption read fails even where the caption is never shown (the port returns `NotRecorded`, as the oracle's reader
  raises). `pre + g` raises TypeError on a truthy caption that is not a string, with Python's message. The turn is open
  only if no atom anywhere in it is an idle span, unlike _session_working's trailing test. BARE_SLASH_RE's `\w` is
  Python's (a combining vowel sign is not one, so `/kaा` is not slash-shaped), and its `$` matches before a final
  newline.
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
(synthetic, 0.56 MB, Python's tables included). Its 415 cases: 170 sessions (every gate of the provisional card,
follow-ups over standing, cleared, absent and replanned targets, every placement phase and drift, retired and
cleared-out placements, captions of every shape, seam tails, every trigger lookup, landings by quote, by fold, by
substantive and stub atoms, identity against uuid, outrun stamps, every latch and anchor of the delegation test) and
245 direct calls (opening sentences, quote location, reminders, postal pairs, BARE_SLASH_RE, FOLLOWUP_RE, the comment
strip, prompt records, outrun, card_gone, placements, captions, the landing over hand-made segments).

Result: 404 cases load and match on every value (14,856), 0 differences. The other 11 are refused at load, exactly
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
when the plant is caught). The 62 source mutants were edits to `layer5.rs` and `pyre.rs`, each built, run on all four
inputs and reverted by a scratch script (not committed); 58 are below and 4 are the equivalent ones named above (M58,
N1 to N3), which no check can catch because no admitted input tells them apart. The table is the run on the final
bytes: the 415-case edge set and fuzz seed 1. Each cell is differences, then the sessions or cases they fell in.

| Mistake | Small | Large | Edge set (404) | Fuzz, seed 1 (10,000) |
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
| the 200 cap counts UTF-8 bytes (M10) | 0 | 0 | 1 in 1 | 1 in 1 |
| the located span is not cut at 300 (M11) | 0 | 0 | 2 in 1 | 0 |
| the turn's other atoms searched before the segment's (M12) | 0 | 0 | 2 in 1 | 146 in 35 |
| the quote search runs oldest first (M13) | 0 | 0 | 18 in 6 | 472 in 115 |
| the substantive fallback takes the first, not the newest (M14) | 0 | 0 | 2 in 1 | 1,466 in 338 |
| the stub is the first text atom, not the last (M15) | 0 | 0 | 4 in 4 | 806 in 326 |
| _text_atoms keeps the settle reply (M16) | 0 | 0 | 7 in 2 | 1,465 in 671 |
| _text_atoms keeps API errors (M17) | 0 | 0 | 2 in 1 | 1,571 in 656 |
| outrun compares with >= (an equal stamp outruns) (M18) | 0 | 0 | 4 in 2 | 634 in 571 |
| outrun ignores briefedMt (M19) | 0 | 0 | 4 in 4 | 1,211 in 1,071 |
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
| an absent caption reads null, not "" (M40) | 11 in 1 | 376 in 1 | 385 in 144 | 24,897 in 5,537 |
| captions read after the system check (M41) | 0 | 0 | 1 in 1 | 15 in 15 |
| the follow-up resurrect ignores whether the turn is open (M42) | 0 | 0 | 1 in 1 | 428 in 428 |
| an absent follow-up target counts as gone without clear evidence (M43) | 0 | 0 | 1 in 1 | 43 in 43 |
| the seam text is not cut at 100 (M44) | 0 | 0 | 1 in 1 | 1 in 1 |
| an empty seam text is kept (no "a completed goal") (M45) | 0 | 0 | 2 in 2 | 5 in 5 |
| a command that put the model to work gets no card (M46) | 0 | 0 | 2 in 2 | 28 in 28 |
| judging is the turn being open (inverted) (M47) | 1 in 1 | 1 in 1 | 67 in 67 | 1,043 in 1,043 |
| a non-string caption is formatted, not a TypeError (M48) | 0 | 0 | 6 in 6 | 6 in 6 |
| the card's t is the turn's, not the held segment's (M49) | 0 | 0 | 26 in 13 | 1,080 in 540 |
| the placed gate resurrects on an ended turn too (M50) | 0 | 0 | 1 in 1 | 32 in 32 |
| re.I without re._casefix's extra cases (M51) (the table test passes) | 0 | 0 | 21 in 7 | 797 in 247 |
| cased read from Rust's is_lowercase/is_uppercase (M52) (the table test fails) | 0 | 0 | 4 in 2 | 20 in 4 |
| the human branch skips the interrupt test for a seg (jd._seg_human) (M53) | 0 | 0 | 94 in 47 | 383 in 334 |
| a falsy command flag counts (command key present) (M54) | 0 | 0 | 3 in 1 | 1,472 in 1,152 |
| the located offset counts UTF-8 bytes (M55) | 0 | 0 | 2 in 2 | 13 in 13 |
| the sentence cut starts past 12 bytes, not 12 code points (M56) | 0 | 0 | 1 in 1 | 43 in 21 |
| list-marker digits are any numeric character (is_numeric) (M57) | 0 | 0 | 1 in 1 | 915 in 258 |
| em.postal_pairs' value group is greedy (N4) | 0 | 0 | 1 in 1 | 1 in 1 |

The worlds catch 3 of the 64: a zero coverage no longer stops _summary_outrun (every node with a trail outruns), an
absent caption read as null (s004's recorded captions are `{}`, so every segment row of it changes), and `judging`
inverted (s004's provisional card). The edge set catches every row. Fuzz seed 1 catches every row but four: the
paragraph split (M05: only a list marker whose `\s+` would run across a whitespace-only line into the next paragraph
shows it), the 300 cut (M11: no fuzz span reaches 300 code points), the postal findall's consumption (M24: the fuzz
makes no kind marker whose value runs into an id marker) and `$` before a final newline (M34: _seg_slash_shaped strips
its text, so only a direct call on an unstripped line shows it, and the fuzz drew none). M52, the case table read from
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

Tests: 17 new, 148 pass in the workspace: 7 unit tests in `layer5.rs`, 3 in `pyre.rs`, 1 in `tree.rs` (the `text`
refusal), `tests/layer5_edge.rs` (5: every case answers as Python does, each plant is caught on exactly the cases built
for it, every code point against Python's tables, the line trace's missed lines, the schema) and 1 in romp-spike for
the comparison's messages.

What this feeds: layer 6's `_brief_landing` calls _summary_outrun and _summary_text_anchor with the walk's maps, the
card loop calls _pure_delegation_top per top, and the session's tail calls _provisional_card; "What layer 6 needs"
below has the gates and the order.

## What layer 6 needs (the body and its closures)

Section 2 item 6: kernel._feed_session_entry (55894 to 57009 at this HEAD, 515 lines and 362 statements; the large
world's replay runs 424 of the lines and 309 of the statements, all 90 top-level ones) with its closures _note_peers,
_subtree, _closure_done, _fsubmax, _closure_blocked, _block_check_floor, _brief_landing and flatten. Layer 6 is the first
layer whose answer is the card itself, so besides per-closure answers its oracle can use the dump's own
`oracle/entries/<sid>.json` (the clock-free memo entry) and the replay's slice. Found while building layer 5 and scoping
the body from the current source:
- Carried forward for layer 6 (found in S4.4's review): the body calls _node_anchor_uuids inside `flatten` (kernel.py,
  the `_pa, _wa = _node_anchor_uuids(nd, seg_trig, seg_uuid)` line, about 56228), not over the store. It runs once per
  flattened node: a card top, then its children sorted by `_fsubmax` (the newest `mt`, else `t`, over the subtree)
  descending, stably, each recursively before the next. The tops flattened are `children[None]` of the body's children
  minus cleared tops (rollup status `cleared`, or in the build's cleared set) and pure-delegation tops (unless a live
  floor keys on them). Hidden nodes, parentId-cycle members and orphans that are not born (their missing parent key
  never hangs under None) are never flattened, so they never write or read the table. Layer 4's oracle and
  `session_json` call it per node in store order, which agrees with the body only while no two flattened nodes share
  an id. Since `AnchorTable` lives for the kernel run, the order decides which of two same-id nodes' anchors a later
  cold lookup serves, within one build and across builds; and a session whose entry is served from the feed memo
  (a `_feed_session_key` hit) runs no body, so it writes nothing that build. Layer 6 must make these calls from its
  own flatten, in this order.
- _brief_landing, the tiers layer 5 serves (about 56149): one resolve per build per `(nid, bool(completed), line or "")`
  (`_land_memo`). In order: a completed node pins the newest substantive tail over its subtree (`tt > tail[0]`, strict,
  so the first of equal times stays); else the cited `summaryAnchor` when it is in `cite_uuids` and _summary_outrun over
  the subtree's trails says not outrun, with `summaryQuote` (unported today); else
  `_summary_text_anchor(seg_turn.get(sk), line, memo_key=(fsid, nid, sk))`, where sk is the newest trail segment over
  the subtree with `k in seg_turn` by `t >= skt` from `skt = -1` (the last of equal times wins, and a segment whose
  `seg_best` time is below -1 never qualifies); else the latest-prose walk by `(bsub, bt) > (best[0], best[1])` (a tuple
  compare: substantive first, strict); else the stub call (`stub_ok=True`); else the newest trail segment's work anchor.
  It returns `(u, q or None)`. flatten asks it for each row with `_landing_inputs("completed" if st == "done" else
  "blocked" if st == "question" else None, "", nd)` and skips handoff rows and rows without a line; the card top asks
  with its own state and column (about 56805).
- `_fsubmax` is `cn.get("mt", cn.get("t", 0))` then `max(m, child)`, so the first of equal values (and its int/float
  tag) stays. Row fields read the Python way: `"mt": nd.get("mt", nd["t"])` (a present null stays null, and `nd["t"]` is
  evaluated first), `"who": _name_of(sid) or sid[:8]` (code points), `"auth": (nd.get("agentTask") or {}).get("status")`,
  `"reviewedEarlier": bool(boundary and out and done and _done_since(nd) <= boundary) or None`, `"born": _born or None`
  (an empty born dict becomes None).
- The body's layer-5 gates: _pure_delegation_top for each top of `children[None]` that is not cleared and not perm_top,
  api_top or jauth_top, one memo per derivation; _provisional_card only when `not had_working and perm_top is None and
  ps` and the store did not fault, else the blocked placeholder (`perm_state in _NEEDS_INPUT_STATES`) or the awaiting
  card (`sess_awaiting_why`). The derivation's `faults` is the change in `_SUMMARY_ANCHOR_STATS["fault"]` across it
  (always 0 on a tree the loader admits, layer 5), and an entry with faults is never memoized.
- Keys layer 6 reads that the loader still refuses (`NODE_UNPORTED`): background, blockCheckT, briefParts, failLog,
  followupPending, relayCarried, serving, stallSummary, summaryAnchors, summaryParts, summaryQuote, umbrella, warns;
  `tracked` and `peerHost` on handoffs and origins; `a`, `q`, `login` and `fsid` on log rows. Type each with its reader
  and check the list again with an ast scan of the body (S4.5 ran one over the node keys of the body and layers 1 to 5).
- What the worlds miss is listed in s2-design.md section 2's "Left out" (hide, the cold warm-ask, store_faulted,
  born and hidden tops, the API-error and judge-auth floors, the heal un-nest, the bg chip, the owned-task yield, the
  stall, the cold summary fallback, the serving fold, the blocked and awaiting placeholders, and the card literal's
  rarer arms). An edge set and a fuzz have to reach them, as for layers 2 to 5.

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
    pinned. One reading in the notes did not hold: `ß` and `ẞ` match each other both ways under `re.I`.
  - Python's `\d`, `\w` and case tables generated from its engine (`dump_layer5.py --tables`, `pyre_tables.rs`) and
    checked on every code point.
  - Loader: placements may be null or hold null values, nodes type askAnchor, userAsk, followupAt and briefedMt,
    captions are typed, a truthy non-string atom `text` is refused. Re-run: the round trip and layers 1 to 4 match on
    both worlds (6,370 and 2,499,514 layer-1 values, 336 and 135,016 layer-2, 1,801 and 695,672 layer-3, 864 and 6,406
    layer-4) and on the layer 2 to 4 edge sets, 0 differences. Rust tests: 148 pass.
