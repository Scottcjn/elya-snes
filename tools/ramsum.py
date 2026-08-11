#!/usr/bin/env python3
"""ramsum.py -- is this .ram file a coherent snapshot, or a torn one?

ares autosaves battery SRAM while the emulator runs, and that copy is NOT
atomic with respect to the console's writes.  One gate run produced a file that
held the DONE marker -- which the ROM writes last of all -- while a 512-byte
block written strictly before it was still $FF.  A checker reading that file
can fail on data the console never had; worse, it can PASS on stale data.

So the ROM sums every byte of $0100..$7EFF and stores the total at $7F00
immediately before writing DONE.  This recomputes it.  Exit 0 means the file is
one moment in the console's life rather than several.

The heartbeat that keeps the save RAM dirty lives at $7F10, deliberately
outside the summed range, so it can keep ticking without invalidating the sum.
"""
import sys

LO, HI, SUM = 0x0100, 0x7F00, 0x7F00


def main(path):
    d = open(path, "rb").read()
    if len(d) < HI + 2:
        print("%s: %d bytes, too small to carry the checksum" % (path, len(d)),
              file=sys.stderr)
        return 1
    if d[8:12] != b"DONE":
        print("%s: no DONE marker" % path, file=sys.stderr)
        return 1
    want = d[SUM] | (d[SUM + 1] << 8)
    got = sum(d[LO:HI]) & 0xFFFF
    if got != want:
        print("%s: TORN SNAPSHOT -- checksum $%04X, the ROM wrote $%04X"
              % (path, got, want), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
