//! The feed's per-session memo (rust/SPIKE.md, step S6.1): kernel._feed_session_key, the deps re-evaluation
//! (_feed_key_with_deps), the entry cache (_feed_memo with its LRU byte bound, _feed_memo_get/_put/_drop/_forget) and
//! build_feed's per-session loop around them, so a session whose inputs stand is served its previous entry without a
//! derivation, exactly when Python serves it.
//!
//! **The key.** Python's key is a 31-tuple, one component per input the derivation reads, in _FEED_MEMO_LABELS order:
//! files by identity (a stat), in-memory state by value, the clock only through the two booleans it decides. The port
//! computes the same tuple over its inputs:
//! - by value, from what the port already loads, as Python computes them: `parse` (the warm bit and the last turn's
//!   end), `names`, `cleared` (_cleared_by_sid's slice), `row` (_feed_row_key's positions), `ask`, `bg`, `wait`,
//!   `stalls` and `nudge` (over the previous entry's `reads`), `jauth`, `jactive`, `hide`, `downtime`, `debug`'s mode,
//!   `interrupting`, `closer` (under the body's gate), the store's fault bit, `usage`/`offer`'s gate, and `peers` (each
//!   named peer's names, cleared slice and store presence);
//! - recorded, where Python stats a file or reads an in-memory revision the spike has no model of: the snapshot's
//!   `keyFacts` (board-wide: `files`, `auth`, `capOpen`; per session: `cut`, `live`, `watch`, `postal`, `reg`,
//!   `anchors`, `userWrite`, `snapDone`, `hold`, `replayWrote`). Python's oracle (tools/rust-spike/dump_memo.py) runs the
//!   REAL _feed_session_key with exactly these leaves answered from the same record, so both sides read one input and
//!   the comparison is of the key machinery, not of the stats. Absent facts read as Python's values for an untouched
//!   kernel: identities null, except the transcript's, which is `[0, 0, 0, 0]` when the dump says its leaf stats
//!   (`leafOk`; the inode 0 the S2 replay hands as the leaf head).
//! - constant in the spike, as in Python's replay: `subagents` (no recorded helper reads a subagents tree, so no entry
//!   records one), a peer's host and remote name (no attached hosts), the pass memo's alias count (0).
//!
//! Components compare with Python's `==`: an int equals the float of its value and `True` equals 1 (a tag change alone
//! is a hit, serving the old tag, as Python does), a tuple never equals a list, a dict compares unordered.
//!
//! **The loop.** For each alive session in order (build_feed's): the memo's entry is fetched (a use: it moves to the LRU
//! tail), the key is computed over the previous entry's dependency record, and an equal key is a hit, served as is.
//! Otherwise a miss, attributed to the components that moved (`cold` when there was no entry): the session derives
//! (layer 6), the key's dependency components are re-taken over the new entry (`peers` is left UNSETTLED when the entry
//! names a peer the key had no facts for, so the next build derives once more, then hits), and the entry is put unless
//! it carries faults. A raise, in the key or the derivation, serves the previous entry when there is one (it stays under
//! its old key), else the session is absent and a memoized entry that cannot serve (a hidden session's null) is dropped.
//! After the loop, departed sessions' entries are forgotten, with their held aggregates. The anchor table
//! (_node_anchor_last) lives in the memo across builds, as Python's does: a hit runs no body and writes nothing to it.
//!
//! **What is not ported.** _SUMMARY_ANCHOR_MEMO (the landing memo): its only observable effect is S4.5's stale landing
//! after a seam, so the oracle clears it before every build. The fold's kept records (_FEED_FOLD): a hit here folds a
//! copy of the stored entry, where Python hands out the cards it folded before, restamped; the bytes are the same by
//! Python's own contract, the cost is a deep copy per hit (unfair to the port, in the measurement's direction).

use std::cell::RefCell;
use std::cmp::Ordering;

use romp_json::Value as Out;
use serde_json::{Map, Value as J};

use crate::fxhash::{FxMap, FxSet};
use crate::layer2::Raise;
use crate::layer3;
use crate::layer4::AnchorTable;
use crate::layer6::{self, Build};
use crate::record::{py_truthy, Num, ToJson};
use crate::snapshot::{AliveRow, LiveRow, NudgeRecord, SessionFacts, Snapshot};

/// kernel._FEED_MEMO_LABELS, the components in key order.
pub const LABELS: [&str; 31] = [
    "transcript", "parse", "cut", "states", "names", "captions", "store", "anchors", "reg", "cleared", "row", "ask", "live",
    "bg", "wait", "postal", "stalls", "nudge", "jauth", "jactive", "hide", "watch", "subagents", "usage", "offer", "auth",
    "downtime", "debug", "interrupting", "closer", "peers",
];
const SUBAGENTS: usize = 22;
const USAGE: usize = 23;
const OFFER: usize = 24;
const STALLS: usize = 16;
const NUDGE: usize = 17;
const PEERS: usize = 30;
const TRANSCRIPT: usize = 0;

/// kernel._FEED_ROW_RETRY_FIELDS, _FEED_ROW_AGENT_FIELDS, _FEED_ROW_TASK_FIELDS.
const RETRY_FIELDS: [&str; 4] = ["max", "status", "networkDown", "rateLimitType"];
const AGENT_FIELDS: [&str; 3] = ["agentId", "type", "since"];
const TASK_FIELDS: [&str; 5] = ["toolUseId", "type", "desc", "since", "taskId"];
/// The fields of _bg_live_norm's rows the `bg` component takes.
const BG_FIELDS: [&str; 6] = ["tid", "desc", "t", "type", "deadline", "agentId"];

/// One deliberate mistake, for `romp-spike memo --plant`: the change sequences must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// The deps re-evaluation takes facts for a peer the entry names for the first time (no UNSETTLED), so a session
    /// that names a new peer hits one build early.
    PeersSettled,
    /// Numbers compare with their tag: 1 and 1.0, True and 1 differ.
    TagStrict,
    /// The `row` component keys the whole live row, so a change to a field no card reads re-derives.
    RowWhole,
    /// A session whose derivation raises is absent even when the memo holds its previous entry.
    RaiseAbsent,
    /// Departed sessions' entries are kept, so a session that leaves and returns hits.
    NoForget,
    /// The `stalls` component keys every deferral record, not only the node ids the entry read.
    StallsAll,
}

impl Plant {
    pub const ALL: [Plant; 6] =
        [Plant::PeersSettled, Plant::TagStrict, Plant::RowWhole, Plant::RaiseAbsent, Plant::NoForget, Plant::StallsAll];

    pub fn parse(name: &str) -> Option<Plant> {
        Plant::ALL.into_iter().find(|p| p.name() == name)
    }

