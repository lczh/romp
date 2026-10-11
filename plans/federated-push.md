# Federated push: one subscription buzzes for every linked kernel

Status: the PULL direction is the design of record (2026-10-10). It replaces push-from-origin
(2026-08-08), which stays only as a transition leg with a written removal condition (below). The
follow-ups from its review (2026-10-11) are folded in: who opened a turn is decided when it opens, an
explicit session bell beats the switches, one policy for every gap ("never late, never silent"), a
per-batch cap, and an event is marked passed only once the gate has decided it.

The ask (the user 2026-08-08, minutes after installing the home-screen app): with kernels linked,
having to think about WHICH kernel a notification comes from is exactly the mechanics romp exists
to hide. A device subscribed at one kernel should get the bell events of every kernel connected to
it. The phone is installed against the always-on box, the laptop's sessions do the day's work, and
the laptop's "needs you" must still buzz the pocket.

**The invariant: push scope = dashboard scope.** The dashboard you subscribed from shows every
linked host's sessions merged together, and the bell you tapped there means "notify me about all of
this", not "about the slice this kernel happens to own". The hosts a dashboard shows are exactly the
rows its kernel holds: `_remote_ws` relays a host's socket only through a row's port and token, and
404s without one. So the hosts whose events reach a device are exactly the rows held by the kernel
the device subscribed at, minus the ones the user has not marked trusted.

## Why push-from-origin failed

The first design (2026-08-08) forwarded at the event source: when a kernel's bell fired, its
`_push_forward` POSTed the event to `/push/relay` on every trusted row in its OWN `_remotes`, and
the receiver mirrored it to its own devices. Two things were wrong with that.

1. **The topology is one-way.** With ssh-attach (the default way to link a machine), only the
   attacher holds a row: the hub dialed the laptop, the hub holds the laptop's port and token, and
   the laptop holds nothing for the hub. The laptop's `_push_forward` had nobody to send to, so its
   turn ends and needs-you cards never reached the phone subscribed at the hub. Only the two-way
   arrangements (check-in, "Share my sessions") gave the origin a row back, and those are the
   exception.
2. **The origin gated on its own switches.** The forward ran only for events that buzzed on the
   ORIGIN: its master, its turn switch and its bells. On an attached machine the master and turn
   switch are absent (nobody opens that machine's popover; the user toggles the hub's), so they read
   off and nothing was forwarded even where a row existed. A latent bug rode on the same gate: when
   the origin's own turn push claimed the turn end's one buzz (`_buzz_claim`), the needs-you card
   that the same turn end raised yielded locally, and since only what buzzed locally was forwarded,
   the hub never heard that needs-you at all.

## The map: every other cross-machine flow starts at the row holder

Every cross-machine flow in romp except push is started by the kernel that holds the row and reads
toward itself:

- the dashboard relay (`/remote/<host>/ws`, `_remote_ws`): the hub splices its browser's socket to
  the row's port with the row's token;
- the supervisor's polls (`_tunnel_supervisor`): `/sessions`, `/version`, `/usage`,
  `/api-health/frame`, the tag views, each through the row;
- settings converge (`_converge_peer_settings`): the hub reads the peer's `/version` and pushes its
  newer picks through the peer's gesture route, both legs started by the row holder;
