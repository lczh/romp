//! Layer 6 of rust/notes/s2-design.md section 2, "The body and its closures": kernel._feed_session_entry, the per-session
//! entry build_feed memoizes, with its closures _note_peers, _subtree, _closure_done, _fsubmax, _closure_blocked,
//! _block_check_floor, _brief_landing and flatten, line for line over layers 1 to 5, plus the three small builders the
//! body calls that no earlier layer ports: _card_warn_rows (the debug-mode join), _blocked_placeholder and
//! _awaiting_card. The answer is the entry dict itself (asks, working, awaiting, bgServices, servingFolds, heal, hidden,
//! cold, peers, reads, faults), written through romp-json, so the comparison is byte for byte against Python's memo
//! entry string (`json.dumps(entry)`; the S2 dump keeps one per session in `oracle/entries/`).
//!
//! The body reads, besides the session's tree and store, what `_feed_session_key` hands it through ctx (taken here as
//! the S2 replay's key takes it: the tree, `_session_working` over it, the recorded `_interrupting` answer, and the
//! recorded `_closer_pending` answer under the key's gate) and the build's board reads (the cleared set, the debug rows,
//! the wait graph, the stalls, the judge-auth latch, the judges in flight, the nudge records and times). The helpers
//! whose bodies read state the spike does not port are answered from the snapshot's recorded answers, by sid
//! (dump_world.py RECORDED); an answer the body needs that was never recorded raises `NotRecorded`, as the replay's
//! harness raises, and that session's entry is the raise. Two of them sit inside a `try` in Python, which swallows the
//! raise: `_interrupted_by` (the interrupted badge reads False) and the live backend's `current_ask` in
//! _blocked_placeholder (no question). Only the call is inside that `try`: the ask's `question` and `header` are read
//! after it, so a truthy one that is not a string raises AttributeError and fails the entry, as here.
//!
//! What a naive port would get wrong, each pinned by the edge cases (tools/rust-spike/dump_layer6.py --edge-cases):
//! - _node_anchor_uuids runs inside flatten, once per flattened node in flatten's order (a card top, then its children
//!   sorted by `_fsubmax` descending, stably, each recursively before the next), not over the store: two flattened nodes
//!   sharing an id, or a node never flattened (hidden, cleared, a pure-delegation top, a cycle member, an orphan),
//!   decide what a later cold lookup of the same id serves, in this session or a later one (`AnchorTable` lives for the
//!   build and is carried across the sessions in alive order).
//! - `max` keeps the first of equal values and its int/float tag: _fsubmax (`mt`, else `t`, else 0, against each
//!   child), a completed card's `max([settledAt or 0] + dts)`, a blocked card's `max(bts)`, `max(disp_t, followupAt or
//!   0)`, the delegation's `max([t or 0 ...] or [0]) or None`; `min` keeps the first too (_block_check_floor). `int()`
//!   truncates toward zero (`blockCheckT`, the nudge count).
//! - `x or y` and `.get(k, d)`: a row's `mt` is `nd.get("mt", nd["t"])` (a present 0 stays 0, where `mt or t` gives t),
//!   `_born or None` (an empty born dict is null), `relayCarried or None`, `followupAt or None`, `(q or None)` on the
//!   landing's quote while the card's own `summaryQuote` passes through raw, `_stamp_since = awaitingAt or None`,
//!   `jerr.get("note") or ""`. `_sa_u is None and ps is None` serves the raw citation only when there is no parse.
//!   (_brief_landing's tiers test `u is None`, which agrees with `not u` here: every tier yields a truthy uuid or None,
//!   since _text_atoms admits only atoms with a truthy uuid; the edge set's "" reply shows it.)
//! - Order: the entry's `reads` has `usage` before `nudges` when the api error is set; the card literal's conditional
//!   keys (`handoffTo`, `delegTracked`, `satellite`) sit between `origin` and `followupPending`; `peers` is sorted; the
//!   card loop runs over `children[None]` after the floor un-nest, which may append a top at the end; a healed top's
//!   un-nest also removes it from its host's list.
//! - `isinstance(n, int)` is true of a bool, so an awaiting count of `true` is kept, where any other non-int is None.
//! - Python's `str.upper()` of the awaiting card's first code point is the full case mapping at Python's Unicode
//!   version, 15.0 (`ß` gives `SS`, and `ɤ` stays `ɤ`): `pystr::upper` reads a table generated from Python, since
//!   Rust's `char::to_uppercase` follows the toolchain's Unicode 18.0 and differs on 76 code points.
//! - A body read that raises fails the whole entry: `nodes[nid]["t"]` under the wait graph, a row's `nd["text"]` and
//!   `nd["t"]` (after its anchors were written and its landing resolved), a stall record's `why`, a session-awaiting
//!   answer's `why` and `kind`, the current ask's `question` or `header` that is truthy and not a string (`.strip()`),
//!   and every recorded answer the body reaches unrecorded.
//!
//! Left out, as in layer 5: _SUMMARY_ANCHOR_MEMO (SPIKE.md, "What layer 6 needs": a pure cache within one build, so the
//! spike's fresh-process comparisons never meet the memo's seam staleness). The held aggregate (_FEED_SEG_HELD) is
//! used only when a build is given a table (`Build::with_held`, S6: layer3::SegHeld); by default every turn is walked.
//! build_feed's per-session memo is used only when a build is given one (`Build::with_memo`, S6.1: memo.rs); by default
//! every session derives, with a fresh anchor table. `lg.mark_refused` is a side effect on the login registry with no part in the entry, so it is not
//! ported. `faults` is the walk's lazy `seg_turn` faults, always 0 on a tree the loader admits.

use std::cell::RefCell;
use std::cmp::Ordering;
use crate::fxhash::{FxMap as HashMap, FxSet as HashSet};

use romp_json::Value as Out;
use serde_json::Value as J;

use crate::layer1;
use crate::layer2::{self, Raise};
use crate::layer3::{self, py_max, ClosedPart, FeedSegWalk, SegHeld};
use crate::layer4::{self, AnchorTable, Answered, Children, Ctx, Healed, Identity, Names, Ns, PeerWorld, WalkTrig, WalkWork};
use crate::layer5::{self, PromptIndex, ProvisionalIn};
use crate::pystr;
use crate::record::{py_truthy, Num, OMap, ToJson};
use crate::snapshot::{AliveRow, Board, Born, LiveRow, Node, NudgeRecord, SessionFacts, Snapshot, Store};
use crate::tree::{Tree, Turn};

/// What an unrecorded helper answer raises (the replay harness's DumpError, named as layer 5 names it).
pub const NOT_RECORDED: &str = layer5::NOT_RECORDED;
/// kernel._NEEDS_INPUT_STATES: the live states that floor a card under BLOCKED.
pub const NEEDS_INPUT_STATES: [&str; 2] = ["permission", "picker"];
/// jd.WHY_IN_FLIGHT: the stall reasons that present as the Analyzing swirl, never the stalled chip.
pub const WHY_IN_FLIGHT: [&str; 4] = [
    "romp's own review of this session is mid-flight",
    "a judge pass is mid-flight",
    "a judge has ruled on a turn that hasn't finished yet",
    "an unblock is awaiting the closer's next word on this goal",
];
/// _card_warn_rows' default cap.
pub const WARN_CAP: usize = 20;
/// The diary sources whose event after a failed nudge's block retires the nudge-failed chip.
pub const STORY_SOURCES: [&str; 6] = ["planner", "closer", "courier", "user", "agent", "unblocker"];

/// One deliberate mistake, for `romp-spike layer6 --plant`: the comparison must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// _node_anchor_uuids is called once per node in store order (layer 4's harness order) before the card loop, and
    /// flatten reads those answers, instead of calling it from flatten in flatten's order.
    AnchorStoreOrder,
    /// _fsubmax's `max` keeps the last of equal values (a child's 5.0 over the node's 5).
    FsubmaxLast,
    /// A row's mt reads `mt or t` instead of `nd.get("mt", nd["t"])`.
    MtOr,
    /// _brief_landing's newest trail segment keeps the first of equal times (`t > skt`).
    LandFirstEqual,
    /// The entry's peers in first-read order, not sorted.
    PeersUnsorted,
    /// An empty born record ships as `{}` (no `_born or None`).
    BornEmptyKept,
}

impl Plant {
    pub const ALL: [Plant; 6] = [
        Plant::AnchorStoreOrder,
        Plant::FsubmaxLast,
        Plant::MtOr,
        Plant::LandFirstEqual,
        Plant::PeersUnsorted,
        Plant::BornEmptyKept,
    ];

    pub fn parse(name: &str) -> Option<Plant> {
        Plant::ALL.into_iter().find(|p| p.name() == name)
    }

    pub fn name(self) -> &'static str {
        match self {
            Plant::AnchorStoreOrder => "anchor-store-order",
            Plant::FsubmaxLast => "fsubmax-last",
            Plant::MtOr => "mt-or",
            Plant::LandFirstEqual => "land-first-equal",
            Plant::PeersUnsorted => "peers-unsorted",
            Plant::BornEmptyKept => "born-empty-kept",
        }
    }
}

// ───────────────────────────── small Python readings ─────────────────────────────

fn s2(x: &Option<Option<String>>) -> Option<&str> {
    x.as_ref().and_then(|v| v.as_deref())
}

fn n2(x: &Option<Option<Num>>) -> Option<Num> {
    x.and_then(|v| v)
}

fn flag(x: &Option<bool>) -> bool {
    *x == Some(true)
}

fn tj(x: &Option<J>) -> bool {
    x.as_ref().is_some_and(py_truthy)
}

/// `d.get(k)` of an untyped key, as JSON (absent and null alike are null).
fn jget(x: &Option<J>) -> Out {
    x.as_ref().map_or(Out::Null, ToJson::to_json)
}

/// `d.get(k) or None`.
fn jor_none(x: &Option<J>) -> Out {
    x.as_ref().filter(|v| py_truthy(v)).map_or(Out::Null, ToJson::to_json)
}

fn jv(v: Option<&J>) -> Out {
    v.map_or(Out::Null, ToJson::to_json)
}

fn truthy_str(s: Option<&str>) -> bool {
    s.is_some_and(|s| !s.is_empty())
}

/// `s[:n]` in code points.
fn head(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map_or(s, |(i, _)| &s[..i])
}

