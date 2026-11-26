//! Layer 4 of rust/notes/s2-design.md section 2, "Store helpers", over the typed goal store, the recorded helper
//! answers and the peer facts: the per-node and per-store readers the per-card body calls between its anchor walk
//! (layer 3) and its card literal (layer 6). Each function follows its Python line for line:
//! - kernel._agent_open_set, _parked_rows, _focus_open_top with jd._top_of, _goal_awaiting_stamp_full with the unlisted
//!   _peer_stamp_superseded and _stamp_written_at, _open_leaves, _all_outstanding_delegated;
//! - _handoff_card_fields with the unlisted _title_shaped and _HANDOFF_LABEL_RE, _handoff_peer_identities,
//!   _peer_identity (the whole ladder: the registry, then the attached hosts' snapshot and the postal log's pairing as
//!   recorded inputs, `PeerWorld`, then the composite and the stub), _awaiting_item and _awaiting_peer_items;
//! - _newest_block_ev_t, jd._block_ev_t, _block_since, _done_since and review_boundary;
//! - _session_started_face, _node_log_rows, _node_anchor_uuids with jd.junk_quote, _landing_inputs,
//!   _login_refusal_label, and _name_color and _name_of over _names_parts (the snapshot's names, `Names`);
//! - the body's own `children` build (the healed parents, the hidden set, a born step whose parent is gone), which
//!   these helpers take as input, and its `_subtree` walk.
//!
//! What a naive port would get wrong, each pinned by the edge cases (tools/rust-spike/dump_layer4.py --edge-cases) and
//! the randomized check (--fuzz):
//! - `max` keeps the first of equal values, and the int/float tag rides through (`max(5, 5.0)` is 5). The awaiting
//!   stamp is the `max` over tuples `(awaitingAt or 0, why, kind or "", peers)`, so equal anchors break ties on the
//!   strings, then on the peer lists, and a full tie keeps the first stamp the stack walk meets (its tag).
//! - `int(since)` truncates toward zero, at any size (1e20 is an int of 21 digits).
//! - `x or y` skips every falsy value and yields its last operand when all are: `ev_t or at` (a zero ev_t falls to
//!   at), `promptUuid or seg_trig[...]` (an empty uuid falls through), `mt or t or 0` and a done row's
//!   `ev_t or at or 0` (all falsy: the literal int 0, never the node's 0.0 or -0.0), and _parked_rows' `(t or 0) > t0`
//!   (a sibling with no t reads as 0, younger than a negative t). _done_since keeps a done row with no time as 0 (its
//!   list is not filtered), where _block_ev_t drops a falsy time.
//! - `is None` against truthiness: an own `born: {}` is not None, so the heal's record is never read and the face is
//!   None; an empty node id is never written to or read from the anchor table (`if nid`), while an empty work anchor
//!   is warm (`is not None`).
//! - Dicts keep insertion order: the children map's keys in first-seen order, _parked_rows in that order,
//!   _open_leaves in document order, _handoff_peer_identities sorted by name, stably. The stamp's peers keep their
//!   repeats (one identity per listed key); only _handoff_peer_identities deduplicates.
//! - Absent and null: an awaiting item writes `agentId`, `detail` and `stoppable` only when truthy (never null);
//!   _node_log_rows answers None for no rows, not [].
//! - The body's own children feed _agent_open_set: an open to-do the heal re-parents marks its new parent, and one on
//!   a hidden top marks nothing.
//! - A str regex's `\s` is Unicode (`pystr::is_space`): the title's sentence cut takes U+001C, U+0085, U+00A0 or
//!   U+3000 after the stop. Splits: the handoff label's `host:name` at its last colon (`rpartition`), the postal log's
//!   pairing at its first (`partition`).
//! - _UUIDISH_RE ends in `$`, which in Python also matches before one trailing newline, so `"<uuid>\n"` is uuid-shaped.
//! - jd.junk_quote lowercases with Python's Unicode `lower()`: the Kelvin sign (U+212A) lowers to an ASCII "k", so a
//!   quote "o\u{212a}" is the stub "ok". It is the only code point outside ASCII whose lowercase is all ASCII.
//! - _node_anchor_uuids keeps state across calls: a warm resolve is remembered by node id (`AnchorTable`, Python's
//!   _node_anchor_last, which lives for the kernel run), and a later cold lookup of the same id serves it, whichever
//!   session it came from. The feed passes no sid, so _node_anchor_rev is not ported.
//!
//! Recursion: _agent_open_set and _open_leaves recurse once per tree level in Python, which raises RecursionError near
//! 1,000. Here every walk is iterative, and the loader refuses a parent chain past MAX_NODE_DEPTH (500), as does the
//! body's children build (`body_children`), so the port answers only where Python does. _goal_awaiting_stamp_full and
//! the body's _subtree have no seen-set and loop forever on a node in a parentId cycle; the body calls them on card
//! tops only, which no cycle reaches. Here they stop at the first repeated node with `Err(Loops)`.
//!
//! _login_refusal_label raises AttributeError on a truthy api-error answer that is not a dict; that is `Err(Raise)`.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};

use romp_json::Value as Out;

use crate::layer1;
use crate::layer2::Raise;
use crate::layer3::{py_max, SegMaps};
use crate::pystr;
use crate::record::{py_truthy, Num, OMap, ToJson};
use crate::snapshot::{self, Born, Handoff, LiveRow, Node, Snapshot, Store, MAX_NODE_DEPTH};

/// kernel._AWAIT_ITEM_KINDS: the awaited-row groups, in display order (an assert in _awaiting_item).
pub const AWAIT_ITEM_KINDS: [&str; 5] = ["agents", "commands", "watches", "peer", "timer"];
/// jd._JUNK_QUOTES: the continuation stubs.
pub const JUNK_QUOTES: [&str; 19] = [
    "retry", "try again", "continue", "keep going", "go", "go ahead", "ok", "okay", "k", "yes", "y", "yeah", "sure",
    "proceed", "please continue", "carry on", "resume", "keep at it", "continue from where you left off",
];
/// _title_shaped's default cap (the planter's card-title length).
pub const TITLE_CAP: usize = 120;
/// A sid no registry or store names: the name queries' negative case.
pub const ABSENT_SID: &str = "absent-sid";
/// The distill states and columns the body hands _landing_inputs (its modal row and its card).
pub const LANDING_STATES: [Option<&str>; 3] = [Some("completed"), Some("blocked"), None];
pub const LANDING_COLUMNS: [&str; 4] = ["", "working", "completed", "needs_input"];
/// _node_log_rows' default cap.
pub const LOG_CAP: usize = 8;

/// One deliberate mistake, for `romp-spike layer4 --plant`: the comparison must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// The awaiting stamp's `max` keeps the last of equal candidates.
    MaxKeepsLast,
    /// _awaiting_item rounds `since` to the nearest integer instead of truncating it.
    IntRounds,
    /// _UUIDISH_RE's `$` matches only at the very end (no trailing newline).
    UuidStrict,
    /// _node_anchor_uuids remembers nothing across calls.
    TableOff,
    /// _parked_rows answers in node-id order instead of the children map's order.
    ParkedSorted,
}

impl Plant {
    pub const ALL: [Plant; 5] =
        [Plant::MaxKeepsLast, Plant::IntRounds, Plant::UuidStrict, Plant::TableOff, Plant::ParkedSorted];

    pub fn parse(name: &str) -> Option<Plant> {
        Plant::ALL.into_iter().find(|p| p.name() == name)
    }

    pub fn name(self) -> &'static str {
        match self {
            Plant::MaxKeepsLast => "max-last",
            Plant::IntRounds => "int-round",
            Plant::UuidStrict => "uuid-strict",
            Plant::TableOff => "table-off",
            Plant::ParkedSorted => "parked-sorted",
        }
    }
}

// ───────────────────────────── small Python readings ─────────────────────────────

fn truthy_str(s: Option<&str>) -> bool {
    s.is_some_and(|s| !s.is_empty())
}

/// `a or b` over strings ("" is falsy), owned.
fn or_str(a: Option<String>, b: Option<String>) -> Option<String> {
    if truthy_str(a.as_deref()) { a } else { b }
}

/// The first truthy number of `xs` (`x or y or ...`), else the last (Python's `or` yields its last operand).
fn or_nums(xs: &[Option<Num>]) -> Option<Num> {
    xs.iter().copied().find(|x| x.is_some_and(|n| n.truthy())).unwrap_or_else(|| xs.last().copied().flatten())
}

fn s2(x: &Option<Option<String>>) -> Option<&str> {
    x.as_ref().and_then(|v| v.as_deref())
}

fn n2(x: &Option<Option<Num>>) -> Option<Num> {
    x.and_then(|v| v)
}

fn flag(x: &Option<bool>) -> bool {
    *x == Some(true)
}

fn truthy_json(x: &Option<serde_json::Value>) -> bool {
    x.as_ref().is_some_and(py_truthy)
}

/// `s[:n]` in code points.
fn head(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map_or(s, |(i, _)| &s[..i])
}

/// `" ".join(s.split())`: Python's whitespace runs (pystr::is_space) collapsed to one space, the ends dropped.
fn collapse_space(s: &str) -> String {
    s.split(pystr::is_space).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ")
}

fn parent_id(nd: &Node) -> Option<&str> {
    s2(&nd.parent_id)
}

/// `isinstance(nd.get("handoff"), dict)`.
fn handoff(nd: &Node) -> Option<&Handoff> {
    match &nd.handoff {
        Some(Some(h)) => Some(h),
        _ => None,
    }
}

/// `nd.get("born") if isinstance(nd.get("born"), dict) else None`.
fn born(nd: &Node) -> Option<&Born> {
    match &nd.born {
        Some(Some(b)) => Some(b),
        _ => None,
    }
}

/// `int(x)` for a number: an int as is, a float truncated toward zero, exactly at any size.
fn py_int(n: Num, plant: Option<Plant>) -> Out {
    match n {
        Num::Int(i) => Out::Int(i),
        Num::Float(f) => {
            let t = if plant == Some(Plant::IntRounds) { f.round() } else { f.trunc() };
            if t.abs() < 9_223_372_036_854_775_808.0 {
                Out::Int(t as i64)
            } else {
                Out::BigInt(Num::Float(t).percent_d())
            }
        }
    }
}

/// Python's type name of a JSON value (for an AttributeError's message).
fn py_type(v: &serde_json::Value) -> &'static str {
    use serde_json::Value as J;
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

// ───────────────────────────── the store's shapes ─────────────────────────────

/// A store's nodes with an index by key (`nodes.get(k)`), iterated in store order.
pub struct Ns<'a> {
    map: &'a OMap<Node>,
    index: HashMap<&'a str, &'a Node>,
}

impl<'a> Ns<'a> {
    pub fn new(map: &'a OMap<Node>) -> Self {
        Ns { map, index: map.iter().map(|(k, v)| (k.as_str(), v)).collect() }
    }

    pub fn get(&self, k: &str) -> Option<&'a Node> {
        self.index.get(k).copied()
    }

    pub fn contains(&self, k: &str) -> bool {
        self.index.contains_key(k)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&'a str, &'a Node)> + '_ {
        self.map.iter().map(|(k, v)| (k.as_str(), v))
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }
}

/// A children map: a parent key (None for the roots) to its child ids, keys in first-seen order, as the dict
/// `children.setdefault(parent, []).append(nid)` builds.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Children {
    entries: Vec<(Option<String>, Vec<String>)>,
    index: HashMap<Option<String>, usize>,
}

