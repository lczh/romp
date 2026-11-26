//! S6.1's memo against Python's own decisions on the small world's scripted sequence
//! (tests/fixtures/memo_edge.json in romp-views, written by `tools/rust-spike/dump_memo.py --edge DUMP OUT`): every
//! session's hit or miss, the components a miss names, every raise served or absent, the held walk's counters, the
//! memo's entries and evictions and the SHA-1 of every memo-served and fresh build, through `romp-spike memo`; and each
//! of the six plants is caught by it.

use std::process::Command;

fn fixture() -> String {
    concat!(env!("CARGO_MANIFEST_DIR"), "/../romp-views/tests/fixtures/memo_edge.json").to_string()
}

fn run(plant: Option<&str>) -> (bool, String) {
    let mut c = Command::new(env!("CARGO_BIN_EXE_romp-spike"));
    c.arg("memo").arg(fixture());
    if let Some(p) = plant {
        c.args(["--plant", p]);
    }
    let out = c.output().expect("romp-spike runs");
    (out.status.success(), String::from_utf8_lossy(&out.stdout).into_owned())
}

#[test]
fn every_decision_and_build_is_pythons() {
    let (ok, out) = run(None);
    let last = out.lines().last().unwrap_or_default().to_string();
    assert!(ok, "{out}");
    assert!(
        last.starts_with("memo cases: 3 cases, 79 steps, 158 builds compared (315 session decisions: 214 hits, 99 misses, \
                          6 raises), 70 memo-served builds equal to their fresh build, 7 stale where Python's are, 0 \
                          differences in 0 steps (2 fresh comparisons skipped where Python's frame differs;"),
        "{last}"
    );
}

#[test]
fn each_plant_is_caught() {
    for p in ["peers-settled", "tag-strict", "row-whole", "raise-absent", "no-forget", "stalls-all"] {
        let (ok, out) = run(Some(p));
        let last = out.lines().last().unwrap_or_default().to_string();
        assert!(ok, "the plant {p} was not caught: {last}");
        assert!(last.contains(&format!("[planted: {p}]")) && !last.contains(" 0 differences"), "{p}: {last}");
    }
}
