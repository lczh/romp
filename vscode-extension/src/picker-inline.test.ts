// The live AskUserQuestion picker renders INLINE in the chat transcript (the user 2026-06-27): it's the last
// child of #content and scrolls WITH the chat history, instead of a fixed mini-window below it — so a tall
// picker never buries the context above it. Keyboard control is unchanged (the card still owns keydown). And
// "Chat about this" is a real selectable answer again, not filtered-out TUI chrome. Source pins (fleet/render
// run as modules, no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKEL = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("#live-ask lives INSIDE #content in the skeleton (both the VS Code body and the kernel's ported copy)", () => {
  assert.match(SKEL, /<div id="content"><div id="live-ask" style="display:none"><\/div><\/div>/);
  assert.match(KERNEL, /<div id="content"><div id="live-ask" style="display:none"><\/div><\/div>/);
});

test("new threads insert BEFORE the picker so it stays the last child of #content", () => {
  // …and before the live-status foot line when it exists (2026-10-08), which itself sits just above the picker
  assert.match(SRC, /content\?\.insertBefore\(elv, document\.getElementById\("live-status"\) \|\| document\.getElementById\("live-ask"\)\)/);
  assert.match(SRC, /content\.insertBefore\(n, document\.getElementById\("live-ask"\)\);/);
});

test("renderLiveAsk keeps the picker last and reveals it when the user is parked at the bottom", () => {
  // re-append to the end of #content if a thread landed after it
  assert.match(SRC, /if \(content && host\.parentNode === content && host !== content\.lastChild\) content\.appendChild\(host\)/);
  // scroll the transcript to the bottom to reveal the picker — only when stuck to the bottom (never yank a
  // user scrolled up reading context)
  assert.match(SRC, /const v = activeId \? views\.get\(activeId\) : undefined;\s*\n\s*if \(content && \(!v \|\| v\.stick\)\) writeScroll\(content, content\.scrollHeight, "liveask-reveal", true, topBefore\);/);
});

test("#live-ask is no longer a fixed 42vh mini-region — it flows in the scroll", () => {
  assert.doesNotMatch(CSS, /#live-ask \{[^}]*max-height: 42vh/);
  assert.match(CSS, /#live-ask \{[^}]*padding: 0 24px 6px var\(--thread-gutter\); \}/);
});

test("#live-ask keeps the thread's left gutter, so the jump cluster floating over it never covers the card", () => {
  // the host sits beside the threads, not inside one, so it reserves the gutter itself (the user 2026-10-18: the card ran to
  // the pane's edge under the cluster); the gutter is the one token .thread pads with, so the two cannot drift apart
  const host = CSS.match(/#live-ask \{([^}]*)\}/)?.[1] || "";
  assert.match(host, /max-width: var\(--chat-col\); margin: 0 auto;/, "the thread's column");
  assert.ok(host.includes("padding: 0 24px 6px var(--thread-gutter);"), `the thread's gutter: ${host}`);
});

test("every row of the chat column pads with the ONE gutter token, so none drifts off the turns' column and under the cluster", () => {
  // the review of #306 (2026-10-18): the 56px was copied into four rules and the host-offline foot had none of it. The token is
  // declared once per theme block (geometry: the same value in both, as --chat-col), and every row reads it
  const decl = [...CSS.matchAll(/--thread-gutter: ([^;]+);/g)].map((m) => m[1]);
  assert.deepEqual(decl, ["56px", "56px"], ":root and body.theme-light declare it, and nothing else does");
  assert.doesNotMatch(CSS, /padding: \S+ 24px \S+ 56px;/, "no row pads with the gutter's value by hand");
  const rule = (sel: string) => CSS.match(new RegExp("(?:^|\\n)" + sel.replace(/[.#]/g, "\\$&") + " \\{([^}]*)\\}"))?.[1] || "";
  for (const [sel, pad] of [[".thread", "0 24px 0"], [".statusline", "7px 24px 0"], ["#live-ask", "0 24px 6px"],
                            ["#live-status", "0 24px 0"], [".notice-foot", "6px 24px 10px"]]) {
    const r = rule(sel);
    assert.match(r, /max-width: var\(--chat-col\);\s*margin: 0 auto;/, `${sel} takes the thread's column: ${r}`);
    assert.ok(r.includes(`padding: ${pad} var(--thread-gutter);`), `${sel} pads with the gutter token: ${r}`);
  }
  // the rail's time stamp spans the whole gutter, its right edge 3px past the turn's left edge whatever the gutter
  assert.match(CSS, /\.time-marker \{[^}]*left: calc\(3px - var\(--thread-gutter\)\); width: var\(--thread-gutter\);/);
});

test("a long unbroken token in a waiting question wraps inside the card", () => {
  // the review of #306 (2026-10-18): a path or URL in the question ran past the card's right border and was clipped at the
  // pane's edge; the options' labels and descriptions already wrapped anywhere (the served lab measures the card)
  assert.match(CSS, /\.ask-qtext \{[^}]*overflow-wrap: anywhere;/);
  assert.match(CSS, /\.ask-warn \{[^}]*overflow-wrap: anywhere;/);
});

test("'Chat about this' is a selectable answer again (only Type-something / Submit are filtered)", () => {
  assert.match(SRC, /function isMetaOption\(label: string\): boolean \{ return \/\^\\s\*\(type something\|submit\$\)\/i\.test\(label\.trim\(\)\); \}/);
  // and it is NOT excluded any longer
  assert.doesNotMatch(SRC, /type something\|chat about\|submit/);
});

test("the picker card still owns the keyboard (focus + keydown), so inline-ness doesn't lose keyboard control", () => {
  // (on the card's own surface since thread-live-1: the chat's, or a comment popover's)
  assert.match(SRC, /card\.addEventListener\("keydown", \(e\) => onSingleKey\(s, e\)\);/);
  assert.match(SRC, /card\.addEventListener\("keydown", \(e\) => onMultiKey\(s, e\)\);/);
  assert.match(SRC, /querySelector\("#live-ask \.ask-card"\)/);   // Enter from a tab still focuses the card
});
