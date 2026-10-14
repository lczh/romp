// Comment threads (the user 2026-08-13): highlight a passage in the chat, comment on it, and a side
// conversation opens right there — an anchored highlight + popover, powered kernel-side by a fork of
// the session cut at the anchored message. This module is the PURE half (node-testable, no DOM):
// thread types, the whitespace-tolerant exact-text matcher that re-finds a highlight inside a
// re-rendered turn, and the small derivations the popover renders from. All DOM wiring lives in
// render.ts (source-pinned by comments.test.ts, the repo convention).

import { stagedRunBody, type StagedStack } from "./staged-messages";

export type CommentMsg = { who: "you" | "agent"; text: string; t: number };

export type CommentThread = {
  /** the thread's mail is off (T356): a comment thread neither sends nor receives peer mail until broken out */
  mailOff?: boolean;
  /** why, when it is off: "thread" (not yet broken out), "isolation" (the lane's mailbox toggle), "unreadable" (its record cannot be read) */
  mailOffWhy?: string;
  /** messages waiting in its postal box (they land when it is broken out) */
  heldMail?: number;
  tid: string;
  sid?: string;               // the thread's own session (the same id as tid for every thread romp makes): its question's askLive frames and answers key on it (thread-live-1)
  name?: string;              // the thread's editable name (<session>-comment-<N> by default)
  color?: string;             // the comment's identity color — picked distinct from its parent's
  anchorUuid: string;
  exact: string;
  status: "open" | "resolved" | "promoting" | "promoted" | "merging" | "merged";   // merging/merged: folded back into the parent (the user 2026-08-23)
  createdT: number;
  state: string;              // the thread session's live state ("working"/"waiting"/…, "" when dormant)
  /** the CHAT's state chip for a running thread (the kernel's _session_chip over the thread's live row: needsInput,
   *  blocked, compacting, retrying, working, awaitingBg, ready, interrupting), "" for a dormant thread or an older kernel
   *  (thread-live-1, 2026-10-14). Read through threadChipState, never alone. */
  chip?: string;
  error?: string;             // the thread CLI's launch error, when it could not start
  unread: boolean;            // a FINISHED agent reply newer than the read watermark — yellow (kernel truth, T237)
  replyOwed?: boolean;        // a reply is still owed (no exchange yet / user's message newest / turn in progress / a send held) — the green wash (kernel truth, T237; absent on an older kernel)
  queued?: number;            // sends the backend holds or has fed for this thread, not yet in the transcript (T237)
  lastUuid?: string;          // the newest record shown/held — "did the transcript move?" without the projection caps (T237)
  unreachable?: boolean | null;   // a broken thread (missing transcript / lost cut): the kernel owes nothing and says so (T237)
  sinceEpoch?: number;        // ms epoch the thread's current state began — the popover chip's timer
  mode?: string;              // the thread's permission mode — the popover statusline's Auto badge
  fast?: string;              // fast-mode state ("on"/"off"/"cooldown"; "" = unknown → no badge)
  modelFallback?: { pick: string; pickValue: string; live: string; cause: string; category?: string;
                    retry: { on: boolean; everyMin: number; armed: boolean; nextIn: number | null; attempts: number } } | null;   // the requested-model mark (render.ts ModelFallback)
  modelColor?: number[];      // the chat statusline's rank tints, so metaColor paints the popover
  effortColor?: number[];     //   badges exactly as the chat's (the 2026-08-25 color rider)
  promotedName: string;       // the board session it became, when status === "promoted"
  relayedT?: number;          // when the discussion was last sent back to the session (T145) — 0/absent = never
  model?: string;             // the thread's live/chosen model (the popover's switchable chip)
  effort?: string;            // the thread's effort level (ditto)
  msgs: CommentMsg[];
  events?: unknown[];         // the CHAT's own ChatEvents from the branch point on (render parity)
  /** what romp sent with the user's comment, verbatim: everything its opening message carried around their words (the
   *  split-off line and the quote; on a passage from before the last compaction also the turns around it, where the
   *  transcript is saved and the ask's label). From the thread row, or read back from the stored opening message for a
   *  thread made before rows kept it; absent when romp added nothing. The popover's note (render.ts cmtSentNote) */
  sent?: string;
  /** the passage predates the session's last compaction, so `sent` carries the turns around it (the note's longer line) */
  sentBeforeSummary?: boolean;
};

