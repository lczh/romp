// THE API-ERROR CARD'S META LINE, in one place. The live API card says under its gist what happens next: a
// countdown to the next automatic retry, why retrying is paused, or the fix for a stop only the person can
// clear. Three writers set that line: the card's own render, the one-second tick, and the Stop/Resume-all
// flip (a press, or the kernel's globalRetryPaused frame). Each used to carry its own ladder, and they
// disagreed: the flip rewrote every card to the pause's words or to "retrying soon…", and the tick fell
// through to "retrying soon…" for a session it had left out of the retry set, so a card stopped on a
// spend cap promised a retry the kernel never sends the moment the pause lifted (2026-11-15: a Resume
// press after the cap was raised, and every capped card read "retrying soon…" while nothing retried).
// One function now decides the line from the same inputs for all three, so they cannot disagree: the
// card's own session status, the global pause, and that session's next retry time. Pure and DOM-free so
// node tests execute it; the Status interface in render.ts is a superset of the shape read here.
import { durLabel } from "./duration";
import { apiErrorReason } from "./api-error-reason";

export interface ApiCardStatus {
  apiRefusal?: boolean;
  apiSpendLimit?: boolean;
  apiModelLimit?: boolean;
  apiAuthErr?: boolean;
  retrySuppressed?: boolean;
  retryTries?: number | null;
}

/** The global retry pause as the kernel's globalRetryPaused frame carries it. */
export interface RetryPause {
  paused: boolean;
  resumeAt: number | null;   // epoch SECONDS the limiting usage window resets; null for a manual or spend pause
  reason: string;            // "spend" for a monthly spend cap, "limit" for a usage window, "" for a manual Stop
  still?: boolean;           // a spend pause that came back because a retry sent after the cap looked lifted, or on
                             // Resume, was refused: the cap is still in force (the kernel's word, on the same frame)
}

// The lines of the four stops nothing retries (the card draws no Retry button for them): each names its own fix, in
// words the interface already uses for that state, so the chat card, the feed card and the bell cannot disagree
// about what a failure asks of the person.
// A refusal is deterministic on the same input (api-error-reason.ts carries the same sentence for the bell).
export const REFUSAL_REMEDY = "the model's safeguards refused this prompt — rewrite it or drop this thread";
// A monthly spend cap.
export const SPEND_CAP_REMEDY = "spend limit reached — raise it at claude.ai/settings/usage";
// …and the spend cap's own line once a retry has been tried and refused (the pause's `still`): the answer to a
// Resume press that changed nothing, in place of a line that would read exactly as it did before the press.
export const SPEND_STILL_REMEDY = "spend limit still in force — raise it at claude.ai/settings/usage";
// A spent model allowance: the retry line's and the bell's own words for it (api-error-reason.ts), read from there.
export const MODEL_LIMIT_REMEDY = apiErrorReason({ modelLimit: true });
// A dead credential: the feed card's sentence for it (the kernel's _feed_session_entry `what`; the test pins the two
// equal). The feed names the stored login instead when it knows which one was refused; this card says the general one.
export const AUTH_REMEDY = "this session's sign-in or API key isn't working — fix the login (claude /login) or the key, or switch which one it bills";
// A thread the person interrupted: its auto-retry stays off until a turn of theirs lands.
export const SUPPRESSED_LINE = "auto-retry stopped for this session — send a message to resume";

/** Whether nothing retries this card automatically, pause or no pause: a refusal, a spend cap, a spent
 *  model allowance or a dead credential. The pause is about automatic retries, so it never words these. */
export function apiCardOnYou(st: ApiCardStatus | null | undefined): boolean {
  return !!(st && (st.apiRefusal || st.apiSpendLimit || st.apiModelLimit || st.apiAuthErr));
}

/** The paused line names why and until when: a usage-window pause carries the window's reset, so the card
 *  counts down to it (the user 2026-07-13); a monthly spend cap has no reset and lifts only when it is
 *  raised, so the line says so (the user 2026-07-14); a manual Stop is the plain off label. */
export function retryPausedText(pause: RetryPause, nowMs: number): string {
  if (pause.reason === "spend") {
    return pause.still ? "monthly spend limit still in force — raise it at claude.ai/settings/usage"
                       : "monthly spend limit reached — raise it at claude.ai/settings/usage";
  }
  if (pause.resumeAt) {
    const dt = Math.max(0, Math.ceil(pause.resumeAt - nowMs / 1000));
    const hm = new Date(pause.resumeAt * 1000).toTimeString().slice(0, 5);
    return `usage limit — retrying at ${hm} (in ${durLabel(dt)})`;
  }
  return "auto-retry off (global)";
}

/** The card's line. `st` is the status of the session the card belongs to, `nextAtMs` that session's
 *  next automatic retry (epoch ms; undefined when none is scheduled), `nowMs` the clock.
 *  The order is the card's own: what nothing retries first (each stop's own remedy, which holds whatever
 *  the pause does), then the global pause, then this thread's own stop, then the countdown. */
export function apiCardLine(st: ApiCardStatus | null | undefined, pause: RetryPause,
                            nextAtMs: number | undefined, nowMs: number): string {
  if (st?.apiRefusal) return REFUSAL_REMEDY;
  if (st?.apiSpendLimit) return pause.paused && pause.reason === "spend" && pause.still ? SPEND_STILL_REMEDY : SPEND_CAP_REMEDY;
  if (st?.apiModelLimit) return MODEL_LIMIT_REMEDY;
  if (st?.apiAuthErr) return AUTH_REMEDY;
  if (pause.paused) return retryPausedText(pause, nowMs);
  if (st?.retrySuppressed) return SUPPRESSED_LINE;
  if (!nextAtMs) return "retrying soon…";
  const left = Math.max(0, Math.ceil((nextAtMs - nowMs) / 1000));
  const tries = st?.retryTries || 0;
  // the ONE duration format (duration.ts): "2m 30s", never "150s"; the attempt count explains why the
  // gap has grown (each failure steps the backoff up) rather than looking stuck
  return `retrying in ${durLabel(left)}` + (tries > 1 ? ` · ${tries} tries so far` : "");
}
