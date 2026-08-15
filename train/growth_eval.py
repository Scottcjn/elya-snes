#!/usr/bin/env python3
"""Before and after the corpus growth, on questions that did not move.

Growing the corpus moves the held-out set: new facts bring new dev and test
phrasings, so `dev` before and `dev` after are not the same questions and the
two numbers are not a comparison.  Three question lists ARE the same on both
sides and this file scores those:

  frozen137   every held-out question of the 34-fact corpus.  Still held out
              here, still carrying the same answer; train/corpus.py's check()
              asserts both.
  hole25      the 25 of those 137 that train/route_diag.py --residual named as
              vocabulary holes - every content word occurred exactly once in
              the whole corpus.  Each now has a training phrasing that uses the
              word, so these are the questions the growth made LEXICALLY
              easier, and they are scored apart from the other 112 for exactly
              that reason.  If the gain is all here, the gain is lexical
              coverage and should be described that way.
  legacy35    entry 10's held-out set, a subset of the 137.

`--models` takes any number of npz paths with a `%d` for the seed, so an arm
mean over seeds comes out of one invocation.  A mean of one seed is not a
result on this corpus: 137 questions is +-4 points of binomial noise.

Sharded arms are scored through train/router.py, the same way the cartridge
would: the router picks a shard from the question and that shard answers.
`--oracle` reports the unreachable bound beside it.
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


def pct(m, s):
    return "%5.1f +-%4.1f" % (100 * m, 100 * s)


def sets():
    frozen = C.subset_rows(C.FROZEN137)
    holes = C.subset_rows(C.HOLE25)
    hq = set(C.HOLE25)
    rest = [r for r in frozen if r[1] not in hq]
    return [("frozen137", frozen), ("hole25", holes),
            ("frozen112", rest), ("legacy35", C.legacy_rows()),
            ("dev", C.rows_of("dev")), ("test", C.rows_of("test"))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--whole", default=None,
                    help="npz path with %%d for the seed, one model over all "
                         "five topics")
    ap.add_argument("--shards", default=None,
                    help="npz path with %%s topic and %%d seed")
    ap.add_argument("--seeds", default="1,2,3,4,5")
    ap.add_argument("--vocab", default="data/vocab.json")
    ap.add_argument("--router", default="wordgram-lr")
    ap.add_argument("--router-v1", default=None,
                    help="fit the router on the 34-fact corpus in this "
                         "directory instead of on this one.  The BEFORE arm "
                         "has to be routed by the router that existed then, "
                         "or the comparison moves two things at once.")
    ap.add_argument("--json", default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("--dump", default=None,
                    help="print every question of this set for the first seed")
    a = ap.parse_args()

    C.check()
    vocab = E.load_vocab(a.vocab)
    seeds = [int(s) for s in a.seeds.split(",")]
    res = {"label": a.label, "seeds": seeds, "sets": {}}

    rt = None
    if a.shards:
        import router as R
        if a.router_v1:
            import importlib.util
            sp = importlib.util.spec_from_file_location(
                "corpus_v1", os.path.join(a.router_v1, "corpus.py"))
            C1 = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(C1)
            tr = [(t, q, ans, False)
                  for t, q, ans, s in C1.qa_rows() if s == "train"]
            W = R.fit_lr(tr, R.wordgrams)

            class _RT:
                kind = "wordgram-lr/v1"

                def topic(self, q):
                    return R.argmax_low(R.score(q, W, R.wordgrams))
            rt = _RT()
            print("router fitted on the 34-fact corpus (%d train questions)"
                  % len(tr))
        else:
            rt = R.build(kind=a.router)

    print("corpus: %d facts  %d questions  (%d train)"
          % (len(C.FACTS), len(C.qa_rows()), len(C.rows_of("train"))))
    if a.label:
        print("arm: %s" % a.label)
    hdr = "%-10s %4s" % ("set", "n")
    if a.whole:
        hdr += "  %-12s" % "unsharded"
    if a.shards:
        hdr += "  %-12s  %-12s" % ("routed", "oracle")
    print(hdr)

    for name, rows in sets():
        cols = {"unsharded": [], "routed": [], "oracle": []}
        dump = []
        for s in seeds:
            wm = ref.Model.from_npz(a.whole % s) if a.whole else None
            sm = ({t: ref.Model.from_npz(a.shards % (t, s)) for t in C.TOPICS}
                  if a.shards else None)
            nu = nr = no = 0
            for topic, q, want, _h in rows:
                ids = E.encode(q, vocab)
                if wm is not None:
                    gu = E.answer(wm, ids, vocab).rstrip()
                    nu += gu == want
                if sm is not None:
                    pick = C.TOPICS[rt.topic(q)]
                    gr = E.answer(sm[pick], ids, vocab).rstrip()
                    go = E.answer(sm[topic], ids, vocab).rstrip()
                    nr += gr == want
                    no += go == want
                    if a.dump == name and s == seeds[0]:
                        dump.append((topic, pick, q, want, gr))
                elif a.dump == name and s == seeds[0]:
                    dump.append((topic, topic, q, want, gu))
            n = len(rows)
            if wm is not None:
                cols["unsharded"].append(nu / n)
            if sm is not None:
                cols["routed"].append(nr / n)
                cols["oracle"].append(no / n)
        line = "%-10s %4d" % (name, len(rows))
        if a.whole:
            line += "  %-12s" % pct(*msd(cols["unsharded"]))
        if a.shards:
            line += "  %-12s  %-12s" % (pct(*msd(cols["routed"])),
                                        pct(*msd(cols["oracle"])))
        print(line, flush=True)
        res["sets"][name] = {"n": len(rows),
                             **{k: v for k, v in cols.items() if v}}
        for topic, pick, q, want, got in dump:
            print("    %s %-22r want %-20r got %r"
                  % (" " if pick == topic else "*", q, want, got))

    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