/** The note's line (the user 2026-09-30): they can always see exactly what romp sent on their behalf, one click from its
 *  words. User-facing UI, so it names romp. */
export const CMT_SENT_GIST = "What romp sent with your comment";
export const CMT_SENT_GIST_PRIOR = "What romp sent with your comment, including the turns around it (from before the summary)";

// A frame whose store the kernel could not read carries `error` in place of `threads`.
export type CommentsFrame =
  | { type: "comments"; id: string; threads: CommentThread[] }
  | { type: "comments"; id: string; error: string };

/** Threads grouped by the turn they anchor to — what the mark/badge pass walks per rendered view. */
export function threadsByAnchor(threads: CommentThread[]): Map<string, CommentThread[]> {
  const by = new Map<string, CommentThread[]>();
  for (const th of threads) {
    const list = by.get(th.anchorUuid);
    if (list) list.push(th); else by.set(th.anchorUuid, [th]);
  }
  return by;
}

/** Which thread a click on a comment mark opens, given the mark CHAIN under the pointer — the clicked
 *  mark first, then each enclosing mark outward. Marks nest when two threads anchor to the same passage
 *  (the user 2026-09-10, who commented on one selection twice within seconds): ensureCommentMark re-finds
 *  the identical range for the second thread and wraps its <mark> inside the first's, and the store's
 *  order makes the EARLIER thread the outer one. The delegate hands the click to the innermost mark, so
 *  the outer thread's needs-you ring could never be opened from its own ring: its unread never cleared
 *  and the reply-ready chip stayed lit. The rule: the innermost UNREAD mark when any is (the ring under
 *  the pointer belongs to it; two rings, the newest is the one under the finger), else the innermost,
 *  as before. Null for an empty chain. */
export function pickMarkToOpen(chain: { tid: string; unread: boolean }[]): string | null {
  if (!chain.length) return null;
  const ring = chain.find((m) => m.unread);
  return (ring || chain[0]).tid;
}

/** Does the thread wear the DIMMED look (the mark's .resolved wash, no code/math host tint, the rail tick's .resolved
 *  fade)? Only a thread the user CLOSED. A relayed thread (merged, and merging on its way there) is still an open
 *  conversation (T145: Relay sends the discussion back and the thread stays open for more talk), so it renders like an
 *  open one; dimming it too made its highlight vanish on the light theme's page (the user 2026-09-30, who pressed Relay
 *  and watched the comment stop showing). */
export function threadSettled(status: CommentThread["status"]): boolean {
  return status === "resolved";
}

/** Does the thread get a tick on the scroll rail and a stop in the comment jumps? Open, resolved and relayed threads,
 *  merging included: the relay holds `merging` while it delivers, and the thread must not drop off the rail meanwhile
 *  (nor for good, should a relay ever fail to settle). A promoted thread's talk lives in its own session. */
export function threadOnRail(status: CommentThread["status"]): boolean {
  return status === "open" || status === "resolved" || status === "merging" || status === "merged";
}

/** The thread session is mid-turn — the popover shows its thinking dots. */
export function threadBusy(state: string): boolean {
  return state === "working" || state === "retrying" || state === "compacting";
}

/** The thread session is stuck on an interactive prompt the popover can't answer — say so, and point
 *  at Break out (a full session can). */
