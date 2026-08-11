; ---------------------------------------------------------------------------
; nn.s -- the ternary transformer forward pass on a stock 5A22.
;
; The model is the NES port's: V=64, D=64, L=3, H=2, d_head=32, F=128, T=20,
; 102,400 ternary weights of which 52,764 are non-zero, 4-bit activations, the
; exact softmax normaliser.  host/ref.py is the specification and this ROM is
; verified against it bit for bit.
;
; WHAT THE 16-BIT ACCUMULATOR CHANGED (see FINDINGS entry 6, all measured)
;   * The NES's block-16 structure is gone.  128*14 = 1,792 cannot carry out of
;     a word, so a whole row accumulates with no `clc` anywhere and no fold.
;   * The weight stream is gone.  The cheapest gather on this machine is no
;     gather: the index lives in the operand byte of the accumulate, so the
;     "stream" is straight-line 65816 emitted by tools/emit.py.  33.09 wall
;     master clocks per MAC against 72.11 for the best data-driven form.
;   * The header table and the bank-chain machinery went with it.
;   What is LEFT of the NES design is the two attention kernels, which keep its
;   shape exactly -- self-modified unrolled chains with the operand patched per
;   dimension (QK) and per position (AV) -- because attention operands are
;   dynamic and cannot be baked into a cartridge.
;
; REGISTER CONTRACT ACROSS A WEIGHT SEGMENT
;   A   the row accumulator, 16 bits, biased by OFFSET so that no adc carries
;       out and no sbc borrows, which is what lets the carry flag stay
;       untouched for a whole row
;   Y   the row handler's output index; the weight program never touches it
;   X   scratch, owned by the handler
;   D   the activation array the segment reads (ACTA or ACTB)
;   DBR $7E for the whole run; every array lives there or in its bank-$00
;       mirror, so one data bank covers the lot
;
; The two gather chains run with 8-BIT index registers -- FINDINGS entry 6
; measured that shape as 20% cheaper than the 16-bit one -- and everything
; else runs with 16-bit ones.
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .include "model.inc"
        .p816
        .smart -

; ---- WRAM map, bank $00 low RAM (mirrored at $7E:0000-$1FFF) ---------------
ACTA    = $0200         ; biased residual stream x        (64 words)
ACTB    = $0300         ; biased attention out / hidden  (128 words)
QKCODE  = $0400         ; 64 self-modified gather slots, 6 bytes each
AVCODE  = $0600         ; NCTX self-modified gather slots
QKV     = $0680         ; jmp vector: which half of QKCODE this head uses
SCOR    = $0700         ; NCTX raw attention scores, words
BBUF    = $0740         ; NCTX exp bucket indices, words
BEST    = $0768
BESTI   = $076A
MUL16   = $0800         ; 512 B, page aligned; row = MUL16 + (q<<5)
PV16    = $0A00         ; 512 B, page aligned; row = PV16 + (p<<5)
TQMROW  = $0C00         ; Wq  -> the MUL16 row address for this dimension
TQK2    = $0C80         ; Wk  -> key nibble * 2
TQV2    = $0D00         ; Wv  -> value nibble * 2
TQHDN   = $0D80         ; W1  -> relu(q) + 7
TQO2    = $0E00         ; Wo  -> q + 7
TQO3    = $0E80         ; W2  -> q + 7   (W2_SHIFT)
TCLAMP  = $0F80         ; clamp7(s - 14) + 7
TQAV    = $1000         ; attention value -> quant(.,AV_SHIFT) + 7
EXPV    = $1100         ; the exp table, as words
SMB     = $1200         ; score-difference low byte -> exp bucket
TBLTOP  = $1300

; ---- $7E only -------------------------------------------------------------
EMBW    = $2000         ; NVOCAB * NDMODEL words, biased
POSW    = $4000         ; NCTX  * NDMODEL words, biased
VB      = $5000         ; NCTX * 256 bytes, the value cache  (t stride 256)
KT      = $8000         ; 64 * 64 bytes, the key cache       (d stride 64)

