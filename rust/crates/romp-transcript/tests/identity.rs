//! The identity contract on real files: append, a torn last line, touch, same-size rewrites, shrink, relocation,
//! deletion. Each case states Python's answer (`_read_jsonl_entry_unlocked`); tools/rust-spike/check_store.py runs the
//! same cases in lockstep against Python itself.
use romp_transcript::{Field, FieldRef, Growth, ReadKind, Store, crc32, py_mtime};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, UNIX_EPOCH};

static N: AtomicU32 = AtomicU32::new(0);
const T0: u64 = 1_781_100_000;

struct Tmp(PathBuf);

impl Tmp {
    fn new() -> Tmp {
        let n = N.fetch_add(1, Ordering::SeqCst);
        let p = std::env::temp_dir().join(format!("romp-transcript-test-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        Tmp(p)
    }
    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn set_mtime(p: &Path, sec: u64, nsec: u32) {
    let f = File::options().write(true).open(p).unwrap();
    f.set_modified(UNIX_EPOCH + Duration::new(sec, nsec)).unwrap();
}

fn write(p: &Path, b: &[u8], sec: u64) {
    fs::write(p, b).unwrap();
    set_mtime(p, sec, 0);
}

fn append(p: &Path, b: &[u8], sec: u64) {
    OpenOptions::new().append(true).open(p).unwrap().write_all(b).unwrap();
    set_mtime(p, sec, 0);
}

/// A transcript-shaped record of a fixed length for every i < 1000.
fn rec(i: usize) -> String {
    format!(
        "{{\"type\": \"user\", \"uuid\": \"u-{i:03}\", \"parentUuid\": null, \"message\": {{\"role\": \"user\", \"content\": [{{\"type\": \"tool_result\", \"tool_use_id\": \"toolu_{i:03}\"}}]}}}}\n"
    )
}

fn recs(r: std::ops::Range<usize>) -> String {
    r.map(rec).collect()
}

fn uuids(s: &Store, p: &Path) -> Vec<String> {
    s.get(p)
        .unwrap()
        .lines()
        .map(|l| match l.field(Field::Uuid) {
            FieldRef::Str(u) => u.to_string_lossy(),
            other => format!("{other:?}"),
        })
        .collect()
}

#[test]
fn a_first_read_indexes_and_an_unchanged_file_is_a_hit() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, recs(0..3).as_bytes(), T0);
    let mut s = Store::new();
    assert_eq!(s.read(&p), ReadKind::Zero);
    let e = s.get(&p).unwrap();
    assert_eq!((e.records(), e.offset(), e.size()), (3, 3 * rec(0).len() as u64, 3 * rec(0).len() as u64));
    assert_eq!(e.crc32(), crc32::update(0, &fs::read(&p).unwrap()));
    let l = e.line(1);
    assert_eq!((l.offset(), l.len() as usize), (rec(0).len() as u64, rec(0).len()));
    assert_eq!(l.field(Field::ParentUuid), FieldRef::Null);
    assert_eq!(l.tool_ids().map(|i| i.to_string_lossy()).collect::<Vec<_>>(), vec!["toolu_001"]);
    let before = s.stats.bytes_read;
    assert_eq!(s.read(&p), ReadKind::Hit);
    assert_eq!(s.stats.bytes_read, before, "a hit reads nothing");
}

#[test]
fn an_append_reads_only_the_guard_and_the_new_bytes() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, recs(0..3).as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let generation = s.get(&p).unwrap().generation();
    let before = s.stats.bytes_read;
    let more = recs(3..5);
    append(&p, more.as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Grown);
    let e = s.get(&p).unwrap();
    assert_eq!(e.generation(), generation, "an append keeps the generation");
    assert_eq!(uuids(&s, &p), vec!["u-000", "u-001", "u-002", "u-003", "u-004"]);
    assert_eq!(s.stats.bytes_read - before, 64 + more.len() as u64, "the 64-byte guard and the appended bytes only");
    assert_eq!(e.crc32(), crc32::update(0, &fs::read(&p).unwrap()), "the CRC runs on over the appended lines");
}

