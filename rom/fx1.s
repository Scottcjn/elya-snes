; ---------------------------------------------------------------------------
; fx1.s -- SuperFX bring-up.  EMULATOR ONLY: the target Kaico Super DSP cart
; cannot run SuperFX, so nothing in this file has a path to real silicon.
;
; While the GSU owns ROM (SCMR RON=1) the 65816 cannot fetch instructions from
; the cartridge, so the routine that starts the GSU and waits for it has to run
; from WRAM.  The FXCODE segment is linked to run at $0300 and copied there.
; ---------------------------------------------------------------------------
        .include "snes.inc"
        .p816
        .smart -

        .import __FXCODE_LOAD__, __FXCODE_RUN__, __FXCODE_SIZE__

SCMR    = $303A
SFR     = $3030
PBR     = $3034
R15     = $301E

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

        ; copy the WRAM-resident GSU driver into place
        ldx #$0000
:       lda f:__FXCODE_LOAD__, x
        sta f:__FXCODE_RUN__, x
        inx
        inx
        cpx #__FXCODE_SIZE__
        bcc :-

        ; clear the two result words so a stale value cannot be mistaken for
        ; a successful GSU write
        lda #$0000
        sta f:$700100
        sta f:$700102

        lda #(gsu_hello - $8000) + $8000
        sta $10                 ; GSU entry address for the driver
        jsr fx_run

        sep #$20
        .a8
        ldx #$0000
:       lda $12, x
        sta f:SRAM+$0010, x
        inx
        cpx #$000C
        bcc :-
        lda #$46                ; 'F'
        sta f:SRAM+0
        lda #$58                ; 'X'
        sta f:SRAM+1
        lda #$44                ; 'D'
        sta f:SRAM+2
        lda #$4E                ; 'N'
        sta f:SRAM+3
        rep #$30
        .a16
        .i16
halt:   bra halt

nmi:    rti
irq:    rti

gsu_hello:
        .incbin "gsu/hello.bin"

; ---------------------------------------------------------------------------
        .segment "FXCODE"
; Runs from WRAM.  $10 holds the GSU entry address.
.proc fx_run
        ; $10 = GSU entry address.  Returns:
        ;   $12 = poll iterations, $14 = SFR, $16 = R15, $18 = R0, $1A = R1,
        ;   $1C = timeout marker ($0000 ok, $DEAD gave up)
        sep #$20
        .a8
        stz $3037               ; CFGR: do not mask the STOP interrupt
        stz PBR                 ; GSU program bank = $00
        lda #$18                ; SCMR: bit3 RAN=1, bit4 RON=1 -> GSU owns both
        sta SCMR
        lda SFR+1               ; reading the SFR high byte clears a latched
                                ; IRQ; without this the first poll can see a
                                ; stale flag and tear SCMR away mid-run
        rep #$20
        .a16
        lda #$0000
        sta $1C
        lda $10
        sta R15                 ; writing the R15 high byte sets GO

        ldx #$0000
:       sep #$20
        .a8
        lda SFR+1               ; bit 7 = IRQ, latched by the GSU's STOP
        rep #$20
        .a16
        bmi fin
        inx
        bne :-
        lda #$DEAD
        sta $1C
fin:
        stx $12
        lda SFR
        sta $14
        lda R15
        sta $16
        lda $3000
        sta $18
        lda $3002
        sta $1A
        sep #$20
        .a8
        stz SCMR                ; hand ROM and RAM back to the CPU
        rep #$30
        .a16
        .i16
        rts
.endproc

        SNES_HEADER "ELYA SNES FX BRINGUP ", $20, $15, $05, $05
        SNES_VECTORS reset, nmi, irq
