//! The store: per file, the raw bytes of every complete line, a per-line index, and the identity the Python record
//! cache checks before it trusts what it holds. A read follows `_read_jsonl_entry_unlocked` (kernel/event_model.py)
//! step for step, for a whole entry (base 0; the checkpoint's tail entries are not ported):
//!
//! 1. `stat` fails: the entry is dropped and the read answers no records (`Absent` for a missing file, `Failed`
//!    for any other error, as Python's `on_fail` tells them apart).
//! 2. The same mtime (compared as the float CPython computes, `sec + nsec * 1e-9`) and the same size: served as held,
//!    nothing read (`Hit`). Neither the inode nor the bytes are looked at, so a same-size rewrite that keeps its
//!    mtime, or a copy renamed over the path with its mtime preserved, is served stale, as in Python.
//! 3. Larger than the entry: the 64 bytes before the held offset are read and compared with the held ones (the
//!    tail guard). Standing, only the bytes from the offset to the size the stat saw are read and indexed (`Grown`,
//!    generation kept). The inode is not consulted on this road, so a longer file renamed over the path whose guard
//!    bytes agree is read as an append, and a change before the guard window is not seen, as in Python.
//! 4. The same size under a new mtime and the same inode: the tail guard, then the CRC-32 of every held byte
//!    `[0, offset)` read back from the file against the one the entry kept. Both standing: served under its
//!    generation, with the bytes past the offset scanned as for a growth (`Touched`). Either failing: from zero.
//! 5. Anything else is read from zero under a new generation: smaller than the held offset (`Shrunk`), a guard
//!    mismatch (`Guard`), a held-byte mismatch or the same size under a new inode (`Rewrite`, the name Python's trace
//!    gives both), or a first read (`Zero`).
//!
//! A read never goes past the size its stat saw, and a trailing line with no newline is left unconsumed: the entry's
//! offset stays before it, so the read that finds it completed indexes it (scan.rs).
//!
//! Reads are positioned (`FileExt::read_at`, Unix `pread`), never a memory map: the CLI can truncate a transcript in
//! place, and touching a mapped page past the new end kills the process with SIGBUS.
//!
//! What an entry holds per kept record: its raw bytes inside the entry's buffer (which also keeps the bytes of
//! blank and refused lines, everything in `[0, offset)`), one 64-byte `LineIndex` and 8 bytes per tool id. Decoded
//! records are never held (`Line::decode` builds one for the caller). Python's entry holds every decoded record
//! (measured at 2.86 heap bytes per file byte) plus 16 bytes of offsets per record and a 64-byte tail copy.

use crate::Plant;
use crate::crc32;
use crate::decode::{self, DecodeError};
use crate::scan::{self, Kind, LineIndex, Scratch, Span};
use romp_json::Value;
use std::collections::HashMap;
use std::fs::{self, File, Metadata};
use std::io;
use std::os::unix::fs::{FileExt, MetadataExt};
use std::path::{Path, PathBuf};

/// Bytes before the held offset re-read and compared before an append is trusted (`_JSONL_TAIL_GUARD`).
pub const TAIL_GUARD: u64 = 64;
const CRC_CHUNK: usize = 1 << 22; // `_held_crc` reads 4 MiB at a time

/// How a read was served: Python's trace kinds (`ROMP_READER_TRACE`), plus `Hit` (no bytes read) and the two
/// answers with no entry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReadKind {
    Hit,
    Zero,
    Grown,
    Touched,
    Guard,
    Rewrite,
    Shrunk,
    Absent,
    Failed,
}

impl ReadKind {
    pub const ALL: [ReadKind; 9] = [
        ReadKind::Hit,
        ReadKind::Zero,
        ReadKind::Grown,
        ReadKind::Touched,
        ReadKind::Guard,
        ReadKind::Rewrite,
        ReadKind::Shrunk,
        ReadKind::Absent,
        ReadKind::Failed,
    ];

