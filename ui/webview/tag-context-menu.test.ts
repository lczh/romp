// The tag menu (the user 2026-10-01, who wanted to right-click a tag and get a menu to rename it, delete it and so on).
// Executed here: the rows the menu offers in each place it opens (tagMenuSections), the delete's words, the rename's
// verdict, what one gesture posts (planTagUnionEdit: the local op, the editTag per remote home, the optimistic copy
// with the name carried through the order and the lenses), the folds a rename keeps and a delete drops (tab-groups.ts),
// and the optimistic applier's carry (views-writes.ts). Pinned in the source: the page's wiring (render.ts), the
// phone picker's forwarding (kernel.py) and the sheet's rules. The served lab (tests/test_tag_menu_served.py) drives
// the real page. Synthetic notes-api names, host TESTHOST.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewTagUnion, type SessionViews, type TagUnion } from "./session-views";
import { tagMenuSections, deleteTagWords, tagRenameVerdict, planTagUnionEdit, TAG_NAME_MAX } from "./tag-context-menu";
import { applyTagEdit, carryTagOrder, carryTagNames } from "./views-writes";
import { parseTabGroups, setSectionCollapsed, isSectionCollapsed, setPinned, isPinned, sectionRef, carrySectionToName,
         forgetSection, restoreSection, followTagRenames, tagRenames, type TabGroupsState } from "./tab-groups";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const MENU = ui("webview", "tag-context-menu.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

const SID = (n: number) => `${String(n).repeat(8)}-1111-2222-3333-444444444444`;
const V: SessionViews = {
  active: "all",
  tags: [{ id: "g1", name: "web", color: "#4EC9B0", members: [SID(1), SID(2)] },
         { id: "g2", name: "api", color: "#c98cff", members: [SID(3)] },
         { id: "g3", name: "docs", color: "#e5a50a", members: [] }],
  remoteTags: [{ id: "TESTHOST:t7", host: "TESTHOST", name: "tests", color: "#9cd2ff", members: ["TESTHOST:" + SID(5)] },
               { id: "TESTHOST:t8", host: "TESTHOST", name: "api", color: "#c98cff", members: ["TESTHOST:" + SID(6)] }],
  tagOrder: ["docs", "web", "tests", "api"],
  actives: { chat: { tags: ["web"] }, outline: { tags: ["web", "api"] }, timeline: { all: true } },
  seq: 7,
};
const union = (v: SessionViews, name: string): TagUnion => viewTagUnion(v).find((u) => u.name === name)!;
const rowsOf = (s: ReturnType<typeof tagMenuSections>) => s.map((sec) => sec.map((r) => r.label));

test("the rows: Rename… first; on a group header the fold as it renders; Show only this tag unless the filter already is; Configure tags…; Delete tag… last, alone, in the danger dress", () => {
  const head = tagMenuSections({ name: "web", members: 2, folded: false, only: false, configure: true });
  assert.deepEqual(rowsOf(head), [["Rename…"], ["Fold group", "Show only this tag", "Configure tags…"], ["Delete tag…"]]);
  assert.equal(head[2][0].danger, true);
  assert.equal(head[2][0].sub, "2 sessions lose it; the sessions stay", "the delete row says what goes and what stays");
  assert.equal(head[1][1].sub, "filter the tabs to its 2 sessions");
  const folded = tagMenuSections({ name: "web", members: 1, folded: true, only: true, configure: true });
  assert.deepEqual(rowsOf(folded), [["Rename…"], ["Open group", "Configure tags…"], ["Delete tag…"]], "folded: Open; already the filter: no Show only");
  assert.equal(folded[2][0].sub, "1 session loses it; the sessions stay", "the sub-line's count (one session)");
  const chip = tagMenuSections({ name: "docs", members: 0, folded: null, only: false, configure: true });
  assert.deepEqual(rowsOf(chip), [["Rename…"], ["Show only this tag", "Configure tags…"], ["Delete tag…"]], "a chip has no fold");
  assert.equal(chip[2][0].sub, "no session holds it");
  assert.deepEqual(rowsOf(tagMenuSections({ name: "x", members: 0, folded: null, only: null, configure: false })), [["Rename…"], ["Delete tag…"]],
    "a surface with no filter and no dialog route: the empty section and its divider go");
  assert.deepEqual(tagMenuSections({ name: "web", members: 2, folded: false, only: false, configure: true }).flat().map((r) => r.act),
    ["rename", "fold", "only", "configure", "delete"]);
});

test("the overview's row (the user 2026-10-02: a header's click only folds, so the section at a glance opens here): Show overview after the fold on a header, Close overview while that tag's shows, none off a header or on the phone", () => {
  const head = tagMenuSections({ name: "web", members: 2, folded: false, only: false, configure: true, overview: false });
  assert.deepEqual(rowsOf(head), [["Rename…"], ["Fold group", "Show overview", "Show only this tag", "Configure tags…"], ["Delete tag…"]], "beside the fold, named as the view's heading names it (Overview of)");
  assert.deepEqual([head[1][1].act, head[1][1].sub], ["overview", "what each of its sessions is doing"]);
  const shown = tagMenuSections({ name: "web", members: 2, folded: true, only: false, configure: true, overview: true });
  assert.deepEqual(rowsOf(shown)[1], ["Open group", "Close overview", "Show only this tag", "Configure tags…"], "its overview up: the way back to the session being read");
  assert.deepEqual([shown[1][1].act, shown[1][1].sub], ["closeOverview", "back to the session you were reading"]);
  for (const overview of [null, undefined])
    assert.deepEqual(rowsOf(tagMenuSections({ name: "docs", members: 0, folded: null, only: false, configure: true, overview })), [["Rename…"], ["Show only this tag", "Configure tags…"], ["Delete tag…"]],
      "a chip (or the phone, where no overview shows): no row");
  // the page: the row only from a header and never on the phone, Close while this tag's overview shows; the picks take the
  // view's own doors (tab-snapshot-pane.test.ts runs them over the lifted page)
  assert.match(RENDER, /const overview = folded === null \|\| phoneLayout\(\) \? null : snapView === name;/);
  assert.match(RENDER, /\{ name, members: g\.members\.length, folded, only, configure: true, overview \}/);
  assert.match(RENDER, /\} else if \(act === "overview"\) \{\s*\n\s*showOverview\(name\);[^\n]*\n\s*\} else if \(act === "closeOverview"\) \{\s*\n\s*leaveSnapshot\(\);/);
});

test("the delete asks first and counts the sessions that lose the tag; the sessions stay", () => {
  assert.deepEqual(deleteTagWords("web", 3), { title: "Delete the tag “web”?", confirm: "Delete tag",
    detail: "3 sessions lose this tag. The sessions stay, with any other tags they have." });
  assert.equal(deleteTagWords("web", 1).detail, "1 session loses this tag. The sessions stay, with any other tags they have.");
  assert.equal(deleteTagWords("docs", 0).detail, "No session holds it.");
});

test("the rename's verdict: empty or unchanged does nothing; a name another tag has (local or remote) is refused with the reason; the store's basis (clamped, stripped)", () => {
  const u = viewTagUnion(V);
  assert.deepEqual(tagRenameVerdict(u, "web", "   "), { cancel: true });
  assert.deepEqual(tagRenameVerdict(u, "web", " web "), { cancel: true });
  assert.deepEqual(tagRenameVerdict(u, "web", "docs"), { error: "a tag named “docs” already exists" });
  assert.deepEqual(tagRenameVerdict(u, "web", "tests"), { error: "a tag named “tests” already exists" }, "a remote host's tag counts: the groups would merge");
  assert.deepEqual(tagRenameVerdict(u, "web", "  frontend "), { name: "frontend" });
  assert.deepEqual(tagRenameVerdict(u, "web", "x".repeat(60)), { name: "x".repeat(TAG_NAME_MAX) });
});

test("a local tag's rename: ONE targeted op by its id, no remote edit, the copy renamed with its place in the order and every filter carried", () => {
  const p = planTagUnionEdit(V, union(V, "web"), { rename: "frontend" });
  assert.deepEqual(p.ops, [{ op: "rename", tid: "g1", newName: "frontend" }]);
  assert.deepEqual(p.remote, []);
  assert.deepEqual(p.nv.tagOrder, ["docs", "frontend", "tests", "api"], "the group keeps its slot");
  assert.deepEqual(p.nv.actives, { chat: { tags: ["frontend"] }, outline: { tags: ["frontend", "api"] }, timeline: { all: true } });
  assert.deepEqual(union(p.nv, "frontend").members, [SID(1), SID(2)], "the same sessions under the new name");
  assert.deepEqual(p.lens, { tagOrder: ["docs", "frontend", "tests", "api"], actives: p.nv.actives }, "what a kernel without tagNameCarry is written");
  assert.equal(V.tags![0].name, "web", "the input is never mutated");
});

test("a recolor posts the colour and moves no name", () => {
  const p = planTagUnionEdit(V, union(V, "api"), { color: "#54B204" });
  assert.deepEqual(p.ops, [{ op: "recolor", tid: "g2", color: "#54B204" }]);
  assert.deepEqual(p.remote, [{ host: "TESTHOST", name: "api", color: "#54B204" }], "the union's remote half too: a tag is its name");
  assert.equal(p.lens, null);
  assert.equal(union(p.nv, "api").color, "#54B204");
});

test("a delete: the local op and each remote home's, the name gone from the order and the filters (a filter on it alone goes back to All)", () => {
  const p = planTagUnionEdit(V, union(V, "api"), { delete: true });
  assert.deepEqual(p.ops, [{ op: "delete", tid: "g2" }]);
  assert.deepEqual(p.remote, [{ host: "TESTHOST", name: "api", delete: true }]);
  assert.deepEqual(p.nv.tagOrder, ["docs", "web", "tests"]);
  assert.deepEqual(p.nv.actives!.outline, { tags: ["web"] });
  assert.equal(viewTagUnion(p.nv).some((u) => u.name === "api"), false, "the group is gone from the copy");
  const w = planTagUnionEdit(V, union(V, "web"), { delete: true });
  assert.deepEqual(w.nv.actives!.chat, { all: true }, "never a filter naming nothing");
});

test("a tag only an attached kernel holds: no local op, the editTag to its home, the mirrored copy carrying the name", () => {
  const p = planTagUnionEdit(V, union(V, "tests"), { rename: "qa" });
  assert.deepEqual(p.ops, []);
  assert.deepEqual(p.remote, [{ host: "TESTHOST", name: "tests", rename: "qa" }]);
  assert.deepEqual(p.nv.tagOrder, ["docs", "web", "qa", "api"]);
  assert.deepEqual(union(p.nv, "qa").members, ["TESTHOST:" + SID(5)]);
  // a create still in flight has no id: no op is posted for it
  const pend: SessionViews = { ...V, tags: [{ id: "pending-x", name: "new", color: "", members: [] }] };
  assert.deepEqual(planTagUnionEdit(pend, union(pend, "new"), { rename: "fresh" }).ops, []);
});

test("the name carry: a rename takes the old slot, a delete drops it; a name another tag still bears keeps its slot, the new one right behind", () => {
  assert.deepEqual(carryTagOrder(["a", "web", "b"], "web", "frontend", false), ["a", "frontend", "b"]);
  assert.deepEqual(carryTagOrder(["a", "web", "b"], "web", "frontend", true), ["a", "web", "frontend", "b"]);
  assert.deepEqual(carryTagOrder(["a", "web", "b"], "web", "b", false), ["a", "b"], "a name the order holds keeps its own slot");
  assert.deepEqual(carryTagOrder(["a", "b"], "web", "frontend", false), ["a", "b"], "no slot to keep");
  assert.deepEqual(carryTagOrder(["a", "web"], "web", null, false), ["a"]);
  assert.deepEqual(carryTagOrder(undefined, "web", "x", false), []);
  // the optimistic applier carries it on the copy: the local web renamed while the remote api half stays
  const nv = applyTagEdit(V, { op: "rename", tid: "g2", newName: "service" });
  assert.deepEqual(nv.tagOrder, ["docs", "web", "tests", "api", "service"], "the remote api still makes that group: it keeps its slot");
  assert.deepEqual(nv.actives!.outline, { tags: ["web", "api", "service"] });
  const d = applyTagEdit(V, { op: "delete", tid: "g1" });
  assert.deepEqual([d.tagOrder, d.actives!.chat], [["docs", "tests", "api"], { all: true }]);
  const bare: SessionViews = { active: "all", tags: [] };
  assert.equal(carryTagNames(bare, "web", "x", false), false, "nothing named: nothing changed");
});

// the folds, as the strip keeps them (tab-groups.ts)
const sec = (v: SessionViews, name: string) => sectionRef(union(v, name));
const fold = (st: TabGroupsState, ...names: string[]) => names.reduce((s, n) => setSectionCollapsed(s, n, true), st);

test("at the gesture a rename's fold and pins go to the new name too, the old name's kept until the kernel's blob lands; refused, put back", () => {
  let st = fold(parseTabGroups(null), "web", "docs");
  st = setPinned(st, sec(V, "web"), SID(1), true);
  const at = carrySectionToName(st, sec(V, "web"), "frontend");
  assert.equal(isSectionCollapsed(at, "frontend"), true, "the renamed group stays folded while the answer is on its way");
  assert.equal(isSectionCollapsed(at, "web"), true, "another device still shows the old name: its fold stands");
  assert.equal(isPinned(at, { name: "frontend", localId: "g1" }, SID(1)), true, "the pinned member still shows through");
  assert.deepEqual(carrySectionToName(parseTabGroups(null), sec(V, "web"), "frontend").collapsed, [], "an open group stays open");
  // the kernel refused: the new name's state goes back to what it was, the old name's untouched
  const undone = restoreSection(at, st, { name: "frontend", localId: "g1" });
  assert.deepEqual([undone.collapsed, undone.pinned], [st.collapsed, st.pinned]);
});

test("the follow on the adopted blob moves the fold from the old name to the new one, on every device, and keeps it where a union still bears the old name", () => {
  const V2: SessionViews = { ...V, tags: [{ ...V.tags![0], name: "frontend" }, V.tags![1], V.tags![2]], seq: 8 };
  const st = fold(parseTabGroups(null), "web");
  const out = followTagRenames(st, tagRenames(V, V2), viewTagUnion(V2), V2);
  assert.deepEqual(out.collapsed, ["frontend"], "a device that never saw the gesture folds the renamed group too");
  // the renaming page carried it at the gesture: the follow only retires the old entry
  const out2 = followTagRenames(carrySectionToName(st, sec(V, "web"), "frontend"), tagRenames(V, V2), viewTagUnion(V2), V2);
  assert.deepEqual(out2.collapsed, ["frontend"]);
  // an open group the user opened under the new name before the answer: the old name had no fold of its own left, nothing moves
  const opened = setSectionCollapsed(st, "web", false);
  assert.deepEqual(followTagRenames(opened, tagRenames(V, V2), viewTagUnion(V2), V2).collapsed, []);
  // the split: the local api renamed while the remote api half stays, so the api group remains; both folded
  const V3: SessionViews = { ...V, tags: [V.tags![0], { ...V.tags![1], name: "service" }, V.tags![2]], seq: 8 };
  const split = followTagRenames(fold(parseTabGroups(null), "api"), tagRenames(V, V3), viewTagUnion(V3), V3);
  assert.deepEqual(split.collapsed.slice().sort(), ["api", "service"]);
  // archived starts folded: renamed, the group stays folded under its new name
  const VA: SessionViews = { active: "all", tags: [{ id: "g9", name: "archived", color: "", members: [SID(4)] }], seq: 3 };
  const VB: SessionViews = { ...VA, tags: [{ ...VA.tags![0], name: "old work" }], seq: 4 };
  assert.deepEqual(followTagRenames(parseTabGroups(null), tagRenames(VA, VB), viewTagUnion(VB), VB).collapsed, ["old work"]);
});

test("a delete takes the group's fold and pins with it (a tag made later under the name starts fresh); refused, put back", () => {
  let st = fold(parseTabGroups(null), "web", "api");
  st = setPinned(st, sec(V, "web"), SID(2), true);
  const gone = forgetSection(st, sec(V, "web"));
  assert.deepEqual([gone.collapsed, gone.pinned], [["api"], []]);
  assert.equal(forgetSection(gone, sec(V, "web")), gone, "nothing names it: no write");
  const back = restoreSection(gone, st, sec(V, "web"));
  assert.deepEqual([back.collapsed.slice().sort(), back.pinned], [["api", "web"], st.pinned]);
});

test("the page's wiring: the strip's header opens the menu through the stable #tabs delegate on its RENDERED fold, and from the menu key; the flyout's tag rows and the filter chips open it too", () => {
  assert.match(RENDER, /tabs\.addEventListener\("contextmenu", \(e\) => \{[\s\S]{0,300}closest\?\.\("\.tab-group-head\[data-group\]"\)[\s\S]{0,200}showTagMenu\(e\.clientX, e\.clientY, h\.dataset\.group \|\| "", h\.dataset\.folded === "1"\);/);
  assert.match(RENDER, /if \(e\.key === "ContextMenu" \|\| \(e\.key === "F10" && e\.shiftKey\)\) \{[\s\S]{0,200}showTagMenu\(r\.left \+ 12, r\.bottom, name, collapsed, true\);/);
  assert.equal((RENDER.match(/tagRowMenu\(row, g\);/g) || []).length, 2, "the flyout's holding rows and its other tags' rows");
  assert.match(RENDER, /row\.addEventListener\("contextmenu", \(e2\) => \{ e2\.preventDefault\(\); e2\.stopPropagation\(\); showTagMenu\(e2\.clientX, e2\.clientY, g\.name, null\); \}\);/);
  assert.equal((RENDER.match(/onContext: \(name, ev\) => showTagMenu\(ev\.clientX, ev\.clientY, name, null\)/g) || []).length, 2, "the strip's filter chips and the phone header's");
  // every pick resolves the tag by name when it runs; the menu is the shared card
  assert.match(RENDER, /\{ pick: \(act\) => tagMenuPick\(act, name, folded !== null\), recolor: \(c\) => \{ const u = unionNamed\(name\); if \(u\) commitTagUnionEdit\(u, \{ color: c \}\); \} \}/);
  assert.match(MENU, /const menu = menuCard\(/);
  assert.match(MENU, /return showMenuCard\(menu, x, y, opts\);/);
});

test("the page's writes: one editTag per remote home, the targeted op on the one optimistic copy, the folds moved at the gesture and put back on a refusal, and the order written by the page only on a kernel without the carry", () => {
  const fn = RENDER.slice(RENDER.indexOf("function commitTagUnionEdit("), RENDER.indexOf("// THE INLINE RENAME on a group header"));
  assert.match(fn, /writeTabGroups\(carrySectionToName\(before, sec, edit\.rename\)\);/);
  assert.match(fn, /writeTabGroups\(forgetSection\(before, sec\)\);/);
  assert.match(fn, /for \(const r of plan\.remote\) vscodeApi\?\.postMessage\(\{ type: "editTag", edit: r \}\);/);
  assert.match(fn, /for \(const op of plan\.ops\) last = postTagEdit\(plan\.nv, op\);/);
  assert.match(fn, /if \(plan\.lens && kernelCaps\.has\("tagEdit"\) && !kernelCaps\.has\("tagNameCarry"\)\) postLens\(plan\.lens\);/);
  assert.match(fn, /if \(last && undo\) refusalUndo\.set\(last, undo\);/);
  assert.match(RENDER, /const undo = typeof m\.writeId === "string" \? refusalUndo\.get\(m\.writeId\) : undefined;[\s\S]{0,120}if \(undo && m\.ok === false\) undo\(\);/);
  assert.match(RENDER, /function postTagEdit\(nv: SessionViews, edit: TagEditOp, newId\?: string\): string \{/);
  // the inline rename holds the strip's renders like the tab rename, and the field keeps its own presses
  const ren = RENDER.slice(RENDER.indexOf("function startTagRename("), RENDER.indexOf("// Keyboard nav on a focused tab"));
  assert.match(ren, /renameActive = true;/);
  assert.match(ren, /renameActive = false;/);
  assert.match(ren, /for \(const ev of \["click", "mousedown", "pointerdown", "dblclick", "contextmenu", "dragstart"\]\) input\.addEventListener\(ev, \(e\) => e\.stopPropagation\(\)\);/);
  assert.match(ren, /if \(phoneLayout\(\)\) return false;/, "the phone's strip is hidden: the prompt asks there");
  assert.match(RENDER, /if \(!\(fromHead && startTagRename\(name\)\)\)\s*\n\s*openTagRenamePrompt\(/, "inline only from a header; a chip's Rename… asks where it was clicked");
});

test("the phone picker forwards a heading's long press to the strip's hidden header; the card rides above the list; the sheet's rules use tokens", () => {
  assert.match(KERNEL, /list\.addEventListener\('contextmenu',function\(e\)\{var hd=e\.target&&e\.target\.closest\?e\.target\.closest\('\.mhead'\):null;if\(!hd\)return;\s*\ne\.preventDefault\(\);e\.stopPropagation\(\);var rh=realHead\(hd\.getAttribute\('data-group'\)\);\s*\nif\(rh\)rh\.dispatchEvent\(new MouseEvent\('contextmenu',\{bubbles:true,cancelable:true,clientX:e\.clientX,clientY:e\.clientY\}\)\);\}\);/);
  assert.match(CSS, /\n\.ctx-menu\.ctx-tagmenu \{ z-index: 210; \}/, "over #mlist (z 200)");
  assert.match(KERNEL, /"#mlist\{display:none;position:absolute;left:8px;right:8px;top:100%;margin-top:4px;z-index:200;"/);
  for (const sel of [".ctx-tagmenu-head", ".tag-rename", ".tag-rename.bad"]) {
    const body = CSS.match(new RegExp("\\n" + sel.replace(/\./g, "\\.") + " \\{([^}]*)\\}"))![1];
    assert.doesNotMatch(body, /#[0-9a-fA-F]{3,6}\b|rgba?\(/, "tokens only in " + sel);
  }
  assert.match(KERNEL, /KERNEL_WS_CAPS = \([^)]*"tagNameCarry"[^)]*\)/);
});
