; ---------------------------------------------------------------------------
; game.inc -- the presentation layer.  Included by rom/nn.s under -DGAME.
;
; THE SHAPE, AND WHY IT IS THIS SHAPE
;
; The main thread is the transformer and nothing else.  It runs forward passes
; back to back, for ever.  Every frame the vblank NMI stops it, does the entire
; game -- OAM DMA, scroll, text, input, physics, the state machine -- and hands
; it back.  So the "game loop" is the NMI and the "idle task" is inference,
; which is the inverse of how a SNES game is normally written and is the only
; arrangement in which presentation cannot slow the model down by more than the
; fixed cost of one vblank.
;
; THE BINDING RULE, which is the whole point of the exercise:
;
;   A COIN MAY ONLY BE SPAWNED FROM THE CODE PATH THAT COMMITS A GENERATED
;   TOKEN.
;
; Stated so that grep can check it rather than so that it sounds good:
;
;   * COINQ is INCREMENTED in exactly one place, `gcommit`.
;   * COINQ is DECREMENTED in exactly one place, `spawn_coins`, in vblank.
;   * COINQ is zeroed once, in `gsetup`.
;   * A coin slot goes from free to live in exactly one place -- the `sta
;     a:COINT, x` inside `spawn_coins`, in the same basic block as that
;     decrement.  `move_coins` only ever counts a live slot down.
;   * Every other mention of COINQ in this file is a read, into the trace or
;     the SRAM dump.
;
; There is no timer and no frame counter anywhere in the producing path.
;
; So if inference stalls, the coins stall.  COINSP + COINQ == TOKCNT is an
; invariant the ROM writes to SRAM at the end of a run and tools/check_game.py
; asserts.  The -DNOGEN build removes the forward pass and nothing else; it
; must produce zero coins over the same number of frames, which is the negative
; control for the whole claim.
;
; WHAT THE INFERENCE PATH IS ALLOWED TO DO (FINDINGS' Genesis complaint: its
; drawTokSpeed() runs a sprintf and a VRAM write INSIDE the measured interval):
; `gcommit` stores a token, increments two counters and pushes one byte into a
; ring.  It touches no PPU register, formats no text and writes no VRAM.  The
; NMI drains the ring and does all of that.
; ---------------------------------------------------------------------------

        .include "gdata.inc"            ; out/game -- NQUEST, prompt sizes
        .include "obj.inc"              ; assets   -- OBJT_* sprite tiles
        .include "font.inc"             ; assets   -- FONT_PAGE*, BOXT_*
        .include "bg.inc"               ; assets   -- LEVEL_*, BGT_*

; ---- hardware registers not already named in snes.inc ---------------------
OBSEL    = $2101
OAMADDL  = $2102
OAMDATA  = $2104
BG1SC    = $2107
BG2SC    = $2108
BG3SC    = $2109
BG12NBA  = $210B
BG34NBA  = $210C
BG1HOFS  = $210D
BG1VOFS  = $210E
BG2HOFS  = $210F
BG2VOFS  = $2110
BG3HOFS  = $2111
BG3VOFS  = $2112
VMAIN    = $2115
VMADDL   = $2116
VMDATAL  = $2118
CGADD    = $2121
CGDATA   = $2122
TMREG    = $212C
TSREG    = $212D
RDOAM    = $2138
RDVRAML  = $2139
RDCGRAM  = $213B
MDMAEN   = $420B
HDMAEN   = $420C
HVBJOY   = $4212
JOY1     = $4218

; ---- where the game's data lives -----------------------------------------
; ld65 has no idea a LoROM bank is 32 KiB, so the GAMEDATA segment's labels are
; plain 16-bit addresses and the bank is added here as a constant.  Every read
; of it is long-addressed off GDBASE.
.ifdef FASTROM
GDBASE   = $860000
.else
GDBASE   = $060000
.endif

; ---- VRAM map, in WORD addresses where the PPU wants them -----------------
V_BG1CHR = $0000        ; byte $0000
V_BG2CHR = $1000        ; byte $2000   (BG char bases step 8 KiB)
V_BG3CHR = $2000        ; byte $4000
V_OBJCHR = $4000        ; byte $8000   (OBJ base steps 16 KiB)
V_BG1MAP = $6000        ; byte $C000   64x32, 4 KiB
V_BG2MAP = $6800        ; byte $D000   32x32, 2 KiB
V_BG3MAP = $6C00        ; byte $D800   32x32, 2 KiB

; ---- states ---------------------------------------------------------------
ST_LOGO   = 0
ST_PLAY   = 1
ST_STOP   = 2
ST_LINE   = 3
ST_ASK    = 4
ST_ANSWER = 5
ST_END    = 6

; ---- what the main thread is doing ---------------------------------------
GM_IDLE  = 0
GM_FREE  = 1
GM_FEED  = 2
GM_GEN   = 3

GRQ_NONE = 0
GRQ_FREE = 1
GRQ_LINE = 2
GRQ_ASK  = 3
GRQ_STOP = 4

; ---- tuning ---------------------------------------------------------------
.ifdef GFAST
; A short arc, for iterating on the verification run without waiting nine
; seconds of platformer every time.  It changes nothing about the code paths.
LOGO_FRAMES = 20
ACT1_TOKENS = 10
STOP_FRAMES = 20
.else
LOGO_FRAMES = 200               ; ~3.3 s, or Start
ACT1_TOKENS = 64                ; ~9 s of platformer at the measured 7 tok/s
STOP_FRAMES = 100
.endif
GRAV     = 6                    ; 12.4 fixed: 0.375 px/frame^2
JUMPV    = $FFA4                ; -92 = -5.75 px/frame
RUNSPD   = 26                   ; 1.625 px/frame
MAXFALL  = 96
HB_L     = 10                   ; her hitbox inside the 32x32 cell
HB_W     = 12
HB_T     = 4
HB_H     = 28
BLK_X    = 200                  ; the @ block, in world pixels
BLK_Y    = 112
STOP_X   = 128                  ; where she comes to a halt
NCOIN    = 8

PAD_B     = $8000
PAD_START = $1000
PAD_UP    = $0800
PAD_DOWN  = $0400
PAD_LEFT  = $0200
PAD_RIGHT = $0100
PAD_A     = $0080

; ---- game RAM.  All of it is in low WRAM, which banks $00 and $7E both see
;      at the same addresses -- so the NMI (DBR $00, for the registers) and the
;      engine's main thread (DBR $7E, which it needs) reach every variable with
;      a plain absolute access and neither needs long addressing.
GSTATE  = $1300
GTIMER  = $1302
FRAMEC  = $1304
PADNOW  = $1306
PADNEW  = $1308
PX      = $130A
PY      = $130C
VXX     = $130E
VYY     = $1310
ONGND   = $1312
FACING  = $1314
ANIMF   = $1316
ANIMT   = $1318
CAMX    = $131A
NX      = $131C
NVX     = $131E
NANIM   = $1320
BLKT    = $1322
COINQ   = $1324
TOKCNT  = $1326
COINSP  = $1328
BG3DRT  = $132A
TCOL    = $132C
TROW    = $132E
MENUI   = $1330
GREQ    = $1332
GQSEL   = $1334
GENMODE = $1336
GENLEFT = $1338
PROMPTI = $133A
PROMPTN = $133C
LASTTOK = $133E
GNMION  = $1340
TRW     = $1342
TRR     = $1344
RELOAD  = $1346
HUDN    = $1348
GDONE   = $134A
TMPW    = $134C
TMPW2   = $134E
OSX     = $1350
OSY     = $1352
OST     = $1354
OSA     = $1356
OSZ     = $1358
OSLOT   = $135A
DSRC    = $135C
DBNK    = $135E
DLEN    = $1360
DDST    = $1362
NY      = $1364
TRACEP  = $1366
ANSN    = $1368
TOKN    = $136A                 ; tokens written to the SRAM stream
LINEN   = $136C
ANSP    = $136E
STICK   = $1370                 ; frames the auto-player has held a jump
TPAGE   = $1372                 ; which font page the cursor writes
TPAL    = $1374                 ; palette + priority bits for it
SLEN    = $1376
TMPW3   = $1378
HIACC   = $137A                 ; the OAM high table, accumulated 4 sprites
STRIKE  = $137C                 ; the block was hit this frame

COINX   = $1380
COINY   = $1390
COINV   = $13A0
COINT   = $13B0
COINF   = $13C0
; Scratch is per-routine on purpose.  The first draft shared TMPW between
; gputc and its callers, and print_token's remaining-length counter was being
; overwritten by the character it had just drawn -- which shows up as text that
; stops after one letter, not as a crash.
SCR1    = $13D0                 ; solid_at
GP1     = $13D2                 ; gputc
GP2     = $13D4
GBUSY   = $13D6                 ; the main thread has a job in flight
NMIBUSY = $13DA                 ; the vblank handler is already inside itself
CAMY    = $13DC                 ; the camera tilts up when she stops talking
NPROFP  = $13DE                 ; -DNMIPROF cursor
SCR2    = $13D8                 ; hit_test's sample cursor

