# elya-snes — FINDINGS

Journal. Appended to after every discrete result. Newest at the bottom.

The single question this repo answers first: **which arithmetic primitive is
cheapest per multiply-accumulate on a stock SNES, in exact cycles.** No engine
is written until that is measured.

---

## 2026-08-10 — 1. A ROM boots under ares, and results come out through battery SRAM

**Toolchain.** `ca65`/`ld65` V2.18 with `--cpu 65816`. cc65 ships no SNES
linker config, so `rom/lorom32.cfg` is ours: a 32 KiB LoROM image where file
`$0000-$7FBF` maps to `$00:8000-$00:FFBF`, the cartridge header sits at
`$00:FFC0`, and the vectors at `$00:FFE0`. `tools/fixhdr.py` patches the
internal checksum after link.

**The first build was wrong and the ROM would have been silently mismapped.**
The 21-byte title field was written with 20 characters, so every header byte
after it shifted down one — map mode landed at `$FFD4` instead of `$FFD5`.
Caught by dumping `$7FC0` with `xxd`, not by any tool complaining. There is now
an `.assert .strlen(title) = 21` in `rom/snes.inc` so it cannot recur.

**Readout channel: battery SRAM.** The cartridge header declares
`carttype = $02` (ROM+RAM+battery) and `ramsize = $03` (8 KiB), which LoROM maps
at `$70:0000`. On exit **ares writes that RAM verbatim to `<romname>.ram` in the
same directory as the ROM.** That is the readout: the ROM stores numbers to
`$70:xxxx`, the host reads the file. No debugger, no savestate, no screen
scraping.

Verified:

```
$ xxd -l 16 ~/snesroms/boot.ram
00000000: 534e 4553 5aa5 d006 ffff ffff ffff ffff  SNESZ...........
```

`SNES` + `$5A $A5` is the magic the ROM writes once at startup. `d0 06` is the
live frame counter, little-endian = `$06D0` = 1744 frames = 29.0 s at 60.1 Hz,
which matches how long ares was left running. So the CPU booted, executed, and
kept executing.

**Operating notes for ares (flatpak build 147), the hard way:**

* The flatpak cannot see `/tmp`; ROMs are staged in `$HOME/snesroms`.
* `flatpak run dev.ares.ares --system "Super Famicom" --no-file-prompt rom.sfc`.
* SRAM is flushed on `SIGINT` (`pkill -INT -f "ares --system"`).
* **Screen capture is not available on this host.** `import -window root` gets
  nothing (the window is Wayland-native) and `grim` fails with *"compositor
  doesn't support wlr-screencopy-unstable-v1"* under GNOME. So an on-screen hex
  readout is not a usable channel here, and everything below goes through SRAM.

Status: task 1 done. Next: a cycle instrument.

---

## 2026-08-10 — 2. The instrument, and the two ways it lied before it worked

**ares has no scripting**, so there is no write-tap and no host-side cycle
counter. The counter is on the console: reading `$2137` latches the PPU's
horizontal and vertical counters, and `$213C`/`$213D` read them back. V counts
scanlines, H counts dots, one dot is 4 master clocks of the 21.477 MHz master
clock, and one NTSC scanline is 341 dots = 1364 master clocks:

    wall_master = (V2 - V1) * 1364 + (H2 - H1) * 4

**Resolution is 4 master clocks** — half of one SlowROM CPU cycle. Each
workload runs 128 to 2048 times inside one window, so the per-operation
resolution is a few thousandths of a cycle. No arithmetic happens on the
console: the ROM stores the four raw counter values to SRAM and the host does
the maths, so the timing model can be changed without rebuilding a ROM.

Accounting: every primitive runs inside the same loop skeleton, and `empty` is
that skeleton with the bodies deleted. Subtracting `empty` cancels the loop
bookkeeping exactly. `empty` is measured at four lengths, and a least-squares
line through them is the linearity check:

```
 outer   V1   H1   V2   H2     wall       fit   resid
     2    0  167    3  201     4228    4212.0   +16.0
     4    0  189    6   70     7708    7716.0    -8.0
     8    0  175   11  101    14708   14724.0   -16.0
    16    0  185   21  211    28748   28740.0    +8.0
empty(k) = 708.0 + 1752.0*k        worst residual 0.056%
```

### The instrument was wrong twice, and both were silent

**Lie 1 — the V counter wraps and nothing complains.** The first sweep sized
its measurement windows from my own cost estimates. Three of them ran longer
than one 262-scanline frame, the V counter rolled over, and the arithmetic
produced *smaller* numbers than the truth. `cpuhw-packed` came out at **-9.67
master clocks per MAC** — a negative cost. That one is obvious. But `softmul`
came out at 107.8, a perfectly plausible-looking number that was wrong by a
factor of 14, and `ppu-m7` came out at 46.0, which would have made it the
fastest primitive on the machine by a wide margin and *confirmed the
prediction this whole exercise is testing*. A believable wrong number is far
more dangerous than a negative one.

Fixed two ways, belt and braces: every window is now sized under ~140
scanlines, and the ROM reads `$4210` (RDNMI) before and after each window — if
its vblank flag got set inside the window, the run touched scanline 225 and the
measurement is rejected outright rather than reported.

