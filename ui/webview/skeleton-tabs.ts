// Skeleton tabs (2026-09-07): the client half of the reconnect diet. When a pane redials after its socket
// died (a laptop lid, a long freeze, a network change), the kernel used to serve the fresh socket as a client
// that holds nothing: seventeen full `session` frames, ~9 MB, for ONE tab on screen. Now a redial declares
// itself (`reconnect=1`), the kernel sends the tab strip with a `skeleton` list (every background tab it
// is withholding, cheapest first), the ACTIVE tab in full, and a ~400 B `status` frame per withheld tab. This
// module owns the client's copy of that set — which tabs are resting, what the kernel last said about each,
// and which one to fetch next in idle — as a pure, DOM-free state machine (the prebuild.ts / tab-meta.ts
// pattern: executable in node, so the policy is pinned by skeleton-tabs.test.ts while render.ts only wires it).
// A redial declares itself only once the kernel's caps frame has answered the bundle's ready; before that, with
// the ready still queued for the open, or after a socket that died before its caps frame came back, the shim dials
// as a fresh page and the kernel sends everything whole (2026-09-10).
//
// The page HELD every session before the outage, and render.ts deliberately keeps those `sessions` entries
// (the eventual full then takes upsert's append path, so the DOM and the reader's scroll survive). What a
// skeleton id must never do is DISPLAY that stale copy as current — so render.ts asks this set before every
// active-tab display path (`liveSession`) and before drawing a tab (`renderKind`).
//
// A tab at REST (2026-11-15). The kernel stops rebuilding a tab no connected page is watching, and when such a tab's
// transcript moves it takes the tab BACK from every page that had loaded it, with a `status` frame that carries
// `rest: true` (kernel.py _rest_unwatched): it will send this page no chat frame for the tab until the page asks.
// The page KEEPS the copy it holds and does not hide it (the review of the first cut: the copy used to paint at once,
// and a loader in its place for the ten seconds a large session takes to build traded one slowness for another).
// `rested` records that the copy is BEHIND. A rested tab is not a skeleton: the strip draws it as the loaded tab it is,
// from the copy and the kernel's status frames laid over it, and opening it shows the copy at once, says it is
// catching up, and asks for the full, which replaces the copy in place (render.ts). Two rules follow for this module.
// A strip's `skeleton` list names a rested tab (the kernel holds it as one), and it is never adopted from there while
// the page holds its copy. And the idle prefetch never asks for it: a session that is working goes behind again as
// soon as it is fetched, so fetching it in idle would rebuild every tab nobody is watching, over and over, which is
// what the kernel stopped. A tab the page holds NO copy of when the frame lands is a skeleton like any other.

export interface SkeletonState {
  ids: Set<string>;            // the tabs this page holds no CURRENT copy of (the kernel is withholding them)
  order: string[];             // the kernel's list order — ascending transcript size, the prefetch order
  status: Map<string, any>;    // the last kernel-sent status per skeleton id (the chip reads ONLY this)
  loaded: Set<string>;         // ids whose full `session` frame arrived on THIS socket (see applyTabOrderSkeleton)
  rested: Set<string>;         // ids whose copy the page holds and the kernel took back: behind, shown, never in `ids` (see onRest)
}

export function newSkeletonState(): SkeletonState {
  return { ids: new Set(), order: [], status: new Map(), loaded: new Set(), rested: new Set() };
}

/** A tabOrder frame landed. An ARRAY is the kernel's authoritative set → REPLACE (a second reconnect while
 *  skeletons are outstanding re-lists tabs that had loaded — they are stale again, the honest state; once a set
 *  has existed the kernel attaches the key on EVERY strip, an empty list after it empties). NOT an array (an
 *  older kernel, which never sends the key) → KEEP what we hold, pruned to the ids still on the strip. Status entries for ids no longer held are dropped. Returns whether anything
 *  changed, so the caller can key a repaint / re-show on it.
 *
 *  One exception to "array = authoritative": an id whose FULL frame already arrived on this socket is never
 *  re-adopted from an array. The kernel releases a sid the moment it sends its full and enqueues both under
 *  one lock, so a tabOrder that still names X can only be AHEAD of X's full on the wire, never behind it — a
 *  list naming X after X's full is therefore a stale relay, not new information. The federation merge is
 *  exactly such a relay: it keeps a per-host copy of the list and re-attaches it to every merged strip, and
 *  a merge can re-present a copy taken ahead of the full that released the last id (review find 2026-09-08:
 *  the kernel does send an empty list once the set empties, so the copy is eventually told; the filter is
 *  about ORDER, not about a missing key). Without this filter the last skeleton on the strip re-skeletoned on every push, asked for its full, loaded,
 *  and re-skeletoned again — a flap the user's eye follows forever. `onSocketUp` clears the record: a new
 *  socket's list IS new information about every tab. */
