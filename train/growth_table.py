#!/usr/bin/env python3
"""One table for the whole corpus growth, out of the runs it was measured in.

Four arms, all scored on the SAME 137 held-out questions - the held-out set of
the 34-fact corpus, still held out here and still carrying the same answers,
which `corpus.check()` asserts:

  before          34 facts, the router fitted on their 208 training questions
  ablation/old    the same 34 facts WITH the new coverage for the 25 orphaned
                  words, scored through the OLD router.  Isolates what the
                  coverage does to the ANSWER model.
  ablation/new    the same models through the NEW router.  The difference from
                  the line above is the router alone.
  after           70 facts and the new coverage, the new router.  The
                  difference from ablation/new is the fact count alone.

Reads the JSON train/growth_eval.py writes.  No model is loaded here and no
number is recomputed: this is arithmetic on measurements.
"""
import argparse
import json
import math
import os
import sys

SETS = ("frozen137", "hole25", "frozen112", "legacy35")
COLS = ("unsharded", "routed", "oracle")


def msd(xs):
    m = sum(xs) / len(xs)
    return 100 * m, 100 * math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default="runs/reports/growth_before.json")
    ap.add_argument("--abl-old",
                    default="runs/reports/growth_v1abl_oldrouter.json")
    ap.add_argument("--abl-new", default="runs/reports/growth_v1abl.json")
    ap.add_argument("--after", default="runs/reports/growth_after.json")
    a = ap.parse_args()

    arms = [("before        34f, old router", a.before),
            ("ablation/old  34f+cover, old router", a.abl_old),
            ("ablation/new  34f+cover, new router", a.abl_new),
            ("after         70f+cover, new router", a.after)]
    d = {}
    for name, path in arms:
        if not os.path.exists(path):
            print("missing: %s" % path, file=sys.stderr)
            return 1
        d[name] = json.load(open(path))

    for s in SETS:
        n = d[arms[0][0]]["sets"][s]["n"]
        print("\n%s, %d held-out questions, five seeds" % (s, n))
        print("   %-36s %-13s %-13s %-13s"
              % ("arm", "unsharded", "routed", "oracle"))
        for name, _p in arms:
            row = "   %-36s" % name
            for c in COLS:
                xs = d[name]["sets"][s].get(c)
                row += " %5.1f +-%4.1f " % msd(xs) if xs else " %13s" % "-"
            print(row)
        # the two differences the ablation exists to name
        def r(name):
            return msd(d[name]["sets"][s]["routed"])[0]
        def o(name):
            return msd(d[name]["sets"][s]["oracle"])[0]
        b, ao, an, af = [x[0] for x in arms]
        print("   coverage, answer model only  routed %+5.1f   oracle %+5.1f"
              % (r(ao) - r(b), o(ao) - o(b)))
        print("   coverage, through the router routed %+5.1f"
              % (r(an) - r(ao)))
        print("   doubling the facts           routed %+5.1f   oracle %+5.1f"
              % (r(af) - r(an), o(af) - o(an)))
        print("   net                          routed %+5.1f   oracle %+5.1f"
              % (r(af) - r(b), o(af) - o(b)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
