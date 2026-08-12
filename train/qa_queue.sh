#!/bin/bash
# Run a list of train_qa.py argument strings, at most JOBS at a time.
#
# The GPU on this box is shared with other work and had ~2 GB free; launching
# ten arms at once produced CUDA_STATUS_ALLOC_FAILED on four of them, which is
# a silent way to lose a cell of a factorial.  A queue is cheaper than
# re-reading logs to find out which arms never ran.
#
#   echo "--nexp 1 --seed 1
#         --nexp 4 --seed 1" | train/qa_queue.sh
set -u
cd "$(dirname "$0")/.."
JOBS=${JOBS:-3}
STEPS=${STEPS:-8000}
OUT=${OUT:-runs/qa}
mkdir -p "$OUT" runs/logs
while IFS= read -r args; do
    [ -z "${args// /}" ] && continue
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do sleep 5; done
    tag=$(echo "$args" | tr -cd 'A-Za-z0-9.' | tr -s ' ' '_')
    echo "launch: $args"
    python3 train/train_qa.py --steps "$STEPS" --out "$OUT" $args \
        > "runs/logs/$tag.log" 2>&1 &
done
wait
echo "queue done"
