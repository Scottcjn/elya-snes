#!/usr/bin/env python3
"""Choose which trained seed ships as each topic's shard.

SELECTION IS ON DEV, NEVER ON TEST, and that is not a style rule.  FINDINGS
entry 10 had to flag its own headline as optimistic because the seed was picked
with the held-out column visible, which makes the reported number a selection
artefact rather than a measurement.  train/corpus.py carries a dev split for
exactly this, so the test number is what the recipe generalises to and it is
reported whether or not it is flattering.

Prints both columns so the gap between them is visible: a topic where dev picks
a seed that is much worse on test is a topic where five seeds is not enough to
choose from, and that is worth knowing before it ships.
"""
import glob
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "train"))
import corpus as C                                                # noqa: E402

# Where a real collapse would live.  See the note in the loop below: the
# observed maximum over 25 seeds is 0.038.
DEGEN_MAX = 0.20


def main():
    rundir = sys.argv[1] if len(sys.argv) > 1 else "runs/qa_v3_shards"
    install = "--install" in sys.argv
    print("%-9s %-6s %7s %7s %7s   %s"
          % ("topic", "seed", "dev", "test", "degen", "picked from"))
    picked = {}
    missing = []
    for t in C.TOPICS:
        rows = []
        for j in sorted(glob.glob(os.path.join(ROOT, rundir,
                                               "shard_%s_s*.json" % t))):
            d = json.load(open(j))
            dev = d.get("exact_dev")
            tst = d.get("exact_test")
            deg = d.get("degen_dev", 0.0)
            if dev is None:
                continue
            # degen is a FRACTION of the split, not a flag, and the first
            # version of this file treated it as one -- `if not degen` threw
            # out every seed with a single bad answer.  It reported "ALL 5
            # SEEDS DEGENERATE" for honesty shards scoring 73.1% on test, and
            # the same mistake read the training log's "degenerate 1" (a COUNT
            # OF QUESTIONS) as a verdict on the model.
            #
            # Measured over 25 seeds: max degen_dev is 0.038, which is one
            # question of 26, and the median is 0.  Nothing here is remotely a
            # constant-emitter.  So the threshold is set where an actual
            # collapse would live and the number is REPORTED either way, since
            # a fraction that has never exceeded 4% is a fact about the run and
            # not a filter worth applying blind.
            rows.append((dev, tst, deg, j))
        if not rows:
            missing.append(t)
            print("%-9s %-6s %7s %7s   NONE FOUND" % (t, "-", "-", "-"))
            continue
        # highest dev; ties break to the lower seed so the choice is stable
        live = [r for r in rows if r[2] <= DEGEN_MAX]
        if not live:
            print("%-9s %-6s %7s %7s   ALL %d SEEDS OVER %.0f%% DEGENERATE"
                  % (t, "-", "-", "-", len(rows), DEGEN_MAX * 100))
            missing.append(t)
            continue
        live.sort(key=lambda r: (-r[0], r[3]))
        dev, tst, _deg, j = live[0]
        seed = os.path.basename(j).rsplit("_s", 1)[1].split(".")[0]
        npz = j[:-5] + ".npz"
        picked[t] = npz
        print("%-9s s%-5s %6.1f%% %6.1f%%  %5.1f%%   %d of %d seeds usable"
              % (t, seed, dev * 100, tst * 100 if tst is not None else -1,
                 _deg * 100, len(live), len(rows)))
    if missing:
        print("\n%d topic(s) have no trained shard: %s"
              % (len(missing), ", ".join(missing)))
        return 1
    if install:
        os.makedirs(os.path.join(ROOT, "model"), exist_ok=True)
        for t, npz in picked.items():
            dst = os.path.join(ROOT, "model", "elya_shard_%s.npz" % t)
            shutil.copyfile(npz, dst)
            print("installed %s" % os.path.relpath(dst, ROOT))
    else:
        print("\n(--install copies these into model/elya_shard_<topic>.npz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
