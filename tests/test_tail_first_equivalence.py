#!/usr/bin/env python3
"""Tail-first pages, step 1 (2026-11-12): the DIFFERENTIAL test. A provisional chat page is built from the end of a
transcript alone; it may be shown to someone only if it is CONSISTENT with the page the whole transcript gives, and
consistent is defined exactly: for a window that starts at boundary record B, the provisional build's events from B's
event on equal the whole build's events from B's event on, event for event and field for field, except the fields of a
piece the provisional build declares pending, and less the live cards the whole page ends on (the to-do list, the queued
bubble, a launch error: none of them a transcript event, all of them the liveTail piece every frame declares pending).

Pinned here, over GENERATED synthetic transcripts (a seeded script of the shapes a transcript takes: turns with tool
calls, compactions with and without a replayed tail, a manual compact's detached pair, a broken stitch, rewinds onto an
earlier reply with the abandoned turns left in the file, /clear, a session born as a fork, prompts queued and spliced in
mid-turn or dequeued after a reply, salvaged replies the transcript kept and did not keep, background agents whose
return lands turns after their launch, retry and effort notes from the side store, a stop, a slash command, API errors
flushed late, a record written twice, a clock that stepped back):
  * for EVERY candidate window of every transcript, em.tail_window refuses, or a later gate does, or the provisional
    frame equals the whole build three ways (against the reference build the shadow compares with, against the whole
    build's own events, and the reference against the whole build), with only declared pieces pending;
  * the shadow job over the same corpus never reports a difference, and how often it refuses, by reason, is printed
    (run with -s) and bounded, so a boundary rule that started refusing everything would fail here;
  * the rewound-branch case that broke a naive tail parse, by name: a tail cut at a prompt with no boundary rule shows
    turns the person abandoned, and the same check that passes for tail_window fails for it;
  * a clock that stepped back further back than the bytes a search reads is what the first three mechanisms cannot see
    and the shadow's comparison measures: it is reported through the error center with ids and field names only.
Synthetic transcripts only: invented prompt text, placeholder ids, the demo names (a notes-api with web, api and tests)."""
import collections
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_tail_first_eq", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

NOW = 1781300000
T0 = NOW - 12 * 3600
PEER = "99999999-8888-4777-8666-000000000000"                 # a peer session's id (postal senders, the fork's parent)
MODELS = ("claude-synthetic-a", "claude-synthetic-b")
SEEDS = range(40)                                             # the corpus: one generated transcript per seed
# The fields a pending piece may excuse on an event that names it, written out here and not read from the kernel: widening
# what a placeholder excuses is a decision this file has to be changed for.
OWNED = {"agentLaunches": {"output", "isError", "agentRunning", "agentGist", "running"},
         "taskOutputs": {"taskOutputs"},
         "eventKey": {"uuid", "key"}}
ALWAYS = {"turnNumbers", "ledger", "firstSeen", "scrollBack", "status", "queued", "liveTail"}
TOPICS = ("the notes list", "the tag filter", "the export job", "the retry cap", "the digest file", "the search index",
          "the archive view", "the share link", "the rate limiter", "the import path")
VERBS = ("add pagination to", "tighten", "rename", "write a test for", "profile", "document", "simplify", "review")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def sid_of(seed):
    return "aaaaaaaa-%04d-4222-8333-666666666666" % seed


def U(t, text, uuid, parent, ps="typed", **kw):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "message": {"role": "user", "content": text}}
    if ps is not None:
        r["promptSource"] = ps
    r.update(kw)
    return r


def A(t, uuid, parent, text="", tools=(), stop="end_turn", model=MODELS[0], thinking=None, mid=None):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if text:
        content.append({"type": "text", "text": text})
    for i, (name, inp) in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": name, "input": inp})
    msg = {"role": "assistant", "id": mid or ("msg_" + uuid), "content": content, "stop_reason": stop,
           "usage": {"input_tokens": 100, "output_tokens": 20, "output_tokens_details": {"thinking_tokens": 40 if thinking else 0}}}
    if model:
        msg["model"] = model
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "message": msg}


def R(t, tool_id, uuid, parent, content="ok", **kw):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]}}
    r.update(kw)
    return r


def Q(t, op, content=None):
    return {"type": "queue-operation", "timestamp": iso(t), "operation": op, "content": content}


def ATT(t, prompt, uuid, parent):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False,
            "attachment": {"type": "queued_command", "prompt": prompt}}


