//! Layer 6 against Python's own entries on synthetic worlds (tests/fixtures/layer6_edge.json, written by
//! `python3 tools/rust-spike/dump_layer6.py --edge-cases <that path>`): each case is a small world in the S2 dump's
//! snapshot schema with its trees, and Python's entry for each alive session (the string build_feed memoized, decoded,
//! with its SHA-1), or the raise that failed the session.
//!
//! A case named "REFUSE ..." is one the loader must refuse; every other case must load and give every session's entry
//! byte for byte. Python's entries are read with romp-transcript's decoder, which reads numbers as Python does. The
//! fixture also carries Python's case tables, checked here on every code point.

use std::collections::HashMap;

use romp_json::Value as Out;
use romp_views::layer6::{answer_json, Build, Case, Plant};
use romp_views::{pystr, pystr_tables};
use serde_json::Value as J;

const BYTES: &[u8] = include_bytes!("fixtures/layer6_edge.json");

fn fixture() -> J {
    serde_json::from_slice(BYTES).expect("the fixture is JSON")
}

fn exact() -> Out {
    romp_transcript::decode::decode(BYTES).expect("the fixture decodes as Python reads it")
}

fn member<'a>(o: &'a Out, key: &str) -> Option<&'a Out> {
    match o {
        Out::Object(m) => m.iter().find(|(k, _)| k == key).map(|(_, v)| v),
        _ => None,
    }
}

fn items(o: &Out) -> &[Out] {
    match o {
        Out::Array(a) => a,
        _ => panic!("not an array"),
    }
}

type Loaded = (String, Result<Case, String>, Vec<Out>);

/// Each case: its name, the loaded world (Err: refused, with why), and Python's per-session expect rows.
fn cases() -> Vec<Loaded> {
    let f = fixture();
    let e = exact();
    let raw = f["cases"].as_array().expect("cases");
    let exact_cases = items(member(&e, "cases").expect("cases"));
    assert_eq!(raw.len(), exact_cases.len());
    raw.iter()
        .zip(exact_cases)
        .map(|(c, x)| {
            let name = c["name"].as_str().expect("a name").to_string();
            (name, Case::load(c), items(member(x, "expect").expect("expect")).to_vec())
        })
        .collect()
}

/// The names of the loaded cases where some session's entry under `plant` differs from Python's.
fn differing(cases: &[Loaded], plant: Option<Plant>) -> Vec<String> {
    let mut out = Vec::new();
    for (name, c, expect) in cases {
        let Ok(c) = c else { continue };
        let got = Build::new(&c.snapshot, &c.trees, plant).entries();
        assert_eq!(got.len(), expect.len(), "{name}: one entry per alive session");
        let differs = got.iter().zip(expect).any(|((sid, g), want)| {
            assert_eq!(member(want, "sid"), Some(&Out::Str(sid.clone())), "{name}: alive order");
            romp_json::dumps(&answer_json(g)) != romp_json::dumps(member(want, "answer").expect("an answer"))
        });
        if differs {
            out.push(name.clone());
        }
    }
    out
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer6-edge/1");
    assert_eq!(f["generatedBy"], "tools/rust-spike/dump_layer6.py --edge-cases");
}

#[test]
fn every_case_answers_as_python_does_byte_for_byte() {
    let all = cases();
    let (mut loaded, mut refused, mut sessions, mut raised) = (0, 0, 0, 0);
    for (name, c, expect) in &all {
        match c {
            Err(why) => {
                assert!(name.starts_with("REFUSE "), "{name}: refused at load: {why}");
                refused += 1;
            }
            Ok(c) => {
                assert!(!name.starts_with("REFUSE "), "{name}: the loader accepted it");
                loaded += 1;
                for ((sid, got), want) in Build::new(&c.snapshot, &c.trees, None).entries().iter().zip(expect) {
                    sessions += 1;
                    let got_s = romp_json::dumps(&answer_json(got));
                    let want_answer = member(want, "answer").expect("an answer");
                    assert_eq!(got_s, romp_json::dumps(want_answer), "{name} {sid}");
                    match member(want, "sha1") {
                        Some(Out::Str(h)) => assert_eq!(&romp_views::sha1::hex(got_s.as_bytes()), h, "{name} {sid}"),
                        _ => {
                            assert!(member(want_answer, "raises").is_some(), "{name} {sid}: only a raise has no SHA-1");
                            raised += 1;
                        }
                    }
                }
            }
        }
    }
    assert_eq!((loaded + refused, refused), (all.len(), 21), "every REFUSE case is refused, every other loads");
    assert!(sessions >= 300 && raised >= 30, "{sessions} sessions, {raised} raising");
}

