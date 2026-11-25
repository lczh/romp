//! The index pass: Python's line boundaries and its `json.loads` acceptance, one line at a time, with the few fields
//! the per-line summary keeps captured as byte spans on the way through. No record is built here.
//!
//! What Python's `_scan_jsonl_stream` does to the bytes from a start offset, which this module reproduces:
//! - Only bytes through the last `\n` are consumed. A trailing line with no newline yet (a writer caught mid-append)
//!   is left for the next read: not indexed, not held, not in the CRC.
//! - Each consumed line is split again with `bytes.splitlines(keepends=True)`: `\n`, `\r\n` and a bare `\r` end a
//!   piece (`\x0b`, `\x0c` and `\x1c`-`\x1e` do not; those are `str.splitlines` boundaries, not `bytes`').
//! - Each piece is stripped with `bytes.strip()`: space, `\t`, `\n`, `\r`, `\x0b` and `\x0c` at both ends. Rust's
//!   `trim_ascii` would keep `\x0b`, so the strip is written out. An empty piece is skipped.
//! - The piece is decoded as UTF-8 with `errors="replace"` and handed to `json.loads`; a piece it rejects is skipped
//!   silently. A kept piece is a record, and its `(offset, length)` (terminator included) is what Python's entry
//!   keeps in its offsets array.
//!
//! `json.loads` (3.12, the C scanner, strict) accepts more than RFC 8259 and less than some parsers:
//! - `NaN`, `Infinity` and `-Infinity` are values; any JSON value is a record, not only objects.
//! - Lone surrogate escapes (`"\ud800"`) are accepted. A raw control character (below 0x20) inside a string is not.
//!   Invalid UTF-8 inside a string becomes U+FFFD and is accepted; outside a string it is a syntax error.
//! - An integer literal (no fraction, no exponent) of more than 4,300 digits raises `ValueError`
//!   (`sys.int_info.default_max_str_digits`) and the line is skipped. Floats have no such limit (`1e400` is inf).
//! - Nesting deeper than 9,997 containers raises `RecursionError` (measured on 3.12.11; the exact figure moves by
//!   a few with the C stack depth of the caller, so lines at that depth are outside exact parity).
//! - Duplicate keys: the last value wins. The summary capture follows that.
//! - A leading UTF-8 BOM is refused (it is not JSON whitespace).

use crate::Plant;

/// Python 3.12.11's `json.loads` nesting limit, measured (see the module docs).
pub const MAX_DEPTH: usize = 9997;
/// `sys.int_info.default_max_str_digits`: an integer literal with more digits is refused.
pub const INT_MAX_DIGITS: usize = 4300;

/// The JSON type of a record or of a captured field's value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Kind {
    Object,
    Array,
    String,
    Number,
    Bool,
    Null,
}

impl Kind {
    /// The name the canonical summary prints (Python: dict, list, str, int/float, bool, None).
    pub fn name(self) -> &'static str {
        match self {
            Kind::Object => "object",
            Kind::Array => "array",
            Kind::String => "string",
            Kind::Number => "number",
            Kind::Bool => "bool",
            Kind::Null => "null",
        }
    }
}

// Field tags, 3 bits each in LineIndex::tags.
pub(crate) const T_ABSENT: u8 = 0;
pub(crate) const T_NULL: u8 = 1;
pub(crate) const T_STR: u8 = 2;
pub(crate) const T_NUMBER: u8 = 3;
pub(crate) const T_BOOL: u8 = 4;
pub(crate) const T_OBJECT: u8 = 5;
pub(crate) const T_ARRAY: u8 = 6;

/// The summary fields, in `LineIndex::fields` order.
pub(crate) const F_TYPE: usize = 0;
pub(crate) const F_UUID: usize = 1;
pub(crate) const F_PARENT: usize = 2;
pub(crate) const F_TS: usize = 3;
pub(crate) const F_ROLE: usize = 4;
pub(crate) const NFIELDS: usize = 5;

/// A string's content between its quotes, as byte offsets relative to the start of its line (escapes not decoded).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Span {
    pub start: u32,
    pub end: u32,
}

