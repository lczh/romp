// Pure transcript-compaction logic (no DOM), so it can be unit-tested. Compact mode (the user
// 2026-06-14): collapse each maximal run of TWO OR MORE consecutive tool uses into ONE summary line. A LONE
// tool stays inline — it renders first-class (its own tool line + expandable fold), since there's nothing to
// collapse and "Bash(…)" reads cleaner than "1 Bash" (the user 2026-06-22). Tools separated only by thinking
// still count as consecutive; tools separated by visible content (an assistant reply, a prompt, …) do not.
// The rail timestamp logic runs over the RESULT of this, so the stamps reflect the compacted stream.
// THINKING (the user 2026-10-23, who approved the design and with it the end of the 2026-06-30 rule that an
// expanded tool group never shows thinking): a thinking block WITH WORDS that is not a progress note (a reasoning
// summary; history from before romp recorded which blocks are notes) is a THOUGHT, shown as one collapsed row;
// consecutive thoughts are one row. A PROGRESS NOTE (2026-11-06) is not a thought: the kernel ships it as an assistant
// event flagged `narration`, prose like a reply, and only options C and C2 below give it a place in a run. A thought
// that opens a stretch of work (after a prompt, a mid-turn message, a reply) is its own unit; a
// thought after a tool in a run is a MEMBER of that run, drawn as a collapsed Thought row when the run expands,
// so it never splits the run, and the run's head still counts only its tools. A textless (opaque) block stays
// hidden, as every thinking block was before: it breaks nothing, and the turn's footer gives its tokens.
// The same shape folds LOW-STAKES NOTICES (2026-09-08, generalising the T131 retry-run fold): a run of ≥2
// consecutive foldable notices — a recovery, an effort change, a model swap, a reload, an interrupt + its
// settle, a background report, a system reminder, a romp notice — collapses into one `noticegroup` head.
// Which events are foldable is the caller's call (render.ts isFoldableNotice, passed per event): peers,
// API errors, compaction/clear boundaries, asks, to-dos, dividers and every bubble stay standalone.
// PROSE IN A RUN (options C and C2, 2026-11-06, compact mode only, on the caller's `notes`): Opus 5.5 writes a note before
// most tool calls, so tool, note, tool, note… would otherwise break every run into single tools. The caller says which
// assistant rows are NOTES: under C the progress notes (thinking blocks the CLI listed), under C2 every assistant prose row.
// A note BETWEEN tools is a member of the run, and so is a note right before the run's first tool; a note after the run's last
// tool is not (it waits for a tool that never comes, and stands as itself: the turn's final message stays a reply). The run's
// head shows its LATEST note as prose under the tools' line; expanding the run shows every tool and note in order. One
// exception, keyed on events: the first assistant prose after a message delivered mid-turn (`spliced`, the kernel's
// `absorbed` user row) is the answer to the person and stands on its own. Any user row re-arms or disarms that rule (a new
// prompt, a background task's notice that wakes a turn), and any assistant prose disarms it (a reply that is a text block).

/** The events compact mode may sweep into a noticegroup: the low-stakes, self-similar rows (a recovery, an effort
 *  change, a model swap, a reload, an interrupt marker and its settle, an injected notice: a system reminder or a
 *  romp notice on a user row). Peers, API errors, boundaries, asks, to-dos and every real bubble stay standalone.
 *  Pure over the event's shape so render.ts (the fold) and reveal-progress.ts (what counts as a message) share ONE
 *  reading (T336 review: the progress count took injected notices for messages). */
