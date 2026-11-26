//! S6.1's memo checks (rust/SPIKE.md, "S6.1 memo port and tuning"):
//!
//!     romp-spike memo CASES.json [--plant NAME]   # change sequences against Python's decisions (dump_memo.py)
//!     romp-spike memo DUMP [--plant NAME]         # a memo-served build equals the dump's oracle, byte for byte
//!
//! A case file (schema `romp-spike-memo/1`, tools/rust-spike/dump_memo.py) holds worlds and their steps. Each step's
//! ops edit the world (the same `set`, `init` and `del` the oracle applied), then the port builds it twice: with the
//! memo, the held aggregate and the anchor table carried across the case's builds, and fresh (nothing carried). The memo
//! build must give every session Python's decision (a hit, or a miss naming the same moved components, and the same
//! raise, served or absent), the same absent sessions, the held walk's counters, the memo's entry count and evictions,
//! and the SHA-1 of Python's canonical and wire strings; the fresh build Python's fresh SHA-1s (skipped where the
//! frame build_feed passes through differs between Python's two builds). So where Python's memo-served build equals its
//! fresh one, the port's do too, and where Python serves stale bytes (an input the key does not see moved), the port
//! serves the same stale bytes.
//!
//! The dump form builds the dump three times in one process with the memo (cold, then the second build that settles the
//! sessions whose entries name peers, then a build served whole) and requires every build's bytes to equal the dump's
//! oracle and the third to be all hits with no anchor walk.
//!
//! `--plant` (memo::Plant) makes one deliberate mistake; with it the command passes only when a difference is found.

use std::cell::RefCell;
use std::path::Path;
use std::process::ExitCode;
use std::time::Instant;

use romp_json::Value as Out;
use serde_json::{Map, Value as J};

use romp_views::fxhash::FxMap;
use romp_views::layer3::{SegHeld, SegStats};
use romp_views::memo::{self, FeedMemo, Plant};
use romp_views::record::ToJson;
use romp_views::snapshot::{self, Snapshot};
use romp_views::tree::{self, Tree};
use romp_views::{layer6, layer7, load, sha1};

const SCHEMA: &str = "romp-spike-memo/1";
const MAX_SHOWN: usize = 12;

/// Values allocated once and never moved or freed before the arena: the snapshots and trees of a case's steps, which
/// its held aggregate borrows from across builds.
struct Arena<T> {
    items: RefCell<Vec<Box<T>>>,
}

impl<T> Arena<T> {
    fn new() -> Self {
        Arena { items: RefCell::new(Vec::new()) }
    }

    fn alloc(&self, v: T) -> &T {
        let b = Box::new(v);
        let p: *const T = &*b;
        self.items.borrow_mut().push(b);
        // SAFETY: the box is owned by `items` until the arena drops, is never removed or replaced, and a Box's contents
        // do not move when the Vec of boxes grows; the returned reference cannot outlive `&self`.
        unsafe { &*p }
    }
}