**Lie 2 — the calibration disagreed, and the instrument was right.** With the
wrap fixed, four of five calibration bodies came out uniformly **+3.0%** above
hand-derived. That is not noise, it is a constant: the SNES freezes the CPU for
**40 master clocks once per scanline for DRAM refresh**, and 40/1364 = 2.93%,
i.e. wall clock = CPU time / 0.970674. ares models it. Applying that factor:

```
body                 wall      cpu  derived    error
nop                14.414   13.991       14   -0.06%
lda abs,y          47.391   46.001       46    0.00%
lda dp             32.961   31.994       32   -0.02%
clc + adc dp       47.391   46.001       46    0.00%
lda $2134          37.098   36.010       36    0.03%
```

The fifth body, `lda abs,y`, still disagreed after the refresh correction — 46
measured against 40 derived, exactly one 6-master-clock internal cycle. **The
instrument was right and my derivation was wrong.** With 16-bit index registers
(X flag = 0) the 65816 takes the extra internal cycle on absolute-indexed
addressing *always*, not only when the address crosses a page. Every indexed
load in every primitive here is 6 cycles, not 5. Had I trusted the derivation
over the instrument I would have under-counted every primitive by 6 master
clocks per indexed access.

So: **calibration PASS, all five bodies within 0.06% of hand-derived.**

### Two more checks before any number is quoted

**Correctness.** Each primitive also runs one pass over the same 128 operands
and writes its 16-bit accumulator to SRAM; `tools/gendata.py` prints what each
one must produce. This caught a real bug: `cpuhw-packed` read `$4216` at
exactly 8 CPU cycles after the write to `$4203` and returned `$6A38` instead of
`$6D38`. Documented latency is "8 cycles"; measured, 8 is not enough and 10 is.
A timing number from that code would have been a fast wrong answer. All seven
primitives now verify.

**Self-check.** Every primitive is measured at two different window lengths.
Worst spread across all of them: **0.036%**.

Instrument: trustworthy. Files: `rom/bench.s`, `tools/analyze.py`,
`tools/run_ares.sh`, raw dump in `out/bench.ram`.

---

## 2026-08-10 — 3. Six primitives, measured. Cycles per multiply-accumulate.

Every number below is measured on the instrument calibrated in entry 2, on
operand arrays that each primitive is separately proved to compute correctly.
Nothing here is estimated. Both clock configurations were built and run: the
same source, `-DFASTROM=1` switching map mode `$20`→`$30`, `MEMSEL` bit 0, and
execution to bank `$80`.

Calibration for the FastROM build, hand-derived independently:

```
body                 wall      cpu  derived    error
nop                12.353   11.991       12   -0.08%
lda abs,y          37.093   36.006       36    0.02%
lda dp             28.861   28.015       28    0.05%
clc + adc dp       41.211   40.002       40    0.00%
lda $2134          30.919   30.013       30    0.04%
```

### SlowROM, 2.68 MHz

```
primitive                  wall      cpu     cyc       ns   kMAC/s  x ternary
softmul                  1503.9   1459.8   182.5    70023     14.3     13.77x
qsquare                   570.9    554.2    69.3    26583     37.6      5.23x
dsp1-bus+status           373.0    362.0    45.3    17365     57.6      3.42x
ppu-m7-naive              333.8    324.0    40.5    15543     64.3      3.06x
dsp1-bus-floor            323.5    314.0    39.2    15061     66.4      2.96x
cpuhw                     317.4    308.0    38.5    14776     67.7      2.91x
ppu-m7                    220.5    214.0    26.8    10266     97.4      2.02x
ppu-m7 (screen on)        220.5    214.0    26.8    10265     97.4      2.02x
cpuhw-packed              164.8    160.0    20.0     7674    130.3      1.51x
ternary                   109.2    106.0    13.2     5084    196.7      1.00x
```

### FastROM, 3.58 MHz

```
primitive                  wall      cpu     cyc       ns   kMAC/s  x ternary
softmul                  1327.2   1288.2   161.0    61794     16.2     15.34x
qsquare                   467.6    453.9    56.7    21772     45.9      5.40x
dsp1-bus+status           309.1    300.0    37.5    14391     69.5      3.57x
ppu-m7-naive              274.0    266.0    33.2    12758     78.4      3.17x
dsp1-bus-floor            267.8    260.0    32.5    12470     80.2      3.10x
cpuhw                     261.6    253.9    31.7    12179     82.1      3.02x
ppu-m7                    179.2    174.0    21.7     8344    119.8      2.07x
ppu-m7 (screen on)        179.3    174.0    21.8     8348    119.8      2.07x
cpuhw-packed              136.0    132.0    16.5     6332    157.9      1.57x
ternary                    86.5     84.0    10.5     4029    248.2      1.00x
```

`wall` is master clocks including the DRAM refresh stall — real elapsed time.
`cpu` removes refresh and is what the 65816 datasheet predicts. `cyc` is cpu/8.
The ordering is identical in every column and in both clock configurations.

### What each row actually is

