# elya-snes

A transformer language model that runs on a stock Super Nintendo — no
enhancement chip, no SuperFX, no DSP — and says something.

```
seed token 'b'   ->  because and said, "you ca
seed token 'd'   ->  day, lily said, "that is a
seed token 'q'   ->  quickly. she said, "thank
seed token 'w'   ->  with her mommy and said, "than
```

**102,400 ternary weights**, 52,764 of them non-zero, 4-bit activations, a
64-symbol vocabulary, three layers, two heads, 20 positions of context, trained
on TinyStories with quantisation-aware training so the forward pass the trainer
saw is the forward pass the 65816 executes. **7.03 tokens per second** on a
2.68 MHz Ricoh 5A22, 8.02 on FastROM. Verified against an exact-integer host
reference over **1,280 tokens — every vocabulary symbol as a seed — on both
clock arms**, plus the residual stream and the attention output element by
element.

Every number in [FINDINGS.md](FINDINGS.md) is measured on the console. Nothing
is estimated.

```sh
make nn      # build the cartridge (out/nn.sfc, out/nnfast.sfc)
make gate    # build every variant, run under ares, check every token
make kernels # the gather-kernel A/B
make measure # the arithmetic-primitive tables
```

---

## Two results

### 1. int8 does not beat ternary on the SNES. The prediction is refuted.

This port opened with a prediction: the SNES is the first target in this family
with a fast **signed** hardware multiply, so it should be where int8 finally
wins. Primitives were measured before any engine was written.

Wall master clocks per multiply-accumulate (21.477 MHz master clock):

| primitive | SlowROM | FastROM | vs ternary |
|---|---:|---:|---:|
| software 8x8 shift-add | 1503.9 | 1327.2 | 13.8x |
| quarter-square tables | 570.9 | 467.6 | 5.2x |
| DSP-1, bus transfer only¹ | 323.5–476.1 | 267.9–391.7 | 3.0–4.4x |
| PPU Mode 7, textbook form | 333.8 | 274.1 | 3.1x |
| CPU multiply `$4202`/`$4203` | 317.3 | 261.8 | 2.9x |
| **PPU Mode 7, tuned** | **220.5** | **179.2** | **2.0x** |
| CPU multiply, operands pre-packed² | 164.8 | 136.0 | 1.5x |
| **ternary sign-separated gather** | **109.2** | **86.5** | **1.00x** |

¹ lower bound: the DSP-1 firmware is unavailable, so no DSP execution time and
no RQM polling is included. ² lower bound: needs two runtime arrays interleaved
into one, which costs more than the MAC it saves.

**The multiply was never the variable.** The Mode 7 unit produces a signed
16x8 product in zero cycles; all 214 cycles of that arm are data movement.

> Ternary touches **three** memory locations per accumulate. int8 touches
> **six**.

That one mechanism replaces the per-platform story these ports had been
telling. Ternary's advantage shrinks as a machine gets better at moving
operands, and inverts on one that fetches eight at a time:

| arm | ternary advantage |
|---|---|
| SNES 5A22 SlowROM | 2.02x |
| SNES 5A22 FastROM | 2.07x |
| SNES SuperFX GSU (emulator only) | 1.27x |
| N64 RSP vector unit (sibling port) | inverts — int8 wins |

Also measured, because it is usually asserted: the Mode 7 multiplier's
"vblank only" reputation costs **nothing** here. With the screen genuinely on
in BG mode 1 during active display, the same MAC measures 220.5 against 220.5
in forced blank.

### 2. The cheapest gather is no gather — and the NES's 8-bit finding splits in two

Seven gather kernels, same instrument, same operands, each separately proved to
compute the right sum. Wall master clocks per MAC:

| kernel | SlowROM | FastROM | what it is |
|---|---:|---:|---|
| **code** | **32.98** | **28.84** | the index in the accumulate's operand byte |
| codesgn | 33.09 | 28.95 | the same, sign separated — what ships |
| code8 | 36.26 | 30.16 | the same, 8-bit accumulator + fold |
| i8dp16 | 72.11 | 59.76 | 8-bit index regs, 16-bit accumulator |
| i8acc | 74.28 | 60.13 | 8-bit index regs, 8-bit accumulator |
| i16dp | 86.52 | 72.09 | 16-bit index regs, direct-page activations |
| i16abs | 94.80 | 78.29 | 16-bit index regs, absolute activations |

