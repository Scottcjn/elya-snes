#!/usr/bin/env python3
"""Check a built image against what a Kaico Super DSP V3.1 flash cartridge can
actually run, from the bytes rather than from intent.

The Kaico is a LoROM/HiROM flash cart with battery-backed save RAM whose only
enhancement chips are DSP-1/1B/2/3/4.  FINDINGS entry 4 measured the DSP-1 out
of this design on bus-transfer cost alone, so the requirement here is that the
image needs NO coprocessor at all, is LoROM, is NTSC, declares battery SRAM,
and is inside the cart's capacity.

Every field is read out of the image; nothing is taken on trust from the
assembler source.
"""
import sys

HDR = 0x7FC0                     # $00:FFC0 in a LoROM image
MAX_BITS = 56                    # Kaico Super DSP capacity, megabits

MAPNAME = {0x20: "LoROM / SlowROM", 0x21: "HiROM / SlowROM",
           0x23: "SA-1", 0x30: "LoROM / FastROM", 0x31: "HiROM / FastROM",
           0x32: "ExLoROM", 0x35: "ExHiROM"}
CARTNAME = {0x00: "ROM only", 0x01: "ROM + RAM",
            0x02: "ROM + RAM + battery",
            0x03: "ROM + coprocessor",
            0x04: "ROM + coprocessor + RAM",
            0x05: "ROM + coprocessor + RAM + battery"}
COUNTRY = {0x00: "Japan (NTSC)", 0x01: "USA (NTSC)", 0x02: "Europe (PAL)"}


def main(path):
    d = open(path, "rb").read()
    n = len(d)
    fails = []

    print("image        %s" % path)
    print("size         %d bytes = %d Mbit" % (n, n * 8 // 1024 // 1024))
    if n < 0x8000 or n & (n - 1):
        fails.append("size is not a power of two >= 32 KiB")
    if n * 8 > MAX_BITS * 1024 * 1024:
        fails.append("larger than the Kaico's %d Mbit capacity" % MAX_BITS)

    title = bytes(d[HDR:HDR + 21])
    mapmode = d[HDR + 21]
    carttype = d[HDR + 22]
    romsize = d[HDR + 23]
    ramsize = d[HDR + 24]
    country = d[HDR + 25]
    comp = d[HDR + 28] | (d[HDR + 29] << 8)
    csum = d[HDR + 30] | (d[HDR + 31] << 8)
    reset = d[0x7FFC] | (d[0x7FFD] << 8)

    print("title        %r" % title.decode("ascii", "replace"))
    print("map mode     $%02X  %s" % (mapmode, MAPNAME.get(mapmode, "UNKNOWN")))
    print("cart type    $%02X  %s" % (carttype, CARTNAME.get(carttype, "UNKNOWN")))
    print("rom size     $%02X  %d KiB declared, %d KiB actual"
          % (romsize, 1 << romsize, n // 1024))
    print("sram size    $%02X  %d KiB" % (ramsize, (1 << ramsize) if ramsize else 0))
    print("country      $%02X  %s" % (country, COUNTRY.get(country, "other")))
    print("checksum     $%04X  complement $%04X" % (csum, comp))
    print("reset vector $%04X" % reset)

    if not all(0x20 <= c < 0x7F for c in title):
        fails.append("title is not printable ASCII")
    if mapmode not in (0x20, 0x30):
        fails.append("map mode $%02X is not LoROM; the brief requires LoROM"
                     % mapmode)
    if carttype != 0x02:
        fails.append("cart type $%02X: the brief requires ROM+RAM+battery ($02)"
                     % carttype)
    if carttype >= 0x03:
        fails.append("cart type declares a coprocessor; the Kaico carries only "
                     "DSP-1/2/3/4 and FINDINGS entry 4 measured the DSP-1 out "
                     "of this design")
    if (1 << romsize) * 1024 != n:
        fails.append("declared ROM size %d KiB != actual %d KiB"
                     % (1 << romsize, n // 1024))
    if not 1 <= ramsize <= 5:
        fails.append("SRAM size $%02X is outside 2..32 KiB" % ramsize)
    if country not in (0x00, 0x01):
        fails.append("country $%02X is not NTSC" % country)
    if (csum ^ comp) != 0xFFFF:
        fails.append("checksum and complement are not complementary")
    body = bytearray(d)
    body[HDR + 28:HDR + 30] = b"\xff\xff"
    body[HDR + 30:HDR + 32] = b"\x00\x00"
    want = sum(body) & 0xFFFF
    if want != csum:
        fails.append("checksum is $%04X, should be $%04X" % (csum, want))
    if not 0x8000 <= reset <= 0xFFFF:
        fails.append("reset vector $%04X is not in the LoROM window" % reset)

    print()
    if fails:
        for f in fails:
            print("FAIL: %s" % f)
        return 1
    print("PASS: LoROM, NTSC, %d Mbit of a %d Mbit cartridge, battery SRAM "
          "declared, no enhancement chip." % (n * 8 // 1024 // 1024, MAX_BITS))
    return 0


if __name__ == "__main__":
    rc = 0
    for p in sys.argv[1:]:
        rc |= main(p)
        print()
    sys.exit(rc)
