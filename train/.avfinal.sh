#!/bin/sh
set -e
cd "$(dirname "$0")/.."
for S in 1 2; do
    TAG="smxfinal_av3_exact_s${S}"
    [ -f "runs/$TAG.json" ] && { echo "skip $TAG"; continue; }
    echo "launch $TAG"
    NES_AV_SHIFT=3 NES_SM_NORM=exact \
        python3 train/train_nes.py --vocab bpe64 --tau 0.75 \
        --steps 60000 --seed "$S" --name "$TAG" --out runs \
        > "runs/$TAG.log" 2>&1 &
done
wait
echo done
