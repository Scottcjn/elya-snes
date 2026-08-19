#!/usr/bin/env python3
"""Does the linker config agree with the emitter about where things are?

Two files describe the same cartridge: rom/lorom2m.cfg sizes the memory areas
and tools/emit_sharded.py decides which bank each blob starts in.  Nothing makes
them agree -- ld65 will happily link a WEIGHTS area that is four banks short of
what the emitter wrote, and the overflow lands in whatever follows it.

That is not hypothetical on this tree.  The 1 MiB config was sized for five
shards; the corpus now has six, which needs 33 banks against 32.  A build that
did not check would have put MDATA on top of the padding and PTABSEG on top of
MDATA, and the first symptom would have been wrong answers from one shard.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "train"))
import corpus as C                                                # noqa: E402

BANKSZ = 0x8000
NWBANK = 4
WBANK0 = 1


def cfg_areas(path):
    txt = open(path).read()
    body = txt[txt.index("MEMORY {"):txt.index("SEGMENTS {")]
    out = {}
    # ONLY the `type = ro` areas.  ZEROPAGE and RAM are in the same block and
    # counting them made the cartridge total 2 MiB + 512 bytes, which the
    # power-of-two check then reported as a broken config -- a checker wrong
    # about the thing it exists to check.
    for m in re.finditer(r"(\w+):\s*start\s*=\s*\$([0-9A-Fa-f]+),"
                         r"\s*size\s*=\s*\$([0-9A-Fa-f]+),\s*type\s*=\s*(\w+)",
                         body):
        if m.group(4) == "ro":
            out[m.group(1)] = int(m.group(3), 16)
    return out


def main():
    cfg = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(ROOT, "rom", "lorom2m.cfg"))
    a = cfg_areas(cfg)
    n = len(C.TOPICS)
    want = {
        "WBANKS":  n * NWBANK * BANKSZ,
        "MDBANKS": n * BANKSZ,
        "PBANK":   BANKSZ,
        "GBANK":   BANKSZ,
    }
    bad = []
    for k, v in want.items():
        if a.get(k) != v:
            bad.append("%s is $%X in %s, needs $%X for %d shards"
                       % (k, a.get(k, 0), os.path.basename(cfg), v, n))
    total = sum(a.values())
    used = (1 + n * NWBANK + n + 1 + 1) * BANKSZ
    if total & (total - 1):
        bad.append("the config totals %d bytes, which is not a power of two" % total)
    if used > total:
        bad.append("%d banks of content in a %d bank cartridge"
                   % (used // BANKSZ, total // BANKSZ))
    print("topics        %d  (%s)" % (n, ", ".join(C.TOPICS)))
    print("weight banks  %d  ($%02X-$%02X)"
          % (n * NWBANK, WBANK0, WBANK0 + n * NWBANK - 1))
    print("mdata banks   %d  ($%02X-$%02X)"
          % (n, WBANK0 + n * NWBANK, WBANK0 + n * NWBANK + n - 1))
    print("cartridge     %d banks = %d KiB, %d used, %d spare"
          % (total // BANKSZ, total // 1024, used // BANKSZ,
             (total - used) // BANKSZ))
    if bad:
        for b in bad:
            print("MISMATCH: %s" % b)
        return 1
    print("config and emitter agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
