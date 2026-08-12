#!/usr/bin/env python3
"""Emit the SNES cartridge's model image.

The 65816 gather kernel this port ships is `codesgn` from FINDINGS entry 6: the
weights are not a data stream that a loop walks, they ARE straight-line 65816
code, one `adc <off` or `sbc <off` per non-zero weight, with the gather index in
the instruction's own operand byte.  Measured at 33.09 wall master clocks per
MAC against 72.11 for the best data-driven gather on the same instrument.

So this file is a code generator, not a packer.  It emits

  out/model/weights.bin   the weight program: 19 segments of straight-line
                          65816, one per matrix, each ending in RTL
  out/model/tables.bin    a verbatim image of WRAM $0400-$13FF: the two
                          self-modified gather chains and every lookup table
  out/model/embed.bin     biased embedding table, 16-bit
  out/model/pos.bin       biased positional table, 16-bit
  out/model/ptab.bin      the exact softmax normaliser's division table
  out/model/model.inc     every constant rom/nn.s and this file must agree on

host/ref.py is the specification.  Every table here is derived from it rather
than restated, so the two cannot drift.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))

os.environ.setdefault("NES_T", "20")
import ref                                                        # noqa: E402

# ---------------------------------------------------------------------------
# fixed cartridge geometry
# ---------------------------------------------------------------------------
BANKSZ = 0x8000                 # LoROM: one bank shows 32 KiB at $8000
WBANK0 = 0x01                   # first weight bank
NWBANK = 4                      # weight banks reserved by rom/lorom256.cfg
PTBANK = 0x05                   # bank holding the softmax division table

OFFSET = 4096                   # keeps a row's accumulator in 0..65535 with no
                                # carry out of any adc and no borrow out of any
                                # sbc, which is what lets the carry flag stay
                                # untouched for a whole run.  Bound: the largest
                                # row has 128 inputs, so the accumulator moves
                                # at most 128*14 + 7*128 = 2688 either way.

# WRAM addresses (bank $00 low RAM, mirrored at $7E:0000-$1FFF)
ACTA   = 0x0200                 # biased residual stream x, 16-bit
ACTB   = 0x0300                 # biased attention output / biased hidden
QKCODE = 0x0400                 # 64 gather slots, self-modified per layer
AVCODE = 0x0600                 # NCTX gather slots, self-modified per head
QKV    = 0x0680                 # jmp vector: which half of QKCODE this head uses
SCOR   = 0x0700
BBUF   = 0x0740
MUL16  = 0x0800                 # 512 B, page aligned: row = MUL16 + (q<<5)
PV16   = 0x0A00                 # 512 B, page aligned: row = PV16  + (p<<5)
TQMROW = 0x0C00                 # Wq   -> MUL16 row address
TQK2   = 0x0C80                 # Wk   -> k nibble * 2
TQV2   = 0x0D00                 # Wv   -> v nibble * 2
TQHDN  = 0x0D80                 # W1   -> relu(q) + 7
TQO2   = 0x0E00                 # Wo   -> q + 7
TQO3   = 0x0E80                 # W2   -> q + 7        (W2_SHIFT)
TCLAMP = 0x0F80                 # clamp7(s - 14) + 7, s in 0..28
TQAV   = 0x1000                 # attention value -> quant(.,AV_SHIFT) + 7
EXPV   = 0x1100                 # EXPTAB as words
SMB    = 0x1200                 # score-difference low byte -> exp bucket index
TBLEND = 0x1300

# $7E-only arrays
EMB    = 0x2000                 # V*D words, biased
POS    = 0x4000                 # T*D words, biased
VB     = 0x5000                 # NCTX * 256 bytes, the value cache
KT     = 0x8000                 # 64 *  64 bytes, the key cache, transposed



T  = ref.T
V, D, L, H, DH, F = ref.V, ref.D, ref.L, ref.H, ref.DH, ref.F
K_SHIFT, W2_SHIFT, AV_SHIFT = ref.K_SHIFT, ref.W2_SHIFT, ref.AV_SHIFT
BIAS = ref.BIAS

assert ref.SM_NORM == "exact", "the exact normaliser is the shipping one"
assert ref.SM_TARGET == 8
assert H == 2 and DH == 32


def q_of(t, k):
    """inverse of the handler's index: t = acc + (7<<k) -> quant(acc,k)"""
    return (t >> k) - 7