#[test]
fn each_plant_is_caught_on_the_cases_built_for_it() {
    let all = cases();
    assert!(differing(&all, None).is_empty());
    let want: [(Plant, &[&str]); 6] = [
        (Plant::AnchorStoreOrder, &[
            "awaiting: the tasks unrecorded raise before the tree is flattened (the table keeps nothing of it)",
            "cleared: a cleared top never writes the anchor table (the next session's cold lookup finds nothing)",
            "delegation: a pure-delegation top never writes the anchor table",
            "wait: the raise on a node without t comes before its flatten (the next session's cold lookup finds nothing)",
            "anchors: the same, store order reversed from flatten order",
            "anchors: a hidden node never writes the table",
            "anchors: an orphan and a cycle are never flattened",
        ]),
        (Plant::FsubmaxLast, &[
            "delegation: every open leaf a handoff, the top open: awaiting delegated to the peers, names sorted",
            "time: _fsubmax: mt, else t, and the first of equal values (5 before 5.0)",
            "time: _fsubmax: a child's later equal value does not replace the node's",
        ]),
        (Plant::MtOr, &[
            "time: _fsubmax: mt, else t, and the first of equal values (5 before 5.0)",
            "row: a present mt of 0 is kept, not t",
        ]),
        (Plant::LandFirstEqual, &[
            "landing: of two trail segments with equal times, the last one counted wins",
            "landing: the same, the order of the trail reversed",
        ]),
        (Plant::PeersUnsorted, &[
            "delegation: two peers the registry names alike are one name",
            "tracked: a completed or cleared tracker is not tracked",
            "peers: sorted, across the awaiting signal, stamps, handoff rows and origins",
        ]),
        (Plant::BornEmptyKept, &[
            "un-nest: a hidden top whose record is only hidden comes back with no face",
            "born: an empty born dict on a node is null, its face is null",
            "born: an empty heal record is null in the row",
        ]),
    ];
    for (plant, names) in want {
        assert_eq!(differing(&all, Some(plant)), names, "{}", plant.name());
    }
}

#[test]
fn the_case_tables_are_pythons_on_every_code_point() {
    // Python's `str.upper()` (the awaiting placeholder's headline) from the generated table; and Rust's own lowercase,
    // which the blocked placeholder compares with "permission": it lowers a code point to ASCII letters only exactly
    // where Python's `lower()` does, and to the same letters, so the comparison cannot tell the two apart
    let f = fixture();
    let t = &f["tables"];
    assert_eq!(t["unicode"], pystr_tables::UNICODE_VERSION);
    let pairs = |k: &str| -> HashMap<u32, String> {
        t[k].as_array()
            .expect("a table")
            .iter()
            .map(|p| (u32::try_from(p[0].as_u64().unwrap()).unwrap(), p[1].as_str().unwrap().to_string()))
            .collect()
    };
    let (upper, lower_ascii) = (pairs("upper"), pairs("lowerAscii"));
    let mut checked = 0;
    for c in (0..=0x10FFFFu32).filter_map(char::from_u32) {
        let u = c as u32;
        let one = c.to_string();
        assert_eq!(pystr::upper(&one), upper.get(&u).cloned().unwrap_or_else(|| one.clone()), "upper {u:#x}");
        let low: String = c.to_lowercase().collect();
        let ascii = low.bytes().all(|b| b.is_ascii_lowercase()).then_some(low);
        assert_eq!(ascii, lower_ascii.get(&u).cloned(), "lower {u:#x}");
        checked += 1;
    }
    assert_eq!(checked, 0x110000 - 0x800);
    assert_eq!((upper.len(), lower_ascii.len()), (1525, 53));
}

#[test]
fn the_line_trace_misses_only_lines_no_admitted_input_reaches() {
    // _closure_done's absent node and _block_check_floor's memo hit (every caller passes a node of the tree, each
    // visited once), and the parse reads inside the two placeholders' try (the replay serves the same tree as ps)
    let f = fixture();
    let lc = &f["lineCoverage"];
    let missed = lc["missed"].as_object().expect("missed");
    let count = |k: &str| missed.get(k).and_then(J::as_array).map_or(0, Vec::len);
    assert_eq!(count("kernel._feed_session_entry"), 3);
    assert_eq!(count("kernel._blocked_placeholder"), 2);
    assert_eq!(count("kernel._awaiting_card"), 2);
    assert_eq!(count("kernel._card_warn_rows"), 0);
    assert_eq!(lc["lines"].as_u64().unwrap() - lc["ran"].as_u64().unwrap(), 7);
}
