import { test } from "node:test";
import * as assert from "node:assert/strict";
import { compactDisplay, isFoldableNoticeShape, itemAnchor, itemHolds, runRows, segmentReplies, thoughtHasWords, thoughtKey, thoughtOf, STANDALONE_TOOLS, actionHead, actionParts, actionPhrases, toolRowLabel, diffTotals, toolInputText, type DisplayItem, type ThoughtLike } from "./compact";

test("compactDisplay: without the thoughts array, thinking is dropped entirely (a caller that passes none)", () => {
  const d = compactDisplay(["user", "thinking", "assistant"]);
  assert.deepEqual(d, [{ kind: "event", index: 0 }, { kind: "event", index: 2 }]);
});

test("compactDisplay: a run of consecutive tools collapses to one toolgroup", () => {
  const d = compactDisplay(["user", "tool", "tool", "tool", "assistant"]);
  assert.deepEqual(d, [
    { kind: "event", index: 0 },
    { kind: "toolgroup", indices: [1, 2, 3] },
    { kind: "event", index: 4 },
  ]);
});

test("compactDisplay: textless thinking between tools does NOT break the run", () => {
  const d = compactDisplay(["tool", "thinking", "tool"]);
  assert.deepEqual(d, [{ kind: "toolgroup", indices: [0, 2] }]);
});

test("compactDisplay: visible content between tools DOES split the run; each lone tool stays inline", () => {
  const d = compactDisplay(["tool", "assistant", "tool"]);
  assert.deepEqual(d, [
    { kind: "event", index: 0 },   // a lone tool renders inline, not a 1-element toolgroup (the user 2026-06-22)
    { kind: "event", index: 1 },
    { kind: "event", index: 2 },
  ]);
});

test("compactDisplay: a LONE tool renders inline; only a run of ≥2 collapses (the user 2026-06-22)", () => {
  assert.deepEqual(compactDisplay(["assistant", "tool", "assistant"]),
    [{ kind: "event", index: 0 }, { kind: "event", index: 1 }, { kind: "event", index: 2 }]);
  assert.deepEqual(compactDisplay(["tool", "tool"]), [{ kind: "toolgroup", indices: [0, 1] }]);
  assert.deepEqual(compactDisplay(["tool"]), [{ kind: "event", index: 0 }]);
});

test("compactDisplay: a trailing tool run is flushed", () => {
  const d = compactDisplay(["assistant", "tool", "tool"]);
  assert.deepEqual(d, [{ kind: "event", index: 0 }, { kind: "toolgroup", indices: [1, 2] }]);
});

test("compactDisplay: AskUserQuestion is standalone — it breaks the run and passes through first-class", () => {
  // [Read, Edit, AskUserQuestion, Bash, Bash] → group(Read,Edit), the ask first-class, group(Bash,Bash).
  // The ask must NOT be swept into a toolgroup (the user 2026-06-17): it's the "you answered" box.
  const kinds = ["tool", "tool", "tool", "tool", "tool"];
  const names = ["Read", "Edit", "AskUserQuestion", "Bash", "Bash"];
  assert.deepEqual(compactDisplay(kinds, names), [
    { kind: "toolgroup", indices: [0, 1] },
    { kind: "event", index: 2 },
    { kind: "toolgroup", indices: [3, 4] },
  ]);
  assert.ok(STANDALONE_TOOLS.has("AskUserQuestion"));
});

test("compactDisplay: a lone AskUserQuestion run is a standalone event, never a 1-tool group", () => {
  assert.deepEqual(compactDisplay(["tool"], ["AskUserQuestion"]), [{ kind: "event", index: 0 }]);
});

test("compactDisplay: without names, every tool still collapses (back-compat)", () => {
  assert.deepEqual(compactDisplay(["tool", "tool", "tool"]), [{ kind: "toolgroup", indices: [0, 1, 2] }]);
});


