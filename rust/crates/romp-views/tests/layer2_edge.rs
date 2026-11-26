//! Layer 2 against Python's own answers on synthetic cases (tests/fixtures/layer2_edge.json, written by
//! `python3 tools/rust-spike/dump_layer2.py --edge-cases <that path>`): kernel._segs_seam on 104 turns with stores and
//! em.split_segment on the first segment of 5 more, built to reach the branches neither dumped world does (straddling
//! and recursive seams, equal and int-vs-float times, absent and null seam text, the prose floor at 79/80/81 code
//! points, placed echoes, every trigger shape, "%d" of float times, and the inputs on which Python raises).
//!
//! A case named "REFUSE ..." is one the loader must refuse; every other case must load and answer as Python did,
//! including the exception it raised, and do what Python's apply_seams did (pieces examined, splits tried, splits).

use romp_json::Value as Out;
use romp_views::layer2::{self, Plant, Probe};
use romp_views::record::{Num, ToJson};
use romp_views::snapshot::Store;
use romp_views::tree::{self, Turn};
use serde_json::Value as J;

fn fixture() -> J {
    serde_json::from_str(include_str!("fixtures/layer2_edge.json")).expect("the fixture is JSON")
}

fn load(case: &J) -> Result<(Turn, Option<Store>), String> {
    let turn: Turn = serde_json::from_value(case["turn"].clone()).map_err(|e| e.to_string())?;
    tree::validate_turn(&turn)?;
    let store = match &case["store"] {
        J::Null => None,
        v => Some(serde_json::from_value::<Store>(v.clone()).map_err(|e| e.to_string())?),
    };
    Ok((turn, store))
}

/// The port's answer to a loaded case, as dump_layer2.py writes Python's, and its counts.
fn answer(case: &J, turn: &Turn, store: Option<&Store>, plant: Option<Plant>) -> (Out, [usize; 3]) {
    let index = layer2::atom_index(turn);
    let mut probe = Probe { plant, ..Probe::default() };
    let out = match case["kind"].as_str().unwrap() {
        "segs" => layer2::answer_json(&layer2::segs_seam_with(turn, store, &mut probe), &index),
        "split" => {
            let t: Num = serde_json::from_value(case["t"].clone()).unwrap();
            layer2::split_answer_json(&layer2::split_first_with(turn, t, &mut probe), &index)
        }
        k => panic!("unknown kind {k}"),
    };
    (out, [probe.pieces, probe.split_calls, probe.splits])
}

fn python_counts(case: &J) -> [usize; 3] {
    ["pieces", "splitCalls", "splits"].map(|k| case["counts"][k].as_u64().unwrap() as usize)
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer2-edge/1");
    assert_eq!(f["constants"]["SEAM_PROSE_FLOOR"].as_u64(), Some(layer2::SEAM_PROSE_FLOOR as u64));
}

#[test]
fn every_case_answers_as_python_does() {
    let f = fixture();
    let (mut loaded, mut refused, mut matched_raises, mut refused_raises) = (0, 0, 0, 0);
    let mut diffs = Vec::new();
    for case in f["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let raises = case["expect"].get("raises").is_some();
        match load(case) {
            Err(e) => {
                assert!(name.starts_with("REFUSE "), "{name}: refused but not a REFUSE case: {e}");
                refused += 1;
                refused_raises += usize::from(raises);
            }
            Ok((turn, store)) => {
                assert!(!name.starts_with("REFUSE "), "{name}: the loader accepted it");
                loaded += 1;
                matched_raises += usize::from(raises);
                let (got, counts) = answer(case, &turn, store.as_ref(), None);
                let want = case["expect"].to_json();
                if romp_json::dumps(&got) != romp_json::dumps(&want) {
                    diffs.push(format!("{name}:\n  rust   {}\n  python {}", romp_json::dumps(&got),
                                       romp_json::dumps(&want)));
                }
                if counts != python_counts(case) {
                    diffs.push(format!("{name}: counts rust {counts:?} python {:?}", python_counts(case)));
                }
            }
        }
    }
    assert!(diffs.is_empty(), "{} differences:\n{}", diffs.len(), diffs.join("\n"));
    assert_eq!((loaded, refused), (99, 10));
    assert_eq!((matched_raises, refused_raises), (5, 3), "Python's raises: matched as Raise, refused at load");
}

#[test]
fn the_cases_reach_the_branches_the_worlds_do_not() {
    // what the loaded cases make the port do, so a fixture that lost its splits would fail here
    let f = fixture();
    let (mut tails, mut splits, mut seam_of_null_text, mut float_ids) = (0, 0, 0, 0);
    for case in f["cases"].as_array().unwrap() {
        let Ok((turn, store)) = load(case) else { continue };
        let (got, counts) = answer(case, &turn, store.as_ref(), None);
        splits += counts[2];
        if let Out::Array(segs) = &got {
            for s in segs {
                let Out::Object(m) = s else { continue };
                tails += usize::from(m.iter().any(|(k, _)| k == "seam"));
                let so = m.iter().find(|(k, _)| k == "seamOf").map(|(_, v)| romp_json::dumps(v));
                seam_of_null_text += usize::from(so.is_some_and(|s| s.ends_with("\"text\": null}")));
                let t = m.iter().find(|(k, _)| k == "t").map(|(_, v)| v);
                float_ids += usize::from(matches!(t, Some(Out::Float(_))));
            }
        }
    }
    // the same four counts taken from Python's answers in the fixture (non-REFUSE cases): splits from `counts`,
    // segments carrying `seam`, seamOf texts that are null, segments whose `t` is a float
    assert_eq!((splits, tails, seam_of_null_text, float_ids), (38, 38, 1, 11));
}

#[test]
fn a_planted_floor_in_bytes_is_caught_on_exactly_the_79_code_point_tails() {
    let f = fixture();
    let mut caught = Vec::new();
    for case in f["cases"].as_array().unwrap() {
        let Ok((turn, store)) = load(case) else { continue };
        let (got, _) = answer(case, &turn, store.as_ref(), Some(Plant::FloorInBytes));
        if romp_json::dumps(&got) != romp_json::dumps(&case["expect"].to_json()) {
            caught.push(case["name"].as_str().unwrap());
        }
    }
    assert_eq!(caught, ["tail prose 79 code points, 88 bytes", "tail prose 79 after strip (83 before)"]);
}

#[test]
fn a_planted_end_is_caught() {
    let f = fixture();
    let caught = f["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| {
            let Ok((turn, store)) = load(case) else { return false };
            let (got, _) = answer(case, &turn, store.as_ref(), Some(Plant::EndAtLastAtom));
            romp_json::dumps(&got) != romp_json::dumps(&case["expect"].to_json())
        })
        .count();
    assert!(caught > 50, "{caught}");
}
