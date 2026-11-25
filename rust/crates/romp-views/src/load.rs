//! Reading a dump directory (tools/rust-spike/dump_world.py): `snapshot.json` and the `trees/<sid>.json` its alive
//! rows name, typed and validated. Every error names the file and the path inside it.

use std::fs;
use std::path::{Path, PathBuf};

use crate::snapshot::{self, Snapshot};
use crate::tree::{self, Tree};

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
