#!/bin/sh
# THE REAL QUESTION: with the softmax widened, does a longer context now help?
#
# The context experiment's own screening ladder was 12,000 steps at
# T = 10/20/40/85, and it found an interior optimum at 20 (1.5856 / 1.5292 /
# 1.5347 / 1.5775 nats/char, one seed).  This re-runs the two ends of that
# ladder at TWO seeds under both softmax budgets, so the comparison that
# matters - does T = 85 close on T = 20 when the probability nibble is wider -
# is made at matched steps, matched seeds and matched everything else.
#
# Four cells: {T = 20, T = 85} x {pow2, exact} normaliser, both at the shipped
# budget of 8 - because the screen says the budget is not what was binding and
# the NORMALISER is.  The pow2 cells are a fresh control at 12,000 steps
# rather than the numbers quoted from the context journal, because those were
# one seed and a different tree.
#
#   train/smx_ctx.sh [steps] [workers]
set -e
cd "$(dirname "$0")/.."
STEPS=${1:-12000}
WORKERS=${2:-2}
OUT=runs/smxctx
mkdir -p "$OUT"

JOBS=0
for T in 20 85; do
    for NM in pow2 exact; do
        for S in 1 2; do
            TAG="ctx_T${T}_${NM}_s${S}"
            if [ -f "$OUT/$TAG.json" ]; then
                echo "skip $TAG"
                continue
            fi
            echo "launch $TAG"
            NES_T=$T NES_SM_NORM=$NM \
                python3 train/train_nes.py --vocab bpe64 --tau 0.75 \
                --steps "$STEPS" --seed "$S" --name "$TAG" --out "$OUT" \
                > "$OUT/$TAG.log" 2>&1 &
            JOBS=$((JOBS + 1))
            if [ "$JOBS" -ge "$WORKERS" ]; then
                wait
                JOBS=0
            fi
        done
    done
done
wait
echo "context ladder complete"