; ---- direct page 0, the driver's own -------------------------------------
TOKP    = $00
POSP    = $02
LAYER   = $04
HEADV   = $06
TCNT    = $08           ; p+1, the live position count
TCUR    = $0A
EPTR    = $0C
PPTR    = $0E
SMAX    = $10
SSUM    = $12
TMPA    = $14
TMPB    = $16
JIDX    = $18
JB      = $1A
HOFF    = $1C           ; HEADV * 64, the ACTB byte offset
HDOFF   = $1E           ; HEADV * 32, the cache dimension offset
VYB     = $20           ; LAYER * 64,   the AV chain's layer base
LTB     = $22           ; LAYER * NCTX, the QK chain's layer base
SUBAV   = $24
NEXTT   = $26
S16     = $28           ; SSUM << 4
YARG    = $2A           ; the chain's 8-bit index, staged here
TB2     = $2C           ; (p+1)*2, the live byte count of the word arrays
SRAMB   = $36           ; SRAM offset this run's tokens are written to
SEEDN   = $38           ; SURVEY: which seed token is being run

FRAMES  = $076C         ; PROFILE: vblank counter, maintained by the NMI
; The profile instrument's scratch is ABSOLUTE, not direct page.  It is called
; from stage boundaries where D may still be pointing at an activation array,
; and a DP cell there would land inside the model's own activations and feed
; the forward pass timing-dependent garbage.  That is not hypothetical: it is
; what the first STAGEPROF build did, and the tell was tokens that changed from
; run to run under a wholly deterministic engine.
SMPX    = $0780         ; PROFILE: the next sample's SRAM offset
VSCR    = $0782         ; PROFILE: latched V
HSCR    = $0784         ; PROFILE: latched H
FSCR    = $0786         ; PROFILE: latched frame count
SMPY    = $0788         ; STAGEPROF: the stage sampler's own cursor

.ifdef FASTROM
MAPMODE = $30
.else
MAPMODE = $20
.endif

; ===========================================================================
; A row handler's common prologue: turn the biased accumulator into a table
; index, saturating exactly as host/ref.py's quant() does.
;   A = OFFSET + acc  ->  X = 2 * clamp(acc + (7<<k), 0, (15<<k)-1)
; ===========================================================================
.macro CLAMPT sub, lim
        .local lo, ok
        sec
        sbc #sub
        cmp #$8000              ; unsigned: anything from $8000 up is negative
        bcs lo
        cmp #lim
        bcc ok
        lda #(lim-1)
        bra ok
lo:     lda #$0000
ok:     asl a
        tax
.endmacro

; STAGEPROF samples the multi-frame clock at every stage boundary, overwriting
; the same SRAM block each token, so what survives is a breakdown of the LAST
; and most expensive position.  It is a no-op in every other build.
.macro STAGE
.ifdef STAGEPROF
        ; MUST be placed where D is the driver's own direct page: the sampler
        ; keeps its cursor there, and running it with D pointing at ACTA reads
        ; an activation as the SRAM offset and scribbles over both.
        jsr stagesmp
.endif
.endmacro

; ===========================================================================
; One layer.  Segment addresses are assembled constants and the 65816 has no
; indirect long call, so the layer loop is unrolled; three copies of this cost
; less ROM than a trampoline costs cycles.
; ===========================================================================
.macro DOLAYER l, sq, sk, sv, so, s1, s2
        ; Wq reads x; its handler patches the QK chain's 64 operands
        ldy #$0000
        lda #ACTA
        tcd
        clc
        jsl sq
        lda #$0000
        tcd
        STAGE
        ; Wk: Y = d*64 + (l*NCTX + p), stepping 64
        lda POSP
        clc
        adc #(l*NCTX)
        tay
        lda #ACTA
        tcd
        clc
        jsl sk
        lda #$0000
        tcd
        STAGE
        ; Wv: Y = p*256 + (l*64 + d), stepping 1
        lda POSP
        .repeat 8
        asl a
        .endrepeat
        clc
        adc #(l*64)
        tay
        lda #ACTA
        tcd
        clc
        jsl sv
        lda #$0000
        tcd
        STAGE
        lda #l
        sta LAYER
        jsr attention
        STAGE
        ; Wo reads ACTB (the attention output) and updates ACTA in place
        ldy #$0000
        lda #ACTB
        tcd
        clc
        jsl so
        lda #$0000
        tcd
        STAGE
        ; W1 reads ACTA (x) and writes the biased hidden layer to ACTB
        ldy #$0000
        lda #ACTA
        tcd
        clc
        jsl s1
        lda #$0000
        tcd
        STAGE
        ; W2 reads ACTB and updates ACTA in place
        ldy #$0000
        lda #ACTB
        tcd
        clc
        jsl s2
        lda #$0000
        tcd
        STAGE
