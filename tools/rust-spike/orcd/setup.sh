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
# Then, on the cluster. Every srun and sbatch below runs from the results' logs directory: each job script's --output is
# relative to the submit directory, and common.sh refuses a job submitted from anywhere else, so no log lands in $HOME.
#   cd "$(cat ~/orcd/scratch/agents/romp_fix/RESULTS)/logs"
#   O=~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd
#   srun --account=mit_general --partition=mit_normal --job-name=romp-fix-memo2-setup --ntasks=1 --cpus-per-task=8 \
#        --mem=16G --time=01:00:00 bash $O/setup.sh                     (on a compute node, never the login node)
# The jobs name the partitions ENGAGING.md assigns: mit_normal for the worlds and the measurement, which must not be cut,
# mit_preemptable for the restartable fuzz and mutant tasks.
#
# First a smoke pass, one or two tasks of each array (ENGAGING.md: test with 1-2 jobs before any sweep):
#   sbatch --array=0 $O/worlds.sbatch            the small world: a fresh dump and every comparison on it
#   sbatch --array=0,6 $O/fuzz.sbatch            layer 2's fuzz seed 1 and the memo's fuzz seed 1 (neither reads a dump)
#   sbatch --array=45,46 $O/mutants.sbatch       mutant 45 (the review's dedup regression, caught on the Mac by the
#                                                edge set and fuzz seed 1) and task 46, the unmutated control
# Check them on the pool before anything else is submitted:
#   - worlds/small, fuzz/layer2-seed1 and fuzz/memo-seed1: every row of steps.tsv has exit status 0 (its 2nd column), and
#     `(cd <dir> && sha256sum -c --quiet SHA256SUMS)` prints nothing;
#   - mutants/46.json says "controlPasses": true and mutants/45.json "status": "caught" (each with its .sha256 verifying);
#   - each smoke task's peak memory: `sacct -j <job> --format=JobID%20,JobName%28,MaxRSS,Elapsed,State`. Give each array's
#     remaining tasks about 1.5x its smoke task's MaxRSS with `--mem=` on the sbatch line (it overrides the script's
#     guess; keep it near a proportional share for the job's --cpus-per-task). The small world's peak does not bound the
#     large world's, so the worlds job keeps its 24G for the large tasks; measure.sbatch, which holds the large world in
#     one Python process as the large worlds task does, is sized from that task's MaxRSS once it has run.
# Then the rest:
#   W=$(sbatch --parsable --array=1-2 $O/worlds.sbatch)                       the large and large4 worlds
#   sbatch --mem=<from the smoke> --array=1-5,7-41%16 $O/fuzz.sbatch         every other fuzz seed
#   sbatch --mem=<from the smoke> --dependency=afterok:$W --array=42 $O/fuzz.sbatch   the store's fuzz (reads the dumps)
#   sbatch --mem=<from the smoke> --array=0-44%12 $O/mutants.sbatch          the other 45 mutants
#   sbatch --mem=<from the large worlds task> --dependency=afterok:$W $O/measure.sbatch
# Cancel by name if anything goes wrong: scancel -u "$USER" --name=romp-fix-memo2-<worlds|fuzz|mutants|measure>.
#
# What it does:
#   1. rustup 1.29.3, pinned: rustup-init for this machine's triple from the release archive
#      (static.rust-lang.org/rustup/archive/1.29.3/<triple>/), checked against the archive's published .sha256 and
#      against the SHA-256 pinned below for that triple (x86_64-unknown-linux-gnu's taken from the archive on 2026-11-26;
#      another triple needs RUSTUP_INIT_SHA256 set, or setup refuses); installed into scratch (RUSTUP_HOME, CARGO_HOME
#      under ~/orcd/scratch/agents/romp_fix), no PATH or profile edits.
#   2. Rust 1.100.0 (rust-toolchain.toml's pin), minimal profile: rustup checks every component's SHA-256 against the
#      channel manifest as it installs it; the versions are then checked by name.
#   3. The vendored crates: every crate Cargo.lock names must be in ../vendor with its .cargo-checksum.json (cargo checks
#      each file against it, and the crate against Cargo.lock's checksum, at every build); CARGO_HOME/config.toml
#      replaces crates.io with that directory and turns the network off, so every build is offline and --locked.
#   4. Python: ROMP_FIX_PYTHON when set; else CPython 3.12.11 exactly, in scratch, made with lczh's existing
#      ~/miniforge3 conda (read-only use: the package cache is redirected to scratch and the base environment is
#      untouched; conda checks each package's hash from the channel's repodata; HOME points at scratch for the conda
#      calls, so conda's registration of the new prefix in ~/.conda/environments.txt lands there, not in $HOME). 3.12.11
#      because the port's regex and str.upper tables (pyre_tables.rs, pystr_tables.rs) and every committed fixture were
#      generated under it, with Unicode 15.0.0; a later Python's Unicode tables could turn a cluster difference into an
#      environment artifact. Its Unicode version is checked against the tables' here and by every correctness job
#      (common.sh's need_tables_python); its exact spec is recorded.
#   5. The release build of romp-spike (LTO, one codegen unit: rust/Cargo.toml) and the workspace's tests, once, logged.
# Results: <RESULTS>/setup/ (versions, the conda spec, the test log, checksums).
source "$(dirname "$(realpath "$0")")/common.sh"

