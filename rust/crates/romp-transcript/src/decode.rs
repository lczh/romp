//! A held record decoded on demand into `romp_json::Value`, the way Python's `json.loads` builds it: objects as dicts
//! (a repeated key keeps its first position and takes the last value), integers as `Int` or, past `i64`, `BigInt`,
//! floats correctly rounded (`1e400` is inf), `NaN` and `Infinity` as floats. Nothing is cached: the caller owns the
//! value and drops it. Also the canonical text the comparison prints (`json.dumps(record, sort_keys=True)`).
//!
//! Two limits, both refused loudly rather than guessed, and only when the record Python builds has them: a lone
//! surrogate (`"\ud800"`) in a string or a key, which Python keeps in a `str` and a Rust `String` cannot hold (the
//! index still keeps the record, and its summary fields print exactly), and nesting past `DECODE_MAX_DEPTH`, where
//! this recursive decoder stops while Python's limit is 9,997. A value under a repeated key that a later one replaces
//! is not in Python's record, so it cannot make the record undecodable: such a value is decoded to a placeholder
//! marked with what it held (a deep one is skipped by an iterative pass that still checks its syntax), the marks
//! follow the last-value rule up the tree, and only a mark that survives to the root refuses the record.

use romp_json::Value;
use std::collections::HashMap;

/// Nesting the recursive decoder follows (the index pass is iterative and follows Python's 9,997).
pub const DECODE_MAX_DEPTH: usize = 512;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    /// The record holds a lone surrogate in a string or a key, which `romp_json::Value::Str` cannot carry.
    LoneSurrogate,
    /// The record nests past `DECODE_MAX_DEPTH`.
    TooDeep,
    /// Not JSON as Python reads it (never for a record the index kept).
    Syntax(usize),
}

impl DecodeError {
    pub fn reason(&self) -> &'static str {
        match self {
            DecodeError::LoneSurrogate => "lone surrogate",
            DecodeError::TooDeep => "nesting too deep",
            DecodeError::Syntax(_) => "syntax",
        }
    }
}

/// Decode one record's JSON text (a stripped piece, raw bytes as the file holds them).
pub fn decode(s: &[u8]) -> Result<Value, DecodeError> {
    let mut d = D { s, i: 0 };
    d.ws();
    let (v, marks) = d.value(0)?;
    d.ws();
    if d.i != s.len() {
        return Err(DecodeError::Syntax(d.i));
    }
    // Only what survived the last-value rule counts; a lone surrogate is named first when a record has both.
    if marks & SURROGATE != 0 {
        return Err(DecodeError::LoneSurrogate);
    }
    if marks & DEEP != 0 {
        return Err(DecodeError::TooDeep);
    }
    Ok(v)
}

/// What a decoded subtree holds that the record Python builds would carry if the subtree survives: bits of
/// `SURROGATE`, `DEEP` and, per object member only, `KEY_SURROGATE`.
type Taint = u8;
const CLEAN: Taint = 0;
/// A string (a value or, once it reaches the object, a key) held a lone surrogate.
const SURROGATE: Taint = 1;
/// A container nested past `DECODE_MAX_DEPTH` was skipped and a placeholder stands in for it.
const DEEP: Taint = 2;
/// The member's key held a lone surrogate: it stays whatever value replaces the member's.
const KEY_SURROGATE: Taint = 4;

struct D<'a> {
    s: &'a [u8],
    i: usize,
}

