#!/bin/sh
# The 60,000-step comparison, multi-seed, against the shipped T = 20 pair.
#
# The baseline is runs/final_av2_bpe64_tau0.75.npz (seed 1, 1.4133 nats/char)
# and runs/t20_final_s2.npz (seed 2, 1.4149).  Both were 60,000 steps, batch
# 192, lr 3e-3, bpe64, tau 0.75, AV_SHIFT 2 - so this script uses exactly
# those settings and changes only the softmax knobs.
#
#   train/smx_final.sh <SM_TARGET> <SM_SHIFT> <SM_NORM> [seeds] [steps]
set -e
cd "$(dirname "$0")/.."
TGT=${1:?usage: smx_final.sh <SM_TARGET> <SM_SHIFT> <SM_NORM> [seeds] [steps]}
SH=${2:?}
NM=${3:?}
SEEDS=${4:-"1 2"}
STEPS=${5:-60000}
OUT=runs
for S in $SEEDS; do
    TAG="smxfinal_t${TGT}_sh${SH}_${NM}_s${S}"
    [ -f "$OUT/$TAG.json" ] && { echo "skip $TAG"; continue; }
    echo "launch $TAG"
    NES_SM_TARGET=$TGT NES_SM_SHIFT=$SH NES_SM_NORM=$NM \
        python3 train/train_nes.py --vocab bpe64 --tau 0.75 \
        --steps "$STEPS" --seed "$S" --name "$TAG" --out "$OUT" \
        > "$OUT/$TAG.log" 2>&1 &
done
wait
echo "final runs complete"
