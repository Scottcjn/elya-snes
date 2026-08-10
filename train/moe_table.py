#!/usr/bin/env python3
"""The mixture comparison table, in nats per CHARACTER.

nats/token is not the axis: a bpe64 token is worth
chars_per_token_bpe = 1.453737922720111 characters, and every loss number this
project has ever published is that division.  It is done here from
data/vocab.json rather than from a constant typed in twice.
"""
import glob
import json
import math
import os
import sys

CPT = json.load(open("data/vocab.json"))["chars_per_token_bpe"]
SHARED = 3 * 4 * 64 * 64 + 64 * 64          # Wq/Wk/Wv/Wo x L, plus the head
PER_EXPERT = 3 * (128 * 64 + 64 * 128)      # W1, W2 x L


def rows(pats):
    out = []
    for pat in pats:
        for f in sorted(glob.glob(pat)):
            if f.endswith("_ckpt.json"):
                continue
            out.append(json.load(open(f)))
    return out


def main():
    pats = sys.argv[1:] or ["runs/sw_*.json", "runs/moe_*.json"]
    r = rows(pats)
    print("| arm | N | route | steps | seed | val nats/token | "
          "**val nats/char** | density | weights on cart |")
    print("|---|---:|---|---:|---:|---:|---:|---:|---:|")
    for m in sorted(r, key=lambda x: x["val"]):
        n = m.get("nexp", 1)
        cart = SHARED + n * PER_EXPERT
        print("| %s | %d | %s | %d | %d | %.4f | **%.4f** | %.4f | %d |"
              % (m["name"], n, m.get("route", "-") if n > 1 else "-",
                 m["steps"], m.get("seed", 1), m["val"], m["val"] / CPT,
                 m["density"], cart))
    print("\nuniform ln(64) = %.4f nats/token = %.4f nats/char"
          % (math.log(64), math.log(64) / CPT))
    print("T = 20 dense baseline to beat: 1.4133 / 1.4149 nats/char")


if __name__ == "__main__":
    main()