The weights are static and the SNES has a 24-bit address space, so the gather
index does not need to be *fetched*: it can live in the operand byte of the
accumulate itself. A row becomes `lda #K`, a run of `adc <off`, `sec`, a run of
`sbc <off`. **2.18x cheaper than the best data-driven gather, 3.30x cheaper
than the primitive in the table above**, for 2 bytes of ROM per non-zero
weight. On the NES, where the whole cartridge is a 32 KB window, that trade is
not available.

The NES port found that 8-bit register residency was the whole driver of its
inner-loop cost. On the 65816 that **splits**: the index registers want to be
narrow (`abs,y` with a 16-bit index costs an unconditional extra internal cycle
and one more fetch — 20% cut) and the accumulator wants to be **wide** (a
byte-wide accumulator must be folded into a 16-bit total every 16 elements, and
the fold costs more than the byte-wide `adc` saves). The 6502 has one width and
could not separate the two questions.

---

## The cartridge

| | |
|---|---|
| image | 256 KiB LoROM, NTSC, battery SRAM, no coprocessor |
| weight program | 4 banks of straight-line 65816, 52,764 accumulates |
| ROM == host | 1,280/1,280 tokens over 64 seeds, both clock arms |
| internals | 960/960 residual-stream and attention values, 3 positions |
| cycles/token | 3,055,173 wall master (SlowROM) · 2,678,280 (FastROM) |
| tokens/s | **7.030** (SlowROM) · **8.019** (FastROM) |
| where the time goes | matmul 68.9% · attention 27.7% · embed+head 3.5% |

**FastROM buys 14%, not 33%**, because the engine's operands live in WRAM and
WRAM is 8 master clocks whatever `MEMSEL` says.

The cartridge writes its tokens to battery SRAM, which is how results leave the
console: ares autosaves it to `<rom>.ram` and the host reads the file. There is
no screen output — screen capture is impossible on this development host, so a
display could not be verified, and nothing unverified ships.

## Targets

* **stock 5A22** — the primary target, and what everything above measures. Runs
  on a Kaico Super DSP V3.1 flashcart: LoROM, NTSC, 2 Mbit of a 56 Mbit
  cartridge, battery SRAM declared, **no enhancement chip**. Checked from the
  image's own bytes by `tools/kaico_check.py`.
* **DSP-1** — measured and rejected. Its most optimistic floor, 314 CPU master
  clocks per MAC of pure bus transfer with no DSP execution time at all, is
  already 1.5x worse than the PPU multiplier and 3x worse than ternary.
* **SuperFX** — **emulator only**; the Kaico cart has no GSU. Measured as a
  controlled A/B anyway, because it is the cleanest architecture comparison
  available: same console, same bus, one variable changed.

Sibling ports: [elya-nes](https://github.com/Scottcjn/elya-nes) ·
[legend-of-elya-genesis](https://github.com/Scottcjn/legend-of-elya-genesis) ·
[legend-of-elya-n64](https://github.com/Scottcjn/legend-of-elya-n64) ·
[gbc-transformer](https://github.com/Scottcjn/gbc-transformer)

## How it is measured

ares has no scripting, so the counter is on the console: latch the PPU H/V
counters (`$2137` → `$213C`/`$213D`), run the work, latch again. Resolution 4
master clocks. A token spans eight frames, so `-DPROFILE` adds a vblank counter
kept by the NMI and stitches the two together — and **measures** the frame
length rather than assuming 262 x 1364, which incidentally reconfirms the
DRAM-refresh model to 0.03%.

The instrument was calibrated against hand-derived 65816 timings before any
number was quoted (five bodies, all within **0.06%**), and it lied twice before
it worked. Both lies and both fixes are in [FINDINGS.md](FINDINGS.md), along
with the three bugs the engine shipped through — including the one where a
64-seed survey passed while the single-seed build was seeding itself from
uninitialised WRAM.
