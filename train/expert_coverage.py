#!/usr/bin/env python3
"""Does the exactness survey actually exercise every expert?

A 64-seed survey that never routes to expert 7 does not test expert 7's header
table, its bank numbers or its copy of the lookup tables - and it would still
report 1,216/1,216.  This counts, from the host reference, how often each
expert is routed over exactly the token trajectories the survey runs.

    train/expert_coverage.py runs/<arm>.npz [nseeds]
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import ref


def main():
    npz = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    m = ref.Model.from_npz(npz)
    ne = max(m.nexp, m.nexp_head)
    cnt = collections.Counter()
    per_seed = []
    for s in range(n):
        toks, _ = ref.generate(m, s, ref.T - 1)
        es = [m.route[t] for t in toks[:ref.T]]
        cnt.update(es)
        per_seed.append(len(set(es)))
    tot = sum(cnt.values())
    print("%s: %d seeds x %d positions = %d routed token-positions"
          % (npz, n, ref.T, tot))
    for e in range(ne):
        print("  expert %-2d %6d  %5.1f%%" % (e, cnt[e], 100.0 * cnt[e] / tot))
    never = [e for e in range(ne) if cnt[e] == 0]
    print("experts never routed: %s" % (never or "none"))
    print("distinct experts per seed: min %d  max %d  mean %.2f"
          % (min(per_seed), max(per_seed), sum(per_seed) / float(n)))
    return 1 if never else 0


if __name__ == "__main__":
    sys.exit(main())