- auto-update (`_maybe_auto_push`, `_auto_pull_remote`): started by the supervisor's pass over a row;
- the postal peer exchange: each bus dials the peers its kernel told it about (the kernel's rows),
  and the dialed side long-polls (`/peer-exchange`).

Push was the only flow the ORIGIN started. The fix is to turn it round like the rest.

## The design: the row holder pulls

### The origin's half

Every event the origin DETECTS lands in an in-memory ring as a candidate. Detection stays where it
was, reading the origin's own stores: the card diff (`_feed_notifications_diff`, a card entering a
notified column) and the turn tick (`_turn_notify_tick`, a turn the user opened ending; "Who opened
the turn" below says how that is known). The ring is
keyed `(bootId, seq)`, the boot id being this kernel life's `_BOOT_ID`, and holds the newest 256.

`GET /push/events?after=<bootId>:<seq>&wait=<s>` serves it, behind the serve token like every route a
row's holder calls. It is a long-poll in the postal `/peer-exchange` shape: with nothing newer than
the cursor it parks on a condition variable, and the first new candidate releases every parked
request. No polling cadence anywhere. The answer is `{boot, next, events[, stale]}`, with
`{reset, skipped}` when it resets (below). `next` is the newest seq examined, the puller's next cursor.

The origin advertises the capability in `/version` as `pushPull: 2` (protocol 2, the gap policy's
fields: below).

### The hub's half

The hub runs one puller thread per TRUSTED row that has a port and a token and whose `/version`
advertises `pushPull` (`_push_pull_reconcile`, at the end of each supervisor pass, which also wakes a
puller waiting out its retry backoff when its row answered that pass). It uses the row's own port and
token, the credentials the dashboard relay already uses, so no new credential and no new trust
surface. A call that does not answer backs off exponentially (capped at 30 s, the postal dialer's
ladder).

The cursor is kept on disk per row (`push-pull-cursors.json`, 0600), so a hub restart resumes where
it left off. Each event is marked passed (the `(row, bootId, seq)` backstop) only once the gate, or a
gap's count, has decided it, and the cursor moves on disk after every event in the answer is decided:
a gate that raises part-way (the puller thread dies, the supervisor's next pass restarts it) re-offers
the undecided events rather than skipping them, and the backstop keeps the decided ones to once. A
buzz claim filed for a push that then raised is put back, so the re-offer does not yield to itself.
(Until 2026-10-11 the events were marked passed before the gate ran, so a gate that raised lost them.)

Sids are prefixed with the HUB's own name for the row (`TESTHOST:<sid>`), the merged dashboard's tab
address, never the name the origin declares for itself. `/push/relay` prefixed and judged trust by
the self-declared name, which broke when the hub knew the host under a different ssh name.

### The gating split

- **The origin applies only the explicit bells.** The card and session bells live on the origin (the
  merged dashboard writes them by sid to the kernel that owns the session). An explicit mute (the
  card's bell off, else its session's) drops the event before it is a candidate. An explicit arm
  rides as `bell: "on"`, and no override rides as `bell: "default"`. The origin's master and turn
  switch govern the origin's own devices only, never the candidates.
- **The hub applies its own switches**, by the rules its own sessions follow, with the origin's
  override standing in for a local one. An explicit `"on"` beats the switches, for a card and for a
  turn end alike (the user 2026-10-11: the turn-finished switch is the default, and a session set to
  "Notify me" still notifies with it off). `"default"` follows them: a card the hub's master, a turn
  end the master AND the turn switch. An explicit mute never leaves its origin. A held batch is said
  in one line, as relayed events were.
- **At most 16 notifications per answer** (`PUSH_PULL_BATCH_CAP`, the old relay's 16). Past the cap
  the rest are said in ONE more notification ("TESTHOST · 7 more: 3 sessions finished turns, 4 need
  you", titled "Romp: more notifications") and one loud log line naming how many were collapsed, so an
  unexpected burst (a judge pass moving twenty cards at once, a bug) can never machine-gun the phone,
  and nothing is dropped silently either. The old relay simply dropped everything past 16. The note
  wears its own tag (kind `more`), so it never replaces a gap's summary on the lock screen.
- **The hub owns the one-buzz-per-turn-end rule**, keyed on its own row name, the sid and the
  origin's turn-end key: `_buzz_claim` on the prefixed sid, with the turn key every candidate
  carries. A turn end and the card it raises buzz once between them, whichever arrives first; a card
  that yields replaces the turn notification quietly (the per-session tag). This removes the latent
  bug above, because the origin's own claim no longer decides what leaves it.

**Announced when offered** (a change the pull made to the once-per-(card, column) rule, not in the
original sketch). An announcement is the card ENTERING the column, offered to the origin's devices
(gated by its switches) and to every puller (gated by theirs). So the card diff marks a candidate
announced whatever the origin's own switches say; otherwise a card bouncing in and out of a column on
an origin whose master is off would be re-offered, and buzz the hub, at every bounce. The cost: a
card that entered while the origin's master was off and comes back to the same column with nothing of
the user's in between stays quiet if that master is later turned on, like the first boot's seed (it
was on the board all along). Before the pull, an unarmed entry left no mark.

### Bells follow the dashboard

The gating split only holds if the bells the user sees and clicks are the ones the hub's gate reads.
Two things in the bell store stood in the way (found while building the pull, 2026-10-10, and decided
by the user the same day):

- the origin's setters (`_set_notify_card`, `_set_notify_session`) stored a click only when it differed
  from the ORIGIN's master, so a mute clicked while that master is off (the usual case: nobody opens
  an attached machine's own popover) was never stored, the event still carried `"default"`, and the
  hub buzzed;
- the payloads carried each bell's EFFECTIVE value, resolved against the origin's master, so the
  merged dashboard showed an attached machine's bells off while the phone was buzzed for them.

So a linked kernel's bells now follow the dashboard they are shown on:

- **The origin sends the raw override** beside the effective value: `notifyRaw` (true, false or null
  for none) on every feed card (the card's override, else its session's), on every chat session frame
  and chat tail (the session's override; the chat view key includes it, so an override-only change
  still sends a tail), and on every timeline lane.
- **The hub's switches reach the panes** in a `notifySwitches` frame (`{all, turns}`), sent to each
  pane at its ready (after `caps`) and to every ready pane when a switch flips. The browser's
  federation keeps only the LOCAL kernel's frame, as it keeps only the local `caps`.
- **The browser paints a linked bell against the hub's master** (`ui/webview/bell-follow.ts`; the
  timeline's plain-JS twin is `laneBell` in `ui/romp-timeline-view.js`): an override stands, and no
  override follows the hub's master. The feed card bell, the chat tab menu's row, the bell command and
  the timeline lane gear all read it, and their pending guards confirm a click on what the bell
  paints, since the origin's own effective value can agree with a click before the click has landed.
- **A click on a linked bell goes as given** (`asGiven: true` on `cardNotify` and `setSessionFlag`).
  The origin stores it exactly as sent; a null value as given clears the override, the deliberate
  "back to default" (the protocol carries it; no surface offers it yet).
- **A local card bell is unchanged.** A click on a kernel's own dashboard keeps the delete-if-default
  rule for a CARD's bell. The as-given path is taken for a linked kernel's card bell, decided in the
  browser by the host prefix.
- **A session bell is always the user's explicit choice** (2026-10-11, the user's decision). Every
  click on a session's bell (the tab menu's row, the bell command, the timeline lane gear, the
  Obsidian `/flag` route) is stored exactly as clicked, local or linked: with the hub's master on and
  its turn switch off, "Notify me" used to restate the master and be deleted, so no session could be
  opted in to its turn ends. The row now says whether the bell is the session's own setting or the
  default and what that means ("your setting: when a turn you started ends, or it blocks on you or
  completes", "default: when it blocks on you or completes", and so on: `sessionBell` in
  `bell-follow.ts`, `laneBell` its timeline twin). The row reads on when the session notifies for
  everything a session can (its own setting on, or no setting with the master and the turn switch
  both on), and a click stores the opposite. A refusal frame carries the setting too (`notifyRaw` on
  `settingRefused` and on `/flag`'s refusal body), so a refused click repaints from it. No surface yet
  offers "back to the default" (a null sent as given clears it; the protocol carries it).
- **An older linked kernel falls back visibly.** A kernel that sends no `notifyRaw` serves no pull
  either, and keeps its own rules (its legacy relay gates on its switches). Its bells paint as it
  reports them, its clicks go out as before, and the bell's hover (the tab menu row, the lane gear
  row) says it follows that machine's own switch because that machine runs an older romp. Nothing is
  guessed.

### Staleness

The origin re-validates each event when it serves it, with no time limit (`_push_event_current`):

- a card event is served only while the card still sits in the column it entered (the notify
  snapshot, the last feed build's view) and the user has not acted on it since (the override
  journal, the record `_notify_user_acted_since` already reads);
- a turn event is served only while that turn end is still the session's latest (`_turn_end_key`);
- either is dropped if an explicit mute was set on the origin since.

A stale event is skipped, counted in `stale`, and the cursor passes it.

### Gaps: never late, never silent

The first cut had two rules for a gap, and they disagreed (the user, 2026-10-11): a lost cursor or an
origin restart skipped silently to the newest event, while a hub restart with its cursor delivered
the missed events late, one by one, once the origin judged them still current. One policy replaces
both, for every gap this kernel knows of:

- **Continuous operation:** each event is delivered as it arrives (as built).
- **After a gap: nothing late, one summary.** None of the missed events is delivered one by one.
  This kernel counts those that would have passed its gate (the switches and the explicit bells,
  including an explicit "on" beating the switches) and says them in ONE notification per gap per row,
  titled "Romp: missed notifications", with no session to land on, so a tap opens the dashboard. The
  count includes events the origin no longer calls current (a newer turn end since, a card that has
  moved on): the summary says what would have buzzed had this kernel been listening. An explicit mute
  set since is still dropped at the origin.
- **Nothing would have passed: no buzz**, only a log line.
- **The count cannot be known** (the row's kernel restarted and its ring went with it, or the
  backlog overflowed past the cursor): the summary says notifications may have been missed, with no
  number, and only when something the user's switches and bells allow could have been among the lost
  events (`_push_gap_could_pass`), decided from facts the hub holds, never assumed: the row's kernel
  holds an explicit "on" bell (`bellsOn`), or turn ends pass by default (this kernel's master AND its
  turn switch on; the turn switch alone passes nothing), or cards pass by default (this master on)
  while the row's kernel detects card events at all (`cardsOn`: its Task tracking switch; off, its
  feed is not built and no card enters a notified column). Otherwise only a log line. (The first cut
  said it whenever this kernel's master was on, so with the turn switch off, no bell set and Task
  tracking off over there, every restart of the linked kernel, which is every deploy, buzzed for
  nothing. The review reproduced it, 2026-10-11.)
- **A summary not yet seen takes in the next gaps quietly.** Once a summary has gone out for a row,
  the next gaps from that row fold into it: the same notification (its tag) is replaced with the
  merged counts and reasons ("TESTHOST restarted 3 times · …"), without a sound, so a crash-looping
  kernel buzzes once, not at every restart. The fold ends on one of two events: the user brings a page
  holding a device subscription forward (its `GET /push/pending`, asked on every load, show and focus),
  or the row delivers a notification normally, outside any gap. The next gap after either sounds.
- **One summary per gap, however the link flaps.** A gap opens on the event that proves it: a failed
  call (`unreachable`), a reset answer (`restarted`, `overflow`), this kernel's first answer of a life
  while holding a cursor (`hub`: it restarted). It stays open across further failures and closes only
  on an answer whose request found the cursor at the row's newest (`caughtUp`, protocol 2: the link
  held for a whole long-poll). That answer's own events fired while the puller listened, and are
  delivered one by one after the summary. No timer anywhere: every step is an answer or a failure.

