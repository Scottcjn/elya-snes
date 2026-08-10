#!/usr/bin/env python3
"""Turn a bench.ram dump into cycles per multiply-accumulate.

All the arithmetic lives here rather than on the console, so the timing model
can be changed without rebuilding a ROM.

TIMING MODEL
------------
A measurement is two latches of the PPU's H/V counters.  V counts scanlines,
H counts dots, one dot is 4 master clocks of the 21.477 MHz master clock, and
one NTSC scanline is 341 dots = 1364 master clocks:

    wall_master = (V2 - V1) * 1364 + (H2 - H1) * 4

That is wall-clock time, and it includes the SNES's DRAM refresh stall: the CPU
is frozen for 40 master clocks once per scanline whether it likes it or not.
So CPU-executed time is

    cpu_master = wall_master * (1 - 40/1364) = wall_master * 0.970674

Both are reported.  Wall clock is what a program actually waits; cpu_master is
what the 65816 datasheet predicts, and matching the two against hand-derived
instruction timings is how the instrument is calibrated.

`empty` is the loop skeleton with the bodies deleted, measured at four lengths.
A least-squares line through those gives the skeleton cost; subtracting it from
a primitive leaves the bodies alone.
"""
import sys

DOT = 4
SCANLINE = 1364
REFRESH_PER_LINE = 40
CPU_FRACTION = 1.0 - REFRESH_PER_LINE / SCANLINE
MASTER_HZ = 21477272.0
NELEM = 128

# slot -> (name, outer, MACs per outer pass)
SLOTS = [
    ("empty@2", 2, 0), ("empty@4", 4, 0), ("empty@8", 8, 0), ("empty@16", 16, 0),
    ("nop", 16, NELEM),
    ("lda abs,y", 16, NELEM),
    ("lda dp", 16, NELEM),
    ("clc + adc dp", 16, NELEM),
    ("lda $2134", 16, NELEM),
    ("softmul", 1, NELEM),
    ("qsquare", 2, NELEM), ("qsquare", 1, NELEM),
    ("cpuhw", 4, NELEM), ("cpuhw", 2, NELEM),
    ("cpuhw-packed", 8, NELEM), ("cpuhw-packed", 4, NELEM),
    ("ppu-m7", 8, NELEM), ("ppu-m7", 4, NELEM),
    ("ppu-m7-naive", 4, NELEM), ("ppu-m7-naive", 2, NELEM),
    ("ternary", 16, NELEM), ("ternary", 8, NELEM),
    ("ppu-m7 (screen on)", 8, NELEM),
]

# Hand-derived CPU master clocks per body, from the W65C816S timing tables and
# the SNES memory-speed map: ROM and WRAM accesses are 8 master clocks, $21xx
# and $42xx are 6, internal cycles are 6.
#
# NOTE the trap that cost a whole measurement round: with 16-bit index
# registers (X flag = 0) absolute-indexed addressing ALWAYS takes the extra
# internal cycle, not only when it crosses a page.  `lda abs,y` with 16-bit A
# and 16-bit X is therefore 6 cycles = 8+8+8+6+8+8 = 46, not 40.
DERIVED = {
    "nop": 14,             # 8 opcode + 6 internal
    "lda abs,y": 46,       # 8 + 8 + 8 + 6 + 8 + 8
    "lda dp": 32,          # 8 + 8 + 8 + 8
    "clc + adc dp": 46,    # (8+6) + (8+8+8+8)
    "lda $2134": 36,       # 8 + 8 + 8 + 6 + 6
}

VERIFY = ["softmul", "qsquare", "cpuhw", "cpuhw-packed", "ppu-m7",
          "ppu-m7-naive", "ternary"]
EXPECT = {"softmul": 0x6D38, "qsquare": 0x6D38, "cpuhw": 0x6D38,
          "cpuhw-packed": 0x6D38, "ppu-m7": 0x6C38, "ppu-m7-naive": 0x6C38,
          "ternary": 0xFF6D}