def wtab(vals):
    b = bytearray()
    for v in vals:
        v &= 0xFFFF
        b += bytes([v & 0xFF, v >> 8])
    return b


# ---------------------------------------------------------------------------
# the WRAM image
# ---------------------------------------------------------------------------
def build_tables():
    img = bytearray(TBLEND - QKCODE)

    def put(addr, data):
        o = addr - QKCODE
        assert o >= 0 and o + len(data) <= len(img), hex(addr)
        assert all(x == 0 for x in img[o:o + len(data)]), \
            "table overlap at $%04X" % addr
        img[o:o + len(data)] = data

    # -- the QK gather chain: 64 slots, uniform 6-byte stride ---------------
    #    ldx KT + d*64, y       y = l*T + t, 8-bit index registers
    #    adc <patched>, x       row = MUL16 + (q_d << 5), written by H_Q
    # Head 0 runs slots 0..31 and is terminated by poking RTS over slot 32's
    # opcode; head 1 enters at slot 32 with that opcode restored.
    ch = bytearray()
    for d in range(64):
        a = KT + d * 64
        ch += bytes([0xBE, a & 0xFF, a >> 8])       # ldx abs,y
        ch += bytes([0x7D, 0x00, 0x00])             # adc abs,x  (patched)
    ch += bytes([0x60])                             # rts
    put(QKCODE, ch)

    # -- the AV gather chain: T slots, same shape ---------------------------
    #    ldx VB + t*256, y      y = l*64 + d, 8-bit index registers
    #    adc <patched>, x       row = PV16 + (p_t << 5), written per softmax
    av = bytearray()
    for t in range(T):
        a = VB + t * 256
        av += bytes([0xBE, a & 0xFF, a >> 8])
        av += bytes([0x7D, 0x00, 0x00])
    av += bytes([0x60])
    put(AVCODE, av)

    # -- which half of QKCODE the current head uses -------------------------
    put(QKV, bytes([0x4C, QKCODE & 0xFF, QKCODE >> 8]))

    # -- product tables, 16-bit so that a 16-bit accumulator can `adc` them --
    put(MUL16, wtab([ref.MUL[(q << 4) | k] for q in range(16)
                     for k in range(16)]))
    put(PV16, wtab([ref.PMUL[(p << 4) | v] for p in range(16)
                    for v in range(16)]))

    # -- the row handlers' output tables ------------------------------------
    n2, n3 = 15 << K_SHIFT, 15 << W2_SHIFT
    put(TQMROW, wtab([MUL16 + ((q_of(t, K_SHIFT) & 15) << 5)
                      for t in range(n2)]))
    put(TQK2, wtab([(q_of(t, K_SHIFT) & 15) * 2 for t in range(n2)]))
    put(TQV2, wtab([(q_of(t, K_SHIFT) & 15) * 2 for t in range(n2)]))
    put(TQHDN, wtab([max(q_of(t, K_SHIFT), 0) + BIAS for t in range(n2)]))
    put(TQO2, wtab([q_of(t, K_SHIFT) + BIAS for t in range(n2)]))
    put(TQO3, wtab([q_of(t, W2_SHIFT) + BIAS for t in range(n3)]))
    put(TCLAMP, wtab([ref.clamp7(s - 2 * BIAS) + BIAS for s in range(29)]))
    put(TQAV, wtab([q_of(t, AV_SHIFT) + BIAS for t in range(15 << AV_SHIFT)]))
    put(EXPV, wtab(ref.EXPTAB))

    # -- score difference -> exp bucket.  Valid for diff in [-256,-1]; the
    #    driver handles diff == 0 and diff <= -257 as constants.
    smb = bytearray()
    for b in range(256):
        d = (b - 256) >> ref.SM_SHIFT
        smb.append(max(d, -(ref.EXP_N - 1)) + ref.EXP_N - 1)
    put(SMB, smb)
    return img


