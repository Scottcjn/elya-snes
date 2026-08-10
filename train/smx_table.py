#!/usr/bin/env python3
"""Summarise the softmax-family screen, grouped by arm with a seed spread.

Reports nats per CHARACTER - the only axis that is comparable, and the axis
the T = 20 baseline (1.4133 / 1.4149) is quoted on.  Prints the per-seed
values as well as the mean, because the mean of two seeds hides exactly the
thing multi-seed is for: whether the arms' ranges overlap.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CPT = json.load(open(os.path.join(HERE, "..", "data", "vocab.json")))[
    "chars_per_token_bpe"]

BASELINE = (1.4133, 1.4149)      # T = 20, 60,000 steps, seeds 1 and 2


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "runs/smx"
    arms = {}
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        if f.endswith("_ckpt.json"):
            continue
        m = json.load(open(f))
        nm = m["name"]
        # smx_<arm>_t<target>_sh<shift>_<norm>_s<seed>
        parts = nm.split("_")
        key = "_".join(parts[1:-1])
        arms.setdefault(key, []).append((int(parts[-1][1:]), m))

    print("%-22s %-8s %-8s %-8s %-8s %-8s %s"
          % ("arm", "seed1", "seed2", "mean", "spread", "density", "steps"))
    out = []
    for key, runs in arms.items():
        runs.sort()
        vals = [m["val"] / CPT for _, m in runs]
        mean = sum(vals) / len(vals)
        spread = max(vals) - min(vals) if len(vals) > 1 else float("nan")
        out.append((mean, key, vals, spread, runs))
    for mean, key, vals, spread, runs in sorted(out):
        print("%-22s %-8s %-8s %-8.4f %-8.4f %-8.4f %d"
              % (key,
                 "%.4f" % vals[0],
                 "%.4f" % vals[1] if len(vals) > 1 else "-",
                 mean, spread, runs[0][1]["density"], runs[0][1]["steps"]))
    print("\nnats per character, held out, lower is better.")
    print("shipped T = 20 at 60,000 steps: %.4f / %.4f" % BASELINE)
    print("NOTE: this screen is at reduced steps, so it is comparable ACROSS")
    print("ARMS only.  It is not comparable to the 60,000-step baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
