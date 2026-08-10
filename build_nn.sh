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

emit() { python3 tools/emit.py "$@"; }
asm()  { ca65 --cpu 65816 -I rom -I out/model $DEFS -o "out/$NAME.o" \
              -l "out/$NAME.lst" rom/nn.s; }
link() { ld65 -C rom/lorom256.cfg -o "out/$NAME.sfc" -m "out/$NAME.map" \
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