def build_ptab():
    """PTAB[(S<<4) | b] = (min(EXPTAB[b]*8 // S, 7) << 5), the PV16 row offset.

    The exact normaliser is p = min(e*SM_TARGET // S, PMAX).  On the 6502 that
    cost a table row chosen from (kk, S>>kk); here the whole (S, bucket) pair
    indexes one flat table, because the SNES has the ROM to spend and S is
    bounded by T * max(EXPTAB) = %d.
    """
    smax = T * max(ref.EXPTAB)
    tab = bytearray((smax + 1) * 16 + 2)
    for s in range(1, smax + 1):
        for b, e in enumerate(ref.EXPTAB):
            tab[(s << 4) | b] = min(e * ref.SM_TARGET // s, ref.PMAX) << 5
    return tab, smax


build_ptab.__doc__ %= 0


# ---------------------------------------------------------------------------
# the weight program
# ---------------------------------------------------------------------------
HANDLER = {"Wq": "H_Q", "Wk": "H_K", "Wv": "H_V", "Wo": "H_O",
           "W1": "H_1", "W2": "H_W2", "head": "H_HEAD"}


class Emitter:
    def __init__(self, handlers, hbank, wbank0):
        self.h = handlers
        self.hbank = hbank
        self.wbank0 = wbank0
        self.banks = [bytearray()]
        self.segs = []

    def _room(self, n):
        return BANKSZ - len(self.banks[-1]) >= n

    def _newbank(self):
        nxt = self.wbank0 + len(self.banks)
        self.banks[-1] += bytes([0x5C, 0x00, 0x80, nxt])       # jml nxt:8000
        self.banks.append(bytearray())

    def row(self, pos, neg, handler):
        k = (OFFSET - BIAS * (len(pos) - len(neg))) & 0xFFFF
        b = bytearray([0xA9, k & 0xFF, k >> 8])                # lda #imm16
        for i in pos:
            b += bytes([0x65, 2 * i])                          # adc <2i
        if neg:
            b.append(0x38)                                     # sec
            for i in neg:
                b += bytes([0xE5, 2 * i])                      # sbc <2i
        a = self.h[handler]
        b += bytes([0x22, a & 0xFF, (a >> 8) & 0xFF, self.hbank])   # jsl
        # +5: the segment's closing RTL, and the JML that a bank change costs
        if not self._room(len(b) + 5):
            self._newbank()
        self.banks[-1] += b

    def segment(self, name, rows, handler):
        if not self._room(300):
            self._newbank()
        addr = ((self.wbank0 + len(self.banks) - 1) << 16) | \
               (0x8000 + len(self.banks[-1]))
        for p, n in rows:
            self.row(p, n, handler)
        self.banks[-1].append(0x6B)                            # rtl
        self.segs.append((name, addr))
        return addr

    def image(self):
        assert len(self.banks) <= NWBANK, \
            "weight program needs %d banks, rom/lorom256.cfg reserves %d" \
            % (len(self.banks), NWBANK)
        out = bytearray()
        for b in self.banks:
            out += b + bytes(BANKSZ - len(b))
        return out + bytes((NWBANK - len(self.banks)) * BANKSZ)


def parse_labels(path):
    """ld65 -Ln output: `al 00A123 .name`"""
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        f = line.split()
        if len(f) == 3 and f[0] == "al":
            out[f[2].lstrip(".")] = int(f[1], 16)
    return out


def main():
    outdir = os.path.join(ROOT, "out", "model")
    os.makedirs(outdir, exist_ok=True)
    npz = ref.default_weights()
    lbl = sys.argv[1] if len(sys.argv) > 1 else ""
    fast = os.environ.get("SNES_FAST", "0") == "1"
    base = 0x80 if fast else 0x00
    ptab_addr = ((base + PTBANK) << 16) | 0x8000

    labels = parse_labels(lbl)
    handlers = {n: labels.get(n, 0x8000) for n in set(HANDLER.values())}
    resolved = bool(labels)

    m = ref.Model.from_npz(npz)
    em = Emitter(handlers, base, base + WBANK0)
    for name, mat in m.matrices(0):
        short = name.split(".")[-1]
        em.segment(name, [ref.split_row(r) for r in mat], HANDLER[short])
    w = em.image()
    open(os.path.join(outdir, "weights.bin"), "wb").write(bytes(w))

    tbl = build_tables()
    open(os.path.join(outdir, "tables.bin"), "wb").write(bytes(tbl))

    emb = wtab([v + BIAS for row in m.emb for v in row])
    pos = wtab([v + BIAS for row in m.pos for v in row])
    open(os.path.join(outdir, "embed.bin"), "wb").write(bytes(emb))
    open(os.path.join(outdir, "pos.bin"), "wb").write(bytes(pos))

    ptab, smax = build_ptab()
    assert len(ptab) <= BANKSZ, len(ptab)
    open(os.path.join(outdir, "ptab.bin"), "wb").write(bytes(ptab))

    ngen = int(os.environ.get("SNES_NGEN", str(T - 1)))
    seed = int(os.environ.get("NES_SEED_TOK", "1"))

    with open(os.path.join(outdir, "model.inc"), "w") as f:
        w_ = f.write
        w_("; GENERATED by tools/emit.py -- do not edit\n")
        w_("NCTX      = %d\n" % T)
        w_("NGEN      = %d\n" % ngen)
        w_("SEEDTOK   = %d\n" % seed)
        w_("NSEEDS    = %d\n" % int(os.environ.get("SNES_NSEEDS", str(V))))
        w_("NVOCAB    = %d\n" % V)
        w_("NDMODEL   = %d\n" % D)
        w_("NLAYER    = %d\n" % L)
        w_("NDHEAD    = %d\n" % DH)
        w_("NHEADS    = %d\n" % H)
        w_("NFF       = %d\n" % F)
        w_("SUBK      = %d\n" % (OFFSET - (BIAS << K_SHIFT)))
        w_("SUBW2     = %d\n" % (OFFSET - (BIAS << W2_SHIFT)))
        w_("LIMK      = %d\n" % (15 << K_SHIFT))
        w_("LIMW2     = %d\n" % (15 << W2_SHIFT))
        w_("LIMAV     = %d\n" % (15 << AV_SHIFT))
        w_("AVOFF     = %d\n" % (BIAS << AV_SHIFT))
        w_("PVBIAS    = %d\n" % ref.PBIAS)
        w_("EXPTOPB   = %d\n" % (ref.EXP_N - 1))
        w_("SMSHIFT   = %d\n" % ref.SM_SHIFT)
        w_("BIASV     = %d\n" % BIAS)
        w_("SMAXV     = %d\n" % smax)
        w_("WBANKS    = %d\n" % NWBANK)
        w_("PTABADDR  = $%06X\n" % ptab_addr)
        w_("HBANK     = $%02X\n" % base)
        for name, addr in em.segs:
            w_("SEG_%-9s = $%06X\n" % (name.replace(".", "_"), addr))
    if resolved:
        print("emit: handlers resolved %s" %
              " ".join("%s=$%04X" % kv for kv in sorted(handlers.items())))
    else:
        print("emit: PASS 1 (handler addresses not yet known)")
    print("emit: %d weight banks, %d rows, %d nnz, ptab %d B"
          % (len(em.banks), sum(len(mat) for _, mat in m.matrices(0)),
             sum(len(p) + len(n) for _, mat in m.matrices(0)
                 for p, n in [ref.split_row(r) for r in mat]),
             len(ptab)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