export function applyTabOrderSkeleton(st: SkeletonState, skeleton: unknown, order: readonly string[]): boolean {
  const before = st.ids;
  const beforeOrder = st.order;
  let next: string[];
  const keep = new Set(order);   // the strip is the authority on what exists: an id it does not list is gone
  if (Array.isArray(skeleton)) {
    const seen = new Set<string>();
    next = [];
    for (const id of skeleton) {
      // …so a listed skeleton the order lacks (a session that ended before it loaded) is never adopted — the idle
      // prefetch would otherwise ask the kernel for a session it can no longer send (review find 2026-09-07)
      // …nor a tab at rest (2026-11-15): the kernel lists it, and the page shows the copy it holds (see the header)
      if (typeof id === "string" && id && keep.has(id) && !seen.has(id) && !st.loaded.has(id) && !st.rested.has(id)) { seen.add(id); next.push(id); }
    }
  } else {
    next = beforeOrder.filter((id) => keep.has(id) && before.has(id));
  }
  st.ids = new Set(next);
  st.order = next;
  for (const id of Array.from(st.status.keys())) if (!st.ids.has(id)) st.status.delete(id);
  if (before.size !== st.ids.size) return true;
  for (let i = 0; i < next.length; i++) if (beforeOrder[i] !== next[i]) return true;
  for (const id of next) if (!before.has(id)) return true;
  return false;
}

/** A {type:"status"} frame. For a skeleton id the status is STORED for the chip and the caller schedules one
 *  strip repaint; for any other id the caller runs today's path (apply to the session, or the no-base repair —
 *  which for a skeleton id would ask for the full and defeat the whole set within a cycle). */
export function onStatus(st: SkeletonState, id: string, status: unknown): "skeleton" | "not-skeleton" {
  if (!st.ids.has(id)) return "not-skeleton";
  st.status.set(id, status);
  return "skeleton";
}

/** A {type:"status"} frame for an id the set does not list (yet) and the page holds NO session for. The kernel sends a
 *  status frame for a sid it holds as a skeleton and for no other (_send_chat_or_status), so such a frame is a skeleton
 *  tab's whose strip this page has not applied: the pane shim's dispatch FIFO carries a newer whole-state frame (a
 *  second tabOrder) to the END of the burst, so the statuses that arrived between two strips are delivered ahead of
 *  the strip that names their set (a later chat column's open sends two: the pusher cycle its handshake woke and the
 *  ready arm's connect push). Held for that strip: applyTabOrderSkeleton keeps the entry when the array lists the id
 *  and drops it when it does not, and the chip reads it only for a skeleton id. Never the no-base ask, which asked for
 *  every withheld tab's full and loaded the whole board into a column opened as a view of one session (2026-09-11). */
export function holdStatus(st: SkeletonState, id: string, status: unknown): void {
  st.status.set(id, status);
}

/** A {type:"status"} frame that carries `rest: true` (2026-11-15): the kernel took back a tab it had sent this page
 *  whole, because its transcript moved while no connected page was watching it, and will send it no chat frame until
 *  the page asks. `held` says whether the page holds the tab's copy. Held: the copy is behind from this frame on, and
 *  the id joins `rested` and nothing else (it stays a loaded tab; a strip's list naming it is not adopted). Not held
 *  (the frame outran the session's, or the page dropped it): the id is a skeleton like any other, to be fetched in
 *  idle or on its click. Returns "behind" or "skeleton" accordingly. Status frames are delivered in wire order with
 *  the session frames (the shim's FIFO moves neither), so this frame is never older than a full the page has applied. */
export function onRest(st: SkeletonState, id: string, status: unknown, held: boolean): "behind" | "skeleton" {
  if (held) {
    st.rested.add(id);
    if (st.ids.delete(id)) { st.order = st.order.filter((x) => x !== id); st.status.delete(id); }
    return "behind";
  }
  st.loaded.delete(id);
  st.status.set(id, status);
  if (!st.ids.has(id)) { st.ids.add(id); st.order = st.order.filter((x) => x !== id).concat(id); }
  return "skeleton";
}

/** A full `session` frame landed (upsert): the id is loaded, its skeleton entry and stored status go.
 *  Returns whether it WAS held — a just-loaded skeleton that is the active tab must re-show its view, not
 *  append to the hidden one. Also records the id as loaded on this socket (see applyTabOrderSkeleton). */
