//! Layer 5 of rust/notes/s2-design.md section 2, "Landing, delegation and provisional", over the typed tree, the store
//! and layers 1 to 4. Each function follows its Python line for line:
//! - kernel._summary_text_anchor with _text_atoms, _opening_sentence and jd._locate_quote, and the unlisted
//!   _summary_outrun (the cited tier's freshness test);
//! - kernel._dictated_prompt_uuid with jd._human_prompt_record (and its em.postal_pairs and jd.NUDGE_MARKER_RE), and
//!   _pure_delegation_top;
//! - kernel._provisional_card with _seg_placed, _seg_caption, _seg_prompt and _split_reminders, the unlisted
//!   _live_replanned, _live_cleared_under and _card_gone, the comment strip it runs inline, and jd._seg_human,
//!   _seg_command, the unlisted _seg_command_worked, _seg_slash_shaped (BARE_SLASH_RE), _seg_followup (FOLLOWUP_RE)
//!   and _seg_system (ROMP_SYSTEM_RE).
//!
//! Every regex is matched by hand, as Python's engine matches it: the leftmost match, lazy groups as short as they can
//! be, and Python's classes (`pystr::is_space` for `\s`, `pyre` for `\d`, `\w` and `re.I`). What a naive port would
//! get wrong, each pinned by the edge cases (tools/rust-spike/dump_layer5.py --edge-cases) with a source mutant that
//! the edge set catches:
//! - jd._locate_quote's second try is a regex of the quote's words joined by `\s+` under `re.I`, which folds one code
//!   point at a time with sre's table (`pyre::ignorecase_eq`): the long s matches `s`, the dotless ı `i`, the final ς
//!   `σ`, the micro sign `μ`, `İ` `i`. A comparison of lowercased characters gets each wrong.
//! - Lengths and slices count code points: `para[12:]`, `sent[:200]`, `span[:300]`, `prompt[:140]`, a seam text's
//!   `[:100]`, and the offset _locate_quote returns.
//! - _opening_sentence splits paragraphs at `\n\s*\n` (a run of Unicode whitespace holding two newlines), drops a list
//!   marker whose digits are any Unicode decimal (`١.` is one, `².` is not), and cuts at `[.!?]` before Unicode
//!   whitespace or the end, past the first 12 code points.
//! - _summary_text_anchor searches jd._atom_text, which reads a bare-string content as "", while _text_atoms admits
//!   that atom through em.atom_has_text; the turn's other text atoms exclude the segment's by identity, not by uuid.
//! - _summary_outrun compares with `int()` of the coverage stamps, so a fractional distilledMt is truncated first.
//! - The segment predicates find the trigger atom by `uuid == seg["trigger"]`, so with no trigger the first atom
//!   without a uuid is the trigger, before `atoms[0]`; _seg_prompt instead searches only for a truthy trigger.
//! - em.postal_pairs is a findall: a `kind` marker whose lazy value runs over an `id` marker consumes it, and then no
//!   pair is made.
//! - _provisional_card reads `_captions(fsid)` before it looks at whether the segment is a system notice, so an
//!   unrecorded caption read fails even where the caption is never shown; `pre + g` raises TypeError on a truthy
//!   caption that is not a string; the turn is open only if no atom anywhere in it is an idle span.
//!
//! The summary-anchor memo (_SUMMARY_ANCHOR_MEMO) and _pure_delegation_top's per-derivation memo are left out.
//! _pure_delegation_top's memo lives for one derivation, where a hit returns what a miss computes; its children index
//! and _dictated_prompt_uuid's prompt index are built per call here (`PromptIndex` is built once per session, as the
//! memo does). _SUMMARY_ANCHOR_MEMO returns what a miss computes only within one build, or across builds of the same
//! parse and seams: its key, `(fsid, nid, seg key, hash(line), the turn's end or t, stub_ok)`, leaves out the
//! segment's atoms, and only eviction clears it. A seam stamped between two builds keeps the head segment's key and
//! the turn's end, so the second build's hit serves the landing found over the whole unsplit segment, while this port
//! (no memo) searches the head's own atoms first and can land on an older atom. The spike compares fresh processes, so
//! it never meets this; a warm build after a seam can (SPIKE.md, "What layer 6 needs"). Body reads that
//! fault (a lazy atom's unreadable body) cannot happen on a tree the loader admits (it refuses lazy atoms), so the
//! fault count is always 0. `str(a.get("text") or "")` is ported for strings and falsy values; the loader refuses a
//! truthy `text` that is not a string (`tree::validate_atom`).

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use romp_json::Value as Out;

use crate::layer1;
use crate::layer2::{self, Raise, Segment};
use crate::layer3::{self, FeedSegWalk, SegMaps, SegTurn};
use crate::layer4::{self, Children, Healed, Loops, Ns};
use crate::pyre;
use crate::pystr;
use crate::record::{py_truthy, Num, OMap, ToJson};
use crate::snapshot::{self, CaptionRow, Node, Store};
use crate::tree::{Atom, Author, Content, Tree, Turn};

/// jd.CITE_MIN_CHARS.
pub const CITE_MIN_CHARS: usize = layer1::CITE_MIN_CHARS;
/// em._POSTAL_KINDS: the kinds a `romp-msg-kind` marker may bind to the id before it.
pub const POSTAL_KINDS: [&str; 3] = ["delegate", "coordinate", "question"];
/// What the provisional card says for a kernel resume notice.
pub const RESUMING: &str = "Resuming work after a restart";
/// The exception the port returns where Python's replay would read captions it never recorded.
pub const NOT_RECORDED: &str = "NotRecorded";

/// One deliberate mistake, for `romp-spike layer5 --plant`: the comparison must catch it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plant {
    /// jd._locate_quote's `re.I` compares Rust's lowercase of each character instead of sre's fold.
    Lowercase,
    /// _summary_text_anchor's quote search reads em._text_of (a bare string counts) instead of jd._atom_text.
    EmText,
    /// The turn's other text atoms exclude the segment's by uuid, not by identity.
    UuidExclude,
    /// _summary_outrun compares with the coverage stamps as they are, not their `int()`.
    OutrunFloat,
    /// The provisional card's `prompt[:140]` counts UTF-8 bytes.
    PromptBytes,
    /// The provisional card's turn is open unless its last atom is an idle span (any idle atom closes it in Python).
    IdleTail,
}

impl Plant {
    pub const ALL: [Plant; 6] =
        [Plant::Lowercase, Plant::EmText, Plant::UuidExclude, Plant::OutrunFloat, Plant::PromptBytes, Plant::IdleTail];

    pub fn parse(name: &str) -> Option<Plant> {
        Plant::ALL.into_iter().find(|p| p.name() == name)
    }

    pub fn name(self) -> &'static str {
        match self {
            Plant::Lowercase => "lowercase",
            Plant::EmText => "em-text",
            Plant::UuidExclude => "uuid-exclude",
            Plant::OutrunFloat => "outrun-float",
            Plant::PromptBytes => "prompt-bytes",
            Plant::IdleTail => "idle-tail",
        }
    }
}

// ───────────────────────────── small Python readings ─────────────────────────────

fn truthy_str(s: Option<&str>) -> bool {
    s.is_some_and(|s| !s.is_empty())
}

fn flag(x: &Option<bool>) -> bool {
    *x == Some(true)
}

fn s2(x: &Option<Option<String>>) -> Option<&str> {
    x.as_ref().and_then(|v| v.as_deref())
}

fn is_api_error(a: &Atom) -> bool {
    matches!(a.is_api_error, Some(Some(true)))
}

fn is_human(a: &Atom) -> bool {
    matches!(&a.author, Some(Some(Author::Name(n))) if n == "human")
}

fn command_truthy(a: &Atom) -> bool {
    a.command.as_ref().is_some_and(py_truthy)
}

/// `s[:n]` in code points.
fn head(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map_or(s, |(i, _)| &s[..i])
}

fn rstrip(s: &str) -> &str {
    s.trim_end_matches(pystr::is_space)
}

fn lstrip(s: &str) -> &str {
    s.trim_start_matches(pystr::is_space)
}

/// The byte index past the whitespace run starting at byte `i`.
fn skip_ws(s: &str, i: usize) -> usize {
    s.len() - pystr::skip_space(&s[i..]).len()
}

/// Python's type name of a JSON value (for a TypeError's message).
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

/// `(a.get("message") or {})`: the message when it is a non-empty dict.
fn message_or_empty(a: &Atom) -> Option<&crate::tree::Message> {
    a.message().filter(|m| m.truthy())
}

