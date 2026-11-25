# S2 map and design (workflow wf_0d505e86-fee, 2026-11-25)

Four read-only readers mapped the feed builder, the transcript layer, the synthetic-world tooling and the sources of nondeterminism; a fifth agent synthesized this design. Raw maps: s2-maps.json beside this file. Citations are kernel.py line numbers at main 2c11d8767.

S2 design and S4 scope for the Rust spike: synthesis of the four maps, checked against the code and a scratch probe

READ-ONLY: nothing under ~/romp-romp_fix or ~/romp was edited (git status still shows only the `M rust/SPIKE.md` that was already there). Nothing touched ~/.local/state/romp. The census test tests/test_feed_memo_inputs.py passes at HEAD (31 passed, rerun here). My scratch scripts are /tmp/s2probe/probe.py and /tmp/s2probe/probe2.py, and they are a working starting point for dump_world.py. I deleted the temp worlds they built.

## 0. Bottom line

The problem S2 has to solve is a Python oracle that writes the same bytes on every run. Without that, a Rust mismatch could just be Python drifting. It can be done, and I verified it.
- Freeze the clock. Pin file mtimes in an explicit order. Patch liveness. Warm the parses. Run one adoption build, then clear the memo. Build again.
- Two processes with different hash seeds, temp roots and wall clocks gave byte-identical canonical feeds, insertion-order feeds and per-session entries (probe.py, 18,448 bytes).

The finding that shapes S4: the per-card build does not read transcripts. It reads the cached parse tree (`ps = _parse_cached(...)`, kernel.py:55427). So S4 should take the Python parse tree as input, dumped per session. A small set of non-ported helpers is answered from recorded values. I proved this input set is complete: rebuilding Python's feed from JSON-round-tripped trees, stores and peer stores, plus recorded helper answers keyed by sid, gave the identical bytes (probe2.py, `full_replay_equal: true`).

## 1. The S2 dumper recipe (tools/rust-spike/dump_world.py)

CLI: `dump_world.py --world small|large --out DIR [--cold]`. DIR must be outside the repo (default /tmp/romp-spike/<world>). The world root comes from mkdtemp under /tmp and is realpath'd. It must sit outside any git tree, because ~/Desktop is a repo.

**Step 1. Environment, before any romp import.** STATE, PROJECTS and NAMES are bound at import (judge.py:109-133, kernel.py:110, event_model.py:33-39).
- Strip ROMP_*, ANTHROPIC_*, OP_* and credential names.
- Call pb.prepare_env(state, claude, private) (tools/perf-bench.py:367-397).
- Add HOME=<root>/home, TZ=UTC, GIT_CEILING_DIRECTORIES=<root>, and ROMP_HOST_NAME=spike-host. The host name feeds selfHost (kernel.py:28685-28700).

**Step 2. World files with the clock frozen.**
- Call bcp.write_world(root, N, records, big) (tests/bench_connect_push.py:90-123) inside `mock.patch.object(bcp.time, "time", return_value=float(NOW))`, with NOW=1781100000. It stamps from time.time() at line 99.
- Overwrite s001's transcript with bench_chatbuild's Gen, whose wrap-ups are about 95 characters, above jd.CITE_MIN_CHARS=80 (judge.py:2952).
  - Extract only `class Gen` and `iso` with ast and exec them with SID and T0 in the namespace. Never import tests/bench_chatbuild.py: its body loads a kernel and resets XDG_STATE_HOME (:23-34).
  - Override uid() with a per-session prefix.
- Append an open turn (a typed prompt plus a tool_use with no result) to s003 and s004. "Working" is decided by the transcript's open turn, not by the live row (_session_working, kernel.py:2064).
- Plant non-ASCII text, including one boundary atom: 79 code points but 80 or more UTF-8 bytes, placed in a trail segment of a completed goal. The substantive test uses len() in code points (event_model.py:10843). A byte-count bug in Rust would then flip a visible field. Check this with a one-off mutation run.

