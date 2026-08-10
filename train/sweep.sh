#!/bin/sh
# The training table.  Every arm is the same 102,400-weight shape; only the
# vocabulary, the ternarisation threshold and the quantisation level move.
set -e
cd "$(dirname "$0")/.."
S=${STEPS:-12000}
R() { python3 train/train_nes.py --steps "$S" --eval-every 2000 "$@"; }

# ternarisation threshold sweep - tau is both the sparsity knob and the gain
# knob, because nothing rescales the accumulator except the fixed shift
R --vocab bpe64   --mode twn --tau 0.50
R --vocab bpe64   --mode twn --tau 0.75
R --vocab bpe64   --mode twn --tau 1.00
R --vocab bpe64   --mode twn --tau 1.25
R --vocab bpe64   --mode twn --tau 1.50
# BitNet b1.58 absmean rounding instead of a TWN threshold
R --vocab bpe64   --mode bn  --tau 1.00
# the same thing on plain characters, 31 of the 64 head rows dead
R --vocab charset --mode twn --tau 1.00
# controls: what the ternarisation costs, and what the shape can ever do
R --vocab bpe64   --mode twn --tau 1.00 --quant 1
R --vocab bpe64   --mode twn --tau 1.00 --quant 0
# a second seed on the best-guess arm, to see how much of the gap is noise
R --vocab bpe64   --mode twn --tau 1.00 --seed 2
