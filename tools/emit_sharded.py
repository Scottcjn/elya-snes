#!/usr/bin/env python3
"""Emit the SNES cartridge's model image.

The 65816 gather kernel this port ships is `codesgn` from FINDINGS entry 6: the
weights are not a data stream that a loop walks, they ARE straight-line 65816
code, one `adc <off` or `sbc <off` per non-zero weight, with the gather index in
the instruction's own operand byte.  Measured at 33.09 wall master clocks per
MAC against 72.11 for the best data-driven gather on the same instrument.

So this file is a code generator, not a packer.  It emits

  out/model/weights.bin   FIVE weight programs, one per topic shard: 19
                          segments of straight-line 65816 each, one per
                          matrix, each ending in RTL, four banks per shard
  out/model/stubs.bin     the jump table set_shard copies into WRAM: for each
                          shard, one `jml long` per segment.  This is the
                          whole shard-switching mechanism -- 76 bytes of WRAM
                          and one extra JML per matrix per token
  out/model/mdata.bin     each shard's biased embedding and positional table,
                          one 32 KiB bank each
  out/model/tables.bin    a verbatim image of WRAM $0400-$12FF: the two
                          self-modified gather chains and every lookup table
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
# fixed cartridge geometry -- rom/lorom1m.cfg and this file must agree, and
# every constant below is derived from NSHARD so they cannot drift.
#
# The cartridge holds FIVE whole models, one per train/corpus.py topic.  An
# expert on this port is not a repointed data stream -- FINDINGS entry 6
# measured the cheapest gather as no gather, so the weights ARE straight-line
# 65816 -- which means a shard is a different CODE BLOB in different banks,
# reached through a jump table that set_shard rewrites.  That is the whole
# mechanism; there is no router inside the model and no per-token routing.
# ---------------------------------------------------------------------------
BANKSZ = 0x8000                 # LoROM: one bank shows 32 KiB at $8000
NSHARD = 5                      # one per train/corpus.py TOPIC
WBANK0 = 0x01                   # first weight bank
NWBANK = 4                      # weight banks PER SHARD
MDBANK0 = WBANK0 + NSHARD * NWBANK      # $15: embedding + positional table
PTBANK = MDBANK0 + NSHARD               # $1A: softmax division table
GDBANK = PTBANK + 1                     # $1B: the game layer's data
NSEG = 19                       # L{0,1,2}.{Wq,Wk,Wv,Wo,W1,W2} + head
SEGJ = 0x0584                   # WRAM: NSEG four-byte `jml` stubs.  This sits
                                # in the gap between the QK gather chain (385
                                # bytes from $0400) and the AV chain at $0600,
                                # which is inside the region init_ram copies
                                # from tbl_img -- so set_shard has to run after
                                # init_ram, and rom/nn.s does.

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
            "weight program needs %d banks, rom/lorom1m.cfg reserves %d " \
            "per shard" \
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


def shard_paths():
    """The five npz files, in train/corpus.py's TOPICS order.

    SNES_SHARDS overrides for an experiment; the default is what ships.  The
    order is not cosmetic -- rom/game.inc's router scores topics in this order
    and set_shard indexes the bank set with the result, so a permutation here
    would route every question to the wrong model while every arithmetic check
    in the gate still passed."""
    sys.path.insert(0, os.path.join(ROOT, "train"))
    import corpus as C                                            # noqa: E402
    env = os.environ.get("SNES_SHARDS")
    if env:
        paths = env.split(",")
        if len(paths) != NSHARD:
            raise SystemExit("SNES_SHARDS lists %d models, the cartridge holds "
                             "%d" % (len(paths), NSHARD))
        return list(zip(C.TOPICS, [os.path.join(ROOT, p) if not
                                   os.path.isabs(p) else p for p in paths]))
    return [(t, os.path.join(ROOT, "model", "elya_shard_%s.npz" % t))
            for t in C.TOPICS]


def main():
    outdir = os.path.join(ROOT, "out", "model")
    os.makedirs(outdir, exist_ok=True)
    shards = shard_paths()
    lbl = sys.argv[1] if len(sys.argv) > 1 else ""
    fast = os.environ.get("SNES_FAST", "0") == "1"
    base = 0x80 if fast else 0x00
    ptab_addr = ((base + PTBANK) << 16) | 0x8000

    labels = parse_labels(lbl)
    handlers = {n: labels.get(n, 0x8000) for n in set(HANDLER.values())}
    resolved = bool(labels)

    weights = bytearray()
    mdata = bytearray()
    stubs = bytearray()
    segs0, nnz, nbank = [], [], []
    for si, (topic, npz) in enumerate(shards):
        m = ref.Model.from_npz(npz)
        # A mixture npz packs SILENTLY here, and wrongly: m.matrices(0) hands
        # back expert 0 and the other N-1 are discarded, so the cartridge would
        # be a dense model built from one sixty-fourth of the weights.  The NES
        # port carried the bank-chain machinery that made a mixture executable;
        # this port deleted it on purpose (rom/nn.s, "the header table and the
        # bank-chain machinery went with it") because its weights are
        # straight-line 65816 and not a data stream.  Topic sharding is a
        # DIFFERENT thing: five whole models chosen between at the ask menu,
        # not one model routing per token.  There is still no per-token expert
        # path in rom/nn.s to route to.
        #
        # The exactness gate would catch it - host/ref.py DOES understand _moe,
        # so the ROM and the reference would disagree - but "a checker
        # somewhere else notices" is not the same as refusing to build the
        # wrong thing.
        if m.moe:
            raise SystemExit(
                "%s is a mixture of %d experts and this port has no per-token "
                "expert path: rom/nn.s emits weights as straight-line code, so "
                "an expert is a different CODE BLOB in a different bank, not a "
                "repointed stream.  Refusing rather than packing expert 0 and "
                "calling it the model."
                % (npz, max(m.nexp, m.nexp_head)))
        em = Emitter(handlers, base, base + WBANK0 + si * NWBANK)
        for name, mat in m.matrices(0):
            short = name.split(".")[-1]
            em.segment(name, [ref.split_row(r) for r in mat], HANDLER[short])
        if len(em.segs) != NSEG:
            raise SystemExit("shard %s emitted %d segments, rom/nn.s dispatches "
                             "%d" % (topic, len(em.segs), NSEG))
        weights += em.image()
        nbank.append(len(em.banks))
        nnz.append(sum(len(p) + len(n) for _, mat in m.matrices(0)
                       for p, n in [ref.split_row(r) for r in mat]))
        if si == 0:
            segs0 = list(em.segs)
        # the jump table set_shard copies into WRAM: one `jml long` per segment
        for _name, addr in em.segs:
            stubs += bytes([0x5C, addr & 0xFF, (addr >> 8) & 0xFF,
                            (addr >> 16) & 0xFF])
        md = wtab([v + BIAS for row in m.emb for v in row])
        md += wtab([v + BIAS for row in m.pos for v in row])
        assert len(md) == (V * D + T * D) * 2, len(md)
        mdata += md + bytes(BANKSZ - len(md))

    open(os.path.join(outdir, "weights.bin"), "wb").write(bytes(weights))
    open(os.path.join(outdir, "mdata.bin"), "wb").write(bytes(mdata))
    open(os.path.join(outdir, "stubs.bin"), "wb").write(bytes(stubs))
    assert len(stubs) == NSHARD * NSEG * 4

    tbl = build_tables()
    open(os.path.join(outdir, "tables.bin"), "wb").write(bytes(tbl))

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
        w_("NSHARD    = %d\n" % NSHARD)
        w_("NSEG      = %d\n" % NSEG)
        w_("SEGJ      = $%04X\n" % SEGJ)
        w_("MDBANK    = $%02X\n" % (base + MDBANK0))
        w_("GDBASE    = $%02X0000\n" % (base + GDBANK))
        # Shard 0's segment addresses, for the listing and for anything that
        # wants to name a segment; the ROM reaches every shard through SEGJ.
        for name, addr in segs0:
            w_("SEG_%-9s = $%06X\n" % (name.replace(".", "_"), addr))
    if resolved:
        print("emit: handlers resolved %s" %
              " ".join("%s=$%04X" % kv for kv in sorted(handlers.items())))
    else:
        print("emit: PASS 1 (handler addresses not yet known)")
    print("emit: %d shards x %d banks = %d KiB of weight program, "
          "nnz %s, ptab %d B, mdata %d KiB"
          % (NSHARD, max(nbank), len(weights) // 1024,
             "/".join(str(x) for x in nnz), len(ptab), len(mdata) // 1024))
    for (topic, npz), nb, nz in zip(shards, nbank, nnz):
        print("emit:   shard %-9s bank $%02X  %d banks  %6d nnz  %s"
              % (topic, base + WBANK0 + shards.index((topic, npz)) * NWBANK,
                 nb, nz, os.path.relpath(npz, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