**Step 3. Kernel.**
- Load with `km, sbmod = pb.load_kernel(REPO)` (perf-bench.py:760-801). It loads sdk_backend itself, so the synthetic-world map's KeyError workaround is not needed.
- Assert that jd.STATE and jd.PROJECTS are under the root.
- Set `km._sdk_backend = pb.make_backend(sbmod, state, False, False)` (707) and `km._codex_backend = False`.
- Call `pb.install_guards(km, sbmod, pb.StateShadow(state, state, rec["refused_writes"]), rec, no_git=True)` (515). With the shadow root equal to the world's own state, writes land in place, while any write outside the world is refused. The guards also stub _warm_fleet_bg (536), point checkpoints at the shadow (588-596), answer git as failure and count thread starts.
- Set `km._GLOBAL_CLAUDE_MD = <root>/missing`.

**Step 4. Live state.**
- Use fixed rows shaped like tests/test_feed_session_memo.py:229-232, passed through json.loads(json.dumps(...)).
- Patch `km._live_map = lambda: live`. Four helpers bypass build_feed's argument: kernel.py:24999, 25039, 37868, 37964.
- Set `_live_scope.snapshot/paths/sessions/names` the way perf-bench's scope() does (1084-1090), on the building thread.

**Step 5. Mint goal stores under a second frozen clock**, T_MINT = NOW-1800. record_verdict stamps `"at": int(time.time())` (judge.py:11993), and that value ships in tree rows' `log`.
- Take segment ids from `em.segments(turn)` (event_model.py:5906) over `km._parse(path, sid, NOW)`. Ids have the form `<sid>:<t>:<sha1-8>` (5832-5883). The fixture trail "s1" leaves every anchor null.
- Write the plan with jd.apply_plan(store, seg_id, seg_t, ops, menu, place_key, prompt_uuid, quote) (judge.py:8657), using mint, sub, done and block ops.
- Set the non-protected node fields (handoff, origin, summary, blockSummary) directly. Only nodeComplete, blocked, cleared, blockWhy, doneWhy, log and logTrunc are guarded (judge.py:5077).
- Then call jd.rollup_status(store, session_closed, now=T_MINT) (8916) and jd.save_goals (7073).
- Board files: auto-nudge.json and nudge-events.jsonl. Plant jd._active (judge.py:1666) under jd._active_lock.

**The designed small world.** I verified these outcomes in probe2.py.