// A reply is OWED the moment the user's message is the thread's newest with no agent reply landed
// since (the user 2026-08-24, second report: the mark flashed green on create, dropped to YELLOW
// while the thread CLI was still booting — its live state read idle, a flapping boot-time proxy —
// then went green again once generation started). The in-flight color keys on the EXCHANGE's own
// events: user message in → green until the reply message lands, however the worker session's state
// wobbles on the way. The find-the-event rule, applied to a color.
export function replyOwed(th: CommentThread): boolean {
  const last = th.msgs.length ? th.msgs[th.msgs.length - 1] : null;
  return !!last && last.who === "you";
}
// THE EXCHANGE LATCH (T102, the user 2026-08-26 — replacing the push-count settle): the busy pulse
// is exchange-scoped. It LATCHES at the user's SEND gesture (client-side, optimistic — before any
// kernel round-trip; thread-open must never be the start trigger) and CLEARS only on the
// REPLY-ARRIVED event for that send: the agent's reply RECORD landing in the thread's projected
// msgs — concretely, th.msgs holding MORE who==="agent" entries than it held at the send. Counts of
// the exchange's own records: never wall clocks (cross-host transcripts skew) and never push counts
// (the banned proxy — the old two-quiet-pushes settle counter killed the create-window green
// while the fork booted, and any stall in its 0→1→2 stepping parked green forever with no event to
// clear it). agentCount is that reply-arrived detector's datum; render.ts holds the per-send base.
// Since T237 the KERNEL ships replyOwed (read from the thread's transcript with the event model's own
// turn-end), and the latch covers only the pre-round-trip instant against such a kernel; the
// agentCount clear stays the contract for an older kernel that ships no bit.
export function agentCount(th: CommentThread): number {
  return (th.msgs || []).filter((m) => m.who === "agent").length;
}
export function threadStuck(state: string): boolean {
  return state === "permission" || state === "picker";
}

/** The thread's state as the chat's chip says it (status-chip.ts ChipState): the kernel's chip for a running thread, the
 *  same derivation a tab's statusline wears (thread-live-1, 2026-10-14); for a thread with no chip (dormant, or an older
 *  kernel), its raw backend state read the way the popover always read it: a question or permission prompt is
 *  needsInput, working, retrying and compacting are themselves, anything else is ready. */
export function threadChipState(th: Pick<CommentThread, "chip" | "state">): string {
  if (th.chip) return th.chip;
  const st = th.state || "";
  if (threadStuck(st)) return "needsInput";
  return st === "working" || st === "retrying" || st === "compacting" ? st : "ready";
}

/** The thread waits on the person: open, able to run, and stopped on a question or a permission prompt. Its picker shows
 *  in the popover, and its passage and rail tick wear the needs-you cue (threadCued). */
export function threadNeedsYou(th: Pick<CommentThread, "chip" | "state" | "status" | "error">): boolean {
  return th.status === "open" && !th.error && threadChipState(th) === "needsInput";
}

/** The passage's needs-you cue (the dashed ring or box, and the rail tick's halo): an unread reply on an open thread, or
 *  an open thread waiting on the person (threadNeedsYou). One predicate for the mark, the painter's gate, the rail and
 *  the click's pick, so they never disagree. */
export function threadCued(th: Pick<CommentThread, "chip" | "state" | "status" | "error" | "unread">): boolean {
  return (!!th.unread && th.status === "open") || threadNeedsYou(th);
}

// ── exact-text re-anchoring ────────────────────────────────────────────────────────────────────
// A highlight is stored as the selected text (`exact`); every re-render must re-find it inside the
// anchor turn's rendered text. Rendered whitespace is not byte-stable (markdown collapses runs,
// wraps lines), so both sides are matched through a whitespace-NORMALIZED view with an index map
// back into the raw string. First occurrence wins — same-turn duplicate phrases anchor to their
// first appearance, a known and acceptable simplification.

