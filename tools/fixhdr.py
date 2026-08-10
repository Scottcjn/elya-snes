#!/usr/bin/env python3
"""Patch the SNES internal checksum of a LoROM image, in place.

The convention: with the complement field forced to $FFFF and the checksum
field to $0000, sum every byte of the image mod 65536.  That sum is the
checksum; the complement is its bitwise inverse.  Only sizes that are a power
of two are handled, which is all we build.
"""
import sys

CHKCOMP = 0x7FDC  # file offset of $00:FFDC in any LoROM image
CHKSUM = 0x7FDE


def main(path: str) -> int:
    data = bytearray(open(path, "rb").read())
    n = len(data)
    if n < 0x8000 or n & (n - 1):
        print(f"fixhdr: expected a power-of-two image >= 32 KiB, got {n}",
              file=sys.stderr)
        return 1

    data[CHKCOMP:CHKCOMP + 2] = b"\xff\xff"
    data[CHKSUM:CHKSUM + 2] = b"\x00\x00"

    total = sum(data) & 0xFFFF
    comp = total ^ 0xFFFF

    data[CHKCOMP] = comp & 0xFF
    data[CHKCOMP + 1] = comp >> 8
    data[CHKSUM] = total & 0xFF
    data[CHKSUM + 1] = total >> 8

    open(path, "wb").write(data)
    title = bytes(data[0x7FC0:0x7FD5]).decode("ascii", "replace")
    print(f"fixhdr: {path}  title='{title}'  checksum=${total:04X} complement=${comp:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
