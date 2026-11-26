//! Layer 7 of rust/notes/s2-design.md section 2, "Loop, fold and cross-session pass": what kernel.build_feed does
//! around the per-session entries layer 6 makes, so the whole feed frame comes out of the port. Line for line:
//! - the session loop minus the memo and its key (a raising derivation lists its sid in `absentSids`, since the spike
//!   has no previous entry to serve; a hidden session's null folds nothing), with _feed_fold_session's fresh path;
//! - the fold: _feed_fold_tints, _feed_fold_stamp and _feed_fold_card (`_ageT` popped from the card and every tree row,
//!   `t := now` on a placeholder whose `_ageT` is null, `trgb` from cm.age_rgb over cm.ramp and cm.stops_for in
//!   colormap.py) and _feed_fold_bell (`notify` from _notify_card_effective, `notifyRaw` from _push_explicit_bell, the
//!   session's own flag read as _session_flag_raw reads it);
//! - the serving-fold join (a candidate whose tracker row rendered this build folds its rows under that row, else it
//!   keeps its own card at the end), the handoff rows' `whoWorking` stamp from the sids behind `working`;
//! - the two producers that add cards after the loop: the parked handoffs (_parked_handoffs over _parked_fold_step's
//!   fold of the postal log, timeline/messages.jsonl, and the maildir `new/` test) and the notice cards (_notice_cards
//!   over _notice_rows' filter of each notice file and _notice_projection, with _held_mail_row, _notice_item_id and
//!   _notice_action_kind); then the bell pass over every card the fold did not stamp;
//! - the frame, assembled from the snapshot's passthrough keys (`frameKeys`, `frame`) around what the port builds:
//!   `asks`, `now`, `working`, `awaiting`, `bgServices`, and `servingFoldedIds` and `absentSids` right after `now`
//!   when non-empty, as the frame literal spreads them.
//!
//! What the build reads besides layer 6's inputs: the board's bells store (`notifyCards`, `_notify_cards()`), each
//! session's flags (the `notify` flag), the world's colormap (`_colormap()`), and three recorded file reads that the S2
//! dumper never writes (neither world has any): the postal log's records (`board.postalLog`), the maildir files that
//! stand (`board.mailNew`) and the notice files' lines (`board.notices`). The held-mail backfill that runs before
//! _notice_cards reads the notice files is a writer (it posts and retires held-mail notice rows from the hold
//! directory); its effect is in the rows, and dump_layer7.py stubs it.
//!
//! What a naive port would get wrong, each pinned by the edge cases (tools/rust-spike/dump_layer7.py --edge-cases) with a
//! mutant the edge set catches:
//! - The tint is `ramp(1 - f)` with `f` the log position of the age, each channel `round()`ed half to even (59.5 reads
//!   60, 50.5 reads 50), and the colormap's name lowercased with Python's `lower()`; an unknown or null name is
//!   colormap.DEFAULT, "aurora" (the module docstring's "default hawaii" is stale). The age is `now - _ageT` in Python
//!   arithmetic (int minus int exactly, then `float()`: past 2**53 an age of 1000 s reads 1024 s through floats; an int
//!   clock minus a float time is float arithmetic, the time never truncated), and a null `_ageT` is the clock itself
//!   (age 0, the brightest stop). ramp mixes `a + (b - a) * fr`, which rounds differently from `a * (1 - fr) + b * fr`
//!   on some ages. Not pinned, because no feed shows a difference:
//!   age_rgb clamps the age to [120, 345600] s and ramp clamps `v` to [0, 1], and the two clamps overlap, so taking out
//!   either one alone changes no tint of an age (ramp's own clamp shows on a direct call outside [0, 1], which the
//!   fixture's tint table makes).
//! - The fold pops `_ageT` (so the key leaves its place), writes `t := now` only on a card whose `_ageT` is null, with
//!   the clock's own int/float tag, appends `trgb`, then `notify` and `notifyRaw` last. (In a fresh build a placeholder
//!   whose `_ageT` is null already carries the clock as its `t`, so the restamp itself changes nothing here; it matters
//!   for an entry the memo serves from an earlier build. Its tag and its "only" are pinned.)
//! - A bell override is read with `is None`: a stored `false` or `0` on a card is an explicit mute (`notify` null,
//!   `notifyRaw` false) and never falls through to the session flag or the master. `notify` is `true` or null, never
//!   false; `notifyRaw` is the card's override, else the session's flag, else null, and the master is never read there.
//!   A truthy override that is not a bool (1, "yes") arms; a card whose itemId is "" reads its override in `notify` but
//!   not in `notifyRaw` (Python's `if item_id:`).
//! - The serving join keys the tracker rows by id with the LAST handoff row of that id winning, appends the candidate's
//!   first row id to the tracker row's children and every candidate row to the tracker card's tree; a candidate with
//!   no rendered tracker row lands as its own card after every session card and takes its bells from the bell pass.
//!   `servingFoldedIds` lists the folded candidates' item ids. The tracker rows are indexed once, before the join, so
//!   neither a joined row nor a kept candidate's handoff row is a tracker for a later candidate.
//! - `whoWorking` on a handoff row is its recipient's (`whoSid` among the sids whose fold added a `working` name), not
//!   the card owner's, and only handoff rows take the stamp: a working session whose name is "" adds no name, so a
//!   handoff to it stays dark while its own ask rows keep the bit layer 6 gave them.
//! - The fold runs outside build_feed's per-session guard: a session whose entry raised is absent and the build goes
//!   on, but a null card in an entry's `asks` (the awaiting placeholder of a session that is not live, which
//!   _awaiting_card answers as None and the body appends) raises AttributeError out of the fold and fails the whole
//!   build; the port answers that raise.
//! - `bgServices` is a dict keyed by the session's name: a later session of the same name replaces the value in the
//!   first one's place.
//! - Parked handoffs: the fold keeps a re-sent message id in its first place, a recall or bounce removes it (a later
//!   send re-adds it at the end); a candidate counts only while its recipient is not alive and its maildir file stands;
//!   names fall back to the sid's first 8 code points; a missing time is `int(now)`; the list is sorted by time,
//!   stably. The alive set the parked and notice cards read is every alive session, the absent ones included.
//! - Notices: the newest revision per key wins on `>=` (a later post of an equal rev replaces); an expire or acted row
//!   applies to its exact (key, rev); an acted card ships no actions and `acted: true`; an expiry applies when
//!   `now >= int(expiresAt)`; the cap of 50 counts only cards that are not held mail; cards sort by (t, key) within a
//!   file and by (t, itemId) across files; an action's kind is its stripped `kind`, else its route read as a kind.
//!   (Not pinned: _notice_rows keeps a line only when its op and key are truthy, and the op half changes nothing, since
//!   the projection reads only post, expire and acted rows; its key half is pinned.)
//!
//! Refused at load (`validate`), each where Python would raise, or read a shape the port does not mirror: an alive row
//! whose name is not a string, a world without a colormap key, a bells store that is not a dict, a node whose serving
//! `goalId` is a list or a dict (the join hashes it), postal log rows whose id, recipient or sender is not a string on a
//! parked send (or not hashable on a recall), whose truthy time is a string (Python's `int(str)` is not ported) or
//! truncates past i64, or whose truthy body is neither a string nor a list (the fold slices it); a parked send whose id
//! or recipient fails kernel._safe_id's rule (empty, over 128 characters, or not `[A-Za-z0-9][A-Za-z0-9._-]*`, which
//! also rules out `/`, `\`, NUL and a leading dot): Python asks `Path(base/"mail"/to/"new"/mid).exists()`, a path
//! lookup that drops `.` components and trailing slashes, while the port looks the exact pair up in `mailNew`; a clock
//! whose `int()` is past i64 while the postal log holds a parked send (a parked send with no time reads `int(now)`,
//! which Python keeps exact); notice rows (op post, expire or acted) whose key is not a string, whose truthy rev, time
//! or expiry is not a number or a bool (or truncates past i64), whose truthy actions are not a list of dicts, or whose
//! action carries a truthy non-string kind or route or a truthy non-dict body; a notice file sid that is empty or holds
//! `/`; frame keys without `now`, or naming a passthrough key the frame lacks.
//!
//! What the loader cannot see: whether two different safe names reach one file on the filesystem Python runs on (APFS,
//! the macOS default, ignores letter case: `M1` finds `m1`). `mailNew` is a record of which maildir files stand, and
//! the port reads it as exact pairs; dump_layer7.py refuses to answer a world where a parked candidate's `exists()`
//! disagrees with its `mailNew` membership on the filesystem it runs on, as it refuses notice files that collide.