    pub fn name(self) -> &'static str {
        match self {
            Plant::PeersSettled => "peers-settled",
            Plant::TagStrict => "tag-strict",
            Plant::RowWhole => "row-whole",
            Plant::RaiseAbsent => "raise-absent",
            Plant::NoForget => "no-forget",
            Plant::StallsAll => "stalls-all",
        }
    }
}

// ───────────────────────────── a key component's value ─────────────────────────────

/// One key component (or a part of one) as Python holds it: None, a bool, an int, a float, a string, a tuple, and the
/// list and dict of a recorded JSON value. Equality is Python's `==` (see `PartialEq`).
#[derive(Debug, Clone)]
pub enum KV {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Tuple(Vec<KV>),
    List(Vec<KV>),
    Dict(Vec<(String, KV)>),
}

thread_local! {
    /// The `tag-strict` plant, read by `PartialEq` (a plant is a whole run's setting).
    static TAG_STRICT: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

impl KV {
    /// A JSON value as Python's json module decodes it: arrays as lists, objects as dicts.
    pub fn from_json(v: &J) -> KV {
        match v {
            J::Null => KV::None,
            J::Bool(b) => KV::Bool(*b),
            J::Number(n) => match n.as_i64() {
                Some(i) => KV::Int(i),
                None => KV::Float(n.as_f64().expect("a loaded number is an i64 or a float (refuse_big_ints)")),
            },
            J::String(s) => KV::Str(s.clone()),
            J::Array(a) => KV::List(a.iter().map(KV::from_json).collect()),
            J::Object(m) => KV::Dict(m.iter().map(|(k, v)| (k.clone(), KV::from_json(v))).collect()),
        }
    }

    /// A value the port wrote, as Python would hold its JSON (a planted whole-row key reads one).
    fn from_out(v: &Out) -> KV {
        match v {
            Out::Null => KV::None,
            Out::Bool(b) => KV::Bool(*b),
            Out::Int(i) => KV::Int(*i),
            Out::BigInt(s) => KV::Str(s.clone()),
            Out::Float(f) => KV::Float(*f),
            Out::Str(s) => KV::Str(s.clone()),
            Out::Array(a) => KV::List(a.iter().map(KV::from_out).collect()),
            Out::Object(m) => KV::Dict(m.iter().map(|(k, v)| (k.clone(), KV::from_out(v))).collect()),
        }
    }

    fn from_num(n: Option<Num>) -> KV {
        match n {
            Some(Num::Int(i)) => KV::Int(i),
            Some(Num::Float(f)) => KV::Float(f),
            None => KV::None,
        }
    }

    fn from_str(s: Option<&str>) -> KV {
        s.map_or(KV::None, |s| KV::Str(s.to_string()))
    }

    fn num(&self) -> Option<Num> {
        match *self {
            KV::Bool(b) => Some(Num::Int(i64::from(b))),
            KV::Int(i) => Some(Num::Int(i)),
            KV::Float(f) => Some(Num::Float(f)),
            _ => None,
        }
    }

    fn tag(&self) -> u8 {
        match self {
            KV::Bool(_) => 1,
            KV::Int(_) => 2,
            KV::Float(_) => 3,
            _ => 0,
        }
    }

    /// As JSON, for a decision's report and the tests (a tuple written as a list).
    pub fn to_out(&self) -> Out {
        match self {
            KV::None => Out::Null,
            KV::Bool(b) => Out::Bool(*b),
            KV::Int(i) => Out::Int(*i),
            KV::Float(f) => Out::Float(*f),
            KV::Str(s) => Out::Str(s.clone()),
            KV::Tuple(v) | KV::List(v) => Out::Array(v.iter().map(KV::to_out).collect()),
            KV::Dict(m) => Out::Object(m.iter().map(|(k, v)| (k.clone(), v.to_out())).collect()),
        }
    }
}

impl PartialEq for KV {
    /// Python's `==`: numbers by value across int, float and bool (`1 == 1.0 == True`), a tuple only with a tuple and a
    /// list only with a list (`[1] != (1,)`), a dict by its key set and each value, regardless of order. Loaded floats are
    /// finite, so no NaN arises.
    fn eq(&self, other: &KV) -> bool {
        match (self, other) {
            (KV::None, KV::None) => true,
            (KV::Str(a), KV::Str(b)) => a == b,
            (KV::Tuple(a), KV::Tuple(b)) | (KV::List(a), KV::List(b)) => a == b,
            (KV::Dict(a), KV::Dict(b)) => {
                a.len() == b.len() && a.iter().all(|(k, v)| b.iter().find(|(k2, _)| k2 == k).is_some_and(|(_, w)| v == w))
            }
            _ => match (self.num(), other.num()) {
                (Some(x), Some(y)) => {
                    x.py_cmp(y) == Ordering::Equal && !(TAG_STRICT.with(|t| t.get()) && self.tag() != other.tag())
                }
                _ => false,
            },
        }
    }
}

/// `("unsettled",)`: a `peers` component no computed key equals (kernel._FEED_PEERS_UNSETTLED).
fn unsettled() -> KV {
    KV::Tuple(vec![KV::Str("unsettled".into())])
}

fn tuple_of_strs<'s>(it: impl IntoIterator<Item = &'s str>) -> KV {
    KV::Tuple(it.into_iter().map(|s| KV::Str(s.to_string())).collect())
}

/// `json.dumps(v, sort_keys=True, default=str)` of a JSON value, as a string component.
fn dumps_sorted(v: Option<&J>) -> KV {
    let out = v.map_or(Out::Null, ToJson::to_json);
    KV::Str(romp_json::dumps(&crate::record::canon(&out)))
}

// ───────────────────────────── the recorded facts ─────────────────────────────

fn obj(v: Option<&J>) -> Option<&Map<String, J>> {
    v.and_then(J::as_object)
}

/// The board-wide recorded facts: `files` (kind:name to identity), `auth`, `capOpen`.
fn board_facts(snap: &Snapshot) -> Option<&Map<String, J>> {
    obj(snap.key_facts.as_ref())
}

/// A recorded file identity: Some(the value, null meaning the stat failed) when recorded, None when absent.
fn file<'s>(snap: &'s Snapshot, name: &str) -> Option<&'s J> {
    board_facts(snap).and_then(|f| obj(f.get("files"))).and_then(|m| m.get(name))
}

fn ident(snap: &Snapshot, name: &str) -> KV {
    file(snap, name).map_or(KV::None, KV::from_json)
}

/// A session's recorded fact `k`.
fn session_fact<'s>(facts: &'s SessionFacts, k: &str) -> Option<&'s J> {
    obj(facts.key_facts.as_ref()).and_then(|m| m.get(k))
}