test("every non-tool event owns exactly one display unit — the scroll-mark translation's ground truth", () => {
  // eventUnitIndex (render.ts) inverts this stream to map mark anchors (events) onto the frame's
  // unit space; that inversion is sound only if a user/assistant event is never folded into a
  // group and never dropped (the user 2026-08-18, whose reply notches vanished in compact mode)
  const kinds = ["user", "tool", "tool", "assistant", "user", "tool", "user"];
  const items = compactDisplay(kinds, kinds.map((k) => (k === "tool" ? "Bash" : undefined)));
  const seen = new Map<number, number>();
  items.forEach((it, u) => {
    if (it.kind === "toolgroup" || it.kind === "noticegroup") for (const i of it.indices) seen.set(i, u);   // noticegroup since 2026-09-08
    else if (it.kind === "event") seen.set(it.index, u);
  });
  for (let i = 0; i < kinds.length; i++) {
    assert.ok(seen.has(i), "event " + i + " (" + kinds[i] + ") maps to a unit");
    if (kinds[i] !== "tool") {
      const u = seen.get(i)!;
      const it = items[u];
      assert.equal(it.kind, "event", "a non-tool event is its OWN unit, never inside a fold");
    }
  }
});

test("consecutive retry-recovery notes fold like a tool run — a lone one stays first-class (T131)", () => {
  // the user 2026-08-27: seventeen consecutive 'Recovered after N retries' rows are a flood; the
  // collapsed-run idiom fits consecutive same-shape machine notices exactly. 2026-09-08: the fold is the
  // generic NOTICEGROUP (a bare "retried" run still folds when the caller passes no foldability array).
  const kinds = ["user", "retried", "retried", "retried", "assistant", "retried", "user"];
  const items = compactDisplay(kinds);
  assert.deepEqual(items, [
    { kind: "event", index: 0 },
    { kind: "noticegroup", indices: [1, 2, 3] },
    { kind: "event", index: 4 },
    { kind: "event", index: 5 },   // a lone recovery renders first-class, like a lone tool
    { kind: "event", index: 6 },
  ]);
});

test("the foldable set is the caller's (isFoldableNotice): effort/model-swap/reload/interrupt pairs fold; peers and boundaries never do (2026-09-08)", () => {
  const kinds = ["user", "effortApplied", "modelFallback", "user", "assistant", "postal-service", "compact", "retried", "retried"];
  const notices = [false, true, true, true /* an interrupt marker */, true /* its settle */, false, false, true, true];
  assert.deepEqual(compactDisplay(kinds, undefined, notices), [
    { kind: "event", index: 0 },
    { kind: "noticegroup", indices: [1, 2, 3, 4] },
    { kind: "event", index: 5 },   // a peer's mail stands alone — a reply may be owed
    { kind: "event", index: 6 },   // a compaction boundary is a boundary
    { kind: "noticegroup", indices: [7, 8] },
  ]);
  // with the array present, a "retried" NOT marked foldable is a plain event (the array is authoritative)
  assert.deepEqual(compactDisplay(["retried", "retried"], undefined, [false, false]),
    [{ kind: "event", index: 0 }, { kind: "event", index: 1 }]);
});

