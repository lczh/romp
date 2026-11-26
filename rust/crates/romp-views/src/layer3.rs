//! Layer 3 of rust/notes/s2-design.md section 2, "Tree facts and the anchor walk", over the typed tree and store:
//! kernel._session_working with _suspended_after, _open_turn_progress and _last_plain_user_turn_t; _seg_anchors,
//! _seg_jump (unlisted: the fold calls it when a segment has no reply), jd._prompt_anchor_uuid and _seg_last_text; and
//! _feed_seg_fold, _feed_seg_walk, _feed_seg_plain_t and _FeedSegTurns.get, by default without the held aggregate, so
//! the walk folds every turn. S6 added the held aggregate (`SegHeld`: _FEED_SEG_HELD with _turn_hold_key, _seams_fp,
//! the prefix compare, _feed_seg_copy, the store under the byte bound and _feed_seg_forget), used only when a build is
//! given a table (`layer6::Build::with_held`), and the cheap bound's `ClosedPart`. Each function follows its Python line
//! for line. What a naive port would get wrong, each pinned by the edge cases (tools/rust-spike/dump_layer3.py
//! --edge-cases) and the randomized check (--fuzz):
//! - The fold's maps are Python dicts: a later segment with the same key (jd._seg_key, `rompuuid:hash`, so two prompts
//!   with the same text in two turns share one) overwrites the earlier one's value but keeps its place. The four maps
//!   are always written together, so `SegMaps` holds one insertion-ordered map (`PyDict`) of all four values. That
//!   relies on nothing raising between Python's `pos[k] = (ti, si)` and `seg_best[k] = ...` inside one segment, which
//!   holds on every tree the loader admits (the only known raiser there, a string message, an AttributeError in
//!   em._stop_reason, is refused at load). A loader that admitted such a shape would need four separate maps.
//! - A segmentation raise (layer 2's `Err(Raise)`) ends the walk with the maps as far as the fold got and `ok` false; it
//!   is never a panic. _FeedSegTurns.get answers its default when segmentation raises, and counts a fault.
//! - `max(best, x)` keeps the first of equal values (5 before 5.0), so the plain-prompt time's int/float tag depends on
//!   the order of the turns; `a.get("t") or turn.get("t") or 0` skips a zero time.
//! - _open_turn_progress counts tool uses over `(message or {}).get("content") or []`, as Python does, not through
//!   em._content. Not a pinned trap: the S4.3 review found that counting through em._content gives the same answer on
//!   every shape the loader admits (a bare-string content, non-dict blocks, `{}` and null included). It is kept for
//!   fidelity. The same holds for `_feed_seg_plain_t`'s closed/open split and its trailing `or 0`.
//! - `x or y` over uuids: "" is falsy, so an empty reply uuid falls through to the settle, then to _seg_jump.
//! - With no trigger, _last_plain_user_turn_t's anchor search looks for `uuid == None`, which an idle atom (uuid null)
//!   or an atom without a uuid matches before `atoms[0]` is tried.
//! - _suspended_after reads the board's downtime list (`snapshot.board.downtime`, typed as `(start, end)` pairs).
//!
//! The restored pre-cut branch of _feed_seg_fold (`em.turn_scalar(turn, "pcs")`, its stored prose counts) cannot run:
//! em.turn_scalar is None unless `pre` is truthy, and the loader refuses such a turn (`tree::validate_turn`). The
//! exception _open_turn_progress swallows per atom cannot happen either: the loader admits only a dict message whose
//! content is a list or a string.

use std::cmp::Ordering;
use crate::fxhash::{FxMap as HashMap, FxSet as HashSet};

use romp_json::Value as Out;

use crate::layer1;
use crate::layer2::{self, Raise, Segment};
use crate::record::{py_truthy, Num, ToJson};
use crate::snapshot::Store;
use crate::tree::{Atom, Author, Block, Content, Tree, Turn};

/// jd.CITE_MIN_CHARS: the substantive floor (_seg_last_text's preference, the citable-uuid set).
pub const CITE_MIN_CHARS: usize = layer1::CITE_MIN_CHARS;

/// A key no fold produces (no colon-free seg id is a key the fold writes): the query each answer makes for an absent
/// key (`k in seg_turn`, `seg_turn.get(k)`).
pub const ABSENT_KEY: &str = "absent-key";

/// One deliberate mistake, for `romp-spike layer3 --plant`: the comparison must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// `max` keeps the last of equal values (the plain-prompt time's tag flips on a tie).
    MaxKeepsLast,
    /// A key seen again moves to the end of the maps (a delete and an insert, not a dict overwrite).
    OverwriteMoves,
    /// The substantive floor (_seg_last_text, the citable set) counts UTF-8 bytes, not code points.
    FloorInBytes,
    /// A segmentation raise skips its turn and the fold goes on, the walk still ok.
    RaiseSkipsTurn,
}

impl Plant {
    pub fn parse(name: &str) -> Option<Plant> {
        match name {
            "max-last" => Some(Plant::MaxKeepsLast),
            "overwrite-moves" => Some(Plant::OverwriteMoves),
            "bytes" => Some(Plant::FloorInBytes),
            "raise-skips" => Some(Plant::RaiseSkipsTurn),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Plant::MaxKeepsLast => "max-last",
            Plant::OverwriteMoves => "overwrite-moves",
            Plant::FloorInBytes => "bytes",
            Plant::RaiseSkipsTurn => "raise-skips",
        }
    }
}

/// Python's `max(a, b)`: `b` only when it is greater, so the first of equal values is kept (`max(5, 5.0)` is 5).
pub fn py_max(a: Num, b: Num) -> Num {
    if b.py_cmp(a) == Ordering::Greater { b } else { a }
}

fn max_with(a: Num, b: Num, plant: Option<Plant>) -> Num {
    match plant {
        Some(Plant::MaxKeepsLast) if b.py_cmp(a) != Ordering::Less => b,
        _ => py_max(a, b),
    }
}

/// Python's `a or b` over uuids: None and "" are falsy.
pub fn py_or<'a>(a: Option<&'a str>, b: Option<&'a str>) -> Option<&'a str> {
    if a.is_some_and(|s| !s.is_empty()) { a } else { b }
}

fn truthy_str(s: Option<&str>) -> bool {
    s.is_some_and(|s| !s.is_empty())
}

fn is_api_error(a: &Atom) -> bool {
    matches!(a.is_api_error, Some(Some(true)))
}

/// A Python dict keyed by string: insertion-ordered, and an existing key's value is overwritten in place (it keeps the
/// position it was first inserted at).
#[derive(Debug, Clone)]
pub struct PyDict<V> {
    entries: Vec<(String, V)>,
    index: HashMap<String, usize>,
}

impl<V> Default for PyDict<V> {
    fn default() -> Self {
        PyDict { entries: Vec::new(), index: HashMap::default() }
    }
}

impl<V> PyDict<V> {
    /// `d[k] = v`.
    pub fn insert(&mut self, k: String, v: V) {
        match self.index.get(&k) {
            Some(&i) => self.entries[i].1 = v,
            None => {
                self.index.insert(k.clone(), self.entries.len());
                self.entries.push((k, v));
            }
        }
    }

    /// `del d[k]; d[k] = v` (the planted mistake: the key moves to the end).
    fn insert_moving(&mut self, k: String, v: V) {
        if let Some(i) = self.index.remove(&k) {
            self.entries.remove(i);
            for (j, (key, _)) in self.entries.iter().enumerate().skip(i) {
                self.index.insert(key.clone(), j);
            }
        }
        self.insert(k, v);
    }

    pub fn get(&self, k: &str) -> Option<&V> {
        self.index.get(k).map(|&i| &self.entries[i].1)
    }