pub fn cmd(args: &[String]) -> ExitCode {
    let mut plant = None;
    let mut pos = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plant" => match it.next().and_then(|p| Plant::parse(p)) {
                Some(p) => plant = Some(p),
                None => return usage(),
            },
            s if s.starts_with("--") => return usage(),
            _ => pos.push(a.as_str()),
        }
    }
    let [path] = pos.as_slice() else { return usage() };
    let p = Path::new(path);
    let got = if p.is_dir() { dump_check(p, plant) } else { cases(p, plant) };
    match got {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(1),
        Err(e) => {
            eprintln!("romp-spike memo: {e}");
            ExitCode::from(2)
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("usage: romp-spike memo CASES.json|DUMP [--plant {}]",
              Plant::ALL.iter().map(|p| p.name()).collect::<Vec<_>>().join("|"));
    ExitCode::from(2)
}

fn plant_note(plant: Option<Plant>) -> String {
    plant.map_or_else(String::new, |p| format!(" [planted: {}]", p.name()))
}

/// kernel.FEED_SEG_HELD_BYTES as measure.rs reads it (ROMP_FEED_SEG_HELD_BYTES, else a 128th of 8 GB here).
fn default_seg_bound() -> u64 {
    std::env::var("ROMP_FEED_SEG_HELD_BYTES").ok().and_then(|v| v.parse::<u64>().ok()).filter(|n| *n > 0).unwrap_or((8u64 << 30) / 128)
}

// ───────────────────────────── the edits ─────────────────────────────

/// One op of a step (dump_memo.apply_ops): `set` (an index equal to a list's length appends; an empty path at a tree
/// replaces the tree, null meaning none), `init` (set where the key is absent or null) and `del`.
fn apply_op(snap: &mut J, trees: &mut Map<String, J>, op: &J) -> Result<Option<String>, String> {
    let kind = op["op"].as_str().ok_or("an op without a kind")?;
    let path = op["path"].as_array().ok_or("an op without a path")?;
    let (mut target, touched): (&mut J, Option<String>) = if op["at"] == "tree" {
        let sid = op["sid"].as_str().ok_or("a tree op without a sid")?.to_string();
        if path.is_empty() {
            match kind {
                "set" => {
                    trees.insert(sid.clone(), op["value"].clone());
                }
                _ => {
                    trees.remove(&sid);
                }
            }
            return Ok(Some(sid));
        }
        (trees.get_mut(&sid).ok_or_else(|| format!("no tree for {sid}"))?, Some(sid))
    } else {
        (snap, None)
    };
    for k in &path[..path.len() - 1] {
        target = step_into(target, k)?;
    }
    let last = path.last().ok_or("an empty path")?;
    match (kind, target) {
        ("init", J::Object(m)) => {
            let k = last.as_str().ok_or("init: a key that is not a string")?;
            if m.get(k).is_none_or(J::is_null) {
                m.insert(k.to_string(), op["value"].clone());
            }
        }
        ("set", J::Object(m)) => {
            m.insert(last.as_str().ok_or("set: a key that is not a string")?.to_string(), op["value"].clone());
        }
        ("set", J::Array(a)) => {
            let i = last.as_u64().ok_or("set: an index that is not a number")? as usize;
            if i == a.len() {
                a.push(op["value"].clone());
            } else {
                *a.get_mut(i).ok_or("set: an index past the end")? = op["value"].clone();
            }
        }
        ("del", J::Object(m)) => {
            m.shift_remove(last.as_str().ok_or("del: a key that is not a string")?).ok_or("del: no such key")?;
        }
        ("del", J::Array(a)) => {
            let i = last.as_u64().ok_or("del: an index that is not a number")? as usize;
            if i >= a.len() {
                return Err("del: an index past the end".into());
            }
            a.remove(i);
        }
        (k, _) => return Err(format!("{k}: not a container at the path's end")),
    }
    Ok(touched)
}

fn step_into<'j>(v: &'j mut J, k: &J) -> Result<&'j mut J, String> {
    match (v, k) {
        (J::Object(m), J::String(s)) => m.get_mut(s).ok_or_else(|| format!("no key {s:?}")),
        (J::Array(a), J::Number(n)) => {
            let i = n.as_u64().ok_or("a negative index")? as usize;
            a.get_mut(i).ok_or_else(|| format!("index {i} past the end"))
        }
        (_, k) => Err(format!("cannot step into {k} here")),
    }
}

// ───────────────────────────── loading a step's world ─────────────────────────────

fn load_snapshot(j: &J) -> Result<Snapshot, String> {
    let snap: Snapshot = serde_json::from_value(j.clone()).map_err(|e| format!("snapshot: {e}"))?;
    snapshot::validate(&snap).map_err(|e| format!("snapshot: {e}"))?;
    layer6::validate(&snap).map_err(|e| format!("snapshot: {e}"))?;
    layer7::validate(&snap).map_err(|e| format!("snapshot: {e}"))?;
    memo::validate(&snap).map_err(|e| format!("snapshot (memo): {e}"))?;
    Ok(snap)
}

fn load_tree(sid: &str, j: &J) -> Result<Option<Tree>, String> {
    if j.is_null() {
        return Ok(None);
    }
    let t: Tree = serde_json::from_value(j.clone()).map_err(|e| format!("trees.{sid}: {e}"))?;
    tree::validate(&t).map_err(|e| format!("trees.{sid}: {e}"))?;
    Ok(Some(t))
}

