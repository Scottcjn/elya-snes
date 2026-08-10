#!/usr/bin/env python3
"""How far back does the attention actually look?

A longer context window only helps if the model uses it.  This measures that
directly, from the exact-integer reference the ROM is verified against, over a
real greedy generation: for every head at every position it records the
quantised softmax vector and reports

  * how many positions receive a NONZERO probability nibble,
  * the mean and 95th-percentile attention DISTANCE (p - t) weighted by that
    probability,
  * how much of the total mass lands more than 19 positions back, i.e. outside
    what the T = 20 cartridge could have seen at all.

The last line is the one that decides whether extending the context did
anything.  Note the hard ceiling this kernel imposes: the quantised softmax
normalises so that sum_t p_t <= 8 with every p_t an integer in 0..7, so **at
most 8 positions can carry any weight at all**, whatever T is.  That is a
property of the 6502 kernel, not of the model, and it is why this measurement
is not optional.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--seeds", default="1,26,40,58")
    ap.add_argument("--n", type=int, default=None)
    a = ap.parse_args()
    n = a.n if a.n is not None else ref.T - 1

    m = ref.Model.from_npz(a.npz)
    rows = []
    for s in (int(x) for x in a.seeds.split(",")):
        r = ref.Runner(m)
        r.record_attn = True
        cur = s
        for p in range(n):
            cur = r.step(cur, p)
        rows += r.attn_log

    print("T = %d   heads logged = %d   (seeds %s, %d steps each)"
          % (ref.T, len(rows), a.seeds, n))

    # per-layer aggregation
    print("\n%-6s %-10s %-10s %-10s %-10s %-12s"
          % ("layer", "nonzero", "mean dist", "p95 dist", "max dist", "mass >19 back"))
    allmass = allfar = 0.0
    for l in range(ref.L):
        nz = dist = mass = far = 0.0
        cnt = 0
        p95src = []
        mx = 0
        for (ll, h, p, pr) in rows:
            if ll != l:
                continue
            cnt += 1
            nz += sum(1 for v in pr if v)
            tot = sum(pr)
            if tot == 0:
                continue
            for t, v in enumerate(pr):
                if not v:
                    continue
                d = p - t
                dist += v * d
                mass += v
                p95src += [d] * v
                if d > mx:
                    mx = d
                if d > 19:
                    far += v
        p95src.sort()
        p95 = p95src[int(0.95 * (len(p95src) - 1))] if p95src else 0
        print("%-6d %-10.2f %-10.2f %-10d %-10d %-12s"
              % (l, nz / max(cnt, 1), dist / max(mass, 1e-9), p95, mx,
                 "%.2f%%" % (100.0 * far / max(mass, 1e-9))))
        allmass += mass
        allfar += far
    print("\nATTENTION MASS LANDING MORE THAN 19 POSITIONS BACK: %.2f%%"
          % (100.0 * allfar / max(allmass, 1e-9)))
    print("(19 back is the furthest the T = 20 cartridge could reach.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