    pub fn name(self) -> &'static str {
        match self {
            ReadKind::Hit => "hit",
            ReadKind::Zero => "zero",
            ReadKind::Grown => "grown",
            ReadKind::Touched => "touched",
            ReadKind::Guard => "guard",
            ReadKind::Rewrite => "rewrite",
            ReadKind::Shrunk => "shrunk",
            ReadKind::Absent => "absent",
            ReadKind::Failed => "failed",
        }
    }

    /// The file was indexed again from byte zero under a new generation.
    pub fn reindexed(self) -> bool {
        matches!(self, ReadKind::Zero | ReadKind::Guard | ReadKind::Rewrite | ReadKind::Shrunk)
    }
}

/// What the store has done since it was made.
#[derive(Clone, Debug, Default)]
pub struct Stats {
    /// Bytes pulled off disk: tail guards, held-byte CRCs and scanned ranges (trailing partials included).
    pub bytes_read: u64,
    /// Reads by kind, in `ReadKind::ALL` order.
    pub reads: [u64; 9],
}

/// One file's entry.
pub struct Entry {
    mtime: f64,
    mtime_ns: (i64, i64),
    size: u64,
    offset: u64,
    inode: u64,
    crc: u32,
    generation: u64,
    bytes: Vec<u8>,
    lines: Vec<LineIndex>,
    tools: Vec<Span>,
}

/// The transcript store.
pub struct Store {
    entries: HashMap<PathBuf, Entry>,
    next_gen: u64,
    scratch: Scratch,
    plant: Option<Plant>,
    pub stats: Stats,
}

impl Default for Store {
    fn default() -> Self {
        Store::new()
    }
}

/// `os.stat(path).st_mtime` as CPython computes it (`fill_time`: `sec + nsec * 1e-9` in doubles). Two mtimes a few
/// hundred nanoseconds apart can give the same float, and Python's cache then sees no change; so does this one.
pub fn py_mtime(md: &Metadata) -> f64 {
    md.mtime() as f64 + md.mtime_nsec() as f64 * 1e-9
}

fn fail_kind(e: &io::Error) -> ReadKind {
    if e.kind() == io::ErrorKind::NotFound { ReadKind::Absent } else { ReadKind::Failed }
}

