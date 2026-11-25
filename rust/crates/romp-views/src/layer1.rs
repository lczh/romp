//! Layer 1 of rust/notes/s2-design.md section 2, "Text and atom primitives", over the typed tree.
//!
//! Each function names the Python it ports (event_model as `em`, judge as `jd`, the kernel as `kernel`) and follows it
//! line for line, including where two of them read the same atom differently: em._content makes a bare-string content
//! one text block, while jd._atom_text iterates the string's characters and finds none, and em._prose_chars counts no
//! prose on it at all. Lengths are code points (Python's `len`), whitespace is Python's (`pystr`), and the only regexes
//! (ROMP_INJECT_RE, SCHEDULED_PREAMBLE_RE) are matched by hand. A lazy atom never reaches these functions: the loader
//! refuses it (`tree::validate`), so every `lz = atom.get("lazy")` branch is dropped.

use std::borrow::Cow;

use romp_json::Value as Out;

use crate::pystr;
use crate::record::{canon, py_truthy, ToJson};
use crate::sha1;
use crate::tree::{Atom, Author, Block, Content, Message, Turn};

/// em.END_STOPS: the stop reasons that end a turn.
pub const END_STOPS: [&str; 2] = ["end_turn", "stop_sequence"];
/// jd.CITE_MIN_CHARS: the "substantive" floor that atom_prose_chars is compared with (layers 3 and 5).
pub const CITE_MIN_CHARS: usize = 80;
/// The CLI's settle reply (em.atom_is_settle).
pub const SETTLE_TEXT: &str = "No response requested.";
/// The model stamp a synthetic reply carries.
pub const SYNTHETIC_MODEL: &str = "<synthetic>";
const INTERRUPT_PREFIX: &str = "[Request interrupted by user";
const SCHEDULED_PREAMBLE: &str = "[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT]";

/// What em._content returns: the message's blocks, one synthesized text block for a bare string with non-whitespace,
/// or nothing.
#[derive(Debug, Clone, Copy)]
pub enum Blocks<'a> {
    List(&'a [Block]),
    Text(&'a str),
    Empty,
}

/// em._content(message): `[]` for a non-dict; a bare string becomes `[{"type": "text", "text": c}]` when `c.strip()`,
/// else `[]`; a list is returned as is; anything else (absent) is `[]`.
pub fn content(message: Option<&Message>) -> Blocks<'_> {
    match message.and_then(|m| m.content.as_ref()) {
        Some(Content::Blocks(b)) => Blocks::List(b),
        Some(Content::Text(c)) if !pystr::strip(c).is_empty() => Blocks::Text(c),
        _ => Blocks::Empty,
    }
}

impl<'a> Blocks<'a> {
    /// The texts of the text blocks, in order (`b.get("text", "")` for each dict block whose type is "text").
    pub fn texts(self) -> impl Iterator<Item = &'a str> {
        let (list, text): (&'a [Block], Option<&'a str>) = match self {
            Blocks::List(b) => (b, None),
            Blocks::Text(c) => (&[], Some(c)),
            Blocks::Empty => (&[], None),
        };
        text.into_iter().chain(list.iter().filter_map(Block::obj).filter(|b| b.is("text")).map(|b| b.text_or_empty()))
    }

    /// atom_tool_uses' pairs: `(b.get("id"), b.get("name"))` of each dict block whose type is "tool_use".
    pub fn tool_uses(self) -> Vec<(Option<&'a str>, Option<&'a str>)> {
        match self {
            Blocks::List(b) => b
                .iter()
                .filter_map(Block::obj)
                .filter(|b| b.is("tool_use"))
                .map(|b| (b.id.as_ref().and_then(|x| x.as_deref()), b.name.as_ref().and_then(|x| x.as_deref())))
                .collect(),
            _ => Vec::new(),
        }
    }

    /// The list as JSON (the synthesized block too), for comparing with Python's `json.dumps(em._content(m))`.
    pub fn to_json(self) -> Out {
        match self {
            Blocks::List(b) => Out::Array(b.iter().map(ToJson::to_json).collect()),
            Blocks::Text(c) => Out::Array(vec![Out::Object(vec![
                ("type".to_string(), Out::Str("text".to_string())),
                ("text".to_string(), Out::Str(c.to_string())),
            ])]),
            Blocks::Empty => Out::Array(Vec::new()),
        }
    }
}

/// `" ".join(texts).strip()`, borrowing when there is at most one text.
fn join_strip<'a>(mut texts: impl Iterator<Item = &'a str>) -> Cow<'a, str> {
    let Some(first) = texts.next() else { return Cow::Borrowed("") };
    let Some(second) = texts.next() else { return Cow::Borrowed(pystr::strip(first)) };
    let mut s = String::with_capacity(first.len() + second.len() + 1);
    s.push_str(first);
    s.push(' ');
    s.push_str(second);
    for t in texts {
        s.push(' ');
        s.push_str(t);
    }
    let stripped = pystr::strip(&s);
    if stripped.len() == s.len() { Cow::Owned(s) } else { Cow::Owned(stripped.to_string()) }
}