fn obj(members: Vec<(&str, Out)>) -> Out {
    Out::Object(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn sv(x: Option<&str>) -> Out {
    x.map_or(Out::Null, |x| Out::Str(x.to_string()))
}

fn int_out(n: usize) -> Out {
    Out::Int(i64::try_from(n).expect("a count fits in i64"))
}

/// `int(x)` of a number as a number that compares exactly: an int as is, a float truncated toward zero (an int when it
/// fits i64, else the integral float, which compares as the int would).
fn py_int(n: Num) -> Num {
    match n {
        Num::Int(i) => Num::Int(i),
        Num::Float(f) => {
            let t = f.trunc();
            if t.abs() < 9_223_372_036_854_775_808.0 { Num::Int(t as i64) } else { Num::Float(t) }
        }
    }
}

/// `int(x)` written as Python prints the int.
fn py_int_out(n: Num) -> Out {
    match py_int(n) {
        Num::Int(i) => Out::Int(i),
        Num::Float(f) => Out::BigInt(Num::Float(f).percent_d()),
    }
}

/// Python's `min(a, b)`: `b` only when it is smaller, so the first of equal values is kept.
fn py_min(a: Num, b: Num) -> Num {
    if b.py_cmp(a) == Ordering::Less { b } else { a }
}

/// `x or 0` of an optional number: the number when truthy, else the literal int 0.
fn or0(x: Option<Num>) -> Num {
    x.filter(|n| n.truthy()).unwrap_or(Num::Int(0))
}

/// A JSON number as a `Num` (None for anything else; a bool is not a number here).
fn jnum(v: &J) -> Option<Num> {
    match v {
        J::Number(n) => n.as_i64().map(Num::Int).or_else(|| n.as_f64().map(Num::Float)),
        _ => None,
    }
}

fn key_error(k: &str) -> Raise {
    Raise { exc: "KeyError", arg: format!("'{k}'") }
}

/// The replay harness's raise for a helper answer it never recorded: `name(sid)` or `name(sid,sub)`.
pub fn not_recorded(name: &str, sid: &str, sub: Option<&str>) -> Raise {
    Raise { exc: NOT_RECORDED, arg: format!("{name}({sid}{})", sub.map_or(String::new(), |s| format!(",{s}"))) }
}

/// `why[:1].upper() + why[1:]`: Python's `str.upper()` of the first code point (`pystr::upper`, Python's own table, not
/// Rust's `char::to_uppercase`, whose Unicode version is the toolchain's), then the rest.
fn cap_first(s: &str) -> String {
    let first = head(s, 1);
    pystr::upper(first) + &s[first.len()..]
}

/// Python's type name of a JSON value, as an AttributeError or TypeError message names it.
fn py_type_name(v: &J) -> &'static str {
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

/// `(v or "").strip()`: "" for an absent or falsy value, a string stripped the Python way, and for any other truthy
/// value the AttributeError Python raises (`'int' object has no attribute 'strip'`).
fn or_empty_strip(v: Option<&J>) -> Result<&str, Raise> {
    match v.filter(|v| py_truthy(v)) {
        None => Ok(""),
        Some(J::String(s)) => Ok(pystr::strip(s)),
        Some(other) => {
            Err(Raise { exc: "AttributeError", arg: format!("'{}' object has no attribute 'strip'", py_type_name(other)) })
        }
    }
}

/// `str.rstrip()` (Python's whitespace).
fn rstrip(s: &str) -> &str {
    s.trim_end_matches(pystr::is_space)
}

/// `str(v)` for the values the loader admits where the body calls `str()` on a recorded answer.
fn py_str(v: &J) -> String {
    match v {
        J::String(s) => s.clone(),
        other => romp_json::dumps(&other.to_json()),
    }
}

// ───────────────────────────── the build's inputs ─────────────────────────────

/// What one build reads across sessions, prepared from a snapshot and its trees.
pub struct Build<'a> {
    pub snap: &'a Snapshot,
    pub(crate) trees: HashMap<&'a str, &'a Tree>,
    pub names: Names,
    pub(crate) world: PeerWorld,
    pub(crate) now: Num,
    pub(crate) cleared: HashSet<String>,
    /// jd.load_goals_shared_or_fault's view: the sessions' stores, then the peers' over them.
    pub(crate) stores: HashMap<&'a str, Option<&'a Store>>,
    /// kernel._session_flags(): the peers' flags, then the sessions' over them (a null entry skipped).
    pub(crate) flags: HashMap<&'a str, &'a J>,
    pub(crate) downtime: Vec<(Num, Num)>,
    pub(crate) board: &'a Board,
    pub(crate) live_map: &'a OMap<LiveRow>,
    pub plant: Option<Plant>,
    /// S6's cheap bound (`Build::with_closed`): each session's closed part, folded ahead and taken by its derivation,
    /// whose walk then folds only the open turn. None: every turn is walked, as Python's walk with nothing held.
    pub(crate) closed: Option<&'a RefCell<HashMap<String, ClosedPart<'a>>>>,
    /// S6's held aggregate (`Build::with_held`): the table kept across builds (kernel._FEED_SEG_HELD). None: nothing is
    /// held, every turn is walked.
    pub(crate) held: Option<&'a RefCell<SegHeld<'a>>>,
    /// S6.1's per-session memo (`Build::with_memo`): entries served while their key stands, kept across builds by the
    /// caller, as Python's _feed_memo is (memo.rs). None: every session derives.
    memo: Option<&'a RefCell<crate::memo::FeedMemo>>,
}

/// The session's walk inputs as Derive::new reads them (the replay key's hide gate, the tree's turns, the store unless
/// it faulted) and whether the replay key hands a leaf head (`leafOk`), without which Python's walk holds nothing.
fn walk_inputs<'a>(b: &Build<'a>, fsid: &str, facts: &'a SessionFacts) -> (Option<&'a [Turn]>, Option<&'a Store>, bool) {
    let hide = facts.flags.as_ref().and_then(J::as_object).is_some_and(|f| f.get("hideFromFeed").is_some_and(py_truthy));
    let tree = if hide { None } else { b.trees.get(fsid).copied() };
    let turns: Option<&'a [Turn]> = tree.map(|t| t.turns.as_deref().unwrap_or_default());
    let store = match facts.store.as_ref().expect("validated: every session has a store key") {
        Some(st) => Some(st),
        None => None,
    };
    (turns, store, crate::memo::leaf_inode(b.snap, fsid, facts).is_some())
}

static EMPTY_BOARD: std::sync::LazyLock<Board> = std::sync::LazyLock::new(Board::default);
static EMPTY_LIVE: std::sync::LazyLock<OMap<LiveRow>> = std::sync::LazyLock::new(OMap::default);
static EMPTY_STORE: std::sync::LazyLock<Store> = std::sync::LazyLock::new(Store::default);
static EMPTY_STATUS: std::sync::LazyLock<OMap<String>> = std::sync::LazyLock::new(OMap::default);
static EMPTY_NODES: std::sync::LazyLock<OMap<Node>> = std::sync::LazyLock::new(OMap::default);

impl<'a> Build<'a> {
    pub fn new(snap: &'a Snapshot, trees: &'a [(String, Option<Tree>)], plant: Option<Plant>) -> Build<'a> {
        Build::from_parts(snap, trees.iter().filter_map(|(s, t)| t.as_ref().map(|t| (s.as_str(), t))), plant)
    }

    /// A build over a snapshot and trees held elsewhere (sid to tree; a session with no tree is left out), as the memo's
    /// change sequences hold them, re-reading only the trees a step touched (romp-spike memo).
    pub fn from_parts(
        snap: &'a Snapshot,
        trees: impl IntoIterator<Item = (&'a str, &'a Tree)>,
        plant: Option<Plant>,
    ) -> Build<'a> {
        let mut stores = HashMap::default();
        let mut flags = HashMap::default();
        for (psid, p) in snap.peers.iter().flat_map(OMap::iter) {
            if let Some(f) = p.flags.as_ref().filter(|f| !f.is_null()) {
                flags.insert(psid.as_str(), f);
            }
        }
        for (sid, s) in snap.sessions.iter().flat_map(OMap::iter) {
            stores.insert(sid.as_str(), s.store.as_ref().and_then(Option::as_ref));
            if let Some(f) = s.flags.as_ref().filter(|f| !f.is_null()) {
                flags.insert(sid.as_str(), f);
            }
        }
        for (psid, p) in snap.peers.iter().flat_map(OMap::iter) {
            stores.insert(psid.as_str(), p.store.as_ref().and_then(Option::as_ref));
        }
        let board = snap.board.as_ref().unwrap_or(&EMPTY_BOARD);
        Build {
            snap,
            trees: trees.into_iter().collect(),
            names: Names::from_snapshot(snap),
            world: PeerWorld::default(),
            now: snap.now(),
            cleared: board.cleared.clone().unwrap_or_default().into_iter().collect(),
            stores,
            flags,
            downtime: board.downtime.clone().unwrap_or_default(),
            board,
            live_map: snap.live_map.as_ref().unwrap_or(&EMPTY_LIVE),
            plant,
            closed: None,
            held: None,
            memo: None,
        }
    }

    /// The build with the memo: each alive session is served its previous entry while its key stands and derives
    /// otherwise (memo.rs), the anchor table carried in the memo across builds.
    pub fn with_memo(mut self, memo: &'a RefCell<crate::memo::FeedMemo>) -> Build<'a> {
        self.memo = Some(memo);
        self
    }

    /// The build with the held aggregate: each derivation's walk is served from and stores into `held`, kept across
    /// builds by the caller, as Python's _FEED_SEG_HELD is (the replay key's leaf head is the alive row's transcript
    /// path when `leafOk`, as the S2 replay hands `(path, 0)`).
    pub fn with_held(mut self, held: &'a RefCell<SegHeld<'a>>) -> Build<'a> {
        self.held = Some(held);
        self
    }

    /// The build with S6's cheap bound: each derivation takes its session's closed part out of `closed` (filled by
    /// `closed_parts`, outside the timed region) and walks only the open turn. A session with no part walks every turn.
    pub fn with_closed(mut self, closed: &'a RefCell<HashMap<String, ClosedPart<'a>>>) -> Build<'a> {
        self.closed = Some(closed);
        self
    }

    /// Every alive session's closed part (layer3::closed_part over the turns and store its derivation walks), for the
    /// sessions whose walk Python would hold: a leaf head (`leafOk`) and at least one closed turn that folds.
    pub fn closed_parts(&self) -> HashMap<String, ClosedPart<'a>> {
        let mut out = HashMap::default();
        for row in self.snap.alive() {
            let fsid = row.sid.as_deref().expect("required at load");
            let Some(facts) = self.snap.sessions.as_ref().and_then(|m| m.get(fsid)) else { continue };
            let (turns, store, leaf_ok) = walk_inputs(self, fsid, facts);
            if !leaf_ok {
                continue;
            }
            if let Some(c) = layer3::closed_part(turns, store) {
                out.insert(fsid.to_string(), c);
            }
        }
        out
    }

    fn ctx(&self) -> Ctx<'_> {
        Ctx { names: &self.names, world: &self.world, plant: None }
    }

    /// `_session_flag(sid, flag)`.
    pub(crate) fn session_flag(&self, sid: &str, flag: &str) -> bool {
        self.flags.get(sid).and_then(|f| f.as_object()).is_some_and(|f| f.get(flag).is_some_and(py_truthy))
    }

    /// `_session_flag_raw(sid, flag)`: the flag's stored value as a bool, None when the session's flags are not a dict
    /// or hold no value (absent or null) for it. The tri-state the bells read (layer 7).
    pub fn session_flag_raw(&self, sid: &str, flag: &str) -> Option<bool> {
        self.flags.get(sid).and_then(|f| f.as_object()).and_then(|f| f.get(flag)).filter(|v| !v.is_null()).map(py_truthy)
    }

    /// The build's cleared set (`_cleared_ids_display()`'s ids), which the body and layer 7 test for membership.
    pub fn cleared(&self) -> &HashSet<String> {
        &self.cleared
    }

    /// The build clock.
    pub fn now(&self) -> Num {
        self.now
    }

    fn wmap(&self) -> Option<&'a OMap<J>> {
        self.board.wmap.as_ref()
    }

    /// The board's debug rows when debug mode is on (`_judge_error_rows(now) if jd._debug_mode() else None`).
    fn dbg_rows(&self) -> Option<&'a [J]> {
        if flag(&self.board.debug) {
            Some(self.board.debug_rows.as_ref().and_then(J::as_array).map_or(&[], Vec::as_slice))
        } else {
            None
        }
    }

    /// Every alive session's entry, in alive order, with one anchor table across them (_node_anchor_last, cleared
    /// before the build). Each entry is `Ok(the entry)` or the raise that failed it.
    pub fn entries(&self) -> Vec<(String, Result<Out, Raise>)> {
        if let Some(m) = self.memo {
            return crate::memo::entries(self, m);
        }
        let mut table = AnchorTable::default();
        if let Some(h) = self.held {
            h.borrow_mut().build_begin(); // _feed_seg_build_begin, before the loop
        }
        let out: Vec<(String, Result<Out, Raise>)> = self
            .snap
            .alive()
            .iter()
            .map(|row| {
                let sid = row.sid.clone().expect("required at load");
                let got = self.entry(row, &mut table);
                (sid, got)
            })
            .collect();
        if let Some(h) = self.held {
            // _feed_memo_forget's _feed_seg_forget, after the loop
            let alive: HashSet<&str> = self.snap.alive().iter().filter_map(|r| r.sid.as_deref()).collect();
            h.borrow_mut().forget(&alive);
        }
        out
    }

    /// kernel._feed_session_entry(s, ctx) for one alive row, with ctx as the replay's key hands it.
    pub fn entry(&self, row: &'a AliveRow, table: &mut AnchorTable) -> Result<Out, Raise> {
        let fsid = row.sid.as_deref().expect("required at load");
        let facts = self
            .snap
            .sessions
            .as_ref()
            .and_then(|m| m.get(fsid))
            .expect("validated: every alive row has session facts");
        Derive::new(self, row, fsid, facts)?.run(table)
    }
}

// ───────────────────────────── one derivation ─────────────────────────────

/// The recorded answers, read the way the replay harness answers them.
struct Rec<'a> {
    sid: &'a str,
    facts: &'a SessionFacts,
}

impl<'a> Rec<'a> {
    fn rec(&self) -> &'a crate::snapshot::Recorded {
        self.facts.recorded.as_ref().expect("required at load")
    }

    fn get<T>(&self, name: &str, v: &'a Option<T>) -> Result<&'a T, Raise> {
        v.as_ref().ok_or_else(|| not_recorded(name, self.sid, None))
    }
}

/// A card or row's anchors from the store-order plant: per node index in store order.
type PlantedAnchors = HashMap<usize, (Option<String>, Option<String>)>;

struct Derive<'a, 'b> {
    b: &'b Build<'a>,
    fsid: &'a str,
    name: Out,
    color: Out,
    rec: Rec<'a>,
    tree: Option<&'a Tree>,
    who_working: bool,
    store: &'a Store,
    store_faulted: bool,
    ns: Ns<'a>,
    status: &'a OMap<String>,
    children: Children,
    agent_open: HashSet<String>,
    healed: Healed,
    parked: HashMap<String, usize>,
    walk: FeedSegWalk<'a>,
    row: &'a AliveRow,
    peers_read: Vec<String>,
    cdone: HashMap<String, bool>,
    fsmemo: HashMap<String, Num>,
    qmemo: HashMap<String, bool>,
    bcmemo: HashMap<String, Option<Num>>,
    sub_memo: HashMap<String, Vec<String>>,
    land_memo: HashMap<(String, bool, String), (Option<String>, Option<J>)>,
    planted: Option<PlantedAnchors>,
    // what the key computed
    interrupting: bool,
    closer: bool,
    live_row: Option<&'a LiveRow>,
}

