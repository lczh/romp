import { test } from "node:test";
import * as assert from "node:assert/strict";
import { anchorUuidAt } from "./anchor-uuid";

// A fake element tree: closest() walks up the parents and matches the two selectors the helper asks for.
class El {
  constructor(public attrs: Record<string, string>, public cls: string[] = [], public parentElement: El | null = null) {}
  getAttribute(n: string) { return this.attrs[n] ?? null; }
  closest(sel: string): El | null {
    for (let e: El | null = this; e; e = e.parentElement) {
      if (sel === "[data-note-uuid]" && e.attrs["data-note-uuid"] != null) return e;
      if (sel === ".turn" && e.cls.includes("turn")) return e;
    }
    return null;
  }
}

test("a selection that starts in a run's caption anchors on the note's own record, not the run's first tool (2026-11-06)", () => {
  const head = new El({ "data-uuid": "first-tool" }, ["turn", "turn-toolgroup"]);
  const caption = new El({ "data-note-uuid": "the-note" }, ["assistant", "md", "toolgroup-caption"], head);
  const para = new El({}, ["p"], caption);
  assert.equal(anchorUuidAt(para), "the-note");
  assert.equal(anchorUuidAt({ parentElement: para }), "the-note", "a text node, through its parent element");
  const line = new El({}, ["toolgroup-line"], head);
  assert.equal(anchorUuidAt(line), "first-tool", "the run's own line anchors on the run");
  const reply = new El({ "data-uuid": "a-reply" }, ["turn", "turn-assistant"]);
  assert.equal(anchorUuidAt(new El({}, ["p"], reply)), "a-reply", "any other row, on its turn");
  assert.equal(anchorUuidAt(null), null);
});
