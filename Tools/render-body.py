#!/usr/bin/env python3
"""Renders the rigged base from the front and the back, flat (orthographic): the pose
reference garment pictures are drawn on (generate.mjs garment).

    python3 render-body.py <rigged-base.glb> <out-dir>      (build-assets.mjs runs it)

Writes front.png and back.png (2048 px, white background). The base faces +z; the back
view is mirrored, as seen from behind.
"""

import importlib.util
import io
import os
import sys

import numpy as np
from PIL import Image

here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fit", os.path.join(here, "fit-outfits.py"))
fit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit)

SIZE = 2048
SUPERSAMPLE = 2
MARGIN = 0.06  # of the image above the head and below the feet
LIGHT = np.array([0.35, 0.5, 1.0]) / np.linalg.norm([0.35, 0.5, 1.0])


def camera(points):
    low, high = points.min(axis=0), points.max(axis=0)
    return {"size": SIZE, "pixelsPerUnit": SIZE * (1 - 2 * MARGIN) / float(high[1] - low[1]),
            "center": [float(low[0] + high[0]) / 2, float(low[1] + high[1]) / 2]}


def to_pixels(points, view, cam, scale=1):
    """Image x, y (pixels) and nearness of each point; the back view looks along +z."""
    side = -1 if view == "back" else 1
    ppu = cam["pixelsPerUnit"] * scale
    x = cam["size"] * scale / 2 + side * (points[:, 0] - cam["center"][0]) * ppu
    y = cam["size"] * scale / 2 - (points[:, 1] - cam["center"][1]) * ppu
    return x, y, side * points[:, 2]


def render(mesh, texture, view, cam):
    size = SIZE * SUPERSAMPLE
    x, y, near = to_pixels(mesh.points, view, cam, SUPERSAMPLE)
    image = np.ones((size, size, 3), np.float32)
    depth = np.full((size, size), -np.inf, np.float32)
    th, tw, _ = texture.shape
    light = LIGHT * [1, 1, -1] if view == "back" else LIGHT
    for tri in mesh.indices:
        u, v = x[tri], y[tri]
        x0, x1 = max(int(u.min()), 0), min(int(u.max()) + 2, size)
        y0, y1 = max(int(v.min()), 0), min(int(v.max()) + 2, size)
        if x1 <= x0 or y1 <= y0:
            continue
        d = (v[1] - v[2]) * (u[0] - u[2]) + (u[2] - u[1]) * (v[0] - v[2])
        if abs(d) < 1e-9:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        l0 = ((v[1] - v[2]) * (xs - u[2]) + (u[2] - u[1]) * (ys - v[2])) / d
        l1 = ((v[2] - v[0]) * (xs - u[2]) + (u[0] - u[2]) * (ys - v[2])) / d
        l2 = 1 - l0 - l1
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not inside.any():
            continue
        weights = np.stack([l0[inside], l1[inside], l2[inside]], axis=1)
        z = weights @ near[tri]
        px, py = xs[inside].astype(int), ys[inside].astype(int)
        front = z > depth[py, px]
        if not front.any():
            continue
        px, py, z, weights = px[front], py[front], z[front], weights[front]
        uv = weights @ mesh.uvs[tri]
        color = texture[np.clip((uv[:, 1] * (th - 1)).astype(int), 0, th - 1),
                        np.clip((uv[:, 0] * (tw - 1)).astype(int), 0, tw - 1)]
        normal = weights @ mesh.normals[tri]
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
        shade = 0.72 + 0.28 * np.clip(normal @ light, 0, 1)
        image[py, px] = color * shade[:, None]
        depth[py, px] = z
    picture = Image.fromarray((np.clip(image, 0, 1) * 255).astype(np.uint8))
    return picture.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    path, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    mesh = fit.Mesh(path)
    info = mesh.material["pbrMetallicRoughness"]["baseColorTexture"]
    texture = np.asarray(Image.open(io.BytesIO(mesh.texture(info))).convert("RGB"), np.float32) / 255
    cam = camera(mesh.points)
    for view in ("front", "back"):
        render(mesh, texture, view, cam).save(os.path.join(out, f"{view}.png"))
    print(f"rendered {out}: front.png, back.png")


if __name__ == "__main__":
    main()
