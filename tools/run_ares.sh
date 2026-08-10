#!/usr/bin/env bash
# Run a ROM under ares and read its battery SRAM back out.
#   usage: tools/run_ares.sh out/bench.sfc
#
# Notes paid for the hard way:
#  * the flatpak cannot see /tmp, so the ROM is staged under $HOME
#  * SIGINT to the flatpak wrapper never reaches the emulator, and SIGTERM to
#    the inner `ares` process kills it WITHOUT flushing SRAM
#  * what does work: ares autosaves battery SRAM to <rom>.ram every ~30 s while
#    running.  So the runner just waits for the file, checks the ROM's own DONE
#    marker, and waits for the next autosave if the run was still in progress.
set -u
ROM="$1"
STAGE="$HOME/snesroms"
BASE="$(basename "$ROM" .sfc)"
RAM="$STAGE/$BASE.ram"
mkdir -p "$STAGE"
cp "$ROM" "$STAGE/$BASE.sfc"
rm -f "$RAM"

DISPLAY=:0 flatpak run dev.ares.ares --system "Super Famicom" --no-file-prompt \
    "$STAGE/$BASE.sfc" >"$STAGE/$BASE.log" 2>&1 &

done_ok() { [ -f "$RAM" ] && [ "$(dd if="$RAM" bs=1 skip=8 count=4 2>/dev/null)" = "DONE" ]; }

for _ in $(seq 90); do
    done_ok && break
    sleep 2
done
pkill -x -KILL ares >/dev/null 2>&1
if ! done_ok; then
    echo "run_ares: no DONE marker in $RAM" >&2
    exit 1
fi
echo "$RAM"
