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
        /// The stored login the launch carried (kernel._login_refusal_label, layer 4); Python reads `str(x or "")`,
        /// so anything but a string or null is refused.
        auth_login: Option<String> = "authLogin",
        /// The login the CLI's init said it used.
        auth_login_live: Option<String> = "authLoginLive",
        /// That login's display label.
        auth_label: Option<String> = "authLabel",
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
        /// The names-registry entry's tab fields (kernel._names_parts), null when the registry has none.
        names: Option<Vec<String>> = "names",
        flags: serde_json::Value = "flags",
        leaf_ok: bool = "leafOk",
        store: Option<Store> = "store",
        /// The captions `_captions(fsid)` answered (unit id to caption row), null when the build never read them: a port
        /// that reaches the read anyway must fail loudly (layer 5's provisional card).
        captions: Option<OMap<CaptionRow>> = "captions",
        recorded: Recorded = "recorded",
    }
    required: [recorded]
}

record! {
    pub struct PeerFacts {
        names: Option<Vec<String>> = "names",
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
        /// kernel._heal_session_tops: node id to `[new parent or null, born record]` (a JSON round trip of its tuple).
        heal_session_tops: OMap<(Option<String>, Born)> = "healSessionTops",
        awaiting_task_descs: serde_json::Value = "awaitingTaskDescs",
        cap_switch_offer: serde_json::Value = "capSwitchOffer",
        warm_wanted: serde_json::Value = "warmWanted",
        /// kernel._bg_owner_tops (reached only with live background tasks): top to `{since, descs}`.
        bg_owner_tops: serde_json::Value = "bgOwnerTops",
        /// kernel._bg_service_descs (live tasks on a live session): the service chip's descriptions.
        bg_service_descs: serde_json::Value = "bgServiceDescs",
        /// The live backend's `current_ask(fsid)` (_blocked_placeholder's question; dump_layer6.py records it, the
        /// S2 dumper does not, since no world reaches it).
        current_ask: serde_json::Value = "currentAsk",
    }
}

