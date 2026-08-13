#!/usr/bin/env bash
# Run every sprite source through the pixel harness at ONE scale and ONE palette,
# so the whole set looks like it belongs together. Scale matters as much as palette:
# assets pixelised at different scales read as separate images, not a sprite set.
set -u
OUT=assets/sprites/px
mkdir -p "$OUT"
SCALE=${SCALE:-5}
COLORS=${COLORS:-15}
for f in "$@"; do
  n=$(basename "$f" .png)
  printf '%-22s ' "$n"
  python3 tools/fal_pixelize.py "$f" "$OUT/$n.png" --colors "$COLORS" --scale "$SCALE" 2>&1 \
    | grep -E "unique colours|flat 8x8" | tr '\n' ' ' | sed 's/  */ /g'
  echo
done
