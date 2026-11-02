"""Hunyuan3D 2.1 on the clothes pod: dressed images in, textured GLBs out. Runs in the
pod's venv (pod.py starts it). Settings are the ones the bake-off used: shape at octree
512, then the PBR texture painter with 8 views at 768 px.

    python make_item.py <image> <out.glb> [<image> <out.glb> ...]

The models load once, so several images in one call is much faster than one call each.
"""

import os
import sys
import time

REPO = "/workspace/Hunyuan3D-2.1"
os.chdir(REPO)
sys.path.insert(0, os.path.join(REPO, "hy3dshape"))
sys.path.insert(0, os.path.join(REPO, "hy3dpaint"))

import torch
import trimesh
from PIL import Image
from hy3dshape import DegenerateFaceRemover, FloaterRemover
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
from hy3dshape.rembg import BackgroundRemover
from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline


def main():
    pairs = list(zip(sys.argv[1::2], sys.argv[2::2]))
    if not pairs or len(sys.argv) % 2 == 0:
        sys.exit(__doc__)
    start = time.time()
    shape = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2.1")
    config = Hunyuan3DPaintConfig(max_num_view=8, resolution=768)
    config.realesrgan_ckpt_path = "hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
    config.multiview_cfg_path = "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
    config.custom_pipeline = "hy3dpaint/hunyuanpaintpbr"
    paint = Hunyuan3DPaintPipeline(config)
    remove_background = BackgroundRemover()
    print(f"models loaded in {time.time() - start:.0f} s", flush=True)

    for image_path, out_path in pairs:
        start = time.time()
        work = os.path.splitext(os.path.abspath(out_path))[0]
        os.makedirs(os.path.dirname(work), exist_ok=True)
        image = remove_background(Image.open(image_path).convert("RGB"))
        image.save(f"{work}_input.png")
        mesh = shape(image=image, num_inference_steps=50, octree_resolution=512, generator=torch.manual_seed(42))[0]
        mesh = DegenerateFaceRemover()(FloaterRemover()(mesh))
        mesh.export(f"{work}_shape.glb")
        paint(mesh_path=f"{work}_shape.glb", image_path=f"{work}_input.png", output_mesh_path=f"{work}.obj", save_glb=False)
        trimesh.load(f"{work}.obj", process=False).export(out_path, include_normals=True)
        print(f"{out_path}: {time.time() - start:.0f} s, peak {torch.cuda.max_memory_allocated() / 1e9:.0f} GB", flush=True)


if __name__ == "__main__":
    main()
