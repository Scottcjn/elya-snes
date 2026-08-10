#!/bin/sh
# AV_SHIFT was laddered under the OLD normaliser and 2 won, with the
# "provably correct" 1 measuring worst.  The exact normaliser makes the
# probability distribution flatter, so the accumulator's spread is a
# different question and the ladder has to be re-run rather than assumed.
set -e
cd "$(dirname "$0")/.."
OUT=runs/smxav
mkdir -p "$OUT"
for A in 1 2 3; do
    for S in 1 2; do
        TAG="av${A}_exact_s${S}"
        [ -f "$OUT/$TAG.json" ] && { echo "skip $TAG"; continue; }
        echo "launch $TAG"
        NES_AV_SHIFT=$A NES_SM_NORM=exact \
            python3 train/train_nes.py --vocab bpe64 --tau 0.75 \
            --steps 12000 --seed "$S" --name "$TAG" --out "$OUT" \
            > "$OUT/$TAG.log" 2>&1 &
    done
    wait
done
echo "av ladder complete"