def SYS(t, subtype, uuid, parent, **extra):
    r = {"type": "system", "subtype": subtype, "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False}
    r.update(extra)
    return r


def NOTE(tool_id, status="completed", result="the agent's closing summary"):
    return ("<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>%s</tool-use-id>\n<status>%s</status>\n"
            "<summary>Agent \"check the export job\" %s</summary>\n<result>%s</result>\n</task-notification>"
            % (tool_id, status, status, result))


class Script:
    """One generated transcript: its records in file order, its side-store rows, its postal rows and (sometimes) the
    registry record of a session born as a fork. Deterministic in `seed`. Every shape is the CLI's as the golden scenarios
    record it (tests/test_event_model_golden.py); the content is invented."""

    def __init__(self, seed, steps=None):
        self.rnd = random.Random(seed)
        self.sid = sid_of(seed)
        self.tag = "%04d" % seed
        self.recs, self.rows, self.mail = [], [], []
        self.t, self.i = T0 + seed * 7, 0
        self.tip = None              # the record the next prompt chains onto
        self.path = []               # the replies that ended a turn on the active path, oldest first (a rewind's targets)
        self.after = {}              # reply uuid -> the last bookkeeping record written after it (the next prompt's parent)
        self.model = MODELS[0]
        self.said = []               # every prompt text so far
        self.agents = []             # background launches that have not returned: tool-use ids
        self.abandoned = set()       # uuids of the records a rewind left behind
        self.kinds = collections.Counter()
        self.fork = None
        self.enqueued = None         # a prompt queued during the running turn, dequeued after its reply
        steps = steps if steps is not None else self.rnd.randint(9, 16)
        self.turn()
        self.turn()
        menu = (("turn", 12), ("rewind", 3), ("clear", 1), ("compact", 2), ("compact_replay", 2), ("compact_manual", 1),
                ("compact_broken", 1), ("splice", 3), ("queue_next", 2), ("orphan", 3), ("agent", 3), ("agent_back", 3),
                ("notes", 3), ("idle", 2), ("stop", 1), ("command", 1), ("api_error", 1), ("eclipse", 1), ("model", 2),
                ("postal", 2), ("twice", 1), ("clock", 1), ("noise", 1), ("repeat", 1), ("sync_agent", 1))
        names, weights = [n for n, _ in menu], [w for _, w in menu]
        for _ in range(steps):
            getattr(self, "do_" + self.rnd.choices(names, weights)[0])()
        for _ in range(self.rnd.randint(1, 4)):                      # every script ends on ordinary turns: the windows
            self.turn()                                              #  whose earlier history holds the shapes above
        if self.rnd.random() < 0.2:
            self.turn(end=False)                                     # ...and some on a turn still running
        if self.rnd.random() < 0.3 and self.path:
            self.fork = {"sid": PEER, "name": "api", "cut": self.rnd.choice(self.path), "t": T0}

    # ── primitives ──
    def u(self, kind):
        self.i += 1
        return "%s%03d-%s" % (kind, self.i, self.tag)

    def tick(self, lo=1, hi=40):
        self.t += self.rnd.randint(lo, hi)
        return self.t

    def emit(self, r):
        self.recs.append(r)
        return r.get("uuid")

    def text(self):
        return "%s %s, pass %d" % (self.rnd.choice(VERBS), self.rnd.choice(TOPICS), len(self.said) + 1)

    def turn(self, text=None, ps=None, end=True, splice=False, agent=False, sync_agent=False, errors=False, queue_next=None,
             gap=None):
        self.kinds["turn"] += 1
        text = text or self.text()
        self.tick(*(gap if gap is not None else ((0, 0) if self.rnd.random() < 0.06 else (1, 40))))
        if self.enqueued is not None and text == self.enqueued:
            self.emit(Q(self.t, "dequeue"))
            self.enqueued = None
        tip = self.emit(U(self.t, text, self.u("u"), self.tip, ps=ps if ps is not None else self.rnd.choice(("typed", "sdk"))))
        self.said.append(text)
        if errors:                                                   # a retried request: the reminder the CLI writes at
            tip = self.emit({"type": "attachment", "timestamp": iso(self.t), "uuid": self.u("m"), "parentUuid": tip,   # request start
                             "isSidechain": False, "attachment": {"type": "total_tokens_reminder"}})
            for n in range(self.rnd.randint(1, 3)):
                tip = self.emit(SYS(self.tick(1, 3), "api_error", self.u("e"), tip, level="error", retryAttempt=n + 1,
                                    maxRetries=10, retryInMs=1000, source="request_retry",
                                    error={"status": 429, "message": "synthetic rate limit"}))
        for step in range(self.rnd.randint(0, 3)):
            a = self.u("a")
            name = self.rnd.choice(("Bash", "Read", "Edit", "Grep"))
            if self.rnd.random() < 0.25:                             # a message written as two records: its text, then its call
                first = self.emit(A(self.tick(), self.u("a"), tip, text="looking at it", stop=None, model=self.model, mid="msg_" + a))
                tip = first
            self.emit(A(self.tick(), a, tip, tools=[(name, {"command": "true"} if name == "Bash" else {"pattern": "cap"})],
                        stop="tool_use", model=self.model, thinking="weighing the options" if self.rnd.random() < 0.3 else None))
            tid = "tu_%s_0" % a
            if queue_next and step == 0:
                self.emit(Q(self.tick(1, 5), "enqueue", queue_next))
                self.enqueued = queue_next
            if splice and step == 0:
                # a message sent while the tool runs: the enqueue op, the result the splice waited for, the remove op, then
                # the attachment, stamped with its SEND time and written after the result
                note = "also check %s, note %d" % (self.rnd.choice(TOPICS), self.i)
                sent = self.tick(1, 5)
                self.emit(Q(sent, "enqueue", note))
                got = self.emit(R(self.tick(1, 9), tid, self.u("r"), a))
                self.emit(Q(self.t, self.rnd.choice(("remove", "popAll"))))
                tip = self.emit(ATT(sent, note, self.u("q"), got))
                self.kinds["splice"] += 1
            else:
                tip = self.emit(R(self.tick(), tid, self.u("r"), a))
        if agent or sync_agent:
            a = self.u("a")
            ask = {"description": "check the export job", "prompt": "read the export job and report what it retries",
                   "subagent_type": "general-purpose"}
            if agent:
                ask["run_in_background"] = True
            self.emit(A(self.tick(), a, tip, tools=[("Agent", ask)], stop="tool_use", model=self.model))
            tid = "tu_%s_0" % a
            aid = "a%016x" % self.rnd.getrandbits(64)
            if agent:
                tip = self.emit(R(self.tick(1, 3), tid, self.u("r"), a, content="Async agent launched.",
                                  toolUseResult={"isAsync": True, "status": "async_launched", "agentId": aid,
                                                 "description": ask["description"], "prompt": ask["prompt"]}))
                self.agents.append(tid)
                self.kinds["agent"] += 1
            else:
                tip = self.emit(R(self.tick(5, 50), tid, self.u("r"), a, content="the export job retries three times",
                                  toolUseResult={"status": "completed", "agentId": aid, "prompt": ask["prompt"]}))
                self.kinds["sync_agent"] += 1
        if not end:
            self.tip = tip
            return
        self.tip = self.emit(A(self.tick(), self.u("a"), tip, text="finished: " + text, model=self.model))
        self.path.append(self.tip)
        self.book()

    def book(self):
        """The harness's turn-end bookkeeping, as the CLI writes it after most replies: a stop hook's failure note, the hook
        summary, the turn's duration, a recap for a person who was away. Records on the chain that show nothing, each chained
        onto the one before and the next prompt onto the last; now and then one hangs off the reply beside its neighbour, or
        is stamped later than the prompt that follows it."""
        if self.rnd.random() < 0.3:
            return
        self.kinds["bookkeeping"] += 1
        reply = self.tip
        if self.rnd.random() < 0.2:
            self.tip = self.emit({"type": "attachment", "timestamp": iso(self.t), "uuid": self.u("m"), "parentUuid": self.tip,
                                  "isSidechain": False, "attachment": {"type": "hook_non_blocking_error", "hookName": "Stop",
                                                                       "stderr": "synthetic hook failure"}})
        self.tip = self.emit(SYS(self.tick(0, 1), "stop_hook_summary", self.u("h"), self.tip, hookCount=1, hookErrors=[],
                                 level="suggestion", preventedContinuation=False))
        if self.rnd.random() < 0.3:
            beside = self.rnd.random() < 0.06
            self.tip = self.emit(SYS(self.t, "turn_duration", self.u("d"), reply if beside else self.tip, durationMs=4200,
                                     isMeta=False))
            if beside:
                self.kinds["bookkeeping beside"] += 1
        if self.rnd.random() < 0.15:
            late = self.rnd.random() < 0.15
            self.tip = self.emit(SYS(self.t + 90 if late else self.tick(1, 30), "away_summary", self.u("w"), self.tip,
                                     content="a synthetic recap of the work so far", isMeta=False))
            if late:
                self.kinds["bookkeeping late"] += 1
        self.after[reply] = self.tip

    def row(self, **kw):
        self.rows.append(dict(kw))

    # ── the shapes ──
    def do_turn(self):
        self.turn()

    def do_splice(self):
        self.turn(splice=True)

    def do_queue_next(self):
        nxt = "then %s" % self.text()
        self.kinds["queue_next"] += 1
        self.turn(queue_next=nxt)
        self.turn(text=nxt, ps="queued")

    def do_rewind(self):
        """Back onto an earlier reply: the abandoned turns stay in the file, and the next prompt chains onto the reply the
        person went back to."""
        if not self.path:
            return
        self.kinds["rewind"] += 1
        at = self.rnd.randrange(len(self.path)) if self.rnd.random() < 0.4 else len(self.path) - 1
        last = self.after.get(self.path[-1], self.path[-1])
        if self.tip != last:
            at = len(self.path) - 1                              # (a stop or a command stands at the tip: abandon from the last reply)
        back = self.path[at]
        self.tip = last
        n0 = len(self.recs)
        for _ in range(self.rnd.randint(1, 2)):
            self.turn()
        self.abandoned.update(r["uuid"] for r in self.recs[n0:] if r.get("uuid"))
        self.path = self.path[:at + 1]
        self.tip = back if self.rnd.random() < 0.5 else self.after.get(back, back)   # onto the reply, or its bookkeeping

    def do_clear(self):
        self.kinds["clear"] += 1
        self.tip, self.path = None, []

    def do_compact(self, replay=False):
        """An attached (auto) compaction: the boundary stitched to the reply before it, its summary, and with `replay` the
        restored tail, an earlier prompt written again under a new uuid before any reply: the last one, or one from a few
        turns back (which a tail that starts between the two would show as a new ask)."""
        self.kinds["compact_replay" if replay else "compact"] += 1
        b, s = self.u("cb"), self.u("cs")
        tb = self.tick(5, 60)
        self.emit(SYS(tb, "compact_boundary", b, None, logicalParentUuid=self.tip, isMeta=False,
                      compactMetadata={"trigger": "auto", "preTokens": 180000, "postTokens": 6000}))
        self.emit(U(tb - 1 if self.rnd.random() < 0.3 else tb + 1, "summary of the conversation so far", s, b, ps=None,
                    isCompactSummary=True, isVisibleInTranscriptOnly=True))
        self.t = tb + 1
        tip = s
        if replay and self.said:
            again = self.said[-1] if self.rnd.random() < 0.5 else self.rnd.choice(self.said[-4:])
            tip = self.emit(U(self.tick(0, 2), again, self.u("u"), tip, ps=None))
            if self.rnd.random() < 0.5:
                tip = self.emit(A(self.tick(), self.u("a"), tip, text="picking the work back up", model=self.model))
                self.path.append(tip)
        self.tip = tip

    def do_compact_replay(self):
        self.do_compact(replay=True)

    def do_compact_manual(self):
        """A live manual /compact: the pair lands first as a detached side branch, then the command's records."""
        self.kinds["compact_manual"] += 1
        tag = self.u("k")
        t_issue = self.tick(5, 30)
        t_done = self.tick(5, 20)
        cb, cs, rt, cw, so = ("cb" + tag, "cs" + tag, "rt" + tag, "cw" + tag, "so" + tag)
        self.emit(SYS(t_done, "compact_boundary", cb, None, logicalParentUuid=self.tip, isMeta=False,
                      compactMetadata={"trigger": "manual", "preTokens": 120000, "postTokens": 5000}))
        self.emit(U(t_done, "summary of the conversation so far", cs, cb, ps=None, isCompactSummary=True,
                    isVisibleInTranscriptOnly=True, promptId="p" + tag))
        self.emit(U(t_issue, "/compact", rt, self.tip, ps=None, isMeta=True, promptId="p" + tag))
        self.emit(U(t_issue, "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n"
                             "<command-args></command-args>", cw, rt, ps=None, promptId="p" + tag))
        self.tip = self.emit(U(t_done, "<local-command-stdout>Compacted (ctrl+o to see full summary)</local-command-stdout>",
                               so, cw, ps=None, promptId="p" + tag))

    def do_compact_broken(self):
        """A boundary whose stitch names a uuid in no record; the real leaf rides its preserved segment."""
        if not self.tip:
            return
        self.kinds["compact_broken"] += 1
        b = self.u("cb")
        self.emit(SYS(self.tick(5, 60), "compact_boundary", b, None, logicalParentUuid="ghost-" + b, isMeta=False,
                      compactMetadata={"trigger": "auto", "preTokens": 99999,
                                       "preservedSegment": {"headUuid": self.tip, "anchorUuid": self.tip, "tailUuid": self.tip}}))
        self.tip = b

    def do_orphan(self):
        """A salvaged-reply marker in the side store: for a reply the transcript never kept, for one it did, or the CLI's
        own error text."""
        self.kinds["orphan"] += 1
        kind = self.rnd.choice(("lost", "lost", "landed", "error"))
        t = self.t + self.rnd.randint(0, 2)
        if kind == "landed" and self.path:
            last = next(r for r in reversed(self.recs) if r.get("uuid") == self.path[-1])
            self.row(t=t, orphanReply={"uuid": last["uuid"], "text": last["message"]["content"][-1]["text"]})
        elif kind == "error":
            self.row(t=t, orphanReply={"uuid": self.u("o"), "text": "API Error: 529 Overloaded. This is a server-side issue."})
        else:
            self.row(t=t, orphanReply={"uuid": self.u("o"), "text": "a reply the transcript never kept, %d" % self.i})
        if self.rnd.random() < 0.5:
            self.tick(2, 9)

    def do_agent(self):
        self.turn(agent=True)

    def do_sync_agent(self):
        self.turn(sync_agent=True)

    def do_agent_back(self):
        """A background agent's return: the notification as a record of its own, which the running conversation folds in,
        and the reply to it."""
        if not self.agents:
            return self.do_agent()
        self.kinds["agent_back"] += 1
        tid = self.agents.pop(self.rnd.randrange(len(self.agents)))
        tip = self.emit(U(self.tick(), NOTE(tid), self.u("n"), self.tip, ps="system"))
        self.tip = self.emit(A(self.tick(), self.u("a"), tip, text="the agent is back with its report", model=self.model))
        self.path.append(self.tip)
        self.book()

    def do_notes(self):
        """Side-store notes: a retry that recovered, one that gave up, an effort change, a command gesture, a spend cut."""
        self.kinds["notes"] += 1
        for _ in range(self.rnd.randint(1, 3)):
            t = self.t + self.rnd.randint(0, 3)
            kind = self.rnd.choice(("recovered", "gaveup", "effort", "gesture", "cut"))
            if kind == "recovered":
                self.row(t=t, retriesRecovered=self.rnd.randint(1, 9))
            elif kind == "gaveup":
                self.row(t=t, retriesGaveUp=10, errorKind="overloaded")
            elif kind == "effort":
                self.row(t=t, effortApplied=self.rnd.choice(("low", "high")))
            elif kind == "gesture":
                self.row(t=t, cmdGesture="/model")
            else:
                self.row(t=t, spendCut={"rate": "1,240", "ceiling": "1,000"})
        if self.rnd.random() < 0.5:
            self.tick(4, 9)

    def do_idle(self):
        self.kinds["idle"] += 1
        self.row(t=self.t + 1, state="idle")
        self.tick(60, 900)
        self.row(t=self.t, state="working")

    def do_stop(self):
        """A turn the person stopped: the CLI's stop record ends it, and the next prompt chains onto that record."""
        self.kinds["stop"] += 1
        self.turn(end=False)
        self.tip = self.emit(U(self.tick(1, 5), "[Request interrupted by user]", self.u("s"), self.tip, ps=None))

    def do_command(self):
        """A slash command between turns: its echo, its wrapper and its output."""
        self.kinds["command"] += 1
        tag = self.u("c")
        t = self.tick()
        rt = self.emit(U(t, "/model", "rt" + tag, self.tip, ps=None, isMeta=True, promptId="p" + tag))
        cw = self.emit(U(t, "<command-name>/model</command-name>\n<command-message>model</command-message>\n"
                            "<command-args></command-args>", "cw" + tag, rt, ps=None, promptId="p" + tag))
        self.tip = self.emit(U(t, "<local-command-stdout>Set model to the synthetic one</local-command-stdout>", "so" + tag, cw,
                               ps=None, promptId="p" + tag))

    def do_api_error(self):
        self.kinds["api_error"] += 1
        self.turn(errors=True)

    def do_eclipse(self):
        """API errors the CLI buffered and flushed late: written after the turn's reply, stamped inside the turn, chained
        off its opening prompt, with the stop hook and the next prompt hung on that spur."""
        self.kinds["eclipse"] += 1
        self.tick()
        u = self.emit(U(self.t, self.text(), self.u("u"), self.tip))
        self.said.append(self.recs[-1]["message"]["content"])
        a1 = self.u("a")
        t1 = self.tick(5, 30)
        self.emit(A(t1, a1, u, text="charting it now", tools=[("Bash", {"command": "true"})], stop=None, model=self.model))
        r1 = self.emit(R(self.tick(5, 10), "tu_%s_0" % a1, self.u("r"), a1))
        t2 = self.tick(5, 20)
        self.emit(A(t2, self.u("a"), r1, text="it rises until the cache saturates", model=self.model))
        e1 = self.emit(SYS(t1 + 1, "api_error", self.u("e"), u, level="error", error={"message": "429 rate_limit_error (synthetic)"}))
        e2 = self.emit(SYS(t1 + 3, "api_error", self.u("e"), e1, level="error", error={"message": "429 rate_limit_error (synthetic)"}))
        self.tip = self.emit(SYS(t2 + 1, "stop_hook_summary", self.u("h"), e2, hookCount=1, hookErrors=[], level="suggestion",
                                 preventedContinuation=False))
        self.t = t2 + 1

    def do_model(self):
        self.kinds["model"] += 1
        self.model = MODELS[1] if self.model == MODELS[0] else MODELS[0]
        self.turn()

    def do_postal(self):
        """A peer's message: its id in the text, its row in the postal log."""
        self.kinds["postal"] += 1
        mid = "17000%05d.111_%d.TESTHOST" % (self.i, self.i)
        body = "ASK: bump %s, request %d" % (self.rnd.choice(TOPICS), self.i)
        self.mail.append({"t": self.t + 1, "ev": "sent", "id": mid, "from": "api", "from_id": PEER, "to_id": self.sid, "body": body})
        self.turn(text=body + "\n<!-- romp-msg-id: %s -->" % mid, ps="sdk")

    def do_twice(self):
        """A record written twice, as a resume re-splice writes it: the same uuid, the same stamp."""
        prompts = [r for r in self.recs if r.get("type") == "user" and isinstance(r["message"]["content"], str)
                   and not r["message"]["content"].startswith(("<", "[", "/"))]
        if not prompts:
            return
        self.kinds["twice"] += 1
        self.emit(dict(self.rnd.choice(prompts[-3:])))

    def do_clock(self):
        """The machine's clock stepped back: the records after it are stamped before the ones ahead of them."""
        self.kinds["clock"] += 1
        self.t -= self.rnd.randint(20, 120)
        self.turn(gap=(1, 5))

    def do_noise(self):
        """Records with no uuid the parse walks past."""
        self.kinds["noise"] += 1
        self.emit({"type": "file-history-snapshot", "messageId": self.u("f"), "isSnapshotUpdate": False,
                   "snapshot": {"trackedFileBackups": {}, "timestamp": iso(self.t)}})
        self.emit({"type": "summary", "summary": "notes-api work", "leafUuid": self.tip})

    def do_repeat(self):
        """A prompt whose text the person sent before (a second "continue")."""
        self.kinds["repeat"] += 1
        self.turn(text=self.rnd.choice(self.said) if self.rnd.random() < 0.5 else "continue")


def _strip(x):
    return json.loads(json.dumps(x, default=lambda o: "<unserializable>"))


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.saved_state = jd.STATE
        jd._rebind_state(self.td / "state")
        for d in ("states", "goals", "sdk", "checkpoints", "timeline"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")                 # a state root of this test's own: no session host
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        self.proj = self.td / "proj"
        self.proj.mkdir()
        self.rows = []
        self.saved = (km._sessions, km._live_map, km._TAIL_FIRST_MAX_BYTES)
        km._sessions = lambda now, **kw: list(self.rows)
        km._live_map = lambda: {}
        self.fresh()
        with km._TAIL_FIRST_LOCK:
            km._TAIL_FIRST_QUEUE.clear()
            km._TAIL_FIRST_STATS.update(offered=0, dropped=0, attempts=0, refused={}, skipped={}, matched=0, mismatched=0,
                                        ms=0.0, msMax=0.0)
        km._TAIL_FIRST_SAID.clear()

    def tearDown(self):
        km._sessions, km._live_map, km._TAIL_FIRST_MAX_BYTES = self.saved
        em.set_checkpoint_dir(None)
        jd._rebind_state(self.saved_state)
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side (the caches a whole build fills)."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear()
            em._HYDRATED_BYTES[0] = 0
        em._LAZY_FILES.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        with km._chat_fold_lock:
            km._chat_fold.clear()
        jd._PARSE_CACHE.clear()
        km._parse_mode.clear()
        km._built_chat.clear()
        km._prev_chat_events.clear()
        km._RENDER_FLOOR.clear()
        with em._MAT_LOCK:
            em._MAT_LRU.clear()

    def install(self, sid, recs, rows=(), mail=(), fork=None, leaf=None):
        """Put one synthetic session on disk: its transcript, its side-store rows, its postal rows, its registry record."""
        leaf = str(leaf or (self.proj / (sid + ".jsonl")))
        Path(leaf).write_text("".join(json.dumps(r) + "\n" for r in recs))
        if rows:
            (jd.STATE / "states" / (sid + ".jsonl")).write_text(
                "".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["t"])))
        if mail:
            with open(jd.MESSAGES, "a") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in mail))
        if fork:
            (jd.STATE / "sdk" / (sid + ".json")).write_text(json.dumps({"forkedFrom": fork}))
        self.rows = [{"sid": sid, "name": "web", "path": leaf, "mtime": NOW, "anchor": sid}]
        return leaf

    def script(self, seed, **kw):
        sc = Script(seed, **kw)
        return sc, self.install(sc.sid, sc.recs, sc.rows, sc.mail, sc.fork)

    def whole(self, sid):
        """The whole build's events (a client's full page), and the tree it parsed."""
        m = km.build_session(sid, NOW, {}, floor=0)
        self.assertIsNotNone(m, "the whole build")
        return _strip(m["events"])

    def tree(self, sid, leaf):
        tree, key, cands = jd.parsed_session_peek(sid, [leaf], states=str(jd.STATE / "states" / (sid + ".jsonl")),
                                                  sdk_human=km._display_sdk_human(sid))
        self.assertIsNotNone(tree, "the whole build left its tree in the parse store")
        return tree, cands

    def differences(self, sid, leaf, win, whole):
        """What the provisional frame of `win` gets wrong against the whole build: [] when it is consistent (the definition in
        this file's docstring), else a list of what differs. A window a later gate refuses raises em.TailRefused; a
        whole-history read no piece caught raises em.WholeHistoryRead (a reader nobody classified: an error here)."""
        tree, cands = self.tree(sid, leaf)
        win.also(cands)
        prov = km._tail_first_frame(sid, NOW, {}, win)
        declared = set(prov["provisional"])
        out = []
        if not ALWAYS <= declared:
            out.append("the frame does not declare %s" % sorted(ALWAYS - declared))
        pe = _strip(prov["events"])
        i0 = next((i for i, e in enumerate(pe) if e.get("uuid") == win.uuid), None)
        j0 = next((i for i, e in enumerate(whole) if e.get("uuid") == win.uuid), None)
        if i0 is None or j0 is None:
            return out + ["the boundary's event is in %s" % ("neither build" if i0 is None and j0 is None else
                                                             "the whole build only" if i0 is None else "the provisional build only")]
        pw, ww = pe[i0:], whole[j0:]
        while ww and "liveTail" in declared and ww[-1].get("kind") in km._OVERLAY_KINDS and ww[-1].get("uuid") == ww[-1]["kind"]:
            ww = ww[:-1]                 # a live card the whole page ends on (named by its kind: no record's event)
        if len(pw) != len(ww):
            out.append("%d events in the provisional window, %d in the whole build's (%s)" % (
                len(pw), len(ww), sorted({e.get("uuid") for e in pw} ^ {e.get("uuid") for e in ww})[:6]))
        k0 = next((e["turn"] for e in ww if "turn" in e), None)
        for p, w in zip(pw, ww):
            names = set(p.get("pending") or ())
            if not names <= declared:
                out.append("%s: a placeholder the frame does not declare: %s" % (w.get("uuid"), sorted(names - declared)))
            if not names <= set(OWNED):
                out.append("%s: a placeholder this test does not know: %s" % (w.get("uuid"), sorted(names - set(OWNED))))
            own = {"pending", "turn", "tailTurn"}.union(*(OWNED.get(n, ()) for n in names))
            bad = sorted(f for f in (set(p) | set(w)) - own if (f in p) != (f in w) or p.get(f) != w.get(f))
            if "turn" in p:
                bad.append("turn (the provisional build states an absolute turn number)")
            if ("turn" in w) != ("tailTurn" in p) or ("turn" in w and w["turn"] - k0 != p["tailTurn"]):
                bad.append("tailTurn")
            if bad:
                out.append("%s: %s" % (w.get("uuid") or p.get("uuid"), bad))
        # the kernel's own comparison agrees, and its reference is the whole build's window
        turns = tree.get("turns") or []
        k = next((i for i in range(len(turns) - 1, -1, -1) if (turns[i].get("trigger") or {}).get("uuid") == win.uuid), None)
        if k is None:
            return out + ["the whole parse opens no turn at the boundary"]
        names = {n for n, v in prov.items() if n not in ("type", "id", "events", "wm", "provisional") and not isinstance(v, km._Pending)}
        ref = km._tail_first_reference(sid, NOW, {}, win, tree, k, names)
        re_ = _strip(ref["events"])
        r0 = next((i for i, e in enumerate(re_) if e.get("uuid") == win.uuid), None)
        if r0 is None or re_[r0:] != ww:
            out.append("the reference build is not the whole build's window")
        kd = km._tail_first_diff(prov, ref, win, k)
        if bool(kd) != bool(out):
            out.append("the kernel's comparison says %r" % (kd[:4],))
        for name, v in prov.items():
            if isinstance(v, km._Pending) and v["pending"] not in declared:
                out.append("piece %s is a placeholder the frame does not declare" % name)
        return out

    def windows(self, leaf):
        """Every answer em.tail_window gives for `leaf`, asked for each window size from one record to the whole file:
        ({start offset: window}, Counter of refusal reasons, one per size refused, the sizes answered with a window)."""
        n = sum(1 for ln in open(leaf) if '"type": "user"' in ln or '"type": "assistant"' in ln)
        wins, why, got = {}, collections.Counter(), 0
        for me in range(1, n + 2):
            win, reason = em.tail_window(leaf, me)
            if win is None:
                why[reason] += 1
            else:
                self.assertGreaterEqual(win.n, me)
                wins.setdefault(win.start, win)
                got += 1
        return wins, why, got


