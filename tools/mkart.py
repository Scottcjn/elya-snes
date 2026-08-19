#!/usr/bin/env python3
"""mkart.py -- every sprite the game draws, as one SNES 4bpp OBJ sheet.

Two sources, and the split is deliberate.

*Elya* comes from a generated render, pixelised here against the hardware:
crop to content, box-filter down to the OAM cell, then snap every pixel to the
ONE fifteen-colour object palette.  Doing it here and not in the generator is
the whole point of ART_SPEC -- the constraint decides the art, not the other
way round.

She is NOT, at the time of writing, built from the renders sitting in
assets/sprites: those face left, put her in a maid's apron, came out with dark
hair instead of auburn-red, and read as 8-bit.  docs/ART_SPEC.md is normative
and they fail it on four counts, so this file emits a canon-correct PLACEHOLDER
and says so on every build.  A ROM with a placeholder in it is honest; a ROM
with the wrong character in it is not.

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
# CANON, from docs/ART_SPEC.md: auburn-RED hair, BROWN Victorian dress, no
# apron and no maid gear.  The first batch of generated art drifted on every
# one of those, so the palette itself now has no apron white and no blue-grey
# dress to drift into -- there is nowhere for a maid outfit to live.
OBJPAL = [
    (0, 0, 0),          # 0  transparent (never written)
    (16, 16, 24),       # 1  outline
    (248, 208, 176),    # 2  skin
    (200, 152, 120),    # 3  skin shadow
    (216, 104, 56),     # 4  hair, auburn-red light
    (168, 68, 32),      # 5  hair, auburn-red mid
    (104, 40, 20),      # 6  hair, auburn-red dark
    (164, 120, 76),     # 7  dress brown light
    (124, 84, 52),      # 8  dress brown mid
    (80, 52, 32),       # 9  dress brown dark
    (255, 232, 120),    # 10 gold light
    (232, 176, 40),     # 11 gold mid
    (152, 96, 8),       # 12 gold dark
    (224, 48, 48),      # 13 nabla red
    (128, 16, 16),      # 14 nabla red dark
    (248, 248, 248),    # 15 white -- collar, cuffs, the nabla's spikes
]

# Elya may only use these: snapping her to the gold or the nabla red would
# make her flicker between palette neighbours frame to frame.
ELYA_INK = [1, 2, 3, 4, 5, 6, 7, 8, 9, 15]

# ---------------------------------------------------------------------------
# The corrected generations are not here yet.  Building the ones that ARE here
# is not an option: they face left, they are wearing a maid's apron, the hair
# came out dark instead of auburn-red and they read as 8-bit.  A ROM with a
# placeholder in it is honest; a ROM with the wrong character in it is not.
#
# When corrected art lands, drop it in as assets/sprites/elya_canon_run.png and
# assets/sprites/elya_canon_idle.png and this file will use it instead --
# CANON_RUN existing is the whole switch.
# ---------------------------------------------------------------------------
CANON_RUN = 'elya_canon_run.png'
CANON_IDLE = 'elya_canon_idle.png'


# The figure below is hand-placed at native size, span by span, in the same
# idiom as the @ block and the nabla further down.  That is not a stylistic
# choice: ART_SPEC says "hand-placed pixels at native size beat any downscale",
# and two rounds of generated art proved it the expensive way.  The first batch
# faced left in a maid's apron with dark hair; the geometry placeholder that
# replaced it was canon-correct but spent 16 of its pixels on skin, so it had
# no face and no legs and the run frames differed only in how the hem swayed.
#
# Numbers, because this is the kind of thing that drifts back:
#
#     placeholder   19/32 wide, 16 px of skin, run frames differ by 48 px
#     this          29/32 wide, 34 px of skin, run frames differ by 343 px
#
# art_spec_check() below is the gate, and it rejects all six placeholder frames.
SKIN_IX = {2, 3}
HAIR_IX = {4, 5, 6}
DRESS_IX = {7, 8, 9}
WHITE_IX = {15}


def _span(g, y, x0, s):
    """Place a run of palette digits at (x0, y).  '.' is transparent."""
    for i, c in enumerate(s):
        if 0 <= x0 + i < 32 and 0 <= y < 32 and c != '.':
            g[y][x0 + i] = HEXMAP[c]


def _outline(g):
    """One dark pixel around the silhouette, so she reads against any sky."""
    out = [r[:] for r in g]
    for y in range(32):
        for x in range(32):
            if g[y][x]:
                continue
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < 32 and 0 <= xx < 32 and g[yy][xx]:
                    out[y][x] = 1
                    break
    return out


def elya(lean=0, arm=0, swing=0, trail=0, hem=0, bob=0):
    """Elya, 32x32, facing RIGHT, canon per docs/ART_SPEC.md.

    lean   upper body forward, px      arm    front-arm phase
    swing  skirt swing, px             trail  hair trailing behind
    hem    hem lift (jump)             bob    whole-figure bob (idle)
    """
    g = [[0] * 32 for _ in range(32)]
    L, B = lean, bob
    # -- head.  Auburn-red hair over the crown and forehead; the face sits to
    #    the RIGHT of the hair mass, which is what makes the facing read.
    _span(g,  1 + B, 14 + L, '5555')
    _span(g,  2 + B, 12 + L, '66555445')
    _span(g,  3 + B, 11 + L, '6655554455')
    _span(g,  4 + B, 11 + L, '665555444555')
    _span(g,  5 + B, 10 + L, '66555544455')
    _span(g,  6 + B, 10 + L, '6655554');  _span(g,  6 + B, 17 + L, '222222')
    _span(g,  7 + B, 10 + L, '665555');   _span(g,  7 + B, 16 + L, '2122122')
    _span(g,  8 + B, 10 + L, '665555');   _span(g,  8 + B, 16 + L, '2222222')
    _span(g,  9 + B, 10 + L, '665555');   _span(g,  9 + B, 16 + L, '222332')
    _span(g, 10 + B, 10 + L, '6655554');  _span(g, 10 + B, 17 + L, '22222')
    _span(g, 11 + B, 10 + L, '66555544'); _span(g, 11 + B, 18 + L, '222')
    # -- the HIGH collar.  A narrow standing band at the throat.  A wide flat
    #    white collar across the shoulders is a maid's collar by another name,
    #    and the first attempt at this drew exactly that: the palette has no
    #    apron white to drift into, so the drift came back through the shape.
    _span(g, 12 + B, 17 + L, 'FFF')
    _span(g, 13 + B, 16 + L, '8FF8')
    # -- hair down the back, past the waist, tapering to a tail.  Attached at
    #    the nape and trailing only at the bottom; trailing the whole mass
    #    detaches it from the head and it reads as a floating braid.
    for y in range(12 + B, 27):
        t = (y - 12 - B) / max(1.0, 14 - B)
        wob = (0, 0, 1, 1, 0, -1)[y % 6]
        x0 = 10 + L - int(round(trail * t)) + wob
        wide = 5 if t < .25 else (4 if t < .55 else (3 if t < .85 else 2))
        _span(g, y, x0, ('6654' if y % 3 == 0 else '66554')[:wide])
    _span(g, 27, 10 + L - trail, '66')
    # -- bodice, long sleeves, waist
    _span(g, 14 + B, 13 + L, '887778888')
    _span(g, 15 + B, 13 + L, '8877888888')
    _span(g, 16 + B, 13 + L, '88778888888')
    _span(g, 17 + B, 14 + L, '887788888')
    _span(g, 18 + B, 14 + L, '88778888')
    _span(g, 19 + B, 15 + L, '887788')
    # -- front arm: sleeve to a white cuff, then the hand
    ax, ay = 22 + L, 15 + arm + B
    for i, (dx, s) in enumerate([(0, '88'), (0, '88'), (1, '88'),
                                 (1, '88'), (1, 'FF'), (1, '22')]):
        _span(g, ay + i, ax + dx, s)
    # -- skirt, floor length, swinging.  The light panel is the front seam;
    #    it is what stops the skirt reading as one flat brown trapezoid.
    for i, y in enumerate(range(20 + B, 32 - hem)):
        t = i / 10.0
        half = 3.5 + t * 8.5
        cx = 17 + L + int(round(swing * t * 2))
        l, r = int(round(cx - half)), int(round(cx + half))
        row = []
        for x in range(l, r + 1):
            d = (x - l) / max(1, (r - l))
            row.append('7' if .20 < d < .40 else ('9' if d > .78 else '8'))
        _span(g, y, l, ''.join(row))
    return _outline(g)


# The six poses the sheet needs.  Run is two frames, jump one, idle three --
# ART_SPEC's frame budget, and idle1/idle2 are the foot tap that loops while
# she waits on you.
ELYA_POSES = [
    dict(lean=0, arm=0,  swing=0,  trail=0),            # run0   contact
    dict(lean=1, arm=2,  swing=2,  trail=3),            # run1   passing
    dict(lean=1, arm=-1, swing=1,  trail=4, hem=2),     # jump   skirt lifts
    dict(lean=0, arm=0,  swing=0,  trail=0),            # idle0  neutral
    dict(lean=0, arm=0,  swing=1,  trail=0, bob=1),     # idle1  tap down
    dict(lean=0, arm=1,  swing=-1, trail=1),            # idle2  tap up
]


def art_spec_check(g):
    """docs/ART_SPEC.md, as numbers.  Returns the list of failures.

    This exists because canon drift is not something a palette check or a tile
    count catches -- the palette was already scrubbed of apron white and the
    maid's collar came back anyway, as a shape.  Every threshold here is one
    of the four counts the first sprite batch drifted on, plus 'fill the cell'.
    """
    from collections import Counter
    xs = [x for y in range(32) for x in range(32) if g[y][x]]
    if not xs:
        return ['empty']
    c = Counter(v for row in g for v in row if v)
    w = max(xs) - min(xs) + 1
    fill = len(xs) * 100 // 1024
    skin = sum(c.get(v, 0) for v in SKIN_IX)
    white = sum(c.get(v, 0) for v in WHITE_IX)
    hair = sum(c.get(v, 0) for v in HAIR_IX)
    dress = sum(c.get(v, 0) for v in DRESS_IX)
    sx = [x for y in range(32) for x in range(32) if g[y][x] in SKIN_IX]
    hx = [x for y in range(32) for x in range(32) if g[y][x] in HAIR_IX]
    bad = []
    if w < 24:
        bad.append('fills only %d/32 wide' % w)
    if fill < 45:
        bad.append('fill %d%%' % fill)
    if skin < 28:
        bad.append('skin %d px -- no room for a face' % skin)
    if white > 12:
        bad.append('white %d px -- that is an apron/maid collar' % white)
    if hair < dress // 3:
        bad.append('hair %d px vs dress %d -- not long hair' % (hair, dress))
    if not (sx and hx and sum(sx) / len(sx) > sum(hx) / len(hx)):
        bad.append('faces LEFT')
    return bad

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
...F...F...F....
..FFF.FFF.FFF...
EEEEEEEEEEEEEEEE
EDDDDDDDDDDDDDDE
.EDDFFFDDFFFDDE.
.EDDF1FDDF1FDDE.
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
          'F': 15}


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


NAMES32_ = ['run0', 'run1', 'jump', 'idle0', 'idle1', 'idle2']


def main(out_prefix='assets/obj'):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sp = os.path.join(root, 'assets', 'sprites')

    # -- Elya.  The tap sheet holds four standing poses across 1024 px; the run
    #    sheet holds one moving pose.  Backgrounds differ between the two, and
    #    the run sheet has a black border the crop must stay inside of.
    canon_run = os.path.join(sp, CANON_RUN)
    canon_idle = os.path.join(sp, CANON_IDLE)

    # The hand-authored figure is the default, not the fallback.  It is drawn
    # at native size in this file; see the note above elya().
    drawn = [elya(**p) for p in ELYA_POSES]

    # Generated art may still override it -- but only if it PASSES the gate.
    # Keying on "the file exists" is what let a maid's apron into the tree the
    # first time; the filename never knew what was in the picture.
    elya_frames, source = drawn, 'hand-authored (tools/mkart.py)'
    if os.path.exists(canon_run):
        BG = Image.open(canon_run).convert('RGB').getpixel((0, 0))
        r0 = pixelise(canon_run, (0, 0) + Image.open(canon_run).size, (32, 32), BG)
        r1 = [[0] * 32] + [row[:] for row in r0[:-1]]
        jp = [[0] * 32, [0] * 32] + [row[:] for row in r0[:-2]]
        src = canon_idle if os.path.exists(canon_idle) else canon_run
        BG2 = Image.open(src).convert('RGB').getpixel((0, 0))
        w, h = Image.open(src).size
        n = 3 if os.path.exists(canon_idle) else 1
        idle = [pixelise(src, (i * w // n, 0, (i + 1) * w // n, h), (32, 32), BG2)
                for i in range(n)]
        while len(idle) < 3:
            idle.append([row[:] for row in idle[-1]])
        cand = [r0, r1, jp] + idle
        fails = [(NAMES32_[i], b) for i, g in enumerate(cand)
                 for b in [art_spec_check(g)] if b]
        if fails:
            print("elya    : %s is present but FAILS docs/ART_SPEC.md --" % CANON_RUN)
            for nm, b in fails:
                print("          %-6s %s" % (nm, '; '.join(b)))
            print("          keeping the hand-authored figure.")
        else:
            elya_frames, source = cand, CANON_RUN

    # The gate runs on whatever is about to be baked, every build.
    bad = [(NAMES32_[i], b) for i, g in enumerate(elya_frames)
           for b in [art_spec_check(g)] if b]
    if bad:
        for nm, b in bad:
            print("elya    : ART_SPEC FAIL %-6s %s" % (nm, '; '.join(b)))
        raise SystemExit("mkart: Elya fails docs/ART_SPEC.md -- refusing to bake it")
    print("elya    : %s, ART_SPEC pass (6 poses)" % source)

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
    rows32 = (len(elya_frames) + 3) // 4
    h = rows32 * 32 + ((len(objs16) * 16 + SHEET_W - 1) // SHEET_W) * 16
    sheet = [[0] * SHEET_W for _ in range(h)]

    NAMES32 = ['run0', 'run1', 'jump', 'idle0', 'idle1', 'idle2']
    NAMES16 = ['blk_rest', 'blk_hit', 'coin0', 'coin1', 'coin2', 'coin3',
               'nabla0', 'nabla1']
    index = {}
    for i, g in enumerate(elya_frames):
        ox, oy = (i % 4) * 32, (i // 4) * 32
        for y in range(32):
            for x in range(32):
                sheet[oy + y][ox + x] = g[y][x]
        index['elya_' + NAMES32[i]] = (oy // 8) * 16 + ox // 8
    base16 = rows32 * 32
    for i, g in enumerate(objs16):
        ox, oy = (i % 8) * 16, base16 + (i // 8) * 16
        for y in range(16):
            for x in range(16):
                sheet[oy + y][ox + x] = g[y][x]
        index[NAMES16[i]] = (oy // 8) * 16 + ox // 8

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
