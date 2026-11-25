//! zlib's CRC-32 (`zlib.crc32`), written by hand: the reflected IEEE polynomial 0xEDB88320, initial value and final
//! XOR of all ones, and a running value that continues across calls exactly as `zlib.crc32(data, value)` does. The
//! store keeps it over the bytes it holds so a same-size file under a new mtime is proved byte for byte (Python's
//! `_held_crc`). Slicing by 8 (eight tables of 256 entries, built at compile time) reads a word per step.

const fn tables() -> [[u32; 256]; 8] {
    let mut t = [[0u32; 256]; 8];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { 0xEDB8_8320 ^ (c >> 1) } else { c >> 1 };
            k += 1;
        }
        t[0][i] = c;
        i += 1;
    }
    let mut i = 0;
    while i < 256 {
        let mut s = 1;
        while s < 8 {
            let prev = t[s - 1][i];
            t[s][i] = (prev >> 8) ^ t[0][(prev & 0xff) as usize];
            s += 1;
        }
        i += 1;
    }
    t
}

static T: [[u32; 256]; 8] = tables();

/// `zlib.crc32(data, crc)`: the running CRC-32 of everything before `data` (0 to start) extended over `data`.
pub fn update(crc: u32, data: &[u8]) -> u32 {
    let mut c = !crc;
    let mut chunks = data.chunks_exact(8);
    for w in &mut chunks {
        let lo = c ^ u32::from_le_bytes([w[0], w[1], w[2], w[3]]);
        c = T[7][(lo & 0xff) as usize]
            ^ T[6][((lo >> 8) & 0xff) as usize]
            ^ T[5][((lo >> 16) & 0xff) as usize]
            ^ T[4][(lo >> 24) as usize]
            ^ T[3][w[4] as usize]
            ^ T[2][w[5] as usize]
            ^ T[1][w[6] as usize]
            ^ T[0][w[7] as usize];
    }
    for &b in chunks.remainder() {
        c = T[0][((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    !c
}

#[cfg(test)]
mod tests {
    use super::update;

    #[test]
    fn matches_zlib() {
        // Values from Python's zlib.crc32 (3.12.11).
        assert_eq!(update(0, b""), 0);
        assert_eq!(update(0, b"a"), 0xe8b7be43);
        assert_eq!(update(0, b"123456789"), 0xcbf43926);
        assert_eq!(update(0, b"The quick brown fox jumps over the lazy dog"), 0x414fa339);
        let all: Vec<u8> = (0..=255u8).collect();
        assert_eq!(update(0, &all), 0x29058c73);
    }

    #[test]
    fn running_value_continues_like_zlib() {
        let data = b"{\"type\": \"user\"}\n{\"type\": \"assistant\"}\n";
        let whole = update(0, data);
        for cut in 0..=data.len() {
            assert_eq!(update(update(0, &data[..cut]), &data[cut..]), whole, "cut at {cut}");
        }
    }
}