test("the spend guard's stop and its message never fold into a notice run (the user 2026-10-08)", () => {
  // the guard stopping a session over its hourly ceiling is not a low-stakes notice: swept into "2 notices" beside a
  // background report, the stop read as a plain interrupt and the guard's message went unrecognised
  assert.equal(isFoldableNoticeShape({ kind: "user", interruptMarker: true, interruptCause: "spend" }), false);
  assert.equal(isFoldableNoticeShape({ kind: "user", rompSystem: true, md: "You are spending…", spendGuard: true }), false);
  // every other interrupt and romp notice folds as before
  assert.equal(isFoldableNoticeShape({ kind: "user", interruptMarker: true }), true);
  assert.equal(isFoldableNoticeShape({ kind: "user", interruptMarker: true, interruptCause: "restart" }), true);
  assert.equal(isFoldableNoticeShape({ kind: "user", rompSystem: true, md: "[romp] The romp kernel restarted" }), true);
  // the live shape: the guard's stop, a background agent's report, the reply, the guard's message
  const evs = [{ kind: "user", interruptMarker: true, interruptCause: "spend" }, { kind: "user", source: { kind: "subagent" } },
               { kind: "assistant" }, { kind: "user", rompSystem: true, md: "You are spending…", spendGuard: true }];
  assert.deepEqual(compactDisplay(evs.map((e) => e.kind), undefined, evs.map((e) => isFoldableNoticeShape(e as any))),
    [{ kind: "event", index: 0 }, { kind: "event", index: 1 }, { kind: "event", index: 2 }, { kind: "event", index: 3 }]);
});

test("retry runs and tool runs break each other — two folds, never one mixed group", () => {
  const kinds = ["retried", "retried", "tool", "tool", "retried", "retried"];
  const items = compactDisplay(kinds, kinds.map((k) => (k === "tool" ? "Bash" : undefined)));
  assert.deepEqual(items, [
    { kind: "noticegroup", indices: [0, 1] },   // noticegroup since 2026-09-08
    { kind: "toolgroup", indices: [2, 3] },
    { kind: "noticegroup", indices: [4, 5] },
  ]);
});

test("thinking hides without breaking a retry run, same as a tool run", () => {
  const kinds = ["retried", "thinking", "retried"];
  assert.deepEqual(compactDisplay(kinds), [{ kind: "noticegroup", indices: [0, 2] }]);   // noticegroup since 2026-09-08
});

// T339 (the user 2026-09-11): a collapsed run is placed and timed by its LATEST member, never its first. The user's
// transcript held a notice run whose first member carried yesterday's clock among today's rows.
test("itemAnchor: a lone event is its own anchor; a run anchors on its latest member, ties on the later one", () => {
  const epochs = [1000, 2000, 500, 2000, null, 3000];
  const at = (i: number) => epochs[i] ?? null;
  assert.equal(itemAnchor({ kind: "event", index: 2 }, at), 2);
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [2, 5] }, at), 5, "yesterday then today: today");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [5, 2] }, at), 5, "the latest, wherever it sits in the run");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [1, 3] }, at), 3, "a tie: the later member");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [0, 4, 1] }, at), 1, "a member with no epoch never anchors");
  assert.equal(itemAnchor({ kind: "toolgroup", indices: [2, 5] }, at), 2, "a tool run keeps its first member, as before");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [4, 4] } as DisplayItem, at), 4, "no timed member: the first");
});


// ── Thoughts in compact mode (the user 2026-10-23, who approved the design and the end of the 2026-06-30 rule) ──
// A thinking block WITH WORDS is a thought; consecutive thoughts are one row; a thought that opens a stretch of work is a unit
// of its own; a thought after a tool rides that tool's run; a textless block stays hidden. Synthetic notes-api events only.
type Ev = ThoughtLike & { name?: string };
const ev = (kind: string, extra: Partial<Ev> = {}): Ev => ({ kind, ...extra });
const words = (text: string, extra: Partial<Ev> = {}): Ev => ev("thinking", { text, ...extra });
const opaque = (extra: Partial<Ev> = {}): Ev => ev("thinking", { text: "", ...extra });
const units = (evs: Ev[]): DisplayItem[] => compactDisplay(evs.map((e) => e.kind), evs.map((e) => e.name), undefined, evs.map(thoughtHasWords));

