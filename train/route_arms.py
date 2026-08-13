#!/usr/bin/env python3
"""Candidate routers, cheapest first, measured on the same splits.

train/route_diag.py located the 31.4% dev+test error: 9 questions whose words
the router has never seen (they fall to TOPICS[0]) and 34 it decided wrong, of
which 25 were decided by a function word -- 'any' five times, 'what' five,
'a' four, 'you' four.  So the two things to try are (i) stop the function
words voting and (ii) give the OOV questions any signal at all.

Every arm here produces the SAME artefact the shipped router produces: a dict
feature -> five signed bytes, scored by integer addition, argmax with ties to
the lowest topic index.  An arm that cannot be quantised into that shape is
not a candidate, because rom/game.inc has no multiplier and no floats.

FEATURES
  words          the shipped one: maximal a-z runs
  words-df2      words seen in >= 2 training questions.  Kills the hapaxes,
                 of which 'any' is the expensive one.
  words-nostop   words that occur in <= S of the five topics' training
                 questions.  The stopword list found rather than written.
  words+stem     words, plus each word truncated to its first K characters, so
                 an unseen 'slowish' can still reach a trained 'slow'.
  words+ng       words, plus character n-grams, so a fully unseen word has
                 something to score with.
  tokens         the 64-entry vocabulary.  Reported because the brief asks for
                 a 64 x 5 learned layer and 320 weights is the cheapest table
                 that could possibly work.
  words+tokens   both, one table.

FITTERS
  counts         the shipped log-odds, quantised.  Generative.
  lr             multinomial logistic regression, L2, then quantised to the
                 same signed bytes.  Discriminative: a feature that does not
                 help train accuracy gets shrunk, which is exactly the
                 treatment 'any' needs.
  perc           averaged perceptron, integers throughout, no float step at
                 all.  Kept because it is the only fitter here whose output is
                 native to the target machine.

Selection is on DEV.  test is printed in the same table because hiding it
until the end would not make it any less looked at, but nothing is chosen on
it and the report says which arm was chosen on what.
"""
import argparse
import collections
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import router as R

NT = len(C.TOPICS)
WMIN, WMAX = -128, 127


# ---------------------------------------------------------------------------
# feature extractors
# ---------------------------------------------------------------------------
def f_words(q):
    return R.words(q)


def f_tokens_factory(vocab):
    cache = {}

    def f(q):
        if q not in cache:
            cache[q] = ["t%d" % t for t in R.encode(q, vocab)]
        return cache[q]
    return f


def f_stem_factory(k):
    def f(q):
        out = []
        for w in R.words(q):
            out.append(w)
            if len(w) > k:
                out.append("^" + w[:k])
        return out
    return f


def f_suffix_factory(k):
    def f(q):
        out = []
        for w in R.words(q):
            if len(w) > k:
                out.append("$" + w[-k:])
        return out
    return f


def f_ng_factory(lo, hi):
    def f(q):
        out = list(R.words(q))
        for w in R.words(q):
            p = "<" + w + ">"
            for n in range(lo, hi + 1):
                for i in range(len(p) - n + 1):
                    out.append("#" + p[i:i + n])
        return out
    return f


def f_union(fs):
    def f(q):
        out = []
        for g in fs:
            out.extend(g(q))
        return out
    return f


# ---------------------------------------------------------------------------
# feature filters, applied to the fitted table
# ---------------------------------------------------------------------------
def df_of(rows, feat):
    d = collections.Counter()
    for _t, q, _a, _h in rows:
        for f in set(feat(q)):
            d[f] += 1
    return d


def topics_of(rows, feat):
    d = collections.defaultdict(set)
    for t, q, _a, _h in rows:
        for f in set(feat(q)):
            d[f].add(t)
    return d


# ---------------------------------------------------------------------------
# fitters -- every one returns {feature: [5 signed bytes]}
# ---------------------------------------------------------------------------
def fit_counts(rows, feat, scale=32):
    return R.fit_counts(rows, feat, scale=scale)


def _matrix(rows, feat, index=None, binary=True):
    if index is None:
        index = {}
        for _t, q, _a, _h in rows:
            for f in feat(q):
                index.setdefault(f, len(index))
    X = np.zeros((len(rows), len(index)), dtype=np.float64)
    y = np.zeros(len(rows), dtype=np.int64)
    for i, (t, q, _a, _h) in enumerate(rows):
        y[i] = C.TOPICS.index(t)
        for f in feat(q):
            j = index.get(f)
            if j is not None:
                X[i, j] = 1.0 if binary else X[i, j] + 1.0
    return X, y, index


