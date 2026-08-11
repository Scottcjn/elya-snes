#!/usr/bin/env python3
"""mkart.py -- every sprite the game draws, as one SNES 4bpp OBJ sheet.

Two sources, and the split is deliberate.

*Elya* comes from the SDXL + trained-LoRA generations in assets/sprites.  They
are ~1024 px paintings of pixel art, not pixel art, so they are pixelised here
against the hardware: crop to content, box-filter down to the OAM cell, then
snap every pixel to the ONE fifteen-colour object palette.  Doing it here and
not in the generator is the whole point of ART_SPEC -- the constraint decides
the art, not the other way round.

*Everything else* -- the @ block, the coin, the nabla -- is authored directly
at native size as an ASCII grid below.  A 16x16 object has 256 pixels; a
downscale of a 512 px render spends most of them on mush, and ART_SPEC says so
in as many words.  It is also the honest way to satisfy the originality rule:
these three are drawn here, in this file, and nowhere else.

Sheet layout is not free-form.  OAM addresses a 32x32 object by the top-left
tile of a 4x4 block in a 16-tile-wide CHR grid, so the sheet is 128 px wide and
sprites are placed on that grid, not packed.
"""
import os
import struct
import sys

from PIL import Image

# ---------------------------------------------------------------------------
# The object palette.  ONE row for every sprite in the game: 15 colours plus
# transparent.  Exact repeated triples, per ART_SPEC -- two reds that merely
# look alike cost a palette entry each and buy nothing.
# ---------------------------------------------------------------------------
OBJPAL = [
    (0, 0, 0),          # 0  transparent (never written)
    (16, 16, 24),       # 1  outline
    (248, 208, 176),    # 2  skin
    (200, 152, 120),    # 3  skin shadow
    (176, 104, 56),     # 4  hair, auburn light
    (104, 56, 32),      # 5  hair, auburn dark
    (56, 56, 80),       # 6  dress mid
    (32, 32, 48),       # 7  dress dark
    (248, 248, 248),    # 8  apron / spike white
    (184, 184, 200),    # 9  apron shade
    (255, 232, 120),    # 10 gold light
    (232, 176, 40),     # 11 gold mid
    (152, 96, 8),       # 12 gold dark
    (224, 48, 48),      # 13 nabla red
    (128, 16, 16),      # 14 nabla red dark
    (120, 120, 136),    # 15 neutral grey
]

# Elya may only use these: snapping her to the gold or the nabla red would
# make her flicker between palette neighbours frame to frame.
ELYA_INK = [1, 2, 3, 4, 5, 6, 7, 8, 9, 15]

# ---------------------------------------------------------------------------
# Hand-authored objects.  Digits are palette indices, '.' is transparent.
# ---------------------------------------------------------------------------

# The @ block.  A gold cube stamped with the matrix-multiply operator, because
# `A @ B` in numpy is what makes the tokens the block gives out.  Deliberately
# not a '?'.
#
# The glyph is the 5x7 '@' from tools/mkfont.py at 2x horizontally and 12/7
# vertically, which is why it reads at all: a freehand 10x12 '@' came out as a
# black blob, and the font's is the shape that was already known to work.
def at_glyph(w, h):
    """The '@' as two concentric rings, the inner one open to the right.

    Drawn from ellipse geometry rather than by hand, and the reason is on
    record: three hand-drawn attempts at 10-14 pixels all came out as a black
    blob or a spiral, because at this size the stroke width and the ring gap
    have to be within half a pixel of right and eyeballing them is not that
    accurate.  The shape is the CP437 idiom -- a ring with a 'c' inside -- and
    it is what still reads as '@' when the whole glyph is twelve pixels.
    """
    import math
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    rx, ry = w / 2.0, h / 2.0
    g = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            dx, dy = (x - cx) / rx, (y - cy) / ry
            d = math.hypot(dx, dy)
            a = math.degrees(math.atan2(y - cy, x - cx))
            if 0.72 <= d <= 0.94:
                g[y][x] = 1
            if 0.28 <= d <= 0.50 and not (-48 <= a <= 48):
                g[y][x] = 1
    return g


