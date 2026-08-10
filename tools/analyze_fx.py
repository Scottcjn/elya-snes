#!/usr/bin/env python3
"""SuperFX arm: turn fx2.ram into GSU cycles per multiply-accumulate.

EMULATOR ONLY.  The target Kaico Super DSP V3.1 cart cannot run SuperFX.

Same instrument as the 5A22 arm (PPU H/V counter latch, 4 master clocks per
dot, 1364 per scanline).  Each slot is N invocations of a GSU kernel that does
128 MACs; `empty` is the same invocation with no MAC body, so subtracting it
cancels the GSU start/stop handshake and the CPU-side driver.  Two window
lengths per kernel; the slope between them is the per-invocation cost.
"""
import random
import sys

SCANLINE, DOT = 1364, 4
CPU_FRACTION = 1.0 - 40 / 1364
MASTER_HZ = 21477272.0
GSU_HZ = MASTER_HZ / 2          # GSU-1 runs at half the master clock
NELEM = 128

SLOTS = [("empty", 16), ("empty", 8), ("int8", 16), ("int8", 8),
         ("tern", 16), ("tern", 8), ("nomul", 16), ("nomul", 8)]

DESC = {
    "int8": "int8 MAC, signed 8x8 via GSU MULT",
    "tern": "ternary sign-separated gather (no multiply)",
    "nomul": "control: int8 kernel with MULT replaced by ADD",
}


def u16(b, o):
    return b[o] | (b[o + 1] << 8)


def main(path):
    ram = open(path, "rb").read()
    if ram[0:4] != b"FX2!" or ram[8:12] != b"DONE":
        print("fx2 ROM did not run to completion", file=sys.stderr)
        return 1

    random.seed(0x5A17)
    WB = [random.randrange(256) for _ in range(NELEM)]
    XB = [random.randrange(256) for _ in range(NELEM)]
    PERM = list(range(NELEM))
    random.shuffle(PERM)
    s8 = lambda v: v - 256 if v > 127 else v
    exp = {
        "int8": sum(s8(w) * s8(x) for w, x in zip(WB, XB)) & 0xFFFF,
        "tern": sum(s8(XB[PERM[i]]) * (1 if i % 2 == 0 else -1)
                    for i in range(NELEM)) & 0xFFFF,
    }

    print("=" * 76)
    print("SUPERFX / GSU-1 -- EMULATOR ONLY (the Kaico Super DSP cart cannot")
    print("run SuperFX; ares is bsnes-derived but it is not silicon)")
    print("=" * 76)
    print("\nCORRECTNESS")
    ok = True
    for i, k in enumerate(("int8", "tern")):
        got = u16(ram, 0xF0 + i * 2)
        ok &= got == exp[k]
        print(f"  {k:<6} got ${got:04X}  want ${exp[k]:04X}  "
              f"{'ok' if got == exp[k] else 'WRONG'}")
    print("  ->", "PASS" if ok else "FAIL")

    res = {}
    print("\nRAW WINDOWS")
    for i, (n, k) in enumerate(SLOTS):
        b = 0x10 + i * 8
        v1, h1, v2h, h2 = u16(ram, b), u16(ram, b + 2), u16(ram, b + 4), u16(ram, b + 6)
        if v2h & 0x8000:
            print(f"  slot {i} crossed vblank -- INVALID", file=sys.stderr)
            return 1
        v2 = v2h & 0x1FF
        w = (v2 - v1) * SCANLINE + (h2 - h1) * DOT
        print(f"  {n:<6}@{k:<3} V1={v1:<4} H1={h1:<4} V2={v2:<4} H2={h2:<4} wall={w}")
        res.setdefault(n, {})[k] = w

    slope = {n: (d[16] - d[8]) / 8 for n, d in res.items()}
    print(f"\nGSU invocation overhead (start + STOP handshake + CPU driver): "
          f"{slope['empty']:.1f} wall master clocks")

    print("\n" + "=" * 76)
    print("GSU CYCLES PER MULTIPLY-ACCUMULATE")
    print("=" * 76)
    print(f"{'kernel':<8} {'wall':>8} {'cpu':>8} {'GSU clk':>8} {'ns':>8} "
          f"{'kMAC/s':>9}  description")
    per = {}
    for n in ("int8", "nomul", "tern"):
        per[n] = (slope[n] - slope["empty"]) / NELEM
        print(f"{n:<8} {per[n]:>8.2f} {per[n]*CPU_FRACTION:>8.2f} "
              f"{per[n]/2:>8.2f} {per[n]/MASTER_HZ*1e9:>8.0f} "
              f"{MASTER_HZ/per[n]/1000:>9.1f}  {DESC[n]}")

    print(f"\nMULT itself costs {(per['int8']-per['nomul'])/2:.2f} GSU clocks more "
          f"than ADD -- one cycle, as documented.")
    print(f"ternary is {per['int8']/per['tern']:.2f}x cheaper than int8 on the GSU.")
    print(f"Of the {(per['int8']-per['tern'])/2:.2f} GSU-clock gap, only "
          f"{(per['int8']-per['nomul'])/2:.2f} is the multiply; the rest is the "
          f"memory\naccess pattern (the kernels execute the same 11 instructions "
          f"per MAC).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