use std::collections::{HashMap, HashSet};

use romp_json::Value as Out;
use serde_json::{Map, Value as J};

use crate::layer2::Raise;
use crate::layer4::color_json;
use crate::layer6::Build;
use crate::pystr;
use crate::record::{py_truthy, Num, ToJson};
use crate::snapshot::Snapshot;
use crate::tree::Tree;

/// colormap.FADE_LO and FADE_HI: the brightest and darkest ages, in seconds (2 minutes, 96 hours).
pub const FADE_LO: f64 = 120.0;
pub const FADE_HI: f64 = 345600.0;
/// colormap.DEFAULT.
pub const DEFAULT_COLORMAP: &str = "aurora";
/// colormap.COLORMAPS, dark to light, in the module's order.
pub const COLORMAPS: [(&str, &[[i64; 3]]); 7] = [
    ("aurora", &[[84, 178, 4], [0, 180, 115], [35, 175, 156], [66, 169, 176], [25, 168, 201], [14, 164, 227],
                 [74, 155, 241], [113, 145, 244], [144, 136, 240]]),
    ("hawaii", &[[140, 2, 115], [146, 46, 85], [151, 78, 62], [155, 111, 40], [156, 150, 28], [137, 189, 74],
                 [107, 212, 142], [103, 233, 213], [179, 242, 253]]),
    ("viridis", &[[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137],
                  [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]]),
    ("magma", &[[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100],
                [251, 135, 97], [254, 194, 135], [252, 253, 191]]),
    ("inferno", &[[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21],
                  [250, 193, 39], [252, 255, 164]]),
    ("plasma", &[[13, 8, 135], [75, 3, 161], [125, 3, 168], [168, 34, 150], [203, 70, 121], [229, 107, 93],
                 [248, 148, 65], [253, 195, 40], [240, 249, 33]]),
    ("cividis", &[[0, 34, 78], [33, 59, 110], [76, 85, 108], [108, 110, 114], [142, 137, 120], [177, 165, 112],
                  [217, 197, 92], [254, 232, 56]]),
];
/// kernel.NOTIFY_ALL_KEY: the master bell in the bells store.
pub const NOTIFY_ALL_KEY: &str = "*";
/// kernel.NOTICE_OWNERLESS_SID and NOTICE_OWNERLESS_NAME: the owner-less notice cards' file and their run's name.
pub const NOTICE_OWNERLESS_SID: &str = "notes";
pub const NOTICE_OWNERLESS_NAME: &str = "Notes";
/// kernel.NOTICE_LIVE_KEYS_MAX: live notice keys per session file, held-mail cards aside.
pub const NOTICE_LIVE_KEYS_MAX: usize = 50;
/// kernel.HELD_MAIL_PRODUCER.
pub const HELD_MAIL_PRODUCER: &str = "postal";
/// The frame keys the port builds; every other key in `frameKeys` is passed through from `frame`.
pub const BUILT_KEYS: [&str; 7] = ["asks", "now", "working", "awaiting", "bgServices", "servingFoldedIds", "absentSids"];

/// One deliberate mistake, for `romp-spike feed --plant`: the comparison must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// A falsy card override (false, 0) falls through to the session flag and the master (`v or ...`, not `is None`).
    BellFalsySkip,
    /// The serving join keeps the first handoff row of a tracker id, not the last.
    ServingFirstRow,
    /// A handoff row's whoWorking is its card owner's working bit, not its recipient's.
    WhoOwner,
    /// Parked handoffs in the fold's order, not sorted by time.
    ParkedUnsorted,
    /// The notice cap counts held-mail cards too.
    NoticeCapHeld,
    /// `absentSids` at the end of the frame, not after `now` (a difference only the wire order shows).
    AbsentLast,
}

impl Plant {
    pub const ALL: [Plant; 6] = [
        Plant::BellFalsySkip,
        Plant::ServingFirstRow,
        Plant::WhoOwner,
        Plant::ParkedUnsorted,
        Plant::NoticeCapHeld,
        Plant::AbsentLast,
    ];

    pub fn parse(name: &str) -> Option<Plant> {
        Plant::ALL.into_iter().find(|p| p.name() == name)
    }

    pub fn name(self) -> &'static str {
        match self {
            Plant::BellFalsySkip => "bell-falsy-skip",
            Plant::ServingFirstRow => "serving-first-row",
            Plant::WhoOwner => "who-owner",
            Plant::ParkedUnsorted => "parked-unsorted",
            Plant::NoticeCapHeld => "notice-cap-held",
            Plant::AbsentLast => "absent-last",
        }
    }
}

// ───────────────────────────── colormap.py ─────────────────────────────

/// cm.stops_for(name): `COLORMAPS.get((name or "").lower(), COLORMAPS[DEFAULT])`. Rust's `to_lowercase` stands in for
/// Python's `lower()`: the result is compared with ASCII names only, and on every code point the two lower to ASCII
/// letters exactly where they agree (A to Z, a to z, the Kelvin sign; S4.6's table test), so the equality cannot differ.
pub fn stops_for(name: Option<&str>) -> &'static [[i64; 3]] {
    let key = name.unwrap_or("").to_lowercase();
    let default = COLORMAPS.iter().find(|(n, _)| *n == DEFAULT_COLORMAP).expect("the default is listed").1;
    COLORMAPS.iter().find(|(n, _)| *n == key).map_or(default, |(_, s)| s)
}

/// cm.ramp(v, stops): v clamped to [0, 1], then the two neighboring stops mixed by the fraction, each channel
/// `round()`ed half to even, in Python's order of operations (`a + (b - a) * fr`).
pub fn ramp(v: f64, stops: &[[i64; 3]]) -> [i64; 3] {
    let v = py_max(0.0, py_min(1.0, v));
    let last = stops.len() - 1;
    let x = v * last as f64;
    let i = x as i64; // int(x): x >= 0, so truncation is the floor
    let fr = x - i as f64;
    if i >= last as i64 {
        return stops[last];
    }
    let (a, b) = (stops[i as usize], stops[i as usize + 1]);
    [0, 1, 2].map(|j| (a[j] as f64 + (b[j] - a[j]) as f64 * fr).round_ties_even() as i64)
}

