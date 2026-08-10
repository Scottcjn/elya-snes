; ---------------------------------------------------------------------------
; fx2.s -- SuperFX arm of the cycles-per-MAC measurement.
;
; EMULATOR ONLY.  The target Kaico Super DSP V3.1 cannot run SuperFX, so
; nothing measured by this ROM has a path to real silicon.  ares's SNES core is
; bsnes-derived, which is the accurate lineage, but it is not a GSU.
;
; Same instrument as the 5A22 arm: latch the PPU H/V counters, run the work,
; latch again.  The work here is N invocations of a GSU kernel that does 128
; MACs, and `k_empty` is the same invocation with no MAC body, so subtracting
; it cancels the GSU start/stop handshake and the CPU-side driver exactly.
;
; While SCMR grants the GSU ROM (RON) the 65816 cannot fetch from the
; cartridge, so the whole measurement routine runs from WRAM.
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .p816
        .smart -
        .import __FXCODE_LOAD__, __FXCODE_RUN__, __FXCODE_SIZE__

SCMR    = $303A
SFR     = $3030
PBR     = $3034
CFGR    = $3037
R15     = $301E

GENTRY  = $10
OUTER   = $18
LATBUF  = $20


.macro MEASURE entry, outer, slot
        lda #entry
        sta GENTRY
        lda #outer
        sta OUTER
        jsr fx_measure
        ldx #(slot*8)
        jsr store_result
.endmacro

.macro VERIFY entry, slot
        lda #entry
        sta GENTRY
        lda #1
        sta OUTER
        jsr fx_measure
        sep #$20
        .a8
        lda f:$700500
        sta f:$7000F0 + (slot*2)
        lda f:$700501
        sta f:$7000F1 + (slot*2)
        rep #$30
        .a16
        .i16
.endmacro

        .segment "CODE"
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
        stz CFGR                ; do not mask the GSU's STOP interrupt
        stz SCMR                ; CPU owns ROM and Game Pak RAM for setup
        rep #$30
        .a16
        .i16

        ; WRAM-resident driver
        ldx #$0000
:       lda f:__FXCODE_LOAD__, x
        sta f:__FXCODE_RUN__, x
        inx
        inx
        cpx #__FXCODE_SIZE__
        bcc :-

        ; operands into Game Pak RAM where the GSU can reach them
        COPY128 = 128
        ldx #$0000
:       sep #$20
        .a8
        lda f:WB, x
        sta f:$700100, x
        lda f:XB, x
        sta f:$700200, x
        rep #$20
        .a16
        inx
        cpx #128
        bcc :-
        ldx #$0000
:       sep #$20
        .a8
        lda f:IDX, x
        sta f:$700300, x
        lda f:XS16, x
        sta f:$700400, x
        rep #$20
        .a16
        inx
        cpx #256
        bcc :-

        sep #$20
        .a8
        lda #$46                ; 'F'
        sta f:$700000
        lda #$58                ; 'X'
        sta f:$700001
        lda #$32                ; '2'
        sta f:$700002
        lda #$21                ; '!'
        sta f:$700003
        rep #$30
        .a16
        .i16

        MEASURE gsu_empty, 16, 0
        MEASURE gsu_empty,  8, 1
        MEASURE gsu_int8, 16, 2
        MEASURE gsu_int8,  8, 3
        MEASURE gsu_tern, 16, 4
        MEASURE gsu_tern,  8, 5
        MEASURE gsu_nomul,16, 6
        MEASURE gsu_nomul, 8, 7

        ; one pass of each kernel, accumulator kept for the host to check
        VERIFY gsu_int8, 0
        VERIFY gsu_tern, 1

        sep #$20
        .a8
        lda #$44                ; 'D'
        sta f:$700008
        lda #$4F
        sta f:$700009
        lda #$4E
        sta f:$70000A
        lda #$45
        sta f:$70000B
        rep #$30
        .a16
        .i16
halt:   bra halt
nmi:    rti
irq:    rti

        .segment "GSUEMPTY"
gsu_empty:
        .incbin "gsu/k_empty.bin"
        .segment "GSUINT8"
gsu_int8:
        .incbin "gsu/k_int8.bin"
        .segment "GSUTERN"
gsu_tern:
        .incbin "gsu/k_tern.bin"
        .segment "GSUNOMUL"
gsu_nomul:
        .incbin "gsu/k_nomul.bin"
        .include "data.inc"


; ---------------------------------------------------------------------------
        .segment "FXCODE"
; Everything below runs from WRAM, because the GSU holds the cartridge.

; read the V counter into A (16-bit)
.proc read_v16
        sep #$20
        .a8
        lda SLHV
        lda STAT78
        lda OPVCT
        sta $12
        lda OPVCT
        and #$01
        sta $13
        rep #$20
        .a16
        lda $12
        rts
.endproc

.proc sync_frame
:       jsr read_v16
        cmp #250
        bcc :-
:       jsr read_v16
        cmp #10
        bcs :-
        rts
.endproc

.macro LATCH off
        sep #$20
        .a8
        lda SLHV
        lda STAT78
        lda OPHCT
        sta LATBUF+off+2
        lda OPHCT
        and #$01
        sta LATBUF+off+3
        lda OPVCT
        sta LATBUF+off+0
        lda OPVCT
        and #$01
        sta LATBUF+off+1
        lda $4210
        .if off <> 0
        and #$80
        ora LATBUF+off+1
        sta LATBUF+off+1
        .endif
        rep #$30
        .a16
        .i16
.endmacro

; run the kernel at GENTRY, OUTER times, timed
.proc fx_measure
        jsr sync_frame
        sep #$20
        .a8
        lda #$18                ; SCMR: RAN (bit3) + RON (bit4) -> GSU owns both
        sta SCMR
        stz PBR
        rep #$30
        .a16
        .i16
        LATCH 0
        ldx OUTER
loop:
        sep #$20
        .a8
        lda SFR+1               ; clear a latched IRQ before starting
        rep #$20
        .a16
        lda GENTRY
        sta R15                 ; writing the high byte sets GO
        sep #$20
        .a8
:       lda SFR+1               ; bit 7 = IRQ, set by the kernel's STOP
        bpl :-
        rep #$30
        .a16
        .i16
        dex
        bne loop
        LATCH 4
        sep #$20
        .a8
        stz SCMR                ; CPU takes the cartridge back
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc store_result
        sep #$20
        .a8
        ldy #$0000
:       lda LATBUF, y
        sta f:$700010, x
        inx
        iny
        cpy #8
        bne :-
        rep #$30
        .a16
        .i16
        rts
.endproc

        SNES_HEADER "ELYA SNES FX BENCH   ", $20, $15, $05, $05
        SNES_VECTORS reset, nmi, irq
