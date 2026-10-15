// Landing on a comment thread's passage (the user 2026-10-15, who clicked a thread's tick on the scroll rail and got the
// thread's box with no way to reach the passage): the tick, the tinted rail line and the open thread's quote land the
// PASSAGE (aligned at the top of the pane, its highlight flashed) and then open the thread beside it; the jump cluster's
// comment rows take the same landing and open nothing. Source pins for the wiring (no jsdom for the chat renderer); the
// behaviour is driven in a real browser by tests/test_comment_tick_passage_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SANITIZE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "md-sanitize.ts"), "utf8");
const fn = (name: string): string => RENDER.split("\nfunction " + name + "(")[1].split("\n}\n")[0];

test("the landing aligns on the thread's highlight and flashes it, and re-finds it after a rebuild", () => {
  const land = fn("scrollToAnchor");
  assert.match(land, /const mark = pendingAnchorMark && pendingAnchorMark\.uuid === uuid \? pendingAnchorMark\.tid : null; pendingAnchorMark = null;/,
    "keyed by the anchor: a landing that waits on history keeps it, any other landing drops it");
  assert.match(land, /const markAt = mark \? passageTarget\(target, mark\) : null;\s*\n\s*const markEl = markAt \? markAt\.el : null;/);
  assert.match(land, /landOn\(target, uuid, markEl \?\? quoteEl \?\? firstTextAtomBelow\(target\), quote, mark\);\s*\n\s*passageLanded\(uuid, mark, markEl, target, !!markAt\?\.folded\);/,
    "the passage is what the one write aligns on and what flashes; then the thread opens");
  assert.match(fn("threadMarkIn"), /for \(const t of threadsByAnchor\(threads\)\.get\(th\.anchorUuid\) \|\| \[\]\) ensureCommentMark\(turn, t\);/,
    "a turn the landing just rendered gets its highlights back first (applyCommentMarks' per-turn step, without its repaints)");
  assert.match(RENDER, /s\.at = \(s\.mark \? passageTarget\(turn, s\.mark, rebuilt\)\?\.el : null\) \?\?/, "the settle re-finds the highlight after a rebuild");
  assert.match(CSS, /mark\.cmt-hl\.anchor-flash \{ animation: cmt-land-flash 1\.6s ease-out; border-radius: 0; \}/,
    "the passage's flash fades back to the highlight's own tint");
});