/// `str(a.get("text") or "")`: a string as is, any falsy value "" (the loader refuses a truthy non-string).
fn text_field(a: &Atom) -> &str {
    match &a.text {
        Some(serde_json::Value::String(s)) => s,
        _ => "",
    }
}

// ───────────────────────────── the regexes, by hand ─────────────────────────────

/// `re.search(r"<!--\s*WORD\s*-->", text)` for a WORD that starts and ends with a non-space: ROMP_SYSTEM_RE
/// (`romp-system`) and NUDGE_MARKER_RE (`romp-injected`, layer 1's ROMP_INJECT_RE).
pub fn comment_marker(text: &str, word: &str) -> bool {
    text.match_indices("<!--").any(|(i, _)| {
        pystr::skip_space(&text[i + 4..])
            .strip_prefix(word)
            .is_some_and(|rest| pystr::skip_space(rest).starts_with("-->"))
    })
}

/// At byte `j`: the shortest non-empty run of characters none of which `stop` takes, followed by `\s*-->`; returns
/// the run's end and the match's end. A lazy `(X+?)\s*-->` group.
fn lazy_to_close(text: &str, j: usize, stop: impl Fn(char) -> bool) -> Option<(usize, usize)> {
    let mut k = j;
    for c in text[j..].chars() {
        if stop(c) {
            return None;
        }
        k += c.len_utf8();
        let r = skip_ws(text, k);
        if text[r..].starts_with("-->") {
            return Some((k, r + 3));
        }
    }
    None
}

/// FOLLOWUP_RE.search(text) (`<!--\s*romp-goal-id:\s*([^\s>]+?)\s*-->`): the first group of the leftmost match.
pub fn followup_search(text: &str) -> Option<&str> {
    for (i, _) in text.match_indices("<!--") {
        let j = skip_ws(text, i + 4);
        let Some(rest) = text[j..].strip_prefix("romp-goal-id:") else { continue };
        let v = skip_ws(text, text.len() - rest.len());
        if let Some((end, _)) = lazy_to_close(text, v, |c| pystr::is_space(c) || c == '>') {
            return Some(&text[v..end]);
        }
    }
    None
}

/// em.postal_pairs(text): `[(mid, kind)]` from the findall of `<!--\s*romp-msg-(id|kind):\s*(\S+?)\s*-->`, matches
/// taken left to right without overlap; a kind binds to the id just before it when that id has none yet.
pub fn postal_pairs(text: &str) -> Vec<(String, String)> {
    let mut pairs: Vec<(String, String)> = Vec::new();
    let mut i = 0;
    while let Some(off) = text[i..].find("<!--") {
        let at = i + off;
        let mut hit = None;
        let j = skip_ws(text, at + 4);
        if let Some(rest) = text[j..].strip_prefix("romp-msg-") {
            let (typ, rest) = if let Some(r) = rest.strip_prefix("id") {
                ("id", r)
            } else if let Some(r) = rest.strip_prefix("kind") {
                ("kind", r)
            } else {
                ("", rest)
            };
            if !typ.is_empty()
                && let Some(r) = rest.strip_prefix(':')
            {
                let v = skip_ws(text, text.len() - r.len());
                if let Some((end, close)) = lazy_to_close(text, v, pystr::is_space) {
                    hit = Some((typ, &text[v..end], close));
                }
            }
        }
        match hit {
            Some((typ, val, close)) => {
                if typ == "id" {
                    pairs.push((val.to_string(), String::new()));
                } else if let Some(last) = pairs.last_mut()
                    && last.1.is_empty()
                    && POSTAL_KINDS.contains(&val)
                {
                    last.1 = val.to_string();
                }
                i = close;
            }
            None => i = at + 1,
        }
    }
    pairs
}

/// BARE_SLASH_RE.match(s) (`^/[A-Za-z][\w:-]*(?:[ \t][^\n]*)?$`) on a stripped text: one line, a slash, an ASCII letter,
/// Python word characters, colons and hyphens, then nothing or a space or tab and the rest of the line. `$` also
/// matches before a final newline, which a stripped text never has.
pub fn bare_slash(s: &str) -> bool {
    let mut it = s.chars();
    if it.next() != Some('/') || !it.next().is_some_and(|c| c.is_ascii_alphabetic()) {
        return false;
    }
    let rest = it.as_str();
    let tail = rest.trim_start_matches(|c: char| pyre::is_word(c) || c == ':' || c == '-');
    match tail.chars().next() {
        None => true,
        Some(' ' | '\t') => !tail.contains('\n') || (tail.ends_with('\n') && !tail[..tail.len() - 1].contains('\n')),
        Some('\n') => tail.len() == 1,
        Some(_) => false,
    }
}

/// `re.sub(r"<!--.*?-->", "", s, flags=re.S)`: each `<!--` with the first `-->` after it (not overlapping it) removed,
/// scanning on after the removed span. A `<!--` with no `-->` after it stays, and so does everything after it.
pub fn strip_comments(s: &str) -> Cow<'_, str> {
    if !s.contains("<!--") {
        return Cow::Borrowed(s);
    }
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    loop {
        let Some(off) = s[i..].find("<!--") else { break };
        let at = i + off;
        let Some(e) = s[at + 4..].find("-->") else { break };
        out.push_str(&s[i..at]);
        i = at + 4 + e + 3;
    }
    out.push_str(&s[i..]);
    Cow::Owned(out)
}

// ───────────────────────────── the summary landing ─────────────────────────────

/// kernel._text_atoms(atoms): the assistant atoms with a truthy uuid, no API error, text (em.atom_has_text: a bare
/// string counts) and not the settle reply.
pub fn text_atoms<'a>(atoms: &[&'a Atom]) -> Vec<&'a Atom> {
    atoms
        .iter()
        .copied()
        .filter(|a| {
            a.kind() == "assistant"
                && truthy_str(a.uuid())
                && !is_api_error(a)
                && layer1::atom_has_text(a)
                && !layer1::atom_is_settle(a)
        })
        .collect()
}

/// The pieces of `re.split(r"\n\s*\n", s)`: each separator runs from a newline through the last newline of the
/// whitespace run after it.
fn paragraphs(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let (mut start, mut i) = (0, 0);
    while let Some(off) = s[i..].find('\n') {
        let at = i + off;
        let mut last_nl = None;
        let mut j = at + 1;
        for c in s[at + 1..].chars() {
            if !pystr::is_space(c) {
                break;
            }
            if c == '\n' {
                last_nl = Some(j);
            }
            j += c.len_utf8();
        }
        match last_nl {
            Some(e) => {
                out.push(&s[start..at]);
                start = e + 1;
                i = e + 1;
            }
            None => i = at + 1,
        }
    }
    out.push(&s[start..]);
    out
}

/// `re.sub(r"^\s*(?:\d+[.)]|[-*])\s+", "", para)`: a leading list marker (Unicode decimal digits and `.` or `)`, or a
/// `-` or `*`) and the whitespace after it dropped, when that whitespace is there.
fn drop_list_marker(para: &str) -> &str {
    let s = pystr::skip_space(para);
    let digits = s.len() - s.trim_start_matches(pyre::is_decimal).len();
    let after = if digits > 0 {
        match s[digits..].strip_prefix(['.', ')']) {
            Some(r) => r,
            None => return para,
        }
    } else {
        match s.strip_prefix(['-', '*']) {
            Some(r) => r,
            None => return para,
        }
    };
    let rest = pystr::skip_space(after);
    if rest.len() == after.len() { para } else { rest }
}

/// kernel._opening_sentence(line): the first non-empty paragraph, its list marker dropped, its first line, cut after
/// the first `.`, `!` or `?` past 12 code points that whitespace or the end follows, at most 200 code points, stripped.
pub fn opening_sentence(line: Option<&str>) -> String {
    let text = line.unwrap_or("");
    let para = paragraphs(text).into_iter().map(pystr::strip).find(|p| !p.is_empty()).unwrap_or("");
    let para = drop_list_marker(para);
    let para = pystr::strip(para.split('\n').next().unwrap_or(""));
    // re.search(r"[.!?](?=\s|$)", para[12:]): para holds no newline, so `$` is its end
    let chars: Vec<(usize, char)> = para.char_indices().collect();
    let cut = (12..chars.len())
        .find(|&i| matches!(chars[i].1, '.' | '!' | '?') && chars.get(i + 1).is_none_or(|&(_, c)| pystr::is_space(c)))
        .map(|i| chars[i].0 + 1);
    let sent = cut.map_or(para, |e| &para[..e]);
    pystr::strip(head(sent, 200)).to_string()
}

