#!/usr/bin/env python3
"""Aggregate runs/*.json into per-arm means with seed spread.

One run is an anecdote.  Entry 10's positional ablation moved the held-out
number by nine points and the seed spread inside one arm was twenty-one, so a
single-seed comparison on this corpus is noise with a narrative attached.
Everything here is mean +- population sd over the seeds of an arm, and the
per-seed column is printed so the spread is visible rather than summarised.

The columns are the four scores train/eval_answers.py produces:

  train   the phrasings the trainer saw.  Saturated by design; not the number.
  dev     held-out paraphrases, used to CHOOSE the arm and the seed.
  test    held-out paraphrases, not looked at until the arm is chosen.
  legacy  the 35 questions entry 10 held out, still held out, so the before
          and after are the same 35 questions.
  held    what entry 10's trainer called held-out.  On the entry-10 corpus it
          IS the legacy 35; runs made before the dev/test split have only this
          column, and it is printed so the two eras aggregate in one table.
"""
import argparse
import collections
import glob
import json
import math
import os
import sys


KEYS = ("exact_train", "exact_dev", "exact_test", "exact_legacy", "exact_held")
HEAD = ("train", "dev", "test", "legacy", "held")


def arm_of(meta):
    """Everything that defines the arm except the seed."""
    return ("e%d" % meta.get("nexp", 1),
            "pos" if not meta.get("nopos") else "nopos",
            "qn%g" % meta.get("qnoise", 0.0),
            "qw%g" % meta.get("qw", 1.0),
            "st%d" % meta.get("steps", 0))


def msd(xs):
    if not xs:
        return float("nan"), float("nan")
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("globs", nargs="+")
    ap.add_argument("--sort", default="exact_dev", choices=KEYS)
    ap.add_argument("--show", default="exact_dev", choices=KEYS,
                    help="which column the per-seed list prints")
    a = ap.parse_args()

    metas = []
    for g in a.globs:
        for p in sorted(glob.glob(g)):
            m = json.load(open(p))
            if "exact_train" not in m:
                continue
            m["_path"] = p
            metas.append(m)
    if not metas:
        print("no scored runs matched")
        return 1

    arms = collections.defaultdict(list)
    for m in metas:
        arms[arm_of(m)].append(m)

    print("%-34s %2s  %s   seeds (%s)"
          % ("arm", "n",
             "  ".join("%13s" % h for h in HEAD), a.show.replace("exact_", "")))
    rows = []
    for k, ms in arms.items():
        ms.sort(key=lambda m: m.get("seed", 0))
        cells = []
        for key in KEYS:
            xs = [m[key] for m in ms if key in m]
            mean, sd = msd(xs)
            cells.append("%5.1f +-%5.1f" % (100 * mean, 100 * sd)
                         if xs else "        -    ")
        seeds = " ".join("%.0f" % (100 * m[a.show]) for m in ms if a.show in m)
        srt = msd([m[a.sort] for m in ms if a.sort in m])[0]
        rows.append((srt, "%-34s %2d  %s   %s"
                     % (" ".join(k), len(ms), "  ".join(cells), seeds)))
    for _s, line in sorted(rows, key=lambda r: -(r[0] if r[0] == r[0] else -1)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
