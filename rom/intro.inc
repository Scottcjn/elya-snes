; ===========================================================================
; intro.inc -- the ESV1 stream player.  10 seconds of AI-generated video,
; decoded by a 65816 out of cartridge banks $21-$37.
;
; Format (tools/mkintro.py, all little-endian):
;   header 16 B   'ESV1' u16 tw, th, n_frames, fps, n_pals, peak_tiles
;   palettes      n_pals x 16 x u16 BGR555, CGRAM order
;   per frame     u16 n_tiles, n_tiles x { u16 slot, 32 B 4bpp tile }
;                 u16 n_map,   n_map   x { u16 cell, u16 entry }
;   $FFFF at a frame boundary = the rest of this bank is padding; the next
;   frame starts at the next bank's $8000.  No record straddles a bank, which
;   is what lets IPTR stay a 16-bit offset plus a bank byte.
;
; THE BUDGET, and why the loop is shaped this way.  One NTSC vblank is 38
; lines x 1,364 = 51,832 master clocks.  At 12 fps an animation frame owns
; 60/12 = 5 of them: tiles go up in the first four, the tilemap flips in the
; fifth, so a frame is never on screen with the wrong map.  The first draft
; of the tile loop went through dma_vram -- a jsr per tile -- and priced out
; at ~45 tiles a vblank against a worst frame of 192, which does not fit in
; four.  Inlined, with the DMA channel programmed once per vblank and only
; VMADD/source/count touched per tile, a tile costs ~730 master clocks and
; the quota is 56: 4 x 56 = 224 >= 192.  Tiles DO land in slots the old map
; still points at, so the picture assembles progressively across those four
; vblanks -- the same compromise the Genesis player ships, visible for at
; most 4/60 s, and the map flip itself stays atomic in its own vblank.
;
; NMI IS OFF for the whole intro.  The engine's NMI handler clears the $4210
; vblank flag as its first act, and this player paces by polling exactly that
; flag -- with NMI enabled the handler eats every edge and ivwait never
; returns.  gsetup re-enables NMI afterwards as if the intro never ran.
; ===========================================================================

.ifndef SHARDS
        .error "the intro stream lives in the 2 MiB map -- build with SNES_SHARDED=1"
.endif

; ---- state (absolute low WRAM; $13E0+ is unclaimed past NPROFP) -----------
IFRM    = $13E0                 ; frames remaining
IVBL    = $13E2                 ; vblanks seen since intro start
IFV     = $13E4                 ; IVBL at the current frame's start
ITLEFT  = $13E6                 ; tile records left in this frame
IVPF    = $13E8                 ; vblanks per animation frame (60/fps)
ISCR    = $13EA                 ; nonzero once the screen has been unblanked
IMLEFT  = $13EC                 ; map records left in this frame

