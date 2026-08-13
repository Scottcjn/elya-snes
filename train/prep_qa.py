#!/usr/bin/env python3
"""Build the conversational vocabulary and the position-aligned training set.

Two things happen here that did not happen in train/prep_corpus.py, and both
are forced by how rom/game.inc actually runs a conversation:

This writes `data/vocab.json`, which is the vocabulary the ROM tools read:
tools/mkgame.py builds the printable string table from it, tools/check_game.py
tokenises the menu with it, and train/sample.py detokenises with it.  The
TinyStories vocabulary the shipped model was fitted to is kept beside it as
`data/vocab_tinystories.json`, because `model/dense_exact_s1.npz` still needs
it to be read back and the README reproduces strings with it.

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

# The base symbols are MEASURED, not inherited.  prep_corpus.py fixed 33 for
# TinyStories, where every one of `.,"'!?` earns its slot because dialogue is
# everywhere.  This corpus is questions and short answers: it uses no quotes and
# no exclamation marks at all, and a base symbol that never occurs is a dead
# head row AND a merge slot not spent.  Anything with a zero count is dropped
# and the slot goes to a merge; anything that occurs even once is kept, because
# a symbol the vocabulary lacks is a symbol she can never say.
CANDIDATES = "abcdefghijklmnopqrstuvwxyz .,\"'!?"
TARGET = 64
MAXTOK = 5          # tools/mkgame.py's vocab.tbl row is [len][5 bytes]
T = 20


BASE = ""          # set by set_base() from the measured corpus


def set_base(text):
    global BASE
    BASE = "".join(c for c in CANDIDATES if c in text)
    drop = [c for c in CANDIDATES if c not in text]
    print("base symbols  %d kept, %d dropped (%s) -> %d merge slots"
          % (len(BASE), len(drop), " ".join(repr(c) for c in drop),
             TARGET - len(BASE)))


def enc_char(s):
    return [BASE.index(c) for c in s]


def spell(tid, merges):
    if tid < len(BASE):
        return BASE[tid]
    a, b = merges[tid - len(BASE)]
    return spell(a, merges) + spell(b, merges)


def learn_merges(docs, weights, budget=None, objective="count"):
    """Greedy merges, weighted by how often a line is used.

    Weighting matters here in a way it did not on TinyStories: a question that
    appears three times as three paraphrases is three lines, and an answer that
    35 facts share a shape with is one.  The counts below are over the corpus
    as the model will see it, not over unique strings.

    Two objectives, and the second one exists because the first optimises the
    wrong thing for this ROM:

    `count`     classic BPE - take the most frequent adjacent pair.  Since a
                merge removes exactly one token per application, this is greedy
                minimisation of TOTAL corpus tokens.

    `overflow`  greedy minimisation of the tokens by which rows exceed the
                context.  Total tokens is not the constraint here; 20 positions
                per row is, and rom/game.inc simply never produces the tail of
                a row that does not fit.  A merge that saves forty tokens
                spread over rows already inside the budget buys nothing, and
                one that saves three tokens on the three longest rows buys
                three answers.  Candidates are scored by tokens removed FROM
                ROWS CURRENTLY OVER BUDGET, with the plain total as the
                tie-break so the objective degrades to `count` once everything
                fits.

    The merge set is fitted to the TRAINING split only, whichever objective is
    used.  A vocabulary fitted to the held-out questions would compress the
    test set by construction and flatter it; the held-out rows are shortened by
    hand instead, which is a corpus edit and not a measurement.
    """
    docs = [list(d) for d in docs]
    merges = []
    while len(BASE) + len(merges) < TARGET:
        # pair -> {doc index: adjacent occurrences}.  Overlapping, as the
        # original counter was; application below is non-overlapping, so this
        # over-counts a run of three identical tokens by one.  Nothing in this
        # corpus has such a run and the ranking is unaffected either way.
        per = collections.defaultdict(collections.Counter)
        for i, d in enumerate(docs):
            for a, b in zip(d, d[1:]):
                per[(a, b)][i] += 1
        # Longest-token cap: a merge whose spelling would exceed MAXTOK bytes
        # cannot be stored in the ROM's vocab table, so it is not a candidate.
        best, best_key = None, None
        for pair, hits in per.items():
            if len(spell(pair[0], merges)) + len(spell(pair[1], merges)) > MAXTOK:
                continue
            total = sum(weights[i] * n for i, n in hits.items())
            if objective == "overflow" and budget is not None:
                over = sum(weights[i] * min(n, max(0, len(docs[i]) - budget))
                           for i, n in hits.items())
                key = (over, total)
            else:
                key = (total,)
            if best_key is None or key > best_key:
                best, best_key = pair, key
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


# ---------------------------------------------------------------------------
# The vocabulary does not have to be a merge tree
#
# Nothing downstream reads the merge list.  vocab.json carries a "merges" key
# and no consumer opens it: tools/mkgame.py, tools/check_game.py,
# train/sample.py and encode() below all use the 64 STRINGS with longest-match
# tokenisation.  So the merge tree is a construction method, not a contract -
# and it is a bad construction method at this budget.  Byte-pair encoding grows
# tokens one character at a time and there are only 34 slots above the 30 base
# symbols, so it never gets far enough to spend one on `usand` or `ttle`; it
# spends all 34 on two-character fragments.
#
# These two functions choose the 34 strings directly instead: greedy, scored by
# what the ROM actually cannot do, which is emit a row that does not fit in 20
# positions.  The candidate is any substring of 2..MAXTOK characters that the
# training corpus contains, the score is the weighted reduction in OVERFLOW
# with total tokens as the tie-break, and the cost model is the exact
# longest-match tokeniser the cartridge runs rather than a proxy for it.
# ---------------------------------------------------------------------------
def tok_cost(text, by_len):
    """Token count under longest-match tokenisation - encode()'s loop, counting
    instead of emitting.  by_len maps length -> set of vocabulary strings of
    that length; single characters are always in BASE and are not stored."""
    i, n, L = 0, 0, len(text)
    while i < L:
        for k in range(MAXTOK, 1, -1):
            if k <= L - i and text[i:i + k] in by_len[k]:
                i += k
                break
        else:
            i += 1
        n += 1
    return n


def learn_vocab_greedy(items, weights, budget, cap=1000):
    """Choose TARGET - len(BASE) strings, greedily, to stop rows overflowing.

    `items` is a list of segment tuples.  A QA row is (question, answer)
    because train/prep_qa.py tokenises the two separately and concatenates the
    ids - which is what rom/game.inc does, feeding the prompt and then
    generating - so a token that would straddle the boundary does not exist at
    run time and must not exist in the cost model either.

    Only rows a candidate actually occurs in are re-costed, which is what makes
    an exhaustive 34 x cap search finish in seconds rather than minutes.

    `cap` is how many of the 4,123 distinct substrings are considered, ranked
    by weighted frequency.  It was 400 and that was too few for the wrong
    reason: an ANSWER appears once per phrasing, so `sixty` occurs six times
    against ` t`'s several hundred, and the strings that would fix the rows
    that actually overflow were ranked out of the pool.  Measured over the
    whole corpus, rows that do not fit in 20 positions:

        cap  400   2.2s   16 rows over
        cap 1000   3.3s   14 rows over
        cap 2000   4.2s   15 rows over
        cap 5000   4.9s   15 rows over

    Not monotone, because the selection is greedy: a candidate that wins one
    round can leave the next round worse off.  1000 is where it stops paying.
    """
    by_len = {k: set() for k in range(2, MAXTOK + 1)}
    chosen = []

    def cost(segs):
        return sum(tok_cost(s, by_len) for s in segs)

    cur = [cost(segs) for segs in items]

    cnt = collections.Counter()
    for segs, w in zip(items, weights):
        for s in segs:
            for k in range(2, MAXTOK + 1):
                for i in range(len(s) - k + 1):
                    cnt[s[i:i + k]] += w
    pool = [c for c, _n in cnt.most_common(cap)]
    where = {c: [i for i, segs in enumerate(items)
                 if any(c in s for s in segs)] for c in pool}

    while len(BASE) + len(chosen) < TARGET:
        best, best_key = None, None
        for c in pool:
            if c in by_len[len(c)]:
                continue
            by_len[len(c)].add(c)
            d_over = d_tot = 0.0
            for i in where[c]:
                new = cost(items[i])
                w = weights[i]
                d_tot += w * (cur[i] - new)
                d_over += w * (max(0, cur[i] - budget) - max(0, new - budget))
            by_len[len(c)].discard(c)
            key = (d_over, d_tot, -len(c), c)
            if best_key is None or key > best_key:
                best, best_key = c, key
        if best is None or best_key[1] <= 0:
            # Nothing left that removes a token.  The table still has to be 64
            # entries long - tools/mkgame.py asserts it and the ROM indexes it
            # - so fill the rest with the commonest unused candidates.  They
            # buy nothing and they cost nothing.
            for c in pool:
                if len(BASE) + len(chosen) >= TARGET:
                    break
                if c not in by_len[len(c)]:
                    by_len[len(c)].add(c)
                    chosen.append(c)
            break
        by_len[len(best)].add(best)
        chosen.append(best)
        for i in where[best]:
            cur[i] = cost(items[i])
    return chosen


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
    ap.add_argument("--vocab-out", default="data/vocab.json")
    ap.add_argument("--merges", default="greedy",
                    choices=("count", "overflow", "greedy"),
                    help="how the 34 non-base vocabulary slots are chosen. "
                         "`count` is classic BPE, `overflow` is BPE scored on "
                         "rows that do not fit, `greedy` drops the merge tree "
                         "and picks substrings directly - see "
                         "learn_vocab_greedy().")
    a = ap.parse_args()

    C.check()
    rows = C.qa_rows()
    # The charset is measured over EVERY question, held-out ones included: a
    # symbol the vocabulary lacks is a symbol that cannot be tokenised, and a
    # held-out question that cannot be tokenised cannot be scored.  The MERGES
    # below are a different matter and are fitted to the training split only.
    set_base("".join(q + ans for _t, q, ans, _s in rows) + "".join(C.MONOLOGUE))
    train = [(t, q, ans) for t, q, ans, s in rows if s == "train"]
    dev = [(t, q, ans) for t, q, ans, s in rows if s == "dev"]
    test = [(t, q, ans) for t, q, ans, s in rows if s == "test"]
    held = dev + test

    # Fitting corpus: the training questions with their answers, plus the
    # monologue.  Held-out questions are NOT in it - a vocabulary fitted to the
    # test set would compress the test set by construction and flatter it.
    items = [(q, ans) for _t, q, ans in train] + [(m,) for m in C.MONOLOGUE]
    wts = [1.0] * len(items)

    if a.merges == "greedy":
        merges = []
        extra = learn_vocab_greedy(items, wts, budget=T)
    else:
        docs = [enc_char("".join(segs)) for segs in items]
        merges = learn_merges(docs, wts, budget=T, objective=a.merges)
        extra = [spell(len(BASE) + k, merges) for k in range(len(merges))]
    vocab = list(BASE) + extra
    assert len(vocab) == len(set(vocab)) == TARGET, (len(vocab), len(set(vocab)))
    print("%s: %d chosen -> vocab %d" % (a.merges, len(extra), len(vocab)))
    print("chosen tokens: " + " ".join("%r" % v for v in extra))

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
    pack(dev, "dev")
    pack(test, "test")
    Xho, Qho, Aho, Tho = pack(held, "held")
    # Monologue rows have no question: the whole row is "answer" from
    # position 0, which is exactly how act 2 runs.
    Xmo, Qmo, Amo, Tmo = pack([("game", "", m) for m in C.MONOLOGUE], "mono")

    # Over-budget rows are two different problems and only one of them is fatal.
    #
    # A TRAINING or MONOLOGUE row that does not fit is corrupt data: row() slices
    # it to T, so the trainer is taught an answer with its tail cut off and then
    # scored against the whole answer.  That is a bug and it dies here.
    #
    # A DEV or TEST row that does not fit is a question this cartridge cannot
    # answer: train/eval_answers.py feeds the whole prompt and generates
    # `T - len(prompt)` tokens, so the answer is a token short before the model
    # is consulted and the row scores zero.  That is a real limit of a
    # 20-position context and it is reported, not repaired - repairing it means
    # either shortening a question the entry-10 comparison has frozen, or
    # fitting the vocabulary to the held-out split, which would compress the
    # test set by construction.  Both of the survivors below are LEGACY
    # questions (train/corpus.py's LEGACY_HELD), so the first is not available;
    # they are carried as guaranteed misses, which biases the headline DOWN.
    fatal = [r for r in over if r[0] in ("train", "mono")]
    if over:
        print("\n%d lines over the %d-position context (%d fatal):"
              % (len(over), T, len(fatal)))
        for tag, q, ans, qn, an in sorted(over, key=lambda r: -(r[3] + r[4])):
            print("  %-6s %2d+%2d=%2d  %r %r" % (tag, qn, an, qn + an, q, ans))
    if fatal:
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
    # "vocab" is the only key any consumer reads - tools/mkgame.py,
    # tools/check_game.py, train/sample.py and encode() above all tokenise by
    # longest match over these 64 strings.  "merges" is kept for the two BPE
    # methods because it records how they got there, and is empty for `greedy`,
    # which has no merge tree.
    json.dump({"base": BASE, "fold": {}, "merges": merges, "vocab": vocab,
               "method": a.merges, "chars_per_token": nch / ntok,
               "source": "train/corpus.py"},
              open(a.vocab_out, "w"), indent=1)
    print("wrote %s, %s/qa_train.npz, %s/qa_held.npz"
          % (a.vocab_out, a.out, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