fn locate_with(atom_text: Option<&str>, quote: Option<&str>, plant: Option<Plant>) -> Option<(usize, String)> {
    let at = atom_text.unwrap_or("");
    let q = pystr::strip(quote.unwrap_or(""));
    if at.is_empty() || q.is_empty() {
        return None;
    }
    if let Some(i) = at.find(q) {
        return Some((at[..i].chars().count(), q.to_string()));
    }
    let words: Vec<Vec<char>> = q.split(pystr::is_space).filter(|w| !w.is_empty()).map(|w| w.chars().collect()).collect();
    let chars: Vec<char> = at.chars().collect();
    let eq = |p: char, s: char| match plant {
        Some(Plant::Lowercase) => p.to_lowercase().eq(s.to_lowercase()),
        _ => pyre::ignorecase_eq(p, s),
    };
    let match_at = |start: usize| -> Option<usize> {
        let mut i = start;
        for (wi, w) in words.iter().enumerate() {
            if wi > 0 {
                let j = i;
                while i < chars.len() && pystr::is_space(chars[i]) {
                    i += 1;
                }
                if i == j {
                    return None;
                }
            }
            if i + w.len() > chars.len() || !w.iter().zip(&chars[i..]).all(|(&p, &s)| eq(p, s)) {
                return None;
            }
            i += w.len();
        }
        Some(i)
    };
    (0..chars.len()).find_map(|s| match_at(s).map(|e| (s, chars[s..e].iter().collect())))
}

/// jd._locate_quote(atom_text, quote): (offset in code points, span), the stripped quote found exactly, else its words
/// joined by `\s+` under `re.I` (the span is the atom's own text there); None when either side is empty or nothing
/// matches.
pub fn locate_quote(atom_text: Option<&str>, quote: Option<&str>) -> Option<(usize, String)> {
    locate_with(atom_text, quote, None)
}

/// kernel._summary_text_anchor(turn_seg, line, memo_key=None, stub_ok): (uuid, quote) of where a brief or summary
/// lands, the memo left out (module comment).
pub fn summary_text_anchor<'a>(
    turn_seg: Option<&SegTurn<'a>>,
    line: Option<&str>,
    stub_ok: bool,
    plant: Option<Plant>,
) -> (Option<&'a str>, Option<String>) {
    let Some(ts) = turn_seg else { return (None, None) };
    let seg_texts = text_atoms(&ts.atoms);
    let turn_atoms: Vec<&'a Atom> = ts.turn.atoms.iter().flatten().collect();
    let turn_texts: Vec<&'a Atom> = text_atoms(&turn_atoms)
        .into_iter()
        .filter(|a| match plant {
            Some(Plant::UuidExclude) => !seg_texts.iter().any(|s| s.uuid() == a.uuid()),
            _ => !seg_texts.iter().any(|s| std::ptr::eq(*s, *a)),
        })
        .collect();
    let opening = opening_sentence(line);
    let mut out: (Option<&'a str>, Option<String>) = (None, None);
    if !opening.is_empty() {
        'tiers: for cands in [&seg_texts, &turn_texts] {
            for a in cands.iter().rev() {
                let text = match plant {
                    Some(Plant::EmText) => layer1::atom_text_of(a),
                    _ => layer1::jd_atom_text(a),
                };
                if let Some((_off, span)) = locate_with(Some(&text), Some(&opening), plant) {
                    let q = head(&span, 300);
                    out = (a.uuid(), if q.is_empty() { None } else { Some(q.to_string()) });
                    break 'tiers;
                }
            }
        }
    }
    if !truthy_str(out.0) {
        for cands in [&seg_texts, &turn_texts] {
            if let Some(a) = cands.iter().rev().find(|a| layer1::kernel_atom_prose_chars(a) >= CITE_MIN_CHARS) {
                out = (a.uuid(), None);
                break;
            }
        }
    }
    if !truthy_str(out.0) && stub_ok {
        let last = if seg_texts.is_empty() { turn_texts.last() } else { seg_texts.last() };
        out = (last.and_then(|a| a.uuid()), None);
    }
    out
}

/// `int(x or 0)` as a number that compares exactly: an int as is, a float truncated toward zero (an int when it fits
/// i64, else the integral float, which compares as the int would), a falsy value 0.
fn py_int_or0(x: Option<Num>, plant: Option<Plant>) -> Num {
    match x.filter(|n| n.truthy()) {
        None => Num::Int(0),
        Some(Num::Int(i)) => Num::Int(i),
        Some(Num::Float(f)) if plant == Some(Plant::OutrunFloat) => Num::Float(f),
        Some(Num::Float(f)) => {
            let t = f.trunc();
            if t.abs() < 9_223_372_036_854_775_808.0 { Num::Int(t as i64) } else { Num::Float(t) }
        }
    }
}

fn outrun_with(nd: &Node, trails: &[Option<&[String]>], seg_t: &dyn Fn(&str) -> Option<Num>, plant: Option<Plant>) -> bool {
    let cov = layer3::py_max(py_int_or0(nd.distilled_mt.flatten(), plant), py_int_or0(nd.briefed_mt.flatten(), plant));
    if !cov.truthy() {
        return false;
    }
    let gt = |x: Option<Num>| x.filter(|n| n.truthy()).unwrap_or(Num::Int(0)).py_cmp(cov).is_gt();
    if gt(nd.followup_at.flatten()) {
        return true;
    }
    trails.iter().flat_map(|tr| tr.unwrap_or_default()).any(|s| gt(seg_t(&layer1::kernel_seg_key(s))))
}

/// kernel._summary_outrun(nd, trails, seg_best): whether the follow-up stamp, or the start of a segment some trail
/// names (`seg_best[key][2]`, through `seg_t`), postdates the summary's coverage, `max(int(distilledMt or 0),
/// int(briefedMt or 0))`; False when that is 0.
pub fn summary_outrun(nd: &Node, trails: &[Option<&[String]>], seg_t: &dyn Fn(&str) -> Option<Num>) -> bool {
    outrun_with(nd, trails, seg_t, None)
}

/// Layer 3's walk as `seg_best`'s start times.
pub fn walk_seg_t<'m>(maps: &'m SegMaps<'_>) -> impl Fn(&str) -> Option<Num> + 'm {
    move |k: &str| maps.segs.get(k).map(|e| e.best.t)
}

// ───────────────────────────── delegation ─────────────────────────────

/// jd._human_prompt_record(a, sender)'s `text` when the atom is a human-dictated prompt record, else None: a human
/// atom that is not the interrupt record (its string content as is, else jd._atom_text, else its `text`), or an
/// attachment with text that carries no postal pair and no nudge marker.
pub fn human_prompt_record(a: &Atom) -> Option<Cow<'_, str>> {
    if is_human(a) {
        if layer1::is_interrupt_record(a) {
            return None;
        }
        return Some(match message_or_empty(a).and_then(|m| m.content.as_ref()) {
            Some(Content::Text(c)) => Cow::Borrowed(c.as_str()),
            _ => {
                let t = layer1::jd_atom_text(a);
                if t.is_empty() { Cow::Borrowed(text_field(a)) } else { t }
            }
        });
    }
    if a.kind() == "attachment" {
        let t = layer1::jd_atom_text(a);
        let txt = if t.is_empty() { Cow::Borrowed(text_field(a)) } else { t };
        if !postal_pairs(&txt).is_empty() || layer1::romp_inject_search(&txt) {
            return None;
        }
        return if pystr::strip(&txt).is_empty() { None } else { Some(txt) };
    }
    None
}

/// _dictated_prompt_uuid's index of a parse: the first atom under each truthy uuid, in turn and atom order.
#[derive(Debug, Default)]
pub struct PromptIndex<'a>(HashMap<&'a str, &'a Atom>);

impl<'a> PromptIndex<'a> {
    pub fn new(tree: Option<&'a Tree>) -> Self {
        let mut recs = HashMap::new();
        for turn in tree.and_then(|t| t.turns.as_deref()).unwrap_or_default() {
            for a in turn.atoms.iter().flatten() {
                if let Some(u) = a.uuid().filter(|u| !u.is_empty()) {
                    recs.entry(u).or_insert(a);
                }
            }
        }
        PromptIndex(recs)
    }
}

/// kernel._dictated_prompt_uuid(sid, path, pu): whether the record `pu` names in the session's parse is a human-dictated
/// prompt (True or False), None when `pu` or the path is falsy or no atom carries `pu`.
pub fn dictated_prompt_uuid(index: &PromptIndex<'_>, has_path: bool, pu: Option<&str>) -> Option<bool> {
    let pu = pu.filter(|p| !p.is_empty())?;
    if !has_path {
        return None;
    }
    index.0.get(pu).map(|a| human_prompt_record(a).is_some())
}