record! {
    /// A session's goal store as the build renders it (transient `_base*`, `_unread` and `_relayPending` stripped).
    pub struct Store {
        romp_uuid: Option<String> = "rompUuid",
        seq: Num = "seq",
        placements_v: Num = "placementsV",
        nodes: OMap<Node> = "nodes",
        /// A segment id (with its `#p`, `#d` or `#live` phase) to the node the planner placed it on; null is a retired
        /// placement, still a ruling (kernel._seg_placed, _live_cleared_under). The map may be null
        /// (`(store or {}).get("placements") or {}`).
        placements: Option<OMap<Option<String>>> = "placements",
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
        /// The agent's own to-do item this node mirrors (`(nd.get("agentTask") or {}).get("status")`): any falsy
        /// value or a dict; a truthy non-dict is refused (`validate`), as Python's `.get` would raise on it.
        agent_task: serde_json::Value = "agentTask",
        /// The judge's awaiting stamp (layer 4, _goal_awaiting_stamp_full): its why, anchor, kind and peer keys.
        awaiting_why: Option<String> = "awaitingWhy",
        awaiting_at: Option<Num> = "awaitingAt",
        awaiting_kind: Option<String> = "awaitingKind",
        awaiting_peers: Option<Vec<String>> = "awaitingPeers",
        /// A rolled-down done flag (read for its truthiness only).
        rolled_up: serde_json::Value = "rolledUp",
        /// A step the session started on its own (T319). Python reads it only when it is a dict, so null is kept and
        /// any other non-dict is refused.
        born: Option<Born> = "born",
        /// jd.review_boundary's reopen boundary and summary watermark.
        delta_since: Option<Num> = "deltaSince",
        distilled_mt: Option<Num> = "distilledMt",
        /// The distiller's cited anchor (_node_anchor_uuids' cold fallback).
        summary_anchor: Option<String> = "summaryAnchor",
        /// The judge's latched verdict on whether the top is the dictated ask (_pure_delegation_top): compared with
        /// "human", "absent", "scheduled" and "machine", so any value loads and only those strings count.
        ask_anchor: serde_json::Value = "askAnchor",
        /// T105's chain-proven ask record (_pure_delegation_top reads only whether it is a dict).
        user_ask: serde_json::Value = "userAsk",
        /// The follow-up stamp and the brief's coverage stamp (_summary_outrun: `(followupAt or 0) > cov`,
        /// `int(briefedMt or 0)`). Numbers or null; Python would also take a bool, and `int()` a numeric string, which
        /// the loader refuses.
        followup_at: Option<Num> = "followupAt",
        briefed_mt: Option<Num> = "briefedMt",
        /// Layer 6's card and row fields, passed through as Python reads them: `background`, `followupPending` and
        /// `summaryQuote` raw (`nd.get(k)`); `briefParts`, `summaryParts`, `relayCarried`, `stallSummary`, `warns` and
        /// `failLog` as `nd.get(k) or None`; `umbrella` for its truthiness (_closure_done); `serving` as a dict whose
        /// truthy `goalId` folds the card (the serving fold).
        background: serde_json::Value = "background",
        followup_pending: serde_json::Value = "followupPending",
        summary_quote: serde_json::Value = "summaryQuote",
        brief_parts: serde_json::Value = "briefParts",
        summary_parts: serde_json::Value = "summaryParts",
        relay_carried: serde_json::Value = "relayCarried",
        stall_summary: serde_json::Value = "stallSummary",
        warns: serde_json::Value = "warns",
        fail_log: serde_json::Value = "failLog",
        umbrella: serde_json::Value = "umbrella",
        serving: serde_json::Value = "serving",
        /// The unblocker's watermark: `int(nd.get("blockCheckT") or 0)` in _block_check_floor, `blockCheckT or 0` in the
        /// re-judging latch. A number or null; Python would also take a bool, and `int()` a numeric string, which the
        /// loader refuses.
        block_check_t: Option<Num> = "blockCheckT",
        /// The per-paragraph citations: a list of `{a, q}` (or falsy items); layer 6 refuses any other truthy shape.
        summary_anchors: serde_json::Value = "summaryAnchors",
    }
    required: [id]
}

record! {
    /// One caption row (kernel._captions' mapping value); _seg_caption reads `(row or {}).get("caption", "")`.
    pub struct CaptionRow {
        caption: serde_json::Value = "caption",
    }
}

record! {
    /// A born record: on a node (`born`) or in a heal answer (`healSessionTops`). `why` and `parentText` are read
    /// through `str(x or "")`, so anything but a string or null is refused.
    pub struct Born {
        why: Option<String> = "why",
        parent_text: Option<String> = "parentText",
        /// The feed shows no card for it (read for its truthiness only).
        hidden: serde_json::Value = "hidden",
    }
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
        /// A migration's reconstructed row (_parked_rows skips it; truthiness only).
        synth: serde_json::Value = "synth",
        /// A lift row retracts a wait (kernel._stamp_written_at skips it; truthiness only).
        lift: serde_json::Value = "lift",
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
        /// A report-back handoff (truthiness: the card's delegTracked).
        tracked: serde_json::Value = "tracked",
        /// The recipient's host as the courier recorded it (no ported reader; kept typed for the round trip).
        peer_host: serde_json::Value = "peerHost",
    }
}

record! {
    /// Where a goal came from: a peer's handoff.
    pub struct NodeOrigin {
        peer: Option<String> = "peer",
        goal_id: Option<String> = "goalId",
        peer_name: Option<String> = "peerName",
        msg_id: Option<String> = "msgId",
        /// The planted copy of a tracked handoff (truthiness: the card's `satellite`).
        tracked: serde_json::Value = "tracked",
        /// The sender's host (`o.get("peerHost") or ""`, passed through when truthy).
        peer_host: serde_json::Value = "peerHost",
    }
}

