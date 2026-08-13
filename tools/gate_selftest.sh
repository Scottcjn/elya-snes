#!/bin/bash
# Does the gate actually gate?
#
# It did not, for the whole life of this repo until it was fixed: every checker
# in gate.sh is piped into `tail`, and without `set -o pipefail` the pipeline's
# exit status is tail's.  The gate printed a checker's own "FAIL:" line and
# "GATE: pass" underneath it.  A green gate is worth exactly as much as the
# proof that a broken build turns it red, so this produces one.
#
# The break is deliberate and surgical: tools/emit.py builds the ROM's
# attention requantise table at AV_SHIFT - 1 instead of AV_SHIFT.  That is a
# real bug of the class this repo has already been bitten by (a model trained
# at one shift and run at another, FINDINGS and train/train_nes.py both warn
# about it), it leaves a ROM that builds, boots and generates fluent-looking
# text, and it does NOT touch host/ref.py - so the reference still computes the
# right answer and the cartridge no longer agrees with it.  rom/*.s is not
# modified.
#
# The 190-iteration wait was too short whenever the box is busy: ares runs at
# less than realtime under load, the ROM never reaches DONE, and the self-test
# reports "broken run failed" - which is not a result either way.  WAIT is now
# an override with a longer default.
#
# Measured, 2026-08-11:
#   host text 'because and said, "you ca'
#   rom  text 'bass the sadradrasrasras'
#   FAIL: 18 of 20 positions differ
#   FAIL=1
#
# The same ROM built from an unpatched tools/emit.py gives 20/20 and FAIL=0,
# so the checker is discriminating, not merely pessimistic.
set -u
cd "$(dirname "$0")/.."
EMIT=tools/emit.py
BAK=$(mktemp)
cp "$EMIT" "$BAK"
restore() { cp "$BAK" "$EMIT"; rm -f "$BAK"; }
trap restore EXIT

echo "=== control: the tree as it stands ==="
SNES_FAST=0 NAME=gateself DEFS="" ./build_nn.sh > out/gateself.build 2>&1 \
    || { echo "control build failed"; exit 2; }
MARK=DONE WAIT=${WAIT:-400} bash tools/run_ares.sh out/gateself.sfc > /dev/null 2>&1 \
    || { echo "control run failed"; exit 2; }
cp "$HOME/snesroms/gateself.ram" out/gateself.ram
set -o pipefail
CTL=0
python3 tools/check_nn.py out/gateself.ram | tail -n 3 || CTL=1

echo
echo "=== the deliberate break: the AV requantise table off by one shift ==="
python3 - "$EMIT" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
a = "put(TQAV, wtab([q_of(t, AV_SHIFT) + BIAS for t in range(15 << AV_SHIFT)]))"
assert a in s, "emit.py no longer has the line this self-test patches"
open(p, "w").write(s.replace(a, a.replace("q_of(t, AV_SHIFT)",
                                          "q_of(t, AV_SHIFT - 1)")))
PY
SNES_FAST=0 NAME=gateselfbad DEFS="" ./build_nn.sh > out/gateselfbad.build 2>&1 \
    || { echo "broken build failed to BUILD, which is not the test"; exit 2; }
restore; trap - EXIT
MARK=DONE WAIT=${WAIT:-400} bash tools/run_ares.sh out/gateselfbad.sfc > /dev/null 2>&1 \
    || { echo "broken run failed"; exit 2; }
cp "$HOME/snesroms/gateselfbad.ram" out/gateselfbad.ram
BAD=0
python3 tools/check_nn.py out/gateselfbad.ram | tail -n 3 || BAD=1

echo
echo "control FAIL=$CTL   broken FAIL=$BAD"
if [ "$CTL" = 0 ] && [ "$BAD" = 1 ]; then
    echo "SELFTEST: pass - the gate accepts a good build and rejects a bad one"
    exit 0
fi
echo "SELFTEST: FAIL - the gate did not discriminate"
exit 1