def naive_window(leaf, at):
    """The NAIVE tail the survey's lab measured: the file from the prompt at record index `at` to its end, with no boundary
    rule at all (the record before it is taken as the one written before it, whatever it is)."""
    recs, offs, off = [], [], 0
    with open(leaf, "rb") as fh:
        for ln in fh:
            recs.append(json.loads(ln))
            offs.append(off)
            off += len(ln)
    st = os.stat(leaf)
    prev = next((r for r in reversed(recs[:at]) if r.get("uuid")), None) or {}
    return em.TailWindow(leaf, offs[at], off, (st.st_mtime_ns, st.st_size, st.st_ino), recs[at:], recs[at]["uuid"],
                         em.parse_z(recs[at]["timestamp"]), prev.get("uuid"), em.parse_z(prev.get("timestamp")) or 0,
                         (prev.get("message") or {}).get("model") if isinstance(prev.get("message"), dict) else None,
                         sum(1 for r in recs[at:] if r.get("type") in ("user", "assistant")))


class TestGeneratedCorpus(Harness):
    def test_every_window_is_refused_or_equal_to_the_whole_build(self):
        """The property. Every window em.tail_window accepts, for every size asked of every generated transcript, builds a
        provisional frame equal to the whole build's window with only declared pieces pending, or is refused by a later
        gate with a reason; and the corpus is not vacuous: windows are accepted after every shape the script writes, and
        with the shapes a window may hold inside them."""
        accepted = refused_later = asked = answered = 0
        why_window, why_gate, pend = collections.Counter(), collections.Counter(), collections.Counter()
        kinds, behind, inside = collections.Counter(), collections.Counter(), collections.Counter()
        for seed in SEEDS:
            sc, leaf = self.script(seed)
            kinds.update(sc.kinds)
            whole = self.whole(sc.sid)
            wins, why, got = self.windows(leaf)
            asked += sum(why.values()) + got
            answered += got
            why_window.update(why)
            for start, win in sorted(wins.items()):
                try:
                    diffs = self.differences(sc.sid, leaf, win, whole)
                except em.TailRefused as e:
                    refused_later += 1
                    why_gate[e.reason] += 1
                    continue
                self.assertEqual(diffs, [], "seed %d, the window at %s (byte %d)" % (seed, win.uuid, start))
                accepted += 1
                prov = km._tail_first_frame(sc.sid, NOW, {}, win)
                pend.update(n for e in prov["events"] for n in (e.get("pending") or ()))
                # what stands BEFORE this window (the history a tail never reads), and what it holds INSIDE
                tail = win.records
                held = {r.get("uuid") for r in tail}
                before = [r for r in sc.recs if r.get("uuid") and r["uuid"] not in held]
                text = lambda r: r["message"]["content"] if isinstance((r.get("message") or {}).get("content"), str) else ""
                for what, yes in (
                        ("a compaction", any(r.get("subtype") == "compact_boundary" for r in before)),
                        ("a clear", any(r.get("parentUuid") is None and r.get("type") == "user" for r in before[1:])),
                        ("a rewind's abandoned turns", any(r["uuid"] in sc.abandoned for r in before)),
                        ("a background launch", any((r.get("toolUseResult") or {}).get("isAsync") for r in before)),
                        ("the fork's cut", bool(sc.fork) and sc.fork["cut"] not in held),
                        ("bookkeeping between the reply and the boundary", before[-1]["uuid"] != win.prev_uuid)):
                    behind[what] += bool(yes)
                for what, yes in (
                        ("a spliced message", any((r.get("attachment") or {}).get("type") == "queued_command" for r in tail)),
                        ("a queued prompt", any(r.get("promptSource") == "queued" for r in tail)),
                        ("an agent's return", any(text(r).startswith("<task-notification>") for r in tail)),
                        ("a background launch", any((r.get("toolUseResult") or {}).get("isAsync") for r in tail)),
                        ("an API error", any(r.get("subtype") == "api_error" for r in tail)),
                        ("a stop", any(text(r).startswith("[Request interrupted") for r in tail)),
                        ("a slash command", any(text(r).startswith("<command-name>") for r in tail)),
                        ("a model switch", len({r["message"].get("model") for r in tail if r.get("type") == "assistant"}) > 1),
                        ("a side-store note", any(r["t"] >= win.t and not ({"orphanReply", "state"} & set(r)) for r in sc.rows)),
                        ("a salvaged-reply marker", any(r["t"] >= win.t and "orphanReply" in r for r in sc.rows)),
                        ("an idle gap", any(r["t"] >= win.t and "state" in r for r in sc.rows)),
                        ("a peer's message", any(r["t"] >= win.t for r in sc.mail)),
                        ("a turn still running", next(r for r in reversed(tail) if r.get("type") in ("user", "assistant"))
                         ["message"].get("stop_reason") != "end_turn"),
                        ("the fork's cut", bool(sc.fork) and sc.fork["cut"] in held)):
                    inside[what] += bool(yes)
        refused = sum(why_window.values())
        rate = lambda c, n: {k: "%d (%.1f%%)" % (v, 100.0 * v / n) for k, v in sorted(c.items(), key=lambda kv: -kv[1])}
        print("\ntail-first corpus: %d generated transcripts, %d window sizes asked for: %d answered with a window (%.1f%%; "
              "%d distinct windows), %d refused by em.tail_window: %r\n  of the %d distinct windows, %d built and equal to the "
              "whole build, %d refused by a later gate: %r\n  placeholders on events: %r\n  shapes written: %r\n  accepted "
              "windows with, in the history BEFORE them: %r\n  accepted windows holding: %r"
              % (len(SEEDS), asked, answered, 100.0 * answered / asked, accepted + refused_later, refused, rate(why_window, asked),
                 accepted + refused_later, accepted, refused_later, dict(why_gate), dict(pend), dict(kinds), dict(behind),
                 dict(inside)))
        self.assertGreater(accepted, 4 * len(SEEDS), "the corpus accepts windows: several per transcript")
        for shape in ("rewind", "clear", "compact", "compact_replay", "compact_manual", "compact_broken", "splice", "queue_next",
                      "orphan", "agent", "agent_back", "notes", "idle", "stop", "command", "api_error", "eclipse", "postal",
                      "twice", "clock", "bookkeeping", "bookkeeping beside", "bookkeeping late"):
            self.assertGreater(kinds[shape], 0, "the corpus writes %s" % shape)
        for what in ("a compaction", "a clear", "a rewind's abandoned turns", "a background launch", "the fork's cut",
                     "bookkeeping between the reply and the boundary"):
            self.assertGreater(behind[what], 0, "windows are accepted with %s before them" % what)
        for what in ("a spliced message", "a queued prompt", "an agent's return", "a background launch", "an API error",
                     "a slash command", "a model switch", "a side-store note", "a salvaged-reply marker", "an idle gap",
                     "a peer's message", "a turn still running", "the fork's cut"):
            self.assertGreater(inside[what], 0, "windows are accepted holding %s" % what)
        self.assertGreater(pend["agentLaunches"] + pend["taskOutputs"], 0,
                           "a background agent launched before a window shows as a placeholder in it")

    def test_the_shadow_job_never_reports_a_difference_on_the_corpus(self):
        """The kernel's own job over the corpus, at the sizes a page would ask for: matched or refused, never mismatched, and
        nothing in the error center; the refusal rate by reason is printed and bounded."""
        said = []
        saved, km._sdk_problem = km._sdk_problem, lambda text: said.append(str(text))
        out, sizes = collections.Counter(), (1, 6, 12, 24, 40)
        try:
            for seed in SEEDS:
                sc, leaf = self.script(seed)
                self.whole(sc.sid)
                for me in sizes:
                    outcome, detail = km._tail_first_shadow_job(sc.sid, now=NOW, live_map={}, min_events=me)
                    self.assertNotEqual(outcome, "mismatched", "seed %d, %d records: %r" % (seed, me, detail))
                    out[outcome if outcome == "matched" else "%s:%s" % (outcome, detail)] += 1
        finally:
            km._sdk_problem = saved
        rep = km._tail_first_report()
        print("\ntail-first shadow over the corpus (%d transcripts x sizes %r): %r; the /perf block: %r"
              % (len(SEEDS), sizes, dict(out), rep))
        self.assertEqual(said, [], "nothing reaches the error center")
        self.assertEqual(rep["mismatched"], 0)
        self.assertEqual(rep["attempts"], len(SEEDS) * len(sizes))
        self.assertEqual(rep["matched"] + sum(rep["refused"].values()) + sum(rep["skipped"].values()), rep["attempts"])
        self.assertNotIn("unclassified-read", rep["refused"], "every whole-history read of the build is a declared piece")
        self.assertGreater(rep["matched"], rep["attempts"] // 3, "the boundary rule accepts a real share of what it is asked")

    def test_a_naive_tail_differs_on_the_same_corpus(self):
        """The hazard is real in this corpus: cut at a prompt with no boundary rule, a share of the tails differ from the
        whole build (or cannot be built). The same check passes for every window tail_window accepts (the test above)."""
        differ = same = unbuildable = 0
        for seed in list(SEEDS)[:10]:
            sc, leaf = self.script(seed)
            whole = self.whole(sc.sid)
            for at, r in enumerate(sc.recs):
                if at == 0 or not em._tail_prompt_shaped(r):
                    continue
                try:
                    diffs = self.differences(sc.sid, leaf, naive_window(leaf, at), whole)
                except (em.TailRefused, em.WholeHistoryRead):
                    unbuildable += 1
                    continue
                if diffs:
                    differ += 1
                else:
                    same += 1
        print("\nnaive tails over 10 transcripts: %d differ from the whole build, %d equal it, %d refused by a later gate"
              % (differ, same, unbuildable))
        self.assertGreater(differ, 0, "a tail cut at any prompt is not the whole build's window")
        self.assertGreater(same, 0)


class TestRewoundBranch(Harness):
    """The case the survey's lab found: of five windows cut at a prompt, one kept two abandoned turns of a rewound branch."""
    SID = "aaaaaaaa-7001-4222-8333-666666666666"
    KEPT = ["second attempt instead", "now write its test", "and document it"]

    def records(self):
        m = MODELS[0]
        return [U(T0, "first attempt at the retry cap", "u1", None), A(T0 + 30, "a1", "u1", text="did it one way", model=m),
                # the person rewound to a1 after these three turns: abandoned, and still in the file (records 2 to 7)
                U(T0 + 100, "abandoned follow-up one", "x1", "a1"), A(T0 + 130, "y1", "x1", text="down a dead end", model=m),
                U(T0 + 160, "abandoned follow-up two", "x2", "y1"), A(T0 + 190, "y2", "x2", text="further down it", model=m),
                U(T0 + 220, "abandoned follow-up three", "x3", "y2"), A(T0 + 250, "y3", "x3", text="and further", model=m),
                # the surviving line chains onto a1 (record 8 on)
                U(T0 + 300, "second attempt instead", "u4", "a1"), A(T0 + 330, "a4", "u4", text="a better approach", model=m),
                U(T0 + 400, "now write its test", "u5", "a4"), A(T0 + 430, "a5", "u5", text="test written", model=m),
                U(T0 + 500, "and document it", "u6", "a5"), A(T0 + 530, "a6", "u6", text="documented", model=m)]

    def test_the_rewound_branch_that_broke_the_naive_tail(self):
        """A tail cut at a prompt INSIDE the abandoned branch: the branch's own chain ends at a record the tail does not hold,
        so the parse reads it as a broken chain and keeps it on principle, and the page shows turns the person abandoned.
        The consistency check fails for that tail, and em.tail_window never offers it."""
        leaf = self.install(self.SID, self.records())
        whole = self.whole(self.SID)
        self.assertEqual([e.get("md") for e in whole if e.get("kind") == "user"], ["first attempt at the retry cap"] + self.KEPT,
                         "the whole build drops the rewound branch")
        # THE NAIVE TAIL, cut at the branch's second prompt (record 4) and at its third (record 6)
        for at, ghosts in ((4, ["abandoned follow-up two", "abandoned follow-up three"]), (6, ["abandoned follow-up three"])):
            win = naive_window(leaf, at)
            prov = km._tail_first_frame(self.SID, NOW, {}, win)
            self.assertEqual([e.get("md") for e in prov["events"] if e.get("kind") == "user"], ghosts + self.KEPT,
                             "the naive tail shows turns the person abandoned")
            diffs = self.differences(self.SID, leaf, win, whole)
            self.assertTrue(diffs, "...and the consistency check FAILS for it")
            self.assertIn("the boundary's event is in the provisional build only", diffs)
        # cut at the branch's FIRST prompt (record 2), the tail holds both children of a1 and drops the losing one like the
        # whole parse does: no ghost, and no turn at the boundary either, which the parse's own gate refuses
        with self.assertRaises(em.TailRefused) as cm:
            km._tail_first_frame(self.SID, NOW, {}, naive_window(leaf, 2))
        self.assertEqual(cm.exception.reason, "boundary-not-opener")
        # THE REAL BOUNDARY, the same check: every size asked is refused or equal, and no window starts inside the branch
        wins, why, _ = self.windows(leaf)
        self.assertEqual(sorted(w.uuid for w in wins.values()), ["u5", "u6"], "the windows that start after the rewind's last trace")
        for win in wins.values():
            self.assertEqual(self.differences(self.SID, leaf, win, whole), [])
        self.assertEqual(em.tail_window(leaf, 5), (None, "parent-not-predecessor"))    # at u4: it chains onto a1, not the record before it
        for me in (7, 9, 11):                                                          # at x3, x2, x1: inside the abandoned branch,
            self.assertEqual(em.tail_window(leaf, me), (None, "chain"))                #  the surviving line does not chain onto them
        self.assertEqual(em.tail_window(leaf, 13), (None, "whole-file"))
        self.assertEqual(set(why), {"parent-not-predecessor", "chain", "whole-file", "too-short"})

    def test_the_shadow_job_matches_on_the_rewound_transcript_and_its_comparison_names_a_naive_window(self):
        """The kernel's own job on this transcript: matched at the sizes it accepts, refused at the rest, never a difference.
        And the comparison itself, handed the naive window: a difference at the boundary, named without a word of the text."""
        leaf = self.install(self.SID, self.records())
        self.whole(self.SID)
        got = [km._tail_first_shadow_job(self.SID, now=NOW, live_map={}, min_events=me) for me in (2, 4, 6, 8)]
        self.assertEqual(got, [("matched", None), ("matched", None), ("refused", "parent-not-predecessor"), ("refused", "chain")])
        tree, _ = self.tree(self.SID, leaf)
        win = naive_window(leaf, 4)
        self.assertIsNone(next((i for i, t in enumerate(tree["turns"]) if (t.get("trigger") or {}).get("uuid") == win.uuid), None),
                          "the whole parse opens no turn at the abandoned prompt")
        prov = km._tail_first_frame(self.SID, NOW, {}, win)
        ref = km._tail_first_reference(self.SID, NOW, {}, win, tree, 1, set())
        self.assertEqual(km._tail_first_diff(prov, ref, win, 1), [("window", ["boundary"])])


class TestEachRule(Harness):
    """Every refusal, one at a time, on a hand-built transcript of three turns (u1 a1, u2 a2, u3 a3) with one anomaly at the
    SECOND prompt's boundary. Asked for three records, the nearest boundary that could hold them is u2, so the reason
    em.tail_window returns is that boundary's (u1 answers whole-file, which ends the search). Beside each refusal, what a
    tail cut at u2 with no rule at all gives, in three kinds, and the kind is part of what is pinned:
      DIFFERS   the naive tail's page is not the whole build's: the rule closes a hole this test shows open without it
      GATE      the naive tail is refused further on (the parse's own check that its first turn opens at the boundary)
      PRINCIPLE no difference is known: the records in hand cannot PROVE the window equal, and the rule refuses on that
                alone. These are named so nobody reads them as load-bearing by evidence; they cost windows, not correctness."""
    BOOK = dict(hookCount=1, hookErrors=[], level="suggestion", preventedContinuation=False)
    CUT = dict(rate="1,240", ceiling="1,000")

    def three(self):
        m = MODELS[0]
        return [U(T0, "tighten the tag filter", "u1", None), A(T0 + 30, "a1", "u1", text="tightened", model=m),
                U(T0 + 100, "now write a test for it", "u2", "a1"), A(T0 + 130, "a2", "u2", text="test written", model=m),
                U(T0 + 200, "and document it", "u3", "a2"), A(T0 + 230, "a3", "u3", text="documented", model=m)]

    def between(self, *recs):
        """The three turns with `recs` written between the first reply and the second prompt, the prompt chained onto the last."""
        r = self.three()
        r[2]["parentUuid"] = recs[-1]["uuid"]
        return r[:2] + list(recs) + r[2:]

    def compacted(self, replay):
        """Two turns, an attached compaction, and with `replay` its restored tail: the FIRST prompt written again under a new
        uuid, answered, and a third turn."""
        m = MODELS[0]
        r = self.three()[:4]
        r += [SYS(T0 + 160, "compact_boundary", "cb1", None, logicalParentUuid="a2", isMeta=False,
                  compactMetadata={"trigger": "auto", "preTokens": 180000, "postTokens": 6000}),
              U(T0 + 161, "summary of the conversation so far", "cs1", "cb1", ps=None, isCompactSummary=True,
                isVisibleInTranscriptOnly=True)]
        tip = "cs1"
        if replay:
            r += [U(T0 + 162, "tighten the tag filter", "ur", "cs1", ps=None),
                  A(T0 + 190, "ar", "ur", text="picking the work back up", model=m)]
            tip = "ar"
        return r + [U(T0 + 200, "and document it", "u3", tip), A(T0 + 230, "a3", "u3", text="documented", model=m)]

    def cases(self):
        """name -> (records, the size asked, the refusal, the naive tail at u2: ("differs", part of what differs), ("gate",
        the later refusal) or None for a refusal on principle)."""
        m, out = MODELS[0], {}
        opener = "the whole parse opens no turn at the boundary"
        # ── a turn's start ──
        out["a message queued ahead of the prompt"] = (
            self.between(ATT(T0 + 40, "also check the digest file", "q1", "a1")), 3, "predecessor-not-assistant", ("differs", opener))
        out["a stop record ahead of the prompt"] = (
            self.between(U(T0 + 60, "[Request interrupted by user]", "s1", "a1", ps=None)), 3, "predecessor-not-assistant", None)
        out["an API error's notice ahead of the prompt"] = (
            self.between(SYS(T0 + 40, "api_error", "e1", "a1", level="error", error={"message": "429 rate_limit_error (synthetic)"})),
            3, "predecessor-not-assistant", None)
        r = self.three()
        r[1] = A(T0 + 30, "a1", "u1", text="working on it", tools=[("Bash", {"command": "true"})], stop="tool_use", model=m)
        out["a reply that had not ended its turn"] = (r, 3, "turn-open", ("differs", opener))
        r = self.three()
        r[1] = A(T0 + 30, "a1", "u1", text="tightened", model=None)
        out["a reply that names no model"] = (r, 3, "predecessor-model", None)
        r = self.three()
        r[2:2] = [U(T0 + 40, "an abandoned follow-up", "x1", "a1"), A(T0 + 70, "y1", "x1", text="down a dead end", model=m)]
        out["a prompt chained past the record before it"] = (r, 3, "parent-not-predecessor", None)
        out["bookkeeping beside its neighbour, not after it"] = (
            self.between(SYS(T0 + 30, "stop_hook_summary", "h1", "a1", **self.BOOK),
                         SYS(T0 + 31, "turn_duration", "d1", "a1", durationMs=4200, isMeta=False)), 3, "parent-not-predecessor", None)
        # ── chronological order ──
        r = self.three()
        r[0] = U(T0 + 100, "now write a test for it", "u1", None)
        r[1] = A(T0 + 100, "a1", "u1", text="tightened", model=m)
        out["the prompt, its text and its second all an earlier prompt's"] = (
            r, 3, "clock", ("differs", "the boundary's event is in the provisional build only"))
        r = self.three()
        r[0] = U(T0 + 150, "tighten the tag filter", "u1", None)
        out["an earlier record stamped after the reply"] = (r, 3, "clock", ("differs", "4 events in the provisional window, 5"))
        out["bookkeeping stamped after the prompt"] = (
            self.between(SYS(T0 + 30, "stop_hook_summary", "h1", "a1", **self.BOOK),
                         SYS(T0 + 150, "away_summary", "w1", "h1", content="a synthetic recap", isMeta=False)), 3, "clock", None)
        r = self.three()
        r[3] = A(T0 + 90, "a2", "u2", text="test written", model=m)
        out["a tail record stamped before the prompt"] = (r, 3, "stamp-floor", ("gate", "boundary-not-opener"))
        # ── the kept set ──
        r = self.three()
        r.insert(4, dict(r[3]))
        out["a tail record written twice"] = (r, 3, "uuid-repeat", None)
        r = self.three()
        r.insert(4, dict(r[1]))
        out["an earlier record written again in the tail"] = (r, 3, "uuid-repeat", ("gate", "boundary-not-opener"))
        # ── the replay burst ──
        out["a compaction in the tail"] = (self.compacted(False), 3, "compaction-in-window", None)
        out["a compaction in the tail that replays a prompt from before it"] = (
            self.compacted(True), 5, "compaction-in-window", ("differs", "7 events in the provisional window, 6"))
        r = self.three()
        r[4:4] = [U(T0 + 150, "/compact", "rt1", "a2", ps=None, isMeta=True, promptId="p1"),
                  U(T0 + 150, "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n"
                              "<command-args></command-args>", "cw1", "rt1", ps=None, promptId="p1"),
                  U(T0 + 170, "<local-command-stdout>Compacted (ctrl+o to see full summary)</local-command-stdout>", "so1", "cw1",
                    ps=None, promptId="p1")]
        r[7]["parentUuid"] = "so1"
        out["a /compact command in the tail, its boundary written before it"] = (r, 3, "compaction-in-window", None)
        # ── carried links ──
        r = self.three()
        r[1] = A(T0 + 30, "a1", "u1", text="started it", tools=[("Bash", {"command": "true"})], model=m)
        r[3:4] = [R(T0 + 120, "tu_a1_0", "r1", "u2", content="exit 0"), A(T0 + 130, "a2", "r1", text="test written", model=m)]
        out["a tool result for a call before the tail"] = (r, 3, "tool-result-before-window", None)
        r = self.three()
        r[1] = A(T0 + 30, "a1", "u1", text="started it", tools=[("Skill", {"skill": "notes"})], model=m)
        r.insert(4, U(T0 + 140, "the skill's payload text", "k1", "a2", ps=None, isMeta=True, sourceToolUseID="tu_a1_0"))
        r[5]["parentUuid"] = "k1"
        out["a skill's payload for a call before the tail"] = (r, 3, "carry", ("differs", "tailTurn"))
        r = self.between(U(T0 + 40, "<command-name>/review</command-name>\n<command-message>review</command-message>\n"
                                    "<command-args>the diff</command-args>", "cw1", "a1", ps=None, promptId="p1"),
                         A(T0 + 60, "ac", "cw1", text="reviewed", model=m))
        r.insert(6, U(T0 + 140, "/review the diff", "rt1", "a2", ps=None, promptId="p1"))
        r[7]["parentUuid"] = "rt1"
        out["a command's echo whose wrapper is before the tail"] = (
            r, 3, "command-twin", ("differs", "5 events in the provisional window, 4"))
        return out

    def test_each_refusal_and_what_a_tail_without_the_rule_shows(self):
        kinds = collections.Counter()
        for n, (name, (recs, me, reason, naive)) in enumerate(sorted(self.cases().items())):
            with self.subTest(name):
                sid = "aaaaaaaa-71%02d-4222-8333-666666666666" % n
                self.fresh()
                leaf = self.install(sid, recs)
                whole = self.whole(sid)
                self.assertEqual(em.tail_window(leaf, me), (None, reason))
                self.assertEqual(km._tail_first_shadow_job(sid, now=NOW, live_map={}, min_events=me), ("refused", reason))
                at = next(k for k, r in enumerate(recs) if r.get("uuid") == "u2")
                try:
                    got = ("differs", self.differences(sid, leaf, naive_window(leaf, at), whole))
                except em.TailRefused as e:
                    got = ("gate", e.reason)
                if naive is None:
                    kinds["principle"] += 1
                    continue
                kinds[naive[0]] += 1
                self.assertEqual(got[0], naive[0])
                if naive[0] == "gate":
                    self.assertEqual(got[1], naive[1])
                else:
                    self.assertTrue(any(naive[1] in d for d in got[1]), "the naive tail differs as recorded: %r" % (got[1],))
        self.assertEqual(dict(kinds), {"differs": 7, "gate": 2, "principle": 10})

    def test_the_same_three_turns_with_no_anomaly_are_a_window(self):
        sid = "aaaaaaaa-7199-4222-8333-666666666666"
        leaf = self.install(sid, self.three())
        whole = self.whole(sid)
        win, why = em.tail_window(leaf, 3)
        self.assertEqual((win.uuid, win.prev_uuid, win.prev_t, win.prev_model, win.n, why), ("u2", "a1", T0 + 30, MODELS[0], 4, None))
        self.assertEqual(self.differences(sid, leaf, win, whole), [])
        self.assertEqual(em.tail_window(leaf, 5), (None, "whole-file"))
        self.assertEqual(em.tail_window(leaf, 7), (None, "too-short"))

    def test_turn_end_bookkeeping_between_the_reply_and_the_prompt_is_passed_over(self):
        """What the CLI writes after nearly every reply (a stop hook's failure note, the hook summary, the turn's duration, a
        recap): each chained onto the one before and the prompt onto the last. The window carries the REPLY's time and
        model across the boundary, not a bookkeeping record's, and its page is the whole build's."""
        sid = "aaaaaaaa-7198-4222-8333-666666666666"
        recs = self.between({"type": "attachment", "timestamp": iso(T0 + 30), "uuid": "m1", "parentUuid": "a1", "isSidechain": False,
                             "attachment": {"type": "hook_non_blocking_error", "hookName": "Stop", "stderr": "synthetic hook failure"}},
                            SYS(T0 + 30, "stop_hook_summary", "h1", "m1", **self.BOOK),
                            SYS(T0 + 31, "turn_duration", "d1", "h1", durationMs=4200, isMeta=False),
                            SYS(T0 + 95, "away_summary", "w1", "d1", content="a synthetic recap", isMeta=False))
        leaf = self.install(sid, recs)
        whole = self.whole(sid)
        win, why = em.tail_window(leaf, 3)
        self.assertEqual((win.uuid, win.prev_uuid, win.prev_t, win.prev_model, why), ("u2", "a1", T0 + 30, MODELS[0], None))
        self.assertEqual(self.differences(sid, leaf, win, whole), [])
        self.assertEqual(km._tail_first_shadow_job(sid, now=NOW, live_map={}, min_events=3), ("matched", None))

    def test_a_side_store_row_at_the_boundary_is_the_later_gates(self):
        """What no transcript record shows: a salvaged reply or a spend cut stamped between the reply and the prompt belongs
        to the turn before the window. em.tail_window accepts (it reads the transcript alone) and the build's gate refuses;
        a cut stamped inside the window's first turn is the window's own."""
        for n, (row, want) in enumerate((({"t": T0 + 60, "orphanReply": {"uuid": "o1", "text": "a reply the transcript never kept"}},
                                         ("refused", "orphan-marker")),
                                        ({"t": T0 + 60, "spendCut": self.CUT}, ("refused", "spend-cut-at-boundary")),
                                        ({"t": T0 + 30, "spendCut": self.CUT}, ("refused", "spend-cut-at-boundary")),
                                        ({"t": T0 + 110, "spendCut": self.CUT}, ("matched", None)))):
            with self.subTest(row=sorted(row), t=row["t"] - T0):
                sid = "aaaaaaaa-72%02d-4222-8333-666666666666" % n
                self.fresh()
                leaf = self.install(sid, self.three(), rows=[row])
                self.whole(sid)
                self.assertEqual(em.tail_window(leaf, 3)[0].uuid, "u2")
                self.assertEqual(km._tail_first_shadow_job(sid, now=NOW, live_map={}, min_events=3), want)

    def test_a_live_card_the_whole_page_ends_on_is_no_difference(self):
        """A launch error is a card the whole page appends after the transcript's events, named by its kind: the liveTail
        piece every provisional frame declares pending. The comparison is over the transcript's events."""
        sid = "aaaaaaaa-7197-4222-8333-666666666666"
        leaf = self.install(sid, self.three())
        saved, km._launch_error = km._launch_error, lambda s: {"text": "the Agent SDK is not installed (synthetic)", "dep": True}
        try:
            whole = self.whole(sid)
            self.assertEqual((whole[-1].get("kind"), whole[-1].get("uuid")), ("apiError", "apiError"), "the whole page ends on the card")
            win, _ = em.tail_window(leaf, 3)
            prov = km._tail_first_frame(sid, NOW, {}, win)
            self.assertIn("liveTail", prov["provisional"])
            self.assertNotIn("apiError", [e.get("kind") for e in prov["events"]])
            self.assertEqual(self.differences(sid, leaf, win, whole), [])
            self.assertEqual(km._tail_first_shadow_job(sid, now=NOW, live_map={}, min_events=3), ("matched", None))
        finally:
            km._launch_error = saved


class TestWhatOnlyTheShadowSees(Harness):
    SID = "aaaaaaaa-7002-4222-8333-666666666666"

    def records(self):
        """Eight turns, the SECOND stamped two hours ahead of the rest (the clock stepped back after it): chronologically it
        sorts after everything, and it lies far behind the last turns in the file."""
        m, recs, prev = MODELS[0], [], None
        for i in range(8):
            t = T0 + i * 100 + (7200 if i == 1 else 0)
            recs.append(U(t, "step %d of the export job" % i, "u%d" % i, prev))
            recs.append(A(t + 30, "a%d" % i, "u%d" % i, text="step %d done" % i, model=m))
            prev = "a%d" % i
        return recs

    def test_a_clock_step_inside_the_bytes_read_is_refused(self):
        leaf = self.install(self.SID, self.records())
        self.whole(self.SID)
        for me in range(1, 17):
            win, why = em.tail_window(leaf, me)
            self.assertIsNone(win, "no window: a record before every boundary is stamped after it")
        self.assertEqual(em.tail_window(leaf, 2), (None, "clock"))

    def test_a_clock_step_beyond_the_bytes_read_is_reported_by_the_shadow(self):
        """The limit the first three mechanisms have, and the fourth one's reason: a search that read only the file's last
        bytes cannot see the earlier record stamped in the future. The window is accepted, the two builds differ, and the
        shadow says so: counted, and filed once in the error center with ids and field names, never a word of the text."""
        recs = self.records()
        leaf = self.install(self.SID, recs)
        whole = self.whole(self.SID)
        km._TAIL_FIRST_MAX_BYTES = sum(len(json.dumps(r)) + 1 for r in recs[-5:])   # the search reads the last five records only
        said = []
        saved, km._sdk_problem = km._sdk_problem, lambda text: said.append(str(text))
        try:
            first = km._tail_first_shadow_job(self.SID, now=NOW, live_map={}, min_events=2)
            again = km._tail_first_shadow_job(self.SID, now=NOW, live_map={}, min_events=2)
        finally:
            km._sdk_problem = saved
        self.assertEqual(first, ("mismatched", [("window", ["count"])]),
                         "the whole build sorts the turn stamped ahead after the window's turns; the tail never read it")
        self.assertEqual(again[0], "mismatched")
        self.assertEqual(len(said), 1, "reported once, counted every time")
        rep = km._tail_first_report()
        self.assertEqual((rep["attempts"], rep["mismatched"], rep["matched"]), (2, 2, 0))
        self.assertIn(self.SID[:8], said[0])
        for r in recs:                                                 # ids and field names only
            body = r["message"]["content"] if isinstance(r["message"]["content"], str) else r["message"]["content"][0].get("text", "")
            self.assertNotIn(body, said[0])
        self.assertNotIn(str(self.td), said[0], "no path either")
        self.assertTrue(whole)


class TestSecondFile(Harness):
    """A conversation resumed into a second file: the leaf's first record chains into the session's first file."""
    SID = "aaaaaaaa-7003-4222-8333-666666666666"
    LAST = "bbbbbbbb-7003-4222-8333-666666666666"

    def lay(self):
        m = MODELS[0]
        first = [U(T0, "start the notes export", "u1", None), A(T0 + 30, "a1", "u1", text="export started", model=m)]
        leaf, prev = [], "a1"
        for i in range(2, 7):
            leaf.append(U(T0 + i * 100, "export step %d" % i, "u%d" % i, prev))
            leaf.append(A(T0 + i * 100 + 30, "a%d" % i, "u%d" % i, text="step %d done" % i, model=m))
            prev = "a%d" % i
        anchor = self.proj / (self.SID + ".jsonl")
        anchor.write_text("".join(json.dumps(r) + "\n" for r in first))
        os.utime(anchor, (T0 + 60, T0 + 60))                           # last written before the leaf began
        return anchor, self.install(self.SID, leaf, leaf=self.proj / (self.LAST + ".jsonl"))

    def test_windows_in_the_leaf_equal_the_whole_build_over_both_files(self):
        anchor, leaf = self.lay()
        whole = self.whole(self.SID)
        self.assertEqual([e["md"] for e in whole if e.get("kind") == "user"][:2], ["start the notes export", "export step 2"],
                         "the whole build reads both files")
        wins, why, _ = self.windows(leaf)
        self.assertEqual(sorted(w.uuid for w in wins.values()), ["u3", "u4", "u5", "u6"])
        for win in wins.values():
            self.assertEqual(self.differences(self.SID, leaf, win, whole), [])
        self.assertEqual(km._tail_first_shadow_job(self.SID, now=NOW, live_map={}, min_events=2), ("matched", None))

    def test_a_first_file_written_inside_the_window_refuses(self):
        anchor, leaf = self.lay()
        self.whole(self.SID)
        os.utime(anchor, (T0 + 650, T0 + 650))                         # the other file moved after the boundary's time
        self.whole(self.SID)
        self.assertEqual(km._tail_first_shadow_job(self.SID, now=NOW, live_map={}, min_events=2), ("refused", "sibling-file-active"))


if __name__ == "__main__":
    unittest.main()
