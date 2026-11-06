// Where the fork spots go (2026-11-06, lifted out of render.ts applyForkSpots so it runs in node tests). A spot lives BELOW the
// response it branches from (the user 2026-08-19): one per response run, under the run's last response, its cut the first
// genuine editable prompt after the run; the TIP run, with no prompt after it, forks the whole conversation (cut "").
//
// A turn's last prose is its reply wherever it sits (compact.ts segmentReplies), a progress note included, so a turn that ends on
// a note (a summary before a cleanup tool) still anchors its spot. A note becomes that reply only once the turn has ENDED: while
// the session works, each new note would otherwise take the reply over and the tip spot would hop from note to note, its row
// reserving space under the newest one. The event it keys on is the session's status leaving "working" (the kernel's settle
// on the CLI's result frame, carried by the session frame's status). A turn that has notes alone has no tip spot while it
// streams; at its end the spot lands under its reply, inline in the "worked …" line that appears with it, so nothing moves.
// (A text reply mid-turn anchors the tip as it always did; under look E its spot rides the reply's tool line, the last row
// before anything newer, so a move of it moves no painted row.)
import { isProseShape, segmentReplies } from "./compact";

export interface SpotEvent { kind: string; uuid?: string; narration?: boolean; absorbed?: boolean; interruptSettle?: unknown; command?: unknown }

/** response uuid -> cut uuid ("" = the whole conversation). `isCut(ev)`: a genuine editable prompt a run is cut below. */
export function forkSpotPlan(evs: readonly SpotEvent[], isCut: (ev: SpotEvent) => boolean, working: boolean): Map<string, string> {
  const boundary = (i: number) => evs[i].kind === "user" && !evs[i].absorbed;
  const replies = segmentReplies(evs.map((e) => e.kind), (i) => isProseShape(evs[i]), boundary);
  let open = 0;                              // the first event of the turn still open: its reply is not final while working
  for (let i = 0; i < evs.length; i++) if (boundary(i)) open = i + 1;
  const spots = new Map<string, string>();
  let run: string | null = null;             // the newest response uuid whose run has no cut yet
  for (let ix = 0; ix < evs.length; ix++) {
    const ev = evs[ix];
    // a progress note (narration) is the agent speaking mid-turn, not a response the fork cuts below: it anchors no spot,
    // unless it is its turn's reply, and the open turn's reply only once the turn has ended
    if (ev.kind === "assistant" && ev.uuid && (!ev.narration || (replies.has(ix) && (!working || ix < open)))) run = ev.uuid;
    else if (ev.kind === "user" && ev.uuid && run && !spots.has(run) && isCut(ev)) spots.set(run, ev.uuid);
  }
  if (run && !spots.has(run)) spots.set(run, "");   // the tip run: nothing follows -> whole conversation
  return spots;
}
