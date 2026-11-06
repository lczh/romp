import { test } from "node:test";
import * as assert from "node:assert/strict";
import { forkSpotPlan, type SpotEvent } from "./fork-spots";

// Synthetic events: "p*" a genuine editable prompt (a cut), "r*" a reply (text), "n*" a progress note (narration), "t*" a tool,
// "s*" a message delivered mid-turn (absorbed), "e*" a user row that cuts nothing (an optimistic echo, a nudge).
const E = (id: string): SpotEvent => id[0] === "p" || id[0] === "e" ? { kind: "user", uuid: id } : id[0] === "s" ? { kind: "user", uuid: id, absorbed: true }
  : id[0] === "r" ? { kind: "assistant", uuid: id } : id[0] === "n" ? { kind: "assistant", uuid: id, narration: true } : { kind: "tool", uuid: id };
const plan = (ids: string[], turnOpen: boolean) => Object.fromEntries(forkSpotPlan(ids.map(E), (ev) => ev.uuid?.[0] === "p", turnOpen));

test("while a turn streams, a note never takes the tip spot over: no hop from note to note; the turn's end settles it (2026-11-06)", () => {
  const live = ["p1", "r1", "t1", "p2", "n1", "t2", "n2", "t3"];
  assert.deepEqual(plan(live, true), { r1: "p2" }, "the turn open, a turn of notes: no tip spot moving with them");
  assert.deepEqual(plan([...live, "n3"], true), { r1: "p2" }, "a newer note moves nothing");
  // the turn ends (the kernel's turnOpen goes false): the spot lands under the turn's reply, its last prose, a note included
  assert.deepEqual(plan([...live, "n3", "t4"], false), { r1: "p2", n3: "" });
});

test("while the turn is open no spot moves at any step: a text block between tools, a message mid-turn, a non-cutting user row (round six)", () => {
  // the stepped turn, one event at a time: the plan holds still from the prompt to the last step, the earlier turn's spot included
  const steps = ["p2", "n1", "t1", "r2", "t2", "s1", "n2", "t3", "r3", "t4", "e1", "n3"];
  const prefix = ["p1", "r1", "t0"];
  for (let k = 1; k <= steps.length; k++) {
    assert.deepEqual(plan([...prefix, ...steps.slice(0, k)], true), { r1: "p2" }, "open, after " + steps.slice(0, k).join(","));
  }
  // a text reply an older model writes between its tools takes no spot while the turn is open (it used to take the tip, and
  // hop at every text block); the turn's end settles the spot on the last one
  assert.deepEqual(plan(["p1", "r1", "t1", "r2", "t2"], true), {});
  assert.deepEqual(plan(["p1", "r1", "t1", "r2", "t2"], false), { r2: "" });
  // a user row that cuts nothing (an optimistic echo of a message sent mid-turn, before the kernel reads it as delivered mid-turn)
  // splits the open turn on the client's side: the note before it still anchors nothing while the turn is open
  assert.deepEqual(plan(["p1", "r1", "p2", "n1", "t1", "e1"], true), { r1: "p2" });
  // the turn ended: a spot under each response, the tip under the last
  assert.deepEqual(plan(["p1", "r1", "p2", "n1", "t1", "s1", "n2", "t2"], false), { r1: "p2", n2: "" });
});

test("a turn's last prose anchors its spot wherever it sits; a message delivered mid-turn ends no turn", () => {
  assert.deepEqual(plan(["p1", "n1", "t1", "n2", "t2", "p2"], false), { n2: "p2" }, "a summary note before a cleanup tool");
  assert.deepEqual(plan(["p1", "n1", "t1", "s1", "n2", "t2", "r1", "p2", "r2"], false), { r1: "p2", r2: "" },
    "the note before a mid-turn message is not the turn's reply");
});