OAMBUF  = $1400                 ; 512 B, DMA'd to OAM every frame
OAMHI   = $1600                 ; 32 B
TOKRING = $1640                 ; 64 B, main thread -> NMI
BG3SH   = $1800                 ; 2 KiB, the BG3 tilemap shadow

; ---- SRAM map (32 KiB; the game declares ramsize $05) ---------------------
SR_TOK   = $0010        ; the free run's first 20 tokens -- check_nn.py's format
SR_TRACE = $0200        ; per-16-frame trace records
SR_STAT  = $1000        ; the counters the binding proof reads
SR_OAM   = $1100        ; OAM read back THROUGH THE PPU, 544 B
SR_VM3   = $1400        ; the BG3 tilemap read back out of VRAM, 2 KiB
SR_CGR   = $1C00        ; CGRAM read back, 512 B
SR_LINE  = $2000        ; act 2: seed + 19 generated tokens
SR_ANS   = $2040        ; act 3: [q][n][20 tokens] per answer
SR_SHAD  = $2200        ; the OAM shadow in WRAM, for comparison with SR_OAM
SR_NMI   = $6000        ; -DNMIPROF: 256 frames of (V,H) at NMI entry/exit
SR_CGR2  = $5000        ; CGRAM read a second time, back to back
SR_SNAP1 = $3000        ; a frame captured mid-act-1, coins in the air
SR_SNAP2 = $4000        ; the last frame of the run, act 3 on screen
                        ; each is OAM shadow (544) + BG3 shadow (2048) + scroll

; ---------------------------------------------------------------------------
; DMA.  Three shapes, all through one routine, because getting $4300's transfer
; mode wrong is silent -- the data lands, in the wrong place, and the screen
; just looks odd.
; ---------------------------------------------------------------------------
.macro DMAVRAM lbl, len, dst
        lda #.loword(lbl)
        sta a:DSRC
        lda #(len)
        sta a:DLEN
        lda #(dst)
        sta a:DDST
        sep #$20
        .a8
        lda #.bankbyte(GDBASE + lbl)
        sta a:DBNK
        rep #$20
        .a16
        jsr dma_vram
.endmacro

.macro DMAVRAMW src, bank, len, dst
        lda #(src)
        sta a:DSRC
        lda #(len)
        sta a:DLEN
        lda #(dst)
        sta a:DDST
        sep #$20
        .a8
        lda #(bank)
        sta a:DBNK
        rep #$20
        .a16
        jsr dma_vram
.endmacro

.macro DMACG lbl, len, first
        lda #.loword(lbl)
        sta a:DSRC
        lda #(len)
        sta a:DLEN
        lda #(first)
        sta a:DDST
        sep #$20
        .a8
        lda #.bankbyte(GDBASE + lbl)
        sta a:DBNK
        rep #$20
        .a16
        jsr dma_cgram
.endmacro

.proc dma_vram
        .a16
        .i16
        sep #$20
        .a8
        lda #$80                        ; increment after the high byte
        sta VMAIN
        rep #$20
        .a16
        lda a:DDST
        sta VMADDL
        sep #$20
        .a8
        lda #$01                        ; two registers, once: $2118, $2119
        sta $4300
        lda #$18
        sta $4301
        rep #$20
        .a16
        lda a:DSRC
        sta $4302
        sep #$20
        .a8
        lda a:DBNK
        sta $4304
        rep #$20
        .a16
        lda a:DLEN
        sta $4305
        sep #$20
        .a8
        lda #$01
        sta MDMAEN
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc dma_cgram
        .a16
        .i16
        sep #$20
        .a8
        lda a:DDST
        sta CGADD
        lda #$00                        ; one register, once: $2122
        sta $4300
        lda #$22
        sta $4301
        rep #$20
        .a16
        lda a:DSRC
        sta $4302
        sep #$20
        .a8
        lda a:DBNK
        sta $4304
        rep #$20
        .a16
        lda a:DLEN
        sta $4305
        sep #$20
        .a8
        lda #$01
        sta MDMAEN
        rep #$30
        .a16
        .i16
        rts
.endproc

; ===========================================================================
; the main thread: forward passes, for ever
; ===========================================================================
.proc game_main
        .a16
        .i16
        jsr gsetup                      ; graphics, HDMA, NMI on.  Sets DBR
        lda #$7E                        ; back to the engine's data bank
        pha
        plb

        stz a:GENMODE
        stz a:GREQ
mloop:
.ifdef PROFILE
        ; The token boundaries the profiler measures are the FIRST NGEN+1 of
        ; the free run, at the same positions the engine-only build measures,
        ; so the two numbers are directly comparable.
        lda a:SMPX
        cmp #($0140 + (NGEN + 1) * 6)   ; exactly NGEN+1 boundaries, which is
        bcs :+                          ; what tools/prof_nn.py reads
        lda a:GENMODE
        cmp #GM_FREE
        bne :+
        jsr sample
:
.endif
        lda a:GREQ
        beq nojob
        jsr take_request
nojob:
        lda a:GENMODE
        beq mloop                       ; nothing to generate: the NMI runs on
        cmp #GM_FEED
        beq mfeed

        ; ---- one generated token ------------------------------------------
.ifdef NOGEN
        brl mloop                       ; the control: no forward pass, so no
.endif                                  ; commit, so no coin, ever
        jsr step
        jsr gcommit
        lda a:POSP
        inc a
        cmp #NCTX
        bcc :+
        lda #$0000                      ; context is 20 positions and that is
:       sta a:POSP                      ; what the positional table was trained
                                        ; with; wrap rather than pretend
        lda a:GENMODE
        cmp #GM_GEN
        bne mloop
        lda a:GENLEFT
        dec a
        sta a:GENLEFT
        bne mloop
        stz a:GENMODE
        stz a:GBUSY                     ; the NMI watches THIS for "done"
        bra mloop

        ; ---- one prompt token: input, not output.  No coin. ---------------
mfeed:
        jsr prompt_token                ; -> A = the question's PROMPTI'th token
        sta a:TOKP
        ora #$0080                      ; ring flag: draw it as the question
        jsr ring_push
        jsr step
        lda a:PROMPTI
        inc a
        sta a:PROMPTI
        sta a:POSP
        cmp a:PROMPTN
        bcc mloop
        ; The last feed step's output IS the first generated token.  Switch
        ; mode BEFORE committing it: the SRAM recorder keys off GENMODE, and
        ; committing first dropped this token from every recorded answer.
        lda #19
        sec
        sbc a:PROMPTN
        sta a:GENLEFT
        lda #GM_GEN
        sta a:GENMODE
        jsr gcommit
        brl mloop
.endproc

; ---------------------------------------------------------------------------
; gcommit -- THE ONLY PLACE A TOKEN IS COMMITTED, AND THE ONLY WRITER OF COINQ.
;
; Deliberately tiny.  It stores the token, bumps two counters and drops one
; byte in a ring buffer.  No PPU register, no formatting, no VRAM: all of that
; is the NMI's job, so none of it is inside the interval that measures the
; model.
; ---------------------------------------------------------------------------
.proc gcommit
        .a16
        .i16
        lda a:NEXTT
        sta a:TOKP
        sta a:LASTTOK
        lda a:TOKCNT
        inc a
        sta a:TOKCNT
        lda a:COINQ                     ; <-- one of exactly two sites
        inc a
        sta a:COINQ
        lda a:TOKP
        jsr ring_push
        jsr sram_token
        rts
.endproc

; the ring the NMI drains for text.  bit 7 set = "this was the question", which
; is what makes the screen itself show which characters the console generated.
.proc ring_push
        .a16
        .i16
        pha
        lda a:TRW
        tax
        pla
        sep #$20
        .a8
        sta a:TOKRING, x
        rep #$20
        .a16
        lda a:TRW
        inc a
        and #$003F
        sta a:TRW
        rts
.endproc

; A = index, GQSEL = question -> A = that prompt token
.proc prompt_token
        .a16
        .i16
        lda a:GQSEL
        asl a
        tax
        lda f:GDBASE + g_qoff, x
        clc
        adc a:PROMPTI
        tax
        sep #$20
        .a8
        lda f:GDBASE + g_qtok, x
        rep #$20
        .a16
        and #$00FF
        rts
.endproc

; ---------------------------------------------------------------------------
; the token stream the existing checkers read.  The first NGEN+1 tokens of the
; free run land in check_nn.py's exact format, so the GAME cartridge proves its
; own arithmetic against host/ref.py with the tool the engine already used.
; ---------------------------------------------------------------------------
.proc sram_token
        .a16
        .i16
        lda a:GENMODE
        cmp #GM_FREE
        bne notfree
        lda a:TOKN
        cmp #(NGEN + 1)
        bcs notfree
        tax
        inc a
        sta a:TOKN
        sep #$20
        .a8
        lda a:TOKP
        sta f:SRAM + SR_TOK, x
        rep #$20
        .a16
        rts
notfree:
        lda a:GENMODE
        cmp #GM_GEN
        bne :+
        lda a:GSTATE
        cmp #ST_LINE
        bne ans
        lda a:LINEN                     ; act 2's line
        cmp #20
        bcs :+
        tax
        inc a
        sta a:LINEN
        sep #$20
        .a8
        lda a:TOKP
        sta f:SRAM + SR_LINE + 1, x
        rep #$20
        .a16
        rts
