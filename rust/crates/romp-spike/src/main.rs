//! The spike's standalone binary (rust/SPIKE.md): load a snapshot and transcripts, build the feed, print canonical
//! JSON and timings. Steps S2 to S6 fill it in.
//!
//!   romp-spike store DUMP [--records] [--plant NAME]
//!       Read every file of DUMP/transcripts/ (a dump_world.py dump) through the transcript store and print the
//!       canonical summary (romp_transcript::canon): per file one line, then one line per record. --records prints
//!       each record's canonical text instead of its CRC. A footer on stderr gives counts, held bytes and time.
//!   romp-spike store-serve [--plant NAME]
//!       One store kept across requests, for tools/rust-spike/check_store.py's lockstep cases: each stdin line
//!       `read<TAB>PATH` answers one JSON line {"kind", "generation", "file", "lines"} (records in full).
//!
//! --plant NAME builds the store with a deliberate defect (no-tool-result, vt-strip, skip-held-crc), which the
//! comparison must catch.

use romp_transcript::canon::{self, RecordMode};
use romp_transcript::{Plant, ReadKind, Store};
use std::io::{self, BufRead, BufWriter, Write};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

fn usage() -> ExitCode {
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
    let Some((records, plant, rest)) = flags(args) else { return usage() };
    let [dump] = rest.as_slice() else { return usage() };
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
    let Some((_, plant, rest)) = flags(args) else { return usage() };
    if !rest.is_empty() {
        return usage();
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
    match args.first().map(String::as_str) {
        Some("store") => store_cmd(&args[1..]),
        Some("store-serve") => serve_cmd(&args[1..]),
        _ => usage(),
    }
}