/// Keys the per-card code reads on a node (an ast scan grouped by receiver: `nd`, `nodes[x]`, `nodes[nid]`, `root`)
/// that `Node` does not type yet. No node in either world carries one; the layer that ports its reader types it.
/// Layer 6 typed the last 13 (S4.6); an ast scan of the body and layers 1 to 5 found no other untyped node key read.
pub const NODE_UNPORTED: &[&str] = &[];
/// The same for a handoff (`nd['handoff']`, `nodes[x]['handoff']`) and an origin (`o`): `tracked` and `peerHost`, typed
/// by layer 6.
pub const HANDOFF_UNPORTED: &[&str] = &[];
pub const ORIGIN_UNPORTED: &[&str] = &[];
/// The same for a log row. The scan grouped by receiver name had listed `a`, `q`, `login` and `fsid` under `e` and
/// `r`, but those are reads of other dicts under the same names: a summaryAnchors item (`e["a"]`, `e.get("q")`), a
/// debug row (`r.get("fsid")`), and build_feed's judge-auth entries (`e.get("login")`, outside the body). No ported
/// function reads them from a log row.
pub const LOG_UNPORTED: &[&str] = &[];

/// The longest parent chain a store may hold (nodes, counting the node itself). _agent_open_set and _open_leaves
/// recurse once per level, and Python raises RecursionError near 1,000 levels (the exact depth moves with the stack
/// at the call), so a deeper store is refused at load. Layer 4 walks iteratively and would answer at any depth; the
/// refusal keeps the port to inputs on which Python answers.
pub const MAX_NODE_DEPTH: usize = 500;

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
/// store the port refuses (`validate_store`).
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
        validate_store(&format!("{path}.store"), st)?;
    }
    Ok(())
}

/// Refuse a store the port would read differently from Python: a node the port refuses (`validate_node`), or a parent
/// chain longer than MAX_NODE_DEPTH (Python's recursive walks raise near 1,000 levels).
pub fn validate_store(path: &str, st: &Store) -> Result<(), String> {
    let Some(nodes) = &st.nodes else { return Ok(()) };
    for (nid, nd) in nodes.iter() {
        validate_node(&format!("{path}.nodes.{nid}"), nd)?;
    }
    if let Some((nid, depth)) = deepest_chain(nodes).filter(|&(_, d)| d > MAX_NODE_DEPTH) {
        return Err(format!("{path}.nodes.{nid}: a parent chain of {depth} nodes, past the {MAX_NODE_DEPTH} the port \
                            admits (Python's recursive walks raise near 1,000)"));
    }
    Ok(())
}

/// The node with the longest chain of distinct ancestors (following `parentId` until a missing node or a repeat),
/// counting the node itself: the deepest recursion _agent_open_set or _open_leaves can reach in this store. The count
/// stops at MAX_NODE_DEPTH + 1.
pub fn deepest_chain(nodes: &OMap<Node>) -> Option<(&str, usize)> {
    let index: std::collections::HashMap<&str, &Node> = nodes.iter().map(|(k, v)| (k.as_str(), v)).collect();
    let mut best: Option<(&str, usize)> = None;
    for (nid, nd) in nodes.iter() {
        let mut seen = std::collections::HashSet::from([nid.as_str()]);
        let mut cur = nd;
        while seen.len() <= MAX_NODE_DEPTH {
            let Some(p) = cur.parent_id.as_ref().and_then(|p| p.as_deref()) else { break };
            let Some(next) = index.get(p) else { break };
            if !seen.insert(p) {
                break;
            }
            cur = next;
        }
        if best.is_none_or(|(_, d)| seen.len() > d) {
            best = Some((nid.as_str(), seen.len()));
        }
    }
    best
}

