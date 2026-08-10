; ---------------------------------------------------------------------------
; bench.s -- the cycle instrument, and the arithmetic primitives it measures.
;
; INSTRUMENT
;   ares has no scripting, so the counter is on the console.  Reading $2137
;   latches the PPU's H and V counters; $213C/$213D then read them back.  V
;   counts scanlines, H counts dots, and one dot is four master clocks.  So a
;   measurement is: sync to the top of a frame, latch, run the workload, latch
;   again, and hand the four raw numbers to the host through battery SRAM.  No
;   arithmetic is done on the console -- the host does it, so the model can be
;   changed without rebuilding a ROM.
;
;   Resolution is 4 master clocks, which is half of one SlowROM CPU cycle.
;   Each workload runs thousands of times, so the per-operation resolution is
;   far below a cycle.
;
; ACCOUNTING
;   Every primitive runs inside the same loop skeleton, and `empty` is that
;   skeleton with the bodies deleted.  Subtracting empty from a primitive
;   cancels the loop bookkeeping exactly, leaving the cost of the bodies alone.
;   Empty is measured at four different outer counts, which also proves the
;   instrument is linear.
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .p816
        .smart -

; ---- direct page ----------------------------------------------------------
ASAVE   = $00
OUTCNT  = $02
TMP     = $04
ACC     = $06
MCAND16 = $08
MULB16  = $0A
T0      = $0C
NOUT    = $0E
LATBUF  = $10           ; V1lo V1hi H1lo H1hi V2lo V2hi H2lo H2hi

NELEM   = 128           ; elements per inner pass
UNROLL  = 16

; ===========================================================================
; primitive bodies.  J is the unroll slot, 0..15.
; ===========================================================================

; --- calibration: things whose cycle counts come off the datasheet ---------
.macro B_NOP j
        nop
.endmacro

.macro B_LDA_ABSY j                     ; LDA abs,y, 16-bit A, no page cross
        lda W16Z + (j*2), y
.endmacro

.macro B_LDA_DP j                       ; LDA dp, 16-bit A, DP low byte = 0
        lda T0
.endmacro

.macro B_CLC_ADC_DP j
        clc
        adc T0
.endmacro

.macro B_LDA_PPU j                      ; read of a "fast" (6 master) register
        lda MPYL
.endmacro

; --- 1. software 8x8 shift-add, 65816 native ------------------------------
; multiplicand shifts left, multiplier shifts right, product accumulates in A.
.macro B_SOFTMUL j
        sep #$20
        .a8
        lda WB + j, y
        sta MCAND16
        stz MCAND16+1
        lda XB + j, y
        sta MULB16
        stz MULB16+1
        rep #$20
        .a16
        lda #0
        .repeat 8, K
        lsr MULB16
        bcc :+
        clc
        adc MCAND16
:
        .if K < 7
        asl MCAND16
        .endif
        .endrepeat
        clc
        adc ACC
        sta ACC
.endmacro

; --- 2. quarter-square table lookup ---------------------------------------
; a*b = QS1[a+b] - QS2[(a-b)+255].  Exact for 0 <= a,b <= 255.
.macro B_QSQUARE j
        lda W16Z + (j*2), y
        clc
        adc X16Z + (j*2), y
        asl a
        tax
        lda QS1, x
        sta T0
        lda W16Z + (j*2), y
        sec
        sbc X16Z + (j*2), y
        clc
        adc #255
        asl a
        tax
        lda T0
        sec
        sbc QS2, x
        clc
        adc ACC
        sta ACC
.endmacro

; --- 3. CPU hardware multiply $4202/$4203 ---------------------------------
; Safe form: separate 8-bit writes so $4203 is written exactly once, then
; enough work before reading $4216 to cover the 8-cycle latency.
.macro B_CPUHW j
        sep #$20
        .a8
        lda WB + j, y
        sta WRMPYA
        lda XB + j, y
        sta WRMPYB              ; multiply starts here
        rep #$20
        .a16
        nop
        clc
        lda RDMPYL              ; read lands >= 8 CPU cycles after the write
        adc ACC
        sta ACC
.endmacro

