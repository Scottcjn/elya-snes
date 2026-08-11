#!/usr/bin/env python3
"""mkbg.py -- the background layers, the level, and the HDMA sky.

Three things come out of here and they are on three different budgets:

  BG1  the level.  4bpp, palette 2 (CGRAM 32-47), 64x32 tilemap = 512x256 px.
       One screen of scroll either side of the middle, which is enough level
       for the act and avoids the column-streaming machinery a longer one
       would need.
  BG2  the clouds.  4bpp, palette 3 (CGRAM 48-63), 32x32 tilemap, which wraps
       by itself at 256 px so the parallax needs no seam handling at all.
  BG3  text.  2bpp, palettes 0 and 1 (CGRAM 0-7).

No tile anywhere draws sky.  The sky is a per-scanline backdrop colour driven
by one HDMA channel, so it costs no tiles, no tilemap and no CPU in the token
loop -- which is the whole reason the design puts it there.  The table is
emitted here as well: 28 bands of 8 scanlines, transfer mode 3 ($2121, $2121,
$2122, $2122) so each band writes a whole BGR555 colour into CGRAM entry 0.

Banding rather than per-scanline is not a compromise on looks, it is 1/8th of
the DMA cost, and at 8 scanlines a band the steps are invisible on a gradient
this shallow.
"""
import os
import struct
import sys

# ---------------------------------------------------------------------------
BG1PAL = [
    (0, 0, 0),          # 0  transparent -- the sky shows through
    (120, 208, 88),     # 1  grass light
    (56, 152, 56),      # 2  grass dark
    (176, 120, 72),     # 3  dirt light
    (136, 88, 48),      # 4  dirt mid
    (88, 56, 32),       # 5  dirt dark
    (208, 208, 216),    # 6  stone light
    (152, 152, 168),    # 7  stone mid
    (96, 96, 112),      # 8  stone dark
    (240, 160, 120),    # 9  brick light
    (200, 96, 64),      # A  brick mid
    (120, 48, 32),      # B  brick mortar
    (32, 28, 40),       # C  outline
    (248, 248, 248),    # D  white
    (72, 68, 88),       # E  shade
    (255, 232, 120),    # F  gold
]

BG2PAL = [
    (0, 0, 0),          # 0  transparent
    (248, 248, 248),    # 1  cloud white
    (224, 232, 248),    # 2  cloud light
    (192, 208, 240),    # 3  cloud shade
    (160, 184, 232),    # 4  cloud edge
] + [(0, 0, 0)] * 11

# BG3 is 2bpp: palette 0 is CGRAM 0-3, palette 1 is CGRAM 4-7.  Entry 0 is the
# backdrop, which the HDMA rewrites every eight scanlines; entries 0 and 4 are
# never drawn by a 2bpp tile, so the sky and the text share them harmlessly.
BG3PAL = [
    (0, 0, 0),          # 0  backdrop (HDMA)      page-0 paper: transparent
    (248, 248, 248),    # 1  text ink
    (40, 36, 64),       # 2  text shadow
    (255, 216, 96),     # 3  text highlight
    (0, 0, 0),          # 4  unused (palette 1's transparent index)
    (248, 248, 248),    # 5  text ink, in the box
    (24, 24, 56),       # 6  box fill             page-1 paper: opaque
    (120, 128, 208),    # 7  box border
    (0, 0, 0),          # 8  unused (palette 2's transparent index)
    (255, 216, 96),     # 9  the QUESTION's ink, amber
    (24, 24, 56),       # 10 the same box fill
    (120, 128, 208),    # 11 the same box border
]
# Palette 2 exists so the same page-1 tiles can be drawn in two inks: what the
# player asked in amber, what the console generated in white.  The screen then
# shows, without a caption, which characters came out of the model.

# ---------------------------------------------------------------------------
# BG1 tiles.  Hex digits are palette indices.
# ---------------------------------------------------------------------------
BG1TILES = {
'empty': """
00000000
00000000
00000000
00000000
00000000
00000000
00000000
00000000
""",
'grass': """
11111111
12111211
22212221
34343434
33333333
34333433
33333333
33343333
""",
'dirt': """
44444444
45444544
44444444
44454445
44444444
54444444
44444444
44444544
""",
'brick': """
BBBBBBBB
B9999999
BAAAAAAA
BAAAAAAA
BBBBBBBB
9999B999
AAAABAAA
AAAABAAA
""",
'stone': """
66666666
67777776
67787876
67777776
67787776
67777776
68888886
88888888
""",
}
BG1ORDER = ['empty', 'grass', 'dirt', 'brick', 'stone']

