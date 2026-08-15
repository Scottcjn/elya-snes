#!/usr/bin/env python3
"""The router before and after the corpus grew, on the SAME questions.

train/route_eval.py reports the router against whatever `dev` and `test` are
in train/corpus.py at the time.  When the corpus grows those are different
question sets, so before and after are two numbers and not a comparison.  This
file fits the identical router construction twice - once on the 34-fact
corpus's training split, once on the grown one - and scores both on
`corpus.FROZEN137`, the held-out questions the two corpora share.

The 34-fact corpus is read out of git rather than re-typed:

    git show <commit>:train/corpus.py > /tmp/v1/corpus.py
    python3 train/route_growth.py --v1 /tmp/v1

Both routers are the shipped `wordgram-lr` construction and both are fitted on
training questions only, so the only thing that differs between the columns is
which corpus the training questions came from.
"""
import argparse
import collections
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import router as R


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fit_on(mod):
    """The shipped router, fitted on `mod`'s training split."""
    tr = [(t, q, a, False) for t, q, a, s in mod.qa_rows() if s == "train"]
    return R.fit_lr(tr, R.wordgrams), tr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True,
                    help="directory holding the 34-fact corpus.py")
    a = ap.parse_args()
    C.check()
    C1 = load_module(os.path.join(a.v1, "corpus.py"), "corpus_v1")

    W1, tr1 = fit_on(C1)
    W2, tr2 = fit_on(C)
    assert C1.TOPICS == C.TOPICS

    rows = C.subset_rows(C.FROZEN137)
    holes = set(C.HOLE25)

    print("router wordgram-lr, one construction, two training corpora")
    print("   before  %3d facts  %3d train questions" % (len(C1.FACTS), len(tr1)))
    print("   after   %3d facts  %3d train questions" % (len(C.FACTS), len(tr2)))
    print()

    def route(q, W):
        return C.TOPICS[R.argmax_low(R.score(q, W, R.wordgrams))]

    cells = {}
    for name, sel in (("frozen137", lambda q: True),
                      ("hole25", lambda q: q in holes),
                      ("frozen112", lambda q: q not in holes),
                      ("legacy35", lambda q: q in set(C.LEGACY_HELD))):
        sub = [r for r in rows if sel(r[1])]
        b = sum(1 for t, q, _a, _h in sub if route(q, W1) == t)
        af = sum(1 for t, q, _a, _h in sub if route(q, W2) == t)
        cells[name] = (b, af, len(sub))

    print("%-10s %4s   %-16s %-16s %s"
          % ("set", "n", "before", "after", "delta"))
    for name in ("frozen137", "hole25", "frozen112", "legacy35"):
        b, af, n = cells[name]
        print("%-10s %4d   %3d/%-3d = %5.1f%%  %3d/%-3d = %5.1f%%  %+5.1f"
              % (name, n, b, n, 100 * b / n, af, n, 100 * af / n,
                 100 * (af - b) / n))

    print("\nthe twenty-five vocabulary holes, one line each")
    for t, q, _a, _h in rows:
        if q not in holes:
            continue
        p1, p2 = route(q, W1), route(q, W2)
        print("   %-22r %-9s  before %-9s %s   after %-9s %s"
              % (q, t, p1, "ok " if p1 == t else "WRONG",
                 p2, "ok" if p2 == t else "WRONG"))

    print("\nconfusion on the frozen 137, after")
    m = collections.Counter()
    for t, q, _a, _h in rows:
        m[(t, route(q, W2))] += 1
    print("   %-10s" % "" + "".join("%9s" % x for x in C.TOPICS))
    for t in C.TOPICS:
        print("   %-10s" % t + "".join("%9d" % m[(t, p)] for p in C.TOPICS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
