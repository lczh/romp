# A session's billing is a setting that follows the machine default

**Status:** a design line before code (written 2026-10-07, after a day of switching every session between an enterprise login and the API key during an outage; the design was approved by the repository owner before this was written). Two pull requests, cut from main, red first: the spend hold's release (a `fix`, #144), then the billing model (a `feature`).

## Why now

Switching the account a session bills went wrong five ways in one afternoon, across some two hundred sessions:

1. **Two paths for one pick.** A billing pick on a busy session (an open turn, a queue, a compaction, a move, an account hold) was PARKED in the chat's queue as an `/auth …` chip; on a quiet one it was recorded. The Billing menu shows only the recorded pick, so a click on a busy session looked like it had not taken, and the chip looked stale enough to cancel.
2. **A newer parked pick appended** behind the older one instead of replacing it, so two relaunches were queued for one intent.
3. **A deadlock under a spend cap.** The account hold held the session's input, the parked switch sat behind that hold, and the switch that would have got past the cap could never apply. Separately, a session whose last transcript record was a cap error stayed held whatever account it was on, even after the global pause lifted: that arm of the hold has no release but a new prompt, which the hold itself prevents.
4. **Records disagreeing with the running process.** After a hung kernel was killed mid-switch, sessions whose registries said "key" kept running hosted CLIs launched on the login, and nothing noticed.
5. **No way to switch everything.** Every session carried its own pick (spawn seeded one from the machine default), so changing the default moved nobody, and a session could not be set back to follow it.

## The premise, checked in code

- **The park.** `setAuth` reaches `_set_auth_or_park` (kernel.py), which runs `_gate_or_park` → `_ops_gate`: compacting, an open turn, an existing queue, a move (`_moving`) or `_limit_hold` parks `("auth", value)`. The drain's `auth` arm calls `be.set_auth` when the queue reaches it. `_park_op_locked` replaces model/effort/fast/env/cwd in place, not auth.
- **The recorded road already waits.** `SdkBackend.set_auth` writes the reg (`auth`, `authLogin`, `authPending`), records the waiting pick (`relaunchWait`) and calls `request_reconnect(why="billing")`, which since 2026-10-01 waits for `quiet()`: no turn in flight or queued, the CLI not producing, no subagent, no background task, no question waiting on the user. A compaction is a turn (a delivered `/compact` is fed through the feeder, `inflight`; an automatic one runs inside a turn the CLI produces, `_cli_working`), so quiet already excludes it. A MOVE is not: `move()` sends `set_cwd` with no turn open, so a relaunch could tear the CLI down mid-request. The park covered that through `_moving`; with the park gone, quiet must say it. Not through the move's settle arm (`_move_settle_expected`), which a lost turn-less result leaves standing and which a reconnect is one of the events that lower: gated on it, the two would wait on each other.
- **The hold.** `_limit_hold`'s spend arms: the global pause (`retry-paused.json` with reason `spend`) holds every session, and `_api_error(path).spendLimit` holds a session whose last record is a cap error. The pause records `bills` (the capped session's side) and, when it lifts, `liftedAt` (the evidence's time). The engage (`_spend_capped_session(after=liftedAt)`) and the lift (`_auto_resume_retry`, comparing sides through `_bills_login`) honour both; `_limit_hold` honours neither.
- **Which side a session is on.** `_bills_login(row)` reads the CLI's own report (`authLive`) first, then the intent (`auth`). `set_auth` clears `auth_live` at the PICK, so for the whole wait the session reads as already on the new side while the old CLI still serves it.
- **The attach.** `_reconcile_waiting_picks` compares only picks already waiting with the attached CLI's launch record (the host's echo, `cli.launch`). The construction heal clears `authPending` unless billing is in `relaunchWait`. A session whose reg and CLI disagree with nothing waiting is never compared.
- **The default.** `fallback_auth` / `effective_auth` already make a session with no pick of its own follow the machine's explicit default (T380). But spawn seeds every new reg from `sdk-defaults.json` `auth`, and a per-session pick writes that seed while the default is not explicit (`authExplicit`), so in practice every session carries a pick. Forks copy the parent's. The launch record (`launch_values`) records the session's own pick, so an unpicked session's record says nothing about the default it launched on.

## The rules

