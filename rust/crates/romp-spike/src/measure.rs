//! S6's measurement, the Rust side (rust/SPIKE.md, "S6 method"). Two modes, each printing one JSON line on stdout:
//!
//!     romp-spike measure cpu DUMP [--builds K]
//!     romp-spike measure mem DIR PAYLOAD [--appends K]
//!
//! `cpu` loads the dump (timed), builds the feed once (the cold build, the first in the process) and checks its
//! canonical and wire bytes against the dump's oracle before anything is reported: a build that differs is never timed.
//! Then K more builds (warm; the port keeps nothing between builds, so each derives every session) and K builds of the
//! entries alone (layer 6's `Build::new` plus `entries`, the derivation without the fold, the passes or the frame).
//! The returned feed is dropped after the clock stops, as Python's harness keeps its result past its clock.
//!
//! `mem` reads every file of DIR through the transcript store (romp-transcript, S3) and records the resident size before
//! and after, then K rounds of appending PAYLOAD/<file name> to each file and reading every file again, recording after
//! each round. The driver (tools/rust-spike/measure_drive.py) makes DIR a fresh copy of a dump's transcripts with
//! mtimes near now and PAYLOAD each file's last line, so this side and Python's append the same bytes.
//!
//! The clocks are raw externs into libSystem (no crate): process CPU from clock_gettime(CLOCK_PROCESS_CPUTIME_ID),
//! cross-checked with getrusage(RUSAGE_SELF) and the thread's CPU clock, each beside the wall time (`Instant`); the
//! resident size from task_info(MACH_TASK_BASIC_INFO), the footprint and compressed bytes from task_info(TASK_VM_INFO),
//! the peak from getrusage's ru_maxrss. A spin of 100 ms of wall time calibrates the CPU clock against the wall clock.
//! On Linux the CPU clocks work and the memory fields are null.

use std::fs::{self, OpenOptions};
use std::hint::black_box;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{Value as J, json};

use romp_transcript::{ReadKind, Store};
use romp_views::{layer6, layer7, load};

mod sys {
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Timespec {
        pub tv_sec: i64,
        pub tv_nsec: i64,
    }

    /// Darwin's `struct timeval`: `suseconds_t` is 32 bits there (repr(C) pads it to 16 bytes).
    #[cfg(target_os = "macos")]
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Timeval {
        pub tv_sec: i64,
        pub tv_usec: i32,
    }

    #[cfg(not(target_os = "macos"))]
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Timeval {
        pub tv_sec: i64,
        pub tv_usec: i64,
    }

    /// `struct rusage`: the two times, then 14 longs, ru_maxrss first (bytes on Darwin, KiB on Linux).
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Rusage {
        pub ru_utime: Timeval,
        pub ru_stime: Timeval,
        pub ru_maxrss: i64,
        pub rest: [i64; 13],
    }

    pub const RUSAGE_SELF: i32 = 0;
    #[cfg(target_os = "macos")]
    pub const CLOCK_PROCESS_CPUTIME_ID: i32 = 12; // <time.h>, Darwin
    #[cfg(target_os = "macos")]
    pub const CLOCK_THREAD_CPUTIME_ID: i32 = 16;
    #[cfg(not(target_os = "macos"))]
    pub const CLOCK_PROCESS_CPUTIME_ID: i32 = 2; // <time.h>, Linux
    #[cfg(not(target_os = "macos"))]
    pub const CLOCK_THREAD_CPUTIME_ID: i32 = 3;
    #[cfg(target_os = "macos")]
    pub const MAXRSS_UNIT: u64 = 1;
    #[cfg(not(target_os = "macos"))]
    pub const MAXRSS_UNIT: u64 = 1024;

    unsafe extern "C" {
        pub fn clock_gettime(clock_id: i32, tp: *mut Timespec) -> i32;
        pub fn getrusage(who: i32, usage: *mut Rusage) -> i32;
    }

    #[cfg(target_os = "macos")]
    pub const MACH_TASK_BASIC_INFO: u32 = 20; // <mach/task_info.h>
    #[cfg(target_os = "macos")]
    pub const TASK_VM_INFO: u32 = 22;

    #[cfg(target_os = "macos")]
    unsafe extern "C" {
        pub static mach_task_self_: u32;
        pub fn task_info(target_task: u32, flavor: u32, task_info_out: *mut i32, task_info_out_cnt: *mut u32) -> i32;
    }
}

fn clock_ns(id: i32) -> i128 {
    let mut ts = sys::Timespec::default();
    // SAFETY: clock_gettime writes one timespec through a valid pointer.
    let rc = unsafe { sys::clock_gettime(id, &mut ts) };
    assert_eq!(rc, 0, "clock_gettime({id}) failed");
    ts.tv_sec as i128 * 1_000_000_000 + ts.tv_nsec as i128
}

