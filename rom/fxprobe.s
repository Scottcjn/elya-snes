; ---------------------------------------------------------------------------
; fxprobe.s -- is there a GSU in this cartridge at all?
; Writes a pattern into GSU register R0 at $00:3000 and reads it back.  On a
; plain cart those addresses are open bus and the readback is garbage; if a
; SuperFX is instantiated they are real registers and the pattern survives.
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .p816
        .smart -
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
        rep #$30
        .a16
        .i16

        lda #$1234
        sta $3000               ; GSU R0
        lda #$ABCD
        sta $3002               ; GSU R1
        lda $3000
        sta $10
        lda $3002
        sta $12
        lda $3030               ; SFR
        sta $14
        lda $303A               ; SCMR
        sta $16

        sep #$20
        .a8
        lda #$46                ; 'F'
        sta f:SRAM+0
        lda #$58                ; 'X'
        sta f:SRAM+1
        lda $10
        sta f:SRAM+4
        lda $11
        sta f:SRAM+5
        lda $12
        sta f:SRAM+6
        lda $13
        sta f:SRAM+7
        lda $14
        sta f:SRAM+8
        lda $15
        sta f:SRAM+9
        lda $16
        sta f:SRAM+10
        lda $17
        sta f:SRAM+11
        lda #$44                ; 'D'
        sta f:SRAM+2
        lda #$4E                ; 'N'
        sta f:SRAM+3
halt:   bra halt
nmi:    rti
irq:    rti
        SNES_HEADER "ELYA SNES FXPROBE    ", $20, $15, $05, $05
        SNES_VECTORS reset, nmi, irq
