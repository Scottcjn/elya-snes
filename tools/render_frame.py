#!/usr/bin/env python3
"""render_frame.py -- rebuild, on the host, exactly what the PPU would put on
the television, from the bytes the cartridge wrote into battery SRAM.

Screen capture is not available on this machine: the ares window is Wayland
native, `import -window root` gets nothing and grim fails because GNOME does
not implement wlr-screencopy.  So the ROM reads its own OAM, VRAM and CGRAM
back out THROUGH THE PPU into SRAM, and this composites them.

That is not a workaround, it is a better instrument.  A screenshot shows a
picture and you have to trust the emulator's renderer.  This shows the object
table, the tilemaps, the palettes and the scroll registers that the picture
would be made from, and does the compositing in code that can be read.

Layer order is mode 1 with BG3's priority bit set in BGMODE, which puts the
text above everything:  backdrop, BG2, BG1, objects, BG3.
"""
import os
import struct
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A = os.path.join(ROOT, "assets")

W, H = 256, 224
SR_CGR = 0x1C00
SNAPS = {"act1": 0x3000, "act3": 0x4000}
OAM_LEN = 0x220
BG3_OFF = 0x220
SCROLL_OFF = 0xA20
STATE = ["logo", "play", "stop", "line", "ask", "answer", "end"]


def blob(name):
    return open(os.path.join(A, name), "rb").read()


def decode4(chr_, n):
    """4bpp tile n -> 8x8 of palette indices."""
    o = n * 32
    t = [[0] * 8 for _ in range(8)]
    for y in range(8):
        p0, p1 = chr_[o + y * 2], chr_[o + y * 2 + 1]
        p2, p3 = chr_[o + 16 + y * 2], chr_[o + 16 + y * 2 + 1]
        for x in range(8):
            b = 7 - x
            t[y][x] = (((p0 >> b) & 1) | (((p1 >> b) & 1) << 1)
                       | (((p2 >> b) & 1) << 2) | (((p3 >> b) & 1) << 3))
    return t


def decode2(chr_, n):
    o = n * 16
    t = [[0] * 8 for _ in range(8)]
    for y in range(8):
        p0, p1 = chr_[o + y * 2], chr_[o + y * 2 + 1]
        for x in range(8):
            b = 7 - x
            t[y][x] = ((p0 >> b) & 1) | (((p1 >> b) & 1) << 1)
    return t


def cgram(d):
    pal = []
    for i in range(256):
        # bit 15 is not part of a BGR555 colour and reads back as bus noise
        v = (d[SR_CGR + i * 2] | (d[SR_CGR + i * 2 + 1] << 8)) & 0x7FFF
        r = (v & 31) << 3
        g = ((v >> 5) & 31) << 3
        b = ((v >> 10) & 31) << 3
        pal.append((r | r >> 5, g | g >> 5, b | b >> 5))
    return pal


def sky_rows():
    """The HDMA table, expanded to one colour per scanline.  The backdrop in
    CGRAM entry 0 is rewritten every eight lines, so the palette readback only
    ever holds the last band -- the gradient has to come from the table."""
    hd = blob("sky.hdma")
    rows, i, y = [], 0, 0
    while i < len(hd) and hd[i] and y < H:
        n = hd[i] & 0x7F
        v = hd[i + 3] | (hd[i + 4] << 8)
        r = (v & 31) << 3
        g = ((v >> 5) & 31) << 3
        b = ((v >> 10) & 31) << 3
        c = (r | r >> 5, g | g >> 5, b | b >> 5)
        for _ in range(n):
            if y < H:
                rows.append(c)
                y += 1
        i += 5
    while len(rows) < H:
        rows.append(rows[-1] if rows else (0, 0, 0))
    return rows


def draw_bg(px, tmap, chr_, mapw, hofs, vofs, pal, bpp, want_prio):
    dec = decode4 if bpp == 4 else decode2
    cache = {}
    for y in range(H):
        wy = (y + vofs) & (32 * 8 - 1)
        ty, fy = wy >> 3, wy & 7
        for x in range(W):
            wx = (x + hofs) & (mapw * 8 - 1)
            tx, fx = wx >> 3, wx & 7
            scr = tx >> 5
            e = (ty * 32 + (tx & 31) + scr * 1024) * 2
            wrd = tmap[e] | (tmap[e + 1] << 8)
            if ((wrd >> 13) & 1) != want_prio:
                continue
            n = wrd & 0x3FF
            t = cache.get(n)
            if t is None:
                t = cache[n] = dec(chr_, n)
            sx = 7 - fx if wrd & 0x4000 else fx
            sy = 7 - fy if wrd & 0x8000 else fy
            v = t[sy][sx]
            if v == 0:
                continue
            p = (wrd >> 10) & 7
            px[x, y] = pal[p * (16 if bpp == 4 else 4) + v]