ans:    cmp #ST_ANSWER
        bne :+
        lda a:ANSP                      ; act 3's answer
        cmp #20
        bcs :+
        clc
        adc a:ANSN
        tax
        lda a:ANSP
        inc a
        sta a:ANSP
        sep #$20
        .a8
        lda a:TOKP
        sta f:SRAM + SR_ANS + 2, x
        rep #$20
        .a16
:       rts
.endproc

; ---------------------------------------------------------------------------
; take_request -- the NMI never touches engine state.  It raises a request and
; the main thread picks it up here, between forward passes, where nothing is
; half-written.  That is the whole of the concurrency design.
; ---------------------------------------------------------------------------
.proc take_request
        .a16
        .i16
        lda a:GREQ
        stz a:GREQ
        cmp #GRQ_FREE
        beq rfree
        cmp #GRQ_LINE
        beq rline
        cmp #GRQ_ASK
        beq rask
        stz a:GENMODE                   ; GRQ_STOP
        rts
rfree:
        stz a:POSP
        lda #SEEDTOK
        sta a:TOKP
        lda #$0001
        sta a:TOKN                      ; token 0 of the stream is the seed
        sep #$20
        .a8
        lda #SEEDTOK
        sta f:SRAM + SR_TOK
        rep #$20
        .a16
        lda #GM_FREE
        sta a:GENMODE
        rts
rline:
        stz a:POSP
        lda a:LASTTOK                   ; act 1's last token seeds act 2's line
        sta a:TOKP
        ora #$0080
        jsr ring_push
        stz a:LINEN
        sep #$20
        .a8
        lda a:LASTTOK
        sta f:SRAM + SR_LINE
        rep #$20
        .a16
        lda #19
        sta a:GENLEFT
        lda #GM_GEN
        sta a:GENMODE
        rts
rask:
        stz a:POSP
        stz a:PROMPTI
        stz a:ANSP
        lda a:GQSEL
        tax
        sep #$20
        .a8
        lda f:GDBASE + g_qlen, x
        rep #$20
        .a16
        and #$00FF
        sta a:PROMPTN
        ; record which question, and how long, for the host-side check
        lda a:ANSN
        tax
        sep #$20
        .a8
        lda a:GQSEL
        sta f:SRAM + SR_ANS, x
        lda a:PROMPTN
        sta f:SRAM + SR_ANS + 1, x
        rep #$20
        .a16
        lda #GM_FEED
        sta a:GENMODE
        rts
.endproc

; ===========================================================================
; graphics bring-up.  Runs once, in forced blank, before anything is shown.
; ===========================================================================
.proc gsetup
        .a16
        .i16
        sep #$20
        .a8
        lda #$00
        pha
        plb                             ; DBR = 0 for the whole of setup
        lda #$8F
        sta INIDISP                     ; forced blank
        stz HDMAEN
        rep #$30
        .a16
        .i16

        jsr clear_gram
        jsr clear_bg3

        ; ---- static VRAM ---------------------------------------------------
        DMAVRAM g_objchr, (g_objchr_end - g_objchr), V_OBJCHR
        DMAVRAM g_fontchr, (g_fontchr_end - g_fontchr), V_BG3CHR
        DMAVRAM g_bg2chr, (g_bg2chr_end - g_bg2chr), V_BG2CHR
        DMAVRAM g_cloudmap, (g_cloudmap_end - g_cloudmap), V_BG2MAP
        ; act 0 shows the logo on BG1; the level replaces it on the way out
        DMAVRAM g_logochr, (g_logochr_end - g_logochr), V_BG1CHR
        DMAVRAM g_logomap, (g_logomap_end - g_logomap), V_BG1MAP
        jsr push_bg3

        ; ---- palettes ------------------------------------------------------
        DMACG g_bg3pal, (g_bg3pal_end - g_bg3pal), 0
        DMACG g_logopal, (g_logopal_end - g_logopal), 32
        DMACG g_bg2pal, (g_bg2pal_end - g_bg2pal), 48
        DMACG g_objpal, (g_objpal_end - g_objpal), 128

        sep #$20
        .a8
        lda #$09                        ; mode 1, BG3 priority high
        sta BGMODE
        lda #((V_BG1MAP >> 10) << 2) | 1        ; 64x32
        sta BG1SC
        lda #((V_BG2MAP >> 10) << 2)
        sta BG2SC
        lda #((V_BG3MAP >> 10) << 2)
        sta BG3SC
        lda #((V_BG2CHR >> 12) << 4) | (V_BG1CHR >> 12)
        sta BG12NBA
        lda #(V_BG3CHR >> 12)
        sta BG34NBA
        lda #$60 | (V_OBJCHR >> 13)     ; 16x16 / 32x32, name base
        sta OBSEL
        lda #$01                        ; act 0: BG1 only
        sta TMREG
        stz TSREG
        rep #$30
        .a16
        .i16

.ifndef NOSKY
        jsr sky_hdma
.endif

        ldx #$0000
:       stz a:COINT, x                  ; every slot free.  Without this the
        stz a:COINX, x                  ; spawner finds no free slot, ever:
        stz a:COINY, x                  ; WRAM comes up with whatever was in
        stz a:COINV, x                  ; it and a nonzero timer means "live"
        stz a:COINF, x
        inx
        inx
        cpx #(NCOIN * 2)
        bne :-
        jsr init_play

        stz a:NMIBUSY
        stz a:NPROFP
        stz a:GSTATE
        stz a:GTIMER
        stz a:FRAMEC
        stz a:COINQ
        stz a:TOKCNT
        stz a:COINSP
        stz a:TOKN
        stz a:ANSN
        stz a:ANSP
        stz a:LINEN
        stz a:GDONE
        stz a:TRW
        stz a:TRR
        stz a:MENUI
        stz a:RELOAD
        stz a:TRACEP
        stz a:STICK
        lda #$FFFF
        sta a:HUDN

        sep #$20
        .a8
        lda #$0F
        sta INIDISP                     ; screen on
        lda #$81                        ; vblank NMI + auto joypad read
        sta NMITIMEN
        rep #$30
        .a16
        .i16
        lda #$0001
        sta a:GNMION                    ; from here the NMI is the game
        cli
        rts
.endproc

; the sky.  One HDMA channel writes CGRAM entry 0 -- the backdrop -- once every
; eight scanlines.  Transfer mode 3 sends four bytes to $2121,$2121,$2122,$2122
; which is exactly "point CGRAM at colour 0, then write both halves of it".
.proc sky_hdma
        .a16
        .i16
        sep #$20
        .a8
        lda #$03                        ; mode 3, A->B, direct table
        sta $4370
        lda #$21                        ; $2121
        sta $4371
        lda #.lobyte(g_sky)
        sta $4372
        lda #.hibyte(g_sky)
        sta $4373
        lda #.bankbyte(GDBASE + g_sky)
        sta $4374
        lda #$80
        sta HDMAEN
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc clear_gram
        .a16
        .i16
        ldx #$0000
:       stz a:OAMBUF, x                 ; every sprite off the bottom of the
        inx                             ; screen until build_oam places it
        inx
        cpx #$0200
        bne :-
        ldx #$0000
        lda #$F0F0
:       sta a:OAMBUF, x                 ; Y = $F0 in both entries of the word
        inx
        inx
        inx
        inx
        cpx #$0200
        bne :-
        ldx #$0000
:       stz a:OAMHI, x
        inx
        inx
        cpx #$0020
        bne :-
        rts
.endproc

.proc clear_bg3
        .a16
        .i16
        ldx #$0000
        lda #$0000                      ; tile 0 = space, page 0 = transparent
:       sta a:BG3SH, x
        inx
        inx
        cpx #$0800
        bne :-
        lda #$0001
        sta a:BG3DRT
        rts
.endproc

; ===========================================================================
; the NMI: the whole game, once a frame
; ===========================================================================
game_nmi:
        php
        rep #$30
        .a16
        .i16
        pha
        phx
        phy
        phd
        phb
        lda #$0000
        tcd
        sep #$20
        .a8
        lda #$00
        pha
        plb                             ; DBR = 0: registers and low WRAM
        lda a:$4210                     ; clear the vblank flag
        rep #$30
        .a16
        .i16
.ifdef PROFILE
        lda a:FRAMES
        inc a
        sta a:FRAMES
.endif
        lda a:GNMION
        bne :+
        brl nmi_out                     ; before gsetup: a frame counter only
:       lda a:NMIBUSY
        beq :+
        brl nmi_out                     ; a long frame is still in progress
:       lda #$0001
        sta a:NMIBUSY
.ifdef NMIPROF
        ; The logo state fills the buffer before the game starts, so sampling
        ; begins once there is a game to measure.
        lda a:GSTATE
        beq :+
        jsr latch_hv
        ldx a:NPROFP
        cpx #$0800
        bcs :+
        lda a:VSCR
        sta f:SRAM + SR_NMI + 0, x
        lda a:HSCR
        sta f:SRAM + SR_NMI + 2, x