impl D<'_> {
    fn ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn err<T>(&self) -> Result<T, DecodeError> {
        Err(DecodeError::Syntax(self.i))
    }

    fn eat(&mut self, c: u8) -> Result<(), DecodeError> {
        self.ws();
        if self.s.get(self.i) == Some(&c) {
            self.i += 1;
            Ok(())
        } else {
            self.err()
        }
    }

    fn value(&mut self, depth: usize) -> Result<(Value, Taint), DecodeError> {
        self.ws();
        match self.s.get(self.i) {
            Some(b'{' | b'[') if depth >= DECODE_MAX_DEPTH => {
                self.skip_deep(depth)?;
                Ok((Value::Null, DEEP)) // a placeholder: refused if it survives (decode)
            }
            Some(b'{') => self.object(depth),
            Some(b'[') => self.array(depth),
            _ => self.scalar(),
        }
    }

    /// A string, a number or a literal (never a container).
    fn scalar(&mut self) -> Result<(Value, Taint), DecodeError> {
        let Some(&c) = self.s.get(self.i) else { return self.err() };
        let rest = &self.s[self.i..];
        let lit = |w: &[u8], v: Value, d: &mut Self| {
            if rest.starts_with(w) {
                d.i += w.len();
                Ok((v, CLEAN))
            } else {
                d.err()
            }
        };
        match c {
            b'"' => {
                let (s, t) = self.string()?;
                Ok((Value::Str(s), t))
            }
            b'n' => lit(b"null", Value::Null, self),
            b't' => lit(b"true", Value::Bool(true), self),
            b'f' => lit(b"false", Value::Bool(false), self),
            b'N' => lit(b"NaN", Value::Float(f64::NAN), self),
            b'I' => lit(b"Infinity", Value::Float(f64::INFINITY), self),
            b'-' if rest.starts_with(b"-Infinity") => {
                self.i += 9;
                Ok((Value::Float(f64::NEG_INFINITY), CLEAN))
            }
            b'-' | b'0'..=b'9' => Ok((self.number()?, CLEAN)),
            _ => self.err(),
        }
    }

    fn object(&mut self, depth: usize) -> Result<(Value, Taint), DecodeError> {
        self.i += 1;
        let mut members: Vec<(String, Value)> = Vec::new();
        let mut index: Option<HashMap<String, usize>> = None; // built once an object is large enough to need it
        // Per member, what its key and its current value held (KEY_SURROGATE | SURROGATE | DEEP); empty until a member
        // is marked, which no transcript record ever is.
        let mut marks: Vec<Taint> = Vec::new();
        self.ws();
        if self.s.get(self.i) == Some(&b'}') {
            self.i += 1;
            return Ok((Value::Object(members), CLEAN));
        }
        loop {
            self.ws();
            if self.s.get(self.i) != Some(&b'"') {
                return self.err();
            }
            let (key, kt) = self.string()?;
            self.eat(b':')?;
            let (v, vt) = self.value(depth + 1)?;
            // A key's lone surrogate stays with its member whatever value comes later (Python keeps the key).
            let t = vt | if kt != CLEAN { KEY_SURROGATE } else { CLEAN };
            if index.is_none() && members.len() >= 16 {
                index = Some(members.iter().enumerate().map(|(n, (k, _))| (k.clone(), n)).collect());
            }
            let at = match &index {
                Some(ix) => ix.get(&key).copied(),
                None => members.iter().position(|(k, _)| *k == key),
            };
            let n = match at {
                Some(n) => {
                    members[n].1 = v; // a dict keeps the first position and the last value
                    n
                }
                None => {
                    if let Some(ix) = index.as_mut() {
                        ix.insert(key.clone(), members.len());
                    }
                    members.push((key, v));
                    members.len() - 1
                }
            };
            if t != CLEAN || !marks.is_empty() {
                marks.resize(members.len(), CLEAN);
                marks[n] = (marks[n] & KEY_SURROGATE) | t; // the replaced value's marks go with it
            }
            self.ws();
            match self.s.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    let all = marks.iter().fold(CLEAN, |a, &m| a | m);
                    let up = (all & !KEY_SURROGATE) | if all & KEY_SURROGATE != 0 { SURROGATE } else { CLEAN };
                    return Ok((Value::Object(members), up));
                }
                _ => return self.err(),
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<(Value, Taint), DecodeError> {
        self.i += 1;
        let mut items = Vec::new();
        let mut up = CLEAN;
        self.ws();
        if self.s.get(self.i) == Some(&b']') {
            self.i += 1;
            return Ok((Value::Array(items), up));
        }
        loop {
            let (v, t) = self.value(depth + 1)?;
            items.push(v);
            up |= t;
            self.ws();
            match self.s.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    return Ok((Value::Array(items), up));
                }
                _ => return self.err(),
            }
        }
    }

    /// Step over the container at `self.i`, opened at `depth`, without building it or recursing, checking its syntax
    /// as `value` would and Python's nesting limit (`scan::MAX_DEPTH`) over the whole record.
    fn skip_deep(&mut self, depth: usize) -> Result<(), DecodeError> {
        let mut open: Vec<u8> = Vec::new(); // the closer each open container expects
        loop {
            // A value starts here.
            self.ws();
            match self.s.get(self.i) {
                Some(&c @ (b'{' | b'[')) => {
                    if depth + open.len() >= crate::scan::MAX_DEPTH {
                        return self.err();
                    }
                    self.i += 1;
                    self.ws();
                    let close = if c == b'{' { b'}' } else { b']' };
                    if self.s.get(self.i) == Some(&close) {
                        self.i += 1; // empty: the value ends here
                    } else {
                        open.push(close);
                        if close == b'}' {
                            self.member_key()?;
                        }
                        continue;
                    }
                }
                _ => {
                    self.scalar()?;
                }
            }
            // A value ended: close containers until one takes another member.
            loop {
                let Some(&close) = open.last() else { return Ok(()) };
                self.ws();
                match self.s.get(self.i) {
                    Some(b',') => {
                        self.i += 1;
                        if close == b'}' {
                            self.member_key()?;
                        }
                        break;
                    }
                    Some(&c) if c == close => {
                        self.i += 1;
                        open.pop();
                    }
                    _ => return self.err(),
                }
            }
        }
    }

    /// An object member's key and its colon (the skip's; the key is not kept).
    fn member_key(&mut self) -> Result<(), DecodeError> {
        self.ws();
        if self.s.get(self.i) != Some(&b'"') {
            return self.err();
        }
        self.string()?;
        self.eat(b':')
    }

    /// A string, and SURROGATE when it holds a lone surrogate (then the string is a placeholder, U+FFFD for each).
    fn string(&mut self) -> Result<(String, Taint), DecodeError> {
        let start = self.i + 1;
        let mut i = start;
        loop {
            match self.s.get(i) {
                None => return self.err(),
                Some(b'"') => break,
                Some(b'\\') => i += if self.s.get(i + 1) == Some(&b'u') { 6 } else { 2 },
                Some(&c) if c < 0x20 => return self.err(),
                Some(_) => i += 1,
            }
        }
        let raw = &self.s[start..i.min(self.s.len())];
        self.i = i + 1;
        if !raw.contains(&b'\\') {
            return Ok((String::from_utf8_lossy(raw).into_owned(), CLEAN));
        }
        let mut units = Vec::with_capacity(raw.len());
        crate::scan::decode_units(raw, &mut units);
        Ok(match String::from_utf16(&units) {
            Ok(s) => (s, CLEAN),
            Err(_) => (String::from_utf16_lossy(&units), SURROGATE),
        })
    }

    fn number(&mut self) -> Result<Value, DecodeError> {
        let s = self.s;
        let start = self.i;
        let mut i = start;
        if s.get(i) == Some(&b'-') {
            i += 1;
        }
        match s.get(i) {
            Some(b'0') => i += 1,
            Some(b'1'..=b'9') => {
                while i < s.len() && s[i].is_ascii_digit() {
                    i += 1;
                }
            }
            _ => return self.err(),
        }
        let mut float = false;
        if i + 1 < s.len() && s[i] == b'.' && s[i + 1].is_ascii_digit() {
            i += 2;
            while i < s.len() && s[i].is_ascii_digit() {
                i += 1;
            }
            float = true;
        }
        if i < s.len() && (s[i] == b'e' || s[i] == b'E') {
            let mut j = i + 1;
            if j < s.len() && (s[j] == b'+' || s[j] == b'-') {
                j += 1;
            }
            if j < s.len() && s[j].is_ascii_digit() {
                while j < s.len() && s[j].is_ascii_digit() {
                    j += 1;
                }
                i = j;
                float = true;
            }
        }
        self.i = i;
        let text = std::str::from_utf8(&s[start..i]).expect("ASCII digits");
        if float {
            // Rust's parser is correctly rounded, as CPython's float() is; overflow gives inf, as float() does.
            return text.parse::<f64>().map(Value::Float).or_else(|_| self.err());
        }
        if text.len() - usize::from(text.starts_with('-')) > crate::scan::INT_MAX_DIGITS {
            return self.err();
        }
        Ok(match text.parse::<i64>() {
            Ok(n) => Value::Int(n),
            Err(_) => Value::BigInt(text.to_string()),
        })
    }
}