/** Collapse whitespace runs to single spaces; `map[i]` = raw index of normalized char i. */
function normalize(raw: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = [];
  let inWs = false;
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i];
    if (/\s/.test(c)) {
      inWs = true;
      continue;
    }
    if (inWs && norm.length) {
      norm += " ";
      map.push(i);              // the space stands for the run; anchor it at the run's end
    }
    inWs = false;
    norm += c;
    map.push(i);
  }
  return { norm, map };
}

/** Find `exact` inside `hay`, whitespace-tolerantly. Returns raw [start, end) in `hay`, or null. */
export function findExact(hay: string, exact: string): { start: number; end: number } | null {
  const target = normalize(exact).norm;
  if (!target) return null;
  const { norm, map } = normalize(hay);
  const at = norm.indexOf(target);
  if (at < 0) return null;
  return { start: map[at], end: map[at + target.length - 1] + 1 };
}

/** findExact with a LONGEST-PREFIX fallback (the user 2026-08-13, who wanted the comment visible
 *  in context every time): a selection that spanned several messages anchors to its FIRST turn,
 *  whose rendered text holds only the selection's head — the full exact-match fails and the thread
 *  fell back to the tiny badge alone. Binary-search the longest word-prefix that still matches, so
 *  the portion that lives in the anchored turn highlights. A too-short remnant (under 3 words and
 *  under 12 characters) stays null — highlighting a stray "The" would mark the wrong thing. */
export function findAnchorRange(hay: string, exact: string):
    { start: number; end: number; partial: boolean } | null {
  const full = findExact(hay, exact);
  if (full) return { ...full, partial: false };
  const words = exact.trim().split(/\s+/);
  let lo = 1, hi = words.length - 1, best: { start: number; end: number; k: number } | null = null;
  while (lo <= hi) {
    const k = (lo + hi) >> 1;
    const r = findExact(hay, words.slice(0, k).join(" "));
    if (r) { best = { ...r, k }; lo = k + 1; } else hi = k - 1;
  }
  if (!best) return null;
  const matched = words.slice(0, best.k).join(" ");
  if (best.k < 3 && matched.length < 12) return null;
  return { start: best.start, end: best.end, partial: true };
}

/** A text node the mark pass must leave alone (T349, the user 2026-09-11: a comment on a table's row broke the table):
 *  the whitespace text between a table's cells and rows sits directly under TABLE / THEAD / TBODY / TFOOT / TR, and an
 *  inline <mark> placed there gets its own anonymous table cell, so the columns shift. Those nodes carry no visible
 *  text; skipping them lets the mark ride the row cell by cell while the table's boxes stay. `parentTag` is the text
 *  node's parent element's tagName (upper-case in an HTML document). */
export function markSkipsParent(parentTag: string | null | undefined): boolean {
  return /^(TABLE|THEAD|TBODY|TFOOT|TR)$/.test(parentTag || "");
}

/** Split a global [start, end) character range over consecutive text-node lengths into per-node
 *  slices — what the DOM pass wraps in <mark> elements. */
export function sliceRanges(nodeLens: number[], start: number, end: number):
    { idx: number; s: number; e: number }[] {
  const out: { idx: number; s: number; e: number }[] = [];
  let off = 0;
  for (let i = 0; i < nodeLens.length && off < end; i++) {
    const len = nodeLens[i];
    const s = Math.max(start, off);
    const e = Math.min(end, off + len);
    if (e > s) out.push({ idx: i, s: s - off, e: e - off });
    off += len;
  }
  return out;
}

/** Optimistic pending sends, reconciled against the kernel's frame (the registerOptimistic pattern):
 *  each landed 'you' message spends AT MOST ONE pending row with its text — a count-based match, so
 *  sending the same words twice keeps the second bubble until its own message lands. Returns the
 *  still-pending remainder to render after the server messages. */