test("thoughts, grouping: consecutive thoughts are ONE row, a textless block between them splits nothing", () => {
  assert.deepEqual(units([ev("user"), words("Reading the list handler."), words("Starting with the handler."), ev("assistant")]),
    [{ kind: "event", index: 0 }, { kind: "thought", indices: [1, 2] }, { kind: "event", index: 3 }]);
  assert.deepEqual(units([ev("user"), words("First."), opaque(), words("Second."), ev("assistant")]),
    [{ kind: "event", index: 0 }, { kind: "thought", indices: [1, 3] }, { kind: "event", index: 4 }], "the textless block is no member and no break");
  // inside an expanded run, a stretch of thoughts draws as one row, each tool as its own (runRows)
  const kinds = ["tool", "thinking", "thinking", "tool", "thinking"];
  assert.deepEqual(runRows([0, 1, 2, 3, 4], (i) => kinds[i] === "thinking"), [[0], [1, 2], [3], [4]]);
  assert.deepEqual(runRows([0, 2], (i) => kinds[i] === "thinking"), [[0], [2]], "two tools, no thoughts: a row each");
});

test("thoughts, placement: a thought that opens a stretch is its own row, after a prompt, a mid-turn message or a reply", () => {
  const evs = [
    ev("user"), words("Planning the pagination."),                     // after the prompt
    ev("tool", { name: "Read" }), ev("tool", { name: "Edit" }),
    ev("user"), words("Got it: switching the default to 50."),         // after a mid-turn message
    ev("tool", { name: "Edit" }), ev("tool", { name: "Bash" }),
    ev("assistant"), words("The tests pass; one more check."),         // after a reply
    ev("assistant"),
  ];
  assert.deepEqual(units(evs), [
    { kind: "event", index: 0 }, { kind: "thought", indices: [1] }, { kind: "toolgroup", indices: [2, 3] },
    { kind: "event", index: 4 }, { kind: "thought", indices: [5] }, { kind: "toolgroup", indices: [6, 7] },
    { kind: "event", index: 8 }, { kind: "thought", indices: [9] }, { kind: "event", index: 10 },
  ]);
});

test("thoughts, placement: a thought after a tool is a MEMBER of that run, never a split; a lone tool's thought follows it", () => {
  // two tools with a thought between them and one after: ONE group holding both thoughts (the head counts its tools only)
  assert.deepEqual(units([ev("user"), ev("tool", { name: "Read" }), words("Both callers pass no limit."), ev("tool", { name: "Edit" }), words("Now the tests."), ev("assistant")]),
    [{ kind: "event", index: 0 }, { kind: "toolgroup", indices: [1, 2, 3, 4] }, { kind: "event", index: 5 }]);
  // a LONE tool stays first-class, so there is no group to hold its thought: the thought is the row after it
  assert.deepEqual(units([ev("user"), ev("tool", { name: "Bash" }), words("Three passing."), ev("assistant")]),
    [{ kind: "event", index: 0 }, { kind: "event", index: 1 }, { kind: "thought", indices: [2] }, { kind: "event", index: 3 }]);
  // a standalone tool (the answered question box) ends the run: a thought after it opens a stretch of its own
  assert.deepEqual(units([ev("tool", { name: "Read" }), ev("tool", { name: "Read" }), ev("tool", { name: "AskUserQuestion" }), words("They chose 50.")]),
    [{ kind: "toolgroup", indices: [0, 1] }, { kind: "event", index: 2 }, { kind: "thought", indices: [3] }]);
  // a thought before the run's first tool is the stretch's opener, not a member
  assert.deepEqual(units([words("Reading first."), ev("tool", { name: "Read" }), ev("tool", { name: "Read" })]),
    [{ kind: "thought", indices: [0] }, { kind: "toolgroup", indices: [1, 2] }]);
  // every event a unit holds maps back to it (a deep link, a scroll mark): a run's thought member, a thought's every block
  const it: DisplayItem = { kind: "toolgroup", indices: [1, 2, 3] };
  assert.ok(itemHolds(it, 2) && !itemHolds(it, 4));
  assert.ok(itemHolds({ kind: "thought", indices: [5, 7] }, 7) && !itemHolds({ kind: "thought", indices: [5, 7] }, 6));
  assert.ok(itemHolds({ kind: "event", index: 3 }, 3) && !itemHolds({ kind: "gap", lo: 0, hi: 4, before: 3 }, 3));
  assert.equal(itemAnchor({ kind: "thought", indices: [5, 7] }, () => 1), 5, "a Thought row is placed and timed by its first block");
});

