# elya-snes — FINDINGS

Journal. Appended to after every discrete result. Newest at the bottom.

The single question this repo answered first: **which arithmetic primitive is
cheapest per multiply-accumulate on a stock SNES, in exact cycles.** No engine
was written until that was measured (entries 1-5). Entry 6 then asked how to
*write* the winner, entry 7 is the cartridge that resulted, and entry 8 checks
the one model knob the brief left open.

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

---

## 2026-08-10 — 7. The cartridge. It generates text, and it matches the host

`out/nn.sfc` is a 256 KiB LoROM cartridge that runs the ternary transformer
ported from `~/elya-nes` — V=64, D=64, L=3, H=2, d_head=32, F=128, T=20, the
exact softmax normaliser, 102,400 ternary weights of which **52,764 are
non-zero** — and writes its output to battery SRAM. From seed token `b` it
produces

```
because and said, "you ca
```

which is character for character what `host/ref.py` produces from the same
weights. It is not a demo of a forward pass; it is the forward pass.

### ROM == host

```
one seed,  19 generated tokens                     20/20      identical
64 seeds x 19 generated tokens, SlowROM          1280/1280    identical
64 seeds x 19 generated tokens, FastROM          1280/1280    identical
residual stream + attention output, 3 positions   960/960     identical
```

The 64-seed survey is the gate, not the single-seed run. A greedy trajectory's
first tokens are dominated by the embedding and only start exercising the KV
cache, the softmax and the value path once there is context, so **a short
single-seed comparison can pass a genuinely broken build.** It did, on the
sibling ports, three times. `tools/check_survey.py` runs every vocabulary
symbol as a seed.

Even the survey is an *output* gate: it compares argmaxes, and an arithmetic
error that never moved a winner would survive it. So `-DDEBUG` dumps the
residual stream after every layer and the attention output of layer 0 as signed
bytes, and `tools/check_debug.py` compares all 320 of them against the
reference trace at positions 0, 9 and 18. All 960 agree.

### Cycles per token, measured

A token takes eight NTSC frames, so entry 2's sub-frame instrument cannot span
one — the V counter wraps. `-DPROFILE` adds a vblank counter kept by the NMI
and stitches the two together as `time = F*FRAME + ((V-225) mod 262)*1364 + H*4`,
which is monotonic across the wrap because the NMI fires at scanline 225 and
that is exactly where the second term restarts.

**FRAME is measured, not assumed.** A `dex`/`bne` loop is timed sub-frame at
four lengths, a line is fitted, and the same loop is run long enough to span
six frames; the frame length is whatever makes the two agree.

```
  cal( 1000) =     38304   fit     38318.0   resid  -0.037%
  cal( 2000) =     75420   fit     75394.0   resid  +0.034%
  cal( 3000) =    112460   fit    112470.0   resid  -0.009%
  cal( 4000) =    149544   fit    149546.0   resid  -0.001%
  loop = 1242.0 + 37.0760*k master clocks
  cal(60000) spans 6 frames; measured FRAME = 356815.7 master clocks
```

Two things fall out of that calibration and both are checks, not decoration.
The loop's slope is **37.076** wall master clocks against a hand-derived
**36** CPU master clocks — and 36 / 0.970674 = 37.087, so the DRAM-refresh
model from entry 2 is independently reconfirmed to **0.03%**. And the measured
frame is 0.155% *shorter* than 262 x 1364 = 357,368, which is the NMI handler
itself: the calibration runs with the same handler, so whatever it steals per
frame is inside the calibrated frame length and is therefore **subtracted from
every number below** rather than left as an error term.

```
                       SlowROM 2.68 MHz      FastROM 3.58 MHz
wall master clocks     3,055,173             2,678,280
cpu master clocks      2,965,579             2,599,738
CPU cycles/token         370,697               433,290
seconds/token             0.1423                0.1247
TOKENS PER SECOND          7.030                 8.019
first -> last token   2,689,060 -> 3,418,457   2,327,934 -> 3,026,303
growth over 19 positions   +27.1%                +30.0%
```