/// The transcript's identity as the key's stat takes it (`_chat_ident(path)`), and the inode in it: the recorded
/// `files["transcript:<sid>"]` (null: the stat failed), else the replay's default, `[0, 0, 0, 0]` when the dump says the
/// leaf stats (`leafOk`), else null. `ctx["leaf_ok"]` is the identity being non-null, `ctx["leaf_head"]` the transcript
/// path with that inode; the body and the held aggregate read both (layer6::Derive).
pub fn transcript_ident(snap: &Snapshot, sid: &str, facts: &SessionFacts) -> (KV, Option<i64>) {
    match file(snap, &format!("transcript:{sid}")) {
        Some(J::Null) => (KV::None, None),
        Some(v) => {
            let inode = v.as_array().and_then(|a| a.first()).and_then(J::as_i64);
            (KV::from_json(v), Some(inode.expect("validated: a transcript identity is null or a list led by an int")))
        }
        None if facts.leaf_ok == Some(true) => (KV::Tuple(vec![KV::Int(0); 4]), Some(0)),
        None => (KV::None, None),
    }
}

/// The leaf's inode when its stat succeeded (`ctx["leaf_ok"]`), for the body's warm-ask gate and the held walk's head.
pub fn leaf_inode(snap: &Snapshot, sid: &str, facts: &SessionFacts) -> Option<i64> {
    transcript_ident(snap, sid, facts).1
}

/// `jd._store_identity(sid)[1:]`: the store's, its journal's and its archive entry's identities, recorded as
/// `files["store:<sid>"]` (a list of three), each null by default.
fn store_ident(snap: &Snapshot, sid: &str) -> [KV; 3] {
    match file(snap, &format!("store:{sid}")).and_then(J::as_array) {
        Some(a) => [KV::from_json(&a[0]), KV::from_json(&a[1]), KV::from_json(&a[2])],
        None => [KV::None, KV::None, KV::None],
    }
}

// ───────────────────────────── the board-wide part, once per build ─────────────────────────────

/// kernel._feed_board_facts: what every session's key reads board-wide, taken once per build, and the build's peer
/// facts table (_feed_peer_facts_once).
pub struct BuildFacts<'b> {
    cleared_by_sid: FxMap<&'b str, Vec<&'b str>>,
    usage: KV,
    cap_open: KV,
    auth: KV,
    downtime: KV,
    debug: KV,
    jactive: FxSet<&'b str>,
    peer_facts: FxMap<String, KV>,
}

impl<'b> BuildFacts<'b> {
    pub fn new(b: &'b Build<'_>) -> BuildFacts<'b> {
        let snap = b.snap;
        let board = b.board;
        // kernel._cleared_by_sid: id -> the text before its LAST colon; an id with no colon belongs to no session
        let mut cleared_by_sid: FxMap<&str, Vec<&str>> = FxMap::default();
        for i in board.cleared.as_deref().unwrap_or_default() {
            if let Some(cut) = i.rfind(':') {
                cleared_by_sid.entry(&i[..cut]).or_default().push(i.as_str());
            }
        }
        for v in cleared_by_sid.values_mut() {
            v.sort_unstable();
        }
        let bf = board_facts(snap);
        let cap_open = match obj(bf.and_then(|f| f.get("capOpen"))) {
            Some(w) => KV::Tuple(vec![KV::from_json(&w["window"]), KV::from_json(&w["resetsAt"])]),
            None => KV::None,
        };
        let dt = b.downtime.as_slice();
        let downtime = KV::Tuple(vec![
            KV::Int(dt.len() as i64),
            dt.last().map_or(KV::None, |(s, e)| KV::Tuple(vec![KV::from_num(Some(*s)), KV::from_num(Some(*e))])),
        ]);
        let dbg = board.debug == Some(true);
        BuildFacts {
            cleared_by_sid,
            usage: ident(snap, "usage"),
            cap_open,
            auth: KV::Bool(bf.and_then(|f| f.get("auth")).is_some_and(py_truthy)),
            downtime,
            debug: KV::Tuple(vec![KV::Bool(dbg), if dbg { ident(snap, "errors") } else { KV::None }]),
            jactive: board.jactive.iter().flatten().map(String::as_str).collect(),
            peer_facts: FxMap::default(),
        }
    }

    fn cleared(&self, sid: &str) -> KV {
        tuple_of_strs(self.cleared_by_sid.get(sid).map_or(&[][..], Vec::as_slice).iter().copied())
    }
}

// ───────────────────────────── the key ─────────────────────────────

/// A session's key and the facts its `peers` component took (`ctx["peer_facts"]`, which the deps re-evaluation reads).
pub struct Keyed {
    pub key: Vec<KV>,
    facts: FxMap<String, KV>,
}

/// An entry's `reads.nudges` as a sorted set (`sorted(set(...))`), empty for no entry.
fn read_nudges(entry: Option<&Out>) -> Vec<&str> {
    let mut ids: Vec<&str> = entry
        .and_then(|e| member(e, "reads"))
        .filter(|r| out_truthy(r))
        .and_then(|r| member(r, "nudges"))
        .and_then(|n| match n {
            Out::Array(items) => Some(items.iter().filter_map(|x| if let Out::Str(s) = x { Some(s.as_str()) } else { None })),
            _ => None,
        })
        .into_iter()
        .flatten()
        .collect();
    ids.sort_unstable();
    ids.dedup();
    ids
}

fn member<'o>(o: &'o Out, k: &str) -> Option<&'o Out> {
    match o {
        Out::Object(m) => m.iter().find(|(x, _)| x == k).map(|(_, v)| v),
        _ => None,
    }
}

fn out_truthy(v: &Out) -> bool {
    match v {
        Out::Null => false,
        Out::Bool(b) => *b,
        Out::Int(i) => *i != 0,
        Out::BigInt(_) => true,
        Out::Float(f) => *f != 0.0,
        Out::Str(s) => !s.is_empty(),
        Out::Array(a) => !a.is_empty(),
        Out::Object(m) => !m.is_empty(),
    }
}

/// An entry that is a value (`prev_entry is not None`): a stored null (a hidden session's) is None.
fn some_entry(e: Option<&Out>) -> Option<&Out> {
    e.filter(|e| !matches!(e, Out::Null))
}

fn reads_usage(entry: Option<&Out>) -> bool {
    entry.and_then(|e| member(e, "reads")).and_then(|r| member(r, "usage")).is_some_and(out_truthy)
}

/// kernel._feed_nudge_key(ctx, entry): per node id the entry read, its nudge fields and, when it has a count, its last
/// eight history times.
fn nudge_key(b: &Build<'_>, entry: Option<&Out>) -> KV {
    let recs = b.board.nudge_records.as_ref();
    let times = b.board.nudge_times.as_ref();
    KV::Tuple(
        read_nudges(entry)
            .into_iter()
            .map(|gid| {
                let rec: Option<&NudgeRecord> = recs.and_then(|m| m.get(gid));
                let fields = KV::Tuple(vec![
                    KV::from_num(rec.and_then(|r| r.count)),
                    rec.and_then(|r| r.failed).map_or(KV::None, KV::Bool),
                    KV::from_num(rec.and_then(|r| r.failed_at).flatten()),
                ]);
                let ts = match rec.and_then(|r| r.count).filter(|c| c.truthy()) {
                    Some(_) => {
                        let all = times.and_then(|m| m.get(gid)).map_or(&[][..], Vec::as_slice);
                        KV::Tuple(all[all.len().saturating_sub(8)..].iter().map(|n| KV::from_num(Some(*n))).collect())
                    }
                    None => KV::Tuple(Vec::new()),
                };
                KV::Tuple(vec![KV::Str(gid.to_string()), fields, ts])
            })
            .collect(),
    )
}