; --- 3b. CPU hardware multiply, operands pre-packed -----------------------
; One 16-bit store loads $4202 and $4203 together.  Lower bound only: packing
; two runtime arrays into one is not free in a real engine.
.macro B_CPUHW_PK j
        ldx PK + (j*2), y
        stx WRMPYA              ; $4202 = w, $4203 = x, multiply starts
        nop
        nop                     ; one NOP is NOT enough: reading $4216 at
        clc                     ; exactly 8 CPU cycles returned a wrong sum
        adc RDMPYL              ; (caught by the correctness pass), so 10.
.endmacro

; --- 4. PPU Mode 7 multiply $211B/$211C -----------------------------------
; M7A takes two writes to $211B (low byte then high byte); M7B takes one write
; to $211C.  A 16-bit store spans two registers, so two stores cover all three
; writes:  stx $211A -> ($211A junk, $211B = w)   [M7A low]
;          stx $211B -> ($211B = 0,   $211C = x)  [M7A high, M7B]
; Accumulator stays in A the whole time; X carries the operands.
.macro B_PPU j
        ldx WHI + (j*2), y
        stx M7SEL               ; $211A/$211B  -> M7A low byte = w
        ldx XHI + (j*2), y
        stx M7A                 ; $211B/$211C  -> M7A high = 0, M7B = x
        clc
        adc MPYL                ; 16 low bits of the 24-bit signed product
.endmacro

; --- 4b. PPU Mode 7 multiply, textbook form -------------------------------
; The obvious way to write it: 8-bit stores, two explicit writes to $211B,
; accumulator in memory.  Measured to price the two-write M7A cost honestly.
.macro B_PPU_NAIVE j
        sep #$20
        .a8
        lda WB + j, y
        sta M7A                 ; M7A low byte
        stz M7A                 ; M7A high byte
        lda XB + j, y
        sta M7B
        rep #$20
        .a16
        lda MPYL
        clc
        adc ACC
        sta ACC
.endmacro

; --- 5. ternary sign-separated gather -------------------------------------
; No multiply at all.  Weights are -1/0/+1, the zeros never appear, and the
; non-zeros are two index lists.  Slots 0-7 are the +1 list, 8-15 the -1 list.
.macro B_TERNARY j
        ldx IDX + (j*2), y
        .if j < 8
        clc
        adc XS16, x
        .else
        sec
        sbc XS16, x
        .endif
.endmacro


; --- 6. DSP-1 (NEC uPD77C25) via its documented Multiply command -----------
; DSP-1 op $00 is a 16x16 signed multiply returning (M*N)>>15, so int8 operands
; are usable pre-scaled: (w<<8) * (x<<7) >> 15 == w*x exactly.  The chip runs at
; ~8 MHz and in parallel with the CPU, but every operand and every result has to
; cross the cartridge bus, and in LoROM the DSP data register lives at
; $30:8000 -- a "slow" 8-master-clock address like any other cart address.
;
; The DSP-1 firmware is not available on this machine, so what is measured here
; is the CPU-side transfer sequence alone: no command byte (the DSP-1 repeats
; its last command when fed bare parameters), no RQM polling loop, and no DSP
; execution time.  That makes it a hard LOWER BOUND on the real cost, not the
; real cost.  Access timing on the 65816 is decided by the address, not by
; whether a chip answers, so the cycle counts are exact even with no DSP fitted.
.macro B_DSP1_FLOOR j
        lda WHI + (j*2), y      ; multiplicand
        sta f:$308000           ; -> DR
        lda XHI + (j*2), y      ; multiplier
        sta f:$308000           ; -> DR
        lda f:$308000           ; <- product
        clc
        adc ACC
        sta ACC
.endmacro

; the same, plus the single status-register read that the cheapest possible
; correct driver still has to do before trusting the result
.macro B_DSP1_SR j
        lda WHI + (j*2), y
        sta f:$308000
        lda XHI + (j*2), y
        sta f:$308000
        lda f:$30C000           ; SR -- one read, not a poll loop
        lda f:$308000
        clc
        adc ACC
        sta ACC
.endmacro