The summary is said in the user's terms, naming the machine by this kernel's name for the row and the
reason it actually knows:

    TESTHOST was unreachable for 38 min · 3 sessions finished turns, 1 needs you
    TESTHOST was unreachable for 2 h 5 min · notifications may have been missed
    TESTHOST restarted · notifications may have been missed
    TESTHOST restarted 3 times · notifications may have been missed
    boxa restarted · TESTHOST: 1 session finished a turn
    boxa restarted · notifications from TESTHOST may have been missed

(`boxa` is the hub's own name, `_self_host`. The outage is said as a duration, from the row's last
answer to its first answer back, never as clock times: the kernel's clock zone is not the user's,
and the first cut printed UTC times to a phone in another zone.)

**Protocol 2** carries what the policy needs. A puller that asks `v=2` is told `caughtUp` (the
request found its cursor at this life's newest), `bellsOn` and `cardsOn`, and is served every event
past its cursor, a stale one flagged `current: false` instead of dropped. A v1 puller is served
exactly as before. An origin that does not say `bellsOn` or `cardsOn` (a v1 origin mid-update, or the
first protocol-2 build for `cardsOn`) is assumed to have them on, and a v1 origin's gap closes on an
empty-handed, reset-free answer, which proves the same as `caughtUp`.

**No record versus an unreadable one.** A hub with no cursor for a row in a readable record (or no
record file at all) treats the row as new to it: it starts at the newest, says so in the log, and
summarizes nothing. That is the case at the first boot of a build that pulls, and summarizing there
would interrupt the user for events that were never theirs to miss. A record that exists but cannot
be read is a gap with no number, said once per row.

**The hub's own sessions follow the same rule across its restart.** `_turn_notify_tick`'s memo of each
live session's last turn-end key is written to `turn-notify-memo.json` whenever it moves (keys move at
turn ends, not per cycle). The first tick of a life reads the previous life's copy: a session whose
newest end of a turn a HUMAN opened (`lastHumanStopAt`, which the Stop hook stamps beside
`lastStopAt`) is newer than the key the previous life last saw finished a turn while no kernel was
watching. Those are said in one summary ("boxa restarted · 2 sessions finished turns"), counting only
the sessions whose bell would have let the buzz through, and never buzzed one by one. Before, a
session's first sighting in a life was a silent baseline, so they vanished. With no memo on disk (the
first boot of this build) the first tick is a silent baseline, said in the log.

