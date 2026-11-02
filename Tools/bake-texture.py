#!/usr/bin/env python3
"""Bakes a re-textured copy of a mesh onto the base's own texture layout.

    python3 bake-texture.py <base.glb> <retextured.glb> <out.jpg> [--forward +x] [--size 1024]

Tripo's re-texture returns the same shape with reordered vertices and new UVs,
so its texture doesn't fit the base. For every texel of the base layout this
finds the same spot on the re-textured mesh and copies its color. The base is
the rigged one (normalized: facing +z, feet at 0); the re-textured mesh is
normalized the same way first.
"""

import argparse
import importlib.util
import io
import os

import numpy as np
from PIL import Image, ImageFilter

np.seterr(all="ignore")
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rig", os.path.join(here, "rig-generated-body.py"))
rig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rig)

TURN = {"+z": np.eye(3), "-z": np.diag([-1, 1, -1]),
        "+x": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]]), "-x": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])}


def load(path):
    gltf, binary = rig.read_glb(path)
    primitive = gltf["meshes"][0]["primitives"][0]
    points = rig.accessor(gltf, binary, primitive["attributes"]["POSITION"]).astype(np.float64)
    uvs = rig.accessor(gltf, binary, primitive["attributes"]["TEXCOORD_0"]).astype(np.float64)
    triangles = rig.accessor(gltf, binary, primitive["indices"]).astype(np.int64).reshape(-1, 3)
    material = gltf["materials"][primitive["material"]]
    source = gltf["textures"][material["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
    texture = np.asarray(Image.open(io.BytesIO(rig.image_bytes(gltf, binary, source))).convert("RGB"))
    return points, uvs, triangles, texture


def normalize(points, forward, height):
    """Same normalization as rig-generated-body.py."""
    points = points @ TURN[forward].T
    points *= height / np.ptp(points[:, 1])
    points[:, 1] -= points[:, 1].min()
    points[:, [0, 2]] -= np.median(points[points[:, 1] < height * 0.5][:, [0, 2]], axis=0)
    return points


def texel_positions(points, uvs, triangles, size):
    """3D position of every texel the base's triangles cover in UV space."""
    position = np.full((size, size, 3), np.nan)
    pixel = uvs * (size - 1)
    for tri in triangles:
        u, v = pixel[tri, 0], pixel[tri, 1]
        x0, x1 = int(np.floor(u.min())), int(np.ceil(u.max()))
        y0, y1 = int(np.floor(v.min())), int(np.ceil(v.max()))
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, size - 1) + 1), np.arange(max(y0, 0), min(y1, size - 1) + 1))
        d = (v[1] - v[2]) * (u[0] - u[2]) + (u[2] - u[1]) * (v[0] - v[2])
        if abs(d) < 1e-12:
            continue
        l0 = ((v[1] - v[2]) * (xs - u[2]) + (u[2] - u[1]) * (ys - v[2])) / d
        l1 = ((v[2] - v[0]) * (xs - u[2]) + (u[0] - u[2]) * (ys - v[2])) / d
        l2 = 1 - l0 - l1
        inside = (l0 >= -0.02) & (l1 >= -0.02) & (l2 >= -0.02)
        p = l0[..., None] * points[tri[0]] + l1[..., None] * points[tri[1]] + l2[..., None] * points[tri[2]]
        position[ys[inside], xs[inside]] = p[inside]
    return position


def nearest(queries, targets, chunk=4000):
    targets32 = targets.astype(np.float32)
    squared = (targets32 ** 2).sum(axis=1)
    out = np.empty(len(queries), np.int64)
    for start in range(0, len(queries), chunk):
        q = queries[start:start + chunk].astype(np.float32)
        out[start:start + chunk] = np.argmin(squared[None, :] - 2 * q @ targets32.T, axis=1)
    return out


def sample_on_surface(queries, points, uvs, triangles, texture):
    """Color of the source surface at each query point: the best of the triangles
    around the nearest vertex, by barycentric fit."""
    incident = [[] for _ in range(len(points))]
    for t, tri in enumerate(triangles):
        for v in tri:
            incident[v].append(t)
    width = max(len(i) for i in incident)
    table = np.array([i + [i[0]] * (width - len(i)) for i in incident])

    vertex = nearest(queries, points)
    best_uv = uvs[vertex].copy()
    best_error = np.full(len(queries), np.inf)
    for column in range(width):
        tri = triangles[table[vertex, column]]
        a, b, c = points[tri[:, 0]], points[tri[:, 1]], points[tri[:, 2]]
        v0, v1, v2 = b - a, c - a, queries - a
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        denom = d00 * d11 - d01 * d01
        w1 = (d11 * d20 - d01 * d21) / denom
        w2 = (d00 * d21 - d01 * d20) / denom
        w0 = 1 - w1 - w2
        weights = np.clip(np.stack([w0, w1, w2], 1), 0, 1)
        weights /= np.maximum(weights.sum(1, keepdims=True), 1e-9)
        error = np.maximum(0, -np.stack([w0, w1, w2], 1)).sum(1)
        better = np.isfinite(error) & (error < best_error)
        uv = weights[:, :1] * uvs[tri[:, 0]] + weights[:, 1:2] * uvs[tri[:, 1]] + weights[:, 2:] * uvs[tri[:, 2]]
        best_uv[better], best_error[better] = uv[better], error[better]
    th, tw, _ = texture.shape
    return texture[np.clip((best_uv[:, 1] * (th - 1)).round().astype(int), 0, th - 1),
                   np.clip((best_uv[:, 0] * (tw - 1)).round().astype(int), 0, tw - 1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("retextured")
    parser.add_argument("output")
    parser.add_argument("--forward", default="+z", choices=list(TURN))
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()

    base_points, base_uvs, base_triangles, base_texture = load(args.base)
    height = float(np.ptp(base_points[:, 1]))
    points, uvs, triangles, texture = load(args.retextured)
    points = normalize(points, args.forward, height)

    position = texel_positions(base_points, base_uvs, base_triangles, args.size)
    covered = np.isfinite(position[..., 0])
    colors = sample_on_surface(position[covered], points, uvs, triangles, texture)

    # Start from the base texture so seams and unused texels keep sensible colors.
    out = np.asarray(Image.fromarray(base_texture).resize((args.size, args.size))).copy()
    out[covered] = colors
    image = Image.fromarray(out)
    # Bleed covered colors a few pixels outward so UV seams don't show the old texture.
    bleed = image.filter(ImageFilter.MaxFilter(3))
    image = Image.composite(image, bleed, Image.fromarray((covered * 255).astype(np.uint8)))
    image.save(args.output, quality=90)
    print(f"baked {covered.sum()} texels into {args.output}")


if __name__ == "__main__":
    main()
