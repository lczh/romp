//! S6's measurement, the Rust side (rust/SPIKE.md, "S6 method"). Two modes, each printing one JSON line on stdout:
//!
//!     romp-spike measure cpu DUMP [--builds K]
//!     romp-spike measure mem DIR PAYLOAD [--appends K] [--growth blocks|double] [--relief]
//!
//! `cpu` loads the dump (timed), builds the feed once (the cold build, the first in the process) and checks its
//! canonical and wire bytes against the dump's oracle before anything is reported: a build that differs is never timed.
//! The cheap bound's build (below) is checked against the oracle the same way. Then K rounds, each timing one build of
//! every row in turn (round-robin, so drift spreads over the rows; odd rounds in reverse order, so no row always
//! follows the same one):
//!   warm      the whole build_feed (the port keeps nothing between builds, so each derives every session);
//!   region    layer7::fold_on alone, layer 6's entries plus the per-session fold, with its `Build` made outside the
//!             clock: the region Python's entry plus fold computes (its noHold row is the same work);
//!   bound     the same region with each session's closed turns folded ahead, outside the clock (layer3::closed_part),
//!             so only the open turn is walked: the cheap bound on a held-aggregate port, which flatters the port (it
//!             pays none of the hold keys, the seams' fingerprint, the prefix compare, the map copy or the store);
//!   held      the same region with the held aggregate ported (layer3::SegHeld, kept across the process's held builds
//!             and filled by one build before the rounds): Python's heldWarm, the deciding comparison;
//!   heldCold  the same region over a fresh table each build (every turn walked, the hold keys built, every
//!             aggregate stored): Python's emptyMemo;
//!   entries   `Build::new` plus `entries` (S6's first harness: the derivation without the fold);
//!   buildNew  `Build::new` alone (the maps a build reads across sessions; outside `region`, reported apart).
//! What a timed call returns is dropped after the clock stops, as Python's harness keeps its result past its clock.
//! Every held and heldCold build keeps the held walk's counters it moved (segWalks, segHeld, segWalkTurns, ...), which
//! the driver checks against Python's heldWarm and emptyMemo builds: equal counters show the held path really ran.
//!
//! `mem` reads every file of DIR through the transcript store (romp-transcript, S3) and records the process's memory
//! before and after, then K rounds: append PAYLOAD/<round>/<file name> to each file that has one (rounds counted from
//! 1), read every file again, record. The driver (tools/rust-spike/measure_drive.py) makes DIR a fresh copy of the
//! transcripts with mtimes near now and writes the schedule, so this side and Python's append the same bytes. A file
//! appended must read as `Grown`, any other as `Hit`. `--growth` picks the store's growth policy (romp-transcript's
//! `Growth`, default blocks); `--relief` adds, at every reading, a second one after malloc_zone_pressure_relief(NULL, 0),
//! which hands back what the allocator keeps free, so the two readings part the allocator's cache from live bytes.
//!
//! The clocks are raw externs into libSystem (no crate): process CPU from clock_gettime(CLOCK_PROCESS_CPUTIME_ID),
//! cross-checked with getrusage(RUSAGE_SELF) and the thread's CPU clock, each beside the wall time (`Instant`); the
//! resident size from task_info(MACH_TASK_BASIC_INFO), the footprint and compressed bytes from task_info(TASK_VM_INFO),
//! the default malloc zone's bytes in use and total from mstats, the peak from getrusage's ru_maxrss. A spin of 100 ms of wall time, at the start and at the end, calibrates the CPU
//! clock against the wall clock and counts its loop iterations per CPU microsecond: a run whose rate moves by more than
//! about 10% from the side's median met a slower core or clock (P-cores and E-cores), which the driver flags.
//! On Linux the CPU clocks work and the memory fields are null.

use std::cell::RefCell;
use std::fs::{self, OpenOptions};
use std::hint::black_box;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{Value as J, json};