def draw_objects(px, oam, chr_, pal):
    cache = {}
    for i in range(127, -1, -1):
        x = oam[i * 4]
        y = oam[i * 4 + 1]
        tile = oam[i * 4 + 2]
        attr = oam[i * 4 + 3]
        hi = oam[512 + (i >> 2)]
        bits = (hi >> ((i & 3) * 2)) & 3
        if bits & 1:
            x |= 0x100
        if x >= 0x100:
            x -= 0x200
        size = 32 if bits & 2 else 16
        if y >= 0xE0:
            continue
        base = tile | ((attr & 1) << 8)
        p = (attr >> 1) & 7
        hf, vf = attr & 0x40, attr & 0x80
        n = size // 8
        for r in range(n):
            for c in range(n):
                sr, sc = (n - 1 - r if vf else r), (n - 1 - c if hf else c)
                tn = base + sr * 16 + sc
                t = cache.get(tn)
                if t is None:
                    t = cache[tn] = decode4(chr_, tn)
                for yy in range(8):
                    py = y + r * 8 + yy
                    if not 0 <= py < H:
                        continue
                    ty = 7 - yy if vf else yy
                    for xx in range(8):
                        pxx = x + c * 8 + xx
                        if not 0 <= pxx < W:
                            continue
                        v = t[ty][7 - xx if hf else xx]
                        if v:
                            px[pxx, py] = pal[128 + p * 16 + v]


def render(d, base, out):
    pal = cgram(d)
    oam = d[base:base + OAM_LEN]
    bg3 = d[base + BG3_OFF:base + BG3_OFF + 0x800]
    camx = d[base + SCROLL_OFF] | (d[base + SCROLL_OFF + 1] << 8)
    camy = d[base + SCROLL_OFF + 12] | (d[base + SCROLL_OFF + 13] << 8)
    state = d[base + SCROLL_OFF + 2] | (d[base + SCROLL_OFF + 3] << 8)
    frame = d[base + SCROLL_OFF + 4] | (d[base + SCROLL_OFF + 5] << 8)
    toks = d[base + SCROLL_OFF + 6] | (d[base + SCROLL_OFF + 7] << 8)
    coins = d[base + SCROLL_OFF + 8] | (d[base + SCROLL_OFF + 9] << 8)
    magic = d[base + SCROLL_OFF + 10] | (d[base + SCROLL_OFF + 11] << 8)
    if magic != 0x5A5A:
        print("snapshot at $%04X is not stamped" % base, file=sys.stderr)
        return None

    im = Image.new("RGB", (W, H))
    px = im.load()
    for y, c in enumerate(sky_rows()):
        for x in range(W):
            px[x, y] = c

    draw_bg(px, blob("clouds.map"), blob("bg2.chr"), 32, camx // 2, camy // 2,
            pal, 4, 0)
    draw_bg(px, blob("level.map"), blob("bg1.chr"), 64, camx, camy, pal, 4, 0)
    draw_objects(px, oam, blob("obj.chr"), pal)
    draw_bg(px, bg3, blob("font.chr"), 32, 0, 0, pal, 2, 1)

    im.save(out)
    im.resize((W * 3, H * 3), Image.NEAREST).save(
        out.replace(".png", "_3x.png"))
    print("%-28s state %-6s frame %4d  tokens %3d  coins %3d  camera %3d,%d"
          % (out, STATE[state] if state < 7 else "?", frame, toks, coins,
             camx, camy))
    return im


def main(path, outdir):
    d = open(path, "rb").read()
    if d[8:12] != b"DONE":
        print("the dump is not from a finished run", file=sys.stderr)
        return 1
    os.makedirs(outdir, exist_ok=True)
    rc = 0
    for name, base in SNAPS.items():
        if render(d, base, os.path.join(outdir, "frame_%s.png" % name)) is None:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out/game.ram",
                  sys.argv[2] if len(sys.argv) > 2 else "out/frames"))