:
.endif

        lda a:FRAMEC
        inc a
        sta a:FRAMEC

        ; ---- PPU work first, while it is still vblank ---------------------
        jsr oam_dma
        jsr scroll_out
        lda a:BG3DRT
        beq :+
        jsr push_bg3
        stz a:BG3DRT
:
        lda a:RELOAD
        beq :+
        jsr do_reload
:
        ; ---- and only then the logic --------------------------------------
        jsr read_pad
        jsr drain_ring
        jsr state_update
        jsr spawn_coins
        jsr move_coins
        jsr build_oam
        jsr trace
.ifdef NMIPROF
        lda a:GSTATE
        beq :+
        jsr latch_hv
        ldx a:NPROFP
        cpx #$0800
        bcs :+
        lda a:VSCR
        sta f:SRAM + SR_NMI + 4, x
        lda a:HSCR
        sta f:SRAM + SR_NMI + 6, x
        lda a:GSTATE
        sta f:SRAM + SR_NMI + 8, x
        lda a:COINSP
        sta f:SRAM + SR_NMI + 10, x
        txa
        clc
        adc #12
        sta a:NPROFP
:
.endif
        stz a:NMIBUSY

nmi_out:
        plb
        pld
        ply
        plx
        pla
        plp
        rti

; ---------------------------------------------------------------------------
.ifdef NMIPROF
; latch the PPU's H and V counters -- $2137 samples both, $213C/$213D read them
.proc latch_hv
        .a16
        .i16
        sep #$20
        .a8
        lda SLHV
        lda STAT78
        lda OPHCT
        sta a:HSCR
        lda OPHCT
        and #$01
        sta a:HSCR + 1
        lda OPVCT
        sta a:VSCR
        lda OPVCT
        and #$01
        sta a:VSCR + 1
        rep #$30
        .a16
        .i16
        rts
.endproc
.endif

.proc oam_dma
        .a16
        .i16
        sep #$20
        .a8
        stz OAMADDL
        stz OAMADDL + 1
        lda #$00                        ; 1 register, write once -> $2104
        sta $4300
        lda #$04
        sta $4301
        rep #$20
        .a16
        lda #OAMBUF
        sta $4302
        sep #$20
        .a8
        lda #$00
        sta $4304
        rep #$20
        .a16
        lda #$0220                      ; 512 + 32
        sta $4305
        sep #$20
        .a8
        lda #$01
        sta MDMAEN
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc scroll_out
        .a16
        .i16
        lda a:CAMX
        sta a:TMPW
        sep #$20
        .a8
        lda a:TMPW
        sta BG1HOFS
        lda a:TMPW + 1
        sta BG1HOFS
        rep #$20
        .a16
        lda a:CAMY
        sta a:TMPW
        sep #$20
        .a8
        lda a:TMPW
        sta BG1VOFS
        lda a:TMPW + 1
        sta BG1VOFS
        rep #$20
        .a16
        lda a:CAMX                      ; clouds at half speed: parallax, free
        lsr a
        sta a:TMPW
        sep #$20
        .a8
        lda a:TMPW
        sta BG2HOFS
        lda a:TMPW + 1
        sta BG2HOFS
        rep #$20
        .a16
        lda a:CAMY
        lsr a
        sta a:TMPW
        sep #$20
        .a8
        lda a:TMPW
        sta BG2VOFS
        lda a:TMPW + 1
        sta BG2VOFS
        stz BG3HOFS
        stz BG3HOFS
        stz BG3VOFS
        stz BG3VOFS
        rep #$30
        .a16
        .i16
        rts
.endproc

.proc push_bg3
        .a16
        .i16
        DMAVRAMW BG3SH, $00, $0800, V_BG3MAP
        rts
.endproc

; swapping the logo out for the level is 4 KiB of tilemap, which does not fit
; in one vblank alongside everything else -- so the frame it happens on is
; forced blank on purpose.  One black frame at a scene change.
.proc do_reload
        .a16
        .i16
        sep #$20
        .a8
        lda #$8F
        sta INIDISP
        rep #$30
        .a16
        .i16
        DMAVRAM g_bg1chr, (g_bg1chr_end - g_bg1chr), V_BG1CHR
        DMAVRAM g_levelmap, (g_levelmap_end - g_levelmap), V_BG1MAP
        DMACG g_bg1pal, (g_bg1pal_end - g_bg1pal), 32
        sep #$20
        .a8
        lda #$17                        ; BG1 + BG2 + BG3 + OBJ
        sta TMREG
        lda #$0F
        sta INIDISP
        rep #$30
        .a16
        .i16
        stz a:RELOAD
        rts
.endproc

; ---------------------------------------------------------------------------
.proc read_pad
        .a16
        .i16
.ifdef GAUTO
        ; A scripted player, because this host cannot press a button and
        ; cannot see the screen either.  Everything the run proves has to be
        ; reproducible from the cartridge alone.
        lda #$0000
        sta a:TMPW
        lda a:GSTATE
        cmp #ST_PLAY
        bne :+
        lda #PAD_RIGHT
        sta a:TMPW
        lda a:FRAMEC
        and #$001F
        cmp #$0004
        bcs :+
        lda a:TMPW
        ora #PAD_B
        sta a:TMPW
:       lda a:GSTATE
        cmp #ST_ASK
        bne :++
        lda a:GTIMER
        cmp #20
        bne :+
        lda #PAD_DOWN                   ; move the selection on
        sta a:TMPW
:       lda a:GTIMER
        cmp #40
        bne :+
        lda #PAD_A
        sta a:TMPW
:       lda a:TMPW
.else
:       sep #$20
        .a8
        lda HVBJOY                      ; wait out the auto-read
        and #$01
        bne :-
        rep #$20
        .a16
        lda JOY1
.endif
        sta a:TMPW                      ; cur
        eor a:PADNOW                    ; cur ^ prev
        and a:TMPW                      ; & cur = the bits that went 0 -> 1
        sta a:PADNEW
        lda a:TMPW
        sta a:PADNOW
        rts
.endproc

; ---------------------------------------------------------------------------
; the ring drain: this is where a committed token becomes characters on the
; screen.  It runs in vblank, not in the token loop.
; ---------------------------------------------------------------------------
.proc drain_ring
        .a16
        .i16
        lda a:TRR
        cmp a:TRW
        beq done
loop:   ldx a:TRR
        sep #$20
        .a8
        lda a:TOKRING, x
        rep #$20
        .a16
        and #$00FF
        pha
        lda a:TRR
        inc a
        and #$003F
        sta a:TRR
        pla
        jsr print_token
        lda a:TRR
        cmp a:TRW
        bne loop
        lda #$0001
        sta a:BG3DRT
done:   rts
.endproc

; A = token id, bit 7 set when the token was the QUESTION rather than the
; answer.  The two are drawn in different palettes, so the screen itself shows
; which characters the console generated and which were typed in.
.proc print_token
        .a16
        .i16
        sta a:TMPW2
        lda a:GSTATE
        cmp #ST_LINE
        bcc skip                        ; nothing on screen to print into yet
        lda a:TMPW2
        and #$0080
        beq gen
        lda #((2 << 10) | (1 << 13))    ; question: amber on the box
        bra havepal
gen:    lda #((1 << 10) | (1 << 13))    ; generated: white on the box
havepal:
        sta a:TPAL
        lda #FONT_PAGE1
        sta a:TPAGE
        lda a:TMPW2
        and #$003F
        sta a:TMPW
        asl a
        clc
        adc a:TMPW
        asl a                           ; token * 6, the vocab row stride
        tax
        sep #$20
        .a8
        lda f:GDBASE + g_vocab, x
        rep #$20
        .a16
        and #$00FF
        sta a:TMPW
        beq skip
cloop:  inx
        phx
        sep #$20
        .a8
        lda f:GDBASE + g_vocab, x
        rep #$20
        .a16
        and #$00FF
        jsr text_char
        plx
        lda a:TMPW
        dec a
        sta a:TMPW
        bne cloop
skip:   rts
.endproc

; ---- the transcript: three lines inside the box, cleared when it fills -----
TX_L    = 2
TX_R    = 30
TX_T    = 21
TX_B    = 23

.proc text_char
        .a16
        .i16
        pha
        lda a:TCOL
        cmp #TX_R
        bcc place
        lda #TX_L
        sta a:TCOL
        lda a:TROW
        inc a
        cmp #(TX_B + 1)
        bcc setrow
        jsr clear_transcript
        bra place
setrow: sta a:TROW
place:  pla
        jsr gputc
        rts
.endproc

.proc clear_transcript
        .a16
        .i16
        lda #TX_T
        sta a:TROW
        lda #TX_L
        sta a:TCOL
        ldy #TX_T
rloop:  ldx #TX_L
cloop:  phx
        phy
        tya
        jsr rowbase                     ; -> A = word offset of column 0
        sta a:TMPW
        ply
        plx
        txa
        asl a
        clc
        adc a:TMPW
        phx
        tax
        lda #(BOXT_FILL | (1 << 10) | (1 << 13))
        sta a:BG3SH, x
        plx
        inx
        cpx #TX_R
        bne cloop
        iny
        cpy #(TX_B + 1)
        bne rloop
        lda #$0001
        sta a:BG3DRT
        rts