use romp_transcript::{Growth, ReadKind, Store};
use romp_views::layer3::SegHeld;
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

    /// <malloc/malloc.h> `struct mstats`: the default zone's bytes in use and its total.
    #[cfg(target_os = "macos")]
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    pub struct Mstats {
        pub bytes_total: usize,
        pub chunks_used: usize,
        pub bytes_used: usize,
        pub chunks_free: usize,
        pub bytes_free: usize,
    }

    #[cfg(target_os = "macos")]
    pub const MACH_TASK_BASIC_INFO: u32 = 20; // <mach/task_info.h>
    #[cfg(target_os = "macos")]
    pub const TASK_VM_INFO: u32 = 22;

    #[cfg(target_os = "macos")]
    unsafe extern "C" {
        pub static mach_task_self_: u32;
        pub fn task_info(target_task: u32, flavor: u32, task_info_out: *mut i32, task_info_out_cnt: *mut u32) -> i32;
        /// <malloc/malloc.h>: return free memory to the system, from every zone when `zone` is null (goal 0: all).
        pub fn malloc_zone_pressure_relief(zone: *mut core::ffi::c_void, goal: usize) -> usize;
        pub fn mstats() -> Mstats;
        pub fn sysctlbyname(
            name: *const core::ffi::c_char,
            oldp: *mut core::ffi::c_void,
            oldlenp: *mut usize,
            newp: *mut core::ffi::c_void,
            newlen: usize,
        ) -> i32;
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
    let mut iters = 0u64;
    while a.wall.elapsed() < Duration::from_millis(ms) {
        for i in 0..10_000u64 {
            x = black_box(x.wrapping_mul(6364136223846793005).wrapping_add(i));
        }
        iters += 10_000;
    }
    black_box(x);
    let mut s = span(a);
    let cpu = s["cpuNs"].as_i64().unwrap_or(0);
    s["iterations"] = json!(iters);
    s["loopsPerCpuUs"] = json!(if cpu > 0 { iters as f64 * 1000.0 / cpu as f64 } else { 0.0 });
    s
}

/// malloc_zone_pressure_relief(NULL, 0) and the memory after it: what the allocator kept free, handed back.
#[cfg(target_os = "macos")]
fn relieved() -> J {
    // SAFETY: a null zone asks every zone; goal 0 asks for everything that can be released.
    let released = unsafe { sys::malloc_zone_pressure_relief(std::ptr::null_mut(), 0) };
    json!({"released": released, "memory": mem()})
}

#[cfg(not(target_os = "macos"))]
fn relieved() -> J {
    json!({"released": null, "memory": mem()})
}

/// The process's memory now: resident size (MACH_TASK_BASIC_INFO), footprint and compressed bytes (TASK_VM_INFO) and
/// the peak resident size (getrusage), all in bytes; a field the platform cannot answer is null.
pub fn mem() -> J {
    let maxrss = rusage().ru_maxrss.max(0) as u64 * sys::MAXRSS_UNIT;
    let (resident, footprint, compressed) = task_mem();
    let (in_use, total) = malloc_stats();
    json!({"resident": resident, "footprint": footprint, "compressed": compressed, "maxrss": maxrss,
           "mallocInUse": in_use, "mallocTotal": total})
}

/// The default malloc zone's bytes in use and its total (mstats): live allocations against what the zone holds.
#[cfg(target_os = "macos")]
fn malloc_stats() -> (Option<u64>, Option<u64>) {
    // SAFETY: mstats takes no argument and returns a plain struct.
    let m = unsafe { sys::mstats() };
    (Some(m.bytes_used as u64), Some(m.bytes_total as u64))
}

