//! A held record decoded on demand into `romp_json::Value`, the way Python's `json.loads` builds it: objects as dicts
//! (a repeated key keeps its first position and takes the last value), integers as `Int` or, past `i64`, `BigInt`,
//! floats correctly rounded (`1e400` is inf), `NaN` and `Infinity` as floats. Nothing is cached: the caller owns the
//! value and drops it. Also the canonical text the comparison prints (`json.dumps(record, sort_keys=True)`).
//!
//! Two limits, both refused loudly rather than guessed: a lone surrogate (`"\ud800"`), which Python keeps in a `str`
//! and a Rust `String` cannot hold (the index still keeps the record, and its summary fields print exactly), and
//! nesting past `DECODE_MAX_DEPTH`, where this recursive decoder stops while Python's limit is 9,997.

use romp_json::Value;
use std::collections::HashMap;

/// Nesting the recursive decoder follows (the index pass is iterative and follows Python's 9,997).
pub const DECODE_MAX_DEPTH: usize = 512;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    /// A string holds a lone surrogate, which `romp_json::Value::Str` cannot carry.
    LoneSurrogate,
    /// Nesting past `DECODE_MAX_DEPTH`.
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
    let v = d.value(0)?;
    d.ws();
    if d.i != s.len() {
        return Err(DecodeError::Syntax(d.i));
    }
    Ok(v)
}

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

    fn value(&mut self, depth: usize) -> Result<Value, DecodeError> {
        self.ws();
        let Some(&c) = self.s.get(self.i) else { return self.err() };
        let rest = &self.s[self.i..];
        let lit = |w: &[u8], v: Value, d: &mut Self| {
            if rest.starts_with(w) {
                d.i += w.len();
                Ok(v)
            } else {
                d.err()
            }
        };
        match c {
            b'{' | b'[' if depth >= DECODE_MAX_DEPTH => Err(DecodeError::TooDeep),
            b'{' => self.object(depth),
            b'[' => self.array(depth),
            b'"' => Ok(Value::Str(self.string()?)),
            b'n' => lit(b"null", Value::Null, self),
            b't' => lit(b"true", Value::Bool(true), self),
            b'f' => lit(b"false", Value::Bool(false), self),
            b'N' => lit(b"NaN", Value::Float(f64::NAN), self),
            b'I' => lit(b"Infinity", Value::Float(f64::INFINITY), self),
            b'-' if rest.starts_with(b"-Infinity") => {
                self.i += 9;
                Ok(Value::Float(f64::NEG_INFINITY))
            }
            b'-' | b'0'..=b'9' => self.number(),
            _ => self.err(),
        }
    }

    fn object(&mut self, depth: usize) -> Result<Value, DecodeError> {
        self.i += 1;
        let mut members: Vec<(String, Value)> = Vec::new();
        let mut index: Option<HashMap<String, usize>> = None; // built once an object is large enough to need it
        self.ws();
        if self.s.get(self.i) == Some(&b'}') {
            self.i += 1;
            return Ok(Value::Object(members));
        }
        loop {
            self.ws();
            if self.s.get(self.i) != Some(&b'"') {
                return self.err();
            }
            let key = self.string()?;
            self.eat(b':')?;
            let v = self.value(depth + 1)?;
            if index.is_none() && members.len() >= 16 {
                index = Some(members.iter().enumerate().map(|(n, (k, _))| (k.clone(), n)).collect());
            }
            let at = match &index {
                Some(ix) => ix.get(&key).copied(),
                None => members.iter().position(|(k, _)| *k == key),
            };
            match at {
                Some(n) => members[n].1 = v, // a dict keeps the first position and the last value
                None => {
                    if let Some(ix) = index.as_mut() {
                        ix.insert(key.clone(), members.len());
                    }
                    members.push((key, v));
                }
            }
            self.ws();
            match self.s.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    return Ok(Value::Object(members));
                }
                _ => return self.err(),
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<Value, DecodeError> {
        self.i += 1;
        let mut items = Vec::new();
        self.ws();
        if self.s.get(self.i) == Some(&b']') {
            self.i += 1;
            return Ok(Value::Array(items));
        }
        loop {
            items.push(self.value(depth + 1)?);
            self.ws();
            match self.s.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    return Ok(Value::Array(items));
                }
                _ => return self.err(),
            }
        }
    }

    fn string(&mut self) -> Result<String, DecodeError> {
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
            return Ok(String::from_utf8_lossy(raw).into_owned());
        }
        let mut units = Vec::with_capacity(raw.len());
        crate::scan::decode_units(raw, &mut units);
        String::from_utf16(&units).map_err(|_| DecodeError::LoneSurrogate)
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