impl Children {
    fn push(&mut self, k: Option<&str>, nid: &str) {
        let key = k.map(str::to_string);
        match self.index.get(&key) {
            Some(&i) => self.entries[i].1.push(nid.to_string()),
            None => {
                self.index.insert(key.clone(), self.entries.len());
                self.entries.push((key, vec![nid.to_string()]));
            }
        }
    }

    /// `children.get(k, [])`.
    pub fn get(&self, k: Option<&str>) -> &[String] {
        self.index.get(&k.map(str::to_string)).map_or(&[], |&i| &self.entries[i].1)
    }

    pub fn iter(&self) -> impl Iterator<Item = (Option<&str>, &[String])> {
        self.entries.iter().map(|(k, v)| (k.as_deref(), v.as_slice()))
    }

    /// `children.setdefault(nd.get("parentId"), []).append(nid)` over the nodes in store order (_open_leaves, and
    /// _goal_awaiting_stamp_full without its `children` argument).
    pub fn from_parents(ns: &Ns<'_>) -> Children {
        let mut c = Children::default();
        for (nid, nd) in ns.iter() {
            c.push(parent_id(nd), nid);
        }
        c
    }

    /// `[[parent or null, [child, ...]], ...]` in the dict's order.
    pub fn to_json(&self) -> Out {
        Out::Array(
            self.entries
                .iter()
                .map(|(k, v)| {
                    Out::Array(vec![k.clone().to_json(), Out::Array(v.iter().map(|s| Out::Str(s.clone())).collect())])
                })
                .collect(),
        )
    }
}

/// kernel._heal_session_tops' answer as recorded (`healSessionTops`): node id to `(new parent or None, born record)`.
pub type Healed = OMap<(Option<String>, Born)>;

/// The longest root-to-leaf path of a children forest, in nodes (from `children[None]`; no cycle is reachable from a
/// root, since each node has one parent key).
fn forest_depth(c: &Children) -> usize {
    let mut best = 0;
    let mut stack: Vec<(&str, usize)> = c.get(None).iter().map(|s| (s.as_str(), 1)).collect();
    while let Some((x, d)) = stack.pop() {
        best = best.max(d);
        stack.extend(c.get(Some(x)).iter().map(|k| (k.as_str(), d + 1)));
    }
    best
}

/// The body's `children` (kernel._feed_session_entry, after the heal): each node under its healed parent when the heal
/// names one (truthy), else its own `parentId`; a born step whose parent is gone is a root; a node the heal marks
/// hidden is left out. Also the hidden ids, sorted. `Err` when the forest is deeper than MAX_NODE_DEPTH.
pub fn body_children(ns: &Ns<'_>, healed: &Healed) -> Result<(Children, Vec<String>), String> {
    let hidden: HashSet<&str> =
        healed.iter().filter(|(_, (_, b))| truthy_json(&b.hidden)).map(|(k, _)| k.as_str()).collect();
    let mut c = Children::default();
    for (nid, nd) in ns.iter() {
        if hidden.contains(nid) {
            continue;
        }
        let mut pk = match healed.get(nid) {
            Some((Some(p), _)) if !p.is_empty() => Some(p.as_str()),
            _ => parent_id(nd),
        };
        if pk.is_some_and(|p| !ns.contains(p)) && born(nd).is_some() {
            pk = None;
        }
        c.push(pk, nid);
    }
    let depth = forest_depth(&c);
    if depth > MAX_NODE_DEPTH {
        return Err(format!(
            "the healed children forest is {depth} nodes deep, past the {MAX_NODE_DEPTH} the port admits"
        ));
    }
    let mut hidden: Vec<String> = hidden.into_iter().map(str::to_string).collect();
    hidden.sort_unstable();
    Ok((c, hidden))
}

/// A walk that met a node twice: Python's stack walk has no seen-set and never ends there.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Loops;

/// The body's `_subtree(root)`: every id at or under `root`, pre-order by a stack (the last child first).
pub fn subtree(c: &Children, root: &str) -> Result<Vec<String>, Loops> {
    let (mut stack, mut acc, mut seen) = (vec![root.to_string()], Vec::new(), HashSet::new());
    while let Some(x) = stack.pop() {
        if !seen.insert(x.clone()) {
            return Err(Loops);
        }
        stack.extend(c.get(Some(&x)).iter().cloned());
        acc.push(x);
    }
    Ok(acc)
}

// ───────────────────────────── the tree walks ─────────────────────────────

/// `(nd.get("agentTask") or {}).get("status") == "open"`.
fn agent_open_own(nd: Option<&Node>) -> bool {
    nd.and_then(|n| n.agent_task.as_ref())
        .filter(|a| a.is_object())
        .and_then(|a| a.get("status"))
        .is_some_and(|s| s.as_str() == Some("open"))
}

/// kernel._agent_open_set(nodes, children): every id that is, or has a descendant that is, an agentTask-open node,
/// walked from the roots (`children[None]`), every child visited. Iterative post-order.
pub fn agent_open_set(ns: &Ns<'_>, c: &Children) -> HashSet<String> {
    let mut open = HashSet::new();
    for top in c.get(None) {
        let mut stack: Vec<(&str, &[String], usize, bool)> =
            vec![(top.as_str(), c.get(Some(top)), 0, agent_open_own(ns.get(top)))];
        while let Some(fr) = stack.last_mut() {
            if fr.2 < fr.1.len() {
                let k = fr.1[fr.2].as_str();
                fr.2 += 1;
                stack.push((k, c.get(Some(k)), 0, agent_open_own(ns.get(k))));
            } else {
                let (nid, _, _, r) = stack.pop().expect("a frame");
                if r {
                    open.insert(nid.to_string());
                    if let Some(parent) = stack.last_mut() {
                        parent.3 = true;
                    }
                }
            }
        }
    }
    open
}

/// kernel._parked_rows(nodes, children): `{nid: n}` for every open row (not complete, blocked or cleared, no handoff
/// and no unsynthesized log row anywhere in its subtree) that `n >= 1` younger siblings (`t or 0` greater) with a
/// handoff in their subtrees have leapfrogged; in the children map's order. The subtree tests are cycle-safe.
pub fn parked_rows(ns: &Ns<'_>, c: &Children) -> Vec<(String, usize)> {
    parked_with(ns, c, None)
}

fn parked_with(ns: &Ns<'_>, c: &Children, plant: Option<Plant>) -> Vec<(String, usize)> {
    let mut hmemo: HashMap<String, bool> = HashMap::new();
    let mut rmemo: HashMap<String, bool> = HashMap::new();
    fn sub_has(ns: &Ns<'_>, c: &Children, x: &str, memo: &mut HashMap<String, bool>, hit: &dyn Fn(&Node) -> bool)
        -> bool {
        if let Some(&f) = memo.get(x) {
            return f;
        }
        let (mut seen, mut stack, mut found) = (HashSet::new(), vec![x.to_string()], false);
        while let Some(y) = stack.pop() {
            if !seen.insert(y.clone()) {
                continue;
            }
            if ns.get(&y).is_some_and(hit) {
                found = true;
                break;
            }
            stack.extend(c.get(Some(&y)).iter().cloned());
        }
        memo.insert(x.to_string(), found);
        found
    }
    let has_handoff = |nd: &Node| handoff(nd).is_some();
    let has_row = |nd: &Node| nd.log.iter().flatten().any(|r| !truthy_json(&r.synth));
    let t_or_0 = |nd: Option<&Node>| nd.and_then(|n| n.t).filter(|t| t.truthy()).unwrap_or(Num::Int(0));
    let mut out = Vec::new();
    for (_, kids) in c.iter() {
        if kids.len() < 2 {
            continue;
        }
        for nid in kids {
            let nd = ns.get(nid);
            if nd.is_some_and(|n| flag(&n.node_complete) || flag(&n.blocked) || flag(&n.cleared))
                || sub_has(ns, c, nid, &mut hmemo, &has_handoff)
                || sub_has(ns, c, nid, &mut rmemo, &has_row)
            {
                continue;
            }
            let t0 = t_or_0(nd);
            let mut n = 0;
            for sib in kids {
                if sib != nid
                    && t_or_0(ns.get(sib)).py_cmp(t0) == Ordering::Greater
                    && sub_has(ns, c, sib, &mut hmemo, &has_handoff)
                {
                    n += 1;
                }
            }
            if n > 0 {
                out.push((nid.clone(), n));
            }
        }
    }
    if plant == Some(Plant::ParkedSorted) {
        out.sort();
    }
    out
}

/// jd._top_of(nodes, nid): the top-level ancestor, cycle-safe (a repeat stops the climb where it is).
pub fn top_of(ns: &Ns<'_>, nid: Option<&str>) -> Option<String> {
    let mut top = nid.map(str::to_string);
    let mut seen = HashSet::new();
    loop {
        let parent = top.as_deref().and_then(|t| ns.get(t)).and_then(parent_id);
        let Some(p) = parent else { break };
        if !seen.insert(top.clone()) {
            break;
        }
        top = Some(p.to_string());
    }
    top
}

/// kernel._focus_open_top(nodes, focus, status): the open root above `focus` (its rollup status not completed or
/// cleared), else None.
pub fn focus_open_top(ns: &Ns<'_>, focus: Option<&str>, status: &OMap<String>) -> Option<String> {
    let f = top_of(ns, focus)?;
    let nd = ns.get(&f)?;
    let open = !matches!(status.get(&f).map(String::as_str), Some("completed" | "cleared"));
    (parent_id(nd).is_none() && open).then_some(f)
}

/// kernel._open_leaves(nodes, top_id): the open leaves under `top_id` in document order. Open: not cleared, and not
/// complete unless agentTask-open somewhere at or under it (_agent_open_set over the nodes' own parents). A seen-set
/// makes it cycle-safe; Python recurses, here an explicit stack keeps the same pre-order.
pub fn open_leaves(ns: &Ns<'_>, top: &str) -> Vec<String> {
    let c = Children::from_parents(ns);
    let aopen = agent_open_set(ns, &c);
    let open = |nid: &str| {
        ns.get(nid).is_some_and(|nd| !flag(&nd.cleared) && (!flag(&nd.node_complete) || aopen.contains(nid)))
    };
    // _walk(x): `if x in seen or not _open(x): return`; seen.add(x); its open kids (taken before any recursion),
    // each walked in order, else x is a leaf. Each frame is a node's open kids and the next one to walk.
    fn visit<'c>(
        x: &'c str,
        c: &'c Children,
        open: &dyn Fn(&str) -> bool,
        seen: &mut HashSet<&'c str>,
        stack: &mut Vec<(Vec<&'c str>, usize)>,
        leaves: &mut Vec<String>,
    ) {
        if seen.contains(x) || !open(x) {
            return;
        }
        seen.insert(x);
        let okids: Vec<&'c str> = c.get(Some(x)).iter().map(String::as_str).filter(|k| open(k)).collect();
        if okids.is_empty() {
            leaves.push(x.to_string());
        } else {
            stack.push((okids, 0));
        }
    }
    let mut leaves = Vec::new();
    let mut seen: HashSet<&str> = HashSet::new();
    let mut stack: Vec<(Vec<&str>, usize)> = Vec::new();
    visit(top, &c, &open, &mut seen, &mut stack, &mut leaves);
    while let Some(fr) = stack.last_mut() {
        if fr.1 < fr.0.len() {
            let k = fr.0[fr.1];
            fr.1 += 1;
            visit(k, &c, &open, &mut seen, &mut stack, &mut leaves);
        } else {
            stack.pop();
        }
    }
    leaves
}

/// kernel._all_outstanding_delegated(nodes, top_id): every open leaf is a handoff node (False with no leaves).
pub fn all_outstanding_delegated(ns: &Ns<'_>, top: &str) -> bool {
    let leaves = open_leaves(ns, top);
    !leaves.is_empty() && leaves.iter().all(|nid| ns.get(nid).and_then(handoff).is_some())
}

// ───────────────────────────── the awaiting stamp ─────────────────────────────

/// What _peer_answered hands _goal_awaiting_stamp_full: the live helper's tuple `(newest answered time, {peer key:
/// time})`, or a legacy scalar (kept pair-blind).
#[derive(Debug, Clone, Copy)]
pub enum Answered<'a> {
    Pair(Num, &'a OMap<Num>),
    Scalar(Num),
}

/// The freshest live stamp: `(awaitingAt or 0, why, kind or None, peers)`.
#[derive(Debug, Clone, PartialEq)]
pub struct Stamp {
    pub at: Num,
    pub why: String,
    pub kind: Option<String>,
    pub peers: Vec<String>,
}

impl Stamp {
    pub fn to_json(&self) -> Out {
        Out::Array(vec![
            self.at.to_json(),
            Out::Str(self.why.clone()),
            self.kind.clone().to_json(),
            Out::Array(self.peers.iter().map(|p| Out::Str(p.clone())).collect()),
        ])
    }
}

/// kernel._stamp_written_at(nd): the newest `at or ev_t or 0` over the node's awaiting rows that are not lifts, floored
/// at `awaitingAt or 0` (`max`, the first of equal values kept).
pub fn stamp_written_at(nd: &Node) -> Num {
    let mut best = Num::Int(0);
    for e in nd.log.iter().flatten() {
        if s2(&e.kind) != Some("awaiting") || truthy_json(&e.lift) {
            continue;
        }
        best = py_max(best, or_nums(&[n2(&e.at), n2(&e.ev_t)]).filter(|n| n.truthy()).unwrap_or(Num::Int(0)));
    }
    py_max(best, n2(&nd.awaiting_at).filter(|n| n.truthy()).unwrap_or(Num::Int(0)))
}

/// kernel._peer_stamp_superseded(nd, answered): the superseding answer's time, or 0. Only a peer-kind or kindless stamp
/// with an anchor can be superseded, by an answer (the newest over its named peers when the tuple carries the pair
/// map, else the newest from anyone) written after the stamp.
pub fn peer_stamp_superseded(nd: &Node, answered: &Answered<'_>) -> Num {
    if !matches!(s2(&nd.awaiting_kind), None | Some("peer")) {
        return Num::Int(0);
    }
    let (any_t, per) = match *answered {
        Answered::Pair(t, per) => (t, Some(per)),
        Answered::Scalar(t) => (if t.truthy() { t } else { Num::Int(0) }, None),
    };
    let peers = nd.awaiting_peers.as_ref().and_then(|p| p.as_deref()).filter(|p| !p.is_empty());
    let ans = match (peers, per) {
        (Some(peers), Some(per)) => peers
            .iter()
            .map(|pk| per.get(pk).copied().unwrap_or(Num::Int(0)))
            .reduce(py_max)
            .expect("a non-empty peer list"),
        _ => any_t,
    };
    let at = n2(&nd.awaiting_at).filter(|n| n.truthy());
    if !(at.is_some() && ans.truthy() && stamp_written_at(nd).py_lt(ans)) {
        return Num::Int(0);
    }
    ans
}

type Cand<'n> = (Num, &'n str, &'n str, &'n [String]);

