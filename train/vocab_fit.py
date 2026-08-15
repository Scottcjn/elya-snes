#!/usr/bin/env python3
"""What a vocabulary choice costs in rows that do not fit 20 positions.

train/prep_qa.py prints its over-budget rows and dies on the fatal ones, which
is right for a build step and useless for choosing between fitter settings: the
question is not "did it die" but "how many TRAINING rows would have to be
shortened by hand, and how many HELD-OUT questions become unanswerable at any
model quality".  Those are different costs and only the second one is a
measurement.

The frozen 137 held-out questions of the 34-fact corpus are counted separately,
because a question that fitted before the corpus grew and does not fit after is
a regression the growth caused, not a limit of the machine.

Nothing here reads a model, and the vocabulary is fitted on the TRAINING split
exactly as prep_qa.py fits it.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import prep_qa as P


def fit(cap, shadow, shadow_w):
    rows = C.qa_rows()
    P.set_base("".join(q + a for _t, q, a, _s in rows) + "".join(C.MONOLOGUE))
    train = [(t, q, a) for t, q, a, s in rows if s == "train"]
    items = [(q, a) for _t, q, a in train] + [(m,) for m in C.MONOLOGUE]
    wts = [1.0] * len(items)
    buds = [P.T] * len(items)
    if shadow:
        for a in sorted({a for _t, _q, a in train}):
            items.append((a,))
            wts.append(shadow_w)
            buds.append(P.T - shadow)
    extra = P.learn_vocab_greedy(items, wts, budget=P.T, cap=cap, budgets=buds)
    return list(P.BASE) + extra


def score(vocab):
    frozen = set(C.FROZEN137)
    out = {"train": [], "mono": [], "held": [], "frozen": []}
    for t, q, a, s in C.qa_rows():
        n = len(P.encode(q, vocab)) + len(P.encode(a, vocab))
        if n <= P.T:
            continue
        if s == "train":
            out["train"].append((q, a, n))
        else:
            out["held"].append((q, a, n))
            if q in frozen:
                out["frozen"].append((q, a, n))
    for m in C.MONOLOGUE:
        if len(P.encode(m, vocab)) > P.T:
            out["mono"].append(("", m, len(P.encode(m, vocab))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", default="1000")
    ap.add_argument("--shadow", default="0,6,7,8,9,10,11,12")
    ap.add_argument("--shadow-w", default="1")
    ap.add_argument("--dump", action="store_true",
                    help="print the offending rows for the LAST setting")
    a = ap.parse_args()
    C.check()
    print("%6s %7s %8s | %6s %5s %6s %7s"
          % ("cap", "shadow", "shadow_w", "train", "mono", "held", "frozen"))
    last = None
    for cap in [int(x) for x in a.cap.split(",")]:
        for sh in [int(x) for x in a.shadow.split(",")]:
            for w in [float(x) for x in a.shadow_w.split(",")]:
                v = fit(cap, sh, w)
                r = score(v)
                last = r
                print("%6d %7d %8.2f | %6d %5d %6d %7d"
                      % (cap, sh, w, len(r["train"]), len(r["mono"]),
                         len(r["held"]), len(r["frozen"])), flush=True)
    if a.dump and last:
        for k in ("train", "mono", "held", "frozen"):
            for q, ans, n in last[k]:
                print("  %-7s %2d  %r %r" % (k, n, q, ans))
    return 0


if __name__ == "__main__":
    sys.exit(main())