/// A case's starting world as JSON: inline (`snapshot`, `trees`) or a dump directory (`dump`).
fn base_world(case: &J, file: &Path) -> Result<(J, Map<String, J>), String> {
    if let Some(d) = case["dump"].as_str() {
        let dir = if Path::new(d).is_absolute() { Path::new(d).to_path_buf() } else { file.parent().unwrap_or(Path::new(".")).join(d) };
        let bytes = std::fs::read(dir.join("snapshot.json")).map_err(|e| format!("{}: {e}", dir.display()))?;
        load::refuse_big_ints(&bytes)?;
        let snap: J = serde_json::from_slice(&bytes).map_err(|e| format!("snapshot.json: {e}"))?;
        let mut trees = Map::new();
        for row in snap["alive"].as_array().ok_or("snapshot.json: no alive rows")? {
            let sid = row["sid"].as_str().ok_or("an alive row without a sid")?.to_string();
            let t = match row["tree"].as_str() {
                Some(rel) => {
                    let p = load::tree_path(&dir, rel)?;
                    let b = std::fs::read(&p).map_err(|e| format!("{}: {e}", p.display()))?;
                    serde_json::from_slice(&b).map_err(|e| format!("{}: {e}", p.display()))?
                }
                None => J::Null,
            };
            trees.insert(sid, t);
        }
        return Ok((snap, trees));
    }
    let trees = case["trees"].as_object().cloned().unwrap_or_default();
    Ok((case["snapshot"].clone(), trees))
}

// ───────────────────────────── comparing ─────────────────────────────

#[derive(Default)]
struct Tally {
    cases: usize,
    steps: usize,
    builds: usize,
    decisions: usize,
    hits: usize,
    misses: usize,
    raises: usize,
    fresh_equal: usize,
    stale: usize,
    fresh_skipped: usize,
    diffs: usize,
    differing: usize,
    shown: Vec<String>,
}

impl Tally {
    fn diff(&mut self, s: String) {
        self.diffs += 1;
        if self.shown.len() < MAX_SHOWN {
            self.shown.push(s);
        }
    }
}

fn seg_delta(a: &SegStats, b: &SegStats) -> Vec<(&'static str, u64)> {
    a.named().iter().zip(b.named().iter()).map(|((k, x), (_, y))| (*k, y - x)).collect()
}

fn digests(f: &layer7::Feed) -> (String, String) {
    (sha1::hex(f.canon().as_bytes()), sha1::hex(f.wire().as_bytes()))
}

fn absent_of(f: &layer7::Feed) -> Vec<String> {
    match &f.frame {
        Out::Object(m) => match m.iter().find(|(k, _)| k == "absentSids") {
            Some((_, Out::Array(a))) => a.iter().filter_map(|v| if let Out::Str(s) = v { Some(s.clone()) } else { None }).collect(),
            _ => Vec::new(),
        },
        _ => Vec::new(),
    }
}

/// Where two byte strings first differ, with a little of each around it.
fn first_difference(a: &[u8], b: &[u8]) -> Option<String> {
    let i = a.iter().zip(b).position(|(x, y)| x != y).or((a.len() != b.len()).then(|| a.len().min(b.len())))?;
    let lo = i.saturating_sub(60);
    let show = |s: &[u8]| String::from_utf8_lossy(&s[lo.min(s.len())..(i + 60).min(s.len())]).into_owned();
    Some(format!("byte {i}: python ...{}... rust ...{}...", show(a), show(b)))
}

fn raise_json(r: &romp_views::layer2::Raise) -> J {
    serde_json::json!({"raises": r.exc, "arg": r.arg})
}

