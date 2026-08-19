# Art spec — draw against these numbers

Everything here is a hardware constraint, not a style preference. Art that
matches these drops straight into the pipeline; art that does not has to be
resampled, and resampling is what made the logo need 704 tiles instead of 109.

## Elya, canon — this section is normative

From `patch-and-veil-elya-design-lock.md`. Written here so it cannot drift
again: the first sprite batch drifted on all four counts below, and drift in a
character is not something a palette check or a tile count can catch.

| | |
|---|---|
| **hair** | **long uncut AUBURN-RED, past the waist.** Not brown. Not dark. |
| **dress** | **BROWN Victorian** — high collar, long sleeves, floor-length skirt |
| **NOT** | **no apron. No maid gear.** She is not a servant and is not dressed as one |
| **build** | petite adult woman, mid-twenties — **never childlike** |
| **NOT** | **no earrings, no jewellery, no lipstick, no makeup.** Image models add all four by default and every one of them has to be explicitly negated |
| **facing** | **RIGHT.** OAM's H-flip is free, but only if the source faces the way the game runs |
| **read** | **16-bit, not 8-bit.** She should fill her 32x32 cell and carry visible detail — hair mass, collar, sleeve, hem — not read as a 16x16 character scaled up |

The last one is a size statement, not a style one: a figure that occupies 13
of 32 pixels of width has three pixels of face and no room for the dress to be
a dress. Fill the cell.

`tools/mkart.py` will **not** build a sprite sheet from art that has not been
checked against this list. `art_spec_check()` is that check, expressed as
numbers, and it runs on every build against whatever is about to be baked:

| check | threshold | which drift it catches |
|---|---|---|
| width | **>= 24 of 32** | "fill the cell" — a narrow figure has no room for a face |
| fill | **>= 45%** | same, by area |
| skin | **>= 28 px** | a face needs pixels; the placeholder had 16 and had none |
| white | **<= 12 px** | **apron / maid's collar.** A wide flat white collar is maid gear by another name |
| hair | **>= dress/3** | "long uncut, past the waist" |
| facing | skin centroid right of hair centroid | **faces RIGHT** |

The white budget is there because the palette trick was not enough. The object
palette was deliberately scrubbed of apron white so a maid outfit would have
nowhere to live — and the drift came back anyway, through the *shape*, as a
broad flat collar drawn in the one white entry the collar legitimately needs.
A palette check cannot catch that. A pixel budget can.

Elya herself is hand-placed at native size in `mkart.py`, in the same idiom as
the `@` block and the nabla, because two rounds of generated art failed this
list and downscaling a 512 px render into a 32 px cell spends most of the cell
on mush. Generated art may still override her — but only if it passes the gate.
Keying on "the file exists" is how the apron got in the first time; the
filename never knew what was in the picture.

## Canvas

**256 x 224.** That is the whole NTSC screen. Design at 1x — not at 4x and
downscaled. Downscaling invents intermediate colours at every edge, and on a
4bpp target those either get quantised away (mush) or eat palette entries
(expensive). Hand-placed pixels at native size beat any downscale.

If you must work large, work at an **integer multiple** (2x = 512x448, 4x =
1024x896) with hard edges and no antialiasing, then nearest-neighbour down.

## Sprites

Object size is set **globally**, one pair for the whole screen (`OBSEL`). The
useful pair for a platformer is **16x16 and 32x32**:

| object | slot | notes |
|---|---|---|
| Elya | 32x32 | gives room for a jump pose and a foot-tap without swapping size |
| `@` block | 16x16 | |
| coin | 16x16 | |
| nabla | 16x16 or 32x32 | whichever the design needs — pick before drawing |

* **16 colours per sprite palette, entry 0 transparent — so 15 usable.**
* Sprites use palettes 8-15; backgrounds use 0-7. They are separate budgets,
  so sprite colour does not compete with the sky.
* **128 sprites on screen; 32 per scanline.** A 32x32 Elya costs four 16x16
  slots' worth of scanline budget. Not a concern for this design, but it is why
  you do not get an army of nablas.
* Horizontal and vertical flip are **free** in OAM. Draw Elya facing one way
  only; walking left costs nothing extra.

## Background

* 8x8 tiles, **4bpp, 15 colours + transparent**.
* Flat areas deduplicate to a single tile, so large fields of one colour are
  effectively free. **Texture is what costs VRAM**, not size.
* H/V flip works in the tilemap too — symmetric scenery is half price.
* The sky gradient is HDMA and costs no tiles at all. Do not draw a gradient
  into the background art; it will be generated.

## Palette discipline — the thing that actually bites

The logo taught this the hard way: **the source red was not flat** (11,610
unique colours), and every extra palette entry fragmented the background into
hundreds of near-identical tiles.

So: **use exact, repeated colour values.** If two areas are meant to be the same
red, they must be the *same* RGB triple, not two reds that look alike. In your
editor, work from a fixed swatch set and turn antialiasing OFF.

A good starting budget for a platformer scene:

| use | entries |
|---|---|
| Elya skin/hair/dress | 6-8 |
| `@` block + coin (gold ramp) | 3-4 |
| nabla | 3 |
| ground/scenery | 6-8 |
| **sky** | **0 — HDMA** |

## Animation

Frames cost VRAM linearly, so budget them:

| animation | frames | why |
|---|---|---|
| idle bob | 2 | |
| **foot tap** | **4** | the loop that runs while she waits on you |
| jump | 1 | |
| land | 1 | |
| coin spin | 4 | classic rotation reads at any size |
| block struck | 1 | squash frame |

That is ~13 sprite frames. At 32x32 4bpp a frame is 512 bytes, at 16x16 it is
128. Call it 8 KB of VRAM for everything, against 64 KB total and 5.2 KB already
spent on the logo.

## What to send me

PNG, native resolution, no antialiasing, hard edges, transparent background for
sprites (real alpha, not a magenta key). I will run it through
`tools/png2snes.py` and send back a preview of **exactly what the PPU renders**,
plus the tile count, before anything gets baked into the ROM.

## One boundary

Original art in the familiar idiom, not the familiar art. The mechanic — jump,
strike block, collectible pops out — is not protectable and never has been. The
specific Mario sprite, the coin sprite, the `?` glyph and the sounds are. That
is why the block is stamped `@`, and it is a better joke anyway.
