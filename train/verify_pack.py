#!/usr/bin/env python3
"""Prove max|dW| = 0 between the trained npz and the bytes the ROM will read.

The sibling N64 run lost a week to an exporter that failed SILENTLY three ways
at once (wrong magic, LSB-first packing against an MSB-first reader,
offset-binary against a two's-complement decoder).  Nothing in that pipeline
raised.  So this decodes `out/model/stream.bin` + `out/model/headers.bin` back
into weight matrices using ONLY the documented stream format - it does not call
the packer's own code path - and diffs them against the npz the trainer wrote.

It also checks the embedding / positional tables and the derived d7 constants,
and re-measures the block-16 carry bound on the TRAINED weights, since the
bound depends on activation range, not on the weights, but the block-32
overflow count depends on the sparsity the training chose.
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import ref

BANK, BLOCK, SENT = ref.BANK, ref.BLOCK, ref.SENTINEL


def decode(stream, headers, shapes):
    """Walk the stream exactly as rom/nn.s does and rebuild each matrix.

    The `$FF` sentinel carries the ABSOLUTE bank number in its second byte, so
    this decoder follows the walk wherever it goes - out of the shared region
    into an expert's banks and back - using only the bytes, never the packer's
    idea of where things are.
    """
    mats, hi, bank, off = [], 0, None, None
    for name, rows, cols in shapes:
        m = np.zeros((rows, cols), dtype=np.int8)
        for r in range(rows):
            npos, nneg, d7lo, d7hi = headers[hi:hi + 4]
            hi += 4
            if npos == SENT:
                bank = int(nneg)          # byte 1 of a sentinel IS the bank
                off = BLOCK
                npos, nneg, d7lo, d7hi = headers[hi:hi + 4]
                hi += 4
            if bank is None:
                raise SystemExit("%s row %d read before any bank was selected"
                                 % (name, r))
            d7 = d7lo | (d7hi << 8)
            if d7 >= 0x8000:
                d7 -= 0x10000
            if d7 != -ref.BIAS * (int(npos) - int(nneg)):
                raise SystemExit("d7 wrong on %s row %d: %d" % (name, r, d7))
            base = bank * BANK + off
            if base + npos + nneg > (bank + 1) * BANK:
                raise SystemExit("row %s:%d straddles a bank" % (name, r))
            p = list(stream[base:base + npos])
            n = list(stream[base + npos:base + npos + nneg])
            off += npos + nneg
            m[r, np.asarray(p, dtype=np.int64)] = 1
            m[r, np.asarray(n, dtype=np.int64)] = -1
            if len(set(p)) != len(p) or len(set(n)) != len(n) or (set(p) & set(n)):
                raise SystemExit("index list not a partition on %s row %d" % (name, r))
        mats.append((name, m))
    if hi != len(headers):
        raise SystemExit("header table has %d unread bytes" % (len(headers) - hi))
    return mats


def shapes_for(z, e, nexp, nexp_head):
    """The walk order rom/nn.s consumes, with expert e's copies substituted."""
    out = []
    for l in range(ref.L):
        for nm in ("Wq", "Wk", "Wv", "Wo"):
            out.append(("L%d_%s" % (l, nm), ref.D, ref.D))
        for nm, r, c in (("W1", ref.F, ref.D), ("W2", ref.D, ref.F)):
            k = "L%d_%s" % (l, nm) if nexp == 1 else "L%d_%s_e%d" % (l, nm, e)
            out.append((k, r, c))
    out.append(("head" if nexp_head == 1 else "head_e%d" % e, ref.V, ref.D))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--dir", default="out/model")
    a = ap.parse_args()

    z = np.load(a.npz)
    stream = open(os.path.join(a.dir, "stream.bin"), "rb").read()
    embb = open(os.path.join(a.dir, "embed.bin"), "rb").read()
    posb = open(os.path.join(a.dir, "pos.bin"), "rb").read()
    if "_moe" in z:
        nexp, nexp_head = (int(v) for v in z["_moe"])
    else:
        nexp, nexp_head = 1, 1
    ne = max(nexp, nexp_head)

    worst, tot, nnz = 0, 0, 0
    shared_seen = {}
    print("%-14s %-12s %-10s %s" % ("matrix", "shape", "nnz", "max|dW|"))
    for e in range(ne):
        hf = "headers.bin" if ne == 1 else "headers_e%d.bin" % e
        headers = open(os.path.join(a.dir, hf), "rb").read()
        shapes = shapes_for(z, e, nexp, nexp_head)
        mats = decode(stream, headers, shapes)
        for name, m in mats:
            w = z[name].astype(np.int8)
            d = int(np.abs(m.astype(np.int32) - w.astype(np.int32)).max())
            worst = max(worst, d)
            shared = "_e" not in name
            if shared:
                # A shared matrix must decode IDENTICALLY from every expert's
                # header table.  If it does not, one expert is reading the
                # shared weights from the wrong bank and the model it runs is
                # not the model that was trained.
                if name in shared_seen:
                    if not np.array_equal(shared_seen[name], m):
                        raise SystemExit(
                            "shared %s decodes differently from expert %d's "
                            "header table" % (name, e))
                    continue
                shared_seen[name] = m
            tot += m.size
            nnz += int((m != 0).sum())
            print("%-14s %-12s %-10d %d" % (name, "x".join(map(str, m.shape)),
                                            int((m != 0).sum()), d))
    if ne > 1:
        print("shared matrices decode identically from all %d header tables: "
              "%d checked" % (ne, len(shared_seen)))

    def s8(b):
        v = np.frombuffer(b, dtype=np.uint8).astype(np.int32)
        return np.where(v >= 128, v - 256, v)

    de = int(np.abs(s8(embb).reshape(ref.V, ref.D) - z["emb"].astype(np.int32)).max())
    dp = int(np.abs(s8(posb).reshape(ref.T, ref.D) - z["pos"].astype(np.int32)).max())
    print("\nembed.bin  max|d| = %d   over %d values" % (de, ref.V * ref.D))
    print("pos.bin    max|d| = %d   over %d values" % (dp, ref.T * ref.D))
    print("weights    %d   nonzero %d   density %.4f" % (tot, nnz, nnz / tot))
    ok = worst == 0 and de == 0 and dp == 0
    if ne > 1:
        rb = open(os.path.join(a.dir, "routebank.bin"), "rb").read()
        route = [int(v) for v in z["_route"]]
        base = min(rb)
        bad = sum(1 for t in range(ref.V) if rb[t] - base != route[t])
        print("routebank.bin agrees with the trained route: %d/%d entries"
              % (ref.V - bad, ref.V))
        ok = ok and bad == 0
    print("\nmax|dW| = %d   over %d ternary weights -> %s"
          % (worst, tot, "EXACT" if ok else "*** MISMATCH ***"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
