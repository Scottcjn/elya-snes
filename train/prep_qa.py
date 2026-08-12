#!/usr/bin/env python3
"""Build the conversational vocabulary and the position-aligned training set.

Two things happen here that did not happen in train/prep_corpus.py, and both
are forced by how rom/game.inc actually runs a conversation:

**The merges are relearned on THIS corpus.**  The shipped 64-slot vocabulary
was fitted to TinyStories, so it spends merges on `he `, `wa`, `ing` and ` the `
- excellent for children's stories and useless for `no. i get things wrong.`
The vocabulary is the only place in this port where context can be bought
without touching the ROM: 20 positions is fixed, but 20 positions of 1.45
chars each and 20 positions of 2.3 chars each are different conversations.
The measured chars/token is printed below and is the number that matters.

**Every example is laid at position 0.**  The positional table is absolute and
rom/game.inc feeds the question starting at POSP = 0.  A trainer that samples
random windows out of a concatenated stream - which is what train_nes.py does
for TinyStories, correctly - shows the model each question at a random offset,
so at run time the question arrives somewhere it has never been.  Here each
example is a fixed T-long row: question, answer, then spaces to the end.

The trailing spaces are not filler.  The ROM has no end-of-sequence token; it
generates exactly `19 - len(prompt)` tokens and prints all of them.  Training
the tail as spaces is how the answer stops on screen.
"""
import argparse
import collections
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C

BASE = "abcdefghijklmnopqrstuvwxyz .,\"'!?"      # identical to prep_corpus.BASE
assert len(BASE) == len(set(BASE)) == 33
TARGET = 64
MAXTOK = 5          # tools/mkgame.py's vocab.tbl row is [len][5 bytes]
T = 20


def enc_char(s):
    return [BASE.index(c) for c in s]


def spell(tid, merges):
    if tid < len(BASE):
        return BASE[tid]
    a, b = merges[tid - len(BASE)]
    return spell(a, merges) + spell(b, merges)


def learn_merges(docs, weights):
    """Greedy most-frequent-pair merges, weighted by how often a line is used.

    Weighting matters here in a way it did not on TinyStories: a question that
    appears three times as three paraphrases is three lines, and an answer that
    35 facts share a shape with is one.  The counts below are over the corpus
    as the model will see it, not over unique strings.
    """
    docs = [list(d) for d in docs]
    merges = []
    while len(BASE) + len(merges) < TARGET:
        cnt = collections.Counter()
        for d, w in zip(docs, weights):
            for a, b in zip(d, d[1:]):
                cnt[(a, b)] += w
        # Longest-token cap: a merge whose spelling would exceed MAXTOK bytes
        # cannot be stored in the ROM's vocab table, so it is not a candidate.
        best = None
        for pair, n in cnt.most_common():
            if len(spell(pair[0], merges)) + len(spell(pair[1], merges)) <= MAXTOK:
                best = pair
                break
        if best is None:
            break
        new = len(BASE) + len(merges)
        merges.append(best)
        a, b = best
        for i, d in enumerate(docs):
            out, j = [], 0
            while j < len(d):
                if j + 1 < len(d) and d[j] == a and d[j + 1] == b:
                    out.append(new)
                    j += 2
                else:
                    out.append(d[j])
                    j += 1
            docs[i] = out
    return merges


def apply_merges(ids, merges):
    for k, (a, b) in enumerate(merges):
        new = len(BASE) + k
        out, j = [], 0
        n = len(ids)
        while j < n:
            if j + 1 < n and ids[j] == a and ids[j + 1] == b:
                out.append(new)
                j += 2
            else:
                out.append(ids[j])
                j += 1
        ids = out
    return ids


