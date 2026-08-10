#!/usr/bin/env python3
"""Turn an nnprof.ram dump into master clocks per generated token.

A token takes eight NTSC frames, so the sub-frame H/V instrument of FINDINGS
entry 2 cannot span one on its own: the V counter wraps.  This instrument adds
a vblank counter maintained by the NMI and stitches the two together,

    time(F,V,H) = F * FRAME + ((V - 225) mod 262) * 1364 + H * 4

which is monotonic across the wrap because the NMI fires at scanline 225 and
that is exactly where the second term restarts.

FRAME is MEASURED, not assumed to be 262*1364.  A loop of fixed shape is timed
sub-frame at four lengths, a straight line is fitted through those, and the
same loop is then run long enough to span several frames; the frame length is
whatever makes the two agree.  A useful side effect: the NMI handler is running
during the calibration too, so whatever it steals per frame is already inside
the calibrated FRAME and is therefore removed from every number below.
"""
import sys

DOT = 4
LINE = 1364
LINES = 262
CPU_FRACTION = 1.0 - 40 / LINE
MASTER_HZ = 21477272.0
SLOW_HZ = 2684658.0          # 21477272 / 8
FAST_HZ = 3579545.0          # 21477272 / 6
NMI_LINE = 225

CAL_BASE = 0x100
CAL_KS = (1000, 2000, 3000, 4000)
LONG_BASE = CAL_BASE + 8 * 6
LONG_K = 60000
TOK_BASE = 0x140


def le16(b, o):
    return b[o] | (b[o + 1] << 8)


def sample(d, i, base):
    o = base + i * 6
    return le16(d, o), le16(d, o + 2), le16(d, o + 4)


def lin(v, h):
    return ((v - NMI_LINE) % LINES) * LINE + h * DOT


def main(path, fast=False):
    d = open(path, "rb").read()
    if d[0:4] != b"ELYA" or d[8:12] != b"DONE":
        print("bad or unfinished dump", file=sys.stderr)
        return 1
    ngen = d[4]

    # ---- the frame length ------------------------------------------------
    pts = []
    for i, k in enumerate(CAL_KS):
        f1, v1, h1 = sample(d, 2 * i, CAL_BASE)
        f2, v2, h2 = sample(d, 2 * i + 1, CAL_BASE)
        assert f1 == f2, "calibration window %d crossed a frame" % k
        pts.append((k, (v2 - v1) * LINE + (h2 - h1) * DOT))
    n = len(pts)
    sx = sum(k for k, _ in pts)
    sy = sum(w for _, w in pts)
    sxx = sum(k * k for k, _ in pts)
    sxy = sum(k * w for k, w in pts)
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    inter = (sy - slope * sx) / n
    print("== the multi-frame instrument's calibration ==")
    for k, w in pts:
        fit = inter + slope * k
        print("  cal(%5d) = %9d   fit %11.1f   resid %+7.3f%%"
              % (k, w, fit, 100 * (w - fit) / fit))
    print("  loop = %.1f + %.4f*k master clocks" % (inter, slope))

    f1, v1, h1 = sample(d, 0, LONG_BASE)
    f2, v2, h2 = sample(d, 1, LONG_BASE)
    frames = f2 - f1
    expect = inter + slope * LONG_K
    frame_len = (expect - (lin(v2, h2) - lin(v1, h1))) / frames
    print("  cal(%d) spans %d frames; measured FRAME = %.1f master clocks"
          % (LONG_K, frames, frame_len))
    print("  262 * 1364 = %d, so the measurement is %+.3f%% from nominal"
          % (LINES * LINE, 100 * (frame_len / (LINES * LINE) - 1)))

    # ---- per token --------------------------------------------------------
    s = [sample(d, i, TOK_BASE) for i in range(ngen + 1)]
    walls = []
    for i in range(ngen):
        f1, v1, h1 = s[i]
        f2, v2, h2 = s[i + 1]
        walls.append((f2 - f1) * frame_len + lin(v2, h2) - lin(v1, h1))
    print()
    print("== master clocks per generated token ==")
    print("%4s %12s %12s %10s" % ("pos", "wall", "cpu", "ms"))
    for i, w in enumerate(walls):
        print("%4d %12.0f %12.0f %10.2f"
              % (i, w, w * CPU_FRACTION, 1000 * w / MASTER_HZ))
    tot = sum(walls)
    mean = tot / len(walls)
    hz = FAST_HZ if fast else SLOW_HZ
    print()
    print("  total        %12.0f wall master clocks for %d tokens"
          % (tot, len(walls)))
    print("  mean/token   %12.0f wall   %12.0f cpu" % (mean, mean * CPU_FRACTION))
    print("  mean/token   %12.0f %s cycles"
          % (mean * CPU_FRACTION / (MASTER_HZ / hz),
             "FastROM" if fast else "SlowROM"))
    print("  seconds/token  %8.4f      tokens/s  %8.3f"
          % (mean / MASTER_HZ, MASTER_HZ / mean))
    print("  first / last token: %.0f / %.0f  (%.1f%% growth over %d positions)"
          % (walls[0], walls[-1], 100 * (walls[-1] / walls[0] - 1), len(walls)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--fast" in sys.argv))