/// Python's `min(a, b)` of two floats: the first unless the second is smaller.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Python's `max(a, b)` of two floats: the first unless the second is larger.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// cm.age_rgb(age, name): the age clamped to [FADE_LO, FADE_HI], its position on a log scale, then `ramp(1 - f)` (recent
/// is the bright end). The logarithms run through the platform's libm at run time, as Python's `math.log` does; the
/// constants are kept from compile-time folding.
pub fn age_rgb(age: f64, name: Option<&str>) -> [i64; 3] {
    let a = py_max(FADE_LO, py_min(FADE_HI, age));
    let lo = std::hint::black_box(FADE_LO).ln();
    let hi = std::hint::black_box(FADE_HI).ln();
    let f = (a.ln() - lo) / (hi - lo);
    ramp(1.0 - f, stops_for(name))
}

/// `float(now - t)` in Python arithmetic, `t` None meaning the clock itself: an int minus an int exactly, then rounded
/// to the nearest float; with a float on either side, both as floats.
pub fn age_of(now: Num, t: Option<Num>) -> f64 {
    match (now, t.unwrap_or(now)) {
        (Num::Int(a), Num::Int(b)) => (a as i128 - b as i128) as f64,
        (a, b) => a.as_f64() - b.as_f64(),
    }
}

fn rgb_out(c: [i64; 3]) -> Out {
    Out::Array(c.iter().map(|&x| Out::Int(x)).collect())
}

// ───────────────────────────── small Python readings over the entry's JSON ─────────────────────────────

type Obj = Vec<(String, Out)>;

fn get<'a>(o: &'a Obj, k: &str) -> Option<&'a Out> {
    o.iter().find(|(x, _)| x == k).map(|(_, v)| v)
}

fn get_mut<'a>(o: &'a mut Obj, k: &str) -> Option<&'a mut Out> {
    o.iter_mut().find(|(x, _)| x == k).map(|(_, v)| v)
}

/// `d[k] = v`: in place when the key is there, else appended.
fn set(o: &mut Obj, k: &str, v: Out) {
    match get_mut(o, k) {
        Some(slot) => *slot = v,
        None => o.push((k.to_string(), v)),
    }
}

/// `d.pop(k, None)`.
fn pop(o: &mut Obj, k: &str) {
    o.retain(|(x, _)| x != k);
}

