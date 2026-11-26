#!/bin/bash
# The full fuzz seeds of every layer and of the memo on the cluster (rust/SPIKE.md, S6.1): one array task per
# (tool, seed), at the sizes every step's record used: layers 2 to 5 at 10,000 cases a seed, layers 6 and 7 at 4,000
# worlds, the memo at 2,000 worlds of 8 steps, seeds 1 to 6; and the transcript store's 6 fuzz seeds over the small and
# large dumps (task 42; needs worlds.sbatch's dumps: submit with --dependency=afterok:<the worlds job>). Each task's
# output and exit status go to the pool. Restartable (a task redoes its seed), so the preemptable partition.
#   sbatch [--dependency=afterok:<worlds job>] ~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd/fuzz.sbatch
#SBATCH --job-name=romp-fix-memo2-fuzz
#SBATCH --account=mit_general
#SBATCH --partition=mit_preemptable
#SBATCH --array=0-42%16
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=03:00:00
#SBATCH --requeue
#SBATCH --output=%x_%A_%a.out

HERE="${ROMP_FIX_ROOT:-$HOME/orcd/scratch/agents/romp_fix}/romp/tools/rust-spike/orcd"
if [ "${1:-}" != "--step" ]; then
    cleanup() { trap '' SIGTERM SIGINT SIGQUIT; kill -TERM -$$ 2>/dev/null; wait; }
    trap cleanup SIGTERM SIGINT SIGQUIT
    srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-1}" bash "$HERE/fuzz.sbatch" --step
    rc=$?
    # preemption cancels the step first; stay RUNNING until the job-level kill lands so the requeue sticks (the
    # cluster rules' requeue shape); a real failure exits on its own after the wait
    if [ $rc -ge 128 ]; then sleep 300; exit 143; fi
    exit $rc
fi

source "$HERE/common.sh"
need_built
TASKS=()
for seed in 1 2 3 4 5 6; do
    TASKS+=("layer2 $seed dump_layer2.py --fuzz 10000 --seed $seed")
    TASKS+=("layer3 $seed dump_layer3.py --fuzz 10000 --seed $seed")
    TASKS+=("layer4 $seed dump_layer4.py --fuzz 10000 --seed $seed")
    TASKS+=("layer5 $seed dump_layer5.py --fuzz 10000 --seed $seed")
    TASKS+=("layer6 $seed dump_layer6.py --fuzz 4000 --seed $seed")
    TASKS+=("layer7 $seed dump_layer7.py --fuzz 4000 --seed $seed")
    TASKS+=("memo $seed dump_memo.py --fuzz 2000 --seed $seed --steps 8")
done
TASKS+=("store 1-6 check_store.py --dump $ROOT/dumps/small --dump $ROOT/dumps/large --fuzz 6")
I="${SLURM_ARRAY_TASK_ID:-0}"
[ "$I" -lt "${#TASKS[@]}" ] || die "task $I: only ${#TASKS[@]} tasks"
read -r tool seed script args <<< "${TASKS[$I]}"
OUT="$RESULTS/fuzz/$tool-seed$seed"
rm -rf "$OUT"
mkdir -p "$OUT"
: > "$OUT/steps.tsv"
echo "task $I: $tool seed $seed, revision $REVISION, $(hostname -s), $(date -u +%FT%TZ)" | tee "$OUT/run.txt"
cd "$CODE"
# shellcheck disable=SC2086  # $args is the tool's flags, split on purpose
run fuzz "$PYTHON" "$SPIKE/$script" $args --bin "$BIN"
seal
awk -F'\t' '$2 != 0 {bad=1} END {exit bad}' "$OUT/steps.tsv"
