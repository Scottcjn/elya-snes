#!/bin/sh
# Everything a mixture cartridge has to pass, in one place, in the order that
# makes a silent failure impossible to mistake for a pass:
#   0 every build variant still LINKS (cheap, and it runs first)
#   1 pack, and PROVE max|dW| = 0 by decoding the stream back out
#   2 assemble and run the real ROM against the host reference
#   3 show that the survey actually routes to every expert
#   4 the 64-seed survey itself
#   5 the per-stage cycle profile
#   6 the block-16 accumulator, which the trainer does NOT model
#
# Step 6 is here because the trainer's docstring asserts the block-16 sum is
# provably below 256 and therefore not worth modelling.  That proof is about
# the ACTIVATIONS, which neither the normaliser nor the mixture changes - but
# "provable" is why it was never measured on a mixture, and this is the tree
# where two things changed at once.
#
#   train/moe_gate.sh runs/<arm>.npz [tag]
set -e
cd "$(dirname "$0")/.."
NPZ=$1
TAG=${2:-MOE}
[ -n "$NPZ" ] || { echo "usage: moe_gate.sh <npz> [tag]"; exit 2; }

echo "########## 0. every build variant links ##########"
MIXEXACT="$NPZ" sh train/link_variants.sh | tee "out/${TAG}_LINKS.txt" | tail -3

echo
echo "########## 1. pack + max|dW| ##########"
sh train/build_trained.sh "$NPZ" 1 | tee "out/${TAG}_PACK.txt"

echo
echo "########## 2. ROM vs host, seed token 1 ##########"
python3 tools/run_nn.py out/nn.nes out/model/expected.json 300 \
    | tee "out/${TAG}_VERIFICATION.txt" | tail -25

echo
echo "########## 3. expert coverage of the survey ##########"
python3 train/expert_coverage.py "$NPZ" 64 | tee "out/${TAG}_COVERAGE.txt"

echo
echo "########## 4. 64-seed survey ##########"
sh train/survey_exact.sh "$NPZ" 64 | tee "out/${TAG}_SURVEY.txt" | tail -3

echo
echo "########## 5. cycle profile ##########"
sh train/build_trained.sh "$NPZ" 1 > /dev/null
python3 tools/run_profile.py out/nnprof.nes | tee "out/${TAG}_PROFILE.txt"

echo
echo "########## 6. block-16 accumulator saturation ##########"
python3 host/blocksize.py "$NPZ" | tee "out/${TAG}_BLOCKSIZE.txt" | tail -20