    pub fn contains_key(&self, k: &str) -> bool {
        self.index.contains_key(k)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, &V)> {
        self.entries.iter().map(|(k, v)| (k.as_str(), v))
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

// ───────────────────────────── tree facts ─────────────────────────────

/// kernel._suspended_after(t): whether some host suspension in `downtime` began after `t` (`start > (t or 0)`).
pub fn suspended_after(downtime: &[(Num, Num)], t: Option<Num>) -> bool {
    let t = t.filter(|x| x.truthy()).unwrap_or(Num::Int(0));
    downtime.iter().any(|(start, _end)| start.py_cmp(t) == Ordering::Greater)
}

/// The time _session_working hands _suspended_after: the last turn's `end` (else its `t`; `end` is required at load).
pub fn last_activity(turns: &[Turn]) -> Option<Num> {
    turns.last().map(|lt| lt.end.or(lt.t).expect("a turn's end is required at load"))
}

/// kernel._session_working(turns): the last turn is open (not ended, its last atom past any trailing command atoms is
/// not an idle span) and the host has not slept since its last activity.
pub fn session_working(turns: &[Turn], downtime: &[(Num, Num)]) -> bool {
    let Some(lt) = turns.last() else { return false };
    let atoms = lt.atoms.as_deref().unwrap_or_default();
    let mut i = atoms.len();
    while i > 0 && atoms[i - 1].command.as_ref().is_some_and(py_truthy) {
        i -= 1;
    }
    if lt.ended == Some(true) || (i > 0 && atoms[i - 1].kind() == "idle") {
        return false;
    }
    !suspended_after(downtime, last_activity(turns))
}

/// What _open_turn_progress returns for an open last turn: `{"since": turn["t"], "toolUses": n}`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Progress {
    pub since: Num,
    pub tool_uses: usize,
}

/// kernel._open_turn_progress(turns): None without turns or when the last turn ended; else its `t` and the count of
/// tool_use blocks over its assistant atoms' raw content (`(message or {}).get("content") or []`).
pub fn open_turn_progress(turns: &[Turn]) -> Option<Progress> {
    let lt = turns.last()?;
    if lt.ended == Some(true) {
        return None;
    }
    let mut n = 0;
    for a in lt.atoms.as_deref().unwrap_or_default() {
        if a.kind() != "assistant" {
            continue;
        }
        // `(a.get("message") or {})`: an empty dict is falsy; a string content iterates characters, none a dict
        if let Some(Content::Blocks(blocks)) = a.message().filter(|m| m.truthy()).and_then(|m| m.content.as_ref()) {
            n += blocks.iter().filter_map(Block::obj).filter(|b| b.is("tool_use")).count();
        }
    }
    Some(Progress { since: lt.t.expect("a turn's t is required at load"), tool_uses: n })
}

/// kernel._last_plain_user_turn_t(turns): the latest time of a turn whose anchor atom (its trigger's, else its first)
/// is a human prompt that is not an echo and carries no `romp-goal-id` marker; 0 if none.
pub fn last_plain_user_turn_t(turns: &[Turn]) -> Num {
    last_plain_with(turns, None)
}

fn last_plain_with(turns: &[Turn], plant: Option<Plant>) -> Num {
    let mut best = Num::Int(0);
    for turn in turns {
        let atoms = turn.atoms.as_deref().unwrap_or_default();
        // trig = turn.get("trigger") or {}; tuid = trig.get("uuid"): None for an absent, null or empty trigger
        let tuid = match &turn.trigger {
            Some(Some(tr)) => tr.uuid.as_ref().and_then(|u| u.as_deref()),
            _ => None,
        };
        // `x.get("uuid") == tuid`: with no trigger uuid, the first atom without one (an idle span) matches
        let Some(a) = atoms.iter().find(|x| x.uuid() == tuid).or(atoms.first()) else { continue };
        let human = matches!(&a.author, Some(Some(Author::Name(n))) if n == "human");
        if !human || a.echo_text.as_ref().is_some_and(py_truthy) || turn.echo_turn.as_ref().is_some_and(py_truthy) {
            continue;
        }
        if layer1::atom_user_text(a).is_some_and(|t| t.contains("romp-goal-id")) {
            continue;
        }
        let x = [a.t, turn.t].into_iter().flatten().find(|x| x.truthy()).unwrap_or(Num::Int(0));
        best = max_with(best, x, plant);
    }
    best
}

// ───────────────────────────── the anchors ─────────────────────────────

/// kernel._seg_anchors(atoms): (workUuid, replyUuid). Work is the first assistant atom that is not a bare end; the
/// reply the last assistant atom with text that is not the settle filler, else the last settle. API errors skipped.
pub fn seg_anchors<'a>(atoms: &[&'a Atom]) -> (Option<&'a str>, Option<&'a str>) {
    let (mut work, mut reply, mut settle) = (None, None, None);
    for &a in atoms {
        if a.kind() != "assistant" || is_api_error(a) {
            continue;
        }
        if work.is_none() && !layer1::atom_is_bare_end(a) {
            work = a.uuid();
        }
        if layer1::atom_has_text(a) {
            if layer1::atom_is_settle(a) {
                settle = a.uuid();
            } else {
                reply = a.uuid();
            }
        }
    }
    (work, py_or(reply, settle))
}

/// kernel._seg_jump(atoms): the reply, else the first assistant atom with a uuid that carries text or a tool use.
pub fn seg_jump<'a>(atoms: &[&'a Atom]) -> Option<&'a str> {
    let (_work, reply) = seg_anchors(atoms);
    if truthy_str(reply) {
        return reply;
    }
    atoms
        .iter()
        .copied()
        .find(|a| {
            a.kind() == "assistant"
                && !is_api_error(a)
                && truthy_str(a.uuid())
                && (layer1::atom_has_text(a) || !layer1::atom_tool_uses(a).is_empty())
        })
        .and_then(Atom::uuid)
}

/// jd._prompt_anchor_uuid(seg): the trigger uuid, unless that atom is an attachment; then the segment's first user atom
/// with a uuid, else the trigger. None when the segment has no trigger (None or "").
pub fn prompt_anchor_uuid<'a>(seg: &Segment<'a>) -> Option<&'a str> {
    let t = seg.trigger.filter(|t| !t.is_empty())?;
    match seg.atoms.iter().find(|a| a.uuid() == Some(t)) {
        Some(ta) if ta.kind() == "attachment" => seg
            .atoms
            .iter()
            .find(|a| a.kind() == "user" && truthy_str(a.uuid()))
            .and_then(|a| a.uuid())
            .or(Some(t)),
        _ => Some(t),
    }
}

fn prose_chars(a: &Atom, plant: Option<Plant>) -> usize {
    if plant != Some(Plant::FloorInBytes) {
        return layer1::kernel_atom_prose_chars(a);
    }
    if a.kind() != "assistant" || is_api_error(a) {
        return 0;
    }
    match a.message().filter(|m| m.truthy()).and_then(|m| m.content.as_ref()) {
        Some(Content::Blocks(b)) => layer1::Blocks::List(b).texts().map(str::len).sum(),
        _ => 0,
    }
}

/// kernel._seg_last_text(atoms): (uuid, substantive), the last assistant prose atom with a uuid, preferring the last
/// one of at least CITE_MIN_CHARS code points (kernel._atom_prose_chars).
pub fn seg_last_text<'a>(atoms: &[&'a Atom]) -> (Option<&'a str>, bool) {
    last_text_with(atoms, None)
}

fn last_text_with<'a>(atoms: &[&'a Atom], plant: Option<Plant>) -> (Option<&'a str>, bool) {
    let (mut last_any, mut last_sub) = (None, None);
    for &a in atoms {
        let n = prose_chars(a, plant);
        if n > 0
            && let Some(u) = a.uuid().filter(|u| !u.is_empty())
        {
            last_any = Some(u);
            if n >= CITE_MIN_CHARS {
                last_sub = Some(u);
            }
        }
    }
    (py_or(last_sub, last_any), last_sub.is_some())
}

// ───────────────────────────── the walk ─────────────────────────────

/// `seg_best[k]`: the segment's latest prose (`_seg_last_text`) and its start, `seg.get("t", 0)` with its tag.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SegBest<'a> {
    pub uuid: Option<&'a str>,
    pub substantive: bool,
    pub t: Num,
}

/// What the fold stores under one segment key, across the four maps the loop wrote together.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SegEntry<'a> {
    /// `pos[k]`: (turn index, segment ordinal), what _FeedSegTurns.get re-segments.
    pub pos: (usize, usize),
    /// `seg_uuid[k]`: the work anchor, `r or _seg_jump(atoms)`.
    pub uuid: Option<&'a str>,
    /// `seg_trig[k]`: the prompt anchor, jd._prompt_anchor_uuid(seg).
    pub trig: Option<&'a str>,
    /// `seg_best[k]`.
    pub best: SegBest<'a>,
}

