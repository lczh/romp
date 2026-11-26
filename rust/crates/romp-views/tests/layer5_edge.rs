//! Layer 5 against Python's own answers on synthetic cases (tests/fixtures/layer5_edge.json, written by
//! `python3 tools/rust-spike/dump_layer5.py --edge-cases <that path>`): sessions answered as the per-card build reads
//! layer 5 (the provisional card over every gate, each segment's predicates, prompt, placements and caption, each
//! atom's prompt record, and per node _card_gone, _dictated_prompt_uuid, _pure_delegation_top, _summary_outrun and the
//! summary landing), direct calls of every function with a text input, and Python's own regex tables.
//!
//! A case named "REFUSE ..." is one the loader must refuse; every other case must load and answer as Python did.
//! Python's answers are read with romp-transcript's decoder, which keeps an integer past u64 an integer.

use romp_json::Value as Out;
use romp_views::layer5::{EdgeCase, Plant};
use romp_views::{pyre, pyre_tables};
use serde_json::Value as J;

const BYTES: &[u8] = include_bytes!("fixtures/layer5_edge.json");

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

type Case = (String, Result<EdgeCase, String>, Option<Out>);

/// Each case: its name, the loaded case (Err: refused, with why), and Python's answer.
fn cases() -> Vec<Case> {
    let f = fixture();
    let e = exact();
    let raw = f["cases"].as_array().expect("cases");
    let exact_cases = items(member(&e, "cases").expect("cases"));
    assert_eq!(raw.len(), exact_cases.len());
    raw.iter()
        .zip(exact_cases)
        .map(|(c, x)| {
            let loaded = EdgeCase::load_checked(c, x).and_then(|l| match l.refused_session() {
                Some(why) => Err(why),
                None => Ok(l),
            });
            (c["name"].as_str().expect("a name").to_string(), loaded, member(x, "expect").cloned())
        })
        .collect()
}

/// The names of the loaded cases whose answer under `plant` differs from Python's.
fn differing(cases: &[Case], plant: Option<Plant>) -> Vec<String> {
    cases
        .iter()
        .filter_map(|(n, c, want)| {
            let c = c.as_ref().ok()?;
            let want = want.as_ref().expect("a loaded case has an answer");
            (romp_json::dumps(&c.answer(plant)) != romp_json::dumps(want)).then(|| n.clone())
        })
        .collect()
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer5-edge/1");
    assert_eq!(f["generatedBy"], "tools/rust-spike/dump_layer5.py --edge-cases");
    assert_eq!(f["constants"]["CITE_MIN_CHARS"], romp_views::layer5::CITE_MIN_CHARS as u64);
}

#[test]
fn every_case_answers_as_python_does() {
    let (mut loaded, mut refused, mut refused_raises, mut sessions) = (0, 0, 0, 0);
    let mut diffs = Vec::new();
    for (n, c, want) in cases() {
        let raises = want.as_ref().and_then(|w| member(w, "raises")).is_some();
        match c {
            Err(e) => {
                assert!(n.starts_with("REFUSE "), "{n}: refused but not a REFUSE case: {e}");
                refused += 1;
                refused_raises += usize::from(raises);
            }
            Ok(c) => {
                assert!(!n.starts_with("REFUSE "), "{n}: the loader accepted it");
                loaded += 1;
                sessions += usize::from(matches!(c, EdgeCase::Session(_)));
                let got = c.answer(None);
                let want = want.expect("a loaded case has an answer");
                if romp_json::dumps(&got) != romp_json::dumps(&want) {
                    diffs.push(format!("{n}:\n  rust   {}\n  python {}", romp_json::dumps(&got),
                                       romp_json::dumps(&want)));
                }
            }
        }
    }
    assert!(diffs.is_empty(), "{} differences:\n{}", diffs.len(), diffs.join("\n"));
    assert_eq!((loaded, sessions, refused, refused_raises), (404, 161, 11, 3));
}