def quantise(Wf, index, headroom=WMAX):
    """Scale a float weight matrix so the largest magnitude lands on +-127 and
    round.  One scale for the whole table, so relative weights survive."""
    m = np.abs(Wf).max()
    if m == 0:
        s = 1.0
    else:
        s = headroom / m
    Wq = np.clip(np.rint(Wf * s), WMIN, WMAX).astype(int)
    return {f: [int(x) for x in Wq[j]] for f, j in index.items()}


def fit_lr(rows, feat, creg=3.0):
    """train/router.py's, delegated rather than restated -- two quantisations
    of one idea is how the shipped router stops being the measured one."""
    return R.fit_lr(rows, feat, creg=creg)


def fit_perc(rows, feat, epochs=30, seed=0):
    """Averaged perceptron.  Integer weights, integer updates, integer output;
    the only fitter here that never touches a float."""
    rng = np.random.RandomState(seed)
    index = {}
    for _t, q, _a, _h in rows:
        for f in feat(q):
            index.setdefault(f, len(index))
    n = len(index)
    W = np.zeros((n, NT), dtype=np.int64)
    A = np.zeros((n, NT), dtype=np.int64)
    c = 1
    order = list(range(len(rows)))
    for _e in range(epochs):
        rng.shuffle(order)
        for i in order:
            t, q, _a, _h = rows[i]
            gold = C.TOPICS.index(t)
            js = [index[f] for f in feat(q) if f in index]
            s = W[js].sum(axis=0) if js else np.zeros(NT, dtype=np.int64)
            pred = int(np.argmax(s[::-1]))
            pred = NT - 1 - pred            # ties to the LOWEST index
            if pred != gold:
                for j in js:
                    W[j, gold] += 1
                    W[j, pred] -= 1
                    A[j, gold] += c
                    A[j, pred] -= c
            c += 1
    Wavg = W.astype(np.float64) - A.astype(np.float64) / c
    return quantise(Wavg, index)


# ---------------------------------------------------------------------------
def acc(W, feat, rows):
    ok = 0
    for t, q, _a, _h in rows:
        s = [0] * NT
        for f in feat(q):
            w = W.get(f)
            if w:
                for i in range(NT):
                    s[i] += w[i]
        if C.TOPICS[R.argmax_low(s)] == t:
            ok += 1
    return ok, len(rows)


def restrict(W, keep):
    return {f: w for f, w in W.items() if f in keep}


ARMS = []                # (name, feature-factory, fitter, note)


def _reg(name, featf, fitf, note=""):
    ARMS.append((name, featf, fitf, note))


def _filtered(fitf, topmax=None, dfmin=None):
    """Fit, then drop the features a filter rejects.  The filter is measured on
    the same rows the fit saw, so a CV fold refits its own filter and nothing
    leaks across the split."""
    def f(rows, feat):
        W = fitf(rows, feat)
        keep = set(W)
        if topmax is not None:
            tp = topics_of(rows, feat)
            keep &= {k for k, ts in tp.items() if len(ts) <= topmax}
        if dfmin is not None:
            d = df_of(rows, feat)
            keep &= {k for k, c in d.items() if c >= dfmin}
        return restrict(W, keep)
    return f


