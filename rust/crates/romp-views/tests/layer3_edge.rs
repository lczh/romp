//! Layer 3 against Python's own answers on synthetic cases (tests/fixtures/layer3_edge.json, written by
//! `python3 tools/rust-spike/dump_layer3.py --edge-cases <that path>`): 112 session cases (7 of them shapes the
//! loader must refuse) answered as the per-card build reads them (_session_working, _open_turn_progress, the anchor
//! walk and its plain-prompt time, every key's `seg_turn` answer, each segment's anchors), 11 direct _FeedSegTurns
//! cases over hand-made positions and 12 direct _suspended_after reads, built to reach the branches neither dumped
//! world does.
//!
//! A case named "REFUSE ..." is one the loader must refuse; every other case must load and answer as Python did.

use romp_json::Value as Out;
use romp_views::layer3::{self, EdgeCase, Plant};
use romp_views::record::ToJson;
use serde_json::Value as J;

fn fixture() -> J {
    serde_json::from_str(include_str!("fixtures/layer3_edge.json")).expect("the fixture is JSON")
}

fn cases(f: &J) -> &Vec<J> {
    f["cases"].as_array().expect("cases")
}

fn name(case: &J) -> &str {
    case["name"].as_str().expect("a name")
}

/// The names of the loaded cases whose answer under `plant` differs from Python's.
fn differing(f: &J, plant: Option<Plant>) -> Vec<String> {
    let mut out = Vec::new();
    for case in cases(f) {
        let Ok(c) = EdgeCase::load(case) else { continue };
        if romp_json::dumps(&c.answer(plant)) != romp_json::dumps(&case["expect"].to_json()) {
            out.push(name(case).to_string());
        }
    }
    out
}

fn member<'a>(o: &'a Out, key: &str) -> &'a Out {
    match o {
        Out::Object(m) => &m.iter().find(|(k, _)| k == key).unwrap_or_else(|| panic!("no {key}")).1,
        _ => panic!("not an object"),
    }
}

fn items(o: &Out) -> &[Out] {
    match o {
        Out::Array(a) => a,
        _ => panic!("not an array"),
    }
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer3-edge/1");
    assert_eq!(f["constants"]["CITE_MIN_CHARS"].as_u64(), Some(layer3::CITE_MIN_CHARS as u64));
}

#[test]
fn every_case_answers_as_python_does() {
    let f = fixture();
    let (mut loaded, mut refused, mut refused_raises) = (0, 0, 0);
    let mut diffs = Vec::new();
    for case in cases(&f) {
        let n = name(case);
        let raises = case["expect"].get("raises").is_some();
        match EdgeCase::load(case) {
            Err(e) => {
                assert!(n.starts_with("REFUSE "), "{n}: refused but not a REFUSE case: {e}");
                refused += 1;
                refused_raises += usize::from(raises);
            }
            Ok(c) => {
                assert!(!n.starts_with("REFUSE "), "{n}: the loader accepted it");
                assert!(!raises, "{n}: Python raised on a case the loader accepts");
                loaded += 1;
                let (got, want) = (romp_json::dumps(&c.answer(None)), romp_json::dumps(&case["expect"].to_json()));
                if got != want {
                    diffs.push(format!("{n}:\n  rust   {got}\n  python {want}"));
                }
            }
        }
    }
    assert!(diffs.is_empty(), "{} differences:\n{}", diffs.len(), diffs.join("\n"));
    assert_eq!((loaded, refused, refused_raises), (128, 7, 3));
}

#[test]
fn the_cases_reach_the_branches_the_worlds_do_not() {
    // counted from the port's own answers, so a fixture that lost its branches fails here; the right-hand sides are
    // the fixture's `coverage` block, which Python counted from its answers
    let f = fixture();
    let (mut working, mut suspended, mut not_ok, mut overwrites, mut plain_float, mut faults, mut raising) =
        (0, 0, 0, 0, 0, 0, 0);
    for case in cases(&f) {
        let Ok(c) = EdgeCase::load(case) else { continue };
        let a = c.answer(None);
        match c {
            EdgeCase::Session { .. } => {
                working += usize::from(*member(&a, "working") == Out::Bool(true));
                suspended += usize::from(*member(&a, "suspended") == Out::Bool(true));
                let ok = *member(&a, "walkOk") == Out::Bool(true);
                not_ok += usize::from(!ok);
                plain_float += usize::from(matches!(member(&a, "plainT"), Out::Float(_)));
                let rows = items(member(&a, "turns"));
                raising += rows.iter().filter(|r| matches!(r, Out::Object(_))).count();
                if ok {
                    let folded: usize = rows.iter().map(|r| items(r).len()).sum();
                    overwrites += folded - items(member(&a, "pos")).len();
                }
            }
            EdgeCase::SegTurns { .. } => {
                faults += usize::try_from(match member(&a, "faults") { Out::Int(n) => *n, _ => -1 }).unwrap();
            }
            EdgeCase::Suspended { .. } => {}
        }
    }
    let cov = &f["coverage"];
    let want = |k: &str| cov[k].as_u64().unwrap() as usize;
    assert_eq!(
        (working, suspended, not_ok, overwrites, plain_float, faults, raising),
        (want("working"), want("suspended"), want("walkNotOk"), want("overwrites"), want("plainFloat"),
         want("faults"), want("raisingTurns"))
    );
    assert_eq!((working, suspended, not_ok, overwrites, plain_float, faults, raising), (20, 4, 6, 35, 6, 5, 6));
}

#[test]
fn each_plant_is_caught_on_the_cases_built_for_it() {
    let f = fixture();
    assert_eq!(differing(&f, None), Vec::<String>::new());
    assert_eq!(differing(&f, Some(Plant::MaxKeepsLast)), [
        "equal times: the int first is kept",
        "equal times: the float first is kept",
        "a tie across the closed and open turns keeps the closed time",
        "a tie across the closed and open turns, int closed",
        "the float 2**53 does not beat the int 2**53",
    ]);
    // its 79-code-point atom (88 bytes) is short of the floor; the case with 79 after a leading space is not caught,
    // since the count is unstripped (80 code points, 89 bytes: both sides clear the floor)
    assert_eq!(differing(&f, Some(Plant::FloorInBytes)), [
        "last text: 80 code points is substantive, 79 is not (88 bytes)",
    ]);
    assert_eq!(differing(&f, Some(Plant::RaiseSkipsTurn)), [
        "a raise in the middle turn: the maps as far as the fold got",
        "a raise in the first turn: empty maps, the plain time still read",
        "a raise in the open turn: the closed maps complete",
        "a raise after a repeated key",
        "a trigger-less turn without inputs raises KeyError 'trigger'",
        "a seam without t that hits raises in the open turn",
    ]);
    // the other overwrite cases repeat their session's only key, which a move leaves where it was
    assert_eq!(differing(&f, Some(Plant::OverwriteMoves)), [
        "the same prompt in two turns: the later overwrites in the first place",
        "one key three times, two keys between",
    ]);
}