/// `json.dumps(value, sort_keys=True)`: Python's separators and escapes, every object's keys in code-point order
/// (Rust's `str` order is byte order, which is code-point order for UTF-8).
pub fn dumps_sorted(v: &Value) -> String {
    let mut out = String::new();
    write_sorted(&mut out, v);
    out
}

fn write_sorted(out: &mut String, v: &Value) {
    match v {
        Value::Object(members) => {
            let mut order: Vec<usize> = (0..members.len()).collect();
            order.sort_by(|&a, &b| members[a].0.cmp(&members[b].0));
            out.push('{');
            for (n, &k) in order.iter().enumerate() {
                if n > 0 {
                    out.push_str(", ");
                }
                romp_json::write_str(out, &members[k].0);
                out.push_str(": ");
                write_sorted(out, &members[k].1);
            }
            out.push('}');
        }
        Value::Array(items) => {
            out.push('[');
            for (n, item) in items.iter().enumerate() {
                if n > 0 {
                    out.push_str(", ");
                }
                write_sorted(out, item);
            }
            out.push(']');
        }
        _ => romp_json::write_value(out, v),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn canon(s: &[u8]) -> String {
        dumps_sorted(&decode(s).unwrap())
    }

    #[test]
    fn decodes_as_python_builds_its_dict() {
        // Expected text from Python 3.12.11: json.dumps(json.loads(s), sort_keys=True).
        assert_eq!(canon(br#"{"b":1,"a":2,"b":3}"#), r#"{"a": 2, "b": 3}"#);
        assert_eq!(canon(b"[NaN,Infinity,-Infinity,1e400,-0,-0.0,1E2]"), "[NaN, Infinity, -Infinity, Infinity, 0, -0.0, 100.0]");
        assert_eq!(canon(b"[9223372036854775807,9223372036854775808,-9223372036854775809]"),
                   "[9223372036854775807, 9223372036854775808, -9223372036854775809]");
        let bs = '\\'; // the expected escapes are built from a backslash so no editor turns them into characters
        assert_eq!(canon("{\"t\":\"caf\u{e9} \\ud83d\\ude00 \\u00e9\\/\"}".as_bytes()),
                   format!("{{\"t\": \"caf{bs}u00e9 {bs}ud83d{bs}ude00 {bs}u00e9/\"}}"));
        assert_eq!(canon(b"\"a\xffb\""), format!("\"a{bs}ufffdb\""));
        assert_eq!(canon(b"0.1"), "0.1");
    }

    #[test]
    fn refuses_what_a_rust_string_cannot_hold() {
        assert_eq!(decode(br#"{"a":"\ud800"}"#), Err(DecodeError::LoneSurrogate));
        let deep = format!("{}{}", "[".repeat(DECODE_MAX_DEPTH + 1), "]".repeat(DECODE_MAX_DEPTH + 1));
        assert_eq!(decode(deep.as_bytes()), Err(DecodeError::TooDeep));
    }

    fn deep_list(n: usize) -> String {
        format!("{}{}", "[".repeat(n), "]".repeat(n))
    }

    #[test]
    fn only_what_survives_a_repeated_key_can_refuse_the_record() {
        // Expected text from Python 3.12.11: json.dumps(json.loads(s), sort_keys=True). The replaced value held the
        // lone surrogate or the deep nesting; Python's record does not.
        assert_eq!(canon(br#"{"type": "\ud800", "type": "ok"}"#), r#"{"type": "ok"}"#);
        assert_eq!(canon(format!(r#"{{"a": {}, "a": 1}}"#, deep_list(600)).as_bytes()), r#"{"a": 1}"#);
        assert_eq!(canon(br#"{"message": {"role": "\udc00"}, "message": {"role": "user"}}"#), r#"{"message": {"role": "user"}}"#);
        assert_eq!(canon(br#"{"m": {"\udc00": 1}, "m": 2}"#), r#"{"m": 2}"#, "a key under a replaced value");
        assert_eq!(canon(br#"{"a": [1, "\ud800"], "a": [2]}"#), r#"{"a": [2]}"#);
        // A deep object, skipped with its keys checked, then replaced.
        let deep_obj = format!("{}1{}", "{\"k\": ".repeat(600), "}".repeat(600));
        assert_eq!(canon(format!(r#"{{"a": {deep_obj}, "b": 0, "a": null}}"#).as_bytes()), r#"{"a": null, "b": 0}"#);
        // The same in an object large enough for the key index.
        let mut big = String::from(r#"{"k0": "\ud800""#);
        for k in 1..40 {
            big.push_str(&format!(r#", "k{k}": {k}"#));
        }
        big.push_str(r#", "k0": "ok"}"#);
        let Value::Object(m) = decode(big.as_bytes()).unwrap() else { panic!() };
        assert_eq!((m.len(), &m[0]), (40, &("k0".to_string(), Value::Str("ok".to_string()))));
    }

    #[test]
    fn what_survives_still_refuses_the_record() {
        assert_eq!(decode(br#"{"type": "ok", "type": "\ud800"}"#), Err(DecodeError::LoneSurrogate));
        assert_eq!(decode(format!(r#"{{"a": 1, "a": {}}}"#, deep_list(600)).as_bytes()), Err(DecodeError::TooDeep));
        assert_eq!(decode(br#"[1, "\ud800"]"#), Err(DecodeError::LoneSurrogate));
        // A key's lone surrogate stays with its member whatever value replaces the member's.
        assert_eq!(decode(br#"{"\ud800": 1}"#), Err(DecodeError::LoneSurrogate));
        assert_eq!(decode(br#"{"\ud800": "\ud800", "\ud800": "ok"}"#), Err(DecodeError::LoneSurrogate));
        assert_eq!(decode(br#"{"a": {"\udc00": 1}}"#), Err(DecodeError::LoneSurrogate));
        // Both: the lone surrogate is named.
        assert_eq!(decode(format!(r#"[{}, "\ud800"]"#, deep_list(600)).as_bytes()), Err(DecodeError::LoneSurrogate));
    }

    #[test]
    fn the_skip_checks_syntax_and_python_s_nesting_limit() {
        // A syntax error inside a skipped value is an error even when a later key would replace it.
        let broken = format!(r#"{{"a": {}1,{}, "a": 1}}"#, "[".repeat(600), "]".repeat(600));
        assert!(matches!(decode(broken.as_bytes()), Err(DecodeError::Syntax(_))));
        let bad_key = format!(r#"{{"a": {}{{1: 2}}{}, "a": 1}}"#, "[".repeat(600), "]".repeat(600));
        assert!(matches!(decode(bad_key.as_bytes()), Err(DecodeError::Syntax(_))));
        // Python reads 9,997 levels and refuses 9,998 (scan.rs); the skip counts the same.
        assert_eq!(decode(deep_list(crate::scan::MAX_DEPTH).as_bytes()), Err(DecodeError::TooDeep));
        assert!(matches!(decode(deep_list(crate::scan::MAX_DEPTH + 1).as_bytes()), Err(DecodeError::Syntax(_))));
        let over = format!(r#"{{"a": {}, "a": 1}}"#, deep_list(crate::scan::MAX_DEPTH));
        assert!(matches!(decode(over.as_bytes()), Err(DecodeError::Syntax(_))), "9,998 levels counting the object");
        // The decoder's own limit: 512 levels decode, 513 do not.
        assert!(decode(deep_list(DECODE_MAX_DEPTH).as_bytes()).is_ok());
        assert_eq!(decode(deep_list(DECODE_MAX_DEPTH + 1).as_bytes()), Err(DecodeError::TooDeep));
    }

    #[test]
    fn a_large_object_keeps_dict_semantics() {
        let mut s = String::from("{");
        for k in 0..40 {
            s.push_str(&format!("\"k{k}\":{k},"));
        }
        s.push_str("\"k3\":\"last\"}");
        let v = decode(s.as_bytes()).unwrap();
        let Value::Object(m) = v else { panic!() };
        assert_eq!(m.len(), 40);
        assert_eq!(m[3], ("k3".to_string(), Value::Str("last".to_string())));
    }
}
