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
#  * the staging directory is SHARED BY EVERY CHECKOUT OF THIS REPO, and the
#    staged name is the ROM's basename.  Two trees building `nnfastprof` stage
#    to the same file, and an emulator left running by one of them keeps
#    autosaving ITS output over the other's .ram every ~30 s.  That is not
#    hypothetical: a stray ares holding snesroms/nnfastprof.sfc, 42 hours old
#    and from a previous session, failed that one gate arm on this tree three
#    runs in a row with the identical wrong text while a byte-identical ROM
#    staged under a different name passed.  Two defences below, and neither is
#    a `pkill`: SNES_STAGE gives a tree its own directory, and the guard turns
#    a shared name into a loud failure instead of another process's answer.
set -u
ROM="$1"
STAGE="${SNES_STAGE:-$HOME/snesroms/$(basename "$(cd "$(dirname "$0")/.." && pwd)")}"
BASE="$(basename "$ROM" .sfc)"
RAM="$STAGE/$BASE.ram"
mkdir -p "$STAGE"

# Is someone else already running this exact staged ROM?  Asked of the staged
# PATH in /proc/*/cmdline, not of any process NAME: the path is unique to this
# file, so a match is a real conflict and cannot be a sibling agent's unrelated
# emulator.  This only REPORTS - nothing here kills anything it did not start.
#
# An fd scan was tried first and does not work: ares reads the ROM at load and
# closes the descriptor, so /proc/PID/fd is empty of it while the emulator is
# very much still running and still autosaving over the .ram.
holder=""
for c in /proc/[0-9]*/cmdline; do
    [ -r "$c" ] || continue
    case "$(tr '\0' ' ' < "$c" 2>/dev/null)" in
        *"$STAGE/$BASE.sfc"*)
            pid="${c#/proc/}"; pid="${pid%%/*}"
            [ "$pid" = "$$" ] && continue
            holder="$pid"; break;;
    esac
done
if [ -n "$holder" ]; then
    echo "run_ares: pid $holder is already running $STAGE/$BASE.sfc." >&2
    echo "run_ares: its autosaves overwrite this run's .ram, so the result" >&2
    echo "run_ares: would be that process's answer and not this build's." >&2
    echo "run_ares: give this tree its own SNES_STAGE, or stop that process" >&2
    echo "run_ares: by ITS OWN process group id." >&2
    exit 2
fi

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
