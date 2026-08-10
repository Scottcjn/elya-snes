#!/bin/sh
# Re-run the NES port's AV_SHIFT ladder on this tree, unchanged in every other
# respect: 12,000 steps, bpe64, tau 0.75, the EXACT softmax normaliser, two
# seeds per rung.  Those are the settings runs/smxav/* in the NES repo used, so
# the two ladders are directly comparable and any difference is the port.
set -e
cd "$(dirname "$0")/.."
SEEDS=${SEEDS:-"1 2"}
STEPS=${STEPS:-12000}
mkdir -p runs/avladder
for A in 1 2 3 4 5; do
  for S in $SEEDS; do
    TAG="av${A}_exact_s${S}"
    [ -f "runs/avladder/$TAG.json" ] && { echo "skip $TAG"; continue; }
    echo "launch $TAG"
    NES_AV_SHIFT=$A NES_SM_NORM=exact \
      python3 train/train_nes.py --vocab bpe64 --tau 0.75 \
      --steps "$STEPS" --seed "$S" --name "$TAG" --out runs/avladder \
      > "runs/avladder/$TAG.log" 2>&1
  done
done
echo "av ladder complete"