#[cfg(not(target_os = "macos"))]
fn malloc_stats() -> (Option<u64>, Option<u64>) {
    (None, None)
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

const USAGE: &str = "usage: romp-spike measure cpu DUMP [--builds K]\n       romp-spike measure mem DIR PAYLOAD [--appends K] \
                     [--growth blocks|double] [--relief]";

/// `romp-spike measure ...`: parse, run, print one JSON line. Exit 0, 1 when a check failed, 2 on usage or load error.
pub fn cmd(args: &[String]) -> std::process::ExitCode {
    let mut pos = Vec::new();
    let (mut builds, mut appends) = (11usize, 6usize);
    let (mut growth, mut relief) = (Growth::default(), false);
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
            "--growth" => match it.next().and_then(|g| Growth::parse(g)) {
                Some(g) => growth = g,
                None => return usage(),
            },
            "--relief" => relief = true,
            s if s.starts_with("--") => return usage(),
            _ => pos.push(a.as_str()),
        }
    }
    let out = match pos.as_slice() {
        ["cpu", dump] => cpu(Path::new(dump), builds),
        ["mem", dir, payload] => mem_cmd(Path::new(dir), Path::new(payload), appends, growth, relief),
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

fn oracle_check(dir: &Path, feed: &layer7::Feed, what: &str) -> Result<J, String> {
    let want_canon = fs::read(dir.join("oracle").join("feed.canon.json")).map_err(|e| format!("oracle: {e}"))?;
    let want_wire = fs::read(dir.join("oracle").join("feed.wire.json")).map_err(|e| format!("oracle: {e}"))?;
    let (canon, wire) = (feed.canon(), feed.wire());
    let equal = canon.as_bytes() == want_canon.as_slice() && wire.as_bytes() == want_wire.as_slice();
    if !equal {
        return Err(format!("the {what} build's bytes differ from {}'s oracle: nothing is timed", dir.display()));
    }
    Ok(json!({"sessions": feed.sessions, "cards": feed.cards, "rows": feed.rows, "absent": feed.absent,
              "canonBytes": canon.len(), "wireBytes": wire.len(), "equalsOracle": equal}))
}

/// The rows of a round, in the order each round times them.
const ROWS: [&str; 7] = ["warm", "region", "bound", "held", "heldCold", "entries", "buildNew"];

/// kernel.FEED_SEG_HELD_BYTES: ROMP_FEED_SEG_HELD_BYTES when it names a positive integer, else a hundred-twenty-eighth
/// of the machine's memory (hw.memsize on macOS; 8 GB when unknown, as kernel._mem_total_bytes falls back).
fn seg_bound() -> u64 {
    if let Some(n) = std::env::var("ROMP_FEED_SEG_HELD_BYTES").ok().and_then(|v| v.parse::<u64>().ok()).filter(|n| *n > 0) {
        return n;
    }
    memsize().filter(|m| *m > 0).unwrap_or(8 << 30) / 128
}

#[cfg(target_os = "macos")]
fn memsize() -> Option<u64> {
    let mut v: u64 = 0;
    let mut len = std::mem::size_of::<u64>();
    // SAFETY: hw.memsize is a 64-bit integer written through a valid pointer of the length given.
    let rc = unsafe {
        sys::sysctlbyname(c"hw.memsize".as_ptr(), (&mut v as *mut u64).cast(), &mut len, std::ptr::null_mut(), 0)
    };
    (rc == 0).then_some(v)
}

#[cfg(not(target_os = "macos"))]
fn memsize() -> Option<u64> {
    None
}

fn seg_delta(a: &romp_views::layer3::SegStats, b: &romp_views::layer3::SegStats) -> J {
    let mut m = serde_json::Map::new();
    for ((k, x), (_, y)) in a.named().iter().zip(b.named().iter()) {
        m.insert((*k).to_string(), json!(y - x));
    }
    J::Object(m)
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
    let shape = oracle_check(dir, &feed, "cold")?;
    drop(feed);

    // the cheap bound's closed parts, folded once outside every clock; each bound build gets its own copy
    let m = mark();
    let closed = layer6::Build::new(snap, &dump.trees, None).closed_parts();
    let closed_span = span(m);
    let closed_turns: usize = closed.values().map(|c| c.maps.segs.len()).sum();
    let bound_shape = {
        let cell = RefCell::new(closed.clone());
        let b = layer6::Build::new(snap, &dump.trees, None).with_closed(&cell);
        let f = layer7::build_feed_on(&b, None).map_err(|r| format!("the bound's build raised {}", r.exc))?;
        let left = cell.borrow().len();
        if left != 0 {
            return Err(format!("the bound's build left {left} closed parts untaken"));
        }
        oracle_check(dir, &f, "bound")?
    };

    // the held aggregate: one table for the process's held builds, filled by a first build (as Python's heldWarm follows
    // the emptyMemo build that stored the aggregates), then a served build; both must give the oracle's bytes
    let bound = seg_bound();
    let held = RefCell::new(SegHeld::new(bound));
    let mut held_checks = Vec::new();
    for what in ["held (filling)", "held (served)"] {
        let s0 = held.borrow().stats;
        let b = layer6::Build::new(snap, &dump.trees, None).with_held(&held);
        let f = layer7::build_feed_on(&b, None).map_err(|r| format!("the held build raised {}", r.exc))?;
        let mut c = oracle_check(dir, &f, what)?;
        c["seg"] = seg_delta(&s0, &held.borrow().stats);
        held_checks.push(c);
    }

    let mut rows: Vec<Vec<J>> = vec![Vec::with_capacity(builds); ROWS.len()];
    for round in 0..builds {
        // odd rounds run the rows in reverse, so no row always follows the same one (nothing a row needs depends on
        // another row: the held table is the held row's alone)
        let order: Vec<usize> = if round % 2 == 1 { (0..ROWS.len()).rev().collect() } else { (0..ROWS.len()).collect() };
        for ri in order {
            let row = &ROWS[ri];
            let s = match *row {
                "warm" => {
                    let m = mark();
                    let f = layer7::build_feed(snap, &dump.trees, None).map_err(|r| format!("build_feed raised {}", r.exc))?;
                    let s = span(m);
                    black_box(&f);
                    s
                }
                "region" => {
                    let b = layer6::Build::new(snap, &dump.trees, None);
                    let m = mark();
                    let f = layer7::fold_on(&b, None).map_err(|r| format!("fold_on raised {}", r.exc))?;
                    let s = span(m);
                    black_box(f.cards());
                    drop(f);
                    s
                }
                "bound" => {
                    let cell = RefCell::new(closed.clone());
                    let b = layer6::Build::new(snap, &dump.trees, None).with_closed(&cell);
                    let m = mark();
                    let f = layer7::fold_on(&b, None).map_err(|r| format!("fold_on raised {}", r.exc))?;
                    let s = span(m);
                    black_box(f.cards());
                    drop(f);
                    s
                }
                "held" => {
                    let s0 = held.borrow().stats;
                    let b = layer6::Build::new(snap, &dump.trees, None).with_held(&held);
                    let m = mark();
                    let f = layer7::fold_on(&b, None).map_err(|r| format!("fold_on raised {}", r.exc))?;
                    let mut s = span(m);
                    black_box(f.cards());
                    drop(f);
                    s["seg"] = seg_delta(&s0, &held.borrow().stats);
                    s
                }
                "heldCold" => {
                    let fresh = RefCell::new(SegHeld::new(bound));
                    let b = layer6::Build::new(snap, &dump.trees, None).with_held(&fresh);
                    let m = mark();
                    let f = layer7::fold_on(&b, None).map_err(|r| format!("fold_on raised {}", r.exc))?;
                    let mut s = span(m);
                    black_box(f.cards());
                    drop(f);
                    drop(b);
                    s["seg"] = seg_delta(&Default::default(), &fresh.borrow().stats);
                    s
                }
                "entries" => {
                    let m = mark();
                    let b = layer6::Build::new(snap, &dump.trees, None);
                    let e = b.entries();
                    let s = span(m);
                    black_box(&e);
                    s
                }
                _ => {
                    let m = mark();
                    let b = layer6::Build::new(snap, &dump.trees, None);
                    let s = span(m);
                    black_box(&b);
                    s
                }
            };
            rows[ri].push(s);
        }
    }
    let mem_end = mem();
    let calibration_end = calibrate(100);
    let mut row_doc = serde_json::Map::new();
    for (ri, row) in ROWS.iter().enumerate() {
        row_doc.insert((*row).to_string(), J::Array(std::mem::take(&mut rows[ri])));
    }
    let doc = json!({
        "side": "rust", "mode": "cpu", "schema": "romp-spike-measure-rust/2",
        "world": world_of(snap), "builds": builds, "clocks": clocks(), "calibration": calibration,
        "calibrationEnd": calibration_end, "load": load_span, "validate": validate_span, "cold": cold,
        "rowOrder": ROWS, "rows": row_doc,
        "bound": {"closedParts": closed.len(), "closedSegments": closed_turns, "foldAhead": closed_span,
                  "feed": bound_shape},
        "held": {"segBound": bound, "checks": held_checks, "entries": held.borrow().len(),
                 "segBytes": held.borrow().bytes()},
        "feed": shape, "memory": {"start": mem0, "loaded": mem_loaded, "end": mem_end},
        "build": build_profile(),
    });
    Ok((true, doc))
}

/// How this binary was built: the choices that hold the port back are named in the S6 method's unfairness list.
fn build_profile() -> J {
    json!({"debugAssertions": cfg!(debug_assertions), "profile": if cfg!(debug_assertions) { "debug" } else { "release" },
           "allocator": "system", "lto": "false (thin-local only)", "codegenUnits": 16, "targetCpu": "default"})
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

/// What the store holds, counted: files, records, the bytes held, the index's bytes, the blocks and the allocated
/// capacity.
fn counted(store: &Store) -> J {
    let (mut files, mut records, mut held, mut index, mut alloc, mut blocks) = (0usize, 0usize, 0usize, 0usize, 0usize, 0usize);
    for (_, e) in store.entries() {
        files += 1;
        records += e.records();
        held += e.bytes_held();
        index += e.index_bytes();
        alloc += e.allocated_bytes();
        blocks += e.blocks();
    }
    json!({"files": files, "records": records, "heldBytes": held, "indexBytes": index, "allocatedBytes": alloc,
           "blocks": blocks})
}

/// Read every file, each expected to read as `want` (`Grown` for the files in `grown`, when given, `Hit` for the rest).
fn read_all(store: &mut Store, names: &[PathBuf], want: ReadKind, grown: Option<&[bool]>) -> (J, Vec<String>) {
    let m = mark();
    let kinds: Vec<ReadKind> = names.iter().map(|p| store.read(p)).collect();
    let s = span(m);
    let wrong = names
        .iter()
        .zip(&kinds)
        .enumerate()
        .filter(|(i, (_, k))| {
            let w = match grown {
                Some(g) if !g[*i] => ReadKind::Hit,
                _ => want,
            };
            **k != w
        })
        .map(|(_, (p, k))| format!("{}: {}", p.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(), k.name()))
        .collect();
    (s, wrong)
}

fn reading(relief: bool, extra: J) -> J {
    let mut r = extra;
    r["memory"] = mem();
    if relief {
        r["relieved"] = relieved();
    }
    r
}

fn mem_cmd(dir: &Path, payload: &Path, appends: usize, growth: Growth, relief: bool) -> Result<(bool, J), String> {
    let names = sorted_files(dir)?;
    let file_bytes: u64 = names.iter().map(|p| fs::metadata(p).map(|m| m.len()).unwrap_or(0)).sum();
    let before = reading(relief, json!({}));
    let mut store = Store::with_growth(growth);
    let (read_span, wrong) = read_all(&mut store, &names, ReadKind::Zero, None);
    let mut problems: Vec<String> = wrong.into_iter().map(|w| format!("cold read not from zero: {w}")).collect();
    let cold = reading(relief, json!({"counted": counted(&store), "read": read_span}));
    let mut rounds = Vec::new();
    let mut appended_total = 0u64;
    for r in 1..=appends {
        let round_dir = payload.join(r.to_string());
        if !round_dir.is_dir() {
            return Err(format!("no payload for round {r} under {}", payload.display()));
        }
        let mut grown = vec![false; names.len()];
        let (mut added, mut files_appended) = (0u64, 0usize);
        for (i, p) in names.iter().enumerate() {
            let name = p.file_name().ok_or("a file without a name")?;
            let src = round_dir.join(name);
            if !src.is_file() {
                continue;
            }
            let bytes = fs::read(&src).map_err(|e| format!("payload for {}: {e}", p.display()))?;
            let mut f = OpenOptions::new().append(true).open(p).map_err(|e| format!("{}: {e}", p.display()))?;
            f.write_all(&bytes).map_err(|e| format!("{}: {e}", p.display()))?;
            grown[i] = !bytes.is_empty();
            added += bytes.len() as u64;
            files_appended += 1;
        }
        if added == 0 {
            problems.push(format!("round {r}: the append added nothing"));
        }
        appended_total += added;
        let (s, wrong) = read_all(&mut store, &names, ReadKind::Grown, Some(&grown));
        problems.extend(wrong.into_iter().map(|w| format!("round {r}: not the expected read: {w}")));
        rounds.push(reading(relief, json!({"round": r, "counted": counted(&store), "read": s, "appendedBytes": added,
                                            "filesAppended": files_appended})));
    }
    let doc = json!({
        "side": "rust", "mode": "mem", "schema": "romp-spike-measure-rust/2", "files": names.len(),
        "fileBytes": file_bytes, "appendedBytes": appended_total, "appends": appends, "growth": growth.name(),
        "relief": relief, "clocks": clocks(), "before": before, "cold": cold, "rounds": rounds, "problems": problems,
        "build": build_profile(),
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
        assert!(s["iterations"].as_u64().unwrap() > 0 && s["loopsPerCpuUs"].as_f64().unwrap() > 0.0);
        let r = relieved();
        assert!(r["memory"]["maxrss"].as_u64().unwrap() > 0);
        assert!(seg_bound() > 0);
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
