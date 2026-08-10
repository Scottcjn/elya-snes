# elya-snes — FINDINGS

Journal. Appended to after every discrete result. Newest at the bottom.

The single question this repo answers first: **which arithmetic primitive is
cheapest per multiply-accumulate on a stock SNES, in exact cycles.** No engine
is written until that is measured.

---

## 2026-08-10 — 1. A ROM boots under ares, and results come out through battery SRAM

**Toolchain.** `ca65`/`ld65` V2.18 with `--cpu 65816`. cc65 ships no SNES
linker config, so `rom/lorom32.cfg` is ours: a 32 KiB LoROM image where file
`$0000-$7FBF` maps to `$00:8000-$00:FFBF`, the cartridge header sits at
`$00:FFC0`, and the vectors at `$00:FFE0`. `tools/fixhdr.py` patches the
internal checksum after link.

**The first build was wrong and the ROM would have been silently mismapped.**
The 21-byte title field was written with 20 characters, so every header byte
after it shifted down one — map mode landed at `$FFD4` instead of `$FFD5`.
Caught by dumping `$7FC0` with `xxd`, not by any tool complaining. There is now
an `.assert .strlen(title) = 21` in `rom/snes.inc` so it cannot recur.

**Readout channel: battery SRAM.** The cartridge header declares
`carttype = $02` (ROM+RAM+battery) and `ramsize = $03` (8 KiB), which LoROM maps
at `$70:0000`. On exit **ares writes that RAM verbatim to `<romname>.ram` in the
same directory as the ROM.** That is the readout: the ROM stores numbers to
`$70:xxxx`, the host reads the file. No debugger, no savestate, no screen
scraping.

Verified:

```
$ xxd -l 16 ~/snesroms/boot.ram
00000000: 534e 4553 5aa5 d006 ffff ffff ffff ffff  SNESZ...........
```

`SNES` + `$5A $A5` is the magic the ROM writes once at startup. `d0 06` is the
live frame counter, little-endian = `$06D0` = 1744 frames = 29.0 s at 60.1 Hz,
which matches how long ares was left running. So the CPU booted, executed, and
kept executing.

**Operating notes for ares (flatpak build 147), the hard way:**

* The flatpak cannot see `/tmp`; ROMs are staged in `$HOME/snesroms`.
* `flatpak run dev.ares.ares --system "Super Famicom" --no-file-prompt rom.sfc`.
* SRAM is flushed on `SIGINT` (`pkill -INT -f "ares --system"`).
* **Screen capture is not available on this host.** `import -window root` gets
  nothing (the window is Wayland-native) and `grim` fails with *"compositor
  doesn't support wlr-screencopy-unstable-v1"* under GNOME. So an on-screen hex
  readout is not a usable channel here, and everything below goes through SRAM.

Status: task 1 done. Next: a cycle instrument.