### Who opened the turn

Only a turn a human opened may produce a turn-end notification. The review found the label wrong for
half of them: on the big machine, 234 of 461 turn ends labelled "human" in one day had no text fed by
romp. The label was set at the feeder's pop and never reset, and the mark meant to catch a turn the
CLI opened itself (a user record streamed with an injected origin stamp) almost never fired, because
the CLI does not stream the prompt record of a turn it opens. So a turn opened by a background task's
notification, a peer's message, a scheduled prompt, or a message the CLI ran as its own turn after the
one it was fed into ended, kept the previous turn's "human".

The opener is now decided when the turn OPENS, from the event that opened it
(`SdkSession._open_turn_as`):

- the feeder's pop from idle: the text it fed says who (the human's words, or romp's marker-carrying
  nudge, notice or relayed mail);
- a turn frame the CLI streams while nothing romp fed is in flight (`_on_message`, where the CLI's own
  turn is already counted): the CLI opened it, so "injected", unless the turn is the drain of a text
  romp fed (its lifecycle `started` after its turn ended, or the turn-boundary road), whose speaker it
  takes;
- a human text the CLI takes into a turn already running (its `started` while that turn runs, or its
  landed record on a CLI without lifecycle frames) claims that turn; a text fed mid-turn no longer
  claims the turn at its pop, since the CLI may run it as its own turn instead.

