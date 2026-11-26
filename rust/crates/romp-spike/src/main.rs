//! The spike's standalone binary (rust/SPIKE.md): load a dump (tools/rust-spike/dump_world.py), check it against
//! Python's own output, and later build the feed and time it.
//!
//!     romp-spike roundtrip DUMP [--plant float-tag]   # typed load, re-serialize through romp-json, compare bytes
//!     romp-spike layer1 DUMP [--plant bytes]          # layer 1 against DUMP/layer1.json (dump_layer1.py)
//!     romp-spike layer2 DUMP|EDGE.json [--plant end|bytes]
//!         Layer 2 (segmentation) against DUMP/layer2.json, or against an edge set (both by dump_layer2.py).
//!     romp-spike store DUMP [--records] [--plant NAME]
//!         Read every file of DUMP/transcripts/ through the transcript store and print the canonical summary
//!         (romp_transcript::canon): per file one line, then one line per record. --records prints each record's
//!         canonical text instead of its CRC. A footer on stderr gives counts, held bytes and time.
//!     romp-spike store-serve [--plant NAME]
//!         One store kept across requests, for tools/rust-spike/check_store.py's lockstep cases: each stdin line
//!         `read<TAB>PATH` answers one JSON line {"kind", "generation", "file", "lines"} (records in full).
//!
//! `--plant` makes one deliberate mistake, to show the comparison catches it: `float-tag` drops the int/float tag of
//! the alive rows' mtime (1781099940.0 written as 1781099940) and gives the first tree's first atom a float `t`,
//! `bytes` counts atom_prose_chars in UTF-8 bytes; layer2's `end` ends each segment at its own last atom and its
//! `bytes` counts the seam prose floor in UTF-8 bytes; the store's plants are no-tool-result, vt-strip and
//! skip-held-crc.
//! Exit status: 0 when everything matched, 1 on any difference, 2 on a usage or load error.

use romp_transcript::canon::{self, RecordMode};
use romp_transcript::{Plant, ReadKind, Store};
use std::io::{self, BufRead, BufWriter, Write};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use romp_json::Value as Out;
use romp_views::layer1::{self, ATOM_COLUMNS, SEG_COLUMNS, TURN_KEYS};
use romp_views::layer2::{self, Plant as Plant2, Probe};
use romp_views::load;
use romp_views::record::{Num, OMap, ToJson};
use romp_views::snapshot::{Snapshot, Store as GoalStore};
use romp_views::tree::{self, Atom, Content, Tree, Turn};

const USAGE: &str =
    "usage: romp-spike roundtrip DUMP [--plant float-tag]\n       romp-spike layer1 DUMP [--plant bytes]\n       romp-spike layer2 DUMP|EDGE.json [--plant end|bytes]\n       romp-spike store DUMP [--records] [--plant NAME]\n       romp-spike store-serve [--plant NAME]";
const MAX_SHOWN: usize = 10;

fn store_usage() -> ExitCode {
    eprintln!("usage: romp-spike store DUMP [--records] [--plant NAME] | romp-spike store-serve [--plant NAME]");
    ExitCode::from(2)
}

/// The flags both store commands take: (records, plant, positional arguments). None on a bad flag.
fn flags(args: &[String]) -> Option<(bool, Option<Plant>, Vec<String>)> {
    let (mut records, mut plant, mut rest) = (false, None, Vec::new());
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => records = true,
            "--plant" => {
                let name = it.next()?;
                let Some(p) = Plant::parse(name) else {
                    eprintln!("romp-spike: unknown plant {name:?}");
                    return None;
                };
                plant = Some(p);
            }
            s if s.starts_with("--") => {
                eprintln!("romp-spike: unknown flag {s}");
                return None;
            }
            _ => rest.push(a.clone()),
        }
    }
    Some((records, plant, rest))
}