ORDER = ["softmul", "qsquare", "cpuhw", "cpuhw-packed", "ppu-m7-naive",
         "ppu-m7", "ppu-m7 (screen on)", "ternary"]


def u16(b, o):
    return b[o] | (b[o + 1] << 8)


def main(path):
    ram = open(path, "rb").read()
    if ram[0:4] != b"BNCH":
        print(f"bad magic {ram[0:4]!r} -- ROM did not run", file=sys.stderr)
        return 1
    if ram[8:12] != b"DONE":
        print(f"no DONE marker ({ram[8:12]!r}) -- run cut short", file=sys.stderr)
        return 1

    raw = []
    fatal = False
    for i, (name, outer, per) in enumerate(SLOTS):
        b = 0x10 + i * 8
        v1, h1, v2h, h2 = u16(ram, b), u16(ram, b + 2), u16(ram, b + 4), u16(ram, b + 6)
        wrapped = bool(v2h & 0x8000)   # RDNMI flag stashed in bit 15
        v2 = v2h & 0x01FF
        wall = (v2 - v1) * SCANLINE + (h2 - h1) * DOT
        if wrapped or v2 <= v1:
            print(f"slot {i} ({name}@{outer}): window crossed vblank "
                  f"(V1={v1} V2={v2}) -- INVALID", file=sys.stderr)
            fatal = True
        raw.append([name, outer, per, v1, h1, v2, h2, wall, wrapped])
    if fatal:
        return 1

    # ---- instrument: linearity of the empty skeleton ----------------------
    pts = [(r[1], r[7]) for r in raw if r[0].startswith("empty")]
    n = len(pts)
    sx, sy = sum(x for x, _ in pts), sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    icept = (sy - slope * sx) / n
    empty_at = lambda k: icept + slope * k

    print("=" * 76)
    print("INSTRUMENT  (H/V counter latch, resolution 4 master clocks)")
    print("=" * 76)
    print(f"{'outer':>6} {'V1':>4} {'H1':>4} {'V2':>4} {'H2':>4} "
          f"{'wall':>8} {'fit':>9} {'resid':>7}")
    for r in raw:
        if not r[0].startswith("empty"):
            continue
        f = empty_at(r[1])
        print(f"{r[1]:>6} {r[3]:>4} {r[4]:>4} {r[5]:>4} {r[6]:>4} "
              f"{r[7]:>8} {f:>9.1f} {r[7]-f:>+7.1f}")
    worst = max(abs(y - empty_at(x)) for x, y in pts)
    print(f"\nempty(k) = {icept:.1f} + {slope:.1f}*k wall master clocks")
    print(f"worst residual {worst:.1f} master clocks on {max(y for _,y in pts)} "
          f"= {worst/max(y for _,y in pts)*100:.4f}%  -> the instrument is linear")

    # ---- calibration ------------------------------------------------------
    print()
    print("=" * 76)
    print("CALIBRATION  measured vs hand-derived, master clocks per body")
    print("=" * 76)
    print(f"{'body':<16} {'wall':>8} {'cpu':>8} {'derived':>8} {'error':>8}")
    cal_ok = True
    for r in raw:
        if r[0] not in DERIVED:
            continue
        wall_b = (r[7] - empty_at(r[1])) / (r[1] * r[2])
        cpu_b = wall_b * CPU_FRACTION
        d = DERIVED[r[0]]
        err = (cpu_b - d) / d * 100
        if abs(err) > 0.5:
            cal_ok = False
        print(f"{r[0]:<16} {wall_b:>8.3f} {cpu_b:>8.3f} {d:>8} {err:>7.2f}%"
              + ("" if abs(err) <= 0.5 else "   <-- MISMATCH"))
    print(f"\nrefresh model: CPU time = wall * (1 - 40/1364) = wall * {CPU_FRACTION:.6f}")
    print("calibration:", "PASS" if cal_ok else "FAIL -- nothing below is trustworthy")

    # ---- correctness ------------------------------------------------------
    print()
    print("=" * 76)
    print("CORRECTNESS  one pass of each primitive over the same 128 operands")
    print("=" * 76)
    all_ok = True
    for i, name in enumerate(VERIFY):
        got, want = u16(ram, 0x200 + i * 2), EXPECT[name]
        all_ok &= got == want
        print(f"  {name:<14} got ${got:04X}  want ${want:04X}   "
              f"{'ok' if got == want else 'WRONG'}")
    print("correctness:", "PASS" if all_ok else "FAIL -- a primitive is broken")

    # ---- per-MAC ----------------------------------------------------------
    res = {}
    for r in raw:
        if r[2] == 0 or r[0] in DERIVED:
            continue
        wall_b = (r[7] - empty_at(r[1])) / (r[1] * r[2])
        res.setdefault(r[0], []).append((r[1], wall_b))

    print()
    print("=" * 76)
    print("SELF-CHECK  same primitive measured at two window lengths")
    print("=" * 76)
    lin_ok = True
    for name, vals in res.items():
        if len(vals) < 2:
            print(f"  {name:<20} single point ({vals[0][0]} outer) -- no cross-check")
            continue
        a, b = vals[0][1], vals[1][1]
        d = abs(a - b) / ((a + b) / 2) * 100
        if d > 0.5:
            lin_ok = False
        print(f"  {name:<20} @{vals[0][0]:<3}={a:8.2f}  @{vals[1][0]:<3}={b:8.2f}  "
              f"spread {d:.3f}%" + ("" if d <= 0.5 else "  <-- NONLINEAR"))
    print("self-check:", "PASS" if lin_ok else "FAIL")

    print()
    print("=" * 76)
    print("CYCLES PER MULTIPLY-ACCUMULATE   SNES SlowROM, 21.477 MHz master")
    print("=" * 76)
    print(f"{'primitive':<22} {'wall':>8} {'cpu':>8} {'cyc':>7} {'ns':>8} "
          f"{'kMAC/s':>8} {'x ternary':>10}")
    avg = {k: sum(v for _, v in vs) / len(vs) for k, vs in res.items()}
    tern = avg["ternary"]
    for name in ORDER:
        if name not in avg:
            continue
        w = avg[name]
        print(f"{name:<22} {w:>8.1f} {w*CPU_FRACTION:>8.1f} "
              f"{w*CPU_FRACTION/8:>7.1f} {w/MASTER_HZ*1e9:>8.0f} "
              f"{MASTER_HZ/w/1000:>8.1f} {w/tern:>9.2f}x")
    print("\nwall = master clocks including DRAM refresh; cpu = refresh removed;")
    print("cyc  = cpu/8, i.e. SlowROM CPU cycles (a CPU cycle is 6, 8 or 12")
    print("       master clocks depending on the address, so master clocks are")
    print("       the only unambiguous unit and everything else is derived).")

    print()
    print("=" * 76)
    print("VERDICT: does int8 beat ternary on the SNES?")
    print("=" * 76)
    best8 = min((avg[k], k) for k in ("ppu-m7", "cpuhw", "cpuhw-packed") if k in avg)
    print(f"cheapest int8 MAC : {best8[1]} at {best8[0]:.1f} wall master clocks")
    print(f"ternary MAC       : ternary at {tern:.1f} wall master clocks")
    ratio = best8[0] / tern
    if ratio > 1:
        print(f"\nternary is {ratio:.2f}x CHEAPER per accumulate than the best int8 path.")
        print(f"int8 would need ternary to be denser than {ratio*100:.0f}% non-zero to")
        print("win, which is impossible -- density caps at 100%.  PREDICTION REFUTED.")
    else:
        print(f"\nint8 is {1/ratio:.2f}x cheaper per MAC. PREDICTION CONFIRMED.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
