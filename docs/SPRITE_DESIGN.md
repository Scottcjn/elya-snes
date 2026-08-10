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

---

## The world

Blue sky, puffy clouds, and something with spikes chasing her.

### The sky is an HDMA gradient — and that is the point

The SNES can change a background colour **per scanline** using HDMA, driven by
the DMA controller rather than the CPU. A sky that fades from deep blue at the
top to pale at the horizon costs one HDMA channel and **zero CPU cycles in the
inference loop**.

This matters beyond looking nice. The Genesis port could not do this: its
`drawTokSpeed()` runs a `sprintf` and a VRAM write *inside* the measured
interval, because the 68000 had to do the drawing itself. On the SNES the
gradient is set up once and the DMA controller repaints it every frame while the
65816 does nothing but ternary gathers.

So the prettier console is also the more honest one — more of what you see is
free.

Clouds: a second BG layer scrolling slowly, parallax against the ground. Also
free, also DMA-driven.

### The antagonist

A spiky triangle face chasing Elya.

Proposal, take it or leave it: make it **∇ — the Gradient**.

`∇` (nabla) is the gradient operator, and it is already a spiky downward
triangle. "Gradient descent" becomes literal: the thing descending on her.

It also lands a real joke. The ROM does **inference**, not training — there are
no gradients in it at all. So ∇ is the thing from training that cannot touch her
any more, still chasing anyway. She got quantised to ternary and left it behind.

Same joke family as the `@` block: a real mathematical operator as a character,
readable as pure platformer to anyone who does not care, and a second layer for
anyone who does.

If ∇ is too cute, a plain spiky triangle works fine and the world does not need
the pun to hold together.

### Sprite budget, revised

| object | size | frames | cost |
|---|---|---|---|
| Elya | 16x24 | idle 2, jump 1, land 1 | OAM |
| `@` block | 16x16 | rest 1, struck 1 | OAM |
| coin | 8x8 | 4 spin | OAM |
| ∇ antagonist | 16x16 | 2 (menace bob) | OAM |
| sky gradient | — | — | **1 HDMA channel, 0 CPU** |
| clouds | BG layer | scroll | **DMA, 0 CPU** |

Everything expensive is on hardware that is not the CPU. The inference loop
still only ever sets a flag.