/// Fill `buf` from `off` with positioned reads; returns how many bytes the file had (fewer at its end).
fn read_full_at(file: &File, buf: &mut [u8], off: u64) -> io::Result<usize> {
    let mut got = 0;
    while got < buf.len() {
        match file.read_at(&mut buf[got..], off + got as u64) {
            Ok(0) => break,
            Ok(n) => got += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(got)
}

impl Store {
    pub fn new() -> Self {
        Store { entries: HashMap::new(), next_gen: 0, scratch: Scratch::default(), plant: None, stats: Stats::default() }
    }

    /// A store with one deliberate defect, for the comparison's self-check (it must catch it).
    pub fn with_plant(plant: Option<Plant>) -> Self {
        Store { plant, ..Store::new() }
    }

    /// Bring `path`'s entry up to date with the file (the module docs give the rules) and say how.
    pub fn read(&mut self, path: &Path) -> ReadKind {
        let kind = self.read_inner(path);
        self.stats.reads[ReadKind::ALL.iter().position(|k| *k == kind).expect("listed")] += 1;
        kind
    }

    /// The entry the last read left, if any.
    pub fn get(&self, path: &Path) -> Option<&Entry> {
        self.entries.get(path)
    }

    /// Drop `path`'s entry.
    pub fn forget(&mut self, path: &Path) {
        self.entries.remove(path);
    }

    pub fn entries(&self) -> impl Iterator<Item = (&PathBuf, &Entry)> {
        self.entries.iter()
    }

    fn read_inner(&mut self, path: &Path) -> ReadKind {
        let md = match fs::metadata(path) {
            Ok(m) => m,
            Err(e) => {
                self.entries.remove(path);
                return fail_kind(&e);
            }
        };
        if let Some(e) = self.entries.get(path)
            && e.mtime == py_mtime(&md)
            && e.size == md.len()
        {
            return ReadKind::Hit;
        }
        let prev = self.entries.remove(path);
        match self.refresh(path, &md, prev) {
            Ok((entry, kind)) => {
                self.entries.insert(path.to_path_buf(), entry);
                kind
            }
            Err(e) => fail_kind(&e), // the entry stays out, as Python pops it on an OSError
        }
    }

    fn refresh(&mut self, path: &Path, md: &Metadata, prev: Option<Entry>) -> io::Result<(Entry, ReadKind)> {
        if md.is_dir() {
            return Err(io::Error::new(io::ErrorKind::IsADirectory, "a directory")); // Python's open() refuses it
        }
        let file = File::open(path)?;
        let (mtime, size, inode) = (py_mtime(md), md.len(), md.ino());
        let mut kind = ReadKind::Zero;
        if let Some(mut e) = prev {
            let grown = size > e.size;
            let touched = size == e.size && mtime != e.mtime && e.inode == inode;
            if grown || touched {
                let tail_len = e.offset.min(TAIL_GUARD) as usize;
                let tail = &e.bytes[e.bytes.len() - tail_len..];
                let mut guard = vec![0u8; tail_len];
                let n = read_full_at(&file, &mut guard, e.offset - tail_len as u64)?;
                self.stats.bytes_read += n as u64;
                let guard_ok = n == tail_len && guard == tail;
                let held_ok = guard_ok
                    && (!touched || self.plant == Some(Plant::SkipHeldCrc) || self.held_crc(&file, e.offset)? == Some(e.crc));
                if held_ok {
                    self.extend(&file, &mut e, size)?;
                    e.mtime = mtime;
                    e.mtime_ns = (md.mtime(), md.mtime_nsec());
                    e.size = size;
                    e.inode = inode;
                    return Ok((e, if touched { ReadKind::Touched } else { ReadKind::Grown }));
                }
                kind = if guard_ok { ReadKind::Rewrite } else { ReadKind::Guard };
            } else {
                kind = if size < e.offset { ReadKind::Shrunk } else { ReadKind::Rewrite };
            }
        }
        self.next_gen += 1;
        let mut e = Entry {
            mtime,
            mtime_ns: (md.mtime(), md.mtime_nsec()),
            size,
            offset: 0,
            inode,
            crc: 0,
            generation: self.next_gen,
            bytes: Vec::new(),
            lines: Vec::new(),
            tools: Vec::new(),
        };
        self.extend(&file, &mut e, size)?;
        Ok((e, kind))
    }

    /// `_held_crc`: the CRC-32 of the file's bytes `[0, end)`, or None when it holds fewer.
    fn held_crc(&mut self, file: &File, end: u64) -> io::Result<Option<u32>> {
        let mut buf = vec![0u8; CRC_CHUNK.min(end as usize)];
        let (mut crc, mut at) = (0u32, 0u64);
        while at < end {
            let want = ((end - at) as usize).min(buf.len());
            let n = read_full_at(file, &mut buf[..want], at)?;
            self.stats.bytes_read += n as u64;
            crc = crc32::update(crc, &buf[..n]);
            at += n as u64;
            if n < want {
                return Ok(None);
            }
        }
        Ok(Some(crc))
    }

    /// Read the file from the entry's offset to `size` (the stat's), index its complete lines and hold their bytes.
    fn extend(&mut self, file: &File, e: &mut Entry, size: u64) -> io::Result<()> {
        let want = size.saturating_sub(e.offset) as usize;
        let mut buf = vec![0u8; want];
        let n = read_full_at(file, &mut buf, e.offset)?;
        self.stats.bytes_read += n as u64;
        buf.truncate(n);
        let consumed = scan::scan_chunk(&buf, e.offset, &mut e.lines, &mut e.tools, &mut self.scratch, self.plant)
            .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, format!("{err:?}")))?;
        buf.truncate(consumed);
        e.crc = crc32::update(e.crc, &buf);
        e.offset += consumed as u64;
        if e.bytes.is_empty() {
            buf.shrink_to_fit();
            e.bytes = buf;
        } else {
            e.bytes.extend_from_slice(&buf);
        }
        Ok(())
    }
}