fn cand_cmp(a: &Cand<'_>, b: &Cand<'_>) -> Ordering {
    a.0.py_cmp(b.0).then_with(|| a.1.cmp(b.1)).then_with(|| a.2.cmp(b.2)).then_with(|| a.3.cmp(b.3))
}

/// kernel._goal_awaiting_stamp_full(nodes, top, children, answered_at): the `max` stamp of `top`'s subtree over the
/// nodes with a truthy awaitingWhy, not rolledUp and not superseded, walked by a stack (the last child first). With
/// no `children`, they are built from the nodes' own parents. `Err(Loops)` where Python's walk never ends.
pub fn goal_awaiting_stamp_full(
    ns: &Ns<'_>,
    top: &str,
    children: Option<&Children>,
    answered: &Answered<'_>,
) -> Result<Option<Stamp>, Loops> {
    stamp_with(ns, top, children, answered, None)
}

fn stamp_with(
    ns: &Ns<'_>,
    top: &str,
    children: Option<&Children>,
    answered: &Answered<'_>,
    plant: Option<Plant>,
) -> Result<Option<Stamp>, Loops> {
    let own;
    let c = match children {
        Some(c) => c,
        None => {
            own = Children::from_parents(ns);
            &own
        }
    };
    let mut best: Option<Cand<'_>> = None;
    let (mut stack, mut seen) = (vec![top.to_string()], HashSet::new());
    while let Some(x) = stack.pop() {
        let Some(nd) = ns.get(&x) else { continue };
        if !seen.insert(x.clone()) {
            return Err(Loops);
        }
        if let Some(why) = s2(&nd.awaiting_why).filter(|w| !w.is_empty())
            && !truthy_json(&nd.rolled_up)
            && !peer_stamp_superseded(nd, answered).truthy()
        {
            let at = n2(&nd.awaiting_at).filter(|n| n.truthy()).unwrap_or(Num::Int(0));
            let kind = s2(&nd.awaiting_kind).unwrap_or("");
            let peers = nd.awaiting_peers.as_ref().and_then(|p| p.as_deref()).unwrap_or_default();
            let cand: Cand<'_> = (at, why, kind, peers);
            best = Some(match best {
                None => cand,
                Some(b) => {
                    let o = cand_cmp(&cand, &b);
                    if o == Ordering::Greater || (plant == Some(Plant::MaxKeepsLast) && o == Ordering::Equal) {
                        cand
                    } else {
                        b
                    }
                }
            });
        }
        stack.extend(c.get(Some(&x)).iter().cloned());
    }
    Ok(best.map(|(at, why, kind, peers)| Stamp {
        at,
        why: why.to_string(),
        kind: (!kind.is_empty()).then(|| kind.to_string()),
        peers: peers.to_vec(),
    }))
}

// ───────────────────────────── block and done times ─────────────────────────────

/// jd._block_ev_t(nd): the newest truthy `ev_t or at` over the node's block rows, None without one.
pub fn block_ev_t(nd: &Node) -> Option<Num> {
    nd.log
        .iter()
        .flatten()
        .filter(|e| s2(&e.kind) == Some("block"))
        .filter_map(|e| or_nums(&[n2(&e.ev_t), n2(&e.at)]).filter(|t| t.truthy()))
        .reduce(py_max)
}

/// jd._block_since(nd): _block_ev_t, else `mt or t or 0`.
pub fn block_since(nd: &Node) -> Num {
    block_ev_t(nd).unwrap_or_else(|| or_nums(&[nd.mt, nd.t]).filter(|n| n.truthy()).unwrap_or(Num::Int(0)))
}

/// jd._done_since(nd): the newest `ev_t or at or 0` over the done rows (a row with no time counts as 0), else
/// `mt or t or 0`.
pub fn done_since(nd: &Node) -> Num {
    nd.log
        .iter()
        .flatten()
        .filter(|e| s2(&e.kind) == Some("done"))
        .map(|e| or_nums(&[n2(&e.ev_t), n2(&e.at)]).filter(|t| t.truthy()).unwrap_or(Num::Int(0)))
        .reduce(py_max)
        .unwrap_or_else(|| or_nums(&[nd.mt, nd.t]).filter(|n| n.truthy()).unwrap_or(Num::Int(0)))
}

/// kernel._newest_block_ev_t(nodes, ids): None when any node has no block row, else the newest (0 for no ids).
pub fn newest_block_ev_t(ns: &Ns<'_>, ids: &[String]) -> Option<Num> {
    let mut best: Option<Num> = None;
    for x in ids {
        let t = block_ev_t(ns.get(x).expect("the ids are the subtree's, every one a node"))?;
        best = Some(best.map_or(t, |b| py_max(b, t)));
    }
    Some(best.unwrap_or(Num::Int(0)))
}

/// jd.review_boundary(nd): deltaSince, advanced to distilledMt when the node has a summary, a reopen row after
/// distilledMt, and no later deltaSince.
pub fn review_boundary(nd: &Node) -> Option<Num> {
    let b = n2(&nd.delta_since);
    let Some(dm) = n2(&nd.distilled_mt).filter(|n| n.truthy()) else { return b };
    let summary = !pystr::strip(s2(&nd.summary).unwrap_or("")).is_empty();
    let reopened = nd.log.iter().flatten().any(|e| {
        s2(&e.kind) == Some("reopen")
            && n2(&e.ev_t).filter(|n| n.truthy()).unwrap_or(Num::Int(0)).py_cmp(dm) == Ordering::Greater
    });
    if summary && reopened && b.is_none_or(|b| dm.py_cmp(b) == Ordering::Greater) { Some(dm) } else { b }
}

// ───────────────────────────── names and peers ─────────────────────────────

/// The names registry as the replay scopes it (`_live_scope.names`): sid to its tab fields, the peers' entries then
/// the sessions' (a session's wins), a null entry skipped.
#[derive(Debug, Clone, Default)]
pub struct Names(pub HashMap<String, Vec<String>>);

impl Names {
    pub fn from_snapshot(snap: &Snapshot) -> Names {
        let mut m = HashMap::new();
        for (sid, p) in snap.peers.iter().flat_map(OMap::iter) {
            if let Some(Some(n)) = &p.names {
                m.insert(sid.clone(), n.clone());
            }
        }
        for (sid, s) in snap.sessions.iter().flat_map(OMap::iter) {
            if let Some(Some(n)) = &s.names {
                m.insert(sid.clone(), n.clone());
            }
        }
        Names(m)
    }

    /// kernel._names_parts(sid).
    pub fn parts(&self, sid: &str) -> Option<&[String]> {
        self.0.get(sid).map(Vec::as_slice)
    }

    /// kernel._name_of(sid): the first field, None when empty or no entry.
    pub fn name_of(&self, sid: &str) -> Option<&str> {
        self.parts(sid).and_then(|p| p.first()).map(String::as_str).filter(|n| !n.is_empty())
    }

    /// kernel._name_color(sid): the third field when it starts with "#" (as `{bg, fg: "#ffffff"}`).
    pub fn name_color(&self, sid: &str) -> Option<&str> {
        self.parts(sid).and_then(|p| p.get(2)).map(String::as_str).filter(|c| c.starts_with('#'))
    }
}

fn color_json(bg: Option<&str>) -> Out {
    bg.map_or(Out::Null, |bg| {
        Out::Object(vec![("bg".into(), Out::Str(bg.to_string())), ("fg".into(), Out::Str("#ffffff".into()))])
    })
}

record! {
    /// One attached host as the tunnel supervisor polls it (kernel._remotes' values): its name, the sids it owns and
    /// what it calls them.
    pub struct RemoteRow {
        host: Option<String> = "host",
        sids: Option<Vec<String>> = "sids",
        names: Option<OMap<String>> = "names",
    }
}

/// The state _peer_identity reads past the registry, as recorded inputs: the attached hosts (kernel._remotes, by host
/// key, in order) and the postal log's pairing (_postal_peer_names: sid to `"<host>:<name>"`). Both are empty in a
/// dump's replay (an empty state root, no tunnel supervisor), which `dump_layer4.py` checks.
#[derive(Debug, Clone, Default)]
pub struct PeerWorld {
    pub remotes: OMap<RemoteRow>,
    pub postal_names: OMap<String>,
}