/// `bool(row)`: a live row is falsy when it has no keys.
fn row_truthy(r: Option<&LiveRow>) -> bool {
    r.is_some_and(|r| !r.order.is_empty())
}

impl<'a, 'b> Derive<'a, 'b> {
    fn new(b: &'b Build<'a>, row: &'a AliveRow, fsid: &'a str, facts: &'a SessionFacts) -> Result<Self, Raise> {
        let rec = Rec { sid: fsid, facts };
        let tm = b.live_map.get(fsid);
        // the replay's key: hide from the session's own flags, the tree unless hidden, _session_working over it,
        // the recorded _interrupting answer, the store, and _closer_pending under the key's gate
        let hide = facts.flags.as_ref().and_then(J::as_object).is_some_and(|f| f.get("hideFromFeed").is_some_and(py_truthy));
        let tree = if hide { None } else { b.trees.get(fsid).copied() };
        let turns: Option<&'a [Turn]> = tree.map(|t| t.turns.as_deref().unwrap_or_default());
        let who = turns.is_some_and(|t| layer3::session_working(t, &b.downtime));
        let interrupting = if hide { false } else { *rec.get("_interrupting", &rec.rec().interrupting)? };
        let jact = b.board.jactive.as_ref().is_some_and(|j| j.iter().any(|s| s == fsid));
        let closer = !hide
            && tm.is_some()
            && tree.is_some()
            && !who
            && !jact
            && *rec.get("_closer_pending", &rec.rec().closer_pending)?;
        let (store, store_faulted) = match facts.store.as_ref().expect("validated: every session has a store key") {
            Some(st) => (st, false),
            None => (&*EMPTY_STORE, true),
        };
        let nodes = store.nodes.as_ref().unwrap_or(&EMPTY_NODES);
        let status = store.status.as_ref().unwrap_or(&EMPTY_STATUS);
        let _ = turns;
        Ok(Derive {
            b,
            fsid,
            name: row.name.as_ref().map_or(Out::Null, ToJson::to_json),
            color: layer4::color_json(b.names.name_color(fsid)),
            rec,
            tree,
            who_working: who,
            store,
            store_faulted,
            ns: Ns::new(nodes),
            status,
            children: Children::default(),
            agent_open: HashSet::default(),
            healed: OMap::default(),
            parked: HashMap::default(),
            // the anchor walk runs where the body runs it (`walk_now`, after the retrying read): a read that raises
            // before it leaves the held table untouched, as in Python
            walk: layer3::feed_seg_walk_from(None, None, None),
            row,
            peers_read: Vec::new(),
            cdone: HashMap::default(),
            fsmemo: HashMap::default(),
            qmemo: HashMap::default(),
            bcmemo: HashMap::default(),
            sub_memo: HashMap::default(),
            land_memo: HashMap::default(),
            planted: None,
            interrupting,
            closer,
            live_row: tm,
        })
    }