fn cases(file: &Path, plant: Option<Plant>) -> Result<bool, String> {
    let t0 = Instant::now();
    let name = file.display().to_string();
    let bytes = std::fs::read(file).map_err(|e| format!("{name}: {e}"))?;
    load::refuse_big_ints(&bytes).map_err(|e| format!("{name}: {e}"))?;
    let doc: J = serde_json::from_slice(&bytes).map_err(|e| format!("{name}: {e}"))?;
    if doc["schema"] != SCHEMA {
        return Err(format!("{name}: schema {}, expected {SCHEMA}", doc["schema"]));
    }
    let mut t = Tally::default();
    for case in doc["cases"].as_array().ok_or_else(|| format!("{name}: no cases"))? {
        let cname = case["name"].as_str().unwrap_or("?").to_string();
        t.cases += 1;
        run_case(case, file, &cname, plant, &mut t)?;
    }
    for s in &t.shown {
        println!("  {s}");
    }
    println!(
        "memo cases{}: {} cases, {} steps, {} builds compared ({} session decisions: {} hits, {} misses, {} raises), {} \
         memo-served builds equal to their fresh build, {} stale where Python's are, {} differences in {} steps ({} fresh \
         comparisons skipped where Python's frame differs; {:.0} ms)",
        plant_note(plant), t.cases, t.steps, t.builds, t.decisions, t.hits, t.misses, t.raises, t.fresh_equal, t.stale,
        t.diffs, t.differing, t.fresh_skipped, t0.elapsed().as_secs_f64() * 1e3
    );
    Ok(if plant.is_some() { t.diffs > 0 } else { t.diffs == 0 })
}

