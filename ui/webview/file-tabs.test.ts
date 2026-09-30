// FILE TABS (the user 2026-09-30, who wanted a file from the viewer kept open as a tab of its own beside the sessions, to
// flip between them): the model (file-tabs.ts) executed. Placement beside the session a file came from, the dedupe on
// host + path, close, a drag's landing, the keyboard's walk with the files in it, the labels, and the store's sync with
// the kernel (the folds' protocol: cache, publish once heard, a pending delta merged over the kernel's list). Synthetic
// sessions and paths only (the notes-api demo world).
import { test } from "node:test";
import assert from "node:assert/strict";
import { addFileTab, anchorOf, emptyFileTabs, fileDropPlace, fileTabId, fileTabLabels, hearSharedFileTabs, isFileTabId, mergeFileTabs,
         moveFileTab, navWithFiles, parseFileTabs, readFileTabs, removeFileTab, setFileTabsPublisher, weaveFileTabs, writeFileTabs,
         FILETABS_CAP, FILETABS_EVENT, FILETABS_PENDING_KEY, FILETABS_SHARED_KEY, type FileTabsState } from "./file-tabs";
import { routeOutbound } from "./federation";
import * as fs from "node:fs";
import * as path from "node:path";

const WEB = "11111111-2222-3333-4444-555555555501", API = "11111111-2222-3333-4444-555555555502", TESTS = "11111111-2222-3333-4444-555555555503";
const FAR_API = "TESTHOST:11111111-2222-3333-4444-555555555504";
const all = (..._ids: string[]) => (sid: string) => [WEB, API, TESTS, FAR_API].includes(sid);

function withStore<T>(seed: Record<string, unknown>, fn: (store: Map<string, string>, events: string[]) => T): T {
  const g: any = globalThis;
  const store = new Map<string, string>();
  for (const [k, v] of Object.entries(seed)) store.set(k, typeof v === "string" ? v : JSON.stringify(v));
  const events: string[] = [];
  const win: any = new EventTarget();
  win.addEventListener(FILETABS_EVENT, () => events.push(FILETABS_EVENT));
  const saved = [g.localStorage, g.window];
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); }, removeItem: (k: string) => { store.delete(k); } };
  g.window = win;
  try { return fn(store, events); } finally { [g.localStorage, g.window] = saved; setFileTabsPublisher(null); }
}

test("one tab per file per machine: the id is stable, colon-free (never read as a session's host), and apart by host and path", () => {
  const a = fileTabId("", "~/notes-api/README.md");
  assert.equal(a, fileTabId("", "~/notes-api/README.md"), "every device mints the same id for the same file");
  assert.ok(isFileTabId(a) && !a.includes(":"), a);
  assert.notEqual(a, fileTabId("TESTHOST", "~/notes-api/README.md"), "the same path on another machine is another file");
  assert.notEqual(a, fileTabId("", "~/notes-api/docs/README.md"));
  assert.ok(!isFileTabId(WEB) && !isFileTabId(FAR_API), "a session id is never a file tab's");
  // a RELATIVE path is resolved against its session's folder by the kernel: the same words from two sessions may be
  // two files, so the session is part of its identity; an absolute or ~ path is the machine's alone
  assert.notEqual(fileTabId("", "docs/guide.md", WEB), fileTabId("", "docs/guide.md", API));
  assert.equal(fileTabId("", "~/notes-api/README.md", WEB), fileTabId("", "~/notes-api/README.md", API));
  assert.equal(fileTabId("", "/srv/notes-api/README.md", WEB), fileTabId("", "/srv/notes-api/README.md", API));
});

test("a file opens beside the session it came from, after that session's other files; the same file again is the same tab", () => {
  let st = emptyFileTabs();
  const r1 = addFileTab(st, "~/notes-api/src/app.py", WEB, WEB);
  assert.ok(r1.added && !r1.full); st = r1.st;
  const r2 = addFileTab(st, "~/notes-api/README.md", WEB, WEB); st = r2.st;
  const r3 = addFileTab(st, "~/notes-api/tests/test_app.py", TESTS, TESTS); st = r3.st;
  const again = addFileTab(st, "~/notes-api/src/app.py", API, API);
  assert.equal(again.id, r1.id, "the file already open is that tab, whichever session asks");
  assert.equal(again.added, false);
  assert.equal(again.st, st, "and nothing changes");
  const woven = weaveFileTabs([{ id: WEB }, { id: API }, { id: TESTS }], (it) => it.id, st.tabs, all());
  assert.deepEqual(woven.items.map((it) => ("file" in it ? it.file : it.id)), [WEB, r1.id, r2.id, API, TESTS, r3.id],
    "right after its session, the second file after the first, each beside its own session");
  assert.deepEqual(woven.trail, []);
  const remote = addFileTab(st, "~/notes-api/src/app.py", FAR_API, FAR_API);
  assert.ok(remote.added && remote.id !== r1.id, "the same path on the remote machine is its own tab");
});