#[test]
fn a_torn_last_line_waits_for_its_newline() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, recs(0..2).as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let (off, crc) = (s.get(&p).unwrap().offset(), s.get(&p).unwrap().crc32());
    let whole = rec(2);
    let (head, rest) = whole.split_at(40);
    append(&p, head.as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Grown);
    let e = s.get(&p).unwrap();
    assert_eq!((e.records(), e.offset(), e.crc32()), (2, off, crc), "the partial is neither indexed, held nor in the CRC");
    assert_eq!(e.size(), off + 40);
    assert_eq!(s.read(&p), ReadKind::Hit);
    append(&p, rest.as_bytes(), T0 + 2);
    assert_eq!(s.read(&p), ReadKind::Grown);
    let e = s.get(&p).unwrap();
    assert_eq!(e.records(), 3);
    assert_eq!(e.line(2).offset(), off, "the completed line starts where the held bytes ended");
    assert_eq!(e.line(2).raw(), whole.as_bytes());
}

#[test]
fn a_touch_is_proved_by_the_held_crc_and_keeps_the_generation() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, format!("{}{{\"partial\": ", recs(0..3)).as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let (generation, off) = (s.get(&p).unwrap().generation(), s.get(&p).unwrap().offset());
    let before = s.stats.bytes_read;
    set_mtime(&p, T0 + 5, 0);
    assert_eq!(s.read(&p), ReadKind::Touched);
    let e = s.get(&p).unwrap();
    assert_eq!((e.generation(), e.records()), (generation, 3));
    let partial = e.size() - off;
    assert_eq!(s.stats.bytes_read - before, 64 + off + partial, "guard, every held byte, then the partial again");
}

#[test]
fn a_same_size_rewrite_under_a_new_mtime_is_reindexed() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4);
    write(&p, body.as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let generation = s.get(&p).unwrap().generation();
    // A byte early in the file: outside the 64-byte guard, so only the held CRC can see it.
    write(&p, body.replacen("u-001", "u-00X", 1).as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Rewrite);
    assert_ne!(s.get(&p).unwrap().generation(), generation);
    assert_eq!(uuids(&s, &p)[1], "u-00X");
    // A byte inside the guard window: the last line's final bytes.
    let now = fs::read_to_string(&p).unwrap();
    let cut = now.len() - 5;
    write(&p, format!("{}X{}", &now[..cut], &now[cut + 1..]).as_bytes(), T0 + 2);
    assert_eq!(s.read(&p), ReadKind::Guard);
}

#[test]
fn a_same_size_rewrite_that_keeps_its_mtime_is_served_stale_as_python_serves_it() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4);
    write(&p, body.as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    write(&p, body.replacen("u-001", "u-00X", 1).as_bytes(), T0);
    assert_eq!(s.read(&p), ReadKind::Hit);
    assert_eq!(uuids(&s, &p)[1], "u-001", "mtime and size are the whole hit test");
}

#[test]
fn mtimes_that_round_to_one_float_are_one_mtime() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..2);
    write(&p, body.as_bytes(), T0);
    set_mtime(&p, T0, 0);
    let a = py_mtime(&fs::metadata(&p).unwrap());
    let mut s = Store::new();
    s.read(&p);
    fs::write(&p, body.replacen("u-001", "u-00X", 1)).unwrap();
    set_mtime(&p, T0, 50); // 50 ns later: under half a float step (2^-22 s near 1.78e9), the same double
    assert_eq!(py_mtime(&fs::metadata(&p).unwrap()), a, "the premise: the float did not move");
    assert_eq!(s.read(&p), ReadKind::Hit);
    set_mtime(&p, T0, 1000);
    assert_ne!(py_mtime(&fs::metadata(&p).unwrap()), a);
    assert_eq!(s.read(&p), ReadKind::Rewrite);
}

#[test]
fn a_shrink_is_reindexed_and_dropping_only_the_partial_is_a_rewrite() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4);
    write(&p, format!("{body}{{\"torn\": 1").as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    write(&p, body.as_bytes(), T0 + 1); // smaller than the size, not than the held offset
    assert_eq!(s.read(&p), ReadKind::Rewrite);
    assert_eq!(s.get(&p).unwrap().records(), 4);
    write(&p, recs(0..2).as_bytes(), T0 + 2);
    assert_eq!(s.read(&p), ReadKind::Shrunk);
    assert_eq!(uuids(&s, &p), vec!["u-000", "u-001"]);
}

#[test]
fn a_partial_cut_short_is_a_rewrite_not_a_growth() {
    // Growth is judged against the size the entry saw, not its held offset: a 10-byte partial cut to 5 bytes leaves
    // the file past the offset but smaller than before, and Python reads it from zero.
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..3);
    write(&p, format!("{body}{{\"torn\": 1").as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let (generation, off) = (s.get(&p).unwrap().generation(), s.get(&p).unwrap().offset());
    assert_eq!(s.get(&p).unwrap().size(), off + 10);
    write(&p, format!("{body}{{\"tor").as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Rewrite);
    let e = s.get(&p).unwrap();
    assert_ne!(e.generation(), generation);
    assert_eq!((e.records(), e.offset(), e.size()), (3, off, off + 5));
}

