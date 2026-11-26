#!/bin/bash
# The memo's mutant sweep on the cluster (rust/SPIKE.md, S6.1): one array task per source mutant of the memo
# (tools/rust-spike/memo_mutants.py --list: 47 mutants, tasks 0 to 46) and task 47, the unmutated control. A task copies
# rust/ into its own scratch tree, applies its mutant, builds romp-spike under the `mutant` profile (release without LTO),
# and runs the memo edge set (`romp-spike memo` on tests/fixtures/memo_edge.json) and memo fuzz seed 1 (200 worlds of 8
# steps) against it. One JSON line per task in the pool, with the revision, its "status" one of: caught (either check
# found a difference), missed (both passed: each check's exit 0 AND its clean summary line), crashed (no difference, but
# a check's binary died: the profile inherits panic = "abort", so a panic is a SIGABRT), timedOut, buildFailed (the
# mutant does not compile; no check ran) or error (anything else, such as a Python traceback); each check's own status,
# exit code and summary line beside it. The control must pass both checks (or the sweep says nothing). The task exits 0
# for a result (caught, missed, crashed, timedOut; the control's passed) and 1 for buildFailed, error and a control that
# did not pass, so sacct shows those as FAILED; collate.py mutants checks all 48 JSONs (one revision, the control passing)
# and prints the table. The scratch tree is removed after. Restartable, so preemptable. Smoke first (setup.sh's header):
#   sbatch --array=46,47 ~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd/mutants.sbatch   (after setup.sh)
#SBATCH --job-name=romp-fix-memo2-mutants
#SBATCH --account=mit_general
#SBATCH --partition=mit_preemptable
#SBATCH --array=0-47%12
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
# --mem: about 4x the measured peak: the regression mutant's task peaked at 0.82 GB and the control's at 0.83 GB (sacct
# MaxRSS, the ORCD smoke run of d73c655bd, 45 s and 60 s; the build and both checks)
#SBATCH --mem=4G
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
I="${SLURM_ARRAY_TASK_ID:-0}"
OUT="$RESULTS/mutants"
mkdir -p "$OUT"
# this task's outputs set aside before any check that can fail: a refused rerun must not leave an earlier JSON that
# still verifies against its .sha256
set_aside "$OUT/$I.json" "$OUT/$I.log" "$OUT/$I.sha256"
start_job
need_built
need_tables_python
N="$("$PYTHON" "$SPIKE/memo_mutants.py" --count)"
[ "$I" -le "$N" ] || die "task $I: only $N mutants and the control"
T="$ROOT/mutants/$I"
rm -rf "$T"
mkdir -p "$T"
LOG="$OUT/$I.log"
: > "$LOG"
cd "$CODE"
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
"$PYTHON" "$HERE/mutant_status.py" "$I" "$name" "$build" "$edge_rc" "$edge_line" "$fuzz_rc" "$fuzz_line" "$REVISION" \
    > "$OUT/$I.json"
verdict_rc=$?
set -e
cat "$OUT/$I.json"
rm -rf "$T"
(cd "$OUT" && sha256sum "$I.json" "$I.log" > "$I.sha256" && sha256sum -c --quiet "$I.sha256")
# mutant_status.py's exit: 0 for a result, 1 for buildFailed, error or a control that did not pass (or its own failure)
exit "$verdict_rc"
