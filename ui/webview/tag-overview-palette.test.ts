// "Show tag overview" in the command palette (the user 2026-10-02: a group header's click only folds now, so the section
// at a glance opens from the tag's menu and from here). palette-main.ts's openTagOverviewPick is lifted from its own
// source and run against a stand-in chat column: the pick lists the column's tag groups in strip order (the page's own
// __rompTagSections), each a row in the tag's colour with its member count, and a pick posts the tag to that column
// (render.ts's showOverview arm, pinned in tab-snapshot-pane.test.ts); a column that did not answer, the phone layout
// and a strip with no groups each say so in the list instead of opening it empty. The notes-api demo world.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const MAIN = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "palette-main.ts"), "utf8");
const FROM = MAIN.indexOf("  function openTagOverviewPick(): void {");
const TO = MAIN.indexOf('  registerCommand({ id: "chat.tagOverview", title: "Show tag overview", run: openTagOverviewPick });');

type Pick = { placeholder: string; items: Array<{ title: string; color?: string; dim?: string; run: () => void }> };
function lift(contentWindow: unknown): { picks: Pick[]; posts: unknown[]; open: () => void } {
  assert.ok(FROM > 0 && TO > FROM, "palette-main.ts: the pick or its registration moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(MAIN.slice(FROM, TO), { loader: "ts" }).code;
  const picks: Pick[] = [], posts: unknown[] = [];
  const open = new Function("chatPane", "chatPost", "palette", js + "\nreturn openTagOverviewPick;")(
    () => (contentWindow === undefined ? null : { contentWindow }), (m: unknown) => { posts.push(m); }, { openPick: (p: Pick) => { picks.push(p); } });
  return { picks, posts, open };
}

test("the pick lists the column's tag groups in strip order, in the tag's colour with the count, and a pick posts the tag to that column", () => {
  const h = lift({ __rompTagSections: () => [{ name: "web", color: "#4EC9B0", count: 2 }, { name: "tests", color: "", count: 1 }] });
  h.open();
  assert.equal(h.picks.length, 1);
  assert.equal(h.picks[0].placeholder, "Show the overview of a tag…");
  assert.deepEqual(h.picks[0].items.map((i) => [i.title, i.color, i.dim]), [["web", "#4EC9B0", "2 sessions"], ["tests", undefined, "1 session"]]);
  h.picks[0].items[1].run();
  assert.deepEqual(h.posts, [{ romp: "showOverview", tag: "tests" }], "the menu's path, in the column last worked in (chatPost)");
});

test("loud, never an empty list: no answer from the column, the phone layout and a strip with no groups each say so", () => {
  const cases: Array<[unknown, string]> = [
    [undefined, "Couldn't read the tag groups: the chat pane didn't answer"],
    [{}, "Couldn't read the tag groups: the chat pane didn't answer"],
    [{ __rompTagSections: () => { throw new Error("detached"); } }, "Couldn't read the tag groups: the chat pane didn't answer"],
    [{ __rompTagSections: () => null }, "The phone layout has no tag overview"],
    [{ __rompTagSections: () => [] }, "No tag groups on this chat column's strip"],
  ];
  for (const [win, title] of cases) {
    const h = lift(win);
    h.open();
    assert.deepEqual(h.picks[0].items.map((i) => i.title), [title]);
    h.picks[0].items[0].run();
    assert.deepEqual(h.posts, [], "the note's row does nothing");
  }
});

test("the command is listed always (no `when`), under the words the menu uses", () => {
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.tagOverview", title: "Show tag overview", run: openTagOverviewPick \}\);/);
});