/// em._text_of(blocks): the text blocks' texts joined by one space, stripped.
pub fn text_of(blocks: Blocks<'_>) -> Cow<'_, str> {
    join_strip(blocks.texts())
}

/// `_text_of(_content(atom.get("message")))`, the text every predicate below reads.
pub fn atom_text_of(atom: &Atom) -> Cow<'_, str> {
    text_of(content(atom.message()))
}

/// em._has_text(atom).
pub fn has_text(atom: &Atom) -> bool {
    !atom_text_of(atom).is_empty()
}

/// em.atom_has_text(atom): em._has_text.
pub fn atom_has_text(atom: &Atom) -> bool {
    has_text(atom)
}

/// ROMP_INJECT_RE.search(text): `<!--\s*romp-injected\s*-->` anywhere. Neither `\s*` can backtrack into the literal
/// after it (whitespace is never `r` or `-`), and `<!--` cannot overlap itself, so trying each occurrence of `<!--`
/// in turn is the regex's own search.
pub fn romp_inject_search(text: &str) -> bool {
    text.match_indices("<!--").any(|(i, _)| {
        pystr::skip_space(&text[i + 4..])
            .strip_prefix("romp-injected")
            .is_some_and(|rest| pystr::skip_space(rest).starts_with("-->"))
    })
}

/// SCHEDULED_PREAMBLE_RE.match(text): `^\s*\[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT\]`.
pub fn scheduled_preamble_match(text: &str) -> bool {
    pystr::skip_space(text).starts_with(SCHEDULED_PREAMBLE)
}

/// em._machine_written(atom): text that romp injected or a scheduled task fired (the id keys on the anchor's uuid).
pub fn machine_written(atom: &Atom) -> bool {
    let text = atom_text_of(atom);
    if text.is_empty() {
        return false;
    }
    if romp_inject_search(&text) || scheduled_preamble_match(&text) {
        return true;
    }
    match &atom.origin {
        Some(serde_json::Value::Object(o)) => {
            o.get("kind").and_then(serde_json::Value::as_str) == Some("task-notification")
                && o.get("subkind").and_then(serde_json::Value::as_str) == Some("scheduled-trigger")
        }
        _ => false,
    }
}

/// em._text_hash8(atom): `sha1(text.encode("utf-8", "replace")).hexdigest()[:8]`.
pub fn text_hash8(atom: &Atom) -> String {
    sha1::hex8(atom_text_of(atom).as_bytes())
}

/// `(atom.get("message") or {})`: the message when it is a non-empty dict.
fn message_or_empty(atom: &Atom) -> Option<&Message> {
    atom.message().filter(|m| m.truthy())
}

/// em._stop_reason(atom): `(atom.get("message") or {}).get("stop_reason")`.
pub fn stop_reason(atom: &Atom) -> Option<&str> {
    message_or_empty(atom).and_then(|m| m.stop_reason.as_ref()).and_then(|s| s.as_deref())
}

/// em.is_interrupt_record(atom): a user atom whose text starts with "[Request interrupted by user".
pub fn is_interrupt_record(atom: &Atom) -> bool {
    atom.kind() == "user" && atom_text_of(atom).starts_with(INTERRUPT_PREFIX)
}

/// em._is_opener(atom): a user atom authored human, sdk or romp, or by a peer (a dict).
pub fn is_opener(atom: &Atom) -> bool {
    if atom.kind() != "user" {
        return false;
    }
    match &atom.author {
        Some(Some(Author::Name(a))) => matches!(a.as_str(), "human" | "sdk" | "romp"),
        Some(Some(Author::Peer(_))) => true,
        _ => false,
    }
}

/// em._is_segment_input(atom): em._is_opener.
pub fn is_segment_input(atom: &Atom) -> bool {
    is_opener(atom)
}

/// em._prose_chars(atom): code points of the text blocks of `(atom.get("message") or {}).get("content", [])`, each
/// unstripped; 0 when the content is not a list (a bare string counts nothing here).
pub fn prose_chars(atom: &Atom) -> usize {
    match message_or_empty(atom).and_then(|m| m.content.as_ref()) {
        Some(Content::Blocks(b)) => Blocks::List(b).texts().map(pystr::len).sum(),
        _ => 0,
    }
}

/// em.atom_prose_chars(atom): assistant prose only, 0 on an API error.
pub fn atom_prose_chars(atom: &Atom) -> usize {
    if atom.kind() != "assistant" || matches!(atom.is_api_error, Some(Some(true))) {
        return 0;
    }
    prose_chars(atom)
}