**softmul** — 8-round shift-add, multiplicand shifting left, multiplier
shifting right, product in A. The multiplier lives in a 16-bit direct-page word
because with 16-bit A a `lsr dp` is a 16-bit shift whether you want one or not,
and that alone is 7 cycles × 8 rounds. **180 CPU cycles per MAC.** It is not
close to competitive and no amount of polishing brings it within 5x of the
hardware paths, which is why I did not keep optimising it.

**qsquare** — quarter-square tables, `a*b = QS1[a+b] - QS2[(a-b)+255]`, exact
for all 8-bit unsigned pairs. **Note the brief asked for a 256-byte table: that
size cannot do an exact 8×8 product.** The identity needs indices spanning
0..510, so the real cost is two 511-entry 16-bit tables = **2044 bytes**, and
even then it loses to the CPU multiplier by 1.8x, because each MAC needs two
index computations, two `asl`/`tax` pairs and two table reads. On a machine
with no multiplier (the NES) this technique wins. On the SNES it is dead
weight — the silicon already does it faster.

**cpuhw** (`$4202`/`$4203`) — unsigned 8×8→16. Two 8-bit operand writes, then
enough work to cover the 8-cycle latency, then a 16-bit read of `$4216`.
**cpuhw-packed** is the same unit fed by a single 16-bit store that lands both
operands at once and starts the multiply; that is the fastest int8 MAC on the
machine at 132 CPU cycles... **but it is a lower bound, not a usable number**:
it requires the weight and the activation to be pre-interleaved in one word
array, and interleaving two arrays that both vary per MAC costs more than the
MAC. Quoted for the floor, not for the design.

The 8-cycle latency is not advisory. Reading `$4216` at exactly 8 CPU cycles
after the `$4203` write returned the wrong product; 10 cycles is correct. The
correctness pass caught this, the timing pass would not have.

**ppu-m7** (`$211B`/`$211C`) — the signed one, and the honest winner among the
multiply paths at **214 / 174 CPU cycles per MAC**. Two 16-bit stores cover all
three register writes, because a 16-bit store spans two consecutive registers:
`stx $211A` puts the weight in `$211B` (M7A low byte) and `stx $211B` puts zero
in `$211B` (M7A high byte) and the activation in `$211C` (M7B). The accumulator
never leaves A. The naive form — 8-bit stores, two explicit writes to `$211B`,
accumulator in memory, mode switches — costs **324 / 266**, i.e. **the M7A
two-write requirement plus 8-bit register discipline costs 1.5x**.

**The vblank-only constraint is smaller than it sounds, and I measured it
rather than assuming.** The Mode 7 registers are unusable only while the PPU is
*rendering* Mode 7. In BG modes 0-6 they belong to the program all frame. The
table above has two entries: forced blank (screen off, `$2100 = $8F`) and
screen genuinely on with BG1 enabled during active display. They differ by
**0.03%** — 220.51 vs 220.51 wall master clocks. The SNES CPU is not stalled by
rendering. So the constraint is "do not use Mode 7 for graphics", which for a
text-generating cartridge is not a constraint at all.

**dsp1** — see entry 4; it is a bounded floor, not a measurement of the chip.

**ternary** — sign-separated gather, no multiply anywhere: `ldx IDX,y` /
`clc` / `adc XS16,x` for the +1 list, `sec` / `sbc` for the -1 list, zeros never
appearing at all. **106 / 84 CPU cycles per MAC.**

### The prediction: REFUTED

The prediction under test was that the SNES, being the first target with a fast
**signed** hardware multiply, would be where int8 finally beats ternary.

It does not. **Ternary is 2.02x cheaper than the best usable int8 MAC on
SlowROM and 2.07x cheaper on FastROM**, and still 1.51x / 1.57x cheaper than
the pre-packed CPU-multiplier floor that no real engine can reach. Ternary
would have to be denser than 100% non-zero for int8 to win, which is not a
thing. At a realistic ternary density of 50% the margin is 4x.

The reason is visible in the numbers and it is not about the multiplier at all.
**The multiply is free; the operands are not.** The Mode 7 unit produces a
signed 16×8 product in zero cycles — the entire 214-cycle cost of `ppu-m7` is
moving data: two operand loads (46 each), two register writes (36 each), one
product read (36), and the accumulate (46). Ternary's 106 cycles are one index
load (46), one accumulate (46) and a carry clear (14). Ternary wins because it
touches **three** memory locations per accumulate where int8 touches **six**,
and on a 65816 every one of those is 5-6 cycles of address and data traffic.

A faster multiplier cannot fix that. It is the same conclusion the NES and
Genesis ports reached, arrived at from the opposite direction: there the
multiplier was absent, here it is free, and ternary wins either way because the
binding constraint on this class of machine is **memory traffic per
accumulate**, not arithmetic. The N64's RSP is the exception that proves it —
it beat ternary because a vector unit amortises operand movement across 8 lanes,
not because its multiplier was fast.

A refutation, plainly stated: **int8 does not beat ternary on the SNES.**

Raw dumps: `out/bench.ram`, `out/benchfast.ram`.
Full reports: `out/bench_report.txt`, `out/benchfast_report.txt`.
