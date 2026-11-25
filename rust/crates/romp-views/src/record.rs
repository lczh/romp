//! The machinery under the typed structs: Python's int/float distinction (`Num`), insertion-ordered maps (`OMap`), the
//! conversion to romp-json's `Value` (`ToJson`), and `record!`, which declares a struct whose fields are typed, whose
//! unknown keys are kept aside, and which remembers the order its keys came in.
//!
//! Why the order: `snapshot.json` is written in insertion order, and a store's keys come in an order that depends on
//! its history (in both worlds s004's never-planned store has `placementsV` last, the planned ones have it third). A
//! struct with fixed field order could not re-serialize those bytes, so the round trip (load, then write through
//! romp-json, then compare with the file) would prove nothing. Each record keeps one byte per key: an index into its
//! typed keys, or past them, into its untyped ones.
//!
//! Absent and null are different in Python (`d.get(k, default)` returns the default only for an absent key), so a field
//! is `Option<T>` for absent or present, and a key that may hold null is declared `Option<X>`, stored
//! `Option<Option<X>>`.

use std::borrow::Cow;
use std::fmt;

use romp_json::Value as Out;
use serde::de::{self, Deserialize, Deserializer, MapAccess, Visitor};

/// A JSON number with Python's int/float distinction kept: `5` and `5.0` print differently (the alive rows' `mtime`
/// is the float `1781099940.0`), so a number the build passes through must carry its tag.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    /// Python's truthiness: 0 and 0.0 are false.
    pub fn truthy(self) -> bool {
        match self {
            Num::Int(i) => i != 0,
            Num::Float(f) => f != 0.0,
        }
    }

    pub fn as_f64(self) -> f64 {
        match self {
            Num::Int(i) => i as f64,
            Num::Float(f) => f,
        }
    }
}

impl<'de> Deserialize<'de> for Num {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl Visitor<'_> for V {
            type Value = Num;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a number")
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Num, E> {
                Ok(Num::Int(v))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Num, E> {
                i64::try_from(v).map(Num::Int).map_err(|_| E::custom(format!("integer {v} is past i64")))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Num, E> {
                Ok(Num::Float(v))
            }
        }
        d.deserialize_any(V)
    }
}

/// A value written as Python's `json.dumps` would write it, through romp-json.
pub trait ToJson {
    fn to_json(&self) -> Out;
}

impl ToJson for Num {
    fn to_json(&self) -> Out {
        match *self {
            Num::Int(i) => Out::Int(i),
            Num::Float(f) => Out::Float(f),
        }
    }
}

impl ToJson for String {
    fn to_json(&self) -> Out {
        Out::Str(self.clone())
    }
}

impl ToJson for bool {
    fn to_json(&self) -> Out {
        Out::Bool(*self)
    }
}

impl<T: ToJson> ToJson for Option<T> {
    fn to_json(&self) -> Out {
        self.as_ref().map_or(Out::Null, ToJson::to_json)
    }
}

impl<T: ToJson> ToJson for Vec<T> {
    fn to_json(&self) -> Out {
        Out::Array(self.iter().map(ToJson::to_json).collect())
    }
}

impl<A: ToJson, B: ToJson> ToJson for (A, B) {
    fn to_json(&self) -> Out {
        Out::Array(vec![self.0.to_json(), self.1.to_json()])
    }
}

impl ToJson for serde_json::Value {
    fn to_json(&self) -> Out {
        use serde_json::Value as J;
        match self {
            J::Null => Out::Null,
            J::Bool(b) => Out::Bool(*b),
            J::Number(n) => {
                if let Some(i) = n.as_i64() {
                    Out::Int(i)
                } else if let Some(u) = n.as_u64() {
                    Out::BigInt(u.to_string())
                } else {
                    Out::Float(n.as_f64().expect("a JSON number is an int or a float"))
                }
            }
            J::String(s) => Out::Str(s.clone()),
            J::Array(items) => Out::Array(items.iter().map(ToJson::to_json).collect()),
            J::Object(m) => Out::Object(m.iter().map(|(k, v)| (k.clone(), v.to_json())).collect()),
        }
    }
}

/// Python's truthiness of a JSON value: null, false, 0, 0.0, "", [] and {} are false.
pub fn py_truthy(v: &serde_json::Value) -> bool {
    use serde_json::Value as J;
    match v {
        J::Null => false,
        J::Bool(b) => *b,
        J::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        J::String(s) => !s.is_empty(),
        J::Array(a) => !a.is_empty(),
        J::Object(m) => !m.is_empty(),
    }
}

