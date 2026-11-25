//! Layer 1 against Python's own answers on synthetic cases (tests/fixtures/layer1_edge.json, written by
//! `python3 tools/rust-spike/dump_layer1.py --edge-cases <that path>`): every atom function on 80 atoms, em.turn_scalar
//! on 11 turns, both seg-key functions on 18 ids, Python's whitespace on every code point, and the constants.
//!
//! A case named "REFUSE ..." is one the loader must refuse; every case where a Python function raises must be one.

use romp_json::Value as Out;
use romp_views::layer1::{self, ATOM_COLUMNS, SEG_COLUMNS, TURN_KEYS};
use romp_views::record::ToJson;
use romp_views::tree::{self, Atom, Turn};
use romp_views::{pystr, sha1};
use serde_json::Value as J;

fn fixture() -> J {
    serde_json::from_str(include_str!("fixtures/layer1_edge.json")).expect("the fixture is JSON")
}

fn names(v: &J) -> Vec<&str> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect()
}

fn load_atom(v: &J) -> Result<Atom, String> {
    let a: Atom = serde_json::from_value(v.clone()).map_err(|e| e.to_string())?;
    tree::validate_atom(&a)?;
    Ok(a)
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer1-edge/1");
    let rust: Vec<&str> = ATOM_COLUMNS.iter().map(|(n, _)| *n).collect();
    assert_eq!(names(&f["atomColumns"]), rust);
    let turns: Vec<String> = TURN_KEYS.iter().map(|k| format!("em.turn_scalar:{k}")).collect();
    assert_eq!(names(&f["turnColumns"]), turns);
    let segs: Vec<&str> = std::iter::once("input").chain(SEG_COLUMNS.iter().map(|(n, _)| *n)).collect();
    assert_eq!(names(&f["segColumns"]), segs);
}

#[test]
fn every_atom_function_answers_as_python_does() {
    let f = fixture();
    let mut compared = 0;
    let mut diffs = Vec::new();
    let mut refused = 0;
    for case in f["atoms"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let expect = case["expect"].as_array().unwrap();
        let python_raises = expect.iter().any(|v| v.get("raises").is_some());
        match load_atom(&case["atom"]) {
            Err(e) => {
                assert!(name.starts_with("REFUSE "), "{name}: refused but not a REFUSE case: {e}");
                refused += 1;
            }
            Ok(a) => {
                assert!(!name.starts_with("REFUSE "), "{name}: the loader accepted it");
                assert!(!python_raises, "{name}: Python raises on it, so the loader must refuse it");
                for ((col, func), want) in ATOM_COLUMNS.iter().zip(expect) {
                    compared += 1;
                    let got = func(&a);
                    if got != want.to_json() {
                        diffs.push(format!("{name}: {col}: rust {} python {}", romp_json::dumps(&got), want));
                    }
                }
            }
        }
    }
    assert!(diffs.is_empty(), "{} differences:\n{}", diffs.len(), diffs.join("\n"));
    assert_eq!(refused, 13, "the REFUSE cases");
    assert_eq!(compared, 67 * ATOM_COLUMNS.len());
}

#[test]
fn turn_scalar_answers_as_python_does() {
    let f = fixture();
    for case in f["turns"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let t: Turn = serde_json::from_value(case["turn"].clone()).unwrap();
        for (k, want) in TURN_KEYS.iter().zip(case["expect"].as_array().unwrap()) {
            assert_eq!(layer1::turn_scalar(&t, k), want.to_json(), "{name}: turn_scalar(turn, {k:?})");
        }
    }
}

#[test]
fn seg_keys_answer_as_python_does() {
    let f = fixture();
    for row in f["segKeys"].as_array().unwrap() {
        let row = row.as_array().unwrap();
        let input = row[0].as_str();
        for ((col, func), want) in SEG_COLUMNS.iter().zip(&row[1..]) {
            assert_eq!(func(input), want.to_json(), "{col}({:?})", row[0]);
        }
    }
}

#[test]
fn whitespace_is_pythons_on_every_code_point() {
    let f = fixture();
    let python: std::collections::BTreeSet<u32> =
        f["isspace"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as u32).collect();
    let rust: std::collections::BTreeSet<u32> =
        (0..=0x10FFFFu32).filter_map(char::from_u32).filter(|c| pystr::is_space(*c)).map(u32::from).collect();
    assert_eq!(rust, python);
    let rust_trim: std::collections::BTreeSet<u32> =
        (0..=0x10FFFFu32).filter_map(char::from_u32).filter(|c| c.is_whitespace()).map(u32::from).collect();
    let missing: Vec<u32> = python.difference(&rust_trim).copied().collect();
    assert_eq!(missing, [0x1c, 0x1d, 0x1e, 0x1f], "what Rust's own is_whitespace misses");
}

#[test]
fn the_constants_are_pythons() {
    let f = fixture();
    let c = &f["constants"];
    assert_eq!(names(&c["END_STOPS"]), layer1::END_STOPS);
    assert_eq!(c["CITE_MIN_CHARS"].as_u64(), Some(layer1::CITE_MIN_CHARS as u64));
    assert_eq!(c["SETTLE_TEXT_H8"].as_str(), Some(sha1::hex8(layer1::SETTLE_TEXT.as_bytes()).as_str()));
}

#[test]
fn a_planted_byte_count_is_caught() {
    // The comparison above must be able to fail: a port that counts prose in UTF-8 bytes differs from Python on the
    // boundary atom (82 against 79) and on every other non-ASCII prose case.
    let f = fixture();
    let col = ATOM_COLUMNS.iter().position(|(n, _)| *n == "em.atom_prose_chars").unwrap();
    let mut caught = Vec::new();
    for case in f["atoms"].as_array().unwrap() {
        let Ok(a) = load_atom(&case["atom"]) else { continue };
        let bytes = if a.kind() == "assistant" && !matches!(a.is_api_error, Some(Some(true))) {
            layer1::content(a.message()).texts().map(str::len).sum::<usize>()
        } else {
            0
        };
        let bytes = if matches!(a.message().and_then(|m| m.content.as_ref()), Some(tree::Content::Text(_))) {
            0
        } else {
            bytes
        };
        if Out::Int(bytes as i64) != case["expect"][col].to_json() {
            caught.push(case["name"].as_str().unwrap().to_string());
        }
    }
    assert!(caught.contains(&"boundary atom".to_string()), "{caught:?}");
    assert_eq!(caught, ["boundary atom", "boundary atom plus one", "astral and combining"]);
}
