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
dsp1-bus+cmd+status       476.1    462.2    57.8    22168     45.1      4.36x
dsp1-bus+status           373.0    362.1    45.3    17367     57.6      3.42x
ppu-m7-naive              333.8    324.0    40.5    15542     64.3      3.06x
dsp1-bus-floor            323.5    314.0    39.2    15061     66.4      2.96x
cpuhw                     317.3    308.0    38.5    14773     67.7      2.91x
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
dsp1-bus+cmd+status       391.7    380.2    47.5    18240     54.8      4.53x
dsp1-bus+status           309.1    300.0    37.5    14391     69.5      3.57x
ppu-m7-naive              274.1    266.1    33.3    12763     78.4      3.17x
dsp1-bus-floor            267.9    260.1    32.5    12474     80.2      3.10x
cpuhw                     261.8    254.1    31.8    12188     82.1      3.02x
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

---

## 2026-08-10 — 4. DSP-1: a bounded floor, not a measurement of the chip

Target cart is a Kaico Super DSP V3.1, which carries DSP-1/2/3/4, so the DSP-1
is a legitimate third arithmetic unit: NEC uPD77C25 at ~8 MHz, a real 16-bit
multiply-accumulator, its own data RAM, and it runs in parallel with the CPU.

**What I could not do, plainly: the DSP-1 was not run.** ares does emulate the
uPD7725 family (the binary carries `DSP1`, `DSP1B`, `DSP2`, `DSP3`, `DSP4` and
`processor(architecture=uPD7725)`), but it needs the chip's firmware —
`upd7725.program.rom` and `upd7725.data.rom` — supplied in the game folder.
That firmware is Nintendo/NEC silicon microcode. A search of the whole
filesystem turned up no copy, and there is no legitimate way to synthesise one:
the DSP-1's usefulness *is* its fixed firmware. So there is no measurement of
DSP-1 execution time here, and I am not going to pretend otherwise.

