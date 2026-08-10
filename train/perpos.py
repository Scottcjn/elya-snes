#!/usr/bin/env python3
"""Cross entropy PER POSITION, which is the measurement the context question
actually turns on.

The aggregate val/char of a T = 85 model and a T = 20 model are not a clean
comparison: the longer model is scored on 85 positions of which the first 20
have no more context available than the short model ever had, and it also has
to spend 65 extra rows of positional table.  What separates "the window was
the limit" from "the weights were the limit" is whether the loss keeps FALLING
as the position index grows past 19.

So this reports, on the held-out split and with the same quantised forward
pass the ROM runs:

  * loss at each position bucket, in nats per character,
  * the loss at positions 0..19 (what a T = 20 model can also see),
  * the loss at positions 20..T-1 (context the T = 20 cartridge never had).

If those two are equal, the extra context bought nothing.
"""
import argparse
import json
import os
import sys

import numpy as np
import torch
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import model_nes as M

DEV = "cuda" if torch.cuda.is_available() else "cpu"


def load(npz, tau, mode="twn"):
    z = np.load(npz)
    if "_ctx" in z and int(z["_ctx"][0]) != M.T:
        raise SystemExit("%s was trained at T = %d, NES_T says %d"
                         % (npz, int(z["_ctx"][0]), M.T))
    m = M.NesModel(tau=tau, mode=mode, quant=2)
    with torch.no_grad():
        m.emb.copy_(torch.tensor(z["emb"], dtype=torch.float32))
        m.pos.copy_(torch.tensor(z["pos"], dtype=torch.float32))
        for l in range(M.L):
            for nm, pl in (("Wq", m.Wq), ("Wk", m.Wk), ("Wv", m.Wv),
                           ("Wo", m.Wo), ("W1", m.W1), ("W2", m.W2)):
                pl[l].copy_(torch.tensor(z["L%d_%s" % (l, nm)], dtype=torch.float32))
        m.head.copy_(torch.tensor(z["head"], dtype=torch.float32))
    e = m.export_int()
    for k in ("emb", "pos", "head"):
        assert np.array_equal(e[k], z[k]), "quantiser round trip failed on %s" % k
    return m.to(DEV)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--tau", type=float, default=0.75)
    ap.add_argument("--scale", type=float, default=None,
                    help="logit scale; default reads it from the sibling .json")
    ap.add_argument("--batch", type=int, default=96)
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--vocab", default="bpe64")
    ap.add_argument("--cpt", type=float, default=None,
                    help="characters per token; default from data/vocab.json")
    a = ap.parse_args()

    scale = a.scale
    if scale is None:
        j = a.npz[:-4] + ".json"
        scale = json.load(open(j))["logit_scale"] if os.path.exists(j) else 1.0
    cpt = a.cpt
    if cpt is None:
        cpt = json.load(open("data/vocab.json"))["chars_per_token_bpe"]

    val = np.load("data/val_%s.npy" % a.vocab)
    m = load(a.npz, a.tau)
    m.eval()
    gen = torch.Generator().manual_seed(9999)

    tot = torch.zeros(M.T, dtype=torch.float64)
    n = 0
    with torch.no_grad():
        for _ in range(a.iters):
            ix = torch.randint(0, len(val) - M.T - 1, (a.batch,), generator=gen)
            x = torch.stack([torch.from_numpy(val[i:i + M.T].astype(np.int64)) for i in ix]).to(DEV)
            y = torch.stack([torch.from_numpy(val[i + 1:i + 1 + M.T].astype(np.int64)) for i in ix]).to(DEV)
            lg = m(x) * scale
            ls = F.cross_entropy(lg.reshape(-1, M.V), y.reshape(-1),
                                 reduction="none").view(a.batch, M.T)
            tot += ls.mean(0).double().cpu()
            n += 1
    per = (tot / n).numpy()

    print("model      %s" % a.npz)
    print("T = %d   logit_scale %.6f   chars/token %.3f   (%d x %d val windows)"
          % (M.T, scale, cpt, a.iters, a.batch))
    print("\n%-12s %-14s %-14s" % ("positions", "nats/token", "nats/char"))
    edges = [0, 5, 10, 20, 40, 60, 85]
    edges = [e for e in edges if e <= M.T]
    if edges[-1] != M.T:
        edges.append(M.T)
    for lo, hi in zip(edges, edges[1:]):
        v = per[lo:hi].mean()
        print("%-12s %-14.4f %-14.4f" % ("%d-%d" % (lo, hi - 1), v, v / cpt))
    print()
    early = per[:min(20, M.T)].mean()
    print("positions 0-%-2d  %.4f nats/token  %.4f nats/char   <- also visible to T=20"
          % (min(20, M.T) - 1, early, early / cpt))
    if M.T > 20:
        late = per[20:].mean()
        print("positions 20-%-2d %.4f nats/token  %.4f nats/char   <- beyond T=20's reach"
              % (M.T - 1, late, late / cpt))
        print("\nIMPROVEMENT FROM CONTEXT BEYOND 20 TOKENS: %.4f nats/char (%.2f%%)"
              % ((early - late) / cpt, 100.0 * (early - late) / early))
    allv = per.mean()
    print("\nALL POSITIONS  %.4f nats/token  %.4f nats/char" % (allv, allv / cpt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
