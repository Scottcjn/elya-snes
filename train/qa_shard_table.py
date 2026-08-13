#!/usr/bin/env python3
"""Shards against the whole-corpus model, topic by topic, on the same rows.

train/qa_shards.sh trains one model per topic and scores it on that topic's
held-out paraphrases.  That number means nothing on its own: a shard answering
17 identity questions is not comparable to a model answering 69 questions of
five kinds.  This re-scores the WHOLE-CORPUS runs of the same recipe on the
same per-topic subsets, so the two columns are the identical questions and the
only difference is what the model was trained on.

The comparison is deliberately generous to the shards.  Each is asked only
about its own topic, which assumes a router that is always right - and there is
no router in rom/nn.s to be right or wrong (FINDINGS entry 10).  So the shard
column is an upper bound on what topic sharding could buy this port, and if it
does not clear the whole-corpus column here it cannot clear it on a cartridge.
"""
import argparse
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import corpus as C
import eval_answers as E
import ref


def msd(xs):
    if not xs:
        return float("nan"), float("nan")
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", default="runs/qa_shard/shard_*.json")
    ap.add_argument("--whole", default="runs/qa_para/para_qn0.1_qw0.25_st16k_s*.npz",
                    help="npz files of the whole-corpus arm to compare against")
    ap.add_argument("--vocab", default="data/vocab.json")
    ap.add_argument("--split", default="test", choices=("dev", "test"))
    a = ap.parse_args()

    C.check()
    vocab = E.load_vocab(a.vocab)
    rows = C.rows_of(a.split)

    # Whole-corpus models, re-scored per topic.  Decoded here rather than read
    # from the run json, because the json holds one number over all five topics
    # and the comparison needs the five separately.
    whole = {t: [] for t in C.TOPICS}
    npzs = sorted(glob.glob(a.whole))
    for p in npzs:
        m = ref.Model.from_npz(p)
        for t in C.TOPICS:
            sub = [r for r in rows if r[0] == t]
            n = sum(1 for _t, q, want, _h in sub
                    if E.answer(m, E.encode(q, vocab), vocab).rstrip() == want)
            whole[t].append(n / len(sub))

    shard = {t: [] for t in C.TOPICS}
    for p in sorted(glob.glob(a.shards)):
        j = json.load(open(p))
        if j.get("topic") in shard and ("exact_" + a.split) in j:
            shard[j["topic"]].append(j["exact_" + a.split])

    print("split %s, %d whole-corpus models, shard seeds per topic below\n"
          % (a.split, len(npzs)))
    print("%-9s %4s   %-16s %-16s %s"
          % ("topic", "n", "whole corpus", "one shard", "shard - whole"))
    tw, ts, wn = [], [], 0
    for t in C.TOPICS:
        n = sum(1 for r in rows if r[0] == t)
        wm, ws = msd(whole[t])
        sm, ss = msd(shard[t])
        print("%-9s %4d   %5.1f +-%5.1f    %5.1f +-%5.1f    %+5.1f"
              % (t, n, 100 * wm, 100 * ws, 100 * sm, 100 * ss,
                 100 * (sm - wm)))
        tw.append((n, wm)); ts.append((n, sm)); wn += n
    # Row-weighted, so the aggregate is the rate over all held-out questions and
    # not the mean of five rates over unequal topics.
    aw = sum(n * m for n, m in tw) / wn
    as_ = sum(n * m for n, m in ts) / wn
    print("%-9s %4d   %5.1f          %5.1f          %+5.1f"
          % ("ALL", wn, 100 * aw, 100 * as_, 100 * (as_ - aw)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