/// The anchor maps (seg_uuid, seg_trig, seg_best, positions as one dict, keyed by jd._seg_key of the segment id) and
/// the citable-uuid set.
#[derive(Debug, Clone, Default)]
pub struct SegMaps<'a> {
    pub segs: PyDict<SegEntry<'a>>,
    pub cite: HashSet<&'a str>,
}

/// kernel._feed_seg_fold(maps, turns, start, stop, store): fold turns[start:stop] into the maps, segment by segment in
/// turn order (kernel._segs_seam). A raise leaves the maps as far as the fold got.
pub fn feed_seg_fold<'a>(
    maps: &mut SegMaps<'a>,
    turns: &'a [Turn],
    start: usize,
    stop: usize,
    store: Option<&'a Store>,
) -> Result<(), Raise> {
    fold_with(maps, turns, start, stop, store, None)
}

fn fold_with<'a>(
    maps: &mut SegMaps<'a>,
    turns: &'a [Turn],
    start: usize,
    stop: usize,
    store: Option<&'a Store>,
    plant: Option<Plant>,
) -> Result<(), Raise> {
    for (ti, turn) in turns.iter().enumerate().take(stop).skip(start) {
        let segs = match layer2::segs_seam(turn, store) {
            Ok(s) => s,
            Err(_) if plant == Some(Plant::RaiseSkipsTurn) => continue,
            Err(r) => return Err(r),
        };
        for (si, seg) in segs.iter().enumerate() {
            let atoms = seg.atoms.as_slice();
            let k = layer1::kernel_seg_key(&seg.id).into_owned();
            let (_work, r) = seg_anchors(atoms);
            let uuid = if truthy_str(r) { r } else { seg_jump(atoms) };
            let (lu, lsub) = last_text_with(atoms, plant);
            let entry = SegEntry {
                pos: (ti, si),
                uuid,
                trig: prompt_anchor_uuid(seg),
                best: SegBest { uuid: lu, substantive: lsub, t: seg.t },
            };
            if plant == Some(Plant::OverwriteMoves) {
                maps.segs.insert_moving(k, entry);
            } else {
                maps.segs.insert(k, entry);
            }
            // em.turn_scalar(turn, "pcs") is None on every turn the loader admits (module comment): the atoms count
            for &a in atoms {
                if let Some(u) = a.uuid().filter(|u| !u.is_empty())
                    && prose_chars(a, plant) >= CITE_MIN_CHARS
                {
                    maps.cite.insert(u);
                }
            }
        }
    }
    Ok(())
}

/// One turn's segment, as _FeedSegTurns.get returns it: `(turn, segment atoms)`, with the turn's index.
#[derive(Debug, Clone, PartialEq)]
pub struct SegTurn<'a> {
    pub ti: usize,
    pub turn: &'a Turn,
    pub atoms: Vec<&'a Atom>,
}

/// kernel._FeedSegTurns: a seg key to (turn, segment atoms), answered lazily from the positions the fold recorded, each
/// turn segmented at most once (a raising one is retried, and faults each time).
#[derive(Debug)]
pub struct FeedSegTurns<'a> {
    turns: &'a [Turn],
    store: Option<&'a Store>,
    segs: HashMap<usize, Vec<Segment<'a>>>,
    /// `_SUMMARY_ANCHOR_STATS["fault"]` increments: a turn index out of range or a segmentation that raised.
    pub faults: usize,
}

impl<'a> FeedSegTurns<'a> {
    pub fn new(turns: &'a [Turn], store: Option<&'a Store>) -> Self {
        FeedSegTurns { turns, store, segs: HashMap::default(), faults: 0 }
    }

    /// `get(k)` where the positions map holds `pos` under `k` (None: `k` is absent). The segment at the ordinal when it
    /// carries `k`, else the turn's last segment with `k`, else the default (None).
    pub fn get_at(&mut self, k: &str, pos: Option<(usize, usize)>) -> Option<SegTurn<'a>> {
        let (ti, si) = pos?;
        if !self.segs.contains_key(&ti) {
            // `_segs_seam(self._turns[ti], ...)` inside the try: an index out of range faults as a raise does
            let Some(turn) = self.turns.get(ti) else {
                self.faults += 1;
                return None;
            };
            match layer2::segs_seam(turn, self.store) {
                Ok(s) => {
                    self.segs.insert(ti, s);
                }
                Err(_) => {
                    self.faults += 1;
                    return None;
                }
            }
        }
        let segs = &self.segs[&ti];
        let has_k = |s: &&Segment<'a>| layer1::kernel_seg_key(&s.id) == k;
        let seg = segs.get(si).filter(has_k).or_else(|| segs.iter().rev().find(has_k))?;
        Some(SegTurn { ti, turn: &self.turns[ti], atoms: seg.atoms.clone() })
    }
}

/// kernel._feed_seg_walk's result, without the held aggregate: the maps, whether the fold finished, and what
/// _feed_seg_plain_t and the lazy `seg_turn` read.
#[derive(Debug)]
pub struct FeedSegWalk<'a> {
    pub maps: SegMaps<'a>,
    /// `w.ok`: false when the fold raised (the maps are as far as it got).
    pub ok: bool,
    /// The raise that stopped the fold (Python swallows it; kept for diagnostics).
    pub raised: Option<Raise>,
    pub turns: &'a [Turn],
    pub n_closed: usize,
    pub seg_turn: FeedSegTurns<'a>,
    /// `w.start`: the first turn the walk folded (0 unless it began from a closed part, `ClosedPart`).
    pub start: usize,
    /// `w.base_plain`: the closed part's plain-prompt time (0 with none), which _feed_seg_plain_t starts from.
    pub base_plain: Num,
    /// What a walk over a held table carries to its finish (`SegHeld::finish`): None for a walk with no table.
    pub hold: Option<Box<Hold<'a>>>,
}

/// A held walk's state between `SegHeld::walk` and `SegHeld::finish` (the _FeedSegWalk fields Python's store reads).
#[derive(Debug)]
pub struct Hold<'a> {
    fsid: String,
    head: Option<Head<'a>>,
    fp: Vec<SeamFp<'a>>,
    keys: Vec<HoldKey<'a>>,
    /// `w.closed`: the maps over the closed turns, when the walk folded closed turns and has keys to hold them under.
    closed: Option<SegMaps<'a>>,
}

/// The closed turns' part of a walk (S6, rust/SPIKE.md "S6 method"): the maps over `turns[..n_closed]` and their
/// plain-prompt time, as kernel._FEED_SEG_HELD keeps them (its entry's maps and plain_t). S6's cheap bound folds it
/// ahead of the timed build (`closed_part`, outside the clock) and hands it to `feed_seg_walk_from`, which then folds
/// only the open turn: the held path with none of its own costs (the hold keys, the seams' fingerprint, the prefix
/// compare, the map copy, the store), so it flatters the port. Not the held aggregate itself.
#[derive(Debug, Clone)]
pub struct ClosedPart<'a> {
    pub maps: SegMaps<'a>,
    pub plain_t: Num,
}

/// The closed part of `turns` (every turn but the last) for `feed_seg_walk_from`, or None when there is no closed turn
/// (kernel._feed_seg_walk holds nothing then) or its fold raises (Python's walk then stores nothing). The plain-prompt
/// time is `max(0, _last_plain_user_turn_t(closed))`, what _feed_seg_plain_t stores as the aggregate's plain_t.
pub fn closed_part<'a>(turns: Option<&'a [Turn]>, store: Option<&'a Store>) -> Option<ClosedPart<'a>> {
    let turns = turns.unwrap_or_default();
    let n_closed = turns.len().saturating_sub(1);
    if n_closed == 0 {
        return None;
    }
    let mut maps = SegMaps::default();
    fold_with(&mut maps, turns, 0, n_closed, store, None).ok()?;
    let plain_t = py_max(Num::Int(0), last_plain_with(&turns[..n_closed], None));
    Some(ClosedPart { maps, plain_t })
}

impl<'a> FeedSegWalk<'a> {
    /// `k in seg_turn`.
    pub fn seg_turn_contains(&self, k: &str) -> bool {
        self.maps.segs.contains_key(k)
    }

    /// `seg_turn.get(k)`.
    pub fn seg_turn_get(&mut self, k: &str) -> Option<SegTurn<'a>> {
        let pos = self.maps.segs.get(k).map(|e| e.pos);
        self.seg_turn.get_at(k, pos)
    }
}

