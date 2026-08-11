import json, urllib.request, time, sys, uuid
HOST="http://192.168.0.136:8188"
def run(prompt, neg, seed, w=768, h=768, lora_w=0.85, steps=28):
    g={
     "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"juggernautXL_v9.safetensors"}},
     "2":{"class_type":"LoraLoader","inputs":{"lora_name":"sophia_elya_trained.safetensors",
          "strength_model":lora_w,"strength_clip":lora_w,"model":["1",0],"clip":["1",1]}},
     "3":{"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["2",1]}},
     "4":{"class_type":"CLIPTextEncode","inputs":{"text":neg,"clip":["2",1]}},
     "5":{"class_type":"EmptyLatentImage","inputs":{"width":w,"height":h,"batch_size":1}},
     "6":{"class_type":"KSampler","inputs":{"seed":seed,"steps":steps,"cfg":6.0,
          "sampler_name":"dpmpp_2m","scheduler":"karras","denoise":1.0,
          "model":["2",0],"positive":["3",0],"negative":["4",0],"latent_image":["5",0]}},
     "7":{"class_type":"VAEDecode","inputs":{"samples":["6",0],"vae":["1",2]}},
     "8":{"class_type":"SaveImage","inputs":{"filename_prefix":"elya_px","images":["7",0]}},
    }
    cid=str(uuid.uuid4())
    req=urllib.request.Request(HOST+"/prompt",
        data=json.dumps({"prompt":g,"client_id":cid}).encode(),
        headers={"Content-Type":"application/json"})
    pid=json.loads(urllib.request.urlopen(req,timeout=30).read())["prompt_id"]
    for _ in range(180):
        time.sleep(3)
        h_=json.loads(urllib.request.urlopen(HOST+f"/history/{pid}",timeout=20).read())
        if pid in h_:
            outs=h_[pid]["outputs"]
            return [i["filename"] for n in outs.values() for i in n.get("images",[])]
    return []

POS=("pixel art sprite of a young woman in a dark victorian dress with a white apron, "
     "long brown hair, side view running pose, 16-bit SNES game sprite, "
     "flat solid colours, no gradients, no anti-aliasing, hard black outline, "
     "limited 15 colour palette, chunky large pixels, clean pixel grid, "
     "plain white background, full body")
NEG=("photorealistic, 3d render, soft shading, gradient, blurry, antialiased, "
     "smooth, painterly, realistic, depth of field, noise, jpeg artifacts, text, watermark")
files=run(POS,NEG,seed=int(sys.argv[1]) if len(sys.argv)>1 else 7331)
print("generated:", files)

# ---------------------------------------------------------------------------
# Why this pipeline instead of asking a model for "pixel art"
#
# Asking a generator for pixel art gives a PAINTING of pixel art. Measured on a
# Grok frame: 92,859 colours, and only 13 of 289 sampled 8x8 blocks were a
# single flat colour. Flat blocks are what make tiles deduplicate, so that art
# cost 583 unique tiles for one screen.
#
# SDXL + the trained Elya LoRA, with the flat-colour instruction in the prompt
# and gradients in the negative, measures 96 of 196 blocks flat - and every
# sprite is the SAME Elya, which re-prompting can never give you.
#
# Pixelisation is then done deliberately by tools/png2snes.py against real
# hardware constraints (15-colour palette, 8x8 tiles, BGR555) rather than
# imitated by the generator.
#
# Host: 192.168.0.136, ComfyUI on :8188, JuggernautXL v9 + sophia_elya_trained.
# The V100 is shared with an ollama qwen2.5:32b holding ~25 GB, so run --lowvram
# and keep generations at 768 or below.
# ---------------------------------------------------------------------------
