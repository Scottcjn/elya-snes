#!/usr/bin/env python3
"""Where a token's time goes, from the STAGEPROF build.

Twenty-four samples of the multi-frame clock, taken at every stage boundary of
one forward pass and rewritten each token, so what the dump holds is the LAST
position -- the most expensive one, because attention grows with context.
"""
import sys

DOT, LINE, LINES, NMI_LINE = 4, 1364, 262, 225
CPU_FRACTION = 1.0 - 40 / LINE
STAGE_BASE = 0x400
NAMES = ["embed+pos"]
for l in range(3):
    NAMES += ["L%d Wq" % l, "L%d Wk" % l, "L%d Wv" % l, "L%d attention" % l,
              "L%d Wo" % l, "L%d W1" % l, "L%d W2" % l]
NAMES += ["head"]


def le16(b, o):
    return b[o] | (b[o + 1] << 8)


def main(path, frame_len):
    d = open(path, "rb").read()
    if d[8:12] != b"DONE":
        print("unfinished dump", file=sys.stderr)
        return 1
    s = []
    for i in range(len(NAMES) + 1):
        o = STAGE_BASE + i * 6
        f, v, h = le16(d, o), le16(d, o + 2), le16(d, o + 4)
        s.append(f * frame_len + ((v - NMI_LINE) % LINES) * LINE + h * DOT)
    walls = [s[i + 1] - s[i] for i in range(len(NAMES))]
    tot = sum(walls)
    print("== where one token's master clocks go (last position) ==")
    print("%-14s %10s %7s" % ("stage", "wall", "share"))
    for n, w in zip(NAMES, walls):
        print("%-14s %10.0f %6.2f%%" % (n, w, 100 * w / tot))
    print("%-14s %10.0f" % ("TOTAL", tot))
    agg = {}
    for n, w in zip(NAMES, walls):
        k = ("attention" if "attention" in n else
             "embed/head" if n in ("embed+pos", "head") else "matmul")
        agg[k] = agg.get(k, 0) + w
    print()
    for k, w in sorted(agg.items(), key=lambda kv: -kv[1]):
        print("  %-12s %10.0f  %6.2f%%" % (k, w, 100 * w / tot))
    return 0


if __name__ == "__main__":
    fl = float(sys.argv[2]) if len(sys.argv) > 2 else 356815.7
    sys.exit(main(sys.argv[1], fl))
