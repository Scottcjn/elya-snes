# elya-snes

A transformer language model on the Super Nintendo / Super Famicom.

**Status: measurement, not engine.** Nothing is built until the arithmetic is
priced. The full journal is [FINDINGS.md](FINDINGS.md).

## The question this repo answered first

Which arithmetic primitive is cheapest per multiply-accumulate on a stock SNES,
in exact cycles? The prediction under test was that the SNES, being the first
target in this family with a fast **signed** hardware multiply, would be where
int8 finally beats ternary weights.

**It does not. The prediction is refuted.**

Wall-clock master clocks per MAC (21.477 MHz master clock), SlowROM / FastROM:

| primitive | 2.68 MHz | 3.58 MHz | vs ternary |
|---|---|---|---|
| software 8x8 shift-add | 1503.9 | 1327.4 | 13.8x |
| quarter-square tables | 570.9 | 468.0 | 5.2x |
| DSP-1, bus transfer only¹ | 323.5 – 476.1 | 267.9 – 391.7 | 3.0 – 4.4x |
| PPU Mode 7 `$211B`/`$211C`, textbook form | 333.8 | 274.1 | 3.1x |
| CPU multiply `$4202`/`$4203` | 317.3 | 261.8 | 2.9x |
| **PPU Mode 7, tuned** | **220.5** | **179.3** | **2.0x** |
| CPU multiply, operands pre-packed² | 164.8 | 136.0 | 1.5x |
| **ternary sign-separated gather** | **109.2** | **86.5** | **1.00x** |

¹ lower bound; the DSP-1 firmware is unavailable so no DSP execution time or
RQM polling is included. ² lower bound; needs two runtime arrays interleaved
into one, which costs more than the MAC it saves.

The multiply is free — the operands are not. The entire 214-cycle cost of a
Mode 7 MAC is data movement. Ternary wins because it touches three memory
locations per accumulate where int8 touches six.

Also measured: the Mode 7 unit's vblank-only reputation costs **nothing** for
this workload. With the screen genuinely on in BG mode 1 during active display,
the same MAC measures 220.5 against 220.5 in forced blank.

## How it is measured

ares has no scripting, so the counter is on the console: latch the PPU H/V
counters (`$2137` → `$213C`/`$213D`), run the work, latch again. Resolution 4
master clocks. Results leave the console through battery SRAM, which ares
autosaves to `<rom>.ram`.

The instrument is calibrated against hand-derived 65816 timings before any
number is quoted — five bodies, all within **0.06%** — and every primitive is
separately proved to compute the right answer over the same operands.

```sh
make            # build every ROM
make measure    # run under ares, print both cycles-per-MAC tables
make fx         # the SuperFX arm (EMULATOR ONLY)
```

## Targets

* **non-FX** — the primary target, runs on a Kaico Super DSP V3.1 flashcart.
  Ricoh 5A22 (65816) with the arithmetic offloaded to the PPU's Mode 7
  multiplier, and the DSP-1 measured and rejected.
* **SuperFX** — **emulator only.** The Kaico cart cannot run SuperFX. Measured
  as a controlled A/B anyway, because it is the cleanest architecture
  comparison available: same console, same bus, one variable changed. Ternary
  still leads there, but only by 1.27x.

Sibling ports: [elya-nes](https://github.com/Scottcjn/elya-nes) ·
[legend-of-elya-genesis](https://github.com/Scottcjn/legend-of-elya-genesis) ·
[legend-of-elya-n64](https://github.com/Scottcjn/legend-of-elya-n64) ·
[gbc-transformer](https://github.com/Scottcjn/gbc-transformer)

---

## First result: the prediction was refuted

This port opened with a prediction — that the SNES's fast **signed** hardware
multiply would let int8 beat ternary, the way it does on the N64's RSP. Primitives
were measured before any engine was written.

**It does not.** Ternary is **2.02x cheaper** per multiply-accumulate.

| primitive | cycles/MAC (SlowROM) | vs ternary |
|---|---|---|
| software 8x8 shift-add | 1503.9 | 13.8x |
| quarter-square tables | 570.9 | 5.2x |
| DSP-1 (bus transfer alone) | 323.5-476.1 | 3.0-4.4x |
| CPU multiply `$4202` | 317.3 | 2.9x |
| PPU Mode 7, tuned | 220.5 | 2.0x |
| **ternary gather** | **109.2** | **1.00x** |

The multiplier was never the variable. The Mode 7 multiply is genuinely free —
every one of the 214 cycles in that arm is data movement.

> **Ternary touches three memory locations per accumulate. Int8 touches six.**

That single mechanism replaces the per-platform story these ports have been
telling. Ternary's advantage decays as a machine gets better at moving operands,
and inverts on one that fetches eight at a time:

| arm | ternary advantage |
|---|---|
| SNES 5A22 SlowROM | 2.02x |
| SNES 5A22 FastROM | 2.07x |
| SNES SuperFX GSU | 1.27x |
| N64 RSP vector | inverts — int8 wins |

Full method, calibration and the two times the instrument lied are in
[FINDINGS.md](FINDINGS.md).