/// `isinstance(nd.get("handoff"), dict)`.
fn is_handoff(nd: &Node) -> bool {
    matches!(nd.handoff, Some(Some(_)))
}

/// kernel._pure_delegation_top(nodes, top_id, sid, path): whether every leaf under `top` (the nodes' own parents, a
/// seen-set walk) is a handoff tracker, unless the top is the dictated ask: a userAsk dict, or a promptUuid on a top
/// with no origin dict whose latch says human, absent or scheduled, or, unlatched, whose record `dictated` does not
/// answer False.
pub fn pure_delegation_top(ns: &Ns<'_>, top: &str, dictated: &mut dyn FnMut(&str) -> Option<bool>) -> bool {
    let root = ns.get(top);
    if !root.is_some_and(is_handoff) {
        if root.is_some_and(|r| matches!(r.user_ask, Some(serde_json::Value::Object(_)))) {
            return false;
        }
        let pu = root.and_then(|r| s2(&r.prompt_uuid)).filter(|p| !p.is_empty());
        if let Some(pu) = pu
            && !root.is_some_and(|r| matches!(r.origin, Some(Some(_))))
        {
            let latch = root.and_then(|r| r.ask_anchor.as_ref()).and_then(serde_json::Value::as_str);
            if matches!(latch, Some("human" | "absent" | "scheduled")) {
                return false;
            }
            if latch != Some("machine") && dictated(pu) != Some(false) {
                return false;
            }
        }
    }
    let children = Children::from_parents(ns);
    let (mut leaves, mut stack, mut seen) = (Vec::new(), vec![top.to_string()], HashSet::new());
    while let Some(x) = stack.pop() {
        if seen.contains(&x) || !ns.contains(&x) {
            continue;
        }
        let kids = children.get(Some(&x));
        if kids.is_empty() {
            leaves.push(x.clone());
        } else {
            stack.extend(kids.iter().cloned());
        }
        seen.insert(x);
    }
    !leaves.is_empty() && leaves.iter().all(|l| ns.get(l).is_some_and(is_handoff))
}

// ───────────────────────────── the segment predicates ─────────────────────────────

/// The trigger lookup every predicate shares: the first atom whose uuid equals the segment's trigger (None matches an
/// atom without one), else the first atom.
fn seg_trig<'a>(seg: &Segment<'a>) -> Option<&'a Atom> {
    seg.atoms.iter().copied().find(|a| a.uuid() == seg.trigger).or_else(|| seg.atoms.first().copied())
}

/// jd._seg_human(seg): the trigger is a human atom that is not the interrupt record.
pub fn seg_human(seg: &Segment<'_>) -> bool {
    seg_trig(seg).is_some_and(|t| is_human(t) && !layer1::is_interrupt_record(t))
}

/// jd._seg_command(seg): the trigger carries a truthy `command`.
pub fn seg_command(seg: &Segment<'_>) -> bool {
    seg_trig(seg).is_some_and(command_truthy)
}

/// jd._seg_command_worked(seg): some assistant atom carries no truthy `command`.
pub fn seg_command_worked(seg: &Segment<'_>) -> bool {
    seg.atoms.iter().any(|a| a.kind() == "assistant" && !command_truthy(a))
}

/// jd._seg_slash_shaped(seg): a human trigger without a command whose stripped jd._atom_text is BARE_SLASH_RE's shape.
pub fn seg_slash_shaped(seg: &Segment<'_>) -> bool {
    match seg_trig(seg) {
        Some(t) if is_human(t) && !command_truthy(t) => bare_slash(pystr::strip(&layer1::jd_atom_text(t))),
        _ => false,
    }
}

/// jd._seg_followup(seg): the goal-node id the trigger's jd._atom_text tags (FOLLOWUP_RE), else None.
pub fn seg_followup(seg: &Segment<'_>) -> Option<String> {
    seg_trig(seg).and_then(|t| followup_search(&layer1::jd_atom_text(t)).map(str::to_string))
}

/// jd._seg_system(seg): the trigger's jd._atom_text carries the romp-system marker.
pub fn seg_system(seg: &Segment<'_>) -> bool {
    seg_trig(seg).is_some_and(|t| comment_marker(&layer1::jd_atom_text(t), "romp-system"))
}

/// kernel._seg_prompt(seg): the request text of the atom whose uuid is the (truthy) trigger, else of the first user
/// atom: its text blocks joined and stripped, a bare string as is (unstripped), else "".
pub fn seg_prompt<'a>(seg: &Segment<'a>) -> Cow<'a, str> {
    let trig = seg.trigger.filter(|t| !t.is_empty());
    let a = trig
        .and_then(|t| seg.atoms.iter().copied().find(|x| x.uuid() == Some(t)))
        .or_else(|| seg.atoms.iter().copied().find(|x| x.kind() == "user"));
    let Some(a) = a else { return Cow::Borrowed("") };
    match message_or_empty(a).and_then(|m| m.content.as_ref()) {
        Some(Content::Text(s)) => Cow::Borrowed(s.as_str()),
        _ => layer1::jd_atom_text(a),
    }
}

/// kernel._split_reminders(text): (the prompt, the reminders) with every `<system-reminder>` and `<task-notification>`
/// block peeled (an unclosed one runs to the end), each seam rejoined with a paragraph break, a newline, a space or
/// nothing by the whitespace around it. A text with neither opener comes back as it was, unstripped.
pub fn split_reminders(text: &str) -> (String, Vec<String>) {
    const WRAPPERS: [(&str, &str); 2] =
        [("<system-reminder>", "</system-reminder>"), ("<task-notification>", "</task-notification>")];
    if !WRAPPERS.iter().any(|(o, _)| text.contains(o)) {
        return (text.to_string(), Vec::new());
    }
    let (mut reminders, mut out, mut i): (Vec<&str>, Vec<&str>, usize) = (Vec::new(), Vec::new(), 0);
    loop {
        let cand = WRAPPERS.iter().filter_map(|&(o, c)| text[i..].find(o).map(|j| (i + j, o, c))).min_by_key(|x| x.0);
        let Some((j, open, close)) = cand else {
            out.push(&text[i..]);
            break;
        };
        out.push(&text[i..j]);
        match text[j..].find(close) {
            None => {
                reminders.push(pystr::strip(&text[j + open.len()..]));
                break;
            }
            Some(k) => {
                let k = j + k;
                reminders.push(pystr::strip(&text[j + open.len()..k]));
                i = k + close.len();
            }
        }
    }
    let mut prompt = out[0].to_string();
    for seg in &out[1..] {
        let (left, right) = (rstrip(&prompt), lstrip(seg));
        let (lws, rws) = (&prompt[left.len()..], &seg[..seg.len() - right.len()]);
        let nl = lws.matches('\n').count().max(rws.matches('\n').count());
        let sep = if nl >= 2 {
            "\n\n"
        } else if nl > 0 {
            "\n"
        } else if !lws.is_empty() || !rws.is_empty() {
            " "
        } else {
            ""
        };
        prompt = format!("{left}{sep}{right}");
    }
    (pystr::strip(&prompt).to_string(), reminders.into_iter().filter(|r| !r.is_empty()).map(str::to_string).collect())
}

/// The prompt _provisional_card shows: `re.sub(r"<!--.*?-->", "", _split_reminders(text)[0], flags=re.S).strip()`.
pub fn clean_prompt(text: &str) -> String {
    pystr::strip(&strip_comments(&split_reminders(text).0)).to_string()
}

// ───────────────────────────── placements and captions ─────────────────────────────

/// A store's placements, `(store or {}).get("placements") or {}`.
pub type Placements = OMap<Option<String>>;

fn has_key(p: &Placements, k: &str) -> bool {
    p.get(k).is_some()
}

/// kernel._seg_placed(placements, seg_id): the id under any phase (bare, `#p`, `#d`, `#live`), exactly, else any
/// placement key whose jd._seg_key is one of theirs (a drifted time). A retired (null) placement counts.
pub fn seg_placed(p: &Placements, seg_id: &str) -> bool {
    let phases = ["", "#p", "#d", "#live"].map(|s| format!("{seg_id}{s}"));
    if phases.iter().any(|k| has_key(p, k)) {
        return true;
    }
    let want: Vec<String> = phases.iter().map(|k| layer1::kernel_seg_key(k).into_owned()).collect();
    p.keys().any(|k| want.iter().any(|w| *w == *layer1::kernel_seg_key(k)))
}

/// kernel._live_replanned(placements, seg_id): the `#live` key exactly, else by jd._seg_key.
pub fn live_replanned(p: &Placements, seg_id: &str) -> bool {
    let live = format!("{seg_id}#live");
    if has_key(p, &live) {
        return true;
    }
    let want = layer1::kernel_seg_key(&live);
    p.keys().any(|k| layer1::kernel_seg_key(k) == want)
}

