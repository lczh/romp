//! Layer 2 of rust/notes/s2-design.md section 2, "Segmentation", over the typed tree and store: em.segments with
//! em._segment_id, em.split_segment with em._seam_real_work, jd.apply_seams, kernel._turn_sans_placed_echoes and
//! kernel._segs_seam, the entry the per-card code calls (from _provisional_card, _feed_seg_fold and _FeedSegTurns.get).
//!
//! A `Segment` borrows its atoms: em.split_segment filters a segment's atoms by time, so a piece is a list of atom
//! references, not a slice of the turn. Each function follows its Python line for line. Four details a naive port
//! would get wrong, each pinned by the edge cases (tools/rust-spike/dump_layer2.py --edge-cases):
//! - Times keep Python's int/float tag (`Num`) and compare exactly across the two (`Num::py_cmp`), and an id prints
//!   its time with `"%d"`, which truncates a float (`Num::percent_d`).
//! - `min(hits, key=lambda s: s["t"])` keeps the first of equal times, and calls its key on every hit, so a hit seam
//!   without `t` raises KeyError even though the hit test read it as 0.
//! - `seamOf.text` is `sm.get("text", "")`: an absent text is `""`, a null one stays null.
//! - em._seam_real_work's floor (SEAM_PROSE_FLOOR) counts code points of the stripped joined text (em._text_of), not
//!   bytes and not em._prose_chars' unstripped sum.
//!
//! Python raises KeyError on some inputs the loader accepts (an absent `session_id` on a turn's first atom, an absent
//! `trigger`, a trigger dict without `uuid`, a hit seam without `t`); those return `Err(Raise)`, which the comparison
//! matches against the exception Python raised. The restored pre-cut branch of em.segments is refused at load
//! (`tree::validate_turn`), and a lazy atom never reaches here, so em.hydrate's call in _seam_real_work is dropped.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};

use romp_json::Value as Out;

use crate::layer1;
use crate::pystr;
use crate::record::{Num, ToJson};
use crate::sha1;
use crate::snapshot::{Seam, Store};
use crate::tree::{Atom, Turn};

/// em.SEAM_PROSE_FLOOR: assistant prose at least this long (code points of the stripped joined text) is real work.
pub const SEAM_PROSE_FLOOR: usize = 80;

/// An exception Python raises on an input the loader accepts: its name and `str(e)` (`'session_id'` for a KeyError).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Raise {
    pub exc: &'static str,
    pub arg: String,
}

impl Raise {
    fn key_error(key: &str) -> Raise {
        Raise { exc: "KeyError", arg: format!("'{key}'") }
    }

    /// As tools/rust-spike/dump_layer2.py records it: `{"raises": name, "arg": str(e)}`.
    pub fn to_json(&self) -> Out {
        Out::Object(vec![
            ("raises".to_string(), Out::Str(self.exc.to_string())),
            ("arg".to_string(), Out::Str(self.arg.clone())),
        ])
    }
}

/// What a seam tail follows: `{"top": sm.get("top"), "text": sm.get("text", "")}`. `text` None is a null text; an
/// absent one is `Some("")`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SeamOf<'a> {
    pub top: Option<&'a str>,
    pub text: Option<&'a str>,
}

/// One segment, as em.segments and em.split_segment build the dict: `t`, `end`, `trigger`, `atoms`, `id`, and on a
/// seam tail (and on a head re-split from one, which `dict(seg, ...)` copies) `seam: True` and `seamOf`.
#[derive(Debug, Clone, PartialEq)]
pub struct Segment<'a> {
    pub t: Num,
    pub end: Num,
    /// The opening atom's uuid, the turn trigger's, or None.
    pub trigger: Option<&'a str>,
    pub atoms: Vec<&'a Atom>,
    pub id: String,
    pub seam: bool,
    pub seam_of: Option<SeamOf<'a>>,
}