/// kernel._feed_seg_walk(fsid, head, turns, store) with nothing held: the closed turns, then the open one, folded
/// into one set of maps (`turns` None is no turns). A raise in either fold stops the walk, not ok.
pub fn feed_seg_walk<'a>(turns: Option<&'a [Turn]>, store: Option<&'a Store>) -> FeedSegWalk<'a> {
    walk_with(turns, store, None)
}

fn walk_with<'a>(turns: Option<&'a [Turn]>, store: Option<&'a Store>, plant: Option<Plant>) -> FeedSegWalk<'a> {
    walk_from(turns, store, None, plant)
}

/// The walk begun from `closed` (S6's cheap bound, `ClosedPart`): its maps and plain-prompt time stand for every closed
/// turn, so only the open turn is folded, as kernel._feed_seg_walk folds a session whose held entry covers every closed
/// turn (`start == n_closed`). With None, or no closed turn, it is `feed_seg_walk`. The maps and the plain-prompt time
/// come out as the walk over every turn gives them, since the fold is sequential and the closed part is that fold's
/// prefix.
pub fn feed_seg_walk_from<'a>(
    turns: Option<&'a [Turn]>,
    store: Option<&'a Store>,
    closed: Option<ClosedPart<'a>>,
) -> FeedSegWalk<'a> {
    walk_from(turns, store, closed, None)
}

fn walk_from<'a>(
    turns: Option<&'a [Turn]>,
    store: Option<&'a Store>,
    closed: Option<ClosedPart<'a>>,
    plant: Option<Plant>,
) -> FeedSegWalk<'a> {
    let turns = turns.unwrap_or_default();
    let n = turns.len();
    let n_closed = n.saturating_sub(1);
    let (mut maps, start, base_plain) = match closed {
        Some(c) if n_closed > 0 => (c.maps, n_closed, c.plain_t),
        _ => (SegMaps::default(), 0, Num::Int(0)),
    };
    let done = fold_with(&mut maps, turns, start, n_closed, store, plant)
        .and_then(|()| fold_with(&mut maps, turns, n_closed, n, store, plant));
    let (ok, raised) = match done {
        Ok(()) => (true, None),
        Err(r) => (false, Some(r)),
    };
    FeedSegWalk { maps, ok, raised, turns, n_closed, seg_turn: FeedSegTurns::new(turns, store), start, base_plain, hold: None }
}

// ───────────────────────────── the held aggregate (S6) ─────────────────────────────

/// kernel._turn_hold_key(turn): (id, ended, atom count, end, the trigger's uuid, the newest atom uuid that is truthy).
/// A restored pre-cut turn answers from its stored uuids in Python; the loader refuses such a turn (`pre`).
#[derive(Debug, Clone, Copy)]
pub struct HoldKey<'a> {
    id: Option<&'a str>,
    ended: bool,
    atoms: usize,
    end: Option<Num>,
    trig: Option<&'a str>,
    last: Option<&'a str>,
}

fn num_eq(a: Option<Num>, b: Option<Num>) -> bool {
    match (a, b) {
        (Some(a), Some(b)) => a.py_cmp(b) == Ordering::Equal,
        (None, None) => true,
        _ => false,
    }
}

impl PartialEq for HoldKey<'_> {
    /// Python's tuple `==`: numbers by value across int and float.
    fn eq(&self, o: &Self) -> bool {
        self.id == o.id
            && self.ended == o.ended
            && self.atoms == o.atoms
            && num_eq(self.end, o.end)
            && self.trig == o.trig
            && self.last == o.last
    }
}

pub fn turn_hold_key(turn: &Turn) -> HoldKey<'_> {
    let atoms = turn.atoms.as_deref().unwrap_or_default();
    let trig = match &turn.trigger {
        Some(Some(tr)) => tr.uuid.as_ref().and_then(|u| u.as_deref()),
        _ => None,
    };
    let last = atoms.iter().rev().find_map(|a| a.uuid().filter(|u| !u.is_empty()));
    HoldKey { id: turn.id.as_deref(), ended: turn.ended.unwrap_or(false), atoms: atoms.len(), end: turn.end, trig, last }
}

/// One seam as kernel._seams_fp keeps it: (segs, `t` or 0, top, `text` or ""), compared by value.
#[derive(Debug, Clone, Copy)]
pub struct SeamFp<'a> {
    segs: &'a [String],
    t: Num,
    top: Option<&'a str>,
    text: Option<&'a str>,
}

impl PartialEq for SeamFp<'_> {
    fn eq(&self, o: &Self) -> bool {
        self.segs == o.segs && self.t.py_cmp(o.t) == Ordering::Equal && self.top == o.top && self.text == o.text
    }
}

/// kernel._seams_fp(store): every seam with truthy `segs`, in store order. The loader types seams as dicts with a
/// string list for `segs`, so the fingerprint cannot raise here (Python's walk then holds nothing).
pub fn seams_fp(store: Option<&Store>) -> Vec<SeamFp<'_>> {
    let seams = store.and_then(|st| st.seams.as_ref()).and_then(|s| s.as_deref()).unwrap_or_default();
    seams
        .iter()
        .filter_map(|s| {
            let segs = s.segs.as_ref().and_then(|v| v.as_deref()).filter(|v| !v.is_empty())?;
            Some(SeamFp {
                segs,
                t: s.t.unwrap_or(Num::Int(0)),
                top: s.top.as_ref().and_then(|t| t.as_deref()),
                text: match &s.text {
                    None => Some(""),
                    Some(t) => t.as_deref(),
                },
            })
        })
        .collect()
}

/// kernel._FEED_SEG_STATS: the held walk's counters (the names GET /perf reports in builds.feed.memo).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SegStats {
    pub seg_walks: u64,
    pub seg_held: u64,
    pub seg_walk_turns: u64,
    pub seg_partial: u64,
    pub seg_reset: u64,
    pub seg_raised: u64,
    pub seg_evict: u64,
    pub seg_over_bound: u64,
}

impl SegStats {
    /// (name, value) in kernel._FEED_SEG_STATS's order.
    pub fn named(&self) -> [(&'static str, u64); 8] {
        [
            ("segWalks", self.seg_walks),
            ("segHeld", self.seg_held),
            ("segWalkTurns", self.seg_walk_turns),
            ("segPartial", self.seg_partial),
            ("segReset", self.seg_reset),
            ("segRaised", self.seg_raised),
            ("segEvict", self.seg_evict),
            ("segOverBound", self.seg_over_bound),
        ]
    }
}

/// The leaf a held aggregate is valid under: the transcript's path and inode, as the key's stat took them
/// (`ctx["leaf_head"]`, `(str(path), transcript[0][0])`). The S2 replay hands `(path, 0)`; the memo's key takes the inode
/// from the snapshot's recorded identity (memo.rs), so a rewrite under a new inode resets the aggregate as in Python.
pub type Head<'a> = (&'a str, i64);

/// One held entry: (head, seams, keys, maps, plain_t, bytes, build) as kernel._FEED_SEG_HELD keeps it, with `tick` for
/// the OrderedDict's least-recently-used order (bumped where Python moves the entry to the newest end).
#[derive(Debug)]
struct HeldEntry<'a> {
    head: Head<'a>,
    fp: Vec<SeamFp<'a>>,
    keys: Vec<HoldKey<'a>>,
    maps: SegMaps<'a>,
    plain_t: Num,
    bytes: u64,
    build: u64,
    tick: u64,
}

/// kernel._feed_seg_entry_bytes's estimate: 64 + 480 per turn key + 700 per segment key + 120 per citable uuid.
fn entry_bytes(keys: usize, maps: &SegMaps<'_>) -> u64 {
    64 + 480 * keys as u64 + 700 * maps.segs.len() as u64 + 120 * maps.cite.len() as u64
}

/// kernel._FEED_SEG_HELD and its counters across builds: a session's closed turns' maps, held under the turns' keys,
/// its leaf and its seams, so the next walk folds only the turns since (the comment above _FEED_SEG_HELD).
#[derive(Debug)]
pub struct SegHeld<'a> {
    entries: HashMap<String, HeldEntry<'a>>,
    build: u64,
    tick: u64,
    bound: u64,
    pub stats: SegStats,
}

