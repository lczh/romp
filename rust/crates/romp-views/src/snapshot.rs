//! `snapshot.json` (schema `romp-spike-snapshot/1`, tools/rust-spike/dump_world.py): the non-transcript inputs of the
//! per-card build. The alive rows in order, the live map, the board facts, per session its names, flags, store,
//! captions and recorded helper answers, the peers' stores, and the frame keys the build passes through.
//!
//! Stores are typed down to their nodes, log rows, seams, handoffs and origins, in file order (children are built in
//! store order). The recorded helper answers stay JSON except where their shape is fixed: an absent key means Python
//! never called that helper, and a port that reaches it must fail loudly rather than guess.

use crate::record::{Num, OMap};

/// The schema this loader reads.
pub const SCHEMA: &str = "romp-spike-snapshot/1";

record! {
    pub struct Snapshot {
        schema: String = "schema",
        world: World = "world",
        alive: Vec<AliveRow> = "alive",
        live_map: OMap<LiveRow> = "liveMap",
        board: Board = "board",
        sessions: OMap<SessionFacts> = "sessions",
        peers: OMap<PeerFacts> = "peers",
        frame_keys: Vec<String> = "frameKeys",
        frame: OMap<serde_json::Value> = "frame",
    }
    required: [schema, world, alive, live_map, board, sessions, peers, frame_keys, frame]
}

record! {
    pub struct World {
        name: String = "name",
        now: Num = "now",
        mint_clock: Num = "mintClock",
        colormap: Option<String> = "colormap",
        host_name: String = "hostName",
        repo_head: String = "repoHead",
        python: String = "python",
        sizes: Sizes = "sizes",
    }
    required: [now]
}

record! {
    pub struct Sizes {
        sessions: Num = "sessions",
        fillers: Num = "fillers",
        records: Num = "records",
        chat_turns: Num = "chatTurns",
        chat_records: Num = "chatRecords",
    }
}

record! {
    /// One alive session, newest transcript first (the asks order).
    pub struct AliveRow {
        sid: String = "sid",
        name: Option<String> = "name",
        anchor: Option<String> = "anchor",
        /// A float in both worlds (`1781099940.0`): the int/float tag matters.
        mtime: Num = "mtime",
        tree: Option<String> = "tree",
        transcript: String = "transcript",
    }
    required: [sid]
}

record! {
    pub struct LiveRow {
        state: Option<String> = "state",
        since: Option<Num> = "since",
        model: Option<String> = "model",
        effort: Option<String> = "effort",
        context: serde_json::Value = "context",
        compact_pct: Option<Num> = "compactPct",
        color: Option<String> = "color",
    }
}

record! {
    pub struct Board {
        cleared: Vec<String> = "cleared",
        jactive: Vec<String> = "jactive",
        jauth: OMap<serde_json::Value> = "jauth",
        wmap: OMap<serde_json::Value> = "wmap",
        stalls: OMap<serde_json::Value> = "stalls",
        nudge_records: OMap<NudgeRecord> = "nudgeRecords",
        nudge_times: OMap<Vec<Num>> = "nudgeTimes",
        debug: bool = "debug",
        debug_rows: serde_json::Value = "debugRows",
        notify_cards: serde_json::Value = "notifyCards",
        /// Host suspensions, oldest first: `[start, end]` wall times (kernel._downtime, read by _suspended_after in
        /// layer 3). Anything but a pair of numbers is refused: Python unpacks each entry as `(start, _e)`.
        downtime: Vec<(Num, Num)> = "downtime",
    }
}

record! {
    pub struct NudgeRecord {
        count: Num = "count",
        failed: bool = "failed",
        failed_at: Option<Num> = "failedAt",
    }
}

record! {
    /// What the per-card build reads about one alive session, besides its tree.
    pub struct SessionFacts {
        names: Vec<Option<String>> = "names",
        flags: serde_json::Value = "flags",
        leaf_ok: bool = "leafOk",
        store: Option<Store> = "store",
        captions: serde_json::Value = "captions",
        recorded: Recorded = "recorded",
    }
    required: [recorded]
}

