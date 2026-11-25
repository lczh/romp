//! The spike's standalone binary (rust/SPIKE.md): load a dump (tools/rust-spike/dump_world.py), check it against
//! Python's own output, and later build the feed and time it.
//!
//!     romp-spike roundtrip DUMP [--plant float-tag]   # typed load, re-serialize through romp-json, compare bytes
//!     romp-spike layer1 DUMP [--plant bytes]          # layer 1 against DUMP/layer1.json (dump_layer1.py)
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
//! `bytes` counts atom_prose_chars in UTF-8 bytes; the store's plants are no-tool-result, vt-strip and skip-held-crc.
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
use romp_views::load;
use romp_views::record::{Num, OMap, ToJson};
use romp_views::snapshot::Snapshot;
use romp_views::tree::{Atom, Content, Tree};

const USAGE: &str =
    "usage: romp-spike roundtrip DUMP [--plant float-tag]\n       romp-spike layer1 DUMP [--plant bytes]\n       romp-spike store DUMP [--records] [--plant NAME]\n       romp-spike store-serve [--plant NAME]";
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
        for sm in st.seams.iter().flatten() {
            if let Some(Some(top)) = &sm.top {
                out.push(Some(top.clone()));
            }
            out.extend(sm.segs.iter().flatten().cloned().map(Some));
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
    fn the_planted_byte_count_differs_only_off_ascii() {
        let a: Atom = serde_json::from_str(
            r#"{"type": "assistant", "t": 1, "message": {"content": [{"type": "text", "text": "na\u00efve"}]}}"#,
        )
        .unwrap();
        assert_eq!(prose_bytes(&a), Out::Int(6));
        assert_eq!(layer1::atom_prose_chars(&a), 5);
    }
}