export function onFull(st: SkeletonState, id: string, declined = false): boolean {
  st.loaded.add(id);
  // the full is the answer: the copy the page holds is current again. A frame the page DECLINED answers nothing (an empty
  // one for a session it holds with content, a read that failed: frame-merge.ts keepResidentEvents): the copy is as far
  // behind as it was and stays marked, so the line that says so stays up until a frame that carries the transcript lands
  // (2026-11-16: the mark went first, and the line came down over a copy eight turns behind)
  if (!declined) st.rested.delete(id);
  const held = st.ids.delete(id);
  if (!held) return false;
  st.order = st.order.filter((x) => x !== id);
  st.status.delete(id);
  return true;
}

/** The tab left the strip (a close, the kernel's omission, a host drop): nothing to load any more. */
export function onDismiss(st: SkeletonState, id: string): void {
  // its view and session left the page with the tab, so it is no longer LOADED here: a later re-listing (a host
  // re-attach, a relay redial) names it a skeleton again and the pane asks for its frame — the way the unfocused
  // pane gets the session the user was on back (T357); before, a once-loaded id could never be a skeleton again on
  // the same socket, so the re-listed tab sat with an "opening…" loader and no ask
  st.loaded.delete(id);
  st.rested.delete(id);
  if (!st.ids.delete(id)) return;
  st.order = st.order.filter((x) => x !== id);
  st.status.delete(id);
}

/** A new socket opened: every earlier full was delivered on a socket that is gone, so the next list the
 *  kernel sends may legitimately re-list those tabs (they are stale after the outage). */
export function onSocketUp(st: SkeletonState): void {
  st.loaded.clear();
}

/** A host's RELAY socket (re)opened (romp:hostRelayUp; `admits` names that host's ids): the per-host twin of onSocketUp
 *  (2026-09-23). A remote kernel's restart redials the relay with reconnect=1, and the fresh remote client withholds every
 *  background tab as a skeleton, including tabs this page loaded on the dead relay socket. `loaded` cleared only on the local
 *  socket's flip, so the remote's list naming those tabs was refused as a stale relay (the rule above), the page held them
 *  as loaded, the remote sent them status frames alone, and their transcripts froze until clicked. The dead socket's frames
 *  are all delivered before the new socket opens (the relay redials from its onclose), so the record is safe to drop here. */
export function onHostSocketUp(st: SkeletonState, admits: (id: string) => boolean): void {
  for (const id of Array.from(st.loaded)) if (admits(id)) st.loaded.delete(id);
}

/** The one skeleton to fetch in this idle callback, or null. Null while the page is hidden (bytes and work
 *  nobody sees — the very regime this exists to spare), while ANY skeleton id is already in flight (one at a
 *  time: a 1 MB full ahead of the active tab's 2 KB tail on a slow link delays that tail; one bounds it), and
 *  when nothing is left. Otherwise the first id of the kernel's order still held, not the active tab (its own
 *  click path asks), and in the current view (a view-hidden tab loads on click instead).
 *
 *  Never a tab at REST (`rested`, 2026-11-15): it is not in the set at all (the page shows the copy it holds), and the
 *  check below stands for a caller that put it there. The prefetch fetches a tab the page has never held, once; a tab
 *  that went behind after loading is brought up to date when it is opened. That is the bound on what the chain can
 *  make the kernel build: at most one build per tab per page load, one in flight at a time.
 *
 *  `reachable` (2026-10-11): whether the socket that would answer an ask for this id is open (a linked machine's relay;
 *  this page's own kernel always is). An unreachable id is neither fetched nor counted as in flight: before, a relay that
 *  dropped mid-prefetch left its ask in flight with no answer coming until the relay reopened, and the one-at-a-time rule
 *  then stopped the prefetch for every other machine's tabs too. Its tabs resume when the relay reopens. */
export function nextPrefetch(st: SkeletonState, activeId: string | null, inFlight: ReadonlySet<string>,
                             hidden: boolean, inView: (id: string) => boolean,
                             reachable: (id: string) => boolean = () => true): string | null {
  if (hidden) return null;
  for (const id of st.ids) if (inFlight.has(id) && reachable(id)) return null;
  for (const id of st.order) {
    if (!st.ids.has(id) || id === activeId || st.rested.has(id) || !reachable(id)) continue;
    if (inView(id)) return id;
  }
  return null;
}

/** How the strip draws a tab. A skeleton id draws as a skeleton even when a (stale) session entry exists —
 *  that entry is kept for the eventual append, never used to paint. A placeholder is "the kernel is building
 *  this, it is coming" (swirl); a skeleton is "resting, loads on your click" (honest dot, muted label). */
export function renderKind(st: SkeletonState, id: string, hasSession: boolean): "skeleton" | "placeholder" | "loaded" {
  if (st.ids.has(id)) return "skeleton";
  return hasSession ? "loaded" : "placeholder";
}
