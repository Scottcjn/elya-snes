#!/usr/bin/env python3
"""The router: which topic shard answers this question.

FINDINGS entry 11 measured five topic shards at 61.8% held-out exact against
38.0% for one model over all five topics -- **assuming a router that is always
right**.  There is no such thing.  This file is the router, and the gap between
its accuracy and 100% is what the shipped number pays.

THE RULE THIS FILE OBEYS: dumb first, and report the dumb ones even after
something beats them.  The sibling Genesis port reached 91% routing with a
deterministic keyword router.  Five constructions are implemented here; four
are baselines and one ships.

  tok-unique   a TOKEN votes for a topic if it appears in that topic's train
               questions and no other topic's.  Score = votes; argmax.
  tok-counts   score = sum over the question's tokens of an integer weight
               W[token][topic], a rounded log-odds fitted on train.  64 x 5
               signed bytes.
  word-unique  the Genesis's literal shape: a WORD that occurs in exactly one
               topic's train questions votes for it.  Argmax of the votes.
  word-counts  the same log-odds scorer over whole words instead of tokens.
               THIS IS THE ONE THAT SHIPS.
  char-ngram   log-odds over character 2..5-grams, to answer the obvious
               objection that word matching is too brittle for paraphrase.

Measured, fitted on the 208 train questions only.  dev is the selection set;
test and the dev+test total are reported alongside because a router's error
rate is the headline result of this work and hiding half of it would be a
choice:

    arm            dev     test    dev+test    table
    tok-unique    27.9%   30.4%     29.2%      320 B
    tok-counts    35.3%   49.3%     42.3%      320 B
    word-unique   61.8%   69.6%     65.7%      1.4 KB
    word-counts   64.7%   72.5%     68.6%      1.6 KB   <- ships
    char-ngram    66.2%   69.6%     67.9%     25.8 KB

`char-ngram` is one dev question ahead of `word-counts` and 2,659 table
entries fatter.  One question out of 68 is well inside the +-6 points of
binomial noise FINDINGS entry 11 measured on sets this size, so dev does not
distinguish the two arms; 25.8 KB of ROM and a substring scan against 1.6 KB
and a word compare does.  The cheap one ships and the expensive one is
reported.

**The 64-token vocabulary is what costs the token routers their accuracy.**
' the ', 'coin', 'how m' and fifty-eight single letters do not carry topic:
`what machine? ` and `what depth? ` share every token but one.  Words do carry
it, and the router gets its whole signal from the handful of words the corpus
does not share between topics.

Everything is fitted on the TRAIN split.  A router fitted on dev or test would
make the routed accuracy a train-set score, which is the mistake this repo has
spent two entries not making.

WHAT WAS TRIED AND LOST, so nobody spends the cycles again:

  * Model confidence.  Feed the question to all five shards and pick the one
    that finds it least surprising - log P(question) under each shard, which
    is the textbook generative classifier and costs five forward passes per
    answer on the cartridge.  Measured 51.8 / 57.7 / 62.0% over three seeds,
    BELOW the free word router.  The margin at the first answer token is worse
    still: 37.2 / 48.9 / 42.3%.
  * A real classifier.  TF-IDF + logistic regression over char 2..5-grams
    reaches 73.0% held-out and over words 72.3% - four points above the
    shipped router, for something that does not fit in 65816 without a
    floating-point library.  So the shipped router is within four points of
    what ANY bag-of-features model does on this corpus, and the remaining
    ~27% is not a router-quality problem: `location? `, `capacity? `,
    `slowish? `, `preset? `, `the spike? ` and `your limits? ` share no word
    with any training question of their own topic.

The weights are quantised to signed bytes HERE, and the host router scores
with the quantised integers, so rom/game.inc's arithmetic and this file's are
the same arithmetic rather than two roundings of one idea.
"""
import argparse
import collections
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import corpus as C

# The scorer's fixed point.  A log-odds is multiplied by SCALE and rounded into
# a signed byte, so the cartridge holds one row of five weights per word and
# the score is five 16-bit adds per word of the question.
# SCALE was chosen on DEV, which is what dev is for, and the choice is a
# plateau rather than a peak: dev is 64.7% at every scale from 24 to 128 and
# falls to 61.8% below 12.  32 is the smallest value on the plateau at which no
# weight clips against the signed-byte range (64 clips five, 96 clips forty-two),
# so nothing about the table is silently truncated.
SCALE = 32
WMIN, WMAX = -128, 127
ALPHA = 0.5             # Laplace smoothing on the per-topic counts


def load_vocab(path=None):
    p = path or os.path.join(HERE, "..", "data", "vocab.json")
    return json.load(open(p))["vocab"]


def encode(text, vocab):
    """Longest match over the 64 strings -- the tokeniser tools/mkgame.py and
    train/eval_answers.py both use, restated so this file imports cleanly."""
    order = sorted(range(len(vocab)), key=lambda i: -len(vocab[i]))
    out, i = [], 0
    while i < len(text):
        for t in order:
            if vocab[t] and text.startswith(vocab[t], i):
                out.append(t)
                i += len(vocab[t])
                break
        else:
            raise SystemExit("cannot tokenise at %r" % text[i:])
    return out


