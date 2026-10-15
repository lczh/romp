// Comment threads (the user 2026-08-13): the pure half (comments.ts) is driven behaviorally; the
// render.ts / kernel / CSS wiring is pinned at the source (no jsdom harness for the renderers — the
// repo convention). Synthetic text only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { threadsByAnchor, threadBusy, threadStuck, replyOwed, agentCount, findExact, findAnchorRange, sliceRanges, prunePending, markSkipsParent,
         newCommentCreate, commentCreateFrame, holdAnswered, holdForPassage, synthSuperseded, samePassage, existingThreadFor,
         threadTakesReplies,
         type CommentThread, type CommentCreate } from "./comments";
import { compactDisplay } from "./compact";
import { quoteContext } from "./quote-context";

const requireCjs = createRequire(__filename);

const th = (over: Partial<CommentThread>): CommentThread => ({
  tid: "t1", anchorUuid: "a1", exact: "the passage", status: "open", createdT: 0,
  state: "", unread: false, promotedName: "", msgs: [], ...over,
});

// ── findExact: whitespace-tolerant re-anchoring ────────────────────────────────────────────────

test("findExact finds a verbatim passage", () => {
  const r = findExact("Use exponential backoff with jitter.", "exponential backoff");
  assert.ok(r);
  assert.equal("Use exponential backoff with jitter.".slice(r!.start, r!.end), "exponential backoff");
});

test("findExact tolerates collapsed and rewrapped whitespace", () => {
  // the selection was made on one rendering; the re-render wraps the line elsewhere
  const hay = "Cap the delay\n  at two   minutes.";
  const r = findExact(hay, "delay at two minutes");
  assert.ok(r);
  assert.equal(hay.slice(r!.start, r!.end).replace(/\s+/g, " "), "delay at two minutes");
});

test("findExact returns null when the text drifted away", () => {
  assert.equal(findExact("something else entirely", "the old passage"), null);
});

test("findExact never matches an empty selection", () => {
  assert.equal(findExact("anything", "   "), null);
});

// ── findAnchorRange: longest-prefix fallback for cross-message selections ──────────────────────

test("findAnchorRange returns the full match, not partial, when the text is present", () => {
  const r = findAnchorRange("Use exponential backoff with jitter.", "exponential backoff");
  assert.ok(r && !r.partial);
});

test("findAnchorRange falls back to the longest prefix that lives in this turn", () => {
  // the selection continued into the NEXT message; only its head is in the anchor turn
  const hay = "Cap the delay at two minutes for every retry loop.";
  const r = findAnchorRange(hay, "delay at two minutes for every retry loop. And the jitter stays at ten percent.");
  assert.ok(r);
  assert.ok(r!.partial);
  assert.equal(hay.slice(r!.start, r!.end), "delay at two minutes for every retry loop.");
});

test("findAnchorRange refuses a trivial remnant rather than mark the wrong words", () => {
  assert.equal(findAnchorRange("The cap is fine.", "The completely different selection body"), null);
});

// ── sliceRanges: one global range over many text nodes ─────────────────────────────────────────

test("sliceRanges splits a range across nodes", () => {
  // nodes: "Use " (4) | "exponential" (11) | " backoff." (9); range covers "exponential backoff"
  const slices = sliceRanges([4, 11, 9], 4, 23);
  assert.deepEqual(slices, [
    { idx: 1, s: 0, e: 11 },
    { idx: 2, s: 0, e: 8 },
  ]);
});

test("sliceRanges stays inside one node when the range does", () => {
  assert.deepEqual(sliceRanges([10, 10], 12, 15), [{ idx: 1, s: 2, e: 5 }]);
});

// ── grouping + state predicates ────────────────────────────────────────────────────────────────

test("threadsByAnchor groups threads per turn", () => {
  const by = threadsByAnchor([th({ tid: "t1" }), th({ tid: "t2" }), th({ tid: "t3", anchorUuid: "a2" })]);
  assert.deepEqual([...by.keys()], ["a1", "a2"]);
  assert.equal(by.get("a1")!.length, 2);
});

test("the popover keeps the chat renderer but sheds its transcript-coupled hover chrome", () => {
  // the user 2026-08-23, with a recording: hovering inside the comment box appended glow bands into
  // .cmt-msgs (the band math expects the transcript's host), posted dotHover/dotOpen with the MAIN
  // session's id and the THREAD's uuids (cross-lighting the timeline wrongly), and rail time-markers
  // painted 45px left of gutterless turns — a clipped sliver at the popover edge.
  assert.match(UI, /let renderingIntoThread = false;/);
  assert.match(UI, /renderingIntoThread = true;\s*\/\/ same renderer, minus the transcript-coupled hover chrome/);
  assert.match(UI, /renderingIntoThread = false;\s*\n\s*renderingSid = saved;/, "cleared before the fill returns");
  assert.match(UI, /if \(\(anchorUuid \|\| epoch != null\) && !renderingIntoThread\) wireTurnHover/,
    "no glow bands, no cross-pane posts, no dot-nav promises inside the thread");
  assert.match(UI, /epoch != null && !renderingIntoThread && turn\.querySelector/,
    "no rail time-markers on gutterless popover turns");
});

test("an unread thread wears ONE outline box around its whole passage and a shouting rail tick", () => {
  // the user 2026-08-23: the 45% unread tint alone was too subtle — a thread that replied while the
  // box was closed needs a visible element. That element was a yellow corner dot on the run's last
  // segment until 2026-09-08 (then the tab strip's dashed needs-you ring on the mark), and since
  // 2026-09-10 it is ONE outline around the WHOLE highlighted area: the ring was an outline on the
  // inline mark and painted once per line fragment, a dashed box per line. Since 2026-09-12 the box is
  // dashed in the needs-you red again (the user wanted the tab strip's idiom back; matching the notch
  // had made it solid yellow), and a passage of one or two rows wears the per-fragment ring instead —
  // the painter's call, by row count. The box is a positioned child of the turn that render.ts measures
  // from the marks (its pins live in comment-outline.test.ts). The rail tick still grows and
  // double-rings, its halo in the same red. All clear with the unread flag on open.
  assert.doesNotMatch(CSS, /mark\.cmt-hl\.unread\.hl-last::after/, "the corner dot is gone");
  assert.doesNotMatch(CSS, /mark\.cmt-hl\.unread \{ outline/, "no outline on the mark by unread alone: it would paint per line fragment");
  assert.match(CSS, /\.cmt-outline \{ position: absolute; pointer-events: none;[^}]*outline: 1\.5px dashed var\(--st-awaiting-bg\);/s, "one box, the needs-you ring");
  assert.match(CSS, /mark\.cmt-hl\.unread\.cmt-ring \{ outline: 1\.5px dashed var\(--st-awaiting-bg\); outline-offset: 1px; \}/, "the one- or two-row ring, the same stroke");
  assert.match(CSS, /\.cmt-tick\.unread, \.cmt-tick\.needs \{[^}]*0 0 0 3px var\(--st-awaiting-bg\); \}/s, "the tick's halo agrees by colour (and a thread waiting on you wears it too, thread-live-1)");
  assert.match(UI, /function paintCommentOutlines\(sid: string\): void \{/);
  assert.doesNotMatch(CSS, /mark\.cmt-hl \{[^}]*position: relative;/s, "nothing left for the mark to anchor");
  assert.match(CSS, /\.cmt-tick\.unread, \.cmt-tick\.needs \{ width: 16px; height: 6px; right: 0; opacity: 1;/);   // 10px until the comment-forward rail (2026-09-24, rail-comment-forward.test.ts)
  // the clearing story is the existing machinery, untouched: optimistic on open + kernel watermark
  assert.match(UI, /if \(th\) th\.unread = false;\s*\/\/ optimistic; the kernel's watermark reconciles/);
});

test("an unread thread tints its turn's RAIL segment yellow; clicking the rail opens the thread", () => {
  // the user 2026-08-23: the corner dot is easy to miss — the identity line's own segment is the
  // prominent cue. Cleared on this same pass the moment the thread is viewed (openCommentPopover
  // drops the flag and re-runs applyCommentMarks); the clear sweep runs BEFORE the re-apply so a
  // resolved/removed thread can never leave a stale tint.
  assert.match(UI, /for \(const t of Array\.from\(v\.el\.querySelectorAll\("\.turn\.cmt-rail-unread"\)\)\) t\.classList\.remove\("cmt-rail-unread"\);/);
  assert.match(UI, /turn\.classList\.toggle\("cmt-rail-unread", list\.some\(\(t\) => !!t\.unread && t\.status === "open"\)\);/);
  assert.match(CSS, /\.turn\.cmt-rail-unread::before \{ background: var\(--cmt-hl\); opacity: 1; width: 3px; left: 10px; \}/);
  // the rail-hit click checks at CLICK time, so the strip reverts to timeline navigation once read
  assert.match(UI, /const um = turn\.querySelector\("mark\.cmt-hl\.unread"\) as HTMLElement \| null;/);
  assert.match(UI, /if \(um\?\.dataset\.tid && activeId\) \{ passageLanding\(activeId, um\.dataset\.tid\); return; \}/, "to the passage, then the thread beside it (the rail tick's landing)");
});

test("busy and stuck are disjoint state families", () => {
  for (const s of ["working", "retrying", "compacting"]) assert.ok(threadBusy(s) && !threadStuck(s));
  for (const s of ["permission", "picker"]) assert.ok(threadStuck(s) && !threadBusy(s));
  assert.ok(!threadBusy("waiting") && !threadStuck(""));
});

// ── optimistic sends reconcile against the frame ───────────────────────────────────────────────

test("prunePending spends a pending row when its message lands", () => {
  const pending = [{ text: "why jitter?", t: 1 }, { text: "and the cap?", t: 2 }];
  const msgs = [{ who: "you" as const, text: "why  jitter?", t: 5 }];   // whitespace drift tolerated
  assert.deepEqual(prunePending(pending, msgs), [{ text: "and the cap?", t: 2 }]);
});

test("prunePending spends one pending per landed message — a repeated reply keeps its bubble", () => {
  const pending = [{ text: "why?", t: 1 }, { text: "why?", t: 2 }];
  const msgs = [{ who: "you" as const, text: "why?", t: 5 }];
  assert.deepEqual(prunePending(pending, msgs), [{ text: "why?", t: 2 }]);
});

// ── source pins: the wiring (render.ts, kernel.py, sdk_backend.py, styles.css) ─────────────────

const UI = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the selection menu offers Comment, gated on a real transcript turn (commentAim: the gate the comment key shares)", () => {
  assert.match(UI, /mk\("Comment", \(\) => openCommentComposer\(aim\.sid, aim\.uuid, aim\.text, e\.clientX, e\.clientY, aim\.at, false, aim\.view\)/,
               "the menu's Comment hands on the selection's live range (test_comment_layout_existing_served.py runs it: the menu "
               + "road of its repeated-phrase test, repeatInsideMenu)");
  assert.match(UI, /if \(!q\?\.uuid\) return "none";\n\s*if \(!activeId \|\| isProvisionalId\(activeId\) \|\| !liveSession\(activeId\)\) return "session";/);
});

test("marks, badges AND every popover button ride the stable document.body delegate", () => {
  assert.match(UI, /cmtopen: \(elx\) =>/, "delegated — marks are re-created on every rebuild");
  assert.match(UI, /m\.dataset\.act = "cmtopen"/);
  // the count badge is GONE (the user 2026-08-17): the highlight + the rail tick do the speaking
  assert.doesNotMatch(UI, /cmt-badge/);
  for (const act of ["cmtclose", "cmtsend", "cmtbreak", "cmtdelete", "cmtopensession"]) {
    assert.ok(UI.includes(`${act}:`), `${act} handler missing from the body delegate`);
    assert.ok(UI.includes(`dataset.act = "${act}"`), `${act} button missing its data-act`);
  }
  // Resolve is GONE (the user 2026-08-17): Delete is the only closer — the handler survives for
  // legacy resolved rows, but no button mints new ones
  assert.ok(!UI.includes('rs.dataset.act = "cmtresolve"'), "no Resolve button remains");
});

test("highlights re-apply after every render path", () => {
  assert.match(UI, /m\.type === "session" \|\| m\.type === "chatTail" \|\| m\.type === "chatHead" \|\| m\.type === "chatWindow" \|\| m\.type === "chatTurns" \|\| m\.type === "chatEpisode"\)\)\s*\n\s*applyCommentMarks\(String\(m\.id\)\)/);   // chatTurns: a gap's page (T386 stage 2), where chatMore was   // chatWindow / chatMore: the proto-2 pages rebuild DOM too (T323 stage 4b)
  assert.match(UI, /applyCommentMarks\(activeId\);\s+\/\/ the re-window rebuilt turns/,
               "the scroll re-window path re-anchors too");
  // the syncView wrapper covers renders that run OFF the message handlers (tab switch, prebuild)
  assert.match(UI, /function syncView\(id: string, atBottom\?: boolean\): View \{\s*\n\s*const v = syncViewInner\(id, atBottom\);\s*\n\s*applyCommentMarks\(id\);/);
});

test("a comments frame refreshes the open popover IN PLACE — composer and caret survive", () => {
  assert.match(UI, /prev\.dataset\.mode === mode && prev\.dataset\.tid === \(th \? th\.tid : create!\.uuid\)\s*\n\s*&& prev\.dataset\.status === status/);
  assert.match(UI, /function fillCommentMsgs\(list: HTMLElement, th: CommentThread, sid: string\)/);
});

test("the working state starts on the gesture, and delete is optimistic and cuts the work", () => {
  // create: a synthetic working thread marks the passage before any round-trip, under the GESTURE's id (two creates on
  // one reply are two marks); the frame that lists its thread retires it, and a refusal drops it by that id
  assert.match(UI, /tid: "pending:" \+ held\.createId, anchorUuid: create\.uuid/);
  assert.match(UI, /state: "working",\s*\n\s*unread: false/);
  assert.match(UI, /function dropSynthThread\(sid: string, createId: string\): void \{\s*\n\s*const tid = "pending:" \+ createId;/);
  // reply: the local state flips on send; the kernel's next frame confirms
  assert.match(UI, /cur\.th\.state = "working";\s+\/\/ optimistic/);
  // delete: the highlight goes NOW, and the kernel interrupts the in-flight reply before the kill
  assert.match(UI, /filter\(\(t\) => t\.tid !== cur\.th\.tid\)\);\s*\n\s*applyCommentMarks\(cur\.sid\);\s*\n\s*closeCommentPop\(\);/);
  assert.match(KERNEL, /be\.interrupt\(th\["sid"\]\)[\s\S]{0,200}be\.kill\(th\["sid"\]\)/);
});

test("the popover send acknowledges before any round-trip", () => {
  assert.match(UI, /send\.disabled = true; send\.classList\.add\("busy"\); \}\s+\/\/ ack before the round-trip/);
  assert.match(UI, /the pending bubble IS the acknowledgement/);
});

