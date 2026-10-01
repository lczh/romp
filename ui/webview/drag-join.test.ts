// Executable rule for dragging a tab INTO and OUT OF a tag group (the user 2026-09-23; the move, the user
// 2026-09-30): what a drop does to the dragged session's TAGS, given the strip's unions, the zone the
// provisional tab is sitting in, the group its copy came from and whether the copy modifier is held. The
// place a tab is dropped states where the user wants it to appear, and only there: a drop into a group
// MOVES the session there (its tags become exactly that one), Option / Alt held ADDS the tag and keeps the
// rest, the ungrouped row CLEARS every tag, and a drop back into the copy's own group writes nothing. The
// decision and the ops that carry it execute in node (drag-join.ts is pure); the geometry that produces the
// zone, and the writes it drives, are the browser tests' (tests/test_tab_drag_reorder_browser.py,
// tests/test_tab_drag_move_served.py).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { resolveTabDrop, moveToTag, writesTag, leaveWords, dropLocalOps, dropRemoteEdits, type DropZone, type DropHow, type TabDrop } from "./drag-join";
import { viewTagUnion, type SessionViews } from "./session-views";
import { applyTagEdit } from "./views-writes";

// the notes-api demo world, synthetic throughout: placeholder sids, invented session names
const WEB = "11111111-2222-3333-4444-555555555555";
const API = "22222222-3333-4444-5555-666666666666";
const TESTS = "33333333-4444-5555-6666-777777777777";
// three tags: web is in infra AND qa (a copy under each), tests in qa alone, api in none
const VIEWS: SessionViews = {
  active: "all",
  tags: [{ id: "tag-infra", name: "infra", color: "#4EC9B0", members: [WEB] },
         { id: "tag-qa", name: "qa", color: "#c98cff", members: [WEB, TESTS] },
         { id: "tag-release", name: "release", color: "#e5a50a", members: [] }],
};
const U = () => viewTagUnion(VIEWS);
const inGroup = (name: string): DropZone => ({ section: name, trail: false });
const TRAIL: DropZone = { section: null, trail: true };
const HEAD: DropZone = { section: null, trail: false };
/** a drag of the copy drawn in `from` (a group's name, "" for the ungrouped row), with or without the modifier */
const from = (copy: string, add = false): DropHow => ({ from: copy === "" ? TRAIL : inGroup(copy), add });
/** the blob a drop's LOCAL ops leave behind, applied in order through the shared applier the kernel's merge mirrors */
const applyOps = (v: SessionViews, d: TabDrop, sid: string, canSet: boolean): SessionViews =>
  dropLocalOps(d, sid, canSet).reduce((nv, op) => applyTagEdit(nv, op), v);
const holds = (v: SessionViews, sid: string) => viewTagUnion(v).filter((u) => u.members.includes(sid)).map((u) => u.name);
/** the end state both kernels reach: ONE setTags on a kernel with the tagSet cap, the older ops on one without */
const both = (v: SessionViews, d: TabDrop, sid: string) => {
  const a = holds(applyOps(v, d, sid, true), sid), b = holds(applyOps(v, d, sid, false), sid);
  assert.deepEqual(a, b, "the setTags op and the older ops reach the same tags");
  return a;
};

test("an untagged tab dropped into a group MOVES there: it holds exactly that tag, in ONE setTags op", () => {
  const d = resolveTabDrop(U(), inGroup("infra"), API, from(""));
  assert.deepEqual(d, { kind: "move", add: { tag: "infra", tid: "tag-infra", hosts: [] }, remove: [], keep: ["tag-infra"], why: "" });
  assert.equal(writesTag(d), true);
  assert.deepEqual(dropLocalOps(d, API, true), [{ op: "setTags", tids: ["tag-infra"], sids: [API] }]);
  assert.deepEqual(dropLocalOps(d, API, false), [{ op: "addMember", tid: "tag-infra", sids: [API] }],
    "an older kernel: nothing comes off, so the plain add is the whole move");
  assert.deepEqual(both(VIEWS, d, API), ["infra"]);
});