export function isFoldableNoticeShape(ev: { kind: string; interruptMarker?: unknown; interruptSettle?: unknown; interruptCause?: unknown; rompSystem?: unknown; spendGuard?: unknown; md?: unknown; source?: unknown; human?: unknown; undelivered?: unknown }): boolean {
  if (ev.kind === "retried" || ev.kind === "effortApplied" || ev.kind === "modelFallback" || ev.kind === "reconnecting") return true;
  // The spend guard's stop and its message are never low-stakes (the user 2026-10-08): swept into "2 notices" beside a
  // background report, the stop read as a plain interrupt and the guard's message went unrecognised. Each stands alone.
  if (ev.kind === "user" && (ev.spendGuard || ev.interruptCause === "spend")) return false;
  if (ev.kind === "user") return !!(ev.interruptMarker || (ev.rompSystem && ev.md) || (ev.source && !ev.human && !ev.undelivered));
  if (ev.kind === "assistant") return !!ev.interruptSettle;
  return false;
}

/** An injected notice rather than a message, for the reveal progress count (reveal-progress.ts messageCount): every
 *  notice compact mode folds, plus the spend guard's stop and its message, which are notices that never fold. */
export function isNoticeShape(ev: Parameters<typeof isFoldableNoticeShape>[0]): boolean {
  if (ev.kind === "user" && (ev.spendGuard || ev.interruptCause === "spend")) return true;
  return isFoldableNoticeShape(ev);
}

export type DisplayItem =
  | { kind: "event"; index: number }            // a pass-through event, by its index in the source array
  | { kind: "toolgroup"; indices: number[] }    // a collapsed run of ≥2 consecutive tool uses (a lone tool is an "event"), with the thoughts that came after a tool in it
  | { kind: "thought"; indices: number[] }      // consecutive thoughts that open a stretch of work: ONE collapsed Thought row
  | { kind: "noticegroup"; indices: number[] }  // a collapsed run of ≥2 consecutive foldable notices (was the "retried"-only retrygroup)
  | { kind: "gap"; lo: number; hi: number; before: number };   // turns [lo, hi) the page does not hold (T386 stage 2): empty space before event `before`

// Tools that are an EXCEPTION to collapsing: they render FIRST-CLASS even in compact mode, never swept
// into a toolgroup (the user 2026-06-17). AskUserQuestion is the "↳ You answered Claude's question" box —
// a reply to a popup, not bookkeeping — so it must stay visible, not buried under a collapsed tool run.
export const STANDALONE_TOOLS = new Set<string>(["AskUserQuestion"]);

// Given the per-event `kind` strings (and, for tool events, the tool `names` so the standalone-tool
// exception can be applied; `notices[i]` = whether event i is a foldable notice; `thoughts[i]` = whether
// event i is a thinking block with words, thoughtHasWords), produce the compacted display list. `names[i]` is
// the tool name for a "tool" event, undefined otherwise. Without `notices`, only a bare "retried" run folds
// (the pre-2026-09-08 behaviour, kept for callers that pass none); without `thoughts`, every thinking block
// is hidden (the pre-2026-10-23 behaviour).
/** The roles options C and C2 read (compact mode, 2026-11-06): `note[i]`, event i is prose that may join a run (C: a
 *  progress note, an assistant event flagged narration; C2: any assistant prose); `spliced[i]`, event i is a message the
 *  person sent that was delivered mid-turn (a user event flagged absorbed). */
export interface NoteRoles { note: readonly boolean[]; spliced: readonly boolean[] }

