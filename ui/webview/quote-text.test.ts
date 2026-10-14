// What a quote of a transcript passage SAYS (review of #231, 2026-10-14): the content's text, never the page's controls,
// labels or what is not shown. The range's own text (every text node's data) put a code block's Copy button into a
// triple-click on its last line ("x = refill(cache)Copy"), and a selection across two replies picked up a collapsed tool
// row's label, its fold toggle and its folded input and output, and a notice's source label. render.ts's quoteText reads
// the passage as it reads instead, and stops once it holds more than a quote can carry.
// EXECUTED: quoteText, quoteSkips and quoteTextTrim, lifted whole from render.ts, over a small DOM stand-in shaped like the chat's
// rendering (no jsdom here, the thread-selection-scope.test.ts precedent); the real page in Chromium is
// quote-context-browser.test.ts. Synthetic text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { QUOTE_CAP } from "./quote-context";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const requireCjs = createRequire(path.join(process.cwd(), "package.json"));

// ── the DOM stand-in: elements with a tag, classes, a style and children; text nodes with data ──────────────────────
let visits = 0;                                                      // how many nodes the walk stepped onto
class FakeNode {
  parentNode: FakeEl | null = null;
  constructor(public nodeType: number) {}
  get parentElement(): FakeEl | null { return this.parentNode; }
  get nextSibling(): FakeNode | null {
    visits++;
    const p = this.parentNode;
    return p ? p.childNodes[p.childNodes.indexOf(this) + 1] || null : null;
  }
  contains(n: FakeNode | null): boolean { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
}
class FakeText extends FakeNode { constructor(public data: string) { super(3); } }
class FakeEl extends FakeNode {
  childNodes: FakeNode[] = [];
  classes: Set<string>;
  open = false;
  constructor(public tagName: string, cls: string, public style: Record<string, string>) {
    super(1);
    this.classes = new Set(cls.split(/\s+/).filter(Boolean));
  }
  get classList() { return { contains: (c: string) => this.classes.has(c) }; }
  get firstChild(): FakeNode | null { visits++; return this.childNodes[0] || null; }
  get previousElementSibling(): FakeEl | null {
    const p = this.parentNode;
    if (!p) return null;
    for (let i = p.childNodes.indexOf(this) - 1; i >= 0; i--) if (p.childNodes[i] instanceof FakeEl) return p.childNodes[i] as FakeEl;
    return null;
  }
  // the simple selectors the page's lists use: a tag, a class, or "a, b" of them
  matches(sel: string): boolean {
    return sel.split(",").map((s) => s.trim()).some((s) => s.startsWith(".") ? this.classes.has(s.slice(1)) : s.toUpperCase() === this.tagName);
  }
}
// h("p.md", { display: "none" }, "text", h(...)): an element; a string child is a text node
function h(spec: string, ...kids: Array<string | FakeNode | Record<string, string>>): FakeEl {
  const [tag, ...cls] = spec.split(".");
  const style = kids.length && typeof kids[0] === "object" && !(kids[0] instanceof FakeNode) ? kids.shift() as Record<string, string> : {};
  const e = new FakeEl(tag.toUpperCase(), cls.join(" "), style);
  for (const k of kids as Array<string | FakeNode>) {
    const n = typeof k === "string" ? new FakeText(k) : k;
    n.parentNode = e;
    e.childNodes.push(n);
  }
  return e;
}
const textOf = (root: FakeNode, data: string): FakeText => {
  const all: FakeText[] = [];
  const walk = (n: FakeNode): void => { if (n instanceof FakeText) all.push(n); else if (n instanceof FakeEl) n.childNodes.forEach(walk); };
  walk(root);
  const t = all.find((x) => x.data.includes(data));
  assert.ok(t, "premise: the stand-in holds " + JSON.stringify(data));
  return t!;
};
const BODY = h("body");

// ── the page's functions, lifted whole ──────────────────────────────────────────────────────────────────────────────
const liftFrom = (text: string, name: string): string => {
  const i = text.indexOf(`function ${name}(`);
  assert.ok(i > 0, name);
  let depth = 0, j = text.indexOf("{\n", i);
  for (; j < text.length; j++) { if (text[j] === "{") depth++; else if (text[j] === "}") { depth--; if (!depth) break; } }
  return text.slice(i, j + 1);
};
const constOf = (name: string): string => {
  const m = new RegExp(`\\nconst ${name} = [\\s\\S]*?;[^\\n]*\\n`).exec(RENDER);
  assert.ok(m, "render.ts's " + name);
  return m![0];
};
const SRC = ["QUOTE_CHROME", "QUOTE_BREAKS", "QUOTE_PARAS"].map(constOf).join("") + ["quoteSkips", "quoteText", "quoteTextTrim"].map((n) => liftFrom(RENDER, n)).join("\n");
const JS = requireCjs("esbuild").transformSync(SRC, { loader: "ts", target: "es2020", legalComments: "none" }).code;
const quoteText = new Function("Node", "document", "getComputedStyle", "QUOTE_CAP", JS + "\nreturn quoteText;")(
  { TEXT_NODE: 3, ELEMENT_NODE: 1 }, { body: BODY },
  (e: FakeEl) => ({ display: e.style.display || "block", visibility: e.style.visibility || "visible", userSelect: e.style.userSelect || "auto" }),
  QUOTE_CAP) as (r: object, cap?: number) => string;

type Point = [FakeNode, number];
const range = (a: Point, b: Point) => ({ startContainer: a[0], startOffset: a[1], endContainer: b[0], endOffset: b[1] });
const read = (root: FakeEl, a: Point, b: Point, cap?: number): string => {
  BODY.childNodes = [root];
  root.parentNode = BODY;
  visits = 0;
  return quoteText(range(a, b), cap);
};

// a reply with a code block (its rows, its Copy button), a closed fold and a table, then a collapsed tool row, a user's turn
// shown as a notice, and the next reply
function chat(): FakeEl {
  const code = h("pre.has-copy",
    h("code", h("span.cl", h("span.ct", "def refill(cache):")), h("span.cl", h("span.ct", "    return cache.load()")),
      h("span.cl", h("span.ct", "x = refill(cache)"))),
    h("button.code-copy", "Copy"));
  const reply = h("div.turn.turn-assistant", h("span.dot"),
    h("div.md", h("p", "Call ", h("code", "refill(cache)"), " before the load."), "\n", code, "\n",
      h("details", h("summary", "More detail"), "Text inside the closed fold."), "\n",
      h("table", h("tbody", h("tr", h("td", "latency"), "\n", h("td", "The time it takes.")), "\n",
        h("tr", h("td", "throughput"), "\n", h("td", "Requests per second")))), "\n",
      h("p", "The reply closes here.")),
    h("div.rail-hit"));
  const tool = h("div.turn.turn-tool", h("span.dot"),
    h("div.tool-head", h("span.tool-label", "List the files"), h("span.tool-fold-toggle", { userSelect: "none" }, "1 line")),
    h("div.tool-io.tool-fold-body", { display: "none" },
      h("div.io-row", h("span.io-label", { userSelect: "none" }, "IN"), h("pre.io-pre", "ls -la")),
      h("div.io-row", h("span.io-label", { userSelect: "none" }, "OUT"), h("pre.io-pre", "total 0"))));
  const user = h("div.turn.turn-user", h("span.dot"),
    h("div.notice", h("div.notice-head", h("span.notice-glyph", h("svg")), h("span.notice-src", "system"), h("span.notice-gist", "Thanks, carry on."))));
  const next = h("div.turn.turn-assistant", h("div.md", h("p", "The next reply starts here.")), h("div.fork-spot", h("button.msg-fork", "fork")));
  const thread = h("div.thread", reply, tool, user, next);
  return thread;
}

test("a triple-click on a code block's last line: the line, never the Copy button the range runs into", () => {
  const root = chat();
  const pre = textOf(root, "x = refill").parentElement!.parentElement!.parentElement!.parentElement!;   // ct > cl > code > pre
  assert.equal(pre.tagName, "PRE", "premise");
  assert.equal(read(root, [textOf(root, "x = refill"), 0], [pre, pre.childNodes.length]), "x = refill(cache)");
});

test("a code block's rows are its lines, indent kept; a single line is trimmed", () => {
  const root = chat();
  assert.equal(read(root, [textOf(root, "def refill"), 0], [textOf(root, "x = refill"), 17]),
    "def refill(cache):\n    return cache.load()\nx = refill(cache)");
  assert.equal(read(root, [textOf(root, "    return"), 0], [textOf(root, "    return"), 23]), "return cache.load()");
});

test("across two replies: a collapsed tool row's label, toggle and folded input and output, and a notice's labels stay out", () => {
  const root = chat();
  assert.equal(read(root, [textOf(root, "The reply closes"), 0], [textOf(root, "The next reply"), 21]),
    "The reply closes here.\n\nThanks, carry on.\n\nThe next reply starts");
});

// review of #234 (2026-10-14): a tool group's line, its caret and the edits' totals as much as its summary, and an opened
// edit's line-number gutters, which a <pre> holds and whose user-select: none the walk does not read there
test("a tool group's caret and totals stay out, and an opened diff's line numbers; a diff row keeps its sign", () => {
  const ns = { userSelect: "none" };
  const row = (o: string, n: string, sign: string, code: string) =>
    h("div.diff-row", h("span.diff-gut.diff-gut-old", ns, o), h("span.diff-gut.diff-gut-new", ns, n), h("span.diff-sign", ns, sign), h("span.diff-code", code));
  const group = h("div.turn.turn-toolgroup", h("span.dot"),
    h("div.toolgroup-line", h("span.toolgroup-caret", "\u25b8"), " ", h("span.toolgroup-head", "Ran a command, edited a file"), " ",
      h("span.tool-totals", h("span.tool-plus", "+1"), " ", h("span.tool-minus", "-1"))));
  const edit = h("div.turn.turn-tool.fold-open", h("span.dot"),
    h("div.tool-head", h("span.tool-label", "Edited notes.py"), h("span.tool-fold-toggle", ns, h("span.tool-totals", "+1 -1"))),
    h("pre.io-pre.fold-pre.diff-fold.tool-fold-body", row("10", "10", "", "a = 1"), row("11", "", "-", "b = 2"), row("", "11", "+", "b = 3")));
  const root = h("div.thread", h("div.turn.turn-assistant", h("div.md", h("p", "The first reply ends here."))), group, edit,
    h("div.turn.turn-assistant", h("div.md", h("p", "The second reply starts here."))));
  assert.equal(read(root, [textOf(root, "The first reply"), 0], [textOf(root, "The second reply"), 23]),
    "The first reply ends here.\n\na = 1\n-b = 2\n+b = 3\n\nThe second reply starts");
  assert.equal(read(root, [textOf(root, "a = 1"), 0], [textOf(root, "b = 3"), 5]), "a = 1\n-b = 2\n+b = 3");
});

test("a closed fold shows its summary alone; an open one its contents too", () => {
  const root = chat();
  const a: Point = [textOf(root, "x = refill"), 0], b: Point = [textOf(root, "latency"), 7];
  assert.equal(read(root, a, b), "x = refill(cache)\n\nMore detail\n\nlatency");
  const details = textOf(root, "More detail").parentElement!.parentElement!;
  details.open = true;
  assert.equal(read(root, a, b), "x = refill(cache)\n\nMore detail\nText inside the closed fold.\n\nlatency");
});

test("a table's cells are a tab apart, its rows a line apart; inline code is the prose's", () => {
  const root = chat();
  assert.equal(read(root, [textOf(root, "latency"), 0], [textOf(root, "Requests"), 8]), "latency\tThe time it takes.\nthroughput\tRequests");
  assert.equal(read(root, [textOf(root, "Call "), 0], [textOf(root, " before the load."), 17]), "Call refill(cache) before the load.");
});

test("a range that starts inside a label leaves the label out; one wholly inside a label reads as nothing", () => {
  const root = chat();
  assert.equal(read(root, [textOf(root, "List the files"), 5], [textOf(root, "Thanks"), 6]), "Thanks");
  assert.equal(read(root, [textOf(root, "List the files"), 0], [textOf(root, "List the files"), 14]), "");
});

test("the walk stops once it holds more than the cap: a long passage costs what a quote can carry", () => {
  const paras = Array.from({ length: 2000 }, (_, i) => h("p", `Sentence ${i} talks about item ${i}.`));
  const md = h("div.md", ...paras);
  const root = h("div.thread", h("div.turn", md));
  const all = read(root, [textOf(root, "Sentence 0 "), 0], [textOf(root, "Sentence 1999 "), 10], 500);
  assert.ok(all.length > 500 && all.length < 600, "it read past the cap and stopped: " + all.length);
  assert.ok(visits < 200, "it stepped onto " + visits + " nodes of the 4,000 the passage spans");
});