.endmacro

        .segment "CODE"

; ===========================================================================
reset:
        sei
        clc
        xce
        .a16
        .i16
        rep #$38
        ldx #$01FF
        txs
        lda #$0000
        tcd
        sep #$20
        .a8
        lda #$8F
        sta INIDISP             ; forced blank; nothing is drawn, ever
        stz NMITIMEN
        stz $420B
        stz $420C
.ifdef FASTROM
        lda #$01
        sta MEMSEL
        jml $800000 + fast_entry
fast_entry:
.else
        stz MEMSEL
.endif
        lda #$7E
        pha
        plb                     ; DBR = $7E for the whole run
        rep #$30
        .a16
        .i16

        jsr init_ram

        sep #$20
        .a8
        lda #$45                ; 'E'
        sta f:SRAM+0
        lda #$4C                ; 'L'
        sta f:SRAM+1
        lda #$59                ; 'Y'
        sta f:SRAM+2
        lda #$41                ; 'A'
        sta f:SRAM+3
        lda #NGEN
        sta f:SRAM+4
        lda #SEEDTOK
        sta f:SRAM+5
        lda #NSEEDS
        sta f:SRAM+6
        lda #$00                ; DONE marker cleared
        sta f:SRAM+8
        sta f:SRAM+9
        sta f:SRAM+10
        sta f:SRAM+11
        rep #$30
        .a16
        .i16

.ifdef PROFILE
        jsr profile_calibrate   ; the multi-frame instrument's frame length
        stz a:FRAMES
        sep #$20
        .a8
        lda #$80
        sta f:NMITIMEN          ; vblank NMI on; the handler counts frames
        rep #$30
        .a16
        .i16
        cli
        lda #$0140
        sta a:SMPX
.endif

.ifdef SURVEY
        ; Every vocabulary symbol as a seed.  One seed and nineteen tokens is
        ; not a check -- on the sibling ports a short comparison passed three
        ; genuinely broken changes -- so the shipping gate is the whole survey.
        stz SEEDN
survey: lda SEEDN
        sta TOKP
        lda SEEDN
        sta TMPA
        asl a
        asl a
        clc
        adc TMPA                ; 5n
        asl a
        asl a                   ; 20n = NGEN+1 per seed
        clc
        adc #$0010
        sta SRAMB
        jsr generate
        lda SEEDN
        inc a
        sta SEEDN
        cmp #NSEEDS
        bne survey
.else
        lda #SEEDTOK            ; the single-seed path has to seed TOKP itself:
        sta TOKP                ; generate() stopped doing it when the survey
        lda #$0010              ; made the seed a parameter, and for three
        sta SRAMB               ; builds this line's absence let the cartridge
        jsr generate            ; start from whatever WRAM happened to hold
.endif
.ifdef PROFILE
        sep #$20
        .a8
        lda #$00
        sta f:NMITIMEN
        rep #$30
        .a16
        .i16
.endif

        sep #$20
        .a8
        lda #$44
        sta f:SRAM+8
        lda #$4F
        sta f:SRAM+9
        lda #$4E
        sta f:SRAM+10
        lda #$45
        sta f:SRAM+11
        rep #$30
        .a16
        .i16
halt:   bra halt

; ---------------------------------------------------------------------------
; Copy the WRAM image, the embedding and the positional table out of ROM.
; The WRAM image is a verbatim picture of $0400-$12FF -- both self-modified
; gather chains and every lookup table -- laid out by tools/emit.py, so the
; addresses in that file and the addresses in this one cannot drift apart.
; ---------------------------------------------------------------------------
.proc init_ram
        .a16
        .i16
        ldx #$0000
:       lda f:tbl_img, x
        sta a:QKCODE, x
        inx
        inx
        cpx #(TBLTOP - QKCODE)
        bne :-
        ldx #$0000