TILE = 8


def grid(art):
    rows = [r for r in art.strip('\n').split('\n')]
    assert len(rows) == TILE, "tile has %d rows" % len(rows)
    out = []
    for r in rows:
        assert len(r) == TILE, "tile row %r is %d wide" % (r, len(r))
        out.append([int(c, 16) for c in r])
    return out


def encode4bpp(t):
    b = bytearray()
    for y in range(TILE):
        p0 = p1 = 0
        for x in range(TILE):
            v = t[y][x]
            p0 |= (v & 1) << (7 - x)
            p1 |= ((v >> 1) & 1) << (7 - x)
        b += bytes([p0, p1])
    for y in range(TILE):
        p2 = p3 = 0
        for x in range(TILE):
            v = t[y][x]
            p2 |= ((v >> 2) & 1) << (7 - x)
            p3 |= ((v >> 3) & 1) << (7 - x)
        b += bytes([p2, p3])
    return bytes(b)


def pal555(pal):
    b = bytearray()
    for r, g, bl in pal:
        b += struct.pack('<H', ((bl >> 3) << 10) | ((g >> 3) << 5) | (r >> 3))
    return bytes(b)


# ---------------------------------------------------------------------------
# clouds: three overlapping ellipses, rasterised, then shaded by depth so the
# shape reads without an outline.  Two clouds, 32x16 and 24x16.
# ---------------------------------------------------------------------------
def cloud(w, h, blobs):
    g = [[0] * w for _ in range(h)]
    for (cx, cy, rx, ry) in blobs:
        for y in range(h):
            for x in range(w):
                d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
                if d <= 1.0:
                    g[y][x] = max(g[y][x], 1)
    # shade: the bottom two rows of the shape go to cloud shade, the outline
    # pixel below that to the edge colour
    for x in range(w):
        col = [y for y in range(h) if g[y][x]]
        if not col:
            continue
        lo = max(col)
        for y in col:
            if y >= lo - 1:
                g[y][x] = 3
            elif y >= lo - 3:
                g[y][x] = 2
        if lo + 1 < h:
            g[lo + 1][x] = 4
    return g