fn store_cmd(args: &[String]) -> ExitCode {
    let Some((records, plant, rest)) = flags(args) else { return store_usage() };
    let [dump] = rest.as_slice() else { return store_usage() };
    let dir = Path::new(dump).join("transcripts");
    let mut names: Vec<PathBuf> = match std::fs::read_dir(&dir) {
        Ok(rd) => rd.filter_map(|d| d.ok()).filter(|d| d.path().is_file()).map(|d| d.path()).collect(),
        Err(e) => {
            eprintln!("romp-spike: cannot list {}: {e}", dir.display());
            return ExitCode::FAILURE;
        }
    };
    names.sort_by(|a, b| a.file_name().map(OsStrExt::as_bytes).cmp(&b.file_name().map(OsStrExt::as_bytes)));
    let mode = if records { RecordMode::Full } else { RecordMode::Crc };
    let mut store = Store::with_plant(plant);
    let t = Instant::now();
    for p in &names {
        store.read(p);
    }
    let read_time = t.elapsed();
    let stdout = io::stdout();
    let mut out = BufWriter::with_capacity(1 << 20, stdout.lock());
    let (mut nrec, mut held, mut index, mut alloc) = (0usize, 0usize, 0usize, 0usize);
    let mut line = String::new();
    for p in &names {
        let name = format!("transcripts/{}", p.file_name().map(|n| n.to_string_lossy()).unwrap_or_default());
        let Some(e) = store.get(p) else {
            line.clear();
            line.push_str("{\"file\": ");
            romp_json::write_str(&mut line, &name);
            line.push_str(", \"unreadable\": true}");
            let _ = writeln!(out, "{line}");
            continue;
        };
        line.clear();
        canon::file_header(&mut line, &name, e);
        let _ = writeln!(out, "{line}");
        for (i, l) in e.lines().enumerate() {
            line.clear();
            canon::line_summary(&mut line, i, &l, mode);
            let _ = writeln!(out, "{line}");
        }
        nrec += e.records();
        held += e.bytes_held();
        index += e.index_bytes();
        alloc += e.allocated_bytes();
    }
    if out.flush().is_err() {
        return ExitCode::FAILURE;
    }
    eprintln!(
        "romp-spike store: {} files, {nrec} records, {held} bytes held, index {index} bytes, {alloc} bytes allocated, \
         {} bytes read, indexed in {:.1} ms",
        names.len(),
        store.stats.bytes_read,
        read_time.as_secs_f64() * 1e3
    );
    ExitCode::SUCCESS
}

