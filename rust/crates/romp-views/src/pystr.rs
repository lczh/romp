//! Python's string semantics where they differ from Rust's: `str.isspace` (and so `strip()` and a str regex's `\s`)
//! counts U+001C..U+001F as whitespace, which Rust's `char::is_whitespace` does not, and `len()` counts code points,
//! not UTF-8 bytes (the boundary atom is 79 code points and 82 bytes).

/// `ch.isspace()`, and what `\s` matches in a Python str regex (both are `Py_UNICODE_ISSPACE`): Unicode White_Space
/// plus the four information separators U+001C..U+001F (bidirectional class B or S). The unit test checks every
/// code point against the list Python wrote into the layer-1 fixture.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `s.strip()`.
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `len(s)`: code points.
pub fn len(s: &str) -> usize {
    s.chars().count()
}

/// `s[start:]` in whitespace terms: the rest of `s` after its leading `\s*`.
pub fn skip_space(s: &str) -> &str {
    s.trim_start_matches(is_space)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn separators_are_space_in_python_but_not_in_rust() {
        let s = "\u{1c}\u{1f} x \u{1d}";
        assert_eq!(s.trim(), s, "Rust's trim keeps U+001C..U+001F");
        assert_eq!(strip(s), "x");
        assert_eq!(strip("\u{a0}\u{2003}x\u{3000}\u{85}"), "x");
        assert_eq!(strip("\u{200b}x"), "\u{200b}x", "a zero-width space is not whitespace in either");
    }

    #[test]
    fn len_counts_code_points() {
        let boundary = "Recap: limit \u{2192} 3, the na\u{ef}ve cache is gone; the search suite passes, once again.";
        assert_eq!(len(boundary), 79);
        assert_eq!(boundary.len(), 82);
        assert_eq!(len("\u{1f600}"), 1);
    }
}
