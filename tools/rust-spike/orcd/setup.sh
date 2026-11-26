#!/bin/bash
# The romp_fix toolchain and build on MIT Engaging (ORCD), for the Rust spike's heavy checks (rust/SPIKE.md, step S6.1;
# ~/manager/ENGAGING.md). Idempotent: each part is skipped when already in place, and checked either way.
#
# Before it, from the Mac (the main session, after lczh opens the Duo connection; light, no build), with CLUSTER the ssh
# alias ENGAGING.md names:
#   cd <worktree>/rust && ~/.cargo/bin/cargo vendor --offline --locked --versioned-dirs /tmp/romp-fix-vendor
#   rsync -a --delete /tmp/romp-fix-vendor/ CLUSTER:orcd/scratch/agents/romp_fix/vendor/
#   rsync -a --delete --exclude .git --exclude rust/target <worktree>/ CLUSTER:orcd/scratch/agents/romp_fix/romp/
#   git -C <worktree> rev-parse HEAD | ssh CLUSTER 'cat > orcd/scratch/agents/romp_fix/REVISION'
#   echo <the pool's agent-runs/romp_fix, ENGAGING.md> | ssh CLUSTER 'cat > orcd/scratch/agents/romp_fix/RESULTS'
#   rm -rf /tmp/romp-fix-vendor
# Then, on the cluster, on a compute node (never the login node), from the results' logs directory:
#   cd "$(cat ~/orcd/scratch/agents/romp_fix/RESULTS)/logs"
#   srun --account=mit_general --partition=mit_normal --job-name=romp-fix-memo2-setup --ntasks=1 --cpus-per-task=8 \
#        --mem=16G --time=01:00:00 bash ~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd/setup.sh
# and after it, in this order (the jobs name the partitions ENGAGING.md assigns: mit_normal for the worlds and the
# measurement, which must not be cut, mit_preemptable for the restartable fuzz and mutant tasks):
#   W=$(sbatch --parsable .../worlds.sbatch)
#   sbatch --dependency=afterok:$W .../fuzz.sbatch      (its store task reads the worlds' dumps)
#   sbatch .../mutants.sbatch
#   sbatch --dependency=afterok:$W .../measure.sbatch
#
# What it does:
#   1. rustup 1.29.3, pinned: rustup-init for this machine's triple from the release archive
#      (static.rust-lang.org/rustup/archive/1.29.3/<triple>/), checked against the archive's published .sha256 and,
#      when RUSTUP_INIT_SHA256 is set below or in the environment, against that pin too; installed into scratch
#      (RUSTUP_HOME, CARGO_HOME under ~/orcd/scratch/agents/romp_fix), no PATH or profile edits.
#   2. Rust 1.100.0 (rust-toolchain.toml's pin), minimal profile: rustup checks every component's SHA-256 against the
#      channel manifest as it installs it; the versions are then checked by name.
#   3. The vendored crates: every crate Cargo.lock names must be in ../vendor with its .cargo-checksum.json (cargo checks
#      each file against it, and the crate against Cargo.lock's checksum, at every build); CARGO_HOME/config.toml
#      replaces crates.io with that directory and turns the network off, so every build is offline and --locked.
#   4. Python: ROMP_FIX_PYTHON when set; else a CPython 3.13 environment in scratch made with lczh's existing
#      ~/miniforge3 conda (read-only use: the package cache is redirected to scratch, the base environment is untouched;
#      conda checks each package's hash from the channel's repodata); its exact spec is recorded.
#   5. The release build of romp-spike (LTO, one codegen unit: rust/Cargo.toml) and the workspace's tests, once, logged.
# Results: <RESULTS>/setup/ (versions, the conda spec, the test log, checksums).
source "$(dirname "$(realpath "$0")")/common.sh"

RUSTUP_VERSION=1.29.3
RUST_VERSION=1.100.0
RUSTUP_INIT_SHA256="${RUSTUP_INIT_SHA256:-}"     # pin it here after the first verified run (setup prints it)
OUT="$RESULTS/setup"
mkdir -p "$OUT" "$CARGO_HOME" "$RUSTUP_HOME"
: > "$OUT/steps.tsv"
[ -n "${SLURM_JOB_ID:-}" ] || die "run this on a compute node through srun (see the header), not on a login node"