test("thoughts: a textless (opaque) block stays hidden, makes no unit and breaks no run", () => {
  assert.deepEqual(units([ev("user"), opaque(), ev("assistant")]), [{ kind: "event", index: 0 }, { kind: "event", index: 2 }]);
  assert.deepEqual(units([ev("tool", { name: "Edit" }), opaque(), ev("tool", { name: "Bash" })]), [{ kind: "toolgroup", indices: [0, 2] }]);
  assert.equal(thoughtHasWords(opaque()), false);
  assert.equal(thoughtHasWords(words("   \n ")), false, "whitespace is no words");
  assert.equal(thoughtHasWords(ev("assistant", { text: "a reply" })), false, "only a thinking event is a thought");
  assert.deepEqual(units([ev("user"), opaque(), opaque()]), [{ kind: "event", index: 0 }]);
});

test("thoughts: the gist is the first line of the LAST block with words; the text is every block's; the tokens span the messages", () => {
  const evs: Ev[] = [
    ev("user"),
    opaque({ uuid: "11111111-2222-4333-8444-000000000001", thinkTokens: 700 }),   // the message's first thinking row carries its figure
    words("The default has to move from 20 to 50,\nand one test pins 20.", { uuid: "11111111-2222-4333-8444-000000000002" }),
    words("\n  Got it:   switching the default page size to 50.\nThen the test.", { uuid: "11111111-2222-4333-8444-000000000003", thinkTokens: 260 }),
    ev("tool", { name: "Edit" }),
  ];
  const th = thoughtOf(evs, [2, 3]);
  assert.equal(th.gist, "Got it: switching the default page size to 50.", "the latest note's first line, its whitespace collapsed");
  assert.equal(th.text, "The default has to move from 20 to 50,\nand one test pins 20.\n\nGot it:   switching the default page size to 50.\nThen the test.",
    "every block's words, in order, a blank line apart");
  assert.equal(th.tokens, 960, "the textless row just before the words carries its message's figure, and counts");
  assert.equal(thoughtOf(evs, [3]).gist, "Got it: switching the default page size to 50.");
  assert.equal(thoughtOf([ev("tool"), words("Only this.", { thinkTokens: 0 })], [1]).tokens, 0, "no figure: no tokens");
});

test("thoughts: the fold persists under think:<first block's uuid>, the key the full view's clamp keeps", () => {
  const evs: Ev[] = [words("One.", { uuid: "11111111-2222-4333-8444-00000000000a" }), words("Two.", { uuid: "11111111-2222-4333-8444-00000000000b" })];
  assert.equal(thoughtOf(evs, [0, 1]).key, "think:11111111-2222-4333-8444-00000000000a", "the first block's uuid, whichever block the gist comes from");
  assert.equal(thoughtKey(evs[1]), "think:11111111-2222-4333-8444-00000000000b", "a lone block (the full view's clamp) keys the same way");
  assert.equal(thoughtKey({}), undefined, "no uuid: no key (a transient fold)");
  assert.equal(thoughtKey(undefined), undefined);
});

// ── T418: the tool rows in the user's terms ──
const T = (name: string, extra: Record<string, unknown> = {}) => ({ name, desc: "", input: "{}", ...extra });
const rows = (add: number, del: number) => [...Array(add).fill({ sign: "+" }), ...Array(del).fill({ sign: "-" })];

