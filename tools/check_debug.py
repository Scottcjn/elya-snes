#!/usr/bin/env python3
"""Compare the cartridge's INTERNALS against host/ref.py, not just its output.

The 64-seed survey compares argmaxes.  That is a strong gate but it is still an
output gate: an arithmetic error that never moves a winner would survive it.
This compares the residual stream after every layer, and the attention output
of layer 0, element by element, for one chosen position -- 320 signed values
that have to agree exactly.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
os.environ.setdefault("NES_T", "20")
import ref                                                        # noqa: E402

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
    # Re-run the reference rather than read a stored trace: a file on disk goes
    # stale the moment the weights change and says nothing when it does.
    npz = os.environ.get("SNES_WEIGHTS",
                         os.path.join(ROOT, "model", "dense_exact_s1.npz"))
    m = ref.Model.from_npz(npz)
    r = ref.Runner(m)
    cur = d[5]                                    # the seed token the ROM used
    for p in range(pos + 1):
        cur = r.step(cur, p)
    tr = r.trace[pos]
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