fn truthy(v: &Out) -> bool {
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

fn num(v: &Out) -> Option<Num> {
    match v {
        Out::Int(i) => Some(Num::Int(*i)),
        Out::Float(f) => Some(Num::Float(*f)),
        _ => None,
    }
}

fn obj(members: Vec<(&str, Out)>) -> Out {
    Out::Object(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn s(x: &str) -> Out {
    Out::Str(x.to_string())
}

/// `s[:n]` in code points.
fn head(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map_or(s, |(i, _)| &s[..i])
}

/// A card's tree rows (`card.get("tree") or []`).
fn rows_of(card: &Obj) -> &[Out] {
    match get(card, "tree") {
        Some(Out::Array(rows)) => rows,
        _ => &[],
    }
}

fn rows_of_mut(card: &mut Obj) -> Option<&mut Vec<Out>> {
    match get_mut(card, "tree") {
        Some(Out::Array(rows)) => Some(rows),
        _ => None,
    }
}

fn as_obj(v: &Out) -> Option<&Obj> {
    match v {
        Out::Object(m) => Some(m),
        _ => None,
    }
}

fn as_obj_mut(v: &mut Out) -> Option<&mut Obj> {
    match v {
        Out::Object(m) => Some(m),
        _ => None,
    }
}

fn str_of(v: Option<&Out>) -> Option<&str> {
    match v {
        Some(Out::Str(s)) => Some(s),
        _ => None,
    }
}

/// `str(card.get("sid") or "")`: layer 6 writes a sid string or null.
fn sid_of(card: &Obj) -> &str {
    str_of(get(card, "sid")).unwrap_or("")
}

/// `int(f)` of a float (truncated toward zero), or None when the integer is past i64 (2**63 and beyond, either sign;
/// refused at load wherever the port would read it).
fn float_int(f: f64) -> Option<i64> {
    Some(f.trunc()).filter(|t| t.abs() < 9.223_372_036_854_776e18).map(|t| t as i64)
}

/// `int(x)` of a JSON number or bool (Python truncates a float toward zero; `True` is 1); None for anything else, or a
/// float whose integer is past i64 (refused at load).
pub fn py_int(v: &J) -> Option<i64> {
    match v {
        J::Bool(b) => Some(i64::from(*b)),
        J::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|_| n.is_f64()).and_then(float_int)),
        _ => None,
    }
}

/// `int(v or 0)` over a value `validate` admitted.
fn int_or0(v: Option<&J>) -> i64 {
    match v.filter(|v| py_truthy(v)) {
        None => 0,
        Some(v) => py_int(v).expect("validated at load: a number or a bool within i64"),
    }
}

/// `int(now)`, which only a parked send with no time reads; `validate` refuses a clock past i64 while the postal log
/// holds a parked send.
fn int_now(now: Num) -> i64 {
    match now {
        Num::Int(i) => i,
        Num::Float(f) => float_int(f).expect("validated at load: a clock within i64 when a parked send reads it"),
    }
}

/// kernel._safe_id(s): a single path component the maildir lookup reads as itself. `[A-Za-z0-9]` in Python's pattern is
/// a literal ASCII range, so every admitted id is ASCII and its length in code points is its length in bytes.
pub fn safe_id(s: &str) -> bool {
    if s.is_empty() || s.chars().count() > 128 {
        return false;
    }
    if s.contains(['/', '\\', '\0']) || s.starts_with('.') {
        return false;
    }
    let mut cs = s.chars();
    cs.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && cs.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

// ───────────────────────────── the feed ─────────────────────────────

/// The feed frame the port built, and what it holds.
pub struct Feed {
    /// The frame, keys in Python's insertion order (what `json.dumps(feed)` writes, the wire oracle).
    pub frame: Out,
    pub sessions: usize,
    pub cards: usize,
    pub rows: usize,
    pub absent: usize,
    pub folded: usize,
    pub parked: usize,
    pub notices: usize,
}

impl Feed {
    /// `json.dumps(feed)`: the wire bytes.
    pub fn wire(&self) -> String {
        romp_json::dumps(&self.frame)
    }

    /// `json.dumps(feed, sort_keys=True)`: the canonical bytes, the S4 gate.
    pub fn canon(&self) -> String {
        romp_json::dumps(&crate::record::canon(&self.frame))
    }
}

struct Pass<'p, 'a> {
    b: &'p Build<'a>,
    snap: &'a Snapshot,
    now: Num,
    cmap: Option<&'a str>,
    bells: Option<&'a Map<String, J>>,
    plant: Option<Plant>,
}

impl Pass<'_, '_> {
    /// _feed_fold_bell's pair, which the bell pass computes the same way: `notify` from _notify_card_effective (the
    /// card's override, else the session's flag, else the master; true or null) and `notifyRaw` from
    /// _push_explicit_bell (the card's override, else the session's flag; true, false or null).
    fn bell(&self, card: &Obj) -> (Out, Out) {
        let sid = sid_of(card);
        let item = str_of(get(card, "itemId")).unwrap_or("");
        let over = self.bells.and_then(|m| m.get(item)).filter(|v| !v.is_null());
        let over = match self.plant {
            Some(Plant::BellFalsySkip) => over.filter(|v| py_truthy(v)),
            _ => over,
        };
        let flag = || self.b.session_flag_raw(sid, "notify");
        let effective = match over {
            Some(v) => py_truthy(v),
            None => flag().unwrap_or_else(|| self.bells.and_then(|m| m.get(NOTIFY_ALL_KEY)).is_some_and(py_truthy)),
        };
        let raw = match over.filter(|_| !item.is_empty()) {
            Some(v) => Some(py_truthy(v)),
            None if sid.is_empty() => None,
            None => flag(),
        };
        (if effective { Out::Bool(true) } else { Out::Null }, raw.map_or(Out::Null, Out::Bool))
    }

    /// _feed_fold_tints, _feed_fold_stamp and _feed_fold_card over one card: the clock inputs read, `_ageT` popped from
    /// the card, `t := now` when it was null, `trgb` appended, the same for every row, then the bell pair (a session
    /// card's; a serving-fold candidate's stamp carries none).
    fn fold_card(&self, card: &mut Obj, bell: Option<(Out, Out)>) {
        let age_t = get(card, "_ageT").and_then(num);
        let row_ts: Vec<Option<Num>> =
            rows_of(card).iter().map(|r| as_obj(r).and_then(|r| get(r, "_ageT")).and_then(num)).collect();
        pop(card, "_ageT");
        if age_t.is_none() {
            set(card, "t", self.now.to_json());
        }
        set(card, "trgb", rgb_out(age_rgb(age_of(self.now, age_t), self.cmap)));
        if let Some(rows) = rows_of_mut(card) {
            for (r, rt) in rows.iter_mut().zip(row_ts) {
                if let Some(r) = as_obj_mut(r) {
                    pop(r, "_ageT");
                    set(r, "trgb", rgb_out(age_rgb(age_of(self.now, rt), self.cmap)));
                }
            }
        }
        if let Some((n, raw)) = bell {
            set(card, "notify", n);
            set(card, "notifyRaw", raw);
        }
    }

    fn color(&self, sid: &str) -> Out {
        color_json(self.b.names.name_color(sid))
    }

    fn name_or_head<'n>(&'n self, sid: &'n str) -> &'n str {
        self.b.names.name_of(sid).unwrap_or_else(|| head(sid, 8))
    }

    /// kernel._parked_handoffs over the postal log's fold, then build_feed's parked-handoff card per handoff whose id is
    /// not cleared.
    fn parked_cards(&self, alive: &HashSet<&str>) -> Vec<Out> {
        let board = self.snap.board.as_ref();
        let log = board.and_then(|b| b.postal_log.as_deref()).unwrap_or_default();
        let standing: HashSet<(&str, &str)> = board
            .and_then(|b| b.mail_new.as_ref())
            .map(|m| m.iter().map(|(to, mid)| (to.as_str(), mid.as_str())).collect())
            .unwrap_or_default();
        struct Ph<'a> {
            mid: &'a str,
            to: &'a str,
            from_name: &'a str,
            to_name: &'a str,
            t: i64,
        }
        let mut out: Vec<Ph> = Vec::new();
        for (mid, c) in parked_fold(log) {
            if alive.contains(c.to) || !standing.contains(&(c.to, mid)) {
                continue;
            }
            out.push(Ph {
                mid,
                to: c.to,
                from_name: self.name_or_head(c.from),
                to_name: self.name_or_head(c.to),
                t: c.t.unwrap_or_else(|| int_now(self.now)),
            });
        }
        if self.plant != Some(Plant::ParkedUnsorted) {
            out.sort_by_key(|h| h.t);
        }
        let mut cards = Vec::new();
        for ph in out {
            let item_id = format!("parked:{}", ph.mid);
            if self.b.cleared().contains(&item_id) {
                continue;
            }
            cards.push(obj(vec![
                ("itemId", s(&item_id)),
                ("sid", s(ph.to)),
                ("name", s(ph.to_name)),
                ("color", self.color(ph.to)),
                ("text", Out::Str(format!("Hand-off parked for {} (offline)", ph.to_name))),
                ("t", Out::Int(ph.t)),
                ("live", Out::Bool(false)),
                ("trgb", rgb_out(age_rgb(age_of(self.now, Some(Num::Int(ph.t))), self.cmap))),
                ("turnId", s(&item_id)),
                ("origin", Out::Null),
                ("followupPending", Out::Null),
                ("waitingOn", Out::Null),
                ("summary", Out::Null),
                ("blockSummary", Out::Null),
                ("background", Out::Null),
                ("summaryAnchorUuid", Out::Null),
                ("warns", Out::Null),
                ("nudged", Out::Null),
                ("blocked", obj(vec![
                    ("state", s("parkedHandoff")),
                    ("toSid", s(ph.to)),
                    ("toName", s(ph.to_name)),
                    ("what", Out::Str(format!("a handoff from {} is parked — revive {} to deliver it", ph.from_name,
                                              ph.to_name))),
                ])),
                ("column", s("needs_input")),
                ("board", s("feed")),
                ("category", s("needs_input")),
                ("tree", Out::Array(Vec::new())),
            ]));
        }
        cards
    }

    /// kernel._notice_cards(now, cleared, alive): every notice file in sorted order, its projection, one card per row,
    /// then all of them sorted by (t, itemId).
    fn notice_cards(&self, alive: &HashSet<&str>) -> Vec<Out> {
        let Some(files) = self.snap.board.as_ref().and_then(|b| b.notices.as_ref()) else { return Vec::new() };
        let mut sids: Vec<&String> = files.keys().collect();
        sids.sort();
        let mut out: Vec<(i64, String, Out)> = Vec::new();
        for sid in sids {
            let ownerless = sid == NOTICE_OWNERLESS_SID;
            let rows: Vec<&Map<String, J>> = files
                .get(sid)
                .expect("a listed file")
                .iter()
                .filter_map(J::as_object)
                .filter(|o| o.get("op").is_some_and(py_truthy) && o.get("key").is_some_and(py_truthy))
                .collect();
            for p in self.notice_projection(sid, &rows) {
                let r = p.r;
                let item_id = notice_item_id(sid, p.key, p.rev);
                let t = int_or0(r.get("t"));
                let needs = if r.get("needsYou").is_some_and(py_truthy) { "needs_input" } else { "completed" };
                let board_id = r.get("board").filter(|v| py_truthy(v)).map_or_else(|| s("feed"), ToJson::to_json);
                let category_id = r.get("category").filter(|v| py_truthy(v)).map_or_else(|| s(needs), ToJson::to_json);
                let column = if board_id == s("feed") { category_id.clone() } else { s(needs) };
                let or_empty = |k: &str| r.get(k).filter(|v| py_truthy(v)).map_or_else(|| s(""), ToJson::to_json);
                let actions: Vec<Out> = if p.acted {
                    Vec::new()
                } else {
                    r.get("actions")
                        .filter(|v| py_truthy(v))
                        .and_then(J::as_array)
                        .map(|acts| {
                            acts.iter()
                                .map(|a| {
                                    let a = a.as_object().expect("validated at load: an action is a dict");
                                    let mut m: Obj = a.iter().map(|(k, v)| (k.clone(), v.to_json())).collect();
                                    set(&mut m, "kind", Out::Str(action_kind(a)));
                                    Out::Object(m)
                                })
                                .collect()
                        })
                        .unwrap_or_default()
                };
                let card = obj(vec![
                    ("itemId", s(&item_id)),
                    ("sid", s(sid)),
                    ("name", if ownerless { s(NOTICE_OWNERLESS_NAME) } else { s(self.name_or_head(sid)) }),
                    ("color", if ownerless { Out::Null } else { self.color(sid) }),
                    ("text", or_empty("title")),
                    ("t", Out::Int(t)),
                    ("live", Out::Bool(!ownerless && alive.contains(sid.as_str()))),
                    ("board", board_id),
                    ("category", category_id),
                    ("trgb", rgb_out(age_rgb(age_of(self.now, Some(Num::Int(t))), self.cmap))),
                    ("turnId", s(&item_id)),
                    ("origin", Out::Null),
                    ("followupPending", Out::Null),
                    ("waitingOn", Out::Null),
                    ("summary", Out::Null),
                    ("blockSummary", Out::Null),
                    ("background", Out::Null),
                    ("summaryAnchorUuid", Out::Null),
                    ("warns", Out::Null),
                    ("nudged", Out::Null),
                    ("blocked", Out::Null),
                    ("notice", obj(vec![
                        ("producer", or_empty("producer")),
                        ("key", s(p.key)),
                        ("rev", Out::Int(p.rev)),
                        ("body", or_empty("body")),
                        ("attachment", r.get("attachment").map_or(Out::Null, ToJson::to_json)),
                        ("actions", Out::Array(actions)),
                        ("expiresAt", r.get("expiresAt").map_or(Out::Null, ToJson::to_json)),
                        ("dismissOnAction", Out::Bool(r.get("dismissOnAction").is_some_and(py_truthy))),
                        ("acted", Out::Bool(p.acted || r.get("acted").is_some_and(py_truthy))),
                    ])),
                    ("column", column),
                    ("tree", Out::Array(Vec::new())),
                ]);
                out.push((t, item_id, card));
            }
        }
        out.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
        out.into_iter().map(|(_, _, c)| c).collect()
    }

    /// kernel._notice_projection(sid, now, cleared) over one file's filtered rows.
    fn notice_projection<'r>(&self, sid: &str, rows: &[&'r Map<String, J>]) -> Vec<Proj<'r>> {
        let mut newest: Vec<(&'r str, &'r Map<String, J>)> = Vec::new();
        let mut retired: HashSet<(&str, i64)> = HashSet::new();
        let mut acted: HashSet<(&str, i64)> = HashSet::new();
        for &r in rows {
            let op = r.get("op").and_then(J::as_str);
            if !matches!(op, Some("post" | "expire" | "acted")) {
                continue;
            }
            let k = r.get("key").and_then(J::as_str).expect("validated at load: a string key");
            let rev = int_or0(r.get("rev"));
            match op {
                Some("post") => match newest.iter_mut().find(|(x, _)| *x == k) {
                    None => newest.push((k, r)),
                    Some((_, cur)) => {
                        if rev >= int_or0(cur.get("rev")) {
                            *cur = r;
                        }
                    }
                },
                Some("expire") => {
                    retired.insert((k, rev));
                }
                _ => {
                    acted.insert((k, rev));
                }
            }
        }
        let mut out: Vec<Proj> = Vec::new();
        for &(k, r) in &newest {
            let rev = int_or0(r.get("rev"));
            if retired.contains(&(k, rev)) {
                continue;
            }
            let was_acted = acted.contains(&(k, rev));
            if let Some(exp) = r.get("expiresAt").filter(|v| py_truthy(v)) {
                let exp = py_int(exp).expect("validated at load: a number or a bool within i64");
                if self.now.py_cmp(Num::Int(exp)) != std::cmp::Ordering::Less {
                    continue;
                }
            }
            if self.b.cleared().contains(&notice_item_id(sid, k, rev)) {
                continue;
            }
            out.push(Proj { r, key: k, rev, acted: was_acted });
        }
        out.sort_by(|a, b| (int_or0(a.r.get("t")), a.key).cmp(&(int_or0(b.r.get("t")), b.key)));
        let newest_of = |k: &str| newest.iter().find(|(x, _)| *x == k).expect("a projected key is newest").1;
        let capped: Vec<&str> = out
            .iter()
            .filter(|p| self.plant == Some(Plant::NoticeCapHeld) || !held_mail_row(newest_of(p.key)))
            .map(|p| p.key)
            .collect();
        if capped.len() > NOTICE_LIVE_KEYS_MAX {
            let gone: HashSet<&str> = capped[..capped.len() - NOTICE_LIVE_KEYS_MAX].iter().copied().collect();
            out.retain(|p| !gone.contains(p.key));
        }
        out
    }
}