test("create adopts exactly the thread the kernel named — never a guess", () => {
  assert.match(UI, /m\.type === "commentCreated"/);
  assert.match(UI, /function adoptCommentThread\(sid: string, tid: string\)/);
  assert.match(KERNEL, /"type": "commentCreated", "id": sid, "tid": tid/);
  // the ACK carries the new thread's row, built as the frame builds it (2026-10-01: the kernel answers the moment the row
  // exists and leaves the frame to its pusher), and the page lists that row before it adopts, so adoption finds the thread
  // without waiting for the frame
  assert.match(KERNEL, /entry = _comment_frame_entry\(row, _sdk\(\), int\(time\.time\(\)\)\)/);
  assert.match(KERNEL, /ack\["thread"\] = entry/);
  const ack = UI.split('else if (m.type === "commentCreated" && m.id && m.tid) {')[1].split("\n  }\n  // a reply the kernel refused")[0];
  assert.match(ack, /if \(row && row\.tid === tid && !have\.some\(\(t\) => t\.tid === tid\)\) commentThreads\.set\(String\(m\.id\), \[\.\.\.have, row\]\);/);
  assert.ok(ack.indexOf("commentThreads.set(String(m.id), [...have, row])") < ack.indexOf("dropSynthThread(held.sid, held.createId)"),
            "the real row is listed before the optimistic mark goes, so the highlight never blinks out");
  // …and a frame built before that row was saved never follows the answer: the pusher sends a frame only at the store
  // generation it was built from, under the lock the answer takes
  assert.match(KERNEL, /if _comments_gen\.get\(sid, 0\) != gen:/);
});

test("a refused reply hands the words back instead of thinking forever", () => {
  assert.match(KERNEL, /"type": "commentSendFailed", "id": sid, "tid": str\(msg\["tid"\]\)/);
  assert.match(UI, /m\.type === "commentSendFailed"/);
  assert.match(UI, /commentDrafts\.set\(String\(m\.tid\), lost\.text\)/);
});

test("ending the parent sweeps its threads' CLIs — no unreachable running sessions", () => {
  assert.match(KERNEL, /_comment_kill_all\(sid, be\)/);
  assert.match(KERNEL, /def _comment_kill_all\(parent_sid, be\):/);
});