/// One deliberate mistake, for `romp-spike layer2 --plant`: the comparison must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// em.segments ends each segment at its own last atom's time, not at the next segment's first (or the turn's end).
    EndAtLastAtom,
    /// em._seam_real_work counts the prose floor in UTF-8 bytes, not code points.
    FloorInBytes,
}

/// What a run saw, for the coverage counts the comparison checks against Python's, and an optional plant.
#[derive(Debug, Clone, Default)]
pub struct Probe {
    pub plant: Option<Plant>,
    /// Pieces jd.apply_seams examined (one jd._seg_key call each), past its early return.
    pub pieces: usize,
    /// em.split_segment calls: pieces with a hit seam inside their span.
    pub split_calls: usize,
    /// Calls that split.
    pub splits: usize,
}

fn atom_t(a: &Atom) -> Num {
    a.t.expect("an atom's t is required at load")
}

/// kernel._turn_sans_placed_echoes(turn)'s atoms: the turn's atoms without those whose uuid is in `placedEchoes`
/// (all of them when `placedEchoes` is absent, null or empty). An atom without a uuid matches a null entry.
pub fn turn_sans_placed_echoes(turn: &Turn) -> Vec<&Atom> {
    let atoms = turn.atoms.as_deref().unwrap_or_default();
    match turn.placed_echoes.as_ref().and_then(Option::as_ref) {
        Some(placed) if !placed.is_empty() => {
            let placed: HashSet<Option<&str>> = placed.iter().map(Option::as_deref).collect();
            atoms.iter().filter(|a| !placed.contains(&a.uuid())).collect()
        }
        _ => atoms.iter().collect(),
    }
}

/// em._segment_id(rompuuid, seg_t, atoms, trigger_uuid): `rompuuid:%d(seg_t):hash`. The hash is the trigger atom's
/// text hash (else the first atom's, when it has text), unless that text is machine-written or the anchor is the
/// CLI's interrupt record; then, and for a text-less segment, it is sha1 of the anchor's uuid (else the first uuid
/// among the atoms, else "").
pub fn segment_id(rompuuid: &str, seg_t: Num, atoms: &[&Atom], trigger_uuid: Option<&str>) -> String {
    let (mut anchor, mut texted): (Option<&Atom>, Option<&Atom>) = (None, None);
    if let Some(tu) = trigger_uuid.filter(|u| !u.is_empty())
        && let Some(a) = atoms.iter().copied().find(|x| x.uuid() == Some(tu))
    {
        anchor = Some(a);
        if layer1::has_text(a) {
            texted = Some(a);
        }
    }
    if texted.is_none()
        && let Some(&first) = atoms.first()
    {
        anchor = anchor.or(Some(first));
        if layer1::has_text(first) {
            texted = Some(first);
        }
    }
    let machine_written =
        texted.is_some_and(|x| layer1::machine_written(x) || anchor.is_some_and(layer1::is_interrupt_record));
    let h = match texted {
        Some(x) if !machine_written => layer1::text_hash8(x),
        _ => {
            let basis = anchor
                .and_then(Atom::uuid)
                .filter(|u| !u.is_empty())
                .or_else(|| atoms.iter().find_map(|a| a.uuid().filter(|u| !u.is_empty())))
                .unwrap_or("");
            sha1::hex8(basis.as_bytes())
        }
    };
    format!("{rompuuid}:{}:{h}", seg_t.percent_d())
}

/// `"%s" % x` of a `session_id`: a string as is, null as `None`.
fn session_id_str(a: &Atom) -> Option<&str> {
    match &a.session_id {
        None => None,
        Some(None) => Some("None"),
        Some(Some(s)) => Some(s),
    }
}

/// em.segments(turn) over the turn's own atoms.
pub fn segments(turn: &Turn) -> Result<Vec<Segment<'_>>, Raise> {
    let atoms: Vec<&Atom> = turn.atoms.iter().flatten().collect();
    segments_of(turn, &atoms, &mut Probe::default())
}

