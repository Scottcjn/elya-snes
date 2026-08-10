#!/usr/bin/env python3
"""Every vocabulary symbol as a seed: the cartridge against host/ref.py.

One seed is not a gate.  A greedy run's first tokens are dominated by the
embedding and only start exercising the KV cache, the softmax and the value
path once there is context, so a short single-seed comparison can pass a
genuinely broken build -- it did, three times, on the sibling ports.  This
compares every token of every seed.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
os.environ.setdefault("NES_T", "20")
import ref                                                        # noqa: E402


def main(path):
    d = open(path, "rb").read()
    if d[0:4] != b"ELYA":
        print("not an nn dump", file=sys.stderr)
        return 1
    if d[8:12] != b"DONE":
        print("the ROM did not finish", file=sys.stderr)
        return 1
    ngen, nseed = d[4], d[6]
    npz = os.environ.get("SNES_WEIGHTS",
                         os.path.join(ROOT, "model", "dense_exact_s1.npz"))
    m = ref.Model.from_npz(npz)
    vocab = json.load(open(os.path.join(ROOT, "data", "vocab.json")))["vocab"]

    ok = tot = 0
    bad = []
    for s in range(nseed):
        base = 0x10 + s * (ngen + 1)
        got = list(d[base:base + ngen + 1])
        want, _ = ref.generate(m, s, ngen)
        tot += len(want)
        ok += sum(1 for a, b in zip(want, got) if a == b)
        if want != got:
            bad.append(s)
        txt = "".join(vocab[i] for i in got)
        print("seed %2d %-6r -> %r%s" % (s, vocab[s], txt,
                                         "   *** MISMATCH" if want != got else ""))
    print()
    print("%d seeds x %d tokens: %d/%d identical" % (nseed, ngen + 1, ok, tot))
    if bad:
        print("FAIL: seeds %s differ" % bad)
        return 1
    print("PASS: every seed matches host/ref.py exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