/// One projected notice row: the post row, its key and int rev, and whether an acted row spent it (Python's
/// `dict(r, actions=[], acted=True)`).
struct Proj<'r> {
    r: &'r Map<String, J>,
    key: &'r str,
    rev: i64,
    acted: bool,
}

/// kernel._notice_item_id(sid, key, rev).
fn notice_item_id(sid: &str, key: &str, rev: i64) -> String {
    format!("notice:{sid}:{key}:{rev}")
}

/// kernel._notice_action_kind(a): the stripped `kind`, else the route read as a kind (`/send`), else "".
fn action_kind(a: &Map<String, J>) -> String {
    let field = |k: &str| a.get(k).filter(|v| py_truthy(v)).and_then(J::as_str).map_or("", pystr::strip);
    let kind = field("kind");
    if !kind.is_empty() {
        return kind.to_string();
    }
    if field("route") == "/send" { "send".into() } else { String::new() }
}

/// kernel._held_mail_row(r): the held-mail producer's row with every action of the quarantine kind on its own key, or
/// with no actions in the owner-less home.
fn held_mail_row(r: &Map<String, J>) -> bool {
    if r.get("producer").and_then(J::as_str) != Some(HELD_MAIL_PRODUCER) {
        return false;
    }
    let acts = r.get("actions").filter(|v| py_truthy(v)).and_then(J::as_array);
    let Some(acts) = acts else { return r.get("sid").and_then(J::as_str) == Some(NOTICE_OWNERLESS_SID) };
    let key = r.get("key").and_then(J::as_str);
    acts.iter().all(|a| {
        let a = a.as_object().expect("validated at load: an action is a dict");
        action_kind(a) == "quarantine"
            && a.get("body").filter(|v| py_truthy(v)).and_then(J::as_object).and_then(|b| b.get("mid")).and_then(J::as_str)
                == key
    })
}

/// One parked-handoff candidate of the fold: sender, recipient, time (`int(t)` of a truthy time, else None).
struct Cand<'a> {
    from: &'a str,
    to: &'a str,
    t: Option<i64>,
}