impl PeerWorld {
    /// kernel._host_for_sid(sid): the first row whose sids hold `sid`.
    fn host_for_sid(&self, sid: &str) -> Option<&RemoteRow> {
        self.remotes
            .values()
            .find(|r| r.sids.as_ref().and_then(|s| s.as_ref()).is_some_and(|s| s.iter().any(|x| x == sid)))
    }

    /// kernel._remote_name_of(host, sid): the row keyed `host`'s name for `sid`, None when empty.
    fn remote_name_of(&self, host: &str, sid: &str) -> Option<&str> {
        self.remotes
            .get(host)
            .and_then(|r| r.names.as_ref().and_then(|n| n.as_ref()))
            .and_then(|n| n.get(sid))
            .map(String::as_str)
            .filter(|n| !n.is_empty())
    }
}

/// What the name and identity readers read: the registry, the peer facts past it, and an optional planted mistake.
pub struct Ctx<'a> {
    pub names: &'a Names,
    pub world: &'a PeerWorld,
    pub plant: Option<Plant>,
}

/// `_UUIDISH_RE.match(s)`: 8-4-4-4-12 ASCII hex digits, then the end, or one newline and the end (Python's `$`).
pub fn uuidish(s: &str, plant: Option<Plant>) -> bool {
    let s = if plant == Some(Plant::UuidStrict) { s } else { s.strip_suffix('\n').unwrap_or(s) };
    let b = s.as_bytes();
    b.len() == 36
        && b.iter()
            .enumerate()
            .all(|(i, &c)| if [8, 13, 18, 23].contains(&i) { c == b'-' } else { c.is_ascii_hexdigit() })
}

/// One peer identity: `{name, host, sid, color}`.
#[derive(Debug, Clone, PartialEq)]
pub struct Identity {
    pub name: String,
    pub host: String,
    pub sid: String,
    /// The registry color's `bg`, None for a peer another kernel owns.
    pub color: Option<String>,
}

impl Identity {
    pub fn to_json(&self) -> Out {
        Out::Object(vec![
            ("name".into(), Out::Str(self.name.clone())),
            ("host".into(), Out::Str(self.host.clone())),
            ("sid".into(), Out::Str(self.sid.clone())),
            ("color".into(), color_json(self.color.as_deref())),
        ])
    }
}

/// kernel._peer_identity(psid): the registry name, else (a uuid-shaped sid) the owning host's name for it, the postal
/// log's pairing, or the stub under the owning host; else a `<host>:<tail>` composite's parts, else the 8-character
/// stub.
pub fn peer_identity(ctx: &Ctx<'_>, psid: Option<&str>) -> Identity {
    let raw0 = psid.unwrap_or("");
    let raw = raw0.strip_prefix("peer:").unwrap_or(raw0);
    let id = |name: &str, host: &str, color: Option<&str>| Identity {
        name: name.to_string(),
        host: host.to_string(),
        sid: raw.to_string(),
        color: color.map(str::to_string),
    };
    if let Some(pn) = ctx.names.name_of(raw) {
        return id(pn, "", ctx.names.name_color(raw));
    }
    if uuidish(raw, ctx.plant) {
        let r = ctx.world.host_for_sid(raw);
        let host = r.and_then(|r| s2(&r.host)).unwrap_or("");
        if r.is_some()
            && let Some(rn) = ctx.world.remote_name_of(host, raw)
        {
            return id(rn, host, None);
        }
        let hn = ctx.world.postal_names.get(raw).map_or("", String::as_str);
        if !hn.is_empty() {
            let (h, t) = hn.split_once(':').unwrap_or((hn, ""));
            return id(if t.is_empty() { head(raw, 8) } else { t }, h, None);
        }
        if !host.is_empty() {
            return id(head(raw, 8), host, None);
        }
    }
    if let Some((h, t)) = raw.split_once(':') {
        let name = if uuidish(t, ctx.plant) { head(t, 8) } else { t };
        return id(if name.is_empty() { head(raw, 8) } else { name }, h, ctx.names.name_color(raw));
    }
    id(head(raw, 8), "", ctx.names.name_color(raw))
}

/// kernel._handoff_peer_identities(nodes, hnodes): the identities of the distinct handoff peers, sorted by name
/// (stably: equal names keep first-seen order), None when there are none.
pub fn handoff_peer_identities(ctx: &Ctx<'_>, ns: &Ns<'_>, hnodes: &[&str]) -> Option<Vec<Identity>> {
    let mut seen: Vec<(String, Identity)> = Vec::new();
    for x in hnodes {
        let psid = ns.get(x).and_then(handoff).and_then(|h| s2(&h.peer)).unwrap_or("");
        if psid.is_empty() || seen.iter().any(|(p, _)| p == psid) {
            continue;
        }
        seen.push((psid.to_string(), peer_identity(ctx, Some(psid))));
    }
    let mut out: Vec<Identity> = seen.into_iter().map(|(_, i)| i).collect();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    (!out.is_empty()).then_some(out)
}

/// `_HANDOFF_LABEL_RE.match(txt)` (`^\s*↪\s*delegated to (.+?): (.*)$`, re.S): the two groups. The lazy first group
/// ends at the first ": " after at least one character; the second runs to the end.
pub fn handoff_label(txt: &str) -> Option<(&str, &str)> {
    let s = pystr::skip_space(txt);
    let s = pystr::skip_space(s.strip_prefix('↪')?);
    let rest = s.strip_prefix("delegated to ")?;
    let first = rest.chars().next()?.len_utf8();
    let i = first + rest[first..].find(": ")?;
    Some((&rest[..i], &rest[i + 2..]))
}

/// kernel._title_shaped(text, cap): one title line: whitespace collapsed when the text has a newline, cut after the
/// first `.`, `!` or `?` followed by whitespace at or past the 13th code point, clamped to `cap` with an ellipsis.
pub fn title_shaped(text: Option<&str>, cap: usize) -> String {
    let mut t: String = text.unwrap_or("").to_string();
    if t.contains('\n') {
        t = collapse_space(&t);
    }
    let chars: Vec<char> = t.chars().collect();
    if let Some(i) = (12..chars.len().saturating_sub(1))
        .find(|&i| matches!(chars[i], '.' | '!' | '?') && pystr::is_space(chars[i + 1]))
    {
        t = chars[..=i].iter().collect();
    }
    if pystr::len(&t) > cap {
        t = format!("{}…", head(&t, cap - 1).trim_end_matches(pystr::is_space));
    }
    t
}

/// The badge _handoff_card_fields returns: `{peer, peerHost, peerSid, color}`.
#[derive(Debug, Clone, PartialEq)]
pub struct Badge {
    pub peer: String,
    pub peer_host: String,
    pub peer_sid: String,
    pub color: Option<String>,
}

impl Badge {
    pub fn to_json(&self) -> Out {
        Out::Object(vec![
            ("peer".into(), Out::Str(self.peer.clone())),
            ("peerHost".into(), Out::Str(self.peer_host.clone())),
            ("peerSid".into(), Out::Str(self.peer_sid.clone())),
            ("color".into(), color_json(self.color.as_deref())),
        ])
    }
}

/// kernel._handoff_card_fields(nodes, nid): for a handoff node with a peer, the badge naming the recipient (the
/// registry first, then the plant-time label when the registry misses) and the work as a title; else (None, text).
pub fn handoff_card_fields(ctx: &Ctx<'_>, ns: &Ns<'_>, nid: &str) -> (Option<Badge>, String) {
    let nd = ns.get(nid);
    let txt = nd.and_then(|n| s2(&n.text)).unwrap_or("");
    let Some(peer) = nd.and_then(handoff).and_then(|h| s2(&h.peer)).filter(|p| !p.is_empty()) else {
        return (None, txt.to_string());
    };
    let ident = handoff_peer_identities(ctx, ns, &[nid])
        .and_then(|v| v.into_iter().next())
        .expect("a handoff with a peer has an identity");
    let mut badge = Badge {
        peer: ident.name,
        peer_host: ident.host,
        peer_sid: if ident.sid.is_empty() { peer.to_string() } else { ident.sid },
        color: ident.color,
    };
    let m = handoff_label(txt);
    if let Some((g1, _)) = m
        && ctx.names.name_of(&badge.peer_sid).is_none()
    {
        let g1 = pystr::strip(g1);
        let (lh, ln) = g1.rsplit_once(':').unwrap_or(("", g1));
        if !ln.is_empty() {
            badge.peer = ln.to_string();
            if !lh.is_empty() {
                badge.peer_host = lh.to_string();
            }
        }
    }
    let mut work = m.map_or("", |(_, g2)| pystr::strip(g2)).to_string();
    if work.is_empty() || work == "(work)" {
        let nd = nd.expect("a handoff node");
        let why = pystr::strip(s2(&nd.why).unwrap_or(""));
        let summary = pystr::strip(s2(&nd.summary).unwrap_or(""));
        let replaced = txt.replacen("↪ ", "", 1);
        let bare = pystr::strip(&replaced);
        work = [why, summary, bare, txt].into_iter().find(|s| !s.is_empty()).unwrap_or(txt).to_string();
    }
    (Some(badge), title_shaped(Some(&work), TITLE_CAP))
}

/// One awaited row: `{kind, id, label, since}`, then `agentId`, `detail` and `stoppable` only when truthy.
#[derive(Debug, Clone, PartialEq)]
pub struct Item {
    pub kind: String,
    pub id: String,
    pub label: String,
    pub since: Out,
    pub agent_id: Option<String>,
    pub detail: Option<String>,
    pub stoppable: bool,
}

impl Item {
    pub fn to_json(&self) -> Out {
        let mut m = vec![
            ("kind".to_string(), Out::Str(self.kind.clone())),
            ("id".to_string(), Out::Str(self.id.clone())),
            ("label".to_string(), Out::Str(self.label.clone())),
            ("since".to_string(), self.since.clone()),
        ];
        if let Some(a) = &self.agent_id {
            m.push(("agentId".to_string(), Out::Str(a.clone())));
        }
        if let Some(d) = &self.detail {
            m.push(("detail".to_string(), Out::Str(d.clone())));
        }
        if self.stoppable {
            m.push(("stoppable".to_string(), Out::Bool(true)));
        }
        Out::Object(m)
    }
}

/// kernel._awaiting_item(kind, iid, label, since, agent_id, detail, stoppable). `Err` is the AssertionError on a kind
/// outside _AWAIT_ITEM_KINDS. `since` is `int(since)` when truthy (a float truncated toward zero), else None.
#[allow(clippy::too_many_arguments)]
pub fn awaiting_item(
    kind: &str,
    iid: Option<&str>,
    label: Option<&str>,
    since: Option<Num>,
    agent_id: Option<&str>,
    detail: Option<&str>,
    stoppable: bool,
    plant: Option<Plant>,
) -> Result<Item, Raise> {
    if !AWAIT_ITEM_KINDS.contains(&kind) {
        return Err(Raise { exc: "AssertionError", arg: kind.to_string() });
    }
    Ok(Item {
        kind: kind.to_string(),
        id: iid.unwrap_or("").to_string(),
        label: pystr::strip(label.unwrap_or("")).to_string(),
        since: since.filter(|s| s.truthy()).map_or(Out::Null, |s| py_int(s, plant)),
        agent_id: agent_id.filter(|a| !a.is_empty()).map(str::to_string),
        detail: detail.filter(|d| !d.is_empty()).map(str::to_string),
        stoppable,
    })
}

