#!/usr/bin/env python3
"""Differentiable twin of host/ref.py.

The forward pass here is the NES reference forward pass, op for op, evaluated
on integer-valued float tensors.  Every non-differentiable step (ternarise,
requantise, floored product, quantised softmax) runs the EXACT integer op in
the forward direction and a matched linear surrogate in the backward direction
via a straight-through estimator.

This is why the trainer could not simply be the sibling N64 QAT trainer: that
model has float attention, per-32-block fp16 weight scales and an int8 tied
head over a 256 vocab.  The NES kernel has NO scales anywhere - a weight is
literally -1, 0 or +1 and an activation is literally an integer in -7..7 - so
the output magnitude is set by the fixed requantise shift and the row's
nonzero count, and nothing else.  The STE helpers below are the sibling
trainer's; the model is this port's.

Post-training ternarisation is not an option and was not tried: on the sibling
N64 model PTQ of a trained fp32 artifact scored a fit loss of 6.63 on a
byte-level corpus where uniform random is ln(256) = 5.545 - worse than random -
while QAT of the identical artifact reached 0.1012 against an fp32 control's
0.1007.
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

# ---- shape: settled, see DESIGN.md -----------------------------------------
import os
V, D, L, H, DH, FF = 64, 64, 3, 2, 32, 128
# T is the only shape knob that varies; see host/ref.py for the KV bound.
# Keep NES_T identical here and in host/ref.py - the trainer stamps it into the
# npz and ref.Model.from_npz refuses to load a mismatch.
T = int(os.environ.get("NES_T", "20"))
K_SHIFT  = int(os.environ.get("NES_K_SHIFT", "2"))
W2_SHIFT = int(os.environ.get("NES_W2_SHIFT", "3"))
AV_SHIFT = int(os.environ.get("NES_AV_SHIFT", "3"))
SM_SHIFT = int(os.environ.get("NES_SM_SHIFT", "3"))
ACT_MAX = 7
MUL_SHIFT = 2          # the QK mul table is floor(q*k / 4)
SM_TEMP = 16.0         # exp table is ~64*exp(d/2) with d = floor(ds/8)

# The softmax probability budget.  See host/ref.py for the derivation; the
# short version is that PMUL_SHIFT rises with SM_TARGET so that the attention
# accumulator bound (SM_TARGET * 7 / 2^PMUL_SHIFT = 14) does not move, which
# is what keeps AV_SHIFT and the attention output range fixed across the
# family.  Keep this identical to host/ref.py - train/test_equiv.py proves it.
SM_TARGET = int(os.environ.get("NES_SM_TARGET", "8"))
SM_NORM = os.environ.get("NES_SM_NORM", "exact")  # see host/ref.py
PMAX = SM_TARGET - 1
PMUL_SHIFT = 2 + (SM_TARGET // 8).bit_length() - 1
SM_SUM = float(SM_TARGET)

# exp table: EXP_SPAN raw score units of dynamic range, in 2^SM_SHIFT buckets
EXP_SPAN = 112
EXP_N = EXP_SPAN // (1 << SM_SHIFT) + 1


def ste(soft, hard):
    """forward = hard (exact integer), backward = d(soft)."""
    return soft + (hard - soft).detach()


# ---------------------------------------------------------------------------
# exact integer primitives (float tensors holding integers)
# ---------------------------------------------------------------------------
def q_exact(acc, k):
    """clamp(floor(acc / 2^k), -7, 7) - identical to ref.quant(acc, k)."""
    return torch.clamp(torch.floor(acc / (1 << k)), -ACT_MAX, ACT_MAX)


def q_ste(acc, k):
    soft = torch.clamp(acc / (1 << k), -ACT_MAX, ACT_MAX)
    return ste(soft, q_exact(acc, k))


def floor_prod(a, b, k=MUL_SHIFT):
    """The 256-byte mul table: floor(a*b / 4), with a linear surrogate."""
    p = a * b
    return ste(p / (1 << k), torch.floor(p / (1 << k)))


# ---------------------------------------------------------------------------
# The two attention contractions, written so the (B, H, T, T, DH) intermediate
# never enters the autograd graph.
#
# sum_t ste(a_t, b_t) == ste(sum_t a_t, sum_t b_t), because the detached term
# is linear in the sum.  So a sum of per-element straight-through products is
# EXACTLY a straight-through of (plain matmul, exact floored sum) - identical
# forward value and identical gradient, with the elementwise tensor confined to
# a no_grad chunk that is freed immediately.
#
# At T = 20 the naive form allocates 20 MB per layer and nobody notices.  At
# T = 85 it allocates 355 MB per layer three times over and the run OOMs on an
# 8 GB card.  train/test_equiv.py proves the rewrite did not change the model.
# ---------------------------------------------------------------------------
_CHUNK = 16


def _hard_qk(q, k):
    """sum_d floor(q[.,i,d] * k[.,j,d] / 4), exact, outside autograd."""
    B, H, T, _ = q.shape
    out = q.new_empty(B, H, T, T)
    with torch.no_grad():
        kk = k.unsqueeze(2)
        for i in range(0, T, _CHUNK):
            p = q[:, :, i:i + _CHUNK].unsqueeze(3) * kk
            out[:, :, i:i + _CHUNK] = torch.floor(p / (1 << MUL_SHIFT)).sum(-1)
    return out


def _hard_av(p, v):
    """sum_t floor(p[.,i,t] * v[.,t,d] / 2^PMUL_SHIFT), exact, outside autograd."""
    B, H, T, _ = p.shape
    out = v.new_empty(B, H, T, v.shape[-1])
    with torch.no_grad():
        vv = v.unsqueeze(2)
        for i in range(0, T, _CHUNK):
            pr = p[:, :, i:i + _CHUNK].unsqueeze(-1) * vv
            out[:, :, i:i + _CHUNK] = torch.floor(pr / (1 << PMUL_SHIFT)).sum(3)
    return out


def qk_scores(q, k):
    return ste((q @ k.transpose(-1, -2)) / (1 << MUL_SHIFT), _hard_qk(q, k))


def av_sum(p, v):
    return ste((p @ v) / (1 << PMUL_SHIFT), _hard_av(p, v))


def softmax_q(scores, mask, exptab):
    """Quantised softmax, bit-identical to ref.softmax_q.

    scores : (..., T) integer-valued
    mask   : (..., T) bool, True where the position is attendable
    """
    neg = torch.full_like(scores, -1e6)
    s = torch.where(mask, scores, neg)
    m = s.amax(-1, keepdim=True)
    lim = float(EXP_N - 1)
    d = torch.clamp(torch.floor((s - m) / (1 << SM_SHIFT)), min=-lim)
    idx = (d + lim).long().clamp(0, EXP_N - 1)
    e = exptab[idx]                                   # exact table lookup
    S = e.sum(-1, keepdim=True)
    if SM_NORM == "exact":
        hard = torch.clamp(torch.floor(e * SM_TARGET / S), max=float(PMAX))
    else:
        # kk = smallest k with (S >> k) <= SM_TARGET
        kk = torch.zeros_like(S)
        cur = S.clone()
        for _ in range(16):                           # S <= T*64 = 5440 < 2^13
            step = (torch.floor(cur / 2 ** kk) > SM_SUM).float()
            kk = kk + step
            if step.sum() == 0:
                break
        hard = torch.clamp(torch.floor(e / 2 ** kk), max=float(PMAX))
    hard = torch.where(mask, hard, torch.zeros_like(hard))
    soft = torch.softmax(torch.where(mask, scores, neg) / SM_TEMP, dim=-1) * SM_SUM
    soft = torch.clamp(soft, max=float(PMAX))
    return ste(soft, hard)


# ---------------------------------------------------------------------------
# weight quantisers
# ---------------------------------------------------------------------------
def ternarise(w, tau, mode):
    """No scale is stored anywhere in the ROM, so the quantiser emits bare
    -1/0/+1 and the output magnitude is governed by the row's nonzero count
    against the fixed requantise shift.  tau is therefore both the sparsity
    knob and the gain knob."""
    if mode == "none":
        return w
    a = w.abs()
    if mode == "twn":
        delta = tau * a.mean(dim=-1, keepdim=True)
        q = torch.sign(w) * (a > delta).to(w.dtype)
    elif mode == "bn":                                # BitNet b1.58 absmean
        s = a.mean(dim=-1, keepdim=True).clamp(min=1e-8) * tau
        q = torch.clamp(torch.round(w / s), -1.0, 1.0)
    else:
        raise ValueError(mode)
    return ste(w, q)


def quant_act_tbl(w):
    """embedding / positional entries: integers in -7..7."""
    return ste(torch.clamp(w, -ACT_MAX, ACT_MAX),
               torch.clamp(torch.round(w), -ACT_MAX, ACT_MAX))


# ---------------------------------------------------------------------------
class NesModel(nn.Module):
    def __init__(self, tau=1.0, mode="twn", quant=2, logit_scale=0.05,
                 nexp=1, moe_head=False, route=None):
        """quant: 2 = full QAT (what the ROM runs)
                  1 = float weights, integer activations - isolates the cost of
                      ternarising the weights
                  0 = full fp32 control - isolates what this 3x64x64 shape can
                      ever do, quantisation aside

        nexp: number of feed-forward experts.  1 is the dense baseline and is
              bit-identical to the pre-MoE model, including its RNG draws.
        moe_head: also give the output head one matrix per expert.
        route: (V,) integer expert assignment per vocabulary id.  The router
              IS this table - see FINDINGS for why a table indexed by the
              current token beats a learned projection on this machine."""
        super().__init__()
        self.tau, self.mode, self.quant = tau, mode, int(quant)
        self.qw = self.quant >= 2
        self.qa = self.quant >= 1
        self.nexp = int(nexp)
        self.moe_head = bool(moe_head)
        self.nexp_head = self.nexp if self.moe_head else 1
        E, EH = self.nexp, self.nexp_head
        g = torch.Generator().manual_seed(20260807)

        def p(*shape, s=1.0):
            return nn.Parameter(torch.randn(*shape, generator=g) * s)

        # NOTE on RNG order: torch.randn draws a flat sequence and reshapes it,
        # so p(1, FF, D) consumes exactly the draws p(FF, D) consumed.  At
        # nexp = 1 every parameter in this model is therefore the same tensor
        # the dense model had, which is what makes the E = 1 arm a true control
        # rather than a re-run.
        self.emb = p(V, D, s=2.0)
        self.pos = p(T, D, s=1.0)
        self.Wq, self.Wk, self.Wv, self.Wo = (nn.ParameterList() for _ in range(4))
        self.W1, self.W2 = nn.ParameterList(), nn.ParameterList()
        for _ in range(L):
            self.Wq.append(p(D, D)); self.Wk.append(p(D, D))
            self.Wv.append(p(D, D)); self.Wo.append(p(D, D))
            self.W1.append(p(E, FF, D)); self.W2.append(p(E, D, FF))
        self.head = p(EH, V, D)
        self.logit_scale = nn.Parameter(torch.tensor(float(logit_scale)))

        if route is None:
            route = [0] * V
        assert len(route) == V and max(route) < max(E, EH)
        self.register_buffer("route", torch.tensor(list(route), dtype=torch.long))

        import numpy as np
        exptab = [max(0, min(64, int(round(64.0 * math.exp(
                      (i - (EXP_N - 1)) * (1 << SM_SHIFT) / SM_TEMP)))))
                  for i in range(EXP_N)]
        self.register_buffer("exptab", torch.tensor(exptab, dtype=torch.float32))
        m = torch.tril(torch.ones(T, T, dtype=torch.bool))
        self.register_buffer("causal", m)

    # -- quantised views ----------------------------------------------------
    def _w(self, p):
        return ternarise(p, self.tau, self.mode if self.qw else "none")

    def _tbl(self, p):
        return quant_act_tbl(p) if self.qa else p

    def _mm(self, x, w, k, relu=False, raw=False):
        acc = x @ w.t()
        if raw:
            return acc
        q = q_ste(acc, k) if self.qa else acc / (1 << k)
        if relu:
            q = torch.clamp(q, min=0.0)
        return q

    def _moe_ffn(self, x, l, ef):
        """Feed-forward with one expert per position.

        x  : (N, D) flattened positions
        ef : (N,)   expert index per position, already routed

        Each expert runs on its OWN positions only, gathered out and scattered
        back, so the arithmetic per position is exactly the dense FFN and the
        total work does not grow with the expert count.  That is the whole
        point: the ROM streams one expert per token, and if the trainer ran all
        of them and averaged it would be training a model the cartridge cannot
        execute."""
        w1 = self._w(self.W1[l])                      # (E, FF, D)
        w2 = self._w(self.W2[l])                      # (E, D, FF)
        if self.nexp == 1:
            h = self._mm(x, w1[0], K_SHIFT, relu=True)
            return self._mm(h, w2[0], W2_SHIFT)
        out = x.new_zeros(x.shape[0], D)
        for e in range(self.nexp):
            sel = (ef == e).nonzero(as_tuple=True)[0]
            if sel.numel() == 0:
                continue
            xe = x.index_select(0, sel)
            he = self._mm(xe, w1[e], K_SHIFT, relu=True)
            out = out.index_copy(0, sel, self._mm(he, w2[e], W2_SHIFT))
        return out

    def _moe_head(self, x, ef):
        """Output head, optionally one matrix per expert.  Raw integer logits."""
        hw = self._w(self.head)                       # (EH, V, D)
        if self.nexp_head == 1:
            return self._mm(x, hw[0], 0, raw=True)
        out = x.new_zeros(x.shape[0], V)
        for e in range(self.nexp_head):
            sel = (ef == e).nonzero(as_tuple=True)[0]
            if sel.numel() == 0:
                continue
            out = out.index_copy(0, sel,
                                 self._mm(x.index_select(0, sel), hw[e], 0, raw=True))
        return out

    # -----------------------------------------------------------------------
    def forward(self, idx):
        """idx: (B, T) int64.  Returns logits (B, T, V)."""
        B, Tn = idx.shape
        ef = self.route[idx].reshape(-1)              # expert per position
        emb, pos = self._tbl(self.emb), self._tbl(self.pos)
        x = emb[idx] + pos[:Tn].unsqueeze(0)
        x = torch.clamp(x, -ACT_MAX, ACT_MAX)

        mask = self.causal[:Tn, :Tn].unsqueeze(0).unsqueeze(0)      # (1,1,T,T)
        self.last_x = []

        for l in range(L):
            q = self._mm(x, self._w(self.Wq[l]), K_SHIFT)
            k = self._mm(x, self._w(self.Wk[l]), K_SHIFT)
            v = self._mm(x, self._w(self.Wv[l]), K_SHIFT)

            def heads(t):
                return t.view(B, Tn, H, DH).transpose(1, 2)          # (B,H,T,DH)

            qh, kh, vh = heads(q), heads(k), heads(v)
            # scores[b,h,i,j] = sum_d floor(q[i,d]*k[j,d] / 4)
            if self.qa:
                sc = qk_scores(qh, kh)                            # (B,H,T,T)
                pr = softmax_q(sc, mask.expand(B, H, Tn, Tn), self.exptab)
                # zeroing outside the causal mask kills both the value and the
                # gradient there, exactly as the previous `where(mask, ., 0)`
                # after the elementwise product did
                pr = pr * mask.to(pr.dtype)
                av = av_sum(pr, vh)
                att = q_ste(av, AV_SHIFT)
            else:
                sc = (qh @ kh.transpose(-1, -2)) / (1 << MUL_SHIFT)
                pr = torch.softmax(sc.masked_fill(~mask, -1e9) / SM_TEMP, -1) * SM_SUM
                av = (pr @ vh) / (1 << PMUL_SHIFT)
                att = av / (1 << AV_SHIFT)
            att = att.transpose(1, 2).reshape(B, Tn, D)

            o = self._mm(att, self._w(self.Wo[l]), K_SHIFT)
            x = x + o
            if self.qa:
                x = torch.clamp(x, -ACT_MAX, ACT_MAX)
            f = self._moe_ffn(x.reshape(-1, D), l, ef).reshape(B, Tn, D)
            x = x + f
            if self.qa:
                x = torch.clamp(x, -ACT_MAX, ACT_MAX)
            self.last_x.append(x.detach())

        # RAW integer logits, exactly what the ROM argmaxes.  The training
        # temperature is applied by the caller so that this function stays a
        # bit-exact twin of ref.Runner.step.
        return self._moe_head(x.reshape(-1, D), ef).reshape(B, Tn, V)

    # -----------------------------------------------------------------------
    @torch.no_grad()
    def renorm_(self):
        """Keep every ternary matrix at unit mean-|w|.  The quantiser is scale
        invariant (delta is proportional to mean|w|), so this only stops the
        latent weights from drifting numerically; it changes no forward value.

        Expert stacks are normalised PER EXPERT, not over the stack: the
        quantiser's delta is a per-row mean so the forward value is unchanged
        either way, but a shared divisor lets a rarely-routed expert's latent
        weights be dragged by a busy one, which is a real coupling between
        arms that are supposed to be independent."""
        for pl in (self.Wq, self.Wk, self.Wv, self.Wo, self.W1, self.W2):
            for p in pl:
                p.data /= p.data.abs().mean(dim=(-2, -1), keepdim=True).clamp(min=1e-8)
        self.head.data /= self.head.data.abs().mean(dim=(-2, -1),
                                                    keepdim=True).clamp(min=1e-8)

    @torch.no_grad()
    def _tern(self, w):
        a = w.abs()
        if self.mode == "twn":
            delta = self.tau * a.mean(dim=-1, keepdim=True)
            return torch.sign(w) * (a > delta).to(w.dtype)
        s = a.mean(dim=-1, keepdim=True).clamp(min=1e-8) * self.tau
        return torch.clamp(torch.round(w / s), -1.0, 1.0)

    @torch.no_grad()
    def export_int(self):
        """Integer arrays exactly as host/ref.py wants them.

        At nexp = 1 the names are the dense model's, unchanged, so every
        existing npz, packer and checker keeps working untouched."""
        import numpy as np
        out = {}
        out["emb"] = torch.clamp(torch.round(self.emb), -ACT_MAX, ACT_MAX).cpu().numpy().astype(np.int8)
        out["pos"] = torch.clamp(torch.round(self.pos), -ACT_MAX, ACT_MAX).cpu().numpy().astype(np.int8)
        for l in range(L):
            for nm, pl in (("Wq", self.Wq), ("Wk", self.Wk), ("Wv", self.Wv),
                           ("Wo", self.Wo)):
                out["L%d_%s" % (l, nm)] = self._tern(pl[l]).cpu().numpy().astype(np.int8)
            for nm, pl in (("W1", self.W1), ("W2", self.W2)):
                q = self._tern(pl[l]).cpu().numpy().astype(np.int8)
                if self.nexp == 1:
                    out["L%d_%s" % (l, nm)] = q[0]
                else:
                    for e in range(self.nexp):
                        out["L%d_%s_e%d" % (l, nm, e)] = q[e]
        q = self._tern(self.head).cpu().numpy().astype(np.int8)
        if self.nexp_head == 1:
            out["head"] = q[0]
        else:
            for e in range(self.nexp_head):
                out["head_e%d" % e] = q[e]
        if self.nexp > 1 or self.nexp_head > 1:
            out["_route"] = self.route.cpu().numpy().astype(np.int16)
            out["_moe"] = np.array([self.nexp, self.nexp_head], dtype=np.int16)
        return out