/// em.segments over `atoms` (the turn's, or kernel._turn_sans_placed_echoes's): one segment per input atom
/// (em._is_segment_input) to the next input or the turn's end, leading atoms attached to the first at the turn's
/// `t`; one segment of the whole turn when no atom is an input. Ids last, after the leading attach.
fn segments_of<'a>(turn: &'a Turn, atoms: &[&'a Atom], probe: &mut Probe) -> Result<Vec<Segment<'a>>, Raise> {
    let rompuuid = match atoms.first() {
        None => "",
        Some(a) => session_id_str(a).ok_or_else(|| Raise::key_error("session_id"))?,
    };
    let turn_t = turn.t.expect("a turn's t is required at load");
    let turn_end = turn.end.expect("a turn's end is required at load");
    let starts: Vec<usize> = (0..atoms.len()).filter(|&i| layer1::is_segment_input(atoms[i])).collect();
    let mut segs = Vec::with_capacity(starts.len().max(1));
    if starts.is_empty() {
        // turn["trigger"]["uuid"] if turn["trigger"] else None: an empty dict is falsy
        let trigger = match &turn.trigger {
            None => return Err(Raise::key_error("trigger")),
            Some(None) => None,
            Some(Some(tr)) if tr.order.is_empty() => None,
            Some(Some(tr)) => match &tr.uuid {
                None => return Err(Raise::key_error("uuid")),
                Some(u) => u.as_deref(),
            },
        };
        segs.push(Segment {
            t: turn_t,
            end: turn_end,
            trigger,
            atoms: atoms.to_vec(),
            id: String::new(),
            seam: false,
            seam_of: None,
        });
    } else {
        for (k, &i0) in starts.iter().enumerate() {
            let i1 = starts.get(k + 1).copied().unwrap_or(atoms.len());
            let end = if probe.plant == Some(Plant::EndAtLastAtom) {
                atom_t(atoms[i1 - 1])
            } else if i1 < atoms.len() {
                atom_t(atoms[i1])
            } else {
                turn_end
            };
            segs.push(Segment {
                t: atom_t(atoms[i0]),
                end,
                trigger: atoms[i0].uuid(),
                atoms: atoms[i0..i1].to_vec(),
                id: String::new(),
                seam: false,
                seam_of: None,
            });
        }
        if starts[0] > 0 {
            let mut lead = atoms[..starts[0]].to_vec();
            lead.append(&mut segs[0].atoms);
            segs[0].atoms = lead;
            segs[0].t = turn_t;
        }
    }
    for seg in &mut segs {
        seg.id = segment_id(rompuuid, seg.t, &seg.atoms, seg.trigger);
    }
    Ok(segs)
}

/// em._seam_real_work(atoms): any assistant atom with a tool_use block, or whose em._text_of is at least
/// SEAM_PROSE_FLOOR code points. An API error counts like any assistant atom (Python does not check it).
pub fn seam_real_work(atoms: &[&Atom]) -> bool {
    real_work(atoms, None)
}

fn real_work(atoms: &[&Atom], plant: Option<Plant>) -> bool {
    let chars = |s: &str| if plant == Some(Plant::FloorInBytes) { s.len() } else { pystr::len(s) };
    atoms.iter().any(|a| {
        a.kind() == "assistant"
            && (!layer1::atom_tool_uses(a).is_empty() || chars(&layer1::atom_text_of(a)) >= SEAM_PROSE_FLOOR)
    })
}

/// em.split_segment(seg, t): (head, tail) or None. The head is the atoms at or before `t` (`a.get("t", 0) <= t`),
/// ending at the tail's first time and keeping every other key; the tail is the rest, trigger-less, `seam: True`,
/// with its own id. None unless both sides are non-empty and the tail holds real work.
pub fn split_segment<'a>(seg: &Segment<'a>, t: Num) -> Option<(Segment<'a>, Segment<'a>)> {
    split_with(seg, t, None)
}