fn run_case(case: &J, file: &Path, cname: &str, plant: Option<Plant>, t: &mut Tally) -> Result<(), String> {
    let (mut snap_j, mut trees_j) = base_world(case, file)?;
    let memo_bound = case["memoBound"].as_u64().ok_or_else(|| format!("{cname}: no memoBound"))? as usize;
    let seg_bound = case["segBound"].as_u64().unwrap_or_else(default_seg_bound);
    let snaps: Arena<Snapshot> = Arena::new();
    let tree_arena: Arena<Option<Tree>> = Arena::new();
    let sid_arena: Arena<String> = Arena::new();
    // each sid's current tree, both held in the arenas so a build's borrows outlive the map's changes
    let mut current: FxMap<String, (&str, &Option<Tree>)> = FxMap::default();
    for (sid, tj) in &trees_j {
        let t = tree_arena.alloc(load_tree(sid, tj).map_err(|e| format!("{cname}: {e}"))?);
        current.insert(sid.clone(), (sid_arena.alloc(sid.clone()).as_str(), t));
    }
    let memo = RefCell::new(FeedMemo::new(memo_bound).with_plant(plant));
    let held = RefCell::new(SegHeld::new(seg_bound));
    for (si, step) in case["steps"].as_array().ok_or_else(|| format!("{cname}: no steps"))?.iter().enumerate() {
        let at = format!("{cname} step {si} ({})", step["note"].as_str().unwrap_or(""));
        let mut touched = Vec::new();
        for op in step["ops"].as_array().map(Vec::as_slice).unwrap_or_default() {
            if let Some(sid) = apply_op(&mut snap_j, &mut trees_j, op).map_err(|e| format!("{at}: {e}"))? {
                touched.push(sid);
            }
        }
        for sid in touched {
            let tj = trees_j.get(&sid).cloned().unwrap_or(J::Null);
            let t = tree_arena.alloc(load_tree(&sid, &tj).map_err(|e| format!("{at}: {e}"))?);
            let key = match current.get(&sid) {
                Some((k, _)) => *k,
                None => sid_arena.alloc(sid.clone()).as_str(),
            };
            current.insert(sid, (key, t));
        }
        let snap: &Snapshot = snaps.alloc(load_snapshot(&snap_j).map_err(|e| format!("{at}: {e}"))?);
        let parts: Vec<(&str, &Tree)> = current.values().filter_map(|&(s, t)| t.as_ref().map(|t| (s, t))).collect();
        let refs = || parts.iter().copied();
        let expect = &step["expect"];
        t.steps += 1;
        let before = t.diffs;

        // the memo build
        let s0 = held.borrow().stats;
        let evict0 = memo.borrow().stats.evict;
        let b = layer6::Build::from_parts(snap, refs(), None).with_held(&held).with_memo(&memo);
        let got = layer7::build_feed_on(&b, None);
        drop(b);
        t.builds += 1;
        let m = memo.borrow();
        let want_dec = expect["decisions"].as_array().ok_or_else(|| format!("{at}: no decisions"))?;
        if want_dec.len() != m.decisions.len() {
            t.diff(format!("{at}: {} sessions decided here, {} in python", m.decisions.len(), want_dec.len()));
        }
        for (d, w) in m.decisions.iter().zip(want_dec) {
            t.decisions += 1;
            match d.hit {
                Some(true) => t.hits += 1,
                Some(false) => t.misses += 1,
                None => {}
            }
            if d.raised.is_some() {
                t.raises += 1;
            }
            let got_d = romp_json::dumps(&romp_views::record::canon(&d.to_out()));
            let want_d = romp_json::dumps(&romp_views::record::canon(&w[1].to_json()));
            if w[0].as_str() != Some(d.sid.as_str()) || got_d != want_d {
                t.diff(format!("{at}: {}: here {got_d}, python {} {want_d}", d.sid, w[0]));
            }
        }
        let seg = seg_delta(&s0, &held.borrow().stats);
        for (k, v) in &seg {
            if expect["seg"][*k].as_u64() != Some(*v) {
                t.diff(format!("{at}: held counter {k} moved {v} here, {} in python", expect["seg"][*k]));
            }
        }
        let (entries, evict) = (m.len() as u64, m.stats.evict - evict0);
        if expect["memo"]["entries"].as_u64() != Some(entries) || expect["memo"]["evict"].as_u64() != Some(evict) {
            t.diff(format!("{at}: the memo holds {entries} entries after {evict} evictions here, python {}",
                           expect["memo"]));
        }
        drop(m);
        let memo_digest = match (&got, expect.get("raises")) {
            (Ok(feed), None) => {
                let (c, w) = digests(feed);
                if expect["canonSha1"].as_str() != Some(&c) || expect["wireSha1"].as_str() != Some(&w) {
                    let first = expect["wire"].as_str().map(|pw| first_difference(pw.as_bytes(), feed.wire().as_bytes()));
                    t.diff(format!("{at}: the memo build's bytes differ from Python's{}",
                                   first.flatten().map_or_else(String::new, |f| format!(": {f}"))));
                }
                let absent = absent_of(feed);
                let want_absent: Vec<&str> = expect["absent"].as_array().into_iter().flatten().filter_map(J::as_str).collect();
                if absent.iter().map(String::as_str).ne(want_absent.iter().copied()) {
                    t.diff(format!("{at}: absent here {absent:?}, python {want_absent:?}"));
                }
                Some((c, w))
            }
            (Err(r), Some(w)) => {
                if raise_json(r) != *w {
                    t.diff(format!("{at}: build_feed raised {} here, python {w}", raise_json(r)));
                }
                None
            }
            (Ok(_), Some(w)) => {
                t.diff(format!("{at}: python's build raised {w}, here it built a feed"));
                None
            }
            (Err(r), None) => {
                t.diff(format!("{at}: build_feed raised here {}: {}, python built a feed", r.exc, r.arg));
                None
            }
        };

        // the fresh build: nothing carried
        let fresh = layer6::Build::from_parts(snap, refs(), None);
        let fgot = layer7::build_feed_on(&fresh, None);
        t.builds += 1;
        let fw = &expect["fresh"];
        let fresh_digest = match (&fgot, fw.get("raises")) {
            (Ok(f), None) => Some(digests(f)),
            (Err(r), Some(w)) => {
                if raise_json(r) != *w {
                    t.diff(format!("{at}: the fresh build raised {} here, python {w}", raise_json(r)));
                }
                None
            }
            (Ok(_), Some(w)) => {
                t.diff(format!("{at}: python's fresh build raised {w}, here it built a feed"));
                None
            }
            (Err(r), None) => {
                t.diff(format!("{at}: the fresh build raised here {}: {}", r.exc, r.arg));
                None
            }
        };
        if fw["frameDiffers"] == true {
            t.fresh_skipped += 1;
        } else if let Some((c, w)) = &fresh_digest {
            if fw["canonSha1"].as_str() != Some(c) || fw["wireSha1"].as_str() != Some(w) {
                t.diff(format!("{at}: the fresh build's bytes differ from Python's fresh build"));
            }
        }
        if let (Some(md), Some(fd)) = (&memo_digest, &fresh_digest) {
            let equal = md == fd;
            if expect["freshEqualsMemo"] == true {
                if equal {
                    t.fresh_equal += 1;
                } else {
                    t.diff(format!("{at}: Python's memo-served build equals its fresh build; here they differ"));
                }
            } else if fw["frameDiffers"] != true {
                if equal {
                    t.diff(format!("{at}: Python's memo-served build is stale; here it equals the fresh build"));
                } else {
                    t.stale += 1;
                }
            }
        }
        if t.diffs > before {
            t.differing += 1;
        }
    }
    Ok(())
}