**What I could measure exactly, and did.** The DSP-1 answers only through the
cartridge bus. In LoROM its data register is at `$30:8000` and its status
register at `$30:C000` ([SNESdev wiki](https://snes.nesdev.org/wiki/DSP-1)).
Those are ordinary cartridge addresses, and **65816 access timing is decided by
the address, not by whether a chip answers** — so the CPU-side transfer
sequence costs exactly the same cycles with the chip absent. That side is fully
measurable on this instrument, and it is a hard floor on the total.

Documented op `$00` is a 16-bit multiply whose product is "rounded to <= 15
bits" — a fixed-point multiply, not an integer one. int8 operands are still
usable pre-scaled: feed `w<<8` and `x<<7` and the returned high word is `w*x`.
Pre-scaling is free (weights are static; activations shift once at production).

One thing the documentation does not settle: whether the DSP-1 remembers its
last command and accepts bare parameters for a repeat. The wiki does not say.
Rather than estimate, I measured the command byte both ways, which brackets the
truth:

| variant | what it includes | SlowROM cpu | FastROM cpu |
|---|---|---|---|
| `dsp1-bus-floor` | 2 operand writes + 1 result read | 314 | 260 |
| `dsp1-bus+status` | + one status-register read | 362 | 300 |
| `dsp1-bus+cmd+status` | + command byte written per MAC | 462 | 380 |

**None of these include a single cycle of DSP execution time or one iteration
of an RQM polling loop.** A correct driver must poll `SR` bit 7 until the chip
signals ready, and each poll iteration is another ~40-50 master clocks. The
real cost is at or above the top of that bracket.

**Conclusion: the DSP-1 cannot win here, and the 2.2x clock advantage is
irrelevant.** Even its most optimistic floor, 314 CPU cycles per MAC, is
**1.5x worse than the PPU Mode 7 multiplier** at 214, which needs no handshake
at all, and **3x worse than ternary** at 106. The reason is the same one that
decided the whole table: a DSP-1 MAC moves six bytes plus a status byte across
the cart bus, and the SNES charges 8 (or 6) master clocks for every one of
them. A coprocessor that answers through a byte-wide port cannot outrun an
on-die register file, however fast its multiplier is.

This is a refutation of the coprocessor idea on transfer cost, exactly as the
scope note asked me to test rather than argue. If the firmware ever becomes
available the measurement should be redone — but it can only move the number
up from 314, never down, so the ordering will not change.

---

## 2026-08-10 — 5. SuperFX, phase two. EMULATOR ONLY.

**Label first, because it matters: none of this has a path to real silicon.**
The target cart is a Kaico Super DSP V3.1, which carries DSP-1/2/3/4 and
**cannot run SuperFX**. ares's SNES core is bsnes-derived, which is the
accurate lineage, but a bsnes-derived GSU model is not a GSU. Everything in
this entry is emulator-only and should be read that way. The non-FX table in
entry 3 is the one that decides the design.

Asar 1.91 (`~/bin/asar`, `arch superfx`, `--no-title-check`) assembles GSU
code; ca65 does the 65816 side and the kernels are `.incbin`'d.

### Getting a GSU to run at all: three silent failures

**ares instantiates a GSU from the header alone** — map mode `$20`, cart type
`$15`, no manifest, no game folder. Confirmed by writing `$1234`/`$ABCD` into
GSU R0/R1 at `$00:3000` and reading them back intact, with VCR = `$04`.

Then three failures in a row, none of which announced themselves:

1. **The poll raced the start.** Writing R15 does not set the GO flag on the
   very next CPU cycle in ares. A naive "wait until GO clears" saw GO=0, decided
   the GSU had finished, and pulled SCMR out from under a *running* GSU. The
   register dump gave it away: R0 held `$BEEF`, so the kernel had executed, but
   nothing reached memory. Fixed by polling SFR bit 15 (IRQ), which `STOP`
   latches and which cannot be raced.

2. **SCMR's documented bit assignment is wrong.** [SnesLab](https://sneslab.net/wiki/Super_FX)
   gives bit 4 = RAN, bit 5 = RON. bsnes and ares use **bit 3 = RAN, bit 4 =
   RON**. With `SCMR = $30` the GSU got ROM but not RAM, so code executed
   perfectly and the first `stw` to Game Pak RAM **hung the GSU forever** —
   waiting for RAM it did not own. `SCMR = $18` fixed it instantly. Bisected by
   cutting the kernel down to `stop`, which worked, proving the harness was fine
   and the store was not.

3. **The kernels contained absolute addresses and were linked somewhere else.**
   `iwt r13,#inner` bakes the LOOP target as an absolute address. asar
   assembled at `org $8000`; ld65 placed the blob wherever RODATA happened to
   land. Result: the GSU ran, the loop target was garbage, the kernel fell
   straight through to `STOP`, and the measurement came back as **0.96 master
   clocks per MAC** — 128 multiply-accumulates in 124 master clocks, which is
   impossible and was the only reason I caught it. Fixed with a dedicated
   linker config (`rom/lorom32fx.cfg`) that pins each kernel to the address
   asar assembled it for.

Number three is the entry-2 lesson repeating: a wrong number that *looks* like
a spectacular win is the most dangerous output an instrument can produce.

### The controlled A/B

Same instrument, same operand data, same console, same cartridge bus, one
variable changed. Both kernels are unrolled by two and software-pipelined —
the GSU stalls if a RAM load's destination register is used by the very next
instruction, and the first int8 kernel did exactly that, costing it 1 GSU clock
per MAC that had nothing to do with int8. Both kernels execute **the same 11
instructions per MAC** and both are verified against a host-computed sum.

```
kernel       wall      cpu  GSU clk       ns    kMAC/s  description
int8        28.70    27.86    14.35     1336     748.4  signed 8x8 via GSU MULT
nomul       26.68    25.90    13.34     1242     805.0  control: MULT -> ADD
tern        22.60    21.94    11.30     1052     950.3  ternary gather, no multiply
```

Correctness: `int8` = `$9A38`, `tern` = `$FCFB`, both matching the host. GSU
invocation overhead (start, `STOP` handshake, CPU-side driver) measured
separately at 323.5 wall master clocks and subtracted.

**Ternary wins again, but only by 1.27x**, against 2.02x on the stock 5A22.

The control kernel says why. Replacing `MULT` with `ADD` recovers only **1.01
GSU clocks** — the multiply is genuinely one cycle, exactly as documented. The
other 2.04 clocks of the gap are the **memory access pattern**: int8 reads two
bytes through two independently-advancing pointers in two different RAM pages,
while the ternary gather's first two reads are sequential from one pointer.
Same instruction count, different locality.

**Honest limit on this result:** 1.27x is narrow enough that I cannot rule out
a better-scheduled int8 GSU kernel closing or reversing it. I scheduled both as
well as I could and made them instruction-count-equal, but "as well as I could"
is not "optimal", and on the 5A22 the margin was wide enough (2.02x) that
scheduling could not have flipped it. Here it might. Treat the SuperFX arm as
*ternary is at least competitive and probably ahead*, not as a settled 1.27x.

### What the trend across arms actually says

| arm | ternary | best int8 | ternary advantage |
|---|---|---|---|
| 5A22 SlowROM 2.68 MHz | 109.2 | 220.5 (PPU M7) | 2.02x |
| 5A22 FastROM 3.58 MHz | 86.5 | 179.2 (PPU M7) | 2.07x |
| GSU-1 10.74 MHz | 22.6 | 28.7 (GSU MULT) | 1.27x |
| N64 RSP (sibling port) | — | — | int8 wins by 12% |

(wall master clocks per MAC; the GSU column is emulator-only)

The advantage shrinks monotonically as the arithmetic unit gets closer to the
operands. It is 2x when the multiplier is across a register bus, 1.27x when the
multiplier is in the same datapath as the pointers, and it inverts on the RSP
where one instruction moves eight lanes. **Ternary's win is not a fact about
multipliers. It is a fact about how many memory transactions an accumulate
costs**, and it survives exactly as long as the machine charges per operand.

Files: `rom/fx1.s` (bring-up), `rom/fx2.s` (benchmark), `rom/gsu/*.asm`,
`rom/lorom32fx.cfg`, `tools/analyze_fx.py`, raw dump `out/fx2.ram`,
report `out/fx2_report.txt`.

---

## 2026-08-10 — 6. The gather kernel A/B: the cheapest gather is no gather

Entry 3 settled *which arithmetic* wins. This entry settles *how to write it*,
and it answers the question the brief flagged as genuinely open: the NES port
found that 8-bit register residency was the whole driver of its inner-loop
cost, and the 65816 in native mode has a 16-bit accumulator. Does that finding
survive?

Same instrument, same discipline: `rom/kern.s`, seven kernels, each measured at
two window lengths and each summing the same 128 biased activations through the
same permutation so a kernel that is fast because it is broken is caught by its
sum. All seven produced the right sum. Worst two-length spread: **0.203%**.

Activations are stored **biased** (value + 7, so 0..14) exactly as the NES port
stores them. On the NES that is what keeps 16 of them inside a byte; here it is
what lets a run of `adc` need no `clc` between elements at all — 128 × 14 =
1,792 cannot carry out of 16 bits, so **the block-16 structure the NES needed
has no reason to exist on the 65816**. That is the first thing the wide
accumulator changes and it is a deletion, not an addition.

### SlowROM, 2.68 MHz — wall master clocks per multiply-accumulate

```
kernel          wall       cpu   derived      err     kMAC/s
code           32.98     32.01     32.00    0.03%      651.3
codesgn        33.09     32.12     32.11    0.02%      649.1
code8          36.26     35.19     33.97    3.60%      592.4
i8dp16         72.11     69.99     70.00   -0.01%      297.8
i8acc          74.28     72.10     71.12    1.37%      289.1
i16dp          86.52     83.98     84.00   -0.02%      248.2
i16abs         94.80     92.02     92.00    0.02%      226.6
ternary (e.3) 109.20    106.00                         196.7
```

### FastROM, 3.58 MHz

```
kernel          wall       cpu   derived      err     kMAC/s
code           28.84     27.99     28.00   -0.02%      744.7
codesgn        28.95     28.10     28.09    0.04%      741.8
code8          30.16     29.28     28.34    3.29%      712.1
i8dp16         59.76     58.01     58.00    0.01%      359.4
i8acc          60.13     58.37     57.62    1.29%      357.2
i16dp          72.09     69.98     70.00   -0.03%      297.9
i16abs         78.29     76.00     76.00   -0.01%      274.3
ternary (e.3)  86.50     84.00                         248.2
```

Five of the seven agree with hand-derived instruction timings to within
**0.04%**. The two that do not are the two that pay the 8-bit fold, and there
the *instrument* is right and the derivation was optimistic; the measured fold
is what the tables above use.

### What each row is

* **i16abs** — `ldx IDXW,y` / `adc ACT16,x`, 16-bit index registers, activations
  reached absolutely. This is entry 3's `ternary` primitive with the `clc`
  deleted by the bias trick: 109.20 → 94.80, so **the bias is worth 14.4 wall
  master clocks per MAC** and it is free.
* **i16dp** — the same with the activations in the **direct page**. `adc dp,x`
  is one operand byte shorter than `adc abs,x`, which is one 8-master-clock
  fetch: 94.80 → 86.52. Free again; the direct page just has to be pointed at
  the activation array.
* **i8dp16** — 8-bit index registers, 16-bit accumulator. Narrowing the *index*
  registers removes the 65816's unconditional extra internal cycle on
  absolute-indexed addressing *and* shortens the load: 86.52 → **72.11**, a
  20% cut. The stream byte carries the pre-doubled offset (2 × 127 = 254 still
  fits a byte), so an 8-bit X still reaches all 128 16-bit activations.
* **i8acc** — 8-bit index registers **and** an 8-bit accumulator: the NES shape,
  including the fold that a byte-wide accumulator must pay every 16 elements.
  74.28. **Worse than i8dp16.**
* **code / codesgn** — the weights are static, the SNES has a 24-bit address
  space and megabytes of ROM, so the gather index does not need to be *fetched*
  at all: it can live in the operand byte of the accumulate itself. A row is
  `lda #K` / a run of `adc <off` / `sec` / a run of `sbc <off`. `codesgn` is the
  shape a real row has — **one carry transition per row, not per MAC** —, and
  costs 0.11 wall master clocks per MAC more than the unsigned form.
* **code8** — the same, 8-bit accumulator, folding every 16. 36.26.

### Two results, and the second one is a refutation

**1. The cheapest gather is no gather. 33.09 against 72.11** — the best
data-driven kernel on this machine — **is 2.18x, and against entry 3's measured
`ternary` primitive it is 3.30x.** The whole of the difference is one load. A
data-driven gather spends `ldx IDXB2,y` (32 master clocks) fetching a number
that was known when the cartridge was mastered; the code-as-weights form spends
zero, because the number is already inside the instruction the CPU had to fetch
anyway. This is exactly entry 3's conclusion pushed one step further: the
binding constraint is memory transactions per accumulate, and the index fetch
is one of them, so delete it.

It is not free — it costs ROM. At 2 bytes per non-zero weight the trained
model's 52,764 non-zeros become 105,528 bytes of instruction stream against
52,764 bytes of index stream, so the technique buys 2.18x of speed with 2x of
ROM. On the NES, where the whole cartridge is a 32 KB window and PRG banks are
a scarce resource, that trade is not available. On a LoROM cartridge with 4 MB
of address space it is close to free.

**2. The NES's 8-bit residency finding does NOT survive — and it splits in
two.** In *both* kernel families the 16-bit accumulator wins:

```
                     SlowROM            FastROM
8-bit acc / 16-bit   36.26 / 33.09      30.16 / 28.95     code family   +9.6% / +4.2%
8-bit acc / 16-bit   74.28 / 72.11      60.13 / 59.76     data family   +3.0% / +0.6%
```

but the 8-bit **index** registers win by 20%:

```
i16dp / i8dp16       86.52 / 72.11      72.09 / 59.76                   -16.7% / -17.1%
```

So the NES finding was right about register width mattering and wrong about
which register. On the 65816 the index registers want to be **narrow**, because
`abs,y` with a 16-bit index takes an unconditional extra internal cycle and one
more data fetch; the accumulator wants to be **wide**, because a byte-wide
accumulator has to be folded into a 16-bit total every 16 elements and that
fold costs more than the byte-wide `adc` saves. The NES could not separate the
two questions — the 6502 has one width — and this port can, which is the whole
value of asking it here.

The margin is not uniform. At FastROM in the data-driven family the two
accumulator widths are within 0.6% of each other, i.e. a tie; the 8-bit
accumulator only loses clearly where instruction fetches are expensive relative
to the fold. Stated plainly: **the 8-bit-accumulator result does not transfer,
and at FastROM the honest statement is "no longer an advantage" rather than "a
disadvantage".**

### The design that falls out

The shipping engine uses `codesgn`: the weight program is straight-line 65816
code, one `adc <off` or `sbc <off` per non-zero weight, `lda #K` per row with
the bias correction `-7*(n_pos - n_neg)` folded into the immediate, and one
`jsl` per row into a handler that quantises and stores. There is no weight
stream, no header table, no bank-chain machinery and no block-16 accumulator —
four of the NES port's structures deleted outright by a wide accumulator and a
flat address space.

Files: `rom/kern.s`, `tools/genkern.py`, `tools/analyze_kern.py`, raw dumps
`out/kern.ram` / `out/kernfast.ram`, reports `out/kern_report.txt` /
`out/kernfast_report.txt`.