def encode(text, vocab):
    """Longest-match tokenisation - the same function tools/mkgame.py and
    train/sample.py use, so what the cartridge is fed is what was trained."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--vocab-out", default="data/qa_vocab.json")
    a = ap.parse_args()

    rows = C.qa_lines()
    train = [(t, q, ans) for t, q, ans, held in rows if not held]
    held = [(t, q, ans) for t, q, ans, held in rows if held]

    # Merge corpus: the training questions with their answers, plus the
    # monologue.  Held-out questions are NOT in it - a vocabulary fitted to the
    # test set would flatter the test set.
    docs, wts = [], []
    for _t, q, ans in train:
        docs.append(enc_char(q + ans))
        wts.append(1.0)
    for m in C.MONOLOGUE:
        docs.append(enc_char(m))
        wts.append(1.0)

    merges = learn_merges(docs, wts)
    vocab = [BASE[i] for i in range(len(BASE))] + \
            [spell(len(BASE) + k, merges) for k in range(len(merges))]
    assert len(vocab) == len(set(vocab)) == TARGET, (len(vocab), len(set(vocab)))
    print("learned %d merges -> vocab %d" % (len(merges), len(vocab)))
    print("merge tokens: " + " ".join("%r" % v for v in vocab[len(BASE):]))

    space = BASE.index(" ")

    over = []

    def row(q, ans, tag):
        qt, at = encode(q, vocab), encode(ans, vocab)
        if len(qt) + len(at) > T:
            # Collect, do not die: one over-budget line at a time turns a
            # corpus edit into a dozen round trips.
            over.append((tag, q, ans, len(qt), len(at)))
        ids = (qt + at)[:T]
        ids = ids + [space] * (T - len(ids))
        return np.array(ids, dtype=np.uint8), len(qt), len(qt) + len(at)

    def pack(items, label):
        X, QN, AN, TP = [], [], [], []
        worst = None
        for t, q, ans in items:
            r, qn, an = row(q, ans, label)
            X.append(r); QN.append(qn); AN.append(an)
            TP.append(C.TOPICS.index(t))
            if worst is None or an > worst[0]:
                worst = (an, q, ans)
        print("%-8s %3d examples   longest q+a %d/%d tokens  %r %r"
              % (label, len(X), worst[0], T, worst[1], worst[2]))
        return (np.stack(X), np.array(QN, dtype=np.int16),
                np.array(AN, dtype=np.int16), np.array(TP, dtype=np.int16))

    Xtr, Qtr, Atr, Ttr = pack(train, "train")
    Xho, Qho, Aho, Tho = pack(held, "held")
    # Monologue rows have no question: the whole row is "answer" from
    # position 0, which is exactly how act 2 runs.
    Xmo, Qmo, Amo, Tmo = pack([("game", "", m) for m in C.MONOLOGUE], "mono")

    if over:
        print("\n%d lines over the %d-position context:" % (len(over), T))
        for tag, q, ans, qn, an in sorted(over, key=lambda r: -(r[3] + r[4])):
            print("  %-6s %2d+%2d=%2d  %r %r" % (tag, qn, an, qn + an, q, ans))
        raise SystemExit("shorten them; the ROM cannot grow")

    nch = sum(len(q + ans) for _t, q, ans in train) + sum(len(m) for m in C.MONOLOGUE)
    ntok = sum(len(encode(q + ans, vocab)) for _t, q, ans in train) + \
        sum(len(encode(m, vocab)) for m in C.MONOLOGUE)
    print("compression  %.3f chars/token  (TinyStories bpe64 was 1.454)"
          % (nch / ntok))

    os.makedirs(a.out, exist_ok=True)
    np.savez(os.path.join(a.out, "qa_train.npz"),
             X=Xtr, Q=Qtr, A=Atr, TOPIC=Ttr,
             Xmono=Xmo, Qmono=Qmo, Amono=Amo, TOPICmono=Tmo)
    np.savez(os.path.join(a.out, "qa_held.npz"), X=Xho, Q=Qho, A=Aho, TOPIC=Tho)
    json.dump({"base": BASE, "fold": {}, "merges": merges, "vocab": vocab,
               "chars_per_token": nch / ntok,
               "source": "train/corpus.py"},
              open(a.vocab_out, "w"), indent=1)
    print("wrote %s, %s/qa_train.npz, %s/qa_held.npz"
          % (a.vocab_out, a.out, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
