# elya-snes

A transformer language model on the Super Nintendo / Super Famicom.

Two targets:

* **non-FX** — stock SNES. Ricoh 5A22 (65816) at 2.68/3.58 MHz, with the
  arithmetic offloaded to the PPU's Mode 7 matrix multiplier.
* **FX** — SuperFX GSU coprocessor in-cart.

Status: bring-up. Nothing measured yet.

## Why the PPU multiplier matters

| multiplier | operands | signed | latency |
|---|---|---|---|
| CPU `$4202/$4203` | 8x8 -> 16 | unsigned | 8 cycles |
| **PPU `$211B/$211C` -> `$2134-36`** | **16x8 -> 24** | **signed** | **immediate** |

Sibling ports found a different deciding constraint on every platform: no
multiply on the Game Boy's SM83, cheap vector multiply-accumulates on the N64's
RSP, register pressure on the NES's 6502. The SNES is the first target where a
**signed** hardware multiply is effectively free, which is the condition under
which int8 should beat ternary. That is a prediction, and it is the first thing
this port measures.

Sibling ports: [elya-nes](https://github.com/Scottcjn/elya-nes) ·
[legend-of-elya-genesis](https://github.com/Scottcjn/legend-of-elya-genesis) ·
[legend-of-elya-n64](https://github.com/Scottcjn/legend-of-elya-n64) ·
[gbc-transformer](https://github.com/Scottcjn/gbc-transformer)