.endproc

; A = row -> A = byte offset of (row, 0) in the 32-wide tilemap shadow
.proc rowbase
        .a16
        .i16
        .repeat 6
        asl a
        .endrepeat
        rts
.endproc

; A = ASCII.  TCOL/TROW/TPAGE/TPAL choose where and in what.
.proc gputc
        .a16
        .i16
        sec
        sbc #$0020
        clc
        adc a:TPAGE
        ora a:TPAL
        sta a:GP2
        lda a:TROW
        jsr rowbase
        sta a:GP1
        lda a:TCOL
        asl a
        clc
        adc a:GP1
        tax
        lda a:GP2
        sta a:BG3SH, x
        lda a:TCOL
        inc a
        sta a:TCOL
        rts
.endproc

; X = address of a length-prefixed string in the game data bank
.proc gputs
        .a16
        .i16
        sep #$20
        .a8
        lda f:GDBASE, x
        rep #$20
        .a16
        and #$00FF
        sta a:TMPW2
        beq done
        sta a:SLEN
sloop:  inx
        phx
        sep #$20
        .a8
        lda f:GDBASE, x
        rep #$20
        .a16
        and #$00FF
        jsr gputc
        plx
        lda a:SLEN
        dec a
        sta a:SLEN
        bne sloop
done:   rts
.endproc

; ---------------------------------------------------------------------------
; the HUD: '@ x NNN'.  Redrawn only when the number changes, so a frame that
; generated nothing costs no VRAM traffic at all.
; ---------------------------------------------------------------------------
.proc hud
        .a16
        .i16
        lda a:TOKCNT
        cmp a:HUDN
        beq done
        sta a:HUDN
        lda a:TCOL
        pha
        lda a:TROW
        pha
        lda a:TPAGE
        pha
        lda a:TPAL
        pha
        lda #$0001
        sta a:TCOL
        sta a:TROW
        lda #FONT_PAGE1                 ; opaque paper: the counter has to stay
        sta a:TPAGE                     ; legible over a white cloud
        lda #((1 << 10) | (1 << 13))
        sta a:TPAL
        lda #'@'
        jsr gputc
        lda #' '
        jsr gputc
        lda #'x'
        jsr gputc
        lda #' '
        jsr gputc
        lda a:HUDN
        jsr put3
        pla
        sta a:TPAL
        pla
        sta a:TPAGE
        pla
        sta a:TROW
        pla
        sta a:TCOL
        lda #$0001
        sta a:BG3DRT
done:   rts
.endproc

; A = 0..999 -> three digits.  Repeated subtraction: the value is small, this
; runs once per token at most, and it needs no division table.
.proc put3
        .a16
        .i16
        sta a:TMPW
        ldy #$0000
:       lda a:TMPW
        cmp #100
        bcc :+
        sec
        sbc #100
        sta a:TMPW
        iny
        bra :-
:       tya
        clc
        adc #'0'
        jsr gputc
        ldy #$0000
:       lda a:TMPW
        cmp #10
        bcc :+
        sec
        sbc #10
        sta a:TMPW
        iny
        bra :-
:       tya
        clc
        adc #'0'
        jsr gputc
        lda a:TMPW
        clc
        adc #'0'
        jsr gputc
        rts
.endproc

; ---------------------------------------------------------------------------
; the dialogue box: rows 20..27, the bottom eight of the screen
; ---------------------------------------------------------------------------
.proc draw_box
        .a16
        .i16
        ldy #20
rloop:  ldx #$0000
cloop:  phx
        phy
        tya
        jsr rowbase
        sta a:TMPW
        ply
        plx
        txa
        asl a
        clc
        adc a:TMPW
        phx
        tax
        cpy #20
        beq top
        lda #(BOXT_FILL | (1 << 10) | (1 << 13))
        bra put
top:    lda #(BOXT_TOP | (1 << 10) | (1 << 13))
put:    sta a:BG3SH, x
        plx
        inx
        cpx #32
        bne cloop
        iny
        cpy #28
        bne rloop
        lda #$0001
        sta a:BG3DRT
        rts
.endproc

; the question line and the controls line
.proc draw_menu
        .a16
        .i16
        lda #(BOXT_FILL | (1 << 10) | (1 << 13))
        ldy #25
        jsr blank_row
        lda #(BOXT_FILL | (1 << 10) | (1 << 13))
        ldy #26
        jsr blank_row
        lda #25
        sta a:TROW
        lda #$0002
        sta a:TCOL
        lda #FONT_PAGE1
        sta a:TPAGE
        lda #((2 << 10) | (1 << 13))
        sta a:TPAL
        ldx #.loword(s_ask)
        jsr gputs
        ; the selected question, spelled out of its own tokens -- the same
        ; bytes that will be fed to the model, so the menu cannot say one
        ; thing and the prompt be another
        lda a:MENUI
        sta a:GQSEL
        stz a:TMPW2
qloop:  lda a:TMPW2
        sta a:PROMPTI
        jsr prompt_token
        jsr print_vocab
        lda a:TMPW2
        inc a
        sta a:TMPW2
        ldx a:GQSEL
        sep #$20
        .a8
        lda f:GDBASE + g_qlen, x
        rep #$20
        .a16
        and #$00FF
        cmp a:TMPW2
        beq qdone
        bcs qloop
qdone:
        lda #26
        sta a:TROW
        lda #$0002
        sta a:TCOL
        lda #((1 << 10) | (1 << 13))
        sta a:TPAL
        ldx #.loword(s_help)
        jsr gputs
        lda #$0001
        sta a:BG3DRT
        rts
.endproc

; A = fill word, Y = row
.proc blank_row
        .a16
        .i16
        sta a:TMPW2
        tya
        jsr rowbase
        tax
        ldy #$0000
:       lda a:TMPW2
        sta a:BG3SH, x
        inx
        inx
        iny
        cpy #32
        bne :-
        rts
.endproc

; A = token id -> its characters, at the current cursor and palette
.proc print_vocab
        .a16
        .i16
        and #$003F
        sta a:TMPW
        asl a
        clc
        adc a:TMPW
        asl a
        tax
        sep #$20
        .a8
        lda f:GDBASE + g_vocab, x
        rep #$20
        .a16
        and #$00FF
        sta a:SLEN
        beq done
:       inx
        phx
        sep #$20
        .a8
        lda f:GDBASE + g_vocab, x
        rep #$20
        .a16
        and #$00FF
        jsr gputc
        plx
        lda a:SLEN
        dec a
        sta a:SLEN
        bne :-
done:   rts
.endproc

; ===========================================================================
; the state machine -- the three acts
; ===========================================================================
.proc state_update
        .a16
        .i16
.ifdef GFRAMES
        lda a:FRAMEC
        cmp #GFRAMES
        bcc :+
        lda a:GSTATE
        cmp #ST_END
        beq :+
        lda #ST_END
        sta a:GSTATE
        stz a:GTIMER
:
.endif
        lda a:GTIMER
        inc a
        sta a:GTIMER
        lda a:GSTATE
        asl a
        tax
        jmp (jtab, x)
jtab:   .addr st_logo, st_play, st_stop, st_line, st_ask, st_answer, st_end
.endproc

; ---- act 0: the lab -------------------------------------------------------
.proc st_logo
        .a16
        .i16
        lda a:GTIMER
        cmp #LOGO_FRAMES
        bcs go
        lda a:PADNEW
        and #PAD_START
        beq done
go:     lda #$0001
        sta a:RELOAD                    ; swap the logo out for the level
        jsr init_play
        lda #ST_PLAY
        sta a:GSTATE
        stz a:GTIMER
        lda #GRQ_FREE
        sta a:GREQ                      ; the main thread starts generating
done:   rts
.endproc

.proc init_play
        .a16
        .i16
        lda #(32 << 4)
        sta a:PX
        lda #(160 << 4)
        sta a:PY
        stz a:VXX
        stz a:VYY
        lda #$0001
        sta a:ONGND
        stz a:FACING
        stz a:ANIMF
        stz a:ANIMT
        stz a:CAMX
        stz a:CAMY
        stz a:NX
        lda #12
        sta a:NVX
        lda #176
        sta a:NY
        stz a:BLKT
        rts
.endproc

; ---- act 1: the platformer -----------------------------------------------
.proc st_play
        .a16
        .i16
        jsr do_physics
        jsr move_nabla
        jsr hud
        lda a:TOKCNT
        cmp #ACT1_TOKENS
        bcc done
        lda #ST_STOP                    ; she has said enough with coins
        sta a:GSTATE
        stz a:GTIMER
        lda #.loword(-20)
        sta a:NVX                       ; the nabla loses interest
done:   rts
.endproc

; ---- act 2: the stop ------------------------------------------------------
; She walks to a mark and halts.  The nabla wanders off.  Nothing is scripted
; about the model: it keeps generating the whole time, and the coins keep
; coming out of the block, because that is what the binding rule means.
.proc st_stop
        .a16
        .i16
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        sta a:TMPW
        cmp #STOP_X
        bcs left
        lda #RUNSPD
        sta a:VXX
        stz a:FACING
        bra phys