/// kernel._atom_prose_chars(a): em.atom_prose_chars.
pub fn kernel_atom_prose_chars(atom: &Atom) -> usize {
    atom_prose_chars(atom)
}

/// em.atom_tool_uses(atom): `[(id, name)]` of the tool_use blocks of em._content.
pub fn atom_tool_uses(atom: &Atom) -> Vec<(Option<&str>, Option<&str>)> {
    content(atom.message()).tool_uses()
}

/// em.atom_is_bare_end(atom): an assistant atom that only ends its turn (an END_STOPS stop, no text, no tool use).
pub fn atom_is_bare_end(atom: &Atom) -> bool {
    if atom.kind() != "assistant" {
        return false;
    }
    stop_reason(atom).is_some_and(|s| END_STOPS.contains(&s)) && !has_text(atom) && atom_tool_uses(atom).is_empty()
}

/// em.atom_is_settle(atom): the settle reply's text, or a synthetic model's stamp.
pub fn atom_is_settle(atom: &Atom) -> bool {
    let msg = message_or_empty(atom);
    text_of(content(msg)) == SETTLE_TEXT
        || msg.and_then(|m| m.model.as_ref()).and_then(|m| m.as_deref()) == Some(SYNTHETIC_MODEL)
}

/// em.turn_scalar(turn, key): a restored pre-cut turn's stored scalar, None (`Null`) for a plain turn.
pub fn turn_scalar(turn: &Turn, key: &str) -> Out {
    if turn.pre.as_ref().is_some_and(py_truthy) { turn.get_json(key).unwrap_or(Out::Null) } else { Out::Null }
}

/// jd._seg_key(seg_id): the timestamp-invariant key, `parts[0] + ":" + parts[-1]` of a seg id with three or more
/// colon-separated parts; anything else (empty included) unchanged.
pub fn seg_key(seg_id: &str) -> Cow<'_, str> {
    let mut parts = seg_id.split(':');
    let first = parts.next().unwrap_or("");
    let mut n = 1;
    let mut last = first;
    for p in parts {
        n += 1;
        last = p;
    }
    if n >= 3 {
        let mut s = String::with_capacity(first.len() + last.len() + 1);
        s.push_str(first);
        s.push(':');
        s.push_str(last);
        Cow::Owned(s)
    } else {
        Cow::Borrowed(seg_id)
    }
}

/// kernel._seg_key(seg_id): jd._seg_key.
pub fn kernel_seg_key(seg_id: &str) -> Cow<'_, str> {
    seg_key(seg_id)
}

/// kernel._atom_user_text(a): a user atom's text under sb.echo_text_key (outer whitespace stripped), None when empty
/// or not a user atom. A list content joins its text blocks; a bare string is stripped; anything else is "".
pub fn atom_user_text(atom: &Atom) -> Option<Cow<'_, str>> {
    if atom.kind() != "user" {
        return None;
    }
    let text = match message_or_empty(atom).and_then(|m| m.content.as_ref()) {
        Some(Content::Blocks(b)) => join_strip(Blocks::List(b).texts()),
        Some(Content::Text(c)) => Cow::Borrowed(pystr::strip(c)),
        None => Cow::Borrowed(""),
    };
    if text.is_empty() { None } else { Some(text) }
}

/// jd._atom_text(atom): the text blocks of `msg.get("content", [])` joined and stripped. Iterating a bare string
/// yields characters, none of them a dict, so a string content reads "" here.
pub fn jd_atom_text(atom: &Atom) -> Cow<'_, str> {
    match message_or_empty(atom).and_then(|m| m.content.as_ref()) {
        Some(Content::Blocks(b)) => join_strip(Blocks::List(b).texts()),
        _ => Cow::Borrowed(""),
    }
}

/// em._content's list as `json.dumps(list, sort_keys=True)` hashes it: the first 16 hex digits of its SHA-1 (how
/// tools/rust-spike/dump_layer1.py records that column, so the dump does not hold every body twice).
pub fn content_hash(atom: &Atom) -> String {
    let text = romp_json::dumps(&canon(&content(atom.message()).to_json()));
    let mut h = sha1::hex(text.as_bytes());
    h.truncate(16);
    h
}

fn opt_str(s: Option<&str>) -> Out {
    s.map_or(Out::Null, |s| Out::Str(s.to_string()))
}

fn count(n: usize) -> Out {
    Out::Int(i64::try_from(n).expect("a count fits in i64"))
}

/// A layer-1 atom function answering as JSON.
pub type AtomFn = fn(&Atom) -> Out;