; ===========================================================================
; ===========================================================================
; loop skeleton
; ===========================================================================
.macro LOOPTAIL stride, lbl
        sta ASAVE
        tya
        clc
        adc #(stride*::UNROLL)
        tay
        lda ASAVE
        cpy #(stride*::NELEM)
        beq :+
        brl lbl                 ; unrolled bodies overflow an 8-bit branch;
:                               ; every test uses the same long form so the
.endmacro                       ; skeleton still cancels against `empty`.

; ca65 will not accept a macro name as a macro argument, so the per-test
; wrapper is a head/tail pair and each test spells out its own .repeat.
.macro THEAD
        lda NOUT
        sta OUTCNT
        stz ACC
        lda #0
.endmacro

.macro TTAIL stride
        LOOPTAIL stride, inner
        dec OUTCNT
        beq :+
        brl outer
:
        sta ACC                 ; A-resident accumulators land here too
        rep #$30
        .a16
        .i16
        rts
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
        ldx #$1FFF
        txs
        lda #$0000
        tcd
        sep #$20
        .a8
        phk
        plb

        lda #$8F
        sta INIDISP             ; forced blank
        stz NMITIMEN
        stz $420B
        stz $420C
        stz MEMSEL              ; SlowROM: every ROM access is 8 master clocks
        lda #$01
        sta BGMODE              ; mode 1 -- NOT mode 7, so $211B/$211C are ours
        stz $212C
        stz $212D

        rep #$30
        .a16
        .i16

        ; magic
        sep #$20
        .a8
        lda #$42                ; 'B'
        sta f:SRAM+0
        lda #$4E                ; 'N'
        sta f:SRAM+1
        lda #$43                ; 'C'
        sta f:SRAM+2
        lda #$48                ; 'H'
        sta f:SRAM+3
        lda #$00                ; DONE marker cleared (STZ has no long mode)
        sta f:SRAM+8
        sta f:SRAM+9
        sta f:SRAM+10
        sta f:SRAM+11
        rep #$30
        .a16
        .i16

        jsr run_pass            ; pass 1  -> SRAM+$0010
        jsr run_pass            ; pass 2  -> same slots, overwritten
        jsr run_verify          ; correctness of every primitive

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

; ===========================================================================
; run every timing slot
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

.proc run_pass
        ; --- linearity of the instrument: the same empty skeleton, 4 lengths
        SLOT t_empty,   2,  0
        SLOT t_empty,   4,  1
        SLOT t_empty,   8,  2
        SLOT t_empty,  16,  3
        ; --- calibration bodies, all hand-derivable
        SLOT t_nop,    16,  4
        SLOT t_ldaabsy,16,  5
        SLOT t_ldadp,  16,  6
        SLOT t_clcadc, 16,  7
        SLOT t_ldappu, 16,  8
        ; --- the primitives.  Outer counts are sized so the longest window is
        ;     ~140 scanlines, well short of the 262-line frame; each primitive
        ;     is also measured at half length so the two must agree.
        SLOT t_softmul, 1,  9
        SLOT t_qsquare, 2, 10
        SLOT t_qsquare, 1, 11
        SLOT t_cpuhw,   4, 12
        SLOT t_cpuhw,   2, 13
        SLOT t_cpuhwpk, 8, 14
        SLOT t_cpuhwpk, 4, 15
        SLOT t_ppu,     8, 16
        SLOT t_ppu,     4, 17
        SLOT t_ppunaive,4, 18
        SLOT t_ppunaive,2, 19
        SLOT t_ternary,16, 20
        SLOT t_ternary, 8, 21
        ; --- the PPU multiply with the screen actually on
        jsr screen_on
        SLOT t_ppu,     8, 22
        jsr screen_off
        ; --- DSP-1 bus-transfer floor (no DSP fitted; see B_DSP1_FLOOR)
        SLOT t_dsp1floor, 4, 23
        SLOT t_dsp1floor, 2, 24
        SLOT t_dsp1sr,    4, 25
        SLOT t_dsp1sr,    2, 26
        rts
.endproc