/// kernel._feed_stalls_key(ctx, entry): the deferral records of the node ids the entry read, as (gid, why, since),
/// sorted. A record that is not a dict raises AttributeError at `v.get`, as Python's does.
fn stalls_key(b: &Build<'_>, entry: Option<&Out>, plant: Option<Plant>) -> Result<KV, Raise> {
    let ids = read_nudges(entry);
    let mut out: Vec<(&str, KV)> = Vec::new();
    for (g, v) in b.board.stalls.iter().flat_map(|m| m.iter()) {
        if plant != Some(Plant::StallsAll) && ids.binary_search(&g.as_str()).is_err() {
            continue;
        }
        let Some(m) = v.as_object() else {
            return Err(Raise { exc: "AttributeError", arg: format!("'{}' object has no attribute 'get'", py_type(v)) });
        };
        let get = |k: &str| m.get(k).map_or(KV::None, KV::from_json);
        out.push((g.as_str(), KV::Tuple(vec![KV::Str(g.clone()), get("why"), get("since")])));
    }
    out.sort_by(|a, b| a.0.cmp(b.0));
    Ok(KV::Tuple(out.into_iter().map(|(_, t)| t).collect()))
}

fn py_type(v: &J) -> &'static str {
    match v {
        J::Null => "NoneType",
        J::Bool(_) => "bool",
        J::Number(n) if n.is_f64() => "float",
        J::Number(_) => "int",
        J::String(_) => "str",
        J::Array(_) => "list",
        J::Object(_) => "dict",
    }
}

/// `tuple(sorted(d.items(), key=str)) or None` over a board map's entry for the session (`(m.get(sid) or {})`). The keys
/// are unique, so any total order on them gives Python's equality; key order is used.
fn sorted_items(m: Option<&crate::record::OMap<J>>, sid: &str) -> KV {
    match m.and_then(|m| m.get(sid)).filter(|v| py_truthy(v)).and_then(J::as_object) {
        Some(d) if !d.is_empty() => {
            let mut items: Vec<(&String, &J)> = d.iter().collect();
            items.sort_by(|a, b| a.0.cmp(b.0));
            KV::Tuple(items.into_iter().map(|(k, v)| KV::Tuple(vec![KV::Str(k.clone()), KV::from_json(v)])).collect())
        }
        _ => KV::None,
    }
}

/// A live row's key as `tm.get(key)` reads it (absent and null alike are None).
fn live_kv(r: &LiveRow, key: &str) -> KV {
    let s = |x: &Option<Option<String>>| KV::from_str(x.as_ref().and_then(Option::as_deref));
    match key {
        "state" => s(&r.state),
        "since" => KV::from_num(r.since.flatten()),
        "authLogin" => s(&r.auth_login),
        "authLoginLive" => s(&r.auth_login_live),
        "authLabel" => s(&r.auth_label),
        _ => live_extra(r, key).map_or(KV::None, KV::from_json),
    }
}

fn live_extra<'r>(r: &'r LiveRow, key: &str) -> Option<&'r J> {
    r.extra.iter().find(|(k, _)| k == key).map(|(_, v)| v)
}

/// The dicts of a live row's list field (`for a in (tm.get(k) or ()) if isinstance(a, dict)`); `memo::validate` admits
/// only a list or null there, so nothing else is iterated.
fn live_dicts<'r>(r: &'r LiveRow, key: &str) -> impl Iterator<Item = &'r Map<String, J>> {
    live_extra(r, key).and_then(J::as_array).into_iter().flatten().filter_map(J::as_object)
}

fn get_kv(m: &Map<String, J>, k: &str) -> KV {
    m.get(k).map_or(KV::None, KV::from_json)
}

/// kernel._feed_row_key(tm): the live row's fields the derivation reads, by position; None when not live.
fn row_key(tm: Option<&LiveRow>, plant: Option<Plant>) -> KV {
    let Some(tm) = tm else { return KV::None };
    if plant == Some(Plant::RowWhole) {
        return KV::from_out(&tm.to_json());
    }
    let info = live_extra(tm, "retryInfo").and_then(J::as_object);
    let auth = ["authLive", "auth", "authPending", "authLogin", "authLoginLive", "authLabel"];
    let mut retry = vec![live_kv(tm, "retryCount")];
    retry.extend(RETRY_FIELDS.iter().map(|k| info.map_or(KV::None, |m| get_kv(m, k))));
    KV::Tuple(vec![
        live_kv(tm, "state"),
        live_kv(tm, "since"),
        KV::Tuple(auth.iter().map(|k| live_kv(tm, k)).collect()),
        KV::Tuple(retry),
        KV::Tuple(live_dicts(tm, "subagents").map(|a| KV::Tuple(AGENT_FIELDS.iter().map(|k| get_kv(a, k)).collect())).collect()),
        KV::Tuple(vec![
            KV::Bool(live_extra(tm, "bgTasks").is_some()),
            KV::Tuple(live_dicts(tm, "bgTasks").map(|t| KV::Tuple(TASK_FIELDS.iter().map(|k| get_kv(t, k)).collect())).collect()),
        ]),
    ])
}

/// What the recorded helper `name` answered for the session, or Python's replay raise when it never recorded one.
fn recorded<'r, T>(sid: &str, name: &str, v: &'r Option<T>) -> Result<&'r T, Raise> {
    v.as_ref().ok_or_else(|| layer6::not_recorded(name, sid, None))
}

/// kernel._feed_peer_facts(p) through _feed_peer_facts_once: the peer's store, journal and archive identities, its goal
/// file's identity, whether its store read faulted (the replay's reader raises when it has no store for p), its names
/// entry, its cleared slice, and its attached host and the name that host gave it (none in the replay).
fn peer_facts(b: &Build<'_>, bf: &mut BuildFacts<'_>, p: &str) -> Result<KV, Raise> {
    if let Some(f) = bf.peer_facts.get(p) {
        return Ok(f.clone());
    }
    let snap = b.snap;
    let store_id = KV::Tuple(store_ident(snap, p).into());
    let goal = ident(snap, &format!("goal:{p}"));
    if !b.stores.contains_key(p) {
        return Err(Raise { exc: layer6::NOT_RECORDED, arg: format!("store({p})") });
    }
    let names = tuple_of_strs(b.names.parts(p).unwrap_or_default().iter().map(String::as_str));
    let f = KV::Tuple(vec![store_id, goal, KV::Bool(false), names, bf.cleared(p), KV::Str(String::new()), KV::None]);
    bf.peer_facts.insert(p.to_string(), f.clone());
    Ok(f)
}