/// _parked_fold_step over the postal log's records (fold_records steps only dicts): a parked `sent` row with an id, a
/// recipient and a sender enters (a re-sent id keeps its place, its value replaced), a `recall` or `bounced` row
/// removes its id.
fn parked_fold(log: &[J]) -> Vec<(&str, Cand<'_>)> {
    let mut state: Vec<(&str, Cand)> = Vec::new();
    for o in log.iter().filter_map(J::as_object) {
        let Some(mid) = o.get("id").filter(|v| py_truthy(v)) else { continue };
        match o.get("ev").and_then(J::as_str) {
            Some("sent") => {
                let (to, from) = (o.get("to_id"), o.get("from_id"));
                if !(o.get("park").is_some_and(py_truthy) && to.is_some_and(py_truthy) && from.is_some_and(py_truthy)) {
                    continue;
                }
                fn text(v: Option<&J>) -> &str {
                    v.and_then(J::as_str).expect("validated at load: a string id")
                }
                let t = o.get("t").filter(|v| py_truthy(v)).and_then(py_int);
                let c = Cand { from: text(from), to: text(to), t };
                let mid = text(Some(mid));
                match state.iter_mut().find(|(m, _)| *m == mid) {
                    Some((_, slot)) => *slot = c,
                    None => state.push((mid, c)),
                }
            }
            Some("recall" | "bounced") => {
                if let Some(mid) = mid.as_str() {
                    state.retain(|(m, _)| *m != mid);
                }
            }
            _ => {}
        }
    }
    state
}

/// build_feed over the snapshot: layer 6's entries for every alive session, folded in alive order, the serving join,
/// the whoWorking stamp, the parked and notice cards, the bell pass and the frame; or the raise that fails the whole
/// build (the one an admitted input reaches: a null card in an entry's asks, which the fold reads with `.get`).
/// `validate` must have admitted the snapshot.
pub fn build_feed(snap: &Snapshot, trees: &[(String, Option<Tree>)], plant: Option<Plant>) -> Result<Feed, Raise> {
    let b = Build::new(snap, trees, None);
    let world = snap.world.as_ref().expect("required at load");
    let p = Pass {
        b: &b,
        snap,
        now: snap.now(),
        cmap: world.colormap.as_ref().and_then(|c| c.as_deref()),
        bells: snap.board.as_ref().and_then(|bd| bd.notify_cards.as_ref()).and_then(J::as_object),
        plant,
    };
    let alive_rows = snap.alive();
    let alive: HashSet<&str> = alive_rows.iter().filter_map(|r| r.sid.as_deref()).collect();
    let (mut asks, mut working, mut awaiting, mut serving) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let mut bg: Obj = Vec::new();
    let mut absent: Vec<String> = Vec::new();
    let mut working_sids: HashSet<String> = HashSet::new();
    let entries = b.entries();
    let sessions = entries.len();
    for (row, (sid, got)) in alive_rows.iter().zip(entries) {
        let entry = match got {
            Err(_) => {
                absent.push(sid);
                continue;
            }
            Ok(Out::Object(e)) => e,
            Ok(_) => continue, // a hidden session's null folds nothing
        };
        let mut entry = entry;
        let mut take = |k: &str| match get_mut(&mut entry, k).map(|v| std::mem::replace(v, Out::Null)) {
            Some(Out::Array(items)) => items,
            _ => Vec::new(),
        };
        let (cards, folds) = (take("asks"), take("servingFolds"));
        for card in cards {
            let mut card = match card {
                Out::Object(m) => m,
                // _awaiting_card answers None for a session that is not live, and the body appends it: the fold's
                // `card.get("_ageT")` then raises, outside the per-session guard, so the whole build raises
                Out::Null => {
                    return Err(Raise { exc: "AttributeError", arg: "'NoneType' object has no attribute 'get'".into() });
                }
                other => panic!("layer 6 writes cards as objects or null, not {other:?}"),
            };
            let bell = p.bell(&card);
            p.fold_card(&mut card, Some(bell));
            asks.push(Out::Object(card));
        }
        for mut f in folds {
            if let Some(card) = as_obj_mut(&mut f).and_then(|f| get_mut(f, "card")).and_then(as_obj_mut) {
                p.fold_card(card, None);
            }
            serving.push(f);
        }
        if let Some(w) = get(&entry, "working").filter(|w| truthy(w)) {
            working.push(w.clone());
            working_sids.insert(sid.clone());
        }
        if let Some(a) = get(&entry, "awaiting").filter(|a| truthy(a)) {
            awaiting.push(a.clone());
        }
        if let Some(v) = get(&entry, "bgServices").filter(|v| truthy(v)) {
            let name = row.name.as_ref().and_then(|n| n.as_deref()).expect("validated at load: a string name");
            set(&mut bg, name, v.clone());
        }
    }

    // THE SERVING FOLD, commit side: each candidate's rows join its tracker row when that row rendered this build.
    let n_folded = asks.len();
    let mut serving_folded: Vec<Out> = Vec::new();
    if !serving.is_empty() {
        let mut byrow: HashMap<String, (usize, usize)> = HashMap::new();
        for (ci, c) in asks.iter().enumerate() {
            for (ri, r) in as_obj(c).map(rows_of).unwrap_or_default().iter().enumerate() {
                let Some(r) = as_obj(r) else { continue };
                if str_of(get(r, "kind")) == Some("handoff") {
                    let id = str_of(get(r, "id")).expect("layer 6 writes a row id").to_string();
                    if plant == Some(Plant::ServingFirstRow) && byrow.contains_key(&id) {
                        continue;
                    }
                    byrow.insert(id, (ci, ri));
                }
            }
        }
        for f in serving {
            let f = as_obj(&f).expect("layer 6 writes a serving fold as an object");
            let card = as_obj(get(f, "card").expect("a candidate's card")).expect("a card object");
            let hit = str_of(get(f, "tracker")).and_then(|t| byrow.get(t)).copied();
            let Some((ci, ri)) = hit else {
                asks.push(Out::Object(card.clone()));
                continue;
            };
            serving_folded.push(get(card, "itemId").cloned().unwrap_or(Out::Null));
            let rows = rows_of(card);
            if let Some(first) = rows.first() {
                let first_id = as_obj(first).and_then(|r| get(r, "id")).cloned().expect("layer 6 writes a row id");
                let tree = rows_of_mut(as_obj_mut(&mut asks[ci]).expect("a card")).expect("a tracker card's tree");
                let row = as_obj_mut(&mut tree[ri]).expect("a row");
                match get_mut(row, "children") {
                    Some(Out::Array(kids)) => kids.push(first_id),
                    Some(_) => panic!("layer 6 writes a row's children as a list"),
                    None => row.push(("children".into(), Out::Array(vec![first_id]))),
                }
                tree.extend(rows.iter().cloned());
            }
        }
    }

    // A HANDOFF ROW'S DOT IS ITS RECIPIENT'S: whoWorking from the sids behind `working`.
    for c in asks.iter_mut() {
        let Some(c) = as_obj_mut(c) else { continue };
        let owner = sid_of(c).to_string();
        let Some(rows) = rows_of_mut(c) else { continue };
        for r in rows.iter_mut().filter_map(as_obj_mut) {
            if str_of(get(r, "kind")) != Some("handoff") {
                continue;
            }
            let Some(who) = get(r, "whoSid").filter(|w| truthy(w)) else { continue };
            let who = if plant == Some(Plant::WhoOwner) { owner.as_str() } else { str_of(Some(who)).unwrap_or("") };
            let w = working_sids.contains(who);
            if get(r, "whoWorking").is_some_and(truthy) != w {
                set(r, "whoWorking", Out::Bool(w));
            }
        }
    }

    // The producers after the loop, then the bell pass over every card the fold did not stamp.
    let parked = p.parked_cards(&alive);
    let n_parked = parked.len();
    asks.extend(parked);
    let notices = p.notice_cards(&alive);
    let n_notices = notices.len();
    asks.extend(notices);
    for a in asks[n_folded..].iter_mut().filter_map(as_obj_mut) {
        let (n, raw) = p.bell(a);
        set(a, "notify", n);
        set(a, "notifyRaw", raw);
    }

    let cards = asks.len();
    let rows = asks.iter().filter_map(as_obj).map(|c| rows_of(c).len()).sum();
    let (n_absent, n_folded_ids) = (absent.len(), serving_folded.len());
    let mut frame: Obj = Vec::new();
    let mut absent_out = Some(Out::Array(absent.into_iter().map(Out::Str).collect()));
    let mut folded_out = Some(Out::Array(serving_folded));
    let mut asks = Some(Out::Array(asks));
    let (mut working, mut awaiting, mut bg) = (Some(Out::Array(working)), Some(Out::Array(awaiting)), Some(Out::Object(bg)));
    let pass = snap.frame.as_ref().expect("required at load");
    for k in snap.frame_keys.as_deref().unwrap_or_default() {
        match k.as_str() {
            "servingFoldedIds" | "absentSids" => {}
            "asks" => frame.push((k.clone(), asks.take().unwrap_or(Out::Null))),
            "working" => frame.push((k.clone(), working.take().unwrap_or(Out::Null))),
            "awaiting" => frame.push((k.clone(), awaiting.take().unwrap_or(Out::Null))),
            "bgServices" => frame.push((k.clone(), bg.take().unwrap_or(Out::Null))),
            "now" => {
                frame.push((k.clone(), p.now.to_json()));
                if n_folded_ids > 0 {
                    frame.push(("servingFoldedIds".into(), folded_out.take().expect("once")));
                }
                if n_absent > 0 && plant != Some(Plant::AbsentLast) {
                    frame.push(("absentSids".into(), absent_out.take().expect("once")));
                }
            }
            other => frame.push((k.clone(), pass.get(other).expect("validated at load: a passthrough key").to_json())),
        }
    }
    if n_absent > 0 && plant == Some(Plant::AbsentLast) {
        frame.push(("absentSids".into(), absent_out.take().expect("once")));
    }
    Ok(Feed {
        frame: Out::Object(frame),
        sessions,
        cards,
        rows,
        absent: n_absent,
        folded: n_folded_ids,
        parked: n_parked,
        notices: n_notices,
    })
}

