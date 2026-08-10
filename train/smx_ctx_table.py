#!/usr/bin/env python3
"""The context re-test, as a table: {T = 20, T = 85} x {pow2, exact}.

The question is not "is exact better" - that is settled at 60,000 steps
elsewhere.  It is whether the T = 85 / T = 20 GAP closes when the normaliser
stops wasting the budget.  So the table reports the gap in both columns and
the difference of the gaps, which is the actual quantity of interest.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CPT = json.load(open(os.path.join(HERE, "..", "data", "vocab.json")))[
    "chars_per_token_bpe"]


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "runs/smxctx"
    cell = {}
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        if f.endswith("_ckpt.json"):
            continue
        m = json.load(open(f))
        _, T, nm, s = m["name"].split("_")
        cell.setdefault((int(T[1:]), nm), []).append(m["val"] / CPT)

    print("%-6s %-24s %-24s" % ("", "pow2 (shipped)", "exact"))
    print("%-6s %-8s %-8s %-7s %-8s %-8s %-7s"
          % ("T", "seed1", "seed2", "mean", "seed1", "seed2", "mean"))
    means = {}
    for T in sorted(set(k[0] for k in cell)):
        row = [T]
        for nm in ("pow2", "exact"):
            v = sorted(cell.get((T, nm), []))
            if len(v) == 2:
                means[(T, nm)] = sum(v) / 2
                row += ["%.4f" % v[0], "%.4f" % v[1], "%.4f" % (sum(v) / 2)]
            else:
                row += ["-", "-", "-"]
        print("%-6d %-8s %-8s %-7s %-8s %-8s %-7s" % tuple(row))

    print()
    for nm in ("pow2", "exact"):
        if (20, nm) in means and (85, nm) in means:
            g = means[(85, nm)] - means[(20, nm)]
            print("%-6s T=85 minus T=20: %+.4f nats/char  (%s)"
                  % (nm, g, "longer context HURTS" if g > 0 else
                     "longer context HELPS"))
    if all((T, nm) in means for T in (20, 85) for nm in ("pow2", "exact")):
        gp = means[(85, "pow2")] - means[(20, "pow2")]
        ge = means[(85, "exact")] - means[(20, "exact")]
        print("\nthe gap moved by %+.4f nats/char" % (ge - gp))
        print("(negative = fixing the softmax made longer context relatively "
              "better; it has to CROSS ZERO for longer context to be worth it)")
    print("\nnats per character, held out, lower is better. 12,000 steps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