fn serve_cmd(args: &[String]) -> ExitCode {
    let Some((_, plant, rest)) = flags(args) else { return store_usage() };
    if !rest.is_empty() {
        return store_usage();
    }
    let mut store = Store::with_plant(plant);
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for req in stdin.lock().lines() {
        let Ok(req) = req else { return ExitCode::FAILURE };
        let Some(("read", path)) = req.split_once('\t') else {
            eprintln!("romp-spike store-serve: bad request {req:?}");
            return ExitCode::from(2);
        };
        let p = Path::new(path);
        let kind = store.read(p);
        let mut line = String::from("{\"kind\": \"");
        line.push_str(kind.name());
        line.push('"');
        match store.get(p) {
            Some(e) if !matches!(kind, ReadKind::Absent | ReadKind::Failed) => {
                line.push_str(&format!(", \"generation\": {}, \"file\": ", e.generation()));
                canon::file_header(&mut line, path, e);
                line.push_str(", \"lines\": [");
                for (i, l) in e.lines().enumerate() {
                    if i > 0 {
                        line.push_str(", ");
                    }
                    canon::line_summary(&mut line, i, &l, RecordMode::Full);
                }
                line.push(']');
            }
            _ => line.push_str(", \"generation\": null, \"file\": null, \"lines\": []"),
        }
        line.push('}');
        if writeln!(out, "{line}").and_then(|_| out.flush()).is_err() {
            return ExitCode::FAILURE;
        }
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (cmd, rest) = match args.split_first() {
        Some((c, r)) => (c.as_str(), r),
        None => return usage(),
    };
    match cmd {
        "store" => return store_cmd(rest),
        "store-serve" => return serve_cmd(rest),
        _ => {}
    }
    let (dir, plant) = match rest {
        [d] => (PathBuf::from(d), None),
        [d, flag, p] if flag == "--plant" => (PathBuf::from(d), Some(p.as_str())),
        _ => return usage(),
    };
    let result = match (cmd, plant) {
        ("roundtrip", None | Some("float-tag")) => roundtrip(&dir, plant.is_some()),
        ("layer1", None | Some("bytes")) => layer1_cmd(&dir, plant.is_some()),
        ("layer2", None) => layer2_cmd(&dir, None),
        ("layer2", Some("end")) => layer2_cmd(&dir, Some(Plant2::EndAtLastAtom)),
        ("layer2", Some("bytes")) => layer2_cmd(&dir, Some(Plant2::FloorInBytes)),
        _ => return usage(),
    };
    match result {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(1),
        Err(e) => {
            eprintln!("romp-spike: {e}");
            ExitCode::from(2)
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

fn read(path: &Path) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))
}

fn world_name(snap: &Snapshot) -> String {
    snap.world.as_ref().and_then(|w| w.name.clone()).unwrap_or_else(|| "?".to_string())
}

/// Where two byte strings first differ, with a little of each around it.
fn first_difference(a: &[u8], b: &[u8]) -> Option<String> {
    let i = a.iter().zip(b).position(|(x, y)| x != y).or((a.len() != b.len()).then(|| a.len().min(b.len())))?;
    let lo = i.saturating_sub(40);
    let show = |s: &[u8]| String::from_utf8_lossy(&s[lo.min(s.len())..(i + 40).min(s.len())]).into_owned();
    Some(format!("byte {i}: python ...{}... rust ...{}...", show(a), show(b)))
}

/// Load the snapshot and every tree typed, write each back through romp-json and compare with the file's bytes.
fn roundtrip(dir: &Path, plant_float_tag: bool) -> Result<bool, String> {
    let t0 = Instant::now();
    let snap_bytes = read(&dir.join("snapshot.json"))?;
    let mut snap = load::snapshot_from_slice(&snap_bytes).map_err(|e| format!("snapshot.json: {e}"))?;
    let mut trees = Vec::new();
    for row in snap.alive() {
        if let Some(Some(rel)) = &row.tree {
            let path = load::tree_path(dir, rel)?;
            let bytes = read(&path)?;
            let tree = load::tree_from_slice(&bytes).map_err(|e| format!("{rel}: {e}"))?;
            trees.push((rel.clone(), bytes, tree));
        }
    }
    let load_ms = t0.elapsed().as_secs_f64() * 1e3;
    if plant_float_tag {
        for row in snap.alive.iter_mut().flatten() {
            if let Some(Num::Float(f)) = row.mtime {
                row.mtime = Some(Num::Int(f as i64));
            }
        }
        // and the other way in the first tree: its first atom's int t written as a float
        let first_atom = trees
            .first_mut()
            .and_then(|(_, _, t)| t.turns.as_mut()?.first_mut()?.atoms.as_mut()?.first_mut());
        if let Some(atom) = first_atom
            && let Some(Num::Int(i)) = atom.t
        {
            atom.t = Some(Num::Float(i as f64));
        }
    }
    let t1 = Instant::now();
    let mut ok = true;
    let snap_out = romp_json::dumps(&snap.to_json());
    match first_difference(&snap_bytes, snap_out.as_bytes()) {
        None => {}
        Some(d) => {
            ok = false;
            println!("snapshot.json differs at {d}");
        }
    }
    let (mut same, mut tree_bytes) = (0, 0);
    for (rel, bytes, tree) in &trees {
        let out = romp_json::dumps(&tree.to_json());
        tree_bytes += bytes.len();
        match first_difference(bytes, out.as_bytes()) {
            None => same += 1,
            Some(d) => {
                ok = false;
                println!("{rel} differs at {d}");
            }
        }
    }
    let write_ms = t1.elapsed().as_secs_f64() * 1e3;
    println!(
        "roundtrip {}{}: snapshot.json {} ({} bytes), trees {} of {} identical ({} bytes); typed load {:.0} ms, \
         write {:.0} ms",
        world_name(&snap),
        if plant_float_tag { " [planted: float-tag]" } else { "" },
        if snap_out.as_bytes() == snap_bytes.as_slice() { "identical" } else { "DIFFERS" },
        snap_bytes.len(),
        same,
        trees.len(),
        tree_bytes,
        load_ms,
        write_ms
    );
    Ok(ok)
}

/// The seg-id-shaped strings of a dump, in the order dump_layer1.py's seg_inputs lists them.
fn seg_inputs(snap: &Snapshot, trees: &[(String, Option<Tree>)]) -> Vec<Option<String>> {
    let mut out = Vec::new();
    for (_sid, tree) in trees {
        for turn in tree.iter().flat_map(|t| t.turns.iter().flatten()) {
            out.push(turn.id.clone());
        }
    }
    for (_path, _sid, st) in snap.stores() {
        for (nid, nd) in st.nodes.iter().flat_map(OMap::iter) {
            out.push(Some(nid.clone()));
            out.extend(nd.trail.iter().flatten().cloned().map(Some));
            out.extend(nd.log.iter().flatten().filter_map(|r| r.seg.clone().flatten()).map(Some));
        }
        for (k, v) in st.placements.iter().flat_map(OMap::iter) {
            out.push(Some(k.clone()));
            out.push(Some(v.clone()));
        }
        out.extend(st.status.iter().flat_map(OMap::keys).cloned().map(Some));
        if let Some(Some(last)) = &st.last_node {
            out.push(Some(last.clone()));
        }
        for sm in st.seams.iter().flatten().flatten() {
            if let Some(Some(top)) = &sm.top {
                out.push(Some(top.clone()));
            }
            out.extend(sm.segs.iter().flatten().flatten().cloned().map(Some));
        }
    }
    out
}

/// The planted mistake: atom_prose_chars counted in UTF-8 bytes.
fn prose_bytes(a: &Atom) -> Out {
    if a.kind() != "assistant" || matches!(a.is_api_error, Some(Some(true))) {
        return Out::Int(0);
    }
    match a.message().filter(|m| m.truthy()).and_then(|m| m.content.as_ref()) {
        Some(Content::Blocks(b)) => {
            Out::Int(layer1::Blocks::List(b).texts().map(str::len).sum::<usize>() as i64)
        }
        _ => Out::Int(0),
    }
}

struct Diffs {
    count: usize,
    shown: Vec<String>,
}

impl Diffs {
    fn check(&mut self, got: &Out, want: &serde_json::Value, at: impl FnOnce() -> String) {
        if *got != want.to_json() {
            self.count += 1;
            if self.shown.len() < MAX_SHOWN {
                self.shown.push(format!("{}: rust {} python {}", at(), romp_json::dumps(got), want));
            }
        }
    }
}

fn names(v: &serde_json::Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default()
}

/// Layer 1 over the typed trees against Python's answers in DUMP/layer1.json.
fn layer1_cmd(dir: &Path, plant_bytes: bool) -> Result<bool, String> {
    let dump = load::load_dump(dir)?;
    let expect: serde_json::Value =
        serde_json::from_slice(&read(&dir.join("layer1.json"))?).map_err(|e| format!("layer1.json: {e}"))?;
    if expect["schema"] != "romp-spike-layer1/1" {
        return Err(format!("layer1.json: schema {}, expected romp-spike-layer1/1", expect["schema"]));
    }
    let mut cols: Vec<(&str, layer1::AtomFn)> = ATOM_COLUMNS.to_vec();
    if plant_bytes {
        let i = cols.iter().position(|(n, _)| *n == "em.atom_prose_chars").expect("the column exists");
        cols[i].1 = prose_bytes;
    }
    let want_cols: Vec<String> =
        std::iter::once("uuid").chain(cols.iter().map(|(n, _)| *n)).map(String::from).collect();
    let want_turn: Vec<String> = TURN_KEYS.iter().map(|k| format!("em.turn_scalar:{k}")).collect();
    let want_seg: Vec<String> =
        std::iter::once("input").chain(SEG_COLUMNS.iter().map(|(n, _)| *n)).map(String::from).collect();
    if names(&expect["atomColumns"]) != want_cols
        || names(&expect["turnColumns"]) != want_turn
        || names(&expect["segColumns"]) != want_seg
    {
        return Err("layer1.json's columns are not this port's (regenerate it with dump_layer1.py)".to_string());
    }
    let sessions = expect["sessions"].as_array().ok_or("layer1.json: no sessions")?;
    if sessions.len() != dump.trees.len() {
        return Err(format!("layer1.json has {} sessions, the dump {}", sessions.len(), dump.trees.len()));
    }
    let t0 = Instant::now();
    let mut d = Diffs { count: 0, shown: Vec::new() };
    let (mut n_atoms, mut n_turns, mut values) = (0usize, 0usize, 0usize);
    let (mut non_ascii, mut straddle) = (0usize, Vec::new());
    for ((sid, tree), sess) in dump.trees.iter().zip(sessions) {
        if sess["sid"].as_str() != Some(sid.as_str()) {
            return Err(format!("layer1.json's sessions are out of order at {sid}"));
        }
        let rows = sess["atoms"].as_array().ok_or("layer1.json: a session without atoms")?;
        let trows = sess["turns"].as_array().ok_or("layer1.json: a session without turns")?;
        let turns: &[romp_views::tree::Turn] = tree.as_ref().and_then(|t| t.turns.as_deref()).unwrap_or_default();
        let atoms: Vec<&Atom> = turns.iter().flat_map(|t| t.atoms.iter().flatten()).collect();
        if atoms.len() != rows.len() || turns.len() != trows.len() {
            return Err(format!("{sid}: {} atoms and {} turns here, {} and {} in layer1.json", atoms.len(),
                               turns.len(), rows.len(), trows.len()));
        }
        for (ai, (a, row)) in atoms.iter().zip(rows).enumerate() {
            let row = row.as_array().ok_or("layer1.json: an atom row is not a list")?;
            if row[0].as_str() != a.uuid() {
                return Err(format!("{sid} atom {ai}: uuid {:?} here, {} in layer1.json", a.uuid(), row[0]));
            }
            for ((name, f), want) in cols.iter().zip(&row[1..]) {
                values += 1;
                d.check(&f(a), want, || format!("{sid} atom {ai} ({}) {name}", a.uuid().unwrap_or("-")));
            }
            let chars = layer1::atom_prose_chars(a);
            let bytes: usize = layer1::content(a.message()).texts().map(str::len).sum();
            if chars > 0 && chars != bytes {
                non_ascii += 1;
                if chars < layer1::CITE_MIN_CHARS && bytes >= layer1::CITE_MIN_CHARS {
                    straddle.push(format!("{} ({chars} code points, {bytes} bytes)", a.uuid().unwrap_or("-")));
                }
            }
        }
        for (ti, (t, row)) in turns.iter().zip(trows).enumerate() {
            for (k, want) in TURN_KEYS.iter().zip(row.as_array().ok_or("a turn row is not a list")?) {
                values += 1;
                d.check(&layer1::turn_scalar(t, k), want, || format!("{sid} turn {ti} turn_scalar({k:?})"));
            }
        }
        n_atoms += atoms.len();
        n_turns += turns.len();
    }
    let seg_rows = expect["segKeys"].as_array().ok_or("layer1.json: no segKeys")?;
    let inputs = seg_inputs(&dump.snapshot, &dump.trees);
    let theirs: Vec<Option<String>> =
        seg_rows.iter().map(|r| r.get(0).and_then(|v| v.as_str()).map(String::from)).collect();
    if inputs != theirs {
        let i = inputs.iter().zip(&theirs).position(|(a, b)| a != b).unwrap_or(inputs.len().min(theirs.len()));
        return Err(format!("the seg-id inputs differ from layer1.json's at {i} ({} here, {} there)", inputs.len(),
                           theirs.len()));
    }
    for (i, row) in seg_rows.iter().enumerate() {
        let row = row.as_array().ok_or("a segKeys row is not a list")?;
        for ((name, f), want) in SEG_COLUMNS.iter().zip(&row[1..]) {
            values += 1;
            d.check(&f(inputs[i].as_deref()), want, || format!("seg id {i} {:?} {name}", inputs[i]));
        }
    }
    let ms = t0.elapsed().as_secs_f64() * 1e3;
    for s in &d.shown {
        println!("  {s}");
    }
    println!(
        "layer1 {}{}: {} sessions, {} atoms x {} functions, {} turns x {}, {} seg ids x {}: {} values compared, {} \
         differences ({:.0} ms; python {})",
        world_name(&dump.snapshot),
        if plant_bytes { " [planted: bytes]" } else { "" },
        dump.trees.len(),
        n_atoms,
        cols.len(),
        n_turns,
        TURN_KEYS.len(),
        inputs.len(),
        SEG_COLUMNS.len(),
        values,
        d.count,
        ms,
        expect["python"].as_str().unwrap_or("?")
    );
    println!("  non-ASCII prose atoms {non_ascii}; under the {} floor in code points but not in bytes: {}",
             layer1::CITE_MIN_CHARS, if straddle.is_empty() { "none".to_string() } else { straddle.join(", ") });
    Ok(d.count == 0)
}

/// A field-by-field comparison of layer-2 answers (Python's, read from JSON, against the port's), counting values.
#[derive(Default)]
struct Cmp {
    values: usize,
    diffs: usize,
    shown: Vec<String>,
}

fn count_str(v: Option<u64>) -> String {
    v.map_or_else(|| "absent".to_string(), |n| n.to_string())
}

fn dumps_short(v: Option<&Out>) -> String {
    let Some(v) = v else { return "<absent>".to_string() };
    let s = romp_json::dumps(v);
    if s.chars().count() > 160 { format!("{}...", s.chars().take(160).collect::<String>()) } else { s }
}

fn member<'a>(o: &'a Out, key: &str) -> Option<&'a Out> {
    match o {
        Out::Object(m) => m.iter().find(|(k, _)| k == key).map(|(_, v)| v),
        _ => None,
    }
}