; ---- zero page (the $2E-$35 gap; set_shard's scratch is $3A+) -------------
IPTR    = $30                   ; 3 bytes: 16-bit offset + bank, for [IPTR]
ITMP    = $2E                   ; map-address scratch
ITM2    = $34                   ; second scratch for the shadow offset
ISHAD   = $1800                 ; the map shadow -- BG3SH's 2 KiB, borrowed

IBLANK  = 192                   ; the reserved blank tile: slot 192, zeroed
IHDR    = (INTROBANK << 16) | $8000

; the 16 header bytes, as long absolute reads -- no pointer needed
I_NFR   = IHDR + 8
I_FPS   = IHDR + 10
I_NPAL  = IHDR + 12

.proc ivwait
        ; wait for the NEXT start-of-vblank edge.  $4210 bit 7 latches at the
        ; edge and clears on read, and nothing else reads it while the intro
        ; owns the machine (see the NMI note above).
        sep #$20
        .a8
:       lda a:$4210
        bpl :-
        rep #$20
        .a16
        lda a:IVBL
        inc a
        sta a:IVBL
        rts
.endproc

.proc imapdma
        ; the whole shadow to V_BG1MAP: one channel, 2 KiB
        .a16
        lda #ISHAD
        sta a:DSRC
        lda #$0800
        sta a:DLEN
        lda #V_BG1MAP
        sta a:DDST
        sep #$20
        .a8
        stz a:DBNK                      ; the shadow is bank $00 WRAM
        rep #$20
        .a16
        jmp dma_vram
.endproc

.proc iadv2
        .a16
        lda z:IPTR
        clc
        adc #2
        sta z:IPTR
        rts
.endproc

.proc intro_play
        .a16
        .i16
        phb
        sep #$20
        .a8
        lda #$00
        pha
        plb                             ; DBR = 0: registers and low WRAM
        lda #$80
        sta INIDISP                     ; forced blank while the stage is set
        stz a:$4200                     ; NMI OFF -- see the header note
        rep #$20
        .a16

        ; magic, or walk away and let the game boot normally
        lda f:IHDR
        cmp #$5345                      ; 'ES'
        beq :+
        brl bail
:       lda f:IHDR + 2
        cmp #$3156                      ; 'V1'
        beq :+
        brl bail
:
        lda f:I_NFR
        sta a:IFRM
        stz a:IVBL
        stz a:IFV
        stz a:ISCR

        ; IVPF = 60 / fps, by repeated subtraction: fps is a header field and
        ; this runs once.  The first version put `inx` between the sbc and the
        ; branch that was supposed to test the sbc -- inx rewrites Z from X,
        ; which is never zero, so the loop only ever left on borrow and the
        ; count came out one high: 6 vblanks a frame, measured as 726 for 121
        ; frames by the very receipt this exists to satisfy.  The subtract is
        ; now the LAST flag-setting op before its branch.
        lda #60
        ldx #$0000
:       sec
        sbc f:I_FPS
        bcc :+                          ; borrowed: this one does not count
        inx
        bra :-
:       stx a:IVPF                      ; fps=12 -> 5

        ; ---- palettes: n_pals x 16 entries into CGRAM 0 --------------------
        lda #$8010                      ; header is 16 bytes
        sta a:DSRC
        lda f:I_NPAL
        asl a
        asl a
        asl a
        asl a
        asl a                           ; x32 bytes per palette
        sta a:DLEN
        lda #$0000
        sta a:DDST
        sep #$20
        .a8
        lda #INTROBANK
        sta a:DBNK
        rep #$20
        .a16
        jsr dma_cgram

        ; ---- PPU: mode 1, BG1 only, map at V_BG1MAP as 32x32, CHR at 0 -----
        sep #$20
        .a8
        lda #$01
        sta BGMODE
        lda #((V_BG1MAP >> 10) << 2)    ; 32x32: size bits 0
        sta BG1SC
        stz BG12NBA                     ; BG1 CHR base $0000
        lda #$01
        sta TMREG                       ; BG1 alone
        stz BG1HOFS
        stz BG1HOFS
        stz BG1VOFS
        stz BG1VOFS
        lda #$80
        sta VMAIN                       ; word-increment after the high byte
        rep #$20
        .a16

        ; ---- the blank tile: slot IBLANK, 16 zero words --------------------
        lda #(IBLANK * 16)
        sta VMADDL
        ldx #$0010
        lda #$0000
:       sta $2118
        dex
        bne :-

        ; ---- the map shadow: 1024 entries of blank tile in WRAM ------------
        ; BG3SH ($1800, 2 KiB) belongs to the game's BG3 machinery, which has
        ; not started yet -- the intro borrows it and gsetup rebuilds it.
        ldx #$0000
        lda #IBLANK
:       sta a:ISHAD, x
        inx
        inx
        cpx #$0800
        bne :-
        ; ...and into VRAM whole, by DMA.  Forced blank, so timing is free.
        jsr imapdma

        ; ---- IPTR = first frame: header + palettes -------------------------
        lda f:I_NPAL
        asl a
        asl a
        asl a
        asl a
        asl a
        clc
        adc #$8010
        sta z:IPTR
        sep #$20
        .a8
        lda #INTROBANK
        sta z:IPTR + 2
        rep #$20
        .a16

frame:
        ; a $FFFF where a tile count should be means "next bank"
        lda [IPTR]
        cmp #$FFFF
        bne nopad
        sep #$20
        .a8
        inc z:IPTR + 2
        rep #$20
        .a16
        lda #$8000
        sta z:IPTR
nopad:
        lda [IPTR]
        sta a:ITLEFT
        jsr iadv2

        ; ---- tiles, 48 a vblank ---------------------------------------------
        ; The quota was 56 and the loop went through ITLEFT in memory, and
        ; that combination overran the vblank: honestly counted at SlowROM
        ; fetch costs the tile body was ~1,080 master clocks, 56 of them 60k
        ; against a vblank of 51,832 -- so the last few tiles of every full
        ; vblank landed in active display, where VRAM writes glitch to the
        ; address the PPU is fetching.  The receipt measured it as 590 bad
        ; bytes across 32 slots.  The count now lives in X (dex against a
        ; memory read-modify-write), the DMA trigger is a 16-bit store that
        ; also writes a zero into HDMAEN -- which holds nothing during the
        ; intro -- and the quota is 48: ~784 master clocks a tile, 37,632 a
        ; vblank, 73% of budget, and the 192-tile peak frame is exactly four
        ; vblanks, which with the map flip's fifth is the whole 12 fps frame.
        lda a:ITLEFT
        tax
tvbl:   cpx #$0000
        beq tdone
        jsr ivwait
        sep #$20
        .a8
        lda #$80
        sta VMAIN
        lda #$01
        sta $4300                       ; two registers, once
        lda #$18
        sta $4301                       ; $2118/$2119
        lda z:IPTR + 2
        sta $4304                       ; the stream's bank, fixed all frame
        rep #$20
        .a16
        ldy #$0030                      ; the 48-tile quota
tile:   lda [IPTR]                      ; slot
        asl a
        asl a
        asl a
        asl a                           ; x16 words
        sta $2116                       ; VMADDL/H in one 16-bit store
        lda z:IPTR
        inc a
        inc a
        sta $4302                       ; source = the 32 tile bytes
        lda #$0020
        sta $4305
        lda #$0001
        sta $420B                       ; MDMAEN, and a harmless 0 to HDMAEN
        lda z:IPTR
        clc
        adc #34                         ; slot word + 32 bytes
        sta z:IPTR
        dex
        beq tdone
        dey
        bne tile
        bra tvbl
tdone:

        ; ---- the map deltas, into the WRAM shadow, in ACTIVE display -------
        ; The first version wrote every cell straight to $2118 in the flip
        ; vblank.  192 cells is ~11,500 CPU cycles against a vblank of ~8,600,
        ; and a VRAM write that lands outside blank does not just drop -- it
        ; glitches to whatever address the PPU is fetching.  The receipt
        ; showed exactly that: 44 stale map cells AND 222 corrupted bytes
        ; across 12 tile slots, both from the same overrun.  So the deltas go
        ; to a WRAM shadow with the beam wherever it likes, and the flip is
        ; one 2 KiB DMA that costs 16,384 of the vblank's 51,832 master
        ; clocks, constant, whatever the frame changed.
        lda [IPTR]
        sta a:IMLEFT
        jsr iadv2
        lda a:IMLEFT
        beq mflip
mcell:  lda [IPTR]                      ; cell 0..191 in the 16x12 window
        sta z:ITMP
        and #$FFF0                      ; (row * 32) << 1: row and column are
        asl a                           ; disjoint bit fields, so OR is the
        sta z:ITM2                      ; address arithmetic
        lda z:ITMP
        and #$000F
        ora z:ITM2
        clc
        adc #(8 * 32 + 8)               ; centre 16x12 on the 32x28 screen
        asl a                           ; word index -> byte offset
        tax
        jsr iadv2
        lda [IPTR]                      ; the entry: slot | palette << 10
        sta a:ISHAD, x
        jsr iadv2
        lda a:IMLEFT
        dec a
        sta a:IMLEFT
        bne mcell
mflip:  jsr ivwait
        jsr imapdma

        ; first presented frame: the screen comes on only now, so nobody sees
        ; the map being assembled
        lda a:ISCR
        bne paced
        sep #$20
        .a8
        lda #$0F
        sta INIDISP
        rep #$20
        .a16
        lda #$0001
        sta a:ISCR

        ; ---- pace: hold until this frame has owned 60/fps vblanks ----------
paced:  lda a:IVBL
        sec
        sbc a:IFV
        cmp a:IVPF
        bcs pdone
        jsr ivwait
        bra paced
pdone:  lda a:IFV
        clc
        adc a:IVPF
        sta a:IFV

        lda a:IFRM
        dec a
        sta a:IFRM
        beq fin
        brl frame

fin:
        sep #$20
        .a8
        lda #$80
        sta INIDISP                     ; blank for the handoff
        rep #$20
        .a16
.ifdef INTROQA
        jsr intro_dump
:       bra :-                          ; the receipt is written; hold still
.endif
        plb
        rts
bail:
        ; no stream (or not ours): hand the machine straight to the game
        plb
        rts
.endproc

.ifdef INTROQA
; ---------------------------------------------------------------------------
; The receipt.  Read the BG1 map, all 193 tile slots and CGRAM back out
; through the PPU into SRAM, so tools/check_intro.py can compare the END
; STATE of the player against the END STATE of the reference decoder.  The
; idiom -- one dummy pair after setting the VRAM address, raw CGRAM from
; entry 0 with bit 15 masked on the host -- is the one the game's own
; readback established by A/B on this instrument.
; ---------------------------------------------------------------------------
SR_IMAP = $0800                 ; 2 KiB: the 32x32 BG1 map
SR_ICHR = $1000                 ; 6,176 B: slots 0..192 as 4bpp bytes
SR_ICGR = $2900                 ; 512 B: CGRAM raw
SR_ISTA = $2B00                 ; vblank count, IVPF, end bank

.proc intro_dump
        .a16
        .i16
        sep #$20
        .a8
        lda #$80
        sta VMAIN
        rep #$20
        .a16
        lda #V_BG1MAP
        sta VMADDL
        sep #$20
        .a8
        lda RDVRAML                     ; the dummy pair
        lda RDVRAML + 1
        rep #$20
        .a16
        ldx #$0000
:       sep #$20
        .a8
        lda RDVRAML
        sta f:SRAM + SR_IMAP, x
        lda RDVRAML + 1
        sta f:SRAM + SR_IMAP + 1, x
        rep #$20
        .a16
        inx
        inx
        cpx #$0800
        bne :-

        lda #$0000                      ; CHR from word 0
        sta VMADDL
        sep #$20
        .a8
        lda RDVRAML
        lda RDVRAML + 1
        rep #$20
        .a16
        ldx #$0000
:       sep #$20
        .a8
        lda RDVRAML
        sta f:SRAM + SR_ICHR, x
        lda RDVRAML + 1
        sta f:SRAM + SR_ICHR + 1, x
        rep #$20
        .a16
        inx
        inx
        cpx #((IBLANK + 1) * 32)
        bne :-

        sep #$20
        .a8
        stz CGADD
        rep #$30
        .a16
        .i16
        ldx #$0000
:       sep #$20
        .a8
        lda RDCGRAM
        sta f:SRAM + SR_ICGR, x
        rep #$30
        .a16
        .i16
        inx
        cpx #$0200
        bne :-

        lda a:IVBL
        sta f:SRAM + SR_ISTA
        lda a:IVPF
        sta f:SRAM + SR_ISTA + 2
        lda #$0000
        sep #$20
        .a8
        lda z:IPTR + 2
        sta f:SRAM + SR_ISTA + 4
        ; the marker tools/run_ares.sh waits for
        lda #'D'
        sta f:SRAM + 8
        lda #'O'
        sta f:SRAM + 9
        lda #'N'
        sta f:SRAM + 10
        lda #'E'
        sta f:SRAM + 11
        rep #$20
        .a16
        rts
.endproc
.endif
