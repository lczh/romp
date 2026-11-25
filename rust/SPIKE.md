# The Rust spike: status and step plan

This is the first step of `plans/rust-migration.md`: its one-week benchmark spike, done at a slow pace while the
work is affordable. It ports the transcript store and `build_feed`'s per-card build to a standalone Rust program
with no server. Both implementations run on one synthetic state, and the feed JSON is compared byte for byte.

**Status (keep this current: a session that stops mid-step resumes from here):**

- Step: S3 and S4.1 done (merged into `romp_fix` from `romp_fix-s3` and `romp_fix-s4a`, each with its review's fixes).
  - S3: romp-transcript reads both dumps' transcripts with every summary line identical to Python's reader, records
    compared in full, and matches Python's cache read for read on 46 lockstep steps of the identity contract and on
    1,200 random ones (`tools/rust-spike/check_store.py --fuzz 6`). See "S3 as built" below.
  - S4.1: the typed snapshot and trees load and write back through romp-json byte for byte on both worlds, and the 21
    layer-1 functions match Python's own answers with zero differences: 6,370 values on the small world, 2,499,514 on
    the large, plus 1,206 on 67 synthetic edge atoms. See "S4.1 as built" below.
  - S2 done before them (byte-identical oracle dumps across seeds and roots, "S2 as built"); S1 before that
    (romp-json matches `json.dumps` on 3,264 golden cases and 499,869 random floats).
- S2 mapping: `rust/notes/s2-design.md`, the raw maps in `rust/notes/s2-maps.json`, the probes in
  `rust/notes/probes/`. The design changed the plan; see "Revisions from the S2 map" below.
- Next action: S4.2, layer 2 (segmentation), scoped in "What layer 2 needs" below.
  - Inputs: `python3 tools/rust-spike/dump_world.py --world small` (or `large`), then
    `python3 tools/rust-spike/dump_layer1.py DUMP`. Never commit a dump.
  - Checks: `romp-spike roundtrip DUMP`, `romp-spike layer1 DUMP` and
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
| `tools/rust-spike/` | the Python side: the world and oracle dumper (`dump_world.py`), each port layer's Python answers (`dump_layer1.py`), the measurement |

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

## What layer 2 needs (segmentation)

Section 2 item 2: em.segments with em._segment_id, em.split_segment (with em._seam_real_work), jd.apply_seams,
kernel._segs_seam and kernel._turn_sans_placed_echoes. The per-card code reaches them through `_segs_seam`, from
_provisional_card, _feed_seg_fold and _FeedSegTurns.get (the direct `em.segments` call in _blocked_placeholder is on
a branch neither world reaches).
- A derived `Segment` over borrowed atoms: `t`, `end`, `trigger`, `atoms`, `id`, and on a seam tail `seam: true`
  and `seamOf {top, text}`. em.split_segment filters atoms by time (`a.get("t", 0) <= t`), so a piece is a list of
  atom references, not a slice.
- Numbers: a segment's `t` is an atom's or the turn's, and the id prints it with `"%d"`, which truncates a float.
  apply_seams compares `p["t"] < s.get("t", 0) < p["end"]` across ints and floats, so `Num` needs Python's numeric
  ordering, and `min(hits, key=t)` keeps the first of equal times.
- Layer 1 supplies _segment_id's predicates (_has_text, _machine_written, is_interrupt_record, _text_hash8); the
  uuid basis is a second SHA-1 call. `rompuuid` is `atoms[0]["session_id"]` (`.get(..., "")` in split_segment), and
  `turn["trigger"]["uuid"] if turn["trigger"] else None` raises on a dict without `uuid`.
- _seam_real_work counts the stripped joined text's code points against SEAM_PROSE_FLOOR (80): a second boundary
  like CITE_MIN_CHARS, but over `_text_of` rather than `_prose_chars`. `seamOf.text` is `sm.get("text", "")`, so an
  absent text and a null one differ.
- Coverage: split_segment never runs in either world (no seam's `segs` holds a key whose span straddles its `t`),
  so the dump comparison cannot prove the split. Port it anyway with Python-made edge cases as layer 1 did, or
  refuse loudly when a seam hits. The restored pre-cut branch of em.segments stays refused at load.
- The oracle: `dump_layer2.py` calling `kernel._segs_seam(turn, store)` per turn with the session's store, writing
  each segment's t, end, trigger, id, atom uuids, seam and seamOf; `romp-spike layer2` compares, as for layer 1.

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
