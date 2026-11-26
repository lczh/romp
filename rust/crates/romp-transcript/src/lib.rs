//! The transcript store: raw bytes plus a per-line index, with the Python cache's identity contract
//! (rust/SPIKE.md, step S3; plans/rust-migration.md, "The transcript store").
//!
//! It replaces `_JSONL_CACHE` and `_read_jsonl_incremental` (kernel/event_model.py) for the whole-file read and
//! answers the same questions the same way: which lines are records (scan.rs), when an entry still stands and when
//! a file must be read again from byte zero (store.rs), and what a record decodes to (decode.rs). Where Python holds
//! every record decoded, the store holds the bytes and a 64-byte summary per record, and decodes on demand.
//!
//! Not ported here: the checkpoint's tail entries (base > 0) and their restore, the LRU count cap and the byte budget,
//! the quiescent drop, fold cursors, the read stripes, and Python's byte counters by caller.

pub mod canon;
pub mod crc32;
pub mod decode;
pub mod scan;
pub mod store;

pub use decode::{DecodeError, decode, dumps_sorted};
pub use scan::Kind;
pub use store::{Entry, Field, FieldRef, Growth, Line, ReadKind, Stats, Store, StrRef, TAIL_GUARD, py_mtime};

/// A deliberate defect the comparison's self-check plants to prove it would catch one (tools/rust-spike/check_store.py
/// `--plant`). Never set outside that check.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Plant {
    /// Tool results' `tool_use_id`s are not collected (the dumped worlds have them).
    NoToolResult,
    /// The strip is Rust's `trim_ascii`, which keeps `\x0b` (the edge corpus has a line it changes).
    VtStrip,
    /// A same-size file under a new mtime is trusted without its held-byte CRC (the same-size rewrite scenario).
    SkipHeldCrc,
}

impl Plant {
    pub fn parse(s: &str) -> Option<Plant> {
        match s {
            "no-tool-result" => Some(Plant::NoToolResult),
            "vt-strip" => Some(Plant::VtStrip),
            "skip-held-crc" => Some(Plant::SkipHeldCrc),
            _ => None,
        }
    }
}