/// kernel._feed_session_key(s, tm, ctx, prev_entry) over the port's inputs (the module's header lists each component's
/// source). `prev` is the session's previous entry (None when cold; a stored null is a hidden session's).
pub fn session_key(
    b: &Build<'_>,
    bf: &mut BuildFacts<'_>,
    row: &AliveRow,
    facts: &SessionFacts,
    prev: Option<&Out>,
    plant: Option<Plant>,
) -> Result<Keyed, Raise> {
    let snap = b.snap;
    let fsid = row.sid.as_deref().expect("required at load");
    let path = row.transcript.as_deref().expect("validated: every alive row names its transcript");
    let tm = b.live_map.get(fsid);
    let live = tm.is_some();
    let hide = b.session_flag(fsid, "hideFromFeed");
    let prev = some_entry(prev);
    let sk = |k: &str| session_fact(facts, k);

    let (tident, _) = transcript_ident(snap, fsid, facts);
    let transcript = KV::Tuple(vec![tident, KV::Str(path.to_string())]);
    let mut keys_states: Vec<&str> = vec![fsid];
    let anchor = row.anchor.as_ref().and_then(Option::as_deref).unwrap_or("");
    if anchor != fsid {
        keys_states.push(anchor);
    }
    let states = KV::Tuple(keys_states.into_iter().filter(|k| !k.is_empty()).map(|k| ident(snap, &format!("states:{k}"))).collect());
    let names = KV::Tuple(vec![
        KV::from_str(row.name.as_ref().and_then(Option::as_deref)),
        tuple_of_strs(b.names.parts(fsid).unwrap_or_default().iter().map(String::as_str)),
    ]);
    let captions = ident(snap, &format!("captions:{fsid}"));
    let sident = store_ident(snap, fsid);
    let hold = match sk("hold").and_then(J::as_object).filter(|m| !m.is_empty()) {
        Some(m) => KV::Tuple(vec![get_kv(m, "cutT"), get_kv(m, "leaf"), get_kv(m, "at")]),
        None => KV::None,
    };
    let anchors = sk("anchors").map_or(KV::Int(0), KV::from_json);
    let reg = KV::Tuple(vec![sk("reg").map_or(KV::None, KV::from_json), ident(snap, &format!("gone:{fsid}"))]);
    let cleared = bf.cleared(fsid);
    let rowk = row_key(tm, plant);
    let postal = sk("postal").map_or_else(|| KV::Tuple(vec![KV::Tuple(Vec::new()), KV::Tuple(Vec::new())]), KV::from_json);
    let nudge = nudge_key(b, prev);
    let stalls = stalls_key(b, prev, plant)?;
    let jauth = sorted_items(b.board.jauth.as_ref(), fsid);
    let jactive = bf.jactive.contains(fsid);
    let usage = if reads_usage(prev) { bf.usage.clone() } else { KV::None };
    let offer = if reads_usage(prev) { bf.cap_open.clone() } else { KV::None };
    // the peers the previous derivation read, each one's facts taken once per build
    let mut pfacts: FxMap<String, KV> = FxMap::default();
    if let Some(Out::Array(ps)) = prev.and_then(|e| member(e, "peers")) {
        for p in ps {
            if let Out::Str(p) = p {
                let f = peer_facts(b, bf, p)?;
                pfacts.insert(p.clone(), f);
            }
        }
    }
    let peers = if prev.is_some() {
        let mut named: Vec<&String> = pfacts.keys().collect();
        named.sort_unstable();
        KV::Tuple(named.into_iter().map(|p| KV::Tuple(vec![KV::Str(p.clone()), pfacts[p].clone()])).collect())
    } else {
        KV::None
    };
    let cut = sk("cut").map_or_else(|| KV::Str(String::new()), KV::from_json);
    let live_rev = sk("live").map_or(KV::Int(0), KV::from_json);
    let watch = dumps_sorted(sk("watch"));
    let rec = facts.recorded.as_ref().expect("required at load");
    let ask = match tm.and_then(|t| t.state.as_ref()).and_then(Option::as_deref) {
        Some(st) if layer6::NEEDS_INPUT_STATES.contains(&st) => rec.current_ask.as_ref().map_or(KV::None, |a| dumps_sorted(Some(a))),
        _ => KV::None,
    };
    let wait = sorted_items(b.board.wmap.as_ref(), fsid);
    // the per-session facts the body reads (a hidden session reads none of them)
    let (mut ps_some, mut who, mut interrupting, mut closer, mut st_none) = (false, false, false, false, true);
    let mut end = KV::None;
    if !hide {
        let tree = b.trees.get(fsid).copied();
        ps_some = tree.is_some();
        if let Some(t) = tree {
            let turns = t.turns.as_deref().unwrap_or_default();
            who = layer3::session_working(turns, &b.downtime);
            end = turns.last().map_or(KV::None, |lt| KV::from_num(lt.end));
        }
        interrupting = *recorded(fsid, "_interrupting", &rec.interrupting)?;
        let store = facts.store.as_ref().expect("validated: every session has a store key");
        st_none = store.is_none();
        closer = live && ps_some && !who && !jactive && *recorded(fsid, "_closer_pending", &rec.closer_pending)?;
    }
    let replayed = if matches!(sident[1], KV::None) {
        KV::None
    } else if sk("replayWrote") == Some(&J::Bool(false)) {
        KV::None
    } else {
        KV::Bool(true)
    };
    let store = KV::Tuple(vec![
        KV::Tuple(sident.to_vec()),
        KV::Tuple(vec![sident[0].clone(), KV::Int(0)]),
        replayed,
        sk("userWrite").map_or(KV::Float(0.0), KV::from_json),
        sk("snapDone").map_or(KV::None, KV::from_json),
        hold,
        KV::Bool(st_none && !hide),
    ]);
    let bg = if ps_some {
        KV::Tuple(
            rec.bg_live
                .iter()
                .flatten()
                .map(|r| {
                    let m = r.as_object().expect("validated: a recorded bgLive row is a dict");
                    KV::Tuple(BG_FIELDS.iter().map(|k| get_kv(m, k)).collect())
                })
                .collect(),
        )
    } else {
        KV::None
    };
    let parse = KV::Tuple(vec![KV::Bool(ps_some), end]);
    let key = vec![
        transcript, parse, cut, states, names, captions, store, anchors, reg, cleared, rowk, ask, live_rev, bg, wait, postal,
        stalls, nudge, jauth, KV::Bool(jactive), KV::Bool(hide), watch, KV::None, usage, offer, bf.auth.clone(),
        bf.downtime.clone(), bf.debug.clone(), KV::Bool(interrupting), KV::Bool(closer), peers,
    ];
    debug_assert_eq!(key.len(), LABELS.len());
    Ok(Keyed { key, facts: pfacts })
}

