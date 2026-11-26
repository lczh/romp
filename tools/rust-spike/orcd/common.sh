# Sourced by every romp_fix job script in this directory (rust/SPIKE.md, "Where heavy work runs"; ~/manager/ENGAGING.md).
# The layout on the cluster (lczh's ORCD account; the main session ships the code and the vendored crates, see setup.sh):
#   ~/orcd/scratch/agents/romp_fix/romp     the code: `git archive` of one commit (tracked files only, no .git)
#   ~/orcd/scratch/agents/romp_fix/REVISION that commit's 40-digit id (dump_world.py and measure_drive.py record it, as
#                                           ROMP_SPIKE_REVISION, where a checkout would give its HEAD)
#   ~/orcd/scratch/agents/romp_fix/vendor   the 15 crates `cargo vendor` wrote on the Mac (each with its checksums)
#   ~/orcd/scratch/agents/romp_fix/target   the cargo target directory (outside the code, which each revision replaces)
#   ~/orcd/scratch/agents/romp_fix/{cargo,rustup,py,tmp,dumps,mutants,...}   toolchain, Python, scratch (setup.sh)
#   ~/orcd/scratch/agents/romp_fix/RESULTS   one line: the results directory, the group pool's agent-runs/romp_fix
#                                            (ENGAGING.md names it; kept out of the repository), checksummed outputs
#                                            under it and the jobs' logs in its logs/ (submit every job from there:
#                                            `cd "$(cat ~/orcd/scratch/agents/romp_fix/RESULTS)/logs" && sbatch ...`,
#                                            since each script's --output is relative to the submit directory; a job
#                                            submitted from anywhere else is refused below)
# Every output records the revision; a job moves its earlier outputs aside before any check that can fail (set_aside),
# and refuses to run on a build or a Python that setup.sh did not make for this revision (need_built).
# Nothing here touches $HOME outside ~/orcd/scratch/agents/romp_fix, and no credential or token is used.
set -euo pipefail

ROOT="${ROMP_FIX_ROOT:-$HOME/orcd/scratch/agents/romp_fix}"
RESULTS="${ROMP_FIX_RESULTS:-$(cat "$ROOT/RESULTS" 2>/dev/null || true)}"
CODE="$ROOT/romp"
SPIKE="$CODE/tools/rust-spike"

export CARGO_HOME="$ROOT/cargo"
export RUSTUP_HOME="$ROOT/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
export CARGO_TARGET_DIR="$ROOT/target"
export TMPDIR="$ROOT/tmp"
export HF_HOME="$ROOT/hf_cache"                 # every job sets it (ENGAGING.md), though nothing here downloads models
export UV_CACHE_DIR="$ROOT/.cache/uv"
export PYTHONDONTWRITEBYTECODE=1
PYTHON="${ROMP_FIX_PYTHON:-$ROOT/py/bin/python3}"
BIN="$CARGO_TARGET_DIR/release/romp-spike"
JOBS="${SLURM_CPUS_PER_TASK:-2}"
# The code's revision, written by the runbook beside the copy; the copy has no .git, so the Python tools that record a
# revision read it from ROMP_SPIKE_REVISION (dump_world.py refuses to dump without one)
REVISION="$(cat "$ROOT/REVISION" 2>/dev/null || true)"
if [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    export ROMP_SPIKE_REVISION="$REVISION"
else
    REVISION=unknown
fi

die() { echo "romp_fix: $*" >&2; exit 2; }
[ -n "$RESULTS" ] || die "no results directory: write it (ENGAGING.md's pool, agent-runs/romp_fix) into $ROOT/RESULTS"

# set_aside PATH...: move an earlier run's outputs out of the way, into $RESULTS/superseded/<stamp, job, task>/ (nothing
# is deleted), BEFORE any check that can fail: a refused or failed rerun must not leave the last run's outputs in place,
# where they would still verify against their checksums
set_aside() {
    local p dest
    dest="$RESULTS/superseded/$(date -u +%Y%m%dT%H%M%SZ)-${SLURM_JOB_ID:-nojob}-${SLURM_ARRAY_TASK_ID:-0}"
    for p in "$@"; do
        [ -e "$p" ] || continue
        if [ -d "$p" ] && [ -z "$(ls -A "$p")" ]; then rmdir "$p"; continue; fi
        mkdir -p "$dest"
        mv "$p" "$dest/"
    done
}

# fresh_out DIR: the job's output directory, emptied (its earlier contents set aside) and made
fresh_out() { set_aside "$1"; mkdir -p "$1"; }

# start_job: after the job has set its outputs aside, the scratch directories and the submit-directory check. Each job
# script's --output is relative to the submit directory, so a job submitted from anywhere but the pool's logs directory
# writes its log there ($HOME, say): refuse it before any work, and say where the stray log is.
start_job() {
    mkdir -p "$TMPDIR" "$HF_HOME" "$UV_CACHE_DIR" "$RESULTS/logs"
    if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ "$(realpath "$SLURM_SUBMIT_DIR")" != "$(realpath "$RESULTS/logs")" ]; then
        die "submitted from $SLURM_SUBMIT_DIR, not $RESULTS/logs: cd there and submit again (setup.sh's header); delete this job's log from $SLURM_SUBMIT_DIR"
    fi
}

