# Assets

## logo — Elyan Labs startup screen

`logo_source.png` (1536x1024) converted by `tools/png2snes.py` at 4 colours.

| | |
|---|---|
| unique tiles | **109** of 896 slots |
| CHR | 3.4 KB (4bpp planar) |
| tilemap | 1.8 KB (with H/V flip bits) |
| palette | 4 entries of 15, index 0 transparent |
| **VRAM** | **5.2 KB of 64 KB** |

`logo_preview3x.png` is exactly what the PPU renders, at 3x for inspection.

### Why 4 colours and not 15

A frequency-based quantiser is the wrong tool for a two-tone logo. Red covers
most of the frame, so median-cut spent **every** palette entry on shades of red
and the white lettering disappeared — 704 unique tiles and an illegible result
that was, in colour-error terms, optimal.

`png2snes.py` instead takes the darkest and lightest colours actually present,
builds an explicit ramp between them, and assigns by luminance. 704 -> 109 tiles.

The source red is not flat (11,610 unique colours), so every *extra* palette
entry fragments the background into hundreds of near-identical tiles. Four is
the point where the curves are smooth and the flats still deduplicate.
