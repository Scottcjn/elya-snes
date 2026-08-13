#!/usr/bin/env python3
"""mkrouter.py -- the router's table, and the questions the gate routes.

train/router.py fits the shipped scorer on the 208 training questions and
quantises it into signed bytes.  This packs that table for rom/game.inc so the
cartridge computes the identical integer score, and packs every question in
train/corpus.py so the gate can run the CARTRIDGE's router over all 345 of
them and require it to agree with the host on every one.

THE FEATURE SET CHANGED.  The router is no longer words alone: it is words
PLUS every four-character window of `<word>`, weights fitted discriminatively.
Measured held-out routing went 68.6% -> 75.9% dev+test and the test error
27.5% -> 21.7%; train/route_arms.py has the thirty-five candidates that lost.
So the table is packed as TWO tables, because the two feature kinds have
different shapes and merging them would waste six bytes on every gram row:

  rtab.bin   NRWORD rows of RWSTRIDE bytes, the words:
                 [0]      length of the word, 1..RWMAXW
                 [1..10]  the word, zero padded
                 [11..15] five signed weights, in train/corpus.py's TOPICS
                          order: identity hardware model game honesty
             A fixed stride is worth the padding: the ROM indexes a row with
             one shift instead of walking a length-prefixed list, and the scan
             is the only thing the router does that is not an add.

  rgram.bin  NRGRAM rows of RGSTRIDE=16 bytes, the four-character grams:
                 [0..3]   the gram, exactly four bytes, from `<word>` so the
                          `<` and `>` mark the ends -- `<slo` is a word that
                          STARTS `slo`, which is a different fact from `slo`
                          occurring anywhere, and the corpus's signal is in
                          stems.
                 [4..8]   five signed weights
             A gram row needs nine bytes and is padded to sixteen for the same
             shift-indexing reason.  Packing it tighter costs a multiply per
             probe and saves 3,262 bytes of a 256 KB cartridge, which is the
             wrong trade.
             The grams are why an unseen word scores at all: `slowish? ` is a
             word the old table had never seen and could not score, and its
             grams `<slo` and `slow` are in the table from `slow? `.

             The two tables are separate ALSO because a gram and a word can be
             the same four characters -- `slow` is both -- and they carry
             different weights.  One table would need a tag byte per row;
             two tables need none.

WHAT THIS DOES NOT DO.  rom/game.inc has no router in it yet and there is no
tools/check_route.py, so nothing below has been executed by a 65816.  The
cycle cost of the scan is counted in train/route_cost.py: 6,929 cycles per
question with the table sorted and bisected, which is 0.08% of the 9.12M
cycles one twenty-token answer takes at the measured FastROM rate.

  rsq.bin / rsoff.bin / rslen.bin
             every corpus question, TOKENISED, exactly as the menu's prompts
             are.  The route-survey build feeds each one through the same
             expand-and-score path the ask menu uses and writes the shard it
             chose to SRAM; tools/check_route.py recomputes all 345 with
             train/router.py and requires them equal.  That is what makes the
             65816 router the same router as the measured one, rather than a
             reimplementation of it that was eyeballed on six menu entries.

WHY THE ROUTER IS OVER WORDS AND NOT OVER TOKENS.  Measured in train/router.py:
a bag-of-tokens scorer over this 64-entry vocabulary routes 42.3% of held-out
paraphrases correctly and a bag-of-words scorer routes 68.6%.  ' the ', 'coin'
and fifty-eight single letters do not carry a topic.  A LEARNED 64 x 5 layer
over the same vocabulary, which is the cheapest table that could possibly
work at 320 bytes, reaches 52.2% on test -- twenty-six points below words.
The vocabulary is the ceiling there, not the fitter.  So the ROM expands the
prompt back to ASCII -- which it already does to draw it -- and splits on
anything that is not a-z, which is what train/router.py's words() does.
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "train"))
import corpus as C                                                # noqa: E402
import router as R                                                # noqa: E402

RWMAXW = 10             # the widest word the table can hold
RWSTRIDE = 16           # 1 length + RWMAXW characters + 5 weights, padded to
                        # a power of two so a row index is a shift
RGRAM = 4               # the gram width; train/router.py's wordgrams(n=4)
RGSTRIDE = 16           # RGRAM characters + 5 weights, padded for the same
                        # shift-indexing reason
NTOPIC = 5


def encode(vocab, s):
    """Longest-match tokenise -- tools/mkgame.py's, restated so a change to one
    cannot silently diverge from the other; the round trip is asserted."""
    out, i = [], 0
    while i < len(s):
        best = None
        for j, t in enumerate(vocab):
            if s.startswith(t, i) and (best is None or len(t) > len(vocab[best])):
                best = j
        if best is None:
            raise SystemExit("cannot tokenise at %r" % s[i:])
        out.append(best)
        i += len(vocab[best])
    assert "".join(vocab[t] for t in out) == s, "round trip failed for %r" % s
    return out


def main(outdir):
    vocab = json.load(open(os.path.join(ROOT, "data", "vocab.json")))["vocab"]
    C.check()
    rt = R.build()
    os.makedirs(outdir, exist_ok=True)

    assert len(C.TOPICS) == NTOPIC, C.TOPICS

    # The table carries two feature kinds; split them.  train/router.py marks
    # a gram with a leading '#', which is host bookkeeping and never reaches
    # the ROM: the ROM tells the two apart by which table it is scanning.
    rows = [(f, w) for f, w in rt.table() if not f.startswith("#")]
    grams = sorted((f[1:], w) for f, w in rt.table() if f.startswith("#"))
    for g, _w in grams:
        if len(g) != RGRAM:
            raise SystemExit("gram %r is %d characters and RGRAM is %d"
                             % (g, len(g), RGRAM))
    gtab = bytearray()
    for g, ws in grams:
        gtab += g.encode("ascii") + bytes(RGSTRIDE - RGRAM - NTOPIC)
        gtab += bytes((x & 0xFF) for x in ws)
    assert len(gtab) == len(grams) * RGSTRIDE
    open(os.path.join(outdir, "rgram.bin"), "wb").write(bytes(gtab))

    tab = bytearray()
    for w, ws in rows:
        if len(w) > RWMAXW:
            raise SystemExit(
                "router word %r is %d characters and the table holds %d.  "
                "Widen RWSTRIDE here AND the compare loop in rom/game.inc, or "
                "the ROM will silently score that word as zero while the host "
                "scores it." % (w, len(w), RWMAXW))
        tab += bytes([len(w)]) + w.encode("ascii") + bytes(RWMAXW - len(w))
        tab += bytes((x & 0xFF) for x in ws)
    assert len(tab) == len(rows) * RWSTRIDE
    open(os.path.join(outdir, "rtab.bin"), "wb").write(bytes(tab))

    # Every question in the corpus, in one fixed order that tools/check_route.py
    # reproduces from train/corpus.py.  qa_rows() is that order.
    qs = [q for _t, q, _a, _s in C.qa_rows()]
    toks, offs, lens = bytearray(), bytearray(), bytearray()
    widest = 0
    for q in qs:
        p = encode(vocab, q)
        if len(p) > 255:
            raise SystemExit("%r is %d tokens" % (q, len(p)))
        offs += struct.pack("<H", len(toks))
        lens += bytes([len(p)])
        toks += bytes(p)
        widest = max(widest, len(q))
    open(os.path.join(outdir, "rsq.bin"), "wb").write(bytes(toks))
    open(os.path.join(outdir, "rsoff.bin"), "wb").write(bytes(offs))
    open(os.path.join(outdir, "rslen.bin"), "wb").write(bytes(lens))

    with open(os.path.join(outdir, "rdata.inc"), "w") as f:
        f.write("; GENERATED by tools/mkrouter.py -- do not edit\n")
        f.write("NRWORD      = %d\n" % len(rows))
        f.write("RWSTRIDE    = %d\n" % RWSTRIDE)
        f.write("RWMAXW      = %d\n" % RWMAXW)
        f.write("NRGRAM      = %d\n" % len(grams))
        f.write("RGSTRIDE    = %d\n" % RGSTRIDE)
        f.write("RGRAM       = %d\n" % RGRAM)
        f.write("NTOPIC      = %d\n" % NTOPIC)
        f.write("NRSQ        = %d\n" % len(qs))
        f.write("RSQ_BYTES   = %d\n" % len(toks))
        f.write("RQTEXTMAX   = %d\n" % 64)
    print("router %s: %d words x %d = %d bytes + %d grams x %d = %d bytes "
          "= %d total;\n  survey %d questions, %d prompt tokens, widest "
          "question %d characters"
          % (rt.kind, len(rows), RWSTRIDE, len(tab), len(grams), RGSTRIDE,
             len(gtab), len(tab) + len(gtab), len(qs), len(toks), widest))
    if widest >= 64:
        raise SystemExit("a question is %d characters and RQTEXTMAX is 64"
                         % widest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out/game"))