/// kernel._card_gone(nodes, nid): the node is missing, or it or an ancestor is cleared (a seen-set walk up the parents;
/// a cycle ends it, not gone).
pub fn card_gone(ns: &Ns<'_>, nid: &str) -> bool {
    let mut seen = HashSet::new();
    let mut cur = Some(nid);
    while let Some(n) = cur {
        if !seen.insert(n) {
            break;
        }
        let Some(nd) = ns.get(n) else { return true };
        if flag(&nd.cleared) {
            return true;
        }
        cur = s2(&nd.parent_id);
    }
    false
}

/// kernel._live_cleared_under(placements, nodes, seg_id): not live-replanned, some placement of the segment's family
/// (bare, `#p`, `#d`, by jd._seg_key), none retired, and every one names a gone card.
pub fn live_cleared_under(p: &Placements, ns: &Ns<'_>, seg_id: &str) -> bool {
    if live_replanned(p, seg_id) {
        return false;
    }
    let fam: Vec<String> =
        ["", "#p", "#d"].iter().map(|s| layer1::kernel_seg_key(&format!("{seg_id}{s}")).into_owned()).collect();
    let vals: Vec<&Option<String>> =
        p.iter().filter(|(k, _)| fam.iter().any(|f| *f == *layer1::kernel_seg_key(k))).map(|(_, v)| v).collect();
    if vals.is_empty() || vals.iter().any(|v| v.is_none()) {
        return false;
    }
    vals.iter().all(|v| v.as_deref().is_some_and(|nid| card_gone(ns, nid)))
}

/// kernel._seg_caption(caps, seg_id): the `<seg>#p` row's caption, else the first row (in the rows' order) whose id ends
/// in `#p` and shares its jd._seg_key; `row.get("caption", "")`, so an absent caption is "" and a null one null.
pub fn seg_caption(caps: &OMap<CaptionRow>, seg_id: &str) -> serde_json::Value {
    let id = format!("{seg_id}#p");
    let hit = caps.get(&id).or_else(|| {
        let want = layer1::kernel_seg_key(&id);
        caps.iter().find(|(k, _)| k.ends_with("#p") && layer1::kernel_seg_key(k) == want).map(|(_, v)| v)
    });
    hit.and_then(|row| row.caption.clone()).unwrap_or_else(|| serde_json::Value::String(String::new()))
}

// ───────────────────────────── the provisional card ─────────────────────────────

/// What _provisional_card is handed: the session's sid, its alive row's name, its color (kernel._name_color's
/// answer), whether it is live, its parse, its store, the captions `_captions(fsid)` answered (None: never recorded,
/// and reading them fails loudly) and the build's cleared set (`_cleared_ids_display()`).
pub struct ProvisionalIn<'a> {
    pub fsid: &'a str,
    pub name: Out,
    pub color: Out,
    pub live: bool,
    pub tree: Option<&'a Tree>,
    pub store: Option<&'a Store>,
    pub captions: Option<&'a OMap<CaptionRow>>,
    pub cleared: &'a HashSet<String>,
}

fn obj(members: Vec<(&str, Out)>) -> Out {
    Out::Object(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

/// kernel._provisional_card(s, name, color, fsid, live, now, store): the working placeholder for a live session whose
/// newest segment the planner has not placed, None where Python returns None, or the exception Python raises
/// (segmentation's KeyError, a truthy non-string caption's TypeError, or `NOT_RECORDED` for an unrecorded caption read).
pub fn provisional_card(pi: &ProvisionalIn<'_>, plant: Option<Plant>) -> Result<Option<Out>, Raise> {
    if !pi.live {
        return Ok(None);
    }
    let Some(tree) = pi.tree else { return Ok(None) };
    let turns = tree.turns.as_deref().unwrap_or_default();
    let Some(lt) = turns.last() else { return Ok(None) };
    let segs = layer2::segs_seam(lt, pi.store)?;
    let Some(held) = segs.last() else { return Ok(None) };
    if !seg_human(held) && !held.seam && !seg_system(held) {
        return Ok(None);
    }
    if (seg_command(held) && !seg_command_worked(held)) || seg_slash_shaped(held) {
        return Ok(None);
    }
    let empty_p = Placements::default();
    let empty_n = OMap::default();
    let placements = pi.store.and_then(|s| s.placements.as_ref()).and_then(Option::as_ref).unwrap_or(&empty_p);
    let ns = Ns::new(pi.store.and_then(|s| s.nodes.as_ref()).unwrap_or(&empty_n));
    let atoms = lt.atoms.as_deref().unwrap_or_default();
    let idle = match plant {
        Some(Plant::IdleTail) => atoms.last().is_some_and(|a| a.kind() == "idle"),
        _ => atoms.iter().any(|a| a.kind() == "idle"),
    };
    let turn_open = !flag(&lt.ended) && !idle;
    if let Some(fu) = seg_followup(held) {
        let gone = if ns.contains(&fu) { card_gone(&ns, &fu) } else { pi.cleared.contains(&fu) };
        if !(turn_open && gone) || live_replanned(placements, &held.id) {
            return Ok(None);
        }
    }
    if seg_placed(placements, &held.id) && !(turn_open && live_cleared_under(placements, &ns, &held.id)) {
        return Ok(None);
    }
    let prompt = clean_prompt(&seg_prompt(held));
    let system = seg_system(held);
    if prompt.is_empty() && !held.seam && !system {
        return Ok(None);
    }
    let pre = if turn_open { "Working: " } else { "Analyzing: " };
    let caps = pi.captions.ok_or_else(|| Raise { exc: NOT_RECORDED, arg: format!("_captions({})", pi.fsid) })?;
    let g = seg_caption(caps, &held.id);
    let with_caption = |g: &serde_json::Value| -> Result<String, Raise> {
        match g {
            serde_json::Value::String(s) => Ok(format!("{pre}{s}")),
            other => Err(Raise {
                exc: "TypeError",
                arg: format!("can only concatenate str (not \"{}\") to str", py_type(other)),
            }),
        }
    };
    let text = if system {
        RESUMING.to_string()
    } else if held.seam && prompt.is_empty() {
        if py_truthy(&g) {
            with_caption(&g)?
        } else {
            let past = held.seam_of.and_then(|s| s.text).filter(|t| !t.is_empty()).unwrap_or("a completed goal");
            format!("Working past \u{201c}{}\u{201d}", head(past, 100))
        }
    } else if py_truthy(&g) {
        with_caption(&g)?
    } else if plant == Some(Plant::PromptBytes) {
        let mut n = prompt.len().min(140);
        while !prompt.is_char_boundary(n) {
            n -= 1;
        }
        pystr::strip(&prompt[..n]).to_string()
    } else {
        pystr::strip(head(&prompt, 140)).to_string()
    };
    let t = held.t.to_json();
    Ok(Some(obj(vec![
        ("itemId", Out::Str(format!("provisional:{}", pi.fsid))),
        ("sid", Out::Str(pi.fsid.to_string())),
        ("name", pi.name.clone()),
        ("color", pi.color.clone()),
        ("text", Out::Str(text)),
        ("t", t.clone()),
        ("live", Out::Bool(pi.live)),
        ("_ageT", t),
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
        ("provisional", Out::Bool(true)),
        ("judging", Out::Bool(!turn_open)),
        ("tree", Out::Array(Vec::new())),
    ])))
}

// ───────────────────────────── answers as tools/rust-spike/dump_layer5.py writes Python's ─────────────────────────────

fn s(x: Option<&str>) -> Out {
    x.map_or(Out::Null, |x| Out::Str(x.to_string()))
}

fn int(n: usize) -> Out {
    Out::Int(i64::try_from(n).expect("a count fits in i64"))
}

fn loops() -> Out {
    obj(vec![("loops", Out::Bool(true))])
}

fn opt_bool(b: Option<bool>) -> Out {
    b.map_or(Out::Null, Out::Bool)
}

fn raise_json(r: &Raise) -> Out {
    r.to_json()
}

/// A located quote as Python's tuple: `[offset, span]` or `[null, null]`.
pub fn locate_json(got: Option<(usize, String)>) -> Out {
    match got {
        Some((off, span)) => Out::Array(vec![int(off), Out::Str(span)]),
        None => Out::Array(vec![Out::Null, Out::Null]),
    }
}

fn split_json((prompt, reminders): (String, Vec<String>)) -> Out {
    Out::Array(vec![Out::Str(prompt), Out::Array(reminders.into_iter().map(Out::Str).collect())])
}

fn record_json(a: &Atom, sender: &str) -> Out {
    human_prompt_record(a).map_or(Out::Null, |t| {
        obj(vec![("text", Out::Str(t.into_owned())), ("sid", Out::Str(sender.to_string()))])
    })
}

/// One session as the per-card build sees it, for its layer-5 answer.
pub struct SessionIn<'a> {
    pub sid: &'a str,
    /// `s["path"]` (only its truthiness is read: _dictated_prompt_uuid's `not path`).
    pub has_path: bool,
    pub name: Out,
    pub color: Out,
    pub live: bool,
    pub tree: Option<&'a Tree>,
    pub store: Option<&'a Store>,
    pub captions: Option<&'a OMap<CaptionRow>>,
    pub cleared: &'a HashSet<String>,
    pub healed: &'a Healed,
}

/// One segment's row: `[id, human, seam, system, command, worked, slash, followup, prompt, [split prompt, reminders],
/// clean prompt, placed, replanned, liveCleared, caption, [text atom indices]]`.
fn seg_row(seg: &Segment<'_>, index: &HashMap<*const Atom, usize>, p: &Placements, ns: &Ns<'_>,
           captions: Option<&OMap<CaptionRow>>, sid: &str) -> Out {
    let prompt = seg_prompt(seg);
    let caption = match captions {
        Some(c) => seg_caption(c, &seg.id).to_json(),
        None => raise_json(&Raise { exc: NOT_RECORDED, arg: format!("_captions({sid})") }),
    };
    let texts = text_atoms(&seg.atoms).into_iter().map(|a| int(index[&(a as *const Atom)])).collect();
    Out::Array(vec![
        Out::Str(seg.id.clone()),
        Out::Bool(seg_human(seg)),
        Out::Bool(seg.seam),
        Out::Bool(seg_system(seg)),
        Out::Bool(seg_command(seg)),
        Out::Bool(seg_command_worked(seg)),
        Out::Bool(seg_slash_shaped(seg)),
        s(seg_followup(seg).as_deref()),
        Out::Str(prompt.to_string()),
        split_json(split_reminders(&prompt)),
        Out::Str(clean_prompt(&prompt)),
        Out::Bool(seg_placed(p, &seg.id)),
        Out::Bool(live_replanned(p, &seg.id)),
        Out::Bool(live_cleared_under(p, ns, &seg.id)),
        caption,
        Out::Array(texts),
    ])
}

/// The distinct truthy lines a node's landing reads (`summary`, then `blockSummary`).
fn node_lines(nd: &Node) -> Vec<&str> {
    let mut out: Vec<&str> = Vec::new();
    for l in [s2(&nd.summary), s2(&nd.block_summary)].into_iter().flatten() {
        if !l.is_empty() && !out.contains(&l) {
            out.push(l);
        }
    }
    out
}

/// The seg keys of the trails over `sub`, in subtree then trail order, each once.
fn sub_keys(ns: &Ns<'_>, sub: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for x in sub {
        for s in ns.get(x).and_then(|n| n.trail.as_deref()).unwrap_or_default() {
            let k = layer1::kernel_seg_key(s).into_owned();
            if !out.contains(&k) {
                out.push(k);
            }
        }
    }
    out
}