// ───────────────────────────── refusals ─────────────────────────────

fn is_text(v: &J) -> bool {
    v.is_string()
}

/// The shapes layer 7 refuses at load (see the module's paragraph "Refused at load"). Run after `snapshot::validate`
/// and `layer6::validate`.
pub fn validate(snap: &Snapshot) -> Result<(), String> {
    for (i, r) in snap.alive().iter().enumerate() {
        if !matches!(&r.name, Some(Some(_))) {
            return Err(format!("alive[{i}].name: not a string (build_feed keys bgServices by it)"));
        }
    }
    let world = snap.world.as_ref().ok_or("no world")?;
    if world.colormap.is_none() {
        return Err("world: no colormap key (the replay's _colormap reads it)".into());
    }
    let board = snap.board.as_ref().ok_or("no board")?;
    if !board.notify_cards.as_ref().is_some_and(J::is_object) {
        return Err("board.notifyCards: not a dict (_notify_cards() answers a dict)".into());
    }
    for (sid, s) in snap.sessions.iter().flat_map(|m| m.iter()) {
        let Some(Some(st)) = &s.store else { continue };
        for (nid, nd) in st.nodes.iter().flat_map(|m| m.iter()) {
            if let Some(J::Object(srv)) = &nd.serving
                && srv.get("goalId").is_some_and(|g| g.is_array() || g.is_object())
            {
                return Err(format!("sessions.{sid}.store.nodes.{nid}.serving.goalId: a list or a dict (the serving \
                                    join hashes it)"));
            }
        }
    }
    let mut parked_send = false;
    for (i, o) in board.postal_log.iter().flatten().enumerate() {
        let Some(o) = o.as_object() else { continue };
        let at = format!("board.postalLog[{i}]");
        let Some(mid) = o.get("id").filter(|v| py_truthy(v)) else { continue };
        match o.get("ev").and_then(J::as_str) {
            Some("sent") => {
                let (to, from) = (o.get("to_id"), o.get("from_id"));
                if !(o.get("park").is_some_and(py_truthy) && to.is_some_and(py_truthy) && from.is_some_and(py_truthy)) {
                    continue;
                }
                parked_send = true;
                if !is_text(mid) || !to.is_some_and(is_text) || !from.is_some_and(is_text) {
                    return Err(format!("{at}: a parked send whose id, to_id or from_id is not a string"));
                }
                for (k, v) in [("id", mid), ("to_id", to.expect("checked above"))] {
                    if !safe_id(v.as_str().expect("checked above")) {
                        return Err(format!("{at}.{k}: not a safe path component (kernel._safe_id): Python looks the \
                                            parked mail up by path"));
                    }
                }
                if let Some(t) = o.get("t").filter(|v| py_truthy(v)) {
                    if t.is_string() {
                        return Err(format!("{at}.t: a string (Python's int(str) is not ported)"));
                    }
                    if t.is_number() && py_int(t).is_none() {
                        return Err(format!("{at}.t: past i64"));
                    }
                }
                if o.get("body").is_some_and(|b| py_truthy(b) && !b.is_string() && !b.is_array()) {
                    return Err(format!("{at}.body: truthy and neither a string nor a list (the fold slices it)"));
                }
            }
            Some("recall" | "bounced") if mid.is_array() || mid.is_object() => {
                return Err(format!("{at}.id: a list or a dict (the fold pops it from a dict)"));
            }
            _ => {}
        }
    }
    if let Num::Float(f) = snap.now()
        && parked_send
        && float_int(f).is_none()
    {
        return Err("world.now: int() past i64 while the postal log holds a parked send (a parked send with no time \
                    reads int(now))"
            .into());
    }
    for (sid, lines) in board.notices.iter().flat_map(|m| m.iter()) {
        if sid.is_empty() || sid.contains('/') || sid.contains('\0') {
            return Err(format!("board.notices.{sid:?}: not a notice file's sid"));
        }
        for (i, o) in lines.iter().enumerate() {
            let Some(o) = o.as_object() else { continue };
            if !(o.get("op").is_some_and(py_truthy) && o.get("key").is_some_and(py_truthy)) {
                continue;
            }
            let op = o.get("op").and_then(J::as_str);
            if !matches!(op, Some("post" | "expire" | "acted")) {
                continue;
            }
            let at = format!("board.notices.{sid}[{i}]");
            if !o.get("key").is_some_and(is_text) {
                return Err(format!("{at}.key: not a string"));
            }
            let int_ok = |k: &str| match o.get(k).filter(|v| py_truthy(v)) {
                None => true,
                Some(v) => py_int(v).is_some(),
            };
            if !int_ok("rev") {
                return Err(format!("{at}.rev: truthy and not a number or a bool within i64"));
            }
            if op != Some("post") {
                continue;
            }
            for k in ["t", "expiresAt"] {
                if !int_ok(k) {
                    return Err(format!("{at}.{k}: truthy and not a number or a bool within i64"));
                }
            }
            match o.get("actions").filter(|v| py_truthy(v)) {
                None => {}
                Some(J::Array(acts)) => {
                    for (j, a) in acts.iter().enumerate() {
                        let Some(a) = a.as_object() else {
                            return Err(format!("{at}.actions[{j}]: not a dict"));
                        };
                        for k in ["kind", "route"] {
                            if a.get(k).is_some_and(|v| py_truthy(v) && !v.is_string()) {
                                return Err(format!("{at}.actions[{j}].{k}: truthy and not a string"));
                            }
                        }
                        if a.get("body").is_some_and(|v| py_truthy(v) && !v.is_object()) {
                            return Err(format!("{at}.actions[{j}].body: truthy and not a dict"));
                        }
                    }
                }
                Some(_) => return Err(format!("{at}.actions: truthy and not a list")),
            }
        }
    }
    let keys = snap.frame_keys.as_deref().unwrap_or_default();
    if !keys.iter().any(|k| k == "now") {
        return Err("frameKeys: no now (servingFoldedIds and absentSids go right after it)".into());
    }
    let pass = snap.frame.as_ref().ok_or("no frame")?;
    for k in keys {
        if !BUILT_KEYS.contains(&k.as_str()) && pass.get(k).is_none() {
            return Err(format!("frameKeys: {k} is not in the frame"));
        }
    }
    Ok(())
}

