#!/usr/bin/env python3
"""Prove the trainer's forward pass IS host/ref.py, bit for bit.

If this does not hold then QAT is training a different model from the one the
ROM runs and every downstream number is decorative.  The check is done at
every layer of every position, not just on the final token id - the ROM's own
Duff's-device bug produced the right token at positions 0 and 1 while layer 0
was already wrong at position 0.
"""
import os
import sys

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
sys.path.insert(0, HERE)

import ref
import model_nes as M


def random_int_model(seed=7, nexp=1):
    g = np.random.default_rng(seed)
    z = {}
    z["emb"] = g.integers(-7, 8, size=(M.V, M.D)).astype(np.int8)
    z["pos"] = g.integers(-7, 8, size=(M.T, M.D)).astype(np.int8)
    def tern(r, c):
        u = g.integers(0, 4, size=(r, c))
        return np.where(u < 2, 0, np.where(u == 2, 1, -1)).astype(np.int8)
    for l in range(M.L):
        for nm, sh in (("Wq", (M.D, M.D)), ("Wk", (M.D, M.D)), ("Wv", (M.D, M.D)),
                       ("Wo", (M.D, M.D))):
            z["L%d_%s" % (l, nm)] = tern(*sh)
        for nm, sh in (("W1", (M.FF, M.D)), ("W2", (M.D, M.FF))):
            if nexp == 1:
                z["L%d_%s" % (l, nm)] = tern(*sh)
            else:
                for e in range(nexp):
                    z["L%d_%s_e%d" % (l, nm, e)] = tern(*sh)
    z["head"] = tern(M.V, M.D)
    if nexp > 1:
        # every expert must be reachable or the test does not test it
        z["_route"] = np.array([t % nexp for t in range(M.V)], dtype=np.int16)
        z["_moe"] = np.array([nexp, 1], dtype=np.int16)
    return z


def load_into_torch(z, tau=1.0, nexp=1):
    route = [int(v) for v in z["_route"]] if nexp > 1 else None
    m = M.NesModel(tau=tau, mode="twn", quant=True, nexp=nexp, route=route)
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
        m.head[0].copy_(torch.tensor(z["head"], dtype=torch.float32))
    # the quantiser must reproduce the integers it was handed
    e = m.export_int()
    for k in z:
        if k.startswith("_"):
            continue
        assert np.array_equal(e[k], z[k]), "round trip failed on %s" % k
    return m


def main():
    # the mul table must be exactly floor(a*b/4) with no clamping, otherwise
    # model_nes.floor_prod is not the table the ROM reads
    bad = 0
    for a in range(-7, 8):
        for b in range(-7, 8):
            t = ref.MUL[((a & 15) << 4) | (b & 15)] - ref.MUL_BIAS
            if t != (a * b) >> 2:
                bad += 1
    print("mul table == floor(a*b/4) over -7..7 : %s (%d disagreements)"
          % ("YES" if bad == 0 else "NO", bad))
    assert bad == 0

    nexp = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print("experts: %d" % nexp)
    z = random_int_model(nexp=nexp)
    np.savez("/tmp/_equiv.npz", **z)
    rm = ref.Model.from_npz("/tmp/_equiv.npz")
    tm = load_into_torch(z, nexp=nexp)

    rng = np.random.default_rng(3)
    toks = rng.integers(0, M.V, size=M.T).tolist()
    if nexp > 1:
        # force the trajectory through EVERY expert, so a mixture that only
        # ever routes to expert 0 cannot pass this
        toks = [(i * 7 + e) % M.V for i, e in
                enumerate([x % nexp for x in range(M.T)])]
        toks = [t - (t % nexp) + (i % nexp) for i, t in enumerate(toks)]
        used = sorted(set(rm.route[t] for t in toks))
        print("experts exercised by the test trajectory: %s of %d"
              % (used, nexp))
        assert len(used) == nexp

    # ---- host reference, teacher forced on the same tokens ----------------
    r = ref.Runner(rm)
    ref_x, ref_next, ref_logits = [], [], []
    for p, t in enumerate(toks):
        nxt = r.step(t, p)
        s = r.trace[-1]
        ref_x.append([s["L%d.x" % l] for l in range(M.L)])
        ref_next.append(nxt)
        ref_logits.append(ref.matmul(r.split[rm.route[t]]["head"],
                                     s["L2.x"], 0, raw=True))

    # ---- torch twin -------------------------------------------------------
    tm.eval()
    with torch.no_grad():
        logits = tm(torch.tensor([toks], dtype=torch.long))
    tl = logits[0].numpy().astype(np.int64)
    dbg = tm.last_x                                   # (L, B, T, D)

    nx = 0
    for l in range(M.L):
        a = dbg[l][0].numpy().astype(np.int64)
        b = np.array([ref_x[p][l] for p in range(M.T)], dtype=np.int64)
        d = int(np.abs(a - b).max())
        nx += d
        print("layer %d  x:  max|torch - ref| = %d   over %d values" % (l, d, a.size))

    dl = int(np.abs(tl - np.array(ref_logits, dtype=np.int64)).max())
    print("logits:      max|torch - ref| = %d   over %d values" % (dl, tl.size))
    ta = tl.argmax(-1).tolist()
    print("argmax token ids equal        : %s" % (ta == ref_next))
    print("  torch:", ta)
    print("  ref  :", ref_next)

    ok = (nx == 0 and dl == 0 and ta == ref_next)
    print("\nFORWARD-PASS EQUIVALENCE: %s" % ("EXACT" if ok else "*** MISMATCH ***"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