fn split_with<'a>(seg: &Segment<'a>, t: Num, plant: Option<Plant>) -> Option<(Segment<'a>, Segment<'a>)> {
    let (head_a, tail_a): (Vec<&Atom>, Vec<&Atom>) =
        seg.atoms.iter().partition(|a| atom_t(a).py_cmp(t) != Ordering::Greater);
    if head_a.is_empty() || tail_a.is_empty() || !real_work(&tail_a, plant) {
        return None;
    }
    // atoms[0].get("session_id", ""): absent is "", null prints None
    let rompuuid = session_id_str(seg.atoms[0]).unwrap_or("");
    let tail_t = atom_t(tail_a[0]);
    let id = segment_id(rompuuid, tail_t, &tail_a, None);
    let head = Segment {
        t: seg.t,
        end: tail_t,
        trigger: seg.trigger,
        atoms: head_a,
        id: seg.id.clone(),
        seam: seg.seam,
        seam_of: seg.seam_of,
    };
    let tail = Segment { t: tail_t, end: seg.end, trigger: None, atoms: tail_a, id, seam: true, seam_of: None };
    Some((head, tail))
}

/// The seams jd.apply_seams reads: `(store or {}).get("seams") or []`, those whose `segs` is non-empty.
fn live_seams(store: Option<&Store>) -> Vec<(&Seam, &[String])> {
    let seams = store.and_then(|s| s.seams.as_ref()).and_then(Option::as_ref);
    seams
        .into_iter()
        .flatten()
        .filter_map(|s| match &s.segs {
            Some(Some(keys)) if !keys.is_empty() => Some((s, keys.as_slice())),
            _ => None,
        })
        .collect()
}

/// jd.apply_seams(segs, store): split every piece whose key (jd._seg_key of its id) a seam lists and whose span holds
/// that seam's time strictly inside (`p["t"] < s.get("t", 0) < p["end"]`), at the earliest such seam (the first of
/// equal times), until no piece splits. A tail carries `seamOf` from its seam.
pub fn apply_seams<'a>(segs: Vec<Segment<'a>>, store: Option<&'a Store>) -> Result<Vec<Segment<'a>>, Raise> {
    apply_seams_with(segs, store, &mut Probe::default())
}

fn apply_seams_with<'a>(
    segs: Vec<Segment<'a>>,
    store: Option<&'a Store>,
    probe: &mut Probe,
) -> Result<Vec<Segment<'a>>, Raise> {
    let seams = live_seams(store);
    if seams.is_empty() || segs.is_empty() {
        return Ok(segs);
    }
    let mut out = Vec::with_capacity(segs.len());
    for seg in segs {
        let (mut pieces, mut changed) = (vec![seg], true);
        while changed {
            changed = false;
            let mut nxt = Vec::with_capacity(pieces.len() + 1);
            for p in pieces {
                probe.pieces += 1;
                let key = layer1::seg_key(&p.id);
                let mut sm: Option<(&Seam, Num)> = None;
                for &(s, keys) in &seams {
                    let st = s.t.unwrap_or(Num::Int(0));
                    if !(keys.iter().any(|k| *k == *key) && p.t.py_lt(st) && st.py_lt(p.end)) {
                        continue;
                    }
                    // min's key, s["t"], runs on every hit
                    let st = s.t.ok_or_else(|| Raise::key_error("t"))?;
                    if sm.is_none_or(|(_, cur)| st.py_lt(cur)) {
                        sm = Some((s, st));
                    }
                }
                let sp = sm.and_then(|(s, st)| {
                    probe.split_calls += 1;
                    split_with(&p, st, probe.plant).map(|pair| (pair, s))
                });
                match sp {
                    Some(((head, mut tail), s)) => {
                        probe.splits += 1;
                        tail.seam_of = Some(SeamOf {
                            top: s.top.as_ref().and_then(Option::as_deref),
                            text: match &s.text {
                                None => Some(""),
                                Some(t) => t.as_deref(),
                            },
                        });
                        nxt.push(head);
                        nxt.push(tail);
                        changed = true;
                    }
                    None => nxt.push(p),
                }
            }
            pieces = nxt;
        }
        out.extend(pieces);
    }
    Ok(out)
}

