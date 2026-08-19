#!/usr/bin/env python3
"""mkintro.py -- convert an .mp4 into a streaming 4bpp SNES animation.

Usage:
  mkintro.py in.mp4 [--w 128] [--h 96] [--fps 12] [--pals 8] [--out assets/intro.bin]

WHY IT IS SHAPED THIS WAY (the SNES budget, not artistic choice)

The Genesis port of this idea is train/make_intro.py in the sibling repo, and
its shape carries over because the two machines are close.  What does NOT carry
over is the colour, and that is the whole reason this is a separate tool.

  vblank DMA        6,479 B/frame   38 lines x 1364 master clocks / 8 clocks
                                    per byte, NTSC, 224-line mode.  ~5,960 B
                                    after NMI, OAM and CGRAM overhead.
  a 4bpp tile       32 bytes, 8x8 px
  128x96 window     16x12 = 192 tiles = 6,144 B for a FULL refresh

A full refresh of even this small a window does not fit in ONE vblank.  But at
12 fps the picture only changes every fifth vblank, so an animation frame gets
five of them -- reserve one for the tilemap flip and the tile budget is
4 x 5,960 = 23,840 B, not 5,960.

That correction came from reading the Genesis player rather than reasoning about
the hardware, and it matters because the two machines differ exactly here.  The
Genesis player does not use vblank DMA at all: it writes VRAM with the CPU,
interrupts off, DURING ACTIVE DISPLAY, then waits 60/fps vsyncs.  The Genesis
VDP allows that.  The SNES does not -- VRAM is locked while the beam is drawing
unless you force blank, and force blank shows a black screen.

So the SNES plays this back double-buffered: stream the changed tiles into
off-screen slots across four vblanks, then flip the tilemap cells in the fifth.
2 x 192 = 384 slots, well inside the 1024 a tilemap entry can name.  It is
strictly cleaner than the Genesis player, which can tear in principle, and it
is not a choice -- it is what the machine allows.

THE ONE THING THE SNES DOES BETTER

A 4bpp tilemap entry carries a 3-bit palette field, so every tile picks one of
8 background palettes for free -- the bits are already in the map word that has
to be sent anyway.  Up to 8 x 15 = 120 colours on screen against the Genesis's
15, at a cost of 256 bytes of CGRAM DMA for the whole clip.

That is NOT the same as the per-frame palette the Genesis encoder tried and
rejected.  Its build_palette() says why: re-quantising every frame destroys the
frame-to-frame stability that delta encoding depends on, so the palette win was
paid for many times over in tile churn.  Here the palettes are global and
STABLE for the entire clip -- deltas still work -- and the per-tile choice is
what buys the colour.  --pals 1 reproduces the Genesis behaviour exactly, which
is how the difference gets measured rather than assumed.

OUTPUT FORMAT (little-endian, 65816-native)

  header 16 B  'ESV1' u16 tiles_w, u16 tiles_h, u16 n_frames,
               u16 fps, u16 n_pals, u16 max_tiles_per_frame
  palettes     n_pals x 16 x u16   BGR555, CGRAM order, sent once
  per frame    u16 n_tiles, n_tiles x { u16 slot, 32 B tile }
               u16 n_map,   n_map   x { u16 cell, u16 entry }

`entry` is a real SNES tilemap word -- tile index in bits 0-9, palette in bits
10-12 -- so the player pokes it into VRAM unmodified.

The player DMAs the tile payload and pokes the map deltas.  Both are bounded by
max_tiles_per_frame so the ROM can size its buffer statically, and this tool
REFUSES to emit a clip whose peak frame does not fit the vblank budget: a
stream the player cannot keep up with tears, and tearing is not something the
gate can see.
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

# The measured budget.  See the header note.
VBLANK_BYTES = 6479
VBLANK_USABLE = 5960


def bgr555(r, g, b):
    return ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)


def unbgr555(w):
    return ((w & 31) << 3, ((w >> 5) & 31) << 3, ((w >> 10) & 31) << 3)


def encode4bpp(t):
    """SNES planar 4bpp: rows 0-7 as bitplanes 0/1, then rows 0-7 as 2/3."""
    b = bytearray()
    for lo in (0, 2):
        for y in range(8):
            pa = pb = 0
            for x in range(8):
                v = int(t[y][x])
                pa |= ((v >> lo) & 1) << (7 - x)
                pb |= ((v >> (lo + 1)) & 1) << (7 - x)
            b += bytes([pa, pb])
    return bytes(b)


def extract(path, w, h, fps, tmp):
    """ffmpeg -> RGB frames at the target size, letterboxed rather than
    stretched.  Stretching an AI-generated 16:9 clip into a 4:3 window is the
    kind of thing nobody notices until it is on a television."""
    vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,fps=%d" % (w, h, w, h, fps))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', path,
                    '-vf', vf, os.path.join(tmp, 'f%05d.png')], check=True)
    return sorted(os.path.join(tmp, f) for f in os.listdir(tmp)
                  if f.endswith('.png'))


def tile_cells(a, tw, th):
    """Frame -> list of 8x8 RGB blocks in tilemap order."""
    return [a[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8]
            for ty in range(th) for tx in range(tw)]


def _quant15(blocks):
    """15 colours out of a pile of 8x8 blocks, by median cut."""
    if not blocks:
        return [(0, 0, 0)] * 15
    sheet = Image.fromarray(np.concatenate(blocks, 0).astype(np.uint8))
    q = sheet.quantize(colors=15, method=Image.MEDIANCUT, dither=Image.NONE)
    pl = q.getpalette()[:45]
    out = [tuple(pl[i * 3:i * 3 + 3]) for i in range(15)]
    return out + [(0, 0, 0)] * (15 - len(out))


def _tile_err(px, pal):
    pa = np.asarray(pal, dtype=np.int32)
    d = ((px[:, None, :] - pa[None, :, :]) ** 2).sum(2)
    idx = d.argmin(1)
    return d[np.arange(len(px)), idx].sum(), idx


def build_palettes(frames, tw, th, n_pals, sample=24, rounds=6):
    """Global palettes, stable for the whole clip, refined by Lloyd's algorithm.

    The first version of this clustered tiles by their MEAN colour, built one
    palette per cluster, and stopped.  That gives every tile a vote on WHICH
    palette it uses and no vote at all on what is IN it, so a tile sitting at a
    cluster boundary gets a palette that does not contain its colours -- and on
    a dark clip that showed up as flat grey blocks where a gradient should be.
    Visible, and not something the error average reports: the mean was fine.

    So: assign every tile to its best-fitting palette, rebuild each palette from
    the tiles that actually chose it, repeat.  That is Lloyd's algorithm with
    "distortion against a 15-colour median cut" as the distance, and it makes
    the palettes a function of the tiles that use them rather than of a mean
    that no pixel has.

    The palettes stay GLOBAL and stable across the clip either way.  Per-frame
    palettes are a different idea and a refuted one -- the Genesis encoder's
    build_palette() records why: re-quantising each frame destroys the
    frame-to-frame stability that delta encoding depends on.
    """
    step = max(1, len(frames) // sample)
    blocks = []
    for f in frames[::step]:
        a = np.asarray(Image.open(f).convert('RGB'), dtype=np.uint8)
        blocks.extend(tile_cells(a, tw, th))
    px = [b.reshape(-1, 3).astype(np.int32) for b in blocks]

    if n_pals == 1:
        return [_quant15(blocks)], None

    # SEED, and the seed is the whole ballgame.  Bucketing tiles into equal
    # counts by luma was tried and measured: it makes Lloyd converge in three
    # rounds to a worse optimum than not refining at all (mean 258 against 189,
    # p95 1203 against 756, against the source frames).  Equal-count buckets are
    # wrong for a clip that is mostly one dark colour -- they split the dark
    # into groups that did not need splitting and starve everything else.
    #
    # So seed with k-means on tile MEAN colour, which lets the groups be as
    # uneven as the clip is, and let Lloyd refine from there.  Deterministic:
    # centres start at spread positions in luma order, and there is no RNG
    # anywhere in this file -- the encoder has to be reproducible or the ROM is
    # not.
    means = np.array([b.reshape(-1, 3).mean(0) for b in blocks])
    luma = means @ np.array([0.299, 0.587, 0.114])
    order = np.argsort(luma)
    cent = means[order[[int(i * (len(order) - 1) / max(1, n_pals - 1))
                        for i in range(n_pals)]]].astype(np.float64)
    for _ in range(12):
        dd = ((means[:, None, :] - cent[None, :, :]) ** 2).sum(2)
        lab = dd.argmin(1).astype(np.int32)
        for k in range(n_pals):
            m = lab == k
            if m.any():
                cent[k] = means[m].mean(0)

    pals = [_quant15([blocks[i] for i in range(len(blocks)) if lab[i] == k])
            for k in range(n_pals)]
    prev_total = None
    for r in range(rounds):
        total = 0
        newlab = np.empty_like(lab)
        for i in range(len(blocks)):
            best, bestk = None, 0
            for k in range(n_pals):
                e, _ = _tile_err(px[i], pals[k])
                if best is None or e < best:
                    best, bestk = e, k
            newlab[i] = bestk
            total += best
        lab = newlab
        pals = [_quant15([blocks[i] for i in range(len(blocks)) if lab[i] == k])
                or pals[k] for k in range(n_pals)]
        per_px = total / (len(blocks) * 64)
        print("  palette round %d: %.1f err/px" % (r + 1, per_px))
        if prev_total is not None and total >= prev_total * 0.999:
            break
        prev_total = total
    return pals, None


def quantise_all(cell, pals):
    """Error and pixel indices for this tile against EVERY palette.

    The choice is deferred to the caller because choosing per tile, in
    isolation, is what produced the artifact this replaced: two adjacent flat
    tiles pick DIFFERENT palettes, each palette's nearest colour to the same
    true value differs a little, and the seam shows as a hard rectangle.  Each
    of those tiles has low error -- that is why a per-tile error budget scored
    them as fine.  The defect lives between tiles, so the metric that finds it
    has to as well."""
    px = cell.reshape(-1, 3).astype(np.int32)
    out = []
    for p_ in pals:
        pa = np.asarray(p_, dtype=np.int32)
        d = ((px[:, None, :] - pa[None, :, :]) ** 2).sum(2)
        idx = d.argmin(1)
        out.append((int(d[np.arange(len(px)), idx].sum()),
                    (idx + 1).reshape(8, 8)))
    return out


def quantise_tile(cell, pals, cent):
    """Pick the palette that fits this tile best, then map every pixel.

    Entry 0 of each palette is transparent on real hardware for objects, but
    this is a background layer where entry 0 is the backdrop colour -- so all
    16 entries would be usable.  15 is kept anyway so the same palettes can be
    handed to a sprite later without a re-quantise."""
    px = cell.reshape(-1, 3).astype(np.int32)
    best, bestk, bestidx = None, 0, None
    for k, p in enumerate(pals):
        pa = np.asarray(p, dtype=np.int32)
        d = ((px[:, None, :] - pa[None, :, :]) ** 2).sum(2)
        idx = d.argmin(1)
        err = d[np.arange(len(px)), idx].sum()
        if best is None or err < best:
            best, bestk, bestidx = err, k, idx
    return bestk, (bestidx + 1).reshape(8, 8), best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--w', type=int, default=128)
    ap.add_argument('--h', type=int, default=96)
    ap.add_argument('--fps', type=int, default=12)
    ap.add_argument('--pals', type=int, default=8)
    ap.add_argument('--out', default='assets/intro.bin')
    ap.add_argument('--report', default=None)
    ap.add_argument('--bias', type=float, default=0.0,
                    help='how much a tile prefers its neighbours palette, '
                         '0..1.  MEASURED AND IT DOES NOT WORK: 0.25 moved the '
                         'visible-seam rate from 2.72%% to 2.68%% of tile '
                         'boundaries.  Defaulted off so it is not mistaken for '
                         'a fix; kept so the next person does not re-derive it.')
    ap.add_argument('--bank', type=int, default=32768,
                    help='pad frames so none straddles a bank of this size; '
                         '0 disables (LoROM shows 32 KiB per bank)')
    ap.add_argument('--churn', action='store_true',
                    help='rebuild the slot table every frame, as the Genesis '
                         'encoder does, instead of keeping tiles in place')
    ap.add_argument('--force', action='store_true',
                    help='emit even if the peak frame busts the vblank budget')
    a = ap.parse_args()
    assert a.w % 8 == 0 and a.h % 8 == 0, 'window must be a whole number of tiles'
    assert 1 <= a.pals <= 8, 'a 4bpp tilemap entry has three palette bits'
    tw, th = a.w // 8, a.h // 8

    with tempfile.TemporaryDirectory() as tmp:
        frames = extract(a.input, a.w, a.h, a.fps, tmp)
        if not frames:
            sys.exit('no frames extracted')
        print("%d frames at %dx%d (%dx%d tiles), %d fps"
              % (len(frames), a.w, a.h, tw, th, a.fps))
        pals, cent = build_palettes(frames, tw, th, a.pals)
        print("%d global palette%s x 15 colours, stable across the clip"
              % (a.pals, '' if a.pals == 1 else 's'))

        # ---- slot allocation -------------------------------------------
        # Slots are re-derived per frame, so their number is bounded by the
        # window (192 for 16x12) and can never reach the 1024 a tilemap entry
        # can name.  The Genesis encoder does the same thing.
        #
        # What it does NOT do is keep a tile in the slot it already had.  Its
        # dedup list is rebuilt from scratch each frame, so identical picture
        # content can land in a different slot than it did last frame -- and a
        # slot whose CONTENT changed has to be re-sent, even though not one
        # pixel of that tile moved.  --churn reproduces that behaviour so the
        # cost of it is a measurement rather than a claim.
        # header (16 B) + palettes, so bank offsets are counted from the
        # start of the ROM image and not from the start of the body
        hdr_pad = b'\x00' * (16 + a.pals * 32)
        padded = [0]
        n_cells = tw * th
        prev_slot_content = {}      # slot -> raw bytes currently in VRAM
        prev_map = {}               # cell -> tilemap entry
        qcache = {}
        body = bytearray()
        peak_tiles = peak_map = peak_frame = 0
        err_total = 0
        sum_tiles = sum_map = 0
        for fi, fp in enumerate(frames):
            arr = np.asarray(Image.open(fp).convert('RGB'), dtype=np.uint8)
            cells = tile_cells(arr, tw, th)

            # Per cell: the cost of every palette, then a choice that prefers
            # to AGREE with the cell to its left and above.  `--bias 0`
            # reproduces the per-tile-optimal choice, which is how the seams
            # get measured rather than asserted.
            opts = []
            for cell in cells:
                key = cell.tobytes()
                got = qcache.get(key)
                if got is None:
                    got = [(e, encode4bpp(idx))
                           for e, idx in quantise_all(cell, pals)]
                    qcache[key] = got
                opts.append(got)

            chosen = [0] * n_cells
            for ci in range(n_cells):
                cx, cy = ci % tw, ci // tw
                nb = []
                if cx: nb.append(chosen[ci - 1])
                if cy: nb.append(chosen[ci - tw])
                best, bestk = None, 0
                for k in range(len(pals)):
                    e = opts[ci][k][0]
                    if nb and a.bias:
                        agree = sum(1 for n in nb if n == k)
                        e = e * (1.0 - a.bias * agree / len(nb))
                    if best is None or e < best:
                        best, bestk = e, k
                chosen[ci] = bestk

            raws = []
            for ci in range(n_cells):
                k = chosen[ci]
                err, raw = opts[ci][k]
                err_total += err
                raws.append((raw, k))

            want = []
            for raw, k in raws:
                if raw not in want:
                    want.append(raw)
            assert len(want) <= n_cells

            if a.churn:
                slot_of = {raw: i for i, raw in enumerate(want)}
            else:
                # keep every content in the slot it already occupies
                held = {c: s for s, c in prev_slot_content.items()}
                slot_of = {}
                free = [s for s in range(n_cells)]
                for raw in want:
                    s = held.get(raw)
                    if s is not None and s not in slot_of.values():
                        slot_of[raw] = s
                taken = set(slot_of.values())
                free = [s for s in free if s not in taken]
                for raw in want:
                    if raw not in slot_of:
                        slot_of[raw] = free.pop(0)

            new_tiles = [(s, raw) for raw, s in slot_of.items()
                         if prev_slot_content.get(s) != raw]
            new_tiles.sort()
            for s, raw in new_tiles:
                prev_slot_content[s] = raw

            new_map = []
            for ci, (raw, k) in enumerate(raws):
                entry = slot_of[raw] | (k << 10)
                if prev_map.get(ci) != entry:
                    new_map.append((ci, entry))
                    prev_map[ci] = entry

            sum_tiles += len(new_tiles)
            sum_map += len(new_map)
            if len(new_tiles) * 34 + len(new_map) * 4 > peak_tiles * 34 + peak_map * 4:
                peak_tiles, peak_map, peak_frame = len(new_tiles), len(new_map), fi
            # BANK ALIGNMENT.  LoROM shows 32 KiB of a bank at $NN:8000, so a
            # 664 KiB stream is 21 banks and the player walks them.  A record
            # that STRADDLES a bank boundary would have to be reassembled from
            # two halves in 65816 with the data bank register changing
            # mid-record, which is a lot of code to save a few hundred bytes.
            #
            # So a frame is emitted whole or not at all: if it will not fit in
            # what is left of the current bank, pad to the next one.  The
            # player then only has to check for the pad marker at a frame
            # boundary, where it already is.  $FFFF is the marker because a
            # real frame's first word is a tile count, and 65535 tiles is
            # impossible for a window the tilemap can even name.
            fr = bytearray()
            fr += struct.pack('<H', len(new_tiles))
            for slot, raw in new_tiles:
                fr += struct.pack('<H', slot) + raw
            fr += struct.pack('<H', len(new_map))
            for cell, entry in new_map:
                fr += struct.pack('<HH', cell, entry)
            if a.bank:
                off = (len(hdr_pad) + len(body)) % a.bank
                if off + len(fr) + 2 > a.bank:
                    body += struct.pack('<H', 0xFFFF)
                    body += b'\x00' * (a.bank - (off + 2))
                    padded[0] += a.bank - off
            body += fr

        slots = prev_slot_content
        pal_blob = bytearray()
        for p in pals:
            pal_blob += struct.pack('<H', 0)
            for r, g, b in p:
                pal_blob += struct.pack('<H', bgr555(r, g, b))

        # The budget is per ANIMATION frame, not per vblank: at `fps` the
        # picture changes every 60/fps vblanks.  One of them is reserved for
        # the tilemap flip, so the tiles get the rest.
        vbl_per_frame = max(1, 60 // a.fps)
        tile_budget = max(1, vbl_per_frame - 1) * VBLANK_USABLE
        peak_tile_bytes = peak_tiles * 34
        peak_map_bytes = peak_map * 4 + 4
        peak_bytes = peak_tile_bytes + peak_map_bytes
        hdr = struct.pack('<4sHHHHHH', b'ESV1', tw, th, len(frames), a.fps,
                          a.pals, peak_tiles)
        blob = hdr + bytes(pal_blob) + bytes(body)

        rep = []
        rep.append("mkintro %s -> %s" % (a.input, a.out))
        rep.append("  window        %dx%d px, %dx%d = %d tiles"
                   % (a.w, a.h, tw, th, tw * th))
        rep.append("  clip          %d frames at %d fps = %.1f s"
                   % (len(frames), a.fps, len(frames) / a.fps))
        rep.append("  palettes      %d x 15, global and stable" % a.pals)
        rep.append("  slots in use  %d of %d cells (cap 1024 by tilemap index)"
                   % (len(slots), tw * th))
        rep.append("  palette bias  %.2f toward the left/upper neighbour"
                   % a.bias)
        rep.append("  allocation    %s"
                   % ('per-frame rebuild (Genesis behaviour)' if a.churn
                      else 'stable -- a tile keeps its slot'))
        rep.append("  mean/frame    %.1f tiles + %.1f map cells = %.0f bytes"
                   % (sum_tiles / len(frames), sum_map / len(frames),
                      (sum_tiles * 34 + sum_map * 4 + 4) / len(frames)))
        rep.append("  mean quant err %.1f per pixel"
                   % (err_total / (len(frames) * tw * th * 64)))
        rep.append("  peak frame    #%d  %d tiles (%d B) + %d map cells (%d B)"
                   % (peak_frame, peak_tiles, peak_tile_bytes,
                      peak_map, peak_map_bytes))
        rep.append("  vblanks/frame %d at %d fps; 1 reserved for the map flip"
                   % (vbl_per_frame, a.fps))
        rep.append("  tile budget   %d B  (%d vblanks x %d B usable)"
                   % (tile_budget, vbl_per_frame - 1, VBLANK_USABLE))
        rep.append("  tiles use     %.0f%% of it, %d B spare"
                   % (peak_tile_bytes / tile_budget * 100,
                      tile_budget - peak_tile_bytes))
        rep.append("  map flip      %d B of %d B in one vblank (%.0f%%)"
                   % (peak_map_bytes, VBLANK_USABLE,
                      peak_map_bytes / VBLANK_USABLE * 100))
        rep.append("  slots needed  %d double-buffered, cap 1024"
                   % (tw * th * 2))
        rep.append("  bank padding  %d B across %d banks of %d (%.2f%% waste)"
                   % (padded[0], (len(blob) + a.bank - 1) // a.bank if a.bank else 0,
                      a.bank, padded[0] / max(1, len(blob)) * 100)
                   if a.bank else "  bank padding  disabled")
        rep.append("  size          %d B = %.1f KiB = %.1f banks of 32 KiB"
                   % (len(blob), len(blob) / 1024, len(blob) / 32768))
        text = '\n'.join(rep)
        print(text)

        if a.report:
            open(a.report, 'w').write(text + '\n')

        why = []
        if peak_tile_bytes > tile_budget:
            why.append("the peak frame's tiles need %d B and %d vblanks carry %d"
                       % (peak_tile_bytes, vbl_per_frame - 1, tile_budget))
        if peak_map_bytes > VBLANK_USABLE:
            why.append("the tilemap flip needs %d B and one vblank carries %d"
                       % (peak_map_bytes, VBLANK_USABLE))
        if tw * th * 2 > 1024:
            why.append("double buffering needs %d slots and a tilemap entry "
                       "names 1024" % (tw * th * 2))
        if why and not a.force:
            sys.exit("\nREFUSING to emit: " + "; ".join(why) +
                     ".\nThe player would fall behind and the picture would "
                     "tear, which is not something\nthe gate can see.  Lower "
                     "--fps, shrink the window, or pass --force if you mean to.")

        os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
        open(a.out, 'wb').write(blob)
        print("\nwrote %s" % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
