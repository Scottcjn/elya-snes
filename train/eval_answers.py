#!/usr/bin/env python3
"""Score a model on ANSWERS, not on loss.

Fit loss is the wrong instrument for this job twice over.  It is measured
against a distribution, and a topic-sharded arm trains on a different
distribution from a dense one, so the two numbers are not comparable at all.
And even within one arm it answers "how surprised is she by the corpus",
which is not the question the game asks.  The question the game asks is
*given a stored question, does she reply with the right sentence*.

The decoding path here is rom/game.inc's, step for step:

  * the question is FED, one token per position, starting at position 0;
  * the last feed step's output is the FIRST answer token (game.inc:462-471
    is explicit about this and about the bug that dropped it);
  * generation then runs to position 19 and stops on the count, because the
    ROM has no end-of-sequence token;
  * argmax with ties to the lowest index, which is what rom/nn.s does.

It runs on host/ref.py - exact integer arithmetic, the same reference the
1280/1280 survey compares the cartridge against - so a score here is a score
the cartridge will reproduce.

Metrics, all over the corpus's own answers:

  exact    the generated text, with trailing spaces stripped, IS the answer
  prefix   longest common prefix / len(answer), which is the metric that
           caught the sibling Genesis port's positional failure: answers that
           start right and rot after 13-28 characters score high here and 0
           on exact, and that difference is the diagnosis
  degen    the 'aaa aaa' / 'the the the' collapse, which can score well on
           prefix and is not an answer
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import corpus as C
import ref


def load_vocab(path):
    v = json.load(open(path))
    return v["vocab"]


def encode(text, vocab):
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


def answer(model, prompt_ids, vocab, nctx=20):
    """Feed the prompt, then free-run to the end of the context.  Returns the
    generated text only - the question is input, not output, exactly as the
    ROM draws it (amber prompt, white answer)."""
    r = ref.Runner(model)
    out = []
    cur = prompt_ids[0]
    for p in range(nctx):
        nxt = r.step(cur, p)
        if p + 1 < len(prompt_ids):
            cur = prompt_ids[p + 1]           # still inside the question
        else:
            out.append(nxt)                   # this token is hers
            cur = nxt
    return "".join(vocab[t] for t in out)


def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def degenerate(gen):
    w = gen.split()
    if len(w) < 3:
        return len(set(gen.replace(" ", ""))) <= 2
    return len(set(w)) <= max(1, len(w) // 3)


def evaluate(model, vocab, rows, label, verbose=0):
    n = len(rows)
    ex = pfx = deg = 0
    detail = []
    for topic, q, want, _held in rows:
        gen = answer(model, encode(q, vocab), vocab)
        got = gen.rstrip()
        hit = 1 if got == want else 0
        p = common_prefix(got, want) / max(len(want), 1)
        d = 1 if degenerate(got) else 0
        ex += hit; pfx += p; deg += d
        detail.append(dict(topic=topic, q=q, want=want, got=got,
                           exact=hit, prefix=p, degenerate=d))
    res = dict(label=label, n=n, exact=ex / n, prefix=pfx / n, degen=deg / n,
               n_exact=ex, rows=detail)
    print("%-6s exact %3d/%-3d = %5.1f%%   mean prefix %5.1f%%   degenerate %d"
          % (label, ex, n, 100 * ex / n, 100 * pfx / n, deg))
    if verbose:
        for r in sorted(detail, key=lambda r: r["prefix"])[:verbose]:
            print("    %-20r want %-22r got %r" % (r["q"], r["want"], r["got"]))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--vocab", default="data/qa_vocab.json")
    ap.add_argument("--verbose", type=int, default=0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--all", action="store_true", help="print every question")
    a = ap.parse_args()

    vocab = load_vocab(a.vocab)
    m = ref.Model.from_npz(a.npz)
    rows = C.qa_lines()
    tr = [r for r in rows if not r[3]]
    ho = [r for r in rows if r[3]]

    print("%s" % a.npz)
    rtr = evaluate(m, vocab, tr, "train", a.verbose)
    rho = evaluate(m, vocab, ho, "held", a.verbose)
    if a.all:
        for r in rtr["rows"] + rho["rows"]:
            print("  %-20r -> %r%s" % (r["q"], r["got"],
                                       "" if r["exact"] else
                                       "   (want %r)" % r["want"]))
    if a.json:
        json.dump(dict(npz=a.npz, train=rtr, held=rho), open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
