#!/usr/bin/env python3
"""Routing tables for the NES mixture of experts.

The router is a 64-entry byte table indexed by the CURRENT token id.  On the
6502 that is one `lda route,x` - 4 cycles - against a token that costs
1,116,979.  Nothing else about routing is affordable enough to be interesting
unless it buys loss, so this file's job is to produce several tables that cost
the same and let the training decide between them.

Four constructions, all with the same cartridge cost:

  mod   e = tok % N.  The obvious one, and load-unbalanced: the bpe64 token
        frequencies span 5,621 to 597,458, so tok % 2 already sends 55.2% of
        the corpus to one expert.
  bal   greedy frequency balance - sort by count, hand each token to the
        least-loaded expert.  Equal token mass per expert, no semantics.
  clus  balanced k-means on the rows of the bigram matrix P(next | tok), so
        tokens that predict the same continuations share an expert.  This is
        the only construction that tries to specialise.
  rand  a seeded random assignment.  The control that says whether any of the
        above is doing anything a coin could not.
"""
import numpy as np

V = 64


def _counts(fit):
    return np.bincount(np.asarray(fit, dtype=np.int64), minlength=V).astype(np.float64)


def _bigram(fit):
    """P(next | tok) as a V x V row-stochastic matrix."""
    a = np.asarray(fit, dtype=np.int64)
    m = np.zeros((V, V), dtype=np.float64)
    np.add.at(m, (a[:-1], a[1:]), 1.0)
    m /= np.maximum(m.sum(1, keepdims=True), 1.0)
    return m


def route_mod(n, fit=None, seed=0):
    return [t % n for t in range(V)]


def route_rand(n, fit=None, seed=0):
    g = np.random.default_rng(seed)
    return [int(x) for x in g.integers(0, n, size=V)]


def _greedy_balance(order, n, w, score=None):
    """Hand tokens out in `order`, each to the least-loaded admissible expert.

    score[t][e] (optional) breaks ties by preference; without it the choice is
    purely the lightest expert, which is the load-balance-only construction."""
    load = np.zeros(n)
    out = [0] * V
    # A hard capacity, not a preference.  The first cut of this used "prefer
    # the lightest half", which lets an expert nobody's score likes sit at
    # 1.8% of the corpus forever: N = 8 clustering came out 12.2:1 imbalanced
    # and the tail experts were untrainable.  A cap is the fix.
    cap = w.sum() / n * 1.02
    for t in order:
        if score is None:
            e = int(np.argmin(load))
        else:
            adm = np.where(load + w[t] <= cap)[0]
            if adm.size == 0:
                adm = np.array([int(np.argmin(load))])
            e = int(adm[np.argmin(score[t][adm])])
        out[t] = e
        load[e] += w[t]
    return out, load


def route_bal(n, fit, seed=0):
    c = _counts(fit)
    order = list(np.argsort(c)[::-1])
    return _greedy_balance(order, n, c)[0]


def route_clus(n, fit, seed=0):
    """Balanced k-means over P(next | tok), frequency weighted."""
    c = _counts(fit)
    m = _bigram(fit)
    g = np.random.default_rng(seed)
    # k-means++ style seeding on the frequency-weighted rows
    cen = m[g.choice(V, size=1, p=c / c.sum())]
    while len(cen) < n:
        d = ((m[:, None, :] - cen[None, :, :]) ** 2).sum(-1).min(1) * c
        cen = np.vstack([cen, m[int(np.argmax(d))]])
    for _ in range(50):
        d = ((m[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
        a = d.argmin(1)
        new = np.stack([
            (m[a == e] * c[a == e, None]).sum(0) / max(c[a == e].sum(), 1e-9)
            if (a == e).any() else cen[e]
            for e in range(n)])
        if np.allclose(new, cen):
            break
        cen = new
    d = ((m[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
    order = list(np.argsort(c)[::-1])
    return _greedy_balance(order, n, c, score=d)[0]


BUILDERS = {"mod": route_mod, "bal": route_bal, "clus": route_clus,
            "rand": route_rand}


def build(kind, n, fit, seed=0):
    if n == 1:
        return [0] * V
    return BUILDERS[kind](n, fit, seed)


def report(route, fit, n):
    c = _counts(fit)
    r = np.asarray(route)
    load = np.array([c[r == e].sum() for e in range(n)]) / c.sum()
    return "load %s  (max/min = %.2f)" % (
        " ".join("%.3f" % x for x in load),
        load.max() / max(load.min(), 1e-12))


if __name__ == "__main__":
    fit = np.load("data/fit_bpe64.npy")
    for n in (2, 4, 8):
        for kind in ("mod", "bal", "clus", "rand"):
            r = build(kind, n, fit, seed=1)
            print("N=%d %-5s %s" % (n, kind, report(r, fit, n)))