/// One kept record's index entry: where it sits and its compact summary. 64 bytes; the strings it names stay in the
/// held bytes and are decoded when asked for.
#[derive(Clone, Copy, Debug)]
pub(crate) struct LineIndex {
    /// The piece's byte offset in the file (Python's offsets array, first value).
    pub off: u64,
    /// The piece's length, its line terminator included (Python's offsets array, second value).
    pub len: u32,
    pub kind: Kind,
    /// NFIELDS tags of 3 bits: absent, null, string, number, bool, object, array.
    pub tags: u16,
    pub fields: [Span; NFIELDS],
    /// This record's tool ids: `count` spans from `start` in the entry's tool table.
    pub tools_start: u32,
    pub tools_len: u32,
}

impl LineIndex {
    pub fn tag(&self, f: usize) -> u8 {
        ((self.tags >> (3 * f)) & 7) as u8
    }
}

/// Python's `bytes.isspace` (what `bytes.strip()` removes).
#[inline]
fn is_py_space(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

/// JSON's whitespace as Python's decoder skips it (`[ \t\n\r]`).
#[inline]
fn is_json_ws(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r')
}

/// The bounds of `bytes.strip()` over `s` (start, end); start == end when nothing is left.
pub(crate) fn py_strip(s: &[u8], plant: Option<Plant>) -> (usize, usize) {
    let space: fn(u8) -> bool = if plant == Some(Plant::VtStrip) { |b: u8| b.is_ascii_whitespace() } else { is_py_space };
    let mut a = 0;
    let mut b = s.len();
    while a < b && space(s[a]) {
        a += 1;
    }
    while b > a && space(s[b - 1]) {
        b -= 1;
    }
    (a, b)
}

/// Reused buffers for the index pass (one per store, so a scan allocates nothing per line).
#[derive(Default)]
pub(crate) struct Scratch {
    stack: Vec<Frame>,
    tools: Vec<Span>,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Ctx {
    Top,
    Message,
    Content,
    Block,
    Other,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum St {
    First,
    Key,
    Colon,
    Value,
    Next,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Slot {
    Top,
    Elem,
    Field(usize),
    BlockType,
    BlockId,
    BlockToolUse,
    Message,
    Content,
    Other,
}

#[derive(Clone, Copy, Debug)]
struct Frame {
    obj: bool,
    ctx: Ctx,
    state: St,
    pending: Slot,
}

#[derive(Default)]
struct Capture {
    tags: u16,
    fields: [Span; NFIELDS],
    btype: (u8, Span),
    bid: (u8, Span),
    btuid: (u8, Span),
}

impl Capture {
    fn set(&mut self, f: usize, tag: u8, sp: Span) {
        self.tags = (self.tags & !(7 << (3 * f))) | ((tag as u16) << (3 * f));
        self.fields[f] = sp;
    }
}

/// Whether the JSON string content `raw` (escapes undecoded) decodes to the ASCII text `name`.
pub(crate) fn raw_str_eq(raw: &[u8], name: &[u8]) -> bool {
    if !raw.contains(&b'\\') {
        return raw == name; // a non-ASCII byte decodes to a non-ASCII character (or U+FFFD): never equal to ASCII
    }
    let mut units = Vec::with_capacity(name.len());
    decode_units(raw, &mut units);
    units.len() == name.len() && units.iter().zip(name).all(|(u, b)| *u == *b as u16)
}

/// The UTF-16 code units of the JSON string content `raw`, as Python's `str` holds them after
/// `decode("utf-8", "replace")` and `json.loads`: invalid UTF-8 becomes U+FFFD (substitution of maximal subparts,
/// what both CPython and `String::from_utf8_lossy` do) and escapes are decoded, lone surrogates kept as units.
/// `raw` is content the index pass accepted, so every escape is well formed.
pub(crate) fn decode_units(raw: &[u8], out: &mut Vec<u16>) {
    let mut i = 0;
    while i < raw.len() {
        let j = raw[i..].iter().position(|&b| b == b'\\').map_or(raw.len(), |k| i + k);
        if j > i {
            let run = &raw[i..j];
            if run.is_ascii() {
                out.extend(run.iter().map(|&b| b as u16));
            } else {
                out.extend(String::from_utf8_lossy(run).encode_utf16());
            }
        }
        if j >= raw.len() {
            break;
        }
        let e = raw.get(j + 1).copied().unwrap_or(b'\\');
        let (unit, step) = match e {
            b'"' => (0x22, 2),
            b'\\' => (0x5c, 2),
            b'/' => (0x2f, 2),
            b'b' => (0x08, 2),
            b'f' => (0x0c, 2),
            b'n' => (0x0a, 2),
            b'r' => (0x0d, 2),
            b't' => (0x09, 2),
            b'u' => (hex4(&raw[j + 2..j + 6]), 6),
            _ => (0xfffd, 2), // unreachable for accepted content
        };
        out.push(unit);
        i = j + step;
    }
}

fn hex4(h: &[u8]) -> u16 {
    h.iter().fold(0u16, |acc, &c| {
        let d = match c {
            b'0'..=b'9' => c - b'0',
            b'a'..=b'f' => c - b'a' + 10,
            _ => c - b'A' + 10,
        };
        (acc << 4) | d as u16
    })
}

struct P<'a> {
    s: &'a [u8],
    i: usize,
}

impl<'a> P<'a> {
    #[inline]
    fn ws(&mut self) {
        while self.i < self.s.len() && is_json_ws(self.s[self.i]) {
            self.i += 1;
        }
    }

    /// At an opening quote: the content's span, or None when the string is not one `json.loads` accepts.
    fn string(&mut self) -> Option<Span> {
        let s = self.s;
        let start = self.i + 1;
        let mut i = start;
        loop {
            let c = *s.get(i)?;
            match c {
                b'"' => {
                    self.i = i + 1;
                    return Some(Span { start: start as u32, end: i as u32 });
                }
                b'\\' => match *s.get(i + 1)? {
                    b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => i += 2,
                    b'u' => {
                        let h = s.get(i + 2..i + 6)?;
                        if !h.iter().all(u8::is_ascii_hexdigit) {
                            return None;
                        }
                        i += 6;
                    }
                    _ => return None,
                },
                0..=0x1f => return None, // strict: no raw control character inside a string
                _ => i += 1,
            }
        }
    }

    /// A number as Python's `_match_number_unicode` matches it (backtracking an incomplete fraction or exponent,
    /// which then fails as extra data), with the integer digit limit.
    fn number(&mut self) -> Option<()> {
        let s = self.s;
        let mut i = self.i;
        if s.get(i) == Some(&b'-') {
            i += 1;
        }
        let int_start = i;
        match s.get(i)? {
            b'0' => i += 1,
            b'1'..=b'9' => {
                while i < s.len() && s[i].is_ascii_digit() {
                    i += 1;
                }
            }
            _ => return None,
        }
        let int_digits = i - int_start;
        let mut is_float = false;
        if i + 1 < s.len() && s[i] == b'.' && s[i + 1].is_ascii_digit() {
            i += 2;
            while i < s.len() && s[i].is_ascii_digit() {
                i += 1;
            }
            is_float = true;
        }
        if i < s.len() && (s[i] == b'e' || s[i] == b'E') {
            let save = i;
            i += 1;
            if i < s.len() && (s[i] == b'+' || s[i] == b'-') {
                i += 1;
            }
            if i < s.len() && s[i].is_ascii_digit() {
                while i < s.len() && s[i].is_ascii_digit() {
                    i += 1;
                }
                is_float = true;
            } else {
                i = save;
            }
        }
        if !is_float && int_digits > INT_MAX_DIGITS {
            return None;
        }
        self.i = i;
        Some(())
    }

    fn lit(&mut self, w: &[u8]) -> Option<()> {
        if self.s[self.i..].starts_with(w) {
            self.i += w.len();
            Some(())
        } else {
            None
        }
    }
}

/// The summary of one kept piece: the record's kind, the captured fields and (in `Scratch::tools`) its tool ids.
pub(crate) struct Indexed {
    pub kind: Kind,
    pub tags: u16,
    pub fields: [Span; NFIELDS],
}

/// Index one stripped piece. Some when Python's `json.loads` accepts it (the record is kept), with the tool ids in
/// `sc.tools`; None when it does not (the line is skipped, as Python skips it).
pub(crate) fn index_piece(s: &[u8], sc: &mut Scratch, plant: Option<Plant>) -> Option<Indexed> {
    sc.stack.clear();
    sc.tools.clear();
    let mut p = P { s, i: 0 };
    let mut cap = Capture::default();
    p.ws();
    let kind = begin(&mut p, &mut cap, sc, Slot::Top)?;
    while let Some(top) = sc.stack.last().copied() {
        p.ws();
        let c = *p.s.get(p.i)?;
        let n = sc.stack.len() - 1;
        if top.obj {
            match top.state {
                St::First | St::Key => {
                    if c == b'}' && top.state == St::First {
                        p.i += 1;
                        close(&mut cap, sc, s, plant);
                        continue;
                    }
                    if c != b'"' {
                        return None;
                    }
                    let k = p.string()?;
                    let key = &s[k.start as usize..k.end as usize];
                    let slot = slot_for(top.ctx, key, &mut cap, sc);
                    let f = &mut sc.stack[n];
                    f.pending = slot;
                    f.state = St::Colon;
                }
                St::Colon => {
                    if c != b':' {
                        return None;
                    }
                    p.i += 1;
                    sc.stack[n].state = St::Value;
                }
                St::Value => {
                    sc.stack[n].state = St::Next;
                    begin(&mut p, &mut cap, sc, top.pending)?;
                }
                St::Next => match c {
                    b',' => {
                        p.i += 1;
                        sc.stack[n].state = St::Key;
                    }
                    b'}' => {
                        p.i += 1;
                        close(&mut cap, sc, s, plant);
                    }
                    _ => return None,
                },
            }
        } else {
            match top.state {
                St::First | St::Value => {
                    if c == b']' && top.state == St::First {
                        p.i += 1;
                        close(&mut cap, sc, s, plant);
                        continue;
                    }
                    sc.stack[n].state = St::Next;
                    let slot = if top.ctx == Ctx::Content { Slot::Elem } else { Slot::Other };
                    begin(&mut p, &mut cap, sc, slot)?;
                }
                St::Next => match c {
                    b',' => {
                        p.i += 1;
                        sc.stack[n].state = St::Value;
                    }
                    b']' => {
                        p.i += 1;
                        close(&mut cap, sc, s, plant);
                    }
                    _ => return None,
                },
                St::Key | St::Colon => unreachable!("arrays have no keys"),
            }
        }
    }
    p.ws();
    if p.i != s.len() {
        return None; // "Extra data"
    }
    Some(Indexed { kind, tags: cap.tags, fields: cap.fields })
}

/// The slot a member's value fills, from its container's context and its key (decoded). A repeated `message` or
/// `content` key starts its capture over: the last value wins, as in the dict Python builds.
fn slot_for(ctx: Ctx, key: &[u8], cap: &mut Capture, sc: &mut Scratch) -> Slot {
    match ctx {
        Ctx::Top => {
            if raw_str_eq(key, b"type") {
                Slot::Field(F_TYPE)
            } else if raw_str_eq(key, b"uuid") {
                Slot::Field(F_UUID)
            } else if raw_str_eq(key, b"parentUuid") {
                Slot::Field(F_PARENT)
            } else if raw_str_eq(key, b"timestamp") {
                Slot::Field(F_TS)
            } else if raw_str_eq(key, b"message") {
                cap.set(F_ROLE, T_ABSENT, Span::default());
                sc.tools.clear();
                Slot::Message
            } else {
                Slot::Other
            }
        }
        Ctx::Message => {
            if raw_str_eq(key, b"role") {
                Slot::Field(F_ROLE)
            } else if raw_str_eq(key, b"content") {
                sc.tools.clear();
                Slot::Content
            } else {
                Slot::Other
            }
        }
        Ctx::Block => {
            if raw_str_eq(key, b"type") {
                Slot::BlockType
            } else if raw_str_eq(key, b"id") {
                Slot::BlockId
            } else if raw_str_eq(key, b"tool_use_id") {
                Slot::BlockToolUse
            } else {
                Slot::Other
            }
        }
        Ctx::Content | Ctx::Other => Slot::Other,
    }
}

fn record(cap: &mut Capture, slot: Slot, tag: u8, sp: Span) {
    match slot {
        Slot::Field(f) => cap.set(f, tag, sp),
        Slot::BlockType => cap.btype = (tag, sp),
        Slot::BlockId => cap.bid = (tag, sp),
        Slot::BlockToolUse => cap.btuid = (tag, sp),
        _ => {}
    }
}

/// Begin a value at the cursor: a scalar is consumed whole, a container pushes its frame.
fn begin(p: &mut P, cap: &mut Capture, sc: &mut Scratch, slot: Slot) -> Option<Kind> {
    let c = *p.s.get(p.i)?;
    let none = Span::default();
    let kind = match c {
        b'"' => {
            let sp = p.string()?;
            record(cap, slot, T_STR, sp);
            Kind::String
        }
        b'{' | b'[' => {
            if sc.stack.len() >= MAX_DEPTH {
                return None; // RecursionError
            }
            p.i += 1;
            let obj = c == b'{';
            record(cap, slot, if obj { T_OBJECT } else { T_ARRAY }, none);
            let ctx = match (obj, slot) {
                (true, Slot::Top) => Ctx::Top,
                (true, Slot::Message) => Ctx::Message,
                (true, Slot::Elem) => {
                    cap.btype = (T_ABSENT, none);
                    cap.bid = (T_ABSENT, none);
                    cap.btuid = (T_ABSENT, none);
                    Ctx::Block
                }
                (false, Slot::Content) => Ctx::Content,
                _ => Ctx::Other,
            };
            sc.stack.push(Frame { obj, ctx, state: St::First, pending: Slot::Other });
            if obj { Kind::Object } else { Kind::Array }
        }
        b'n' => {
            p.lit(b"null")?;
            record(cap, slot, T_NULL, none);
            Kind::Null
        }
        b't' | b'f' => {
            p.lit(if c == b't' { b"true" } else { b"false" })?;
            record(cap, slot, T_BOOL, none);
            Kind::Bool
        }
        b'N' | b'I' => {
            p.lit(if c == b'N' { b"NaN" } else { b"Infinity" })?;
            record(cap, slot, T_NUMBER, none);
            Kind::Number
        }
        b'-' | b'0'..=b'9' => {
            if p.s[p.i..].starts_with(b"-Infinity") {
                p.i += 9;
            } else {
                p.number()?;
            }
            record(cap, slot, T_NUMBER, none);
            Kind::Number
        }
        _ => return None,
    };
    Some(kind)
}

/// Close the innermost container; a content block closing files its tool id.
fn close(cap: &mut Capture, sc: &mut Scratch, s: &[u8], plant: Option<Plant>) {
    let f = sc.stack.pop().expect("close with an open frame");
    if f.ctx != Ctx::Block || cap.btype.0 != T_STR {
        return;
    }
    let ty = &s[cap.btype.1.start as usize..cap.btype.1.end as usize];
    if raw_str_eq(ty, b"tool_use") {
        if cap.bid.0 == T_STR {
            sc.tools.push(cap.bid.1);
        }
    } else if raw_str_eq(ty, b"tool_result") && cap.btuid.0 == T_STR && plant != Some(Plant::NoToolResult) {
        sc.tools.push(cap.btuid.1);
    }
}

/// Why a chunk could not be indexed.
#[derive(Debug, PartialEq, Eq)]
pub enum ScanError {
    /// A single line of 4 GiB or more: the index keeps a line's length in 32 bits.
    LineTooLong(u64),
}

/// Index every complete line of `buf`, the file's bytes from offset `base`: kept records go to `lines` (their tool ids
/// to `tools`). Returns how many bytes of `buf` were consumed: through its last `\n`, 0 when it has none.
pub(crate) fn scan_chunk(
    buf: &[u8],
    base: u64,
    lines: &mut Vec<LineIndex>,
    tools: &mut Vec<Span>,
    sc: &mut Scratch,
    plant: Option<Plant>,
) -> Result<usize, ScanError> {
    let consumed = buf.iter().rposition(|&b| b == b'\n').map_or(0, |p| p + 1);
    let body = &buf[..consumed];
    let mut start = 0;
    let mut i = 0;
    let mut piece = |ps: usize, pe: usize, sc: &mut Scratch| -> Result<(), ScanError> {
        let len = pe - ps;
        if len > u32::MAX as usize {
            return Err(ScanError::LineTooLong(len as u64));
        }
        let raw = &body[ps..pe];
        let (a, b) = py_strip(raw, plant);
        if a == b {
            return Ok(());
        }
        if let Some(ix) = index_piece(&raw[a..b], sc, plant) {
            let lead = a as u32;
            let shift = |sp: Span| Span { start: sp.start + lead, end: sp.end + lead };
            let tools_start = tools.len() as u32;
            tools.extend(sc.tools.iter().map(|&sp| shift(sp)));
            lines.push(LineIndex {
                off: base + ps as u64,
                len: len as u32,
                kind: ix.kind,
                tags: ix.tags,
                fields: ix.fields.map(shift),
                tools_start,
                tools_len: sc.tools.len() as u32,
            });
        }
        Ok(())
    };
    while i < body.len() {
        match body[i] {
            b'\n' => {
                piece(start, i + 1, sc)?;
                i += 1;
                start = i;
            }
            b'\r' => {
                let end = if body.get(i + 1) == Some(&b'\n') { i + 2 } else { i + 1 };
                piece(start, end, sc)?;
                i = end;
                start = i;
            }
            _ => i += 1,
        }
    }
    Ok(consumed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kept(line: &[u8]) -> bool {
        let mut sc = Scratch::default();
        let (a, b) = py_strip(line, None);
        a < b && index_piece(&line[a..b], &mut sc, None).is_some()
    }

    #[test]
    fn acceptance_matches_python_json_loads() {
        // Each verdict checked against Python 3.12.11's json.loads.
        let yes: &[&[u8]] = &[
            b"{}", b"[]", b"0", b"-0", b"1e5", b"1E+5", b"-1.5e-3", b"\"x\"", b"null", b"true", b"false",
            b"NaN", b"Infinity", b"-Infinity", b"{\"a\":1,\"a\":2}", b"\"\\ud800\"", b"\"\\ud800\\u0041\"",
            b"\"\x7f\"", b"\"caf\xe9\"", b" {\"a\" : [1 , 2] } ", b"\"\\/\\b\\f\\n\\r\\t\\\"\\\\\"",
        ];
        let no: &[&[u8]] = &[
            b"\xef\xbb\xbf{}", b"{\"a\":1,}", b"[1,]", b"01", b"1.", b"-", b"\"\x01\"", b"\"\\x\"", b"\"\\u12\"",
            b"{\"a\" 1}", b"{1:2}", b"[1 2]", b"nul", b"-NaN", b"+1", b".5", b"1e", b"1e+", b"{} {}",
            b"caf\xe9", b"[\"a\"\xe9]", b"\"abc", b"{\"a\":1", b"\x1c{}", b"\"a\tb\"",
        ];
        for y in yes {
            assert!(kept(y), "Python keeps {:?}", String::from_utf8_lossy(y));
        }
        for n in no {
            assert!(!kept(n), "Python skips {:?}", String::from_utf8_lossy(n));
        }
    }

    #[test]
    fn integer_digit_limit_and_nesting_limit() {
        let int = |n: usize| "7".repeat(n).into_bytes();
        assert!(kept(&int(4300)));
        assert!(!kept(&int(4301)));
        assert!(kept(format!("-{}", "7".repeat(4300)).as_bytes()));
        assert!(kept(format!("{}.0", "7".repeat(5000)).as_bytes()), "a float has no digit limit");
        assert!(kept(format!("{}e1", "7".repeat(5000)).as_bytes()));
        let nest = |n: usize| format!("{}{}", "[".repeat(n), "]".repeat(n)).into_bytes();
        assert!(kept(&nest(MAX_DEPTH)));
        assert!(!kept(&nest(MAX_DEPTH + 1)));
    }

    #[test]
    fn strip_is_bytes_strip_not_trim_ascii() {
        assert_eq!(py_strip(b"\x0b\x0c {} \t\r\n", None), (3, 5));
        assert_eq!(py_strip(b"\x0b{}", Some(Plant::VtStrip)), (0, 3), "the plant keeps \\x0b as Rust does");
        assert_eq!(py_strip(b"\x1c{}\x85", None), (0, 4), "\\x1c and \\x85 are not bytes whitespace");
    }

    #[test]
    fn pieces_split_like_bytes_splitlines() {
        let buf = b"{\"a\":1}\r{\"b\":2}\r\n{\"c\":3}\n\n{\"d\":\x0c4}\n{\"e\":5}\r";
        let (mut lines, mut tools, mut sc) = (Vec::new(), Vec::new(), Scratch::default());
        let consumed = scan_chunk(buf, 100, &mut lines, &mut tools, &mut sc, None).unwrap();
        assert_eq!(consumed, buf.len() - b"{\"e\":5}\r".len(), "the partial after the last newline is left");
        let got: Vec<(u64, u32)> = lines.iter().map(|l| (l.off, l.len)).collect();
        assert_eq!(got, vec![(100, 8), (108, 9), (117, 8)], "\\x0c inside a line is no boundary (and invalid JSON)");
    }

    #[test]
    fn summary_capture_follows_last_wins() {
        let s = br#"{"type":"a","type":"user","message":{"role":"x","content":[{"type":"tool_use","id":"T1"}]},"message":{"content":[{"id":"T2","type":"tool_use"},{"type":"tool_result","tool_use_id":"T3"},{"type":"text","id":"no"}],"role":"assistant"}}"#;
        let mut sc = Scratch::default();
        let ix = index_piece(s, &mut sc, None).unwrap();
        let text = |sp: Span| std::str::from_utf8(&s[sp.start as usize..sp.end as usize]).unwrap().to_string();
        assert_eq!(text(ix.fields[F_TYPE]), "user");
        assert_eq!(text(ix.fields[F_ROLE]), "assistant");
        let ids: Vec<String> = sc.tools.iter().map(|&sp| text(sp)).collect();
        assert_eq!(ids, vec!["T2", "T3"]);
        let ix = index_piece(br#"{"message":{"role":"r","content":[{"type":"tool_use","id":"T"}]},"message":5}"#, &mut sc, None).unwrap();
        assert_eq!(ix.tags >> (3 * F_ROLE) & 7, T_ABSENT as u16, "a later non-object message drops the role");
        assert!(sc.tools.is_empty(), "and the tool ids");
    }

    #[test]
    fn escaped_keys_and_values_compare_decoded() {
        let s = br#"{"\u0074ype":"x","message":{"content":[{"type":"tool\u005fuse","id":"I"}]}}"#;
        let mut sc = Scratch::default();
        let ix = index_piece(s, &mut sc, None).unwrap();
        assert_eq!(ix.tags & 7, T_STR as u16);
        assert_eq!(sc.tools.len(), 1);
    }

    #[test]
    fn decode_units_keeps_lone_surrogates_and_replaces_bad_utf8() {
        let mut u = Vec::new();
        decode_units(b"a\\ud800\\u00e9\xff\xe2\x82", &mut u);
        assert_eq!(u, vec![0x61, 0xd800, 0xe9, 0xfffd, 0xfffd]);
    }
}
