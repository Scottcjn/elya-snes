#!/usr/bin/env python3
"""Choose act 3's stored menu from what the model can actually answer.

tools/mkgame.py's old menu was picked by running host/ref.py over candidates
and keeping the ones whose answers were inside the context budget and
recognisably replies.  This does the same selection mechanically against a
given model, so the menu is a property of the shipped weights rather than of
whoever last edited the list.

Two hard filters, then one editorial choice:

  * the prompt must be at most MAXPROMPT tokens (tools/mkgame.py's cap, which
    exists so that at least half the twenty positions are left for her);
  * host/ref.py, decoding exactly as rom/game.inc does, must produce the
    corpus's answer exactly.

Then one question per topic, shortest prompt first, so the six on screen are
not six ways of asking the same thing.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import corpus as C
import eval_answers as E
import ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--vocab", default="data/vocab.json")
    ap.add_argument("--maxprompt", type=int, default=10)
    ap.add_argument("--n", type=int, default=6)
    a = ap.parse_args()

    vocab = E.load_vocab(a.vocab)
    m = ref.Model.from_npz(a.npz)

    ok = []
    for topic, q, want, held in C.qa_lines():
        if held:
            continue
        p = E.encode(q, vocab)
        if len(p) > a.maxprompt:
            continue
        got = E.answer(m, p, vocab).rstrip()
        if got == want:
            ok.append((topic, len(p), q, want))

    print("%d of the training questions are inside the prompt budget AND "
          "answered exactly" % len(ok))
    chosen, seen = [], set()
    for topic, n, q, want in sorted(ok, key=lambda r: (r[1], r[2])):
        if topic in seen:
            continue
        seen.add(topic)
        chosen.append((topic, n, q, want))
    for topic, n, q, want in sorted(ok, key=lambda r: (r[1], r[2])):
        if len(chosen) >= a.n:
            break
        if (topic, n, q, want) not in chosen:
            chosen.append((topic, n, q, want))
    chosen = chosen[:a.n]

    print("\nQUESTIONS = [")
    for topic, n, q, want in chosen:
        print("    %-22r  # %-8s %2d tokens, %2d left -> %r"
              % (q, topic, n, 20 - n, want))
    print("]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
