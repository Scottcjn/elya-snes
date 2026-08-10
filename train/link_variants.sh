#!/bin/sh
# Every build variant this tree claims to support, assembled and LINKED.
#
# The merge put two conditional families into one rom/nn.s - SM_EXACTNORM
# (which normaliser) and MOE (which bank map) - and they are independent, so
# the number of ways this file can be assembled is now a product, not a sum.
# A variant that no longer links is a claim in README.md that has quietly
# stopped being true, and only a link says so.
#
# Nothing here runs a ROM; that is what the survey is for.  This is the cheap
# gate that runs first.
#
#   sh train/link_variants.sh [dense-npz] [mixture-npz]
set -e
cd "$(dirname "$0")/.."
DENSE=${1:-runs/smxfinal_av3_exact_s1.npz}
MIX=${2:-runs/moe_n8bal60k_s1.npz}
mkdir -p out out/model
OK=0
FAIL=0

pack() {    # pack <npz> ; env carries NES_SM_NORM / NES_AV_SHIFT / NES_T / banks
    rm -f out/model/moe.inc out/model/moebanks.inc out/model/nnmoe.cfg
    NES_WEIGHTS="$1" python3 host/ref.py out/model > /dev/null
}

link() {    # link <label> <cfg> [defines...]
    label=$1; cfg=$2; shift 2
    if ca65 -I rom -I out/model "$@" -o out/_v.o rom/nn.s > out/_v.log 2>&1 \
       && ld65 -C "$cfg" -o out/_v.nes out/_v.o >> out/_v.log 2>&1; then
        printf '  ok    %-46s %8d bytes\n' "$label" "$(stat -c%s out/_v.nes)"
        OK=$((OK + 1))
    else
        printf '  FAIL  %s\n' "$label"
        sed 's/^/        /' out/_v.log
        FAIL=$((FAIL + 1))
    fi
}

echo "=== dense, exact normaliser (the shipping configuration) ==="
NES_AV_SHIFT=3 NES_SM_NORM=exact pack "$DENSE"
for d in "" "-DPROFILE" "-DBENCH" "-DDEBUG -DDBGPOS=0" "-DATTNPROF" \
         "-DATTNBENCH" "-DRAMEXEC" "-DBANKPROF"; do
    link "exact dense ${d:-(plain)}" rom/nn.cfg -DNCTX=20 -DNSTREAM=7 -DSEEDTOK=1 $d
done

echo "=== dense, power-of-two normaliser (the regression target) ==="
NES_AV_SHIFT=2 NES_SM_NORM=pow2 pack runs/moe_dense60k_s1.npz
link "pow2 dense AV_SHIFT=2" rom/nn.cfg -DNCTX=20 -DNSTREAM=7 -DSEEDTOK=1
link "pow2 dense AV_SHIFT=2 -DPROFILE" rom/nn.cfg -DNCTX=20 -DNSTREAM=7 -DSEEDTOK=1 -DPROFILE

echo "=== dense, nine stream banks (rom/nn9.cfg) ==="
NES_AV_SHIFT=3 NES_SM_NORM=exact NES_STREAM_BANKS=9 pack "$DENSE"
link "exact dense 9 stream banks" rom/nn9.cfg -DNCTX=20 -DNSTREAM=9 -DSEEDTOK=1

# The context ladder was run at AV_SHIFT = 2 in every cell (see FINDINGS), so
# the only T = 85 weights that exist are AV_SHIFT = 2 ones and the guard in
# ref.Model.from_npz is right to refuse anything else.
echo "=== dense, T = 85 (the legacy attention path) ==="
NES_T=85 NES_AV_SHIFT=2 NES_SM_NORM=exact pack runs/smxctx/ctx_T85_exact_s1.npz
NES_T=85 link "exact dense T=85 AV_SHIFT=2" rom/nn.cfg -DNCTX=85 -DNSTREAM=7 -DSEEDTOK=1
NES_T=85 link "exact dense T=85 -DPROFILE" rom/nn.cfg -DNCTX=85 -DNSTREAM=7 -DSEEDTOK=1 -DPROFILE

echo "=== mixture, exact normaliser (the MERGED configuration) ==="
MIXEXACT=${MIXEXACT:-runs/fac/n8_exact_s1.npz}
if [ ! -f "$MIXEXACT" ]; then
    echo "  SKIPPED - no exact-normalisation mixture npz at $MIXEXACT"
    FAIL=$((FAIL + 1))
else
    NES_AV_SHIFT=3 NES_SM_NORM=exact pack "$MIXEXACT"
    for d in "" "-DPROFILE" "-DBANKPROF" "-DDEBUG -DDBGPOS=0" "-DATTNPROF"; do
        link "exact mixture ${d:-(plain)}" out/model/nnmoe.cfg \
             -DNCTX=20 -DSEEDTOK=1 -DMOE $d
    done
fi

echo "=== mixture, power-of-two normaliser ==="
NES_AV_SHIFT=2 NES_SM_NORM=pow2 pack "$MIX"
link "pow2 mixture" out/model/nnmoe.cfg -DNCTX=20 -DSEEDTOK=1 -DMOE
link "pow2 mixture -DPROFILE" out/model/nnmoe.cfg -DNCTX=20 -DSEEDTOK=1 -DMOE -DPROFILE

echo "=== the non-transformer ROMs ==="
for pair in "calib rom/calib.s rom/nrom.cfg" "prim rom/prim.s rom/mmc5.cfg" \
            "mmc1 rom/mmc1.s rom/mmc1.cfg" "mmc3 rom/mmc3.s rom/mmc3.cfg"; do
    set -- $pair
    if ca65 -I rom -I out/model -o out/_v.o "$2" > out/_v.log 2>&1 \
       && ld65 -C "$3" -o out/_v.nes out/_v.o >> out/_v.log 2>&1; then
        printf '  ok    %-46s %8d bytes\n' "$1" "$(stat -c%s out/_v.nes)"
        OK=$((OK + 1))
    else
        printf '  FAIL  %s\n' "$1"; sed 's/^/        /' out/_v.log; FAIL=$((FAIL + 1))
    fi
done

echo "-----------------------------------------"
echo "variants linked: $OK   failed: $FAIL"
rm -f out/_v.o out/_v.nes out/_v.log
[ "$FAIL" -eq 0 ]