record! {
    pub struct PeerFacts {
        names: Vec<Option<String>> = "names",
        flags: serde_json::Value = "flags",
        store: Option<Store> = "store",
    }
}

record! {
    /// The boundary helpers' answers, recorded by sid. An absent key: Python never called the helper.
    pub struct Recorded {
        closer_pending: bool = "closerPending",
        interrupting: bool = "interrupting",
        session_retrying: serde_json::Value = "sessionRetrying",
        /// Keyed by the idle argument (`idle=true`, `idle=false`).
        session_awaiting: OMap<serde_json::Value> = "sessionAwaiting",
        bg_live: Vec<serde_json::Value> = "bgLive",
        api_error: serde_json::Value = "apiError",
        interrupted_by: serde_json::Value = "interruptedBy",
        /// A tuple: (the newest answered time, {peer key: time}); times keep their int/float tag.
        peer_answered: (Num, OMap<Num>) = "peerAnswered",
        heal_session_tops: serde_json::Value = "healSessionTops",
        awaiting_task_descs: serde_json::Value = "awaitingTaskDescs",
        cap_switch_offer: serde_json::Value = "capSwitchOffer",
        warm_wanted: serde_json::Value = "warmWanted",
    }
}

record! {
    /// A session's goal store as the build renders it (transient `_base*`, `_unread` and `_relayPending` stripped).
    pub struct Store {
        romp_uuid: Option<String> = "rompUuid",
        seq: Num = "seq",
        placements_v: Num = "placementsV",
        nodes: OMap<Node> = "nodes",
        placements: OMap<String> = "placements",
        status: OMap<String> = "status",
        last_node: Option<String> = "lastNode",
        /// May be null (`store.get("seams") or []`).
        seams: Option<Vec<Seam>> = "seams",
        confirming: Vec<serde_json::Value> = "confirming",
        rev: Num = "rev",
    }
}

record! {
    /// One goal node.
    pub struct Node {
        id: String = "id",
        text: Option<String> = "text",
        parent_id: Option<String> = "parentId",
        node_complete: bool = "nodeComplete",
        blocked: bool = "blocked",
        cleared: bool = "cleared",
        trail: Vec<String> = "trail",
        prompt_uuid: Option<String> = "promptUuid",
        quote: Option<String> = "quote",
        t: Num = "t",
        mt: Num = "mt",
        why: Option<String> = "why",
        log: Vec<LogRow> = "log",
        done_why: Option<String> = "doneWhy",
        block_why: Option<String> = "blockWhy",
        summary: Option<String> = "summary",
        block_summary: Option<String> = "blockSummary",
        settled_at: Option<Num> = "settledAt",
        settled_done: bool = "settledDone",
        handoff: Option<Handoff> = "handoff",
        origin: Option<NodeOrigin> = "origin",
    }
    required: [id]
}

record! {
    /// A verdict row in a node's log.
    pub struct LogRow {
        ev_t: Option<Num> = "ev_t",
        src: Option<String> = "src",
        kind: Option<String> = "kind",
        why: Option<String> = "why",
        seg: Option<String> = "seg",
        at: Option<Num> = "at",
    }
}

record! {
    /// A settle seam: the segments a settled top owns at the split.
    pub struct Seam {
        t: Num = "t",
        top: Option<String> = "top",
        text: Option<String> = "text",
        /// The timestamp-invariant keys (jd._seg_key) of the segments the top owned; null or empty skips the seam.
        segs: Option<Vec<String>> = "segs",
    }
}

record! {
    pub struct Handoff {
        peer: Option<String> = "peer",
        msg_id: Option<String> = "msgId",
        goal_id: Option<String> = "goalId",
    }
}

record! {
    /// Where a goal came from: a peer's handoff.
    pub struct NodeOrigin {
        peer: Option<String> = "peer",
        goal_id: Option<String> = "goalId",
        peer_name: Option<String> = "peerName",
        msg_id: Option<String> = "msgId",
    }
}