**FastROM buys 14.1%, not 33%.** Moving cartridge fetches from 8 master clocks
to 6 is a 33% cut on the cartridge bus, but the engine's operands live in WRAM
and **WRAM is 8 master clocks whatever MEMSEL says**. A `codesgn` accumulate is
`adc <dp`: two of its four bus cycles are the instruction fetch (which FastROM
speeds up) and two are the WRAM read (which it does not). 32 -> 28, and 28/32 =
0.875, which is where 14% comes from and 33% never was.

### Where the time goes

`-DSTAGEPROF` samples the clock at all 24 stage boundaries of a forward pass,
rewriting the same SRAM block each token, so what survives is the breakdown of
the last and dearest position:

```
stage                wall   share      stage                wall   share
embed+pos           22672   0.66%      L1 Wo             109060   3.16%
L0 Wq              103300   3.00%      L1 W1             198604   5.76%
L0 Wk              104132   3.02%      L1 W2             174860   5.07%
L0 Wv              103748   3.01%      L2 Wq             102964   2.99%
L0 attention       316984   9.19%      L2 Wk             103476   3.00%
L0 Wo              109936   3.19%      L2 Wv             101248   2.94%
L0 W1              198696   5.76%      L2 attention      319092   9.26%
L0 W2              179140   5.20%      L2 Wo             108120   3.14%
L1 Wq              101960   2.96%      L2 W1             198812   5.77%
L1 Wk              103112   2.99%      L2 W2             168920   4.90%
L1 Wv              103952   3.02%      head               96504   2.80%
L1 attention       318272   9.23%      TOTAL            3447561
```

```
  matmul       2374038   68.86%
  attention     954347   27.68%
  embed/head    119176    3.46%
```

Entry 6's `codesgn` figure predicts 52,764 x 33.09 = **1,745,951** wall master
clocks of pure accumulate. The matmul stages measure 2,374,038, so the **row
overhead is 628,087 master clocks over 1,408 rows = 446 per row**: the `lda
#K` that carries the bias correction, the `sec` between the two sign runs, one
`JSL`/`RTL` pair, and the handler that quantises and stores. At an average of
37.5 non-zeros per row that is 12 master clocks of overhead per accumulate, or
36% on top of the 33.09 the accumulate itself costs. It is the price of a row
being a *row*, and it is the largest single thing left to optimise.

### An independent check of the per-MAC number, from a second cartridge

The AV_SHIFT=1 arm (entry 8) is a different model with a different weight
count, built and verified the same way. Comparing the two whole cartridges:

```
                nnz       wall master clocks / token
AV_SHIFT 3   52,764              3,055,173
AV_SHIFT 1   54,998              3,129,559
marginal cost of one extra non-zero weight:  33.30 wall master clocks
entry 6's isolated `codesgn` measurement:    33.09
                                             +0.63%
```

Predicting the second cartridge's cost from the first plus 33.09 per extra
non-zero gives 3,129,096 against a measured 3,129,559 — **0.015%.** The
kernel A/B and the whole running engine agree.

### The design, and what the 16-bit accumulator deleted

The engine keeps three things from the NES port and deletes four.

**Kept.** The activation bias (+7, values 0..14). The `[layer][k|v][t]`-style
cache split, with the key cache transposed and the value cache not, because QK
walks dimensions for a fixed position and AV walks positions for a fixed
dimension. And the two attention kernels' *shape* exactly: unrolled chains with
the accumulate's operand **patched per dimension (QK) and per position (AV)**,
which is what keeps the accumulator resident in `A`. Attention operands are
dynamic and cannot be baked into a cartridge, so this is where the NES design
still wins.

**Deleted, all four by the 16-bit accumulator or the flat address space.**

* **Block 16.** 128 x 14 = 1,792 cannot carry out of a word, so a whole row
  accumulates with no `clc` and no fold anywhere. The NES needed a block
  because 16 x 14 = 224 was the largest thing that fitted a byte.
* **The weight stream.** The gather index lives in the operand byte of the
  accumulate (entry 6). There is no stream to walk.
