#!/usr/bin/env python3
"""QAT trainer for the conversational model.

Same forward pass as train/train_nes.py - it imports the identical
model_nes.NesModel, so ternary weights, 4-bit activations, the floored multiply
table and the quantised softmax are all still the 65816 kernel's - and a
different DATA CONTRACT, which is the whole reason this file exists.

train_nes.py samples random T-long windows out of a concatenated stream.  That
is right for TinyStories and wrong here, for one reason: the positional table
is absolute and rom/game.inc feeds the question starting at POSP = 0.  A
question the trainer only ever saw at position 11 arrives at run time somewhere
the model has never seen it.  So every example here is a fixed 20-token row -
question, answer, spaces - and position 0 means position 0.

Per-position loss weights are exposed because the three regions are not the
same job.  The question is input the ROM feeds; the answer is the output it
draws; the trailing spaces are how the answer stops, because there is no
end-of-sequence token in this kernel and the ROM prints exactly 19 - len(prompt)
tokens whatever they are.
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
import route as R

DEV = "cuda" if torch.cuda.is_available() else "cpu"


def load(path="data", mono=True):
    z = np.load(os.path.join(path, "qa_train.npz"))
    X, Q, A = z["X"], z["Q"], z["A"]
    if mono:
        X = np.concatenate([X, z["Xmono"]])
        Q = np.concatenate([Q, z["Qmono"]])
        A = np.concatenate([A, z["Amono"]])
    return X.astype(np.int64), Q.astype(np.int64), A.astype(np.int64)


def weights(Q, A, T, qw, aw, pw):
    """Per-position loss weight for the PREDICTED token at position i+1."""
    w = np.zeros((len(Q), T), dtype=np.float32)
    for r in range(len(Q)):
        for i in range(T):
            w[r, i] = qw if i < Q[r] else (aw if i < A[r] else pw)
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.75)
    ap.add_argument("--mode", default="twn", choices=("twn", "bn"))
    ap.add_argument("--quant", type=int, default=2)
    ap.add_argument("--steps", type=int, default=8000)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--eval-every", type=int, default=1000)
    ap.add_argument("--logit-scale", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--nexp", type=int, default=1)
    ap.add_argument("--moe-head", type=int, default=0)
    ap.add_argument("--route", default="bal",
                    choices=("mod", "bal", "clus", "rand", "id"))
    ap.add_argument("--route-seed", type=int, default=1)
    ap.add_argument("--qw", type=float, default=1.0, help="loss on question positions")
    ap.add_argument("--aw", type=float, default=1.0, help="loss on answer positions")
    ap.add_argument("--pw", type=float, default=1.0, help="loss on trailing pad")
    ap.add_argument("--mono", type=int, default=1, help="include the act-2 monologue")
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default="runs/qa")
    ap.add_argument("--eval-answers", type=int, default=1)
    ap.add_argument("--qnoise", type=float, default=0.0,
                    help="per-token probability of replacing a QUESTION token "
                         "with a random one.  The forward pass sees the noisy "
                         "question and the loss is scored against the clean "
                         "row, so the model is asked to answer through a "
                         "corrupted question - the cheapest stand-in for the "
                         "paraphrases the held-out set is made of.")
    ap.add_argument("--nopos", type=int, default=0,
                    help="ablate the learned absolute positional table: zero it "
                         "and freeze it.  The sibling Genesis port measured "
                         "0/38 -> 36/38 exact answers from adding exactly this, "
                         "so it is worth knowing what it is worth here.")
    a = ap.parse_args()

    name = a.name or ("qa_e%d_%s%s_s%d"
                      % (a.nexp, a.route,
                         ("_nopos" if a.nopos else "") +
                         ("_qn%g" % a.qnoise if a.qnoise else ""), a.seed))
    os.makedirs(a.out, exist_ok=True)

    X, Q, A = load(mono=bool(a.mono))
    T = M.T
    assert X.shape[1] == T, (X.shape, T)
    W = weights(Q, A, T, a.qw, a.aw, a.pw)
    x = torch.from_numpy(X).to(DEV)
    w = torch.from_numpy(W).to(DEV)
    # mask of the question region, for --qnoise
    qmask = torch.zeros_like(x, dtype=torch.bool)
    for r in range(len(Q)):
        qmask[r, :Q[r]] = True

    # The router.  At nexp == V it is the identity - one expert per vocabulary
    # id, no construction to choose and nothing to tune, which is the dumbest
    # router the machine can express and the one the NES measurements say costs
    # nothing to prefer.
    if a.route == "id":
        assert a.nexp == M.V, "route=id needs nexp = V = %d" % M.V
        rt = list(range(M.V))
    else:
        rt = R.build(a.route, a.nexp, X.reshape(-1), seed=a.route_seed)
    if a.nexp > 1:
        print("route %-4s N=%d  %s" % (a.route, a.nexp,
                                       R.report(rt, X.reshape(-1), a.nexp)),
              flush=True)

    torch.manual_seed(a.seed)
    model = M.NesModel(tau=a.tau, mode=a.mode, quant=a.quant,
                       logit_scale=a.logit_scale, nexp=a.nexp,
                       moe_head=bool(a.moe_head), route=rt).to(DEV)
    torch.manual_seed(a.seed)
    with torch.no_grad():
        for p in model.parameters():
            if p.dim() >= 2:
                p.add_(torch.randn_like(p) * 0.1 * a.seed)
    if a.nopos:
        # Zero and freeze.  The table still ships - the ROM reads 20 x 64 bytes
        # whatever is in them - so this ablates the INFORMATION, not the
        # arithmetic, and the exactness gate is unaffected either way.
        with torch.no_grad():
            model.pos.zero_()
        model.pos.requires_grad_(False)
    model.renorm_()

    def scale():
        return model.logit_scale.abs()

    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, betas=(0.9, 0.95),
                            weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / a.warmup if s < a.warmup else
        0.5 * (1 + math.cos(math.pi * min(1.0, (s - a.warmup) /
                                          max(1, a.steps - a.warmup)))))

    def loss_of(lg):
        # predict position i+1 from position i
        l = F.cross_entropy(lg[:, :-1].reshape(-1, M.V),
                            x[:, 1:].reshape(-1), reduction="none")
        ww = w[:, 1:].reshape(-1)
        return (l * ww).sum() / ww.sum().clamp(min=1e-8)

    def stamps(z):
        z["_shifts"] = np.array([M.K_SHIFT, M.W2_SHIFT, M.AV_SHIFT, M.SM_SHIFT],
                                dtype=np.int16)
        z["_ctx"] = np.array([M.T], dtype=np.int16)
        z["_smtarget"] = np.array([M.SM_TARGET], dtype=np.int16)
        z["_smnorm"] = np.array([1 if M.SM_NORM == "exact" else 0], dtype=np.int16)
        return z

    t0 = time.time()
    hist = []
    best = None
    for step in range(a.steps):
        if a.qnoise > 0:
            hit = (torch.rand_like(x, dtype=torch.float) < a.qnoise) & qmask
            xin = torch.where(hit, torch.randint_like(x, 0, M.V), x)
        else:
            xin = x
        lg = model(xin) * scale()
        loss = loss_of(lg)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        model.renorm_()
        if (step + 1) % a.eval_every == 0 or step == 0:
            with torch.no_grad():
                # Teacher-forced exact-row recall: the fraction of examples
                # whose every ANSWER position argmaxes correctly.  This is not
                # the free-running number - it cannot be, one wrong token
                # derails free running and teacher forcing hides that - so it
                # is only a training monitor.  train/eval_answers.py on
                # host/ref.py is the number that counts and runs at the end.
                pred = model(x).argmax(-1)
                ok = 0
                for r in range(len(Q)):
                    lo, hi = max(Q[r] - 1, 0), A[r] - 1
                    if bool((pred[r, lo:hi] == x[r, lo + 1:hi + 1]).all()):
                        ok += 1
                hist.append(dict(step=step + 1, loss=float(loss),
                                 tf_rows=ok, n=len(Q), secs=time.time() - t0))
                print("%-22s step %5d  loss %.4f  tf-rows %3d/%d  %.0fs"
                      % (name, step + 1, float(loss), ok, len(Q),
                         time.time() - t0), flush=True)

    z = stamps(model.export_int())
    np.savez(os.path.join(a.out, name + ".npz"), **z)
    nnz = sum(int((v != 0).sum()) for k, v in z.items()
              if k not in ("emb", "pos") and not k.startswith("_"))
    tot = sum(int(v.size) for k, v in z.items()
              if k not in ("emb", "pos") and not k.startswith("_"))
    meta = dict(name=name, nexp=a.nexp, moe_head=int(a.moe_head), route=a.route,
                route_seed=a.route_seed, seed=a.seed, tau=a.tau, mode=a.mode,
                nopos=int(a.nopos), qnoise=a.qnoise,
                quant=a.quant, steps=a.steps, lr=a.lr, qw=a.qw, aw=a.aw,
                pw=a.pw, mono=a.mono, ctx=M.T, loss=float(loss.detach()),
                nnz=nnz, weights=tot, density=nnz / tot,
                secs=time.time() - t0, hist=hist)

    if a.eval_answers:
        sys.path.insert(0, os.path.join(HERE, "..", "host"))
        import eval_answers as E
        import corpus as C
        import ref
        vocab = E.load_vocab("data/qa_vocab.json")
        m = ref.Model.from_npz(os.path.join(a.out, name + ".npz"))
        rows = C.qa_lines()
        rtr = E.evaluate(m, vocab, [r for r in rows if not r[3]], "train")
        rho = E.evaluate(m, vocab, [r for r in rows if r[3]], "held")
        meta["exact_train"] = rtr["exact"]
        meta["exact_held"] = rho["exact"]
        meta["prefix_train"] = rtr["prefix"]
        meta["prefix_held"] = rho["prefix"]
        meta["degen_train"] = rtr["degen"]

    json.dump(meta, open(os.path.join(a.out, name + ".json"), "w"), indent=1)
    print("FINAL %-20s loss %.4f  density %.4f  weights %d  %.0fs"
          % (name, float(loss.detach()), nnz / tot, tot, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
