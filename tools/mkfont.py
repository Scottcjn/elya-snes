#!/usr/bin/env python3
"""mkfont.py -- an original 5x7 bitmap font, emitted as SNES 2bpp CHR.

Why hand-authored rather than rendered from a system TTF: at 7 pixels tall a
rasteriser produces antialiased grey, and a 2bpp target has three inks.  Every
grey pixel then has to be thresholded, and where the threshold lands decides
whether an 'e' has a hole in it.  Drawing the 7 rows directly settles that
question once, and it is unambiguously original work -- the repo ships to a
flash cartridge, so "it is probably fine" is not a licence.

Layout: ASCII $20..$7F, one glyph per 8x8 tile, tile index = char - $20.
The glyph occupies columns 0..4 and rows 0..6, which leaves a one-pixel gap on
the right and the bottom so adjacent characters never touch.

2bpp is two planes interleaved by row: 16 bytes a tile.  Ink 1 is the letter.
Ink 2 is a one-pixel drop shadow (down-right), generated rather than drawn,
which is what keeps text legible over the clouds without a solid box behind it.
"""
import sys
import struct

# ---------------------------------------------------------------------------
# The font.  '#' is ink, '.' is paper.  5 wide, 7 tall, no exceptions -- the
# loader asserts it, because a mis-sized glyph shifts every row after it and
# the result looks like a corrupted tile rather than like a typo.
# ---------------------------------------------------------------------------
GLYPHS = {
' ': "..... ..... ..... ..... ..... ..... .....",
'!': "..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..",
'"': ".#.#. .#.#. ..... ..... ..... ..... .....",
'#': ".#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.",
'$': "..#.. .#### #.#.. .###. ..#.# ####. ..#..",
'%': "##..# ##..# ...#. ..#.. .#... #..## #..##",
'&': ".##.. #..#. .##.. .##.# #..## #..#. .##.#",
"'": "..#.. ..#.. ..... ..... ..... ..... .....",
'(': "...#. ..#.. .#... .#... .#... ..#.. ...#.",
')': ".#... ..#.. ...#. ...#. ...#. ..#.. .#...",
'*': "..... #.#.# .###. ..#.. .###. #.#.# .....",
'+': "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
',': "..... ..... ..... ..... ..##. ..#.. .#...",
'-': "..... ..... ..... ##### ..... ..... .....",
'.': "..... ..... ..... ..... ..... ..##. ..##.",
'/': "....# ...#. ...#. ..#.. .#... .#... #....",
'0': ".###. #...# #..## #.#.# ##..# #...# .###.",
'1': "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
'2': ".###. #...# ....# ...#. ..#.. .#... #####",
'3': "####. ....# ....# .###. ....# ....# ####.",
'4': "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
'5': "##### #.... ####. ....# ....# #...# .###.",
'6': "..##. .#... #.... ####. #...# #...# .###.",
'7': "##### ....# ...#. ..#.. ..#.. .#... .#...",
'8': ".###. #...# #...# .###. #...# #...# .###.",
'9': ".###. #...# #...# .#### ....# ...#. .##..",
':': "..... ..##. ..##. ..... ..##. ..##. .....",
';': "..... ..##. ..##. ..... ..##. ..#.. .#...",
'<': "...#. ..#.. .#... #.... .#... ..#.. ...#.",
'=': "..... ..... ##### ..... ##### ..... .....",
'>': ".#... ..#.. ...#. ....# ...#. ..#.. .#...",
'?': ".###. #...# ....# ...#. ..#.. ..... ..#..",
'@': ".###. #...# #.### #.#.# #.### #.... .###.",
'A': ".###. #...# #...# ##### #...# #...# #...#",
'B': "####. #...# #...# ####. #...# #...# ####.",
'C': ".###. #...# #.... #.... #.... #...# .###.",
'D': "###.. #..#. #...# #...# #...# #..#. ###..",
'E': "##### #.... #.... ####. #.... #.... #####",
'F': "##### #.... #.... ####. #.... #.... #....",
'G': ".###. #...# #.... #.### #...# #...# .###.",
'H': "#...# #...# #...# ##### #...# #...# #...#",
'I': ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
'J': "....# ....# ....# ....# #...# #...# .###.",
'K': "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
'L': "#.... #.... #.... #.... #.... #.... #####",
'M': "#...# ##.## #.#.# #.#.# #...# #...# #...#",
'N': "#...# ##..# #.#.# #.#.# #..## #...# #...#",
'O': ".###. #...# #...# #...# #...# #...# .###.",
'P': "####. #...# #...# ####. #.... #.... #....",
'Q': ".###. #...# #...# #...# #.#.# #..#. .##.#",
'R': "####. #...# #...# ####. #.#.. #..#. #...#",
'S': ".###. #...# #.... .###. ....# #...# .###.",
'T': "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
'U': "#...# #...# #...# #...# #...# #...# .###.",
'V': "#...# #...# #...# #...# #...# .#.#. ..#..",
'W': "#...# #...# #...# #.#.# #.#.# ##.## #...#",
'X': "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
'Y': "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
'Z': "##### ....# ...#. ..#.. .#... #.... #####",
'[': ".###. .#... .#... .#... .#... .#... .###.",
'\\':"#.... .#... .#... ..#.. ...#. ...#. ....#",
']': ".###. ...#. ...#. ...#. ...#. ...#. .###.",
'^': "..#.. .#.#. #...# ..... ..... ..... .....",
'_': "..... ..... ..... ..... ..... ..... #####",
'`': ".#... ..#.. ..... ..... ..... ..... .....",
'a': "..... ..... .###. ....# .#### #...# .####",
'b': "#.... #.... ####. #...# #...# #...# ####.",
'c': "..... ..... .###. #.... #.... #...# .###.",
'd': "....# ....# .#### #...# #...# #...# .####",
'e': "..... ..... .###. #...# ##### #.... .###.",
'f': "..##. .#..# .#... ###.. .#... .#... .#...",
'g': "..... ..... .#### #...# .#### ....# .###.",
'h': "#.... #.... ####. #...# #...# #...# #...#",
'i': "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.",
'j': "...#. ..... ...#. ...#. ...#. #..#. .##..",
'k': "#.... #.... #..#. #.#.. ##... #.#.. #..#.",
'l': ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
'm': "..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#",
'n': "..... ..... ####. #...# #...# #...# #...#",
'o': "..... ..... .###. #...# #...# #...# .###.",
'p': "..... ..... ####. #...# ####. #.... #....",
'q': "..... ..... .#### #...# .#### ....# ....#",
'r': "..... ..... #.##. ##..# #.... #.... #....",
's': "..... ..... .#### #.... .###. ....# ####.",
't': ".#... .#... ###.. .#... .#... .#..# ..##.",
'u': "..... ..... #...# #...# #...# #..## .##.#",
'v': "..... ..... #...# #...# #...# .#.#. ..#..",
'w': "..... ..... #...# #.#.# #.#.# #.#.# .#.#.",
'x': "..... ..... #...# .#.#. ..#.. .#.#. #...#",
'y': "..... ..... #...# #...# .#### ....# .###.",
'z': "..... ..... ##### ...#. ..#.. .#... #####",
'{': "...#. ..#.. ..#.. .#... ..#.. ..#.. ...#.",
'|': "..#.. ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
'}': ".#... ..#.. ..#.. ...#. ..#.. ..#.. .#...",
'~': "..... ..... .#..# #.#.# #..#. ..... .....",
'\x7f':"##### #...# #...# #...# #...# #...# #####",
}

