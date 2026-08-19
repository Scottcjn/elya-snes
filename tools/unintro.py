#!/usr/bin/env python3
"""unintro.py -- decode an ESV1 stream back into pictures.

This is the preview, and it is also the only check that the format is
self-consistent: it replays the stream the way the ROM will, keeping a slot
table and a tilemap and applying deltas, so a frame it gets wrong is a frame
the cartridge would get wrong.  Rendering from the encoder's own intermediate
state would prove nothing -- it would only show that the encoder agrees with
itself.

Usage:
  unintro.py assets/intro.bin [--out out/intro/preview] [--mp4 out/intro/preview.mp4]
             [--scale 3] [--frames 0,30,60,90]
"""
import argparse
import os
import struct
import subprocess
import sys

from PIL import Image


def unbgr555(w):
    r = (w & 31) << 3
    g = ((w >> 5) & 31) << 3
    b = ((w >> 10) & 31) << 3
    return (r | r >> 5, g | g >> 5, b | b >> 5)


def decode4bpp(raw):
    """Inverse of mkintro.encode4bpp: SNES planar 4bpp -> 8x8 indices."""
    t = [[0] * 8 for _ in range(8)]
    for half, lo in ((0, 0), (16, 2)):
        for y in range(8):
            pa, pb = raw[half + y * 2], raw[half + y * 2 + 1]
            for x in range(8):
                bit = 7 - x
                t[y][x] |= (((pa >> bit) & 1) << lo) | (((pb >> bit) & 1) << (lo + 1))
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('blob')
    ap.add_argument('--out', default='out/intro/preview')
    ap.add_argument('--mp4', default=None)
    ap.add_argument('--scale', type=int, default=3)
    ap.add_argument('--frames', default=None,
                    help='comma-separated frame numbers to write as PNG; '
                         'default is every frame when --mp4 is given, else four')
    a = ap.parse_args()

    d = open(a.blob, 'rb').read()
    magic, tw, th, nfr, fps, npal, peak = struct.unpack('<4sHHHHHH', d[:16])
    if magic != b'ESV1':
        sys.exit('not an ESV1 stream: %r' % magic)
    p = 16
    pals = []
    for _ in range(npal):
        row = []
        for _ in range(16):
            row.append(unbgr555(struct.unpack_from('<H', d, p)[0]))
            p += 2
        pals.append(row)
    print('ESV1  %dx%d tiles (%dx%d px)  %d frames @ %d fps  %d palettes  peak %d tiles'
          % (tw, th, tw * 8, th * 8, nfr, fps, npal, peak))

    os.makedirs(a.out, exist_ok=True)
    want = None
    if a.frames:
        want = set(int(x) for x in a.frames.split(','))
    elif not a.mp4:
        want = {0, nfr // 3, 2 * nfr // 3, nfr - 1}

    slots = {}                       # slot -> 8x8 indices
    cells = [0] * (tw * th)          # tilemap entries
    W, H = tw * 8, th * 8
    S = a.scale
    written = 0
    for f in range(nfr):
        n_t = struct.unpack_from('<H', d, p)[0]; p += 2
        for _ in range(n_t):
            slot = struct.unpack_from('<H', d, p)[0]; p += 2
            slots[slot] = decode4bpp(d[p:p + 32]); p += 32
        n_m = struct.unpack_from('<H', d, p)[0]; p += 2
        for _ in range(n_m):
            cell, entry = struct.unpack_from('<HH', d, p); p += 4
            cells[cell] = entry
        if want is not None and f not in want and not a.mp4:
            continue
        im = Image.new('RGB', (W, H))
        px = im.load()
        for ci, entry in enumerate(cells):
            slot = entry & 0x3FF
            k = (entry >> 10) & 7
            t = slots.get(slot)
            if t is None:
                continue
            ox, oy = (ci % tw) * 8, (ci // tw) * 8
            for y in range(8):
                for x in range(8):
                    px[ox + x, oy + y] = pals[k][t[y][x]]
        if a.mp4 or (want and f in want):
            im.resize((W * S, H * S), Image.NEAREST).save(
                os.path.join(a.out, 'f%05d.png' % f))
            written += 1
    if p != len(d):
        print('WARNING: %d bytes left unread of %d -- the stream and the '
              'decoder disagree' % (len(d) - p, len(d)))
    else:
        print('stream fully consumed: %d bytes, decoder and format agree' % len(d))
    print('wrote %d PNG(s) to %s' % (written, a.out))
    if a.mp4:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(fps),
                        '-pattern_type', 'glob', '-i',
                        os.path.join(a.out, 'f*.png'),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', a.mp4],
                       check=True)
        print('wrote %s' % a.mp4)
    return 0


if __name__ == '__main__':
    sys.exit(main())
