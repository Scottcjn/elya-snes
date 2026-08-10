#!/bin/sh
# The 2 x 2 that answers "do the two wins compose?"
#
#   A  softmax   pow2  (NES_SM_NORM=pow2,  NES_AV_SHIFT=2) - what shipped first
#              exact  (NES_SM_NORM=exact, NES_AV_SHIFT=3) - the softmax branch
#   B  capacity dense (--nexp 1)                           - the control
#              n8     (--nexp 8 --route bal)               - the mixture branch
#
# Both branches published a number, but they published it on THEIR OWN tree,
# with their own dense control - the mixture journal already records that its
# baseline (1.4020/1.4096) is 0.011 nats/char away from the number the context
# journal published for the same recipe (1.4133/1.4149).  Comparing the merged
# cell against two numbers measured elsewhere would put that discrepancy
# straight into the interaction term, which is the quantity of interest and is
# the same size.  So all four cells are re-run HERE, on this tree, at two seeds
# each, with everything else identical, and scored by one estimator
# (train/eval_npz.py, 60 batches, the trainer's own eval generator).
#
#   sh train/factorial.sh [steps] [outdir]
set -e
cd "$(dirname "$0")/.."
STEPS=${1:-60000}
OUT=${2:-runs/fac}
JOBS=${JOBS:-4}
mkdir -p "$OUT"

run() {   # run <cell> <norm> <avshift> <nexp> <seed>
    NES_SM_NORM=$2 NES_AV_SHIFT=$3 \
    python3 train/train_nes.py --vocab bpe64 --tau 0.75 --steps "$STEPS" \
        --batch 192 --lr 3e-3 --nexp "$4" --route bal --route-seed 1 \
        --seed "$5" --name "$1_s$5" --out "$OUT" \
        > "$OUT/$1_s$5.log" 2>&1
}

n=0
for seed in 1 2; do
    run dense_pow2  pow2  2 1 $seed & n=$((n+1))
    run dense_exact exact 3 1 $seed & n=$((n+1))
    run n8_pow2     pow2  2 8 $seed & n=$((n+1))
    run n8_exact    exact 3 8 $seed & n=$((n+1))
    [ "$JOBS" -ge 8 ] || wait
done
wait
echo "all $n runs finished"