:       lda f:emb_img, x
        sta a:EMBW, x
        inx
        inx
        cpx #(NVOCAB * NDMODEL * 2)
        bne :-
        ldx #$0000
:       lda f:pos_img, x
        sta a:POSW, x
        inx
        inx
        cpx #(NCTX * NDMODEL * 2)
        bne :-
        rts
.endproc

; ===========================================================================
; the generation loop: free-run greedy from one seed token, exactly as
; host/ref.py's generate() does
; ===========================================================================
.proc generate
        .a16
        .i16
        stz POSP
        ldx SRAMB
        sep #$20
        .a8
        lda TOKP
        sta f:SRAM, x           ; token 0 is the seed
        rep #$30
        .a16
        .i16
loop:
.ifdef PROFILE
        jsr sample
.endif
        jsr step
        lda NEXTT
        sta TOKP
        lda POSP
        inc a
        clc
        adc SRAMB
        tax
        sep #$20
        .a8
        lda TOKP
        sta f:SRAM, x           ; token p+1, written as it is produced so a
        rep #$30                ; partial run is still readable
        .a16
        .i16
        lda POSP
        inc a
        sta POSP
        cmp #NGEN
        bne loop
.ifdef PROFILE
        jsr sample
.endif
        rts
.endproc

; ===========================================================================
; one forward pass: token TOKP at position POSP -> NEXTT
; ===========================================================================
.proc step
        .a16
        .i16
.ifdef STAGEPROF
        lda #$0400              ; the stage block is rewritten every token, so
        sta a:SMPY                ; what survives is the LAST and dearest position
        jsr stagesmp
.endif
        ; ---- x = clamp7(emb[tok] + pos[p]) + 7, into ACTA ------------------
        ; Both tables are stored pre-biased, so their sum is x+14 and the very
        ; same TCLAMP the residual adds use turns that into clamp7(x)+7.
        lda TOKP
        .repeat 7
        asl a
        .endrepeat
        clc
        adc #EMBW
        sta EPTR
        lda POSP
        .repeat 7
        asl a
        .endrepeat
        clc
        adc #POSW
        sta PPTR
        ldy #$0000
xloop:  lda (EPTR), y
        clc
        adc (PPTR), y
        asl a
        tax
        lda a:TCLAMP, x
        sta a:ACTA, y
        iny
        iny
        cpy #(NDMODEL * 2)
        bne xloop
        STAGE                   ; stage 0 closes here: the embedding lookup

        lda POSP
        inc a
        sta TCNT                ; p+1 live positions
        asl a
        sta TB2

        ; ---- The AV chain runs slots 0..p.  Restore slot p's opcode and put
        ;      an RTS over slot p+1's: two byte writes a token, which is why
        ;      this port has no chain-entry table.
        lda POSP
        beq norestore
        jsr mul6
        tax
        sep #$20
        .a8
        lda #$BE                ; ldx abs,y
        sta a:AVCODE, x
        rep #$20
        .a16
norestore:
        lda POSP
        inc a
        cmp #NCTX
        bcs noterm
        jsr mul6
        tax
        sep #$20
        .a8
        lda #$60                ; rts
        sta a:AVCODE, x
        rep #$20
        .a16
noterm:

        ; ---- SUBAV = PVBIAS*(p+1) - (7<<AV_SHIFT): the whole AV correction
        ;      folded into one subtraction per output element
        lda TCNT
        sta TMPA
        asl a
        asl a
        sta TMPB                ; 4n
        asl a                   ; 8n
        clc
        adc TMPB                ; 12n
        clc
        adc TMPA                ; 13n = PVBIAS*n
        sec
        sbc #AVOFF
        sta SUBAV

        DOLAYER 0, SEG_L0_Wq, SEG_L0_Wk, SEG_L0_Wv, SEG_L0_Wo, SEG_L0_W1, SEG_L0_W2
        DOLAYER 1, SEG_L1_Wq, SEG_L1_Wk, SEG_L1_Wv, SEG_L1_Wo, SEG_L1_W1, SEG_L1_W2
        DOLAYER 2, SEG_L2_Wq, SEG_L2_Wk, SEG_L2_Wv, SEG_L2_Wo, SEG_L2_W1, SEG_L2_W2

        ; ---- output head: greedy argmax, ties to the lowest index ----------
        stz BEST
        stz BESTI
        ldy #$0000
        lda #ACTA
        tcd
        clc
        jsl SEG_head
        lda #$0000
        tcd
        STAGE
        lda a:BESTI
        lsr a
        sta NEXTT
        rts
