#!/usr/bin/env python3
"""Check that every crates.io package in rust/Cargo.lock is at least a week old (rust/SPIKE.md's dependency rule).

lczh's rule for new dependencies: pin each to a release published at least a week before it is added, since a freshly
published release is where a supply-chain compromise lands first. This reads Cargo.lock, asks crates.io when each
locked version was published (metadata only, nothing is downloaded), and exits non-zero naming any version younger
than --min-days, with the newest version that is old enough, for `cargo update -p NAME --precise VERSION`.

    python3 tools/rust-spike/check_crate_ages.py [rust/Cargo.lock] [--min-days 7] [--asof YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.request

UA = {"User-Agent": "romp-spike-dependency-check"}     # crates.io asks automated clients for a User-Agent


def locked_packages(path):
    """(name, version) for every package whose source is the crates.io registry."""
    out, cur = [], {}
    for line in open(path, encoding="utf-8").read().splitlines() + ["[[package]]"]:
        line = line.strip()
        if line == "[[package]]":
            if cur.get("source", "").startswith("registry+") and "name" in cur:
                out.append((cur["name"], cur["version"]))
            cur = {}
        elif " = " in line:
            k, v = line.split(" = ", 1)
            cur[k] = v.strip('"')
    return sorted(set(out))


def versions(name):
    req = urllib.request.Request("https://crates.io/api/v1/crates/%s/versions?per_page=100" % name, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["versions"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lock", nargs="?", default="rust/Cargo.lock")
    ap.add_argument("--min-days", type=int, default=7)
    ap.add_argument("--asof", help="the date the rule is checked against (default: today, UTC)")
    a = ap.parse_args()
    asof = dt.datetime.fromisoformat(a.asof).replace(tzinfo=dt.timezone.utc) if a.asof else dt.datetime.now(dt.timezone.utc)
    young = []
    for name, ver in locked_packages(a.lock):
        vs = versions(name)
        time.sleep(0.25)                                       # gentle on the API: one request per crate
        age = lambda v: (asof - dt.datetime.fromisoformat(v["created_at"].replace("Z", "+00:00"))).days
        mine = next((v for v in vs if v["num"] == ver), None)
        if mine is None:
            young.append((name, ver, None, "not found on crates.io"))
            continue
        days = age(mine)
        flag = "" if days >= a.min_days else "  <-- younger than %d days" % a.min_days
        print("%-20s %-10s %4d days%s" % (name, ver, days, flag))
        if days < a.min_days:
            major = ver.split(".")[0] if not ver.startswith("0.") else ".".join(ver.split(".")[:2])
            ok = next((v["num"] for v in vs if not v["yanked"] and "-" not in v["num"] and age(v) >= a.min_days
                       and (v["num"].split(".")[0] if not v["num"].startswith("0.") else ".".join(v["num"].split(".")[:2])) == major), None)
            young.append((name, ver, ok, "%d days old" % days))
    if young:
        print("\nToo young:")
        for name, ver, ok, why in young:
            print("  %s %s (%s); pin back with: cargo update -p %s@%s --precise %s" % (name, ver, why, name, ver, ok or "?"))
        sys.exit(1)
    print("\nall %d registry packages are at least %d days old" % (len(locked_packages(a.lock)), a.min_days))


if __name__ == "__main__":
    main()
