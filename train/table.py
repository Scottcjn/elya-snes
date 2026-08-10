#!/usr/bin/env python3
"""Assemble the training table from runs/*.json, including whether each arm's
sparsity actually fits the ROM's 7-bank weight-stream window."""
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
import ref

CAP = ref.STREAM_BANKS * ref.BANK - ref.STREAM_BANKS * ref.BLOCK

rows = []
for f in sorted(glob.glob(os.path.join(sys.argv[1] if len(sys.argv) > 1 else "runs", "*.json"))):
    if f.endswith("_ckpt.json"):        # mid-run checkpoints, not results
        continue
    m = json.load(open(f))
    rows.append(m)

# nats per TOKEN are not comparable across vocabularies: a bpe64 token is
# worth 1.454 characters and a charset token exactly 1.  Everything is also
# reported per CHARACTER, which is the only fair axis.
CPT = json.load(open("data/vocab.json"))["chars_per_token_bpe"]
print("| arm | vocab | quant | T | tau | AV_SHIFT | fit | val | val/char | density | nnz | banks | fits 7? |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for m in sorted(rows, key=lambda r: r["val"] / (CPT if r["vocab"] == "bpe64" else 1.0)):
    nnz = m["nnz"]
    banks = int(math.ceil((nnz + ref.STREAM_BANKS * ref.BLOCK) / float(ref.BANK)))
    q = {2: "QAT", 1: "float W", 0: "fp32"}[m["quant"]]
    cpt = CPT if m["vocab"] == "bpe64" else 1.0
    print("| %s | %s | %s | %d | %.2f | %d | %.4f | %.4f | **%.4f** | %.4f | %d | %d | %s |"
          % (m["name"], m["vocab"], q, m.get("ctx", 20), m["tau"],
             m.get("av_shift", 4), m["fit"], m["val"],
             m["val"] / cpt, m["density"], nnz, banks,
             "yes" if nnz <= CAP else "**NO**"))
print("\nuniform baseline ln(64) = %.4f" % math.log(64))
print("7-bank stream window holds at most %d index bytes -> density %.4f"
      % (CAP, CAP / 102400.0))
