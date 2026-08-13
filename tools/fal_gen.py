#!/usr/bin/env python3
"""fal_gen.py - generate an image on fal, then pixelise it through the harness.

Generation supplies the subject; tools/fal_pixelize.py supplies the format.
Runs entirely on the paid API, so it never competes with the lab's GPUs.
"""
import base64, json, os, subprocess, sys, urllib.request

def key():
    for l in open(os.path.expanduser("~/.bottube_alibaba.env")):
        if l.startswith("FAL_KEY="): return l.split("=",1)[1].strip().strip('"\'')
    raise SystemExit("FAL_KEY not found")

def gen(prompt, out, model="fal-ai/flux/dev", size="square_hd", steps=32):
    body={"prompt":prompt,"image_size":size,"num_images":1,
          "num_inference_steps":steps,"enable_safety_checker":False}
    r=urllib.request.Request(f"https://fal.run/{model}", data=json.dumps(body).encode(),
        headers={"Authorization":f"Key {key()}","Content-Type":"application/json"})
    d=json.loads(urllib.request.urlopen(r,timeout=300).read())
    url=d["images"][0]["url"]
    data=base64.b64decode(url.split(",",1)[1]) if url.startswith("data:") \
         else urllib.request.urlopen(url,timeout=120).read()
    open(out,'wb').write(data)
    return out

if __name__=="__main__":
    prompt, out = sys.argv[1], sys.argv[2]
    gen(prompt, out)
    print(f"generated {out}")
