#!/usr/bin/env python3
"""QAT trainer for the NES ternary transformer.

Every constraint the 6502 kernel imposes is inside the forward pass:
ternary weights, 4-bit activations in -7..7, the fixed requantise shifts, the
floored 256-byte multiply table, the quantised softmax with its 15-entry exp
table and power-of-two normalisation, and the -7..7 residual clamps.  Nothing
is applied after training.

The block-16 accumulate is NOT modelled here and does not need to be: with the
activations stored biased into 0..14 a block of 16 sums to at most 224 < 256,
so the 8-bit accumulate is provably lossless and `ternary_row` is a plain sum.
That is the whole reason the port uses block 16; block 32 was measured to
overflow and is refuted in FINDINGS.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model_nes as M

DEV = "cuda" if torch.cuda.is_available() else "cpu"


def batches(data, B, T, gen):
    n = len(data) - T - 1
    ix = torch.randint(0, n, (B,), generator=gen)
    x = torch.stack([torch.from_numpy(data[i:i + T].astype(np.int64)) for i in ix])
    y = torch.stack([torch.from_numpy(data[i + 1:i + 1 + T].astype(np.int64)) for i in ix])
    return x.to(DEV), y.to(DEV)


@torch.no_grad()
def evaluate(model, data, B, T, iters, gen, scale):
    model.eval()
    tot = 0.0
    for _ in range(iters):
        x, y = batches(data, B, T, gen)
        lg = model(x) * scale()
        tot += F.cross_entropy(lg.reshape(-1, M.V), y.reshape(-1)).item()
    model.train()
    return tot / iters


@torch.no_grad()
def sparsity(model):
    z = model.export_int()
    nnz = tot = 0
    for k, v in z.items():
        if k in ("emb", "pos") or k.startswith("_"):
            continue
        nnz += int((v != 0).sum())
        tot += v.size
    return nnz, tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", default="bpe64", choices=("bpe64", "charset"))
    ap.add_argument("--tau", type=float, default=1.0)
    ap.add_argument("--mode", default="twn", choices=("twn", "bn"))
    ap.add_argument("--quant", type=int, default=2,
                help="2=full QAT, 1=float weights only, 0=fp32 control")
    ap.add_argument("--learn-scale", type=int, default=1)
    ap.add_argument("--steps", type=int, default=6000)
    ap.add_argument("--batch", type=int, default=192)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--eval-every", type=int, default=500)
    ap.add_argument("--eval-iters", type=int, default=20)
    ap.add_argument("--logit-scale", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--nexp", type=int, default=1,
                    help="feed-forward experts; 1 is the dense baseline")
    ap.add_argument("--moe-head", type=int, default=0,
                    help="1 = the output head is also per-expert")
    ap.add_argument("--route", default="bal", choices=("mod", "bal", "clus", "rand"))
    ap.add_argument("--route-seed", type=int, default=1)
    a = ap.parse_args()
    name = a.name or ("%s_%s_tau%.2f_q%d_s%d" % (a.vocab, a.mode, a.tau, a.quant, a.seed))
    os.makedirs(a.out, exist_ok=True)

    fit = np.load("data/fit_%s.npy" % a.vocab)
    val = np.load("data/val_%s.npy" % a.vocab)
    torch.manual_seed(a.seed)
    gen = torch.Generator().manual_seed(a.seed)
    gev = torch.Generator().manual_seed(9999)

    # The routing table is a property of the CORPUS, not of the training seed,
    # so it is built from the fit split with its own seed.  Two arms that
    # differ only in --seed must route identically or the comparison is
    # measuring two things at once.
    import route as R
    rt = R.build(a.route, a.nexp, fit, seed=a.route_seed)
    if a.nexp > 1:
        print("route %-5s N=%d  %s" % (a.route, a.nexp, R.report(rt, fit, a.nexp)),
              flush=True)

    model = M.NesModel(tau=a.tau, mode=a.mode, quant=a.quant,
                       logit_scale=a.logit_scale, nexp=a.nexp,
                       moe_head=bool(a.moe_head), route=rt).to(DEV)
    torch.manual_seed(a.seed)          # re-seed: NesModel uses its own generator
    with torch.no_grad():              # per-arm init so seeds actually differ
        for p in model.parameters():
            if p.dim() >= 2:
                p.mul_(1.0).add_(torch.randn_like(p) * 0.1 * a.seed)
    model.renorm_()
    def scale():
        return model.logit_scale.abs() if a.learn_scale else a.logit_scale

    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, betas=(0.9, 0.95),
                            weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / a.warmup) *
        (0.5 * (1 + math.cos(math.pi * min(1.0, s / a.steps)))) if s > a.warmup
        else (s + 1) / a.warmup)

    # Save the exportable artifact at every eval, not only at the end.  A
    # T = 85 run is ~19x the wall clock of a T = 20 one and this box shares its
    # GPU with other work, so "the process died at hour three" is a real
    # failure mode and losing the whole run to it is avoidable.
    def save(fl, vl, nnz, tot, hist, tag=""):
        z = model.export_int()
        # Stamp the requantise shifts INTO the weights file.  A model trained
        # at one AV_SHIFT and evaluated at another produces a lower loss and
        # visibly worse text with nothing anywhere raising, which happened here
        # once.
        z["_shifts"] = np.array([M.K_SHIFT, M.W2_SHIFT, M.AV_SHIFT, M.SM_SHIFT],
                                dtype=np.int16)
        # Same argument for the context length: an npz trained at one T loaded
        # at another gives a positional table of the wrong height, and the only
        # thing that would have caught it was a bare assert.
        z["_ctx"] = np.array([M.T], dtype=np.int16)
        # And the probability budget.  A model trained with a sum <= 16
        # softmax and run against a sum <= 8 kernel loses half its attention
        # mass silently; this is the same class of trap as the shifts.
        z["_smtarget"] = np.array([M.SM_TARGET], dtype=np.int16)
        # And WHICH normaliser spent that budget.  Both are trainable and both
        # are buildable, so the tree now holds models that differ only in this;
        # `_shifts` catches the pow2/exact pair only because the AV_SHIFT ladder
        # happened to land on a different rung under each, which is luck, not a
        # check.  1 = exact, 0 = power-of-two.
        z["_smnorm"] = np.array([1 if M.SM_NORM == "exact" else 0],
                                dtype=np.int16)
        np.savez(os.path.join(a.out, name + tag + ".npz"), **z)
        meta = dict(name=name + tag, vocab=a.vocab, tau=a.tau, mode=a.mode,
                    nexp=a.nexp, moe_head=int(a.moe_head), route=a.route,
                    route_seed=a.route_seed,
                    quant=a.quant, ctx=M.T, k_shift=M.K_SHIFT,
                    w2_shift=M.W2_SHIFT, av_shift=M.AV_SHIFT, sm_shift=M.SM_SHIFT,
                    sm_target=M.SM_TARGET, sm_norm=M.SM_NORM,
                    steps=a.steps, batch=a.batch, lr=a.lr, seed=a.seed,
                    logit_scale=float(scale().detach()) if a.learn_scale
                                else a.logit_scale,
                    fit=fl, val=vl, nnz=nnz, weights=tot, density=nnz / tot,
                    uniform=math.log(M.V), hist=hist, secs=time.time() - t0)
        json.dump(meta, open(os.path.join(a.out, name + tag + ".json"), "w"),
                  indent=1)

    hist = []
    t0 = time.time()
    for step in range(a.steps):
        x, y = batches(fit, a.batch, M.T, gen)
        lg = model(x) * scale()
        loss = F.cross_entropy(lg.reshape(-1, M.V), y.reshape(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        model.renorm_()
        if (step + 1) % a.eval_every == 0 or step == 0:
            fl = evaluate(model, fit, a.batch, M.T, a.eval_iters, gev, scale)
            vl = evaluate(model, val, a.batch, M.T, a.eval_iters, gev, scale)
            nnz, tot = sparsity(model)
            hist.append(dict(step=step + 1, fit=fl, val=vl, nnz=nnz,
                             density=nnz / tot, secs=time.time() - t0))
            print("%-28s step %5d  fit %.4f  val %.4f  density %.4f  %.0fs"
                  % (name, step + 1, fl, vl, nnz / tot, time.time() - t0), flush=True)
            save(fl, vl, nnz, tot, hist, tag="_ckpt")

    fl = evaluate(model, fit, a.batch, M.T, 60, gev, scale)
    vl = evaluate(model, val, a.batch, M.T, 60, gev, scale)
    nnz, tot = sparsity(model)
    save(fl, vl, nnz, tot, hist)
    print("FINAL %-24s fit %.4f  val %.4f  (uniform %.4f)  nnz %d/%d = %.4f"
          % (name, fl, vl, math.log(M.V), nnz, tot, nnz / tot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
