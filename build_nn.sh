#!/bin/sh
# Build the transformer cartridge.
#
# Two passes, and the reason is worth stating: the weight program is straight-
# line 65816 that CALLS the row handlers, so tools/emit.py needs their
# addresses, which only ld65 knows.  Pass 1 links with placeholder calls, pass 2
# re-emits against the real addresses.  The blob is the same size either way --
# every JSL is four bytes whatever it targets -- so bank $00's layout cannot
# move between the passes, and the script ASSERTS that rather than assuming it.
set -e
cd "$(dirname "$0")"
mkdir -p out out/model

NAME=${NAME:-nn}
DEFS=${DEFS:-}

# The game's tables are a function of data/vocab.json, and this script did not
# build them - only the Makefile did.  So a corpus change that refitted the
# vocabulary left out/game/qtok.bin holding the OLD tokenisation, and the
# cartridge fed a prompt of the wrong length while every engine arm passed.
# That is exactly what happened when the paraphrase corpus landed: 'what are
# you? ' costs five tokens now and the ROM fed seven.  tools/check_game.py
# caught it - "the ROM fed 7 prompt tokens, the host tokeniser makes 5" - but
# only because the game arm is in the gate.  Building them here makes the ROM a
# function of the tree instead of of what was last run by hand.
python3 tools/mkgame.py out/game > /dev/null

# SNES_SHARDED=1 builds the six-shard 2 MiB cartridge: a different emitter, a
# different linker map, and -DSHARDS in the engine so every matrix goes through
# the WRAM stub table instead of an absolute address.  tools/check_shards.py
# asserts the map and the emitter agree about which bank holds what, because
# ld65 will happily link a WEIGHTS area four banks short of what was written
# into it and the overflow lands in whatever follows.
#
# NOT called SNES_SHARDS: tools/emit_sharded.py already uses that name for a
# comma-separated list of model paths, and `SNES_SHARDS=1` reached it as a
# one-model list against a six-shard cartridge.
if [ "${SNES_SHARDED:-0}" = "1" ]; then
    python3 tools/check_shards.py > /dev/null || {
        python3 tools/check_shards.py >&2
        echo "*** rom/lorom2m.cfg and tools/emit_sharded.py disagree" >&2
        exit 1
    }
    EMITTER=tools/emit_sharded.py
    LDCFG=rom/lorom2m.cfg
    DEFS="$DEFS -DSHARDS"
else
    EMITTER=tools/emit.py
    LDCFG=rom/lorom256.cfg
fi

emit() { python3 "$EMITTER" "$@"; }
# SNES_FAST=1 moves every JSL/JML target and the PTAB base into banks $80+,
# which is the only difference between the two clock arms.
asm()  { ca65 --cpu 65816 -I rom -I out/model -I out/game -I assets $DEFS -o "out/$NAME.o" \
              -l "out/$NAME.lst" rom/nn.s; }
link() { ld65 -C "$LDCFG" -o "out/$NAME.sfc" -m "out/$NAME.map" \
              -Ln "out/$NAME.lbl" "out/$NAME.o"; }

emit
asm
link
grep -E '\.H_' "out/$NAME.lbl" | sort > out/.h1
emit "out/$NAME.lbl"
asm
link
grep -E '\.H_' "out/$NAME.lbl" | sort > out/.h2
if ! cmp -s out/.h1 out/.h2; then
    echo "*** handler addresses moved between passes -- the weight program's" >&2
    echo "*** JSL targets are stale.  diff:" >&2
    diff out/.h1 out/.h2 >&2 || true
    exit 1
fi
python3 tools/fixhdr.py "out/$NAME.sfc"