/// kernel._awaiting_peer_items(peers): a label-only `peer` row per identity (`peer:<name>`, the name or "a peer").
pub fn awaiting_peer_items(peers: Option<&[Identity]>) -> Vec<Item> {
    peers
        .unwrap_or_default()
        .iter()
        .map(|p| {
            let label = if p.name.is_empty() { "a peer" } else { &p.name };
            awaiting_item("peer", Some(&format!("peer:{}", p.name)), Some(label), None, None, None, false, None)
                .expect("peer is an awaited-row kind")
        })
        .collect()
}

// ───────────────────────────── the per-node faces ─────────────────────────────

/// What _session_started_face returns: `{why, parent}`.
#[derive(Debug, Clone, PartialEq)]
pub struct Face {
    pub why: String,
    pub parent: Option<String>,
}

/// kernel._session_started_face(nodes, nid, healed): for a born step (its own `born` dict, else the heal's record)
/// the story `{why, parent}`, the parent named by `parentText`, else by the parent node's text; else None.
pub fn session_started_face(ns: &Ns<'_>, nid: &str, healed: &Healed) -> Option<Face> {
    let nd = ns.get(nid);
    let b = nd.and_then(born).or_else(|| healed.get(nid).map(|(_, b)| b))?;
    if b.order.is_empty() {
        return None;
    }
    let mut parent = s2(&b.parent_text).filter(|s| !s.is_empty()).map(str::to_string);
    if parent.is_none()
        && let Some(pid) = nd.and_then(parent_id).filter(|p| !p.is_empty())
        && let Some(pn) = ns.get(pid)
    {
        parent = s2(&pn.text).filter(|s| !s.is_empty()).map(str::to_string);
    }
    Some(Face { why: s2(&b.why).unwrap_or("").to_string(), parent })
}

/// A seg-key map the store helpers read (layer 3's `seg_uuid` or `seg_trig`): `get(key)`, absent and None alike.
pub trait SegLookup {
    fn lookup(&self, key: &str) -> Option<&str>;
}

/// A seg map given as data (an edge case's or a fuzz case's).
#[derive(Debug, Clone, Default)]
pub struct SegMap(pub HashMap<String, Option<String>>);

impl SegLookup for SegMap {
    fn lookup(&self, key: &str) -> Option<&str> {
        self.0.get(key).and_then(|v| v.as_deref())
    }
}

/// Layer 3's walk as `seg_uuid` (the work anchors).
pub struct WalkWork<'m, 'a>(pub &'m SegMaps<'a>);

impl SegLookup for WalkWork<'_, '_> {
    fn lookup(&self, key: &str) -> Option<&str> {
        self.0.segs.get(key).and_then(|e| e.uuid)
    }
}

/// Layer 3's walk as `seg_trig` (the prompt anchors).
pub struct WalkTrig<'m, 'a>(pub &'m SegMaps<'a>);

impl SegLookup for WalkTrig<'_, '_> {
    fn lookup(&self, key: &str) -> Option<&str> {
        self.0.segs.get(key).and_then(|e| e.trig)
    }
}

/// kernel._node_log_rows(nd, seg_work, cap): the newest `cap` log rows as `{kind, src, why, at, evT, anchorUuid}`, the
/// anchor looked up by the row's seg key; None when the node has no rows.
pub fn node_log_rows(nd: &Node, seg_work: &dyn SegLookup, cap: usize) -> Out {
    let log = nd.log.as_deref().unwrap_or_default();
    let rows: Vec<Out> = log[log.len().saturating_sub(cap)..]
        .iter()
        .map(|r| {
            let anchor =
                s2(&r.seg).filter(|s| !s.is_empty()).and_then(|seg| seg_work.lookup(&layer1::kernel_seg_key(seg)));
            Out::Object(vec![
                ("kind".into(), s2(&r.kind).map(str::to_string).to_json()),
                ("src".into(), s2(&r.src).map(str::to_string).to_json()),
                ("why".into(), s2(&r.why).map(str::to_string).to_json()),
                ("at".into(), n2(&r.at).to_json()),
                ("evT".into(), n2(&r.ev_t).to_json()),
                ("anchorUuid".into(), anchor.map(str::to_string).to_json()),
            ])
        })
        .collect();
    if rows.is_empty() { Out::Null } else { Out::Array(rows) }
}

/// jd.junk_quote(q): a continuation stub ("retry", "continue", ...) or a bare slash command, after collapsing
/// whitespace, lowercasing (Unicode) and dropping trailing `.!?…`.
pub fn junk_quote(q: Option<&str>) -> bool {
    let Some(q) = q.filter(|q| !q.is_empty()) else { return false };
    let qs = collapse_space(q).to_lowercase();
    let qs = qs.trim_end_matches(['.', '!', '?', '…']);
    JUNK_QUOTES.contains(&qs) || (qs.starts_with('/') && !qs.contains(' '))
}

/// kernel._node_anchor_last: node id to its last warm-resolved `(prompt, work)`, kept across calls for the kernel run.
#[derive(Debug, Clone, Default)]
pub struct AnchorTable(pub HashMap<String, (Option<String>, String)>);

/// kernel._node_anchor_uuids(nd, seg_trig, seg_work): (prompt anchor, work anchor). The prompt is the stored
/// promptUuid, else the trigger of trail[0]'s segment, dropped when the quote is junk; the work is trail[-1]'s
/// segment's anchor. A warm work anchor is remembered by node id; a cold one serves the remembered pair, else the
/// node's summaryAnchor.
pub fn node_anchor_uuids(
    nd: &Node,
    seg_trig: &dyn SegLookup,
    seg_work: &dyn SegLookup,
    table: &mut AnchorTable,
) -> (Option<String>, Option<String>) {
    anchors_with(nd, seg_trig, seg_work, table, None)
}

fn anchors_with(
    nd: &Node,
    seg_trig: &dyn SegLookup,
    seg_work: &dyn SegLookup,
    table: &mut AnchorTable,
    plant: Option<Plant>,
) -> (Option<String>, Option<String>) {
    let trail = nd.trail.as_deref().unwrap_or_default();
    let key = |s: &str| layer1::kernel_seg_key(s).into_owned();
    let derived = trail.first().and_then(|t0| seg_trig.lookup(&key(t0))).map(str::to_string);
    let mut prompt = or_str(s2(&nd.prompt_uuid).map(str::to_string), derived);
    if truthy_str(prompt.as_deref()) && junk_quote(s2(&nd.quote)) {
        prompt = None;
    }
    let mut work = trail.last().and_then(|a| seg_work.lookup(&key(a))).map(str::to_string);
    let nid = nd.id.as_deref().filter(|n| !n.is_empty());
    let remember = plant != Some(Plant::TableOff);
    if let Some(w) = &work {
        if let Some(nid) = nid
            && remember
            && table.0.get(nid) != Some(&(prompt.clone(), w.clone()))
        {
            table.0.insert(nid.to_string(), (prompt.clone(), w.clone()));
        }
    } else {
        if let Some(held) = nid.and_then(|n| table.0.get(n)) {
            prompt = or_str(prompt, held.0.clone());
            work = Some(held.1.clone());
        }
        if work.is_none() {
            work = s2(&nd.summary_anchor).map(str::to_string);
        }
    }
    (prompt, work)
}

/// kernel._landing_inputs(state, column, nd): (completed, line) by the client's distillInputs rule: a working column
/// shows nothing, else the state decides, else the column; the line is the block brief or the summary, "" as None.
pub fn landing_inputs<'n>(state: Option<&str>, column: &str, nd: &'n Node) -> (bool, Option<&'n str>) {
    let (completed, blocked) = if column == "working" {
        (false, false)
    } else if state == Some("completed") {
        (true, false)
    } else if state == Some("blocked") {
        (false, true)
    } else {
        (column == "completed", column == "needs_input")
    };
    let line = if blocked { s2(&nd.block_summary) } else if completed { s2(&nd.summary) } else { None };
    (completed, line.filter(|l| !l.is_empty()))
}

/// kernel._login_refusal_label(row, aerr): the stored login's label when the api error is an auth error and the
/// launch carried and used that login, else "". `Err` is Python's AttributeError on a truthy non-dict api error.
pub fn login_refusal_label(row: Option<&LiveRow>, aerr: Option<&serde_json::Value>) -> Result<String, Raise> {
    let Some(aerr) = aerr.filter(|a| py_truthy(a)) else { return Ok(String::new()) };
    let Some(obj) = aerr.as_object() else {
        return Err(Raise { exc: "AttributeError", arg: format!("'{}' object has no attribute 'get'", py_type(aerr)) });
    };
    if !obj.get("authErr").is_some_and(py_truthy) {
        return Ok(String::new());
    }
    let lid = row.and_then(|r| s2(&r.auth_login)).unwrap_or("");
    if lid.is_empty() || row.and_then(|r| s2(&r.auth_login_live)).unwrap_or("") != lid {
        return Ok(String::new());
    }
    Ok(row.and_then(|r| s2(&r.auth_label)).filter(|l| !l.is_empty()).unwrap_or("login").to_string())
}

// ───────────────────────── answers as tools/rust-spike/dump_layer4.py writes Python's ─────────────────────────

fn s(x: Option<&str>) -> Out {
    x.map_or(Out::Null, |x| Out::Str(x.to_string()))
}

fn strs<S: AsRef<str>>(xs: &[S]) -> Out {
    Out::Array(xs.iter().map(|x| Out::Str(x.as_ref().to_string())).collect())
}