/// Keys the per-card code reads on a node (an ast scan grouped by receiver: `nd`, `nodes[x]`, `nodes[nid]`, `root`)
/// that `Node` does not type yet. No node in either world carries one; the layer that ports its reader types it.
pub const NODE_UNPORTED: &[&str] = &[
    "agentTask", "askAnchor", "awaitingAt", "awaitingKind", "awaitingPeers", "awaitingWhy", "background",
    "blockCheckT", "born", "briefParts", "deltaSince", "distilledMt", "failLog", "followupAt", "followupPending",
    "relayCarried", "rolledUp", "serving", "stallSummary", "summaryAnchor", "summaryAnchors", "summaryParts",
    "summaryQuote", "umbrella", "userAsk", "warns",
];
/// The same for a handoff (`nd['handoff']`, `nodes[x]['handoff']`) and an origin (`o`).
pub const HANDOFF_UNPORTED: &[&str] = &["tracked", "peerHost"];
pub const ORIGIN_UNPORTED: &[&str] = &["tracked", "peerHost"];
/// The same for a log row (`e`, `r`).
pub const LOG_UNPORTED: &[&str] = &["a", "q", "login", "synth", "fsid"];

impl Snapshot {
    /// `now`, the build clock.
    pub fn now(&self) -> Num {
        self.world.as_ref().and_then(|w| w.now).expect("world.now is required at load")
    }

    pub fn alive(&self) -> &[AliveRow] {
        self.alive.as_deref().unwrap_or_default()
    }

    /// Every store in the snapshot, in file order: the alive sessions', then the peers'. Each with the sid it
    /// belongs to and where it sits.
    pub fn stores(&self) -> Vec<(String, &str, &Store)> {
        let mut out = Vec::new();
        for (sid, s) in self.sessions.iter().flat_map(OMap::iter) {
            if let Some(Some(st)) = &s.store {
                out.push((format!("sessions.{sid}"), sid.as_str(), st));
            }
        }
        for (sid, p) in self.peers.iter().flat_map(OMap::iter) {
            if let Some(Some(st)) = &p.store {
                out.push((format!("peers.{sid}"), sid.as_str(), st));
            }
        }
        out
    }
}