export function compactDisplay(kinds: readonly string[], names?: readonly (string | undefined)[], notices?: readonly boolean[], thoughts?: readonly boolean[], notes?: NoteRoles): DisplayItem[] {
  const out: DisplayItem[] = [];
  let run: number[] | null = null;                // a tool run: its tool uses, and the thoughts (and, option C, notes) in it
  let runTools = 0;
  let lead: number[] | null = null;               // a thought that opens a stretch: consecutive thoughts, one row
  let pend: number[] | null = null;               // C/C2: notes (and thoughts after them) before any tool, waiting for one
  let trail: number[] | null = null;              // C/C2: notes (and thoughts) after the run's last tool, waiting for another
  let pinArmed = false;                           // C/C2: a mid-turn message was delivered and no prose has answered it yet
  let noticeRun: number[] | null = null;          // consecutive foldable notices
  const isNote = (i: number) => !!notes && !!notes.note[i];
  // members that end up outside any run, in order: a note as its own event, a stretch of thoughts as ONE Thought row
  const loose = (ix: readonly number[]) => {
    let th: number[] | null = null;
    for (const i of ix) {
      if (kinds[i] === "thinking") { (th ||= []).push(i); continue; }
      if (th) { out.push({ kind: "thought", indices: th }); th = null; }
      out.push({ kind: "event", index: i });
    }
    if (th) out.push({ kind: "thought", indices: th });
  };
  // a LONE tool passes through as a normal event (its first-class inline tool line + fold); only a run of
  // TWO OR MORE collapses into a summary toolgroup (the user 2026-06-22). A lone tool has no group to hold the
  // thoughts that followed it, so they follow it as a Thought row of their own (and a note as itself).
  const flush = () => {
    if (run) {
      if (runTools >= 2) out.push({ kind: "toolgroup", indices: run });
      else loose(run);
      run = null; runTools = 0;
    }
    if (trail) { loose(trail); trail = null; }   // no tool came after them: they stand as themselves, after the run
  };
  const flushPend = () => { if (pend) { loose(pend); pend = null; } };
  const flushLead = () => {
    if (!lead) return;
    out.push({ kind: "thought", indices: lead });
    lead = null;
  };
  // same shape for notice runs (the user 2026-08-27, seventeen consecutive recovery rows): a lone notice
  // stays a first-class row; a run of ≥2 collapses into one expandable head
  const flushNotices = () => {
    if (!noticeRun) return;
    out.push(noticeRun.length === 1 ? { kind: "event", index: noticeRun[0] } : { kind: "noticegroup", indices: noticeRun });
    noticeRun = null;
  };
  const foldable = (i: number) => notices ? !!notices[i] : kinds[i] === "retried";
  for (let i = 0; i < kinds.length; i++) {
    const k = kinds[i];
    if (k === "thinking") {
      if (!thoughts || !thoughts[i]) continue;      // textless: hidden, and does NOT break a tool run
      if (trail) { trail.push(i); continue; }       // C/C2: after a note past the run's last tool, in order with it
      if (run) { run.push(i); continue; }           // after a tool in this stretch: a member of the run
      if (pend) { pend.push(i); continue; }         // C/C2: after a note that may yet open a run
      flushNotices();
      (lead ||= []).push(i);                        // opens a stretch: its own Thought row
      continue;
    }
    if (isNote(i)) {                                // C/C2: prose that may join a run
      if (pinArmed) {                               // the first prose after a mid-turn message: the answer, on its own
        pinArmed = false;
        flush(); flushPend(); flushLead(); flushNotices();
        out.push({ kind: "event", index: i });
        continue;
      }
      if (run) { (trail ||= []).push(i); continue; }   // after a tool: a member once another tool follows
      flushNotices(); flushLead();
      (pend ||= []).push(i);                        // before any tool: joins the run a tool opens next, else stands alone
      continue;
    }
    if (k === "assistant") pinArmed = false;        // any other assistant row: a reply as a text block answers the message too
    // A standalone tool (AskUserQuestion) is NOT collapsed: it breaks the run and passes through as its
    // own event, so renderTool → renderAsk draws the first-class box instead of "+1" inside a group.
    if (k === "tool" && !STANDALONE_TOOLS.has(names?.[i] ?? "")) {
      flushLead(); flushNotices();
      if (!run && pend) { run = pend; pend = null; }   // C/C2: the notes that led up to the run open it
      if (run && trail) { run.push(...trail); trail = null; }   // C/C2: the notes between two tools are the run's
      (run ||= []).push(i); runTools++;
      continue;
    }
    // any user row re-arms or disarms: a message delivered mid-turn arms, anything else (a prompt, a background task's
    // notice that wakes a turn) disarms
    if (k === "user") pinArmed = !!notes && !!notes.spliced[i];
    if (foldable(i)) { flush(); flushPend(); flushLead(); (noticeRun ||= []).push(i); continue; }
    flush();
    flushPend();
    flushLead();
    flushNotices();
    out.push({ kind: "event", index: i });
  }
  flush();
  flushPend();
  flushLead();
  flushNotices();
  return out;
}

