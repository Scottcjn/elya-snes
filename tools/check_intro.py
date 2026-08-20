#!/usr/bin/env python3
"""Did the 65816 play the stream the encoder wrote?

The player cannot be watched from this host, so the receipt is END STATE: the
INTROQA build reads the BG1 tilemap, every tile slot and CGRAM back out
through the PPU into SRAM after the last frame, and this compares all of it
against an independent replay of assets/intro.bin -- the same decode
tools/unintro.py does, slot table and deltas, so a byte the ROM got wrong is a
byte this notices.  What end state cannot show is MOTION: pacing is checked
only as a vblank count, and nothing here says frame 60 looked right at second
five.  That is what a camera on real hardware is for.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SR_IMAP, SR_ICHR, SR_ICGR, SR_ISTA = 0x0800, 0x1000, 0x2900, 0x2B00
IBLANK = 192
V_BG1MAP = 0x6000  # unused here, the map is read relative


def main():
    ram = open(sys.argv[1] if len(sys.argv) > 1 else "out/introqa.ram",
               "rb").read()
    d = open(os.path.join(ROOT, "assets", "intro.bin"), "rb").read()
    magic, tw, th, nfr, fps, npal, peak = struct.unpack("<4sHHHHHH", d[:16])
    assert magic == b"ESV1"
    p = 16
    pals = []
    for _ in range(npal):
        row = [struct.unpack_from("<H", d, p + 2 * i)[0] for i in range(16)]
        pals.append(row)
        p += 32

    # replay every frame
    BANK = 32768
    slots, cells = {}, {}
    for _f in range(nfr):
        if struct.unpack_from("<H", d, p)[0] == 0xFFFF:
            p = ((p // BANK) + 1) * BANK
        n_t = struct.unpack_from("<H", d, p)[0]; p += 2
        for _ in range(n_t):
            s = struct.unpack_from("<H", d, p)[0]; p += 2
            slots[s] = d[p:p + 32]; p += 32
        n_m = struct.unpack_from("<H", d, p)[0]; p += 2
        for _ in range(n_m):
            c, e = struct.unpack_from("<HH", d, p); p += 4
            cells[c] = e
    if p != len(d):
        print("FAIL: decoder consumed %d of %d bytes" % (p, len(d)))
        return 1

    fails = []

    # ---- the map: border cells blank, window cells the final entries -------
    got_map = [ram[SR_IMAP + 2 * i] | (ram[SR_IMAP + 2 * i + 1] << 8)
               for i in range(1024)]
    bad = 0
    for my in range(32):
        for mx in range(32):
            if 8 <= mx < 8 + tw and 8 <= my < 8 + th:
                want = cells.get((my - 8) * tw + (mx - 8), IBLANK)
            else:
                want = IBLANK
            if got_map[my * 32 + mx] != want:
                bad += 1
    if bad:
        fails.append("map: %d of 1024 entries differ" % bad)
    else:
        print("  ok    BG1 map: all 1024 entries (192 window + 832 border)")

    # ---- the tiles: every slot's 32 bytes, plus the zeroed blank -----------
    bad = badslots = 0
    for s in range(IBLANK + 1):
        want = bytes(32) if s == IBLANK else slots.get(s, bytes(32))
        got = ram[SR_ICHR + 32 * s:SR_ICHR + 32 * s + 32]
        n = sum(1 for a, b in zip(got, want) if a != b)
        if n:
            bad += n
            badslots += 1
    if bad:
        fails.append("tiles: %d bytes differ across %d slots" % (bad, badslots))
    else:
        print("  ok    CHR: all %d slots byte-identical (%d B), blank slot zero"
              % (IBLANK + 1, (IBLANK + 1) * 32))

    # ---- CGRAM: entries 0..127, bit 15 masked (it reads back as bus noise) -
    bad = 0
    for i in range(npal * 16):
        got = (ram[SR_ICGR + 2 * i] | (ram[SR_ICGR + 2 * i + 1] << 8)) & 0x7FFF
        if got != pals[i // 16][i % 16]:
            bad += 1
    if bad:
        fails.append("CGRAM: %d of %d entries differ" % (bad, npal * 16))
    else:
        print("  ok    CGRAM: all %d entries match the stream's palettes"
              % (npal * 16))

    # ---- pacing, as arithmetic: the player counts its own vblanks ----------
    ivbl = ram[SR_ISTA] | (ram[SR_ISTA + 1] << 8)
    ivpf = ram[SR_ISTA + 2] | (ram[SR_ISTA + 3] << 8)
    endbank = ram[SR_ISTA + 4]
    want_vbl = nfr * (60 // fps)
    print("  info  %d vblanks for %d frames (paced floor %d), %d/frame, end bank $%02X"
          % (ivbl, nfr, want_vbl, ivpf, endbank))
    if ivpf != 60 // fps:
        fails.append("vblanks/frame is %d, want %d" % (ivpf, 60 // fps))
    if not (want_vbl <= ivbl <= want_vbl + 8):
        fails.append("%d vblanks for %d frames -- pacing is off (floor %d)"
                     % (ivbl, nfr, want_vbl))

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: the player's end state equals the decoder's")
    return 0


if __name__ == "__main__":
    sys.exit(main())
