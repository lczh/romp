//! Layer 4 against Python's own answers on synthetic cases (tests/fixtures/layer4_edge.json, written by
//! `python3 tools/rust-spike/dump_layer4.py --edge-cases <that path>`): worlds of sessions sharing one anchor table, a
//! names registry and peer facts, answered as the per-card build reads its store helpers (the body's children,
//! _agent_open_set, _parked_rows, _focus_open_top, and per node the stamp, the block and done times, the open leaves,
//! the handoff card, the faces, the log rows, the anchors, the landing inputs), name queries over every identity
//! shape, and direct calls of _awaiting_item, _title_shaped, jd.junk_quote, _peer_stamp_superseded, the stamp walk
//! without its children argument and _landing_inputs, built to reach the branches neither dumped world does.
//!
//! A case named "REFUSE ..." is one the loader must refuse; every other case must load and answer as Python did.
//! Python's answers are read with romp-transcript's decoder, which keeps an integer past u64 an integer (`int(1e20)`),
//! and an answer the fixture keeps as a digest (`expectSha1`, the depth-limit worlds) is compared by the SHA-1 of what
//! romp-json writes for the port's answer.

use romp_json::Value as Out;
use romp_views::layer4::{EdgeCase, Plant};
use serde_json::Value as J;

const BYTES: &[u8] = include_bytes!("fixtures/layer4_edge.json");

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

/// Each case: its name, the loaded case (Err: refused, with why), and Python's answer (exact) or its digest.
fn cases() -> Vec<Case> {
    let f = fixture();
    let e = exact();
    let raw = f["cases"].as_array().expect("cases");
    let exact_cases = items(member(&e, "cases").expect("cases"));
    assert_eq!(raw.len(), exact_cases.len());
    raw.iter()
        .zip(exact_cases)
        .map(|(c, x)| {
            let loaded = EdgeCase::load(c).and_then(|l| match l.refused_session() {
                Some(why) => Err(why),
                None => Ok(l),
            });
            (
                c["name"].as_str().expect("a name").to_string(),
                loaded,
                member(x, "expect").cloned(),
                c["expectSha1"].as_str().map(str::to_string),
            )
        })
        .collect()
}

fn matches(got: &Out, want: &Option<Out>, digest: &Option<String>) -> bool {
    match (want, digest) {
        (_, Some(h)) => romp_views::sha1::hex(romp_json::dumps(got).as_bytes()) == *h,
        (Some(w), None) => romp_json::dumps(got) == romp_json::dumps(w),
        (None, None) => panic!("a case with neither an answer nor a digest"),
    }
}

type Case = (String, Result<EdgeCase, String>, Option<Out>, Option<String>);

/// The names of the loaded cases whose answer under `plant` differs from Python's.
fn differing(cases: &[Case], plant: Option<Plant>) -> Vec<String> {
    cases
        .iter()
        .filter_map(|(n, c, want, digest)| {
            let c = c.as_ref().ok()?;
            (!matches(&c.answer(plant), want, digest)).then(|| n.clone())
        })
        .collect()
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer4-edge/1");
    assert_eq!(f["generatedBy"], "tools/rust-spike/dump_layer4.py --edge-cases");
}

#[test]
fn every_case_answers_as_python_does() {
    let (mut loaded, mut refused, mut refused_raises, mut digests) = (0, 0, 0, 0);
    let mut diffs = Vec::new();
    for (n, c, want, digest) in cases() {
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
                digests += usize::from(digest.is_some());
                let got = c.answer(None);
                if !matches(&got, &want, &digest) {
                    diffs.push(format!("{n}:\n  rust   {}\n  python {}", romp_json::dumps(&got),
                                       want.as_ref().map_or_else(|| "(digest)".to_string(), romp_json::dumps)));
                }
            }
        }
    }
    assert!(diffs.is_empty(), "{} differences:\n{}", diffs.len(), diffs.join("\n"));
    assert_eq!((loaded, refused, refused_raises, digests), (195, 10, 2, 2));
}