left:   lda a:TMPW
        cmp #(STOP_X + 1)
        bcc halt
        lda #.loword(-RUNSPD)
        sta a:VXX
        lda #$0001
        sta a:FACING
        bra phys
halt:   stz a:VXX
        stz a:FACING
phys:   lda a:CAMY                      ; tilt up over the stop, so the box
        cmp #48                         ; does not land on top of her
        bcs :+
        inc a
        sta a:CAMY
:       jsr do_physics_noinput
        jsr move_nabla
        jsr hud
        lda a:GTIMER
        cmp #STOP_FRAMES
        bcc done
        jsr draw_box
        jsr clear_transcript
        lda #ST_LINE
        sta a:GSTATE
        stz a:GTIMER
        lda #$0001
        sta a:GBUSY
        lda #GRQ_LINE
        sta a:GREQ
done:   rts
.endproc

; ---- act 2b: she says it --------------------------------------------------
.proc st_line
        .a16
        .i16
        stz a:VXX
        jsr do_physics_noinput
        jsr hud
        lda a:GBUSY
        bne done                        ; still generating
        jsr draw_menu
        lda #ST_ASK
        sta a:GSTATE
        stz a:GTIMER
done:   rts
.endproc

; ---- act 3: the conversation ---------------------------------------------
.proc st_ask
        .a16
        .i16
        stz a:VXX
        jsr do_physics_noinput
        jsr hud
        lda a:PADNEW
        and #PAD_DOWN
        beq chkup
        lda a:MENUI
        inc a
        cmp #NQUEST
        bcc setsel
        lda #$0000
        bra setsel
chkup:  lda a:PADNEW
        and #PAD_UP
        beq chka
        lda a:MENUI
        dec a
        bpl setsel
        lda #(NQUEST - 1)
setsel: sta a:MENUI
        jsr draw_menu
chka:   lda a:PADNEW
        and #PAD_A
        beq done
        lda a:MENUI
        sta a:GQSEL
        jsr clear_transcript
        lda #ST_ANSWER
        sta a:GSTATE
        stz a:GTIMER
        lda #$0001
        sta a:GBUSY
        lda #GRQ_ASK
        sta a:GREQ
done:   rts
.endproc

.proc st_answer
        .a16
        .i16
        stz a:VXX
        jsr do_physics_noinput
        jsr hud
        lda a:GBUSY
        bne done
        lda a:ANSN
        clc
        adc #22
        sta a:ANSN
.ifdef GRUN
        cmp #(22 * GRUN)
        bcc :+
        lda #ST_END
        sta a:GSTATE
        stz a:GTIMER
        rts
:
.endif
        lda #ST_ASK
        sta a:GSTATE
        stz a:GTIMER
        jsr draw_menu
done:   rts
.endproc

; ---- the end of a VERIFICATION run: freeze, read the PPU back, write DONE --
.proc st_end
        .a16
        .i16
        lda a:GDONE
        bne done
        lda a:GTIMER
        cmp #$0008                      ; a few frames so the last DMA landed
        bcc done
        jsr dump_all
        lda #$0001
        sta a:GDONE
        rts
done:   lda a:FRAMEC
        and #$000F
        bne :+
        lda a:FRAMEC
        sta f:SRAM + SR_STAT + 24       ; heartbeat: keeps the save RAM dirty
:       rts
.endproc

; ===========================================================================
; physics.  12.4 fixed point everywhere; solidity comes out of level.solid,
; which tools/mkbg.py emitted from the same array it drew the tilemap from.
; ===========================================================================
.proc do_physics
        .a16
        .i16
        stz a:VXX
        lda a:PADNOW
        and #PAD_LEFT
        beq :+
        lda #.loword(-RUNSPD)
        sta a:VXX
        lda #$0001
        sta a:FACING
:       lda a:PADNOW
        and #PAD_RIGHT
        beq :+
        lda #RUNSPD
        sta a:VXX
        stz a:FACING
:       lda a:PADNEW
        and #(PAD_A | PAD_B)
        beq :+
        lda a:ONGND
        beq :+
        lda #JUMPV
        sta a:VYY
        stz a:ONGND
:       ; fall through
.endproc
.proc do_physics_noinput
        .a16
        .i16
        stz a:STRIKE
        ; ---- gravity ------------------------------------------------------
        lda a:VYY
        clc
        adc #GRAV
        cmp #MAXFALL
        bcc :+
        bit #$8000
        bne :+
        lda #MAXFALL
:       sta a:VYY

        ; ---- vertical -----------------------------------------------------
        lda a:PY
        sta a:TMPW3
        clc
        adc a:VYY
        sta a:PY
        jsr hit_test
        beq noclip
        lda a:VYY
        bmi rising
        ; falling: put her feet on top of the row she entered
        lda a:PY
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #(HB_T + HB_H - 1)          ; the bottom pixel of the hitbox
        lsr a
        lsr a
        lsr a                           ; its tile row
        .repeat 3
        asl a
        .endrepeat                      ; the top pixel of that row
        sec
        sbc #(HB_T + HB_H)
        .repeat 4
        asl a
        .endrepeat
        sta a:PY
        stz a:VYY
        bra noclip
rising: lda a:TMPW3                     ; a ceiling: just stop going up
        sta a:PY
        stz a:VYY
noclip:
        jsr ground_probe
        ; ---- horizontal ---------------------------------------------------
        lda a:PX
        sta a:TMPW3                     ; remember, to revert on a hit
        clc
        adc a:VXX
        bpl :+
        lda #$0000
:       cmp #((LEVEL_W * 8 - 32) << 4)
        bcc :+
        lda #((LEVEL_W * 8 - 32) << 4)
:       sta a:PX
        jsr hit_test
        beq :+
        lda a:TMPW3
        sta a:PX
        stz a:VXX
:
        ; ---- did she just fall off the world? -----------------------------
        lda a:PY
        cmp #((LEVEL_H * 8) << 4)
        bcc :+
        lda #(32 << 4)
        sta a:PX
        lda #(160 << 4)
        sta a:PY
        stz a:VYY
:
        jsr block_test
        jsr set_anim
        jsr set_camera
        rts
.endproc

; 1 if any of six sample points inside her hitbox is on a solid tile.  Six
; rather than four because the hitbox is 28 tall and a tile is 8: four corners
; would step straight through a one-tile ledge at running speed.
.proc hit_test
        .a16
        .i16
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #HB_L
        sta a:TMPW                      ; left
        lda a:PY
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #HB_T
        sta a:TMPW2                     ; top

        stz a:SCR2
sloop:  ldx a:SCR2
        lda a:TMPW
        clc
        adc f:GDBASE + sample_dx, x
        sta a:OSX                       ; scratch: this sample's world x
        ldx a:SCR2
        lda a:TMPW2
        clc
        adc f:GDBASE + sample_dy, x
        sta a:OSY
        jsr solid_at
        cmp #$0000
        bne hit
        lda a:SCR2
        inc a
        inc a
        sta a:SCR2
        cmp #12
        bne sloop
        lda #$0000
        rts
hit:    lda #$0001
        rts
.endproc

; OSX / OSY in world pixels -> A = 1 if that pixel is solid
.proc solid_at
        .a16
        .i16
        lda a:OSY
        bmi outside
        cmp #(LEVEL_H * 8)
        bcs outside
        lsr a
        lsr a
        lsr a
        .repeat 6
        asl a
        .endrepeat                      ; row * 64
        sta a:SCR1
        lda a:OSX
        bmi outside
        cmp #(LEVEL_W * 8)
        bcs outside
        lsr a
        lsr a
        lsr a
        clc
        adc a:SCR1
        tax
        sep #$20
        .a8
        lda f:GDBASE + g_solid, x
        rep #$20
        .a16
        and #$00FF
        rts
outside:
        lda #$0000
        rts
.endproc

; the @ block is an object, not a tile, so it gets its own test.  Striking it
; only ever animates -- it never creates a coin.  Coins come from tokens.
.proc block_test
        .a16
        .i16
        lda a:VYY
        bpl done                        ; only on the way up
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #HB_L
        sta a:TMPW                      ; her left
        cmp #(BLK_X + 16)
        bcs done
        clc
        adc #HB_W
        cmp #(BLK_X + 1)
        bcc done
        lda a:PY
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #HB_T
        cmp #(BLK_Y + 16)
        bcs done
        clc
        adc #$0008
        cmp #(BLK_Y + 12)
        bcc done
        lda #$0008
        sta a:BLKT                      ; the bump
        stz a:VYY
        lda #$0001
        sta a:STRIKE
done:   rts
.endproc

; Is there solid ground within one pixel of her feet?  This is the ONLY writer
; of ONGND.
.proc ground_probe
        .a16
        .i16
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #HB_L
        sta a:TMPW
        lda a:PY
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #(HB_T + HB_H)              ; the pixel below her feet
        sta a:TMPW2
        sta a:OSY
        lda a:TMPW
        sta a:OSX
        jsr solid_at
        cmp #$0000
        bne yes
        lda a:TMPW
        clc
        adc #(HB_W - 1)
        sta a:OSX
        lda a:TMPW2
        sta a:OSY
        jsr solid_at
        cmp #$0000
        bne yes
        stz a:ONGND
        rts
