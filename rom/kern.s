; ---------------------------------------------------------------------------
; kern.s -- the ternary GATHER KERNEL A/B.
;
; Entry 3 of FINDINGS settled which arithmetic wins (ternary, 2.02x).  This ROM
; settles a different question: what is the cheapest way to WRITE a ternary
; gather on a 65816, and does the NES port's finding -- that 8-bit register
; residency was the whole driver of its inner-loop cost -- survive a machine
; with a 16-bit accumulator?
;
; Same instrument as rom/bench.s: PPU H/V counters latched around a window, raw
; counts out through battery SRAM, host does the arithmetic.  Every kernel also
; sums the same 128 biased activations through the same permutation and writes
; its accumulator out, so a kernel that is fast because it is broken is caught
; by its sum and not by its cycles.
;
; Activations are stored BIASED (value + 7, i.e. 0..14), which is what makes a
; run of `adc` need no `clc` between elements -- on the NES because 16*14 = 224
; fits a byte, here because 128*14 = 1792 fits a word with room to spare.
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .p816
        .smart -

; ---- absolute scratch (NOT direct page: the direct page holds activations) --
ASAVE   = $0300
OUTCNT  = $0302
ACC     = $0304
TMP     = $0306
LATBUF  = $0310         ; V1lo V1hi H1lo H1hi V2lo V2hi H2lo H2hi
NOUT    = $0320

ACT16   = $1000         ; 128 biased activations, 16-bit -- direct page A
ACT8    = $1100         ; the same values as bytes    -- direct page B
TOTDP   = $F0           ; 16-bit fold total, direct page B ($11F0)

NELEM   = 128
UNROLL  = 16
START   = 4096          ; keeps a sign-separated row's accumulator >= 0

.ifdef FASTROM
MAPMODE = $30
.else
MAPMODE = $20
.endif

; ===========================================================================
; kernel bodies.  J is the unroll slot, 0..15.
; ===========================================================================

; --- 16-bit index registers, activations reached absolutely ---------------
.macro B_I16ABS j
        ldx IDXW + (j*2), y
        adc a:ACT16, x
.endmacro

; --- 16-bit index registers, activations in the direct page ---------------
.macro B_I16DP j
        ldx IDXW + (j*2), y
        adc z:$00, x
.endmacro

; --- 8-bit index registers, 16-bit accumulator ----------------------------
; The stream byte is the PRE-DOUBLED activation offset, so an 8-bit X still
; reaches all 128 16-bit activations (2*127 = 254).
.macro B_I8DP16 j
        ldx IDXB2 + j, y
        adc z:$00, x
.endmacro

; --- 8-bit index registers, 8-bit accumulator: the NES shape --------------
.macro B_I8ACC j
        ldx IDXB1 + j, y
        adc z:$00, x
.endmacro

; Fold an 8-bit block sum into the 16-bit total.  Sixteen biased values cannot
; exceed 224, so no carry sets inside a block; this is the price of that.
.macro FOLD8
        rep #$20
        .a16
        and #$00FF
        clc
        adc z:TOTDP
        sta z:TOTDP
        sep #$20
        .a8
        lda #0
.endmacro

; ===========================================================================
; loop skeletons
; ===========================================================================
.macro THEAD
        lda NOUT
        sta OUTCNT
.endmacro

; -- skeleton I: an inner loop of UNROLL bodies, y walking the index stream --
.macro ITAIL stride
        sta ASAVE
        tya
        clc
        adc #(stride*::UNROLL)
        tay
        lda ASAVE
        cpy #(stride*::NELEM)
        beq :+
        brl inner
:       dec OUTCNT
        beq :+
        brl outer
:       sta ACC
        rts
.endmacro

; -- skeleton C: no stream and no inner loop; the bodies are 128 long -------
.macro CTAIL
        dec OUTCNT
        beq :+
        brl outer
:       sta ACC
        rts
.endmacro

        .include "kern.inc"

        .segment "CODE"

; ===========================================================================
reset:
        sei
        clc
        xce
        .a16
        .i16
        rep #$38
        ldx #$1FFF
        txs
        lda #$0000
        tcd
        sep #$20
        .a8
        phk
        plb
        lda #$8F
        sta INIDISP
        stz NMITIMEN
        stz $420B
        stz $420C
.ifdef FASTROM
        lda #$01
        sta MEMSEL
        jml $800000 + fast_entry
fast_entry:
        phk
        plb
.else
        stz MEMSEL
