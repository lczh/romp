//! The feed's per-card build, the spike's slice of the views crate (rust/SPIKE.md, step S4).
//!
//! Built in the order of rust/notes/s2-design.md section 2: the typed loader (`record`, `tree`, `snapshot`, `load`),
//! then layer 1, the text and atom primitives (`layer1`, over `pystr` and `sha1`), layer 2, segmentation (`layer2`),
//! layer 3, the tree facts and the anchor walk (`layer3`), layer 4, the store helpers (`layer4`), and layer 5, landing,
//! delegation and provisional (`layer5`, over `pyre`: Python's regex classes and ignore-case folding, from generated
//! tables), and layer 6, the body and its closures (`layer6`: the per-session entry build_feed memoizes, over
//! `pystr::upper`: Python's `str.upper()`, from a generated table in `pystr_tables`), and layer 7, the loop, the fold and the
//! cross-session pass (`layer7`: the whole feed frame).

pub mod fxhash;
#[macro_use]
pub mod record;
pub mod layer1;
pub mod layer2;
pub mod layer3;
pub mod layer4;
pub mod layer5;
pub mod layer6;
pub mod layer7;
pub mod memo;
pub mod load;
pub mod pyre;
pub mod pyre_tables;
pub mod pystr;
pub mod pystr_tables;
pub mod sha1;
pub mod snapshot;
pub mod tree;
