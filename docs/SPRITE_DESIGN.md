# Visual design: the @ block

## The idea

Elya jumps, strikes a block stamped `@`, and a coin pops out. One coin per
generated token. The coin counter is the token counter.

`@` is the matrix-multiplication operator in Python and numpy — `A @ B`. The
block Elya hits to produce tokens is stamped with the operator that produces
them. It is also, deliberately, not a `?`: this is an original design in a
familiar idiom, not a reskin of someone else's.

## Why a coin per token is worth the sprites

The Genesis port animates Elya's mouth per token, and it works because the
animation is *driven by the token stream* — it cannot run ahead of the
arithmetic. A coin does the same job with a stronger read:

> **The coin count is a visible, countable proof of inference.**

A viewer can pause the video and check coins against generated characters
without trusting any number the ROM prints. That matters on this project
specifically, where the most common objection is "it must be a lookup table."

**Binding rule, non-negotiable: a coin may only be spawned from the same code
path that commits a token.** Not a timer, not a frame counter. If the engine
stalls, the coins stall. Same discipline as the mouth animation on the Genesis.

## Timing constraints — from measurements, not taste

Measured on the Genesis: the game loop runs one forward pass per iteration and
syncs to vblank, so token cadence is quantised to 60/k. Observed k = 30 to 34,
i.e. **one token every ~0.5 s**.

The SNES engine's cadence is not measured yet. But if it lands anywhere near
that, a coin bounce lasting ~20 frames is *slower than nothing* but close enough
that the design must be:

* **one coin spawns per token; animations overlap.** Never "block waits for the
  bounce to finish" — that would make the animation the bottleneck.
* the block bump (~4 frames) and the coin arc (~24 frames) run independently
* if tokens ever outrun the animation, coins stack rather than drop

## Sprite budget

The SNES has hardware sprites with OAM DMA, so this is cheap in a way the
Genesis text counter was not — `drawTokSpeed()` there runs a `sprintf` and a
VRAM write **inside the measured interval**. Here the animation should sit
almost entirely outside the inference path: build the OAM table during vblank,
DMA it, and let the inference loop only ever *set a flag*.

| object | size | frames | notes |
|---|---|---|---|
| Elya | 16x24 | idle 2, jump 1, land 1 | small sprite, personality in the idle bob |
| `@` block | 16x16 | rest 1, struck 1 (squash) | `@` glyph in the tile, not printed text |
| coin | 8x8 | 4 (spin) | classic 4-frame rotation reads at any size |
| coin counter | text | — | `@ x 000` in the status bar |

Palette: 4bpp is plenty. Keep the coin on its own palette row so the spin can
cycle colours without touching Elya's.

## What it must NOT do

* No `?` glyph, no Nintendo coin sprite, no Nintendo sound. Original art in the
  same idiom.
* No animation on a timer. Token-driven or it does not ship.
* No work inside the measured inference window. Flag-set only; draw in vblank.

## Open

Cadence is unknown until the engine reports cycles/token. Revisit the frame
budgets then — this document is a design, not a measurement.