.endif
        lda #$01
        sta BGMODE
        stz $212C
        stz $212D
        rep #$30
        .a16
        .i16

        jsr init_act

        sep #$20
        .a8
        lda #$4B                ; 'K'
        sta f:SRAM+0
        lda #$45                ; 'E'
        sta f:SRAM+1
        lda #$52                ; 'R'
        sta f:SRAM+2
        lda #$4E                ; 'N'
        sta f:SRAM+3
        lda #$00
        sta f:SRAM+8
        sta f:SRAM+9
        sta f:SRAM+10
        sta f:SRAM+11
        rep #$30
        .a16
        .i16

        jsr run_pass
        jsr run_pass
        jsr run_verify

        sep #$20
        .a8
        lda #$44                ; 'D'
        sta f:SRAM+8
        lda #$4F                ; 'O'
        sta f:SRAM+9
        lda #$4E                ; 'N'
        sta f:SRAM+10
        lda #$45                ; 'E'
        sta f:SRAM+11
        rep #$30
        .a16
        .i16
halt:   bra halt

; Copy the activation arrays into WRAM.  ACT16 is 128 words at $1000 and ACT8
; is the same 128 values as bytes at $1100, so the direct page can be pointed
; at either one and the two accumulator widths see identical data.
.proc init_act
        .a16
        .i16
        ldx #$0000
:       lda ACTSRC16, x
        sta a:ACT16, x
        inx
        inx
        cpx #$0100
        bne :-
        sep #$20
        .a8
        ldx #$0000
:       lda ACTSRC8, x
        sta a:ACT8, x
        inx
        cpx #$0080
        bne :-
        rep #$30
        .a16
        .i16
        rts
.endproc

; ===========================================================================
.macro SLOT sub, outercnt, idx
        lda #outercnt
        sta NOUT
        jsr sync_frame
        jsr latch_start
        jsr sub
        jsr latch_end
        ldx #(idx*8)
        jsr store_result
.endmacro

.macro SETDP page
        lda #page
        tcd
.endmacro

.proc run_pass
        .a16
        .i16
        ; ---- skeleton I, 16-bit index registers -------------------------
        SETDP $1000
        SLOT t_empty16,  2,  0
        SLOT t_empty16,  4,  1
        SLOT t_empty16,  8,  2
        SLOT t_empty16, 16,  3
        SLOT t_i16abs,   8,  4
        SLOT t_i16abs,   4,  5
        SLOT t_i16dp,    8,  6
        SLOT t_i16dp,    4,  7
        ; ---- skeleton I, 8-bit index registers, 16-bit accumulator ------
        SLOT t_empty8,   8,  8
        SLOT t_empty8,   4,  9
        SLOT t_i8dp16,   8, 10
        SLOT t_i8dp16,   4, 11
        ; ---- skeleton I, 8-bit index registers, 8-bit accumulator -------
        SETDP $1100
        SLOT t_empty8a,  8, 12
        SLOT t_empty8a,  4, 13
        SLOT t_i8acc,    8, 14
        SLOT t_i8acc,    4, 15
        ; ---- skeleton C, the index baked into the operand byte ----------
        SETDP $1000
        SLOT t_emptyC,  16, 16
        SLOT t_emptyC,   8, 17
        SLOT t_code,    16, 18
        SLOT t_code,     8, 19
        SLOT t_codesgn, 16, 20
        SLOT t_codesgn,  8, 21
        SETDP $1100
        SLOT t_code8,   16, 22
        SLOT t_code8,    8, 23
        SETDP $1000
        rts
.endproc

.macro VSLOT sub, idx
        lda #1
        sta NOUT
        jsr sub
        lda ACC
        ldx #(idx*2)
        jsr store_acc
.endmacro

.proc run_verify
        .a16
        .i16
        SETDP $1000
        VSLOT t_i16abs,  0
        VSLOT t_i16dp,   1
        VSLOT t_i8dp16,  2
        SETDP $1100
        VSLOT t_i8acc,   3
        SETDP $1000
        VSLOT t_code,    4
        VSLOT t_codesgn, 5
        SETDP $1100
        VSLOT t_code8,   6
        SETDP $1000
        rts
.endproc

.proc store_acc
        .a16
        .i16
        sta f:SRAM+$0200, x
        rts
.endproc

; ===========================================================================
; instrument (identical to rom/bench.s)
; ===========================================================================
.proc read_v16
        .a16
        .i16
        sep #$20
        .a8
        lda SLHV
        lda STAT78
        lda OPVCT
        sta TMP
        lda OPVCT
        and #$01
        sta TMP+1
        rep #$20
        .a16
        lda TMP
        rts
.endproc

.proc sync_frame
        .a16
        .i16
:       jsr read_v16
        cmp #250
        bcc :-
:       jsr read_v16
        cmp #10
        bcs :-
        rts
.endproc

