# Sourced by every romp_fix job script in this directory (rust/SPIKE.md, "Where heavy work runs"; ~/manager/ENGAGING.md).
# The layout on the cluster (lczh's ORCD account; the main session rsyncs the code and the vendored crates, see setup.sh):
#   ~/orcd/scratch/agents/romp_fix/romp     the code (this repository, without .git), REVISION beside it
#   ~/orcd/scratch/agents/romp_fix/vendor   the 15 crates `cargo vendor` wrote on the Mac (each with its checksums)
#   ~/orcd/scratch/agents/romp_fix/{cargo,rustup,py,tmp,dumps,mutants,...}   toolchain, Python, scratch (setup.sh)
#   ~/orcd/scratch/agents/romp_fix/RESULTS   one line: the results directory, the group pool's agent-runs/romp_fix
#                                            (ENGAGING.md names it; kept out of the repository), checksummed outputs
#                                            under it and the jobs' logs in its logs/ (submit every job from there:
#                                            `cd "$(cat ~/orcd/scratch/agents/romp_fix/RESULTS)/logs" && sbatch ...`,
#                                            since each script's --output is relative to the submit directory)
# Nothing here touches $HOME outside ~/orcd/scratch/agents/romp_fix, and no credential or token is used.
set -euo pipefail

ROOT="${ROMP_FIX_ROOT:-$HOME/orcd/scratch/agents/romp_fix}"
RESULTS="${ROMP_FIX_RESULTS:-$(cat "$ROOT/RESULTS" 2>/dev/null || true)}"
CODE="$ROOT/romp"
SPIKE="$CODE/tools/rust-spike"

export CARGO_HOME="$ROOT/cargo"
export RUSTUP_HOME="$ROOT/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
export CARGO_TARGET_DIR="$CODE/rust/target"
export TMPDIR="$ROOT/tmp"
export HF_HOME="$ROOT/hf_cache"                 # every job sets it (ENGAGING.md), though nothing here downloads models
export UV_CACHE_DIR="$ROOT/.cache/uv"
export PYTHONDONTWRITEBYTECODE=1
PYTHON="${ROMP_FIX_PYTHON:-$ROOT/py/bin/python3}"
BIN="$CARGO_TARGET_DIR/release/romp-spike"
JOBS="${SLURM_CPUS_PER_TASK:-2}"
REVISION="$(cat "$ROOT/REVISION" 2>/dev/null || echo unknown)"

die() { echo "romp_fix: $*" >&2; exit 2; }
[ -n "$RESULTS" ] || die "no results directory: write it (ENGAGING.md's pool, agent-runs/romp_fix) into $ROOT/RESULTS"
mkdir -p "$TMPDIR" "$HF_HOME" "$UV_CACHE_DIR" "$RESULTS/logs"

need_built() {
    [ -x "$BIN" ] || die "no $BIN: run setup.sh first (it builds the release binary)"
    [ -x "$PYTHON" ] || die "no Python at $PYTHON: run setup.sh first, or set ROMP_FIX_PYTHON to a CPython 3.12+"
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
