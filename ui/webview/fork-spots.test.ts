import { test } from "node:test";
import * as assert from "node:assert/strict";
import { forkSpotPlan, type SpotEvent } from "./fork-spots";

// Synthetic events: "p*" a genuine editable prompt (a cut), "r*" a reply (text), "n*" a progress note (narration), "t*" a tool,
// "s*" a message delivered mid-turn (absorbed).
const E = (id: string): SpotEvent => id[0] === "p" ? { kind: "user", uuid: id } : id[0] === "s" ? { kind: "user", uuid: id, absorbed: true }
  : id[0] === "r" ? { kind: "assistant", uuid: id } : id[0] === "n" ? { kind: "assistant", uuid: id, narration: true } : { kind: "tool", uuid: id };
const plan = (ids: string[], working: boolean) => Object.fromEntries(forkSpotPlan(ids.map(E), (ev) => ev.uuid?.[0] === "p", working));

test("while a turn streams, a note never takes the tip spot over: no hop from note to note; the turn's end settles it (2026-11-06)", () => {
  const live = ["p1", "r1", "t1", "p2", "n1", "t2", "n2", "t3"];
  assert.deepEqual(plan(live, true), { r1: "p2" }, "working, a turn of notes: no tip spot moving with them");
  assert.deepEqual(plan([...live, "n3"], true), { r1: "p2" }, "a newer note moves nothing");
  // the status leaves "working" (the settle): the spot lands under the turn's reply, its last prose, a note included
  assert.deepEqual(plan([...live, "n3", "t4"], false), { r1: "p2", n3: "" });
  // a text reply mid-turn anchors the tip as it always did
  assert.deepEqual(plan(["p1", "r1", "t1", "r2", "t2"], true), { r2: "" });
});

test("a turn's last prose anchors its spot wherever it sits; a message delivered mid-turn ends no turn", () => {
  assert.deepEqual(plan(["p1", "n1", "t1", "n2", "t2", "p2"], false), { n2: "p2" }, "a summary note before a cleanup tool");
  assert.deepEqual(plan(["p1", "n1", "t1", "s1", "n2", "t2", "r1", "p2", "r2"], false), { r1: "p2", r2: "" },
    "the note before a mid-turn message is not the turn's reply");
});
