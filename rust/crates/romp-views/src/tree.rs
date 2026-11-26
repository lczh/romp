//! The parse tree the per-card build reads (`trees/<sid>.json`: `ctx["ps"]`, em.plain_tree without `dir` and `_txKey`,
//! written with sorted keys). Session, Turn, Atom, Message, content blocks.
//!
//! The typed keys are the ones the per-card code reads (an ast scan of the 86 listed functions and the unlisted ones
//! that run inside them, grouped by the receiver: `a`, `atom`, `x`, `trig`, `atoms[i]` for atoms; `turn`, `lt`, `seg`
//! for turns; `ps` for the session). Everything else is kept untyped for the round trip. Shapes the build would read
//! differently from Python, or would need a part the spike does not port, are refused by `validate`: a lazy atom (its
//! body is on disk, em.hydrate), a restored pre-cut turn (its spans are stored, em.segments' first branch), a content
//! that is neither a list nor a string, a text that is not a string.

use std::fmt;

use romp_json::Value as Out;
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde::de::value::{MapAccessDeserializer, SeqAccessDeserializer};

use crate::record::{py_truthy, Num, ToJson};

record! {
    /// One session's parse: its turns in order.
    pub struct Tree {
        color: Option<String> = "color",
        landed_text_uuids: Vec<String> = "landedTextUuids",
        leaf_fsid: Option<String> = "leafFsid",
        name: Option<String> = "name",
        romp_uuid: Option<String> = "rompUuid",
        skill_loads: serde_json::Value = "skillLoads",
        turns: Vec<Turn> = "turns",
    }
    required: [turns]
}

record! {
    /// A turn: an opener and what follows it, to the next opener.
    pub struct Turn {
        atoms: Vec<Atom> = "atoms",
        t: Num = "t",
        end: Num = "end",
        ended: bool = "ended",
        id: String = "id",
        trigger: Option<Trigger> = "trigger",
        /// The uuids of stale echoes placed into this turn (kernel._turn_sans_placed_echoes drops them); an atom
        /// without a uuid matches a null entry, as `None in set(placed)` does.
        placed_echoes: Option<Vec<Option<String>>> = "placedEchoes",
        echo_turn: serde_json::Value = "echoTurn",
        /// Set on a restored pre-cut turn (refused by `validate`; em.turn_scalar reads it).
        pre: serde_json::Value = "pre",
    }
    required: [atoms, t, end]
}

record! {
    /// A turn's trigger: the uuid of the atom that opened it.
    pub struct Trigger {
        uuid: Option<String> = "uuid",
    }
}

record! {
    /// One transcript record as the parser keeps it (or a synthesized one: an idle span, a live echo).
    pub struct Atom {
        kind: String = "type",
        uuid: Option<String> = "uuid",
        t: Num = "t",
        end: Num = "end",
        author: Option<Author> = "author",
        message: Option<Message> = "message",
        is_api_error: Option<bool> = "isApiError",
        origin: serde_json::Value = "origin",
        command: serde_json::Value = "command",
        echo_text: serde_json::Value = "_echo_text",
        /// A lazy atom's scalars (no body): refused by `validate` unless null.
        lazy: serde_json::Value = "lazy",
        text: serde_json::Value = "text",
        parent_uuid: Option<String> = "parentUuid",
        session_id: Option<String> = "session_id",
        fsid: Option<String> = "fsid",
        prompt_source: Option<String> = "promptSource",
    }
    required: [kind, t]
}

record! {
    /// A record's message. `content` may be absent, but not null: jd._atom_text iterates it (a TypeError in Python).
    pub struct Message {
        role: Option<String> = "role",
        content: Content = "content",
        model: Option<String> = "model",
        stop_reason: Option<String> = "stop_reason",
    }
}

record! {
    /// One content block. `text` must be a string wherever it appears (Python joins it, a TypeError otherwise).
    pub struct BlockObj {
        kind: Option<String> = "type",
        text: String = "text",
        id: Option<String> = "id",
        name: Option<String> = "name",
        tool_use_id: Option<String> = "tool_use_id",
    }
}