fn obj(members: Vec<(&str, Out)>) -> Out {
    Out::Object(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn loops() -> Out {
    obj(vec![("loops", Out::Bool(true))])
}

fn idents(v: Option<&[Identity]>) -> Out {
    v.map_or(Out::Null, |v| Out::Array(v.iter().map(Identity::to_json).collect()))
}

fn items(v: &[Item]) -> Out {
    Out::Array(v.iter().map(Item::to_json).collect())
}

/// What one session hands the store helpers: its store (None reads as no nodes), the recorded heal and
/// peer-answered answers (None: Python never called it), its live row, the recorded api error (None when absent or
/// null) and layer 3's two maps.
pub struct SessionIn<'a> {
    pub store: Option<&'a Store>,
    pub healed: &'a Healed,
    pub answered: Option<Answered<'a>>,
    pub live_row: Option<&'a LiveRow>,
    pub api_error: Option<&'a serde_json::Value>,
    pub seg_work: &'a dyn SegLookup,
    pub seg_trig: &'a dyn SegLookup,
}

/// One session's layer-4 answer (dump_layer4.py's `session_answer`): the body's children and hidden set,
/// _agent_open_set (sorted), _parked_rows, _focus_open_top over lastNode, then per node in store order every per-node
/// helper (the stamp only when peerAnswered was recorded), and _login_refusal_label. `table` carries
/// _node_anchor_last from the sessions before. `Err` when the body's children forest is refused.
pub fn session_json(ctx: &Ctx<'_>, si: &SessionIn<'_>, table: &mut AnchorTable) -> Result<Out, String> {
    let plant = ctx.plant;
    let empty_nodes = OMap::default();
    let empty_status = OMap::default();
    let nodes = si.store.and_then(|s| s.nodes.as_ref()).unwrap_or(&empty_nodes);
    let status = si.store.and_then(|s| s.status.as_ref()).unwrap_or(&empty_status);
    let ns = Ns::new(nodes);
    let (children, hidden) = body_children(&ns, si.healed)?;
    let mut aopen: Vec<String> = agent_open_set(&ns, &children).into_iter().collect();
    aopen.sort_unstable();
    let parked = parked_with(&ns, &children, plant);
    let last_node = si.store.and_then(|s| s2(&s.last_node));
    let mut per = Vec::new();
    for (nid, nd) in ns.iter() {
        let mut r: Vec<(&str, Out)> = Vec::new();
        r.push(("topOf", top_of(&ns, Some(nid)).to_json()));
        r.push(("focusTop", focus_open_top(&ns, Some(nid), status).to_json()));
        let sub = subtree(&children, nid);
        if let Some(ans) = &si.answered {
            match stamp_with(&ns, nid, Some(&children), ans, plant) {
                Err(Loops) => r.push(("stamp", loops())),
                Ok(sf) => {
                    let peers: Option<Vec<Identity>> = sf
                        .as_ref()
                        .filter(|sf| sf.kind.as_deref() == Some("peer") && !sf.peers.is_empty())
                        .map(|sf| {
                            let mut v: Vec<Identity> = sf.peers.iter().map(|p| peer_identity(ctx, Some(p))).collect();
                            v.sort_by(|a, b| a.name.cmp(&b.name));
                            v
                        });
                    r.push(("stamp", sf.as_ref().map_or(Out::Null, Stamp::to_json)));
                    r.push(("stampPeers", idents(peers.as_deref())));
                    r.push(("stampItems", items(&awaiting_peer_items(peers.as_deref()))));
                }
            }
        }
        r.push((
            "newestBlockEvT",
            match &sub {
                Err(Loops) => loops(),
                Ok(sub) => {
                    let ids: Vec<String> =
                        sub.iter().filter(|x| ns.get(x).is_some_and(|n| flag(&n.blocked))).cloned().collect();
                    newest_block_ev_t(&ns, &ids).to_json()
                }
            },
        ));
        let leaves = open_leaves(&ns, nid);
        r.push(("openLeaves", strs(&leaves)));
        r.push(("allDelegated", Out::Bool(all_outstanding_delegated(&ns, nid))));
        let hn: Vec<&str> =
            leaves.iter().map(String::as_str).filter(|x| ns.get(x).and_then(handoff).is_some()).collect();
        let dp = handoff_peer_identities(ctx, &ns, &hn);
        r.push(("delegPeers", idents(dp.as_deref())));
        r.push(("delegItems", items(&awaiting_peer_items(dp.as_deref()))));
        let (badge, title) = handoff_card_fields(ctx, &ns, nid);
        r.push(("handoffCard", Out::Array(vec![badge.as_ref().map_or(Out::Null, Badge::to_json), Out::Str(title)])));
        r.push(("blockEvT", block_ev_t(nd).to_json()));
        r.push(("blockSince", block_since(nd).to_json()));
        r.push(("doneSince", done_since(nd).to_json()));
        r.push(("reviewBoundary", review_boundary(nd).to_json()));
        r.push((
            "startedFace",
            session_started_face(&ns, nid, si.healed)
                .map_or(Out::Null, |f| obj(vec![("why", Out::Str(f.why)), ("parent", f.parent.to_json())])),
        ));
        r.push(("logRows", node_log_rows(nd, si.seg_work, LOG_CAP)));
        let (pa, wa) = anchors_with(nd, si.seg_trig, si.seg_work, table, plant);
        r.push(("anchors", Out::Array(vec![pa.to_json(), wa.to_json()])));
        r.push(("junk", Out::Bool(junk_quote(s2(&nd.quote)))));
        let mut landing = Vec::new();
        for st in LANDING_STATES {
            for col in LANDING_COLUMNS {
                let (c, line) = landing_inputs(st, col, nd);
                landing.push(Out::Array(vec![Out::Bool(c), s(line)]));
            }
        }
        r.push(("landing", Out::Array(landing)));
        per.push(Out::Array(vec![Out::Str(nid.to_string()), obj(r)]));
    }
    let label = match login_refusal_label(si.live_row, si.api_error) {
        Ok(l) => Out::Str(l),
        Err(e) => e.to_json(),
    };
    Ok(obj(vec![
        ("children", children.to_json()),
        ("hidden", strs(&hidden)),
        ("agentOpen", strs(&aopen)),
        (
            "parked",
            Out::Array(
                parked.iter().map(|(k, n)| Out::Array(vec![Out::Str(k.clone()), Out::Int(*n as i64)])).collect(),
            ),
        ),
        ("focusTop", focus_open_top(&ns, last_node, status).to_json()),
        ("nodes", Out::Array(per)),
        ("loginLabel", label),
    ]))
}

/// The name queries: per query `[q, _name_of(q), _name_color(q), _peer_identity(q)]`.
pub fn names_json(ctx: &Ctx<'_>, queries: &[Option<String>]) -> Out {
    Out::Array(
        queries
            .iter()
            .map(|q| {
                let k = q.as_deref().unwrap_or("");
                Out::Array(vec![
                    q.clone().to_json(),
                    s(ctx.names.name_of(k)),
                    color_json(ctx.names.name_color(k)),
                    peer_identity(ctx, q.as_deref()).to_json(),
                ])
            })
            .collect(),
    )
}

/// Every sid-shaped string a dump's layer-4 answers are asked about, first occurrence kept (dump_layer4.py
/// `queries`): the alive sids, the peers' sids, then per store (the sessions' in snapshot order, then the peers') per
/// node its handoff peer, origin peer and awaitingPeers, then ABSENT_SID and "".
pub fn dump_queries(snap: &Snapshot) -> Vec<Option<String>> {
    let mut out: Vec<Option<String>> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    let mut add = |x: &str| {
        if seen.insert(x.to_string()) {
            out.push(Some(x.to_string()));
        }
    };
    for r in snap.alive() {
        add(r.sid.as_deref().expect("required at load"));
    }
    for psid in snap.peers.iter().flat_map(OMap::keys) {
        add(psid);
    }
    for (_path, _sid, st) in snap.stores() {
        for nd in st.nodes.iter().flat_map(OMap::values) {
            if let Some(p) = handoff(nd).and_then(|h| s2(&h.peer)) {
                add(p);
            }
            if let Some(Some(o)) = &nd.origin
                && let Some(p) = s2(&o.peer)
            {
                add(p);
            }
            for p in nd.awaiting_peers.iter().flatten().flatten() {
                add(p);
            }
        }
    }
    add(ABSENT_SID);
    add("");
    out
}

// ───────────────────────────── the edge set and the fuzz ─────────────────────────────

/// One session of an edge-set world, owned.
#[derive(Debug)]
pub struct SessionCase {
    pub store: Option<Store>,
    pub healed: Healed,
    pub answered: Option<(Num, OMap<Num>)>,
    pub live_row: Option<LiveRow>,
    pub api_error: Option<serde_json::Value>,
    pub seg_work: SegMap,
    pub seg_trig: SegMap,
}

/// A legacy scalar or the tuple, as a direct case hands _peer_stamp_superseded or _goal_awaiting_stamp_full.
#[derive(Debug)]
pub enum AnsweredOwned {
    Pair(Num, OMap<Num>),
    Scalar(Num),
}

impl AnsweredOwned {
    fn borrow(&self) -> Answered<'_> {
        match self {
            AnsweredOwned::Pair(t, m) => Answered::Pair(*t, m),
            AnsweredOwned::Scalar(t) => Answered::Scalar(*t),
        }
    }
}

/// One case of tools/rust-spike/dump_layer4.py `--edge-cases` or `--fuzz`, loaded through the port's loader.
#[derive(Debug)]
pub enum EdgeCase {
    /// Sessions sharing one anchor table, a names registry and the peer facts, plus name queries.
    World { names: Names, world: PeerWorld, sessions: Vec<SessionCase>, queries: Vec<Option<String>> },
    /// A direct _awaiting_item call.
    Item {
        kind: String,
        iid: Option<String>,
        label: Option<String>,
        since: Option<Num>,
        agent_id: Option<String>,
        detail: Option<String>,
        stoppable: bool,
    },
    /// A direct _title_shaped call.
    Title { text: Option<String>, cap: usize },
    /// A direct jd.junk_quote call.
    Junk { q: Option<String> },
    /// A direct _peer_stamp_superseded call.
    Superseded { node: Box<Node>, answered: AnsweredOwned },
    /// _goal_awaiting_stamp_full without `children` (built from the nodes' parents).
    StampDefault { nodes: OMap<Node>, top: String, answered: AnsweredOwned },
    /// A direct _landing_inputs call.
    Landing { state: Option<String>, column: String, node: Box<Node> },
}

fn field<T: serde::de::DeserializeOwned>(case: &serde_json::Value, key: &str) -> Result<T, String> {
    serde_json::from_value(case.get(key).cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| format!("{key}: {e}"))
}

fn node_value(v: &serde_json::Value, path: &str) -> Result<Node, String> {
    let nd: Node = serde_json::from_value(v.clone()).map_err(|e| format!("{path}: {e}"))?;
    snapshot::validate_node(path, &nd)?;
    Ok(nd)
}

fn answered_value(case: &serde_json::Value) -> Result<AnsweredOwned, String> {
    if case["tuple"].as_bool() == Some(true) {
        let (t, m): (Num, OMap<Num>) = field(case, "answered")?;
        Ok(AnsweredOwned::Pair(t, m))
    } else {
        let t: Option<Num> = field(case, "answered")?;
        Ok(AnsweredOwned::Scalar(t.unwrap_or(Num::Int(0))))
    }
}

fn seg_map(v: &serde_json::Value, key: &str) -> Result<SegMap, String> {
    let rows: Vec<(String, Option<String>)> =
        serde_json::from_value(v.get(key).cloned().unwrap_or(serde_json::Value::Array(vec![])))
            .map_err(|e| format!("{key}: {e}"))?;
    Ok(SegMap(rows.into_iter().collect()))
}

impl SessionCase {
    fn load(v: &serde_json::Value, i: usize) -> Result<SessionCase, String> {
        let at = |e: String| format!("sessions[{i}].{e}");
        let store: Option<Store> = field(v, "store").map_err(at)?;
        if let Some(st) = &store {
            snapshot::validate_store(&format!("sessions[{i}].store"), st)?;
        }
        let healed: Healed = match v.get("healed") {
            None | Some(serde_json::Value::Null) => OMap::default(),
            Some(h) => serde_json::from_value(h.clone()).map_err(|e| at(format!("healed: {e}")))?,
        };
        let answered = match v.get("peerAnswered") {
            None => None,
            Some(a) => Some(serde_json::from_value(a.clone()).map_err(|e| at(format!("peerAnswered: {e}")))?),
        };
        let live_row: Option<LiveRow> = field(v, "liveRow").map_err(at)?;
        Ok(SessionCase {
            store,
            healed,
            answered,
            live_row,
            api_error: v.get("apiError").cloned(),
            seg_work: seg_map(v, "segUuid").map_err(at)?,
            seg_trig: seg_map(v, "segTrig").map_err(at)?,
        })
    }
}

/// A case's members that are Python's answers, not its inputs.
pub const ANSWER_KEYS: [&str; 3] = ["expect", "expectSha1", "expectBytes"];

impl EdgeCase {
    /// Load one case, `exact` being the same case as romp-transcript's decoder reads it (Python's reading): refused
    /// when an input holds an integer past i64, which serde_json reads as a float (load.rs), else as `load` does.
    pub fn load_checked(case: &serde_json::Value, exact: &Out) -> Result<EdgeCase, String> {
        crate::load::refuse_big_int(exact, &ANSWER_KEYS)?;
        EdgeCase::load(case)
    }