    /// `_feed_seg_walk(fsid, ctx["leaf_head"], ps["turns"] if ps else None, store)`, at the body's point: over the held
    /// table when the build has one (the leaf head is the transcript's path and inode from the key's stat, None when it
    /// failed: memo.rs), else every turn (or the cheap bound's closed part).
    fn walk_now(&self) -> FeedSegWalk<'a> {
        let b = self.b;
        let turns: Option<&'a [Turn]> = self.tree.map(|t| t.turns.as_deref().unwrap_or_default());
        let walk_store = if self.store_faulted { None } else { Some(self.store) };
        match b.held {
            Some(held) => {
                let head = crate::memo::leaf_inode(b.snap, self.fsid, self.rec.facts)
                    .map(|ino| (self.row.transcript.as_deref().unwrap_or(""), ino));
                held.borrow_mut().walk(self.fsid, head, turns, walk_store)
            }
            None => {
                let closed = b.closed.and_then(|c| c.borrow_mut().remove(self.fsid));
                layer3::feed_seg_walk_from(turns, walk_store, closed)
            }
        }
    }

    fn note_peer(&mut self, p: &str) {
        if !p.is_empty() && !self.peers_read.iter().any(|x| x == p) {
            self.peers_read.push(p.to_string());
        }
    }

    /// `_note_peers(idents)`: identity dicts or bare sids, each truthy sid added.
    fn note_peers_json(&mut self, idents: Option<&J>) {
        let Some(J::Array(items)) = idents.filter(|v| py_truthy(v)) else { return };
        for d in items {
            let p = match d {
                J::Object(m) => m.get("sid").cloned().unwrap_or(J::Null),
                other => other.clone(),
            };
            if py_truthy(&p) {
                self.note_peer(&py_str(&p));
            }
        }
    }

    fn note_identities(&mut self, idents: Option<&[Identity]>) {
        for d in idents.unwrap_or_default() {
            let sid = d.sid.clone();
            self.note_peer(&sid);
        }
    }

    // ───────────── the closures ─────────────

    fn node(&self, nid: &str) -> &'a Node {
        self.ns.get(nid).expect("a children id is a node")
    }

    /// _subtree(root): every id at or under root, pre-order by a stack, memoized.
    fn subtree(&mut self, root: &str) -> Result<Vec<String>, Raise> {
        if let Some(got) = self.sub_memo.get(root) {
            return Ok(got.clone());
        }
        let got = layer4::subtree(&self.children, root)
            .map_err(|_| Raise { exc: "Loops", arg: format!("_subtree({root}) meets a node twice") })?;
        self.sub_memo.insert(root.to_string(), got.clone());
        Ok(got)
    }

    fn closure_done(&mut self, nid: &str) -> bool {
        if let Some(&v) = self.cdone.get(nid) {
            return v;
        }
        let Some(nd) = self.ns.get(nid) else {
            self.cdone.insert(nid.to_string(), false);
            return false;
        };
        let mut res = flag(&nd.node_complete);
        if !res && tj(&nd.umbrella) {
            let kids: Vec<String> =
                self.children.get(Some(nid)).iter().filter(|c| !flag(&self.node(c).cleared)).cloned().collect();
            res = !kids.is_empty() && kids.iter().all(|c| self.closure_done(c));
        }
        self.cdone.insert(nid.to_string(), res);
        res
    }

    fn fsubmax(&mut self, cid: &str) -> Num {
        if let Some(&v) = self.fsmemo.get(cid) {
            return v;
        }
        let mut m = match self.ns.get(cid) {
            Some(cn) => cn.mt.or(cn.t).unwrap_or(Num::Int(0)),
            None => Num::Int(0),
        };
        let kids: Vec<String> = self.children.get(Some(cid)).to_vec();
        for k in kids {
            let c = self.fsubmax(&k);
            m = if self.b.plant == Some(Plant::FsubmaxLast) && c.py_cmp(m) != Ordering::Less { c } else { py_max(m, c) };
        }
        self.fsmemo.insert(cid.to_string(), m);
        m
    }

    fn closure_blocked(&mut self, nid: &str) -> bool {
        if let Some(&v) = self.qmemo.get(nid) {
            return v;
        }
        let nd = self.ns.get(nid);
        let stop = match nd {
            None => true,
            Some(nd) => flag(&nd.cleared) || (self.closure_done(nid) && !self.agent_open.contains(nid)),
        };
        if stop {
            self.qmemo.insert(nid.to_string(), false);
            return false;
        }
        let nd = nd.expect("checked");
        let res = flag(&nd.blocked) || {
            let kids: Vec<String> = self.children.get(Some(nid)).to_vec();
            kids.iter().any(|c| self.closure_blocked(c))
        };
        self.qmemo.insert(nid.to_string(), res);
        res
    }

    fn block_check_floor(&mut self, nid: &str) -> Option<Num> {
        if let Some(&v) = self.bcmemo.get(nid) {
            return v;
        }
        let nd = self.ns.get(nid);
        let stop = match nd {
            None => true,
            Some(nd) => flag(&nd.cleared) || (self.closure_done(nid) && !self.agent_open.contains(nid)),
        };
        if stop {
            self.bcmemo.insert(nid.to_string(), None);
            return None;
        }
        let nd = nd.expect("checked");
        let mut vals: Vec<Num> = if flag(&nd.blocked) { vec![py_int(or0(n2(&nd.block_check_t)))] } else { Vec::new() };
        let kids: Vec<String> = self.children.get(Some(nid)).to_vec();
        for c in kids {
            if let Some(v) = self.block_check_floor(&c) {
                vals.push(v);
            }
        }
        let res = vals.into_iter().reduce(py_min);
        self.bcmemo.insert(nid.to_string(), res);
        res
    }

    fn seg_best(&self, k: &str) -> (Option<&'a str>, bool, Num) {
        match self.walk.maps.segs.get(k) {
            Some(e) => (e.best.uuid, e.best.substantive, e.best.t),
            None => (None, false, Num::Int(0)),
        }
    }

    fn trail(&self, x: &str) -> &'a [String] {
        self.node(x).trail.as_deref().unwrap_or_default()
    }

    /// _brief_landing(nid, completed, line): (uuid, quote), one resolve per (nid, completed, line) per derivation.
    fn brief_landing(&mut self, nid: &str, completed: bool, line: Option<&str>) -> Result<(Option<String>, Option<J>), Raise> {
        let mk = (nid.to_string(), completed, line.unwrap_or("").to_string());
        if let Some(got) = self.land_memo.get(&mk) {
            return Ok(got.clone());
        }
        let nd = self.node(nid);
        let sub = self.subtree(nid)?;
        let cited = s2(&nd.summary_anchor);
        let (mut u, mut q): (Option<String>, Option<J>) = (None, None);
        if completed {
            let mut tail: Option<(Num, &str)> = None;
            for x in &sub {
                let tr = self.trail(x);
                if let Some(last) = tr.last() {
                    let (tu, tsub, tt) = self.seg_best(&layer1::kernel_seg_key(last));
                    if let Some(tu) = tu.filter(|t| !t.is_empty())
                        && tsub
                        && tail.is_none_or(|(t0, _)| tt.py_cmp(t0) == Ordering::Greater)
                    {
                        tail = Some((tt, tu));
                    }
                }
            }
            if let Some((_, tu)) = tail {
                u = Some(tu.to_string());
            }
        }
        if u.is_none()
            && let Some(c) = cited.filter(|c| !c.is_empty())
            && self.walk.maps.cite.contains(c)
        {
            let trails: Vec<Option<&[String]>> = sub.iter().map(|x| self.node(x).trail.as_deref()).collect();
            let seg_t = layer5::walk_seg_t(&self.walk.maps);
            if !layer5::summary_outrun(nd, &trails, &seg_t) {
                u = Some(c.to_string());
                q = nd.summary_quote.clone();
            }
        }
        let (mut sk, mut skt): (Option<String>, Num) = (None, Num::Int(-1));
        let line_t = truthy_str(line);
        if line_t {
            for x in &sub {
                for s in self.trail(x) {
                    let k = layer1::kernel_seg_key(s).into_owned();
                    let t = or0(Some(self.seg_best(&k).2));
                    let ge = if self.b.plant == Some(Plant::LandFirstEqual) {
                        t.py_cmp(skt) == Ordering::Greater
                    } else {
                        t.py_cmp(skt) != Ordering::Less
                    };
                    if self.walk.seg_turn_contains(&k) && ge {
                        sk = Some(k);
                        skt = t;
                    }
                }
            }
        }
        if u.is_none()
            && line_t
            && let Some(k) = sk.clone()
        {
            let ts = self.walk.seg_turn_get(&k);
            let (au, aq) = layer5::summary_text_anchor(ts.as_ref(), line, false, None);
            u = au.map(str::to_string);
            q = aq.map(J::String);
        }
        if u.is_none() {
            let mut best: Option<(bool, Num, &str)> = None;
            for x in &sub {
                for s in self.trail(x) {
                    let (bu, bsub, bt) = self.seg_best(&layer1::kernel_seg_key(s));
                    if let Some(bu) = bu.filter(|b| !b.is_empty())
                        && best.is_none_or(|(s0, t0, _)| (bsub, bt).cmp_py(&(s0, t0)) == Ordering::Greater)
                    {
                        best = Some((bsub, bt, bu));
                    }
                }
            }
            if let Some((_, _, bu)) = best {
                u = Some(bu.to_string());
            }
        }
        if u.is_none()
            && line_t
            && let Some(k) = sk.clone()
        {
            let ts = self.walk.seg_turn_get(&k);
            let (au, aq) = layer5::summary_text_anchor(ts.as_ref(), line, true, None);
            u = au.map(str::to_string);
            q = aq.map(J::String);
        }
        if u.is_none() {
            'outer: for x in &sub {
                for s in self.trail(x).iter().rev() {
                    let wu = self.walk.maps.segs.get(&layer1::kernel_seg_key(s)).and_then(|e| e.uuid);
                    if let Some(wu) = wu.filter(|w| !w.is_empty()) {
                        u = Some(wu.to_string());
                        break 'outer;
                    }
                }
            }
        }
        let got = (u, q.filter(py_truthy));
        self.land_memo.insert(mk, got.clone());
        Ok(got)
    }

    /// flatten(nid, out, ancestor_done, boundary): the card's tree rows, root first, children by recency.
    fn flatten(
        &mut self,
        nid: &str,
        out: &mut Vec<Out>,
        ancestor_done: bool,
        boundary: Option<Num>,
        table: &mut AnchorTable,
    ) -> Result<(), Raise> {
        let nd = self.node(nid);
        let mut kids: Vec<String> = self.children.get(Some(nid)).to_vec();
        let keys: Vec<Num> = kids.iter().map(|k| self.fsubmax(k)).collect();
        let mut order: Vec<usize> = (0..kids.len()).collect();
        order.sort_by(|&a, &b| keys[b].py_cmp(keys[a]));
        kids = order.into_iter().map(|i| kids[i].clone()).collect();
        let explicit = flag(&nd.node_complete);
        let done = (explicit || self.closure_done(nid) || ancestor_done) && !self.agent_open.contains(nid);
        let derived = done && !explicit;
        let st = if done {
            "done"
        } else if self.closure_blocked(nid) {
            "question"
        } else {
            "open"
        };
        let (pa, wa) = match &self.planted {
            Some(p) => p.get(&self.node_index(nid)).cloned().unwrap_or((None, None)),
            None => layer4::node_anchor_uuids(nd, &WalkTrig(&self.walk.maps), &WalkWork(&self.walk.maps), table),
        };
        let ho = match &nd.handoff {
            Some(Some(h)) if !h.order.is_empty() => Some(h),
            _ => None,
        };
        let ho_sid: String = ho.and_then(|h| s2(&h.peer)).unwrap_or("").to_string();
        if !ho_sid.is_empty() {
            self.note_peer(&ho_sid);
        }
        let state = match st {
            "done" => Some("completed"),
            "question" => Some("blocked"),
            _ => None,
        };
        let (ncompleted, nline) = layer4::landing_inputs(state, "", nd);
        let (nsa_u, nsa_q) =
            if !ho_sid.is_empty() || nline.is_none() { (None, None) } else { self.brief_landing(nid, ncompleted, nline)? };
        let born: Option<&Born> = match &nd.born {
            Some(Some(b)) => Some(b),
            _ => self.healed.get(nid).map(|(_, b)| b),
        };
        let born_out = match born {
            Some(b) if !b.order.is_empty() || self.b.plant == Some(Plant::BornEmptyKept) => b.to_json(),
            _ => Out::Null,
        };
        let text = match &nd.text {
            None => return Err(key_error("text")),
            Some(t) => t.as_ref().map_or(Out::Null, |t| Out::Str(t.clone())),
        };
        let t = nd.t.ok_or_else(|| key_error("t"))?;
        let who = if ho_sid.is_empty() {
            self.name.clone()
        } else {
            Out::Str(self.b.names.name_of(&ho_sid).map_or_else(|| head(&ho_sid, 8).to_string(), str::to_string))
        };
        let who_color = if ho_sid.is_empty() {
            self.color.clone()
        } else {
            layer4::color_json(self.b.names.name_color(&ho_sid))
        };
        let reviewed = match boundary {
            Some(bd) if bd.truthy() && !out.is_empty() && done => {
                layer4::done_since(nd).py_cmp(bd) != Ordering::Greater
            }
            _ => false,
        };
        let parked = match self.parked.get(nid) {
            Some(&n) if st == "open" => obj(vec![("n", int_out(n))]),
            _ => Out::Null,
        };
        let auth = match &nd.agent_task {
            Some(J::Object(m)) if !m.is_empty() => jv(m.get("status")),
            _ => Out::Null,
        };
        let last = self.fsubmax(nid);
        let mt = match self.b.plant {
            Some(Plant::MtOr) => if nd.mt.is_some_and(|m| m.truthy()) { nd.mt.expect("checked") } else { t },
            _ => nd.mt.unwrap_or(t),
        };
        let log = if st != "done" {
            layer4::node_log_rows(nd, &WalkWork(&self.walk.maps), layer4::LOG_CAP)
        } else {
            Out::Null
        };
        out.push(obj(vec![
            ("id", Out::Str(nid.to_string())),
            ("kind", Out::Str(if ho_sid.is_empty() { "ask" } else { "handoff" }.into())),
            ("text", text),
            ("born", born_out),
            ("who", who),
            ("whoSid", Out::Str(if ho_sid.is_empty() { self.fsid.to_string() } else { ho_sid.clone() })),
            ("whoColor", who_color),
            ("whoWorking", Out::Bool(self.who_working && ho_sid.is_empty())),
            ("status", Out::Str(st.into())),
            ("derived", Out::Bool(derived)),
            ("cleared", Out::Bool(flag(&nd.cleared))),
            ("reviewedEarlier", if reviewed { Out::Bool(true) } else { Out::Null }),
            ("qderived", Out::Bool(st == "question" && !flag(&nd.blocked))),
            ("parked", parked),
            ("auth", auth),
            ("t", t.to_json()),
            ("last", last.to_json()),
            ("_ageT", last.to_json()),
            ("mt", mt.to_json()),
            ("anchorUuid", sv(wa.as_deref())),
            ("promptAnchorUuid", sv(pa.as_deref())),
            ("summaryAnchorUuid", sv(nsa_u.as_deref())),
            ("summaryAnchorQuote", jv(nsa_q.as_ref())),
            ("summary", sv(s2(&nd.summary))),
            ("blockSummary", sv(s2(&nd.block_summary))),
            ("relayNote", jor_none(&nd.relay_carried)),
            ("followupPending", jget(&nd.followup_pending)),
            ("log", log),
            ("children", Out::Array(kids.iter().map(|k| Out::Str(k.clone())).collect())),
        ]));
        for c in kids {
            self.flatten(&c, out, done, boundary, table)?;
        }
        Ok(())
    }

    fn node_index(&self, nid: &str) -> usize {
        self.ns.iter().position(|(k, _)| k == nid).expect("a children id is a node")
    }

    // ───────────── the body ─────────────

    fn run(mut self, table: &mut AnchorTable) -> Result<Out, Raise> {
        let b = self.b;
        let fsid = self.fsid;
        let rec = self.rec.rec();
        let mut ent_asks: Vec<Out> = Vec::new();
        let mut ent_folds: Vec<Out> = Vec::new();
        let mut ent_working = Out::Null;
        let mut ent_awaiting = Out::Null;
        let mut ent_bg = Out::Null;
        let mut cold_parse = false;
        let mut reads: Vec<(&str, Out)> = Vec::new();
        if b.session_flag(fsid, "hideFromFeed") {
            return Ok(Out::Null);
        }
        let tm = self.live_row;
        let live = tm.is_some();
        let ps = self.tree;
        let turns: Option<&'a [Turn]> = ps.map(|t| t.turns.as_deref().unwrap_or_default());
        if ps.is_none() && crate::memo::leaf_inode(b.snap, self.fsid, self.rec.facts).is_some() {
            let w = self.rec.get("_warm_wanted", &rec.warm_wanted)?;
            if py_truthy(w) {
                cold_parse = true;
            }
        }
        let who_working = self.who_working;
        if who_working {
            ent_working = self.name.clone();
        }
        let sess_progress = match turns {
            Some(t) if who_working => layer3::open_turn_progress(t).map_or(Out::Null, |p| {
                obj(vec![("since", p.since.to_json()), ("toolUses", int_out(p.tool_uses))])
            }),
            _ => Out::Null,
        };
        let sess_state = if who_working {
            "open"
        } else if ps.is_some() {
            "quiet"
        } else {
            "unknown"
        };
        // `bool(ps) and not who_working and _interrupted_by(...)` inside a try: an unrecorded answer reads False
        let sess_interrupted: J = if ps.is_none() || who_working {
            J::Bool(false)
        } else {
            rec.interrupted_by.clone().unwrap_or(J::Bool(false))
        };
        let sess_interrupting = self.interrupting;
        let sess_retrying = self.rec.get("_session_retrying", &rec.session_retrying)?.clone();
        self.walk = self.walk_now();
        let store = self.store;
        let sess_judging = !who_working && (b.board.jactive.as_ref().is_some_and(|j| j.iter().any(|s| s == fsid)) || self.closer);
        let nodes_ns = &self.ns;
        let confirming: HashSet<String> = store
            .confirming
            .as_ref()
            .map(|c| c.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        let _ = nodes_ns;
        // T319 / T333: the heal and the hidden set
        let healed = self.rec.get("_heal_session_tops", &rec.heal_session_tops)?.clone();
        let mut heal_total: i64 = healed.values().filter(|(p, _)| truthy_str(p.as_deref())).count() as i64;
        let mut hidden_set: Vec<String> =
            healed.iter().filter(|(_, (_, born))| tj(&born.hidden)).map(|(k, _)| k.clone()).collect();
        let mut hidden_total = hidden_set.len() as i64;
        let (children, _hidden_sorted) = layer4::body_children(&self.ns, &healed)
            .map_err(|e| Raise { exc: "Refused", arg: e })?;
        self.children = children;
        self.healed = healed;
        self.agent_open = layer4::agent_open_set(&self.ns, &self.children);
        self.parked = layer4::parked_rows(&self.ns, &self.children).into_iter().collect();
        let last_node = s2(&store.last_node);
        let status = self.status;
        let perm_state: Option<&str> = if row_truthy(tm) { tm.and_then(|r| s2(&r.state)) } else { None };
        let mut perm_top: Option<String> = None;
        if perm_state.is_some_and(|p| NEEDS_INPUT_STATES.contains(&p)) {
            perm_top = layer4::focus_open_top(&self.ns, last_node, status);
        }
        // the session-level awaiting signal
        let sess_aw: Option<&serde_json::Map<String, J>> = if ps.is_some() {
            let sub = if !who_working { "idle=true" } else { "idle=false" };
            let m = rec.session_awaiting.as_ref().ok_or_else(|| not_recorded("_session_awaiting", fsid, Some(sub)))?;
            let v = m.get(sub).ok_or_else(|| not_recorded("_session_awaiting", fsid, Some(sub)))?;
            v.as_object().filter(|o| !o.is_empty())
        } else {
            None
        };
        let (sess_awaiting_why, sess_awaiting_kind, sess_awaiting_since, sess_awaiting_count, sess_awaiting_peers) =
            match sess_aw {
                Some(o) => (
                    o.get("why").cloned().ok_or_else(|| key_error("why"))?,
                    o.get("kind").cloned().ok_or_else(|| key_error("kind"))?,
                    o.get("since").cloned().unwrap_or(J::Null),
                    o.get("count").cloned().unwrap_or(J::Null),
                    o.get("peers").cloned().unwrap_or(J::Null),
                ),
                None => (J::Null, J::Null, J::Null, J::Null, J::Null),
            };
        self.note_peers_json(Some(&sess_awaiting_peers));
        let sess_awaiting_items: Vec<J> = match sess_aw.and_then(|o| o.get("items")) {
            Some(J::Array(a)) => a.clone(),
            _ => Vec::new(),
        };
        let why_t = py_truthy(&sess_awaiting_why);
        if why_t && !who_working {
            ent_awaiting = self.name.clone();
        }
        // the API-error floor
        let aerr: Option<&J> = if ps.is_some() && !who_working && !why_t {
            Some(self.rec.get("_api_error", &rec.api_error)?)
        } else {
            None
        };
        let aerr_t = aerr.is_some_and(py_truthy);
        let cap_off = if aerr_t { Some(self.rec.get("_cap_switch_offer", &rec.cap_switch_offer)?.clone()) } else { None };
        if aerr_t {
            reads.push(("usage", Out::Bool(true)));
        }
        let auth_login_lbl = layer4::login_refusal_label(if row_truthy(tm) { tm } else { None }, aerr)?;
        let aobj: Option<&serde_json::Map<String, J>> = aerr.filter(|a| py_truthy(a)).and_then(J::as_object);
        let aget = |k: &str| aobj.and_then(|o| o.get(k));
        let aflag = |k: &str| aget(k).is_some_and(py_truthy);
        let mut api_top: Option<String> = None;
        if aerr_t {
            api_top = layer4::focus_open_top(&self.ns, last_node, status);
        }
        // the judge-auth floor
        let jerr: Option<&serde_json::Map<String, J>> =
            b.board.jauth.as_ref().and_then(|m| m.get(fsid)).filter(|v| py_truthy(v)).and_then(J::as_object);
        let jauth_login: String = jerr
            .and_then(|j| j.get("loginLabel"))
            .filter(|v| py_truthy(v))
            .map_or_else(String::new, py_str);
        let mut jauth_top: Option<String> = None;
        if jerr.is_some() && api_top.is_none() && perm_top.is_none() {
            jauth_top = layer4::focus_open_top(&self.ns, last_node, status);
        }
        // T319 / T333: a floor that resolves to a healed or hidden top brings it back as a card
        let mut unnested = false;
        for f in [perm_top.clone(), api_top.clone(), jauth_top.clone()] {
            let Some(f) = f else { continue };
            let Some((hp, hb)) = self.healed.get(&f).cloned() else { continue };
            if let Some(p) = hp.as_deref().filter(|p| !p.is_empty())
                && self.children.get(Some(p)).iter().any(|c| c == &f)
            {
                self.children.remove_child(Some(p), &f);
                self.children.push_child(None, &f);
                set_healed(&mut self.healed, &f, (None, hb));
                heal_total -= 1;
                unnested = true;
            } else if tj(&hb.hidden) && hidden_set.iter().any(|h| h == &f) {
                self.children.push_child(None, &f);
                set_healed(&mut self.healed, &f, (None, born_without_hidden(&hb)));
                hidden_set.retain(|h| h != &f);
                hidden_total -= 1;
                unnested = true;
            }
        }
        if unnested {
            self.agent_open = layer4::agent_open_set(&self.ns, &self.children);
            self.parked = layer4::parked_rows(&self.ns, &self.children).into_iter().collect();
        }
        let plain_user_t = match (ps.is_some(), b.held) {
            (false, _) => Num::Int(0),
            (true, Some(held)) => held.borrow_mut().finish(&mut self.walk),
            (true, None) => layer3::feed_seg_plain_t(&self.walk),
        };
        let mut had_working = false;
        let mut had_awaiting = false;
        let live_tasks: &[J] = if ps.is_some() && !who_working {
            self.rec.get("_bg_live_norm", &rec.bg_live)?.as_slice()
        } else {
            &[]
        };
        let owned: Option<&serde_json::Map<String, J>> = if !live_tasks.is_empty() {
            self.rec.get("_bg_owner_tops", &rec.bg_owner_tops)?.as_object()
        } else {
            None
        };
        if !live_tasks.is_empty() && live {
            let svc = self.rec.get("_bg_service_descs", &rec.bg_service_descs)?;
            if py_truthy(svc) {
                ent_bg = svc.to_json();
            }
        }
        if self.b.plant == Some(Plant::AnchorStoreOrder) {
            let mut p = HashMap::default();
            for (i, (_, nd)) in self.ns.iter().enumerate() {
                p.insert(i, layer4::node_anchor_uuids(nd, &WalkTrig(&self.walk.maps), &WalkWork(&self.walk.maps), table));
            }
            self.planted = Some(p);
        }
        let index = PromptIndex::new(b.trees.get(fsid).copied());
        let floors = [perm_top.clone(), api_top.clone(), jauth_top.clone()];
        let is_floor = |nid: &str| floors.iter().any(|f| f.as_deref() == Some(nid));
        let wmap_entry: Option<&serde_json::Map<String, J>> = b.wmap().and_then(|w| w.get(fsid)).and_then(J::as_object);
        let wsince = || wmap_entry.and_then(|w| w.get("since")).and_then(jnum).expect("validated: a wait has a number since");
        let mut nudges: Vec<Out> = Vec::new();
        let tops: Vec<String> = self.children.get(None).to_vec();
        for nid in tops {
            let col0 = status.get(&nid).map_or("working", String::as_str);
            if col0 == "cleared" || b.cleared.contains(&nid) {
                continue;
            }
            if !is_floor(&nid)
                && layer5::pure_delegation_top(&self.ns, &nid, &mut |p| layer5::dictated_prompt_uuid(&index, true, Some(p)))
            {
                continue;
            }
            let nd = self.node(&nid);
            let mut col: &str = col0;
            let peer_wait = match wmap_entry {
                Some(_) => nd.t.ok_or_else(|| key_error("t"))?.py_cmp(wsince()) != Ordering::Greater,
                None => false,
            };
            let mut await_ok = py_truthy(&sess_awaiting_why);
            let mut owned_why: Option<String> = None;
            let mut owned_since: Option<Num> = None;
            let mut blk_t: Option<Num> = None;
            if col == "blocked" {
                let sub = self.subtree(&nid)?;
                let mut ids = Vec::new();
                for x in &sub {
                    if flag(&self.node(x).blocked) && !self.closure_done(x) {
                        ids.push(x.clone());
                    }
                }
                blk_t = layer4::newest_block_ev_t(&self.ns, &ids);
                let own = owned.and_then(|o| o.get(&nid)).filter(|v| py_truthy(v)).and_then(J::as_object);
                let own_since = own.and_then(|o| o.get("since")).and_then(jnum);
                await_ok = match (own, blk_t) {
                    (Some(_), Some(bt)) => own_since.expect("validated").py_cmp(bt) != Ordering::Less,
                    _ => false,
                };
                if await_ok {
                    let own = own.expect("checked");
                    let descs = own.get("descs").and_then(J::as_array).map_or(&[][..], Vec::as_slice);
                    owned_why = Some(format!(
                        "waiting on a background command{}",
                        if descs.is_empty() { String::new() } else { format!(": {}", py_str(&descs[0])) }
                    ));
                    owned_since = own_since;
                }
            }
            let sf = if col == "working" {
                let (t, per) = self.rec.get("_peer_answered", &rec.peer_answered)?;
                let answered = Answered::Pair(*t, per);
                layer4::goal_awaiting_stamp_full(&self.ns, &nid, Some(&self.children), &answered)
                    .map_err(|_| Raise { exc: "Loops", arg: format!("the stamp walk from {nid} meets a node twice") })?
            } else {
                None
            };
            let stamp_why: Option<String> = sf.as_ref().map(|s| s.why.clone());
            let stamp_kind: Option<String> = sf.as_ref().and_then(|s| s.kind.clone());
            let stamp_since: Option<Num> = sf.as_ref().and_then(|s| Some(s.at).filter(|a| a.truthy()));
            let stamp_peers: Option<Vec<Identity>> = match &sf {
                Some(s) if stamp_kind.as_deref() == Some("peer") && !s.peers.is_empty() => {
                    let ctx = b.ctx();
                    let mut v: Vec<Identity> = s.peers.iter().map(|p| layer4::peer_identity(&ctx, Some(p))).collect();
                    v.sort_by(|a, b| a.name.cmp(&b.name));
                    Some(v)
                }
                _ => None,
            };
            self.note_identities(stamp_peers.as_deref());
            let mut deleg_why: Option<String> = None;
            let mut deleg_since: Option<Num> = None;
            let mut deleg_peers: Option<Vec<Identity>> = None;
            if col == "working"
                && !truthy_str(stamp_why.as_deref())
                && !await_ok
                && layer4::all_outstanding_delegated(&self.ns, &nid)
            {
                let hnodes: Vec<String> = layer4::open_leaves(&self.ns, &nid)
                    .into_iter()
                    .filter(|x| matches!(self.node(x).handoff, Some(Some(_))))
                    .collect();
                let hrefs: Vec<&str> = hnodes.iter().map(String::as_str).collect();
                let ctx = b.ctx();
                deleg_peers = layer4::handoff_peer_identities(&ctx, &self.ns, &hrefs);
                let dp = deleg_peers.clone();
                self.note_identities(dp.as_deref());
                let mut names: Vec<String> = deleg_peers.iter().flatten().map(|d| d.name.clone()).collect();
                names.sort();
                names.dedup();
                if names.is_empty() {
                    names.push("a peer".to_string());
                }
                deleg_why = Some(format!("delegated to {}; waiting on their result", names.join(", ")));
                let m = hnodes.iter().map(|x| or0(self.node(x).t)).reduce(py_max).unwrap_or(Num::Int(0));
                deleg_since = Some(m).filter(|m| m.truthy());
            }
            let not_hard = api_top.as_deref() != Some(nid.as_str()) && perm_top.as_deref() != Some(nid.as_str());
            if not_hard
                && (col == "working" || col == "blocked")
                && (await_ok
                    || truthy_str(stamp_why.as_deref())
                    || deleg_why.is_some()
                    || (col == "blocked"
                        && peer_wait
                        && blk_t.is_some_and(|bt| wsince().py_cmp(bt) != Ordering::Less)))
            {
                col = "awaiting";
            }
            // tracked delegation payloads
            let sub = self.subtree(&nid)?;
            let tracked_hn: Vec<&str> = sub
                .iter()
                .map(String::as_str)
                .filter(|x| {
                    let n = self.node(x);
                    matches!(&n.handoff, Some(Some(h)) if tj(&h.tracked)) && !flag(&n.node_complete) && !flag(&n.cleared)
                })
                .collect();
            let tracked_peers: Option<Vec<Identity>> = if tracked_hn.is_empty() {
                None
            } else {
                layer4::handoff_peer_identities(&b.ctx(), &self.ns, &tracked_hn)
            };
            self.note_identities(tracked_peers.as_deref());
            // the origin badge
            let o = match &nd.origin {
                Some(Some(o)) => Some(o),
                _ => None,
            };
            let mut origin = Out::Null;
            let mut origin_live = false;
            if let Some(o) = o
                && let Some(psid) = s2(&o.peer).filter(|p| !p.is_empty())
            {
                let gid = s2(&o.goal_id);
                self.note_peer(psid);
                let pstore: Option<&Store> = if truthy_str(gid) {
                    match b.stores.get(psid) {
                        Some(st) => *st,
                        None => return Err(Raise { exc: NOT_RECORDED, arg: format!("store({psid})") }),
                    }
                } else {
                    None
                };
                let sgoal = pstore.and_then(|p| p.nodes.as_ref()).and_then(|n| n.get(gid.unwrap_or("")));
                origin_live = sgoal.is_some_and(|g| !flag(&g.node_complete) && !flag(&g.cleared))
                    && !b.cleared.contains(gid.unwrap_or(""));
                let pname = b.names.name_of(psid);
                let peer = match pname {
                    Some(n) => n.to_string(),
                    None => s2(&o.peer_name)
                        .filter(|n| !n.is_empty())
                        .map_or_else(|| head(psid, 8).to_string(), str::to_string),
                };
                let peer_host = if pname.is_some() {
                    Out::Str(String::new())
                } else {
                    match o.peer_host.as_ref().filter(|v| py_truthy(v)) {
                        Some(v) => v.to_json(),
                        None => Out::Str(String::new()),
                    }
                };
                origin = obj(vec![
                    ("peer", Out::Str(peer)),
                    ("peerHost", peer_host),
                    ("peerSid", Out::Str(psid.to_string())),
                    ("color", layer4::color_json(b.names.name_color(psid))),
                    ("live", Out::Bool(origin_live)),
                ]);
            }
            let (handoff_to, card_text) = layer4::handoff_card_fields(&b.ctx(), &self.ns, &nid);
            // Python's `_note_peers([handoff_to] if handoff_to else None)` is left out: it adds nothing, since the
            // handoffTo badge carries `peerSid`, not the `sid` _note_peers reads (flatten notes the top's own handoff
            // peer when it writes the card's first row)
            // the awaiting badge's why, kind, since, peers, count and items: the or-chain's first truthy why
            let is_awaiting = col == "awaiting";
            let await_why: Out = if is_awaiting {
                if py_truthy(&sess_awaiting_why) {
                    sess_awaiting_why.to_json()
                } else if truthy_str(stamp_why.as_deref()) {
                    sv(stamp_why.as_deref())
                } else if let Some(d) = &deleg_why {
                    Out::Str(d.clone())
                } else {
                    sv(owned_why.as_deref())
                }
            } else {
                Out::Null
            };
            let (mut await_kind, mut await_since, mut await_peers, mut await_count) = (Out::Null, Out::Null, Out::Null, Out::Null);
            let mut await_items: Vec<Out> = Vec::new();
            if is_awaiting {
                let idents = |v: &Option<Vec<Identity>>| v.as_ref().map_or(Out::Null, |v| Out::Array(v.iter().map(Identity::to_json).collect()));
                let items = |v: &Option<Vec<Identity>>| layer4::awaiting_peer_items(v.as_deref()).iter().map(layer4::Item::to_json).collect::<Vec<_>>();
                let count_of = |n: Option<usize>| n.filter(|&n| n > 0).map_or(Out::Null, int_out);
                if py_truthy(&sess_awaiting_why) {
                    await_kind = sess_awaiting_kind.to_json();
                    await_since = sess_awaiting_since.to_json();
                    await_peers = sess_awaiting_peers.to_json();
                    await_items = sess_awaiting_items.iter().map(ToJson::to_json).collect();
                    await_count = match &sess_awaiting_count {
                        J::Bool(true) => Out::Bool(true),
                        J::Number(n) => match n.as_i64() {
                            Some(i) if i > 0 => Out::Int(i),
                            _ => Out::Null,
                        },
                        _ => Out::Null,
                    };
                } else if truthy_str(stamp_why.as_deref()) {
                    await_kind = sv(stamp_kind.as_deref());
                    await_since = stamp_since.map_or(Out::Null, |n| n.to_json());
                    await_peers = idents(&stamp_peers);
                    await_count = count_of(stamp_peers.as_ref().map(Vec::len));
                    await_items = items(&stamp_peers);
                } else if deleg_why.is_some() {
                    await_kind = Out::Str("peer".into());
                    await_since = deleg_since.map_or(Out::Null, |n| n.to_json());
                    await_peers = idents(&deleg_peers);
                    await_count = count_of(deleg_peers.as_ref().map(Vec::len));
                    await_items = items(&deleg_peers);
                } else if owned_why.is_some() {
                    await_kind = Out::Str("task".into());
                    await_since = owned_since.map_or(Out::Null, |n| n.to_json());
                    await_peers = Out::Null;
                    await_count = Out::Int(1);
                    await_items = Vec::new();
                }
            }
            // the card's time
            let mut disp_t = self.fsubmax(&nid);
            if col == "completed" {
                let mut dts: Vec<Num> = Vec::new();
                for x in &sub {
                    if x != &nid && self.closure_done(x) && !tj(&self.node(x).rolled_up) {
                        dts.push(layer4::done_since(self.node(x)));
                    }
                }
                let cand = dts.into_iter().fold(or0(n2(&nd.settled_at)), py_max);
                if cand.truthy() {
                    disp_t = cand;
                }
            } else if col == "blocked" {
                let mut bts: Vec<Num> = Vec::new();
                for x in &sub {
                    if flag(&self.node(x).blocked) && !self.closure_done(x) {
                        bts.push(layer4::block_since(self.node(x)));
                    }
                }
                if let Some(m) = bts.into_iter().reduce(py_max) {
                    disp_t = m;
                }
            }
            if col == "working" {
                disp_t = py_max(disp_t, or0(n2(&nd.followup_at)));
            }
            let fpend = tj(&nd.followup_pending);
            let recheck = col == "blocked" && not_hard && fpend;
            let bct = self.block_check_floor(&nid);
            let bound = bct.unwrap_or_else(|| or0(n2(&nd.block_check_t)));
            let rejudging = col == "blocked"
                && not_hard
                && !fpend
                && plain_user_t.py_cmp(disp_t) == Ordering::Greater
                && plain_user_t.py_cmp(bound) == Ordering::Greater;
            let api_block = api_top.as_deref() == Some(nid.as_str())
                && aerr_t
                && ["tooLong", "spendLimit", "modelLimit", "noRetry", "authErr", "refusal"].iter().any(|k| aflag(k));
            nudges.push(Out::Str(nid.clone()));
            let nrec: Option<&NudgeRecord> =
                b.board.nudge_records.as_ref().and_then(|m| m.get(&nid)).filter(|r| !r.order.is_empty());
            let mut stall_rec: Option<&serde_json::Map<String, J>> =
                b.board.stalls.as_ref().and_then(|m| m.get(&nid)).filter(|v| py_truthy(v)).and_then(J::as_object);
            let stall_inflight =
                stall_rec.is_some_and(|r| r.get("why").and_then(J::as_str).is_some_and(|w| WHY_IN_FLIGHT.contains(&w)));
            if stall_inflight {
                stall_rec = None;
            }
            let stall_block = stall_rec.is_some()
                && col != "completed"
                && !confirming.contains(&nid)
                && !who_working
                && !py_truthy(&sess_awaiting_why);
            let nlog = nd.log.as_deref().unwrap_or_default();
            let lastblk: Option<&str> = nlog.iter().rev().find(|e| s2(&e.kind) == Some("block")).and_then(|e| s2(&e.src));
            let lastblk_found = nlog.iter().any(|e| s2(&e.kind) == Some("block"));
            let _ = lastblk_found;
            let row_t = |e: &crate::snapshot::LogRow| -> Num {
                [n2(&e.at), n2(&e.ev_t)].into_iter().flatten().find(|n| n.truthy()).unwrap_or(Num::Int(0))
            };
            let nblk_at: Option<Num> = nlog
                .iter()
                .rev()
                .find(|e| s2(&e.kind) == Some("block") && s2(&e.src) == Some("nudge"))
                .map(|e| row_t(e));
            let nfloor: Option<Num> = nblk_at.or_else(|| nrec.and_then(|r| n2(&r.failed_at)));
            let story_moved = match nfloor {
                Some(fl) => nlog.iter().any(|e| {
                    row_t(e).py_cmp(fl) == Ordering::Greater && s2(&e.src).is_some_and(|s| STORY_SOURCES.contains(&s))
                }),
                None => nlog.iter().any(|e| s2(&e.src) == Some("user")),
            };
            let nudge_failed = nrec.is_some_and(|r| flag(&r.failed))
                && !story_moved
                && (col == "working" || (col == "blocked" && lastblk == Some("nudge")));
            let is_jauth = jauth_top.as_deref() == Some(nid.as_str());
            let is_perm = perm_top.as_deref() == Some(nid.as_str());
            let is_api = api_top.as_deref() == Some(nid.as_str());
            let column = if api_block || is_jauth || is_perm || stall_block || (col == "blocked" && !recheck && !rejudging) {
                "needs_input"
            } else if col == "completed" {
                "completed"
            } else {
                "working"
            };
            had_working = had_working || column == "working";
            had_awaiting = had_awaiting || col == "awaiting";
            let distill_state = if col == "completed" {
                Some("completed")
            } else if api_block || is_jauth || is_perm || col == "blocked" {
                Some("blocked")
            } else {
                None
            };
            let (completed, line) = layer4::landing_inputs(distill_state, column, nd);
            let cited = s2(&nd.summary_anchor);
            let (mut sa_u, sa_q) = self.brief_landing(&nid, completed, line)?;
            if sa_u.is_none() && ps.is_none() {
                sa_u = cited.map(str::to_string);
            }
            let sa_is_cited = truthy_str(sa_u.as_deref()) && sa_u.as_deref() == cited;
            let satellite = o.is_some_and(|o| tj(&o.tracked)) && origin_live && !matches!(origin, Out::Null) && column != "needs_input";
            let mut card: Vec<(&str, Out)> = vec![
                ("itemId", Out::Str(nid.clone())),
                ("sid", Out::Str(fsid.to_string())),
                ("name", self.name.clone()),
                ("color", self.color.clone()),
                ("text", Out::Str(card_text)),
                ("t", disp_t.to_json()),
                ("live", Out::Bool(live)),
                ("_ageT", disp_t.to_json()),
                ("turnId", Out::Str(nid.clone())),
                ("origin", origin.clone()),
            ];
            if let Some(h) = &handoff_to {
                card.push(("handoffTo", h.to_json()));
            }
            if let Some(tp) = tracked_peers.as_ref().filter(|v| !v.is_empty()) {
                card.push(("delegTracked", Out::Array(tp.iter().map(Identity::to_json).collect())));
            }
            if satellite {
                card.push(("satellite", Out::Bool(true)));
            }
            card.push(("followupPending", jget(&nd.followup_pending)));
            card.push(("followupAt", n2(&nd.followup_at).filter(|n| n.truthy()).map_or(Out::Null, |n| n.to_json())));
            card.push((
                "doneConfirming",
                if column == "working" && confirming.contains(&nid) { Out::Bool(true) } else { Out::Null },
            ));
            card.push((
                "waitingOn",
                if column == "working" && peer_wait {
                    b.wmap().and_then(|w| w.get(fsid)).map_or(Out::Null, ToJson::to_json)
                } else {
                    Out::Null
                },
            ));
            card.push((
                "awaiting",
                if is_awaiting {
                    obj(vec![
                        ("why", await_why.clone()),
                        ("kind", await_kind),
                        ("since", await_since),
                        ("count", await_count),
                        ("peers", await_peers),
                        ("items", Out::Array(await_items)),
                        ("tasks", self.rec.get("_awaiting_task_descs", &rec.awaiting_task_descs)?.to_json()),
                    ])
                } else {
                    Out::Null
                },
            ));
            card.push(("summary", sv(s2(&nd.summary))));
            card.push(("distillState", sv(distill_state)));
            card.push(("blockSummary", sv(s2(&nd.block_summary))));
            card.push(("relayNote", jor_none(&nd.relay_carried)));
            card.push(("briefParts", jor_none(&nd.brief_parts)));
            card.push(("summaryParts", jor_none(&nd.summary_parts)));
            let stale = or0(n2(&nd.followup_at)).py_cmp(or0(n2(&nd.distilled_mt))) == Ordering::Greater
                && !pystr::strip(s2(&nd.summary).unwrap_or("")).is_empty();
            card.push(("summaryStale", if stale { Out::Bool(true) } else { Out::Null }));
            card.push(("background", jget(&nd.background)));
            card.push(("summaryAnchorUuid", sv(sa_u.as_deref())));
            card.push((
                "summaryAnchorQuote",
                if sa_is_cited { jget(&nd.summary_quote) } else { jv(sa_q.as_ref().filter(|q| py_truthy(q))) },
            ));
            card.push((
                "summaryAnchorsPara",
                match &nd.summary_anchors {
                    Some(J::Array(items)) if !items.is_empty() && sa_is_cited => Out::Array(
                        items
                            .iter()
                            .map(|e| match e.as_object().filter(|_| py_truthy(e)) {
                                Some(m) => {
                                    let mut v = vec![("u", jv(m.get("a")))];
                                    if let Some(q) = m.get("q").filter(|q| py_truthy(q)) {
                                        v.push(("q", q.to_json()));
                                    }
                                    obj(v)
                                }
                                None => Out::Null,
                            })
                            .collect(),
                    ),
                    _ => Out::Null,
                },
            ));
            card.push(("warns", jor_none(&nd.warns)));
            card.push(("failLog", jor_none(&nd.fail_log)));
            card.push((
                "nudged",
                match nrec.and_then(|r| r.count).filter(|c| c.truthy()) {
                    Some(c) => obj(vec![
                        ("count", py_int_out(c)),
                        (
                            "times",
                            Out::Array(
                                b.board
                                    .nudge_times
                                    .as_ref()
                                    .and_then(|m| m.get(&nid))
                                    .map(|v| v.iter().map(ToJson::to_json).collect())
                                    .unwrap_or_default(),
                            ),
                        ),
                    ]),
                    None => Out::Null,
                },
            ));
            let blocked = if is_api {
                let a = aobj.expect("api_top is set only when the api error is truthy, and a truthy non-dict raised");
                let mut m: Vec<(&str, Out)> = vec![("state", Out::Str("apiError".into()))];
                if let Some(c) = cap_off.as_ref().filter(|c| py_truthy(c)) {
                    m.push(("capOffer", c.to_json()));
                }
                let what = if aflag("spendLimit") {
                    Out::Str("this account hit its monthly spend limit — raise it at claude.ai/settings/usage to continue".into())
                } else if aflag("tooLong") {
                    Out::Str("this session's prompt is too long — compact it to continue".into())
                } else if aflag("modelLimit") {
                    Out::Str("this session's model is out of allowance — switch its model or add credits to continue".into())
                } else if aflag("authErr") && !auth_login_lbl.is_empty() {
                    Out::Str(format!("the {auth_login_lbl} login was refused: remove it or add it again in the gear's Billing block, or switch which login this session bills"))
                } else if aflag("authErr") {
                    Out::Str("this session's sign-in or API key isn't working — fix the login (claude /login) or the key, or switch which one it bills".into())
                } else if aflag("refusal") {
                    Out::Str("the model's safeguards refused this prompt — rewrite it or drop this thread".into())
                } else if aflag("noRetry") {
                    match a.get("text").filter(|t| py_truthy(t)) {
                        Some(t) => t.to_json(),
                        None => Out::Str("this session stopped and nothing retries it".into()),
                    }
                } else {
                    Out::Str("this session stopped on an API error — Retry to resume".into())
                };
                m.extend([
                    ("status", jv(a.get("status"))),
                    ("text", jv(a.get("text"))),
                    ("tooLong", Out::Bool(aflag("tooLong"))),
                    ("spendLimit", Out::Bool(aflag("spendLimit"))),
                    ("modelLimit", Out::Bool(aflag("modelLimit"))),
                    ("authErr", Out::Bool(aflag("authErr"))),
                    ("refusal", Out::Bool(aflag("refusal"))),
                    ("noRetry", Out::Bool(aflag("noRetry"))),
                    ("what", what),
                ]);
                obj(m)
            } else if is_jauth {
                let j = jerr.expect("jauth_top is set only with a latch entry");
                let mode_key = j.get("mode").and_then(J::as_str) == Some("key");
                let what = if mode_key {
                    "romp can't analyze this session — the API key its judges bill is being refused. Fix the key behind Claude Code's apiKeyHelper (rotate the vault item) or switch which account this session bills".to_string()
                } else if !jauth_login.is_empty() {
                    format!("romp can't analyze this session: the {jauth_login} login its judges bill is being refused. Remove it or add it again in the gear's Billing block, or switch which login this session bills")
                } else {
                    "romp can't analyze this session — the login its judges bill is being refused. Sign in again (claude /login) or switch which account this session bills".to_string()
                };
                obj(vec![
                    ("state", Out::Str("judgeAuth".into())),
                    ("mode", jv(j.get("mode"))),
                    ("login", Out::Str(jauth_login.clone())),
                    ("since", jv(j.get("t"))),
                    ("text", match j.get("note").filter(|n| py_truthy(n)) {
                        Some(n) => n.to_json(),
                        None => Out::Str(String::new()),
                    }),
                    ("what", Out::Str(what)),
                ])
            } else if is_perm {
                let ps_ = perm_state.expect("perm_top is set only in a needs-input state");
                obj(vec![
                    ("state", Out::Str(ps_.to_string())),
                    ("what", Out::Str(if ps_ == "picker" {
                        "this session is stopped awaiting your input"
                    } else {
                        "this session is stopped awaiting your approval"
                    }.into())),
                ])
            } else {
                Out::Null
            };
            card.push(("blocked", blocked));
            card.push(("retrying", if column == "working" { sess_retrying.to_json() } else { Out::Null }));
            card.push(("nudgeFailed", Out::Bool(nudge_failed)));
            let stalled = match stall_rec {
                Some(r) if !nudge_failed && (column == "working" || stall_block) => obj(vec![
                    ("why", r.get("why").ok_or_else(|| key_error("why"))?.to_json()),
                    ("since", r.get("since").ok_or_else(|| key_error("since"))?.to_json()),
                    ("note", jor_none(&nd.stall_summary)),
                    ("blocked", Out::Bool(stall_block)),
                ]),
                _ => Out::Null,
            };
            card.push(("stalled", stalled));
            let interrupt_col = column == "working" || (col == "blocked" && lastblk == Some("interrupt"));
            card.push(("interrupting", Out::Bool(sess_interrupting && interrupt_col)));
            card.push((
                "interrupted",
                if py_truthy(&sess_interrupted) && !sess_interrupting && interrupt_col {
                    sess_interrupted.to_json()
                } else {
                    Out::Bool(false)
                },
            ));
            card.push(("column", Out::Str(column.into())));
            card.push(("board", Out::Str("feed".into())));
            card.push(("category", Out::Str(column.into())));
            card.push(("recheck", Out::Bool(recheck)));
            card.push(("rejudging", Out::Bool(rejudging)));
            card.push(("judging", Out::Bool((sess_judging || stall_inflight) && column == "working")));
            card.push(("working", if column == "working" { sess_progress.clone() } else { Out::Null }));
            card.push(("sessState", if column == "working" { Out::Str(sess_state.into()) } else { Out::Null }));
            let warn_rows = match b.dbg_rows() {
                Some(rows) => {
                    let subset: HashSet<&str> = sub.iter().map(String::as_str).collect();
                    let placements = store.placements.as_ref().and_then(Option::as_ref);
                    let got = card_warn_rows(rows, fsid, &subset, placements, WARN_CAP);
                    if got.is_empty() { Out::Null } else { Out::Array(got) }
                }
                None => Out::Null,
            };
            card.push(("warnRows", warn_rows));
            card.push((
                "sessionStarted",
                layer4::session_started_face(&self.ns, &nid, &self.healed)
                    .map_or(Out::Null, |f| obj(vec![("why", Out::Str(f.why)), ("parent", f.parent.to_json())])),
            ));
            let mut rows = Vec::new();
            let boundary = layer4::review_boundary(nd);
            self.flatten(&nid, &mut rows, false, boundary, table)?;
            card.push(("tree", Out::Array(rows)));
            let card = obj(card);
            let srv = nd.serving.as_ref().and_then(J::as_object);
            match srv.and_then(|s| s.get("goalId")).filter(|g| py_truthy(g)) {
                Some(g) if column == "working" || column == "completed" => {
                    ent_folds.push(obj(vec![("tracker", g.to_json()), ("card", card)]));
                }
                _ => ent_asks.push(card),
            }
        }
        if !nudges.is_empty() {
            reads.push(("nudges", Out::Array(nudges)));
        }
        if had_awaiting && !who_working && matches!(ent_awaiting, Out::Null) {
            ent_awaiting = self.name.clone();
        }
        if !had_working && perm_top.is_none() && ps.is_some() {
            let pc = if self.store_faulted {
                None
            } else {
                layer5::provisional_card(
                    &ProvisionalIn {
                        fsid,
                        name: self.name.clone(),
                        color: self.color.clone(),
                        live,
                        tree: b.trees.get(fsid).copied(),
                        store: Some(store),
                        captions: self.rec.facts.captions.as_ref().and_then(Option::as_ref),
                        cleared: &b.cleared,
                    },
                    None,
                )?
            };
            if let Some(pc) = pc {
                ent_asks.push(pc);
            } else if perm_state.is_some_and(|p| NEEDS_INPUT_STATES.contains(&p)) {
                ent_asks.push(self.blocked_placeholder(live, perm_state.expect("checked"))?);
            } else if py_truthy(&sess_awaiting_why) {
                ent_asks.push(self.awaiting_card(
                    live,
                    &sess_awaiting_why,
                    &sess_awaiting_kind,
                    &sess_awaiting_since,
                    &sess_awaiting_count,
                    &sess_awaiting_items,
                )?);
            }
        }
        let mut peers = self.peers_read.clone();
        if self.b.plant != Some(Plant::PeersUnsorted) {
            peers.sort();
        }
        Ok(obj(vec![
            ("asks", Out::Array(ent_asks)),
            ("working", ent_working),
            ("awaiting", ent_awaiting),
            ("bgServices", ent_bg),
            ("servingFolds", Out::Array(ent_folds)),
            ("heal", Out::Int(heal_total)),
            ("hidden", Out::Int(hidden_total)),
            ("cold", Out::Bool(cold_parse)),
            ("peers", Out::Array(peers.into_iter().map(Out::Str).collect())),
            ("reads", obj(reads)),
            ("faults", int_out(self.walk.seg_turn.faults)),
        ]))
    }

    /// kernel._blocked_placeholder(s, name, color, fsid, live, now, perm_state, since).
    fn blocked_placeholder(&self, live: bool, perm_state: &str) -> Result<Out, Raise> {
        let b = self.b;
        let mut text: Out = Out::Null;
        let (mut t, mut age_t) = (b.now.to_json(), Out::Null);
        let turns: &[Turn] = b.trees.get(self.fsid).map_or(&[], |t| t.turns.as_deref().unwrap_or_default());
        if let Some(lt) = turns.last() {
            let segs = layer2::segments(lt)?;
            if let Some(held) = segs.last() {
                t = held.t.to_json();
                age_t = t.clone();
                if layer5::seg_human(held) {
                    let prompt = layer5::clean_prompt(&layer5::seg_prompt(held));
                    if !prompt.is_empty() {
                        let caps = self
                            .rec
                            .facts
                            .captions
                            .as_ref()
                            .and_then(Option::as_ref)
                            .ok_or_else(|| Raise { exc: NOT_RECORDED, arg: format!("_captions({})", self.fsid) })?;
                        let g = layer5::seg_caption(caps, &held.id);
                        text = if py_truthy(&g) { g.to_json() } else { Out::Str(pystr::strip(head(&prompt, 140)).to_string()) };
                    }
                }
            }
        }
        if !out_truthy(&text) {
            // `Sessions.backend_for(fsid).current_ask(fsid)` inside a try: an unrecorded answer reads None
            let ask = self.rec.rec().current_ask.as_ref().filter(|a| py_truthy(a)).and_then(J::as_object);
            if let Some(ask) = ask {
                // `(ask.get("question") or "").strip()`, then the header's, both outside the try: a truthy value that is
                // not a string raises AttributeError and fails the entry (the question's first, and the header's even
                // when the question is a string)
                let q = or_empty_strip(ask.get("question"))?;
                let h = or_empty_strip(ask.get("header"))?;
                if !q.is_empty() {
                    text = Out::Str(if pystr::len(q) > 140 { format!("{}…", rstrip(head(q, 139))) } else { q.to_string() });
                } else if !h.is_empty() && h.to_lowercase() != "permission" {
                    // Rust's lowercase and Python's `lower()` differ in Unicode version, but not on any code point that
                    // lowers to ASCII letters (A to Z, a to z, the Kelvin sign), the only ones an equal "permission"
                    // can hold: tests/layer6_edge.rs checks every code point against Python's list
                    text = Out::Str(h.to_string());
                }
            }
        }
        if !out_truthy(&text) {
            text = Out::Str(if perm_state == "picker" { "Awaiting your input" } else { "Awaiting your approval" }.into());
        }
        Ok(obj(vec![
            ("itemId", Out::Str(format!("blocked:{}", self.fsid))),
            ("sid", Out::Str(self.fsid.to_string())),
            ("name", self.name.clone()),
            ("color", self.color.clone()),
            ("text", text),
            ("t", t),
            ("live", Out::Bool(live)),
            ("_ageT", age_t),
            ("turnId", Out::Null),
            ("origin", Out::Null),
            ("followupPending", Out::Null),
            ("summary", Out::Null),
            ("blockSummary", Out::Null),
            ("background", Out::Null),
            (
                "blocked",
                obj(vec![
                    ("state", Out::Str(perm_state.to_string())),
                    (
                        "what",
                        Out::Str(if perm_state == "picker" {
                            "this session is stopped awaiting your input"
                        } else {
                            "this session is stopped awaiting your approval"
                        }
                        .into()),
                    ),
                ]),
            ),
            ("column", Out::Str("needs_input".into())),
            ("board", Out::Str("feed".into())),
            ("category", Out::Str("needs_input".into())),
            ("provisional", Out::Bool(true)),
            ("tree", Out::Array(Vec::new())),
        ]))
    }

    /// kernel._awaiting_card(s, name, color, fsid, live, now, why, kind, since, count, items): None (null in asks) for a
    /// session that is not live.
    fn awaiting_card(&self, live: bool, why: &J, kind: &J, since: &J, count: &J, items: &[J]) -> Result<Out, Raise> {
        if !live {
            return Ok(Out::Null);
        }
        let b = self.b;
        let (mut t, mut age_t) = (b.now.to_json(), Out::Null);
        if let Some(lt) = b.trees.get(self.fsid).and_then(|t| t.turns.as_deref()).and_then(<[Turn]>::last)
            && let Some(lt_t) = lt.t
        {
            t = lt_t.to_json();
            age_t = t.clone();
        }
        let w = why.as_str().expect("validated: a truthy why is a string");
        let text = if w.is_empty() { "Waiting on a background command".to_string() } else { cap_first(w) };
        let count_out = match count {
            J::Bool(b) => Out::Bool(*b),
            J::Number(n) if n.as_i64().is_some() => count.to_json(),
            _ => Out::Null,
        };
        Ok(obj(vec![
            ("itemId", Out::Str(format!("awaiting:{}", self.fsid))),
            ("sid", Out::Str(self.fsid.to_string())),
            ("name", self.name.clone()),
            ("color", self.color.clone()),
            ("text", Out::Str(text)),
            ("t", t),
            ("live", Out::Bool(live)),
            ("_ageT", age_t),
            ("turnId", Out::Null),
            ("origin", Out::Null),
            ("followupPending", Out::Null),
            ("summary", Out::Null),
            ("blockSummary", Out::Null),
            ("background", Out::Null),
            ("blocked", Out::Null),
            ("column", Out::Str("working".into())),
            ("board", Out::Str("feed".into())),
            ("category", Out::Str("working".into())),
            (
                "awaiting",
                obj(vec![
                    ("why", why.to_json()),
                    ("kind", kind.to_json()),
                    ("since", since.to_json()),
                    ("count", count_out),
                    ("items", Out::Array(items.iter().map(ToJson::to_json).collect())),
                    ("tasks", self.rec.get("_awaiting_task_descs", &self.rec.rec().awaiting_task_descs)?.to_json()),
                ]),
            ),
            ("provisional", Out::Bool(true)),
            ("judging", Out::Bool(false)),
            ("tree", Out::Array(Vec::new())),
        ]))
    }
}