The opener is persisted in the registry (`turnOpener`) at each open, so a session object born mid-turn
(a kernel restart under a hosted CLI) reads it back. The Stop hook stamps `lastTurnOpener` beside
`lastStopAt` ("unknown" when no open was ever seen), and the kernel buzzes only for "human": anything
else on an SDK registry is silent. A session with no SDK registry is a Codex session, whose every turn
is a send through romp; its turn ends still read as the human's.

One-time cost at the deploy that brought this in: a turn already running when the kernel restarted
onto it has no saved opener (the registry's `turnOpener` did not exist yet), so its end is stamped
"unknown" and stays silent, even when the user had started it. From the next turn on every turn's
opener is recorded at its open. Accepted (the review, 2026-10-11): one missed buzz per session busy
at that restart, against guessing.

Checked against the big machine's own records, read-only (the turn ledger and the transcripts' queue
and origin records, no message text): of 237 "human" turn ends in the 24 hours before the fix with no
text fed by romp, the transcripts show 222 opened by a background task's notification and 15 by a
peer's message. The new rule labels 236 of them as opened by the CLI and one as the human's, which is
right: a human's message joined that turn. Of the 287 with a fed text, it keeps 282 as the human's and
labels 5 as the CLI's: notification turns that ended before the CLI took the human's message, which it
then answered in the next turn (labelled the human's).

## The transition: one mechanism per pair, and its removal condition

Old and new kernels meet during an update, so both legs exist for now, and each pair uses exactly one:

- The hub pulls from any origin that advertises `pushPull`, and ignores `/push/relay` from an origin
  it pulls from. The origin is mapped onto the row by the row's key or the self-name its `/version`
  taught the row (`_peer_key`). The ignore is said once per origin.
- The origin keeps `_push_forward` only toward rows whose `/version` does not advertise the pull, and
  stops for each as soon as the supervisor's poll of that row sees `pushPull` (the row's
  `push_pull`).
- The hub's gate passes each pulled event at most once by `(row, bootId, seq)`, a backstop beside the
  cursor.