fn keys_of(o: &Out) -> Vec<&str> {
    match o {
        Out::Object(m) => m.iter().map(|(k, _)| k.as_str()).collect(),
        _ => Vec::new(),
    }
}

impl Cmp {
    fn diff(&mut self, what: String) {
        self.diffs += 1;
        if self.shown.len() < MAX_SHOWN {
            self.shown.push(what);
        }
    }

    /// One answer: a list of segments (one value for the count, then per segment its key order and each key), or
    /// anything else (an exception, a null split) as one value. Texts are compared as romp-json writes them, so an
    /// int/float tag or a negative zero counts.
    fn answer(&mut self, got: &Out, want: &Out, at: &dyn Fn() -> String) {
        self.values += 1;
        let (Out::Array(g), Out::Array(w)) = (got, want) else {
            if romp_json::dumps(got) != romp_json::dumps(want) {
                self.diff(format!("{}: rust {} python {}", at(), dumps_short(Some(got)), dumps_short(Some(want))));
            }
            return;
        };
        if g.len() != w.len() {
            self.diff(format!("{}: {} segments here, {} in python", at(), g.len(), w.len()));
            return;
        }
        for (i, (gs, ws)) in g.iter().zip(w).enumerate() {
            let (kg, kw) = (keys_of(gs), keys_of(ws));
            self.values += 1;
            if kg != kw {
                self.diff(format!("{} segment {i}: keys rust {kg:?} python {kw:?}", at()));
            }
            for k in kw {
                self.values += 1;
                let (a, b) = (member(gs, k), member(ws, k));
                if a.map(romp_json::dumps) != b.map(romp_json::dumps) {
                    self.diff(format!("{} segment {i} {k}: rust {} python {}", at(), dumps_short(a), dumps_short(b)));
                }
            }
        }
    }
}