test("T418 head: the screenshot's turn reads by action, ordered by the printed number, the edits' totals once at the end", () => {
  const tools = [
    ...Array(11).fill(T("Bash", { input: JSON.stringify({ command: "true" }) })),
    T("Write", { file: "/w/a.txt" }), T("Write", { file: "/w/b.txt" }),
    T("Edit", { file: "/w/f.ts", diffRows: rows(12, 0) }), T("Edit", { file: "/w/g.ts", diffRows: rows(20, 0) }), T("MultiEdit", { file: "/w/h.ts", diffRows: rows(5, 0) }),
    T("Read", { file: "/w/1" }), T("Read", { file: "/w/2" }), T("Read", { file: "/w/3" }), T("Read", { file: "/w/4" }),
  ];
  assert.equal(actionHead(tools), "Ran 11 commands, read 4 files, edited 3 files, created 2 files +37 -0");
  assert.deepEqual(actionParts(tools), { text: "Ran 11 commands, read 4 files, edited 3 files, created 2 files", add: 37, del: 0 });
});
test("T418 head: singulars, the sole search by its own words, tools with no action, the task list", () => {
  assert.equal(actionHead([T("Bash")]), "Ran a command");
  assert.equal(actionHead([T("Read", { file: "/a/b/c.py" })]), "Read a file");
  assert.equal(actionHead([T("Edit", { file: "/a.ts", diffRows: rows(12, 3) })]), "Edited a file +12 -3");
  assert.equal(actionHead([T("Grep", { input: JSON.stringify({ pattern: "foo", path: "/repo/src" }), file: "/repo/src" })]), "Searched for foo");
  assert.equal(actionHead([T("Grep", { input: JSON.stringify({ pattern: "foo" }) }), T("Glob", { input: JSON.stringify({ pattern: "*.ts" }) })]), "Searched 2 times");
  assert.equal(actionHead([T("WebFetch", { input: JSON.stringify({ url: "https://example.org/x" }) })]), "Fetched a page");
  assert.equal(actionHead([T("Agent"), T("Task")]), "Ran 2 agents");
  assert.equal(actionHead([T("TodoWrite"), T("TaskUpdate")]), "Updated the task list");
  assert.equal(actionHead([T("Skill"), T("Monitor")]), "Used 2 tools");
  assert.equal(actionHead([T("Skill")]), "Used a tool");
});
test("T418 head: distinct files for reads, edits and creations, uses for the rest; the order follows the printed number; the head never reads desc", () => {
  const tools = [T("Edit", { file: "/a.ts", diffRows: rows(3, 1), desc: "this is ignored" }), T("Edit", { file: "/a.ts", diffRows: rows(4, 0) })];
  assert.equal(actionHead(tools), "Edited a file +7 -1");
  assert.deepEqual(actionPhrases(tools).map((p) => [p.action, p.count, p.printed, p.add, p.del]), [["edit", 2, 1, 7, 1]]);
  assert.equal(actionHead([...Array(4).fill(T("Read", { file: "/one" })), ...Array(3).fill(T("Bash"))]), "Ran 3 commands, read a file");
  assert.deepEqual(diffTotals({ name: "Write", diff: "+ a\n+ b\n- c" }), { add: 2, del: 1 });
});
test("T418 head: the totals of every edit in a group sum into ONE printing; a creation carries none (the kernel emits no Write diff)", () => {
  const tools = [T("Edit", { file: "/a.ts", diffRows: rows(3, 1) }), T("NotebookEdit", { file: "/b.ipynb", diffRows: rows(2, 0) }), T("Write", { file: "/c.md" })];
  assert.deepEqual(actionParts(tools), { text: "Edited 2 files, created a file", add: 5, del: 1 });
  assert.equal(actionHead(tools), "Edited 2 files, created a file +5 -1");
  assert.equal(actionHead([T("Write", { file: "/c.md" })]), "Created a file");
});
test("T418 row: the model's description wins; else the derived phrase per tool, the file as the link after it; a bare Bash shows its command in the code face", () => {
  assert.deepEqual(toolRowLabel(T("Bash", { desc: "Verified the venv exists", input: JSON.stringify({ command: "ls" }) })), { text: "Verified the venv exists" });
  assert.deepEqual(toolRowLabel(T("Bash", { input: JSON.stringify({ command: "cd ~/x && make\necho done" }) })), { text: "cd ~/x && make", code: true });
  assert.deepEqual(toolRowLabel(T("Read", { file: "/home/u/repo/kernel/kernel.py" })), { text: "Read ", link: true });
  assert.deepEqual(toolRowLabel(T("Edit", { file: "/r/ui/feed.ts", diffRows: rows(12, 3) })), { text: "Edited ", link: true });   // the totals are the diff fold's toggle, not the label's
  assert.deepEqual(toolRowLabel(T("Write", { file: "/r/new.md" })), { text: "Created ", link: true });
  assert.deepEqual(toolRowLabel(T("Grep", { input: JSON.stringify({ pattern: "foo", path: "/r/src/lib" }), file: "/r/src/lib" })), { text: "Searched for foo in ", link: true });
  assert.deepEqual(toolRowLabel(T("Grep", { input: JSON.stringify({ pattern: "foo" }) })), { text: "Searched for foo" });
  assert.deepEqual(toolRowLabel(T("Glob", { input: JSON.stringify({ pattern: "**/*.ts", path: "/r" }), file: "/r" })), { text: "Searched for **/*.ts in ", link: true });
  assert.deepEqual(toolRowLabel(T("WebFetch", { input: JSON.stringify({ url: "https://docs.example.org/a/b" }) })), { text: "Fetched docs.example.org" });
  assert.deepEqual(toolRowLabel(T("WebSearch", { input: JSON.stringify({ query: "romp kernel" }) })), { text: "Searched the web for romp kernel" });
  assert.deepEqual(toolRowLabel(T("Agent")), { text: "Ran an agent" });
  assert.deepEqual(toolRowLabel(T("TaskUpdate")), { text: "Updated the task list" });
  assert.deepEqual(toolRowLabel(T("Skill")), { text: "Used a tool", secondary: "Skill" });
  assert.deepEqual(toolRowLabel(T("LS", { file: "/r/dir" })), { text: "Used a tool", secondary: "LS" });
  const long = "x".repeat(100);
  assert.equal(toolRowLabel(T("Grep", { input: JSON.stringify({ pattern: long }) })).text.length, "Searched for ".length + 80);
});
test("T418 expanded row: a Bash shows its command as typed; other tools their input JSON", () => {
  assert.equal(toolInputText(T("Bash", { input: JSON.stringify({ command: "cd ~/x && make", description: "Built it" }) })), "cd ~/x && make");
  assert.equal(toolInputText(T("Read", { input: JSON.stringify({ file_path: "/a" }) })), JSON.stringify({ file_path: "/a" }));
  assert.equal(toolInputText(T("Bash", { input: "{not json" })), "{not json");
});

