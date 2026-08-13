#!/usr/bin/env python3
"""What the router costs the 65816, counted rather than assumed.

The router runs ONCE per question and the engine then runs twenty token
steps, so the router's budget is enormous next to anything in the inner loop.
That is a reason to say what it costs, not a reason to skip saying it.

WHAT IS COUNTED.  The table is tools/mkrouter.py's: NROW rows of RWSTRIDE=16
bytes, each row one length byte, ten characters zero-padded, five signed
weights.  Looking a feature up is a scan; this file counts, over every
held-out question, how many table rows each of three scan strategies touches
and how many characters it compares.  Those counts are exact.  Turning them
into cycles needs a cost per row, which is stated below and is the only
estimated quantity here:

  ROW_MISS   a row rejected on its length byte.  LDA table,x (5 with the long
             addressing rom/game.inc uses for GDBASE data) + CMP # (2) +
             BNE (3) + the 16-byte index advance, which on the 65816 is
             TXA/CLC/ADC #16/TAX (2+2+3+2) = 9.            -> 19 cycles
  CHAR_CMP   one character of a row whose length matched: LDA table,x (5) +
             CMP abs,y (5) + BNE (3) + INY (2)             -> 15 cycles
  ROW_HIT    the five 16-bit weight adds once a row matches: 5 x
             (LDA abs,x 5 + CLC 2 + ADC abs 5 + STA abs 5) -> 85 cycles
  FEAT       per feature: extracting it from the expanded prompt and setting
             up the scan, generously                       -> 40 cycles

THREE SCANS, all of which rom/game.inc could implement:

  linear     walk every row.  The simplest thing that works and the shape
             tools/mkrouter.py's docstring already describes.
  bucketed   256-entry index from the first character to the first row with
             that character, table sorted by (first char, length).  256 x 2
             bytes of extra ROM, one indexed load, then a short run.
  bisect     table sorted by the feature bytes, binary search.  ceil(log2 N)
             row probes, each a full character compare.

The comparison at the end is against ONE ANSWER: twenty token steps at the
measured 7.850 t/s FastROM engine rate, which is 3,580,000 / 7.850 * 20 / 20
= 456,051 cycles per token and 9,121,019 for the twenty.  A router that costs
one percent of that is free in any sense that matters on this machine.
"""
import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import router as R

ROW_MISS = 19
CHAR_CMP = 15
ROW_HIT = 85
FEAT = 40

CLK = 3_580_000.0            # FastROM 65816
TPS = 7.850                  # measured engine rate, FINDINGS entry 11
ANSWER_CYCLES = CLK / TPS * 20


def costs(rt, rows):
    """Exact operation counts for one router over one set of questions."""
    tab = [f for f, _w in rt.table()]
    n = len(tab)
    # bucketed: rows grouped by first character
    order = sorted(tab)
    bucket = {}
    for i, f in enumerate(order):
        bucket.setdefault(f[0], []).append(f)
    tot = {"lin": 0, "buc": 0, "bis": 0}
    nfeat = 0
    for _t, q, _a, _h in rows:
        for f in rt.feat(q):
            nfeat += 1
            hit = f in rt.W
            # --- linear: every row until the match, or all of them ---------
            if hit:
                idx = tab.index(f)
                miss = idx
            else:
                miss = n
            tot["lin"] += FEAT + miss * ROW_MISS + (len(f) * CHAR_CMP +
                                                    ROW_HIT if hit else 0)
            # --- bucketed: only the rows sharing a first character ---------
            b = bucket.get(f[0], [])
            if hit:
                miss = b.index(f)
            else:
                miss = len(b)
            tot["buc"] += FEAT + miss * ROW_MISS + (len(f) * CHAR_CMP +
                                                    ROW_HIT if hit else 0)
            # --- bisect: log2 probes, each a character compare -------------
            probes = max(1, int(math.ceil(math.log2(n + 1))))
            tot["bis"] += (FEAT + probes * (len(f) * CHAR_CMP) +
                           (ROW_HIT if hit else 0))
    return tot, nfeat, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default=None)
    a = ap.parse_args()
    C.check()
    rows = C.rows_of("dev") + C.rows_of("test")

    print("router cost per question, over the %d held-out questions.\n"
          "Row and character counts are exact; the cycles per row are the "
          "stated model\nat the top of this file.  One answer at the measured "
          "FastROM rate is %s cycles.\n" % (len(rows), format(int(ANSWER_CYCLES), ",")))
    print("%-14s %5s %6s %7s   %-22s %-22s %-22s"
          % ("router", "rows", "bytes", "feat/q", "linear scan",
             "bucketed by 1st char", "sorted + bisect"))
    for kind in ("word-counts", "wordgram-lr"):
        rt = R.build(kind=kind)
        tot, nfeat, n = costs(rt, rows)
        cells = []
        for k in ("lin", "buc", "bis"):
            c = tot[k] / len(rows)
            cells.append("%9s cy  %5.2f%%" % (format(int(c), ","),
                                              100 * c / ANSWER_CYCLES))
        print("%-14s %5d %6d %7.1f   %-22s %-22s %-22s"
              % (kind, n, 16 * n, nfeat / len(rows), cells[0], cells[1],
                 cells[2]))
    print("\nThe percentage is of ONE ANSWER, not of one token: the router "
          "runs once per\nquestion and the engine then runs twenty steps.  "
          "The table is %d rows of\nRWSTRIDE=16, so the ROM cost is the "
          "%d bytes above plus, for the bucketed\nscan, 512 more for the "
          "first-character index."
          % (len(R.build().W), 16 * len(R.build().W)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
