#!/usr/bin/env python3
"""Build the 64-symbol corpus for the NES transformer.

V = 64 is a HARD constraint of the port: rom/nn.s declares NVOCAB = 64 and the
output head is 64 x 64 ternary weights.  64 symbols is not enough for printable
ASCII, so the charset has to be chosen deliberately.  This script measures the
corpus first and reports exactly what the mapping costs.

Two vocabularies are produced from the same mapped text so the training arms
are comparable:

  charset : 33 base symbols, 31 of the 64 slots unused (dead head rows)
  bpe64   : the same 33 base symbols + 31 greedy merges, all 64 slots used

The split is BY STORY, never by line.  The sibling N64 run was bitten by a
"validation" set that was a reshuffle of the fit lines, which makes val
identical to fit by construction and the number meaningless.
"""
import argparse
import collections
import json
import os
import random
import sys

import numpy as np

# ---------------------------------------------------------------------------
# the charset, chosen from the measured histogram (see report below)
# ---------------------------------------------------------------------------
BASE = (
    "abcdefghijklmnopqrstuvwxyz"   # 26 - 91.0% of the corpus on their own
    " "                            #  1 - 18.8%, the single most frequent symbol
    ".,"                           #  2 - 1.86% + 0.90%
    "\"'"                          #  2 - 0.48% + 0.22%, dialogue is everywhere
    "!?"                           #  2 - 0.17% + 0.06%
)                                  # = 33 symbols

# Everything else is either folded onto a symbol that is in BASE or dropped.
# Folds are chosen so that the *shape* of the text survives: a newline is a
# word gap at T=20, an em dash is a word gap, a colon introduces a clause the
# way a comma does.
FOLD = {
    "\n": " ", "\t": " ", "\r": " ", " ": " ",
    "-": " ", "–": " ", "—": " ", "/": " ",
    ":": ",", ";": ",",
    "“": '"', "”": '"', "‘": "'", "’": "'", "´": "'",
    "…": ".",
    "é": "e", "ñ": "n",
}
# Dropped outright: digits (0.0035% of all characters) and the handful of
# stray symbols (&, #, $, parens, emoji).  Dropping is recorded and reported.


def build_maps():
    stoi = {c: i for i, c in enumerate(BASE)}
    return stoi


def map_text(raw, stoi):
    """Lowercase, fold, drop.  Returns (mapped_string, stats)."""
    out = []
    n_exact = n_fold = n_drop = 0
    for ch in raw.lower():
        if ch in stoi:
            out.append(ch)
            n_exact += 1
        elif ch in FOLD:
            out.append(FOLD[ch])
            n_fold += 1
        else:
            n_drop += 1
    s = "".join(out)
    # collapse runs of spaces created by the newline/dash folds
    parts = s.split()
    joined = " ".join(parts)
    return joined, dict(exact=n_exact, folded=n_fold, dropped=n_drop)


# ---------------------------------------------------------------------------
# greedy pair merges to fill the 64 slots
# ---------------------------------------------------------------------------
def learn_merges(token_lists, base_n, target, sample_docs=4000):
    """Classic BPE: repeatedly merge the most frequent adjacent pair."""
    docs = [list(t) for t in token_lists[:sample_docs]]
    merges = []
    nxt = base_n
    while nxt < target:
        cnt = collections.Counter()
        for d in docs:
            for a, b in zip(d, d[1:]):
                cnt[(a, b)] += 1
        if not cnt:
            break
        (a, b), _ = cnt.most_common(1)[0]
        merges.append((a, b))
        new = nxt
        nxt += 1
        for i, d in enumerate(docs):
            out, j = [], 0
            while j < len(d):
                if j + 1 < len(d) and d[j] == a and d[j + 1] == b:
                    out.append(new)
                    j += 2
                else:
                    out.append(d[j])
                    j += 1
            docs[i] = out
    return merges


def apply_merges(ids, merges, base_n):
    for k, (a, b) in enumerate(merges):
        new = base_n + k
        out, j = [], 0
        n = len(ids)
        while j < n:
            if j + 1 < n and ids[j] == a and ids[j + 1] == b:
                out.append(new)
                j += 2
            else:
                out.append(ids[j])
                j += 1
        ids = out
    return ids