test("closing a tab takes it off the list; the cap refuses out loud rather than dropping one", () => {
  let st = emptyFileTabs();
  const r = addFileTab(st, "~/notes-api/src/app.py", WEB, WEB); st = r.st;
  assert.deepEqual(removeFileTab(st, r.id).tabs, []);
  assert.equal(removeFileTab(st, "ftab-nothing"), st, "an unknown id changes nothing");
  let full = emptyFileTabs();
  for (let i = 0; i < FILETABS_CAP; i++) full = addFileTab(full, "~/notes-api/f" + i + ".md", WEB, WEB).st;
  const over = addFileTab(full, "~/notes-api/one-more.md", WEB, WEB);
  assert.equal(over.full, true); assert.equal(over.added, false); assert.equal(over.st.tabs.length, FILETABS_CAP);
});

test("a file tab follows its session: every copy of it under several tags, none while it is folded away, and the opener when its neighbour left", () => {
  let st = emptyFileTabs();
  const r = addFileTab(st, "~/notes-api/src/app.py", WEB, WEB); st = r.st;
  // web under two tags: two copies, each with the file after it; a header is not a session
  type Item = { head: string } | { id: string };
  const idOf = (it: Item) => ("id" in it ? it.id : null);
  const plan: Item[] = [{ head: "infra" }, { id: WEB }, { id: API }, { head: "qa" }, { id: WEB }, { id: TESTS }];
  const woven = weaveFileTabs(plan, idOf, st.tabs, all());
  assert.deepEqual(woven.items.map((it) => ("file" in it ? "f" : "head" in it ? "h:" + it.head : it.id)), ["h:infra", WEB, "f", API, "h:qa", WEB, "f", TESTS]);
  // folded away (the plan does not show web): its file shows nowhere, and nothing trails
  const folded = weaveFileTabs<Item>([{ head: "infra" }, { id: API }], idOf, st.tabs, all());
  assert.equal(folded.items.filter((it) => "file" in it).length, 0); assert.deepEqual(folded.trail, []);
  // moved beside api, then api leaves the strip: back beside web, the session it was opened from; web gone too: it trails
  st = moveFileTab(st, r.id, API, false, null);
  assert.deepEqual(anchorOf(st.tabs[0], all()), { anchor: API, lead: false });
  assert.deepEqual(anchorOf(st.tabs[0], (s) => s === WEB), { anchor: WEB, lead: false });
  assert.equal(anchorOf(st.tabs[0], () => false), null);
  const gone = weaveFileTabs([{ id: TESTS }], (it) => it.id, st.tabs, (s) => s === TESTS);
  assert.deepEqual(gone.trail.map((t) => t.id), [r.id], "a file whose sessions both left the strip is never dropped: it trails");
});

test("a drag lands a file beside the session before it, or at the head of a group before the session after it, in its place among that session's files", () => {
  let st = emptyFileTabs();
  const a = addFileTab(st, "~/notes-api/a.md", WEB, WEB); st = a.st;
  const b = addFileTab(st, "~/notes-api/b.md", WEB, WEB); st = b.st;
  const c = addFileTab(st, "~/notes-api/c.md", TESTS, TESTS); st = c.st;
  const order = (s: FileTabsState) => navWithFiles([WEB, API, TESTS], s.tabs, all(), true);
  // c dropped between web's two files: after web, just before b
  let place = fileDropPlace(WEB, API, b.id)!;
  let moved = moveFileTab(st, c.id, place.anchor, place.lead, place.before);
  assert.deepEqual(order(moved), [WEB, a.id, c.id, b.id, API, TESTS]);
  // dropped at the head of a group (no session before it there): before the session after it
  place = fileDropPlace(null, API, null)!;
  assert.deepEqual(place, { anchor: API, lead: true, before: null });
  moved = moveFileTab(st, a.id, place.anchor, place.lead, place.before);
  assert.deepEqual(order(moved), [WEB, b.id, a.id, API, TESTS, c.id]);
  // a `before` on another run is ignored: the end of the run
  moved = moveFileTab(st, c.id, WEB, false, "ftab-elsewhere");
  assert.deepEqual(order(moved), [WEB, a.id, b.id, c.id, API, TESTS]);
  assert.equal(fileDropPlace(null, null, null), null, "no session in reach: nothing to sit beside");
  assert.equal(moveFileTab(st, "ftab-nothing", WEB, false, null), st);
});