/// kernel._feed_key_with_deps(key, ctx, entry): the dependency components re-taken over the entry a derivation just
/// produced (`entry` None or null for a hidden session). A peer the entry names that the key had no facts for leaves
/// `peers` UNSETTLED, so the next build derives once more with every peer's facts taken before the read.
pub fn with_deps(
    b: &Build<'_>,
    bf: &mut BuildFacts<'_>,
    keyed: Keyed,
    entry: Option<&Out>,
    plant: Option<Plant>,
) -> Result<Vec<KV>, Raise> {
    let Keyed { mut key, facts } = keyed;
    let entry = some_entry(entry);
    key[SUBAGENTS] = KV::None;
    key[USAGE] = if reads_usage(entry) { bf.usage.clone() } else { KV::None };
    key[OFFER] = if reads_usage(entry) { bf.cap_open.clone() } else { KV::None };
    key[NUDGE] = nudge_key(b, entry);
    key[STALLS] = stalls_key(b, entry, plant)?;
    key[PEERS] = match entry {
        None => KV::None,
        Some(e) => {
            let mut named: Vec<&str> = match member(e, "peers") {
                Some(Out::Array(ps)) => ps.iter().filter_map(|p| if let Out::Str(s) = p { Some(s.as_str()) } else { None }).collect(),
                _ => Vec::new(),
            };
            named.sort_unstable();
            if named.iter().all(|p| facts.contains_key(*p)) {
                KV::Tuple(named.iter().map(|p| KV::Tuple(vec![KV::Str((*p).to_string()), facts[*p].clone()])).collect())
            } else if plant == Some(Plant::PeersSettled) {
                let mut out = Vec::new();
                for p in named {
                    out.push(KV::Tuple(vec![KV::Str(p.to_string()), peer_facts(b, bf, p)?]));
                }
                KV::Tuple(out)
            } else {
                unsettled()
            }
        }
    };
    Ok(key)
}

/// kernel._feed_memo_miss's attribution: the sorted labels of the components that differ, `cold` when there was no
/// entry or its key has another length.
pub fn miss_labels(old: Option<&[KV]>, new: &[KV]) -> Vec<&'static str> {
    match old {
        Some(o) if o.len() == new.len() => {
            let mut out: Vec<&'static str> =
                LABELS.iter().zip(o.iter().zip(new)).filter(|(_, (a, b))| a != b).map(|(l, _)| *l).collect();
            out.sort_unstable();
            out
        }
        _ => vec!["cold"],
    }
}

// ───────────────────────────── the entry cache ─────────────────────────────

struct MemoEnt {
    key: Vec<KV>,
    entry: Out,
    size: usize,
    used: u64,
}

/// The memo's counters, as GET /perf builds.feed.memo reports them.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MemoStats {
    pub hit: u64,
    pub miss: u64,
    pub evict: u64,
    pub derived: u64,
    pub failed: u64,
}

/// What became of one session in one build: a hit or a miss (with the components that moved), and a raise (in the key,
/// with no hit or miss, or in the derivation, after the miss), with whether the previous entry was served.
#[derive(Debug, Clone, PartialEq)]
pub struct Decision {
    pub sid: String,
    pub hit: Option<bool>,
    pub miss: Vec<&'static str>,
    pub raised: Option<(&'static str, bool)>,
}

impl Decision {
    /// As the oracle writes it: `{"hit": true}` or `{"miss": [labels]}`, with `"raise": name, "served": bool`.
    pub fn to_out(&self) -> Out {
        let mut m: Vec<(String, Out)> = Vec::new();
        match self.hit {
            Some(true) => m.push(("hit".into(), Out::Bool(true))),
            Some(false) => m.push(("miss".into(), Out::Array(self.miss.iter().map(|l| Out::Str((*l).into())).collect()))),
            None => {}
        }
        if let Some((exc, served)) = self.raised {
            m.push(("raise".into(), Out::Str(exc.into())));
            m.push(("served".into(), Out::Bool(served)));
        }
        Out::Object(m)
    }
}

/// kernel._feed_memo (sid to key, entry and size; the least recently served first out past the byte bound) with its
/// counters, the anchor table it keeps company with across builds (_node_anchor_last), and the last build's decisions.
pub struct FeedMemo {
    map: FxMap<String, MemoEnt>,
    tick: u64,
    bytes: usize,
    /// kernel.FEED_MEMO_BYTES.
    pub bound: usize,
    pub stats: MemoStats,
    pub table: AnchorTable,
    pub decisions: Vec<Decision>,
    pub plant: Option<Plant>,
}

impl FeedMemo {
    pub fn new(bound: usize) -> FeedMemo {
        FeedMemo {
            map: FxMap::default(),
            tick: 0,
            bytes: 0,
            bound,
            stats: MemoStats::default(),
            table: AnchorTable::default(),
            decisions: Vec::new(),
            plant: None,
        }
    }

