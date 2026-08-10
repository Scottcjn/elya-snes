#!/usr/bin/env python3
"""Held-out loss for an npz, on the SAME eval the trainer's final number uses.

The trainer's mid-run checkpoints report `--eval-iters` (20) batches; its final
number reports 60.  Comparing a checkpoint against a completed run without
re-evaluating it compares two different estimators, so this re-runs the 60-batch
evaluation with the trainer's own fixed eval generator (seed 9999) and prints
nats per token and per character.

    train/eval_npz.py runs/<arm>.npz [iters]
"""
import json
import os
import sys

import numpy as np
import torch
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model_nes as M

DEV = "cuda" if torch.cuda.is_available() else "cpu"
CPT = json.load(open("data/vocab.json"))["chars_per_token_bpe"]


def main():
    path = sys.argv[1]
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    meta = json.load(open(path.replace(".npz", ".json")))
    z = np.load(path)
    # The forward pass this script builds comes from the ENVIRONMENT
    # (model_nes reads NES_AV_SHIFT / NES_SM_NORM at import), and this tree now
    # holds four arms that differ in exactly those.  Evaluating a power-of-two
    # model through the exact-normalisation forward pass is a real, silent,
    # plausible-looking number - and it is the number the whole "do they add?"
    # question turns on.  So it is refused, not warned about.
    want = [M.K_SHIFT, M.W2_SHIFT, M.AV_SHIFT, M.SM_SHIFT]
    if "_shifts" in z and [int(v) for v in z["_shifts"]] != want:
        raise SystemExit(
            "%s was trained at shifts K/W2/AV/SM = %s; this evaluator is "
            "configured for %s.  Set NES_AV_SHIFT etc to match."
            % (path, [int(v) for v in z["_shifts"]], want))
    if "_smnorm" in z:
        got = "exact" if int(z["_smnorm"][0]) else "pow2"
        if got != M.SM_NORM:
            raise SystemExit(
                "%s was trained with the %s normaliser; this evaluator is "
                "configured for %s.  Set NES_SM_NORM=%s." % (path, got,
                                                             M.SM_NORM, got))
    nexp, nexp_head = ((int(v) for v in z["_moe"]) if "_moe" in z else (1, 1))
    route = [int(v) for v in z["_route"]] if "_route" in z else None
    m = M.NesModel(tau=meta["tau"], mode=meta["mode"], quant=meta["quant"],
                   logit_scale=meta["logit_scale"], nexp=nexp,
                   moe_head=nexp_head > 1, route=route).to(DEV)
    with torch.no_grad():
        m.emb.copy_(torch.tensor(z["emb"], dtype=torch.float32))
        m.pos.copy_(torch.tensor(z["pos"], dtype=torch.float32))
        for l in range(M.L):
            for nm, pl in (("Wq", m.Wq), ("Wk", m.Wk), ("Wv", m.Wv), ("Wo", m.Wo)):
                pl[l].copy_(torch.tensor(z["L%d_%s" % (l, nm)], dtype=torch.float32))
            for nm, pl in (("W1", m.W1), ("W2", m.W2)):
                for e in range(nexp):
                    k = ("L%d_%s" % (l, nm)) if nexp == 1 else ("L%d_%s_e%d" % (l, nm, e))
                    pl[l][e].copy_(torch.tensor(z[k], dtype=torch.float32))
        for e in range(nexp_head):
            k = "head" if nexp_head == 1 else "head_e%d" % e
            m.head[e].copy_(torch.tensor(z[k], dtype=torch.float32))
        # the integers we loaded must be the integers the quantiser emits, or
        # this is evaluating a different model from the one in the file
        ex = m.export_int()
        for k in z.files:
            if not k.startswith("_"):
                assert np.array_equal(ex[k], z[k]), "round trip failed on %s" % k

    val = np.load("data/val_%s.npy" % meta["vocab"])
    gev = torch.Generator().manual_seed(9999)          # the trainer's own
    B, T = meta["batch"], M.T
    scale = meta["logit_scale"]
    m.eval()
    tot = 0.0
    with torch.no_grad():
        for _ in range(iters):
            n = len(val) - T - 1
            ix = torch.randint(0, n, (B,), generator=gev)
            x = torch.stack([torch.from_numpy(val[i:i + T].astype(np.int64)) for i in ix]).to(DEV)
            y = torch.stack([torch.from_numpy(val[i + 1:i + 1 + T].astype(np.int64)) for i in ix]).to(DEV)
            lg = m(x) * scale
            tot += F.cross_entropy(lg.reshape(-1, M.V), y.reshape(-1)).item()
    v = tot / iters
    print("%-34s N=%d  steps_done=%d  val %.6f nats/token  %.4f nats/char  (%d batches)"
          % (os.path.basename(path), nexp, meta["hist"][-1]["step"], v, v / CPT, iters))


if __name__ == "__main__":
    main()
