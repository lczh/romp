//! SHA-1 (FIPS 180-4), by hand: the segment ids' content hash is `hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]`
//! and no crate beyond serde and serde_json is approved. A Rust `String` holds no lone surrogate, so Python's
//! `encode("utf-8", "replace")` is the string's own bytes here.

/// The 20-byte digest of `data`.
pub fn digest(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476, 0xC3D2_E1F0];
    let bit_len = (data.len() as u64).wrapping_mul(8);
    let mut tail = Vec::with_capacity(128);
    let full = data.len() / 64 * 64;
    tail.extend_from_slice(&data[full..]);
    tail.push(0x80);
    while tail.len() % 64 != 56 {
        tail.push(0);
    }
    tail.extend_from_slice(&bit_len.to_be_bytes());
    for block in data[..full].chunks_exact(64).chain(tail.chunks_exact(64)) {
        compress(&mut h, block);
    }
    let mut out = [0u8; 20];
    for (i, word) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

fn compress(h: &mut [u32; 5], block: &[u8]) {
    let mut w = [0u32; 80];
    for (i, chunk) in block.chunks_exact(4).enumerate() {
        w[i] = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
    }
    for i in 16..80 {
        w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
    }
    let [mut a, mut b, mut c, mut d, mut e] = *h;
    for (i, wi) in w.iter().enumerate() {
        let (f, k) = match i {
            0..=19 => ((b & c) | (!b & d), 0x5A82_7999),
            20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
            40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
            _ => (b ^ c ^ d, 0xCA62_C1D6),
        };
        let t = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(*wi);
        e = d;
        d = c;
        c = b.rotate_left(30);
        b = a;
        a = t;
    }
    for (x, v) in h.iter_mut().zip([a, b, c, d, e]) {
        *x = x.wrapping_add(v);
    }
}

/// `hexdigest()`.
pub fn hex(data: &[u8]) -> String {
    let mut s = String::with_capacity(40);
    for b in digest(data) {
        s.push(char::from_digit(u32::from(b >> 4), 16).expect("a nibble"));
        s.push(char::from_digit(u32::from(b & 15), 16).expect("a nibble"));
    }
    s
}

/// `hexdigest()[:8]`, the ids' hash.
pub fn hex8(data: &[u8]) -> String {
    let mut s = hex(data);
    s.truncate(8);
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fips_vectors() {
        assert_eq!(hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
                   "84983e441c3bd26ebaae4aa1f95129e5e54670f1");
        assert_eq!(hex(&vec![b'a'; 1_000_000]), "34aa973cd4c4daa4f61eeb2bdbad27316534016f");
    }

    #[test]
    fn every_padding_length() {
        // 55, 56, 63, 64 and 65 bytes cross the one-block and two-block padding cases; the expected digests are
        // Python's hashlib.sha1(b"x" * n).hexdigest().
        let cases = [
            (1, "11f6ad8ec52a2984abaafd7c3b516503785c2072"),
            (55, "cef734ba81a024479e09eb5a75b6ddae62e6abf1"),
            (56, "901305367c259952f4e7af8323f480d59f81335b"),
            (57, "025ecbd5d70f8fb3c5457cd96bab13fda305dc59"),
            (63, "0ddc4e0cccd9a12850deb5abb0853a4425559fec"),
            (64, "bb2fa3ee7afb9f54c6dfb5d021f14b1ffe40c163"),
            (65, "78c741ddc482e4cdf8c474a0876347a0905b6233"),
            (119, "4300320394f7ee239bcdce7d3b8bcee173a0cd5c"),
            (120, "ceb2821639c4b6dcb10bce0e522ca2e608ce056d"),
            (128, "150fa3fbdc899bd0b8f95a9fb6027f564d953762"),
        ];
        for (n, want) in cases {
            assert_eq!(hex(&vec![b'x'; n]), want, "{n} bytes");
        }
    }

    #[test]
    fn non_ascii_text_hashes_its_utf8_bytes() {
        // Python: hashlib.sha1(BOUNDARY_TEXT.encode("utf-8")).hexdigest(), and _SETTLE_TEXT_H8.
        let boundary = "Recap: limit \u{2192} 3, the na\u{ef}ve cache is gone; the search suite passes, once again.";
        assert_eq!(hex(boundary.as_bytes()), "634a428a39a07b87c36f6bbc8ee7a35b745ece2c");
        assert_eq!(hex8(b"No response requested."), "d6202be5");
    }
}