#[test]
fn the_tail_guard_is_exactly_the_64_bytes_before_the_held_offset() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4).into_bytes();
    let n = body.len();
    let flip = |b: &[u8], at: usize| {
        assert!(b[at].is_ascii_alphanumeric(), "the premise: a byte inside a string or key");
        let mut b = b.to_vec();
        b[at] = if b[at] == b'X' { b'Y' } else { b'X' };
        b
    };
    write(&p, &body, T0);
    let mut s = Store::new();
    s.read(&p);
    let at64 = flip(&body, n - 64); // the first byte the guard holds
    write(&p, &at64, T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Guard);
    write(&p, &flip(&at64, n - 65), T0 + 2); // the last byte before it: only the held CRC sees it
    assert_eq!(s.read(&p), ReadKind::Rewrite);
}

#[test]
fn a_relocated_file_is_judged_by_size_mtime_and_inode() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4);
    write(&p, body.as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let ino = s.get(&p).unwrap().inode();
    let swap = |content: &str, sec: u64| {
        let q = t.path("a.jsonl.new");
        write(&q, content.as_bytes(), sec);
        fs::rename(&q, &p).unwrap();
    };
    // The same size under a new inode and a new mtime: a replacement.
    swap(&body.replacen("u-001", "u-00X", 1), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Rewrite);
    assert_ne!(s.get(&p).unwrap().inode(), ino);
    // The same size and the same mtime under a new inode: a hit, stale, as in Python (no inode in the hit test).
    swap(&body, T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Hit);
    assert_eq!(uuids(&s, &p)[1], "u-00X");
    // Longer, new inode, the held tail guard intact: read as an append (Python ignores the inode on growth).
    let generation = s.get(&p).unwrap().generation();
    swap(&format!("{}{}", body.replacen("u-001", "u-00X", 1), rec(4)), T0 + 2);
    assert_eq!(s.read(&p), ReadKind::Grown);
    assert_eq!(s.get(&p).unwrap().generation(), generation);
    // Longer, new inode, a byte inside the guard window (the held lines end with rec(4), whose last 64 bytes carry
    // its tool id) changed: from zero.
    swap(&format!("{}{}", format!("{}{}", body, rec(4)).replacen("toolu_004", "toolu_00Y", 1), recs(5..6)), T0 + 3);
    assert_eq!(s.read(&p), ReadKind::Guard);
    assert_eq!(s.get(&p).unwrap().records(), 6);
}

#[test]
fn a_growth_with_a_change_before_the_guard_serves_the_old_prefix_as_python_does() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4);
    write(&p, body.as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    write(&p, format!("{}{}", body.replacen("u-000", "u-00X", 1), rec(4)).as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Grown, "only the 64 bytes before the offset are checked on growth");
    assert_eq!(uuids(&s, &p)[0], "u-000");
}

#[test]
fn a_deleted_file_drops_its_entry_and_comes_back_from_zero() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, recs(0..2).as_bytes(), T0);
    let mut s = Store::new();
    s.read(&p);
    let generation = s.get(&p).unwrap().generation();
    fs::remove_file(&p).unwrap();
    assert_eq!(s.read(&p), ReadKind::Absent);
    assert!(s.get(&p).is_none());
    write(&p, recs(0..2).as_bytes(), T0);
    assert_eq!(s.read(&p), ReadKind::Zero);
    assert!(s.get(&p).unwrap().generation() > generation);
    let d = t.path("dir.jsonl");
    fs::create_dir(&d).unwrap();
    assert_eq!(s.read(&d), ReadKind::Failed, "Python's open() refuses a directory");
    assert!(s.get(&d).is_none());
}

#[test]
fn an_empty_file_then_its_first_lines() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, b"", T0);
    let mut s = Store::new();
    assert_eq!(s.read(&p), ReadKind::Zero);
    assert_eq!(s.get(&p).unwrap().records(), 0);
    append(&p, recs(0..1).as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Grown);
    assert_eq!(s.get(&p).unwrap().records(), 1);
}

