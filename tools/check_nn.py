#!/usr/bin/env python3
"""Compare the cartridge's token stream against host/ref.py, token for token.

The reference is re-run here rather than read from a stored file, so the thing
the ROM is checked against is the specification executing, not a transcript of
it that could have gone stale.

A three-token check is not a check.  On the sibling ports a short comparison
passed three genuinely broken changes, because the first tokens of a greedy
run are dominated by the embedding and only diverge once the KV cache has
anything in it.  This compares every generated token and refuses anything
shorter than 16.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
os.environ.setdefault("NES_T", "20")
import ref                                                        # noqa: E402

MIN_TOKENS = 16


def main(path):
    d = open(path, "rb").read()
    if d[0:4] != b"ELYA":
        print("not an nn dump: %r" % d[0:4], file=sys.stderr)
        return 1
    if d[8:12] != b"DONE":
        print("the ROM did not finish: %r" % d[8:12], file=sys.stderr)
        return 1
    ngen, seed = d[4], d[5]
    got = list(d[0x10:0x10 + ngen + 1])

    npz = ref.default_weights()
    m = ref.Model.from_npz(npz)
    want, _ = ref.generate(m, seed, ngen)

    vocab = None
    vp = os.path.join(ROOT, "data", "vocab.json")
    if os.path.exists(vp):
        vocab = json.load(open(vp))["vocab"]

    def show(ids):
        return "".join(vocab[i] for i in ids) if vocab else ""

    print("weights   %s" % npz)
    print("seed tok  %d   generated %d" % (seed, ngen))
    print("host      %s" % want)
    print("rom       %s" % got)
    if vocab:
        print("host text %r" % show(want))
        print("rom  text %r" % show(got))

    if ngen < MIN_TOKENS:
        print("FAIL: %d generated tokens is below the %d-token minimum"
              % (ngen, MIN_TOKENS))
        return 1
    bad = [i for i, (a, b) in enumerate(zip(want, got)) if a != b]
    if len(want) != len(got) or bad:
        print("FAIL: %d of %d positions differ: %s"
              % (len(bad), len(want), bad[:10]))
        return 1
    print("PASS: %d/%d tokens identical (seed + %d generated)"
          % (len(got), len(want), ngen))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