/** Options C and C2: the note a collapsed run shows as its caption, the run's LATEST note, or -1 for a run that holds none. */
export function runCaption(indices: readonly number[], isNote: (i: number) => boolean): number {
  for (let j = indices.length - 1; j >= 0; j--) if (isNote(indices[j])) return indices[j];
  return -1;
}

/** Whether display unit `it` holds event `i` (a run or a thought its member, an event itself; a gap holds none): the one
 *  reading every event → unit lookup shares (a deep link, a scroll mark, a glow, the reader's anchor). */
export function itemHolds(it: DisplayItem, i: number): boolean {
  if (it.kind === "event") return it.index === i;
  if (it.kind === "gap") return false;
  return it.indices.includes(i);
}

/** The rows an EXPANDED tool run draws, in order: each tool use on its own, each stretch of consecutive thoughts as ONE
 *  Thought row (`isThought(i)`: event i is a thinking event; the run's thinking members are thoughts by construction). */
export function runRows(indices: readonly number[], isThought: (i: number) => boolean): number[][] {
  const rows: number[][] = [];
  for (const i of indices) {
    const last = rows[rows.length - 1];
    if (last && isThought(i) && isThought(last[last.length - 1])) last.push(i); else rows.push([i]);
  }
  return rows;
}

// ── A Thought row's content ──────────────────────────────────────────────────────────────────────────────
export interface ThoughtLike { kind: string; text?: string; uuid?: string; thinkTokens?: number }
/** A thinking block with words: a reasoning summary, or a progress note the API returns as thinking text. The only
 *  thinking compact mode shows; an opaque block (a signature and no text) has nothing to show but a token count. */
export function thoughtHasWords(e: ThoughtLike): boolean { return e.kind === "thinking" && !!(e.text || "").trim(); }
/** The fold key of a thinking block's expanded state in the shared fold set: think:<first block's uuid>. ONE key for both
 *  shapes, compact mode's Thought row and the full view's two-line clamp, so a thought opened in one is open in the other. */
export function thoughtKey(first: { uuid?: string } | undefined): string | undefined { return first && first.uuid ? "think:" + first.uuid : undefined; }
export interface Thought { text: string; gist: string; tokens: number; key?: string }
/** One Thought row from consecutive thinking events `members` (indices into `evs`): the words of each, in order, a blank
 *  line apart; the GIST, the first line of the LAST block with words (under summaries a stretch is a reasoning summary then
 *  the note written for the person watching, and the note is the one that answers them); the thinking tokens of the API
 *  messages it spans (the figure rides each message's first thinking row, often a textless one just before the words, so
 *  the opaque rows directly before the first member and between the members count too); and the fold key of its first. */
export function thoughtOf(evs: readonly ThoughtLike[], members: readonly number[]): Thought {
  const words = members.map((i) => evs[i]).filter(thoughtHasWords);
  const text = words.map((e) => (e.text || "").trim()).join("\n\n");
  const lastWords = (words[words.length - 1]?.text || "").split("\n").find((l) => l.trim()) || "";
  let tokens = 0;
  for (const j of thoughtSpan(evs, members)) {
    const e = evs[j];
    if (e.kind === "thinking" && typeof e.thinkTokens === "number" && e.thinkTokens > 0) tokens += e.thinkTokens;
  }
  return { text, gist: lastWords.replace(/\s+/g, " ").trim(), tokens, key: thoughtKey(evs[members[0]]) };
}
/** Every event a Thought row over `members` reads, in order: the thinking rows directly before its first member, then each
 *  row from the first member to the last (the textless ones between them included). The textless rows here belong to no
 *  display unit, so the keyed paint signs this span rather than the members (unit-diff.ts unitReads): a token figure that
 *  lands on one of them in a later frame repaints the row that shows it. */
