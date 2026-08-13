#!/usr/bin/env python3
"""
fal_pixelize.py - turn a rendered image into REAL pixel art under hard constraints.

Why this exists
---------------
Asking a diffusion model for "pixel art" produces a PAINTING of pixel art. Measured
on this project: a Grok frame had 92,859 colours and only 13 of 289 sampled 8x8
blocks were a single flat colour; SDXL with heavy prompt engineering reached 96 of
196. Neither aligns to a pixel grid, because neither model has a grid.

So we stop asking. Generation supplies the CHARACTER; this supplies the FORMAT.
fal-ai/image2pixel takes a fixed palette, a max colour count and a grid-snap flag,
which are constraints rather than suggestions - and then verify() checks the result
against the SNES's actual limits instead of trusting the service.

Usage:
    fal_pixelize.py in.png out.png [--colors 15] [--scale 4] [--alpha]
"""
import base64, json, os, sys, urllib.request
from PIL import Image

FAL = "https://fal.run/fal-ai/image2pixel"

def key():
    for line in open(os.path.expanduser("~/.bottube_alibaba.env")):
        if line.startswith("FAL_KEY="):
            return line.split("=",1)[1].strip().strip('"').strip("'")
    raise SystemExit("FAL_KEY not found")

def pixelize(src, colors=15, scale=None, alpha=False, palette=None):
    data = base64.b64encode(open(src,'rb').read()).decode()
    body = {
        "image_url": f"data:image/png;base64,{data}",
        "max_colors": colors,
        "snap_grid": True,          # the whole point - a real grid, not the look of one
        "cleanup_jaggy": True,
        "cleanup_morph": True,
        "trim_borders": False,
        "transparent_background": alpha,
        "sync_mode": True,
    }
    if scale:   body["scale"] = scale
    if palette: body["fixed_palette"] = palette
    req = urllib.request.Request(FAL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Key {key()}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())

def verify(path):
    """Check the result against the SNES's real limits, not the service's word."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    cols = len(im.getcolors(maxcolors=10**7) or [])
    same = tot = 0
    for by in range(0, h-8, 8):
        for bx in range(0, w-8, 8):
            px = {im.getpixel((bx+x, by+y)) for y in range(8) for x in range(8)}
            tot += 1; same += (len(px) == 1)
    flat = same/tot if tot else 0
    print(f"  size            : {w}x{h}")
    print(f"  unique colours  : {cols}   (SNES allows 15 per palette)")
    print(f"  flat 8x8 blocks : {same}/{tot} = {flat:.0%}   (Grok 4%, SDXL 49%)")
    return cols, flat

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    a = sys.argv[3:]
    colors = int(a[a.index("--colors")+1]) if "--colors" in a else 15
    scale  = int(a[a.index("--scale")+1])  if "--scale"  in a else None
    r = pixelize(src, colors=colors, scale=scale, alpha="--alpha" in a)
    imgs = r.get("images") or ([r["image"]] if r.get("image") else [])
    url = imgs[0]["url"] if imgs else r.get("url")
    if r.get("palette"):
        print(f'  detected palette: {r["num_colors"]} colours, scale {r.get("pixel_scale")}')
    if not url: raise SystemExit(f"no image in response: {json.dumps(r)[:300]}")
    if url.startswith("data:"):
        open(dst,'wb').write(base64.b64decode(url.split(",",1)[1]))
    else:
        open(dst,'wb').write(urllib.request.urlopen(url, timeout=120).read())
    print(f"wrote {dst}")
    verify(dst)