/// Python's truthiness of an answer value.
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

/// `(bsub, bt) > (best[0], best[1])`: Python's tuple comparison (a bool first, False < True, then the time).
trait TupleCmp {
    fn cmp_py(&self, other: &Self) -> Ordering;
}

impl TupleCmp for (bool, Num) {
    fn cmp_py(&self, other: &Self) -> Ordering {
        self.0.cmp(&other.0).then_with(|| self.1.py_cmp(other.1))
    }
}

/// `healed[k] = v`: an existing key keeps its place.
fn set_healed(h: &mut Healed, k: &str, v: (Option<String>, Born)) {
    match h.0.iter_mut().find(|(key, _)| key == k) {
        Some(slot) => slot.1 = v,
        None => h.0.push((k.to_string(), v)),
    }
}

/// `{k: v for k, v in born.items() if k != "hidden"}`.
fn born_without_hidden(b: &Born) -> Born {
    let hidden = Born::KEYS.iter().position(|k| *k == "hidden").expect("a typed key") as u8;
    let mut out = b.clone();
    out.hidden = None;
    out.order.retain(|&i| i != hidden);
    out
}

/// kernel._card_warn_rows(rows, fsid, subtree, placements, cap): the debug rows that belong on one card (its `goal`
/// names a node of the subtree, or its `seg` resolves into it through the placements under any phase), the last `cap`.
pub fn card_warn_rows(
    rows: &[J],
    fsid: &str,
    subtree: &HashSet<&str>,
    placements: Option<&OMap<Option<String>>>,
    cap: usize,
) -> Vec<Out> {
    let mut out = Vec::new();
    for r in rows {
        let Some(m) = r.as_object() else { continue };
        if m.get("fsid").and_then(J::as_str) != Some(fsid) {
            continue;
        }
        let mut hit = match m.get("goal") {
            Some(J::String(g)) => subtree.contains(g.as_str()),
            Some(J::Array(g)) => g.iter().any(|x| x.as_str().is_some_and(|x| subtree.contains(x))),
            _ => false,
        };
        if !hit
            && let Some(seg) = m.get("seg").filter(|s| py_truthy(s)).and_then(J::as_str)
        {
            for suf in ["", "#p", "#live", "#d"] {
                let v = placements.and_then(|p| p.get(&format!("{seg}{suf}"))).and_then(Option::as_deref);
                if v.is_some_and(|v| subtree.contains(v)) {
                    hit = true;
                    break;
                }
            }
        }
        if hit {
            out.push(r.to_json());
        }
    }
    let n = out.len();
    out.split_off(n.saturating_sub(cap))
}