export function thoughtSpan(evs: readonly { kind: string }[], members: readonly number[]): number[] {
  if (!members.length) return [];
  let lo = members[0];
  while (lo > 0 && evs[lo - 1].kind === "thinking") lo--;
  const span: number[] = [];
  for (let j = lo; j <= members[members.length - 1]; j++) span.push(j);
  return span;
}

/** The member a display unit is PLACED and TIMED by (T339, the user 2026-09-11). A collapsed NOTICE run anchors on the
 *  member with the LATEST epoch (ties: the later one), never the first: a run can hold a notice stamped earlier than the
 *  rows around it (one that kept the moment it was queued and landed in the transcript at delivery), so timed by its first
 *  member the run wore yesterday's clock among today's rows and the day walk read the step back as a day boundary. The
 *  latest member is the one in sequence with its neighbours; the head's rail time, the day walk and the walk's exit all
 *  read it (a run with no timed member falls to its first). Every other unit keeps its FIRST member, as before: a lone
 *  event is its own anchor, and a tool run's members are in transcript order with its head timed by its first.
 *  `epochAt(i)` is event i's epoch or null. */
export function itemAnchor(it: DisplayItem, epochAt: (i: number) => number | null): number {
  if (it.kind === "event") return it.index;
  if (it.kind === "gap") return it.before;   // a gap has no member: the event below it stands for its place
  if (it.kind === "toolgroup" || it.kind === "thought") return it.indices[0];
  let best = it.indices[0], bestEp: number | null = null;
  for (const i of it.indices) {
    const ep = epochAt(i);
    if (ep != null && (bestEp == null || ep >= bestEp)) { best = i; bestEp = ep; }
  }
  return best;
}

// One pluralized count of a tool kind, e.g. { label: "Edits", count: 3 }. The label keeps the tool's
// own Capitalized name (so it reads AS a tool — the user 2026-06-14, matching the bold .tool-name in
// the non-compact view); only the Edit variants merge under "Edit".
// ── The tool rows in the user's terms (T418, the user 2026-09-14: the chat's tool rows read the way the desktop app's do) ──
// The group head speaks by ACTION, not by tool name: "Ran 11 commands, read 4 files, edited 3 files, created 2 files +37 -0". A single
// tool use reads the same phrase in the singular. Inside a group each row's label is the model's own description when it wrote one,
// else a phrase derived from the tool's input in the same voice. Nothing here reads desc for the head's counts or totals: those come
// from the tool names and the edits' diff rows alone. Counts: commands, searches, fetches, agents and other tools count USES; reads,
// edits and creations count DISTINCT files; the head orders its phrases by the number it prints.

export interface ToolLike {
  name: string;
  desc?: string;
  input?: string;       // the tool_use input as JSON (clipped by the kernel)
  file?: string;        // file_path or path, when the input carried one
  diff?: string;        // the kernel's minimal diff for Edit / MultiEdit / Write ("- old" / "+ new" lines)
  diffRows?: readonly { sign: string }[];   // the real-line diff rows when the record carried a structuredPatch
}

export type ToolAction = "command" | "create" | "edit" | "read" | "search" | "fetch" | "agent" | "tasklist" | "tool";

const ACTION: Record<string, ToolAction> = {
  Bash: "command",
  Write: "create",
  Edit: "edit", MultiEdit: "edit", NotebookEdit: "edit",
  Read: "read",
  Grep: "search", Glob: "search",
  WebFetch: "fetch", WebSearch: "fetch",
  Agent: "agent", Task: "agent", Workflow: "agent",
  TodoWrite: "tasklist", TaskCreate: "tasklist", TaskUpdate: "tasklist", TaskGet: "tasklist", TaskList: "tasklist",
};
export function toolAction(name: string): ToolAction { return ACTION[name] || "tool"; }

