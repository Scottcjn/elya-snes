; ---------------------------------------------------------------------------
; boot.s -- step 1: does a ROM boot under ares, and can we read a value back?
;
; Two independent readouts, on purpose, so that if one channel is broken the
; other still tells us whether the CPU ran:
;   1. visual  -- backdrop goes green, then the brightness pulses once a frame
;   2. data    -- a magic pattern + a live frame counter in battery SRAM,
;                 which ares flushes to a .srm file on exit
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .p816
        .smart -

        .segment "CODE"

reset:
        sei
        clc
        xce                     ; leave 6502 emulation mode -> 65816 native
        .a16
        .i16
        rep #$38                ; A/X/Y 16-bit, decimal off
        ldx #$1FFF
        txs                     ; stack at $1FFF
        lda #$0000
        tcd                     ; direct page = $0000
        sep #$20
        .a8
        phk
        plb                     ; data bank = program bank = $00

        lda #$8F
        sta INIDISP             ; forced blank while we set up
        stz NMITIMEN            ; no NMI, no auto-joypad
        stz $420B               ; no DMA
        stz $420C               ; no HDMA

        ; backdrop colour 0 = green ($03E0 in BGR555)
        stz $2121
        lda #$E0
        sta $2122
        lda #$03
        sta $2122

        lda #$01
        sta BGMODE              ; mode 1, nothing enabled -> pure backdrop
        stz $212C               ; main screen: nothing
        stz $212D

        ; ---- magic pattern into battery SRAM -------------------------------
        lda #$53                ; 'S'
        sta f:SRAM+0
        lda #$4E                ; 'N'
        sta f:SRAM+1
        lda #$45                ; 'E'
        sta f:SRAM+2
        lda #$53                ; 'S'
        sta f:SRAM+3
        lda #$5A                ; marker
        sta f:SRAM+4
        lda #$A5
        sta f:SRAM+5
        lda #$00
        sta f:SRAM+6            ; frame counter low
        sta f:SRAM+7            ; frame counter high

        lda #$0F
        sta INIDISP             ; screen on, full brightness

        ; ---- alive loop: count frames into SRAM ----------------------------
        ; $4212 (HVBJOY) bit7 is a level, not a latch, so polling it is safe.
        rep #$20
        .a16
        lda #$0000
        sta $00                 ; 16-bit frame counter in direct page
        sep #$20
        .a8

alive:
:       lda $4212
        bmi :-                  ; wait for vblank to end
:       lda $4212
        bpl :-                  ; wait for vblank to begin

        inc $00
        bne :+
        inc $01
:       lda $00
        sta f:SRAM+6
        lda $01
        sta f:SRAM+7
        bra alive

nmi:
        rti
irq:
        rti

        SNES_HEADER "ELYA SNES BOOT       ", $20, $02, $05, $03
        SNES_VECTORS reset, nmi, irq
