#!/bin/bash
# The memo's mutant sweep on the cluster (rust/SPIKE.md, S6.1): one array task per source mutant of the memo
# (tools/rust-spike/memo_mutants.py --list: 46 mutants, tasks 0 to 45) and task 46, the unmutated control. A task copies
# rust/ into its own scratch tree, applies its mutant, builds romp-spike under the `mutant` profile (release without LTO),
# and runs the memo edge set (`romp-spike memo` on tests/fixtures/memo_edge.json) and memo fuzz seed 1 (200 worlds of 8
# steps) against it. One JSON line per task in the pool, its "status" one of: caught (either check found a difference),
# missed (both passed), crashed (no difference, but a check's binary died: the profile inherits panic = "abort", so a
# panic is a SIGABRT), buildFailed (the mutant does not compile; no check ran) or error (anything else, such as a Python
# traceback); each check's own status and exit code beside it. The control must pass both checks (or the sweep says
# nothing). The scratch tree is removed after. Restartable, so preemptable. Smoke first (setup.sh's header):
#   sbatch --array=45,46 ~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd/mutants.sbatch   (after setup.sh)
#SBATCH --job-name=romp-fix-memo2-mutants
#SBATCH --account=mit_general
#SBATCH --partition=mit_preemptable
#SBATCH --array=0-46%12
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
# --mem: a guess: size the rest from the smoke tasks' sacct MaxRSS (setup.sh's header)
#SBATCH --mem=8G
#SBATCH --time=01:30:00
#SBATCH --requeue
#SBATCH --output=%x_%A_%a.out

HERE="${ROMP_FIX_ROOT:-$HOME/orcd/scratch/agents/romp_fix}/romp/tools/rust-spike/orcd"
if [ "${1:-}" != "--step" ]; then
    cleanup() { trap '' SIGTERM SIGINT SIGQUIT; kill -TERM -$$ 2>/dev/null; wait; }
    trap cleanup SIGTERM SIGINT SIGQUIT
    srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-1}" bash "$HERE/mutants.sbatch" --step
    rc=$?
    if [ $rc -ge 128 ]; then sleep 300; exit 143; fi
    exit $rc
fi

source "$HERE/common.sh"
need_built
need_tables_python
I="${SLURM_ARRAY_TASK_ID:-0}"
N="$("$PYTHON" "$SPIKE/memo_mutants.py" --count)"
OUT="$RESULTS/mutants"
mkdir -p "$OUT"
T="$ROOT/mutants/$I"
rm -rf "$T"
mkdir -p "$T"
LOG="$OUT/$I.log"
: > "$LOG"
cd "$CODE"
[ "$I" -le "$N" ] || die "task $I: only $N mutants and the control"
name="$("$PYTHON" "$SPIKE/memo_mutants.py" --apply "$I" --tree "$T")"     # task N is the control (copied, unmutated)
echo "task $I: $name, revision $REVISION, $(hostname -s)" >> "$LOG"
MBIN="$T/target/mutant/romp-spike"
set +e
(cd "$T/rust" && CARGO_TARGET_DIR="$T/target" cargo build --offline --locked --profile mutant -j "$JOBS" -p romp-spike) >> "$LOG" 2>&1
build=$?
edge_rc=-1; edge_line=""; fuzz_rc=-1; fuzz_line=""
if [ $build = 0 ]; then
    # each check under a timeout (exit 124, its own status): a mutant may loop; the edge set runs in well under a second
    timeout 600 "$MBIN" memo "$CODE/rust/crates/romp-views/tests/fixtures/memo_edge.json" > "$T/edge.txt" 2>&1
    edge_rc=$?
    edge_line="$(grep -E '^memo cases' "$T/edge.txt" | tail -n 1)"
    timeout 1800 "$PYTHON" "$SPIKE/dump_memo.py" --fuzz 200 --seed 1 --steps 8 --bin "$MBIN" > "$T/fuzz.txt" 2>&1
    fuzz_rc=$?
    # its verdict line, or dump_memo's note that the binary exited without its summary (a crash, or an error exit)
    fuzz_line="$(grep -E '^memo fuzz seed [0-9]+: (OK|FAILED), [0-9]+ differences|exited -?[0-9]+ without its summary line' \
                 "$T/fuzz.txt" | tail -n 1)"
    cat "$T/edge.txt" "$T/fuzz.txt" >> "$LOG"
fi
set -e
"$PYTHON" "$HERE/mutant_status.py" "$I" "$name" "$build" "$edge_rc" "$edge_line" "$fuzz_rc" "$fuzz_line" > "$OUT/$I.json"
cat "$OUT/$I.json"
rm -rf "$T"
(cd "$OUT" && sha256sum "$I.json" "$I.log" > "$I.sha256" && sha256sum -c --quiet "$I.sha256")