    pub fn with_plant(mut self, plant: Option<Plant>) -> FeedMemo {
        self.plant = plant;
        TAG_STRICT.with(|t| t.set(plant == Some(Plant::TagStrict)));
        self
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    pub fn bytes(&self) -> usize {
        self.bytes
    }

    /// _feed_memo_get: a read is a use (the entry moves to the LRU tail).
    fn touch(&mut self, sid: &str) {
        self.tick += 1;
        let t = self.tick;
        if let Some(e) = self.map.get_mut(sid) {
            e.used = t;
        }
    }

    /// _feed_memo_put: store (replacing), then shed the least recently served entries while more than one is held and
    /// the bytes exceed the bound (never the whole memo: an entry alone over the bound still serves).
    fn put(&mut self, sid: &str, key: Vec<KV>, entry: Out) {
        let size = romp_json::dumps(&entry).len();
        if let Some(old) = self.map.remove(sid) {
            self.bytes -= old.size;
        }
        self.tick += 1;
        self.map.insert(sid.to_string(), MemoEnt { key, entry, size, used: self.tick });
        self.bytes += size;
        while self.map.len() > 1 && self.bytes > self.bound {
            let k = self.map.iter().min_by_key(|(_, e)| e.used).map(|(k, _)| k.clone()).expect("non-empty");
            self.bytes -= self.map.remove(&k).expect("listed").size;
            self.stats.evict += 1;
        }
    }

    /// _feed_memo_drop: an entry that cannot serve, counted as an eviction.
    fn drop_entry(&mut self, sid: &str) {
        if let Some(old) = self.map.remove(sid) {
            self.bytes -= old.size;
            self.stats.evict += 1;
        }
    }

    /// _feed_memo_forget: the entries of sessions outside the build's alive set.
    fn forget(&mut self, alive: &FxSet<&str>) {
        let gone: Vec<String> = self.map.keys().filter(|k| !alive.contains(k.as_str())).cloned().collect();
        for k in gone {
            self.bytes -= self.map.remove(&k).expect("listed").size;
            self.stats.evict += 1;
        }
    }

    /// For the measurement's miss row: make the stored key's component `label` differ from any computed one, so the next
    /// build misses on it exactly as on a moved input, with the previous entry intact (Python's row moves the recorded
    /// identity instead; the work is the same: the key, the compare, the derivation, the deps, the put).
    pub fn invalidate(&mut self, sid: &str, label: &str) -> bool {
        let i = LABELS.iter().position(|l| *l == label).unwrap_or(TRANSCRIPT);
        match self.map.get_mut(sid) {
            Some(e) => {
                e.key[i] = KV::Str("\u{0}invalidated".into());
                true
            }
            None => false,
        }
    }
}

/// Whether the fold raises on this entry: a null card in its asks (an awaiting placeholder for a session that is not
/// live), which _feed_fold_session reads with `.get` outside build_feed's per-session guard.
fn fold_raises(entry: &Out) -> bool {
    matches!(member(entry, "asks"), Some(Out::Array(cards)) if cards.iter().any(|c| matches!(c, Out::Null)))
}

enum Outcome {
    KeyRaised(Raise),
    Hit(Out),
    Miss(Keyed, Vec<&'static str>),
}

/// build_feed's per-session loop over the memo (the module's header): each alive session's entry, served or derived,
/// in alive order, `Err` only for a session that is absent this build. Records each session's decision in the memo.
pub fn entries<'a>(b: &Build<'a>, memo: &RefCell<FeedMemo>) -> Vec<(String, Result<Out, Raise>)> {
    let mut m = memo.borrow_mut();
    let plant = m.plant;
    let mut bf = BuildFacts::new(b);
    m.decisions.clear();
    if let Some(h) = b.held {
        h.borrow_mut().build_begin();
    }
    let rows = b.snap.alive();
    let mut out = Vec::with_capacity(rows.len());
    for row in rows {
        let fsid = row.sid.as_deref().expect("required at load");
        let facts = b
            .snap
            .sessions
            .as_ref()
            .and_then(|s| s.get(fsid))
            .expect("validated: every alive row has session facts");
        let mut d = Decision { sid: fsid.to_string(), hit: None, miss: Vec::new(), raised: None };
        m.touch(fsid); // _feed_memo_get: a read is a use
        let outcome = {
            let ent = m.map.get(fsid);
            match session_key(b, &mut bf, row, facts, ent.map(|e| &e.entry), plant) {
                Err(r) => Outcome::KeyRaised(r),
                Ok(k) => match ent {
                    Some(e) if e.key == k.key => Outcome::Hit(e.entry.clone()),
                    _ => {
                        let labels = miss_labels(ent.map(|e| e.key.as_slice()), &k.key);
                        Outcome::Miss(k, labels)
                    }
                },
            }
        };
        let got: Result<Out, Raise> = match outcome {
            Outcome::KeyRaised(r) => Err(r),
            Outcome::Hit(entry) => {
                m.stats.hit += 1;
                d.hit = Some(true);
                Ok(entry)
            }
            Outcome::Miss(keyed, labels) => {
                m.stats.miss += 1;
                m.stats.derived += 1;
                d.hit = Some(false);
                d.miss = labels;
                let derived = {
                    let mm = &mut *m;
                    b.entry(row, &mut mm.table)
                };
                derived.and_then(|entry| {
                    let key = with_deps(b, &mut bf, keyed, Some(&entry), plant)?;
                    if !member(&entry, "faults").is_some_and(out_truthy) {
                        m.put(fsid, key, entry.clone());
                    }
                    Ok(entry)
                })
            }
        };
        let got = match got {
            Ok(e) => Ok(e),
            Err(r) => {
                m.stats.failed += 1;
                // the previous cards, served when the memo holds an entry that is a value (it stays under its old key);
                // else absent, and a held entry that cannot serve (a hidden session's null) is dropped
                let held = m.map.get(fsid).map(|e| &e.entry);
                let prev = held.filter(|e| !matches!(e, Out::Null)).filter(|_| plant != Some(Plant::RaiseAbsent)).cloned();
                d.raised = Some((r.exc, prev.is_some()));
                match prev {
                    Some(p) => Ok(p),
                    None => {
                        if held.is_some() {
                            m.drop_entry(fsid);
                        }
                        Err(r)
                    }
                }
            }
        };
        // build_feed folds each session right after its turn of the loop, and the fold raises on a null card in the
        // entry's asks (layer7.rs): the whole build fails there, so no later session's key, derivation or put runs, and
        // nothing after the loop (the forget) either. The fold below meets the same card and raises the same.
        let stop = got.as_ref().is_ok_and(fold_raises);
        m.decisions.push(d);
        out.push((fsid.to_string(), got));
        if stop {
            for r in &rows[out.len()..] {
                let sid = r.sid.clone().expect("required at load");
                m.decisions.push(Decision { sid, hit: None, miss: Vec::new(), raised: None });
            }
            return out;
        }
    }
    let alive: FxSet<&str> = rows.iter().filter_map(|r| r.sid.as_deref()).collect();
    if plant != Some(Plant::NoForget) {
        m.forget(&alive);
    }
    if let Some(h) = b.held {
        h.borrow_mut().forget(&alive);
    }
    out
}

// ───────────────────────────── refusals ─────────────────────────────

/// The shapes the memo's key would read differently from Python's key, or that break Python's replay harness before
/// any key runs; none is in either world. Every alive row names its `name`, `anchor` (string or null) and `transcript`
/// (the replay builds its path from it); the recorded facts have their shapes (a transcript identity null or a list led
/// by an int, a store identity a list of three, `capOpen` null or `{window, resetsAt}`, `hold` null or a dict); a live
/// row's `subagents` and `bgTasks` are lists or null (Python iterates any value and raises on a number); a recorded
/// `bgLive` row is a dict; and a session's own flags and the merged flags agree on `hideFromFeed` (the S2 replay's body
/// reads the first, Python's key the second; they differ only for a session with null flags and a peer entry of the
/// same sid that carries the flag).
pub fn validate(snap: &Snapshot) -> Result<(), String> {
    for (i, r) in snap.alive().iter().enumerate() {
        let at = format!("alive[{i}]");
        if r.name.is_none() || r.anchor.is_none() {
            return Err(format!("{at}: the replay reads its name and anchor keys"));
        }
        if r.transcript.is_none() {
            return Err(format!("{at}: no transcript path (the replay names the leaf by it)"));
        }
    }
    if let Some(kf) = snap.key_facts.as_ref().filter(|v| !v.is_null()) {
        let kf = kf.as_object().ok_or("keyFacts: not a dict")?;
        for (k, _) in kf.iter() {
            if !["files", "auth", "capOpen"].contains(&k.as_str()) {
                return Err(format!("keyFacts.{k}: not a fact the key reads"));
            }
        }
        if let Some(files) = kf.get("files").filter(|v| !v.is_null()) {
            let files = files.as_object().ok_or("keyFacts.files: not a dict")?;
            for (name, v) in files {
                if name.starts_with("transcript:")
                    && !v.is_null()
                    && !v.as_array().and_then(|a| a.first()).is_some_and(|x| x.as_i64().is_some())
                {
                    return Err(format!("keyFacts.files.{name}: a transcript identity is null or a list led by an int inode"));
                }
                if name.starts_with("store:") && v.as_array().is_none_or(|a| a.len() != 3) {
                    return Err(format!("keyFacts.files.{name}: a store identity is a list of three"));
                }
            }
        }
        if let Some(w) = kf.get("capOpen").filter(|v| !v.is_null()) {
            let w = w.as_object().ok_or("keyFacts.capOpen: not a dict")?;
            if !w.contains_key("window") || !w.contains_key("resetsAt") {
                return Err("keyFacts.capOpen: needs window and resetsAt".into());
            }
        }
    }
    let flags = |sid: &str| -> bool {
        let own = snap.sessions.as_ref().and_then(|m| m.get(sid)).and_then(|s| s.flags.as_ref()).filter(|f| !f.is_null());
        let peer = snap.peers.as_ref().and_then(|m| m.get(sid)).and_then(|p| p.flags.as_ref()).filter(|f| !f.is_null());
        own.or(peer).and_then(J::as_object).is_some_and(|f| f.get("hideFromFeed").is_some_and(py_truthy))
    };
    for (sid, s) in snap.sessions.iter().flat_map(|m| m.iter()) {
        let at = format!("sessions.{sid}");
        let own = s.flags.as_ref().and_then(J::as_object).is_some_and(|f| f.get("hideFromFeed").is_some_and(py_truthy));
        if own != flags(sid) {
            return Err(format!("{at}.flags: hideFromFeed reads one way from the session's flags and another merged"));
        }
        if let Some(kf) = s.key_facts.as_ref().filter(|v| !v.is_null()) {
            let kf = kf.as_object().ok_or_else(|| format!("{at}.keyFacts: not a dict"))?;
            const KNOWN: [&str; 10] =
                ["cut", "live", "watch", "postal", "reg", "anchors", "userWrite", "snapDone", "hold", "replayWrote"];
            for (k, _) in kf.iter() {
                if !KNOWN.contains(&k.as_str()) {
                    return Err(format!("{at}.keyFacts.{k}: not a fact the key reads"));
                }
            }
            if kf.get("hold").is_some_and(|h| !h.is_null() && !h.is_object()) {
                return Err(format!("{at}.keyFacts.hold: null or a dict"));
            }
        }
        if let Some(rows) = s.recorded.as_ref().and_then(|r| r.bg_live.as_ref()) {
            if rows.iter().any(|r| !r.is_object()) {
                return Err(format!("{at}.recorded.bgLive: a row that is not a dict"));
            }
        }
    }
    for (sid, r) in snap.live_map.iter().flat_map(|m| m.iter()) {
        for k in ["subagents", "bgTasks"] {
            if live_extra(r, k).is_some_and(|v| !v.is_null() && !v.is_array()) {
                return Err(format!("liveMap.{sid}.{k}: a list or null"));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn j(s: &str) -> J {
        serde_json::from_str(s).unwrap()
    }

    #[test]
    fn components_compare_as_python_compares_them() {
        assert_eq!(KV::Int(1), KV::Float(1.0), "1 == 1.0");
        assert_eq!(KV::Bool(true), KV::Int(1), "True == 1");
        assert_eq!(KV::Bool(false), KV::Float(-0.0), "False == -0.0");
        assert_ne!(KV::Int(1), KV::Str("1".into()));
        assert_ne!(KV::Tuple(vec![KV::Int(1)]), KV::List(vec![KV::Int(1)]), "(1,) != [1]");
        assert_eq!(KV::from_json(&j(r#"{"a": 1, "b": [2]}"#)), KV::from_json(&j(r#"{"b": [2.0], "a": true}"#)), "dicts unordered");
        assert_ne!(KV::from_json(&j(r#"{"a": 1}"#)), KV::from_json(&j(r#"{"a": 1, "b": 2}"#)));
        assert_ne!(KV::None, KV::Int(0));
        assert_eq!(KV::Int(9_007_199_254_740_993), KV::Int(9_007_199_254_740_993));
        assert_ne!(KV::Int(9_007_199_254_740_993), KV::Float(9_007_199_254_740_992.0), "an int and a float compare exactly");
        assert_ne!(unsettled(), KV::Tuple(vec![KV::Tuple(vec![KV::Str("unsettled".into())])]));
        assert_eq!(unsettled(), KV::Tuple(vec![KV::Str("unsettled".into())]));
    }

    #[test]
    fn misses_name_the_components_that_moved() {
        let a: Vec<KV> = (0..31).map(KV::Int).collect();
        let mut b = a.clone();
        assert!(miss_labels(Some(&a), &b).is_empty());
        b[10] = KV::Int(-1);
        b[0] = KV::Str("x".into());
        assert_eq!(miss_labels(Some(&a), &b), vec!["row", "transcript"], "sorted, as Python sorts them");
        assert_eq!(miss_labels(None, &b), vec!["cold"]);
        assert_eq!(miss_labels(Some(&a[..30]), &b), vec!["cold"], "a key of another length is cold");
        b[10] = KV::Float(10.0);
        b[0] = KV::Bool(false);
        assert!(miss_labels(Some(&a), &b).is_empty(), "a tag change alone moves nothing");
    }

    #[test]
    fn the_cache_sheds_the_least_recently_served() {
        let mut m = FeedMemo::new(30);
        let key = || vec![KV::None; 31];
        let e = |s: &str| Out::Str(s.into()); // "\"aaaaaaaaaa\"" is 12 bytes
        m.put("a", key(), e("aaaaaaaaaa"));
        m.put("b", key(), e("bbbbbbbbbb"));
        assert_eq!((m.len(), m.bytes()), (2, 24));
        m.touch("a"); // a is served, so b is now the oldest
        m.put("c", key(), e("cccccccccc"));
        assert_eq!(m.len(), 2);
        assert!(m.map.contains_key("a") && m.map.contains_key("c") && !m.map.contains_key("b"));
        assert_eq!(m.stats.evict, 1);
        m.put("d", key(), e(&"d".repeat(40)));
        assert_eq!(m.len(), 1, "an entry alone over the bound still serves, and sheds every other");
        let alive: FxSet<&str> = FxSet::default();
        m.forget(&alive);
        assert_eq!((m.len(), m.bytes()), (0, 0));
    }
}