test("a multi-tagged session's copy dragged from one group to ANOTHER holds only the new one", () => {
  // web is in infra and qa; its infra copy is dragged into release: it ends in release alone, so its
  // qa copy leaves the strip too (the user 2026-09-30: the tab takes that group's tag and loses the rest)
  const d = resolveTabDrop(U(), inGroup("release"), WEB, from("infra"));
  assert.equal(d.kind, "move");
  assert.deepEqual(d.add, { tag: "release", tid: "tag-release", hosts: [] });
  assert.deepEqual(d.remove.map((r) => r.tid), ["tag-infra", "tag-qa"], "every OTHER tag it carries comes off, not only the dragged copy's");
  assert.deepEqual(d.keep, ["tag-release"]);
  assert.deepEqual(dropLocalOps(d, WEB, true), [{ op: "setTags", tids: ["tag-release"], sids: [WEB] }],
    "one op, applied by the kernel under its lock: no device reads a half-moved session");
  assert.deepEqual(dropLocalOps(d, WEB, false),
    [{ op: "move", tid_from: "tag-infra", tid_to: "tag-release", sids: [WEB] }, { op: "removeMember", tid: "tag-qa", sids: [WEB] }],
    "an older kernel: its atomic move for the first pair, then the rest removed in turn");
  assert.deepEqual(both(VIEWS, d, WEB), ["release"]);
});

test("…and dragged into a group it ALREADY carries, from another of its groups, it sheds the rest", () => {
  // web's infra copy dropped into qa, which web is in too: the move adds nothing and takes infra off
  const d = resolveTabDrop(U(), inGroup("qa"), WEB, from("infra"));
  assert.deepEqual(d, { kind: "move", add: null, remove: [{ tag: "infra", tid: "tag-infra", hosts: [] }], keep: ["tag-qa"], why: "" });
  assert.deepEqual(dropLocalOps(d, WEB, false), [{ op: "removeMember", tid: "tag-infra", sids: [WEB] }]);
  assert.deepEqual(both(VIEWS, d, WEB), ["qa"]);
});

test("the tab menu's Move to <tag> is the drag's move: a session tagged infra and qa ends [release], in ONE setTags", () => {
  // the user 2026-10-01: the menu's "Move to" took off only the right-clicked copy's group and added the target, so a
  // session under several tags kept the rest; it now makes the drag's move, through the drag's decision and writer
  const d = moveToTag(U(), "release", WEB);
  assert.equal(d.kind, "move");
  assert.deepEqual(d, resolveTabDrop(U(), inGroup("release"), WEB, from("infra")), "the plain drop of its infra copy into release");
  assert.deepEqual(d, resolveTabDrop(U(), inGroup("release"), WEB, from("qa")), "…and of its qa copy: whichever copy was right-clicked");
  assert.deepEqual(dropLocalOps(d, WEB, true), [{ op: "setTags", tids: ["tag-release"], sids: [WEB] }],
    "a kernel with the tagSet cap: one op, the full list");
  assert.deepEqual(holds(applyOps(VIEWS, d, WEB, true), WEB), ["release"], "it holds exactly the tag it was moved to");
  assert.deepEqual(dropRemoteEdits(d, WEB), [], "every tag here is local: no editTag wire");
});

test("…and on an older kernel (no tagSet cap) the menu's Move to reaches the same [release] through the older ops", () => {
  const d = moveToTag(U(), "release", WEB);
  assert.deepEqual(dropLocalOps(d, WEB, false),
    [{ op: "move", tid_from: "tag-infra", tid_to: "tag-release", sids: [WEB] }, { op: "removeMember", tid: "tag-qa", sids: [WEB] }],
    "the atomic move for the first pair, then the rest removed in turn");
  assert.deepEqual(holds(applyOps(VIEWS, d, WEB, false), WEB), ["release"]);
  assert.deepEqual(both(VIEWS, d, WEB), ["release"]);
});