/// kernel._segs_seam(turn, store): `jd.apply_seams(em.segments(_turn_sans_placed_echoes(turn)), store or {})`.
pub fn segs_seam<'a>(turn: &'a Turn, store: Option<&'a Store>) -> Result<Vec<Segment<'a>>, Raise> {
    segs_seam_with(turn, store, &mut Probe::default())
}

/// `segs_seam`, counting into `probe` (and making its planted mistake, if any).
pub fn segs_seam_with<'a>(
    turn: &'a Turn,
    store: Option<&'a Store>,
    probe: &mut Probe,
) -> Result<Vec<Segment<'a>>, Raise> {
    let atoms = turn_sans_placed_echoes(turn);
    let segs = segments_of(turn, &atoms, probe)?;
    apply_seams_with(segs, store, probe)
}

/// `em.split_segment(em.segments(turn)[0], t)`, the edge set's direct split cases, counted and planted as
/// `segs_seam_with` is.
pub fn split_first_with<'a>(
    turn: &'a Turn,
    t: Num,
    probe: &mut Probe,
) -> Result<Option<(Segment<'a>, Segment<'a>)>, Raise> {
    let atoms: Vec<&Atom> = turn.atoms.iter().flatten().collect();
    let segs = segments_of(turn, &atoms, probe)?;
    probe.split_calls += 1;
    let sp = split_with(&segs[0], t, probe.plant);
    probe.splits += usize::from(sp.is_some());
    Ok(sp)
}

/// A turn's answer as tools/rust-spike/dump_layer2.py writes Python's: the segments, or the exception raised.
pub fn answer_json(got: &Result<Vec<Segment<'_>>, Raise>, index: &HashMap<*const Atom, usize>) -> Out {
    match got {
        Ok(segs) => Out::Array(segs.iter().map(|s| s.to_json(index)).collect()),
        Err(r) => r.to_json(),
    }
}

/// A direct split's answer: null, `[head, tail]`, or the exception raised.
pub fn split_answer_json(
    got: &Result<Option<(Segment<'_>, Segment<'_>)>, Raise>,
    index: &HashMap<*const Atom, usize>,
) -> Out {
    match got {
        Ok(None) => Out::Null,
        Ok(Some((h, t))) => Out::Array(vec![h.to_json(index), t.to_json(index)]),
        Err(r) => r.to_json(),
    }
}

/// Each atom of a turn by address, to its index in the turn's `atoms`.
pub fn atom_index(turn: &Turn) -> HashMap<*const Atom, usize> {
    turn.atoms.iter().flatten().enumerate().map(|(i, a)| (a as *const Atom, i)).collect()
}

/// Whether some seam the store holds lists this segment's key (the coverage count `keyHits`).
pub fn key_listed(seg: &Segment<'_>, store: Option<&Store>) -> bool {
    let key = layer1::seg_key(&seg.id);
    live_seams(store).iter().any(|(_, keys)| keys.iter().any(|k| *k == *key))
}

