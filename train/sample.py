#!/usr/bin/env python3
"""Generate text with host/ref.py - the exact-integer reference the ROM is
verified against - and detokenise it.

Greedy argmax with ties to the lowest index, exactly as rom/nn.s does, so the
strings printed here are the strings the cartridge produces.  The ROM free-runs
from a single seed token; --prompt is a host-only convenience for inspecting
conditional behaviour and is labelled as such wherever it is used.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import ref


def load_vocab(path="data/vocab.json"):
    v = json.load(open(path))
    return v["vocab"], v["base"]


def detok(ids, vocab):
    return "".join(vocab[i] for i in ids)


def encode(text, vocab, base):
    """Longest-match tokenisation against the 64-symbol vocabulary."""
    order = sorted(range(len(vocab)), key=lambda i: -len(vocab[i]))
    out, i = [], 0
    while i < len(text):
        for t in order:
            s = vocab[t]
            if s and text.startswith(s, i):
                out.append(t)
                i += len(s)
                break
        else:
            i += 1
    return out


def run(model, prompt_ids, nsteps):
    """Teacher-force the prompt, then free-run greedy. Returns the full id list."""
    r = ref.Runner(model)
    ids = list(prompt_ids)
    cur = ids[0]
    p = 0
    while p < nsteps:
        nxt = r.step(cur, p)
        p += 1
        if p < len(ids):
            cur = ids[p]                     # still inside the prompt
        else:
            ids.append(nxt)
            cur = nxt
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--vocab", default="data/vocab.json")
    ap.add_argument("--seeds", default=None, help="comma separated seed token ids")
    ap.add_argument("--prompt", default=None, help="host-only text prompt")
    ap.add_argument("--n", type=int, default=ref.T - 1)
    ap.add_argument("--survey", action="store_true", help="all 64 seed tokens")
    a = ap.parse_args()

    vocab, base = load_vocab(a.vocab)
    m = ref.Model.from_npz(a.npz)

    if a.prompt is not None:
        ids = encode(a.prompt, vocab, base)
        out = run(m, ids, a.n)
        print("prompt (host-only) %-20r -> %r"
              % (detok(ids, vocab), detok(out, vocab)))
        return 0

    seeds = (list(range(len(vocab))) if a.survey else
             [int(s) for s in (a.seeds or "1").split(",")])
    for s in seeds:
        out = run(m, [s], a.n)
        print("seed %2d %-8r -> %r" % (s, vocab[s], detok(out, vocab)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