.proc latch_start
        .a16
        .i16
        sep #$20
        .a8
        lda SLHV
        lda STAT78
        lda OPHCT
        sta LATBUF+2
        lda OPHCT
        and #$01
        sta LATBUF+3
        lda OPVCT
        sta LATBUF+0
        lda OPVCT
        and #$01
        sta LATBUF+1
        lda $4210
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc latch_end
        .a16
        .i16
        sep #$20
        .a8
        lda SLHV
        lda STAT78
        lda OPHCT
        sta LATBUF+6
        lda OPHCT
        and #$01
        sta LATBUF+7
        lda OPVCT
        sta LATBUF+4
        lda OPVCT
        and #$01
        sta LATBUF+5
        lda $4210
        and #$80
        ora LATBUF+5
        sta LATBUF+5
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc store_result
        .a16
        .i16
        sep #$20
        .a8
        ldy #$0000
:       lda LATBUF, y
        sta f:SRAM+$0010, x
        inx
        iny
        cpy #8
        bne :-
        rep #$30
        .a16
        .i16
        rts
.endproc

; ===========================================================================
; the tests
; ===========================================================================
.proc t_empty16
        .a16
        .i16
        THEAD
outer:  ldy #$0000
        lda #0
inner:  ITAIL 2
.endproc

.proc t_i16abs
        .a16
        .i16
        THEAD
outer:  ldy #$0000
        lda #0
inner:
        .repeat ::UNROLL, J
        B_I16ABS J
        .endrepeat
        ITAIL 2
.endproc

.proc t_i16dp
        .a16
        .i16
        THEAD
outer:  ldy #$0000
        lda #0
inner:
        .repeat ::UNROLL, J
        B_I16DP J
        .endrepeat
        ITAIL 2
.endproc

; -- 8-bit index registers, accumulator still 16 bits ----------------------
.proc t_empty8
        .a16
        .i16
        THEAD
        sep #$10
        .i8
outer:  ldy #$00
        lda #0
inner:  ITAIL 1
.endproc

.proc t_i8dp16
        .a16
        .i16
        THEAD
        sep #$10
        .i8
outer:  ldy #$00
        lda #0
inner:
        .repeat ::UNROLL, J
        B_I8DP16 J
        .endrepeat
        ITAIL 1
.endproc

; -- 8-bit index registers AND an 8-bit accumulator: the NES shape ----------
; The FOLD8 is charged to the kernel, not to the skeleton, because paying it
; is exactly what a byte-wide accumulator costs.
.proc t_empty8a
        .a16
        .i16
        THEAD
        sep #$30
        .a8
        .i8
        stz z:TOTDP
        stz z:TOTDP+1
outer:  ldy #$00
        lda #0
inner:  sta ASAVE
        tya
        clc
        adc #(1*::UNROLL)
        tay
        lda ASAVE
        cpy #(1*::NELEM)
        beq :+
        brl inner
:       dec OUTCNT
        beq :+
        brl outer
:       rep #$30
        .a16
        .i16
        lda z:TOTDP
        sta ACC
        rts
.endproc

.proc t_i8acc
        .a16
        .i16
        THEAD
        sep #$30
        .a8
        .i8
        stz z:TOTDP
        stz z:TOTDP+1
outer:  ldy #$00
        lda #0
inner:
        .repeat ::UNROLL, J
        B_I8ACC J
        .endrepeat
        FOLD8
        .a8
        .i8
        sta ASAVE
        tya
        clc
        adc #(1*::UNROLL)
        tay
        lda ASAVE
        cpy #(1*::NELEM)
        beq :+
        brl inner
:       dec OUTCNT
        beq :+
        brl outer
:       rep #$30
        .a16
        .i16
        lda z:TOTDP
        sta ACC
        rts
.endproc

; ===========================================================================
; skeleton C: no stream and no inner loop.  The gather index lives in the
; instruction's own operand byte, so the load that fetched it is gone.
; ===========================================================================
.proc t_emptyC
        .a16
        .i16
        THEAD
outer:  lda #START
        CTAIL
.endproc

.proc t_code
        .a16
        .i16
        THEAD
outer:  lda #START
        KCODE
        CTAIL
.endproc

.proc t_codesgn
        .a16
        .i16
        THEAD
outer:  lda #START
        KCODESGN
        CTAIL
.endproc

.proc t_code8
        .a16
        .i16
        THEAD
outer:  lda #START
        stz z:TOTDP
        sep #$20
        .a8
        lda #0
        KCODE8
        rep #$20
        .a16
        lda z:TOTDP
        CTAIL
.endproc

nmi:    rti
irq:    rti

        SNES_HEADER "ELYA SNES KERNAB     ", MAPMODE, $02, $05, $03
        SNES_VECTORS reset, nmi, irq