* **The header table** — `n_pos`, `n_neg` and the bias correction were four
  bytes per row on the NES; here `-7*(n_pos - n_neg)` is folded into the row's
  own `lda #K` immediate and the counts are implicit in how many `adc`s the
  emitter wrote.
* **The bank-chain machinery.** A 24-bit address space and `JSL`/`RTL` replace
  it; the emitter drops a `JML` at a bank boundary and nothing else notices.

Two SNES-specific pieces are new. The exact softmax normaliser
`p = min(e*8/S, 7)` is **one flat table lookup** here — `S` is bounded by
`T*max(exp) = 1,280` and the exp table has 15 entries, so `(S<<4)|bucket`
indexes the whole division in a 20,498-byte ROM table. On the 6502 that needed
a row chosen from `(kk, S>>kk)`. And the AV chain's live length is set by
**writing one `RTS` byte over slot p+1's opcode** and restoring slot p's, two
byte writes a token, which is why this port has no chain-entry table.

### Three bugs, and what each one taught

**1. The whole engine runs with DBR = $7E, so an absolute access cannot reach a
hardware register.** The first `-DPROFILE` build's NMI handler read RDNMI with
`lda $4210`, which at DBR=$7E is a WRAM read: the vblank flag was never
cleared, the CPU re-entered the handler forever, and the ROM simply never
finished. Every register touch in the instrument is now `f:`-long.

**2. A profiling probe placed one instruction too early.** `-DSTAGEPROF`'s
sampler kept its cursor in the direct page and was called *before* the `TCD`
that restores the driver's direct page — so with D still pointing at the
activation array it read an activation as an SRAM offset and wrote the PPU's H
and V counters into the model's own activations. The tell was **tokens that
changed from run to run under a wholly deterministic engine.** The instrument's
scratch is now absolute, so where it is called cannot matter.

**3. The one that mattered, and the reason the gate runs everything.** The
64-seed survey made the seed token a parameter, and `generate` stopped seeding
`TOKP` itself. The survey path set it; **the single-seed path did not**, and
for three builds the cartridge started from whatever WRAM happened to hold. The
64-seed survey passed the whole time. It was caught by re-running the *other*
arm, and `gate.sh` now builds and checks all nine variants on every change for
exactly that reason.

Files: `rom/nn.s`, `tools/emit.py`, `rom/lorom256.cfg`, `build_nn.sh`,
`gate.sh`, checkers `tools/check_nn.py` / `check_survey.py` / `check_debug.py`,
instruments `tools/prof_nn.py` / `stage_nn.py`, reports `out/nn_profile.txt`,
`out/nnfast_profile.txt`, `out/nn_stages.txt`, `out/nn_survey.txt`,
`out/nn_internals.txt`.

---

## 2026-08-10 — 8. Re-laddering AV_SHIFT: it transfers, and it could not not have

`AV_SHIFT` is the shift that requantises the attention output back to a 4-bit
activation. The brief flagged it as an open question — the 6502's optimum
might not survive a machine with a 16-bit accumulator — so it was re-run here
rather than carried over, `train/av_ladder.sh`, five rungs, two seeds, 12,000
steps each, exactly the settings the NES port's ladder used.

```
== AV_SHIFT ladder, 12,000 steps, exact softmax normaliser, two seeds ==
             this tree (SNES)     elya-nes (6502)      delta
AV_SHIFT    seed 1   seed 2     mean       mean nats/char       mean
1           2.2889   2.3047   2.2968     2.2968   1.5799    -0.0000
2           2.2035   2.2066   2.2050     2.2050   1.5168    -0.0000
3           2.1882   2.1935   2.1909     2.1909   1.5071    +0.0000
4           2.2587   2.2534   2.2560     2.2561   1.5519    -0.0000
5           2.2662   2.2677   2.2670     2.2670   1.5594    +0.0000
```

**AV_SHIFT = 3 wins on both trees**, and the two ladders agree to twelve
decimal places at all ten rungs — the difference is exactly 0.00e+00 at every
one. (A correction to the premise: `AV_SHIFT = 2` was the 6502 optimum under
the **power-of-two** softmax normaliser. Under the **exact** normaliser this
port ships, the NES port re-laddered and moved to 3, and that is what
reproduces here.)

