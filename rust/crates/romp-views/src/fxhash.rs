//! An in-tree fast hasher for the build's maps (rust/SPIKE.md, step S6.1): FxHash, the multiply-rotate hash rustc uses
//! for its own tables, written by hand (no crate). std's default `RandomState` hashes every key with SipHash-1-3 under a
//! per-process random seed, which resists collision floods but costs about four times as much per uuid as this. S6 found
//! that cost decisive on the large4 world: one per-derivation index over s001's 24,000 atom uuids
//! (`_dictated_prompt_uuid`'s prompt index) was most of the port's held build, where CPython reuses each string's cached
//! hash.
//!
//! What it gives up: the hash is deterministic and not keyed, so inputs chosen to collide could degrade a map to linear
//! probing. The spike's inputs are its own dumps; a production port reading untrusted keys would keep SipHash on the maps
//! those keys reach.
//!
//! Iteration order: a map's iteration order now repeats from run to run, where `RandomState` changed it per process. No
//! output of the port depends on a map's iteration order (every such read sorts first, or reads a `Vec` or an `OMap`):
//! under `RandomState` any such dependence would already have shown as a run-to-run difference against Python's bytes.

use std::collections::{HashMap, HashSet};
use std::hash::{BuildHasherDefault, Hasher};

/// The multiplier rustc's FxHasher uses for 64-bit words (from Firefox's hash).
const SEED: u64 = 0x51_7c_c1_b7_27_22_0a_95;

/// FxHash: per word, `hash = (hash.rotate_left(5) ^ word) * SEED`. Bytes are taken eight at a time, then four, two and
/// one; `write_str`'s terminator (std writes a 0xff byte after a str) keeps "ab" + "c" apart from "a" + "bc".
#[derive(Clone, Copy, Default)]
pub struct FxHasher {
    hash: u64,
}

impl FxHasher {
    #[inline]
    fn add(&mut self, word: u64) {
        self.hash = (self.hash.rotate_left(5) ^ word).wrapping_mul(SEED);
    }
}

impl Hasher for FxHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        let mut b = bytes;
        while b.len() >= 8 {
            self.add(u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]));
            b = &b[8..];
        }
        if b.len() >= 4 {
            self.add(u64::from(u32::from_le_bytes([b[0], b[1], b[2], b[3]])));
            b = &b[4..];
        }
        if b.len() >= 2 {
            self.add(u64::from(u16::from_le_bytes([b[0], b[1]])));
            b = &b[2..];
        }
        if let Some(&x) = b.first() {
            self.add(u64::from(x));
        }
    }

    #[inline]
    fn write_u8(&mut self, i: u8) {
        self.add(u64::from(i));
    }

    #[inline]
    fn write_u16(&mut self, i: u16) {
        self.add(u64::from(i));
    }

    #[inline]
    fn write_u32(&mut self, i: u32) {
        self.add(u64::from(i));
    }

    #[inline]
    fn write_u64(&mut self, i: u64) {
        self.add(i);
    }

    #[inline]
    fn write_usize(&mut self, i: usize) {
        self.add(i as u64);
    }

    #[inline]
    fn finish(&self) -> u64 {
        self.hash
    }
}

/// The hasher's builder: every map gets a fresh `FxHasher` with hash 0.
pub type FxBuild = BuildHasherDefault<FxHasher>;
/// A `HashMap` under FxHash.
pub type FxMap<K, V> = HashMap<K, V, FxBuild>;
/// A `HashSet` under FxHash.
pub type FxSet<T> = HashSet<T, FxBuild>;

#[cfg(test)]
mod tests {
    use super::*;
    use std::hash::{BuildHasher, Hash};

    fn h<T: Hash + ?Sized>(v: &T) -> u64 {
        FxBuild::default().hash_one(v)
    }

    #[test]
    fn the_hash_is_rustcs_fxhash() {
        // one word: (0.rotate_left(5) ^ 1) * SEED
        let mut s = FxHasher::default();
        s.write_u64(1);
        assert_eq!(s.finish(), SEED);
        // two words fold in order
        s.write_u64(2);
        assert_eq!(s.finish(), (SEED.rotate_left(5) ^ 2).wrapping_mul(SEED));
        // a byte string: one 8-byte word, then the tail of 3 as a u16 and a u8
        let mut a = FxHasher::default();
        a.write(b"abcdefghijk");
        let mut b = FxHasher::default();
        b.write_u64(u64::from_le_bytes(*b"abcdefgh"));
        b.write_u16(u16::from_le_bytes(*b"ij"));
        b.write_u8(b'k');
        assert_eq!(a.finish(), b.finish());
    }

    #[test]
    fn strings_hash_deterministically_and_apart() {
        assert_eq!(h("cccccccc-0001-0000-0000-000000000033"), h(&"cccccccc-0001-0000-0000-000000000033".to_string()));
        assert_ne!(h("cccccccc-0001-0000-0000-000000000033"), h("cccccccc-0001-0000-0000-000000000032"));
        assert_ne!(h(&("ab", "c")), h(&("a", "bc")), "str's terminator keeps a split apart");
        // a collision is legal (the map compares keys), and FxHash has easy ones: a leading zero word folds into a zero
        // state unchanged, so "" and "\0" hash alike; correctness never rests on the hash
        assert_eq!(h(""), h("\u{0}"));
    }

    #[test]
    fn the_maps_work() {
        let mut m: FxMap<String, usize> = FxMap::default();
        for i in 0..10_000 {
            m.insert(format!("11111111-2222-3333-4444-{i:012}"), i);
        }
        assert_eq!(m.len(), 10_000);
        assert_eq!(m.get("11111111-2222-3333-4444-000000004321"), Some(&4321));
        let s: FxSet<&str> = ["a", "b", "a"].into_iter().collect();
        assert_eq!(s.len(), 2);
    }
}