CELL = 8
GW, GH = 5, 7


def bitmap(ch, page=0):
    """The glyph as an 8x8 grid of ink indices.

    Page 0 is for text floating over the sky: paper is index 0, which a 2bpp
    tile leaves transparent, and the letter carries a drop shadow so it stays
    legible against a cloud.

    Page 1 is for text inside the dialogue box: paper is index 2, the box fill,
    so the tile is opaque.  Two pages exist because a 2bpp tile has exactly one
    transparent index and it cannot be both the sky and the box -- and putting
    the box on another layer instead would mean the clouds had to stop.
    """
    rows = GLYPHS[ch].split()
    assert len(rows) == GH, "%r has %d rows, want %d" % (ch, len(rows), GH)
    for r in rows:
        assert len(r) == GW, "%r row %r is %d wide, want %d" % (ch, r, len(r), GW)
    paper = 0 if page == 0 else 2
    g = [[paper] * CELL for _ in range(CELL)]
    for y in range(GH):
        for x in range(GW):
            if rows[y][x] == '#':
                g[y][x] = 1
    if page == 0:
        for y in range(CELL - 1, 0, -1):
            for x in range(CELL - 1, 0, -1):
                if g[y][x] == 0 and g[y - 1][x - 1] == 1:
                    g[y][x] = 2
    return g


