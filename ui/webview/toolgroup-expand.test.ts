// Expanding a compact tool-group reveals the run's MEMBERS only: its tools and, since 2026-10-23, the thoughts with words
// that came after a tool in it, each stretch of them as ONE collapsed Thought row. Never the contiguous start..end span: a
// textless thinking block between the tools is no member, so it stays hidden. History: the expansion used to render the
// contiguous span, which leaked every thinking block back in (the user 2026-06-29); the fix walked it.indices, then the tools
// alone, and made "an expanded group never shows thinking" a rule (2026-06-30). The user reversed that rule on 2026-10-23,
// approving the Thought rows. Source pin, mirroring the other render.ts tests; the unit stream itself is executed in
// compact.test.ts.
import { test } from "node:test";
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("an expanded tool-group draws its members' rows (runRows over it.indices), not the contiguous span", () => {
  const tg = RENDER.slice(RENDER.indexOf('if (it.kind === "toolgroup") {', RENDER.indexOf("function appendItem(")), RENDER.indexOf('} else if (it.kind === "thought") {', RENDER.indexOf("function appendItem(")));
  assert.match(tg, /const rows = runRows\(it\.indices, \(i\) => s\.events\[i\]\.kind === "thinking"\);/, "one row per tool, one per stretch of thoughts");
  assert.match(tg, /const child = renderRunRow\(s\.events, row, prevEpoch, turnFoot\(s\.events, row\[row\.length - 1\], working\)\);/, "each row as the chat draws it, the footer on its last member");
  assert.match(tg, /if \(j === rows\.length - 1\) child\.classList\.add\("tg-last"\)/);
  assert.match(tg, /renderToolGroup\(runTools\(s\.events, it\.indices\), prevEpoch, key, open\)/, "the head counts the run's tools only");
  // the old contiguous-span walk that surfaced every in-between thinking block is gone
  assert.doesNotMatch(RENDER, /const start = it\.indices\[0\], end = it\.indices\[it\.indices\.length - 1\];/);
  assert.doesNotMatch(RENDER, /for \(let i = start; i <= end; i\+\+\)/);
  // a stretch of thoughts draws as the collapsed Thought row, a tool as its own turn
  assert.match(RENDER, /function renderRunRow\([^\n]*\n\s*return evs\[row\[0\]\]\.kind === "thinking" \? renderThoughtRow\(evs, row, prevEpoch, \.\.\.foot\) : renderEvent\(evs\[row\[0\]\], prevEpoch, \.\.\.foot\);/);
});
