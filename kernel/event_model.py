#!/usr/bin/env python3
"""romp-event-model — the rebuilt bottom-layer parser (docs/event-model.md).

Turns a romp session's transcript(s) into the Session -> Turn -> Atom tree the
design doc pins down: the Claude streaming protocol made graph-aware. An atom is
a streaming message, a turn is the user-to-`end_turn` cycle, and the on-disk
transcript is those messages plus the graph metadata needed to rebuild them
after rewinds and resume-forks.

NOT wired into the live pipeline. This is a standalone module built for
side-by-side evaluation against bin/romp-events (inventory Decision 1); it does
NO summaries / relevance / asks / links / model calls — only the event layer.

The Session/Turn/Atom shapes are substrate-neutral. Everything specific to
rebuilding them from the append-only on-disk graph lives in the FILE ADAPTER
section and only there; on a future stream substrate the same tree is filled
directly (steps that recover linearity, `ended`, and idle simply disappear).

CLI:
  romp-event-model --test <transcript>   # human dump of one session's turn/atom tree
  romp-event-model --emit <transcript>   # the Session tree as JSON

Auxiliary inputs the file adapter may read (same category as the transcript):
  states/<sid>.jsonl        -> idle atoms (real idle transitions, not a silence heuristic)
                               + salvaged assistant atoms (orphanReply markers — replies the
                               transcript lost to an API-errored try; judge parse only)
  timeline/messages.jsonl   -> peer rompUuid for a postal atom (join on the msg id)
"""
import array, bisect, collections, copy, functools, gzip, json, os, re, sys, time, hashlib, threading, traceback, weakref, zlib
from datetime import datetime
from pathlib import Path

HOME     = Path.home()
STATE    = Path(os.environ.get("ROMP_STATE_DIR")   # per-kernel state root override (plans/multi-kernel.md)
                or Path(os.environ.get("XDG_STATE_HOME") or str(HOME / ".local/state")) / "romp")
# `or`, not a .get default: an EMPTY XDG_STATE_HOME is unset (the XDG spec, and every shell reader's
# ${XDG_STATE_HOME:-...}). A .get default kept the empty string and made the root the RELATIVE path
# romp under the process cwd, so the kernel and the shell surfaces used different roots.
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")) / "projects"   # per-kernel Claude root (plans/multi-kernel.md phase 2)
NAMES    = STATE / "names"
STATES_DIR   = STATE / "states"
MESSAGES_LOG = STATE / "timeline" / "messages.jsonl"

# A turn ends when the model hands the floor back: stream `end_turn` / `stop_sequence`.
# Mid-turn the model stops with `tool_use` (a tool cycle) — that does NOT end the turn.
END_STOPS = ("end_turn", "stop_sequence")
# The toolUseResult keys a consumer actually reads (Edit's structuredPatch → diffRows,
# AskUserQuestion's answers map → the answered box, the Agent tool's agentId / isAsync → the
# subagent join + the background-launch flag, plans/subagent-transcripts.md). The atom carry is
# GATED on one of these being present: an unconditional dict carry held every Read result's full
# file bytes in the parse cache by reference — ~a fifth of transcript bytes on read-heavy sessions —
# for shapes nothing reads. Widen this set when a new consumer appears; never revert to carry-all.
TUR_CONSUMED_KEYS = frozenset(("answers", "structuredPatch", "agentId", "isAsync"))
# romp's own postal marker, injected into a delivered message body. It is the ONLY
# postal signal — never the generic "Stop hook feedback:" prefix (any blocking Stop
# hook produces that). The sender rompUuid is resolved from timeline/messages.jsonl
# by joining this id (the on-disk marker carries the id but not the sender).
# COMMENT FORM ONLY — the same rule ROMP_INJECT_RE/ROMP_AUTO_RE were given on 2026-07-08, and the
# rule docs/event-model.md already documents ("Postal is detected by the `<!-- romp-msg-id: <id> -->`
# marker"). Both emitters have only ever written the literal comment (postal_service format_inbox /
# format_push, where the file calls that marker a stable CONSUMER CONTRACT), but a bare word-match
# also fired on text that merely MENTIONS the marker: an agent quoting the mail it just received, a
# hook or tool output echoing one (a grep of a transcript, a fetched page), a human prompt about the
# marker itself. The failure is not cosmetic — with no matching id in the log author_of still
# returned {"peer": None}, and that is a dict, so the segment reads peer-rather-than-human and both
# the planner and the courier drop it: the user's ask silently gets no card.
POSTAL_RE = re.compile(r"<!--\s*romp-msg-id:\s*(\S+?)\s*-->")
POSTAL_KIND_RE = re.compile(r"<!--\s*romp-msg-kind:\s*(delegate|coordinate|question)\s*-->")
# Both markers, IN ORDER, so a sender / message id / declared kind always describe the SAME message.
# A drain writes id-then-kind per message and concatenates every pending message into one injected
# text (postal_service format_inbox/format_push), so a two-message delivery carries two of each —
# and three separate scans of that text picked three different answers: the author from the last
# marker, the id and the kind from the first. That filed one peer's identity against another peer's
# message, planting the delegation-tracking node on the wrong board. postal_pairs() below is the one
# parser author_of and both judge scans now share.
_POSTAL_ANY_RE = re.compile(r"<!--\s*romp-msg-(id|kind):\s*(\S+?)\s*-->")
_POSTAL_KINDS = ("delegate", "coordinate", "question")
# romp's marker on a message IT injected straight into a pane (a feed NUDGE / auto-nudge / Retry — NOT a
# peer message, and NOT a follow-up YOU typed). It means "render this as a romp-injected system message"
# (the gray bubble), distinct from a human prompt or a peer's postal message. ONLY romp-injected authors
# romp: romp-goal-id is orthogonal "which goal" metadata that rides EVERY feed follow-up, INCLUDING ones
# you type yourself — those are yours (blue human bubble), so a goal-id alone must NOT author romp; the
# kernel adds romp-injected for nudges only (the user 2026-06-20).
# COMMENT FORM ONLY (the user 2026-07-08): every real emitter writes the literal `<!-- romp-injected -->`
# (kernel _followup_body / RETRY_MSG, the sdk backend's restart notices; romp-judge's NUDGE_MARKER_RE
# already matches this way). A bare word-match also fired on message CONTENT that merely *mentions* the
# marker — the user's typed follow-up quoted a card summary discussing romp-injected and rendered as a
# GRAY ROMP CARD. \s* so the absorbed-atom path's historical whitespace-collapse still matches.
ROMP_INJECT_RE = re.compile(r"<!--\s*romp-injected\s*-->")
# romp-AUTO: an AUTO-nudge (the kernel's background _auto_nudge_tick), distinct from a Nudge BUTTON click or a
# typed follow-up — both of which are romp-injected too. Only auto-nudges (+ postal) are "from romp" for the
# romp-logo marker; the user's own button/follow-ups are not (the user 2026-06-23). Rides alongside
# romp-injected; an atom carrying it gets atom["rompAuto"]=True for the timeline/chat to mark.
# Comment form only, same reason as ROMP_INJECT_RE (content mentioning the marker must not match).
ROMP_AUTO_RE = re.compile(r"<!--\s*romp-auto\s*-->")
# A sender-declared RENDER HINT on an injected message (the user 2026-08-15): auto-generated text — a
# kickoff template, a scripted brief — carries `<!-- romp-tag: <label> -->` (romp send --tag, or the
# marker appended by hand) so the chat shows it as machine-sent under that label instead of posing it
# as the user's typed words. The label is the SENDER's own word; romp attaches no meaning to it — a
# render hint, not a message type. Comment form only, same reason as ROMP_INJECT_RE.
MSG_TAG_RE = re.compile(r"<!--\s*romp-tag:\s*([A-Za-z0-9][A-Za-z0-9-]{0,23})\s*-->")
# Harness-injected SYSTEM wrappers that are NOT the user: a background-task completion (`<task-notification>`,
# fired when a backgrounded Agent/Task finishes) and `<system-reminder>` blocks. In an SDK session these arrive
# over the stream as promptSource "sdk", so sdk_human would author them 'human' → _is_opener opens a turn →
# the planner force-pins a junk goal titled "<task-notification>" (the user 2026-06-30, screenshot). Anchored
# at the START so a real user message with a reminder APPENDED isn't caught (the kernel splits those off).
# ...and the CLI's harness PREAMBLE (Claude Code 2.1.263, the user 2026-09-07): the CLI now puts a fixed
# paragraph AHEAD of the wrapper tag so the MODEL never mistakes an injected turn for its user —
# "[SYSTEM NOTIFICATION - NOT USER INPUT]" before a background task's <task-notification>. The tag no
# longer opens the record, so the tag-anchored test above stopped seeing these as harness-injected and
# every background agent's completion authored 'human' → the blue "you typed this" bubble, wearing the
# preamble paragraph as its text, and a junk goal per completion. Authorship is FIELD-FIRST now (author_of
# reads the record's own `origin.kind`); this text shape is the fallback for a record that lacks the field
# (an older CLI, the live stream's echo, a queued copy). Matched as the record's OPENING line only, like
# the tags — a prompt that merely quotes the sentinel is still the user's.
SYSTEM_WRAPPER_RE = re.compile(r"^\s*(?:\[SYSTEM NOTIFICATION - NOT USER INPUT\]|<(?:task-notification|system-reminder)\b)")
# A scheduled task's FIRED PROMPT (origin.subkind "scheduled-trigger") wears its own preamble. Unlike a
# notification it IS a prompt — the work that follows is real and must open a turn — so it authors 'sdk'
# (a programmatic prompt: opens a turn, never the human's blue bubble), not 'system'.
SCHEDULED_PREAMBLE_RE = re.compile(r"^\s*\[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT\]")
# The preamble PARAGRAPH, wherever it sits in an injected record's text (the sentinel line and the
# explanatory lines that run to the next blank line) — what strip_harness_preamble lifts out of the shown
# text into the card's fold. Only ever applied to a record already classified as NOT the human's.
HARNESS_PREAMBLE_PARA_RE = re.compile(
    r"\[(?:SYSTEM NOTIFICATION - NOT USER INPUT|SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT)\][^\n]*"
    r"(?:\n(?!\s*\n)[^\n]*)*")
# Claude Code's NATIVE teammate/agent-message channel — one agent messages another, DISTINCT from romp's
# own postal bus (no romp-msg-id). It's delivered as a promptSource "sdk" user record whose text is a
# <prompt> wrapper: "Another Claude session sent a message:" + one or more <teammate-message
# teammate_id="…" color="…" [summary="…"]>body</teammate-message> blocks + a fixed "permission laundering"
# boilerplate. We recognize it so the chat renders it as its OWN collapsed card, not a blue "you typed
# this" bubble (the user 2026-07-05: idle_notification coordination JSON showed as a human message).
# Anchored at the START (optionally inside a one-level <prompt>/<unit> wrapper) so it matches a real
# DELIVERY and NOT a conversation SUMMARY that merely quotes one ("<turn>\nUSER ASKED: Another Claude…").
# Three text shapes, all the CLI's own fixed lead-ins (2.1.263 added the mid-turn forms and the
# <cross-session-message from="…"> envelope its cross-session SendMessage delivers): matched by that lead-in,
# never by the body. Field-first still wins — a record stamped origin.kind "peer" authors 'teammate'
# whatever its text (author_of).
TEAMMATE_MSG_RE = re.compile(r"^\s*(?:<\w+>\s*)?(?:(?:Another Claude session|A peer session) sent a message"
                             r"(?: while you were working)?:|<cross-session-message\b)", re.I)
TEAMMATE_BLOCK_RE = re.compile(r"<teammate-message\b([^>]*)>(.*?)</teammate-message>", re.S)
CROSS_SESSION_BLOCK_RE = re.compile(r"<cross-session-message\b([^>]*)>(.*?)</cross-session-message>", re.S)
# Claude Code's slash-command transcript wrappers. The INVOCATION (`<command-name>`) and its OUTPUT
# (`<local-command-stdout>`, or `<local-command-stderr>` when the command fails) become a tracked COMMAND TURN
# (the user 2026-06-29) — see atoms(); the rest (`<command-message|args|contents>`, `<local-command-caveat>`)
# stay skipped as harness noise.
CMD_WRAP_RE = re.compile(r"^\s*<(?:command-(?:name|message|args|contents)|local-command-(?:stdout|stderr|caveat))>")
COMMAND_NAME_RE = re.compile(r"^\s*<command-name>([^<]*)</command-name>")           # the slash command itself, e.g. "/usage"
# ...but the CLI does NOT fix the wrapper ORDER: a built-in writes <command-name> first, while a SKILL /
# custom command writes <command-message> first ("/jld" → "<command-message>jld</command-message>\n
# <command-name>/jld</command-name>…"). Anchored-only matching therefore MISSED every skill invocation: the
# record fell through to the harness-noise skip, so the command never became an atom, and the work it
# triggered was absorbed into whatever segment came BEFORE it — a JLD session ran with its request buried in
# the preceding "/model" command segment and no card of its own (the user 2026-07-22). Find the tag anywhere,
# but ONLY inside a record that already begins with a command wrapper (CMD_WRAP_RE), so prose that merely
# quotes "<command-name>" can never be mistaken for an invocation.
COMMAND_NAME_ANY_RE = re.compile(r"<command-name>([^<]*)</command-name>")
COMMAND_ARGS_RE = re.compile(r"<command-args>([\s\S]*?)</command-args>")            # its arguments (often empty)
LOCAL_STDOUT_RE = re.compile(r"^\s*<local-command-stdout>([\s\S]*?)</local-command-stdout>")   # the command's output
# ...and its twin when the command FAILS: a custom command, skill or MCP prompt that cannot expand, or a forked
# command that errors, writes its error as a plain user record (no isMeta) wrapped in <local-command-stderr>. The
# CLI reads that record as the command's output, never a prompt. Unmatched, it fell through every skip to a human
# atom: an open turn of its own whose text was the raw tags, read by the judges as a human ask (2026-10-19).
LOCAL_STDERR_RE = re.compile(r"^\s*<local-command-stderr>([\s\S]*?)</local-command-stderr>")


def local_command_output(text):
    """The match of a slash command's output record, stdout or stderr (group 1 is the output), or None."""
    return LOCAL_STDOUT_RE.match(text) or LOCAL_STDERR_RE.match(text)
# A slash command's stdout is captured from the TUI VERBATIM, so it can carry ANSI SGR color codes
# (e.g. /rate-limit-options prints "\x1b[38;5;114mRemoved monthly spend limit\x1b[39m" in green). The
# ESC byte is invisible but the "[38;5;114m…[39m" renders as LITERAL text in the chat (the user
# 2026-07-16). Strip the full CSI/SGR family at the atom source — the one place both the chat and the
# timeline read — so the codes never reach any renderer. Only a command's output (stdout or stderr) needs
# this; model API text never contains ANSI.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(s: str) -> str:
    """Remove ANSI CSI/SGR escape sequences (color, cursor) from captured terminal output."""
    return ANSI_RE.sub("", s) if s else s
# The harness's OWN skill load (the user 2026-09-10, confused by feed cards titled after a skill their worker
# sessions never asked for): when the CLI loads a skill for the model by itself (its orchestration mode loads
# the workflow-authoring reference with the prompt whenever that mode is on), it writes the command wrapper
# as an isMeta user record with a BARE name and a <skill-format> tag and NO arguments slot, the three tags and
# nothing else, and the skill's markdown follows in its own isMeta record. Nobody typed it: a typed command or
# skill carries its leading slash ("/jld", "/compact") in both wrapper orders, and a typed skill in the new
# format carries the same <skill-format> tag with its <command-args>, so the tag alone tells nothing and the
# three together are the shape (the arguments slot is absent only when nothing was typed at all). The command branch read the bare
# wrapper as a typed "/workflow-authoring": a human command atom that opened a segment of its own right
# after the real prompt, so the work that answered the prompt was filed under a card titled from the skill's
# name, anchored on a record the feed took for the user's words. It is harness noise of the same class as the
# other wrappers: no atom and no twin, and the prompt it rode in on keeps its segment and its work. The pre-pass
# records each one it skips on the carry, and the parse reports them as session["skillLoads"] for the judge's
# stamp on the tops older stores minted from them.
SKILL_FORMAT_RE = re.compile(r"<skill-format>")


def is_skill_load_wrapper(text):
    """True for the harness's own skill-load wrapper: a record that begins with a command wrapper, carries a
    <skill-format> tag, has NO <command-args> slot and names its command WITHOUT a leading slash. A typed
    invocation has the slash, and one with anything typed after the name has the slot; a bare name with a
    slot (the shape the echo-retire test guards) stays a command."""
    if not text or not CMD_WRAP_RE.match(text) or not SKILL_FORMAT_RE.search(text) or COMMAND_ARGS_RE.search(text):
        return False
    m = COMMAND_NAME_ANY_RE.search(text)
    return bool(m) and not m.group(1).strip().startswith("/")


# The Skill tool's INSTRUCTIONS record (the user 2026-07-08): after a `Skill` tool_use + its "Launching
# skill: X" tool_result, the CLI writes the skill's full markdown as an isMeta user record opening with
# this line. It's the ONE isMeta payload worth keeping — surfaced as a flagged, content-EMPTY atom (the
# text rides atom["skillMd"], so no assistant-text reader — judge work text, captions, summary anchors —
# ever mistakes the skill's instructions for something the agent wrote). The kernel folds it into the
# invoking Skill tool event, collapsed by default.
SKILL_CONTENT_RE = re.compile(r"^\s*Base directory for this skill:")
SKILL_MD_CAP = 16000              # transport cap for the joined skill markdown (skills run ~2-15k chars)
# An image fed to the model via a tool (a Read of a PNG, a screenshot) — Claude Code emits a synthetic
# user record carrying JUST this human-readable placeholder alongside the image block. On disk it's isMeta
# (skipped below), but that flag is absent on the SDK live stream, so the twin skip in sdk_backend keys on
# this pattern instead; matching here too keeps the two adapters in lockstep and covers any Claude build
# that omits isMeta on the record. The `:` after Image distinguishes it from the composer's `[Image #N]`
# paste chips, which never stand alone as a whole turn.
IMG_ECHO_RE = re.compile(r"^\[Image:[^\]]*\]$")
SUMMARY_CAP = 8000                # cap the compaction-summary text attached to a compact_boundary atom (a raw
#   summary runs ~16k chars; the head carries the key sections, and it re-ships with the tail — keep it bounded)


# ───────────────────────── small helpers ─────────────────────────
def parse_z(s):
    """Transcript timestamp (ISO-8601 UTC, '…Z') -> epoch int, or None.

    Every transcript record carries a timestamp, so this runs tens of thousands of times per fleet parse
    — the C-accelerated datetime.fromisoformat (3.11+, accepts a '+00:00' offset + fractional secs) is
    ~13x faster than strptime and was a real chunk of "startup is slow" (the user 2026-07-03). strptime
    stays as a fallback for any exotic form fromisoformat rejects, so behavior is unchanged."""
    if not s:
        return None
    s = s.strip()
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        pass
    s = s.replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except Exception:
            pass
    return None


def _result_text(content):
    """The text of a tool_result's content, whether it's a plain string or a list of {type:text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


_WF_META_DESC_RE = re.compile(r"\b(description|name)\s*:\s*(['\"])(.*?)\2", re.S)


def _meta_literal(script):
    """The text inside a Workflow script's FIRST `export const meta = {...}` literal, matched by brace depth (a
    nested object or array inside meta never ends the scan early); '' when none."""
    src = str(script or "")[:8000]
    m = re.search(r"export\s+const\s+meta\s*=\s*\{", src)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(src) and depth:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.end():i - 1] if depth == 0 else src[m.end():]


def _launch_desc(name, inp):
    """The dispatch's OWN words at launch time: an Agent/Task's description, a Workflow's meta description
    (else its meta name) read off the script's meta literal alone (never a schema field's or a prompt's quoted
    description later in the script), else ''. Kept on the task row as `launchDesc` and never overwritten by
    the completion notification, whose summary replaces `summary` when the run ends (T319: the feed matches a
    session-started goal to the launch that produced it on these words)."""
    inp = inp if isinstance(inp, dict) else {}
    d = str(inp.get("description") or "").strip()
    if not d and name == "Workflow":
        lit = _meta_literal(inp.get("script"))
        meta = {m.group(1): m.group(3) for m in _WF_META_DESC_RE.finditer(lit)} if lit else {}
        d = str(meta.get("description") or meta.get("name") or "").strip()
        if not d and inp.get("scriptPath"):
            d = os.path.splitext(os.path.basename(str(inp["scriptPath"])))[0]
    return " ".join(d.split())[:200]


def _parse_task_notification(txt):
    """Parse a <task-notification> block's fields, or None if it isn't one. Keys on the exact tags the
    harness emits (status / summary / output-file / tool-use-id), not a guess. tool_use_id is the join
    key back to the LAUNCHING tool_use — the standalone (string-content) notification shape carries it
    only inside the text, unlike the older tool_result wrapper whose block names it."""
    if not txt or "<task-notification>" not in txt:
        return None

    def fld(tag):
        a = txt.find("<" + tag + ">")
        if a < 0:
            return ""
        a += len(tag) + 2
        b = txt.find("</" + tag + ">", a)
        return txt[a:b].strip() if b >= 0 else ""
    st = fld("status")
    # has_status: whether <status> was PRESENT, distinct from the "completed" default. A Monitor's
    # per-EVENT notification carries no status tag (only its terminal one does) — without this bit the
    # default would let a wrapped event read as "completed" and end a watch that is still live.
    # result: an AGENT's closing message (the <result> body) — the report the parent's Agent tool head
    # shows once a background agent has come to rest (plans/subagent-transcripts.md); a command's
    # notification carries none. Capped like the chat's own tool output.
    return {"status": (st or "completed").lower(), "has_status": bool(st), "summary": fld("summary"),
            "output_file": fld("output-file"), "tool_use_id": fld("tool-use-id"),
            "result": fld("result")[:_RESULT_CAP]}


# A background agent's <result> text kept on its scan row (the want_all view) — the same 16000-char cap
# build_session puts on every tool's output, so the report fold and the scan row can never disagree.
_RESULT_CAP = 16000


# A non-persistent Monitor records its own lifetime ceiling at launch (timeout_ms — the harness kills
# it at the deadline), so a scan row still "running" past deadline+grace is a record whose terminal
# notification can never arrive (the CLI died with the monitor out, and the transcript never learns).
# Consumers apply this with THEIR now — baking it into the scan would freeze inside the mtime caches,
# which an idle transcript never busts. The grace absorbs kill/notify latency.
def _bg_expiry_t(task, grace=120.0):
    """The instant a scan row starts reading as expired: deadline + grace, or None for a row with no
    deadline (it never expires by the clock). One definition for the predicate below and for the judge
    gate's clock input (judge._settle_not_before), so the two cannot drift."""
    dl = (task or {}).get("deadline")
    if not dl:
        return None
    if (task or {}).get("deadlineSrc") == "hook":
        grace = 5.0   # a hook-recorded deadline is the harness's own kill moment (Monitor's
        #               required timeout_ms, journaled at launch) — exact, so only clock skew
    return dl + grace


def _bg_expired(task, now, grace=120.0):
    x = _bg_expiry_t(task, grace)
    return x is not None and now > x


# The detail an agent/workflow row expands to (the Agent prompt / the Workflow script) is clipped:
# the box's detail pre scrolls, but a workflow script can run to 512KB and the payload ships whole.
_DETAIL_CAP = 4000


def _clip_detail(text):
    text = str(text or "").strip()
    return text if len(text) <= _DETAIL_CAP else text[:_DETAIL_CAP] + "\n… (truncated)"


def _scan_bg_tasks(path, want_all=False):
    """Walk the transcript pairing async LAUNCHES with their <task-notification> results, and surface a task
    ONLY while it's still RUNNING (in flight across turns). A finished task drops out the instant its result
    lands. Newest-launched first, capped. No output content here (a running task's output grows independently
    of the transcript). Shared substrate: the kernel's chat box + awaiting sources read it through their
    mtime caches, and the judge's settled gate reads it as the DURABLE awaited-work source — the pairing
    lives in the transcript, so unlike any live snapshot it survives a kernel restart (2026-08-08).

    Launches come in THREE durable shapes: a Bash tool_use with run_in_background:true, a non-persistent
    Monitor tool_use (see below), and an async
    Agent/Workflow dispatch — its ack is a user record whose TOP-LEVEL toolUseResult says
    isAsync/"async_launched" (the tool_result block names the launching tool_use id). The ack names the
    work at best in one line (description / the workflow meta's summary), so the LAUNCHING tool_use is
    remembered too: its description is the gist when the ack has none, and its full ask — the Agent
    prompt / the Workflow script — rides `command`, the detail block the box already expands (the user
    2026-08-15, whose background agent expanded to a generic label with nothing inside). The ack's
    taskType rides `type`, so the scan rows carry the same agent-vs-shell fact the lifecycle set does.
    Results come in THREE shapes: the notification inside a tool_result block (the older wrapper), a
    standalone user record whose message.content IS the notification string (the current dominant shape —
    missing this left finished tasks reading 'running' forever), and a queue-operation enqueue holding
    the notification while the session is busy — the task itself is already finished the moment any of
    the three exists.
    Returns [{id,status,summary,command,outputFile}] (+ type on agent/workflow rows; + endT — the
    transcript time its result LANDED — on rows a notification has terminal-marked, so the awaiting-stamp
    lift can tell a return that ENDED a stamped wait from one the stamping judge had already seen)."""
    state = _bg_fresh(want_all)
    for o in _read_jsonl(path):
        if isinstance(o, dict):
            _bg_step(state, o)
    return _bg_finish(state, want_all)


def _bg_fresh(want_all=False):
    """A fresh pairing state. `all`: keep every task's row (the launch-ordered history view); otherwise a
    task that reaches a terminal status is dropped from the state at once — the running-only view can
    never show it again (no record the harness emits returns an existing task to "running"; a literal
    <status>running</status> notification after a terminal one is the one shape the old whole-file walk
    would have resurrected, and the fold refuses), so a long-lived
    transcript's fold state stays the size of its in-flight work, not its history. `done` is that view's
    tombstone set: transcripts DO replay records — a duplicate async ack (same tool-use id, even the same
    uuid) can land after the terminal notification — and with the row gone such a replay would pass the
    `tid not in tasks` creation guards and resurrect the task as running, where a retained row ignored it.
    The tombstone keeps that answer while releasing the row and its prompt text."""
    return {"tasks": {}, "order": [], "dispatch": {}, "all": bool(want_all), "done": set()}


def _bg_forget_terminal(state, tid):
    if not state["all"] and state["tasks"].get(tid, {}).get("status") != "running":
        state["done"].add(tid)
        state["tasks"].pop(tid, None)
        try:
            state["order"].remove(tid)
        except ValueError:
            pass


def _bg_step(state, o):
    """One transcript record of the pairing — see _scan_bg_tasks. Returns the state (the fold_records
    contract)."""
    tasks, order, dispatch, done = state["tasks"], state["order"], state["dispatch"], state["done"]

    def _mark(note, end_t=None):
        # a notification keyed by its INNER <tool-use-id> (the string/queue shapes have no wrapper block)
        tid = (note or {}).get("tool_use_id")
        if tid and tid in tasks:
            if tasks[tid].get("monitor") and not note.get("has_status"):
                return                     # a monitor EVENT (no <status> tag) — the watch is still live
            tasks[tid].update(status=note["status"], outputFile=note["output_file"],
                              summary=note["summary"] or tasks[tid]["summary"])
            if note.get("result"):         # an agent's closing message → the Agent head's report fold
                tasks[tid]["result"] = note["result"]
            if end_t:                      # WHEN the result landed — the awaiting-stamp lift keys the
                tasks[tid]["endT"] = end_t  # "returned after the stamp was written" test on it (2026-08-16)
            _bg_forget_terminal(state, tid)

    t = o.get("type")
    c = (o.get("message") or {}).get("content")
    if t == "assistant" and isinstance(c, list):
        for b in c:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            inp = b.get("input") or {}
            if b.get("name") in ("Agent", "Task", "Workflow") and b.get("id") \
                    and b["id"] not in tasks and b["id"] not in done:
                # remember the dispatch's own words — consumed by its async ack below, or
                # right here when an explicit run_in_background rides the input (the dominant
                # real Agent shape, which registers via the launch branch below instead). A
                # replayed launch record for a task already registered or already returned can
                # never be consumed (both creation paths refuse the id), so it is not captured.
                dispatch[b["id"]] = {
                    "desc": str(inp.get("description") or "").strip(),
                    "launchDesc": _launch_desc(b.get("name"), inp),
                    "detail": _clip_detail(inp.get("prompt") or inp.get("script")
                                           or ("script: " + str(inp["scriptPath"])
                                               if inp.get("scriptPath") else "")),
                    "type": "local_workflow" if b.get("name") == "Workflow" else "local_agent"}
            # The THIRD durable launch shape: a Monitor tool_use. A non-persistent monitor is
            # dispatched background work exactly like a backgrounded Bash — a session idle
            # behind one read as plain 'ready', its goal stamps could lift only by the 6h
            # backstop, and the nudge gates couldn't see the wait. A PERSISTENT monitor is
            # skipped: a session-length subscription (a log tail) never returns, so counting it
            # would hold "awaiting" forever — it is furniture, not awaited work.
            is_mon = b.get("name") == "Monitor"
            if is_mon and inp.get("persistent"):
                continue
            if not (inp.get("run_in_background") or is_mon):
                continue
            tid = b.get("id")
            if tid and tid not in tasks and tid not in done:
                tasks[tid] = {"id": tid, "status": "running", "t": parse_z(o.get("timestamp")),
                              "summary": (inp.get("description") or b.get("name") or "Background task"),
                              "launchDesc": _launch_desc(b.get("name"), inp),
                              "command": inp.get("command") or (inp.get("ws") or {}).get("url", ""),
                              "outputFile": ""}
                d = dispatch.pop(tid, None)
                if d:   # an Agent/Task/Workflow with an explicit run_in_background lands
                        # HERE, not at its ack (tid already registered) — same enrichment
                    tasks[tid]["command"] = tasks[tid]["command"] or d["detail"]
                    tasks[tid]["type"] = d["type"]
                if is_mon:
                    tasks[tid]["monitor"] = True
                    # its recorded lifetime ceiling → the deadline consumers expire on
                    # (see _bg_expired); the harness clamps timeout_ms to [1s, 30 min]: the
                    # tool's input schema still takes up to an hour, but the CLI caps any
                    # deadline above 30 minutes, so an hour-long ask is killed at 30 minutes
                    # (the SDK backend's launch ledger applies the same cap)
                    tmo = inp.get("timeout_ms")
                    tmo = float(tmo) if isinstance(tmo, (int, float)) else 300000.0
                    if tasks[tid]["t"]:
                        tasks[tid]["deadline"] = tasks[tid]["t"] + min(max(tmo, 1000.0), 1800000.0) / 1000.0
                order.append(tid)
    elif t == "user" and isinstance(c, list):
        tur = o.get("toolUseResult")
        tur = tur if isinstance(tur, dict) else {}
        async_launch = bool(tur.get("isAsync")) or tur.get("status") == "async_launched"
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if not async_launch:
                    # the tool_use's ONE result has landed synchronously — a remembered Agent/Task/Workflow
                    # dispatch can no longer be acked async, so its words are dead weight in the state
                    dispatch.pop(tid, None)
                if async_launch and tid and tid not in tasks and tid not in done:
                    # an async Agent/Workflow dispatch ack — the durable "this work is now
                    # running" record; the gist prefers the ack's own words, the launching
                    # tool_use fills what the ack omits (see docstring). Acks carry the ask
                    # too (prompt / scriptPath) — the fallback when the launch predates the
                    # transcript tail or the block went unseen.
                    d = dispatch.pop(tid, {})
                    wf = tur.get("workflowName")
                    tasks[tid] = {"id": tid, "status": "running", "t": parse_z(o.get("timestamp")),
                                  "summary": (tur.get("description") or tur.get("summary")
                                              or d.get("desc")
                                              or ("workflow " + str(wf) if wf else "Background agent")),
                                  "launchDesc": (d.get("launchDesc") or str(tur.get("description") or "").strip()
                                                 or ("workflow " + str(wf) if wf else "")),
                                  "command": d.get("detail")
                                             or _clip_detail(tur.get("prompt")
                                                             or ("script: " + str(tur["scriptPath"])
                                                                 if tur.get("scriptPath") else "")),
                                  "outputFile": tur.get("outputFile") or ""}
                    if tur.get("taskType") or d.get("type"):
                        tasks[tid]["type"] = tur.get("taskType") or d["type"]
                    if tur.get("agentId"):   # the ack names the agent whose own transcript this launch writes
                        tasks[tid]["agentId"] = str(tur["agentId"])   # (plans/subagent-transcripts.md)
                    order.append(tid)
                    continue
                if async_launch and tid in tasks and not b.get("is_error") \
                        and tasks[tid]["status"] == "running":
                    # the ack of a launch the assistant branch already registered (explicit
                    # run_in_background): the ack still owns outputFile/taskType — fill what
                    # the launch row lacks, never overwrite what it has
                    tk = tasks[tid]
                    tk["outputFile"] = tk["outputFile"] or tur.get("outputFile") or ""
                    if not tk.get("launchDesc") and tur.get("description"):
                        tk["launchDesc"] = str(tur["description"]).strip()[:200]
                    if tur.get("taskType"):
                        tk["type"] = tur["taskType"]
                    if tur.get("agentId") and not tk.get("agentId"):
                        tk["agentId"] = str(tur["agentId"])
                    if not tk["command"]:
                        tk["command"] = _clip_detail(tur.get("prompt") or "")
                    continue
                note = _parse_task_notification(_result_text(b.get("content")))
                if tid in tasks and note:      # its result landed → mark it done; the keep-filter drops it
                    if tasks[tid].get("monitor") and not note.get("has_status"):
                        continue               # a wrapped monitor EVENT — not a terminal (see _mark)
                    tasks[tid].update(status=note["status"], outputFile=note["output_file"],
                                      summary=note["summary"] or tasks[tid]["summary"])
                    if note.get("result"):
                        tasks[tid]["result"] = note["result"]
                    et = parse_z(o.get("timestamp"))
                    if et:                     # the return's moment (see _mark)
                        tasks[tid]["endT"] = et
                    _bg_forget_terminal(state, tid)
                elif tid in tasks and note is None and b.get("is_error") \
                        and tasks[tid]["status"] == "running":
                    # the LAUNCH's own ack errored (refused permission, bad input) → nothing ever
                    # started, and no notification will ever come. Without this, the phantom
                    # reads "running" forever and holds awaiting/nudge gates open on nothing.
                    tasks[tid]["status"] = "failed"
                    et = parse_z(o.get("timestamp"))
                    if et:
                        tasks[tid]["endT"] = et
                    _bg_forget_terminal(state, tid)
    elif t == "user" and isinstance(c, str):
        _mark(_parse_task_notification(c), parse_z(o.get("timestamp")))
    elif t == "queue-operation" and o.get("operation") == "enqueue":
        _mark(_parse_task_notification(o.get("content") or ""), parse_z(o.get("timestamp")))
    return state


def _bg_finish(state, want_all=False):
    """The scan's answer from a pairing state — row COPIES, so a holder of one answer is never changed by
    the next fold step (fold_records deep-copies the cached state before folding, and a caller scribbling
    on its rows must not reach the cache either)."""
    tasks, order = state["tasks"], state["order"]
    if want_all:
        # EVERY task the transcript knows, launch-ordered, each carrying its launch `t` and its CURRENT
        # status (still "running", or the terminal status its notification reported). The awaiting-stamp
        # lift (_lift_spent_awaiting) needs the RETURNED ones too: "this goal's dispatches have all come
        # back" is precisely the event that ends a wait, and the running-only view cannot express it.
        return [dict(tasks[tid]) for tid in order]
    keep = [dict(tasks[tid]) for tid in order if tasks[tid]["status"] == "running"]
    keep.reverse()
    return keep[:60]


def scan_bg_tasks_cached(path, cache, want_all=False, ckpt=None):
    """_scan_bg_tasks folded append-incrementally through `cache` (fold_records): a changed transcript
    steps only its appended records instead of re-pairing the whole file — the kernel's chat box, awaiting
    source and awaiting-stamp lift, and the judge's settled gate, all asked per push per session, and
    each re-walked a working session's entire transcript on every streamed record (measured live
    2026-09-02: four such readers over one 180 MB transcript held the push loop at a full core). `cache`
    is the caller's dict (the kernel keeps a running-only and an every-task cache; the judge its own), so
    the two views never invalidate each other and a test's `.clear()` still forces a re-fold."""
    return _bg_finish(_bg_fold(path, cache, want_all, ckpt), want_all)


def _bg_fold(path, cache, want_all, ckpt):
    """The pairing state of `path` folded through `cache`, in the view the caller asked for."""
    state = fold_records(cache, path, lambda: _bg_fresh(want_all), _bg_step, ckpt=ckpt)
    if state["all"] != bool(want_all):                    # the view is baked into the state at init: a cache handed to
        raise ValueError("bg fold cache for %s is shaped for want_all=%r, asked for %r"   # both views would answer
                         % (path, state["all"], bool(want_all)))                          # one of them wrong
    return state


def bg_task_reader(path, cache, ckpt=None):
    """ONE read of the every-task fold (scan_bg_tasks_cached's want_all view, the same `cache`), answered by launch
    id: returns row(tid), that task's row (a COPY, like _bg_finish's) or None when the transcript records no launch
    under the id. For a caller joining notifications to their launches: the state keys its rows by id already, and
    through scan_bg_tasks_cached each join copied every row and searched the copies, N rows N times over a
    transcript of N background tasks (lab 2026-11-12: 17 million row copies, 8 s, for 4,131 notifications). In that
    view a row is never dropped and an id is registered once, so the row under an id IS the first launch-ordered
    row carrying it, the one the search answered. The reader holds the state it was made from (the fold never
    changes a state it handed out), so it answers as of its read; a caller wanting newer records asks again."""
    tasks = _bg_fold(path, cache, True, ckpt)["tasks"]

    def row(tid):
        r = tasks.get(tid)
        return dict(r) if r is not None else None
    return row


# ───────────────────────── tail-first: the scope that cannot read whole history ─────────────────────────
# Tail-first pages (the user 2026-11-12, who wanted pages to stay responsive after a restart and approved the idea on one
# condition: no consistency problem, guaranteed by a mechanism rather than by care). A provisional chat page is built from the
# tail of a transcript, from a boundary record B that tail_window accepted, and it must never show anything the whole build
# would not. Three mechanisms carry that, and this is the second: while a thread is inside `tail_scope(win)`, every shared
# reader of the window's transcript REFUSES (WholeHistoryRead) before it looks at a memo, a cache or the disk, so no piece of
# the provisional build can be computed from bytes before B, or from a memo some earlier whole read filled, by accident. The
# window's own bytes were read by tail_window before the scope opened, into a list only the window holds, and the parse takes
# that list by identity (parse_session's tail road). A piece that needs whole history therefore cannot be wrong inside the
# scope: it raises, and its caller shows a placeholder until the whole parse lands (the kernel's _piece). Step 1 is the SHADOW:
# the provisional build is only compared with the whole one (the kernel's tail-first shadow) and reaches no browser.
class WholeHistoryRead(BaseException):
    """A read of a transcript's whole history (the file through a shared reader, a fold over it, its head record, a parse of
    it, or a memo any of those filled) attempted inside the tail-first scope that holds the file. Raised, never answered: a
    provisional build shows a placeholder for the piece that asked. A BaseException on purpose: the builders between a
    reader and the piece that asked are full of `except Exception` arms that answer a failed read with a default (an
    empty list, a count of zero), and inside a scope that default would be a guess shown as fact. Only the kernel's _piece
    and the shadow catch it, by name; it is raised nowhere outside a scope, and every raise is a reader's first statement,
    before the reader holds a claim or a lock."""


class TailRefused(Exception):
    """A tail-first window that cannot be proven equal to the whole build's: `reason` names the rule that refused it
    (tail_window's reasons, and the gates past it). Refusing costs the provisional page, never correctness."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _TailScopeLocal(threading.local):
    """The calling thread's tail-first scope. `win` is a class attribute, so a thread that never opened a scope reads None
    off the class: the guard every reader runs first is one attribute read (about 0.25 us against 1.3 us for getattr
    with a default on a plain threading.local)."""
    win = None


_TAIL_SCOPE = _TailScopeLocal()   # .win: the TailWindow the calling thread builds a provisional page from (tail_scope)


class tail_scope:
    """`with tail_scope(win):` the calling thread reads `win`'s transcript (and the session's other transcript files the
    window names) only through the window's own records until the block ends. Nested scopes stack; None clears it. A window
    tail_open did not hand out is refused on entry (TailRefused "unopened"): the gates that entry point runs (an armed
    rollback, another file of the conversation written inside the window, the stamps written before the bytes read)
    cannot be skipped by building from tail_window's answer directly."""

    def __init__(self, win):
        self.win = win

    def __enter__(self):
        if self.win is not None and not self.win.opened:
            raise TailRefused("unopened")
        self.prev = _TAIL_SCOPE.win
        _TAIL_SCOPE.win = self.win
        return self

    def __exit__(self, *exc):
        _TAIL_SCOPE.win = self.prev
        return False


def tail_scope_window():
    """The window of the calling thread's tail-first scope, or None outside one."""
    return _TAIL_SCOPE.win


def _tail_guard(path, what):
    """Refuse `what` (a reader's name) over `path` when the calling thread's tail-first scope holds that file. The first
    statement of every transcript reader below, ahead of its memo and cache lookups, so a hit a whole read left behind is
    refused the same as a fresh read; outside a scope it is one thread-local read."""
    win = _TAIL_SCOPE.win
    if win is not None and win.holds(path):
        raise WholeHistoryRead("%s inside the tail-first scope" % what)


def _read_jsonl(path):
    """The parsed json objects of a .jsonl file, as an iterator; nothing on any error. The tail-first guard runs at the
    CALL, before an iterator exists: a generator's body, the guard with it, would wait for the first next(), so a caller
    that took the iterator inside a scope and drained it outside would have read the whole file."""
    _tail_guard(path, "_read_jsonl")
    return _read_jsonl_iter(path)


def _read_jsonl_iter(path):
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


# Append-incremental transcript reads (the user 2026-07-05: the dashboard re-parsed a 40MB streaming
# transcript FROM BYTE ZERO on every push — ~0.6s of json.loads per append, saturating the push loop and
# queueing every click behind it). Transcripts are append-only, so cache each file's parsed records with
# the byte offset of the last COMPLETE line: a grown file loads only the appended bytes. Guards, in order:
#  - same (mtime, size)   → serve the cached records as-is (the common no-change poll);
#  - grew                 → verify the cached tail bytes still sit before the cached offset (a REWRITE that
#                           happens to be larger would otherwise serve a corrupt splice), then parse just
#                           the new bytes. Mismatch → full re-read;
#  - same size under a new mtime, same inode, a WHOLE entry → checked, not re-read (2026-10-23): a writer that reopens
#                           its file and writes nothing (a relaunched CLI, a touch) moves the time alone, and a whole re-read
#                           of a 464 MB transcript for it (a fresh generation, so every fold and the assembly entry started
#                           over) took 46 s of a jobs pass. The tail guard first (a cheap reject), then the crc32 of every
#                           byte the entry holds, read raw with no decode, against the one the entry kept: both standing
#                           serve the entry under its generation (the bytes past the offset scanned, as for a growth);
#                           either differing → full re-read, so an in-place edit of the same length is still read. A NEW
#                           inode at the same size is a replacement (a rename over the path), and a TAIL entry's prefix was
#                           never read in this process (nothing proves it, as the checkpoint restore refuses the same stat):
#                           both read whole;
#  - shrank → full re-read (a rewrite, not an append).
# A trailing line with no "\n" yet (a writer caught mid-append) is NOT consumed — the offset stays before
# it, so the next read picks the completed line up. Records are treated as IMMUTABLE by every consumer
# (FileAdapter builds fresh atom dicts; nothing writes into a record), matching the kernel's existing
# whole-parse cache contract. The cached list itself is never extended in place — a grown file stores a
# NEW list — so a concurrent reader holding the old list is never surprised mid-iteration.
_JSONL_CACHE = {}                 # path -> (mtime, size, offset, tail_bytes, records, base, gen, offsets, inode, crc_start, crc);
#                                   dict order = LRU, hits reinsert
#                                   offsets: array of (byte offset, byte length) pairs, one per held record (T323 stage 4)
#                                   inode, crc_start, crc (2026-10-23): the file's st_ino at the read and, for a WHOLE entry, the
#                                   zlib.crc32 of the bytes [0, offset) its records came from (crc_start 0; None for a tail entry,
#                                   whose prefix this process never read), so a same-size file under a new mtime is proved byte
#                                   for byte without a decode; an entry without them reads it whole
#                                   base: how many records of the file precede records[0] (0 = the whole file is held; > 0 = a
#                                   TAIL entry restored from a checkpoint, T323 stage 3); gen: a process-wide counter's value,
#                                   fresh for every from-zero read and every restored entry (a mismatch, an eviction, a first
#                                   read), kept across an upgrade from a tail entry to a whole one and across appends, so a
#                                   fold cursor keyed on it survives those and nothing else
_JSONL_CACHE_MAX = 1024           # bounds MEMORY only (384 → 1024 on 2026-09-03: the per-session states,
                                  # captions and the postal/nudge logs became tenants — a few KB each — and
                                  # must never evict a live transcript's slot) — past the cap, evict the least-recently-USED entry, one per
                                  # insert, never clear(). The old clear-at-cap was sized to the session count, but
                                  # the working set is FILES, not sessions (every subagent writes its own transcript):
                                  # once more distinct files than slots passed through one push cycle, the clear
                                  # nuked the HOT entries too and every push re-parsed every active transcript from
                                  # byte zero — the exact stall this cache exists to prevent, back as a permanent
                                  # background burn (recurred 2026-08-15, kernel pinned at ~30-60% CPU; the survival
                                  # guarantee is pinned by tests/test_kernel_jsonl_cache.py). 256 -> 384 when the
                                  # states/postal logs became tenants too (2026-09-01): one states file per session
                                  # plus messages.jsonl now share the slots, and an evicted LEAF slot also demotes
                                  # the assembly cache's identity gate to a full parse.
# A BYTE budget beside the count (the kernel memory work, 2026-09-11): the count bounded slots, never memory, and the
# working set is files of every size (a large leaf and a small states log take one slot each), so the kernel's resident
# size climbed between restarts, holding every live and subagent transcript's records. Each entry weighs the bytes it
# holds (the file size less a tail entry's offset), and the budget counts those bytes DECODED, times
# RECORD_CACHE_DECODED_MULTIPLE (2026-10-01: weighed in file bytes, half of a 32 GiB machine meant about 46 GiB of
# records, so it never bound, while the kernel grew into swap); past the budget the least
# recently used entries go first, one at a time, under the same LRU order the count uses, so a hot leaf survives a
# cold flood of subagent files exactly as before. A single entry larger than the whole budget still inserts: a leaf is
# never refused, the budget then holds that one entry. Counters under /perf recordCache, where `bytes` stays file
# bytes and `realBytes` is the figure the budget compares.
# A budget eviction also releases every assembly entry that read the evicted file (_asm_drop_holding, the owner's call,
# 2026-10-02): an entry's adapter keeps the very list it read (FileAdapter._src), so an eviction behind a chat's entry
# freed 0.4% of the leaf's memory (review probe, 2026-10-01) and the budget bounded only this cache's books. Released
# with it, the records' lists go; an idle session's tree in the judges' parse store shares their strings, so the text
# stays until the store replaces or drops that slot and a shown tab's build memos of the tree go, at that tab's next
# build (its goal-tree memo at the next build that walks its goals) or when the tab is no longer shown, and the
# live-merge memo (the kernel's _merge_sets_memo), which the chat, feed and timeline builds fill for any alive session,
# shown or not, keeps it until a build merges a live tail over a newer parse or, at the next push that serves a chat
# tab or the Outline pane, the session is neither shown nor alive (its 512-entry cap ages out the oldest first). Two
# windows keep records: a build that read the file before the
# eviction and installs after it keeps what it read, and a path evicted during its own read is left out of the release
# (_READ_IN_FLIGHT: an append read re-inserts the records it began from; a read from zero or a tail's upgrade builds new
# ones). While the file is unchanged the display's next build is a parse-store hit that rebuilds nothing; a comment
# create rebuilds the entry once (_comment_cut_from_parse; for a session parsed whole, with no usable document, beside
# the tree the store keeps, a second copy until the budget evicts the file again or the old tree goes: the store
# replaces or drops its slot, and a shown tab's build memos of it go at that tab's next build (its goal-tree memo at
# the next build that walks its goals: a build that merges a live tail, runs while a rewind is armed or reads an empty
# goal store keeps it), or when the tab is no longer shown, and the live-merge memo as above; a session whose stored
# parse fell back installs nothing and its creates read the transcript, and one whose rebuild itself falls back after a
# release parses again at each create until its files change), as does the first build after an append (a restore from
# the document, or a whole parse), and near the limit busy sessions may rebuild repeatedly. Counted under
# recordCache.budgetReleased. LRU order does not follow need: a display walk the assembly entry serves by a stat check
# never touches this cache, and a file only the judges read goes cold between their passes. The default is HALF of the
# machine's memory PER PROCESS (a judges child, when on, computes its own) (the user's direction, 2026-09-11, to use the
# memory we have; kept, in real units, on 2026-10-01), never under 4 GiB of FILE bytes (2026-09-11, the day the budget
# shipped at 1 GiB of file bytes, about 2.9 GiB decoded): the working set of a devbox running many sessions is their
# live leaves, read by every build in every pusher cycle, and a budget below it does not save memory, it thrashes: a
# few leaves filled the 1 GiB, every build re-read whole transcripts, pusher cycles and chat builds slowed to a crawl,
# and glibc's arenas kept the churn, the process resident far over its 1 GiB cache.
# An LRU under reads that cycle through more than it holds misses on EVERY read; it does not degrade partway. So the
# floor is a working-set bound and stays in the working set's unit, file bytes (in memory, the floor times the
# multiple): wherever it binds (a machine under about 23 GiB, or one without /proc/meminfo, such as macOS) the cache
# still holds 4 GiB of file bytes, what the floor has held since 2026-09-11; a Linux machine between 8 and 23 GiB, which
# held half its memory in file bytes before 2026-10-01, now holds those 4 GiB. Half of a 32 GiB machine in real units
# binds at about 5.6 GiB of file bytes (16 / 2.85). A busy machine's cache reaches that only after a long uptime, and by
# then the process holds those records decoded beside everything else it keeps, near or past the point where the
# machine starts swapping: half of memory caps this cache only near swap on a busy machine, and a budget tight enough
# to bind before swap sits at the working set, the thrash above. Once it
# binds it evicts the least recently used entries, cold ones first, which is safe while the files read every cycle (live
# leaves and their lineage) stay under the binding point; past it the thrash returns, and budgetRereads (an evicted path
# read back) shows it on /perf. What is not needed until looked at (subagent transcripts) leaves through
# drop_after="quiescent" folds instead; the budget is the backstop, not the mechanism. ROMP_RECORD_CACHE_BUDGET_MB still
# sets it outright, in megabytes of memory, floor or not.
RECORD_CACHE_BUDGET_FLOOR_BYTES = 4 * 1024 ** 3   # FILE bytes: the default budget is never under this times the multiple
RECORD_CACHE_BUDGET_FRACTION = 0.5
RECORD_CACHE_DECODED_MULTIPLE = 2.85   # bytes of memory per file byte a held entry takes decoded: a 37.7 MB transcript in
#                                        a lab process decoded to 2.86 live heap bytes per source byte (_scan_jsonl_stream's
#                                        docstring). A running kernel's resident size, fitted against the cache's file
#                                        bytes, gives a slope near this figure; that slope also carries whatever grew
#                                        beside the cache. One figure for every entry: it fits the
#                                        bulk, whole live leaves; content moves it, 1.44 to 7.7 by shape as measured (long
#                                        text rows low, small metadata rows high; tests/test_record_cache_real_budget.py
#                                        pins two mixes). ROMP_RECORD_CACHE_DECODED_MULTIPLE overrides it, over 0 and at
#                                        most 1000; 1 restores the file-byte budget.


def _record_cache_decoded_multiple(value=None):
    """The decoded multiple in force: `value` (ROMP_RECORD_CACHE_DECODED_MULTIPLE's text) when it is a number over 0 and
    at most 1000, else RECORD_CACHE_DECODED_MULTIPLE. The cap keeps every product finite (from about 4.2e298 the floor's
    int() raised OverflowError at import). A value set but refused is said on stderr, never dropped silently, and
    /perf recordCache.decodedMultiple reads the multiple in force either way."""
    if value is None or not str(value).strip():
        return RECORD_CACHE_DECODED_MULTIPLE
    try:
        m = float(value)
    except (TypeError, ValueError):
        m = float("nan")
    if 0 < m <= 1000:
        return m
    sys.stderr.write("romp: ROMP_RECORD_CACHE_DECODED_MULTIPLE=%r is not a number over 0 and at most 1000; the record "
                     "cache weighs entries at %s\n" % (value, RECORD_CACHE_DECODED_MULTIPLE))
    return RECORD_CACHE_DECODED_MULTIPLE


_RECORD_CACHE_MULTIPLE = _record_cache_decoded_multiple(os.environ.get("ROMP_RECORD_CACHE_DECODED_MULTIPLE"))


def _real_bytes(file_bytes) -> int:
    """`file_bytes` of held entries as memory, decoded: the unit of the budget and of /perf recordCache.realBytes."""
    return int(file_bytes * _RECORD_CACHE_MULTIPLE)


def _record_cache_default_budget_bytes(meminfo_text=None):
    """The default budget in memory units (the budget weighs held entries decoded): half of MemTotal (from /proc/meminfo,
    or the text given), never under the floor's 4 GiB of FILE bytes (RECORD_CACHE_BUDGET_FLOOR_BYTES times the multiple
    in force); the floor alone when the file is unreadable (macOS, a container without procfs), so such a machine holds
    the 4 GiB of file bytes it held before 2026-10-01."""
    floor = int(RECORD_CACHE_BUDGET_FLOOR_BYTES * _RECORD_CACHE_MULTIPLE)
    try:
        text = meminfo_text if meminfo_text is not None else open("/proc/meminfo", encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return max(floor, int(kb * 1024 * RECORD_CACHE_BUDGET_FRACTION))
    except Exception:
        pass
    return floor


_JSONL_CACHE_BUDGET_BYTES = (int(float(os.environ["ROMP_RECORD_CACHE_BUDGET_MB"]) * 1024 * 1024)
                             if os.environ.get("ROMP_RECORD_CACHE_BUDGET_MB") else _record_cache_default_budget_bytes())
_JSONL_CACHE_BYTES = [0]          # the sum of every held entry's weight, kept in step with _JSONL_CACHE under its lock
_RECORD_CACHE_STATS = {"inserts": 0, "evictions": 0, "evictedBytes": 0, "budgetEvictions": 0, "dropped": 0, "droppedBytes": 0,
                       "budgetRereads": 0, "budgetRereadBytes": 0,   # inserts of a path the byte budget had evicted, and the
                       #                   file bytes they read again (a tail restored first, then the whole read that
                       #                   upgrades it): the thrash signal (2026-10-01). Near zero while the budget evicts
                       #                   only cold entries; level with budgetEvictions once the files read every cycle
                       #                   outgrow it, every read then a miss
                       "budgetReleased": 0,   # assembly entries a budget eviction released (_asm_drop_holding): one evicted
                       #                   lineage file read by several chats releases each of them (review, 2026-10-02)
                       "released": 0,   # #1735: EVERY pop that removed an entry, whatever the cause (eviction, a re-read
                       #                   replacement, an OSError pop, a quiescent drop). A /perf STATISTIC only, never a
                       #                   gc-freeze reclaim trigger: these entries are decoded json, acyclic, freed by refcount
                       #                   whether frozen or not, so an unfreeze reclaim on a pop would collect nothing (the
                       #                   2026-09-21 review's correction)
                       "touched": 0,   # reads of a file that kept its size under a new mtime and whose held bytes stood: served
                       #                   from the entry, no decode (2026-10-23; a relaunched CLI moves its transcript's time alone)
                       "pinnedKept": 0,   # drops that kept an entry a whole assembly entry folds over (_drop_unless_pinned)
                       "droppedHeld": 0, "droppedHeldBytes": 0,   # the drops (of `dropped`) whose records an assembly entry folding
                       #                   over that generation still holds, and those records' bytes, held until its next visit:
                       #                   counted bytes gone, memory kept (_drop_unless_pinned_locked, 2026-10-01)
                       "wholeReads": {}}   # "kind<-caller" -> {"count", "bytes"}: every read that pulled a file WHOLE (from zero, or a
#                                          tail entry upgraded to the whole file), named by the reader's kind and the first frame
#                                          outside this module (T384: the way hydratedBy named the planner; the 0.8 GB of whole
#                                          reads of restored leaves the per-path bytes could not attribute). A restore's tail read
#                                          and an append are not whole reads and are not counted here.
_DROP_AFTER_QUIESCENT_S = float(os.environ.get("ROMP_RECORD_CACHE_DROP_QUIESCENT_S", "120"))   # a file this long unchanged
#                                   is one whose writer has finished (a subagent that returned): its records are not kept
_BUDGET_EVICTED = {}              # path -> True or "tail": the paths the byte budget evicted, oldest first, the last
#                                   _JSONL_CACHE_MAX of them; the first insert of one is a budget re-read (budgetRereads). A
#                                   whole insert takes it off; a tail insert (a checkpoint restore came back first) leaves it
#                                   as "tail" while that tail is held, so the whole read upgrading it adds its bytes to the
#                                   same re-read (review find, 2026-10-01: the judges' upgrade went uncounted). Keyed on the
#                                   eviction and the read back, the two events a thrash is made of, never on a time window. A
#                                   file shrunk, rewritten or replaced after its eviction counts as a re-read too (no writer
#                                   in kernel/ does that to a cache tenant)
_READ_IN_FLIGHT = set()           # paths whose read is under way, under _JSONL_CACHE_LOCK: marked at the read's hit
#                                   check and cleared at its insert and in a finally. A budget eviction leaves them out
#                                   of the paths it releases: an append read re-inserts the very records it began from,
#                                   so a release there freed nothing and cost the chat a rebuild (review find,
#                                   2026-10-02); a read from zero or a tail's upgrade builds new records, and the chat's
#                                   entry keeps the old ones until it is rebuilt


def _entry_weight(ent) -> int:
    """The bytes an entry holds, the unit recordCache.bytes counts and the byte budget weighs decoded: a whole entry (base 0) the
    file's size; a TAIL entry (base > 0, the records past a checkpoint's cut) the file's size less the offset its
    FIRST held record sits at, offs[0], and nothing while it holds no record (a checkpoint's bare cut before its
    first read, a tail of blank lines); a tail that holds records but carries no offsets to say where they start
    (a shape no current writer produces) is weighed as the whole file, over rather than under, because a bound
    that under-counts is no bound. The first version subtracted ent[2], which is the CONSUMED END offset, not
    the tail's start: after every newline-terminated read it stands at the file's size, so every tail entry
    weighed 0, the budget never saw the bytes a restored session's tail held nor their growth on append, and
    recordCache.bytes under-read the cache by exactly those tails (review find, 2026-09-15: a 1 MiB tail read
    through the reader reported weight 0 and cache bytes 0). A pure function of the tuple, so an insert and its
    pop subtract what they added."""
    try:
        size, base = int(ent[1]), int(ent[5])
        if base <= 0:
            return size
        offs = ent[7] if len(ent) > 7 else None
        if offs:
            return max(0, size - int(offs[0]))
        return size if (len(ent) > 4 and ent[4]) else 0
    except Exception:
        return 0


def _cache_pop_locked(path, keep_gen=None):
    """Under _JSONL_CACHE_LOCK: drop `path`'s entry and its weight; returns the weight (0 when absent). Every pop
    that actually removed an entry counts under `released`, a /perf STATISTIC only (#1735): these entries are
    decoded json, acyclic, freed by reference counting whether frozen or not, so a pop is never a gc-freeze reclaim
    trigger (the reclaim keys on the freeze controller's observed ended-session judgement and the backstop, not on `released`).
    The file's pending checkpoint restore goes with the entry it was taken for (_ckpt_release_pending), unless the caller
    puts back an entry of that same generation (`keep_gen`: an append, a tail's upgrade to the whole file)."""
    ent = _JSONL_CACHE.pop(path, None)
    if ent is None:
        return 0
    _RECORD_CACHE_STATS["released"] += 1
    gen = ent[6] if len(ent) > 6 else None
    if gen != keep_gen:
        _ckpt_release_pending(path, gen)
    w = _entry_weight(ent)
    _JSONL_CACHE_BYTES[0] = max(0, _JSONL_CACHE_BYTES[0] - w)
    return w


def _cache_insert_locked(path, ent, began=None):
    """Under _JSONL_CACHE_LOCK: insert `ent` at the LRU tail, evicting the least recently used entries past the count cap
    and past the byte budget (the new entry's own weight counted, the held file bytes weighed decoded; an entry larger
    than the budget alone still inserts). The first insert of a path the budget evicted counts under budgetRereads; when
    it comes back as a tail, the whole read that upgrades that tail adds its bytes to the same re-read. `began`: the
    (cached entry or None, budget mark) the read saw at its hit check; a path the budget evicted WHILE a read ran from its
    cached entry (another thread's insert, the reader holds no lock while it decodes) read nothing again for that
    eviction, so the read keeps the mark it began with (review find, 2026-10-02); a read that began uncached, a restore
    included, counts. Returns the paths the budget evicted, for _asm_drop_holding once the caller has let this lock go,
    less any whose own read is under way (_READ_IN_FLIGHT)."""
    prev = _JSONL_CACHE.get(path)
    _cache_pop_locked(path, keep_gen=ent[6] if len(ent) > 6 else None)   # the same generation back keeps its pending restore
    w = _entry_weight(ent)
    mark = _BUDGET_EVICTED.pop(path, None)
    if mark is True and began is not None and began[0] is not None:
        prev, mark = began
    if mark is not None:
        whole = not ent[5]
        if mark is True:                         # the first insert back after a budget eviction: one re-read
            _RECORD_CACHE_STATS["budgetRereads"] += 1; _RECORD_CACHE_STATS["budgetRereadBytes"] += w
        elif prev is None:                       # "tail", but that tail left by another road (the count cap, a drop)
            mark = None
        elif whole:                              # "tail": the whole read upgrading the tail it came back as
            _RECORD_CACHE_STATS["budgetRereadBytes"] += w
        if mark is not None and not whole:
            _BUDGET_EVICTED[path] = "tail"       # back as a tail, or that tail grown: a whole read may still follow
    while len(_JSONL_CACHE) >= _JSONL_CACHE_MAX:
        _RECORD_CACHE_STATS["evictions"] += 1
        _RECORD_CACHE_STATS["evictedBytes"] += _cache_pop_locked(next(iter(_JSONL_CACHE)))   # oldest-used first; hot entries survive any cold flood
    gone = []
    while _JSONL_CACHE and _real_bytes(_JSONL_CACHE_BYTES[0] + w) > _JSONL_CACHE_BUDGET_BYTES:
        old = next(iter(_JSONL_CACHE))
        _RECORD_CACHE_STATS["evictions"] += 1; _RECORD_CACHE_STATS["budgetEvictions"] += 1
        _RECORD_CACHE_STATS["evictedBytes"] += _cache_pop_locked(old)
        _BUDGET_EVICTED.pop(old, None); _BUDGET_EVICTED[old] = True   # newest: a fresh eviction is a fresh re-read
        if len(_BUDGET_EVICTED) > _JSONL_CACHE_MAX:
            del _BUDGET_EVICTED[next(iter(_BUDGET_EVICTED))]
        if old not in _READ_IN_FLIGHT:           # its own read is under way (an append re-inserts the entry's records)
            gone.append(old)
    _JSONL_CACHE[path] = ent
    _JSONL_CACHE_BYTES[0] += w
    _RECORD_CACHE_STATS["inserts"] += 1
    return gone


def record_cache_stats() -> dict:
    """The record cache for /perf: entries, held bytes (file bytes, as the kernel's samples read them), the same held
    bytes decoded (realBytes, the budget's unit) and the multiple, the budget, and the counters (inserts, evictions by
    count and by budget, evicted bytes, budget re-reads, drop-after-fold drops). What the entries weigh by class is
    record_cache_resident's (the heap block). Called by the pusher's gc-freeze tick every cycle and by the judge child's
    done line, so it stays O(1) beyond the wholeReads tables."""
    with _JSONL_CACHE_LOCK:
        out = {"entries": len(_JSONL_CACHE), "bytes": _JSONL_CACHE_BYTES[0], "realBytes": _real_bytes(_JSONL_CACHE_BYTES[0]),
               "decodedMultiple": _RECORD_CACHE_MULTIPLE, "budgetBytes": _JSONL_CACHE_BUDGET_BYTES,
               "countCap": _JSONL_CACHE_MAX, **_RECORD_CACHE_STATS}
        table = _RECORD_CACHE_STATS.get("wholeReads")
        out["wholeReads"] = {k: dict(v) for k, v in table.items()} if isinstance(table, dict) else {}
        bys = _RECORD_CACHE_STATS.get("wholeReadsByStage")             # T401: the same reads per (stage, caller)
        out["wholeReadsByStage"] = {k: dict(v) for k, v in bys.items()} if isinstance(bys, dict) else {}
    with _SWALLOWED_LOCK:
        out["swallowed"] = dict(_SWALLOWED)
    return out


_SWALLOWED = {}                   # "site:ExceptionType" -> raises a handler kept from its caller (note_swallowed)
_SWALLOWED_LOCK = threading.Lock()


def note_swallowed(site, exc, path):
    """A raise that a handler keeps from its caller (the returned-agent drop's witness check, the spend guard's agent folds;
    2026-10-02): counted under recordCache.swallowed on /perf by site and exception type, and said on stderr at the first,
    tenth, hundredth (each power of ten) of each, since these handlers run on every jobs pass. A raise that persists would
    otherwise return those agents to the old retention with no trace. The line names the file `path` and, since both
    handlers call this inside their except block, the frame that raised."""
    k = "%s:%s" % (site, type(exc).__name__)
    with _SWALLOWED_LOCK:
        n = _SWALLOWED[k] = _SWALLOWED.get(k, 0) + 1
    if n == 10 ** (len(str(n)) - 1):
        sys.stderr.write("record cache: %s raised %s (%d so far) on %s: %s\n%s"
                         % (site, type(exc).__name__, n, path, exc, traceback.format_exc()))


# What a held record weighs in memory against its bytes on disk (the kernel memory diagnosis, 2026-10-01): the lab's records-only
# measurement, 2.86 live-heap bytes per source byte on a real 37.7 MB transcript by tracemalloc (_scan_jsonl_stream's docstring),
# and 3.01 on a synthetic plain-ASCII transcript of 6,000 records, 5.57 MB (tracemalloc, Python 3.12, 2026-10-01); the default,
# RECORD_CACHE_DECODED_MULTIPLE (above), is 2.85, within 0.01 of the lab's, and a fit of the kernel's resident size against
# recordCache.bytes gives about the same slope, but that slope is the whole process's: it also takes in every holder that
# grows with the same transcripts (assembly entries, built atoms, the hydrated memo), which the heap block weighs beside this one,
# so the block's estimates are not meant to be summed. Content moves it: about 1.1 for one long plain-ASCII string, such as a
# large tool result, and about 4 to 5 for text with emoji or for small, metadata-heavy records (synthetic tests, 2026-10-01), so
# subagent files likely weigh more than this says. The logs class is mixed: rows shaped like the per-session states log weigh
# about 9 traced bytes per file byte, and body-heavy postal rows under 2 (a review's tracemalloc probe, 2026-10-02), so 2.85
# under-reads the first and over-reads the second. The heap block publishes it beside every estimate built on it. It is the
# record cache's multiple in force (_RECORD_CACHE_MULTIPLE above: RECORD_CACHE_DECODED_MULTIPLE, 2.85, unless
# ROMP_RECORD_CACHE_DECODED_MULTIPLE sets it), so the budget and these gauges weigh a file byte alike. The gauges read it
# when they run, as the budget does; RECORD_DECODED_MULTIPLE is the same figure under this name, taken at load, which only
# a test that rebinds _RECORD_CACHE_MULTIPLE can leave behind (review, 2026-10-03).
RECORD_DECODED_MULTIPLE = _RECORD_CACHE_MULTIPLE
RECORD_CACHE_CLASSES = ("wholeLeaf", "tail", "subagent", "logs")
_SUBAGENT_SEGMENT = os.sep + "subagents" + os.sep


def record_cache_resident(logs_root=None, transcript_roots=()) -> dict:
    """The record cache in memory units for /perf's heap block. The entries are copied under the cache's lock (tuples a read
    replaces, never changes in place), then weighed and classed after it, so a reader's hit never waits on the weighing.
    Each is classed by its path and base: `logs` (a file under `logs_root`, the state root the kernel passes: the states logs, the
    captions, the postal and nudge logs; never a file under one of `transcript_roots`, the Codex backend's session
    transcripts and the Claude projects root, which the kernel passes because either can sit under the state root, the
    first always and the second when the Claude config directory is placed there; their files are never logs and fall into
    the three classes below by the same rules as any other), `subagent` (a `subagents` path segment), `tail` (any other
    entry with base above zero: a leaf's records past a checkpoint's or an assembly document's cut), `wholeLeaf` (the rest:
    transcripts held from record 0). Per class `entries`, `fileBytes` (each entry's _entry_weight, the unit of
    recordCache.bytes, so the classes sum to it at the read) and `bytes`, the file bytes times the multiple in force
    (_RECORD_CACHE_MULTIPLE, read at the call), an estimate; the totals beside them. O(entries): a weight and a few prefix
    tests per entry, never a walk over records."""
    m = _RECORD_CACHE_MULTIPLE
    prefix = (str(logs_root).rstrip(os.sep) + os.sep) if logs_root else None
    leaves = tuple(str(r).rstrip(os.sep) + os.sep for r in transcript_roots or () if r)
    with _JSONL_CACHE_LOCK:
        held = list(_JSONL_CACHE.items())
    rows = [(p, _entry_weight(e), e[5]) for p, e in held]
    classes = {c: {"entries": 0, "fileBytes": 0, "bytes": 0} for c in RECORD_CACHE_CLASSES}
    for p, w, base in rows:
        p = str(p)
        log = prefix is not None and p.startswith(prefix) and not p.startswith(leaves)
        cls = ("logs" if log else "subagent" if _SUBAGENT_SEGMENT in p
               else "tail" if int(base or 0) > 0 else "wholeLeaf")
        classes[cls]["entries"] += 1
        classes[cls]["fileBytes"] += w
    for row in classes.values():
        row["bytes"] = int(round(row["fileBytes"] * m))
    return {"multiple": m, "entries": len(rows), "fileBytes": sum(r["fileBytes"] for r in classes.values()),
            "bytes": sum(r["bytes"] for r in classes.values()), "classes": classes}


_JSONL_TAIL_GUARD = 64            # bytes of pre-offset content re-verified before an incremental read
_JSONL_CACHE_LOCK = threading.Lock()   # the cache has cross-thread callers (the judge tiers' worker pools,
                                       # the pusher, the warm threads) and HITS mutate (LRU reinsert): the
                                       # lock covers only the cheap dict ops — the parse runs outside it —
                                       # and pops stay guarded so a lost race degrades to a re-parse, never
                                       # a raise


_READ_BYTES = {}                  # path -> bytes this process read from it through the reader (the exit-then-boot witness; /perf)
_READ_BYTES_LOCK = threading.Lock()


_READ_BYTES_TOTAL = [0]           # the reader's bytes off disk since the process began, one integer (T397: a stage mark)
_THREAD_BYTES = threading.local()  # the same, per THREAD (`read`, `hydrated`): the pusher's stage split reads its own thread's


def _count_read(path, n):
    with _READ_BYTES_LOCK:
        _READ_BYTES[path] = _READ_BYTES.get(path, 0) + int(n)
        _READ_BYTES_TOTAL[0] += int(n)
    _THREAD_BYTES.read = getattr(_THREAD_BYTES, "read", 0) + int(n)


def read_bytes_total():
    """What the reader pulled off disk since the process began, as one number (the per-path table is read_bytes_report)."""
    with _READ_BYTES_LOCK:
        return _READ_BYTES_TOTAL[0]


def thread_read_bytes():
    """What the reader pulled off disk on the CALLING thread since it began (T397 round one, low 2: a stage's bytes are the
    pusher's own, not the judges' first pass or a boot warm reading through the same window)."""
    return getattr(_THREAD_BYTES, "read", 0)


def thread_hydrated_bytes():
    """The assembly cut's hydrated bytes on the CALLING thread since it began (the process total is asmCheckpoint.hydratedBytes)."""
    return getattr(_THREAD_BYTES, "hydrated", 0)


def read_bytes_report():
    """{path: bytes read since this process began} plus "total": what the reader itself pulled off disk, file by
    file, so a test or /perf can say how much of a boot was tails and how much whole files."""
    with _READ_BYTES_LOCK:
        out = dict(_READ_BYTES)
        out["total"] = _READ_BYTES_TOTAL[0]              # the running total, kept for this report alone (T397 round two, low 1)
    return out


# ───────────────────────── fold checkpoints (T323 stage 3, 2026-09-11) ─────────────────────────
# A checkpoint is one small JSON file per folded JSONL file, under the state root's checkpoints/ directory, named by
# the sha1 of the file's realpath. It records the reader's PREFIX WITNESS (the byte offset past the last complete
# line, the up-to-64 bytes before it, the record count before it) and, for every fold_records fold whose cursor stood
# at that count, the fold's state (through _ckpt_encode: sets and tuples survive the JSON). A fresh process that folds
# the file restores the witness, verifies the guard bytes on disk and reads only offset..EOF; the folds resume from
# their recorded states over the tail. Anything that does not verify (version, path, a shrunk file, a rewrite under the
# guard, a same-size-new-mtime, a corrupt document) falls back to a whole read from zero, is counted per reason and
# says so on stderr. The directory is asked of a provider the kernel/judge install (it follows _rebind_state); with no
# provider, checkpoints are off and every fold behaves as before. Writes are event-keyed by the kernel (a session's
# settle, its states log moving, exit), never by a timer.
_CKPT_V = 1
_CKPT_DIR_FN = None               # () -> Path of the checkpoint directory; None = checkpoints off
_CKPT_STATS = {"restored": 0, "writes": 0, "swept": 0, "skippedFolds": 0, "fallbacks": {}, "restoredFolds": {}, "droppedRestores": 0,
               "docConsults": 0,      # fold documents loaded by the shared validated read (seeded: the key stands before the first load)
               "oversizeFolds": {}, "coldFolds": {}, "coldWrites": {},
               "refolds": {},         # per fold name: {"count", "bytes"} of whole refolds that READ (a fold with no cursor and nothing to
#                                       restore over a tail entry reads the file whole; T377 named the boot's whole reads this way)
               "converge": {"passes": 0, "writes": 0, "bytes": 0, "heals": 0, "healBytes": 0, "primed": 0, "deferred": 0,
                            "failed": 0, "unhealed": 0, "docReadBytes": 0, "quiescent": 0, "skipped": 0,
                            "dropWrites": 0, "dropDeferred": 0, "viaDrop": 0,   # T360, T361, T362
                            "dropHandedOff": 0,    # drops met off the document writer and handed to it (2026-10-08)
                            "handOffFailed": 0}}   # hand-offs whose write raised at the writer's turn, each its own (review of PR 155)
_CKPT_DOC_FOLDS = {}              # path -> {fold name: "state" | "over" | "cold" | "bare"}: the document on disk as last written or
#                                   loaded in this process, so the converge pass can tell a document lacking a state without a read
_COLD = object()                  # a restored cursor with no state (its fold was oversize): fold_records inits it and steps the tail
_COLD_FOLDS = set()               # (path, fold name) whose state in this process began cold at a cut: a TAIL-ONLY state, written as a
_COLD_OVER_KB = {}                # (path, fold name) -> the KB an over-the-cap state measured, carried through a cold write (T359)
_COLD_REASONS = {}                # (path, fold name) -> why it restarts cold: "over" (its state was over the cap: by design, every
#                                   boot) or "cold" (its document carried a cursor without a state: a tail-only state written by
#                                   a process where it began cold, or an older kernel's entry; the settle's primer heals it once)
                                  #  cursor without state until a whole refold, never as a complete one (review find, 2026-09-11)
_SAID = set()


def _say_once(line):
    """One stderr line per distinct text for the process (a skip that recurs at every settle is said the first time)."""
    if line in _SAID:
        return
    _SAID.add(line)
    try:
        sys.stderr.write(line + "\n")
    except Exception:
        pass


def _machine_memory_bytes(meminfo_text=None):
    """MemTotal from /proc/meminfo (or the text given) in bytes; 0 when unreadable (macOS, a container without procfs).
    The ceilings below are fractions of it (the user's direction, 2026-09-11: romp uses the machine's memory; every cache
    is one shared pool sized to the machine, never a small literal that sits below the working set and thrashes)."""
    try:
        text = meminfo_text if meminfo_text is not None else open("/proc/meminfo", encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _env_or(name, default, scale=1):
    """An integer knob from the environment (scaled), else the derived default."""
    try:
        v = os.environ.get(name)
        return int(float(v) * scale) if v else default
    except (TypeError, ValueError):
        return default


# 8 MiB, was 64 KB (2026-09-11, the day the checkpoints shipped): every leaf whose bgAll state sat over 64 KB was
# cold-refolded whole at every boot, each one read whole in the first pusher cycle; a document of a few MB reads in
# milliseconds. ROMP_CKPT_FOLD_CAP_KB sets it outright.
_CKPT_FOLD_CAP = _env_or("ROMP_CKPT_FOLD_CAP_KB", 8 * 1024 * 1024, 1024)
#                                   bytes of encoded state a fold may put in a checkpoint. A state that grows with its file (the postal
#                                   log fold's map of every sent row, the state intervals' list of every transition, the every-task
#                                   background view on a long transcript) would make the document a second copy of the file: reading
#                                   it at boot costs what the checkpoint exists to save (measured 2026-09-11: 11 MB of documents in a
#                                   44 MB world). Such a fold is left out, counted per name, and cold-folds at first touch; the folds
#                                   whose state is bounded (the gist's capped steps, the running tasks, the newest rows) are the ones
#                                   the checkpoint pays for. A bounded projection for the growing ones is a later stage's item.
_CKPT_CYCLE_CAP_DEFAULT = _env_or("ROMP_CKPT_CONVERGE_MB", 8 * 1024 * 1024, 1024 * 1024)   # the pusher cycle's byte budget for
#                                   checkpoint work (documents written and read, leaves read for a heal), shared by the converge pass
#                                   and the quiescence drop's write (T362); the kernel's knobs read these defaults, so they reach
#                                   the drop write before the first cycle begins (round two, low 3)
try:
    _CKPT_CONVERGE_MS_DEFAULT = float(os.environ.get("ROMP_CKPT_CONVERGE_MS", "150"))   # the pass's wall budget; 0 turns the pass
except ValueError:                                                                        #  off and the drop write with it
    _CKPT_CONVERGE_MS_DEFAULT = 150.0


def _ckpt_cycle_default_cap():
    """The cycle budget that stands before any pusher cycle has begun, and in a process that never begins one (the judges):
    the byte knob, or 0 (the drop write off) when either knob is zero."""
    return _CKPT_CYCLE_CAP_DEFAULT if _CKPT_CONVERGE_MS_DEFAULT > 0 and _CKPT_CYCLE_CAP_DEFAULT > 0 else 0


_CKPT_CYCLE = {"cap": _ckpt_cycle_default_cap(), "spent": 0}   # the cycle in progress: the kernel begins one at each cycle of its
#                                   document writer's thread (the review of PR 155; the jobs pass's write phase on 2026-10-08, the
#                                   pusher's cycle start before); before
#                                   any cycle the default above stands; a cap of 0 turns the drop write off (the drop pops as
#                                   before T362, unless a whole assembly entry pins it: recordCache.pinnedKept)
_DROP_OWED = {}                   # path -> when its quiescence drop was deferred for the cycle's budget (T362): paid at the next
#                                   cycle's start with the room it has, oldest first, or by the next fold over the file
_DROP_OWED_MAX = 4096             # over it the OLDEST owed entry is popped unwritten (its memory released), never the table
#                                   cleared; one a whole assembly entry pins stays, no longer owed, until the file's next quiet
#                                   drop retries it
# THE DOCUMENT WRITER (2026-10-08). A fold document or an assembly document is built and written on one thread while the kernel
# runs: its document writer thread (the review of PR 155; the jobs thread on 2026-10-08, where one big document held the jobs
# pass for its whole write), which holds document_writer for its loop's life. A drop that needs a write on any other thread (a
# build on the pusher, a request handler, a judge tier, the jobs pass) and a parse's rewrite after a refused document are
# HANDED to that thread (_DROP_HANDOFF, _ASM_REFUSAL_HANDOFF) and done at its next cycle exactly as the thread that met them
# would have done them, so neither a frame nor a decision waits on a document: an assembly document for a 4 MB session took
# 0.6 to 2.5 s to write on a live box, and the converge pass wrote them inside the pusher's cycle (9.9 s of one cycle over a
# 38 MB idle session in a lab). With no writer held (the judges child, a test driving folds, the kernel's boot before its
# loops start) every thread writes its own, as before. _DOC_STOP is the kernel's exit stand-down: past it no thread starts a
# document write at a drop or a hand-off (the exit writes its own checkpoints, and a write begun beside it competes with it for the interpreter).
_DOC_WRITER = {"thread": None}    # the ident of the thread holding document_writer, or None
_DOC_STOP = [None]                # a predicate (set_document_stop): True once the process is leaving
_DOC_PROGRESS = [None]            # the kernel's tracker of the writer's writes (set_document_progress): begin/end around each one
_DROP_HANDOFF = {}                # path -> (the reader entry's generation, pop): a drop that needs a write, met off the writer,
#                                   oldest first; paid by the writer over the entry of that generation (documents_pay_handoffs)
_ASM_REFUSAL_HANDOFF = {}         # leaf realpath -> (leaf_path, rompuuid, sdk_human, tree, why, refused cut offset, the whole entry the
#                                   parse installed, the reader entries its records came from): a parse's rewrite after the chain proof
#                                   refused the standing document, met off the writer (parse_session). The entry rides with it (the
#                                   review of PR 155): the writer writes from it when the assembly cache evicted it before the writer's
#                                   turn, so the cache's churn never turns the rewrite into nothing, and the reader entries ride beside
#                                   it for their record offsets, which nothing keeps resident once the entry is evicted (review of PR
#                                   158); bounded like the cache (_ASM_CACHE_MAX), since each one holds a whole entry
_HANDOFF_FAULTS = {}              # (hand-off kind, key) -> the kind of the fault its write raised, while its hand-offs keep failing: one
#                                   line per episode (_handoff_fault), the episode ending when a hand-off of that kind and key pays
_DROP_HOLD = threading.local()    # the converge pass holds this thread's quiescence drops while it heals and primes a leaf, then pays
#                                   them once (T362 follow-up review, low 2): {path: pop} of the drops held, or absent
_CKPT_LOCK = threading.Lock()
_CKPT_PENDING = {}                # path -> {"count": N, "gen": g, "folds": {name: {"count", "state"}}} restores not yet taken,
#                                   for the reader entry of generation g alone and released with it (_ckpt_release_pending)
_CKPT_SEQ = {}                    # path -> the seq of the last checkpoint read or written for it
_FOLD_DIRTY = set()               # paths whose fold cursors moved since their checkpoint was last written
_FOLD_REG = {}                    # checkpoint name -> the fold's cursor dict (fold_records registers at first call)
_GEN = [0]                        # the reader's generation counter: every from-zero read and every restored entry takes the
#                                   next value, process-wide, so no two entries of a path ever share one (a cursor left by an
#                                   entry the cache evicted or a stat failure popped must never match a later read's, since
#                                   the later read may be of a rewritten file with as many records; review find, 2026-09-11)


def _next_gen():
    with _JSONL_CACHE_LOCK:
        _GEN[0] += 1
        return _GEN[0]
_FOLD_NAME_OF = {}                # id(cursor dict) -> checkpoint name, for callers that name their cache once (name_fold_cache)


class document_writer:
    """`with document_writer():` makes this thread the process's document writer for the block (the kernel's document writer
    loop, for its life): from then on a drop that needs a write on any other thread, and a parse's rewrite after a refused
    document, are handed to it (see _DOC_WRITER above). The previous holder, None outside the block, is put back on the way out, so a test
    that runs the loop and stops it leaves the process as it found it."""

    def __enter__(self):
        self._prev = _DOC_WRITER["thread"]
        _DOC_WRITER["thread"] = threading.get_ident()
        return self

    def __exit__(self, exc_type, exc, tb):
        _DOC_WRITER["thread"] = self._prev
        return False


def set_document_stop(pred):
    """Kernel wiring (once, at its import): `pred()` is True once the process is leaving (the exit's stand-down); from then no
    thread starts a document write at a drop or a hand-off. None removes it."""
    _DOC_STOP[0] = pred


def set_document_progress(tracker):
    """Kernel wiring (once, at its import): `tracker.begin(kind, path, nbytes)` and `tracker.end()` are called around every
    write the writer's turn makes here (a handed drop's fold document, a handed rewrite's assembly document, an owed drop),
    so the kernel can say when its document writer has spent longer on one write than its own history gives (review of
    PR 158). None removes it."""
    _DOC_PROGRESS[0] = tracker


class _doc_progress:
    """`with _doc_progress(kind, path, nbytes):` the write's progress events to the kernel's tracker, when one is wired; the
    tracker records them on the document writer's own thread alone, and a tracker that raises never fails the write."""

    def __init__(self, kind, path, nbytes):
        self.args, self.t, self.wrote = (kind, str(path), int(nbytes or 0)), _DOC_PROGRESS[0], None

    def __enter__(self):
        if self.t is not None:
            try:
                self.t.begin(*self.args)
            except Exception:
                self.t = None
        return self

    def __exit__(self, exc_type, *exc):
        if self.t is not None:
            try:
                self.t.end(wrote=(exc_type is None and self.wrote is not False))   # a write that wrote nothing teaches nothing
            except Exception:
                pass
        return False


def _file_size(path):
    try:
        return os.path.getsize(str(path))
    except OSError:
        return 0


def documents_write_here():
    """Whether this thread may start a document write now: not once the process is leaving (_DOC_STOP), and only on the
    document writer's thread while one is held (document_writer); with none held, any thread. A stop predicate that raises
    reads as not leaving: the predicate is the kernel's event, and a fault in reading it must not silence every write."""
    stop = _DOC_STOP[0]
    if stop is not None:
        try:
            if stop():
                return False
        except Exception:
            pass
    w = _DOC_WRITER["thread"]
    return w is None or w == threading.get_ident()


def _hand_off_drop(key, ent, pop):
    """A drop that needs a write, met off the document writer: handed to it with the entry's generation and the drop's `pop`
    (True, False, or "witness"), newest last; the entry stays meanwhile, as a deferred drop's does. A second hand-off of the same
    entry before the writer's turn keeps the strongest pop (a stepping fold's True over a witness or a write-only drop); one of a
    newer entry replaces it. Over _DROP_OWED_MAX the oldest hand-off is let go unwritten: its entry stays until its next quiet
    fold hands it again or the reader's bounds evict it. Counted under checkpoints.converge.dropHandedOff."""
    gen = ent[6]
    with _CKPT_LOCK:
        prev = _DROP_HANDOFF.pop(key, None)
        if prev is not None and prev[0] == gen and pop is not True:
            pop = prev[1] if prev[1] is True else (pop or prev[1])
        _DROP_HANDOFF[key] = (gen, pop)
        while len(_DROP_HANDOFF) > _DROP_OWED_MAX:
            _DROP_HANDOFF.pop(next(iter(_DROP_HANDOFF)))
        _CKPT_STATS["converge"]["dropHandedOff"] = _CKPT_STATS["converge"].get("dropHandedOff", 0) + 1
    return False, "handed"


_REWRITE_HANDOFF = "rewrite after a refused document"   # a handed rewrite's kind, as _handoff_fault names it


def _handoff_fault(what, key, e):
    """One hand-off whose write raised at the writer's turn (documents_pay_handoffs): its own failure, never the turn's. Said
    once per episode (the first raise of a kind for `key`, until a hand-off of that key pays again), counted under
    checkpoints.converge.handOffFailed. Nothing is handed again: a drop's entry stays where the raise left it (before its
    pop) and its file's dirty mark stands, so the converge pass's own road or the file's next quiet fold writes it, as the
    raise on the drop's own thread would have left it; a rewrite comes back with the leaf's next proof and whole parse."""
    kind, slot = type(e).__name__, (what, key)        # a drop's episode and a rewrite's are apart, though both name the file
    with _CKPT_LOCK:
        _CKPT_STATS["converge"]["handOffFailed"] = _CKPT_STATS["converge"].get("handOffFailed", 0) + 1
        said = _HANDOFF_FAULTS.get(slot) == kind
        _HANDOFF_FAULTS.pop(slot, None)
        _HANDOFF_FAULTS[slot] = kind
        while len(_HANDOFF_FAULTS) > _DROP_OWED_MAX:
            _HANDOFF_FAULTS.pop(next(iter(_HANDOFF_FAULTS)))
    if not said:
        try:
            sys.stderr.write("checkpoint: the %s handed to the document writer for %s raised %r; skipped, the rest of its turn "
                             "done (said once until a hand-off of it pays; counted under checkpoints.converge.handOffFailed)\n"
                             % (what, key, e))
        except Exception:
            pass


def _handoff_paid(what, key):
    """A hand-off of kind `what` for `key` paid without raising: its fault episode, if one was open, ends (_handoff_fault)."""
    if (what, key) in _HANDOFF_FAULTS:
        with _CKPT_LOCK:
            _HANDOFF_FAULTS.pop((what, key), None)


def documents_pay_handoffs():
    """The document writer's turn (a cycle of the kernel's document writer thread): the drops other threads handed over are
    done now, oldest first, each as its thread would have done it (_write_then_drop over the entry of the generation it met, with its
    pop: a release with no age gate, a quiet drop, a witness drop), against the cycle's budget, so one that does not fit is
    deferred and owed as any drop over the budget is; a hand-off whose entry is gone or was replaced by a newer read is
    dropped (that entry's own folds decide its drop). Then the rewrites the parses handed over after a refused document
    (_asm_write_after_refusal, from the entry each parse installed). Past the stop, or called off the writer, nothing is done
    and what is left stays handed. Each hand-off is its own (the review of PR 155): both tables are taken whole first, so a
    write that raised took every drop after it and every rewrite with it; a raise is now that hand-off's failure, said once
    per episode (_handoff_fault), and the turn goes on to the next. Returns (drops paid, rewrites done)."""
    with _CKPT_LOCK:
        drops = list(_DROP_HANDOFF.items())
        _DROP_HANDOFF.clear()
    with _ASM_CKPT_LOCK:
        rewrites = list(_ASM_REFUSAL_HANDOFF.items())
        _ASM_REFUSAL_HANDOFF.clear()
    paid = done = 0
    for i, (key, (gen, pop)) in enumerate(drops):
        if not documents_write_here():
            with _CKPT_LOCK:                              # put back under any hand-off made since, which is newer
                for k, v in drops[i:]:
                    _DROP_HANDOFF.setdefault(k, v)
            break
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None or ent[6] != gen:
            continue
        try:
            with _doc_progress("fold", key, ent[1]) as _w:
                _w.wrote = bool(_write_then_drop(key, ent, pop=pop)[0])
        except Exception as e:                            # noqa: BLE001 — this drop's failure, never the turn's
            _handoff_fault("drop", key, e)
            continue
        _handoff_paid("drop", key)
        paid += 1
    for i, (rk, args) in enumerate(rewrites):
        if not documents_write_here():
            with _ASM_CKPT_LOCK:
                for k, v in rewrites[i:]:
                    _ASM_REFUSAL_HANDOFF.setdefault(k, v)
            break
        try:
            with _doc_progress("assembly", args[0], _file_size(args[0])) as _w:
                _w.wrote = bool(_asm_write_after_refusal(*args))
        except Exception as e:                            # noqa: BLE001 — as at the parse: the rewrite is not retried here
            _handoff_fault(_REWRITE_HANDOFF, rk, e)
            continue
        _handoff_paid(_REWRITE_HANDOFF, rk)
        done += 1
    return paid, done


def drop_cold_cursors(path):
    """Drop the cursors of the folds over `path` that began cold for want of a state (reason "cold", never "over"), so each
    is refolded whole at its next run and complete again (T359). Returns the names. For a leaf the kernel reruns its folds
    at the settle before the write; for another file the write leaves them out and the next boot reads the file whole once."""
    key = str(path)
    with _CKPT_LOCK:
        names = [n for k, n in _COLD_FOLDS if k == key and _COLD_REASONS.get((k, n), "cold") == "cold"]
        for n in names:                                   # the cold marks go with the cursor: the fold's next run is a whole refold,
            _COLD_FOLDS.discard((key, n)); _COLD_REASONS.pop((key, n), None); _COLD_OVER_KB.pop((key, n), None)   #  and until then
    for n in names:                                       #  the path is no candidate for it (review: an unhealable cold fold kept a
        cache = _FOLD_REG.get(n)                          #  document being written every cycle)
        if cache is not None:
            cache.pop(key, None)
    return names


def cold_fold_reasons(path):
    """{fold name: reason} for the folds over `path` that began cold in this process ("over": its state is over the cap, by
    design; "cold": its document carried a cursor without a state, which one whole refold heals). The settle's primer asks."""
    key = str(path)
    with _CKPT_LOCK:
        return {n: _COLD_REASONS.get((k, n), "cold") for k, n in _COLD_FOLDS if k == key}


def entry_whole_resident(path):
    """True when the reader holds `path`'s WHOLE file in memory (an entry read from byte zero): a fold over it costs
    no read. False for a tail entry or none."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(str(path))
    return ent is not None and ent[5] == 0


def fold_cursor_appendable(cache, path):
    """True when `cache` holds a cursor for `path` at the reader's current entry generation: a fold over it is a stat or
    an append over the entry's records in hand, never a whole read. False with no cursor, no entry or another generation."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(str(path))
    cur = cache.get(str(path))
    return ent is not None and cur is not None and cur[1] == ent[6]


def fold_steps_in_hand(cache, name, path, new=True):
    """Whether fold `name` (its cursor dict `cache`) over `path` would step only records the reader already holds, never a
    whole read, over an entry that holds records: the fold's cursor stands at that entry's generation inside its records (a
    hit, or an append), or the file's pending restore carries a complete state for the fold inside them (a tail restored from
    the document), or, with `new`, the entry is the whole file (a first fold over it reads nothing). False when the reader
    holds no entry for the path, or an empty one, and while a parse's assembly entry holds the entry's very records list
    (the subagent viewer's, _asm_holds_records), which a parse rebuilt since the file's last append does: a drop there
    frees nothing, and a stepping fold's drop would leave the viewer's next rebuild a whole read. The spend guard asks before it folds an agent file it priced (2026-10-01), so its
    fold never enters a file the reader let go, nor pulls a restored tail whole, nor drops records a viewer holds. A pending
    restore whose count for the fold does not read as an int is a corrupt document, counted and removed as the restore
    counts it (review round ten, 2026-10-04: a count json read as infinity raised out of the gate at every jobs pass, and
    the document stayed with nothing recorded), and a restored tail the reader holds for the path is let go with it, so
    the caller's next read takes the file whole from zero (review round eleven, 2026-10-04: the fallback removes every
    fold's restored state, and the guard asks once per fold, so the folds it asked after the corrupt one found a tail with
    no cursor and no state at that pass and at every later one, and a returned agent's tail stayed). A tail only, of the
    generation the gate looked at, whichever read stored it, an append's longer tail included (review round twelve,
    2026-10-05): a whole entry stays, for the assembly pin and the quiet drop to decide as before, since a pop of one a
    viewer's parse read freed nothing and left the viewer's next rebuild a whole read; and an entry of another generation,
    from a read the gate never looked at, stays. Such a tail has lost its saved restore and the document with the fallback,
    so the guard never lets it go. Its records stay until the count cap or the byte budget evicts it, or a reader that
    drops a returned subagent's records reads it whole (the card's own readers, or the guard's within ten minutes of the
    file's last write); a whole read by any other reader swaps the tail for the whole file, which then stays the same way
    (review rounds thirteen and fourteen, 2026-10-05: memory only; it takes a corrupt document and another read restoring
    the tail between the gate's look and the fallback)."""
    key = str(path)
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(key)
    if ent is None or not ent[4]:
        return False
    base, total = ent[5], ent[5] + len(ent[4])
    cur = cache.get(key)
    if cur is not None and cur[1] == ent[6]:
        ok = base <= cur[0] <= total
    elif new and base == 0:
        ok = True
    else:
        with _CKPT_LOCK:
            pend = _CKPT_PENDING.get(key)
            f = (pend.get("folds") or {}).get(name) if isinstance(pend, dict) and pend.get("gen") == ent[6] else None
        try:
            ok = isinstance(f, dict) and "state" in f and base <= int(f["count"]) <= total
        except (KeyError, TypeError, ValueError, OverflowError) as e:   # a count that is no int (OverflowError: one json read as
            _ckpt_fallback(key, "corrupt", "fold %s: %s" % (name, e))   #  infinity) is a corrupt document, counted and removed
            with _JSONL_CACHE_LOCK:                                     #  once, as the restore counts it; a TAIL of the generation
                c = _JSONL_CACHE.get(key)                               #  looked at goes with it, so the next read takes the file
                if c is not None and c[5] > 0 and c[6] == ent[6]:       #  whole and the folds start over it; a whole entry stays,
                    _cache_pop_locked(key)                              #  for the assembly pin and the quiet drop to decide
            return False
    return ok and not _asm_holds_records(key, ent[4])


def name_fold_cache(cache, name):
    """Give a fold's cursor dict its checkpoint name once, so every fold_records call over it is resumable without
    a `ckpt` argument at the call (a caller whose call shape other code stubs, such as the judge's background-task
    scan, keeps its signature)."""
    _FOLD_NAME_OF[id(cache)] = name
    _FOLD_REG[name] = cache


def set_checkpoint_dir(fn):
    """Kernel/judge wiring: `fn()` names the checkpoint directory (a Path) at CALL time, so a rebound state root
    moves it. None turns checkpoints off."""
    global _CKPT_DIR_FN
    _CKPT_DIR_FN = fn
    with _ASM_CKPT_LOCK:
        _ASM_CHAIN_REFUSED_PATHS.clear()                  # a rebind forgets a refusal recorded against another directory's document
        _ASM_REFUSAL_HANDOFF.clear()                      # and a rewrite handed to the writer for one
        _ASM_LAST_WRITE_CUT.clear()
        _ASM_DOC_MEMO.clear(); _ASM_DOC_MEMO_BYTES[0] = 0   # nor does a memoized assembly document, the seeded walk's or the restore's (round two)
        _ASM_REBUILD.clear(); _ASM_REBUILD_DECLINED.clear(); _ASM_REBUILD_BLIPPED.clear()   # nor a rebuild noted against another directory
        _ASM_REBUILD_RAISED.clear()
        _ASM_OVERSIZE.clear()                             # nor an oversize verdict read from another directory
    with _CKPT_LOCK:
        _CKPT_PENDING.clear(); _CKPT_SEQ.clear(); _FOLD_DIRTY.clear(); _CKPT_DOC_FOLDS.clear(); _DROP_OWED.clear()
        _DROP_HANDOFF.clear(); _HANDOFF_FAULTS.clear()
        _RETIRED_FOLDS.clear()                            # a retirement never outlives a state rebind (round two, low 2)
        _DOC_MEMO.clear(); _DOC_MEMO_BYTES[0] = 0         # nor does a memoized document (the harnesses' fresh process is this setter)
        _DOC_INDEX.clear()                                # nor what a write's carry knows of a document without reading it
        _CKPT_CYCLE["cap"] = _ckpt_cycle_default_cap(); _CKPT_CYCLE["spent"] = 0


def _ckpt_dir():
    fn = _CKPT_DIR_FN
    return None if fn is None else fn()


def _ckpt_file(path):
    d = _ckpt_dir()
    if d is None:
        return None
    return Path(d) / (hashlib.sha1(os.path.realpath(str(path)).encode("utf-8")).hexdigest()[:20] + ".json")


def _ckpt_fallback(path, reason, detail=""):
    """A checkpoint that did not verify: counted per reason, said once on stderr, and its file removed so the next
    write starts clean. The caller reads the whole file from zero."""
    with _CKPT_LOCK:
        _CKPT_STATS["fallbacks"][reason] = _CKPT_STATS["fallbacks"].get(reason, 0) + 1
        _CKPT_PENDING.pop(str(path), None)
        _CKPT_DOC_FOLDS.pop(str(path), None)              # the document is gone: the converge pass must not believe it whole
        _DOC_INDEX.pop(str(path), None); _doc_memo_drop(str(path))   # nor may a write's carry or a consult serve it from memory
    try:
        sys.stderr.write("checkpoint fallback (%s) for %s%s\n" % (reason, path, (": " + detail) if detail else ""))
    except Exception:
        pass
    cp = _ckpt_file(path)
    if cp is not None:
        try:
            cp.unlink()
        except OSError:
            pass


def _ckpt_encode(o):
    """The fold states are plain data (dict, list, set, tuple, str, int, float, bool, None); JSON keeps dicts with
    string keys and lists, so sets, tuples and non-string-keyed dicts are tagged. Raises TypeError on anything else,
    which the writer treats as a fold that cannot be checkpointed (counted, never a torn state)."""
    if o is None or isinstance(o, (bool, int, float, str)):
        return o
    if isinstance(o, list):
        return [_ckpt_encode(x) for x in o]
    if isinstance(o, tuple):
        return {"~tuple": [_ckpt_encode(x) for x in o]}
    if isinstance(o, (set, frozenset)):
        return {"~set": sorted((_ckpt_encode(x) for x in o), key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(o, dict):
        if all(isinstance(k, str) and not k.startswith("~") for k in o):
            return {k: _ckpt_encode(v) for k, v in o.items()}
        return {"~dict": [[_ckpt_encode(k), _ckpt_encode(v)] for k, v in o.items()]}
    raise TypeError("not checkpointable: %s" % type(o).__name__)


def _ckpt_decode(o):
    if isinstance(o, list):
        return [_ckpt_decode(x) for x in o]
    if isinstance(o, dict):
        if len(o) == 1:
            (k, v), = o.items()
            if k == "~tuple":
                return tuple(_ckpt_decode(x) for x in v)
            if k == "~set":
                return set(_ckpt_decode(x) for x in v)
            if k == "~dict":
                return {_ckpt_decode(a): _ckpt_decode(b) for a, b in v}
        return {k: _ckpt_decode(v) for k, v in o.items()}
    return o


def _ckpt_load(path):
    """The verified-by-shape checkpoint document for `path`, or None (absent, or a fallback was counted)."""
    cp = _ckpt_file(path)
    if cp is None or not cp.exists():
        return None
    try:
        text = cp.read_bytes()
        _count_read(str(cp), len(text))                   # the document is a read of the boot too (/perf, the bench)
        doc = json.loads(text.decode("utf-8"))
    except (OSError, ValueError) as e:
        _ckpt_fallback(path, "corrupt", str(e)[:80]); return None
    if not isinstance(doc, dict) or doc.get("v") != _CKPT_V:
        _ckpt_fallback(path, "version", repr(doc.get("v")) if isinstance(doc, dict) else "not a document"); return None
    if doc.get("path") != os.path.realpath(str(path)):
        _ckpt_fallback(path, "path", str(doc.get("path"))[:80]); return None
    try:
        offset, count, size = int(doc["offset"]), int(doc["count"]), int(doc["size"])
        int(doc.get("seq") or 0); float(doc.get("mtime") or 0)   # as the restore reads them: a bad one is corrupt here, not a raise there
        guard = bytes.fromhex(doc.get("guard") or "")
        folds = doc.get("folds") or {}
        if offset < 0 or count < 0 or len(guard) > _JSONL_TAIL_GUARD or not isinstance(folds, dict):
            raise ValueError("shape")
    except (KeyError, TypeError, ValueError, OverflowError) as e:   # OverflowError: a number json read as infinity, which raised out
        _ckpt_fallback(path, "corrupt", "shape: %s" % e); return None   #  of every process's restore before, never counted or removed
    return doc


def _ckpt_verdict(doc, st_size, st_mtime):
    """What the file's stat says about a checkpoint document before its guard is read: "shrunk" (the file ends before
    the recorded offset), "rewrite" (the same size under another mtime, or shorter than recorded while past the offset:
    a rewrite the guard could miss), or None (grown, or the very file the document describes: the guard decides). Both
    restore paths (the tail read and the whole-reader-first fold) ask this, so one rewrite gets one verdict and one
    fallback reason whichever reader came first."""
    offset, size, mtime = int(doc["offset"]), int(doc["size"]), float(doc.get("mtime") or 0)
    if st_size < offset:
        return "shrunk"
    if st_size < size or (st_size == size and st_mtime != mtime):
        return "rewrite"
    return None


def _checkpoint_entry(path, st):
    """A TAIL reader entry restored from `path`'s checkpoint, (mtime, size, offset, guard, [], count, 0), or None. The
    guard bytes are verified by the reader against the file; here the document and the size are: a file shorter than
    the recorded offset is a shrink."""
    doc = _ckpt_doc_shared(str(path))   # one read shared with the write's carry and a retirement's consult
    _CKPT_DOC_FOLDS[str(path)] = _doc_fold_shapes(doc.get("folds")) if isinstance(doc, dict) else {}   # what the disk holds (T360)
    if doc is None:
        return None
    offset, count = int(doc["offset"]), int(doc["count"])
    verdict = _ckpt_verdict(doc, st.st_size, st.st_mtime)
    if verdict is not None:
        _ckpt_fallback(path, verdict, "size %d, recorded %d at offset %d" % (st.st_size, int(doc["size"]), offset)); return None
    gen = _next_gen()
    with _CKPT_LOCK:
        _CKPT_PENDING[str(path)] = {"count": count, "gen": gen, "folds": dict(doc.get("folds") or {})}
        _CKPT_SEQ[str(path)] = int(doc.get("seq") or 0)
    return (float(doc.get("mtime") or 0), int(doc["size"]), offset, bytes.fromhex(doc.get("guard") or ""), [], count, gen)


def _ckpt_pending(path, ent):
    """The restores waiting for `path`'s folds, given the reader's current entry: the ones a tail restore left, or,
    when a whole reader read the file first (the entry holds every record and no restore happened), the checkpoint's
    fold states after its guard is verified on disk by one 64-byte read. None when there is nothing to resume."""
    key = str(path)
    with _CKPT_LOCK:
        pend = _CKPT_PENDING.get(key)
        if pend is not None and ent is not None and pend["gen"] < ent[6]:
            _CKPT_PENDING.pop(key, None)                  # taken for an entry that left memory by a road that did not release
            pend = None                                   #  it (a read that failed after the restore began): it serves no later
    #                                                       entry, and the document is not consulted again (_CKPT_SEQ below)
    if pend is not None or ent is None or _CKPT_DIR_FN is None:
        return pend                                       # a tail entry the assembly checkpoint's cut read created (T323
    #                                                       stage 4a) is consulted like a whole one: the fold's recorded
    #                                                       count must lie within the records the entry holds
    with _CKPT_LOCK:
        if key in _CKPT_SEQ:                          # already consulted (or written) in this process: nothing new
            return None
    doc = _ckpt_doc_shared(str(path))   # one read shared with the write's carry and a retirement's consult
    _CKPT_DOC_FOLDS[str(path)] = _doc_fold_shapes(doc.get("folds")) if isinstance(doc, dict) else {}   # what the disk holds (T360)
    if doc is None:
        with _CKPT_LOCK:
            _CKPT_SEQ.setdefault(key, 0)
        return None
    offset, count, guard = int(doc["offset"]), int(doc["count"]), bytes.fromhex(doc.get("guard") or "")
    verdict = _ckpt_verdict(doc, ent[1], ent[0])          # the same facts the tail path checks: size, mtime, then the guard
    if verdict is not None:
        _ckpt_fallback(path, verdict, "whole reader first"); return None
    ok = False
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, offset - len(guard)))
            ok = fh.read(len(guard)) == guard
        _count_read(key, len(guard))
    except OSError:
        ok = False
    if not ok:
        _ckpt_fallback(path, "guard", "whole reader first"); return None
    if count < ent[5] or count > ent[5] + len(ent[4]):
        with _CKPT_LOCK:
            _CKPT_SEQ[key] = int(doc.get("seq") or 0)
        return None                                       # the fold's count is outside the held records: a cold fold, no fallback
    pend = {"count": count, "gen": ent[6], "folds": dict(doc.get("folds") or {})}
    with _CKPT_LOCK:
        _CKPT_PENDING[key] = pend
        _CKPT_SEQ[key] = int(doc.get("seq") or 0)
        _CKPT_STATS["restored"] += 1
    return pend


def _ckpt_release_pending(path, gen):
    """Let `path`'s pending restore go with the reader entry of generation `gen` it was taken for (_cache_pop_locked). The
    restore serves that entry alone (_restored_cursor), yet it carries a state for every fold in the document, folds this
    process never runs over the file included (the judges' fold, in the kernel). Kept after its entry left, it held those
    states until the process exited, outside every cache bound, and a whole read of the file afterward met it as a dropped
    restore. Runs under _JSONL_CACHE_LOCK, so without _CKPT_LOCK (the module never nests the two): a get and a pop, where a
    lost race costs a fold one refold, the module's rule for its shared dicts."""
    pend = _CKPT_PENDING.get(path)
    if pend is not None and pend.get("gen") == gen:
        _CKPT_PENDING.pop(path, None)


def _document_carries_state(key, name, ent):
    """Whether the document's pending restore for `key` carries a complete state for fold `name` (a cursor with `state`), as
    a stale-generation fold asks before taking it over a state it holds (T362 round one, medium). Consults the same pending
    record the restore would, taking nothing."""
    pend = _ckpt_pending(key, ent)
    f = pend["folds"].get(name) if pend is not None else None
    return isinstance(f, dict) and "state" in f


def _restored_cursor(key, name, ent):
    """The fold cursor (count, gen, state) a checkpoint carries for fold `name` of `key`, when it stands at a count
    the current entry can resume from; None otherwise. Each fold's restore is taken once."""
    pend = _ckpt_pending(key, ent)
    if pend is None:
        return None
    f = pend["folds"].pop(name, None)
    if not isinstance(f, dict):
        return None
    base = ent[5]
    try:
        count = int(f["count"])
        if pend["gen"] != ent[6]:                          # the entry is not the one the restore was taken for: another
            with _CKPT_LOCK:                              # read replaced it (the stripe lock makes this a residual)
                _CKPT_STATS["droppedRestores"] += 1
            try:
                sys.stderr.write("checkpoint restore dropped for fold %s of %s: the reader's entry moved under it\n" % (name, key))
            except Exception:
                pass
            return None
        if count < base or count > base + len(ent[4]):
            return None                                   # a fold's count may differ from the document's cut (T359: the cut is the
        if "state" not in f:                              #  lowest written cursor): inside the entry's held records it is an append
            reason = "over" if f.get("over") else "cold"  #  from there; outside them it is nothing to resume from
            with _CKPT_LOCK:                              # a cursor without a state: the fold starts cold at the cut, for the reason
                _CKPT_STATS["coldFolds"][name] = _CKPT_STATS["coldFolds"].get(name, 0) + 1   #  the document names (T359: the line
                _COLD_REASONS[(key, name)] = reason       #  blamed the cap whatever the reason)
                if reason == "over":
                    _COLD_OVER_KB[(key, name)] = int(f["over"])   # carried through this process's cold writes: over stays over
            if reason == "over":
                _say_once("checkpoint: fold %s of %s restarts cold over the tail: its state was %d KB, over the %d KB cap"
                          % (name, key, int(f["over"]), _CKPT_FOLD_CAP // 1024))
            else:
                _say_once("checkpoint: fold %s of %s restarts cold over the tail: its document carries its cursor without a state "
                          "(a tail-only state, or an older kernel's cursor-only entry); one whole refold heals it, at the session's "
                          "next settle for a leaf's folds, at the file's next checkpoint write otherwise" % (name, key))
            return (count, ent[6], _COLD)
        state = _ckpt_decode(f["state"])
        with _CKPT_LOCK:
            _CKPT_STATS["restoredFolds"][name] = _CKPT_STATS["restoredFolds"].get(name, 0) + 1
        return (count, ent[6], state)
    except (KeyError, TypeError, ValueError, OverflowError) as e:   # OverflowError: a count json read as infinity
        _ckpt_fallback(key, "corrupt", "fold %s: %s" % (name, e)); return None


_CKPT_LAG_FLOOR = 64                # a fold's count may lag the entry's by this many records, or an eighth of the entry, whichever
#                                     is more, and still be written (or carried) at its own count; further behind it is left out,
#                                     so a fold that ran early and stopped (a skipped wake-tail, a cursor dropped at quiescence)
#                                     cannot drag the document's cut, and with it every later boot's tail read, back to its count


def _lag_ok(count, c):
    return count - c <= max(_CKPT_LAG_FLOOR, count // 8)


def _count_recordable(base, total, c):
    """Whether a fold count `c` can be recorded against an entry holding records base..total: inside the held records and not
    further behind than the lag bound. The ONE count rule of the writer, the carry, the candidate check and the drop (T362)."""
    return base <= c <= total and _lag_ok(total, c)


def _cursor_recordable(cur, gen, base, total):
    """Whether a fold's cursor `cur` (count, gen, state) is one the writer records at this entry."""
    return cur is not None and cur[1] == gen and _count_recordable(base, total, cur[0])


def _path_needs_write(key, ent, at_drop=False):
    """Whether a write now would improve `key`'s document on disk with something this process holds (T360's candidate rule,
    T362's one rule): a recordable cursor whose fold holds a complete state here and has none in the document (missing, a bare
    cursor, a tail-only one). For the converge pass, a fold cold for want of a state too (the pass heals it: a whole refold,
    then the write). At the drop (`at_drop`): a dirty path too (a file about to leave memory whose folds moved since its
    document; never for the pass, for which a live leaf is dirty every turn and is written at its settle), and a cold fold
    never (the drop cannot heal it, and a write would record it cold again: no improvement, so a quiescent file whose document
    holds the dropping fold's cursor without a state is not rewritten at every fold; round one, low 1). False for a whole
    document over a clean path: nothing to write."""
    with _CKPT_LOCK:
        if at_drop and key in _FOLD_DIRTY:
            return True
        cold = {n for k, n in _COLD_FOLDS if k == key}
        if not at_drop and any(_COLD_REASONS.get((key, n), "cold") == "cold" for n in cold):
            return True
    shapes = _CKPT_DOC_FOLDS.get(key, {})
    base, gen, total = ent[5], ent[6], ent[5] + len(ent[4])
    for name, cache in list(_FOLD_REG.items()):
        if name in cold:
            continue                                      # a tail-only state is written as a cursor without one: nothing gained
        if _cursor_recordable(cache.get(key), gen, base, total) and shapes.get(name) not in ("state", "over"):
            return True
    return False


def checkpoint_cycle_begin(cap):
    """A checkpoint cycle begins (a cycle of the kernel's document writer thread, since the review of PR 155; the jobs pass's
    write phase on 2026-10-08, the pusher's cycle start before): the checkpoint byte budget `cap` (documents written and read,
    leaves read for a heal) is whole again; the converge pass and the quiescence drop's writes charge it (T362). A cap of 0
    (the pass off, or a zero byte knob) turns the drop write off: the drop pops as before, holding nothing, unless a whole
    assembly entry pins it (recordCache.pinnedKept)."""
    with _CKPT_LOCK:
        _CKPT_CYCLE["cap"] = int(cap); _CKPT_CYCLE["spent"] = 0


def checkpoint_cycle_take(n):
    """Take `n` bytes of the cycle's budget in ONE step (the room check and the charge under one lock, so concurrent folds
    cannot each pass a check the other's charge would fail): True and charged when they fit, False and uncharged otherwise."""
    with _CKPT_LOCK:
        if _CKPT_CYCLE["spent"] + int(n) > _CKPT_CYCLE["cap"]:
            return False
        _CKPT_CYCLE["spent"] += int(n)
        return True


def checkpoint_drop_writes_on():
    """Whether the quiescence drop writes documents this cycle (a cap above zero)."""
    with _CKPT_LOCK:
        return _CKPT_CYCLE["cap"] > 0


def _pop_owed_entry(key):
    """Release `key`'s cached entry unwritten (an owed drop paid by the pop alone), under the reader's lock only. A file still
    on disk goes through _drop_unless_pinned, so an entry a whole assembly entry folds over stays (2026-10-01); a file that is
    gone is popped."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(key)
    if ent is not None and os.path.exists(key):
        _drop_unless_pinned(key, ent)
        return
    with _JSONL_CACHE_LOCK:
        w = _cache_pop_locked(key)
        if w:
            _RECORD_CACHE_STATS["dropped"] += 1
            _RECORD_CACHE_STATS["droppedBytes"] += w


def _asm_entry_pins(e, key):
    """Whether assembly entry `e`, which folds over `key`'s reader entry at its generation, holds a drop of that entry back:
    a WHOLE entry whose next visit after the drop would parse whole again (2026-10-01). Its adapter holds the very records
    list (_src), so the drop frees nothing; it retires the generation, so that next visit demotes (g:rewrite, g:nonleaf) to
    the restore road, and with no document a restore would take it parses whole, while a whole reader in between decodes a
    second copy. That is a whole entry whose leaf has no document a restore would take (none stands, or a refusal mark stands
    at the leaf's stat; a restore that refuses a document without marking it removes it), or one kept whole because a
    restore in this process refused its leaf's document (keepWhole: the document it then wrote is offered to the same
    restore). Every other entry is popped as before, since its next visit restores from a document and the restore that
    replaces it frees what it held: a RESTORED entry (round one of the review: a reader entry kept, or trimmed to its cut,
    under a restored entry held a whole reader's second decoding of the tail next to the restore's own until the entry next
    demoted), and a whole entry with a document a restore would take, whether marked to re-seat on it or not (the converge
    pass writes an idle leaf's document from its whole entry and then pops, so that entry leaves memory at its next visit;
    its tail past the cut may already meet the churn bound's share). The document asked about is the entry's LEAF's (`path`),
    whichever of its files `key` is: a lineage file's drop demotes the entry under g:nonleaf, and that demotion restores from
    the leaf's document."""
    if getattr(e.get("ad"), "seed", None) is not None:
        return False
    return bool(e.get("keepWhole")) or not _asm_document_would_restore(e.get("path") or key)


def _asm_held_bytes(e, key, ent):
    """The bytes of reader entry `ent`'s records that assembly entry `e`'s adapter still holds through its source list for
    `key` (FileAdapter._src): the run of the reader's list that the adapter's list shares by identity, weighed by the reader's
    offsets. The adapter's list starts either at the reader's first record (a whole entry, or a restored entry that folded over
    a whole reader's upgrade and took the reader's whole list) or at the record its row's base names (a restored entry over a
    tail entry that has since grown by appends), and it ends where the adapter's own read ended (an append since made the
    reader a longer list over the same records). 0 when neither start is shared (a whole reader's upgrade decodes every record
    afresh, so a restored entry that has not folded since holds none of the upgraded list), or no offsets say where the run
    ends."""
    try:
        src = (getattr(e.get("ad"), "_src", None) or {}).get(key)
        recs, base = ent[4], int(ent[5])
        if not src or not recs:
            return 0
        row = (e.get("recs") or {}).get(key)
        i0 = None
        for c in (0, int(row[1]) - base if row and len(row) == 3 else -1):
            if 0 <= c < len(recs) and src[0] is recs[c]:
                i0 = c
                break
        if i0 is None:
            return 0
        i1 = min(len(recs), i0 + len(src))
        offs = ent[7] if len(ent) > 7 else None           # flat (start, length) pairs, one pair per held record
        if not offs or len(offs) < 2 * len(recs):
            return _entry_weight(ent) if i0 == 0 and i1 == len(recs) else 0
        end = int(ent[1]) if i1 >= len(recs) else int(offs[2 * i1])
        return max(0, end - int(offs[2 * i0]))
    except Exception:
        return 0


def _asm_hold_of(key, ent):
    """What the assembly entries folding over `key`'s reader entry `ent` at its generation (their recs row, the (gen, base,
    count) the fold's gate compares) make of a drop of that entry: ("pin", 0) when one of them holds it back
    (_asm_entry_pins); ("held", bytes) when some fold over it without pinning, `bytes` being the most of its records any one
    of their adapters still holds (_asm_held_bytes), which the pop cannot free before that entry's next visit replaces it (a
    restored entry; a whole one whose leaf has a document a restore would take); (None, 0) when none folds over it. Takes
    _ASM_LOCK for the snapshot alone, never under the reader's lock; the pin test's stat and sidecar read run with no lock
    held. A pin test that raises (a stat refused other than not-found) counts as no pin for that entry, so the drop goes
    ahead unless another entry pins it, counted and said through release_failed (round one of the fold's review)."""
    with _ASM_LOCK:
        entries = list(_ASM_CACHE.values())
    gen, kind, held = ent[6], None, 0
    for e in entries:
        row = (e.get("recs") or {}).get(key) if e else None
        if row is not None and len(row) == 3 and row[0] == gen:
            try:
                pins = _asm_entry_pins(e, key)
            except Exception as exc:                      # noqa: BLE001 — the pin never fails the drop it guards
                release_failed("record drop's pin check", key, exc, outcome="the check raised, so the entry counts as not pinning")
                pins = False
            if pins:
                return "pin", 0
            kind, held = "held", max(held, _asm_held_bytes(e, key, ent))
    return kind, held


def _drop_unless_pinned(key, ent):
    """Let `key`'s reader entry `ent` go (that very entry, never a newer one another thread stored), the way every drop of
    the record cache does: popped, unless a whole assembly entry folding over its generation pins it (_asm_entry_pins,
    2026-10-01), in which case it is kept as it stands, at the same generation and in its place in the LRU, counted
    recordCache.pinnedKept. Returns "dropped", "kept", or None when the cache no longer holds `ent`."""
    hold = _asm_hold_of(key, ent)
    with _JSONL_CACHE_LOCK:
        if _JSONL_CACHE.get(key) is not ent:
            return None
        return _drop_unless_pinned_locked(key, ent, hold)


def _drop_unless_pinned_locked(key, ent, hold):
    """_drop_unless_pinned's step under _JSONL_CACHE_LOCK, `ent` being the entry the cache holds for `key` and `hold`
    _asm_hold_of's answer for it, taken before the lock. A pop of records an assembly entry's adapter still holds without
    pinning them is counted again under droppedHeld, and the bytes it holds under droppedHeldBytes: recordCache.bytes falls
    by the entry's whole weight (file bytes; times the decoded multiple in memory) while that much stays until the holding
    entry's next visit replaces it, so the fall is
    not read as memory freed (round one of the review: a restored entry that folded an append over a whole reader's upgrade
    holds the reader's whole list, pre-cut records included, and a pop of it freed nothing)."""
    kind, held = hold
    if kind == "pin":
        _RECORD_CACHE_STATS["pinnedKept"] = _RECORD_CACHE_STATS.get("pinnedKept", 0) + 1
        return "kept"
    w = _cache_pop_locked(key)
    _RECORD_CACHE_STATS["dropped"] += 1
    _RECORD_CACHE_STATS["droppedBytes"] += w
    if kind == "held" and held > 0:
        _RECORD_CACHE_STATS["droppedHeld"] = _RECORD_CACHE_STATS.get("droppedHeld", 0) + 1
        _RECORD_CACHE_STATS["droppedHeldBytes"] = _RECORD_CACHE_STATS.get("droppedHeldBytes", 0) + min(w, held)
    return "dropped"


def _write_then_drop(key, ent, pop=True):
    """The quiescence drop's T362 half, shared by the quiet fold's drop (_drop_quiescent_entry, after its age gate) and the
    explicit release at the end of a file's use (release_records, with no age gate): if the file's fold document lacks
    something this process holds (_path_needs_write, the one rule), it is written NOW, before any pop, from the entry the
    process already read, so the folds' cursors (the judges' rewound memo, the kernel's named folds) restore from it at
    their next visit, reading the guard bytes, rather than reading the whole file again under a generation that no longer
    exists. The write runs with neither lock held (checkpoint_write takes _CKPT_LOCK and _JSONL_CACHE_LOCK itself) and on the
    document writer's thread alone (documents_write_here): met anywhere else, or once the process is leaving, the write AND
    the drop are handed to the writer (_hand_off_drop: the entry stays meanwhile, outcome "handed"), which does them at its
    write phase as this call would have (documents_pay_handoffs). It
    charges the cycle's byte budget shared with the converge pass, and over the budget the write AND the drop are
    deferred (the entry stays; the read is not lost), the drop owed (paid at the next cycle's start, or at the
    next quiet fold over the file, once the file is _DROP_AFTER_QUIESCENT_S old: both pay through _drop_quiescent_entry and
    its age gate, so a release's deferred drop waits for the file's quiet too), counted under
    checkpoints.converge.dropDeferred. A file whose standing document is over half the cycle's cap never fits a fresh cycle
    (the take is twice the document's size), so its drop stays owed at every cycle until the reader's bounds or the owed
    table's bound evict the entry, a limit the quiet drop already had before the release existed. Then, with `pop`, the
    very entry `ent` goes through the pin (_drop_unless_pinned_locked); a `pop` of "witness" (a returned agent's quiet fold
    that stepped nothing, _drop_quiescent_entry) is never owed over the budget, and goes only when _document_at_witness
    says the next fold restores from the document over a zero-byte tail.
    Returns (wrote, outcome): outcome "dropped", "kept" (pinned), "deferred", "handed", None when the cache no longer holds
    `ent`, or "stayed" without `pop`."""
    try:
        needs = checkpoint_drop_writes_on() and _path_needs_write(key, ent, at_drop=True)   # off (a cap of 0): the pop alone
    except Exception:
        needs = False
    wrote = False
    if needs and not documents_write_here():              # a frame thread's drop (the pusher's build, a request): the writer's
        return _hand_off_drop(key, ent, pop)              #  to do, as it would have been done here
    if needs:
        cp = _ckpt_file(key)
        try:
            est = cp.stat().st_size if cp is not None and cp.exists() else 0
        except OSError:
            est = 0
        if est <= 0:                                      # no previous document (a first write, or one the fallback removed): an
            est = max(4096, int(ent[1]) // 8)             #  estimate from the file, never a free pass against the budget, and at
            with _CKPT_LOCK:                              #  most half the cycle's cap, so a file over four caps fits a fresh cycle
                est = min(est, max(1, _CKPT_CYCLE["cap"] // 2))   # instead of deferring at every one (the true-up below charges
            #                                                   what was written; round one of the fold's review)
        if not checkpoint_cycle_take(2 * est):           # the previous document read for the carry, and about as much written:
            oldest = None                                 #  taken in one step, or deferred (the drop owed) when it does not fit
            with _CKPT_LOCK:
                _CKPT_STATS["converge"]["dropDeferred"] += 1
                if pop is True:                           # a "witness" pop is never owed: the next quiet fold takes it
                    _DROP_OWED[key] = time.time()
                    if len(_DROP_OWED) > _DROP_OWED_MAX:  # over the bound the OLDEST owed drop is paid by its pop alone (round
                        oldest = next(iter(_DROP_OWED))   #  two, low 1: a cleared mark left its entry neither paid nor popped)
                        _DROP_OWED.pop(oldest, None)
            if oldest is not None:
                _pop_owed_entry(oldest)                   # outside _CKPT_LOCK: the reader's lock alone
            return False, "deferred"
        if checkpoint_write(key):
            try:
                written = cp.stat().st_size if cp is not None else 0
            except OSError:
                written = 0
            checkpoint_cycle_charge(written - est)       # the true-up: what was written against the estimate taken above
            wrote = True
            with _CKPT_LOCK:
                _CKPT_STATS["converge"]["dropWrites"] += 1
    if pop == "witness":                                  # a returned agent's fold that stepped nothing: the entry goes only
        pop = _document_at_witness(key, ent)              #  when the next fold restores from the document over a zero-byte tail
    if not pop:
        return wrote, "stayed"
    hold = _asm_hold_of(key, ent)                         # a whole assembly entry folding over it keeps it (_drop_unless_pinned,
    with _JSONL_CACHE_LOCK:                               #  2026-10-01)
        if _JSONL_CACHE.get(key) is not ent:
            return wrote, None
        return wrote, _drop_unless_pinned_locked(key, ent, hold)


def release_records(paths, keep=()):
    """The record side of the events that end a file's use (2026-10-01): each of `paths` outside `keep` (the files a live
    session still reads) leaves the reader's cache the way the quiet fold's drop lets a file go (_write_then_drop): the file's
    fold document is written first when it lacks something this process holds, so the next fold or walk over the file, while
    the file is unchanged, restores from it at the guard bytes' cost instead of reading the whole file again (round one of the
    review: a pop with no write sent the judges' episode scan after a second /clear, and the next named fold over a dead
    lane's leaf, back to the whole file); over the cycle's budget the drop is deferred and owed (a file whose document is over
    half the cycle's cap stays owed at every cycle until the reader's bounds or the owed table's bound evict the entry, a
    limit the quiet drop already had: _write_then_drop); and a file a whole assembly entry still folds over stays
    (_drop_unless_pinned_locked). There is no age gate: the event, not the file's quiet, says the use ended. Counted under
    dropped and droppedBytes. Returns how many entries were dropped now. A process that never begins a cycle (the judges
    child, with judges-process on, off by default) keeps the default cap for its life: once that is spent, a drop that needs a
    write stays owed until the reader's bounds or the owed table's bound evict the entry, as that process's quiet drops
    already did before this release existed."""
    keep = {str(p) for p in keep}
    n = 0
    for p in paths:
        key = str(p)
        if key in keep:
            continue
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None:
            continue
        with _CKPT_LOCK:
            _DROP_OWED.pop(key, None)                     # an earlier deferral of this file's drop is taken here (re-owed if this
        #                                                   one is deferred too)
        if _write_then_drop(key, ent, pop=True)[1] == "dropped":
            n += 1
    return n


class hold_quiescent_drops:
    """`with hold_quiescent_drops():` on this thread a quiescent-drop fold neither writes nor pops at its end; the drop is held
    (with whether it would have popped) for pay_held_drops, so a pass that heals and primes a leaf over its resident entry
    writes the document ONCE, after every fold is current, and pops once (a heal whose last fold was the launch fold popped the
    entry mid-heal before, and the folds never called stayed out of the document)."""
    def __enter__(self):
        _DROP_HOLD.held = {}
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            _DROP_HOLD.held = None                        # a raise inside the block: nothing stays held on this thread (an unpaid
        return False                                      #  hold would hold every later drop here; the entries fall to the reader's LRU)


def pay_held_drops():
    """Pay the drops held on this thread (see hold_quiescent_drops): {path: True when its document was written}. Each is paid
    as the fold would have (the write when the rule says so, the pop when a fold stepped records), against the cycle's budget."""
    held = getattr(_DROP_HOLD, "held", None)
    _DROP_HOLD.held = None
    out = {}
    for key, pop in (held or {}).items():
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None:
            continue
        out[key] = bool(_drop_quiescent_entry(key, ent, pop=pop))
    return out


def checkpoint_pay_owed_drops():
    """The cycle's start (T362 round one, low 2): the drops deferred for an earlier cycle's budget are paid now, oldest first,
    with the room this cycle has, since the files this serves (a returned subagent's transcript) may never be folded again and
    their whole entries would otherwise stay resident for the kernel's life. An owed path whose entry or file is gone is
    forgotten. The document writer's work (documents_write_here): past the stop, or off the writer, it pays nothing and the
    rest stay owed. Returns the drops paid (written or popped)."""
    with _CKPT_LOCK:
        owed = list(_DROP_OWED)
    paid = 0
    for key in owed:
        if not documents_write_here():
            break
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None or not os.path.exists(key):
            with _CKPT_LOCK:
                _DROP_OWED.pop(key, None)
            if ent is not None:                           # the file is gone: nothing to write, everything to release (round two,
                _pop_owed_entry(key)                      #  low 2: the memory the drop was owed for)
            continue
        with _doc_progress("fold", key, ent[1]) as _w:
            _w.wrote = bool(_drop_quiescent_entry(key, ent, pop=True))   # takes the owed mark itself; over the budget again it
            #                                                                re-defers
        with _CKPT_LOCK:
            if key not in _DROP_OWED:
                paid += 1
    return paid


def checkpoint_cycle_charge(n):
    """Charge `n` bytes to the cycle's budget (the pass's reads and writes, known only after the fact; a true-up may be
    negative); True while the cycle stays within it."""
    with _CKPT_LOCK:
        _CKPT_CYCLE["spent"] += int(n)
        return _CKPT_CYCLE["spent"] <= _CKPT_CYCLE["cap"]


def checkpoint_cycle_room(n):
    """Whether `n` more bytes would stay within the cycle's budget (the pass's gate between candidates)."""
    with _CKPT_LOCK:
        return _CKPT_CYCLE["spent"] + int(n) <= _CKPT_CYCLE["cap"]


def _carry_forward_states(key, folds, base, count, size, mtime):
    """The on-disk document's entries for folds this process holds no cursor for (a fold that never ran here, such as the
    transcript's wake-tail or queue-ledger folds beside the five leaf folds), carried into the next write so a write from
    this process's cursors alone does not strip states an earlier process stored (T360 review, medium 2: before the
    converge pass an idle session's document was never rewritten, so they survived). Only entries with a state, or an
    over-the-cap cursor, at a count inside this entry's held records, and only when the document's own guard still
    stands on disk (a file rewritten since voids its states); a bare or tail-only cursor is not carried: the next boot reads
    the file whole for that fold once and the write after carries its state.
    The document is not read when this process already knows it gives nothing: when _DOC_INDEX describes the very file on
    disk (its inode, mtime and size, as this process last wrote or loaded it) and none of its carryable entries passes the
    filter below (each names a fold this write holds, stands at a count it cannot record, or has no int count), the
    answer is {} without a read, the answer the read would give whatever its verdict and guard say, since those only ever
    withhold entries."""
    cp = _ckpt_file(key)
    if cp is None:
        return {}
    sig = _doc_stat_sig(cp, key)
    if sig is None:
        return {}                                         # no document (a missing file is the common case: a first write)
    with _CKPT_LOCK:
        idx = _DOC_INDEX.get(key)
    if idx is not None and idx[0] == sig and not any(
            n not in folds and c is not None and _count_recordable(base, count, c) for n, c in idx[1].items()):
        return {}                                         # nothing in it this write could carry: no read
    doc = _ckpt_doc_shared(key)                           # one read, shared with a retirement's consult of the same document (low C)
    if not isinstance(doc, dict) or doc.get("v") != _CKPT_V or doc.get("path") != os.path.realpath(key):
        return {}
    try:
        if _ckpt_verdict(doc, size, mtime):                   # the entry's stat against the document, as the restore paths ask: a
            return {}                                         #  same-size edit under another mtime is a rewrite, and carries nothing
        off, guard = int(doc["offset"]), bytes.fromhex(doc.get("guard") or "")
        with open(key, "rb") as fh:
            fh.seek(max(0, off - len(guard)))
            ok = fh.read(len(guard)) == guard
        _count_read(key, len(guard))
        if not ok:
            return {}
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    out = {}
    for name, f in (doc.get("folds") or {}).items():
        if name in folds or not isinstance(f, dict) or not ("state" in f or f.get("over")):
            continue
        c = f.get("count")
        if type(c) is not int:                                # an int count only: the entry is carried as it stands and the cut takes
            continue                                          #  its count as is, so "3", 3.0, infinity or none is not carried
        if _count_recordable(base, count, c):                 # a carried count too far behind would drag the cut (the bound above)
            out[name] = f
    return out


def checkpoint_write(path, force=False):
    """Write `path`'s checkpoint from the reader's entry and every registered fold whose cursor stands at the entry's
    record count. False when there is nothing to write (no entry, or no fold at the witness and not `force`)."""
    key = str(path)
    cp = _ckpt_file(key)
    if cp is None:
        return False
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(key)
    if ent is None:
        return False
    mtime, size, offset, tail, records, base, gen = ent[:7]
    count = base + len(records)
    folds = {}
    for name, cache in list(_FOLD_REG.items()):
        cur = cache.get(key)
        if not _cursor_recordable(cur, gen, base, count):
            continue                                      # no cursor at this entry, one outside its held records, or one too far
        #                                                   behind to be written at its count without dragging the cut (_lag_ok)
        fcount = cur[0]                                   # the fold's OWN count (T359): a fold stepped by builds, not by the settle,
        with _CKPT_LOCK:                                  #  lags the entry and was left out silently before, to read the leaf whole
            cold = (key, name) in _COLD_FOLDS             #  at the next boot; the restore steps the held tail from its count
        if cold:                                          # a state that began cold at a cut covers the tail only: it must not
            with _CKPT_LOCK:                              #  be written as a complete one (the next process would restore it
                _CKPT_STATS["coldWrites"][name] = _CKPT_STATS["coldWrites"].get(name, 0) + 1   #  as whole and say nothing)
                over_kb = _COLD_OVER_KB.get((key, name)) if _COLD_REASONS.get((key, name)) == "over" else None
            if over_kb is not None:                       # cold BECAUSE over the cap: the reason and its KB carry through, so the
                folds[name] = {"count": fcount, "over": over_kb}   #  next boot neither promises a heal nor refolds it whole
            else:
                folds[name] = {"count": fcount, "cold": 1}   # a true tail-only state: the next process says why, and heals it
            continue
        try:
            enc = _ckpt_encode(cur[2])
            n_enc = len(json.dumps(enc, separators=(",", ":")))
            if n_enc > _CKPT_FOLD_CAP:
                with _CKPT_LOCK:                          # a state the size of its file: the document must not become the file
                    _CKPT_STATS["oversizeFolds"][name] = _CKPT_STATS["oversizeFolds"].get(name, 0) + 1
                folds[name] = {"count": fcount, "over": -(-n_enc // 1024)}   # the cursor without its state, with the reason (its KB):
                continue                                  #  the next process starts this fold cold over the tail (counted)
            folds[name] = {"count": fcount, "state": enc}
        except TypeError:
            with _CKPT_LOCK:
                _CKPT_STATS["skippedFolds"] += 1
    if not folds and not force:
        return False
    carried = _carry_forward_states(key, folds, base, count, size, mtime)
    folds.update(carried)                                 # the disk document's states for folds this process never ran
    with _CKPT_LOCK:
        retired = set(_RETIRED_FOLDS.get(key, ()))        # taken AFTER the cursor snapshot and the carry: a forget that raced the
    for n in retired:                                     #  snapshot still omits its fold here; every name taken is honoured by
        folds.pop(n, None)                                #  this write whether the fold was present to pop or already absent
    omitted = retired
    cut = min([f["count"] for f in folds.values()] or [count])   # the document's cut: the LOWEST written cursor (T359), so the
    moved = None                                          #  next process's tail read holds every record a lagging fold has
    if cut < count and len(ent) >= 8 and ent[7]:          #  yet to step (its append), bounded by the records this entry holds
        off_cut = int(ent[7][(cut - base) * 2])           # the cut record's byte offset; the 64 bytes before it are the guard.
        try:                                              # The entry holds records, not bytes, so the guard is read from the file, in
            with open(key, "rb") as fh:                   #  the same open that first verifies the entry's OWN witness guard (its bytes
                fh.seek(max(0, offset - len(tail)))       #  before its offset, captured with its records): an append since the read
                if fh.read(len(tail)) != tail:            #  leaves that prefix intact and the cut move proceeds, carrying the lagging
                    raise OSError("the file was rewritten under the entry")   #  fold; a rewrite of any size or time fails it and writes
                fh.seek(max(0, off_cut - 64)); guard_cut = fh.read(min(64, off_cut))   #  the witness form, which the restore then refuses
            if off_cut and not guard_cut.endswith(b"\n"):   #  (review: a stat gate refused appends and passed a time-preserving copy)
                raise OSError("no record boundary at the cut")
        except OSError:
            guard_cut = None
        if guard_cut is not None:
            moved = (off_cut, guard_cut, records[cut - base - 1] if cut > base else None, cut)
    left_out = False
    if moved is not None:
        offset, tail, last, count = moved
    else:
        if cut < count:                                   # no cut move: the folds at the witness only; the lagging ones are left
            folds = {n: f for n, f in folds.items() if f["count"] == count}   #  out, and the path stays DIRTY below so the next
            left_out = True                               #  write (a settle, the exit drain) tries them again (review, low 3)
            if not folds and not force:
                return False
        last = records[-1] if records else None
    with _CKPT_LOCK:
        seq = _CKPT_SEQ.get(key, 0) + 1
    doc = {"v": _CKPT_V, "path": os.path.realpath(key), "size": int(size), "mtime": float(mtime), "offset": int(offset),
           "guard": tail.hex(), "count": int(count), "lastUuid": (last.get("uuid") if isinstance(last, dict) else None),
           "seq": seq, "t": time.time(), "folds": folds}
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = _writer_tmp(cp)
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        sig = _doc_sig(tmp.stat())                        # the rename keeps the inode, mtime and size: the stat of the file this
        os.replace(tmp, cp)                               #  write puts at `cp`, never another writer's (a later rename there: the next stat differs)
    except OSError:
        return False
    with _CKPT_LOCK:
        _CKPT_SEQ[key] = seq
        _CKPT_STATS["writes"] += 1
        _CKPT_DOC_FOLDS[key] = _doc_fold_shapes(folds)
        _doc_index_put_locked(key, sig, folds)            # what the next write's carry could take from it, known without a read
        if any(n in folds for n in carried):              # it holds an entry this process does not compute: the next carry needs it
            _doc_memo_put_locked(key, sig, doc)           #  again and takes it from memory (`doc` is what _ckpt_load would return)
        else:
            _doc_memo_drop(key)                           # this process's own states only: the index serves the next carry
        if omitted:                                       # the retirements this document honoured are done; any that arrived
            rem = _RETIRED_FOLDS.get(key)                 #  after the check above stay, and keep the path dirty for the next write
            if rem is not None:
                rem -= omitted
                if not rem:
                    _RETIRED_FOLDS.pop(key, None)
        if left_out or _RETIRED_FOLDS.get(key):
            _FOLD_DIRTY.add(key)                          # a lagging fold has no cursor in this document yet, or a retirement owed
        else:
            _FOLD_DIRTY.discard(key)
    return True


def checkpoint_dirty():
    """The paths whose fold cursors moved since their checkpoint was last written."""
    with _CKPT_LOCK:
        return sorted(_FOLD_DIRTY)


def _doc_fold_shapes(folds):
    return {n: ("state" if "state" in f else "over" if f.get("over") else "cold" if f.get("cold") else "bare")
            for n, f in (folds or {}).items() if isinstance(f, dict)}


def checkpoint_converge_candidates():
    """The dirty paths whose document on disk lacks a complete state for a fold this process holds a cursor for at the
    current entry (the fold is missing from it, or recorded as a bare or tail-only cursor), or whose fold began cold for
    want of a state: the writes that make a boot's whole reads pay ONCE (T360). Before them a fold that never ran in the
    writing process had no entry, the next boot read the leaf whole for it, and an idle session, which never settles,
    paid that at every boot. A dirty path whose document already carries every fold that ran is not one: a live session's
    leaf is dirty every turn and is written at its settle, never here (no steady stream of writes)."""
    out = []
    with _CKPT_LOCK:
        cold = {(k, n) for k, n in _COLD_FOLDS if _COLD_REASONS.get((k, n), "cold") == "cold"}
        paths = sorted(_FOLD_DIRTY | {k for k, n in cold})   # a cold cursor at the witness steps nothing and marks nothing dirty
    for key in paths:
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None:
            continue
        if _path_needs_write(key, ent):                   # the one rule (T362): dirty, cold for want of a state, or a recordable
            out.append(key)                               #  cursor whose fold has no complete state in the document
    return out


def checkpoint_has_work():
    """Whether any fold cursor moved since its checkpoint was written, or any fold began cold: the converge pass's zero-cost
    gate (no lock, no stat) so a quiet cycle costs it nothing (T361)."""
    return bool(_FOLD_DIRTY) or bool(_COLD_FOLDS)


def file_quiescent(path):
    """Whether `path` has been unchanged for longer than the reader keeps a quiescent file's whole entry
    (_DROP_AFTER_QUIESCENT_S): a fold with drop_after="quiescent" drops that entry right after it steps, so a whole read
    of such a file by the converge pass cannot be held long enough to write its checkpoint from (T361). The reader's own
    rule, asked by the pass to predict what a fold will do to the entry; it says nothing about whether a CLI is writing the
    file (the kernel's converge pass asks the session's live state for that, 2026-10-08)."""
    try:
        return time.time() - os.stat(str(path)).st_mtime >= _DROP_AFTER_QUIESCENT_S
    except OSError:
        return False


def converge_stat(name, n=1):
    """Count a converge pass's work under checkpoints.converge (the kernel's pass reports through here)."""
    with _CKPT_LOCK:
        _CKPT_STATS["converge"][name] = _CKPT_STATS["converge"].get(name, 0) + n


def checkpoint_write_dirty(paths=None, budget_s=None):
    """Write the checkpoints of `paths` (default: every dirty path); returns how many were written. `budget_s`
    bounds the pass (the exit path, 2026-09-11: an unbounded write over a kernel life's dirty files outran the
    manager's 5 s grace and the SIGKILL lost the cut row): the first path is always attempted, the pass stops once the
    budget has passed, whether or not anything was written, and what is left stays dirty for the next writer."""
    return len(checkpoint_write_dirty_paths(paths, budget_s))


def checkpoint_write_dirty_paths(paths=None, budget_s=None, stop=None):
    """checkpoint_write_dirty, returning the paths written (the converge pass skips them at its next look: the kernel's
    `_ckpt_just_take`). The budget is keyed on the paths ATTEMPTED, as the converge pass's is: under a lasting fault every
    write fails (a checkpoints directory the kernel cannot search or write, a full disk), each after its encode, and a
    budget that tripped only after a written path let the exit drain attempt every dirty one. `stop`, when given, is asked
    before EVERY path, the first included: once it is True no further write starts (the kernel's settle write passes its exit
    stand-down, so a write never begins beside the exit's own; the exit drain passes none)."""
    out = []
    t0 = time.monotonic()
    for i, p in enumerate(checkpoint_dirty() if paths is None else [str(p) for p in paths]):
        if stop is not None and stop():
            break
        if budget_s is not None and i and time.monotonic() - t0 > budget_s:
            break
        if checkpoint_write(p):
            out.append(p)
    return out


def _asm_retire_version_mark(meta, doc):
    """Retire the refusal mark of a version-old sidecar (checkpoint_sweep): the sidecar's bytes kept beside the document as
    .meta.retired-<stamp> (_asm_retire_refusal_mark), the sidecar rewritten without the `refused` block, one count under
    removed["refusedMark:version"]. The rewrite's own errors stay HERE (the 1708 read, low 1): inside the sweep loop's try an
    OSError on the tmp write or the replace (a full disk, a read-only directory) fell to the loop's except, which reads
    "this document does not verify" and UNLINKED the document, its sidecar and the aside just written, counted under `sweep`,
    with the .meta.<pid>.tmp left behind. Now a failed rewrite leaves the document and the mark standing (the mark is read
    before the document, so the leaf keeps parsing whole as before; the next boot retries), removes its tmp, says so once
    on stderr and counts under removed["refusedMark:versionFailed"]; the document is never removed for a sidecar write."""
    _asm_retire_refusal_mark(meta)
    d2 = {k: v for k, v in doc.items() if k != "refused"}
    mtmp = _writer_tmp(meta)
    try:
        mtmp.write_text(json.dumps(d2))
        os.replace(mtmp, meta)
    except OSError as e:
        try:
            mtmp.unlink()
        except OSError:
            pass
        _asm_removed("refusedMark:versionFailed")
        _say_once("checkpoint sweep: the refusal mark of %s could not be retired (%s); the mark stands, the document stays" % (meta.name, e))
        return False
    _asm_removed("refusedMark:version")
    return True


_TMP_PID_RE = re.compile(r"\.(?:mark\.)?(\d+)(?:\.[0-9a-f]+)?\.tmp$")   # the writers' temp names: <file>.<pid>[.<thread>].tmp, <meta>.mark.<pid>.<thread>.tmp


def _writer_tmp(p, tag=""):
    """The temp name a writer stages `p` under before its atomic replace: the process AND the thread, so two writers of one file
    never share one, in the shape the boot sweep reads back (_TMP_PID_RE: the pid first, so a dead process's file is removed).
    The sidecar's was the process's alone until 2026-10-23 (the review of PR #367): the document rebuild's thread and the
    converge pass can write the same leaf's sidecar at once, and then one writer's replace moved the other's half-written file
    into place, or found it already gone and dropped its own write."""
    return p.with_name("%s.%s%d.%x.tmp" % (p.name, tag, os.getpid(), threading.get_ident()))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                                       # it exists under another user: alive for this purpose
    return True


def checkpoint_sweep():
    """One pass over the checkpoint directory at boot: a document whose recorded file no longer exists, or that does
    not parse, is removed (counted as swept), so cleared and removed sessions do not grow the directory forever."""
    d = _ckpt_dir()
    if d is None or not Path(d).is_dir():
        return 0
    gone = 0
    for tmp in Path(d).glob("*.tmp"):                     # a writer's temp file whose process died before its rename (an exit lands
        m = _TMP_PID_RE.search(tmp.name)                  #  wherever the document refresh's thread stands, asm_refresh_document):
        if m is None or int(m.group(1)) == os.getpid() or _pid_alive(int(m.group(1))):   # a live writer's is never touched
            continue
        try:
            tmp.unlink(); gone += 1
        except OSError:
            pass
    for aside in Path(d).glob("*.asm.json.gz.meta.retired-*"):   # a retired refusal sidecar (the writer keeps the bytes of a mark it
        cp = aside.with_name(aside.name.split(".meta.retired-")[0])   #  replaced): it leaves with its document, never on its own
        if not cp.exists():
            try:
                aside.unlink(); gone += 1
            except OSError:
                pass
    for ov in list(Path(d).glob("*.asm.oversize")) + list(Path(d).glob("*.asm.memory")) + list(Path(d).glob("*.tail-stamps")):
        #                                                   an oversize verdict (_ASM_OVERSIZE), a memory verdict (the document
        #                                                   worker's) or a high-water entry (tail_stamps) leaves with its transcript
        try:
            v = json.loads(ov.read_text(encoding="utf-8"))
            keep = isinstance(v, dict) and isinstance(v.get("path"), str) and os.path.exists(v["path"])
        except (OSError, ValueError):
            keep = False
        if not keep:
            try:
                ov.unlink(); gone += 1
            except OSError:
                pass
    for cp in list(Path(d).glob("*.json")) + list(Path(d).glob("*.asm.json.gz")):
        keep = False
        try:
            meta = cp.with_name(cp.name + ".meta") if cp.name.endswith(".gz") else None
            if meta is not None and meta.exists():
                text = meta.read_bytes()                      # the sidecar: the sweep never inflates a document
            else:
                text = cp.read_bytes()
                if cp.name.endswith(".gz"):
                    text = gzip.decompress(text)
            _count_read(str(cp), len(text))
            doc = json.loads(text.decode("utf-8"))
            keep = isinstance(doc, dict) and isinstance(doc.get("path"), str) and os.path.exists(doc["path"])
            if keep and meta is not None and meta.exists() and isinstance(doc.get("refused"), dict):
                av = doc.get("av")
                if not isinstance(av, int) or av < _ASM_CKPT_V:
                    # A refusal mark belongs to the cut rule it was made under (plans/checkpoint-mark-version-retirement.md,
                    # 2026-09-15): this sidecar's mark was made against an older document version, and since the mark is read
                    # BEFORE the document (_asm_refusal_stands), the load's version check never reached the leaf: an idle session
                    # parsed whole at every boot (sixteen of them at the measurement boot after stage one b). The mark is retired
                    # here, once: the sidecar's bytes kept beside it as .meta.retired-<stamp>, the sidecar rewritten without the
                    # block (its av, path, files and linked unchanged), so the next parse takes the version-refusal road once and
                    # the settle's write produces the current version's document. A sidecar without av is version-old too
                    _asm_retire_version_mark(meta, doc)
        except (OSError, ValueError):
            keep = False
        if not keep:
            try:
                cp.unlink(); gone += 1
                if cp.name.endswith(".gz"):                # an assembly document: counted as removed, its sidecar with it
                    _asm_removed("sweep", document=True)
                    cp.with_name(cp.name + ".meta").unlink(missing_ok=True)
                    for aside in Path(d).glob(cp.name + ".meta.retired-*"):   # and every retired refusal sidecar it left
                        try:
                            aside.unlink()
                        except OSError:
                            pass
            except OSError:
                pass
    with _CKPT_LOCK:
        _CKPT_STATS["swept"] += gone
    return gone


READ_BY_PATH_TOP = 50             # rows of readByPath a plain /perf read carries: the paths with the most bytes (2026-10-01: the whole
#                                   table, one row per path ever read, was most of the snapshot's bytes within minutes of
#                                   a boot, on every poll); GET /perf?paths=all carries the whole table, and so does ?walk=1 (the
#                                   kernel's _perf_paths)


def _top_paths(items, top):
    """{path: bytes} of the `top` (path, bytes) pairs with the most bytes, or all of them when `top` is None: one pass with a
    bounded heap, never a sort of every path."""
    if top is None or len(items) <= top:
        return dict(items)
    import heapq
    return dict(heapq.nlargest(top, items, key=lambda kv: kv[1]))


def read_by_path(top=None):
    """({path: bytes the reader pulled off it since boot}, the running total): the `top` paths with the most bytes, or every
    path when `top` is None (the whole table GET /perf?paths=all carries), and the total taken in the same lock acquisition,
    so the rows never run ahead of the total beside them (review of 2026-10-02: a total from the snapshot's earlier copy
    trailed the table by the reads between the two)."""
    with _READ_BYTES_LOCK:
        items = list(_READ_BYTES.items())
        total = _READ_BYTES_TOTAL[0]
    return _top_paths(items, top), total


def checkpoint_document_bytes(items):
    """documentBytes from a copy of the read table, its (path, bytes) pairs: the rows under the checkpoint directory, 0 with
    no directory. checkpoint_stats and the kernel's whole-table read (_perf_paths) both take it from the copy their readBytes
    comes from, so the table's checkpoint rows never run ahead of it (review of 2026-10-02)."""
    d = _ckpt_dir()
    if d is None:
        return 0
    prefix = str(d) + os.sep
    return sum(n for p_, n in items if p_.startswith(prefix))


def checkpoint_stats():
    """The fold checkpoints for /perf. Most fields count events since boot (restored, writes, swept, fallbacks and the other
    per-reason tables), and `documentBytes` (the checkpoint documents' own reads) and `readBytes` (every byte the JSONL reader
    pulled off disk) are running counters of bytes READ since boot, never memory held. The gauges are `dirty`, `docMemo`
    and `readPaths`. `readByPath` is the per-file split of readBytes, cut to the READ_BY_PATH_TOP paths with the most bytes
    (`readPaths` says how many paths the whole table has; GET /perf?paths=all carries it whole, and so does ?walk=1)."""
    with _CKPT_LOCK:
        out = dict(_CKPT_STATS); out["fallbacks"] = dict(_CKPT_STATS["fallbacks"]); out["restoredFolds"] = dict(_CKPT_STATS["restoredFolds"])
        out["oversizeFolds"] = dict(_CKPT_STATS["oversizeFolds"]); out["coldFolds"] = dict(_CKPT_STATS["coldFolds"])
        out["coldWrites"] = dict(_CKPT_STATS["coldWrites"]); out["converge"] = dict(_CKPT_STATS["converge"])
        out["refolds"] = {k: dict(v) for k, v in _CKPT_STATS["refolds"].items()}
        out["rewoundMemo"] = dict(_REWOUND_STATS)
        out["docMemo"] = {"entries": len(_DOC_MEMO), "bytes": _DOC_MEMO_BYTES[0], "capBytes": _DOC_MEMO_CAP,
                          "parseMultiple": _DOC_MEMO_PARSE_MULTIPLE}
    with _READ_BYTES_LOCK:
        out["dirty"] = len(_FOLD_DIRTY)
    with _READ_BYTES_LOCK:                            # the total and the table from one copy, as read_bytes_report takes them
        items = list(_READ_BYTES.items())
        out["readBytes"] = _READ_BYTES_TOTAL[0]
    out["documentBytes"] = checkpoint_document_bytes(items)   # and the documents' share, from that same copy
    out["readPaths"] = len(items)
    out["readByPath"] = _top_paths(items, READ_BY_PATH_TOP)   # per file, so a boot can be read as tails versus whole files
    return out


def _scan_jsonl_bytes(data, base_offset, offsets=None):
    """(records, consumed) for a bytes blob of jsonl starting at base_offset: parsed objects of every
    COMPLETE line, and the byte offset just past the last complete line (a trailing partial is left).
    `offsets`, an array when given, receives each parsed record's (byte offset, byte length) as two
    appended values: the assembly checkpoint names a record by where it sits (T323 stage 4).
    The reader itself no longer calls this: it streams its lines off the open file (_scan_jsonl_stream
    below, measured 2026-09-15), because this scanner copies the blob to its last newline and splits the
    copy into a list of every line before it decodes one record: two copies of the source live at once beside
    the records (the blob and the list of its lines; a third, the copy to the last newline, only when a partial
    line trails, since CPython hands the same object back for a full slice). It stays as the REFERENCE the
    streaming scanner is held equal to (tests/test_reader_stream_peak.py) and has no other caller."""
    end = data.rfind(b"\n")
    if end < 0:
        return [], base_offset
    records = []
    pos = 0
    body = data[:end + 1]
    for line in body.splitlines(keepends=True):
        at, pos = pos, pos + len(line)
        line_s = line.strip()
        if not line_s:
            continue
        try:
            records.append(json.loads(line_s.decode("utf-8", "replace")))
        except Exception:
            continue
        if offsets is not None:
            offsets.append(base_offset + at); offsets.append(len(line))
    return records, base_offset + end + 1


def _held_crc(fh, path, start, end):
    """The zlib.crc32 of `fh`'s bytes [start, end), read raw in 4 MiB chunks and counted as read, or None when the file holds
    fewer: the proof a same-size file under a new mtime still holds the bytes a reader entry kept (2026-10-23)."""
    fh.seek(start)
    crc, left = 0, max(0, end - start)
    while left > 0:
        chunk = fh.read(min(left, 1 << 22))
        if not chunk:
            break
        crc = zlib.crc32(chunk, crc)
        left -= len(chunk)
    _count_read(path, max(0, end - start) - left)
    return crc if left == 0 else None


def _scan_jsonl_stream(fh, base_offset, offsets=None, limit=None, crc=None):
    """(records, consumed, bytes_read) for the jsonl from `fh`'s CURRENT position to its end, decoded line by line off
    the open binary file: the parsed objects of every COMPLETE line, the byte offset just past the last complete line
    (a trailing line with no newline yet is a writer caught mid-append and is left for the next read, as
    _scan_jsonl_bytes leaves it), and the bytes the stream pulled, which the caller's byte counters take the way they
    took len() of the one read this replaces. `offsets`, as for _scan_jsonl_bytes, receives each parsed record's
    (byte offset, byte length); the values are identical to the reference scanner's. `limit`, when given, is the
    number of bytes past the current position that existed when the caller statted the file: the read ENDS there, a
    line crossing it is left as a partial for the next read, so a reader never chases a fast writer's appends (the one
    read this replaces captured its end at read time; a line-by-line loop over a file being appended would otherwise
    run for as long as the writer keeps ahead of the decode: review find, 2026-09-15).

    Why a stream (measured 2026-09-15): the reader pulled the file, or its grown tail, into one bytes object and handed
    it to _scan_jsonl_bytes, which copied it up to the last newline and split that copy into a list of every line
    before decoding a single record, so about three copies of the source were live at once beside the records being
    built, and the allocator kept the arenas that peak took. Over a 37.7 MB transcript in a lab process the records
    weighed 107.6 MB (2.86 live heap bytes per source byte) but the read peaked at 183.8 MB and left the process
    223 MB larger (155 MB with tracemalloc off; VmRSS deltas of 217,900 and 151,140 KiB): the temporaries are the
    whole blob and the list of every line, and the copy to the last newline when the blob ends in a partial line
    (CPython hands the same object back for a full slice), so up to three copies beside the records;
    on the live kernel a 40-minute sample stepped the resident size by 5.8 bytes per source byte the record cache
    admitted (+525 MB against +94.5 MB of source; one +60 MB read, +332 MB), the retained heap the lag investigation
    traced, of which the lab attributes about one byte per source byte to this transient (4.1 to 3.1 resident bytes
    per source byte with tracemalloc off); the rest is the records the cache holds. Streamed, the same read peaks
    10 KB over its records and leaves the process 116 MB larger with tracemalloc off (113,320 KiB): the records
    themselves, which a reader must hold. Iterating the file yields one line at a time, so what is live beside the records is bounded by the largest line (with its strip and decode copies) and the file's read buffer, not by the file; what is live beyond the
    records is the line in hand and the file's read buffer. The file stays a binary file object, so the caller's
    seek and tell after the iteration are exact (a text wrapper's are not).

    The boundaries are the reference's exactly: file iteration yields newline-terminated lines, and each is then split
    with the same bytes.splitlines(keepends=True) _scan_jsonl_bytes ran over the whole body, so a bare \\r inside a line
    breaks it into the same pieces (a \\r\\n ending stays one boundary; \\x0c, \\x0b and \\x1c-\\x1e break under neither,
    those are str.splitlines' boundaries), and a malformed line such as `{"a":1}<CR>junk` yields the object before the
    \\r under both. Splitting the concatenation equals concatenating the splits, since b"\\n" is itself a boundary, so
    records, consumed offset and offsets are identical for every input, valid or not (review find, 2026-09-15: the
    first cut split at b"\\n" alone and documented the bare-\\r case as a difference; parity costs one splitlines call
    per line). tests/test_reader_stream_peak.py pins the equivalence, the bare-\\r case included.

    `crc`, when given, is a one-item list holding a running zlib.crc32, carried over every COMPLETE line the scan consumes
    (the bytes from the start position to the consumed offset; a trailing partial is not in it), so the reader's entry can
    prove the bytes it holds without keeping them (2026-10-23: a same-size file under a new mtime is checked against it)."""
    records = []
    seen = 0                                              # bytes iterated so far, complete lines and the trailing partial alike
    consumed = 0                                          # bytes of complete lines: what the next read resumes after
    remaining = limit
    while True:
        line = fh.readline() if remaining is None else fh.readline(remaining)   # the bound is in the read itself: a growing
        if not line:                                                            #  line with no newline yet cannot be pulled past
            break                                                               #  the captured end, nor keep the reader busy there
        if not line.endswith(b"\n"):
            seen += len(line)
            break                                         # the file's last line with no newline yet, or a line running past the
        #                                                   end the caller captured (a writer still appending): a partial, left as is
        if remaining is not None:
            remaining -= len(line)
        if crc is not None:
            crc[0] = zlib.crc32(line, crc[0])
        for piece in line.splitlines(keepends=True):      # the reference's boundaries within the line (a bare \r splits)
            at = seen
            seen += len(piece)
            piece_s = piece.strip()
            if not piece_s:
                continue
            try:
                records.append(json.loads(piece_s.decode("utf-8", "replace")))
            except Exception:
                continue
            if offsets is not None:
                offsets.append(base_offset + at); offsets.append(len(piece))
        consumed = seen
    return records, base_offset + consumed, seen


def _entry_offsets_gen(path):
    """(record offsets from record 0, generation) of the reader's entry for `path` from ONE entry tuple under one lock
    acquisition, or (None, None) with no entry or a tail entry: what the assembly writer compares its adapter's source
    key against before trusting the entry's offsets for the records the adapter read (T396 round one, low 2: the
    _asm_gates pattern, never two reads of the cache that could see two entries)."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(str(path))
    if ent is None or len(ent) < 8 or ent[5] > 0:
        return None, None
    offs = ent[7]
    return [(offs[i], offs[i + 1]) for i in range(0, len(offs), 2)], ent[6]


def _reader_entries_of(entry):
    """{path: the reader's entry tuple} for every file whose records the assembly `entry`'s adapter took from the reader (its
    recs row: (generation, base, count)), taken now while the parse that installed `entry` has just read them, whole entries
    at that very generation only. A handed rewrite keeps them beside the entry (review of PR 158): the writer needs the
    records' byte offsets, which only the reader's entry carries, and once the assembly entry is evicted nothing keeps that
    entry resident (the assembly pin goes with it, and the reader's count cap and byte budget pin nothing). The records
    themselves are the ones the kept adapter already holds, so this keeps the offsets alone alive."""
    out = {}
    rows = (entry or {}).get("recs") or {}
    with _JSONL_CACHE_LOCK:
        for fp, row in rows.items():
            ent = _JSONL_CACHE.get(fp)
            if (ent is not None and len(ent) >= 8 and not ent[5] and isinstance(row, tuple) and len(row) == 3
                    and row[0] == ent[6]):
                out[fp] = ent
    return out


def _kept_reader_offsets(path, ent):
    """(record offsets, generation) from a kept reader entry `ent` for `path` (_reader_entries_of), or (None, None) when the
    file no longer holds the bytes the entry read: its witness, the bytes before the entry's offset, read from the file now
    and compared, the reader's own test for an append against a rewrite (_read_jsonl_entry, checkpoint_write). An append
    since leaves the prefix and its offsets standing; a rewrite or a truncation fails it, and the writer declines for
    offsets, a blip that marks nothing (_ASM_SKIP_BLIP)."""
    try:
        offset, tail = int(ent[2]), ent[3]
        with open(str(path), "rb") as fh:
            fh.seek(max(0, offset - len(tail)))
            if fh.read(len(tail)) != tail:
                return None, None
    except (OSError, TypeError, ValueError, IndexError):
        return None, None
    offs = ent[7]
    _asm_stat("write:keptOffsets")
    return [(offs[i], offs[i + 1]) for i in range(0, len(offs), 2)], ent[6]


def _read_jsonl_incremental(path, on_fail=None):
    """The parsed records of `path` (a list, NOT a generator), served append-incrementally per the cache
    contract above. Falls back to a full read on any surprise; [] on any error, like _read_jsonl. `on_fail`,
    when given, is called with the exception for a stat, open or read that raised on a file that EXISTS (any
    OSError but FileNotFoundError): an absent file is a state and answers [] quietly, an unreadable one is a
    failure the caller may count and log (fold_records passes it through as on("fail")). The answer is []
    either way. This is the WHOLE-file read: a tail entry a checkpoint restored is upgraded to the whole file
    first (T323 stage 3); fold_records reads the entry itself through _read_jsonl_entry with tail_ok."""
    _tail_guard(path, "_read_jsonl_incremental")
    ent = _read_jsonl_entry(path, on_fail=on_fail, tail_ok=bool(getattr(_TAIL_OK, "flag", False)))
    _LAST_ENTRY.ent = ent
    return ent[4] if ent is not None else []


_TAIL_OK = threading.local()      # .flag: the calling fold accepts a tail entry (set by fold_records around its read)
_READER_TRACE = bool(os.environ.get("ROMP_READER_TRACE"))   # one stderr line per read that pulled bytes (a diagnosis aid)
_WHOLE_READ_KINDS = ("zero", "rewrite", "guard", "shrunk", "upgrade")   # the reader's kinds that pull a file whole (T384's counter)
_READ_STAGE_FN = [None]           # T401: the kernel's answer to "which stage is the calling thread in" (its stage mark: a job or push
#                                   sub-stage, a handler's route, a judge tier and the rest; None on a thread with no mark), so a whole
#                                   read or a hydration is also counted per (stage, caller)


def set_read_stage_provider(fn):
    """Install fn() -> the calling thread's current stage name or None (the kernel's per-thread stage mark), so the whole-read
    and hydration rows are also counted per stage (T401: the first instrumented boot said jobs.autoNudge read 162.8 MB and
    the callers' rows could not say which of them read it inside that job)."""
    _READ_STAGE_FN[0] = fn


def _read_stage():
    fn = _READ_STAGE_FN[0]
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


_SET_STAGE_FN = [None]            # T401 (5a): the kernel's setter for the calling thread's stage mark, so a module that fans work into
#                                   a pool (the judge's _TimedPool) can carry the submitter's mark into the worker: thread-locals do
#                                   not cross into pool workers on their own


def set_stage_provider(fn):
    """Install fn(name) -> None, the kernel's per-thread stage setter (the pair of set_read_stage_provider): a pool's submit reads
    the submitter's mark through _read_stage and its worker sets the same mark through this, restoring the worker's previous mark
    on exit, so a build or a hydration inside a pool worker counts under the tier that submitted it, never under `none`."""
    _SET_STAGE_FN[0] = fn


def _set_stage_mark(name):
    """Set the calling thread's stage mark through the kernel's setter; a no-op without one (a module used on its own)."""
    fn = _SET_STAGE_FN[0]
    if fn is None:
        return
    try:
        fn(name)
    except Exception:
        pass
_WHOLE_READ_PASSTHROUGH = set()   # the CODE objects of the parse family every walker shares (this module's parse_session, the judges'
#                                   parsed_session, parse_cached and _parse_store, the kernel's _parse, each registered where it is
#                                   defined): the whole-read row names the first caller beyond them, the real walker. Matched by code
#                                   object, never by name (round two, low 3: a local helper named like one of them was skipped)


def _synthetic_scope(fr):
    """A comprehension's, generator expression's or lambda's own frame (a code name in angle brackets other than the module's):
    the attribution walks keep going to the enclosing function. Before Python 3.12 a list, set or dict comprehension runs in
    its own frame (PEP 709 inlines them from 3.12 on); a generator expression and a lambda keep theirs on every version."""
    n = fr.f_code.co_name
    return n.startswith("<") and n != "<module>"


def register_whole_read_passthrough(*fns):
    """Register functions the whole-read attribution walks past (the parse family a walker reaches the reader through)."""
    for fn in fns:
        _WHOLE_READ_PASSTHROUGH.add(fn.__code__)
_LAST_ENTRY = threading.local()   # .ent: the entry the last _read_jsonl_incremental on this thread served


_READ_STRIPES = [threading.RLock() for _ in range(64)]   # per-path serialization of the reads that pull bytes: two threads
#                                                            meeting a file's first read at once (the judges' parse and the pusher's
#                                                            folds at boot) used to read it twice and, with a checkpoint in play, the
#                                                            later store's generation orphaned the earlier restore's pending fold
#                                                            states (review find, 2026-09-11); the second thread now waits and hits.
#                                                            Striped by path hash, so at most one in 64 unrelated files waits behind another.


def _read_stripe(path):
    return _READ_STRIPES[hash(path) % len(_READ_STRIPES)]


def _read_jsonl_entry(path, on_fail=None, tail_ok=False, tail_from=None):
    """The reader's cache entry for `path`, current as of this call (the contract at _read_jsonl_entry_unlocked). A hit
    is served under the cache lock alone; a read that would pull bytes runs under the path's stripe lock and re-checks
    the cache first, so concurrent first reads of one file cost one read and one generation. `tail_from`, a
    (byte offset, record count before it, guard bytes) triple, asks for an entry that holds the records from that
    offset on (the assembly checkpoint's cut, T323 stage 4): an entry whose base is at or before it serves as is; a
    missing or later one is read from the offset after the guard verifies, and a guard mismatch reads whole."""
    _tail_guard(path, "_read_jsonl_entry")
    path = str(path)
    try:
        st = os.stat(path)
    except OSError as e:
        with _JSONL_CACHE_LOCK:
            _cache_pop_locked(path)
        if on_fail is not None and not isinstance(e, FileNotFoundError):
            on_fail(e)
        return None
    with _JSONL_CACHE_LOCK:
        hit = _JSONL_CACHE.get(path)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size and (tail_ok or hit[5] == 0) \
                and (tail_from is None or hit[5] <= tail_from[1]):
            _JSONL_CACHE.pop(path, None)  # reinsert at the LRU tail: a served entry is a USED entry
            _JSONL_CACHE[path] = hit
            return hit
    with _read_stripe(path):
        try:
            return _read_jsonl_entry_unlocked(path, on_fail=on_fail, tail_ok=tail_ok, tail_from=tail_from)
        finally:
            with _JSONL_CACHE_LOCK:
                _READ_IN_FLIGHT.discard(path)      # marked at the read's hit check: cleared whatever road it left by


def _read_jsonl_entry_unlocked(path, on_fail=None, tail_ok=False, tail_from=None):
    """The reader's cache entry for `path`, (mtime, size, offset, tail, records, base, gen, offsets), current as of this call,
    or None when the file is absent or unreadable (`on_fail` as in _read_jsonl_incremental). With `tail_ok` a caller
    accepts a TAIL entry (base > 0: records[0] is the file's record number base), and a first touch with no entry
    consults the file's checkpoint (T323 stage 3): the recorded offset's guard bytes are verified on disk and only
    offset..EOF is read. Without it a tail entry is upgraded to the whole file from zero (same gen: the prefix stood).
    A mismatch anywhere (the guard, a shrink) reads from zero and bumps gen; when the entry came from a checkpoint that is
    a counted fallback. The same size under a new mtime, over the same inode and a WHOLE entry, is no mismatch by itself:
    the guard and the checksum of the held bytes decide it (kind "touched", counted under `touched`), and the entry keeps
    its gen; under a new inode (a replacement) or over a tail entry (a prefix nothing here proves) it reads from zero."""
    _tail_guard(path, "_read_jsonl_entry_unlocked")
    path = str(path)
    try:
        st = os.stat(path)
    except OSError as e:
        with _JSONL_CACHE_LOCK:
            _cache_pop_locked(path)
        if on_fail is not None and not isinstance(e, FileNotFoundError):
            on_fail(e)
        return None
    with _JSONL_CACHE_LOCK:
        hit = _JSONL_CACHE.get(path)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size and (tail_ok or hit[5] == 0) \
                and (tail_from is None or hit[5] <= tail_from[1]):
            _JSONL_CACHE.pop(path, None)  # reinsert at the LRU tail: a served entry is a USED entry
            _JSONL_CACHE[path] = hit
            return hit
        began = (hit, _BUDGET_EVICTED.get(path))   # what this read starts from, before `hit` is reassigned below and
        #                                            before another thread's insert can evict the path mid-read
        _READ_IN_FLIGHT.add(path)                  # a budget eviction meanwhile releases no entry over it (_cache_insert_locked)
    restored = None
    if tail_from is not None and (hit is None or hit[5] > tail_from[1]):
        # the assembly checkpoint's cut: an entry from its offset, when the guard before it stands (else whole)
        t_off, t_base, t_guard = tail_from
        if st.st_size >= t_off:
            keep_gen = hit[6] if hit is not None else _next_gen()   # an earlier start for an existing tail entry keeps its
            hit = restored = (0.0, t_off, t_off, bytes(t_guard), [], int(t_base), keep_gen)   # generation: the folds' cursors stand
    elif hit is None and tail_ok and _CKPT_DIR_FN is not None:
        hit = restored = _checkpoint_entry(path, st)
    try:
        with open(path, "rb") as fh:
            base, gen, done, kind = 0, None, False, "zero"
            crc_start, crc = 0, [0]                       # the checksum of the bytes [0, offset) a WHOLE entry holds; a tail entry
            #                                               carries None (its prefix was never read here, so no checksum proves it)
            grown = hit is not None and (st.st_size > hit[1] or (restored is not None and st.st_size == hit[1]
                                                                    and (st.st_mtime == hit[0] or tail_from is not None)))
            unchanged_tail = (hit is not None and not tail_ok and hit[5] > 0 and st.st_size == hit[1]
                              and st.st_mtime == hit[0])            # a whole reader meets an unchanged tail entry
            touched = (hit is not None and restored is None and st.st_size == hit[1] and st.st_mtime != hit[0]
                       and len(hit) > 10 and hit[5] == 0 and hit[9] == 0 and hit[8] == st.st_ino)   # a whole entry's file, the
            #                                                         same inode and size under a new mtime: the guard, then the
            #                                                         checksum of every byte it holds decide (below). A new inode is
            #                                                         a replacement; a TAIL entry's prefix was never read in this
            #                                                         process, so nothing proves it, and the restore refuses the same
            #                                                         stat for its document (_ckpt_verdict): both read whole
            if grown or unchanged_tail or touched:
                _, _, offset, tail, records, base0, gen0 = hit[:7]
                fh.seek(max(0, offset - len(tail)))
                _count_read(path, len(tail))
                guard_ok = fh.read(len(tail)) == tail
                held_ok = guard_ok and (not touched or _held_crc(fh, path, 0, offset) == hit[10])   # a touch: every held byte
                if held_ok:                               # the file really is our cached prefix + more
                    if tail_ok or base0 == 0:
                        offs = array.array("q", hit[7]) if len(hit) > 7 else array.array("q")
                        crc_start, crc[0] = (hit[9], hit[10]) if (len(hit) > 10 and base0 == 0) else (None, 0)
                        fh.seek(offset)
                        new, offset, nread = _scan_jsonl_stream(fh, offset, offs, limit=max(0, st.st_size - fh.tell()),   # the appended
                                                                crc=crc if crc_start is not None else None)              #  lines, one at a time
                        _count_read(path, nread)
                        records = (records + new) if records else new   # a NEW list — never extend the served one in place
                        base, gen, done = base0, gen0, True
                        kind = "restore" if restored is not None else ("touched" if touched else "grown")
                        if restored is not None and tail_from is None:
                            with _CKPT_LOCK:
                                _CKPT_STATS["restored"] += 1
                    else:                                 # a whole reader over a tail entry: the whole file, same gen
                        fh.seek(0)
                        offs = array.array("q")
                        records, offset, nread = _scan_jsonl_stream(fh, 0, offs, limit=st.st_size, crc=crc)
                        _count_read(path, nread)
                        base, gen, done, kind = 0, gen0, True, "upgrade"
                else:
                    kind = "guard" if not guard_ok else "rewrite"   # prefix changed (or, under a touch, a held byte did) → a
                    #                                                 rewrite → full re-read, a fresh generation
                    if restored is not None and tail_from is None:
                        _ckpt_fallback(path, "guard")
            elif hit is not None:
                kind = "shrunk" if st.st_size < hit[2] else "rewrite"   # shrank (past the offset: a rewrite), or the same size
                #                                                          under a new inode: a replacement (the same inode's
                #                                                          same size under a new mtime takes the guard above)
                if restored is not None and tail_from is None:
                    _ckpt_fallback(path, kind)
            if not done:
                fh.seek(0)
                offs = array.array("q")
                crc_start, crc[0] = 0, 0
                records, offset, nread = _scan_jsonl_stream(fh, 0, offs, limit=st.st_size, crc=crc)   # line by line, to the size the stat saw
                _count_read(path, nread)
                base, gen = 0, _next_gen()                # a from-zero read: a generation no cursor of this path can hold
            tail_from = max(0, offset - _JSONL_TAIL_GUARD)
            fh.seek(tail_from)
            tail = fh.read(offset - tail_from)
            _count_read(path, len(tail))                  # the guard capture is a read too (/perf's count is what was pulled)
            if kind in _WHOLE_READ_KINDS:                 # a whole read: counted by kind and caller on /perf (T384), always on; the
                try:                                      #  frame walk runs only here, on the rare whole read, never on a tail or an
                    fr = sys._getframe(1)                 #  append
                    while fr is not None and (fr.f_code.co_filename == __file__ or fr.f_code in _WHOLE_READ_PASSTHROUGH
                                              or _synthetic_scope(fr)):   #  past this module, the parse family and any comprehension's
                        fr = fr.f_back                    #  or generator expression's own frame, to the walker (review, low 1)
                    who = fr.f_code.co_name if fr is not None else "?"
                except Exception:
                    who = "?"
                with _JSONL_CACHE_LOCK:
                    table = _RECORD_CACHE_STATS.get("wholeReads")
                    if not isinstance(table, dict):       # a harness that zeroes every counter zeroes this one too: a table again
                        table = _RECORD_CACHE_STATS["wholeReads"] = {}
                    wr = table.setdefault("%s<-%s" % (kind, who), {"count": 0, "bytes": 0})
                    wr["count"] += 1; wr["bytes"] += nread    # what the stream read: the whole file, as len(data) was
                    stg = _read_stage() or "none"          # T401: the same read under its stage, so a job's reads name their callers
                    bys = _RECORD_CACHE_STATS.get("wholeReadsByStage")
                    if not isinstance(bys, dict):
                        bys = _RECORD_CACHE_STATS["wholeReadsByStage"] = {}
                    ws = bys.setdefault("%s:%s<-%s" % (stg, kind, who), {"count": 0, "bytes": 0})
                    ws["count"] += 1; ws["bytes"] += nread
            if _READER_TRACE:
                fr, inner = sys._getframe(1), []          # the caller outside this module, and the path through it
                while fr is not None and fr.f_code.co_filename == __file__:
                    inner.append(fr.f_code.co_name)
                    fr = fr.f_back
                who = ("%s:%d" % (fr.f_code.co_name, fr.f_lineno) if fr is not None else "?") + " via " + "<".join(inner[:6])
                sys.stderr.write("reader: %s %s base=%d gen=%d size=%d by %s\n" % (kind, path, base, gen, st.st_size, who))
    except OSError as e:
        with _JSONL_CACHE_LOCK:
            _cache_pop_locked(path)
        if on_fail is not None and not isinstance(e, FileNotFoundError):
            on_fail(e)
        return None
    ent = (st.st_mtime, st.st_size, offset, tail, records, base, gen, offs, st.st_ino, crc_start, crc[0])
    with _JSONL_CACHE_LOCK:
        _READ_IN_FLIGHT.discard(path)                   # done reading: an eviction from here on releases as any other
        if kind == "touched":
            _RECORD_CACHE_STATS["touched"] = _RECORD_CACHE_STATS.get("touched", 0) + 1
        gone = _cache_insert_locked(path, ent, began)   # the count cap and the byte budget, LRU order (hot entries survive
    if gone:                                            #  any cold flood)
        _asm_drop_holding(gone)              # AFTER the cache lock: the assembly entries over what the budget evicted go too
    return ent


def cached_whole_records(path):
    """The records of the reader's WHOLE entry for `path` when one stands at the file's current stat, else None. The entry is
    not touched (its place in the LRU order stays), so a caller that only looks through it leaves the cache as it found it."""
    _tail_guard(path, "cached_whole_records")
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        return None
    with _JSONL_CACHE_LOCK:
        hit = _JSONL_CACHE.get(path)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size and hit[5] == 0:
            return hit[4]
    return None


def read_records_detached(path, on_fail=None):
    """`path`'s records read whole off the disk, line by line as the reader reads a file (_scan_jsonl_stream, to the size a stat
    taken before the read saw), into a list only the caller holds: no reader entry is installed, upgraded or touched, so the
    record cache keeps exactly what it held for the file (a tail a restore left stays a tail, a file it did not hold stays
    out) and nothing stays resident once the caller lets the list go (review round two, 2026-10-03: the comment cut's look
    into a session's first file after a /clear). Nor the file's read stripe: this read fills no cache. The bytes count like
    every reader byte (read_bytes_report), and the read as a whole read on /perf, kind "detached" under its caller. None when
    the file cannot be stat'd, opened or read, after on_fail(error) with the OSError that stopped it when given, so a caller
    can say why (review round three); a read that failed counts no byte and no whole read."""
    _tail_guard(path, "read_records_detached")
    path = str(path)
    try:
        st = os.stat(path)
        with open(path, "rb") as fh:
            records, _end, nread = _scan_jsonl_stream(fh, 0, None, limit=st.st_size)
    except OSError as e:
        if on_fail is not None:
            on_fail(e)
        return None
    _count_read(path, nread)
    try:
        fr = sys._getframe(1)
        while fr is not None and (fr.f_code.co_filename == __file__ or _synthetic_scope(fr)):
            fr = fr.f_back
        who = fr.f_code.co_name if fr is not None else "?"
    except Exception:
        who = "?"
    with _JSONL_CACHE_LOCK:
        table = _RECORD_CACHE_STATS.get("wholeReads")
        if not isinstance(table, dict):
            table = _RECORD_CACHE_STATS["wholeReads"] = {}
        wr = table.setdefault("detached<-%s" % who, {"count": 0, "bytes": 0})
        wr["count"] += 1; wr["bytes"] += nread
        stg = _read_stage() or "none"
        bys = _RECORD_CACHE_STATS.get("wholeReadsByStage")
        if not isinstance(bys, dict):
            bys = _RECORD_CACHE_STATS["wholeReadsByStage"] = {}
        ws = bys.setdefault("%s:detached<-%s" % (stg, who), {"count": 0, "bytes": 0})
        ws["count"] += 1; ws["bytes"] += nread
    return records


_SERVED_RECORDS = threading.local()   # .by_path = {path: records} while a caller serves its own records (serve_records)


class serve_records:
    """`with serve_records({path: records}):` every fold_records over one of these paths ON THIS THREAD folds the given
    records from a fresh state, touching no cursor, reader entry or checkpoint (2026-10-01). A comment thread's popover is
    built over the thread's own records past its fork cut (the kernel's _thread_events), and every fold the chat build
    makes of the transcript (the session meta, the background tasks) must read those, never the parent history the fork
    copied in front of them: a whole read of 40 to 130 MB per new thread. Nested scopes stack."""

    def __init__(self, by_path):
        self.by_path = {str(k): list(v) for k, v in (by_path or {}).items()}

    def __enter__(self):
        for _p in self.by_path:
            _tail_guard(_p, "serve_records")              # a tail served as a whole file is the one thing the scope exists to stop
        self.prev = getattr(_SERVED_RECORDS, "by_path", None)
        _SERVED_RECORDS.by_path = dict(self.prev or {}, **self.by_path)
        return self

    def __exit__(self, *exc):
        _SERVED_RECORDS.by_path = self.prev
        return False


def fold_records(cache, path, init, step, on=None, ckpt=None, drop_after=None):
    """Fold a JSONL file's records into a carried state, APPEND-INCREMENTALLY (issue 903, 2026-09-03):
    the states/transcript readers re-read their whole file behind an (mtime,size) key that every append
    invalidates — O(file) per push for every working session. The reader serves the parsed records of a
    growing file incrementally (the parse reads the same list, so this adds no I/O), and only the appended
    records run through `step`; a rewrite (a shrink, a changed prefix under the reader's guard, a same-size file
    under a new mtime whose held bytes changed or whose inode did) re-folds from record 0. `init()` makes a fresh state; `step(state, record)` returns the
    next state (mutate-and-return is fine: the cached state is deep-copied before folding onto it, so a
    state a caller was handed never changes under it). Returns the state; [] records (a missing or
    unreadable file) fold to init().

    The cursor in `cache` is (count, gen, state): the record count folded and the reader entry's generation
    (T323 stage 3, 2026-09-11; it was (count, last record object, state) gated on the identity of the
    records list's objects, which no checkpoint can carry; the count stays first, as every reader of the
    cursor knew it). A same-gen entry whose count grew is an append; any other gen (a rewrite, a shrink, an
    entry the cache evicted or a failure popped and a later read replaced: every from-zero read takes a
    fresh process-wide generation) refolds. `ckpt`, a name, makes the fold RESUMABLE across processes: its
    cursor is written into the file's checkpoint (checkpoint_write, when it stands at the reader's witness)
    and restored from it at the first fold of the file in a new process, over a TAIL entry that read only the
    bytes past the checkpoint's offset; the fold then steps the tail alone. A refold over a tail entry reads
    the whole file first. Folds without `ckpt` behave as before, over whatever entry the reader holds. The cursor dict is
    bounded by the reader's entries (2026-09-17): past 256 cursors a stepping fold sweeps the ones whose entry left memory or
    was replaced (they could only refold or restore) and keeps every cursor at a standing entry's generation, so live cursors
    number at most _JSONL_CACHE_MAX. It was cleared whole at 256, a figure sized to sessions where the keys are files, and a
    finished agent file's cursor cleared under its standing tail entry read the file whole once per build.

    `on`, when given, is called once per call with the path the fold took: "hit" (the records are the
    cached ones; nothing stepped), "append" (only the records past the cached prefix stepped), "refold"
    (every record stepped: a rewrite, a shrink, or the first fold of this file), "restore" (the cursor came
    from the checkpoint and only the tail past it was stepped) or "fail" (the file exists and its stat, open
    or read raised: the answer is init(), the cache entry for the path is dropped and nothing is memoized, so
    the next call reads again; an ABSENT file is not a failure and folds to init() through the normal path).
    A caller's counters ride it (the kernel's `_states_awaiting_overlay`); the fold itself keeps no counters,
    since one cache dict serves many readers and a caller's counters are locked per reader.
    `drop_after` (the kernel memory work, 2026-09-11): "quiescent" drops the file's records from the shared reader's
    cache once this fold has stepped them, when the file has not changed for _DROP_AFTER_QUIESCENT_S (a subagent
    that returned; the user's rule: nothing stays resident that nobody looks at). The cursor and its checkpoint stand;
    a later fold of an unchanged file re-reads it once, a growing file keeps its records (see _drop_quiescent_entry).
    "returned" (a subagent's OWN transcript, 2026-10-01) does that and more: a fold that finds nothing new drops the
    quiescent entry too, once the file's document stands at the entry's witness (_document_at_witness). A subagent the
    kernel follows live writes its last record while its file is still fresh, so its last stepping fold kept the entry and
    every later fold found nothing new: under "quiescent" its whole entry stayed for the kernel's life. Never for a
    session's leaf, which the judges read whole again if it leaves memory.

    Lives here (moved from the kernel, 2026-09-03) so the judge's readers can fold too — the
    background-task pairing below is shared by both."""
    _tail_guard(path, "fold_records")
    key = str(path)
    served = getattr(_SERVED_RECORDS, "by_path", None)
    if served and key in served:                          # the caller's own records (serve_records): folded fresh, nothing kept
        state = init()
        for r in served[key]:
            if isinstance(r, dict):
                state = step(state, r)
        if on is not None:
            on("served")
        return state
    with _READ_BYTES_LOCK:
        r0 = _READ_BYTES.get(key, 0)                      # what this call reads of the file shows under refolds (T377)
    if ckpt is None:
        ckpt = _FOLD_NAME_OF.get(id(cache))               # a cache named once (name_fold_cache)
    if ckpt is not None:
        _FOLD_REG[ckpt] = cache                           # the newest caller's cursor dict (a test process loads the kernel
        #                                                   several times over one event model; production loads it once)
    failed = []                                           # the reader's failures: a stat, open or read that raised on a
    recs = _tail_read(path, failed)                       # file that exists (an absent file is [] and no failure)
    ent = _pinned_entry(key, recs)                        # the reader's entry for THIS read, pinned by identity: another
    if ent is _UNPINNED:                                  # thread (the judge pool, a handler) may advance the shared entry
        del failed[:]                                     # past our records before we look at its tail, and a newer tail
        recs = _tail_read(path, failed)                   # folded onto an older prefix would skip the records between. A
        ent = _pinned_entry(key, recs)                    # lost pin re-reads once: the newer entry pins cleanly, so the
        if ent is _UNPINNED:                              # answer is current, not a poll behind; lost twice, the fold
            ent = None                                    # answers its own records without a tail, never with the wrong
    if failed:                                            # one. The re-read's verdict is the one that counts.
        cache.pop(key, None)                              # A failed read is never memoized: the next call reads again
        if on is not None:
            on("fail")
        return init()
    base = ent[5] if ent is not None else 0               # no entry (lost twice): the records in hand, whole, no tail

    gen = ent[6] if ent is not None else 0
    total = base + len(recs)
    hit = cache.get(key)
    kind = None
    if ckpt is not None and ent is not None and (hit is None or hit[1] != gen):
        # no cursor, or one at an entry that left memory (the quiescence drop, an eviction) while the file stood: the reader's
        # new entry came from the file's document when it stands, and the document's cursor for this fold serves the fold at
        # that entry (T362: a dropped file's later fold is a tail read, not a whole one); with nothing to restore, a refold.
        # Against a HELD state the document's cursor is taken only when it carries a state of its own (round one, medium): a
        # cursor without one (an over-cap, cold or bare legacy write) would replace the complete state this process holds
        # with a tail-only one that answers empty, and for an idle file nothing would ever heal it; the fold reads whole instead
        restored = _restored_cursor(key, ckpt, ent) if hit is None or _document_carries_state(key, ckpt, ent) else None
        if restored is not None:
            hit = restored
            kind = "restore"
            if hit[2] is _COLD:                           # the cursor without its state: the fold starts at the entry's
                hit = (base, hit[1], init()); kind = "cold"   #  base and steps the tail it holds
                with _CKPT_LOCK:
                    _COLD_FOLDS.add((key, ckpt))          # and stays a tail-only state until a whole refold
    if hit is not None:
        n0, g0, state0 = hit
        if g0 == gen and n0 == total:
            if on is not None:
                on("hit" if kind is None else kind)
            if kind is not None:
                cache[key] = hit                          # a restore at the witness: the cursor stands, nothing stepped
            if drop_after in ("quiescent", "returned") and ent is not None:   # T362: the document is written here too; the
                _drop_quiescent_entry(key, ent, pop="witness" if drop_after == "returned" else False)   #  entry stays, since
            #                                                   nothing was stepped (unless a deferred drop is owed), except under
            #                                                   "returned", where it goes once its document stands at the witness
            return _fold_eof_fragment(key, ent, state0, step)   # unchanged records; a newline-less tail may still sit past them
        if g0 == gen and base <= n0 < total:
            state, start = copy.deepcopy(state0), n0 - base
            kind = kind or "append"
        else:
            kind = None
    if kind is None:
        if base > 0:                                      # a refold needs every record: the entry is a tail, so read whole
            del failed[:]
            ent = _read_jsonl_entry(path, on_fail=failed.append, tail_ok=False)
            if failed:
                cache.pop(key, None)
                if on is not None:
                    on("fail")
                return init()
            recs = ent[4] if ent is not None else []
            gen = ent[6] if ent is not None else 0
            total = len(recs)
        state, start, kind = init(), 0, "refold"
        if ckpt is not None:
            with _READ_BYTES_LOCK:
                got = _READ_BYTES.get(key, 0) - r0
            with _CKPT_LOCK:
                _COLD_FOLDS.discard((key, ckpt))          # every record stepped: the state is complete again
                _COLD_REASONS.pop((key, ckpt), None); _COLD_OVER_KB.pop((key, ckpt), None)
                if got > 0:                               # a refold that read (the whole file, over a tail entry or from zero): named
                    rf = _CKPT_STATS["refolds"].setdefault(ckpt, {"count": 0, "bytes": 0})   #  and weighed per fold on /perf (T377).
                    rf["count"] += 1; rf["bytes"] += got   #  Diagnostic: the delta is over the path's process-wide counter, so another
        #                                                    thread's read of the same file inside this call lands in it
    for r in recs[start:]:
        if isinstance(r, dict):
            state = step(state, r)
    if len(cache) > 256:
        # Past 256 cursors the dict is swept of the ones that cannot serve a hit (2026-09-17). It was CLEARED here, "bounded by
        # the session count", but a fold dict is keyed per FILE, and the chat build's agent-gist dict holds one cursor per agent
        # transcript the board shows, finished ones included (a live board: 315), so a running agent's append or a new agent's
        # first fold cleared it at every build; a finished file's cursor gone while its restored tail entry still stood had
        # nothing to restore from (a document's restore is taken once per pending record), so the next fold read the file
        # whole, the drop popped the entry and rewrote the document, the fold after restored again: one whole read per build
        # per finished file (live: 2,369 refolds, 1.24 GB read in an hour). A cursor is dead the moment the reader's entry for
        # its path left memory (the quiescent drop, an eviction, a failure's pop) or was replaced (every from-zero read is a new
        # gen): those go, and would have refolded or restored anyway; a cursor at a standing entry's gen is exactly what makes
        # a finished file a stat, and stays. The bound is the reader's: live cursors number at most its standing entries
        # (_JSONL_CACHE_MAX); dead ones are swept at the next stepping fold past 256. One pass of dict lookups under the
        # reader's lock (its cheap-ops discipline). The dicts are shared across threads and, like every cursor dict here,
        # unlocked (the store at this function's end, a failure's pop): the pass walks a snapshot and pops a cursor only while
        # the dict still holds the very one it judged, which narrows the window but does not close it (review 2026-09-17: the
        # check and the pop are two operations). A concurrent fold's fresh cursor stored between them is popped, and that costs
        # its file one refold at its next fold, the price a lost cursor has always had here, never a raise. Not worth a lock: a
        # same-class race stands without the sweep (two threads folding one path, last store wins), and the module's rule for
        # its shared dicts is that a lost race degrades to a re-read.
        with _JSONL_CACHE_LOCK:
            dead = [(k, cur) for k, cur in list(cache.items())
                    if k != key and (_JSONL_CACHE.get(k) is None or _JSONL_CACHE[k][6] != cur[1])]
        for k, cur in dead:
            if cache.get(k) is cur:
                cache.pop(k, None)
    cache[key] = (total, gen, state)
    if ckpt is not None:
        with _CKPT_LOCK:
            _FOLD_DIRTY.add(key)
    if on is not None:
        on(kind)
    out = _fold_eof_fragment(key, ent, state, step)
    if drop_after in ("quiescent", "returned") and ent is not None:
        _drop_quiescent_entry(key, ent)
    return out


def _drop_quiescent_entry(key, ent, pop=True):
    """drop_after="quiescent" (the kernel memory work, 2026-09-11): a file unchanged for _DROP_AFTER_QUIESCENT_S is one
    whose writer has finished (a subagent that returned), and its records are not kept in the shared reader's cache
    once a fold has stepped them: the fold's cursor (and its checkpoint) stands, the file's bytes leave memory. Only the
    very entry this fold read is dropped (another thread's newer entry is left alone); a file still changing keeps its
    records, since its next append would otherwise re-read it whole. Without `pop` (a fold that stepped nothing: a hit or
    a restore at the witness) only the T362 write below runs and the entry stays, as it always has on that path. `pop`
    "witness" (a drop_after="returned" fold that stepped nothing, 2026-10-01): the write runs as without `pop`, and the
    entry then goes only when _document_at_witness says the next fold restores from the document over a zero-byte tail;
    such a drop is never owed for the budget nor taken under a hold (the next quiet fold takes it). Returns True when the
    document was written here."""
    try:
        if time.time() - float(ent[0]) < _DROP_AFTER_QUIESCENT_S:
            return False
    except Exception:
        return False
    held = getattr(_DROP_HOLD, "held", None)
    if held is not None:                                  # the converge pass is healing and priming this leaf on this thread: held,
        held[key] = held.get(key, False) or pop is True   #  paid once after (pay_held_drops), with the pop if any fold stepped
        return False
    with _CKPT_LOCK:
        if key in _DROP_OWED:                             # a drop deferred for the budget is taken at the next fold over the file
            _DROP_OWED.pop(key, None); pop = True         #  (whichever path that fold takes) or at the next cycle's start
    # T362: the entry a quiescent-drop fold ends over is a read that already happened (the boot's, by whichever fold read the
    # file whole); if the file's document lacks something this process holds (the one rule), it is written NOW, before any
    # pop, from that read: the idle sessions' documents converge here, where no settle reaches them and the converge pass must
    # refuse them (a heal on the cycle re-read them whole). The write, its budget and its deferral, and the pop through the
    # assembly pin are _write_then_drop's, shared with the explicit release at the end of a file's use (release_records).
    return _write_then_drop(key, ent, pop=pop)[0]


def _document_at_witness(key, ent):
    """Whether a fold that stepped nothing may drop `ent` (drop_after="returned"): the entry holds records (an empty restored
    tail frees nothing, and dropping it would make every later fold a restore), the path is not dirty, and the file's fold
    document stands at the entry's witness, the very count, offset, guard bytes, size and mtime, carrying a complete state
    at that count for every fold whose cursor sits at the entry's generation. The next fold over the file then restores
    from the document over a zero-byte tail, never a whole read. False with checkpoints off (no document), for a document
    behind the entry (a lagging fold's cut), and for a cursor of this generation that lags or is missing from the document
    (an over-the-cap or cold fold included). A cursor of another generation is not checked here: when the document carries
    its state at a count inside the entry's records, the cut sits at that count and the document is not at the witness;
    when it does not, its fold reads the file once at its next run, as any fold does after a drop. False too while a
    parse's assembly entry holds this very records list (the subagent viewer's): the pop would free nothing, and the
    parse's next rebuild would read the file whole and parse it in full. The test is by identity, so it holds only for a
    parse rebuilt since the file's last append. A parse built before it keeps an older list at the same generation, a
    whole entry, so the assembly pin holds the drop back (_write_then_drop, counted under recordCache.pinnedKept; under
    droppedHeld when the pin check raises). The viewer's last close releases that parse wherever the close lands
    (the kernel's _release_subagent_view, called by closeSubagent, by the socket's teardown, and by the pusher's re-check
    after a render, one that raised included), so it does not hold the records past the viewer; a release that raises is
    counted and said (release_failed) and leaves the parse to the assembly cache's bound. A reader that wants the whole
    file and holds no such entry pays one whole read after each drop, so a reader of agent files that runs every cycle
    takes a tail (the folds' rule)."""
    try:
        recs = ent[4]
        if not recs:
            return False
        with _CKPT_LOCK:
            if key in _FOLD_DIRTY:
                return False
        doc = _ckpt_doc_shared(key)
        if not isinstance(doc, dict):
            return False
        total, gen = int(ent[5]) + len(recs), ent[6]
        if (int(doc["count"]), int(doc["offset"]), int(doc["size"]), float(doc.get("mtime") or 0), doc.get("guard") or "") \
                != (total, int(ent[2]), int(ent[1]), float(ent[0]), bytes(ent[3]).hex()):
            return False
        folds = doc.get("folds") or {}
        for name, cache in list(_FOLD_REG.items()):
            cur = cache.get(key)
            if cur is None or cur[1] != gen:
                continue
            f = folds.get(name)
            if cur[0] != total or not (isinstance(f, dict) and "state" in f and int(f.get("count", -1)) == total):
                return False
        return not _asm_holds_records(key, recs)
    except Exception as e:
        note_swallowed("dropWitness", e, key)             # the entry stays: memory, never a wrong answer
        return False


def _asm_holds_records(key, recs):
    """Whether an assembly entry's adapter holds `recs`, this very list, as `key`'s records (the subagent viewer's parse of an
    agent file). One identity test per entry, on a snapshot taken under _ASM_LOCK alone."""
    with _ASM_LOCK:
        entries = list(_ASM_CACHE.values())
    for e in entries:
        src = getattr(e.get("ad"), "_src", None) if isinstance(e, dict) else None
        if isinstance(src, dict) and src.get(key) is recs:
            return True
    return False


_UNPINNED = object()


def _tail_read(path, failed):
    """fold_records' read: the whole-list reader (the seam every fold shares, so a test's stub or counter on
    _read_jsonl_incremental still sees every fold read) with the tail flag raised, so a checkpoint-restored tail
    entry is served instead of upgraded to the whole file."""
    _TAIL_OK.flag = True
    try:
        return _read_jsonl_incremental(path, on_fail=failed.append)
    finally:
        _TAIL_OK.flag = False


def _pinned_entry(key, recs):
    """The shared reader's cache entry for `key` if it still describes the read that returned `recs` (its
    records list IS `recs`), None when the reader holds nothing for the path, _UNPINNED when another thread
    has advanced the entry past that read. The entry the reader served on this thread is checked first
    (T323 stage 3: an entry evicted from the cache between the read and the pin still describes the read)."""
    last = getattr(_LAST_ENTRY, "ent", None)
    if last is not None and last[4] is recs:
        return last
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(key)
    if ent is not None and ent[4] is recs:
        return ent
    if ent is None and not recs:
        return None                                        # the reader holds nothing because there is nothing (or no file)
    return _UNPINNED                                       # advanced past our read — or evicted from under it (LRU)


_TRAILING_CACHE = {}          # path -> ((mtime, size, offset), record|None): the tail verdict per file version, so a
#                               torn tail that never completes (a CLI killed mid-write) costs one read, not one per poll.
#                               LRU at _TRAILING_CACHE_MAX (the least recently used entry goes, never the whole memo:
#                               a clear-at-cap above the cap re-reads every pending tail per poll — see _JSONL_CACHE_MAX)
_TRAILING_CACHE_MAX = 256
_TRAILING_LOCK = threading.Lock()   # the memo is shared by the pusher, the handler threads and the judge pools: the
#                                     LRU pop/reinsert/evict are several dict ops, not one (an unlocked evict raised
#                                     KeyError under two inserters at the cap, reproduced 2026-09-04)


def _trailing_record(path, ent):
    """The complete JSON record sitting past _read_jsonl_incremental's consumed offset WITHOUT its newline
    yet, or None. `ent` is the reader's cache entry for the read being folded (mtime, size, offset, tail,
    records; None when there is none). The incremental reader leaves such a line unconsumed by design (a
    writer caught mid-append must not enter the cache torn); the readers folded here used to walk the text
    to EOF and saw a final newline-less record — so the fold reads it provisionally (see _fold_eof_fragment).
    The verdict is memoized per (mtime, size, offset): an unchanged file answers from memory, whether its
    tail was a record or a torn fragment, with no I/O and no failed parse per poll (review find, 2026-09-04:
    a CLI killed mid-write left a torn line that every fold reader re-read and re-failed on every push)."""
    if ent is None or ent[1] <= ent[2]:                   # nothing past the consumed offset (the common case)
        return None
    ver = (ent[0], ent[1], ent[2])
    with _TRAILING_LOCK:
        hit = _TRAILING_CACHE.get(path)
        if hit is not None and hit[0] == ver:
            _TRAILING_CACHE.pop(path, None)               # a served verdict is a used one: to the LRU tail
            _TRAILING_CACHE[path] = hit
            return hit[1]
    o = None
    try:
        with open(path, "rb") as fh:
            fh.seek(ent[2])
            frag = fh.read(ent[1] - ent[2]).strip()
        _count_read(path, ent[1] - ent[2])
        if frag and b"\n" not in frag:
            o = json.loads(frag.decode("utf-8", "replace"))
    except (OSError, ValueError):
        o = None
    o = o if isinstance(o, dict) else None
    with _TRAILING_LOCK:                                  # the read above ran unlocked; two readers of one version
        _TRAILING_CACHE.pop(path, None)                   # agree on the verdict, so the last writer wins harmlessly
        while len(_TRAILING_CACHE) >= _TRAILING_CACHE_MAX:
            del _TRAILING_CACHE[next(iter(_TRAILING_CACHE))]   # the least recently used goes first
        _TRAILING_CACHE[path] = (ver, o)
    return o


def _fold_eof_fragment(key, ent, state, step):
    """fold_records' answer with a newline-less final record folded in PROVISIONALLY: applied to a copy,
    never to the cached state, so the record is folded for good exactly once — when its newline lands and
    the incremental reader serves it as a record. Keeps the old whole-file readers' answer (they saw that
    final line) without giving up the cache's torn-write safety. `ent` is the reader's entry for the very
    read being folded (fold_records pins it by the identity of its records list), never a fresh lookup."""
    frag = _trailing_record(key, ent)
    if frag is None:
        return state
    return step(copy.deepcopy(state), frag)


def _is_tool_result(r):
    """True when a user-typed record is a machine-made tool_result (its first content block),
    not a person's prompt — the discriminator the eclipse stand-down needs."""
    c = _content((r or {}).get("message"))
    return bool(c) and isinstance(c[0], dict) and c[0].get("type") == "tool_result"


def _content(message):
    """The content[] of a message, normalized: a bare string becomes one text block,
    so every atom carries a list of blocks (the 'one atom, many blocks' shape)."""
    if not isinstance(message, dict):
        return []
    c = message.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}] if c.strip() else []
    return c if isinstance(c, list) else []


def _text_of(blocks):
    """Joined text of the text blocks in a content list (thinking/tool_use/tool_result skipped)."""
    return " ".join(b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text").strip()


def _block_types(blocks):
    return [b.get("type") for b in blocks if isinstance(b, dict)]


def _is_real_prompt(blocks):
    """A user line is a genuine PROMPT (vs a tool_result-only harness line) when it
    carries any text. A tool_result-only line has no text block."""
    return bool(_text_of(blocks))


def _has_tool_result(blocks):
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)


def _norm_message(message):
    """The Anthropic message object kept verbatim where it exists (role, content blocks,
    model, usage, stop_reason, id). Content is normalized to a block list. The id is the API
    message's: the CLI writes one record per content block, every record of one message carrying
    the same id and the same final usage, so a per-message figure (the chat's thinking tokens) is
    counted once by it."""
    if not isinstance(message, dict):
        return None
    out = {"role": message.get("role"), "content": _content(message)}
    for k in ("model", "stop_reason", "usage", "id"):
        if message.get(k) is not None:
            out[k] = message[k]
    return out


def postal_pairs(text):
    """[(mid, kind), ...] in delivery order; kind is "" when the sender declared none (CLI mail).
    Position is the only thing that pairs them — the markers carry no cross-reference."""
    pairs = []
    for typ, val in _POSTAL_ANY_RE.findall(text or ""):
        if typ == "id":
            pairs.append([val, ""])
        elif pairs and not pairs[-1][1] and val in _POSTAL_KINDS:
            pairs[-1][1] = val                   # binds to the id it follows, never a later one
    return [(a, b) for a, b in pairs]


# ───────────────────────── authorship (the one real addition over the stream) ─────────────────────────
# A user atom's author is the ONE field the stream lacks: it cannot tell a peer romp
# message from a human prompt (both are `user` messages). Everything else the old
# typed/queued/absorbed/decision/postal enum encoded is derived from position
# (opener vs mid-turn) and content, not stored here.
def author_of(blocks, prompt_source, postal_index, sdk_human=False, origin=None):
    """human | romp | sdk | system | teammate | {"peer": <rompUuid|None>, "mid": <id>, "kind": <kind|"">} | None.

    Order matters: the postal marker wins over promptSource (a delivered message can
    arrive with any promptSource). A tool_result-only user atom has no author.

    origin: the record's own `origin` field — the CLI's PROVENANCE stamp on every turn it injects on its
    own (claude_agent_sdk MessageOrigin: kind task-notification / peer / coordinator / channel /
    auto-continuation / observer / unclassified…; "human" or absent for the person's own prompt). Read
    FIRST (the user 2026-09-07): the text shapes below are how each kind happened to be worded by one CLI
    build, and 2.1.263 reworded the background-task notification (a preamble ahead of the tag), which
    made every one of them a blue human bubble. The field is the authoritative source; the text tests
    stay as the fallback for records without it. Any kind that is not "human" is NOT the human, whatever
    sdk_human says — that flag exists only to read an UNSTAMPED "sdk" prompt as the composer's.

    A peer author carries the marker it resolved, not just the sender: one delivery can hold
    several messages, so every later reader (the judge's _seg_peer / _seg_peer_kind) must be
    told WHICH one this author came from rather than re-scanning and picking a different one.

    sdk_human: this session is SDK-backed, so its HUMAN input arrives over the programmatic
    stream-json channel as promptSource "sdk" (the human typed it in the composer). romp's own
    injections still carry the romp-injected marker and peers the postal marker (both handled
    above), so an UNMARKED "sdk" prompt here is the human → render it as the blue human bubble.
    Off (the default) elsewhere, where "sdk" means a genuine programmatic/autonomous injection."""
    okind = origin.get("kind") if isinstance(origin, dict) else None
    osub = origin.get("subkind") if isinstance(origin, dict) else None
    # A peer STAMP is kind "peer" OR a task-notification whose subkind is "peer-send-message", the SDK's other
    # task-channel subkind (claude_agent_sdk TaskNotificationOriginSubkind): a message sent from another of the
    # user's sessions. Another session's words, classified exactly like kind "peer": never a background
    # task's report, so it neither opens nor folds into a task turn, and the task channel's own preamble on
    # its text cannot re-read it as one (review find, 2026-09-09, on #1099).
    peer_stamp = okind == "peer" or (okind == "task-notification" and osub == "peer-send-message")
    if okind == "task-notification" and not peer_stamp:
        if osub == "scheduled-trigger":
            return "sdk"                          # a scheduled task's fired PROMPT: programmatic, but real work follows
        return "system"                           # a background task's completion → folds in, never a goal
    text = _text_of(blocks)
    if text:
        if SYSTEM_WRAPPER_RE.match(text) and not peer_stamp:   # a harness <task-notification> / <system-reminder> / its preamble
            return "system"                       # → author 'system' so _is_opener folds it in, never a goal
        if SCHEDULED_PREAMBLE_RE.match(text) and not peer_stamp:   # a scheduled task's fired prompt, unstamped → programmatic prompt
            return "sdk"
        if TEAMMATE_MSG_RE.match(text):           # Claude Code's native agent-to-agent delivery, not the user typing
            return "teammate"                     # → its own collapsed chat card; a non-opener (like 'system'), so
            #   high-frequency coordination pings never pin a junk goal. Checked before the postal marker: the
            #   OUTER native wrapper wins even if a forwarded body happened to carry a romp-msg-id.
        pairs = postal_pairs(text)
        if pairs:
            # LAST first: the delivery appends its markers AFTER the body, so when a body itself
            # carries one — a peer forwarding mail it received, an agent quoting its own — the real
            # sender's marker is the trailing one. Taking the first let the quoted id name the author.
            peer, mid, kind = None, pairs[-1][0], pairs[-1][1]
            for m, k in reversed(pairs):
                if postal_index.get(m):
                    peer, mid, kind = postal_index[m], m, k
                    break
            # The chosen marker travels WITH the author, so the judge reads the same one rather than
            # re-scanning and landing on a different message.
            return {"peer": peer, "mid": mid, "kind": kind}
        if ROMP_INJECT_RE.search(text):           # romp pasted this into the pane (a feed nudge) → system, not human
            return "romp"
    if okind and okind != "human":
        # Stamped as injected, and no romp/postal marker claimed it above: a peer session's (or an
        # in-process background subagent's) message, by kind "peer" or the task channel's "peer-send-message"
        # subkind → the teammate card; everything else the CLI injects (coordinator, channel,
        # auto-continuation, observer…) → a programmatic prompt.
        return "teammate" if peer_stamp else "sdk"
    if prompt_source == "sdk":
        return "human" if sdk_human else "sdk"
    if prompt_source == "system":
        return "system"
    if prompt_source in ("typed", "queued"):
        return "human"
    # promptSource absent: a genuine prompt with no SDK/system/postal signal is presumed
    # human (a typed prompt the harness recorded without the field — ~10% on disk). A
    # tool_result-only line (no text) gets no author.
    if _is_real_prompt(blocks):
        return "human"
    return None


def _author_final(author, text):
    """True when a postal author can never change again: the TRAILING marker resolved. author_of
    scans markers tail-first for the first index hit, so a resolution landing later than the
    CHOSEN marker re-decides the author — a record whose quoted/forwarded earlier marker resolved
    while its trailing real one hadn't yet gets a plausible-but-PROVISIONAL author, not just a
    peer-None one (the postal log is append-only and first-wins per id, so resolutions only
    arrive, never change). The assembly heal re-emits any non-final author each visit until the
    tail settles; non-postal authors are trivially final."""
    if not isinstance(author, dict):
        return True
    pairs = postal_pairs(text)
    return bool(author.get("peer") is not None and pairs and author.get("mid") == pairs[-1][0])


def parse_teammate_message(text):
    """Split a native Claude Code teammate-message delivery (see TEAMMATE_MSG_RE) into per-sender blocks
    for the chat to render its own way: a list of {"id", "summary", "body"}. `color` is DELIBERATELY
    dropped — these get a neutral treatment, NOT the per-peer color chrome of a romp postal card, so the
    two are tellable apart (the user 2026-07-05). The fixed "permission laundering" boilerplate and the
    <prompt> wrapper fall away naturally (only the <teammate-message> block contents are kept). Returns []
    when there are no blocks (a delivery with no parseable block → caller shows the raw text)."""
    out = []
    for attrs, body in TEAMMATE_BLOCK_RE.findall(text or ""):
        a = dict(re.findall(r'(\w+)="([^"]*)"', attrs))
        out.append({"id": (a.get("teammate_id") or "").strip(),
                    "summary": (a.get("summary") or "").strip(),
                    "body": body.strip()})
    if not out:
        # the 2.1.263 cross-session envelope: <cross-session-message from="…" [from-name="…"]>body</…>. The
        # sender's display name when the CLI gave one, else its address; the CLI's "This came from another
        # Claude session…" boilerplate around the block falls away like the teammate wrapper's.
        for attrs, body in CROSS_SESSION_BLOCK_RE.findall(text or ""):
            a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
            out.append({"id": (a.get("from-name") or a.get("from") or "").strip(),
                        "summary": "", "body": body.strip()})
    return out


# The `origin` keys the atom carries — the SDK's documented per-kind fields (claude_agent_sdk MessageOrigin).
# `body` is the peer message with the envelope stripped, "byte-exact with what the model saw" — the SDK
# says to render it rather than re-parse the text, so it rides (capped like every other body).
_ORIGIN_KEYS = ("kind", "subkind", "name", "from", "server", "senderTaskId", "body")


def _record_origin(rec):
    """A transcript record's provenance stamp as the atom carries it, or None. Two record shapes: a user
    record's top-level `origin`, and a queued_command ATTACHMENT's (the mid-turn splice), which either
    carries `origin` outright or says `commandMode: "task-notification"` — the same fact, older spelling."""
    o = rec.get("origin")
    if not isinstance(o, dict):
        att = rec.get("attachment") if rec.get("type") == "attachment" else None
        if isinstance(att, dict):
            o = att.get("origin")
            if not isinstance(o, dict) and att.get("commandMode") == "task-notification":
                o = {"kind": "task-notification"}
    if not isinstance(o, dict) or not isinstance(o.get("kind"), str) or o["kind"] == "human":
        return None            # a "human" stamp says nothing the author does not; only INJECTED kinds ride
    out = {k: o[k] for k in _ORIGIN_KEYS if isinstance(o.get(k), str)}
    if out.get("body") and len(out["body"]) > _RESULT_CAP:
        out["body"] = out["body"][:_RESULT_CAP]
    return out


def strip_harness_preamble(text):
    """(text without the CLI's harness preamble paragraph, the paragraph) — for a record ALREADY known
    not to be the human's. The paragraph is the CLI's fixed note to the model ("[SYSTEM NOTIFICATION -
    NOT USER INPUT] This is an automated background-task event…"); the chat shows the record under its
    source label and keeps the paragraph one click away in the card's fold, never as the message."""
    if not text:
        return text, ""
    m = HARNESS_PREAMBLE_PARA_RE.search(text)
    if not m:
        return text, ""
    pre = m.group(0).strip()
    rest = (text[:m.start()].rstrip() + "\n\n" + text[m.end():].lstrip()).strip()
    return rest, pre


def injected_source(author, origin, reminders=(), preamble=""):
    """The SOURCE a harness-injected user-role record is shown under (the chat's notice head), or None for
    the human's own words. `kind`: "subagent" (a background agent came to rest — name = its description,
    the one the model itself sees in the notification's <summary>), "task" (a background command),
    "system" (a harness notice: a bare reminder, a scheduled task's firing, an automatic continuation…),
    "peer" (another session / the coordinating session / an MCP channel — postal has its own card and
    never reaches here). Pure: the kernel's chat build and its tests read it the same way.

    preamble: the harness paragraph strip_harness_preamble lifted off the record's text, when there was
    one. It is the fallback that names an UNSTAMPED scheduled firing (an older CLI, the live echo): the
    stamped path labels it "Scheduled task", and the text path must not hand the same record back as an
    unlabelled neutral note (review find, 2026-09-09, on #1099).

    The user asked whether the model can tell WHICH subagent a notification came from (2026-09-07): it
    can — the notification names the task id, the launching tool-use id and the agent's description in
    its <summary> ("Agent \"<description>\" came to rest"), so the head shows at least that description."""
    if author == "human" or author == "romp" or isinstance(author, dict):
        return None
    okind = (origin or {}).get("kind") if isinstance(origin, dict) else None
    sub = (origin or {}).get("subkind") if isinstance(origin, dict) else None
    # kind "peer", or the task channel's "peer-send-message" subkind (a message from another of the user's
    # sessions): the peer notice, never a finished background task, so the notification-naming loop below
    # never runs for it either (review find, 2026-09-09, on #1099). Same rule as author_of's peer_stamp.
    peer_stamp = okind == "peer" or (okind == "task-notification" and sub == "peer-send-message")
    if peer_stamp:
        # a "peer-send-message" stamp carries none of kind "peer"'s sender fields, so it reads "another session"
        return {"kind": "peer", "name": origin.get("name") or origin.get("from") or "another session",
                "subagent": bool(origin.get("senderTaskId"))}
    # The notification names its task only when the RECORD is the notification: a system-authored (or
    # author-less live) record, or one stamped task-notification. An unstamped programmatic prompt that
    # merely arrived with a notification attached keeps today's neutral note with the card nested under it.
    for r in (reminders or ()) if (author in ("system", None) or okind == "task-notification") else ():
        note = _parse_task_notification("<task-notification>%s</task-notification>" % r)
        if not note or not (note.get("summary") or "<task-id>" in r):
            continue                     # a plain reminder, not a task's notification (the parser defaults a status)
        summ = note.get("summary") or ""
        m = re.search(r'Agent\s+"([^"]+)"', summ)
        if m:
            return {"kind": "subagent", "name": m.group(1), "status": note["status"]}
        m = re.search(r'Background command\s+"([^"]+)"', summ)
        if m:
            return {"kind": "task", "name": m.group(1), "status": note["status"]}
        return {"kind": "task", "name": re.sub(r"\s+(?:came to rest|completed).*$", "", summ).strip() or "task",
                "status": note["status"]}
    if okind == "task-notification":
        if sub == "scheduled-trigger":
            return {"kind": "system", "label": "Scheduled task"}
        return {"kind": "task", "name": "background task", "status": ""}
    if okind == "coordinator":
        return {"kind": "peer", "name": "the coordinating session", "subagent": False}
    if okind == "channel":
        return {"kind": "peer", "name": origin.get("server") or "a channel", "subagent": False}
    if okind == "auto-continuation":
        return {"kind": "system", "label": "Automatic continuation"}
    if okind in ("observer", "observer-activity"):
        return {"kind": "system", "label": "Observer report"}
    if okind and okind != "human":
        return {"kind": "system", "label": okind.replace("-", " ")}
    if author == "system":
        return {"kind": "system", "label": "System reminder"}   # a bare <system-reminder> record, unstamped
    if preamble and SCHEDULED_PREAMBLE_RE.match(preamble):
        # unstamped, but the lifted preamble itself says what fired the prompt: the same label the stamped
        # path gives it, with the preamble kept on the event for the card's fold: nothing the text path
        # labelled before becomes an unlabelled note (review find, 2026-09-09, on #1099)
        return {"kind": "system", "label": "Scheduled task"}
    return None   # an unstamped 'sdk' prompt keeps today's neutral note; a teammate has its own card


# ═════════════════════════ FILE ADAPTER: graph recovery, quarantined ═════════════════════════
def _th(text):
    """The carry's text key: sha1 of the text. The dedup sets compare for equality only, so a hash serves them, and
    the assembly checkpoint can carry the sets without carrying every prompt ever typed (T323 stage 4)."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _emit_state():
    """The emit layer's cross-record carry — everything the per-record emit reads that EARLIER
    records established. A full parse starts one empty and folds every kept record through it;
    the assembly cache (_assemble) persists it per session and folds only appended records.
    Field-for-field these are the old atoms() locals, hoisted so both paths share one
    implementation and cannot drift."""
    return {"replay": set(),           # uuids classified post-compaction replays (never atoms)
            "seen_exact": set(),       # (second, text hash) of every kept user text — verbatim re-writes
            "seen_text": set(),        # text hashes ever seen — the restore-burst dedup's memory
            "compacted": False, "restoring": False, "last_boundary": None,
            "summaries": {},           # boundary uuid -> its compaction summary text
            "skill_ids": set(),        # Skill tool_use ids among kept assistants
            "cmd_names": {},           # promptId -> {command names} (slash-invocation twins)
            "skill_loads": {},         # wrapper uuid -> skill name: the harness's own skill loads the emit skips (T333)
            "absorbed_keys": set(),    # _absorbed's (ts, hash of the collapsed text) dedup memory
            "postal_miss_rec": set(),  # kept records whose postal marker missed the index
            "postal_miss_att": set(),  # absorbed (ts, hash of the collapsed text) keys likewise
            "max_ppt": 0.0}            # chronological watermark of folded pre-pass-relevant records


def _chrono(ad, uuids):
    """`uuids` in the emit layer's canonical order — ascending (timestamp, read order), exactly
    the key the replay pre-pass has always walked in."""
    return sorted(uuids, key=lambda x: (ad.ts_of.get(x) or 0, ad.seq_of.get(x, 0)))


class FileAdapter:
    """Rebuilds the active linear message sequence from the append-only on-disk graph.

    Reads every candidate transcript once into a uuid index, then does a DIRECTED
    backward walk from the leaf via parentUuid (crossing files on resume), so the
    active path is exactly the leaf->root ancestors. Rewound branches are non-
    ancestors and drop out for free; `/clear` leaves no parent link so the walk
    stops there and pre-clear history drops out naturally."""

    def __init__(self, candidate_files, leaf_path, leaf_override=None, resume_links=None, seed=None, records=None):
        # records: {path: [record, ...]} for a file the CALLER read (a comment thread's own records past its fork cut,
        # parse_session's tail_records): ingested as given, never through the reader's cache, so no reader entry is minted
        # at an offset whose record count before it nobody counted. Its _src_keys row is ("private",): no fold gate or
        # document writer ever sees an adapter built this way (parse_session returns before either)
        self.seed = seed         # the assembly checkpoint's pre-cut graph (T323 stage 4): uuids, verdicts, types, the kept
        #                          chain, the gate facts and the read-counter floor of every record before the cut, so the
        #                          walks and gates over the tail answer as the whole graph would; None for a whole read
        self.resume_links = dict(resume_links or {})   # {to_fsid: from_fsid} — recorded resume forks (states/ rows)
        self.by_uuid = {}        # uuid -> record
        self.fsid_of = {}        # uuid -> transcript file stem (provenance / click-to-open)
        self.seq_of = {}         # uuid -> global read order (tie-break for equal timestamps)
        self.parent_of = {}      # uuid -> parentUuid (or logicalParentUuid, for the compaction stitch)
        self.qatts = []          # queued_command attachment records IN FILE ORDER — the CLI's own splice
                                 #   witnesses: {uuid, ts (the ENQUEUE timestamp the record carries),
                                 #   text (full prompt, markers intact), seq}
        self.leaf_uuid = None
        self._seq = 0            # global read-order counter — continued by assembly folds
        self._seq_ts = []        # (seq, repaired ts) per uuid-bearing record, in read order — sorted by
        #                          construction, so _landing_t bisects it for a record's file-order
        #                          predecessor without a per-lookup scan or a per-fold sort
        self.ts_of = {}          # uuid -> repaired epoch seconds — THE timestamp every consumer
        #                          reads (_chrono, the emit, the boundary splice). A record whose
        #                          stamp does not parse borrows the last good stamp seen in file
        #                          order (its real neighbor): the atom stays VISIBLE at a truthful
        #                          adjacent time instead of t=None TypeError-ing segment_turns'
        #                          sort and taking the whole session view down (T210). Fold-safe
        #                          with no carry: _ingest is the one shared path and re-ingestion
        #                          replays the same order, so fold and full repair identically.
        self._last_ts = 0        # the borrow source — last parseable stamp ingested
        # Graph-level facts the assembly fast-path gates read, collected at INGEST so they cover
        # the whole graph (kept or not): every user record's promptId (a delta record repeating
        # one forces a full parse — command twins/episodes key on it), every assistant Skill
        # tool_use id (a delta invocation an old payload record already references forces full),
        # and every parent target that dangled at ingest time (a delta record RESURRECTING one
        # would rebind repaired stitches, so it forces full).
        self.prompt_ids = set()
        self.boundary_pids = set()
        self.summary_pids = {}   # compaction summary promptId -> the uuids of the summaries THIS adapter ingested wearing it (a
        #                          restored adapter's tail alone; the seed's boundary_pids stands for the rest): what the fold's
        #                          promptid gate reads to tell an attached pair's pid from an adoptable one (_asm_pid_refusal)
        self.skill_use_ids = set()
        self.src_tool_links = set()
        self.dangling = set()
        self._src = {}           # path -> the exact records list ingested (the jsonl cache's list
        #                          object; identity anchors the fold's pure-append gate)
        leaf_stem = Path(leaf_path).stem
        # read the leaf last so its trailing uuid wins as the walk anchor even if a
        # sibling file happens to sort after it
        files = [f for f in candidate_files if Path(f).stem != leaf_stem] + [Path(leaf_path)]
        self._src_keys = {}      # path -> the reader's (gen, base, count) the records came from (the fold's identity gate)
        self._src_stat = {}      # path -> the (size, mtime) of the file AS READ for those records: the witness a document row
        #                          carries for a file wholly before the cut, never the write-time stat (T396 round one)
        self._src_bytes = {}     # path -> (the reader entry's weight, its record count) when the list was taken or a fold
        #                          re-seated it: what the heap block weighs a list by once the record cache no longer holds it
        #                          (asm_pinned_records)
        self._src_superseded = {}   # path -> [(first record, records, file bytes)] for each list a fold moved the leaf off without
        #                             sharing its records (_asm_reseat_src): by_uuid still holds them, so the heap block counts them
        if seed is not None:
            self._seq = int(seed["seq_base"])
            self.prompt_ids |= seed["prompt_ids"]; self.boundary_pids |= seed["boundary_pids"]
            self.skill_use_ids |= seed["skill_use_ids"]; self.src_tool_links |= seed["src_tool_links"]
            self.dangling |= seed["dangling"]
            if seed.get("seq_ts") is not None:
                self._seq_ts.append(tuple(seed["seq_ts"]))   # the last pre-cut witness: a splice right after the cut lands on it
            self._last_ts = float(seed.get("last_ts") or 0)
        for fp in files:
            fsid = Path(fp).stem
            if records is not None and str(fp) in records:
                recs = list(records[str(fp)])
                self._src[str(fp)] = recs
                self._src_keys[str(fp)] = ("private",)
                self._ingest(recs, fsid, fsid == leaf_stem)
                continue
            cut = (seed or {}).get("cuts", {}).get(fsid)      # (offset, base, guard) for a file read from its cut, "skip" for
            if cut == "skip":                                   #  a file wholly before the cut (a fork's prior file, immutable)
                self._src[str(fp)] = []
                self._src_keys[str(fp)] = ("skip",)
                st_skip = (seed or {}).get("stat", {}).get(fsid)   # the document's verified witness for it (the load checked it)
                if st_skip is not None:
                    self._src_stat[str(fp)] = tuple(st_skip)
                continue
            if cut is not None:
                ent = _read_jsonl_entry(fp, tail_ok=True, tail_from=tuple(cut))
            else:
                ent = _read_jsonl_entry(fp, tail_ok=False)
            recs = ent[4] if ent is not None else []
            self._src[str(fp)] = recs
            self._src_keys[str(fp)] = (ent[6], ent[5], ent[5] + len(recs)) if ent is not None else (None, 0, 0)
            if ent is not None:
                self._src_stat[str(fp)] = (ent[1], ent[0])    # the reader's (size, mtime) for this very read
                self._src_bytes[str(fp)] = (_entry_weight(ent), len(recs))
            if cut is not None and ent is not None and ent[5] < cut[1]:
                recs = recs[cut[1] - ent[5]:]            # the entry holds records before the cut (a whole reader came
            #                                              first): the seed stands for those, ingest from the cut on
            self._ingest(recs, fsid, fsid == leaf_stem)
        # PRISTINE graph state — parent links / leaf exactly as the records say, BEFORE the three
        # repair passes mutate them. The assembly fold re-derives the passes from this each time
        # (extend pristine with the delta, re-run the passes on a working copy), so folded graph
        # state is literally the fresh-__init__ computation — never a latched earlier repair.
        self._pristine_parent = dict(self.parent_of)
        self._pristine_leaf = self.leaf_uuid
        # A PENDING bare rollback (chat delete): the kernel passes the cut point as leaf_override so
        # the walk starts there and the not-yet-abandoned tail drops exactly as it will once the CLI's
        # --resume-session-at branch takes. Applied only when the uuid is really in this graph — a
        # stale override (wrong file, raced clear) falls back to the true file leaf, never an empty parse.
        if leaf_override and leaf_override in self.by_uuid:
            self.leaf_uuid = leaf_override
        self._run_graph_passes()

    def _ingest(self, records, fsid, is_leaf):
        """Index `records` into the graph maps — the ONE ingestion path the fresh build and the
        assembly fold share, so a folded index is field-identical to a freshly built one."""
        seq = self._seq
        for r in records:
            seq += 1
            t = r.get("type")
            u = r.get("uuid")
            if u:
                self.by_uuid[u] = r
                self.fsid_of[u] = fsid
                self.seq_of[u] = seq
                ts = parse_z(r.get("timestamp"))
                if ts is None:
                    ts = self._last_ts
                    if u not in _TS_REPAIRED_SEEN:   # count DISTINCT corrupt records — a full
                        #                              parse re-ingests every line, and a stable
                        #                              corrupt one must not inflate per parse
                        if len(_TS_REPAIRED_SEEN) >= 4096:
                            _TS_REPAIRED_SEEN.clear()
                        _TS_REPAIRED_SEEN.add(u)
                        _asm_stat("ts-repair")
                    if fsid not in _TS_REPAIR_NOTED:
                        if len(_TS_REPAIR_NOTED) >= 64:
                            _TS_REPAIR_NOTED.clear()   # re-arm — capped silence must not become
                            #                            permanent silence for every NEW file
                        _TS_REPAIR_NOTED.add(fsid)
                        print("romp event-model: unparseable timestamp on record %s in %s.jsonl — "
                              "shown at the previous record's time (ts-repair in parse stats)"
                              % (u, fsid), file=sys.stderr)
                else:
                    self._last_ts = ts
                self.ts_of[u] = ts
                if t != "attachment":
                    # an attachment is never a landing WITNESS (a queued_command IS the spliced item,
                    # stamped with its send time): several sends spliced at one boundary must all
                    # read that boundary, not each other — see _landing_t
                    self._seq_ts.append((seq, ts))
                # parentUuid normally; compact_boundary carries parentUuid:null +
                # logicalParentUuid:<pre-compaction leaf> — follow that so the active
                # path survives compaction instead of orphaning every pre-compaction turn.
                # A SELF-referential link (corrupt record) becomes a root: kept as-is it
                # 1-cycles every walk that starts or passes there.
                p = r.get("parentUuid") or r.get("logicalParentUuid")
                self.parent_of[u] = None if p == u else p
                if p and p != u and p not in self.by_uuid:
                    self.dangling.add(p)   # may resolve later in this read — swept below
                if is_leaf:
                    self.leaf_uuid = u
                if t == "user":
                    if r.get("promptId"):
                        self.prompt_ids.add(r["promptId"])
                        if r.get("isCompactSummary") is True:
                            # a compaction summary's promptId is the designed link the boundary
                            # ADOPTION keys on — episode records wearing it can re-seat a card
                            self.boundary_pids.add(r["promptId"])
                            self.summary_pids.setdefault(r["promptId"], []).append(u)
                    if r.get("sourceToolUseID"):
                        self.src_tool_links.add(r["sourceToolUseID"])
                elif t == "assistant":
                    for b in _content(r.get("message")) or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and \
                                b.get("name") == "Skill" and b.get("id"):
                            self.skill_use_ids.add(b["id"])
            if t == "attachment":
                a = r.get("attachment") or {}
                if a.get("type") == "queued_command" and a.get("prompt"):
                    # the prompt can be a plain string OR a content-block LIST (the SDK injection
                    # path) — extract the TEXT either way; str() of a list keyed the Python repr,
                    # which no enqueue content ever matches (the user 2026-07-06)
                    ptext = a["prompt"] if isinstance(a["prompt"], str) else _text_of(a["prompt"])
                    # the repaired stamp when the record is uuid-bearing (real CLI splices are):
                    # raw-None dropped the user's typed prompt from the chat (T210 review)
                    self.qatts.append({"uuid": u, "ts": self.ts_of.get(u, parse_z(r.get("timestamp"))),
                                       "text": ptext, "seq": seq})
        self._seq = seq
        self.dangling -= self.by_uuid.keys()   # a target that landed later in the read is not dangling

    def _run_graph_passes(self):
        self._adopted = {}       # boundary uuid -> its episode's splice record (the /compact stdout),
        #                          filled by _adopt_detached_compactions. Downstream consumers key on
        #                          membership: an ADOPTED boundary is a LIVE manual compact, so the
        #                          replay dedup, which arms at the pair's SUMMARY record keyed to that
        #                          record's own boundary, must not arm for an adopted pair (nothing
        #                          after the pair is a replayed tail: the 2026-08-19 reason, the arming
        #                          record named 2026-09-19) and its atom must sort AFTER the episode's
        #                          stdout.
        self._repair_compaction_stitches()
        self._stitch_resume_forks()
        self._adopt_detached_compactions()

    def _stitch_resume_forks(self):
        """Some CLI resumes of a machine-cut turn FORK the transcript with a FRESH head (parentUuid
        null, no cross-file back-link) instead of continuing the chain. On disk that fork is
        byte-indistinguishable from a /clear, so kept_uuids dropped the ENTIRE pre-cut conversation
        and the judges never saw the cut turn's work again — an hourly watch's finding lost its card
        to two mid-turn restarts (the user 2026-08-14). The kernel records the fork the moment the
        resumed CLI's init reports the new fsid (states/ resumeFork rows -> resume_links), so the
        lineage is an exact recorded event, never a guess: re-point the fork head's parent at the
        resumed file's last uuid-bearing record, restoring ONE chain the walk can cross (the
        compaction-stitch precedent above). Only a genuinely fresh head is stitched — an intact
        back-link is never overridden — and a /clear records no lineage, so its history keeps
        dropping by design."""
        if not self.resume_links:
            return
        first_of, last_of = {}, {}
        for fs, ends in ((self.seed or {}).get("file_ends") or {}).items():   # files (or file heads) before the cut
            if ends[0]:
                first_of[fs] = ends[0]
            if ends[1]:
                last_of[fs] = ends[1]
        for u in self.by_uuid:               # insertion order = file read order
            fs = self.fsid_of.get(u)
            if fs not in first_of:
                first_of[fs] = u
            last_of[fs] = u
        for to, frm in self.resume_links.items():
            head, tail = first_of.get(to), last_of.get(frm)
            if head and tail and head != tail and self.parent_of.get(head) is None:
                self.parent_of[head] = tail

    def _repair_compaction_stitches(self):
        """Claude Code sometimes writes a compact_boundary whose logicalParentUuid points
        at a message that exists in compactMetadata.allUuids but was NEVER written as its
        own transcript line (3/69 compactions in the live corpus). Followed blindly, that
        dangling stitch orphans ALL pre-compaction history. The real in-file pre-compaction
        leaf is in compactMetadata.preservedSegment (tail/anchor/head), so when the stitch
        target is missing, re-point parent_of there — reconnecting the pre-compaction tree.
        (Verified: rescues 100% of the corpus's broken stitches.)"""
        known = (self.seed or {}).get("verdicts") or {}   # records before an assembly checkpoint's cut (T323 stage 4)
        for u, r in self.by_uuid.items():
            if r.get("type") != "system" or r.get("subtype") != "compact_boundary":
                continue
            target = self.parent_of.get(u)
            if target is None or target in self.by_uuid or target in known:
                continue                          # no stitch, or stitch is intact
            seg = (r.get("compactMetadata") or {}).get("preservedSegment") or {}
            for k in ("tailUuid", "anchorUuid", "headUuid"):   # tail = the pre-compaction leaf
                cand = seg.get(k)
                if cand and (cand in self.by_uuid or cand in known):
                    self.parent_of[u] = cand
                    break

    def _adopt_detached_compactions(self):
        """A LIVE manual /compact writes its compact_boundary + summary as a DETACHED side
        branch: the boundary carries parentUuid:null + logicalParentUuid:<pre-compact leaf>,
        the summary record is its only child, and NOTHING chains through them — the visible
        conversation parents through the /compact invocation records (caveat/wrapper/stdout)
        instead. The backward walk never visits the side branch, so the compaction atom — the
        chat's "Context compacted" card — was silently never emitted for a live manual
        compact, while auto-compactions (whose continuation chains THROUGH boundary+summary)
        kept theirs (the user 2026-08-19).

        Adopt the orphaned pair by splicing it in AFTER its own invocation episode's stdout
        record: …anchor ← caveat ← wrapper ← stdout ← boundary ← summary ← former child. Two
        deliberate choices there, both corrections of the first cut (2026-08-19 review):

        * The boundary's own EPISODE — not its bare anchor — is both the gate and the splice
          point. The designed link is the summary record's promptId, which the CLI stamps
          with the invoking /compact's promptId (13/13 manual boundaries in the live corpus;
          file-order adjacency is the fallback for summaries carrying NO promptId at all, and
          is genuinely a fallback: one corpus episode is appended BEFORE its boundary). Gating
          on the bare anchor resurrected compactions the user had REWOUND AWAY (next prompt
          re-parents at the pre-compact leaf: wrappers off-path, anchor still on it), and two
          compactions sharing one anchor threaded through each other. Episode off the active
          path → its /compact was undone → the boundary stays hidden with it; no episode →
          nothing witnesses the invocation on the visible history → stays hidden — and a
          promptId that names NOTHING on record stays hidden the same way, never handed to
          adjacency: that is the crash-truncated write this module models mid-write, and
          adjacency in its place stole a later same-anchor /compact's episode.
        * Splicing BEFORE the stdout pulled that stdout atom out of its /compact command
          segment into the boundary's fresh triggerless turn, minting a brand-new
          judge-visible WORK unit ("Compacted (ctrl+o…)") for every manual compact in every
          existing session. After the stdout, the command segment keeps its output and the
          boundary's turn holds nothing — no assistant work, no unit.

        Keyed on the SHAPE (boundary off the active path, its episode on it), never on
        trigger=manual: an attached boundary of either kind (auto, or the resume re-splice a
        manual pair arrives back in) no-ops here. When the stdout IS the leaf (the user
        compacted and has not typed since), the pair becomes the chain's new tail — the card
        must not wait for the next prompt. Runs after the stitch repair and the resume
        stitching (the active path must already cross files). parent_of/leaf_uuid only —
        records are never mutated, so the shared _read_jsonl_incremental cache lists stay
        pristine. Adopted boundaries are recorded in self._adopted for the two downstream
        consumers that must NOT treat them as attached: the replay dedup, which arms at the
        pair's SUMMARY record keyed to that record's own boundary and must not arm for an
        adopted pair (2026-09-19; a live manual compact replays no tail, and armed there the
        dedup ate the user's next genuine prompt whenever its text repeated an earlier one,
        2026-08-19), and the emit-order override (the boundary record is appended BEFORE the
        stdout, so raw (t, seq) order would put the card inside the command exchange it
        belongs after).

        Placement note (re-derived 2026-08-19 against the golden scenario AND every
        boundary-bearing live-corpus transcript, plan_units pre vs post — the first cut
        asserted no-bump from intention and was wrong, so only measurements are recorded
        here; tests/test_placements_canary.py pins the class): the added boundary atom's
        turn holds no user ask and no assistant work of its own, so on the golden scenario
        and 11 of 12 corpus transcripts the unit sets are byte-identical pre/post — no
        recorded placement key shifts, no unit appears or disappears. The residue: when
        assistant work FOLLOWS the manual compact with no new opener (a queued prompt
        spliced through it — 1 of 12), that continuation moves from the /compact command
        segment (where the old parse misfiled it as a human-triggered "/compact worked"
        unit) into the boundary's own turn — the same non-human continuation unit an
        attached auto-compact has always produced. One re-attributed unit per such
        transcript. DECIDED no-bump (2026-08-19, from traced evidence): the orphaned row
        is inert — every placements consumer queries only units the CURRENT parse yields,
        and _migrate_placements never removes old rows, so orphans are the normal
        post-bump state of every store since v2; the fuzzy _placed_key path reads them
        only in the dedup direction (prevents replay, never causes one). The one NEW unit
        costs a single planner call. A bump would be strictly worse: it seals every
        store's currently-ready unplaced units — measured ~29 across the live corpus,
        including genuinely pending work — the silent drop of a real ask this repo calls
        its one fatal error."""
        active = self.active_path()
        if not any(r.get("type") == "system" and r.get("subtype") == "compact_boundary"
                   and u not in active for u, r in self.by_uuid.items()):
            return                        # nothing detached — skip the episode scan entirely
        # the active CHILD of each on-path uuid — the chain has at most one per node
        child_of, u = {}, self.leaf_uuid
        while u is not None:
            p = self.parent_of.get(u)
            if p is None or p in child_of:
                break
            child_of[p] = u
            u = p
        # /compact invocation EPISODES, keyed by promptId: head = first record in file order
        # (the caveat/raw twin, parented on the pre-compact leaf); splice = the FIRST stdout
        # record — a restore burst can replay the episode verbatim with the promptId preserved,
        # and a later copy must never re-seat the card off the original splice (the copy
        # seq-nearest the boundary+summary pair; the replayed copy's atoms fall to the dedup) —
        # else the last record seen: a MID-WRITE episode, one parse wide, never hidden, each
        # phase self-correcting at the next record. Boundary- or summary-as-leaf the pair is ON
        # the active path (attached by shape, emits natively; the dedup arms at the summary on an
        # empty window — the file ends at the pair); caveat- or wrapper-as-leaf it adopts AT the
        # episode's last landed record — adopted, so unarmed — and re-seats once the stdout lands.
        episodes = {}
        for eu, er in self.by_uuid.items():          # insertion order = file read order
            pid = _compact_episode_pid(er)           # a user record's promptId, a compaction summary's aside: the one filter,
            if not pid:                              # which the promptid gate's every reading of an episode asks too
                continue
            blocks = _content(er.get("message"))
            btext = (_text_of(blocks) if blocks else "") or ""
            g = episodes.setdefault(pid, {"head_parent": self.parent_of.get(eu),
                                          "head_seq": self.seq_of.get(eu, 0),
                                          "splice": eu, "stdout": None, "compact": False})
            if _names_compact(btext, first=True):
                g["compact"] = True                  # the episode invokes /compact, not some other command (its first tag:
                #                                      the promptid gate reads every tag with the same reader, _asm_names_compact)
            if LOCAL_STDOUT_RE.match(btext) and g["stdout"] is None:
                g["stdout"] = eu                     # first stdout wins — see the splice rule above
            g["splice"] = g["stdout"] or eu
        boundaries = sorted((self.seq_of.get(u, 0), u) for u, r in self.by_uuid.items()
                            if r.get("type") == "system" and r.get("subtype") == "compact_boundary")
        for _, b in boundaries:
            if b in active:
                continue                  # attached (auto / resume re-splice) — never double-emit
            summary = next((s for s, sr in self.by_uuid.items()
                            if sr.get("isCompactSummary") is True and self.parent_of.get(s) == b),
                           None)
            pid = (self.by_uuid.get(summary) or {}).get("promptId") if summary else None
            if pid:
                # the designed link, and the ONLY one honored when present: a promptId that
                # names no on-record /compact episode (a crash-truncated compaction —
                # boundary+summary landed, the episode records never did) keeps its boundary
                # HIDDEN. Degrading to adjacency stole a later same-anchor /compact's episode:
                # the stale summary rendered at the live splice, and the already-claimed guard
                # below then hid the real compact's card (2026-08-19 second review).
                ep = episodes.get(pid)
                if ep is not None and not ep["compact"]:
                    ep = None             # the summary's promptId names some OTHER exchange — not a witness
            else:
                # fallback ONLY for summaries carrying no promptId at all (older writes,
                # synthetic shapes): b's own episode is the nearest /compact invoked from b's
                # anchor and appended after b — the CLI writes boundary+summary first, then
                # the episode records
                anchor = self.parent_of.get(b)
                cands = [g for g in episodes.values()
                         if g["compact"] and g["head_parent"] == anchor
                         and g["head_seq"] > self.seq_of.get(b, 0)]
                ep = min(cands, key=lambda g: g["head_seq"]) if cands else None
            if ep is None:
                continue                  # no on-record /compact invocation owns this boundary — stays hidden
            sp = ep["splice"]
            if sp not in active or sp == b or sp in self._adopted.values():
                continue                  # the episode was rewound/cleared away (or already claimed) — hidden
            c = child_of.get(sp)
            tail = summary if summary is not None else b
            self.parent_of[b] = sp        # …stdout <- b (<- summary) <- former child
            if c is not None:
                self.parent_of[c] = tail
            else:
                self.leaf_uuid = tail     # the stdout was the leaf: the adopted pair is the new tail
            active.add(b)
            child_of[sp] = b
            if summary is not None:
                active.add(summary)
                child_of[b] = summary
                if c is not None:
                    child_of[summary] = c
            elif c is not None:
                child_of[b] = c
            self._adopted[b] = sp

    def active_path(self):
        """The set of uuids on the leaf->root chain (directed walk, O(chain length))."""
        active, u, guard = set(), self.leaf_uuid, 0
        while u is not None and u not in active and guard < 500000:
            active.add(u)
            u = self.parent_of.get(u)
            guard += 1
        return active

    def landed_text_uuids(self):
        """Uuids whose record carries NON-EMPTY assistant text — on ANY branch, kept or dropped.
        The orphan salvage dedups against this rather than the kept path alone: a reply that
        LANDED and was then abandoned by a chat-delete rollback (its branch forks away once the
        CLI relaunches with --resume-session-at) is not a loss, and its orphanReply marker must
        never resurrect it. The parse_session leaf_override filter covers only the ARMED window;
        after the rollback is CONSUMED the abandoned reply left the kept path and its marker
        re-fired forever — a durable ghost bubble, visible to the judges too (the user
        2026-08-03). Text-bearing only, so a TEXTLESS twin record (the fable+AskUserQuestion
        empty-thinking case) still does not eat its marker's salvage."""
        out = set()
        for u, r in self.by_uuid.items():
            if r.get("type") == "assistant" and _text_of(_content(r.get("message"))).strip():
                out.add(u)
        return out

    def _batch_head(self, fork, head):
        """Whether `head`, an off-spine record whose parent `fork` is on the spine, is the rest of a PARALLEL TOOL BATCH
        (2026-09-23). The CLI writes a model message that makes several tool calls as one assistant record per content
        block, chained block to block under one message id, and parents each call's RESULT at the record carrying that
        call (the result's sourceToolAssistantUUID names it). The transcript is a tree there: while the results land the
        leaf is whichever result, or the hook attachment after it, was written last, so the spine passes through that
        result's call alone, and when the reply chains off the last result, the other results hang beside the spine.
        Filed as a rewound fork, the second and later calls left the chat until their own results landed, and every
        result but the last one's was gone for good once the batch ended (their tool rows never showed an output).

        The two designed links, read off the records, never guessed: `fork` is an assistant record carrying a tool_use
        and `head` is either the next record of fork's own message (same message id) or a tool_result answering only
        calls fork carries. In a sample of one machine's transcripts (2026-09-23, structure only) every child of a
        tool-call record was one of those two (2,411 results, 156 next blocks, nothing else): machine output of the
        call's own turn, never a rollback's or a retry's residue. The keep is the branch under `head`, up to a message
        someone sent (chain_verdicts: _sent_message). A single call's result takes the same link: on the normal chain
        it IS the spine, and when a retry storm's api_error spur takes the spine from the call record, its result
        (which the eclipse probe used to salvage as "eclipsed") is kept as the call's own, "active"."""
        return self._batch_link(self.by_uuid.get(fork), self.by_uuid.get(head))

    @staticmethod
    def _batch_link(fr, hr):
        """The batch's two designed links between two RECORDS (see _batch_head): `fr` is an assistant record carrying a
        tool_use and `hr` is the next record of fr's own message (same message id) or a tool_result answering only calls
        fr carries. Read off the records alone, so the assembly gate asks it of an appended record the graph has not
        ingested yet (_batch_delta) and the two can never disagree on what a batch is."""
        if fr is None or hr is None or fr.get("type") != "assistant":
            return False
        fmsg = fr.get("message") if isinstance(fr.get("message"), dict) else {}
        calls = {b.get("id") for b in _content(fmsg) if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")}
        if not calls:
            return False
        if hr.get("type") == "assistant":
            hmsg = hr.get("message") if isinstance(hr.get("message"), dict) else {}
            return bool(fmsg.get("id")) and hmsg.get("id") == fmsg.get("id")
        if hr.get("type") == "user":
            answers = [b.get("tool_use_id") for b in _content(hr.get("message"))
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            return bool(answers) and all(a in calls for a in answers)
        return False

    def _batch_delta(self, delta, parent_of, old_leaf):
        """Whether an appended `delta` whose new leaf does not chain back to `old_leaf` is a PARALLEL TOOL BATCH landing
        (2026-09-24): every record whose parent lies outside the delta either continues the old leaf or hangs off a
        record already in the graph by the batch's own link (_batch_link: the next call of that record's message, or a
        result answering only its calls), and at least one does the latter. `parent_of` is the delta's uuid -> parent map
        (the gate's). The assembly gate folds such a delta and leaves the ruling to the fold's kept-invariance check; an
        api_error spur, a rewind, a /clear fork, another message's record or a result for a call its parent does not
        carry hangs off no such link, and a parent before a restored entry's cut is not in this graph: each still demotes."""
        seen = False
        for r in delta:
            u = r.get("uuid")
            if not u:
                continue
            p = parent_of.get(u)
            if p in parent_of or (p is not None and p == old_leaf):
                continue
            if not self._batch_link(self.by_uuid.get(p), r):
                return False
            seen = True
        return seen

    def _sent_message(self, u):
        """Whether record `u` is a message someone sent (a user record carrying no tool_result, not the harness's isMeta
        bookkeeping, not a compaction summary): where a parallel batch's keep stops (chain_verdicts). A rollback
        re-parents the user's next prompt, so a prompt found under a batch branch the leaf left keeps the verdict it
        always had (fork_kind's: a rewind, or an eclipse behind a retry storm's spur), rather than showing again a
        message the person deleted."""
        r = self.by_uuid.get(u) or {}
        if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary"):
            return False
        blocks = _content(r.get("message"))
        return bool(blocks) and not _has_tool_result(blocks)

    def chain_verdicts(self, active=None):
        """THE chain-membership identity, one verdict per uuid in the graph — the single
        implementation every consumer must share (the goal-store rewind cleanup grew four
        hand-rolled partial twins of this walk before it was exported, and they disagreed
        on exactly the cases that matter — resume forks, pending cuts, broken chains):
          "active" — on the leaf->root spine (what the chat shows), or on a branch that is the
                     rest of a parallel tool batch hanging off it (see _batch_head): the
                     calls of one model message and their results, which the CLI writes as a
                     tree, not a line. Such a branch is in the `active` verdict but not in the
                     `active` SET (the spine walk), so kept_uuids and _membership_of read the
                     verdict.
          "rewind" — the chain rejoins the active spine: this line was REWOUND AWAY. The
                     only verdict that ever justifies dropping/sweeping content.
          "eclipsed" — the chain rejoins the active spine, but the spine leaves the fork
                     through MACHINE bookkeeping (an api_error spur): nobody rewound
                     anything — the CLI flushed its buffered retry-storm records chained
                     off the turn's opener and hung the next prompt on that spur, so the
                     turn's real output fell off the spine with no user gesture. KEPT:
                     it is the ONLY copy of a reply the user watched render (T209, the
                     user 2026-09-01 — a five-minute turn's answer vanished behind a
                     "Recovered after retries" note when the next send flushed the spur;
                     no orphanReply marker exists because the disk DID keep the text).
                     Within the fork's component the verdict is NARROWED to the one
                     machine-orphaned reply chain — sibling stub pairs, error bursts and
                     user-headed branches drop exactly as they do at any other fork
                     (see _select_eclipsed_chains).
          "clear"  — the chain reaches a clean null root the leaf does not share: `/clear`
                     jurisdiction (the episode machinery settles those) — never swept as
                     a rewind.
          "broken" — parentUuid points at a uuid in NO transcript, or a cycle: unprovable,
                     KEPT (silently dropping a real ask is this repo's one fatal error).
        `active` defaults to active_path(); pass it when already computed."""
        if active is None:
            active = self.active_path()
        seed = self.seed
        seed_types = seed["types"] if seed is not None else {}
        seed_verdicts = seed["verdicts"] if seed is not None else {}
        # spine child map (parent -> its child ON the spine), for the fork-kind probe below
        spine_child, _u, _guard = {}, self.leaf_uuid, 0
        while _u is not None and _guard < 500000:
            _p = self.parent_of.get(_u)
            if _p is None or _p in spine_child:
                break                              # root, or a cycle already crossed
            spine_child[_p] = _u
            _u = _p; _guard += 1
        if seed is not None:                       # the pre-cut spine, root to cut, so a probe from a pre-cut fork walks on
            sp = seed["spine"]
            for i in range(len(sp) - 1):
                spine_child.setdefault(sp[i], sp[i + 1])
        _fork_memo, _fork_terminal = {}, {}

        def _type_of(u):
            r = self.by_uuid.get(u)
            if r is not None:
                return r.get("type"), r.get("subtype")
            return seed_types.get(u, (None, None))
        def fork_kind(f):
            """rewind vs eclipsed for a chain rejoining the spine at f. A genuine rollback
            re-parents the user's NEXT PROMPT directly at the cut (the spine leaves f with a
            conversational record); the SDK's buffered-flush geometry puts >=1 system
            api_error record STRICTLY BETWEEN f and the next conversational record — an
            exact machine event, so this can never re-show content a person deleted. An
            assistant record ending the probe means the spine re-replied: the fork is a
            superseded attempt (the orphan salvage's jurisdiction), not an eclipse.
            An eclipse also records WHICH terminal decided it in _fork_terminal — "user"
            (the flush completed: the next prompt sits past the spur) or "exhausted" (the
            spur is the transcript's tail, or a corrupt cycle) — because the two terminals
            keep differently (see _select_eclipsed_chains)."""
            if f in _fork_memo:
                return _fork_memo[f]
            res, u, saw_err, _seen = "rewind", spine_child.get(f), False, set()
            while u is not None and u not in _seen:
                _seen.add(u)
                t, sub = _type_of(u)
                if t == "assistant":
                    break
                if t == "user":
                    if saw_err:
                        res = "eclipsed"
                        _fork_terminal[f] = "user"
                    break
                if t == "system" and sub == "api_error":
                    saw_err = True
                u = spine_child.get(u)
            else:
                # The spine RAN OUT (or cycled — _seen is the guard classify's own cycle branch
                # has and this probe first shipped without: a multi-node cycle of system records
                # hung every parse, found by adversarial review) before any conversational
                # record: the spur is the transcript's current TAIL — a parse racing the CLI's
                # multi-line flush, or a session that died in the storm. An api_error on the
                # spine out of the fork is a machine artifact whatever follows, and defaulting
                # this terminal to the one SWEEPABLE verdict re-opened the T209 loss for
                # exactly those windows (one-way goal archives in the race; a permanent eat on
                # the mid-storm death). A pending-cut fork is untouched: the cut leaf has no
                # spine child, so the loop never runs and saw_err stays False.
                if saw_err:
                    res = "eclipsed"
                    _fork_terminal[f] = "exhausted"
            _fork_memo[f] = res
            return res
        verdict = {}
        batch_fork = {}                            # a batch branch's record -> the spine tool call it hangs from
        def classify(start):
            path, u, fork = [], start, None
            while True:
                if u in active:
                    # chain rejoins the active spine -> rewound fork, unless a machine spur abandoned it
                    # (eclipsed), or the branch is the rest of a parallel tool batch (see _batch_head)
                    if self._batch_head(u, path[-1]):
                        res, fork = "active", u
                    else:
                        res = fork_kind(u)
                    break
                if u in verdict:
                    res = verdict[u]; fork = batch_fork.get(u); break
                if u not in self.by_uuid:
                    sv = seed_verdicts.get(u)      # a record before the cut: the checkpoint recorded its verdict
                    if sv == "active":
                        res = fork_kind(u); break  # the chain rejoins the pre-cut spine
                    if sv is not None:
                        res = sv; break
                    res = "broken"; break           # dangling target uuid (corruption)
                if u in path:
                    res = "broken"; break           # cycle -> unprovable, keep
                path.append(u)
                p = self.parent_of.get(u)
                if p is None:
                    res = "clear"; break            # clean null root, not the leaf's -> pre-clear
                u = p
            if fork is not None:
                # A batch branch, or a record under one reached through the memo. The keep stops at a message
                # someone sent: that message and everything under it take the verdict the fork gives any other
                # branch leaving the spine there (fork_kind: a rewind, or an eclipse behind a retry storm's spur).
                # `path` runs from `start` up toward the branch, so the sent message nearest the spine cuts it.
                cut = max((i for i, x in enumerate(path) if self._sent_message(x)), default=-1)
                below = fork_kind(fork) if cut >= 0 else None
                for i, x in enumerate(path):
                    if i <= cut:
                        verdict[x] = below
                    else:
                        verdict[x] = "active"
                        batch_fork[x] = fork
                return verdict[start]
            for x in path:
                verdict[x] = res
            return res
        out = {}
        for u in self.by_uuid:
            out[u] = "active" if u in active else classify(u)
        self._select_eclipsed_chains(out, active, _fork_terminal)
        return out

    def _select_eclipsed_chains(self, verdict, active, fork_terminal):
        """WHICH uuids an eclipsed fork keeps (2026-09-01). fork_kind above decides WHETHER a
        fork was machine-abandoned — >=1 system api_error record strictly between the fork and
        the spine's next conversational record (T209) — and classify hands every chain
        rejoining there the "eclipsed" verdict. But an eclipsed fork can hold SIBLING chains
        that are not the bypassed reply: a parallel tool-call stub pair (textless twins the
        walk drops everywhere else), a second flushed api_error burst, an older persisted
        attempt, or a branch headed by a USER record. Keeping the whole component re-renders
        the stub twins beside their kept originals, doubles a two-attempt turn, and — the one
        direction the eclipsed verdict promised never to take — can re-show a prompt the user
        deleted: a rollback typed DURING a storm produces exactly this geometry, because the
        CLI flushes its buffered api_error records at the next enqueue, so the user's
        replacement prompt lands past the error spur and their abandoned branch rejoins at an
        api_error fork. So the eclipse keeps ONE BRANCH — the sub-tree under one fork-side
        head — selected by what it carries, never by the CLI's byte order of writes (nothing
        contracts flush order; an order-keyed pick let a late-written stub pair steal the keep,
        and a late-written burst veto it). A branch qualifies when its fork-side HEAD is an
        assistant record (a user gesture's head is always the user's own record) and it
        carries reply text anywhere in the sub-tree; the winner is the branch with the
        latest-written reply text, tiebroken by head seq. The text witness is the eclipse
        set's text-bearing assistant records (landed_text_uuids' own test, applied to the
        eclipse set alone — the ranking reads it for sub-tree members only, and the sub-trees
        lie inside that set) MINUS isApiErrorMessage records (error-as-text failure echoes,
        which atoms() likewise refuses to treat as replies) — so junk carries no weight in the
        key, and no textless tail can move the pick whatever its file position. The unit is
        deliberately the WHOLE branch, never a leaf-chain within it: leaf-chains of one branch
        share records, and every per-chain read of shared state proved breakable (a full-chain
        gate laundered a junk tail's steal; a unique-suffix gate let a branch's own twin
        sub-branches strip their turn's text and hand the keep to an older sibling — reply
        loss, this repo's one fatal error). The accepted cost is cosmetic: a stub twin inside
        the winning branch renders one output-less duplicate tool row, and a grafted burst
        inside it costs nothing (system records never become atoms). The qualifying properties
        are the ones every incident's salvaged branch has in the author's corpus scan (49/49
        across 4,678 transcripts; the 2,809 non-eclipse rewind forks: 2,035 stub pairs, 700
        superseded retries, 42 user-gesture rollbacks, 32 other; zero overlap). Sibling
        branches demote to "rewind", exactly as their on-spine twins classify.

        When NO chain qualifies, the two eclipse terminals part ways, each on its own event:
          "user"      — the flush COMPLETED (the next prompt sits past the spur), so a machine-
                        orphaned reply would qualify if one existed; a USER-HEADED branch here
                        is indistinguishable from a rollback the storm raced, and re-showing
                        deleted content is the hazard this verdict was designed around. The
                        user-headed branches demote to "rewind" — and ONLY those, where
                        user-headed means a user PROMPT at the fork-side head: an assistant-
                        headed textless branch (a turn of pure tool activity) and a stranded
                        tool_result head (user-TYPED but machine-MADE) are both provably not
                        rollback residue, and they stay "eclipsed"/kept.
          "exhausted" — the spur is the transcript's TAIL (a parse racing the multi-line
                        flush, a session dead mid-storm) or a corrupt machine cycle: no next
                        prompt exists, so no user gesture can have abandoned anything, and
                        keep-on-unprovable — this module's bias — holds. Everything stays
                        "eclipsed".
        The CLI's buffered flush is the root cause (filed as anthropics/claude-code#91113);
        this parse-side keep defends regardless."""
        ecl = {u for u, v in verdict.items() if v == "eclipsed"}
        if not ecl:
            return
        # Everything below reads only uuids INSIDE the eclipse set, so build only that much: the
        # child map over ecl (the component walk skips any popped uuid outside ecl, and a sub-tree
        # walk skips anything outside its component, so children outside ecl were never followed)
        # and the text witness over ecl (the ranking tests it for sub-tree members only). A
        # whole-graph child map and landed_text_uuids() over every record made this walk a third
        # of chain_membership's cost on a large transcript with a handful of eclipsed records; the
        # verdicts are identical. Nothing here is cached on the adapter: _run_graph_passes mutates
        # parent_of after ingest, so a per-adapter child map would go stale under the repair passes.
        children = {}                     # parent -> its children in ecl
        forks = {}                        # fork uuid -> its eclipsed branch heads
        for u in ecl:
            p = self.parent_of.get(u)
            if p is None:
                continue
            children.setdefault(p, []).append(u)
            if p in active:
                forks.setdefault(p, []).append(u)
        landed = set()                    # text-bearing assistant uuids in ecl (the reply witness)
        for u in ecl:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "assistant" and _text_of(_content(r.get("message"))).strip():
                landed.add(u)
        for F, heads in forks.items():
            comp, stack = set(), list(heads)
            while stack:                  # the branch component: child-closure of the eclipsed heads
                x = stack.pop()
                if x in comp or x not in ecl:
                    continue
                comp.add(x)
                stack.extend(children.get(x, ()))
            # SELECTION IS PER BRANCH — the sub-tree under each fork-side head — never per
            # leaf-chain. Leaf-chains of one branch SHARE records, and any per-chain read of
            # shared state proved breakable by construction: a full-chain gate let a junk tail
            # inherit the prefix's text and steal the pick on write order, and a unique-suffix
            # gate let a branch's own twin sub-branches strip their turn's text record from
            # both suffixes and disenfranchise the REAL reply (an older sibling then stole the
            # keep — reply loss, this repo's one fatal error). The branch is the unit the CLI
            # persisted; it is kept or dropped WHOLE. The known cost is cosmetic and deliberate:
            # a parallel stub twin inside the winning branch renders one output-less duplicate
            # tool row, and a grafted error burst inside it costs nothing at all (system
            # records never become atoms, kept or not). Never trade a possible reply for a
            # duplicate row.
            def _subtree(h):
                seen, st = set(), [h]
                while st:
                    x = st.pop()
                    if x in seen or x not in comp:
                        continue
                    seen.add(x)
                    st.extend(children.get(x, ()))
                return seen
            qual, user_heads = [], []
            for h in heads:
                hr = self.by_uuid.get(h) or {}
                head_t = hr.get("type")
                if head_t == "user" and not _is_tool_result(hr):
                    user_heads.append(h)  # rollback-shaped: the "user" stand-down's one target.
                #                           A tool_result head is user-TYPED but machine-MADE (a
                #                           result the storm stranded beside its on-spine tool_use)
                #                           — provably not rollback residue, so it stays kept; its
                #                           output even reunites with the spine's tool call.
                if head_t != "assistant":
                    continue              # fork-side head isn't a streamed reply record (a user gesture, a burst)
                sub = _subtree(h)
                txt = [self.seq_of.get(x, 0) for x in sub
                       if x in landed and not (self.by_uuid.get(x) or {}).get("isApiErrorMessage")]
                #                           failure echoes (isApiErrorMessage: error-as-text) are not
                #                           replies — mirrors atoms()' own refusal to anchor on them
                if not txt:
                    continue              # no reply text anywhere in the branch → a stub pair, a burst
                qual.append(((max(txt), self.seq_of.get(h, 0)), h, sub))
                #             ^ ranked by the branch's LATEST REPLY TEXT — the CLI's final word on the
                #               turn. Junk records carry no text, so nothing textless can move the key.
            if qual:
                keep = max(qual)[2]
                for u in comp - keep:
                    verdict[u] = "rewind"     # sibling branches drop exactly as their on-spine twins do
            elif fork_terminal.get(F) == "user":
                for h in user_heads:          # completed flush, no reply branch → drop ONLY rollback-shaped
                    for u in _subtree(h):     # branches (fork-side head = the user's own deleted record);
                        verdict[u] = "rewind" # an assistant-headed textless branch (a tool-only turn) is
                #                               provably not rollback residue and stays kept
            # "exhausted" with no qualifying branch: everything stays eclipsed (keep-on-unprovable)

    def kept_uuids(self, active):
        """The active leaf-ancestors PLUS any line on a BROKEN chain (its parentUuid points
        at a uuid that exists in NO transcript — corruption / a partial write). The two
        kinds of off-path line we DO drop are both intentional: a rewind fork (its chain
        rejoins the active spine) and a clear branch (its chain reaches a clean null root
        the leaf does not share — `/clear` breaks the parent link, spec-mandated drop). A
        dangling chain is the one thing we cannot prove dead, and silently dropping a real
        ask is this repo's one fatal error, so we keep it. (Verified 0 dangling cases
        across the live corpus: this is a safety net, not a behavior change.)
        An ECLIPSED chain is also kept: a machine-written api_error spur stole the leaf's
        ancestry from a turn's real output (see chain_verdicts) — dropping it ate the only
        visible copy of a rendered reply (T209).
        So is the rest of a PARALLEL TOOL BATCH beside the spine (its "active" verdict off the
        spine walk, _batch_head): the other calls of the message and their results.
        Derived from chain_verdicts — one implementation, so the exported membership
        predicate (chain_membership) can never diverge from what the parse keeps.
        set(active) is unioned as-is: the walk can record a dangling FINAL ancestor that is
        in no file's index, and it has always been kept. Within an eclipsed fork's component,
        _select_eclipsed_chains has already narrowed the verdict — to the one machine-orphaned
        reply chain when one qualifies, and otherwise per that walk's terminal rules (user-
        headed chains demote behind a completed flush; a tail spur keeps everything) — so this
        union keeps exactly what the eclipse salvages."""
        return set(active) | {u for u, v in self.chain_verdicts(active).items()
                              if v in ("active", "broken", "eclipsed")}

    def _absorbed_atom(self, full, t, seq, auid, rompuuid, postal_index, *, stats):
        """One synthesized user atom for a mid-turn splice, placed where the model READ it (T252d, the
        user 2026-09-08): its `t` is the LANDING time — the moment the CLI took the message off its
        queue, the file-order predecessor's stamp (_landing_t) — so the atom sits BELOW the steps that
        ran while the message waited, and the order on screen is the order the model saw. The SEND time
        stays on the atom as `sentAt` (the chat's bubble hover). Ordering rule: atoms sort by (t, _seq)
        (parse_session), and the attachment's FILE ORDER is the authority — the atom sorts after every
        record the CLI wrote before taking it (the witness's stamp, and a higher seq than the witness)
        and before the assistant record that answers it (written after the attachment, stamped no
        earlier); several sends the CLI took at one boundary share that boundary's stamp and keep
        their send order, which is their file order. A send the CLI took AFTER a witness stamped
        before the send clamps to the send time (no truthful landing precedes it); an attachment with
        no predecessor in the read keeps its send time as the only truthful place. Before T252d the
        atom sat at its send time, above those steps (the T252 in-place rule); the user's call was
        that the read position is the one that matches what the model actually saw.

        DEPLOY RULE: `t` is half of the segment id (fsid:t:texthash), so moving it changed placement
        identity for every absorbed atom whose landing differs from its send — PLACEMENTS_V 12 (the
        seal keeps dormant sessions from replaying the moved atoms as new goals);
        tests/test_placements_canary.py pins the new derivation.

        The atom carries the FULL text — any whitespace-collapsed form is for MATCHING only (the user
        2026-07-08: collapsing ate the blank line between a follow-up's quoted context and the typed
        reply, so markdown folded the reply INTO the blockquote; and the kernel's optimistic echo could
        never text-prune against the collapsed copy, so the message rendered TWICE).

        `stats` False keeps the clamp below out of the parse stats, for a caller that is no parse (_absorbed's)."""
        blocks = [{"type": "text", "text": full}]
        arec = self.by_uuid.get(auid) or {}
        origin = _record_origin(arec)   # a queued_command attachment carries its own origin / commandMode
        atom = {
            "type": "user", "uuid": auid, "session_id": rompuuid,
            "t": t, "sentAt": t, "fsid": self.fsid_of.get(auid),
            "parentUuid": arec.get("parentUuid"),
            "message": {"role": "user", "content": blocks},
            "author": author_of(blocks, None, postal_index, getattr(self, "sdk_human", False), origin),
            "absorbed": True,   # a mid-turn splice, placed where the model read it (T252d): the
            #                     atoms that FOLLOW it are the model's work after reading it — its
            #                     reply, as for any ask. (Until T252d the atom sat at its SEND time and
            #                     the following atoms were the interrupted turn's work, which the judges
            #                     had to refuse as evidence; that leg is gone with the placement.) The
            #                     flag still tells the chat and the judges how the message arrived.
            "_seq": seq,
        }
        if origin:
            atom["origin"] = origin
        landed_t = self._landing_t(seq)
        if landed_t is not None:
            if landed_t < t:
                # The witness (the attachment's file-order predecessor) is stamped BEFORE the
                # attachment's own ENQUEUE stamp. Real transcripts do this only by clock granularity
                # (the live corpus's worst case is -0.2 s, which whole-second stamps can turn into a
                # 1 s inversion); anything larger is a shape the CLI does not write. No truthful
                # landing precedes the send, and the chat's cue must never read "took it at" a time
                # before the bubble's own send time — so clamp to the send, and COUNT it: parse stats
                # (`landedT-clamp`, beside ts-repair, served on the version route) are where a run of
                # these would show up, since a silent clamp would hide a CLI write-order change. (The
                # 2026-09-06 review: two goldens had pinned a landedT 30-40 s before the send, from a
                # synthetic shape with no tool_result before the attachment.)
                if stats:
                    _asm_stat("landedT-clamp")
                landed_t = t
            atom["t"] = landed_t         # placed where the model READ it (T252d); `sentAt` keeps the send
        if ROMP_AUTO_RE.search(full):   # an AUTO-nudge → flag it, mirroring the native user-record path
            atom["rompAuto"] = True
        return atom

    def _landing_t(self, seq):
        """When the CLI TOOK a mid-turn prompt: the repaired stamp of the record written just BEFORE
        the queued_command attachment in file order. The attachment's own stamp is the ENQUEUE time
        (the moment the user sent it — kept on the atom as `sentAt`), but the CLI writes the record at
        the splice, right after the tool boundary it waited for, so that boundary's own record — the
        file-order predecessor — is the landing moment to within the boundary's latency, and since
        T252d it is where the atom is PLACED (its `t`): below the steps that ran while the message
        waited, where the model read it. The PREDECESSOR, not the successor, on purpose: it is always ingested when
        the atom is emitted, so a fold that sees the attachment as the newest record still stamps
        it — a successor read would find nothing there, and no later fold re-emits the atom (the
        (ts, text) dedup). Attachment records are skipped as witnesses (a run of splices at one
        boundary all read that boundary). None only when nothing precedes the attachment in the
        read. The caller clamps the result to the atom's own send time (never earlier — see
        _absorbed_atom). Placement, not metadata, since T252d: it is half of the atom's segment id."""
        i = bisect.bisect_left(self._seq_ts, (seq,)) - 1
        return self._seq_ts[i][1] if i >= 0 else None

    def _absorbed(self, qatts, kept, st, rompuuid, postal_index, stats=True):
        """Mid-turn prompts spliced into a running turn. The witness is the queued_command
        ATTACHMENT record: the CLI writes one per splice, uuid-bearing and parent-chained,
        carrying the FULL prompt text and stamped with the ENQUEUE timestamp — and writes
        NONE for a dequeued prompt (that resurfaces as a native user line), a still-pending
        one, or a popAll (a recall: the queue is cleared, nothing spliced). So each
        attachment becomes one user atom, placed at its LANDING time (_absorbed_atom, T252d) with
        the send time beside it; the (send ts, text) pair stays the dedup key below.

        The queue-operation ledger is deliberately NOT read at all: its dequeue/remove
        records are anonymous, and a CLI killed with items queued never writes their
        resolutions — one missing resolution shifted EVERY later FIFO pairing, so a message
        typed at 16:56 was stamped with another message's resolution time and rendered as
        the NEWEST message in the chat, hours out of place, while never-delivered
        task-notifications rendered as absorbed prompts at junk times (the user 2026-07-10,
        the nimbus session). The witness is universal: 0 of the live corpus's 104
        remove-bearing transcripts lack attachments, so dropping the ledger loses nothing.

        DEPLOY RULE: changing WHICH atoms this class emits from an existing transcript (here
        or in atoms()) changes placement identity just like an id drift — previously-invisible
        atoms become fresh plannable segments and dormant sessions replay them as new goals
        (2026-07-10). Bump jd.PLACEMENTS_V in the same commit; tests/test_placements_canary.py
        pins both dimensions.

        `stats` False: a caller that is no parse (the comment walk's fresh read, TranscriptGraph._splice_atom)
        adds nothing to the parse stats, whose landedT-clamp count is there to show a change in the CLI's
        write order across parses."""
        atoms, emitted = [], st["absorbed_keys"]   # dedup memory rides the carry: one set whether
        #                                            the qatts arrive in one full parse or many folds
        for q in qatts:
            if q["ts"] is None:
                continue   # unparseable timestamp — nowhere truthful to place it
            key = (q["ts"], _th(" ".join(q["text"].split())))
            if key in emitted:
                continue   # identical (ts, text) copies are the SAME splice written more than
                           # once (compaction/resume replays the record verbatim — x2 is common
                           # in the live corpus, one retry storm hit x24)
            if q["uuid"] is not None and q["uuid"] not in kept:
                continue   # this copy sits on a rewound branch — a kept twin may still emit
            emitted.add(key)
            a = self._absorbed_atom(q["text"], q["ts"], q["seq"], q["uuid"],
                                    rompuuid, postal_index, stats=stats)
            if not _author_final(a.get("author"), q["text"]):
                st["postal_miss_att"].add(key)   # trailing marker unresolved — the heal re-checks
            atoms.append(a)
        return atoms

    def atoms(self, rompuuid, postal_index):
        """Every emitted atom on the active path (plus broken-chain survivors and eclipsed
        machine-orphaned reply chains — see _select_eclipsed_chains), plus synthesized
        absorbed atoms. (Idle atoms are added separately from the state log.)
        One fold from an EMPTY carry over every kept record in chronological order — the
        assembly cache (_assemble) folds later appends through the same _prepass/_emit_fold
        code with the carried state, so full parse and fold are one implementation."""
        active = self.active_path()
        kept = self.kept_uuids(active)
        st = _emit_state()
        order = _chrono(self, kept)
        self._prepass(order, st)
        self.skill_loads = st["skill_loads"]   # read by _assemble beside the atoms (T333)
        out = list(self._emit_fold(order, st, rompuuid, postal_index))
        out += self._absorbed(self.qatts, kept, st, rompuuid, postal_index)
        return out

    def _prepass(self, order, st):
        """Fold the cross-record facts of `order` (kept records, ascending (t, seq)) into the
        carry `st`: replay classification, boundary summaries, Skill payload ids, command twins,
        and the chronological watermark. The emit loop reads these and never writes them. The
        assembly fold calls this with only the appended kept records — valid because its gates
        guarantee the delta sorts at-or-after everything already folded."""
        replay_uuids, _seen_text, _compacted = st["replay"], st["seen_text"], st["compacted"]
        _seen_exact, _restoring = st["seen_exact"], st["restoring"]
        summaries, last_boundary = st["summaries"], st["last_boundary"]
        # Post-compaction REPLAY dedup (the user 2026-06-22): a compact_boundary restores the recent message
        # tail VERBATIM with NEW uuids/timestamps. A replayed user prompt is the same text as an EARLIER one,
        # after a boundary — NOT new work; left in, it gets a fresh seg-id and the judges re-mint an
        # already-done (even CLEARED) goal. Identify replays in CHRONOLOGICAL order (the emit loop is
        # order-agnostic) so we keep the ORIGINAL and drop the later replay — then placements dedup still holds.
        for u in order:
            r = self.by_uuid.get(u)
            if not r:
                continue
            if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
                _compacted = True
                last_boundary = u
                # The boundary alone arms NOTHING (2026-09-19): the restore window opens at the
                # isCompactSummary record below, the record that begins the Claude CLI's replay.
                # Armed here, the window also covered a boundary that NO replay follows — a Codex
                # session compacts at the top of the next turn and writes no summary record, so the
                # record right after its boundary is the person's next prompt, and one whose text
                # repeated an earlier message ("continue", a canned follow-up sent twice) was read
                # as a replay and dropped: gone from the chat and the turns, its reply filed as a
                # triggerless continuation of the boundary's turn. Measured on one machine's live
                # corpus (2026-09-19, counts only): every attached boundary (338 in 32 transcripts,
                # all trigger auto) has its summary as the very next record in FILE order, but this
                # walk is (second, read order) and the CLI stamps the summary one second BEFORE its
                # boundary in 104 of the 338 (9 of them the transcript's first compaction), so there
                # the summary is walked first; no live window holds a textual replayed record (0 of
                # 338, so the atom sets could not tell the two arming records apart), and old-vs-new
                # direct parses of all 32 agree atom for atom and turn for turn (371,758 atoms and
                # 10,418 turns each side; no PLACEMENTS_V bump), while the cards carrying a summary
                # rose from 261 to 338 because an early-stamped summary now lands on its own card.
                # The summary branch keys on its OWN boundary, so the window opens before the first
                # replayed record on either stamp order (tests/test_event_model_compact_turn.py).
            elif r.get("type") == "assistant":
                _restoring = False         # work resumed → anything later is new, not restored context
            elif r.get("type") == "user" and r.get("isCompactSummary") is True:
                # The summary's OWN boundary, by the record's designed link (2026-09-19): its parent
                # when that is a compact_boundary on record, else the last boundary walked. The walk
                # is (second, read order) and the CLI stamps the summary one second before its
                # boundary in 104 of the 338 attached boundaries on one machine's corpus (every one
                # of the 338 parents its summary to its boundary), so keyed on the last boundary
                # walked this branch met NO boundary yet (a transcript's first compaction: the window
                # never opened and a replayed tail rendered as new asks) or the PREVIOUS one (an
                # adopted manual pair earlier in the session disarmed an unrelated auto compaction's
                # dedup, and this summary's text landed on the manual card). The fallback serves a
                # summary whose boundary is not on record here: a restored assembly entry holds only
                # the tail's records and carries last_boundary from its checkpoint.
                bp = self.parent_of.get(u)
                br = self.by_uuid.get(bp) if bp else None
                own = bp if (br is not None and br.get("type") == "system"
                             and br.get("subtype") == "compact_boundary") else last_boundary
                stext = _text_of(_content(r.get("message")))
                if own and stext:                      # attach to its own boundary; cap for transport
                    summaries[own] = stext[:SUMMARY_CAP] + (
                        "\n\n…(summary truncated)" if len(stext) > SUMMARY_CAP else "")
                if own and own not in self._adopted:
                    # the restore burst starts HERE — the summary is what the CLI writes before it
                    # replays the recent tail verbatim — and ends at the next assistant. An ADOPTED
                    # boundary's summary (a LIVE manual compact) never arms it: its transcript replays
                    # NO tail — the records after the pair are the user's genuine next actions, and
                    # the armed window silently ate the next typed prompt whenever its text repeated
                    # any earlier message ("continue", a nudge) — a dropped real ask (2026-08-19).
                    # Attached boundaries' summaries (auto, and the resume re-splice, which DOES
                    # replay) arm it. _adopted is complete by now: every _prepass caller runs on an
                    # adapter whose __init__ ended in _run_graph_passes (which fills it), or right
                    # after _asm_fold's own _run_graph_passes call (all five call sites read
                    # 2026-09-19), so membership here is the whole parse's, never a partial one.
                    _restoring = True
            elif r.get("type") == "user" and not r.get("isMeta"):
                txt = _text_of(_content(r.get("message")))
                if txt:
                    # A REPLAY is one of two measured shapes, and neither is "this text was said before"
                    # (the user 2026-08-01). Text alone identified something else entirely: any message a
                    # person ever repeats. Once a session had compacted, the SECOND "Now?", "retry",
                    # "[Request interrupted by user]" or romp notice was read as a replay of the first and
                    # dropped from the chat outright, however many days apart — the live case was a
                    # one-word question whose ANSWER rendered while the question itself did not, in a
                    # session that had compacted two days earlier. Over 22 compacted transcripts, 179
                    # duplicate texts differ in timestamp (genuine repeats, all being eaten) against 90
                    # that share one. So:
                    #   * a VERBATIM re-write — same text at the same SECOND — is the same record written
                    #     twice (resume/compaction re-splice; the absorbed-send dedupe below has keyed on
                    #     exactly this pair all along, and its comment records x2 as common, x24 once);
                    #   * a RESTORED TAIL — duplicate text inside the burst that follows a boundary, before
                    #     any assistant work resumes — is the shape this guard was written for.
                    # Outside those, a repeat is a message the person actually sent, and it renders.
                    # The exact-pair case needs NO compaction gate: a re-write carries the original's
                    # timestamp, so this chronological walk meets it right beside the original — before
                    # the boundary that produced it — and gating on _compacted would never fire. It is
                    # the same record either way. Only the restore-burst case is compaction-scoped.
                    if is_interrupt_record(r):
                        # The CLI's stop record closes the restore window, as an assistant record does
                        # (2026-10-22). Left open until the model's first record, a stop pressed before that
                        # record kept the window armed: the stop, and the person's next prompt parented at it,
                        # were each read as a replay and dropped whenever the text repeated an earlier message
                        # ("continue" after a stop). The prompt vanished from the chat and the turns, and its
                        # reply was filed under the stop's turn or, with an earlier stop in the session, as the
                        # compaction's triggerless continuation. This is a TRADEOFF, not an invariant. A stop
                        # pressed before the model's first record is the person's own gesture, and so is the
                        # prompt after it. A restored tail that itself carries a stop copy also closes the
                        # window at that copy, so the later copies in that burst render as new asks. The two
                        # look alike (the stop's text and parent are the same in both), so no event tells them
                        # apart. Accepted: no live restore window held any textual replayed record (0 of 338,
                        # the 2026-09-19 measurement above), while a stop before the model's first record is a
                        # live shape. tests/test_event_model_compact_turn.py pins both sides, so a later change
                        # to the window is a visible decision.
                        _restoring = False
                    th = _th(txt)
                    key = (int(self.ts_of.get(u) or 0), th)
                    if key in _seen_exact or (_compacted and _restoring and th in _seen_text):
                        replay_uuids.add(u)
                    else:
                        _seen_exact.add(key)
                        _seen_text.add(th)
        st["compacted"], st["restoring"], st["last_boundary"] = _compacted, _restoring, last_boundary
        # The chronological watermark the assembly fold's monotonic gate compares appended records
        # against: a delta that sorts at-or-after everything already folded is the invariant that
        # makes carrying the sets above across folds valid at all. `order` is ascending by
        # (ts_of, seq), so the LAST pre-pass-relevant record carries the max — one lookup, not a
        # second sweep of the whole session (that re-walk measured 33ms at 30k). MUST read the
        # repaired ts_of, the exact key the sort used: reading the raw stamp here let a garbled
        # TAIL record (which borrows the max stamp and sorts last) zero the watermark and disarm
        # the g:ts gate — a reproduced fold!=full divergence (T210 review).
        for u in reversed(order):
            r = self.by_uuid.get(u) or {}
            if r.get("type") in ("user", "assistant") or (
                    r.get("type") == "system" and r.get("subtype") == "compact_boundary"):
                ts = self.ts_of.get(u)
                if ts and ts > st["max_ppt"]:
                    st["max_ppt"] = ts
                break
        # Skill tool_use block ids: the anchor for the NEW skill-instructions shape (2026-07-10). Newer
        # CLIs inject the payload as an isMeta user record whose sourceToolUseID names the invoking Skill
        # tool_use — the text no longer starts with the "Base directory for this skill:" preamble
        # SKILL_CONTENT_RE keys on, so the designed link is the id, not a prefix.
        skill_tool_ids = st["skill_ids"]
        for u in order:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "assistant":
                for b in _content(r.get("message")) or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and \
                            b.get("name") == "Skill" and b.get("id"):
                        skill_tool_ids.add(b["id"])
        # Bare invocation TWINS (CLI 2.1.215+, the user 2026-07-20): a typed slash command lands TWICE —
        # a raw-text user record (the submitted prompt verbatim, carrying promptId) AND the
        # <command-name> wrapper (same promptId). The wrapper becomes the tracked command atom below;
        # the raw twin carries no wrapper, no isMeta, no isCompactSummary, so it would fall through as
        # a genuine HUMAN atom — and the planner minted a feed card from a /compact ("Compact
        # conversation context", the rescue thread). Collect wrapper promptIds so the twin drops as the
        # invocation echo it is.
        cmd_prompt_names, skill_loads = st["cmd_names"], st["skill_loads"]
        for u in order:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "user":
                btext = _text_of(_content(r.get("message"))) or ""
                if is_skill_load_wrapper(btext):   # the harness's own skill load: no invocation, so no twin to drop;
                    m = COMMAND_NAME_ANY_RE.search(btext)   # recorded for the judge's stamp (session["skillLoads"]),
                    skill_loads[u] = (m.group(1).strip() if m else "") or "skill"   # across every file this walk crossed
                    continue
                if not r.get("promptId"):
                    continue
                # the SAME matcher the emit path uses (COMMAND_NAME_ANY_RE inside a wrapper record): the
                # anchored-only form missed every <command-message>-FIRST invocation (skills / custom
                # commands), so shape-B twins survived as phantom human segments beside the real command
                # atom — same hash, different t (2026-08-13; the emit path got this fix on 2026-07-22 and
                # this pre-pass silently didn't).
                m = COMMAND_NAME_RE.match(btext) or (COMMAND_NAME_ANY_RE.search(btext)
                                                     if CMD_WRAP_RE.match(btext) else None)
                if m:
                    name = m.group(1).strip() or "/?"
                    cmd_prompt_names.setdefault(r["promptId"], set()).add(
                        name if name.startswith("/") else "/" + name)

    def _emit_fold(self, order, st, rompuuid, postal_index):
        """Yield each kept record's atom (some records emit none) — record-local given the carry
        `st` that _prepass filled and the graph maps. The full parse and the assembly fold drive
        this SAME generator; the fold passes only the appended kept records, so the two paths
        cannot drift."""
        replay_uuids, summaries = st["replay"], st["summaries"]
        skill_tool_ids, cmd_prompt_names = st["skill_ids"], st["cmd_names"]
        for u in order:
            r = self.by_uuid.get(u)
            if not r:
                continue
            t = r.get("type")
            ts = self.ts_of.get(u, 0)   # repaired at ingest — never None (T210)
            fsid = self.fsid_of.get(u)
            seq = self.seq_of.get(u, 0)
            if t == "assistant":
                a = {"type": "assistant", "uuid": u, "session_id": rompuuid,
                     "t": ts, "fsid": fsid, "parentUuid": r.get("parentUuid"),
                     "message": _norm_message(r.get("message")), "_seq": seq}
                if r.get("isApiErrorMessage"):
                    a["isApiError"] = True   # a FAILURE record — Claude Code writes the error as an
                                             # assistant text block, so it carries text but is NOT a
                                             # reply; deep-link anchors must skip it (kernel _seg_anchors)
                    if isinstance(r.get("apiErrorStatus"), int):
                        a["apiErrorStatus"] = r["apiErrorStatus"]   # → the durable chat card's badge ("API error · 529")
                yield a
            elif t == "user":
                blocks = _content(r.get("message"))
                btext = _text_of(blocks) if blocks else ""
                # SLASH-COMMAND TURN (the user 2026-06-29): a "/usage"-style command is no longer dropped — its
                # INVOCATION becomes a `command`-flagged user atom (an opener → a tracked, working turn that
                # shows in the chat + timeline) and its OUTPUT a synthetic assistant atom (so the turn has a
                # reply and ENDS naturally). The `command` flag makes the planner/judge skip it (never a goal /
                # feed card — see _seg_command). This runs BEFORE the isMeta skip because some Claude versions
                # mark these records isMeta. The other wrappers (message/args/contents/caveat) stay skipped.
                if is_skill_load_wrapper(btext):
                    continue   # the harness's own skill load (bare name + <skill-format>): noise, never a command turn
                mcmd = COMMAND_NAME_RE.match(btext) or (COMMAND_NAME_ANY_RE.search(btext)
                                                        if CMD_WRAP_RE.match(btext) else None)
                if mcmd and u not in replay_uuids:
                    name = mcmd.group(1).strip() or "/?"
                    if not name.startswith("/"):
                        name = "/" + name
                    margs = COMMAND_ARGS_RE.search(btext)
                    args = (margs.group(1).strip() if margs else "")
                    disp = name + ((" " + args) if args else "")
                    yield {"type": "user", "uuid": u, "session_id": rompuuid, "t": ts,
                           "fsid": fsid, "parentUuid": r.get("parentUuid"), "_seq": seq,
                           "author": "human", "command": name,
                           "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
                    continue
                mout = local_command_output(btext)   # stdout, or stderr when the command failed
                if mout and u not in replay_uuids:
                    yield {"type": "assistant", "uuid": u, "session_id": rompuuid, "t": ts,
                           "fsid": fsid, "parentUuid": r.get("parentUuid"), "_seq": seq, "command": True,
                           "message": {"role": "assistant",
                                       "content": [{"type": "text", "text": strip_ansi(mout.group(1)).strip()}],
                                       "stop_reason": "end_turn"}}
                    continue
                has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result"
                                      for b in (blocks or []))
                twins = cmd_prompt_names.get(r.get("promptId") or "")
                if twins and not has_tool_result and any(
                        btext.strip() == n or btext.strip().startswith(n + " ") for n in twins):
                    continue   # the raw-text TWIN of a slash invocation (see the pre-pass above) — the
                               # wrapper is the one tracked command atom; this is its echo, not a message
                if btext and not has_tool_result and u not in replay_uuids and \
                        (SKILL_CONTENT_RE.match(btext) or r.get("sourceToolUseID") in skill_tool_ids):
                    # a Skill invocation's INSTRUCTIONS payload — kept, but flagged and content-EMPTY:
                    # assistant-flavored with no stop so it can neither open nor close the running turn,
                    # and the markdown rides skillMd where generic text readers never look. BEFORE the
                    # isMeta skip (the CLI marks the record isMeta), like the command paths above (the
                    # user 2026-07-08). TWO shapes: the legacy "Base directory for this skill:" preamble
                    # (SKILL_CONTENT_RE) and the newer sourceToolUseID link to the invoking Skill
                    # tool_use (the user 2026-07-10 — a 151KB skill md rendered as a giant note box
                    # because the prefix missed, the isMeta skip ate the record, and the un-superseded
                    # LIVE atom stuck around forever). The tool_result guard keeps the Skill tool's own
                    # "Launching skill: X" result out of this branch if it ever carries the same link.
                    yield {"type": "assistant", "uuid": u, "session_id": rompuuid, "t": ts,
                           "fsid": fsid, "parentUuid": r.get("parentUuid"), "_seq": seq,
                           "skillMd": btext[:SKILL_MD_CAP] + ("\n\n…(skill content truncated)"
                                                              if len(btext) > SKILL_MD_CAP else ""),
                           "message": {"role": "assistant", "content": [], "stop_reason": None}}
                    continue
                # A postal DELIVERY is a message, not harness noise, even though the CLI flags it isMeta:
                # Claude Code hands romp mail to a session as Stop-hook feedback, and that record carries
                # isMeta, so this skip ate every hook-delivered message whole. It never became an atom, so
                # no user event reached _hydrate_postal, no incoming card was built, and nothing carried
                # the message id — which is why a timeline arc into one of these landed nowhere while the
                # transcript plainly contained it (the user 2026-07-23; romp_docs found the record).
                # Deliveries arriving by other paths were unaffected, which is why it failed for some
                # messages and not others. Keyed on the romp-msg-id marker, so only real mail is admitted
                # and the `<command-…>` echoes and caveats stay skipped.
                if IMG_ECHO_RE.match(btext) and not has_tool_result:
                    continue   # synthetic image-read placeholder (twin of sdk_backend's _IMG_ECHO_RE) — the
                               # tool that fed the image already shows; on disk it's also isMeta (below)
                if r.get("isMeta") is True and not POSTAL_RE.search(btext):
                    continue   # `<command-…>` echoes / caveats — harness noise, not a message
                if r.get("isCompactSummary") is True:
                    continue   # the compaction SUMMARY payload — kept in the graph (above) but not an atom;
                               # the compaction itself is the system:compact_boundary atom below
                if u in replay_uuids:
                    continue   # a post-compaction REPLAY of restored context (see the pre-pass above) — not new
                               # work; dropping it keeps the planner's seg-id dedup intact, so no goal re-mints
                if not blocks:
                    continue
                if CMD_WRAP_RE.match(btext):
                    continue   # the remaining slash-command wrappers (message/args/contents/caveat) — noise
                ps = r.get("promptSource")
                atom = {"type": "user", "uuid": u, "session_id": rompuuid, "t": ts,
                        "fsid": fsid, "parentUuid": r.get("parentUuid"),
                        "message": _norm_message(r.get("message")), "_seq": seq}
                if has_tool_result and isinstance(r.get("toolUseResult"), dict) \
                        and (set(r["toolUseResult"]) & TUR_CONSUMED_KEYS):
                    # The record's top-level toolUseResult — Claude Code's STRUCTURED result (Edit's
                    # structuredPatch, AskUserQuestion's answers map). The kernel's chat build reads it
                    # at tool_result attach time; the atom used to drop it, so every consumer silently
                    # fell to its lossy fallback (regex-scraping the flat output string — which is how
                    # quote-bearing AskUserQuestion answers vanished from the answered box). Dict form
                    # only: an errored result records a plain string, which no consumer reads. Carried
                    # ONLY when a consumed key is present (_TUR_CONSUMED_KEYS): an unconditional carry
                    # held every Read result's full file bytes in the parse cache by reference —
                    # roughly a fifth of transcript bytes on read-heavy sessions — for shapes nothing
                    # reads. Widen the key set when a new consumer appears; never back to carry-all.
                    atom["toolUseResult"] = r["toolUseResult"]
                if ps:
                    atom["promptSource"] = ps
                origin = _record_origin(r)
                if origin:
                    atom["origin"] = origin      # the CLI's provenance stamp rides the atom (the chat's source head)
                author = author_of(blocks, ps, postal_index, getattr(self, "sdk_human", False), origin)
                if author is not None:
                    atom["author"] = author
                    if not _author_final(author, btext):
                        st["postal_miss_rec"].add(u)   # trailing marker unresolved — the assembly
                        #                                heal re-authors this record until it lands
                if ROMP_AUTO_RE.search(_text_of(blocks)):   # an AUTO-nudge → flag it (vs a button/typed follow-up)
                    atom["rompAuto"] = True
                yield atom
            elif t == "system" and r.get("subtype") == "compact_boundary":
                if (r.get("parentUuid") or r.get("logicalParentUuid")) == u:
                    continue   # self-anchored (corrupt): a boundary claiming to compact itself
                               # anchors nothing — no card, and no cycle for the turn builder
                sp = self._adopted.get(u)
                if sp is not None:
                    # an ADOPTED boundary's record is appended BEFORE its episode's stdout, so raw
                    # (t, seq) order would drop the card into the middle of the /compact exchange —
                    # and the stdout atom would then fold into the boundary's fresh turn as
                    # "assistant work", minting a phantom WORK unit (2026-08-19). Sort it right
                    # after the stdout instead: the moment the compaction visibly completed.
                    spt = self.ts_of.get(sp)
                    if spt is not None and (ts is None or spt > ts):
                        ts = spt
                    seq = self.seq_of.get(sp, seq) + 0.5
                cm = r.get("compactMetadata") or r.get("compact_metadata") or {}
                yield {"type": "system", "subtype": "compact_boundary", "uuid": u,
                       "session_id": rompuuid, "t": ts, "fsid": fsid,
                       "parentUuid": self.parent_of.get(u),   # the repaired stitch (see _repair_compaction_stitches);
                       #                                        for an ADOPTED boundary, its episode's stdout record
                       "compact_metadata": {"trigger": cm.get("trigger"),
                                            "pre_tokens": cm.get("preTokens") or cm.get("pre_tokens"),
                                            "post_tokens": cm.get("postTokens") or cm.get("post_tokens")},
                       "summary": summaries.get(u),   # the compaction SUMMARY captured in the pre-pass (or None)
                       "_seq": seq}
            elif t == "system" and r.get("subtype") == "model_refusal_fallback":
                # The model's safeguards flagged the prompt and the CLI silently retried the turn on a
                # fallback model. That is CONVERSATION state, not harness bookkeeping — the reply that
                # follows came from a different model, and the user must see the swap where it happened
                # (the user 2026-08-03, after a fable→opus swap mid-turn was invisible in the chat).
                # The record's timestamp is the retry start, so the atom sorts BEFORE the fallback
                # model's reply. Non-opener (not a user atom), so it folds into the running turn.
                yield {"type": "system", "subtype": "model_refusal_fallback", "uuid": u,
                       "session_id": rompuuid, "t": ts, "fsid": fsid,
                       "parentUuid": self.parent_of.get(u),
                       "content": r.get("content") or "",          # the CLI's full explanation
                       "fallback_from": r.get("originalModel") or "",
                       "fallback_to": r.get("fallbackModel") or "",
                       # T279: the refusal category (an open string; null when neither lane carried one),
                       # the API's explanation (display-only prose; null on server-lane banners) and the
                       # scope ('session' = the session model is swapped; 'local' = a subagent's or a
                       # side question's reply only; absent on older CLIs = session)
                       "refusal_category": r.get("apiRefusalCategory") or "",
                       "refusal_explanation": r.get("apiRefusalExplanation") or "",
                       "scope": r.get("scope") or "session",
                       "_seq": seq}
            # other system subtypes (turn_duration, stop_hook_summary, local_command,
            # away_summary) are harness bookkeeping, not conversational messages -> skipped.


# A session is NOT working once it has STOPPED: the states/ log records state:"waiting" when a turn's result
# lands (the agent handed the floor back — the SDK backend writes it on the ResultMessage; the tmux backend's
# Stop hook did until its removal, 2026-09-11) and state:"idle" for a stop without one (an interrupt now; the
# idle-prompt, then). Both terminate the turn — keying
# only on "idle" left a finished session whose last assistant message wasn't a clean end_turn (e.g. it ended
# on a tool_use) stuck reading "working" from Stop until the idle-prompt eventually landed (the user 2026-06-25,
# "reverting working when stuff isn't working"). Event-based, not a grace timer.
_IDLE_STATES = ("idle", "waiting")


def synthesize_idle(states, atoms, now):
    """Idle atoms from real idle/stopped transitions in states/<sid>.jsonl — NOT a 15-minute silence
    heuristic. An idle span runs from an `state` in _IDLE_STATES (the Stop "waiting" or the idle-prompt
    "idle") to the next state record (or to `now` if it is the last). Only spans that START inside the
    session's atom timespan are kept, so unrelated history doesn't leak in.

    A span that starts before the parse's first atom belongs to no turn of this conversation: the SDK
    backend's create-time "waiting" (ended by the first prompt's "working", often in the same whole
    second as the prompt's record) or the previous conversation's last stop before a /clear. Kept, its
    atom sorted ahead of the first prompt and led turn 0, which then took the prompt in as a mid-turn
    input: the turn was triggerless, started at the create time, and held an idle atom from its start,
    so every reader testing for an idle atom in the last turn read a running first turn as closed. A
    restored parse drops the same spans (parse_session keeps idle at or after the tail's first atom)."""
    rows = sorted([r for r in states if isinstance(r, dict) and r.get("t") is not None],
                  key=lambda r: r["t"])
    if not rows or not atoms:
        return []
    lo = min(a["t"] for a in atoms)
    hi = max(a.get("end", a["t"]) for a in atoms)
    out = []
    for i, r in enumerate(rows):
        if r.get("state") not in _IDLE_STATES:
            continue
        start = r["t"]
        end = rows[i + 1]["t"] if i + 1 < len(rows) else (now if now is not None else hi)
        if end <= start:
            continue
        if start < lo or start > max(hi, now or hi):
            continue   # span starting outside the session's activity window (before its first atom: see above)
        out.append({"type": "idle", "uuid": None, "session_id": atoms[0]["session_id"],
                    "t": start, "end": end, "_seq": 10 ** 12 + start})
    return out


def synthesize_orphans(states, atoms, landed_text_uuids=None, rompuuid=None, pre=None, t_floor=None, overlay=None):
    """Salvaged assistant replies from orphanReply markers in states/<sid>.jsonl — text that STREAMED
    live but the transcript never kept (an API-errored try; the SDK backend persists it at settle,
    see its append_orphan_reply). The kernel's chat build has interleaved these since 2026-07-21, but
    this parse never did — so every judge (planner/closer/distiller/briefer) read those turns as
    having NO reply at all (found by a peer session 2026-07-25, ~1,600 markers fleet-wide): the
    planner confabulated outcomes for them, and once the workless-segment guard landed it flipped to
    never filing genuinely finished work as done. Each marker becomes a real assistant atom at its
    timestamp, DEDUP'd the way the chat build dedups — by uuid against every text-bearing record,
    then exact-or-either-way-prefix text against what the disk kept (a retry that re-replied never
    doubles) and against earlier markers (settles can re-orphan the same reply), in the chat's window
    only (its _texts_near): the turn holding the first atom at or after the marker's time and the
    turn on each side. A reply kept far from the marker's time is not this reply: matched against
    the whole session, a short salvaged reply ("Done.") that an early turn also said, or a partial
    stream that prefixes a far reply, was dropped here while the chat showed it, and every judge read
    its turn as having no reply (2026-10-25). A marker carrying the CLI's own error text
    ("API Error: …") is skipped: markers written before the backend tagged error settles isApiError
    hold that noise, and it must not resurface as work.

    landed_text_uuids (FileAdapter.landed_text_uuids): text-bearing assistant uuids on ANY branch
    of the transcript graph, kept or dropped. A marker whose uuid landed SOMEWHERE is never a loss
    — when its record is off the kept path, that is a rollback's deliberate abandonment, and
    resurrecting it un-deletes the tail the user rolled back (the ghost-reply bug, the user
    2026-08-03: the deleted message vanished but its reply came back once the rollback was
    consumed, since the atoms-only dedup below can no longer see the abandoned record).

    overlay: a callable returning the idle atoms the parse adds after these (synthesize_idle). An
    idle span stretches its turn's end, which decides the turn holding a marker's time as the chat
    reads it; called once, and only when a marker's text needs the window."""
    if not atoms:
        return []
    if t_floor is not None:                                           # a restored parse: only a marker the tail can hold counts (the
        states = [r for r in states or [] if isinstance(r, dict) and (r.get("t") or 0) >= t_floor]   #  caller discards the rest)
    if not any(isinstance(r, dict) and r.get("t") and isinstance(r.get("orphanReply"), dict) for r in states or []):
        return []                                                     # no marker: nothing to salvage, and no pre-cut row decoded
    # TEXT-BEARING uuids only (the user 2026-07-28): a marker whose uuid the disk knows solely as a
    # TEXTLESS record must still interleave. On some model+tool combinations (observed: fable-5 replying
    # before an AskUserQuestion) the CLI persists the streamed reply text as an EMPTY thinking record
    # under the same uuid — the very loss the marker salvages — so counting that twin as "seen" ate the
    # salvage. A retry that DID re-reply carries its text and still dedups, as does a re-orphaned marker
    # (the add below); the text check in the marker's window guards every remaining double.
    # The tail's atoms only. A restored tree's pre-cut replies are in landed_text_uuids (the document's records carry the
    # bit), and a reply salvaged into a pre-cut turn came from a marker below the floor: the backend stamps a marker with the
    # streamed reply's own time, so a reply orphaned again at a later settle keeps it. Walking every pre-cut row for its uuid
    # decoded the whole history on every parse while any marker sat in the tail, one skipped by its landed uuid included.
    seen_uuids = {a.get("uuid") for a in atoms
                  if a.get("uuid") and (a["lazy"].get("nt") if a.get("lazy") is not None else _text_of(_content(a.get("message"))).strip())}
    pre_says = {}                                                     # restored pre-cut turn index (stage 4c) → its assistants' text rows

    def _pre_says(i):
        """Pre-cut turn i's assistant text rows as (slot, hash), one decode per row and no atom built, read only for a turn in
        some marker's window and kept for the next marker."""
        if i not in pre_says:
            la, says = pre[i]["atoms"], []
            for k, r in enumerate(la.rows()):
                typ, nt, hh = la._index.text_flags(r)
                if nt and typ == "assistant":                          # a user prompt or a command with the same text is not a
                    says.append((k, hh))                               #  kept reply, so only assistants count (round 3 M2a)
            pre_says[i] = says
        return pre_says[i]
    disk_texts = [t for a in atoms if a.get("type") == "assistant" and a.get("lazy") is None
                  if (t := _text_of(_content(a.get("message"))).strip())]
    # a kept reply with no body in hand (a lazy atom before an assembly checkpoint's cut, a pre-cut turn): the window decides
    unread = bool(pre) or any(a.get("type") == "assistant" and a["lazy"].get("nt") for a in atoms if a.get("lazy") is not None)
    sid = atoms[0]["session_id"]
    made = []                                                         # (t, text) of the markers salvaged so far
    win = [None]                                                      # the turns, grouped once, only if a marker's text needs them
    said = {}                                                         # turn index → the assistant texts it kept

    def _either(txt, texts):
        return any(dt.startswith(txt) or txt.startswith(dt) for dt in texts)

    def _turns():
        """(start, end, source) per turn of the tree this parse returns: the restored pre-cut turns, then the tail as
        segment_turns groups it with the idle spans the parse adds, without the salvaged replies (a dropped one is not in
        the tree the chat reads either)."""
        if win[0] is None:
            tail = segment_turns(atoms + list(overlay() if overlay is not None else ()), sid)
            win[0] = ([(pt["t"], pt.get("end") or pt.get("t") or 0, ("pre", i)) for i, pt in enumerate(pre or ())]
                      + [(tu["t"], tu.get("end") or tu.get("t") or 0, ("tail", tu["atoms"])) for tu in tail])
        return win[0]

    def _said(i):
        """The assistant texts turn i kept, its lazy bodies hydrated here: a turn in some marker's window, never the rest."""
        if i not in said:
            kind, ref = _turns()[i][2]
            if kind == "pre":
                la = pre[ref]["atoms"]
                las = [la[k] for k, hh in _pre_says(ref)]
            else:
                las = [a for a in ref if a.get("type") == "assistant" and (a.get("lazy") is None or a["lazy"].get("nt"))]
            hydrate(las, rompuuid or sid, by="synthesize_orphans")
            said[i] = [t for a in las if (t := _text_of(_content(a.get("message"))).strip())]
        return said[i]

    def _kept_near(t, txt):
        """Whether the turn holding the first atom at or after `t`, or the turn on either side, kept `txt` exactly or as a
        prefix either way (the chat's _texts_near over the same turns), or a marker salvaged into one of them did."""
        turns = _turns()
        j = next((i for i, (s, e, src) in enumerate(turns) if e >= t), len(turns) - 1)
        near = range(max(0, j - 1), min(len(turns), j + 2))
        h = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:8]
        if any(turns[i][2][0] == "pre" and any(hh == h for k, hh in _pre_says(turns[i][2][1])) for i in near):
            return True                                               # a pre-cut reply kept exactly, from the rows' hashes: no atom built
        starts = [s for s, e, src in turns]
        if any(max(0, bisect.bisect_right(starts, tm) - 1) in near and _either(txt, (mt,)) for tm, mt in made):
            return True                                               # a salvaged reply joins the turn running at its time
        return any(_either(txt, _said(i)) for i in near)
    out = []
    for r in states or []:
        if not isinstance(r, dict) or not r.get("t"):
            continue
        orq = r.get("orphanReply")
        if not isinstance(orq, dict):
            continue
        txt = (orq.get("text") or "").strip()
        if not txt or txt.startswith("API Error:") or txt == "(no content)":
            # "(no content)" is the CLI's own placeholder for a contentless command-feedback message
            # (an SDK /clear streams one; its transcript record is a system/local_command row). Salvaged
            # as a real assistant atom it read as MODEL WORK: _seg_command_worked turned True for the
            # bare /clear turn and the planner minted a "clearing conversation history" card (the user
            # 2026-07-27). Every such marker in the live corpus rides a command turn, which plans no
            # units at all once skipped — so this drops no real work and needs no PLACEMENTS_V bump.
            continue
        u = orq.get("uuid") or ""
        if u and landed_text_uuids is not None and u in landed_text_uuids:
            continue   # the disk kept this reply on SOME branch — possibly one a rollback abandoned
        if u and u in seen_uuids:
            continue
        if (unread or _either(txt, disk_texts) or _either(txt, [mt for tm, mt in made])) and _kept_near(int(r["t"]), txt):
            continue                                                   # kept near its time (a text no kept reply matches skips the window)
        out.append({"type": "assistant", "uuid": u or ("orphan:%d" % int(r["t"])), "session_id": sid,
                    "t": int(r["t"]), "fsid": None, "parentUuid": None, "orphaned": True,
                    "message": {"role": "assistant", "content": [{"type": "text", "text": txt}],
                                "stop_reason": "end_turn"},   # the marker is written AT settle — the turn ended
                    "_seq": 10 ** 12 + int(r["t"])})
        if u:
            seen_uuids.add(u)
        made.append((int(r["t"]), txt))
    return out


# ═════════════════════════ SUBSTRATE-NEUTRAL: turns over atoms ═════════════════════════
class LazyBodyRead(RuntimeError):
    """A consumer read the message of an atom restored from the assembly checkpoint without hydrating it first
    (em.hydrate). Raised on any read, in tests and in the kernel alike: a silent empty body would misclassify
    the atom (no text, no tool blocks) where a loud failure names the consumer that bypassed hydration."""


class _Unhydrated:
    """The one value inside a _LazyBody's storage: not JSON serializable, so a lazy atom cannot leave the kernel as an
    empty message by accident."""
    __slots__ = ("uuid",)

    def __init__(self, uuid):
        self.uuid = uuid

    def __repr__(self):
        return "<unhydrated body of %s>" % self.uuid


_UNBOUND_LAZY_SOURCE = object()    # compatibility for descriptors constructed without a verified document (2026-09-17)


class _LazyBody(dict):
    """The message of a lazy atom: a dict-shaped sentinel that refuses every read. `_content` sees a dict and asks
    it for content; the ask raises LazyBodyRead with the atom's uuid, so the bypassing site is on the traceback."""
    __slots__ = ("uuid", "source_path")

    def __init__(self, uuid, source_path=_UNBOUND_LAZY_SOURCE):
        super().__init__()
        self.uuid = uuid
        self.source_path = source_path                  # private, not a wire field: this snapshot's verified source (2026-09-17)
        super().__setitem__("lazy body", _Unhydrated(uuid))   # the C json encoder walks a dict subclass's storage
        #                                                        directly, never through get/items: the value inside makes
        #                                                        json.dumps raise (TypeError, not JSON serializable) instead
        #                                                        of shipping an empty message

    def _refuse(self, *a, **k):
        raise LazyBodyRead("atom %s: message read before hydration (em.hydrate)" % self.uuid)

    get = __getitem__ = __contains__ = items = keys = values = __iter__ = __len__ = _refuse

    def __bool__(self):
        return True

    def __eq__(self, other):
        # the source slot and the uuid, not the class (2026-09-21): __hash__ keys on ("lazy", uuid) with no class in it, and
        # a class check resolved _LazyBody from the module's globals at call time, so across the loader's re-execution
        # (see is_lazy) two old sentinels of one uuid compared unequal, and an old one equaled a new one of its uuid that
        # did not equal it back. Only this class declares the slot here; _Unhydrated has a uuid alone and stays unequal
        return hasattr(other, "source_path") and getattr(other, "uuid", None) == self.uuid

    def __ne__(self, other):
        # the dict base's own __ne__ sits before object's in the lookup, so without this != compared storage, two distinct
        # _Unhydrated values, and a same-uuid pair answered both == True and != True (2026-09-21)
        return not self.__eq__(other)

    def __hash__(self):
        return hash(("lazy", self.uuid))

    def __repr__(self):
        return "<lazy body of %s>" % self.uuid


def is_lazy(atom):
    # the source slot, not the class, the one test the first loop of hydrate keys on (2026-09-21): the module loader
    # re-executes this file into the same module object at every import and rebinds _LazyBody, so a sentinel built
    # before a re-execution is no instance of the current class, and a class check answered False for a lazy body.
    # A plain dict and None have no slot and answer False as before
    return hasattr(atom.get("message"), "source_path")


def _text_hash8(atom):
    """sha1(text)[:8] of an atom's text, the ids' content hash: read from the atom's lazy scalars when it has no body."""
    lz = atom.get("lazy")
    if lz is not None:
        return lz["h"]
    return hashlib.sha1(_text_of(_content(atom.get("message"))).encode("utf-8", "replace")).hexdigest()[:8]


def _stop_reason(atom):
    lz = atom.get("lazy")
    if lz is not None:
        return lz.get("sr")
    return (atom.get("message") or {}).get("stop_reason")


def is_interrupt_record(atom):
    """The CLI's own stop record — a user atom reading '[Request interrupted by user]' (Esc) or
    '[Request interrupted by user for tool use]' (a permission prompt dismissed). It is the interrupt
    EVENT itself, written by the CLI whether the stop came from romp's Stop button or a raw Esc in the
    pane — so it must END its turn (the user 2026-07-05: without this, the dangling user atom read as
    an OPEN turn, so the chip latched 'Interrupting…' for the full 120s cap and _ops_gate parked a
    /model pick against a session that was actually idle). Public: the kernel's auto-nudge gate keys
    on the same event."""
    if atom.get("type") != "user":
        return False
    lz = atom.get("lazy")
    if lz is not None:
        return bool(lz.get("ir"))
    return _text_of(_content(atom.get("message"))).startswith("[Request interrupted by user")


def _is_opener(atom):
    """A genuine new prompt opens a turn: author human / sdk / peer / romp. `system`
    (`<task-notification>`) and tool_result-only atoms fold in, never open. A romp follow-up
    (a feed NUDGE / auto-nudge carrying the romp-injected marker, author 'romp') IS a fresh
    prompt to the agent — it MUST open its own turn so the planner reads the romp-goal-id off
    the trigger, reopens that goal, and files the reply under it. Without this it folds into the
    prior (often already-completed) turn, so the judges never see the follow-up and the goal
    never reopens (the user 2026-06-21)."""
    if atom["type"] != "user":
        return False
    a = atom.get("author")
    return a in ("human", "sdk", "romp") or isinstance(a, dict)


def _turn_id(rompuuid, turn):
    """`${rompUuid}:${t}:${hash}` — anchor-keyed, fork-stable (the trigger's text, or the
    first atom's text for an autonomous turn)."""
    atoms = turn["atoms"]
    trig = turn["trigger"]
    a = None
    if trig:
        a = next((x for x in atoms if x.get("uuid") == trig["uuid"]), None)
    elif atoms:
        a = atoms[0]
    h = _text_hash8(a) if a is not None else hashlib.sha1(b"").hexdigest()[:8]
    return "%s:%d:%s" % (rompuuid, turn["t"], h)


def segment_turns(atoms, rompuuid):
    """Group atoms into `end_turn`-bounded turns. A turn opens at an opener atom (or at
    the first non-opener if work begins without one) and runs until the next opener
    that arrives AFTER the turn hit `end_turn`. A new prompt arriving while the turn is
    still open (last assistant stop_reason != end_turn) is a mid-turn input (absorb),
    kept inside the turn — that is how one turn holds several inputs."""
    atoms = sorted(atoms, key=lambda a: (a["t"], a.get("_seq", 0)))
    turns = []
    cur = None
    ended = False     # has the current turn hit end_turn since its last opener?
    for atom in atoms:
        if atom["type"] == "system" and atom.get("subtype") == "compact_boundary":
            # Compaction always opens a FRESH turn (the user 2026-07-13): a non-opener would absorb into
            # the current turn, and _finalize_turn's end = max(atom ends) then stretched that turn's bar
            # to the boundary's timestamp — the timeline drew a phantom work period spanning the whole
            # idle gap "leading up to the moment of compaction", growing live while the compact ran. The
            # boundary anchors its own turn instead. ended=True so a GENUINE post-compact prompt opens
            # its own turn (it's a real ask — the planner needs it as a trigger); the CLI's autonomous
            # continuation (assistant atoms, non-openers) still files under the boundary turn, so its
            # bar starts AT the compaction, never before.
            cur = {"trigger": None, "atoms": [atom]}
            turns.append(cur)
            ended = True
            continue
        if _is_opener(atom):
            if cur is None or ended:
                cur = {"trigger": {"uuid": atom.get("uuid")}, "atoms": [atom]}
                turns.append(cur)
                ended = False
            else:
                cur["atoms"].append(atom)   # mid-turn input (absorbed)
        else:
            if cur is None:
                cur = {"trigger": None, "atoms": [atom]}   # autonomous / continuation
                turns.append(cur)
                ended = False
            else:
                cur["atoms"].append(atom)
        if atom["type"] == "assistant":
            sr = _stop_reason(atom)
            ended = sr in END_STOPS
        if atom["type"] == "user" and atom.get("command"):
            ended = True   # a slash-command invocation is self-contained → ends its turn so the NEXT prompt opens fresh
        if atom["type"] == "user" and is_interrupt_record(atom):
            ended = True   # the CLI's stop record — the interrupted turn is OVER; the next prompt opens fresh
    for turn in turns:
        _finalize_turn(turn, rompuuid)
    turns.sort(key=lambda t: t["t"])
    return turns


def _finalize_turn(turn, rompuuid):
    atoms = turn["atoms"]
    turn["t"] = atoms[0]["t"]
    turn["end"] = max(a.get("end", a["t"]) for a in atoms)
    # ended (FILE substrate): inferred from the turn's last assistant stop_reason, since
    # the transcript carries no `result` line. Interrupted / still-streaming -> False.
    last_sr = None
    for a in atoms:
        if a["type"] == "assistant":
            last_sr = _stop_reason(a)
    turn["ended"] = last_sr in END_STOPS
    # a slash-COMMAND turn with no reply/output atom is SELF-CONTAINED → ended (the user 2026-06-29). Without
    # this, a command that produced no output (a hung /usage, a control command) leaves the turn open forever,
    # so the session reads as "working" indefinitely and a stuck provisional card never resolves (the JLD case).
    # A command WITH output / model work ends naturally on that assistant atom's stop_reason above; this only
    # catches the bare-invocation case. (Working-during-execution is the live backend state's job, not this.)
    if not turn["ended"] and atoms[0].get("command") and not any(a["type"] == "assistant" for a in atoms):
        turn["ended"] = True
    # a compaction turn with no assistant work yet is likewise SELF-CONTAINED (the user 2026-07-13): the
    # boundary is a completed event, not in-flight work — left open it reads as a phantom open bar/WORKING
    # until the CLI's continuation lands (whose stop_reason then owns `ended` via the rule above).
    if (not turn["ended"] and atoms[0].get("type") == "system" and atoms[0].get("subtype") == "compact_boundary"
            and not any(a["type"] == "assistant" for a in atoms)):
        turn["ended"] = True
    # an INTERRUPT record at the turn's tail ends it (the user 2026-07-05): the CLI's stop record is the
    # interrupt event — the aborted assistant work before it never wrote an end_turn, so without this the
    # turn read open forever (stuck 'Interrupting…' chip, /model picks parked against an idle session).
    # Tail = last atom ignoring idle spans (a states overlay lands one after the record) and command
    # confirmations (a completed exchange, same skip _session_working does). An interrupt record MID-turn
    # (later work follows) means the turn resumed — that later work decides `ended`, so only the tail counts.
    if not turn["ended"]:
        i = len(atoms) - 1
        while i >= 0 and (atoms[i].get("command") or atoms[i]["type"] == "idle"):
            i -= 1
        if i >= 0 and is_interrupt_record(atoms[i]):
            turn["ended"] = True
    turn["id"] = _turn_id(rompuuid, turn)


# ── segment derivation: a turn split at its input atoms (timeline grain). DERIVED, not stored.
def _is_segment_input(atom):
    """A segment boundary is a genuine new input (opener or absorbed human/peer prompt).
    tool_result and `system` (task-notification) atoms do not start a segment; a
    higher layer MAY additionally split at a decision atom — the bottom layer does not."""
    return _is_opener(atom)


def _segment_id(rompuuid, seg_t, atoms, trigger_uuid):
    """`${rompUuid}:${seg.t}:${hash}` — parallel to the turn id; the summarizer layer's
    dedup key for a segment. Hash of the trigger atom's text (or the first atom's text
    for a triggerless/autonomous segment).

    A TEXT-LESS segment (a settle-seam tail, a tool-only continuation) has no content to hash —
    sha1("") is the SAME for every one, so a content key would alias them ALL under the
    timestamp-invariant _seg_key: a fresh working seam inherited a long-done seam's placement, and a
    session working past a completed goal showed a blank board (the user 2026-07-22). Its identity is
    instead its ANCHOR ATOM's uuid — unique per atom, present in the transcript, and STABLE across the
    judge parse (which carries the states/idle overlay) and the kernel render parse (which omits it),
    since the anchor is the segment's opener, a real atom the overlay never displaces (verified).
    Text-BEARING segments keep the content hash: it is drift-invariant across the SDK optimistic echo
    (send time) and the real transcript atom (process time), which share text but NOT uuid — so an
    atom-uuid key there would MISS its own echo. Hash the content, or — only when there is none — the
    anchor atom's identity.

    A segment opened by a MACHINE-WRITTEN trigger is keyed by its anchor atom's uuid too (T318, 2026-09-10):
    anything romp injected itself (the romp-injected marker: a kernel restart or crash notice, an auto-nudge,
    the retry message, the compaction suggestion, a Nudge-button follow-up), the CLI's own stop record
    ('[Request interrupted by user…]', is_interrupt_record), and a SCHEDULED task's fired prompt (origin
    subkind scheduled-trigger, or its preamble on an unstamped record: the CLI fires the stored prompt
    verbatim every interval, with no time or task id interpolated). Most of these are worded identically every time,
    so a content hash gave every such segment in a session the same hash and the timestamp-invariant _seg_key
    aliased them all (one session held 19 restart-notice segments and 25 stop records under three keys): a
    card whose recorded segments held one such segment resolved to whichever the parse saw last, and its
    summary click landed hours away from the work it described. Keying them by uuid is safe on the echo
    axis for a different reason than for typed prompts: every recorded key (a placement, a trail, a seam, a
    caption) is written by the judge from the TRANSCRIPT parse and the kernel only looks up, and a romp
    send's optimistic echo is hidden the moment its record lands, so the echo-time id and the landed id
    never coexist in anything recorded. A follow-up the USER typed into a card carries no romp-injected
    marker (only the Nudge button's does) and keeps its content hash, since its echo and its record must
    share a key while both are on screen."""
    anchor, texted = None, None           # texted: the atom whose text is the basis (the anchor with text)
    if trigger_uuid:
        a = next((x for x in atoms if x.get("uuid") == trigger_uuid), None)
        if a:
            anchor = a
            if _has_text(a):
                texted = a
    if texted is None and atoms:
        anchor = anchor or atoms[0]
        if _has_text(atoms[0]):
            texted = atoms[0]
    machine_written = texted is not None and (_machine_written(texted)
                                              or (anchor is not None and is_interrupt_record(anchor)))
    if texted is not None and not machine_written:
        h = _text_hash8(texted)               # the content hash: sha1(text)[:8], the same on a lazy atom
    else:
        basis = (anchor or {}).get("uuid") or next((a.get("uuid") for a in atoms if a.get("uuid")), "")
        h = hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:8]
    return "%s:%d:%s" % (rompuuid, seg_t, h)


def _has_text(atom):
    lz = atom.get("lazy")
    if lz is not None:
        return bool(lz.get("nt"))
    return bool(_text_of(_content(atom.get("message"))))


def _machine_written(atom):
    """Whether the atom's text is romp's own or a scheduled task's fired prompt (the id keys on the anchor uuid then;
    see _segment_id). Read from the lazy scalars when the atom has no body."""
    lz = atom.get("lazy")
    if lz is not None:
        return bool(lz.get("mw"))
    text = _text_of(_content(atom.get("message")))
    origin = atom.get("origin")
    return bool(text) and bool(
        ROMP_INJECT_RE.search(text) or SCHEDULED_PREAMBLE_RE.match(text)
        or (isinstance(origin, dict) and origin.get("kind") == "task-notification" and origin.get("subkind") == "scheduled-trigger"))


def segments(turn):
    """The per-input spans of a turn (what the timeline draws as bars). A segment runs
    from one input to the next (or to the turn end). Each carries a stable `id` for the
    summarizer layer. Pure function over a turn."""
    atoms = turn["atoms"]
    if turn.get("pre") and turn.get("segs") is not None:   # a restored pre-cut turn (T323 stage 4c): the spans as written; the
        return [dict({"id": s[0], "trigger": s[1], "t": s[2], "end": s[3], "atoms": atoms[s[4]:s[4] + s[5]]},   # atoms a lazy view
                     **({"w": bool(s[6]), "mids": list(s[7]), "hp": bool(s[8])} if len(s) >= 9 else {}))   # (T358: the stored verdicts)
                for s in turn["segs"]]
    rompuuid = atoms[0]["session_id"] if atoms else ""
    starts = [i for i, a in enumerate(atoms) if _is_segment_input(a)]
    if not starts:
        segs = [{"t": turn["t"], "end": turn["end"],
                 "trigger": turn["trigger"]["uuid"] if turn["trigger"] else None,
                 "atoms": list(atoms)}]
    else:
        bounds = starts + [len(atoms)]
        segs = []
        for k, i0 in enumerate(starts):
            i1 = bounds[k + 1]
            segs.append({"t": atoms[i0]["t"],
                         "end": atoms[i1]["t"] if i1 < len(atoms) else turn["end"],
                         "trigger": atoms[i0].get("uuid"),
                         "atoms": atoms[i0:i1]})
        if starts[0] > 0:   # leading atoms before the first input attach to the first segment
            lead = atoms[:starts[0]]
            segs[0]["atoms"] = lead + segs[0]["atoms"]
            segs[0]["t"] = turn["t"]
    for seg in segs:        # id last: after the leading-attach may have moved seg[0]'s t/atoms
        seg["id"] = _segment_id(rompuuid, seg["t"], seg["atoms"], seg["trigger"])
    return segs


# ── settle-time SEAM split (plans/segment-regrowth.md): when a goal settles while its segment is
# still growing, the post-settle tail becomes its OWN segment so the planner can see it. The split
# primitive lives here (pure over a segment); WHICH segments split — ownership via the goal store's
# placements — is the judge's call (jd.apply_seams), keeping this layer store-free.
SEAM_PROSE_FLOOR = 80                     # tail "real work" = a tool_use atom or assistant prose past this


def _seam_real_work(atoms):
    """True if `atoms` hold REAL work — any assistant tool_use, or assistant prose ≥ SEAM_PROSE_FLOOR
    chars (above connective stubs). The event condition that gates a seam split: post-settle wrap-up
    chatter never mints a noise segment."""
    hydrate(atoms)                                            # bodies before the assembly cut: read on demand (T323 stage 4a)
    for a in atoms:
        if a.get("type") != "assistant":
            continue
        blocks = _content(a.get("message"))
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                return True
        if len(_text_of(blocks)) >= SEAM_PROSE_FLOOR:
            return True
    return False


def split_segment(seg, t):
    """(head, tail) or None — split `seg` after the last atom at/before wall-clock `t` (a goal's settle
    moment, plans/segment-regrowth.md). None unless BOTH sides are non-empty and the tail holds real
    work (_seam_real_work). The head keeps the original id (its t + trigger text are unchanged, so an
    existing placement still matches); the tail is trigger-less, `seam`-flagged, with a STABLE id from
    its own first atom — every pass re-derives the same split, so placement idempotency holds."""
    atoms = seg.get("atoms") or []
    head_a = [a for a in atoms if a.get("t", 0) <= t]
    tail_a = [a for a in atoms if a.get("t", 0) > t]
    if not head_a or not tail_a or not _seam_real_work(tail_a):
        return None
    rompuuid = atoms[0].get("session_id", "")
    head = dict(seg, atoms=head_a, end=tail_a[0]["t"])
    for k_ in ("w", "mids", "hp"):
        head.pop(k_, None)                            # the whole segment's stored verdicts (T358) do not describe a part
    tail = {"t": tail_a[0]["t"], "end": seg["end"], "trigger": None, "atoms": tail_a, "seam": True,
            "id": _segment_id(rompuuid, tail_a[0]["t"], tail_a, None)}
    return head, tail


# ═════════════════════════ assembly ═════════════════════════
def _load_postal_index(postal_log):
    """{msg-id -> sender rompUuid} from timeline/messages.jsonl (`from_id` is the sender's
    anchor sid). Accepts a path or an in-memory list of rows (tests)."""
    idx = {}
    # append-incremental like the transcripts (the log is append-only; a rewrite fails the reader's
    # tail guard and degrades to a full re-read) — the full _read_jsonl here was a measured 22ms on
    # EVERY parse_session call against a 4MB live log
    rows = postal_log if isinstance(postal_log, list) else _read_jsonl_incremental(postal_log or MESSAGES_LOG)
    for o in rows:
        if not isinstance(o, dict):
            continue
        mid, frm = o.get("id"), o.get("from_id")
        if mid and frm and mid not in idx:
            idx[mid] = frm
    return idx


def _load_states(states):
    if states is None:
        return []
    # incremental for the same reason as the postal index above: states/<sid>.jsonl is append-only
    return list(states) if isinstance(states, list) else list(_read_jsonl_incremental(states))


def resume_fork_links(srows):
    """{to_fsid: from_fsid} from states/ resumeFork rows — the kernel's exact record that a resume of
    a machine-cut turn FORKED the transcript (fresh head) instead of continuing the chain. See
    FileAdapter._stitch_resume_forks for why the parse needs it; the kernel's episode-boundary check
    reads the same rows (jd.resume_lineage) to keep the fork from being processed as a /clear. Last
    row wins per fork head; malformed rows are skipped (a missing lineage simply keeps the old drop)."""
    links = {}
    for r in srows or []:
        rf = r.get("resumeFork")
        if isinstance(rf, dict) and rf.get("from") and rf.get("to"):
            links[str(rf["to"])] = str(rf["from"])
    return links


def _lineage_closure(leaf_path, candidate_files, links):
    """The candidate-file set CLOSED over the recorded resume lineage: a fork chain across several
    restarts needs every resumed-from file present for the stitched walk to cross (a caller's anchor
    covers only one hop). Shared by parse_session AND chain_membership so the exported membership
    predicate reads the exact same file set the display parse does. From-files are frozen after
    their fork (the CLI writes only the new file), so callers' cache keys stay honest."""
    if not links:
        return list(candidate_files)
    have = {Path(f).stem for f in candidate_files}
    stem, hops = Path(leaf_path).stem, 0
    candidate_files = list(candidate_files)
    while stem in links and hops < 16:
        stem = links[stem]
        hops += 1
        fp = Path(leaf_path).with_name(stem + ".jsonl")
        if stem not in have and fp.exists():
            candidate_files.append(str(fp))
            have.add(stem)
    return candidate_files


def chain_membership(leaf_path, candidate_files=None, states=None, leaf_override=None, rompuuid=None, sdk_human=None):
    """THE exported chain-membership fact — {"kept", "rewind", "clear", "broken", "eclipsed"}
    uuid sets, built from the DISPLAY parse's exact inputs (resume links + lineage closure +
    leaf_override = the kernel's pending bare-rollback cut) so it can never disagree with what the
    user sees. This is the one predicate every rewind-cleanup consumer must use (goal sweeps,
    mint-time stand-downs, the dead-branch reconciliation): before it was exported, four partial
    hand-rolled twins of this walk disagreed on resume forks, pending cuts and broken chains
    (2026-08-17).

    "rewind" is the ONLY set that ever justifies sweeping a goal: "clear" branches are /clear
    jurisdiction (the episode machinery settles those cards), "broken" chains are kept by design,
    "eclipsed" chains are KEPT content a machine spur abandoned (never a user gesture — T209;
    narrowed by _select_eclipsed_chains to the fork's one machine-orphaned reply chain when one
    qualifies, else per its terminal rules, so nothing sweeps a reply nobody abandoned and
    nothing keeps a sibling the walk drops on-spine),
    the rest of a parallel tool batch beside the spine is in "kept" and in no other set (its verdict
    is "active": FileAdapter._batch_head), and a uuid in NO set is unprovable (a synthetic
    orphan:<t> salvage id, a cross-file uuid whose file is outside the lineage, a legacy None) —
    callers must treat unknown as NOT abandoned.
    Caveat (resume-fork stitch shape): a recorded fork's fresh head is re-pointed at the from-file's
    LAST record — if that tip was itself an abandoned tail, the stitch makes it active again; this
    predicate follows the stitch exactly as the display parse does (kept semantics, by design)."""
    leaf_path = Path(leaf_path)
    if candidate_files is None:
        candidate_files = [str(leaf_path)]
    links = resume_fork_links(_load_states(states))
    candidate_files = _lineage_closure(leaf_path, candidate_files, links)
    # The display parse's own assembly entry answers when it stands for these inputs (T323 stage 4a): a fresh
    # adapter here read the whole leaf and held its graph a second time, which on a session restored from its
    # assembly document was the one whole read left at a boot. Read under the entry's key lock (folds mutate the
    # adapter's links transiently); an entry restored from a document adds the pre-cut verdicts its seed carries.
    adapter, how = None, "whole"
    if not leaf_override and rompuuid is not None:
        key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
        with _asm_key_lock(key):
            with _ASM_LOCK:
                entry = _ASM_CACHE.get(key)
            if entry is not None and entry["cands"] == tuple(str(f) for f in candidate_files) \
                    and entry["links"] == dict(links or {}) and _entry_current(entry, candidate_files):
                if _READER_TRACE:
                    sys.stderr.write("chain: entry %s\n" % leaf_path)
                return _membership_of(entry["ad"])        # the display's own current graph, under its lock
        if _CKPT_DIR_FN is not None:
            _standing = _asm_refusal_stands(leaf_path)   # one sidecar read per call (low 4)
            doc = None if _standing else _asm_ckpt_load(leaf_path, rompuuid, sdk_human, candidate_files, links)
            if _standing:
                _asm_stat("seeded:refusedStanding")       # the cold walk, no proof, while the mark stands (round two)
            if doc is not None and not _tail_chains_onto_the_document(leaf_path, doc):
                _asm_stat("seeded:chainRefused"); doc = None   # the tail re-parents into the pre-cut part: the cold walk, as
            if doc is not None:                              #  before T391 (T402 round four)
                try:                                         # no current entry: the document's pre-cut facts plus the tail
                    seed, _landed = _seed_from_doc(doc)      #  read now, the whole graph's verdicts without the whole read
                    adapter = FileAdapter(candidate_files, leaf_path, resume_links=links, seed=seed)
                    how = "seeded"
                except Exception as e:                       # noqa: BLE001
                    _asm_ckpt_note(leaf_path, "restore", repr(e)[:120]); adapter = None
            elif _READER_TRACE:
                sys.stderr.write("chain: no document for %s (rompuuid %s sdk_human %r cands %r)\n" % (leaf_path, rompuuid, sdk_human, [str(f) for f in candidate_files]))
    if adapter is None:
        adapter = FileAdapter(candidate_files, leaf_path, leaf_override=leaf_override, resume_links=links)
    if _READER_TRACE:
        sys.stderr.write("chain: %s %s\n" % (how, leaf_path))
    return _membership_of(adapter)


def file_rewound(path, rompuuid=None, sdk_human=None, own=True):
    """The uuids a ONE-FILE walk of `path` classifies "rewind" (the judges' per-file discriminator). When the file is a
    leaf whose assembly document stands for a single-file lineage, the walk runs over a seeded adapter (the pre-cut
    verdicts from the document, the tail read now) instead of reading the file whole; a multi-file lineage's document
    records whole-graph verdicts, which are not this walk's, so that file reads whole as before. Raises OSError when a
    non-empty file yields no records (a failed read, not an empty file). `own` False is the judges' walk over ANOTHER
    session's leaf (2026-09-15): its document is loaded without the note, so a document that does not verify for this
    walk (written under the other owner bit, moved, rewritten) is refused quietly and stands for its owner, and the walk
    reads the file whole here as before the seeded road."""
    path = Path(path)
    ad = None
    if rompuuid is not None and _CKPT_DIR_FN is not None:
        _standing = _asm_refusal_stands(path)              # one sidecar read per call (low 4)
        doc = None if _standing else _asm_ckpt_load(path, rompuuid, sdk_human, [str(path)], {}, quiet_inputs=True, own=own,
                                                    memo="seeded")   # the decode once per process per document (2026-09-15)
        if _standing:                                     # the cold walk, no proof, while the mark stands (round two); a reader
            _asm_stat("seeded:refusedStanding" if own else "foreign:refusedStanding")   # that does not own the leaf counts its own
        if doc is not None and not _tail_chains_onto_the_document(path, doc):
            _asm_stat("seeded:chainRefused"); doc = None      # the cold walk over a tail that re-parents into the pre-cut
        if doc is not None:                                   #  part (T402 round four)
            try:
                seed, _landed = _seed_from_doc(doc)
                ad = FileAdapter([str(path)], str(path), seed=seed)
            except Exception as e:                            # noqa: BLE001
                if own:
                    _asm_ckpt_note(path, "restore", repr(e)[:120])
                else:
                    _asm_stat("foreign:restore")              # another session's document: never unlinked from here
                ad = None
    if ad is None:
        ad = FileAdapter([str(path)], str(path))
    if not ad.by_uuid and (ad.seed is None or not ad.seed["verdicts"]) and path.stat().st_size > 0:
        raise OSError("transcript read yielded no records")
    verdicts = dict(ad.chain_verdicts())
    if ad.seed is not None:
        for u, v in ad.seed["verdicts"].items():
            verdicts.setdefault(u, v)
    return {u for u, v in verdicts.items() if v == "rewind"}


_REWOUND_CACHE = {}               # path -> (count, gen, {"uuids": [...]}): file_rewound's verdict set over a FROZEN file, a registered
#                                   fold (T391) the fold document carries and restores, so a dead episode file is read whole once
_REWOUND_STATS = {"served": 0, "walked": 0, "stale": 0, "fallback": 0}   # the memo's answers, the walks it took, the memos an
#                                   append or rewrite retired, and the walks whose memo could not be read or stored


def _rewound_walk(path):
    """The plain one-file walk `rewound_uuids` memoizes: file_rewound's road without a rompuuid, returning the verdict set and
    the reader's (gen, base, count) the adapter's records came from (FileAdapter._src_keys), the witness the memo is stored at.
    Its own, never re-fetched from the cache after the walk: an append and a refresh between the two would memoize pre-append
    verdicts at the post-append witness (T391 round one, low 1)."""
    key = str(path)
    ad = FileAdapter([key], key)
    if not ad.by_uuid and Path(path).stat().st_size > 0:
        raise OSError("transcript read yielded no records")
    verdicts = dict(ad.chain_verdicts())
    return {u for u, v in verdicts.items() if v == "rewind"}, ad._src_keys.get(key, (None, 0, 0))


def rewound_uuids(path, drop=True):
    """`file_rewound(path)` for a file with no rompuuid road (a dead episode's transcript in a lineage, walked by the judges'
    incident scan), memoized per FROZEN file as the fold `rewoundUuids` of its fold document (T391): the memo rides the
    existing document, its witness (size, mtime, the cut's guard), its restore, its fold state cap and its counted fallbacks,
    and the quiescence drop writes it from the walk's own read, so the file is read whole once and not at the next process.
    fold_records consults it: a hit or a restore at the witness answers with no read; a file that grew or was rewritten steps
    a `step` that retires the state (None), so the walk runs again and the memo is rewritten; an over-cap set is recorded as
    such and walked again next time. The leaf road with a rompuuid never comes here.

    `drop`: whether the walk's entry leaves the reader's cache after the memo is stored (the quiescence drop, when the file
    has been idle past its window). True for a dead episode's file, which nothing else reads, and for a /clear's anchor, which
    no parse reads since 2026-10-03; False for a file of a LIVE session's lineage (an anchor its parse reaches, T391 round
    one, medium): the chain walk reads that file whole first at every pass,
    and a memo that popped the entry made the next pass's chain walk read it whole again, every pass, while the memo itself
    found its cursor at a moved generation and walked. Resident, the anchor is read once per process and the memo's cursor
    stays at the generation the chain walk's entry holds, so the scan reads nothing at all.

    The counters (checkpoints.rewoundMemo): `served`, a memo answered in memory or from the document at the witness; `walked`,
    every walk; `stale`, the walks over a memo the file's growth or rewrite retired (an in-process cursor stepped past by an
    append, a document cursor restored and stepped past, a refold whose count moved); a walk over an unchanged file whose entry
    left memory and came back under a fresh generation is walked, not stale (round one, low 2); `fallback`, a document state of
    the wrong shape (walked, never trusted) or a walk whose reader entry was gone before the memo could be stored (round one,
    low 3), both counted so a memo that never takes is visible on /perf."""
    key = str(path)
    had = _REWOUND_CACHE.get(key)
    kinds = []
    state = fold_records(_REWOUND_CACHE, key, lambda: None, lambda st, o: None, on=kinds.append, ckpt="rewoundUuids")
    if isinstance(state, dict) and isinstance(state.get("uuids"), list):
        with _CKPT_LOCK:
            _REWOUND_STATS["served"] += 1
        return set(state["uuids"])
    if state is not None:                                 # a state that is not the memo's shape: never trusted, counted, walked
        with _CKPT_LOCK:
            _REWOUND_STATS["fallback"] += 1
        _REWOUND_CACHE.pop(key, None)
    out, (gen, base, count) = _rewound_walk(path)         # the walk (a whole read of a frozen file: the record cache holds it)
    kind = kinds[0] if kinds else None
    with _CKPT_LOCK:
        _REWOUND_STATS["walked"] += 1
        if kind in ("append", "restore") or (kind == "refold" and had is not None and had[0] != count):
            _REWOUND_STATS["stale"] += 1                  # a memo stood and the file moved under it
    if gen is None:                                       # no reader entry for the walk's records: nothing to store the memo at
        with _CKPT_LOCK:
            _REWOUND_STATS["fallback"] += 1
        return out
    _REWOUND_CACHE[key] = (count, gen, {"uuids": sorted(out)})   # the memo at the walk's own witness, dirty
    with _CKPT_LOCK:
        r = _RETIRED_FOLDS.get(key)                        # a retirement still pending from a flip before this store is stale:
        if r is not None:                                  #  the store is the newer event (T391 follow-up, low 7)
            r.discard("rewoundUuids")
            if not r:
                _RETIRED_FOLDS.pop(key, None)
    with _CKPT_LOCK:
        _FOLD_DIRTY.add(key)
    if drop and checkpoint_drop_writes_on():              # the quiescence drop over this frozen file writes the document from the
        with _JSONL_CACHE_LOCK:                           #  walk's own read and lets the records go (T362's drop, its budget and its
            ent = _JSONL_CACHE.get(key)                   #  deferral); a file still changing keeps its entry and is written at its
        if ent is not None and ent[6] == gen:             #  settle; only the very entry the walk read is dropped. With the drop's
            _drop_quiescent_entry(key, ent, pop=True)     #  document write OFF (a cycle cap of 0) the entry stays resident: the memo
    #                                                        could not reach the disk, and a drop then made the file a whole read at
    #                                                        every pass where the old road read it once per process (round two, low 1)
    return out


def rewound_memo_stats():
    with _CKPT_LOCK:
        return dict(_REWOUND_STATS)


def _membership_of(adapter):
    """The five-way membership dict from an adapter's walk; a seeded adapter's pre-cut verdicts join its own."""
    active = adapter.active_path()
    verdicts = dict(adapter.chain_verdicts(active))
    if adapter.seed is not None:
        for u, v in adapter.seed["verdicts"].items():
            verdicts.setdefault(u, v)
        active = set(active) | {u for u, v in adapter.seed["verdicts"].items() if v == "active"}
    # kept, derived from the verdicts already in hand — BY DEFINITION the same set kept_uuids
    # computes (active ∪ broken ∪ eclipsed, the "active" verdict included for a parallel tool
    # batch's branch beside the spine; see its docstring: "derived from chain_verdicts — one
    # implementation"), without paying the graph walk a second time inside it. The hold view
    # re-asks this on every build of a held session, so the walk count matters there.
    out = {"kept": set(active) | {u for u, v in verdicts.items() if v in ("active", "broken", "eclipsed")},
           "rewind": set(), "clear": set(), "broken": set(), "eclipsed": set()}
    for u, v in verdicts.items():
        if v != "active":
            out[v].add(u)
    return out


# ═════════════════════ the display parse's graph, read in place (the comment cut) ═════════════════════
# (2026-10-01.) A comment thread's cut is found on the parent's transcript graph: the anchor must exist and sit on the
# active chain, a compaction between it and the leaf makes the thread a tip fork, and the cut is the nearest prose
# record at or above it. That walk re-read the parent's whole stitched transcript at every create (1 to 3.6 s for a
# 110 MB parent, on the page's own socket thread), while the chat's own parse already held the graph in its assembly
# entry. with_display_graph hands the walk that entry, under its key lock, when it stands for the walk's inputs and has
# folded everything its files hold now. A whole entry's maps are the graph the read would build; a RESTORED entry holds
# the tail past its document's cut in full and the pre-cut part as the document's facts (each record's type, the active
# spine root to cut) plus the atom rows' byte locations, so a pre-cut record is read back alone, by offset, only when
# the walk needs its body. A fact the entry cannot give (a pre-cut record with no row to read it by, a walk leaving the
# spine) raises GraphMiss, and the caller reads the transcript as before: never an answer from a guess.
# A message the CLI splices into a running turn is no user record: the CLI writes it as a queued_command attachment
# (FileAdapter._absorbed), and writes it again when a compaction or a resume replays it. Whether an attachment is such a
# message, and the original rather than a copy, is the display parse's own answer, the atom _absorbed emitted for the
# record: read from the entry's atoms, or on a restored entry's pre-cut part from the atom's row.


class GraphMiss(LookupError):
    """A restored entry's pre-cut part cannot answer what the walk asked: read the transcript instead."""


def _splice_prompt(rec):
    """The words of queued_command attachment record `rec` as its atom carries them: the prompt as is when it is a string,
    else the joined text of its content blocks (FileAdapter._ingest's witness, _hydrate_one's kind "b")."""
    p = (rec.get("attachment") or {}).get("prompt")
    return p if isinstance(p, str) else _text_of(p or [])


class TranscriptGraph:
    """The read surface of a walk over one session's stitched transcript graph (the comment cut, kernel
    _comment_cut_walk), over a whole FileAdapter's maps: has, rec, kind (type, subtype), parent, fsid, the leaf, and
    splice_author / splice_text for a message spliced into a running turn. `atoms`: the atoms the display parse emitted
    from those maps (its assembly entry's); None over a fresh read, where _absorbed is run on the adapter's own splices."""

    def __init__(self, ad, atoms=None):
        self._ad = ad
        self.leaf = ad.leaf_uuid
        self._atoms = atoms
        self._splices = None

    def has(self, u):
        return u in self._ad.by_uuid

    def rec(self, u):
        return self._ad.by_uuid.get(u)

    def kind(self, u):
        r = self._ad.by_uuid.get(u)
        return (r.get("type"), r.get("subtype")) if r is not None else (None, None)

    def parent(self, u):
        return self._ad.parent_of.get(u)

    def fsid(self, u):
        return self._ad.fsid_of.get(u)

    def dangling(self):
        """The parent targets records of the graph name that are no record of it, as a frozenset: the adapter's dangling set,
        collected at ingest over every record, kept or not (a restored entry's seed carries its pre-cut part's), less any the
        graph holds (a tail record's parent before a restored entry's cut). A link from the active chain is among them even
        where a restored entry's spine stops at it (review round two, 2026-10-03: after a /clear the comment cut builds the
        graph over both files only when one of these is a record of the session's first file)."""
        return frozenset(u for u in self._ad.dangling if not self.has(u))

    def root_may_link(self, u):
        """Whether `u`, a record this graph gives no parent, names one all the same that the graph lacks (root_parent).
        Never over a whole adapter, whose parent map is its records' own links; a restored entry's spine root may
        (_SeededGraph, review round five, 2026-10-03, narrowed to a recorded parent in round six, 2026-10-04)."""
        return False

    def root_parent(self, u):
        """The parent `u`'s record names where this graph gives `u` none: None over a whole adapter, whose parent map holds
        every link its records name; for a restored entry's spine root, the one its document recorded (_SeededGraph)."""
        return None

    def _splice_atom(self, u):
        """The absorbed atom the parse emitted for attachment `u`, else None. Over a fresh read the atoms are _absorbed's over
        the adapter's splices and its kept chain, from an empty dedup memory as a whole parse starts, with no postal index
        and no count in the parse stats: this read is no parse. The map is built once per graph, so a walk across a long
        tool turn's attachments costs one pass over the atoms."""
        if self._splices is None:
            atoms = self._atoms
            if atoms is None:
                ad = self._ad
                atoms = ad._absorbed(ad.qatts, ad.kept_uuids(ad.active_path()), _emit_state(), None, {}, stats=False)
            self._splices = {a["uuid"]: a for a in atoms if a.get("absorbed") and a.get("uuid")}
        return self._splices.get(u)

    def splice_author(self, u):
        """The author (author_of's) of the atom the parse emitted for queued_command attachment `u`, a message spliced into
        a running turn (_splice_atom: the display parse's, or over a fresh read the same emit run on it); None when it
        emitted none, for example for a splice off the kept chain."""
        a = self._splice_atom(u)
        return a.get("author") if a is not None else None

    def splice_text(self, u):
        """The prompt of attachment `u`'s record (_splice_prompt)."""
        return _splice_prompt(self._ad.by_uuid.get(u) or {})


def _read_record_at(path, at, uuid):
    """The raw transcript record at `at` = (byte offset, byte length) in `path`, verified to be `uuid`'s; hydrate's own read
    (one seek, one read, counted). Never the hydration memo: it keeps a record's body fields alone (2026-10-01), and the cut
    walk decides on the record's type, time and provenance. Nor the file's read stripe (2026-10-02): this read fills no
    cache, and under the stripe the comment cut waited, inside its entry's key lock, for a whole hydrate group of the file
    or of a file sharing its stripe, or for a record-cache read."""
    _tail_guard(path, "_read_record_at")
    off, ln = int(at[0]), int(at[1])
    try:
        with open(path, "rb") as fh:
            fh.seek(off)
            raw = fh.read(ln)
    except OSError as e:
        raise GraphMiss("record %s: %s" % (uuid, e))
    _count_read(path, len(raw))
    try:
        rec = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        rec = None
    if not isinstance(rec, dict) or rec.get("uuid") != uuid:
        raise GraphMiss("record %s: the line at its offset is another record" % uuid)
    return rec


def _precut_locations(entry, rompuuid):
    """{uuid: [(at, fsid, path, kind, lazy header), ...]} for a restored entry's pre-cut atom rows that carry a record
    location, built once per index (or per atoms-only prefix) and kept on it: what _SeededGraph reads a pre-cut record
    back by. A turns-section entry's rows stay bytes in the LazyIndex; only the rows the walk asks for are decoded."""
    index = entry.get("index")
    if index is not None:
        got = getattr(index, "_cut_rows", None)
        if got is None:
            got = {}
            for pt in entry.get("preTurns") or []:
                rows = getattr(pt.get("atoms"), "_rows", None) or []
                for u, k in zip(pt.get("uuids") or [], rows):
                    if u:
                        got.setdefault(u, []).append(k)
            index._cut_rows = got
        return ("index", index, got)
    prefix = entry.get("prefix") or []
    got = entry.get("_cutPrefix")
    if got is None:
        got = {}
        for a in prefix:
            if isinstance(a, dict) and a.get("uuid"):
                got.setdefault(a["uuid"], []).append(a)
        entry["_cutPrefix"] = got
    return ("prefix", rompuuid, got)


class _SeededGraph(TranscriptGraph):
    """TranscriptGraph over a RESTORED entry (its adapter seeded from the assembly document): the tail past the cut from the
    adapter, the pre-cut part from the seed's facts (types, the active spine root to cut) and the atom rows' locations. A
    pre-cut tool record (its row's lazy header lists tool calls or results) answers as a body-less record of its type, so a
    walk past a long tool turn reads nothing; any other pre-cut message is read back alone by offset. A splice's atom is the
    entry's (its tail's atoms) past the cut, its atom row before it."""

    def __init__(self, entry, rompuuid):
        super().__init__(entry["ad"], entry["atoms"])
        seed = self._ad.seed
        self._types = seed["types"]
        sp = seed.get("_spineParent")
        if sp is None:
            spine = seed["spine"]
            sp = seed["_spineParent"] = dict(zip(spine, [None] + list(spine[:-1])))
        self._spine_parent = sp
        self._root_parent = seed.get("spine_root_parent")    # the spine root's raw parentUuid (_seed_from_doc)
        self._locs = _precut_locations(entry, rompuuid)
        self._recs = {}

    def has(self, u):
        return u in self._ad.by_uuid or u in self._types

    def kind(self, u):
        r = self._ad.by_uuid.get(u)
        if r is not None:
            return r.get("type"), r.get("subtype")
        return self._types.get(u, (None, None))

    def parent(self, u):
        if u in self._ad.by_uuid:
            return self._ad.parent_of.get(u)
        if u in self._spine_parent:
            return self._spine_parent[u]
        if u in self._types:
            raise GraphMiss("record %s is before the document's cut and off its active spine" % u)
        return None

    def root_may_link(self, u):
        """True for the root of the pre-cut spine when its record names a parent, not itself, that this graph lacks: the
        spine, root to cut, gives that root no parent, so it reads as parentless here even where its record links into a
        file the parse left out, a session's first transcript after a /clear among them (the comment cut, review round
        five, 2026-10-03). A root whose record names no parent answers False (review round six, 2026-10-04)."""
        p = self.root_parent(u)
        return p is not None and p != u and not self.has(p)

    def root_parent(self, u):
        """The raw parentUuid the document recorded for the pre-cut spine's root (the seed's "spine_root_parent", review
        round six), for that root alone; None for any other record."""
        if u in self._ad.by_uuid or u not in self._spine_parent or self._spine_parent[u] is not None:
            return None
        return self._root_parent

    def _loc(self, u):
        form, src, got = self._locs
        for c in got.get(u) or ():
            if form == "index":
                try:
                    row = src._row(c)
                except LazyIndexError as e:
                    raise GraphMiss(str(e))
                if row.get("syn") or not row.get("at"):
                    continue
                sc, ri = row.get("s") or {}, row.get("r")
                fsid = sc.get("fsid")
                if fsid is None and ri is not None and 0 <= ri < len(src.records):
                    k = src.records[ri][6]
                    fsid = src.fsids[k] if 0 <= k < len(src.fsids) else None
                path = (src.source_files or {}).get(fsid)
                lz = row.get("lz") or {}
            else:
                lz = c.get("lazy") or {}
                if not lz.get("at"):
                    continue
                fsid = c.get("fsid")
                path = getattr(c.get("message"), "source_path", None)
                if path is None or path is _UNBOUND_LAZY_SOURCE:
                    path = (_LAZY_FILES.get(str(src)) or {}).get(fsid)
            if path:
                return {"at": tuple(lz.get("at") if form == "prefix" else row["at"]), "fsid": fsid, "path": path,
                        "kind": lz.get("k"), "lz": lz}
        return None

    def fsid(self, u):
        if u in self._ad.by_uuid:
            return self._ad.fsid_of.get(u)
        loc = self._loc(u)
        if loc is None:
            raise GraphMiss("record %s: no row names its file" % u)
        return loc["fsid"]

    def rec(self, u):
        r = self._ad.by_uuid.get(u)
        if r is not None or u not in self._types:
            return r
        if u in self._recs:
            return self._recs[u]
        t, sub = self._types[u]
        if t not in ("user", "assistant"):
            out = {"type": t, "subtype": sub, "uuid": u}        # a system or attachment record: its type is the walk's whole read
        else:
            loc = self._loc(u)
            if loc is None:
                raise GraphMiss("record %s: no row carries its location" % u)
            if loc["kind"] in ("a", "u") and (loc["lz"].get("tu") or loc["lz"].get("tr")):
                out = {"type": t, "uuid": u}                   # tool traffic: never a prose record, nothing to read
            else:
                out = _read_record_at(loc["path"], loc["at"], u)
        self._recs[u] = out
        return out

    def splice_author(self, u):
        """Past the cut, the tail's atoms answer; before it, the scalars of the atom row the document holds for `u`: a row
        only for an attachment the parse made an atom of, so a hook's result or a token reminder costs no read."""
        if u in self._ad.by_uuid or u not in self._types:
            return super().splice_author(u)
        form, src, got = self._locs
        for c in got.get(u) or ():
            if form == "index":
                try:
                    sc = src._row(c).get("s") or {}
                except LazyIndexError as e:
                    raise GraphMiss(str(e))
            else:
                sc = c
            if sc.get("absorbed"):
                return sc.get("author")
        return None

    def splice_text(self, u):
        """Before the cut, the attachment record read back alone by the offset its atom row carries (an attachment's only
        atom is its splice's)."""
        if u in self._ad.by_uuid or u not in self._types:
            return super().splice_text(u)
        loc = self._loc(u)
        if loc is None:
            raise GraphMiss("record %s: no row carries its splice's location" % u)
        return _splice_prompt(_read_record_at(loc["path"], loc["at"], u))


def _files_witness(files):
    """{path: (size, mtime_ns, inode)} for every file in `files`, None when one cannot be stat'd: the witness _assemble stamps on
    the entry it leaves (entry["seen"], taken before its reads) and with_display_graph compares with the files now."""
    out = {}
    for f in files:
        try:
            st_ = os.stat(f)
        except OSError:
            return None
        out[str(f)] = (st_.st_size, st_.st_mtime_ns, st_.st_ino)
    return out


def with_display_graph(leaf_path, rompuuid, sdk_human, candidate_files, states, fn):
    """Run fn(graph) over the display parse's own assembly entry for this leaf, under the entry's key lock (folds mutate the
    adapter), when the entry stands for these inputs (the candidate set and resume links parse_session derives from them,
    chain_membership's test) and has folded everything its files hold now: their stat equals the witness _assemble stamped
    on the entry before it last read them (entry["seen"]). Stat alone, never the reader cache's generation: the reader drops
    an idle file's records to save memory (the quiescence drop) while the entry keeps its graph, and a currency test keyed on
    that generation read a parse that stood as stale, then read the file to find out (a 110 MB re-read per create in the lab,
    2026-10-01). Returns (how, fn's value): how "whole" or "seeded" (a restored entry, _SeededGraph); ("none", None) when no
    entry stands for the inputs, ("stale", None) when one stands but the files moved since it folded them (the caller
    parses, then asks again). GraphMiss from the walk propagates."""
    leaf_path = Path(leaf_path)
    links = resume_fork_links(_load_states(states))
    candidate_files = _lineage_closure(leaf_path, list(candidate_files), links)
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))

    def walk(entry):
        if entry is None or entry["cands"] != tuple(str(f) for f in candidate_files) or entry["links"] != dict(links or {}):
            return "none", None
        seen = entry.get("seen")
        if seen is None or seen != _files_witness(candidate_files):
            return "stale", None
        if entry["ad"].seed is None:
            return "whole", fn(TranscriptGraph(entry["ad"], entry["atoms"]))
        return "seeded", fn(_SeededGraph(entry, rompuuid))

    def cached():
        with _ASM_LOCK:
            return _ASM_CACHE.get(key)
    lock = _asm_key_lock(key)
    if not lock.acquire(blocking=False):
        # held: when by a document write over this very entry (_DocWriterScope's mark), which reads the graph and changes
        # none of it, walk beside the writer, and keep the answer only if its mark still stands after the walk (the writer
        # held the lock throughout, so nothing folded meanwhile); any other holder (a fold, a parse) is waited out
        entry = cached()
        tok = entry.get("docWriter") if entry is not None else None
        if tok is not None:
            got = walk(entry)
            if entry.get("docWriter") is tok:
                return got
        lock.acquire()
    try:
        return walk(cached())
    finally:
        lock.release()



# ═════════════════════ assembly cache: fold appends, don't re-emit the world ═════════════════════
# (2026-09-01.) _read_jsonl_incremental already amortizes bytes -> records; what still re-ran in full
# on every append was everything ABOVE the records. Measured on the live corpus: the emit layer is
# 0.2-0.5s per parse at 20-130MB transcripts, while the graph semantics (active_path, chain_verdicts
# incl. the eclipsed probe, kept_uuids) cost 2-6ms even at 28k records. So a fold RE-DERIVES all
# graph semantics exactly, every time, and folds ONLY the emit: appended records run through the
# same _prepass/_emit_fold/_absorbed code with the carried emit state. Gates demote anything the
# carry cannot provably absorb to a full parse — a fold never guesses; wrong means full, never
# divergent:
#   - candidates / resume links / sdk_human changed, or a pending cut is armed        -> full/bypass
#   - a non-leaf candidate changed at all, or the leaf did anything but grow          -> full
#   - the new leaf does not descend from the old leaf through the delta, unless the
#     delta is a parallel tool batch landing (FileAdapter._batch_delta)               -> full
#   - the delta contains: a compact boundary or summary; a uuid already in the graph
#     or among its recorded dangling parent targets; a promptId already seen, on a
#     command-wrapper record or wearing an adoptable compaction's summary pid
#     (_asm_pid_refusal); a Skill tool_use an old payload record already references; an unparseable or
#     watermark-regressing conversational timestamp                                   -> full
#   - a delta holding a record the narrowed promptid arm let through, refused by a compaction,
#     the descent or a batch's kept check: the whole parse, never the restore road    -> full
#     (a compaction summary does not mark its own delta; the re-seat steps aside for such a
#     delta that passed on a batch's link, so its kept check rules)
#   - after the fold's graph recompute, ANY old record's kept-membership changed      -> full
#     (a batch landing's delta: the restore road first, as its descent demotion took)
# Postal is the one input with no gate, on the log's own invariant: rows are append-only and
# resolution is first-wins per id, so an author can only go from marker-missed to resolved — and
# _asm_heal re-authors exactly those atoms each visit until they resolve.
# The entry is mutated only under its per-key lock, held across gates + fold + commit — a
# deliberate departure from _JSONL_CACHE_LOCK's cheap-ops-only discipline, because this cache's
# hits MUTATE the entry (two concurrent parses of one path must serialize or they double-fold the
# same delta). Served atom lists are copy-on-fold and served atoms are per-serve shallow copies,
# so callers pop _seq / rebuild turns as they always have without reaching into the cache. Any
# exception on the fold path logs ONCE and falls back to plain full parses — loud, never wrong.
_ASM_CACHE = {}          # key -> entry; dict order = LRU, hits reinsert. Evicts oldest-used one at
#                          a time, never clear-at-cap (the _JSONL_CACHE 2026-08-15 lesson: a
#                          wholesale clear nukes the hot entries and every push re-parses in full)
_ASM_CACHE_MAX = 256
_ASM_LOCK = threading.Lock()       # guards the cache dict + the per-key lock registry only
_ASM_KEYLOCKS = {}                 # key -> Lock; never pruned (a Lock is tiny, and swapping a
#                                    key's lock mid-flight would let two folds interleave)
_ASM_STATS = {"full": 0, "fold": 0, "serve": 0, "restore": 0, "bypass": 0, "fallback": 0}   # observability + tests (restore
#                                                                                             seeded: a row without it means zero)


def _asm_stat(key, n=1):
    """One increment of the parse's road counters under _ASM_CKPT_LOCK (the lock the perf copy takes): every write goes through
    here, so a judge parse and the pusher's parse never lose each other's increment (T398 follow-up, low 3)."""
    with _ASM_CKPT_LOCK:
        _ASM_STATS[key] = _ASM_STATS.get(key, 0) + n
_ASM_WARNED = [False]
_TS_REPAIR_NOTED = set()     # file stems already warned about a garbled stamp — once per file;
#                              the cap CLEARS and re-arms (an occasional repeat note beats silence)
_TS_REPAIRED_SEEN = set()    # record uuids already counted in ts-repair — distinct corruption,
#                              not parse volume; races only overcount by one, acceptable


_ASM_DEMOTE_TL = threading.local()   # the calling thread's last demotion reason: what _assemble reads to pick the road after it
_ASM_RESTORE_AFTER_DEMOTE = ("descent", "rewrite", "nonleaf", "reseat", "boundary", "summary", "late-parent")   # the demotions the document
#                                   still stands for (T402): the tail moved (a spur, a rewind, a fork), the leaf's record entry was
#                                   replaced, a lineage file moved, or a compaction landed in the tail (its boundary or its summary,
#                                   2026-09-24: the fold cannot carry one, but the restore parses the tail whole from the cut,
#                                   compaction included, as every boot over a document does; sent to the whole parse instead, each
#                                   compaction re-read the transcript, 0.6 to 1.7 s at 150 to 184 MB measured, the settle then
#                                   rewrote the document for 2.3 to 2.7 s more, and the next parse re-seated the new whole entry on
#                                   it). `reseat` (2026-09-24) is a
#                                   whole entry whose own document now stands (asm_checkpoint_write): re-seated on it, the folds after
#                                   walk the tail alone. `late-parent` (2026-11-12) is a record landing as the parent an earlier
#                                   record named: the restore reads the tail from the cut with the parent in hand, and its proof
#                                   refuses when the earlier record lies before the cut. The load's own checks and the chain proof
#                                   refuse a document that no longer fits (a boundary anchored before the cut, say). Every other reason (a prompt id, a skill link, a
#                                   stamp out of order, ...) keeps the whole parse, and the gates file a compaction's reason only
#                                   after every record in the delta has met them, so a compaction never carries such a record onto
#                                   the restore road. A compaction restores only while the tail past the standing cut is under the
#                                   churn bound's share (_asm_compaction_under_share); past it the compaction parses whole, so the
#                                   settle writes a later cut. Nor does a compaction restore a whole entry whose postal author still
#                                   waits on the log, or any entry whose document was written while one waited, whether it wrote
#                                   that document and has healed since or was restored from it (_assemble): the writer's own test for
#                                   its re-seat mark.
_ASM_COMPACTION_DEMOTES = ("boundary", "summary")   # the compaction's two reasons: the restore road takes them under the share only


def _asm_demote(reason):
    """Count WHY a fold demoted to a full parse (g:<reason> in _ASM_STATS) and return None —
    the hit-rate diagnosis this cache lives or dies by, in prod and in the corpus replay. The reason is
    left on the thread for _assemble, which tries the restore road for the reasons the document still stands for."""
    k = "g:" + reason
    _asm_stat(k)
    _ASM_DEMOTE_TL.reason = reason
    return None


def _asm_demote_passed():
    """The demotion of a delta the narrowed promptid arm let through (_asm_pid_refusal: every summary wearing the record's
    id hangs off an attached pair, or no /compact episode wears it) that then fails for a reason the restore road takes: a
    compaction in the delta, the leaf descent, or the kept check of a parallel tool batch the descent let by (review of
    2026-10-03, round one). It is filed as
    the promptid demotion it was before the arm narrowed, which the restore road does not take, so it parses whole as it did
    then, counted g:promptid:passedDemote beside the arms. The restore proves only that the tail chains onto the document, and
    a record wearing the summary's prompt id that rewinds onto the pre-compaction leaf hangs off the tip of a document cut at
    the compaction's turn: restored, it served a turn the cold parse does not build, and the settle, which writes nothing for
    a restored entry, left that document standing, and each boot served the same turn, until the tail reached the churn
    share. The descent cannot tell that rewind from an api_error retry, so a retry whose result wears the id parses whole here
    too, as it did before the narrowing. The re-seat (_assemble) steps aside for a passed delta that passed on a parallel
    batch's link, so its kept check rules and a rejection lands here (round two: re-seated, a result parented at a parallel
    call that an automatic compaction was anchored on was served as a turn of its own); a passed delta that extends the path
    keeps its re-seat. A compaction summary does not mark its own delta (_asm_gates), so a turn's later compaction alone in its
    append takes the restore road as any compaction does."""
    _asm_stat("g:promptid:passedDemote")
    return _asm_demote("promptid")


def _asm_on_path(ad, u):
    """Whether `u` sits on the adapter's active path as the last parse or fold left it: a walk back over ad.parent_of (the
    graph after the repair passes, adoption's splices included) from ad.leaf_uuid, stopping at `u`. For a compaction in the
    current turn the walk is the records since it, never the whole chain; the seen set is the cycle guard, as in
    active_path."""
    cur, seen = ad.leaf_uuid, set()
    while cur is not None and cur not in seen:
        if cur == u:
            return True
        seen.add(cur)
        cur = ad.parent_of.get(cur)
    return False


_ASM_PID_REFUSALS = ("seed", "nosummary", "replaced", "parent", "adopted", "offpath", "seedEpisode")   # _asm_pid_refusal's
#                                   reasons, in the order it tries them: each one's demotion counts as g:promptid:boundary:<reason>
#                                   (the PromptIdAdoptable tests check every reason their shapes return against this list)
_ASM_PID_EPISODE_VOUCHES = ("replaced", "parent", "offpath")   # the shapes the episode rule can vouch for (2026-10-05)


def _compact_episode_pid(r):
    """The prompt id of the /compact invocation episode record `r` joins, or None when it joins none: a user record's promptId,
    a compaction summary's aside. The one filter of which records make up an episode, so it cannot drift apart between the
    readers that ask (review of 2026-10-05, round two): adoption groups its episodes by it (_adopt_detached_compactions), and
    the promptid gate asks it of the records on hand (_asm_pid_compact_episode), of a delta record (_asm_gates) and of the
    records before a document's cut (_asm_doc_compact_pids). tests/test_event_model_assembly.py holds a summary and a record
    of another type wearing the id, each with a text that opens on the /compact tag: a filter widened for adoption alone
    adopts a pair there that the gate vouched for, and the fold serves a tree without the turn the cold parse builds."""
    if r.get("type") != "user" or r.get("isCompactSummary"):
        return None
    return r.get("promptId") or None


def _names_compact(text, first=False):
    """Whether a user record's text (its text blocks, joined) names /compact: a <command-name> tag whose name, read with a
    leading slash when it has none, is /compact. The one reader of it, so its matcher and its name normalization cannot drift
    apart between the two that ask (review of 2026-10-05, round one): adoption reads an episode record's FIRST tag (`first`,
    _adopt_detached_compactions), and the promptid gate reads every tag (_asm_names_compact) of the records the shared filter
    puts in an episode (_compact_episode_pid), so the gate says yes whenever adoption does. tests/test_event_model_assembly.py
    runs adoption itself against the gate's reading."""
    if "<command-name>" not in text:
        return False
    for m in COMMAND_NAME_ANY_RE.finditer(text):
        name = m.group(1).strip()
        if (name if name.startswith("/") else "/" + name) == "/compact":
            return True
        if first:
            return False
    return False


def _asm_names_compact(r):
    """Whether user record `r` names /compact as the promptid gate reads it: any <command-name> tag in the record's text blocks,
    the text adoption reads an episode record's first tag in (_names_compact). Raw transcript records only: the adapter's, the
    writer's rows and the gate's delta records."""
    blocks = _content(r.get("message"))
    return _names_compact((_text_of(blocks) if blocks else "") or "")


def _asm_pid_compact_episode(ad, pid):
    """Whether the episode adoption keys on `pid` can be a /compact one as far as this entry can read it, and which check said
    so: "episode" when a record on hand that adoption's own filter puts in that episode (_compact_episode_pid: a user record
    wearing the id, a compaction summary aside) names /compact (_asm_names_compact); "seed" when none does but a restored
    entry's seed lists the id, since the records before the document's cut are not in this adapter; None when neither. The
    seed's list is the document's compact_pids, the prompt ids that a record before its cut, read the same way, wears
    (_asm_doc_compact_pids); a document written before that field (2026-10-05) has none, and its prompt_ids stands in for
    it: every prompt id its writer had read, the open turn's included, a superset that only refuses more. One walk over the
    adapter's records, asked once per delta and id (the gate's `adoptable` map), and only for an id whose pair is not
    attached."""
    for r in ad.by_uuid.values():
        if _compact_episode_pid(r) == pid and _asm_names_compact(r):
            return "episode"
    seed = ad.seed or {}
    listed = seed.get("compact_pids")
    if listed is None:
        listed = seed.get("prompt_ids") or ()      # a document written before compact_pids: every prompt id its writer had read
    if pid in listed:
        return "seed"
    return None


def _asm_pid_refusal(ad, pid):
    """The promptid gate's boundary arm, asked of `pid`, a compaction summary's promptId (2026-10-03, reasons and the episode rule
    2026-10-05): (why, how), `why` the reason the entry cannot vouch for a delta record wearing the id, None when it can, and
    `how` the rule that vouched ("attached", or the episode rule's shape) or None. A record wearing the id could re-seat a
    boundary adoption, the one thing such a record can change that the fold's kept check cannot see, and there are two ways to
    rule that out.

    The attached rule: every summary on record wearing the id hangs off a compact_boundary on the entry's active path that
    adoption did not splice. An automatic compaction writes such a pair, and _adopt_detached_compactions skips it (`if b in
    active: continue`). A delta that the descent gate passes extends that path, or lands a parallel tool batch (_batch_delta),
    whose results can move the leaf onto another call's branch; the fold's recompute and kept check rule on that, and a record
    wearing the id after it is asked again here. The records after the pair already fold on the carried state (the restore
    dedup, the summaries map) when they wear another prompt's id.

    The episode rule, for a pair that is not attached (its summary replaced by a later copy of its uuid, its summary hung off a
    record that is no compact_boundary, or its boundary off the path): adoption splices a detached pair only behind the episode
    its summary's promptId names, and only when that episode is a /compact one (`if ep is not None and not ep["compact"]`; a
    summary with no promptId falls to the adjacency fallback, which reads /compact episodes alone too). So when no record the
    episode filter admits (_compact_episode_pid: a user record, a compaction summary aside) wearing the id names /compact, on
    hand or, on a restored entry, before its document's cut (_asm_pid_compact_episode), no record wearing it can make adoption
    splice anything, and the gate refuses, per record, one that adoption would put in the episode and that itself names
    /compact.
    An automatic compaction's summary wearing its turn's prompt id names that turn's records, none of them a /compact. On a
    live kernel the attached rule alone vouched for none of the summaries the transcripts held: the boundary arm refused
    every one, and each record wearing such an id parsed whole.

    Refused, as before, the reasons in _ASM_PID_REFUSALS: `seed`, a pid the restore seed carries (the document's gates name the
    pids of the summaries before its cut, whose pairs and episodes this adapter never read: _asm_doc_boundary_pids; a document
    written before that names every summary's pid, which only demotes more); `nosummary`, one with no summary in the graph;
    `adopted`, a pair adoption spliced (a live manual /compact, whose episode a later record moves, and whose episode is a
    /compact one), whichever summary wearing the id it is; `replaced`, `parent` or `offpath` when a record on hand wearing the id
    that the episode filter admits names /compact; and `seedEpisode`, a pair of one of those three shapes when no record on
    hand does but a restored entry's seed lists the id: its document's compact_pids (a record before the cut that the episode
    filter admits, wearing it, names /compact) or, for a document
    written before that field, its prompt_ids (some record its writer had read wore it). Counted apart from the shapes, so
    the restored entry's guard is told from a /compact episode the entry reads. A delta in which either rule lets through a
    record other than a compaction summary, and which a compaction, the descent or a batch's kept check then refuses, parses
    whole (_asm_demote_passed), and a fold of one counts under fold:attachedPass (and fold:attachedPass:<shape> for the
    episode rule)."""
    if pid in ((ad.seed or {}).get("boundary_pids") or ()):
        return "seed", None
    sums = getattr(ad, "summary_pids", {}).get(pid)
    if not sums:
        return "nosummary", None
    why = None
    for s in sums:
        sr = ad.by_uuid.get(s)
        if not (sr and sr.get("isCompactSummary") is True and sr.get("promptId") == pid):
            why = why or "replaced"                      # a later copy of the uuid replaced the summary (last write wins)
            continue
        b = ad.parent_of.get(s)                          # the summary's own link: adoption rewrites the boundary's and its child's
        br = ad.by_uuid.get(b) if b else None
        if not (br and br.get("type") == "system" and br.get("subtype") == "compact_boundary"):
            why = why or "parent"
            continue
        if b in ad._adopted:
            return "adopted", None                       # whichever summary it is: never vouched for
        if not _asm_on_path(ad, b):
            why = why or "offpath"
    if why is None:
        return None, "attached"
    ep = _asm_pid_compact_episode(ad, pid)
    if ep == "seed":
        return "seedEpisode", None
    if ep:
        return why, None
    return None, why


def _asm_doc_boundary_pids(ad, all_recs, cut_seq):
    """The prompt ids a document's gates name as boundary_pids (2026-10-03): those of the compaction summaries before the cut,
    every copy the pre-cut bytes carry (`all_recs`, the writer's (seq, record) pairs; _ingest adds each copy's). The writer
    reads a whole entry's adapter, so no seed is carried: it refuses a restored entry with pre-cut atoms or turns, and no
    exercised road reaches this line from the one restored entry it does not refuse there, a degenerate document's whose
    pre-cut part restored no atoms (tests/test_asm_write_over_grown_entry.py; review round one). Every restore re-ingests the
    tail, its summaries included, so the adapter it seeds holds the same boundary_pids a whole parse does, while the seed
    names only the summaries the restored entry cannot see, which is what _asm_pid_refusal's seed check asks. The gates
    named every summary's pid until then, so a restore from any document written after a compaction past its cut (a restart
    in the turn, the re-seat after a later whole parse) demoted every record wearing that summary's pid for the rest of the
    turn. A document written before then still restores, and its wider set only demotes more."""
    out = set()
    for sq, r in all_recs:
        if sq < cut_seq and r.get("type") == "user" and r.get("promptId") and r.get("isCompactSummary") is True:
            out.add(r["promptId"])
    return out


def _asm_doc_compact_pids(ad, all_recs, cut_seq):
    """The prompt ids a document's gates name as compact_pids (2026-10-05): those that a record before the cut naming /compact
    wears, every copy the pre-cut bytes carry, read as the episode rule reads a record on hand: adoption's filter of the records
    in an episode (_compact_episode_pid: a user record, a compaction summary aside) and the gate's reading of /compact
    (_asm_names_compact). What _asm_pid_compact_episode's seed check asks: a restored entry holds the tail's records, and
    this names the pre-cut episodes that could be /compact ones. The gates' prompt_ids, every prompt id the writer had read,
    would not do: an entry restored from any document written after a turn opened (a restart, a dropped entry restored again,
    the re-seat after a whole parse) would refuse that turn's every later record wearing an off-path pair's id for the rest of
    the turn, as an entry restored from a document written before this field still does. No seed term, as in
    _asm_doc_boundary_pids: the writer refuses a restored entry with pre-cut atoms or turns, and it writes for the degenerate
    one it does not refuse there only when each file's records reached the adapter from record zero (the offsets check
    refuses a reader entry read from the cut, and lends a longer one only to an adapter that read from zero), so all_recs
    holds the records before the cut on every road that reaches this line (review of 2026-10-05, round one: the term was
    reached only through seeds a test set by hand)."""
    out = set()
    for sq, r in all_recs:
        if sq < cut_seq and _compact_episode_pid(r) and _asm_names_compact(r):
            out.add(r["promptId"])
    return out


def _asm_doc_measure(entry):
    """(the pre-cut bytes over the lineage, the leaf's cut offset) of the document standing for `entry`: a restored entry's
    (docPre, docCutOff), the one a whole entry wrote (docCut), or, for a whole entry built after a restore refused the
    document for a parent not written yet and left it standing, the one the entry it replaced knew (docStands, 2026-11-12:
    a compaction whose summary lands with the late parent restores from it). None when the entry knows of none."""
    if entry.get("docPre") is not None:
        return entry["docPre"], entry.get("docCutOff")
    if entry.get("docCut") is not None:
        return entry["docCut"][1], entry["docCut"][4]
    return entry.get("docStands")


def _asm_compaction_under_share(entry, leaf_path):
    """Whether a compaction's demotion may take the restore road: True while the leaf's bytes past the standing document's cut,
    times _ASM_TAIL_SHARE, are still under that document's pre-cut bytes, False once they have reached them. A restored entry
    carries the cut it came from (docPre, docCutOff), a whole entry the one it wrote (docCut) or the one a refusal for a parent
    not written yet left standing (docStands, _asm_doc_measure). None when there is nothing to
    measure: a whole entry that wrote no document (a session under the first document's floor, a settle that declined its
    write, a compaction before the settle ran) or a leaf the stat cannot read. The caller parses whole on None as on False, but
    counts only False as past the share, so the counter holds only compactions that had a document to restore from. The churn
    bound this keeps (review of 2026-09-24): a restored entry writes no document, and the fold's own share gate reads
    only the atoms-only restore and a re-seated entry, so while every compaction restored nothing moved the cut of the
    lazy-index entry a boot restores, and the tail every later restore and boot reads grew for the session's life (34 MB
    past the cut after four compactions at 150 to 184 MB, the boot's restore doubled); a compaction past the share now
    parses whole, as every compaction did before, and the settle after it writes a later cut."""
    measure = _asm_doc_measure(entry)
    if measure is None:
        return None
    pre, cut_off = measure
    try:
        tail = os.stat(leaf_path).st_size - int(cut_off or 0)
    except OSError:
        return None
    return tail * _ASM_TAIL_SHARE < max(1, int(pre))


class _NoLock:
    """A context manager that takes nothing (the writer over a private entry, asm_refresh_document)."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_NO_LOCK = _NoLock()


def _asm_key_lock(key):
    with _ASM_LOCK:
        lk = _ASM_KEYLOCKS.get(key)
        if lk is None:
            lk = _ASM_KEYLOCKS[key] = threading.Lock()
        return lk


def _asm_release(entry):
    """The lazy index behind a DROPPED or REPLACED assembly entry gives its materialized atoms back to the LRU
    (LazyIndex.release): the entry was the index's owner of record, and what outlives it (a tree the parse cache or a build
    in flight still holds) rebuilds through its own index on its next read, as after an eviction. Called on the popped
    entry OUTSIDE _ASM_LOCK: release takes _MAT_LOCK, and the two locks are never nested, in either order. None, a whole
    parse's entry (no index) and an index released twice are no-ops."""
    ix = entry.get("index") if entry else None
    if ix is not None:
        ix.release()


def _asm_serve(entry):
    """A caller-owned copy of the entry's emit outputs: fresh top-level atom dicts (parse_session
    pops _seq and the turn builder sorts in place; the pristine list keeps both), a landed copy,
    and no pending cut (cut parses never reach the cache). A restored entry's prefix (the lazy atoms
    before the checkpoint's cut, T323 stage 4) comes first, in emit order, like a whole parse's; its skill loads
    (T333) are the carried pre-cut ones plus the tail's, in the seeded emit state."""
    return ([dict(a) for a in entry.get("prefix") or []] + [dict(a) for a in entry["atoms"]], set(entry["landed"]), None,
            dict(entry["st"].get("skill_loads") or {}), list(entry.get("preTurns") or []))


def _asm_full(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, keep_whole=False, installed_out=None,
              stands=None):
    """Full parse + a fresh cache entry — the same steps atoms() runs, inlined so the entry keeps
    the carry (st) the emit actually used; later folds continue from it. `keep_whole`: a restore just
    refused this leaf's document, so the entry is never re-seated on the one written from it (keepWhole). `installed_out`,
    when a list, takes the entry installed (parse_session hands it with a rewrite after a refused document). `stands`: the
    document a refusal for a parent not written yet left standing, as the demoted entry knew it ((pre, cut offset), whether
    it was written while a postal author waited), kept on the entry for the compaction road (_asm_doc_measure)."""
    entry = _asm_whole_entry(leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human)
    if keep_whole:
        entry["keepWhole"] = True
    if stands is not None:
        entry["docStands"] = stands[0]
        if stands[1]:
            entry["docPostalWait"] = True
    if installed_out is not None:
        installed_out.append(entry)
    with _ASM_LOCK:
        gone = [_ASM_CACHE.pop(key, None)]
        while len(_ASM_CACHE) >= _ASM_CACHE_MAX:
            gone.append(_ASM_CACHE.pop(next(iter(_ASM_CACHE))))   # oldest-used first; hot entries survive floods
        _ASM_CACHE[key] = entry
    for e in gone:
        _asm_release(e)                              # the replaced generation and the evicted entries give their memo back
    _asm_stat("full")
    return _asm_serve(entry)


def _asm_whole_entry(leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, private=False):
    """The whole parse's assembly entry, built and NOT installed: _asm_full installs it as the leaf's entry, and the document
    refresh (asm_refresh_document) writes a document from one that no reader ever sees. `private`: the private road's call,
    which holds no lock, so its phases are a rebuild's chunk points (_asm_bg_yield: after the read, after the pre-pass, and
    every _ASM_BG_EMIT_CHUNK atoms of the emit; a no-op on any thread but the rebuild's). _asm_full's call holds the key lock
    and never waits."""
    ad = FileAdapter(candidate_files, leaf_path, resume_links=links)
    ad.sdk_human = sdk_human
    gate = _asm_bg_yield if private and getattr(_ASM_BG_TL, "gated", False) else None
    held = None
    if gate is not None:
        held = _reader_entries_of({"recs": dict(ad._src_keys)})   # the reader entries just read, kept for the writer's record
        gate()                                                    #  offsets: a wait below may outlast their stay in the cache
    st = _emit_state()
    kept = ad.kept_uuids(ad.active_path())
    order = _chrono(ad, kept)
    ad._prepass(order, st)
    if gate is None:
        atoms = list(ad._emit_fold(order, st, rompuuid, postal_index))
    else:
        gate()
        atoms = []
        for a in ad._emit_fold(order, st, rompuuid, postal_index):   # the generator holds no lock between its atoms
            atoms.append(a)
            if len(atoms) % _ASM_BG_EMIT_CHUNK == 0:
                gate()
    atoms += ad._absorbed(ad.qatts, kept, st, rompuuid, postal_index)
    entry = {"ad": ad, "st": st, "atoms": atoms, "kept": kept,
             "landed": ad.landed_text_uuids(),
             "cands": tuple(str(f) for f in candidate_files), "links": dict(links or {}),
             "recs": dict(ad._src_keys), "n_qatts": len(ad.qatts), "prefix": [],
             "path": str(leaf_path)}   # as handed: the reader's key for the same leaf (asm_whole_entries hands it back)
    if held is not None:
        entry["heldReaders"] = held     # a rebuild's: the writer takes the offsets from them when the cache has let go (held_readers)
    return entry


def _asm_drop_where(holds):
    """Pop every assembly entry `holds(entry)` is true of under _ASM_LOCK, then release each popped entry OUTSIDE it
    (_asm_release takes _MAT_LOCK, never nested with _ASM_LOCK). Returns the popped entries. evict_document's assembly
    half, shared with the byte budget's release (_asm_drop_holding)."""
    popped = []
    with _ASM_LOCK:
        for k in [k for k, e in _ASM_CACHE.items() if isinstance(e, dict) and holds(e)]:
            e = _ASM_CACHE.pop(k, None)
            if e is not None:
                popped.append(e)
    for e in popped:
        _asm_release(e)
    return popped


def _asm_drop_holding(paths):
    """The byte budget's release (the owner's call, 2026-10-02): drop every assembly entry that read one of `paths`, the
    files a budget eviction just took (a file before a restore's cut, read by no one, is skipped), so the eviction lets
    go of the records the entry's adapter held: for a chat still using the file, their lists go at once and their text
    when the parse store's slot, a shown tab's build memos and the live-merge memo of the tree go; each entry dropped
    counts under recordCache.budgetReleased. The next parse that is not a parse-store hit (a comment create, the first
    build after an append) restores the entry from its document or parses whole, adding the records again (a session
    whose stored parse fell back installs nothing; one whose rebuild itself falls back after this release parses again
    at each create until its files change). Called by the reader after it lets _JSONL_CACHE_LOCK go, never under it:
    it takes _ASM_LOCK, then _MAT_LOCK, then _JSONL_CACHE_LOCK for the count, one at a time (the last as the reader's
    own insert takes it), and no holder of the first two waits on any other lock."""
    gone = set(paths)
    popped = _asm_drop_where(lambda e: any(k in gone and v != ("skip",) for k, v in (e.get("recs") or {}).items()))
    if popped:
        with _JSONL_CACHE_LOCK:
            _RECORD_CACHE_STATS["budgetReleased"] += len(popped)
    return popped


def evict_document(leaf_path):
    """Drop one leaf document's entries from BOTH parse caches: the record cache (_JSONL_CACHE) and the assembly cache
    (_ASM_CACHE). Neither cache's own bound helps a loop that parses many ONE-SHOT documents (the judge experiment's
    per-ending passes): the assembly cache stops at _ASM_CACHE_MAX (256) and the record cache's byte budget is half of
    physical memory, so both grow to the cap over hundreds of endings, and a whole-instance clear would nuke a live kernel's
    hot entries. Calling this after each document keeps both flat. Record entries pop through _cache_pop_locked under the
    record lock so the byte count stays right; assembly entries (found by the leaf `path` their _asm_full stamped) pop under
    _ASM_LOCK and release through _asm_release OUTSIDE the lock, so a lazy index gives its materialized atoms back (a bare dict
    clear skips both, as _asm_full's comment notes). Absent entries are no-ops. Returns (asm_dropped, record_dropped)."""
    leaf = str(leaf_path)
    popped, files = _asm_drop_where(lambda e: e.get("path") == leaf), {leaf}
    for e in popped:
        files.update(e.get("cands") or ())
    dropped = 0
    with _JSONL_CACHE_LOCK:
        for f in files:
            if f in _JSONL_CACHE:
                _cache_pop_locked(f)
                dropped += 1
    return len(popped), dropped


def _records_shared(held, recs):
    """Whether the reader's held list `held` holds the records of `recs` (a list an adapter took from the reader): the same
    list, or the same first and last record objects at the list's start. Only an append shares records, and it stores
    records + new under the same base; every read that changes an entry's base (a tail upgraded to the whole file, a read
    from zero, a tail restored from an earlier start) decodes new objects, so the start is the one place to look. Two
    identity checks, never a walk."""
    if held is recs:
        return True
    n = len(recs)
    return 0 < n <= len(held) and held[0] is recs[0] and held[n - 1] is recs[-1]


def _list_file_bytes(size, n):
    """The file bytes of an n-record list an adapter holds, from the (weight, count) it recorded for its list (_src_bytes):
    the weight itself for the list it was recorded for, else the same bytes per record."""
    w, n0 = size if size else (0, 0)
    return int(w) if n0 == n else (int(w * n / n0) if n0 else 0)


def asm_pinned_records() -> dict:
    """The records an assembly entry's adapter holds that the record cache no longer does, for /perf's heap block (the memory
    diagnosis, 2026-10-01: the record cache let go of a leaf, its counted bytes fell, and the entry's adapter still held the very
    same list, so the memory did not; the next whole read decoded a second copy). For every installed entry, each file list the
    adapter took from the reader (its _src, a private or skipped file aside) whose records the reader's current entry for that
    path does not hold (_records_shared) is pinned, and so is every list a fold superseded (_src_superseded: a tail the reader
    upgraded to the whole file, whose records the adapter's maps keep after the fold re-seated the leaf on the whole list):
    `lists`, `records`, `fileBytes` (the reader entry's weight when the adapter took or re-seated the list, scaled by record
    count when the adapter could not weigh its current list: the leaf's own bytes per record) and `bytes` (times the multiple
    in force, _RECORD_CACHE_MULTIPLE, read at the call). A list two entries share counts once. It weighs records alone: an
    entry's own atoms and lookup maps (by_uuid, the graph), about 0.9 to 1.1 KB per record of the leaf a whole entry parsed
    (tracemalloc on two synthetic leaves, 2026-10-01: 0.31 bytes per file byte where records carry long tool results, 4.0
    where they are short), stay a count (assemblyEntries), so a 0 here never says the entries are free. O(entries x their files):
    one record-cache lookup per file, taken under the cache's lock with nothing else, then two identities each after it, so a
    reader's hit never waits on the checks; no list is iterated; the two locks are taken one after the other."""
    with _ASM_LOCK:
        entries = list(_ASM_CACHE.values())
    cands, pinned = [], {}

    def pin(key, n, fb):                                  # the same records, whichever list carries them: the longest counts
        if key not in pinned or pinned[key][0] < n:
            pinned[key] = (n, fb)
    for entry in entries:
        ad = entry.get("ad") if isinstance(entry, dict) else None
        if ad is None:
            continue
        keys, sizes = getattr(ad, "_src_keys", None) or {}, getattr(ad, "_src_bytes", None) or {}
        for p, recs in list((getattr(ad, "_src", None) or {}).items()):
            k = keys.get(p)
            if not recs or not (isinstance(k, tuple) and len(k) == 3):   # an empty list, a ("private",) or ("skip",) row
                continue
            cands.append((p, recs, sizes.get(p)))
        for p, gone in list((getattr(ad, "_src_superseded", None) or {}).items()):
            for first, n, fb in list(gone):
                pin((p, id(first)), n, fb)
    with _JSONL_CACHE_LOCK:                               # the reader's entries as they stand (a tuple whose list a read replaces,
        held = [_JSONL_CACHE.get(p) for p, _, _ in cands]  #  never extends in place); the identities are checked after the lock
    for (p, recs, size), ent in zip(cands, held):
        if ent is not None and _records_shared(ent[4], recs):
            continue
        pin((p, id(recs[0])), len(recs), _list_file_bytes(size, len(recs)))
    fb_total = sum(fb for _, fb in pinned.values())
    return {"lists": len(pinned), "records": sum(n for n, _ in pinned.values()), "fileBytes": fb_total,
            "bytes": int(round(fb_total * _RECORD_CACHE_MULTIPLE))}


def _asm_lazy_uuids(e):
    """The uuids of the pre-cut records assembly entry `e` serves as lazy atoms, whose bodies the hydrated memo holds, one at a
    time: its lazy index's record rows, or the atoms-only form's prefix atoms (read without materializing any). Nothing for a
    whole entry."""
    ix = e.get("index") if e else None
    if ix is not None:
        for r in ix.records or ():
            if r:
                yield r[0]
    pre = e.get("prefix") if e else None
    if isinstance(pre, list):
        for a in list.__iter__(pre):
            if isinstance(a, dict) and a.get("uuid"):
                yield a["uuid"]


_ASM_RELEASE_HOOK = [None]        # the kernel's listener for the end of a leaf's use (set_assembly_release_hook)


def set_assembly_release_hook(fn):
    """Kernel wiring (once, at its import): `fn(realpath, rompuuid or None)` is called at every release_assembly, after the
    leaf's entries and its noted rebuilds are gone, so a rebuild of that leaf running OUTSIDE this process (the document
    worker) is ended too: nothing will restore from its document. The hook must not block (a release runs under its caller's
    locks) and a raise from it never fails the release (said once per kind). None removes it."""
    _ASM_RELEASE_HOOK[0] = fn


def release_assembly(leaf_path, rompuuid=None):
    """Drop a leaf's assembly entries at an event that ends its use (2026-10-01): a /clear or a resume fork retiring the
    leaf (the judges' _note_leaf), a dead timeline lane, the last viewer of a subagent closing. Before, nothing left at
    those events: the entry (an adapter holding every record it read, its atoms and maps, a restored entry's lazy index)
    stayed until the cache's count cap reached it. Only the assembly cache and the hydrated-body memo are touched: the
    record cache is the reader's (a /clear's anchor is still the new leaf's candidate; a caller drops the files no live
    session reads with release_records), and evict_document, which pops every candidate's record entry, is the scripts'.
    With `rompuuid`, only that session's entries go (a fork child's flip leaves the parent's entry of the same file). Each
    popped entry's lazy index gives its memo back outside _ASM_LOCK (_asm_release), and the hydrated bodies of its pre-cut
    records leave the memo: nothing serves that entry's atoms again, and the session parsed again restores and hydrates
    afresh, as after an eviction. The memo is keyed by uuid alone, and other live entries can name the same records (an SDK
    fork child's transcript carries its parent's history verbatim, a resume fork's leaf walks through the old one's): those
    bodies go too, and such an entry's next read of one is a seek read from disk. No other entry's rows are read here (round
    one of the fold's review: subtracting what every other entry named read every peer's rows, under the timeline build's
    lock at a dead lane, to spare reads that cost one seek each). Returns how many entries went, counted `released` among
    the parse's road counters."""
    rp = os.path.realpath(str(leaf_path))
    with _ASM_LOCK:
        keys = [k for k in _ASM_CACHE if k[0] == rp and (rompuuid is None or k[1] == str(rompuuid))]
        popped = [e for e in (_ASM_CACHE.pop(k, None) for k in keys) if e is not None]
    uuids = set()
    for e in popped:
        _asm_release(e)                              # OUTSIDE _ASM_LOCK: release takes _MAT_LOCK, never nested with it
        uuids.update(_asm_lazy_uuids(e))
    if uuids:
        with _ASM_CKPT_LOCK:
            for u in uuids:
                hit = _HYDRATED.pop(u, None)
                if hit is not None:
                    _HYDRATED_BYTES[0] -= hit[1]
    if popped:
        _asm_stat("released", len(popped))
    asm_rebuild_drop(leaf_path, rompuuid)            # the leaf's use ended: no document is rebuilt for it
    hook = _ASM_RELEASE_HOOK[0]
    if hook is not None:                             # nor does one being built in another process carry on (the document worker)
        try:
            hook(rp, None if rompuuid is None else str(rompuuid))
        except Exception as e:                       # noqa: BLE001 - a trim must never fail the event it rides
            _say_once("assembly checkpoint: the release hook raised (%s: %s; said once per kind); the release stands"
                      % (type(e).__name__, str(e)[:120]))
    return len(popped)


_RELEASE_FAILED = set()            # (where, path, outcome) whose release at the end of a file's use, or whose record drop's
#                                    pin check, raised: said once each, a release's line saying it stopped where it raised and that what
#                                    ran before it stays released (RELEASE_FAILED_OUTCOME), the pin check's that the entry
#                                    counts as not pinning
RELEASE_FAILED_OUTCOME = "the release stopped where it raised and what ran before it stays released"


def release_failed(where, path, exc, sid=None, outcome=RELEASE_FAILED_OUTCOME):
    """A release at the end of a file's use raised (a stat or a sidecar read refused, a lineage that does not resolve), and
    the caller's event goes on, since a trim must never fail the event it rides (discover's leaf flip, under every pane; a
    timeline frame; a viewer's close); or a record drop's pin check raised, and that entry counts as not pinning, so the
    drop goes ahead unless another entry pins it. Counted `releaseFailed` among the parse's
    road counters at every failure, said on stderr once per (where, path, outcome) (2026-10-01; the outcome since round four
    of the fold's review, so the dead lane's two outcomes for one leaf are each said), with the session `sid` when the
    caller has one; the memo keys on the event, the path and the outcome, so of several sessions failing on a shared leaf
    only the first is named. The default `outcome` says only that the release stopped where it raised and that what ran before it
    stays released, true at every caller, one where nothing had run yet included: the leaf flip's record drop and its
    lineage read run after its assembly release, and a dead lane's record release after its own. A caller adds what holds
    at it alone (the dead lane, once it has recorded the transcript's version: not retried until the transcript moves; the
    leaf flip: its record drop is not retried), and the pin check's words replace the default, saying only that the entry
    counts as not pinning (round three of the fold's review: the old default said the memory was left as it stood, false
    wherever a release had popped an entry before the raise)."""
    _asm_stat("releaseFailed")
    k = (str(where), str(path), str(outcome))
    if k in _RELEASE_FAILED:
        return
    if len(_RELEASE_FAILED) >= 4096:
        _RELEASE_FAILED.clear()                      # re-arm past the bound: a repeated line beats a silence that never lifts
    _RELEASE_FAILED.add(k)
    try:
        sys.stderr.write("romp event-model: releasing the caches of %s%s at %s failed (%s; said once per file and outcome): "
                         "%s\n"
                         % (path, " for session %s" % sid if sid else "", where, outcome,
                            ("%s: %s" % (type(exc).__name__, exc))[:200]))
    except Exception:
        pass


def _asm_gates(entry, leaf_path, candidate_files, links):
    """None -> full parse. Else (delta, leaf_records): the leaf's appended records (possibly
    empty) and the records list they came from, gate-checked per the block comment above."""
    if entry["cands"] != tuple(str(f) for f in candidate_files) or \
            entry["links"] != dict(links or {}):
        return _asm_demote("inputs")
    ad = entry["ad"]
    leaf_stem = Path(leaf_path).stem
    files = [f for f in candidate_files if Path(f).stem != leaf_stem] + [Path(leaf_path)]
    delta = leaf_recs = None
    if entry.get("docPre") is not None and (entry.get("prefix") or entry.get("reseated")):
        # a RESTORED entry: its cut advances only through a whole parse (the pre-cut records are lazy rows, not in hand), so
        # when the tail past the document's cut has grown to the share the entry is demoted here and the settle that follows
        # the whole parse writes the new cut (stage one b's churn bound; a compaction in the tail demotes below and restores from
        # the same document while the tail is under the share, 2026-09-24: _asm_compaction_under_share; at the share this gate
        # files tailShare first, and the compaction parses whole under that reason). A RE-SEATED entry (2026-09-24) is held to
        # it whatever its document's form: the whole entry it replaced carried this bound in the writer, and a turns-section
        # restore leaves `prefix` empty, so the test on `prefix` alone would freeze the re-seated leaf's cut for the rest of the
        # process
        try:
            tail_now = os.stat(leaf_path).st_size - int(entry.get("docCutOff") or 0)
        except OSError:
            tail_now = 0
        if tail_now > 0 and tail_now * _ASM_TAIL_SHARE >= max(1, int(entry["docPre"])):
            return _asm_demote("tailShare")
    for fp in files:
        old = entry["recs"].get(str(fp))
        if old is None:
            return _asm_demote("recs-gone")
        if old == ("skip",):                                   # a file wholly before the checkpoint's cut: immutable by
            fst = (entry.get("skipped") or {}).get(str(fp))    #  contract (a fork's prior file); its stat is the proof
            try:
                st_ = os.stat(fp)
                if fst is None or (st_.st_size, st_.st_mtime) != tuple(fst):
                    return _asm_demote("nonleaf")
            except OSError:
                return _asm_demote("nonleaf")
            continue
        ent = _read_jsonl_entry(fp, tail_ok=True)
        if ent is None:
            return _asm_demote("recs-gone")
        gen, base, recs = ent[6], ent[5], ent[4]
        ogen, obase, ocount = old
        if gen != ogen or base > ocount:
            return _asm_demote("rewrite" if Path(fp).stem == leaf_stem else "nonleaf")   # a from-zero read replaced the
        #                                                                                  entry: a rewrite, a shrink, an eviction
        count = base + len(recs)
        if Path(fp).stem != leaf_stem:
            if count != ocount:
                return _asm_demote("nonleaf")   # a lineage file grew: the closure changed under the entry
            continue
        leaf_recs = recs
        if count == ocount:
            delta = []
        elif count > ocount:
            delta = recs[ocount - base:]        # the records past what the entry folded (the reader's generation proves the prefix)
        else:
            return _asm_demote("rewrite")
    if delta is None:
        return _asm_demote("no-leaf-slot")
    if not delta:
        return [], leaf_recs
    max_ppt = entry["st"]["max_ppt"]
    old_leaf = ad._pristine_leaf
    if old_leaf is None:
        return _asm_demote("empty-graph")
    parent_d, new_leaf = {}, old_leaf
    compaction = None      # the delta's compaction reason, filed only once every record has met the other gates (below)
    adoptable = {}         # a summary pid -> _asm_pid_refusal's (why, how), asked once per delta
    attached_pass = False  # the narrowed boundary arm let a record through where it demoted before: a later demotion the restore
    #                        road would take parses whole instead (_asm_demote_passed), and the fold counts it once it serves
    pass_tags = set()      # the shapes the episode rule vouched for in this delta's passes (fold:attachedPass:<shape>)
    late = False           # a record landing as the parent an earlier record named: filed after the delta, as a compaction is
    for r in delta:
        t, u = r.get("type"), r.get("uuid")
        if u:
            if u in ad.by_uuid or u in ((ad.seed or {}).get("verdicts") or {}):
                return _asm_demote("uuid-known")   # a re-write rebinds last-write-wins index state; a RESTORED entry's adapter holds
                #                                    only the tail's records, its pre-cut uuids live in the seed (round seven: a
                #                                    reuse of a pre-cut uuid folded and served u1 a1 u2 a2 where a cold parse clears
                #                                    them)
            if u in ad.dangling:
                late = True        # a resurrected dangling target rebinds repaired stitches, which the fold cannot re-derive; the
                #                    restore can, from the cut: `late-parent` takes its road (2026-11-12). It was `uuid-known`, a
                #                    whole parse: the CLI writes some of a compaction's records before the parent they name, so a
                #                    build between the two writes left the parent dangling and its arrival re-read the transcript
            p = r.get("parentUuid") or r.get("logicalParentUuid")
            parent_d[u] = None if p == u else p
            new_leaf = u
        if t == "system" and r.get("subtype") == "compact_boundary":
            compaction = compaction or "boundary"   # sets the pre-pass's compaction gate and can re-seat adoptions
        if r.get("isCompactSummary") is True:
            compaction = compaction or "summary"    # attaches to its boundary and arms the restore dedup in the
            #                                         chronological pre-pass (the summary, not the boundary, 2026-09-19)
        if t == "user" and r.get("promptId") and r["promptId"] in ad.prompt_ids:
            # A repeated promptId is ROUTINE — every record of a turn wears its prompt's id, so
            # tool results repeat it on nearly every append (measured: this gate, unshaped,
            # demoted 1863 of 1869 bursts on a live 46MB replay). Only two shapes can
            # re-classify OLD atoms: a command-wrapper-family record (it grows the twins map
            # retroactively) and any record wearing an adoptable boundary's episode pid (it can
            # re-seat the adopted card's splice without changing kept — invisible to the
            # invariance check). Everything else folds: the carried twins map serves the DELTA
            # record's own classification record-locally. A summary pid counts as adoptable
            # unless the entry vouches for it (_asm_pid_refusal): every summary wearing it hangs
            # off an attached boundary (2026-10-03: an automatic compaction's summary, when it
            # wears its turn's prompt id, demoted every later tool result of that turn), or no
            # /compact episode wears it, so adoption has no episode to splice its pair behind
            # (the episode rule, 2026-10-05), and then a record that adoption would put in the
            # episode and that itself names /compact is refused here. The two arms count apart
            # (g:promptid:boundary, g:promptid:wrapper),
            # the boundary arm's demotions again by reason (g:promptid:boundary:<reason>, and
            # boundarySeed, the seed's part), beside g:promptid. A delta the narrowed arm let through
            # (a record other than a compaction summary, which does not mark its own delta)
            # is marked (attached_pass): if a compaction, the descent or a batch's kept check then
            # refuses it, it parses whole under promptid as before (g:promptid:passedDemote), and
            # if it folds, the fold counts it (fold:attachedPass, the folds the narrowing saved,
            # and fold:attachedPass:<shape> for each shape the episode rule vouched for in it).
            _wtxt = _text_of(_content(r.get("message"))) or ""
            _pid = r["promptId"]
            _why = _how = None
            if _pid in ad.boundary_pids:
                if _pid not in adoptable:
                    adoptable[_pid] = _asm_pid_refusal(ad, _pid)
                _why, _how = adoptable[_pid]
                if _why is None and _how in _ASM_PID_EPISODE_VOUCHES and _compact_episode_pid(r) and _asm_names_compact(r):
                    _why = _how          # this record would make the id's episode a /compact one: the episode rule no longer holds
            _bnd = _why is not None
            if _bnd or (CMD_WRAP_RE.match(_wtxt) and not is_skill_load_wrapper(_wtxt)):   # the harness's skill load
                _asm_stat("g:promptid:" + ("boundary" if _bnd else "wrapper"))          #   re-classifies nothing (T333)
                if _bnd:
                    _asm_stat("g:promptid:boundary:" + _why)
                if _bnd and _pid in ((ad.seed or {}).get("boundary_pids") or ()):
                    _asm_stat("g:promptid:boundarySeed")
                return _asm_demote("promptid")
            if _pid in ad.boundary_pids and r.get("isCompactSummary") is not True:
                attached_pass = True     # not the delta's own summary (round two): a turn's later compaction, its summary wearing
                #                          the id the first put on record, restores alone in its append as any compaction does
                if _how in _ASM_PID_EPISODE_VOUCHES:
                    pass_tags.add(_how)
        if t == "assistant":
            for b in _content(r.get("message")) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and \
                        b.get("name") == "Skill" and b.get("id") in ad.src_tool_links:
                    return _asm_demote("skill-link")   # an already-ingested payload references it
        if t in ("user", "assistant"):
            ts = parse_z(r.get("timestamp"))
            if ts is None or ts < max_ppt:
                return _asm_demote("ts")   # the carry is valid only for a delta sorting at-or-
                #                            after everything folded (the pre-pass sorts None first)
    if compaction is not None:
        # A compaction demotes the fold, and its demotion falls to the restore road, which parses the tail from the document's
        # cut and proves only that the tail chains onto the document. So the reason is filed only here, after the whole delta:
        # a record past the compaction that the fold refuses for its own reason (a stamp before the records folded, a Skill call
        # a pre-cut payload names, a re-classifying prompt id) demoted above for that reason and keeps its whole parse (review
        # of 2026-09-24: returning at the compaction masked them, and the restore served a tree a cold parse does not build, at
        # every later boot as well). A record landing as the parent a pre-cut record named was such a record until 2026-11-12;
        # it now files `late-parent` (below, masked by the compaction like any restore-road reason), and the restore's chain
        # proof refuses it either way. A delta holding a record the narrowed promptid arm let through parses whole as it did
        # before the narrowing (_asm_demote_passed)
        return _asm_demote_passed() if attached_pass else _asm_demote(compaction)
    if late:
        # A late parent (2026-11-12) demotes the fold to the restore road, filed here after the whole delta for the reason a
        # compaction is: a record past it that the fold refuses for its own reason keeps its whole parse. The restore proves the
        # tail chains onto the document with the parent in hand, and refuses when a PRE-CUT record named it, whose verdicts the
        # document decided without it (_tail_chains_onto_the_document), so that case parses whole as it did under uuid-known
        return _asm_demote_passed() if attached_pass else _asm_demote("late-parent")
    # Leaf descent: the new file-leaf must chain back to the old one THROUGH the delta — an
    # api_error spur re-parent, a rewind, and a /clear fork all fail here. walk.pop doubles
    # as the cycle guard; a delta with no uuid-bearing record passes trivially (leaf unmoved).
    # The one shape that fails the walk and still folds is a PARALLEL TOOL BATCH landing
    # (2026-09-24): each result is parented at its own call, so while the results land the
    # leaf moves from one call's branch to the next and no chain leads back. The graph keeps
    # those branches (FileAdapter._batch_head), so a delta hanging off known records only by
    # the batch's own link (_batch_delta) folds and the kept-invariance check in _asm_fold
    # rules on it. Before, every result not parented at the current leaf demoted here: a
    # restore (or a whole parse with no document) and a chat prefix rebuild per result, 15 in
    # the parse investigation's lab over five batches of three calls. The thread's batch bit
    # says the delta passed on the batch link, so a kept rejection of it takes the road the
    # descent gave it (_asm_fold). It is cleared before every walk, so it speaks for this delta
    # alone: a later delta that descends from the old leaf keeps a kept rejection's own road.
    # The pass bit beside it, set the same way, says the narrowed promptid arm let a record of
    # this delta through: a descent demotion of such a delta, and a batch's kept rejection,
    # parse whole instead of restoring (_asm_demote_passed), and its fold is counted. Both bits
    # set means the re-seat (_assemble) steps aside, so that kept check rules (round two). The
    # pass tags, set with them, name the shapes the episode rule vouched for, for the fold's count.
    _ASM_DEMOTE_TL.batch = False
    _ASM_DEMOTE_TL.passed = attached_pass
    _ASM_DEMOTE_TL.pass_tags = tuple(sorted(pass_tags))
    cur, hops, walk = new_leaf, 0, dict(parent_d)
    while cur != old_leaf:
        if cur is None or cur not in walk or hops > len(delta) + 1:
            if ad._batch_delta(delta, parent_d, old_leaf):
                _ASM_DEMOTE_TL.batch = True
                break
            return _asm_demote_passed() if attached_pass else _asm_demote("descent")
        cur = walk.pop(cur)
        hops += 1
    return delta, leaf_recs


def _asm_heal(entry, rompuuid, postal_index):
    """Re-author atoms whose postal author is still PROVISIONAL (_author_final) against the
    current index: replace on any change so serves match a full parse NOW, and retire the miss
    only once the trailing marker resolves — a record whose quoted earlier marker resolved first
    would otherwise latch the wrong sender forever (review catch, 2026-09-01). Record-local by
    design: author_of reads only the record's text + the index. The qatt pick mirrors
    _absorbed's kept filter exactly — the first same-key twin can sit on a rewound branch, and
    healing from it would flip the atom's identity to the abandoned record (same review)."""
    st, ad = entry["st"], entry["ad"]
    if st["postal_miss_rec"]:
        pos = {a.get("uuid"): i for i, a in enumerate(entry["atoms"]) if a.get("uuid")}
        done = set()
        for u in list(st["postal_miss_rec"]):
            i = pos.get(u)
            if i is None:
                done.add(u)      # no atom carries it any more (entry rebuilt around it) — retire
                continue
            a2 = next(iter(ad._emit_fold([u], st, rompuuid, postal_index)), None)
            if a2 is None:
                done.add(u)
                continue
            if a2 != entry["atoms"][i]:
                entry["atoms"][i] = a2   # in place is safe: serves copy, and we hold the key lock
            if _author_final(a2.get("author"), _text_of(_content(a2.get("message")))):
                done.add(u)
        st["postal_miss_rec"] -= done
    if st["postal_miss_att"]:
        kept = entry["kept"]
        for i, a in enumerate(entry["atoms"]):
            if not a.get("absorbed"):
                continue
            key = (a.get("sentAt", a["t"]), _th(" ".join(_text_of(_content(a.get("message"))).split())))   # the (send ts,
            #                                                                            text hash) key: `t` is the landing (T252d)
            if key not in st["postal_miss_att"]:
                continue
            q = next((q for q in ad.qatts if q["ts"] == key[0]
                      and _th(" ".join(q["text"].split())) == key[1]
                      and (q["uuid"] is None or q["uuid"] in kept)), None)
            if q is None:
                st["postal_miss_att"].discard(key)
                continue
            a2 = ad._absorbed_atom(q["text"], q["ts"], q["seq"], q["uuid"], rompuuid, postal_index, stats=True)
            if a2 != a:
                entry["atoms"][i] = a2
            if _author_final(a2.get("author"), q["text"]):
                st["postal_miss_att"].discard(key)


def _asm_fold(entry, delta, leaf_recs, leaf_key, leaf_stem, rompuuid, postal_index):
    """Fold `delta` into the entry: extend the PRISTINE graph, re-derive the repair passes on a
    working copy (fold state is literally the fresh-__init__ computation — never a latched earlier
    repair), recompute ALL graph verdicts exactly, then emit only the appended kept records with
    the carried state. None -> the kept-invariance check failed; caller demotes to full."""
    ad = entry["ad"]
    ad.parent_of = ad._pristine_parent
    ad.leaf_uuid = ad._pristine_leaf
    n_qatts = entry["n_qatts"]
    ad._ingest(delta, leaf_stem, True)
    ad._pristine_parent = ad.parent_of
    ad._pristine_leaf = ad.leaf_uuid
    ad.parent_of = dict(ad._pristine_parent)
    ad._run_graph_passes()
    kept = ad.kept_uuids(ad.active_path())
    dset = {r.get("uuid") for r in delta if r.get("uuid")}
    if kept - dset != entry["kept"]:
        if getattr(_ASM_DEMOTE_TL, "batch", False) and getattr(_ASM_DEMOTE_TL, "passed", False):
            return _asm_demote_passed()   # a batch delta the narrowed promptid arm let through: the whole parse it had before the
            #                               narrowing, never the restore road the batch's kept rejection takes below (round one;
            #                               the re-seat steps aside for such a delta so it reaches this check, round two)
        _asm_demote("kept")          # ancestry moved under an old record — the fold cannot
        #                              re-emit history; a full parse can, and so can a restore
        if getattr(_ASM_DEMOTE_TL, "batch", False):
            # A delta the gate passed on the batch link (_batch_delta) is one the leaf descent
            # demoted before batches folded (2026-09-24), and that demotion took the RESTORE road
            # when a document stood. Counted under kept, it takes the same road: the restore reads
            # the document and the tail from its cut, and its chain proof rules on that tail as it
            # did after the descent. A seeded fuzz of synthetic batches met about two hundred such
            # rejections (the leaf leaving a spur, a stray record mid-batch); only 44 of them restore
            # on this road, the ones with a document standing, which the descent had restored. The
            # rest have no document and parse whole on either road.
            _ASM_DEMOTE_TL.reason = "descent"
        return None
    st = entry["st"]
    order = _chrono(ad, kept & dset)
    ad._prepass(order, st)
    new_atoms = list(ad._emit_fold(order, st, rompuuid, postal_index))
    new_atoms += ad._absorbed(ad.qatts[n_qatts:], kept, st, rompuuid, postal_index)
    for u in dset:
        r = ad.by_uuid.get(u) or {}
        if r.get("type") == "assistant" and _text_of(_content(r.get("message"))).strip():
            entry["landed"].add(u)
    entry["atoms"] = entry["atoms"] + new_atoms   # a NEW list — outstanding serves stay stable
    entry["kept"] = kept
    entry["n_qatts"] = len(ad.qatts)
    ogen, obase, ocount = entry["recs"][leaf_key]
    entry["recs"][leaf_key] = (ogen, obase, ocount + len(delta))   # commit LAST: a bail above re-slices the same
    _asm_reseat_src(ad, leaf_key, leaf_recs)      #  delta next visit and the uuid gate demotes it
    ad._src_keys[leaf_key] = entry["recs"][leaf_key]
    _asm_stat("fold")
    if getattr(_ASM_DEMOTE_TL, "passed", False):
        _asm_stat("fold:attachedPass")   # a fold the narrowed promptid arm saved, counted only once the fold serves (round one): a
        #                                  delta it let through that a later gate, the kept check or the re-seat took is no fold
        for tag in getattr(_ASM_DEMOTE_TL, "pass_tags", ()) or ():
            _asm_stat("fold:attachedPass:" + tag)   # the shape the episode rule vouched for (2026-10-05)
    return _asm_serve(entry)


def _asm_reseat_src(ad, path, recs):
    """The fold's re-seat of the adapter's leaf list on `recs`, the reader's list the delta came from. When that list does not
    hold the old one's records (a tail the reader upgraded to the whole file under the same generation, then an append that
    folds: the judges' road over a restored live leaf), the adapter's maps still hold the old records, so the old list joins
    _src_superseded and the heap block keeps weighing it for as long as the adapter lives (asm_pinned_records; review of
    2026-10-01: the gauge said the copy had gone while the memory stayed). The new list is weighed by the reader's entry when
    the cache still holds this very list (the entry the gates read), so a later drop is weighed by real bytes. O(1): two
    identities and one cache lookup, never a walk."""
    old = ad._src.get(path)
    sizes = getattr(ad, "_src_bytes", None)
    if old and not _records_shared(recs, old):
        gone = getattr(ad, "_src_superseded", None)
        if gone is None:
            gone = ad._src_superseded = {}
        gone.setdefault(path, []).append((old[0], len(old), _list_file_bytes((sizes or {}).get(path), len(old))))
    ad._src[path] = recs
    if sizes is not None:
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(path)
            if ent is not None and ent[4] is recs:
                sizes[path] = (_entry_weight(ent), len(recs))


# ═══════════════════════ THE ASSEMBLY CHECKPOINT (T323 stage 4, 2026-09-11) ═══════════════════════
# One document per leaf transcript beside the fold checkpoints (checkpoints/<sha1(realpath)[:20]>.asm.json), written
# from a WHOLE assembly entry when the tree has a compaction boundary: everything before the CUT (the first kept record
# of the turn that holds the last compact_boundary atom, or of the /compact command turn when that boundary is an
# adopted manual one) is recorded as identities and locations, never bodies. A fresh process verifies the document
# (version, path, session, every file's witness, the cut's guard bytes), rebuilds the pre-cut turns from the document
# as LAZY atoms (every scalar the ids and the segmentation read, a _LazyBody where the message was), reads the leaf
# from the cut's byte offset only, parses that tail through a FileAdapter seeded with the pre-cut graph facts and the
# carried emit state, and proves the pre-cut part identical to the whole parse's by a sha1 over its turn ids, segment
# ids and atom uuids. Bodies come back on demand (hydrate). Anything that does not verify is a counted fallback to a
# whole parse; a compaction landing after the document demotes the tail fold to the restore road, whose tail parse from
# the cut takes the compaction in, while the tail is under the churn bound's share (2026-09-24; past the share, and until
# then always, a whole parse, after which the next settle writes a new cut).
_ASM_CKPT_V = 12                      # 2: atom rows carry [offset, len], nt for every atom; 3: the carry holds skill_loads (T333);
#                                       4: a `turns` section over the pre-cut rows (T323 stage 4c: the lazy index)
#                                       5: lazy markers carry pc (assistant prose chars) and mid (postal message ids); a turn row
#                                          carries pcs and hT, a segment row w, mids and hp (T358: the per-cycle walkers read scalars).
#                                          The stored w and hp are atom_has_work's and seg_prompt_atom's verdicts at write time: a
#                                          change to either rule, or to what pc, mid, pcs or hT mean, is a bump of this constant
#                                          (tests/test_asm_index.py pins the rules' source beside it).
#                                       6: the atom rows are stored as pre-serialized JSON STRINGS (T401 (4)): the whole-document
#                                          loads builds strs instead of dicts and the lazy index takes each row's bytes with one
#                                          encode and no dumps (on the largest live document: loads 182 to 136 ms, the index's
#                                          re-encode 98 to 2 ms, the JSON 6.5 percent larger, the gzip 0.7 percent); a row that
#                                          is not a string in a version-6 document is refused (`rows`), never read by a second road;
#                                          the first row is decoded at load and must be a JSON object
# Materialized pre-cut atoms resident across every session (the lazy index's LRU): MemTotal / 32 KiB, never under 500,000
# (about 1.05 million on a 32 GiB machine, 4.2 million on a 128 GiB one); ROMP_ASM_INDEX_CAP sets it outright. At
# 20,000 (2026-09-11, the day the index shipped) the restored turns of many sessions evicted nearly every atom they
# materialized within minutes, and every chat build ran cold: a ceiling under the working set is a thrash, not a saving.
#                                       7: the cut is the boundary before the last SETTLED turn, not only a compaction's (stage one b, 2026-09-15):
#                                          every v6 document is refused once (`version`) at the deploy boot and rewritten at the next settle
#                                       8: a parallel tool batch's branch beside the spine is kept (FileAdapter._batch_head, 2026-09-23): its
#                                          records' stored verdict is "a", not "r", and the pre-cut atom rows hold the results a v7 document
#                                          dropped, so a v7 document restores a history the whole parse no longer builds; refused once
#                                          (`version`) at the deploy boot and rewritten at the next settle, as v7 was
#                                       9: a failed slash command's <local-command-stderr> record is the command's output atom (kind "o"),
#                                          not a human user atom (LOCAL_STDERR_RE, 2026-10-19): a v8 document stores the old human row, its
#                                          own turn and its segment; refused once (`version`) at the deploy boot and rewritten, as v8 was
#                                      10: the CLI's stop record closes the restore window after a compaction (FileAdapter._prepass,
#                                          2026-10-22): a v9 document's pre-cut rows lack a repeated prompt sent after such a stop, and its
#                                          carry can hold the window still open; refused once (`version`) at the deploy boot and rewritten
#                                      11: a stop recorded before the parse's first atom makes no idle atom (synthesize_idle, 2026-10-25): a
#                                          v10 document's first pre-cut turn can hold the create-time stop's idle atom ahead of the first
#                                          prompt, with no trigger and the create time as its t and its first segment's; refused once
#                                          (`version`) at the deploy boot and rewritten, as v10 was
#                                      12: a salvaged reply is deduped against its own turn and the turn on each side only (synthesize_orphans,
#                                          2026-10-25): a v11 document's pre-cut turns lack a reply only a far turn's text matched; refused
#                                          once (`version`) at the deploy boot and rewritten, as v11 was
_MAT_CAP = _env_or("ROMP_ASM_INDEX_CAP", max(500_000, _machine_memory_bytes() // (32 * 1024)))
_MAT_LRU = collections.OrderedDict()  # (id(LazyAtoms), row) → (weakref.ref(LazyAtoms), row): eviction drops the memo, never a field
#                                       in place. The list is held WEAKLY (measured 2026-09-15): a strong reference here kept every
#                                       superseded generation's atoms, and through them its LazyIndex, rows and document, resident until
#                                       they aged past the cap (every restore mints a new index, so a busy day's restores sat the LRU at its
#                                       cap holding mostly dead generations, 1.3 to 2.1 KB an atom as tracemalloc measured over real indexes,
#                                       and live atoms evicted by stale ones were each rebuilt on their next read). Now a
#                                       dropped tree's entries die with it and expire at the old end (_mat_trim), and a dropped assembly
#                                       entry releases its index's at once (LazyIndex.release). No weakref callback: one fires at any
#                                       decref, under this lock included, and the lock is not reentrant; dead entries expire lazily.
_MAT_LOCK = threading.Lock()
_ASM_INDEX_STATS = {"materialized": 0, "materializedBy": {}, "materializedByStage": {}, "resident": 0, "evictions": 0,
                    "restoredTurns": 0, "rowDecodes": 0, "released": 0, "expired": 0}
#                                                              materializedByStage: the same builds under "<stage>:<caller>" (T401 (5a):
#                                                              a build from an unmarked thread reads "none:<caller>", the read boot's face)
#                                                              released: entries LazyIndex.release popped for a dropped assembly entry;
#                                                              expired: entries of a collected list dropped, at the cap or when a live list registers under the id the dead one held; no slot touched
_PRE_TURN_KEYS = ("pre", "uuids", "lastT", "maxT", "lastModel", "tools", "segs", "pcs", "hT")   # a pre-turn's fields beyond a plain turn's


class LazyIndexError(RuntimeError):
    """A pre-cut row the index cannot decode: names the session and the row, so the fallback is a loud one."""


class _Unmaterialized:
    """The placeholder a LazyAtoms slot holds before its atom is built. Not JSON-serializable and not a dict: a serializer
    or a copier reaching a pre-cut turn's atoms through the list's storage raises instead of shipping placeholders."""
    __slots__ = ()

    def __repr__(self):
        return "<unmaterialized atom>"


_UNMAT = _Unmaterialized()
_USER_FACTS_CAP = 8192                # light facts cached PER INDEX for the interrupt-marks tally: user rows only, the cache cleared whole
#                                       past this (about 447 bytes a row measured; the parse cache holds up to 256 indexes, so the theoretical
#                                       worst is about 937 MB); the gauge asmIndex.userFacts sums the live indexes' caches at report time
_LIVE_INDEXES = weakref.WeakSet()     # every LazyIndex alive (the parse cache's and any caller's): what the userFacts gauge sums; a dropped
#                                       index leaves the set with its cache, so the gauge falls with it (round three medium)
_IR_TRUE, _IR_FALSE = {"ir": True}, {"ir": False}   # the light facts' lazy header, shared and never mutated (is_interrupt_record reads ir)


def _materialize_caller():
    """The consumer that reached for a pre-cut atom: the first frame above the index's own methods."""
    f = sys._getframe(2)
    for _ in range(8):
        if f is None:
            break
        name = f.f_code.co_name
        if name not in ("__getitem__", "__iter__", "__reversed__", "__contains__", "__eq__", "index", "count", "copy", "__add__",
                        "__radd__", "__reduce_ex__", "_at", "materialize", "_materialize_caller", "<genexpr>", "<listcomp>", "get"):
            return name
        f = f.f_back
    return "?"


def _mat_register(la, i):
    """Under _MAT_LOCK: la's slot i joins the LRU at its young end under la's OWN weak reference. An entry already under the
    key that is not la's (a collected list whose id this one reuses: ids recycle the moment a list is freed) is popped first
    and counted `expired`, and popped rather than assigned over, since an assignment to a standing key keeps the key's old
    position and the fresh entry would sit at the old end, first to go."""
    key = (id(la), i)
    old = _MAT_LRU.pop(key, None)
    if old is not None and old[0]() is not la:
        _ASM_INDEX_STATS["expired"] += 1
    _MAT_LRU[key] = (weakref.ref(la), i)


def _mat_count(la, d):
    """Under _MAT_LOCK: the index behind `la` holds `d` more (or fewer) materialized atoms that the LRU registers, the live
    count the heap block's builtAtoms sums over the live indexes: a build adds one (LazyAtoms._at), an eviction takes one off
    (_mat_trim) and a release takes off what it popped (LazyIndex.release). A stand-in index without the counter is skipped."""
    ix = getattr(la, "_index", None)
    n = getattr(ix, "_built", None)
    if n is not None:
        ix._built = max(0, n + d)


def _mat_trim():
    """Under _MAT_LOCK: the LRU back within _MAT_CAP from its old end. A live entry is evicted as ever (its slot back to the
    placeholder, counted `evictions`); an entry whose list has been collected is dropped and counted `expired`, and no slot is
    touched for it (the list is gone, and its id may by now be another live list's, whose slot this entry never described).
    The cap bounds len(_MAT_LRU) with the dead entries included. A dropped list's entries are dead where they sit: a list
    dropped recently leaves dead entries YOUNGER than older live ones, so a live entry ahead of them is evicted first and the
    dead ones clear only as they reach the old end; release() is what makes a dropped index's entries leave at once, and the
    `expired` count says how many dead ones the trim met instead."""
    while len(_MAT_LRU) > _MAT_CAP:
        _, (ref, j) = _MAT_LRU.popitem(last=False)
        lz = ref()
        if lz is None:
            _ASM_INDEX_STATS["expired"] += 1
        else:
            list.__setitem__(lz, j, _UNMAT)
            _mat_count(lz, -1)
            _ASM_INDEX_STATS["evictions"] += 1
            # #1735: no gc-freeze note here. A materialized atom is decoded json (a dict), acyclic: it dies by
            # reference counting when the slot drops it, frozen or not, so an unfreeze reclaim would collect nothing.


class LazyIndex:
    """One restored session's pre-cut rows (T323 stage 4c): the document's atom rows kept as BYTES, decoded one at a time
    when a consumer reaches for an atom, through a process-wide LRU (_MAT_CAP). The record rows (identity, time, file,
    parent) stay decoded: they are small and every materialization reads one. The LRU holds the index's atom lists weakly
    and the index knows the lists it minted (_minted), so a dropped assembly entry can give the memo back at once
    (release) and a tree nobody holds takes nothing to the LRU but entries that expire (measured 2026-09-15, see _MAT_LRU)."""

    def __init__(self, doc, rompuuid, leaf_path, cache_key=None):
        self.rompuuid = str(rompuuid)
        self.leaf = str(leaf_path)
        self._cache_key = cache_key                        # the assembly entry this index serves: dropped when a row fails to build
        self._rows_noted = False                          # the document noted `rows` once, at the first row that fails to build
        self._minted = []                                 # weakref.ref to every LazyAtoms minted over this index (LazyAtoms.__init__ adds
        #                                                   under _MAT_LOCK; release() walks and prunes them): plain refs, no WeakSet, since
        #                                                   a LazyAtoms is unhashable and a ref callback may fire under the lock
        self.rowb = [r.encode("utf-8") for r in doc["atoms"]]   # v6: the document's rows are JSON strings already (T401 (4))
        self.row_bytes = sum(map(len, self.rowb))         # the rows' encoded bytes, summed here and kept by the one writer of rowb,
        #                                                   the restore's summary patch before any reader holds the index
        #                                                   (_asm_patch_pre_cut_summaries), so the heap block's estimate reads a
        #                                                   number, never the rows (restored_index_estimate)
        self.records = doc["records"]
        self.fsids = list(doc.get("fsids") or [])
        files = doc.get("files")
        self.source_files = None if files is None else _source_files(files)   # the held view's own document, not the last
        #                                                                         restore's (_restore_prefix_atoms, 2026-09-17)
        self._user_facts = {}                             # the interrupt-marks tally's light facts by row (user_facts), bounded by _USER_FACTS_CAP
        self._built = 0                                   # the atoms of this index's lists the LRU registers, kept under _MAT_LOCK (_mat_count):
        #                                                   the heap block's live atoms, where the LRU's length also counts the dead slots of
        #                                                   collected lists until they expire
        with _MAT_LOCK:                                   # the add under the lock the userFacts gauge sums under: an add beside the sum raised
            _LIVE_INDEXES.add(self)                       #  "set changed size during iteration" and /perf answered 500 (1597 low 1)

    def _row(self, k):
        """The one row decode every accessor uses (build, text_flags, uuid_of, user_facts, tool_ids; uuids through uuid_of): a
        row that does not decode to a JSON object refuses the DOCUMENT to the truth (1610 round three, mediums 1 and 2):
        noted `rows` once (counted, said once per leaf, the document unlinked by the note), the leaf's assembly entry dropped
        so the next parse is the whole parse, and this read alone raises LazyIndexError. A checkpoint is a cache: every road
        that decodes a row can find it corrupt, and none of them may crash its reader twice or answer from one row fewer."""
        with _MAT_LOCK:
            _ASM_INDEX_STATS["rowDecodes"] += 1
        try:
            row = json.loads(self.rowb[k])
            if not isinstance(row, dict):
                raise ValueError("row is not a JSON object")
            return row
        except (IndexError, ValueError) as e:
            self._refuse_rows("row %d: %s" % (k, e))
            raise LazyIndexError("session %s row %d: %s" % (self.rompuuid[:8], k, e)) from e

    def build(self, k):
        return self._build_row(self._row(k))

    def _build_row(self, row):
        """The atom a decoded row restores to, a pure function of the row: build after its decode, and tool_ids for the
        rows that carry no lazy marker."""
        if row.get("syn"):                                # a synthesized atom (idle, a salvaged reply): its message inline, if any
            a = dict(row.get("s") or {})
            if "m" in row:
                a.update(message=row["m"])                # a WRITE of the synthesized atom's message (no body read: the audit's regex)
            return a
        a = _restore_prefix_atoms([row], self.rompuuid, self.records, self.fsids, self.source_files)[0]
        a.pop("_seq", None)                               # the read-order tiebreak: the section fixed the order (parse_session pops it too)
        return a

    def tool_ids(self, k):
        """Row k's tool_use ids and tool_result tool_use_ids (atom_tool_ids: falsy ones dropped) from one decode. A row with a
        lazy marker answers from the marker's tu and tr scalars and builds nothing: the built atom carries that marker whole
        (_restore_prefix_atoms), so it would answer the same. A row without one (an inline body, a synthesized atom, a
        boundary) is built for this read and the atom dropped, never stored in a slot or registered in the LRU. A broken row
        refuses the document and raises here, as a build does (_row)."""
        row = self._row(k)
        lz = row.get("lz")
        return atom_tool_ids({"lazy": lz} if lz is not None else self._build_row(row))

    def _refuse_rows(self, detail):
        """A row that passed the load's shape check but does not decode to an object: the document is noted `rows` (counted once
        per index, said once per leaf), its assembly entry is dropped so the next parse of the leaf is the whole parse, and the
        document is removed by the note (the settle rewrites it). The caller raises for THIS build; nothing lazier is possible
        once a consumer holds the row."""
        with _ASM_CKPT_LOCK:                              # compare-and-set under the note's own lock (1610 low 4): two threads
            if self._rows_noted:                          #  finding the row at once note it once
                return
            self._rows_noted = True
        _asm_ckpt_note(self.leaf, "rows", detail)
        if self._cache_key is not None:
            with _ASM_LOCK:
                old = _ASM_CACHE.pop(self._cache_key, None)
            _asm_release(old)                             # the dropped entry's index (this one, unless superseded) gives its memo back

    def release(self):
        """Every LRU entry of the lists this index minted leaves the LRU and its slot goes back to the placeholder, under
        _MAT_LOCK, counted `released`: the prompt half of the LRU's weak ownership (measured 2026-09-15, see _MAT_LRU), called
        for the index of an assembly entry that is dropped or replaced (_asm_release), the moment the kernel stops serving it,
        rather than at the cap, a million entries later. The per-slot atom a consumer already holds is a value and is never
        mutated. A tree that outlives its entry (a parse cache slot, a build in flight) reads a released slot as it reads an
        evicted one: rebuilt through this index, and registered again; that is allowed and needs no retired flag, since under
        weak ownership the LRU then holds nothing beyond that tree's own lifetime, and its entries expire when it goes. The
        walk is over this index's own lists' slots, never the LRU (measured 2026-09-15, a lab process: 20,000 rows with 200
        built, 1.3 ms; 200,000 rows with 2,000 built, 9.8 ms; 200,000 rows with 20,000 built, 26.5 ms), so a release costs
        the dropped index its row count in list reads, once, where the cap paid a million-entry residency."""
        with _MAT_LOCK:
            n, live = 0, []
            for ref in self._minted:
                la = ref()
                if la is None:
                    continue                              # a collected list: its entries, if any stand, expire at the old end
                live.append(ref)
                for i, a in enumerate(list.__iter__(la)):
                    if a is _UNMAT:
                        continue
                    if _MAT_LRU.pop((id(la), i), None) is not None:   # a built slot's entry is its own (a stale key under a reused
                        n += 1                                        #  id was popped at the build), so the count is this list's
                    list.__setitem__(la, i, _UNMAT)
            self._minted[:] = live                        # in place: a constructor holding this list appends to the one list
            self._built = max(0, self._built - n)
            _ASM_INDEX_STATS["released"] += n
            _ASM_INDEX_STATS["resident"] = len(_MAT_LRU)
        # #1735: no gc-freeze note here, as in _mat_trim. The slots reset to _UNMAT release materialized atoms, which
        # are decoded json (dicts), acyclic: they die by reference counting, so an unfreeze reclaim would collect nothing.
        return n

    def user_facts(self, k):
        """The fields the interrupt-marks tally reads from a USER row, from one decode and no atom build (T401 (3) target 3):
        type, uuid, t, author (the recorded scalars applied over the record row's fields, exactly as the build applies them)
        and lazy.ir (the interrupt flag), as a light dict that is never stored in the slot or the LRU; None for a row that
        is not a user record. What is cached: the USER rows' facts, per index in self._user_facts, cleared whole past
        _USER_FACTS_CAP; a non-user row is re-decoded on every tally (a broken row refuses the document, see _row), so a
        second tally over the same index decodes every non-user row again. That second tally is rare: the
        kernel's identity memo answers a repeated tally over the same parse before this method runs, and a changed
        transcript restores a new index. The gauge asmIndex.userFacts is the sum of the live indexes' caches, taken at
        report time (asm_index_stats), so this hot path takes no lock but the row-decode counter's."""
        cache = self._user_facts
        f = cache.get(k)
        if f is not None:
            return f
        row = self._row(k)                             # a broken row refuses the document and raises here: the tally never answers
        ri = row.get("r"); sc = row.get("s") or {}     #  from one row fewer than the whole parse (1610 round three, medium 2)
        tname = {"u": "user", "a": "assistant", "s": "system"}
        typ = sc.get("type") or (tname.get(self.records[ri][2], "user") if ri is not None else None)
        if typ != "user":
            return None                                # not cached: only USER rows are kept (the bound below is over them)
        rr = self.records[ri] if ri is not None else None
        lz = row.get("lz")
        facts = {"type": "user", "uuid": rr[0] if rr else None, "t": rr[5] if rr else 0,
                 "lazy": _IR_TRUE if (lz or {}).get("ir") else _IR_FALSE, "_light": k,
                 "_nt": (bool(lz.get("nt")) if lz is not None else None)}   # has text, from the lazy header; None when unknown (5b)
        for f in ("type", "uuid", "t", "author"):      # the recorded scalars over the record row's fields, exactly as the build
            if f in sc:                                #  applies them (a repaired timestamp lives in the scalars, not the record)
                facts[f] = sc[f]
        if lz is None and "m" in row:                  # an inline body with no lazy header: the interrupt flag is in the TEXT, which
            facts["_build"] = True                     #  only the audited body readers may read, so this rare row is the build's
        if len(cache) >= _USER_FACTS_CAP:              # bounded per index: never the whole corpus (round two, low 1)
            cache.clear()
        cache[k] = facts
        return facts

    def uuid_of(self, k):
        """A row's uuid without building its atom (the record row's, else the synthesized scalars')."""
        row = self._row(k)
        ri = row.get("r")
        if ri is not None:
            return self.records[ri][0]
        return (row.get("s") or {}).get("uuid")

    def text_flags(self, k):
        """(type, has text, text hash) for a row without building its atom, one decode: what an orphan marker's dedup reads.
        The hash is lz.h (the first eight hex of sha1 over the text) for a lazy row, the same digest over an inline message
        for a synthesized or inline row; None without text."""
        row = self._row(k)
        ri = row.get("r")
        lz = row.get("lz")
        tname = {"u": "user", "a": "assistant", "s": "system"}
        typ = (row.get("s") or {}).get("type") or (tname.get(self.records[ri][2], "user") if ri is not None else None)
        if lz is not None:
            return typ, bool(lz.get("nt")), (lz.get("h") if lz.get("nt") else None)
        if "m" in row:
            txt = _text_of(_content(row["m"])).strip()
            return typ, bool(txt), (hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:8] if txt else None)
        return typ, False, None


class LazyAtoms(list):
    """A pre-cut turn's atoms (T323 stage 4c): a list whose slots hold placeholders until a consumer reaches for an
    atom, which the index then builds (a lazy atom, its body still on disk, hydrate() as before). Every read path a
    list offers goes through the build (indexing, slicing, iteration, membership, equality, copies, concatenation,
    pickling), so a consumer sees plain atom dicts; the placeholders reach only a serializer or copier that reads the
    list's storage directly (json's encoder, refused at __iter__ while a slot is unbuilt), and those raise. Materialized atoms live in a process-wide LRU
    (_MAT_CAP): eviction puts the placeholder back in the slot, the consumer's own reference stays whole. The LRU holds
    this list by a weak reference (see _MAT_LRU), so the list, its index and the document behind it live exactly as long
    as their consumers do; the index's release() empties the list's entries early when its assembly entry is dropped."""

    def __init__(self, index, rows):
        list.__init__(self, [_UNMAT] * len(rows))
        self._index = index
        self._rows = list(rows)
        with _MAT_LOCK:                                   # release() walks and prunes the list under this lock, in place:
            minted = getattr(index, "_minted", None)      #  the read and the append sit under it too, so a list minted while
            if minted is not None:                        #  a release runs is never appended to a list the release replaced
                minted.append(weakref.ref(self))          #  (a stand-in index in tests may carry no list of its own)

    # ── the build ──
    def _at(self, i):
        a = list.__getitem__(self, i)
        if a is not _UNMAT:
            with _MAT_LOCK:
                key = (id(self), i)
                ent = _MAT_LRU.get(key)
                if ent is not None and ent[0]() is self:  # this list's own entry: the LRU touch
                    _MAT_LRU.move_to_end(key)
                elif list.__getitem__(self, i) is not _UNMAT:   # built and not registered (a dead entry under a reused id, or none):
                    _mat_register(self, i)                      #  registered now; an eviction or release between the read above and
                    _mat_count(self, 1)                         #  this lock left the slot unbuilt, and then `a` is the caller's value
                    _mat_trim()
            return a
        a = self._index.build(self._rows[i])
        by = _materialize_caller()
        with _MAT_LOCK:
            cur = list.__getitem__(self, i)
            if cur is not _UNMAT:                         # another thread built it first
                return cur
            list.__setitem__(self, i, a)
            _mat_register(self, i)
            _mat_count(self, 1)
            _ASM_INDEX_STATS["materialized"] += 1
            _ASM_INDEX_STATS["materializedBy"][by] = _ASM_INDEX_STATS["materializedBy"].get(by, 0) + 1
            bs = "%s:%s" % (_read_stage() or "none", by)      # the calling thread's stage mark beside the caller (T401 (5a))
            _ASM_INDEX_STATS["materializedByStage"][bs] = _ASM_INDEX_STATS["materializedByStage"].get(bs, 0) + 1
            _mat_trim()
            _ASM_INDEX_STATS["resident"] = len(_MAT_LRU)
        return a

    def uuids(self):
        """The atoms' uuids without building them."""
        return [self._index.uuid_of(r) for r in self._rows]

    def user_facts(self):
        """[(slot, atom-or-facts)] for the USER rows in order, building nothing: a slot already built yields its atom, an
        unbuilt one the index's light facts (T401 (3) target 3: the interrupt-marks tally used to build every atom of the
        transcript through __iter__)."""
        out = []
        for i, r in enumerate(self._rows):
            a = list.__getitem__(self, i)
            if a is not _UNMAT:
                if a.get("type") == "user":
                    out.append((i, a))
                continue
            f = self._index.user_facts(r) if hasattr(self._index, "user_facts") else None
            if f is None and not hasattr(self._index, "user_facts"):
                a = self._at(i)                            # an index without the accessor: the build, as before
                if a.get("type") == "user":
                    out.append((i, a))
            elif f is not None:
                if f.get("_build"):
                    out.append((i, self._at(i)))           # an inline-body row: its interrupt flag is in the text, the build reads it
                else:
                    out.append((i, f))
        return out

    def rows(self):
        return list(self._rows)

    def tool_ids(self):
        """Every tool_use id and tool_result tool_use_id the atoms carry (falsy ones dropped), as a frozenset kept on the list
        after the first call: the rows never change (release() resets slots, not rows), and the list is shared by every parse
        its assembly entry serves (_asm_serve), so the set outlives the call. The reader is the settle gate's background hold
        (the judge's _awaiting_bg_hold), which looks for the turn naming a running launch without walking the atoms. A built
        slot is read where it sits (list.__getitem__: no lock, no LRU touch, as user_facts reads it; a hydrated atom carries
        the ids its marker carried); an unbuilt one answers from one row decode (LazyIndex.tool_ids). Nothing is stored in a
        slot or registered, so neither the LRU nor asmIndex.materialized sees this read. Two threads computing the set at once
        store equal sets."""
        got = self.__dict__.get("_tool_ids")
        if got is None:
            out = set()
            for i, r in enumerate(self._rows):
                a = list.__getitem__(self, i)
                if a is _UNMAT:
                    out |= self._index.tool_ids(r)        # a turn's list is minted over a LazyIndex (_pre_turns_of); the one
                    #                                       stand-in index in tests (build and uuid_of, no tool_ids) serves
                    #                                       lane tests, which never ask a list for its tool ids
                else:
                    out |= atom_tool_ids(a)
            got = self._tool_ids = frozenset(out)
        return got

    # ── the list surface ──
    def _unbuilt(self):
        return any(a is _UNMAT for a in list.__iter__(self))
    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(len(self))
            if step == 1:                                 # a contiguous slice (a segment's atoms) stays lazy: a view over this list
                return _LazyView(self, start, max(start, stop))
            return [self._at(j) for j in range(start, stop, step)]
        n = len(self)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError("LazyAtoms index out of range")
        return self._at(i)

    def __iter__(self):
        # json's encoder (C or Python) reaches a list SUBCLASS through iteration, so a dump of a tree would build every atom
        # and ship it: refused here, loudly, as the design asks (plain_tree is the dump's road)
        f = sys._getframe(1)
        if f is not None and f.f_code.co_name in ("iterencode", "encode", "_iterencode", "_iterencode_list", "_iterencode_dict") \
                and self._unbuilt():
            raise TypeError("a pre-cut turn's atoms are not JSON-serializable before they are built: dump em.plain_tree(session)")
        for j in range(len(self)):
            yield self._at(j)

    def __reversed__(self):
        for j in range(len(self) - 1, -1, -1):
            yield self._at(j)

    def __contains__(self, x):
        return any(a is x or a == x for a in self)

    def __eq__(self, other):
        return list(self) == list(other) if isinstance(other, list) else NotImplemented

    def __ne__(self, other):
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    __hash__ = None

    def index(self, x, *args):
        return list(self).index(x, *args)

    def count(self, x):
        return list(self).count(x)

    def copy(self):
        return list(self)

    def __add__(self, other):
        return list(self) + list(other)

    def __radd__(self, other):
        return list(other) + list(self)

    def __mul__(self, n):
        return list(self) * n

    def __reduce_ex__(self, proto):
        return (list, (list(self),))                     # a copy or a pickle is a plain list of built atoms

    def __repr__(self):
        return "LazyAtoms(%d rows, %d built)" % (len(self), sum(1 for a in list.__iter__(self) if a is not _UNMAT))

    def sort(self, *a, **k):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def append(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def extend(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def insert(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def pop(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def remove(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def __setitem__(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def __delitem__(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")


class _LazyView(LazyAtoms):
    """A contiguous slice of a LazyAtoms (a segment's atoms, T358) that builds nothing until read: a first read of a slot
    goes to the parent's slot (a build there is the parent's build under the parent's LRU key), and the view KEEPS what it
    read in its own slot for its lifetime, as the plain-list slice it replaces did, so a second reader of the same segment
    (the anchors after the ids, the jump after the anchors) touches no lock: with two builders at once (the pusher's cycle
    and a client's ready frame both building the same chat) every locked access convoyed on the index lock at about one
    atom per scheduler switch, and a restored session's first frame took 20 s on a four-core runner (T358 CI, 2026-09-12).
    A view is per build and short-lived, so what it holds is bounded by the build; an eviction in the parent is not seen by
    a slot the view already read, so while a build (or a planner pass whose returned tasks hold its views) is alive
    its read set pins atoms past the index cap by that one build's worth, as the plain-list slices did. json's encoder
    is refused while the parent's slots are unbuilt; slicing a view is a
    view of the parent."""
    def __init__(self, parent, start, stop):
        list.__init__(self, [_UNMAT] * (stop - start))
        self._parent, self._off = parent, start
        self._index, self._rows = parent._index, parent._rows[start:stop]
    def _at(self, i):
        a = list.__getitem__(self, i)
        if a is not _UNMAT:
            return a
        a = self._parent._at(self._off + i)
        list.__setitem__(self, i, a)
        return a
    def _unbuilt(self):
        return any(list.__getitem__(self._parent, self._off + j) is _UNMAT for j in range(len(self)))
    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(len(self))
            if step == 1:
                return _LazyView(self._parent, self._off + start, self._off + max(start, stop))
            return [self._at(j) for j in range(start, stop, step)]
        return LazyAtoms.__getitem__(self, i)


class PreTurn(dict):
    """A restored pre-cut turn: a plain turn's fields (id, trigger, t, end, ended, atoms) with `atoms` a LazyAtoms, plus
    the turn-level scalars the kernel's walkers read instead of the atoms (_PRE_TURN_KEYS: uuids, lastT, maxT, lastModel,
    tools, segs). A dict subclass only so a walker can tell one apart; `trigger` is {"uuid": the opener's}, as the
    segmentation shapes it, from the uuids alone."""


def _pre_turns_of(doc, index):
    """The pre-cut turns of a version-4 document as PreTurns over the index (no atom built)."""
    out = []
    for td in doc["turns"]:
        at = td.get("triggerAt")
        pt = PreTurn(id=td["id"], trigger=({"uuid": td["uuids"][at]} if at is not None else None), t=td["t"], end=td["end"],
                     ended=bool(td["ended"]), atoms=LazyAtoms(index, td["atoms"]),
                     pre=True, uuids=list(td["uuids"]), lastT=td.get("lastT"), maxT=td.get("maxT"), lastModel=td.get("lastModel"),
                     tools=[tuple(x) for x in td.get("tools") or []], segs=[list(s) for s in td["segs"]],
                     pcs={u: n for u, n in td.get("pcs") or []}, hT=td.get("hT"))
        out.append(pt)
    return out


def _summary_digest(summary):
    """A boundary atom's summary as the document's `bnd` map records it: a short sha1, None for no summary."""
    return None if summary is None else hashlib.sha1(summary.encode("utf-8", "replace")).hexdigest()[:16]


def _asm_boundary_rows(doc, index, prefix):
    """Each pre-cut boundary atom's row in the document and its frozen summary's digest ({uuid: (row index, digest)}, the
    index of the turns section's rows or of the atoms-only prefix): the writer's `bnd` map, or, for a document written
    before it, found through the turns section's per-turn uuids (which cover every row) or the prefix atoms, keyed by the
    record rows that are boundaries, with no digest (the row is decoded to compare)."""
    bnd = doc.get("bnd")
    if bnd is not None:
        return {u: (int(v[0]), v[1]) for u, v in bnd.items()}
    want = {r[0] for r in doc["records"] if r[3] == "compact_boundary"}
    out = {}
    if index is not None:
        for td in doc.get("turns") or ():
            for j, u in enumerate(td["uuids"]):
                if u in want:
                    out[u] = (td["atoms"][j], _UNKNOWN_DIGEST)
    else:
        for k, a in enumerate(prefix):
            if a.get("uuid") in want:
                out[a["uuid"]] = (k, _UNKNOWN_DIGEST)
    return out


_UNKNOWN_DIGEST = object()


def _asm_patch_pre_cut_summaries(desired, rows_of, index, prefix):
    """Hold each pre-cut boundary atom in `desired` ({uuid: summary}) to the summary the pre-pass left it, as a cold parse
    emits it (_asm_restore_inner). `rows_of` maps each to its row and its frozen summary's digest (_asm_boundary_rows): an
    atom whose digest matches is left without decoding its row. The rows of a turns-section document
    are patched in the restore's own index (its row bytes, a copy of the document's, which no reader holds yet); an
    atoms-only document's prefix atoms are fresh dicts. Returns how many atoms changed."""
    patched = 0
    for u, want in desired.items():
        k, digest = rows_of.get(u, (None, None))
        if k is None or (digest is not _UNKNOWN_DIGEST and digest == _summary_digest(want)):
            continue                                       # no atom, or the frozen summary is the one the pass leaves: no decode
        if index is not None:
            row = index._row(k)
            sc = dict(row.get("s") or {})
            if sc.get("summary") == want:
                continue
            sc["summary"] = want
            row["s"] = sc
            b = json.dumps(row, separators=(",", ":")).encode("utf-8")
            index.row_bytes += len(b) - len(index.rowb[k])
            index.rowb[k] = b
            patched += 1
        elif 0 <= k < len(prefix) and prefix[k].get("uuid") == u and prefix[k].get("summary") != want:
            prefix[k]["summary"] = want
            patched += 1
    return patched


def _tree_identity_of_doc(turns_doc, identity):
    """sha1 over a `turns` section's turn ids, segment ids and atom uuids, and the atoms' own identity digest (the whole
    parse's, verified at write time): the proof a restore reads from the section alone, no atom built."""
    h = hashlib.sha1()
    h.update((identity or "").encode()); h.update(b"#")
    for td in turns_doc:
        h.update(td["id"].encode()); h.update(b"|")
        for sg in td["segs"]:
            h.update(sg[0].encode()); h.update(b",")
        for u in td["uuids"]:
            h.update((u or "").encode()); h.update(b";")
        h.update(",".join(str(k) for k in td["atoms"]).encode()); h.update(b"/")   # the rows each turn holds (a permuted section differs)
    return h.hexdigest()


def plain_tree(session):
    """A plain copy of a parsed session for a comparison or a dump: every turn a plain dict with the six turn fields, its
    atoms built into plain dicts (bodies as they are: hydrate first for the text). The pre-turn fields are left out, so a
    restored tree and the whole parse's compare equal when they are."""
    turns = []
    for t in session.get("turns") or []:
        pt = {k: v for k, v in t.items() if k != "atoms" and k not in _PRE_TURN_KEYS}
        if isinstance(pt.get("trigger"), dict):
            pt["trigger"] = dict(pt["trigger"])
        pt["atoms"] = [dict(a) for a in t["atoms"]]
        turns.append(pt)
    out = {k: v for k, v in session.items() if k != "turns"}
    out["turns"] = turns
    return out


def asm_index_stats():
    """The lazy index for /perf. `materialized` (atoms built from document rows), `restoredTurns` (pre-cut turns restored from
    documents) and `rowDecodes` (document rows decoded, a build's or a light read's) are running counters since boot, never
    what is held, and so are `evictions`, `released` and `expired`. The gauges are `resident` (the LRU's slots, an upper bound
    on the live atoms), `cap` and `userFacts`; what the slots and the live indexes weigh is the heap block's builtAtoms and
    restoredIndexes."""
    with _MAT_LOCK:
        return {"materialized": _ASM_INDEX_STATS["materialized"], "materializedBy": dict(_ASM_INDEX_STATS["materializedBy"]),
                "materializedByStage": dict(_ASM_INDEX_STATS["materializedByStage"]),
                "resident": len(_MAT_LRU), "evictions": _ASM_INDEX_STATS["evictions"], "cap": _MAT_CAP,
                "released": _ASM_INDEX_STATS["released"], "expired": _ASM_INDEX_STATS["expired"],
                "restoredTurns": _ASM_INDEX_STATS["restoredTurns"], "rowDecodes": _ASM_INDEX_STATS["rowDecodes"],
                "userFacts": sum(len(ix._user_facts) for ix in list(_LIVE_INDEXES))}   # a GAUGE: the light facts resident across the
#                                                                                       live indexes (a dropped index takes its cache with it)
#   resident is len(_MAT_LRU) with the entries of collected lists included until they expire at the cap (_mat_trim) or are released;
#   released and expired are the counters those two roads bump (the LRU's weak ownership, measured 2026-09-15)
MAT_ATOM_BYTES = 1700                 # what one materialized pre-cut atom weighs: the middle of the 1.3 to 2.1 KB tracemalloc measured
#                                       per atom over real indexes (2026-09-15, see _MAT_LRU); a synthesized atom with a short message,
#                                       its LRU entry included, read 1,531 (Python 3.12, 2026-10-01). An atom as built, with no body:
#                                       hydrate() writes a body into the atom in place, and that body is not in this figure
MAT_SLOT_BYTES = 220                  # what one LRU entry weighs once its list has been collected (a dead slot, until it expires at the
#                                       old end): its key and value tuples, the id's int and its share of the ordered dict's tables
#                                       (218.6 by tracemalloc over 20,000 entries of 50-atom lists collected without a release, 234.1
#                                       with 500-atom lists, whose slot numbers past 256 are ints of their own; Python 3.12, 2026-10-01)
INDEX_ROW_OVERHEAD_BYTES = 42         # beyond its encoded bytes, what one atom row costs a LazyIndex: the bytes object's header and its
#                                       list slot (41.7 by tracemalloc over 20,000 rows of a synthetic document, Python 3.12, 2026-10-01)
INDEX_RECORD_ROW_BYTES = 430          # one decoded record row of a document, a list of ten (two uuids, a seq, a stamp, small codes) and
#                                       its slot in the records list (429.4 by tracemalloc over 20,000 rows, the same probe); structural,
#                                       so a synthetic document measures it as a real one would
USER_FACT_BYTES = 447                 # one cached light fact (about 447 bytes a row measured, _USER_FACTS_CAP)


def built_atoms_estimate() -> dict:
    """The materialized pre-cut atoms for /perf's heap block: `slots` (the LRU's length, the same read as asmIndex.resident),
    `live` (the slots whose list is alive: the atoms the live indexes count as built, _mat_count), `overSlots` (how far `live`
    runs past `slots`, 0 unless a count drifted) and `bytes`: the live atoms, at most one per slot, x `atomBytes`
    (MAT_ATOM_BYTES) plus the other slots x `slotBytes` (MAT_SLOT_BYTES), since a collected list's slots stay in the LRU,
    holding only their entries, until they expire. An estimate of the atoms as built: a hydrated atom also holds its body,
    which this leaves out (heap.hydrated counts the bodies only while its memo holds them). A list collected while its index
    lives keeps its slots in `live` until the index goes, so `live` is the indexes' own sum, never cut to `slots` (a cut
    would hide the counter's every miscount whenever the LRU held little else), while `bytes` never prices more atoms than
    the LRU has slots (review of 2026-10-01: an over-count priced atoms nothing held); such a drift shows as `overSlots`
    rather than as bytes. The dead slots are what `slots` leaves over `live`, never fewer than none. O(live indexes): one
    counter per index."""
    with _MAT_LOCK:                                   # the WeakSet is read under the lock its adds take (the userFacts gauge's rule)
        n = len(_MAT_LRU)
        live = sum(int(getattr(ix, "_built", 0) or 0) for ix in list(_LIVE_INDEXES))
    atoms = min(live, n)                              # the LRU registers every built atom, so it cannot hold more than its slots
    return {"slots": n, "live": live, "overSlots": max(0, live - n), "atomBytes": MAT_ATOM_BYTES, "slotBytes": MAT_SLOT_BYTES,
            "bytes": atoms * MAT_ATOM_BYTES + max(0, n - live) * MAT_SLOT_BYTES}


def restored_index_estimate() -> dict:
    """The live lazy indexes for /perf's heap block (a restored session's pre-cut rows, T323 stage 4c): `indexes`, `rows` and
    `rowBytes` (the atom rows' encoded bytes, which each index summed once when it was built), `records` (the document's record
    rows, counted once per list: indexes restored from one memoized document share its list) and `userFacts` (the light facts
    cached), and `bytes`: rowBytes plus rows x INDEX_ROW_OVERHEAD_BYTES plus records x INDEX_RECORD_ROW_BYTES plus userFacts x
    USER_FACT_BYTES, an estimate. The record rows are the decoded document's own, so while the assembly document memo holds
    that document (asmCheckpoint.asmDocMemo) the two gauges both count them. O(live indexes): lengths and the stored sum,
    never a walk over rows."""
    with _MAT_LOCK:                                   # the WeakSet is read under the lock its adds take (the userFacts gauge's rule)
        live = list(_LIVE_INDEXES)
    out = {"indexes": len(live), "rows": 0, "rowBytes": 0, "records": 0, "userFacts": 0}
    seen = set()                                      # the record lists already counted, by identity (every list is alive here: the
    for ix in live:                                   #  indexes in `live` hold them, so no id is reused during the loop)
        out["rows"] += len(ix.rowb)                   # each index encodes its own rows, so those count per index
        out["rowBytes"] += int(getattr(ix, "row_bytes", 0) or 0)
        recs = ix.records
        if recs is not None and id(recs) not in seen:
            seen.add(id(recs))
            out["records"] += len(recs)
        out["userFacts"] += len(ix._user_facts)
    out["bytes"] = (out["rowBytes"] + out["rows"] * INDEX_ROW_OVERHEAD_BYTES + out["records"] * INDEX_RECORD_ROW_BYTES
                    + out["userFacts"] * USER_FACT_BYTES)
    return out


_ASM_CKPT_CAP = _env_or("ROMP_ASM_CKPT_CAP_MB", 64 * 1024 * 1024, 1024 * 1024)   # a document past this is not written
#                                   (counted, and its verdict kept: _asm_oversize_note): that session parses whole. 64 MiB since
#                                   2026-11-12, was 16: a document runs about 106 compressed bytes per pre-cut record, linear in the
#                                   records (a lab over synthetic Claude-Code-shaped transcripts at 3.4 KB a record: 5.1 MB for
#                                   50,000 records, 160 MB of transcript; 30.4 MB for 300,000, 963 MB), so 16 MiB stopped at about
#                                   160,000 records, half a GB, and a host's 994 MB session parsed whole at every open while its
#                                   writer built and threw away a document for six minutes at a time. At 963 MB the whole parse took
#                                   28 s and 3.2 GB peak and the restore from the 30 MB document 4.3 s and 0.5 GB; 64 MiB covers about
#                                   630,000 records, two GB of transcript, and its restore stays a fraction of that parse in time and
#                                   memory. ROMP_ASM_CKPT_CAP_MB sets it outright
_ASM_CKPT_STATS = {"written": 0, "restored": 0, "fallbacks": {}, "skipped": {}, "hydratedBytes": 0, "hydratedAtoms": 0,
                   "hydratedHits": 0, "hydratedEvicted": 0,   # the hydrated-body memo's fills served without a read, and the
                   #                     entries its cap pushed out (2026-10-01): its gauge counts real bytes, so it no longer
                   #                     equals hydratedBytes (line bytes read) while nothing is evicted; these two say it. A
                   #                     memo hit for an atom whose body a peer had already written when this call looked is
                   #                     not a hit: the peer's own read or hit counted that body
                   "restoreMs": {"load": 0.0, "verify": 0.0, "index": 0.0, "seed": 0.0, "total": 0.0},   # the restore's parts since boot, ms
                   #                     (`total` is the whole of _asm_restore, entry to return, so the unnamed remainder, the tail's
                   #                     parse through the seeded adapter, is total minus the four named parts)
                   #                     (T401 (4)): the document's read and checks, the section's identity and coverage (or the
                   #                     atoms-only form's build and identity), the lazy index, the adapter seed; each bumped on
                   #                     the return it names so a boot read names the mover; three decimals on /perf, floats inside
                   "hydratedBy": {},     # bytes per calling function: a whole-tree hydration anywhere shows here
                   "converge": {"writes": 0, "bytes": 0, "deferred": 0, "candidates": 0, "skipped": {}},   # the pass's writes for
#                                          idle leaves from the boot's own parse (T376): looked at, written, deferred for the budget,
#                                          skipped per the writer's reason
                   "oversizeHeld": 0}    # the writes and rebuilds an oversize verdict turned away before any build (2026-11-12;
#                                          they also count under skipped.oversize, or rebuild.oversize for a rebuild never noted)
_ASM_CKPT_LOCK = threading.Lock()
_ASM_CKPT_SAID = set()             # (path, reason) said once per process
_ASM_DOC_MEMO = {}                 # document path -> ((size, mtime_ns), decoded document): the seeded walk's and the restore road's
#                                   decode (2026-09-15; the restore's since 2026-09-24) served once per process for a document whose
#                                   bytes stand, a shared object no reader writes into; only the read, gunzip and JSON decode are
#                                   memoized, every stat check and the guard read in the load stay per call (they are the freshness
#                                   proof), and a document is memoized only once those checks PASSED (a refused one takes no slot)
_ASM_DOC_MEMO_MULTIPLE = 10        # what a decoded assembly document weighs resident against its COMPRESSED bytes on disk: measured
#                                    2.4 times its JSON text (45 MiB resident for 18.5 MiB of text) and up to ten times the gzipped
#                                    file (round two of the memo head, 2026-09-15); the memo's weights and its cap are resident bytes
_ASM_DOC_MEMO_BYTES = [0]          # the memoized documents' resident weight: compressed size times the multiple, summed
_ASM_DOC_MEMO_CAP = _env_or("ROMP_ASM_DOC_MEMO_CAP_MB", max(64 * 1024 ** 2, _machine_memory_bytes() // 512), 1024 * 1024)
#                                    the memo's resident cap: MemTotal / 512, never under 64 MiB, the sibling _DOC_MEMO's convention (a
#                                    count cap said nothing about bytes); reported under asmCheckpoint.asmDocMemo, unrelated to
#                                    checkpoints.docMemo (the fold documents' read memo)


def _asm_doc_memo_weight(size):
    """A memoized assembly document's resident weight from its compressed size on disk."""
    return int(size * _ASM_DOC_MEMO_MULTIPLE)


def _asm_doc_memo_drop(key):
    """Forget `key`'s memoized document (under _ASM_CKPT_LOCK), its bytes let go with it."""
    old = _ASM_DOC_MEMO.pop(key, None)
    if old is not None:
        _ASM_DOC_MEMO_BYTES[0] -= _asm_doc_memo_weight(old[0][0])


def _asm_doc_memo_put(key, mkey, doc):
    """Memoize a VERIFIED document under its file's (size, mtime_ns); the oldest entries leave until the resident total fits the
    cap (the newest stays, it is the one the caller is using)."""
    with _ASM_CKPT_LOCK:
        _asm_doc_memo_drop(key)
        _ASM_DOC_MEMO[key] = (mkey, doc); _ASM_DOC_MEMO_BYTES[0] += _asm_doc_memo_weight(mkey[0])
        for k_ in list(_ASM_DOC_MEMO):
            if _ASM_DOC_MEMO_BYTES[0] <= _ASM_DOC_MEMO_CAP or len(_ASM_DOC_MEMO) <= 1:
                break
            _asm_doc_memo_drop(k_)
_LAZY_FILES = {}                   # rompuuid -> {fsid: path}: fallback for legacy unbound descriptors; restored bodies own their source (2026-09-17)
_HYDRATED = {}                     # uuid -> (the body fields a fill of any kind may read, their weight); dict order = LRU
_HYDRATED_BYTES = [0]              # the entries' weights summed: REAL bytes, each entry's decoded size (_decoded_size)
_HYDRATED_CAP = _env_or("ROMP_HYDRATED_CAP_MB", max(256 * 1024 ** 2, _machine_memory_bytes() // 48), 1024 * 1024)
#                                    the hydrated-body memo's cap in real bytes (2026-10-01): MemTotal / 48, never under 256 MiB
#                                    (about 683 MiB on a 32 GiB machine, 2.7 GiB on a 128 GiB one). It was max(1 GiB, MemTotal /
#                                    32) of LINE bytes over whole records, about 2.9 GiB decoded on a 32 GiB machine. A running
#                                    kernel's memo held a steady set for hours, then grew, with nothing evicted. As decoded body
#                                    fields its size is an estimate in a wide band, since an entry weighs 0.55 of its line for a
#                                    Read result and up to 4.6 for a question's answers. This cap holds that set by the estimate
#                                    and binds as it grows; a 64th would bind sooner and save little more. An LRU thrashes once
#                                    the records read again and again outgrow it, and the size of that set is unmeasured. 64 MB
#                                    of line bytes thrashed on 2026-09-11, before the per-cycle walkers moved onto the lazy
#                                    scalars (T358).
#                                    The weights count a body an atom also holds, so while that atom lives its entry's eviction
#                                    frees only the entry's own share. asmCheckpoint's hydratedHits and hydratedEvicted count the
#                                    memo's serves and evictions
_HYDRATED_ENTRY_BYTES = sys.getsizeof((None, None)) + sys.getsizeof(1 << 20) + sys.getsizeof("0" * 36) + 64
#                                    an entry's own tuple and weight beside its body, its uuid key (the memo keeps it alive once
#                                    the atom has gone), and its slot in the memo's table: a 16-byte entry and its index, 44 to 88
#                                    bytes at the load CPython's growth rule allows, the deleted slots an LRU's moves leave included.
#                                    An estimate: a freshly grown table with no deleted slots costs less, and a tuple taken from the
#                                    interpreter's free list allocates nothing, so on small records it overcounts by about a tenth
#                                    (tests/test_hydrated_memo.py bounds it on both sides)
_LAZY_KINDS = ("a", "u", "c", "o", "k", "b")   # atom kinds whose message is lazy; boundary and refusal atoms carry no message


def _asm_sidecar(doc):
    """The document's sidecar ({"av", "path", "files", "linked"}): what the boot sweep and asm_document_seeds read, a few bytes,
    never the document. `linked` says resume-fork links joined the inputs (the load refuses a document on its links too)."""
    return {"av": _ASM_CKPT_V, "path": doc["path"], "files": sorted(doc["files"]), "linked": bool(doc.get("links"))}


def asm_sidecar_refresh(leaf_path, doc):
    """Rewrite an OLDER sidecar (one without the inputs list) beside a document just restored, so the scan's predicate stops
    degenerating to the one-file lineage test for a session that already carried a document (round one, low 2): a write of
    a few bytes, the document untouched."""
    cp = _asm_ckpt_file(leaf_path)
    if cp is None:
        return False
    meta = cp.with_name(cp.name + ".meta")
    try:
        text = meta.read_bytes(); _count_read(str(meta), len(text))   # counted like the seeds read (round two, low 3)
        d = json.loads(text.decode("utf-8"))
        if isinstance(d, dict) and isinstance(d.get("files"), list) and "linked" in d:
            return False
    except (OSError, ValueError):
        pass
    try:
        mtmp = _writer_tmp(meta)
        mtmp.write_text(json.dumps(_asm_sidecar(doc)))
        os.replace(mtmp, meta)
        return True
    except OSError:
        return False


def _asm_leaf_stat(leaf_path):
    try:
        st_ = os.stat(leaf_path)
        return [st_.st_size, st_.st_mtime]
    except OSError:
        return None


def _asm_mark_refused(leaf_path, reason, rompuuid=None, sdk_human=False):
    """Record in the document's sidecar that the chain proof refused the standing document for the TAIL's SHAPE (a re-rooted
    tail, a reused pre-cut uuid), with the leaf's stat: while that stat stands, the same cut reproduces the same refusal, so
    no road retries the proof or the rewrite. The missing-bit case is marked only when the writer DECLINED its offered rewrite
    (an accepted rewrite converges and is never marked); a transient decline (the entry evicted between the parse and the
    write) marks a document whose only defect was the missing bit, and the next accepted write clears it, so that cost is
    bounded. The mark clears when the leaf moves (the stat differs) or a write the writer accepts replaces the sidecar (T402
    follow-up, round two)."""
    cp = _asm_ckpt_file(leaf_path)
    st_ = _asm_leaf_stat(leaf_path)
    if cp is None or st_ is None:
        return False
    meta = cp.with_name(cp.name + ".meta")
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))   # the writer's own key (asm_checkpoint_write)
    with _asm_key_lock(key):                                 # the writer and the sidecar refresh write this file under the KEY lock;
        try:                                                  #  the mark joins them there (round three, low 2), with a tmp name of its
            d = json.loads(meta.read_bytes().decode("utf-8"))   #  own, and re-reads after its replace: a writer racing in between
            if not isinstance(d, dict):                        #  leaves the mark absent, which the next refusal re-applies
                d = {}
        except (OSError, ValueError):
            d = {}
        d["refused"] = {"reason": reason, "size": st_[0], "mtime": st_[1]}
        try:
            mtmp = _writer_tmp(meta, "mark.")
            mtmp.write_text(json.dumps(d)); os.replace(mtmp, meta)
        except OSError:
            return False
        try:
            return (json.loads(meta.read_bytes().decode("utf-8")).get("refused") or {}).get("reason") == reason
        except (OSError, ValueError, AttributeError):
            return False


def _asm_retire_refusal_mark(meta):
    """The sidecar `meta` is about to be replaced by a write with a new cut: a `refused` mark in it belonged to the old cut and
    must not stand over the new document. The old sidecar's bytes are kept beside it as `<meta>.retired-<stamp>` for forensics,
    the way the flags quarantine keeps its sidecar; only the mark the readers key on goes (with the file). Best-effort."""
    try:
        text = meta.read_bytes()
        d = json.loads(text.decode("utf-8"))
    except (OSError, ValueError):
        return
    if not (isinstance(d, dict) and isinstance(d.get("refused"), dict)):
        return
    for prior in meta.parent.glob(meta.name + ".retired-*"):   # this very mark already kept aside (a retirement whose rewrite keeps
        try:                                                    #  failing retries at every sweep): one copy, never one per retry (the
            if prior.read_bytes() == text:                      #  1717 read, low 2)
                return
        except OSError:
            continue
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    aside, n = meta.with_name("%s.retired-%s" % (meta.name, stamp)), 0
    while aside.exists():                               # loop-ok: a second retirement in the same second
        n += 1
        aside = meta.with_name("%s.retired-%s-%d" % (meta.name, stamp, n))
    try:
        aside.write_bytes(text)
    except OSError as e:                                # best effort by design, but never silent: the mark's forensic copy is lost, so
        _asm_removed("refusedMark:asideFailed")         #  count it and say so once (the 1717 read, low 4); the retirement itself proceeds
        _say_once("checkpoint: the refusal mark of %s could not be kept aside (%s)" % (meta.name, e))


def _asm_refusal_stands(leaf_path):
    """Whether the sidecar marks the standing document refused for the tail's shape at the leaf's CURRENT stat: a few bytes
    read, no document, no tail; True sends every road straight to the whole or cold parse (restore:refusedStanding)."""
    cp = _asm_ckpt_file(leaf_path)
    if cp is None:
        return False
    meta = cp.with_name(cp.name + ".meta")
    try:
        text = meta.read_bytes(); _count_read(str(meta), len(text))
        d = json.loads(text.decode("utf-8"))
        ref = d.get("refused") if isinstance(d, dict) else None
    except (OSError, ValueError):
        return False
    if not isinstance(ref, dict):
        return False
    st_ = _asm_leaf_stat(leaf_path)
    return st_ is not None and [ref.get("size"), ref.get("mtime")] == st_


def asm_document_seeds(leaf_path):
    """Whether the assembly document for `leaf_path` can SEED a one-file walk of the leaf: its inputs are the leaf alone. Read
    from the sidecar's `files` (a few bytes, never the document); a sidecar without the list (an older write) answers as
    asm_document_stands does, and the next write adds it. A back-linked or resume-forked session's document is written over
    the leaf plus its lineage, so file_rewound's load refused it on the inputs comparison and the leaf was read whole at every
    process (T391 follow-up, round one, low 1): such a leaf takes the memo road. A /clear's leaf documents alone since
    2026-10-03 (judge _judge_candidates), and takes the seeded leaf road."""
    if not asm_document_stands(leaf_path):
        return False
    cp = _asm_ckpt_file(leaf_path)
    meta = cp.with_name(cp.name + ".meta")
    try:
        text = meta.read_bytes(); _count_read(str(meta), len(text))   # the one steady-state read this predicate adds: counted
        d = json.loads(text.decode("utf-8"))
    except (OSError, ValueError):
        return True                                       # no readable sidecar: the document stands, its inputs unknown
    files = d.get("files") if isinstance(d, dict) else None
    if not isinstance(files, list):
        return True
    return files == [Path(leaf_path).stem] and not d.get("linked")   # the leaf alone, no resume links among the inputs


_RETIRED_FOLDS = {}                # path -> {fold name}: folds a caller retired whose cursor may still sit in the on-disk document;
#                                   checkpoint_write consults it AFTER its cursor snapshot and its carry, so the document omits them
#                                   (T391 follow-up, round one, medium: the carry re-added the memo's cursor from the document)


def _retire_fold(path, name):
    """Retire fold `name` of `path` for the next write: the in-memory cursor goes now, and the write skips the name when it
    carries the on-disk document's states forward, so the document omits the fold and the cut follows the live folds."""
    key = str(path)
    with _CKPT_LOCK:
        _RETIRED_FOLDS.setdefault(key, set()).add(name)
        while len(_RETIRED_FOLDS) > _DROP_OWED_MAX:       # bounded like the owed drops: the oldest path's retirement is let go
            _RETIRED_FOLDS.pop(next(iter(_RETIRED_FOLDS)), None)
        _FOLD_DIRTY.add(key)


_DOC_MEMO = {}                     # path -> (document file's _doc_sig, the loaded document): one read shared between the
#                                   retirement's consult and the write's carry (T391 follow-up, low C), dropped when the file moves;
#                                   a write keeps the document it wrote here only when it carried an entry (a state or an
#                                   over-the-cap cursor) the disk alone had
_DOC_MEMO_PARSE_MULTIPLE = 4.5     # what a parsed fold document weighs resident against its bytes on disk (measured on the devbox's
#                                    documents, 2026-09-12): the memo's weights and its cap are RESIDENT bytes, so the ceiling means
#                                    what it says
_DOC_MEMO_BYTES = [0]              # the memoized documents' resident weight: size on disk times the multiple, summed
_DOC_MEMO_CAP = _env_or("ROMP_DOC_MEMO_CAP_MB", max(64 * 1024 ** 2, _machine_memory_bytes() // 512), 1024 * 1024)
#                                    the memo's resident cap: MemTotal / 512, never under 64 MiB (256 MiB on a 128 GiB machine; a count
#                                    cap said nothing about bytes and held whole documents for files no writer touched again), reported
#                                    under checkpoints.docMemo
_DOC_INDEX = {}                    # path -> (document file's _doc_sig, {fold name: count, or None when not an int}) for every entry
#                                   with a state or an over-the-cap cursor, as this process last WROTE or LOADED the document: what a
#                                   write's carry could take from it, so a write after a write that changed nothing carryable reads
#                                   no document byte (_carry_forward_states)
_DOC_INDEX_MAX = 4096              # paths; over it the one least recently written or loaded is let go (its next carry reads the
#                                    document once)


def _doc_sig(st_):
    """What identifies a fold document's bytes from its stat: (inode, mtime_ns, size). Every writer replaces a document by a
    rename (a new inode), so a document another writer replaced at the same size within one clock tick still differs."""
    return (st_.st_ino, st_.st_mtime_ns, st_.st_size)


def _doc_stat_sig(cp, key):
    """The _doc_sig of `key`'s fold document `cp`, or None when there is none to read. A missing file (or directory) is the
    quiet common case, a first write. Any other stat error (a checkpoints directory this account cannot search, a failing
    disk) answers None too and never raises, but is said once, naming the document and the error: the file is then read
    whole and its write carries nothing (a lasting one can fail the write as well, and every boot reads the file whole), and
    that line is what says why."""
    try:
        return _doc_sig(cp.stat())
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        _say_once("checkpoint: the fold document %s of %s could not be checked (%s); read as no document, so the file is "
                  "read whole and its write carries nothing" % (cp, key, e.strerror or e))
        return None


def _doc_carryable(folds):
    """{fold name: count} of the entries a write's carry could take from a document's `folds` (the carry's own shape test: a
    dict with a state or an over-the-cap cursor; the count None when it is not an int, "3", 3.0 or infinity among them, an
    entry the carry skips)."""
    out = {}
    for n, f in (folds or {}).items():
        if isinstance(f, dict) and ("state" in f or f.get("over")):
            c = f.get("count")
            out[n] = c if type(c) is int else None
    return out


def _doc_index_put_locked(key, sig, folds):
    """Record (under _CKPT_LOCK) what `key`'s document, the file with stat `sig`, can give a write's carry; the path becomes the
    index's newest, so the bound lets go of the path least recently written or loaded."""
    _DOC_INDEX.pop(key, None)
    _DOC_INDEX[key] = (sig, _doc_carryable(folds))
    while len(_DOC_INDEX) > _DOC_INDEX_MAX:
        _DOC_INDEX.pop(next(iter(_DOC_INDEX)), None)


def _doc_memo_weight(sig):
    """A memoized document's resident weight from its file stat: the size on disk times the parse multiple."""
    return int(sig[-1] * _DOC_MEMO_PARSE_MULTIPLE)


def _doc_memo_drop(key):
    """Forget `key`'s memoized document (under _CKPT_LOCK), its bytes let go with it."""
    old = _DOC_MEMO.pop(key, None)
    if old is not None:
        _DOC_MEMO_BYTES[0] -= _doc_memo_weight(old[0])


def _doc_memo_put_locked(key, sig, doc):
    """Memoize (under _CKPT_LOCK) `key`'s document, the file with stat `sig`: bounded in bytes (the caches rule), the oldest
    documents go and the newest stays for the read it shares."""
    _doc_memo_drop(key)
    _DOC_MEMO[key] = (sig, doc); _DOC_MEMO_BYTES[0] += _doc_memo_weight(sig)
    while _DOC_MEMO_BYTES[0] > _DOC_MEMO_CAP and len(_DOC_MEMO) > 1:
        _doc_memo_drop(next(iter(_DOC_MEMO)))


def _ckpt_doc_shared(key):
    """`key`'s fold document as _ckpt_load verifies it (version, path, shape; a corrupt one counted and removed), or None; the
    read shared with the write's carry through _DOC_MEMO, keyed by the document file's stat, so a consult and the write that
    follows read the document once (low C). Counted under checkpoints.docConsults. A validated load also records what the
    document can give a write's carry (_DOC_INDEX)."""
    cp = _ckpt_file(key)
    if cp is None:
        return None
    sig = _doc_stat_sig(cp, key)
    if sig is None:
        return None                                       # no document
    with _CKPT_LOCK:
        hit = _DOC_MEMO.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    doc = _ckpt_load(key)                                 # the validated read: version, path and shape (low A: a raw read raised
    with _CKPT_LOCK:                                      #  on a folds that is not a dict, retired from another path's or an old
        _CKPT_STATS["docConsults"] += 1                   #  version's document, re-read a corrupt one forever)
        if doc is not None:
            _doc_memo_put_locked(key, sig, doc)
            _doc_index_put_locked(key, sig, doc.get("folds"))
        else:
            _doc_memo_drop(key); _DOC_INDEX.pop(key, None)
    return doc


def _doc_folds_on_disk(key):
    """The fold shapes of `key`'s fold document as it sits on disk, {} with none or one that does not verify: what a retirement
    consults before any fold of this process has loaded the document."""
    doc = _ckpt_doc_shared(key)
    folds = doc.get("folds") if isinstance(doc, dict) else None
    shapes = _doc_fold_shapes(folds) if isinstance(folds, dict) else {}
    with _CKPT_LOCK:
        _CKPT_DOC_FOLDS.setdefault(key, shapes)
    return shapes


def rewound_memo_forget(path):
    """Drop the incident scan's memo cursor for `path` (T391 follow-up, round one, low 2): a leaf that took the memo road while it
    had no assembly document carries a rewoundUuids cursor in its fold document; once its first compaction lands and the scan
    flips to the leaf road for good, that cursor would never step again, and the checkpoint's cut, the minimum over the folds,
    would drag behind it by up to the lag bound (about an eighth of the file) until growth passed it, every later boot's
    restore reading that much more tail for every fold. Forgotten here, the next write omits the fold and the cut follows the
    live folds."""
    key = str(path)
    had = _REWOUND_CACHE.pop(key, None) is not None
    with _CKPT_LOCK:
        shapes = _CKPT_DOC_FOLDS.get(key)
    if shapes is None:                                    # no fold of this process has read or written the document yet (a fresh
        shapes = _doc_folds_on_disk(key)                  #  process whose scan reaches the leaf first): ask the disk (low 8)
    on_disk = "rewoundUuids" in (shapes or {})            # the document as last read or written carries the fold
    if had or on_disk:                                    # retire only at the FLIP, when there is something to retire: a leaf-road
        _retire_fold(key, "rewoundUuids")                 #  pass over a clean path retires nothing and dirties nothing (round two:
    #                                                        an unconditional retirement popped a memo stored later in the process
    #                                                        out of the next document and kept every leaf-road path dirty forever)


def asm_document_stands(leaf_path):
    """Whether an assembly document file exists for `leaf_path` (a stat, no read; False with no checkpoint directory): the
    existence half of asm_document_seeds, the predicate the judges' incident scan asks before taking the leaf road."""
    cp = _asm_ckpt_file(leaf_path)
    return cp is not None and cp.exists()


def _asm_document_would_restore(leaf_path):
    """Whether a restore of `leaf_path` would take its document, as far as a stat and the sidecar's few bytes can tell (never
    the document, so not the load's proof): the file stands and no refusal mark stands at the leaf's current stat."""
    return asm_document_stands(leaf_path) and not _asm_refusal_stands(leaf_path)


def asm_document_restores(leaf_path, rompuuid=None):
    """Whether the next parse of `leaf_path` would restore from its document rather than parse whole, as far as a cheap check
    can tell (_asm_document_would_restore), and no assembly entry of the session (`rompuuid`; any session's when None) was
    kept whole because a restore in this process refused that document (keepWhole). The release at a dead timeline lane asks
    it before letting the session's entry and records go (2026-10-01): with no document a restore would take, the next parse
    after the release would be a whole one."""
    if not _asm_document_would_restore(leaf_path):
        return False
    rp = os.path.realpath(str(leaf_path))
    with _ASM_LOCK:
        entries = [e for k, e in _ASM_CACHE.items() if k[0] == rp and (rompuuid is None or k[1] == str(rompuuid))]
    return not any(e.get("keepWhole") for e in entries)


def _asm_ckpt_file(leaf_path):
    d = _ckpt_dir()
    if d is None:
        return None
    return Path(d) / (hashlib.sha1(os.path.realpath(str(leaf_path)).encode("utf-8")).hexdigest()[:20] + ".asm.json.gz")


# THE OVERSIZE VERDICT (2026-11-12). A document past _ASM_CKPT_CAP is built whole and then thrown away, and the cut that makes it
# moves with every settled turn, so the entry's own memo (docSkip, keyed on the cut) never held for a live session, and a fresh
# entry (a new parse, a new process) held nothing at all: on a host whose 994 MB session's document ran past the cap, the single
# document writer spent about 382 s on it at each settle and wrote no other document meanwhile, and the rebuild parsed it whole
# at each kernel life. A document only grows as its transcript does (its rows are every record before a cut that only moves
# forward), so the verdict is kept per transcript, on disk beside where its document would be (a deploy restarts the kernel
# several times a day): {path, dev, ino, size, bytes, records, av}. It holds while the transcript is the same file (device and
# inode), at least `size` long (the leaf's bytes before the refused cut), the document version is `av` and the cap is still
# under `bytes`; a transcript rewritten or truncated, a version bump or a raised cap is looked at again. While it holds, the
# writer turns the leaf away before it builds anything, a parse does not note it for a rebuild and a refresh is not claimed.
_ASM_OVERSIZE = {}                # realpath -> the verdict as read from its file (None: no file), loaded once per leaf per process


def _asm_oversize_file(leaf_path):
    cp = _asm_ckpt_file(leaf_path)
    return None if cp is None else cp.with_name(cp.name[:-len(".asm.json.gz")] + ".asm.oversize")


def _asm_oversize_verdict(leaf_path):
    """The leaf's recorded oversize verdict (memoized per process), or None."""
    rp = os.path.realpath(str(leaf_path))
    with _ASM_CKPT_LOCK:
        if rp in _ASM_OVERSIZE:
            return _ASM_OVERSIZE[rp]
    f = _asm_oversize_file(rp)
    v = None
    if f is not None:
        try:
            v = json.loads(f.read_text(encoding="utf-8"))
            if not (isinstance(v, dict) and v.get("path") == rp):
                v = None
        except (OSError, ValueError):
            v = None
    with _ASM_CKPT_LOCK:
        _ASM_OVERSIZE.setdefault(rp, v)
        return _ASM_OVERSIZE[rp]


def asm_oversize_stands(leaf_path):
    """The leaf's oversize verdict when it still holds (see above), else None. A dict read once the verdict is loaded, and one
    stat of the leaf when a verdict exists."""
    if _CKPT_DIR_FN is None:
        return None
    v = _asm_oversize_verdict(leaf_path)
    if v is None:
        return None
    try:
        if v.get("av") != _ASM_CKPT_V or int(v.get("bytes") or 0) <= int(_ASM_CKPT_CAP):
            return None
        st_ = os.stat(v["path"])
        if (int(st_.st_dev), int(st_.st_ino)) != (int(v["dev"]), int(v["ino"])) or int(st_.st_size) < int(v["size"]):
            return None
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return v


def _asm_oversize_note(leaf_path, size, nbytes, records):
    """Keep the verdict on a document past the cap: the leaf's file identity, `size` (its bytes before the refused cut), the
    document's compressed `nbytes` and its pre-cut `records`. Said once per leaf with the numbers; a file that cannot be written
    still holds for this process."""
    rp = os.path.realpath(str(leaf_path))
    try:
        st_ = os.stat(rp)
    except OSError:
        return None
    v = {"path": rp, "dev": int(st_.st_dev), "ino": int(st_.st_ino), "size": int(min(size, st_.st_size)), "bytes": int(nbytes),
         "records": int(records), "av": _ASM_CKPT_V, "t": time.time()}
    with _ASM_CKPT_LOCK:
        _ASM_OVERSIZE[rp] = v
    f = _asm_oversize_file(rp)
    if f is not None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = _writer_tmp(f)
            tmp.write_text(json.dumps(v), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass
    _say_once("assembly checkpoint: %s gets no document: it would be %.1f MB (%d records), past the %.0f MB cap "
              "(ROMP_ASM_CKPT_CAP_MB); it parses whole, and no document is built for it again until the transcript is replaced "
              "or the cap rises" % (rp, nbytes / 1048576.0, records, _ASM_CKPT_CAP / 1048576.0))
    return v


def _asm_oversize_clear(leaf_path):
    """A document was written for the leaf: any verdict against it is history."""
    rp = os.path.realpath(str(leaf_path))
    had = _asm_oversize_verdict(rp) is not None           # (loaded once per leaf per process: a file an earlier process kept)
    with _ASM_CKPT_LOCK:
        _ASM_OVERSIZE[rp] = None
    if had:
        f = _asm_oversize_file(rp)
        try:
            if f is not None:
                f.unlink()
        except OSError:
            pass


_ASM_LAST_WRITE_CUT = {}          # realpath -> the leaf's cut offset of the document the writer last published (under _ASM_CKPT_LOCK):
#                                   the refusal road reads it after its offered rewrite to tell a rewrite that MOVED the cut (a new tail,
#                                   proven afresh at the next restore) from one that reproduced the refused cut (marked; stage one b)
_ASM_CHAIN_REFUSED_PATHS = {}     # realpath -> why the chain proof refused the standing document at this parse ("unproven": the
#                                   missing bit; "shape": the tail's own shape): parse_session
#                                   rewrites the document from the whole parse that follows, then and there (the writer has the
#                                   resolved graph in hand and the refusal is the event), never leaving it to the entry's next
#                                   quiescence drop, which a quiet session reaches slowly or never (T402 follow-up: sixteen of
#                                   twenty-six sessions paid a whole parse at every boot while their documents lacked the bit)
_ASM_CKPT_REFUSED = {}            # realpath -> the reason a standing document was refused at this path's last restore: read
#                                   by _assemble to book full:refused, since the note below unlinks the document before the
#                                   parse decides its road (T398 round one, medium: a refused document read as none at all)


def _asm_ckpt_note(path, reason, detail=""):
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["fallbacks"][reason] = _ASM_CKPT_STATS["fallbacks"].get(reason, 0) + 1
        first = (str(path), reason) not in _ASM_CKPT_SAID
        _ASM_CKPT_SAID.add((str(path), reason))
        _ASM_CKPT_REFUSED[os.path.realpath(str(path))] = (str(reason), time.monotonic())   # stamped: the write pops only an older one
    if first:
        try:
            sys.stderr.write("assembly checkpoint fallback (%s) for %s%s\n" % (reason, path, (": " + detail) if detail else ""))
        except Exception:
            pass
    cp = _asm_ckpt_file(path)
    if cp is not None:
        with _ASM_CKPT_LOCK:                              # the document goes, and nothing of it stays memoized: here rather than in
            _asm_doc_memo_drop(str(cp))                   #  the load's refusal alone, since the restore's own proofs (identity,
        #                                                   coverage, a row the index cannot decode) refuse a document the load
        #                                                   already memoized (2026-09-24, the restore road reads through the memo)
        try:
            cp.unlink()
            _asm_removed("fallback:" + str(reason), document=True)
            cp.with_name(cp.name + ".meta").unlink(missing_ok=True)
        except OSError:
            pass


def _asm_ckpt_skip(reason):
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["skipped"][reason] = _ASM_CKPT_STATS["skipped"].get(reason, 0) + 1
    return False


def _restore_ms(part, t0):
    """Add the restore part's elapsed time (perf_counter since `t0`) under asmCheckpoint.restoreMs, on the return it names."""
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["restoreMs"][part] = _ASM_CKPT_STATS["restoreMs"].get(part, 0.0) + (time.perf_counter() - t0) * 1000.0


def asm_checkpoint_stats():
    """The assembly documents for /perf. Almost everything here counts since boot (written, restored, the per-reason tables,
    restoreMs), and `hydratedBytes` (the bytes of pre-cut bodies read on demand) and `hydratedAtoms` (those bodies) are
    running counters too, so they keep rising while the memo that holds the bodies sits at its cap (what the memo holds is
    the heap block's hydrated). The one gauge is asmDocMemo."""
    with _ASM_CKPT_LOCK:
        out = dict(_ASM_CKPT_STATS); out["fallbacks"] = dict(out["fallbacks"]); out["skipped"] = dict(out["skipped"])
        out["restoreMs"] = {k: round(v, 3) for k, v in (out.get("restoreMs") or {}).items()}   # microsecond resolution (a fast
        #                                                                                       restore's parts read above zero)
        out["hydratedBy"] = dict(out["hydratedBy"]); out["removed"] = dict(out.get("removed") or {})
        out["hydratedByStage"] = dict(out.get("hydratedByStage") or {})   # T401: bytes per (stage, calling function)
        cv = out["converge"] = dict(out["converge"]); cv["skipped"] = dict(cv["skipped"])
    with _ASM_CKPT_LOCK:
        out["asmDocMemo"] = {"entries": len(_ASM_DOC_MEMO), "bytes": _ASM_DOC_MEMO_BYTES[0], "capBytes": _ASM_DOC_MEMO_CAP,
                             "multiple": _ASM_DOC_MEMO_MULTIPLE}   # the document memo the seeded walk and the restore share (unrelated to
        #                                                                 checkpoints.docMemo)
        out["parse"] = dict(_ASM_STATS)               # the parse's roads (T398): serve, fold, restore, full (with its reason), bypass,
        out["refresh"] = dict(_ASM_REFRESH_STATS)     #  fallback, and every g:<reason> demotion, so a whole parse names its road; the
        #                                                document refreshes a restored entry's churn bound asked for (asm_refresh_claim)
        out["rebuild"] = dict(_ASM_REBUILD_STATS, pending=len(_ASM_REBUILD),   # the documents rebuilt for leaves a parse found
                              waiting=_ASM_BG["waiting"])   # without one (asm_rebuild_note), `pending` the table's size now, `waiting`
    return out                                        #  a rebuild held at a chunk point for the pusher's next sleep (0 or 1)


_ASM_DOC_REMOVED = [0]            # assembly documents this process has removed (the fallback that refused one, the boot sweep): the
#                                   kernel's converge pass looks again at every leaf it had set aside because a document stood there
#                                   once this moves (asm_documents_removed; the review of PR #370)


def asm_documents_removed():
    """How many assembly document files this process has removed so far (a refusal's fallback, the boot sweep): a generation the
    converge pass keys its "a document stands" marks on, so a document removed under an unchanged leaf is written again."""
    with _ASM_CKPT_LOCK:
        return _ASM_DOC_REMOVED[0]


def _asm_removed(reason, document=False):
    """The checkpoint directory's removals and retirements, counted per reason (T398): a document file removed by the fallback
    that refused it or by the boot sweep (`fallback:<reason>`, `sweep`; `document` True, which also moves the generation the
    converge pass reads, asm_documents_removed); a refusal mark the sweep retired from a version-old
    sidecar (`refusedMark:version`, the document stays); a retirement whose sidecar rewrite failed and left the mark standing
    (`refusedMark:versionFailed`, nothing removed, retried next boot); a mark whose forensic aside could not be written
    (`refusedMark:asideFailed`, the retirement proceeded). The report is `asmCheckpoint.removed` on /perf."""
    with _ASM_CKPT_LOCK:
        r = _ASM_CKPT_STATS.setdefault("removed", {})
        r[reason] = r.get(reason, 0) + 1
        if document:
            _ASM_DOC_REMOVED[0] += 1


def asm_converge_stat(name, n=1):
    """Count the converge pass's assembly work under asmCheckpoint.converge (T376)."""
    with _ASM_CKPT_LOCK:
        cv = _ASM_CKPT_STATS["converge"]
        cv[name] = cv.get(name, 0) + n


def asm_converge_skip(reason):
    with _ASM_CKPT_LOCK:
        sk = _ASM_CKPT_STATS["converge"]["skipped"]
        sk[reason] = sk.get(reason, 0) + 1


def asm_whole_entries():
    """The WHOLE (unrestored) assembly entries this process holds, as (leaf path, rompuuid, sdk_human): the parses the boot
    actually did, whatever the sessions' age. The converge pass enumerates its assembly candidates from these (T382: the
    discover window's rows left every idle leaf older than 48 hours out, the very population the step targets)."""
    with _ASM_LOCK:
        rows = [(k, e.get("path")) for k, e in _ASM_CACHE.items() if e is not None and not e.get("prefix") and not e.get("preTurns")]
    # the leaf path AS THE PARSE WAS HANDED IT, not the cache's resolved key: the reader keys its record entries on the path as
    # given, and the pass asks it (entry_whole_resident) under the path it enumerates here. Handed the resolved path, a root
    # that crosses a symlink (a temp root under /var on macOS, a symlinked home or CLAUDE_CONFIG_DIR anywhere) found no record
    # entry, counted noEntry every cycle and wrote no document, so the next boot paid the whole read (the macOS triage of
    # v0.16). The document itself is keyed on the resolved path (_asm_ckpt_file), so it lands where the restore looks either way.
    return [((p or k[0]), k[1], bool(k[2])) for k, p in rows]


def asm_whole_entry_for(leaf_path):
    """This process's whole assembly entries over `leaf_path` as {rompuuid: set of sdk_human flags}: how the pass names the
    session of a leaf the discover window no longer lists, and sees which flags it was parsed under (T382; a process can hold
    two whole entries for one leaf, the judges' flag and the display's)."""
    real = os.path.realpath(str(leaf_path)); out = {}
    with _ASM_LOCK:
        for k, e in _ASM_CACHE.items():
            if k[0] == real and e is not None and not e.get("prefix") and not e.get("preTurns"):
                out.setdefault(k[1], set()).add(bool(k[2]))
    return out


def asm_entry_whole(leaf_path, rompuuid, sdk_human=False):
    """Whether this process holds a WHOLE (unrestored) assembly entry for the session over `leaf_path`: the boot's own parse,
    which the converge pass writes an idle leaf's document from (T376); a restored entry's document already stands, and no
    entry means no write (never a parse of the pass's own)."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _ASM_LOCK:
        entry = _ASM_CACHE.get(key)
    return entry is not None and not entry.get("prefix") and not entry.get("preTurns")


def _atom_kind(a):
    """The lazy kind code of an emitted atom: which body fields hydrate rebuilds and how."""
    t = a.get("type")
    if t == "system":
        return "x" if a.get("subtype") == "compact_boundary" else "r"
    if a.get("absorbed"):
        return "b"
    if t == "user":
        return "c" if a.get("command") else "u"
    if a.get("skillMd") is not None:
        return "k"
    if a.get("command") is True:
        return "o"
    return "a"


def _atom_scalars(a):
    """Every field of an emitted atom but the bodies (message, toolUseResult, skillMd) and the read-order tiebreak."""
    return {k: v for k, v in a.items() if k not in ("message", "toolUseResult", "skillMd", "_seq")}


def _lazy_of(a, kind, rec_index):
    """The identity scalars a lazy atom carries in place of its body (what the ids, the segmentation and the gates read).
    An atom that is lazy already keeps its marker (its body is on disk; the marker was made from it)."""
    if a.get("lazy") is not None:
        return {k: v for k, v in a["lazy"].items() if k not in ("i", "at")}
    text = _text_of(_content(a.get("message"))) if a.get("message") is not None else ""
    lz = {"k": kind, "h": hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8], "nt": bool(text)}
    if _machine_written(a):                     # defaults left out: not machine-written, not an interrupt, no stop reason,
        #                                          no structured tool result, no model
        lz["mw"] = True
    if is_interrupt_record(a):
        lz["ir"] = True
    msg = a.get("message") if isinstance(a.get("message"), dict) else None
    if msg is not None and msg.get("stop_reason") is not None:
        lz["sr"] = msg["stop_reason"]
    if msg is not None and msg.get("model"):
        lz["mdl"] = msg["model"]                # the model stamp a few readers test (synthetic replies, the settle)
    if "toolUseResult" in a:
        lz["tur"] = True
    blocks = _content(msg) if msg is not None else []
    tu = [[b.get("id"), b.get("name")] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    tr = [b.get("tool_use_id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    if tu:
        lz["tu"] = tu                           # tool calls and results by id: what the settle gates and the task
    if tr:                                      #  pairings read, so they need no body
        lz["tr"] = tr
    if kind == "c":
        lz["disp"] = text                       # the invocation's display text is short: inline, no read to rebuild it
    if a.get("type") == "assistant" and not a.get("isApiError"):
        pc = _prose_chars(a)
        if pc:
            lz["pc"] = pc                       # assistant prose chars: the substantive reads and the citation gate (T358)
    mids = postal_mids(_content(msg) if msg is not None else None)
    if mids:
        lz["mid"] = mids                        # postal message ids: the timeline connector and the caption join (T358)
    return lz


def _pre_tree_identity(atoms, rompuuid):
    """sha1 over the turn ids, segment ids and atom uuids of `atoms` segmented as a session (no idle or orphan atoms):
    the proof that a restored prefix is the whole parse's prefix."""
    h = hashlib.sha1()
    for turn in segment_turns([dict(a) for a in atoms], rompuuid):
        h.update(turn["id"].encode()); h.update(b"|")
        for seg in segments(turn):
            h.update(seg["id"].encode()); h.update(b",")
        for a in turn["atoms"]:
            h.update((a.get("uuid") or "").encode()); h.update(b";")
    return h.hexdigest()


_ASM_SKIP_STRUCTURAL = ("unsplittable", "reconstruction", "unencodable", "oversize", "noCut", "reuse", "closure", "young")   # true of a cut until it moves
_ASM_SKIP_BLIP = ("stat", "offsets", "write")   # the writer's declines of the MOMENT (a stat or a guard read the filesystem refused, the reader's
#                                       record offsets not in memory, a write the disk refused): they say nothing about the document's shape,
#                                       and the same parse a moment later writes (the rewrite after a refusal marks none: review of PR 158)
_ASM_FIRST_DOC_MIN = _env_or("ROMP_CKPT_FIRST_DOC_KB", _CKPT_FOLD_CAP // 8, 1024)   # the young-session floor (the 1695 read, low 1): no FIRST
#                                       document until the pre-cut part holds this many bytes (an eighth of the fold cap: 1 MB by default, moving with
#                                       ROMP_CKPT_FOLD_CAP_KB; ROMP_CKPT_FIRST_DOC_KB sets it outright, 0 turns the floor off, as the test suite does
#                                       over its small fixtures). Why: with uniform turns the churn bound (the tail past the cut times eight at least
#                                       the pre-cut bytes) is met on almost every settle until the pre-cut part reaches eight times the two-to-three
#                                       turn lag, so a young session rewrote its document at nearly every settle (27 rewrites over its first 30 settled
#                                       turns measured); below the floor a whole parse costs milliseconds and a document saves nothing
_ASM_TAIL_SHARE = 8                   # the churn bound (stage one b): a standing document is rewritten when the tail past its cut has
#                                       grown to ONE EIGHTH of the pre-cut bytes (each rewrite is a whole parse, so a share bounds the
#                                       rewrites over a leaf's life to a logarithm of its growth while the tail every cold parse still
#                                       decodes stays under an eighth of the documented part; the eighth is the machine's own cap share,
#                                       the share a capped job's CPU and memory quota take, never a literal chosen for this file); a
#                                       compaction past the cut restores from the standing document while the tail is under the share
#                                       and parses whole once it has reached it, so the settle writes a later cut (2026-09-24)


def _tree_key(tree):
    """A parsed tree's identity for the writer's memo: its object, its turn count and its last turn's id (a new parse mints
    new dicts; the same tree yields the same section, or none, every time)."""
    turns = (tree or {}).get("turns") or []
    return (id(tree), len(turns), turns[-1].get("id") if turns else None)


class _DocWriterScope:
    """The settle's or the converge pass's document write over the leaf's OWN entry: the key lock for the write's whole
    length, so no fold changes the entry while the writer reads it, and the entry marked `docWriter` meanwhile (set once the
    lock is taken, cleared before it is released). The writer only reads the graph (its own flags aside), so a reader of the
    graph that must not wait out a multi-second write (14 s at 110 MB in a lab: the comment cut, with_display_graph) reads
    beside it while the mark it saw stands (2026-10-01). The wait for the lock is unbounded: every caller can wait out the
    holder (the exit, which could not, no longer writes documents: 2026-10-08)."""

    def __init__(self, key):
        self.key = key

    def __enter__(self):
        self.lock = _asm_key_lock(self.key)
        self.lock.acquire()
        with _ASM_LOCK:
            self.entry = _ASM_CACHE.get(self.key)
        self.tok = object()
        if self.entry is not None:
            self.entry["docWriter"] = self.tok
        return self

    def __exit__(self, *exc):
        if self.entry is not None and self.entry.get("docWriter") is self.tok:
            self.entry.pop("docWriter", None)
        self.lock.release()
        return False


def asm_checkpoint_write(leaf_path, rompuuid, sdk_human=False, tree=None, reason_out=None, who="settle", from_entry=None, held=None,
                         held_readers=None, stand_down=None):
    """Write the leaf's assembly checkpoint from its WHOLE assembly entry. False when there is nothing to write: no
    entry, an entry restored from a document (its cut stands until a whole parse replaces the entry), no compaction boundary in
    the tree (the whole file would be the tail), a cut that would not split the chronological order the fold's gate
    needs (garbled stamps), or a document past the cap; each counted under asmCheckpoint.skipped, and appended to
    `reason_out` when a list is given (the converge pass reads its refusal there; T376 review). `who` names the caller
    in the blip line (the settle, the converge pass), said once per leaf and reason. An entry it wrote from is marked for
    re-seat: the next parse restores the entry from this document (2026-09-24). `from_entry`, when given, is the whole entry
    to write from in place of the leaf's own (the document refresh's private parse, asm_refresh_document). `held`, when given,
    is the whole entry a parse installed for this key (a handed rewrite after a refused document): written from, under the
    key lock, when the cache no longer holds any entry for the key (evicted or released since, so no reader folds it now),
    counted write:fromHeld; an entry the cache does hold is written as always. `held_readers`, beside `held`, are the reader
    entries the parse's records came from ({path: entry tuple}, _reader_entries_of): written from the kept entry, a file whose
    reader entry the cache no longer holds at that generation takes its record offsets from the kept one, once its witness
    bytes still stand in the file (_kept_reader_offsets; the review of PR 158: nothing pins the reader entry once the
    assembly entry is evicted, and the write declined for offsets). `stand_down`, when given, is asked right before the
    document's replace: True leaves the file standing there alone and skips (`moot`; the rebuild's, whose document another
    writer may have written while it worked). Over a private entry the writer's phases are a rebuild's chunk points
    (_asm_bg_yield, a no-op on any other thread). The kernel's exit never calls it (2026-10-08): its callers run while the
    kernel lives."""
    cp = _asm_ckpt_file(leaf_path)
    if cp is None:
        return False

    def _skip(reason):
        if reason_out is not None:
            reason_out.append(reason)
        return _asm_ckpt_skip(reason)
    if asm_oversize_stands(leaf_path) is not None:
        with _ASM_CKPT_LOCK:
            _ASM_CKPT_STATS["oversizeHeld"] = _ASM_CKPT_STATS.get("oversizeHeld", 0) + 1
        return _skip("oversize")                # its document was past the cap and can only have grown: nothing is built
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with (_DocWriterScope(key) if from_entry is None else _NO_LOCK):   # a private entry is nobody else's: the leaf's readers
        #                                                                  never wait on the refresh's write (they took the key
        #                                                                  lock for it)
        if from_entry is not None:
            entry = from_entry
        else:
            with _ASM_LOCK:
                entry = _ASM_CACHE.get(key)
            if entry is None and held is not None:
                entry = held                    # evicted since its parse installed it: the hand-off kept it (the review of PR 155)
                _asm_stat("write:fromHeld")
            else:
                held_readers = None             # the cache's own entry: its reader entries are the cache's to answer for
        if entry is None:
            return _skip("noEntry")
        if entry.get("prefix") or entry.get("preTurns"):
            return _skip("restored")            # the document it came from stands (its refresh: asm_refresh_document)
        ad = entry["ad"]
        atoms = entry["atoms"]
        def chunk():
            """A rebuild's chunk point between the phases below (a private entry's write holds no lock; a no-op on any other
            thread). True when it waited and another writer's document stands now: the write stands down there (`moot`), the
            rest of its phases unspent, as it would at its replace."""
            return bool(from_entry is not None and _asm_bg_yield() and stand_down is not None and stand_down())
        bounds = [a for a in atoms if a.get("type") == "system" and a.get("subtype") == "compact_boundary"]
        turns = segment_turns([dict(a) for a in atoms], rompuuid)
        if chunk():                                           # the turns are cut; the cut search is next
            return _skip("moot")
        # THE CUT (stage one b, plans/checkpoint-settled-cut.md): the boundary before the last SETTLED turn, a turn whose result
        # landed (`ended`) and whose next turn exists, or the turn holding the last compaction boundary, whichever is later; a
        # leaf with neither has no cut (structural, re-armed as the tree grows). The pre-cut part ends before that turn, so the
        # tip is a settled record with a following record, never a record of an open turn: the cut never chases a live turn
        # (a session mid-turn for hours keeps its previous document; the open turn is the tail, decoded as today)
        si = max((i for i in range(len(turns) - 1) if turns[i].get("ended")), default=None)
        last_b = max(bounds, key=lambda a: (a["t"], a.get("_seq", 0))) if bounds else None
        bi = None
        if last_b is not None:
            bi = next(i for i, t in enumerate(turns) if any(a.get("uuid") == last_b["uuid"] for a in t["atoms"]))
            if last_b["uuid"] in ad._adopted and bi > 0:
                bi -= 1                                   # an adopted manual compact: its /compact episode is the turn before
        ci = max(x for x in (si, bi) if x is not None) if (si is not None or bi is not None) else None
        if ci is None:
            return _skip("noCut")
        cut_uuid = next((a.get("uuid") for a in turns[ci]["atoms"] if a.get("uuid")), None) or "turn%d" % ci
        memo = entry.get("docSkip")
        if memo is not None and memo[0] == cut_uuid:
            return _skip(memo[1])               # this cut already failed to write: nothing rebuilt until it moves
        standing = entry.get("docCut")              # (the cut's first uuid, the pre-cut bytes, the boundaries before the cut)
        if entry.get("docWritten") and cp.exists() and (tree is None or entry.get("docTurns")
                                                          or entry.get("docNoTurns") == _tree_key(tree)):
            # this entry's pre-cut part has not moved (a fold appends after the cut); a document written without a tree is
            # written again once one is given, one whose tree yielded no section is not, for that tree (review low 4). The cut
            # ADVANCES (a rewrite) only under the churn bound: more boundaries than the standing cut counted, or the tail past it
            # has grown to the share (_ASM_TAIL_SHARE); otherwise the standing document stands. A compaction landing past the cut
            # replaces this entry either way (the fold cannot carry one): under the share with a restored entry, which writes
            # nothing, past it with a fresh whole entry, which writes its own cut (2026-09-24)
            if standing is None or standing[0] == cut_uuid:
                return _skip("written")
            try:
                tail_now = os.stat(leaf_path).st_size - int(standing[4])   # the leaf's bytes past the standing cut: one stat
            except OSError:
                tail_now = 0
            if not (len(bounds) > standing[2] or tail_now * _ASM_TAIL_SHARE >= max(1, int(standing[1]))):
                return _skip("written")
        active = ad.active_path()
        verdicts = ad.chain_verdicts(active)

        def skip(reason):
            if reason in _ASM_SKIP_STRUCTURAL:            # a property of this cut: memoized like a success (docWritten),
                entry["docSkip"] = (cut_uuid, reason)     #  re-armed when the cut moves
                _say_once("assembly checkpoint: %s not written: %s (said once until its cut moves)" % (leaf_path, reason))
            else:                                         # a blip (a stat or a write failing, a record landing between the
                _say_once("assembly checkpoint: %s not written by the %s: %s (said once per leaf; tried again at the next %s)"
                          % (leaf_path, who, reason, who))   #  parse and the offsets): named for its caller, once per leaf
            return _skip(reason)
        def _rec_seq(a):
            return ad.seq_of.get(a.get("uuid"), 0)
        cut_seq = None
        uuid_at = {sq: u for u, sq in ad.seq_of.items()}   # seq -> uuid, for the cut record's raw-against-resolved parent check
        # EVERY record the pre-cut bytes carry, in seq order (the kept chain, the absorbed attachments, every copy of a repeated
        # uuid): the reuse and closure guards below read these, never the document's rows (round two of stage one b, 2026-09-15: a
        # pre-cut absorbed attachment whose uuid a tail record reused was no row, so neither guard saw it, and the document restored
        # silently missing that atom). A uuid seen before the cut and again at or past it blocks the cut (`reuse`); a pre-cut
        # record whose parent resolves (last-wins) past the cut blocks it (`closure`)
        all_recs, _sq = [], 0
        for _fp, _recs in ad._src.items():
            _base = None                                  # the adapter's own numbering: a SEEDED adapter (a restore's) numbers its tail
            for _k, _r in enumerate(_recs):               #  from the document's cut, so the file's base is read off any record the
                _u = _r.get("uuid") if isinstance(_r, dict) else None   #  adapter indexed (its last-wins copy), never assumed to be one
                if _u and ad.by_uuid.get(_u) is _r and _u in ad.seq_of:
                    _base = ad.seq_of[_u] - _k - 1
                    break
            if _base is None:
                _base = _sq                               # no indexed record in this file: the cumulative count (the whole parse's own)
            for _k, _r in enumerate(_recs):
                if isinstance(_r, dict) and _r.get("uuid"):
                    all_recs.append((_base + _k + 1, _r))
            _sq = _base + len(_recs)
        first_at, last_at = {}, {}
        for _sq, _r in all_recs:
            first_at.setdefault(_r["uuid"], _sq); last_at[_r["uuid"]] = _sq
        spans = sorted((a, b) for a, b in ((first_at[u], last_at[u]) for u in first_at) if a < b)   # a reused uuid: (first, last)
        def _reused_across(cand):
            return any(a < cand <= b for a, b in spans)
        def _parent_past(cand):
            for _sq, _r in all_recs:
                if _sq >= cand:
                    break
                u = _r["uuid"]
                p = ad.parent_of.get(u) if ad.by_uuid.get(u) is _r else (_r.get("parentUuid") or None)   # the last-wins copy: as the
                if p and ad.seq_of.get(p, 0) >= cand:                                                     #  parse resolves it; a shadowed
                    return True                                                                           #  copy: its raw parent, last-wins
            return False
        blocked = set()                                   # why candidates fell: the named skip when none survives
        qseqs = {q["seq"] for q in ad.qatts}              # the absorbed attachments' seqs: a turn's bytes begin at the attachments the
        #                                                   CLI spliced before its prompt, so a cut at the prompt's record leaves them
        #                                                   pre-cut (absorbed through the carry) and a cut before them is the same turn
        #                                                   boundary; both are tried, the attachment-first one when the prompt's fails
        def _cands(ti):
            seqs = [_rec_seq(a) for t in turns[ti:] for a in t["atoms"] if a.get("uuid") in ad.seq_of]
            if not seqs:
                return []
            out, c = [min(seqs)], min(seqs)
            for _k in range(len(qseqs)):                  # bounded: at most every attachment
                if (c - 1) not in qseqs:
                    break
                c -= 1
                out.append(c)
            return out
        for ti, cand in ((ti, c) for ti in range(ci, -1, -1) for c in _cands(ti)):   # the cut turn, then earlier ones while a guard fails
            first = ad.by_uuid.get(uuid_at.get(cand))
            if (first is not None and not (first.get("type") == "system" and first.get("subtype") == "compact_boundary")
                    and ad.parent_of.get(first.get("uuid")) != (first.get("parentUuid") or None)):
                continue                                  # the tail's first record chains onto the tip in the RESOLVED graph but not
                #                                           in the RAW one (the record after an adopted manual /compact pair, whose
                #                                           parent the parse re-points from the /compact stdout to the summary): the
                #                                           restore's proof walks raw parents, so this cut could never be proven; the
                #                                           turn before it is the cut (stage one b, correction 1)
            if _reused_across(cand):
                blocked.add("reuse"); continue            # a tail record reuses a uuid the pre-cut bytes carry (a row's, an absorbed
                #                                           attachment's, a shadowed copy's): the restore cannot rebuild what the parse
                #                                           makes of the pair, so the cut steps back before the first copy
            if _parent_past(cand):
                blocked.add("closure"); continue          # a pre-cut record whose RESOLVED parent lies past the cut: a ring across the
                #                                           cut, which the restore cannot rebuild (the pre-cut rows are frozen, their parent
                #                                           bound to a record the tail holds). The pre-cut part is closed under parents,
                #                                           or the cut steps back (stage one b; found by the moving-cut oracle)
            pre_ts = [ad.ts_of.get(u, 0) for u in entry["kept"] if ad.seq_of.get(u, 0) < cand and u in ad.by_uuid
                      and (ad.by_uuid[u].get("type") in ("user", "assistant") or ad.by_uuid[u].get("subtype") == "compact_boundary")]
            tail_ts = [ad.ts_of.get(u, 0) for u in entry["kept"] if ad.seq_of.get(u, 0) >= cand and u in ad.by_uuid
                       and (ad.by_uuid[u].get("type") in ("user", "assistant") or ad.by_uuid[u].get("subtype") == "compact_boundary")]
            if pre_ts and tail_ts and max(pre_ts) > min(tail_ts):
                continue                                  # a stamp out of order across the cut: the carry would not hold
            cut_seq = cand
            break
        if cut_seq is None or cut_seq <= 1 or not any(ad.seq_of.get(u, 0) < cut_seq for u in entry["kept"]):
            if blocked:
                return skip("reuse" if "reuse" in blocked else "closure")   # a guard blocked every cut that cuts anything: its name
            return skip("unsplittable")               # nothing pre-cut, or pre-cut bytes that hold no kept record (every record before
            #                                           the cut shadowed by a later copy of its uuid): no document says less than none
        if chunk():                                           # the cut is chosen; the files, their witnesses and the rows are next
            return _skip("moot")
        # the files: each one's records before the cut, its witness, and where the tail read starts
        first_seq, n = {}, 0
        for fp, recs in ad._src.items():
            first_seq[fp] = n + 1
            n += len(recs)
        files, cuts, fsid_paths, file_offs = {}, {}, {}, {}
        cut_off_total = 0                                 # the pre-cut bytes over the lineage: the churn bound's denominator
        for fp, recs in ad._src.items():
            fsid = Path(fp).stem
            fsid_paths[fsid] = fp
            try:
                st_ = os.stat(fp)
            except OSError:
                return skip("stat")
            pre_n = max(0, min(len(recs), cut_seq - first_seq[fp]))   # records of this file before the cut
            is_leaf = Path(fp).stem == Path(leaf_path).stem
            offs, ent_gen = _entry_offsets_gen(fp)
            # The reader's entry may hold MORE records than the tree's adapter read: a live LEAF grows between the settle's
            # parse and this write (the CLI appends while the settle runs), and the reader extends its entry by a new list
            # whose prefix is the very records the adapter holds, under the same generation. The document's pre-cut part
            # is that prefix, so the prefix's offsets stand for the leaf; a shorter entry, or one under another generation
            # (a rewrite, a refold from zero), does not (T396: a continuously active 120 MB session never got a document
            # while its kernel lived, since every settle's write met an entry one record longer than its tree, and every
            # boot read it whole through whichever reader came first). A LINEAGE file longer than the tree still skips
            # (round one, medium): its row would be a skip row proven by a stat alone, and a prior file that gained a record
            # after the parse (its own session resumed elsewhere, the case _asm_gates demotes as nonleaf) would be stamped
            # as wholly before the cut with the appended record missing from every restore of this leaf's kernel life.
            src_gen, src_base = ((getattr(ad, "_src_keys", {}) or {}).get(fp, (None, None)) + (None, None))[:2]
            if held_readers and fp in held_readers and (offs is None or ent_gen != src_gen):
                offs, ent_gen = _kept_reader_offsets(fp, held_readers[fp])   # the kept entry's own reader entry (review of PR 158):
                #                                            the cache's left (a quiet drop, its bounds) or was replaced by a later read
            # The prefix is accepted for the leaf under the tree's own generation AND from base zero: a seeded adapter that
            # read the file from its cut (a degenerate document whose pre-cut part holds records but no atoms restores unseen
            # as restored) holds records the entry's prefix does not begin with, so a whole reader upgrading the entry to
            # base zero under the same generation must not lend it that prefix (round two, low 1).
            if offs is None or len(offs) < len(recs) or (len(offs) > len(recs) and not (is_leaf and src_gen == ent_gen and src_base == 0)):
                return skip("offsets")
            offs = offs[:len(recs)]                       # defensive: no row index below passes len(recs), so the extra
            file_offs[fp] = offs                          #  offsets of a grown entry are never read (round one, low 3)
            pre_uuids = [r.get("uuid") for r in recs[:pre_n] if r.get("uuid")]
            f = {"path": fp, "size": st_.st_size, "mtime": st_.st_mtime, "pre": pre_n, "n": len(recs),
                 "first": pre_uuids[0] if pre_uuids else None, "last": pre_uuids[-1] if pre_uuids else None}
            if pre_n >= len(recs) and not is_leaf:
                f["skip"] = True                          # wholly before the cut: never read at restore, stat is its proof
                cut_off_total += int(st_.st_size)       # a prior file lies wholly before the cut: all of it is pre-cut bytes
                st_read = (getattr(ad, "_src_stat", {}) or {}).get(fp)
                if st_read is None:
                    return skip("stat")                   # no witness for the records the tree was parsed from: no row
                f["size"], f["mtime"] = int(st_read[0]), float(st_read[1])   # the stat AS READ, never the write-time one: a
                #                                            record appended to the prior file between the parse and this write
                #                                            must fail the next boot's verification, not be stamped away (round
                #                                            one, medium, the half that needed no reader in between)
            else:
                cut_off = offs[pre_n][0] if pre_n < len(recs) else st_.st_size
                cut_off_total += int(cut_off)           # the pre-cut bytes of this file
                try:
                    with open(fp, "rb") as fh:
                        fh.seek(max(0, cut_off - _JSONL_TAIL_GUARD))
                        guard = fh.read(cut_off - max(0, cut_off - _JSONL_TAIL_GUARD))
                except OSError:
                    return skip("stat")
                f["cut"] = [cut_off, pre_n, guard.hex()]
            files[fsid] = f
        # the pre-cut records: identity, verdict, type, order, time, file, landed
        type_code = {"user": "u", "assistant": "a", "system": "s", "attachment": "t"}
        if standing is None and not cp.exists() and cut_off_total < _ASM_FIRST_DOC_MIN:
            return skip("young")                          # the young-session floor: no first document under the floor's bytes; the memo
            #                                               re-arms as the cut moves with the settled turns, so the first write lands once
            #                                               the pre-cut part is worth a document (a standing document is never held by it).
            #                                               Decided HERE, once the files loop has summed the pre-cut bytes and BEFORE the
            #                                               rows and atom rows are built, so a young session pays the stat and the cut
            #                                               choice alone at each settle (1721 round two, low 4)
        if chunk():                                           # the files are witnessed (their offsets in hand); the rows are next
            return _skip("moot")
        rows, row_of, files_order = [], {}, {}
        for u, r in ad.by_uuid.items():                   # insertion order = read order
            sq = ad.seq_of.get(u, 0)
            if sq >= cut_seq:
                continue
            fsid = ad.fsid_of.get(u)
            fp = fsid_paths.get(fsid)
            idx = sq - first_seq[fp] if fp is not None else -1
            row_of[u] = len(rows)
            fsids = files_order.setdefault("_", [])
            if fsid not in fsids:
                fsids.append(fsid)
            rows.append([u, verdicts.get(u, "broken")[0], type_code.get(r.get("type"), "?"), r.get("subtype") if r.get("type") == "system" else None,
                         sq, ad.ts_of.get(u, 0), fsids.index(fsid), idx, 1 if u in entry["landed"] else 0, r.get("parentUuid")])
        if chunk():
            return _skip("moot")
        # the pre-cut atoms in emit order
        pre_atoms = []
        bnd_rows = {}                                     # a boundary atom's uuid -> [its row, its summary's digest]: the restore
        #                                                   holds each one to the summary the pass leaves it with no scan, and
        #                                                   decodes the row only when the digests differ (_asm_patch_pre_cut_summaries)
        for n_atom, a in enumerate(atoms):
            if n_atom and n_atom % _ASM_BG_EMIT_CHUNK == 0 and chunk():   # a rebuild's chunk point every _ASM_BG_EMIT_CHUNK
                return _skip("moot")                                         #  atoms of the rows
            u = a.get("uuid")
            sq = ad.seq_of.get(u, 0) if u in ad.seq_of else None
            if sq is None:
                if a.get("absorbed"):
                    q = next((q for q in ad.qatts if q["uuid"] == u), None)
                    if q is None or q["seq"] >= cut_seq:
                        continue
                    sq = q["seq"]
                else:
                    continue
            if sq >= cut_seq:
                continue
            kind = _atom_kind(a)
            scal = _atom_scalars(a)
            fsid = a.get("fsid")
            fp = fsid_paths.get(fsid)
            idx = (sq - first_seq[fp]) if fp is not None else -1
            ri = row_of.get(u)
            if ri is not None:                             # the record row carries uuid, type, t, fsid and parentUuid: derive them
                rr = rows[ri]
                for k_, v_ in (("uuid", u), ("fsid", fsid), ("session_id", rompuuid), ("t", rr[5]), ("parentUuid", rr[9])):
                    if scal.get(k_) == v_:
                        scal.pop(k_, None)
                if scal.get("type") == {"u": "user", "a": "assistant", "s": "system"}.get(rr[2]):
                    scal.pop("type", None)
            row = {"i": idx}
            f_offs = file_offs.get(fp)
            if f_offs is not None and 0 <= idx < len(f_offs):
                row["at"] = list(f_offs[idx])                  # (byte offset, byte length): hydrate seeks straight to it
            if scal:
                row["s"] = scal
            if ri is not None:
                row["r"] = ri
            if a.get("_seq", sq) != sq:
                row["seq"] = a.get("_seq", sq)             # an adopted boundary's emit order differs from its read order
            if kind in _LAZY_KINDS and idx >= 0 and fp is not None:
                row["lz"] = _lazy_of(a, kind, idx)
            elif kind in _LAZY_KINDS:                          # no record to read back (an absorbed attachment with no uuid, round 3):
                row["m"] = a.get("message")                    #  the body rides inline, never a lazy marker hydrate cannot fill
                if "toolUseResult" in a:
                    row["tur"] = a["toolUseResult"]
            if a.get("type") == "system" and a.get("subtype") == "compact_boundary" and u:
                bnd_rows[u] = [len(pre_atoms), _summary_digest(a.get("summary"))]
            pre_atoms.append(row)
        if chunk():                                           # the rows and the pre-cut atom rows are built; the spine is next
            return _skip("moot")
        # the pre-cut spine, root to cut: the leaf's parent chain as the PARSE resolves it (repeated uuids last-wins, a
        # self-link a root), walked with a visited set that ends at the first revisit exactly as active_path does, so the
        # document's spine is the spine the chat shows. Until 2026-09-14 the walk was hop-bounded and a cycle in the
        # resolved graph (a reused uuid closing a ring 3 to 50 records long, real in 34 of the 76 live transcripts over
        # 10 MB) refused the WHOLE document, retried at every settle; every chat build of those sessions was a cold parse
        chain, u, seen = [], ad.leaf_uuid, set()
        while u is not None and u not in seen:
            seen.add(u)
            if ad.seq_of.get(u, 0) < cut_seq and u in ad.by_uuid:
                chain.append(u)
            u = ad.parent_of.get(u)
        spine = [row_of[u] for u in reversed(chain) if u in row_of]   # record indexes, root to cut
        tip = chain[0] if chain else None                 # the pre-cut spine's tip: the first pre-cut record on the leaf's path
        on_spine = set(chain)
        tip_childless = tip is not None and not any(    # decided HERE from the RESOLVED graph (parentUuid or logicalParentUuid,
            p == tip and ad.seq_of.get(u, 0) < cut_seq and u in ad.by_uuid and u not in on_spine   # the stitch repair
            for u, p in ad.parent_of.items())           #  applied): a pre-cut child of the tip OFF the spine, a compaction
        #                                                   anchored on it included, means a tail child would decide the fork
        #                                                   (T402 round five, medium 1); the restore reads this bit, never the
        #                                                   rows' raw parents. A child of the tip that is ON the spine is the
        #                                                   ring closing on the tip (a reused uuid): a tail chaining onto it
        #                                                   reaches the tip through the same spine nodes with the same verdicts,
        #                                                   so it decides no fork and does not make the tip a fork (2026-09-14)
        seq_ts = None
        i = bisect.bisect_left(ad._seq_ts, (cut_seq,)) - 1
        if i >= 0:
            seq_ts = list(ad._seq_ts[i])
        last_ts = 0
        for u, r in ad.by_uuid.items():
            if ad.seq_of.get(u, 0) < cut_seq and parse_z(r.get("timestamp")) is not None:
                last_ts = ad.ts_of.get(u, last_ts)
        # the carry as the emit left it after the pre-cut records: re-run the pre-pass over them alone (the sets
        # are the same as the whole parse's restricted to those records, since the walk is chronological)
        st = _emit_state()
        pre_order = [u for u in _chrono(ad, entry["kept"]) if ad.seq_of.get(u, 0) < cut_seq]
        ad._prepass(pre_order, st)
        for q in ad.qatts:
            if q["seq"] < cut_seq and q["ts"] is not None and (q["uuid"] is None or q["uuid"] in entry["kept"]):
                st["absorbed_keys"].add((q["ts"], _th(" ".join(q["text"].split()))))
        fsids = files_order.get("_", [])
        if chunk():                                           # the carry is re-run; the identity proof is next
            return _skip("moot")
        pre_whole = [dict(a) for a in atoms if (ad.seq_of.get(a.get("uuid")) if a.get("uuid") in ad.seq_of else
                                                 next((q["seq"] for q in ad.qatts if q["uuid"] == a.get("uuid")), cut_seq)) < cut_seq]
        identity = _pre_tree_identity(pre_whole, rompuuid)     # the WHOLE parse's ids over the pre-cut atoms (bodies in hand)
        del pre_whole
        if chunk():
            return _skip("moot")
        pre_lazy = _restore_prefix_atoms(pre_atoms, rompuuid, rows, fsids)
        if chunk():
            return _skip("moot")
        if _pre_tree_identity(pre_lazy, rompuuid) != identity:
            return skip("reconstruction")            # the lazy reconstruction would not reproduce the whole parse's ids
        del pre_lazy
        if chunk():                                           # the identity proof holds; the turns section is next
            return _skip("moot")
        # the `turns` section (T323 stage 4c): the parsed TREE's pre-cut turns as rows, so a restore builds the turns
        # without an atom; a synthesized atom (an idle span, a salvaged reply) has no record and rides as a row of its own
        # with its message inline. The section stops at the first turn holding a post-cut record (the cut is a turn
        # boundary: the chronological split above put every pre-cut record before every post-cut one).
        turns_doc = None
        n0 = len(pre_atoms)                               # the rows the walk appends leave with a refusal (review round 2, M1)
        if tree is not None:
            row_of_atom, row_of_scalars = {}, {}
            for k_, row_ in enumerate(pre_atoms):
                u_ = rows[row_["r"]][0] if "r" in row_ else (row_.get("s") or {}).get("uuid")
                if u_ and u_ not in row_of_atom:
                    row_of_atom[u_] = k_
                elif not u_ and "r" not in row_:            # a uuid-less absorbed atom (a queued_command attachment): its row is
                    row_of_scalars.setdefault(json.dumps(row_.get("s") or {}, sort_keys=True, default=str), k_)   #  found by its scalars
            turns_doc = []
            cut_ts = min((ad.ts_of.get(u, 0) for u in entry["kept"] if ad.seq_of.get(u, 0) >= cut_seq), default=None)
            for n_turn, turn in enumerate(tree.get("turns") or []):
                if n_turn and n_turn % 1024 == 0 and chunk():   # a rebuild's chunk point every 1,024 turns of the section
                    return _skip("moot")
                t_uuids = turn["uuids"] if turn.get("pre") else [a.get("uuid") for a in turn["atoms"]]   # a restored turn: no atom built
                seqs = [ad.seq_of[u_] for u_ in t_uuids if u_ in ad.seq_of]
                if seqs:
                    if max(seqs) >= cut_seq:
                        break                            # the first turn holding a post-cut record ends the pre-cut run
                elif cut_ts is None or (turn.get("t") or 0) >= cut_ts:
                    break                                # a turn of synthesized atoms alone (no record of its own): pre-cut
                #                                          by its time, else the tail's
                idxs = []
                for ai_, u_ in enumerate(t_uuids):
                    a = turn["atoms"][ai_] if not u_ or u_ not in row_of_atom or not turn.get("pre") else None   # a restored turn's
                    #                                    atom is built only when it is uuid-less or unknown; a fresh turn's is in hand
                    salvaged = a is not None and bool(a.get("orphaned"))   # a salvaged reply (synthesize_orphans): synthesized, whatever
                    #                                    uuid it carries. Its marker can name a transcript record (a local command's
                    #                                    streamed output, kept as a system/local_command row the emit skips, or an
                    #                                    empty twin of the reply), and looked up by that uuid it found no row of its own
                    #                                    and refused the document as turnRows at every write for the leaf's life (a
                    #                                    203 MB live leaf read whole at every boot, 2026-10-01), or took the twin's row
                    k_ = None if salvaged else (row_of_atom.get(u_) if u_ else
                                                row_of_scalars.pop(json.dumps(_atom_scalars(a), sort_keys=True, default=str), None))
                    #  (two uuid-less attachments in one turn with the same enqueue stamp collide on the scalar key: the second
                    #   finds no row, the walk mints a synthesized one, and the coverage check refuses the section cleanly)
                    if k_ is None:
                        if u_ in ad.seq_of and not salvaged:
                            return skip("turnRows")      # a record atom with no row of its own: the tree and the entry disagree
                        k_ = len(pre_atoms)
                        row_ = {"i": -1, "syn": 1, "s": _atom_scalars(a)}   # a synthesized atom's row: its scalars, its message inline
                        if "message" in a:                                #  when it has one (an idle span has none)
                            row_["m"] = a["message"]
                        pre_atoms.append(row_)
                    idxs.append(k_)
                seg_rows, start = [], 0
                pre_segs = {s_[0]: s_ for s_ in (turn.get("segs") or [])} if turn.get("pre") else {}
                for sg in segments(turn):
                    ps_ = pre_segs.get(sg["id"])
                    if ps_ is not None and len(ps_) >= 9:            # a restored turn: its stored verdicts carry over, no atom built
                        w_, mids_, hp_ = ps_[6], ps_[7], ps_[8]
                    else:                                             # w is atom_has_work's verdict at write time (see _ASM_CKPT_V)
                        w_ = 1 if any(atom_has_work(a) for a in sg["atoms"]) else 0
                        mids_ = [m_ for a in sg["atoms"] for m_ in atom_mids(a)]
                        pa_ = seg_prompt_atom(sg)                     # hp: whether a MESSAGE caption is wanted (the planner's rule)
                        hp_ = 1 if pa_ is not None and pa_.get("author") == "human" else 0
                    seg_rows.append([sg["id"], sg.get("trigger"), sg["t"], sg["end"], start, len(sg["atoms"]), w_, mids_, hp_])
                    start += len(sg["atoms"])
                if turn.get("pre") and turn.get("pcs") is not None:
                    pcs_, hT_ = [[u_, n_] for u_, n_ in turn["pcs"].items()], turn.get("hT")
                    ts_ = [turn.get("lastT")] if turn.get("lastT") else []
                    ts_max, models, tools_ = turn.get("maxT"), ([turn["lastModel"]] if turn.get("lastModel") else []), [list(x) for x in turn.get("tools") or []]
                    trig_at = t_uuids.index(turn["trigger"]["uuid"]) if turn.get("trigger") and turn["trigger"].get("uuid") in t_uuids else None
                else:
                    pcs_ = [[u_, n_] for u_, n_ in ((a.get("uuid"), atom_prose_chars(a)) for a in turn["atoms"]) if u_ and n_ > 0]
                    hT_ = max((a.get("t", 0) for a in turn["atoms"] if a.get("type") == "user" and a.get("author") == "human"
                               and not is_interrupt_record(a)), default=None)
                    ts_ = [a.get("t") for a in turn["atoms"] if a.get("t")]
                    ts_max = max(ts_) if ts_ else None
                    models = [atom_model(a) for a in turn["atoms"] if a.get("type") == "assistant"]
                    models = [m_ for m_ in models if m_]
                    tools_ = [[i_, n_] for a in turn["atoms"] if a.get("type") == "assistant" for i_, n_ in atom_tool_uses(a)]
                    trig_at = next((i_ for i_, a in enumerate(turn["atoms"]) if turn["trigger"] is not None and a is turn["trigger"]), None)
                    if trig_at is None and turn["trigger"] is not None:
                        trig_at = next((i_ for i_, a in enumerate(turn["atoms"]) if a.get("uuid") == turn["trigger"].get("uuid")), None)
                if start != len(turn["atoms"]):
                    return skip("segs")                  # the segments are contiguous slices of the turn, in order
                turns_doc.append({"id": turn["id"], "t": turn["t"], "end": turn["end"], "ended": bool(turn["ended"]), "atoms": idxs,
                                  "segs": seg_rows, "uuids": list(t_uuids), "triggerAt": trig_at,
                                  "lastT": ts_[-1] if ts_ else None, "maxT": ts_max,
                                  "lastModel": models[-1] if models else None, "tools": tools_,
                                  "pcs": pcs_, "hT": hT_})
            # the section must COVER the pre-cut rows: every row in exactly one turn (a permutation of the row indexes; the
            # turns sort by time, so a row's index need not be contiguous with its neighbours'), else the tree the store
            # holds is not this entry's whole (a bare rollback armed before the last compaction cuts it short) and the
            # document is written without a section rather than restore a session missing history (review medium 1)
            if turns_doc and sorted(k_ for td in turns_doc for k_ in td["atoms"]) != list(range(len(pre_atoms))):
                _asm_ckpt_skip("turnsCoverage")
                turns_doc = None
            if not turns_doc:
                turns_doc = None                          # nothing before the cut in this tree: the atoms-only form
                del pre_atoms[n0:]                        # …whose rows are the emit's alone, the identity's count (M1)
        doc = {"av": _ASM_CKPT_V, "path": os.path.realpath(str(leaf_path)), "rompuuid": str(rompuuid), "sdkHuman": bool(sdk_human),
               "cands": list(entry["cands"]), "links": dict(entry["links"]), "files": files, "fsids": fsids, "cutSeq": cut_seq,
               "records": rows, "atoms": None,             # v6: string rows, built inside the guard below (an unencodable row
               "spine": spine, "seqTs": seq_ts, "lastTs": last_ts,   #  is the counted `unencodable` skip, never a raise)
               "xu": sorted({_r["uuid"] for _sq, _r in all_recs if _sq < cut_seq} - set(row_of)),   # the pre-cut uuids that are no row
               #                                             (absorbed attachments, shadowed copies): the restore's reuse check reads
               #                                             rows and these, so a tail record reusing any of them refuses (round two)
               "turns": turns_doc, "treeIdentity": _tree_identity_of_doc(turns_doc, identity) if turns_doc else None,
               "gates": {"prompt_ids": sorted(ad.prompt_ids), "boundary_pids": sorted(_asm_doc_boundary_pids(ad, all_recs, cut_seq)),
                         "compact_pids": sorted(_asm_doc_compact_pids(ad, all_recs, cut_seq)),   # 2026-10-05, optional: no
                         #                    version bump, as an older kernel reads the gates by name and never looks at it,
                         #                    and this one reads a document lacking it by its prompt_ids, a superset that only
                         #                    refuses more (_asm_pid_compact_episode)
                         "skill_use_ids": sorted(ad.skill_use_ids), "src_tool_links": sorted(ad.src_tool_links),
                         "dangling": sorted(ad.dangling)},
               "carry": _carry_encode(st), "identity": identity, "t": time.time(), "tipChildless": bool(tip_childless),
               "bnd": bnd_rows}                           # optional (2026-11-12): a document without it is scanned at its restore
        _pst = entry.get("st") or {}
        doc["postalWait"] = bool(_pst.get("postal_miss_rec") or _pst.get("postal_miss_att"))   # a postal author waits on the log
        #                                               (the test the re-seat mark below makes): the document carries the provisional
        #                                               author and no heal state, so a compaction on an entry restored from it, or on
        #                                               this entry after its heal (docPostalWait, below), parses whole, where the
        #                                               whole entry heals the author once the log catches up (_assemble, 2026-09-24).
        #                                               Written either way: a document with no such key was written before the
        #                                               writer recorded the wait, and the restore counts it as one written while an
        #                                               author waited (_asm_restore_inner, 2026-09-25)
        if chunk():                                           # the document is built; its encode is next
            return _skip("moot")
        try:
            enc = []
            for n_row, r_ in enumerate(pre_atoms):        # the rows first: the same guard
                if n_row and n_row % _ASM_BG_EMIT_CHUNK == 0 and chunk():   # a rebuild's chunk point every _ASM_BG_EMIT_CHUNK
                    return _skip("moot")                                     #  rows: about half the encode's hold on the
                enc.append(json.dumps(r_, separators=(",", ":")))           #  interpreter (the review of PR #370; the dump of
            doc["atoms"] = enc                                               #  the whole document below is one call)
            text = json.dumps(doc, separators=(",", ":"))
        except TypeError:
            return skip("unencodable")
        if chunk():                                           # encoded; the compression (zlib lets the interpreter go) is next
            return _skip("moot")
        data = gzip.compress(text.encode("utf-8"), compresslevel=6)   # identities and hashes compress about five to one; the
        if len(data) > _ASM_CKPT_CAP:                                  #  bytes a boot reads are the compressed ones
            _lf = files.get(Path(leaf_path).stem) or {}               # the verdict outlives this entry and this cut (see
            _asm_oversize_note(leaf_path, int((_lf.get("cut") or [_lf.get("size") or 0])[0]), len(data), len(rows))   # _ASM_OVERSIZE),
            entry["docSkip"] = (cut_uuid, "oversize")                 #  and says the numbers once
            return _skip("oversize")
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = _writer_tmp(cp)
            tmp.write_bytes(data)
            if stand_down is not None and stand_down():
                try:
                    tmp.unlink()                          # another writer's document stands now: it is left alone, never written over
                except OSError:
                    pass
                return _skip("moot")
            _t_write = time.monotonic()                   # the window's edge: a refusal stamped before this was against the document
            os.replace(tmp, cp)                           #  the replace retires (popped below); one stamped after it stands
            entry["docPostalWait"] = bool(doc.get("postalWait"))   # the document on disk from here: a compaction on this WHOLE
            #                                                        entry parses whole while it carries a provisional postal author,
            #                                                        even once the entry's own heal has retired the miss, since the
            #                                                        restore would read this document, not the healed entry; kept
            #                                                        before the sidecar write, so a sidecar that fails (the `write`
            #                                                        skip) leaves no stale record (_assemble, 2026-09-25)
            meta = cp.with_name(cp.name + ".meta")            # {"av", "path"}: what the boot sweep reads, never the document
            _asm_retire_refusal_mark(meta)                    # a refusedStanding mark for the cut this write replaces is history
            mtmp = _writer_tmp(meta)
            mtmp.write_text(json.dumps(_asm_sidecar(doc)))   # the inputs' fsids and whether resume links joined them: what
            #                                                   asm_document_seeds reads, never the document
            os.replace(mtmp, meta)
        except OSError:
            return skip("write")
        entry["docWritten"] = True
        _asm_oversize_clear(leaf_path)                    # a document stands: a verdict a smaller cap or an older file left is history
        entry["docCut"] = (cut_uuid, int(cut_off_total), sum(1 for b_ in bounds if ad.seq_of.get(b_.get("uuid"), 0) < cut_seq), cut_seq,
                           int(((files.get(Path(leaf_path).stem) or {}).get("cut") or [0])[0]))
        with _ASM_CKPT_LOCK:
            _ASM_LAST_WRITE_CUT[os.path.realpath(str(leaf_path))] = entry["docCut"][4]   # what the refusal road compares its offer against
        #                                                   the cut's first uuid, the pre-cut bytes over the lineage, the boundaries before
        #                                                   it, the cut seq, the leaf's cut offset: the churn bound reads them at the next
        #                                                   settle (stage one b)
        with _ASM_CKPT_LOCK:                              # a fresh document stands: a later parse with none is noDocument, not an
            _rk = os.path.realpath(str(leaf_path))        #  OLD refusal (T398 follow-up, low 1); a refusal a judge recorded inside this
            _rv = _ASM_CKPT_REFUSED.get(_rk)              #  write's window (against the document just published) stays (low B)
            if _rv is not None and _rv[1] < _t_write:
                _ASM_CKPT_REFUSED.pop(_rk, None)
        entry["docTurns"] = bool(turns_doc)              # the turns section was written (T323 stage 4c)
        entry["docNoTurns"] = _tree_key(tree) if (tree is not None and not turns_doc) else None   # …or this tree yields none
        _leaf_f = files.get(Path(leaf_path).stem) or {}
        _tail = int(_leaf_f.get("size") or 0) - int((_leaf_f.get("cut") or [0])[0])
        _st = entry.get("st") or {}
        entry["reseat"] = (not entry.get("keepWhole") and _tail * _ASM_TAIL_SHARE < max(1, int(cut_off_total))
                           and not (_st.get("postal_miss_rec") or _st.get("postal_miss_att")))
        #                                                   the next parse re-seats this entry on the document (_assemble, 2026-09-24): a
        #                                                   whole entry kept folding the whole history for the life of the process. Not
        #                                                   when a restore refused the leaf's document (keepWhole), nor when the tail
        #                                                   past this cut already meets the churn bound's share: the restored entry would
        #                                                   demote at its first fold (tailShare) and, under a turn that stays open, the
        #                                                   whole parse would write the same cut again, a loop of whole parses. Nor while
        #                                                   a postal marker is unresolved: the document carries the provisional author and
        #                                                   no heal state, so the whole entry heals the atom when the log catches up and a
        #                                                   re-seat would freeze it (review low 1); the rewrite after the heal marks it
        with _ASM_CKPT_LOCK:
            _ASM_CKPT_STATS["written"] += 1
        return True


_ASM_REFRESH = {"inflight": None,    # the cache key of the leaf whose document refresh is running (one at a time: each is a whole
                "paused": None}      #  parse), under _ASM_LOCK; set by asm_refresh_claim, cleared by asm_refresh_document. `paused`:
#                                      the key of a rebuild waiting at its gate, which lent the claim out meanwhile (_asm_bg_yield)
_ASM_REFRESH_STATS = {"claimed": 0, "written": 0, "refused": 0, "failed": 0, "ms": 0.0}   # asmCheckpoint.refresh on /perf


def asm_refresh_claim(leaf_path, rompuuid, sdk_human=False):
    """Claim the document refresh for `leaf_path` when it is due, and say whether it was claimed. Due: the leaf's assembly
    entry was RESTORED from a document that carries a turns section (a boot's restore, or a restore after a demotion that
    was not a re-seat), so the fold's gate does not hold it to the churn bound (that gate reads the atoms-only form and a
    re-seated entry only, _asm_gates), and the leaf's bytes past the document's cut, times _ASM_TAIL_SHARE, have reached the
    document's pre-cut bytes (after a refresh in this process: the bytes past the point that refresh started from, against
    the pre-cut bytes of the document it wrote). Claimed, the caller runs asm_refresh_document next, on a thread of its own,
    and that call releases the claim. Not claimed when not due, when the leaf has no such entry, or while any refresh runs."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _ASM_LOCK:
        entry = _ASM_CACHE.get(key)
        if _ASM_REFRESH["inflight"] is not None or entry is None:
            return False
        if not entry.get("preTurns") or entry.get("prefix") or entry.get("reseated") or entry.get("docPre") is None:
            return False                                  # a whole entry writes at its settle; the gate holds the other restored ones
        pre, off = entry.get("refreshAt") or (entry["docPre"], entry.get("docCutOff") or 0)
    try:
        tail = os.stat(leaf_path).st_size - int(off)
    except OSError:
        return False
    if tail <= 0 or tail * _ASM_TAIL_SHARE < max(1, int(pre)):
        return False
    if asm_oversize_stands(leaf_path) is not None:
        return False                                      # its next document is past the cap: the standing one stands (2026-11-12)
    with _ASM_LOCK:
        if _ASM_REFRESH["inflight"] is not None or _ASM_CACHE.get(key) is not entry:
            return False
        _ASM_REFRESH["inflight"] = key
    with _ASM_CKPT_LOCK:
        _ASM_REFRESH_STATS["claimed"] += 1
    return True


def asm_refresh_release(leaf_path, rompuuid, sdk_human=False):
    """Release a claim that asm_refresh_document never got to run (its caller raised first); a no-op for any other leaf."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _ASM_LOCK:
        if _ASM_REFRESH["inflight"] == key:
            _ASM_REFRESH["inflight"] = None
    _asm_bg_wake()                                        # a rebuild waiting for the claim back takes it


def asm_build_document(leaf_path, rompuuid, sdk_human=False, candidate_files=None, states=None, postal_log=None, now=None,
                       rebuild=None, refuse=None):
    """The document-building core of a refresh or a rebuild, over plain arguments and nothing of the caller's process: a WHOLE
    parse of the leaf on the private road (no entry installed, no key lock), then the writer over that parse. One
    implementation for the two places that run it (2026-11-12): asm_refresh_document on a thread of the kernel, and the
    document worker (kernel/doc_worker.py), a process of its own that the kernel hands the same arguments. It touches no
    claim, no counter of the refresh or the rebuild and no live entry: the caller books the outcome (asm_document_handback).
    `refuse`, when given, is asked once at the replace, after the parse and the encode: a true answer (its text is the
    reason) publishes nothing and the outcome is `failed` (the worker's memory bound: a document built while an allocation
    may have failed is not one to keep). Returns {"outcome": written | refused | declined | moot | failed, "wrote", "reason"
    (the writer's skip reason or the error), "tb" (a rebuild's traceback), "newPre" (the new document's pre-cut bytes) and
    "docCut" (the written document's cut, as the private entry holds it)}: JSON-shaped, since the worker prints it."""
    wrote, outcome, new_pre, reasons, tb, dc, refused = False, "failed", None, [], None, None, []

    def stands():                                         # asked at the replace, after the writer's last wait
        why = refuse() if refuse is not None else None
        if why:
            refused.append(str(why))
            return True
        return rebuild is not None and asm_document_stands(leaf_path)
    try:
        _asm_bg_yield()                                   # (a cycle may have begun since the cycle end that started it)
        if rebuild is not None and asm_document_stands(leaf_path):
            outcome = "moot"                              # the settle or the converge pass wrote it since the claim: no parse
        else:
            priv = []
            tree = parse_session(leaf_path, rompuuid=rompuuid, candidate_files=candidate_files, states=states, postal_log=postal_log,
                                 now=now, sdk_human=sdk_human, private_entry_out=priv)
            _asm_bg_yield()
            if rebuild is not None and asm_document_stands(leaf_path):
                outcome = "moot"                          # asked again AFTER the parse (the review of PR #367): a page's whole parse
                #                                           that ran meanwhile left an entry the converge pass wrote the document
                #                                           from, and the rebuild then wrote it a second time over that one
            else:
                if priv:
                    wrote = bool(asm_checkpoint_write(leaf_path, rompuuid, sdk_human, tree=tree, reason_out=reasons,
                                                      who="refresh" if rebuild is None else "rebuild", from_entry=priv[0],
                                                      held_readers=priv[0].get("heldReaders"),
                                                      stand_down=stands if (rebuild is not None or refuse is not None) else None))
                if refused:
                    outcome = "failed"                    # the caller's bound said no at the replace: nothing was published
                    reasons.append(refused[-1])
                elif not wrote and rebuild is not None and reasons and reasons[-1] == "moot":
                    outcome = "moot"
                else:
                    outcome = "written" if wrote else ("refused" if rebuild is None else "declined")
                dc = priv[0].get("docCut") if wrote else None
                new_pre = int(dc[1]) if dc else None
    except Exception as e:                                # noqa: BLE001 - a refresh is an optimization: the standing document stands
        if rebuild is None:
            _say_once("assembly checkpoint: the refresh of %s failed (%r); the standing document stands" % (leaf_path, e))
        else:
            reasons.append(repr(e))
            tb = traceback.format_exc()
    return {"outcome": outcome, "wrote": bool(wrote), "reason": reasons[-1] if reasons else None, "tb": tb, "newPre": new_pre,
            "docCut": list(dc) if dc else None}


def asm_document_job(leaf_path, rompuuid, sdk_human=False):
    """The ticket of one document job, taken BEFORE its parse begins and handed to asm_document_handback when it ends: the
    claim's key, the leaf's live entry and stat as the job began, and the two clocks its bookkeeping reads (the job's
    duration; a refusal stamped before the job began is one its document retires)."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    t0 = time.perf_counter()
    with _ASM_LOCK:
        live = _ASM_CACHE.get(key)
    return {"leaf": leaf_path, "rompuuid": rompuuid, "key": key, "live": live, "st0": _asm_rebuild_stat(leaf_path), "t0": t0,
            "mono": time.monotonic()}


def asm_document_handback(job, rebuild, res, external=False):
    """A document job is over (`job`: asm_document_job's ticket; `res`: asm_build_document's result, None when the job never
    produced one, which reads as failed): the entry's refresh baseline moves (a refresh; see asm_refresh_document), the shared
    claim is released, the outcome is counted (a refresh under asmCheckpoint.refresh, a rebuild through _asm_rebuild_done,
    which says it and memoizes a decline) and the rebuild starter is woken. One implementation for both roads.

    `external`: ANOTHER PROCESS ran the build (the document worker), so this process's writer never ran and the three things
    it keeps per leaf are brought level here. The oversize verdict's memo is dropped (read once per leaf per process: the
    worker's writer may have removed the verdict file, after a write, or written one, past the cap), so the next question
    reads the file. After a write, the cut of the document last published for the leaf is recorded (what the refusal road
    compares its offered rewrite against), and a refusal of the leaf's previous document recorded before this job began is
    retired (a fresh document stands: a later parse with none is noDocument, not an old refusal). Only one stamped before
    the job BEGAN: the worker's replace happened at some moment of the job this process cannot place, and a refusal that
    stays one job too long costs a label on /perf, never a parse. asmCheckpoint.written stays this process's writer's own
    count (the worker's writes are under docWorker and rebuild.written). The restore is not done here: the next parse of
    the leaf finds the document and restores from it, as after any writer."""
    res = res if isinstance(res, dict) else {}
    key, live, st0 = job["key"], job["live"], job["st0"]
    outcome, new_pre = str(res.get("outcome") or "failed"), res.get("newPre")
    size0 = st0[0] if st0 is not None else None
    if external:
        dc = res.get("docCut")
        with _ASM_CKPT_LOCK:
            _ASM_OVERSIZE.pop(key[0], None)
            if outcome == "written":
                if dc and len(dc) > 4:
                    _ASM_LAST_WRITE_CUT[key[0]] = int(dc[4])
                rv = _ASM_CKPT_REFUSED.get(key[0])
                if rv is not None and rv[1] < job["mono"]:
                    _ASM_CKPT_REFUSED.pop(key[0], None)
    with _ASM_LOCK:
        if live is not None and size0 is not None and rebuild is None:   # the baseline moves on every attempt: a refusal or a
            pre0 = (live.get("refreshAt") or (live.get("docPre") or 0, 0))[0]   # failure is not retried at the next settle, only
            live["refreshAt"] = (int(new_pre) if new_pre is not None else int(pre0), int(size0))   # after the next share
        if _ASM_REFRESH["inflight"] == key:
            _ASM_REFRESH["inflight"] = None
        if rebuild is not None and _ASM_REFRESH["paused"] == key:
            _ASM_REFRESH["paused"] = None                 # (a rebuild that left its gate without the claim back: none is lent now)
    ms = (time.perf_counter() - job["t0"]) * 1000.0
    if rebuild is None:
        with _ASM_CKPT_LOCK:
            _ASM_REFRESH_STATS[outcome] = _ASM_REFRESH_STATS.get(outcome, 0) + 1
            _ASM_REFRESH_STATS["ms"] += ms
    else:
        _asm_rebuild_done(job["leaf"], job["rompuuid"], rebuild, outcome, st0, ms, res.get("reason"), tb=res.get("tb"))
    _asm_bg_wake()                                        # the claim is back: a waiting rebuild takes it (a refresh ran on its lent
    #                                                       claim), and the rebuild starter starts the next one, a retry the outcome
    #                                                       above put back on the table included (2026-11-12)


def asm_refresh_document(leaf_path, rompuuid, sdk_human=False, candidate_files=None, states=None, postal_log=None, now=None,
                         rebuild=None):
    """The refresh a claim granted (asm_refresh_claim): a WHOLE parse of the leaf on the private road, which installs no entry and
    takes no key lock, so the leaf's restored entry and every reader of it carry on untouched, then the writer over that parse,
    so the document's cut moves up to the turn before the last settled one. Returns True when a document was written.
    `rebuild`, when given, is the reason a parse noted the leaf for a document REBUILD (asm_rebuild_claim granted it: "refused",
    "noDocument" or "overCap"): the same road for a leaf with no document at all, counted under asmCheckpoint.rebuild, said once
    per leaf, and a decline memoized at the leaf's stat (see _ASM_REBUILD).

    Why it exists (2026-10-01): a restored entry's cut moved only through a whole parse that REPLACED the entry (a demotion,
    a compaction past the share), so a session restored at every boot kept the document it first got and the tail past its
    cut grew without bound: a 133 MB session restored a 36 MB tail five days after its document was written, and the first
    chat build after every boot rendered all 545 turns of it. Here the whole parse runs off the pusher, on the caller's own
    thread, and only at a settle once the share is reached; the entry in hand is left as it is for this process and the
    next boot restores from the new document. After the attempt, written or not, the entry carries the new baseline
    (`refreshAt`: the new document's pre-cut bytes and the leaf's size when the refresh began), so the next refresh is due only
    once the leaf has grown past that point by the share again: a cut that cannot move (one long open turn) costs a logarithm
    of the growth, never a whole parse per settle. The claim is released whatever happens."""
    job = asm_document_job(leaf_path, rompuuid, sdk_human)
    res, gated0, claim0 = None, getattr(_ASM_BG_TL, "gated", False), getattr(_ASM_BG_TL, "claim", None)
    if rebuild is not None:
        _ASM_BG_TL.gated = True                           # a rebuild waits for the pusher's sleep at its chunk points, and lends
        _ASM_BG_TL.claim = job["key"]                     #  its claim to a refresh while it waits (_asm_bg_yield)
    try:
        res = asm_build_document(leaf_path, rompuuid, sdk_human, candidate_files=candidate_files, states=states,
                                 postal_log=postal_log, now=now, rebuild=rebuild)
    finally:
        _ASM_BG_TL.gated, _ASM_BG_TL.claim = gated0, claim0
        asm_document_handback(job, rebuild, res)
    return bool(res and res.get("wrote"))


# THE DOCUMENT REBUILD (2026-10-23). A whole parse that finds a leaf's document REFUSED (every document a version bump outdates is
# refused and unlinked at the deploy boot) or MISSING, on a leaf at or over the rebuild floor, notes the leaf here once its parse is
# done, and the kernel starts its rebuild at the pusher's next cycle end (the background gate below) on the refresh's own road,
# largest first and one at a time (the refresh's claim is shared):
# a private whole parse that installs nothing and takes no key lock, then the writer over it, with its proofs and its atomic
# replace. Before this, a document was written only from a whole entry the settle or the converge pass still held in memory, so
# after ce361ecd9 (PR #313) moved the version to 9, ten to sixteen sessions per boot parsed whole for want of a document (about
# 1.2 GB) for three days, and the largest session, idle and too big for the converge pass's cycle budget, went six kernel lives
# without one. The kernel leaves a leaf to the converge pass while that pass can write it from the boot's own read (no second
# parse), and hands it one the pass can never fit (`overCap`).
_ASM_REBUILD = {}               # (realpath, rompuuid) -> {"path": the leaf as handed, "why": the reason it was noted}: waiting
_ASM_REBUILD_DECLINED = {}      # (realpath, rompuuid) -> the leaf's (size, mtime_ns) when the writer declined its rebuild, or the
#                                 rebuild failed: not noted again until the leaf moves (a cut that cannot be written costs one
#                                 private parse per leaf state, never one per parse)
_ASM_REBUILD_BLIPPED = set()    # (realpath, rompuuid) whose rebuild the writer declined for a blip (_ASM_SKIP_BLIP): tried once more
_ASM_REBUILD_RAISED = {}        # (realpath, rompuuid) -> the error of a rebuild that raised once: tried once more at the next
#                                 pusher cycle's end, as a blip is; a second raise is final for the leaf's stat and said loudly
_ASM_REBUILD_MIN = _env_or("ROMP_ASM_REBUILD_MIN_KB", _CKPT_FOLD_CAP // 8, 1024)   # the rebuild floor: the young-session floor's value
#                                 (_ASM_FIRST_DOC_MIN, an eighth of the fold cap, 1 MB by default) as a knob of its own, so the suite's
#                                 floor-off knob does not make every small fixture a rebuild; below it a whole parse costs milliseconds
_ASM_REBUILD_STATS = {"noted": 0, "claimed": 0, "written": 0, "declined": 0, "failed": 0, "retry": 0, "moot": 0, "gone": 0,
                      "small": 0, "released": 0, "subagent": 0, "oversize": 0, "ms": 0.0, "bytes": 0,
                      "yields": 0, "yieldMs": 0.0, "lent": 0}   # asmCheckpoint.rebuild on /perf (`pending` rides beside, a gauge);
#                                 yields: the rebuild's waits at its chunk points for the pusher's next sleep, yieldMs their wall, lent:
#                                 those a refresh's run on the lent claim prolonged (_asm_bg_yield)
_ASM_REBUILD_DRAIN = {"written": 0, "declined": 0, "failed": 0, "bytes": 0, "ms": 0.0, "running": 0}   # since the last summary line


# THE BACKGROUND GATE (2026-10-23, the review of PR #367). A rebuild competes with the pages for the interpreter, not for a core: on
# a machine with cores to spare, nice 19 bought nothing, and in a lab a page's 40 MB whole parse took 2.1 times as long while a
# rebuild ran, with full collections of 0.65 to 1.04 s over the rebuild's heap. So a rebuild runs only while the pusher sleeps: the
# pusher shuts the gate as each cycle begins and opens it as each cycle ends (_asm_rebuild_tick), when it goes to sleep until its
# next wake, and starts the next rebuild there. Until the review of PR #370 only an IDLE cycle's end opened it (one that sent
# nothing and changed nothing, the event the frozen heap's reclaim waits for): on a host of 65 sessions a tenth of the cycles are
# idle, the gate stood open about 45 s an hour, and the 824 MB session's rebuild took about two hours, all 28 transcripts over
# 20 MiB nine to eleven, longer than a kernel lasts between deploys there. A busy cycle's end opens it too, since the pusher is
# asleep after it as much as after an idle one, and the pusher loses nothing. Foreground work in flight holds the gate shut
# whatever the pusher says: a parse of a session on any other thread (parse_session: a page's, a judge's, the boot's) and a page
# connect's push (asm_foreground). A running rebuild waits at its chunk points (_asm_bg_yield) until the gate is open again:
# before its parse, after the read, every _ASM_BG_EMIT_CHUNK atoms of the emit, after the parse, and between the writer's phases.
# Inside a phase it runs on (the read under the reader's stripe lock, one identity proof, the compression), so the longest stretch
# a page can still meet is one phase, never the whole rebuild: about 2 s of a 79 MB leaf's 25 s rebuild in a lab, where a page's
# 26 MB parse that took 2.4 to 2.5 times as long beside a rebuild took 1.2 to 1.8 times. A waiting rebuild lends the shared claim
# out (the review of PR #370: it held it through every wait, and a rebuild that waits for hours refused every settle's refresh of
# every session for those hours), so a refresh runs meanwhile, and takes the claim back before it goes on.
#
# THE GATE'S OPENING STARTS THE NEXT REBUILD (2026-11-12). Until then a rebuild started only AT the pusher's cycle end, so a gate that
# was shut at that one instant (a parse on another thread) waited for the next cycle end, and nothing looked again when it opened a
# moment later: on a host whose cycles ran 88 to 565 s after a version bump, three cycle ends in 13.5 minutes each met the timeline
# builder mid-parse, and no rebuild ran (79 noted, none claimed). Every opening of the gate, every leaf noted and every release of
# the claim now bumps a generation (`gen`) and wakes the kernel's rebuild starter (asm_rebuild_wait), which starts the next rebuild
# then. And the kernel's BACKGROUND loops (the timeline builder, the judges, the jobs pass, the boot's warm parses: _ASM_BG_STAGES)
# no longer hold the gate shut: nobody waits on them, and their whole parses, which run for minutes exactly while the documents
# are missing, kept the rebuild that would end them from starting or going on. A page's parse and a page connect's push still do.
_ASM_BG_COND = threading.Condition()
_ASM_BG = {"idle": False, "fg": 0, "waiting": 0, "gen": 0}   # idle: the pusher is asleep, its last cycle ended and no cycle has
#                                                     begun since; fg: foreground work in flight; waiting: rebuild threads waiting at
#                                                     the gate; gen: bumped (under the condition, with a notify) at every gate
#                                                     opening, leaf noted and claim released, what the rebuild starter waits on
_ASM_BG_STAGES = ("timeline.build", "judge.", "producer", "jobs.", "warm.", "rewind.")   # the kernel's stage marks (T401) of the
#                                                     loops whose parses never shut the gate: the timeline builder, the judge tiers
#                                                     and their pool workers (which carry the tier's mark), the producer, the jobs
#                                                     pass, the boot's warm parses and its rewind passes. A thread with no mark, or
#                                                     any other (a page's request, the pusher's cycle, a connect), is foreground
_ASM_BG_TL = threading.local()   # .gated on the rebuild's own thread (asm_refresh_document with `rebuild`): its chunk points wait;
#                                  .claim, the claim's key that thread holds and lends out while it waits
_ASM_BG_EMIT_CHUNK = 4096        # atoms of the rebuild's emit between two chunk points (about 40 ms of a 27 MB leaf's emit)


def asm_background_idle(idle):
    """The pusher's edges for the background gate: False as a cycle begins, True as a cycle ends and the pusher goes to sleep
    until its next wake (any cycle's end, idle or busy, since the review of PR #370). Wakes a rebuild waiting at a chunk point
    when the gate opens."""
    with _ASM_BG_COND:
        _ASM_BG["idle"] = bool(idle)
        if idle and not _ASM_BG["fg"]:
            _asm_bg_kick_locked()


class asm_foreground:
    """Foreground work in flight for the span of the block (a parse that is not the rebuild's own, a page connect's push): the
    background gate stays shut until every such block has left. Nests and counts; a no-op on a rebuild's own thread, which never
    waits on itself."""

    def __enter__(self):
        self._on = not getattr(_ASM_BG_TL, "gated", False)
        if self._on:
            with _ASM_BG_COND:
                _ASM_BG["fg"] += 1
        return self

    def __exit__(self, *a):
        if self._on:
            with _ASM_BG_COND:
                _ASM_BG["fg"] = max(0, _ASM_BG["fg"] - 1)
                if not _ASM_BG["fg"] and _ASM_BG["idle"]:
                    _asm_bg_kick_locked()             # the gate opens HERE too: the rebuild starter looks now (2026-11-12)
        return False


def asm_background_open():
    """Whether a background rebuild may run now: the pusher is asleep (its last cycle ended and none has begun since), and no
    foreground work is in flight."""
    with _ASM_BG_COND:
        return bool(_ASM_BG["idle"]) and not _ASM_BG["fg"]


def _asm_bg_kick_locked():
    """Under _ASM_BG_COND: something a rebuild's start or a waiting rebuild depends on changed (the gate opened, a leaf was
    noted, the claim came back). The generation moves and every waiter looks again."""
    _ASM_BG["gen"] += 1
    _ASM_BG_COND.notify_all()


def _asm_bg_wake():
    """Wake the rebuild waiting at its gate, and the rebuild starter, to look again: the claim is back (a refresh or a rebuild
    released it) or a leaf was noted. Never called under _ASM_LOCK or _ASM_CKPT_LOCK (the waiter takes _ASM_LOCK inside
    _ASM_BG_COND)."""
    with _ASM_BG_COND:
        _asm_bg_kick_locked()


def asm_rebuild_wait(seen=None, stop=None):
    """The kernel's rebuild starter's wait: block until the background gate is open and the generation has moved past `seen`
    (the gate opened, a leaf was noted or the claim came back since the starter last looked), then return the generation, which
    the caller hands back next time. An event, never a timer: a starter that found nothing to start waits for the next change.
    `stop`, a threading.Event, ends the wait once set (asm_rebuild_wait_release wakes it); returns None then."""
    with _ASM_BG_COND:
        while not (_ASM_BG["idle"] and not _ASM_BG["fg"] and _ASM_BG["gen"] != seen):
            if stop is not None and stop.is_set():
                return None
            _ASM_BG_COND.wait()
        return _ASM_BG["gen"]


def asm_background_wait(stop=None):
    """Block until the background gate is open (the pusher asleep and no foreground work in flight), then return True; a
    reader's wait that changes nothing of the gate and claims nothing (the kernel's tail-first shadow, before each of its
    steps, so it never runs beside a page's build). An event, never a timer: every opening of the gate notifies the
    condition. `stop`, a threading.Event, ends the wait once set (asm_rebuild_wait_release wakes it); returns False then."""
    with _ASM_BG_COND:
        while not (_ASM_BG["idle"] and not _ASM_BG["fg"]):
            if stop is not None and stop.is_set():
                return False
            _ASM_BG_COND.wait()
        return not (stop is not None and stop.is_set())


def asm_rebuild_wait_release():
    """Wake the rebuild starter's wait so it sees its stop (the kernel's loops are stopping)."""
    with _ASM_BG_COND:
        _ASM_BG_COND.notify_all()


def _asm_background_stage():
    """Whether the calling thread is one of the kernel's background loops (_ASM_BG_STAGES), by its stage mark."""
    stage = _read_stage()
    return isinstance(stage, str) and stage.startswith(_ASM_BG_STAGES)


def _asm_claim_lend(key):
    """A waiting rebuild lets go of the shared claim (`key`, the one it holds) so a refresh may take it meanwhile; `paused`
    keeps a second rebuild from starting (asm_rebuild_claim, asm_refresh_running). Returns whether it held the claim."""
    with _ASM_LOCK:
        if key is None or _ASM_REFRESH["inflight"] != key:
            return False
        _ASM_REFRESH["inflight"], _ASM_REFRESH["paused"] = None, key
        return True


def _asm_claim_retake(key):
    """The waiting rebuild takes its lent claim back, when no refresh holds it now. Returns whether it did."""
    with _ASM_LOCK:
        if _ASM_REFRESH["inflight"] is not None:
            return False
        _ASM_REFRESH["inflight"] = key
        if _ASM_REFRESH["paused"] == key:
            _ASM_REFRESH["paused"] = None
        return True


def _asm_bg_yield():
    """A rebuild's chunk point: on the rebuild's own thread, wait until the background gate is open; elsewhere nothing. Called
    only where the thread holds no lock a reader or the pusher takes (the private road's phase boundaries), so a wait here
    holds nobody. While it waits, the rebuild lends its claim out (the review of PR #370), so a settle's refresh, cheap and
    targeted, takes it and runs; once the gate opens, the rebuild takes the claim back before it goes on, and waits on while a
    refresh still holds it, so one whole parse runs at a time (a waiting rebuild's heap stays as it is beside the refresh's).
    Each wait is counted under asmCheckpoint.rebuild (`yields`, `yieldMs`), and each one a refresh's claim prolonged
    (`lent`). Returns whether it waited: the caller asks again whether its leaf has a document now."""
    if not getattr(_ASM_BG_TL, "gated", False):
        return False
    key = getattr(_ASM_BG_TL, "claim", None)
    lent_to_refresh = False
    with _ASM_BG_COND:
        if _ASM_BG["idle"] and not _ASM_BG["fg"]:
            return False
        t0 = time.perf_counter()
        _ASM_BG["waiting"] += 1
        try:
            lent = _asm_claim_lend(key)
            while True:
                while not (_ASM_BG["idle"] and not _ASM_BG["fg"]):
                    _ASM_BG_COND.wait()
                if not lent or _asm_claim_retake(key):
                    break
                lent_to_refresh = True
                _ASM_BG_COND.wait()                       # a refresh holds the claim: its release wakes this wait (_asm_bg_wake)
        finally:
            _ASM_BG["waiting"] -= 1
    with _ASM_CKPT_LOCK:
        _ASM_REBUILD_STATS["yields"] += 1
        _ASM_REBUILD_STATS["yieldMs"] += (time.perf_counter() - t0) * 1000.0
        if lent_to_refresh:
            _ASM_REBUILD_STATS["lent"] += 1
    return True


def _asm_rebuild_stat(leaf_path):
    """(size, mtime_ns) of the leaf, None when it cannot be statted."""
    try:
        st_ = os.stat(leaf_path)
        return (int(st_.st_size), int(st_.st_mtime_ns))
    except OSError:
        return None


def asm_rebuild_note(leaf_path, rompuuid, why):
    """Note `leaf_path` (parsed under `rompuuid`) for a document rebuild: its parse found the document refused or missing, or the
    converge pass can never fit it (`why`). One stat; nothing when checkpoints are off, the leaf is under the rebuild floor or
    gone, it is already noted, or its rebuild was declined at the leaf's current stat. Returns whether it was noted."""
    if _CKPT_DIR_FN is None or not rompuuid:
        return False
    st_ = _asm_rebuild_stat(leaf_path)
    if st_ is None or st_[0] < max(1, int(_ASM_REBUILD_MIN)):
        return False
    k = (os.path.realpath(str(leaf_path)), str(rompuuid))
    with _ASM_CKPT_LOCK:
        if k in _ASM_REBUILD or _ASM_REBUILD_DECLINED.get(k) == st_:
            return False
    if asm_oversize_stands(leaf_path) is not None:        # its document is past the cap: a rebuild would parse it whole for nothing
        with _ASM_CKPT_LOCK:                              #  (2026-11-12: the declined memo above is keyed on the leaf's stat, which a
            _ASM_REBUILD_STATS["oversize"] = _ASM_REBUILD_STATS.get("oversize", 0) + 1   # live session moves at every turn)
            _ASM_CKPT_STATS["oversizeHeld"] = _ASM_CKPT_STATS.get("oversizeHeld", 0) + 1
        return False
    with _ASM_CKPT_LOCK:
        if k in _ASM_REBUILD:
            return False
        _ASM_REBUILD[k] = {"path": str(leaf_path), "why": str(why)}
        _ASM_REBUILD_STATS["noted"] += 1
    _asm_bg_wake()                                        # the rebuild starter looks now, if the gate is open (2026-11-12)
    return True


_RECORD_COUNTS = {}              # realpath -> ((dev, ino), bytes counted, newlines in them, whether they end on one): the memo of
_RECORD_COUNTS_LOCK = threading.Lock()   # transcript_records, oldest first
_RECORD_COUNTS_MAX = 4096
_RECORD_COUNT_CHUNK = 1 << 20


def transcript_records(path):
    """How many records (lines) `path` holds now, counted exactly, or None when it cannot be read: for sizing a whole parse's
    memory before it starts (the kernel's document worker, 2026-11-12). A parse holds memory per record as well as per byte,
    and an estimate of the count will not do: a sample of windows across a file missed a run of small records between them
    and counted 0.15 of them (the review of PR #465). The file is read in chunks and nothing is decoded. Memoized per file:
    the same file at the same size is a stat; one that only grew (the same device and inode, longer) has its new bytes
    counted from where the last count stopped, as a transcript is appended to; anything else is counted from the start. A
    last line with no newline yet counts as a record."""
    rp = os.path.realpath(str(path))
    try:
        st_ = os.stat(rp)
    except OSError:
        return None
    ident, size = (int(st_.st_dev), int(st_.st_ino)), int(st_.st_size)
    with _RECORD_COUNTS_LOCK:
        m = _RECORD_COUNTS.get(rp)
    pos, lines, ends = 0, 0, True
    if m is not None and m[0] == ident and m[1] <= size:
        pos, lines, ends = m[1], m[2], m[3]
    if pos < size:
        try:
            with open(rp, "rb") as fh:
                fh.seek(pos)
                while True:
                    chunk = fh.read(_RECORD_COUNT_CHUNK)
                    if not chunk:
                        break
                    lines += chunk.count(b"\n")
                    pos += len(chunk)
                    ends = chunk.endswith(b"\n")
        except OSError:
            return None
    with _RECORD_COUNTS_LOCK:
        _RECORD_COUNTS.pop(rp, None)
        _RECORD_COUNTS[rp] = (ident, pos, lines, ends)
        while len(_RECORD_COUNTS) > _RECORD_COUNTS_MAX:
            _RECORD_COUNTS.pop(next(iter(_RECORD_COUNTS)))
    return lines + (0 if ends or pos == 0 else 1)


def asm_rebuild_drop(leaf_path, rompuuid=None, why="released"):
    """Take the leaf's noted rebuilds off the table (`rompuuid`'s alone when given), counted under `why`: the end of the leaf's
    use (release_assembly) or a leaf the kernel will not rebuild. Returns how many went."""
    rp = os.path.realpath(str(leaf_path))
    with _ASM_CKPT_LOCK:
        keys = [k for k in _ASM_REBUILD if k[0] == rp and (rompuuid is None or k[1] == str(rompuuid))]
        for k in keys:
            _ASM_REBUILD.pop(k, None)
        if keys:
            _ASM_REBUILD_STATS[why] = _ASM_REBUILD_STATS.get(why, 0) + len(keys)
    return len(keys)


_ASM_REBUILD_WANT = {}          # rompuuid -> the sequence number of the latest time a page opened or switched to that session
#                                 (asm_rebuild_prefer): the rebuild's order puts those sessions first, the most recent first
_ASM_REBUILD_WANT_SEQ = [0]
_ASM_REBUILD_WANT_MAX = 256


def asm_rebuild_prefer(rompuuid):
    """A page opened session `rompuuid` or switched to its tab (the kernel's connect and its activeTab message): a pending rebuild
    of a leaf parsed under it goes before every other, the most recently opened first (2026-11-12: largest first alone left
    the session the user was waiting on behind every larger idle one). A dict write; the oldest preference leaves past
    _ASM_REBUILD_WANT_MAX."""
    if not rompuuid:
        return
    with _ASM_CKPT_LOCK:
        _ASM_REBUILD_WANT_SEQ[0] += 1
        _ASM_REBUILD_WANT.pop(str(rompuuid), None)
        _ASM_REBUILD_WANT[str(rompuuid)] = _ASM_REBUILD_WANT_SEQ[0]
        while len(_ASM_REBUILD_WANT) > _ASM_REBUILD_WANT_MAX:
            _ASM_REBUILD_WANT.pop(next(iter(_ASM_REBUILD_WANT)))


def asm_rebuild_pending():
    """The noted leaves still wanting a rebuild: those of a session a page opened first, the most recently opened first
    (asm_rebuild_prefer), then LARGEST FIRST: [(leaf as handed, rompuuid, why, size)]. A leaf that is gone, has shrunk under
    the floor, has a document file standing (the settle, the converge pass or the rewrite after a chain refusal wrote one,
    which that road answers for), or carries an oversize verdict leaves the table here, counted. When nothing is pending and
    no rebuild runs, a drain that rebuilt or declined anything says its summary line once (the first after a boot is the
    boot's)."""
    with _ASM_CKPT_LOCK:
        items = list(_ASM_REBUILD.items())
    out, drop = [], []
    for k, v in items:
        st_ = _asm_rebuild_stat(v["path"])
        if st_ is None:
            drop.append((k, v, "gone"))
        elif st_[0] < max(1, int(_ASM_REBUILD_MIN)):
            drop.append((k, v, "small"))
        elif asm_document_stands(v["path"]):
            drop.append((k, v, "moot"))
        elif asm_oversize_stands(v["path"]) is not None:
            drop.append((k, v, "oversize"))               # a verdict kept since it was noted (another road built it past the cap)
        else:
            out.append((v["path"], k[1], v["why"], st_[0]))
    line = None
    with _ASM_CKPT_LOCK:
        for k, v, why in drop:
            if _ASM_REBUILD.get(k) is v:
                _ASM_REBUILD.pop(k, None)
                _ASM_REBUILD_STATS[why] = _ASM_REBUILD_STATS.get(why, 0) + 1
        d = _ASM_REBUILD_DRAIN
        if not out and not _ASM_REBUILD and not d["running"] and (d["written"] or d["declined"] or d["failed"]):
            line = "assembly checkpoint: rebuilt %d document%s (%.0f MB of transcripts, %.1f s)%s%s" % (
                d["written"], "" if d["written"] == 1 else "s", d["bytes"] / 1048576.0, d["ms"] / 1000.0,
                ("; %d declined by the writer" % d["declined"]) if d["declined"] else "",
                ("; %d failed" % d["failed"]) if d["failed"] else "")
            d.update(written=0, declined=0, failed=0, bytes=0, ms=0.0)
    if line is not None:
        try:
            sys.stderr.write(line + "\n")
        except Exception:
            pass
    with _ASM_CKPT_LOCK:
        want = dict(_ASM_REBUILD_WANT)
    out.sort(key=lambda r: (-want.get(r[1], 0), -r[3]))   # a session a page opened first, the most recently opened before the
    return out                                            #  rest (2026-11-12), then the largest


def asm_refresh_running():
    """Whether a document refresh or rebuild holds the one claim (one whole parse at a time), or a rebuild waiting at its gate
    has lent it out (it goes on once the gate opens: no second rebuild starts meanwhile)."""
    with _ASM_LOCK:
        return _ASM_REFRESH["inflight"] is not None or _ASM_REFRESH["paused"] is not None


def asm_rebuild_claim(leaf_path, rompuuid, sdk_human=False):
    """Claim the rebuild of a noted leaf under the flag the caller will parse it with (the display parse's, which the next boot's
    restore reads with): the refresh's one claim, so a rebuild never runs beside a refresh or another rebuild, and the leaf
    leaves the table. Returns the reason it was noted, or None when it is not noted or another whole parse holds the claim.
    The caller runs asm_refresh_document(..., rebuild=<that reason>) next, which releases the claim."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _ASM_CKPT_LOCK:
        v = _ASM_REBUILD.get(key[:2])
    if v is None:
        return None
    with _ASM_LOCK:
        if _ASM_REFRESH["inflight"] is not None or _ASM_REFRESH["paused"] is not None:
            return None                                   # (a waiting rebuild lent the claim to a refresh: still one at a time)
        _ASM_REFRESH["inflight"] = key
    with _ASM_CKPT_LOCK:
        if _ASM_REBUILD.get(key[:2]) is v:
            _ASM_REBUILD.pop(key[:2], None)
        _ASM_REBUILD_STATS["claimed"] += 1
        _ASM_REBUILD_DRAIN["running"] += 1
    return v["why"]


def asm_rebuild_abandon(leaf_path, rompuuid, sdk_human, why, err=None):
    """A claimed rebuild whose asm_refresh_document never ran (its caller raised first, or its thread did not start): the claim
    released and the rebuild counted as failed, so the drain's summary still comes. A no-op once the document road ran (it
    released the claim and counted its own outcome)."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _ASM_LOCK:
        if _ASM_REFRESH["inflight"] != key:
            return False
        _ASM_REFRESH["inflight"] = None
    _asm_rebuild_done(leaf_path, rompuuid, why, "failed", _asm_rebuild_stat(leaf_path), 0.0, err)
    _asm_bg_wake()                                        # the claim is back: the rebuild starter looks again
    return True


def _asm_rebuild_done(leaf_path, rompuuid, why, outcome, st0, ms, reason, tb=None):
    """A rebuild's outcome: counted, said once for the leaf (written, declined with the writer's reason, failed), and a decline
    or a failure memoized at the stat the rebuild read, so the next parse of the unchanged leaf does not note it again. A decline
    of the moment (_ASM_SKIP_BLIP: the reader's offsets evicted while the parse ran, a stat or a write refused) says nothing about
    the cut, so the leaf goes back on the table for one more try at the pusher's next cycle end before it is memoized (`retry`).
    So does a rebuild that RAISED (2026-10-23, the review of PR #367: it was memoized at once, so an idle session was not tried
    again until it changed or the kernel restarted): the first raise is said and retried once, a second is final for the leaf's
    stat and said loudly, with both errors and the second one's traceback (`tb`)."""
    k = (os.path.realpath(str(leaf_path)), str(rompuuid))
    size = st0[0] if st0 is not None else 0
    first_raise = raised_before = None
    with _ASM_CKPT_LOCK:
        if outcome == "declined" and reason in _ASM_SKIP_BLIP and k not in _ASM_REBUILD_BLIPPED:
            outcome = "retry"
            _ASM_REBUILD_BLIPPED.add(k)
            if len(_ASM_REBUILD_BLIPPED) > 4096:
                _ASM_REBUILD_BLIPPED.clear()
            _ASM_REBUILD.setdefault(k, {"path": str(leaf_path), "why": why})
        elif outcome == "failed" and k not in _ASM_REBUILD_RAISED:
            outcome, first_raise = "retry", reason or "no error"
            _ASM_REBUILD_RAISED[k] = first_raise
            while len(_ASM_REBUILD_RAISED) > 4096:
                _ASM_REBUILD_RAISED.pop(next(iter(_ASM_REBUILD_RAISED)))
            _ASM_REBUILD.setdefault(k, {"path": str(leaf_path), "why": why})
        elif outcome == "failed":
            raised_before = _ASM_REBUILD_RAISED.get(k)
        if outcome != "retry":
            _ASM_REBUILD_RAISED.pop(k, None)              # the leaf's one retry after a raise is spent (or never needed)
        _ASM_REBUILD_STATS[outcome] = _ASM_REBUILD_STATS.get(outcome, 0) + 1
        _ASM_REBUILD_STATS["ms"] += ms
        d = _ASM_REBUILD_DRAIN
        d["running"] = max(0, d["running"] - 1)
        d["ms"] += ms
        if outcome == "written":
            _ASM_REBUILD_STATS["bytes"] += size
            d["written"] += 1; d["bytes"] += size
            _ASM_REBUILD_DECLINED.pop(k, None); _ASM_REBUILD_BLIPPED.discard(k)
        elif outcome in ("declined", "failed"):
            d[outcome] += 1
            _ASM_REBUILD_BLIPPED.discard(k)
            if st0 is not None:
                _ASM_REBUILD_DECLINED[k] = st0
                while len(_ASM_REBUILD_DECLINED) > 4096:
                    _ASM_REBUILD_DECLINED.pop(next(iter(_ASM_REBUILD_DECLINED)))
    if outcome == "written":
        line = "assembly checkpoint: rebuilt the document of %s (%s; %.0f MB in %.1f s)" % (leaf_path, why, size / 1048576.0,
                                                                                            ms / 1000.0)
    elif first_raise is not None:
        line = ("assembly checkpoint: the rebuild of %s raised (%s); it is tried once more at the pusher's next cycle end"
                % (leaf_path, first_raise))
    elif outcome == "failed" and raised_before is not None:
        line = ("assembly checkpoint: THE REBUILD OF %s FAILED TWICE (%s; the first time: %s); it is not tried again until the "
                "leaf changes or the kernel restarts, and the leaf parses whole at every boot until then%s"
                % (leaf_path, reason or "no error", raised_before, ("\n" + tb.rstrip()) if tb else ""))
    elif outcome in ("declined", "failed"):
        line = "assembly checkpoint: the document of %s was not rebuilt (%s: %s)" % (leaf_path, outcome, reason or "no entry")
    else:
        return                                            # moot (written by another road since the claim) or a blip's retry
    try:
        sys.stderr.write(line + "\n")
    except Exception:
        pass


def _carry_encode(st):
    """The emit carry as the document holds it: sets as lists, (second, hash) pairs as lists, the postal heal state
    left out (a restored entry heals only the tail it emits itself)."""
    return {"replay": sorted(st["replay"]), "seen_exact": sorted([list(k) for k in st["seen_exact"]]),
            "seen_text": sorted(st["seen_text"]), "compacted": bool(st["compacted"]), "restoring": bool(st["restoring"]),
            "last_boundary": st["last_boundary"], "summaries": dict(st["summaries"]), "skill_ids": sorted(st["skill_ids"]),
            "cmd_names": {k: sorted(v) for k, v in st["cmd_names"].items()},
            "absorbed_keys": sorted([list(k) for k in st["absorbed_keys"]]), "max_ppt": st["max_ppt"],
            "skill_loads": dict(st.get("skill_loads") or {})}     # wrapper uuid -> skill name before the cut (T333)


def _carry_decode(c):
    return {"replay": set(c["replay"]), "seen_exact": {tuple(k) for k in c["seen_exact"]}, "seen_text": set(c["seen_text"]),
            "compacted": bool(c["compacted"]), "restoring": bool(c["restoring"]), "last_boundary": c.get("last_boundary"),
            "summaries": dict(c.get("summaries") or {}), "skill_ids": set(c.get("skill_ids") or []),
            "cmd_names": {k: set(v) for k, v in (c.get("cmd_names") or {}).items()},
            "absorbed_keys": {tuple(k) for k in c.get("absorbed_keys") or []}, "max_ppt": float(c.get("max_ppt") or 0),
            "skill_loads": dict(c.get("skill_loads") or {}), "postal_miss_rec": set(), "postal_miss_att": set()}


def atom_tool_uses(atom):
    """[(tool_use id, name)] of an atom's tool calls, from the body or, for a lazy atom, its scalars: no hydration."""
    lz = atom.get("lazy")
    if lz is not None:
        return [tuple(x) for x in lz.get("tu") or []]
    return [(b.get("id"), b.get("name")) for b in _content(atom.get("message")) if isinstance(b, dict) and b.get("type") == "tool_use"]


def atom_tool_results(atom):
    """[tool_use id] of an atom's tool results, from the body or the lazy scalars."""
    lz = atom.get("lazy")
    if lz is not None:
        return list(lz.get("tr") or [])
    return [b.get("tool_use_id") for b in _content(atom.get("message")) if isinstance(b, dict) and b.get("type") == "tool_result"]


def atom_tool_ids(atom):
    """The set of an atom's tool_use ids and tool_result tool_use_ids (atom_tool_uses, atom_tool_results: a lazy atom answers
    from its marker, no hydration), falsy ones dropped: an id the settle gate can look up is never falsy (_bg_step registers a
    background task only under a truthy one)."""
    out = {x for x, _n in atom_tool_uses(atom) if x}
    out.update(x for x in atom_tool_results(atom) if x)
    return out


def postal_mids(content, ids=None):
    """POSTAL_RE's matches over a message's content, in document order, appended to `ids`: a bare string, the text blocks,
    and a tool_result block's content (a string, or a list of blocks whose strings are searched one by one: only the strings
    json.dumps would write can carry a marker, and a match cannot cross the encoder's separators, so encoding each string
    alone yields the ids the encoded whole would, without encoding an image block). The one search the timeline's
    connector, the message-caption join and the lazy marker share (T358; before it the kernel's _encoded_mids)."""
    if ids is None:
        ids = []
    if isinstance(content, str):
        if "romp-msg-id" in content:
            ids += POSTAL_RE.findall(content)
    elif isinstance(content, dict):
        if content.get("type") == "text":
            t = content.get("text")
            if isinstance(t, str):                    # a null text field is skipped, not a TypeError
                postal_mids(t, ids)
        elif content.get("type") == "tool_result":
            c = content.get("content")                # str | list[dict] | None from the SDK, as passed through
            if isinstance(c, str):
                postal_mids(c, ids)
            elif c is not None:
                _encoded_mids(c, ids)               # any other block (thinking, tool_use, image) carries no marker: skipped
    elif isinstance(content, (list, tuple)):
        for b in content:
            if isinstance(b, dict):                   # a bare string or a nested list inside a content list is no block: skipped,
                postal_mids(b, ids)                   #  as the kernel's body road read it
    return ids


def _encoded_mids(content, ids=None):
    """The strings-only search over an encoded value: dict keys and string values, every other value encodes to digits,
    true/false/null or brackets and cannot hold a marker."""
    if ids is None:
        ids = []
    if isinstance(content, str):
        if "romp-msg-id" in content:
            ids += POSTAL_RE.findall(json.dumps(content))
    elif isinstance(content, dict):
        for k, v in content.items():
            if isinstance(k, str) and "romp-msg-id" in k:
                ids += POSTAL_RE.findall(json.dumps(k))
            _encoded_mids(v, ids)
    elif isinstance(content, (list, tuple)):
        for v in content:
            _encoded_mids(v, ids)
    return ids


def atom_has_text(atom):
    """Whether an atom's text (the text blocks joined, stripped) is non-empty: the lazy marker's nt, else the body."""
    return _has_text(atom)


def atom_mids(atom):
    """The postal message ids an atom's message carries: the lazy marker's `mid`, else the body's (no hydration)."""
    lz = atom.get("lazy")
    if lz is not None:
        return list(lz.get("mid") or [])
    msg = atom.get("message")
    return postal_mids((msg or {}).get("content") if isinstance(msg, dict) else None)


def _prose_chars(atom):
    """Chars of the text blocks of an atom's body (each block's length, unstripped): pc's definition."""
    blocks = (atom.get("message") or {}).get("content", [])
    if not isinstance(blocks, list):
        return 0
    return sum(len(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text")


def atom_prose_chars(atom):
    """Chars of ASSISTANT prose on one atom: 0 for a non-assistant, API-error or prose-less atom. The lazy marker's `pc`
    for a lazy atom (no hydration), the body's text blocks for a resident one. The one measure behind every
    "substantive" read (the summary deep-link floor, the feed's citation gate, both against the judge's CITE_MIN_CHARS)."""
    if atom.get("type") != "assistant" or atom.get("isApiError"):
        return 0
    lz = atom.get("lazy")
    if lz is not None:
        return int(lz.get("pc") or 0)
    return _prose_chars(atom)


def atom_has_work(atom):
    """Whether an atom is real ASSISTANT output: its own text or a tool_use, on an assistant record that is not an
    API error (the captioner has nothing to gloss without one; an error record carries the error's text and is not
    work). Read from the lazy scalars (nt, tu) for a lazy atom. A segment row's `w` in the assembly document is this
    verdict at write time over the segment's atoms: changing this rule is a document version bump (_ASM_CKPT_V).
    An assistant message whose content is a bare string counts as text here (the judges' body road read no text in that
    shape): the lazy marker's nt cannot tell a bare string from a text block, and the two roads must agree; no CLI writes
    an assistant record in that shape (tests/test_asm_index.py pins the reading)."""
    if atom.get("type") != "assistant" or atom.get("isApiError"):
        return False
    return _has_text(atom) or bool(atom_tool_uses(atom))


def atom_is_bare_end(atom):
    """Whether an assistant atom only ENDS its turn: a stop in END_STOPS with no text and no tool use. That is the shape
    of the Codex normalizer's end record for a completion with nothing held, an empty content list (2026-09-23, the
    post-merge review of the restart-cut fix). The chat renders no row for it, so a deep-link anchor must never name
    it (kernel _seg_anchors), and a turn holding nothing else has no opener to arm on (kernel _turn_only_ends). Read
    from the lazy scalars (sr, nt, tu) for a lazy atom: no hydration."""
    if atom.get("type") != "assistant":
        return False
    return _stop_reason(atom) in END_STOPS and not _has_text(atom) and not atom_tool_uses(atom)


_SETTLE_TEXT_H8 = hashlib.sha1(b"No response requested.").hexdigest()[:8]


def atom_is_settle(atom):
    """Whether an assistant atom's text is the CLI's settle reply ("No response requested.") or a synthetic model's: the
    lazy marker's text hash and model stamp answer for a lazy atom, the body for a resident one."""
    lz = atom.get("lazy")
    if lz is not None:
        return lz.get("mdl") == "<synthetic>" or (bool(lz.get("nt")) and lz.get("h") == _SETTLE_TEXT_H8)
    msg = atom.get("message") or {}
    return _text_of(_content(msg)) == "No response requested." or msg.get("model") == "<synthetic>"


def seg_prompt_atom(seg):
    """The atom a segment's MESSAGE caption glosses: its trigger, else its first atom (the caption planner's rule). One
    build at most for a restored segment; the document stores whether it is human-authored (`hp`)."""
    atoms = seg.get("atoms") or []
    return next((a for a in atoms if a.get("uuid") == seg.get("trigger")), None) or (atoms[0] if atoms else None)


def turn_scalar(turn, key):
    """A restored pre-cut turn's stored scalar (`pcs`, `hT`, ...), None for a plain turn: the walkers ask this before
    touching a turn's atoms."""
    return turn.get(key) if turn.get("pre") else None


def atom_model(atom):
    """The model stamp of an atom's message (None when absent), from the body or the lazy scalars."""
    lz = atom.get("lazy")
    if lz is not None:
        return lz.get("mdl")
    msg = atom.get("message")
    return msg.get("model") if isinstance(msg, dict) else None


def _source_files(files):
    """A document's files map as hydration reads it, fsid -> the path of the ingested file: one form for the index, the
    atoms-only restore and the per-session map (2026-09-20)."""
    return {fsid: f["path"] for fsid, f in files.items()}


def _restore_prefix_atoms(pre_atoms, rompuuid, rows, fsids, source_files=None):
    """The pre-cut atoms as the tree holds them: the identity fields from the record row (uuid, type, t, fsid, session,
    parentUuid), the recorded scalars over them, a _LazyBody where a message was, the lazy scalars under `lazy`, and
    the read-order tiebreak the segmentation sorts by. The body's private source path belongs to this verified document
    (2026-09-17): another leaf restored under the same session may replace _LAZY_FILES while this view is still held."""
    tname = {"u": "user", "a": "assistant", "s": "system"}
    out = []
    for row in pre_atoms:
        a = {}
        ri = row.get("r")
        if ri is not None:
            rr = rows[ri]
            a.update({"type": tname.get(rr[2], "user"), "uuid": rr[0], "session_id": rompuuid, "t": rr[5],
                      "fsid": fsids[rr[6]] if 0 <= rr[6] < len(fsids) else None, "parentUuid": rr[9]})
            seq = rr[4]
        else:
            seq = row.get("seq", 0)
        a.update(row.get("s") or {})
        a["_seq"] = row.get("seq", seq)
        lz = row.get("lz")
        if lz is not None:
            a["lazy"] = dict(lz, i=row["i"], at=tuple(row["at"]) if row.get("at") else None)
            source = _UNBOUND_LAZY_SOURCE if source_files is None else source_files.get(a.get("fsid"))
            a["message"] = _LazyBody(a.get("uuid"), source)
        elif "m" in row:                                  # an inline body: an emitted atom with no record behind it (round 3)
            a["message"] = row["m"]
            if "tur" in row:
                a["toolUseResult"] = row["tur"]
        out.append(a)
    return out


def _asm_ckpt_load(leaf_path, rompuuid, sdk_human, candidate_files, links, quiet_inputs=False, own=True, memo=None):
    """The verified document for `leaf_path`, or None after a counted fallback (a document that exists and does not
    verify) or quietly when there is none. `memo` names the road that may be served the decode (the read, the gunzip, the
    JSON parse) from `_ASM_DOC_MEMO` when the document file's size and mtime stand, counted `<memo>:asmDocMemo`: "seeded" for
    the judges' seeded walk (2026-09-15: a leaf named by several sessions' episode rows decoded the same document once per
    naming session per pass), "restore" for the restore road (2026-09-24: every restore after a descent, rewrite or nonleaf
    demotion decoded the same unchanged document again, 61 percent of a lab's restore time at 120 MB). The verification below
    runs on the memoized document exactly as on a fresh one, a decode is memoized only once it passed, and an owner's note
    drops it. A memoized document is SHARED by every later load: no caller writes into it. `own` False is a reader over
    ANOTHER session's document (the judges' cross-session walk, 2026-09-15): a document that does not verify for it is
    refused quietly, counted under the parse's `foreign:<reason>`, never noted and never unlinked; the note, which removes
    the document so the owner's next settle rewrites it, belongs to the owner's own parse, the one reader whose inputs (its
    owner bit above all) are the document's."""
    def fail(reason, detail=""):
        if own:
            _asm_ckpt_note(leaf_path, reason, detail)     # the note removes the document and drops its memoized decode
        else:
            _asm_stat("foreign:" + str(reason))
        return None
    cp = _asm_ckpt_file(leaf_path)
    if cp is None or not cp.exists():
        return None
    memo_key = None                                       # set when this call decoded the file: memoized once the checks pass
    try:
        doc = None
        if memo:
            st_ = cp.stat(); mkey = (st_.st_size, st_.st_mtime_ns)
            with _ASM_CKPT_LOCK:
                ent = _ASM_DOC_MEMO.get(str(cp))
            if ent is not None and ent[0] == mkey:
                doc = ent[1]; _asm_stat(memo + ":asmDocMemo")
            else:
                memo_key = mkey
        if doc is None:
            data = cp.read_bytes()
            _count_read(str(cp), len(data))
            doc = json.loads(gzip.decompress(data).decode("utf-8"))
    except (OSError, ValueError, EOFError) as e:
        return fail("corrupt", str(e)[:80])
    if not isinstance(doc, dict) or doc.get("av") != _ASM_CKPT_V:
        return fail("version")
    if not isinstance(doc.get("atoms"), list) or not all(isinstance(r_, str) for r_ in doc["atoms"]):
        return fail("rows")     # v6 rows are strings; anything else is not this version's document
    if doc["atoms"]:
        try:
            first_ = json.loads(doc["atoms"][0])           # one row decoded here, cheaply: a string that is not a JSON object would
        except ValueError:                                 #  otherwise take the restore road and raise at its first build, uncounted
            first_ = None
        if not isinstance(first_, dict) or not all(r_.startswith("{") and r_.endswith("}") for r_ in doc["atoms"]):
            return fail("rows")   # every row must be shaped as an object (a bare string, a list, a number
        #                                                   or truncated text is refused whole here, cheaply); a row that is shaped
        #                                                   right but does not decode is the build's belt below (LazyIndex.build)
    if doc.get("path") != os.path.realpath(str(leaf_path)) or doc.get("rompuuid") != str(rompuuid) \
            or bool(doc.get("sdkHuman")) != bool(sdk_human):
        return fail("session")
    if list(doc.get("cands") or []) != [str(f) for f in candidate_files] or dict(doc.get("links") or {}) != dict(links or {}):
        if quiet_inputs:
            return None                                       # a reader asking about other inputs (the one-file walk over a
        return fail("inputs")      #  lineage): not this document's failure, it stands
    try:
        for fsid, f in doc["files"].items():
            st_ = os.stat(f["path"])
            if f.get("skip"):
                if (st_.st_size, st_.st_mtime) != (f["size"], f["mtime"]):
                    return fail("lineage", fsid)
                continue
            cut_off, pre_n, guard_hex = f["cut"]
            if st_.st_size < cut_off:
                return fail("shrunk", fsid)
            if st_.st_size < f["size"] or (st_.st_size == f["size"] and st_.st_mtime != f["mtime"]):
                return fail("rewrite", fsid)
            guard = bytes.fromhex(guard_hex)
            with open(f["path"], "rb") as fh:
                fh.seek(max(0, cut_off - len(guard)))
                ok = fh.read(len(guard)) == guard
            _count_read(f["path"], len(guard))
            if not ok:
                return fail("guard", fsid)
    except (OSError, KeyError, TypeError, ValueError) as e:
        return fail("corrupt", "files: %s" % e)
    if memo_key is not None:                              # every check above passed: this decode is worth keeping (round two)
        _asm_doc_memo_put(str(cp), memo_key, doc)
    return doc


def _seed_from_doc(doc):
    """(seed, landed) from a verified document: the pre-cut graph facts a FileAdapter takes, and the pre-cut uuids
    whose records carried assistant text. "spine_root_parent" is the raw parentUuid of the pre-cut spine's root, as that
    record's row holds it, None with no spine: the spine gives its root no parent, and _SeededGraph.root_may_link reads
    this to tell a root whose record links outside the graph from one with no parent (review round six, 2026-10-04)."""
    verdict_of = {"a": "active", "r": "rewind", "e": "eclipsed", "c": "clear", "b": "broken"}
    type_of = {"u": "user", "a": "assistant", "s": "system", "t": "attachment"}
    seed = {"seq_base": int(doc["cutSeq"]) - 1, "verdicts": {}, "types": {}, "spine": [doc["records"][i][0] for i in doc["spine"]],
            "spine_root_parent": doc["records"][doc["spine"][0]][9] if doc["spine"] else None,
            "prompt_ids": set(doc["gates"]["prompt_ids"]), "boundary_pids": set(doc["gates"]["boundary_pids"]),
            "compact_pids": set(doc["gates"]["compact_pids"]) if "compact_pids" in doc["gates"] else None,   # None: a document
            #                                                written before the field (2026-10-05), read by its prompt_ids
            "skill_use_ids": set(doc["gates"]["skill_use_ids"]), "src_tool_links": set(doc["gates"]["src_tool_links"]),
            "dangling": set(doc["gates"]["dangling"]), "seq_ts": doc.get("seqTs"), "last_ts": doc.get("lastTs"), "cuts": {},
            "file_ends": {fsid: (f.get("first"), f.get("last")) for fsid, f in doc["files"].items()}}
    landed = set()
    for row in doc["records"]:
        u, v, tc, sub = row[0], row[1], row[2], row[3]
        seed["verdicts"][u] = verdict_of.get(v, "broken")
        seed["types"][u] = (type_of.get(tc), sub)
        if row[8]:
            landed.add(u)
    for fsid, f in doc["files"].items():
        seed["cuts"][fsid] = "skip" if f.get("skip") else (int(f["cut"][0]), int(f["cut"][1]), bytes.fromhex(f["cut"][2]))
        if f.get("skip"):
            seed.setdefault("stat", {})[fsid] = (int(f["size"]), float(f["mtime"]))   # the witness a rewrite carries forward
    return seed, landed


def _entry_current(entry, candidate_files):
    """Whether an assembly entry folded everything its files hold now: each file's reader key (generation, base, count)
    equals the entry's, and a skipped lineage file's stat stands. The chain predicate may only answer from an entry
    that is current (its guard reads fresh inputs by contract)."""
    for fp in candidate_files:
        key = entry["recs"].get(str(fp))
        if key is None:
            return False
        if key == ("skip",):
            fst = (entry.get("skipped") or {}).get(str(fp))
            try:
                st_ = os.stat(fp)
            except OSError:
                return False
            if fst is None or (st_.st_size, st_.st_mtime) != tuple(fst):
                return False
            continue
        ent = _read_jsonl_entry(fp, tail_ok=True)
        if ent is None or (ent[6], ent[5] + len(ent[4])) != (key[0], key[2]):
            return False
    return True


def _boundary_effective_parent(r, known):
    """A compact_boundary's parent as the parse resolves it (FileAdapter._ingest, then _repair_compaction_stitches): its
    parentUuid or logicalParentUuid when that names a known record; when it names an UNKNOWN one, the first of
    compactMetadata.preservedSegment's tail, anchor and head that does (the stitch repair re-points only a truthy, unknown
    target); None when the resolved target is falsy (the parse leaves such a boundary a root) or nothing is known."""
    target = r.get("parentUuid") or r.get("logicalParentUuid")
    if not target:
        return None                                       # the parse leaves such a boundary a ROOT and never reads the segment
    if target in known:
        return target
    seg = (r.get("compactMetadata") or {}).get("preservedSegment") or {}
    for k in ("tailUuid", "anchorUuid", "headUuid"):
        cand = seg.get(k)
        if cand and cand in known:
            return cand
    return None


def _tail_chains_onto_the_document(leaf_path, doc, assume_childless=False, unwritten_out=None):
    """Whether the leaf's tail (its records past the document's cut) CHAINS onto the document: every tail record that bears a
    uuid or a parentUuid key, whatever its type (user, assistant, a system spur, a summary, a sidechain record, an attachment),
    parents a record IN THE TAIL, or the pre-cut SPINE TIP when the document says the writer proved the tip had no pre-cut
    child (`tipChildless`, decided at write time from the resolved graph, a compaction anchored on the tip counting as a child;
    an older document without the bit is not proven, so the exemption does not apply). A compaction boundary in the tail is
    held to the same rule through its EFFECTIVE parent, resolved as the parse resolves it (logicalParentUuid, else the
    preserved segment's tail, anchor or head that names a known record): the boundary at the cut anchors on the tip or a tail
    record; one anchored in the pre-cut interior, or on no known record, invalidated the document. A missing parentUuid key
    counts as a null root. So a /clear fork, a rewind onto any pre-cut record but a proven-childless tip, a system spur
    anchored before the cut, an orphan parent, a summary or sidechain record parented into the pre-cut part, a compaction
    re-anchored into the interior or onto an unknown uuid, a self-linked record, a parent cycle, a boundary with no anchor at all, a tail uuid reusing a pre-cut record's, all refuse to the whole parse; a uuid repeated within the tail is the parse's last-wins node: graph invalidations the document's
    byte checks cannot see, after which the pre-cut verdicts the document carries may be stale (T402 rounds one to six). The rule is REACHABILITY: the tail is a forest whose only root parent is the proven tip and every record's parent chain reaches it (a boundary through its effective parent); set membership alone approved a tail that re-rooted itself while a cold parse dropped the pre-cut conversation. The
    childless tip is exempt because a first child cannot change which pre-cut branch is active, and the live manual /compact
    chains its command wrappers onto the pre-compact leaf, a childless tip, in ten of thirteen corpus cases (the golden detached
    scenario). One predicate for EVERY read that seeds an adapter from a document: the boot restore, the restore after a
    descent, rewrite or nonleaf demotion, the chain-membership and file-rewound readers. A leaf with no cut in the document
    has no tail to chain.
    A tail record landing as a parent that a PRE-CUT record named and the pre-cut bytes never held (the document's dangling
    targets) refuses too (2026-11-12): the document's verdicts were decided while that parent was missing, and its arrival
    rebinds them (a cold parse grafts the pre-cut orphan onto it), while every chain in the tail may still reach the tip; a
    restart before the next settle restored such a tail with a pre-cut turn the cold parse folds away.
    `unwritten_out`, a list when given, learns why a refusal was made: when every chain that fails meets a record naming a
    parent NO record holds, pre-cut or tail (a parent not written yet: the CLI writes some records of a compaction before
    the parent they name, 2026-11-12), the uuids of those parents are appended, so the restore road can tell a tail the next
    append may complete from one whose shape refuses for good. Without it the walk stops at the first chain that fails."""
    fsid = Path(leaf_path).stem
    f = (doc.get("files") or {}).get(fsid) or {}
    cut = f.get("cut")
    if not cut or f.get("skip"):
        return True
    ent = _read_jsonl_entry(leaf_path, tail_ok=True, tail_from=(int(cut[0]), int(cut[1]), bytes.fromhex(cut[2])))
    recs = ent[4] if ent is not None else []
    if ent is not None and ent[5] < int(cut[1]):          # a whole entry: the tail is the records past the cut
        recs = recs[int(cut[1]) - ent[5]:]
    nodes = [r for r in recs if isinstance(r, dict) and r.get("uuid")]   # the graph nodes: records the parse indexes by uuid; a
    by_uuid = {r["uuid"]: r for r in nodes}                               #  uuid-less record bearing a parentUuid is no node to the
    rows, spine = doc.get("records") or [], doc.get("spine") or []       #  parse either (nothing is indexed for it), so it is not
    #                                                                       walked (T402 follow-up); a snapshot or index row is none
    pre_uuids = {row[0] for row in rows} | set(doc.get("xu") or [])   # every uuid the pre-cut bytes carry: the rows and the ones no
    #                                                                   row holds (absorbed attachments, shadowed copies; `xu`, round two)
    tip = rows[spine[-1]][0] if spine and spine[-1] < len(rows) else None
    tip_ok = tip if tip is not None and (doc.get("tipChildless") is True or assume_childless) else None
    known = pre_uuids | set(by_uuid)
    if any(u in pre_uuids for u in by_uuid):
        return False                                      # a tail uuid reusing a pre-cut record's: a cycle across the spine, and the parse
    #                                                       would re-bind a frozen record (round seven). A uuid REPEATED within the tail is
    #                                                       resolved as the parse resolves it: the last record wins (by_uuid), so the walk
    #                                                       below runs over one node per uuid with the last copy's parent; the common real
    #                                                       shape (a verbatim duplicate, 3.5 percent of transcripts) grafts, and a repeat
    #                                                       whose last copy is a non-tip root refuses through reachability (round eight)
    pre_dangling = set((doc.get("gates") or {}).get("dangling") or ())
    if any(u in pre_dangling for u in by_uuid):
        return False                                      # a parent the pre-cut bytes named lands in the tail: the verdicts are stale.
    #                                                       The writer's set also holds a parent a record past its cut named, so
    #                                                       one landing later refuses too: a whole parse, never a wrong tree
    walk_nodes = list(by_uuid.values())

    def parent_of(r):
        """The record's parent as the parse resolves it; None for a root (a null or missing parent, a self-link)."""
        if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
            p = _boundary_effective_parent(r, known)      # the boundary's anchor as the parse resolves it (round five, medium 2)
        else:
            p = r.get("parentUuid")
        return None if (not p or p == r.get("uuid")) else p

    def unwritten(r):
        """The parent `r` names that no record holds (pre-cut or tail), or None: for a boundary its raw anchor, which the
        parse follows once it lands, whatever preserved-segment record stands in for it meanwhile."""
        p = r.get("parentUuid")
        if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
            p = p or r.get("logicalParentUuid")
        return p if (p and p != r.get("uuid") and p not in known) else None

    return _tail_forest_reaches(walk_nodes, by_uuid, parent_of, tip_ok, unwritten=unwritten, unwritten_out=unwritten_out)


def _tail_forest_reaches(walk_nodes, by_uuid, parent_of, tip, unwritten=None, unwritten_out=None):
    """Whether every record of `walk_nodes` reaches `tip` through `parent_of` over the records of `by_uuid`: the reachability
    walk of _tail_chains_onto_the_document, lifted out whole (2026-11-12) so the tail-first window (tail_window) holds a tail
    to the rule the restore holds one to, by the same code. `tip` None reaches nothing. `unwritten(r)`, when given, names the
    parent of `r` that no record holds, and `unwritten_out` learns those parents as the restore's docstring says; the window
    passes neither, so its walk stops at the first chain that fails."""
    reaches = {}                                          # uuid -> True when its parent chain reaches the proven tip, "u" when it
    #                                                       fails through a parent not written yet, False when it fails otherwise
    missing = set()                                       # the unwritten parents met on chains that fail
    for r in walk_nodes:                                  # REACHABILITY, not membership (round six, medium): every tail node's
        path, on_path, cur, last_miss, misses = [], set(), r, -1, []   # parent chain must end at the proven tip; a chain ending at
        while True:                                       #  any other root (null, missing, self-link, unknown), revisiting a
            u = cur.get("uuid")                           #  record (a cycle) or leaving the tail into the pre-cut part refuses
            if u is not None and u in reaches:
                ok = reaches[u]; break
            if u is not None and u in on_path:
                ok = False; break                         # a cycle (the set beside the path keeps a backwards-written tail linear)
            if u is not None:
                path.append(u); on_path.add(u)
            miss = unwritten(cur) if unwritten is not None else None
            if miss is not None:
                last_miss = len(path) - 1; misses.append(miss)
            p = parent_of(cur)
            if p is None:
                ok = False; break                         # a root that is not the tip: the tail re-roots the graph
            if p == tip:
                ok = True; break
            if p in by_uuid:
                cur = by_uuid[p]; continue
            ok = False; break                             # the pre-cut interior, an unproven tip, or an unknown uuid
        if ok is not True:
            missing.update(misses)
        for i, x in enumerate(path):                      # a record at or below the last unwritten parent on its chain fails
            reaches[x] = True if ok is True else ("u" if (ok == "u" or i <= last_miss) else False)   # through it; one above it
        if ok is not True and (unwritten_out is None or reaches.get(r.get("uuid")) is False):        #  fails on its own
            return False
    if any(v == "u" for v in reaches.values()):
        unwritten_out.extend(sorted(missing))
        return False
    return True


def _asm_restore(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, reseated=False, after_demote=False,
                 postal_gate=False):
    """_asm_restore_inner timed whole: asmCheckpoint.restoreMs.total lands on every return (a served entry or a refusal)."""
    _t0 = time.perf_counter()
    try:
        return _asm_restore_inner(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, reseated=reseated,
                                  after_demote=after_demote, postal_gate=postal_gate)
    finally:
        _restore_ms("total", _t0)


def _asm_restore_inner(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, reseated=False,
                       after_demote=False, postal_gate=False):
    """The entry restored from the leaf's assembly checkpoint, served; None when there is none or it does not verify.
    The pre-cut turns come from the document as lazy atoms; the tail is read from the cut and parsed through an
    adapter seeded with the pre-cut graph facts and the carried emit state; the prefix's identity is proven. `reseated`:
    the restore re-seats a whole entry on the document written from it (the entry carries the churn bound, _asm_gates).
    `after_demote`: the restore road after this process's own entry for the leaf demoted, so the transcript grew under it
    in this process's life; a refusal there for a parent not written yet leaves the document standing (below). `postal_gate`:
    refuse a document written while a postal author waited on the log (its own `postalWait`; one without the key counts as
    such), as the late parent's road asks (review of 2026-11-12): the restored entry would serve that provisional author
    until its next whole parse, and the entry it replaces may know nothing of the document to say so (_assemble)."""
    if _asm_refusal_stands(leaf_path):
        _asm_stat("restore:refusedStanding")             # refused for the tail's shape at this very stat: no proof, no rewrite (round two)
        return None
    _t0 = time.perf_counter()
    doc = _asm_ckpt_load(leaf_path, rompuuid, sdk_human, candidate_files, links, memo="restore")
    #                                                     through the memo (2026-09-24): a restore after a descent, rewrite or
    #                                                     nonleaf demotion loads the document the boot's restore or a walk already
    #                                                     decoded, unchanged, and decoded it whole again every time (1,725 of
    #                                                     2,831 ms over nine restores at 120 MB in a lab); the load's checks still
    #                                                     run per call, so the memo serves the decode and never the proof
    _restore_ms("load", _t0)                              # the load's return, a document or a counted refusal (T401 (4))
    if doc is None:
        return None
    asm_sidecar_refresh(leaf_path, doc)                   # an older sidecar gains the inputs list here (round one, low 2)
    if postal_gate and doc.get("postalWait", True):
        _asm_stat("restore:postalWait")                   # the whole parse heals the author once the log catches up, and the
        _ASM_DEMOTE_TL.postal = True                      #  settle writes a document that says so; _assemble keeps that entry
        return None                                       #  re-seatable, as a compaction the postal gate sends whole leaves it
    rp_ = os.path.realpath(str(leaf_path))
    cut_off_ = int((((doc.get("files") or {}).get(Path(leaf_path).stem) or {}).get("cut") or [0])[0])
    try:
        _t0 = time.perf_counter()
        seed, landed = _seed_from_doc(doc)
        _restore_ms("seed", _t0)
        _unwritten = []
        if not _tail_chains_onto_the_document(leaf_path, doc, unwritten_out=_unwritten):
            _asm_stat("restore:chainRefused")             # the tail does not chain onto the document: the whole parse (T402); the
            # Every chain that fails names a parent no record holds yet (2026-11-12). The whole parse serves meanwhile (a cold
            # parse cuts the chain there too), but after a demotion the document is neither rewritten nor marked: the CLI
            # writes some of a compaction's records before the parent they name, and the parse that read between the two
            # writes rewrote the document from that half-written graph, every pre-compaction record off its path, so when the
            # parent landed nothing could restore it and the transcript was parsed whole a second time (minutes, on a session
            # near 1 GB). Left standing, the document serves the parent's arrival (`late-parent`, _asm_gates). If the parent
            # never lands, the session's settle or periodic write writes the document from the whole entry, which stays whole
            # (keepWhole), and a boot's refusal rewrites at once as before: nothing in a fresh process says a writer is mid-write
            if _unwritten:
                _asm_stat("restore:chainUnwritten")
                if after_demote:
                    _ASM_DEMOTE_TL.unwritten = True       # read by _assemble: the whole entry learns which document stands
                    return None
            _why = ("unproven" if doc.get("tipChildless") is None and _tail_chains_onto_the_document(leaf_path, doc, assume_childless=True)
                    else "shape")                         # the missing bit alone, or the tail's own shape
            with _ASM_CKPT_LOCK:
                _ASM_CHAIN_REFUSED_PATHS[rp_] = (_why, cut_off_)
                #                                                                  the why and the refused document's leaf cut offset:
                #                                                                  the whole parse that follows offers its document
            #                                               once; for the shape it then marks the sidecar at the leaf's stat, so the
            #                                               same cut is not proved or rewritten again while the leaf stands (round two)
            return None                                   #  document stands on disk until the next write replaces it
        fsids = list(doc.get("fsids") or [])
        pre_turns, prefix, index = [], [], None
        if doc.get("turns"):
            # the lazy index (T323 stage 4c): the turns from the section, their atoms built on demand; the section's own
            # digest proves it is the one the writer verified against the whole parse (no atom built here)
            _t0 = time.perf_counter()
            if _tree_identity_of_doc(doc["turns"], doc.get("identity")) != doc.get("treeIdentity"):
                _restore_ms("verify", _t0); _asm_ckpt_note(leaf_path, "identity"); return None
            # the section must cover the rows, every row in exactly one turn: else never a short history
            if sorted(k_ for td in doc["turns"] for k_ in td["atoms"]) != list(range(len(doc["atoms"]))):
                _restore_ms("verify", _t0); _asm_ckpt_note(leaf_path, "coverage"); return None
            _restore_ms("verify", _t0)
            _t0 = time.perf_counter()
            index = LazyIndex(doc, rompuuid, leaf_path, cache_key=key)
            pre_turns = _pre_turns_of(doc, index)
            doc = dict(doc, atoms=None)                        # the rows live in the index as bytes from here: dropped from a COPY,
            #                                                    since the load's document is the memo's (2026-09-24), and a None
            #                                                    written into it would refuse the next restore as `rows` and parse whole
            with _MAT_LOCK:
                _ASM_INDEX_STATS["restoredTurns"] += len(pre_turns)
            _restore_ms("index", _t0)
        else:
            _t0 = time.perf_counter()
            prefix = _restore_prefix_atoms([json.loads(r_) for r_ in doc["atoms"]], rompuuid, doc["records"], fsids,
                                           _source_files(doc["files"]))   # v6 string rows
            ok_ = _pre_tree_identity(prefix, rompuuid) == doc.get("identity")
            _restore_ms("verify", _t0)                         # the atoms-only form: its rows built and its identity proven, one part
            if not ok_:
                _asm_ckpt_note(leaf_path, "identity"); return None
        ad = FileAdapter(candidate_files, leaf_path, resume_links=links, seed=seed)
        ad.sdk_human = sdk_human
        st = _emit_state()
        st.update(_carry_decode(doc["carry"]))
        kept = ad.kept_uuids(ad.active_path())
        order = _chrono(ad, kept)
        mp_ = float(st.get("max_ppt") or 0)
        if any((ad.ts_of.get(u) or 0) < mp_ for u in order if u in ad.by_uuid and ad.by_uuid[u].get("type") in ("user", "assistant")):
            # A kept tail record stamped before the carried watermark (review of 2026-11-12): the carry, and every pre-pass
            # fact it holds (the last boundary walked, the replay window), is valid only for a tail that sorts at or after
            # everything before the cut, the invariant the fold's `ts` gate keeps for its delta and the writer's stamp guard
            # keeps for the records present when it wrote. A record appended since can break it (clock skew: a summary under
            # an attachment stamped between two pre-cut boundaries, which a cold parse gives to the earlier one and the carry
            # to the later). Refused as the tail's shape: the whole parse serves and its document cuts where the order holds.
            # Counted against all 232 of the live host's documents before it shipped: none refused (the CLI stamps a summary
            # 2 to 49 s before its boundary, never before the record the compaction followed)
            _asm_stat("restore:stampOrder")
            if index is not None:
                index.release()
            with _ASM_CKPT_LOCK:
                _ASM_CHAIN_REFUSED_PATHS[rp_] = ("shape", cut_off_)
            return None
        ad._prepass(order, st)
        # Every pre-cut boundary atom is held to the summary the pass leaves it (review of 2026-11-12). A summary whose parent
        # is no boundary belongs to the last boundary walked, and the CLI now hangs its summary under attachments and stamps
        # it seconds BEFORE its own boundary, so the pass, cold or restored, gives it to the previous boundary, one before the
        # cut when the cut opens at the compaction; a summary whose boundary is off the path does the same. A cold parse emits
        # each boundary's atom with the value the pass leaves last, and the frozen atom holds the value it left when the
        # document was written: the two differ when a tail summary landed since (it moves onto the atom) or LEFT the path
        # since (a fork at the anchor: the atom goes back to its own), so the comparison is with the frozen atom, never with
        # the carry, which never holds a tail summary (the refusal of #462 read the carry and parsed 16 sessions whole at one
        # boot where every frozen atom was right; the first patch read it too and left the forked case stale)
        rows_of = _asm_boundary_rows(doc, index, prefix)
        n_ = _asm_patch_pre_cut_summaries({u: st["summaries"].get(u) for u in rows_of}, rows_of, index, prefix)
        if n_:
            _asm_stat("restore:summaryPatched", n_)
        atoms = list(ad._emit_fold(order, st, rompuuid, postal_index))
        atoms += ad._absorbed(ad.qatts, kept, st, rompuuid, postal_index)
        entry = {"ad": ad, "st": st, "atoms": atoms, "kept": kept, "landed": landed | ad.landed_text_uuids(),
                 "cands": tuple(str(f) for f in candidate_files), "links": dict(links or {}),
                 "recs": dict(ad._src_keys), "n_qatts": len(ad.qatts), "prefix": prefix, "preTurns": pre_turns, "index": index,
                 "skipped": {f["path"]: (f["size"], f["mtime"]) for f in doc["files"].values() if f.get("skip")},
                 "docPre": sum((int(f["size"]) if f.get("skip") else int((f.get("cut") or [0])[0])) for f in doc["files"].values()),
                 "docCutOff": int(((doc["files"].get(Path(leaf_path).stem) or {}).get("cut") or [0])[0]),
                 "path": str(leaf_path)}                   # as handed: the reader's key for the same leaf
        #        docPre: the pre-cut bytes over the lineage; docCutOff: the leaf's cut offset. The fold's gate demotes this entry
        #        to a whole parse when the tail past the cut reaches the share (tailShare), so the settle rewrites the cut; a
        #        compaction's road reads the same two (_asm_compaction_under_share, 2026-09-24)
        if reseated:
            entry["reseated"] = True
        if doc.get("postalWait", True):
            entry["docPostalWait"] = True                      # its writer's entry had a postal author waiting on the log, or the
            #                                                    document predates the key and may have had one: a compaction on this
            #                                                    entry parses whole (_assemble, 2026-09-24), for such a document until
            #                                                    a settle rewrites it with the key, as the one after that whole parse
            #                                                    does (2026-09-25)
    except Exception as e:                                     # noqa: BLE001 — a document the code cannot use is a fallback
        _asm_ckpt_note(leaf_path, "restore", repr(e)[:120]); return None
    _LAZY_FILES[str(rompuuid)] = _source_files(doc["files"])
    with _ASM_LOCK:
        gone = [_ASM_CACHE.pop(key, None)]
        while len(_ASM_CACHE) >= _ASM_CACHE_MAX:
            gone.append(_ASM_CACHE.pop(next(iter(_ASM_CACHE))))
        _ASM_CACHE[key] = entry
    for e in gone:
        _asm_release(e)                                    # the superseded generation's index gives its memo back at once
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["restored"] += 1
    return _asm_serve(entry)


def _decoded_size(obj):
    """The bytes a decoded JSON value occupies in this process: sys.getsizeof over every container and scalar reachable from
    it, each object once (the decoder shares one key string across a record's dicts). None, True, False, the small ints and
    the empty and one-character Latin-1 strings are the interpreter's shared singletons and weigh nothing here. The hydration
    memo's weight (2026-10-01): a record's line on disk is no measure of it, since decoded text takes one to four bytes a
    character against one to twelve on disk, and every small block costs a dict."""
    seen, todo, n = set(), [obj], 0
    while todo:
        o = todo.pop()
        if o is None or o is True or o is False:
            continue
        t = type(o)
        if (t is int and -5 <= o <= 256) or (t is str and len(o) < 2 and (not o or ord(o) < 256)):
            continue
        i = id(o)
        if i in seen:
            continue
        seen.add(i)
        n += sys.getsizeof(o)
        if t is dict:
            todo.extend(o.keys())
            todo.extend(o.values())
        elif t is list or t is tuple:
            todo.extend(o)
    return n


def _hydrate_fields(rec, lz):
    """What the hydration memo keeps of a record read for a lazy atom, and what the atom is filled from (2026-10-01): the
    fields a fill of any kind may read, the record's own objects, never copies. An entry serves every atom of its record,
    whatever kind the atom's document gave it, so the choice never depends on this atom's kind: the message stays even for
    a command atom, whose fill rebuilds it from the marker, and whole for a skill or a command's output, whose atom keeps
    text derived from it. The top-level tool result only when it is a dict the atom carries (lazy tur) or of a consumed
    shape (TUR_CONSUMED_KEYS, the emit's carry rule), so every atom of the record finds it; an attachment's prompt alone.
    Left out: the record's metadata (ids, cwd, version, branch, times) and a Read call's second copy of the file's text,
    which no atom carries. An attachment that is not a dict is kept as it is, so the fill meets it as it did."""
    out = {}
    if "message" in rec:
        out["message"] = rec["message"]
    tur = rec.get("toolUseResult")
    if isinstance(tur, dict) and ((lz or {}).get("tur") or not TUR_CONSUMED_KEYS.isdisjoint(tur)):
        out["toolUseResult"] = tur
    att = rec.get("attachment")
    if isinstance(att, dict):
        if "prompt" in att:
            out["attachment"] = {"prompt": att["prompt"]}
    elif att:
        out["attachment"] = att
    return out


def _hydrate_one(a, rec):
    """Fill a lazy atom's body fields from its record, the way the emit built them. An atom another thread finished
    meanwhile (its marker gone) is left as it is (review find C)."""
    lz = a.get("lazy")
    if lz is None:
        return
    k = lz["k"]
    if k == "a":
        a["message"] = _norm_message(rec.get("message"))
    elif k == "u":
        if lz.get("tur") and isinstance(rec.get("toolUseResult"), dict):
            a["toolUseResult"] = rec["toolUseResult"]       # before the message, as kind k sets its skill text first (2026-09-20):
        a["message"] = _norm_message(rec.get("message"))    #  a peer meeting the plain-dict body in hydrate's first loop counts the
        #                                                      atom filled once its memo entry is gone, so the body a consumer is
        #                                                      handed must be whole the instant the message lands. Written the other
        #                                                      way round, a diff row or an answer built in that window read no tool
        #                                                      result for one build. The marker pop below is the one write still in
        #                                                      flight then, and a present marker costs a reader a hydrate call, never
        #                                                      a body
    elif k == "c":
        a["message"] = {"role": "user", "content": [{"type": "text", "text": lz.get("disp", "")}]}
    elif k == "o":
        btext = _text_of(_content(rec.get("message")))
        m = local_command_output(btext)
        a["message"] = {"role": "assistant", "content": [{"type": "text", "text": strip_ansi(m.group(1)).strip() if m else ""}],
                        "stop_reason": "end_turn"}
    elif k == "k":
        btext = _text_of(_content(rec.get("message")))
        a["skillMd"] = btext[:SKILL_MD_CAP] + ("\n\n…(skill content truncated)" if len(btext) > SKILL_MD_CAP else "")
        a["message"] = {"role": "assistant", "content": [], "stop_reason": None}
    elif k == "b":
        a["message"] = {"role": "user", "content": [{"type": "text", "text": _splice_prompt(rec) or ""}]}
    a.pop("lazy", None)


_HYDRATE_TEXT_READERS = set()     # the CODE objects of the shared text readers (the judges' _unit_text, _prompt_text and _atom_text,
#                                   the kernel's _atom_user_texts), each registered where it is defined: a hydration through one is
#                                   attributed with the reader's caller. Matched by code object, never by name (T384 round three: a
#                                   local function named like a reader was consumed as one)


def register_hydrate_text_reader(*fns):
    """Register shared text readers the hydration attribution names together with their caller."""
    for fn in fns:
        _HYDRATE_TEXT_READERS.add(fn.__code__)


def unregister_hydrate_text_reader(*fns):
    for fn in fns:
        _HYDRATE_TEXT_READERS.discard(fn.__code__)


def hydrate_text_reader_registered(fn):
    return fn.__code__ in _HYDRATE_TEXT_READERS


def hydrate(atoms, rompuuid=None, by=None):
    """Fill the bodies of the lazy atoms among `atoms` (a list, a turn's atoms, a whole session's turns) from their
    records on disk, one open per file and one seek-read per atom, through a byte-capped memo; returns how many
    atoms were filled. Every consumer that reads a pre-cut atom's message, toolUseResult or skillMd calls this first
    (a read without it raises LazyBodyRead). `rompuuid` names the session when the atoms carry none. The bytes read
    are counted per caller (`by`, else the calling function's name) under asmCheckpoint.hydratedBy."""
    if isinstance(atoms, dict):
        atoms = [a for t in atoms.get("turns", []) for a in t["atoms"]]
    lazy = [a for a in atoms if isinstance(a, dict) and a.get("lazy") is not None]
    if not lazy:
        return 0
    if by is None:
        try:
            f = sys._getframe(1)
            while f is not None and _synthetic_scope(f):  # a comprehension's or generator expression's own frame is no caller
                f = f.f_back
            by = f.f_code.co_name if f is not None else "?"
            if f is not None and f.f_code in _HYDRATE_TEXT_READERS:   # a text reader every walker shares says nothing about WHO
                g = f.f_back                              #  walked: the first caller outside the shared readers is recorded with it
                while g is not None and (g.f_code in _HYDRATE_TEXT_READERS or _synthetic_scope(g)):   #  (T377: naming the boot's
                    g = g.f_back                          #  reader; a reader reached through another reader, or through a
                by = "%s<-%s" % (by, g.f_code.co_name if g is not None else "?")   #  comprehension's frame, still names the walker)
        except Exception:
            by = "?"
    filled, by_file = 0, {}
    for a in lazy:
        u = a.get("uuid")
        with _ASM_CKPT_LOCK:
            hit = _HYDRATED.get(u) if u else None
            if hit is not None:
                _HYDRATED.pop(u, None); _HYDRATED[u] = hit      # a served body is a used one: to the LRU tail
                if a.get("lazy") is not None and hasattr(a.get("message"), "source_path"):
                    _ASM_CKPT_STATS["hydratedHits"] += 1        # a serve when the body is still unfilled as this call
                    #                                              looks; one a peer already wrote (marker gone, or its pop
                    #                                              in flight) was counted by that peer's read or hit
        if hit is not None:
            if a.get("lazy") is not None:                       # another thread may have finished it since the filter (C)
                _hydrate_one(a, hit[0])
            filled += 1
            continue
        msg = a.get("message")
        if not hasattr(msg, "source_path"):                 # another thread finished this atom between the memo miss above and
            with _ASM_CKPT_LOCK:                            #  here (2026-09-20): its body is a plain dict with no source, and the
                hit = _HYDRATED.get(u) if u else None       #  per-session map below could name a newer document and refuse an atom
                if hit is not None:                         #  whose body is in place, the whole call with it. Its bookkeeping (the
                    _HYDRATED[u] = _HYDRATED.pop(u, hit)    #  popped marker) may still be in flight: finish it from the memo as the
            if hit is not None:                             #  hit branch does, its entry to the LRU tail, or count it filled when
                _hydrate_one(a, hit[0])                     #  the entry is gone already (the move, 2026-10-02: a hit uncounted here
            filled += 1                                     #  is still a use, and the cap evicts the least recently used).
            continue                                        #  Entry gone with the marker present: the peer's pop is in flight, its
        #                                                      put having left the memo already, self-evicted under a cap the record
        #                                                      does not fit under or evicted by later puts from any thread between
        #                                                      the peer's put and its fill (2026-09-21); the body stands whole, since
        #                                                      _hydrate_one writes the message last, and readers key on the body
        #                                                      type, not the marker (2026-09-20).
        #                                                      The test is the source slot, not the class: the module loader
        #                                                      re-executes this file into the same module object at every import,
        #                                                      rebinding _LazyBody, and a sentinel built before that fails isinstance
        #                                                      against the new class, so it was counted filled, read nothing and
        #                                                      left its marker for the caller's next body read to raise on. A bound
        #                                                      body built before the re-execution is read now; an unbound one still
        #                                                      fails loudly below, its stale source sentinel being no path (the
        #                                                      product re-executes only at import, before any body exists). A None
        #                                                      message has no slot either and counts filled as before (2026-09-20).
        #                                                      is_lazy and the sentinel's __eq__ key on the same slot (2026-09-21)
        # Resolve from the held body's document, not the last document restored for this session (2026-09-17).
        # A shallow atom copy keeps its sentinel and source. A missing bound source stays a loud failure; it must
        # never borrow a path from a different snapshot. Legacy unbound descriptors retain the old lookup.
        path = msg.source_path
        if path is _UNBOUND_LAZY_SOURCE:
            sid = a.get("session_id") or rompuuid
            path = (_LAZY_FILES.get(str(sid)) or {}).get(a.get("fsid"))
        if path is None:
            raise LazyBodyRead("atom %s: no file known for fsid %s" % (u, a.get("fsid")))
        by_file.setdefault(path, []).append(a)
    for path, group in by_file.items():
        _tail_guard(path, "hydrate")                # a lazy body is a whole parse's atom: none is read inside a tail-first scope
        # the file's read stripe is held across the group: two threads hydrating the same atoms (the judges' unit text
        # and the frame's markdown at a boot) would both miss the memo and both read; the second now waits and hits it
        with _READ_STRIPES[hash(path) % len(_READ_STRIPES)], open(path, "rb") as fh:
            for a in sorted(group, key=lambda x: (x.get("lazy") or {}).get("at") or (0, 0)):
                lz = a.get("lazy")
                if lz is None:
                    filled += 1; continue                 # another thread hydrated it between the filter and here (the feed's
                u = a.get("uuid")                         #  build and the judges both ask): its body is in place
                with _ASM_CKPT_LOCK:
                    hit = _HYDRATED.get(u) if u else None
                    if hit is not None:
                        _HYDRATED.pop(u, None); _HYDRATED[u] = hit    # a peer's put served here is used too: to the LRU tail
                        if a.get("lazy") is not None and hasattr(a.get("message"), "source_path"):
                            _ASM_CKPT_STATS["hydratedHits"] += 1      # the rule of the first loop's count
                if hit is not None:
                    _hydrate_one(a, hit[0]); filled += 1
                    continue
                at_ln = lz.get("at")
                if not at_ln:
                    raise LazyBodyRead("atom %s: the document carries no record location" % a.get("uuid"))
                at, ln = at_ln
                fh.seek(at)
                raw = fh.read(ln)
                _count_read(path, ln)
                rec = json.loads(raw.decode("utf-8", "replace"))
                if rec.get("uuid") != a.get("uuid"):
                    raise LazyBodyRead("atom %s: the record at its offset is %s" % (a.get("uuid"), rec.get("uuid")))
                body = _hydrate_fields(rec, lz)           # the memo's entry and this fill's source alike: a hit then fills
                w = _decoded_size(body) + _HYDRATED_ENTRY_BYTES if u else 0   # exactly as a miss does. Weighed outside the
                #                                           memo's lock but inside the file's read stripe, where the put must
                #                                           land for a waiting thread to find it: the walk costs from a tenth
                #                                           of the decode (a long file text) to several times it (many small
                #                                           blocks), once per disk read, never on a hit. Every waiter on the
                #                                           stripe waits for the walks too: another hydrate of this file, a
                #                                           record-cache read that pulls its bytes (_read_jsonl_entry), and
                #                                           both for the files whose paths share the stripe, one in 64. The
                #                                           comment cut's by-offset read takes no stripe (_read_record_at)
                with _ASM_CKPT_LOCK:
                    _ASM_CKPT_STATS["hydratedBytes"] += ln; _ASM_CKPT_STATS["hydratedAtoms"] += 1
                    _THREAD_BYTES.hydrated = getattr(_THREAD_BYTES, "hydrated", 0) + ln   # this thread's share (T397)
                    _ASM_CKPT_STATS["hydratedBy"][by] = _ASM_CKPT_STATS["hydratedBy"].get(by, 0) + ln
                    hbs = _ASM_CKPT_STATS.setdefault("hydratedByStage", {})   # T401: the same bytes under the calling thread's stage
                    hk = "%s:%s" % (_read_stage() or "none", by)
                    hbs[hk] = hbs.get(hk, 0) + ln
                    if a.get("uuid"):
                        _old = _HYDRATED.pop(a["uuid"], None)     # a peer that missed it too put it first: its weight leaves
                        if _old is not None:                      #  with it, and the entry goes to the young end
                            _HYDRATED_BYTES[0] -= _old[1]
                        _HYDRATED[a["uuid"]] = (body, w); _HYDRATED_BYTES[0] += w
                        while _HYDRATED_BYTES[0] > _HYDRATED_CAP and _HYDRATED:
                            _old = _HYDRATED.pop(next(iter(_HYDRATED)))    # the least recently used body goes first
                            _HYDRATED_BYTES[0] -= _old[1]; _ASM_CKPT_STATS["hydratedEvicted"] += 1
                _hydrate_one(a, body); filled += 1
    return filled




def _assemble(leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, leaf_override,
              mode_out=None, private_out=None, installed_out=None):
    """(atoms, landed, cut_t, skill_loads) for parse_session; atoms are caller-owned copies. The one entry
    point that decides bypass vs serve vs fold vs full; see the block comment above. `mode_out`,
    when a list, receives the path taken ("serve" | "fold" | "full" | "bypass" | "fallback"): the
    kernel's chat-payload fold (issue 903) keys the validity of its cached prefix on it — a serve or
    a fold leaves every previously emitted atom in place (heals aside, which ride the postal index),
    while a full parse may have re-emitted history. Kept OUT of the returned tree on purpose: the
    golden fixtures and the fold==full replay compare that tree byte for byte. `private_out`, when a list, takes the
    "private" road: a whole parse whose entry is appended there and never installed, under no key lock, so the leaf's
    own entry and every reader of it are left as they stand (the document refresh, asm_refresh_document). `installed_out`,
    when a list, takes the whole entry the full road installs (_asm_full)."""
    _tail_guard(leaf_path, "_assemble")
    def _mode(m):
        if mode_out is not None:
            mode_out.append(m)
    if private_out is not None and not leaf_override:
        entry = _asm_whole_entry(leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, private=True)
        private_out.append(entry)
        _asm_stat("private")
        _mode("private")
        return _asm_serve(entry)
    if leaf_override:
        # A pending cut changes the walk anchor and is transient: always a plain full parse,
        # never cached — a cut parse's truncated emit state must not seed later folds.
        _asm_stat("bypass")
        _mode("bypass")
        ad = FileAdapter(candidate_files, leaf_path, leaf_override=leaf_override, resume_links=links)
        ad.sdk_human = sdk_human
        cut_t = None
        if leaf_override in ad.by_uuid:
            cut_t = ad.ts_of.get(leaf_override)
        return ad.atoms(rompuuid, postal_index), ad.landed_text_uuids(), cut_t, dict(getattr(ad, "skill_loads", None) or {}), []
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    seen = _files_witness(candidate_files)        # BEFORE any read: the entry this call leaves holds at least this content

    def _stamped(served, ent=None):
        """`served`, after stamping the entry this road leaves for the key with `seen` (with_display_graph's currency
        test: the files have not moved since the entry folded them, whatever the reader cache dropped meanwhile)."""
        if ent is None:
            with _ASM_LOCK:
                ent = _ASM_CACHE.get(key)
        if ent is not None:
            ent["seen"] = seen
        return served
    try:
        with _asm_key_lock(key):
            with _ASM_LOCK:
                entry = _ASM_CACHE.get(key)
                if entry is not None:
                    _ASM_CACHE.pop(key, None)
                    _ASM_CACHE[key] = entry       # a served entry is a USED entry (LRU touch)
            keep_whole = False                    # set when a restore refused a document: the whole parse below keeps its entry whole
            stands = None                         # the document such a refusal left standing for a parent not written yet
            if entry is not None:
                _ASM_DEMOTE_TL.reason = None
                got = _asm_gates(entry, leaf_path, candidate_files, links)
                if got is not None and entry.get("reseat") and not (got[0] and getattr(_ASM_DEMOTE_TL, "batch", False)
                                                                    and getattr(_ASM_DEMOTE_TL, "passed", False)):
                    # A WHOLE entry whose own document now stands (2026-09-24): the settle or the converge pass wrote it from this
                    # entry, which then kept every record and atom for the life of the process, so every fold re-ran the graph
                    # passes over the whole history and copied every atom (at 150 MB, an order of magnitude over a fold of the
                    # restored entry) and the chat's render floor stayed at turn 0. A delta the gates pass is re-seated instead: the
                    # restore road reads the document and the tail from its cut, and the folds after it walk the tail alone. A
                    # delta the gates demote keeps its own road and its own reason. Nor is a delta the narrowed promptid arm let
                    # through that passed on a parallel batch's link re-seated (both thread bits, which the gate's walk sets for
                    # every delta it rules on; an empty delta runs no walk, so it re-seats whatever an earlier delta left on the
                    # thread): the fold's kept check rules on it, and a rejection there parses whole (_asm_demote_passed).
                    # Re-seated, it took the restore road, which proves only that the tail chains onto the document: with an
                    # automatic compaction anchored on the parallel call and a result of that call parented at the call, the
                    # restore served the result as a turn of its own, and the settle, which writes nothing for a restored entry,
                    # left that tree standing for the next record and every boot (review of 2026-10-03, round two). A passed
                    # delta that extends the path keeps its re-seat
                    got = _asm_demote("reseat")
                if got is not None:
                    delta, leaf_recs = got
                    _asm_heal(entry, rompuuid, postal_index)
                    if not delta:
                        _asm_stat("serve")
                        _mode("serve")
                        return _stamped(_asm_serve(entry), entry)
                    served = _asm_fold(entry, delta, leaf_recs, str(leaf_path),
                                       Path(leaf_path).stem, rompuuid, postal_index)
                    if served is not None:
                        _mode("fold")
                        return _stamped(served, entry)
                with _ASM_LOCK:                   # gate/invariance demotion: the entry is stale
                    gone = _ASM_CACHE.pop(key, None)
                _asm_release(gone)                # ...and its index's memo goes with it
                # A demoted entry falls to the RESTORE road before the whole parse (T402): for a descent (the delta does not
                # chain the new leaf to the old: an api_error spur, a rewind, a /clear fork in the tail), a rewrite, a moved
                # lineage file or a compaction in the tail (2026-09-24), the document still stands for the pre-cut part and its
                # own load checks refuse it when it does not fit; the tail read from the cut covers the moved leaf and the
                # compaction. The first instrumented boot (T398) paid two whole parses under g:descent inside the auto-nudge tick
                # where a restore would have read the tail.
                _why = getattr(_ASM_DEMOTE_TL, "reason", None)
                if _CKPT_DIR_FN is not None and _why in _ASM_COMPACTION_DEMOTES:
                    _under = _asm_compaction_under_share(entry, leaf_path)
                    if _under is False:
                        _asm_stat("restore:pastShare")    # a compaction with the tail past the churn bound's share: the whole parse
                        _why = None                       #  below, and the settle after it writes a later cut (2026-09-24)
                    elif _under is None:
                        _why = None                       # no document to measure: the whole parse, counted as it always was
                    elif entry.get("docPostalWait") or (entry.get("docPre") is None and ((entry.get("st") or {}).get("postal_miss_rec")
                                                                                          or (entry.get("st") or {}).get("postal_miss_att"))):
                        # a postal author waits on the log, the test the writer's re-seat mark makes (asm_checkpoint_write): in
                        # a WHOLE entry's heal state, or in the writer's entry when it wrote the document this entry wrote or was
                        # restored from (its `postalWait`, kept as docPostalWait on both: a whole entry that healed after writing
                        # still wrote a document carrying the provisional author; a document written before the key existed
                        # counts as written while an author waited, 2026-09-25). The document carries it and no heal state,
                        # so the restored entry would serve that author until the next whole parse, where the whole entry heals
                        # it once the log catches up. The whole parse, counted as it always was (2026-09-24). A restored entry's
                        # own heal state is its tail's, which the restore parses again
                        _why = None
                if _CKPT_DIR_FN is not None and _why in _ASM_RESTORE_AFTER_DEMOTE:
                    # every restore over a document, this one and the boot's, first asks whether the tail CHAINS onto it
                    # (_tail_chains_onto_the_document); a rewind into the pre-cut interior, a /clear fork, a system spur
                    # anchored before the cut or an orphan parent refuses to the whole parse, as before (rounds one and two)
                    # the churn bound rides every restore of an entry that was re-seated or marked for it, whatever demoted it
                    # (an api_error spur or a rewind in the tail demotes for descent, a moved lineage file for nonleaf): a
                    # restore without the flag leaves a turns-section entry with no churn gate (`prefix` empty), so its cut
                    # froze for the rest of the process (review of 2026-09-24, medium 1). An entry kept whole (its tail at the
                    # share, a refused document, a postal author waiting) carries no flag and restores without the bound, as
                    # every entry did before: held to the share there, an open turn's entry would parse whole at every descent
                    _ASM_DEMOTE_TL.unwritten = _ASM_DEMOTE_TL.postal = False
                    served = _asm_restore(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human,
                                          reseated=_why == "reseat" or bool(entry.get("reseat") or entry.get("reseated")),
                                          after_demote=True, postal_gate=_why == "late-parent")
                    #                     the late parent asks the DOCUMENT whether a postal author waited when it was written
                    #                     (review of 2026-11-12): a compaction's postal gate reads the entry, but the entry a late
                    #                     parent lands on is often the whole one that gate itself sent to the whole parse, which
                    #                     knows nothing of the document and may have healed since
                    if served is not None:
                        _asm_stat("restore"); _asm_stat("restore:afterDemote")
                        _mode("restore")
                        return _stamped(served)
                    # a re-seat the restore did not serve is not tried again for the entry the whole parse builds, whatever the
                    # refusal (else every settle would write a document and every miss after it refuse it and parse whole); nor is
                    # any entry whose parse follows a refusal that left the document standing (the chain proof, a standing mark:
                    # the same tail refuses the next document too). Either way the entry stays whole and folds, as before the re-seat
                    keep_whole = (_why == "reseat" or asm_document_stands(leaf_path)) and not getattr(_ASM_DEMOTE_TL, "postal", False)
                    _measure = _asm_doc_measure(entry) if getattr(_ASM_DEMOTE_TL, "unwritten", False) else None
                    if _measure is not None:              # the refusal left this entry's document standing for the parent's
                        stands = (_measure, bool(entry.get("docPostalWait")))   # arrival: the whole entry knows which one
            elif _CKPT_DIR_FN is not None:
                served = _asm_restore(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human)
                if served is not None:
                    _asm_stat("restore")
                    _mode("restore")
                    return _stamped(served)
                keep_whole = asm_document_stands(leaf_path)   # a boot's refusal that left the document standing: as above
            # A full parse names its road (T398): an entry the gates DEMOTED (the g:<reason> beside it: the leaf's record
            # entry replaced by a from-zero read, a lineage file moved), a leaf with NO document file, a document that
            # stood but was REFUSED at the restore (its fallback reason counted beside), or no checkpoint directory at all.
            with _ASM_CKPT_LOCK:
                refused = _ASM_CKPT_REFUSED.pop(os.path.realpath(str(leaf_path)), None)   # the restore's own refusal (reason, stamp), if any
            if entry is not None:
                why = "demoted"
            elif _CKPT_DIR_FN is None:
                why = "noDir"
            elif refused is not None:
                why = "refused"                           # a document stood and did not verify (its reason under fallbacks); the
            else:                                         #  note unlinked it, so the stat below would have read it as none
                cp_ = _asm_ckpt_file(leaf_path)
                why = "noDocument" if cp_ is None or not cp_.exists() else "refused"
            _asm_stat("full:" + why)
            _mode("full")
            served = _asm_full(key, leaf_path, candidate_files, links, rompuuid,
                               postal_index, sdk_human, keep_whole=keep_whole, installed_out=installed_out, stands=stands)
            if why in ("refused", "noDocument"):
                asm_rebuild_note(leaf_path, rompuuid, why)   # a stat, AFTER the parse installed its entry (the review of PR #367):
                #                                              noted before it, any rebuild start during the parse saw no entry the
                #                                              converge pass could write from and parsed the same leaf beside it;
                #                                              now the kernel leaves the leaf to that pass when it can write it
            return _stamped(served)
    except Exception as e:
        _asm_stat("fallback")
        _mode("fallback")
        if not _ASM_WARNED[0] or _ASM_STATS["fallback"] in (10, 100, 1000, 10000):
            _ASM_WARNED[0] = True    # once, then at count milestones — a PERSISTENT fold bug
            #                          must not hide behind a single line in an old log
            print("romp-event-model: assembly fold failed (%r) — serving plain full parses "
                  "(stats %r); the fallback is correct but slow, fix the fold"
                  % (e, dict(_ASM_STATS)), file=sys.stderr)
        with _ASM_LOCK:
            gone = _ASM_CACHE.pop(key, None)
        _asm_release(gone)
        ad = FileAdapter(candidate_files, leaf_path, resume_links=links)
        ad.sdk_human = sdk_human
        return ad.atoms(rompuuid, postal_index), ad.landed_text_uuids(), None, dict(getattr(ad, "skill_loads", None) or {}), []


_FILE_HEAD_T = {}                 # realpath -> the stamp of the file's first uuid-bearing record, once one is on disk (a head never moves)
_FILE_HEAD_T_MAX = 4096


def _file_head_t(path):
    """The stamp (epoch seconds) of `path`'s first uuid-bearing record, or None while it has none, when its stamp does not
    parse, or when the file cannot be read. The record judge.transcript_head reads (a transcript is append-only, so its head
    never changes once written): read from the start a line at a time, normally that one record, the bytes counted like every
    reader byte, and memoized by realpath once found. Each line is decoded as transcript_head and the reader decode it, with
    bad bytes replaced, so a head record holding an invalid byte is the head here too, as it is to the candidate rule (review
    round two, 2026-10-03: raw bytes made json.loads refuse it and the floor fell to the next record's stamp)."""
    _tail_guard(path, "_file_head_t")
    key = os.path.realpath(str(path))
    hit = _FILE_HEAD_T.get(key)
    if hit is not None:
        return hit
    t, n = None, 0
    try:
        with open(path, "rb") as fh:
            for line in fh:
                n += len(line)
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("uuid"):
                    t = parse_z(r.get("timestamp")) or None
                    break
    except OSError:
        t = None
    if n:
        _count_read(str(path), n)
    if t is not None:
        if len(_FILE_HEAD_T) >= _FILE_HEAD_T_MAX:
            try:
                _FILE_HEAD_T.pop(next(iter(_FILE_HEAD_T)), None)   # the oldest entry; a racing writer's resize is harmless
            except (StopIteration, RuntimeError):
                pass
        _FILE_HEAD_T[key] = t
    return t


def _conversation_floor(candidate_files):
    """When the conversation a parse reads began: the earliest head stamp over its candidate files, or None when any of them
    has no readable head stamp yet (no floor, then: nothing is dropped on a guess)."""
    ts = []
    for f in candidate_files:
        t = _file_head_t(f)
        if t is None:
            return None
        ts.append(t)
    return min(ts) if ts else None


def _orphan_rows_from(srows, candidate_files):
    """The states rows synthesize_orphans reads: every row, less the orphanReply markers stamped before the conversation the
    parse reads began (_conversation_floor over `candidate_files`; None, the tail road's, is no floor). A marker from before
    that is a reply of a conversation this parse does not hold: a /clear's new file starts after the pre-clear markers, and
    once the pre-clear file that held what landed of them is no candidate (judge _judge_candidates leaves it out of a /clear's
    parse, 2026-10-03) the landed-text dedup cannot see them, so they would come back as fresh replies at pre-clear times; a
    marker that never landed leaked into every post-clear parse already. Same-second markers stay (`>=` the whole second:
    losing a reply is the worse error). A no-op whenever the session's first file is a candidate: no marker predates the
    conversation's first record. The heads are read only when a marker exists, and the rows themselves stay whole for idle
    spans (synthesize_idle)."""
    if candidate_files is None or not any(isinstance(r, dict) and isinstance(r.get("orphanReply"), dict) for r in srows or []):
        return srows
    conv0 = _conversation_floor(candidate_files)
    if conv0 is None:
        return srows
    floor = int(conv0)
    return [r for r in srows if not (isinstance(r, dict) and isinstance(r.get("orphanReply"), dict) and (r.get("t") or 0) < floor)]


# ───────────────────────── tail-first: the window (an exact boundary, or a refusal) ─────────────────────────
# The first of the three mechanisms (the scope above is the second, the kernel's shadow comparison the third). A tail parse
# is equal to the whole parse over the window only when nothing before the window's first record can change how a record
# inside it is kept, emitted, placed or grouped, and a naive tail is not: of five windows cut at a prompt in the survey's lab,
# one kept two abandoned turns of a rewound branch, because a branch whose fork point lies before the cut ends at a parent
# the tail does not hold and so reads as a broken chain, which the parse keeps on principle. So tail_window accepts a start
# record B only when every way the earlier file reaches into the tail is ruled out by the records themselves, and otherwise
# says why not. Each rule names the parser fact it closes:
#   the kept set        every tail record reaches B through tail records (_tail_forest_reaches, the restore predicate's own
#                       walk): no branch forked before B, no broken chain, no second root; and no uuid is written twice
#   a turn's start      B is a person's prompt whose chain runs to the record P written before it, directly or across the
#                       harness's turn-end bookkeeping (_tail_bookkeeping: records the parse keeps on the chain and reads
#                       nothing from, which the CLI writes after nearly every reply), each of them chained onto the one
#                       before; and P is an assistant record whose turn ended: the whole parse opens a turn at B, as a
#                       parse that begins there does
#   chronological order every tail stamp parses and is at or after B's, and P's is before it: the whole parse sorts the tail
#                       after everything earlier, so the verbatim re-write dedup (same second, same text) and a mid-turn
#                       prompt's send time cannot pair a tail record with an earlier one
#   the replay burst    no compaction record in the tail, and P, an assistant record, closed any burst an earlier one opened
#   carried links       a tool result, a skill payload and a slash command's echo name a record of the tail, never an earlier
#                       one (tool-result-before-window, carry, command-twin)
# What the records in hand cannot show is left to the gates past this one (a pending rollback, another transcript file of the
# session, a salvaged reply: the kernel's and tail_parse's) and, for a clock that stepped backwards further back than the
# bytes read here, to the shadow's comparison, which is why step 1 serves nothing.
_TAIL_SPAN0 = 256 * 1024          # the first read back from the end of the file
_TAIL_SPAN_MAX = 32 * 1024 * 1024  # the most a search reads; a file with no boundary in that much refuses ("no-boundary")
_TAIL_TRIES = 48                  # prompts a search looks at (the sync-point test, a few field reads each)
_TAIL_DEEP_TRIES = 8              # of those, how many may run the checks that walk the whole tail
_TAIL_FINAL = ("compaction-in-window", "uuid-repeat")   # refusals no earlier boundary can lift: its tail holds this one's
_TAIL_BOOKKEEPING = ("stop_hook_summary", "turn_duration", "away_summary")   # the system subtypes _emit_fold skips by name


class TailWindow:
    """A window tail_window accepted: `records`, the transcript's records from the boundary record B (`uuid`, stamped `t`) to
    the end of the file as it stood (`start`..`end` in bytes, `stat` the (mtime ns, size, inode) the read was taken at), the
    reply written before B (`prev_uuid`, `prev_t`, `prev_model`: the turn-ending assistant record, whose time and model
    are the only two facts of the earlier file a chat build carries across the boundary) and `n`, the user and assistant
    records it holds. `records` is the pass for parse_session's tail road inside tail_scope: that list, by identity."""
    __slots__ = ("leaf", "key", "keys", "start", "end", "stat", "records", "uuid", "t", "prev_uuid", "prev_t", "prev_model", "n",
                 "opened")

    def __init__(self, leaf, start, end, stat, records, uuid, t, prev_uuid, prev_t, prev_model, n):
        self.leaf = str(leaf)
        self.key = os.path.realpath(self.leaf)
        self.keys = {self.key}
        self.opened = False           # set by tail_open alone, once its gates passed: tail_scope refuses a window without it
        self.start, self.end, self.stat, self.records = start, end, stat, records
        self.uuid, self.t, self.prev_uuid, self.prev_t, self.prev_model, self.n = uuid, t, prev_uuid, prev_t, prev_model, n

    def holds(self, path):
        """Whether `path` is a file this window's scope refuses to read whole: the leaf, or one named through `also`."""
        p = str(path)
        return p == self.leaf or os.path.realpath(p) in self.keys

    def also(self, paths):
        """Name the session's other transcript files, so the scope refuses a whole read of them too."""
        for p in paths or ():
            self.keys.add(os.path.realpath(str(p)))
        return self


def _tail_prompt_shaped(r):
    """Whether record `r` can start a window: a person's prompt as plain text. Not a tool result, a harness record, a command
    wrapper or its echo (text opening with a tag or a slash), a stop record or a notice (a bracket), which the parse may
    skip, fold into the running turn or pair with an earlier record."""
    if r.get("type") != "user" or not r.get("uuid"):
        return False
    if r.get("isMeta") or r.get("isSidechain") or r.get("isCompactSummary") or r.get("isVisibleInTranscriptOnly"):
        return False
    if r.get("sourceToolUseID") or r.get("toolUseResult") is not None:
        return False
    blocks = _content(r.get("message"))
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
        return False
    txt = _text_of(blocks).strip()
    return bool(txt) and not txt.startswith(("<", "[", "/", "Caveat:"))


def _tail_parent(r):
    """A record's parent as the graph reads it (FileAdapter._ingest): parentUuid, else the compaction stitch's
    logicalParentUuid; a self link is a root."""
    p = r.get("parentUuid") or r.get("logicalParentUuid")
    return None if p == r.get("uuid") else p


def _tail_bookkeeping(r):
    """Whether uuid-bearing record `r` is turn-end bookkeeping: a record of the main conversation the parse keeps on the
    chain and reads nothing else from. A system record of one of the subtypes the emit loop skips by name (the stop hook's
    summary, the turn's duration, the recap written for a person who was away), or an attachment that is no queued_command
    (FileAdapter._ingest reads that kind alone: a spliced message). Never a compaction record, an API error's notice or a
    command's, which the parse does read."""
    if r.get("isSidechain") or r.get("logicalParentUuid"):
        return False
    if r.get("type") == "system":
        return r.get("subtype") in _TAIL_BOOKKEEPING
    return r.get("type") == "attachment" and (r.get("attachment") or {}).get("type") != "queued_command"


def _tail_sync_point(recs, ts, i, whole):
    """(index of the reply before B, None) when recs[i] starts a turn the whole parse starts too, else (None, reason): its
    chain runs to the uuid-bearing record written before it, straight or across turn-end bookkeeping written between the two
    (each such record the parent of the next), and that record is an assistant record of the main conversation that ended
    its turn and names its model, stamped before B. On real transcripts' shapes the bookkeeping is the common case: of 313
    prompts in 40 transcripts (2026-11-12, types and links only), 22 followed their reply directly and 251 across one to
    three such records."""
    b = recs[i]
    j, child = i - 1, b
    while j >= 0:
        r = recs[j]
        if r.get("uuid"):
            if not _tail_bookkeeping(r):
                break
            if child.get("parentUuid") != r["uuid"]:
                return None, "parent-not-predecessor"
            child = r
        j -= 1
    if j < 0:
        return None, ("whole-file" if whole else "no-predecessor")
    p = recs[j]
    if p.get("type") != "assistant" or p.get("isSidechain") or p.get("isApiErrorMessage"):
        return None, "predecessor-not-assistant"
    msg = p.get("message") if isinstance(p.get("message"), dict) else {}
    if msg.get("stop_reason") not in END_STOPS:
        return None, "turn-open"
    if not msg.get("model"):
        return None, "predecessor-model"
    if child.get("parentUuid") != p.get("uuid"):
        return None, "parent-not-predecessor"
    if ts[i] is None or ts[j] is None or not ts[j] < ts[i]:
        return None, "clock"
    return j, None


def _tail_refusal(recs, ts, i, j):
    """Why the tail recs[i:] is not provably the whole parse's (a reason), or None when every rule holds. `j` is the reply
    before B (_tail_sync_point). The reasons are tail_window's, in the order tested."""
    b, bt, pt = recs[i], ts[i], ts[j]
    tail = recs[i:]
    by_uuid, tool_ids, wrappers = {}, set(), {}
    for k, r in enumerate(tail, i):
        typ = r.get("type")
        if (typ == "system" and r.get("subtype") == "compact_boundary") or r.get("isCompactSummary") is True \
                or r.get("logicalParentUuid"):
            return "compaction-in-window"
        u = r.get("uuid")
        if u:
            if u in by_uuid:
                return "uuid-repeat"
            by_uuid[u] = r
        if typ == "assistant":
            for blk in _content(r.get("message")):
                if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("id"):
                    tool_ids.add(blk["id"])
        elif typ == "user":
            btext = _text_of(_content(r.get("message"))) or ""
            if is_skill_load_wrapper(btext):
                continue
            m = COMMAND_NAME_RE.match(btext) or (COMMAND_NAME_ANY_RE.search(btext) if CMD_WRAP_RE.match(btext) else None)
            if m:                                        # the pre-pass's own matcher (FileAdapter._prepass, the twins map)
                name = m.group(1).strip() or "/?"
                name = name if name.startswith("/") else "/" + name
                if name == "/compact":
                    return "compaction-in-window"        # a /compact episode can adopt a boundary written before the window
                if r.get("promptId"):
                    wrappers.setdefault(r["promptId"], set()).add(name)
    for k in range(i):                                   # the records read before B: none shares a uuid with the tail, and
        u = recs[k].get("uuid")                          #  none is stamped after P (it would sort between P and the window)
        if u:
            if u in by_uuid:
                return "uuid-repeat"
            if k > j:                                    # the bookkeeping between P and B: no atom to sort, and stamped
                if ts[k] is None or not pt <= ts[k] <= bt:   # from P's second to B's all the same
                    return "clock"
            elif ts[k] is not None and ts[k] > pt:
                return "clock"
    for k, r in enumerate(tail, i):
        if r.get("uuid"):
            if ts[k] is None or ts[k] < bt:
                return "stamp-floor"
        elif r.get("type") == "attachment":
            t2 = parse_z(r.get("timestamp"))
            if t2 is not None and t2 < bt:
                return "stamp-floor"
    nodes = [r for r in tail if r.get("uuid") and r is not b]
    if not _tail_forest_reaches(nodes, by_uuid, _tail_parent, b["uuid"]):
        return "chain"
    for r in tail:
        typ = r.get("type")
        if typ == "user":
            blocks = _content(r.get("message"))
            has_result = False
            for blk in blocks:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    has_result = True
                    if blk.get("tool_use_id") not in tool_ids:
                        return "tool-result-before-window"
            txt = _text_of(blocks).strip()
            if r.get("promptId") and not has_result and txt.startswith("/") and not any(
                    txt == n or txt.startswith(n + " ") for n in wrappers.get(r["promptId"], ())):
                return "command-twin"                    # an echo only a wrapper written before the window could drop
        for key in ("sourceToolUseID", "parentToolUseID"):
            if r.get(key) and r[key] not in tool_ids:
                return "carry"
        if r.get("sourceToolAssistantUUID") and r["sourceToolAssistantUUID"] not in by_uuid:
            return "carry"
    return None


# THE HIGH-WATER STAMP (the review of step 1, 2026-11-12). tail_window proves its rules over the bytes it read, and a record
# written before those bytes but stamped after the reply before B sorts into the window in the whole build (segment_turns
# orders atoms by stamp), which a search that read only the file's end cannot see. Claude Code does write stamps out of order:
# a scan of real transcripts found a reminder attachment stamped up to a month ahead of the records written after it. So each
# transcript keeps, from one pass over its bytes, the highest stamp among the records that become atoms placed by their own
# stamp (user and assistant records, the two system records the emit loop keeps, the compaction boundary and the model's
# fallback notice, and the queued-command splice, which lands no earlier than its own stamp), and, for each of its newest
# prompts that could open a window, that highest stamp over the records written before it. tail_window refuses a boundary
# whose stamp-before is past the reply it follows ("high-water"). Attachments and bookkeeping that never become atoms are not
# counted, so a reminder stamped a month ahead refuses nothing. The pass is incremental and proved the way the reader proves
# its entries: a longer file by the bytes before the entry's end (an append leaves them as they were), a file of the same
# length under a new time by the crc32 of every byte the entry covers; anything else is scanned from byte zero. The entry
# is kept beside the file's checkpoint, so a new process scans only what was appended since.
_TAIL_STAMP_PROMPTS = 512         # the prompts an entry keeps a stamp-before for, newest: a window opens at one of the last
#                                   _TAIL_TRIES prompts within the search's bytes, so an older one is never asked about
_TAIL_STAMP_GUARD = 64            # the bytes before an entry's end it keeps: a file whose bytes there moved was not only appended to
_TAIL_STAMP_V = 1
_TAIL_STAMPS = {}                 # realpath -> this process's entry: {path, dev, ino, mtime, end, guard, crc, hw, before}; never
#                                   mutated in place
_TAIL_STAMPS_LOCK = threading.Lock()


def _tail_stamp_counts(r):
    """Whether record `r`'s stamp places an atom of the whole parse (the high-water stamp's records)."""
    typ = r.get("type")
    if typ in ("user", "assistant"):
        return True
    if typ == "system":
        return r.get("subtype") in ("compact_boundary", "model_refusal_fallback")
    return typ == "attachment" and (r.get("attachment") or {}).get("type") == "queued_command"


def _tail_stamps_step(ent, r):
    """Fold one record, in file order, into high-water entry `ent`: a prompt that could open a window records the stamp
    written before it, then a counted record raises the mark."""
    if not isinstance(r, dict) or not r.get("uuid"):
        return
    if _tail_prompt_shaped(r):
        before = ent["before"]
        before.pop(r["uuid"], None)               # a record written twice: the later copy is the one a window can open at
        before[r["uuid"]] = ent["hw"]
        while len(before) > _TAIL_STAMP_PROMPTS:
            before.pop(next(iter(before)))
    if _tail_stamp_counts(r):
        t = parse_z(r.get("timestamp"))
        if t is not None and t > ent["hw"]:
            ent["hw"] = t


def _tail_stamps_file(key):
    d = _ckpt_dir()
    if d is None:
        return None
    return Path(d) / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".tail-stamps")


def _tail_stamps_guard_ok(ent, key, st):
    """Whether entry `ent` still describes a prefix of file `key` (stat `st`), proved as the reader proves its own entries:
    the same file (device and inode) and at least as long; then unchanged since the scan (the same length and time), or
    longer with the bytes before the entry's end as they were (the append guard), or the same length under a new time with
    the crc32 of every byte the entry covers unchanged (a writer that reopened the file and wrote nothing, against an
    in-place rewrite)."""
    if ent.get("dev") != st.st_dev or ent.get("ino") != st.st_ino or st.st_size < ent["end"]:
        return False
    if st.st_size == ent["end"]:
        if st.st_mtime_ns == ent.get("mtime"):
            return True
        if ent.get("crc") is None:
            return False
        crc = 0
        try:
            with open(key, "rb") as fh:
                left = ent["end"]
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        return False
                    crc = zlib.crc32(chunk, crc)
                    left -= len(chunk)
        except OSError:
            return False
        return crc == ent["crc"]
    g = ent["guard"]
    if not g:
        return ent["end"] == 0
    try:
        with open(key, "rb") as fh:
            fh.seek(ent["end"] - len(g))
            return fh.read(len(g)) == g
    except OSError:
        return False


def _tail_stamps_load(key):
    """The entry kept beside the file's checkpoint, as written (its guard not yet checked), or None."""
    f = _tail_stamps_file(key)
    if f is None:
        return None
    try:
        v = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(v, dict) or v.get("v") != _TAIL_STAMP_V or v.get("path") != key:
            return None
        return {"path": key, "dev": int(v["dev"]), "ino": int(v["ino"]), "mtime": int(v["mtime"]), "end": int(v["end"]),
                "guard": bytes.fromhex(v["guard"]), "crc": None if v["crc"] is None else int(v["crc"]), "hw": float(v["hw"]),
                "before": {str(u): float(t) for u, t in v["before"]}}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _tail_stamps_save(ent):
    f = _tail_stamps_file(ent["path"])
    if f is None:
        return
    doc = {"v": _TAIL_STAMP_V, "path": ent["path"], "dev": ent["dev"], "ino": ent["ino"], "mtime": ent["mtime"], "end": ent["end"],
           "guard": ent["guard"].hex(), "crc": ent["crc"], "hw": ent["hw"], "before": [[u, t] for u, t in ent["before"].items()]}
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = _writer_tmp(f)
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass                                      # kept in memory all the same; the next process scans the file again


def _tail_stamps_seed(key, path, st):
    """A fresh entry for file `key`: stepped over the records of the reader's WHOLE entry for it when one stands and still
    describes the file (no read, no decode), else empty at byte zero."""
    ent = {"path": key, "dev": st.st_dev, "ino": st.st_ino, "mtime": st.st_mtime_ns, "end": 0, "guard": b"", "crc": 0, "hw": 0.0,
           "before": {}}
    with _JSONL_CACHE_LOCK:
        held = _JSONL_CACHE.get(str(path)) or _JSONL_CACHE.get(key)
    if held is None or len(held) < 11 or held[5] != 0 or held[8] != st.st_ino or not held[3] or held[9] != 0 or held[10] is None:
        return ent                    # no WHOLE entry with its crc: nothing proves the bytes before its offset
    unchanged = held[0] == st.st_mtime and held[1] == st.st_size   # the reader's own hit test: it proved this entry
    trial = dict(ent, end=int(held[2]), guard=bytes(held[3][-_TAIL_STAMP_GUARD:]), crc=int(held[10]),
                 mtime=st.st_mtime_ns if unchanged else None)
    if not _tail_stamps_guard_ok(trial, key, st):
        return ent
    for r in held[4]:
        _tail_stamps_step(trial, r)
    return trial


def tail_stamps(path):
    """The high-water entry of transcript `path`, current to its last complete line, or None when it cannot be statted:
    {"hw": the highest counted stamp in the file, "before": {prompt uuid: the highest counted stamp written before it}, ...}.
    Brought forward from this process's entry, else the one kept beside the checkpoint, else the reader's whole entry, else
    byte zero, whichever still passes the append guard, by scanning the bytes past it; the entry is kept again when it moved.
    A whole-history read by nature, so refused inside a tail-first scope over the file."""
    _tail_guard(path, "tail_stamps")
    key = os.path.realpath(str(path))
    try:
        st = os.stat(key)
    except OSError:
        return None
    with _TAIL_STAMPS_LOCK:
        ent = _TAIL_STAMPS.get(key)
    if ent is None or not _tail_stamps_guard_ok(ent, key, st):
        ent = _tail_stamps_load(key)
        if ent is not None and not _tail_stamps_guard_ok(ent, key, st):
            ent = None
    ent = dict(ent, before=dict(ent["before"])) if ent is not None else _tail_stamps_seed(key, path, st)
    start = ent["end"]
    ent["mtime"] = st.st_mtime_ns
    if st.st_size > start:
        try:
            with open(key, "rb") as fh:
                fh.seek(start)
                nread, end, crc = 0, start, ent["crc"]
                remaining = st.st_size - start
                while remaining > 0:
                    line = fh.readline(remaining)
                    if not line or not line.endswith(b"\n"):
                        nread += len(line)
                        break                     # a line still being written: the next scan takes it whole
                    remaining -= len(line)
                    nread += len(line)
                    end += len(line)
                    if crc is not None:
                        crc = zlib.crc32(line, crc)
                    for piece in line.splitlines(keepends=True):   # the reader's boundaries (_scan_jsonl_stream)
                        piece = piece.strip()
                        if piece:
                            try:
                                _tail_stamps_step(ent, json.loads(piece.decode("utf-8", "replace")))
                            except ValueError:
                                pass
                if end > start:
                    fh.seek(max(0, end - _TAIL_STAMP_GUARD))
                    ent["guard"] = fh.read(min(_TAIL_STAMP_GUARD, end))
                    ent["end"], ent["crc"] = end, crc
        except OSError:
            return None
        if nread:
            _count_read(key, nread)
    with _TAIL_STAMPS_LOCK:
        was = _TAIL_STAMPS.get(key) or {}
        moved = (ent["end"], ent["mtime"]) != (was.get("end"), was.get("mtime"))
        _TAIL_STAMPS[key] = ent
    if moved:
        _tail_stamps_save(ent)
    return ent


def tail_window(leaf, min_events=1, max_bytes=None, stamps=None):
    """(window, None) for the tail of transcript `leaf` from the boundary nearest its end that holds at least `min_events`
    user and assistant records and passes every rule above, or (None, reason) when none does: the search REFUSES, it never
    relaxes a rule. Reads back from the end of the file, 256 KiB and then four times as much again while a boundary may
    still lie earlier, to `max_bytes` (32 MiB when not given); each read is positioned and counted like every reader byte,
    and touches no reader entry, memo or checkpoint. The reason is the nearest candidate's: no-prompt and too-short (nothing
    to start at), whole-file (the boundary is the file's first record: the whole parse is the tail parse), no-predecessor,
    predecessor-not-assistant, turn-open, predecessor-model, parent-not-predecessor, clock, uuid-repeat, stamp-floor, chain,
    compaction-in-window, tool-result-before-window, carry, command-twin, no-boundary (the tries or the bytes ran out),
    and empty or unreadable for the file itself. With `stamps` (tail_stamps' entry, as tail_open passes it) a boundary that
    passed every rule is also refused when a record before it, read or not, is stamped after the reply it follows
    (high-water), or when the entry has not reached it (high-water-unknown: appended after the scan)."""
    leaf = str(leaf)
    try:
        st = os.stat(leaf)
    except OSError:
        return None, "unreadable"
    size = st.st_size
    cap = min(size, int(max_bytes or _TAIL_SPAN_MAX))
    if cap <= 0:
        return None, "empty"
    recs, offs, ts = [], [], []       # the records read so far in file order, each one's byte offset and parsed stamp
    lo, end = size, None              # [lo, end): the bytes read; `end` is past the last complete line of the first read
    span = min(cap, _TAIL_SPAN0)
    rejected, why = set(), None       # offsets of boundaries a rule refused (more bytes cannot lift it), the nearest one's reason
    tries = deep = 0
    while True:
        at = max(0, size - span)
        try:
            with open(leaf, "rb") as fh:
                if at > 0:
                    fh.seek(at - 1)
                    fh.readline()     # to the first line starting at or after `at` (a line that starts there is kept whole)
                    at = fh.tell()
                o, chunk, nread = [], [], 0
                if at < lo:
                    chunk, c_end, nread = _scan_jsonl_stream(fh, at, o, limit=lo - at)
                    if end is None:
                        end = c_end
        except OSError:
            return None, "unreadable"
        if nread:
            _count_read(leaf, nread)
        if at < lo:
            keep = [x for x, r in enumerate(chunk) if isinstance(r, dict)]
            recs[:0] = [chunk[x] for x in keep]
            offs[:0] = [o[2 * x] for x in keep]
            ts[:0] = [parse_z(chunk[x].get("timestamp")) if chunk[x].get("uuid") else None for x in keep]
            lo = at
        whole = lo == 0
        n, prompts = 0, 0
        for i in range(len(recs) - 1, -1, -1):
            r = recs[i]
            if r.get("type") in ("user", "assistant"):
                n += 1
            if not _tail_prompt_shaped(r):
                continue
            prompts += 1
            if n < min_events or offs[i] in rejected:
                continue
            if tries >= _TAIL_TRIES:
                return None, why or "no-boundary"
            tries += 1
            j, reason = _tail_sync_point(recs, ts, i, whole)
            if reason is None:
                if deep >= _TAIL_DEEP_TRIES:
                    return None, why or "no-boundary"
                deep += 1
                reason = _tail_refusal(recs, ts, i, j)
                if reason is None and stamps is not None:
                    hw = stamps["before"].get(r["uuid"])
                    reason = "high-water-unknown" if hw is None else "high-water" if hw > ts[j] else None
                if reason is None:
                    p = recs[j]
                    return TailWindow(leaf, offs[i], end, (st.st_mtime_ns, st.st_size, st.st_ino), recs[i:], r["uuid"], ts[i],
                                      p["uuid"], ts[j], p["message"]["model"], n), None
            if reason == "no-predecessor":
                continue                                 # the record before it is not in the bytes read yet: the next read's
            why = why or reason
            if reason in _TAIL_FINAL or reason == "whole-file":
                return None, why
            rejected.add(offs[i])
        if whole or span >= cap:
            if why is None:
                why = "no-prompt" if not prompts else "too-short" if whole else "no-boundary"
            return None, why
        span = min(cap, span * 4)


def tail_open(leaf, *, files, cut_armed, min_events=1, max_bytes=None):
    """(window, None) or (None, reason): THE way to a window tail_scope takes (it refuses any other, "unopened"), so a caller
    cannot build from a window whose gates it forgot. tail_window's search, given the leaf's high-water entry, then the gates
    that need more than the records in hand, each a refusal by name:
      pending-cut           `cut_armed`: a rollback is armed, and the whole parse reads a cut the file does not show;
      high-water            (tail_window) a record written before the boundary, read or not, is stamped after the reply before
                            it; high-water-unknown, the boundary was appended after the scan;
      sibling-file-active   another file of `files` (every transcript file the whole parse reads: the leaf's candidates closed
                            over the resume lineage) was written at or after the boundary's stamp;
      sibling-high-water    another file holds a counted record stamped after the reply before the boundary (it would sort
                            into the window).
    The window then names `files` (TailWindow.also), so its scope refuses a whole read of any of them, and is marked opened.
    Keyword-only, with no default for `files` or `cut_armed`: a caller states both. Refused inside a scope over the leaf, like
    every whole read (its stamps are one)."""
    _tail_guard(leaf, "tail_open")
    if cut_armed:
        return None, "pending-cut"
    stamps = tail_stamps(leaf)
    if stamps is None:
        return None, "unreadable"
    win, why = tail_window(leaf, min_events, max_bytes, stamps=stamps)
    if win is None:
        return None, why
    for c in files or ():
        if os.path.realpath(str(c)) == win.key:
            continue
        try:
            if os.stat(c).st_mtime >= win.t:
                return None, "sibling-file-active"
        except OSError:
            continue
        other = tail_stamps(c)
        if other is None or other["hw"] > win.prev_t:
            return None, "sibling-high-water"
    win.also(files)
    win.opened = True
    return win, None


def tail_parse(win, rompuuid=None, states=None, postal_log=None, now=None, sdk_human=False, name=None):
    """The Session tree of window `win` alone, parsed inside its scope (parse_session's tail road over the window's own
    records: no candidate file, lineage file, document or cache is read or written), or TailRefused: a salvaged reply the
    window cannot place (orphan-marker, parse_session's gate), or a parse that did not open its first turn at the boundary
    record (boundary-not-opener: the record reads as no prompt, so the whole parse would fold it into the turn before)."""
    with tail_scope(win):
        tree = parse_session(win.leaf, rompuuid=rompuuid, name=name, states=states, postal_log=postal_log, now=now,
                             sdk_human=sdk_human, tail_records=win.records)
    turns = tree.get("turns") or []
    atoms = turns[0]["atoms"] if turns else []
    if not atoms or atoms[0].get("uuid") != win.uuid or (turns[0].get("trigger") or {}).get("uuid") != win.uuid:
        raise TailRefused("boundary-not-opener")
    return tree


def _tail_orphan_gate(srows, landed, win):
    """Refuse (TailRefused "orphan-marker") a tail-first parse when a salvaged-reply marker stamped at or after the reply
    before the window names a reply the window's records did not land. The whole parse places such a marker by reading what
    the transcript kept around its time, on any branch, and the tail holds only its own records: every marker the gate lets
    through landed inside the window, where both parses skip it by its uuid, or is one synthesize_orphans skips on sight,
    so the tail synthesizes none. An earlier marker is the whole parse's, in a turn before the window."""
    for r in srows or ():
        if not isinstance(r, dict) or not r.get("t") or not isinstance(r.get("orphanReply"), dict):
            continue
        if int(r["t"]) < win.prev_t:
            continue
        txt = (r["orphanReply"].get("text") or "").strip()
        if not txt or txt.startswith("API Error:") or txt == "(no content)":
            continue
        if (r["orphanReply"].get("uuid") or "") not in landed:
            raise TailRefused("orphan-marker")


def _in_the_foreground(parse):
    """`parse` (parse_session) as foreground work for the background gate (asm_foreground, _ASM_BG): no document rebuild starts
    while a page's parse runs, and a running one waits at its next chunk point until it is done. The rebuild's own parse (the
    private road, `private_entry_out`) is not, and since 2026-11-12 neither is a parse on one of the kernel's background loops
    (_ASM_BG_STAGES: the timeline builder, the judges, the jobs pass, the boot's warm parses), which nobody waits on and whose
    whole parses kept the rebuild from ever starting on a host whose documents a version bump had refused. The whole call, not
    its whole-parse road alone: in a lab the turns built after the assembly were a fifth of a page's 26 MB parse, and a rebuild
    that ran on through them still doubled it. A served or folded parse holds the gate for its few milliseconds."""
    @functools.wraps(parse)
    def run(*a, **k):
        if k.get("private_entry_out") is not None or _asm_background_stage():
            return parse(*a, **k)
        with asm_foreground():
            return parse(*a, **k)
    return run


@_in_the_foreground
def parse_session(leaf_path, rompuuid=None, name=None, color="#888888", dir=None,
                  candidate_files=None, states=None, postal_log=None, now=None, sdk_human=False,
                  leaf_override=None, asm_mode_out=None, private_entry_out=None, tail_records=None):
    """Build one session's Session -> Turn -> Atom tree from the on-disk transcript graph.

    leaf_path        the newest (leaf) transcript file; the walk's start pointer.
    rompuuid         stable session identity (binds everything). Real runs resolve it from
                     names/<sid> (the anchor sid); defaults to the leaf file stem for --test.
    candidate_files  the session's transcript files the resume walk may cross into.
                     Defaults to JUST [leaf_path] — a safe single-file parse. Cross-file
                     resume requires the caller to pass the explicit session file set;
                     we deliberately do NOT glob the project dir (that would read every
                     unrelated transcript in it). Session->files resolution is a
                     higher-layer concern, not the parser's.
    states           states/<sid>.jsonl path or rows -> idle atoms.
    postal_log       timeline/messages.jsonl path or rows -> peer rompUuid.
    leaf_override    start the walk at this record instead of the file's last (a PENDING
                     bare rollback — the kernel's pending_cut); ignored if absent from the graph.
    asm_mode_out     a list, when given, that receives the assembly path taken (see _assemble);
                     never part of the returned tree.
    private_entry_out  a list, when given: a whole parse whose assembly entry is appended there and never
                     installed or offered to the writer here (_assemble's private road, the document refresh).
    tail_records     a list, when given: the leaf's records from some point on, read by the caller, parsed ALONE
                     (the "tail" road): no candidate file, lineage file or assembly entry is read, cached or written,
                     and the walk ends where the tail's first record names a parent it does not hold. A comment
                     thread's popover reads its own records past the fork cut this way (the kernel's
                     _thread_tail_parse), never the parent's history the fork copied in front of them.
    """
    leaf_path = Path(leaf_path)
    _tf = _TAIL_SCOPE.win                           # a tail-first scope over this leaf: only the window's own records parse
    if _tf is not None and not _tf.holds(leaf_path):
        _tf = None
    if _tf is not None and tail_records is not _tf.records:
        raise WholeHistoryRead("parse_session inside the tail-first scope")
    if dir is None:
        dir = str(leaf_path.parent)
    if rompuuid is None:
        rompuuid = leaf_path.stem
    if candidate_files is None:
        candidate_files = [str(leaf_path)]
    postal_index = _load_postal_index(postal_log)
    _srows = _load_states(states)
    links = resume_fork_links(_srows)
    # The lineage closure joins the candidate set: a fork chain across several restarts needs
    # every resumed-from file present for the stitched walk to cross (the caller's anchor covers
    # only one hop). Resolved HERE so both parses (the judge's and the kernel's) inherit it from
    # the one states plumbing they already share; chain_membership shares the same helper. The
    # callers' cache keys — candidate files + the states file, whose mtime moves when a lineage
    # row lands — stay honest without knowing about these.
    _installed = []                                 # the whole entry the full road installs, if it runs (a refusal's rewrite needs it)
    if tail_records is not None:
        # the tail road: the caller's records, one adapter, the whole parse's emit over them (FileAdapter.atoms), nothing
        # installed anywhere. The pending cut is the live leaf's; a tail has none
        ad = FileAdapter([str(leaf_path)], leaf_path, records={str(leaf_path): tail_records})
        ad.sdk_human = sdk_human
        atoms = ad.atoms(rompuuid, postal_index)
        landed, cut_t, skill_loads, pre_turns = ad.landed_text_uuids(), None, dict(getattr(ad, "skill_loads", None) or {}), []
        if _tf is not None:
            _tail_orphan_gate(_srows, landed, _tf)  # a salvaged reply the window cannot place refuses the window
        _asm_stat("tail")
        if asm_mode_out is not None:
            asm_mode_out.append("tail")
    else:
        candidate_files = _lineage_closure(leaf_path, candidate_files, links)
        # The assembly cache serves/folds/rebuilds as the gates decide — a streamed append folds only
        # the new records through the shared emit code; everything else is a full parse. atoms/landed
        # come back caller-owned (fresh top-level dicts), so the mutations below never reach the cache.
        atoms, landed, cut_t, skill_loads, pre_turns = _assemble(leaf_path, candidate_files, links, rompuuid,
                                                    postal_index, sdk_human, leaf_override, mode_out=asm_mode_out,
                                                    private_out=private_entry_out, installed_out=_installed)
        if private_entry_out is not None:
            _asm_bg_yield()                         # a rebuild's chunk point (a no-op elsewhere): the emit is done, the turns are next
    # a restored tree's pre-cut turns (T323 stage 4c) come whole from the document, their synthesized atoms included; the
    # tail alone is segmented and synthesized over below, and a span or a marker from before the tail's first atom is the
    # document's (or nothing: the whole parse put it in a pre-cut turn)
    t_tail0 = min((a["t"] for a in atoms if a.get("t")), default=None) if pre_turns else None

    def _tail_idle():
        """The idle spans the tail's turns take, over the atoms as they stand (a restored tree's pre-cut turns carry theirs)."""
        idle = synthesize_idle(_srows, atoms, now)
        return [a for a in idle if t_tail0 is not None and a["t"] >= t_tail0] if pre_turns else idle
    # salvaged replies never predate the conversation the parse reads (2026-10-03): the floor is the first record of its own
    # candidate files, the same number whatever road served the atoms (whole, fold, restore, refresh), so it never races the
    # kernel's episode tick. The tail road reads no candidate file and has no floor
    orphans = [] if _tf is not None else \
        synthesize_orphans(_orphan_rows_from(_srows, candidate_files if tail_records is None else None), atoms,
                           landed_text_uuids=landed, rompuuid=rompuuid,
                           pre=(pre_turns if pre_turns else None), t_floor=t_tail0 if pre_turns else None,
                           overlay=_tail_idle)                # (a tail-first window synthesizes none: _tail_orphan_gate)
    if pre_turns:
        orphans = [a for a in orphans if t_tail0 is not None and a["t"] >= t_tail0]
    #                                            # salvaged replies FIRST: they are real atoms the turn
    #                                              grouping must absorb (idle spans overlay afterwards)
    # A salvaged reply has NO position in the transcript graph — that absence is the very thing the
    # marker exists to paper over — so the leaf_override walk cannot drop it the way it drops the
    # abandoned chain. Unfiltered, deleting a message left its reply standing alone in the chat: the
    # prompt vanished (it was on the chain) while the answer stayed (it was re-synthesized from
    # states/), which reads as though the delete half-worked (the user 2026-08-01). Filter on the CUT
    # RECORD's own timestamp — an exact event, not a window — since time is the only ordering a
    # graph-less atom has. Idle spans are deliberately NOT filtered: they describe the session's
    # working state NOW (an open span runs to `now`), not conversation content.
    if cut_t:
        orphans = [a for a in orphans if a["t"] <= cut_t]
    atoms += orphans
    idle = _tail_idle()
    atoms += idle
    tail_turns = segment_turns(atoms, rompuuid) if atoms else []
    turns = list(pre_turns) + tail_turns
    for turn in tail_turns:
        for a in turn["atoms"]:
            a.pop("_seq", None)
        turn_keys = {"id": turn["id"], "trigger": turn["trigger"], "t": turn["t"],
                     "end": turn["end"], "ended": turn["ended"], "atoms": turn["atoms"]}
        turn.clear()
        turn.update(turn_keys)
    # the cut turn (T323 stage 4b): the first turn after the last one holding a lazy (pre-cut) atom, taken here before
    # any consumer hydrates; 0 for a whole parse. The chat build renders from it (its render floor).
    cut_turn = 0
    if pre_turns:
        cut_turn = min(len(pre_turns), len(turns) - 1)   # the first tail turn (stage 4c: the pre-cut turns are the index's)
    else:
        for _i in range(len(turns) - 1, -1, -1):
            if any(a.get("lazy") is not None for a in turns[_i]["atoms"]):
                cut_turn = min(_i + 1, len(turns) - 1)
                break
    out = {"rompUuid": rompuuid, "name": name or rompuuid, "dir": dir,
            "color": color, "leafFsid": leaf_path.stem, "turns": turns,
            # for the kernel chat build's own marker interleave: its dedup reads the KEPT turns
            # only, so without this a marker whose reply landed on an abandoned branch would
            # ghost back through that second door (sorted → deterministic payloads).
            "landedTextUuids": sorted(landed),
            # the harness's own skill-load wrappers the emit skipped, {uuid: skill name}, over every file the
            # walk crossed: the judge stamps the tops older stores minted from them off this (T333)
            "skillLoads": skill_loads}
    if cut_turn:
        out["cutTurn"] = cut_turn                   # a restored tree only (T323 stage 4b): where its lazy atoms ended
    if private_entry_out is not None or tail_records is not None:
        return out                                  # a private parse read no document: a refusal flagged for the leaf is its own parse's
    _rk = os.path.realpath(str(leaf_path))
    with _ASM_CKPT_LOCK:
        _refused = _ASM_CHAIN_REFUSED_PATHS.pop(_rk, None)
    _why, _refused_off = _refused if _refused is not None else (None, None)
    if _why is not None and _CKPT_DIR_FN is not None:
        _held = _installed[-1] if _installed else None   # the entry this parse installed: the writer writes from it even once evicted,
        _readers = _reader_entries_of(_held) if _held is not None else {}   # at the offsets of the reader entries it came from
        if documents_write_here():                  # the chain proof refused the standing document and this whole parse produced a
            _asm_write_after_refusal(leaf_path, rompuuid, sdk_human, out, _why, _refused_off, _held, _readers)   # sound tree: now
        else:                                       # a parse on a frame thread (the pusher's build, a request): the rewrite is the
            let_go = 0                              #  document writer's, at its next cycle, from the entry this parse installed
            with _ASM_CKPT_LOCK:
                _ASM_REFUSAL_HANDOFF.pop(_rk, None)
                _ASM_REFUSAL_HANDOFF[_rk] = (leaf_path, rompuuid, sdk_human, out, _why, _refused_off, _held, _readers)
                while len(_ASM_REFUSAL_HANDOFF) > _ASM_CACHE_MAX:   # each holds a whole entry: bounded like the cache. One let go
                    _ASM_REFUSAL_HANDOFF.pop(next(iter(_ASM_REFUSAL_HANDOFF)))   # leaves no mark, so the leaf's next parse proves
                    let_go += 1                     #  and rewrites again; counted, never silent
            _asm_stat("write:afterRefusalHandedOff")
            if let_go:
                _asm_stat("write:afterRefusalLetGo", let_go)
    return out


_ASM_REFUSAL_MOOT = ("written", "noEntry", "restored")   # the writer's skips that say the parse's entry has moved on (its document
#                                                          was written since, or replaced by a restore; noEntry only when the parse
#                                                          installed none to hand over, a fallback road's)


def _asm_write_after_refusal(leaf_path, rompuuid, sdk_human, tree, why, refused_off, held=None, held_readers=None):
    """The rewrite after a refused document (T402 follow-up): the whole parse that followed the refusal installed a sound entry,
    and its document is written from it, carrying the childless bit, so the next restore takes it. Run by the parse itself
    on the document writer's thread, and otherwise by the writer at its write phase (documents_pay_handoffs, 2026-10-08), which
    is why the writer's skips that say the entry moved on since (_ASM_REFUSAL_MOOT: its document written meanwhile, or the
    entry replaced by a restore) leave no mark: a mark there would refuse the very document just written, or one no parse has
    proven against. `held` is the entry the parse installed (the review of PR 155): when the assembly cache has evicted it
    by the writer's turn (or a release let it go), the document is written from it, as the parse would have written it on
    its own thread; before, that was "no entry", nothing written and nothing marked, and every later parse of the leaf paid
    the proof and the whole parse again. `held_readers` are the reader entries that entry's records came from, whose record
    offsets the writer takes when the cache no longer holds them (review of PR 158). A decline for a blip of the moment
    (_ASM_SKIP_BLIP: a stat or a guard read refused, the offsets not in memory, a write refused) marks nothing of its own:
    it says nothing about the document, and marking it sent every later parse of the leaf straight to a whole parse until
    the leaf changed (an idle leaf: every boot); a refusal the proof made for the tail's shape is marked all the same, since
    that is the proof's evidence. Any other decline is marked like a shape, as before. Returns whether a document was
    written (the writer's progress events learn from writes that wrote)."""
    _rk = os.path.realpath(str(leaf_path))
    wrote = False
    try:
        reasons = []
        if asm_checkpoint_write(leaf_path, rompuuid, sdk_human, tree=tree, reason_out=reasons, who="refusal", held=held,
                                held_readers=held_readers):
            _asm_stat("write:afterRefusal")         # carrying the childless bit, so the next restore takes it (T402 follow-up)
            wrote = True
            with _ASM_CKPT_LOCK:
                _new_off = _ASM_LAST_WRITE_CUT.get(_rk)
            if why == "shape" and _new_off is not None and _new_off != refused_off:
                _asm_stat("write:afterRefusalMovedCut")   # the rewrite moved the cut (stage one b: the settled turns advanced it, or a
                why = None                          #  compaction landed): a new tail, proven afresh at the next restore, so the mark for
                #                                      the refused cut's shape does not stand over it (the writer retired the old mark)
        elif reasons and reasons[-1] in _ASM_REFUSAL_MOOT:
            _asm_stat("write:afterRefusalMoot")     # the entry moved on: the next parse proves whatever stands then
            return False
        elif reasons and reasons[-1] in _ASM_SKIP_BLIP:
            _asm_stat("write:afterRefusalBlip")     # a blip of the moment (review of PR 158): no evidence about the document, so
            #                                         no mark of its own; the proof's verdict stands as it was (a refused shape is
            #                                         still marked below), and the next parse proves and offers the rewrite again
        else:
            _asm_stat("write:afterRefusalSkipped")   # the writer declined (its own skip reason is counted under asmCheckpoint.skipped)
            why = "shape"                           # a declined offer, whatever the refusal's reason (a legacy document whose
            #                                         whole parse builds past the cap, say), would repeat the proof, the second
            #                                         walk, the build and the offer at every boot: marked like a shape (low 3)
    except Exception as e:                          # noqa: BLE001 — the flag was popped by the parse, so a write that RAISES is not
        _say_once("assembly checkpoint: %s not rewritten after a refusal: %r" % (leaf_path, e))   # retried here: the settle's road
        #                                                                                             writes the entry at its next drop
    if why == "shape":
        _asm_mark_refused(leaf_path, "shape", rompuuid, sdk_human)   # AFTER the write, so an accepted write cannot erase it: the same cut
        #                                     reproduces the same refusal until the leaf moves (round two, medium 1)
    return wrote



register_whole_read_passthrough(parse_session)   # the parse's own entry (T384)

def task_store_dir(fsid):
    """Claude Code's task store for one transcript stem: <CLAUDE_CONFIG_DIR or ~/.claude>/tasks/<fsid>,
    resolved at call time (the env var, as task_store_plan reads it)."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")) / "tasks" / str(fsid)


def task_store_fp(fsid):
    """The task store's identity for the judge gate: None when the stem has no store dir (task_store_plan
    returns None too, and the caller folds the transcript instead), else the sorted (name, mtime_ns, size)
    of its item files. An item created, deleted or flipped in place moves it. A dir that exists but cannot
    be listed raises OSError, as task_store_plan does: the gate then runs the stage and stamps nothing,
    and the stage surfaces the failure loudly. Item files are stat'd by name, never read: a stat is all
    identity needs."""
    if not fsid:
        return None
    d = task_store_dir(fsid)
    if not d.is_dir():
        return None
    out = []
    with os.scandir(d) as it:                              # raises OSError → the caller's bypass
        for e in it:
            if not e.name.endswith(".json"):
                continue
            try:
                st = e.stat()
            except FileNotFoundError:
                continue                                   # deleted between the listing and the stat
            out.append((e.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(out))


def task_store_plan(fsid):
    """The agent's to-do list read from Claude Code's LIVE task store (<config>/tasks/<fsid>/<N>.json,
    honoring $CLAUDE_CONFIG_DIR) — the AUTHORITATIVE state TaskList/TaskGet read, updated by EVERY
    writer including subagents. The transcript fold (declared_plan below) is a lossy reconstruction:
    it misses a completion whose record fell off the transcript's live chain — an api-error retry
    forks the parent graph and the abandoned branch keeps the TaskUpdate that actually RAN (store
    updated, transcript forgot), leaving a mirror card phantom-open that re-mints itself after every
    clear (the 2026-07-09 g204 loop). Same item shape as declared_plan: [{key, text, activeForm,
    status}], ordered by numeric id. Returns None when the fsid has no store dir — a session that
    never declared a plan there (the caller may fall back to the fold). Raises OSError when the dir
    EXISTS but can't be listed: that is the authoritative source failing, and the caller must surface
    it loudly, never silently fold (repo policy). A single corrupt item file is skipped."""
    if not fsid:
        return None
    d = task_store_dir(fsid)
    if not d.is_dir():
        return None
    items = []
    for n in os.listdir(d):                                # raises OSError → the caller surfaces it
        if not n.endswith(".json"):
            continue
        try:
            t = json.loads((d / n).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(t, dict):
            continue
        key = str(t.get("id") or n.rsplit(".", 1)[0])
        af = t.get("activeForm")
        items.append({"key": key, "text": str(t.get("subject") or ""),
                      "activeForm": str(af) if af else None,
                      "status": str(t.get("status") or "pending")})
    items.sort(key=lambda t: (0, int(t["key"])) if t["key"].isdigit() else (1, t["key"]))
    return items


def plan_atoms(session):
    """The atoms declared_plan reads bodies from: assistant atoms calling TaskCreate or TaskUpdate and the user atoms
    carrying those calls' results. A lazy atom answers from its scalars (tu = [[id, name]], tr = [tool_use_id]), a
    hydrated one from its blocks, so the list costs no read; hydrating it reads nothing for a session that declared
    no plan (the planner asks every pass for every session with no task store: the whole prefix a pass would
    otherwise seek-read, T323 stage 4a review). A restored pre-cut turn whose stored tool calls (`tools`: the
    [id, name] pairs of its assistant atoms, written from the same atoms by atom_tool_uses) name no task call is
    passed over without building an atom, as the kernel's task fold passes it (2026-10-23: the walk built or touched
    every pre-cut atom of a long session on every planner pass, to find none)."""
    out, ids = [], set()
    for turn in session.get("turns", []):
        if turn.get("pre") and not any(n in ("TaskCreate", "TaskUpdate") for _i, n in turn.get("tools") or ()):
            continue
        for a in turn["atoms"]:
            if a.get("type") != "assistant":
                continue
            lz = a.get("lazy")
            if lz is not None:
                calls = [(i, nm) for i, nm in (lz.get("tu") or [])]
            else:
                calls = [(b.get("id"), b.get("name")) for b in _content(a.get("message"))
                         if isinstance(b, dict) and b.get("type") == "tool_use"]
            mine = [i for i, nm in calls if nm in ("TaskCreate", "TaskUpdate")]
            if mine:
                out.append(a); ids.update(mine)
    if not ids:
        return out
    for turn in session.get("turns", []):
        for a in turn["atoms"]:
            if a.get("type") != "user":
                continue
            lz = a.get("lazy")
            if lz is not None:
                got = lz.get("tr") or []
            else:
                got = [b.get("tool_use_id") for b in _content(a.get("message")) if isinstance(b, dict) and b.get("type") == "tool_result"]
            if ids.intersection(got):
                out.append(a)
    return out


def declared_plan(session):
    """The agent's OWN to-do list (Claude Code's Task tool) folded into ordered items
    [{key, text, activeForm, status}] — the FALLBACK behind task_store_plan for a session with no
    live task store, so downstream (the judge's plan-sync) sees a generic 'declared plan' shape
    instead of raw tool calls. Mirrors the kernel's _fold_tasks, with the same blind spots: only the
    MAIN agent's TaskCreate/TaskUpdate calls, and only those on the transcript's live chain — and the
    same skips: a TaskCreate the CLI rejected (its paired tool_result carries is_error) is not a step,
    and a rejected TaskUpdate moves none.
    `key` is the stable `Task #N` id lifted from TaskCreate's
    result text (a creation-order `cN` fallback if the result is unreadable); `status` rides each
    TaskUpdate. Only TaskCreate/TaskUpdate are folded — plain TodoWrite (no durable ids) is not
    used by romp. Empty list if the session declared no plan."""
    sel = plan_atoms(session)                              # the task calls and their results, in turn order per type
    hydrate(sel, by="declared_plan")                       # only those bodies: nothing read for a session with no plan
    results = {}                                           # tool_use_id → result content (a TaskCreate's carries 'Task #N')
    rejected = set()                                       # tool_use_ids whose result came back is_error
    for a in sel:
        if True:
            if a.get("type") != "user":
                continue
            for b in (a.get("message") or {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    results[b["tool_use_id"]] = b.get("content")
                    if b.get("is_error"):
                        rejected.add(b["tool_use_id"])
    tasks, order = {}, 0
    for a in sel:
        if True:
            if a.get("type") != "assistant":
                continue
            for b in (a.get("message") or {}).get("content", []) or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "TaskCreate":
                    # A TaskCreate the CLI rejected (a malformed call — no `subject`, or a {tasks: [...]}
                    # batch — answered by an is_error tool_result naming the missing field) created no task,
                    # so it is not a declared step. Folded, it became a keyless item with empty text that
                    # _sync_declared_plan minted as a standalone open "(declared step)" card no TaskUpdate
                    # could ever close. Keyed on the result's is_error, as the kernel's fold is.
                    if b.get("id") in rejected:
                        continue
                    # Only a TaskCreate's result is ever read, so only it is encoded, here, to the same text
                    # the regex saw when every result was encoded up front, as the kernel's _fold_tasks does.
                    r = results.get(b.get("id"), "")
                    m = re.search(r"Task #(\d+)", (r if isinstance(r, str) else json.dumps(r)) or "")
                    key = m.group(1) if m else "c%d" % order
                    af = inp.get("activeForm")
                    tasks[key] = {"_order": order, "key": key, "text": str(inp.get("subject") or ""),
                                  "activeForm": str(af) if af else None, "status": "pending"}
                    order += 1
                elif b.get("name") == "TaskUpdate":
                    if b.get("id") in rejected:            # the same rejection-keyed skip as the kernel's
                        continue                           # _fold_tasks: a refused update moved nothing
                    t = tasks.get(str(inp.get("taskId", "")))
                    if t:
                        t["status"] = str(inp.get("status") or t["status"])
    return sorted(tasks.values(), key=lambda t: t["_order"])


# ───────────────────────── CLI ─────────────────────────
def _hh(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "--:--:--"


def _atom_line(a):
    t = a["type"]
    if t == "idle":
        return "    · idle            %s-%s  (not working)" % (_hh(a["t"]), _hh(a.get("end")))
    if t == "system":
        cm = a.get("compact_metadata") or {}
        return "    · system:%-9s %s  trigger=%s pre_tokens=%s" % (
            a.get("subtype", "?"), _hh(a["t"]), cm.get("trigger"), cm.get("pre_tokens"))
    blocks = _content(a.get("message"))
    kinds = _block_types(blocks)
    if t == "assistant":
        sr = (a.get("message") or {}).get("stop_reason")
        tools = [b.get("name") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        extra = ("tools=" + ",".join(tools)) if tools else ""
        return "    · assistant       %s  %s %s (stop=%s)" % (_hh(a["t"]), "+".join(kinds), extra, sr)
    # user
    author = a.get("author")
    auth = (("peer:" + str(author.get("peer"))) if isinstance(author, dict)
            else (author or "-"))
    snippet = _text_of(blocks)[:48].replace("\n", " ")
    if not snippet and _has_tool_result(blocks):
        snippet = "(tool_result)"
    return "    · user/%-11s %s  %s" % (auth, _hh(a["t"]), snippet)


def _dump(session):
    s = session
    print("Session %s  [%s]  leaf=%s" % (s["name"], s["rompUuid"], s["leafFsid"]))
    print("  dir=%s  color=%s  turns=%d" % (s["dir"], s["color"], len(s["turns"])))
    for i, turn in enumerate(s["turns"], 1):
        trig = turn["trigger"]
        tatom = next((a for a in turn["atoms"] if trig and a.get("uuid") == trig["uuid"]), None)
        tlabel = "autonomous"
        if tatom:
            author = tatom.get("author")
            tlabel = (("peer:" + str(author.get("peer"))) if isinstance(author, dict)
                      else (author or "?")) + " " + repr(_text_of(_content(tatom.get("message")))[:40])
        segs = segments(turn)
        print("\n  Turn %d  [%s-%s]  ended=%s  segments=%d  trigger=%s" % (
            i, _hh(turn["t"]), _hh(turn["end"]), turn["ended"], len(segs), tlabel))
        for a in turn["atoms"]:
            print(_atom_line(a))


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("--test", "--emit"):
        sys.stderr.write("usage: romp-event-model [--test | --emit] <transcript> "
                         "[--rompuuid X] [--states PATH] [--name N]\n")
        sys.exit(2)
    mode, path = args[0], args[1]
    opts = {}
    rest = args[2:]
    for i in range(0, len(rest) - 1, 2):
        opts[rest[i].lstrip("-")] = rest[i + 1]
    states = opts.get("states")
    if states is None:                       # default: states/<leaf-stem>.jsonl if present
        cand = STATES_DIR / (Path(path).stem + ".jsonl")
        states = str(cand) if cand.exists() else None
    session = parse_session(path, rompuuid=opts.get("rompuuid"), name=opts.get("name"),
                            states=states, now=int(time.time()))
    if mode == "--emit":
        sys.stdout.write(json.dumps(plain_tree(session), indent=1, sort_keys=True, default=lambda o: "<unserializable>"))   # a restored tree's pre-cut turns built plain (stage 4c)
        sys.stdout.write("\n")
    else:
        _dump(session)


if __name__ == "__main__":
    main()