def main(outdir='assets'):
    # ---- BG1 --------------------------------------------------------------
    chr1 = bytearray()
    for name in BG1ORDER:
        chr1 += encode4bpp(grid(BG1TILES[name]))
    open(os.path.join(outdir, 'bg1.chr'), 'wb').write(chr1)
    open(os.path.join(outdir, 'bg1.pal'), 'wb').write(pal555(BG1PAL))

    T = {n: i for i, n in enumerate(BG1ORDER)}

    # ---- the level.  64x32 tiles = 512x256 px; the visible window is the top
    #      224 px, so the ground surface is row 24 and rows 28-31 never show.
    MW, MH = 64, 32
    GROUND = 24
    lvl = [[T['empty']] * MW for _ in range(MH)]
    for x in range(MW):
        lvl[GROUND][x] = T['grass']
        for y in range(GROUND + 1, MH):
            lvl[y][x] = T['dirt']
    # a hole to jump, and three ledges.  Solid ground is what the physics reads
    # back out of this array, so the level and the collision are one thing.
    for x in range(30, 33):
        for y in range(GROUND, MH):
            lvl[y][x] = T['empty']
    for (x0, x1, y) in ((10, 14, 20), (26, 29, 17), (40, 45, 20), (52, 56, 16)):
        for x in range(x0, x1 + 1):
            lvl[y][x] = T['brick']
    for (x0, x1, y) in ((20, 23, 21), (36, 38, 15)):
        for x in range(x0, x1 + 1):
            lvl[y][x] = T['stone']

    # tilemap words: tile index in bits 0-9, palette in 10-12, priority bit 13
    PAL1 = 2 << 10
    with open(os.path.join(outdir, 'level.map'), 'wb') as f:
        for scr in range(2):                    # 64x32 is two 32x32 screens
            for y in range(MH):
                for x in range(32):
                    f.write(struct.pack('<H', lvl[y][scr * 32 + x] | PAL1))
    with open(os.path.join(outdir, 'level.solid'), 'wb') as f:
        # one byte a tile, 1 = solid.  The ROM reads THIS, not the tilemap, so
        # a decorative tile can never be walked on by accident.
        for y in range(MH):
            for x in range(MW):
                f.write(bytes([1 if lvl[y][x] in (T['grass'], T['dirt'],
                                                  T['brick'], T['stone'])
                               else 0]))

    # ---- BG2 clouds -------------------------------------------------------
    c1 = cloud(32, 16, [(10, 10, 8, 5), (18, 8, 7, 6), (25, 11, 6, 4)])
    c2 = cloud(24, 16, [(8, 10, 6, 4), (15, 9, 6, 5)])
    tiles2 = [[[0] * TILE for _ in range(TILE)]]        # tile 0 = empty
    def add(g, w, h):
        base = len(tiles2)
        for ty in range(h // TILE):
            for tx in range(w // TILE):
                tiles2.append([[g[ty * TILE + y][tx * TILE + x]
                                for x in range(TILE)] for y in range(TILE)])
        return base, w // TILE, h // TILE
    b1 = add(c1, 32, 16)
    b2 = add(c2, 24, 16)
    chr2 = bytearray()
    for t in tiles2:
        chr2 += encode4bpp(t)
    open(os.path.join(outdir, 'bg2.chr'), 'wb').write(chr2)
    open(os.path.join(outdir, 'bg2.pal'), 'wb').write(pal555(BG2PAL))

    sky = [[0] * 32 for _ in range(32)]
    PAL2 = 3 << 10
    for (base, tw, th, ox, oy) in ((b1[0], b1[1], b1[2], 1, 3),
                                   (b2[0], b2[1], b2[2], 12, 8),
                                   (b1[0], b1[1], b1[2], 20, 1),
                                   (b2[0], b2[1], b2[2], 27, 6)):
        for ty in range(th):
            for tx in range(tw):
                sky[(oy + ty) % 32][(ox + tx) % 32] = base + ty * tw + tx
    with open(os.path.join(outdir, 'clouds.map'), 'wb') as f:
        for y in range(32):
            for x in range(32):
                f.write(struct.pack('<H', sky[y][x] | PAL2))

    # ---- BG3 palette ------------------------------------------------------
    open(os.path.join(outdir, 'bg3.pal'), 'wb').write(pal555(BG3PAL))

    # ---- the HDMA sky -----------------------------------------------------
    # 28 bands of 8 scanlines.  Deep blue at the top, pale at the horizon; the
    # ground starts at scanline 192, so the last four bands are under the
    # tilemap and are dark on purpose -- they are what shows in the hole.
    TOP, BOT = (24, 40, 128), (176, 216, 248)
    hd = bytearray()
    for b in range(28):
        t = b / 27.0
        c = tuple(round(TOP[i] + (BOT[i] - TOP[i]) * t) for i in range(3))
        v = ((c[2] >> 3) << 10) | ((c[1] >> 3) << 5) | (c[0] >> 3)
        hd += bytes([8, 0x00, 0x00, v & 0xFF, v >> 8])
    hd += bytes([0])
    open(os.path.join(outdir, 'sky.hdma'), 'wb').write(hd)

    with open(os.path.join(outdir, 'bg.inc'), 'w') as f:
        f.write("; GENERATED by tools/mkbg.py -- do not edit\n")
        for i, n in enumerate(BG1ORDER):
            f.write("BGT_%-6s = %d\n" % (n.upper(), i))
        f.write("BG1_TILES   = %d\n" % len(BG1ORDER))
        f.write("BG2_TILES   = %d\n" % len(tiles2))
        f.write("LEVEL_W     = %d\n" % MW)
        f.write("LEVEL_H     = %d\n" % MH)
        f.write("LEVEL_GND   = %d\n" % GROUND)
        f.write("SKY_BANDS   = %d\n" % 28)

    print("bg1  %d tiles, %d B   level %dx%d, %d B map, %d B solid"
          % (len(BG1ORDER), len(chr1), MW, MH, MW * MH * 2, MW * MH))
    print("bg2  %d tiles, %d B   clouds 32x32, %d B map"
          % (len(tiles2), len(chr2), 32 * 32 * 2))
    print("sky  %d bands, %d B HDMA table" % (28, len(hd)))

    # preview of the level as the PPU would draw it, for eyeballing
    from PIL import Image
    im = Image.new('RGB', (MW * TILE, MH * TILE))
    p = im.load()
    tl = [grid(BG1TILES[n]) for n in BG1ORDER]
    for y in range(MH):
        for x in range(MW):
            t = tl[lvl[y][x]]
            for yy in range(TILE):
                for xx in range(TILE):
                    v = t[yy][xx]
                    band = min(27, (y * TILE + yy) // 8)
                    sk = tuple(round(TOP[i] + (BOT[i] - TOP[i]) * band / 27.0)
                               for i in range(3))
                    p[x * TILE + xx, y * TILE + yy] = BG1PAL[v] if v else sk
    im.save(os.path.join(outdir, 'level_preview.png'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'assets'))