/// Refuse a node the port would read differently from Python: a key the per-card code reads that the port does not
/// type, on the node, its handoff, its origin or a log row, or an `agentTask` that is truthy but not a dict.
pub fn validate_node(path: &str, nd: &Node) -> Result<(), String> {
    if let Some(k) = nd.unported_key(NODE_UNPORTED) {
        return Err(format!("{path}: key {k:?} is read by the per-card code but not typed"));
    }
    if let Some(Some(h)) = &nd.handoff
        && let Some(k) = h.unported_key(HANDOFF_UNPORTED)
    {
        return Err(format!("{path}.handoff: key {k:?} is read but not typed"));
    }
    if let Some(Some(o)) = &nd.origin
        && let Some(k) = o.unported_key(ORIGIN_UNPORTED)
    {
        return Err(format!("{path}.origin: key {k:?} is read but not typed"));
    }
    for (i, r) in nd.log.iter().flatten().enumerate() {
        if let Some(k) = r.unported_key(LOG_UNPORTED) {
            return Err(format!("{path}.log[{i}]: key {k:?} is read but not typed"));
        }
    }
    if let Some(a) = &nd.agent_task
        && crate::record::py_truthy(a)
        && !a.is_object()
    {
        return Err(format!("{path}.agentTask: a truthy non-dict ({a}): Python's `.get(\"status\")` raises on it"));
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
    fn an_integer_past_i64_is_refused() {
        // serde_json would read it as the float 1e+20, where Python's json keeps the int
        let big = SNAP.replace(r#""mt": 5.0"#, r#""mt": 100000000000000000000"#);
        let e = crate::load::snapshot_from_slice(big.as_bytes()).unwrap_err();
        assert_eq!(e, "sessions.s-1.store.nodes.s-1:g1.mt: an integer past i64, which Python reads as an int and \
                       serde_json as a float");
        let edge = SNAP.replace(r#""mt": 5.0"#, r#""mt": 9223372036854775807"#);
        assert!(crate::load::snapshot_from_slice(edge.as_bytes()).is_ok(), "i64's top loads");
    }

    #[test]
    fn another_schema_is_refused() {
        let other = SNAP.replace("romp-spike-snapshot/1", "romp-spike-snapshot/2");
        let e = crate::load::snapshot_from_slice(other.as_bytes()).unwrap_err();
        assert_eq!(e, "schema is Some(\"romp-spike-snapshot/2\"), this loader reads \"romp-spike-snapshot/1\"");
    }

    #[test]
    fn a_node_key_the_build_reads_is_typed_and_a_truthy_non_dict_task_is_refused() {
        // layer 6 typed the last keys the per-card code reads (NODE_UNPORTED is empty): each loads into its field and
        // round-trips in place; a log row's `login` (a judge-auth entry's key, never a log row's) stays untyped
        let keys = r#""warns": ["y"], "umbrella": true, "blockCheckT": 150.5, "summaryAnchors": [{"a": "u"}], "#;
        let typed = SNAP.replace(r#""why": "x", "log""#, &format!(r#""why": "x", {keys}"log""#));
        let snap = crate::load::snapshot_from_slice(typed.as_bytes()).unwrap();
        assert_eq!(romp_json::dumps(&snap.to_json()), typed);
        let nd = snap.sessions.as_ref().unwrap().get("s-1").unwrap().store.as_ref().unwrap().as_ref().unwrap();
        let nd = nd.nodes.as_ref().unwrap().get("s-1:g1").unwrap();
        assert_eq!(nd.block_check_t, Some(Some(Num::Float(150.5))));
        assert!(nd.extra.is_empty(), "every key typed");
        assert!(NODE_UNPORTED.is_empty() && HANDOFF_UNPORTED.is_empty() && ORIGIN_UNPORTED.is_empty());
        let row = SNAP.replace(r#""kind": "done", "seg""#, r#""kind": "done", "login": "x", "seg""#);
        assert!(crate::load::snapshot_from_slice(row.as_bytes()).is_ok(), "a log row's login is no read of the build");
        let bct = SNAP.replace(r#""why": "x", "log""#, r#""why": "x", "blockCheckT": "5", "log""#);
        let e = crate::load::snapshot_from_slice(bct.as_bytes()).unwrap_err();
        assert!(e.contains("Node.blockCheckT"), "{e}");
        let task = SNAP.replace(r#""why": "x", "log""#, r#""why": "x", "agentTask": "open", "log""#);
        let e = crate::load::snapshot_from_slice(task.as_bytes()).unwrap_err();
        assert_eq!(e, "sessions.s-1.store.nodes.s-1:g1.agentTask: a truthy non-dict (\"open\"): Python's \
                       `.get(\"status\")` raises on it");
        let falsy = SNAP.replace(r#""why": "x", "log""#, r#""why": "x", "agentTask": false, "born": null, "log""#);
        assert!(crate::load::snapshot_from_slice(falsy.as_bytes()).is_ok(), "a falsy agentTask and a null born load");
        let born = SNAP.replace(r#""why": "x", "log""#, r#""why": "x", "born": "y", "log""#);
        let e = crate::load::snapshot_from_slice(born.as_bytes()).unwrap_err();
        assert!(e.contains("Node.born: invalid type: string \"y\", expected a JSON object (Born)"), "{e}");
    }

    #[test]
    fn a_parent_chain_past_the_depth_limit_is_refused() {
        let chain = |n: usize| {
            let nodes: Vec<String> = (0..n)
                .map(|i| {
                    let parent = if i == 0 { "null".to_string() } else { format!("\"g{}\"", i - 1) };
                    format!(r#""g{i}": {{"id": "g{i}", "parentId": {parent}}}"#)
                })
                .collect();
            SNAP.replace(r#""placements": {"s-1:5:abcd1234": "s-1:g1"}"#,
                         &format!(r#""nodes2": null, "placements": {{}}"#))
                .replacen(r#""nodes": {"s-1:g1": "#, &format!(r#""nodes": {{{}, "x": "#, nodes.join(", ")), 1)
        };
        assert!(crate::load::snapshot_from_slice(chain(MAX_NODE_DEPTH).as_bytes()).is_ok());
        let e = crate::load::snapshot_from_slice(chain(MAX_NODE_DEPTH + 1).as_bytes()).unwrap_err();
        assert_eq!(e, format!("sessions.s-1.store.nodes.g{MAX_NODE_DEPTH}: a parent chain of {} nodes, past the \
                               {MAX_NODE_DEPTH} the port admits (Python's recursive walks raise near 1,000)",
                              MAX_NODE_DEPTH + 1));
        // a cycle counts its distinct members only: one of 500 loads, one of 501 is refused (Python's _open_leaves
        // recurses once around it, and answers until near 1,000)
        let cyc = SNAP.replace(r#""parentId": null"#, r#""parentId": "s-1:g1""#);
        assert!(crate::load::snapshot_from_slice(cyc.as_bytes()).is_ok());
        let ring = |n: usize| {
            let nodes: Vec<String> =
                (0..n).map(|i| format!(r#""y{i}": {{"id": "y{i}", "parentId": "y{}"}}"#, (i + n - 1) % n)).collect();
            let m: OMap<Node> = serde_json::from_str(&format!("{{{}}}", nodes.join(", "))).unwrap();
            validate_store("st", &Store { nodes: Some(m), ..Store::default() })
        };
        assert_eq!(ring(MAX_NODE_DEPTH), Ok(()));
        let e = ring(MAX_NODE_DEPTH + 1).unwrap_err();
        assert!(e.contains(&format!("a parent chain of {} nodes", MAX_NODE_DEPTH + 1)), "{e}");
    }

    #[test]
    fn an_alive_row_without_facts_is_refused() {
        let orphan = SNAP.replace(r#""sessions": {"s-1": "#, r#""sessions": {"s-9": "#);
        let e = crate::load::snapshot_from_slice(orphan.as_bytes()).unwrap_err();
        assert_eq!(e, "alive session s-1 has no entry in sessions");
    }
}