def at_block(height=16):
    """A bevelled gold cube stamped with the matmul operator.  `A @ B` in numpy
    is what makes the tokens the block gives out, so it is what the block is
    stamped with.  Deliberately not a '?'."""
    g = [[0] * 16 for _ in range(16)]
    top = (16 - height) // 2 + (16 - height) % 2      # a strike squashes down
    for y in range(height):
        for x in range(16):
            yy = top + y
            if y in (0, height - 1) or x in (0, 15):
                g[yy][x] = 12
            elif y == 1 or x == 1:
                g[yy][x] = 10
            elif y == height - 2 or x == 14:
                g[yy][x] = 12
            else:
                g[yy][x] = 11
    gl = at_glyph(12, height - 4)
    for y in range(len(gl)):
        for x in range(12):
            if gl[y][x]:
                g[top + 2 + y][2 + x] = 1
    return g

# The coin: four frames of rotation.  A ring rather than a disc with a face --
# a ring is what still reads as a coin when the rotation has it one pixel wide.
COIN = ["""
................
................
......CCCC......
....CCAAAACC....
...CAABBBBAAC...
..CAABB..BBAAC..
..CAB......BAC..
..CAB......BAC..
..CAB......BAC..
..CAB......BAC..
..CAABB..BBAAC..
...CAABBBBAAC...
....CCAAAACC....
......CCCC......
................
................
""", """
................
................
......CCCC......
.....CAAAAC.....
....CAABBAAC....
....CAB..BAC....
....CAB..BAC....
....CAB..BAC....
....CAB..BAC....
....CAB..BAC....
....CAB..BAC....
....CAABBAAC....
.....CAAAAC.....
......CCCC......
................
................
""", """
................
................
.......CC.......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
......CAAC......
.......CC.......
................
................
""", """
................
................
......CCCC......
.....CBBBBC.....
....CBBCCBBC....
....CBC..CBC....
....CBC..CBC....
....CBC..CBC....
....CBC..CBC....
....CBC..CBC....
....CBC..CBC....
....CBBCCBBC....
.....CBBBBC.....
......CCCC......
................
................
"""]

# The nabla.  A downward triangle with white spikes and two eyes: the gradient
# operator as the thing descending on her.  The ROM does inference and has no
# gradients in it at all, which is the joke -- the thing from training that
# cannot touch her any more, still chasing.
NABLA = ["""
...8...8...8....
..888.888.888...
EEEEEEEEEEEEEEEE
EDDDDDDDDDDDDDDE
.EDD888DD888DDE.
.EDD818DD818DDE.
..EDDDDDDDDDDE..
..EDDDDDDDDDDE..
...EDDDDDDDDE...
...EDDDDDDDDE...
....EDDDDDDE....
....EDDDDDDE....
.....EDDDDE.....
.....EDDDDE.....
......EDDE......
.......EE.......
"""]

HEXMAP = {'.': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
          '8': 8, '9': 9, 'A': 10, 'B': 11, 'C': 12, 'D': 13, 'E': 14,
          'W': 8, 'F': 15}


def grid(art, w, h):
    rows = [r for r in art.strip('\n').split('\n')]
    assert len(rows) == h, "art is %d rows, want %d" % (len(rows), h)
    g = []
    for r in rows:
        assert len(r) == w, "art row %r is %d wide, want %d" % (r, len(r), w)
        g.append([HEXMAP[c] for c in r])
    return g


# ---------------------------------------------------------------------------
# Pixelising a generated frame
# ---------------------------------------------------------------------------
def near(c, allowed):
    """Nearest palette entry, in plain RGB distance.  Perceptual weighting was
    tried and made her hair snap to the dress: at 32 px the hue matters more
    than the luminance does."""
    best, bd = allowed[0], None
    for i in allowed:
        p = OBJPAL[i]
        d = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2
        if bd is None or d < bd:
            best, bd = i, d
    return best