/// The coverage counts both sides keep (dump_layer2.py's `coverage`, the port's `Probe` plus its own counts).
const COVERAGE_KEYS: [&str; 9] =
    ["turns", "segments", "seamTails", "keyHits", "raises", "pieces", "splitCalls", "splits", "seams"];

#[derive(Default)]
struct Coverage {
    turns: usize,
    segments: usize,
    seam_tails: usize,
    key_hits: usize,
    raises: usize,
    seams: usize,
}

impl Coverage {
    fn count(&mut self, got: &Result<Vec<layer2::Segment<'_>>, layer2::Raise>, store: Option<&GoalStore>) {
        self.turns += 1;
        match got {
            Ok(segs) => {
                self.segments += segs.len();
                self.seam_tails += segs.iter().filter(|s| s.seam).count();
                self.key_hits += segs.iter().filter(|s| layer2::key_listed(s, store)).count();
            }
            Err(_) => self.raises += 1,
        }
    }

    fn get(&self, probe: &Probe, key: &str) -> usize {
        match key {
            "turns" => self.turns,
            "segments" => self.segments,
            "seamTails" => self.seam_tails,
            "keyHits" => self.key_hits,
            "raises" => self.raises,
            "pieces" => probe.pieces,
            "splitCalls" => probe.split_calls,
            "splits" => probe.splits,
            "seams" => self.seams,
            _ => unreachable!("a coverage key"),
        }
    }
}

