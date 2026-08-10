; ---------------------------------------------------------------------------
; snes.inc -- register names and the cartridge header, shared by every ROM here.
; ---------------------------------------------------------------------------

; ---- PPU ------------------------------------------------------------------
INIDISP  = $2100        ; screen on/off + brightness ($8F = forced blank)
BGMODE   = $2105
M7SEL    = $211A
M7A      = $211B        ; Mode 7 matrix A  -- write TWICE (low byte, high byte)
M7B      = $211C        ; Mode 7 matrix B  -- ALSO the 2nd multiply operand
M7C      = $211D
M7D      = $211E
SLHV     = $2137        ; read: latch H/V counters
MPYL     = $2134        ; product bits  0.. 7   (M7A * M7B, signed 16x8 -> 24)
MPYM     = $2135        ; product bits  8..15
MPYH     = $2136        ; product bits 16..23
OPHCT    = $213C        ; horizontal counter latch, low/high (dots, 0..339)
OPVCT    = $213D        ; vertical counter latch, low/high (scanlines)
STAT78   = $213F        ; read: resets the H/V latch read toggle

; ---- CPU ------------------------------------------------------------------
NMITIMEN = $4200
WRIO     = $4201
WRMPYA   = $4202        ; hardware multiply operand A (unsigned 8)
WRMPYB   = $4203        ; operand B; writing this STARTS the 8-cycle multiply
WRDIVL   = $4204
WRDIVH   = $4205
WRDIVB   = $4206
MEMSEL   = $420D        ; bit0 = FastROM enable
RDDIVL   = $4214
RDDIVH   = $4215
RDMPYL   = $4216        ; product low   (8x8 -> 16 unsigned)
RDMPYH   = $4217        ; product high

; ---- where results go -----------------------------------------------------
; LoROM battery SRAM. ares flushes this to a .srm next to the ROM on exit,
; which is how the host reads numbers back out of the console.
SRAM     = $700000

; ---------------------------------------------------------------------------
.macro SNES_HEADER title, mapmode, carttype, romsize, ramsize
    .segment "SNESHDR"
    .assert .strlen(title) = 21, error, "cartridge title must be exactly 21 characters"
    .byte title             ; $FFC0  21 bytes, space padded
    .byte mapmode           ; $FFD5  $20 = LoROM/SlowROM, $30 = LoROM/FastROM
    .byte carttype          ; $FFD6  $02 = ROM + RAM + battery
    .byte romsize           ; $FFD7  log2(KiB): $05 = 32 KiB
    .byte ramsize           ; $FFD8  log2(KiB): $03 = 8 KiB
    .byte $01               ; $FFD9  country: NTSC / US
    .byte $00               ; $FFDA  developer id
    .byte $00               ; $FFDB  version
    .word $FFFF             ; $FFDC  checksum complement (patched by tools/fixhdr.py)
    .word $0000             ; $FFDE  checksum
.endmacro

.macro SNES_VECTORS reset, nmi, irq
    .segment "VECTORS"
    .word $0000, $0000      ; $FFE0 unused
    .word $0000             ; $FFE4 native COP
    .word $0000             ; $FFE6 native BRK
    .word $0000             ; $FFE8 native ABORT
    .word nmi               ; $FFEA native NMI
    .word $0000             ; $FFEC unused
    .word irq               ; $FFEE native IRQ
    .word $0000, $0000      ; $FFF0 unused
    .word $0000             ; $FFF4 emu COP
    .word $0000             ; $FFF6 unused
    .word $0000             ; $FFF8 emu ABORT
    .word nmi               ; $FFFA emu NMI
    .word reset             ; $FFFC emu RESET
    .word irq               ; $FFFE emu IRQ/BRK
.endmacro
