#!/usr/bin/env python3
"""Generate romp-json's golden tests from Python's own json.dumps (rust/SPIKE.md, step S1).

One corpus, defined here, is written out as a Rust test file: for every case, a Rust expression that builds the value
(floats by their exact bit pattern, strings by code point) and the exact text Python's json.dumps gives it with its
defaults. So the expected bytes come from Python itself and the two sides cannot drift apart. Re-run after changing
the corpus:

    python3 tools/rust-spike/gen_json_golden.py rust/crates/romp-json/tests/golden.rs

Backslashes are built from chr(92) throughout, so no editor or tool can turn an escape in this file into the
character it names.
"""
import json
import random
import struct
import sys

BS = chr(92)
I64_MIN, I64_MAX = -(2 ** 63), 2 ** 63 - 1


def rust_str(s):
    """A Rust string literal for `s`: printable ASCII as is (quote and backslash escaped), everything else by code
    point."""
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if ch == '"' or ch == BS:
            out.append(BS + ch)
        elif 0x20 <= cp <= 0x7E:
            out.append(ch)
        else:
            out.append("%su{%x}" % (BS, cp))
    out.append('"')
    return "".join(out)


def rust_value(v):
    if v is None:
        return "Value::Null"
    if v is True:
        return "Value::Bool(true)"
    if v is False:
        return "Value::Bool(false)"
    if isinstance(v, int):
        if I64_MIN <= v <= I64_MAX:
            return "Value::Int(%d)" % v
        return "Value::BigInt(%s.to_string())" % rust_str(str(v))
    if isinstance(v, float):
        bits = struct.unpack("<Q", struct.pack("<d", v))[0]
        return "Value::Float(f64::from_bits(0x%016x))" % bits
    if isinstance(v, str):
        return "Value::Str(%s.to_string())" % rust_str(v)
    if isinstance(v, list):
        return "Value::Array(vec![%s])" % ", ".join(rust_value(x) for x in v)
    if isinstance(v, dict):
        return "Value::Object(vec![%s])" % ", ".join(
            "(%s.to_string(), %s)" % (rust_str(k), rust_value(x)) for k, x in v.items())
    raise TypeError(type(v))


def raw_literal(text):
    """A Rust raw string literal holding `text` (json.dumps output with ensure_ascii is ASCII)."""
    hashes = "#"
    while ('"' + hashes) in text:
        hashes += "#"
    return 'r%s"%s"%s' % (hashes, text, hashes)


def corpus():
    cases = []
    add = lambda name, v: cases.append((name, v))

    add("null", None)
    add("true", True)
    add("false", False)
    for i, n in enumerate([0, 1, -1, 42, I64_MAX, I64_MIN, I64_MAX + 1, I64_MIN - 1, 10 ** 30, -(10 ** 40)]):
        add("int_%d" % i, n)

    tricky = [0.0, -0.0, 1.0, -1.0, 0.5, 0.1, 0.2, 0.1 + 0.2, 1 / 3, 2 / 3, 100.0, 1e15, 1e16, 1e17, 9999999999999998.0,
              1234567890123456.0, 12345678901234567.0, 0.001, 0.0001, 0.00001, 0.000123, 1.5e-7, 123456789.123,
              1e22, 1e100, 1e300, 1.7976931348623157e308, 2.2250738585072014e-308, 5e-324, 4.9e-324, 1e-320,
              float("inf"), float("-inf"), float("nan"), 3.14159, -2.5e-5, 6.02214076e23, 1e-4 - 1e-20]
    for i, f in enumerate(tricky):
        add("float_tricky_%d" % i, f)

    rng = random.Random(20261125)              # deterministic: the same corpus on every run
    for i in range(1500):                      # random bit patterns: every exponent, subnormals, NaN payloads
        add("float_bits_%d" % i, struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0])
    for i in range(1500):                      # values around the two notations' boundary
        add("float_scaled_%d" % i, rng.uniform(-10, 10) * 10 ** rng.randint(-8, 20))

    add("str_empty", "")
    add("str_ascii", "hello, world")
    add("str_quote_backslash", 'say "hi" ' + BS + " path" + BS + "x")
    add("str_controls", "".join(chr(c) for c in range(0x20)) + chr(0x7F))
    add("str_slash", "a/b</script>")
    add("str_latin_cjk", "café 日本語 Ωmega")
    add("str_astral", "emoji " + chr(0x1F600) + chr(0x1F680) + " math " + chr(0x1D400))
    add("str_bmp_edges", chr(0x80) + chr(0x7FF) + chr(0x800) + chr(0xFFFD) + chr(0xFFFF) + chr(0x10000) + chr(0x10FFFF))
    for i in range(200):                       # random strings over the whole code space minus lone surrogates
        chars = []
        for _ in range(rng.randint(0, 24)):
            cp = rng.choice([rng.randint(0, 0x7F), rng.randint(0x80, 0xD7FF), rng.randint(0xE000, 0x10FFFF)])
            chars.append(chr(cp))
        add("str_random_%d" % i, "".join(chars))

    add("array_empty", [])
    add("object_empty", {})
    add("nested", {"z": [1, 2.5, None, {"inner": [True, False, "x"]}], "a": {"b": {"c": []}}, "": "empty key"})
    add("order_kept", {"b": 1, "a": 2, "c": 3, "_": 4, "1": 5})
    add("feed_like", {"itemId": "s1:turn:7", "kind": "card", "title": "Fix the " + chr(0x201C) + "flaky" + chr(0x201D) +
                      " test", "t": 1795636070.123, "age": 0.0, "counts": {"turns": 12, "tools": 3}, "tags": ["web", "api"],
                      "cost": 0.000123, "live": False})
    return cases


def main(path):
    cases = corpus()
    lines = [
        "// GENERATED by tools/rust-spike/gen_json_golden.py from Python's json.dumps; do not edit by hand.",
        "// Python %s." % sys.version.split()[0],
        "use romp_json::{dumps, Value};",
        "",
        "#[test]",
        "fn every_golden_case_is_byte_identical_to_python() {",
        "    let cases: Vec<(&str, Value, &str)> = vec![",
    ]
    for name, v in cases:
        lines.append("        (%s, %s, %s)," % (rust_str(name), rust_value(v), raw_literal(json.dumps(v))))
    lines += [
        "    ];",
        "    let mut bad = Vec::new();",
        "    for (name, value, want) in &cases {",
        "        let got = dumps(value);",
        "        if got != *want {",
        "            bad.push(format!(\"{name}: got {got} want {want}\"));",
        "        }",
        "    }",
        "    assert!(bad.is_empty(), \"{} of {} cases differ:\\n{}\", bad.len(), cases.len(), bad.join(\"\\n\"));",
        "}",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("wrote %d cases to %s" % (len(cases), path))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "rust/crates/romp-json/tests/golden.rs")
