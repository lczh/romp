//! Layer 7 against Python's own feeds on synthetic worlds (tests/fixtures/layer7_edge.json, written by
//! `python3 tools/rust-spike/dump_layer7.py --edge-cases <that path>`): each case is a small world in the S2 dump's
//! snapshot schema with its trees, the postal log, maildir and notice files it reads, and the frame keys of Python's own
//! build, and Python's whole feed (decoded from its wire string, with the SHA-1 of its canonical and wire strings), or
//! the raise that failed the build.
//!
//! A case named "REFUSE ..." is one the loader must refuse; every other case must load and give the feed byte for byte
//! in both key orders. Python's feeds are read with romp-transcript's decoder, which reads numbers as Python does. The
//! fixture also carries Python's cm.age_rgb and cm.ramp over a table of ages and points, checked here point by point.

use romp_json::Value as Out;
use romp_views::layer7::{self, build_feed, load_case, Plant};
use serde_json::Value as J;

const BYTES: &[u8] = include_bytes!("fixtures/layer7_edge.json");

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

type Loaded = (String, Result<romp_views::layer6::Case, String>, Out);

/// Each case: its name, the loaded world (Err: refused, with why), and Python's expect (the feed and its SHA-1s, or a
/// raise).
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
            (name, load_case(c), member(x, "expect").expect("expect").clone())
        })
        .collect()
}

/// Whether the build under `plant` answers as Python does: the wire bytes, both SHA-1s, or the same raise.
fn answers(c: &romp_views::layer6::Case, expect: &Out, plant: Option<Plant>) -> bool {
    match (build_feed(&c.snapshot, &c.trees, plant), member(expect, "feed")) {
        (Ok(feed), Some(want)) => {
            let (wire, canon) = (feed.wire(), feed.canon());
            wire == romp_json::dumps(want)
                && member(expect, "wireSha1") == Some(&Out::Str(romp_views::sha1::hex(wire.as_bytes())))
                && member(expect, "canonSha1") == Some(&Out::Str(romp_views::sha1::hex(canon.as_bytes())))
        }
        (Err(r), None) => romp_json::dumps(&r.to_json()) == romp_json::dumps(expect),
        _ => false,
    }
}

/// The names of the loaded cases whose feed under `plant` differs from Python's.
fn differing(cases: &[Loaded], plant: Option<Plant>) -> Vec<String> {
    cases
        .iter()
        .filter_map(|(name, c, expect)| match c {
            Ok(c) if !answers(c, expect, plant) => Some(name.clone()),
            _ => None,
        })
        .collect()
}

#[test]
fn the_fixture_is_this_ports_schema() {
    let f = fixture();
    assert_eq!(f["schema"], "romp-spike-layer7-edge/1");
    assert_eq!(f["generatedBy"], "tools/rust-spike/dump_layer7.py --edge-cases");
}

#[test]
fn every_case_builds_pythons_feed_byte_for_byte() {
    let all = cases();
    let (mut loaded, mut refused, mut raised, mut cards) = (0, 0, 0, 0);
    for (name, c, expect) in &all {
        match c {
            Err(why) => {
                assert!(name.starts_with("REFUSE "), "{name}: refused at load: {why}");
                refused += 1;
            }
            Ok(c) => {
                assert!(!name.starts_with("REFUSE "), "{name}: the loader accepted it");
                loaded += 1;
                match (build_feed(&c.snapshot, &c.trees, None), member(expect, "feed")) {
                    (Ok(feed), Some(want)) => {
                        let wire = feed.wire();
                        assert_eq!(wire, romp_json::dumps(want), "{name}: the wire bytes");
                        assert_eq!(member(expect, "wireSha1"), Some(&Out::Str(romp_views::sha1::hex(wire.as_bytes()))),
                                   "{name}: the SHA-1 of Python's wire string");
                        let canon = feed.canon();
                        assert_eq!(member(expect, "canonSha1"), Some(&Out::Str(romp_views::sha1::hex(canon.as_bytes()))),
                                   "{name}: the SHA-1 of Python's canonical string");
                        cards += feed.cards;
                    }
                    (Err(r), None) => {
                        assert_eq!(romp_json::dumps(&r.to_json()), romp_json::dumps(expect), "{name}: the raise");
                        raised += 1;
                    }
                    (got, _) => panic!("{name}: rust {:?}, python {}", got.map(|f| f.wire()), romp_json::dumps(expect)),
                }
            }
        }
    }
    assert_eq!((loaded + refused, refused), (all.len(), 36), "every REFUSE case is refused, every other loads");
    assert_eq!(raised, 1, "the one admitted build that raises: a null card in an entry's asks");
    assert!(cards >= 380, "{cards} cards");
}