def pixelise(path, crop, size, bg, tol=26, ink=None, shift=(0, 0), cover=0.45):
    """One generated frame -> a size[0] x size[1] grid of palette indices.

    crop is the source rectangle to take (the sheets hold several poses).
    bg/tol identify the flat background.

    The background is removed BEFORE the downscale, not after, and the colour
    is carried premultiplied.  The first version of this function tested for
    background after the box filter, and every edge pixel had already been
    averaged with the paper -- so the light grey that produced snapped to the
    apron white and Elya came out wearing a halo.  Downscaling the coverage
    mask separately is what fixes it: a pixel is drawn only if more than
    `cover` of it was actually her.

    Scale is fixed by HEIGHT.  Fitting the whole bounding box inside the cell
    sounds right and is not: her running pose has a skirt sweep, so the box is
    wide, and fitting it made a 32-pixel-tall character 18 pixels tall.
    """
    ink = ink or ELYA_INK
    im = Image.open(path).convert('RGB').crop(crop)
    px = im.load()
    W_, H_ = im.width, im.height

    opaque = Image.new('L', (W_, H_), 0)
    op = opaque.load()
    x0, y0, x1, y1 = W_, H_, -1, -1
    for y in range(H_):
        for x in range(W_):
            c = px[x, y]
            if max(abs(c[i] - bg[i]) for i in range(3)) > tol:
                op[x, y] = 255
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 < x0:
        raise SystemExit("%s: crop %r is entirely background" % (path, crop))
    box = (x0, y0, x1 + 1, y1 + 1)
    im, opaque = im.crop(box), opaque.crop(box)

    # premultiply so the paper cannot bleed into the edge colours
    pm = Image.new('RGB', im.size)
    ip, mp, pp = im.load(), opaque.load(), pm.load()
    for y in range(im.height):
        for x in range(im.width):
            if mp[x, y]:
                pp[x, y] = ip[x, y]

    w, h = size
    sc = h / im.height
    nw, nh = max(1, round(im.width * sc)), h
    pm = pm.resize((nw, nh), Image.BOX)
    opaque = opaque.resize((nw, nh), Image.BOX)
    a, m = pm.load(), opaque.load()

    g = [[0] * w for _ in range(h)]
    ox, oy = (w - nw) // 2 + shift[0], (h - nh) + shift[1]
    for y in range(nh):
        for x in range(nw):
            cov = m[x, y] / 255.0
            if cov < cover:
                continue
            c = tuple(min(255, round(v / cov)) for v in a[x, y])
            gy, gx = oy + y, ox + x
            if 0 <= gy < h and 0 <= gx < w:
                g[gy][gx] = near(c, ink)
    return g


# ---------------------------------------------------------------------------
# 4bpp CHR, and the 16-tile-wide sheet OAM needs
# ---------------------------------------------------------------------------
def encode4bpp(t):
    b = bytearray()
    for y in range(8):
        p0 = p1 = 0
        for x in range(8):
            v = t[y][x]
            p0 |= (v & 1) << (7 - x)
            p1 |= ((v >> 1) & 1) << (7 - x)
        b += bytes([p0, p1])
    for y in range(8):
        p2 = p3 = 0
        for x in range(8):
            v = t[y][x]
            p2 |= ((v >> 2) & 1) << (7 - x)
            p3 |= ((v >> 3) & 1) << (7 - x)
        b += bytes([p2, p3])
    return bytes(b)


SHEET_W = 128


