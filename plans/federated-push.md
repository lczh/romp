# Federated push: one subscription buzzes for every linked kernel

Status: the PULL direction is the design of record (2026-10-10). It replaces push-from-origin
(2026-08-08), which stays only as a transition leg with a written removal condition (below).

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
notified column) and the turn tick (`_turn_notify_tick`, a turn the user opened ending). The ring is
keyed `(bootId, seq)`, the boot id being this kernel life's `_BOOT_ID`, and holds the newest 256.

`GET /push/events?after=<bootId>:<seq>&wait=<s>` serves it, behind the serve token like every route a
row's holder calls. It is a long-poll in the postal `/peer-exchange` shape: with nothing newer than
the cursor it parks on a condition variable, and the first new candidate releases every parked
request. No polling cadence anywhere. The answer is `{boot, next, events[, stale]}`, with
`{reset, skipped}` when it resets (below). `next` is the newest seq examined, the puller's next cursor.

The origin advertises the capability in `/version` as `pushPull: 1`.

### The hub's half

The hub runs one puller thread per TRUSTED row that has a port and a token and whose `/version`
advertises `pushPull` (`_push_pull_reconcile`, at the end of each supervisor pass, which also wakes a
puller waiting out its retry backoff when its row answered that pass). It uses the row's own port and
token, the credentials the dashboard relay already uses, so no new credential and no new trust
surface. A call that does not answer backs off exponentially (capped at 30 s, the postal dialer's
ladder).

The cursor is kept on disk per row (`push-pull-cursors.json`, 0600), so a hub restart resumes where
it left off. The events pass the hub's gate BEFORE the cursor moves on disk: a crash between the two
re-offers them, and the origin re-validates them, rather than losing them.

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
  override standing in for a local one. A card buzzes when its bell is `"on"`, or `"default"` with
  the hub's master on (a card's override beats the master, as on a local card). A turn end needs the
  hub's master and turn switch on, and its session's bell `"on"` or `"default"`. A held batch is said
  in one line, as relayed events were.
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

- the origin's setters (`_set_notify_card`, `_set_notify_session`) store a click only when it differs
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
- **Local bells are unchanged.** A click on a kernel's own dashboard keeps the delete-if-default rule,
  so a single machine behaves exactly as before. That is the choice made here: the as-given path is
  taken only for a linked kernel's bell, decided in the browser by the host prefix.
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

### Restarts and gaps

The hub resumes at the newest event, skipping the missed ones (the user's explicit choice), in three
cases: the ring overflowed past its cursor (`overflow`), the origin restarted, so a new boot id
(`restarted`), or the hub holds no cursor (`no-cursor`: it lost the file, or this is a new row). The
answer carries the reason and how many events it skipped, and the hub says so in one loud line:

    romp: web push: events from 'TESTHOST' resume at its newest — its kernel restarted since this
    kernel last heard from it; 2 event(s) it fired meanwhile skipped

A hub restart WITH its cursor resumes from it, and the events fired meanwhile are served if they are
still current.

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
  restart with its cursor (the missed event served once), a hub restart without it (resumes at the
  newest, says so, delivers nothing old) and an origin restart (resumes at the newest, the next turn
  end delivered once), and nothing twice throughout.
  The user's bell scenario runs there too, over the hub's relay socket: with the hub's master on and
  the origin's never set, the origin's card carries no override, a mute clicked as given is stored and
  the next turn end stays on the origin, and an arm clicked as given buzzes.
- `tests/test_push_pull.py`: the ring under each detector, the explicit-bell gate, the latent claim
  bug, the serve (long-poll release, each reset, staleness, the token gate), the hub's gate, the
  puller (reset line, cursor on disk, dedupe, refusal), the transition (the forward skips pullers,
  the relay-ignore, the `/version` poll, an event offered both ways reaching the devices once), and
  the bells (as-given storage, local delete-if-default unchanged, the payload fields, the switches
  frame, the gate's local rules).
- `ui/webview/bell-follow.test.ts` and `feed-render-incremental.test.ts`: the paint rule, federation
  keeping only the local switches frame, the timeline twin and its pending reconcile, and the feed's
  card bell in the user's scenario, an older kernel's bell painted as reported with its hover note.