test("the keyboard walks the strip's tabs with the files in it: each beside its session, the trailing ones last where the first column paints them", () => {
  let st = emptyFileTabs();
  const w = addFileTab(st, "~/notes-api/src/app.py", WEB, WEB); st = w.st;
  const orphan = addFileTab(st, "~/notes-api/old.md", "11111111-2222-3333-4444-5555555555ff", "11111111-2222-3333-4444-5555555555ff"); st = orphan.st;
  assert.deepEqual(navWithFiles([WEB, API], st.tabs, all(), true), [WEB, w.id, API, orphan.id]);
  assert.deepEqual(navWithFiles([WEB, API], st.tabs, all(), false), [WEB, w.id, API], "a later column paints no trailing files, and walks none");
  assert.deepEqual(navWithFiles([API, WEB], [], all(), true), [API, WEB], "no file tabs: the session walk, untouched");
});

test("labels: the basename, and the parent path as far as it tells two same-named files apart; another machine's file is apart by its host", () => {
  let st = emptyFileTabs();
  const a = addFileTab(st, "~/notes-api/web/index.ts", WEB, WEB); st = a.st;
  const b = addFileTab(st, "~/notes-api/api/index.ts", API, API); st = b.st;
  const c = addFileTab(st, "~/notes-api/README.md", WEB, WEB); st = c.st;
  const d = addFileTab(st, "~/notes-api/web/index.ts", FAR_API, FAR_API); st = d.st;
  const e = addFileTab(st, "/srv/one/src/index.ts", API, API); st = e.st;
  const f = addFileTab(st, "/srv/two/src/index.ts", API, API); st = f.st;
  const L = fileTabLabels(st.tabs);
  assert.deepEqual(L.get(c.id), { base: "README.md", hint: "" });
  assert.deepEqual(L.get(a.id), { base: "index.ts", hint: "web" });
  assert.deepEqual(L.get(b.id), { base: "index.ts", hint: "api" });
  assert.deepEqual(L.get(d.id), { base: "index.ts", hint: "" }, "alone of its name on its machine: its host prefix tells it apart");
  assert.deepEqual(L.get(e.id), { base: "index.ts", hint: "one/src" }, "as much of the path as it takes");
  assert.deepEqual(L.get(f.id), { base: "index.ts", hint: "two/src" });
});

test("a stored list reads back whole, and anything malformed costs that entry alone, never the strip", () => {
  const good = addFileTab(emptyFileTabs(), "~/notes-api/README.md", WEB, WEB).st;
  assert.deepEqual(parseFileTabs(JSON.stringify(good)), good);
  assert.deepEqual(parseFileTabs("{not json"), emptyFileTabs());
  assert.deepEqual(parseFileTabs({ tabs: "nope" }), emptyFileTabs());
  assert.deepEqual(parseFileTabs(null), emptyFileTabs());
  const mixed = parseFileTabs({ v: 1, tabs: [good.tabs[0], { id: WEB, path: "x", anchor: WEB }, { id: "ftab-x" }, good.tabs[0], 7] });
  assert.deepEqual(mixed.tabs, good.tabs, "a non-file id, a path-less entry, a duplicate and junk are dropped");
});

test("a write caches, publishes once heard, and tells every pane; before it is heard it waits as a pending change", () => {
  withStore({}, (store, events) => {
    const st = addFileTab(emptyFileTabs(), "~/notes-api/README.md", WEB, WEB).st;
    writeFileTabs(st);
    assert.deepEqual(readFileTabs(), st);
    assert.equal(store.get(FILETABS_PENDING_KEY), JSON.stringify(emptyFileTabs()), "no publisher yet: the base the change was made on is kept");
    assert.deepEqual(events, [FILETABS_EVENT]);
    const sent: FileTabsState[] = [];
    setFileTabsPublisher((f) => sent.push(f));
    writeFileTabs(st);
    assert.deepEqual(sent, [], "an unchanged write publishes nothing");
    const next = removeFileTab(st, st.tabs[0].id);
    writeFileTabs(next);
    assert.deepEqual(sent, [next], "a change is published");
    assert.equal(events.length, 2);
  });
});