#[test]
fn the_tint_table_is_pythons_point_for_point() {
    let e = exact();
    let name = |v: &Out| match v {
        Out::Str(s) => Some(s.clone()),
        Out::Null => None,
        other => panic!("a colormap name: {other:?}"),
    };
    let num = |v: &Out| match v {
        Out::Int(i) => *i as f64,
        Out::Float(f) => *f,
        other => panic!("a number: {other:?}"),
    };
    let rgb = |c: [i64; 3]| Out::Array(c.iter().map(|&x| Out::Int(x)).collect());
    let tints = items(member(&e, "tints").expect("tints"));
    for row in tints {
        let r = items(row);
        assert_eq!(rgb(layer7::age_rgb(num(&r[1]), name(&r[0]).as_deref())), r[2], "age_rgb {}", romp_json::dumps(row));
    }
    let ramps = items(member(&e, "ramps").expect("ramps"));
    for row in ramps {
        let r = items(row);
        assert_eq!(rgb(layer7::ramp(num(&r[0]), layer7::stops_for(name(&r[1]).as_deref()))), r[2],
                   "ramp {}", romp_json::dumps(row));
    }
    assert!(tints.len() >= 4000 && ramps.len() >= 1700);
    // the table holds exact halves on both sides of the even rule: 59.5 rounds up to 60, 50.5 down to 50 (aurora)
    for (v, want) in [(0.0625, [42, 179, 60]), (0.3125, [50, 172, 166])] {
        assert!(ramps.iter().any(|r| { let r = items(r); num(&r[0]) == v && r[1] == Out::Null && r[2] == rgb(want) }),
                "ramp({v}) of the default colormap is in the table");
    }
}