.endproc

; A * 6, the AV chain's slot stride
.proc mul6
        .a16
        .i16
        sta TMPB
        asl a
        clc
        adc TMPB
        asl a
        rts
.endproc

; ===========================================================================
; attention for one layer.  LAYER is set by the caller.
; ===========================================================================
.proc attention
        .a16
        .i16
        lda LAYER
        .repeat 6
        asl a
        .endrepeat
        sta VYB                 ; LAYER * 64
        lda LAYER
        sta TMPB
        lda #$0000
        ldx #NCTX
:       clc
        adc TMPB
        dex
        bne :-
        sta LTB                 ; LAYER * NCTX

        stz HEADV
hloop:
        ; -- head 0 runs QK slots 0..31 and is stopped by an RTS written over
        ;    slot 32's opcode; head 1 starts at that slot with it restored.
        lda HEADV
        bne h1
        sep #$20
        .a8
        lda #$60
        sta a:QKCODE + 32*6
        rep #$20
        .a16
        lda #QKCODE
        bra setv
h1:     sep #$20
        .a8
        lda #$BE
        sta a:QKCODE + 32*6
        rep #$20
        .a16
        lda #(QKCODE + 32*6)
setv:   sta a:QKV+1

        lda HEADV
        .repeat 6
        asl a
        .endrepeat
        sta HOFF                ; HEADV * 64
        lda HEADV
        .repeat 5
        asl a
        .endrepeat
        sta HDOFF               ; HEADV * 32

        ; ---- scores: one QK chain call per live position -------------------
        stz TCUR
sloop:  lda TCUR
        clc
        adc LTB
        sta YARG
        sep #$10
        .i8
        ldy YARG
        lda #$0000
        clc
        jsr QKV
        rep #$10
        .i16
        pha
        lda TCUR
        asl a
        tax
        pla
        sta a:SCOR, x
        lda TCUR
        inc a
        sta TCUR
        cmp TCNT
        bne sloop

        ; ---- max, unsigned: every score is in 0..800 ----------------------
        lda a:SCOR
        sta SMAX
        ldx #$0002
mloop:  cpx TB2
        beq mdone
        lda a:SCOR, x
        cmp SMAX
        bcc :+
        sta SMAX
:       inx
        inx
        bra mloop
mdone:

        ; ---- exp buckets and their sum ------------------------------------
        stz SSUM
        ldy #$0000
eloop:  lda a:SCOR, y
        sec
        sbc SMAX                ; <= 0
        beq etop
        cmp #$FF00              ; below -256 the exp table has bottomed out
        bcc ezero
        and #$00FF
        tax
        sep #$20
        .a8
        lda a:SMB, x
        rep #$20
        .a16
        and #$00FF
        bra ehave
etop:   lda #EXPTOPB
        bra ehave
ezero:  lda #$0000
ehave:  sta a:BBUF, y
        asl a
        tax
        lda a:EXPV, x
        clc
        adc SSUM
        sta SSUM
        iny
        iny
        cpy TB2
        bne eloop

        ; ---- the exact normaliser.  p_t = min(e_t*8 / S, 7) is ONE flat
        ;      table lookup here: S is bounded by NCTX*64 and the exp table has
        ;      EXPTOPB+1 entries, so (S<<4)|bucket indexes the whole division.
        ;      The result is written straight into the AV chain as a PV16 row.
        lda SSUM
        .repeat 4
        asl a
        .endrepeat
        sta S16
        ldy #$0000
ploop:  tya
        sta TMPB
        asl a
        clc
        adc TMPB
        clc
        adc #(AVCODE + 4)
        tax                     ; the operand word of slot t
        lda a:BBUF, y
        clc
        adc S16
        phx
        tax
        lda f:PTABADDR, x
        and #$00FF
        ora #PV16
        plx
        sta a:$0000, x
        iny
        iny
        cpy TB2
        bne ploop

        ; ---- AV: one chain call per output dimension ----------------------
        stz JIDX
        lda HOFF
        sta JB
