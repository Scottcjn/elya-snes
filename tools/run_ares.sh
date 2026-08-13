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

# setsid so the emulator and everything flatpak spawns under it land in their
# OWN process group, whose id is the wrapper's pid.  That group is the exact
# blast radius this script is allowed: killing it needs no pattern, matches no
# sibling agent's emulator, and cannot match this script.
setsid env DISPLAY=:0 flatpak run dev.ares.ares --system "Super Famicom" \
    --no-file-prompt "$STAGE/$BASE.sfc" >"$STAGE/$BASE.log" 2>&1 &
APID=$!

# every measurement ROM writes "DONE" at offset 8 of its battery RAM when it
# has finished; MARK/MARK_OFF override that for the bring-up ROMs.
# The DONE marker alone is not enough for the ROMs that dump 20 KiB of PPU
# state: ares's autosave is not atomic with the console's writes, so a file can
# hold the LAST thing the ROM wrote and be missing something written earlier.
# CKSUM=1 additionally requires tools/ramsum.py to validate, and the runner
# simply waits for the next autosave if it does not.
done_ok() {
    [ -f "$RAM" ] || return 1
    [ "$(dd if="$RAM" bs=1 skip=$((${MARK_OFF:-8})) count=4 2>/dev/null)" \
        = "${MARK:-DONE}" ] || return 1
    [ -z "${CKSUM:-}" ] || python3 "$(dirname "$0")/ramsum.py" "$RAM" 2>/dev/null
}

for _ in $(seq ${WAIT:-90}); do
    done_ok && break
    sleep 2
done
# Kill our own process group and nothing else.
#
# This was `pkill -x -KILL ares`, which killed EVERY emulator on the box and
# took out three sibling agents' N64 runs mid-measurement.  The first fix for
# that was `pkill -KILL -f "ares.*$(basename "$ROM")"`, which is worse in a way
# that is invisible until it bites: this script's own command line is
# `bash tools/run_ares.sh out/gateself.sfc`, which CONTAINS "ares" followed by
# the ROM name, so the pattern matched the runner and the runner killed itself.
# The symptom was `Killed` and `control run failed` from tools/gate_selftest.sh,
# which reads as an emulator problem and is not one.
#
# A pid is not a pattern.  $APID is the wrapper setsid made a group leader.
#
# `wait` reaps it, and both are inside a redirected group so bash's job-control
# notice ("Killed  setsid env DISPLAY=:0 flatpak run ...") does not go to stderr
# and read like a failure in every log this script appears in.
{ kill -KILL -"$APID"; wait "$APID"; } >/dev/null 2>&1
if ! done_ok; then
    echo "run_ares: no DONE marker in $RAM" >&2
    exit 1
fi
echo "$RAM"