/// Refuse a snapshot the port would read differently from Python: another schema, an alive row without facts, or a
/// store whose records carry a key the per-card code reads but the typed structs do not carry.
pub fn validate(snap: &Snapshot) -> Result<(), String> {
    if snap.schema.as_deref() != Some(SCHEMA) {
        return Err(format!("schema is {:?}, this loader reads {SCHEMA:?}", snap.schema));
    }
    let sessions = snap.sessions.as_ref().expect("required at load");
    for row in snap.alive() {
        let sid = row.sid.as_deref().expect("required at load");
        if sessions.get(sid).is_none() {
            return Err(format!("alive session {sid} has no entry in sessions"));
        }
    }
    for (path, _sid, st) in snap.stores() {
        for (nid, nd) in st.nodes.iter().flat_map(OMap::iter) {
            if let Some(k) = nd.unported_key(NODE_UNPORTED) {
                return Err(format!("{path}.store.nodes.{nid}: key {k:?} is read by the per-card code but not typed"));
            }
            if let Some(Some(h)) = &nd.handoff
                && let Some(k) = h.unported_key(HANDOFF_UNPORTED)
            {
                return Err(format!("{path}.store.nodes.{nid}.handoff: key {k:?} is read but not typed"));
            }
            if let Some(Some(o)) = &nd.origin
                && let Some(k) = o.unported_key(ORIGIN_UNPORTED)
            {
                return Err(format!("{path}.store.nodes.{nid}.origin: key {k:?} is read but not typed"));
            }
            for (i, r) in nd.log.iter().flatten().enumerate() {
                if let Some(k) = r.unported_key(LOG_UNPORTED) {
                    return Err(format!("{path}.store.nodes.{nid}.log[{i}]: key {k:?} is read but not typed"));
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::record::ToJson;

    /// A synthetic snapshot in the dumper's shape: one alive session whose store has `placementsV` last (as s004's
    /// never-planned store has it in both worlds), a float mtime, a recorded tuple.
    const SNAP: &str = r##"{"schema": "romp-spike-snapshot/1", "world": {"name": "unit", "now": 1781100000, "mintClock": 1781098200, "colormap": "aurora", "hostName": "TESTHOST", "repoHead": "000000000000", "python": "3.12.11", "sizes": {"sessions": 1, "fillers": 0, "records": 2, "chatTurns": 0, "chatRecords": 1}}, "alive": [{"sid": "s-1", "name": "s001", "anchor": null, "mtime": 1781099940.0, "tree": "trees/s-1.json", "transcript": "transcripts/s-1.jsonl"}], "liveMap": {"s-1": {"state": "idle", "since": 1781099900, "model": "", "effort": "", "context": null, "compactPct": null, "color": null}}, "board": {"cleared": [], "jactive": [], "jauth": {}, "wmap": {}, "stalls": {}, "nudgeRecords": {}, "nudgeTimes": {}, "debug": false, "debugRows": null, "notifyCards": {}, "downtime": []}, "sessions": {"s-1": {"names": ["s001", "/TESTDIR/work/x", "#4a9eff"], "flags": null, "leafOk": true, "store": {"rompUuid": "s-1", "seq": 1, "nodes": {"s-1:g1": {"id": "s-1:g1", "text": "caf\u00e9", "parentId": null, "nodeComplete": false, "blocked": false, "cleared": false, "trail": ["s-1:5:abcd1234"], "promptUuid": null, "quote": null, "t": 5, "mt": 5.0, "why": "x", "log": [{"ev_t": 5, "src": "planner", "kind": "done", "seg": "s-1:5:abcd1234", "at": 7}]}}, "placements": {"s-1:5:abcd1234": "s-1:g1"}, "status": {}, "placementsV": 18}, "captions": null, "recorded": {"interrupting": false, "peerAnswered": [0, {"s-2": 1781099000.5}]}}}, "peers": {}, "frameKeys": ["asks", "now"], "frame": {}}"##;

    #[test]
    fn a_snapshot_round_trips_in_its_own_key_order() {
        let snap = crate::load::snapshot_from_slice(SNAP.as_bytes()).unwrap();
        assert_eq!(romp_json::dumps(&snap.to_json()), SNAP);
        assert_eq!(snap.alive()[0].mtime, Some(Num::Float(1781099940.0)));
        let rec = snap.sessions.as_ref().unwrap().get("s-1").unwrap().recorded.as_ref().unwrap();
        let (at, pairs) = rec.peer_answered.as_ref().unwrap();
        assert_eq!((*at, pairs.get("s-2")), (Num::Int(0), Some(&Num::Float(1781099000.5))));
        assert_eq!(rec.closer_pending, None, "never called: absent, not false");
        assert_eq!(snap.stores().len(), 1);
    }

    #[test]
    fn another_schema_is_refused() {
        let other = SNAP.replace("romp-spike-snapshot/1", "romp-spike-snapshot/2");
        let e = crate::load::snapshot_from_slice(other.as_bytes()).unwrap_err();
        assert_eq!(e, "schema is Some(\"romp-spike-snapshot/2\"), this loader reads \"romp-spike-snapshot/1\"");
    }

    #[test]
    fn a_node_key_the_build_reads_but_the_port_does_not_type_is_refused() {
        let born = SNAP.replace(r#""why": "x", "log""#, r#""why": "x", "born": {"why": "y"}, "log""#);
        let e = crate::load::snapshot_from_slice(born.as_bytes()).unwrap_err();
        assert_eq!(e, "sessions.s-1.store.nodes.s-1:g1: key \"born\" is read by the per-card code but not typed");
        let row = SNAP.replace(r#""kind": "done", "seg""#, r#""kind": "done", "synth": true, "seg""#);
        let e = crate::load::snapshot_from_slice(row.as_bytes()).unwrap_err();
        assert_eq!(e, "sessions.s-1.store.nodes.s-1:g1.log[0]: key \"synth\" is read but not typed");
    }

    #[test]
    fn an_alive_row_without_facts_is_refused() {
        let orphan = SNAP.replace(r#""sessions": {"s-1": "#, r#""sessions": {"s-9": "#);
        let e = crate::load::snapshot_from_slice(orphan.as_bytes()).unwrap_err();
        assert_eq!(e, "alive session s-1 has no entry in sessions");
    }
}