**Removal condition:** once auto-update has converged every linked kernel on a build that serves
`/push/events`, delete `_push_forward`, its two call sites, the `/push/relay` route and its tests
(`tests/test_kernel_webpush_federation.py`'s forward and relay classes), and the hub's relay-ignore.

## What stays from the first design

- **A pulled event omits `badge`.** The origin's needs-you count is not the hub's, and the worker
  applies `setAppBadge` only to a numeric value, so a pulled event never clears a real local count.
- **The title passes through as the origin composed it** (`_notify_title`: the session name alone).
  The host rides only the sid prefix and the payload's `host`.
- **`_reveal_msg` trusts the browser for prefixed sids.** A `host:sid` focus is handed to the chat
  pane as-is; the local session list would otherwise mint a wrong `confirmRevive`.
- **Trust is the user's line.** Only a host the user marked trusted is pulled; the gate re-reads the
  trust at delivery, so a row lowered while a call was parked buzzes nothing.

## Known limits and follow-ups

- **Card events are dormant while Task tracking is off.** The card diff runs on the feed's cards, and
  with all background judging off (the Task tracking switch) no card enters a notified column, so only
  turn ends travel. When it is on, card events reach the phone under the hub's master and the explicit
  bells, by the gating split above.
- **A card that entered a notified column while its kernel was down is still announced late**, at the
  first build after the restart (the once-per-(card, column) rule's "it entered while no kernel was
  watching"). The gap policy covers turn ends across a restart (the memo) but not this; folding it in
  needs an event that says the restart's catch-up is complete, since sessions revive one at a time.
- **A send romp injects into a Codex session reads as the human's**: Codex has no Stop-hook stamp, and
  every Codex turn is a send through romp.

- **Card buzzes need an open socket on the origin.** The card diff runs on the origin's feed builds,
  and the pusher builds the feed only while a client rides it (any dashboard socket to that kernel,
  the hub's relay included). An origin no dashboard is looking at still detects turn ends, which the
  turn tick reads every cycle, but not card moves. This predates the pull, holds for a single kernel
  too, and is left as it is for now: a headless feed build costs a build per cycle on every kernel.
- **A merged badge** (optional, not built): each long-poll answer could carry the origin's needs-you
  count, for an icon count across every linked kernel.
- **Cold-start precision for remote sids.** A tap that boots the app delivers its parked reveal on
  the chat pane's `ready`, possibly before federation has merged that host's tabs. The event-based
  fix, when it earns its keep, is to park the prefixed focus browser-side until those tabs arrive.

## Tests

- `tests/test_push_pull_kernels.py`: two real kernels where only the hub holds a row (the check-in
  shape), a loopback push service, the hub's ledger as the record. A turn end and a needs-you card on
  the origin each reach the hub's gate exactly once, under the hub's name for the row. Then a hub
  restart with its cursor (the missed turn end is not delivered late; one summary says it), a hub
  restart with no record file (resumes at the newest, says so, delivers nothing old) and an origin
  restart (one "may have been missed" summary, then the next turn end delivered once), and nothing
  twice throughout.
  The user's bell scenario runs there too, over the hub's relay socket: with the hub's master on and
  the origin's never set, the origin's card carries no override, a mute clicked as given is stored and
  the next turn end stays on the origin, and an arm clicked as given buzzes.
- `tests/test_push_pull.py`: the ring under each detector, the explicit-bell gate, the latent claim
  bug, the serve (long-poll release, each reset, staleness, the token gate, protocol 2's fields), the
  hub's gate (an explicit "on" beating both switches, the cap's one collapsed notification and log
  line), the puller (reset line, cursor on disk, dedupe, refusal, an event marked passed only once
  decided), the gap policy (an outage, a flapping link, nothing that would have passed, the row's
  restart, an overflow, this kernel's restart with and without its record, a v1 row), the hub's own
  sessions across its restart, the transition, and the bells (as-given storage, a session click
  stored as clicked, the payload fields, the switches frame, a refusal's setting, the gate's rules).
- `tests/test_kernel_notify_popover.py`: who opened the turn, decided at its open (a turn the CLI
  opens itself, a message it runs as its own turn, a human text spliced in or drained, a session born
  mid-turn, no open seen) and end to end through the Stop hook to the kernel's tick; an explicit
  session "on" beating both switches.
- `ui/webview/bell-follow.test.ts`, `flag-pending.test.ts`, `ui/timeline-flags.test.ts`,
  `ui/timeline-views-ack.test.ts` and `feed-render-incremental.test.ts`: the paint rule, the session
  bell's own setting versus the default and its sub-lines, the pending guard confirming on the setting,
  federation keeping only the local switches frame, the timeline twin, and the feed's card bell in the
  user's scenario, an older kernel's bell painted as reported with its hover note.