**Say plainly what that does and does not prove.** The trainer is deterministic
given its seed, so re-running it on a faithful copy of the tree reproduces the
numbers bit for bit. That confirms this repo's trainer and corpus are the same
ones — worth having — but a bit-identical reproduction is not an independent
measurement of the ladder, and it should not be presented as one.

### What is actually 65816-specific, and what it measures

The interesting claim is not "3 wins again". It is **that the accumulator width
cannot move the answer at all**, and there are two measurements behind it.

**The arithmetic is width-independent, and the console proves it.** `AV_SHIFT`
selects among values of an exact integer expression that `host/ref.py` defines.
The 6502 evaluated the attention accumulator in blocks of ten, which is
*provably* lossless — the AV product table is biased so ten entries cannot
exceed 250 and no carry can leave a byte — and the 65816 evaluates it flat in
sixteen bits. Different implementations, identical integers. That is checked
rather than argued: `-DDEBUG` compares all 64 attention outputs of layer 0 (and
the residual stream after every layer) against the reference trace at three
positions, 960 values, all identical. And a **second cartridge was built at
`AV_SHIFT = 1`** with the ladder's own `av1_exact_s1` weights and a host
reference configured to match; it also matches, 20/20 tokens. Two different
shifts, two different models, both bit-exact.

**AV_SHIFT is cost-neutral on the 65816.** Diffing the `$8000` code region of
the `AV_SHIFT = 1` and `AV_SHIFT = 3` cartridges:

```
$8194  AVOFF     $0E vs $38     7<<1 = 14   against  7<<3 = 56
$844E  LIMAV     $1E vs $78    15<<1 = 30   against 15<<3 = 120
$8453  LIMAV-1   $1D vs $77
+ 34 bytes of JSL target addresses, which differ because the two models have
  different weight counts and therefore different weight-program layouts
```

**Three immediate operands. No instruction added, none removed.** The
saturating requantise is a range test and a table lookup whose *size* depends
on the shift but whose *code* does not — the same reason it was free on the
6502.

### A by-product worth more than the ladder

Two whole cartridges, verified independently, differing in weight count:

```
                nnz       wall master clocks / token
AV_SHIFT 3   52,764              3,055,173
AV_SHIFT 1   54,998              3,129,559
marginal cost of one extra non-zero weight:   33.30 wall master clocks
entry 6's isolated `codesgn` kernel:          33.09          (+0.63%)
```

Predicting the second cartridge's cost from the first plus 33.09 per extra
non-zero weight gives 3,129,096 against a measured 3,129,559 — **0.015%.**

The kernel A/B measured one instruction pair in a tight loop; this measures the
same quantity as the *marginal* cost of a weight in a 3-million-clock forward
pass with attention, softmax, quantisation and 1,408 subroutine calls around
it. They agree to well under one percent, which is the strongest evidence in
this repo that entry 6's table describes the engine and not just the bench.

Files: `train/av_ladder.sh`, `train/av_table.py`, `runs/avladder/*`,
report `out/av_ladder.txt`, the `AV_SHIFT = 1` profile `out/nnav1_profile.txt`.

---

## 2026-08-11 — 9. The game. It plays, it stops, it talks, and the coins are the tokens

`out/game.sfc` is the same 256 KiB LoROM cartridge as entry 7 with a
presentation layer on top: an Elyan Labs logo, a platformer, a halt, and a
conversation. The engine underneath is untouched — the same `rom/nn.s`, the
same weights, the same 1280/1280 agreement with `host/ref.py`.

### The architecture, which is inverted on purpose

**The main thread is the transformer and nothing else.** It runs forward passes
back to back, for ever. Every frame the vblank NMI stops it, does the entire
game — OAM DMA, scroll, text, input, physics, the state machine — and hands it
back. So the game loop is the interrupt and the idle task is inference, which
is the opposite of how a SNES game is normally written.

