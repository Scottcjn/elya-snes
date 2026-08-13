#!/bin/bash
# The narrow-expert experiment: one model per topic, against one model over all
# five topics, on the same held-out paraphrases.
#
# The sibling Genesis port's README records the finding this repo has quoted
# twice and never tested: "One 114K-parameter model trying to memorise 122 QA
# pairs across four unrelated topics produced word salad.  The same model on one
# topic produced complete sentences."  train/corpus.py labels every fact with a
# topic for exactly this and nothing has ever cut on it.
#
# Each shard trains on its topic's questions plus the whole monologue (which is
# not a topic - it is act 2's free run) and is SCORED ON ITS OWN TOPIC ONLY.
# That is deliberately generous to the shard: it assumes a router that always
# picks the right one, which this port does not have (FINDINGS entry 10 - no
# router in rom/nn.s, and 64 experts of straight-line weight code exceed the
# 4 MB LoROM ceiling).  If the shards do not win even with free routing, the
# router is not worth building; if they do, the number is an upper bound.
#
# The comparison arm is the whole-corpus run of the identical recipe, scored on
# the same per-topic subsets - train/qa_shard_table.py does that split.
set -eu
cd "$(dirname "$0")/.."

OUT=${OUT:-runs/qa_shard}
SEEDS=${SEEDS:-"1 2 3"}
FLAGS=${FLAGS:-"--qnoise 0.1 --qw 0.25 --steps 16000"}
JOBS=${JOBS:-2}
mkdir -p "$OUT"

JOBFILE=$(mktemp)
trap 'rm -f "$JOBFILE"' EXIT
for topic in identity hardware model game honesty; do
    for s in $SEEDS; do
        name="shard_${topic}_s${s}"
        [ -f "$OUT/$name.json" ] && { echo "skip $name"; continue; }
        printf '%s\t%s\t%s\n' "$name" "$s" "$topic" >> "$JOBFILE"
    done
done

if [ -s "$JOBFILE" ]; then
    echo "$(wc -l < "$JOBFILE") shard runs, $JOBS at a time:  $FLAGS"
    < "$JOBFILE" xargs -P "$JOBS" -I{} -d '\n' sh -c '
        set -- $(printf "%s" "{}" | tr "\t" " ")
        name=$1; seed=$2; topic=$3
        echo "=== $name ==="
        python3 train/train_qa.py --nopos 1 '"$FLAGS"' --topic "$topic" \
            --seed "$seed" --name "$name" --out "'"$OUT"'" \
            > "'"$OUT"'/$name.log" 2>&1 \
            || { echo "FAILED $name"; tail -3 "'"$OUT"'/$name.log"; exit 1; }
        grep -E "^(train|dev|test|legacy|FINAL)" "'"$OUT"'/$name.log" || true
    '
fi
echo
python3 train/qa_arms.py "$OUT/*.json"