1. **A billing pick is a session setting.** `setAuth` records it at once (`SdkBackend.set_auth`), always, whatever the session is doing. It is never parked in the chat's queue, so no account hold, compaction, queue or move can stand in front of it. The Billing menu shows it at once, pending while the CLI serving the session was launched on another billing.
2. **It applies at the next moment nothing would be cut**, through #67's relaunch (`request_reconnect` → `want_switch_reconnect` → `quiet()`), with Apply now as the way through. `quiet()` also reads a move's `set_cwd` round trip as work (`_cwd_request_open`, raised and lowered around the request), and the round trip's end carries a standing ask, as a turn's end does.
3. **A newer pick replaces an unapplied one.** The reg holds one billing; the relaunch reads the reg when it launches, so however many picks land while it waits, one relaunch carries the latest. A pick equal to what the serving CLI was launched with clears the pending mark (nothing to apply); the standing ask, if any, still relaunches once, carrying nothing new.
4. **Input goes out on the current account until the switch lands.** A billing preference never holds a conversation (a relaunch can wait hours behind background tasks); romp holds input only when the account cannot serve it, which is the account hold's job.
5. **A session follows the machine default unless the user picks otherwise for it.** The setting has two scopes, the machine and the session, and one writer each: the Billing flyout's session entries write the session's override, its **Set default billing** submenu writes the machine's. A pick never moves the default (the legacy seed, written by a per-session pick while the default was not explicit, is retired). The machine default is the explicit one when set, else Automatic (the helper rule: the key when an apiKeyHelper is configured, else the login), exactly what an unpicked session already followed.
6. **Spawn records no override.** A new session follows the default unless its creator chose a billing for it: the picker's Billing row counts as a choice only when the user clicked it (its seeded selection is the default and sends nothing), `romp new --auth` is a choice, and a fork or comment thread inherits none (it follows the default; its parent's override was a decision about the parent).
7. **A session can be set back to follow the default**: `setAuth` with `value: "default"` clears its override; if the default bills differently from what its CLI was launched on, it goes pending and relaunches like any pick.
8. **Changing the default re-targets every following session.** `set_auth_default` compares each live following session's CLI launch record with the new default; each one launched on something else goes pending and relaunches at its own next quiet moment. Overrides are untouched. Switching everything is one action.
9. **The launch record says what the session was launched to bill.** `launch_values` records the effective billing pick (its own, else the explicit default's side and login, else "" for Automatic): the inputs the launch decides from, never the availability fall or the helper rule's resolution, so a reading that flaps (a settings file mid-rewrite) can never relaunch anything.
10. **On attaching to a surviving CLI, billing is always compared** with the host's launch record (the authoritative source: what the host spawned). A mismatch shows pending and relaunches at the next quiet moment. No record (a host older than the record) cannot be vouched for: said in the log, pending shown, one relaunch at a quiet moment, after which the record exists. Never inferred from a heuristic. The construction heal's billing exception goes: the reconcile decides at the attach.
11. **The spend hold releases on its events.** A session's own cap record holds only while it is newer than (a) the global pause's `liftedAt` (the rule the engage already uses) and (b) the moment the session's CLI came up on a new billing (`authSwitchedAt`). The global pause holds only sessions on the billing side it recorded (`bills`), through ONE helper `_auto_resume_retry` shares. The engage reads the same record rule, so a record from the account a session left cannot re-engage the pause on the account it moved to.
12. **The landing is an event, not the pick.** `authSwitchedAt` is stamped where the reconcile finds a waiting billing pick carried by the CLI the session just connected to: now, for a launch made here; that CLI's spawn time, for one an earlier kernel made. Until then the old account's CLI is still the one serving, so the session's side for the pause reads its CLI's own report (`authLive`, no longer cleared at the pick); the landing clears that report and its persisted twin (`apiKeyAuth`), so the side reads the new launch's intent until the new CLI's init confirms it. A cap error on the new account is a newer record and holds again. A session with no CLI running (dormant) has nothing serving it, so its pick clears the report at once, as today.

## Migration

- **Parked `auth` ops** in `pending-ops.json` leave the queue on load; the latest per session becomes a recorded pick (`set_auth`) once the SDK backend is built, logged per session. A session the SDK backend does not own is dropped with a line. The rest of the queue keeps its order.
- **Existing per-session picks stay overrides.** Clearing them is the user's call, made with rule 7's op.
- **The legacy seed** (`sdk-defaults.json` `auth` without `authExplicit`) is read by nothing; an explicit default stays as it is. Nothing is rewritten.
- **`_declared_auth`** (the box's expected landing for an unpicked session) reads the explicit default, else `ROMP_EXPECTED_AUTH`, since the seed no longer means "the user has picked by hand".

## What the user sees

The tab menu's Billing row says where the session's billing comes from: `Machine default · API key` for a session that follows it, the billing alone for an override, and the pending line while a switch waits (`applies after 2 background tasks`, `applying…`). Its flyout lists **Machine default · …** first, then the billings this machine can apply, the current one check-marked, then Apply now while a switch waits, then **Set default billing**. No `/auth` chip appears in the chat, parked or acknowledged: billing is a setting, shown where settings are, and a default change that re-targets a hundred sessions does not write into a hundred chats. The tab's hover row appends `· machine default` to a following session's billing.

## The events, in one table

| Move | Keyed on |
|---|---|
| A pick is recorded and shown | the `setAuth` op |
| A pick goes pending | the serving CLI's launch record differing from the session's effective billing (at the pick, at a default change, at an attach) |
| The relaunch | `quiet()` turning true: a turn's result, a subagent's stop, a background task's end, a move's `set_cwd` answer (the 30 s tick behind them) |
| Pending clears | the connect whose CLI's launch record carries the billing |
| The cap hold on a session's own record lets go | the pause's `liftedAt`, or `authSwitchedAt`, passing the record's time |
| The global pause holds a session | the pause's `bills` matching the side of the CLI serving it |

## Tests (red first at main where behaviour changes)

- A pick on a busy session (open turn, queue, spend hold) is recorded at once and parks nothing.
- A newer pick replaces an unapplied one; one relaunch carries the latest.
- Input sent after a pick goes out on the current CLI.
- A cap record older than the pause's lift, or older than a landed switch, no longer holds; a newer one does. The global pause holds only its billing side; the engage skips a record older than its session's landing.
- A default change re-targets following sessions and leaves overrides; `value: "default"` returns a session to following.
- An attach whose launch record names other billing shows pending and relaunches when quiet; one with no record says it cannot vouch.
- A move's `set_cwd` round trip is not quiet and its end carries the standing ask; a stale settle arm alone holds nothing.
- Parked auth ops migrate on load, the latest per session, the rest of the queue intact.
- Spawn and fork mint no override; the picker sends no billing unless clicked.

## Follow-up after review (2026-10-08)

An independent review of #144 and #146 found these, fixed in one `fix` pull request:

1. **Relaunches share the launch budget.** A relaunch a standing ask makes takes one of the machine-wide launch slots boot's resumes and the other spawns take (`_LaunchSlots`, `BOOT_RESUME_CONCURRENCY`), never blocking: the session's loop asks, the old CLI keeps serving while it waits in a FIFO, and a release offers the slot to the first waiter (one that went busy passes it on). A change of default that re-targets every follower, or the 30 s tick carrying every no-record attach, relaunches three at a time. The slot is held from the take to the relaunched CLI's handshake, and given back when the relaunch stands down, the connect fails or the thread ends. A gesture that asks for its own relaunch (Restart, Apply now, a rewind) is not throttled.
2. **Unknown is not a mismatch.** With no launch record on hand (between a teardown and the next connect, an attach in progress, a connect failing) a pick decides nothing; the next connect's reconcile compares billing whatever happens.
3. **A CLI that ends lands the waiting switch.** When the CLI serving a session ends with a switch waiting (never a detach, whose host keeps it), the pending mark and waiting record go, and the landing is stamped with the dead CLI's report cleared: the session's next launch carries the billing, so the dormant row no longer reads the dead CLI's side as pending under a spend pause.
4. **Every landing clears the replaced CLI's report**, an attach's included.
5. **The landing is ordered exactly**: the record's millisecond stamp (`tExact`) against the landing's sub-second clock; `liftedAt` stays in the CLI's whole seconds against `t`, as its evidence is stamped.
6. **Serving is the host lease's word**, not only the kernel's session object: a pick on a session a live host still serves lands nothing before the attach compares.
7. The landing is decided from the replaced CLI's billing (`_served_billing`, which outlives the teardown), so a pick back to what ran lands nothing; a pick back to what runs drops a standing relaunch that picks alone asked for (`PICK_RELAUNCH_WHYS`; a pick's reason never overwrites another), and `_limit_hold` reads the live row at most once.

## Roads not taken

- **Keep the park, and let the hold release a parked pick.** A second road for one pick, with its own ordering and its own chip; the recorded road already waits correctly since #67.
- **Hold input while a switch waits.** A relaunch can wait hours behind background tasks; a conversation must not stall on a preference.
- **Release the cap hold at the pick.** The old account's CLI serves until the relaunch lands; releasing at the pick sends the held input into the cap it was held from.
- **Infer a surviving CLI's billing from its environment or its last init.** The host's launch record is the source; with none, romp says it cannot vouch and relaunches once.
- **Promote the legacy seed to the explicit default.** It would re-target every session that followed Automatic on such a box at deploy, from a value the menus never showed as the default.
- **A time window after the switch** inside which cap records are ignored: a clock where the event (the landing) is known.