/// One session's layer-5 answer (dump_layer5.py's `session_answer`): the provisional card (asked whatever the body's
/// gate), each turn's segment rows (or the raise), each atom's _human_prompt_record, and per node in store order
/// _card_gone, _dictated_prompt_uuid over its promptUuid, _pure_delegation_top, and over the body's subtree of it
/// _summary_outrun and every _summary_text_anchor call (each trail key of the subtree, each line, both stub_ok). `Err`
/// when the body's children forest is refused.
pub fn session_json(si: &SessionIn<'_>, plant: Option<Plant>) -> Result<Out, String> {
    let provisional = provisional_card(
        &ProvisionalIn {
            fsid: si.sid,
            name: si.name.clone(),
            color: si.color.clone(),
            live: si.live,
            tree: si.tree,
            store: si.store,
            captions: si.captions,
            cleared: si.cleared,
        },
        plant,
    );
    let provisional = match provisional {
        Ok(None) => Out::Null,
        Ok(Some(card)) => card,
        Err(r) => raise_json(&r),
    };
    let turns: &[Turn] = si.tree.and_then(|t| t.turns.as_deref()).unwrap_or_default();
    let empty_p = Placements::default();
    let empty_n = OMap::default();
    let placements = si.store.and_then(|s| s.placements.as_ref()).and_then(Option::as_ref).unwrap_or(&empty_p);
    let nodes = si.store.and_then(|s| s.nodes.as_ref()).unwrap_or(&empty_n);
    let ns = Ns::new(nodes);
    let mut segs = Vec::with_capacity(turns.len());
    let mut prompts = Vec::with_capacity(turns.len());
    for turn in turns {
        let index = layer2::atom_index(turn);
        segs.push(match layer2::segs_seam(turn, si.store) {
            Err(r) => raise_json(&r),
            Ok(ss) => Out::Array(ss.iter().map(|g| seg_row(g, &index, placements, &ns, si.captions, si.sid)).collect()),
        });
        prompts.push(Out::Array(turn.atoms.iter().flatten().map(|a| record_json(a, si.sid)).collect()));
    }
    let (children, _hidden) = layer4::body_children(&ns, si.healed)?;
    let index = PromptIndex::new(si.tree);
    let mut walk: FeedSegWalk<'_> = layer3::feed_seg_walk(si.tree.map(|_| turns), si.store);
    let mut per = Vec::new();
    for (nid, nd) in ns.iter() {
        let pu = s2(&nd.prompt_uuid);
        let dictated = dictated_prompt_uuid(&index, si.has_path, pu);
        let pure = pure_delegation_top(&ns, nid, &mut |p| dictated_prompt_uuid(&index, si.has_path, Some(p)));
        let (outrun, landing) = match layer4::subtree(&children, nid) {
            Err(Loops) => (loops(), loops()),
            Ok(sub) => {
                let trails: Vec<Option<&[String]>> =
                    sub.iter().map(|x| ns.get(x).and_then(|n| n.trail.as_deref())).collect();
                let outrun = {
                    let seg_t = walk_seg_t(&walk.maps);
                    outrun_with(nd, &trails, &seg_t, plant)
                };
                let mut rows = Vec::new();
                for line in node_lines(nd) {
                    for k in sub_keys(&ns, &sub) {
                        let ts = walk.seg_turn_get(&k);
                        for stub in [false, true] {
                            let (u, q) = summary_text_anchor(ts.as_ref(), Some(line), stub, plant);
                            rows.push(Out::Array(vec![
                                Out::Str(k.clone()),
                                Out::Str(line.to_string()),
                                Out::Bool(stub),
                                Out::Array(vec![s(u), s(q.as_deref())]),
                            ]));
                        }
                    }
                }
                (Out::Bool(outrun), Out::Array(rows))
            }
        };
        per.push(Out::Array(vec![
            Out::Str(nid.to_string()),
            obj(vec![
                ("cardGone", Out::Bool(card_gone(&ns, nid))),
                ("dictated", opt_bool(dictated)),
                ("pure", Out::Bool(pure)),
                ("outrun", outrun),
                ("landing", landing),
            ]),
        ]));
    }
    Ok(obj(vec![
        ("provisional", provisional),
        ("segs", Out::Array(segs)),
        ("prompts", Out::Array(prompts)),
        ("nodes", Out::Array(per)),
        ("faults", int(walk.seg_turn.faults)),
    ]))
}

// ───────────────────────────── the edge set and the fuzz ─────────────────────────────

/// One session of an edge-set or fuzz case, owned.
#[derive(Debug)]
pub struct SessionCase {
    pub sid: String,
    pub path: Option<String>,
    pub name: Out,
    pub color: Out,
    pub live: bool,
    pub tree: Option<Tree>,
    pub store: Option<Store>,
    pub captions: Option<OMap<CaptionRow>>,
    pub cleared: HashSet<String>,
    pub healed: Healed,
}

/// A direct call of one function, as dump_layer5.py writes it (`fn`, `args`).
#[derive(Debug)]
pub enum Call {
    Opening(Option<String>),
    Locate(Option<String>, Option<String>),
    Split(String),
    Postal(String),
    BareSlash(String),
    Followup(String),
    CommentStrip(String),
    Record(Box<Atom>),
    Outrun(Box<Node>, Vec<Option<Vec<String>>>, HashMap<String, Num>),
    CardGone(OMap<Node>, String),
    SegPlaced(Placements, String),
    Replanned(Placements, String),
    LiveCleared(Placements, OMap<Node>, String),
    Caption(OMap<CaptionRow>, String),
    TextAnchor(Box<Turn>, Vec<usize>, Option<String>, bool),
}