// ── tools under prose (progress notes, 2026-11-06): every prose row stays, the tools after it attach ─────────────────────────
// Events as (kind, role): "N" a progress note (an assistant event flagged narration), "R" a reply, "S" a message delivered
// mid-turn (a user event flagged absorbed), "U" a prompt, "K" a background task's notice on a user row (a foldable notice), "T" a
// tool, "A" an Agent card (a kept tool), "Q" an AskUserQuestion, "th" a thought with words.
function optE(seq: string[]) {
  const kinds = seq.map((x) => (x === "N" || x === "R" ? "assistant" : x === "S" || x === "U" || x === "K" ? "user" : x === "T" || x === "A" || x === "Q" ? "tool" : "thinking"));
  const names = seq.map((x) => (x === "A" ? "Agent" : x === "Q" ? "AskUserQuestion" : x === "T" ? "Bash" : undefined));
  const roles = { note: seq.map((x) => x === "N" || x === "R"), keep: seq.map((x) => x === "A") };
  return compactDisplay(kinds, names, seq.map((x) => x === "K"), seq.map((x) => x === "th"), roles);
}
const att = (...indices: number[]) => ({ kind: "toolgroup", indices, attached: true });
const one = (index: number) => ({ kind: "event", index });

const ae = (index: number) => ({ kind: "event", index, attached: true });