// ───────────────────────────── what the loader admits for this layer ─────────────────────────────

fn is_num(v: &J) -> bool {
    matches!(v, J::Number(_))
}

/// Refuse the inputs found that layer 6 would read differently from Python and does not mirror, each with the path of
/// the refused part. None is in either world. This is not every such shape by construction: S4.6's review found one
/// it had missed (a current ask's truthy `question` or `header` that is not a string, read after the `try`), which the
/// port now mirrors with Python's AttributeError rather than refusing. Python answers most of these (or raises in ways
/// the port does not mirror):
/// - a session without a `store` key (the replay's key raises KeyError), a truthy `flags` that is not a dict;
/// - a wait-graph entry that is not a dict with a number `since`; a stall record or a judge-auth entry that is truthy
///   and not a dict, a judge-auth entry with a truthy `login` (build_feed resolves its label through the SDK, which is
///   not replayed) or a `loginLabel` that is truthy and not a string; debug rows (when debug is on) that are not a list
///   of dicts, a row whose `seg` is truthy and not a string or whose `goal` list holds a list or a dict (unhashable);
/// - `confirming` items that are not strings;
/// - a node's `summaryAnchors` that is truthy and not a list, or holds a truthy item that is not a dict with `a`;
/// - recorded answers: a session-awaiting answer that is truthy and not a dict, whose truthy `why` is not a string,
///   whose truthy `items` is not a list, or whose `peers` list holds a truthy sid that is not a string (`str()` of
///   it); an owner-tops answer that is not a dict of `{since: number, descs: [str]}`; a current-ask answer that is
///   truthy and not a dict.
pub fn validate(snap: &Snapshot) -> Result<(), String> {
    let board = snap.board.as_ref().unwrap_or(&EMPTY_BOARD);
    for (sid, w) in board.wmap.iter().flat_map(OMap::iter) {
        if !w.as_object().is_some_and(|o| o.get("since").is_some_and(is_num)) {
            return Err(format!("board.wmap.{sid}: a wait that is not a dict with a number since"));
        }
    }
    for (nid, r) in board.stalls.iter().flat_map(OMap::iter) {
        if py_truthy(r) && !r.is_object() {
            return Err(format!("board.stalls.{nid}: a truthy stall record that is not a dict"));
        }
    }
    for (sid, j) in board.jauth.iter().flat_map(OMap::iter) {
        if !py_truthy(j) {
            continue;
        }
        let Some(o) = j.as_object() else { return Err(format!("board.jauth.{sid}: a truthy entry that is not a dict")) };
        if o.get("login").is_some_and(py_truthy) {
            return Err(format!("board.jauth.{sid}: a login whose label build_feed resolves through the SDK (not replayed)"));
        }
        if o.get("loginLabel").is_some_and(|l| py_truthy(l) && !l.is_string()) {
            return Err(format!("board.jauth.{sid}.loginLabel: a truthy label that is not a string"));
        }
    }
    if flag(&board.debug) {
        let rows = board.debug_rows.as_ref().and_then(J::as_array);
        let Some(rows) = rows else { return Err("board.debugRows: debug is on and the rows are not a list".into()) };
        for (i, r) in rows.iter().enumerate() {
            let Some(o) = r.as_object() else { return Err(format!("board.debugRows[{i}]: a row that is not a dict")) };
            if o.get("seg").is_some_and(|s| py_truthy(s) && !s.is_string()) {
                return Err(format!("board.debugRows[{i}].seg: a truthy seg that is not a string"));
            }
            if let Some(J::Array(g)) = o.get("goal")
                && g.iter().any(|x| x.is_array() || x.is_object())
            {
                return Err(format!("board.debugRows[{i}].goal: a list holding an unhashable value"));
            }
        }
    }
    for (sid, s) in snap.sessions.iter().flat_map(OMap::iter) {
        let at = format!("sessions.{sid}");
        if s.store.is_none() {
            return Err(format!("{at}: no store key"));
        }
        if s.flags.as_ref().is_some_and(|f| py_truthy(f) && !f.is_object()) {
            return Err(format!("{at}.flags: truthy and not a dict"));
        }
        if let Some(Some(st)) = &s.store {
            validate_store(&format!("{at}.store"), st)?;
        }
        if let Some(rec) = &s.recorded {
            for (k, v) in rec.session_awaiting.iter().flat_map(OMap::iter) {
                let p = format!("{at}.recorded.sessionAwaiting.{k}");
                if !py_truthy(v) {
                    continue;
                }
                let Some(o) = v.as_object() else { return Err(format!("{p}: truthy and not a dict")) };
                if o.get("why").is_some_and(|w| py_truthy(w) && !w.is_string()) {
                    return Err(format!("{p}.why: truthy and not a string"));
                }
                if o.get("items").is_some_and(|w| py_truthy(w) && !w.is_array()) {
                    return Err(format!("{p}.items: truthy and not a list"));
                }
                if let Some(J::Array(peers)) = o.get("peers") {
                    for (i, d) in peers.iter().enumerate() {
                        let sidv = match d {
                            J::Object(m) => m.get("sid").cloned().unwrap_or(J::Null),
                            other => other.clone(),
                        };
                        if py_truthy(&sidv) && !sidv.is_string() {
                            return Err(format!("{p}.peers[{i}]: a truthy sid that is not a string"));
                        }
                    }
                } else if o.get("peers").is_some_and(py_truthy) {
                    return Err(format!("{p}.peers: truthy and not a list"));
                }
            }
            if let Some(v) = &rec.bg_owner_tops {
                let ok = v.as_object().is_some_and(|m| {
                    m.values().all(|r| {
                        r.as_object().is_some_and(|r| {
                            r.get("since").is_some_and(is_num)
                                && r.get("descs").and_then(J::as_array).is_some_and(|d| d.iter().all(J::is_string))
                        })
                    })
                });
                if !ok {
                    return Err(format!("{at}.recorded.bgOwnerTops: not a dict of {{since: number, descs: [str]}}"));
                }
            }
            if let Some(v) = &rec.current_ask
                && py_truthy(v)
                && !v.is_object()
            {
                return Err(format!("{at}.recorded.currentAsk: truthy and not a dict"));
            }
        }
    }
    Ok(())
}

