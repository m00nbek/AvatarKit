#!/usr/bin/env python3
"""Fits garments made on their own (generate.mjs garment) onto the rigged base.

    python3 fit-garments.py <rigged-base.glb> <outfits.json> <out-dir>

Same arguments and output as fit-other-generators.py, which handles every other outfit.
Outfits marked `"garment": true` hold one item, and their model is just that garment
(Tripo or Hunyuan), in its own pose and size. Fitting it:

1. Facing: the turn whose front view looks most like the garment's front picture.
2. Height: a top hangs from the shoulders, a bottom sits at the waist.
3. Size: the smallest whose girth goes around the body's torso (or hips), plus ease.
4. The generator closes the openings (neck, cuffs, hem) and may add an inner layer;
   only faces that point away from the body are kept, which drops both.
5. Wrapped: pushed out wherever the body is inside it, smoothly, like cloth over it.
6. The rest is fit-outfits': cut edges, skinned, and the body hidden under it.
"""

import importlib.util
import json
import os
import sys

import numpy as np
from PIL import Image

np.seterr(all="ignore")
tools = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("other", os.path.join(tools, "fit-other-generators.py"))
other = importlib.util.module_from_spec(spec)
spec.loader.exec_module(other)
fit, rig = other.fit, other.rig

# Room between body and garment, as a share of height, and how much more cloth than the
# body's girth a garment needs around it.
EASE_GAP = 0.006
EASE_GIRTH = 1.08
# A face is kept when its normal and the body's under it agree at least this much.
FACING_BODY = 0.2
TORSO_BONES = ("pelvis", "spine", "clavicle")
HIP_BONES = ("pelvis", "thigh")


# MARK: - Facing

