#!/bin/bash
# The worlds' comparisons on the cluster (rust/SPIKE.md, S6.1): one array task per world (0 small, 1 large, 2 large4).
# Each dumps its world fresh (dump_world.py, with its own replay self-checks), then runs every comparison on it: the
# typed round trip, layers 1 to 5 (Python's answers, then romp-spike's), layer 6 against the dump's entries and the
# layer 6 harness against them, the feed against the dump's oracle and the layer 7 harness against it, the memo (three
# builds against the oracle, then dump_memo.py's default sequence against Python's decisions) and the transcript store
# (check_store.py). The dumps stay in scratch (~/orcd/scratch/agents/romp_fix/dumps/<world>) for measure.sbatch; the
# outputs, a steps.tsv of exit statuses and SHA256SUMS go to the pool.
#   sbatch ~/orcd/scratch/agents/romp_fix/romp/tools/rust-spike/orcd/worlds.sbatch     (after setup.sh)
#SBATCH --job-name=romp-fix-memo2-worlds
#SBATCH --account=mit_general
#SBATCH --partition=mit_normal
#SBATCH --array=0-2
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=24G
#SBATCH --time=03:00:00
#SBATCH --output=%x_%A_%a.out

HERE="${ROMP_FIX_ROOT:-$HOME/orcd/scratch/agents/romp_fix}/romp/tools/rust-spike/orcd"
if [ "${1:-}" != "--step" ]; then
    cleanup() { trap '' SIGTERM SIGINT SIGQUIT; kill -TERM -$$ 2>/dev/null; wait; }
    trap cleanup SIGTERM SIGINT SIGQUIT
    srun --ntasks=1 --cpus-per-task="${SLURM_CPUS_PER_TASK:-1}" bash "$HERE/worlds.sbatch" --step
    exit $?
fi

source "$HERE/common.sh"
need_built
WORLDS=(small large large4)
W="${WORLDS[${SLURM_ARRAY_TASK_ID:-0}]}"
D="$ROOT/dumps/$W"
OUT="$RESULTS/worlds/$W"
mkdir -p "$OUT" "$ROOT/dumps"
: > "$OUT/steps.tsv"
echo "world $W, revision $REVISION, $(hostname -s), $(date -u +%FT%TZ)" | tee "$OUT/run.txt"
cd "$CODE"

run dump "$PYTHON" "$SPIKE/dump_world.py" --world "$W" --out "$D"
run roundtrip "$BIN" roundtrip "$D"
for n in 1 2 3 4 5; do
    run "py-layer$n" "$PYTHON" "$SPIKE/dump_layer$n.py" "$D"
    run "layer$n" "$BIN" "layer$n" "$D"
done
run layer6 "$BIN" layer6 "$D"
run py-layer6-harness "$PYTHON" "$SPIKE/dump_layer6.py" "$D"
run feed "$BIN" feed "$D"
run py-layer7-harness "$PYTHON" "$SPIKE/dump_layer7.py" "$D"
run memo-dump "$BIN" memo "$D"
run memo-sequence "$PYTHON" "$SPIKE/dump_memo.py" --dump "$D" --bin "$BIN" --out "$OUT/memo_sequence.json"
run store "$PYTHON" "$SPIKE/check_store.py" --dump "$D" --bin "$BIN" --json "$OUT/store.json"
# the dumps' answer files are large (the large world's layer1.json is 36 MB): keep their sizes and hashes, not copies
(cd "$D" && sha256sum snapshot.json layer*.json oracle/*.json 2>/dev/null) > "$OUT/dump-files.sha256"

failed=$(awk -F'\t' '$2 != 0' "$OUT/steps.tsv" | wc -l)
echo "world $W: $(wc -l < "$OUT/steps.tsv" | tr -d " ") steps, $(echo $failed) failed" | tee -a "$OUT/run.txt"
seal
[ "$failed" = 0 ]
