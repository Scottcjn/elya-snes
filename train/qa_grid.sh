#!/bin/bash
# The paraphrase grid: one arm per training recipe, five seeds each.
#
# Five seeds because entry 10's positional ablation moved the held-out number
# by nine points while the seed spread INSIDE one arm was twenty-one.  A
# single-seed comparison on this corpus is noise with a narrative attached, and
# this script exists so that nothing on this tree gets compared with one.
#
# Arms are chosen on `dev` and reported on `test`; train/qa_arms.py prints
# both.  Nothing in this script looks at `test` - see train/corpus.py for why
# the split exists at all.
#
#   bash train/qa_grid.sh                       the default sweep, 5 seeds
#   ARMS="qn0.1" bash train/qa_grid.sh          one arm
#   SEEDS="1 2 3" bash train/qa_grid.sh         fewer seeds
#
# An arm name is turned into flags by `flags_of` below, so the name in
# runs/*/ and the recipe that produced it cannot drift apart.
set -eu
cd "$(dirname "$0")/.."

OUT=${OUT:-runs/qa_para}
SEEDS=${SEEDS:-"1 2 3 4 5"}
ARMS=${ARMS:-"qn0 qn0.05 qn0.1 qn0.15 qn0.2"}
mkdir -p "$OUT"

flags_of() {                # arm name -> trainer flags.  Spelled out rather
    case "$1" in            # than parsed, so a typo is an error and not a
        qn0)          echo "--qnoise 0"    ;;   # silently different recipe.
        qn0.05)       echo "--qnoise 0.05" ;;
        qn0.1)        echo "--qnoise 0.1"  ;;
        qn0.15)       echo "--qnoise 0.15" ;;
        qn0.2)        echo "--qnoise 0.2"  ;;
        qn0.3)        echo "--qnoise 0.3"  ;;
        # --qw scales the loss on the QUESTION positions.  At 1.0 the model
        # spends capacity predicting the next token of a question it is being
        # handed anyway, which on a paraphrase corpus is 208 strings of pure
        # memorisation competing with the answers for 102,400 ternary weights.
        qn0.1_qw0.25) echo "--qnoise 0.1 --qw 0.25" ;;
        qn0.1_qw0)    echo "--qnoise 0.1 --qw 0"    ;;
        qn0.2_qw0.25) echo "--qnoise 0.2 --qw 0.25" ;;
        # three times the corpus at the same step count is a third of the
        # passes over each row; this asks whether 8000 was enough.
        qn0.1_st16k)  echo "--qnoise 0.1 --steps 16000" ;;
        qn0.1_qw0.25_st16k) echo "--qnoise 0.1 --qw 0.25 --steps 16000" ;;
        *)            echo "unknown arm $1" >&2; exit 2 ;;
    esac
}

for arm in $ARMS; do
    FLAGS=$(flags_of "$arm")
    for s in $SEEDS; do
        name="para_${arm}_s${s}"
        if [ -f "$OUT/$name.json" ]; then
            echo "skip $name (already scored)"
            continue
        fi
        echo "=== $name   $FLAGS ==="
        # shellcheck disable=SC2086
        python3 train/train_qa.py --nopos 1 $FLAGS \
            --seed "$s" --name "$name" --out "$OUT" > "$OUT/$name.log" 2>&1 \
            || { echo "FAILED $name"; tail -5 "$OUT/$name.log"; exit 1; }
        grep -E "^(train|dev|test|legacy|FINAL)" "$OUT/$name.log" || true
    done
done

echo
python3 train/qa_arms.py "$OUT/*.json"