    /// Load one case; `Err` names the part the loader refused.
    pub fn load(case: &serde_json::Value) -> Result<EdgeCase, String> {
        match case["kind"].as_str() {
            Some("world") => {
                let w = &case["world"];
                let mut names = Names::default();
                let reg: OMap<Option<Vec<String>>> = field(w, "names")?;
                for (sid, parts) in reg.iter() {
                    if let Some(p) = parts {
                        names.0.insert(sid.clone(), p.clone());
                    }
                }
                let world = PeerWorld { remotes: field(w, "remotes")?, postal_names: field(w, "postalNames")? };
                let rows = w["sessions"].as_array().ok_or("world.sessions: not a list")?;
                let sessions = rows.iter().enumerate().map(|(i, v)| SessionCase::load(v, i)).collect::<Result<_, _>>()?;
                let queries = field(w, "queries")?;
                Ok(EdgeCase::World { names, world, sessions, queries })
            }
            Some("item") => Ok(EdgeCase::Item {
                kind: field(case, "itemKind")?,
                iid: field(case, "iid")?,
                label: field(case, "label")?,
                since: field(case, "since")?,
                agent_id: field(case, "agentId")?,
                detail: field(case, "detail")?,
                stoppable: field::<Option<bool>>(case, "stoppable")?.unwrap_or(false),
            }),
            Some("title") => {
                let cap: usize = field(case, "cap")?;
                if cap < 1 {
                    return Err("cap: below 1 (Python would slice from the end)".into());
                }
                Ok(EdgeCase::Title { text: field(case, "text")?, cap })
            }
            Some("junk") => Ok(EdgeCase::Junk { q: field(case, "q")? }),
            Some("superseded") => Ok(EdgeCase::Superseded {
                node: Box::new(node_value(&case["node"], "node")?),
                answered: answered_value(case)?,
            }),
            Some("stampDefault") => {
                let nodes: OMap<Node> = field(case, "nodes")?;
                let st = Store { nodes: Some(nodes), ..Store::default() };
                snapshot::validate_store("nodes", &st)?;
                Ok(EdgeCase::StampDefault {
                    nodes: st.nodes.expect("set above"),
                    top: field(case, "top")?,
                    answered: answered_value(case)?,
                })
            }
            Some("landing") => Ok(EdgeCase::Landing {
                state: field(case, "state")?,
                column: field(case, "column")?,
                node: Box::new(node_value(&case["node"], "node")?),
            }),
            k => Err(format!("unknown kind {k:?}")),
        }
    }

    /// The port's answer, in the shape dump_layer4.py writes Python's `expect`.
    pub fn answer(&self, plant: Option<Plant>) -> Out {
        match self {
            EdgeCase::World { names, world, sessions, queries } => {
                let ctx = Ctx { names, world, plant };
                let mut table = AnchorTable::default();
                let answers = sessions
                    .iter()
                    .map(|sc| {
                        let si = SessionIn {
                            store: sc.store.as_ref(),
                            healed: &sc.healed,
                            answered: sc.answered.as_ref().map(|(t, m)| Answered::Pair(*t, m)),
                            live_row: sc.live_row.as_ref(),
                            api_error: sc.api_error.as_ref(),
                            seg_work: &sc.seg_work,
                            seg_trig: &sc.seg_trig,
                        };
                        session_json(&ctx, &si, &mut table).unwrap_or_else(|e| obj(vec![("refused", Out::Str(e))]))
                    })
                    .collect();
                obj(vec![("sessions", Out::Array(answers)), ("names", names_json(&ctx, queries))])
            }
            EdgeCase::Item { kind, iid, label, since, agent_id, detail, stoppable } => {
                match awaiting_item(kind, iid.as_deref(), label.as_deref(), *since, agent_id.as_deref(),
                                    detail.as_deref(), *stoppable, plant) {
                    Ok(it) => it.to_json(),
                    Err(e) => e.to_json(),
                }
            }
            EdgeCase::Title { text, cap } => Out::Str(title_shaped(text.as_deref(), *cap)),
            EdgeCase::Junk { q } => Out::Bool(junk_quote(q.as_deref())),
            EdgeCase::Superseded { node, answered } => peer_stamp_superseded(node, &answered.borrow()).to_json(),
            EdgeCase::StampDefault { nodes, top, answered } => {
                let ns = Ns::new(nodes);
                match stamp_with(&ns, top, None, &answered.borrow(), plant) {
                    Err(Loops) => loops(),
                    Ok(sf) => sf.as_ref().map_or(Out::Null, Stamp::to_json),
                }
            }
            EdgeCase::Landing { state, column, node } => {
                let (c, line) = landing_inputs(state.as_deref(), column, node);
                Out::Array(vec![Out::Bool(c), s(line)])
            }
        }
    }