// ───────────────────────────── a case of the edge set and the fuzz ─────────────────────────────

/// Load a layer 7 case (a world in the S2 snapshot schema with its trees; the frame keys are Python's own build's).
pub fn load_case(case: &J) -> Result<crate::layer6::Case, String> {
    let c = crate::layer6::Case::load(case)?;
    validate(&c.snapshot).map_err(|e| format!("snapshot: {e}"))?;
    Ok(c)
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
    fn the_tint_is_pythons() {
        // values from Python's colormap.age_rgb
        assert_eq!(age_rgb(0.0, None), [144, 136, 240], "age 0 clamps to the brightest stop");
        assert_eq!(age_rgb(1e12, Some("aurora")), [84, 178, 4], "past 96 h: the darkest stop");
        assert_eq!(age_rgb(0.0, Some("VIRIDIS")), [253, 231, 37], "the name is lowercased");
        assert_eq!(age_rgb(0.0, Some("nope")), age_rgb(0.0, Some("aurora")), "an unknown name is the default");
        assert_eq!(age_rgb(0.0, Some("")), age_rgb(0.0, Some("aurora")));
        assert_eq!(ramp(1.5, stops_for(None)), [144, 136, 240], "v clamps to 1");
        assert_eq!(ramp(-0.5, stops_for(None)), [84, 178, 4], "v clamps to 0");
        // ramp(1/16) on aurora: x = 0.5 between (84,178,4) and (0,180,115): 42, 179, 59.5 -> 60 (half to even)
        assert_eq!(ramp(0.0625, stops_for(None)), [42, 179, 60]);
        // x = 2.5 between (35,175,156) and (66,169,176): 50.5 -> 50 (half to even rounds down here), 172, 166
        assert_eq!(ramp(0.3125, stops_for(None)), [50, 172, 166]);
    }

    #[test]
    fn the_age_is_python_arithmetic() {
        assert_eq!(age_of(Num::Int(10), None), 0.0);
        assert_eq!(age_of(Num::Int(1781100000), Some(Num::Int(1781099880))), 120.0);
        assert_eq!(age_of(Num::Float(10.5), Some(Num::Int(10))), 0.5);
        assert_eq!(age_of(Num::Int(i64::MAX), Some(Num::Int(i64::MIN))), 1.8446744073709552e19, "exact, then rounded");
    }

    #[test]
    fn python_int_truncates_and_reads_bools() {
        let j = |s: &str| serde_json::from_str::<J>(s).unwrap();
        assert_eq!(py_int(&j("2.9")), Some(2));
        assert_eq!(py_int(&j("-2.9")), Some(-2));
        assert_eq!(py_int(&j("true")), Some(1));
        assert_eq!(py_int(&j("1e300")), None, "past i64: refused at load");
        assert_eq!(py_int(&j("\"5\"")), None);
        assert_eq!(int_or0(Some(&j("[]"))), 0, "a falsy value reads 0");
    }

    #[test]
    fn safe_ids_are_the_kernels() {
        // each checked against kernel._safe_id
        let long = |n: usize| "m".repeat(n);
        for ok in ["m1", "M1", "a.b_c-d", "0", "m1.", "m..1", long(128).as_str()] {
            assert!(safe_id(ok), "{ok:?} is safe");
        }
        for bad in ["", ".", "..", ".m1", "m1/", "a/b", "m\\1", "m\u{0}1", "-m1", "_m1", "m 1", "\u{fc}1", "m1\n",
                    "\u{212a}1", long(129).as_str()] {
            assert!(!safe_id(bad), "{bad:?} is not safe");
        }
    }

    #[test]
    fn int_of_a_clock_past_i64_is_none() {
        assert_eq!(float_int(-2.9), Some(-2));
        assert_eq!(float_int(9.2e18), Some(9_200_000_000_000_000_000));
        assert_eq!(float_int(1e19), None);
        assert_eq!(float_int(-1e19), None);
        assert_eq!(float_int(-9.223_372_036_854_776e18), None, "-2**63 itself is refused too (conservative)");
        assert_eq!(int_now(Num::Float(1781100000.75)), 1781100000);
    }

    #[test]
    fn notice_action_kinds_and_held_mail() {
        let m = |s: &str| serde_json::from_str::<Map<String, J>>(s).unwrap();
        assert_eq!(action_kind(&m(r#"{"kind": " send\u001c"}"#)), "send", "stripped the Python way");
        assert_eq!(action_kind(&m(r#"{"route": " /send "}"#)), "send");
        assert_eq!(action_kind(&m(r#"{"route": "/other"}"#)), "");
        assert_eq!(action_kind(&m(r#"{"kind": "", "route": "/send"}"#)), "send");
        assert!(held_mail_row(&m(r#"{"producer": "postal", "key": "m1",
                                      "actions": [{"kind": "quarantine", "body": {"mid": "m1"}}]}"#)));
        assert!(!held_mail_row(&m(r#"{"producer": "postal", "key": "m1",
                                       "actions": [{"kind": "quarantine", "body": {"mid": "m2"}}]}"#)));
        assert!(held_mail_row(&m(r#"{"producer": "postal", "key": "m1", "sid": "notes"}"#)));
        assert!(!held_mail_row(&m(r#"{"producer": "postal", "key": "m1"}"#)));
    }

    #[test]
    fn the_parked_fold_keeps_a_resent_id_in_place() {
        let log: Vec<J> = serde_json::from_str(
            r#"[{"ev": "sent", "id": "a", "to_id": "x", "from_id": "y", "park": true, "t": 5},
                {"ev": "sent", "id": "b", "to_id": "x", "from_id": "y", "park": 1},
                {"ev": "sent", "id": "a", "to_id": "z", "from_id": "y", "park": true, "t": 7.9},
                {"ev": "recall", "id": "b"}, 5,
                {"ev": "sent", "id": "b", "to_id": "x", "from_id": "y", "park": true, "t": [1]},
                {"ev": "sent", "id": "c", "to_id": "x", "from_id": "y", "park": false}]"#,
        )
        .unwrap();
        let got: Vec<(&str, &str, Option<i64>)> = parked_fold(&log).iter().map(|(m, c)| (*m, c.to, c.t)).collect();
        assert_eq!(got, [("a", "z", Some(7)), ("b", "x", None)]);
    }
}
