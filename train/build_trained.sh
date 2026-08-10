#!/bin/sh
# Take a trained npz all the way to a verified cartridge.
#   train/build_trained.sh runs/<arm>.npz [seed_tok]
# Order matters: pack, PROVE max|dW| = 0, then assemble.  Assembling first
# would mean a silent exporter bug becomes a ROM that runs perfectly and says
# the wrong thing, which is exactly how the sibling N64 port lost a week.
set -e
cd "$(dirname "$0")/.."
NPZ=$1
SEED=${2:-1}
[ -n "$NPZ" ] || { echo "usage: build_trained.sh <npz> [seed_tok]"; exit 2; }

mkdir -p out out/model
NES_T=${NES_T:-20}
export NES_T
# A denser model needs more than the seven 8 KB weight-stream banks.  The
# packer, the assembler and the linker config all have to agree about how many,
# so it is set in ONE place here.
#
# NES_SM_TARGET / NES_SM_SHIFT are inherited from the environment: host/ref.py
# writes them into out/model/shifts.inc and ca65 reads that, so the packer and
# the kernel cannot disagree.  from_npz refuses an npz whose stamped budget
# does not match, which is what stops a wide-softmax model being packed for a
# narrow kernel.
NES_STREAM_BANKS=${NES_STREAM_BANKS:-7}
export NES_STREAM_BANKS
if [ "$NES_STREAM_BANKS" -eq 7 ]; then CFG=rom/nn.cfg
elif [ "$NES_STREAM_BANKS" -eq 9 ]; then CFG=rom/nn9.cfg
else echo "no linker config for $NES_STREAM_BANKS stream banks"; exit 2; fi
# Every per-mixture artifact is removed before packing, not just the three the
# assembler reads.  headers_e*.bin and routebank.bin are per-EXPERT and their
# COUNT changes with N: packing an 8-expert model on top of a 16-expert one
# leaves headers_e8..e15 on disk, describing a different model, in the
# directory the ROM is assembled from.  Nothing downstream would say so - the
# 8-expert build never opens them - but they would be committed alongside a
# cartridge they do not belong to, which is the same class of trap as the
# stale .nes that survived a failed link.
rm -f out/model/moe.inc out/model/moebanks.inc out/model/nnmoe.cfg \
      out/model/headers_e*.bin out/model/routebank.bin
NES_WEIGHTS="$NPZ" NES_SEED_TOK="$SEED" \
    python3 host/ref.py out/model | tee out/model/pack_report.txt

echo
python3 train/verify_pack.py "$NPZ" --dir out/model | tail -6

echo
build() {
    name=$1; src=$2; cfg=$3; shift 3
    ca65 -I rom -I out/model "$@" -o "out/$name.o" "$src"
    # exit status captured BEFORE the pipe; see build.sh for why
    if ! ld65 -C "$cfg" -o "out/$name.nes" -Ln "out/$name.lbl" \
              "out/$name.o" > out/$name.ldlog 2>&1; then
        echo "*** LINK FAILED: $name" >&2
        cat out/$name.ldlog >&2
        exit 1
    fi
    grep -v "Segment 'CHARS' does not exist" out/$name.ldlog \
        | grep -v "Segment 'POS' does not exist" || true
}
# A mixture build's bank map is COMPUTED by the packer, which writes both the
# assembler include and the linker config, so neither is passed in here.  The
# presence of out/model/moe.inc is the signal, and it is removed first so a
# dense build can never pick up a stale one.
if [ -f out/model/moe.inc ]; then
    CFG=out/model/nnmoe.cfg
    DEFS="-DNCTX=$NES_T -DSEEDTOK=$SEED -DMOE"
else
    DEFS="-DNCTX=$NES_T -DNSTREAM=$NES_STREAM_BANKS -DSEEDTOK=$SEED"
fi
build nn      rom/nn.s $CFG $DEFS
build nnprof  rom/nn.s $CFG $DEFS -DPROFILE
ls -l out/nn.nes out/nnprof.nes