test("the MODIFIER (Option / Alt) adds the group's tag and keeps the rest: the old join", () => {
  const d = resolveTabDrop(U(), inGroup("release"), WEB, from("infra", true));
  assert.deepEqual(d, { kind: "join", add: { tag: "release", tid: "tag-release", hosts: [] }, remove: [], keep: [], why: "" });
  assert.deepEqual(dropLocalOps(d, WEB, true), [{ op: "addMember", tid: "tag-release", sids: [WEB] }],
    "an add is one addMember on every kernel");
  assert.deepEqual(both(VIEWS, d, WEB), ["infra", "qa", "release"], "so a session with several tags shows under each one");
  assert.deepEqual(resolveTabDrop(U(), inGroup("qa"), WEB, from("infra", true)), { kind: "reorder", add: null, remove: [], keep: [], why: "already" },
    "…and with the modifier, a group it already carries writes nothing");
});

test("the UNGROUPED row clears every tag, with or without the modifier: that row is the sessions with none", () => {
  for (const add of [false, true]) {
    const d = resolveTabDrop(U(), TRAIL, WEB, from("qa", add));
    assert.equal(d.kind, "leave");
    assert.deepEqual(d.remove, [{ tag: "infra", tid: "tag-infra", hosts: [] }, { tag: "qa", tid: "tag-qa", hosts: [] }]);
    assert.deepEqual(dropLocalOps(d, WEB, true), [{ op: "setTags", tids: [], sids: [WEB] }], "an empty set: one op for the whole clear");
    assert.deepEqual(dropLocalOps(d, WEB, false),
      [{ op: "removeMember", tid: "tag-infra", sids: [WEB] }, { op: "removeMember", tid: "tag-qa", sids: [WEB] }]);
    assert.deepEqual(both(VIEWS, d, WEB), [], "untagged afterwards, so the strip draws it once, in that row");
  }
});

test("a drop back into the group the copy was drawn in is a reorder: nothing is written", () => {
  // even for a session with other tags: a move needs a different group
  const d = resolveTabDrop(U(), inGroup("qa"), WEB, from("qa"));
  assert.deepEqual(d, { kind: "reorder", add: null, remove: [], keep: [], why: "same" });
  assert.equal(writesTag(d), false, "…so the cue lights nothing and the drop posts nothing");
  assert.deepEqual(dropLocalOps(d, WEB, true), []);
  assert.deepEqual(dropRemoteEdits(d, WEB), []);
  assert.equal(resolveTabDrop(U(), inGroup("qa"), WEB, from("qa", true)).why, "same", "the modifier changes nothing there");
});

test("trail to trail is a plain reorder, and the head of the strip clears nothing", () => {
  assert.equal(resolveTabDrop(U(), TRAIL, API, from("")).why, "untagged");
  assert.equal(resolveTabDrop(U(), HEAD, WEB, from("infra")).why, "head",
    "a drop aimed a little left of the first group's chip finds no header either, and must not strip a tag");
  assert.equal(resolveTabDrop(U(), inGroup("qa"), TESTS).why, "already", "a session whose only tag is the target: nothing to do");
});

test("REVERSIBILITY is one tag, not the set: a moved session dragged back holds the tag it was dragged into", () => {
  const moved = applyOps(VIEWS, resolveTabDrop(U(), inGroup("release"), WEB, from("infra")), WEB, true);
  assert.deepEqual(holds(moved, WEB), ["release"]);
  const back = resolveTabDrop(viewTagUnion(moved), inGroup("infra"), WEB, from("release"));
  assert.deepEqual(holds(applyOps(moved, back, WEB, true), WEB), ["infra"], "infra alone: qa does not come back");
});

test("a section no union holds, and a create still in flight, write nothing", () => {
  assert.equal(resolveTabDrop(U(), inGroup("archived"), API).why, "unknown");
  const pending: SessionViews = { tags: [{ id: "pending-1", name: "draft", members: [] }] };
  assert.equal(resolveTabDrop(viewTagUnion(pending), inGroup("draft"), API).why, "pending",
    "the placeholder id is the one the ack replaces — the kernel would refuse an op addressed by it");
  const flight: SessionViews = { tags: [{ id: "pending-2", name: "draft", members: [API] }] };
  assert.equal(resolveTabDrop(viewTagUnion(flight), TRAIL, API).why, "unknown", "…and a clear stands down on it too");
});