def words(q):
    """The question's words.  '?' and '.' are separators and not part of a
    word, so `you? ` and `you ` are the same feature -- rom/game.inc splits
    the same way, on anything that is not a-z."""
    out, cur = [], []
    for ch in q:
        if "a" <= ch <= "z":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def ngrams(q, lo=2, hi=5):
    s = q.strip()
    return [s[i:i + n] for n in range(lo, hi + 1)
            for i in range(len(s) - n + 1)]


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------
def fit_unique(rows, feat):
    """W[f][topic] = 1 if f occurs in that topic's train questions and no
    other topic's, else 0.  The keyword router with the keywords found rather
    than chosen by hand."""
    nT = len(C.TOPICS)
    seen = collections.defaultdict(lambda: [0] * nT)
    for topic, q, _a, _h in rows:
        ti = C.TOPICS.index(topic)
        for f in set(feat(q)):
            seen[f][ti] = 1
    W = {}
    for f, s in seen.items():
        if sum(s) == 1:
            W[f] = [1 if i == s.index(1) else 0 for i in range(nT)]
    return W


def fit_counts(rows, feat, scale=SCALE):
    """W[f][topic] = clamp(round(scale * log(P(f|topic) / P(f)))).

    The per-topic prior is deliberately absent: including it makes the router
    prefer the largest topic on a question with no signal at all, and a tie
    broken by topic size is not more honest than a tie broken by index."""
    nT = len(C.TOPICS)
    cnt = collections.defaultdict(lambda: [0] * nT)
    tot = [0] * nT
    for topic, q, _a, _h in rows:
        ti = C.TOPICS.index(topic)
        for f in feat(q):
            cnt[f][ti] += 1
            tot[ti] += 1
    nfeat = len(cnt)
    grand = sum(tot)
    W = {}
    for f, c in cnt.items():
        row = []
        for ti in range(nT):
            p = (c[ti] + ALPHA) / (tot[ti] + ALPHA * nfeat)
            pa = (sum(c) + ALPHA * nT) / (grand + ALPHA * nfeat * nT)
            row.append(max(WMIN, min(WMAX, int(round(scale * math.log(p / pa))))))
        W[f] = row
    return W


def score(q, W, feat):
    nT = len(C.TOPICS)
    s = [0] * nT
    for f in feat(q):
        w = W.get(f)
        if w:
            for i in range(nT):
                s[i] += w[i]
    return s


def argmax_low(s):
    """Ties to the LOWEST topic index, which is what rom/game.inc does: it
    keeps the running best and only replaces it on a strict >."""
    best = 0
    for i in range(1, len(s)):
        if s[i] > s[best]:
            best = i
    return best


# ---------------------------------------------------------------------------
# the shipped router
# ---------------------------------------------------------------------------
class Router:
    """word-counts.  Integer weights, integer score, ties to topic 0."""

    kind = "word-counts"

    def __init__(self, W):
        self.W = W

    def scores(self, q):
        return score(q, self.W, words)

    def topic(self, q):
        return argmax_low(self.scores(q))

    def table(self):
        """(word, [w0..w4]) in the order rom/game.inc searches -- longest
        first, so a linear scan cannot match a prefix of a longer word before
        the word itself.  The ROM compares whole words, so the order is
        cosmetic there and load-bearing only for tools/mkrouter.py's own
        round-trip test."""
        return sorted(self.W.items(), key=lambda kv: (-len(kv[0]), kv[0]))


def build(rows=None):
    C.check()
    return Router(fit_counts(rows if rows is not None else C.rows_of("train"),
                             words))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", default=None)
    ap.add_argument("--confusion", action="store_true")
    ap.add_argument("--dump-wrong", action="store_true")
    ap.add_argument("--dump-table", action="store_true")
    a = ap.parse_args()

    vocab = load_vocab(a.vocab)
    C.check()
    tr = C.rows_of("train")
    toks = lambda q: encode(q, vocab)                             # noqa: E731

    arms = [
        ("tok-unique", fit_unique(tr, toks), toks),
        ("tok-counts", fit_counts(tr, toks), toks),
        ("word-unique", fit_unique(tr, words), words),
        ("word-counts", fit_counts(tr, words), words),
        ("char-ngram", fit_counts(tr, ngrams), ngrams),
    ]

    print("router accuracy -- every arm fitted on the %d TRAIN questions only,"
          " integer weights\n" % len(tr))
    print("%-12s %s" % ("arm", "  ".join("%-14s" % s for s in
                                         ("train", "dev", "test", "dev+test"))))
    for name, W, feat in arms:
        cells = []
        for rows in (C.rows_of("train"), C.rows_of("dev"), C.rows_of("test"),
                     C.rows_of("dev") + C.rows_of("test")):
            ok = sum(1 for t, q, _a, _h in rows
                     if C.TOPICS[argmax_low(score(q, W, feat))] == t)
            cells.append("%3d/%-3d %5.1f%%" % (ok, len(rows),
                                               100 * ok / len(rows)))
        print("%-12s %s" % (name, "  ".join("%-14s" % c for c in cells)))

    r = build(tr)
    print("\nshipped: %s, %d words x 5 signed bytes = %d bytes of table"
          % (r.kind, len(r.W), 5 * len(r.W)))

    held = C.rows_of("dev") + C.rows_of("test")
    if a.confusion:
        print("\nheld-out confusion (row = true topic, col = routed)")
        print("%-9s %s" % ("", " ".join("%9s" % t for t in C.TOPICS)))
        for t in C.TOPICS:
            row = [0] * len(C.TOPICS)
            for topic, q, _a, _h in held:
                if topic == t:
                    row[r.topic(q)] += 1
            print("%-9s %s" % (t, " ".join("%9d" % x for x in row)))
    if a.dump_wrong:
        print("\nevery held-out question the shipped router sends to the wrong "
              "shard")
        for topic, q, ans, _h in held:
            p = r.topic(q)
            if C.TOPICS[p] != topic:
                print("  %-22r %-9s -> %-9s  (%r)" % (q, topic, C.TOPICS[p],
                                                      ans))
    if a.dump_table:
        print("\nthe table")
        for w, ws in r.table():
            print("  %-12s %s" % (w, " ".join("%4d" % x for x in ws)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