aloop:  lda JIDX
        clc
        adc VYB
        clc
        adc HDOFF
        sta YARG
        sep #$10
        .i8
        ldy YARG
        lda #$0000
        clc
        jsr AVCODE
        rep #$10
        .i16
        sec
        sbc SUBAV
        cmp #$8000
        bcs alo
        cmp #LIMAV
        bcc aok
        lda #(LIMAV-1)
        bra aok
alo:    lda #$0000
aok:    asl a
        tax
        lda a:TQAV, x
        ldx JB
        sta a:ACTB, x
        lda JB
        inc a
        inc a
        sta JB
        lda JIDX
        inc a
        sta JIDX
        cmp #NDHEAD
        bne aloop

        lda HEADV
        inc a
        sta HEADV
        cmp #NHEADS
        beq :+
        brl hloop
:       rts
.endproc

; ===========================================================================
; the seven row handlers.  Entered by JSL from the weight program with the
; row's biased accumulator in A and its output index in Y; each must return
; with carry CLEAR, because the next row opens with a run of `adc`.
; ===========================================================================
        .export H_Q, H_K, H_V, H_O, H_1, H_W2, H_HEAD

; Wq: the query nibble becomes the MUL16 row address that this dimension's QK
; slot will accumulate through.  Patching the chain IS the store.
H_Q:
        .a16
        .i16
        CLAMPT SUBK, LIMK
        lda a:TQMROW, x
        sta a:QKCODE + 4, y
        tya
        clc
        adc #6
        tay
        clc
        rtl

; Wk: the key cache is transposed -- KT[d][l*T+t], 64-byte d stride -- so the
; QK chain reaches it with an assembled absolute base per dimension.
H_K:
        .a16
        .i16
        CLAMPT SUBK, LIMK
        lda a:TQK2, x
        sep #$20
        .a8
        sta a:KT, y
        rep #$20
        .a16
        tya
        clc
        adc #64
        tay
        clc
        rtl

; Wv: the value cache is NOT transposed -- VB[t][l*64+d], 256-byte t stride --
; because the AV chain walks t for one fixed dimension.
H_V:
        .a16
        .i16
        CLAMPT SUBK, LIMK
        lda a:TQV2, x
        sep #$20
        .a8
        sta a:VB, y
        rep #$20
        .a16
        iny
        clc
        rtl

; Wo and W2 both land on the residual stream, so the quantise and the residual
; add are one handler: ACTA holds x+7, the table holds o+7, and TCLAMP folds
; the clamp and the re-bias into the same lookup the embedding sum uses.
H_O:
        .a16
        .i16
        CLAMPT SUBK, LIMK
        lda a:TQO2, x
        clc
        adc a:ACTA, y
        asl a
        tax
        lda a:TCLAMP, x
        sta a:ACTA, y
        iny
        iny
        clc
        rtl

H_W2:
        .a16
        .i16
        CLAMPT SUBW2, LIMW2
        lda a:TQO3, x
        clc
        adc a:ACTA, y
        asl a
        tax
        lda a:TCLAMP, x
        sta a:ACTA, y
        iny
        iny
        clc
        rtl

; W1: the relu and the +7 bias are both in the table, so the hidden layer
; lands already in the form W2's direct page wants to read.
H_1:
        .a16
        .i16
        CLAMPT SUBK, LIMK
        lda a:TQHDN, x
        sta a:ACTB, y
        iny
        iny
        clc
        rtl

; head: no quantisation at all.  A is OFFSET + logit and OFFSET is the same for
; every row, so an UNSIGNED compare orders the logits exactly.  Only strictly
; greater replaces, so ties go to the lowest index -- as host/ref.py does.
H_HEAD:
        .a16
        .i16
        cmp a:BEST
        beq @s
        bcc @s
        sta a:BEST
        sty a:BESTI
@s:     iny
        iny
        clc
        rtl

