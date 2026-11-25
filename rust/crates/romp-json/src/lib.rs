//! JSON written exactly as Python's default `json.dumps` writes it (rust/SPIKE.md, step S1).
//!
//! The feed the browser receives is `json.dumps(obj)` with the defaults: `", "` and `": "` separators,
//! `ensure_ascii=True` (every non-ASCII character escaped as `\uXXXX`, a surrogate pair above U+FFFF), the short
//! escapes `\" \\ \n \r \t \b \f` and `\u00XX` for the other control characters, floats as Python's `repr` writes
//! them (`5.0`, `1e-05`, `1e+16`, `NaN`, `Infinity`), integers of any size, and keys in insertion order. Matching
//! those bytes lets the spike compare the Rust feed with the Python one byte for byte, and lets a later delta
//! decision that compares payload lengths choose the same way.
//!
//! Known limit: a Rust `String` cannot hold a lone surrogate, which a Python `str` decoded from `"\ud800"` can.
//! Such text is out of scope here; the transcript layer decides how to carry it.

use std::fmt::Write as _;

/// A JSON value with object keys kept in insertion order, as a Python `dict` keeps them.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    /// An integer that fits in an `i64`.
    Int(i64),
    /// An integer outside `i64`, kept as its decimal digits (with a leading `-` when negative), as Python's
    /// arbitrary-precision `int` prints.
    BigInt(String),
    Float(f64),
    Str(String),
    Array(Vec<Value>),
    Object(Vec<(String, Value)>),
}

/// `json.dumps(value)` with Python's defaults.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value);
    out
}

/// Append `json.dumps(value)` to `out`.
pub fn write_value(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(i) => {
            let _ = write!(out, "{i}");
        }
        Value::BigInt(digits) => out.push_str(digits),
        Value::Float(f) => write_float(out, *f),
        Value::Str(s) => write_str(out, s),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_value(out, item);
            }
            out.push(']');
        }
        Value::Object(members) => {
            out.push('{');
            for (i, (key, item)) in members.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_str(out, key);
                out.push_str(": ");
                write_value(out, item);
            }
            out.push('}');
        }
    }
}

/// A string as `json.dumps` writes it with `ensure_ascii=True`.
pub fn write_str(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0C}' => out.push_str("\\f"),
            ' '..='~' => out.push(ch),
            _ => {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{:04x}", unit);
                }
            }
        }
    }
    out.push('"');
}

/// A float as `json.dumps` writes it: `float.__repr__`, with `NaN`, `Infinity` and `-Infinity` for the values
/// JSON has no literal for (`allow_nan=True`, the default).
pub fn write_float(out: &mut String, f: f64) {
    if f.is_nan() {
        out.push_str("NaN");
        return;
    }
    if f.is_infinite() {
        out.push_str(if f > 0.0 { "Infinity" } else { "-Infinity" });
        return;
    }
    // Python's repr is the shortest digit string that round-trips, correctly rounded: when two strings of that length
    // both round-trip and sit equally close to the float (an exact tie, e.g. -271556098175374.125 at 17 digits), it
    // keeps the even digit (...374.12). Rust's `{:e}` finds the shortest length but rounds such a tie up (...374.13),
    // so it supplies only the length, and the exact formatter (`{:.Ne}`, which rounds half to even) the digits.
    let shortest = format!("{:e}", f);
    let n = shortest.split('e').next().map_or(1, |m| m.bytes().filter(u8::is_ascii_digit).count());
    let sci = format!("{:.*e}", n.saturating_sub(1), f);
    let (mantissa, exp) = sci.split_once('e').expect("LowerExp always writes an exponent");
    let exp: i32 = exp.parse().expect("LowerExp writes a decimal exponent");
    let (negative, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if negative {
        out.push('-');
    }
    // Python's repr (format code 'r') uses positional notation when -4 <= exponent < 16, else scientific.
    if (-4..16).contains(&exp) {
        if exp < 0 {
            out.push_str("0.");
            for _ in 0..(-exp - 1) {
                out.push('0');
            }
            out.push_str(&digits);
        } else {
            let int_len = (exp + 1) as usize;
            if digits.len() <= int_len {
                out.push_str(&digits);
                for _ in digits.len()..int_len {
                    out.push('0');
                }
                out.push_str(".0");
            } else {
                out.push_str(&digits[..int_len]);
                out.push('.');
                out.push_str(&digits[int_len..]);
            }
        }
    } else {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let _ = write!(out, "e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn obj(members: Vec<(&str, Value)>) -> Value {
        Value::Object(members.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }

    #[test]
    fn separators_and_insertion_order_are_pythons() {
        let v = obj(vec![("b", Value::Int(1)), ("a", Value::Array(vec![Value::Null, Value::Bool(true)]))]);
        assert_eq!(dumps(&v), r#"{"b": 1, "a": [null, true]}"#);
    }

    #[test]
    fn floats_follow_reprs_two_notations() {
        let cases = [
            (5.0, "5.0"),
            (0.1, "0.1"),
            (1e-05, "1e-05"),
            (0.0001, "0.0001"),
            (1e16, "1e+16"),
            (1234567890123456.0, "1234567890123456.0"),
            (-0.0, "-0.0"),
            (1.5e300, "1.5e+300"),
        ];
        for (f, want) in cases {
            assert_eq!(dumps(&Value::Float(f)), want, "{f:e}");
        }
    }

    #[test]
    fn non_ascii_is_escaped_with_surrogate_pairs() {
        // U+00E9, U+1F600 (a surrogate pair in UTF-16) and U+0001; the expected text is built from a backslash
        // character so no tool or editor can turn its escapes into the characters themselves.
        let input: String = ['\u{e9}', '\u{1f600}', '\u{1}'].iter().collect();
        let bs = '\\';
        let want = format!("\"{bs}u00e9{bs}ud83d{bs}ude00{bs}u0001\"");
        assert_eq!(dumps(&Value::Str(input)), want);
    }
}
