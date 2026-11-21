# Moving romp's Python core to Rust: a one-month port behind a comparison harness, with a staged fallback

**Status:** proposal, 2026-11-19, not approved to build. The repository owner will scope it later, when there is capacity for an effort of this size. Written against main at `bc551f41c`; line numbers there only date a finding, so locate code by the function or module named.

## The decision this doc asks for

The problem first. As sessions and their histories grow, romp's kernel spends more and more of its time rebuilding what the browser shows, and its memory grows with every transcript's history. Part of that cost is Python itself and part is work repeated with no new input.

The proposal is to replace romp's Python core (the kernel, the event model, the Claude and Codex drivers and the judges) with one Rust binary, and optionally postal, which runs as its own process and may stay Python at cutover. Everything outside the core stays: the browser UI and VS Code extension, the bash CLI, the Node manager, the session hosts, and every file format under the state directory.

The plan is aggressive. The port itself takes about one month, run as 10 to 15 lanes at once, peaking at 15 in week 3. A lane is one agent session on its own git worktree, owning one Rust crate behind an interface fixed on day one, with its own review subagents.

The whole calendar is one benchmark week, then the four-week port with cutover around day 24, then a week of watched use: about six weeks in all.

The repository owner believes a focused effort can do the port in a month. An earlier estimate put full parity at 6 to 10 months, because it assumed about three lanes and a long chain of steps that must happen one after another. This doc lays out the month and says what has to be true for it to hold.

It needs a one-week benchmark spike first, a Python feature freeze, pinned Claude CLI and SDK versions, same-day owner decisions on divergences, and the spend for many parallel sessions.

The safety net has three parts. A harness runs both kernels on one synthetic world and diffs everything they show the outside. A shadow period follows, in which the Rust kernel runs read-only beside the live Python kernel on the same state, its output compared but never used. And rollback is one state-file write and a restart.

If the spike or the first week's gate misses, the fallback is a staged path: the hot data path (the transcript store and event model, then the view builders) moves to Rust behind the Python kernel, and the drivers stay in Python.

## What the kernel is

romp lets one person run many Claude Code and Codex agent sessions at once. The kernel is the local server at its center. It drives each session, reads every transcript as it grows, and turns transcripts into what the browser shows: the feed of cards, each session's chat, and the timeline.

It also runs the judges (short model calls that decide whether a session is working, finished or waiting on the person) and housekeeping such as nudging stalled goals and stopping runaway spend. It serves all this over loopback HTTP and WebSocket and keeps state as JSON files under `$STATE`.

Today it is one Python process: about 96k lines in `kernel/kernel.py`, about 70k in sibling modules under `kernel/`, plus about 10k in `postal/` and `cli/`.

## Why Rust, and what it will and will not fix

Under load the kernel's pusher thread, the loop that builds and sends the feed, chat and timeline frames, can stay busy for long stretches; `/perf` shows how much, as `pusher.cycle_cpu_ms_sum` against `uptime_s`. Memory grows with the history of every live transcript, because whole transcripts are held decoded in `_JSONL_CACHE`.

A faster language fixes this only partly. Much of the cost is work repeated without new input, which a faster language merely repeats faster.

In one sentence: Rust removes the costs that come from Python itself, while the costs that come from the algorithms go away only if the port changes the algorithms, which this plan does.

### Costs that come from the language

**Decoded records held as Python objects.** The record cache (`_JSONL_CACHE` in `kernel/event_model.py`) keeps records as dictionaries, so a few hundred bytes on disk become a tree of objects with headers, reference counts and hash tables.

The kernel estimates this already. `_record_cache_decoded_multiple` turns file bytes into an estimated resident size, reported by class as `/perf heap.recordCacheResident`, and `_heap_stats` reports the structures on top (`heap.hydrated`, `heap.builtAtoms`, `heap.restoredIndexes`). That estimate is file bytes times a configured constant, not a measurement.

In Rust a record can be a typed struct with interned keys (each distinct field name stored once and shared) and slices that point into the file's own bytes, so it can sit near the file's size or below it.

**Garbage-collection pauses.** Python's full collection is a tracing collector: it periodically walks every live container, mostly those dictionary trees, and stops every thread while it does, the pusher included. `kernel/gc_freeze.py` (`GcFreeze`) and `plans/gc-full-collection-pause.md` fight this by freezing the loaded heap. Rust has no tracing collector; memory is freed when its owner drops it, and a large drop can move off the hot thread.

**The global interpreter lock (the GIL).** `kernel/kernel.py` starts threads at 66 sites and `kernel/sdk_backend.py` at 15, but only one runs Python at a time. That is why the judges moved into their own process, `romp-judge --serve` (`_judge_child_argv`; `plans/judges-process.md`).

The judge child parses the same transcripts through its own record cache (`judge.child.recordCache` in `/perf`), so they are decoded twice, in two processes. In Rust, threads run in parallel, and judges can be tasks sharing one store.

**Interpreter overhead.** The builders assemble output one field at a time, each step a bytecode dispatch, a hash lookup and a reference-count update, and `kernel.py` serializes with the standard `json` module at 637 `json.dumps` sites. How much cheaper compiled code is, for this code, is what the spike measures.

### Costs that come from the algorithms

A straight port would carry these four over unchanged.