fn rusage() -> sys::Rusage {
    let mut ru = sys::Rusage::default();
    // SAFETY: getrusage writes one struct rusage through a valid pointer; the layout is checked by a test.
    let rc = unsafe { sys::getrusage(sys::RUSAGE_SELF, &mut ru) };
    assert_eq!(rc, 0, "getrusage failed");
    ru
}

fn tv_ns(t: sys::Timeval) -> i128 {
    t.tv_sec as i128 * 1_000_000_000 + t.tv_usec as i128 * 1_000
}

/// The process's clocks at one instant.
#[derive(Clone, Copy)]
pub struct Mark {
    cpu: i128,
    thread: i128,
    ru: i128,
    wall: Instant,
}

pub fn mark() -> Mark {
    let ru = rusage();
    Mark {
        cpu: clock_ns(sys::CLOCK_PROCESS_CPUTIME_ID),
        thread: clock_ns(sys::CLOCK_THREAD_CPUTIME_ID),
        ru: tv_ns(ru.ru_utime) + tv_ns(ru.ru_stime),
        wall: Instant::now(),
    }
}

/// The clocks' advance since `a`, in nanoseconds: process CPU (the measurement), getrusage's user plus system time (the
/// same kernel counters by another call), the thread's CPU and the wall time.
pub fn span(a: Mark) -> J {
    let wall = a.wall.elapsed().as_nanos() as i128;
    let b = mark();
    json!({"cpuNs": (b.cpu - a.cpu) as i64, "ruNs": (b.ru - a.ru) as i64, "threadNs": (b.thread - a.thread) as i64,
           "wallNs": wall as i64})
}

/// Spin for `ms` of wall time: a CPU clock that under-reports (as `time.thread_time()` did on this Mac, s2-design
/// section 4) shows here as a CPU/wall ratio far under 1.
fn calibrate(ms: u64) -> J {
    let a = mark();
    let mut x = 0u64;
    while a.wall.elapsed() < Duration::from_millis(ms) {
        for i in 0..10_000u64 {
            x = black_box(x.wrapping_mul(6364136223846793005).wrapping_add(i));
        }
    }
    black_box(x);
    span(a)
}

/// The process's memory now: resident size (MACH_TASK_BASIC_INFO), footprint and compressed bytes (TASK_VM_INFO) and
/// the peak resident size (getrusage), all in bytes; a field the platform cannot answer is null.
pub fn mem() -> J {
    let maxrss = rusage().ru_maxrss.max(0) as u64 * sys::MAXRSS_UNIT;
    let (resident, footprint, compressed) = task_mem();
    json!({"resident": resident, "footprint": footprint, "compressed": compressed, "maxrss": maxrss})
}

#[cfg(target_os = "macos")]
fn task_info_words(flavor: u32) -> Option<([u32; 128], usize)> {
    let mut buf = [0u32; 128];
    let mut cnt: u32 = buf.len() as u32;
    // SAFETY: task_info fills at most `cnt` 32-bit words of `buf` and writes back how many it filled.
    let kr = unsafe { sys::task_info(sys::mach_task_self_, flavor, buf.as_mut_ptr().cast::<i32>(), &mut cnt) };
    (kr == 0).then_some((buf, cnt as usize))
}

/// A 64-bit field at `byte` of a task_info answer (both structs are `#pragma pack(4)`, read word by word).
#[cfg(target_os = "macos")]
fn word64(ans: &Option<([u32; 128], usize)>, byte: usize) -> Option<u64> {
    let (buf, cnt) = ans.as_ref()?;
    let i = byte / 4;
    (*cnt >= i + 2).then(|| buf[i] as u64 | (buf[i + 1] as u64) << 32)
}

#[cfg(target_os = "macos")]
fn task_mem() -> (Option<u64>, Option<u64>, Option<u64>) {
    // mach_task_basic_info: virtual_size, resident_size (byte 8), ...; task_vm_info: compressed at byte 120,
    // phys_footprint at byte 144 (revision 1 and later)
    let basic = task_info_words(sys::MACH_TASK_BASIC_INFO);
    let vm = task_info_words(sys::TASK_VM_INFO);
    (word64(&basic, 8), word64(&vm, 144), word64(&vm, 120))
}

#[cfg(not(target_os = "macos"))]
fn task_mem() -> (Option<u64>, Option<u64>, Option<u64>) {
    (None, None, None)
}

const USAGE: &str = "usage: romp-spike measure cpu DUMP [--builds K]\n       romp-spike measure mem DIR PAYLOAD [--appends K]";