def spell(tid, merges, base_n):
    if tid < base_n:
        return BASE[tid]
    a, b = merges[tid - base_n]
    return spell(a, merges, base_n) + spell(b, merges, base_n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/TinyStories-valid.txt")
    ap.add_argument("--out", default="data")
    ap.add_argument("--val-stories", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20260807)
    a = ap.parse_args()

    raw = open(a.raw, encoding="utf-8", errors="replace").read()
    stories = [s.strip() for s in raw.split("<|endoftext|>")]
    stories = [s for s in stories if s]
    print("raw stories                  %d" % len(stories))

    stoi = build_maps()
    assert len(BASE) == len(set(BASE)) == 33, len(BASE)

    mapped, tot = [], collections.Counter()
    for s in stories:
        m, st = map_text(s, stoi)
        if len(m) < 40:
            continue
        mapped.append(m)
        for k, v in st.items():
            tot[k] += v
    n_all = tot["exact"] + tot["folded"] + tot["dropped"]
    print("mapped stories               %d" % len(mapped))
    print("charset size                 %d  (%d free slots of 64)"
          % (len(BASE), 64 - len(BASE)))
    print("chars represented exactly    %10d  %8.4f%%" % (tot["exact"], 100 * tot["exact"] / n_all))
    print("chars folded onto a symbol   %10d  %8.4f%%" % (tot["folded"], 100 * tot["folded"] / n_all))
    print("chars dropped                %10d  %8.4f%%" % (tot["dropped"], 100 * tot["dropped"] / n_all))
    print("coverage (exact+folded)      %8.4f%%" % (100 * (tot["exact"] + tot["folded"]) / n_all))

    rng = random.Random(a.seed)
    order = list(range(len(mapped)))
    rng.shuffle(order)
    val_ix = set(order[: a.val_stories])
    fit = [mapped[i] for i in range(len(mapped)) if i not in val_ix]
    val = [mapped[i] for i in range(len(mapped)) if i in val_ix]
    print("fit stories %d   val stories %d   (disjoint whole stories)"
          % (len(fit), len(val)))

    os.makedirs(a.out, exist_ok=True)

    # ---- arm 1: plain charset -------------------------------------------
    def enc_char(txt):
        return [stoi[c] for c in txt]

    fit_c = np.concatenate([np.array(enc_char(s) + [stoi[" "]], dtype=np.uint8) for s in fit])
    val_c = np.concatenate([np.array(enc_char(s) + [stoi[" "]], dtype=np.uint8) for s in val])
    np.save(os.path.join(a.out, "fit_charset.npy"), fit_c)
    np.save(os.path.join(a.out, "val_charset.npy"), val_c)
    print("charset  fit tokens %d   val tokens %d" % (len(fit_c), len(val_c)))

    # ---- arm 2: 64-slot BPE ---------------------------------------------
    merges = learn_merges([enc_char(s) for s in fit], len(BASE), 64)
    print("learned %d merges -> vocab %d" % (len(merges), len(BASE) + len(merges)))
    voc = [BASE[i] for i in range(len(BASE))] + [spell(len(BASE) + k, merges, len(BASE))
                                                 for k in range(len(merges))]
    print("merge tokens: " + " ".join("%r" % v for v in voc[len(BASE):]))

    fit_b = np.concatenate([np.array(apply_merges(enc_char(s) + [stoi[" "]], merges, len(BASE)),
                                     dtype=np.uint8) for s in fit])
    val_b = np.concatenate([np.array(apply_merges(enc_char(s) + [stoi[" "]], merges, len(BASE)),
                                     dtype=np.uint8) for s in val])
    np.save(os.path.join(a.out, "fit_bpe64.npy"), fit_b)
    np.save(os.path.join(a.out, "val_bpe64.npy"), val_b)
    print("bpe64    fit tokens %d   val tokens %d   (%.3f chars/token)"
          % (len(fit_b), len(val_b), len(fit_c) / len(fit_b)))

    json.dump({"base": BASE, "fold": FOLD, "merges": merges, "vocab": voc,
               "chars_per_token_bpe": len(fit_c) / len(fit_b),
               "coverage_exact": tot["exact"] / n_all,
               "coverage_folded": tot["folded"] / n_all,
               "coverage_dropped": tot["dropped"] / n_all},
              open(os.path.join(a.out, "vocab.json"), "w"), indent=1)
    print("wrote data/vocab.json")


if __name__ == "__main__":
    sys.exit(main())