- **Clock-driven rebuilds.** The view signature that `build_feed` and `build_timeline` share includes `now // 5` (kernel.py line 74486 at `bc551f41c`), which its comment calls the one remaining clock input, kept until the age tints, host ages and the timeline's live edge tick on the client. Those views rebuild on a five-second clock with nothing changed; `REBUILD_MIN_S` limits how often but keeps the clock.
- **Whole-feed rebuilds on any session change.** The signature also changes whenever any transcript, states log or postal file moves (`_producer_sig`), and `build_feed` walks every card. The card memo (`_feed_memo_report`, `/perf builds.feed.memo`) skips unchanged cards, but a card's key carries facts about other sessions (peers through `_feed_peer_facts`, a board-wide postal component, the `row`, `bg` and `live` components), so one session's message invalidates other cards. `builds.feed.memo.miss_by` counts misses by reason.
- **Rescans on every pass.** The jobs thread runs `_auto_nudge_tick` over every session each pass (`memos.nudgeWalk`, including the looks its memo can never skip, `unbounded`); `_spend_guard_tick` stats subagent trees (`memos.spendTree.dirStats`, `fileStats`); `_lift_spent_awaiting` and `_interrupt_block_tick` sweep all sessions. `plans/nudge-walk-events.md` and `plans/spend-guard-events.md` moved part of this onto events, not all.
- **Whole transcripts in memory.** romp keeps each transcript whole across compactions, where the Claude CLI keeps only the part after its last compaction boundary, and several scanners re-read from byte zero under their own caches (`_bg_scan_all_cached`, `_session_meta`, the judges' whole-file readers).

A Rust kernel copying these would still slow down as sessions and history grow. So the architecture section redesigns each, keyed on an exact event, as the repository's design rule asks.

### The measurement plan

**What to compare.** Python's `/perf` comes from `_PerfStats.snapshot`. The Rust kernel serves the same field names wherever a field means the same thing, and null (not zero) where it has no counterpart, such as the `gc` block, so `romp perf` and the comparison scripts read both unchanged.

- **CPU:** `process.cpu_s` against `uptime_s`; `pusher.cycle_cpu_ms_sum`, `jobs.pass_cpu_ms_sum`, `writer.cycle_cpu_ms_sum`; on the Python side `judge.cpu_ms_sum`, `judge.cpu_ms_child_workers` and the judge child's own CPU, since in Rust the judges are inside the kernel.
- **Latency:** `pusher.cycle_ms_p50`, `cycle_ms_p90`, `cycle_ms_ring_max`; `pusher.connectPush` (`ms_max`, `byApp`); `jobs.pass_ms_p90`.
- **Where time goes:** `stages_ms` (`push.chat`, `push.feed`, `push.timeline`, `push.send`, `jobs.autoNudge`, `jobs.spendGuard`, `jobs.liftSpentAwaiting`, `jobs.interruptBlock`) and `jobs.cpuByJob`.
- **Count against cost:** `builds.feed`, `builds.timeline`, `builds.chat` (`built`, `cached`, `ms`) and `builds.feed.memo.miss_by`. Fewer builds is the algorithm fix; cheaper builds is the language.
- **Memory:** `process.rss_kb` per process plus swap from `/proc/<pid>/status`; the Python total is kernel plus judge child, read beside `heap.recordCacheResident`, `recordCache` and `judge.child.recordCache`.
- **Pauses:** `gc.gen.2.msMax` and `msSum` on Python; Rust adds a cycle-time histogram.
- **Frames:** `sends.full`, `sends.delta` and `sends.deduped` are reported per kernel. Parity is judged on the client state after folding the frames, not on the frame sequence, since the redesign is meant to send fewer frames.

**How to compare.** A single before-and-after pair can mislead, because load on the machine drifts between the two runs. So alternate the two kernels on the same load, several times.

Count only runs with a browser connected. A reading with no client connected skips the push work a browser causes, so it understates the cost.

The load is synthetic: `write_world` in `tests/bench_connect_push.py` (the `notes-api` board with `web`, `api` and `tests` sessions), the `Gen` transcripts of `tests/bench_chatbuild.py` at small and large sizes, and a state copy replayed offline in the style of `tools/perf-bench.py`. Shadow mode adds a reading on real load.

**The one-week spike, before the month.** Port the transcript store and `build_feed`'s per-card derivation to a standalone Rust binary with no server. Run both implementations on the same synthetic state and compare the feed JSON byte for byte after canonical key ordering.

The per-card derivation reads more than transcripts: the goal store, the judge runs in flight, the judge-auth latch, the states logs and the live-session map. The spike takes these as a JSON snapshot that Python dumps once from the synthetic world, and both implementations read the same snapshot.

Measure CPU per build (warm and cold), a cold load, and the store's resident size. Python's side of the memory comparison is measured in the spike's own Python run: tracemalloc over the record cache, plus the process's resident-size difference before and after loading. `heap.recordCacheResident` is reported beside it for reference only.

Build the derivation twice, over typed structs and over `serde_json::Value` (Rust's generic JSON tree). The second measures the trap of porting dict-shaped code as dict-shaped data, which keeps most of the memory and much of the CPU cost.

**The targets,** relative to Python on the same load, proposed for the owner to set:

| Measure | Target, as a fraction of the Python kernel plus its judge child |
| --- | --- |
| Total CPU (`process.cpu_s`, both processes) | at most one quarter |
| Resident memory plus swap, both processes | at most one third |
| Pusher cycle p90 and connect push `ms_max` | at most one half |
| Worst cycle (`cycle_ms_ring_max`) | at most one fifth, no stall tied to a collection |
| Client state after folding every frame through `view-deltas.ts` | identical, which is parity; frame counts and bytes are a performance figure, expected lower |

**The spike's gate** is a feed build at least five times cheaper in CPU over typed structs, and a store at most a third of Python's measured resident size for the same files.

The gate is stricter than the final CPU target because the build is where compiled code gains most; the kernel's total also includes I/O and system calls, which do not get faster.

Passing starts the month. Missing starts the staged fallback with the store, where the memory gain does not depend on interpreter speed.

### How this relates to the other plans

**`plans/free-threading.md`** moves the kernel onto CPython 3.15t (the free-threaded build of Python 3.15, which can run without the GIL) with the GIL still on, and its later stages would turn the GIL off. That removes one language cost and keeps three: the Python objects (that plan's own lab found more resident memory on the free-threaded build), a stop-the-world collector, and interpreter overhead. It also rests on a hand audit of about 150 shared-state sites and on CPython's crash fixes.

Rust would make that plan's stages 2 to 5 (turning the lock off and auditing shared state) unnecessary. Rust's compiler refuses to build code that shares data between threads unsafely (its `Send` and `Sync` rules), which is what the hand audit checks.

The approved stages 0 and 1 are still worth finishing. Python serves users until cutover and stays the rollback, and stage 1's map of which threads share what is the inventory the Rust tasks must each own.

**`plans/process-split.md`** moves work out of the interpreter the browser waits on, paying with a process boundary and a second copy of the data. Its lesson, that good seams hand over compact bytes rather than parsed trees, is the transcript store's design.

In Rust, decoding is cheap and parallel in one process, so the decode worker, the judges' child and frame-builder processes become unnecessary (Python keeps them through the rollback window). Saved documents stay, for a fast cold boot; for the month Rust writes them under its own version (compatibility rule 2).

**`plans/multi-kernel.md`** is unaffected: a Rust kernel is one more registry entry.

## What exists today, and the Rust architecture

In one sentence: the Rust kernel keeps every outside contract (the browser's HTTP and WebSocket traffic, the state directory, the session-host socket, the calls other processes make) unchanged, and changes only how the work inside is scheduled and stored.

### The processes today

Only the Python core is in scope.

- **The manager** (`bin/romp-manager`, about 1.1k lines of Node) spawns each kernel through `bin/romp-serve`, respawns it on a crash or restart request, and has a loopback control port. It stays.
- **The kernel** (`bin/romp-kernel` loading `kernel/kernel.py` and its siblings) serves loopback HTTP and WebSocket (`_LoopbackServer`, a `ThreadingHTTPServer`, one `Handler` class). `main()` starts more than a dozen long-lived threads, among them `producer` (the judges' loop), `pusher`, `jobs`, `doc-writer`, `timeline-build`, `_heartbeat`, `_tunnel_supervisor`, `_update_check_loop` and `_ensure_postal_bus`.
- **The judges' process.** With `$STATE/judges-process` set to `on`, the judge tiers run in a `romp-judge --serve` child (`_JudgeChild`); otherwise as threads from `_producer`. Judges call models as `claude -p` or `codex exec` subprocesses.
- **The document worker** (`bin/romp-doc-worker`, `kernel/doc_worker.py`, switched by `$STATE/doc-worker`) builds one saved document per process.
- **One session host per Claude session** (`bin/romp-session-host`, `kernel/session_host.py`) owns the CLI process, journals its output and serves a Unix socket, which is how sessions survive a kernel restart.
- **The agents**, a Claude Code CLI or a `codex app-server` thread per session, are untouched.
- **Postal** (`postal/postal_service.py`): `romp-postal-service serve` is a loopback HTTP bus with Maildir mailboxes under `$STATE/postal/`, and `romp-postal-service mcp` is each Claude session's hand-written stdio MCP server (MCP is the protocol agents use to call external tools).
- **The CLI** (`bin/romp`, about 3.3k lines of bash) calls the kernel with `curl`, the serve token passed as a curl config on stdin. It stays.

### The kernel's subsystems

kernel.py has about 96k lines and 2,686 top-level definitions. The rough line counts below overlap, since helpers serve several rows, so they sum past the file's size.

| Subsystem | Main functions and classes | Rough lines |
|---|---|---|
| HTTP and WebSocket server | `Handler` (`do_GET`, `do_POST`, `_dispatch_ws`), `_ws_send`, `_ws_recv`, `_heartbeat`, `_LoopbackServer` | 9k |
| Build pipeline: feed, chat, timeline, deltas | `build_session`, `build_feed`, `build_timeline`, `build_subagent`, `_send_chat`, `_send_client`, `_push`, `_pusher_cycle`, `_DELTA_SLOTS` | 30k |
| Session lifecycle and drive ops | `class Sessions`, `_drive`, `_live_map`, `_interrupt_marks`, `_machine_cut_cause`, `_drain_and_exit`, `_graceful_term` | 20k |
| Goals, nudges, jobs, boards, notifications | `_jobs_pass`, `_jobs_loop`, `_jobs_gate_due` and siblings, `_auto_nudge_tick`, `_stalled_goals`, `_spend_guard_tick`, `_producer` | 20k |
| Remotes, tunnels, PR watches, update, restart | `_tunnel_supervisor`, `set_trust`, `_host_trust`, `_update_check_loop`, `_pr_watch_contact_sid` | 10k |
| Stores, ledgers, comments, checkpoints | `_comment_create`, `_persist_checkpoints`, `_doc_writer_loop`, the load and persist pairs | 10k |
| Instrumentation | `_PerfStats`, `_perf`, `_heap_stats`, `_gc_freeze_tick` | 4k |

Smaller subsystems also run inside the kernel and must not be forgotten:

- **Web Push** to phones and browsers: RFC 8291 and RFC 8292 cryptography (P-256 keys, HKDF, AES-GCM, an ES256 token), outbound HTTPS, and the key file `push-vapid.json` (`_webpush_encrypt`, `_vapid_auth`, `_vapid_keys`).
- **The login flow**, which drives the Claude CLI's login output (`_login_reader`, with `kernel/logins.py` and `kernel/credentials.py`).
- **Comment threads**, which are running agent processes with their own live map (`_thread_live_map`).
- **The question poll** (`_ask_poll_pass`), which polls each live session for pending questions on a short timer.
- **Postal-bus supervision** (`_ensure_postal_bus`, `_revive_postal_bus`).
- **Background fetchers and indexes:** the `model-catalog` and `router-models` threads and the repo index.

Around kernel.py sit its sibling modules:

- `event_model.py` (13k lines, 408 top-level definitions, no threads) turns transcript records into turns and atoms, the smallest pieces a chat is drawn from, such as one tool call or one text block (`segment_turns`, `FileAdapter`, `tail_parse`, `parse_session`). It owns the record cache and its reader `_read_jsonl_incremental`.
- `sdk_backend.py` (25k) drives Claude through the Python Claude Agent SDK; `judge.py` (22k) holds the judge tiers, the goal store and its migrations.
- `codex_backend.py` and `codex_events.py` (4k) drive Codex and turn its notifications into transcript-shaped records; `session_host.py` and `host_transport.py` add about 2.2k.
- Smaller ones: `logins.py`, `credentials.py`, `narration.py`, `model_fast.py`, `subagent_canary.py`, `session_backend.py`, `cli_prices.py`, `codex_runtime.py`, `gc_freeze.py`, `doc_worker.py` and a few helpers.
- Outside `kernel/`: postal (about 6.6k), and `cli/spend_rebuild.py` and `cli/spend_repair.py`, which write `spend.json` offline. These two stay Python and read and write the frozen `spend.json` format.

### The contracts the UI, the tools and a rollback depend on

**HTTP.** `Handler` serves 104 distinct route paths: pages and assets (`/`, `/dist/`, `/media/`, `/sw.js`, `/manifest.webmanifest`), views (`/feed`, `/timeline`, `/chat`, `/sessions`, `/views`, `/panes`), drive actions (`/send`, `/new`, `/fork`, `/interrupt`, `/compact`, `/rename`, `/move`, `/color`), settings (`/settings`, `/judge-settings`, `/mesh-settings`), browser push (`/push/*`), tunnels and remotes (`/tunnels/*`, `/remote/`), postal hooks (`/deliver`, `/postal-notice`), diagnostics (`/perf`, `/healthz`, `/version`, `/api-health`, `/client-diag`) and the `/ws` upgrade. Callers send the `X-Romp-Token` header, whose value is in `$STATE/serve-token`.

**WebSocket.** The kernel frames WebSocket itself, permessage-deflate compression included (`_ws_send`, `_ws_deflate_offer`). Inbound messages go first to `_drive`, whose `ID_OPS` names the per-session actions (`sendMessage`, `interrupt`, `answerAsk`, `setModel`, `forkSession`, the comment operations); the rest go to `_dispatch_ws`, about 90 interface messages (`activeTab`, `loadOlder`, `needSlot`, `needFull`, many `set*` settings).

Outbound frames include `feed`, `timeline`, `chatTail`, `chatTurns`, `chatWindow`, `sessionList`, `liveStatus`, `sendAck` and `settingAck`. Large views travel as deltas: `_DELTA_SLOTS` keys each collection of the `feed` and `bars` frames, `_DELTA_MAX_FRACTION` decides when a whole frame goes instead, and a client that cannot apply a delta asks for `needSlot` or `needFull`.

**The state directory.** kernel.py, judge.py and sdk_backend.py name about 106 stores under `$STATE`, all JSON, JSONL or directories (`names/`, `goals/`, `sdk/`, `checkpoints/`, `session-order.json`, `auto-nudge.json`, `nudge-events.jsonl`, `spend.json`, `usage.json`, and more).

In `checkpoints/`, fold checkpoints are plain JSON and saved session documents are gzip-compressed JSON (`*.asm.json.gz`) with a `.meta` sidecar. The judges, postal and the bash CLI share these files under `fcntl` locks. The only pickle is an in-memory cache in judge.py, so nothing on disk is Python-only.

**The session-host protocol,** version 1 (`PROTOCOL_VERSION` in `session_host.py`), is newline-delimited JSON on the host's socket; the drivers section lists its messages. The kernel's side is `HostTransport` in `host_transport.py`, implementing the SDK's six-method `Transport` interface.

**Other processes' calls.** These routes are a contract with processes that stay Python or bash, so none may be stubbed or switched off without a decision. The list below comes from fixed-string extraction at `bc551f41c`; lane T2 regenerates it as a checked file, and the harness covers every entry.

- **`bin/romp`** reads `/sessions`, `/perf`, `/api-health`, `/healthz`, `/version`, `/views`, `/panes`, `/boards`, `/watches` and `/logins`, and posts to `/new`, `/send`, `/fork`, `/rename`, `/move`, `/color`, `/tag`, `/board`, `/pane`, `/compact`, `/apply`, `/watch`, `/watch-pr`, `/notice`, `/logins` and `/tunnels/checkin`, some through its POST helper and some by direct `curl`.
- **`bin/romp-manager`** calls `/busy` and `/version`.
- **`postal/postal_service.py`** posts to `/deliver`, `/postal-notice`, `/working`, `/walk-root` and `/redial`, and reads `/sessions/by-fsid`.
- **`hooks/romp-wake.sh`** posts `/tick`, the event-driven wake the Stop and UserPromptSubmit hooks send.

**Kernel to kernel.** Kernels on different machines talk to each other: remote relays (`/remote/…`, `_remote_forward`, `_REMOTE_SESSION_READS`), settings propagation (`_push_settings_to_peer`, `_propagate_judge_settings`) and forwarded `/deliver`. During the rollback window one side can be Rust and the other Python, so this is a frozen contract too.

### The Rust shape

A cargo workspace builds one binary, `romp-kernel-rs` (a distinct name, so `romp-kernel` keeps meaning the existing Python launcher). The manager starts either kernel through `bin/romp-serve`, chosen by a state switch the way `judges-process` is chosen.

| Crate | Responsibility | Replaces |
|---|---|---|
| `romp-json` | JSON written exactly as Python's default `json.dumps` writes it | (new) |
| `romp-wire` | HTTP server and routes, typed bodies and frames, deltas per `_DELTA_SLOTS`, WebSocket framing with deflate | `Handler`, `_ws_send`, the delta code |
| `romp-state` | One typed module per store, atomic writes, the same `fcntl` locks, unknown fields kept | the load and persist pairs |
| `romp-transcript` | The transcript store: raw bytes plus an index | `_JSONL_CACHE`, `_read_jsonl_incremental` |
| `romp-events` | Records to turns and atoms; saved documents | `event_model.py` |
| `romp-views` | Feed, chat, timeline and subagent builders, keyed per session | the build pipeline |
| `romp-claude` | The Claude CLI control protocol over the host socket | `sdk_backend.py`, `host_transport.py` |
| `romp-codex` | The app-server client and event normalization | `codex_backend.py`, `codex_events.py` |
| `romp-judges` | Judge tiers, the goal store, model subprocesses | `judge.py`, `_producer` |
| `romp-postal` | The bus and the stdio MCP server | `postal_service.py` |
| `romp-push` | Web Push cryptography and delivery | `_webpush_encrypt`, `_vapid_*` |
| `romp-logins` | The login flow and credential selection | `_login_reader`, `logins.py`, `credentials.py` |
| `romp-core` | Sessions, jobs, wiring, restart and drain; builds `romp-kernel-rs` | the rest of kernel.py |
| `romp-diff` | The comparison harness | (new) |

**Runtime.** tokio is Rust's standard engine for running many waiting tasks on a few threads; hyper and axum are HTTP and WebSocket server libraries on top of it; serde is the library that turns Rust types into JSON and back, and is used for every shape.

CPU-heavy builds run on a separate pool of worker threads (rayon, or tokio's `spawn_blocking`), so a long build never delays a request. The WebSocket library must support permessage-deflate, or the kernel's few hundred lines of framing are ported into `romp-wire`.

**Byte-compatible JSON.** Most `json.dumps` calls use Python's defaults: `", "` and `": "` separators, non-ASCII escaped as `\uXXXX`, Python's float formatting (`1e-05`, `5.0`). `romp-json` reproduces that with a custom serde formatter and insertion-ordered maps. Then `_DELTA_MAX_FRACTION`, which compares payload lengths, chooses the same way, and the harness can compare bytes where a frame family needs it.

**Concurrency.** Today the pusher wakes on `_pusher_wake` or a 0.5-second backstop, the jobs thread runs every job in a fixed order every `JOBS_PASS_S`, and the judges loop on `_producer_wake`. In Rust these become tasks reacting to typed events:

- a **change bus** carrying "transcript appended (session, byte range)", "store written", "verdict filed", "client message", "host frame" and "timer due";
- a **sessions actor** (one task that alone owns the data; others send it requests instead of taking a lock) owning the registry and every drive op, so no lock audit is needed;
- **per-session view tasks** that rebuild on their inputs and hand fragments to **per-client senders**, which compute deltas against what each client last acknowledged;
- **job handlers** subscribed to the events that can change their answer, plus a timer index, keeping `_jobs_pass`'s documented order as an explicit dependency order;
- **judge tasks** under a semaphore sized by the judge-concurrency setting, model calls as `tokio::process` children;
- **one writer task per store**, as the document writer does today.

Several ordering rules came from past incidents: a writer whose evidence predates a filed verdict stands down, and a transient state holds until its deciding event. In Rust they become checks on event sequence numbers instead of timestamp comparisons across threads.

**The transcript store.** Rust keeps each transcript's raw bytes plus a per-line index: byte offset and a compact summary (record type, `uuid`, `parentUuid`, timestamp, role, tool ids, size). Full records are decoded on demand as borrowed slices and dropped behind a small bound. Live transcripts keep decoded records only after their last compaction boundary; older records stay as raw bytes and are decoded on demand.

It keeps the identity fields the Python cache tracks (inode, size, mtime, a CRC over the bytes read), so a shrunk, rewritten or relocated file is re-indexed.

It uses positioned reads (reading a given byte range with `pread`), not a memory map. The CLI can truncate a transcript in place, and touching a mapped page that a truncation removed kills the process (SIGBUS). Builders and judges share the one store.

### The algorithms redesigned during the port

Each fix is how Rust works from day one, and each must still produce what Python shows for the same inputs. Anything that changes what the user sees waits until after parity.

- **Clock rebuilds become due-time patches.** Each build records the next moment a shown value changes and arms a timer; when it fires, only the time-dependent fields (ages, tints, the live edge) are recomputed and patched in. Full builds run only on input events. No UI change is needed, and the ticks can still move to the browser later.
- **Whole-feed rebuilds become per-session fragments.** Each session's cards are a fragment keyed by that session's revision, with facts read from other sessions (peers, postal, background) as explicit dependency edges. A change invalidates exactly the fragments that read it, and deltas go by card (`itemId`), which the `feed` slot already supports.
- **Rescans become indexes.** A due-time index and a per-session dirty set fed by transcript events replace the full passes of the nudge walk, spend guard, spent-awaiting lift and interrupt block, finishing the two event plans. Subagent trees use the per-scope fingerprint keyed on romp's own writes that discovery uses.
- **File-system watches are only wake hints confirmed by a stat,** the way the subagent tree watch already treats inotify (Linux's file-change notifications; `_TREE_WATCH_ON` in kernel.py). A watch alone cannot tell a file shrunk, rewritten or relocated in place from an append.
- **Whole-file scanners become folds over the index.** `_bg_scan_all_cached`, `_session_meta` and the judges' readers read summary fields from the shared index.

### Compatibility rules

1. **The wire contract is frozen:** routes, methods, status codes, headers, frame types, field names and delta semantics, for the browser, the other processes listed above, and kernels on other machines. The UI changes no line for the cutover.
2. **State formats are frozen, except saved documents.** Rust reads and writes every store in its current format, keeps unknown fields and takes the same locks, and for the month adds no store or field. Saved session documents are the exception: Rust writes them under its own version stamp, which Python refuses and rebuilds after a rollback. That costs boot time, not data.
3. **Rollback is a restart** that finds every session, goal and setting where Rust left them, including what Rust records to resume a host, such as the acknowledged journal offset.
4. **The session-host protocol stays at version 1** until both kernels speak a version 2.
5. **The CLI and the manager change by at most the kernel switch;** `bin/romp-serve` gains the switch.
6. **Rust reports a code identity of its own** in `/version` (`code_ident`) and the page stamp (`__ROMP_CODE__`), so a switch either way reloads open pages. Python's `_code_ident_of` hashes `kernel/*.py`, which says nothing about a Rust binary.

## Driving Claude and Codex from Rust

**The problem.** If the Rust kernel speaks a session's protocol slightly wrong, nothing crashes. A permission prompt never shows, a hook answer arrives late, or a turn's cost is counted twice. These failures are quiet and show only in live use, so the drivers carry the largest correctness risk. The data path is larger, but its mistakes show up as diffs.

**The conclusion.** For Claude, the Rust driver re-implements a protocol that today only the Python Claude Agent SDK implements, so it must be pinned to one CLI version and checked against frames recorded from the Python SDK. For Codex, the server is Rust already and romp can use its protocol types. The judges and postal need no agent protocol. The Python designs being replaced are in `plans/sdk-backend.md` and `plans/codex-backend.md`.

### Claude: what the Python SDK does today

romp uses `claude_agent_sdk` as `bin/romp-sdk-setup` installs it, which is unpinned, so the version is whatever release was newest at install time. The SDK bundles its own pinned CLI.

In `kernel/sdk_backend.py`, `SdkSession._amain` opens a `ClaudeSDKClient` in streaming mode with options from `SdkBackend._options`. romp also reaches past the public API three times: the private `parse_message`, the private `Query._send_control_request`, and the private `Transport` base class that `kernel/host_transport.py` subclasses.

The Rust driver must get four things right. The full flag, frame and subtype inventory is in the appendix.

**Launch.** With the Python hosts kept, the Rust kernel never builds the CLI's command line itself. It writes `spawn.json`, and the host builds argv and environment by passing it to the Python SDK's `SubprocessCLITransport`. So Rust must write exactly the `spawn.json` Python writes. The resulting argv and environment still matter, as a check that the SDK on the host has not drifted from the pin.

**Framing.** Both directions are one JSON object per line. A line that starts with `{` and does not parse is an error, not skipped, and an oversized message is an error too.

**Messages.** The driver must classify every frame the CLI sends as the SDK and `sdk_backend.py` do, because a misclassified frame silently changes turn logic. Three behaviors live in the SDK's `Query` (`_internal/query.py`), not in its message parser, and are easy to miss:

- The SDK sets `CLAUDE_CODE_SDK_READS_SESSION_STATE=1`, which makes the CLI emit `session_state_changed` frames marked `sdk_host_only`. `Query._read_messages` uses them for its end-of-run logic and then drops them before they reach romp. A Rust driver that misses this either never gets them or forwards extra frames into romp's turn logic.
- `Query` tracks background tasks across `result` frames before it declares a run over.
- `Query` turns a non-zero exit after an error result into a typed `ResultError` and fails pending control requests fast.

Lane D1 ports these from `query.py`, not only from `message_parser.py`.

**Control requests.** Both sides send requests, and every one must be answered with the matching id in time. A missed answer leaves a permission prompt or a hook hanging. romp sends `initialize`, `interrupt`, model and permission changes, file rewinds and several raw requests; the CLI sends `can_use_tool` (permission asks), `hook_callback` and `mcp_message`. romp registers Stop, UserPromptSubmit, SubagentStart, SubagentStop, PostToolUse (four matchers) and PostToolUseFailure hooks.

### What sdk_backend adds on top

**Session hosts.** The kernel writes a `spawn.json` limited to `SPEC_FIELDS` plus the host's own `SPEC_HOST_KEYS`. `romp-session-host` spawns the CLI through the SDK's own `SubprocessCLITransport` (so the argv is the SDK's byte for byte), journals every stdout record, and serves a socket (`PROTOCOL_VERSION = 1`, newline-delimited JSON with a `t` field).

The kernel sends `attach`, `in`, `ack`, `signal`, `end`, `detach`, `ping` and `reexec`. The host sends `hello` (CLI pid, journal position, parked request ids, hooks it answered itself, the launch record), `out`, `stderr`, `exit`, `fault`, `busy`, `pong` and `reexec` replies.

After a kernel restart, `HostTransport` replays the journal from the last acknowledged offset, receives every open control request again, and sends a fresh `initialize`, which the CLI takes as a replacement hook table.

That last step has a quiet failure. The pinned SDK says a CLI process that is already running can answer `initialize` with `hooks_applied: false`, meaning nothing registered the hooks. The SDK checks this only for hook matchers with `on_failure` set, and romp sets none. So after a reattach a CLI could drop romp's Stop, permission and PostToolUse hooks with nobody noticing. **On every reattach `initialize`, the Rust driver checks `hooks_applied`; `false` is a host fault surfaced to the user, never ignored.** Lane D0 records the pinned CLI's answer to a second `initialize` on a running process.

With no kernel attached, the host answers a parked hook with `HOOK_NEUTRAL_OUTPUT` before the CLI's deadline. `_reexec_now` swaps the host's own code and keeps the CLI: it writes a handoff file with the CLI's pipe descriptors and execs, and the new process checks them against `/proc/<pid>/fd` (`_pipe_fds_match_cli`) before `AdoptedCliTransport` uses them.

**Decision: the Rust kernel speaks host protocol 1 to the existing Python hosts.** Hosts do little work, and keeping them makes cutover and rollback a kernel restart, which sessions already survive. A Rust host can come later through the re-exec handoff: with the handoff file format frozen, a Python host can exec a Rust host in place. The hosts-off path, where the kernel spawns the CLI itself, is not ported.

Two consequences follow. The Python SDK stays a runtime dependency after cutover, because the hosts use it. And `session_host.py` loads lease helpers from `sdk_backend.py` (`_lease_api`: `write_lease`, `remove_lease`, `proc_start`), so those functions stay frozen in Python.

**The semantics in `sdk_backend.py`,** each needing its own parity cases:

- **Spend.** `_record_spend` folds results into `spend.json`. `total_cost_usd` is cumulative per CLI process, so romp converts it to deltas with watermarks (`cost_state_watermarks`, `_seed_spend_watermarks`, `_persist_cost_state`) and drops repeats (`_spend_redelivered`). The spend guard's stop is `append_spend_cut`; prices are in `kernel/cli_prices.py`.
- **Model fallback.** A refused live model change goes through `_do_set_model`, which restores the previous model; `model_refusal_fallback` frames are filed by `_on_refusal_fallback`, and `model_fallback_row` marks the picker.
- **Turn bookkeeping.** `_turn_frame` and `_untaken_taken` decide when the CLI has taken a queued message.
- **Host adoption.** Reattach after a restart, mid-turn and mid-permission request, with the `hooks_applied` check above.

### Keeping the Rust driver in step with the CLI

`bin/romp-sdk-setup` installs the SDK with `pip install --upgrade` and no pin, so today the SDK silently absorbs CLI changes. A Rust driver has nothing that does that, and the hosts' unpinned SDK could even change the CLI's flags under a pinned Rust protocol. So:

1. **Pin the SDK and one CLI version.** `romp-sdk-setup` pins `claude-agent-sdk` before week 1. The driver records the CLI version it was checked against, reads it at launch, and shows an error outside the supported range.
2. **Record conformance from the Python SDK.** A script runs the pinned SDK over the option matrix `_options` builds (fork, resume, rewind, login versus key, fast, ultracode). It records the `spawn.json` content, the argv and environment keys from `_build_command` and `connect` (including the SDK's own extra variables: the session-state flag, client-app labels and trace context), and every frame both ways. It runs once against `tests/fixtures/fake_claude.py` (which stages permission asks, hooks and cancels) and once against the real pinned CLI in a throwaway config directory. In CI, Rust must write the same `spawn.json` (every `SPEC_FIELDS` and `SPEC_HOST_KEYS` key), send the same outgoing frames, and parse incoming frames to the JSON `parse_message` produces; the recorded argv and environment check the pin.
3. **Keep unknown frames.** Serde enums tagged on `type` and `subtype` each have a catch-all variant holding raw JSON. An unknown frame is logged and passed through, and an unknown control subtype fails a check.
4. **Upgrade by procedure:** bump the pin, re-run the recorder, read the diff, update, merge.

### Codex: use the server's own types

`kernel/codex_backend.py` uses the `openai-codex` Python SDK (0.144.4, `SDK_PIN`). `CodexClient` launches `codex [--config k=v]… app-server --listen stdio://`, with the runtime pinned in `kernel/codex_runtime.py` (0.169.0, wheels checked by SHA-256); the handshake is `initialize` with `clientInfo` and `capabilities.experimentalApi`, then `initialized`. One app-server hosts every Codex thread.

- **Requests:** `thread/start` (with `dynamicTools` for postal, `developerInstructions`, `approvalPolicy`, `approvalsReviewer`, permission fields), `thread/resume`, `thread/compact/start`, `thread/name/set`, `turn/start`, `turn/steer`, `turn/interrupt`, `model/list` (raw, via `_request_raw`) and `account/read`.
- **Notifications:** `turn/started`, `turn/completed`, `item/started`, `item/completed`, `thread/compacted`, `thread/tokenUsage/updated`, `thread/status/changed` and `error`, turned into transcript-shaped records by `ThreadNormalizer.feed` in `kernel/codex_events.py`.
- **Server requests:** `item/tool/call` (postal), answered by `_postal_tool_call`; `_handle_approval` declines `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval` and `item/tool/requestUserInput` at once.

The app-server's wire types live in the openai/codex repository's app-server protocol crate. Depending on it at the runtime pin's tag keeps the types from drifting and turns a runtime bump into compile errors wherever a field changed; undescribed fields go as raw JSON.

With `codex_events.py` on plain dictionaries and `tests/test_codex_events_golden.py` as an oracle (a reference whose output is taken as correct), this is the lowest-risk port.

One behavior is kept on purpose: `_handle_approval` declines manual approvals because the Python SDK runs it on its only reader thread, where waiting for a person would stall every Codex session. Rust lacks that limit, but parity comes first.

### The judges and postal

The Rust judge runner must reproduce exactly the command line and environment Python uses. Otherwise a judge gets a different model, tool set or settings, and its verdicts quietly change.

`kernel/judge.py` does not stream. `_judge_cmd` builds `claude -p --safe-mode --model M --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --system-prompt S --exclude-dynamic-system-prompt-sections --output-format json`, plus `--effort` when set and a `--settings` overlay for fast mode or to turn off the API key helper. The prompt goes on stdin, and stdout is one envelope (`result`, `usage`, `duration_ms`, `total_cost_usd`). `_judge_cmd_codex` runs `codex exec --ephemeral … -o <file>` under a judge-only permission profile.

Rust needs:

- a runner with a hard deadline and kill, as `perl -e alarm` (`CALL_ALARM_S`) gives today;
- a semaphore re-read from state each pass, as `_TimedPool` is sized by `_judge_concurrency()`;
- the same environment and overlay rules (`_judge_env`);
- envelope parsing, including fast-mode state and the usage rows for spend;
- the failure classification (`_call_fail_kill`);
- `ROMP_CLAUDE_BIN` support, so tests can use a fake CLI.

`postal/postal_service.py` is standard library only. Its hand-written JSON-RPC loop handles `initialize` (echoing `protocolVersion`), `notifications/initialized`, `ping`, `tools/list` and `tools/call` for the six tools in `MCP_TOOLS`. Rust can use `rmcp`, the official Rust MCP SDK, or an equally small serde loop if `rmcp`'s dependencies or version handling get in the way.

Postal is its own process, so it can be ported early, switched last, or stay Python at cutover. Its command names and modes stay, since bash, hooks and the MCP config call them.

**Drivers are not shadowed live.** The shadow kernel never attaches to a host, by design (see the shadow leg). Driver checks are instead the replay of host journals through both readers, diffing the decoded messages and the state written, plus lab kernels over the fake CLI. Real journals are replayed only locally under `$STATE`; committed cases are the recorder's synthetic ones.

## Proving the Rust kernel behaves the same

A rewrite is safe only if something says, every day, whether Rust still does what Python does. The existing tests mostly cannot. Of the roughly 1,080 Python files under `tests/`, about 850 load kernel modules in process and call or patch internals, and only about 160 talk to a running kernel over HTTP, WebSocket or a browser. Rewriting the rest against Rust internals would take longer than the port.

So **the Python kernel is the specification: both kernels run on the same synthetic world, driven by the same script, and everything they show the outside is compared.** Where they disagree, Rust is wrong unless the disagreement is a known Python bug recorded in a ledger.

### The comparison harness

**Each kernel gets a private copy of one synthetic world.** Sharing a state directory would make both write the same stores under the same locks, and the diff would measure the race.

Seeding reuses `kernel_env` in `tests/test_ship_reship_served.py`, which builds a hermetic environment from named variables: state under `XDG_STATE_HOME`, transcripts under `CLAUDE_CONFIG_DIR` (read by `judge.py` as `PROJECTS`), its own `ROMP_KERNEL_PORT` and token, a postal port that is never started, and `ROMP_CLAUDE_BIN` pointed at a stand-in.

**The corpus is synthetic and checked in as generators,** with no real transcript, prompt, id or session name, and worlds named like the demo (`notes-api` with `web`, `api` and `tests`). It starts from:

- the event-model scenarios behind `tests/fixtures/event-model-golden/` (compaction, rewinds, resume and clear lineage, queued turns, retries; built in `tests/test_event_model_golden.py` with a fixed clock and placeholder UUIDs);
- the Codex scenarios in `tests/test_codex_events_golden.py`;
- the served labs' worlds, such as `test_strip_scroll_keep_served.py`'s many-session world;
- transcripts from `tests/fixtures/fake_claude.py`, which writes each turn to `FAKE_CLI_TRANSCRIPT_DIR` and stages long turns, permission asks, hook callbacks, background subagents and automatic compaction from tokens in the message text;
- files the CLI shrinks, rewrites and relocates.

Large transcripts are generated at run time.

**Both kernels run on one shared, stepped clock.** Frames carry values computed from the clock, not just timestamps: "X ago" ages, elapsed times, age tints, and nudge and stall due decisions. The kernel has no clock seam today (kernel.py calls `time.time()` at 323 sites, with no override). Two kernels started at different moments would show different ages at the same point, and Python may never settle while an age keeps changing.

The fix is a kernel-wide clock seam: one `_now()` that reads a harness clock file when a test variable names it, the same in both kernels. The harness then advances time in explicit steps. The judge child's protocol already accepts an explicit clock (`{"op":"pass",…,"now":<epoch>}` in judge.py).

**Steps are compared only when both kernels are settled.** A step is an HTTP request, a WebSocket message, a transcript append, a clock step, a state write published by rename (the labs' `PUBLISH_JSON_JS` rule: write a temporary file, then rename it, so a reader never sees half a file) or a fake-CLI event.

The harness then waits until neither pusher has sent anything for two idle cycles (`pusher.sends`, `pusher.idle_cycles`) and no judge pass is in flight. So Rust must serve those `/perf` fields with the same meaning from week 1; a small `/debug/settled` route is the backup.

**Five outputs are compared at each settled point:**

1. **Served documents:** `/feed.json`, `/timeline`, `/sessions`, chat payloads, session builds and the other routes, as parsed JSON or normalized HTML.
2. **WebSocket streams, folded through the shipped client.** Two correct kernels can batch one change into different frames. So each kernel's frames, decompressed, go through the extension's own decoder (`vscode-extension/src/view-deltas.ts`, and the `KernelPipe` class `pipe-view-deltas.test.ts` already runs on synthetic sockets), and the resulting client states are compared. The raw sequence is a weaker report: an extra full frame is a performance note, not a failure.
3. **What each kernel sent its sessions:** `FAKE_CLI_LOG` records every stdin line the scripted CLI received, which checks the Rust control protocol.
4. **The state directory,** file by file and JSONL record by record.
5. **What other processes see:** `romp sessions --json`, `romp perf --json` and similar CLI verbs, plus every route postal, the hooks and the manager call (the list under the contracts).

**A component harness compares the builders directly,** with no timing noise. The builders take the clock as an argument (`build_feed(now, live_map)`, `build_timeline(now, ...)`, `build_session(sid, now, ...)`), and `romp-event-model --emit <transcript>` prints `plain_tree(session)` as sorted JSON; Rust's equivalent is `romp-kernel-rs emit <transcript>`.

Two harness-only seams finish it: a `--now` option for `romp-event-model --emit` (its `main` passes `int(time.time())` today) and a builder dump command on each kernel. Most divergences should surface here first.

**The harness proves it can see a failure before it is trusted:** Python against Python must show zero differences after normalization, and a planted deviation must be reported.

### Normalization rules

Each rule lives in a rules file with its reason.

- **Timestamps** (`t`, `end`, mtimes, `/perf`'s `now` and `since`) become order-keeping tokens per kernel (`T1`, `T2`): equal stays equal, order survives. Other `/perf` timings and counters are dropped.
- **Clock-derived values** (ages, elapsed times, tints, due fields) compare only when both kernels ran on the shared clock. Without it, end-to-end comparison of those fields is disabled and left to the component harness.
- **Content-derived ids must match** (a turn id such as `<sid>:<t>:<hash>` comes from the transcript). Random ids (a `uuid4`, a ship id, a CLI `request_id`, boot ids, the serve token) are renamed by first appearance (`R1`, `R2`) consistently across all five outputs.
- **Ordering:** object keys compare as sets; lists compare in order unless a path is listed as unordered with a reason, so a Sessions pane sort regression is caught by default.
- **Transport:** compression, chunking, header order and the `?v=` bundle token are ignored; status codes and content types are not.
- **The live-session map's source:** in the harness, both kernels get the same map from the stand-in hosts; in shadow, Rust takes the live kernel's map (see the shadow leg) and the rule records that this input is not Rust's own.

**Every rule is tested by planting a difference it must not hide** (equal timestamps made unequal, a content id changed by one character, two ordered rows swapped). A comparison that never fails is the signature of a broken comparison, not of identical kernels.

### The divergence ledger

Every divergence ends in one of four states:

- **Rust bug:** fixed in Rust; the case stays in the corpus.
- **Known Python bug:** Rust matches it for parity, or does the intended thing and the owner accepts the difference at cutover. The entry is reviewed data in the repository (for example `tests/parity/divergences.json`): the surface (route, frame kind or state file, plus JSON path), a matcher scoped to that path and corpus world, the reason, the date and the decision.
- **Harness noise:** the rule is fixed and re-proven with a planted difference.
- **Unexplained:** blocks cutover.

A matcher that starts matching a new world is reported as a new divergence, so entries never widen silently. The daily run reports ledger size and unexplained count. The decisions that need the owner go out as one daily digest, both outputs side by side with a recommendation.

### What the existing tests can and cannot do

A sample of 45 Python test files (every 24th in `tests/`) was classified by fixed-string markers (`load_source`, `mock.patch` and `monkeypatch`, `_SRC` and `getsource`, lab launches, Playwright) and then read:

| Kind | Files | Lines | Runs against Rust? |
|---|---|---|---|
| Served or browser, no kernel import | 7 | 3,280 | Yes, once the launch is switched |
| Not kernel code (CI policy, helpers) | 2 | 1,413 | Not affected |
| Imports the kernel, no patching | 19 | 3,646 | Some become golden or harness cases |
| Imports and patches the kernel | 13 | 12,631 | No |
| Pins kernel source text | 4 | 2,762 | No; passes, proves nothing |

The served group (`test_narration_served`, `test_tab_widgets_browser`) encodes user-visible behavior without naming an internal, the most valuable carry-over.

The patched group (`test_restart_cuts`; even `test_browser_first_draw` calls `km.Handler._push_one` in process) stays as Python's regression suite, and a past bug's edge case in it becomes a synthetic world. Of the unpatched imports, `test_event_model_postal_ismeta` can become an emit golden case (a golden file is a stored expected output the test compares against); `test_awaiting_audit_classes` reads private caches (`jd._PEER_ASK_CACHE`) and stays.

The 15 scenario trees in `tests/fixtures/event-model-golden/` must come out identical from `romp-kernel-rs emit`, the Codex goldens from an equivalent dump. The Python model's output for every corpus transcript is stored as an extra golden, since the goldens are thin.

The 40 bats files test the CLI against a stubbed `curl` (`romp-perf.bats`) and run unchanged. Of the 726 `.test.ts` files, the 177 that read `kernel/kernel.py` with `readFileSync` stay Python-only.

**A `KERNEL_IMPL` switch runs the reusable suites on either kernel.** `KERNEL_IMPL=python` (default) or `rust` picks the binary in one helper, `lab_kernel_cmd()`, which replaces the 153 literal `os.path.join(BIN, "romp-kernel")` launch paths in one mechanical commit. Served tests that go through `bin/romp-serve` use its new kernel switch (see the cutover).

Under `rust`, `tests/conftest.py` skips every file not marked `pytestmark = pytest.mark.kernel_blackbox` (an explicit opt-in) and prints how many and why. `ROMP_SERVED_TESTS_REQUIRE=1` turns a skipped browser test into a failure, so a lab that never starts cannot read as green.

### Judges: record the model, replay the answer

The same prompt can draw different verdicts, so judges are compared on the prompt each kernel builds and on what it does with a given answer. `_judge_run_impl` runs `_judge_cmd`'s argv with the prompt on stdin and reads an envelope, and the binaries come from `ROMP_CLAUDE_BIN` (`_judge_claude_bin`) and `ROMP_CODEX_BIN`, so a tape player can stand in:

- **Record once from Python on the corpus.** A wrapper forwards each call to a real model and stores argv, stdin and envelope, keyed by tier, model, hashes of system prompt and stdin, and a count of earlier identical calls, so the parallel pool cannot scramble the tape. Hand-written envelopes like `tests/fixtures/claude-p-result-envelope-*.json` add error and timeout shapes.
- **Replay into both kernels;** their goal stores, cards and injected messages must match.
- **A miss is a prompt divergence,** answered with an error envelope and reported with a diff against the nearest recorded prompt of that tier.

This works wherever the judges run, the `romp-judge --serve` child included. A deliberate prompt change is a re-record, reviewed like a golden regeneration.

### Rollback, host and shadow legs

**The round-trip leg** runs a script on Python, continues on Rust, finishes on Python, and compares with an all-Python run, catching any state Rust writes in a shape Python reads differently. **The host leg** kills Python mid-turn (and, in a variant, mid-permission request), starts Rust, and checks the turn ends with the same chat and CLI log as an uninterrupted run.

**The shadow leg** runs Rust beside the live kernel, on a second port, in a hard-coded read-only mode (lane R3). Read-only means no state writes, CLI spawns, model calls or injections, and it is built in, not left to other processes refusing it:

- **It never sends `attach` and never starts an app-server.** A host answers `busy` only while another kernel is attached, so during a live-kernel restart a shadow that attached would win the host and the live kernel would be refused. Shadow therefore has no attach code path at all.
- **It reads the live state root in place,** without taking write locks, or re-copies it before each comparison point. The live judges keep writing goals, states logs, spend and nudges there, and shadow cannot run judges itself, so a copy refreshed only at restart would go stale within minutes. A lock-free reader sees whole files for stores published by rename, and must tolerate a partial last line in a JSONL log, as the transcript reader already does.
- **It takes the live-session map from the live kernel** (its `/sessions`, or a map the live kernel publishes to a file, a harness seam) instead of deriving it. With no hosts attached, its own map would be empty, and every live card, badge and `liveStatus` row would diverge.

`kernel_env`-style settings give it an inert CLI and an unstarted postal port. A comparator diffs both kernels at settled points. Durable logs record only a divergence's shape (surface, JSON path, kind); content-bearing diffs stay under `$STATE`, and each divergence is reproduced as a synthetic world before triage. Shadow covers read paths only; write paths rely on corpus replay and the other two legs.

### What "done" means at cutover

Rust becomes the default when all of these hold on one commit:

1. The harness is clean on the full corpus, all five outputs and the component level: zero unexplained divergences, every ledger entry decided.
2. Every normalization rule catches its planted difference.
3. The reusable suites are green on Rust: black-box files, browser tests under `ROMP_SERVED_TESTS_REQUIRE=1`, the event and Codex goldens, the bats files.
4. The judge tapes replay with no misses, and each deliberate prompt change has a reviewed re-record.
5. The round-trip and host legs pass, and a rollback drill (Python to Rust to Python on a lab, diffing the state root after each leg) has passed.
6. Shadow has run at least three days, ending with one full day of normal use, on the cutover commit, with no unexplained divergence. Any Rust change to a read path restarts that clean day.

## The one-month plan, the conditions it needs, and the fallback

**Why the earlier estimate was 6 to 10 months.** It summed 50 to 90 weeks of agent work. At about three lanes, which it assumed because one person decides every divergence, that is 17 to 30 weeks. It also found a chain of steps that must run one after another (harness, then event model, then builds and drivers, then write paths, then shadow and cutover) of about 22 to 39 weeks. Both readings landed near 6 to 10 months.

**What the month changes.** It keeps the work but spreads it over 10 to 15 lanes, each checked by a machine (the harness and its oracles) rather than by a person reading its code. It shortens the serial chain by running its stages side by side: every lane starts from an interface fixed on day one, and the harness compares each crate the day it can build.

Put simply: the month holds if the harness shows a clean Python-against-Python `/feed` by day 5, and the owner clears each day's digest the same day.

Each lane owns a crate, a written interface, its oracle, an exit check, and a checklist of the dated rationale comments (thousands in `kernel.py`) in the code it ports, which its review subagent checks the port against. Lanes from a finished week become fix lanes.

### Week 1: harness, skeleton, event model, transcript store (13 lanes)

- **H1** harness runner and seeding (from `kernel_env`), with the shared clock; **H2** frame recorder, normalization rules, ledger; **H3** the synthetic corpus.
- **D0** the Claude conformance recorder, so recordings exist by week's end, including a second `initialize` on a running CLI.
- **S1** cargo workspace and CI, with per-platform release builds if the owner chooses binaries; **S2** `romp-wire`: token auth, all 104 routes stubbed and counted, the WebSocket handshake, the `Handler._dispatch_ws` message catalog, and the `pusher.sends` and `pusher.idle_cycles` fields of `/perf` the harness settles on.
- **E1** `romp-transcript`, keeping the cache's identity contract (mtime, size, offset, inode, CRC); **E2** and **E3** `romp-events` (`parse_session`, `tail_parse`, `segment_turns`, `FileAdapter`), split into record-to-atom assembly and turn segmentation.
- **C1** the Codex normalizer in `romp-codex`, a port of `codex_events.py`.
- **T1** `romp-state`, round-trip tested against Python; **T2** the test inventory (black-box, white-box with public calls, pinned), the `kernel_blackbox` markers, and the generated list of routes other processes call.
- **T3** read-only readers for the goal store (`load_goals_shared_or_fault`, override journal replay, `migrate_store`'s read path), the states logs, the SDK registry directory and the Codex rows, so the views can build from a state root without a live backend.

**Day 5 checkpoint:** H1 to H3 show zero Python-against-Python differences on `/feed` and catch a planted one.

**The rate gate, end of day 7:** Rust's event model matches every case in `tests/test_event_model_golden.py`, `tests/test_codex_events_golden.py` and the corpus, shrink and rewrite cases included. The harness shows zero differences Python against Python on `/feed`, catches the planted one, and diffs Rust's stubbed routes (status codes and shapes) against Python's.

### Week 2: builds, Codex driver, postal, read-only server, Claude protocol start (14 lanes)

- **B1** the feed (`build_feed`, the `_FeedSeg*` family) and **B2** sessions and chat (`build_session`, the tail-first path that builds a chat from its newest turns first, `build_chat`), two lanes each by function family; **B3** the timeline; **B4** the pusher: event-driven rebuilds, fragment keys, wire deltas.
- **K1** saved documents in `romp-events`, written under Rust's own version stamp, which Python refuses and rebuilds after a rollback. Byte compatibility is deferred until after cutover.
- **X1** and **X2** the Codex driver (`CodexBackend`): client, model and account reads, notification loop.
- **P1** postal, a mechanical port that may stay Python at cutover, plus the kernel's postal-bus supervision (`_ensure_postal_bus`, `_revive_postal_bus`).
- **R1** read-only routes; **R2** the rest of `/perf` with Python's field names; **R3** the hard-coded shadow mode: no attach, no app-server, lock-free reads of a live root, the live map taken from the live kernel.
- **D1** starts the Claude control protocol in `romp-claude` on day 8 from the recordings, as the longest lane.

Exit check: Rust serves the real UI from a state root, read-only, with the live-session map supplied by the harness, and with zero unexplained differences on `/feed`, `/timeline` and chat frames over the corpus.

### Week 3: Claude driver, hosts, sessions, jobs, judges, shadow start (15 lanes, the hardest week)

- **D1** to **D3** the control protocol, rebuilt from the SDK's `_internal/query.py`, `message_parser.py` and `types.py`; **D4** the host client with journal replay, re-sent requests, the `hooks_applied` check and `reexec`; **D5** the `sdk_backend` semantics (hooks, permissions, spend watermarks, model fallback, turn bookkeeping, raw requests) and the question poll (`_ask_poll_pass`).
- **L1** and **L2** `Sessions` lifecycle and ops: create and fork, the ops gate and parking, interrupts, compaction, retries, `_drain_and_exit`, and comment threads with their live map.
- **G1** to **G3** goals and boards (`load_goals` and its shared view), the nudge walk (`_nudge_fire_list`), the jobs and spend guard. Their checklists are the latch list: the rules that hold a state until a named deciding event.
- **J1** and **J2** `romp-judges`: `run_pass`, the tier calls, `migrate_store`, `apply_close`, `apply_courier`.
- **W1** write routes (`do_POST`); **W2** remotes, PR watches, update and restart, and the kernel-to-kernel contract; **W3** `romp-push` and `romp-logins`.

Shadow starts as soon as the week-2 read-only server passes, on read paths, and runs through week 4.

Exit check: a lab Rust kernel drives a real Claude session through a Python host; a Rust restart mid-turn loses nothing; Python to Rust to Python works on one lab session; one judge pass over the fake CLI writes Python's stores; a Codex session runs through a lab Rust kernel against a real app-server; a postal message makes the round trip.

**If the judges miss parity by day 18,** Rust drives the existing `romp-judge --serve` child through its pass protocol (`plans/judges-process.md`) and the judges port finishes in the tail.

### Week 4: reused suites, shadow, cutover (about 10 lanes, mostly fixing)

Black-box and browser tests run on Rust, failures going to the ledger. Shadow, running since week 3, reaches its three days and its clean final day on the cutover commit. Cutover comes around day 24, after the rollback drill, when every "done" criterion holds. Tests that read `kernel.py` source keep running against the frozen Python tree until the owner retires them.

### The conditions one month needs

- **A Python feature freeze,** drivers included. Without it every change is written twice and the target moves. Fixes to live Python still land, each entered in the ledger and matched in Rust the same day.
- **Pinned versions:** the Claude CLI and SDK and the Codex runtime hold for the month. The SDK is pinned in `romp-sdk-setup` before week 1 and stays installed for the hosts.
- **Frozen host helpers:** `session_host.py` keeps loading the lease helpers from `sdk_backend.py`, so those functions do not change.
- **The owner's daily attention** to the digest; this is why the earlier estimate assumed three lanes.
- **The spend:** up to 15 concurrent lanes plus review subagents for four weeks, a multiple of a normal month. Tests run in CI or on batch machines, never the live kernel's machine.
- **A tail of regressions,** each fixed as found and given a harness case.
- **Scope held to the core.** The CLI, manager, UI and hosts stay; the hosts-off path is dropped; remotes, tunnels, mesh and PR watches may ship switched off and follow in the tail, if the owner agrees.

### The cutover

**The switch.** `bin/romp-serve` picks the kernel path from `ROMP_KERNEL_BIN` but runs it under `$PY` (`exec "$PY" "$KERNEL"`), the interpreter it resolves from the SDK venv, so pointing that variable at a Rust binary would not work. The plan changes `romp-serve` to read `$STATE/kernel-impl`, the way `judges-process` is read, and to exec `romp-kernel-rs` directly when it says `rust`, still resolving `$PY` for the Python hosts and postal.

Cutover is writing `rust` and a normal restart, which hosts survive by design. **Rollback** is writing `python` and restarting. It is safe because every store keeps its format both ways; Python rebuilds the saved documents at its first boot, and the code identity change reloads open pages.

**The first week after.** Python stays installed as the rollback, and the comparator keeps replaying the corpus nightly against both kernels.

The owner watches what only a person can judge: false "needs you" cards, cards moving without new information, lost or doubled messages. A monitoring session reads `/perf` (cycle percentiles, process memory, store size, `judge.passesLost`), the error log and the comparator. It calls a rollback when a trigger fires: a message lost, a session that fails to attach, a stuck card, or a store Python cannot read. Lane owners stay on call, fixing forward within a day.

### The risk register

| Risk | Likelihood | Impact | Early warning | Mitigation |
|---|---|---|---|---|
| The Claude driver | high | high | D1 to D3 unfinished at day 16, or a mid-month CLI release | The day-8 start, the pins, a failing check on unknown control subtypes |
| Session-host adoption (bugs show only under real restarts) | medium | high | The handoff check fails, `fault` frames appear, or `hooks_applied` is false | Daily lab restart drills, mid-turn and mid-permission request |
| Judges parity | medium | medium | Store diffs on tape replays | The `romp-judge --serve` fallback |
| Test leverage | high | medium | T2 finds fewer reusable served tests than planned | The corpus is the main net; white-box tests with public calls become recorded cases |
| A moving target | medium | high | More than a few Python commits a week reaching the ledger | The freeze, and same-day porting of fixes |
| Lost incident knowledge | high | medium | Shadow divergences traced to a dated comment a checklist skipped | Mandatory checklists, checked by each lane's reviewer |
| The serial chain does not compress | medium | high | Week-2 lanes blocked on week-1 crates past day 9 | Interfaces fixed on day one; stubs from T3 and S2 let lanes start early |

### The fallback: a staged path

If the spike misses its gate, or at day 7 the event model is not at parity or the harness cannot produce a clean `/feed` diff, the month does not hold and the calendar becomes a staged path.

It ships the Rust transcript store and event model first, behind the Python kernel and behind a state-file switch like `judges-process`, shared by the kernel and the judges' child. It takes one of two forms:

- a Python extension module written in Rust (built with PyO3, the standard library for calling Rust from Python), which the kernel imports like any module, keeping `event_model`'s Python API; or
- a sidecar, a separate Rust process beside the kernel that hands it finished frames over a local socket.

That captures the cost that grows with history: whole transcripts as Python objects in two processes, and parsing on the interpreter the pusher waits on. The builds (`build_feed`, `build_timeline`, `build_chat`) move next; the drivers, the judges' prompt logic, the restart protocols and their tests stay put.

Python must receive views or finished frames, not freshly converted dictionaries, or the memory win is lost. An extension must also build for the free-threaded interpreter of `plans/free-threading.md`, and it composes with the decode worker of `plans/process-split.md`.

Nothing from week 1 is wasted: the harness, `romp-transcript`, `romp-events` and `romp-state` are the full port's first crates, and later stages add the server, pusher and drivers one at a time, each switchable back.

### First-day checklist

1. Read this plan and the related plans; get the owner's answers on at least freeze dates, scope, pins, packaging and spend.
2. Tag the base commit as the rollback baseline, pin the SDK in `romp-sdk-setup`, and announce the freeze.
3. Land the mechanical test commit (`lab_kernel_cmd()`, the `kernel_blackbox` markers) and the approved harness seams, the kernel-wide clock seam first: one `_now()` reading a harness clock file when a test variable names it, so end-to-end steps run on a shared, stepped clock.
4. Write the crate interfaces (types, traits, exchanged JSON) before any lane starts.
5. Generate the rationale-comment checklists, the T2 inventory and the outside-callers route list with fixed-string markers only.
6. Bring up H1 on `/feed`: Python against Python to zero differences, then a planted deviation caught.
7. Launch the week-1 lanes, each brief naming crate, interface, oracle, checklist and exit check; set up the digest and put the day-5 checkpoint and day-7 gate on the calendar.

## Open questions for the owner

**Calendar and freeze**

1. When does the month start? Do free-threading stages 0 and 1 keep landing (recommended, since Python stays the rollback), matched by the Rust lanes? Is stage 2 shelved once the month is committed?
2. Does the freeze cover the drivers (`sdk_backend.py`, `codex_backend.py`, `session_host.py`)? The plan assumes so.
3. May harness-only seams land during the freeze: a kernel-wide clock seam (one `_now()` reading a harness clock file when a test variable names it, the same in both kernels), `--now` on `romp-event-model --emit`, a builder dump, a live-session map the live kernel publishes for shadow, possibly `/debug/settled`, and optionally prompt-hash logging so shadow can check judge prompts on live data by hash only?
4. Should the mechanical test commit (`lab_kernel_cmd()` for the 153 launch sites, the `kernel_blackbox` markers) land before any Rust lane starts?

**Scope**

5. May cutover ship with remotes, tunnels, mesh and PR watches off, ported in the tail? Is dropping the hosts-off direct-spawn path acceptable?
6. Postal: port it in month one or keep the Python process at cutover? If ported, `rmcp` or a hand-written serde loop? Will the driver ever need in-process MCP servers (`mcp_message`)?
7. Should the session hosts stay Python through the whole migration and move to a Rust host later through the re-exec handoff, which freezes the handoff file format?
8. If the judges miss parity by day 18, is cutover driving the Python `romp-judge --serve` child acceptable, keeping its duplicate transcript cache? Afterward, should judges always run in-process as tasks, retiring the `judges-process` switch after the rollback window, or keep a child option?
9. Should manual Codex approvals stay declined after the port, with real approvals as a separate change?

**Versions and dependencies**

10. May `bin/romp-sdk-setup` pin `claude-agent-sdk` now, before any Rust work, and may the CLI and SDK be pinned for the month? Unpinned, new CLI record types can appear mid-month with no SDK to absorb them, and the hosts' SDK can change the CLI's flags under a pinned Rust driver.
11. Should the Codex app-server protocol crate be a git dependency at the runtime tag (0.169.0) or a vendored copy? Its license needs checking either way, and its exact name at that tag is unconfirmed.
12. WebSocket compression: which library with permessage-deflate is acceptable, or should the kernel's own framing be ported?

**Contracts and parity**

13. Should the age tints, host ages and the timeline's live edge tick in the browser (a small TypeScript change), or must Rust keep patching time-dependent fields on a timer so no UI line changes?
14. Must the harness require byte-identical JSON (via `romp-json`) for every frame family, or only semantic equality for some?
15. Is it acceptable that saved documents are not shared across a rollback, so Python rebuilds them at its first boot after one? Should byte compatibility with Python's document version be a post-cutover goal, given that it would tie the Rust store to Python's internal adapter state?
16. Which divergences may be accepted at cutover rather than fixed in Python, and does a known-Python-bug entry block cutover until the owner signs it off?
17. Who pays for the one-time judge tape recording against a real model on the corpus, on which account and judge engine? Tapes are re-recorded whenever a judge prompt changes on purpose.

**Shadow and cutover**

18. Will the owner approve the hard-coded read-only shadow mode, and does running it beside live state need its own sign-off, given the memory both kernels use together?
19. Is three days of shadow enough, given it exercises read paths only, or should it be a week?
20. Is a new `$STATE/kernel-impl` file, read by `bin/romp-serve`, the right switch (name, default)?
21. The performance targets and the spike gate above are proposals. What are the final numbers?

**Resources and afterward**

22. Will the owner fund up to 15 concurrent lanes plus review subagents for four weeks, and the daily time for the digest?
23. Packaging: prebuilt binaries per platform (Linux and macOS, x86 and arm), a Rust toolchain at install, or both? Today romp installs from a git clone run by the system `python3`.
24. 177 TypeScript and about 240 Python test files read `kernel.py` source text. Who decides when they are retired or re-pointed once `kernel.py` is no longer the running code?

## Appendix: the Claude wire inventory at `bc551f41c`

This is the detail the driver lanes port and the conformance recorder checks. Names refer to the SDK's `_internal/` modules and to `kernel/sdk_backend.py`.

**Launch flags.** `SubprocessCLITransport._build_command` (`_internal/transport/subprocess_cli.py`) always passes `--output-format stream-json --verbose` and, last, `--input-format stream-json`. romp's options add:

- `--permission-mode`, `--model`, `--effort`;
- `--resume=<id>` or `--session-id=<id>`, and `--fork-session`;
- `--settings <file>`, a per-session file from `flag_settings_path` for keys the SDK has no field for;
- `--mcp-config` (postal), `--system-prompt` or `--append-system-prompt`, and `--thinking` options;
- `extra_args` as `--resume-session-at=…` and `--append-subagent-system-prompt …`.

File checkpointing is on; partial messages are off.

**Launch environment.** `connect` copies the environment minus `CLAUDECODE` and sets `CLAUDE_CODE_ENTRYPOINT=sdk-py`, `CLAUDE_AGENT_SDK_VERSION` and `CLAUDE_CODE_SDK_READS_SESSION_STATE=1`. It sets a client-app label (`DERIVED_CLIENT_APP_ENV` or `EXPLICIT_CLIENT_APP_ENV`) and propagates OpenTelemetry trace context (`TRACEPARENT`) when one is active. It then merges `options.env`, to which `_options` adds `ROMP_SID`, `ROMP_SESSION_NAME`, the state tag, the repo's `bin/` on `PATH` and per-launch credential variables. With per-session scopes on, `cli_path` is a wrapper that execs `systemd-run` and then the CLI, keeping its argv and pid.

**Framing.** `_parse_stdout_line` skips blank lines and lines not starting with `{`, and raises on a `{` line that does not parse; a message over `max_buffer_size` is an error (romp sets it via `SDK_MAX_BUFFER`). stderr goes line by line to `SdkSession._on_cli_stderr`. Closing is stdin closed, a grace period for the CLI to flush its transcript, SIGTERM, then SIGKILL.

**Messages in.** `message_parser.parse_message` handles:

- `user` and `assistant`, with blocks `text`, `thinking`, `tool_use`, `tool_result`, `server_tool_use` and `advisor_tool_result`, plus `parent_tool_use_id` for subagents;
- `system`, whose subtypes romp acts on (classified in `SdkBackend._turn_frame`) are `init`, `compact_boundary`, `task_started`, `task_progress`, `task_updated`, `task_notification`, `status`, `api_retry`, `thinking_tokens`, `background_tasks_changed`, `model_refusal_fallback`, `commands_changed` and hook events;
- `result`, with a cumulative `total_cost_usd`, `usage` and turn count;
- `stream_event`, `rate_limit_event` and `conversation_reset`.

`Query._read_messages` first removes `control_request`, `control_response`, `control_cancel_request` and `transcript_mirror`, and consumes `session_state_changed` frames marked `sdk_host_only`.

**Messages out.** A user turn is `{"type":"user","message":{"role":"user","content":…}}` with optional `client_composed` (`stamp_user_message`); everything else is a control request or an answer.

**Control requests from romp** are `{"type":"control_request","request_id":"req_<n>_<hex>","request":{"subtype":…}}`, answered by a `control_response` (`success` or `error`, same `request_id`), with a 60-second default timeout and a longer one for initialize:

- `initialize` registers hooks as `{event: [{matcher, hookCallbackIds, timeout, onFailure}]}`; romp keeps the reply (`hooks_applied`, `hook_on_failure_supported`) as server info (`get_server_info`);
- `interrupt`, `set_permission_mode`, `set_model`, `rewind_files`, `stop_task`, `get_context_usage`, `mcp_status`, `mcp_reconnect` and `mcp_toggle` go through SDK wrappers;
- `set_cwd`, `get_usage`, `apply_flag_settings` and `status` go raw, each with its own reply shape; `set_cwd`'s three outcomes (`ok`, `needs_trust`, `rejected`) are documented above `MOVE_CONTROL_TIMEOUT`.

**Control requests from the CLI.** `Query._handle_control_request` runs each as its own task, and a `control_cancel_request` cancels it with no reply.

- `can_use_tool` goes to `SdkSession._can_use_tool`, answered `{behavior:"allow", updatedInput, updatedPermissions?}` or `{behavior:"deny", message, interrupt?}`.
- `hook_callback` is found by `callback_id`, its reply's `async_` and `continue_` renamed to `async` and `continue` (`_convert_hook_output_for_cli`).
- `mcp_message` serves in-process MCP servers, of which romp has none, so Rust answers it with an error.
