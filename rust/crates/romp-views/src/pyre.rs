//! Python's `re` where its classes and its ignore-case matching differ from anything Rust's standard library offers,
//! for layer 5's hand-written matchers (rust/SPIKE.md, S4.5). The tables (`pyre_tables.rs`) are generated from
//! Python's own engine (`python3 tools/rust-spike/dump_layer5.py --tables`), and `tests/layer5_edge.rs` checks every
//! code point against the tables Python writes into the layer-5 fixture, so a drift on either side fails a test.
//!
//! - `\d` in a str pattern is any Unicode decimal digit (category Nd: `١`, `１`), not only ASCII and not every
//!   numeric character (`²` is not one), so neither `is_ascii_digit` nor `is_numeric` reads it.
//! - `\w` is `str.isalnum()` or `_`. Rust's `is_alphanumeric` also takes the Other_Alphabetic marks (U+0345, the
//!   Devanagari vowel sign U+093E), which Python's `\w` does not, and both follow their own Unicode versions.
//! - Under `re.I` a literal matches one code point at a time (sre_compile's LITERAL with IGNORECASE): an uncased
//!   pattern character only itself; a cased one any subject character whose simple lowercase (`_sre.unicode_tolower`)
//!   is the pattern's, or one of `re._casefix._EXTRA_CASES` for it. So the Kelvin sign (U+212A) matches `k` (its
//!   lowercase), and through the extra cases the long s (U+017F) matches `s`, the dotless ı matches `i`, the final ς
//!   matches `σ` and the micro sign matches `μ`; `İ` (U+0130) matches `i`, its simple lowercase. A comparison of
//!   lowercased characters gets all of these but the Kelvin sign wrong (Rust lowers `İ` to two code points).
//! - `\s` is `pystr::is_space`.

use crate::pyre_tables::{CASED, DECIMAL, EXTRA_CASES, LOWER, WORD};

fn in_ranges(table: &[(u32, u32)], c: char) -> bool {
    let c = c as u32;
    match table.binary_search_by(|&(lo, _)| lo.cmp(&c)) {
        Ok(_) => true,
        Err(0) => false,
        Err(i) => c <= table[i - 1].1,
    }
}

/// What `\d` matches in a Python str regex.
pub fn is_decimal(c: char) -> bool {
    in_ranges(DECIMAL, c)
}

/// What `\w` matches in a Python str regex.
pub fn is_word(c: char) -> bool {
    in_ranges(WORD, c)
}

/// `_sre.unicode_iscased(c)`.
pub fn is_cased(c: char) -> bool {
    in_ranges(CASED, c)
}

/// `_sre.unicode_tolower(c)`: the simple lowercase mapping (`İ` lowers to `i`, one code point).
pub fn lower(c: char) -> char {
    let u = c as u32;
    let i = match LOWER.binary_search_by(|&(first, ..)| first.cmp(&u)) {
        Ok(i) => i,
        Err(0) => return c,
        Err(i) => i - 1,
    };
    let (first, last, step, delta) = LOWER[i];
    if u > last || (step > 0 && (u - first) % step != 0) || (step == 0 && u != first) {
        return c;
    }
    char::from_u32(u.wrapping_add_signed(delta)).expect("a lowercase mapping is a code point")
}

/// The extra code points an ignore-case literal whose lowercase is `lo` accepts (`re._casefix._EXTRA_CASES[lo]`).
pub fn extra_cases(lo: char) -> &'static [u32] {
    match EXTRA_CASES.binary_search_by(|&(k, _)| k.cmp(&(lo as u32))) {
        Ok(i) => EXTRA_CASES[i].1,
        Err(_) => &[],
    }
}

/// Whether the subject character `s` matches the pattern literal `p` under `re.I` (a str pattern).
pub fn ignorecase_eq(p: char, s: char) -> bool {
    if !is_cased(p) {
        return s == p;
    }
    let lo = lower(p);
    let ls = lower(s);
    ls == lo || extra_cases(lo).contains(&(ls as u32))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digits_are_unicode_decimals_only() {
        for c in ['0', '9', '\u{661}', '\u{ff11}', '\u{1d7ce}'] {
            assert!(is_decimal(c), "{c:?}");
        }
        for c in ['a', '\u{b2}', '\u{2160}', '\u{bc}', '/'] {
            assert!(!is_decimal(c), "{c:?}");
        }
    }

    #[test]
    fn word_is_pythons_not_rusts() {
        for c in ['a', 'Z', '_', '0', '\u{e9}', '\u{b2}', '\u{2160}', '\u{4e00}'] {
            assert!(is_word(c), "{c:?}");
        }
        // Other_Alphabetic marks are alphanumeric to Rust and not \w to Python
        for c in ['\u{93e}', '\u{345}'] {
            assert!(c.is_alphanumeric() && !is_word(c), "{c:?}");
        }
        for c in [':', '-', ' ', '/', '\u{200b}'] {
            assert!(!is_word(c), "{c:?}");
        }
    }

    #[test]
    fn ignorecase_follows_sre() {
        assert!(ignorecase_eq('k', '\u{212a}') && ignorecase_eq('\u{212a}', 'K') && ignorecase_eq('K', 'k'));
        assert!(ignorecase_eq('s', '\u{17f}') && ignorecase_eq('\u{17f}', 'S'));
        assert!(ignorecase_eq('\u{1e9e}', '\u{df}') && ignorecase_eq('\u{df}', '\u{1e9e}'));
        assert!(ignorecase_eq('i', '\u{130}') && ignorecase_eq('i', '\u{131}') && ignorecase_eq('\u{131}', 'I'));
        assert!(ignorecase_eq('\u{3c3}', '\u{3c2}') && ignorecase_eq('\u{3a3}', '\u{3c2}'));
        assert!(ignorecase_eq('\u{b5}', '\u{39c}') && ignorecase_eq('\u{3bc}', '\u{b5}'));
        assert!(!ignorecase_eq('.', ',') && ignorecase_eq('.', '.') && !ignorecase_eq('a', 'b'));
        assert_eq!((lower('\u{130}'), lower('A'), lower('a'), lower('\u{1e9e}')), ('i', 'a', 'a', '\u{df}'));
    }
}
