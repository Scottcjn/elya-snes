#!/usr/bin/env python3
"""Re-measure the quantised softmax's representational limit, on THIS tree.

The context experiment concluded that `sum_t p_t <= 8` with integer p_t in
0..7 is what stops a long-range head from paying for itself, and quoted
"only 1.14 to 1.75 positions carry any weight at all".  Both of those are
claims about the SHIPPED kernel, so both have to be re-measured here before
anything is changed - if the shipped softmax has moved, everything downstream
of the diagnosis moves with it.

Reports, over a real greedy generation through host/ref.py:

  * the distribution of sum_t p_t and of max_t p_t (the "<= 8", "0..7" claim)
  * positions with a nonzero nibble, per layer (the 1.14-1.75 claim)
  * the same count for the UNQUANTISED softmax of the identical integer
    scores, which is the ceiling a wider representation could ever reach.
    If the float distribution is itself concentrated on ~1.5 positions the
    quantiser is not the binding constraint and this whole line of work is
    dead on arrival.
"""
import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import ref


def eff_positions(w):
    """exp(entropy) of a non-negative weight vector: the number of positions
    it effectively spreads over.  1.0 means it is a single position."""
    s = float(sum(w))
    if s <= 0:
        return 0.0
    h = 0.0
    for v in w:
        if v > 0:
            p = v / s
            h -= p * math.log(p)
    return math.exp(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--seeds", default="1,26,40,58")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--temp", type=float, default=16.0,
                    help="float-softmax temperature for the reference column; "
                         "the exp table is ~64*exp(d/2) on d = floor(ds/8), "
                         "i.e. an effective temperature of 16 on the raw score")
    a = ap.parse_args()
    n = a.n if a.n is not None else ref.T - 1

    m = ref.Model.from_npz(a.npz)
    rows = []
    for s in (int(x) for x in a.seeds.split(",")):
        r = ref.Runner(m)
        r.record_attn = True
        cur = s
        for p in range(n):
            cur = r.step(cur, p)
        rows += r.attn_log

    # The probability vectors alone do not carry the scores, so re-derive the
    # scores by re-running with a scores hook.  Cheaper: patch softmax_q to
    # record its input.
    scorelog = []
    orig = ref.softmax_q

    def spy(scores):
        out = orig(scores)
        scorelog.append((list(scores), list(out)))
        return out

    ref.softmax_q = spy
    for s in (int(x) for x in a.seeds.split(",")):
        r = ref.Runner(m)
        cur = s
        for p in range(n):
            cur = r.step(cur, p)
    ref.softmax_q = orig

    print("T = %d   heads logged = %d   (seeds %s, %d steps each)"
          % (ref.T, len(rows), a.seeds, n))

    # ---- claim 1: sum <= 8, values in 0..7 --------------------------------
    sums = {}
    mx = 0
    smax = 0
    viol_sum = viol_val = 0
    for (_sc, pr) in scorelog:
        t = sum(pr)
        sums[t] = sums.get(t, 0) + 1
        smax = max(smax, t)
        mx = max(mx, max(pr) if pr else 0)
        if t > ref.SM_TARGET:
            viol_sum += 1
        if pr and max(pr) > ref.PMAX:
            viol_val += 1
    print("\n-- claim: sum_t p_t <= %d, p_t integer in 0..%d --"
          % (ref.SM_TARGET, ref.PMAX))
    print("softmax evaluations      %d" % len(scorelog))
    print("max observed sum_t p_t   %d" % smax)
    print("max observed p_t         %d" % mx)
    print("evaluations over budget  %d" % viol_sum)
    print("evaluations over clamp   %d" % viol_val)
    print("sum_t p_t histogram:")
    for k in sorted(sums):
        print("   sum = %-3d  %6d  %5.1f%%" % (k, sums[k],
                                               100.0 * sums[k] / len(scorelog)))

    # ---- claim 2: 1.14 - 1.75 positions carry weight ----------------------
    print("\n-- claim: only 1.14-1.75 positions carry any weight --")
    print("%-6s %-9s %-9s %-11s %-11s %-9s"
          % ("layer", "nonzero", "eff(quant)", "eff(float)", "nonzero_f",
             "avail"))
    tot_nz = tot_eq = tot_ef = tot_nf = tot_av = 0.0
    cnt_all = 0
    for l in range(ref.L):
        nz = eq = ef = nf = av = 0.0
        cnt = 0
        for (ll, h, p, pr) in rows:
            if ll != l:
                continue
            cnt += 1
            nz += sum(1 for v in pr if v)
            eq += eff_positions(pr)
            av += p + 1
        # scorelog is in the same evaluation order as rows
        for i, (sc, pr) in enumerate(scorelog):
            if rows[i][0] != l:
                continue
            mxs = max(sc)
            e = [math.exp((s - mxs) / a.temp) for s in sc]
            ef += eff_positions(e)
            # positions holding >= 1/8 of the mass: what a sum-8 budget could
            # in principle name at all
            tt = sum(e)
            nf += sum(1 for v in e if v / tt >= 1.0 / ref.SM_TARGET)
        print("%-6d %-9.2f %-9.2f %-11.2f %-11.2f %-9.2f"
              % (l, nz / cnt, eq / cnt, ef / cnt, nf / cnt, av / cnt))
        tot_nz += nz; tot_eq += eq; tot_ef += ef; tot_nf += nf; tot_av += av
        cnt_all += cnt
    print("%-6s %-9.2f %-9.2f %-11.2f %-11.2f %-9.2f"
          % ("all", tot_nz / cnt_all, tot_eq / cnt_all, tot_ef / cnt_all,
             tot_nf / cnt_all, tot_av / cnt_all))
    print("\nnonzero    = positions with a nonzero 4-bit nibble (the kernel)")
    print("eff(quant) = exp(entropy) of the quantised nibbles")
    print("eff(float) = exp(entropy) of exp(score/%.0f) on the SAME scores" % a.temp)
    print("nonzero_f  = float positions holding >= 1/%d of the mass"
          % ref.SM_TARGET)
    print("avail      = positions the causal mask allows (p+1)")

    # ---- AV sparsity: the thing a wider representation would destroy ------
    nzmac = totmac = 0
    for (_sc, pr) in scorelog:
        totmac += len(pr)
        nzmac += sum(1 for v in pr if v)
    # ---- the AV accumulator: does AV_SHIFT still fit? ---------------------
    r = ref.Runner(m)
    r.record_av = True
    cur = int(a.seeds.split(",")[0])
    for p in range(n):
        cur = r.step(cur, p)
    av = r.av_log
    lo = min(av); hi = max(av)
    mean = sum(av) / float(len(av))
    var = sum((v - mean) ** 2 for v in av) / float(len(av))
    HI = (8 << ref.AV_SHIFT) - 1
    LO = -(7 << ref.AV_SHIFT)
    sat = sum(1 for v in av if v > HI or v < LO)
    levels = len(set(ref.quant(v, ref.AV_SHIFT) for v in av))
    print("\n-- AV accumulator (AV_SHIFT = %d, PMUL_SHIFT = %d) --"
          % (ref.AV_SHIFT, ref.PMUL_SHIFT))
    print("samples        %d" % len(av))
    print("range          %d .. %d" % (lo, hi))
    print("mean / std     %.3f / %.3f" % (mean, var ** 0.5))
    print("saturating     %d  (%.2f%%)  outside [%d, %d]"
          % (sat, 100.0 * sat / len(av), LO, HI))
    print("output levels  %d of 15 reachable" % levels)

    print("\n-- AV multiply-adds --")
    print("softmax positions total      %d" % totmac)
    print("with a nonzero probability   %d  (%.2f%%)"
          % (nzmac, 100.0 * nzmac / totmac))
    print("multiplying by zero          %d  (%.2f%%)"
          % (totmac - nzmac, 100.0 * (totmac - nzmac) / totmac))
    return 0


if __name__ == "__main__":
    sys.exit(main())