That is the only arrangement in which presentation cannot slow the model down
by more than the fixed cost of one vblank, and it is what lets rule 1 be
structural rather than a promise: `gcommit`, the only place a generated token
is committed, stores the token, increments two counters and pushes one byte
into a ring buffer. It touches no PPU register, formats no text and writes no
VRAM. The NMI drains the ring and does all of that. (The Genesis port's
`drawTokSpeed()` runs a `sprintf` and a VRAM write *inside* its measured
interval; this does not.)

The NMI never touches engine state either. It raises a request byte and the
main thread picks it up between forward passes, where nothing is half-written.
That is the whole of the concurrency design and it needs no locks.

### The binding rule, and how it is proved rather than asserted

> A coin may only be spawned from the code path that commits a generated token.

`gcommit` is the only writer of `COINQ`. `spawn_coins`, in vblank, is the only
reader and the only code in the cartridge that ever fills a coin slot. Two
sites, and `grep COINQ rom/game.inc` shows both.

Three separate checks, because "read the code" is not evidence:

```
final counters      118 spawned + 0 queued == 118 committed
every trace sample  104/104 samples satisfy spawned + queued == committed
coins ahead of tokens                                   0 samples
```

The invariant is checked at **every** sample and not only at the end, because
holding only at the end would also be satisfied by a coin source that ran fast
and then waited.

And the negative control, which is the part that makes it a claim about the
machine rather than about the source:

```
-DNOGEN   the forward pass and the commit removed, nothing else changed
          1,707 frames, 0 tokens, 0 coins
```

If inference stalls, the coins stall.

### What it costs, measured

Entry 7's instrument, unchanged, re-run with the whole game running. The frame
length is calibrated *before* the game's NMI is installed — `GNMION` keeps the
handler down to a bare frame counter until `gsetup` says otherwise — so every
master clock the presentation costs lands **inside** the measured per-token
intervals instead of being calibrated away.

```
                       SlowROM 2.68 MHz          FastROM 3.58 MHz
                   engine    +game    delta   engine    +game    delta
wall clocks/token  3,055,173 3,805,702 +24.6% 2,678,280 3,344,006 +24.9%
seconds/token         0.1423  0.1772           0.1247    0.1557
TOKENS PER SECOND      7.030   5.644  -19.7%    8.019    6.423  -19.9%
```

**The game costs 19.7% of the model's speed on SlowROM and 19.9% on FastROM.**
Say it plainly: presentation is not free here, and a fifth of the arithmetic
went to making it a game.

The two arms agree on something more useful than the percentage. Converting the
extra cost to *per frame*:

```
SlowROM   +750,529 clocks/token over 10.65 frames/token  =  70,472 / frame
FastROM   +665,726 clocks/token over  9.36 frames/token  =  71,124 / frame
                                                    0.9% apart
```

The presentation costs the same number of master clocks per **frame** in both
clock configurations, to within 0.9%. That is the same structural fact entry 7
found for the engine, from the other side: FastROM speeds up cartridge fetches
and the game layer is dominated by DMA and WRAM traffic, neither of which
MEMSEL touches. A frame of game is a frame of game whatever the CPU clock is.

### Where the 70,000 clocks go

The NMI window is short enough for entry 2's sub-frame instrument — the H/V
counters are latched at handler entry and exit — and the V-counter wrap is
handled exactly as `tools/prof_nn.py` handles it, because the NMI fires at
scanline 225 and that is where the modular arithmetic restarts.

```
NMI handler, 170 frames of act 1     master clocks     % of a 356,816 frame
  mean                                     60,688              17.01%
  min                                      49,820              13.96%
  max                                     102,264              28.66%
  frames with no coin spawned              59,663
  frames where a coin spawned              67,885   (+8,222)
```

So of the 70,472 clocks a frame the presentation costs, **60,688 are inside the
handler and 9,784 (2.74% of a frame) are outside it** — HDMA, which runs during
active display, and whatever else the DMA controller steals while the CPU is
running the model. The next section takes that 9,784 apart.

### What the design document claimed about the sky, and what is true