yes:    lda #$0001
        sta a:ONGND
        rts
.endproc

.proc set_anim
        .a16
        .i16
        lda a:ANIMT
        inc a
        sta a:ANIMT
        lda a:ONGND
        beq air
        lda a:VXX
        beq idle
        lda a:ANIMT
        and #$0004
        lsr a
        lsr a
        sta a:ANIMF                     ; run, two frames, every 4 frames
        rts
air:    lda #$0002
        sta a:ANIMF                     ; jump
        rts
idle:   lda a:ANIMT
        lsr a
        lsr a
        lsr a
        and #$0003                      ; a four-beat tap out of three frames
        tax
        lda f:GDBASE + tapcycle, x
        and #$00FF
        clc
        adc #$0003
        sta a:ANIMF
        rts
.endproc

.proc set_camera
        .a16
        .i16
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc #16
        sec
        sbc #128
        bpl :+
        lda #$0000
:       cmp #((LEVEL_W * 8) - 256 + 1)
        bcc :+
        lda #((LEVEL_W * 8) - 256)
:       sta a:CAMX
        rts
.endproc

.proc move_nabla
        .a16
        .i16
        lda a:GSTATE
        cmp #ST_PLAY
        bne drift
        lda a:PX                        ; chase her, slower than she runs
        cmp a:NX
        bcc goleft
        lda #12
        bra move
goleft: lda #.loword(-12)
        bra move
drift:  lda a:NVX
move:   clc
        adc a:NX
        sta a:NX
        lda a:NANIM
        inc a
        sta a:NANIM
        rts
.endproc

; ===========================================================================
; coins.  spawn_coins is the ONLY reader of COINQ and the only code in this
; cartridge that ever fills a coin slot.
; ===========================================================================
.proc spawn_coins
        .a16
        .i16
        ldy #$0002                      ; at most two a frame: at 7 tokens a
try:    lda a:COINQ                     ; second the queue never builds up, and
        beq done                        ; a burst still reads as separate coins
        ldx #$0000
find:   lda a:COINT, x
        beq got
        inx
        inx
        cpx #(NCOIN * 2)
        bne find
        rts                             ; no free slot: it STAYS queued
got:    lda #((BLK_X + 0) << 4)
        sta a:COINX, x
        lda #((BLK_Y - 14) << 4)
        sta a:COINY, x
        lda #.loword(-72)
        sta a:COINV, x
        lda #48
        sta a:COINT, x
        stz a:COINF, x
        lda a:COINQ                     ; <-- the other of exactly two sites
        dec a
        sta a:COINQ
        lda a:COINSP
        inc a
        sta a:COINSP
        lda #$0006
        sta a:BLKT
        dey
        bne try
done:   rts
.endproc

.proc move_coins
        .a16
        .i16
        ldx #$0000
loop:   lda a:COINT, x
        beq next
        dec a
        sta a:COINT, x
        lda a:COINY, x
        clc
        adc a:COINV, x
        sta a:COINY, x
        lda a:COINV, x
        clc
        adc #(GRAV + 2)
        sta a:COINV, x
        lda a:COINT, x
        lsr a
        lsr a
        lsr a
        and #$0003
        sta a:COINF, x
next:   inx
        inx
        cpx #(NCOIN * 2)
        bne loop
        lda a:BLKT
        beq :+
        dec a
        sta a:BLKT
:       rts
.endproc

; ===========================================================================
; OAM.  Twelve slots are written every frame in a fixed order; sprites 12..127
; were parked off the bottom of the screen at setup and never move again.
; ===========================================================================
.proc build_oam
        .a16
        .i16
        stz a:HIACC

        ; ---- 0: Elya ------------------------------------------------------
        stz a:OSLOT
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        sec
        sbc a:CAMX
        sta a:OSX
        lda a:PY
        lsr a
        lsr a
        lsr a
        lsr a
        sec
        sbc a:CAMY
        sta a:OSY
        ldx a:ANIMF
        lda f:GDBASE + elya_tiles, x
        and #$00FF
        sta a:OST
        lda #$0030                      ; palette 0, priority 3
        ldx a:FACING
        beq :+
        ora #$0040                      ; H flip: she is drawn facing right
:       sta a:OSA
        lda #$0002                      ; large: 32x32
        sta a:OSZ
        jsr oam_put

        ; ---- 1: the @ block -----------------------------------------------
        lda #$0001
        sta a:OSLOT
        lda #BLK_X
        sec
        sbc a:CAMX
        sta a:OSX
        lda a:BLKT
        beq :+
        lda #(BLK_Y + 2)
        sec
        sbc a:CAMY
        sta a:OSY
        lda #OBJT_BLK_HIT
        bra :++
:       lda #BLK_Y
        sec
        sbc a:CAMY
        sta a:OSY
        lda #OBJT_BLK_REST
:       sta a:OST
        lda #$0030
        sta a:OSA
        stz a:OSZ
        jsr oam_put

        ; ---- 2: the nabla -------------------------------------------------
        lda #$0002
        sta a:OSLOT
        lda a:NX
        lsr a
        lsr a
        lsr a
        lsr a
        sec
        sbc a:CAMX
        sta a:OSX
        lda a:NY
        sec
        sbc a:CAMY
        sta a:OSY
        lda a:NANIM
        and #$0010
        beq :+
        lda #OBJT_NABLA1
        bra :++
:       lda #OBJT_NABLA0
:       sta a:OST
        lda #$0030
        sta a:OSA
        stz a:OSZ
        jsr oam_put

        ; ---- 3..10: the coins ---------------------------------------------
        ldy #$0000
cloop:  tya
        clc
        adc #$0003
        sta a:OSLOT
        tya
        asl a
        tax
        lda a:COINT, x
        bne live
        lda #$00F0                      ; parked
        sta a:OSY
        stz a:OSX
        lda #OBJT_COIN0
        bra place
live:   lda a:COINX, x
        lsr a
        lsr a
        lsr a
        lsr a
        sec
        sbc a:CAMX
        sta a:OSX
        lda a:COINY, x
        lsr a
        lsr a
        lsr a
        lsr a
        sec
        sbc a:CAMY
        sta a:OSY
        lda a:COINF, x
        tax
        lda f:GDBASE + coin_tiles, x
        and #$00FF
place:  sta a:OST
        lda #$0030
        sta a:OSA
        stz a:OSZ
        phy
        jsr oam_put
        ply
        iny
        cpy #NCOIN
        bne cloop

        ; ---- 11: the filler that flushes the high table -------------------
        lda #11
        sta a:OSLOT
        stz a:OSX
        lda #$00F0
        sta a:OSY
        stz a:OST
        lda #$0030
        sta a:OSA
        stz a:OSZ
        jsr oam_put
        rts
.endproc

.proc oam_put
        .a16
        .i16
        lda a:OSLOT
        asl a
        asl a
        tax
        sep #$20
        .a8
        lda a:OSX
        sta a:OAMBUF, x
        lda a:OSY
        sta a:OAMBUF + 1, x
        lda a:OST
        sta a:OAMBUF + 2, x
        lda a:OSA
        sta a:OAMBUF + 3, x
        rep #$20
        .a16
        ; the high table is two bits a sprite: X bit 8, and the size flag
        lda a:OSX
        and #$0100
        beq :+
        lda #$0001
        bra :++
:       lda #$0000
:       ora a:OSZ
        sta a:TMPW2
        lda a:OSLOT
        and #$0003
        beq shdone
        tay
shl:    asl a:TMPW2
        asl a:TMPW2
        dey
        bne shl
shdone: lda a:HIACC
        ora a:TMPW2
        sta a:HIACC
        lda a:OSLOT
        and #$0003
        cmp #$0003
        bne done
        lda a:OSLOT
        lsr a
        lsr a
        tax
        sep #$20
        .a8
        lda a:HIACC
        sta a:OAMHI, x
        rep #$20
        .a16
        stz a:HIACC
done:   rts
.endproc

; ===========================================================================
; the readout.  Screen capture is not available on this host, so what the
; console actually renders is proved by reading OAM, VRAM and CGRAM back OUT
; THROUGH THE PPU into battery SRAM and rebuilding the frame on the host --
; tools/render_frame.py.  That is stronger than a screenshot anyway: a
; screenshot shows a picture, this shows the registers the picture came from.
; ===========================================================================
.proc trace
        .a16
        .i16
        lda a:FRAMEC
        and #$000F
        bne snapcheck
        lda a:TRACEP
        cmp #$0D00
        bcs snapcheck
        tax
        lda a:FRAMEC
        sta f:SRAM + SR_TRACE + 0, x
        lda a:GSTATE
        sta f:SRAM + SR_TRACE + 2, x
        lda a:PX
        lsr a
        lsr a
        lsr a
        lsr a
        sta f:SRAM + SR_TRACE + 4, x
        lda a:PY
        lsr a
        lsr a
        lsr a
        lsr a
        sta f:SRAM + SR_TRACE + 6, x
        lda a:COINQ
        sta f:SRAM + SR_TRACE + 8, x
        lda a:TOKCNT
        sta f:SRAM + SR_TRACE + 10, x
        lda a:COINSP
        sta f:SRAM + SR_TRACE + 12, x
        phx
        jsr count_coins
        plx
        sta f:SRAM + SR_TRACE + 14, x
        txa
        clc
        adc #16
        sta a:TRACEP