impl<'a> SegHeld<'a> {
    /// An empty table under the byte bound (kernel.FEED_SEG_HELD_BYTES).
    pub fn new(bound: u64) -> Self {
        SegHeld { entries: HashMap::default(), build: 0, tick: 0, bound, stats: SegStats::default() }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The held entries' estimated bytes (segBytes).
    pub fn bytes(&self) -> u64 {
        self.entries.values().map(|e| e.bytes).sum()
    }

    /// kernel._feed_seg_build_begin: a board build begins.
    pub fn build_begin(&mut self) {
        self.build += 1;
    }

    /// kernel._feed_seg_forget(alive_sids): drop the entries of sessions outside the build's alive set.
    pub fn forget(&mut self, alive: &HashSet<&str>) {
        let before = self.entries.len();
        self.entries.retain(|k, _| alive.contains(k.as_str()));
        self.stats.seg_evict += (before - self.entries.len()) as u64;
    }

    fn touch(&mut self) -> u64 {
        self.tick += 1;
        self.tick
    }

    /// kernel._feed_seg_walk(fsid, head, turns, store): the maps, the closed turns' part served from the held entry
    /// where it stands (the same leaf, the same seams, its keys a prefix of the closed turns' keys), copied for this
    /// derivation; an entry that cannot serve is deleted (segReset: no closed turn or keys, another leaf, other seams;
    /// segPartial: the keys diverged inside it).
    pub fn walk(
        &mut self,
        fsid: &str,
        head: Option<Head<'a>>,
        turns: Option<&'a [Turn]>,
        store: Option<&'a Store>,
    ) -> FeedSegWalk<'a> {
        let turns = turns.unwrap_or_default();
        let n = turns.len();
        let n_closed = n.saturating_sub(1);
        let fp = if n > 0 { seams_fp(store) } else { Vec::new() };
        let keys: Vec<HoldKey<'a>> =
            if head.is_some() && n_closed > 0 { turns[..n_closed].iter().map(turn_hold_key).collect() } else { Vec::new() };
        let mut served: Option<(SegMaps<'a>, Num)> = None;
        let mut start = 0;
        if n > 0 && self.entries.contains_key(fsid) {
            let e = &self.entries[fsid];
            let hk = e.keys.len();
            if keys.is_empty() || Some(e.head) != head || e.fp != fp {
                self.entries.remove(fsid);
                self.stats.seg_reset += 1;
            } else if hk <= n_closed && keys[..hk] == e.keys[..] {
                start = hk;
                let (build, tick) = (self.build, self.touch());
                let e = self.entries.get_mut(fsid).expect("present");
                e.build = build;
                e.tick = tick;
                served = Some((e.maps.clone(), e.plain_t)); // _feed_seg_copy
            } else {
                self.entries.remove(fsid);
                self.stats.seg_partial += 1;
            }
        }
        let (mut maps, base_plain) = served.unwrap_or_else(|| (SegMaps::default(), Num::Int(0)));
        let mut closed = None;
        let mut done = fold_with(&mut maps, turns, start, n_closed, store, None);
        if done.is_ok() {
            if !keys.is_empty() && start < n_closed {
                closed = Some(maps.clone()); // `w.closed = maps; maps = _feed_seg_copy(maps)`
            }
            done = fold_with(&mut maps, turns, n_closed, n, store, None);
        }
        let (ok, raised) = match done {
            Ok(()) => (true, None),
            Err(r) => {
                closed = None;
                self.stats.seg_raised += 1;
                (false, Some(r))
            }
        };
        let hold = Hold { fsid: fsid.to_string(), head, fp, keys, closed };
        FeedSegWalk {
            maps,
            ok,
            raised,
            turns,
            n_closed,
            seg_turn: FeedSegTurns::new(turns, store),
            start,
            base_plain,
            hold: Some(Box::new(hold)),
        }
    }

    /// kernel._feed_seg_plain_t(w) over a held walk: the plain-prompt time, then (a walk over turns whose fold finished)
    /// the counters, and the closed turns' aggregate stored under the byte bound, shedding the least recently used
    /// entries this build has not used (segEvict), or not held at all (segOverBound, or segEvict when shedding would not
    /// make room), keeping then the entry the walk was served from.
    pub fn finish(&mut self, w: &mut FeedSegWalk<'a>) -> Num {
        let (closed_t, best) = plain_parts(w, None);
        if w.turns.is_empty() || !w.ok {
            return best;
        }
        self.stats.seg_walks += 1;
        self.stats.seg_held += w.start as u64;
        self.stats.seg_walk_turns += (w.turns.len() - w.start) as u64;
        let Some(h) = w.hold.as_mut() else { return best };
        let Some(closed) = h.closed.take() else { return best };
        let size = entry_bytes(h.keys.len(), &closed);
        let old = self.entries.remove(&h.fsid);
        let (build, bound) = (self.build, self.bound);
        let keep = if size > bound {
            self.stats.seg_over_bound += 1;
            old
        } else if size + self.entries.values().filter(|e| e.build == build).map(|e| e.bytes).sum::<u64>() > bound {
            self.stats.seg_evict += 1;
            old
        } else {
            let mut total = size + self.bytes();
            if total > bound {
                let mut lru: Vec<(u64, String)> =
                    self.entries.iter().filter(|(_, e)| e.build != build).map(|(k, e)| (e.tick, k.clone())).collect();
                lru.sort_unstable();
                for (_, k) in lru {
                    total -= self.entries.remove(&k).expect("listed").bytes;
                    self.stats.seg_evict += 1;
                    if total <= bound {
                        break;
                    }
                }
            }
            Some(HeldEntry {
                head: h.head.expect("a walk with keys has a head"),
                fp: std::mem::take(&mut h.fp),
                keys: std::mem::take(&mut h.keys),
                maps: closed,
                plain_t: closed_t,
                bytes: size,
                build,
                tick: 0,
            })
        };
        if let Some(mut k) = keep {
            k.tick = self.touch();
            self.entries.insert(h.fsid.clone(), k);
        }
        best
    }
}

/// kernel._feed_seg_plain_t(w): `max(max(base, plain(closed turns from start)), plain(the open turn))`, the larger of
/// the closed turns' plain-prompt time and the open turn's, the first of equal values kept (`base` is 0 and `start` 0
/// unless the walk began from a closed part).
pub fn feed_seg_plain_t(w: &FeedSegWalk<'_>) -> Num {
    plain_t_with(w, None)
}

fn plain_t_with(w: &FeedSegWalk<'_>, plant: Option<Plant>) -> Num {
    plain_parts(w, plant).1
}

/// (the closed turns' plain-prompt time, what the aggregate stores; the larger of it and the open turn's).
fn plain_parts(w: &FeedSegWalk<'_>, plant: Option<Plant>) -> (Num, Num) {
    let mut closed_t = w.base_plain;
    if w.start < w.n_closed {
        closed_t = max_with(closed_t, last_plain_with(&w.turns[w.start..w.n_closed], plant), plant);
    }
    (closed_t, max_with(closed_t, last_plain_with(&w.turns[w.n_closed..], plant), plant))
}

// ───────────────────────────── answers as tools/rust-spike/dump_layer3.py writes Python's ─────────────────────────────

fn s(x: Option<&str>) -> Out {
    x.map_or(Out::Null, |x| Out::Str(x.to_string()))
}

fn int(n: usize) -> Out {
    Out::Int(i64::try_from(n).expect("a count fits in i64"))
}

fn obj(members: Vec<(&str, Out)>) -> Out {
    Out::Object(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

/// `[ti, [atomIdx, ...]]` for a get answer, each atom by its index in the turn's `atoms`, or null.
pub fn seg_turn_json(got: Option<&SegTurn<'_>>) -> Out {
    let Some(st) = got else { return Out::Null };
    let index = layer2::atom_index(st.turn);
    let idx = st.atoms.iter().map(|a| int(*index.get(&(*a as *const Atom)).expect("a segment atom is its turn's")));
    Out::Array(vec![int(st.ti), Out::Array(idx.collect())])
}

/// One turn's segment rows: per segment `[key, work, reply, jump, trig, lastUuid, lastSubstantive, t]`, or the raise.
pub fn turn_rows_json(turn: &Turn, store: Option<&Store>, plant: Option<Plant>) -> Out {
    match layer2::segs_seam(turn, store) {
        Err(r) => r.to_json(),
        Ok(segs) => Out::Array(
            segs.iter()
                .map(|seg| {
                    let atoms = seg.atoms.as_slice();
                    let (work, reply) = seg_anchors(atoms);
                    let (lu, lsub) = last_text_with(atoms, plant);
                    Out::Array(vec![
                        Out::Str(layer1::kernel_seg_key(&seg.id).into_owned()),
                        s(work),
                        s(reply),
                        s(seg_jump(atoms)),
                        s(prompt_anchor_uuid(seg)),
                        s(lu),
                        Out::Bool(lsub),
                        seg.t.to_json(),
                    ])
                })
                .collect(),
        ),
    }
}

/// One session's layer-3 answer, as the per-card build reads it (dump_layer3.py's `session_answer`): `working` (the
/// key's _session_working, False without a tree), `suspended` (its last read, null without turns), `progress`
/// (_open_turn_progress, asked whether or not the session works), the walk's `walkOk` and maps in their order, the
/// sorted citable set, `plainT` (0 without a tree), every key's `seg_turn` answer plus one absent key's, the faults
/// those gets counted, and each turn's segment rows.
pub fn session_json(tree: Option<&Tree>, store: Option<&Store>, downtime: &[(Num, Num)], plant: Option<Plant>) -> Out {
    let turns: Option<&[Turn]> = tree.map(|t| t.turns.as_deref().unwrap_or_default());
    let all = turns.unwrap_or_default();
    let working = turns.is_some_and(|t| session_working(t, downtime));
    let suspended = if all.is_empty() { Out::Null } else { Out::Bool(suspended_after(downtime, last_activity(all))) };
    let progress = match turns.and_then(open_turn_progress) {
        None => Out::Null,
        Some(p) => obj(vec![("since", p.since.to_json()), ("toolUses", int(p.tool_uses))]),
    };
    let mut w = walk_with(turns, store, plant);
    let entries: Vec<(String, SegEntry<'_>)> = w.maps.segs.iter().map(|(k, e)| (k.to_string(), *e)).collect();
    let pairs = |f: &dyn Fn(&SegEntry<'_>) -> Out| {
        Out::Array(entries.iter().map(|(k, e)| Out::Array(vec![Out::Str(k.clone()), f(e)])).collect())
    };
    let seg_uuid = pairs(&|e| s(e.uuid));
    let seg_trig = pairs(&|e| s(e.trig));
    let seg_best = pairs(&|e| Out::Array(vec![s(e.best.uuid), Out::Bool(e.best.substantive), e.best.t.to_json()]));
    let pos = pairs(&|e| Out::Array(vec![int(e.pos.0), int(e.pos.1)]));
    let mut cite: Vec<&str> = w.maps.cite.iter().copied().collect();
    cite.sort_unstable();
    let plain_t = if tree.is_some() { plain_t_with(&w, plant).to_json() } else { Out::Int(0) };
    let mut queries: Vec<Out> = Vec::new();
    for k in entries.iter().map(|(k, _)| k.as_str()).chain([ABSENT_KEY]) {
        let has = w.seg_turn_contains(k);
        let got = w.seg_turn_get(k);
        queries.push(Out::Array(vec![Out::Str(k.to_string()), Out::Bool(has), seg_turn_json(got.as_ref())]));
    }
    let rows = Out::Array(all.iter().map(|t| turn_rows_json(t, store, plant)).collect());
    obj(vec![
        ("working", Out::Bool(working)),
        ("suspended", suspended),
        ("progress", progress),
        ("walkOk", Out::Bool(w.ok)),
        ("segUuid", seg_uuid),
        ("segTrig", seg_trig),
        ("segBest", seg_best),
        ("pos", pos),
        ("cite", Out::Array(cite.into_iter().map(|u| Out::Str(u.to_string())).collect())),
        ("plainT", plain_t),
        ("segTurn", Out::Array(queries)),
        ("faults", int(w.seg_turn.faults)),
        ("turns", rows),
    ])
}

/// A direct `_FeedSegTurns(pos, turns, store)` case: each query's `[key, key in seg_turn, get(key)]`, then the faults,
/// as `{"gets": [...], "faults": n}`.
pub fn seg_turns_json(turns: &[Turn], store: Option<&Store>, pos: &PyDict<(usize, usize)>, keys: &[String]) -> Out {
    let mut st = FeedSegTurns::new(turns, store);
    let gets = keys
        .iter()
        .map(|k| {
            let p = pos.get(k).copied();
            let got = st.get_at(k, p);
            Out::Array(vec![Out::Str(k.clone()), Out::Bool(p.is_some()), seg_turn_json(got.as_ref())])
        })
        .collect();
    obj(vec![("gets", Out::Array(gets)), ("faults", int(st.faults))])
}

/// One case of an edge set or a fuzz file (tools/rust-spike/dump_layer3.py `--edge-cases`, `--fuzz`), loaded through
/// the port's loader: a session (`tree` or null, `store` or null, `downtime`), a direct _FeedSegTurns over hand-made
/// positions (`tree`, `store`, `pos`, `keys`), or a direct _suspended_after read (`downtime`, `t`).
#[derive(Debug)]
pub enum EdgeCase {
    Session { tree: Option<Tree>, store: Option<Store>, downtime: Vec<(Num, Num)> },
    SegTurns { tree: Tree, store: Option<Store>, pos: PyDict<(usize, usize)>, keys: Vec<String> },
    Suspended { downtime: Vec<(Num, Num)>, t: Option<Num> },
}

fn tree_value(v: &serde_json::Value) -> Result<Option<Tree>, String> {
    if v.is_null() {
        return Ok(None);
    }
    let tree: Tree = serde_json::from_value(v.clone()).map_err(|e| format!("tree: {e}"))?;
    crate::tree::validate(&tree).map_err(|e| format!("tree: {e}"))?;
    Ok(Some(tree))
}

fn store_value(v: &serde_json::Value) -> Result<Option<Store>, String> {
    match v {
        serde_json::Value::Null => Ok(None),
        v => serde_json::from_value::<Store>(v.clone()).map(Some).map_err(|e| format!("store: {e}")),
    }
}

impl EdgeCase {
    /// Load one case; `Err` names the part the loader refused.
    pub fn load(case: &serde_json::Value) -> Result<EdgeCase, String> {
        let downtime = || -> Result<Vec<(Num, Num)>, String> {
            serde_json::from_value(case["downtime"].clone()).map_err(|e| format!("downtime: {e}"))
        };
        match case["kind"].as_str() {
            Some("session") => Ok(EdgeCase::Session {
                tree: tree_value(&case["tree"])?,
                store: store_value(&case["store"])?,
                downtime: downtime()?,
            }),
            Some("segturns") => {
                let tree = tree_value(&case["tree"])?.ok_or("segturns: no tree")?;
                let rows: Vec<(String, (usize, usize))> =
                    serde_json::from_value(case["pos"].clone()).map_err(|e| format!("pos: {e}"))?;
                let mut pos = PyDict::default();
                for (k, p) in rows {
                    pos.insert(k, p);
                }
                let keys = serde_json::from_value(case["keys"].clone()).map_err(|e| format!("keys: {e}"))?;
                Ok(EdgeCase::SegTurns { tree, store: store_value(&case["store"])?, pos, keys })
            }
            Some("suspended") => {
                let t = serde_json::from_value(case["t"].clone()).map_err(|e| format!("t: {e}"))?;
                Ok(EdgeCase::Suspended { downtime: downtime()?, t })
            }
            k => Err(format!("unknown kind {k:?}")),
        }
    }

    /// The port's answer, in the shape dump_layer3.py writes Python's `expect`.
    pub fn answer(&self, plant: Option<Plant>) -> Out {
        match self {
            EdgeCase::Session { tree, store, downtime } => session_json(tree.as_ref(), store.as_ref(), downtime, plant),
            EdgeCase::SegTurns { tree, store, pos, keys } => {
                seg_turns_json(tree.turns.as_deref().unwrap_or_default(), store.as_ref(), pos, keys)
            }
            EdgeCase::Suspended { downtime, t } => Out::Bool(suspended_after(downtime, *t)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SID: &str = "s-1";

    fn turn(json: &str) -> Turn {
        let t: Turn = serde_json::from_str(json).unwrap();
        crate::tree::validate_turn(&t).unwrap();
        t
    }

    fn atom(json: &str) -> Atom {
        serde_json::from_str(json).unwrap()
    }

    fn user(uuid: &str, t: &str, text: &str) -> String {
        format!(r#"{{"type": "user", "uuid": "{uuid}", "t": {t}, "session_id": "{SID}", "author": "human", "message": {{"content": [{{"type": "text", "text": "{text}"}}]}}}}"#)
    }

    fn asst(uuid: &str, t: &str, text: &str) -> String {
        format!(r#"{{"type": "assistant", "uuid": "{uuid}", "t": {t}, "session_id": "{SID}", "message": {{"content": [{{"type": "text", "text": "{text}"}}], "stop_reason": "end_turn"}}}}"#)
    }

    fn tool(uuid: &str, t: &str) -> String {
        format!(r#"{{"type": "assistant", "uuid": "{uuid}", "t": {t}, "session_id": "{SID}", "message": {{"content": [{{"type": "tool_use", "id": "x", "name": "Bash"}}], "stop_reason": "tool_use"}}}}"#)
    }

    fn turn_of(atoms: &[String], t: &str, end: &str, ended: bool) -> Turn {
        turn(&format!(r#"{{"atoms": [{}], "t": {t}, "end": {end}, "ended": {ended}, "trigger": {{"uuid": "u1"}}}}"#,
                      atoms.join(", ")))
    }

    #[test]
    fn max_keeps_the_first_of_equal_values() {
        assert_eq!(py_max(Num::Int(5), Num::Float(5.0)), Num::Int(5));
        assert_eq!(py_max(Num::Float(5.0), Num::Int(5)), Num::Float(5.0));
        assert_eq!(py_max(Num::Int(0), Num::Int(-3)), Num::Int(0));
        assert_eq!(max_with(Num::Int(5), Num::Float(5.0), Some(Plant::MaxKeepsLast)), Num::Float(5.0));
    }

    #[test]
    fn a_dict_overwrite_keeps_the_first_place() {
        let mut d = PyDict::default();
        for (k, v) in [("a", 1), ("b", 2), ("a", 3), ("c", 4)] {
            d.insert(k.to_string(), v);
        }
        assert_eq!(d.iter().collect::<Vec<_>>(), [("a", &3), ("b", &2), ("c", &4)]);
        d.insert_moving("a".to_string(), 5);
        assert_eq!(d.iter().collect::<Vec<_>>(), [("b", &2), ("c", &4), ("a", &5)]);
        assert_eq!((d.get("c"), d.get("z"), d.len()), (Some(&4), None, 3));
    }

    #[test]
    fn working_skips_trailing_commands_and_reads_the_downtime() {
        let open = [user("u1", "100", "go"), tool("a1", "105")];
        let t = vec![turn_of(&open, "100", "110", false)];
        assert!(session_working(&t, &[]));
        assert!(!session_working(&t, &[(Num::Float(110.5), Num::Float(200.0))]), "asleep after the last activity");
        assert!(session_working(&t, &[(Num::Int(110), Num::Int(200))]), "a suspension at the end is not after it");
        let idle = r#"{"type": "idle", "uuid": null, "t": 106, "end": 110}"#;
        let cmd = r#"{"type": "system", "uuid": "c1", "t": 111, "command": "/model"}"#;
        let tail = vec![turn_of(&[open[0].clone(), idle.to_string(), cmd.to_string()], "100", "111", false)];
        assert!(!session_working(&tail, &[]), "the command is skipped and the idle span ends the turn");
        let ended = vec![turn_of(&open, "100", "110", true)];
        assert!(!session_working(&ended, &[]) && !session_working(&[], &[]));
        assert!(suspended_after(&[(Num::Float(0.5), Num::Int(1))], Some(Num::Float(-0.0))), "t or 0");
    }

    #[test]
    fn progress_counts_raw_tool_uses_of_the_open_turn() {
        let s = r#"{"type": "assistant", "uuid": "a9", "t": 107, "message": {"content": "tool_use"}}"#;
        let user_tool = r#"{"type": "user", "uuid": "r1", "t": 108, "message": {"content": [{"type": "tool_use"}]}}"#;
        let atoms = [user("u1", "100", "go"), tool("a1", "105"), tool("a2", "106"), s.to_string(), user_tool.into()];
        let p = open_turn_progress(&[turn_of(&atoms, "100.0", "110", false)]).unwrap();
        assert_eq!(p, Progress { since: Num::Float(100.0), tool_uses: 2 });
        assert_eq!(open_turn_progress(&[turn_of(&atoms, "100", "110", true)]), None);
        assert_eq!(open_turn_progress(&[]), None);
    }

    #[test]
    fn the_plain_prompt_time_skips_echoes_and_targeted_replies_and_keeps_the_first_tie() {
        let a = |t: &str, text: &str| turn_of(&[user("u1", t, text)], t, t, true);
        let turns = vec![a("5", "hi"), a("5.0", "again"), a("9", "x <!-- romp-goal-id: g1 --> y")];
        assert_eq!(last_plain_user_turn_t(&turns), Num::Int(5));
        assert_eq!(last_plain_with(&turns, Some(Plant::MaxKeepsLast)), Num::Float(5.0));
        let mut echo = a("7", "e");
        echo.echo_turn = Some(serde_json::Value::Bool(true));
        assert_eq!(last_plain_user_turn_t(&[echo]), Num::Int(0));
        // a zero atom time falls to the turn's
        let zero = turn(&format!(r#"{{"atoms": [{}], "t": 12.5, "end": 13, "trigger": {{"uuid": "u1"}}}}"#,
                                 user("u1", "0", "z")));
        assert_eq!(last_plain_user_turn_t(&[zero]), Num::Float(12.5));
    }

    #[test]
    fn no_trigger_uuid_matches_the_first_atom_without_one() {
        let idle = r#"{"type": "idle", "uuid": null, "t": 99, "end": 100}"#;
        let t = turn(&format!(r#"{{"atoms": [{}, {idle}], "t": 98, "end": 101, "trigger": null}}"#,
                              user("u1", "98", "hi")));
        assert_eq!(last_plain_user_turn_t(&[t]), Num::Int(0), "the idle span is the anchor, not the prompt");
    }

    #[test]
    fn anchors_prefer_the_reply_then_the_settle_and_skip_api_errors() {
        let settle = atom(r#"{"type": "assistant", "uuid": "s1", "t": 1, "message": {"content": [{"type": "text", "text": "No response requested."}]}}"#);
        let reply = atom(&asst("r1", "2", "done"));
        let err = atom(r#"{"type": "assistant", "uuid": "e1", "t": 3, "isApiError": true, "message": {"content": [{"type": "text", "text": "API Error"}]}}"#);
        let empty = atom(&asst("", "4", "late"));
        assert_eq!(seg_anchors(&[&settle, &reply, &err]), (Some("s1"), Some("r1")));
        assert_eq!(seg_anchors(&[&settle, &err]), (Some("s1"), Some("s1")));
        // the later reply's uuid "" replaces "r1", and `"" or None` is None, `"" or "s1"` is "s1"
        assert_eq!(seg_anchors(&[&reply, &empty]), (Some("r1"), None));
        assert_eq!(seg_anchors(&[&settle, &empty]), (Some("s1"), Some("s1")));
        let t = atom(&tool("t1", "5"));
        assert_eq!(seg_jump(&[&empty, &t]), Some("t1"), "an empty reply falls to the first landable atom");
        assert_eq!(seg_jump(&[&err]), None);
    }

    #[test]
    fn the_fold_overwrites_in_place_and_stops_at_a_raise() {
        let t1 = turn_of(&[user("u1", "100", "continue"), asst("a1", "101", "one")], "100", "102", true);
        let t2 = turn_of(&[user("u2", "200", "other"), asst("a2", "201", "two")], "200", "202", true);
        let t3 = turn_of(&[user("u3", "300", "continue"), asst("a3", "301", "three")], "300", "302", true);
        let broken = turn(r#"{"atoms": [{"type": "user", "uuid": "u4", "t": 400, "author": "human"}], "t": 400, "end": 401}"#);
        let turns = vec![t1, t2, t3, broken];
        let mut w = feed_seg_walk(Some(&turns), None);
        assert!(!w.ok && w.raised == Some(Raise { exc: "KeyError", arg: "'session_id'".into() }));
        let keys: Vec<(&str, Option<&str>, (usize, usize))> =
            w.maps.segs.iter().map(|(k, e)| (k, e.uuid, e.pos)).collect();
        let cont = format!("{SID}:{}", crate::sha1::hex8(b"continue"));
        let other = format!("{SID}:{}", crate::sha1::hex8(b"other"));
        assert_eq!(keys, [(cont.as_str(), Some("a3"), (2, 0)), (other.as_str(), Some("a2"), (1, 0))]);
        assert_eq!(feed_seg_plain_t(&w), Num::Int(400), "the plain-prompt read does not segment, so it reads the raiser");
        let got = w.seg_turn_get(&cont).unwrap();
        assert_eq!((got.ti, got.atoms.len()), (2, 2));
        assert!(w.seg_turn_get(ABSENT_KEY).is_none() && !w.seg_turn_contains(ABSENT_KEY));
        let skipped = walk_with(Some(&turns), None, Some(Plant::RaiseSkipsTurn));
        assert!(skipped.ok);
        let moved = walk_with(Some(&turns), None, Some(Plant::OverwriteMoves));
        assert_eq!(moved.maps.segs.iter().map(|(k, _)| k).collect::<Vec<_>>(), [other.as_str(), cont.as_str()]);
    }

    #[test]
    fn a_get_at_a_stale_position_falls_back_or_faults() {
        let t = turn_of(&[user("u1", "100", "a"), user("u2", "110", "b"), user("u3", "120", "a")], "100", "130", true);
        let turns = vec![t];
        let key_a = format!("{SID}:{}", crate::sha1::hex8(b"a"));
        let mut st = FeedSegTurns::new(&turns, None);
        let at = |g: Option<SegTurn<'_>>| g.map(|g| g.atoms[0].uuid().unwrap().to_string());
        assert_eq!(at(st.get_at(&key_a, Some((0, 1)))), Some("u3".into()), "another key at 1: the last with the key");
        assert_eq!(at(st.get_at(&key_a, Some((0, 9)))), Some("u3".into()), "an ordinal past the end");
        assert_eq!(at(st.get_at("s-1:nope", Some((0, 0)))), None);
        assert_eq!(st.faults, 0);
        assert_eq!(at(st.get_at(&key_a, Some((5, 0)))), None, "a turn index out of range faults");
        assert_eq!(st.faults, 1);
    }

    #[test]
    fn the_prompt_anchor_steps_past_an_attachment() {
        let att = r#"{"type": "attachment", "uuid": "at1", "t": 100, "session_id": "s-1"}"#;
        let t = turn(&format!(r#"{{"atoms": [{att}, {}], "t": 100, "end": 120, "trigger": {{"uuid": "at1"}}}}"#,
                              asst("a1", "101", "x")));
        let segs = layer2::segs_seam(&t, None).unwrap();
        assert_eq!(prompt_anchor_uuid(&segs[0]), Some("at1"), "no user atom: the raw trigger");
        let t2 = turn(&format!(
            r#"{{"atoms": [{att}, {{"type": "user", "uuid": "", "t": 101}}, {{"type": "user", "uuid": "p1", "t": 102}}], "t": 100, "end": 120, "trigger": {{"uuid": "at1"}}}}"#
        ));
        let segs = layer2::segs_seam(&t2, None).unwrap();
        assert_eq!(prompt_anchor_uuid(&segs[0]), Some("p1"));
    }

    #[test]
    fn the_substantive_floor_counts_code_points() {
        let a = atom(&asst("a1", "1", &"\u{e9}".repeat(79)));
        assert_eq!(seg_last_text(&[&a]), (Some("a1"), false));
        assert_eq!(last_text_with(&[&a], Some(Plant::FloorInBytes)), (Some("a1"), true));
    }

    fn turn_id(id: &str, n: u32, ended: bool) -> Turn {
        let t = 100 * n;
        let atoms = [user(&format!("u{n}"), &t.to_string(), &format!("ask number {n}")),
                     asst(&format!("a{n}"), &(t + 1).to_string(), &format!("the long enough reply to ask {n}, more than eighty code points of prose so that it can be cited"))];
        turn(&format!(r#"{{"id": "{id}", "atoms": [{}], "t": {t}, "end": {}, "ended": {ended}, "trigger": {{"uuid": "u{n}"}}}}"#,
                      atoms.join(", "), t + 2))
    }

    fn entries_of<'a>(m: &SegMaps<'a>) -> (Vec<(String, SegEntry<'a>)>, Vec<&'a str>) {
        let mut cite: Vec<&str> = m.cite.iter().copied().collect();
        cite.sort_unstable();
        (m.segs.iter().map(|(k, e)| (k.to_string(), *e)).collect(), cite)
    }

    #[test]
    fn the_held_walk_serves_the_closed_turns_and_gives_the_full_walks_maps() {
        let turns: Vec<Turn> = (1..=3).map(|n| turn_id(&format!("t{n}"), n, true)).collect();
        let full = feed_seg_walk(Some(&turns), None);
        let mut held = SegHeld::new(1 << 30);
        for (build, (start, held_turns, walked)) in [(0usize, 0u64, 3u64), (2, 2, 4)].into_iter().enumerate() {
            held.build_begin();
            let mut w = held.walk("s", Some(("p", 0)), Some(&turns), None);
            assert_eq!(w.start, start, "build {build}");
            assert_eq!(entries_of(&w.maps), entries_of(&full.maps), "build {build}");
            assert_eq!(held.finish(&mut w), feed_seg_plain_t(&full));
            assert_eq!((held.stats.seg_held, held.stats.seg_walk_turns), (held_turns, walked));
        }
        assert_eq!((held.len(), held.stats.seg_walks, held.stats.seg_reset, held.stats.seg_partial), (1, 2, 0, 0));
        // the cheap bound's closed part is the held entry's maps, so its walk agrees too
        let w = feed_seg_walk_from(Some(&turns), None, closed_part(Some(&turns), None));
        assert_eq!((w.start, entries_of(&w.maps)), (2, entries_of(&full.maps)));
    }

    #[test]
    fn another_leaf_resets_and_a_diverged_or_longer_hold_is_partial() {
        let turns: Vec<Turn> = (1..=3).map(|n| turn_id(&format!("t{n}"), n, true)).collect();
        let edited: Vec<Turn> = [turn_id("t1", 1, false), turn_id("t2", 2, true), turn_id("t3", 3, true)].into();
        fn fill<'a>(held: &mut SegHeld<'a>, turns: &'a [Turn]) {
            let mut w = held.walk("s", Some(("p", 0)), Some(turns), None);
            held.finish(&mut w);
        }
        let mut held = SegHeld::new(1 << 30);
        fill(&mut held, &turns);
        let mut w = held.walk("s", Some(("q", 0)), Some(&turns), None); // another leaf
        assert_eq!((w.start, held.stats.seg_reset), (0, 1));
        held.finish(&mut w); // stores under the new leaf
        let mut w = held.walk("s", None, Some(&turns), None); // no leaf head: no keys, nothing held
        assert_eq!((w.start, held.stats.seg_reset, held.len()), (0, 2, 0));
        held.finish(&mut w);
        assert_eq!(held.len(), 0, "a walk without keys stores nothing");
        fill(&mut held, &turns);
        let w = held.walk("s", Some(("p", 0)), Some(&edited), None); // the first closed turn's key moved
        assert_eq!((w.start, held.stats.seg_partial), (0, 1));
        fill(&mut held, &turns);
        let w = held.walk("s", Some(("p", 0)), Some(&turns[..2]), None); // a rewind: fewer closed turns than held keys
        assert_eq!((w.start, held.stats.seg_partial), (0, 2));
    }

    #[test]
    fn the_byte_bound_refuses_an_aggregate_over_it_and_forget_drops_the_departed() {
        let turns: Vec<Turn> = (1..=3).map(|n| turn_id(&format!("t{n}"), n, true)).collect();
        let mut held = SegHeld::new(100);
        held.build_begin();
        let mut w = held.walk("s", Some(("p", 0)), Some(&turns), None);
        held.finish(&mut w);
        assert_eq!((held.len(), held.stats.seg_over_bound), (0, 1));
        let mut held = SegHeld::new(1 << 30);
        for sid in ["a", "b"] {
            let mut w = held.walk(sid, Some(("p", 0)), Some(&turns), None);
            held.finish(&mut w);
        }
        held.forget(&["a"].into_iter().collect());
        assert_eq!((held.len(), held.stats.seg_evict), (1, 1));
        assert_eq!(turn_hold_key(&turns[0]), turn_hold_key(&turn_id("t1", 1, true)));
        assert_ne!(turn_hold_key(&turns[0]), turn_hold_key(&turn_id("t1", 1, false)));
    }
}
