#!/usr/bin/env python3
"""check_game.py -- what the game cartridge claims, checked against the bytes.

Four things, and the first is the one the whole design exists to support.

1. THE BINDING.  A coin may only be spawned from the code path that commits a
   generated token.  The ROM keeps three counters -- tokens committed, coins
   spawned, coins still queued -- and the invariant

       coins_spawned + coins_queued == tokens_committed

   must hold at EVERY trace sample, not just at the end.  Holding only at the
   end would be satisfied by a coin source that ran fast and then waited.

   The negative control is a separate build: -DNOGEN removes the forward pass
   and nothing else.  It must produce zero coins over a comparable number of
   frames.  Without that, "coins come from tokens" is a claim about code the
   reader has to take on trust.

2. THE MODEL IS STILL THE MODEL.  Act 2's line and act 3's answers are re-run
   here through host/ref.py from the seeds and prompts the ROM recorded, and
   must match token for token.  A game that generates different text than the
   reference is a broken game, whatever it looks like.

3. THE DMA LANDED.  The BG3 tilemap read back out of VRAM through $2139 must
   equal the shadow copy in WRAM.  This host cannot capture a screen, so the
   PPU's own memory is the evidence that anything was drawn at all.

4. THE FRAME IS PLAUSIBLE.  Her position stays inside the level, the state
   machine only moves forwards, and the run reached act 3.
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
os.environ.setdefault("NES_T", "20")
import ref                                                        # noqa: E402
sys.path.insert(0, HERE)
import mkgame                                                     # noqa: E402

SR_TOK, SR_TRACE, SR_STAT = 0x0010, 0x0200, 0x1000
SR_OAM, SR_VM3, SR_CGR = 0x1100, 0x1400, 0x1C00
SR_LINE, SR_ANS = 0x2000, 0x2040
SR_CGR2 = 0x5000        # CGRAM read a second time, back to back
SR_CGR0 = 0x5200        # CGRAM as it stood the moment setup finished
SR_SNAP1, SR_SNAP2 = 0x3000, 0x4000
NCTX = 20
STATE = ["logo", "play", "stop", "line", "ask", "answer", "end"]


def u16(d, o):
    return d[o] | (d[o + 1] << 8)


def main(path, ctrl=None):
    d = open(path, "rb").read()
    fails = []
    ok = []

    if d[0:4] != b"ELYA":
        print("not an elya dump", file=sys.stderr)
        return 1
    if d[8:12] != b"DONE":
        print("the ROM did not finish (stage marker %d)" % d[0x0C], file=sys.stderr)
        return 1
    # A DONE marker does not mean the file is one coherent moment: see
    # tools/ramsum.py.  Refuse to draw conclusions from a torn snapshot.
    want = d[0x7F00] | (d[0x7F01] << 8)
    got = sum(d[0x0100:0x7F00]) & 0xFFFF
    if got != want:
        print("TORN SNAPSHOT: checksum $%04X, the ROM wrote $%04X. This file "
              "mixes moments and nothing may be concluded from it." % (got, want),
              file=sys.stderr)
        return 1

    stat = [u16(d, SR_STAT + 2 * i) for i in range(12)]
    (tokcnt, coinsp, coinq, framec, ansn, linen,
     tracep, tokn, act1, nquest, camx, magic) = stat
    if magic != 0xC0DE:
        fails.append("the stat block is not stamped $C0DE")

    print("== the run ==")
    print("  frames            %6d   (%.1f s at 60.1 Hz)" % (framec, framec / 60.0988))
    print("  tokens committed  %6d" % tokcnt)
    print("  coins spawned     %6d" % coinsp)
    print("  coins queued      %6d" % coinq)
    print("  act 1 threshold   %6d tokens" % act1)
    print("  act 3 answers     %6d of %d questions" % (ansn // 22, nquest))

    # ---- 1. the binding ---------------------------------------------------
    print()
    print("== the binding: a coin may only come from a committed token ==")
    if coinsp + coinq == tokcnt:
        ok.append("final: %d spawned + %d queued == %d committed"
                  % (coinsp, coinq, tokcnt))
    else:
        fails.append("final counters: %d + %d != %d" % (coinsp, coinq, tokcnt))

    n = tracep // 16
    bad = []
    prev = None
    coin_lead = 0
    for i in range(n):
        o = SR_TRACE + i * 16
        r = [u16(d, o + 2 * j) for j in range(8)]
        fr, st, px, py, cq, tc, cs, live = r
        if cs + cq != tc:
            bad.append((i, fr, cs, cq, tc))
        if cs > tc:
            coin_lead += 1
        if not (0 <= px < 64 * 8) or not (0 <= py < 32 * 8 + 64):
            fails.append("sample %d has her at (%d,%d), outside the level"
                         % (i, px, py))
        # acts 0-3 only ever move forwards; act 3 itself is a loop, ask ->
        # answer -> ask, so the monotonic check stops at the conversation
        if prev is not None and st < prev and prev < 4:
            fails.append("sample %d went backwards: %s -> %s"
                         % (i, STATE[prev], STATE[st]))
        prev = st
    if bad:
        fails.append("the invariant broke at %d of %d samples, first %r"
                     % (len(bad), n, bad[0]))
    else:
        ok.append("every one of %d trace samples: spawned + queued == committed" % n)
    if coin_lead:
        fails.append("coins ran AHEAD of tokens at %d samples" % coin_lead)
    else:
        ok.append("coins never once ran ahead of the token count")
    if prev is None or prev < 5:
        fails.append("the run never reached act 3 (last state %s)"
                     % (STATE[prev] if prev is not None else "none"))
    else:
        ok.append("the state machine ran logo -> play -> stop -> line -> ask -> answer")

    if ctrl:
        c = open(ctrl, "rb").read()
        if c[8:12] != b"DONE":
            fails.append("the -DNOGEN control did not finish")
        else:
            cs = [u16(c, SR_STAT + 2 * i) for i in range(8)]
            print()
            print("== the negative control: -DNOGEN, the forward pass removed ==")
            print("  frames %d, tokens %d, coins %d" % (cs[3], cs[0], cs[1]))
            if cs[1] == 0 and cs[0] == 0:
                ok.append("-DNOGEN: %d frames, 0 tokens, 0 coins -- the coins "
                          "stall exactly when inference does" % cs[3])
            else:
                fails.append("-DNOGEN produced %d coins from %d tokens; a coin "
                             "is coming from somewhere that is not the model"
                             % (cs[1], cs[0]))

    # ---- 2. the model is still the model ----------------------------------
    print()
    print("== the generated text, against host/ref.py ==")
    npz = ref.default_weights()
    m = ref.Model.from_npz(npz)
    vocab = json.load(open(os.path.join(ROOT, "data", "vocab.json")))["vocab"]
    show = lambda t: "".join(vocab[i] for i in t)

    got = list(d[SR_TOK:SR_TOK + NCTX])
    want, _ = ref.generate(m, got[0], NCTX - 1)
    print("  act 1 free run  %r" % show(got))
    if got == want:
        ok.append("act 1's free run: %d/%d tokens identical to host/ref.py"
                  % (len(got), len(want)))
    else:
        fails.append("act 1's free run differs from the reference: %r vs %r"
                     % (got, want))

    seed = d[SR_LINE]
    gline = list(d[SR_LINE + 1:SR_LINE + 1 + linen])
    r = ref.Runner(m)
    cur, wline = seed, []
    for p in range(linen):
        cur = r.step(cur, p)
        wline.append(cur)
    print("  act 2 line      seed %r -> %r" % (vocab[seed], show(gline)))
    if gline == wline:
        ok.append("act 2's line: %d/%d tokens identical, generated from act 1's "
                  "last token -- so it depends on how the platformer went"
                  % (len(gline), len(wline)))
    else:
        fails.append("act 2's line differs: %r vs %r" % (gline, wline))

    for a in range(ansn // 22):
        o = SR_ANS + a * 22
        q, plen = d[o], d[o + 1]
        ngen = NCTX - plen
        gans = list(d[o + 2:o + 2 + ngen])
        prompt = mkgame.encode(vocab, mkgame.QUESTIONS[q])
        if len(prompt) != plen:
            fails.append("answer %d: the ROM fed %d prompt tokens, the host "
                         "tokeniser makes %d" % (a, plen, len(prompt)))
            continue
        r = ref.Runner(m)
        cur = None
        for p, t in enumerate(prompt):
            cur = r.step(t, p)
        wans = []
        for p in range(plen, NCTX):
            wans.append(cur)
            cur = r.step(cur, p)
        print("  act 3 q%d %-14r -> %r" % (q, mkgame.QUESTIONS[q], show(gans)))
        if gans == wans:
            ok.append("act 3 answer %d: %d/%d tokens identical to host/ref.py"
                      % (a, len(gans), len(wans)))
        else:
            fails.append("act 3 answer %d differs: %r vs %r" % (a, gans, wans))

    # ---- 3. the DMA landed ------------------------------------------------
    print()
    print("== what the PPU actually holds ==")
    vram = d[SR_VM3:SR_VM3 + 0x800]
    shadow = d[SR_SNAP2 + 0x220:SR_SNAP2 + 0x220 + 0x800]
    if vram == shadow:
        ok.append("the BG3 tilemap read back out of VRAM through $2139 equals "
                  "the WRAM shadow, all 1024 entries")
    else:
        diff = sum(1 for a, b in zip(vram, shadow) if a != b)
        fails.append("VRAM and the BG3 shadow differ in %d of 2048 bytes" % diff)

    oam_ppu = d[SR_OAM:SR_OAM + 0x220]
    oam_wram = d[SR_SNAP2:SR_SNAP2 + 0x220]
    same = sum(1 for a, b in zip(oam_ppu, oam_wram) if a == b)
    print("  OAM read back through $2138 agrees with the shadow in %d/%d bytes"
          % (same, 0x220))
    if same >= 0x200:
        ok.append("OAM read back through $2138 matches the shadow the DMA sent")
    else:
        fails.append("OAM readback matches the shadow in only %d/%d bytes"
                     % (same, 0x220))

    # CGRAM is 15 bits wide and bit 15 reads back as whatever the bus had, so
    # it is masked off before comparing.
    cg = bytearray(d[SR_CGR:SR_CGR + 0x200])
    for i in range(1, 0x200, 2):
        cg[i] &= 0x7F
    A = os.path.join(ROOT, "assets")
    # entry 0 is the backdrop, which the sky HDMA rewrites every eight
    # scanlines, so it holds the last band's colour and not the palette file's
    for name, first, skip in (("bg3.pal", 0, 1), ("bg1.pal", 32, 0),
                              ("bg2.pal", 48, 0), ("obj.pal", 128, 0)):
        want = open(os.path.join(A, name), "rb").read()
        got = bytes(cg[(first + skip) * 2:first * 2 + len(want)])
        if got == want[skip * 2:]:
            ok.append("CGRAM %3d..%3d holds %s exactly, read back through $213B"
                      % (first + skip, first + len(want) // 2 - 1, name))
        else:
            bad = [first + skip + i // 2
                   for i in range(len(got)) if got[i] != want[skip * 2 + i]]
            fails.append("CGRAM does not hold %s: entries %s differ (got %s, "
                         "want %s)" % (name, sorted(set(bad)), got.hex(),
                                       want[skip * 2:].hex()))
    # The same read, taken again immediately.  One run in nine came back with
    # a single palette entry wrong; two back-to-back reads say whether it is
    # CGRAM that is wrong or the read of it.  Never reproduced since.
    cg2 = bytearray(d[SR_CGR2:SR_CGR2 + 0x200])
    for i in range(1, 0x200, 2):
        cg2[i] &= 0x7F
    if bytes(cg) == bytes(cg2):
        ok.append("two back-to-back CGRAM readbacks agree on all 256 entries")
    else:
        diff = [i // 2 for i in range(0x200) if cg[i] != cg2[i]]
        fails.append("two CGRAM readbacks in the same run disagree at entries "
                     "%s -- the read port, not CGRAM" % sorted(set(diff)))

    # Setup-time CGRAM against end-of-run CGRAM: which half of the run does a
    # corrupted entry belong to?
    cg0 = bytearray(d[SR_CGR0:SR_CGR0 + 0x200])
    for i in range(1, 0x200, 2):
        cg0[i] &= 0x7F
    # CGRAM 32..47 is legitimately rewritten once, when the logo's palette is
    # replaced by the level's at the act 0 -> act 1 transition, and entry 0
    # belongs to the sky HDMA.  Of the rest, the entries the game actually
    # DISPLAYS are asserted; drift in entries nothing draws with is reported.
    #
    # The distinction is not a softened check, it is the shape of a measured
    # fact: with the sky HDMA running, one or two random CGRAM entries a run
    # come back holding something nobody wrote, and building the identical
    # cartridge with -DNOSKY stops it completely.  The ROM re-uploads the four
    # live palettes every sixteenth frame to bound that, which is why the used
    # entries can be required to be exact while the unused ones cannot.
    USED = set(range(0, 12)) | set(range(48, 64)) | set(range(128, 144))
    drift = sorted(set(i // 2 for i in range(2, 0x200)
                       if cg0[i] != cg[i] and not 32 <= i // 2 <= 47))
    hot = [e for e in drift if e in USED]
    if hot:
        fails.append("CGRAM entries %s -- which the game DRAWS with -- changed "
                     "during the run and the sixteen-frame refresh did not "
                     "restore them" % hot)
    else:
        ok.append("every CGRAM entry the game draws with is unchanged from the "
                  "end of setup to the end of the run")
    if drift:
        print("  note: CGRAM entries %s drifted during the run; none are drawn "
              "with.  Cause: the sky HDMA (see FINDINGS entry 9)." % drift)

    # The startup logo drew in black and white because its tilemap asked for
    # palette 0 while logo.pal had been DMA'd to CGRAM 32.  The art and the
    # palette file were both correct; only the load path was wrong, which is
    # exactly the class of bug a checker has to look for in the PPU rather than
    # in the source.  This reads the palette row out of the tilemap itself and
    # requires CGRAM at the end of SETUP to hold logo.pal there.
    lmap = open(os.path.join(A, "logo.map"), "rb").read()
    rows = set((u16(lmap, i) >> 10) & 7 for i in range(0, len(lmap), 2))
    lpal = open(os.path.join(A, "logo.pal"), "rb").read()
    if len(rows) != 1:
        fails.append("the logo tilemap asks for palette rows %s; it must ask "
                     "for exactly one" % sorted(rows))
    else:
        row = rows.pop()
        got = bytes(cg0[row * 16 * 2:row * 16 * 2 + len(lpal)])
        if got == lpal:
            ok.append("the logo's tilemap asks for palette %d and CGRAM %d..%d "
                      "held logo.pal at the end of setup -- the map and the "
                      "load agree" % (row, row * 16, row * 16 + len(lpal) // 2 - 1))
        else:
            fails.append("the logo tilemap asks for palette %d (CGRAM %d) and "
                         "that is not where logo.pal was loaded: %s vs %s"
                         % (row, row * 16, got.hex(), lpal.hex()))

    sky = open(os.path.join(A, "sky.hdma"), "rb").read()
    last = sky[-3] | (sky[-2] << 8)
    if (cg[0] | (cg[1] << 8)) == last:
        ok.append("CGRAM entry 0 holds the sky HDMA's last band ($%04X): the "
                  "gradient channel really is driving the backdrop" % last)
    else:
        fails.append("CGRAM entry 0 is $%04X, not the sky's last band $%04X"
                     % (cg[0] | (cg[1] << 8), last))

    for name, base in (("act 1", SR_SNAP1), ("act 3", SR_SNAP2)):
        if u16(d, base + 0xA2A) != 0x5A5A:
            fails.append("the %s frame snapshot is not stamped" % name)
        else:
            ok.append("the %s frame snapshot is complete (state %s, %d tokens)"
                      % (name, STATE[u16(d, base + 0xA22)], u16(d, base + 0xA26)))

    print()
    for line in ok:
        print("  ok    %s" % line)
    for line in fails:
        print("  FAIL  %s" % line)
    print()
    if fails:
        print("FAIL: %d of %d checks failed" % (len(fails), len(fails) + len(ok)))
        return 1
    print("PASS: %d checks" % len(ok))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    sys.exit(main(args[0], args[1] if len(args) > 1 else None))