test("a tag homed on another kernel rides the editTag wire, by host, beside the local op", () => {
  const remote: SessionViews = { tags: [{ id: "tag-infra", name: "infra", members: [WEB] }],
                                 remoteTags: [{ id: "TESTHOST:tag-ops", name: "ops", members: [API], host: "TESTHOST" }] };
  // web (local infra) moved into ops (homed on TESTHOST): the add goes to that host, the local tag comes off
  const d = resolveTabDrop(viewTagUnion(remote), inGroup("ops"), WEB, from("infra"));
  assert.deepEqual(d, { kind: "move", add: { tag: "ops", tid: null, hosts: ["TESTHOST"] },
                        remove: [{ tag: "infra", tid: "tag-infra", hosts: [] }], keep: [], why: "" });
  assert.deepEqual(dropLocalOps(d, WEB, true), [{ op: "setTags", tids: [], sids: [WEB] }]);
  assert.deepEqual(dropRemoteEdits(d, WEB), [{ host: "TESTHOST", name: "ops", add: [WEB] }]);
  // api (held only on TESTHOST) moved into local infra: the local add, and the remote removal on its host
  const d2 = resolveTabDrop(viewTagUnion(remote), inGroup("infra"), API, from("ops"));
  assert.deepEqual(dropLocalOps(d2, API, true), [{ op: "setTags", tids: ["tag-infra"], sids: [API] }]);
  assert.deepEqual(dropRemoteEdits(d2, API), [{ host: "TESTHOST", name: "ops", remove: [API] }]);
  // a drop whose tags all live elsewhere posts no local op at all
  const far: SessionViews = { tags: [], remoteTags: [{ id: "TESTHOST:tag-ops", name: "ops", members: [], host: "TESTHOST" }] };
  const d3 = resolveTabDrop(viewTagUnion(far), inGroup("ops"), API, from(""));
  assert.deepEqual(dropLocalOps(d3, API, true), []);
  assert.deepEqual(dropRemoteEdits(d3, API), [{ host: "TESTHOST", name: "ops", add: [API] }]);
  // a REMOVAL goes to every store holding the (name, member) pair — the Tags flyout's rule
  const twoHomes: SessionViews = { tags: [{ id: "tag-ops", name: "ops", members: [API] }],
                                   remoteTags: [{ id: "TESTHOST:tag-ops", name: "ops", members: [API], host: "TESTHOST" },
                                                { id: "OTHERHOST:tag-ops", name: "ops", members: [], host: "OTHERHOST" }] };
  assert.deepEqual(resolveTabDrop(viewTagUnion(twoHomes), TRAIL, API).remove, [{ tag: "ops", tid: "tag-ops", hosts: ["TESTHOST"] }],
    "the host that does NOT hold the pair is left alone");
});

test("a removal addresses the local tag that actually HOLDS the member, not simply the first of the name", () => {
  const twins: SessionViews = { tags: [{ id: "tag-a", name: "infra", members: [API] },
                                       { id: "tag-b", name: "infra", members: [WEB] }] };
  assert.deepEqual(resolveTabDrop(viewTagUnion(twins), TRAIL, WEB).remove, [{ tag: "infra", tid: "tag-b", hosts: [] }],
    "two local tags may share a name; the op must land on the one whose membership is going");
  const intoTwin = resolveTabDrop(viewTagUnion({ tags: [...twins.tags!, { id: "tag-c", name: "qa", members: [WEB] }] }), inGroup("infra"), WEB, from("qa"));
  assert.deepEqual(intoTwin.keep, ["tag-b"], "…and a move into the name keeps the twin already holding it");
});