def build_registry(vocab):
    """Populate ARMS.  A feature-factory takes no arguments and returns the
    extractor, so a CV fold and the real fit use the identical function."""
    del ARMS[:]
    f_tok = f_tokens_factory(vocab)
    W = lambda: f_words                                          # noqa: E731
    _reg("words/counts  (SHIPS)", W, fit_counts)
    _reg("words/lr", W, fit_lr)
    _reg("words/perc", W, fit_perc)
    _reg("words-top<=2/counts", W, _filtered(fit_counts, topmax=2))
    _reg("words-top<=2/lr", W, _filtered(fit_lr, topmax=2))
    _reg("words-df2/counts", W, _filtered(fit_counts, dfmin=2))
    for k in (3, 4, 5):
        _reg("words+stem%d/counts" % k, (lambda k=k: f_stem_factory(k)),
             fit_counts)
        _reg("words+stem%d/lr" % k, (lambda k=k: f_stem_factory(k)), fit_lr)
    _reg("words+stem34/counts", (lambda: f_union([f_stem_factory(3),
                                                  f_stem_factory(4)])),
         fit_counts)
    _reg("words+stem34/lr", (lambda: f_union([f_stem_factory(3),
                                              f_stem_factory(4)])), fit_lr)
    _reg("words+stem3+suf3/counts", (lambda: f_union([f_stem_factory(3),
                                                      f_suffix_factory(3)])),
         fit_counts)
    _reg("words+suf3/counts", (lambda: f_union([f_suffix_factory(3)])),
         fit_counts)
    for lo, hi in ((3, 4), (4, 4), (4, 5), (3, 5)):
        _reg("words+ng%d-%d/counts" % (lo, hi),
             (lambda lo=lo, hi=hi: f_ng_factory(lo, hi)), fit_counts)
        _reg("words+ng%d-%d/lr" % (lo, hi),
             (lambda lo=lo, hi=hi: f_ng_factory(lo, hi)), fit_lr)
    for creg in (0.3, 1.0, 3.0, 10.0):
        _reg("words+ng4-4/lr C=%g" % creg, (lambda: f_ng_factory(4, 4)),
             (lambda rows, feat, c=creg: fit_lr(rows, feat, c)),
             "dev cannot tell these apart; cv chooses" if creg == 0.3 else "")
    _reg("words+ng4-4-df2/counts", (lambda: f_ng_factory(4, 4)),
         _filtered(fit_counts, dfmin=2), "table shrunk by dropping hapax grams")
    _reg("words+ng4-4-df3/counts", (lambda: f_ng_factory(4, 4)),
         _filtered(fit_counts, dfmin=3))
    _reg("words+stem3+ng4/counts",
         (lambda: f_union([f_stem_factory(3), f_ng_factory(4, 4)])),
         fit_counts)
    _reg("words+stem3+ng4/lr",
         (lambda: f_union([f_stem_factory(3), f_ng_factory(4, 4)])), fit_lr)
    _reg("words+stem3-top<=2/counts", (lambda: f_stem_factory(3)),
         _filtered(fit_counts, topmax=2))
    _reg("tokens/counts", (lambda: f_tok), fit_counts)
    _reg("tokens/lr", (lambda: f_tok), fit_lr)
    _reg("tokens/perc", (lambda: f_tok), fit_perc)
    _reg("words+tokens/counts", (lambda: f_union([f_words, f_tok])),
         fit_counts)
    return ARMS


def cv_split(rows, seed, frac=0.25):
    """Hold out a fraction of each FACT's training phrasings.  Splitting by
    fact rather than at random is the only split that reproduces the real
    task: an unseen phrasing of a fact whose other phrasings were seen.  A
    plain random split does the same thing here only by luck."""
    import random
    rnd = random.Random(seed)
    byfact = collections.defaultdict(list)
    for r in rows:
        byfact[(r[0], r[2])].append(r)
    fit, held = [], []
    for _k, rs in sorted(byfact.items()):
        rs = list(rs)
        rnd.shuffle(rs)
        n = max(1, int(round(frac * len(rs))))
        held += rs[:n]
        fit += rs[n:]
    return fit, held


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", default="data/vocab.json")
    ap.add_argument("--cv", type=int, default=0,
                    help="also run N-seed cross-validation inside TRAIN")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    C.check()
    vocab = R.load_vocab(a.vocab)
    tr, dv, te = C.rows_of("train"), C.rows_of("dev"), C.rows_of("test")
    arms = build_registry(vocab)
    if a.only:
        want = set(a.only.split(","))
        arms = [x for x in arms if any(w in x[0] for w in want)]

    print("router arms -- fitted on the %d TRAIN questions only, integer\n"
          "weights, argmax with ties to the lowest topic index.\n" % len(tr))
    if a.cv:
        print("cv = %d seeds, a quarter of each fact's TRAIN phrasings held\n"
              "out and refitted from scratch.  It never sees dev or test, so\n"
              "it is the one column an arm cannot be chosen into by luck.\n"
              % a.cv)
    hdr = "%-26s %-11s %-11s %-11s %6s" % ("arm", "train", "dev", "test",
                                           "table")
    if a.cv:
        hdr += "  %-13s" % "cv(train)"
    print(hdr + "  note")
    rowsout = []
    for name, featf, fitf, note in arms:
        feat = featf()
        W = fitf(tr, feat)
        cells = []
        for rows in (tr, dv, te):
            ok, n = acc(W, feat, rows)
            cells.append("%3d/%-3d %4.1f%%" % (ok, n, 100 * ok / n))
        line = "%-26s %-11s %-11s %-11s %6d" % (name, cells[0], cells[1],
                                                cells[2], 5 * len(W))
        cvm = None
        if a.cv:
            xs = []
            for s in range(1, a.cv + 1):
                fitr, held = cv_split(tr, s)
                Wc = fitf(fitr, feat)
                ok, n = acc(Wc, feat, held)
                xs.append(ok / n)
            m = sum(xs) / len(xs)
            sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
            cvm = m
            line += "  %5.1f +-%4.1f" % (100 * m, 100 * sd)
        print(line + "  " + note)
        rowsout.append((name, acc(W, feat, dv)[0], acc(W, feat, te)[0], cvm))

    print("\nbest on dev : %s" % max(rowsout, key=lambda r: r[1])[0])
    if a.cv:
        print("best on cv  : %s" % max(rowsout, key=lambda r: r[3])[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
