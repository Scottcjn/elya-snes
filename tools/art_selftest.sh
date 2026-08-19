#!/bin/bash
# Does the ART_SPEC gate actually gate?
#
# The palette was the first attempt at this and it was not enough.  The object
# palette has no apron white in it -- deliberately, so a maid outfit would have
# nowhere to live -- and the drift came back anyway, as a SHAPE: a broad flat
# white collar across the shoulders, drawn in the one white entry the high
# collar legitimately needs.  A palette check cannot see that.  A pixel budget
# can, and art_spec_check() is that budget.
#
# So this reproduces the exact drift and requires the build to refuse it.  A
# gate that has never rejected anything is not known to be a gate.
#
# The break is surgical: the two collar spans in elya() are widened from a
# standing band at the throat to a flat disc across the shoulders.  Nothing
# else changes -- she still faces right, still has auburn hair past the waist,
# still fills the cell, still wears a brown floor-length dress.  She passes
# every count the first sprite batch drifted on, which is the point: those are
# the counts a human reviewer already knew to look at.
#
# Measured, 2026-08-19:
#   control  white  7 px   ART_SPEC pass, 160 tiles baked
#   broken   white 18 px   'that is an apron/maid collar', build refuses
set -u
cd "$(dirname "$0")/.."
SRC=tools/mkart.py
BAK=$(mktemp)
cp "$SRC" "$BAK"
restore() { cp "$BAK" "$SRC"; rm -f "$BAK"; }
trap restore EXIT

echo "=== control: the tree as it stands ==="
CTL=0
python3 "$SRC" > out/art_selftest_ctl.log 2>&1 || CTL=1
grep -E "^elya" out/art_selftest_ctl.log || true

echo
echo "=== the deliberate drift: the high collar widened into a maid's collar ==="
python3 - "$SRC" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
a = "    _span(g, 12 + B, 17 + L, 'FFF')\n    _span(g, 13 + B, 16 + L, '8FF8')"
assert a in s, "mkart.py no longer has the collar spans this self-test widens"
b = "    _span(g, 12 + B, 14 + L, 'FFFFFFFF')\n    _span(g, 13 + B, 13 + L, 'FFFFFFFFFF')"
open(p, "w").write(s.replace(a, b))
PY
BAD=0
python3 "$SRC" > out/art_selftest_bad.log 2>&1 || BAD=1
grep -E "ART_SPEC FAIL|refusing" out/art_selftest_bad.log || true
restore; trap - EXIT

# rebuild the good sheet, so a self-test run never leaves drifted art on disk
python3 "$SRC" > /dev/null 2>&1

echo
echo "control FAIL=$CTL   broken FAIL=$BAD"
if [ "$CTL" = 0 ] && [ "$BAD" = 1 ]; then
    echo "ART SELFTEST: pass - the gate bakes canon art and refuses a maid's collar"
    exit 0
fi
echo "ART SELFTEST: FAIL - the gate did not discriminate"
exit 1