/// One case of tools/rust-spike/dump_layer5.py `--edge-cases` or `--fuzz`, loaded through the port's loader.
#[derive(Debug)]
pub enum EdgeCase {
    Session(Box<SessionCase>),
    Call(Call),
}

fn field<T: serde::de::DeserializeOwned>(v: &serde_json::Value, key: &str) -> Result<T, String> {
    serde_json::from_value(v.get(key).cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| format!("{key}: {e}"))
}

fn arg<T: serde::de::DeserializeOwned>(args: &[serde_json::Value], i: usize) -> Result<T, String> {
    serde_json::from_value(args.get(i).cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| format!("args[{i}]: {e}"))
}

fn nodes_value(v: OMap<Node>, path: &str) -> Result<OMap<Node>, String> {
    let st = Store { nodes: Some(v), ..Store::default() };
    snapshot::validate_store(path, &st)?;
    Ok(st.nodes.expect("set above"))
}

fn tree_value(v: &serde_json::Value) -> Result<Option<Tree>, String> {
    if v.is_null() {
        return Ok(None);
    }
    let tree: Tree = serde_json::from_value(v.clone()).map_err(|e| format!("tree: {e}"))?;
    crate::tree::validate(&tree).map_err(|e| format!("tree: {e}"))?;
    Ok(Some(tree))
}

impl SessionCase {
    fn load(v: &serde_json::Value) -> Result<SessionCase, String> {
        let store: Option<Store> = field(v, "store")?;
        if let Some(st) = &store {
            snapshot::validate_store("store", st)?;
        }
        let healed: Healed = match v.get("healed") {
            None | Some(serde_json::Value::Null) => OMap::default(),
            Some(h) => serde_json::from_value(h.clone()).map_err(|e| format!("healed: {e}"))?,
        };
        let cleared: Vec<String> = field::<Option<Vec<String>>>(v, "cleared")?.unwrap_or_default();
        Ok(SessionCase {
            sid: field(v, "sid")?,
            path: field(v, "path")?,
            name: v.get("name").map_or(Out::Null, ToJson::to_json),
            color: v.get("color").map_or(Out::Null, ToJson::to_json),
            live: field(v, "live")?,
            tree: tree_value(v.get("tree").unwrap_or(&serde_json::Value::Null))?,
            store,
            captions: field(v, "captions")?,
            cleared: cleared.into_iter().collect(),
            healed,
        })
    }

    pub fn input(&self) -> SessionIn<'_> {
        SessionIn {
            sid: &self.sid,
            has_path: truthy_str(self.path.as_deref()),
            name: self.name.clone(),
            color: self.color.clone(),
            live: self.live,
            tree: self.tree.as_ref(),
            store: self.store.as_ref(),
            captions: self.captions.as_ref(),
            cleared: &self.cleared,
            healed: &self.healed,
        }
    }
}

/// A case's members that are Python's answers, not its inputs.
pub const ANSWER_KEYS: [&str; 1] = ["expect"];

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
            Some("session") => Ok(EdgeCase::Session(Box::new(SessionCase::load(&case["session"])?))),
            Some("call") => {
                let args = case["args"].as_array().ok_or("args: not a list")?;
                let call = match case["fn"].as_str().unwrap_or("") {
                    "opening_sentence" => Call::Opening(arg(args, 0)?),
                    "locate_quote" => Call::Locate(arg(args, 0)?, arg(args, 1)?),
                    "split_reminders" => Call::Split(arg(args, 0)?),
                    "postal_pairs" => Call::Postal(arg(args, 0)?),
                    "bare_slash" => Call::BareSlash(arg(args, 0)?),
                    "followup_re" => Call::Followup(arg(args, 0)?),
                    "comment_strip" => Call::CommentStrip(arg(args, 0)?),
                    "human_prompt_record" => {
                        let a: Atom = arg(args, 0)?;
                        crate::tree::validate_atom(&a).map_err(|e| format!("args[0]: {e}"))?;
                        Call::Record(Box::new(a))
                    }
                    "summary_outrun" => {
                        let nd: Node = arg(args, 0)?;
                        snapshot::validate_node("args[0]", &nd)?;
                        let best: Vec<(String, (Option<String>, bool, Num))> = arg(args, 2)?;
                        Call::Outrun(Box::new(nd), arg(args, 1)?, best.into_iter().map(|(k, (_, _, t))| (k, t)).collect())
                    }
                    "card_gone" => Call::CardGone(nodes_value(arg(args, 0)?, "args[0]")?, arg(args, 1)?),
                    "seg_placed" => Call::SegPlaced(arg(args, 0)?, arg(args, 1)?),
                    "live_replanned" => Call::Replanned(arg(args, 0)?, arg(args, 1)?),
                    "live_cleared_under" => {
                        Call::LiveCleared(arg(args, 0)?, nodes_value(arg(args, 1)?, "args[1]")?, arg(args, 2)?)
                    }
                    "seg_caption" => Call::Caption(arg(args, 0)?, arg(args, 1)?),
                    "text_anchor" => {
                        let turn: Turn = arg(args, 0)?;
                        crate::tree::validate_turn(&turn).map_err(|e| format!("args[0]{e}"))?;
                        let idx: Vec<usize> = arg(args, 1)?;
                        let n = turn.atoms.as_ref().map_or(0, Vec::len);
                        if idx.iter().any(|&i| i >= n) {
                            return Err("args[1]: an atom index past the turn".into());
                        }
                        Call::TextAnchor(Box::new(turn), idx, arg(args, 2)?, arg(args, 3)?)
                    }
                    f => return Err(format!("unknown fn {f:?}")),
                };
                Ok(EdgeCase::Call(call))
            }
            k => Err(format!("unknown kind {k:?}")),
        }
    }

    /// The port's answer, in the shape dump_layer5.py writes Python's `expect`.
    pub fn answer(&self, plant: Option<Plant>) -> Out {
        match self {
            EdgeCase::Session(sc) => {
                session_json(&sc.input(), plant).unwrap_or_else(|e| obj(vec![("refused", Out::Str(e))]))
            }
            EdgeCase::Call(c) => call_json(c, plant),
        }
    }

    /// Whether the body's children build refuses this case's session (the port answers `{"refused": ...}`).
    pub fn refused_session(&self) -> Option<String> {
        let EdgeCase::Session(sc) = self else { return None };
        let empty = OMap::default();
        let nodes = sc.store.as_ref().and_then(|s| s.nodes.as_ref()).unwrap_or(&empty);
        layer4::body_children(&Ns::new(nodes), &sc.healed).err()
    }
}

