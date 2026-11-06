// Where the fork spots go (2026-11-06, lifted out of render.ts applyForkSpots so it runs in node tests). A spot lives BELOW the
// response it branches from (the user 2026-08-19): one per response run, its cut the first genuine editable prompt after the run;
// the TIP run, with no prompt after it, forks the whole conversation (cut "").
//
// A turn's last prose is its reply wherever it sits (compact.ts segmentReplies), a progress note included, so a turn that ends on
// a note (a summary before a cleanup tool) still anchors its spot. While the turn is OPEN nothing in it anchors a spot and there
// is no tip spot at all: each new note, and each text block an older model writes between its tools, would otherwise take the
// reply over and the spot would hop from row to row. The event the plan keys on is the turn's end as the kernel reads it, the
// session status's `turnOpen` (build_session's _session_working over the CLI's own turns, the reading the running-call stamp
// takes), never the status chip: a permission prompt, a question, a retry, an interrupt in flight and a restart's reattach all
// move the chip off "working" while the turn goes on. The runs of the turns before it keep their spots, cut at the prompts that
// followed them, so no spot moves while the turn is open; at its end the tip spot lands under the reply.
import { isProseShape, segmentReplies } from "./compact";

export interface SpotEvent { kind: string; uuid?: string; narration?: boolean; absorbed?: boolean; interruptSettle?: unknown; command?: unknown }

/** response uuid -> cut uuid ("" = the whole conversation). `isCut(ev)`: a genuine editable prompt a run is cut below. `turnOpen`:
 *  the session's turn has not ended (the kernel's status.turnOpen). */
export function forkSpotPlan(evs: readonly SpotEvent[], isCut: (ev: SpotEvent) => boolean, turnOpen: boolean): Map<string, string> {
  const boundary = (i: number) => evs[i].kind === "user" && !evs[i].absorbed;
  const replies = segmentReplies(evs.map((e) => e.kind), (i) => isProseShape(evs[i]), boundary);
  let open = 0;                              // the first event of the last turn: the open one while turnOpen
  for (let i = 0; i < evs.length; i++) if (boundary(i)) open = i + 1;
  const spots = new Map<string, string>();
  let run: string | null = null;             // the newest response uuid whose run has no cut yet
  for (let ix = 0; ix < evs.length; ix++) {
    const ev = evs[ix];
    // a progress note (narration) is the agent speaking mid-turn, not a response the fork cuts below: it anchors no spot unless
    // it is its turn's reply; and nothing in the turn still open anchors one
    if (turnOpen && ix >= open) break;
    if (ev.kind === "assistant" && ev.uuid && (!ev.narration || replies.has(ix))) run = ev.uuid;
    else if (ev.kind === "user" && ev.uuid && run && !spots.has(run) && isCut(ev)) spots.set(run, ev.uuid);
  }
  if (!turnOpen && run && !spots.has(run)) spots.set(run, "");   // the tip run: nothing follows -> whole conversation
  return spots;
}
