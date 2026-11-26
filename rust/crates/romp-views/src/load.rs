//! Reading a dump directory (tools/rust-spike/dump_world.py): `snapshot.json` and the `trees/<sid>.json` its alive
//! rows name, typed and validated. Every error names the file and the path inside it.
//!
//! An integer literal past i64 is refused anywhere in the snapshot (and, through `refuse_big_ints`, in the edge-case
//! and answer files romp-spike reads). Python's `json` reads one as an int, while serde_json (without
//! `arbitrary_precision`) reads one past u64 as a float, so `Num` would carry 2**64 + 1 as the float
//! 1.8446744073709552e19 (`Num` already refuses 2**63 to 2**64 - 1, which serde_json hands it as a u64). The check
//! decodes the bytes again with romp-transcript's decoder, which reads numbers as Python does and keeps such a literal
//! as `BigInt`. Trees are not checked: the second decode would triple their load (the large world's 79.6 MB, about
//! 150 ms to 450 ms), so a tree holding such an integer would still load it as a float. No dumped tree holds one.

use std::fs;
use std::path::{Path, PathBuf};

use romp_json::Value as Out;

use crate::snapshot::{self, Snapshot};
use crate::tree::{self, Tree};

/// The path of the first integer literal past i64 under `v` (a `BigInt` from romp-transcript's decoder), the top
/// object's members named in `skip` left out; None when there is none.
pub fn big_int_path(v: &Out, skip: &[&str]) -> Option<String> {
    fn walk(v: &Out, path: &mut String, skip: &[&str]) -> bool {
        match v {
            Out::BigInt(_) => true,
            Out::Array(items) => items.iter().enumerate().any(|(i, x)| {
                let n = path.len();
                path.push_str(&format!("[{i}]"));
                let hit = walk(x, path, &[]);
                if !hit {
                    path.truncate(n);
                }
                hit
            }),
            Out::Object(members) => members.iter().filter(|(k, _)| !skip.contains(&k.as_str())).any(|(k, x)| {
                let n = path.len();
                path.push_str(&if n == 0 { k.clone() } else { format!(".{k}") });
                let hit = walk(x, path, &[]);
                if !hit {
                    path.truncate(n);
                }
                hit
            }),
            _ => false,
        }
    }
    let mut path = String::new();
    walk(v, &mut path, skip).then(|| if path.is_empty() { "(the value)".to_string() } else { path })
}

/// Refuse an integer literal past i64 under `v`, the top object's `skip` members left out.
pub fn refuse_big_int(v: &Out, skip: &[&str]) -> Result<(), String> {
    match big_int_path(v, skip) {
        Some(p) => Err(format!("{p}: an integer past i64, which Python reads as an int and serde_json as a float")),
        None => Ok(()),
    }
}

/// Refuse a JSON document holding an integer literal past i64 anywhere (decoded as Python reads it).
pub fn refuse_big_ints(bytes: &[u8]) -> Result<(), String> {
    let v = romp_transcript::decode::decode(bytes).map_err(|e| format!("{} (decoding as Python does)", e.reason()))?;
    refuse_big_int(&v, &[])
}

/// A loaded dump: the snapshot and, per alive row in order, its sid and tree (None when the row names no tree).
pub struct Dump {
    pub dir: PathBuf,
    pub snapshot: Snapshot,
    pub trees: Vec<(String, Option<Tree>)>,
}

impl Dump {
    pub fn tree(&self, sid: &str) -> Option<&Tree> {
        self.trees.iter().find(|(s, _)| s == sid).and_then(|(_, t)| t.as_ref())
    }
}

fn read(path: &Path) -> Result<Vec<u8>, String> {
    fs::read(path).map_err(|e| format!("{}: {e}", path.display()))
}

/// Parse and validate a snapshot's bytes.
pub fn snapshot_from_slice(bytes: &[u8]) -> Result<Snapshot, String> {
    let snap: Snapshot = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    refuse_big_ints(bytes)?;
    snapshot::validate(&snap)?;
    Ok(snap)
}

/// Parse and validate a tree's bytes.
pub fn tree_from_slice(bytes: &[u8]) -> Result<Tree, String> {
    let tree: Tree = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    tree::validate(&tree)?;
    Ok(tree)
}

pub fn load_snapshot(path: &Path) -> Result<Snapshot, String> {
    snapshot_from_slice(&read(path)?).map_err(|e| format!("{}: {e}", path.display()))
}

pub fn load_tree(path: &Path) -> Result<Tree, String> {
    tree_from_slice(&read(path)?).map_err(|e| format!("{}: {e}", path.display()))
}

/// The tree path an alive row names, relative to the dump directory; refused if it leaves the directory.
pub fn tree_path(dir: &Path, rel: &str) -> Result<PathBuf, String> {
    if rel.starts_with('/') || rel.split('/').any(|p| p == "..") {
        return Err(format!("tree path {rel:?} leaves the dump directory"));
    }
    Ok(dir.join(rel))
}

/// Load `dir/snapshot.json` and every tree its alive rows name.
pub fn load_dump(dir: &Path) -> Result<Dump, String> {
    let snapshot = load_snapshot(&dir.join("snapshot.json"))?;
    let mut trees = Vec::new();
    for row in snapshot.alive() {
        let sid = row.sid.clone().expect("required at load");
        let tree = match row.tree.as_ref().and_then(Option::as_deref) {
            Some(rel) => Some(load_tree(&tree_path(dir, rel)?)?),
            None => None,
        };
        trees.push((sid, tree));
    }
    Ok(Dump { dir: dir.to_path_buf(), snapshot, trees })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_integer_past_i64_is_refused_where_python_reads_an_int() {
        let ok = br#"{"a": [9223372036854775807, -9223372036854775808, 1e20, 18446744073709551617.0, -0.0]}"#;
        assert_eq!(refuse_big_ints(ok), Ok(()), "i64's bounds and every float load");
        let big = br#"{"a": {"b": [1, 18446744073709551617]}}"#;
        assert_eq!(refuse_big_ints(big).unwrap_err(),
                   "a.b[1]: an integer past i64, which Python reads as an int and serde_json as a float");
        assert_eq!(big_int_path(&romp_transcript::decode::decode(b"-9223372036854775809").unwrap(), &[]),
                   Some("(the value)".to_string()));
        let case = romp_transcript::decode::decode(br#"{"since": 5, "expect": 100000000000000000000}"#).unwrap();
        assert_eq!(refuse_big_int(&case, &["expect"]), Ok(()), "a skipped member is not looked at");
        assert!(refuse_big_int(&case, &[]).is_err());
        // what the check guards against: serde_json's float, which Num would take
        let lossy: crate::record::Num = serde_json::from_slice(b"18446744073709551617").unwrap();
        assert_eq!(lossy, crate::record::Num::Float(18446744073709551616.0));
        assert!(serde_json::from_slice::<crate::record::Num>(b"9223372036854775808").is_err(), "Num refuses a u64");
    }
}