test("every entry point that opens a thread lands its passage first, and the cluster's comment rows land it too", () => {
  assert.match(RENDER, /cmtjump: \(elx\) => \{\s*\n\s*const tid = elx\.dataset\.tid;\s*\n\s*if \(!tid \|\| !activeId\) return;\s*\n\s*passageLanding\(activeId, tid\);/, "the rail tick");
  assert.match(RENDER, /if \(um\?\.dataset\.tid && activeId\) \{ passageLanding\(activeId, um\.dataset\.tid\); return; \}/, "the tinted rail line");
  assert.match(RENDER, /flashedAnchor = null; pendingAnchorMark = \{ uuid, tid \};/, "the jump cluster's landing (landThread)");
  // the cluster measures a thread's stop where its landing puts the reader, so a step from a landed passage moves on
  assert.match(fn("navStops"), /const at = t && c\.tid \? \(t\.querySelector\(`mark\.cmt-hl\[data-tid="\$\{cssEscape\(c\.tid\)\}"\]`\) as HTMLElement \| null\) \?\? t : t;/);
  const go = fn("passageLanding");
  assert.match(go, /if \(!th \|\| sid !== activeId\) return;/, "never a session switch: every entry point sits on the session's own view");
  assert.match(go, /if \(scrollToAnchor\(uuid\) \|\| anchorPendingOlder\) return;/, "landed, or on its way through the deep links' history loading");
  assert.match(go, /if \(landTrail\[landTrail\.length - 1\] === "pointer-fetch-busy"\) return;/, "another jump in flight refuses this one, with its own toast");
  assert.match(go, /openBesidePassage\(sid, tid, null, PASSAGE_LOST\);/, "a passage the transcript does not hold is said in the box");
  const landed = fn("passageLanded");
  assert.match(landed, /if \(go\.uuid !== uuid \|\| go\.tid !== mark\) return;/, "another landing ends the wait");
  assert.match(landed, /queueMicrotask\(\(\) => openBesidePassage\(go\.sid, go\.tid, passage, folded \? PASSAGE_FOLDED : lost \? PASSAGE_WORDS_LOST : null\)\);/, "the box after the landing");
});

test("the box opens beside the passage, and the open thread's quote is the way back to it", () => {
  assert.match(fn("openBesidePassage"), /if \(pop && passage && passage\.isConnected\) \{ placeBesidePassage\(pop, tid, passage\); watchBeside\(tid, passage\); \}/);
  assert.match(fn("placeBesidePassage"), /const to = cmtPopBeside\(r, at, window\.innerHeight\);/);
  assert.match(RENDER, /if \(th\) paintCmtLost\(pop, th\.tid\);/, "a full rebuild keeps the box's note");
  // both forms of the quote: the standalone block before the thread's events land, and the context on its opening message
  assert.match(RENDER, /if \(!create\) passageLink\(quote\);/);
  assert.match(RENDER, /passageLink\(ctx\);\s*\n\s*quoteHost\.insertBefore\(ctx, quoteHost\.firstChild\);/);
  // click-safe: the press's release acts, so a refill of the thread's list between press and release cannot eat it; a drag is not a press
  assert.match(RENDER, /document\.addEventListener\("pointerup", \(e\) => \{[\s\S]{0,400}?Math\.abs\(e\.clientX - p\.x\) > 4 \|\| Math\.abs\(e\.clientY - p\.y\) > 4\) return;\s*\n\s*flash\(q\);[^\n]*\n\s*passageLanding\(cur\.sid, cur\.th\.tid\);/);
  assert.match(CSS, /\.cmt-pop \.cmt-quote\[data-passage\] \{ cursor: pointer; \}/);
});

// The review of 2026-10-15: three shapes of passage the landing got wrong, driven in a real browser by
// tests/test_comment_passage_reveal_served.py; these pin the wiring.
test("a passage in a closed fold is brought out before the landing aligns on it, and one still hidden lands its fold's heading", () => {
  const target = fn("passageTarget");
  assert.match(target, /if \(reveal\) revealAncestors\(m\);\s*\n\s*const at = shownPassage\(m\);\s*\n\s*return at \? \{ el: at, folded: at !== m \} : null;/);
  assert.match(SANITIZE, /export function revealAncestors\(target: Element\): void \{[\s\S]{0,300}?p\.localName === "details" && n\.localName !== "summary" && !p\.hasAttribute\("open"\)\) p\.setAttribute\("open", ""\);/,
    "the browser's ancestor revealing steps, shared with a message's in-page links");
  assert.match(RENDER, /^import \{ sanitizeMd, userContentTarget, revealAncestors \} from "\.\/md-sanitize";/m);
  assert.match(RENDER, /\/\/ The browser's fragment navigation REVEALS the target before it scrolls; scrollIntoView does not \(revealAncestors\)\s*\n\s*revealAncestors\(target\);/);
  const shown = fn("shownPassage");
  assert.match(shown, /const head = n\.localName === "details" \? n\.querySelector\(":scope > summary"\) as HTMLElement \| null : null;\s*\n\s*if \(head && elShows\(head\)\) return head;/,
    "the fold's heading");
  assert.match(fn("elShows"), /return typeof e\.checkVisibility === "function" \? e\.checkVisibility\(\) : e\.getClientRects\(\)\.length > 0;/,
    "drawn, not only laid out: Chromium lays out a closed details' content");
  assert.match(RENDER, /const PASSAGE_FOLDED = "Couldn't unfold this passage in its message\.";/);
  assert.match(RENDER, /const rebuilt = turn !== s\.turn;/, "the settle opens the fold again only in a rebuilt turn, never one the reader closed");
});

test("every piece of a split highlight flashes", () => {
  assert.match(fn("landOn"), /const flashes = mark && at\.matches\("mark\.cmt-hl"\) \? Array\.from\(\(at\.closest\("\.turn"\) \|\| target\)\.querySelectorAll\(`mark\.cmt-hl\[data-tid="\$\{cssEscape\(mark\)\}"\]`\)\) : \[at\];\s*\n\s*for \(const f of flashes\) f\.classList\.add\("anchor-flash"\);\s*\n\s*setTimeout\(\(\) => \{ for \(const f of flashes\) f\.classList\.remove\("anchor-flash"\); \}, 1700\);/);
});

test("the box keeps clear of a passage that moves after it was placed, until the reader takes over", () => {
  const watch = fn("watchBeside");
  assert.match(watch, /w\.ro = new ResizeObserver\(\(\) => besideFollow\(\)\);/, "a layout change of the passage's turn or the transcript");
  assert.match(watch, /if \(turn\) w\.ro\.observe\(turn\);/);
  assert.match(watch, /if \(v\) w\.ro\.observe\(v\.el\);/);
  assert.match(RENDER, /if \(after !== before && besideWatch\) \{ if \(writerIsReader\(writer\)\) endBesideWatch\(\); else besideFollow\(\); \}/,
    "a move the page writes re-places the box; the reader's own ends the watch");
  const follow = fn("besideFollow");
  assert.match(follow, /if \(!pop \|\| !v \|\| pop\.dataset\.tid !== w\.tid \|\| commentPopPos !== w\.since\) \{ endBesideWatch\(\); return; \}/,
    "the box closed, another thread's, or moved by the user: the watch ends");
  assert.match(follow, /const at = m \? shownPassage\(m\) : null;\s*\n\s*if \(at\) placeBesidePassage\(pop, w\.tid, at\);/, "re-found, never revealed again");
  assert.match(fn("settleInput"), /settleLastInput = Date\.now\(\);\s*\n\s*endBesideWatch\(\);/, "the reader's hand on the transcript ends it");
  assert.match(fn("passageLanding"), /endBesideWatch\(\);/, "a new landing ends the last one's");
  assert.match(fn("closeCommentPop"), /endBesideWatch\(\);/, "and so does closing the box");
});
