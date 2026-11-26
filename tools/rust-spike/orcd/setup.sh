#!/bin/bash
# The romp_fix toolchain and build on MIT Engaging (ORCD), for the Rust spike's heavy checks (rust/SPIKE.md, step S6.1;
# ~/manager/ENGAGING.md). Idempotent: each part is skipped when already in place, and checked either way.
#
# Before it, from the Mac (the main session, after lczh opens the Duo connection; light, no build), with CLUSTER the ssh
# alias ENGAGING.md names and A the worktree. The code goes over as ONE commit's tracked files (git archive: no .git, no
# untracked or ignored file, no local edit), and REVISION names exactly that commit:
#   [ -z "$(git -C A status --porcelain)" ] || { echo "A has local changes: commit them or stash them first"; false; }
#   REV=$(git -C A rev-parse HEAD)
#   cd A/rust && ~/.cargo/bin/cargo vendor --offline --locked --versioned-dirs /tmp/romp-fix-vendor
#   rsync -a --delete /tmp/romp-fix-vendor/ CLUSTER:orcd/scratch/agents/romp_fix/vendor/
#   git -C A archive --format=tar "$REV" | ssh CLUSTER 'set -e; cd orcd/scratch/agents/romp_fix; rm -rf romp.new;
#       mkdir romp.new; tar -x -f - -C romp.new; rm -rf romp; mv romp.new romp'
#   echo "$REV" | ssh CLUSTER 'cat > orcd/scratch/agents/romp_fix/REVISION'
#   echo <the pool's agent-runs/romp_fix, ENGAGING.md> | ssh CLUSTER 'cat > orcd/scratch/agents/romp_fix/RESULTS'
#   rm -rf /tmp/romp-fix-vendor
# Then, on the cluster. Every srun and sbatch below runs from the results' logs directory: each job script's --output is
# relative to the submit directory, and every job refuses to run when submitted from anywhere else, so no log lands in
# $HOME. No submission carries a dependency (sbatch refuses one on a job Slurm has already forgotten, and a job whose
# upstream failed would wait forever; any dependency added later gets --kill-on-invalid-dep=yes).
#   R=~/orcd/scratch/agents/romp_fix; mkdir -p "$(cat $R/RESULTS)/logs"; cd "$(cat $R/RESULTS)/logs"
#   O=$R/romp/tools/rust-spike/orcd; REV=$(cat $R/REVISION); P=$R/py/bin/python3
#   srun --account=mit_general --partition=mit_normal --job-name=romp-fix-memo2-setup --ntasks=1 --cpus-per-task=8 \
#        --mem=8G --time=01:00:00 bash $O/setup.sh                      (on a compute node, never the login node)
#   $P $O/collate.py outputs --revision "$REV" ../setup
# (setup peaked at 2.5 GB in 112 s on 8 CPUs, sacct MaxRSS of the ORCD smoke run of d73c655bd.) The Python environment
# that smoke run made came from an unpinned resolve; setup refuses it (step 4): remove $R/py once (scratch) before it.
# The jobs name the partitions ENGAGING.md assigns: mit_normal for the worlds and the measurement, which must not be cut,
# mit_preemptable for the restartable fuzz and mutant tasks.
#
# First a smoke pass, one or two tasks of each array (ENGAGING.md: test with 1-2 jobs before any sweep):
#   sbatch --array=0 $O/worlds.sbatch            the small world: a fresh dump and every comparison on it
#   sbatch --array=0,6 $O/fuzz.sbatch            layer 2's fuzz seed 1 and the memo's fuzz seed 1 (neither reads a dump)
#   sbatch --array=46,47 $O/mutants.sbatch       mutant 46 (each cleared slice left unsorted, caught on the Mac by the
#                                                edge set and fuzz seed 1) and task 47, the unmutated control
# Check them on the pool before anything else is submitted (each check names what fails):
#   $P $O/collate.py outputs --revision "$REV" ../worlds/small ../fuzz/layer2-seed1 ../fuzz/memo-seed1
#       (run.txt names this revision, steps.tsv has rows and every exit status is 0, SHA256SUMS covers every file
#       and verifies)
#   $P $O/collate.py mutants --revision "$REV" --tasks 46,47 ../mutants
#       (each JSON and its .sha256 there, this revision, the control passing; mutant 46 should read caught)
#   sacct -j <job> --format=JobID%20,JobName%28,State,ExitCode,MaxRSS,Elapsed     for each smoke job: every line
#       COMPLETED with ExitCode 0:0 (a task that refused or failed exits 2 or 1), and the MaxRSS of each task's step
# Memory (sacct MaxRSS of the ORCD smoke run of d73c655bd): worlds small 0.72 GB, memo fuzz seed 1 0.95 GB, the mutant
# tasks 0.82 and 0.83 GB; so fuzz.sbatch and mutants.sbatch ask 4G (about 4x), and the worlds job keeps 24G for the large
# worlds, whose peak the small world's does not bound. Layer 2's fuzz seed 1 ran past 4 minutes there; layers 3 to 7
# were not measured: their seed-1 tasks go in first.
# Then the rest (each wave checked as above before the next that depends on its numbers):
#   sbatch --array=1-2 $O/worlds.sbatch                      the large and large4 worlds
#   sbatch --array=1-5 $O/fuzz.sbatch                        seed 1 of layers 3 to 7 (4G)
#   sbatch --array=0-45%12 $O/mutants.sbatch                 the other 46 mutants
# When fuzz tasks 0 to 6 have finished and check out, read each tool's seed-1 MaxRSS: tool t's other seeds are tasks
# t+7, t+14, t+21, t+28 and t+35 (t: 0 layer2, 1 layer3, 2 layer4, 3 layer5, 4 layer6, 5 layer7, 6 memo). Submit them
# at 4G when every seed-1 peak is under 1 GB:
#   sbatch --array=7-41%16 $O/fuzz.sbatch
# else give a tool above 1 GB its own line with about 4x its peak (sbatch --mem=<4x>G --array=<its five tasks>) and the
# others the line above without those tasks.
# When the large worlds tasks have finished and check out ($P $O/collate.py outputs --revision "$REV" ../worlds/large
# ../worlds/large4; sacct COMPLETED 0:0), take M = about 1.5x the larger of their two MaxRSS, in whole G, and submit what
# reads their dumps:
#   sbatch --mem=<M>G --array=42 $O/fuzz.sbatch               the store's fuzz (the small and large dumps)
#   sbatch --mem=<M>G $O/measure.sbatch                       the measurement (holds the large world, as those tasks do)
# At the end, with sacct COMPLETED 0:0 for every task:
#   $P $O/collate.py outputs --revision "$REV" ../fuzz/* ../measure        (43 fuzz directories and the measurement)
#   $P $O/collate.py mutants --revision "$REV" ../mutants                  (all 48 JSONs: the table, each missed one named)
# Earlier outputs a rerun replaced are under ../superseded/ (nothing is deleted). Cancel by name if anything goes wrong:
#   scancel -u "$USER" --name=romp-fix-memo2-<worlds|fuzz|mutants|measure>
#
# What it does:
#   0. Refuses to run anywhere but a live srun step (SLURM_STEP_ID set, and squeue lists the job as RUNNING), before it
#      creates or changes anything: a login-node shell can carry a stale SLURM_JOB_ID (a shell that outlived its srun) or
#      an salloc's (no step). Then it sets the last setup's outputs aside, so a failed setup leaves none that verify.
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
#      ~/miniforge3 conda from the committed explicit spec python-linux-64.txt (every package named by URL and MD5, each
#      built on or before 2026-11-19, a week before it was pinned; conda checks each download's MD5; no solve, so no
#      dependency resolves to a newer build), read-only use of that conda: the package cache is redirected to scratch,
#      the base environment is untouched, and HOME points at scratch for the conda calls, so conda's registration of the
#      new prefix in ~/.conda/environments.txt lands there, not in $HOME. An existing environment whose explicit spec is
#      not the committed one is refused (remove it and run setup again), so a stale environment is never used. 3.12.11
#      because the port's regex and str.upper tables (pyre_tables.rs, pystr_tables.rs) and every committed fixture were
#      generated under it, with Unicode 15.0.0; a later Python's Unicode tables could turn a cluster difference into an
#      environment artifact. Its Unicode version is checked against the tables' here and by every correctness job
#      (common.sh's need_tables_python); the spec it used is recorded.
#   5. The release build of romp-spike (LTO, one codegen unit: rust/Cargo.toml) and the workspace's tests, once, logged;
#      the binary's SHA-256 and the revision recorded last, which every job checks (common.sh's need_built).
# Results: <RESULTS>/setup/ (run.txt, versions, the Python spec, the test log, the binary's SHA-256, checksums).

