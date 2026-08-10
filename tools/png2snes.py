#!/usr/bin/env python3
"""
png2snes.py - convert a PNG into SNES 4bpp background data.

Emits: a 15-colour palette (index 0 reserved transparent), deduplicated 8x8
tiles, a tilemap, and a preview PNG showing exactly what the console renders.

The SNES is 256x224 (NTSC) with 8x8 tiles, so a full screen is 32x28 = 896 tile
slots. Backgrounds are 4bpp: 16 entries per palette, entry 0 transparent, so 15
usable colours. Colour depth is 5 bits per channel (BGR555), which is what
quantise_555 models - quantising to 8-bit first and to 5-bit later produces
banding the console would show but the preview would hide.
"""
import sys, struct
from PIL import Image

W, H, TILE = 256, 224, 8

def to555(c):
    """8-bit RGB -> the 5-bit-per-channel value the SNES actually stores."""
    return tuple((v >> 3) << 3 | (v >> 3) >> 2 for v in c)

def main(src, out_prefix, ncolors=15):
    im = Image.open(src).convert('RGB')

    # fit inside 256x224 preserving aspect, centre on the logo's own background
    im.thumbnail((W, H), Image.LANCZOS)
    bg = im.getpixel((0, 0))
    canvas = Image.new('RGB', (W, H), bg)
    canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))

    # A frequency-based quantiser is the wrong tool for a two-tone logo: red
    # covers most of the frame, so median-cut spends every palette entry on
    # shades of red and the lettering disappears. Build the ramp explicitly
    # between the darkest and lightest colours actually present, and assign by
    # luminance. ncolors == 2 gives hard edges; 3-4 gives antialiasing.
    px_rgb = list(canvas.getdata())
    lum = lambda c: 0.299*c[0] + 0.587*c[1] + 0.114*c[2]
    lo = min(px_rgb, key=lum); hi = max(px_rgb, key=lum)
    llo, lhi = lum(lo), lum(hi)

    ramp = []
    for i in range(ncolors):
        t = i / (ncolors - 1) if ncolors > 1 else 0
        ramp.append(tuple(round(lo[c] + (hi[c] - lo[c]) * t) for c in range(3)))
    pal555 = [to555(c) for c in ramp]

    span = (lhi - llo) or 1
    idx = []
    for c in px_rgb:
        t = (lum(c) - llo) / span
        idx.append(min(ncolors - 1, max(0, round(t * (ncolors - 1)))) + 1)

    # deduplicate tiles, honouring the H/V flip bits the tilemap provides free
    tiles, tmap, seen = [], [], {}
    for ty in range(H // TILE):
        for tx in range(W // TILE):
            t = tuple(idx[(ty*TILE+y)*W + tx*TILE + x]
                      for y in range(TILE) for x in range(TILE))
            variants = {
                'n' : t,
                'h' : tuple(t[y*TILE + (TILE-1-x)] for y in range(TILE) for x in range(TILE)),
                'v' : tuple(t[(TILE-1-y)*TILE + x] for y in range(TILE) for x in range(TILE)),
                'hv': tuple(t[(TILE-1-y)*TILE + (TILE-1-x)] for y in range(TILE) for x in range(TILE)),
            }
            hit = None
            for flip, v in variants.items():
                if v in seen:
                    hit = (seen[v], flip); break
            if hit is None:
                seen[t] = len(tiles); tiles.append(t); hit = (len(tiles)-1, 'n')
            tmap.append(hit)

    # 4bpp planar, SNES layout: planes 0/1 interleaved by row, then planes 2/3
    def encode(t):
        b = bytearray()
        for y in range(TILE):
            p0 = p1 = 0
            for x in range(TILE):
                v = t[y*TILE+x]
                p0 |= ((v >> 0) & 1) << (7-x)
                p1 |= ((v >> 1) & 1) << (7-x)
            b += bytes([p0, p1])
        for y in range(TILE):
            p2 = p3 = 0
            for x in range(TILE):
                v = t[y*TILE+x]
                p2 |= ((v >> 2) & 1) << (7-x)
                p3 |= ((v >> 3) & 1) << (7-x)
            b += bytes([p2, p3])
        return bytes(b)

    with open(out_prefix + '.chr', 'wb') as f:
        for t in tiles: f.write(encode(t))

    with open(out_prefix + '.pal', 'wb') as f:
        f.write(struct.pack('<H', 0))                       # entry 0 transparent
        for r, g, b in pal555:
            f.write(struct.pack('<H', ((b>>3)<<10) | ((g>>3)<<5) | (r>>3)))

    FLIP = {'n':0x0000, 'h':0x4000, 'v':0x8000, 'hv':0xC000}
    with open(out_prefix + '.map', 'wb') as f:
        for tid, flip in tmap:
            f.write(struct.pack('<H', (tid & 0x3FF) | FLIP[flip]))

    # preview: exactly what the PPU would put on screen
    prev = Image.new('RGB', (W, H))
    prev.putdata([pal555[i-1] for i in idx])
    prev.save(out_prefix + '_preview.png')
    prev.resize((W*3, H*3), Image.NEAREST).save(out_prefix + '_preview3x.png')

    flips = sum(1 for _, f in tmap if f != 'n')
    print(f"tiles       : {len(tiles)} unique of {len(tmap)} slots")
    print(f"  saved by flips : {flips}")
    print(f"CHR bytes   : {len(tiles)*32}  ({len(tiles)*32/1024:.1f} KB)")
    print(f"tilemap     : {len(tmap)*2} bytes")
    print(f"palette     : {ncolors} colours + transparent")
    print(f"VRAM total  : {(len(tiles)*32 + len(tmap)*2)/1024:.1f} KB of 64 KB")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 15)
