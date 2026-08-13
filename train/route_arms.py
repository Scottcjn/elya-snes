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


def fit_lr(rows, feat, Cinv=1.0, binary=True):
    from sklearn.linear_model import LogisticRegression
    X, y, index = _matrix(rows, feat, binary=binary)
    clf = LogisticRegression(C=Cinv, max_iter=5000, fit_intercept=False)
    clf.fit(X, y)
    Wf = clf.coef_.T                      # (nfeat, nclass)
    full = np.zeros((len(index), NT))
    for k, cls in enumerate(clf.classes_):
        full[:, cls] = Wf[:, k]
    return quantise(full, index)


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


def build_arms(vocab):
    tr = C.rows_of("train")
    f_tok = f_tokens_factory(vocab)
    arms = []

    def add(name, feat, W, note=""):
        arms.append((name, feat, W, note))

    # --- the shipped baseline and its two nearest neighbours ---------------
    add("words/counts  (SHIPS)", f_words, fit_counts(tr, f_words))
    add("words/lr", f_words, fit_lr(tr, f_words))
    add("words/perc", f_words, fit_perc(tr, f_words))

    # --- kill the hapaxes and the stopwords -------------------------------
    d = df_of(tr, f_words)
    tp = topics_of(tr, f_words)
    for k in (2, 3):
        keep = {f for f, c in d.items() if c >= k}
        add("words-df%d/counts" % k, f_words,
            restrict(fit_counts(tr, f_words), keep),
            "%d of %d words kept" % (len(keep), len(d)))
    for s in (2, 3, 4):
        keep = {f for f, ts in tp.items() if len(ts) <= s}
        add("words-top<=%d/counts" % s, f_words,
            restrict(fit_counts(tr, f_words), keep),
            "%d of %d words kept" % (len(keep), len(tp)))
        add("words-top<=%d/lr" % s, f_words,
            restrict(fit_lr(tr, f_words), keep))

    # --- both filters at once ---------------------------------------------
    for s in (2, 3, 4):
        keep = {f for f, ts in tp.items() if len(ts) <= s and d[f] >= 2}
        add("words-df2-top<=%d/counts" % s, f_words,
            restrict(fit_counts(tr, f_words), keep),
            "%d of %d words kept" % (len(keep), len(tp)))
        add("words-df2-top<=%d/lr" % s, f_words,
            restrict(fit_lr(tr, f_words), keep))

    # --- give the OOV questions something ---------------------------------
    for k in (3, 4, 5):
        fs = f_stem_factory(k)
        add("words+stem%d/counts" % k, fs, fit_counts(tr, fs))
        add("words+stem%d/lr" % k, fs, fit_lr(tr, fs))
    for lo, hi in ((3, 4), (3, 5), (4, 4)):
        fn = f_ng_factory(lo, hi)
        add("words+ng%d-%d/counts" % (lo, hi), fn, fit_counts(tr, fn))
        add("words+ng%d-%d/lr" % (lo, hi), fn, fit_lr(tr, fn))

    # --- the 64 x 5 layer the brief asks for ------------------------------
    add("tokens/counts", f_tok, fit_counts(tr, f_tok))
    add("tokens/lr", f_tok, fit_lr(tr, f_tok))
    add("tokens/perc", f_tok, fit_perc(tr, f_tok))
    fu = f_union([f_words, f_tok])
    add("words+tokens/counts", fu, fit_counts(tr, fu))
    add("words+tokens/lr", fu, fit_lr(tr, fu))
    return arms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", default="data/vocab.json")
    a = ap.parse_args()
    C.check()
    vocab = R.load_vocab(a.vocab)
    tr, dv, te = C.rows_of("train"), C.rows_of("dev"), C.rows_of("test")

    print("router arms -- every one fitted on the %d TRAIN questions only,\n"
          "integer weights, argmax with ties to the lowest topic index.\n"
          "DEV is the selection set.\n" % len(tr))
    print("%-26s %-11s %-11s %-11s %6s  %s"
          % ("arm", "train", "dev", "test", "table", "note"))
    best = None
    for name, feat, W, note in build_arms(vocab):
        cells = []
        for rows in (tr, dv, te):
            ok, n = acc(W, feat, rows)
            cells.append("%3d/%-3d %4.1f%%" % (ok, n, 100 * ok / n))
        dev_ok = acc(W, feat, dv)[0]
        if best is None or dev_ok > best[0]:
            best = (dev_ok, name)
        print("%-26s %-11s %-11s %-11s %6d  %s"
              % (name, cells[0], cells[1], cells[2], 5 * len(W), note))
    print("\nbest on dev: %s (%d/%d)" % (best[1], best[0], len(dv)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
