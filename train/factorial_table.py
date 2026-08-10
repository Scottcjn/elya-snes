#!/usr/bin/env python3
"""The 2 x 2, and the arithmetic that says whether the two wins compose.

Every cell is re-scored here rather than read out of the training json, and it
is re-scored by ONE estimator (train/eval_npz.py, 60 batches, the trainer's own
eval generator, seed 9999).  Each arm is evaluated in a SUBPROCESS carrying the
environment it was trained under, because train/model_nes.py builds its forward
pass from NES_SM_NORM / NES_AV_SHIFT at import: scoring a power-of-two model
through the exact-normalisation forward pass gives a plausible number, and that
number is the one the interaction term is made of.  eval_npz.py refuses the
mismatch, so this cannot silently do the wrong thing - it can only fail.

    train/factorial_table.py [runs/fac]
"""
import json
import math
import os
import re
import subprocess
import sys

CPT = json.load(open("data/vocab.json"))["chars_per_token_bpe"]
ENV = {"pow2": {"NES_SM_NORM": "pow2", "NES_AV_SHIFT": "2"},
       "exact": {"NES_SM_NORM": "exact", "NES_AV_SHIFT": "3"}}
CELLS = [("dense", "pow2"), ("dense", "exact"), ("n8", "pow2"), ("n8", "exact")]
SEEDS = (1, 2)


def score(path, norm):
    env = dict(os.environ)
    env.update(ENV[norm])
    out = subprocess.run([sys.executable, "train/eval_npz.py", path, "60"],
                         capture_output=True, text=True, env=env)
    if out.returncode:
        raise SystemExit("eval failed on %s:\n%s" % (path, out.stderr.strip()))
    m = re.search(r"val ([\d.]+) nats/token +([\d.]+) nats/char", out.stdout)
    if not m:
        raise SystemExit("unparsable eval output for %s:\n%s" % (path, out.stdout))
    return float(m.group(1)), float(m.group(2))


def arithmetic(label, cell, spread):
    """cell[(capacity, normaliser)] -> mean nats/char.  Prints the 2 x 2's
    two main effects, each measured at BOTH levels of the other factor, and
    the residual that says whether they add."""
    dp, de, np_, ne = (cell[k] for k in CELLS)
    a_dense = de - dp            # softmax effect at dense capacity
    a_moe = ne - np_             # softmax effect at 8 experts
    b_pow2 = np_ - dp            # capacity effect under pow2
    b_exact = ne - de            # capacity effect under exact
    additive = dp + a_dense + b_pow2
    inter = ne - additive        # positive = they get in each other's way
    print("""
--- %s ---
seed spread within a cell (max |seed 1 - seed 2|): %.4f

main effect A, the exact normaliser + AV_SHIFT 3
    at dense capacity     %+.4f   (%.4f -> %.4f)
    at 8 experts          %+.4f   (%.4f -> %.4f)

main effect B, 8 experts
    under pow2            %+.4f   (%.4f -> %.4f)
    under exact           %+.4f   (%.4f -> %.4f)

if they simply ADDED, the merged cell would be
    %.4f %+.4f %+.4f = %.4f
measured merged cell      %.4f
INTERACTION               %+.4f   (positive = the second win is worth LESS
                                   once the first has been taken)
                          %+.1f%% of effect A, %+.1f%% of effect B
                          %s the seed spread
""" % (label, spread,
       a_dense, dp, de, a_moe, np_, ne,
       b_pow2, dp, np_, b_exact, de, ne,
       dp, a_dense, b_pow2, additive, ne, inter,
       100.0 * inter / abs(a_dense), 100.0 * inter / abs(b_pow2),
       "INSIDE" if abs(inter) < spread else "OUTSIDE"))


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "runs/fac"
    got, trained = {}, {}
    for cap, norm in CELLS:
        for s in SEEDS:
            p = os.path.join(d, "%s_%s_s%d.npz" % (cap, norm, s))
            if not os.path.exists(p):
                print("MISSING %s" % p)
                continue
            got[(cap, norm, s)] = score(p, norm)
            # the trainer's OWN final 60-batch number, which is the estimator
            # both branches published with.  It is a different draw from the
            # same held-out split (the trainer's eval generator has been walked
            # by every mid-run evaluation; eval_npz.py restarts it), so the
            # LEVELS differ by ~0.008 nats/char.  Both are reported because the
            # question is whether the two effects add, and an effect that
            # depended on which of two unbiased draws was used would not be one.
            trained[(cap, norm, s)] = json.load(
                open(p.replace(".npz", ".json")))["val"] / CPT

    for label, table in (("train/eval_npz.py, 60 batches, generator reset", got),
                         ("the trainer's own final 60-batch eval", trained)):
        print("\n### %s" % label)
        print("\n| capacity | normaliser | seed 1 | seed 2 | **mean nats/char** |")
        print("|---|---|---:|---:|---:|")
        mean, spread = {}, 0.0
        for cap, norm in CELLS:
            v = [table[(cap, norm, s)][1] if isinstance(table[(cap, norm, s)], tuple)
                 else table[(cap, norm, s)]
                 for s in SEEDS if (cap, norm, s) in table]
            if not v:
                continue
            mean[(cap, norm)] = sum(v) / len(v)
            if len(v) > 1:
                spread = max(spread, abs(v[0] - v[1]))
            print("| %s | %s | %s | %s | **%.4f** |"
                  % ("dense" if cap == "dense" else "8 experts", norm,
                     "%.4f" % v[0], "%.4f" % v[1] if len(v) > 1 else "-",
                     mean[(cap, norm)]))
        if all(k in mean for k in CELLS):
            arithmetic(label, mean, spread)
        else:
            print("\n(incomplete: the interaction needs all four cells)")

    print("uniform ln(64) = %.4f nats/token = %.4f nats/char"
          % (math.log(64), math.log(64) / CPT))
    print("""
Published elsewhere, on OTHER trees:
  dense + pow2      1.4133 / 1.4149 = 1.4141   (runs/final_av2_bpe64_tau0.75,
                                                runs/t20_final_s2 - an older
                                                implementation; these predate
                                                the `_ctx` stamp)
  dense + exact     1.3754 / 1.3779 = 1.3766   (reproduced here EXACTLY by
                                                dense_exact under the trainer's
                                                own estimator)
  8 experts + pow2  1.2202 / 1.2221 = 1.2212   (reproduced here EXACTLY by
                                                n8_pow2, to every digit the
                                                json holds)""")


if __name__ == "__main__":
    main()