test("hearing the kernel: its list is adopted; a kernel with none gets this browser's; a pending change lands over the kernel's list", () => {
  const mine = addFileTab(emptyFileTabs(), "~/notes-api/README.md", WEB, WEB).st;
  const theirs = addFileTab(emptyFileTabs(), "~/notes-api/src/app.py", API, API).st;
  // the kernel keeps a list: adopted, whatever this browser held
  withStore({ [FILETABS_SHARED_KEY]: mine }, (_store, events) => {
    let installed = false;
    hearSharedFileTabs(theirs, () => assert.fail("nothing to publish"), () => { installed = true; });
    assert.deepEqual(readFileTabs(), theirs); assert.ok(installed); assert.deepEqual(events, [FILETABS_EVENT]);
  });
  // the kernel keeps none (null): this browser's go up, so nobody's open files are lost in the move
  withStore({ [FILETABS_SHARED_KEY]: mine }, () => {
    const sent: FileTabsState[] = [];
    hearSharedFileTabs(null, (f) => sent.push(f), () => {});
    assert.deepEqual(sent, [mine]);
  });
  // a file opened here before any page heard: merged over the kernel's list, both kept
  withStore({ [FILETABS_SHARED_KEY]: mine, [FILETABS_PENDING_KEY]: emptyFileTabs() }, (store) => {
    const sent: FileTabsState[] = [];
    hearSharedFileTabs(theirs, (f) => sent.push(f), () => {});
    assert.equal(sent.length, 1);
    assert.deepEqual(sent[0].tabs.map((t) => t.path).sort(), ["~/notes-api/README.md", "~/notes-api/src/app.py"]);
    assert.equal(store.get(FILETABS_PENDING_KEY), undefined, "the pending change is spent");
  });
});

test("the merge: added here is added, closed here is closed, moved here takes its place; the kernel's own changes stand", () => {
  let base = emptyFileTabs();
  const a = addFileTab(base, "~/notes-api/a.md", WEB, WEB); base = a.st;
  const b = addFileTab(base, "~/notes-api/b.md", WEB, WEB); base = b.st;
  let local = removeFileTab(base, a.id);                                  // closed here
  local = addFileTab(local, "~/notes-api/c.md", WEB, WEB).st;            // opened here
  local = moveFileTab(local, b.id, API, false, null);                     // moved here
  let served = addFileTab(base, "~/notes-api/d.md", TESTS, TESTS).st;     // opened on another device meanwhile
  const out = mergeFileTabs(base, local, served);
  const byPath = new Map(out.tabs.map((t) => [t.path, t]));
  assert.ok(!byPath.has("~/notes-api/a.md"), "closed here: closed");
  assert.ok(byPath.has("~/notes-api/c.md"), "opened here: open");
  assert.ok(byPath.has("~/notes-api/d.md"), "opened elsewhere: open");
  assert.equal(byPath.get("~/notes-api/b.md")!.anchor, API, "moved here: in its new place");
  served = removeFileTab(served, b.id);
  assert.ok(mergeFileTabs(base, base, served).tabs.every((t) => t.id !== b.id), "closed elsewhere and untouched here: closed");
});

test("the page's writes reach the LOCAL kernel whole, and the strip weaves the files through the one model", () => {
  const msg = { type: "setViewFiles", files: { v: 1, tabs: [] } };
  assert.deepEqual(routeOutbound(msg as any), [{ host: "", msg }]);
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \("files" in m\) \{\s*\n\s*hearSharedFileTabs\(m\.files, \(f\) => this\.outbound\(\{ type: "setViewFiles", files: f \}\), \(fn\) => \{ w\.__rompPublishViewFiles = fn; \}\);/);
  assert.match(FED, /delete \(window as any\)\.__rompPublishViewFiles;/, "a dropped connection withdraws the publisher with the others");
  assert.match(RENDER, /const woven = weaveFileTabs\(paint, /, "the strip's paint weaves through file-tabs.ts");
  assert.match(RENDER, /return navWithFiles\(visibleOrder\(\), tabs, stripLists, !COL\);/, "the keyboard's walk too, over the walk visibleOrder is");
});

test("the palette: one command presses the viewer's own pop-out, and the session switcher lists the open files after the sessions", () => {
  const PAL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "palette-main.ts"), "utf8");
  assert.match(PAL, /registerCommand\(\{ id: "file\.openAsTab", title: "Open this file as a tab", when: \(\) => !!popOutButton\(\), run: \(\) => \{ popOutButton\(\)\?\.click\(\); \} \}\);/,
    "listed while a viewer is up; its run is the button's own click, one path");
  assert.match(PAL, /querySelector\("#romp-fileview \.fileview-popout:not\(\[hidden\]\)"\)/, "in any pane: the chat columns, the feed, the Files pane");
  assert.match(PAL, /\}\)\.concat\(fileTabItems\(\)\);/, "the switcher's rows: the sessions, then the files");
  assert.match(PAL, /run: \(\) => chatPost\(\{ type: "jumpFileTab", id: r\.id \}\)/);
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /else if \(m\.type === "jumpFileTab" && typeof m\.id === "string"\) \{ revealSelfPane\(\); selectFileTab\(m\.id\); \}/);
  assert.match(RENDER, /\(window as any\)\.__rompFileTabList = \(\) => fileTabsNow\(\)\.map/);
});