| Session | Store and live row | Cards produced |
|---|---|---|
| s001, chat transcript, idle | completed g1 (summary, sub done, settled); g3 blocked on the last segment (blockSummary); g4 dictated top whose open leaves are handoffs to s003 and s002 | completed; needs_input; working + awaiting kind "peer" with 2 handoff rows, whoWorking [true, false] stamped by the post-pass (kernel.py:57387-57398) |
| s002, quiet, row "permission", jactive | g1 with origin {peer: s001, goalId: s001's handoff node}; g2 | working, origin badge, judging; needs_input with blocked.state permission |
| s003, open turn | g1 with a done sub; auto-nudge {count 2, failed, failedAt NOW-600} | working, sessState open, working {since, toolUses 1}, nudged, nudgeFailed |
| s004, open turn, no store | none | provisional placeholder |

Two pitfalls I hit while designing it:
- A top whose every open leaf is a handoff is silently dropped as coordination by _pure_delegation_top (kernel.py:16237) unless its promptUuid names a dictated human record.
- A segment used only under a "#b" place key stays unplaced and spawns an extra provisional card.

**Step 6. Pin mtimes deliberately.** The alive order is newest transcript mtime first (kernel.py:6427), and it sets the asks order. On ties it falls back to the sorted names order (judge.py:10427).
- My probe assigned distinct mtimes in os.walk order, and the asks order flipped between runs because APFS enumerates the root-named project dirs differently. Runs whose order happened to match were byte-identical (sha 08745b52c5d6 twice).
- So assign transcripts explicit distinct mtimes in a designed order, and give every other file a value from its sorted relative path. Keep all values at or below NOW and inside the 48 h window (judge.py:10402).

**Step 7. Warm under the frozen build clock.**
- Patch time.time to return float(NOW) and set km._STARTED=float(NOW) (kernel.py:134).
- Clear jd._discover_cache and refresh the names snapshot.
- Warm each session with `km._parse(s["path"], s["sid"], NOW)` (44489). The trailing idle span ends at the first parse's `now` (event_model.py:5452).

**Step 8. Adoption build, then reset.**
- Call `km.build_feed(NOW, live)` once. The first build adopts session-order.json (test_feed_session_memo.py:216-221).
- Then call `km._feed_memo_forget(set())` (kernel.py:54816), which also drops _FEED_SEG_HELD via 55872, plus `km._FEED_FOLD.clear()` and `km._SUMMARY_ANCHOR_MEMO.clear()` (61411).

**Step 9. Recording build.**
- Wrap 11 boundary helpers and record each answer by sid; _session_awaiting is also keyed by its idle argument. The helpers: _closer_pending 16503, _interrupting 45249, _session_retrying 38531, _session_awaiting 37805, _bg_live_norm 37952, _api_error 40762, _interrupted_by 2530, _peer_answered 54441, _heal_session_tops 38841, _awaiting_task_descs 38048, _warm_wanted 13888 (cold road only), plus _cap_switch_offer 24984.
- Call build_feed(NOW, live) with an int NOW. A float prints `1781100000.0`, because the fold types its stamp (57067).
- Never take the oracle from _build_feed_locked (74701, which swaps the off-frame and fires bells) or from GET /feed.json (90431, float clock).
- Capture each per-session entry from `km._feed_memo[sid][1]` (57298-57300). It is clock-free and taken before the fold. Assert that every alive sid has one: an entry with faults is never memoized (57299).

**Step 10. Write the files.**
- `snapshot.json` (schema below).
- `trees/<sid>.json`: em.plain_tree (event_model.py:8547) of the cached parse, minus `dir` and `_txKey`, written with sort_keys.
- `transcripts/` and `states/`: byte copies, plus a manifest of size, mtime_ns and sha256.
- `oracle/feed.canon.json`: json.dumps(feed, sort_keys=True, allow_nan=False, default=a function that raises). This is the gate.
- `oracle/feed.wire.json`: insertion order, a stricter secondary check.
- `oracle/entries/<sid>.json`.
- `coverage/port.json` and `coverage/full.json`.
- `manifest.json`: hashes, repo HEAD, python version, NOW, seeds and the self-check results.

Optional: `--cold` produces `oracle/feed.cold.canon.json` from a separate fresh process. A fresh process is needed because _node_anchor_last (kernel.py:61742) serves remembered warm anchors on cold builds.

**The proposed schema** (`romp-spike-snapshot/1`):
```
{"schema":"romp-spike-snapshot/1",
 "world":{"name":"small","now":1781100000,"mintClock":1781098200,"colormap":"aurora","hostName":"spike-host",
          "repoHead":"<12hex>","python":"3.12.x","sizes":{"sessions":4,"records":80,"chatTurns":10,"fillers":0,"bigRecords":0}},
 "alive":[{"sid":"…","name":"s003","anchor":null,"tree":"trees/<sid>.json","transcript":"transcripts/<sid>.jsonl"}],
 "liveMap":{"<sid>":{"state":"working","since":1781099900,"model":"","effort":"","context":null,"compactPct":null,"color":null}},
 "board":{"cleared":[], "jactive":["<sid>"], "jauth":{}, "wmap":{}, "stalls":{},
          "nudgeRecords":{"<gid>":{"count":2,"failed":true,"failedAt":1781099400}},
          "nudgeTimes":{"<gid>":[1781098500,1781099100]}, "debugRows":null, "notifyCards":{}, "downtime":[]},
 "sessions":{"<sid>":{"names":["s001","$ROOT/work/notes-api","#4a9eff"],
          "flags":{"hideFromFeed":null,"notify":null}, "leafOk":true,
          "store":{…rendered store, node insertion order kept, transient _base* keys stripped…}|null,
          "captions":[],
          "recorded":{"closerPending":true,"interrupting":false,"sessionRetrying":null,"sessionAwaiting":{"idle=true":null},
                      "bgLive":[],"apiError":null,"interruptedBy":false,"peerAnswered":0,"healSessionTops":{},
                      "awaitingTaskDescs":null,"capSwitchOffer":null,"warmWanted":null}}},
 "peers":{"<psid>":{"names":[…],"store":{…}|null}},
 "frame":{every build_feed key except asks, working, awaiting, bgServices, servingFoldedIds, absentSids, now}}
```
Where each field comes from:
- board fields: kernel.py:57223-57250 and _feed_board_facts at 55101-55128. jauth is taken after the loginLabel resolution (57238-57243).
- store: _feed_goals_keyed (42497). Transient keys are listed at judge.py:6360.
- peers: every sid in any entry's `peers` (57007) plus origin senders. Peer stores are read through jd.load_goals_shared_or_fault, as at 56550.

Two conventions:
- A `recorded` key that is absent means "Python never called this helper". If Rust reaches that path anyway, it must fail loudly instead of guessing.
- Absolute world paths become `$ROOT`, so snapshots from different roots compare byte for byte. cwd is not read by the per-card build.

**Recording which code paths ran** uses stdlib sys.settrace only, so nothing needs installing. A per-code-object line tracer covers kernel.py, judge.py, event_model.py and colormap.py.
- `full.json` comes from the live recording build.
- `port.json` comes from the replay build. In the replay, the recorded helpers are answered from the snapshot and the trees and stores come from JSON, so port.json lists exactly what Rust must implement.
- Each file also lists the body's top-level statements as executed or not. In the probe world, 193 of 232 ran.
- sys.monitoring BRANCH events (3.12) can be added later for branches inside the card literal.
- Keep traced builds separate from any timing run.

**Self-checks the dumper must pass** (they go in manifest.json):
- (a) Replay equality, with `recorded` and trees/stores taken from JSON. Verified true in the probe. The strongest form runs in a fresh process with an empty state root and also redirects the board readers to the snapshot, then compares the per-card slice.
- (b) Guards held: 0 spawns, 0 refused writes, 0 thread starts, 0 warm calls, no absentSids, empty sdk/sync/clear notices, and `_wire_stats["default_str"]` unchanged (kernel.py:65904). Production's str() fallback hides unencodable values (65915).
- (c) Shape census: assert each designed card shape is present. A kernel change that drops a shape then fails the dump instead of quietly shrinking S4's scope.
- (d) A second full dump in a fresh process with another PYTHONHASHSEED, root and wall clock gives identical oracle and snapshot bytes. Prefer two seeds over pinning PYTHONHASHSEED=0, which would hide a set-order dependence.
- (e) The comparator catches a planted mutation of one card.

**Sizes.**
- Small is the table above: 80-record transcripts, s001 with 10 chat turns.
- Large keeps the same 4 designed sessions at 3,000 records, with s001 at 24,000, plus 34 filler sessions of 3,000 records, each with one goal and one done sub on real trails. That is the bench board (tests/bench_connect_push.py:13-15).
- The large output is roughly 170 MB, estimated from the probe's ~48 KB tree per 80 records (I did not run the 38-session board itself). Regenerate it; do not commit it.

## 2. S4 port scope, in dependency order

I measured these from the replay-build trace on the probe world. Executed Python lines are distinct lines actually run. The replay build ran 1,266 lines in 141 functions. About 230 of those are Python plumbing that Rust replaces with snapshot lookups or drops as caches: parse-store lookups, record-cache reads, load_goals_shared, the flag and caption file readers, _sdk_locked, the _FEED_SEG_HELD bookkeeping (_turn_hold_key, _seams_fp, ...) and the summary-anchor memo.

0. **Typed loader.**
   - Covers the snapshot, the trees (Session, Turn{t, end, ended, id, trigger, atoms}, Atom{type, uuid, t, author, message{content blocks, stop_reason, model}, isApiError, command, _echo_text}) and the stores.
   - Keep node order from the file (children are built in store order, kernel.py:56044-56053). Numbers need an int/float tag.
1. **Text and atom primitives**, about 60 lines.
   - _content and _text_of (event_model.py:3677-3692), _has_text 5886, _machine_written 5893 (two regexes, :89 and :120), _text_hash8 5675 (SHA-1), _stop_reason, is_interrupt_record 5690, _is_opener 5706.
   - atom_has_text, atom_prose_chars, _prose_chars, atom_is_bare_end and atom_is_settle (10825-10893).
   - jd._seg_key (judge.py:12654), _atom_user_text (kernel.py:49147), jd._atom_text 2898.
2. **Segmentation**, about 50 lines: em.segments 5906 with _segment_id 5832, em.split_segment 5965, jd.apply_seams (judge.py:12781), _segs_seam (kernel.py:61357), _turn_sans_placed_echoes 49588. Seams are reached: settled tops stamp them.
3. **Tree facts and the anchor walk**, about 125 lines.
   - _session_working 2064 with _suspended_after 2029 (key side, but the body needs it), _open_turn_progress 2037, _last_plain_user_turn_t 2553.
   - _seg_anchors 61324, jd._prompt_anchor_uuid 10904, _seg_last_text 61372.
   - _feed_seg_fold 55673, _feed_seg_walk 55750 and _feed_seg_plain_t 55811 without the held-aggregate cache, and _FeedSegTurns.get 55722.
4. **Store helpers**, about 155 lines in 31 functions.
   - _agent_open_set 49829, _parked_rows 37352, _focus_open_top 13720 with jd._top_of, _goal_awaiting_stamp_full 13556, _open_leaves 16147, _all_outstanding_delegated 16181.
   - _handoff_card_fields 37415, _handoff_peer_identities 37314, _peer_identity 37271 (registry rung only), _awaiting_item and _awaiting_peer_items 37497/37567.
   - _newest_block_ev_t 38314, jd._block_ev_t, _block_since and _done_since (judge.py:12027-12063), jd.review_boundary 18908.
   - _session_started_face 38822, _node_log_rows 61727, _node_anchor_uuids 61749 with jd.junk_quote 11252, _landing_inputs 61464, _login_refusal_label 58184, _name_color 3332, _name_of 4827.
5. **Landing, delegation and provisional**, about 165 lines.
   - _summary_text_anchor 61501 with _text_atoms, _opening_sentence (3 regexes) and jd._locate_quote 7951 (re.I).
   - _dictated_prompt_uuid 16201 with jd._human_prompt_record 20529, and _pure_delegation_top 16237.
   - _provisional_card 53926 with _seg_placed, _seg_caption, _seg_prompt, _split_reminders 41233 and jd._seg_human, _seg_command, _seg_slash_shaped (BARE_SLASH_RE, judge.py:19942), _seg_followup (FOLLOWUP_RE, 2911) and _seg_system.
6. **The body and its closures**, about 425 lines: _feed_session_entry 55894-57009 with _subtree, _closure_done, _fsubmax, _closure_blocked, _block_check_floor, _brief_landing and flatten.
7. **Loop, fold and cross-session pass**, about 80 lines, outside the traced function: the build_feed loop minus memo and key (57255-57320), _feed_fold_stamp and _feed_fold_card (57061-57088), cm.age_rgb and ramp (colormap.py:50-64), _notify_card_effective 10180, _push_explicit_bell 77203, the handoff whoWorking stamp 57387-57398, and frame assembly from the passthrough keys.

The total is about 1,100 executed Python lines across about 130 functions, plus SHA-1 and about 7 hand-written regex matchers. My estimate is 2,500 to 4,000 lines of Rust including types and tests. That is more than the plan's "one week" label suggests.

**Left out.**
- The memo and key machinery: _feed_session_key 55227-55545 and the memo at 54686-54870.
- The 12 recorded helpers and everything under them: _awaiting_live_rows and _awaiting_nest, _bg_placed_tops, _session_stamp_read, em.scan_bg_tasks_cached, _interrupt_marks, and the API-error tail scan.
- Body branches the world never reaches:
  - hide (55933), the cold warm-ask (55954), store_faulted (55999), born/hidden tops (56048-56052);
  - the API-error floor, capOffer and lg.mark_refused (56351-56369), the judge-auth floor (56384), the heal un-nest (56389-56402);
  - the bg service chip (56415), the owned-task yield (56470), the in-flight stall (56719), the cold summary fallback (56813);
  - the serving fold (56965) and the blocked/awaiting placeholders (56989-57002);
  - inside the card literal: the apiError and judgeAuth arms, delegTracked and satellite, summaryAnchorsPara, stalled, the interrupting/interrupted true arms, retrying, warnRows and a non-null sessionStarted.
- The serving-fold join (57345-57378) and every frame-level key, which are passthrough.

## 3. Risks to a byte-exact comparison, and how each is handled

- **Clock.** Use one int NOW for the build and every warm, and a separate T_MINT for verdict `at`. Patch time.time during minting and building, and set _STARTED. trgb and a placeholder's t come from NOW (57080-57081); the provisional card dates itself from its segment, not NOW.
- **Order.**
  - Alive order: explicit mtimes (step 6).
  - Node order: keep store order. serde_json `preserve_order` is already enabled in rust/Cargo.toml and indexmap is locked, so S5 is covered.
  - Tree children use `sorted(..., key=_fsubmax, reverse=True)` (56218). Python's reverse sort is stable, so equal keys keep insertion order; Rust's stable sort_by with reversed comparison gives the same result.
- **Tint math.** The tint is ln, then trunc, then round-half-even (colormap.py:50-64). Use f64::ln (the same system libm on this Mac) and round_ties_even, in the same order of operations. The output must be ints, not floats.
- **Int vs float.** Times pass through `max([settledAt or 0] + dts)` (56620) and `nd.get("mt", nd["t"])`, so a 5 and a 5.0 print differently. Carry a number tag, and mirror Python truthiness: `or` over 0, "", [] and None. Also mirror `.get(k, d)` semantics: a key present with null is not the default.
- **Unicode.** Python counts len() and slices ([:140], [:8]) in code points, and its strip/isspace treat \x1c-\x1f as whitespace while Rust's trim does not. `\s`, `\w` and `re.I` are Unicode-aware. SHA-1 runs over encode("utf-8", "replace") (event_model.py:5680). Handle these with the planted non-ASCII text and the 79/80 boundary atom. Keep lone surrogates, NaN and >64-bit ints out of the world, since serde_json differs on them.
- **Memo JSON round trip.** Tuples become lists and keys become strings (57298 and the fold decode at 57141). Rust emits the decoded shapes.
- **Process history.** _node_anchor_last, _SUMMARY_ANCHOR_MEMO, _FEED_SEG_HELD, _feed_memo/_FEED_FOLD and checkpoints all carry state between builds. Use a fresh process per dump, clear them before the oracle, and build the cold oracle in its own process.
- **Hidden faults.** build_feed swallows per-session exceptions into absentSids (57303-57312), and make_backend raises BenchError for any missing attribute (perf-bench.py:709-711), which would silently drop a card. Assert no absentSids and clean stderr.
- **Hash seed.** Sets are only tested for membership or sorted first (57007). Verified identical across seeds 1, 3, 5, 7 and 9; keep the two-seed check.
- **Frame passthrough.** It hides frame-level divergence. That is declared: the real coverage is asks, working, awaiting and bgServices.
- **Insertion order.** The gate is canonical (sort_keys); the wire-order bytes are diagnostic. The fold appends trgb, notify and notifyRaw last (57081-57086), and the card literal has conditional spreads (56818-56827).

## 4. What makes the spike as planned misleading

1. **"The transcript store feeds the per-card build" is not true of this kernel.**
   - The build reads the cache-only parse tree (55427). Raw records reach it only through the bg-task fold and the API-error tail scan, and both are empty in this world.
   - So S3 (the store) and S4 (the build) are independent measurements, and S4 consumes trees that Python dumped.
   - The records-to-tree parse (event_model, the plan's week-1 E2/E3 lanes, plans/rust-migration.md:492) is not measured at all. It is Python's real per-append warm cost.
   - The spike can say whether tree-to-cards is cheaper in Rust, not whether the feed path end to end is.
2. **The memo decides the steady state.** Medians of 7 runs, process_time:

   | World | Build from an empty memo | Derivation only | Build served from the memo |
   |---|---|---|---|
   | Small | 4.3 ms | 1.9 ms | 3.2 ms |
   | Large (s001 24,000 records, others 3,000) | 90 ms | 87 ms | 7.7 ms, zero derivations |

   - A Rust derivation that is 5x faster would take about 17 ms on the large world, which is worse than Python's 7.7 ms memo hit, unless the memo is ported too or the miss rate is high.
   - So report both rows, and fold in production miss rates from builds.feed.memo.
   - On the small world the derivation is under half the build. The timed region must be the same on both sides: derivation plus fold. Python's key computation and frame work must not count against Python while Rust skips them.
   - For the same reason, time Python in replay mode, with the recorded helpers answered from the snapshot, and separately report what share of the live derivation those helpers take.
3. **The comparison covers only the planted shapes.** Unplanted branches are never ported. That is fine for a CPU gate, but it is not a parity claim.
4. **An ASCII-only world proves nothing about Unicode semantics**, hence the planted boundary case.
5. **time.thread_time() under-reports CPU by about 42x on this Mac.** On a 0.193 s loop, thread_time read 0.0046 s. tests/bench_connect_push.py:133-150 uses it. S6 must use process_time in a single-threaded process. Rust needs a process CPU clock through a raw extern getrusage or clock_gettime (no crate), checked against wall time the same way.
6. **S6 memory:** mtimes pinned in the past make files "quiescent", so Python's record cache drops entries (event_model.py:984, 2520). Set ROMP_RECORD_CACHE_DROP_QUIESCENT_S, or use mtimes near NOW, for the memory run.

## 5. Where the four maps disagreed

- **indexmap.** The feed-builder map says preserve_order needs indexmap and that it is unapproved. Wrong: rust/Cargo.toml enables it and Cargo.lock pins indexmap.
- **Load route.** The synthetic-world map used load_source with no guards and saw 3 git spawns. perf-bench's load_kernel plus install_guards(no_git=True) gives 0 spawns (verified) and needs no sdk_backend workaround.
- **Direct _live_map callers.** The maps each listed a different subset. All four call sites are real (24999, 25039, 37868, 37964).
- **Pinning PYTHONHASHSEED=0.** The transcript map suggested it. It is unnecessary; run two seeds instead.
- **mtime recipe.**
  - Setting every file to NOW-60 (the synthetic-world map) is deterministic, because ties fall to sorted names.
  - The determinism map's "NOW-3600+i" is deterministic only if i follows an explicit order. Following os.walk order, it flipped the asks order between my runs.
- **bench_chatbuild's discovery comment.** Its claim that a fixed epoch falls outside discovery (:33) is false once mtimes are pinned relative to NOW.
- **Anchors.** Every map agreed that trail "s1" leaves anchors null. With real segment ids, every goal card got non-null anchorUuid, promptAnchorUuid and summaryAnchorUuid.