fn plant_name(plant: Option<Plant2>) -> &'static str {
    match plant {
        None => "",
        Some(Plant2::EndAtLastAtom) => " [planted: end]",
        Some(Plant2::FloorInBytes) => " [planted: bytes]",
    }
}

fn check_floor(doc: &serde_json::Value, file: &str) -> Result<(), String> {
    let floor = doc["constants"]["SEAM_PROSE_FLOOR"].as_u64();
    if floor != Some(layer2::SEAM_PROSE_FLOOR as u64) {
        return Err(format!("{file}: SEAM_PROSE_FLOOR is {floor:?} in Python, {} here", layer2::SEAM_PROSE_FLOOR));
    }
    Ok(())
}

/// Layer 2 against Python's answers: DUMP/layer2.json for a dump directory, or an edge set file.
fn layer2_cmd(path: &Path, plant: Option<Plant2>) -> Result<bool, String> {
    if path.is_dir() { layer2_dump(path, plant) } else { layer2_edge(path, plant) }
}

fn layer2_dump(dir: &Path, plant: Option<Plant2>) -> Result<bool, String> {
    let dump = load::load_dump(dir)?;
    let expect: serde_json::Value =
        serde_json::from_slice(&read(&dir.join("layer2.json"))?).map_err(|e| format!("layer2.json: {e}"))?;
    if expect["schema"] != "romp-spike-layer2/1" {
        return Err(format!("layer2.json: schema {}, expected romp-spike-layer2/1", expect["schema"]));
    }
    check_floor(&expect, "layer2.json")?;
    let sessions = expect["sessions"].as_array().ok_or("layer2.json: no sessions")?;
    if sessions.len() != dump.trees.len() {
        return Err(format!("layer2.json has {} sessions, the dump {}", sessions.len(), dump.trees.len()));
    }
    let t0 = Instant::now();
    let (mut cmp, mut cov, mut probe) = (Cmp::default(), Coverage::default(), Probe { plant, ..Probe::default() });
    let facts = dump.snapshot.sessions.as_ref().ok_or("snapshot.json: no sessions")?;
    for ((sid, tree), sess) in dump.trees.iter().zip(sessions) {
        if sess["sid"].as_str() != Some(sid.as_str()) {
            return Err(format!("layer2.json's sessions are out of order at {sid}"));
        }
        let store: Option<&GoalStore> = facts.get(sid).and_then(|f| f.store.as_ref()).and_then(Option::as_ref);
        cov.seams += store
            .and_then(|s| s.seams.as_ref())
            .and_then(Option::as_ref)
            .map_or(0, |v| v.iter().filter(|s| matches!(&s.segs, Some(Some(k)) if !k.is_empty())).count());
        let turns: &[Turn] = tree.as_ref().and_then(|t| t.turns.as_deref()).unwrap_or_default();
        let rows = sess["turns"].as_array().ok_or("layer2.json: a session without turns")?;
        if rows.len() != turns.len() {
            return Err(format!("{sid}: {} turns here, {} in layer2.json", turns.len(), rows.len()));
        }
        for (ti, (turn, want)) in turns.iter().zip(rows).enumerate() {
            let got = layer2::segs_seam_with(turn, store, &mut probe);
            cov.count(&got, store);
            cmp.answer(&layer2::answer_json(&got, &layer2::atom_index(turn)), &want.to_json(), &|| {
                format!("{sid} turn {ti} ({})", turn.id.as_deref().unwrap_or("-"))
            });
        }
    }
    let ms = t0.elapsed().as_secs_f64() * 1e3;
    let mut cov_diffs = Vec::new();
    for k in COVERAGE_KEYS {
        let (r, p) = (cov.get(&probe, k), expect["coverage"][k].as_u64());
        if p != Some(r as u64) {
            cov_diffs.push(format!("{k} rust {r} python {}", count_str(p)));
        }
    }
    for s in &cmp.shown {
        println!("  {s}");
    }
    println!(
        "layer2 {}{}: {} sessions, {} turns, {} segments ({} seam tails), {} values compared, {} differences ({:.0} \
         ms; python {})",
        world_name(&dump.snapshot),
        plant_name(plant),
        dump.trees.len(),
        cov.turns,
        cov.segments,
        cov.seam_tails,
        cmp.values,
        cmp.diffs,
        ms,
        expect["python"].as_str().unwrap_or("?")
    );
    println!(
        "  coverage: {} seams, {} segments whose key a seam lists, {} pieces examined, {} split calls, {} splits, {} \
         raises; python's counts {}",
        cov.seams,
        cov.key_hits,
        probe.pieces,
        probe.split_calls,
        probe.splits,
        cov.raises,
        if cov_diffs.is_empty() { "the same".to_string() } else { format!("DIFFER: {}", cov_diffs.join(", ")) }
    );
    Ok(cmp.diffs == 0 && cov_diffs.is_empty())
}