`docs/SPRITE_DESIGN.md` says the HDMA gradient costs "zero CPU cycles in the
inference loop". It does not. An arm built with `-DNOSKY` — the same cartridge
with `sky_hdma` never called, one HDMA channel the only difference — isolates
it exactly:

```
                        wall clocks/token   tokens/s
game, sky HDMA on            3,805,702        5.644
game, sky HDMA off           3,717,168        5.778
the sky                         88,534        -2.32%
                        = 8,313 master clocks a frame = 2.33% of a frame
```

**The HDMA sky costs 2.33% of the model's throughput.** Small, real, not zero.

That closes the accounting on the 70,472 clocks a frame the presentation costs:

```
  inside the NMI handler        60,688     17.01%   (measured directly)
  the sky's HDMA channel         8,313      2.33%   (measured by -DNOSKY)
  everything else                1,471      0.41%
  ------------------------------------------------
  total presentation            70,472     19.75%
```

The commonly quoted HDMA model — about 18 master cycles per active channel per
scanline plus 8 per byte transferred — predicts 224 x 18 + 28 x 32 = 4,928 for
this table, and the measurement is 8,313. The measurement is what this repo
quotes; the model is noted because the gap is 1.7x and somebody should find out
which of the two is wrong, on hardware.

The honest version of the design's claim is the one entry 3 already made about
screen-on versus forced-blank: the *renderer* costs the CPU nothing (220.51
against 220.51 wall master clocks per MAC with the screen genuinely on), and
the DMA controller costs it something small. 28 bands of 8 scanlines rather
than 224 separate writes is an eighth of that something.

### Verifying a game on a machine that cannot screenshot

Entry 1 recorded that screen capture is unavailable here — the ares window is
Wayland-native, `import -window root` gets nothing and `grim` fails because
GNOME does not implement `wlr-screencopy`. A platformer verified only by
counters is a platformer nobody has seen.

So the ROM **reads its own OAM, VRAM and CGRAM back out through the PPU** into
battery SRAM, and `tools/render_frame.py` composites them on the host into
exactly what the television would show. That is better than a screenshot, not
a substitute for one: a screenshot is a picture you have to trust the
emulator's renderer for, this is the object table, the tilemaps, the palettes
and the scroll registers the picture would be made from, composited in code
that can be read.

What that buys, from one run:

```
BG3 tilemap read back through $2139 == the WRAM shadow      1024/1024 entries
OAM read back through $2138        == the shadow the DMA sent  544/544 bytes
CGRAM   1.. 11 == bg3.pal        CGRAM  32.. 47 == bg1.pal
CGRAM  48.. 63 == bg2.pal        CGRAM 128..143 == obj.pal
CGRAM entry 0  == the sky HDMA's last band
```

and two rendered frames, one mid-platformer and one mid-conversation, which is
how the two worst bugs in this entry were found.

### Three PPU read ports, three different lessons

**The VRAM read port lags one word.** `read k` returns `VRAM[addr + k - 1]`.
The first attempted fix set the address one word early *and* added a dummy
read pair; those cancel exactly, so the diff did not move and it looked like
the dummy doing nothing. An A/B — both setups dumped in the same run — settled
it in one go. Reasoning about the prefetch had produced three mutually
contradictory models by then.

**The CGRAM readback came back as the palettes interleaved with each other**,
and that was not the read port at all. **HDMA keeps running in forced blank.**
The sky channel rewrites `CGADD` every eight scanlines, and a 512-entry CGRAM
read spans about a third of a frame, so the read address was being reset out
from under the loop roughly every thirty entries. It read exactly like a broken
port. With `stz HDMAEN` at the top of the dump the readback is exact and needs
no dummy pair at all — the palettes had been right the whole time.

**The OAM read port needed nothing.** 544 of 544 bytes first time.

### Six bugs, and what each one looked like before it was understood

1. **Every coin slot came up busy.** WRAM is not cleared at reset, so a nonzero
   timer read as "live" and the spawner never found a free slot. Presented as:
   `COINSP` stayed 0 for an entire run while the queue grew to 93. The counters
   were self-consistent the whole time — the invariant held, because zero coins
   is a valid state — which is why the *renderer* was needed to notice.