/// `romp-spike measure ...`: parse, run, print one JSON line. Exit 0, 1 when a check failed, 2 on usage or load error.
pub fn cmd(args: &[String]) -> std::process::ExitCode {
    let mut pos = Vec::new();
    let (mut builds, mut appends) = (10usize, 2usize);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let num = |v: Option<&String>| v.and_then(|s| s.parse::<usize>().ok());
        match a.as_str() {
            "--builds" => match num(it.next()) {
                Some(n) if n > 0 => builds = n,
                _ => return usage(),
            },
            "--appends" => match num(it.next()) {
                Some(n) => appends = n,
                None => return usage(),
            },
            s if s.starts_with("--") => return usage(),
            _ => pos.push(a.as_str()),
        }
    }
    let out = match pos.as_slice() {
        ["cpu", dump] => cpu(Path::new(dump), builds),
        ["mem", dir, payload] => mem_cmd(Path::new(dir), Path::new(payload), appends),
        _ => return usage(),
    };
    match out {
        Ok((ok, doc)) => {
            println!("{doc}");
            if ok { std::process::ExitCode::SUCCESS } else { std::process::ExitCode::from(1) }
        }
        Err(e) => {
            eprintln!("romp-spike measure: {e}");
            std::process::ExitCode::from(2)
        }
    }
}

fn usage() -> std::process::ExitCode {
    eprintln!("{USAGE}");
    std::process::ExitCode::from(2)
}

fn clocks() -> J {
    json!({"cpu": "clock_gettime(CLOCK_PROCESS_CPUTIME_ID)", "ru": "getrusage(RUSAGE_SELF) user+sys",
           "thread": "clock_gettime(CLOCK_THREAD_CPUTIME_ID)", "wall": "std::time::Instant",
           "memory": "task_info MACH_TASK_BASIC_INFO resident_size, TASK_VM_INFO phys_footprint and compressed, \
                      getrusage ru_maxrss"})
}

fn cpu(dir: &Path, builds: usize) -> Result<(bool, J), String> {
    let calibration = calibrate(100);
    let mem0 = mem();
    let m = mark();
    let dump = load::load_dump(dir)?;
    let load_span = span(m);
    let snap = &dump.snapshot;
    let m = mark();
    layer6::validate(snap).map_err(|e| format!("snapshot.json: {e}"))?;
    layer7::validate(snap).map_err(|e| format!("snapshot.json: {e}"))?;
    let validate_span = span(m);
    let mem_loaded = mem();

    let m = mark();
    let feed = layer7::build_feed(snap, &dump.trees, None)
        .map_err(|r| format!("build_feed raised {}: {} (the S2 oracle never raises)", r.exc, r.arg))?;
    let cold = span(m);
    let want_canon = fs::read(dir.join("oracle").join("feed.canon.json")).map_err(|e| format!("oracle: {e}"))?;
    let want_wire = fs::read(dir.join("oracle").join("feed.wire.json")).map_err(|e| format!("oracle: {e}"))?;
    let (canon, wire) = (feed.canon(), feed.wire());
    let equal = canon.as_bytes() == want_canon.as_slice() && wire.as_bytes() == want_wire.as_slice();
    let shape = json!({"sessions": feed.sessions, "cards": feed.cards, "rows": feed.rows, "absent": feed.absent,
                       "canonBytes": canon.len(), "wireBytes": wire.len(), "equalsOracle": equal});
    drop(feed);
    if !equal {
        return Err(format!("the cold build's bytes differ from {}'s oracle: nothing is timed", dir.display()));
    }

    let mut warm = Vec::with_capacity(builds);
    for _ in 0..builds {
        let m = mark();
        let f = layer7::build_feed(snap, &dump.trees, None).map_err(|r| format!("build_feed raised {}", r.exc))?;
        warm.push(span(m));
        black_box(&f);
    }
    let mut entries = Vec::with_capacity(builds);
    for _ in 0..builds {
        let m = mark();
        let b = layer6::Build::new(snap, &dump.trees, None);
        let e = b.entries();
        entries.push(span(m));
        black_box(&e);
    }
    let mem_end = mem();
    let doc = json!({
        "side": "rust", "mode": "cpu", "schema": "romp-spike-measure-rust/1",
        "world": world_of(snap), "builds": builds, "clocks": clocks(), "calibration": calibration,
        "load": load_span, "validate": validate_span, "cold": cold, "warm": warm, "entries": entries, "feed": shape,
        "memory": {"start": mem0, "loaded": mem_loaded, "end": mem_end},
    });
    Ok((true, doc))
}

fn world_of(snap: &romp_views::snapshot::Snapshot) -> J {
    json!({"alive": snap.alive().len()})
}