.ifdef PROFILE
; The multi-frame instrument.  Its cost is NOT an error term: the frame length
; is calibrated with this same handler running, so whatever it steals per frame
; is already subtracted from the calibrated frame length.  See tools/prof_nn.py.
; DBR is $7E for the whole run, so RDNMI has to be reached with a LONG read --
; an absolute one lands in WRAM, the vblank flag is never cleared, and the CPU
; re-enters the handler forever.  That is exactly what the first build did.
nmi:    php
        rep #$30
        .a16
        .i16
        pha
        sep #$20
        .a8
        lda f:$004210           ; clear the vblank flag
        rep #$20
        .a16
        lda a:FRAMES
        inc a
        sta a:FRAMES
        pla
        plp
        rti
        .a16
        .i16
.else
nmi:    rti
.endif
irq:    rti

.ifdef STAGEPROF
.proc stagesmp
        .a16
        .i16
.ifdef STAGENULL
        rts                     ; bisect: the call sites stay, the sampling goes
.else
        lda a:SMPX
        pha
        lda a:SMPY
        sta a:SMPX
        jsr sample
        lda a:SMPX
        sta a:SMPY
        pla
        sta a:SMPX
        rts
.endif
.endproc
.endif

.ifdef PROFILE
; ---------------------------------------------------------------------------
; PROFILE: sample (frames, V, H) into SRAM.  FRAMES is re-read after the latch
; and the sample retried if it moved, because a counter that ticks inside the
; read is exactly how entry 2 of FINDINGS produced a plausible wrong number.
; ---------------------------------------------------------------------------
.proc sample
        .a16
        .i16
retry:  lda a:FRAMES
        sta a:FSCR
        sep #$20
        .a8
        lda f:SLHV
        lda f:STAT78
        lda f:OPHCT
        sta a:HSCR
        lda f:OPHCT
        and #$01
        sta a:HSCR+1
        lda f:OPVCT
        sta a:VSCR
        lda f:OPVCT
        and #$01
        sta a:VSCR+1
        rep #$30
        .a16
        .i16
        lda a:FRAMES
        cmp FSCR
        bne retry
        ldx a:SMPX
        lda a:FSCR
        sta f:SRAM, x
        lda a:VSCR
        sta f:SRAM+2, x
        lda a:HSCR
        sta f:SRAM+4, x
        txa
        clc
        adc #6
        sta a:SMPX
        rts
.endproc

; a loop of known shape; the host fits wall = c + s*k through four sub-frame
; measurements and then solves the frame length from a multi-frame one
.proc calloop
        .a16
        .i16
        ldx TMPA
:       dex
        bne :-
        rts
.endproc

.proc syncframe
        .a16
        .i16
:       jsr readv
        cmp #250
        bcc :-
:       jsr readv
        cmp #10
        bcs :-
        rts
.endproc

.proc readv
        .a16
        .i16
        sep #$20
        .a8
        lda f:SLHV
        lda f:STAT78
        lda f:OPVCT
        sta a:VSCR
        lda f:OPVCT
        and #$01
        sta a:VSCR+1
        rep #$20
        .a16
        lda a:VSCR
        rts
.endproc

.proc profile_calibrate
        .a16
        .i16
        lda #$0100
        sta a:SMPX
        stz a:FRAMES
        lda #1000
cl:     sta TMPB
        jsr syncframe
        jsr sample
        lda TMPB
        sta TMPA
        jsr calloop
        jsr sample
        lda TMPB
        clc
        adc #1000
        cmp #5000
        bne cl
        ; one long run, spanning several frames, with the NMI counting
        stz a:FRAMES
        sep #$20
        .a8
        lda #$80
        sta f:NMITIMEN
        rep #$30
        .a16
        .i16
        cli
        jsr syncframe
        jsr sample
        lda #60000
        sta TMPA
        jsr calloop
        jsr sample
        sei
        sep #$20
        .a8
        lda #$00
        sta f:NMITIMEN
        rep #$30
        .a16
        .i16
        rts
.endproc
.endif

; ===========================================================================
        .segment "RODATA"
tbl_img:
        .incbin "out/model/tables.bin"
emb_img:
        .incbin "out/model/embed.bin"
pos_img:
        .incbin "out/model/pos.bin"

        .segment "WEIGHTS"
        .incbin "out/model/weights.bin"

        .segment "PTABSEG"
        .incbin "out/model/ptab.bin"

        SNES_HEADER "ELYA SNES TERNARY LM ", MAPMODE, $02, $08, $03
        SNES_VECTORS reset, nmi, irq