RUSTUP_VERSION=1.29.3
RUST_VERSION=1.100.0
PY_VERSION=3.12.11
OUT="$RESULTS/setup"
mkdir -p "$OUT" "$CARGO_HOME" "$RUSTUP_HOME"
: > "$OUT/steps.tsv"
[ -n "${SLURM_JOB_ID:-}" ] || die "run this on a compute node through srun (see the header), not on a login node"

# 1. rustup
triple="$(uname -m)-unknown-linux-gnu"
case "$triple" in
    # rustup-init 1.29.3's published SHA-256 (static.rust-lang.org/rustup/archive/1.29.3/<triple>/rustup-init.sha256)
    x86_64-unknown-linux-gnu) pinned=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55 ;;
    *) pinned="" ;;
esac
RUSTUP_INIT_SHA256="${RUSTUP_INIT_SHA256:-$pinned}"
if ! "$CARGO_HOME/bin/rustup" --version 2>/dev/null | grep -q "^rustup $RUSTUP_VERSION "; then
    [ -n "$RUSTUP_INIT_SHA256" ] || die "no pinned rustup-init SHA-256 for $triple: set RUSTUP_INIT_SHA256 (from the archive)"
    dl="$TMPDIR/rustup-init-$RUSTUP_VERSION"
    mkdir -p "$dl"
    base="https://static.rust-lang.org/rustup/archive/$RUSTUP_VERSION/$triple"
    curl --proto '=https' --tlsv1.2 -fsSL "$base/rustup-init" -o "$dl/rustup-init"
    curl --proto '=https' --tlsv1.2 -fsSL "$base/rustup-init.sha256" -o "$dl/rustup-init.sha256"
    published="$(awk '{print $1}' "$dl/rustup-init.sha256")"
    got="$(sha256sum "$dl/rustup-init" | awk '{print $1}')"
    [ "$got" = "$published" ] || die "rustup-init's SHA-256 $got is not the archive's published $published"
    [ "$got" = "$RUSTUP_INIT_SHA256" ] || die "rustup-init's SHA-256 $got is not the pinned $RUSTUP_INIT_SHA256"
    echo "rustup-init $RUSTUP_VERSION ($triple) sha256 $got (matches the published .sha256 and the pin)" \
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
CONDA="$HOME/miniforge3/bin/conda"
mkdir -p "$ROOT/home"
if [ -z "${ROMP_FIX_PYTHON:-}" ] && [ ! -x "$PYTHON" ]; then
    [ -x "$CONDA" ] || die "no Python: set ROMP_FIX_PYTHON to a CPython $PY_VERSION (no $CONDA to make one)"
    HOME="$ROOT/home" CONDA_PKGS_DIRS="$ROOT/conda-pkgs" "$CONDA" create -y -p "$ROOT/py" --override-channels \
        -c conda-forge "python=$PY_VERSION"
fi
"$PYTHON" -c 'import platform, unicodedata; print("python", platform.python_version(), "unicode", unicodedata.unidata_version)' \
    | tee -a "$OUT/versions.txt"
if [ -z "${ROMP_FIX_PYTHON:-}" ]; then
    grep -q "^python $PY_VERSION " "$OUT/versions.txt" \
        || die "$PYTHON is not CPython $PY_VERSION (an older environment there? remove $ROOT/py and run setup again)"
fi
need_tables_python
if [ -x "$CONDA" ] && [ -d "$ROOT/py/conda-meta" ]; then
    HOME="$ROOT/home" "$CONDA" list -p "$ROOT/py" --explicit --md5 > "$OUT/python-conda-spec.txt"
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
