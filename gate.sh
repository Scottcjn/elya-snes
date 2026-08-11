#!/bin/sh
# The shipping gate.  Build every variant of the cartridge, run every one under
# ares, and check every token of every one against host/ref.py.
#
# It runs the WHOLE matrix on purpose.  A change that only touches one arm has
# already silently broken another on this tree: the survey refactor made the
# seed token a parameter and left the single-seed path seeding itself from
# uninitialised WRAM, which passed the 64-seed survey and failed everything
# else -- and only re-running everything found it.
set -e
cd "$(dirname "$0")"
mkdir -p out
FAIL=0

run() {                 # run <name> <defs> <fast> <checker>
    name=$1; defs=$2; fast=$3; check=$4
    echo "=== $name ==="
    SNES_FAST=$fast NAME=$name DEFS="$defs" ./build_nn.sh > "out/$name.build" 2>&1 \
        || { echo "BUILD FAILED"; cat "out/$name.build"; FAIL=1; return; }
    MARK=DONE WAIT=190 bash tools/run_ares.sh "out/$name.sfc" > /dev/null 2>&1 \
        || { echo "RUN FAILED (no DONE marker)"; FAIL=1; return; }
    cp "$HOME/snesroms/$name.ram" "out/$name.ram"
    python3 "tools/$check" "out/$name.ram" | tail -n 3 || FAIL=1
}

run nn            ""                       0 check_nn.py
run nnfast        "-DFASTROM=1"            1 check_nn.py
run nnprof        "-DPROFILE"              0 check_nn.py
run nnfastprof    "-DFASTROM=1 -DPROFILE"  1 check_nn.py
run nnstage       "-DPROFILE -DSTAGEPROF"  0 check_nn.py
run nnsurvey      "-DSURVEY"               0 check_survey.py
run nnfastsurvey  "-DFASTROM=1 -DSURVEY"   1 check_survey.py

# The survey compares argmaxes.  These compare the arithmetic underneath them:
# the residual stream after every layer and the attention output, element by
# element, at the first, a middle and the last position.
for P in 0 9 18; do
    echo "=== nndbg, position $P ==="
    SNES_FAST=0 NAME=nndbg DEFS="-DDEBUG -DDBGPOS=$P" ./build_nn.sh \
        > out/nndbg.build 2>&1 || { echo "BUILD FAILED"; FAIL=1; continue; }
    MARK=DONE WAIT=190 bash tools/run_ares.sh out/nndbg.sfc > /dev/null 2>&1 \
        || { echo "RUN FAILED"; FAIL=1; continue; }
    cp "$HOME/snesroms/nndbg.ram" "out/nndbg_$P.ram"
    python3 tools/check_nn.py "out/nndbg_$P.ram" | tail -n 1 || FAIL=1
    python3 tools/check_debug.py "out/nndbg_$P.ram" "$P" | tail -n 1 || FAIL=1
done

echo "=== cartridge headers ==="
python3 tools/kaico_check.py out/nn.sfc out/nnfast.sfc | tee out/kaico_check.txt \
    | grep -E "^(PASS|FAIL)" || FAIL=1

echo "=== reports ==="
python3 tools/prof_nn.py  out/nnprof.ram      > out/nn_profile.txt
python3 tools/prof_nn.py  out/nnfastprof.ram --fast > out/nnfast_profile.txt
python3 tools/stage_nn.py out/nnstage.ram     > out/nn_stages.txt
python3 tools/check_survey.py out/nnsurvey.ram > out/nn_survey.txt
tail -n 6 out/nn_profile.txt
tail -n 6 out/nnfast_profile.txt

# leave the SHIPPING image in place: the gate's last build must not be a
# profiling or survey variant
SNES_FAST=0 NAME=nn DEFS="" ./build_nn.sh > /dev/null
SNES_FAST=1 NAME=nnfast DEFS="-DFASTROM=1" ./build_nn.sh > /dev/null

[ "$FAIL" = 0 ] && echo "GATE: pass" || { echo "GATE: FAIL"; exit 1; }