# 1. rustup
triple="$(uname -m)-unknown-linux-gnu"
if ! "$CARGO_HOME/bin/rustup" --version 2>/dev/null | grep -q "^rustup $RUSTUP_VERSION "; then
    dl="$TMPDIR/rustup-init-$RUSTUP_VERSION"
    mkdir -p "$dl"
    base="https://static.rust-lang.org/rustup/archive/$RUSTUP_VERSION/$triple"
    curl --proto '=https' --tlsv1.2 -fsSL "$base/rustup-init" -o "$dl/rustup-init"
    curl --proto '=https' --tlsv1.2 -fsSL "$base/rustup-init.sha256" -o "$dl/rustup-init.sha256"
    published="$(awk '{print $1}' "$dl/rustup-init.sha256")"
    got="$(sha256sum "$dl/rustup-init" | awk '{print $1}')"
    [ "$got" = "$published" ] || die "rustup-init's SHA-256 $got is not the archive's published $published"
    if [ -n "$RUSTUP_INIT_SHA256" ] && [ "$got" != "$RUSTUP_INIT_SHA256" ]; then
        die "rustup-init's SHA-256 $got is not the pinned $RUSTUP_INIT_SHA256"
    fi
    echo "rustup-init $RUSTUP_VERSION ($triple) sha256 $got (matches the published .sha256${RUSTUP_INIT_SHA256:+ and the pin})" \
        | tee "$OUT/rustup-init.sha256.txt"
    chmod +x "$dl/rustup-init"
    "$dl/rustup-init" -y --no-modify-path --profile minimal --default-toolchain none --no-update-default-toolchain
    rm -rf "$dl"
fi
"$CARGO_HOME/bin/rustup" --version 2>/dev/null | grep -q "^rustup $RUSTUP_VERSION " || die "rustup is not $RUSTUP_VERSION"

# 2. the toolchain
rustup toolchain install "$RUST_VERSION" --profile minimal --no-self-update
(cd "$CODE/rust" && rustc --version && cargo --version) | tee "$OUT/versions.txt"
grep -q "^rustc $RUST_VERSION " "$OUT/versions.txt" || die "rustc is not $RUST_VERSION in $CODE/rust"
grep -q "^cargo $RUST_VERSION " "$OUT/versions.txt" || die "cargo is not $RUST_VERSION in $CODE/rust"
rustup --version 2>/dev/null >> "$OUT/versions.txt"

# 3. the vendored crates
VENDOR="$ROOT/vendor"
[ -d "$VENDOR" ] || die "no $VENDOR: vendor the crates on the Mac and rsync them (see the header)"
missing=0
while read -r name version; do
    if [ ! -f "$VENDOR/$name-$version/.cargo-checksum.json" ]; then
        echo "missing vendored crate $name $version" >&2
        missing=1
    fi
done < <(awk '/^\[\[package\]\]/{n="";v="";s=0} /^name = /{gsub(/"/,"",$3);n=$3} /^version = /{gsub(/"/,"",$3);v=$3}
              /^source = "registry/{print n, v}' "$CODE/rust/Cargo.lock")
[ "$missing" = 0 ] || die "the vendored crates do not cover Cargo.lock"
cat > "$CARGO_HOME/config.toml" <<EOF
# written by tools/rust-spike/orcd/setup.sh: every build reads the vendored crates, offline
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "$VENDOR"

[net]
offline = true
EOF
ls "$VENDOR" > "$OUT/vendored-crates.txt"

# 4. Python
if [ -z "${ROMP_FIX_PYTHON:-}" ] && [ ! -x "$PYTHON" ]; then
    CONDA="$HOME/miniforge3/bin/conda"
    [ -x "$CONDA" ] || die "no Python: set ROMP_FIX_PYTHON to a CPython 3.12+ (no $CONDA to make one)"
    CONDA_PKGS_DIRS="$ROOT/conda-pkgs" "$CONDA" create -y -p "$ROOT/py" --override-channels -c conda-forge "python=3.13"
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), sys.version; print("python", sys.version.split()[0])' \
    | tee -a "$OUT/versions.txt"
if [ -x "$HOME/miniforge3/bin/conda" ] && [ -d "$ROOT/py/conda-meta" ]; then
    "$HOME/miniforge3/bin/conda" list -p "$ROOT/py" --explicit --md5 > "$OUT/python-conda-spec.txt"
fi

# 5. the build and the tests
cd "$CODE/rust"
run cargo-build cargo build --release --offline --locked -j "$JOBS" -p romp-spike
run cargo-test cargo test --offline --locked -j "$JOBS" -- --test-threads="$JOBS"
grep -q $'^cargo-build\t0\t' "$OUT/steps.tsv" || die "the release build failed (see $OUT/cargo-build.txt)"
grep -q $'^cargo-test\t0\t' "$OUT/steps.tsv" || die "the tests failed (see $OUT/cargo-test.txt)"
sha256sum "$BIN" | tee "$OUT/romp-spike.sha256.txt"
echo "revision $REVISION" >> "$OUT/versions.txt"
uname -a >> "$OUT/versions.txt"
seal
