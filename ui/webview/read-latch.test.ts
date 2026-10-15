// A comment thread's read, held until the kernel's frame shows it (read-latch.ts, 2026-10-15), driven by execution: a frame
// below the count the kernel answered with reads as read, one at or past it is the kernel's word (a reply after the read
// included); an answer for a superseded read changes nothing; a link that drops before the answer lets the first frame after
// it is back decide, on that host alone. The page's wiring and the flap itself run on the real bundle in
// comment-read-latch-browser.test.ts. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { holdRead, heldUnread, answerRead, linkEdge, readKey } from "./read-latch";
import { hostOf } from "./host-prefix";

const SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const TID = "22222222-3333-4444-8555-00000000000a";

test("before the kernel answers, every frame reads as read; the answer's count then splits the frames from before the read from the ones after", () => {
  const l = holdRead(SID, TID, 1, true);
  assert.deepEqual(heldUnread(l, { unread: true, seenSeq: 0 }), { unread: false, done: false }, "unanswered: held");
  assert.deepEqual(heldUnread(l, { unread: false, seenSeq: 5 }), { unread: false, done: false },
                   "unanswered, even a frame that counts more reads: they may be another page's, from before this one");
  assert.equal(answerRead(l, 1, 3), true);
  assert.deepEqual(heldUnread(l, { unread: true, seenSeq: 2 }), { unread: false, done: false }, "a frame from before the read");
  assert.deepEqual(heldUnread(l, { unread: true }), { unread: false, done: false }, "a frame with no count is below every answer");
  assert.deepEqual(heldUnread(l, { unread: false, seenSeq: 3 }), { unread: false, done: true }, "the frame that counts it ends the hold");
  assert.deepEqual(heldUnread(l, { unread: true, seenSeq: 3 }), { unread: true, done: true }, "…and its unread bit is a reply after the read");
  assert.deepEqual(heldUnread(l, { unread: true, seenSeq: 4 }), { unread: true, done: true }, "a later read's frame shows this one too");
});

test("an answer for a read the page has since superseded changes nothing; a malformed one neither", () => {
  const l = holdRead(SID, TID, 2, false);
  assert.equal(answerRead(l, 1, 7), false, "the first read's answer: the second read's decides");
  assert.equal(l.seq, null);
  assert.equal(answerRead(l, 2, "7"), false, "a count that is not a number");
  assert.equal(answerRead(l, "2", 7), false, "an id of another type is another read");
  assert.equal(answerRead(l, 2, 7), true);
  assert.equal(l.seq, 7);
});

test("a link that drops before the answer: frames from the dead link hold, and the first frame after it is back decides, on that host alone", () => {
  const local = holdRead(SID, TID, 1, true);
  const remote = holdRead("TESTHOST:" + SID, TID, 2, true);
  const answered = holdRead("TESTHOST:" + SID, "22222222-3333-4444-8555-00000000000b", 3, true);
  answerRead(answered, 3, 1);
  const all = [local, remote, answered];
  linkEdge(all, "TESTHOST", false, hostOf);
  assert.deepEqual([local.lost, remote.lost, answered.lost], [0, 1, 0], "the linked machine's unanswered read alone, never an answered one");
  assert.deepEqual(heldUnread(remote, { unread: true, seenSeq: 0 }), { unread: false, done: false }, "a frame the dead relay still hands on");
  linkEdge(all, "", true, hostOf);
  assert.equal(remote.lost, 1, "this page's own kernel coming back says nothing of the linked machine's link");
  linkEdge(all, "TESTHOST", true, hostOf);
  assert.equal(remote.lost, 2);
  assert.deepEqual(heldUnread(remote, { unread: true, seenSeq: 0 }), { unread: true, done: true }, "the read never arrived: unread, said");
  assert.deepEqual(heldUnread(answered, { unread: true, seenSeq: 0 }), { unread: false, done: false }, "an answered read keeps its exact rule");
  // the read did arrive: the first frame after the link is back says so
  const again = holdRead(SID, TID, 4, true);
  linkEdge([again], "", false, hostOf);
  linkEdge([again], "", false, hostOf);   // the shim fires a down per failed redial
  assert.equal(again.lost, 1);
  linkEdge([again], "", true, hostOf);
  assert.deepEqual(heldUnread(again, { unread: false, seenSeq: 1 }), { unread: false, done: true });
  // an answer that arrives after the drop (it was in the page's queue behind the close) restores the exact rule
  const late = holdRead(SID, TID, 5, true);
  linkEdge([late], "", false, hostOf);
  assert.equal(answerRead(late, 5, 2), true);
  assert.equal(late.lost, 0);
  linkEdge([late], "", true, hostOf);
  assert.deepEqual(heldUnread(late, { unread: true, seenSeq: 1 }), { unread: false, done: false }, "a frame from before the read, after the return");
});

test("the key keeps a host's session and a thread apart", () => {
  assert.notEqual(readKey("TESTHOST:" + SID, TID), readKey(SID, TID));
  assert.equal(readKey(SID, TID), readKey(SID, TID));
});