/// One edge case loaded through the port's loader: the turn, then the store (null is no store).
fn load_case(case: &serde_json::Value) -> Result<(Turn, Option<GoalStore>), String> {
    let turn: Turn = serde_json::from_value(case["turn"].clone()).map_err(|e| format!("turn: {e}"))?;
    tree::validate_turn(&turn).map_err(|e| format!("turn{e}"))?;
    let store = match &case["store"] {
        serde_json::Value::Null => None,
        v => Some(serde_json::from_value::<GoalStore>(v.clone()).map_err(|e| format!("store: {e}"))?),
    };
    Ok((turn, store))
}

fn layer2_edge(file: &Path, plant: Option<Plant2>) -> Result<bool, String> {
    let name = file.display().to_string();
    let doc: serde_json::Value = serde_json::from_slice(&read(file)?).map_err(|e| format!("{name}: {e}"))?;
    if doc["schema"] != "romp-spike-layer2-edge/1" {
        return Err(format!("{name}: schema {}, expected romp-spike-layer2-edge/1", doc["schema"]));
    }
    check_floor(&doc, &name)?;
    let cases = doc["cases"].as_array().ok_or_else(|| format!("{name}: no cases"))?;
    let mut cmp = Cmp::default();
    let (mut loaded, mut refused, mut raised, mut raised_refused, mut counts) = (0, 0, 0, 0, 0);
    let mut probe_total = Probe::default();
    for case in cases {
        let cname = case["name"].as_str().ok_or("a case without a name")?;
        let want = case["expect"].to_json();
        let python_raises = case["expect"].get("raises").is_some();
        let (turn, store) = match load_case(case) {
            Ok(x) => x,
            Err(e) => {
                if cname.starts_with("REFUSE ") {
                    refused += 1;
                    raised_refused += usize::from(python_raises);
                } else {
                    cmp.diff(format!("{cname}: the loader refused it: {e}"));
                }
                continue;
            }
        };
        if cname.starts_with("REFUSE ") {
            cmp.diff(format!("{cname}: the loader accepted it"));
            continue;
        }
        loaded += 1;
        raised += usize::from(python_raises);
        let index = layer2::atom_index(&turn);
        let mut probe = Probe { plant, ..Probe::default() };
        let got = match case["kind"].as_str() {
            Some("segs") => layer2::answer_json(&layer2::segs_seam_with(&turn, store.as_ref(), &mut probe), &index),
            Some("split") => {
                let t: Num = serde_json::from_value(case["t"].clone()).map_err(|e| format!("{cname}: t: {e}"))?;
                layer2::split_answer_json(&layer2::split_first_with(&turn, t, &mut probe), &index)
            }
            k => return Err(format!("{cname}: unknown kind {k:?}")),
        };
        cmp.answer(&got, &want, &|| cname.to_string());
        for (k, r) in [("pieces", probe.pieces), ("splitCalls", probe.split_calls), ("splits", probe.splits)] {
            counts += 1;
            let p = case["counts"][k].as_u64();
            if p != Some(r as u64) {
                cmp.diff(format!("{cname}: {k} rust {r} python {}", count_str(p)));
            }
        }
        probe_total.pieces += probe.pieces;
        probe_total.split_calls += probe.split_calls;
        probe_total.splits += probe.splits;
    }
    for s in &cmp.shown {
        println!("  {s}");
    }
    println!(
        "layer2 edge set{}: {} cases, {} loaded and compared ({} values, {} counts), {} refused at load (every REFUSE \
         case), {} differences (python {})",
        plant_name(plant),
        cases.len(),
        loaded,
        cmp.values,
        counts,
        refused,
        cmp.diffs,
        doc["python"].as_str().unwrap_or("?")
    );
    println!(
        "  Python raises on {} cases: {} matched as the same exception here, {} refused at load; over the loaded \
         cases {} pieces examined, {} split calls, {} splits",
        raised + raised_refused,
        raised,
        raised_refused,
        probe_total.pieces,
        probe_total.split_calls,
        probe_total.splits
    );
    Ok(cmp.diffs == 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_difference_names_the_byte_and_shows_both_sides() {
        assert_eq!(first_difference(b"abc", b"abc"), None);
        assert_eq!(first_difference(b"\"mtime\": 1.0}", b"\"mtime\": 1}").unwrap(),
                   "byte 10: python ...\"mtime\": 1.0}... rust ...\"mtime\": 1}...");
        assert_eq!(first_difference(b"ab", b"abc").unwrap(), "byte 2: python ...ab... rust ...abc...");
    }

    #[test]
    fn the_layer2_comparison_names_each_differing_field() {
        let seg = |end: Out, extra: bool| {
            let mut m = vec![("t".to_string(), Out::Int(1)), ("end".to_string(), end)];
            if extra {
                m.push(("seam".to_string(), Out::Bool(true)));
            }
            Out::Object(m)
        };
        let mut c = Cmp::default();
        let one = |end: Out, extra: bool| Out::Array(vec![seg(end, extra)]);
        c.answer(&one(Out::Int(2), false), &one(Out::Int(2), false), &|| "a".into());
        assert_eq!((c.values, c.diffs), (4, 0), "the count, the key order and two keys");
        c.answer(&one(Out::Int(2), false), &one(Out::Float(2.0), true), &|| "b".into());
        assert_eq!(c.shown, [
            "b segment 0: keys rust [\"t\", \"end\"] python [\"t\", \"end\", \"seam\"]",
            "b segment 0 end: rust 2 python 2.0",
            "b segment 0 seam: rust <absent> python true",
        ]);
        c.answer(&Out::Array(vec![]), &Out::Array(vec![seg(Out::Int(2), false)]), &|| "c".into());
        assert_eq!(c.shown[3], "c: 0 segments here, 1 in python");
        let raise = layer2::Raise { exc: "KeyError", arg: "'t'".into() }.to_json();
        c.answer(&Out::Null, &raise, &|| "d".into());
        assert_eq!(c.shown[4], "d: rust null python {\"raises\": \"KeyError\", \"arg\": \"'t'\"}");
        assert_eq!(c.diffs, 5);
        assert_eq!(count_str(None), "absent");
    }

    #[test]
    fn the_planted_byte_count_differs_only_off_ascii() {
        let a: Atom = serde_json::from_str(
            r#"{"type": "assistant", "t": 1, "message": {"content": [{"type": "text", "text": "na\u00efve"}]}}"#,
        )
        .unwrap();
        assert_eq!(prose_bytes(&a), Out::Int(6));
        assert_eq!(layer1::atom_prose_chars(&a), 5);
    }
}
