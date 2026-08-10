#!/bin/sh
# Screen the softmax-representation family at reduced steps, MULTI-SEED.
#
# The arms are the design family in DESIGN.md: SM_TARGET is the probability
# budget (sum_t p_t <= SM_TARGET, p_t in 0..SM_TARGET-1) and SM_SHIFT is the
# exp table's bucket width.  They are orthogonal knobs and both are measured
# alone before they are measured together, because "we changed two things and
# it got better" is not a result.
#
# 12,000 steps is the screening budget the context experiment used, and the
# seed noise at that budget was measured there as 0.009 nats/char.  Two seeds
# per arm, because a single-seed difference on this corpus has already
# produced one retracted claim.
#
#   train/smx_screen.sh [steps] [workers]
set -e
cd "$(dirname "$0")/.."
STEPS=${1:-12000}
WORKERS=${2:-2}
OUT=runs/smx
mkdir -p "$OUT"

# arm  SM_TARGET  SM_SHIFT  NORM
ARMS="a:8:3:pow2 b:16:3:pow2 c:32:3:pow2 d:16:2:pow2 e:8:2:pow2 f:8:3:exact g:16:3:exact"
SEEDS="1 2"

JOBS=0
for A in $ARMS; do
    NAME=$(echo "$A" | cut -d: -f1)
    TGT=$(echo "$A" | cut -d: -f2)
    SH=$(echo "$A" | cut -d: -f3)
    NM=$(echo "$A" | cut -d: -f4)
    for S in $SEEDS; do
        TAG="smx_${NAME}_t${TGT}_sh${SH}_${NM}_s${S}"
        if [ -f "$OUT/$TAG.json" ]; then
            echo "skip $TAG (already done)"
            continue
        fi
        echo "launch $TAG"
        NES_SM_TARGET=$TGT NES_SM_SHIFT=$SH NES_SM_NORM=$NM \
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
wait
echo "screen complete"