fn validate_store(path: &str, st: &Store) -> Result<(), String> {
    if st.confirming.iter().flatten().any(|c| !c.is_string()) {
        return Err(format!("{path}.confirming: an item that is not a string"));
    }
    for (nid, nd) in st.nodes.iter().flat_map(OMap::iter) {
        match &nd.summary_anchors {
            Some(J::Array(items)) => {
                for (i, e) in items.iter().enumerate() {
                    if py_truthy(e) && !e.as_object().is_some_and(|m| m.contains_key("a")) {
                        return Err(format!("{path}.nodes.{nid}.summaryAnchors[{i}]: a truthy item that is not a dict with a"));
                    }
                }
            }
            Some(v) if py_truthy(v) => {
                return Err(format!("{path}.nodes.{nid}.summaryAnchors: truthy and not a list"));
            }
            _ => {}
        }
    }
    Ok(())
}

// ───────────────────────────── a case of the edge set and the fuzz ─────────────────────────────

/// One case: a small world (a snapshot and its trees) and Python's entry for each alive session, in alive order.
pub struct Case {
    pub snapshot: Snapshot,
    pub trees: Vec<(String, Option<Tree>)>,
}

impl Case {
    /// Load a case's `snapshot` and `trees` (sid to a tree or null; an alive row without a tree name has none).
    pub fn load(case: &J) -> Result<Case, String> {
        let snapshot: Snapshot = serde_json::from_value(case["snapshot"].clone()).map_err(|e| format!("snapshot: {e}"))?;
        crate::snapshot::validate(&snapshot).map_err(|e| format!("snapshot: {e}"))?;
        validate(&snapshot).map_err(|e| format!("snapshot: {e}"))?;
        let mut trees = Vec::new();
        for row in snapshot.alive() {
            let sid = row.sid.clone().expect("required at load");
            let t = match case["trees"].get(&sid) {
                Some(v) if !v.is_null() => {
                    let tree: Tree = serde_json::from_value(v.clone()).map_err(|e| format!("trees.{sid}: {e}"))?;
                    crate::tree::validate(&tree).map_err(|e| format!("trees.{sid}: {e}"))?;
                    Some(tree)
                }
                _ => None,
            };
            trees.push((sid, t));
        }
        Ok(Case { snapshot, trees })
    }
}

