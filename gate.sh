#!/bin/bash
# The shipping gate.  Build every variant of the cartridge, run every one under
# ares, and check every token of every one against host/ref.py.
#
# It runs the WHOLE matrix on purpose.  A change that only touches one arm has
# already silently broken another on this tree: the survey refactor made the
# seed token a parameter and left the single-seed path seeding itself from
# uninitialised WRAM, which passed the 64-seed survey and failed everything
# else -- and only re-running everything found it.
set -e
# bash, not sh: `set -o pipefail` is not in POSIX sh.  The dash on this machine
# happens to accept it; relying on that is how a gate stops gating somewhere
# else.
# pipefail is LOAD-BEARING.  Every checker below is piped into `tail`, and
# without it the pipeline's status is tail's -- so `check | tail || FAIL=1`
# never sets FAIL and a failing checker printed "FAIL: ..." while the gate
# printed "GATE: pass" underneath it.  That is what happened: the game's
# real-controller arm failed one of fifteen checks and this script reported a
# clean run.  It had been true of every arm since the gate was written.
set -o pipefail
cd "$(dirname "$0")"
mkdir -p out
FAIL=0

run() {                 # run <name> <defs> <fast> <checker> [cksum]
    # cksum=1 additionally requires the .ram to pass tools/ramsum.py.  Only the
    # game arms carry that checksum: they are the ones that dump 20 KiB of PPU
    # state, and therefore the only ones a non-atomic autosave can catch
    # half-written.  The engine arms write twenty bytes and finish in seconds.
    name=$1; defs=$2; fast=$3; check=$4; ck=${5:-}
    echo "=== $name ==="
    SNES_FAST=$fast NAME=$name DEFS="$defs" ./build_nn.sh > "out/$name.build" 2>&1 \
        || { echo "BUILD FAILED"; cat "out/$name.build"; FAIL=1; return; }
    MARK=DONE CKSUM=$ck WAIT=${WAIT:-400} bash tools/run_ares.sh "out/$name.sfc" > /dev/null 2>&1 \
        || { echo "RUN FAILED (no DONE marker, or a torn save)"; FAIL=1; return; }
    cp "$HOME/snesroms/$name.ram" "out/$name.ram"
    if [ "$check" != none ]; then
        python3 "tools/$check" "out/$name.ram" | tail -n 3 || FAIL=1
    fi
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
    MARK=DONE WAIT=${WAIT:-400} bash tools/run_ares.sh out/nndbg.sfc > /dev/null 2>&1 \
        || { echo "RUN FAILED"; FAIL=1; continue; }
    cp "$HOME/snesroms/nndbg.ram" "out/nndbg_$P.ram"
    python3 tools/check_nn.py "out/nndbg_$P.ram" | tail -n 1 || FAIL=1
    python3 tools/check_debug.py "out/nndbg_$P.ram" "$P" | tail -n 1 || FAIL=1
done

# ---------------------------------------------------------------------------
# the game layer.  Its engine is the same rom/nn.s the arms above build, so a
# change that breaks one breaks both -- which is the point of running the whole
# matrix rather than the arm that was touched.
#
# gameqa    scripted player, ends after two answers, dumps the PPU to SRAM
# gamectl   the SAME build with the forward pass removed.  Negative control for
#           the coin binding: it must produce zero coins over 1,700 frames.
# gamepad   NO scripted player: the SHIPPING read_pad, polling $4212/$4218.
#           Nobody presses anything, so it walks itself to the ask menu -- but
#           the path that ships is the path that ran.
run gameqa  "-DGAME -DGAUTO -DGRUN=2"                    0 none 1
run gamectl "-DGAME -DGAUTO -DNOGEN -DGFRAMES=1700"      0 none 1
run gamepad "-DGAME -DGFRAMES=1600"                      0 none 1
echo "=== the game, real controller path ==="
python3 tools/check_game.py out/gamepad.ram | tail -n 1 || FAIL=1
echo "=== the game ==="
python3 tools/check_game.py out/gameqa.ram out/gamectl.ram > out/game_check.txt 2>&1 \
    || FAIL=1
tail -n 2 out/game_check.txt
python3 tools/render_frame.py out/gameqa.ram out/frames || FAIL=1

# the shipping game images: no scripted player, no end condition
SNES_FAST=0 NAME=game     DEFS="-DGAME"             ./build_nn.sh > /dev/null
SNES_FAST=1 NAME=gamefast DEFS="-DGAME -DFASTROM=1" ./build_nn.sh > /dev/null

echo "=== cartridge headers ==="
python3 tools/kaico_check.py out/nn.sfc out/nnfast.sfc out/game.sfc \
    out/gamefast.sfc | tee out/kaico_check.txt \
    | grep -E "^(PASS|FAIL)" || FAIL=1

echo "=== reports ==="
python3 tools/prof_nn.py  out/nnprof.ram      > out/nn_profile.txt
python3 tools/prof_nn.py  out/nnfastprof.ram --fast > out/nnfast_profile.txt
python3 tools/stage_nn.py out/nnstage.ram     > out/nn_stages.txt
python3 tools/check_survey.py out/nnsurvey.ram > out/nn_survey.txt
tail -n 6 out/nn_profile.txt
tail -n 6 out/nnfast_profile.txt

# the game's own profile arm: cycles per token WITH the presentation layer
run gameprof     "-DGAME -DGAUTO -DGRUN=1 -DPROFILE"              0 none 1
run gamefastprof "-DGAME -DGAUTO -DGRUN=1 -DPROFILE -DFASTROM=1"  1 none 1
python3 tools/prof_nn.py out/gameprof.ram          > out/game_profile.txt
python3 tools/prof_nn.py out/gamefastprof.ram --fast > out/gamefast_profile.txt
echo "=== tokens per second, engine only vs with the game ==="
grep "TOKENS\|tokens/s" out/nn_profile.txt out/nnfast_profile.txt \
     out/game_profile.txt out/gamefast_profile.txt | sed 's/^out\///'

# Leave the SHIPPING images in place: the gate's last build must not be a
# profiling or survey variant.  The ENGINE builds go last, in the same order
# `make nn` runs them, on purpose: the weight program's JSL targets are the row
# handlers' addresses, so linking the game layer in emits a DIFFERENT
# out/model/weights.bin.  Ending on the same build `make nn` ends on is what
# keeps the tracked intermediates from flapping between the two.
SNES_FAST=0 NAME=game     DEFS="-DGAME"             ./build_nn.sh > /dev/null
SNES_FAST=1 NAME=gamefast DEFS="-DGAME -DFASTROM=1" ./build_nn.sh > /dev/null
SNES_FAST=0 NAME=nn       DEFS=""                   ./build_nn.sh > /dev/null
SNES_FAST=1 NAME=nnfast   DEFS="-DFASTROM=1"        ./build_nn.sh > /dev/null

[ "$FAIL" = 0 ] && echo "GATE: pass" || { echo "GATE: FAIL"; exit 1; }
