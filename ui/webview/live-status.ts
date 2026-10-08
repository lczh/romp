// Live status (2026-10-08): what a session is doing THIS SECOND that no transcript record says yet, each piece read
// from an authoritative Claude Code event and nothing else: a thinking stretch (the CLI's system/thinking_tokens
// frames: the first starts it, the next assistant message, the result, an API retry, a stop or the CLI going away
// ends it), and each running subagent's progress (the CLI's task_progress: its last tool, its tool-call and token
// counts). The kernel ships it as one small frame per session ({type:"liveStatus", id, thinking, tasks}: kernel.py
// _live_status_frame), outside the chat build, and the page counts the seconds itself. Pure and DOM-free so
// node --test executes it; render.ts owns the DOM.
import { durLabel } from "./duration";

/** A thinking stretch streaming now: `t` its first frame (epoch seconds), `quiet` its latest frame once the stream
 *  has gone silent for a while (the kernel decides when, THINKING_QUIET_S), absent while frames keep coming. */
export interface ThinkingLive { t: number; quiet?: number }
/** One running subagent's progress as the CLI's task_progress reported it. */
export interface TaskProgress { tool?: string; calls?: number; tokens?: number; summary?: string }
export interface LiveStatus { thinking: ThinkingLive | null; tasks: Record<string, TaskProgress> }

/** A frame's payload, defensively read (a remote kernel on another build, a malformed frame): null fields stay null. */
export function readLiveStatus(m: { thinking?: unknown; tasks?: unknown }): LiveStatus {
  const th = m.thinking as ThinkingLive | null | undefined;
  const thinking = th && typeof th === "object" && typeof th.t === "number" && th.t > 0
    ? (typeof th.quiet === "number" && th.quiet > 0 ? { t: th.t, quiet: th.quiet } : { t: th.t }) : null;
  const tasks: Record<string, TaskProgress> = {};
  if (m.tasks && typeof m.tasks === "object") {
    for (const [k, v] of Object.entries(m.tasks as Record<string, unknown>)) {
      if (!v || typeof v !== "object") continue;
      const p = v as TaskProgress, row: TaskProgress = {};
      if (typeof p.tool === "string" && p.tool) row.tool = p.tool;
      if (typeof p.calls === "number" && p.calls >= 0) row.calls = p.calls;
      if (typeof p.tokens === "number" && p.tokens >= 0) row.tokens = p.tokens;
      if (typeof p.summary === "string" && p.summary.trim()) row.summary = p.summary.trim();
      if (Object.keys(row).length) tasks[k] = row;
    }
  }
  return { thinking, tasks };
}

/** The chat's line: "Thinking · 14s", and once the stream has gone silent "Thinking · 1m 20s · quiet for 15s", so a
 *  stalled stream reads as stalled rather than as a count that never ends. */
export function thinkingText(th: ThinkingLive, nowS: number): string {
  const s = "Thinking · " + durLabel(nowS - th.t);
  return th.quiet ? s + " · quiet for " + durLabel(nowS - th.quiet) : s;
}

/** The compact form for the Sessions pane's chip and the tab's tooltip: "Thinking · 14s", silent "Thinking · quiet 15s". */
export function thinkingShort(th: ThinkingLive, nowS: number): string {
  return th.quiet ? "Thinking · quiet " + durLabel(nowS - th.quiet) : "Thinking · " + durLabel(nowS - th.t);
}

/** The hover sentence behind both: what the frames proved, in words. */
export function thinkingTip(th: ThinkingLive, nowS: number): string {
  const s = `the model has been thinking for ${durLabel(nowS - th.t)}`;
  return th.quiet ? s + `; no thinking update has arrived for ${durLabel(nowS - th.quiet)}` : s;
}

/** A token count in the compact form the compaction divider uses: 6514 → "6.5k", 795232 → "795k", 900 → "900". */
export function tokensShort(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return (k < 10 ? k.toFixed(1).replace(/\.0$/, "") : Math.round(k)) + "k";
}

/** A thinking row's label from the exact per-message figure (usage.output_tokens_details.thinking_tokens). */
export function thoughtLabel(tokens: number): string {
  return `Thought · ${tokensShort(tokens)} tokens`;
}

/** A running subagent's one-line progress: "Bash · 12 tool calls · 41k tokens" (its own summary first when the CLI
 *  sends one). `brief`: the row already lists the agent's steps (its fold label counts them, its preview names the latest),
 *  so only what the steps cannot say rides here, the tokens (and the CLI's summary). "" when there is nothing to say. */
export function taskProgressText(p: TaskProgress | undefined, brief = false): string {
  if (!p) return "";
  const parts: string[] = [];
  if (p.summary) parts.push(p.summary);
  else if (p.tool && !brief) parts.push(p.tool);
  if (typeof p.calls === "number" && p.calls > 0 && !brief) parts.push(`${p.calls} tool call${p.calls === 1 ? "" : "s"}`);
  if (typeof p.tokens === "number" && p.tokens > 0) parts.push(`${tokensShort(p.tokens)} tokens`);
  return parts.join(" · ");
}

/** The thinking tokens of the turn reply `i` closes: the sum of the exact per-message figures on the thinking rows
 *  between the turn's trigger (the most recent user-role line, the worked footer's own start) and `i`. Null when the
 *  trigger is outside the events held (a windowed head), so a partial sum is never shown as the turn's. */
export function turnThoughtTokens(events: readonly { kind: string; thinkTokens?: number }[], i: number): number | null {
  let sum = 0;
  for (let j = i; j >= 0; j--) {
    const e = events[j];
    if (e.kind === "user") return sum;
    if (e.kind === "thinking" && typeof e.thinkTokens === "number" && e.thinkTokens > 0) sum += e.thinkTokens;
  }
  return null;
}
