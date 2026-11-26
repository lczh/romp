#!/bin/bash
# S6's CPU measurement re-run with S6.1's memo rows, on the cluster (rust/SPIKE.md, "S6.1 memo port and tuning"). Uses
# worlds.sbatch's fresh dumps of this revision (submit it only after the large worlds tasks have finished and checked
# out, with --mem from their MaxRSS: setup.sh's header): measure_drive.py alternates
# measure.py (the held rows as in S6, plus --memo: the real key over the recorded facts) and `romp-spike measure --memo`
# (the tuned build: FxHash, LTO, one codegen unit; the held rows plus the memo rows), 8 runs on the large, large4 and
# small worlds, with the production window of S6's own run (rust/notes/s6-runs.json's two /perf snapshots: 7.7% misses,
# 0.46 a build) for the blend. ORCD's CPUs are not the Mac's (x86_64 Linux, not Apple M4 Max), so every ratio here is
# indicative; the gate's numbers stay the Mac's (arm64 CPython 3.13.18), and the driver's gate verdict reads
# "insufficient" for this interpreter by design. Not preemptable: a measurement cut in the middle is wasted.
#   sbatch --mem=<from the large worlds tasks> ~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd/measure.sbatch
# The job exits 1 when a step failed, 2 when it refused to start.
#SBATCH --job-name=romp-fix-memo2-measure
#SBATCH --account=mit_general
#SBATCH --partition=mit_normal
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
# --mem: a guess; the sbatch line overrides it with about 1.5x the large worlds tasks' sacct MaxRSS (setup.sh's header)
#SBATCH --mem=24G
#SBATCH --time=06:00:00
#SBATCH --output=%x_%j.out

HERE="${ROMP_FIX_ROOT:-$HOME/orcd/scratch/agents/romp_fix}/romp/tools/rust-spike/orcd"
if [ "${1:-}" != "--step" ]; then
    cleanup() { trap '' SIGTERM SIGINT SIGQUIT; kill -TERM -$$ 2>/dev/null; wait; }
    trap cleanup SIGTERM SIGINT SIGQUIT
    srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-1}" bash "$HERE/measure.sbatch" --step
    exit $?
fi

source "$HERE/common.sh"
OUT="$RESULTS/measure"
fresh_out "$OUT"
start_job
need_built
need_dump "$ROOT/dumps/large" "$ROOT/dumps/large4" "$ROOT/dumps/small"
echo "measure, revision $REVISION, $(hostname -s), $(date -u +%FT%TZ)" | tee "$OUT/run.txt"
: > "$OUT/steps.tsv"
{
    echo "revision $REVISION, $(hostname -s), $(date -u +%FT%TZ)"
    uname -a
    lscpu 2>/dev/null | grep -E '^(Model name|Architecture|CPU\(s\)|Thread|Core|Socket|CPU max MHz|L3)' || true
    "$PYTHON" -c 'import platform; print("python", platform.python_version(), platform.machine())'
    grep -m1 '^rustc' "$RESULTS/setup/versions.txt" 2>/dev/null || true
} > "$OUT/machine.txt"
cat "$OUT/machine.txt"
cd "$CODE"
"$PYTHON" - "$CODE/rust/notes/s6-runs.json" "$OUT" <<'EOF'
import json, os, sys
doc = json.load(open(sys.argv[1]))
for name, snap in zip(("perf-before.json", "perf-after.json"), doc["perf"]):
    with open(os.path.join(sys.argv[2], name), "w") as f:
        json.dump(snap, f)
EOF
run drive "$PYTHON" "$SPIKE/measure_drive.py" --dump "$ROOT/dumps/large" --dump "$ROOT/dumps/large4" \
    --dump "$ROOT/dumps/small" --runs 8 --python "$PYTHON" --cpu --memo --perf "$OUT/perf-before.json" \
    --perf "$OUT/perf-after.json" --binary "$BIN" --out "$OUT/s61-runs.json"
run summary "$PYTHON" "$SPIKE/measure_drive.py" --summarize "$OUT/s61-runs.json"
seal
awk -F'\t' '$2 != 0 {bad=1} END {exit bad}' "$OUT/steps.tsv"