/// `json.dumps(v, sort_keys=True)`'s key order: every object's members sorted by key (code point order, which is
/// UTF-8 byte order), recursively.
pub fn canon(v: &Out) -> Out {
    match v {
        Out::Array(items) => Out::Array(items.iter().map(canon).collect()),
        Out::Object(members) => {
            let mut m: Vec<(String, Out)> = members.iter().map(|(k, v)| (k.clone(), canon(v))).collect();
            m.sort_by(|a, b| a.0.cmp(&b.0));
            Out::Object(m)
        }
        other => other.clone(),
    }
}

/// An object key, borrowed from the input when it holds no escapes.
pub struct Key<'de>(Cow<'de, str>);

impl Key<'_> {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for Key<'de> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Key<'de>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("an object key")
            }
            fn visit_borrowed_str<E: de::Error>(self, v: &'de str) -> Result<Key<'de>, E> {
                Ok(Key(Cow::Borrowed(v)))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Key<'de>, E> {
                Ok(Key(Cow::Owned(v.to_string())))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Key<'de>, E> {
                Ok(Key(Cow::Owned(v)))
            }
        }
        d.deserialize_str(V)
    }
}

/// An error inside `record.key`, prefixed with that path. serde_json appends " at line L column C" to an error each
/// time it crosses an object, so the inner position is dropped here and the outermost one is the one the message keeps.
pub fn at_key(record: &str, key: &str, e: &dyn fmt::Display) -> String {
    let msg = e.to_string();
    let inner = match msg.rfind(" at line ") {
        Some(i) if msg[i + 9..].split(" column ").all(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit())) => {
            &msg[..i]
        }
        _ => &msg[..],
    };
    format!("{record}.{key}: {inner}")
}

/// A JSON object with its insertion order kept, as a Python dict keeps it (a store's nodes are built into children in
/// store order). A duplicate key is refused: Python keeps the last one, a list would keep both.
#[derive(Debug, Clone, PartialEq)]
pub struct OMap<T>(pub Vec<(String, T)>);

impl<T> Default for OMap<T> {
    fn default() -> Self {
        OMap(Vec::new())
    }
}

impl<T> OMap<T> {
    pub fn get(&self, key: &str) -> Option<&T> {
        self.0.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&String, &T)> {
        self.0.iter().map(|(k, v)| (k, v))
    }

    pub fn keys(&self) -> impl Iterator<Item = &String> {
        self.0.iter().map(|(k, _)| k)
    }

    pub fn values(&self) -> impl Iterator<Item = &T> {
        self.0.iter().map(|(_, v)| v)
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl<'de, T: Deserialize<'de>> Deserialize<'de> for OMap<T> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V<T>(std::marker::PhantomData<T>);
        impl<'de, T: Deserialize<'de>> Visitor<'de> for V<T> {
            type Value = OMap<T>;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a JSON object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<OMap<T>, A::Error> {
                let mut out: Vec<(String, T)> = Vec::new();
                let mut seen = std::collections::HashSet::new();
                while let Some(k) = m.next_key::<Key<'de>>()? {
                    let k = k.0.into_owned();
                    if !seen.insert(k.clone()) {
                        return Err(de::Error::custom(format!("duplicate key {k:?}")));
                    }
                    let v = m.next_value::<T>()?;
                    out.push((k, v));
                }
                Ok(OMap(out))
            }
        }
        d.deserialize_map(V(std::marker::PhantomData))
    }
}

impl<T: ToJson> ToJson for OMap<T> {
    fn to_json(&self) -> Out {
        Out::Object(self.0.iter().map(|(k, v)| (k.clone(), v.to_json())).collect())
    }
}

