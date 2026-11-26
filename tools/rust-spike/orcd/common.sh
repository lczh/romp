# Sourced by every romp_fix job script in this directory (rust/SPIKE.md, "Where heavy work runs"; ~/manager/ENGAGING.md).
# The layout on the cluster (lczh's ORCD account; the main session rsyncs the code and the vendored crates, see setup.sh):
#   ~/orcd/scratch/agents/romp_fix/romp     the code (this repository, without .git), REVISION beside it
#   ~/orcd/scratch/agents/romp_fix/vendor   the 15 crates `cargo vendor` wrote on the Mac (each with its checksums)
#   ~/orcd/scratch/agents/romp_fix/{cargo,rustup,py,tmp,dumps,mutants,...}   toolchain, Python, scratch (setup.sh)
#   ~/orcd/scratch/agents/romp_fix/RESULTS   one line: the results directory, the group pool's agent-runs/romp_fix
#                                            (ENGAGING.md names it; kept out of the repository), checksummed outputs
#                                            under it and the jobs' logs in its logs/ (submit every job from there:
#                                            `cd "$(cat ~/orcd/scratch/agents/romp_fix/RESULTS)/logs" && sbatch ...`,
#                                            since each script's --output is relative to the submit directory; a job
#                                            submitted from anywhere else is refused below)
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
# Each job script's --output is relative to the submit directory, so a job submitted from anywhere but the pool's logs
# directory writes its log there ($HOME, say): refuse it before any work, and say where the stray log is.
if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ "$(realpath "$SLURM_SUBMIT_DIR")" != "$(realpath "$RESULTS/logs")" ]; then
    die "submitted from $SLURM_SUBMIT_DIR, not $RESULTS/logs: cd there and submit again (setup.sh's header); delete this job's log from $SLURM_SUBMIT_DIR"
fi

need_built() {
    [ -x "$BIN" ] || die "no $BIN: run setup.sh first (it builds the release binary)"
    [ -x "$PYTHON" ] || die "no Python at $PYTHON: run setup.sh first, or set ROMP_FIX_PYTHON to a CPython"
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
