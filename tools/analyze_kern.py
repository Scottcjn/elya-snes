#!/usr/bin/env python3
"""Turn a kern.ram dump into master clocks per multiply-accumulate, per gather
kernel shape.

Same timing model as tools/analyze.py:

    wall_master = (V2 - V1) * 1364 + (H2 - H1) * 4
    cpu_master  = wall_master * (1 - 40/1364)

Each kernel shape has its OWN empty skeleton, because the shapes differ in
index-register width and in whether there is an inner loop at all, and an
empty measured in the wrong shape would not cancel.
"""
import sys

DOT = 4
SCANLINE = 1364
CPU_FRACTION = 1.0 - 40 / SCANLINE
MASTER_HZ = 21477272.0
NELEM = 128

# slot -> (name, outer)
SLOTS = [
    ("empty16@2", 2), ("empty16@4", 4), ("empty16@8", 8), ("empty16@16", 16),
    ("i16abs", 8), ("i16abs", 4),
    ("i16dp", 8), ("i16dp", 4),
    ("empty8@8", 8), ("empty8@4", 4),
    ("i8dp16", 8), ("i8dp16", 4),
    ("empty8a@8", 8), ("empty8a@4", 4),
    ("i8acc", 8), ("i8acc", 4),
    ("emptyC@16", 16), ("emptyC@8", 8),
    ("code", 16), ("code", 8),
    ("codesgn", 16), ("codesgn", 8),
    ("code8", 16), ("code8", 8),
]

# which empty group each kernel subtracts, and the slot indices of that group
GROUPS = {
    "i16abs": (0, 1, 2, 3),
    "i16dp": (0, 1, 2, 3),
    "i8dp16": (8, 9),
    "i8acc": (12, 13),
    "code": (16, 17),
    "codesgn": (16, 17),
    "code8": (16, 17),
}

# Hand-derived CPU master clocks per MAC.  SNES memory speeds: SlowROM cart
# access 8, FastROM cart access 6, WRAM always 8, internal cycle 6.
DERIVED_SLOW = {
    "i16abs": 46 + 46,                # ldx abs,y (16-bit X) + adc abs,x
    "i16dp": 46 + 38,                 # ldx abs,y (16-bit X) + adc dp,x
    "i8dp16": 32 + 38,                # ldx abs,y (8-bit X)  + adc dp,x
    "i8acc": 32 + 30 + 146 / 16,      # + the 8-bit fold every 16
    "code": 32,                       # adc dp
    "codesgn": 32 + 14 / 128,         # + one sec per 128
    "code8": 24 + 146 / 16 + 108 / 128,
}
DERIVED_FAST = {
    "i16abs": 36 + 40,
    "i16dp": 36 + 34,
    "i8dp16": 24 + 34,
    "i8acc": 24 + 26 + 122 / 16,
    "code": 28,
    "codesgn": 28 + 12 / 128,
    "code8": 20 + 122 / 16 + 92 / 128,
}

VERIFY = [
    ("i16abs", 0), ("i16dp", 1), ("i8dp16", 2), ("i8acc", 3),
    ("code", 4), ("codesgn", 5), ("code8", 6),
]


def le16(b, o):
    return b[o] | (b[o + 1] << 8)


def main(path, fast=False):
    d = open(path, "rb").read()
    if d[0:4] != b"KERN":
        print("not a kern dump: %r" % d[0:4], file=sys.stderr)
        return 1
    if d[8:12] != b"DONE":
        print("run did not finish: %r" % d[8:12], file=sys.stderr)
        return 1

    wall = {}
    for i, (name, outer) in enumerate(SLOTS):
        o = 0x10 + i * 8
        v1, h1, v2, h2 = le16(d, o), le16(d, o + 2), le16(d, o + 4), le16(d, o + 6)
        if v2 & 0x8000:
            print("slot %d (%s) touched vblank -- VOID" % (i, name))
            return 1
        wall[i] = (v2 - v1) * SCANLINE + (h2 - h1) * DOT

    def fit(idxs):
        """least squares a + b*k through the empty slots given"""
        pts = [(SLOTS[i][1], wall[i]) for i in idxs]
        n = len(pts)
        sx = sum(k for k, _ in pts)
        sy = sum(w for _, w in pts)
        sxx = sum(k * k for k, _ in pts)
        sxy = sum(k * w for k, w in pts)
        den = n * sxx - sx * sx
        b = (n * sxy - sx * sy) / den
        a = (sy - b * sx) / n
        return a, b, pts

    print("== empty skeletons (linearity of the instrument) ==")
    for gname, idxs in (("empty16", (0, 1, 2, 3)), ("empty8", (8, 9)),
                        ("empty8a", (12, 13)), ("emptyC", (16, 17))):
        a, b, pts = fit(idxs)
        worst = 0.0
        for k, w in pts:
            f = a + b * k
            worst = max(worst, abs(w - f) / f)
        print("  %-8s = %8.1f + %8.1f*k   worst residual %.3f%%   (%d points)"
              % (gname, a, b, worst * 100, len(pts)))

    derived = DERIVED_FAST if fast else DERIVED_SLOW
    print()
    print("== master clocks per MAC, %s ==" % ("FastROM 3.58 MHz" if fast
                                               else "SlowROM 2.68 MHz"))
    print("%-10s %9s %9s %9s %8s %10s %9s" %
          ("kernel", "wall", "cpu", "derived", "err", "kMAC/s", "spread"))
    rows = []
    seen = {}
    for i, (name, outer) in enumerate(SLOTS):
        if name.startswith("empty"):
            continue
        seen.setdefault(name, []).append(i)
    for name, idxs in seen.items():
        a, b, _ = fit(GROUPS[name])
        per = []
        for i in idxs:
            outer = SLOTS[i][1]
            per.append((wall[i] - (a + b * outer)) / (outer * NELEM))
        w = per[0]
        spread = (max(per) - min(per)) / w
        cpu = w * CPU_FRACTION
        dv = derived[name]
        rows.append((w, name, cpu, dv, cpu / dv - 1.0, MASTER_HZ / w / 1000.0,
                     spread))
    rows.sort()
    for w, name, cpu, dv, err, kmac, spread in rows:
        print("%-10s %9.2f %9.2f %9.2f %7.2f%% %10.1f %8.3f%%"
              % (name, w, cpu, dv, err * 100, kmac, spread * 100))

    print()
    print("== correctness (each kernel's own sum) ==")
    for name, vi in VERIFY:
        got = le16(d, 0x200 + vi * 2)
        print("  %-10s = $%04X (%d)" % (name, got, got))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--fast" in sys.argv))
