#!/usr/bin/env python3
"""Compare the cartridge's INTERNALS against host/ref.py, not just its output.

The 64-seed survey compares argmaxes.  That is a strong gate but it is still an
output gate: an arithmetic error that never moves a winner would survive it.
This compares the residual stream after every layer, and the attention output
of layer 0, element by element, for one chosen position -- 320 signed values
that have to agree exactly.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BLOCKS = [("x0", 0x0600), ("L0.x", 0x0640), ("L1.x", 0x0680),
          ("L2.x", 0x06C0), ("att", 0x0700)]
D = 64


def s8(b):
    return b - 256 if b > 127 else b


def main(path, pos):
    d = open(path, "rb").read()
    if d[0:4] != b"ELYA" or d[8:12] != b"DONE":
        print("bad or unfinished dump", file=sys.stderr)
        return 1
    exp = json.load(open(os.path.join(ROOT, "out", "model", "expected.json")))
    tr = exp["trace"][pos]
    bad = 0
    for name, base in BLOCKS:
        if name not in tr:
            print("%-6s not in the reference trace at position %d" % (name, pos))
            continue
        want = tr[name]
        got = [s8(d[base + i]) for i in range(D)]
        diff = [i for i in range(D) if want[i] != got[i]]
        print("%-6s %d/%d identical%s"
              % (name, D - len(diff), D,
                 "" if not diff else "   *** differ at %s" % diff[:8]))
        if diff:
            print("        host %s" % want[:16])
            print("        rom  %s" % got[:16])
            bad += len(diff)
    print()
    if bad:
        print("FAIL: %d of %d values differ at position %d"
              % (bad, D * len(BLOCKS), pos))
        return 1
    print("PASS: all %d intermediate values identical at position %d"
          % (D * len(BLOCKS), pos))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0))