// ───────────────────────────── the dump form ─────────────────────────────

fn dump_check(dir: &Path, plant: Option<Plant>) -> Result<bool, String> {
    let dump = load::load_dump(dir)?;
    let snap = &dump.snapshot;
    layer6::validate(snap).map_err(|e| format!("snapshot.json: {e}"))?;
    layer7::validate(snap).map_err(|e| format!("snapshot.json: {e}"))?;
    memo::validate(snap).map_err(|e| format!("snapshot.json (memo): {e}"))?;
    let want_canon = std::fs::read(dir.join("oracle").join("feed.canon.json")).map_err(|e| format!("oracle: {e}"))?;
    let want_wire = std::fs::read(dir.join("oracle").join("feed.wire.json")).map_err(|e| format!("oracle: {e}"))?;
    let memo = RefCell::new(FeedMemo::new(usize::MAX).with_plant(plant));
    let held = RefCell::new(SegHeld::new(default_seg_bound()));
    let mut ok = true;
    let mut lines = Vec::new();
    for (i, what) in ["cold", "the peers settle", "served whole"].iter().enumerate() {
        let s0 = held.borrow().stats;
        let t0 = Instant::now();
        let b = layer6::Build::new(snap, &dump.trees, None).with_held(&held).with_memo(&memo);
        let feed = layer7::build_feed_on(&b, None).map_err(|r| format!("build {i} raised {}: {}", r.exc, r.arg))?;
        let ms = t0.elapsed().as_secs_f64() * 1e3;
        drop(b);
        let same = feed.canon().as_bytes() == want_canon.as_slice() && feed.wire().as_bytes() == want_wire.as_slice();
        let m = memo.borrow();
        let hits = m.decisions.iter().filter(|d| d.hit == Some(true)).count();
        let misses: Vec<String> = m
            .decisions
            .iter()
            .filter(|d| d.hit == Some(false))
            .map(|d| format!("{}:{}", &d.sid[d.sid.len().saturating_sub(3)..], d.miss.join("+")))
            .collect();
        let raised = m.decisions.iter().filter(|d| d.raised.is_some()).count();
        let walks = seg_delta(&s0, &held.borrow().stats).iter().find(|(k, _)| *k == "segWalks").map_or(0, |(_, v)| *v);
        ok &= same && raised == 0;
        if i == 2 {
            ok &= hits == m.decisions.len() && walks == 0;
        }
        lines.push(format!(
            "  build {} ({what}): {} hits, {} misses{}, {} raises, {walks} anchor walks, bytes {} the oracle ({ms:.1} ms)",
            i + 1, hits, misses.len(), if misses.is_empty() { String::new() } else { format!(" [{}]", misses.join(" ")) },
            raised, if same { "equal to" } else { "DIFFER from" }
        ));
    }
    for l in &lines {
        println!("{l}");
    }
    println!(
        "memo {}{}: {} sessions, three builds in one process with the memo: {} (canonical {} bytes, wire {} bytes)",
        snap.world.as_ref().and_then(|w| w.name.clone()).unwrap_or_default(), plant_note(plant), snap.alive().len(),
        if ok { "every build equals the oracle and the third is served whole" } else { "FAILED" },
        want_canon.len(), want_wire.len()
    );
    Ok(if plant.is_some() { !ok } else { ok })
}