.proc screen_on
        sep #$20
        .a8
        lda #$01
        sta $212C               ; BG1 on the main screen
        lda #$0F
        sta INIDISP             ; screen on, full brightness
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc screen_off
        sep #$20
        .a8
        stz $212C
        lda #$8F
        sta INIDISP
        rep #$30
        .a16
        .i16
        rts
.endproc

; ===========================================================================
; correctness: one pass of each primitive, accumulator written to SRAM
; ===========================================================================
.macro VSLOT sub, idx
        lda #1
        sta NOUT
        jsr sub
        lda ACC
        ldx #(idx*2)
        jsr store_acc
.endmacro

.proc run_verify
        VSLOT t_softmul,  0
        VSLOT t_qsquare,  1
        VSLOT t_cpuhw,    2
        VSLOT t_cpuhwpk,  3
        VSLOT t_ppu,      4
        VSLOT t_ppunaive, 5
        VSLOT t_ternary,  6
        rts
.endproc

; store 16-bit A at SRAM+$0200+X
.proc store_acc
        sta f:SRAM+$0200, x
        rts
.endproc

; ===========================================================================
; instrument
; ===========================================================================

; read the V counter into A (16-bit)
.proc read_v16
        sep #$20
        .a8
        lda SLHV                ; latch H and V
        lda STAT78              ; reset the high/low read toggle
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

; park at the very top of a frame so a long measurement cannot wrap past V=261
.proc sync_frame
:       jsr read_v16
        cmp #250
        bcc :-
:       jsr read_v16
        cmp #10
        bcs :-
        rts
.endproc

.proc latch_start
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
        lda $4210               ; read RDNMI to clear its vblank flag; if the
        rep #$30                ; flag is set again by latch_end, the window
                                ; ran past scanline 225 and the V counter may
                                ; have wrapped -- the measurement is void.
        .a16
        .i16
        rts
.endproc

.proc latch_end
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
        and #$80                ; vblank seen inside the window?
        ora LATBUF+5
        sta LATBUF+5            ; -> bit 7 of the V2 high byte
        rep #$30
        .a16
        .i16
        rts
.endproc

; copy the 8 latch bytes to SRAM+$0010+X
.proc store_result
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
.macro DEFTEST_BEGIN
        THEAD
.endmacro

.proc t_empty
        THEAD
outer:  ldy #$0000
inner:  TTAIL 2
.endproc

.proc t_nop
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_NOP J
        .endrepeat
        TTAIL 2
.endproc

.proc t_ldaabsy
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_LDA_ABSY J
        .endrepeat
        TTAIL 2
.endproc

.proc t_ldadp
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_LDA_DP J
        .endrepeat
        TTAIL 2
.endproc

.proc t_clcadc
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_CLC_ADC_DP J
        .endrepeat
        TTAIL 2
.endproc

.proc t_ldappu
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_LDA_PPU J
        .endrepeat
        TTAIL 2
.endproc

.proc t_softmul
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_SOFTMUL J
        .endrepeat
        TTAIL 1
.endproc

.proc t_qsquare
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_QSQUARE J
        .endrepeat
        TTAIL 2
.endproc

.proc t_cpuhw
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_CPUHW J
        .endrepeat
        TTAIL 1
.endproc

.proc t_cpuhwpk
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_CPUHW_PK J
        .endrepeat
        TTAIL 2
.endproc

.proc t_ppu
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_PPU J
        .endrepeat
        TTAIL 2
.endproc

.proc t_ppunaive
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_PPU_NAIVE J
        .endrepeat
        TTAIL 1
.endproc

.proc t_dsp1floor
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_DSP1_FLOOR J
        .endrepeat
        TTAIL 2
.endproc

.proc t_dsp1sr
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_DSP1_SR J
        .endrepeat
        TTAIL 2
.endproc

.proc t_ternary
        THEAD
outer:  ldy #$0000
inner:
        .repeat ::UNROLL, J
        B_TERNARY J
        .endrepeat
        TTAIL 2
.endproc

nmi:    rti
irq:    rti

        .include "data.inc"

        SNES_HEADER "ELYA SNES BENCH      ", $20, $02, $05, $03
        SNES_VECTORS reset, nmi, irq