# The dialogue box, as four tiles plus the tilemap's free H/V flips: a corner,
# a top edge, a left edge and the plain fill (which is page 1's space).
BOXART = {
'corner': """
33333333
32222222
32222222
32222222
32222222
32222222
32222222
32222222
""",
'top': """
33333333
22222222
22222222
22222222
22222222
22222222
22222222
22222222
""",
'left': """
32222222
32222222
32222222
32222222
32222222
32222222
32222222
32222222
""",
'fill': """
22222222
22222222
22222222
22222222
22222222
22222222
22222222
22222222
""",
}
BOXORDER = ['corner', 'top', 'left', 'fill']


def encode2bpp(g):
    """SNES 2bpp: planes 0 and 1 interleaved per row, 16 bytes."""
    b = bytearray()
    for y in range(CELL):
        p0 = p1 = 0
        for x in range(CELL):
            v = g[y][x]
            p0 |= (v & 1) << (7 - x)
            p1 |= ((v >> 1) & 1) << (7 - x)
        b += bytes([p0, p1])
    return bytes(b)


def main(out_prefix):
    chars = [chr(c) for c in range(0x20, 0x80)]
    missing = [c for c in chars if c not in GLYPHS]
    if missing:
        print("missing glyphs: %r" % missing, file=sys.stderr)
        return 1
    chr_data = bytearray()
    for page in (0, 1):
        for c in chars:
            chr_data += encode2bpp(bitmap(c, page))
    for name in BOXORDER:
        rows_ = BOXART[name].strip('\n').split('\n')
        assert len(rows_) == CELL
        g = [[int(v) for v in r] for r in rows_]
        chr_data += encode2bpp(g)
    open(out_prefix + '.chr', 'wb').write(chr_data)

    with open(out_prefix + '.inc', 'w') as f:
        f.write("; GENERATED by tools/mkfont.py -- do not edit\n")
        f.write("FONT_PAGE0  = 0\n")          # transparent paper
        f.write("FONT_PAGE1  = %d\n" % len(chars))   # box-fill paper
        for i, n in enumerate(BOXORDER):
            f.write("BOXT_%-6s = %d\n" % (n.upper(), 2 * len(chars) + i))
        f.write("FONT_TILES  = %d\n" % (len(chr_data) // 16))

    # preview: both pages plus the box tiles, at 4x so a bad row is obvious
    from PIL import Image
    ntiles = len(chr_data) // 16
    cols, rows = 16, (ntiles + 15) // 16
    im = Image.new('RGB', (cols * CELL, rows * CELL), (24, 24, 40))
    px = im.load()
    ink = {0: None, 1: (248, 248, 248), 2: (24, 24, 56), 3: (120, 128, 208)}
    cells = ([bitmap(c, 0) for c in chars] + [bitmap(c, 1) for c in chars]
             + [[[int(v) for v in r]
                 for r in BOXART[n].strip('\n').split('\n')] for n in BOXORDER])
    for i, g in enumerate(cells):
        ox, oy = (i % cols) * CELL, (i // cols) * CELL
        for y in range(CELL):
            for x in range(CELL):
                c = ink[g[y][x]]
                if c:
                    px[ox + x, oy + y] = c
    im.resize((im.width * 4, im.height * 4), Image.NEAREST).save(
        out_prefix + '_preview.png')
    print("font: %d glyphs x 2 pages + %d box tiles = %d tiles, %d bytes 2bpp"
          % (len(chars), len(BOXORDER), ntiles, len(chr_data)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'assets/font'))