/// A message's content: a list of blocks, or a bare string (em._content makes it one text block when it has
/// non-whitespace; jd._atom_text iterates its characters and finds no block).
#[derive(Debug, Clone, PartialEq)]
pub enum Content {
    Blocks(Vec<Block>),
    Text(String),
}

/// An element of a content list. Every reader skips one that is not a dict, as Python's `isinstance(b, dict)` does.
#[derive(Debug, Clone, PartialEq)]
pub enum Block {
    Obj(BlockObj),
    Other(serde_json::Value),
}

/// An atom's author: a name (`human`, `sdk`, `romp`, `system`, ...) or a peer's identity dict.
#[derive(Debug, Clone, PartialEq)]
pub enum Author {
    Name(String),
    Peer(serde_json::Map<String, serde_json::Value>),
}

impl Block {
    pub fn obj(&self) -> Option<&BlockObj> {
        match self {
            Block::Obj(b) => Some(b),
            Block::Other(_) => None,
        }
    }
}

impl BlockObj {
    /// `b.get("type") == kind`.
    pub fn is(&self, kind: &str) -> bool {
        matches!(&self.kind, Some(Some(k)) if k == kind)
    }

    /// `b.get("text", "")` (a null text is refused at load).
    pub fn text_or_empty(&self) -> &str {
        self.text.as_deref().unwrap_or("")
    }
}

impl Atom {
    /// `atom.get("message")` as Python's `isinstance(message, dict)` sees it: absent and null are both None.
    pub fn message(&self) -> Option<&Message> {
        self.message.as_ref().and_then(Option::as_ref)
    }

    /// `atom.get("type")`.
    pub fn kind(&self) -> &str {
        self.kind.as_deref().expect("type is required at load")
    }

    /// `atom.get("uuid")`, absent and null both None.
    pub fn uuid(&self) -> Option<&str> {
        self.uuid.as_ref().and_then(|u| u.as_deref())
    }
}

impl Message {
    /// Python's `bool(message)`: a dict is truthy when it has any key.
    pub fn truthy(&self) -> bool {
        !self.order.is_empty()
    }
}