#[test]
fn each_plant_is_caught_on_the_cases_built_for_it() {
    let all = cases();
    assert!(differing(&all, None).is_empty());
    let want: [(Plant, &[&str]); 6] = [
        (Plant::BellFalsySkip, &[
            "bell: a card override false, the master on: an explicit mute (never the master)",
            "bell: a card override 0 and \"\": falsy, not None: mutes",
            "bell: a card override false beats the session's flag true",
            "bell: a card whose itemId is \"\" muted, the master on: notify reads the override, notifyRaw skips it \
             (Python's `if item_id:`)",
            "parked: bells: a card override, the recipient's own flag",
            "frame: every producer at once: session cards, a kept candidate, parked and notice cards, all belled",
        ]),
        (Plant::ServingFirstRow, &["serving: a tracker id two cards render: the last handoff row wins"]),
        (Plant::WhoOwner, &[
            "serving: the joined rows' handoff rows take the whoWorking stamp",
            "who: the recipient works: the row's dot lights",
            "who: the owner works and the recipient does not",
        ]),
        (Plant::ParkedUnsorted, &[
            "parked: times: absent and 0 read the clock, a float truncates, true is 1, a list is no time, negative",
            "parked: sorted by time, ties in the log's order",
            "parked: a float clock: the missing time is int(now), the tint's age a float",
        ]),
        (Plant::NoticeCapHeld, &[
            "notice: the cap keeps the newest 50 keys; held-mail cards hold no slot",
            "notice: held mail: a mismatched mid, another kind, no actions outside the owner-less home all count",
            "notice: held mail in the owner-less home: a row with no actions and the home's sid holds no slot",
        ]),
        (Plant::AbsentLast, &[
            "fold: a raising session is absent; the others fold around it",
            "fold: two raising sessions, absentSids in alive order",
            "who: the recipient's derivation raised (absent): dark",
            "parked and notice: a recipient and a notice file's owner alive but absent (its derivation raised) count as \
             alive: no parked card, the notice live",
            "frame: servingFoldedIds and absentSids both made: right after now, in that order",
        ]),
    ];
    let mut failures = Vec::new();
    for (plant, names) in want {
        let got = differing(&all, Some(plant));
        if got != names {
            failures.push(format!("{}: {got:#?}", plant.name()));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn the_line_trace_missed_only_lines_an_admitted_world_cannot_run_in_a_fresh_replay() {
    // build_feed's memo and key arms (a held entry, a hit, a raise served from the memo, the live map read when the
    // caller passes none, the subagent reads), the fold's kept-record path, the maildir stat that raises (an id past
    // the filesystem's name limit raises OSError 63, file name too long; the loader refuses ids over kernel._safe_id's
    // 128 characters), and _notice_rows' absent, memoized, unreadable, blank and undecodable lines (a decoded snapshot
    // holds none)
    let f = fixture();
    let missed = f["lineCoverage"]["missed"].as_object().expect("missed lines");
    let counts: Vec<(String, usize)> =
        missed.iter().map(|(k, v)| (k.clone(), v.as_array().expect("lines").len())).collect();
    assert_eq!(counts, [
        ("kernel.build_feed".to_string(), 10),
        ("kernel._feed_fold_session".to_string(), 20),
        ("kernel._parked_handoffs".to_string(), 2),
        ("kernel._notice_rows".to_string(), 11),
    ]);
    assert_eq!((f["lineCoverage"]["ran"].as_u64(), f["lineCoverage"]["lines"].as_u64()), (Some(371), Some(414)));
}

#[test]
fn the_review_rounds_refusals_name_their_rule() {
    // S4.7's review round: kernel._safe_id on a parked send's id and to_id (Python looks the mail up by path), and a
    // clock whose int() is past i64 while the postal log holds a parked send (a parked send with no time reads int(now))
    let unsafe_at = |k: &str| {
        format!("snapshot: board.postalLog[0].{k}: not a safe path component (kernel._safe_id): Python looks the parked \
                 mail up by path")
    };
    let clock = "snapshot: world.now: int() past i64 while the postal log holds a parked send (a parked send with no \
                 time reads int(now))"
        .to_string();
    let want: Vec<(&str, String)> = vec![
        ("REFUSE a parked send whose id is '.' (the path is the recipient's new/ directory itself)", unsafe_at("id")),
        ("REFUSE a parked send whose id ends in a slash (pathlib drops it: m1/ finds m1)", unsafe_at("id")),
        ("REFUSE a parked send whose to_id ends in a slash", unsafe_at("to_id")),
        ("REFUSE a parked send whose id starts with a dot", unsafe_at("id")),
        ("REFUSE a parked send whose id holds a backslash", unsafe_at("id")),
        ("REFUSE a parked send whose id holds NUL (pathlib's exists() reads it as no file)", unsafe_at("id")),
        ("REFUSE a parked send whose id is 129 characters (past kernel._safe_id's cap)", unsafe_at("id")),
        ("REFUSE a parked send whose id holds a space (outside [A-Za-z0-9._-])", unsafe_at("id")),
        ("REFUSE a parked send whose id starts with a hyphen", unsafe_at("id")),
        ("REFUSE a parked send whose to_id is not ASCII (APFS also folds Unicode normalization)", unsafe_at("to_id")),
        ("REFUSE a float clock whose int() is past i64 (1e+19) while a parked send has no time", clock.clone()),
        ("REFUSE a float clock whose int() is past i64 (-1e+19) while a parked send has no time", clock),
    ];
    let all = cases();
    for (name, why) in &want {
        let got = all.iter().find(|(n, _, _)| n == name).unwrap_or_else(|| panic!("{name}: not in the fixture"));
        assert_eq!(got.1.as_ref().err(), Some(why), "{name}");
    }
    assert_eq!(want.len(), 12);
}