def picture_mask(path, size=48):
    """The garment in its front picture: mask and colors on a square grid around it."""
    image = np.asarray(Image.open(path).convert("RGB"), np.float32) / 255
    ink = np.linalg.norm(1 - image, axis=2) > 0.1
    rows, cols = np.where(ink)
    crop = image[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    mask = ink[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    return to_grid(crop, mask, size)


def to_grid(colors, mask, size):
    h, w = mask.shape
    side = max(h, w)
    grid_colors, grid_mask = np.ones((side, side, 3), np.float32), np.zeros((side, side), bool)
    top, left = (side - h) // 2, (side - w) // 2
    grid_colors[top:top + h, left:left + w], grid_mask[top:top + h, left:left + w] = colors, mask
    cells = np.linspace(0, side, size + 1).astype(int)
    out_colors, out_mask = np.ones((size, size, 3), np.float32), np.zeros((size, size), bool)
    for i in range(size):
        for j in range(size):
            block = grid_mask[cells[i]:cells[i + 1], cells[j]:cells[j + 1]]
            if block.mean() > 0.5:
                out_mask[i, j] = True
                out_colors[i, j] = grid_colors[cells[i]:cells[i + 1], cells[j]:cells[j + 1]][block].mean(axis=0)
    return out_colors, out_mask


def front_view(points, colors, size=48):
    """The garment seen from the front (+z), as the same grid as picture_mask."""
    low, high = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    span = (high - low).max()
    offset = (span - (high - low)) / 2
    col = np.clip(((points[:, 0] - low[0] + offset[0]) / span * size).astype(int), 0, size - 1)
    row = np.clip(((high[1] - points[:, 1] + offset[1]) / span * size).astype(int), 0, size - 1)
    image, mask = np.ones((size, size, 3), np.float32), np.zeros((size, size), bool)
    order = np.argsort(points[:, 2])  # nearest last, so it wins
    image[row[order], col[order]] = colors[order]
    mask[row, col] = True
    return image, mask


def best_turn(outfit, picture):
    target_colors, target_mask = picture_mask(picture)
    scores = {}
    for turn, matrix in fit.TURN.items():
        colors, mask = front_view(outfit.points @ matrix.T, outfit.colors)
        both = mask & target_mask
        overlap = both.sum() / max((mask | target_mask).sum(), 1)
        difference = np.linalg.norm(colors[both] - target_colors[both], axis=1).mean() if both.any() else 1
        scores[turn] = (1 - overlap) + difference
    turn = min(scores, key=scores.get)
    print("  facing " + ", ".join(f"{t}: {s:.2f}" for t, s in sorted(scores.items(), key=lambda kv: kv[1])))
    return turn


# MARK: - Size and place

def body_part(base, names):
    """Base vertices whose strongest bone is one of `names` (prefixes)."""
    skeleton = rig.Skeleton(base.gltf, base.binary)
    strongest = base.joints[np.arange(len(base.joints)), np.argmax(base.weights, axis=1)]
    bones = np.array([skeleton.names[j].lower().startswith(names) for j in range(len(skeleton.names))])
    return bones[strongest]


def girth(points):
    """Perimeter of the convex hull of 2D points: how much cloth it takes to go around them."""
    points = np.unique(np.round(points, 5), axis=0)
    if len(points) < 3:
        return 0.0
    def half(rows):
        hull = []
        for p in rows:
            while len(hull) >= 2 and np.cross(hull[-1] - hull[-2], p - hull[-2]) <= 0:
                hull.pop()
            hull.append(p)
        return hull
    hull = np.array(half(points)[:-1] + half(points[::-1])[:-1])
    return float(np.linalg.norm(hull - np.roll(hull, 1, axis=0), axis=1).sum())


def too_tight(garment, body, height, bands=16):
    """Whether the garment is short of cloth somewhere: at each height it covers, it has to
    go around the body with EASE_GIRTH to spare. Cloth bends around the body but doesn't
    stretch, so girth is what has to fit, not the garment's shape. Sleeves are left out:
    only garment points within reach of the body's own width count."""
    low, high = garment[:, 1].min(), garment[:, 1].max()
    body = body[(body[:, 1] > low) & (body[:, 1] < high)]
    if not len(body):
        return True
    edges = np.linspace(body[:, 1].min(), body[:, 1].max(), bands + 1)
    for b in range(bands):
        p = body[(body[:, 1] >= edges[b]) & (body[:, 1] <= edges[b + 1])][:, [0, 2]]
        g = garment[(garment[:, 1] >= edges[b]) & (garment[:, 1] <= edges[b + 1])][:, [0, 2]]
        if len(p) < 3:
            continue
        center, reach = np.median(p, axis=0), np.abs(p[:, 0] - np.median(p[:, 0])).max() + height * 0.06
        g = g[np.abs(g[:, 0] - center[0]) < reach]
        if girth(g) < girth(p) * EASE_GIRTH:
            return True
    return False


def resting_offset(garment, up, supports, height):
    """How far to move the garment up (negative: down) so it rests on the support
    points from above: its upward-facing surface over each, EASE_GAP clear of it.
    Only upward-facing points count, so the cap over the hem never does, and a support
    under the neck opening has nothing over it."""
    cell = height * 0.015
    keys = lambda q: np.floor(q[:, [0, 2]] / cell).astype(np.int64) @ np.array([1, 100003])
    tops = {}
    for key, y in zip(keys(garment[up]), garment[up, 1]):
        if y > tops.get(key, -np.inf):
            tops[key] = y
    gaps = np.array([tops[k] - y for k, y in zip(keys(supports), supports[:, 1]) if k in tops])
    return height * EASE_GAP - np.percentile(gaps, 2) if len(gaps) else 0.0


def place(points, normals, base, marks, height, slot):
    """Scale and offset for the garment: resting on the shoulders (a top) or hung at the
    waist (a bottom), centered on the body there, and the smallest size with cloth enough
    to go around the body."""
    body_points = base.points
    if slot == "top":
        torso_mask = body_part(base, TORSO_BONES)
        torso = body_points[torso_mask]
        # Tops rest on the shoulders: torso points above the armpits that face up.
        up = (base.normals[:, 1] / np.linalg.norm(base.normals, axis=1)) > 0.5
        supports = body_points[torso_mask & up & (body_points[:, 1] > marks["armpit"])]
        anchor_y = supports[:, 1].max() + height * EASE_GAP
        held = torso[torso[:, 1] < marks["armpit"]]
        top = True
    else:
        held = body_points[body_part(base, HIP_BONES)]
        held = held[held[:, 1] > marks["crotch"] - height * 0.05]
        anchor_y = marks["crotch"] + height * 0.1
        top = False
    level = marks["armpit"] if top else marks["crotch"] + height * 0.05
    band_body = np.abs(body_points[:, 1] - level) < height * 0.03

    def placed(scale):
        p = points * scale
        p[:, 1] += anchor_y - (p[:, 1].max() if top else np.percentile(p[:, 1], 99))
        for _ in range(2):
            band = np.abs(p[:, 1] - level) < height * 0.03
            p[:, 0] += np.median(body_points[band_body, 0]) - np.median(p[band, 0])
            p[:, 2] += np.median(body_points[band_body, 2]) - (p[band, 2].min() + p[band, 2].max()) / 2
            if top:
                p[:, 1] += resting_offset(p, normals[:, 1] > 0.3, supports, height)
        return p

    small, large = 0.05 * height / np.ptp(points[:, 1]), 3.0 * height / np.ptp(points[:, 1])
    for _ in range(24):
        middle = np.sqrt(small * large)
        if too_tight(placed(middle), held, height):
            small = middle
        else:
            large = middle
    scale = large
    print(f"  placed: {np.ptp(points[:, 1]) * scale / height:.2f} of height tall")
    return placed(scale)


def align_garment(outfit, base, height, marks):
    spec = outfit.garment
    turn = outfit.forward if outfit.forward in fit.TURN else best_turn(outfit, spec["picture"])
    points, normals = outfit.points @ fit.TURN[turn].T, outfit.normals @ fit.TURN[turn].T
    outfit.aligned = place(points, normals, base, marks, height, spec["items"][0]["slot"])
    outfit.aligned_normals = normals


# MARK: - Openings and inner layers

def outer_faces(outfit, base, height):
    """Faces that point away from the body under them: not the caps the generator puts
    over the openings (they run along the body), nor an inner layer (it faces the body)."""
    corners = outfit.aligned[outfit.indices]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    centers = corners.mean(axis=1)
    if np.einsum("ij,ij->i", normals, centers - centers.mean(axis=0)).mean() < 0:
        normals = -normals  # wound inside out
    match = fit.nearest(centers, base.points)
    body_normals = base.normals[match] / np.linalg.norm(base.normals[match], axis=1, keepdims=True)
    # Faces the body pokes through still count: they're pushed out when written.
    return np.einsum("ij,ij->i", normals, body_normals) > FACING_BODY


def wrap(points, triangles, moving, base, height, rounds=16):
    """Pushes the garment out wherever the body is inside it or closer than EASE_GAP, a
    little at a time and smoothed over the garment, so it bends around the body like
    cloth (the bust, the shoulder blades) instead of being poked through."""
    adjacency = rig.vertex_adjacency(triangles.ravel(), points)
    margin = height * EASE_GAP
    for _ in range(rounds):
        match = fit.nearest(points, base.points)
        normals = base.normals[match] / np.linalg.norm(base.normals[match], axis=1, keepdims=True)
        depth = np.einsum("ij,ij->i", points - base.points[match], normals)
        short = np.maximum(margin - depth, 0) * moving
        if short.max() < margin * 0.2:
            break
        move = normals * short[:, None]
        for _ in range(4):
            move = 0.5 * move + 0.5 * np.stack([fit.neighbor_mean(move[:, k], adjacency) for k in range(3)], axis=1)
        points = points + move * moving[:, None]
    return points


def split_garment(outfit, base, marks, height, items):
    keep = outer_faces(outfit, base, height)
    votes = np.zeros(len(outfit.aligned))
    counts = np.zeros(len(outfit.aligned))
    np.add.at(votes, outfit.indices.ravel(), np.repeat(keep, 3))
    np.add.at(counts, outfit.indices.ravel(), 1)
    label = np.where(votes > counts / 2, 0, -1)
    adjacency = rig.vertex_adjacency(outfit.indices.ravel(), outfit.aligned)
    label = fit.fill_holes(label, 0, adjacency)
    label = other.drop_fragments(label, 0, adjacency, keep=0.05)
    outfit.aligned = wrap(outfit.aligned, outfit.indices, label == 0, base, height)
    outfit.adjacency, outfit.labels = adjacency, label
    outfit.references = {0: np.median(fit.lab(outfit.colors[label == 0]), axis=0)}
    print(f"  {items[0]['id']}: {int((label == 0).sum())} of {len(label)} vertices on the outside")
    return label


# MARK: - Hooks

def install(base_path, outfits_path):
    other.install(base_path, outfits_path)
    garments = {s["model"]: s for s in json.load(open(outfits_path)) if s.get("garment")}
    base = fit.Mesh(base_path)
    height = float(np.ptp(base.points[:, 1]))
    marks = rig.measure(rig.surface_samples(base.points, base.indices.ravel(), 300_000), height)
    other_mesh, other_align, other_split, other_write = fit.Mesh, fit.align, fit.split_items, fit.write_item

    class Mesh(other_mesh):
        def __init__(self, path):
            super().__init__(path)
            self.garment = garments.get(path)
            if not self.garment:
                return
            node = next(n for n in self.gltf["nodes"] if n.get("mesh") == 0)
            matrix = other.node_matrix(node)
            self.points = self.points @ matrix[:3, :3].T + matrix[:3, 3]
            normals = self.normals @ np.linalg.inv(matrix[:3, :3])
            self.normals = normals / np.linalg.norm(normals, axis=1, keepdims=True)
            self.colors = other.center_colors(self)

    def align(outfit, base_mesh, skin_color, height_):
        if outfit.garment:
            return align_garment(outfit, base_mesh, height, marks)
        return other_align(outfit, base_mesh, skin_color, height_)

    def split_items(outfit, base_mesh, marks_, height_, items):
        if outfit.garment:
            return split_garment(outfit, base_mesh, marks, height, items)
        return other_split(outfit, base_mesh, marks_, height_, items)

    def write_item(path, outfit, *rest):
        # A garment is written like other generators' items; it has no painted skin to clean.
        return other.write_other_item(path, outfit, *rest) if outfit.garment else other_write(path, outfit, *rest)

    fit.Mesh, fit.align, fit.split_items, fit.write_item = Mesh, align, split_items, write_item


def main():
    base_path, outfits_path, _ = sys.argv[1:4]
    install(base_path, outfits_path)
    fit.main()


if __name__ == "__main__":
    main()