fn sorted_files(dir: &Path) -> Result<Vec<PathBuf>, String> {
    let mut names: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|e| format!("cannot list {}: {e}", dir.display()))?
        .filter_map(|d| d.ok())
        .map(|d| d.path())
        .filter(|p| p.is_file())
        .collect();
    names.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
    if names.is_empty() {
        return Err(format!("no files in {}", dir.display()));
    }
    Ok(names)
}

/// What the store holds, counted: files, records, the bytes held, the index's bytes and the allocated capacity.
fn counted(store: &Store) -> J {
    let (mut files, mut records, mut held, mut index, mut alloc) = (0usize, 0usize, 0usize, 0usize, 0usize);
    for (_, e) in store.entries() {
        files += 1;
        records += e.records();
        held += e.bytes_held();
        index += e.index_bytes();
        alloc += e.allocated_bytes();
    }
    json!({"files": files, "records": records, "heldBytes": held, "indexBytes": index, "allocatedBytes": alloc})
}

fn read_all(store: &mut Store, names: &[PathBuf], want: ReadKind) -> (J, Vec<String>) {
    let m = mark();
    let kinds: Vec<ReadKind> = names.iter().map(|p| store.read(p)).collect();
    let s = span(m);
    let wrong = names
        .iter()
        .zip(&kinds)
        .filter(|(_, k)| **k != want)
        .map(|(p, k)| format!("{}: {}", p.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(), k.name()))
        .collect();
    (s, wrong)
}

fn mem_cmd(dir: &Path, payload: &Path, appends: usize) -> Result<(bool, J), String> {
    let names = sorted_files(dir)?;
    let file_bytes: u64 = names.iter().map(|p| fs::metadata(p).map(|m| m.len()).unwrap_or(0)).sum();
    let before = mem();
    let mut store = Store::new();
    let (read_span, wrong) = read_all(&mut store, &names, ReadKind::Zero);
    let mut problems: Vec<String> = wrong.into_iter().map(|w| format!("cold read not from zero: {w}")).collect();
    let cold = json!({"memory": mem(), "counted": counted(&store), "read": read_span});
    let mut rounds = Vec::new();
    for r in 0..appends {
        for p in &names {
            let name = p.file_name().ok_or("a file without a name")?;
            let bytes = fs::read(payload.join(name)).map_err(|e| format!("payload for {}: {e}", p.display()))?;
            let mut f = OpenOptions::new().append(true).open(p).map_err(|e| format!("{}: {e}", p.display()))?;
            f.write_all(&bytes).map_err(|e| format!("{}: {e}", p.display()))?;
        }
        let (s, wrong) = read_all(&mut store, &names, ReadKind::Grown);
        problems.extend(wrong.into_iter().map(|w| format!("round {}: not an append read: {w}", r + 1)));
        rounds.push(json!({"round": r + 1, "memory": mem(), "counted": counted(&store), "read": s}));
    }
    let doc = json!({
        "side": "rust", "mode": "mem", "schema": "romp-spike-measure-rust/1", "files": names.len(),
        "fileBytes": file_bytes, "appends": appends, "clocks": clocks(), "before": {"memory": before}, "cold": cold,
        "rounds": rounds, "problems": problems,
    });
    Ok((problems.is_empty(), doc))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_structs_have_the_c_layout() {
        assert_eq!(std::mem::size_of::<sys::Timespec>(), 16);
        assert_eq!(std::mem::size_of::<sys::Timeval>(), 16);
        assert_eq!(std::mem::size_of::<sys::Rusage>(), 144);
    }

    #[test]
    fn the_cpu_clock_follows_a_spin_and_the_memory_reads_answer() {
        let s = calibrate(30);
        let (cpu, ru, wall) = (s["cpuNs"].as_i64().unwrap(), s["ruNs"].as_i64().unwrap(), s["wallNs"].as_i64().unwrap());
        assert!(wall >= 30_000_000, "spun {wall} ns");
        // a loaded machine can take the CPU away; a clock that under-reports 42x (thread_time's on this Mac) cannot pass
        assert!(cpu * 4 >= wall, "process CPU {cpu} ns over {wall} ns of wall time");
        assert!((cpu - ru).abs() < 2_000_000, "clock_gettime {cpu} ns against getrusage {ru} ns");
        let m = mem();
        assert!(m["maxrss"].as_u64().unwrap() > 0);
        #[cfg(target_os = "macos")]
        {
            let r = m["resident"].as_u64().expect("task_info answers on macOS");
            assert!(r > 100_000 && r < 1 << 40, "resident {r}");
            assert!(m["footprint"].as_u64().is_some());
        }
    }
}
