#!/usr/bin/env python3
"""Measure whether a block of 32 is safe for THIS formulation.

The prior run found that a block of 32 never saturated an int8 accumulator in
1.148e9 blocks despite a worst case of 224, because ternary zeros and sign
cancellation make a mixed-sign sum grow like sqrt(n) rather than n.

That argument does not obviously carry over to a SIGN-SEPARATED kernel, where
every term inside a block comes from the same index list and therefore has the
same sign - there is nothing left to cancel.  So measure it: run the real
model over the real (T-1)-token trajectory and histogram the actual block
sums.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ref


def main():
    npz = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NES_WEIGHTS")
    m = ref.Model.from_npz(npz) if npz else ref.Model()
    print("weights: %s" % (npz or "(random init)"))
    r = ref.Runner(m)
    cur = 1
    stats = {}
    for b in (16, 32):
        stats[b] = {"blocks": 0, "max": 0, "over127": 0, "over255": 0}

    # monkeypatch ternary_row so every block sum is observed in-flight
    orig = ref.ternary_row

    def watched(pos_idx, neg_idx, actb):
        for lst in (pos_idx, neg_idx):
            for b in (16, 32):
                for i in range(0, len(lst), b):
                    chunk = lst[i:i + b]
                    signed = sum(actb[j] - ref.BIAS for j in chunk)   # unbiased
                    biased = sum(actb[j] for j in chunk)              # as stored
                    s = stats[b]
                    s["blocks"] += 1
                    s["max"] = max(s["max"], abs(signed), biased)
                    if abs(signed) > 127:
                        s["over127"] += 1
                    if biased > 255:
                        s["over255"] += 1
        return orig(pos_idx, neg_idx, actb)

    ref.ternary_row = watched
    for p in range(ref.T - 1):
        cur = r.step(cur, p)
    ref.ternary_row = orig

    print("Measured over the real %d-token trajectory, sign-separated lists:"
          % (ref.T - 1))
    print("  %-6s %10s %8s %14s %14s" % ("block", "blocks", "max |v|",
                                         "signed >127", "biased >255"))
    for b in (16, 32):
        s = stats[b]
        print("  %-6d %10d %8d %14d %14d"
              % (b, s["blocks"], s["max"], s["over127"], s["over255"]))
    print()
    print("Worst case by construction: block*14 biased, block*7 signed.")
    print("  block 16 -> 224 biased / 112 signed : both provably in range")
    print("  block 32 -> 448 biased / 224 signed : both provably OUT of range")


if __name__ == "__main__":
    main()