#[test]
fn each_plant_is_caught_on_the_cases_built_for_it() {
    let cs = cases();
    let want: [(Plant, &[&str]); 6] = [
        (Plant::Lowercase, &[
            "landing: matched only case-insensitively, through the long s, the dotless i and the final sigma",
            "landing: sre's fold (long s, Kelvin, I-dot) and whitespace collapse",
            "locate 'MAST' in 'ma\u{17f}t'",
            "locate 'MA\u{17f}T' in 'mast'",
            "locate 'info' in '\u{130}nfo'",
            "locate '\u{130}NFO' in 'info'",
            "locate 'FIX' in 'f\u{131}x'",
            "locate '\u{3a3}\u{39f}\u{3a6}\u{39f}\u{3a3}' in '\u{3c3}\u{3bf}\u{3c6}\u{3bf}\u{3c2}'",
            "locate '\u{39c}S' in '\u{b5}s'",
        ]),
        (Plant::EmText, &["landing: a bare-string atom is a text atom but jd._atom_text reads it as \"\""]),
        (Plant::UuidExclude, &[
            "landing: the turn's other text atoms are excluded by identity, not uuid (a repeated uuid)",
            "landing: found only in another segment of the turn",
            "landing: the subtree's trails, in subtree order",
            "landing: the healed children decide the subtree",
            "text anchor: identity, not uuid (the same uuid twice, the other copy found)",
        ]),
        (Plant::OutrunFloat, &[
            "outrun: coverage stamps against the trail's segment times and the follow-up",
            "outrun truncated float cover",
        ]),
        (Plant::PromptBytes, &["card: a long non-ASCII prompt is cut at 140 code points"]),
        (Plant::IdleTail, &["card: an idle span mid-turn closes it though the turn did not end"]),
    ];
    assert!(differing(&cs, None).is_empty());
    for (plant, names) in want {
        let mut got = differing(&cs, Some(plant));
        let mut names: Vec<String> = names.iter().map(|s| s.to_string()).collect();
        got.sort();
        names.sort();
        assert_eq!(got, names, "{}", plant.name());
    }
}

/// Inclusive ranges from the fixture's `[[lo, hi], ...]`.
fn ranges(v: &J) -> Vec<(u32, u32)> {
    v.as_array().unwrap().iter().map(|r| (r[0].as_u64().unwrap() as u32, r[1].as_u64().unwrap() as u32)).collect()
}

/// Every code point's membership in `rs`, as a table indexed by code point.
fn member_table(rs: &[(u32, u32)]) -> Vec<bool> {
    let mut t = vec![false; 0x110000];
    for &(lo, hi) in rs {
        t[lo as usize..=hi as usize].iter_mut().for_each(|x| *x = true);
    }
    t
}

#[test]
fn the_regex_tables_are_pythons_on_every_code_point() {
    let f = fixture();
    let t = &f["tables"];
    assert_eq!(t["unicode"], pyre_tables::UNICODE_VERSION);
    let (dec, word, cased) =
        (member_table(&ranges(&t["decimal"])), member_table(&ranges(&t["word"])), member_table(&ranges(&t["cased"])));
    let lower: std::collections::HashMap<u32, u32> = t["lower"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| (p[0].as_u64().unwrap() as u32, p[1].as_u64().unwrap() as u32))
        .collect();
    let mut checked = 0;
    for c in (0..=0x10FFFFu32).filter_map(char::from_u32) {
        let u = c as u32;
        assert_eq!(pyre::is_decimal(c), dec[u as usize], "\\d {u:#x}");
        assert_eq!(pyre::is_word(c), word[u as usize], "\\w {u:#x}");
        assert_eq!(pyre::is_cased(c), cased[u as usize], "cased {u:#x}");
        assert_eq!(pyre::lower(c) as u32, lower.get(&u).copied().unwrap_or(u), "lower {u:#x}");
        checked += 1;
    }
    assert_eq!(checked, 0x110000 - 0x800);
    let extra = t["extraCases"].as_array().unwrap();
    assert_eq!(extra.len(), pyre_tables::EXTRA_CASES.len());
    for kv in extra {
        let k = char::from_u32(kv[0].as_u64().unwrap() as u32).unwrap();
        let want: Vec<u32> = kv[1].as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as u32).collect();
        assert_eq!(pyre::extra_cases(k), want.as_slice(), "extra cases {k:?}");
    }
}

#[test]
fn python_ran_every_ported_line_but_the_unreachable_ones() {
    // the memo's key, hit and put, the lazy-body read and its fault, the `if not segs` return (em.segments never
    // answers no segment) and the caption drift index (the replay hands _seg_caption a plain dict): none can run in
    // the port's input set, and the fixture records exactly these as missed
    let f = fixture();
    let lc = &f["lineCoverage"];
    let missed: Vec<&str> = lc["missed"].as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(missed, ["kernel._summary_text_anchor", "kernel._provisional_card", "kernel._seg_caption"]);
    assert_eq!(lc["missed"]["kernel._summary_text_anchor"].as_array().unwrap().len(), 9);
    assert_eq!(lc["missed"]["kernel._provisional_card"].as_array().unwrap().len(), 1);
    assert_eq!(lc["missed"]["kernel._seg_caption"].as_array().unwrap().len(), 2);
    assert_eq!((lc["ran"].as_u64(), lc["lines"].as_u64()), (Some(263), Some(275)));
}