#[test]
fn the_cases_reach_the_branches_the_worlds_do_not() {
    // counted from the port's own answers, so a fixture that lost its branches fails here; the right-hand sides are
    // the fixture's `coverage` block, which Python counted from its answers
    let f = fixture();
    let (mut stamps, mut loops, mut parked, mut agent_open, mut hidden, mut faces, mut raises, mut badges) =
        (0, 0, 0, 0, 0, 0, 0, 0);
    for (n, c, _want, _digest) in cases() {
        let Ok(c @ EdgeCase::World { .. }) = c else { continue };
        if n.starts_with("REFUSE ") {
            continue;
        }
        let a = c.answer(None);
        for s in items(member(&a, "sessions").expect("sessions")) {
            parked += items(member(s, "parked").unwrap()).len();
            agent_open += items(member(s, "agentOpen").unwrap()).len();
            hidden += items(member(s, "hidden").unwrap()).len();
            raises += usize::from(matches!(member(s, "loginLabel"), Some(Out::Object(_))));
            for row in items(member(s, "nodes").unwrap()) {
                let r = &items(row)[1];
                stamps += usize::from(matches!(member(r, "stamp"), Some(Out::Array(_))));
                loops += usize::from(matches!(member(r, "stamp"), Some(Out::Object(_))));
                faces += usize::from(!matches!(member(r, "startedFace"), Some(Out::Null)));
                badges += usize::from(!matches!(items(member(r, "handoffCard").unwrap())[0], Out::Null));
            }
        }
    }
    let cov = &f["coverage"];
    let want = |k: &str| cov[k].as_u64().unwrap() as usize;
    let got = (stamps, loops, parked, agent_open, hidden, faces, raises, badges);
    assert_eq!(got, (want("stamps"), want("stampLoops"), want("parked"), want("agentOpen"), want("hidden"),
                     want("faces"), want("loginRaises"), want("badges")));
    assert!(stamps > 0 && loops > 0 && parked > 0 && agent_open > 0 && hidden > 0 && faces > 0 && raises > 0);
}

#[test]
fn python_ran_every_reachable_line_of_the_ported_functions() {
    // the two misses cannot run in the per-card build: _node_anchor_uuids' revision bump needs a sid, which the feed
    // never passes, and _names_parts' registry-file read needs no names scope, which the replay always sets
    let f = fixture();
    let lc = &f["lineCoverage"];
    let mut missed: Vec<&String> = lc["missed"].as_object().expect("missed").keys().collect();
    missed.sort();
    assert_eq!(missed, ["kernel._names_parts", "kernel._node_anchor_uuids"]);
    assert_eq!(lc["missed"]["kernel._node_anchor_uuids"].as_array().unwrap().len(), 1);
    assert_eq!(lc["missed"]["kernel._names_parts"].as_array().unwrap().len(), 4);
}

#[test]
fn each_plant_is_caught_on_the_cases_built_for_it() {
    let cases = cases();
    assert_eq!(differing(&cases, None), Vec::<String>::new());
    let got: Vec<(&str, Vec<String>)> = Plant::ALL.iter().map(|p| (p.name(), differing(&cases, Some(*p)))).collect();
    assert_eq!(got.iter().find(|(n, _)| *n == "max-last").unwrap().1, PLANT_MAX_LAST);
    assert_eq!(got.iter().find(|(n, _)| *n == "int-round").unwrap().1, PLANT_INT_ROUND);
    assert_eq!(got.iter().find(|(n, _)| *n == "uuid-strict").unwrap().1, PLANT_UUID_STRICT);
    assert_eq!(got.iter().find(|(n, _)| *n == "table-off").unwrap().1, PLANT_TABLE_OFF);
    assert_eq!(got.iter().find(|(n, _)| *n == "parked-sorted").unwrap().1, PLANT_PARKED_SORTED);
}

/// A full tie of awaiting stamps (5 met first, 5.0 met last) keeps the first one's tag.
const PLANT_MAX_LAST: &[&str] = &["a full tie keeps the first the stack meets (the tag rides): c1, then b, then a"];
/// Every float `since` whose rounding and truncation differ (a positive and a negative half, -0.5 to 0 or -1, a
/// fraction just under 1); 1e20 and the integral floats agree either way.
const PLANT_INT_ROUND: &[&str] = &[
    "awaiting item agents since 1781100000.75",
    "awaiting item commands since -0.5",
    "awaiting item timer since 2.5",
    "awaiting item peer since -2.7",
    "awaiting item peer since 0.9999999999999999",
];
/// A uuid with one trailing newline: uuid-shaped in Python, so its owning host names it (with the registry and peer
/// facts) or it stubs to 8 characters (without).
const PLANT_UUID_STRICT: &[&str] =
    &["identity ladder over every query shape", "no peer facts: the ladder ends at the stubs"];
/// The cases where a cold lookup meets a remembered warm anchor.
const PLANT_TABLE_OFF: &[&str] = &[
    "anchors across sessions: a warm resolve serves a later cold lookup of the same id",
    "anchors: an empty work anchor is warm (`is not None`), remembered and served to a later cold lookup",
    "anchors: the same id twice in one store (two keys) resolves in store order",
];
/// The cases with two or more parked rows out of id order.
const PLANT_PARKED_SORTED: &[&str] = &[
    "a younger sibling's handoff leapfrogs an older quiet row",
    "roots leapfrogged too, two younger handoffs count two, children map order",
];