impl<'de> Deserialize<'de> for Content {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Content;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a content list or string")
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut s: A) -> Result<Content, A::Error> {
                let mut blocks = Vec::with_capacity(s.size_hint().unwrap_or(0));
                while let Some(b) = s.next_element::<Block>()? {
                    blocks.push(b);
                }
                Ok(Content::Blocks(blocks))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Content, E> {
                Ok(Content::Text(v.to_string()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Content, E> {
                Ok(Content::Text(v))
            }
        }
        d.deserialize_any(V)
    }
}

impl<'de> Deserialize<'de> for Block {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Block;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a content block")
            }
            fn visit_map<A: MapAccess<'de>>(self, m: A) -> Result<Block, A::Error> {
                BlockObj::from_map(m).map(Block::Obj)
            }
            fn visit_seq<A: SeqAccess<'de>>(self, s: A) -> Result<Block, A::Error> {
                serde_json::Value::deserialize(SeqAccessDeserializer::new(s)).map(Block::Other)
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Block, E> {
                Ok(Block::Other(serde_json::Value::String(v.to_string())))
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Block, E> {
                Ok(Block::Other(serde_json::Value::Bool(v)))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Block, E> {
                Ok(Block::Other(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Block, E> {
                Ok(Block::Other(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Block, E> {
                serde_json::Number::from_f64(v)
                    .map(|n| Block::Other(serde_json::Value::Number(n)))
                    .ok_or_else(|| E::custom("a non-finite number"))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Block, E> {
                Ok(Block::Other(serde_json::Value::Null))
            }
        }
        d.deserialize_any(V)
    }
}

impl<'de> Deserialize<'de> for Author {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Author;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("an author name or a peer identity object")
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Author, E> {
                Ok(Author::Name(v.to_string()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Author, E> {
                Ok(Author::Name(v))
            }
            fn visit_map<A: MapAccess<'de>>(self, m: A) -> Result<Author, A::Error> {
                serde_json::Map::deserialize(MapAccessDeserializer::new(m)).map(Author::Peer)
            }
        }
        d.deserialize_any(V)
    }
}

impl ToJson for Content {
    fn to_json(&self) -> Out {
        match self {
            Content::Blocks(b) => b.to_json(),
            Content::Text(s) => Out::Str(s.clone()),
        }
    }
}

impl ToJson for Block {
    fn to_json(&self) -> Out {
        match self {
            Block::Obj(b) => b.to_json(),
            Block::Other(v) => v.to_json(),
        }
    }
}

impl ToJson for Author {
    fn to_json(&self) -> Out {
        match self {
            Author::Name(s) => Out::Str(s.clone()),
            Author::Peer(m) => serde_json::Value::Object(m.clone()).to_json(),
        }
    }
}

/// Keys the per-card code reads on a turn that `Turn` does not type: a restored pre-cut turn's stored spans.
const TURN_UNPORTED: &[&str] = &["segs"];

/// Refuse a tree the port would read differently from Python: the shapes the module comment names. `Err` carries
/// the path of the first refused part.
pub fn validate(tree: &Tree) -> Result<(), String> {
    let turns = tree.turns.as_deref().unwrap_or_default();
    for (ti, turn) in turns.iter().enumerate() {
        validate_turn(turn).map_err(|e| format!("turns[{ti}]{e}"))?;
    }
    Ok(())
}

/// Refuse a turn the port would read differently from Python: a restored pre-cut turn (em.segments' first branch
/// returns its stored spans, which are not ported), a key the per-card code reads but `Turn` does not type, or a
/// refused atom. `Err` starts with the path inside the turn (`""`, or `.atoms[i]`) and a colon.
pub fn validate_turn(turn: &Turn) -> Result<(), String> {
    if turn.pre.as_ref().is_some_and(py_truthy) {
        return Err(": a restored pre-cut turn (`pre`): its stored spans are not ported".to_string());
    }
    if let Some(k) = turn.unported_key(TURN_UNPORTED) {
        return Err(format!(": key {k:?} is read by the per-card code but not typed"));
    }
    for (ai, atom) in turn.atoms.as_deref().unwrap_or_default().iter().enumerate() {
        validate_atom(atom).map_err(|e| format!(".atoms[{ai}]: {e}"))?;
    }
    Ok(())
}

/// Refuse an atom the port would read differently from Python: a lazy one (its body is on disk), or one whose `text`
/// is truthy and not a string (jd._human_prompt_record reads `str(a.get("text") or "")`, and Python's `str()` of a
/// number, a list or a dict is a repr the port does not write; a falsy one reads "", as null does).
pub fn validate_atom(atom: &Atom) -> Result<(), String> {
    if atom.lazy.as_ref().is_some_and(|v| !v.is_null()) {
        return Err("a lazy atom (body on disk): em.hydrate is not ported".to_string());
    }
    if let Some(t) = &atom.text
        && py_truthy(t)
        && !t.is_string()
    {
        return Err(format!("a `text` that is truthy and not a string ({t}): its str() is not ported"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn atom(text: &str) -> Result<Atom, String> {
        serde_json::from_str::<Atom>(text).map_err(|e| e.to_string())
    }

    #[test]
    fn an_atom_round_trips_with_its_key_order() {
        let text = r#"{"author": {"peer": "s2"}, "message": {"content": [{"text": "hi", "type": "text"}, 5, "x", null, [1]], "role": "user"}, "t": 1, "type": "user", "uuid": null, "zz": {"a": [1.5]}}"#;
        let a = atom(text).unwrap();
        assert_eq!(romp_json::dumps(&a.to_json()), text);
        assert!(matches!(a.author, Some(Some(Author::Peer(_)))));
        assert_eq!(a.uuid(), None);
        assert_eq!(a.uuid, Some(None));
    }

    #[test]
    fn content_may_be_a_string_but_not_null_or_a_dict() {
        let s = atom(r#"{"type": "user", "t": 1, "message": {"content": "  hi "}}"#).unwrap();
        assert_eq!(s.message().unwrap().content, Some(Content::Text("  hi ".into())));
        let null = atom(r#"{"type": "user", "t": 1, "message": {"content": null}}"#).unwrap_err();
        assert!(null.contains("Atom.message: Message.content: invalid type: null, expected a content list or string"),
                "{null}");
        let dict = atom(r#"{"type": "user", "t": 1, "message": {"content": {"a": 1}}}"#).unwrap_err();
        assert!(dict.contains("invalid type: map, expected a content list or string"), "{dict}");
    }

    #[test]
    fn a_text_must_be_a_string() {
        let e = atom(r#"{"type": "user", "t": 1, "message": {"content": [{"type": "text", "text": null}]}}"#)
            .unwrap_err();
        assert!(e.contains("BlockObj.text: invalid type: null, expected a string"), "{e}");
    }

    #[test]
    fn the_type_and_t_are_required() {
        let e = atom(r#"{"t": 1}"#).unwrap_err();
        assert!(e.contains("Atom has no \"type\" key"), "{e}");
        let e = atom(r#"{"type": "user"}"#).unwrap_err();
        assert!(e.contains("Atom has no \"t\" key"), "{e}");
    }

    #[test]
    fn an_author_is_a_name_or_a_dict() {
        let e = atom(r#"{"type": "user", "t": 1, "author": 5}"#).unwrap_err();
        assert!(e.contains("Atom.author: invalid type: integer `5`, expected an author name or a peer identity object"),
                "{e}");
    }

    #[test]
    fn lazy_atoms_and_pre_cut_turns_are_refused() {
        let lazy: Tree = serde_json::from_str(
            r#"{"turns": [{"atoms": [{"type": "user", "t": 1, "lazy": {"h": "abcd1234"}}], "t": 1, "end": 2}]}"#,
        )
        .unwrap();
        assert_eq!(validate(&lazy).unwrap_err(),
                   "turns[0].atoms[0]: a lazy atom (body on disk): em.hydrate is not ported");
        let null_lazy: Tree =
            serde_json::from_str(r#"{"turns": [{"atoms": [{"type": "user", "t": 1, "lazy": null}], "t": 1, "end": 2}]}"#)
                .unwrap();
        assert_eq!(validate(&null_lazy), Ok(()));
        let pre: Tree = serde_json::from_str(r#"{"turns": [{"atoms": [], "t": 1, "end": 2, "pre": true}]}"#).unwrap();
        assert_eq!(validate(&pre).unwrap_err(),
                   "turns[0]: a restored pre-cut turn (`pre`): its stored spans are not ported");
        let segs: Tree = serde_json::from_str(r#"{"turns": [{"atoms": [], "t": 1, "end": 2, "segs": []}]}"#).unwrap();
        assert_eq!(validate(&segs).unwrap_err(), "turns[0]: key \"segs\" is read by the per-card code but not typed");
    }

    #[test]
    fn a_truthy_non_string_text_is_refused() {
        for (text, ok) in [("\"x\"", true), ("\"\"", true), ("null", true), ("0", true), ("false", true), ("[]", true),
                           ("{}", true), ("5", false), ("[1]", false), ("{\"a\": 1}", false), ("true", false)] {
            let a = atom(&format!(r#"{{"type": "attachment", "t": 1, "text": {text}}}"#)).unwrap();
            assert_eq!(validate_atom(&a).is_ok(), ok, "{text}");
        }
        let a = atom(r#"{"type": "attachment", "t": 1, "text": [1]}"#).unwrap();
        assert_eq!(validate_atom(&a).unwrap_err(), "a `text` that is truthy and not a string ([1]): its str() is not ported");
    }
}