test("the cue names every tag a release would take off — the whole safety on a gesture with no undo", () => {
  assert.deepEqual(leaveWords(resolveTabDrop(U(), TRAIL, WEB, from("qa"))), { leaving: ["infra", "qa"], more: 0 });
  assert.deepEqual(leaveWords(resolveTabDrop(U(), inGroup("release"), WEB, from("infra"))), { leaving: ["infra", "qa"], more: 0 },
    "a move names the tags it takes off: the copy dragged AND the ones left behind");
  assert.deepEqual(leaveWords(resolveTabDrop(U(), inGroup("release"), WEB, from("infra", true))), { leaving: [], more: 0 },
    "an add takes nothing off: its cue is the ring and the + badge");
  assert.deepEqual(leaveWords(resolveTabDrop(U(), inGroup("infra"), API, from(""))), { leaving: [], more: 0 },
    "a move from no tag takes nothing off either");
  assert.deepEqual(leaveWords(resolveTabDrop(U(), TRAIL, API, from(""))), { leaving: [], more: 0 }, "an untagged tab: that drop is a plain reorder");
});

test("…and the cue's chip run is BOUNDED: a session under dozens of tags shows a few and a count", () => {
  const many: SessionViews = { tags: Array.from({ length: 30 }, (_, i) => ({ id: "t" + i, name: "tag" + i, members: [WEB] })) };
  const w = leaveWords(resolveTabDrop(viewTagUnion(many), TRAIL, WEB, from("tag0")));
  assert.deepEqual(w.leaving, ["tag0", "tag1", "tag2"], "the many-tags rule holds for a drag cue too");
  assert.equal(w.more, 27);
  const mv = leaveWords(resolveTabDrop(viewTagUnion({ tags: [...many.tags!, { id: "tx", name: "fresh", members: [] }] }), inGroup("fresh"), WEB, from("tag0")));
  assert.deepEqual([mv.leaving.length, mv.more], [3, 27], "…a move off thirty tags as well");
});

test("the shared applier's setTags is the kernel's: listed tags gain the session, every other local tag loses it", () => {
  const v = applyTagEdit(VIEWS, { op: "setTags", tids: ["tag-release"], sids: [WEB] });
  assert.deepEqual(v.tags!.map((t) => [t.id, t.members]),
    [["tag-infra", []], ["tag-qa", [TESTS]], ["tag-release", [WEB]]], "other members untouched");
  assert.deepEqual(applyTagEdit(v, { op: "setTags", tids: ["tag-release"], sids: [WEB] }).tags!.map((t) => t.members), [[], [TESTS], [WEB]],
    "idempotent: no duplicate member");
  assert.deepEqual(applyTagEdit(VIEWS, { op: "setTags", tids: [], sids: [WEB] }).tags!.map((t) => t.members), [[], [TESTS], []]);
  // the timeline's hand-mirror of the applier carries the same case (ui/romp-timeline-view.js applyTagEditOp)
  const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
  assert.match(TL, /case 'setTags': \{\s*\n\s*const keep = edit\.tids \|\| \[\];/);
});

test("render.ts wires the rule into the strip's drag: one decision, the cue and the drop off the same zone", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /import \{ resolveTabDrop, moveToTag, writesTag, leaveWords, dropLocalOps, dropRemoteEdits, type TabDrop, type LeaveWords, type DropZone \} from "\.\/drag-join";/);
  // the zone comes from the PROVISIONAL TAB's place in the strip (dropZoneOf), so the cue and the drop
  // cannot disagree — and sectionHeadOf, the group drag's own reader, is the same walk; the copy's origin
  // is its data-copy, which the live reorder never touches
  assert.match(RENDER, /resolveTabDrop\(unions, \{ section: zone\.head\?\.dataset\.group \?\? null, trail: zone\.trail \}, id, \{ from, add \}\)/);
  assert.match(RENDER, /const copy = dragged\.dataset\.copy;/);
  assert.match(RENDER, /function sectionHeadOf\(node: HTMLElement\): HTMLElement \| null \{\s*\n\s*return dropZoneOf\(node\)\.head;/);
  // THE MODIFIER is the drag event's own altKey, read by the dragover for the cue AND by the drop for the write
  assert.match(RENDER, /const \{ drop: d, head \} = tabDropFor\(dragged, draggedId, dragUnions, e\.altKey\);/);
  assert.match(RENDER, /const d = tabDropFor\(dragged, draggedId, viewTagUnion\(effViews\(\)\), e\.altKey\)\.drop;/);
  // the dragover paints the cue, the drop posts the tags
  assert.match(RENDER, /paintDragCue\(tabs, dragged, d, head, dragUnions\);/);
  assert.match(RENDER, /if \(postTabDropTag\(d, draggedId\)\) tabDragCommitted = true;/);
  // ONE setTags only on a kernel that announced it; every other kernel gets the older ops (the caps pattern)
  assert.match(RENDER, /const ops = dropLocalOps\(d, id, kernelCaps\.has\("tagEdit"\) && kernelCaps\.has\("tagSet"\)\);/);
  assert.match(RENDER, /for \(const op of ops\) nv = applyTagEdit\(nv, op\);/, "the optimistic copy through the shared applier");
  assert.match(RENDER, /if \(ops\.length\) for \(const op of ops\) postTagEdit\(nv, op\);/,
    "every op of one drop rides ONE optimistic blob, so the strip never shows a half-moved session");
  // the + badge is up exactly while an add is what the release does, and it and the leave line are pinned to the
  // dragged tab's PLACED box, never the rect a FLIP slide is still carrying from its old slot
  assert.match(RENDER, /const plus = d\.kind === "join";/);
  assert.match(RENDER, /const r = words\.leaving\.length \|\| plus \? placedRect\(dragged\) : null;/);
  // and the cue belongs to the gesture: cleared when the pointer leaves the strip and at dragend
  assert.equal((RENDER.match(/clearDragCue\(\)/g) || []).length, 3, "the definition, the dragleave and the dragend");
  assert.match(RENDER, /if \(dragAddEl\) dragAddEl\.style\.display = "none";\n\}/, "…the badge with it");
});