/// A summary field of a record.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Field {
    /// The record's `type`.
    Type,
    /// The record's `uuid`.
    Uuid,
    /// The record's `parentUuid`.
    ParentUuid,
    /// The record's `timestamp`.
    Timestamp,
    /// `message.role`, when `message` is an object.
    Role,
}

impl Field {
    pub const ALL: [Field; 5] = [Field::Type, Field::Uuid, Field::ParentUuid, Field::Timestamp, Field::Role];

    pub fn name(self) -> &'static str {
        match self {
            Field::Type => "type",
            Field::Uuid => "uuid",
            Field::ParentUuid => "parentUuid",
            Field::Timestamp => "timestamp",
            Field::Role => "role",
        }
    }

    fn slot(self) -> usize {
        match self {
            Field::Type => scan::F_TYPE,
            Field::Uuid => scan::F_UUID,
            Field::ParentUuid => scan::F_PARENT,
            Field::Timestamp => scan::F_TS,
            Field::Role => scan::F_ROLE,
        }
    }
}

/// A summary field's value: absent (no such key), null, a string, or a value of another kind.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FieldRef<'a> {
    Absent,
    Null,
    Str(StrRef<'a>),
    Other(Kind),
}

/// A JSON string as the held bytes have it (between its quotes, escapes undecoded).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StrRef<'a>(&'a [u8]);

impl<'a> StrRef<'a> {
    pub fn raw(&self) -> &'a [u8] {
        self.0
    }

    /// The string's UTF-16 code units, as Python's `str` holds it (lone surrogates included).
    pub fn units(&self) -> Vec<u16> {
        let mut u = Vec::with_capacity(self.0.len());
        scan::decode_units(self.0, &mut u);
        u
    }

    /// The string, with any lone surrogate replaced by U+FFFD.
    pub fn to_string_lossy(&self) -> String {
        String::from_utf16_lossy(&self.units())
    }

    /// Whether it decodes to `s` (ASCII).
    pub fn eq_ascii(&self, s: &str) -> bool {
        scan::raw_str_eq(self.0, s.as_bytes())
    }

    /// Append `json.dumps(s)` (ensure_ascii) to `out`, exactly as Python writes the decoded `str`.
    pub fn write_json(&self, out: &mut String) {
        out.push('"');
        for u in self.units() {
            match u {
                0x22 => out.push_str("\\\""),
                0x5c => out.push_str("\\\\"),
                0x0a => out.push_str("\\n"),
                0x0d => out.push_str("\\r"),
                0x09 => out.push_str("\\t"),
                0x08 => out.push_str("\\b"),
                0x0c => out.push_str("\\f"),
                0x20..=0x7e => out.push(u as u8 as char),
                _ => {
                    use std::fmt::Write as _;
                    let _ = write!(out, "\\u{u:04x}");
                }
            }
        }
        out.push('"');
    }
}

/// One kept record, borrowed from its entry.
#[derive(Clone, Copy)]
pub struct Line<'a> {
    e: &'a Entry,
    ix: &'a LineIndex,
}

impl<'a> Line<'a> {
    /// The record's byte offset in the file.
    pub fn offset(&self) -> u64 {
        self.ix.off
    }

    /// Its length in bytes, line terminator included.
    pub fn len(&self) -> u32 {
        self.ix.len
    }

    pub fn is_empty(&self) -> bool {
        self.ix.len == 0
    }

    pub fn kind(&self) -> Kind {
        self.ix.kind
    }

