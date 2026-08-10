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