test("a refused create un-sticks the popover; a pre-seam anchor tip-forks instead of erroring", () => {
  // the TYPED nack hands the box back (2026-10-01), never any warn: an unrelated warning rebuilt an open create box
  // under the caret and, after a send, re-armed the arrow while the create still ran (a second press, a second thread)
  assert.match(UI, /if \(held && !m\.transient\) refuseCmtCreate\(held\);/);
  assert.match(UI, /function refuseCmtCreate\(c: CommentCreate\): void \{[\s\S]{0,700}document\.getElementById\("cmt-pop"\)\?\.remove\(\);\s*\n\s*renderCommentPopover\(\);/);
  assert.doesNotMatch(UI, /m\.type === "warn" && pendingCommentAnchor/, "no warn reaction left on the create box");
  assert.match(KERNEL, /def _anchor_adapter\(path, sid, need=None\)/);   // the leaf's own graph first, the anchor only when needed
  assert.match(KERNEL, /return "", cut_t, None/);
});

test("the projection never reads the parent transcript pre-fork", () => {
  assert.match(KERNEL, /if reg\.get\("forkOf"\):\s*\n\s*return \[\]/);
});

test("a promoted thread whose session ended drops off the frame entirely", () => {
  assert.match(KERNEL, /if status == "promoted" and not _thread_reg\(tsid\)\.get\("alive"\):\s*\n\s*return None/);
  assert.match(KERNEL, /row = _comment_frame_entry\(th, be, now, live_map, thread_live\)\s*\n\s*if row is not None:\s*\n\s*threads\.append\(row\)/);
});

test("promote latches the row before seeding; racing ops refuse through the CAS", () => {
  assert.match(KERNEL, /def _comment_update_if\(parent_sid, tid, expect, \*\*changes\):/);
  assert.match(KERNEL, /_comment_update_if\(parent_sid, tid, \("open", "resolved"\), status="promoting"\)/);
  assert.match(KERNEL, /def _revert\(msg\):/);
});

test("the /kill route sweeps comment threads like the WS endSession op", () => {
  assert.match(KERNEL, /_comment_kill_all\(sid, be\)\s+# its comment threads must not outlive it \(the WS endSession twin\)/);
});

test("a thread that couldn't start says so — the error note renders in the thread", () => {
  // (the pulsing dots this note used to preempt are retired outright — the dots-gone test below)
  assert.match(KERNEL, /be\.launch_error\(tsid\) if hasattr\(be, "launch_error"\) else None/);
  assert.match(UI, /cmt-note cmt-err/);
  assert.match(UI, /if \(th\.status === "open" && th\.error\) \{/);
});

test("Delete is offered on open and resolved threads, never mid-promote", () => {
  // Resolve is gone (the user 2026-08-17) — Delete is the closer for BOTH, still gated off
  // 'promoting'/'promoted' (the kernel refuses those anyway; the button never dangles one)
  assert.match(UI, /if \(th\.status === "open" \|\| th\.status === "resolved"\) \{/);
});

test("break out posts commentPromote and acks with a provisional tab", () => {
  assert.match(UI, /function showBreakoutPrompt\(sid: string, tid: string\)/);
  assert.match(UI, /type: "commentPromote", id: sid, tid, name \}\);\s*\n\s*close\(\);\s*\n\s*closeCommentPop\(\);\s*\n\s*openProvisional\(\{ name, backend: "sdk", dir: "", host: hostOf\(sid\) \}\);/);
});

test("the create dialog pre-reads the kernel's default-comment trio and shows it", () => {
  // The user 2026-08-29: the gear can pin a default model/effort/fast for every NEW thread. The
  // dialog must SHOW the effective default (pick > setting > inherit) so a pick stays a visible
  // deviation; the kernel re-resolves the same order at create, so a stale pre-read can mislabel
  // a chip but never mislaunch a thread.
  assert.match(UI, /let commentDefaults = \{ model: "session", effort: "session", fast: "session" \};/);
  assert.match(UI, /if \(d\.commentDefaults\) adoptCommentDefaults\(d\.commentDefaults\);/);
  assert.match(UI, /function refreshCommentDefaults/);
  // re-read at dialog open, repainting the row IN PLACE (the composer holds focus and a draft)
  assert.match(UI, /refreshCommentDefaults\(\(\) => \{\s*\n\s*if \(pendingCommentAnchor !== create\) return;/);
  assert.match(UI, /const effVal = chosen \|\| setDef\(kind\);/);
  // a version id stored in the setting still labels correctly (families hold their versions)
  assert.match(UI, /function modelChoiceLabel\(value: string\)/);
  // the model is the EFFECTIVE default, the read the create makes too (a stored default the kernel no longer
  // offers falls to "session" on both, so the dialog never shows a model the create will not launch on)
  assert.match(KERNEL, /"commentDefaults": \{"model": _comment_default_model_effective\(\),/);
  assert.match(KERNEL, /def _comment_default_model_effective\(\):/);
});

test("fast rides the create end to end, resolved dialog > setting > inherit at the kernel", () => {
  // the chip is offered only where the effective model could run fast (no control that only toasts)
  assert.match(UI, /if \(canFast\(create\.model \|\| setDef\("model"\) \|\| st\?\.model \|\| ""\)\) metaRight\.append\(mkSel\("fast"\)\);/);
  // the dialog's picks ride the held create (newCommentCreate reads anchor.fast) and every frame built from it,
  // the send's and the in-flight retry's alike; the builder is driven directly by the create-identity tests below
  assert.match(UI, /const held = newCommentCreate\(create, text, nm\)/);
  assert.match(UI, /vscodeApi\.postMessage\(commentCreateFrame\(held\)\)/);
  assert.match(UI, /vscodeApi\?\.postMessage\(commentCreateFrame\(c\)\)/);
  assert.match(KERNEL, /def _comment_launch_prefs\(model="", effort="", fast=""\):/);
  // resolved ONCE above the name claim (the model is vouched there, review round three of the extra-models PR) and
  // reused at the fork: the create hands fast through the same tuple as before
  assert.match(KERNEL, /launch = _comment_launch_prefs\(model, effort, fast\)/);
  assert.match(KERNEL, /model, effort, fast = launch/);
  assert.match(KERNEL, /fast=str\(msg\.get\("fast"\) or ""\)/);       // the ws op hands it through…
  assert.match(KERNEL, /"fast": str\(msg\.get\("fast"\) or ""\),/);   // …and a lag-parked create keeps it
  assert.match(KERNEL, /fast=pk\.get\("fast", ""\)/);
});

test("kernel registers every comment drive op", () => {
  for (const op of ["commentCreate", "commentReply", "commentResolve", "commentDelete", "commentSeen", "commentPromote"]) {
    assert.ok(KERNEL.includes(`"${op}"`), `${op} missing from ID_OPS/handlers`);
  }
});

test("the comments frame rides its own dedup slot, never the chat delta baseline", () => {
  assert.match(KERNEL, /_send_comments_frame\(chat_clients, s\["sid"\], fr, gen\)/);
  assert.match(KERNEL, /_send_client\(c, \("comments", sid\), fr\)/);
});

test("a thread fork withholds the names/ entry; promote seeds first, then registers", () => {
  assert.match(BACKEND, /if not thread_of:\s*\n\s*write_name/);
  assert.match(BACKEND, /def promote_thread\(/);
  assert.match(BACKEND, /reg\.get\("threadOf"\):\s*\n\s*continue/, "live_sessions skips threads — no tab");
  assert.match(KERNEL, /err = _seed_fork_stores\(parent_sid, tsid, parent_path, str\(th\.get\("cutUuid"\) or ""\)\)/);
});

test("the WHOLE popover drags — grip anywhere that isn't a control — and closes on tab switch", () => {
  assert.match(UI, /pop\.addEventListener\("pointerdown"/);
  assert.match(UI, /pop\.setPointerCapture\(ev\.pointerId\)/);
  assert.match(UI, /ev\.clientX > pr\.right - 18 && ev\.clientY > pr\.bottom - 18\) return;/);
  assert.match(CSS, /\.cmt-pop \{[^}]*resize: both/s);
  assert.match(CSS, /\.cmt-pop\.sized \.cmt-quote \{/, "a user resize hands the room to the quoted context");
  assert.match(UI, /commentPopPos = \{ x, y \};/);
  assert.match(UI, /if \(openCommentKey && openCommentKey\.sid !== id\) closeCommentPop\(\);/);
  assert.match(CSS, /\.cmt-pop \{[^}]*cursor: grab/s, "the grab hand covers the whole box now");
});

test("picking a model/effort never reads as an outside press — the box stays put", () => {
  // the dropdowns and the break-out dialog are appended to document.body (fixed position, like the
  // statusline's menus), so the outside-press closer must exempt them: it used to close the popover
  // on mousedown and null pendingCommentAnchor before the item's click could land the pick, and a
  // click on the break-out dialog's Cancel stranded the user the same way (the user 2026-08-18)
  assert.match(UI, /if \(!pop \|\| pop\.contains\(ev\.target as Node\)\) return;\s*\n\s*if \(\(ev\.target as HTMLElement\)\.closest\?\.\("\.meta-menu, #fork-prompt"\)\) return;\s*\n\s*closeCommentPop\(\);/);
  // and the surviving popover shows the pick THROUGH the chat's own builder (2026-08-25 parity):
  // the shared menu's click arms the sid-scoped pending dots, and the frame-driven refresh re-runs
  // syncMetaControls on the popover's row exactly as the chat's tick does
  assert.match(UI, /metaPending\.set\(`\$\{opSid\}:\$\{kind\}`, \{ was, until: Date\.now\(\) \+ 20_000 \}\);/);
  assert.match(UI, /if \(th && cm\) syncMetaControls\(cm, threadMetaStatus\(th\), threadSidOf\(th\)\);/);
});

test("marks use the prefix-tolerant anchor matcher", () => {
  assert.match(UI, /findAnchorRange\(nodes\.map\(\(t\) => t\.data\)\.join\(""\), th\.exact\)/);
});

test("the highlight is highlighter-YELLOW — never the selection blue — and one unbroken block", () => {
  assert.match(CSS, /--cmt-hl: #ffd54a;/);
  assert.match(CSS, /mark\.cmt-hl \{[^}]*var\(--cmt-hl\)/s);
  assert.doesNotMatch(CSS.match(/mark\.cmt-hl \{[^}]*\}/s)![0], /var\(--accent\)/);
  // the radius sits only on the run's outer ends — per-segment rounding drew word-boundary seams
  assert.match(UI, /classList\.toggle\("hl-first", i === 0\)/);
  assert.match(CSS, /mark\.cmt-hl\.hl-first \{ border-top-left-radius: 2px/);
  // a fully-covered inline-code span tints at the ELEMENT: a mark inside it can't paint the code's
  // padded background, which left an untinted sliver around every code word (the word-island look)
  assert.match(UI, /host\.classList\.toggle\("cmt-hl-host", !threadSettled\(th\.status\)\)/);   // a relayed thread keeps the tint (2026-09-30)
  assert.match(UI, /p\.classList\.remove\("cmt-hl-host"\)/);
  assert.match(CSS, /code\.cmt-hl-host \{ background: color-mix\(in srgb, var\(--cmt-hl\) 30%, var\(--code-bg\)\)/);
  assert.match(CSS, /code\.cmt-hl-host > mark\.cmt-hl \{ background: transparent/);
});

test("the create dialog names the thread right there: prefilled <session>-comment-<N>, validated", () => {
  // T289: the prefill is a HINT from the BARE session name (comment-name.ts), remembered on the box so an
  // untouched one is sent as "" and the kernel picks its own default
  assert.match(UI, /const prefill = defaultCommentName\(sess0\?\.name, sid, \(commentThreads\.get\(sid\) \|\| \[\]\)\.length\);\s*\n\s*nameBox\.dataset\.prefill = prefill;\s*\n\s*nameBox\.value = sent \? \(sent\.name \|\| prefill\) : \(commentDrafts\.get\(nk\) \|\| prefill\);/);   // the sent view shows the name as it went
  // the name lives IN the header ("New comment: <name>"), the button says Comment, and the picks ride along
  assert.match(UI, /"New comment:"/);
  assert.match(UI, /if \(nameBox\) head\.append\(title, nameBox, maxBtn, closeBtn\);/);   // the maximize button rides between (2026-09-10, comment-pop-size.test.ts)
  assert.match(UI, /send\.setAttribute\("aria-label", create \? "Comment" : "Send"\);/);   // the ➤ carries the word
  assert.match(UI, /const held = newCommentCreate\(create, text, nm\);[\s\S]{0,3000}vscodeApi\.postMessage\(commentCreateFrame\(held\)\);/);   // the anchor's picks ride the held create
  // the comment's own model/effort selectors reuse the statusline's /models-fed choices + menu skin
  assert.match(UI, /const metaRow = el\("div", "statusline cmt-meta-row"\);/);   // the chat statusline dress (2026-08-25 parity)
  assert.match(UI, /META_CHOICES\[kind\]/);
  assert.match(KERNEL, /model=str\(msg\.get\("model"\) or ""\), effort=str\(msg\.get\("effort"\) or ""\)/);
  // the kernel's default starts from the same count, then bumps past every taken or in-flight name
  // (session names are reserved atomically, 2026-09-08) — the prefill and the default agree on the count
  assert.match(KERNEL, /n = len\(data\.get\("threads"\) or \[\]\) \+ 1[\s\S]{0,400}"%s-comment-%d" % \(sess\["name"\], n\)/);
  assert.match(UI, /const base = thName \|\|/, "break-out prefills the thread's own name");
});

test("the landing pulse fires once per navigation, not once per history-fetch round", () => {
  assert.match(UI, /let flashedAnchor: string \| null = null;/);
  assert.match(UI, /if \(flashKey == null \|\| flashKey !== flashedAnchor\)/);
  assert.match(UI, /landOn\(target, uuid, markEl \?\? quoteEl \?\? firstTextAtomBelow\(target\), quote, mark\);/);   // the one landing write, aligned on the words when the frame quotes them (T386), or on a comment thread's passage
  assert.match(UI, /if \(anchor\) flashedAnchor = null;/);
});

test("math hosts tint like code hosts — a mark can't paint KaTeX's glyph spacing", () => {
  assert.match(UI, /closest\(".katex"\)/);
  assert.match(CSS, /\.md \.katex\.cmt-hl-host \{ background: color-mix\(in srgb, var\(--cmt-hl\) 30%/);
  assert.match(CSS, /\.md \.katex\.cmt-hl-host mark\.cmt-hl \{ background: transparent/);
});

test("scroll-rail ticks mark the commented spots and jump-open on click", () => {
  assert.match(UI, /function updateCommentRail\(\)/);
  assert.match(UI, /tick\.dataset\.act = "cmtjump"/);
  assert.match(UI, /cmtjump: \(elx\) =>/);
  assert.match(UI, /if \(sid === activeId\) updateCommentRail\(\);/);
  assert.match(CSS, /\.cmt-tick \{/);
  assert.match(CSS, /\.cmt-rail \{ position: fixed;/);
});

test("ticks and message notches share ONE scrollbar frame, so they can never disagree about order", () => {
  // the user 2026-08-17: scrolling through a history load moved the comment highlights relative to
  // the blue message notches — the ticks were placed by uniform index fraction, a second frame that
  // drifts from the notches' measured-height pixel offsets. Both painters now consume
  // contentOffsetFrame, the one event-index → content-pixel mapping.
  assert.match(UI, /function contentOffsetFrame\(/);
  assert.match(UI, /const off = frame\.offsetOf\(evUnit\[idx\]\);/,
    "ticks place by the shared frame, in UNIT space (anchors are events; the frame speaks units)");
  assert.doesNotMatch(UI, /\(idx \/ n\) \* 100/, "the uniform index-fraction percent frame is gone");
  // the rail repaints with the notches (same rAF), so both always draw from one world
  assert.match(UI, /paintRailSticky\(\); paintScrollMarks\(\); updateCommentRail\(\);/);
  // an unchanged tick set moves IN PLACE — ticks are buttons, and a mid-press rebuild eats the click
  assert.match(UI, /kids\.every\(\(k, i\) => k\.dataset\.tid === ticks\[i\]\.th\.tid\)/);
  assert.match(UI, /ticks\.forEach\(\(t, i\) => dress\(kids\[i\], t, i\)\);/);
  assert.match(UI, /tick\.style\.top = t\.y \+ "px";/, "the one dress moves the tick (and sets its hit pads, rail-comment-forward.test.ts)");
});

test("while the thread is WRITING the passage holds the await-green tint and NOTHING crawls", () => {
  // the user 2026-08-24 (superseding 2026-08-23's tick-crawl compromise): the in-flight cue is the
  // await-green STATE COLOR on the passage itself — its own new-test block below pins the colors;
  // this one keeps the class wiring and holds the line on motion: no strips, no keyframes, anywhere.
  assert.match(UI, /m\.classList\.toggle\("busy", commentInFlight\(th\)\);/);
  assert.doesNotMatch(CSS, /mark\.cmt-hl\.busy \{[^}]*repeating-linear-gradient/s, "no strips on prose");
  assert.doesNotMatch(CSS, /@keyframes cmt-ants /, "the passage keyframes stay gone");
  assert.doesNotMatch(CSS, /@keyframes cmt-tick-ants/, "…and the tick's miniature march followed them out");
  assert.match(UI, /\+ \(commentInFlight\(th\) \? " busy" : ""\);/);
  assert.match(UI, /\+ ":" \+ \(commentInFlight\(t\.th\) \? 1 : 0\)\)/, "an in-flight flip re-renders the tick");
});

test("the popover renders the thread with the CHAT's own renderer from the branch point", () => {
  assert.match(UI, /renderingSid = threadSidOf\(th\);/);   // the thread's session id (host-prefixed on a linked machine)
  assert.match(UI, /const node = renderEvent\(ev, prev, turnWorkedSecs\(evs, it\.index, thWorking\)\);\s*\n\s*list\.appendChild\(node\);/);   // + the chat's worked footers (the parity bundle, 2026-08-26)
  assert.match(KERNEL, /def _thread_events\(tsid, cut_uuid, now, live_map, tm=None, out=None\):/);   // tm: the thread's live row (thread-live-1)
  assert.match(KERNEL, /evs = evs\[at \+ 1:\]/, "sliced to AFTER the branch point — the head system card never rides");
  // the thread's own statusline posts the chat's own ops through the SHARED menu, keyed to the
  // thread sid (toggleMetaMenu's opSid — 2026-08-25 parity: one builder, sid-scoped)
  assert.match(UI, /type: kind === "model" \? "setModel" : kind === "effort" \? "setEffort" : kind === "fast" \? "setFast" : "setMode", id: opSid, value/);
});

test("the tint ladder keeps every state distinct: base < unread < hover", () => {
  const base = CSS.match(/mark\.cmt-hl \{[^}]*var\(--cmt-hl\) (\d+)%/s)![1];
  const unread = CSS.match(/mark\.cmt-hl\.unread \{[^}]*var\(--cmt-hl\) (\d+)%/s)![1];
  const hover = CSS.match(/mark\.cmt-hl:hover \{[^}]*var\(--cmt-hl\) (\d+)%/s)![1];
  assert.ok(Number(base) < Number(unread), "unread must read stronger than read");
  assert.ok(Number(unread) < Number(hover), "hovering an unread mark must still answer");
});

test("comment chrome (badge, popover card) stays on the menu vocabulary", () => {
  assert.match(CSS, /\.cmt-pop \{[^}]*#252526[^}]*\}/s);
  assert.match(CSS, /\.cmt-pop \{[^}]*border-radius: 6px/s);
});

// ── the comment-thread UI pass (the user 2026-08-24, three asks) ─────────────────────────────────

test("an in-flight thread's highlight PULSES await-green — text and hosts in lockstep", () => {
  // mid-reply the passage wears the await-green blend, and it PULSES while generating (the user
  // 2026-08-25, asking for exactly this — superseding the earlier no-motion ruling for THIS state
  // only; the marching ants stay dead below). Settles into the existing full yellow on landing.
  assert.match(CSS, /mark\.cmt-hl\.busy \{\s*\n\s*background-color: color-mix\(in srgb, var\(--st-awaitbg-bg\) 24%, transparent\);\s*\n\s*animation: cmt-busy-pulse 2\.2s ease-in-out infinite;\s*\n\}/);
  // the code/math HOST pairing greens AND pulses the same way, the SAME clock — lockstep by keyframe
  assert.match(CSS, /code\.cmt-hl-host:has\(mark\.cmt-hl\.busy\),\s*\n\.md \.katex\.cmt-hl-host:has\(mark\.cmt-hl\.busy\) \{\s*\n\s*background-color: color-mix\(in srgb, var\(--st-awaitbg-bg\) 24%, transparent\);\s*\n\s*animation: cmt-busy-pulse 2\.2s ease-in-out infinite;/);
  assert.match(CSS, /code\.cmt-hl-host:has\(mark\.cmt-hl\.busy\) \{\s*\n\s*background-color: color-mix\(in srgb, var\(--st-awaitbg-bg\) 24%, var\(--code-bg\)\);\s*\n\s*animation: cmt-busy-pulse-code 2\.2s ease-in-out infinite;/);
  // reduced motion keeps the static green — the pulse joins the loading-cues idiom family
  assert.match(CSS, /@media \(prefers-reduced-motion: reduce\) \{\s*\n\s*mark\.cmt-hl\.busy,[\s\S]{0,160}animation: none; \}\s*\n\}/);
  // the crawl is gone root and branch; the rail tick wears the same state green instead
  assert.doesNotMatch(CSS, /cmt-tick-ants/);
  assert.match(CSS, /\.cmt-tick\.busy \{ background: var\(--st-awaitbg-bg\); \}/);
  // the other highlight states are untouched: base, unread, hover, resolved stay the yellow family
  assert.match(CSS, /mark\.cmt-hl \{\s*\n\s*background: color-mix\(in srgb, var\(--cmt-hl\) 30%, transparent\);/);
  assert.match(CSS, /mark\.cmt-hl\.unread \{ background: color-mix\(in srgb, var\(--cmt-hl\) 45%, transparent\); \}/);
  assert.match(CSS, /mark\.cmt-hl:hover \{ background: color-mix\(in srgb, var\(--cmt-hl\) 58%, transparent\); \}/);
  assert.match(CSS, /mark\.cmt-hl\.resolved \{ background: var\(--cmt-hl-resolved\); \}/);   // the dimmed comment ink, a token per theme (2026-09-30)
});

test("the thread's identity rail runs continuous — no holes at the list's flex gaps", () => {
  // each chat-parity turn's ::before rail segment spans only its own box (top:0..bottom:0), and
  // .cmt-msgs' 6px flex gap sat UNPAINTED between consecutive turns — visible holes in the line.
  // Every turn following another turn stretches its segment up across the gap; non-turn items
  // (pending bubbles, dots, notes) render after the turns, so the + pair never misses.
  assert.match(CSS, /\.turn::before \{ content: ""; position: absolute; left: 10\.5px; top: 0; bottom: 0; width: 2px;/,
    "the base segment this fix extends");
  assert.match(CSS, /\.cmt-msgs \{ flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; \}/,
    "the 6px gap the -6px below must stay paired with");
  assert.match(CSS, /\.cmt-msgs \.turn \+ \.turn::before \{ top: -6px; \}/);
  // the day divider between two turns (T339): the flex gap sits on both sides of it and `.turn + .turn` does not fire
  // after it, so its own segment spans margin plus gap each side; derived from the gap and the divider's margins
  const gap = Number(CSS.match(/^\.cmt-msgs \{[^}]*gap: (\d+)px;/m)![1]);
  const margins = CSS.match(/^\.day-divider \{[^}]*margin: (\d+)px 0 (\d+)px;/m)!;
  assert.match(CSS, new RegExp("^\\.cmt-msgs \\.day-divider::before \\{ top: -" + (gap + Number(margins[1])) + "px; bottom: -" + (gap + Number(margins[2])) + "px; \\}", "m"),
    "the popover's divider segment covers the gap above and below");
});

test("the quoted passage is CONTEXT on the thread's opening message — never an item above the branch divider", () => {
  // chronology: the branch happened before the quote, so the quote cannot sit above the divider.
  // It attaches to the opening user message (the chat's citation-as-context idiom), and the
  // standalone block — minted only while the events hadn't landed — is swept when they arrive,
  // which used to leave BOTH on screen: quote on top, "branched" marker below it.
  assert.match(UI, /let quoteHost: HTMLElement \| null = null;\s*\/\/ the thread's OPENING message — the quote's home/);
  assert.match(UI, /if \(!quoteHost && ev\.kind === "user"\) quoteHost = node;/);
  assert.match(UI, /const ctx = el\("div", "cmt-quote cmt-quote-ctx"\);/);
  assert.match(UI, /quoteHost\.insertBefore\(ctx, quoteHost\.firstChild\);/);
  assert.match(UI, /list\.closest\("\.cmt-pop"\)\?\.querySelector\(":scope > \.cmt-quote"\)\?\.remove\(\);/);
  // the pre-events standalone block still renders (there is nothing to attach to yet, and no
  // divider to misorder against) — the create/no-events guard is unchanged
  assert.match(UI, /if \(create \|\| !\(th!\.events \|\| \[\]\)\.length\) \{/);
  assert.match(CSS, /\.cmt-quote-ctx \{ margin: 0 0 4px; \}/);
});

test("the popover's typing dots are retired — the green highlight is the only in-flight cue", () => {
  // the user 2026-08-24 (closing the 2026-08-24 green-highlight pass): "rather than the little
  // spinning dot dot dot thing" — the ellipsis animation goes too. The reply's arrival is announced
  // by the green→yellow settle; the pending bubble still acknowledges the user's own send.
  assert.doesNotMatch(UI, /cmt-dots/);
  assert.doesNotMatch(CSS, /cmt-dots|cmt-dot-pulse/);
  assert.match(UI, /the pending bubble IS the acknowledgement/);
});

// ── T104 (the user 2026-08-26, screenshot): the popover's pending echo wore a one-off washed-gray
// pill — the "third look" the chat killed 2026-07-16, reborn thread-locally — while the chip read
// Ready off one stale relayed frame, so it read as a stuck queued thing with no queued dress. The
// echo now RIDES the chat's own component: renderQueued's bare optimistic group, inherited. ──────
test("the popover's pending echo IS the chat's queued idiom — one component, both boot and live", () => {
  assert.match(UI, /function cmtPendingQueued\(pend: \{ text: string; t: number; imgPaths\?: string\[\] \}\[\]\): HTMLElement \{\s*\n\s*return renderQueued\(\{ kind: "queued", bare: true,/);
  assert.match(UI, /texts: pend\.map\(\(p\) => \(\{ md: p\.text, optimistic: true, cancelable: false, imgPaths: p\.imgPaths \}\)\),/);
  // the parity bundle (2026-08-26): a shipped image rides the pending entry, so the echo renders
  // the SAME thumbnails the chat's echo does — event-sourced from the drop ack, never text-parsed
  assert.match(UI, /if \(previewKind\(m\.path\) === "img"\) cmtShippedImgs\.push\(m\.path\);/);
  assert.match(UI, /imgPaths: cmtShippedImgs\.filter\(\(p2\) => text\.includes\(p2\)\) \}\);/);
  const sites = (UI.match(/cmtPendingQueued\(pend\)/g) || []).length;
  assert.equal(sites, 2, "both render sites — the boot view and the live list — share it");
  // the one-off is gone root and branch: no .pending class minted, no washed-gray CSS
  assert.doesNotMatch(UI, /classList\.add\("pending"\)/);
  assert.doesNotMatch(CSS, /\.cmt-msg\.pending/);
});

// ── THE EXCHANGE LATCH (T102, the user 2026-08-26 — replacing the push-count settle): busy latches
// at the SEND GESTURE (cmtAwaitBase.set in render.ts, before any kernel round-trip — thread-open is
// never the start trigger) and clears ONLY on the reply-arrived event: th.msgs holding MORE
// who==="agent" records than at the send. No push counting (the banned proxy: its all-quiet
// fork-birth frames killed the create-window green, and a stall in its stepping parked green
// forever), no thread-state proxy, no clocks. ────────────────────────────────────────────────────
test("agentCount is the reply-arrived detector's datum — records of the exchange itself", () => {
  const msg = (who: "you" | "agent") => ({ who, text: "x", t: 1 });
  assert.equal(agentCount(th({ msgs: [] })), 0);
  assert.equal(agentCount(th({ msgs: [msg("you")] })), 0, "the send alone arrives no reply");
  assert.equal(agentCount(th({ msgs: [msg("you"), msg("agent")] })), 1, "the reply record raises the count");
  assert.equal(agentCount(th({ msgs: [msg("you"), msg("agent"), msg("you")] })), 1, "a follow-up send does not");
  assert.equal(replyOwed(th({ msgs: [] })), false, "an empty thread owes nothing");
  assert.equal(replyOwed(th({ msgs: [msg("you")] })), true, "…the durable owed half survives reloads");
});

test("busy latches at the SEND gesture and clears exactly on the reply-arrived record (source pins)", () => {
  // create: the gesture latches under the synth tid, before any kernel round-trip
  assert.match(UI, /cmtAwaitBase\.set\(synth\.tid, \{ \.\.\.CMT_LATCH_ZERO \}\);\s+\/\/ the SEND gesture latches the pulse/);
  // follow-up: re-latches at ITS send, with the thread's projected counts at that moment as the base (T237)
  assert.match(UI, /cmtAwaitBase\.set\(cur\.th\.tid, cmtLatchOf\(cur\.th\)\);/);
  // the create's latch carries onto the real thread at its ack (the synth tid retires with the create)
  assert.match(UI, /const synthTid = "pending:" \+ held\.createId;\s*\n\s*const base = cmtAwaitBase\.get\(synthTid\);\s*\n\s*if \(base !== undefined\) \{ cmtAwaitBase\.set\(tid, base\); cmtAwaitBase\.delete\(synthTid\); \}/);
  // the ONE clearing site (T237): the comments frame whose projection carries the SEND (more messages
  // than at the click) — or the thread leaving "open"/erroring. From that frame the KERNEL's replyOwed
  // owns the wash: it reads the thread's transcript with the event model's own turn-end, so a mid-turn
  // interim (the specimen's 'checking…' text) or a backend state flap can never clear the pulse early.
  assert.match(UI, /if \(base !== undefined && cmtLatchReleased\(t, base\)\) cmtAwaitBase\.delete\(t\.tid\);/,
    "against a T237 kernel the latch covers only the pre-round-trip instant (released by the user's own send); an older kernel keeps the T102 reply-arrived clear");
  // the mark's predicate: the latch, then the kernel's owed bit (an older kernel: the records' own owed
  // reading); never a timer, never the client's state read
  assert.match(UI, /if \(cmtAwaitBase\.has\(th\.tid\)\) return true;/);
  assert.match(UI, /const owed = typeof th\.replyOwed === "boolean" \? th\.replyOwed : replyOwed\(th\);/);
  assert.match(UI, /return owed && !cmtInterrupted\.has\(th\.tid\);/,
    "the owed arm carries the T138 stop tombstone — an interrupted send owes nothing until the NEXT send");
  assert.match(UI, /if \(th\.status !== "open" \|\| !!th\.error \|\| threadStuck\(th\.state\)\) return false;/);
  // the push-count proxy is GONE root and branch
  assert.doesNotMatch(UI, /settledPushes|commentBusyLatch|latchBusy|SETTLE_CONFIRM_PUSHES/);
});

test("stuck-green regression: a stalled or missing later frame can never park the pulse", () => {
  // the old settle needed the 0→1→2 stepping to arrive; a parent dropping out of the pushed set (or
  // any withheld frame) left !confirmed true with nothing to clear it. The new clear is the reply
  // RECORD itself: the frame that shows the reply clears the latch in the same breath, and a thread
  // with no latch entry and an agent-tail msgs reads settled with NO further frames needed.
  const msg = (who: "you" | "agent") => ({ who, text: "x", t: 1 });
  assert.equal(replyOwed(th({ msgs: [msg("you"), msg("agent")] })), false,
    "the reply record alone reads settled — no confirmation pushes exist to stall");
  assert.doesNotMatch(UI, /settleConfirmed/);
});

// ── LEG C (the user 2026-08-24): the popover ignored the chat's display settings — thinking blocks
// and raw tool runs rendered regardless of the gear's compact option. The popover now renders the
// chat's OWN display units. Audit (setting → chat honors → popover before/after): compact/hide-
// thinking ✓chat / showed→hidden; compact/fold-tools ✓chat / raw cards→folded (shared expand keys);
// chatScheme ✓both already (body-class CSS, popover in scope); colormap/subgoals/collapsed/grouped
// (feed), judge toggles (timeline), backend/defaultDir (create), showBranch (statusline), tabCtx
// (tab strip) — not transcript-rendering settings, N/A both before and after. ───────────────────
test("the popover renders the chat's display units — thinking hidden, tool runs folded, per the gear", () => {
  const at = UI.indexOf("renderingIntoThread = true;");
  const block = UI.slice(at, at + 2800);   // widened past the T145 relay-note insert
  // 2026-09-08 (the notice-vocabulary pass): the retry-run fold became the generic noticegroup, fed the same
  // per-event foldability the chat computes (isFoldableNotice)
  assert.ok(block.includes("? compactDisplay(evs.map((e) => e.kind), evs.map((e) => e.kind === \"tool\" ? e.name : undefined), evs.map(isFoldableNotice))"),
    "the SAME unit builder the chat uses, gated on the SAME settings.compact");
  assert.ok(block.includes('const key = it.kind === "toolgroup" ? toolGroupKey(run[0]) : noticeGroupKey(run[0]);'),
    "the chat's group identities — tool runs AND notice runs — so expands survive refills");
  assert.ok(block.includes("? renderToolGroup(run as Extract<ChatEvent, { kind: \"tool\" }>[], prev, key, open)"),
    "the chat's own folded lines");
  assert.ok(block.includes('child.classList.add("tg-child");'), "expanded children wear the chat's classes");
});

test("executable, real thinking fixture: the unit stream drops thinking and folds the tool run", () => {
  // the exact shape the popover receives (a thread that thought, ran two tools, then replied)
  const kinds = ["user", "thinking", "tool", "tool", "assistant"];
  const units = compactDisplay(kinds, [undefined, undefined, "Read", "Edit", undefined]);
  assert.deepEqual(units, [
    { kind: "event", index: 0 },
    { kind: "toolgroup", indices: [2, 3] },
    { kind: "event", index: 4 },
  ], "no unit for the thinking block; the consecutive tools fold to one group");
});

test("everything that re-renders the chat's units refills the open popover live", () => {
  assert.match(UI, /function refillOpenCommentPop\(\): void \{/);
  assert.match(UI, /refillOpenCommentPop\(\);   \/\/ the popover renders the same units — its copy of this run must flip too/);
  assert.match(UI, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\); renderTabs\(\); updateStatusline\(\); rerenderAll\(\); refillOpenCommentPop\(\); \}\);/);
});

// ── T106 (the user 2026-08-26, found by the romp-lab loop's first full pass): three seam fixes ────
test("a create refused by parse lag holds its mark until the kernel answers it — never dropped by the page", () => {
  // the kernel's typed nack: transient (the anchor record hasn't hit its parse yet) vs real
  const KERNELSRC = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNELSRC, /ANCHOR_LAG_ERR = "that message isn't in the transcript yet; try again in a moment"/);
  assert.match(KERNELSRC, /if err == ANCHOR_LAG_ERR:[\s\S]{0,1600}"createId": cid, "transient": True, "text": err\}\)\)/, "the lag answer is transient");
  assert.match(KERNELSRC, /else:\s*\n(?:\s*#[^\n]*\n)*\s*sys\.stderr\.write\("comment create refused[^\n]*\n\s*_answer_create_refused\(client, sid, str\(msg\["uuid"\]\), cid, err\)/,
    "no toast for plumbing the retry makes moot; real refusals stay loud");
  assert.match(KERNELSRC, /def _answer_create_refused\([\s\S]{0,1200}client\["send"\]\(json\.dumps\(\{"type": "warn", "text": err\}\)\)/, "the warn, on the sending socket");
  assert.match(KERNELSRC, /nack = \{"type": "commentCreateFailed", "id": sid, "uuid": anchor_uuid, "createId": cid, "transient": False, "text": err\}/);
  // …and the kernel PARKS the lag-refused create and retries it per pusher cycle (the file it is
  // waiting on is its own): a settled session emits no further frames, so the client-side re-post
  // alone starved — the park covers that, and ends in an answer to every page (2026-10-11)
  assert.match(KERNELSRC, /_parked_creates\.append\(\{"sid": sid, "uuid": str\(msg\["uuid"\]\)/);
  assert.match(KERNELSRC, /def _retry_parked_creates\(\):/);
  assert.match(KERNELSRC, /_retry_parked_creates\(\)   # lag-parked comment creates ride every pusher cycle \(T106\)/);
  assert.match(KERNELSRC, /_PARK_MAX_TRIES = 30/);
  // the client holds the payload at send until the kernel's answer; it re-posts on a socket reopening, never on a frame,
  // and gives up on no count of its own (review of PR 209, 2026-10-11: a quiet session's frames never came, so the mark
  // pended forever, and where they came the page handed the words back while the park could still make the thread)
  assert.match(UI, /cmtCreateInFlight\.set\(held\.createId, held\);/);   // the held create is the stamped one the send posted, keyed by its id
  assert.doesNotMatch(UI, /retryCmtCreates|CMT_CREATE_MAX_TRIES|couldn't anchor/, "no frame-keyed retry and no page-side give-up");
  // the ack retires the hold it names; a REAL refusal drops the synth honestly and hands the words back
  assert.match(UI, /cmtCreateInFlight\.delete\(held\.createId\);\s+\/\/ the ack retires the retry hold/);
  assert.match(UI, /if \(held && !m\.transient\) refuseCmtCreate\(held\);/);
  assert.match(UI, /function refuseCmtCreate\(c: CommentCreate\): void \{\s*\n\s*cmtCreateInFlight\.delete\(c\.createId\);\s*\n\s*dropSynthThread\(c\.sid, c\.createId\);/);
});

test("the optimistic synth thread survives comments frames until superseded or failed", () => {
  // the frame used to WIPE it: every create's mark blinked between the gesture and the real
  // thread's first frame, and a lag-refused create erased the comment entirely
  assert.match(UI, /const held = cmtCreateInFlight\.get\(t\.tid\.slice\("pending:"\.length\)\);\s*\n\s*return !!held && !synthSuperseded\(threads, held\);/,
    "the thread THIS create made supersedes the synth (synthSuperseded), never any thread on the same reply");
  assert.doesNotMatch(UI, /!threads\.some\(\(r\) => r\.anchorUuid === t\.anchorUuid\)/, "the any-thread-on-the-turn rule is gone");
});

test("the pending echo prunes against EVENTS too — a landed user turn never double-shows", () => {
  assert.match(UI, /const evUserMsgs = \(\(th\.events \|\| \[\]\) as ChatEvent\[\]\)/);
  assert.match(UI, /prunePending\(commentPending\.get\(th\.tid\) \|\| \[\], \[\.\.\.th\.msgs, \.\.\.evUserMsgs\]\);/);
});

test("the parity bundle (2026-08-26): dividers, owner-scoped in-turn controls, the sid stamp", () => {
  // day dividers open new days in the popover exactly as in the chat (same helper, same idiom)
  assert.match(UI, /const anchor = itemAnchor\(it, \(i\) => eventEpoch\(evs\[i\]\)\);\s*\n\s*const dayOpen = eventEpoch\(evs\[anchor\]\);[\s\S]{0,300}?if \(dayOpen != null\) \{\s*\n\s*const dv = dayDividerFor\(dayOpen, walk\);/);   // the T145 relay-note insert sits between; the unit timed by its anchor member, the divider decided against the walk's mark (T339)
  // the popover's list is stamped with the THREAD sid, and in-turn controls resolve their owner
  // from the DOM at click time — queued ✕ and the api-error card act on the thread, never the tab
  assert.match(UI, /list\.dataset\.session = threadSidOf\(th\);/);   // the thread's session: a linked machine's routes to its kernel
  assert.match(UI, /function owningSidOf\(el0: HTMLElement \| null\): string \| null \{/);
  assert.match(UI, /const sidQ = owningSidOf\(el\) \|\| activeId;/);   // resolved once — the optimistic arm reuses it
  assert.match(UI, /\{ type: "cancelQueued", id: sidQ, md: qmd \}/);
  assert.match(UI, /const own = owningSidOf\(b\);\s*\n\s*if \(vscodeApi\) vscodeApi\.postMessage\(\{ type: "apiRetry", id: own, manual: true \}\);/);   // the api-error card's delegate handler (2026-09-08), still owner-scoped
});

test("the thread's running turn offers the chat's stop affordance, owner-scoped to the THREAD (T138)", () => {
  // the user 2026-08-27: threads couldn't be interrupted from the UI at all — diagnosis (a), the
  // affordance was simply absent from the popover statusline (the chat's stopButton never had a
  // popover twin). The fix rides the stable body delegate (this statusline rebuilds per comments
  // frame — press-safety) and pins the target to the thread's own session, never the active tab.
  const at = UI.indexOf("function cmtStateChip(");
  const chip = UI.slice(at, UI.indexOf("\n}", at));
  assert.match(chip, /b\.dataset\.act = "cmtinterrupt";/);
  assert.match(chip, /b\.dataset\.sid = threadSidOf\(th\);/, "the thread's OWN session id rides the button (host-prefixed on a linked machine: the stop routes to its kernel)");
  assert.match(chip, /b\.dataset\.tid = th\.tid;/, "…and the thread, whose latches the stop clears");
  // since thread-live-1 (2026-10-14) the chat bar's own rule decides, through the one predicate: working, compacting,
  // retrying and an API error (blocked) offer it; a question or permission prompt does not (the picker answers that)
  assert.match(chip, /const st = threadChipState\(th\);/, "the chat's chip for the thread");
  assert.match(chip, /if \(chipStops\(st\)\) wrap\.appendChild\(stopBtn\(\)\);/, "the chat bar's stop rule");
});

test("the popover interrupt posts the THREAD sid, clears the send-latch on the gesture, and acks instantly", () => {
  const at = UI.indexOf("cmtinterrupt: (elx) => {");
  assert.ok(at > 0, "the delegated handler exists (a direct listener would die in the per-frame rebuild)");
  const body = UI.slice(at, UI.indexOf("},", at));
  assert.match(body, /const sid = \(elx as HTMLElement\)\.dataset\.sid;/);
  assert.match(body, /vscodeApi\.postMessage\(\{ type: "interrupt", id: sid \}\);/);
  assert.ok(!body.includes("activeId"), "never the active tab — the owner-scoping class queued-x/Retry were fixed for");
  // T102's latch contract at the interrupt edge: the exchange ends with NO reply record coming, so
  // the pulse clears on the gesture itself — the deciding event — not on a reply that never lands.
  assert.match(body, /const tid = \(elx as HTMLElement\)\.dataset\.tid \|\| sid;/, "the latches are the thread's, by its tid (the post is by its session id)");
  assert.match(body, /cmtAwaitBase\.delete\(tid\);/);
  // …and the OWED arm stands down too (lab phase 4e caught it: replyOwed's trailing-user shape held
  // the mark green forever after a stop). The tombstone is record-count-keyed, never a clock, and a
  // fresh send retires it (re-owed).
  assert.match(body, /cmtInterrupted\.add\(tid\);/);
  assert.match(UI, /return owed && !cmtInterrupted\.has\(th\.tid\);/,   // owed = the kernel's replyOwed, else the msgs read (T237)
    "gesture-pair tombstone: stop sets it, ONLY the next send clears it — the CLI files the interrupt "
    + "as a trailing user-kind record, so any record-shape re-derivation would re-fire (lab-caught)");
  assert.match(UI, /cmtInterrupted\.delete\(cur\.th\.tid\);/);
  assert.match(body, /chip chip-interrupting/, "instant acknowledgment: the chip flips before any kernel round-trip");
});

test("a contentless open thread NEVER renders blank — the loader stays past the backstop (T152)", () => {
  // the live specimen: a fork of a 40MB parent took 200+s to spend across kernel restarts; the 8s
  // boot backstop expired and the popover showed the quote floating over an empty list. The
  // backstop now swaps the loader's LABEL (an honest 'taking longer'), never to blank; error and
  // stuck branches still take over on the frame that says so.
  const at = UI.indexOf('if (!evs.length && !th.msgs.length && th.status === "open" && !th.error) {');
  assert.ok(at > 0, "the never-blank guard exists, gated on BOTH projections being empty");
  const body = UI.slice(at, UI.indexOf("return;", at));
  assert.match(body, /const slow = !cmtBootHolds\(th\.tid\);/);
  assert.match(body, /still opening — the thread's session is taking longer than usual…/);
  assert.match(body, /opening the thread…/);
});

// ── the create's identity: one id per send gesture, the same one on every re-post ─────────────────
// The kernel remembers each create it completed and answers a repeat of it with the same thread's ack.
// Keyed on the words (anchor, passage, text) that memo also swallowed a DELIBERATE second comment in the
// same words on the same passage (review, 2026-09-09). The client stamps the gesture instead: the popover
// mints a createId at the send and every re-post of that gesture carries it, so a repeat is a frame with
// an id the kernel has seen and a fresh gesture never is.
const ANCHOR = { sid: "aaaaaaaa-1111-2222-3333-444444444444", uuid: "a1", exact: "exponential backoff",
                 model: "", effort: "", fast: "", color: "" };

test("a send gesture mints its own create id; a second gesture in the same words on the same passage gets another", () => {
  const first = newCommentCreate(ANCHOR, "fix this", "");
  const second = newCommentCreate(ANCHOR, "fix this", "");
  assert.ok(first.createId.length > 0, "the gesture is stamped");
  assert.notEqual(first.createId, second.createId, "two gestures, two ids, whatever their words");
  assert.ok(!("tries" in first), "no retry count: the page re-posts on a reconnect and gives up on nothing of its own");
});

test("the create frame carries the id, and a re-post of the held create is the same frame", () => {
  const held = newCommentCreate({ ...ANCHOR, model: "claude-opus-5", effort: "high", fast: "on", color: "#112233" },
                                "why jitter?", "why-jitter");
  const sent = commentCreateFrame(held);
  assert.deepEqual(sent, { type: "commentCreate", id: ANCHOR.sid, uuid: "a1", exact: "exponential backoff",
                           text: "why jitter?", name: "why-jitter", model: "claude-opus-5", effort: "high",
                           fast: "on", color: "#112233", createId: held.createId });
  assert.deepEqual(commentCreateFrame(held), sent, "a re-post is the same gesture: the same id, the same words");
});

test("an anchor with no picks sends empty picks and an empty name for the kernel's default", () => {
  const held = newCommentCreate({ sid: ANCHOR.sid, uuid: "a1", exact: "exponential backoff" }, "fix this", "");
  const sent = commentCreateFrame(held);
  assert.equal(sent.name, "");
  assert.equal(sent.model, ""); assert.equal(sent.effort, ""); assert.equal(sent.fast, ""); assert.equal(sent.color, "");
});

test("the popover's send and its re-post on a reconnect both post the held create's frame", () => {
  const send = UI.slice(UI.indexOf("function commentSendFromPop("));
  const sendBody = send.slice(0, send.indexOf("\nfunction ", 10));
  assert.match(sendBody, /const held = newCommentCreate\(create, text, nm\)/, "the send mints the gesture's id");
  assert.match(sendBody, /vscodeApi\.postMessage\(commentCreateFrame\(held\)\)/);
  assert.match(sendBody, /cmtCreateInFlight\.set\(held\.createId, held\)/, "the hold IS the stamped create, keyed by its id");
  const repost = UI.slice(UI.indexOf("function repostCmtCreates("));
  const repostBody = repost.slice(0, repost.indexOf("\n}\n") + 3);
  assert.match(repostBody, /vscodeApi\?\.postMessage\(commentCreateFrame\(c\)\)/, "the re-post sends the held frame, id included");
  assert.doesNotMatch(UI, /type: "commentCreate", id:/, "no hand-built create frame remains");
});

// ── executed: the re-post on a reconnect (repostCmtCreates and its two listeners, lifted from render.ts) ──
// The hold lives until the kernel's answer (review of PR 209, 2026-10-11). What that answer cannot survive is the kernel
// losing the create (a restart forgets its park) or the page missing the answer (sent while its socket was down), and both
// are a socket reopening: the local socket's romp:wsup re-posts every hold, a remote host's romp:hostRelayUp that host's.
// A session frame re-posts nothing, and no count of frames or re-posts ever drops a hold.
function liftRepost(held: CommentCreate[]) {
  const start = UI.indexOf("function repostCmtCreates(mine: (c: CommentCreate) => boolean): void {");
  assert.ok(start > 0, "the re-post lives in repostCmtCreates");
  const fn = UI.slice(start, UI.indexOf("\n}\n", start) + 3).replace("(mine: (c: CommentCreate) => boolean): void {", "(mine) {");
  // its two callers, each run here from its own source line: the local socket's reopen (this kernel's holds) and, inside the
  // existing relay listener beside that host's other resets, a remote host's relay reopen (that host's)
  const wsupLine = 'window.addEventListener("romp:wsup", () => repostCmtCreates((c) => !hostOf(c.sid)));';
  assert.ok(UI.includes(wsupLine), "the local socket's reopen re-posts this kernel's holds");
  const relay = UI.slice(UI.indexOf('window.addEventListener("romp:hostRelayUp", (e) => {'));
  const relayLine = "if (h) repostCmtCreates((c) => hostOf(c.sid) === h);";
  assert.ok(relay.slice(0, relay.indexOf("\n});\n")).includes(relayLine), "a relay's reopen re-posts that host's, in the existing listener");
  const src = fn + "\n" + wsupLine + "\nwindow.addEventListener(\"romp:hostRelayUp\", (e) => { const h = String((e.detail || {}).host || \"\"); "
    + relayLine + " });";
  const posted: ReturnType<typeof commentCreateFrame>[] = [];
  const listeners: Record<string, ((e: any) => void)[]> = {};
  const win = { addEventListener: (k: string, f: (e: any) => void) => { (listeners[k] = listeners[k] || []).push(f); } };
  const cmtCreateInFlight = new Map(held.map((c) => [c.createId, c] as [string, CommentCreate]));
  const hostOf = (sid: string) => { const i = sid.indexOf(":"); return i > 0 ? sid.slice(0, i) : ""; };
  new Function("cmtCreateInFlight", "commentCreateFrame", "vscodeApi", "window", "hostOf",
               src)(cmtCreateInFlight, commentCreateFrame, { postMessage: (m: any) => posted.push(m) }, win, hostOf);
  const fire = (k: string, detail?: unknown) => (listeners[k] || []).forEach((f) => f({ detail }));
  return { fire, posted, cmtCreateInFlight };
}

test("a held create re-posts its own frame when its kernel's socket reopens: the local socket, or its host's relay", () => {
  const local = newCommentCreate(ANCHOR, "fix this", "fix-this");
  const remote = newCommentCreate({ ...ANCHOR, sid: "TESTHOST:bbbbbbbb-1111-2222-3333-444444444444", uuid: "b1" }, "and here", "");
  const { fire, posted, cmtCreateInFlight } = liftRepost([local, remote]);
  fire("romp:wsup");
  assert.deepEqual(posted, [commentCreateFrame(local)],
                   "the local socket's reopen: this kernel's hold, its own frame and id. Never a remote's: sent while that host's " +
                   "relay is down, federation refuses it for good, and the words would come back while the remote's park could still make the thread");
  posted.length = 0;
  fire("romp:hostRelayUp", { host: "TESTHOST" });
  assert.deepEqual(posted, [commentCreateFrame(remote)], "a relay's reopen: that host's holds alone");
  posted.length = 0;
  fire("romp:hostRelayUp", {});
  assert.deepEqual(posted, [], "a reopen naming no host re-posts nothing");
  for (let i = 0; i < 50; i++) fire("romp:wsup");
  assert.equal(cmtCreateInFlight.size, 2, "no count of reconnects drops a hold: only the kernel's answer does");
});

test("a session frame re-posts nothing and drops no hold: the kernel's answer alone ends a create", () => {
  const body = (name: string) => { const i = UI.indexOf("function " + name + "("); return UI.slice(i, UI.indexOf("\n}\n", i)); };
  for (const fn of ["upsert", "update"]) {
    assert.doesNotMatch(body(fn), /CmtCreate|cmtCreateInFlight/, fn + " touches no held create");
  }
  // the hold leaves on the kernel's two answers, the thread (commentCreated) and a refusal that is not transient, and on the
  // one event after which no answer can come: its host leaving the mesh, whose relay never reopens (review of PR 211)
  assert.equal((UI.match(/cmtCreateInFlight\.delete\(/g) || []).length, 2, "refuseCmtCreate and the commentCreated arm");
  assert.equal((UI.match(/refuseCmtCreate\(/g) || []).length, 3, "its definition, the typed refusal's arm and a detached host's holds, nothing else");
  assert.match(UI, /else if \(m\.type === "commentCreateFailed" && m\.id && m\.uuid\) \{[\s\S]{0,1200}if \(held && !m\.transient\) refuseCmtCreate\(held\);/);
  assert.equal((UI.match(/loseCmtCreatesOf\(/g) || []).length, 2, "its definition and the host drop's dismissal alone");
  assert.match(UI, /if \(why === "hostDrop"\) loseCmtCreatesOf\(id\);/);
});

// ── the outcome reaches the page wherever it is (review of PR 211, 2026-10-11) ──────────────────────

test("a refusal sent while the page's socket reopened carries `say`, and the page holding the create toasts the reason", () => {
  // the kernel's warn goes to the socket that sent the create; a page that re-posted on a new socket meanwhile is sent the
  // typed refusal with `say`, and only the page holding that create (holdAnswered) reads it
  assert.match(KERNEL, /c\["send"\]\(json\.dumps\(nack if c is client else dict\(nack, say=True\)\)\)/);
  assert.match(UI, /if \(held && !m\.transient\) refuseCmtCreate\(held\);\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(held && !m\.transient && m\.say === true && typeof m\.text === "string" && m\.text\) warnToast\(m\.text\);/);
});

// (a dropped create's dialog, drawn and acknowledged entry by entry on both pages: comment-drops.test.ts, review of PR 218)

// ── executed: a host that left the mesh hands its unanswered creates back (loseCmtCreatesOf, lifted) ──
test("a host leaving the mesh hands back its sessions' unanswered creates: words to the box, one dialog with them, a Log entry each", () => {
  const start = UI.indexOf("function loseCmtCreatesOf(sid: string): void {");
  assert.ok(start > 0, "the hand-back lives in loseCmtCreatesOf");
  const src = UI.slice(start, UI.indexOf("\n}\n", start) + 3).replace("(sid: string): void {", "(sid) {");
  const remote = "TESTHOST:" + ANCHOR.sid;
  const a = newCommentCreate({ ...ANCHOR, sid: remote }, "Why ten percent?", "");
  const b = newCommentCreate({ ...ANCHOR, sid: remote, uuid: "a2" }, "And the cap?", "");
  const other = newCommentCreate(ANCHOR, "fix this", "");
  const inFlight = new Map([a, b, other].map((c) => [c.createId, c] as [string, CommentCreate]));
  const refused: string[] = [], logged: string[] = [], dialogs: { title: string; text: string; cb: (v: string) => void }[] = [], copied: string[] = [];
  const hostOf = (sid: string) => { const i = sid.indexOf(":"); return i > 0 ? sid.slice(0, i) : ""; };
  const lose = new Function("cmtCreateInFlight", "refuseCmtCreate", "notifyShell", "showConfirm", "hostOf", "navigator", src + "\nreturn loseCmtCreatesOf;")(
    inFlight, (c: CommentCreate) => { inFlight.delete(c.createId); refused.push(c.text); },
    (kind: string, text: string) => logged.push(kind + "|" + text),
    (title: string, text: string, _b: unknown, cb: (v: string) => void) => dialogs.push({ title, text, cb }),
    hostOf, { clipboard: { writeText: (t: string) => copied.push(t) } }) as (sid: string) => void;
  lose(remote);
  assert.deepEqual(refused, ["Why ten percent?", "And the cap?"], "that session's holds end, their words back in the comment box");
  assert.deepEqual(Array.from(inFlight.keys()), [other.createId], "a hold on a session elsewhere is untouched");
  assert.equal(dialogs.length, 1, "one dialog for the lot");
  assert.equal(dialogs[0].title, "Those comments may not have started threads");
  assert.match(dialogs[0].text, /TESTHOST/, "it names the machine");
  assert.doesNotMatch(dialogs[0].text, /did not|weren't|was not/, "it claims no outcome: that machine may have started them");
  dialogs[0].cb("copy");
  assert.deepEqual(copied, ["Why ten percent?\n\nAnd the cap?"], "Copy my text carries every word");
  assert.equal(logged.length, 2, "a not-sent Log entry per comment");
  assert.ok(logged.every((l) => l.startsWith("undelivered|")));
  lose(ANCHOR.sid + "-none");
  assert.equal(dialogs.length, 1, "no hold, no dialog");
});

// ── a thread that became its own session (the user 2026-09-10, with a screenshot) ─────────────────

test("a promoted thread's popup is the quote, one line, and Open the session in the shared button dress", () => {
  // the kernel ships a promoted thread with no messages or events, so the popup renders no list for it:
  // an empty list only grew into the box's fixed height (the void under the quote)
  assert.match(UI, /if \(th && th\.status !== "promoted"\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*const list = el\("div", "cmt-msgs"\);/);
  const start = UI.indexOf("} else if (th) {\n    // a thread that became its own session");
  assert.ok(start > 0, "the promoted branch of renderCommentPopover");
  const branch = UI.slice(start, UI.indexOf("document.body.appendChild(pop);", start));
  assert.match(branch, /const open = el\("button", "cmt-act"\) as HTMLButtonElement;/,
               "the shared .cmt-act word-button, never the composer's send-glyph square");
  assert.doesNotMatch(branch, /"cmt-send"/);
  assert.match(branch, /open\.textContent = "Open the session";/);
  assert.match(branch, /open\.dataset\.act = "cmtopensession";/);
  assert.match(branch, /const row = el\("div", "cmt-actions"\);[\s\S]*row\.appendChild\(open\);\s*\n\s*pop\.appendChild\(row\);/, "inside .cmt-actions");
  assert.match(branch, /const note = el\("div", "cmt-note"\);\s*\n\s*note\.textContent = "The discussion continues there\.";/,
               "one line saying where the talk went");
  // and the quote never flexes in this state, whatever size the box is (the .sized rule hands it the free room otherwise)
  assert.match(CSS, /\.cmt-pop\.sized\[data-status="promoted"\] \.cmt-quote \{ flex: 0 0 auto; min-height: 0; \}/);
});

// ── the mark never lands between a table's cells (T349, the user 2026-09-11) ──────────────────────
// The whitespace text between <td>s and <tr>s sits directly under the table's structural elements; an inline <mark>
// placed there gets its own anonymous cell and the columns shift. The DOM pass asks this before wrapping each slice.
test("markSkipsParent: a table's structural parents are skipped, every text-bearing parent is wrapped", () => {
  for (const tag of ["TABLE", "THEAD", "TBODY", "TFOOT", "TR"]) assert.equal(markSkipsParent(tag), true, tag);
  for (const tag of ["TD", "TH", "P", "LI", "CODE", "STRONG", "EM", "SPAN", "DIV", "CAPTION", "A"]) assert.equal(markSkipsParent(tag), false, tag);
  assert.equal(markSkipsParent(null), false); assert.equal(markSkipsParent(undefined), false); assert.equal(markSkipsParent(""), false);
  // a selection across a row: the cells' own text nodes are wrapped, the separators between them are not
  const nodes = [{ text: "Charlie", parent: "TD" }, { text: "\n", parent: "TR" }, { text: "12", parent: "TD" }, { text: "\n", parent: "TR" }, { text: "done", parent: "TD" }];
  const r = findExact(nodes.map((n) => n.text).join(""), "Charlie 12 done")!;
  const wrapped = sliceRanges(nodes.map((n) => n.text.length), r.start, r.end).filter((sl) => !markSkipsParent(nodes[sl.idx].parent)).map((sl) => nodes[sl.idx].text);
  assert.deepEqual(wrapped, ["Charlie", "12", "done"], "one mark per cell, none in the row itself");
});

// ── one comment per send, and an answer for exactly that send (2026-10-01) ─────────────────────────────────────────
// The user pressed Enter in a new comment, saw nothing change for seconds (the kernel reads the whole transcript before
// it answers), and pressed again, or clicked away, re-selected the passage and sent the words the box still held: two
// threads for one comment. An ack keyed by the reply it anchors adopted a
// thread into whatever comment box was open on the session. Synthetic text only.
const SID = "aaaaaaaa-1111-2222-3333-444444444444";

test("an answer names its send by the echoed createId; an older kernel's (turn only) falls to the oldest hold on that turn", () => {
  const a = newCommentCreate({ sid: SID, uuid: "a1", exact: "exponential backoff" }, "why?", "");
  const b = newCommentCreate({ sid: SID, uuid: "a1", exact: "with a jitter" }, "and this?", "");
  const c = newCommentCreate({ sid: SID, uuid: "a2", exact: "the retry cap" }, "how long?", "");
  const holds = [a, b, c];
  assert.equal(holdAnswered(holds, SID, "a1", b.createId), b, "two comments on one reply: the id says which");
  assert.equal(holdAnswered(holds, SID, "a1", "someone-elses-id"), null, "another page's send is nobody's here");
  assert.equal(holdAnswered(holds, SID, "a1", undefined), a, "no id: the oldest hold on the turn");
  assert.equal(holdAnswered(holds, SID, "a1", ""), a);
  assert.equal(holdAnswered(holds, SID, "a3", undefined), null);
  assert.equal(holdAnswered(holds, "bbbbbbbb-1111-2222-3333-444444444444", "a2", undefined), null, "the session counts too");
});

test("a second Comment on the same passage finds the send still unanswered there: same turn, overlapping text", () => {
  const a = newCommentCreate({ sid: SID, uuid: "a1", exact: "exponential backoff with jitter" }, "why?", "");
  assert.equal(holdForPassage([a], SID, "a1", "exponential  backoff\nwith jitter"), a, "whitespace aside");
  assert.equal(holdForPassage([a], SID, "a1", "exponential backoff"), a, "a passage inside the sent one is the same passage");
  const prose = "Use exponential backoff with jitter of ten percent, and cap the delay at two minutes.";
  assert.equal(holdForPassage([a], SID, "a1", "jitter of ten percent", prose), a, "overlapping in the turn's prose");
  assert.equal(holdForPassage([a], SID, "a1", "cap the delay", prose), null, "another passage of the same reply is another comment");
  assert.equal(holdForPassage([a], SID, "a2", "exponential backoff with jitter"), null, "same words in another reply");
  assert.equal(holdForPassage([a], "bbbbbbbb-1111-2222-3333-444444444444", "a1", "exponential backoff with jitter", prose), null,
               "the same turn uuid in another session");
});

test("the optimistic mark stays until the frame lists the thread ITS create made — not any thread on the same reply", () => {
  const thr = (tid: string, anchorUuid: string, exact: string): CommentThread =>
    ({ tid, anchorUuid, exact, status: "open", createdT: 0, state: "", unread: false, promotedName: "", msgs: [] });
  const held = newCommentCreate({ sid: SID, uuid: "a1", exact: "with a jitter" }, "and this?", "");
  held.prior = ["t-old"];
  const older = thr("t-old", "a1", "exponential backoff");
  assert.equal(synthSuperseded([older], held), false, "an earlier thread elsewhere in the reply is not this one (the old rule dropped the mark here)");
  assert.equal(synthSuperseded([older, thr("t-other", "a1", "exponential backoff")], held), false, "a new thread on another passage is not it");
  assert.equal(synthSuperseded([older, thr("t-new", "a2", "with a jitter")], held), false, "nor the same words in another reply");
  assert.equal(synthSuperseded([older, thr("t-new", "a1", "with a jitter")], held), true, "the new thread on this passage is");
  const same = newCommentCreate({ sid: SID, uuid: "a1", exact: "exponential backoff" }, "again", "");
  same.prior = ["t-old"];
  assert.equal(synthSuperseded([older], same), false, "a thread the page already had on the identical passage is not this create's");
  const long = "x".repeat(2400);
  const capped = newCommentCreate({ sid: SID, uuid: "a1", exact: long }, "long one", "");
  capped.prior = [];
  assert.equal(synthSuperseded([thr("t-cap", "a1", long.slice(0, 2000))], capped), true, "the kernel's 2000-character cap is the same passage");
});

test("the send is one per gesture, and the box shows it at once (source pins)", () => {
  const send = UI.slice(UI.indexOf("function commentSendFromPop("));
  const sendBody = send.slice(0, send.indexOf("\nfunction ", 10));
  assert.match(sendBody, /if \(create\) \{[\s\S]{0,400}if \(create\.sent\) return;/, "a box that has sent has nothing left to send");
  assert.match(sendBody, /create\.sent = \{ createId: held\.createId, text, name: synth\.name \|\| "" \};/);
  // the sent view is painted BEFORE the frame is posted: a refusal that comes back synchronously (a remote host's dropped
  // gesture) must land on it, not be painted over by it
  const view = sendBody.indexOf("renderCommentPopover();");
  const post = sendBody.indexOf("vscodeApi.postMessage(commentCreateFrame(held));");
  assert.ok(view > 0 && post > view, "the sent view first, the post last");
  const pop = UI.slice(UI.indexOf("function renderCommentPopover("));
  const popBody = pop.slice(0, pop.indexOf("\nfunction ", 10));
  assert.match(popBody, /const status = th \? th\.status : sent \? "sent" : "";/, "the send flips the refresh key: a full rebuild");
  assert.match(popBody, /if \(sent\) \{[\s\S]{0,1400}rompLoaderInner\("opening the thread…", \{ wordmark: false \}\)\);[^\n]*\n\s*list\.append\(boot, cmtPendingQueued\(\[\{ text: sent\.text, t: Date\.now\(\) \/ 1000 \}\]\)\);/,
               "the loader first (ui/CLAUDE.md), the thread boot's own, and the words as the chat's own pending bubble");
  // nothing to type or send in the sent view: the composer row stays where the thread's will be, read-only and disarmed
  assert.match(popBody, /box\.readOnly = !!sent;\s*\n\s*box\.value = sent \? "" : \(commentDrafts\.get\(dk\) \|\| ""\);/);
  assert.match(popBody, /if \(sent\) \{ send\.disabled = true; send\.classList\.add\("busy"\); attach\.disabled = true; \}/);
  assert.match(popBody, /if \(create && !sent\) \{/, "the picks went with the send");
});

test("a second Comment on a passage whose send is unanswered re-opens on that send (source pin)", () => {
  const open = UI.slice(UI.indexOf("function openCommentComposer("));
  const body = open.slice(0, open.indexOf("\nfunction ", 10));
  assert.match(body, /const held = holdForPassage\(cmtCreateInFlight\.values\(\), sid, uuid, exact, prose, at\);/,
               "the check for a send in flight reads the live range (executed by the test 'New thread instead carries the "
               + "selection's live range' and by test_comment_layout_existing_served.py's in-flight repeat test)");
  assert.match(body, /const live = !!held && \(commentThreads\.get\(sid\) \|\| \[\]\)\.some\(\(t\) => t\.tid === "pending:" \+ held\.createId\);/,
               "only while its mark is still the optimistic one: once its thread is listed the passage opens a fresh box (no trap on a lost ack)");
  assert.match(body, /sent: \{ createId: held\.createId, text: held\.text,/);
});

test("the ack moves only its own send, and only the box that sent it adopts the thread (source pins)", () => {
  const ack = UI.split('else if (m.type === "commentCreated" && m.id && m.tid) {')[1].split("\n  }\n  // a reply the kernel refused")[0];
  assert.match(ack, /const held = holdAnswered\(cmtCreateInFlight\.values\(\), String\(m\.id\), String\(m\.uuid \|\| ""\), m\.createId\);/);
  assert.match(ack, /if \(pendingCommentAnchor\?\.sent\?\.createId === held\.createId\) \{/, "never any create box on the session");
  assert.doesNotMatch(ack, /pendingCommentAnchor && pendingCommentAnchor\.sid === m\.id/, "the any-box-on-the-session adoption is gone");
  // the words stay on screen through the thread's start, as the pending bubble prunePending retires when the record lands
  assert.match(ack, /commentPending\.set\(tid, \[\.\.\.\(commentPending\.get\(tid\) \|\| \[\]\), \{ text: held\.text, t: Date\.now\(\) \/ 1000 \}\]\);/);
  assert.match(UI, /if \(pendingCommentAnchor\?\.sent\?\.createId === pendingAdopt\.createId\) adoptCommentThread\(sid, pendingAdopt\.tid\);/,
               "an ack that beat its frame adopts only into the box that is still showing that send");
});

test("bubbles and reply drafts are pruned against every session's threads, never one frame's list (source pins)", () => {
  assert.doesNotMatch(UI, /const live = new Set\(threads\.filter\(\(t\) => t\.status !== "promoted"\)\.map\(\(t\) => t\.tid\)\);/,
                      "a frame for one session wiped every other session's unsent replies and pending bubbles");
  assert.match(UI, /commentThreads\.forEach\(\(list\) => list\.forEach\(\(t\) => \{ if \(t\.status !== "promoted"\) live\.add\(t\.tid\); \}\)\);/);
  assert.match(UI, /if \(!k\.startsWith\("new:"\) && !k\.startsWith\("newname:"\) && !live\.has\(k\)\) commentDrafts\.delete\(k\);/,
               "the create box's typed name is a draft too");
});

test("every create answer echoes the gesture's createId; a create a remote host never got is refused by type (source pins)", () => {
  assert.match(KERNEL, /cid = str\(msg\.get\("createId"\) or ""\)\s*\n\s*key = _create_key\(sid, msg\["uuid"\], msg\["exact"\], msg\["text"\], cid\)/);
  assert.equal((KERNEL.match(/"createId": cid/g) || []).length, 8,
               "the busy and dropped answers at the door, the job's lag and refused answers, the parked copy, and the drop's broadcast, dialog and undelivered row");
  assert.match(KERNEL, /_comment_create_spawn\(_answer_comment_created, \(\[client\], sid, again, str\(msg\["uuid"\]\), cid\)\)/, "the repeat's");
  assert.match(KERNEL, /_answer_comment_created\(_create_answer_targets\(client\), sid, tid, str\(msg\["uuid"\]\), cid\)/,
               "the created answer's, to the sending socket and every chat page (review of PR 211: a page that reconnected mid-job)");
  assert.match(KERNEL, /"createId": create_id or ""/, "the one answer both carry it in");
  assert.match(KERNEL, /_answer_comment_created\(targets, pk\["sid"\], tid, pk\["uuid"\], pk\.get\("createId"\) or ""\)/, "and the parked create's broadcast");
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \(msg && msg\.type === "commentCreate" && typeof msg\.id === "string" && msg\.uuid\)\s*\n\s*window\.dispatchEvent\(new MessageEvent\("message", \{ data: \{ type: "commentCreateFailed", id: prefixId\(host, msg\.id\),\s*\n\s*uuid: msg\.uuid, createId: msg\.createId \|\| "", transient: false \} \}\)\);/);
});

test("a pending group with no turn before it draws no rail stub (source pin)", () => {
  // the sent view and the thread's boot view hold the pending bubble with nothing above it on the rail: the chat's last-turn
  // stub (.turn:not(:has(~ .turn))::before, 16px) drew alone left of "sending…", a stray caret-like line (2026-10-01)
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.cmt-msgs > \.turn-queued:not\(\.turn ~ \.turn-queued\)::before \{ display: none; \}/);
  assert.match(CSS, /\.cmt-msgs \.turn \+ \.turn::before \{ top: -6px; \}/, "after a turn it keeps its segment, continuous");
});

// ── a passage that already has a thread opens it (the user 2026-10-01) ─────────────────────────────────────────────
// "The same passage", at the anchor level: the same turn, and texts that OVERLAP in that turn's rendered prose, each found
// the way its highlight is drawn. Synthetic text only.
const PROSE = "Keep the retry cap at two minutes on every sync path. Longer caps only delay the error the user will see anyway.";

test("the same passage: overlapping in the turn's prose; touching or apart is two passages", () => {
  assert.equal(samePassage(PROSE, "retry cap at two minutes", "two minutes on every sync path"), true, "a shared run of text");
  assert.equal(samePassage(PROSE, "Keep the retry cap at two minutes on every sync path", "on every sync"), true, "one inside the other");
  assert.equal(samePassage(PROSE, "Keep the retry cap at two minutes", " on every sync path"), false, "touching, not overlapping");
  assert.equal(samePassage(PROSE, "Keep the retry cap", "Longer caps only delay the error"), false, "two sentences apart");
  assert.equal(samePassage(PROSE, "retry  cap at\ntwo minutes", "two minutes on"), true, "whitespace as the highlight reads it");
});

test("without the turn's prose (scrolled out), the same passage is containment of the normalized texts", () => {
  assert.equal(samePassage(null, "retry cap at two minutes", "cap at two"), true);
  assert.equal(samePassage(null, "retry cap at two minutes", "two minutes on every sync path"), false, "an overlap it cannot see is never claimed");
  assert.equal(samePassage("", "Keep the retry cap", "retry   cap"), true);
  assert.equal(samePassage(PROSE, "a passage not in this turn", "a passage not in this turn at all"), true, "unfound in the prose: containment");
});

test("the thread a Comment opens instead: same turn, overlapping, talkable, the newest of several", () => {
  const thr = (over: Partial<CommentThread>): CommentThread => ({ tid: "t", anchorUuid: "a2", exact: "", status: "open",
    createdT: 0, state: "", unread: false, promotedName: "", msgs: [], ...over });
  const old = thr({ tid: "t-old", exact: "Keep the retry cap at two minutes", createdT: 10 });
  const newer = thr({ tid: "t-new", exact: "retry cap", createdT: 20 });
  const elsewhere = thr({ tid: "t-else", exact: "Longer caps only delay the error", createdT: 30 });
  const otherTurn = thr({ tid: "t-turn", anchorUuid: "a1", exact: "Keep the retry cap at two minutes", createdT: 40 });
  assert.equal(existingThreadFor([old, elsewhere, otherTurn], "a2", "cap at two minutes on every", PROSE)?.tid, "t-old");
  assert.equal(existingThreadFor([old, newer, elsewhere], "a2", "the retry cap", PROSE)?.tid, "t-new", "the newest overlapping one");
  assert.equal(existingThreadFor([old, elsewhere], "a2", "on every sync path", PROSE), null, "no overlap: a new thread");
  assert.equal(existingThreadFor([otherTurn], "a2", "Keep the retry cap", PROSE), null, "another reply's thread is not this passage's");
  for (const status of ["resolved", "promoted", "promoting"] as const)
    assert.equal(existingThreadFor([thr({ tid: "t-x", exact: "Keep the retry cap", status })], "a2", "retry cap", PROSE), null,
                 status + " takes no reply: a new comment starts a new thread");
  for (const status of ["open", "merged", "merging"] as const) assert.equal(threadTakesReplies(status), true, status);
  assert.equal(existingThreadFor([thr({ tid: "pending:abc", exact: "Keep the retry cap" })], "a2", "retry cap", PROSE), null,
               "an optimistic mark is the in-flight send's, which re-opens its own sent view");
});

// A phrase that REPEATS in the turn: each text is located at its first occurrence. `at` is where it was selected (render.ts
// selectionAt); a passage is also the same when the live range overlaps it. Synthetic text only.
const REPEATS = "The importer warms the cache before the first request. Each export then refills the cache from the database, "
  + "and the worker drains the queue. After a restart the worker drains the queue again.";
const THREAD_ON = "Each export then refills the cache from the database, and the worker drains the queue";
const nth = (hay: string, text: string, n: number) => {
  let at = -1;
  for (let i = 0; i <= n; i++) at = hay.indexOf(text, at + 1);
  assert.ok(at >= 0, text + " #" + n);
  return { start: at, end: at + text.length };
};

test("a repeated phrase is the same passage when it was selected inside the other, or when its first occurrence is", () => {
  const inside = nth(REPEATS, "the cache", 1), outside = nth(REPEATS, "drains the queue", 1);
  assert.ok(nth(REPEATS, "the cache", 0).end <= REPEATS.indexOf(THREAD_ON), "the first \"the cache\" is outside the passage");
  assert.ok(outside.start >= REPEATS.indexOf(THREAD_ON) + THREAD_ON.length, "the second \"drains the queue\" is outside the passage");
  assert.equal(samePassage(REPEATS, THREAD_ON, "the cache", inside), true, "selected inside the passage, though its first occurrence is not");
  assert.equal(samePassage(REPEATS, THREAD_ON, "the cache"), false, "with no live range the first occurrence decides");
  assert.equal(samePassage(REPEATS, THREAD_ON, "drains the queue", outside), true,
               "selected outside: its first occurrence is inside the passage");
  assert.equal(samePassage(REPEATS, THREAD_ON, "warms the cache", nth(REPEATS, "warms the cache", 0)), false, "neither: two passages");
  assert.equal(samePassage(REPEATS, "the first request", ". Each export", nth(REPEATS, ". Each export", 0)), false,
               "a live range that only touches the passage's end is another passage");
  assert.equal(samePassage(REPEATS, ". Each export", "the first request", nth(REPEATS, "the first request", 0)), false,
               "a live range that only touches the passage's start is another passage");
  assert.equal(samePassage(REPEATS, "Each export", "The importer", { start: 0, end: 12 }), false, "ranges apart stay apart");
});

test("a live range that is not a real range inside the turn's prose is ignored, as if none were given", () => {
  // each malformed range sits on the passage, so accepting it would say true. One case per check atIn makes on a range: an
  // empty one, a negative start, a fractional start, a fractional end, an end past the prose. null and undefined are the
  // missing-range guard's cases, not ranges
  const inside = nth(REPEATS, "the cache", 1);
  assert.equal(samePassage(REPEATS, THREAD_ON, "the cache", inside), true, "the control: the real range there counts");
  for (const at of [{ start: inside.start, end: inside.start }, { start: -1, end: inside.end }, { start: inside.start + 0.5, end: inside.end },
                    { start: inside.start, end: inside.end + 0.5 }, { start: 0, end: REPEATS.length + 1 }, null, undefined] as
                    Array<{ start: number; end: number } | null | undefined>)
    assert.equal(samePassage(REPEATS, THREAD_ON, "the cache", at), false, JSON.stringify(at));
  assert.equal(samePassage(null, THREAD_ON, "the cache", nth(REPEATS, "the cache", 1)), true,
               "no prose at hand: containment");
});

test("the thread a Comment opens and the send it re-opens both read the live range", () => {
  const inside = nth(REPEATS, "the cache", 1), outside = nth(REPEATS, "drains the queue", 1);
  const t2 = th({ tid: "t-two", anchorUuid: "a3", exact: THREAD_ON, createdT: 10 });
  assert.equal(existingThreadFor([t2], "a3", "the cache", REPEATS, inside)?.tid, "t-two", "selected inside its highlight");
  assert.equal(existingThreadFor([t2], "a3", "the cache", REPEATS), null, "no live range: the first occurrence, outside");
  assert.ok(outside.start >= REPEATS.indexOf(THREAD_ON) + THREAD_ON.length, "the second \"drains the queue\" is outside the passage");
  assert.equal(existingThreadFor([t2], "a3", "drains the queue", REPEATS, outside)?.tid, "t-two", "selected outside: its first occurrence is inside");
  const held = newCommentCreate({ sid: SID, uuid: "a3", exact: THREAD_ON }, "does the refill block exports?", "");
  assert.equal(holdForPassage([held], SID, "a3", "the cache", REPEATS, inside), held, "a send in flight on the passage re-opens");
  assert.equal(holdForPassage([held], SID, "a3", "the cache", REPEATS), null);
});

test("several threads match, or several sends: the one the selection was made inside comes before one only its first occurrence meets", () => {
  const inside = nth(REPEATS, "the cache", 1), first = nth(REPEATS, "the cache", 0);
  const t2 = th({ tid: "t-two", anchorUuid: "a3", exact: THREAD_ON, createdT: 10 });
  const t3 = th({ tid: "t-three", anchorUuid: "a3", exact: "warms the cache", createdT: 20 });   // newer, on the first occurrence
  assert.ok(first.start >= REPEATS.indexOf("warms the cache") && first.end <= REPEATS.indexOf("warms the cache") + 15
            && inside.start >= REPEATS.indexOf(THREAD_ON), "the first occurrence lies in t-three's passage, the second in t-two's");
  assert.equal(existingThreadFor([t2, t3], "a3", "the cache", REPEATS, inside)?.tid, "t-two", "selected inside the older thread");
  assert.equal(existingThreadFor([t3, t2], "a3", "the cache", REPEATS, inside)?.tid, "t-two", "whatever the list's order");
  assert.equal(existingThreadFor([t2, { ...t3, createdT: 5 }], "a3", "the cache", REPEATS, inside)?.tid, "t-two",
               "and with the thread on the first occurrence the older one");
  assert.equal(existingThreadFor([t2, t3], "a3", "the cache", REPEATS, first)?.tid, "t-three", "selected at the first occurrence: the thread there");
  assert.equal(existingThreadFor([t2, t3], "a3", "the cache", REPEATS)?.tid, "t-three", "no live range: the first occurrence alone");
  const t4 = th({ tid: "t-four", anchorUuid: "a3", exact: "refills the cache", createdT: 30 });   // nested in t-two's passage
  assert.equal(existingThreadFor([t2, t3, t4], "a3", "the cache", REPEATS, inside)?.tid, "t-four", "the newest of the threads selected inside");
  const a = newCommentCreate({ sid: SID, uuid: "a3", exact: "warms the cache" }, "is the warm-up timed?", "");
  const b = newCommentCreate({ sid: SID, uuid: "a3", exact: THREAD_ON }, "does the refill block exports?", "");
  assert.equal(holdForPassage([a, b], SID, "a3", "the cache", REPEATS, inside), b, "the send selected inside, though sent second");
  assert.equal(holdForPassage([b, a], SID, "a3", "the cache", REPEATS, inside), b);
  assert.equal(holdForPassage([a, b], SID, "a3", "the cache", REPEATS), a, "no live range: the first occurrence's send");
  const c = newCommentCreate({ sid: SID, uuid: "a3", exact: "refills the cache" }, "how often?", "");
  assert.equal(holdForPassage([b, c], SID, "a3", "the cache", REPEATS, inside), b, "the first sent of the sends selected inside");
});

// A passage that runs past the end of its turn: its part in the turn is one word, too short for findAnchorRange to locate,
// so only containment can match it. Its words after the turn hold "retry cap".
const CROSS = "anyway.\n\nWhich sync path is the retry cap on?";
const RETRY_CAP = { start: PROSE.indexOf("retry cap"), end: PROSE.indexOf("retry cap") + "retry cap".length };

test("a passage the turn's prose cannot locate is matched by containment when the selection has a live range", () => {
  assert.equal(findAnchorRange(PROSE, CROSS), null, "the precondition: the prose cannot locate the passage");
  assert.equal(samePassage(PROSE, CROSS, "retry cap", RETRY_CAP), true);
  assert.equal(existingThreadFor([th({ tid: "t-cross", anchorUuid: "a2", exact: CROSS })], "a2", "retry cap", PROSE, RETRY_CAP)?.tid,
               "t-cross");
});

test("among threads, and among sends, a match by containment comes after one whose passage the live range meets", () => {
  const located = th({ tid: "t-located", anchorUuid: "a2", exact: "Keep the retry cap at two minutes", createdT: 10 });
  const cross = th({ tid: "t-cross", anchorUuid: "a2", exact: CROSS, createdT: 20 });
  assert.equal(existingThreadFor([located, cross], "a2", "retry cap", PROSE, RETRY_CAP)?.tid, "t-located", "though the other is newer");
  assert.equal(existingThreadFor([cross, located], "a2", "retry cap", PROSE, RETRY_CAP)?.tid, "t-located", "in either list order");
  const a = newCommentCreate({ sid: SID, uuid: "a2", exact: CROSS }, "which path?", "");
  const b = newCommentCreate({ sid: SID, uuid: "a2", exact: "Keep the retry cap at two minutes" }, "why two?", "");
  assert.equal(holdForPassage([a, b], SID, "a2", "retry cap", PROSE, RETRY_CAP), b, "though the other was sent first");
});

test("a live range may end at the prose's last character", () => {
  const prose = "cache the list. Then we cache the list";
  const at = { start: prose.lastIndexOf("cache the list"), end: prose.length };
  assert.equal(samePassage(prose, "we cache the list", "cache the list"), false, "the precondition: its first occurrence is outside");
  assert.equal(samePassage(prose, "we cache the list", "cache the list", at), true);
});

test("a Comment on a passage with a thread opens it, and its box offers New thread instead (source pins)", () => {
  const open = UI.slice(UI.indexOf("function openCommentComposer("));
  const body = open.slice(0, open.indexOf("\nfunction ", 10));
  assert.match(body, /const existing = !live && !fresh \? existingThreadFor\(commentThreads\.get\(sid\) \|\| \[\], uuid, exact, prose, at\) : null;/,
               "the thread lookup reads the live range (executed by the test 'New thread instead carries the selection's live "
               + "range' and by test_comment_layout_existing_served.py's repeated-phrase, tie, next-turn and rail-stamp tests)");
  assert.match(body, /openCommentPopover\(sid, existing\.tid, undefined, undefined, \{ sid, tid: existing\.tid, uuid, exact, x, y, at, view \}\);/,
               "the redirect keeps the live range for New thread instead (the test 'New thread instead carries the selection's live range' runs it)");
  const pop = UI.slice(UI.indexOf("function renderCommentPopover("));
  const popBody = pop.slice(0, pop.indexOf("\nfunction ", 10));
  assert.match(popBody, /if \(th && cmtRedirect && cmtRedirect\.tid === th\.tid\) \{[\s\S]{0,700}nb\.textContent = "New thread instead";[\s\S]{0,200}nb\.dataset\.act = "cmtnewthread";/);
  assert.match(UI, /cmtnewthread: \(\) => \{ const r = cmtRedirect; if \(r\) openCommentComposer\(r\.sid, r\.uuid, r\.exact, r\.x, r\.y, r\.at, true, r\.view\); \},/,
               "delegated on the body (click-safe), onto the selection the gesture had and its live range (the test 'New thread instead carries the selection's live range' runs r.at)");
  assert.match(UI, /cmtRedirect = redirect && redirect\.tid === tid \? redirect : null;/, "a mark click or a rail jump offers no such action");
});

// openCommentComposer, openCommentPopover and the New thread instead action, run from render.ts's own source with the page
// around them stubbed (comment-key.test.ts runs commentAim the same way)
const liftUi = (name: string): string => {
  const i = UI.indexOf(`function ${name}(`);
  assert.ok(i > 0, name);
  let depth = 0, j = UI.indexOf("{\n", i);
  for (; j < UI.length; j++) { if (UI[j] === "{") depth++; else if (UI[j] === "}") { depth--; if (!depth) break; } }
  return UI.slice(i, j + 1);
};

test("New thread instead carries the selection's live range, so it opens the comment box on the selection and not another send's box", () => {
  // two sends in flight on one reply: `a` on the phrase's first copy, its mark still the optimistic one; `b` on the passage
  // the phrase is selected inside, its thread already listed while its ack is still owed
  const inside = nth(REPEATS, "the cache", 1);
  const a = newCommentCreate({ sid: SID, uuid: "a3", exact: "warms the cache" }, "is the warm-up timed?", "");
  const b = newCommentCreate({ sid: SID, uuid: "a3", exact: THREAD_ON }, "does the refill block exports?", "");
  const threads = [th({ tid: "pending:" + a.createId, anchorUuid: "a3", exact: a.exact, createdT: 20 }),
                   th({ tid: "t-b", anchorUuid: "a3", exact: THREAD_ON, createdT: 10 })];
  assert.equal(holdForPassage([a, b], SID, "a3", "the cache", REPEATS), a, "the premise: with no live range, the send on the first copy");
  const act = UI.match(/cmtnewthread: (\(\) => \{ const r = cmtRedirect;[^\n]*\}),\n/);
  assert.ok(act, "the New thread instead action");
  const ts = [liftUi("openCommentComposer"), liftUi("openCommentPopover"), "const cmtnewthread = " + act![1] + ";"].join("\n");
  const src = requireCjs("esbuild").transformSync(ts, { loader: "ts", target: "es2020" }).code;
  const shown: Array<{ open: unknown; anchor: any }> = [];
  const env = {
    turnProse: () => REPEATS, holdForPassage, existingThreadFor, cmtCreateInFlight: new Map([[a.createId, a], [b.createId, b]]),
    commentThreads: new Map([[SID, threads]]), pickThreadColor: () => "#888", applyCommentMarks: () => {}, vscodeApi: undefined,
    document: { querySelector: () => null }, shown, quoteContext,
  };
  const page = new Function("env", "const { turnProse, holdForPassage, existingThreadFor, cmtCreateInFlight, commentThreads, pickThreadColor, "
    + "applyCommentMarks, vscodeApi, document, quoteContext } = env;\n"
    + "let commentPopPos = null, cmtPopPosMine = false, cmtRedirect = null, pendingCommentAnchor = null, openCommentKey = null;\n"
    + "const renderCommentPopover = () => { env.shown.push({ open: openCommentKey, anchor: pendingCommentAnchor }); };\n"
    + src + "\nreturn { openCommentComposer, cmtnewthread };")(env) as { openCommentComposer: (...args: unknown[]) => void; cmtnewthread: () => void };
  const view = { ctx: "\u2026Each export then refills the cache from the database, and the worker drains the queue\u2026" };
  page.openCommentComposer(SID, "a3", "the cache", 100, 200, inside, false, view);
  assert.deepEqual([shown[0]?.open, shown[0]?.anchor], [{ sid: SID, tid: "t-b" }, null], "the Comment opens b's thread");
  page.cmtnewthread();
  const box = shown[1]?.anchor;
  assert.deepEqual([shown.length, box?.exact, box?.sent], [2, "the cache", undefined],
                   "New thread instead opens the comment box on the selection, not a's sent box: " + JSON.stringify(box));
  // a few words of a sentence (2026-10-14): the box holds the sentence the gesture read off the selection, through New thread
  // instead too, and the create carries it to the thread's first message
  assert.deepEqual([box?.context, box?.shown], [view.ctx, null]);
});

test("the create carries the sentence a short passage sits in, and a frame without one is unchanged (2026-10-14)", () => {
  const ctx = "\u2026Each export then refills the cache from the database\u2026";
  const held = newCommentCreate({ sid: SID, uuid: "a3", exact: "the cache", context: ctx }, "why refill?", "");
  assert.equal(commentCreateFrame(held).context, ctx);
  assert.deepEqual(commentCreateFrame(held), commentCreateFrame(held), "a re-post carries it too");
  const plain = commentCreateFrame(newCommentCreate({ sid: SID, uuid: "a3", exact: "the cache", context: null }, "why?", ""));
  assert.ok(!("context" in plain), "no sentence, no field: the plain quote, as an older kernel reads it");
});

test("renderCommentPopover's focus line has no redirect clause (source pin)", () => {
  // the caret and the rebuild that must not take it back are executed by test_comment_layout_existing_served.py
  const pop = UI.slice(UI.indexOf("function renderCommentPopover("));
  const popBody = pop.slice(0, pop.indexOf("\nfunction ", 10));
  const focusLines = popBody.split("\n").filter((l) => /\.focus\(\)/.test(l));
  assert.ok(focusLines.length > 0, "the box's own focus line is where this pin looks");
  for (const l of focusLines)
    assert.doesNotMatch(l, /cmtRedirect/, "no redirect clause in the box's focus line");
});

test("the thread layout honours only the user's own spot: a selection point places a new comment box alone (source pins)", () => {
  assert.match(UI, /commentPopPos = \{ x, y \};\s*\n\s*cmtPopPosMine = false;/, "a Comment gesture's selection point is not the user's placement");
  assert.equal((UI.match(/cmtPopPosMine = true;/g) || []).length, 5, "a drag, an edge pull, a grip resize, the maximize toggle and a reload's restore");
});