# 0. a live srun step, before anything is created or truncated
if [ -z "${SLURM_JOB_ID:-}" ] || [ -z "${SLURM_STEP_ID:-}" ] \
   || [ "$(squeue -h -j "$SLURM_JOB_ID" -o %T 2>/dev/null)" != RUNNING ]; then
    echo "romp_fix: run setup.sh on a compute node through srun (its header), not on a login node (SLURM_JOB_ID" \
         "'${SLURM_JOB_ID:-}', SLURM_STEP_ID '${SLURM_STEP_ID:-}': no live job step)" >&2
    exit 2
fi
source "$(dirname "$(realpath "$0")")/common.sh"

RUSTUP_VERSION=1.29.3
RUST_VERSION=1.100.0
PY_VERSION=3.12.11
OUT="$RESULTS/setup"
fresh_out "$OUT"
start_job
[ "$REVISION" != unknown ] || die "no revision: $ROOT/REVISION must hold the 40-digit commit id the runbook shipped"
echo "setup, revision $REVISION, $(hostname -s), $(date -u +%FT%TZ)" | tee "$OUT/run.txt"
: > "$OUT/steps.tsv"
mkdir -p "$CARGO_HOME" "$RUSTUP_HOME"

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
SPEC="$CODE/tools/rust-spike/orcd/python-linux-64.txt"
CONDA="$HOME/miniforge3/bin/conda"
spec_urls() { grep '^https://' "$1" | sort; }
if [ -z "${ROMP_FIX_PYTHON:-}" ]; then
    [ "$(uname -sm)" = "Linux x86_64" ] || die "the pinned Python spec is linux-64's: set ROMP_FIX_PYTHON on $(uname -sm)"
    [ -x "$CONDA" ] || die "no $CONDA to make the pinned Python: set ROMP_FIX_PYTHON to a CPython $PY_VERSION"
    mkdir -p "$ROOT/home"
    if [ ! -e "$ROOT/py" ]; then
        HOME="$ROOT/home" CONDA_PKGS_DIRS="$ROOT/conda-pkgs" "$CONDA" create -y -p "$ROOT/py" --file "$SPEC"
    fi
    [ -d "$ROOT/py/conda-meta" ] || die "$ROOT/py is not a conda environment: remove it and run setup again"
    HOME="$ROOT/home" "$CONDA" list -p "$ROOT/py" --explicit --md5 > "$OUT/python-conda-spec.txt"
    diff <(spec_urls "$SPEC") <(spec_urls "$OUT/python-conda-spec.txt") > "$OUT/python-spec.diff" \
        || die "the environment at $ROOT/py is not the committed $SPEC (see $OUT/python-spec.diff): remove $ROOT/py and run setup again"
    rm -f "$OUT/python-spec.diff"
    cp "$SPEC" "$OUT/python-linux-64.txt"
fi
"$PYTHON" -c 'import platform, unicodedata; print("python", platform.python_version(), "unicode", unicodedata.unidata_version)' \
    | tee -a "$OUT/versions.txt"
if [ -z "${ROMP_FIX_PYTHON:-}" ]; then
    grep -q "^python $PY_VERSION " "$OUT/versions.txt" || die "$PYTHON is not CPython $PY_VERSION"
fi
need_tables_python

# 5. the build and the tests
cd "$CODE/rust"
run cargo-build cargo build --release --offline --locked -j "$JOBS" -p romp-spike
run cargo-test cargo test --offline --locked -j "$JOBS" -- --test-threads="$JOBS"
grep -q $'^cargo-build\t0\t' "$OUT/steps.tsv" || die "the release build failed (see $OUT/cargo-build.txt)"
grep -q $'^cargo-test\t0\t' "$OUT/steps.tsv" || die "the tests failed (see $OUT/cargo-test.txt)"
sha256sum "$BIN" | tee "$OUT/romp-spike.sha256.txt"
uname -a >> "$OUT/versions.txt"
echo "revision $REVISION" >> "$OUT/versions.txt"     # last: need_built reads it as setup having finished this revision
seal