test("tools under prose: every prose row stays; the calls after each fold into its line, and ONE call is its own row tucked under it", () => {
  // four note/tool pairs: four notes, a call (or a line of calls) under each, the reply; nothing the session said is folded
  assert.deepEqual(optE(["U", "N", "T", "N", "T", "T", "N", "T", "N", "T", "R"]),
    [one(0), one(1), ae(2), one(3), att(4, 5), one(6), ae(7), one(8), ae(9), one(10)]);
  // tools before a turn's first prose fold as they always did: a run of two, a lone one inline, neither tucked under anything
  assert.deepEqual(optE(["U", "T", "T", "N", "T"]), [one(0), { kind: "toolgroup", indices: [1, 2] }, one(3), ae(4)]);
  assert.deepEqual(optE(["U", "T", "N"]), [one(0), one(1), one(2)]);
});

test("tools under prose: a question mid-turn, the answer to a message and a summary before a cleanup tool are all rows of their own", () => {
  assert.deepEqual(optE(["U", "N", "T", "R", "T", "N", "T", "R"]), [one(0), one(1), ae(2), one(3), ae(4), one(5), ae(6), one(7)]);
  // a message delivered mid-turn: no rule needed, the answer is a row like every prose row
  assert.deepEqual(optE(["T", "S", "R", "T", "N", "T"]), [one(0), one(1), one(2), ae(3), one(4), ae(5)]);
  // the turn's real answer, then a cleanup tool: the answer stays the reply, the cleanup its row under it
  assert.deepEqual(optE(["U", "N", "T", "R", "T", "T"]), [one(0), one(1), ae(2), one(3), att(4, 5)]);
});

test("tools under prose: an Agent card stays its own row and the tools after it attach to it; Thought rows stay rows, as before", () => {
  assert.deepEqual(optE(["U", "N", "A", "T", "T", "N"]), [one(0), one(1), one(2), att(3, 4), one(5)]);
  // a thought right after a prose row keeps its Thought row; the calls after it still attach
  assert.deepEqual(optE(["U", "N", "th", "T", "N", "th", "R"]), [one(0), one(1), { kind: "thought", indices: [2] }, ae(3), one(4), { kind: "thought", indices: [5] }, one(6)]);
  // a thought after a call inside a line rides the line, as in any run; after a lone call, a Thought row of its own
  assert.deepEqual(optE(["U", "N", "T", "th", "T", "R"]), [one(0), one(1), att(2, 3, 4), one(5)]);
  assert.deepEqual(optE(["U", "N", "T", "th", "R"]), [one(0), one(1), ae(2), { kind: "thought", indices: [3] }, one(4)]);
  // an AskUserQuestion is first-class as ever, and a notice ends the stretch
  assert.deepEqual(optE(["N", "Q", "T", "N", "K", "T", "T"]), [one(0), one(1), one(2), one(3), one(4), { kind: "toolgroup", indices: [5, 6] }]);
});

test("a turn's reply is its last prose, wherever it sits, tools after it or not; a message delivered mid-turn ends no turn", () => {
  const kinds = ["user", "assistant", "tool", "assistant", "tool", "user", "assistant", "tool", "user", "tool"];
  assert.deepEqual([...segmentReplies(kinds, (i) => kinds[i] === "assistant")].sort((a, b) => a - b), [3, 6]);
  // index 5 a message delivered mid-turn: the turn runs on, and its reply is the last prose after it
  assert.deepEqual([...segmentReplies(kinds, (i) => kinds[i] === "assistant", (i) => kinds[i] === "user" && i !== 5)].sort((a, b) => a - b), [6]);
});