test("the + badge is the accent's own disc, from tokens, and takes no pointer", () => {
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  const rule = CSS.match(/\.tab-drag-add \{[^}]*\}/);
  assert.ok(rule, "styles.css carries the badge's rule");
  assert.match(rule![0], /background: var\(--accent\); color: var\(--accent-fg\);/);
  assert.match(rule![0], /pointer-events: none;/);
  assert.match(rule![0], /position: fixed;/, "fixed: it takes no space in the strip, so nothing reflows under the drag");
});

test("the docs state the move, the modifier's add and the clear, in the user's terms", () => {
  const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
  const prose = (s: string) => new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
  const GUIDE = read("docs", "guide.md"), REF = read("docs", "reference.md");
  assert.match(GUIDE, prose("Drag a tab onto a tag group to move that session there: it takes the group's tag, loses its "
    + "others and lands where you dropped it. Hold Option (Alt on Windows and Linux) as you let go to add the tag and "
    + "keep the rest instead"));
  assert.match(GUIDE, prose("Drag a tab onto the ungrouped row and it loses every tag it had, since that row is the "
    + "sessions with no tags."));
  assert.doesNotMatch(GUIDE, prose("keeping its other tags, so a session with several tags shows under each one"),
    "the join the guide used to state is the modifier's now, never the plain drag's");
  assert.match(REF, prose("drag its copy from one group to another and it ends under the new group alone, so its copies "
    + "leave the other groups."));
  assert.match(REF, prose("Hold **Option** (**Alt** on Windows and Linux) as you let go to **add** the group's tag "
    + "instead and keep the rest"));
  assert.match(REF, prose("Drop a tab on the ungrouped row and it loses **every** tag instead, with or without the key: "
    + "that row is not a group but the sessions carrying no tags, so nothing short of clearing them would put it there."));
  assert.match(REF, prose("A move or a clear is one write on your kernel, so another device never sees the session half-moved"));
  assert.match(REF, prose("which of the two gestures you get depends on what you picked up, never on where you dropped it"));
  assert.match(REF, prose("pick **Move to <tag>** under **Tags**: one click makes the same move as dragging the tab there (below), so "
    + "the session takes that tag and loses every other tag it carries, whichever of its copies you right-clicked. The row's "
    + "**+** adds the tag and keeps the rest, like a drag with Option held (below)."), "the tab menu's Move to is the drag's move");
  assert.doesNotMatch(REF, prose("drops the tag of the group you right-clicked it in, leaving its other tags alone"),
    "the menu's old one-tag swap is gone from the docs");
});
