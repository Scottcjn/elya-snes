#!/usr/bin/env python3
"""The AV_SHIFT ladder, re-run on this tree, next to the NES port's.

AV_SHIFT is the shift that requantises the attention output back to a 4-bit
activation.  The brief asked whether the 6502 result transfers to the 65816,
because the 65816's accumulator is sixteen bits wide.  This prints both ladders
so the answer is a comparison and not an assertion.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CPT = json.load(open(os.path.join(ROOT, "data", "vocab.json")))["chars_per_token_bpe"]

# The NES port's ladder, runs/smxav/* in ~/elya-nes: same 12,000 steps, same
# corpus, same tau, same exact normaliser, same two seeds.
NES = {1: (2.2889, 2.3047), 2: (2.2035, 2.2066), 3: (2.1882, 2.1935),
       4: (2.2587, 2.2534), 5: (2.2662, 2.2677)}


def main(d=None):
    d = d or os.path.join(ROOT, "runs", "avladder")
    got = {}
    for p in sorted(glob.glob(os.path.join(d, "av*_exact_s*.json"))):
        if "_ckpt" in p:
            continue
        j = json.load(open(p))
        got.setdefault(int(j["av_shift"]), {})[int(j["seed"])] = j["val"]

    print("== AV_SHIFT ladder, 12,000 steps, exact softmax normaliser, two "
          "seeds ==")
    print("%-9s %19s %19s %10s" % ("", "this tree (SNES)", "elya-nes (6502)",
                                   "delta"))
    print("%-9s %8s %8s %8s   %8s %8s   %8s"
          % ("AV_SHIFT", "seed 1", "seed 2", "mean", "mean", "nats/char",
             "mean"))
    rows = []
    for a in sorted(got):
        v = got[a]
        if len(v) < 2:
            print("AV_SHIFT %d: only %d seed(s) so far" % (a, len(v)))
            continue
        m = (v[1] + v[2]) / 2
        nm = sum(NES[a]) / 2
        rows.append((a, v[1], v[2], m, nm))
        print("%-9d %8.4f %8.4f %8.4f   %8.4f %8.4f   %+8.4f"
              % (a, v[1], v[2], m, nm, m / CPT, m - nm))
    if not rows:
        return 1
    best = min(rows, key=lambda r: r[3])
    nbest = min(rows, key=lambda r: r[4])
    print()
    print("best on this tree: AV_SHIFT = %d (%.4f nats/token, %.4f nats/char)"
          % (best[0], best[3], best[3] / CPT))
    print("best on elya-nes:  AV_SHIFT = %d (%.4f nats/token)"
          % (nbest[0], nbest[4]))
    spread = max(abs(r[1] - r[2]) for r in rows)
    print("worst seed spread on this tree: %.4f" % spread)
    print("the two ladders %s on the optimum"
          % ("AGREE" if best[0] == nbest[0] else "DISAGREE"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