    /// Whether the body's children build refuses one of this world's sessions (the port answers `{"refused": ...}`).
    pub fn refused_session(&self) -> Option<String> {
        let EdgeCase::World { sessions, .. } = self else { return None };
        sessions.iter().enumerate().find_map(|(i, sc)| {
            let empty = OMap::default();
            let nodes = sc.store.as_ref().and_then(|s| s.nodes.as_ref()).unwrap_or(&empty);
            body_children(&Ns::new(nodes), &sc.healed).err().map(|e| format!("sessions[{i}]: {e}"))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nodes(json: &str) -> OMap<Node> {
        serde_json::from_str(json).unwrap()
    }

    fn node(json: &str) -> Node {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn the_stamp_breaks_ties_on_strings_and_keeps_the_first_full_tie() {
        let m = nodes(
            r#"{"t": {"id": "t", "parentId": null},
                "a": {"id": "a", "parentId": "t", "awaitingWhy": "b-why", "awaitingAt": 5, "awaitingKind": "job"},
                "b": {"id": "b", "parentId": "t", "awaitingWhy": "a-why", "awaitingAt": 5.0, "awaitingKind": "peer"},
                "c": {"id": "c", "parentId": "t", "awaitingWhy": "b-why", "awaitingAt": 5.0, "awaitingKind": "job"}}"#,
        );
        let ns = Ns::new(&m);
        let none = OMap::default();
        let ans = Answered::Pair(Num::Int(0), &none);
        // the stack pops c, then b, then a: c and a tie fully (5.0 == 5, same strings), so c, met first, is kept
        let sf = goal_awaiting_stamp_full(&ns, "t", None, &ans).unwrap().unwrap();
        assert_eq!(sf, Stamp { at: Num::Float(5.0), why: "b-why".into(), kind: Some("job".into()), peers: vec![] });
        let last = stamp_with(&ns, "t", None, &ans, Some(Plant::MaxKeepsLast)).unwrap().unwrap();
        assert_eq!(last.at, Num::Int(5), "the planted max keeps a, met last");
    }

    #[test]
    fn a_cycle_loops_the_stamp_walk_but_not_the_cycle_safe_readers() {
        let m = nodes(r#"{"a": {"id": "a", "parentId": "b"}, "b": {"id": "b", "parentId": "a"}}"#);
        let ns = Ns::new(&m);
        let c = Children::from_parents(&ns);
        let ans = Answered::Scalar(Num::Int(0));
        assert_eq!(goal_awaiting_stamp_full(&ns, "a", Some(&c), &ans), Err(Loops));
        assert_eq!(subtree(&c, "a"), Err(Loops));
        assert_eq!(top_of(&ns, Some("a")), Some("a".into()));
        assert_eq!(open_leaves(&ns, "a"), Vec::<String>::new(), "each open node has an open kid: no leaf");
        assert!(parked_rows(&ns, &c).is_empty());
    }

    #[test]
    fn a_peer_answer_supersedes_only_a_stamp_written_before_it() {
        let per: OMap<Num> = serde_json::from_str(r#"{"p1": 50, "p2": 70.5}"#).unwrap();
        let nd = |extra: &str| node(&format!(r#"{{"id": "x", "awaitingWhy": "w", "awaitingAt": 40{extra}}}"#));
        let pair = Answered::Pair(Num::Int(60), &per);
        assert_eq!(peer_stamp_superseded(&nd(""), &pair), Num::Int(60));
        assert_eq!(peer_stamp_superseded(&nd(r#", "awaitingPeers": ["p1", "p2"]"#), &pair), Num::Float(70.5));
        assert_eq!(peer_stamp_superseded(&nd(r#", "awaitingPeers": ["p3"]"#), &pair), Num::Int(0), "no answer from p3");
        assert_eq!(peer_stamp_superseded(&nd(r#", "awaitingKind": "job""#), &pair), Num::Int(0), "a job stamp stands");
        let late = r#", "log": [{"kind": "awaiting", "at": 65}, {"kind": "awaiting", "at": 99, "lift": true}]"#;
        assert_eq!(peer_stamp_superseded(&nd(late), &pair), Num::Int(0), "written at 65, after the answer at 60");
        assert_eq!(peer_stamp_superseded(&nd(""), &Answered::Scalar(Num::Float(41.5))), Num::Float(41.5));
    }

    #[test]
    fn int_since_truncates_at_any_size() {
        let it = |n: Num, p| awaiting_item("peer", None, Some(" x "), Some(n), None, None, false, p).unwrap();
        assert_eq!(it(Num::Float(-0.5), None).since, Out::Int(0));
        assert_eq!(it(Num::Float(2.7), None).since, Out::Int(2));
        assert_eq!(it(Num::Float(2.7), Some(Plant::IntRounds)).since, Out::Int(3));
        assert_eq!(it(Num::Float(1e20), None).since, Out::BigInt("100000000000000000000".into()));
        assert_eq!(it(Num::Float(-0.0), None).since, Out::Null, "a falsy since is None");
        assert_eq!(it(Num::Int(7), None).label, "x");
        let e = awaiting_item("tasks", None, None, None, None, None, false, None).unwrap_err();
        assert_eq!(e, Raise { exc: "AssertionError", arg: "tasks".into() });
        let full = awaiting_item("agents", Some("a1"), None, None, Some("ag"), Some(""), true, None).unwrap();
        assert_eq!(romp_json::dumps(&full.to_json()),
                   r#"{"kind": "agents", "id": "a1", "label": "", "since": null, "agentId": "ag", "stoppable": true}"#);
    }

    #[test]
    fn the_uuid_shape_accepts_one_trailing_newline() {
        let u = "11111111-2222-3333-4444-00000000000a";
        assert!(uuidish(u, None) && uuidish(&format!("{u}\n"), None));
        assert!(!uuidish(&format!("{u}\n\n"), None) && !uuidish(&format!("{u} "), None));
        assert!(!uuidish(&format!("{u}\n"), Some(Plant::UuidStrict)));
        assert!(!uuidish("11111111-2222-3333-4444-00000000000\u{ff10}", None), "a fullwidth digit is not [0-9]");
    }

    #[test]
    fn the_identity_ladder() {
        let names = Names(HashMap::from([("s1".to_string(), vec!["one".into(), "/w".into(), "#123456".into()])]));
        let mut world = PeerWorld::default();
        let u = "11111111-2222-3333-4444-000000000009";
        world.remotes = serde_json::from_str(&format!(r#"{{"h1": {{"host": "h1", "sids": ["{u}"], "names": {{}}}}}}"#))
            .unwrap();
        let ctx = Ctx { names: &names, world: &world, plant: None };
        let id = |p: &str| peer_identity(&ctx, Some(p));
        assert_eq!(id("peer:s1"), Identity { name: "one".into(), host: "".into(), sid: "s1".into(),
                                              color: Some("#123456".into()) });
        assert_eq!(id(u), Identity { name: "11111111".into(), host: "h1".into(), sid: u.into(), color: None });
        assert_eq!(id(&format!("h2:{u}")).name, "11111111", "a uuid tail stubs to 8");
        assert_eq!(id("h2:bob").name, "bob");
        assert_eq!(id("h2:").name, "h2:", "an empty tail falls to the stub");
        assert_eq!(id("peer:"), Identity { name: "".into(), host: "".into(), sid: "".into(), color: None });
    }

    #[test]
    fn the_handoff_label_is_lazy_and_spans_lines() {
        assert_eq!(handoff_label("↪ delegated to h:s003: work"), Some(("h:s003", "work")));
        assert_eq!(handoff_label("\u{1c}↪\u{1f}delegated to x: y"), Some(("x", "y")));
        assert_eq!(handoff_label("↪ delegated to x:\ny: z\n"), Some(("x:\ny", "z\n")));
        assert_eq!(handoff_label("↪ delegated to : x"), None, "the first group needs a character");
        assert_eq!(handoff_label("↪ delegated to ::  x"), Some((":", " x")));
        assert_eq!(handoff_label("delegated to a: b"), None);
    }

    #[test]
    fn a_title_is_one_sentence_one_line_inside_the_cap() {
        assert_eq!(title_shaped(Some("e.g. this one. And more"), TITLE_CAP), "e.g. this one.");
        assert_eq!(title_shaped(Some("Short. Then"), TITLE_CAP), "Short. Then", "a stop before 12 is kept");
        assert_eq!(title_shaped(Some("a\n\u{1c}b  c"), TITLE_CAP), "a b c");
        assert_eq!(title_shaped(Some("a\r b  c"), TITLE_CAP), "a\r b  c", "no newline, no collapse");
        let long = "x".repeat(118) + " yz";
        assert_eq!(title_shaped(Some(&long), TITLE_CAP), "x".repeat(118) + "…");
        assert_eq!(title_shaped(None, TITLE_CAP), "");
    }

    #[test]
    fn junk_quotes_lowercase_as_python_does() {
        assert!(junk_quote(Some("  Keep   GOING!! ")));
        assert!(junk_quote(Some("o\u{212a}…")), "the Kelvin sign lowers to k");
        assert!(junk_quote(Some("/compact")) && !junk_quote(Some("/compact now")));
        assert!(!junk_quote(Some("")) && !junk_quote(None) && !junk_quote(Some("okk")));
    }

    #[test]
    fn done_keeps_a_timeless_row_and_block_drops_it() {
        let nd =
            node(r#"{"id": "x", "t": 3, "mt": 9.0, "log": [{"kind": "done"}, {"kind": "block", "ev_t": 0, "at": 0}]}"#);
        assert_eq!(done_since(&nd), Num::Int(0));
        assert_eq!(block_ev_t(&nd), None);
        assert_eq!(block_since(&nd), Num::Float(9.0));
        let tie = node(r#"{"id": "x", "log": [{"kind": "block", "ev_t": 5}, {"kind": "block", "at": 5.0}]}"#);
        assert_eq!(block_ev_t(&tie), Some(Num::Int(5)));
    }

    #[test]
    fn a_cold_lookup_serves_the_remembered_warm_one() {
        let nd = |trail: &str| {
            node(&format!(r#"{{"id": "g1", "trail": [{trail}], "quote": "fix it", "summaryAnchor": "sa"}}"#))
        };
        let work = SegMap(HashMap::from([("s:h1".to_string(), Some("w1".to_string()))]));
        let trig = SegMap(HashMap::from([("s:h1".to_string(), Some("p1".to_string()))]));
        let empty = SegMap::default();
        let mut table = AnchorTable::default();
        let both = (Some("p1".to_string()), Some("w1".to_string()));
        assert_eq!(node_anchor_uuids(&nd(r#""s:1:h1""#), &trig, &work, &mut table), both);
        assert_eq!(node_anchor_uuids(&nd(r#""s:2:h9""#), &empty, &empty, &mut table), both, "served from the table");
        let mut fresh = AnchorTable::default();
        assert_eq!(node_anchor_uuids(&nd(""), &empty, &empty, &mut fresh), (None, Some("sa".into())));
        let mut off = AnchorTable::default();
        anchors_with(&nd(r#""s:1:h1""#), &trig, &work, &mut off, Some(Plant::TableOff));
        assert!(off.0.is_empty());
    }

    #[test]
    fn parked_rows_follow_the_children_order() {
        let m = nodes(
            r#"{"z": {"id": "z", "parentId": "top", "t": 1},
                "h": {"id": "h", "parentId": "top", "t": 2, "handoff": {"peer": "p"}},
                "a": {"id": "a", "parentId": "top", "t": 0.5},
                "top": {"id": "top", "parentId": null}}"#,
        );
        let ns = Ns::new(&m);
        let c = Children::from_parents(&ns);
        assert_eq!(parked_rows(&ns, &c), [("z".to_string(), 1), ("a".to_string(), 1)]);
        assert_eq!(parked_with(&ns, &c, Some(Plant::ParkedSorted)), [("a".to_string(), 1), ("z".to_string(), 1)]);
    }

    #[test]
    fn the_login_label_needs_the_login_carried_and_used() {
        let row: LiveRow =
            serde_json::from_str(r#"{"authLogin": "L1", "authLoginLive": "L1", "authLabel": ""}"#).unwrap();
        let err: serde_json::Value = serde_json::from_str(r#"{"authErr": 1}"#).unwrap();
        assert_eq!(login_refusal_label(Some(&row), Some(&err)), Ok("login".into()));
        assert_eq!(login_refusal_label(None, Some(&err)), Ok("".into()));
        let s = serde_json::Value::String("boom".into());
        assert_eq!(login_refusal_label(Some(&row), Some(&s)).unwrap_err().arg, "'str' object has no attribute 'get'");
        let f: serde_json::Value = serde_json::from_str("1.5").unwrap();
        assert_eq!(login_refusal_label(None, Some(&f)).unwrap_err().arg, "'float' object has no attribute 'get'");
        assert_eq!(login_refusal_label(None, Some(&serde_json::Value::Null)), Ok("".into()));
    }

    #[test]
    fn the_body_children_heal_hide_and_root_a_born_orphan() {
        let m = nodes(
            r#"{"a": {"id": "a", "parentId": null},
                "b": {"id": "b", "parentId": "gone", "born": {"why": "w"}},
                "c": {"id": "c", "parentId": "gone"},
                "d": {"id": "d", "parentId": null},
                "e": {"id": "e", "parentId": "a"}}"#,
        );
        let healed: Healed =
            serde_json::from_str(r#"{"d": ["a", {"why": "healed"}], "e": [null, {"hidden": true}]}"#).unwrap();
        let (c, hidden) = body_children(&Ns::new(&m), &healed).unwrap();
        assert_eq!(romp_json::dumps(&c.to_json()), r#"[[null, ["a", "b"]], ["gone", ["c"]], ["a", ["d"]]]"#);
        assert_eq!(hidden, ["e"]);
        let face = session_started_face(&Ns::new(&m), "d", &healed).unwrap();
        assert_eq!(face, Face { why: "healed".into(), parent: None }, "d's own parent is None");
    }

    #[test]
    fn a_healed_forest_past_the_depth_limit_is_refused() {
        // dump_layer4.py's edge set holds the forest at the limit (Python answers it, and so does the port); one past
        // the limit is refused here, where Python would still answer (it raises near 1,000)
        let forest = |n: usize| {
            let nodes: Vec<String> = (0..n).map(|i| format!(r#""h{i}": {{"id": "h{i}", "parentId": null}}"#)).collect();
            let heal: Vec<String> = (1..n).map(|i| format!(r#""h{i}": ["h{}", {{"why": "heal"}}]"#, i - 1)).collect();
            let m: OMap<Node> = serde_json::from_str(&format!("{{{}}}", nodes.join(", "))).unwrap();
            let h: Healed = serde_json::from_str(&format!("{{{}}}", heal.join(", "))).unwrap();
            (m, h)
        };
        let (m, h) = forest(MAX_NODE_DEPTH);
        assert!(body_children(&Ns::new(&m), &h).is_ok());
        let (m, h) = forest(MAX_NODE_DEPTH + 1);
        assert_eq!(body_children(&Ns::new(&m), &h).unwrap_err(),
                   format!("the healed children forest is {} nodes deep, past the {MAX_NODE_DEPTH} the port admits",
                           MAX_NODE_DEPTH + 1));
    }

    #[test]
    fn open_leaves_pierce_a_done_marker_over_an_agent_open_node() {
        let m = nodes(
            r#"{"t": {"id": "t", "parentId": null, "nodeComplete": true},
                "x": {"id": "x", "parentId": "t", "nodeComplete": true, "agentTask": {"status": "open"}},
                "y": {"id": "y", "parentId": "t", "handoff": {"peer": "p"}},
                "z": {"id": "z", "parentId": "t", "cleared": true}}"#,
        );
        let ns = Ns::new(&m);
        assert_eq!(open_leaves(&ns, "t"), ["x", "y"]);
        assert!(!all_outstanding_delegated(&ns, "t"));
        let aopen = agent_open_set(&ns, &Children::from_parents(&ns));
        assert_eq!(aopen, HashSet::from(["t".to_string(), "x".to_string()]));
    }

    #[test]
    fn falsy_times_end_on_the_literal_int_zero() {
        // `mt or t or 0` and `ev_t or at or 0`: when every operand is falsy, the answer is the trailing literal
        assert_eq!(block_since(&node(r#"{"id": "x", "mt": 0, "t": 0.0}"#)), Num::Int(0));
        assert_eq!(block_since(&node(r#"{"id": "x", "mt": 0, "t": -0.0}"#)), Num::Int(0));
        assert_eq!(done_since(&node(r#"{"id": "x", "log": [{"kind": "done", "at": 0.0}]}"#)), Num::Int(0));
        assert_eq!(block_since(&node(r#"{"id": "x", "mt": 0, "t": 2.0}"#)), Num::Float(2.0));
    }

    #[test]
    fn an_empty_id_is_never_remembered_and_an_empty_born_hides_the_heal() {
        let work = SegMap(HashMap::from([("s:h1".to_string(), Some("w1".to_string()))]));
        let empty = SegMap::default();
        let mut table = AnchorTable::default();
        let warm = node(r#"{"id": "", "trail": ["s:1:h1"]}"#);
        assert_eq!(node_anchor_uuids(&warm, &empty, &work, &mut table), (None, Some("w1".into())));
        assert!(table.0.is_empty(), "`if nid`: an empty id is not written");
        let m = nodes(r#"{"p": {"id": "p", "text": "ask"}, "a": {"id": "a", "parentId": "p", "born": {}}}"#);
        let healed: Healed = serde_json::from_str(r#"{"a": ["p", {"why": "from the heal"}]}"#).unwrap();
        assert_eq!(session_started_face(&Ns::new(&m), "a", &healed), None, "an own empty born is not None, and falsy");
    }

    #[test]
    fn review_boundary_advances_to_the_watermark() {
        let nd = |b: &str, s: &str, ev: &str| {
            let log = format!(r#"[{{"kind": "reopen", "ev_t": {ev}}}]"#);
            node(&format!(r#"{{"id": "x", "deltaSince": {b}, "distilledMt": 10, "summary": {s}, "log": {log}}}"#))
        };
        assert_eq!(review_boundary(&nd("5", "\"s\"", "11")), Some(Num::Int(10)));
        assert_eq!(review_boundary(&nd("null", "\"s\"", "11")), Some(Num::Int(10)));
        assert_eq!(review_boundary(&nd("10.0", "\"s\"", "11")), Some(Num::Float(10.0)), "dm is not past b");
        assert_eq!(review_boundary(&nd("5", "\" \\u001c\"", "11")), Some(Num::Int(5)), "a blank summary");
        assert_eq!(review_boundary(&nd("5", "\"s\"", "10")), Some(Num::Int(5)), "the reopen must be after dm");
    }
}