impl Segment<'_> {
    /// The segment as tools/rust-spike/dump_layer2.py writes Python's: its keys in the dict's insertion order, with
    /// `atoms` as the atoms' uuids, then `atomIdx`, each atom's index in the turn's `atoms` (uuids can be null).
    pub fn to_json(&self, index: &HashMap<*const Atom, usize>) -> Out {
        let s = |x: Option<&str>| x.map_or(Out::Null, |x| Out::Str(x.to_string()));
        let mut m = vec![
            ("t".to_string(), self.t.to_json()),
            ("end".to_string(), self.end.to_json()),
            ("trigger".to_string(), s(self.trigger)),
            ("atoms".to_string(), Out::Array(self.atoms.iter().map(|a| s(a.uuid())).collect())),
        ];
        if self.seam {
            m.push(("seam".to_string(), Out::Bool(true)));
        }
        m.push(("id".to_string(), Out::Str(self.id.clone())));
        if let Some(so) = &self.seam_of {
            let pair = vec![("top".to_string(), s(so.top)), ("text".to_string(), s(so.text))];
            m.push(("seamOf".to_string(), Out::Object(pair)));
        }
        let idx = self.atoms.iter().map(|a| {
            let i = index.get(&(*a as *const Atom)).expect("a segment's atom is one of its turn's");
            Out::Int(i64::try_from(*i).expect("an index fits in i64"))
        });
        m.push(("atomIdx".to_string(), Out::Array(idx.collect())));
        Out::Object(m)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn turn(json: &str) -> Turn {
        let t: Turn = serde_json::from_str(json).unwrap();
        crate::tree::validate_turn(&t).unwrap();
        t
    }

    fn store(json: &str) -> Store {
        serde_json::from_str(json).unwrap()
    }

    const SID: &str = "s-1";

    fn atom(kind: &str, uuid: &str, t: &str, author: &str, block: &str) -> String {
        format!(r#"{{"type": "{kind}", "uuid": "{uuid}", "t": {t}, "session_id": "{SID}", {author}"message": {{"content": [{block}]}}}}"#)
    }

    fn user(uuid: &str, t: &str, text: &str) -> String {
        atom("user", uuid, t, r#""author": "human", "#, &format!(r#"{{"type": "text", "text": "{text}"}}"#))
    }

    fn asst(uuid: &str, t: &str, text: &str) -> String {
        atom("assistant", uuid, t, "", &format!(r#"{{"type": "text", "text": "{text}"}}"#))
    }

    fn tool(uuid: &str, t: &str) -> String {
        atom("assistant", uuid, t, "", r#"{"type": "tool_use", "id": "x", "name": "Bash"}"#)
    }

    fn turn_of(atoms: &[String], t: &str, end: &str) -> Turn {
        turn(&format!(r#"{{"atoms": [{}], "t": {t}, "end": {end}, "trigger": {{"uuid": "u1"}}}}"#, atoms.join(", ")))
    }

    #[test]
    fn one_segment_per_input_with_ids_from_the_trigger_text() {
        let atoms = [user("u1", "100", "fix it"), asst("a1", "101", "ok"), user("u2", "110", "and this")];
        let t = turn_of(&atoms, "100", "120");
        let segs = segments(&t).unwrap();
        assert_eq!(segs.len(), 2);
        assert_eq!((segs[0].t, segs[0].end, segs[1].end), (Num::Int(100), Num::Int(110), Num::Int(120)));
        assert_eq!(segs[0].id, format!("{SID}:100:{}", sha1::hex8(b"fix it")));
        assert_eq!(segs[1].trigger, Some("u2"));
    }

    #[test]
    fn leading_atoms_attach_at_the_turns_time_and_a_float_time_truncates_in_the_id() {
        let t = turn_of(&[asst("a0", "100.9", "lead"), user("u1", "105", "go")], "-3.7", "120");
        let segs = segments(&t).unwrap();
        assert_eq!(segs.len(), 1);
        assert_eq!(segs[0].t, Num::Float(-3.7));
        assert_eq!(segs[0].atoms.len(), 2);
        assert!(segs[0].id.starts_with(&format!("{SID}:-3:")), "{}", segs[0].id);
    }

    #[test]
    fn a_seam_inside_the_span_splits_off_the_real_work() {
        let atoms =
            [user("u1", "100", "fix it"), asst("a1", "101", "ok"), tool("a2", "110"), asst("a3", "115", "done")];
        let t = turn_of(&atoms, "100", "120");
        let key = layer1::seg_key(&segments(&t).unwrap()[0].id).into_owned();
        let st = store(&format!(r#"{{"seams": [{{"t": 105, "top": "s-1:g1", "segs": ["{key}"]}}]}}"#));
        let mut probe = Probe::default();
        let segs = segs_seam_with(&t, Some(&st), &mut probe).unwrap();
        assert_eq!(segs.len(), 2);
        assert_eq!(segs[0].end, Num::Int(110));
        assert_eq!((segs[1].t, segs[1].trigger, segs[1].seam), (Num::Int(110), None, true));
        assert_eq!(segs[1].seam_of, Some(SeamOf { top: Some("s-1:g1"), text: Some("") }), "an absent text is \"\"");
        // the head and the tail are examined again; the head re-hits its seam and that split finds no tail
        assert_eq!((probe.pieces, probe.split_calls, probe.splits), (3, 2, 1));
        // the tail's key, sha1 of its first atom's uuid (it has no text)
        assert_eq!(segs[1].id, format!("{SID}:110:{}", sha1::hex8(b"a2")));
    }

    #[test]
    fn the_seam_bounds_are_strict_and_an_atom_at_the_seam_time_stays_in_the_head() {
        let t = turn_of(&[user("u1", "100", "fix it"), tool("a2", "110"), tool("a3", "115")], "100", "120");
        let key = layer1::seg_key(&segments(&t).unwrap()[0].id).into_owned();
        for (seam_t, parts) in [("100", 1), ("120", 1), ("110.0", 2), ("109.5", 2)] {
            let st = store(&format!(r#"{{"seams": [{{"t": {seam_t}, "text": null, "segs": ["{key}"]}}]}}"#));
            let segs = segs_seam(&t, Some(&st)).unwrap();
            assert_eq!(segs.len(), parts, "seam at {seam_t}");
            if parts == 2 {
                let tail_first = if seam_t == "110.0" { "a3" } else { "a2" };
                assert_eq!(segs[1].atoms[0].uuid(), Some(tail_first), "seam at {seam_t}");
                assert_eq!(segs[1].seam_of.unwrap().text, None, "a null text stays null");
            }
        }
    }

    #[test]
    fn the_prose_floor_is_80_code_points_of_the_stripped_text() {
        let pad = |n: usize| "\u{e9}".repeat(n); // 2 bytes each
        for (text, real) in [(pad(79), false), (pad(80), true), (format!("  {}  ", pad(79)), false)] {
            let a: Atom = serde_json::from_str(&asst("a", "1", &text)).unwrap();
            assert_eq!(seam_real_work(&[&a]), real, "{} code points", pystr::len(&text));
            assert!(real_work(&[&a], Some(Plant::FloorInBytes)), "bytes clear the floor");
        }
    }

    #[test]
    fn a_hit_seam_without_t_raises_as_min_does() {
        let t = turn_of(&[user("u1", "-10", "fix it"), tool("a2", "5")], "-10", "20");
        let key = layer1::seg_key(&segments(&t).unwrap()[0].id).into_owned();
        let st = store(&format!(r#"{{"seams": [{{"segs": ["{key}"]}}]}}"#));
        assert_eq!(segs_seam(&t, Some(&st)), Err(Raise::key_error("t")));
        let missing = turn(r#"{"atoms": [{"type": "assistant", "t": 1}], "t": 1, "end": 2, "trigger": null}"#);
        assert_eq!(segments(&missing), Err(Raise::key_error("session_id")));
        let no_trigger = turn(r#"{"atoms": [], "t": 1, "end": 2}"#);
        assert_eq!(segments(&no_trigger), Err(Raise::key_error("trigger")));
    }
}
