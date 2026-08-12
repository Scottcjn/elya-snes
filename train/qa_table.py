#!/usr/bin/env python3
"""Tabulate runs/qa/*.json by ANSWER QUALITY, aggregated over seeds.

Loss is printed only so that the reader can see it does not separate the arms.
On this corpus every arm lands within a few thousandths of a nat of every
other one and the exact-answer rates differ by tens of points, which is the
whole argument for scoring answers instead.
"""
import argparse
import collections
import glob
import json
import os
import statistics
import sys


# what a run that predates a flag was, in fact, doing
DEFAULTS = {"nopos": 0, "qnoise": 0.0, "moe_head": 0, "pw": 1.0, "nexp": 1,
            "route": "bal"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="runs/qa")
    ap.add_argument("--group", default="nexp,route,nopos,moe_head,pw,qnoise",
                    help="comma separated meta keys that define an arm")
    a = ap.parse_args()
    keys = [k for k in a.group.split(",") if k]

    arms = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(a.dir, "*.json"))):
        m = json.load(open(p))
        if "exact_train" not in m:
            continue
        # Older runs predate a flag and simply lack the key.  Defaulting a
        # missing key to "-" splits one arm into two and quietly halves the
        # seed count of both, which is exactly the kind of bookkeeping error
        # that makes a three-seed claim out of a one-seed run.
        arms[tuple(m.get(k, DEFAULTS.get(k, "-")) for k in keys)].append(m)

    if not arms:
        print("no scored runs in %s" % a.dir)
        return 1

    hdr = "  ".join("%-6s" % k[:6] for k in keys)
    print("%s  n  weights   loss    train exact      held exact     degen" % hdr)
    rows = []
    for k, ms in arms.items():
        tr = [m["exact_train"] for m in ms]
        ho = [m["exact_held"] for m in ms]
        rows.append((statistics.mean(tr), k, ms, tr, ho))
    for mean_tr, k, ms, tr, ho in sorted(rows, reverse=True):
        sd = statistics.stdev(tr) if len(tr) > 1 else 0.0
        sdh = statistics.stdev(ho) if len(ho) > 1 else 0.0
        print("%s  %d  %7d  %.4f  %5.1f%% +-%4.1f  %5.1f%% +-%4.1f  %.2f"
              % ("  ".join("%-6s" % str(v)[:6] for v in k), len(ms),
                 ms[0]["weights"], statistics.mean(m["loss"] for m in ms),
                 100 * mean_tr, 100 * sd,
                 100 * statistics.mean(ho), 100 * sdh,
                 statistics.mean(m.get("degen_train", 0) for m in ms)))
        print("        seeds: train %s   held %s"
              % (" ".join("%.0f%%" % (100 * v) for v in tr),
                 " ".join("%.0f%%" % (100 * v) for v in ho)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
