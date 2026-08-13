#!/usr/bin/env python3
"""The number that ships: held-out accuracy with a REAL router in the loop.

FINDINGS entry 11 measured five topic shards at 61.8% held-out exact against
38.0% for one model over all five topics, and said in the same paragraph that
61.8% assumes a router which is always right.  This file removes that
assumption.  Four columns, the same questions, the same seeds:

  unsharded   one model over all five topics -- entry 11's shipping recipe
  routed      train/router.py picks a shard from the question, that shard
              answers.  THIS IS WHAT THE CARTRIDGE DOES.
  oracle      the shard for the question's TRUE topic answers.  Unreachable
              by construction; it is entry 11's 61.8% re-derived here so the
              two numbers are produced by one program.
  router      how often the router picked the right shard at all

The gap between `routed` and `oracle` is the router's error rate priced in
answers, and it is the whole result of this work.  A wrong shard is not a
partial credit: the four topics a question does not belong to were never
trained on its fact, so a mis-route scores zero almost by definition -- which
this file checks rather than assumes, by reporting `wrong-shard` (exact answers
produced by a shard the router should not have picked).

Everything is decoded through host/ref.py, exactly as rom/game.inc decodes:
question fed from position 0, the last feed step's output taken as the first
answer token, run to position 19, argmax with ties to the lowest index.  So a
score here is a score the cartridge reproduces, and the gate proves it does.
"""
import argparse
import glob
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import corpus as C
import eval_answers as E
import ref
import router as R


def msd(xs):
    if not xs:
        return float("nan"), float("nan")
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def pct(m, s):
    return "%5.1f +-%4.1f" % (100 * m, 100 * s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", default="runs/qa_shard/shard_%s_s%d.npz")
    ap.add_argument("--whole",
                    default="runs/qa_para/para_qn0.1_qw0.25_st16k_s%d.npz")
    ap.add_argument("--seeds", default="1,2,3,4,5")
    ap.add_argument("--vocab", default="data/vocab.json")
    ap.add_argument("--json", default=None)
    ap.add_argument("--per-topic", action="store_true")
    ap.add_argument("--router", default="wordgram-lr",
                    help="which router build() constructs; 'word-counts' is "
                         "the one this replaced, kept so the before and the "
                         "after come out of one program")
    ap.add_argument("--dump", action="store_true",
                    help="print every held-out question and what came back")
    a = ap.parse_args()

    C.check()
    vocab = E.load_vocab(a.vocab)
    rt = R.build(kind=a.router)
    seeds = [int(s) for s in a.seeds.split(",")]

    # The router is a property of the corpus, not of the seed: one number.
    racc = {}
    for split in ("dev", "test"):
        rows = C.rows_of(split)
        ok = sum(1 for t, q, _x, _h in rows if C.TOPICS[rt.topic(q)] == t)
        racc[split] = (ok, len(rows))
    held = C.rows_of("dev") + C.rows_of("test")
    ok = sum(1 for t, q, _x, _h in held if C.TOPICS[rt.topic(q)] == t)
    racc["dev+test"] = (ok, len(held))

    res = {"router": {k: list(v) for k, v in racc.items()},
           "router_kind": rt.kind, "seeds": seeds, "splits": {}}

    print("router: %s, fitted on the %d train questions only"
          % (rt.kind, len(C.rows_of("train"))))
    for k in ("dev", "test", "dev+test"):
        o, n = racc[k]
        print("   %-9s %3d/%-3d correct shard = %5.1f%%   error %5.1f%%"
              % (k, o, n, 100 * o / n, 100 * (1 - o / n)))
    print()

    for split in ("dev", "test"):
        rows = C.rows_of(split)
        cols = {k: [] for k in ("unsharded", "routed", "oracle", "wrong")}
        per_topic = {t: {"routed": [], "oracle": [], "unsharded": []}
                     for t in C.TOPICS}
        detail = []
        for s in seeds:
            wm = ref.Model.from_npz(a.whole % s)
            sm = {t: ref.Model.from_npz(a.shards % (t, s)) for t in C.TOPICS}
            nu = nr = no = 0
            nwrong_hit = nwrong = 0
            pt = {t: [0, 0, 0, 0] for t in C.TOPICS}   # n, routed, oracle, uns
            for topic, q, want, _h in rows:
                ids = E.encode(q, vocab)
                pick = C.TOPICS[rt.topic(q)]
                gu = E.answer(wm, ids, vocab).rstrip()
                gr = E.answer(sm[pick], ids, vocab).rstrip()
                go = E.answer(sm[topic], ids, vocab).rstrip()
                nu += gu == want
                nr += gr == want
                no += go == want
                pt[topic][0] += 1
                pt[topic][1] += gr == want
                pt[topic][2] += go == want
                pt[topic][3] += gu == want
                if pick != topic:
                    nwrong += 1
                    nwrong_hit += gr == want
                if a.dump and s == seeds[0]:
                    detail.append((topic, q, want, pick, gr, go, gu))
            n = len(rows)
            cols["unsharded"].append(nu / n)
            cols["routed"].append(nr / n)
            cols["oracle"].append(no / n)
            cols["wrong"].append(nwrong_hit / nwrong if nwrong else 0.0)
            for t in C.TOPICS:
                if pt[t][0]:
                    per_topic[t]["routed"].append(pt[t][1] / pt[t][0])
                    per_topic[t]["oracle"].append(pt[t][2] / pt[t][0])
                    per_topic[t]["unsharded"].append(pt[t][3] / pt[t][0])

        o, nq = racc[split]
        print("%s split, %d held-out paraphrases, %d seeds" % (split, nq,
                                                               len(seeds)))
        print("   unsharded          %s" % pct(*msd(cols["unsharded"])))
        print("   routed  (ships)    %s" % pct(*msd(cols["routed"])))
        print("   oracle  (bound)    %s" % pct(*msd(cols["oracle"])))
        du = msd(cols["routed"])[0] - msd(cols["unsharded"])[0]
        dv = msd(cols["oracle"])[0] - msd(cols["routed"])[0]
        print("   routed - unsharded %+5.1f      what sharding actually buys"
              % (100 * du))
        print("   oracle - routed    %+5.1f      what the router costs"
              % (-100 * dv))
        print("   right answer from the wrong shard: %s of %d mis-routes"
              % (pct(*msd(cols["wrong"])), nq - o))
        if a.per_topic:
            print("   %-9s %4s  %-12s %-12s %-12s"
                  % ("topic", "n", "unsharded", "routed", "oracle"))
            for t in C.TOPICS:
                nt = sum(1 for r in rows if r[0] == t)
                print("   %-9s %4d  %-12s %-12s %-12s"
                      % (t, nt, pct(*msd(per_topic[t]["unsharded"])),
                         pct(*msd(per_topic[t]["routed"])),
                         pct(*msd(per_topic[t]["oracle"]))))
        print()
        res["splits"][split] = {k: v for k, v in cols.items()}
        res["splits"][split]["per_topic"] = {
            t: {k: v for k, v in d.items()} for t, d in per_topic.items()}
        if a.dump and detail:
            print("   seed %d, every held-out question" % seeds[0])
            for topic, q, want, pick, gr, go, gu in detail:
                flag = " " if pick == topic else "*"
                print("   %s %-22r %-8s->%-8s want %-18r routed %-18r oracle %r"
                      % (flag, q, topic, pick, want, gr, go))
            print()

    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