    /// The line as the file has it, terminator included.
    pub fn raw(&self) -> &'a [u8] {
        &self.e.bytes[self.ix.off as usize..self.ix.off as usize + self.ix.len as usize]
    }

    /// The JSON text: the line stripped as `bytes.strip()` strips it.
    pub fn json(&self) -> &'a [u8] {
        let raw = self.raw();
        let (a, b) = scan::py_strip(raw, None);
        &raw[a..b]
    }

    fn span(&self, sp: Span) -> StrRef<'a> {
        StrRef(&self.raw()[sp.start as usize..sp.end as usize])
    }

    pub fn field(&self, f: Field) -> FieldRef<'a> {
        let slot = f.slot();
        match self.ix.tag(slot) {
            scan::T_ABSENT => FieldRef::Absent,
            scan::T_NULL => FieldRef::Null,
            scan::T_STR => FieldRef::Str(self.span(self.ix.fields[slot])),
            scan::T_NUMBER => FieldRef::Other(Kind::Number),
            scan::T_BOOL => FieldRef::Other(Kind::Bool),
            scan::T_OBJECT => FieldRef::Other(Kind::Object),
            _ => FieldRef::Other(Kind::Array),
        }
    }

    /// The ids of `message.content`'s blocks in order: a `tool_use` block's `id`, a `tool_result` block's
    /// `tool_use_id` (string values only).
    pub fn tool_ids(&self) -> impl Iterator<Item = StrRef<'a>> + 'a {
        let line = *self;
        let (a, n) = (self.ix.tools_start as usize, self.ix.tools_len as usize);
        self.e.tools[a..a + n].iter().map(move |&sp| line.span(sp))
    }

    /// The full record, decoded now; the store keeps nothing of it.
    pub fn decode(&self) -> Result<Value, DecodeError> {
        decode::decode(self.json())
    }
}

impl Entry {
    /// How many records are held (Python's `len(records)`).
    pub fn records(&self) -> usize {
        self.lines.len()
    }

    pub fn line(&self, i: usize) -> Line<'_> {
        Line { e: self, ix: &self.lines[i] }
    }

    pub fn lines(&self) -> impl Iterator<Item = Line<'_>> {
        self.lines.iter().map(move |ix| Line { e: self, ix })
    }

    /// `st_mtime` as the float Python compares.
    pub fn mtime(&self) -> f64 {
        self.mtime
    }

    /// The mtime as (seconds, nanoseconds).
    pub fn mtime_ns(&self) -> (i64, i64) {
        self.mtime_ns
    }

    /// The size the read's stat saw.
    pub fn size(&self) -> u64 {
        self.size
    }

    /// The offset just past the last complete line (Python's consumed offset).
    pub fn offset(&self) -> u64 {
        self.offset
    }

    pub fn inode(&self) -> u64 {
        self.inode
    }

    /// The CRC-32 of the held bytes `[0, offset)`.
    pub fn crc32(&self) -> u32 {
        self.crc
    }

    /// The generation: new for every read from zero, kept across appends and touches.
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// Bytes held: the file's `[0, offset)`.
    pub fn bytes_held(&self) -> usize {
        self.bytes.len()
    }

    /// The index's size: one `LineIndex` per record plus one span per tool id.
    pub fn index_bytes(&self) -> usize {
        self.lines.len() * std::mem::size_of::<LineIndex>() + self.tools.len() * std::mem::size_of::<Span>()
    }

    /// What the entry's three vectors have allocated (held bytes, index and tool table, slack included).
    pub fn allocated_bytes(&self) -> usize {
        self.bytes.capacity()
            + self.lines.capacity() * std::mem::size_of::<LineIndex>()
            + self.tools.capacity() * std::mem::size_of::<Span>()
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn index_entries_stay_compact() {
        assert_eq!(std::mem::size_of::<super::LineIndex>(), 64);
        assert_eq!(std::mem::size_of::<super::Span>(), 8);
    }
}