# need_built: the code's revision is known, setup.sh's outputs verify, setup built THIS revision, and the release binary
# is the one setup built (its SHA-256), so a re-shipped code copy never runs under an older build and gets stamped with
# the new revision
need_built() {
    local S="$RESULTS/setup" built want got
    [ "$REVISION" != unknown ] || die "no revision: $ROOT/REVISION must hold the 40-digit commit id the runbook shipped"
    [ -x "$BIN" ] || die "no $BIN: run setup.sh first (it builds the release binary)"
    [ -x "$PYTHON" ] || die "no Python at $PYTHON: run setup.sh first, or set ROMP_FIX_PYTHON to a CPython"
    (cd "$S" 2>/dev/null && sha256sum -c --quiet SHA256SUMS >/dev/null 2>&1) \
        || die "$S does not verify against its SHA256SUMS (setup.sh did not finish): run setup.sh"
    built="$(sed -n 's/^revision //p' "$S/versions.txt" 2>/dev/null | tail -n 1 || true)"
    [ "$built" = "$REVISION" ] || die "setup.sh built revision ${built:-none}, the code is $REVISION: run setup.sh again"
    want="$(awk 'NR == 1 {print $1}' "$S/romp-spike.sha256.txt" 2>/dev/null || true)"
    got="$(sha256sum "$BIN" | awk '{print $1}')"
    [ "$got" = "$want" ] || die "$BIN's SHA-256 is $got, setup.sh recorded ${want:-none}: run setup.sh again"
}

# need_dump DIR...: worlds.sbatch's dump of each world is there and was made from THIS revision (its snapshot's
# world.repoHead), so the store's fuzz and the measurement never read an earlier revision's dump
need_dump() {
    local d head
    for d in "$@"; do
        [ -f "$d/snapshot.json" ] || die "no dump at $d: run worlds.sbatch first"
        head="$(grep -o '"repoHead": "[0-9a-f]*"' "$d/snapshot.json" | head -n 1 | sed 's/.*: "\(.*\)"/\1/' || true)"
        [ -n "$head" ] && [ "$head" = "${REVISION:0:${#head}}" ] \
            || die "the dump at $d is of revision ${head:-unknown}, the code is $REVISION: run worlds.sbatch again"
    done
}

# The interpreter's Unicode version must be the one the port's tables were generated with (pyre_tables.rs and
# pystr_tables.rs: CPython 3.12.11, Unicode 15.0.0), or a difference on the cluster could be the environment's (Python
# 3.13 carries Unicode 15.1.0). Every correctness job calls it before any oracle runs; the measurement does not need it.
need_tables_python() {
    local t got want=""
    for t in pyre_tables.rs pystr_tables.rs; do
        got="$(sed -n 's/^pub const UNICODE_VERSION: &str = "\(.*\)";$/\1/p' "$CODE/rust/crates/romp-views/src/$t")"
        [ -n "$got" ] || die "no UNICODE_VERSION in $t"
        [ -z "$want" ] || [ "$got" = "$want" ] || die "$t is Unicode $got, the other table $want"
        want="$got"
    done
    got="$("$PYTHON" -c 'import unicodedata; print(unicodedata.unidata_version)')" || die "$PYTHON does not run"
    [ "$got" = "$want" ] \
        || die "$PYTHON has Unicode $got, the port's tables $want: use setup.sh's CPython 3.12.11 (ROMP_FIX_PYTHON unset)"
    echo "python $("$PYTHON" -c 'import platform; print(platform.python_version())'), unicode $got (the port's tables: $want)"
}

# run NAME CMD...: one checked step, its output kept in $OUT/NAME.txt, its exit status appended to $OUT/steps.tsv
run() {
    local name="$1"; shift
    local t0 rc
    t0=$(date -u +%s)
    set +e
    "$@" > "$OUT/$name.txt" 2>&1
    rc=$?
    set -e
    printf '%s\t%s\t%s\t%s\n' "$name" "$rc" "$(( $(date -u +%s) - t0 ))" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$OUT/steps.tsv"
    tail -n 3 "$OUT/$name.txt" | sed "s/^/  [$name] /"
    return 0
}

# seal: SHA-256 of every result file, then verify the sums against the files as they now stand on the pool
seal() {
    (cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c --quiet SHA256SUMS) \
        || die "the checksums of $OUT do not verify"
    echo "sealed $OUT ($(wc -l < "$OUT/SHA256SUMS" | tr -d " ") files, revision $REVISION)"
}
