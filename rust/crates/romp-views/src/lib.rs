//! The feed's per-card build, the spike's slice of the views crate (rust/SPIKE.md, step S4).
//!
//! Built in the order of rust/notes/s2-design.md section 2: the typed loader (`record`, `tree`, `snapshot`, `load`),
//! then layer 1, the text and atom primitives (`layer1`, over `pystr` and `sha1`). Layers 2 to 7 follow.

#[macro_use]
pub mod record;
pub mod layer1;
pub mod load;
pub mod pystr;
pub mod sha1;
pub mod snapshot;
pub mod tree;