/// An entry's answer as the oracle writes it: the entry, or `{"raises": name, "arg": str(e)}`.
pub fn answer_json(got: &Result<Out, Raise>) -> Out {
    match got {
        Ok(v) => v.clone(),
        Err(r) => r.to_json(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plants_parse_by_name() {
        for p in Plant::ALL {
            assert_eq!(Plant::parse(p.name()), Some(p));
        }
        assert_eq!(Plant::parse("nope"), None);
    }

    #[test]
    fn python_readings() {
        assert_eq!(cap_first("ßeta"), "SSeta", "str.upper() is the full case mapping");
        assert_eq!(cap_first("waiting"), "Waiting");
        assert_eq!(cap_first(""), "");
        assert_eq!(cap_first("\u{264}ait"), "\u{264}ait", "Python's Unicode 15 has no uppercase for U+0264");
        let j = |s: &str| serde_json::from_str::<J>(s).unwrap();
        assert_eq!(or_empty_strip(Some(&j(r#"" Plan ""#))), Ok("Plan"));
        assert_eq!(or_empty_strip(Some(&j("0"))).and(or_empty_strip(Some(&j("[]")))).and(or_empty_strip(None)), Ok(""));
        for (v, name) in [("5", "int"), ("2.5", "float"), ("true", "bool"), ("[\"a\"]", "list"), ("{\"a\": 1}", "dict")] {
            let e = or_empty_strip(Some(&j(v))).unwrap_err();
            assert_eq!((e.exc, e.arg), ("AttributeError", format!("'{name}' object has no attribute 'strip'")));
        }
        assert_eq!(py_int(Num::Float(-2.5)), Num::Int(-2));
        assert_eq!(py_int_out(Num::Float(1e20)), Out::BigInt("100000000000000000000".into()));
        assert_eq!(py_min(Num::Int(5), Num::Float(5.0)), Num::Int(5), "min keeps the first of equal values");
        assert_eq!(or0(Some(Num::Float(0.0))), Num::Int(0), "`x or 0` yields the literal 0");
        assert_eq!(rstrip("ab\u{1c} "), "ab");
        assert_eq!((false, Num::Int(9)).cmp_py(&(true, Num::Int(1))), Ordering::Less, "False < True first");
        assert_eq!(not_recorded("_session_awaiting", "s1", Some("idle=true")).arg, "_session_awaiting(s1,idle=true)");
    }

    #[test]
    fn warn_rows_join_by_goal_and_by_placement() {
        let rows: Vec<J> = serde_json::from_str(
            r##"[{"fsid": "s", "goal": "g1"}, {"fsid": "x", "goal": "g1"}, {"fsid": "s", "goal": ["z", "g2"]},
                 {"fsid": "s", "seg": "k"}, {"fsid": "s", "seg": "m"}, {"fsid": "s", "goal": 5}]"##,
        )
        .unwrap();
        let sub: HashSet<&str> = ["g1", "g2", "g3"].into_iter().collect();
        let p: OMap<Option<String>> = serde_json::from_str(r#"{"k#live": "g3", "m": null, "m#d": "zz"}"#).unwrap();
        let got = card_warn_rows(&rows, "s", &sub, Some(&p), 20);
        assert_eq!(got.len(), 3);
        assert_eq!(card_warn_rows(&rows, "s", &sub, Some(&p), 2).len(), 2, "the last cap rows");
    }
}
