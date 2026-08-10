#!/usr/bin/env python3
"""Turn a dense npz into an N-expert npz whose experts are all IDENTICAL.

This is the control that isolates the mixture's cartridge overhead from its
arithmetic.  A cartridge built from it streams a different bank depending on
the routed token, walks the extra region boundaries, and does one router
lookup per token - but every expert holds the same weights, so it must
generate exactly the tokens the dense cartridge generates.  Any difference in
cycles is therefore structure, and any difference in output is a bug.
"""
import argparse
import sys

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--nexp", type=int, default=4)
    ap.add_argument("--route", default="bal",
                    help="routing kind from train/route.py, or 'spread' for "
                         "tok %% N (which maximises how often the routed bank "
                         "changes between consecutive tokens)")
    a = ap.parse_args()

    z = dict(np.load(a.src))
    if "_moe" in z:
        raise SystemExit("%s is already a mixture" % a.src)
    n = a.nexp
    for k in list(z):
        if k.endswith("_W1") or k.endswith("_W2"):
            for e in range(n):
                z["%s_e%d" % (k, e)] = z[k].copy()
            del z[k]
    if a.route == "spread":
        rt = [t % n for t in range(64)]
    else:
        sys.path.insert(0, "train")
        import route as R
        fit = np.load("data/fit_bpe64.npy")
        rt = R.build(a.route, n, fit, seed=1)
    z["_route"] = np.array(rt, dtype=np.int16)
    z["_moe"] = np.array([n, 1], dtype=np.int16)
    np.savez(a.dst, **z)
    print("wrote %s: %d identical experts, route %s" % (a.dst, n, a.route))


if __name__ == "__main__":
    main()
