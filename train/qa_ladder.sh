#!/bin/bash
# The capacity ladder.  One expert to sixty-four, three seeds each.
#
# Sixty-four is the interesting end: V = 64, so at nexp = V the routing table
# is the IDENTITY - one feed-forward per vocabulary id - and there is no
# construction left to choose.  It is the dumbest router the machine can
# express, which is what the NES measurements say to prefer.
#
# Scored on answers, never on loss: a sharded arm and a dense arm are fitted to
# different distributions and their cross entropies are not comparable.
set -u
cd "$(dirname "$0")/.."
STEPS=${STEPS:-8000}
OUT=${OUT:-runs/qa}
mkdir -p "$OUT"
for s in ${SEEDS:-1 2 3}; do
  for n in ${NEXP:-1 4 8 16 32 64}; do
    r=bal; [ "$n" = 1 ] && r=bal; [ "$n" = 64 ] && r=id
    f="$OUT/qa_e${n}_${r}_s${s}.json"
    [ -f "$f" ] && { echo "skip $f"; continue; }
    python3 train/train_qa.py --steps "$STEPS" --nexp "$n" --route "$r" \
        --seed "$s" --out "$OUT" 2>&1 | grep -Ev "^\s*$"
  done
done