/** +added −removed over a tool's diff: the real-line rows when present, else the minimal diff's signed lines. */
export function diffTotals(t: ToolLike): { add: number; del: number } {
  if (t.diffRows && t.diffRows.length) {
    let add = 0, del = 0;
    for (const r of t.diffRows) { if (r.sign === "+") add++; else if (r.sign === "-") del++; }
    return { add, del };
  }
  let add = 0, del = 0;
  for (const ln of (t.diff || "").split("\n")) { if (ln.startsWith("+")) add++; else if (ln.startsWith("-")) del++; }
  return { add, del };
}

function parseInput(t: ToolLike): Record<string, unknown> {
  try { const o = JSON.parse(t.input || ""); return o && typeof o === "object" ? o as Record<string, unknown> : {}; } catch { return {}; }
}
function clip(s: string, n = 80): string { s = s.replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
/** The host a fetched url names; a url with no host (file:, data:, about:) falls to the url itself, clipped like every other label part. */
function hostOf(url: string): string { try { const h = new URL(url).hostname; return h ? clip(h) : clip(url); } catch { return clip(url); } }   // the host clipped like every label part (round three, low c)
/** The one search's own words: "for <pattern>" (Grep, Glob); the path, when the input carries one, is the event's file, which the
 *  renderer prints as the link after "in ". */
function searchWords(t: ToolLike): string | null {
  const o = parseInput(t);
  if (t.name === "Grep" || t.name === "Glob") { const pat = typeof o.pattern === "string" ? o.pattern : ""; return pat ? "for " + clip(pat) : null; }
  return null;
}
const totalsText = (add: number, del: number): string => (add || del ? `+${add} -${del}` : "");

/** One head phrase per action over a group of tool uses. Commands, searches, fetches, agents and other tools count uses; reads,
 *  edits and creations count DISTINCT files (a file edited twice is one file, its rows summed). The phrases are ordered by the
 *  number they print (the largest first; ties keep first appearance). The edits' totals are returned apart, summed over every
 *  edit, for the caller to print ONCE: "ran 11 commands", "read 4 files", "edited 3 files", "created 2 files", "searched 3 times"
 *  (or "searched for <pattern>" for the group's sole search), "fetched 2 pages", "ran 2 agents", "updated the task list",
 *  "used 2 tools". A creation prints no totals: the kernel emits no diff for a Write. */
export interface ActionPhrase { action: ToolAction; count: number; printed: number; text: string; add: number; del: number }
export function actionPhrases(tools: readonly ToolLike[]): ActionPhrase[] {
  const order: ToolAction[] = [];
  const by = new Map<ToolAction, { count: number; files: Set<string>; nofile: number; add: number; del: number; first: ToolLike }>();
  for (const t of tools) {
    const a = toolAction(t.name);
    let e = by.get(a);
    if (!e) { e = { count: 0, files: new Set(), nofile: 0, add: 0, del: 0, first: t }; by.set(a, e); order.push(a); }
    e.count++;
    if (t.file) e.files.add(t.file); else e.nofile++;
    if (a === "edit") { const d = diffTotals(t); e.add += d.add; e.del += d.del; }
  }
  const out: ActionPhrase[] = [];
  for (const a of order) {
    const e = by.get(a)!; const n = e.count;
    const byFile = a === "read" || a === "edit" || a === "create";
    const files = e.files.size + e.nofile;   // the distinct files, plus one per use that carried no file (a NotebookEdit's notebook_path is not a file for the kernel today): round three's medium, where "size or uses" let a sibling's path decide the count
    const printed = byFile ? files : n;
    let text: string;
    switch (a) {
      case "command": text = n === 1 ? "ran a command" : `ran ${n} commands`; break;
      case "create": text = files === 1 ? "created a file" : `created ${files} files`; break;
      case "edit": text = files === 1 ? "edited a file" : `edited ${files} files`; break;
      case "read": text = files === 1 ? "read a file" : `read ${files} files`; break;
      case "search": { const w = n === 1 ? searchWords(e.first) : null; text = n === 1 ? (w ? "searched " + w : "searched once") : `searched ${n} times`; break; }
      case "fetch": text = n === 1 ? "fetched a page" : `fetched ${n} pages`; break;
      case "agent": text = n === 1 ? "ran an agent" : `ran ${n} agents`; break;
      case "tasklist": text = "updated the task list"; break;
      default: text = n === 1 ? "used a tool" : `used ${n} tools`;
    }
    out.push({ action: a, count: n, printed, text, add: e.add, del: e.del });
  }
  out.sort((x, y) => y.printed - x.printed);   // stable: ties keep first appearance
  return out;
}
/** The head's parts: the phrases joined by commas, the first letter capitalised, and the edits' totals summed for one printing. */
export function actionParts(tools: readonly ToolLike[]): { text: string; add: number; del: number } {
  const ph = actionPhrases(tools);
  const s = ph.map((p) => p.text).join(", ");
  return { text: s ? s[0].toUpperCase() + s.slice(1) : "", add: ph.reduce((a, p) => a + p.add, 0), del: ph.reduce((a, p) => a + p.del, 0) };
}
/** The whole head line as text: the phrases, then the totals once at the end when any edit carried rows. */
export function actionHead(tools: readonly ToolLike[]): string {
  const p = actionParts(tools); const tot = totalsText(p.add, p.del);
  return p.text + (tot ? " " + tot : "");
}

/** The raw text a tool row's expanded IN row shows (T418): a Bash command as the model typed it (the desktop app's "$ …" box);
 *  every other tool its input JSON as before. */
export function toolInputText(t: ToolLike): string {
  if (t.name === "Bash") { const o = parseInput(t); if (typeof o.command === "string" && o.command) return o.command; }
  return t.input || "";
}

/** A row's collapsed label: the model's description when it wrote one, else the derived phrase in the same voice. `code` marks a
 *  label that is the command itself (a Bash with no description), for the code face. `link` marks a label that ends expecting the
 *  event's file, which the renderer prints as the link right after it ("Read ", "Edited ", "Created ", "Searched for x in "); an
 *  event with a file the label does not name still gets its link after the label, so no file is lost and none is printed twice.
 *  An edit's totals are the diff fold's toggle, the renderer's; a failed edit changed nothing and prints none (round three). */
export interface RowLabel { text: string; link?: boolean; code?: boolean; secondary?: string }
export function toolRowLabel(t: ToolLike): RowLabel {
  const desc = (t.desc || "").trim();
  if (desc) return { text: clip(desc, 160) };
  const o = parseInput(t);
  if (t.name === "WebSearch") { const q = typeof o.query === "string" ? o.query : ""; return { text: q ? "Searched the web for " + clip(q) : "Searched the web" }; }   // its head counts as a fetch; its row says what it searched
  const a = toolAction(t.name);
  switch (a) {
    case "command": {
      const cmd = typeof o.command === "string" ? o.command : "";
      const first = cmd.split("\n").map((l) => l.trim()).find((l) => l.length > 0) || "";   // the first non-blank line: a command opening with a blank line is not an empty label
      return first ? { text: clip(first), code: true } : { text: "Ran a command" };
    }
    case "read": return t.file ? { text: "Read ", link: true } : { text: "Read a file" };
    case "edit": return t.file ? { text: "Edited ", link: true } : { text: "Edited a file" };
    case "create": return t.file ? { text: "Created ", link: true } : { text: "Created a file" };
    case "search": { const w = searchWords(t); const base = w ? "Searched " + w : "Searched"; return t.file ? { text: base + " in ", link: true } : { text: base }; }
    case "fetch": { const u = typeof o.url === "string" ? o.url : ""; return { text: u ? "Fetched " + hostOf(u) : "Fetched a page" }; }
    case "agent": return { text: "Ran an agent" };
    case "tasklist": return { text: "Updated the task list" };
    default: return { text: "Used a tool", secondary: t.name };
  }
}
