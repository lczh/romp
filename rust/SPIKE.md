# The Rust spike: status and step plan

This is the first step of `plans/rust-migration.md`: its one-week benchmark spike, done at a slow pace while the
work is affordable. It ports the transcript store and `build_feed`'s per-card build to a standalone Rust program
with no server. Both implementations run on one synthetic state, and the feed JSON is compared byte for byte.

**Status (keep this current: a session that stops mid-step resumes from here):**

- Step: S4.3 done on branch `romp_fix-s43` (from `romp_fix` at the S4.2 merge), awaiting review and merge. S3, S4.1
  and S4.2 are merged into `romp_fix` from `romp_fix-s3`, `romp_fix-s4a` and `romp_fix-s42`, each with its review's
  fixes. S4.2's two follow-ups (the empty-trigger pin, `dump_layer2.py --fuzz`) were independently verified: 65,000 more
  random cases on fresh seeds with 0 differences, and 12 planted mutants all caught.
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
  - S2 done before them (byte-identical oracle dumps across seeds and roots, "S2 as built"); S1 before that
    (romp-json matches `json.dumps` on 3,264 golden cases and 499,869 random floats).
- S2 mapping: `rust/notes/s2-design.md`, the raw maps in `rust/notes/s2-maps.json`, the probes in
  `rust/notes/probes/`. The design changed the plan; see "Revisions from the S2 map" below.
- Next action: S4.4, layer 4 (the store helpers), scoped in "What layer 4 needs" below, once S4.3 is reviewed and
  merged.
  - Inputs: `python3 tools/rust-spike/dump_world.py --world small` (or `large`), then
    `python3 tools/rust-spike/dump_layer1.py DUMP`, `python3 tools/rust-spike/dump_layer2.py DUMP` and
    `python3 tools/rust-spike/dump_layer3.py DUMP`. Never commit a dump.
  - Checks: `romp-spike roundtrip DUMP`, `romp-spike layer1 DUMP`, `romp-spike layer2 DUMP`, `romp-spike layer3 DUMP`,
    `romp-spike layer2 rust/crates/romp-views/tests/fixtures/layer2_edge.json`,
    `romp-spike layer3 rust/crates/romp-views/tests/fixtures/layer3_edge.json`,
    `python3 tools/rust-spike/dump_layer2.py --fuzz 10000 --seed S` and
    `python3 tools/rust-spike/dump_layer3.py --fuzz 10000 --seed S` (seeds 1 to 6), and
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
| `tools/rust-spike/` | the Python side: the world and oracle dumper (`dump_world.py`), each port layer's Python answers (`dump_layer1.py`, `dump_layer2.py`, `dump_layer3.py`), the measurement |

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

## What layer 4 needs (the store helpers)

Section 2 item 4: _agent_open_set, _parked_rows, _focus_open_top with jd._top_of, _goal_awaiting_stamp_full,
_open_leaves, _all_outstanding_delegated; _handoff_card_fields, _handoff_peer_identities, _peer_identity (registry rung
only), _awaiting_item and _awaiting_peer_items; _newest_block_ev_t, jd._block_ev_t, jd._block_since, jd._done_since,
jd.review_boundary; _session_started_face, _node_log_rows, _node_anchor_uuids with jd.junk_quote, _landing_inputs,
_login_refusal_label, _name_color, _name_of. Found while scoping it from the current source and the large world's
`coverage/port.json`:
- Unlisted functions and constants they call: _peer_stamp_superseded (from _goal_awaiting_stamp_full, reading
  `recorded.peerAnswered`, a tuple or a legacy scalar), _title_shaped and _HANDOFF_LABEL_RE (from
  _handoff_card_fields), jd._JUNK_QUOTES (junk_quote), _AWAIT_ITEM_KINDS (an assert in _awaiting_item), _UUIDISH_RE,
  and _names_parts (a snapshot lookup behind _name_of and _name_color). _peer_identity's rungs past the registry
  (_host_for_sid, _remote_name_of, _postal_peer_names) read state outside the snapshot. Port the registry rung and the
  composite and stub fallbacks, and refuse or record the rest.
- Node keys to type: the loader refuses them today (`NODE_UNPORTED`), and these readers need them typed. They are
  agentTask (`.status`), awaitingWhy, awaitingAt, awaitingKind, awaitingPeers, rolledUp, born (`parentText`, `why`),
  deltaSince, distilledMt and summaryAnchor, plus `synth` on log rows (_parked_rows skips synthesized rows).
- State across calls: _node_anchor_uuids writes `_node_anchor_last[nid]` on a warm resolve and serves it when a later
  lookup misses. The table is keyed by node id alone and lives for the kernel run. So a cold lookup serves an earlier
  build's warm one. Within one build, a session's answer can depend on an earlier session's wherever two ids collide;
  its docstring says ids are bare (g1), while both worlds' ids carry the sid prefix. The port needs the table as state
  walked in the alive order, and the oracle must clear it (a fresh process, or `_node_anchor_last.clear()`), as
  dump_world's cold-oracle note says. It also takes layer 3's `seg_trig` and `seg_uuid`.
- Numbers and order: _block_ev_t and _done_since take `max` over `ev_t or at` (the first of equal values, and the tag
  rides through), with `mt or t or 0` fallbacks. _goal_awaiting_stamp_full takes `max` over tuples (at, why, kind or "",
  peers), so ties break on the strings. _awaiting_item writes `int(since)`. _parked_rows compares `(t or 0) > t0` and
  returns a dict in the order of `children.values()`. _handoff_peer_identities sorts by name, stably (equal names keep
  first-seen order). _open_leaves keeps document order. _agent_open_set is a set, read by membership only.
- Recursion: _agent_open_set and _open_leaves recurse in Python, past about 1,000 levels a RecursionError. The port
  must not overflow its stack on a deep tree (iterate, or refuse past a depth, and say which).
- Branches the worlds miss, each needing Python-made edge cases. From `coverage/port.json`, these never run in either
  world: _goal_awaiting_stamp_full's stamps (5 of 18 lines missed: no node carries awaitingWhy), _peer_identity past the
  registry (5 of 23 lines run), _handoff_card_fields' handoff-root card (4 of 17 run), _session_started_face's `born`,
  _login_refusal_label's auth error, _node_anchor_uuids' cold road (the held anchor, summaryAnchor), review_boundary's
  distilledMt advance, and _agent_open_set's open agentTask.

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