/// The atom functions, named and ordered as tools/rust-spike/dump_layer1.py writes their Python answers.
pub const ATOM_COLUMNS: &[(&str, AtomFn)] = &[
    ("em._content", |a| Out::Str(content_hash(a))),
    ("em._text_of", |a| Out::Str(atom_text_of(a).into_owned())),
    ("em._has_text", |a| Out::Bool(has_text(a))),
    ("em.atom_has_text", |a| Out::Bool(atom_has_text(a))),
    ("em._machine_written", |a| Out::Bool(machine_written(a))),
    ("em._text_hash8", |a| Out::Str(text_hash8(a))),
    ("em._stop_reason", |a| opt_str(stop_reason(a))),
    ("em.is_interrupt_record", |a| Out::Bool(is_interrupt_record(a))),
    ("em._is_opener", |a| Out::Bool(is_opener(a))),
    ("em._is_segment_input", |a| Out::Bool(is_segment_input(a))),
    ("em._prose_chars", |a| count(prose_chars(a))),
    ("em.atom_prose_chars", |a| count(atom_prose_chars(a))),
    ("kernel._atom_prose_chars", |a| count(kernel_atom_prose_chars(a))),
    ("em.atom_is_bare_end", |a| Out::Bool(atom_is_bare_end(a))),
    ("em.atom_is_settle", |a| Out::Bool(atom_is_settle(a))),
    ("em.atom_tool_uses", |a| {
        Out::Array(atom_tool_uses(a).into_iter().map(|(i, n)| Out::Array(vec![opt_str(i), opt_str(n)])).collect())
    }),
    ("kernel._atom_user_text", |a| atom_user_text(a).map_or(Out::Null, |s| Out::Str(s.into_owned()))),
    ("jd._atom_text", |a| Out::Str(jd_atom_text(a).into_owned())),
];

/// The keys the per-card code asks em.turn_scalar for (the anchor walk's stored floor and prose counts).
pub const TURN_KEYS: [&str; 2] = ["hT", "pcs"];

/// A layer-1 seg-id function: None in, None out (`if not seg_id: return seg_id`).
pub type SegFn = fn(Option<&str>) -> Out;

/// The seg-id functions, named as dump_layer1.py writes them.
pub const SEG_COLUMNS: &[(&str, SegFn)] = &[
    ("jd._seg_key", |s| s.map_or(Out::Null, |s| Out::Str(seg_key(s).into_owned()))),
    ("kernel._seg_key", |s| s.map_or(Out::Null, |s| Out::Str(kernel_seg_key(s).into_owned()))),
];

#[cfg(test)]
mod tests {
    use super::*;

    fn atom(json: &str) -> Atom {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn the_boundary_atom_is_79_not_82() {
        let a = atom(r#"{"type": "assistant", "t": 1, "message": {"content": [{"type": "text", "text": "Recap: limit → 3, the naïve cache is gone; the search suite passes, once again."}]}}"#);
        assert_eq!(atom_prose_chars(&a), 79);
        assert!(atom_prose_chars(&a) < CITE_MIN_CHARS, "short of the floor as Python counts");
        let bytes: usize = content(a.message()).texts().map(str::len).sum();
        assert_eq!(bytes, 82, "a byte count would clear the floor");
    }

    #[test]
    fn a_string_content_reads_three_ways() {
        let a = atom(r#"{"type": "user", "t": 1, "author": "human", "message": {"content": "  hello  "}}"#);
        assert_eq!(atom_text_of(&a), "hello");
        assert_eq!(prose_chars(&a), 0);
        assert_eq!(jd_atom_text(&a), "");
        assert_eq!(atom_user_text(&a).as_deref(), Some("hello"));
    }

    #[test]
    fn join_then_strip() {
        let a = atom(r#"{"type": "assistant", "t": 1, "message": {"content": [{"type": "text", "text": " a "}, {"type": "text"}, {"type": "text", "text": "b\u001c"}]}}"#);
        assert_eq!(atom_text_of(&a), "a   b");
        assert_eq!(prose_chars(&a), 5);
    }

    #[test]
    fn the_inject_marker_needs_its_comment_form() {
        assert!(romp_inject_search("x <!--romp-injected--> y"));
        assert!(romp_inject_search("<!--<!-- \u{3000}romp-injected\u{1c} -->"));
        assert!(!romp_inject_search("<!-- romp-injected --"));
        assert!(!romp_inject_search("romp-injected"));
        assert!(!romp_inject_search("<!-- romp-injected-x -->"));
        assert!(scheduled_preamble_match(" \n[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT] go"));
        assert!(!scheduled_preamble_match("go [SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT]"));
    }

    #[test]
    fn seg_keys() {
        for (inp, want) in [("", ""), ("a", "a"), ("a:b", "a:b"), ("a:b:c", "a:c"), ("a:b:c:d", "a:d"),
                            ("::", ":"), ("s:1:h#d", "s:h#d")] {
            assert_eq!(seg_key(inp), want, "{inp:?}");
        }
    }
}