2. **She walked at two thirds speed and stopped one pixel past her mark.** The
   horizontal collision test ran against a Y that gravity had already pushed
   one pixel into the floor, so roughly a third of frames reverted the
   horizontal move. Resolving Y first fixes it: the X test then always runs
   against a position known not to be overlapping.

3. **She then could not jump at all.** Resting on a floor is a two-frame cycle
   — gravity pushes her a pixel in, the next frame snaps her back — so "on the
   ground" inferred from "did this frame end in a landing" is true on alternate
   frames *in a fixed phase*. The scripted jump landed on the wrong phase every
   single time, deterministically, which is why it looked like the jump code
   never running rather than like a 50/50. `ONGND` now comes from a ground
   probe and has exactly one writer.

4. **The verification dump appeared to hang inside the VRAM read loop.** It
   reads about 3,400 registers back through the PPU, which takes three frames,
   so the vblank NMI re-entered itself, reached `st_end` and started the dump
   again, and again, until the stack ran out. The readback loop was correct the
   entire time. There is now a re-entrancy guard.

5. **The HUD and the transcript shared one text cursor.** Every token printed
   after a HUD update landed next to the coin counter instead of in the
   dialogue box. On screen: `@ x 118ae` and an answer three characters long.
   No counter could have caught this; the rendered frame caught it instantly.

6. **The dialogue box covered the character doing the revealing.** The box is
   the bottom eight rows and she stands in the bottom eight rows. The camera
   now tilts up as she comes to a halt, which reads as a deliberate move and
   was not.

A seventh, quieter one: the first generated token of every answer is the output
of the *last* prompt step, and it was being committed while the mode flag still
said "feeding" — so the SRAM recorder, which keys off that flag, dropped it and
every recorded answer was one token short. Caught by the host-side comparison
against `host/ref.py`, which expects exactly `20 - len(prompt)` tokens.

### The three acts, and what is generated

**Act 0** is the Elyan Labs logo on BG1, 109 tiles from entry's `png2snes.py`.

**Act 1** is the platformer. Elya runs, jumps and strikes a block stamped `@` —
the matrix-multiply operator, because `A @ B` is what makes the tokens the
block gives out, and deliberately not a `?`. A nabla chases her: `∇` is the
gradient operator, and the ROM does inference and contains no gradients at all,
so it is the thing from training that cannot touch her any more, still chasing.
The sky is an HDMA gradient and the clouds are a scrolling BG layer.

**Act 2** is the stop. She walks to a mark, the nabla loses interest and wanders
off, the camera tilts up and a dialogue box opens. Her line is generated from
**the last token act 1 produced** — no stored prompt at all — so it depends on
how the platformer went, and a player who takes longer gets a different line.
On the run recorded here the seed was `s` and she said

```
 a big big because and
```

which is 19 tokens, checked against `host/ref.py`, and is exactly the kind of
wrong the design document said to expect. A lookup table cannot make that
mistake.

**Act 3** is the conversation. Six questions, chosen short because the trained
positional table caps context at 20 tokens and a ten-token question leaves ten
for the answer. The question is stored — a prompt is input, not output — and is
drawn in **amber**; what the console generated is drawn in **white**. The screen
itself shows which characters came out of the model, with no caption.

```
act 3  q1  'what now? '   ->  'he said, "yes, i'      12/12 == host/ref.py
act 3  q2  'once upon '   ->  'her friends. she sa'   13/13 == host/ref.py
```

Coins keep popping out of the block behind the dialogue box the whole time she
is talking, because the binding rule does not have an exception for act 3.

### Cartridge compatibility, from the image bytes

```
image        out/game.sfc
size         262144 bytes = 2 Mbit
map mode     $20  LoROM / SlowROM        (out/gamefast.sfc: $30, FastROM)
cart type    $02  ROM + RAM + battery
rom size     $08  256 KiB declared, 256 KiB actual
sram size    $05  32 KiB
country      $01  USA (NTSC)
PASS: LoROM, NTSC, 2 Mbit of a 56 Mbit cartridge, battery SRAM declared,
      no enhancement chip.
```