snapcheck:
        lda a:GSTATE
        cmp #ST_PLAY
        bne done
        lda a:TOKCNT
        cmp #40                         ; mid-act-1, with coins in the air
        bne done
        lda a:FRAMEC
        and #$0003
        bne done
        jsr snap1
done:   rts
.endproc

.proc count_coins
        .a16
        .i16
        ldx #$0000
        ldy #$0000
:       lda a:COINT, x
        beq :+
        iny
:       inx
        inx
        cpx #(NCOIN * 2)
        bne :--
        tya
        rts
.endproc

; The frame snapshot: everything the host renderer needs that lives in WRAM.
; It is a MACRO with a compile-time destination rather than a routine taking an
; offset, because the 65816's long-indexed store has only one index register
; and copying WRAM -> SRAM at a runtime offset would need both.
.macro SNAPSHOT dst
        .local l1, l2
        ldx #$0000
l1:     lda a:OAMBUF, x
        sta f:SRAM + dst, x
        inx
        inx
        cpx #$0220
        bne l1
        ldx #$0000
l2:     lda a:BG3SH, x
        sta f:SRAM + dst + $0220, x
        inx
        inx
        cpx #$0800
        bne l2
        lda a:CAMX
        sta f:SRAM + dst + $0A20
        lda a:GSTATE
        sta f:SRAM + dst + $0A22
        lda a:FRAMEC
        sta f:SRAM + dst + $0A24
        lda a:TOKCNT
        sta f:SRAM + dst + $0A26
        lda a:COINSP
        sta f:SRAM + dst + $0A28
        lda #$5A5A
        sta f:SRAM + dst + $0A2A
        lda a:CAMY
        sta f:SRAM + dst + $0A2C
.endmacro

.proc snap1
        .a16
        .i16
        SNAPSHOT SR_SNAP1
        rts
.endproc

.proc snap2
        .a16
        .i16
        SNAPSHOT SR_SNAP2
        rts
.endproc

.macro DMARK n
        sep #$20
        .a8
        lda #n
        sta f:SRAM + $000C
        rep #$20
        .a16
.endmacro

.proc dump_all
        .a16
        .i16
        DMARK 1
        sep #$20
        .a8
        ; HDMA KEEPS RUNNING IN FORCED BLANK.  The sky channel writes CGADD=0
        ; and a colour every eight scanlines, and the CGRAM readback below
        ; spans a third of a frame -- so the read address was being reset out
        ; from under the loop about every thirty entries, and the dump came
        ; back as the palettes interleaved with each other.  It read exactly
        ; like a broken read port.
        stz HDMAEN
        lda #$8F
        sta INIDISP                     ; the PPU only reads back reliably in
        rep #$30                        ; blank, and the run is over anyway
        .a16
        .i16

        ; ---- OAM, through $2138 -------------------------------------------
        sep #$20
        .a8
        stz OAMADDL
        stz OAMADDL + 1
        rep #$30
        .a16
        .i16
        ldx #$0000
:       sep #$20
        .a8
        lda RDOAM
        sta f:SRAM + SR_OAM, x
        rep #$30
        .a16
        .i16
        inx
        cpx #$0220
        bne :-

        DMARK 2
        ; ---- the BG3 tilemap, through $2139/$213A -------------------------
        sep #$20
        .a8
        lda #$80                        ; increment after the high byte
        sta VMAIN
        rep #$20
        .a16
        ; The VRAM read port hands back the word BEFORE the one the address
        ; points at: read k returns VRAM[addr + k - 1].  Measured by an A/B in
        ; one run -- reasoning about the prefetch produced three mutually
        ; contradictory models, and the first attempted fix changed the base
        ; address AND added a dummy read, which cancelled exactly and looked
        ; like the dummy doing nothing.  One dummy pair, address unchanged.
        lda #V_BG3MAP
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
        sta f:SRAM + SR_VM3, x
        lda RDVRAML + 1
        sta f:SRAM + SR_VM3 + 1, x
        rep #$30
        .a16
        .i16
        inx
        inx
        cpx #$0800
        bne :-

        DMARK 3
        ; ---- CGRAM, through $213B -----------------------------------------
        ; Same lag as the VRAM port, established by an A/B in one run: a
        ; readback started at colour 0 put every palette one entry late, a
        ; second readback started at colour 120 -- with the port already
        ; streaming -- put them exactly where they were DMA'd to.  So the
        ; palettes were always right and the first read of a freshly addressed
        ; port hands back one stale pair.  Throw it away.
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
        sta f:SRAM + SR_CGR, x
        rep #$30
        .a16
        .i16
        inx
        cpx #$0200
        bne :-

        ; ...and again, immediately.  One run of the real-controller arm came
        ; back with a single palette entry wrong out of 256 while every other
        ; run was exact, so the question is whether CGRAM is corrupt or the
        ; read of it is.  Two back-to-back reads answer that in one run.
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
        sta f:SRAM + SR_CGR2, x
        rep #$30
        .a16
        .i16
        inx
        cpx #$0200
        bne :-

        DMARK 4
        jsr snap2
        DMARK 5

        ; ---- the counters the binding proof reads -------------------------
        lda a:TOKCNT
        sta f:SRAM + SR_STAT + 0
        lda a:COINSP
        sta f:SRAM + SR_STAT + 2
        lda a:COINQ
        sta f:SRAM + SR_STAT + 4
        lda a:FRAMEC
        sta f:SRAM + SR_STAT + 6
        lda a:ANSN
        sta f:SRAM + SR_STAT + 8
        lda a:LINEN
        sta f:SRAM + SR_STAT + 10
        lda a:TRACEP
        sta f:SRAM + SR_STAT + 12
        lda a:TOKN
        sta f:SRAM + SR_STAT + 14
        lda #ACT1_TOKENS
        sta f:SRAM + SR_STAT + 16
        lda #NQUEST
        sta f:SRAM + SR_STAT + 18
        lda a:CAMX
        sta f:SRAM + SR_STAT + 20
        lda #$C0DE
        sta f:SRAM + SR_STAT + 22

        sep #$20
        .a8
        lda #'D'
        sta f:SRAM + 8
        lda #'O'
        sta f:SRAM + 9
        lda #'N'
        sta f:SRAM + 10
        lda #'E'
        sta f:SRAM + 11
        lda #6
        sta f:SRAM + $000C
        rep #$30
        .a16
        .i16
        rts
.endproc

; ===========================================================================
        .segment "GAMEDATA"

elya_tiles:
        .byte OBJT_ELYA_RUN0, OBJT_ELYA_RUN1, OBJT_ELYA_JUMP
        .byte OBJT_ELYA_IDLE0, OBJT_ELYA_IDLE1, OBJT_ELYA_IDLE2
coin_tiles:
        .byte OBJT_COIN0, OBJT_COIN1, OBJT_COIN2, OBJT_COIN3
tapcycle:
        .byte 0, 1, 2, 1                ; four beats out of three frames
; the six points hit_test samples: her hitbox is 12x28 and a tile is 8, so
; four corners would step through a one-tile ledge at running speed
sample_dx:
        .word 0, HB_W - 1, 0, HB_W - 1, 0, HB_W - 1
sample_dy:
        .word 0, 0, HB_H / 2, HB_H / 2, HB_H - 1, HB_H - 1

s_ask:  .byte 5, "ask: "
s_help: .byte 26, "up/down choose    a  ask  "

g_vocab:    .incbin "out/game/vocab.tbl"
g_qtok:     .incbin "out/game/qtok.bin"
g_qoff:     .incbin "out/game/qoff.bin"
g_qlen:     .incbin "out/game/qlen.bin"
g_solid:    .incbin "assets/level.solid"
g_sky:      .incbin "assets/sky.hdma"
g_bg3pal:   .incbin "assets/bg3.pal"
g_bg3pal_end:
g_bg1pal:   .incbin "assets/bg1.pal"
g_bg1pal_end:
g_bg2pal:   .incbin "assets/bg2.pal"
g_bg2pal_end:
g_objpal:   .incbin "assets/obj.pal"
g_objpal_end:
g_logopal:  .incbin "assets/logo.pal"
g_logopal_end:
g_bg1chr:   .incbin "assets/bg1.chr"
g_bg1chr_end:
g_bg2chr:   .incbin "assets/bg2.chr"
g_bg2chr_end:
g_fontchr:  .incbin "assets/font.chr"
g_fontchr_end:
g_objchr:   .incbin "assets/obj.chr"
g_objchr_end:
g_logochr:  .incbin "assets/logo.chr"
g_logochr_end:
g_cloudmap: .incbin "assets/clouds.map"
g_cloudmap_end:
g_levelmap: .incbin "assets/level.map"
g_levelmap_end:
g_logomap:  .incbin "assets/logo.map"
g_logomap_end:

        .segment "CODE"