#[test]
fn refused_and_blank_lines_are_held_but_not_indexed() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = b"{\"a\": 1}\n\n   \nnot json\n\x0b{\"type\": \"x\"}\x0c\r\n[1, 2]\r{\"b\": NaN}\n{\"c\": \"\\ud800\"}\n";
    write(&p, body, T0);
    let mut s = Store::new();
    s.read(&p);
    let e = s.get(&p).unwrap();
    assert_eq!(e.bytes_held(), body.len());
    let got: Vec<(u64, u32, &str)> = e.lines().map(|l| (l.offset(), l.len(), l.kind().name())).collect();
    // Offsets and lengths as Python's _scan_jsonl_stream gives them for these bytes: [0, 9, 23, 17, 40, 7, 47, 11, 58, 16].
    assert_eq!(got, vec![(0, 9, "object"), (23, 17, "object"), (40, 7, "array"), (47, 11, "object"), (58, 16, "object")]);
    assert!(e.line(3).decode().is_ok());
    assert_eq!(e.line(4).decode().unwrap_err().reason(), "lone surrogate");
}

#[test]
fn the_planted_defect_changes_the_answer() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    let body = recs(0..4);
    write(&p, body.as_bytes(), T0);
    let mut s = Store::with_plant(Some(romp_transcript::Plant::SkipHeldCrc));
    s.read(&p);
    write(&p, body.replacen("u-001", "u-00X", 1).as_bytes(), T0 + 1);
    assert_eq!(s.read(&p), ReadKind::Touched, "without the CRC the rewrite passes as a touch");
    assert_eq!(uuids(&s, &p)[1], "u-001");
}

#[test]
fn the_block_growth_holds_each_read_as_its_own_block_and_guards_across_them() {
    // S6's growth policy (the default): an append read's bytes become one exact block, so nothing held is copied, every
    // line is served from its block, and the 64-byte tail guard is gathered across blocks shorter than itself
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, recs(0..3).as_bytes(), T0);
    let mut s = Store::new();
    assert_eq!(s.growth(), Growth::Blocks);
    s.read(&p);
    let mut body = recs(0..3);
    let r3 = rec(3);
    for (k, line) in [r3.as_str(), "{\"a\": 1}\n", "{}\n", "\n", "{\"b\": 22}\n"].iter().enumerate() {
        append(&p, line.as_bytes(), T0 + 1 + k as u64);
        body.push_str(line);
        assert_eq!(s.read(&p), ReadKind::Grown, "append {k}");
    }
    let e = s.get(&p).unwrap();
    assert_eq!((e.blocks(), e.bytes_held(), e.records()), (6, body.len(), 7));
    let raw: Vec<u8> = e.lines().flat_map(|l| l.raw().to_vec()).collect();
    let want: Vec<u8> = body.lines().filter(|l| !l.is_empty()).flat_map(|l| format!("{l}\n").into_bytes()).collect();
    assert_eq!(raw, want, "every line read back from its block");
    assert_eq!(uuids(&s, &p)[3], "u-003");
    // a change inside the guard window, which spans the last five blocks (24 bytes of short lines, then rec(3)'s): a
    // guard failure, read from zero
    assert!(e.lines().last().unwrap().len() < 64);
    let mut bad = body.clone().into_bytes();
    let at = bad.len() - 40;
    bad[at] = b'Z';
    bad.extend_from_slice(rec(4).as_bytes());
    write(&p, &bad, T0 + 10);
    assert_eq!(s.read(&p), ReadKind::Guard);
    assert_eq!(s.get(&p).unwrap().blocks(), 1);
}

#[test]
fn the_doubling_growth_keeps_one_buffer_and_its_slack() {
    let t = Tmp::new();
    let p = t.path("a.jsonl");
    write(&p, recs(0..8).as_bytes(), T0);
    let (mut b, mut d) = (Store::new(), Store::with_growth(Growth::Double));
    b.read(&p);
    d.read(&p);
    append(&p, rec(8).as_bytes(), T0 + 1);
    assert_eq!((b.read(&p), d.read(&p)), (ReadKind::Grown, ReadKind::Grown));
    let (eb, ed) = (b.get(&p).unwrap(), d.get(&p).unwrap());
    assert_eq!((eb.blocks(), ed.blocks()), (2, 1));
    assert_eq!(eb.bytes_held(), ed.bytes_held());
    assert_eq!(uuids(&b, &p), uuids(&d, &p));
    // the doubled buffer carries slack the blocks do not: at least the held bytes again, less the append
    assert!(ed.allocated_bytes() > eb.allocated_bytes() + ed.bytes_held() / 2,
            "double {} against blocks {}", ed.allocated_bytes(), eb.allocated_bytes());
}