def main(out_prefix='assets/obj'):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sp = os.path.join(root, 'assets', 'sprites')

    # -- Elya.  The tap sheet holds four standing poses across 1024 px; the run
    #    sheet holds one moving pose.  Backgrounds differ between the two, and
    #    the run sheet has a black border the crop must stay inside of.
    tap = os.path.join(sp, 'elya_tap_sheet.png')
    run = os.path.join(sp, 'elya_run.png')
    TAPBG, RUNBG = (243, 242, 239), (160, 160, 160)

    # run: one generated pose.  The inner crop dodges the black frame the
    # generator drew round the sheet, which is not background and would
    # otherwise decide the bounding box.
    r0 = pixelise(run, (30, 20, 1000, 492), (32, 32), RUNBG)
    # frame two: the same pose lifted a pixel.  A two-frame run is what the
    # 16-bit era shipped, and a bob the eye reads as a stride is cheaper in
    # VRAM than a second generation -- which would not be the same Elya.
    r1 = [[0] * 32] + [row[:] for row in r0[:-1]]
    # jump: higher still, which is all a one-frame jump needs to read
    jp = [[0] * 32, [0] * 32] + [row[:] for row in r0[:-2]]

    # idle: the foot-tap sheet holds four standing poses, but pose 0 is drawn
    # with a WHITE apron where 1-3 are dark, so cycling all four flashes.  The
    # loop is the three that agree, played 0,1,2,1 -- a four-frame tap out of
    # three frames of VRAM.
    idle = [pixelise(tap, (i * 256, 0, (i + 1) * 256, 512), (32, 32), TAPBG)
            for i in (1, 2, 3)]

    # Both generations draw her facing LEFT.  She runs right, so every frame is
    # mirrored once here and the OAM H-flip bit -- which is free -- is spent on
    # the rarer direction instead of the common one.
    elya = [[row[::-1] for row in f] for f in ([r0, r1, jp] + idle)]

    objs16 = [at_block(16), at_block(12)]
    objs16 += [grid(c, 16, 16) for c in COIN]
    n0 = grid(NABLA[0], 16, 16)
    # the menace bob: the same nabla one pixel lower with its spikes clipped,
    # so two frames cost one drawing and the bob still reads
    n1 = [[0] * 16] + [r[:] for r in n0[:-1]]
    n1[1] = [0] * 16
    objs16 += [n0, n1]

    # -- the sheet.  128 px wide; 32x32 objects sit on the 4-tile grid, 16x16
    #    objects on the 2-tile grid, because that is how OAM addresses them.
    rows32 = (len(elya) + 3) // 4
    h = rows32 * 32 + ((len(objs16) * 16 + SHEET_W - 1) // SHEET_W) * 16
    sheet = [[0] * SHEET_W for _ in range(h)]

    index = {}
    for i, g in enumerate(elya):
        ox, oy = (i % 4) * 32, (i // 4) * 32
        for y in range(32):
            for x in range(32):
                sheet[oy + y][ox + x] = g[y][x]
        index['elya%d' % i] = (oy // 8) * 16 + ox // 8
    base16 = rows32 * 32
    for i, g in enumerate(objs16):
        ox, oy = (i % 8) * 16, base16 + (i // 8) * 16
        for y in range(16):
            for x in range(16):
                sheet[oy + y][ox + x] = g[y][x]
        index['obj%d' % i] = (oy // 8) * 16 + ox // 8

    # -- CHR, row-major over the 16-wide tile grid
    chr_data = bytearray()
    for ty in range(h // 8):
        for tx in range(SHEET_W // 8):
            chr_data += encode4bpp([[sheet[ty * 8 + y][tx * 8 + x]
                                     for x in range(8)] for y in range(8)])
    open(out_prefix + '.chr', 'wb').write(chr_data)

    with open(out_prefix + '.pal', 'wb') as f:
        for r, g_, b in OBJPAL:
            f.write(struct.pack('<H', ((b >> 3) << 10) | ((g_ >> 3) << 5) | (r >> 3)))

    # -- preview: exactly the pixels the PPU will fetch
    prev = Image.new('RGB', (SHEET_W, h), (255, 0, 255))
    p = prev.load()
    for y in range(h):
        for x in range(SHEET_W):
            v = sheet[y][x]
            if v:
                p[x, y] = OBJPAL[v]
    prev.resize((SHEET_W * 4, h * 4), Image.NEAREST).save(out_prefix + '_preview.png')

    with open(out_prefix + '.inc', 'w') as f:
        f.write("; GENERATED by tools/mkart.py -- do not edit\n")
        for k, v in sorted(index.items(), key=lambda kv: kv[1]):
            f.write("OBJT_%-8s = %d\n" % (k.upper(), v))
        f.write("OBJ_TILES = %d\n" % (len(chr_data) // 32))
    print("sheet   %dx%d px, %d tiles, %d bytes of 4bpp CHR"
          % (SHEET_W, h, len(chr_data) // 32, len(chr_data)))
    for k, v in sorted(index.items(), key=lambda kv: kv[1]):
        print("  %-8s tile %3d" % (k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'assets/obj'))