fn call_json(c: &Call, plant: Option<Plant>) -> Out {
    match c {
        Call::Opening(l) => Out::Str(opening_sentence(l.as_deref())),
        Call::Locate(t, q) => locate_json(locate_with(t.as_deref(), q.as_deref(), plant)),
        Call::Split(t) => split_json(split_reminders(t)),
        Call::Postal(t) => Out::Array(
            postal_pairs(t).into_iter().map(|(m, k)| Out::Array(vec![Out::Str(m), Out::Str(k)])).collect(),
        ),
        Call::BareSlash(t) => Out::Bool(bare_slash(t)),
        Call::Followup(t) => s(followup_search(t)),
        Call::CommentStrip(t) => Out::Str(strip_comments(t).into_owned()),
        Call::Record(a) => record_json(a, "s"),
        Call::Outrun(nd, trails, best) => {
            let trails: Vec<Option<&[String]>> = trails.iter().map(|t| t.as_deref()).collect();
            Out::Bool(outrun_with(nd, &trails, &|k| best.get(k).copied(), plant))
        }
        Call::CardGone(nodes, nid) => Out::Bool(card_gone(&Ns::new(nodes), nid)),
        Call::SegPlaced(p, id) => Out::Bool(seg_placed(p, id)),
        Call::Replanned(p, id) => Out::Bool(live_replanned(p, id)),
        Call::LiveCleared(p, nodes, id) => Out::Bool(live_cleared_under(p, &Ns::new(nodes), id)),
        Call::Caption(caps, id) => seg_caption(caps, id).to_json(),
        Call::TextAnchor(turn, idx, line, stub) => {
            let all: Vec<&Atom> = turn.atoms.iter().flatten().collect();
            let ts = SegTurn { ti: 0, turn, atoms: idx.iter().map(|&i| all[i]).collect() };
            let (u, q) = summary_text_anchor(Some(&ts), line.as_deref(), *stub, plant);
            Out::Array(vec![s(u), s(q.as_deref())])
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn atom(json: &str) -> Atom {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn the_opening_sentence_cuts_as_python_does() {
        assert_eq!(opening_sentence(None), "");
        assert_eq!(opening_sentence(Some("\n\n  \n1. Fixed the cache. Then the rest.")), "Fixed the cache.");
        assert_eq!(opening_sentence(Some("e.g. this is long enough. More")), "e.g. this is long enough.");
        // a Unicode decimal list marker is dropped, a superscript is not one
        assert_eq!(opening_sentence(Some("\u{661}. abc")), "abc");
        assert_eq!(opening_sentence(Some("\u{b2}. abc")), "\u{b2}. abc");
        // a paragraph break through Unicode whitespace; a single newline is the first line's end
        assert_eq!(opening_sentence(Some("a\n\u{3000}\nb")), "a");
        assert_eq!(opening_sentence(Some("first line\nsecond")), "first line");
        // the stop must come past 12 code points, before whitespace (U+00A0 counts) or the end
        assert_eq!(opening_sentence(Some("Done. Then the next thing.\u{a0}x")), "Done. Then the next thing.");
        assert_eq!(opening_sentence(Some(&"\u{e9}".repeat(250))), "\u{e9}".repeat(200));
    }

    #[test]
    fn the_quote_is_found_exactly_then_folded() {
        assert_eq!(locate_quote(Some("caf\u{e9} hello world"), Some("hello")), Some((5, "hello".into())));
        assert_eq!(locate_quote(Some("say HELLO\n  World now"), Some(" hello world ")),
                   Some((4, "HELLO\n  World".into())));
        assert_eq!(locate_quote(Some("the \u{212a}ey"), Some("key")), Some((4, "\u{212a}ey".into())));
        assert_eq!(locate_quote(Some("ma\u{17f}t"), Some("MAST")), Some((0, "ma\u{17f}t".into())));
        assert_eq!(locate_quote(Some("stra\u{1e9e}e"), Some("stra\u{df}e")), Some((0, "stra\u{1e9e}e".into())));
        assert_eq!(locate_quote(Some("\u{130}nfo \u{3c2}"), Some("info \u{3a3}")), Some((0, "\u{130}nfo \u{3c2}".into())));
        assert_eq!(locate_with(Some("\u{130}nfo"), Some("info"), Some(Plant::Lowercase)), None);
        assert_eq!(locate_quote(Some("axb"), Some("a.b")), None, "the quote is escaped");
        assert_eq!(locate_quote(Some(""), Some("x")), None);
        assert_eq!(locate_quote(Some("x"), Some("  ")), None);
        assert_eq!(locate_with(Some("the \u{212a}ey"), Some("KEY"), Some(Plant::Lowercase)), Some((4, "\u{212a}ey".into())));
        assert_eq!(locate_with(Some("ma\u{17f}t"), Some("MAST"), Some(Plant::Lowercase)), None, "the plant misses it");
    }

    #[test]
    fn regexes_by_hand() {
        assert_eq!(followup_search("x <!-- romp-goal-id: g1 --> y"), Some("g1"));
        assert_eq!(followup_search("<!--romp-goal-id:g1--->"), Some("g1-"));
        assert_eq!(followup_search("<!-- romp-goal-id: a>b --> <!-- romp-goal-id: c -->"), Some("c"));
        assert_eq!(followup_search("romp-goal-id: g1"), None);
        assert_eq!(postal_pairs("<!-- romp-msg-id: m1 --><!-- romp-msg-kind: question -->"),
                   [("m1".to_string(), "question".to_string())]);
        assert!(postal_pairs("<!-- romp-msg-kind: x<!--romp-msg-id:y -->").is_empty(), "the kind ate the id");
        assert!(bare_slash("/compact") && bare_slash("/model opus") && bare_slash("/a\u{b2}") && bare_slash("/x:y-z"));
        assert!(!bare_slash("/Users/x/y.py") && !bare_slash("/a\u{93e}") && !bare_slash("/\u{e9}") && !bare_slash("/1"));
        assert!(!bare_slash("/a b\nc") && bare_slash("/a\tb c"));
        assert_eq!(strip_comments("a<!-- x\n -->b<!-->c"), "ab<!-->c");
        assert_eq!(strip_comments("<!---->x<!--->"), "x<!--->");
        assert!(comment_marker("hi <!--  romp-system\u{1c}-->", "romp-system"));
    }

    #[test]
    fn reminders_peel_and_rejoin() {
        assert_eq!(split_reminders("  plain  "), ("  plain  ".to_string(), vec![]));
        assert_eq!(split_reminders("a\n\n<system-reminder> r </system-reminder>b"),
                   ("a\n\nb".to_string(), vec!["r".to_string()]));
        assert_eq!(split_reminders("a<task-notification>t</task-notification> b<system-reminder>open"),
                   ("a b".to_string(), vec!["t".to_string(), "open".to_string()]));
        assert_eq!(split_reminders("a<system-reminder></system-reminder>b"), ("ab".to_string(), vec![]));
    }

    #[test]
    fn a_human_record_and_an_attachment_record() {
        let h = atom(r#"{"type": "user", "t": 1, "author": "human", "message": {"content": "  hi  "}}"#);
        assert_eq!(human_prompt_record(&h).as_deref(), Some("  hi  "), "a string content as is");
        let i = atom(r#"{"type": "user", "t": 1, "author": "human", "message": {"content": "[Request interrupted by user]"}}"#);
        assert_eq!(human_prompt_record(&i), None);
        let at = atom(r#"{"type": "attachment", "t": 1, "text": "queued <!-- romp-msg-id: m -->"}"#);
        assert_eq!(human_prompt_record(&at), None);
        let at2 = atom(r#"{"type": "attachment", "t": 1, "text": " queued "}"#);
        assert_eq!(human_prompt_record(&at2).as_deref(), Some(" queued "));
        let blank = atom(r#"{"type": "attachment", "t": 1, "text": " \u001c "}"#);
        assert_eq!(human_prompt_record(&blank), None);
    }

    #[test]
    fn outrun_truncates_the_coverage() {
        let nd: Node = serde_json::from_str(r#"{"id": "g", "distilledMt": 100.9, "trail": ["s:100:h"]}"#).unwrap();
        let t = |k: &str| (k == "s:h").then_some(Num::Float(100.5));
        let trails = [nd.trail.as_deref()];
        assert!(summary_outrun(&nd, &trails, &t), "100.5 > int(100.9)");
        assert!(!outrun_with(&nd, &trails, &t, Some(Plant::OutrunFloat)));
        let none: Node = serde_json::from_str(r#"{"id": "g", "distilledMt": 0.5, "followupAt": 9}"#).unwrap();
        assert!(!summary_outrun(&none, &[], &t), "int(0.5) is 0: no coverage");
        let big: Node = serde_json::from_str(r#"{"id": "g", "briefedMt": 1e20, "followupAt": 1e20}"#).unwrap();
        assert!(!summary_outrun(&big, &[], &t));
    }

    #[test]
    fn placements_and_captions_drift() {
        let p: Placements = serde_json::from_str(r#"{"s:9:h#p": "g1", "s:1:z#live": null}"#).unwrap();
        assert!(seg_placed(&p, "s:5:h") && !seg_placed(&p, "s:5:q") && seg_placed(&p, "s:3:z"));
        assert!(live_replanned(&p, "s:3:z") && !live_replanned(&p, "s:3:h"));
        let nodes: OMap<Node> = serde_json::from_str(r#"{"g1": {"id": "g1", "parentId": "g0"}, "g0": {"id": "g0", "cleared": true},
            "c1": {"id": "c1", "parentId": "c2"}, "c2": {"id": "c2", "parentId": "c1"}}"#).unwrap();
        let ns = Ns::new(&nodes);
        assert!(card_gone(&ns, "g1") && card_gone(&ns, "nope") && !card_gone(&ns, "c1"));
        assert!(live_cleared_under(&p, &ns, "s:5:h"));
        let caps: OMap<CaptionRow> = serde_json::from_str(r#"{"s:1:h": {"caption": "seg"}, "s:2:h#p": {"caption": "msg"},
            "s:3:k#p": {"caption": null}, "s:4:m#p": {}}"#).unwrap();
        assert_eq!(seg_caption(&caps, "s:9:h"), serde_json::json!("msg"));
        assert_eq!(seg_caption(&caps, "s:3:k"), serde_json::Value::Null);
        assert_eq!(seg_caption(&caps, "s:4:m"), serde_json::json!(""));
        assert_eq!(seg_caption(&caps, "s:4:none"), serde_json::json!(""));
    }
}
