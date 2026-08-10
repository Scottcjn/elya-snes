#!/usr/bin/env python3
"""Render a ROM-vs-host verification transcript as a token-by-token table with
the symbols decoded, so the comparison can be read as text and not just as ids.
    train/sidebyside.py out/FINAL_VERIFICATION.txt
"""
import json
import re
import sys

vocab = json.load(open("data/vocab.json"))["vocab"]
txt = open(sys.argv[1] if len(sys.argv) > 1 else "out/FINAL_VERIFICATION.txt").read()

allok = True
for s in txt.split("# seed token ")[1:]:
    seed = int(s.split("\n")[0])
    rows = re.findall(r"^  (\d+)\s+(\d+)\s+(\d+)\s+(ok|\*\*\* MISMATCH \*\*\*)", s, re.M)
    print("seed token %d = %r" % (seed, vocab[seed]))
    print("  %-4s %-5s %-8s %-5s %-8s" % ("pos", "rom", "rom sym", "host", "host sym"))
    rt = ht = vocab[seed]
    for pos, rom, host, ok in rows:
        rt += vocab[int(rom)]
        ht += vocab[int(host)]
        print("  %-4s %-5s %-8r %-5s %-8r %s" % (pos, rom, vocab[int(rom)],
                                                 host, vocab[int(host)], ok))
    print("  ROM  text: %r" % rt)
    print("  HOST text: %r" % ht)
    print("  identical: %s   over %d tokens\n" % (rt == ht, len(rows)))
    allok = allok and rt == ht and len(rows) >= 16
print("ALL SEEDS EXACT OVER 16+ TOKENS: %s" % allok)
sys.exit(0 if allok else 1)