/// Declare a typed record: `field: Type = "jsonKey"`, every field `Option<Type>` (absent or present), unknown keys kept
/// in `extra`, the key order in `order`, and an optional `required: [field, ...]` list whose absence is an error.
/// `UNPORTED` (in `validate`, per record) lists keys the per-card code reads that the record does not type yet.
macro_rules! record {
    (
        $(#[$meta:meta])*
        pub struct $name:ident {
            $( $(#[$fmeta:meta])* $field:ident : $ty:ty = $key:literal ),+ $(,)?
        }
        $(required: [$($req:ident),* $(,)?])?
    ) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Default, PartialEq)]
        pub struct $name {
            $( $(#[$fmeta])* pub $field: ::core::option::Option<$ty>, )+
            /// The keys this record does not type, in file order: kept for the round trip, never read by the port.
            pub extra: ::std::vec::Vec<(::std::string::String, ::serde_json::Value)>,
            /// The order the keys came in: `i < KEYS.len()` is `KEYS[i]`, past it `extra[i - KEYS.len()]`.
            pub order: ::std::vec::Vec<u8>,
        }

        impl $name {
            /// The JSON keys this record types, in declaration order.
            pub const KEYS: &'static [&'static str] = &[$($key),+];
            const FIELDS: &'static [&'static str] = &[$(stringify!($field)),+];

            /// Read the record from a JSON object's members.
            pub fn from_map<'de, A: ::serde::de::MapAccess<'de>>(mut m: A) -> ::core::result::Result<Self, A::Error> {
                use ::serde::de::Error as _;
                let mut out = Self::default();
                while let Some(k) = m.next_key::<$crate::record::Key<'de>>()? {
                    let k = k.as_str();
                    match k {
                        $( $key => {
                            if out.$field.is_some() {
                                return Err(A::Error::custom(format!("duplicate key {:?} in {}", $key,
                                                                    stringify!($name))));
                            }
                            out.$field = Some(m.next_value::<$ty>().map_err(|e| A::Error::custom(
                                $crate::record::at_key(stringify!($name), $key, &e)))?);
                            let i = Self::KEYS.iter().position(|x| *x == $key).expect("a declared key");
                            out.order.push(i as u8);
                        } )+
                        _ => {
                            if out.extra.iter().any(|(e, _)| e == k) {
                                return Err(A::Error::custom(format!("duplicate key {:?} in {}", k,
                                                                    stringify!($name))));
                            }
                            let i = Self::KEYS.len() + out.extra.len();
                            if i > u8::MAX as usize {
                                return Err(A::Error::custom(format!("{} has more than 255 keys", stringify!($name))));
                            }
                            let v = m.next_value::<::serde_json::Value>()?;
                            out.extra.push((k.to_string(), v));
                            out.order.push(i as u8);
                        }
                    }
                }
                $($(
                    if out.$req.is_none() {
                        return Err(A::Error::custom(format!("{} has no {:?} key", stringify!($name),
                                                            Self::key_of(stringify!($req)))));
                    }
                )*)?
                Ok(out)
            }

            #[allow(dead_code)]
            fn key_of(field: &str) -> &'static str {
                Self::KEYS[Self::FIELDS.iter().position(|f| *f == field).expect("a declared field")]
            }

            /// The JSON a key holds, typed or untyped; None when the key is absent (Python's `d.get(key)` without
            /// its None-for-null collapse: a present null is `Some(Null)`).
            pub fn get_json(&self, key: &str) -> ::core::option::Option<::romp_json::Value> {
                match key {
                    $( $key => self.$field.as_ref().map($crate::record::ToJson::to_json), )+
                    _ => self.extra.iter().find(|(k, _)| k == key).map(|(_, v)| $crate::record::ToJson::to_json(v)),
                }
            }

            /// An untyped key's raw value.
            #[allow(dead_code)]
            pub fn extra_get(&self, key: &str) -> ::core::option::Option<&::serde_json::Value> {
                self.extra.iter().find(|(k, _)| k == key).map(|(_, v)| v)
            }

            /// The first untyped key that is in `keys` (the per-card code reads it, so a record carrying it would
            /// be built from facts the port ignores).
            #[allow(dead_code)]
            pub fn unported_key(&self, keys: &[&str]) -> ::core::option::Option<&str> {
                self.extra.iter().map(|(k, _)| k.as_str()).find(|k| keys.contains(k))
            }
        }

        impl<'de> ::serde::Deserialize<'de> for $name {
            fn deserialize<D: ::serde::Deserializer<'de>>(d: D) -> ::core::result::Result<Self, D::Error> {
                struct V;
                impl<'de> ::serde::de::Visitor<'de> for V {
                    type Value = $name;
                    fn expecting(&self, f: &mut ::std::fmt::Formatter) -> ::std::fmt::Result {
                        f.write_str(concat!("a JSON object (", stringify!($name), ")"))
                    }
                    fn visit_map<A: ::serde::de::MapAccess<'de>>(self, m: A)
                        -> ::core::result::Result<$name, A::Error> {
                        $name::from_map(m)
                    }
                }
                d.deserialize_map(V)
            }
        }

        impl $crate::record::ToJson for $name {
            fn to_json(&self) -> ::romp_json::Value {
                let mut members = ::std::vec::Vec::with_capacity(self.order.len());
                for &i in &self.order {
                    let i = i as usize;
                    if i < Self::KEYS.len() {
                        let key = Self::KEYS[i];
                        members.push((key.to_string(), self.get_json(key).expect("an ordered key is set")));
                    } else {
                        let (k, v) = &self.extra[i - Self::KEYS.len()];
                        members.push((k.clone(), $crate::record::ToJson::to_json(v)));
                    }
                }
                ::romp_json::Value::Object(members)
            }
        }
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    record! {
        /// A record for the machinery's own tests.
        pub struct Probe {
            name: String = "name",
            count: Num = "count",
            maybe: Option<String> = "maybe",
        }
        required: [name]
    }

    #[test]
    fn key_order_and_untyped_keys_survive_the_round_trip() {
        let text = r#"{"zeta": [1, 2.5], "count": 3.0, "name": "x", "maybe": null, "alpha": {"b": 1, "a": 2}}"#;
        let p: Probe = serde_json::from_str(text).unwrap();
        assert_eq!(p.name.as_deref(), Some("x"));
        assert_eq!(p.count, Some(Num::Float(3.0)));
        assert_eq!(p.maybe, Some(None));
        assert_eq!(p.extra.len(), 2);
        assert_eq!(romp_json::dumps(&p.to_json()), text);
    }

    #[test]
    fn absent_and_null_are_different() {
        let a: Probe = serde_json::from_str(r#"{"name": "x"}"#).unwrap();
        let b: Probe = serde_json::from_str(r#"{"name": "x", "maybe": null}"#).unwrap();
        assert_eq!(a.maybe, None);
        assert_eq!(b.maybe, Some(None));
        assert_eq!(a.get_json("maybe"), None);
        assert_eq!(b.get_json("maybe"), Some(Out::Null));
    }

    #[test]
    fn wrong_shapes_fail_loudly() {
        let missing = serde_json::from_str::<Probe>(r#"{"count": 1}"#).unwrap_err().to_string();
        assert!(missing.contains("Probe has no \"name\" key"), "{missing}");
        let typed = serde_json::from_str::<Probe>(r#"{"name": 5}"#).unwrap_err().to_string();
        assert!(typed.contains("Probe.name: invalid type: integer `5`, expected a string"), "{typed}");
        let null = serde_json::from_str::<Probe>(r#"{"name": null}"#).unwrap_err().to_string();
        assert!(null.contains("Probe.name: invalid type: null, expected a string"), "{null}");
        let dup = serde_json::from_str::<Probe>(r#"{"name": "a", "name": "b"}"#).unwrap_err().to_string();
        assert!(dup.contains("duplicate key \"name\" in Probe"), "{dup}");
        let big = serde_json::from_str::<Probe>(r#"{"name": "a", "count": 18446744073709551615}"#)
            .unwrap_err()
            .to_string();
        assert!(big.contains("integer 18446744073709551615 is past i64"), "{big}");
    }

    #[test]
    fn floats_parse_as_python_wrote_them() {
        // Python reprs that serde_json's default parser reads one ULP off (on 2026-11-25, 14,434 of 100,000 times
        // drawn below 2e9); the workspace enables its `float_roundtrip` feature, which reads all of them exactly.
        for text in ["-1.5432835417340557e+88", "-5.795503248498993e-228", "-5.988180159386011e+243",
                     "-1.306052388527796e-147", "-8.441872792105456e-239", "1781099940.0", "0.30000000000000004",
                     "5e-324", "1.7976931348623157e+308", "1e+23"] {
            let n: Num = serde_json::from_str(text).unwrap();
            assert_eq!(romp_json::dumps(&n.to_json()), text);
        }
    }

    #[test]
    fn omap_keeps_order_and_refuses_duplicates() {
        let m: OMap<Num> = serde_json::from_str(r#"{"b": 1, "a": 2.0}"#).unwrap();
        assert_eq!(m.keys().map(String::as_str).collect::<Vec<_>>(), ["b", "a"]);
        assert_eq!(romp_json::dumps(&m.to_json()), r#"{"b": 1, "a": 2.0}"#);
        let dup = serde_json::from_str::<OMap<Num>>(r#"{"a": 1, "a": 2}"#).unwrap_err().to_string();
        assert!(dup.contains("duplicate key \"a\""), "{dup}");
    }

    #[test]
    fn truthiness_is_pythons() {
        for (text, want) in [("null", false), ("false", false), ("0", false), ("0.0", false), ("\"\"", false),
                             ("[]", false), ("{}", false), ("1", true), ("-0.5", true), ("\" \"", true),
                             ("[0]", true), ("{\"a\": null}", true), ("true", true)] {
            let v: serde_json::Value = serde_json::from_str(text).unwrap();
            assert_eq!(py_truthy(&v), want, "{text}");
        }
    }

    #[test]
    fn canon_sorts_every_level() {
        let v: serde_json::Value = serde_json::from_str(r#"{"b": [{"z": 1, "y": 2}], "a": null}"#).unwrap();
        assert_eq!(romp_json::dumps(&canon(&v.to_json())), r#"{"a": null, "b": [{"y": 2, "z": 1}]}"#);
    }
}
