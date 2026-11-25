//! The canonical per-file and per-record summary the spike compares with Python's (tools/rust-spike/check_store.py
//! builds the same objects from `_read_jsonl_incremental`'s records and its cache entry, and writes them with
//! `json.dumps` defaults). Keys come in a fixed order; a field absent from the record is absent from the summary.
//!
//! File: `{"file", "size", "mtime", "inode", "consumed", "records", "crc32"}`.
//! Record: `{"i", "off", "len", "kind"` then, for an object record, the five fields when present (a string as the
//! string, null as null, any other value as `{"nonString": kind}`), `"toolIds"` (always, maybe empty), and last
//! `"rec"`: the CRC-32 of `json.dumps(record, sort_keys=True)`, or `"record"`: that text itself (`--records`), or
//! `"undecodable: <reason>"` when the decoder refuses the record (decode.rs).

use crate::crc32;
use crate::decode::dumps_sorted;
use crate::store::{Entry, Field, FieldRef, Line};
use crate::Kind;
use std::fmt::Write as _;

/// How the summary carries each full record.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RecordMode {
    /// Its canonical text's CRC-32.
    Crc,
    /// Its canonical text.
    Full,
}

/// The file's summary line (no newline).
pub fn file_header(out: &mut String, name: &str, e: &Entry) {
    out.push_str("{\"file\": ");
    romp_json::write_str(out, name);
    let _ = write!(out, ", \"size\": {}, \"mtime\": ", e.size());
    romp_json::write_float(out, e.mtime());
    let _ = write!(
        out,
        ", \"inode\": {}, \"consumed\": {}, \"records\": {}, \"crc32\": {}}}",
        e.inode(),
        e.offset(),
        e.records(),
        e.crc32()
    );
}

/// Record `i`'s summary line (no newline).
pub fn line_summary(out: &mut String, i: usize, line: &Line, mode: RecordMode) {
    let _ = write!(out, "{{\"i\": {i}, \"off\": {}, \"len\": {}, \"kind\": \"{}\"", line.offset(), line.len(), line.kind().name());
    if line.kind() == Kind::Object {
        for f in Field::ALL {
            let v = line.field(f);
            if v == FieldRef::Absent {
                continue;
            }
            let _ = write!(out, ", \"{}\": ", f.name());
            match v {
                FieldRef::Null => out.push_str("null"),
                FieldRef::Str(s) => s.write_json(out),
                FieldRef::Other(k) => {
                    let _ = write!(out, "{{\"nonString\": \"{}\"}}", k.name());
                }
                FieldRef::Absent => unreachable!(),
            }
        }
        out.push_str(", \"toolIds\": [");
        for (n, id) in line.tool_ids().enumerate() {
            if n > 0 {
                out.push_str(", ");
            }
            id.write_json(out);
        }
        out.push(']');
    }
    match (line.decode(), mode) {
        (Ok(v), RecordMode::Crc) => {
            let _ = write!(out, ", \"rec\": {}", crc32::update(0, dumps_sorted(&v).as_bytes()));
        }
        (Ok(v), RecordMode::Full) => {
            out.push_str(", \"record\": ");
            out.push_str(&dumps_sorted(&v));
        }
        (Err(e), _) => {
            let _ = write!(out, ", \"{}\": \"undecodable: {}\"", if mode == RecordMode::Crc { "rec" } else { "record" }, e.reason());
        }
    }
    out.push('}');
}