The SRAM grew from 8 KiB to 32 KiB, because the PPU readback dumps need the
room. It is still an ordinary battery-backed LoROM save, and `tools/
kaico_check.py` reads every one of those fields out of the image rather than
out of the assembler source.

### What is not done

**There is no audio.** The design document's act 2 beat is "music drops out",
and there is no music to drop. Uploading an SPC700 program through the APU IPL
handshake is a day's work on its own and none of it would be verifiable on this
host, which has no way to hear the result; a driver that cannot be checked is
not something this repo ships. Stated plainly rather than quietly omitted.

Files: `rom/game.inc`, `tools/mkart.py`, `tools/mkbg.py`, `tools/mkfont.py`,
`tools/mkgame.py`, `tools/check_game.py`, `tools/render_frame.py`,
`rom/lorom256.cfg`, reports `out/game_check.txt`, `out/game_profile.txt`,
`out/gamefast_profile.txt`, frames `out/frames/frame_act1.png` and
`out/frames/frame_act3.png`.

### A footnote on the harness, because it wasted an hour

`tools/run_ares.sh` waits for the ROM's own `DONE` marker to appear in the
autosaved `.ram`. A short game run — 700 frames rather than 1,600 — reliably
produced a `.ram` file whose last trace record was within *four frames* of the
dump, twice, with the dump missing. That is not a timer: ares appears to flush
the save when the cartridge's writes go quiet, so a ROM that finishes and then
stops writing can be snapshotted a few frames before it finished and never
again. Lengthening the run past the flush point fixes it, and the fix is in the
gate as `-DGFRAMES=1600`.

The reason it mattered: it looked exactly like `dump_all` crashing, which is
what a genuine NMI re-entrancy bug had been doing an hour earlier in the same
routine. The same symptom, two entirely different causes, and only the stage
markers written to `SRAM+$0C` told them apart.

### The gate said pass while a checker said fail

Found by the game's own arm and worth its own paragraph, because it is about
the thing everything else in this file is trusted through.

`gate.sh` runs every checker as `python3 tools/check_x.py ... | tail -n 3 ||
FAIL=1`. A pipeline's exit status is the *last* command's, so that is `tail`'s,
which is always 0. **`FAIL=1` could never be reached from a failing checker.**
The gate printed the checker's own `FAIL: ...` line and then `GATE: pass`
underneath it, and exited 0.

```sh
set -e
FAIL=0
false | tail -n 1 || FAIL=1   # FAIL=0
set -o pipefail
false | tail -n 1 || FAIL=1   # FAIL=1
```

This had been true of every arm since the gate was written in entry 7, so the
gate's **exit code** has never been trustworthy on this tree. Its **output**
always was — every arm prints its own `PASS:` or `FAIL:` line and those were
read — which is why nothing bad shipped through it, but "I read the log" is not
what a gate is for. `set -o pipefail` is now the second line of the file.

The lesson is the entry-2 lesson again in a different costume: an instrument
that cannot report failure looks exactly like an instrument reporting success.

### One palette entry, once, unexplained

One run of the real-controller arm read CGRAM entry 141 back as `$2DAD` where
`assets/obj.pal` says `$18DC`. Every other entry in that run was exact, and
eight other runs of the same build were exact.

It did not reproduce. The dump now reads CGRAM **twice, back to back**, into
two SRAM buffers, so a recurrence says whether CGRAM is wrong or the read of it
is; three runs since have agreed on all 256 entries both times. The check is
deliberately left strict rather than widened, and the entry index is printed.

Candidate explanations that were considered and do not fit: a torn SRAM
autosave would have left `$FF` in one of the two bytes and did not; an HDMA
transfer surviving `stz HDMAEN` would have written CGRAM entry 0, not 141; and
a CGADD reset inside the read loop would have shifted every entry after it,
not one. **No mechanism found.** Recorded rather than rounded off.