export function prunePending(pending: { text: string; t: number }[], msgs: CommentMsg[]):
    { text: string; t: number }[] {
  const counts = new Map<string, number>();
  for (const m of msgs) {
    if (m.who !== "you") continue;
    const k = normalize(m.text).norm;
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return pending.filter((p) => {
    const k = normalize(p.text).norm;
    const c = counts.get(k) || 0;
    if (c > 0) {
      counts.set(k, c - 1);
      return false;
    }
    return true;
  });
}

/** A comment create as the client holds it from the send gesture until the kernel answers it: the
 *  anchor, the words, the dialog's picks and the gesture's own id. The kernel answers a repeat of a
 *  create it completed with the same thread's ack (a lost ack, a lag-parked copy that landed before the
 *  client's re-post reached it), and it tells a repeat from a fresh comment by this id: two gestures in
 *  the same words on the same passage are two comments, and a memo keyed on the words alone answered the
 *  second with the first thread and wrote its name nowhere (review, 2026-09-09). */
export type CommentCreate = { sid: string; uuid: string; exact: string; text: string; name: string;
  model: string; effort: string; fast: string; color: string; createId: string;
  /** a few words' sentence or code line, read by the page at the selection (render.ts selectionQuoteView): the thread's
   *  first message names the words and quotes it (the kernel's _comment_quote_parts, 2026-10-14) */
  context?: string;
  /** a code passage's lines with their line breaks, which the first message quotes in place of the passage */
  shown?: string;
  /** the thread ids the page knew on the session at the send: a thread on this passage that is NOT among them is
   *  the one this create made (synthSuperseded) */
  prior?: string[] };

/** One id per send gesture: the moment and a random tail, in the same shape as a provisional tab's id.
 *  Random, not a counter: a reloaded viewer starts its counters over, and the kernel's memo outlives it. */
export function mintCreateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

/** The create a send gesture holds: stamped with a fresh id, picks defaulted to "" (the kernel's
 *  default-comment settings, then the parent's). */
export function newCommentCreate(anchor: { sid: string; uuid: string; exact: string; model?: string; effort?: string;
                                           fast?: string; color?: string; context?: string | null; shown?: string | null },
                                 text: string, name: string): CommentCreate {
  return { sid: anchor.sid, uuid: anchor.uuid, exact: anchor.exact, text, name, model: anchor.model || "",
           effort: anchor.effort || "", fast: anchor.fast || "", color: anchor.color || "",
           createId: mintCreateId(), ...(anchor.context ? { context: anchor.context } : anchor.shown ? { shown: anchor.shown } : {}) };
}

/** The held create an ack or a refusal answers. The kernel echoes the gesture's createId, so the answer names exactly
 *  one send; an older kernel echoes only the anchor turn, and then the oldest create held on that session and turn is
 *  the one (the order the kernel's door handles them in). Keyed by the createId and not the turn (2026-10-01): two
 *  comments on two passages of one reply used to share a slot, so the first ack retired the second create's hold and
 *  adopted the first thread into whichever box was open on the session, the user's half-typed second comment included. */
export function holdAnswered(holds: Iterable<CommentCreate>, sid: string, uuid: string, createId?: unknown): CommentCreate | null {
  const all = Array.from(holds);
  if (typeof createId === "string" && createId) return all.find((c) => c.createId === createId) || null;
  return all.find((c) => c.sid === sid && c.uuid === uuid) || null;
}

/** Where a selection sits in its turn's rendered prose (the text turnProse joins): [start, end) offsets into it. */
export type PassageAt = { start: number; end: number };

/** `at` when its offsets are integers with 0 <= start < end <= len, else null. */
function atIn(at: PassageAt | null | undefined, len: number): PassageAt | null {
  if (!at || !Number.isInteger(at.start) || !Number.isInteger(at.end)) return null;
  return at.start >= 0 && at.start < at.end && at.end <= len ? at : null;
}

/** THE SAME PASSAGE, at the anchor level (2026-10-01, the user's call: a Comment on a passage that already has a thread
 *  opens that thread). Two selections are the same passage when they anchor to the same turn (the caller compares the
 *  anchor uuids) and their texts OVERLAP in that turn's rendered prose: each is located the way its highlight is drawn
 *  (findAnchorRange, the full text or its longest word-prefix in the turn, first occurrence) and the two [start, end)
 *  ranges share at least one character. Touching ranges ("…the first request" / ". Each export…") are two passages.
 *  `at` is where `b` was selected in the prose, when the gesture knows it: `b` is also the same passage when that range
 *  overlaps `a`'s, whether or not `b`'s text is found. Without the prose, or with `a` not found in it, or with `b` not
 *  found and no such range, the fallback is containment of the whitespace-normalized texts: one holding the other is the
 *  same passage. */
export function samePassage(prose: string | null | undefined, a: string, b: string, at?: PassageAt | null): boolean {
  return passageMatch(prose, a, b, at) > 0;
}

/** samePassage's answer, ranked for the callers that choose among several matches: 2 when the live range overlaps `a`'s
 *  range, 1 for any other match (the first occurrences, or the containment fallback), 0 for none. */
function passageMatch(prose: string | null | undefined, a: string, b: string, at?: PassageAt | null): 0 | 1 | 2 {
  if (prose) {
    const ra = findAnchorRange(prose, a), rb = findAnchorRange(prose, b), live = atIn(at, prose.length);
    if (ra && live && ra.start < live.end && live.start < ra.end) return 2;
    if (ra && rb) return ra.start < rb.end && rb.start < ra.end ? 1 : 0;
  }
  const na = normalize(a).norm, nb = normalize(b).norm;
  return na && nb && (na.includes(nb) || nb.includes(na)) ? 1 : 0;
}

/** The create already in flight on the same passage (same session, same turn, overlapping text: samePassage, with the
 *  selection's live range `at` when known), or null. While the send it returns still waits for its thread (its
 *  optimistic row still listed), a second Comment on the passage is the same comment, not a new one: the box re-opens on
 *  that send instead of opening empty-handed with the words already sent, which is how a click outside the box, a
 *  re-select and an Enter made a second thread in the same words (2026-10-01). Where several sends match, one whose
 *  passage the live range overlaps comes before one matched by first occurrence or containment; the first sent wins
 *  within each rank. */
export function holdForPassage(holds: Iterable<CommentCreate>, sid: string, uuid: string, exact: string,
                               prose?: string | null, at?: PassageAt | null): CommentCreate | null {
  let best: CommentCreate | null = null, rank = 0;
  for (const c of holds) {
    if (c.sid !== sid || c.uuid !== uuid) continue;
    const m = passageMatch(prose, c.exact, exact, at);
    if (m > rank) { best = c; rank = m; }
  }
  return best;
}

/** A thread that can still take a reply: open, or relayed (merged, and merging on its way there; T145 keeps a relayed
 *  thread talkable). A resolved thread is closed for good and a promoted one is its own session now: a new comment on
 *  their passage starts a new thread. */
export function threadTakesReplies(status: CommentThread["status"]): boolean {
  return status === "open" || status === "merged" || status === "merging";
}

// ── select, quote, stage, in a thread (the user 2026-10-07, who wanted the message box's behaviours in comment threads) ──
// A passage selected in the thread's own messages becomes a quote chip in its reply box; Cmd/Ctrl+Enter stages the typed
// words with their chips; Enter sends the whole staged run. A thread IS a session with its own id, so its staged stack is
// keyed by that id (the thread's tid) on a stack of its own, never the chat's: the chat's stack belongs to the tabs. The
// release is ONE reply through the comment reply door (commentReply), its body the chat's own staged run (stagedRunBody):
// each staged item's quote under the conversation's lead-in and then its words, the typed reply last. render.ts owns the
// popover's DOM; these are the rules, executed by comments.test.ts.

const hasQuote = (cites: readonly unknown[]): boolean =>
  cites.some((c) => !!c && typeof (c as { quote?: unknown }).quote === "string" && !!(c as { quote: string }).quote);

/** Cmd/Ctrl+Enter in a thread's reply box: hold the words and their quote chips on the thread's own stack. A quote alone
 *  stages (select, stage, repeat, then one typed reply sends the run); nothing at all stages nothing. */
export function stageThreadReply(stack: StagedStack, tid: string, text: string, cites: readonly unknown[]): boolean {
  if (!text && !hasQuote(cites)) return false;
  stack.push(tid, { text, cites: cites.slice() });
  return true;
}

/** Enter in a thread's reply box (or its strip's Send now, which passes no typed reply): the commentReply frame that sends
 *  the thread's staged run and the typed reply as one message, the stack emptied, or null when there is nothing to send
 *  (no stack, no words, no quote). Addressed as every reply is: the parent session and the thread. */
export function threadReply(stack: StagedStack, parentSid: string, tid: string,
                            typed: { text: string; cites?: unknown[] } | null): { type: "commentReply"; id: string; tid: string; text: string } | null {
  const text = stagedRunBody(stack.list(tid), typed);
  if (!text) return null;
  stack.takeAll(tid);
  return { type: "commentReply", id: parentSid, tid, text };
}

/** The thread a new Comment on this passage should open instead of a new one (the user 2026-10-01: one passage never
 *  silently grows a second thread): a talkable thread anchored to the same turn whose passage overlaps the selection
 *  (samePassage, with the selection's live range `at` when known). Where several match, one whose passage the live range
 *  overlaps comes before one matched by first occurrence or containment; the newest wins within each rank (two marks nest
 *  on one passage). Null when none. */
export function existingThreadFor(threads: CommentThread[], uuid: string, exact: string, prose?: string | null,
                                  at?: PassageAt | null): CommentThread | null {
  let best: CommentThread | null = null, rank = 0;
  for (const t of threads) {
    if (t.tid.startsWith("pending:") || t.anchorUuid !== uuid || !threadTakesReplies(t.status)) continue;
    const m = passageMatch(prose, t.exact, exact, at);
    if (!m || m < rank) continue;
    if (!best || m > rank || (t.createdT || 0) >= (best.createdT || 0)) { best = t; rank = m; }
  }
  return best;
}

/** Has this frame got the thread a held create made? A thread on the same anchor turn with the same passage that the
 *  page did not know when the create was sent (its `prior`). Until then the create's optimistic mark stays: an earlier
 *  thread elsewhere in the same reply is NOT it (2026-10-01: the old rule retired the mark on any thread anchored to
 *  the turn, so a second comment on a reply that already had one lost its highlight on the next frame and kept it off
 *  until the kernel answered). The kernel caps a stored passage at 2000 characters, so a row holding that cap of the
 *  sent passage is the same passage. */
export function synthSuperseded(threads: CommentThread[], held: CommentCreate): boolean {
  const prior = new Set(held.prior || []);
  return threads.some((t) => t.anchorUuid === held.uuid && !prior.has(t.tid)
    && (t.exact === held.exact || (t.exact.length >= 1990 && held.exact.startsWith(t.exact))));
}

/** The commentCreate frame for a held create: the send and every re-post of it build the same one, so
 *  the kernel sees one id for one gesture. */
export function commentCreateFrame(c: CommentCreate): { type: "commentCreate"; id: string; uuid: string; exact: string;
    text: string; name: string; model: string; effort: string; fast: string; color: string; createId: string; context?: string;
    shown?: string } {
  return { type: "commentCreate", id: c.sid, uuid: c.uuid, exact: c.exact, text: c.text, name: c.name,
           model: c.model, effort: c.effort, fast: c.fast, color: c.color, createId: c.createId,
           ...(c.context ? { context: c.context } : c.shown ? { shown: c.shown } : {}) };   // only when there is one: a frame without either is the plain quote
}
